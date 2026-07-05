"""Careers page detail fetch and deploy revision."""

from unittest.mock import MagicMock, patch

from app.sources import careers_page


def test_fetch_includes_description_for_detail_limit():
    list_html = """
    <html><body>
      <a href="/jobs/1">Consultant Mumbai</a>
      <a href="/jobs/2">Analyst Pune</a>
    </body></html>
    """
    detail_html = "<html><body><div class='job-description'>Python SQL consulting 3 years</div></body></html>"

    def fake_http(url):
        if url == "https://co.example/search":
            return list_html
        return detail_html

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = fake_http(url)
            return resp

    with patch.object(careers_page, "http_client", lambda: FakeClient()):
        jobs = careers_page.fetch(
            {
                "url": "https://co.example/search",
                "company": "Test Co",
                "link_selector": "a",
                "detail_limit": 1,
            }
        )

    assert len(jobs) == 2
    assert jobs[0].description.startswith("Python SQL")
    assert jobs[1].description == ""


def test_revision_file_fallback(tmp_path, monkeypatch):
    from app import version as version_mod

    rev_file = tmp_path / "REVISION"
    rev_file.write_text("abc1234\n")
    monkeypatch.setattr(version_mod, "REVISION_FILE", rev_file)
    assert version_mod.git_revision() == "abc1234"
