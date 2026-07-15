"""Offline test for the one-topic flow + smart search (no network, no keys).
Covers: auto-brief → intake answers/request fields, intent-phrased queries,
alias retry on thin nodes, URL dedup, finalize → 3 tree charts + report.
Run: `python tools/smoke_flow.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_flow_"))
# Pin the provider so a repo .env (e.g. FUNNEL_LLM_PROVIDER=openrouter) can't
# change what this offline test asserts.
os.environ["FUNNEL_LLM_PROVIDER"] = "anthropic"

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import (  # noqa: E402
    CompanyNode, ProcessNode, ResearchRequest, VisualizationExport,
)
from funnel.research import flow  # noqa: E402
from funnel.research.smartsearch import INTENT_SUFFIX, PlannedQuery, SmartGather  # noqa: E402

SEEN_QUERIES: list[str] = []
EV_CALLS: list[int] = []


def fake_llm(messages, *, online=False):
    t = messages[-1]["content"]
    if "Propose a research brief" in t:
        return {"industry": "Espresso machines", "target_output": "Prosumer espresso machines",
                "geography": ["Italy", "USA"], "relationship_types": ["supplier", "customer"],
                "company_origin": "both", "goal": "ecosystem_understanding",
                "research_depth": "standard", "source_strictness": "balanced",
                "known_terms": ["portafilter"], "aliases": ["coffee machines"],
                "jargon": ["E61 group"], "key_products": ["dual boiler machines"]}
    if "value-chain process stack" in t:
        return {"process_nodes": [
            {"name": "Boiler manufacturing", "layer": "upstream", "keywords": [],
             "bottleneck_risk": "medium", "description": "", "rationale": ""},
            {"name": "Machine assembly", "layer": "midstream", "keywords": [],
             "bottleneck_risk": "low", "description": "", "rationale": ""}]}
    if '"plans"' in t:
        assert "coffee machines" in t, "profile vocabulary missing from search-plan prompt"
        return {"plans": [
            {"node": "Boiler manufacturing",
             "queries": [{"q": "espresso boiler suppliers", "intent": "market"}]},
            {"node": "Machine assembly",
             "queries": [{"q": "espresso machine makers", "intent": "filings"}]}]}
    if "Identify up to" in t:
        if "Boiler manufacturing" in t:
            return {"companies": [
                {"name": "IMS Boilers", "ticker": None, "country": "IT",
                 "role": "supplier", "confidence": 0.7, "sources": []},
                {"name": "Italy", "ticker": None, "country": "IT",
                 "role": "supplier", "confidence": 0.9, "sources": []}]}
        return {"companies": [{"name": "La Marzocco", "ticker": None, "country": "IT",
                               "role": "operator", "confidence": 0.9, "sources": []}]}
    if "Infer supplier" in t:
        return {"edges": [{"from": "IMS Boilers", "to": "La Marzocco",
                           "relationship_type": "supplier", "evidence_level": "confirmed",
                           "confidence": 0.8}]}
    if "Extract factual claims" in t:
        EV_CALLS.append(1)
        if len(EV_CALLS) == 1:
            # Stage-5 pass: evidence that does NOT cover the confirmed edge,
            # so Stage 5b must hunt for it.
            return {"evidence": [{"claim": "La Marzocco leads the prosumer espresso market",
                                  "source_url": "http://ev1", "publisher": "Trade pub",
                                  "date": "2026-05", "related_companies": ["La Marzocco"],
                                  "source_type": "industry_pubs",
                                  "evidence_strength": "moderate", "quote": "…"}]}
        # Targeted (5b) pass: covers the IMS→La Marzocco confirmed edge.
        return {"evidence": [{"claim": "IMS Boilers supplies boilers to La Marzocco",
                              "source_url": "http://ev2", "publisher": "Press release",
                              "date": "2026-06",
                              "related_companies": ["IMS Boilers", "La Marzocco"],
                              "source_type": "press_releases",
                              "evidence_strength": "strong", "quote": "…"}]}
    return {}


def fake_gather(query, k):
    SEEN_QUERIES.append(query)
    # First node's query returns THIN text to trigger the alias retry;
    # every call returns one repeated URL to exercise cross-node dedup.
    if "boiler suppliers" in query:
        return ("thin", ["http://dup", "http://boilers"])
    return ("SOURCE: http://x\n" + ("solid content " * 40), ["http://dup", "http://" + query[:10]])


def run() -> None:
    init_db()

    # --- unit: SmartGather mechanics -------------------------------------
    sg = SmartGather(lambda q, k: ("txt", ["http://a", "http://b"]))
    _, urls1 = sg.gather_node("N1", [PlannedQuery("q1")], 2, [])
    _, urls2 = sg.gather_node("N2", [PlannedQuery("q2", intent="news")], 2, [])
    assert urls1 == ["http://a", "http://b"] and urls2 == [], (urls1, urls2)  # dedup

    caught: list[str] = []
    sg2 = SmartGather(lambda q, k: (caught.append(q) or "t", []), recent_year=1999)
    sg2.gather_query("boiler suppliers", 1)
    assert caught == ["boiler suppliers 1999"], caught  # recency suffix

    # --- unit: junk screen -------------------------------------------------
    from funnel.research.screen import screen_company
    assert screen_company({"name": "China"}) is not None
    assert screen_company({"name": "China (collective industry)"}) is not None
    assert screen_company({"name": "Various suppliers"}) is not None
    assert screen_company({"name": "The semiconductor industry"}) is not None
    assert screen_company({"name": ""}) is not None
    assert screen_company({"name": "Harmonic Drive Systems"}) is None
    assert screen_company({"name": "Mitsubishi Heavy Industries"}) is None
    assert screen_company({"name": "Taiwan Semiconductor Manufacturing"}) is None
    print("SCREEN_OK junk filter: places/collectives/placeholders rejected, real names pass")

    # --- full flow --------------------------------------------------------
    request_id = repo.create_request("Espresso machines").id
    run_row = flow.run_research(request_id, "professional",
                                llm=fake_llm, gather=fake_gather,
                                online_fallback=False)
    assert run_row.status == "complete", run_row.summary

    req = repo.get(ResearchRequest, request_id)
    assert req.industry_of_interest == "Espresso machines"
    assert req.geography == ["Italy", "USA"]
    answers = repo.get_intake_answers(request_id)
    assert answers["target_output"].answer_value == "Prosumer espresso machines"
    assert answers["known_terms"].answer_value == "portafilter"

    # smart search: intent suffix applied + alias retry fired for the thin node
    assert any(INTENT_SUFFIX["filings"] in q for q in SEEN_QUERIES), SEEN_QUERIES
    assert any("coffee machines" in q for q in SEEN_QUERIES), \
        ("alias retry did not fire", SEEN_QUERIES)

    # Stage 5b: the confirmed edge lacked evidence after stage 5, so a
    # targeted, recency-biased hunt query fired and its evidence was staged.
    from datetime import datetime as _dt
    year = str(_dt.now().year)
    hunt = [q for q in SEEN_QUERIES if "supply agreement partnership contract" in q]
    assert hunt and all(year in q for q in hunt), ("edge hunt missing", SEEN_QUERIES)
    from funnel.db.models import EvidenceItem
    ev_props = [p for p in repo.list_proposals(request_id, run_id=run_row.id)
                if p.entity_type == "evidence"]
    assert len(ev_props) == 2, [p.ref_key for p in ev_props]
    print("EVIDENCE_HUNT_OK targeted query fired (recency-pinned), pair evidence staged")

    # junk 'Italy' was staged as auto-rejected, never committed
    junk = [p for p in repo.list_proposals(request_id, run_id=run_row.id)
            if p.payload.get("name") == "Italy"]
    assert junk and junk[0].status == "rejected" and "auto-rejected" in junk[0].rationale, junk

    result = flow.finalize(request_id, run_row.id)
    assert set(result["charts"]) == {"high", "professional", "deep"}
    for path in result["charts"].values():
        assert os.path.getsize(path) > 1000, path
    assert os.path.exists(result["report"])
    assert repo.count_for_request(ProcessNode, request_id) == 2
    assert repo.count_for_request(CompanyNode, request_id) == 2  # Italy screened out
    assert repo.count_for_request(EvidenceItem, request_id) == 2
    trees = [v for v in repo.list_for_request(VisualizationExport, request_id)
             if v.chart_type.startswith("tree_")]
    assert len(trees) == 3, trees

    # --- idempotent merge: run the whole flow again on the same request ---
    run2 = flow.run_research(request_id, "professional",
                             llm=fake_llm, gather=fake_gather, online_fallback=False)
    result2 = flow.finalize(request_id, run2.id)
    assert repo.count_for_request(ProcessNode, request_id) == 2
    assert repo.count_for_request(CompanyNode, request_id) == 2
    from funnel.db.models import RelationshipEdge
    assert repo.count_for_request(RelationshipEdge, request_id) == 1
    assert result2["counts"]["merged"] >= 4, result2["counts"]
    assert result2["counts"]["company"] == 0, result2["counts"]  # no new inserts
    print("MERGE_OK second run merged", result2["counts"]["merged"],
          "entities, zero duplicates")

    # --- enrichment: fake fundamentals + fake estimates --------------------
    from funnel.research import enrich as enrich_mod

    def fake_fetch(ticker):
        return {"market_cap": 5e9, "revenue": 1e9, "symbol": ticker}

    def fake_est_llm(messages, *, online=False):
        assert "revenue_purity" in messages[-1]["content"], "wrong prompt"
        return {"estimates": [{"name": "La Marzocco", "revenue_purity": 0.9,
                               "market_share": 0.4, "rationale": "focused maker"}]}

    comps = repo.list_for_request(CompanyNode, request_id)
    lm = next(c for c in comps if c.name == "La Marzocco")
    ims = next(c for c in comps if c.name == "IMS Boilers")
    lm.ticker = "LMZ.MI"                    # fundamentals candidate
    ims.revenue_purity_estimate = 0.55      # pre-set manual value must survive
    repo.update(lm)
    repo.update(ims)
    counts_e = enrich_mod.enrich_companies(request_id, llm=fake_est_llm, fetch=fake_fetch)
    lm2 = repo.get(CompanyNode, lm.id)
    ims2 = repo.get(CompanyNode, ims.id)
    assert lm2.financials.get("market_cap") == 5e9, lm2.financials
    assert lm2.revenue_purity_estimate == 0.9 and lm2.market_share_estimate == 0.4
    assert ims2.revenue_purity_estimate == 0.55, ims2.revenue_purity_estimate
    assert counts_e == {"financials": 1, "estimates": 1}, counts_e
    print("ENRICH_OK fundamentals + estimates landed; manual value preserved")

    # re-run on the same request must NOT clobber existing intake answers
    flow.apply_brief(request_id, lambda m, **k: {"industry": "SHOULD NOT OVERWRITE"})
    assert repo.get_intake_answers(request_id)["industry"].answer_value == "Espresso machines"

    # --- provider dispatch (no network) -----------------------------------
    from funnel import config
    from funnel.research import llm as llm_factory
    from funnel.research.openrouter import OpenRouterError
    from funnel.research.tiers import get_tier, resolve_model

    assert config.llm_provider() == "anthropic"  # default
    os.environ["FUNNEL_LLM_PROVIDER"] = "openrouter"
    prev_model = os.environ.pop("OPENROUTER_MODEL", None)  # .env may set v3.2 etc.
    try:
        assert config.llm_provider() == "openrouter"
        assert resolve_model(get_tier("professional")) == "deepseek/deepseek-chat"
        os.environ["OPENROUTER_MODEL_DEEP"] = "deepseek/deepseek-r1"
        assert resolve_model(get_tier("deep_research")) == "deepseek/deepseek-r1"
        # factory returns the OpenRouter callable; without a key it must raise
        # the provider's own error (proves dispatch, no network attempted)
        callable_ = llm_factory.make_llm("deepseek/deepseek-chat")
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            callable_([{"role": "user", "content": "hi"}])
            raise AssertionError("expected OpenRouterError without a key")
        except OpenRouterError:
            pass
    finally:
        os.environ["FUNNEL_LLM_PROVIDER"] = "anthropic"
        os.environ.pop("OPENROUTER_MODEL_DEEP", None)
        if prev_model is not None:
            os.environ["OPENROUTER_MODEL"] = prev_model
    assert resolve_model(get_tier("professional")) == "claude-sonnet-4-6"
    print("PROVIDER_OK anthropic default; openrouter dispatch + deepseek defaults + keyless error")

    print("FLOW_OK brief+smartsearch+finalize:",
          "queries=", len(SEEN_QUERIES),
          "charts=", sorted(result["charts"]),
          "report=", os.path.basename(result["report"]))


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
