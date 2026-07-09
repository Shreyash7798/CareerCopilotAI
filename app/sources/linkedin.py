"""LinkedIn public job search (guest API).

Uses the same unauthenticated endpoints LinkedIn's public job search pages call:
  GET /jobs-guest/jobs/api/seeMoreJobPostings/search
  GET /jobs-guest/jobs/api/jobPosting/{id}

No login, cookies, or LinkedIn API keys are required. Individual job pages expose
schema.org JobPosting JSON-LD for descriptions.

This is intentionally limited to public guest data — no profile scraping, no
login-walled content, no automated applications.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from app.sources.base import RawJob, SourceError, USER_AGENT, http_client

SEARCH_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_POSTING_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
JOB_ID_RE = re.compile(r"(\d{8,})")
URN_ID_RE = re.compile(r"urn:li:jobPosting:(\d+)")

LINKEDIN_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/search",
}


def linkedin_client(timeout: float = 30.0):
    """HTTP client with headers LinkedIn guest endpoints expect."""
    import httpx

    return httpx.Client(
        timeout=timeout,
        headers=LINKEDIN_HEADERS,
        follow_redirects=True,
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    return BeautifulSoup(unescape(text or ""), "lxml").get_text(separator="\n", strip=True)


def job_id_from_url(url: str) -> str | None:
    parsed = urlparse(url or "")
    if parsed.query:
        query = parse_qs(parsed.query)
        for key in ("currentJobId", "jobId", "job_id"):
            values = query.get(key) or []
            if values and values[0].isdigit():
                return values[0]
    match = JOB_ID_RE.search(url or "")
    return match.group(1) if match else None


def _job_id_from_card(card) -> str | None:
    urn = card.get("data-entity-urn") or ""
    match = URN_ID_RE.search(urn)
    if match:
        return match.group(1)
    link = card.select_one("a[href*='/jobs/view/']")
    if link and link.get("href"):
        return job_id_from_url(link["href"])
    return None


def _canonical_job_url(job_id: str, href: str | None = None) -> str:
    if href and href.startswith("http"):
        return href.split("?")[0]
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def _get_with_retry(client, url: str, *, params: dict | None = None, attempts: int = 3):
    last_resp = None
    for attempt in range(attempts):
        resp = client.get(url, params=params)
        last_resp = resp
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 503, 999) and attempt + 1 < attempts:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    return last_resp


def parse_search_card(card) -> RawJob | None:
    """Parse one job card from the guest search HTML fragment."""
    job_id = _job_id_from_card(card)
    if not job_id:
        return None

    title_el = card.select_one(".base-search-card__title")
    company_el = card.select_one(".base-search-card__subtitle, h4.base-search-card__subtitle")
    location_el = card.select_one(
        ".job-search-card__location, .base-search-card__metadata span, "
        ".base-search-card__metadata .job-search-card__location"
    )
    date_el = card.select_one("time.job-search-card__listdate, time[datetime]")
    link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")

    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    company = company_el.get_text(strip=True) if company_el else ""
    location = location_el.get_text(strip=True) if location_el else ""
    href = link_el.get("href") if link_el else None
    posted_at = _parse_datetime(date_el.get("datetime")) if date_el else None

    return RawJob(
        company=company or "Unknown",
        title=title,
        location=location,
        description="",
        url=_canonical_job_url(job_id, href),
        source="linkedin",
        external_id=job_id,
        posted_at=posted_at,
    )


def parse_job_posting_fragment(html: str) -> tuple[str, str, str, str, datetime | None]:
    """Parse the lightweight guest jobPosting API HTML fragment."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one(
        ".top-card-layout__title, h2.topcard__title, h1.top-card-layout__title"
    )
    company_el = soup.select_one(
        ".topcard__org-name-link, a.topcard__org-name-link, .top-card-layout__company"
    )
    location_el = soup.select_one(
        ".topcard__flavor--bullet, .top-card-layout__flavor, span.topcard__flavor"
    )
    description_el = soup.select_one(
        ".show-more-less-html__markup, .description__text, .description__job-description"
    )

    title = title_el.get_text(strip=True) if title_el else ""
    company = company_el.get_text(strip=True) if company_el else ""
    location = location_el.get_text(strip=True) if location_el else ""
    description = description_el.get_text("\n", strip=True) if description_el else ""

    posted_at = None
    for script in soup.select('script[type="application/ld+json"]'):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "JobPosting":
            continue
        description = description or _strip_html(data.get("description", ""))
        title = title or (data.get("title") or "")
        org = data.get("hiringOrganization") or {}
        if isinstance(org, dict) and not company:
            company = org.get("name") or company
        posted_at = _parse_datetime(data.get("datePosted"))
        break

    return title, company, location, description, posted_at


def fetch_job_posting(client, job_id: str) -> tuple[str, str, str, str, datetime | None]:
    """Return (title, company, location, description, posted_at) from guest jobPosting API."""
    resp = _get_with_retry(client, JOB_POSTING_API.format(job_id=job_id))
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "no response"
        raise SourceError(f"LinkedIn jobPosting API returned HTTP {code}")
    return parse_job_posting_fragment(resp.text)


