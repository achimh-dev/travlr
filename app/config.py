"""Konfiguration aus Umgebungsvariablen (optional aus einer .env-Datei)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Loader, damit keine Extra-Abhängigkeit nötig ist."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    serpapi_api_key: str
    duffel_api_key: str
    seats_aero_api_key: str
    app_username: str
    app_password: str
    session_secret: str
    cf_access_team_domain: str
    cf_access_aud: str
    ntfy_url: str
    ntfy_topic: str
    ntfy_token: str
    scheduler_enabled: bool
    data_dir: Path
    home_airports: str
    currency: str
    max_searches: int
    cache_ttl_hours: float
    cache_dir: Path
    claude_model: str
    claude_effort: str

    @property
    def demo_mode(self) -> bool:
        """Ohne echte Flugquelle läuft die App mit Beispieldaten."""
        return not (self.serpapi_api_key or self.duffel_api_key)


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
    return Settings(
        serpapi_api_key=os.environ.get("SERPAPI_API_KEY", "").strip(),
        duffel_api_key=os.environ.get("DUFFEL_API_KEY", "").strip(),
        seats_aero_api_key=os.environ.get("SEATS_AERO_API_KEY", "").strip(),
        app_username=os.environ.get("APP_USERNAME", "admin").strip(),
        app_password=os.environ.get("APP_PASSWORD", "").strip(),
        session_secret=os.environ.get("SESSION_SECRET", "").strip(),
        cf_access_team_domain=os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/"),
        cf_access_aud=os.environ.get("CF_ACCESS_AUD", "").strip(),
        ntfy_url=os.environ.get("NTFY_URL", "https://ntfy.sh").strip(),
        ntfy_topic=os.environ.get("NTFY_TOPIC", "").strip(),
        ntfy_token=os.environ.get("NTFY_TOKEN", "").strip(),
        scheduler_enabled=os.environ.get("SCHEDULER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "ja"),
        data_dir=data_dir,
        home_airports=os.environ.get("HOME_AIRPORTS", "FRA").strip().upper(),
        currency=os.environ.get("CURRENCY", "EUR").strip().upper(),
        max_searches=int(os.environ.get("MAX_SEARCHES", "10")),
        cache_ttl_hours=float(os.environ.get("CACHE_TTL_HOURS", "6")),
        cache_dir=Path(os.environ.get("CACHE_DIR", str(data_dir / "cache"))),
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5").strip(),
        claude_effort=os.environ.get("CLAUDE_EFFORT", "medium").strip(),
    )
