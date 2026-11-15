"""Demo-Anbieter mit erfundenen, aber plausiblen Daten.

Wird verwendet, wenn kein SERPAPI_API_KEY gesetzt ist, damit man Oberfläche und Agent
ohne Kontingent ausprobieren kann. Alle Ergebnisse sind klar als DEMO markiert.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta

from ..models import FlightOption, Layover, PriceInsights, SearchQuery, SearchResult, Segment
from .serpapi import price_note_for

_AIRLINES = [
    ("Lufthansa", "LH", 1.35),
    ("Eurowings", "EW", 0.95),
    ("Ryanair", "FR", 0.7),
    ("easyJet", "U2", 0.8),
    ("TAP Air Portugal", "TP", 1.1),
    ("KLM", "KL", 1.25),
    ("Condor", "DE", 1.0),
    ("Vueling", "VY", 0.85),
]
_HUBS = ["MUC", "AMS", "ZRH", "VIE", "MAD", "CDG"]


class DemoProvider:
    name = "demo"

    def search(self, q: SearchQuery, search_id: str) -> SearchResult:
        # Seed ohne leere Gabelflug-Felder, damit normale Suchen die gleichen Demo-Daten liefern wie bisher
        seed_src = q.model_dump_json(exclude=None if q.open_jaw else {"return_from", "return_to"})
        seed = int(hashlib.sha256(seed_src.encode()).hexdigest()[:12], 16)
        rnd = random.Random(seed)
        base = rnd.uniform(60, 220) * q.adults * (1.8 if q.return_date else 1.0)
        base *= {"economy": 1, "premium_economy": 1.8, "business": 3.5, "first": 6}[q.travel_class]
        # Wochentagseffekt: Di/Mi günstiger, Fr/So teurer
        weekday = datetime.fromisoformat(q.outbound_date).weekday()
        base *= [1.0, 0.88, 0.9, 1.0, 1.18, 1.05, 1.15][weekday]

        options: list[FlightOption] = []
        for i in range(rnd.randint(4, 8)):
            name, code, factor = rnd.choice(_AIRLINES)
            origin = rnd.choice(q.origins)
            dest = rnd.choice(q.destinations)
            stops = 0 if q.stops == "nonstop" else rnd.choice([0, 0, 1, 1, 2] if q.stops != "max1" else [0, 1])
            low_cost = factor < 0.9
            price = round(base * factor * (1 - 0.12 * stops) + (25 * q.carry_on_bags if low_cost else 0))
            dep = datetime.fromisoformat(q.outbound_date) + timedelta(hours=rnd.randint(6, 21), minutes=rnd.choice([0, 15, 30, 45]))
            path = [origin] + rnd.sample([h for h in _HUBS if h not in (origin, dest)], stops) + [dest]
            segments, layovers, t = [], [], dep
            for a, b in zip(path, path[1:]):
                dur = rnd.randint(70, 190)
                arr = t + timedelta(minutes=dur)
                segments.append(
                    Segment(
                        from_airport=a, to_airport=b, departure=t.strftime("%Y-%m-%d %H:%M"),
                        arrival=arr.strftime("%Y-%m-%d %H:%M"), airline=name,
                        flight_number=f"{code} {rnd.randint(100, 9999)}", duration_min=dur,
                        extensions=["Großes Handgepäck nicht inklusive"] if low_cost and not q.carry_on_bags else [],
                    )
                )
                if b != dest:
                    wait = rnd.randint(45, 240)
                    layovers.append(Layover(airport=b, duration_min=wait))
                    t = arr + timedelta(minutes=wait)
            total = int((datetime.strptime(segments[-1].arrival, "%Y-%m-%d %H:%M") - dep).total_seconds() // 60)
            options.append(
                FlightOption(
                    id=f"{search_id}-{i + 1}", search_id=search_id, price=price, currency=q.currency,
                    trip_type=q.trip_type,
                    price_note=price_note_for(q) + " (DEMO)",
                    airlines=[name], segments=segments, layovers=layovers, stops=stops,
                    total_duration_min=total, is_best=i < 2, demo=True, source="demo",
                    point_of_sale=q.point_of_sale,
                    booking_url="https://www.google.com/travel/flights",
                )
            )
        options.sort(key=lambda o: o.price or 0)
        low = options[0].price if options else None
        return SearchResult(
            search_id=search_id, query=q, source="demo", options=options, demo=True,
            insights=PriceInsights(lowest_price=low, price_level=rnd.choice(["low", "typical", "high"]),
                                   typical_range=[round(base * 0.8), round(base * 1.3)]),
            google_flights_url="https://www.google.com/travel/flights",
        )


class DemoExplore:
    """Erfundene "günstigste Daten" für den Demo-Modus (wie Google Travel Explore mit festem Ziel)."""

    def cheapest_dates(self, origin, destination, month, duration, adults, travel_class="economy", one_way=False,
                       currency="EUR"):
        rnd = random.Random(f"{origin}{destination}{month}{duration}{adults}{travel_class}{one_way}")
        today = datetime.now().date()
        rows = []
        for _ in range(3):
            if month:
                year = today.year + (1 if month < today.month else 0)
                start = datetime(year, month, rnd.randint(1, 28)).date()
            else:
                start = today + timedelta(days=rnd.randint(14, 170))
            nights = {1: 2, 2: 7, 3: 14}.get(duration, 7)
            rows.append({
                "price": float(round(rnd.uniform(70, 400) * adults)), "currency": currency, "origin": origin,
                "destination": destination, "destination_name": destination, "country": "",
                "outbound": start.isoformat(),
                "inbound": None if one_way else (start + timedelta(days=nights)).isoformat(),
                "airline": rnd.choice([a[0] for a in _AIRLINES]), "stops": rnd.choice([0, 1]),
                "url": "https://www.google.com/travel/explore", "demo": True,
            })
        rows.sort(key=lambda r: r["price"])
        return rows, None
