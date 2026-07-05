"""Jobs page filter query handling."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_jobs_filter_empty_min_score_returns_200():
  client = TestClient(app)
  r = client.get("/jobs", params={"q": "engineer", "min_score": ""})
  assert r.status_code == 200
  assert "engineer" in r.text or "matching" in r.text.lower()


def test_jobs_filter_high_priority():
  client = TestClient(app)
  for param in ("true", "1", "yes"):
    r = client.get("/jobs", params={"high_priority": param})
    assert r.status_code == 200


def test_jobs_query_string_filter():
  from app.routers.pages import _jobs_query_string

  qs = _jobs_query_string(
    {
      "q": "civil",
      "location": "Mumbai",
      "company": "Larsen & Toubro",
      "min_score": 50,
      "high_priority": True,
      "page": 2,
    }
  )
  assert "q=civil" in qs
  assert "location=Mumbai" in qs
  assert "company=Larsen" in qs
  assert "min_score=50" in qs
  assert "high_priority=true" in qs
  assert "page=2" in qs


def test_jobs_company_filter_special_chars():
  client = TestClient(app)
  r = client.get("/jobs", params={"company": "Larsen & Toubro (L&T)"})
  assert r.status_code == 200
