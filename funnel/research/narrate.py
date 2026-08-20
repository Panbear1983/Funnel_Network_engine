"""Beginner-facing narration for research runs — PURE functions (no Textual):
stage-label mapping, outcome guidance, and the coverage/provenance summary.
Extracted from the Map panel so the wording is unit-testable and reusable by
the CLI."""

from __future__ import annotations

from collections import Counter
from typing import Optional
from urllib.parse import urlparse

from funnel import config
from funnel.db.models import Proposal, ResearchRun, ResearchRunOutcome

_ACTIVE_STAGES = (
    ("Stage 5 of 5 — Building and exporting your map", (
        "automatic building", "building map", "committed ", "enriching companies",
        "tree chart", "report →",
    )),
    ("Stage 4 of 5 — Checking relationships and evidence", (
        "inferring relationships", "extracting evidence", "hunting evidence",
        "checking for contradictions", "relationship", "evidence",
    )),
    ("Stage 3 of 5 — Finding relevant companies", (
        "researching companies", "companies found", "unique companies",
    )),
    ("Stage 2 of 5 — Sketching the value-chain steps", (
        "discovering process stack", "process nodes", "planning smart search",
        "smart queries",
    )),
    ("Stage 1 of 5 — Understanding your topic", (
        "understanding the topic", "auto-brief", "brief built",
    )),
)


def plain_stage(progress_line: str) -> Optional[str]:
    """Translate internal progress messages into stable beginner-facing stages."""
    normalized = progress_line.casefold()
    for label, markers in _ACTIVE_STAGES:
        if any(marker in normalized for marker in markers):
            return label
    return None


def outcome_guidance(status: str, *, auto_build: bool, key_name: str) -> str:
    """Return one concise recovery/continuation action for every run outcome."""
    if status == ResearchRunOutcome.COMPLETE.value:
        next_step = (
            "finishing your map automatically."
            if auto_build else
            "review the proposed map items, then choose Finish Reviewed Map."
        )
        return f"[b]Research complete.[/b] Next: {next_step}"
    if status == ResearchRunOutcome.PARTIAL.value:
        next_step = (
            "finishing a map with the usable items found; refine it afterward if needed."
            if auto_build else
            "review the usable items found, then choose Finish Reviewed Map."
        )
        return f"[b]Some research was unavailable, but usable items were saved.[/b] Next: {next_step}"
    if status == ResearchRunOutcome.NEEDS_CREDENTIALS.value:
        hint = config.other_provider_hint()
        return (
            f"[b]Request saved; research credentials are needed.[/b] "
            f"Next: set {key_name} in .env at the repo root (copy .env.example "
            f"if you have no .env), then choose Build Map again."
            + (f" {hint}" if hint else "")
        )
    if status == ResearchRunOutcome.NO_CREDIBLE_RESULTS.value:
        return (
            "[b]No credible map items were found; your request is saved.[/b] "
            "Next: add one detail in the optional context, then choose Build Map again."
        )
    if status == ResearchRunOutcome.FAILED.value:
        return (
            "[b]Research stopped before a usable map was produced; your request is saved.[/b] "
            "Next: choose Build Map to retry, or adjust the topic or optional context first."
        )
    if status == ResearchRunOutcome.CANCELLED.value:
        return (
            "[b]Run cancelled — items found so far are kept.[/b] "
            "Next: choose Build Map to start again, or expand Advanced review "
            "to use what was staged."
        )
    return ""


def plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def source_urls(proposal: Proposal) -> set[str]:
    """Return only inspectable links already stored on a proposal."""
    values = list(proposal.sources or [])
    if proposal.entity_type == "evidence":
        values.append(proposal.payload.get("source_url", ""))
    return {
        value.strip()
        for value in values
        if isinstance(value, str)
        and value.strip().casefold().startswith(("http://", "https://"))
    }


def unavailable_stages(run: ResearchRun) -> list[str]:
    """Extract stage names from stored skip log lines without exposing errors."""
    stages: list[str] = []

    def remember(value: str) -> None:
        stage = value.split(":", 1)[0].strip()
        if stage and stage not in stages:
            stages.append(stage)

    for line in run.log or []:
        if not isinstance(line, str) or not line.casefold().startswith("skipped "):
            continue
        remember(line[8:])
    # Initial failures such as auto-brief are stored in the terminal summary,
    # not as individual log entries.
    summary = run.summary or ""
    for marker in (". Skipped: ", ". Unavailable: "):
        if marker in summary:
            for value in summary.split(marker, 1)[1].split(";"):
                remember(value)
    return stages


