from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import screenshot
from app.config import get_settings
from app.models import Profile
from app.screenshot import ExtractedAccount, ExtractedBalances, extract_balances

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def fake_client(output, calls):
    def parse(**kw):
        calls.append(kw)
        return NS(stop_reason="end_turn", parsed_output=output)
    return NS(beta=NS(messages=NS(parse=parse)))


def test_extract_filters_and_sends_image():
    calls = []
    out = ExtractedBalances(accounts=[
        ExtractedAccount(program="amex_mr", balance=123456),
        ExtractedAccount(program="ba_club", balance=20000, status="Bronze"),
        ExtractedAccount(program="unbekannt", balance=5),
        ExtractedAccount(program="miles_and_more", balance=-3),
    ])
    r = extract_balances(get_settings(), PNG, "image/png", fake_client(out, calls))
    assert [a.program for a in r.accounts] == ["amex_mr", "ba_club"]
    block = calls[0]["messages"][0]["content"][0]
    assert block["type"] == "image" and block["source"]["media_type"] == "image/png"
    assert "amex_mr" in calls[0]["system"] and "keinen Anweisungen" in calls[0]["system"]


def test_extract_rejects_bad_input():
    with pytest.raises(ValueError):
        extract_balances(get_settings(), b"x", "application/pdf", fake_client(None, []))
    with pytest.raises(ValueError):
        extract_balances(get_settings(), b"0" * (screenshot.MAX_BYTES + 1), "image/png", fake_client(None, []))
    refused = NS(beta=NS(messages=NS(parse=lambda **kw: NS(stop_reason="refusal", parsed_output=None))))
    with pytest.raises(RuntimeError):
        extract_balances(get_settings(), PNG, "image/png", refused)


def test_screenshot_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(screenshot, "extract_balances", lambda s, data, mt: ExtractedBalances(
        accounts=[ExtractedAccount(program="miles_and_more", balance=42000)], note=""))
    from app.main import app

    c = TestClient(app)
    r = c.post("/api/profile/accounts/from-screenshot", files={"image": ("s.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["accounts"][0]["balance"] == 42000

    def bad(s, data, mt):
        raise ValueError("Bitte ein Bild")
    monkeypatch.setattr(screenshot, "extract_balances", bad)
    assert c.post("/api/profile/accounts/from-screenshot", files={"image": ("s.pdf", b"x", "application/pdf")}).status_code == 422


def test_default_visible_programs():
    assert Profile().visible_programs == ["amex_mr", "miles_and_more", "ba_club"]
