import json
from dataclasses import replace
from datetime import date

from app.agent import AgentRun
from app.config import get_settings
from app.geo import CITIES, ground_calculator, nearby_airports
from app.ground import AirportAccess, ground_cost, parking_price, trip_days
from app.models import Preferences, Profile
from app.providers.demo import DemoProvider
from app.watches import Watch, check_watch

LAT, LON = CITIES["dortmund"]


def dortmund_profile(**kw):
    return Profile(home_location="Dortmund", home_lat=LAT, home_lon=LON, max_drive_h=2.0,
                   airport_overrides={"AMS": True, "FRA": True}, car_cost_per_km=0.05, **kw)


def airport(code):
    return next(a for a in nearby_airports((LAT, LON), 4) if a.code == code)


def test_parking_interpolation_and_override():
    assert parking_price("DUS", 1) == 25 and parking_price("DUS", 7) == 55
    assert parking_price("DUS", 5) == round(40 + (55 - 40) * 2 / 4)
    assert parking_price("FRA", 21) == 120 + round((120 - 70) / 7 * 7)
    assert parking_price("PAD", 7) == 40  # Regionalflughafen-Schätzung
    own = AirportAccess(mode="car", parking=[10, 20, 30, 50])
    assert parking_price("DUS", 7, own) == 30


def test_trip_days():
    assert trip_days("2027-05-11", "2027-05-18") == 8
    assert trip_days("2027-05-11", "2027-05-18 06:30") == 8
    assert trip_days("2027-05-11", None) is None


def test_dtm_is_taxi_others_park():
    dtm = ground_cost(airport("DTM"), 8, 0.05)
    assert dtm.mode == "taxi" and dtm.parking == 0 and dtm.total > 0
    ams = ground_cost(airport("AMS"), 8, 0.05)
    assert ams.mode == "car" and ams.parking > 90 and "Parken AMS 8 Tage" in ams.note
    own_taxi = ground_cost(airport("DTM"), 8, 0.05, AirportAccess(mode="taxi", taxi_cost=50))
    assert own_taxi.total == 50


def test_agent_options_carry_ground_and_total():
    settings = replace(get_settings(), max_searches=3)
    run = AgentRun(settings, [DemoProvider()], prefs=Preferences(min_layover_min=None), profile=dortmund_profile())
    out = json.loads(run.do_search(origins="DTM,DUS,AMS", destinations="JFK", outbound_date="2027-05-11",
                                   return_date="2027-05-18"))
    opts = [o for s in out["sources"] for o in s["options"]]
    assert opts and all("total_eur" in o and o["total_eur"] == round(o["price"] + o["ground_eur"]) for o in opts)
    brief = run.user_message("NY", date(2026, 11, 13))
    assert "DTM: Taxi" in brief and "AMS: Auto" in brief and "Parken 1/3/7/14 Tage" in brief
    # zwei One-Ways: Parken über die Gesamtdauer
    a = json.loads(run.do_search(origins="DUS", destinations="JFK", outbound_date="2027-05-11"))["sources"][0]["options"][0]
    b = json.loads(run.do_search(origins="JFK", destinations="DUS", outbound_date="2027-05-18"))["sources"][0]["options"][0]
    run.do_recommend([{"option_ids": [a["id"], b["id"]], "award_ids": [], "title": "x", "reason": "y"}])
    pick = run.result("")["picks"][0]
    assert pick["ground"]["airport"] == "DUS" and pick["ground"]["days"] == 8
    assert pick["total_with_ground"] == round(pick["total_price"] + pick["ground"]["total"])


def test_watch_threshold_uses_total():
    calc = ground_calculator(dortmund_profile())
    w = Watch(name="NY", destinations="JFK", max_price=10_000, nights_min=7, nights_max=7, samples_per_check=1)
    w1, msgs = check_watch(w, DemoProvider(), None, ["AMS"], date(2026, 11, 13), ground=calc)
    assert w1.best.ground and w1.best.ground_note.startswith("Parken AMS")
    assert w1.history[-1].price == round(w1.best.price + w1.best.ground, 2)
    assert "gesamt" in msgs[0]["title"] and "Parken AMS" in msgs[0]["message"]
    # Schwelle zwischen Flugpreis und Gesamtpreis → kein Alarm
    tight = w.model_copy(update={"max_price": w1.best.price + 1})
    assert check_watch(tight, DemoProvider(), None, ["AMS"], date(2026, 11, 13), ground=calc)[1] == []
