"""Eigener Preisverlauf: jede echte Suche und jede Alarm-Prüfung hinterlässt Beobachtungen.

Schlüssel: Abflug-Ziel, Reiseart, Kabine. Gespeichert wird der günstigste Preis pro Person je Suche und Abflugmonat.
Damit lässt sich ein gefundener Preis einordnen ("günstiger als 80 % der bisherigen Beobachtungen") – genauer
als Googles grobes Preisniveau, weil es die eigenen Strecken über Monate kennt.
"""

from __future__ import annotations

import json
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .models import FlightOption, SearchQuery

_lock = threading.Lock()
MAX_PER_KEY = 400
MAX_AGE_DAYS = 400
MIN_OBS = 5


def route_key(origin: str, destination: str, trip_type: str, cabin: str) -> str:
    return f"{origin}-{destination}|{trip_type}|{cabin}"


class PriceHistory:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "price_history.json"

    def load(self) -> dict[str, list[dict]]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, entries: list[tuple[str, str, float]], now: datetime | None = None) -> None:
        """entries: (Schlüssel, Abflugdatum YYYY-MM-DD, Preis pro Person)."""
        if not entries:
            return
        now = now or datetime.now()
        cutoff = (now - timedelta(days=MAX_AGE_DAYS)).isoformat(timespec="seconds")
        t = now.isoformat(timespec="seconds")
        with _lock:
            data = self.load()
            for key, dep, price in entries:
                obs = [o for o in data.get(key, []) if o["t"] >= cutoff]
                obs.append({"t": t, "d": dep, "p": round(price, 2)})
                data[key] = obs[-MAX_PER_KEY:]
            self._save(data)

    def assess(self, key: str, price_pp: float, dep: str | None = None, data: dict | None = None) -> dict | None:
        """Einordnung eines Preises pro Person. Bevorzugt Beobachtungen mit Abflug im selben Monat (± 1)."""
        obs = (data if data is not None else self.load()).get(key, [])
        if dep:
            month = dep[:7]
            near = [o for o in obs if _month_distance(o["d"][:7], month) <= 1]
            if len(near) >= MIN_OBS:
                obs = near
        prices = [o["p"] for o in obs]
        if len(prices) < MIN_OBS:
            return None
        cheaper_than = sum(1 for p in prices if p > price_pp) / len(prices)
        return {"n": len(prices), "median": round(statistics.median(prices)), "min": round(min(prices)),
                "cheaper_than_pct": round(cheaper_than * 100)}


def _month_distance(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return abs((ya * 12 + ma) - (yb * 12 + mb))


def note(a: dict, currency: str = "EUR") -> str:
    sym = "€" if currency == "EUR" else currency
    if a["cheaper_than_pct"] >= 80:
        verdict = "sehr gut"
    elif a["cheaper_than_pct"] >= 50:
        verdict = "gut"
    elif a["cheaper_than_pct"] >= 20:
        verdict = "durchschnittlich"
    else:
        verdict = "teuer"
    return (f"{verdict}: günstiger als {a['cheaper_than_pct']} % von {a['n']} eigenen Beobachtungen "
            f"(Median {a['median']} {sym}, Bestwert {a['min']} {sym} pro Person)")


def entries_for(q: SearchQuery, options: list[FlightOption]) -> list[tuple[str, str, float]]:
    """Günstigster Preis pro Person je Abflug-/Zielflughafen-Paar einer Suche."""
    best: dict[str, float] = {}
    for o in options:
        if o.price is None or o.demo or not o.segments:
            continue
        key = route_key(o.segments[0].from_airport, o.segments[-1].to_airport, q.trip_type, q.travel_class)
        pp = o.price / max(1, q.adults)
        best[key] = min(best.get(key, pp), pp)
    return [(k, q.outbound_date, p) for k, p in best.items()]


def annotate(store: PriceHistory, q: SearchQuery, options: list[FlightOption]) -> None:
    data = store.load()
    for o in options:
        if o.price is None or not o.segments:
            continue
        key = route_key(o.segments[0].from_airport, o.segments[-1].to_airport, q.trip_type, q.travel_class)
        a = store.assess(key, o.price / max(1, q.adults), q.outbound_date, data)
        if a:
            o.history_note = note(a, o.currency)
