"""Hintergrund-Jobs: fällige Preisalarme prüfen und den wöchentlichen Community-Digest erstellen.

Läuft als Thread im App-Prozess (ein Container, ein Worker). Abschaltbar mit SCHEDULER_ENABLED=false.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta

from . import community, notify, profile_store
from .config import Settings, get_settings
from .geo import ground_calculator, home_airports
from .history import PriceHistory
from .loyalty import SEATS_AERO_SOURCE_TO_PROGRAM, loyalty_brief, program_name, reachable_miles
from .watches import ExploreClient, Watch, WatchStore, check_watch

log = logging.getLogger("travlr.scheduler")
TICK_SECONDS = 300
DIGEST_EVERY = timedelta(days=7)

_stop = threading.Event()
_run_lock = threading.Lock()  # verhindert parallele Läufe (Tick + "Jetzt prüfen")


def watch_origins(settings: Settings, w: Watch) -> list[str]:
    if w.origins.strip():
        return [c.strip().upper() for c in w.origins.split(",") if c.strip()]
    prof = profile_store.load(settings.data_dir, settings.home_airports)
    near = home_airports(prof)
    if near:
        return [a.code for a in near]
    return [c.strip().upper() for c in prof.home_airports.split(",") if c.strip()] or ["FRA"]


def run_watch(settings: Settings, w: Watch, store: WatchStore | None = None, today: date | None = None) -> Watch:
    from .main import make_providers  # spätes Import, vermeidet Zyklus

    store = store or WatchStore(settings.data_dir)
    providers, award = make_providers(settings)
    explore = ExploreClient(settings.serpapi_api_key, settings.cache_dir, settings.cache_ttl_hours) \
        if settings.serpapi_api_key else None
    with _run_lock:
        prof = profile_store.load(settings.data_dir, settings.home_airports)
        updated, messages = check_watch(w, providers[0] if providers else None, explore, watch_origins(settings, w), today or date.today(),
                                        settings.currency, ground=ground_calculator(prof), award_provider=award,
                                        price_history=PriceHistory(settings.data_dir),
                                        award_hint=lambda src: award_hint(prof, src))
        for m in messages if notify.ntfy_configured(settings) else []:
            err = notify.send(settings, **m)
            if err:
                updated.last_error = ((updated.last_error + "; ") if updated.last_error else "") + err
        store.update(updated)
    return updated


def award_hint(prof, source: str) -> str:
    """Kurzer Hinweis für den Push: Reichen die eigenen Meilen (inkl. Amex-Transfer)?"""
    prog = SEATS_AERO_SOURCE_TO_PROGRAM.get(source)
    if not prog:
        return ""
    return f"Mit {program_name(prog)} erreichbar: {reachable_miles(prof, prog):,} (inkl. Amex-Transfer).".replace(",", ".")


def digest_context(settings: Settings) -> str:
    prof = profile_store.load(settings.data_dir, settings.home_airports)
    watches = WatchStore(settings.data_dir).load()
    near = home_airports(prof)
    parts = [
        f"Wohnort: {prof.home_location or '–'}; Flughäfen in Fahrweite: {', '.join(a.code for a in near) or prof.home_airports}",
        "Alarme: " + ("; ".join(f"{w.name} ({w.destinations or 'überall'}, bis {w.max_price:.0f} "
                                 f"{'Meilen' if w.kind == 'award' else '€'})" for w in watches) or "keine"),
        "Meilen:\n" + loyalty_brief(prof),
    ]
    return "\n".join(parts)


def run_digest(settings: Settings) -> community.Digest:
    store = community.CommunityStore(settings.data_dir)
    with _run_lock:
        d = community.run_digest(settings, store, digest_context(settings))
    if d.content and (d.content.deals or d.content.tips) and notify.ntfy_configured(settings):
        notify.send(settings, title=f"🧭 TRAVLR Wochen-Digest: {len(d.content.deals)} Deals, {len(d.content.tips)} Tipps",
                    message=d.content.summary[:500], tags=["newspaper"])
    return d


def tick(settings: Settings) -> None:
    now = datetime.now()
    store = WatchStore(settings.data_dir)
    for w in store.load():
        if w.due(now):
            try:
                run_watch(settings, w, store)
            except Exception:  # noqa: BLE001 – ein kaputter Alarm darf den Rest nicht stoppen
                log.exception("watch %s failed", w.id)
    last = community.CommunityStore(settings.data_dir).last_run()
    if last is None or now - last >= DIGEST_EVERY:
        try:
            run_digest(settings)
        except Exception:  # noqa: BLE001
            log.exception("digest failed")


def _loop() -> None:
    # kurz warten, damit der Server erst hochfährt
    if _stop.wait(30):
        return
    while not _stop.is_set():
        try:
            tick(get_settings())
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        _stop.wait(TICK_SECONDS)


def start() -> threading.Thread | None:
    if not get_settings().scheduler_enabled:
        return None
    _stop.clear()
    t = threading.Thread(target=_loop, name="travlr-scheduler", daemon=True)
    t.start()
    return t


def stop() -> None:
    _stop.set()
