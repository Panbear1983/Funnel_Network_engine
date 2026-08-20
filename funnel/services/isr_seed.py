"""Offline 'seed from ISR' — build a value-chain map from the ISR knowledge
base with NO LLM call. The user picks a theme (industry / tier); the connector
hands over the already-researched companies, grouped into tier nodes, with
their dossiers attached. This is the low-input, offline path: a real map exists
before any paid research runs, and the normal LLM pipeline (or Phase-5 edge
mining) can enrich it afterwards.

Keying, to survive the CJK / slugify hazards commit_proposals imposes:
- Company ref_key = slugify(label) where label = "Name (TICKER)" — the ticker
  makes the slug non-empty and unique even for a pure-CJK name, and it matches
  exactly how commit_proposals dedups existing companies (by slugify(name)), so
  re-seeding merges instead of duplicating.
- Node names are the canonical ASCII tier labels (never raw CJK), so their
  slugs are meaningful and stable across re-runs.
"""

from __future__ import annotations

from typing import Callable, Optional

from funnel.db import repo
from funnel.db.models import CompanyNode, Proposal, ResearchRun
from funnel.knowledge.isr import IsrKnowledge, get_isr
from funnel.research.enrich import enrich_from_isr
from funnel.research.ingest import commit_proposals, slugify
from funnel.services.build import export_map

Progress = Callable[[str], None]

# canonical tier -> (display node label, a plausible value-chain layer)
_TIER_NODE = {
    "leader": ("Leaders (ISR)", "downstream"),
    "second_leader": ("Second-tier leaders (ISR)", "downstream"),
    "hidden_champion": ("Hidden champions (ISR)", "midstream"),
    "potential": ("Potential (ISR)", "midstream"),
    "supporting": ("Supporting (ISR)", "upstream"),
    "minor_supplier": ("Minor suppliers (ISR)", "upstream"),
    "unknown": ("Other (ISR)", "custom"),
}


def _seed_identity(c) -> tuple[str, str]:
    """(display name, ref_key) for a seeded company. A ticker in the name makes
    the slug non-empty and unique; a tickerless pure-CJK name would otherwise
    slugify to 'x' and collapse distinct companies together, so it is
    disambiguated with the ISR company id."""
    if c.yahoo_ticker or c.base_ticker:
        name = c.label                     # "Name (TICKER)"
    else:
        name = f"{c.name} (ISR#{c.company_id})"
    return name, slugify(name)


def seed_proposals(companies: list) -> list[dict]:
    """Pure: turn a list of IsrCompany into process-node + company proposal
    dicts (grouped by tier). No DB writes."""
    props: list[dict] = []
    seen_nodes: set[str] = set()
    for c in companies:
        label, layer = _TIER_NODE.get(c.tier, _TIER_NODE["unknown"])
        nref = slugify(label)
        if nref not in seen_nodes:
            seen_nodes.add(nref)
            props.append({
                "entity_type": "process_node", "ref_key": nref,
                "payload": {
                    "name": label, "layer": layer,
                    "description": "Companies grouped from the ISR knowledge "
                                   "base by tier.",
                    "keywords": [], "bottleneck_risk": "unknown",
                    "confidence_score": 0.5},
                "rationale": "", "confidence": 0.5, "sources": [],
            })
    for c in companies:
        label, _layer = _TIER_NODE.get(c.tier, _TIER_NODE["unknown"])
        nref = slugify(label)
        cname, cref = _seed_identity(c)       # ASCII-safe even for tickerless CJK
        props.append({
            "entity_type": "company", "ref_key": cref,
            "payload": {
                "name": cname, "ticker": c.yahoo_ticker or None,
                "country": c.country or None, "role": "operator",
                "process_node_ids": [nref], "confidence_score": 0.5},
            "rationale": "", "confidence": 0.5,
            "sources": [f"isr:{c.base_ticker or c.name}"],
        })
    return props


def run_isr_seed(
    request_id: str,
    *,
    industry: Optional[str] = None,
    tier: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = 40,
    isr: Optional[IsrKnowledge] = None,
    export: bool = True,
    progress: Optional[Progress] = None,
) -> dict:
    """Seed a request's map from an ISR theme (offline, no LLM). Commits the
    proposals, attaches dossiers, and optionally exports the tree charts.
    Returns {status, ...counts[, charts, report]}."""
    def note(msg: str) -> None:
        if progress:
            progress(msg)

    isr = isr or get_isr()
    if isr is None:
        return {"status": "no_isr", "company": 0, "process_node": 0}
    companies = isr.by_theme(industry=industry, tier=tier, country=country,
                             limit=limit)
    if not companies:
        return {"status": "empty", "company": 0, "process_node": 0}

    note(f"Seeding {len(companies)} companies from the ISR knowledge base…")
    props = seed_proposals(companies)
    run = repo.add(ResearchRun(request_id=request_id, tier="isr-seed",
                               model="isr-seed", status="complete"))
    for pr in props:
        repo.add(Proposal(
            run_id=run.id, request_id=request_id, entity_type=pr["entity_type"],
            ref_key=pr["ref_key"], payload=pr["payload"],
            rationale=pr["rationale"], confidence=pr["confidence"],
            sources=pr["sources"], status="pending"))
    counts = commit_proposals(request_id, run_id=run.id)

    # Attach dossiers offline (never the networked yfinance/estimates passes).
    committed = repo.list_for_request(CompanyNode, request_id)
    enrich_from_isr(committed, isr, progress=progress)

    result = {"status": "ok", **counts}
    if export:
        note("Exporting tree charts…")
        result.update(export_map(request_id, progress=progress))
    return result
