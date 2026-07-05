"""Recruiter intelligence from public job posting text (spec section 17).

Extracts only information that appears in the job description itself —
recruiter/hiring-manager names and publicly listed work emails. No scraping
of LinkedIn, no private data, no outbound messaging.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Recruiter

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Common noise domains in boilerplate footers.
_SKIP_EMAIL_DOMAINS = {"example.com", "w3.org", "schema.org", "sentry.io"}

NAME_PATTERNS = [
  re.compile(
      r"(?:contact|recruiter|hiring\s+manager|talent\s+acquisition|reach\s+out\s+to)"
      r"[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
      re.IGNORECASE,
  ),
  re.compile(
      r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+"
      r"(?:at\s+)?[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
  ),
]


_SKIP_NAME_WORDS = {
    "contact", "questions", "email", "reach", "hiring", "manager", "recruiter",
    "talent", "team", "human", "resources", "please", "apply", "careers",
}


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name.strip())
    if len(name) < 3 or len(name) > 60:
        return ""
    lowered = name.lower()
    if lowered in _SKIP_NAME_WORDS or lowered in {"the team", "our team", "human resources", "talent team"}:
        return ""
    words = name.split()
    if len(words) == 1 and words[0].lower() in _SKIP_NAME_WORDS:
        return ""
    return name


def extract_from_description(description: str) -> list[dict]:
    """Return candidate recruiter records found in a job description."""
    if not description:
        return []
    text = description[:12000]
    found: list[dict] = []
    seen_emails: set[str] = set()
    seen_names: set[str] = set()
    email_locals: set[str] = set()

    for email in EMAIL_RE.findall(text):
        domain = email.split("@")[-1].lower()
        if domain in _SKIP_EMAIL_DOMAINS or email.lower() in seen_emails:
            continue
        seen_emails.add(email.lower())
        email_locals.add(email.split("@")[0].lower())
        found.append({"name": "", "public_email": email, "department": None})

    for pattern in NAME_PATTERNS:
        for match in pattern.finditer(text):
            name = _clean_name(match.group(1))
            if not name:
                continue
            if name.lower() in email_locals or name.split()[0].lower() in email_locals:
                continue
            if name.lower() not in seen_names:
                seen_names.add(name.lower())
                found.append({"name": name, "public_email": None, "department": None})

    return found


def upsert_recruiters(session, *, company_id: int | None, company_name: str, job_title: str, description: str) -> int:
    """Persist newly discovered recruiters; link requisitions by job title."""
    added = 0
    for item in extract_from_description(description):
        name = item.get("name") or ""
        email = (item.get("public_email") or "").lower()
        if not name and not email:
            continue

        existing = None
        if email:
            existing = session.execute(
                select(Recruiter).where(Recruiter.public_email == email)
            ).scalar_one_or_none()
        if existing is None and name:
            existing = session.execute(
                select(Recruiter).where(
                    Recruiter.name == name,
                    Recruiter.company_id == company_id,
                )
            ).scalar_one_or_none()

        req_note = job_title[:200]
        if existing is None:
            session.add(
                Recruiter(
                    company_id=company_id,
                    name=name or email.split("@")[0].replace(".", " ").title(),
                    public_email=email or None,
                    department=item.get("department"),
                    related_requisitions=req_note,
                    notes="Auto-discovered from public job posting",
                )
            )
            added += 1
        else:
            reqs = existing.related_requisitions or ""
            if req_note not in reqs:
                existing.related_requisitions = f"{reqs}; {req_note}".strip("; ").strip()
            if email and not existing.public_email:
                existing.public_email = email
    return added
