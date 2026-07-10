"""Deterministic, explainable match scoring (spec section 11).

Two complementary scores are stored per job:

* **match_score** — how well the role fits *your preferences* (locations,
  preferred companies, interests, configured role keywords).
* **jd_fit_score** — how well *your background* matches what the company
  asks for in the job description (skills, experience, role terms), without
  preference bias.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.resume_engine import extract_keywords


@dataclass
class ComponentScore:
    name: str
    score: float  # 0..1
    reason: str
    weight: float = 0.0

    @property
    def weighted(self) -> float:
        return self.score * self.weight


def _contains_any(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [t for t in terms if t.lower() in lowered]


def _extract_experience_range(description: str) -> tuple[float, float] | None:
    """Find requirements like '3-5 years', '3+ years', 'minimum 4 years'."""
    text = description.lower()
    m = re.search(r"(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*\+?\s*years?", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:minimum|min\.?|at least)\s*(\d{1,2})\s*\+?\s*years?", text)
    if m:
        return float(m.group(1)), float(m.group(1)) + 4
    m = re.search(r"(\d{1,2})\s*\+\s*years?", text)
    if m:
        return float(m.group(1)), float(m.group(1)) + 4
    return None


def score_role_fit(title: str, description: str, scoring_cfg: dict) -> tuple[float, str]:
    keywords = scoring_cfg.get("role_keywords") or []
    negatives = scoring_cfg.get("negative_role_keywords") or []
    hits = _contains_any(title, keywords)
    desc_hits = [] if hits else _contains_any(description[:2000], keywords)
    neg_hits = _contains_any(title, negatives)

    if neg_hits:
        return 0.1, f"Title contains excluded terms: {', '.join(neg_hits)}"
    if hits:
        score = min(1.0, 0.6 + 0.2 * len(hits))
        return score, f"Title matches role keywords: {', '.join(hits)}"
    if desc_hits:
        return 0.4, f"Description mentions role keywords: {', '.join(desc_hits[:4])}"
    return 0.0, "No role keywords found in title or description"


def score_location_fit(location: str, profile: dict) -> tuple[float, str]:
    preferred = profile.get("preferred_locations") or []
    acceptable = profile.get("acceptable_locations") or []
    if not location:
        return 0.3, "No location listed on the job"
    pref_hits = _contains_any(location, preferred)
    if pref_hits:
        return 1.0, f"Located in preferred location: {', '.join(pref_hits)}"
    acc_hits = _contains_any(location, acceptable)
    if acc_hits:
        return 0.6, f"Located in acceptable location: {', '.join(acc_hits)}"
    if "remote" in location.lower():
        return 0.5, "Remote role"
    return 0.0, f"'{location}' is not in your preferred or acceptable locations"


def score_experience_fit(description: str, profile: dict) -> tuple[float, str]:
    years = float(profile.get("experience_years") or 0)
    required = _extract_experience_range(description or "")
    if required is None:
        return 0.6, "Job does not state a required experience range"
    low, high = required
    if low <= years <= high:
        return 1.0, f"Your {years:.0f} years fit the required {low:.0f}-{high:.0f} years"
    if years < low:
        gap = low - years
        score = max(0.0, 1.0 - 0.35 * gap)
        return score, f"Job asks for {low:.0f}+ years; you have {years:.0f} ({gap:.0f} short)"
    over = years - high
    score = max(0.2, 1.0 - 0.15 * over)
    return score, f"Job targets {low:.0f}-{high:.0f} years; you have {years:.0f} (may be senior for it)"


def score_industry_fit(title: str, description: str, profile: dict) -> tuple[float, str]:
    domains = (profile.get("preferred_domains") or []) + (profile.get("interests") or [])
    text = f"{title}\n{description[:4000]}"
    hits = _contains_any(text, domains)
    if hits:
        score = min(1.0, 0.5 + 0.25 * len(hits))
        return score, f"Matches your domains/interests: {', '.join(hits[:5])}"
    return 0.2, "No overlap with your preferred domains or interests"


def score_skills_fit(description: str, profile: dict) -> tuple[float, str]:
    skills = profile.get("skills") or []
    if not skills:
        return 0.5, "No skills in profile to compare"
    hits = _contains_any(description or "", skills)
    ratio = len(hits) / len(skills)
    score = min(1.0, ratio * 2)  # matching half your skills is a full score
    if hits:
        return score, f"{len(hits)}/{len(skills)} of your skills appear in the JD: {', '.join(hits[:8])}"
    return 0.0, "None of your listed skills appear in the job description"


def score_company_fit(company: str, profile: dict) -> tuple[float, str]:
    avoided = _contains_any(company, profile.get("avoided_companies") or [])
    if avoided:
        return 0.0, f"Company is on your avoid list ({', '.join(avoided)})"
    preferred = _contains_any(company, profile.get("preferred_companies") or [])
    if preferred:
        return 1.0, "Company is on your preferred list"
    current = (profile.get("current_employer") or "").lower()
    lowered = company.lower()
    if current and (current in lowered or lowered in current):
        return 0.3, "This is your current employer"
    return 0.5, "Neutral: company not on preferred or avoid lists"


def score_job(
    *,
    title: str,
    description: str,
    location: str,
    company: str,
    profile: dict,
    scoring_cfg: dict,
) -> tuple[float, list[ComponentScore]]:
    """Return (score 0-100, per-component breakdown)."""
    weights = scoring_cfg.get("weights") or {}
    defaults = {
        "role_fit": 0.30,
        "location_fit": 0.20,
        "experience_fit": 0.15,
        "industry_fit": 0.10,
        "skills_fit": 0.15,
        "company_fit": 0.10,
    }
    merged = {k: float(weights.get(k, v)) for k, v in defaults.items()}
    total_weight = sum(merged.values()) or 1.0
    norm = {k: v / total_weight for k, v in merged.items()}

    components = [
        ComponentScore("role_fit", *score_role_fit(title, description, scoring_cfg), weight=norm["role_fit"]),
        ComponentScore("location_fit", *score_location_fit(location, profile), weight=norm["location_fit"]),
        ComponentScore("experience_fit", *score_experience_fit(description, profile), weight=norm["experience_fit"]),
        ComponentScore("industry_fit", *score_industry_fit(title, description, profile), weight=norm["industry_fit"]),
        ComponentScore("skills_fit", *score_skills_fit(description, profile), weight=norm["skills_fit"]),
        ComponentScore("company_fit", *score_company_fit(company, profile), weight=norm["company_fit"]),
    ]
    total = round(sum(c.weighted for c in components) * 100, 1)
    return total, components


def load_scoring_profile() -> dict:
    """Merge settings profile with parsed CV facts for scoring."""
    from sqlalchemy import select

    from app.config import get_profile
    from app.db import session_scope
    from app.models import UserProfile

    profile = dict(get_profile())
    with session_scope() as session:
        row = session.execute(select(UserProfile)).scalars().first()
        if row and row.profile_json:
            try:
                parsed = json.loads(row.profile_json)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                if parsed.get("skills"):
                    profile["skills"] = list(
                        dict.fromkeys((profile.get("skills") or []) + parsed["skills"])
                    )
                if parsed.get("employers"):
                    profile["employers"] = list(
                        dict.fromkeys((profile.get("employers") or []) + parsed["employers"])
                    )
                for key in ("full_name", "email", "phone", "experience_years", "raw_text"):
                    if parsed.get(key) and not profile.get(key):
                        profile[key] = parsed[key]
    return profile


def _background_text(profile: dict) -> str:
    parts = [
        profile.get("current_employer") or "",
        " ".join(profile.get("previous_employers") or []),
        " ".join(profile.get("employers") or []),
        " ".join(profile.get("skills") or []),
        profile.get("cv_text") or profile.get("raw_text") or "",
    ]
    return "\n".join(parts).lower()


def score_jd_skills_match(description: str, profile: dict) -> tuple[float, str]:
    """Overlap between JD skill terms and the candidate's skills / CV background."""
    jd_terms = extract_keywords(description or "", top_n=30)
    if not jd_terms:
        return 0.5, "Job description has little text to compare against your background"

    background = _background_text(profile)
    skills = [s.lower() for s in (profile.get("skills") or [])]
    hits: list[str] = []
    for term in jd_terms:
        if term in background or any(term in skill for skill in skills):
            hits.append(term)

    ratio = len(hits) / len(jd_terms)
    score = min(1.0, ratio * 1.4)
    if hits:
        return score, (
            f"{len(hits)}/{len(jd_terms)} key JD terms match your background: "
            f"{', '.join(hits[:8])}"
        )
    return 0.0, "None of the main JD skill terms appear in your skills or CV"


