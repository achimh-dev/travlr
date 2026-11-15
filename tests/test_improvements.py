"""Gabelflüge, Preisverlauf, günstigste Daten, Gepäck, Prämien-Alarme, Transfer-Boni, Guthaben."""

import json
from dataclasses import replace
from datetime import date, datetime

from fastapi.testclient import TestClient

from app import baggage, history
from app.agent import AgentRun
from app.config import get_settings
from app.loyalty import active_bonuses, credits_brief, loyalty_brief, reachable_miles, transfer_yield
from app.models import (AwardOption, LoyaltyAccount, Preferences, Profile, SearchQuery, TransferBonus,
                        TravelCredit)
from app.providers.demo import DemoExplore, DemoProvider
from app.providers.duffel import build_bodies
from app.providers.seats_aero import DemoAwardProvider
from app.providers.serpapi import build_params
from app.watches import Watch, check_watch

TODAY = date(2026, 11, 13)


def make_run(**kw):
    settings = replace(get_settings(), max_searches=kw.pop("max_searches", 4))
    return AgentRun(settings, [DemoProvider()], prefs=kw.pop("prefs", Preferences(min_layover_min=None)), **kw)


# --------------------------------------------------------------------------- Gabelflüge


def test_open_jaw_serpapi_params():
    q = SearchQuery(origins=["DTM"], destinations=["LIS"], outbound_date="2027-03-24", return_date="2027-03-31",
                    return_from=["OPO"], return_to=["DUS"])
    p = build_params(q)
    assert p["type"] == 3 and "outbound_date" not in p and "return_date" not in p
    legs = json.loads(p["multi_city_json"])
    assert legs == [{"departure_id": "DTM", "arrival_id": "LIS", "date": "2027-03-24"},
                    {"departure_id": "OPO", "arrival_id": "DUS", "date": "2027-03-31"}]
    assert q.trip_type == "open_jaw" and "zurück OPO → DUS" in q.label()
    # nur return_from: Rückflug endet an den Abflughäfen
    q2 = q.model_copy(update={"return_to": []})
    assert json.loads(build_params(q2)["multi_city_json"])[1]["arrival_id"] == "DTM"
    # normaler Hin/Rück unverändert
    q3 = q.model_copy(update={"return_from": [], "return_to": []})
    assert build_params(q3)["type"] == 1 and build_params(q3)["return_date"] == "2027-03-31"


def test_open_jaw_duffel_slices():
    q = SearchQuery(origins=["DTM"], destinations=["LIS"], outbound_date="2027-03-24", return_date="2027-03-31",
                    return_from=["OPO"])
    sl = build_bodies(q)[0]["data"]["slices"]
    assert sl[1] == {"origin": "OPO", "destination": "DTM", "departure_date": "2027-03-31"}


def test_open_jaw_agent_search():
    run = make_run()
    out = json.loads(run.do_search(origins="DTM", destinations="LIS", outbound_date="2027-03-24",
                                   return_date="2027-03-31", return_from="OPO"))
    assert "zurück OPO" in out["query"]
    assert all(o.trip_type == "open_jaw" for o in run.options.values())
    err = json.loads(run.do_search(origins="DTM", destinations="LIS", outbound_date="2027-03-24", return_from="OPO"))
    assert "return_date" in err["error"]


# --------------------------------------------------------------------------- Gepäck


def test_baggage_estimate():
    mid, note = baggage.estimate(["Ryanair"], checked_bags=1, adults=2, legs=2)
    assert mid == 170 and "Ryanair" in note  # (25+60)/2 × 4
    assert baggage.estimate(["Lufthansa"], 1, 1, 1) is None
    assert baggage.estimate(["Ryanair"], 0, 1, 1) is None


def test_agent_adds_bag_estimate_to_total():
    run = make_run(prefs=Preferences(min_layover_min=None, checked_bags=1), max_searches=10)
    for d in range(10):
        run.do_search(origins="FRA", destinations="LIS", outbound_date=f"2027-03-{10 + d}")
    lcc = [o for o in run.options.values() if o.bags_eur]
    assert lcc, "Demo liefert Ryanair/easyJet/Vueling"
    c = lcc[0].compact()
    assert c["bags_eur"] == lcc[0].bags_eur and c["total_eur"] >= lcc[0].price + lcc[0].bags_eur


