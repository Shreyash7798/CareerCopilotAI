"""Workday career site source.

Workday career sites expose a public JSON API (the same one the site's own
frontend calls):

POST https://<host>/wday/cxs/<tenant>/<site>/jobs
     {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "..."}

The list endpoint has no job description. If `detail_limit: N` is set on the
source entry, full descriptions are fetched for the first N postings (one
extra GET per job), which makes match scoring and resume tailoring much more
accurate. Keep N modest to stay polite to the career site.
"""

from __future__ import annotations

from datetime import datetime
from html import unescape

from bs4 import BeautifulSoup

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


def _fetch_description(client, host: str, tenant: str, site: str, external_path: str) -> str:
    """Fetch the full JD for one posting from the CXS detail endpoint."""
    url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"
    resp = client.get(url)
    if resp.status_code != 200:
        return ""
    info = (resp.json() or {}).get("jobPostingInfo") or {}
    html = info.get("jobDescription", "")
    return BeautifulSoup(unescape(html), "lxml").get_text(separator="\n", strip=True)


def fetch(entry: dict) -> list[RawJob]:
    host = entry["host"].rstrip("/")
    tenant = entry["tenant"]
    site = entry["site"]
    company = entry.get("company") or tenant
    search_text = entry.get("search_text", "")
    detail_limit = int(entry.get("detail_limit", 25))
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
                description = ""
                if path and len(jobs) < detail_limit:
                    description = _fetch_description(client, host, tenant, site, path)
                jobs.append(
                    RawJob(
                        company=company,
                        title=item.get("title", ""),
                        location=item.get("locationsText", ""),
                        description=description
                        or (item.get("bulletFields") and ", ".join(map(str, item["bulletFields"])) or ""),
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