def score_jd_role_alignment(title: str, description: str, profile: dict) -> tuple[float, str]:
    """Whether your career background supports the role type described in the posting."""
    role_terms = extract_keywords(f"{title}\n{(description or '')[:2000]}", top_n=20)
    if not role_terms:
        return 0.5, "Could not extract role terms from the posting"

    background = _background_text(profile)
    hits = [t for t in role_terms if t in background]
    score = min(1.0, len(hits) / max(4, len(role_terms) * 0.45))
    if hits:
        return score, f"Your background reflects JD role themes: {', '.join(hits[:6])}"
    return 0.15, "Your listed experience does not closely reflect this role type in the JD"


def score_jd_experience_match(description: str, profile: dict) -> tuple[float, str]:
    """Experience years vs what the JD states — company requirement, not preference."""
    score, reason = score_experience_fit(description, profile)
    return score, f"JD experience fit: {reason}"


def score_jd_education_match(description: str, profile: dict) -> tuple[float, str]:
    """Light check for degree / MBA requirements mentioned in the JD."""
    text = (description or "").lower()
    degree_terms = [
        "mba",
        "master",
        "bachelor",
        "b.tech",
        "b.e.",
        "engineering degree",
        "ca ",
        "chartered accountant",
    ]
    mentioned = [t.strip() for t in degree_terms if t in text]
    if not mentioned:
        return 0.6, "JD does not state a specific education requirement"

    background = _background_text(profile)
    cv_hits = [t for t in mentioned if t in background]
    if cv_hits:
        return 1.0, f"JD mentions education ({', '.join(cv_hits[:3])}) and your CV/profile reflects it"
    return 0.35, f"JD mentions education ({', '.join(mentioned[:3])}) — not found in your stored profile/CV"


