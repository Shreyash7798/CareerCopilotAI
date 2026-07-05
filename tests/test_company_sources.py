"""Company Management: DB-backed source configuration."""

from datetime import datetime, timedelta

from app.company_sources import (
    entry_from_company,
    is_due,
    keywords_list,
    seed_from_yaml_config,
)
from app.models import Company


def _company(**kwargs) -> Company:
    defaults = dict(name="Test Co", enabled=True)
    defaults.update(kwargs)
    return Company(**defaults)


def test_entry_greenhouse():
    c = _company(ats_type="greenhouse", ats_config='{"board": "testco"}')
    entry = entry_from_company(c)
    assert entry == {"type": "greenhouse", "company": "Test Co", "enabled": True, "board": "testco"}


def test_entry_workday():
    c = _company(
        ats_type="workday",
        ats_config='{"host": "x.wd1.myworkdayjobs.com", "tenant": "x", "site": "S"}',
    )
    entry = entry_from_company(c)
    assert entry["type"] == "workday"
    assert entry["host"] == "x.wd1.myworkdayjobs.com"


def test_entry_sap_maps_to_careers_page_with_defaults():
    c = _company(ats_type="sap", career_url="https://careers.example.com/search/?q=x")
    entry = entry_from_company(c)
    assert entry["type"] == "careers_page"
    assert entry["link_selector"] == "a.jobTitle-link"
    assert entry["url"] == "https://careers.example.com/search/?q=x"


def test_entry_oracle_and_taleo_render():
    for ats in ("oracle", "taleo"):
        entry = entry_from_company(_company(ats_type=ats, career_url="https://x.example/jobs"))
        assert entry["type"] == "careers_page"
        assert entry["render"] is True


def test_entry_none_without_ats_type():
    assert entry_from_company(_company()) is None


def test_keywords_list():
    c = _company(keywords="Process Excellence, lean , Six Sigma")
    assert keywords_list(c) == ["process excellence", "lean", "six sigma"]
    assert keywords_list(_company()) == []


def test_is_due_respects_interval():
    now = datetime(2026, 7, 5, 12, 0)
    c = _company(refresh_interval_minutes=60, last_run_at=now - timedelta(minutes=30))
    assert not is_due(c, now)
    c.last_run_at = now - timedelta(minutes=90)
    assert is_due(c, now)
    assert is_due(_company(), now)  # no interval -> always due


def test_seed_from_yaml(tmp_path, monkeypatch):
    import app.db as db_mod

    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/seed.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    cfg = {
        "sources": [
            {"type": "greenhouse", "company": "Alpha", "board": "alpha", "enabled": True},
            {
                "type": "workday",
                "company": "Beta",
                "host": "b.wd1.myworkdayjobs.com",
                "tenant": "b",
                "site": "S",
                "enabled": False,
            },
            {"type": "unknown_type", "company": "Skipped"},
        ]
    }
    with db_mod.session_scope() as session:
        imported = seed_from_yaml_config(session, cfg)
        assert imported == 2

    from sqlalchemy import select

    with db_mod.session_scope() as session:
        companies = {c.name: c for c in session.execute(select(Company)).scalars()}
        assert companies["Alpha"].ats_type == "greenhouse"
        assert companies["Alpha"].enabled is True
        assert companies["Beta"].enabled is False
        assert "Skipped" not in companies
        # Second seed run is a no-op for already-configured companies.
        imported = seed_from_yaml_config(session, cfg)
        assert imported == 0


def test_auto_migration_adds_columns(tmp_path, monkeypatch):
    """Simulate an old database missing the new company columns."""
    import sqlite3

    import app.db as db_mod

    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        """CREATE TABLE companies (
            id INTEGER PRIMARY KEY, name VARCHAR(255), website VARCHAR(512),
            industry VARCHAR(255), locations VARCHAR(512), notes TEXT,
            is_preferred BOOLEAN, created_at DATETIME)"""
    )
    conn.execute("INSERT INTO companies (name) VALUES ('Legacy Co')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{db_file}")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    from sqlalchemy import select

    with db_mod.session_scope() as session:
        company = session.execute(select(Company)).scalars().one()
        assert company.name == "Legacy Co"
        assert company.ats_type is None
        company.ats_type = "greenhouse"
        company.enabled = True
    with db_mod.session_scope() as session:
        company = session.execute(select(Company)).scalars().one()
        assert company.ats_type == "greenhouse"
