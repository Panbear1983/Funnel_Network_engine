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


# --- URL-domain credibility (Phase D) --------------------------------------
# Registered-domain suffix -> credibility. Small and opinionated: regulators/
# filings > wire services & majors > trade press > vendor PR wires > forums.
_DOMAIN_TIERS = {
    1.0: ("sec.gov", "europa.eu", "sedarplus.ca", "edinet-fsa.go.jp"),
    0.9: ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "apnews.com",
          "nikkei.com", "economist.com"),
    0.7: ("semi.org", "eetimes.com", "digitimes.com", "electronicsweekly.com",
          "spglobal.com", "gartner.com", "idc.com", "trendforce.com"),
    0.4: ("prnewswire.com", "businesswire.com", "globenewswire.com",
          "accesswire.com"),
    0.2: ("reddit.com", "quora.com", "medium.com", "substack.com",
          "blogspot.com", "wordpress.com"),
}


def domain_quality(url: str) -> Optional[float]:
    """Credibility of a source URL's domain; None when unknown (no signal)."""
    if not url:
        return None
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
    except ValueError:
        return None
    host = host.removeprefix("www.")
    if not host:
        return None
    if host.endswith(".gov") or ".gov." in host:
        return 1.0
    for score, domains in _DOMAIN_TIERS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return score
    return None


def evidence_quality(item) -> float:
    """Blended quality of one evidence item: its source *type* tier, adjusted
    by the URL's domain credibility when the domain is recognized."""
    base = source_type_quality(item.source_type)
    dq = domain_quality(getattr(item, "source_url", "") or "")
    if dq is None:
        return base
    return round(0.6 * base + 0.4 * dq, 3)


def strongest_source(evidence_items) -> Optional[str]:
    if not evidence_items:
        return None
    best = min(evidence_items, key=lambda e: _rank(e.source_type))
    return tier_badge(best.source_type)
