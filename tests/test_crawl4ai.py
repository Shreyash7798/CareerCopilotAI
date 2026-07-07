"""Tests for optional Crawl4AI integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.sources import crawl4ai, crawl4ai_client, careers_page


def test_crawl4ai_disabled_by_default(monkeypatch):
    monkeypatch.setattr("app.sources.crawl4ai_client.get_settings", lambda refresh=False: {})
    assert crawl4ai_client.is_enabled() is False


def test_crawl4ai_fetch_requires_enabled():
    with patch.object(crawl4ai_client, "is_enabled", return_value=False):
        with pytest.raises(Exception, match="crawl4ai.enabled"):
            crawl4ai.fetch({"url": "https://example.com/jobs", "company": "Test"})


def test_crawl4ai_fetch_parses_jobs():
    list_html = """
    <html><body>
      <a href="/jobs/1">Site Engineer Mumbai</a>
      <a href="/jobs/2">Project Manager Pune</a>
    </body></html>
    """
    detail_html = "<html><body><div class='job-description'>Highways EPC experience</div></body></html>"

    def fake_fetch(url, **kwargs):
        if url.endswith("/jobs/1"):
            return detail_html
        return list_html

    with patch.object(crawl4ai_client, "is_enabled", return_value=True):
        with patch.object(crawl4ai_client, "fetch_html", side_effect=fake_fetch):
            jobs = crawl4ai.fetch(
                {
                    "url": "https://co.example/careers",
                    "company": "EPC Co",
                    "link_selector": "a",
                    "detail_limit": 1,
                }
            )

    assert len(jobs) == 2
    assert jobs[0].title == "Site Engineer Mumbai"
    assert jobs[0].source == "crawl4ai"
    assert jobs[0].description.startswith("Highways")


def test_careers_page_prefers_crawl4ai_when_enabled():
    html = '<html><body><a href="/j/1">Analyst</a></body></html>'

    with patch.object(crawl4ai_client, "is_enabled", return_value=True):
        with patch.object(crawl4ai_client, "crawl4ai_settings", return_value={"prefer_over_playwright": True}):
            with patch.object(careers_page, "_fetch_html_crawl4ai", return_value=html) as c4a:
                with patch.object(careers_page, "_fetch_html_playwright") as pw:
                    jobs = careers_page.fetch(
                        {
                            "url": "https://co.example/jobs",
                            "company": "Co",
                            "link_selector": "a",
                            "render": True,
                            "detail_limit": 0,
                        }
                    )
    c4a.assert_called_once()
    pw.assert_not_called()
    assert len(jobs) == 1
    assert jobs[0].title == "Analyst"


def test_crawl4ai_client_fetch_html_parses_response():
    settings = {
        "crawl4ai": {
            "enabled": True,
            "base_url": "http://127.0.0.1:11235",
            "timeout_seconds": 30,
        }
    }

    class FakeResp:
        status_code = 200

        def json(self):
            return {"results": [{"success": True, "html": "<html>ok</html>"}]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json):
            assert url.endswith("/crawl")
            assert json["urls"] == ["https://example.com"]
            return FakeResp()

    with patch("app.sources.crawl4ai_client.get_settings", return_value=settings):
        with patch("app.sources.crawl4ai_client.http_client", lambda timeout=30: FakeClient()):
            html = crawl4ai_client.fetch_html("https://example.com")
    assert "ok" in html


def test_entry_crawl4ai_maps_to_connector():
    from app.company_sources import entry_from_company
    from app.models import Company

    company = Company(
        name="Afcons",
        ats_type="crawl4ai",
        career_url="https://www.afcons.com/career-opportunities",
        enabled=True,
        ats_config='{"link_selector": "a[href*=\\"career\\"]"}',
    )
    entry = entry_from_company(company)
    assert entry["type"] == "crawl4ai"
    assert entry["url"] == "https://www.afcons.com/career-opportunities"
