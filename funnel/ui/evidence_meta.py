"""Source-type / strength vocabulary for the evidence forms. Tier logic lives in
funnel.sources; re-exported here so existing UI imports keep working."""

from __future__ import annotations

from funnel.sources import source_type_quality, strongest_source, tier_badge  # noqa: F401

# (label, value) — order matches funnel.sources.SOURCE_TYPE_ORDER (strongest first).
SOURCE_TYPES: list[tuple[str, str]] = [
    ("Company filing", "filings"),
    ("Press release", "press_releases"),
    ("Earnings transcript", "transcripts"),
    ("Trade / customs data", "trade_data"),
    ("Industry publication", "industry_pubs"),
    ("Analyst report", "analyst_reports"),
    ("News", "news"),
    ("Other", "other"),
]

STRENGTHS: list[tuple[str, str]] = [
    ("Strong", "strong"),
    ("Moderate", "moderate"),
    ("Weak", "weak"),
]
