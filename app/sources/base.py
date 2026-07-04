"""Base contract for job sources.

Each source is an independent module that turns a config entry from
config/sources.yaml into a list of RawJob objects. A failure in one source
never affects the others (the pipeline isolates exceptions per source).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CareerCopilotAI/1.0"
)


@dataclass
class RawJob:
    company: str
    title: str
    location: str = ""
    description: str = ""
    url: str = ""
    source: str = ""
    external_id: str = ""
    posted_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def http_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"},
        follow_redirects=True,
    )


class SourceError(Exception):
    """Raised when a source fails; caught per-source by the pipeline."""
