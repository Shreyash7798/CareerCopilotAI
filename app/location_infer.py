"""Infer job location from title/URL when ATS feeds leave it blank.

Many SAP SuccessFactors and careers-page connectors return titles like
"T&T- Customer | Mumbai | Senior Consultant" with an empty location field.
Without inference, location scoring stays neutral and good local matches never
reach the high-priority threshold.
"""

from __future__ import annotations

import re

# Longest names first so "New Delhi" wins over "Delhi".
_CITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnew\s+delhi\b", re.I), "Delhi"),
    (re.compile(r"\bgurugram\b", re.I), "Gurugram"),
    (re.compile(r"\bgurgaon\b", re.I), "Gurugram"),
    (re.compile(r"\bbengaluru\b", re.I), "Bengaluru"),
    (re.compile(r"\bbangalore\b", re.I), "Bengaluru"),
    (re.compile(r"\bmumbai\b", re.I), "Mumbai"),
    (re.compile(r"\bbombay\b", re.I), "Mumbai"),
    (re.compile(r"\bpune\b", re.I), "Pune"),
    (re.compile(r"\bhyderabad\b", re.I), "Hyderabad"),
    (re.compile(r"\bchennai\b", re.I), "Chennai"),
    (re.compile(r"\bkolkata\b", re.I), "Kolkata"),
    (re.compile(r"\bnoida\b", re.I), "Noida"),
    (re.compile(r"\bahmedabad\b", re.I), "Ahmedabad"),
    (re.compile(r"\bjaipur\b", re.I), "Jaipur"),
    (re.compile(r"\bdelhi\b", re.I), "Delhi"),
    (re.compile(r"\bsydney\b", re.I), "Sydney"),
    (re.compile(r"\bsingapore\b", re.I), "Singapore"),
    (re.compile(r"\blondon\b", re.I), "London"),
    (re.compile(r"\bremote\b", re.I), "Remote"),
]

# Workday / SAP URLs often embed the city: .../Hyderabad-Senior-Consultant-...
_URL_CITY = re.compile(
    r"/(?:job/)?([A-Za-z][A-Za-z\s]{2,30}?)-(?:Senior|Associate|Analyst|Consultant|Manager|Specialist|Director|Lead|Staff|Principal|Executive|Intern)",
    re.I,
)


def infer_location(*, title: str = "", url: str = "", existing: str = "") -> str:
    """Return `existing` when set, otherwise the best city guess from title/URL."""
    if (existing or "").strip():
        return existing.strip()

    found: list[str] = []
    seen: set[str] = set()
    text = f"{title}\n{url}"

    for pattern, canonical in _CITY_PATTERNS:
        if pattern.search(text) and canonical.lower() not in seen:
            seen.add(canonical.lower())
            found.append(canonical)

    if not found and url:
        m = _URL_CITY.search(url.replace("%20", " "))
        if m:
            city = m.group(1).strip().title()
            if city.lower() not in seen:
                found.append(city)

    return ", ".join(found)
