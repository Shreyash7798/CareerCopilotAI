from app.dedup import dedup_key, is_fuzzy_duplicate
from app.normalize import normalize, normalize_location, normalize_title, passes_filters
from app.sources.base import RawJob


def test_dedup_key_stable_across_formatting():
    a = dedup_key("PwC India", "Senior Consultant - Operations", "Mumbai")
    b = dedup_key("pwc india", "Senior Consultant – Operations", " MUMBAI ")
    assert a == b


def test_dedup_key_differs_for_different_jobs():
    a = dedup_key("PwC", "Consultant", "Mumbai")
    b = dedup_key("PwC", "Consultant", "Pune")
    assert a != b


def test_fuzzy_duplicate():
    existing = ["Senior Consultant - Supply Chain Operations"]
    assert is_fuzzy_duplicate("Senior Consultant — Supply Chain Operations", existing)
    assert not is_fuzzy_duplicate("Data Engineer", existing)


def test_normalize_title_strips_noise():
    assert normalize_title("Consultant (Remote)") == "Consultant"
    assert normalize_title("  Strategy   Consultant  ") == "Strategy Consultant"


def test_normalize_location_alias():
    assert "Mumbai" in normalize_location("Bombay, India")


def test_passes_filters():
    job = RawJob(company="X", title="Operations Consultant", location="Pune")
    assert passes_filters(job, {"title_must_contain_any": ["consultant"]})
    assert not passes_filters(job, {"title_must_contain_any": ["engineer"]})
    assert passes_filters(job, {"title_must_contain_any": []})
    assert normalize(job).title == "Operations Consultant"
