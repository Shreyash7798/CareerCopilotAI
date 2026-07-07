"""Shared HTML → RawJob parsing for careers_page and crawl4ai connectors."""

from __future__ import annotations

from collections.abc import Callable
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.sources.base import RawJob

_DEFAULT_DETAIL_SELECTORS = (
    "div.job-description",
    "div[class*='jobDescription']",
    "div[class*='description']",
    "section[class*='description']",
    "article",
    "main",
)


def extract_description(html: str, detail_selector: str | None) -> str:
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


def parse_job_listing(
    html: str,
    entry: dict,
    *,
    source: str,
    fetch_detail: Callable[[str], str] | None = None,
) -> list[RawJob]:
    """Parse a careers listing page into RawJob rows."""
    url = entry["url"]
    company = entry.get("company", url)
    selector = entry.get("link_selector", "a")
    detail_limit = int(entry.get("detail_limit", 15))

    soup = BeautifulSoup(html, "lxml")
    jobs: list[RawJob] = []
    seen: set[str] = set()

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
        if fetch_detail and len(jobs) < detail_limit:
            description = fetch_detail(absolute)
        jobs.append(
            RawJob(
                company=company,
                title=title,
                location=entry.get("default_location", ""),
                description=description,
                url=absolute,
                source=source,
                external_id=absolute,
            )
        )
    return jobs
