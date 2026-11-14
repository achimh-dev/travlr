"""Harte Präferenzen deterministisch anwenden, bevor der Agent die Optionen sieht."""

from __future__ import annotations

from .models import FlightOption, Preferences


def violations(o: FlightOption, p: Preferences) -> list[str]:
    v: list[str] = []
    if p.max_stops is not None and o.max_stops > p.max_stops:
        v.append("zu viele Umstiege")
    if p.max_duration_h is not None and o.total_duration_min and o.total_duration_min > p.max_duration_h * 60:
        v.append("zu lange Reisezeit")
    for l in o.all_layovers():
        if l.duration_min is None:
            continue
        if p.min_layover_min is not None and l.duration_min < p.min_layover_min:
            v.append(f"Umstieg zu knapp ({l.airport})")
        if p.max_layover_h is not None and l.duration_min > p.max_layover_h * 60:
            v.append(f"Umstieg zu lang ({l.airport})")
        if l.overnight and not p.allow_overnight_layover:
            v.append(f"Übernachtung beim Umstieg ({l.airport})")
    avoid = {a.strip().lower() for a in p.avoid_airlines if a.strip()}
    if avoid and any(any(x in airline.lower() for x in avoid) for airline in o.airlines):
        v.append("ausgeschlossene Airline")
    return v


def apply(options: list[FlightOption], p: Preferences) -> tuple[list[FlightOption], int]:
    kept = [o for o in options if not violations(o, p)]
    return kept, len(options) - len(kept)
