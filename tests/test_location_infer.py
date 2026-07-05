"""Location inference from job titles and URLs."""

from app.location_infer import infer_location


def test_infer_mumbai_from_pipe_title():
    title = "T&T- Customer - CS&D | Product Manager | Mumbai | Senior Consultant"
    assert infer_location(title=title) == "Mumbai"


def test_infer_pune_from_title():
    title = "Senior Consultant | S&P - Procurement Tech | Pune | Supply Chain"
    assert infer_location(title=title) == "Pune"


def test_infer_hyderabad_from_workday_url():
    url = "https://careers.ey.com/ey/job/Hyderabad-Senior-Consultant-Tech-Consulting/1368026633/"
    assert infer_location(title="", url=url) == "Hyderabad"


def test_keeps_existing_location():
    assert infer_location(title="| Mumbai |", existing="Pune") == "Pune"


def test_infer_multiple_cities():
    title = "Consultant - Mumbai and Pune travel"
    loc = infer_location(title=title)
    assert "Mumbai" in loc and "Pune" in loc
