"""Interview preparation — deterministic talking points from profile vs JD.

No external AI APIs. Uses the same keyword/scoring vocabulary as the rest
of the platform so prep stays explainable and local.
"""

from __future__ import annotations

import json

from app.config import get_profile
from app.resume_engine import extract_keywords


def _skill_gaps(profile_skills: list[str], jd_keywords: list[str]) -> list[str]:
    profile_blob = " ".join(profile_skills).lower()
    gaps = []
    for kw in jd_keywords[:20]:
        if kw not in profile_blob and len(kw) > 3:
            gaps.append(kw)
    return gaps[:10]


def _star_prompts(profile: dict, jd_keywords: list[str]) -> list[str]:
    employers = profile.get("employers") or []
    if isinstance(employers, str):
        employers = [employers]
    current = profile.get("current_employer")
    if current and current not in employers:
        employers = [current] + list(employers)
    skills = profile.get("skills") or []
    focus = ", ".join((jd_keywords[:4] + skills[:3])[:5]) or "the role requirements"
    prompts = []
    for emp in employers[:3]:
        prompts.append(
            f"At {emp}: describe a project involving {focus}. "
            "Structure: Situation → Task → Action → Result (quantify the outcome)."
        )
    if not prompts:
        prompts.append(
            f"Prepare one STAR story demonstrating {focus} with a measurable business outcome."
        )
    return prompts


def _questions_for_interviewer(company: str, job_title: str) -> list[str]:
    return [
        f"What does success look like in the first 90 days for this {job_title} role?",
        f"How is this team structured within {company}, and who are the key stakeholders?",
        "What are the most urgent priorities for the person in this role?",
        "How does the team measure impact for consulting / transformation engagements?",
        "What does the interview process look like from here, and what should I prepare?",
    ]


def build_interview_prep(
    *,
    job_title: str,
    company: str,
    job_description: str,
    location: str = "",
    score_breakdown: list[dict] | None = None,
    profile: dict | None = None,
) -> dict:
    profile = profile or get_profile()
    jd_keywords = extract_keywords(job_description or "", top_n=25)
    skills = profile.get("skills") or []
    matched = [s for s in skills if any(k in s.lower() for k in jd_keywords)]
    gaps = _skill_gaps(skills, jd_keywords)

    strengths = []
    if score_breakdown:
        for item in sorted(score_breakdown, key=lambda x: -x.get("score", 0))[:4]:
            if item.get("score", 0) >= 0.5:
                strengths.append(f"{item['name'].replace('_', ' ')}: {item.get('reason', '')}")

    prep = {
        "job_title": job_title,
        "company": company,
        "location": location,
        "matched_skills": matched[:12],
        "skills_to_emphasise": matched[:6],
        "skills_to_address": gaps[:8],
        "strengths_from_score": strengths,
        "star_prompts": _star_prompts(profile, jd_keywords),
        "questions_for_interviewer": _questions_for_interviewer(company, job_title),
        "jd_keywords": jd_keywords[:15],
        "elevator_pitch": _elevator_pitch(profile, job_title, company, matched),
    }
    prep["content_json"] = json.dumps(prep)
    return prep


def _elevator_pitch(profile: dict, job_title: str, company: str, matched: list[str]) -> str:
    name = profile.get("full_name") or "I"
    years = profile.get("experience_years")
    employer = profile.get("current_employer") or ""
    bits = [f"I'm {name}"]
    if years:
        bits.append(f"a professional with {years:g} years of experience")
    if employer:
        bits.append(f"currently at {employer}")
    if matched:
        bits.append(f"with strengths in {', '.join(matched[:4])}")
    bits.append(f"and I'm excited about the {job_title} opportunity at {company}.")
    return ", ".join(bits[:2]) + ", " + ", ".join(bits[2:]) if len(bits) > 2 else ". ".join(bits) + "."
