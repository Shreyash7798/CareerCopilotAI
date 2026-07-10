"""One-click company bundles (per-user monitors only)."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select

from app.config import PROJECT_ROOT
from app.models import Company
from app.user_access import ensure_monitor

PRESETS_FILE = PROJECT_ROOT / "config" / "company_presets.yaml"


def load_presets() -> list[dict]:
    if not PRESETS_FILE.exists():
        return []
    data = yaml.safe_load(PRESETS_FILE.read_text(encoding="utf-8")) or {}
    return list(data.get("presets") or [])


def apply_preset(session, user_id: int, preset_id: str) -> int:
    """Enable monitors for all companies in a preset. Returns count enabled."""
    preset = next((p for p in load_presets() if p.get("id") == preset_id), None)
    if preset is None:
        raise ValueError(f"Unknown preset: {preset_id}")
    enabled = 0
    for name in preset.get("companies") or []:
        company = session.execute(select(Company).where(Company.name == name)).scalar_one_or_none()
        if company is None or not company.ats_type:
            continue
        ensure_monitor(session, user_id, company, enabled=True)
        enabled += 1
    return enabled
