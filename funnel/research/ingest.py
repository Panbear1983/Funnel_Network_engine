"""Materialize accepted proposals into real graph entities, resolving the
ref-key cross-references the pipeline stored."""

from __future__ import annotations

import re

from funnel.db import repo
from funnel.db.models import (
    CompanyNode,
    Deal,
    EvidenceItem,
    ProcessNode,
    RelationshipEdge,
)

_COMMITTABLE = ("pending", "accepted")


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "x"


def commit_proposals(request_id: str, run_id: str | None = None) -> dict:
    """Create real entities from all non-rejected proposals, in dependency order.
    Returns counts by entity type."""
    proposals = [
        p for p in repo.list_proposals(request_id, run_id=run_id)
        if p.status in _COMMITTABLE
    ]
    by_type: dict[str, list] = {}
    for p in proposals:
        by_type.setdefault(p.entity_type, []).append(p)

    node_map: dict[str, str] = {}     # ref_key -> ProcessNode id
    company_map: dict[str, str] = {}  # ref_key -> CompanyNode id
    counts = {"process_node": 0, "company": 0, "edge": 0, "evidence": 0, "deal": 0}

    for p in by_type.get("process_node", []):
        d = p.payload
        node = repo.add(ProcessNode(
            request_id=request_id, name=d.get("name", ""), layer=d.get("layer", "midstream"),
            description=d.get("description", ""), keywords=d.get("keywords", []),
            bottleneck_risk=d.get("bottleneck_risk", "unknown"),
            confidence_score=d.get("confidence_score", 0.5),
        ))
        node_map[p.ref_key] = node.id
        repo.set_proposal_status(p.id, "committed")
        counts["process_node"] += 1

    for p in by_type.get("company", []):
        d = p.payload
        node_ids = [node_map[k] for k in d.get("process_node_ids", []) if k in node_map]
        company = repo.add(CompanyNode(
            request_id=request_id, name=d.get("name", ""), ticker=d.get("ticker") or None,
            country=d.get("country") or None, role=d.get("role", "operator"),
            process_node_ids=node_ids, confidence_score=d.get("confidence_score", 0.0),
        ))
        company_map[p.ref_key] = company.id
        repo.set_proposal_status(p.id, "committed")
        counts["company"] += 1

    for p in by_type.get("edge", []):
        d = p.payload
        src = company_map.get(d.get("from_company"))
        dst = company_map.get(d.get("to_company"))
        if not src or not dst:
            repo.set_proposal_status(p.id, "skipped")
            continue
        repo.add(RelationshipEdge(
            request_id=request_id, from_company=src, to_company=dst,
            relationship_type=d.get("relationship_type", "supplier"),
            evidence_level=d.get("evidence_level", "inferred"),
            confidence_score=d.get("confidence_score", 0.0),
        ))
        repo.set_proposal_status(p.id, "committed")
        counts["edge"] += 1

    for p in by_type.get("evidence", []):
        d = p.payload
        related = [company_map[k] for k in d.get("related_companies", []) if k in company_map]
        repo.add(EvidenceItem(
            request_id=request_id, source_type=d.get("source_type", "news"),
            source_url=d.get("source_url", ""), publisher=d.get("publisher", ""),
            date=d.get("date", ""), claim=d.get("claim", ""),
            extracted_quote=d.get("quote", ""), related_companies=related,
            evidence_strength=d.get("evidence_strength", "moderate"),
            confidence_score=d.get("confidence_score", 0.0),
        ))
        repo.set_proposal_status(p.id, "committed")
        counts["evidence"] += 1

    for p in by_type.get("deal", []):
        d = p.payload
        repo.add(Deal(
            request_id=request_id, deal_type=d.get("deal_type", "partnership"),
            buyer=company_map.get(d.get("buyer"), ""), supplier=company_map.get(d.get("supplier"), ""),
            product_or_service=d.get("product_or_service", ""),
            announced_date=d.get("announced_date", ""), deal_value=d.get("deal_value") or None,
            confidence_score=d.get("confidence_score", 0.0),
        ))
        repo.set_proposal_status(p.id, "committed")
        counts["deal"] += 1

    return counts
