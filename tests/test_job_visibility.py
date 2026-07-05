"""Tests for job visibility and posting age helpers."""

from datetime import datetime, timedelta

import pytest

from app.job_visibility import (
    is_aged_out,
    job_age_days,
    job_age_label,
    job_status_badge,
    visible_jobs_filter,
)
from app.models import Job


def _job(**kwargs) -> Job:
    defaults = {
        "title": "Consultant",
        "dedup_key": "abc",
        "is_active": True,
        "discovered_at": datetime(2026, 7, 1),
    }
    defaults.update(kwargs)
    return Job(**defaults)


def test_job_age_label_posted():
    now = datetime(2026, 7, 5)
    job = _job(posted_at=datetime(2026, 7, 4))
    assert job_age_label(job, now=now) == "Posted yesterday"
    assert job_age_days(job, now=now) == 1


def test_job_age_label_discovered_fallback():
    now = datetime(2026, 7, 20)
    job = _job(discovered_at=datetime(2026, 7, 1))
    assert job_age_label(job, now=now) == "Found 2w ago"


def test_is_aged_out_by_posted_date():
    cfg = {"hide_stale_untracked": True, "max_posted_age_days": 30, "max_discovered_age_days": 90}
    now = datetime(2026, 7, 5)
    fresh = _job(posted_at=datetime(2026, 6, 20))
    stale = _job(posted_at=datetime(2026, 5, 1))
    assert is_aged_out(fresh, cfg, now=now) is False
    assert is_aged_out(stale, cfg, now=now) is True


def test_is_aged_out_discovered_when_no_posted():
    cfg = {"hide_stale_untracked": True, "max_posted_age_days": 30, "max_discovered_age_days": 14}
    now = datetime(2026, 7, 5)
    job = _job(discovered_at=datetime(2026, 6, 1))
    assert is_aged_out(job, cfg, now=now) is True


def test_status_badge_closed():
    job = _job(is_active=False)
    assert job_status_badge(job) == "No longer listed"
    assert job_status_badge(job, is_tracked=True) == "Posting closed"


def test_visible_jobs_filter_builds(monkeypatch):
    monkeypatch.setattr(
        "app.job_visibility.get_settings",
        lambda: {
            "job_visibility": {
                "hide_stale_untracked": True,
                "max_posted_age_days": 60,
                "max_discovered_age_days": 90,
            }
        },
    )
    clause = visible_jobs_filter(now=datetime(2026, 7, 5))
    assert clause is not None
