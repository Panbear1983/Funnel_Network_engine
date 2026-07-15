"""Deterministic scoring — no LLM. Every score derives from fields already on
the entities, so results are reproducible and explainable."""

from __future__ import annotations

import re
from datetime import datetime

from funnel.sources import evidence_quality

RISK_SCORE = {"unknown": 0.3, "low": 0.2, "medium": 0.6, "high": 1.0}
EVIDENCE_LEVEL_SCORE = {"unknown": 0.0, "inferred": 0.33, "weak": 0.5, "confirmed": 1.0}

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def recency_factor(date_str: str, *, allow_old: bool = False, now_year: int | None = None) -> float:
    """Old evidence weighs lighter unless the request allows older sources.
    Tolerant year parse; undated evidence is neutral (1.0)."""
    if allow_old:
        return 1.0
    match = _YEAR_RE.search(date_str or "")
    if not match:
        return 1.0
    age = (now_year or datetime.now().year) - int(match.group(0))
    if age <= 1:
        return 1.0
    if age <= 3:
        return 0.9
    if age <= 6:
        return 0.75
    return 0.6


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def node_bottleneck_score(node, company_count: int) -> float:
    """Intrinsic bottleneck risk, raised when very few companies serve the node."""
    base = RISK_SCORE.get(node.bottleneck_risk, 0.3)
    scarcity = 0.2 if company_count <= 1 else (0.1 if company_count == 2 else 0.0)
    return round(clamp01(base + scarcity), 3)


def company_source_quality(evidence_items, *, allow_old: bool = False) -> float:
    """Best evidence quality: source-type tier x domain credibility x recency."""
    if not evidence_items:
        return 0.0
    return round(max(
        evidence_quality(e) * recency_factor(e.date, allow_old=allow_old)
        for e in evidence_items), 3)


def company_investment_relevance(company, node_bottleneck: dict, evidence_items, degree: int,
                                 *, allow_old: bool = False) -> float:
    """Blend of conviction, economics, bottleneck exposure, evidence and connectivity."""
    conf = company.confidence_score or 0.0
    rev = company.revenue_purity_estimate if company.revenue_purity_estimate is not None else 0.0
    share = company.market_share_estimate if company.market_share_estimate is not None else 0.0
    bottleneck_exposure = max(
        (node_bottleneck.get(nid, 0.0) for nid in company.process_node_ids), default=0.0
    )
    ev_quality = company_source_quality(evidence_items, allow_old=allow_old)
    ev_volume = clamp01(len(evidence_items) / 3.0)
    connectivity = clamp01(degree / 4.0)
    score = (
        0.25 * conf
        + 0.15 * rev
        + 0.15 * share
        + 0.20 * bottleneck_exposure
        + 0.15 * (ev_quality * ev_volume)
        + 0.10 * connectivity
    )
    return round(clamp01(score), 3)


def edge_strength(edge, deal_count: int, evidence_count: int) -> float:
    base = EVIDENCE_LEVEL_SCORE.get(edge.evidence_level, 0.0)
    conf = edge.confidence_score or 0.0
    boost = clamp01(0.1 * deal_count + 0.05 * evidence_count)
    return round(clamp01(0.6 * base + 0.25 * conf + 0.15 * boost), 3)
