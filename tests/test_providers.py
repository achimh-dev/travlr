from app.models import SearchQuery
from app.providers.duffel import _iso_minutes, build_bodies, parse_offers
from app.providers.seats_aero import parse_availability

Q = SearchQuery(origins=["FRA", "HHN"], destinations=["LIS"], outbound_date="2027-03-24", return_date="2027-03-31",
                adults=2, stops="max1")


def seg(o, d, dep, arr, code="TP", num="573"):
    return {"origin": {"iata_code": o, "name": o}, "destination": {"iata_code": d, "name": d},
            "departing_at": dep, "arriving_at": arr, "duration": "PT2H",
            "marketing_carrier": {"name": "TAP Air Portugal", "iata_code": code},
            "marketing_carrier_flight_number": num,
            "passengers": [{"baggages": [{"type": "carry_on", "quantity": 1}, {"type": "checked", "quantity": 0}]}]}


def test_duffel_bodies():
    bodies = build_bodies(Q)
    assert len(bodies) == 2  # FRA-LIS, HHN-LIS
    d = bodies[0]["data"]
    assert d["slices"][1] == {"origin": "LIS", "destination": "FRA", "departure_date": "2027-03-31"}
    assert len(d["passengers"]) == 2 and d["max_connections"] == 1


def test_duffel_parse():
    offers = [{
        "total_amount": "311.40", "total_currency": "EUR",
        "slices": [
            {"duration": "PT5H10M", "fare_brand_name": "Discount",
             "segments": [seg("FRA", "MAD", "2027-03-24T06:00:00", "2027-03-24T08:30:00"),
                          seg("MAD", "LIS", "2027-03-24T09:40:00", "2027-03-24T10:10:00")]},
            {"duration": "PT3H", "segments": [seg("LIS", "FRA", "2027-03-31T18:00:00", "2027-03-31T22:00:00")]},
        ],
    }]
    [o] = parse_offers(offers, Q, "S1d")
    assert o.id == "S1d-1" and o.price == 311.4 and o.source == "duffel"
    assert o.stops == 1 and o.layovers[0].duration_min == 70 and not o.layovers[0].overnight
    assert o.return_segments[0].from_airport == "LIS" and o.total_duration_min == 310
    assert o.fare_brand == "Discount" and "Handgepäck: 1" in o.baggage_note
    assert o.compact()["return_route"] == "LIS → FRA"


def test_iso_minutes():
    assert _iso_minutes("P1DT2H5M") == 1565 and _iso_minutes("PT45M") == 45 and _iso_minutes("bad") is None


def test_seats_aero_parse():
    data = {"data": [
        {"Date": "2027-05-10", "Source": "flyingblue", "Route": {"OriginAirport": "FRA", "DestinationAirport": "JFK"},
         "JAvailable": True, "JMileageCost": "72000", "JDirect": False, "JAirlines": "AF, KL", "JRemainingSeats": 2,
         "JTotalTaxes": 21050, "TaxesCurrency": "EUR"},
        {"Date": "2027-05-11", "Source": "american", "Route": {"OriginAirport": "FRA", "DestinationAirport": "JFK"},
         "JAvailable": True, "JMileageCost": "57500"},
        {"Date": "2027-05-12", "Source": "united", "JAvailable": False, "JMileageCost": "60000"},
    ]}
    awards = parse_availability(data, "business", "A1")
    assert [a.miles for a in awards] == [57500, 72000]
    assert awards[0].id == "A1-1" and awards[1].taxes == 210.5 and awards[1].program == "flyingblue"
