"""Google-Flights-Suche über SerpApi (https://serpapi.com/google-flights-api).

Jede Suche kostet ein SerpApi-Kontingent. Ergebnisse werden deshalb auf der Platte
zwischengespeichert (siehe CACHE_TTL_HOURS).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .cache import DiskCache
from ..models import FlightOption, Layover, PriceInsights, SearchQuery, SearchResult, Segment

ENDPOINT = "https://serpapi.com/search.json"

_STOPS = {"any": 0, "nonstop": 1, "max1": 2, "max2": 3}
_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}


def build_params(q: SearchQuery) -> dict[str, Any]:
    params: dict[str, Any] = {
        "engine": "google_flights",
        "departure_id": ",".join(q.origins),
        "arrival_id": ",".join(q.destinations),
        "outbound_date": q.outbound_date,
        "type": 1 if q.return_date else 2,
        "adults": q.adults,
        "stops": _STOPS[q.stops],
        "travel_class": _CLASS[q.travel_class],
        "currency": q.currency,
        "hl": "de",
        "gl": q.point_of_sale.lower(),
    }
    if q.return_date:
        params["return_date"] = q.return_date
    if q.carry_on_bags:
        params["bags"] = q.carry_on_bags
    return params


class SerpApiProvider:
    name = "serpapi"

    def __init__(self, api_key: str, cache_dir: Path, cache_ttl_hours: float = 6, timeout: float = 60):
        self.api_key = api_key
        self.cache = DiskCache(cache_dir, cache_ttl_hours, "serpapi")
        self.timeout = timeout

    def search(self, q: SearchQuery, search_id: str) -> SearchResult:
        params = build_params(q)
        data = self.cache.get(params)
        cached = data is not None
        if data is None:
            try:
                resp = httpx.get(ENDPOINT, params={**params, "api_key": self.api_key}, timeout=self.timeout)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                return SearchResult(search_id=search_id, query=q, options=[], error=f"SerpApi nicht erreichbar: {exc}")
            if resp.status_code == 200 and "error" not in data:
                self.cache.set(params, data)
        return parse_response(data, q, search_id, cached=cached)


def parse_response(data: dict, q: SearchQuery, search_id: str, cached: bool = False) -> SearchResult:
    url = (data.get("search_metadata") or {}).get("google_flights_url")
    if "error" in data:
        # "Google Flights hasn't returned any results" ist kein echter Fehler, sondern "nichts gefunden".
        return SearchResult(search_id=search_id, query=q, options=[], error=data["error"], google_flights_url=url)

    price_note = (
        f"Gesamtpreis Hin- und Rückflug für {q.adults} Erw."
        if q.return_date
        else f"Preis One-Way für {q.adults} Erw."
    )
    options: list[FlightOption] = []
    groups = [(True, data.get("best_flights") or []), (False, data.get("other_flights") or [])]
    for is_best, items in groups:
        for item in items:
            idx = len(options) + 1
            segments = [_segment(f) for f in item.get("flights") or []]
            if not segments:
                continue
            layovers = [
                Layover(
                    airport=l.get("id", ""),
                    name=l.get("name", ""),
                    duration_min=l.get("duration"),
                    overnight=bool(l.get("overnight")),
                )
                for l in item.get("layovers") or []
            ]
            airlines = list(dict.fromkeys(s.airline for s in segments if s.airline))
            co2 = (item.get("carbon_emissions") or {}).get("this_flight")
            options.append(
                FlightOption(
                    id=f"{search_id}-{idx}",
                    search_id=search_id,
                    price=item.get("price"),
                    currency=q.currency,
                    trip_type=q.trip_type,
                    price_note=price_note,
                    airlines=airlines,
                    segments=segments,
                    layovers=layovers,
                    stops=len(layovers),
                    total_duration_min=item.get("total_duration"),
                    is_best=is_best,
                    extensions=[e for e in item.get("extensions") or [] if isinstance(e, str)],
                    co2_kg=round(co2 / 1000, 1) if isinstance(co2, (int, float)) else None,
                    booking_url=url,
                    point_of_sale=q.point_of_sale,
                )
            )

    pi = data.get("price_insights") or {}
    insights = (
        PriceInsights(
            lowest_price=pi.get("lowest_price"),
            price_level=pi.get("price_level"),
            typical_range=pi.get("typical_price_range"),
        )
        if pi
        else None
    )
    return SearchResult(
        search_id=search_id, query=q, options=options, insights=insights, google_flights_url=url, cached=cached
    )


def _segment(f: dict) -> Segment:
    dep = f.get("departure_airport") or {}
    arr = f.get("arrival_airport") or {}
    return Segment(
        from_airport=dep.get("id", ""),
        from_name=dep.get("name", ""),
        to_airport=arr.get("id", ""),
        to_name=arr.get("name", ""),
        departure=dep.get("time", ""),
        arrival=arr.get("time", ""),
        airline=f.get("airline", ""),
        flight_number=f.get("flight_number", ""),
        duration_min=f.get("duration"),
        extensions=[e for e in f.get("extensions") or [] if isinstance(e, str)],
    )
