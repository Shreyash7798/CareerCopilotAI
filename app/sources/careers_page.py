"""Generic public careers page source.

Fetches a page over plain HTTP and extracts job links with a CSS selector.
If the page is JavaScript-rendered, set `render: true` in sources.yaml and the
page is rendered with Playwright (Chromium, headless) first.

This deliberately stays read-only and public: no logins, no form submission.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.sources.base import RawJob, SourceError, http_client


def _fetch_html_http(url: str) -> str:
    with http_client() as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise SourceError(f"Careers page '{url}' returned HTTP {resp.status_code}")
    return resp.text


def _fetch_html_playwright(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SourceError("Playwright is not installed; run `playwright install chromium`") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            return page.content()
        finally:
            browser.close()


def fetch(entry: dict) -> list[RawJob]:
    url = entry["url"]
    company = entry.get("company", url)
    selector = entry.get("link_selector", "a")
    html = _fetch_html_playwright(url) if entry.get("render") else _fetch_html_http(url)

    soup = BeautifulSoup(html, "lxml")
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for anchor in soup.select(selector):
        title = anchor.get_text(strip=True)
        href = anchor.get("href", "")
        if not title or not href:
            continue
        absolute = urljoin(url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        jobs.append(
            RawJob(
                company=company,
                title=title,
                location=entry.get("default_location", ""),
                url=absolute,
                source="careers_page",
                external_id=absolute,
            )
        )
    return jobs
