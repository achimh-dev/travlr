"""Einfacher JSON-Dateicache für API-Antworten (spart Kontingent und Zeit)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, directory: Path, ttl_hours: float, prefix: str):
        self.directory = directory
        self.ttl = ttl_hours * 3600
        self.prefix = prefix

    def _path(self, key: Any) -> Path:
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
        return self.directory / f"{self.prefix}-{digest}.json"

    def get(self, key: Any) -> Any | None:
        path = self._path(key)
        if self.ttl > 0 and path.is_file() and time.time() - path.stat().st_mtime < self.ttl:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def set(self, key: Any, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps(value), encoding="utf-8")
