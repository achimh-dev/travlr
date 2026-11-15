import base64

from fastapi.testclient import TestClient

from app import profile_store
from app.filters import violations
from app.loyalty import cents_per_mile, loyalty_brief
from app.models import FlightOption, Layover, LoyaltyAccount, Preferences, Profile, Segment


def option(**kw):
    seg = Segment(from_airport="FRA", to_airport="LIS", departure="2027-03-24 06:00", arrival="2027-03-24 09:00", airline="Ryanair")
    base = dict(id="x", search_id="S1", price=99, currency="EUR", trip_type="one_way", price_note="", airlines=["Ryanair"],
                segments=[seg], stops=1, total_duration_min=600, layovers=[Layover(airport="MAD", duration_min=30, overnight=True)])
    return FlightOption(**{**base, **kw})


def test_filters():
    o = option()
    assert violations(o, Preferences(min_layover_min=None)) == []
    v = violations(o, Preferences(max_stops=0, max_duration_h=8, min_layover_min=45, allow_overnight_layover=False,
                                  avoid_airlines=["ryanair"]))
    assert len(v) == 5


def test_loyalty():
    p = Profile(accounts=[LoyaltyAccount(program="amex_mr", balance=50000), LoyaltyAccount(program="ba_club", balance=10000)])
    text = loyalty_brief(p)
    assert "bis zu 40.000, insgesamt erreichbar 50.000" in text  # 50k MR → 40k Avios (5:4) + 10k vorhanden
    assert "Payback" in text
    assert cents_per_mile(1200, 60000, 200) == 1.67


def test_profile_roundtrip(tmp_path):
    p = Profile(home_airports="FRA,HHN", notes="x")
    profile_store.save(tmp_path, p)
    assert profile_store.load(tmp_path) == p
    (tmp_path / "profile.json").write_text("kaputt")
    assert profile_store.load(tmp_path, "MUC").home_airports == "MUC"


def test_login_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_USERNAME", "reisender")
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import auth
    from app.main import app

    auth._failures.clear()
    c = TestClient(app)
    assert c.get("/api/config").status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/healthz").status_code == 200 and c.get("/login").status_code == 200
    for icon in ("icon-maskable-512.png", "favicon-32.png", "apple-touch-icon.png"):
        assert c.get(f"/static/{icon}").status_code == 200  # ohne Login erreichbar (Homescreen)
    assert c.get("/static/icon.svg").status_code == 200  # Icons ohne Login (Homescreen)
    r = c.post("/login", data={"username": "reisender", "password": "falsch"}, follow_redirects=False)
    assert "error" in r.headers["location"]
    r = c.post("/login", data={"username": "Reisender", "password": "geheim"}, follow_redirects=False)
    assert r.headers["location"] == "/" and auth.COOKIE in r.cookies
    cfg = c.get("/api/config").json()
    assert cfg["demo"] is True and cfg["login"] is True
    assert c.put("/api/profile", json={"home_location": "Dortmund"}).json()["home_lat"] == 51.514
    nearby = c.get("/api/nearby").json()
    assert [a["code"] for a in nearby][:2] == ["DTM", "DUS"]
    assert {a["code"] for a in nearby if a["selected"]} >= {"DTM", "DUS", "CGN"}


def test_login_xss_and_rate_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "geheim")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app import auth
    from app.main import app

    auth._failures.clear()
    c = TestClient(app)
    assert "<script>" not in c.get("/login?error=<script>alert(1)</script>").text
    for _ in range(5):
        c.post("/login", data={"username": "admin", "password": "x"})
    r = c.post("/login", data={"username": "admin", "password": "geheim"}, follow_redirects=False)
    assert "Fehlversuche" in r.headers["location"]


def test_session_tamper(tmp_path):
    from dataclasses import replace

    from app import auth
    from app.config import get_settings

    s = replace(get_settings(), data_dir=tmp_path, app_username="admin", session_secret="")
    tok = auth.make_session(s, "admin")
    assert auth.check_session(s, tok)
    assert not auth.check_session(s, tok[:-2] + "xx")
    assert not auth.check_session(replace(s, session_secret="anders"), tok)


def test_cf_access_required(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "abc")
    from app.main import app

    c = TestClient(app)
    assert c.get("/api/config").status_code == 403
    assert c.get("/api/config", headers={"cf-access-jwt-assertion": "kein.gueltiges.jwt"}).status_code == 403
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/healthz").json() == {"ok": True}


def test_icons_embedded(monkeypatch, tmp_path):
    """Homescreen-Icon und Manifest stecken als data:-URI in der Seite (Cloudflare Access, iPhone)."""
    import base64
    import json

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_URL", "https://travlr.example.de")
    from app.main import app, page_html

    page_html.cache_clear()
    c = TestClient(app)
    for page in (c.get("/").text, c.get("/login").text):
        assert 'rel="apple-touch-icon" href="data:image/png;base64,' in page
        assert "/static/apple-touch-icon.png" not in page
        start = page.index('rel="manifest" href="data:application/manifest+json;base64,') + 59
        manifest = json.loads(base64.b64decode(page[start:page.index('"', start)]))
        assert manifest["start_url"] == "https://travlr.example.de/"
        assert all(i["src"].startswith("data:image/png;base64,") for i in manifest["icons"])
    assert c.get("/manifest.webmanifest").json()["icons"][0]["src"] == "/static/icon-192.png"


def test_assets_have_version(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.main import app

    html = TestClient(app).get("/").text
    assert "/static/style.css?v=" in html and "/static/app.js?v=" in html
    assert 'id="mic"' in html
