"""Preisalarme ("Watches") und Deal-Radar.

- Route-Alarm: feste Strecke (z. B. Umkreis → JFK,EWR), Zeitfenster und Reisedauer. Pro Prüfung werden
  einige Datumspaare im Fenster abgefragt (rotierend), der beste Preis wird gespeichert und mit der
  Alarmschwelle verglichen. Google liefert dazu das Preisniveau (niedrig/typisch/hoch).
- Deal-Radar: "Wohin komme ich unter X €?" über Google Travel Explore (SerpApi), optional gefiltert
  nach Ländern oder Zielflughäfen.
- Prämien-Alarm: Prämienplätze (seats.aero) auf einer Strecke im Zeitfenster; Alarm, sobald ein Platz unter
  der Meilen-Schwelle frei wird. `max_price` ist hier die Schwelle in Meilen pro Person und Richtung.

Die Prüfungen laufen ohne Claude (keine KI-Kosten) und verbrauchen nur Flugsuchen.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from . import history
from .ground import trip_days
from .models import SearchQuery, StopsFilter, TravelClass
from .providers.cache import DiskCache

_lock = threading.Lock()
HISTORY_MAX = 180
ORIGINS_PER_SEARCH = 7  # vorsichtige Obergrenze für mehrere Abflughäfen in einer Google-Flights-Suche


def origin_groups(origins: list[str]) -> list[list[str]]:
    return [origins[i:i + ORIGINS_PER_SEARCH] for i in range(0, len(origins), ORIGINS_PER_SEARCH)] or [[]]


class PricePoint(BaseModel):
    t: str  # ISO-Zeitpunkt der Prüfung
    price: float


class BestFind(BaseModel):
    price: float
    currency: str = "EUR"
    origin: str = ""
    destination: str = ""
    destination_name: str = ""
    outbound: str = ""
    inbound: str | None = None
    airline: str = ""
    stops: int | None = None
    price_level: str | None = None
    typical_range: list[float] | None = None
    url: str | None = None
    checked_at: str = ""
    ground: float | None = None  # Anreise + Parken (Schätzung)
    ground_note: str = ""
    # Prämien-Alarm: price = Meilen pro Person, dazu Steuern und Programm
    taxes: float | None = None
    program: str = ""
    seats: int | None = None

    @property
    def total(self) -> float:
        return self.price + (self.ground or 0)


class Watch(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: Literal["route", "radar", "award"] = "route"
    name: str
    origins: str = ""  # leer = Flughäfen im Umkreis aus dem Profil
    destinations: str = ""  # Route: IATA-Codes. Radar: optionaler Filter (Länder oder IATA-Codes)
    date_from: str | None = None  # frühester Hinflug; None = in 7 Tagen
    date_to: str | None = None  # spätester Hinflug; None = in 90 Tagen
    nights_min: int = Field(5, ge=0, le=60)
    nights_max: int = Field(9, ge=0, le=60)
    max_price: float = Field(gt=0)  # Schwelle: Flug + Anreise/Parken gesamt; beim Prämien-Alarm Meilen p. P.
    adults: int = Field(1, ge=1, le=9)
    cabin: TravelClass = "economy"
    stops: StopsFilter = "any"
    samples_per_check: int = Field(2, ge=1, le=10)
    interval_hours: int = Field(24, ge=1, le=168)
    active: bool = True
    # Zustand
    checks: int = 0
    last_checked: str | None = None
    last_error: str | None = None
    best: BestFind | None = None  # bester Fund der letzten Prüfung
    best_ever: BestFind | None = None
    history: list[PricePoint] = []
    last_notified_price: float | None = None
    last_notified_at: str | None = None
    seen_deals: list[str] = []

    def window(self, today: date) -> tuple[date, date]:
        start = date.fromisoformat(self.date_from) if self.date_from else today + timedelta(days=7)
        end = date.fromisoformat(self.date_to) if self.date_to else today + timedelta(days=90)
        start = max(start, today + timedelta(days=1))
        return start, max(start, end)

    def due(self, now: datetime) -> bool:
        if not self.active:
            return False
        if not self.last_checked:
            return True
        return now - datetime.fromisoformat(self.last_checked) >= timedelta(hours=self.interval_hours)

    def searches_per_month(self, n_origins: int = 1) -> int:
        if self.kind == "award":
            return round(24 / self.interval_hours * 30)  # eine seats.aero-Abfrage pro Prüfung
        per_check = self.samples_per_check * (len(origin_groups(["x"] * n_origins)) if self.kind == "route" else 1)
        if self.kind == "radar":
            per_check = min(2, n_origins) or 1  # Radar: ein Datum, höchstens 2 Abflughäfen
        return round(per_check * 24 / self.interval_hours * 30)


# --------------------------------------------------------------------------- Speicher


class WatchStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "watches.json"

    def load(self) -> list[Watch]:
        if not self.path.is_file():
            return []
        try:
            return [Watch.model_validate(w) for w in json.loads(self.path.read_text(encoding="utf-8"))]
        except (ValueError, json.JSONDecodeError):
            return []

    def save(self, watches: list[Watch]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([w.model_dump() for w in watches], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def upsert(self, watch: Watch) -> Watch:
        with _lock:
            items = [w for w in self.load() if w.id != watch.id]
            items.append(watch)
            self.save(items)
        return watch

    def delete(self, watch_id: str) -> bool:
        with _lock:
            items = self.load()
            kept = [w for w in items if w.id != watch_id]
            self.save(kept)
        return len(kept) != len(items)

    def update(self, watch: Watch) -> None:
        """Zustand nach einer Prüfung zurückschreiben, ohne parallele Änderungen am Rest zu verlieren."""
        with _lock:
            items = self.load()
            for i, w in enumerate(items):
                if w.id == watch.id:
                    # Konfiguration aus der Datei behalten (falls inzwischen bearbeitet), Zustand übernehmen
                    state = watch.model_dump(include={"checks", "last_checked", "last_error", "best", "best_ever",
                                                      "history", "last_notified_price", "last_notified_at",
                                                      "seen_deals"})
                    items[i] = w.model_copy(update={k: getattr(watch, k) for k in state})
            self.save(items)


# --------------------------------------------------------------------------- Datumsauswahl


def sample_dates(w: Watch, today: date) -> list[tuple[str, str | None]]:
    """Datumspaare gleichmäßig über das Fenster verteilen; jede Prüfung startet versetzt (rotierend)."""
    start, end = w.window(today)
    span = (end - start).days
    n = w.samples_per_check
    pairs = []
    for i in range(n):
        frac = ((w.checks * n + i) * 0.618034) % 1  # Goldener Schnitt → gute Abdeckung über viele Prüfungen
        out = start + timedelta(days=round(frac * span))
        nights = w.nights_min + (w.checks + i) % (max(0, w.nights_max - w.nights_min) + 1)
        ret = (out + timedelta(days=nights)).isoformat() if nights > 0 else None
        pairs.append((out.isoformat(), ret))
    return list(dict.fromkeys(pairs))


# --------------------------------------------------------------------------- Google Travel Explore


class ExploreClient:
    """Google Travel Explore über SerpApi: Deal-Radar und günstigste Daten einer Strecke.
    Antwort wird tolerant geparst."""

    def __init__(self, api_key: str, cache_dir: Path, cache_ttl_hours: float = 6, timeout: float = 60):
        self.api_key = api_key
        self.cache = DiskCache(cache_dir, cache_ttl_hours, "explore")
        self.timeout = timeout

    def _get(self, params: dict) -> tuple[dict | None, str | None]:
        data = self.cache.get(params)
        if data is None:
            try:
                resp = httpx.get("https://serpapi.com/search.json", params={**params, "api_key": self.api_key},
                                 timeout=self.timeout)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                return None, f"SerpApi nicht erreichbar: {exc}"
            if "error" in data:
                return None, data["error"]
            self.cache.set(params, data)
        return data, None

    def explore(self, origin: str, outbound: str, inbound: str | None, adults: int, max_price: float,
                currency: str = "EUR") -> tuple[list[dict], str | None]:
        params = {"engine": "google_travel_explore", "departure_id": origin, "outbound_date": outbound,
                  "adults": adults, "currency": currency, "hl": "de", "gl": "de", "max_price": int(max_price)}
        if inbound:
            params["return_date"] = inbound
        data, err = self._get(params)
        return (parse_explore(data, origin, currency) if data else []), err

    def cheapest_dates(self, origin: str, destination: str, month: int, duration: int, adults: int,
                       travel_class: str = "economy", one_way: bool = False,
                       currency: str = "EUR") -> tuple[list[dict], str | None]:
        """Günstigste Reisedaten einer Strecke in einem Monat (0 = nächste 6 Monate).
        duration: 1 Wochenende, 2 eine Woche, 3 zwei Wochen."""
        params = {"engine": "google_travel_explore", "departure_id": origin, "arrival_id": destination,
                  "month": month, "travel_duration": duration, "adults": adults, "currency": currency,
                  "hl": "de", "gl": "de", "travel_class": _CLASS.get(travel_class, 1), "type": 2 if one_way else 1}
        data, err = self._get(params)
        return (parse_explore(data, origin, currency) if data else []), err


_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}


def parse_explore(data: dict, origin: str, currency: str = "EUR") -> list[dict]:
    out = []
    for d in data.get("destinations") or []:
        price = _num(d.get("flight_price"))
        if price is None:
            continue
        airport = d.get("destination_airport") or {}
        out.append({
            "price": price, "currency": currency, "origin": origin,
            "destination": airport.get("code") or d.get("destination_id") or "",
            "destination_name": ", ".join(x for x in (d.get("name"), d.get("country")) if x),
            "country": d.get("country") or "",
            "outbound": d.get("start_date") or "", "inbound": d.get("end_date"),
            "airline": d.get("airline") or "", "stops": d.get("number_of_stops"),
            "url": d.get("link") or d.get("google_flights_link"),
        })
    return sorted(out, key=lambda x: x["price"])


def _num(v) -> float | None:
    """Zahl aus 123, "123", "1.155 €" oder "$1,155.50" lesen."""
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    raw = "".join(c for c in v if c.isdigit() or c in ".,")
    if not raw:
        return None
    last = max(raw.rfind("."), raw.rfind(","))
    if last != -1 and len(raw) - last - 1 == 2:  # Dezimalstellen
        return float(raw[:last].replace(".", "").replace(",", "") + "." + raw[last + 1:])
    return float(raw.replace(".", "").replace(",", ""))


# --------------------------------------------------------------------------- Prüfen


def check_watch(w: Watch, flight_provider, explore: ExploreClient | None, origins: list[str], today: date,
                currency: str = "EUR", ground=None, award_provider=None, price_history=None,
                award_hint=None) -> tuple[Watch, list[dict]]:
    """Führt eine Prüfung aus. Gibt den aktualisierten Watch und auszulösende Benachrichtigungen zurück.
    award_hint(program) → kurzer Text, ob das Profil genug Meilen hat (optional)."""
    now = datetime.now().isoformat(timespec="seconds")
    w = w.model_copy(deep=True)
    if w.kind == "award":
        return _check_award(w, award_provider, origins, today, now, award_hint)
    finds: list[BestFind] = []
    errors: list[str] = []
    pairs = sample_dates(w, today)

    if w.kind == "route" and flight_provider is None:
        errors.append("Keine Flugquelle konfiguriert (SERPAPI_API_KEY oder DUFFEL_API_KEY).")
    elif w.kind == "route":
        dests = [c.strip().upper() for c in w.destinations.split(",") if c.strip()]
        for (out, ret), group in [(p, g) for p in pairs for g in origin_groups(origins)]:
            q = SearchQuery(origins=group, destinations=dests, outbound_date=out, return_date=ret, adults=w.adults,
                            stops=w.stops, travel_class=w.cabin, currency=currency)
            r = flight_provider.search(q, f"W{w.id}")
            if price_history is not None and not r.demo:
                price_history.record(history.entries_for(q, r.options))
            if r.error and not r.options:
                errors.append(r.error)
                continue
            priced = [o for o in r.options if o.price is not None]
            if not priced:
                continue
            o = min(priced, key=lambda o: o.price)
            finds.append(BestFind(
                price=o.price, currency=o.currency, origin=o.segments[0].from_airport,
                destination=o.segments[-1].to_airport, outbound=out, inbound=ret, airline=", ".join(o.airlines),
                stops=o.stops, price_level=r.insights.price_level if r.insights else None,
                typical_range=r.insights.typical_range if r.insights else None,
                url=r.google_flights_url or o.booking_url, checked_at=now,
            ))
    else:
        if explore is None:
            errors.append("Deal-Radar braucht einen SERPAPI_API_KEY (Google Travel Explore).")
        else:
            filt = [x.strip().lower() for x in w.destinations.split(",") if x.strip()]
            for origin in origins[:2]:
                for out, ret in pairs[:1]:
                    deals, err = explore.explore(origin, out, ret, w.adults, w.max_price * 1.5, currency)
                    if err:
                        errors.append(err)
                    for d in deals:
                        if filt and not any(f in (d["destination"].lower(), d["country"].lower(),
                                                  d["destination_name"].lower()) for f in filt):
                            continue
                        finds.append(BestFind(**{k: v for k, v in d.items() if k in BestFind.model_fields},
                                              checked_at=now))

    if ground is not None:
        for f in finds:
            g = ground.cost(f.origin, trip_days(f.outbound, f.inbound))
            if g is not None:
                f.ground, f.ground_note = g.total, g.note

    w.checks += 1
    w.last_checked = now
    w.last_error = "; ".join(dict.fromkeys(errors)) or None
    notifications: list[dict] = []
    if not finds:
        return w, notifications

    best = min(finds, key=lambda f: f.total)
    w.best = best
    w.history = (w.history + [PricePoint(t=now, price=round(best.total, 2))])[-HISTORY_MAX:]
    if w.best_ever is None or best.total < w.best_ever.total:
        w.best_ever = best

    if w.kind == "route":
        if best.total <= w.max_price and _should_notify(w, best.total, now):
            w.last_notified_price, w.last_notified_at = best.total, now
            notifications.append(_route_message(w, best))
    else:
        for f in sorted(finds, key=lambda f: f.total):
            key = f"{f.destination}|{f.outbound}"
            if f.total <= w.max_price and key not in w.seen_deals:
                w.seen_deals = (w.seen_deals + [key])[-300:]
                notifications.append(_radar_message(w, f))
                if len(notifications) >= 3:
                    break
    return w, notifications


def _check_award(w: Watch, award_provider, origins: list[str], today: date, now: str,
                 award_hint=None) -> tuple[Watch, list[dict]]:
    notifications: list[dict] = []
    w.checks += 1
    w.last_checked = now
    if award_provider is None:
        w.last_error = "Prämien-Alarm braucht einen SEATS_AERO_API_KEY (seats.aero Pro)."
        return w, notifications
    start, end = w.window(today)
    dests = [c.strip().upper() for c in w.destinations.split(",") if c.strip()]
    awards, err = award_provider.search(origins, dests, start.isoformat(), end.isoformat(), w.cabin, f"W{w.id}")
    w.last_error = err
    if w.stops == "nonstop":
        awards = [a for a in awards if a.direct]
    if w.adults > 1:
        awards = [a for a in awards if a.seats is None or a.seats >= w.adults]
    if not awards:
        return w, notifications
    finds = [BestFind(price=a.miles, currency="MILES", origin=a.origin, destination=a.destination,
                      outbound=a.date, airline=a.airlines, stops=0 if a.direct else None, checked_at=now,
                      taxes=a.taxes, program=a.program, seats=a.seats) for a in awards]
    best = min(finds, key=lambda f: (f.price, f.taxes or 0))
    w.best = best
    w.history = (w.history + [PricePoint(t=now, price=best.price)])[-HISTORY_MAX:]
    if w.best_ever is None or best.price < w.best_ever.price:
        w.best_ever = best
    for f in sorted(finds, key=lambda f: f.price):
        key = f"{f.program}|{f.origin}-{f.destination}|{f.outbound}|{int(f.price)}"
        if f.price <= w.max_price and key not in w.seen_deals:
            w.seen_deals = (w.seen_deals + [key])[-300:]
            notifications.append(_award_message(w, f, award_hint(f.program) if award_hint else ""))
            if len(notifications) >= 3:
                break
    return w, notifications


def _award_message(w: Watch, f: BestFind, hint: str = "") -> dict:
    taxes = f" + {f.taxes:.0f} € Steuern" if f.taxes is not None else ""
    seats = f", {f.seats} Plätze" if f.seats else ""
    return {
        "title": f"🎟 {w.name}: {f.price:,.0f} Meilen".replace(",", "."),
        "message": f"{f.origin} → {f.destination} am {_fmt_date(f.outbound)}, {w.cabin}, über {f.program}"
                   f"{taxes}{seats}. {f.airline}. {hint} Verfügbarkeit vor dem Transfer im Programm prüfen.".strip(),
        "click": None,
        "tags": ["ticket"],
        "priority": 4,
    }


def _should_notify(w: Watch, price: float, now: str) -> bool:
    if w.last_notified_price is None or w.last_notified_at is None:
        return True
    if price < w.last_notified_price * 0.97:  # spürbar günstiger als beim letzten Alarm
        return True
    return datetime.fromisoformat(now) - datetime.fromisoformat(w.last_notified_at) > timedelta(days=7)


_LEVEL = {"low": "niedrig", "typical": "typisch", "high": "hoch"}


def _fmt_date(d: str | None) -> str:
    return date.fromisoformat(d).strftime("%d.%m.") if d else ""


def _route_message(w: Watch, b: BestFind) -> dict:
    dates = _fmt_date(b.outbound) + (f"–{_fmt_date(b.inbound)}" if b.inbound else " (one-way)")
    level = f" · Preisniveau {_LEVEL.get(b.price_level, b.price_level)}" if b.price_level else ""
    cost = f" + {b.ground_note}" if b.ground else ""
    return {
        "title": f"✈ {w.name}: {b.total:.0f} € gesamt",
        "message": f"{b.origin} → {b.destination}, {dates}, {b.airline}{level}. Flug {b.price:.0f} €{cost}. "
                   f"Deine Schwelle: {w.max_price:.0f} €.",
        "click": b.url,
        "tags": ["airplane"],
        "priority": 4 if b.price <= w.max_price * 0.8 else 3,
    }


def _radar_message(w: Watch, f: BestFind) -> dict:
    dates = _fmt_date(f.outbound) + (f"–{_fmt_date(f.inbound)}" if f.inbound else "")
    return {
        "title": f"🔥 Deal-Radar {w.name}: {f.destination_name or f.destination} {f.total:.0f} €",
        "message": f"Ab {f.origin}, {dates}, {f.airline or 'Airline siehe Link'}. Flug {f.price:.0f} €"
                   + (f" + {f.ground_note}" if f.ground else "") + ".",
        "click": f.url,
        "tags": ["fire"],
        "priority": 4,
    }
