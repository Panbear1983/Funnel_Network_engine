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


def fake_llm(messages, *, online=False, **_kw):
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

    # --- CJK names must NOT collapse into one entity (entity_slug guard) ----
    def cjk_llm(messages, *, online=False, **_kw):
        text = messages[-1]["content"]
        if "process stack" in text:
            return {"process_nodes": [
                {"name": "晶圓代工", "layer": "midstream", "keywords": [],
                 "bottleneck_risk": "high", "description": "", "rationale": ""},
            ]}
        if "Identify up to" in text:
            return {"companies": [
                {"name": "台積電", "ticker": None, "country": "TW",
                 "role": "operator", "confidence": 0.9, "sources": []},
                {"name": "聯發科", "ticker": None, "country": "TW",
                 "role": "operator", "confidence": 0.8, "sources": []},
            ]}
        if "Infer supplier" in text:
            return {"edges": [
                {"from": "台積電", "to": "聯發科", "relationship_type": "supplier",
                 "evidence_level": "inferred", "confidence": 0.5},
            ]}
        return {}

    r2 = repo.create_request("CJK value chain")
    r2.industry_of_interest = "半導體"
    repo.update(r2)
    run2 = run_pipeline(r2.id, "high_level", llm=cjk_llm,
                        gather=fake_gather, online_fallback=False)
    props2 = repo.list_proposals(r2.id, run_id=run2.id)
    comp_refs = {p.ref_key for p in props2 if p.entity_type == "company"}
    assert len(comp_refs) == 2 and "x" not in comp_refs, comp_refs
    counts2 = commit_proposals(r2.id, run_id=run2.id)
    comps2 = repo.list_for_request(CompanyNode, r2.id)
    edges2 = repo.list_for_request(RelationshipEdge, r2.id)
    assert len(comps2) == 2, [c.name for c in comps2]     # NOT merged into one
    assert len(edges2) == 1, "CJK edge endpoints must resolve"
    assert edges2[0].from_company != edges2[0].to_company
    # Re-run merges idempotently — no duplicates.
    run2b = run_pipeline(r2.id, "high_level", llm=cjk_llm,
                         gather=fake_gather, online_fallback=False)
    commit_proposals(r2.id, run_id=run2b.id)
    assert repo.count_for_request(CompanyNode, r2.id) == 2, "CJK re-run duplicated"

    # --- unscraped evidence on a non-web provider is marked model_knowledge --
    os.environ["FUNNEL_LLM_PROVIDER"] = "nvidia"   # no web-search fallback

    def recall_llm(messages, *, online=False, **_kw):
        text = messages[-1]["content"]
        if "process stack" in text:
            return {"process_nodes": [
                {"name": "Assembly", "layer": "midstream", "keywords": [],
                 "bottleneck_risk": "low", "description": "", "rationale": ""}]}
        if "Identify up to" in text:
            return {"companies": [
                {"name": "Foxconn", "ticker": "2317", "country": "TW",
                 "role": "operator", "confidence": 0.8, "sources": []}]}
        if "Extract factual claims" in text:
            return {"evidence": [
                {"claim": "Foxconn assembles the product", "source_url": "http://maybe-fake",
                 "publisher": "memory", "date": "2026", "related_companies": ["Foxconn"],
                 "source_type": "news", "evidence_strength": "strong", "quote": "..."}]}
        return {}

    r3 = repo.create_request("unscraped evidence")
    r3.industry_of_interest = "Widgets"
    repo.update(r3)
    run3 = run_pipeline(r3.id, "professional", llm=recall_llm,
                        gather=lambda q, k: ("", []), online_fallback=True)
    ev3 = [p for p in repo.list_proposals(r3.id, run_id=run3.id)
           if p.entity_type == "evidence"]
    assert ev3, "expected model-recalled evidence proposals"
    assert all(p.payload.get("provenance") == "model_knowledge" for p in ev3), ev3
    assert all(p.payload["evidence_strength"] == "weak" for p in ev3), (
        [p.payload["evidence_strength"] for p in ev3])          # strong capped
    assert any("model-recalled" in line for line in run3.log), run3.log
    commit_proposals(r3.id, run_id=run3.id)
    evs3 = repo.list_for_request(EvidenceItem, r3.id)
    assert all(e.provenance == "model_knowledge" for e in evs3), evs3
    # A scraped run stays "scraped".
    r4 = repo.create_request("scraped evidence")
    r4.industry_of_interest = "Widgets"
    repo.update(r4)
    run4 = run_pipeline(r4.id, "professional", llm=recall_llm,
                        gather=fake_gather, online_fallback=False)
    ev4 = [p for p in repo.list_proposals(r4.id, run_id=run4.id)
           if p.entity_type == "evidence"]
    assert ev4 and all(p.payload.get("provenance") == "scraped" for p in ev4), ev4
    assert all(p.payload["evidence_strength"] == "strong" for p in ev4)

    # --- cooperative cancel: set after the process stack -> partial staging --
    import threading
    from funnel.db.models import ResearchRunOutcome
    cancel_event = threading.Event()

    def cancelling_llm(messages, *, online=False, **_kw):
        result = fake_llm(messages, online=online)
        if "process stack" in messages[-1]["content"]:
            cancel_event.set()          # cancel lands before stage 3 begins
        return result

    r5 = repo.create_request("cancelled run")
    r5.industry_of_interest = "EV batteries"
    repo.update(r5)
    run5 = run_pipeline(r5.id, "professional", llm=cancelling_llm,
                        gather=fake_gather, online_fallback=False,
                        cancel=cancel_event)
    assert run5.status == ResearchRunOutcome.CANCELLED.value, run5.status
    props5 = repo.list_proposals(r5.id, run_id=run5.id)
    kinds5 = {p.entity_type for p in props5}
    assert "process_node" in kinds5, kinds5          # staged work kept
    assert "company" not in kinds5, kinds5           # stage 3 never ran
    assert "cancelled by user" in run5.summary, run5.summary

    print("RESEARCH_OK proposals=", {k: len(v) for k, v in by.items()},
          "committed=", counts, "-> nodes", len(nodes), "comps", len(comps),
          "edges", len(edges), "evidence", len(evs),
          "| CJK distinct:", sorted(comp_refs),
          "| provenance model_knowledge+scraped OK")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
