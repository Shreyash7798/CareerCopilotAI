"""SmartRecruiters public postings API source.

Uses the documented, unauthenticated JSON API:
https://api.smartrecruiters.com/v1/companies/<company>/postings

Full job descriptions live on a per-posting detail endpoint; they are fetched
for the first `detail_limit` postings (default 25) to keep requests polite.
"""

from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from app.sources.base import RawJob, SourceError, http_client

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
PAGE_SIZE = 100
MAX_PAGES = 10


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _fetch_description(client, token: str, posting_id: str) -> str:
    resp = client.get(f"{API.format(company=token)}/{posting_id}")
    if resp.status_code != 200:
        return ""
    sections = ((resp.json() or {}).get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        html = (sections.get(key) or {}).get("text", "")
        if html:
            parts.append(BeautifulSoup(html, "lxml").get_text(separator="\n", strip=True))
    return "\n\n".join(parts)


def fetch(entry: dict) -> list[RawJob]:
    token = entry.get("board") or entry.get("company", "").lower()
    company = entry.get("company") or token
    search_text = entry.get("search_text", "")
    detail_limit = int(entry.get("detail_limit", 25))

    jobs: list[RawJob] = []
    with http_client() as client:
        for page in range(MAX_PAGES):
            params = {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
            if search_text:
                params["q"] = search_text
            resp = client.get(API.format(company=token), params=params)
            if resp.status_code != 200:
                raise SourceError(f"SmartRecruiters '{token}' returned HTTP {resp.status_code}")
            data = resp.json()
            postings = data.get("content", [])
            if not postings:
                break
            for item in postings:
                loc = item.get("location") or {}
                location = ", ".join(
                    v for v in (loc.get("city"), loc.get("region"), loc.get("country")) if v
                )
                posting_id = str(item.get("id", ""))
                description = ""
                if posting_id and len(jobs) < detail_limit:
                    description = _fetch_description(client, token, posting_id)
                jobs.append(
                    RawJob(
                        company=company,
                        title=item.get("name", ""),
                        location=location,
                        description=description,
                        url=f"https://jobs.smartrecruiters.com/{token}/{posting_id}",
                        source="smartrecruiters",
                        external_id=posting_id,
                        posted_at=_parse_date(item.get("releasedDate")),
                    )
                )
            if (page + 1) * PAGE_SIZE >= int(data.get("totalFound", 0)):
                break
    return jobs
