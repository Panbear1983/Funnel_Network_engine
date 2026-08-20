"""Offline test for Phase 5 — ISR relationship mining (no network, no keys).
A fake llm returns extracted counterparties covering every path: ISR-resolved
new company, foreign-ticker gloss, subject-named-itself (self-edge), no-ticker
drop, quote-guard drop, and competitor direction canonicalization. Asserts the
proposals commit into real edges/companies, dedup on re-run, and that a bad LLM
response degrades gracefully. Run: `python tools/smoke_isr_edges.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="funnel_isr_edges_")
os.environ.setdefault("FUNNEL_HOME", _TMP)

from isr_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(_TMP)
os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import CompanyNode, RelationshipEdge  # noqa: E402
from funnel.knowledge.isr import IsrKnowledge  # noqa: E402
from funnel.knowledge.isr_edges import run_isr_edge_mining  # noqa: E402

# Accton's fixture dossier: Clients "Meta、Amazon 等 CSP 客戶",
# Competitors "Cisco, Arista, Celestica", Core "…networking switches…".
EDGES = [
    # ISR-resolved counterparty (台積電 2330) -> new company keyed by ticker.
    {"counterparty_name": "台積電", "counterparty_ticker": "2330",
     "relationship_type": "customer", "direction": "subject_to_counterparty",
     "source_field": "clients", "confidence": 0.8, "quote": "Meta"},
    # Foreign gloss WITH ticker, not in ISR -> co-proposed company.
    {"counterparty_name": "Cisco", "counterparty_ticker": "CSCO",
     "relationship_type": "competitor", "direction": "subject_to_counterparty",
     "source_field": "competitors", "confidence": 0.6, "quote": "Cisco"},
    # Subject names itself (by ISR ticker) -> self-edge, dropped.
    {"counterparty_name": "Accton", "counterparty_ticker": "2345",
     "relationship_type": "partner", "direction": "subject_to_counterparty",
     "source_field": "clients", "confidence": 0.7, "quote": "Meta"},
    # No ticker, unresolvable -> dropped (never invent a CJK company).
    {"counterparty_name": "某新創", "counterparty_ticker": None,
     "relationship_type": "customer", "direction": "subject_to_counterparty",
     "source_field": "clients", "confidence": 0.5, "quote": "Amazon"},
    # Quote not present in the dossier text -> hallucination guard drops it.
    {"counterparty_name": "Ghost Corp", "counterparty_ticker": "GHST",
     "relationship_type": "competitor", "direction": "subject_to_counterparty",
     "source_field": "competitors", "confidence": 0.9,
     "quote": "this text is not in the dossier"},
    # Empty quote -> no textual support -> also dropped.
    {"counterparty_name": "EmptyQuote Co", "counterparty_ticker": "EMPT",
     "relationship_type": "competitor", "direction": "subject_to_counterparty",
     "source_field": "competitors", "confidence": 0.5, "quote": ""},
]


def fake_llm(messages, *, online=False, **_kw):
    assert "counterparty_name" in messages[-1]["content"], "wrong prompt"
    return {"edges": EDGES}


def run() -> None:
    init_db()
    isr = IsrKnowledge.open_default()

    r = repo.create_request("AI networking supply chain")
    subject = repo.add(CompanyNode(
        request_id=r.id, name="Accton", ticker="2345.TW", country="Taiwan",
        role="operator", confidence_score=0.9))

    before_c = repo.count_for_request(CompanyNode, r.id)
    result = run_isr_edge_mining(r.id, subject.id, llm=fake_llm, isr=isr)
    assert result["status"] == "ok", result

    comps = repo.list_for_request(CompanyNode, r.id)
    edges = repo.list_for_request(RelationshipEdge, r.id)
    names = {c.name for c in comps}
    id_by_name = {c.name: c.id for c in comps}

    # Two real counterparties created (台積電, Cisco); self/no-ticker/bad-quote dropped.
    assert "台積電 (2330)" in names, names
    assert "Cisco (CSCO)" in names, names
    assert "某新創" not in " ".join(names), names          # no-ticker dropped
    assert "Ghost" not in " ".join(names), names           # bad-quote dropped
    assert "EmptyQuote" not in " ".join(names), names      # empty-quote dropped
    assert repo.count_for_request(CompanyNode, r.id) == before_c + 2, names

    # Exactly two edges (customer to TSMC, competitor vs Cisco); no self-loop.
    assert len(edges) == 2, [(e.from_company, e.to_company, e.relationship_type) for e in edges]
    assert not any(e.from_company == e.to_company for e in edges), "self-loop leaked"
    subj_id = subject.id
    cust = next(e for e in edges if e.relationship_type == "customer")
    assert cust.from_company == subj_id                    # subject supplies TSMC
    assert cust.to_company == id_by_name["台積電 (2330)"]
    comp = next(e for e in edges if e.relationship_type == "competitor")
    assert {comp.from_company, comp.to_company} == {subj_id, id_by_name["Cisco (CSCO)"]}
    assert comp.evidence_level == "inferred"
    assert comp.confidence_score <= 0.5                    # capped
    # sources/provenance tag on the created counterparty
    cisco = next(c for c in comps if c.name == "Cisco (CSCO)")
    assert cisco.ticker == "CSCO", cisco.ticker

    # Idempotent: mining again merges, adds no duplicate edges/companies.
    result2 = run_isr_edge_mining(r.id, subject.id, llm=fake_llm, isr=isr)
    assert result2["status"] == "ok", result2
    assert repo.count_for_request(RelationshipEdge, r.id) == 2, "edges duplicated"
    assert repo.count_for_request(CompanyNode, r.id) == before_c + 2, "companies duplicated"

    # Competitor direction is canonical (sorted refs) -> stable across re-runs.
    edges2 = repo.list_for_request(RelationshipEdge, r.id)
    comp2 = next(e for e in edges2 if e.relationship_type == "competitor")
    assert (comp2.from_company, comp2.to_company) == (comp.from_company, comp.to_company)

    # A company with no ISR dossier can't be mined (graceful).
    plain = repo.add(CompanyNode(request_id=r.id, name="Nobody Inc",
                                 ticker="ZZZZ", confidence_score=0.5))
    assert run_isr_edge_mining(r.id, plain.id, llm=fake_llm, isr=isr)["status"] == "no_dossier"

    # A malformed LLM response fails gracefully, commits nothing new.
    def bad_llm(messages, *, online=False, **_kw):
        raise ValueError("truncated JSON")
    edges_before = repo.count_for_request(RelationshipEdge, r.id)
    assert run_isr_edge_mining(r.id, subject.id, llm=bad_llm, isr=isr)["status"] == "failed"
    assert repo.count_for_request(RelationshipEdge, r.id) == edges_before

    # Disabled connector -> no_isr, nothing committed.
    assert run_isr_edge_mining(r.id, subject.id, llm=fake_llm, isr=None) if os.environ.get("FUNNEL_ISR_DB") else True
    os.environ["FUNNEL_ISR_DB"] = ""
    import funnel.knowledge.isr as isr_mod
    isr_mod._cache = None
    assert run_isr_edge_mining(r.id, subject.id, llm=fake_llm)["status"] == "no_isr"
    os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

    print("ISR_EDGES_OK resolve+coprop+self-skip+noticket-drop+quote-guard+"
          "canonical-competitor+idempotent+graceful")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
