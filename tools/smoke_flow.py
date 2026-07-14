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
            return {"companies": [{"name": "IMS Boilers", "ticker": None, "country": "IT",
                                   "role": "supplier", "confidence": 0.7, "sources": []}]}
        return {"companies": [{"name": "La Marzocco", "ticker": None, "country": "IT",
                               "role": "operator", "confidence": 0.9, "sources": []}]}
    if "Infer supplier" in t:
        return {"edges": [{"from": "IMS Boilers", "to": "La Marzocco",
                           "relationship_type": "supplier", "evidence_level": "confirmed",
                           "confidence": 0.8}]}
    if "Extract factual claims" in t:
        return {"evidence": []}
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

    result = flow.finalize(request_id, run_row.id)
    assert set(result["charts"]) == {"high", "professional", "deep"}
    for path in result["charts"].values():
        assert os.path.getsize(path) > 1000, path
    assert os.path.exists(result["report"])
    assert repo.count_for_request(ProcessNode, request_id) == 2
    assert repo.count_for_request(CompanyNode, request_id) == 2
    trees = [v for v in repo.list_for_request(VisualizationExport, request_id)
             if v.chart_type.startswith("tree_")]
    assert len(trees) == 3, trees

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
