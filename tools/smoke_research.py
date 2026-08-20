"""Offline test for the research pipeline + ingest (no network, no keys).
Fake llm/gather return canned JSON. Run: `python tools/smoke_research.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_research_"))
# Keep the offline test deterministic: never auto-probe a real ISR sibling DB.
os.environ.setdefault("FUNNEL_ISR_DB", "")

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import (  # noqa: E402
    CompanyNode, EvidenceItem, ProcessNode, RelationshipEdge,
)
from funnel.research.ingest import commit_proposals  # noqa: E402
from funnel.research.pipeline import run_pipeline  # noqa: E402


def fake_llm(messages, *, online=False):
    text = messages[-1]["content"]
    if "process stack" in text:
        return {"process_nodes": [
            {"name": "Cell manufacturing", "layer": "midstream", "keywords": ["cell"],
             "bottleneck_risk": "medium", "description": "", "rationale": "core"},
            {"name": "Cathode materials", "layer": "upstream", "keywords": ["cathode"],
             "bottleneck_risk": "high", "description": "", "rationale": "scarce"},
        ]}
    if "Identify up to" in text:
        if "Cell manufacturing" in text:
            return {"companies": [
                {"name": "CATL", "ticker": "300750", "country": "CN", "role": "operator", "confidence": 0.9, "sources": ["http://s1"]},
                {"name": "LG Energy", "ticker": "373220", "country": "KR", "role": "operator", "confidence": 0.8, "sources": []},
            ]}
        return {"companies": [
            {"name": "Umicore", "ticker": "UMI", "country": "BE", "role": "materials_supplier", "confidence": 0.7, "sources": []},
            {"name": "CATL", "ticker": "300750", "country": "CN", "role": "operator", "confidence": 0.6, "sources": []},
        ]}
    if "Infer supplier" in text:
        return {"edges": [
            {"from": "Umicore", "to": "CATL", "relationship_type": "supplier", "evidence_level": "confirmed", "confidence": 0.8},
            {"from": "CATL", "to": "LG Energy", "relationship_type": "competitor", "evidence_level": "inferred", "confidence": 0.5},
        ]}
    if "Extract factual claims" in text:
        return {"evidence": [
            {"claim": "Umicore supplies cathode material to CATL", "source_url": "http://s2",
             "publisher": "IR filing", "date": "2026", "related_companies": ["Umicore", "CATL"],
             "source_type": "filings", "evidence_strength": "strong", "quote": "..."},
        ]}
    return {}


def fake_gather(query, k):
    return (f"SOURCE: http://s\nText about {query}.", ["http://s"])


def run():
    init_db()
    r = repo.create_request("EV Battery Supply Chain")
    r.industry_of_interest = "EV batteries"
    r.target_output = "Lithium-ion cells"
    r.geography = ["China"]
    r = repo.update(r)

    run = run_pipeline(r.id, "professional", llm=fake_llm, gather=fake_gather, online_fallback=False)
    assert run.status == "complete", run.status

    props = repo.list_proposals(r.id, run_id=run.id)
    by = {}
    for p in props:
        by.setdefault(p.entity_type, []).append(p)
    assert len(by["process_node"]) == 2, by
    assert len(by["company"]) == 3, [p.ref_key for p in by["company"]]  # CATL deduped
    assert len(by["edge"]) == 2, by["edge"]
    assert len(by.get("evidence", [])) == 1, by.get("evidence")

    catl = next(p for p in by["company"] if p.ref_key == "catl")
    assert len(catl.payload["process_node_ids"]) == 2, catl.payload  # linked to both nodes

    # reject LG Energy before committing
    lg = next(p for p in by["company"] if p.ref_key == "lg_energy")
    repo.set_proposal_status(lg.id, "rejected")

    counts = commit_proposals(r.id, run_id=run.id)
    nodes = repo.list_for_request(ProcessNode, r.id)
    comps = repo.list_for_request(CompanyNode, r.id)
    edges = repo.list_for_request(RelationshipEdge, r.id)
    evs = repo.list_for_request(EvidenceItem, r.id)

    assert len(nodes) == 2, len(nodes)
    assert len(comps) == 2, [c.name for c in comps]              # LG rejected
    catl_c = next(c for c in comps if c.name == "CATL")
    assert len(catl_c.process_node_ids) == 2, catl_c.process_node_ids
    assert len(edges) == 1, [(e.from_company, e.to_company) for e in edges]  # CATL->LG skipped
    assert len(evs) == 1 and len(evs[0].related_companies) == 2, evs

    print("RESEARCH_OK proposals=", {k: len(v) for k, v in by.items()},
          "committed=", counts, "-> nodes", len(nodes), "comps", len(comps),
          "edges", len(edges), "evidence", len(evs))


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
