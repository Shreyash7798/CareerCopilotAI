"""Duplicate detection.

Two layers:
1. A deterministic dedup key (company + normalized title + normalized
   location) enforced by a unique constraint in the jobs table.
2. A fuzzy pass that catches near-identical titles within the same company
   (e.g. "Senior Consultant - Operations" vs "Senior Consultant – Operations").
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

_ALNUM = re.compile(r"[^a-z0-9 ]+")

FUZZY_THRESHOLD = 0.93


def _canon(value: str) -> str:
    return re.sub(r"\s+", " ", _ALNUM.sub(" ", (value or "").lower())).strip()


def dedup_key(company: str, title: str, location: str) -> str:
    basis = f"{_canon(company)}|{_canon(title)}|{_canon(location)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def is_fuzzy_duplicate(title: str, existing_titles: list[str]) -> bool:
    canon = _canon(title)
    for other in existing_titles:
        if SequenceMatcher(None, canon, _canon(other)).ratio() >= FUZZY_THRESHOLD:
            return True
    return False
