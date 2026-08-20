"""Offline regression coverage for explicit research-run outcomes.

All provider and source behavior is injected; this script makes no network calls.
Run: ``python tools/smoke_outcomes.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_outcomes_"))
# Keep the offline test deterministic: never auto-probe a real ISR sibling DB.
os.environ.setdefault("FUNNEL_ISR_DB", "")

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import ProcessNode, ResearchRunOutcome  # noqa: E402
from funnel.research import flow  # noqa: E402
from funnel.research.pipeline import run_pipeline  # noqa: E402


def fake_gather(query: str, count: int) -> tuple[str, list[str]]:
    return f"SOURCE: https://example.test/source\nEvidence for {query}", [
        "https://example.test/source"
    ]


def fake_llm(messages, *, online=False):
    text = messages[-1]["content"]
    if "Propose a research brief" in text:
        return {"industry": "Test industry", "target_output": "Test product"}
    if "Decompose this into the value-chain process stack" in text:
        return {"process_nodes": [
            {"name": "Inputs", "layer": "upstream", "description": "Core inputs"},
            {"name": "Assembly", "layer": "midstream", "description": "Core assembly"},
        ]}
    if "craft up to" in text:
        return {"plans": []}
    if "Identify up to" in text:
        return {"companies": [
            {"name": "Alpha Corp", "role": "materials_supplier", "confidence": 0.8},
            {"name": "Beta Corp", "role": "operator", "confidence": 0.8},
        ]}
    if "Infer supplier" in text:
        return {"edges": [
            {"from": "Alpha Corp", "to": "Beta Corp",
             "relationship_type": "supplier", "evidence_level": "inferred",
             "confidence": 0.6},
        ]}
    if "Extract factual claims" in text:
        return {"evidence": [
            {"claim": "Alpha supplies Beta",
             "source_url": "https://example.test/source",
             "related_companies": ["Alpha Corp", "Beta Corp"]},
        ]}
    return {}


def request(title: str) -> str:
    return repo.create_request(title).id


def fail_stage(marker: str, message: str = "injected failure"):
    def injected(messages, *, online=False):
        if marker in messages[-1]["content"]:
            raise RuntimeError(message)
        return fake_llm(messages, online=online)

    return injected


def run() -> None:
    init_db()

    complete = run_pipeline(
        request("complete"), "professional",
        llm=fake_llm, gather=fake_gather, online_fallback=False)
    assert complete.status == ResearchRunOutcome.COMPLETE.value, complete.summary

    stage_failures = {
        "company": "Identify up to",
        "relationship": "Infer supplier",
        "evidence": "Extract factual claims",
    }
    for label, marker in stage_failures.items():
        rid = request(label)
        outcome = run_pipeline(
            rid, "professional", llm=fail_stage(marker),
            gather=fake_gather, online_fallback=False)
        assert outcome.status == ResearchRunOutcome.PARTIAL.value, outcome.summary
        assert label in outcome.summary, outcome.summary
        assert repo.list_proposals(rid, run_id=outcome.id), label
        assert repo.count_for_request(ProcessNode, rid) == 0, (
            "failure handling bypassed proposal staging", label)

    # Stage-2 (process stack) failure must DEGRADE, not FAIL the whole run:
    # no nodes -> no_credible_results, request saved, skip line logged.
    rid = request("stack failure")
    stack_failed = run_pipeline(
        rid, "professional", llm=fail_stage("process stack"),
        gather=fake_gather, online_fallback=False)
    assert stack_failed.status == ResearchRunOutcome.NO_CREDIBLE_RESULTS.value, (
        stack_failed.status, stack_failed.summary)
    assert any("process stack discovery" in line for line in stack_failed.log), (
        stack_failed.log)

    # Startup reaper: an orphaned status="running" row is marked failed.
    from funnel.db.models import ResearchRun
    orphan = repo.add(ResearchRun(request_id=rid, tier="professional",
                                  model="m", status="running"))
    reaped = repo.reap_running_runs()
    assert reaped == 1, reaped
    orphan2 = repo.get(ResearchRun, orphan.id)
    assert orphan2.status == ResearchRunOutcome.FAILED.value
    assert "interrupted" in orphan2.summary and orphan2.finished_at is not None
    assert repo.reap_running_runs() == 0        # idempotent

    rid = request("gather")

    def failed_gather(query: str, count: int):
        raise RuntimeError("offline source unavailable")

    gathered = run_pipeline(
        rid, "professional", llm=fake_llm,
        gather=failed_gather, online_fallback=False)
    assert gathered.status == ResearchRunOutcome.PARTIAL.value, gathered.summary
    assert "source gathering" in gathered.summary, gathered.summary

    optional_credentials = run_pipeline(
        request("optional Tavily"), "professional", llm=fake_llm,
        gather=lambda query, count: (_ for _ in ()).throw(
            RuntimeError("TAVILY_API_KEY is not set")),
        online_fallback=False)
    assert optional_credentials.status == ResearchRunOutcome.PARTIAL.value, (
        optional_credentials.summary)

    calls = 0

    def failed_brief_once(messages, *, online=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("brief unavailable")
        return fake_llm(messages, online=online)

    brief = flow.run_research(
        request("auto brief"), "professional",
        llm=failed_brief_once, gather=fake_gather, online_fallback=False)
    assert brief.status == ResearchRunOutcome.PARTIAL.value, brief.summary
    assert "auto-brief" in brief.summary, brief.summary

    credentials = run_pipeline(
        request("credentials"), "professional",
        llm=fail_stage(
            "Decompose this into the value-chain process stack",
            "ANTHROPIC_API_KEY is not set"),
        gather=fake_gather, online_fallback=False)
    assert credentials.status == ResearchRunOutcome.NEEDS_CREDENTIALS.value, (
        credentials.summary)

    original_key_lookup = flow.config.llm_api_key
    flow.config.llm_api_key = lambda: ""
    try:
        missing_provider = flow.run_research(
            request("missing provider"), "professional",
            gather=fake_gather, online_fallback=False)
    finally:
        flow.config.llm_api_key = original_key_lookup
    assert missing_provider.status == ResearchRunOutcome.NEEDS_CREDENTIALS.value, (
        missing_provider.summary)
    assert "Request saved" in missing_provider.summary, missing_provider.summary

    empty = run_pipeline(
        request("empty"), "professional",
        llm=lambda messages, **kwargs: {},
        gather=fake_gather, online_fallback=False)
    assert empty.status == ResearchRunOutcome.NO_CREDIBLE_RESULTS.value, empty.summary

    # A stage-2 (process stack) failure now DEGRADES (guarded since Phase 1);
    # true FAILED needs a catastrophic error outside the stage guards — e.g.
    # a request id that does not exist (brief building crashes, no proposals).
    stack_degraded = run_pipeline(
        request("stack degraded"), "professional",
        llm=fail_stage("Decompose this into the value-chain process stack"),
        gather=fake_gather, online_fallback=False)
    assert stack_degraded.status == ResearchRunOutcome.NO_CREDIBLE_RESULTS.value, (
        stack_degraded.summary)

    failed = run_pipeline(
        "request_does_not_exist", "professional",
        llm=fake_llm, gather=fake_gather, online_fallback=False)
    assert failed.status == ResearchRunOutcome.FAILED.value, failed.summary

    print(
        "OUTCOMES_OK complete, partial stage/source/brief failures, "
        "needs_credentials, no_credible_results (incl. degraded stage-2), "
        "failed, reaper"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
