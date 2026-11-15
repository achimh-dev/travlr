"""Gemeinsame Datentypen für Flugsuchen, Präferenzen, Profil und Ergebnisse."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .ground import AirportAccess, GroundCost

StopsFilter = Literal["any", "nonstop", "max1", "max2"]
TravelClass = Literal["economy", "premium_economy", "business", "first"]
Priority = Literal["price", "balanced", "comfort"]


# --------------------------------------------------------------------------- Suche


class SearchQuery(BaseModel):
    origins: list[str] = Field(description="IATA-Codes der Abflughäfen")
    destinations: list[str] = Field(description="IATA-Codes der Zielflughäfen")
    outbound_date: str  # YYYY-MM-DD
    return_date: str | None = None  # None = One-Way
    # Gabelflug (Open Jaw): Rückflug ab/nach anderen Flughäfen. Leer = wie Hinflug umgekehrt.
    return_from: list[str] = []
    return_to: list[str] = []
    adults: int = 1
    carry_on_bags: int = 0
    stops: StopsFilter = "any"
    travel_class: TravelClass = "economy"
    currency: str = "EUR"
    point_of_sale: str = "de"  # Land, aus dem "gebucht" wird (Google-Flights-Parameter gl)

    @property
    def open_jaw(self) -> bool:
        return bool(self.return_date and (self.return_from or self.return_to))

    @property
    def trip_type(self) -> str:
        if self.open_jaw:
            return "open_jaw"
        return "round_trip" if self.return_date else "one_way"

    @property
    def return_origins(self) -> list[str]:
        return self.return_from or self.destinations

    @property
    def return_destinations(self) -> list[str]:
        return self.return_to or self.origins

    def label(self) -> str:
        route = f"{','.join(self.origins)} → {','.join(self.destinations)}"
        if self.open_jaw:
            route += f", zurück {','.join(self.return_origins)} → {','.join(self.return_destinations)}"
        dates = self.outbound_date + (f" / zurück {self.return_date}" if self.return_date else " (one-way)")
        pos = f" [Markt {self.point_of_sale.upper()}]" if self.point_of_sale != "de" else ""
        return f"{route}, {dates}{pos}"


class Segment(BaseModel):
    from_airport: str
    from_name: str = ""
    to_airport: str
    to_name: str = ""
    departure: str  # "YYYY-MM-DD HH:MM" Ortszeit
    arrival: str
    airline: str
    flight_number: str = ""
    duration_min: int | None = None
    extensions: list[str] = []


class Layover(BaseModel):
    airport: str
    name: str = ""
    duration_min: int | None = None
    overnight: bool = False


class FlightOption(BaseModel):
    id: str  # eindeutig innerhalb einer Agenten-Anfrage, z. B. "S2-3"
    search_id: str
    source: str = "google_flights"  # google_flights | duffel | demo
    price: float | None
    currency: str
    trip_type: str
    price_note: str  # z. B. "Gesamtpreis Hin+Rück für alle Reisenden"
    airlines: list[str]
    segments: list[Segment]  # Hinflug
    layovers: list[Layover] = []
    return_segments: list[Segment] = []  # nur wenn die Quelle den Rückflug mitliefert (Duffel)
    return_layovers: list[Layover] = []
    stops: int
    total_duration_min: int | None = None
    is_best: bool = False  # von Google Flights als "Top-Flug" markiert
    extensions: list[str] = []
    co2_kg: float | None = None
    booking_url: str | None = None
    point_of_sale: str = "de"
    fare_brand: str | None = None  # z. B. "Economy Light" (Duffel)
    baggage_note: str | None = None
    ground: GroundCost | None = None  # Anreise + Parken ab Heimatflughafen (Schätzung)
    bags_eur: float | None = None  # geschätzte Kosten für Aufgabegepäck (Low-Cost), alle Reisenden/Strecken
    bags_note: str | None = None
    history_note: str | None = None  # Einordnung gegen eigene Preisbeobachtungen
    demo: bool = False

    @property
    def departure(self) -> str:
        return self.segments[0].departure if self.segments else ""

    @property
    def arrival(self) -> str:
        return self.segments[-1].arrival if self.segments else ""

    @property
    def max_stops(self) -> int:
        return max(self.stops, len(self.return_layovers))

    def all_layovers(self) -> list[Layover]:
        return self.layovers + self.return_layovers

    def compact(self) -> dict:
        """Knappe Darstellung für den Agenten (spart Tokens)."""

        def route(segs: list[Segment]) -> str:
            return " → ".join([segs[0].from_airport] + [s.to_airport for s in segs]) if segs else ""

        d = {
            "id": self.id,
            "price": self.price,
            "src": self.source,
            "airlines": self.airlines,
            "route": route(self.segments),
            "dep": self.departure,
            "arr": self.arrival,
            "stops": self.stops,
            "duration_min": self.total_duration_min,
            "layovers": [_lay(l) for l in self.layovers],
            "notes": sorted({e for s in self.segments for e in s.extensions if _relevant(e)} | set(self.extensions)),
        }
        if self.return_segments:
            d["return_route"] = route(self.return_segments)
            d["return_dep"] = self.return_segments[0].departure
            d["return_layovers"] = [_lay(l) for l in self.return_layovers]
        if self.bags_eur:
            d["bags_eur"] = self.bags_eur
        if self.ground:
            d["ground_eur"] = self.ground.total
            d["ground"] = self.ground.note
            if self.price is not None and self.ground.days is not None:
                d["total_eur"] = round(self.price + self.ground.total + (self.bags_eur or 0))
        elif self.bags_eur and self.price is not None:
            d["total_eur"] = round(self.price + self.bags_eur)
        if self.history_note:
            d["history"] = self.history_note
        if self.fare_brand:
            d["fare"] = self.fare_brand
        if self.baggage_note:
            d["baggage"] = self.baggage_note
        if self.is_best:
            d["google_top"] = True
        if self.point_of_sale != "de":
            d["market"] = self.point_of_sale
        return d


def _lay(l: Layover) -> str:
    return f"{l.airport} {l.duration_min}min" + (" (über Nacht)" if l.overnight else "")


def _relevant(ext: str) -> bool:
    """Nur Hinweise weitergeben, die für Preis/Komfort relevant sind (Gepäck, Verspätungen …)."""
    e = ext.lower()
    keywords = ("bag", "gepäck", "carry", "delayed", "verspät", "legroom", "beinfreiheit", "seat", "sitz", "overnight",
                "self", "separate", "getrennt")
    return any(k in e for k in keywords)


class PriceInsights(BaseModel):
    lowest_price: float | None = None
    price_level: str | None = None  # low | typical | high
    typical_range: list[float] | None = None


class SearchResult(BaseModel):
    search_id: str
    query: SearchQuery
    source: str = "google_flights"
    options: list[FlightOption]
    insights: PriceInsights | None = None
    google_flights_url: str | None = None
    error: str | None = None
    cached: bool = False
    demo: bool = False
    filtered_out: int = 0  # durch harte Präferenzen aussortiert


# --------------------------------------------------------------------------- Prämienflüge


class AwardOption(BaseModel):
    id: str
    search_id: str
    program: str  # z. B. "flyingblue", "american" (Programm, über das man bucht)
    origin: str
    destination: str
    date: str
    cabin: str
    miles: int
    taxes: float | None = None
    taxes_currency: str | None = None
    direct: bool | None = None
    airlines: str = ""
    seats: int | None = None
    demo: bool = False

    def compact(self) -> dict:
        return self.model_dump(exclude={"search_id", "demo"}, exclude_none=True)


# --------------------------------------------------------------------------- Präferenzen & Profil


class Preferences(BaseModel):
    """Faktoren pro Suche. Harte Grenzen werden im Code gefiltert, weiche Faktoren gewichtet der Agent."""

    priority: Priority = "price"
    adults: int = Field(1, ge=1, le=9)
    cabin: TravelClass = "economy"
    flex_days: int = Field(2, ge=0, le=7)  # ± Tage um die Wunschdaten
    # Gepäck
    carry_on_bags: int = Field(0, ge=0, le=2)
    checked_bags: int = Field(0, ge=0, le=3)
    # Harte Grenzen (None = egal)
    max_stops: int | None = Field(None, ge=0, le=5)
    max_duration_h: float | None = Field(None, gt=0, le=72)
    min_layover_min: int | None = Field(45, ge=0, le=600)
    max_layover_h: float | None = Field(None, gt=0, le=48)
    allow_overnight_layover: bool = True
    avoid_airlines: list[str] = []
    # Strategien
    allow_separate_tickets: bool = True  # zwei One-Ways / Self-Transfer
    allow_positioning: bool = True  # Zubringerflug/Bahn zu einem günstigeren Abflugort
    max_ground_travel_h: float | None = Field(None, ge=0, le=12)  # None = Flughafenliste aus dem Profil
    try_other_markets: bool = False  # Preis aus anderem Land (Point of Sale) testen
    consider_miles: bool = True
    notes: str = ""  # Freitext, z. B. "nie mit Ryanair"


class TransferBonus(BaseModel):
    program: str  # Schlüssel aus loyalty.PROGRAMS
    bonus_pct: float = Field(gt=0, le=200)
    valid_until: str | None = None  # YYYY-MM-DD
    note: str = ""
    link: str = ""


class TravelCredit(BaseModel):
    name: str  # z. B. "Amex Platinum Reiseguthaben"
    amount_eur: float = Field(ge=0)
    condition: str = ""  # z. B. "nur bei Buchung über Amex Travel"
    valid_until: str | None = None


class LoyaltyAccount(BaseModel):
    program: str  # Schlüssel aus loyalty.PROGRAMS
    balance: int = 0
    status: str = ""  # z. B. "Frequent Traveller", "Bronze"


class Profile(BaseModel):
    """Dauerhafte Einstellungen, gespeichert in data/profile.json."""

    home_airports: str = "FRA"
    home_location: str = ""  # z. B. "Köln"; für den Umkreis-Start
    home_lat: float | None = None
    home_lon: float | None = None
    car_cost_per_km: float = 0.05  # Fahrtkosten Auto (E-Auto: Strom, ca. 0,05 €/km)
    max_drive_h: float = Field(2.0, ge=0, le=6)  # Vorauswahl der Flughäfen nach Fahrzeit
    # Manuelle Auswahl je Flughafen (True = immer dabei, False = nie), überschreibt die Fahrzeit-Vorauswahl
    airport_overrides: dict[str, bool] = {}
    # Anreise je Flughafen: Taxi oder Auto + Parken (eigene Parkpreise optional)
    access: dict[str, AirportAccess] = {}
    accounts: list[LoyaltyAccount] = [
        LoyaltyAccount(program="amex_mr"),
        LoyaltyAccount(program="miles_and_more"),
        LoyaltyAccount(program="ba_club"),
    ]
    card: str = "Amex Platinum"
    card_fx_fee_pct: float = 2.0  # Auslandseinsatzentgelt bei Zahlung in Fremdwährung
    min_value_cent_per_mile: float = 1.5  # ab diesem Gegenwert lohnt sich Meileneinsatz für dich
    transfer_bonuses: list[TransferBonus] = []  # aktuelle Aktionen (z. B. +30 % auf Flying Blue)
    credits: list[TravelCredit] = []  # Reiseguthaben, Gutscheine
    notes: str = ""
