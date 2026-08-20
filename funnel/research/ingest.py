"""Materialize accepted proposals into real graph entities, resolving the
ref-key cross-references the pipeline stored.

Commit is idempotent: re-running Generate + Build on the same request merges
into the existing graph instead of duplicating it — nodes/companies match by
slugified name, edges by (from, to, type), evidence by claim, deals by
(buyer, supplier, type). Merges update empty fields and only ever raise
confidence; manual edits are never blanked.
"""

from __future__ import annotations

import hashlib
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

# evidence_level ranking for upgrade-only merging
_EVIDENCE_RANK = {"unknown": 0, "inferred": 1, "weak": 2, "confirmed": 3}


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "x"


_ASCII_ALNUM = re.compile(r"[a-zA-Z0-9]")


def entity_slug(name: str) -> str:
    """Merge/ref key for entity names and claims. Identical to slugify() for
    any name containing ASCII — but a pure-non-ASCII name (e.g. CJK 台積電)
    would collapse to the degenerate "x" and silently merge DISTINCT entities,
    so those get a stable content-hash suffix instead. Deterministic, so
    idempotent re-run merging still works."""
    text = (name or "").strip()
    slug = slugify(text)
    if slug == "x" and text and not _ASCII_ALNUM.search(text):
        return "x_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return slug


def _fill(entity, field: str, value) -> bool:
    """Set a field only when the incoming value is truthy and the existing one
    is empty/unknown. Returns True when something changed."""
    if not value:
        return False
    current = getattr(entity, field)
    if current in (None, "", [], {}, "unknown"):
        setattr(entity, field, value)
        return True
    return False