def fetch_job_page(client, job_url: str) -> tuple[str, datetime | None]:
    """Return (description, posted_at) from a public LinkedIn job page."""
    resp = _get_with_retry(client, job_url)
    if resp is None or resp.status_code != 200:
        return "", None
    soup = BeautifulSoup(resp.text, "lxml")
    for script in soup.select('script[type="application/ld+json"]'):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "JobPosting":
            continue
        description = _strip_html(data.get("description", ""))
        posted_at = _parse_datetime(data.get("datePosted"))
        return description, posted_at
    markup = soup.select_one(".show-more-less-html__markup, .description__text")
    if markup:
        return markup.get_text("\n", strip=True), None
    return "", None


def parse_job_url(url: str, *, client=None) -> RawJob:
    """Import a single public LinkedIn job URL."""
    job_id = job_id_from_url(url)
    if not job_id:
        raise SourceError("Not a valid LinkedIn job URL")

    canonical = _canonical_job_url(job_id, url)
    if client is None:
        with linkedin_client() as owned:
            return _parse_job_url_with_client(owned, canonical, job_id)
    return _parse_job_url_with_client(client, canonical, job_id)


def _parse_job_url_with_client(client, canonical: str, job_id: str) -> RawJob:
    title = company = location = description = ""
    posted_at = None

    try:
        title, company, location, description, posted_at = fetch_job_posting(client, job_id)
    except SourceError:
        pass

    if not title or not description:
        page_description, page_posted = fetch_job_page(client, canonical)
        description = description or page_description
        posted_at = posted_at or page_posted

    if not title:
        resp = _get_with_retry(client, canonical)
        if resp is None or resp.status_code != 200:
            raise SourceError(f"LinkedIn job page returned HTTP {resp.status_code if resp else 'error'}")
        parsed_title, parsed_company, parsed_location, parsed_description, parsed_posted = (
            parse_job_posting_fragment(resp.text)
        )
        title = title or parsed_title
        company = company or parsed_company
        location = location or parsed_location
        description = description or parsed_description
        posted_at = posted_at or parsed_posted

        if not title:
            soup = BeautifulSoup(resp.text, "lxml")
            for script in soup.select('script[type="application/ld+json"]'):
                if not script.string:
                    continue
                try:
                    data = json.loads(script.string)
                except json.JSONDecodeError:
                    continue
                if data.get("@type") != "JobPosting":
                    continue
                title = data.get("title") or title
                org = data.get("hiringOrganization") or {}
                if isinstance(org, dict):
                    company = org.get("name") or company
                loc = data.get("jobLocation")
                if isinstance(loc, dict):
                    addr = loc.get("address") or {}
                    if isinstance(addr, dict):
                        parts = [
                            addr.get("addressLocality"),
                            addr.get("addressRegion"),
                            addr.get("addressCountry"),
                        ]
                        location = ", ".join(p for p in parts if p)
                posted_at = posted_at or _parse_datetime(data.get("datePosted"))
                description = description or _strip_html(data.get("description", ""))

        if not title:
            h1 = soup.select_one("h1.top-card-layout__title, h1")
            title = h1.get_text(strip=True) if h1 else ""

    if not title:
        raise SourceError("Could not parse job title from LinkedIn URL")

    return RawJob(
        company=company or "Unknown",
        title=title,
        location=location,
        description=description,
        url=canonical,
        source="linkedin",
        external_id=job_id,
        posted_at=posted_at,
    )


def fetch(entry: dict) -> list[RawJob]:
    keywords = entry.get("keywords") or entry.get("search_text") or ""
    location = entry.get("location") or ""
    if not keywords and not location:
        raise SourceError("LinkedIn source needs keywords and/or location in ats_config")

    f_tpr = entry.get("f_TPR") or entry.get("posted_within") or "r604800"
    page_size = 10
    max_pages = int(entry.get("max_pages", 5))
    detail_limit = int(entry.get("detail_limit", 20))
    remote_filter = entry.get("f_WT")  # optional: 2=remote

    jobs: list[RawJob] = []
    seen_ids: set[str] = set()

    with linkedin_client() as client:
        for page in range(max_pages):
            params = {
                "keywords": keywords,
                "location": location,
                "start": page * page_size,
            }
            if f_tpr:
                params["f_TPR"] = f_tpr
            if remote_filter:
                params["f_WT"] = remote_filter

            resp = _get_with_retry(client, SEARCH_API, params=params)
            if resp is None or resp.status_code != 200:
                if page == 0:
                    code = resp.status_code if resp is not None else "no response"
                    raise SourceError(
                        f"LinkedIn search returned HTTP {code}. "
                        "Datacenter IPs are sometimes blocked — try Import on the Jobs page."
                    )
                break

            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("div.base-search-card, li div.base-search-card")
            if not cards:
                break

            for card in cards:
                raw = parse_search_card(card)
                if raw is None or raw.external_id in seen_ids:
                    continue
                seen_ids.add(raw.external_id)
                if len(jobs) < detail_limit and raw.external_id:
                    try:
                        title, company, loc, description, posted = fetch_job_posting(
                            client, raw.external_id
                        )
                        if title and not raw.title:
                            raw.title = title
                        if company and (not raw.company or raw.company == "Unknown"):
                            raw.company = company
                        if loc and not raw.location:
                            raw.location = loc
                        if description:
                            raw.description = description
                        if posted and not raw.posted_at:
                            raw.posted_at = posted
                    except SourceError:
                        description, posted = fetch_job_page(client, raw.url)
                        if description:
                            raw.description = description
                        if posted and not raw.posted_at:
                            raw.posted_at = posted
                    time.sleep(0.35)
                jobs.append(raw)

            if len(cards) < page_size:
                break

    return jobs
