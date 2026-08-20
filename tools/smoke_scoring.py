"""Offline test for the deterministic scoring fixes (no network).
Pins: (1) a contradiction-downgraded 'weak' edge scores BELOW a plain
'inferred' edge; (2) un-estimated purity/share default neutral (0.5) so
un-estimated companies aren't structurally punished; (3) LLM-emitted
confidence is capped at 0.9 at proposal time. Run: `python tools/smoke_scoring.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_scoring_"))
os.environ.setdefault("FUNNEL_ISR_DB", "")

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import CompanyNode, RelationshipEdge  # noqa: E402
from funnel.research.pipeline import run_pipeline  # noqa: E402
from funnel.scoring.scorer import (  # noqa: E402
    EVIDENCE_LEVEL_SCORE, company_investment_relevance, edge_strength,
)


def run() -> None:
    # (1) ordering: weak < inferred < confirmed
    assert EVIDENCE_LEVEL_SCORE["weak"] < EVIDENCE_LEVEL_SCORE["inferred"], \
        EVIDENCE_LEVEL_SCORE
    weak_edge = RelationshipEdge(request_id="r", from_company="a", to_company="b",
                                 relationship_type="supplier",
                                 evidence_level="weak", confidence_score=0.5)
    inferred_edge = RelationshipEdge(request_id="r", from_company="a", to_company="b",
                                     relationship_type="supplier",
                                     evidence_level="inferred", confidence_score=0.5)
    assert edge_strength(weak_edge, 0, 0) < edge_strength(inferred_edge, 0, 0), \
        "contradiction-downgraded edge must not outrank a plain inferred one"

    # (2) neutral default: an un-estimated company matches a 0.5/0.5 one,
    # and beats an explicitly poor (0.1/0.1) one.
    base = dict(request_id="r", name="X", role="operator",
                process_node_ids=[], confidence_score=0.6)
    unestimated = CompanyNode(**base)
    neutral = CompanyNode(**base, revenue_purity_estimate=0.5,
                          market_share_estimate=0.5)
    poor = CompanyNode(**base, revenue_purity_estimate=0.1,
                       market_share_estimate=0.1)
    kw = dict(node_bottleneck={}, evidence_items=[], degree=0)
    assert company_investment_relevance(unestimated, kw["node_bottleneck"], [], 0) == \
        company_investment_relevance(neutral, kw["node_bottleneck"], [], 0)
    assert company_investment_relevance(unestimated, kw["node_bottleneck"], [], 0) > \
        company_investment_relevance(poor, kw["node_bottleneck"], [], 0)

    # (3) confidence cap: a fake llm emitting 1.0 confidence stages <= 0.9.
    init_db()

    def cocky_llm(messages, *, online=False, **_kw):
        text = messages[-1]["content"]
        if "process stack" in text:
            return {"process_nodes": [
                {"name": "Assembly", "layer": "midstream", "keywords": [],
                 "bottleneck_risk": "low", "description": "", "rationale": ""}]}
        if "Identify up to" in text:
            return {"companies": [
                {"name": "Overconfident Corp", "ticker": None, "country": "US",
                 "role": "operator", "confidence": 1.0, "sources": []},
                {"name": "Sure Thing Inc", "ticker": None, "country": "US",
                 "role": "operator", "confidence": 0.99, "sources": []}]}
        if "Infer supplier" in text:
            return {"edges": [
                {"from": "Overconfident Corp", "to": "Sure Thing Inc",
                 "relationship_type": "supplier", "evidence_level": "confirmed",
                 "confidence": 1.0}]}
        return {}

    r = repo.create_request("confidence cap")
    r.industry_of_interest = "Widgets"
    repo.update(r)
    run_row = run_pipeline(r.id, "high_level", llm=cocky_llm,
                           gather=lambda q, k: (f"SOURCE: http://s\n{q}", ["http://s"]),
                           online_fallback=False)
    props = repo.list_proposals(r.id, run_id=run_row.id)
    for p in props:
        if p.entity_type in ("company", "edge"):
            assert p.payload["confidence_score"] <= 0.9, (p.entity_type, p.payload)
            assert p.confidence <= 0.9, p.confidence

    print("SCORING_OK weak<inferred ordering + neutral purity/share default + "
          "0.9 confidence cap")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