def score_jd_fit(
    *,
    title: str,
    description: str,
    company: str,
    profile: dict | None = None,
    scoring_cfg: dict | None = None,
) -> tuple[float, list[ComponentScore]]:
    """Return JD-centric fit (0-100): company requirements vs your background."""
    profile = profile or load_scoring_profile()
    scoring_cfg = scoring_cfg or {}
    weights = (scoring_cfg.get("jd_fit_weights") or {}) if scoring_cfg else {}
    defaults = {
        "jd_skills": 0.35,
        "jd_experience": 0.30,
        "jd_role": 0.20,
        "jd_education": 0.15,
    }
    merged = {k: float(weights.get(k, v)) for k, v in defaults.items()}
    total_weight = sum(merged.values()) or 1.0
    norm = {k: v / total_weight for k, v in merged.items()}

    components = [
        ComponentScore(
            "jd_skills",
            *score_jd_skills_match(description, profile),
            weight=norm["jd_skills"],
        ),
        ComponentScore(
            "jd_experience",
            *score_jd_experience_match(description, profile),
            weight=norm["jd_experience"],
        ),
        ComponentScore(
            "jd_role",
            *score_jd_role_alignment(title, description, profile),
            weight=norm["jd_role"],
        ),
        ComponentScore(
            "jd_education",
            *score_jd_education_match(description, profile),
            weight=norm["jd_education"],
        ),
    ]
    total = round(sum(c.weighted for c in components) * 100, 1)
    return total, components
