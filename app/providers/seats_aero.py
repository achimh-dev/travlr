"""Prämienflug-Verfügbarkeit über die seats.aero Partner-API (Pro-Abo, nicht-kommerzielle Nutzung).

Optional: nur aktiv, wenn SEATS_AERO_API_KEY gesetzt ist. Die Antwortfelder sind defensiv geparst;
die Anbindung ist gegen die öffentliche Doku geschrieben, aber noch nicht mit einem echten Key getestet.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import httpx

from .cache import DiskCache
from ..models import AwardOption

ENDPOINT = "https://seats.aero/partnerapi/search"
_CABIN_PREFIX = {"economy": "Y", "premium_economy": "W", "business": "J", "first": "F"}


class SeatsAeroProvider:
    name = "seats_aero"

    def __init__(self, api_key: str, cache_dir: Path, cache_ttl_hours: float = 6, timeout: float = 30):
        self.api_key = api_key
        self.cache = DiskCache(cache_dir, cache_ttl_hours, "seatsaero")
        self.timeout = timeout

    def search(self, origins: list[str], destinations: list[str], start: str, end: str, cabin: str,
               search_id: str) -> tuple[list[AwardOption], str | None]:
        params = {
            "origin_airport": ",".join(origins),
            "destination_airport": ",".join(destinations),
            "start_date": start,
            "end_date": end,
            "cabin": cabin,
            "take": 200,
        }
        data = self.cache.get(params)
        if data is None:
            try:
                resp = httpx.get(ENDPOINT, params=params, headers={"Partner-Authorization": self.api_key,
                                                                   "Accept": "application/json"},
                                 timeout=self.timeout)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                return [], f"seats.aero nicht erreichbar: {exc}"
            if resp.status_code >= 400:
                return [], f"seats.aero-Fehler {resp.status_code}: {data}"
            self.cache.set(params, data)
        return parse_availability(data, cabin, search_id), None


def parse_availability(data: dict, cabin: str, search_id: str, limit: int = 25) -> list[AwardOption]:
    p = _CABIN_PREFIX.get(cabin, "Y")
    found = []
    for row in data.get("data") or []:
        if not row.get(f"{p}Available"):
            continue
        try:
            miles = int(str(row.get(f"{p}MileageCost") or "0").replace(",", ""))
        except ValueError:
            continue
        if miles <= 0:
            continue
        route = row.get("Route") or {}
        taxes = row.get(f"{p}TotalTaxes")
        found.append(
            AwardOption(
                id="",
                search_id=search_id,
                program=row.get("Source") or route.get("Source") or "?",
                origin=route.get("OriginAirport", ""),
                destination=route.get("DestinationAirport", ""),
                date=str(row.get("Date", ""))[:10],
                cabin=cabin,
                miles=miles,
                # seats.aero liefert Steuern in der kleinsten Einheit (Cent)
                taxes=round(taxes / 100, 2) if isinstance(taxes, (int, float)) else None,
                taxes_currency=row.get("TaxesCurrency"),
                direct=row.get(f"{p}Direct"),
                airlines=row.get(f"{p}Airlines") or "",
                seats=row.get(f"{p}RemainingSeats"),
            )
        )
    found.sort(key=lambda a: a.miles)
    for i, a in enumerate(found[:limit]):
        a.id = f"{search_id}-{i + 1}"
    return found[:limit]


class DemoAwardProvider:
    """Erfundene Prämienverfügbarkeit für den Demo-Modus."""

    name = "demo_awards"

    def search(self, origins, destinations, start, end, cabin, search_id):
        rnd = random.Random(f"{origins}{destinations}{start}{end}{cabin}")
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        out = []
        for i in range(rnd.randint(2, 5)):
            day = d0 + timedelta(days=rnd.randint(0, max(0, (d1 - d0).days)))
            prog, airl = rnd.choice([("flyingblue", "AF, KL"), ("american", "IB, BA"), ("eurobonus", "SK")])
            base = {"economy": 15000, "premium_economy": 30000, "business": 55000, "first": 90000}[cabin]
            out.append(AwardOption(
                id=f"{search_id}-{i + 1}", search_id=search_id, program=prog, origin=rnd.choice(origins),
                destination=rnd.choice(destinations), date=day.isoformat(), cabin=cabin,
                miles=int(base * rnd.uniform(0.7, 1.6) // 500 * 500), taxes=round(rnd.uniform(30, 180)),
                taxes_currency="EUR", direct=rnd.random() < 0.5, airlines=airl, seats=rnd.randint(1, 6), demo=True,
            ))
        out.sort(key=lambda a: a.miles)
        return out, None
