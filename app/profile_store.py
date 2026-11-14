"""Profil (Heimatflughafen, Meilenkonten, Karte) als JSON-Datei im Datenverzeichnis."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Profile


def _path(data_dir: Path) -> Path:
    return data_dir / "profile.json"


def load(data_dir: Path, default_home: str = "FRA") -> Profile:
    path = _path(data_dir)
    if path.is_file():
        try:
            return Profile.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            pass  # kaputte Datei → Standardprofil
    return Profile(home_airports=default_home)


def save(data_dir: Path, profile: Profile) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _path(data_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path(data_dir))
