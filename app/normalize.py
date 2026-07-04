"""Normalization of raw jobs into a canonical shape before dedup/scoring."""

from __future__ import annotations

import re

from app.sources.base import RawJob

_WHITESPACE = re.compile(r"\s+")

# Common noise in titles that should not affect dedup or scoring.
_TITLE_NOISE = re.compile(
    r"\((?:remote|hybrid|on-?site|f/m/d|m/f/d|all genders)\)|"
    r"\b(?:remote|hybrid)\b[-–—]?\s*$",
    re.IGNORECASE,
)

_LOCATION_ALIASES = {
    "bombay": "Mumbai",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "gurgaon": "Gurugram",
    "new delhi": "Delhi",
}


def clean_text(value: str | None) -> str:
    return _WHITESPACE.sub(" ", (value or "").strip())


def normalize_title(title: str) -> str:
    title = clean_text(title)
    title = _TITLE_NOISE.sub("", title)
    return clean_text(title)


def normalize_location(location: str) -> str:
    location = clean_text(location)
    lowered = location.lower()
    for alias, canonical in _LOCATION_ALIASES.items():
        if alias in lowered:
            lowered = lowered.replace(alias, canonical.lower())
    # Preserve original casing where possible, only rewrite if alias applied.
    if lowered != location.lower():
        return ", ".join(part.strip().title() for part in lowered.split(","))
    return location


def normalize(job: RawJob) -> RawJob:
    job.company = clean_text(job.company)
    job.title = normalize_title(job.title)
    job.location = normalize_location(job.location)
    job.description = (job.description or "").strip()
    job.url = clean_text(job.url)
    return job


def passes_filters(job: RawJob, filters: dict) -> bool:
    title_terms = [t.lower() for t in filters.get("title_must_contain_any") or []]
    if title_terms and not any(t in job.title.lower() for t in title_terms):
        return False
    loc_terms = [t.lower() for t in filters.get("location_must_contain_any") or []]
    if loc_terms and not any(t in (job.location or "").lower() for t in loc_terms):
        return False
    return True
