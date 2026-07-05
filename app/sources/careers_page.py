"""Generic public careers page source.

Fetches a page over plain HTTP and extracts job links with a CSS selector.
If the page is JavaScript-rendered, set `render: true` in sources.yaml and the
page is rendered with Playwright (Chromium, headless) first.

For the first `detail_limit` postings (default 15), follows each job link and
extracts description text so SAP/Oracle list pages score on full JDs.
"""

from __future__ import annotations

from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.sources.base import RawJob, SourceError, http_client

_DEFAULT_DETAIL_SELECTORS = (
    "div.job-description",
    "div[class*='jobDescription']",
    "div[class*='description']",
    "section[class*='description']",
    "article",
    "main",
)


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
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            return page.content()
        finally:
            browser.close()


def _page_html(url: str, render: bool) -> str:
    return _fetch_html_playwright(url) if render else _fetch_html_http(url)


def _extract_description(html: str, detail_selector: str | None) -> str:
    soup = BeautifulSoup(html, "lxml")
    if detail_selector:
        node = soup.select_one(detail_selector)
        if node:
            return node.get_text(separator="\n", strip=True)
    for selector in _DEFAULT_DETAIL_SELECTORS:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(separator="\n", strip=True)
            if len(text) > 120:
                return text
    body = soup.body
    return body.get_text(separator="\n", strip=True)[:8000] if body else ""


def _fetch_description(
    job_url: str,
    *,
    render: bool,
    detail_selector: str | None,
    client=None,
) -> str:
    try:
        if render:
            html = _fetch_html_playwright(job_url)
        elif client is not None:
            resp = client.get(job_url)
            if resp.status_code != 200:
                return ""
            html = resp.text
        else:
            html = _fetch_html_http(job_url)
        return _extract_description(html, detail_selector)
    except Exception:  # noqa: BLE001 - detail fetch is best-effort
        return ""


def fetch(entry: dict) -> list[RawJob]:
    url = entry["url"]
    company = entry.get("company", url)
    selector = entry.get("link_selector", "a")
    render = bool(entry.get("render"))
    detail_limit = int(entry.get("detail_limit", 15))
    detail_selector = entry.get("detail_selector")

    html = _page_html(url, render)
    soup = BeautifulSoup(html, "lxml")
    jobs: list[RawJob] = []
    seen: set[str] = set()

    with http_client() as client:
        for anchor in soup.select(selector):
            title = unescape(anchor.get_text(strip=True))
            href = anchor.get("href", "")
            if not title or not href or len(title) < 4:
                continue
            absolute = urljoin(url, href)
            if absolute in seen:
                continue
            seen.add(absolute)
            description = ""
            if len(jobs) < detail_limit:
                description = _fetch_description(
                    absolute,
                    render=render,
                    detail_selector=detail_selector,
                    client=None if render else client,
                )
            jobs.append(
                RawJob(
                    company=company,
                    title=title,
                    location=entry.get("default_location", ""),
                    description=description,
                    url=absolute,
                    source="careers_page",
                    external_id=absolute,
                )
            )
    return jobs
