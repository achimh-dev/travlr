"""Push-Benachrichtigungen über ntfy (https://ntfy.sh oder selbst gehostet, z. B. auf der Synology)."""

from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger("travlr.notify")


def ntfy_configured(s: Settings) -> bool:
    return bool(s.ntfy_topic)


def send(s: Settings, title: str, message: str, click: str | None = None, tags: list[str] | None = None,
         priority: int = 3) -> str | None:
    """Sendet eine Nachricht. Gibt bei Fehler einen Text zurück, sonst None."""
    if not ntfy_configured(s):
        return "ntfy ist nicht konfiguriert (NTFY_TOPIC)."
    body: dict = {"topic": s.ntfy_topic, "title": title, "message": message, "priority": priority}
    if click:
        body["click"] = click
    if tags:
        body["tags"] = tags
    headers = {"Authorization": f"Bearer {s.ntfy_token}"} if s.ntfy_token else {}
    try:
        # JSON-Publish an die Basis-URL unterstützt UTF-8 in Titel und Text
        resp = httpx.post(s.ntfy_url.rstrip("/"), json=body, headers=headers, timeout=15)
        if resp.status_code >= 400:
            return f"ntfy-Fehler {resp.status_code}: {resp.text[:200]}"
    except httpx.HTTPError as exc:
        log.warning("ntfy failed: %s", exc)
        return f"ntfy nicht erreichbar: {exc}"
    return None
