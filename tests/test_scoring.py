from app.scoring import (
    score_company_fit,
    score_experience_fit,
    score_job,
    score_location_fit,
    score_role_fit,
)

PROFILE = {
    "experience_years": 3,
    "preferred_locations": ["Mumbai", "Pune"],
    "acceptable_locations": ["Remote"],
    "preferred_domains": ["Global Business Consulting"],
    "interests": ["Operations Consulting", "Supply Chain Consulting"],
    "skills": ["Supply Chain", "Excel", "Stakeholder Management", "Strategy"],
    "preferred_companies": ["McKinsey"],
    "avoided_companies": ["BadCorp"],
    "current_employer": "PwC India",
}

SCORING_CFG = {
    "role_keywords": ["consultant", "consulting", "strategy", "operations"],
    "negative_role_keywords": ["intern", "director"],
    "weights": {},
}


def test_role_fit_positive_and_negative():
    score, reason = score_role_fit("Senior Consultant - Operations", "", SCORING_CFG)
    assert score >= 0.6
    assert "consultant" in reason.lower()

    score, reason = score_role_fit("Consulting Intern", "", SCORING_CFG)
    assert score <= 0.2


def test_location_fit():
    assert score_location_fit("Mumbai, India", PROFILE)[0] == 1.0
    assert score_location_fit("Remote - India", PROFILE)[0] == 0.6
    assert score_location_fit("Berlin, Germany", PROFILE)[0] == 0.0


def test_experience_fit_ranges():
    score, _ = score_experience_fit("We need 2-5 years of experience", PROFILE)
    assert score == 1.0
    score, _ = score_experience_fit("Minimum 8 years experience required", PROFILE)
    assert score < 0.5
    score, _ = score_experience_fit("No experience info here", PROFILE)
    assert score == 0.6


def test_company_fit():
    assert score_company_fit("McKinsey & Company", PROFILE)[0] == 1.0
    assert score_company_fit("BadCorp Ltd", PROFILE)[0] == 0.0
    assert score_company_fit("Some Startup", PROFILE)[0] == 0.5


def test_score_job_deterministic_and_explainable():
    kwargs = dict(
        title="Operations Consultant",
        description="3-5 years experience in supply chain and strategy. Excel skills.",
        location="Pune, India",
        company="McKinsey",
        profile=PROFILE,
        scoring_cfg=SCORING_CFG,
    )
    score1, comps1 = score_job(**kwargs)
    score2, comps2 = score_job(**kwargs)
    assert score1 == score2  # deterministic
    assert 0 <= score1 <= 100
    assert score1 > 70  # strong match
    assert len(comps1) == 6
    assert all(c.reason for c in comps1)  # explainable
    # weights normalized
    assert abs(sum(c.weight for c in comps1) - 1.0) < 1e-9


def test_score_jd_fit_ignores_location_preference():
    """JD fit should not penalize Berlin if skills/experience match."""
    from app.scoring import score_jd_fit

    kwargs = dict(
        title="Operations Consultant",
        description="3-5 years experience in supply chain, strategy, and Excel required.",
        company="Acme Consulting",
        profile=PROFILE,
        scoring_cfg=SCORING_CFG,
    )
    jd_score, comps = score_jd_fit(**kwargs)
    assert 0 <= jd_score <= 100
    assert jd_score >= 50
    assert len(comps) == 4
    assert all("location" not in c.name for c in comps)


def test_score_jd_fit_rewards_skill_overlap():
    from app.scoring import score_jd_fit

    jd_score, _ = score_jd_fit(
        title="Supply Chain Consultant",
        description="Supply chain optimization, Excel, stakeholder management, 3 years experience.",
        company="Firm",
        profile=PROFILE,
        scoring_cfg=SCORING_CFG,
    )
    assert jd_score >= 60
