"""Canonical subject identifiers used at the public bundle boundary.

Acquisition records deliberately keep their provider-facing underscore IDs.
Only derived editorial indexes and released bundles use these canonical IDs.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


CANONICAL_SUBJECT_LABELS = {
    "administrative-law": "行政法",
    "constitutional-law": "憲法",
    "civil-law": "民法",
    "commercial-law": "商法・会社法",
    "legal-foundations": "基礎法学",
    "general-knowledge": "基礎知識",
}

_SUBJECT_ALIASES = {
    "administrative-law": "administrative-law",
    "administrativelaw": "administrative-law",
    "行政法": "administrative-law",
    "行政法学": "administrative-law",
    "constitutional-law": "constitutional-law",
    "constitutionallaw": "constitutional-law",
    "憲法": "constitutional-law",
    "civil-law": "civil-law",
    "civillaw": "civil-law",
    "民法": "civil-law",
    "commercial-law": "commercial-law",
    "commerciallaw": "commercial-law",
    "company-law": "commercial-law",
    "companylaw": "commercial-law",
    "商法": "commercial-law",
    "会社法": "commercial-law",
    "商法・会社法": "commercial-law",
    "legal-foundations": "legal-foundations",
    "legalfoundations": "legal-foundations",
    "基礎法学": "legal-foundations",
    "general-knowledge": "general-knowledge",
    "generalknowledge": "general-knowledge",
    "basic-knowledge": "general-knowledge",
    "basicknowledge": "general-knowledge",
    "一般知識": "general-knowledge",
    "基礎知識": "general-knowledge",
}
_SEPARATORS = re.compile(r"[\s_]+")
_KEY_SEPARATORS = re.compile(r"[\s_\-・]+")


def canonical_subject_id(value: Any) -> str:
    """Return a stable public subject ID without mutating the source record."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return ""
    slug = re.sub(r"-+", "-", _SEPARATORS.sub("-", text.casefold())).strip("-")
    key = _KEY_SEPARATORS.sub("", text.casefold())
    return _SUBJECT_ALIASES.get(slug, _SUBJECT_ALIASES.get(key, slug))


def subject_label(subject_id: Any) -> str:
    """Return the display label for a supported canonical or raw subject ID."""

    return CANONICAL_SUBJECT_LABELS.get(canonical_subject_id(subject_id), "")
