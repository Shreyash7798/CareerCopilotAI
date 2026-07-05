"""Lever public postings API source.

Uses the documented, unauthenticated JSON API:
https://api.lever.co/v0/postings/<company>?mode=json
"""

from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.sources.base import RawJob, SourceError, http_client

API = "https://api.lever.co/v0/postings/{company}?mode=json"


def _strip_html(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text(separator="\n", strip=True)


def fetch(entry: dict) -> list[RawJob]:
    token = entry.get("board") or entry.get("company", "").lower()
    company = entry.get("company") or token
    url = API.format(company=token)
    with http_client() as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise SourceError(f"Lever board '{token}' returned HTTP {resp.status_code}")
    jobs = []
    for item in resp.json():
        categories = item.get("categories") or {}
        posted = None
        if item.get("createdAt"):
            posted = datetime.fromtimestamp(item["createdAt"] / 1000, tz=timezone.utc).replace(tzinfo=None)
        jobs.append(
            RawJob(
                company=company,
                title=item.get("text", ""),
                location=categories.get("location", "") or ", ".join(item.get("workplaceType", "").split()),
                description=_strip_html(item.get("description", "")),
                url=item.get("hostedUrl", ""),
                source="lever",
                external_id=str(item.get("id", "")),
                posted_at=posted,
            )
        )
    return jobs
