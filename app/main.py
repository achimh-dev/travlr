"""TRAVLR Web-App: FastAPI-Backend + statische Oberfläche.

Start:  uvicorn app.main:app --host 0.0.0.0 --port 8000   →  http://localhost:8000
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import anthropic
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, community, notify, profile_store, scheduler
from .agent import AgentRun, AwardProvider, FlightProvider
from .config import Settings, get_settings
from .geo import airport_choices, geocode
from .ground import default_mode, default_taxi_cost, parking_table
from .history import PriceHistory
from .loyalty import MR_HOTEL_TRANSFERS, MR_TRANSFERS, PROGRAMS, STAND, SWEET_SPOTS
from .models import Preferences, Profile, TransferBonus
from .providers.demo import DemoExplore, DemoProvider
from .providers.duffel import DuffelProvider
from .providers.seats_aero import DemoAwardProvider, SeatsAeroProvider
from .providers.serpapi import SerpApiProvider
from .watches import ExploreClient, Watch, WatchStore

log = logging.getLogger("travlr")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="TRAVLR", lifespan=lifespan)


# --------------------------------------------------------------------------- Zugriffsschutz


@app.middleware("http")
async def access_guard(request: Request, call_next):
    s = get_settings()
    path = request.url.path
    if path in HEALTH_PATHS:
        return await call_next(request)
    # Stufe 1: Cloudflare Access (falls konfiguriert)
    if auth.cf_access_enabled(s) and not auth.check_cf_access(s, request.headers.get("cf-access-jwt-assertion")):
        return Response("Zugriff nur über Cloudflare Access.", status_code=403)
    # Stufe 2: eigenes Login (falls APP_PASSWORD gesetzt)
    if s.app_password and path not in auth.PUBLIC_PATHS and not auth.check_session(s, request.cookies.get(auth.COOKIE)):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "") -> str:
    msg = f'<p class="login-error">{html.escape(error[:120])}</p>' if error else ""
    return page_html("login.html", public_url(request)).replace("<!--ERROR-->", msg)


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    s = get_settings()
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    ok, err = auth.check_credentials(s, username, password, ip)
    if not ok:
        return RedirectResponse(f"/login?error={err}", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(s, s.app_username), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, secure=secure, samesite="lax")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


MANIFEST_ICONS = [("icon-192.png", "192x192", "any"), ("icon-512.png", "512x512", "any"),
                  ("icon-maskable-192.png", "192x192", "maskable"), ("icon-maskable-512.png", "512x512", "maskable")]


def public_url(request: Request) -> str:
    """Öffentliche Adresse (PUBLIC_URL), sonst die der Anfrage. Nötig für start_url im eingebetteten Manifest."""
    url = os.environ.get("PUBLIC_URL", "").strip() or str(request.base_url)
    return url.rstrip("/") + "/"


def manifest_data(start_url: str = "/", inline: bool = False) -> dict:
    src = (lambda name: data_uri(name)) if inline else (lambda name: f"/static/{name}")
    return {
        "name": "TRAVLR", "short_name": "TRAVLR", "start_url": start_url, "scope": start_url,
        "display": "standalone", "background_color": "#151515", "theme_color": "#151515", "lang": "de",
        "icons": [{"src": src(n), "sizes": sz, "type": "image/png", "purpose": p} for n, sz, p in MANIFEST_ICONS],
    }


@lru_cache(maxsize=None)
def data_uri(name: str) -> str:
    mime = "image/svg+xml" if name.endswith(".svg") else "image/png"
    return f"data:{mime};base64," + base64.b64encode((STATIC / name).read_bytes()).decode()


@lru_cache(maxsize=8)
def page_html(name: str, start_url: str) -> str:
    """HTML-Seite mit eingebetteten Icons und Manifest.

    Cloudflare Access lässt separate Abrufe ohne Login-Cookie nicht durch, das iPhone lädt das
    Homescreen-Icon aber genau so. Als data:-URI braucht es keinen eigenen Abruf.
    """
    manifest = json.dumps(manifest_data(start_url, inline=True)).encode()
    page = (STATIC / name).read_text(encoding="utf-8")
    for path, uri in {
        "/manifest.webmanifest": "data:application/manifest+json;base64," + base64.b64encode(manifest).decode(),
        "/static/icon.svg": data_uri("icon.svg"),
        "/static/favicon-32.png": data_uri("favicon-32.png"),
        "/static/apple-touch-icon.png": data_uri("apple-touch-icon.png"),
    }.items():
        page = page.replace(f'"{path}"', f'"{uri}"')
    return page


@app.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    return JSONResponse(manifest_data(), media_type="application/manifest+json")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    return page_html("index.html", public_url(request))


# /api/healthz: gleiche Antwort unter einem zweiten Pfad, z. B. für externes Monitoring
HEALTH_PATHS = ("/healthz", "/api/healthz")


@app.get("/healthz")
@app.get("/api/healthz")
def healthz() -> dict:
    return {"ok": True}


# --------------------------------------------------------------------------- Quellen


def make_providers(settings: Settings) -> tuple[list[FlightProvider], AwardProvider | None]:
    if settings.demo_mode:
        return [DemoProvider()], DemoAwardProvider()
    providers: list[FlightProvider] = []
    if settings.serpapi_api_key:
        providers.append(SerpApiProvider(settings.serpapi_api_key, settings.cache_dir, settings.cache_ttl_hours))
    if settings.duffel_api_key:
        providers.append(DuffelProvider(settings.duffel_api_key, settings.cache_dir, settings.cache_ttl_hours))
    award = (SeatsAeroProvider(settings.seats_aero_api_key, settings.cache_dir, settings.cache_ttl_hours)
             if settings.seats_aero_api_key else None)
    return providers, award


def make_explore(settings: Settings):
    if settings.demo_mode:
        return DemoExplore()
    if settings.serpapi_api_key:
        return ExploreClient(settings.serpapi_api_key, settings.cache_dir, settings.cache_ttl_hours)
    return None


@app.get("/api/config")
def config() -> dict:
    s = get_settings()
    providers, award = make_providers(s)
    return {
        "demo": s.demo_mode,
        "max_searches": s.max_searches,
        "currency": s.currency,
        "sources": [p.name for p in providers],
        "awards": award.name if award else None,
        "explore": bool(s.serpapi_api_key),
        "claude_configured": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "ntfy": notify.ntfy_configured(s),
        "login": bool(s.app_password),
        "programs": {k: v["name"] for k, v in PROGRAMS.items()},
        "mr_transfers": MR_TRANSFERS,
        "mr_hotel_transfers": MR_HOTEL_TRANSFERS,
        "sweet_spots": SWEET_SPOTS,
        "loyalty_stand": STAND,
    }


# --------------------------------------------------------------------------- Profil & Umkreis


@app.get("/api/profile")
def get_profile() -> Profile:
    s = get_settings()
    return profile_store.load(s.data_dir, s.home_airports)


@app.put("/api/profile")
def put_profile(profile: Profile) -> Profile:
    s = get_settings()
    old = profile_store.load(s.data_dir, s.home_airports)
    if profile.home_location and (profile.home_location != old.home_location or profile.home_lat is None):
        coords = geocode(profile.home_location)
        if coords is None:
            raise HTTPException(422, f"Ort '{profile.home_location}' nicht gefunden.")
        profile.home_lat, profile.home_lon = coords
    if not profile.home_location:
        profile.home_lat = profile.home_lon = None
    profile_store.save(s.data_dir, profile)
    return profile


@app.post("/api/profile/bonuses")
def add_bonus(bonus: TransferBonus) -> Profile:
    """Transfer-Bonus übernehmen (z. B. aus dem Community-Digest). Ersetzt einen Bonus desselben Programms."""
    if bonus.program not in MR_TRANSFERS:
        raise HTTPException(422, f"Unbekanntes Programm '{bonus.program}'.")
    s = get_settings()
    p = profile_store.load(s.data_dir, s.home_airports)
    p.transfer_bonuses = [b for b in p.transfer_bonuses if b.program != bonus.program] + [bonus]
    profile_store.save(s.data_dir, p)
    return p


@app.get("/api/nearby")
def nearby() -> list[dict]:
    s = get_settings()
    p = profile_store.load(s.data_dir, s.home_airports)
    return [{"code": a.code, "name": a.name, "km": a.km, "drive_h": a.drive_h, "selected": sel,
             "override": p.airport_overrides.get(a.code),
             "access": p.access[a.code].model_dump() if a.code in p.access else None,
             "default_mode": default_mode(a), "default_taxi": default_taxi_cost(a),
             "default_parking": list(parking_table(a.code))} for a, sel in airport_choices(p)]


# --------------------------------------------------------------------------- Agenten-Suche


class SearchRequest(BaseModel):
    request: str = Field(min_length=3, max_length=2000)
    prefs: Preferences = Preferences()
    max_searches: int | None = Field(default=None, ge=1, le=30)


@app.post("/api/search")
def search(req: SearchRequest) -> StreamingResponse:
    settings = get_settings()
    providers, award = make_providers(settings)
    profile = profile_store.load(settings.data_dir, settings.home_airports)
    tips = community.CommunityStore(settings.data_dir).tips()
    run = AgentRun(settings, providers, award_provider=award, prefs=req.prefs, profile=profile,
                   knowledge=community.tips_brief(tips), explore=make_explore(settings),
                   price_history=PriceHistory(settings.data_dir))
    if req.max_searches:
        run.max_searches = req.max_searches

    def stream() -> Iterator[str]:
        try:
            for event in run.run(req.request):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except anthropic.AuthenticationError:
            yield _err("Claude-API-Key fehlt oder ist ungültig (ANTHROPIC_API_KEY).")
        except anthropic.RateLimitError:
            yield _err("Claude-API: Rate-Limit erreicht. Bitte kurz warten und erneut versuchen.")
        except anthropic.APIStatusError as exc:
            log.exception("Claude API error")
            yield _err(f"Claude-API-Fehler ({exc.status_code}): {exc.message}")
        except anthropic.APIConnectionError:
            yield _err("Claude-API nicht erreichbar. Internetverbindung prüfen.")
        except Exception as exc:  # noqa: BLE001 – Fehler sichtbar in der UI statt abgebrochener Stream
            log.exception("Agent failed")
            yield _err(f"Unerwarteter Fehler: {exc}")

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def _err(text: str) -> str:
    return json.dumps({"type": "error", "text": text}, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- Preisalarme


@app.get("/api/watches")
def list_watches() -> list[dict]:
    s = get_settings()
    out = []
    for w in WatchStore(s.data_dir).load():
        origins = scheduler.watch_origins(s, w)
        out.append({**w.model_dump(), "searches_per_month": w.searches_per_month(len(origins)),
                    "origins_resolved": origins})
    return out


@app.post("/api/watches")
def save_watch(w: Watch) -> Watch:
    if w.kind in ("route", "award") and not w.destinations.strip():
        raise HTTPException(422, "Für diesen Alarm bitte Ziel-Flughäfen angeben.")
    if w.nights_max < w.nights_min:
        raise HTTPException(422, "Maximale Nächte muss ≥ minimale Nächte sein.")
    store = WatchStore(get_settings().data_dir)
    existing = {x.id: x for x in store.load()}
    if w.id in existing:  # Konfiguration ändern, Verlauf behalten
        state = existing[w.id].model_dump(include={"checks", "last_checked", "last_error", "best", "best_ever",
                                                   "history", "last_notified_price", "last_notified_at", "seen_deals"})
        w = Watch.model_validate({**w.model_dump(), **state})
    return store.upsert(w)


@app.delete("/api/watches/{watch_id}")
def delete_watch(watch_id: str) -> dict:
    if not WatchStore(get_settings().data_dir).delete(watch_id):
        raise HTTPException(404, "Alarm nicht gefunden")
    return {"ok": True}


@app.post("/api/watches/{watch_id}/check")
def check_now(watch_id: str) -> Watch:
    s = get_settings()
    store = WatchStore(s.data_dir)
    w = next((x for x in store.load() if x.id == watch_id), None)
    if w is None:
        raise HTTPException(404, "Alarm nicht gefunden")
    return scheduler.run_watch(s, w, store)


@app.post("/api/notify/test")
def notify_test() -> dict:
    err = notify.send(get_settings(), title="TRAVLR Test ✈", message="Push kommt an. Gute Reise!", tags=["airplane"])
    if err:
        raise HTTPException(400, err)
    return {"ok": True}


# --------------------------------------------------------------------------- Community


@app.get("/api/community")
def get_community() -> dict:
    store = community.CommunityStore(get_settings().data_dir)
    return {"feeds": [f.model_dump() for f in store.feeds()],
            "digests": [d.model_dump() for d in reversed(store.digests())][:6],
            "tips": [t.model_dump() for t in store.tips()],
            "last_run": (lr.isoformat(timespec="minutes") if (lr := store.last_run()) else None)}


@app.put("/api/community/feeds")
def put_feeds(feeds: list[community.Feed]) -> dict:
    community.CommunityStore(get_settings().data_dir).save_feeds(feeds)
    return {"ok": True}


@app.post("/api/community/run")
def run_digest_now() -> community.Digest:
    return scheduler.run_digest(get_settings())


@app.post("/api/community/tips")
def add_tip(tip: community.Tip) -> community.Tip:
    store = community.CommunityStore(get_settings().data_dir)
    store.save_tips([t for t in store.tips() if t.title != tip.title] + [tip])
    return tip


@app.delete("/api/community/tips/{tip_id}")
def delete_tip(tip_id: str) -> dict:
    store = community.CommunityStore(get_settings().data_dir)
    store.save_tips([t for t in store.tips() if t.id != tip_id])
    return {"ok": True}
