"""Greenhouse public board API source.

Uses the documented, unauthenticated JSON API:
https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true
"""

from __future__ import annotations

from datetime import datetime
from html import unescape

from bs4 import BeautifulSoup

from app.sources.base import RawJob, SourceError, http_client

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def _strip_html(html: str) -> str:
    return BeautifulSoup(unescape(html or ""), "lxml").get_text(separator="\n", strip=True)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def fetch(entry: dict) -> list[RawJob]:
    board = entry.get("board") or entry.get("company", "").lower()
    company = entry.get("company") or board
    url = API.format(board=board)
    with http_client() as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise SourceError(f"Greenhouse board '{board}' returned HTTP {resp.status_code}")
    jobs = []
    for item in resp.json().get("jobs", []):
        jobs.append(
            RawJob(
                company=company,
                title=item.get("title", ""),
                location=(item.get("location") or {}).get("name", ""),
                description=_strip_html(item.get("content", "")),
                url=item.get("absolute_url", ""),
                source="greenhouse",
                external_id=str(item.get("id", "")),
                posted_at=_parse_date(item.get("updated_at") or item.get("first_published")),
            )
        )
    return jobs
