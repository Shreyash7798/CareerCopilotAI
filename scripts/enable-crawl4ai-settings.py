#!/usr/bin/env python3
"""Enable crawl4ai in settings.yaml when the sidecar is healthy (idempotent)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "settings.yaml"
EXAMPLE = ROOT / "config" / "settings.example.yaml"


def _load_settings() -> dict:
    path = SETTINGS if SETTINGS.exists() else EXAMPLE
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _save_settings(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def main() -> int:
    data = _load_settings()
    crawl = data.setdefault("crawl4ai", {})
    base = str(crawl.get("base_url") or "http://127.0.0.1:11235").rstrip("/")

    try:
        resp = httpx.get(f"{base}/health", timeout=8)
        healthy = resp.status_code == 200
    except Exception:
        healthy = False

    if not healthy:
        print("[crawl4ai] sidecar not reachable — leaving settings unchanged")
        return 0

    if crawl.get("enabled"):
        print("[crawl4ai] already enabled in settings.yaml")
        return 0

    crawl["enabled"] = True
    crawl.setdefault("base_url", base)
    crawl.setdefault("prefer_over_playwright", True)
    crawl.setdefault("fallback_on_playwright_failure", True)
    _save_settings(data)
    print("[crawl4ai] enabled in config/settings.yaml (sidecar healthy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