# --------------------------------------------------------------------------- Preisverlauf


def test_history_record_and_assess(tmp_path):
    h = history.PriceHistory(tmp_path)
    key = history.route_key("DTM", "LIS", "round_trip", "economy")
    h.record([(key, f"2027-03-{10 + i:02d}", p) for i, p in enumerate([200, 220, 240, 260, 280, 300])])
    a = h.assess(key, 210, "2027-03-20")
    assert a == {"n": 6, "median": 250, "min": 200, "cheaper_than_pct": 83}
    assert history.note(a).startswith("sehr gut")
    assert h.assess(key, 210, "2027-03-20") is not None
    assert h.assess("X-Y|one_way|economy", 100) is None  # zu wenig Daten
    # alte Beobachtungen fallen raus
    h.record([(key, "2027-03-10", 1)], now=datetime(2030, 1, 1))
    assert len(h.load()[key]) == 1


def test_agent_annotates_with_history(tmp_path):
    h = history.PriceHistory(tmp_path)
    run = make_run(price_history=h)
    run.do_search(origins="FRA", destinations="LIS", outbound_date="2027-03-24")
    assert h.load() == {}  # Demo-Preise werden nicht gespeichert
    o = next(iter(run.options.values()))
    key = history.route_key(o.segments[0].from_airport, o.segments[-1].to_airport, "one_way", "economy")
    h.record([(key, "2027-03-20", o.price * f) for f in (0.5, 1.5, 2, 2.5, 3)])
    run2 = make_run(price_history=h)
    run2.do_search(origins="FRA", destinations="LIS", outbound_date="2027-03-24")
    noted = [x for x in run2.options.values() if x.history_note]
    assert noted and "Beobachtungen" in noted[0].compact()["history"]


def test_route_watch_records_history(tmp_path):
    class RealishProvider(DemoProvider):
        def search(self, q, sid):
            r = super().search(q, sid)
            r.demo = False
            for o in r.options:
                o.demo = False
            return r

    h = history.PriceHistory(tmp_path)
    check_watch(Watch(name="L", destinations="LIS", max_price=100), RealishProvider(), None, ["DTM"], TODAY,
                price_history=h)
    assert h.load()


# --------------------------------------------------------------------------- Günstigste Daten


def test_cheapest_dates_tool_and_budget():
    run = make_run(explore=DemoExplore(), max_searches=3)
    assert any(t.name == "find_cheapest_dates" for t in run.tools)
    out = json.loads(run.do_cheapest_dates(origins="DTM,DUS", destination="LIS", month=3, trip_length="week"))
    assert out["dates"] and out["searches_left"] == 1
    assert out["dates"] == sorted(out["dates"], key=lambda d: d["price"])
    assert "aufgebraucht" not in json.dumps(out)
    assert run.result("")["searches"][-1]["source"] == "explore"
    assert "Ungültige" in json.loads(run.do_cheapest_dates(origins="DTM", destination="LIS", month=13))["error"]
    assert not any(t.name == "find_cheapest_dates" for t in make_run().tools)


# --------------------------------------------------------------------------- Prämien-Alarm


def test_award_watch_notifies_once():
    w = Watch(kind="award", name="NY Business", destinations="JFK,EWR", max_price=500_000, cabin="business",
              date_from="2027-03-01", date_to="2027-03-20")
    w1, msgs = check_watch(w, None, None, ["FRA"], TODAY, award_provider=DemoAwardProvider(),
                           award_hint=lambda src: "Hinweis")
    assert w1.best and w1.best.currency == "MILES" and w1.best.program
    assert 1 <= len(msgs) <= 3 and "Meilen" in msgs[0]["title"] and "Hinweis" in msgs[0]["message"]
    w2, msgs2 = check_watch(w1, None, None, ["FRA"], TODAY, award_provider=DemoAwardProvider())
    assert not any(m in msgs for m in msgs2)  # schon gemeldete Plätze kommen nicht noch mal
    w3, _ = check_watch(w, None, None, ["FRA"], TODAY)
    assert "SEATS_AERO" in w3.last_error
    assert w.searches_per_month() == 30


