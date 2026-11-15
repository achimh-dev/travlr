"""Duffel Flights API (https://duffel.com/docs/api): echte, buchbare Airline-Angebote (viele NDC-Tarife).

Optional: wird nur genutzt, wenn DUFFEL_API_KEY gesetzt ist. Die Suche ist kostenlos; Duffel
berechnet nur Buchungen. Ein Testmodus-Key (duffel_test_…) liefert Fake-Airlines ("Duffel Airways").
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import product
from pathlib import Path

import httpx

from .cache import DiskCache
from ..models import FlightOption, Layover, SearchQuery, SearchResult, Segment
from .serpapi import price_note_for

ENDPOINT = "https://api.duffel.com/air/offer_requests"
MAX_COMBOS = 4  # Duffel kennt pro Strecke nur einen Flughafen; mehr Kombinationen = mehr Wartezeit
_MAX_CONN = {"any": None, "nonstop": 0, "max1": 1, "max2": 2}


def build_bodies(q: SearchQuery) -> list[dict]:
    bodies = []
    for origin, dest in list(product(q.origins, q.destinations))[:MAX_COMBOS]:
        slices = [{"origin": origin, "destination": dest, "departure_date": q.outbound_date}]
        if q.return_date:
            # Gabelflug: erster Code der Rückflug-Flughäfen, sonst Hinflug umgekehrt
            back_from = q.return_from[0] if q.return_from else dest
            back_to = q.return_to[0] if q.return_to else origin
            slices.append({"origin": back_from, "destination": back_to, "departure_date": q.return_date})
        data: dict = {
            "slices": slices,
            "passengers": [{"type": "adult"} for _ in range(q.adults)],
            "cabin_class": q.travel_class,
        }
        if _MAX_CONN[q.stops] is not None:
            data["max_connections"] = _MAX_CONN[q.stops]
        bodies.append({"data": data})
    return bodies


class DuffelProvider:
    name = "duffel"

    def __init__(self, api_key: str, cache_dir: Path, cache_ttl_hours: float = 6, timeout: float = 45):
        self.api_key = api_key
        self.cache = DiskCache(cache_dir, cache_ttl_hours, "duffel")
        self.timeout = timeout

    def _fetch(self, body: dict) -> dict:
        cached = self.cache.get(body)
        if cached is not None:
            return cached
        resp = httpx.post(
            ENDPOINT,
            params={"return_offers": "true", "supplier_timeout": 20000},
            json=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Duffel-Version": "v2",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        data = resp.json()
        if resp.status_code >= 400:
            errors = data.get("errors") or [{}]
            raise RuntimeError(errors[0].get("message") or f"HTTP {resp.status_code}")
        self.cache.set(body, data)
        return data

    def search(self, q: SearchQuery, search_id: str) -> SearchResult:
        bodies = build_bodies(q)
        offers: list[dict] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
            for fut in [pool.submit(self._fetch, b) for b in bodies]:
                try:
                    offers += (fut.result().get("data") or {}).get("offers") or []
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    errors.append(str(exc))
        options = parse_offers(offers, q, search_id)
        error = None if options else ("; ".join(errors) or "Keine Angebote gefunden.")
        return SearchResult(search_id=search_id, query=q, source="duffel", options=options, error=error)


def parse_offers(offers: list[dict], q: SearchQuery, search_id: str, limit: int = 30) -> list[FlightOption]:
    parsed = []
    for offer in sorted(offers, key=lambda o: float(o.get("total_amount") or 1e9))[:limit]:
        slices = offer.get("slices") or []
        if not slices:
            continue
        out_segs, out_lay = _slice(slices[0])
        ret_segs, ret_lay = _slice(slices[1]) if len(slices) > 1 else ([], [])
        if not out_segs:
            continue
        airlines = list(dict.fromkeys(s.airline for s in out_segs + ret_segs if s.airline))
        parsed.append(
            FlightOption(
                id=f"{search_id}-{len(parsed) + 1}",
                search_id=search_id,
                source="duffel",
                price=float(offer["total_amount"]) if offer.get("total_amount") else None,
                currency=offer.get("total_currency") or q.currency,
                trip_type=q.trip_type,
                price_note=price_note_for(q) + ", direkt buchbarer Airline-Tarif",
                airlines=airlines,
                segments=out_segs,
                layovers=out_lay,
                return_segments=ret_segs,
                return_layovers=ret_lay,
                stops=len(out_lay),
                total_duration_min=_iso_minutes(slices[0].get("duration")),
                fare_brand=slices[0].get("fare_brand_name"),
                baggage_note=_baggage(slices[0]),
            )
        )
    return parsed


def _slice(sl: dict) -> tuple[list[Segment], list[Layover]]:
    segs = []
    for s in sl.get("segments") or []:
        carrier = s.get("marketing_carrier") or {}
        segs.append(
            Segment(
                from_airport=(s.get("origin") or {}).get("iata_code", ""),
                from_name=(s.get("origin") or {}).get("name", ""),
                to_airport=(s.get("destination") or {}).get("iata_code", ""),
                to_name=(s.get("destination") or {}).get("name", ""),
                departure=_fmt(s.get("departing_at")),
                arrival=_fmt(s.get("arriving_at")),
                airline=carrier.get("name", ""),
                flight_number=f"{carrier.get('iata_code', '')} {s.get('marketing_carrier_flight_number', '')}".strip(),
                duration_min=_iso_minutes(s.get("duration")),
            )
        )
    lays = []
    for a, b in zip(segs, segs[1:]):
        try:
            t1 = datetime.strptime(a.arrival, "%Y-%m-%d %H:%M")
            t2 = datetime.strptime(b.departure, "%Y-%m-%d %H:%M")
            lays.append(Layover(airport=a.to_airport, name=a.to_name,
                                duration_min=int((t2 - t1).total_seconds() // 60), overnight=t2.date() > t1.date()))
        except ValueError:
            lays.append(Layover(airport=a.to_airport, name=a.to_name))
    return segs, lays


def _baggage(sl: dict) -> str | None:
    segs = sl.get("segments") or []
    pax = (segs[0].get("passengers") or [{}])[0] if segs else {}
    bags = {b.get("type"): b.get("quantity", 0) for b in pax.get("baggages") or []}
    if not bags:
        return None
    return f"Handgepäck: {bags.get('carry_on', 0)}, Aufgabegepäck: {bags.get('checked', 0)}"


def _fmt(ts: str | None) -> str:
    return (ts or "")[:16].replace("T", " ")


def _iso_minutes(d: str | None) -> int | None:
    if not d:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", d)
    if not m:
        return None
    days, hours, mins = (int(x) if x else 0 for x in m.groups())
    return days * 1440 + hours * 60 + mins
