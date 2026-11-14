from dataclasses import replace
from datetime import date

from fastapi.testclient import TestClient

from app.geo import CITIES, nearby_airports
from app.providers.demo import DemoProvider
from app.watches import Watch, WatchStore, _num, check_watch, parse_explore, sample_dates

TODAY = date(2026, 11, 13)


def test_nearby_dortmund():
    codes = [a.code for a in nearby_airports(CITIES["dortmund"], 2.0)]
    assert codes[0] == "DTM" and {"DUS", "CGN", "FMO", "PAD", "NRN"} <= set(codes)
    assert "FRA" not in codes and "AMS" not in codes


def test_sample_dates_rotate_and_stay_in_window():
    w = Watch(name="NY", destinations="JFK", max_price=300, date_from="2027-01-10", date_to="2027-02-10",
              nights_min=5, nights_max=8, samples_per_check=3)
    seen = set()
    for i in range(6):
        for out, ret in sample_dates(w.model_copy(update={"checks": i}), TODAY):
            assert "2027-01-10" <= out <= "2027-02-10"
            nights = (date.fromisoformat(ret) - date.fromisoformat(out)).days
            assert 5 <= nights <= 8
            seen.add(out)
    assert len(seen) >= 10  # rotiert über das Fenster


def test_route_check_and_notification_rules():
    w = Watch(name="Lissabon", destinations="LIS", max_price=10_000, samples_per_check=2)
    w1, msgs = check_watch(w, DemoProvider(), None, ["DTM", "DUS"], TODAY)
    assert w1.best and w1.checks == 1 and len(w1.history) == 1
    assert len(msgs) == 1 and "Lissabon" in msgs[0]["title"] and msgs[0]["click"]
    # gleiche Prüfung direkt danach → kein erneuter Alarm (Preis nicht 3 % gefallen, < 7 Tage)
    w2, msgs2 = check_watch(w1.model_copy(update={"checks": 0}), DemoProvider(), None, ["DTM", "DUS"], TODAY)
    assert msgs2 == []
    w_high = Watch(name="teuer", destinations="LIS", max_price=1)
    assert check_watch(w_high, DemoProvider(), None, ["DTM"], TODAY)[1] == []


def test_radar_without_serpapi_reports_error():
    w = Watch(kind="radar", name="Überall", max_price=200)
    w1, msgs = check_watch(w, DemoProvider(), None, ["DTM"], TODAY)
    assert "SERPAPI" in w1.last_error and msgs == []


class FakeExplore:
    def explore(self, origin, outbound, inbound, adults, max_price, currency="EUR"):
        data = {"destinations": [
            {"name": "New York", "country": "USA", "destination_airport": {"code": "JFK"}, "flight_price": 249,
             "start_date": outbound, "end_date": inbound, "airline": "Condor", "link": "https://g.co/x"},
            {"name": "Bangkok", "country": "Thailand", "destination_airport": {"code": "BKK"}, "flight_price": "1.155 €"},
        ]}
        return parse_explore(data, origin), None


def test_radar_filter_and_dedupe():
    w = Watch(kind="radar", name="USA", destinations="USA", max_price=300)
    w1, msgs = check_watch(w, DemoProvider(), FakeExplore(), ["DTM"], TODAY)
    assert len(msgs) == 1 and "New York" in msgs[0]["title"] and w1.best.destination == "JFK"
    assert len(w1.seen_deals) == 1
    # gleiches Ziel + gleiches Datum wird nicht erneut gemeldet
    _, again = check_watch(w1.model_copy(update={"checks": 0}), DemoProvider(), FakeExplore(), ["DTM"], TODAY)
    assert again == []


def test_num_parsing():
    assert _num(249) == 249 and _num("1.155 €") == 1155 and _num("$1,155.50") == 1155.5 and _num("299,99 €") == 299.99
    assert _num(None) is None and _num("–") is None


def test_store_update_keeps_config(tmp_path):
    store = WatchStore(tmp_path)
    w = store.upsert(Watch(name="A", destinations="JFK", max_price=300))
    store.upsert(w.model_copy(update={"max_price": 250}))  # Nutzer ändert Schwelle
    store.update(w.model_copy(update={"checks": 5}))  # Prüfung mit alter Kopie schreibt Zustand zurück
    [saved] = store.load()
    assert saved.max_price == 250 and saved.checks == 5


def test_watch_api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app

    c = TestClient(app)
    assert c.post("/api/watches", json={"name": "x", "max_price": 100}).status_code == 422  # Route ohne Ziel
    w = c.post("/api/watches", json={"name": "NY", "destinations": "JFK,EWR", "max_price": 300}).json()
    assert c.post(f"/api/watches/{w['id']}/check").json()["checks"] == 1
    listed = c.get("/api/watches").json()
    assert listed[0]["searches_per_month"] == 60 and listed[0]["origins_resolved"] == ["FRA"]
    edited = c.post("/api/watches", json={**w, "max_price": 280}).json()
    assert edited["checks"] == 1 and edited["max_price"] == 280  # Verlauf bleibt beim Bearbeiten
    assert c.delete(f"/api/watches/{w['id']}").json()["ok"]
    assert c.post("/api/notify/test").status_code == 400  # ntfy nicht konfiguriert


def test_dortmund_up_to_ams_and_fra():
    from app.geo import home_airports
    from app.models import Profile

    lat, lon = CITIES["dortmund"]
    p = Profile(home_location="Dortmund", home_lat=lat, home_lon=lon, max_drive_h=2.0,
                airport_overrides={"AMS": True, "FRA": True, "NRN": False})
    codes = [a.code for a in home_airports(p)]
    assert {"DTM", "DUS", "CGN", "AMS", "FRA"} <= set(codes)
    assert "NRN" not in codes and "HAN" not in codes and "BRU" not in codes
    assert [a.code for a in home_airports(p, max_drive_h=1.0)] == ["DTM", "DUS"]  # pro Suche enger


def test_route_check_splits_many_origins():
    from app.watches import origin_groups

    origins = ["DTM", "DUS", "CGN", "FMO", "PAD", "KSF", "MST", "EIN", "FRA", "AMS"]
    assert [len(g) for g in origin_groups(origins)] == [7, 3]
    seen = []

    class Rec(DemoProvider):
        def search(self, q, sid):
            seen.append(q.origins)
            return super().search(q, sid)

    w = Watch(name="NY", destinations="JFK", max_price=5000, samples_per_check=2)
    check_watch(w, Rec(), None, origins, TODAY)
    assert len(seen) == 4 and ["FRA", "AMS"] in [g[-2:] for g in seen]
    assert w.searches_per_month(len(origins)) == 120
