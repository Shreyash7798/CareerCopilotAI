#!/usr/bin/env python3
"""Disable crawl4ai in settings.yaml (use Playwright-in-venv on 1 GB free VMs)."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "settings.yaml"
EXAMPLE = ROOT / "config" / "settings.example.yaml"


def main() -> int:
    path = SETTINGS if SETTINGS.exists() else EXAMPLE
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    crawl = data.setdefault("crawl4ai", {})
    if crawl.get("enabled") is False:
        print("[crawl4ai] already disabled in settings.yaml")
        return 0

    crawl["enabled"] = False
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    print("[crawl4ai] disabled in config/settings.yaml (free-tier / Playwright mode)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
