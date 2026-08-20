"""Offline test for ISR grounding in the research pipeline + enrichment
(no network, no keys). A fixture ISR DB plus fake llm/gather prove:

1. discovery prompts carry the grounded ISR candidate block;
2. ISR-matched companies get empty ticker/country auto-filled + isr: source;
3. an ISR member whose name looks like junk survives the screen;
4. enrich_companies stores financials['isr'] and yfinance data merges with it;
5. with FUNNEL_ISR_DB="" behavior is identical to today (no ISR anywhere).

Run: `python tools/smoke_isr_grounding.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="funnel_isr_grounding_")
os.environ.setdefault("FUNNEL_HOME", _TMP)

from isr_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(_TMP)
os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import CompanyNode  # noqa: E402
from funnel.research import enrich as enrich_mod  # noqa: E402
from funnel.research import screen  # noqa: E402
from funnel.research.ingest import commit_proposals  # noqa: E402
from funnel.research.pipeline import run_pipeline  # noqa: E402

SEEN_PROMPTS: list[str] = []


def fake_llm(messages, *, online=False):
    text = messages[-1]["content"]
    SEEN_PROMPTS.append(text)
    if "process stack" in text:
        return {"process_nodes": [
            {"name": "Switch manufacturing", "layer": "midstream",
             "keywords": ["networking", "switches"],
             "bottleneck_risk": "medium", "description": "", "rationale": ""},
        ]}
    if "Identify up to" in text:
        # high_level tier caps at 3 companies per node — keep exactly 3.
        return {"companies": [
            # ISR member emitted without ticker/country -> must be grounded.
            {"name": "Accton", "ticker": None, "country": None,
             "role": "operator", "confidence": 0.8, "sources": []},
            # Real junk, not in ISR -> must still be rejected.
            {"name": "Multiple suppliers", "ticker": None, "country": None,
             "role": "supplier", "confidence": 0.4, "sources": []},
            # Not in ISR -> untouched.
            {"name": "Unknown Widgets Ltd", "ticker": None, "country": None,
             "role": "supplier", "confidence": 0.6, "sources": []},
        ]}
    return {}


def fake_gather(query, k):
    return (f"SOURCE: http://s\nText about {query}.", ["http://s"])


def _run_pipeline_once(title: str):
    r = repo.create_request(title)
    r.industry_of_interest = "Networking hardware"
    r.target_output = "Data-center switches"
    r = repo.update(r)
    run = run_pipeline(r.id, "high_level", llm=fake_llm,
                       gather=fake_gather, online_fallback=False)
    assert run.status in ("complete", "partial"), run.status
    return r, run


def run() -> None:
    init_db()

    # Trick the fixture: give "Various Semiconductor Makers" an ISR identity by
    # matching it via name? It is not in the fixture — instead we verify the
    # isr_member shield directly, then verify pipeline behavior with real
    # fixture members.
    assert screen.screen_company({"name": "Various Semiconductor Makers"}) is not None
    assert screen.screen_company({"name": "Various Semiconductor Makers"},
                                 isr_member=True) is None

    # --- pipeline with ISR connected ------------------------------------
    r, run_row = _run_pipeline_once("ISR grounding smoke")

    discovery = next(p for p in SEEN_PROMPTS if "Identify up to" in p)
    assert "private research database" in discovery, discovery[:400]
    assert "Accton (2345.TW)" in discovery, discovery[:400]

    props = repo.list_proposals(r.id, run_id=run_row.id)
    companies = {p.ref_key: p for p in props if p.entity_type == "company"}

    accton = companies["accton"]
    assert accton.status == "pending"
    assert accton.payload["ticker"] == "2345.TW", accton.payload
    assert accton.payload["country"] == "Taiwan", accton.payload
    assert "isr:2345" in accton.sources, accton.sources

    junk = companies["multiple_suppliers"]
    assert junk.status == "rejected", junk.status          # real junk still dies
    untouched = companies["unknown_widgets_ltd"]
    assert untouched.payload["ticker"] is None, untouched.payload
    assert not any(s.startswith("isr:") for s in untouched.sources)

    note_text = " ".join(run_row.log)
    assert "ISR knowledge base connected" in note_text, run_row.log
    assert "grounded in the ISR knowledge base" in note_text, run_row.log

    # --- enrichment: ISR pass 0 + yfinance merge --------------------------
    commit_proposals(r.id, run_id=run_row.id)
    fetched: list[str] = []

    def fake_fetch(ticker):
        fetched.append(ticker)
        return {"market_cap": 1e9, "symbol": ticker}

    counts = enrich_mod.enrich_companies(r.id, llm=None, fetch=fake_fetch)
    assert counts["isr"] >= 1, counts
    comps = repo.list_for_request(CompanyNode, r.id)
    accton_c = next(c for c in comps if c.name == "Accton")
    isr_blob = accton_c.financials.get("isr")
    assert isr_blob and isr_blob["tier"] == "leader", accton_c.financials
    assert "Clients & Orders" in isr_blob["dossier"], isr_blob["dossier"].keys()
    assert isr_blob["dossier_updated_at"] == "2026-08-10"
    assert accton_c.exchange == "TWSE", accton_c.exchange
    # yfinance ran for the ISR-filled ticker and merged, not clobbered.
    assert "2345.TW" in fetched, fetched
    assert accton_c.financials.get("market_cap") == 1e9
    assert accton_c.financials.get("isr") is not None
    # Re-run: write-once — no double counting.
    counts2 = enrich_mod.enrich_companies(r.id, llm=None, fetch=fake_fetch)
    assert counts2["isr"] == 0, counts2

    # --- disabled: byte-identical baseline behavior -------------------------
    os.environ["FUNNEL_ISR_DB"] = ""
    SEEN_PROMPTS.clear()
    r2, run2 = _run_pipeline_once("ISR disabled smoke")
    discovery2 = next(p for p in SEEN_PROMPTS if "Identify up to" in p)
    assert "private research database" not in discovery2
    props2 = repo.list_proposals(r2.id, run_id=run2.id)
    companies2 = {p.ref_key: p for p in props2 if p.entity_type == "company"}
    assert companies2["accton"].payload["ticker"] is None
    assert not any(s.startswith("isr:") for s in companies2["accton"].sources)
    log2 = " ".join(run2.log)
    assert "ISR knowledge base" not in log2, run2.log
    assert "grounded" not in log2, run2.log

    os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)
    print("ISR_GROUNDING_OK grounded prompt + autofill + screen shield + "
          "enrich pass 0 + disabled parity")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
