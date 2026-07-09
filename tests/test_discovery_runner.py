"""Discovery subprocess isolation."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app import discovery_runner as dr


def test_acquire_and_clear_lock(tmp_path, monkeypatch):
    lock = tmp_path / "data" / ".discovery.lock"
    monkeypatch.setattr(dr, "LOCK_FILE", lock)

    assert dr.acquire_discovery_lock() is True
    assert lock.read_text().strip() == str(os.getpid())
    assert dr.is_discovery_running() is True

    dr.clear_discovery_lock()
    assert dr.is_discovery_running() is False


def test_run_discovery_subprocess_skips_when_running(tmp_path, monkeypatch):
    lock = tmp_path / "data" / ".discovery.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(dr, "LOCK_FILE", lock)

    outcome = dr.run_discovery_subprocess()
    assert outcome["started"] is False
    assert outcome["reason"] == "already_running"


def test_run_discovery_subprocess_starts_process(monkeypatch):
    monkeypatch.setattr(dr, "is_discovery_running", lambda: False)

    proc = MagicMock()
    proc.pid = 4242
    with patch.object(dr.subprocess, "Popen", return_value=proc) as popen:
        outcome = dr.run_discovery_subprocess()

    assert outcome["started"] is True
    assert outcome["pid"] == 4242
    popen.assert_called_once()
