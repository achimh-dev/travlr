import json
from pathlib import Path

from app.models import SearchQuery
from app.providers.serpapi import build_params, parse_response

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "serpapi_round_trip.json").read_text())
Q = SearchQuery(origins=["FRA", "HHN"], destinations=["LIS"], outbound_date="2027-03-24", return_date="2027-03-31")


def test_build_params_round_trip():
    p = build_params(Q.model_copy(update={"carry_on_bags": 1, "stops": "max1"}))
    assert p["departure_id"] == "FRA,HHN"
    assert p["type"] == 1 and p["return_date"] == "2027-03-31"
    assert p["stops"] == 2 and p["bags"] == 1
    assert "api_key" not in p


def test_build_params_one_way():
    p = build_params(Q.model_copy(update={"return_date": None}))
    assert p["type"] == 2 and "return_date" not in p and "bags" not in p


def test_parse_response():
    r = parse_response(FIXTURE, Q, "S1")
    assert r.error is None
    assert [o.id for o in r.options] == ["S1-1", "S1-2"]
    best, other = r.options
    assert best.is_best and best.stops == 0 and best.price == 238 and best.co2_kg == 226.0
    assert other.stops == 1 and other.layovers[0].airport == "MAD"
    assert other.booking_url.startswith("https://www.google.com/travel/flights")
    assert r.insights.price_level == "low"
    notes = other.compact()["notes"]
    assert "Großes Handgepäck nicht inklusive" in notes
    assert any("verspätet" in n for n in notes)
    # Irrelevante Hinweise (WLAN, CO2) werden für den Agenten weggelassen
    assert not any("WLAN" in n for n in best.compact()["notes"])


def test_parse_error():
    r = parse_response({"error": "Google Flights hasn't returned any results for this query."}, Q, "S2")
    assert r.options == [] and "results" in r.error
