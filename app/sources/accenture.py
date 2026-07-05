"""Accenture careers elastic search API.

Uses the public multipart endpoint discovered on accenture.com career search:
POST https://www.accenture.com/api/accenture/elastic/findjobs

No authentication required; responses include title, location, description and
detail URLs for India and other country sites via the `country_site` field.
"""

from __future__ import annotations

from datetime import datetime

from app.sources.base import RawJob, SourceError, http_client

API = "https://www.accenture.com/api/accenture/elastic/findjobs"
PAGE_SIZE = 50
MAX_PAGES = 20

_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.accenture.com",
    "Referer": "https://www.accenture.com/in-en/careers/jobsearch",
}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _job_url(raw_url: str, country_site: str) -> str:
    if not raw_url:
        return ""
    url = raw_url.replace("{0}", country_site)
    if url.startswith("http"):
        return url
    return f"https://www.accenture.com{url}"


def _location(item: dict) -> str:
    loc = item.get("location")
    if isinstance(loc, list):
        loc = ", ".join(str(x).strip() for x in loc if x)
    parts = [loc, item.get("feedCity"), item.get("regionName"), item.get("country")]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if isinstance(part, list):
            text = ", ".join(str(x).strip() for x in part if x)
        else:
            text = (part or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        ordered.append(text)
    return ", ".join(ordered)


def fetch(entry: dict) -> list[RawJob]:
    company = entry.get("company") or "Accenture"
    country_site = entry.get("country_site") or "in-en"
    job_country = entry.get("job_country") or entry.get("country") or "India"
    job_keyword = entry.get("job_keyword") or entry.get("search_text") or ""
    job_language = entry.get("job_language") or "en"
    sort_by = str(entry.get("sort_by", "0"))
    page_size = int(entry.get("max_result_size", PAGE_SIZE))

    jobs: list[RawJob] = []
    with http_client() as client:
        for page in range(MAX_PAGES):
            start_index = page * page_size
            payload = {
                "startIndex": str(start_index),
                "maxResultSize": str(page_size),
                "jobKeyword": job_keyword,
                "jobCountry": job_country,
                "jobLanguage": job_language,
                "countrySite": country_site,
                "sortBy": sort_by,
            }
            resp = client.post(API, data=payload, headers=_HEADERS)
            if resp.status_code != 200:
                raise SourceError(f"Accenture API returned HTTP {resp.status_code}")
            body = resp.json()
            if body.get("error"):
                raise SourceError(body["error"])
            batch = body.get("data") or []
            if not batch:
                break
            for item in batch:
                req_id = str(item.get("requisitionId") or item.get("guid") or "")
                jobs.append(
                    RawJob(
                        company=company,
                        title=item.get("title", ""),
                        location=_location(item),
                        description=(item.get("jobDescriptionClean") or item.get("jobDescription") or "").strip(),
                        url=_job_url(item.get("jobDetailUrl", ""), country_site),
                        source="accenture",
                        external_id=req_id,
                        posted_at=_parse_date(item.get("updateDate") or item.get("postedDateText")),
                    )
                )
            total_raw = body.get("totalHits") or 0
            if isinstance(total_raw, dict):
                total_hits = int(total_raw.get("total") or 0)
            else:
                total_hits = int(total_raw or 0)
            if start_index + len(batch) >= total_hits:
                break
    return jobs
