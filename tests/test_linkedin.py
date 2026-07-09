"""Tests for LinkedIn guest job source."""

from pathlib import Path

import pytest

from app.sources import linkedin as linkedin_mod

SEARCH_FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_search.html"
JOB_FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_job_page.html"


@pytest.fixture()
def search_html():
    return SEARCH_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture()
def job_html():
    return JOB_FIXTURE.read_text(encoding="utf-8")


def test_job_id_from_url():
    assert linkedin_mod.job_id_from_url(
        "https://www.linkedin.com/jobs/view/strategy-consultant-at-ey-4434425496"
    ) == "4434425496"
    assert linkedin_mod.job_id_from_url(
        "https://www.linkedin.com/jobs/search/?currentJobId=4434425496&keywords=consulting"
    ) == "4434425496"


def test_parse_job_posting_fragment():
    fragment = (
        Path(__file__).parent / "fixtures" / "linkedin_job_posting_fragment.html"
    ).read_text(encoding="utf-8")
    title, company, location, description, posted = linkedin_mod.parse_job_posting_fragment(
        fragment
    )
    assert title == "Operations Consultant"
    assert company == "EY India"
    assert "Mumbai" in location
    assert "supply chain" in description.lower()
    assert posted is None


def test_parse_search_card(search_html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(search_html, "lxml")
    card = soup.select_one("div.base-search-card")
    raw = linkedin_mod.parse_search_card(card)
    assert raw is not None
    assert raw.title == "Operations Consultant"
    assert raw.company == "EY India"
    assert raw.location == "Mumbai, Maharashtra, India"
    assert raw.external_id == "4434425496"
    assert raw.source == "linkedin"


def test_fetch_job_page_parses_json_ld(job_html):
    class FakeResp:
        status_code = 200
        text = job_html

    class FakeClient:
        def get(self, url, params=None):
            return FakeResp()

    description, posted = linkedin_mod.fetch_job_page(FakeClient(), "https://example.com/job")
    assert "supply chain" in description.lower()
    assert posted is not None


def test_fetch_search_mocked(search_html, job_html, monkeypatch):
    posting_fragment = (
        Path(__file__).parent / "fixtures" / "linkedin_job_posting_fragment.html"
    ).read_text(encoding="utf-8")

    class FakeResp:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            if "seeMoreJobPostings" in url:
                return FakeResp(search_html)
            if "/jobPosting/" in url:
                return FakeResp(posting_fragment)
            return FakeResp(job_html)

    monkeypatch.setattr(linkedin_mod, "linkedin_client", lambda timeout=30.0: FakeClient())
    monkeypatch.setattr(linkedin_mod.time, "sleep", lambda *_: None)

    jobs = linkedin_mod.fetch(
        {
            "keywords": "consulting",
            "location": "Mumbai, Maharashtra, India",
            "max_pages": 1,
            "detail_limit": 1,
        }
    )
    assert len(jobs) == 1
    assert jobs[0].company == "EY India"
    assert "supply chain" in jobs[0].description.lower()
