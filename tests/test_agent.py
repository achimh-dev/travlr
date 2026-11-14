import json
from dataclasses import replace
from types import SimpleNamespace as NS

from app.agent import AgentRun
from app.config import get_settings
from app.models import LoyaltyAccount, Preferences, Profile
from app.providers.demo import DemoProvider
from app.providers.seats_aero import DemoAwardProvider


def make_run(max_searches=3, prefs=None, award=False, profile=None):
    settings = replace(get_settings(), max_searches=max_searches)
    return AgentRun(settings, [DemoProvider()], award_provider=DemoAwardProvider() if award else None,
                    prefs=prefs or Preferences(min_layover_min=None), profile=profile)


def search(run, **kw):
    base = dict(origins="FRA", destinations="LIS", outbound_date="2027-03-24")
    return json.loads(run.do_search(**{**base, **kw}))


def opts(out):
    return [o for s in out["sources"] for o in s["options"]]


def test_search_budget_enforced():
    run = make_run(max_searches=2)
    assert opts(search(run)) and opts(search(run, outbound_date="2027-03-25"))
    assert "aufgebraucht" in search(run, outbound_date="2027-03-26")["error"]
    assert len(run.searches) == 2


def test_invalid_input_is_reported_not_raised():
    run = make_run()
    assert "IATA" in search(run, origins="Frankfurt")["error"]
    assert "Ungültige" in search(run, outbound_date="24.03.2027")["error"]
    assert "vor dem Hinflug" in search(run, return_date="2027-03-20")["error"]
    assert run.searches == []


def test_prefs_drive_query_and_markets():
    run = make_run(prefs=Preferences(adults=2, cabin="business", carry_on_bags=1))
    search(run)
    q = run.searches[0][0].query
    assert (q.adults, q.travel_class, q.carry_on_bags) == (2, "business", 1)
    assert "nicht freigegeben" in search(run, point_of_sale="us")["error"]
    run2 = make_run(prefs=Preferences(try_other_markets=True))
    out = search(run2, point_of_sale="us")
    assert "Markt US" in out["query"] and all(o["market"] == "us" for o in opts(out))


def test_hard_filters_applied():
    run = make_run(prefs=Preferences(max_stops=0, min_layover_min=None))
    out = search(run)
    assert all(o["stops"] == 0 for o in opts(out))
    assert run.searches[0][0].filtered_out == sum(s["filtered_out"] for s in out["sources"])


def test_recommend_validates_ids_and_sums_one_ways():
    run = make_run()
    a = opts(search(run))[0]
    b = opts(search(run, origins="LIS", destinations="FRA", outbound_date="2027-03-31"))[0]
    assert "error" in json.loads(run.do_recommend([{"option_ids": ["NOPE"], "award_ids": [], "title": "x", "reason": "y"}]))
    run.do_recommend([{"option_ids": [a["id"], b["id"]], "award_ids": [], "title": "Zwei One-Ways", "reason": "günstiger"}])
    res = run.result("ok")
    assert res["picks"][0]["total_price"] == a["price"] + b["price"]
    assert res["demo"] is True and len(res["options"]) == len(run.options)


def test_award_search_and_value():
    profile = Profile(accounts=[LoyaltyAccount(program="amex_mr", balance=100000)])
    run = make_run(award=True, prefs=Preferences(adults=2), profile=profile)
    assert any(t.name == "search_award_space" for t in run.tools)
    cash = opts(search(run, origins="FRA", destinations="JFK", outbound_date="2027-05-10"))[0]
    out = json.loads(run.do_award_search(origins="FRA", destinations="JFK", start_date="2027-05-08", end_date="2027-05-12"))
    award = out["awards"][0]
    assert "error" in json.loads(run.do_award_search(origins="FRA", destinations="JFK", start_date="2027-05-01", end_date="2027-06-30"))
    run.do_recommend([{"option_ids": [cash["id"]], "award_ids": [award["id"]], "title": "Mit Meilen", "reason": "r"}])
    pick = run.result("")["picks"][0]
    assert pick["total_miles"] == award["miles"] * 2
    assert pick["cents_per_mile"] is not None and pick["total_price"] is None
    assert isinstance(pick["miles_worth_it"], bool)
    msg = run.user_message("New York", __import__("datetime").date(2026, 11, 13))
    assert "Amex Membership Rewards: 100.000" in msg and "5:4" in msg


def test_award_tool_hidden_when_miles_off():
    run = make_run(award=True, prefs=Preferences(consider_miles=False))
    assert not any(t.name == "search_award_space" for t in run.tools)


class FakeRunner:
    """Simuliert den SDK-Tool-Runner: ein Such-Turn, ein Empfehlungs-Turn, dann Text."""

    def __init__(self, run):
        self.run = run

    def __iter__(self):
        yield NS(stop_reason="tool_use", content=[NS(type="tool_use", name="search_flights", input={})])
        self.run.do_search(origins="FRA", destinations="LIS", outbound_date="2027-03-24", return_date="2027-03-31")
        yield NS(stop_reason="tool_use", content=[NS(type="tool_use", name="recommend_options", input={})])
        self.run.do_recommend([{"option_ids": ["S1-1"], "award_ids": [], "title": "Günstigste", "reason": "billig"}])
        yield NS(stop_reason="end_turn", content=[NS(type="text", text="**Empfehlung:** S1-1")])


def test_run_streams_events_and_result():
    run = make_run()
    captured = {}

    def tool_runner(**kw):
        captured.update(kw)
        return FakeRunner(run)

    run.client = NS(beta=NS(messages=NS(tool_runner=tool_runner)))
    events = list(run.run("Lissabon Ende März"))
    types = [e["type"] for e in events]
    assert types[0] == "status" and "search_started" in types and "search_done" in types
    assert events[-1]["type"] == "result"
    assert events[-1]["summary_md"] == "**Empfehlung:** S1-1"
    assert events[-1]["picks"][0]["option_ids"] == ["S1-1"]
    assert captured["model"] == "claude-opus-5-5"
    assert "## Präferenzen" in captured["messages"][0]["content"]