def coverage_summary(run: ResearchRun, proposals: list[Proposal]) -> str:
    """Explain stored run coverage and provenance without doing new research."""
    usable = [p for p in proposals if p.status not in {"rejected", "skipped"}]
    counts = Counter(p.entity_type for p in usable)
    found_parts = [
        plural(counts["process_node"], "process step"),
        plural(counts["company"], "company", "companies"),
        plural(counts["edge"], "relationship"),
    ]
    if counts["deal"]:
        found_parts.append(plural(counts["deal"], "deal"))

    evidence = [p for p in usable if p.entity_type == "evidence"]
    sourced_evidence = [p for p in evidence if source_urls(p)]
    all_urls = set().union(*(source_urls(p) for p in usable)) if usable else set()
    sites = sorted({
        urlparse(url).netloc.casefold().removeprefix("www.")
        for url in all_urls
        if urlparse(url).netloc
    })
    site_hint = f" ({', '.join(sites[:3])}{'…' if len(sites) > 3 else ''})" if sites else ""

    company_refs = {p.ref_key for p in usable if p.entity_type == "company"}
    sourced_company_refs: set[str] = set()
    for proposal in usable:
        if proposal.entity_type == "company" and source_urls(proposal):
            sourced_company_refs.add(proposal.ref_key)
    for proposal in sourced_evidence:
        sourced_company_refs.update(proposal.payload.get("related_companies", []))
    sourced_company_refs.intersection_update(company_refs)

    evidence_pairs = [
        set(p.payload.get("related_companies", []))
        for p in sourced_evidence
    ]
    edges = [p for p in usable if p.entity_type == "edge"]
    covered_edges = 0
    for edge in edges:
        pair = {
            edge.payload.get("from_company", ""),
            edge.payload.get("to_company", ""),
        }
        if source_urls(edge) or (
            "" not in pair and any(pair <= evidence_pair for evidence_pair in evidence_pairs)
        ):
            covered_edges += 1

    if evidence:
        provenance = (
            f"{len(sourced_evidence)} of {len(evidence)} saved evidence items include "
            f"a source link; {plural(len(all_urls), 'unique link')}{site_hint}. "
            f"{len(sourced_company_refs)} of {counts['company']} companies and "
            f"{covered_edges} of {len(edges)} relationships are linked to sourced evidence."
        )
    else:
        provenance = (
            "No evidence items with inspectable source links were saved for this run."
        )

    uncertainties: list[str] = []
    unverified = [p for p in usable if p.entity_type == "evidence"
                  and p.payload.get("provenance") == "model_knowledge"]
    if unverified:
        uncertainties.append(
            f"{plural(len(unverified), 'evidence item')} "
            f"{'is' if len(unverified) == 1 else 'are'} model-recalled "
            f"(no source was fetched; links unverified)"
        )
    if not evidence:
        uncertainties.append("the run saved no source-backed evidence")
    missing_evidence_links = len(evidence) - len(sourced_evidence)
    if missing_evidence_links:
        uncertainties.append(
            f"{plural(missing_evidence_links, 'evidence item')} "
            f"{'has' if missing_evidence_links == 1 else 'have'} no source link"
        )
    uncovered_edges = len(edges) - covered_edges
    if uncovered_edges:
        uncertainties.append(
            f"{plural(uncovered_edges, 'relationship')} "
            f"{'lacks' if uncovered_edges == 1 else 'lack'} saved source-backed evidence"
        )
    unavailable = unavailable_stages(run)
    if unavailable:
        shown = ", ".join(unavailable[:2])
        if len(unavailable) > 2:
            shown += f", and {len(unavailable) - 2} more"
        uncertainties.append(f"coverage is incomplete because {shown} was unavailable")
    elif run.status == ResearchRunOutcome.PARTIAL.value:
        uncertainties.append("the run is partial because some research was unavailable")
    if not uncertainties:
        uncertainties.append(
            "no stored source gap was flagged, but the saved evidence still needs review"
        )

    return (
        "[b]Research coverage[/b]\n"
        f"Found: {', '.join(found_parts[:-1])}, and {found_parts[-1]}.\n"
        f"Sources: {provenance}\n"
        f"Uncertain: {'; '.join(uncertainties)}.\n"
        "Inspect: expand Advanced review and press [b]v[/b] on an item; after "
        "building, open Advanced → Evidence (F2) for claims, links, and quotes."
    )