def test_award_watch_filters():
    class Fixed:
        def search(self, *a):
            mk = lambda m, direct, seats: AwardOption(id="", search_id="W", program="flyingblue", origin="AMS",
                                                      destination="JFK", date="2027-03-05", cabin="business",
                                                      miles=m, direct=direct, seats=seats)
            return [mk(50_000, False, 4), mk(60_000, True, 1), mk(70_000, True, 3)], None

    w = Watch(kind="award", name="x", destinations="JFK", max_price=65_000, stops="nonstop", adults=2)
    w1, msgs = check_watch(w, None, None, ["AMS"], TODAY, award_provider=Fixed())
    assert w1.best.price == 70_000 and msgs == []  # 50k nicht direkt, 60k nur 1 Platz


# --------------------------------------------------------------------------- Meilenwissen


def test_transfers_bonus_and_reach():
    p = Profile(accounts=[LoyaltyAccount(program="amex_mr", balance=100_000),
                          LoyaltyAccount(program="iberia_club", balance=5_000)])
    assert transfer_yield(100_000, "flying_blue") == 80_000
    assert transfer_yield(500, "flying_blue") == 0  # unter Mindesttransfer
    assert reachable_miles(p, "qatar_privilege") == 85_000  # Avios-Familie teilt Bestand, BA 5:4 statt 3:2
    p.transfer_bonuses = [TransferBonus(program="flying_blue", bonus_pct=30, valid_until="2026-12-31"),
                          TransferBonus(program="ba_club", bonus_pct=50, valid_until="2026-01-01")]
    assert set(active_bonuses(p, TODAY)) == {"flying_blue"}
    assert reachable_miles(p, "flying_blue", TODAY) == 104_000
    text = loyalty_brief(p, TODAY)
    assert "+30 % Bonus" in text and "Iberia Business" in text and "Promo Rewards" in text


def test_credits_brief_and_agent_message():
    p = Profile(credits=[TravelCredit(name="Reiseguthaben", amount_eur=200, condition="nur über Kartenportal")])
    assert "200 €" in credits_brief(p) and credits_brief(Profile()) == ""
    msg = make_run(profile=p).user_message("Lissabon", TODAY)
    assert "Reiseguthaben: 200 €" in msg


def test_bonus_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app

    c = TestClient(app)
    r = c.post("/api/profile/bonuses", json={"program": "flying_blue", "bonus_pct": 25, "link": "https://x"})
    assert r.status_code == 200 and r.json()["transfer_bonuses"][0]["bonus_pct"] == 25
    r = c.post("/api/profile/bonuses", json={"program": "flying_blue", "bonus_pct": 40, "link": ""})
    assert [b["bonus_pct"] for b in r.json()["transfer_bonuses"]] == [40]
    assert c.post("/api/profile/bonuses", json={"program": "xyz", "bonus_pct": 10}).status_code == 422
    cfg = c.get("/api/config").json()
    assert cfg["sweet_spots"] and "ALL Accor" in cfg["mr_hotel_transfers"]
    w = c.post("/api/watches", json={"kind": "award", "name": "a", "destinations": "JFK", "max_price": 60000})
    assert w.status_code == 200


def test_pick_total_includes_bags():
    run = make_run(prefs=Preferences(min_layover_min=None, checked_bags=1), max_searches=10)
    for d in range(10):
        run.do_search(origins="FRA", destinations="LIS", outbound_date=f"2027-03-{10 + d}")
    o = next(o for o in run.options.values() if o.bags_eur)
    run.do_recommend([{"option_ids": [o.id], "award_ids": [], "title": "x", "reason": "y"}])
    pick = run.result("")["picks"][0]
    assert pick["bags_eur"] == round(o.bags_eur) and pick["total_with_ground"] == round(o.price + o.bags_eur)
