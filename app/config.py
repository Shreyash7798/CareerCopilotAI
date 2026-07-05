"""Configuration loading.

All personalization lives in YAML files under config/ (settings.yaml and
sources.yaml). The example files are used as fallbacks so a fresh clone runs
out of the box. Nothing user-specific is hardcoded in the application.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
SETTINGS_EXAMPLE_FILE = CONFIG_DIR / "settings.example.yaml"
SOURCES_FILE = CONFIG_DIR / "sources.yaml"
SOURCES_EXAMPLE_FILE = CONFIG_DIR / "sources.example.yaml"

_lock = threading.Lock()
_settings_cache: dict[str, Any] | None = None
_sources_cache: dict[str, Any] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


def _settings_path() -> Path:
    return SETTINGS_FILE if SETTINGS_FILE.exists() else SETTINGS_EXAMPLE_FILE


def _sources_path() -> Path:
    return SOURCES_FILE if SOURCES_FILE.exists() else SOURCES_EXAMPLE_FILE


def get_settings(refresh: bool = False) -> dict[str, Any]:
    global _settings_cache
    with _lock:
        if _settings_cache is None or refresh:
            _settings_cache = _load_yaml(_settings_path())
        return copy.deepcopy(_settings_cache)


def get_sources_config(refresh: bool = False) -> dict[str, Any]:
    global _sources_cache
    with _lock:
        if _sources_cache is None or refresh:
            _sources_cache = _load_yaml(_sources_path())
        return copy.deepcopy(_sources_cache)


def save_settings(settings: dict[str, Any]) -> None:
    """Persist settings to config/settings.yaml and refresh the cache."""
    global _settings_cache
    with _lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_FILE.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(settings, fh, sort_keys=False, allow_unicode=True)
        _settings_cache = copy.deepcopy(settings)


def save_sources_config(sources: dict[str, Any]) -> None:
    global _sources_cache
    with _lock:
        SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SOURCES_FILE.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(sources, fh, sort_keys=False, allow_unicode=True)
        _sources_cache = copy.deepcopy(sources)


def get_profile() -> dict[str, Any]:
    return get_settings().get("profile", {}) or {}


def save_profile(profile: dict[str, Any]) -> None:
    settings = get_settings(refresh=True)
    settings["profile"] = profile
    save_settings(settings)


def data_dir() -> Path:
    settings = get_settings()
    raw = settings.get("app", {}).get("data_dir", "data")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    settings = get_settings()
    url = settings.get("app", {}).get("database_url", "sqlite:///data/careercopilot.db")
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url[len("sqlite:///"):]
        p = Path(rel)
        if not p.is_absolute():
            p = PROJECT_ROOT / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{p}"
    return url
