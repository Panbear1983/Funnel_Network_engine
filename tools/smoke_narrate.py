"""Offline test for funnel/research/narrate.py — the PURE beginner-facing
wording layer (stage labels, outcome guidance, coverage summary). No Textual,
no DB writes. Run: `python tools/smoke_narrate.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_narrate_"))
os.environ.setdefault("FUNNEL_ISR_DB", "")
os.environ.setdefault("FUNNEL_LLM_PROVIDER", "nvidia")
os.environ.setdefault("NVIDIA_API_KEY", "")

from funnel.db.models import Proposal, ResearchRun, ResearchRunOutcome  # noqa: E402
from funnel.research.narrate import (  # noqa: E402
    coverage_summary, outcome_guidance, plain_stage,
)


def _p(entity_type, ref, payload=None, sources=None, status="pending"):
    return Proposal(run_id="run_x", request_id="req_x", entity_type=entity_type,
                    ref_key=ref, payload=payload or {}, rationale="",
                    confidence=0.5, sources=sources or [], status=status)


def run() -> None:
    # --- stage mapping: one representative marker per stage family ---------
    assert plain_stage("Understanding the topic…").startswith("Stage 1")
    assert plain_stage("Discovering process stack…").startswith("Stage 2")
    assert plain_stage("Researching companies for 'X'…").startswith("Stage 3")
    assert plain_stage("Inferring relationships…").startswith("Stage 4")
    assert plain_stage("Tree chart (high) → x.png").startswith("Stage 5")
    assert plain_stage("some unrecognized line") is None

    # --- guidance exists and is actionable for every outcome ---------------
    for outcome in ResearchRunOutcome:
        text = outcome_guidance(outcome.value, auto_build=True, key_name="NVIDIA_API_KEY")
        assert text and "Next:" in text, (outcome, text)
    creds = outcome_guidance("needs_credentials", auto_build=True,
                             key_name="NVIDIA_API_KEY")
    assert "NVIDIA_API_KEY" in creds and ".env" in creds, creds
    cancelled = outcome_guidance("cancelled", auto_build=False, key_name="K")
    assert "kept" in cancelled, cancelled
    assert outcome_guidance("running", auto_build=True, key_name="K") == ""

    # --- coverage summary: counts, sources, unverified sentence ------------
    run_row = ResearchRun(request_id="req_x", status="complete",
                          summary="complete: …", log=[])
    proposals = [
        _p("process_node", "n1", {"name": "Node"}),
        _p("company", "c1", {"name": "Alpha"}, sources=["https://a.example/x"]),
        _p("company", "c2", {"name": "Beta"}),
        _p("edge", "c1->c2", {"from_company": "c1", "to_company": "c2"}),
        _p("evidence", "ev1", {"claim": "Alpha supplies Beta",
                               "source_url": "https://a.example/x",
                               "related_companies": ["c1", "c2"]}),
        _p("evidence", "ev2", {"claim": "recalled claim", "source_url": "",
                               "related_companies": ["c1"],
                               "provenance": "model_knowledge"}),
        _p("company", "junk", {"name": "Junk"}, status="rejected"),
    ]
    text = coverage_summary(run_row, proposals)
    assert "1 process step" in text, text
    assert "2 companies" in text, text            # rejected one excluded
    assert "1 of 2 saved evidence items include" in text, text
    assert "model-recalled" in text, text          # unverified sentence
    assert "a.example" in text, text               # site hint

    # no-evidence branch
    text2 = coverage_summary(run_row, [_p("process_node", "n", {"name": "N"})])
    assert "No evidence items with inspectable source links" in text2, text2

    print("NARRATE_OK stages + guidance(all outcomes incl cancelled) + coverage")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
