"""Canonical source-type hierarchy and tier helpers (no UI dependency).

Order = preferred source hierarchy, strongest first (docs/roadmap.md Phase 5).
"""

from __future__ import annotations

from typing import Optional

SOURCE_TYPE_ORDER = [
    "filings",
    "press_releases",
    "transcripts",
    "trade_data",
    "industry_pubs",
    "analyst_reports",
    "news",
    "other",
]

_BADGE = {
    "filings": "filing",
    "press_releases": "press",
    "transcripts": "transcript",
    "trade_data": "trade",
    "industry_pubs": "industry",
    "analyst_reports": "analyst",
    "news": "news",
    "other": "other",
}


def tier_badge(source_type: str) -> str:
    return _BADGE.get(source_type, source_type or "?")


def _rank(source_type: str) -> int:
    return SOURCE_TYPE_ORDER.index(source_type) if source_type in SOURCE_TYPE_ORDER else len(SOURCE_TYPE_ORDER)


def source_type_quality(source_type: str) -> float:
    """1.0 for the strongest tier (filings), descending toward 0 for weaker."""
    return round(1.0 - _rank(source_type) / len(SOURCE_TYPE_ORDER), 3)


def strongest_source(evidence_items) -> Optional[str]:
    if not evidence_items:
        return None
    best = min(evidence_items, key=lambda e: _rank(e.source_type))
    return tier_badge(best.source_type)
