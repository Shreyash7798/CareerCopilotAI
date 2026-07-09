"""Generic public careers page source.

Fetches a page over plain HTTP and extracts job links with a CSS selector.
If the page is JavaScript-rendered, set `render: true` in sources.yaml and the
page is rendered with Playwright (Chromium, headless) first.

When Crawl4AI is enabled in settings.yaml, JS-rendered pages can use the
Crawl4AI sidecar instead of local Playwright (saves RAM on small VMs).

For the first `detail_limit` postings (default 15), follows each job link and
extracts description text so SAP/Oracle list pages score on full JDs.
"""

from __future__ import annotations

from app.sources import crawl4ai_client
from app.sources.base import RawJob, SourceError, http_client
from app.sources.page_scrape import extract_description, parse_job_listing

_PAGE_TIMEOUT_MS = 30_000
_RENDER_SETTLE_MS = 2_000


def _fetch_html_http(url: str) -> str:
    with http_client() as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise SourceError(f"Careers page '{url}' returned HTTP {resp.status_code}")
    return resp.text


class _PlaywrightSession:
    """Reuse one Chromium instance for a listing page and its detail fetches."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    def __enter__(self) -> _PlaywrightSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise SourceError("Playwright is not installed; run `playwright install chromium`") from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._page = self._browser.new_page()
        return self

    def fetch(self, url: str) -> str:
        assert self._page is not None
        self._page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
        self._page.wait_for_timeout(_RENDER_SETTLE_MS)
        return self._page.content()

    def __exit__(self, *_args) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()


def _fetch_html_playwright(url: str) -> str:
    with _PlaywrightSession() as session:
        return session.fetch(url)


def _fetch_html_crawl4ai(url: str) -> str:
    return crawl4ai_client.fetch_html(url)


def _should_prefer_crawl4ai(render: bool) -> bool:
    if not render or not crawl4ai_client.is_enabled():
        return False
    cfg = crawl4ai_client.crawl4ai_settings()
    return bool(cfg.get("prefer_over_playwright", True))


def _should_fallback_crawl4ai(render: bool) -> bool:
    if not render or not crawl4ai_client.is_enabled():
        return False
    cfg = crawl4ai_client.crawl4ai_settings()
    return bool(cfg.get("fallback_on_playwright_failure", True))


def _page_html(url: str, render: bool) -> str:
    if not render:
        return _fetch_html_http(url)

    errors: list[str] = []

    if _should_prefer_crawl4ai(render):
        try:
            return _fetch_html_crawl4ai(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Crawl4AI: {exc}")

    try:
        return _fetch_html_playwright(url)
    except Exception as exc:
        errors.append(f"Playwright: {exc}")
        if crawl4ai_client.is_enabled() and _should_fallback_crawl4ai(render):
            try:
                return _fetch_html_crawl4ai(url)
            except Exception as exc2:  # noqa: BLE001
                errors.append(f"Crawl4AI fallback: {exc2}")

    raise SourceError("Failed to render careers page — " + "; ".join(errors))


def _fetch_description_http(job_url: str, detail_selector: str | None, client) -> str:
    resp = client.get(job_url)
    if resp.status_code != 200:
        return ""
    return extract_description(resp.text, detail_selector)


def _fetch_description(
    job_url: str,
    *,
    render: bool,
    detail_selector: str | None,
    client=None,
    playwright_session: _PlaywrightSession | None = None,
) -> str:
    try:
        if render and _should_prefer_crawl4ai(True):
            html = _fetch_html_crawl4ai(job_url)
        elif render and playwright_session is not None:
            html = playwright_session.fetch(job_url)
        elif render:
            html = _fetch_html_playwright(job_url)
        elif client is not None:
            return _fetch_description_http(job_url, detail_selector, client)
        else:
            html = _fetch_html_http(job_url)
        return extract_description(html, detail_selector)
    except Exception:  # noqa: BLE001 - detail fetch is best-effort
        return ""


def fetch(entry: dict) -> list[RawJob]:
    url = entry["url"]
    render = bool(entry.get("render"))
    detail_limit = int(entry.get("detail_limit", 15))
    detail_selector = entry.get("detail_selector")

    if render and not _should_prefer_crawl4ai(render):
        with _PlaywrightSession() as session:
            html = session.fetch(url)
            fetched = 0

            def detail_fetcher(job_url: str) -> str:
                nonlocal fetched
                if fetched >= detail_limit:
                    return ""
                fetched += 1
                return _fetch_description(
                    job_url,
                    render=True,
                    detail_selector=detail_selector,
                    playwright_session=session,
                )

            return parse_job_listing(html, entry, source="careers_page", fetch_detail=detail_fetcher)

    html = _page_html(url, render)
    fetched = 0

    with http_client() as client:

        def detail_fetcher(job_url: str) -> str:
            nonlocal fetched
            if fetched >= detail_limit:
                return ""
            fetched += 1
            return _fetch_description(
                job_url,
                render=render,
                detail_selector=detail_selector,
                client=None if render else client,
            )

        return parse_job_listing(html, entry, source="careers_page", fetch_detail=detail_fetcher)
