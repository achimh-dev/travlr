"""Umkreis-Start: Flughäfen in Fahrweite vom Wohnort schätzen.

Fahrzeiten sind grobe Schätzungen (Luftlinie × Umwegfaktor / Durchschnittstempo), keine Routenplanung.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

# Passagierflughäfen in Deutschland und Nachbarländern (IATA, Name, Breite, Länge)
AIRPORTS: list[tuple[str, str, float, float]] = [
    # Deutschland
    ("DTM", "Dortmund", 51.518, 7.612), ("DUS", "Düsseldorf", 51.289, 6.767), ("CGN", "Köln/Bonn", 50.866, 7.143),
    ("NRN", "Weeze (Niederrhein)", 51.602, 6.142), ("PAD", "Paderborn/Lippstadt", 51.614, 8.616),
    ("FMO", "Münster/Osnabrück", 52.134, 7.685), ("HAN", "Hannover", 52.461, 9.685), ("BRE", "Bremen", 53.047, 8.787),
    ("HAM", "Hamburg", 53.630, 9.988), ("LBC", "Lübeck", 53.805, 10.719), ("FRA", "Frankfurt", 50.033, 8.571),
    ("HHN", "Frankfurt-Hahn", 49.949, 7.264), ("KSF", "Kassel", 51.408, 9.378), ("ERF", "Erfurt-Weimar", 50.980, 10.958),
    ("STR", "Stuttgart", 48.690, 9.222), ("FKB", "Karlsruhe/Baden-Baden", 48.779, 8.080),
    ("SCN", "Saarbrücken", 49.215, 7.110), ("MUC", "München", 48.354, 11.786), ("NUE", "Nürnberg", 49.499, 11.067),
    ("FMM", "Memmingen", 47.989, 10.239), ("FDH", "Friedrichshafen", 47.671, 9.511), ("BER", "Berlin", 52.366, 13.503),
    ("LEJ", "Leipzig/Halle", 51.424, 12.236), ("DRS", "Dresden", 51.133, 13.767),
    # Niederlande, Belgien, Luxemburg
    ("AMS", "Amsterdam", 52.310, 4.768), ("EIN", "Eindhoven", 51.450, 5.375), ("RTM", "Rotterdam", 51.957, 4.437),
    ("MST", "Maastricht", 50.912, 5.770), ("GRQ", "Groningen", 53.120, 6.580), ("BRU", "Brüssel", 50.901, 4.484),
    ("CRL", "Brüssel-Charleroi", 50.459, 4.453), ("LGG", "Lüttich", 50.637, 5.443), ("ANR", "Antwerpen", 51.189, 4.460),
    ("LUX", "Luxemburg", 49.627, 6.211),
    # Weitere Nachbarn
    ("CDG", "Paris CDG", 49.010, 2.548), ("ORY", "Paris Orly", 48.723, 2.379), ("BVA", "Paris-Beauvais", 49.454, 2.113),
    ("SXB", "Straßburg", 48.538, 7.628), ("BSL", "Basel-Mulhouse", 47.590, 7.529), ("ZRH", "Zürich", 47.464, 8.549),
    ("GVA", "Genf", 46.238, 6.109), ("SZG", "Salzburg", 47.793, 13.004), ("INN", "Innsbruck", 47.260, 11.344),
    ("VIE", "Wien", 48.110, 16.570), ("PRG", "Prag", 50.101, 14.260), ("CPH", "Kopenhagen", 55.618, 12.656),
    ("BLL", "Billund", 55.740, 9.152), ("POZ", "Posen", 52.421, 16.826), ("WRO", "Breslau", 51.103, 16.886),
    ("KTW", "Kattowitz", 50.474, 19.080), ("WAW", "Warschau", 52.166, 20.967), ("GDN", "Danzig", 54.378, 18.466),
]

# Offline-Geokodierung für häufige Orte; alles andere über OpenStreetMap Nominatim.
CITIES: dict[str, tuple[float, float]] = {
    "dortmund": (51.514, 7.468), "essen": (51.456, 7.012), "bochum": (51.482, 7.216), "unna": (51.535, 7.689),
    "hagen": (51.367, 7.463), "gelsenkirchen": (51.517, 7.086), "wuppertal": (51.256, 7.150),
    "duisburg": (51.434, 6.762), "düsseldorf": (51.227, 6.773), "köln": (50.938, 6.960), "bonn": (50.737, 7.098),
    "aachen": (50.776, 6.084), "münster": (51.960, 7.626), "bielefeld": (52.030, 8.532), "paderborn": (51.719, 8.754),
    "kassel": (51.312, 9.479), "hannover": (52.375, 9.732), "bremen": (53.079, 8.802), "hamburg": (53.551, 9.993),
    "berlin": (52.520, 13.405), "leipzig": (51.340, 12.375), "dresden": (51.050, 13.738),
    "frankfurt": (50.110, 8.682), "stuttgart": (48.776, 9.183), "nürnberg": (49.452, 11.077),
    "münchen": (48.137, 11.575),
}

ROAD_FACTOR = 1.25  # Straße ist länger als Luftlinie
AVG_KMH = 105  # Autobahn-Mix inkl. Stadtverkehr
FIXED_H = 0.25  # Parken, Shuttle


@dataclass(frozen=True)
class NearbyAirport:
    code: str
    name: str
    km: float  # Luftlinie
    drive_h: float  # geschätzte Fahrzeit

    def label(self) -> str:
        return f"{self.code} ({self.name}, ca. {self.drive_h:.1f} h)"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def drive_hours(km: float) -> float:
    return round(km * ROAD_FACTOR / AVG_KMH + FIXED_H, 1)


def nearby_airports(home: tuple[float, float], max_drive_h: float) -> list[NearbyAirport]:
    found = []
    for code, name, lat, lon in AIRPORTS:
        km = haversine_km(home, (lat, lon))
        h = drive_hours(km)
        if h <= max_drive_h:
            found.append(NearbyAirport(code, name, round(km), h))
    return sorted(found, key=lambda a: a.drive_h)


CANDIDATE_MAX_H = 4.0  # so weit werden Flughäfen zur Auswahl angeboten


def airport_choices(profile) -> list[tuple[NearbyAirport, bool]]:
    """Alle Flughäfen bis CANDIDATE_MAX_H mit Auswahlstatus (Fahrzeit-Vorauswahl + manuelle Overrides)."""
    if profile.home_lat is None or profile.home_lon is None:
        return []
    out = []
    for a in nearby_airports((profile.home_lat, profile.home_lon), CANDIDATE_MAX_H):
        selected = profile.airport_overrides.get(a.code, a.drive_h <= profile.max_drive_h)
        out.append((a, selected))
    return out


def home_airports(profile, max_drive_h: float | None = None) -> list[NearbyAirport]:
    """Die ausgewählten Flughäfen, nach Fahrzeit sortiert; optional pro Suche weiter begrenzt."""
    chosen = [a for a, sel in airport_choices(profile) if sel]
    if max_drive_h is not None:
        chosen = [a for a in chosen if a.drive_h <= max_drive_h]
    return chosen


def ground_calculator(profile, max_drive_h: float | None = None):
    from .ground import GroundCalculator

    return GroundCalculator(home_airports(profile, max_drive_h), profile.car_cost_per_km, profile.access)


def geocode(place: str, timeout: float = 10) -> tuple[float, float] | None:
    """Ort → Koordinaten. Erst die eingebaute Liste, dann OpenStreetMap Nominatim (1 Anfrage, kein Key)."""
    key = place.strip().lower()
    if not key:
        return None
    if key in CITIES:
        return CITIES[key]
    try:
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": "TRAVLR/1.0 (private flight search tool)"},
            timeout=timeout,
        )
        hits = resp.json()
        if hits:
            return float(hits[0]["lat"]), float(hits[0]["lon"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None
    return None
