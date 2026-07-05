"""Accenture elastic search source."""

from unittest.mock import MagicMock

from app.sources import accenture


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def test_fetch_parses_jobs(monkeypatch):
    pages = [
        {
            "data": [
                {
                    "title": "Consulting Manager",
                    "requisitionId": "R123",
                    "jobDetailUrl": "https://www.accenture.com/{0}/careers/jobdetails?id=R123",
                    "jobDescriptionClean": "Lead consulting engagements.",
                    "location": ["Mumbai"],
                    "country": "India",
                    "updateDate": "2026-05-19T00:56:19.814-07:00",
                }
            ],
            "totalHits": {"total": 1},
        }
    ]
    calls: list[dict] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, headers=None):
            calls.append(data)
            return _mock_response(pages.pop(0))

    monkeypatch.setattr(accenture, "http_client", lambda: FakeClient())

    jobs = accenture.fetch(
        {
            "company": "Accenture India",
            "country_site": "in-en",
            "job_country": "India",
            "search_text": "consulting",
        }
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Consulting Manager"
    assert job.external_id == "R123"
    assert job.source == "accenture"
    assert "in-en/careers/jobdetails" in job.url
    assert job.location == "Mumbai, India"
    assert job.description.startswith("Lead consulting")
    assert calls[0]["sortBy"] == "0"


def test_fetch_raises_on_api_error(monkeypatch):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None, headers=None):
            return _mock_response({"error": "bad payload"})

    monkeypatch.setattr(accenture, "http_client", lambda: FakeClient())

    try:
        accenture.fetch({"company": "Accenture"})
        raised = False
    except accenture.SourceError as exc:
        raised = True
        assert "bad payload" in str(exc)
    assert raised