def commit_proposals(request_id: str, run_id: str | None = None) -> dict:
    """Create or merge real entities from all non-rejected proposals, in
    dependency order. Returns counts by entity type plus 'merged'."""
    proposals = [
        p for p in repo.list_proposals(request_id, run_id=run_id)
        if p.status in _COMMITTABLE
    ]
    by_type: dict[str, list] = {}
    for p in proposals:
        by_type.setdefault(p.entity_type, []).append(p)

    # Existing graph, keyed the same way proposals are ref-keyed.
    existing_nodes = {entity_slug(n.name): n for n in repo.list_for_request(ProcessNode, request_id)}
    existing_companies = {entity_slug(c.name): c for c in repo.list_for_request(CompanyNode, request_id)}
    existing_edges = {(e.from_company, e.to_company, e.relationship_type): e
                      for e in repo.list_for_request(RelationshipEdge, request_id)}
    existing_evidence = {entity_slug(e.claim)[:40]: e
                         for e in repo.list_for_request(EvidenceItem, request_id)}
    existing_deals = {(d.buyer, d.supplier, d.deal_type): d
                      for d in repo.list_for_request(Deal, request_id)}

    node_map: dict[str, str] = {}     # ref_key -> ProcessNode id
    company_map: dict[str, str] = {}  # ref_key -> CompanyNode id
    counts = {"process_node": 0, "company": 0, "edge": 0, "evidence": 0,
              "deal": 0, "merged": 0}

    for p in by_type.get("process_node", []):
        d = p.payload
        node = existing_nodes.get(p.ref_key)
        if node is not None:
            _fill(node, "description", d.get("description"))
            _fill(node, "keywords", d.get("keywords"))
            _fill(node, "bottleneck_risk", d.get("bottleneck_risk"))
            node.confidence_score = max(node.confidence_score, d.get("confidence_score", 0.0))
            node = repo.update(node)
            counts["merged"] += 1
        else:
            node = repo.add(ProcessNode(
                request_id=request_id, name=d.get("name", ""), layer=d.get("layer", "midstream"),
                description=d.get("description", ""), keywords=d.get("keywords", []),
                bottleneck_risk=d.get("bottleneck_risk", "unknown"),
                confidence_score=d.get("confidence_score", 0.5),
            ))
            existing_nodes[p.ref_key] = node
            counts["process_node"] += 1
        node_map[p.ref_key] = node.id
        repo.set_proposal_status(p.id, "committed")

    for p in by_type.get("company", []):
        d = p.payload
        node_ids = [node_map[k] for k in d.get("process_node_ids", []) if k in node_map]
        company = existing_companies.get(p.ref_key)
        if company is not None:
            merged_ids = list(company.process_node_ids or [])
            merged_ids.extend(nid for nid in node_ids if nid not in merged_ids)
            company.process_node_ids = merged_ids
            _fill(company, "ticker", d.get("ticker"))
            _fill(company, "country", d.get("country"))
            company.confidence_score = max(company.confidence_score, d.get("confidence_score", 0.0))
            company = repo.update(company)
            counts["merged"] += 1
        else:
            company = repo.add(CompanyNode(
                request_id=request_id, name=d.get("name", ""), ticker=d.get("ticker") or None,
                country=d.get("country") or None, role=d.get("role", "operator"),
                process_node_ids=node_ids, confidence_score=d.get("confidence_score", 0.0),
            ))
            existing_companies[p.ref_key] = company
            counts["company"] += 1
        company_map[p.ref_key] = company.id
        repo.set_proposal_status(p.id, "committed")

    for p in by_type.get("edge", []):
        d = p.payload
        src = company_map.get(d.get("from_company"))
        dst = company_map.get(d.get("to_company"))
        if not src or not dst:
            repo.set_proposal_status(p.id, "skipped")
            continue
        rel_type = d.get("relationship_type", "supplier")
        edge = existing_edges.get((src, dst, rel_type))
        if edge is not None:
            incoming = d.get("evidence_level", "inferred")
            if _EVIDENCE_RANK.get(incoming, 0) > _EVIDENCE_RANK.get(edge.evidence_level, 0):
                edge.evidence_level = incoming
            edge.confidence_score = max(edge.confidence_score, d.get("confidence_score", 0.0))
            repo.update(edge)
            counts["merged"] += 1
        else:
            edge = repo.add(RelationshipEdge(
                request_id=request_id, from_company=src, to_company=dst,
                relationship_type=rel_type,
                evidence_level=d.get("evidence_level", "inferred"),
                confidence_score=d.get("confidence_score", 0.0),
            ))
            existing_edges[(src, dst, rel_type)] = edge
            counts["edge"] += 1
        repo.set_proposal_status(p.id, "committed")

    for p in by_type.get("evidence", []):
        d = p.payload
        related = [company_map[k] for k in d.get("related_companies", []) if k in company_map]
        ev = existing_evidence.get(entity_slug(d.get("claim", ""))[:40])
        if ev is not None:
            merged_rel = list(ev.related_companies or [])
            merged_rel.extend(cid for cid in related if cid not in merged_rel)
            ev.related_companies = merged_rel
            _fill(ev, "source_url", d.get("source_url"))
            _fill(ev, "publisher", d.get("publisher"))
            _fill(ev, "date", d.get("date"))
            repo.update(ev)
            counts["merged"] += 1
        else:
            ev = repo.add(EvidenceItem(
                request_id=request_id, source_type=d.get("source_type", "news"),
                source_url=d.get("source_url", ""), publisher=d.get("publisher", ""),
                date=d.get("date", ""), claim=d.get("claim", ""),
                extracted_quote=d.get("quote", ""), related_companies=related,
                evidence_strength=d.get("evidence_strength", "moderate"),
                provenance=d.get("provenance", ""),
                confidence_score=d.get("confidence_score", 0.0),
            ))
            existing_evidence[entity_slug(ev.claim)[:40]] = ev
            counts["evidence"] += 1
        repo.set_proposal_status(p.id, "committed")

    for p in by_type.get("deal", []):
        d = p.payload
        buyer = company_map.get(d.get("buyer"), "")
        supplier = company_map.get(d.get("supplier"), "")
        key = (buyer, supplier, d.get("deal_type", "partnership"))
        if key in existing_deals:
            counts["merged"] += 1
        else:
            deal = repo.add(Deal(
                request_id=request_id, deal_type=d.get("deal_type", "partnership"),
                buyer=buyer, supplier=supplier,
                product_or_service=d.get("product_or_service", ""),
                announced_date=d.get("announced_date", ""), deal_value=d.get("deal_value") or None,
                confidence_score=d.get("confidence_score", 0.0),
            ))
            existing_deals[key] = deal
            counts["deal"] += 1
        repo.set_proposal_status(p.id, "committed")

    return counts
