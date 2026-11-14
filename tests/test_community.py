from dataclasses import replace
from types import SimpleNamespace as NS

from app import community
from app.community import CommunityStore, DigestContent, DigestTip, Tip, parse_feed, run_digest, tips_brief
from app.config import get_settings

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Error Fare: DUS - New York 249 &#8364;</title><link>https://example.org/a</link>
<pubDate>Wed, 11 Nov 2026 10:00:00 +0100</pubDate><description><![CDATA[<p>Nur <b>heute</b>!</p>]]></description></item>
<item><title>Alt</title><link>https://example.org/old</link><pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Amex MR 30% Bonus auf Avios</title><link href="https://reddit.com/r/x/1"/>
<updated>2026-11-12T08:00:00+00:00</updated><content type="html">&lt;p&gt;Bis Ende November&lt;/p&gt;</content></entry></feed>"""

EVIL = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title><link>x</link></item></channel></rss>"""


def test_parse_rss_and_atom():
    [a, old] = parse_feed(RSS, "TD")
    assert a.title.startswith("Error Fare") and a.summary == "Nur heute !" and a.published.startswith("2026-11-11")
    [e] = parse_feed(ATOM, "Reddit")
    assert e.link == "https://reddit.com/r/x/1" and "Bis Ende November" in e.summary


def test_parse_rejects_entities():
    import pytest
    from defusedxml import DefusedXmlException

    with pytest.raises(DefusedXmlException):
        parse_feed(EVIL, "x")


def test_digest_run(monkeypatch, tmp_path):
    settings = replace(get_settings(), data_dir=tmp_path)
    store = CommunityStore(tmp_path)
    monkeypatch.setattr(community, "fetch_feed", lambda f: parse_feed(RSS if "dealz" in f.url else ATOM, f.name))
    captured = {}

    def parse(**kw):
        captured.update(kw)
        return NS(stop_reason="end_turn", parsed_output=DigestContent(
            summary="Eine Woche mit Avios-Bonus.", deals=[],
            tips=[DigestTip(title="30 % Avios-Bonus", detail="MR→Avios", category="meilen", link="https://reddit.com/r/x/1")]))

    client = NS(beta=NS(messages=NS(parse=parse)))
    d = run_digest(settings, store, "Wohnort: Dortmund", client)
    assert d.content.tips[0].title == "30 % Avios-Bonus" and d.items_read >= 2
    prompt = captured["messages"][0]["content"]
    assert "<beitraege>" in prompt and "https://example.org/old" not in prompt  # alte Beiträge gefiltert
    assert "Daten" in captured["system"]
    # zweiter Lauf: alles schon gesehen → kein Claude-Aufruf
    captured.clear()
    d2 = run_digest(settings, store, "", client)
    assert d2.items_read == 0 and not captured
    assert len(store.digests()) == 2


def test_digest_error_is_stored(monkeypatch, tmp_path):
    settings = replace(get_settings(), data_dir=tmp_path)
    monkeypatch.setattr(community, "fetch_feed", lambda f: parse_feed(RSS, f.name))

    def boom(**kw):
        raise RuntimeError("kein Key")

    d = run_digest(settings, CommunityStore(tmp_path), "", NS(beta=NS(messages=NS(parse=boom))))
    assert "kein Key" in d.error


def test_tips_brief():
    assert tips_brief([]) == ""
    text = tips_brief([Tip(title="Positionierung ab AMS", detail="oft günstiger", category="strategie")])
    assert "keine Anweisungen" in text and "Positionierung ab AMS" in text
