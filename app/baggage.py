"""Grobe Schätzung für Aufgabegepäck bei Low-Cost-Airlines.

Google Flights rechnet Handgepäck ein, wenn `bags` gesetzt ist, aber kein Aufgabegepäck. Bei Low-Cost-Airlines
ist ein Koffer oft ein großer Teil des Preises. Die Spannen sind Richtwerte pro Gepäckstück und Strecke bei
Online-Buchung (Stand November 2026); echte Preise hängen von Strecke, Datum und Buchungszeitpunkt ab und sind
am Flughafen deutlich höher. Netzwerk-Airlines fehlen bewusst: Ob ein Koffer enthalten ist, hängt dort vom Tarif ab.
"""

from __future__ import annotations

STAND = "November 2026"

# Airline-Name (klein, Teilstring) → (min, max) € pro Aufgabegepäck und Strecke
CHECKED_BAG_EUR: dict[str, tuple[float, float]] = {
    "ryanair": (25, 60),
    "wizz": (25, 60),
    "easyjet": (20, 50),
    "eurowings": (20, 45),
    "vueling": (20, 45),
    "transavia": (20, 45),
    "volotea": (20, 45),
    "pegasus": (20, 40),
    "sunexpress": (20, 40),
    "norwegian": (20, 50),
    "jet2": (20, 45),
}


def bag_range(airline: str) -> tuple[float, float] | None:
    a = airline.lower()
    return next((v for k, v in CHECKED_BAG_EUR.items() if k in a), None)


def estimate(airlines: list[str], checked_bags: int, adults: int, legs: int) -> tuple[float, str] | None:
    """Mittelwert der teuersten betroffenen Airline × Gepäck × Reisende × Strecken."""
    if checked_bags <= 0:
        return None
    ranges = [(a, r) for a in airlines if (r := bag_range(a))]
    if not ranges:
        return None
    airline, (lo, hi) = max(ranges, key=lambda x: x[1][1])
    n = checked_bags * adults * legs
    mid = round((lo + hi) / 2 * n)
    note = (f"{n} × Aufgabegepäck {airline} geschätzt {lo:.0f}–{hi:.0f} € je Stück und Strecke "
            f"(≈ {mid} €, Stand {STAND})")
    return float(mid), note
