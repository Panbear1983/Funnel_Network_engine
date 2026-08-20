"""The one-topic flow: topic → auto-brief → smart research → (review) →
commit + tree charts at all three detail levels.

This is the engine behind the Map tab and the `--topic` CLI. The auto-brief
replaces the manual questionnaire on the golden path: one LLM call proposes the
intake answers (kept editable in Advanced → Refine), plus the topic
profile that drives smart search. Nothing here bypasses the review gate —
research still stages Proposals, and `finalize` only materializes non-rejected
ones.
"""

from __future__ import annotations

from typing import Callable, Optional

from funnel import config
from funnel.db import repo
from funnel.db.models import ResearchRequest, ResearchRun, VisualizationExport
from funnel.export import data_export
from funnel.export.chart_tree import render_tree
from funnel.graph.assembly import build_graph
from funnel.intake.questions import RESEARCH_CONTEXT_ID, SECTIONS
from funnel.research import enrich
from funnel.research import llm as llm_factory
from funnel.research import prompts
from funnel.research.ingest import commit_proposals
from funnel.research.pipeline import run_pipeline
from funnel.research.smartsearch import TopicProfile
from funnel.research.tiers import get_tier, resolve_model

Progress = Callable[[str], None]

TREE_RESOLUTIONS = ("high", "professional", "deep")

# question id -> (ResearchRequest attr or None, value is a list)
_BRIEF_FIELDS = {
    "industry": ("industry_of_interest", False),
    "target_output": ("target_output", False),
    "geography": ("geography", True),
    "relationship_types": ("relationship_types", True),
    "company_origin": (None, False),
    "goal": (None, False),
    "depth": ("research_depth", False),
    "strictness": ("source_strictness", False),
    "known_terms": (None, True),
}
# brief JSON key -> question id (where they differ)
_BRIEF_KEYS = {"depth": "research_depth", "strictness": "source_strictness"}

_QUESTION_INDEX = {
    q.id: (section.title, q.text)
    for section in SECTIONS for q in section.questions
}


def _as_text(value, is_list: bool) -> str:
    if is_list:
        return ", ".join(str(v).strip() for v in (value or []) if str(v).strip())
    return str(value or "").strip()


def apply_brief(request_id: str, llm, progress: Optional[Progress] = None) -> TopicProfile:
    """One LLM call: topic → intake answers + request fields + topic profile.
    Existing non-empty answers/fields are never overwritten, so re-running the
    flow on a refined request keeps the user's edits."""
    request = repo.get(ResearchRequest, request_id)
    topic = request.title or request.industry_of_interest or ""
    answers = repo.get_intake_answers(request_id)
    context_answer = answers.get(RESEARCH_CONTEXT_ID)
    research_context = context_answer.answer_value if context_answer else ""
    data = llm(prompts.auto_brief(topic, research_context=research_context))

    filled = 0
    for qid, (attr, is_list) in _BRIEF_FIELDS.items():
        raw = data.get(_BRIEF_KEYS.get(qid, qid))
        text = _as_text(raw, is_list)
        if not text:
            continue
        existing = answers.get(qid)
        if not (existing and existing.answer_value):
            section_title, question_text = _QUESTION_INDEX[qid]
            repo.save_intake_answer(request_id, section_title, qid, question_text, text)
            filled += 1
        if attr and not getattr(request, attr):
            setattr(request, attr, raw if is_list else text)
    repo.update(request)
    if progress:
        progress(f"Auto-brief ready ({filled} intake answers proposed).")
    return TopicProfile.from_brief(data)


def run_research(
    request_id: str,
    tier_key: str,
    *,
    llm=None,
    gather=None,
    progress: Optional[Progress] = None,
    online_fallback: bool = True,
) -> ResearchRun:
    """Auto-brief (1 call) + the staged research pipeline, with smart search."""
    if llm is None:
        if not config.llm_api_key():
            return run_pipeline(
                request_id,
                tier_key,
                llm=None,
                gather=gather,
                progress=progress,
                online_fallback=online_fallback,
            )
        llm = llm_factory.make_llm(resolve_model(get_tier(tier_key)))
    skipped: list[str] = []
    if progress:
        progress("Understanding the topic…")
    try:
        profile = apply_brief(request_id, llm, progress)
    except Exception as exc:
        # A failed brief shouldn't kill the run — fall back to what's on the request.
        profile = None
        skipped.append(f"auto-brief: {type(exc).__name__}: {exc}")
        if progress:
            progress(f"Auto-brief skipped ({type(exc).__name__}) — using existing intake.")
    return run_pipeline(request_id, tier_key, llm=llm, gather=gather,
                        progress=progress, online_fallback=online_fallback,
                        profile=profile, initial_skips=skipped)


def finalize(request_id: str, run_id: str,
             progress: Optional[Progress] = None, llm=None) -> dict:
    """Commit non-rejected proposals, enrich companies, then export the tree
    chart at all three detail levels plus the Markdown report."""
    counts = commit_proposals(request_id, run_id=run_id)
    if progress:
        progress("Committed " + ", ".join(f"{k} {v}" for k, v in counts.items()) + ".")

    # Company intelligence: fundamentals + purity/share estimates, so the
    # relevance scoring under the charts reflects real metrics. Graceful
    # no-op without yfinance/keys; FUNNEL_SKIP_ENRICH=1 disables.
    if llm is None:
        run = repo.get(ResearchRun, run_id)
        if run and run.model:
            llm = llm_factory.make_llm(run.model)
    if progress:
        progress("Enriching companies…")
    counts["enriched"] = sum(
        enrich.enrich_companies(request_id, llm=llm, progress=progress).values())

    exported = export_map(request_id, progress=progress)
    return {"counts": counts, **exported}


def export_map(request_id: str, progress: Optional[Progress] = None) -> dict:
    """Build the graph and export the tree chart at all three detail levels
    plus the Markdown report. Shared by the LLM finalize path and the offline
    ISR-seed path. Returns {'charts': {...}, 'report': str}."""
    model = build_graph(request_id)
    request = model.request

    charts: dict[str, str] = {}
    for resolution in TREE_RESOLUTIONS:
        path = render_tree(model, data_export.export_path(request, f"tree-{resolution}.png"),
                           resolution=resolution)
        repo.add(VisualizationExport(
            request_id=request_id, chart_type=f"tree_{resolution}",
            file_format="png", file_path=str(path),
            layout_options={"resolution": resolution},
        ))
        charts[resolution] = str(path)
        if progress:
            progress(f"Tree chart ({resolution}) → {path.name}")

    report = data_export.export_markdown(model, data_export.export_path(request, "md"))
    if progress:
        progress(f"Report → {report.name}")
    return {"charts": charts, "report": str(report)}
