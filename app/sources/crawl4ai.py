"""Careers-page connector backed by a Crawl4AI Docker sidecar.

Uses the same ats_config fields as careers_page (url, link_selector, detail_limit,
detail_selector, default_location). Requires crawl4ai.enabled in settings.yaml and a
running Crawl4AI server — see docs/CRAWL4AI.md.
"""

from __future__ import annotations

from app.sources import crawl4ai_client
from app.sources.base import RawJob, SourceError
from app.sources.page_scrape import extract_description, parse_job_listing


def _fetch_detail(job_url: str, entry: dict) -> str:
    try:
        html = crawl4ai_client.fetch_html(job_url, delay_before_return_html=1.5)
        return extract_description(html, entry.get("detail_selector"))
    except Exception:  # noqa: BLE001 - detail fetch is best-effort
        return ""


def fetch(entry: dict) -> list[RawJob]:
    if not crawl4ai_client.is_enabled():
        raise SourceError(
            "Crawl4AI connector requires crawl4ai.enabled: true in config/settings.yaml"
        )
    if not entry.get("url"):
        raise SourceError("Career URL is required for crawl4ai")

    html = crawl4ai_client.fetch_html(entry["url"])
    detail_limit = int(entry.get("detail_limit", 15))
    fetched = 0

    def detail_fetcher(job_url: str) -> str:
        nonlocal fetched
        if fetched >= detail_limit:
            return ""
        fetched += 1
        return _fetch_detail(job_url, entry)

    return parse_job_listing(
        html,
        entry,
        source="crawl4ai",
        fetch_detail=detail_fetcher,
    )
