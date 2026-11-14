"""Anreise- und Parkkosten je Abflughafen ("Kleingedrucktes" zum Flugpreis).

Modell:
- Auto: Energie-/Fahrtkosten (km × €/km, hin und zurück) + Parken für die Reisedauer.
- Taxi: fester Betrag hin und zurück (z. B. für den Flughafen um die Ecke), kein Parken.
- Parkpreise sind Schätzungen für vorab online gebuchtes Langzeit- oder Shuttle-Parken (Stand Ende 2026,
  grob nach Vergleichsportalen; vor Ort an der Schranke ist es oft ein Vielfaches). Pro Flughafen im Profil
  überschreibbar. Zwischen den Stützstellen 1/3/7/14 Tage wird linear interpoliert, darüber hinaus mit dem
  Tagessatz zwischen 7 und 14 Tagen fortgeschrieben.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from .geo import ROAD_FACTOR, NearbyAirport

PARKING_DAYS = (1, 3, 7, 14)

# Vorab gebucht, günstige offizielle Langzeit- oder Shuttle-Parkplätze (Schätzwerte in EUR)
PARKING_DEFAULTS: dict[str, tuple[float, float, float, float]] = {
    "FRA": (30, 45, 70, 120),
    "AMS": (35, 60, 95, 160),
    "BRU": (30, 50, 80, 135),
    "DUS": (25, 40, 55, 95),
    "CGN": (20, 30, 45, 75),
    "HAM": (25, 40, 60, 100),
    "MUC": (30, 45, 70, 120),
    "HAN": (20, 35, 50, 85),
}
PARKING_REGIONAL = (15, 25, 40, 65)  # kleinere Flughäfen (DTM, FMO, PAD, NRN, EIN, MST, KSF …)


class AirportAccess(BaseModel):
    """Anreise-Einstellung für einen Flughafen im Profil."""

    mode: str = "car"  # car | taxi
    taxi_cost: float | None = None  # hin + zurück
    parking: list[float] | None = None  # Preise für 1/3/7/14 Tage


class GroundCost(BaseModel):
    airport: str
    mode: str
    days: int | None
    drive: float = 0
    parking: float = 0
    taxi: float = 0
    total: float = 0
    note: str = ""


def parking_table(code: str, override: AirportAccess | None = None) -> tuple[float, ...]:
    if override and override.parking and len(override.parking) == 4:
        return tuple(override.parking)
    return PARKING_DEFAULTS.get(code, PARKING_REGIONAL)


def parking_price(code: str, days: int, override: AirportAccess | None = None) -> float:
    table = parking_table(code, override)
    days = max(1, days)
    pts = list(zip(PARKING_DAYS, table))
    for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
        if days <= d1:
            return round(p0 + (p1 - p0) * (max(days, d0) - d0) / (d1 - d0))
    per_day = (table[3] - table[2]) / 7
    return round(table[3] + per_day * (days - 14))


def default_taxi_cost(airport: NearbyAirport) -> float:
    """Grobe Taxi-Schätzung hin und zurück: 4 € Grundpreis + 2,60 €/km Straße."""
    road_km = airport.km * ROAD_FACTOR
    return round(2 * (4 + 2.6 * road_km), -1)


def default_mode(airport: NearbyAirport) -> str:
    return "taxi" if airport.drive_h <= 0.5 else "car"


def trip_days(outbound: str | None, inbound: str | None) -> int | None:
    """Parktage: vom Abflug- bis zum Rückkehrtag, beide mitgezählt."""
    if not outbound or not inbound:
        return None
    try:
        return (date.fromisoformat(inbound[:10]) - date.fromisoformat(outbound[:10])).days + 1
    except ValueError:
        return None


def ground_cost(airport: NearbyAirport, days: int | None, car_cost_per_km: float,
                access: AirportAccess | None = None) -> GroundCost:
    mode = access.mode if access else default_mode(airport)
    if mode == "taxi":
        taxi = access.taxi_cost if access and access.taxi_cost is not None else default_taxi_cost(airport)
        return GroundCost(airport=airport.code, mode="taxi", days=days, taxi=taxi, total=taxi,
                          note=f"Taxi {airport.code} hin+zurück ~{taxi:.0f} €")
    drive = round(airport.km * ROAD_FACTOR * 2 * car_cost_per_km)
    if days is None:
        return GroundCost(airport=airport.code, mode="car", days=None, drive=drive, total=drive,
                          note=f"Auto {airport.code} ~{drive:.0f} € + Parken (Dauer unbekannt)")
    park = parking_price(airport.code, days, access)
    return GroundCost(airport=airport.code, mode="car", days=days, drive=drive, parking=park, total=drive + park,
                      note=f"Parken {airport.code} {days} Tage ~{park:.0f} € (Schätzung, vorab gebucht)"
                      + (f" + Fahrt ~{drive:.0f} €" if drive else ""))


class GroundCalculator:
    """Berechnet Anreisekosten für die im Profil gewählten Flughäfen."""

    def __init__(self, airports: list[NearbyAirport], car_cost_per_km: float, access: dict[str, AirportAccess]):
        self.airports = {a.code: a for a in airports}
        self.car_cost_per_km = car_cost_per_km
        self.access = access

    def cost(self, code: str, days: int | None) -> GroundCost | None:
        a = self.airports.get(code)
        if a is None:
            return None  # kein Heimatflughafen (z. B. Positionierung): Anreise separat
        return ground_cost(a, days, self.car_cost_per_km, self.access.get(code))

    def brief(self) -> str:
        """Tabelle für den Agenten."""
        lines = []
        for code, a in self.airports.items():
            acc = self.access.get(code)
            mode = acc.mode if acc else default_mode(a)
            if mode == "taxi":
                c = ground_cost(a, None, self.car_cost_per_km, acc)
                lines.append(f"- {code}: Taxi, ~{c.total:.0f} € hin+zurück, kein Parken")
            else:
                t = parking_table(code, acc)
                drive = round(a.km * ROAD_FACTOR * 2 * self.car_cost_per_km)
                lines.append(f"- {code}: Auto ({a.drive_h:.1f} h, Fahrt ~{drive} €) + Parken 1/3/7/14 Tage: "
                             + "/".join(f"{p:.0f}" for p in t) + " €")
        return "\n".join(lines)
