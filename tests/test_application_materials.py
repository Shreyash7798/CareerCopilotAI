"""Recruiter discovery, cover letter and interview prep."""

from app.cover_letter_engine import generate_cover_letter
from app.interview_prep import build_interview_prep
from app.recruiter_discovery import extract_from_description, upsert_recruiters


def test_extract_emails_and_names():
    text = """
    For questions contact Jane Recruiter at talent@acme.com.
    Hiring Manager: Rahul Sharma
    """
    found = extract_from_description(text)
    emails = [f["public_email"] for f in found if f.get("public_email")]
    names = [f["name"] for f in found if f.get("name")]
    assert "talent@acme.com" in emails
    assert any("Rahul" in n or "Jane" in n for n in names)


def test_upsert_recruiters_dedupes(tmp_path, monkeypatch):
    import app.db as db_mod
    from app.models import Company, Recruiter

    monkeypatch.setattr(db_mod, "database_url", lambda: f"sqlite:///{tmp_path}/rec.db")
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_SessionLocal", None)
    db_mod.init_db()

    with db_mod.session_scope() as session:
        company = Company(name="Acme")
        session.add(company)
        session.flush()
        n1 = upsert_recruiters(
            session,
            company_id=company.id,
            company_name="Acme",
            job_title="Consultant",
            description="Contact talent@acme.com for this Consultant role.",
        )
        n2 = upsert_recruiters(
            session,
            company_id=company.id,
            company_name="Acme",
            job_title="Senior Consultant",
            description="Reach out to talent@acme.com",
        )
        assert n1 == 1
        assert n2 == 0  # same email, no duplicate row
        from sqlalchemy import select

        assert session.execute(select(Recruiter)).scalars().one().related_requisitions.count("Consultant")


def test_cover_letter_uses_profile_facts(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cover_letter_engine.data_dir", lambda: tmp_path)
    profile = {
        "full_name": "Rahul Sharma",
        "email": "rahul@example.com",
        "experience_years": 3,
        "current_employer": "PwC India",
        "skills": ["Supply Chain", "Excel", "Strategy"],
        "interests": ["Operations Consulting"],
    }
    result = generate_cover_letter(
        job_title="Operations Consultant",
        company="Acme Corp",
        job_description="We need supply chain and strategy experience. Excel required.",
        profile=profile,
    )
    assert result["docx"]
    assert "PwC India" in result["text"]
    assert "Supply Chain" in result["text"]
    assert "fabricated" not in result["text"].lower()


def test_interview_prep_structure():
    prep = build_interview_prep(
        job_title="Consultant",
        company="Acme",
        job_description="Supply chain transformation and process excellence required.",
        location="Pune",
        profile={
            "full_name": "Rahul",
            "experience_years": 3,
            "current_employer": "PwC",
            "skills": ["Supply Chain", "Excel"],
            "employers": ["PwC India"],
        },
        score_breakdown=[{"name": "role_fit", "score": 0.9, "reason": "Strong match"}],
    )
    assert prep["elevator_pitch"]
    assert prep["star_prompts"]
    assert len(prep["questions_for_interviewer"]) >= 3
    assert "Supply Chain" in prep["matched_skills"] or prep["matched_skills"]
