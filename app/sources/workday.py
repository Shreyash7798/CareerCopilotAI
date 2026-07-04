"""Workday career site source.

Workday career sites expose a public JSON API (the same one the site's own
frontend calls):

POST https://<host>/wday/cxs/<tenant>/<site>/jobs
     {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "..."}
"""

from __future__ import annotations

from datetime import datetime

from app.sources.base import RawJob, SourceError, http_client

MAX_PAGES = 10
PAGE_SIZE = 20


def _parse_posted(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def fetch(entry: dict) -> list[RawJob]:
    host = entry["host"].rstrip("/")
    tenant = entry["tenant"]
    site = entry["site"]
    company = entry.get("company") or tenant
    search_text = entry.get("search_text", "")
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs: list[RawJob] = []
    with http_client() as client:
        for page in range(MAX_PAGES):
            payload = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": page * PAGE_SIZE,
                "searchText": search_text,
            }
            resp = client.post(api, json=payload)
            if resp.status_code != 200:
                if page == 0:
                    raise SourceError(f"Workday '{host}' returned HTTP {resp.status_code}")
                break
            data = resp.json()
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for item in postings:
                path = item.get("externalPath", "")
                jobs.append(
                    RawJob(
                        company=company,
                        title=item.get("title", ""),
                        location=item.get("locationsText", ""),
                        description=item.get("bulletFields") and ", ".join(map(str, item["bulletFields"])) or "",
                        url=f"https://{host}/{site}{path}" if path else f"https://{host}",
                        source="workday",
                        external_id=path or item.get("title", ""),
                        posted_at=_parse_posted(item.get("postedOnDate")),
                    )
                )
            total = data.get("total", 0)
            if (page + 1) * PAGE_SIZE >= total:
                break
    return jobs
