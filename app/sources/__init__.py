"""Job source registry.

To add a new source type, create a module with a `fetch(entry) -> list[RawJob]`
function and register it here — nothing else in the core changes.
"""

from __future__ import annotations

from collections.abc import Callable

from app.sources import careers_page, greenhouse, lever, workday
from app.sources.base import RawJob, SourceError

REGISTRY: dict[str, Callable[[dict], list[RawJob]]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "workday": workday.fetch,
    "careers_page": careers_page.fetch,
}

__all__ = ["REGISTRY", "RawJob", "SourceError"]
