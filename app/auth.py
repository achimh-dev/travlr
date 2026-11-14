"""Zweistufiger Schutz: Cloudflare Access davor (optional geprüft) + eigenes Login der App.

- Login: Benutzername/Passwort (APP_USERNAME/APP_PASSWORD), danach ein signiertes Sitzungs-Cookie
  für 30 Tage. Funktioniert auch als Homescreen-App auf dem Handy.
- Cloudflare Access: Wenn CF_ACCESS_TEAM_DOMAIN und CF_ACCESS_AUD gesetzt sind, muss jede Anfrage ein
  gültiges Access-JWT tragen. Damit kommt niemand an Cloudflare vorbei direkt an den Container.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

import jwt

from .config import Settings

COOKIE = "travlr_session"
SESSION_DAYS = 30
PUBLIC_PATHS = {"/login", "/healthz", "/api/healthz", "/manifest.webmanifest", "/static/style.css", "/static/icon.svg",
                "/static/icon-192.png", "/static/icon-512.png", "/static/apple-touch-icon.png",
                "/static/icon-maskable-192.png", "/static/icon-maskable-512.png", "/static/favicon-32.png"}

_failures: dict[str, list[float]] = {}
_flock = threading.Lock()
_jwks: dict[str, jwt.PyJWKClient] = {}


def session_secret(s: Settings) -> bytes:
    """SESSION_SECRET aus der Umgebung oder einmalig erzeugt und im Datenverzeichnis gespeichert."""
    if s.session_secret:
        return s.session_secret.encode()
    path: Path = s.data_dir / ".session_secret"
    if path.is_file():
        return path.read_bytes().strip()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48).encode()
    path.write_bytes(value)
    path.chmod(0o600)
    return value


def _sign(secret: bytes, payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret, payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_session(s: Settings, user: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode()
    return f"{payload}.{_sign(session_secret(s), payload.encode())}"


def check_session(s: Settings, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(session_secret(s), payload.encode())):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return False
    return data.get("u") == s.app_username and data.get("exp", 0) > time.time()


def check_credentials(s: Settings, user: str, password: str, client_ip: str) -> tuple[bool, str | None]:
    """Prüft Login-Daten mit einfacher Bremse gegen Durchprobieren (5 Fehlversuche / 15 min pro IP)."""
    now = time.time()
    with _flock:
        recent = [t for t in _failures.get(client_ip, []) if now - t < 900]
        _failures[client_ip] = recent
        if len(recent) >= 5:
            return False, "Zu viele Fehlversuche. Bitte 15 Minuten warten."
    ok = hmac.compare_digest(user.strip().lower(), s.app_username.lower()) & hmac.compare_digest(password, s.app_password)
    if not ok:
        with _flock:
            _failures.setdefault(client_ip, []).append(now)
        return False, "Benutzername oder Passwort falsch."
    with _flock:
        _failures.pop(client_ip, None)
    return True, None


def cf_access_enabled(s: Settings) -> bool:
    return bool(s.cf_access_team_domain and s.cf_access_aud)


def check_cf_access(s: Settings, token: str | None) -> bool:
    if not token:
        return False
    team = s.cf_access_team_domain if s.cf_access_team_domain.startswith("http") else f"https://{s.cf_access_team_domain}"
    client = _jwks.setdefault(team, jwt.PyJWKClient(f"{team}/cdn-cgi/access/certs", cache_keys=True))
    try:
        key = client.get_signing_key_from_jwt(token)
        jwt.decode(token, key.key, algorithms=["RS256"], audience=s.cf_access_aud, issuer=team)
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        return False
    return True
