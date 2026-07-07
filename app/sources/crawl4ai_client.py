"""HTTP client for an optional Crawl4AI Docker sidecar (REST API on port 11235).

See docs/CRAWL4AI.md for setup. When disabled, nothing in the app calls this module.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.sources.base import SourceError, http_client


def crawl4ai_settings() -> dict[str, Any]:
    return get_settings().get("crawl4ai") or {}


def is_enabled() -> bool:
    cfg = crawl4ai_settings()
    return bool(cfg.get("enabled")) and bool((cfg.get("base_url") or "").strip())


def base_url() -> str:
    return (crawl4ai_settings().get("base_url") or "http://127.0.0.1:11235").rstrip("/")


def timeout_seconds() -> float:
    return float(crawl4ai_settings().get("timeout_seconds") or 90)


def health_check() -> dict[str, Any]:
    """Ping Crawl4AI /health. Returns {ok, status, detail}."""
    if not is_enabled():
        return {"ok": False, "status": "disabled", "detail": "crawl4ai.enabled is false in settings.yaml"}
    try:
        with http_client(timeout=10) as client:
            resp = client.get(f"{base_url()}/health")
        if resp.status_code == 200:
            return {"ok": True, "status": "up", "detail": resp.text[:200]}
        return {"ok": False, "status": "error", "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "unreachable", "detail": str(exc)}


def fetch_html(
    url: str,
    *,
    delay_before_return_html: float | None = None,
    page_timeout_ms: int | None = None,
) -> str:
    """Render a URL via Crawl4AI and return HTML."""
    if not is_enabled():
        raise SourceError(
            "Crawl4AI is not enabled. Set crawl4ai.enabled: true and base_url in config/settings.yaml"
        )

    cfg = crawl4ai_settings()
    delay = delay_before_return_html if delay_before_return_html is not None else float(
        cfg.get("delay_before_return_html") or 2.0
    )
    page_timeout = page_timeout_ms if page_timeout_ms is not None else int(cfg.get("page_timeout_ms") or 60000)

    payload = {
        "urls": [url],
        "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {
                "cache_mode": "bypass",
                "delay_before_return_html": delay,
                "page_timeout": page_timeout,
            },
        },
    }

    with http_client(timeout=timeout_seconds()) as client:
        resp = client.post(f"{base_url()}/crawl", json=payload)

    if resp.status_code != 200:
        raise SourceError(f"Crawl4AI returned HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    results = data.get("results") or []
    if not results:
        raise SourceError("Crawl4AI returned no results")

    first = results[0]
    if not first.get("success"):
        msg = first.get("error_message") or first.get("error") or "unknown error"
        raise SourceError(f"Crawl4AI crawl failed: {msg}")

    html = first.get("html") or ""
    if not html.strip():
        raise SourceError("Crawl4AI returned empty HTML")
    return html
