"""Deterministic research pipeline. A fixed sequence of stages; each LLM call
returns strict JSON. The `llm` and `gather` callables are injected so the whole
thing runs offline in tests. Everything is written as pending Proposals for
human review — nothing touches the real graph here."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from funnel import config
from funnel.db import repo
from funnel.db.models import (
    Proposal,
    ResearchRequest,
    ResearchRun,
    ResearchRunOutcome,
)
from funnel.intake.questions import RESEARCH_CONTEXT_ID
from funnel.knowledge.isr import IsrCompany, IsrKnowledge
from funnel.research import llm as llm_factory
from funnel.research import prompts, screen, smartsearch, websearch
from funnel.research.ingest import entity_slug, slugify
from funnel.research.smartsearch import SmartGather, TopicProfile
from funnel.research.tiers import get_tier, resolve_model

# llm(messages, *, online=False) -> dict ; gather(query, k) -> (text, urls)
LLM = Callable[..., dict]
Gather = Callable[[str, int], tuple]
Progress = Callable[[str], None]


class RunCancelled(Exception):
    """Raised at a stage boundary when the run's cancel Event is set. The run
    finishes with outcome CANCELLED; staged proposals are kept."""


def _f(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _brief(request: ResearchRequest, answers: dict) -> str:
    parts = [
        f"Industry: {request.industry_of_interest or '(unspecified)'}",
        f"Target output: {request.target_output or '(unspecified)'}",
    ]
    if request.geography:
        parts.append("Geography: " + ", ".join(request.geography))
    context_answer = answers.get(RESEARCH_CONTEXT_ID)
    research_context = (
        context_answer.answer_value.strip() if context_answer else "")
    if research_context:
        parts.append(f"Research context: {research_context}")
    parts.append(f"Depth: {request.research_depth}; source strictness: {request.source_strictness}")
    for qid in ("relationship_types", "company_origin", "goal"):
        a = answers.get(qid)
        if a and a.answer_value:
            parts.append(f"{a.question_text} -> {a.answer_value}")
    return "\n".join(parts)


def _queries(node_name: str, request: ResearchRequest, tier) -> list[str]:
    """Static fallback used when topic-driven query planning fails."""
    target = request.target_output or request.industry_of_interest or ""
    base = f"{node_name} {target}".strip()
    queries = [f"{base} companies suppliers", f"{base} market leaders manufacturers"]
    if request.geography:
        queries.append(f"{base} {request.geography[0]}")
    return queries[: tier.max_queries_per_node]


def run_pipeline(
    request_id: str,
    tier_key: str,
    *,
    llm: Optional[LLM] = None,
    gather: Optional[Gather] = None,
    progress: Optional[Progress] = None,
    dry_run: bool = False,
    online_fallback: bool = True,
    profile: Optional[TopicProfile] = None,
    initial_skips: Optional[list[str]] = None,
    cancel: Optional[threading.Event] = None,
    usage: Optional[llm_factory.UsageTracker] = None,
) -> ResearchRun:
    tier = get_tier(tier_key)
    model = resolve_model(tier)
    request = repo.get(ResearchRequest, request_id)
    run = repo.add(ResearchRun(
        request_id=request_id, tier=tier_key, model=model, status="running"))
    log: list[str] = []
    skips = list(initial_skips or [])
    credential_blocked = False

    def note(msg: str) -> None:
        log.append(msg)
        if progress:
            progress(msg)

    def check_cancel() -> None:
        # Cooperative: honored at stage boundaries and loop tops; an in-flight
        # HTTP call finishes first (the UI wording says so).
        if cancel is not None and cancel.is_set():
            raise RunCancelled()

    def skip(stage: str, exc: Exception) -> None:
        nonlocal credential_blocked
        reason = f"{stage}: {type(exc).__name__}: {exc}"
        if reason not in skips:
            skips.append(reason)
            note(f"Skipped {reason}")
        # Tavily is optional because model-backed online research can replace
        # it. Only a provider-stage credential failure blocks the run.
        if stage != "source gathering" and _is_credentials_error(exc):
            credential_blocked = True

    if llm is None:
        if not config.llm_api_key():
            key_name = config.llm_key_name()
            return _finish(
                run,
                ResearchRunOutcome.NEEDS_CREDENTIALS,
                log,
                note,
                skips=[f"research provider: {key_name} is not configured"],
            )
        try:
            llm = llm_factory.make_llm(model, usage=usage)
        except Exception as exc:
            skip("research provider", exc)
            return _finish(
                run,
                (ResearchRunOutcome.NEEDS_CREDENTIALS
                 if credential_blocked else ResearchRunOutcome.FAILED),
                log,
                note,
                skips=skips,
            )
    injected_gather = gather is not None
    if gather is None:
        gather = websearch.gather_context

    def tracked_gather(query: str, count: int) -> tuple:
        try:
            return gather(query, count)
        except Exception as exc:
            skip("source gathering", exc)
            return "", []

    def add_proposal(entity_type, ref_key, payload, rationale, confidence, sources,
                     status="pending"):
        repo.add(Proposal(
            run_id=run.id, request_id=request_id, entity_type=entity_type, ref_key=ref_key,
            payload=payload, rationale=rationale or "", confidence=confidence,
            sources=sources or [], status=status,
        ))

    try:
        answers = repo.get_intake_answers(request_id)
        brief = _brief(request, answers)
        # Recency bias: unless the intake explicitly allows older sources,
        # searches are pinned to the current year.
        older = answers.get("older_sources")
        recent_year = None if (older and older.answer_value == "yes") else datetime.now().year
        smart = SmartGather(tracked_gather, profile, recent_year=recent_year)
        note(f"Brief built for '{request.title or request.industry_of_interest or request_id}'.")

        # Optional grounding layer: the sibling ISR knowledge DB (read-only).
        # None when absent/disabled — every use below no-ops.
        isr = IsrKnowledge.open_default()
        if isr is not None:
            note(f"  ISR knowledge base connected "
                 f"({isr.stats()['companies']} companies).")

        # Re-run anchoring: existing entity names are fed back into the prompts
        # so a second Generate reuses them and the idempotent merge can match.
        from funnel.db.models import CompanyNode as _CN
        from funnel.db.models import ProcessNode as _PN
        existing_node_names = [n.name for n in repo.list_for_request(_PN, request_id) if n.name]
        existing_company_names = [c.name for c in repo.list_for_request(_CN, request_id) if c.name][:60]

        ctx = StageCtx(
            request_id=request_id, request=request, run=run, tier=tier,
            llm=llm, smart=smart, isr=isr, brief=brief, profile=profile,
            online_fallback=online_fallback, injected_gather=injected_gather,
            note=note, skip=skip, add_proposal=add_proposal,
            check_cancel=check_cancel,
            existing_node_names=existing_node_names,
            existing_company_names=existing_company_names,
        )

        # The explicit stage sequence — each stage is a plain function over the
        # shared context, individually testable/toggleable; cancel is honored
        # between stages (and inside the long per-item loops).
        check_cancel()
        _stage_process_stack(ctx)
        if dry_run:
            proposals = repo.list_proposals(request_id, run_id=run.id)
            return _finish(
                run,
                _successful_outcome(proposals, skips, credential_blocked),
                log,
                note,
                skips=skips,
                prefix="dry-run",
                usage=usage,
                proposals=proposals,
            )
        for stage in (_stage_plan_queries, _stage_companies,
                      _stage_ground_and_screen, _stage_relationships,
                      _stage_evidence, _stage_evidence_targeted,
                      _stage_evidence_isr, _stage_contradictions):
            check_cancel()
            stage(ctx)

        proposals = repo.list_proposals(request_id, run_id=run.id)
        return _finish(
            run,
            _successful_outcome(proposals, skips, credential_blocked),
            log,
            note,
            skips=skips,
            usage=usage,
            proposals=proposals,
        )
    except RunCancelled:
        note("Run cancelled — items staged so far are kept.")
        return _finish(run, ResearchRunOutcome.CANCELLED, log, note,
                       skips=skips, usage=usage)
    except Exception as exc:
        skip("research pipeline", exc)
        proposals = repo.list_proposals(run.request_id, run_id=run.id)
        if credential_blocked:
            outcome = ResearchRunOutcome.NEEDS_CREDENTIALS
        elif any(p.entity_type == "process_node" for p in proposals):
            outcome = ResearchRunOutcome.PARTIAL
        else:
            outcome = ResearchRunOutcome.FAILED
        return _finish(run, outcome, log, note, skips=skips, usage=usage)


@dataclass
class StageCtx:
    """Everything the pipeline stages share: injected callables + the mutable
    accumulators each stage reads/extends. One instance per run."""

    request_id: str
    request: ResearchRequest
    run: ResearchRun
    tier: object
    llm: LLM
    smart: SmartGather
    isr: Optional[IsrKnowledge]
    brief: str
    profile: Optional[TopicProfile]
    online_fallback: bool
    injected_gather: bool
    note: Progress
    skip: Callable[[str, Exception], None]
    add_proposal: Callable[..., None]
    check_cancel: Callable[[], None]
    existing_node_names: list[str]
    existing_company_names: list[str]
    # accumulators (filled as stages run)
    node_refs: list[tuple[str, str]] = field(default_factory=list)
    node_keywords: dict[str, list[str]] = field(default_factory=dict)
    query_plan: dict = field(default_factory=dict)
    company_names: dict[str, str] = field(default_factory=dict)
    companies_acc: dict[str, dict] = field(default_factory=dict)
    context_pool: list[str] = field(default_factory=list)
    isr_hits: dict[str, IsrCompany] = field(default_factory=dict)
    staged_edges: list[tuple[str, str, str]] = field(default_factory=list)
    ev_pairs: list[set] = field(default_factory=list)
    ev_claims: list[str] = field(default_factory=list)


def _stage_process_stack(ctx: StageCtx) -> None:
    """Stage 2 — process stack. Guarded: one flaky response here must not
    FAIL the whole run — an empty stack degrades to NO_CREDIBLE_RESULTS."""
    ctx.note("Discovering process stack…")
    try:
        data = ctx.llm(prompts.process_stack(ctx.brief, ctx.tier.max_layers,
                                             ctx.tier.expand_inputs,
                                             existing=ctx.existing_node_names),
                       max_tokens=4000)
    except Exception as exc:
        ctx.skip("process stack discovery", exc)
        data = {}
    nodes = data.get("process_nodes", [])[: ctx.tier.max_layers]
    for n in nodes:
        ref = entity_slug(n.get("name", ""))
        if not ref:
            continue
        ctx.node_refs.append((ref, n.get("name", "")))
        ctx.node_keywords[ref] = [k for k in n.get("keywords", []) if k]
        ctx.add_proposal("process_node", ref, {
            "name": n.get("name", ""), "layer": n.get("layer", "midstream"),
            "description": n.get("description", ""), "keywords": n.get("keywords", []),
            "bottleneck_risk": n.get("bottleneck_risk", "unknown"), "confidence_score": 0.5,
        }, n.get("rationale", ""), 0.5, [])
    ctx.note(f"  {len(ctx.node_refs)} process nodes proposed.")


def _stage_plan_queries(ctx: StageCtx) -> None:
    """Stage 2.5 — smart source planning: intent-tagged queries generated
    from the topic (and its alias/jargon profile), not fixed templates."""
    ctx.note("Planning smart search queries…")
    ctx.query_plan = smartsearch.plan_queries(ctx.llm, ctx.brief, ctx.node_refs,
                                              ctx.tier, profile=ctx.profile)
    ctx.note(f"  smart queries planned for {len(ctx.query_plan)} nodes"
             + ("" if ctx.query_plan else " (falling back to templates)") + ".")


def _stage_companies(ctx: StageCtx) -> None:
    """Stage 3 — companies per node (dedup companies found under multiple nodes)."""
    for ref, name in ctx.node_refs:
        ctx.check_cancel()
        ctx.note(f"Researching companies for '{name}'…")
        text, urls = ctx.smart.gather_node(name, ctx.query_plan.get(ref),
                                           ctx.tier.sources_per_query,
                                           _queries(name, ctx.request, ctx.tier))
        if text:
            ctx.context_pool.append(text)
        online = ctx.online_fallback and not text
        grounded = None
        if ctx.isr is not None:
            grounded = [f"{c.label} — {c.country}, {c.industry}"
                        for c in ctx.isr.candidates([name, *ctx.node_keywords.get(ref, [])])
                        ] or None
        try:
            data = ctx.llm(
                prompts.company_discovery(name, ctx.brief, text or "(no scraped text available)",
                                          ctx.tier.companies_per_node,
                                          known=ctx.existing_company_names,
                                          grounded=grounded),
                online=online, max_tokens=4000,
            )
        except Exception as exc:
            ctx.skip(f"company discovery for '{name}'", exc)
            continue
        comps = data.get("companies", [])[: ctx.tier.companies_per_node]
        for c in comps:
            cref = entity_slug(c.get("name", ""))
            if not cref:
                continue
            ctx.company_names[cref] = c.get("name", "")
            # Model-guessed confidence caps at 0.9 so re-run max() merging can
            # never ratchet an unverified guess to certainty.
            conf = min(0.9, _f(c.get("confidence"), 0.5))
            if cref in ctx.companies_acc:
                payload = ctx.companies_acc[cref]["payload"]
                if ref not in payload["process_node_ids"]:
                    payload["process_node_ids"].append(ref)
                ctx.companies_acc[cref]["confidence"] = max(ctx.companies_acc[cref]["confidence"], conf)
                payload["confidence_score"] = ctx.companies_acc[cref]["confidence"]
            else:
                ctx.companies_acc[cref] = {
                    "payload": {
                        "name": c.get("name", ""), "ticker": c.get("ticker"),
                        "country": c.get("country"), "role": c.get("role", "operator"),
                        "process_node_ids": [ref], "confidence_score": conf,
                    },
                    "rationale": c.get("rationale", ""), "confidence": conf,
                    "sources": c.get("sources", urls),
                }
        ctx.note(f"  {len(comps)} companies found for '{name}'.")


def _stage_ground_and_screen(ctx: StageCtx) -> None:
    """Ground accumulated companies in the ISR knowledge base (fill empty
    ticker/country, tag provenance, shield known-real companies from the junk
    screen), then stage every company proposal through the screen."""
    if ctx.isr is not None:
        for cref, info in ctx.companies_acc.items():
            payload = info["payload"]
            hit = ctx.isr.lookup_company(str(payload.get("name") or ""),
                                         str(payload.get("ticker") or ""),
                                         country=payload.get("country"))
            if hit is None:
                continue
            ctx.isr_hits[cref] = hit
            if not payload.get("ticker") and hit.yahoo_ticker:
                payload["ticker"] = hit.yahoo_ticker
            if not payload.get("country") and hit.country:
                payload["country"] = hit.country
            tag = f"isr:{hit.base_ticker or hit.name}"
            if tag not in info["sources"]:
                info["sources"] = [*info["sources"], tag]
        if ctx.isr_hits:
            ctx.note(f"  {len(ctx.isr_hits)} companies grounded in the ISR knowledge base.")

    screened = 0
    for cref, info in ctx.companies_acc.items():
        reason = screen.screen_company(info["payload"],
                                       isr_member=cref in ctx.isr_hits)
        if reason:
            # Stage it visibly as rejected (restorable) and keep it out of
            # the relationship/evidence stages entirely.
            ctx.add_proposal("company", cref, info["payload"],
                             f"auto-rejected: {reason}", info["confidence"],
                             info["sources"], status="rejected")
            ctx.company_names.pop(cref, None)
            screened += 1
            continue
        ctx.add_proposal("company", cref, info["payload"], info["rationale"],
                         info["confidence"], info["sources"])
    ctx.note(f"  {len(ctx.companies_acc) - screened} unique companies proposed"
             + (f" ({screened} auto-rejected as non-companies)" if screened else "") + ".")


def _stage_relationships(ctx: StageCtx) -> None:
    """Stage 4 — relationships."""
    if len(ctx.company_names) < 2:
        return
    ctx.note("Inferring relationships…")
    lines = "\n".join(f"- {name}" for name in ctx.company_names.values())
    try:
        data = ctx.llm(prompts.relationships(lines, ctx.brief), max_tokens=8000)
    except Exception as exc:
        # One flaky LLM response must not kill the run (live run 2
        # died here on truncated DeepSeek JSON).
        ctx.skip("relationship inference", exc)
        data = {}
    edges = data.get("edges", [])
    for e in edges:
        fref, tref = entity_slug(e.get("from", "")), entity_slug(e.get("to", ""))
        if fref not in ctx.company_names or tref not in ctx.company_names or fref == tref:
            continue
        conf = min(0.9, _f(e.get("confidence"), 0.4))
        level = e.get("evidence_level", "inferred")
        ctx.add_proposal("edge", f"{fref}->{tref}", {
            "from_company": fref, "to_company": tref,
            "relationship_type": e.get("relationship_type", "supplier"),
            "evidence_level": level, "confidence_score": conf,
        }, e.get("rationale", ""), conf, [])
        ctx.staged_edges.append((fref, tref, level))
    ctx.note(f"  {len(ctx.staged_edges)} relationships proposed.")


def _stage_evidence_items(ctx: StageCtx, data: dict, *,
                          provenance: str = "scraped") -> int:
    """Shared evidence stager for stages 5/5b. model_knowledge = the provider
    recalled it without fetching any source: capped weak, lower confidence."""
    added = 0
    unverified = provenance == "model_knowledge"
    for ev in data.get("evidence", []):
        related = [r for r in (entity_slug(x) for x in ev.get("related_companies", []))
                   if r in ctx.company_names]
        url = ev.get("source_url", "")
        ctx.ev_claims.append(
            f"- {ev.get('claim', '')} "
            f"[{', '.join(ctx.company_names[r] for r in related)}] {url}")
        strength = ev.get("evidence_strength", "moderate")
        if unverified and strength in ("strong", "moderate"):
            strength = "weak"
        ctx.add_proposal("evidence", (entity_slug(ev.get("claim", ""))[:40] or "ev"), {
            "source_type": ev.get("source_type", "news"), "source_url": url,
            "publisher": ev.get("publisher", ""), "date": ev.get("date", ""),
            "claim": ev.get("claim", ""), "quote": ev.get("quote", ""),
            "related_companies": related,
            "evidence_strength": strength,
            "provenance": provenance,
            "confidence_score": 0.4 if unverified else 0.6,
        }, "", 0.4 if unverified else 0.6, [url] if url else [])
        ctx.ev_pairs.append(set(related))
        added += 1
    return added


def _stage_evidence(ctx: StageCtx) -> None:
    """Stage 5 — evidence + deals. With no scraped context (e.g. no Tavily
    key), providers WITH their own web search stand in; a provider without
    one (NVIDIA) answers from training knowledge, so that evidence is marked
    model_knowledge and capped at "weak"."""
    web_capable = llm_factory.provider_web_capable()
    context = "\n\n".join(ctx.context_pool)[:12000]
    evidence_online = False
    if ctx.tier.extract_evidence and not context and ctx.online_fallback:
        context = "(no scraped text available — search the web for evidence)"
        evidence_online = True
    if not (ctx.tier.extract_evidence and context and ctx.company_names):
        return
    if evidence_online and not web_capable:
        ctx.note("⚠ no scraped sources and no model web search — evidence "
                 "will be model-recalled and marked unverified.")
    ctx.note("Extracting evidence…" + (" (model web search)" if evidence_online and web_capable else ""))
    names = ", ".join(ctx.company_names.values())
    try:
        data = ctx.llm(prompts.evidence_and_deals(context, names, ctx.tier.extract_deals),
                       online=evidence_online, max_tokens=8000)
    except Exception as exc:
        ctx.skip("evidence extraction", exc)
        data = {}
    _stage_evidence_items(
        ctx, data,
        provenance=("model_knowledge"
                    if evidence_online and not web_capable else "scraped"))
    for dl in data.get("deals", []):
        ctx.add_proposal("deal", (entity_slug(dl.get("buyer", "") + "_" + dl.get("supplier", "")) or "deal"), {
            "deal_type": dl.get("deal_type", "partnership"),
            "buyer": entity_slug(dl.get("buyer", "")), "supplier": entity_slug(dl.get("supplier", "")),
            "product_or_service": dl.get("product_or_service", ""),
            "announced_date": dl.get("announced_date", ""), "deal_value": dl.get("deal_value"),
            "confidence_score": 0.5,
        }, "", 0.5, [])
    ctx.note("  evidence/deals proposed.")


def _stage_evidence_targeted(ctx: StageCtx) -> None:
    """Stage 5b — per-edge evidence targeting: confirmed edges that no staged
    evidence covers get one bounded, targeted search pass. Deterministic cap."""
    if not (ctx.tier.extract_evidence and ctx.staged_edges):
        return
    web_capable = llm_factory.provider_web_capable()
    uncovered = [(f, t) for f, t, level in ctx.staged_edges
                 if level == "confirmed"
                 and not any(f in pair and t in pair for pair in ctx.ev_pairs)]
    targets = uncovered[: ctx.tier.max_layers]
    if not targets:
        return
    ctx.note(f"Hunting evidence for {len(targets)} confirmed edges…")
    # Real Tavily runs get a deeper search for these; injected test
    # gathers are reused as-is. URL dedup is shared with the run.
    ev_gather = ctx.smart if ctx.injected_gather else SmartGather(
        lambda q, k: websearch.gather_context(q, k, depth="advanced"),
        ctx.profile, recent_year=ctx.smart.recent_year, seen=ctx.smart._seen)
    texts = []
    for f, t in targets:
        ctx.check_cancel()
        query = (f"{ctx.company_names[f]} {ctx.company_names[t]} "
                 "supply agreement partnership contract")
        text, _ = ev_gather.gather_query(query, ctx.tier.sources_per_query)
        if text:
            texts.append(text)
    context2 = "\n\n".join(texts)[:10000]
    online2 = False
    if not context2 and ctx.online_fallback:
        context2 = "(no scraped text — search the web for evidence)"
        online2 = True
    if not context2:
        return
    try:
        data = ctx.llm(prompts.evidence_and_deals(
            context2, ", ".join(ctx.company_names.values()), False),
            online=online2, max_tokens=8000)
    except Exception as exc:
        ctx.skip("targeted evidence hunt", exc)
        data = {}
    added = _stage_evidence_items(
        ctx, data,
        provenance=("model_knowledge"
                    if online2 and not web_capable else "scraped"))
    ctx.note(f"  {added} targeted evidence items proposed.")


def _stage_evidence_isr(ctx: StageCtx) -> None:
    """Stage 5c — ISR maintenance evidence (offline, bounded). Weekly
    screenings of ISR-grounded companies become evidence proposals with dates
    and source URLs. The ASCII "[ISR <ticker> <week>]" claim prefix is
    load-bearing: slugify collapses CJK reasons to nothing, so the prefix
    keeps ref/merge keys unique and re-runs idempotent."""
    if ctx.isr is None or not ctx.isr_hits:
        return
    isr_evidence = 0
    for cref, hit in ctx.isr_hits.items():
        ctx.check_cancel()
        if cref not in ctx.company_names:
            continue  # screened out above
        for sc in ctx.isr.screenings(hit, needs_update_only=True, limit=3):
            claim = (f"[ISR {hit.base_ticker or hit.name} "
                     f"{sc.week_start}] {sc.reason}")
            url = sc.source_urls[0] if sc.source_urls else ""
            ctx.add_proposal("evidence", entity_slug(claim)[:40] or "ev", {
                "source_type": "analyst_reports", "source_url": url,
                "publisher": "ISR maintenance screening",
                "date": sc.evidence_date or sc.week_start,
                "claim": claim, "quote": "",
                "related_companies": [cref],
                "evidence_strength": "moderate",
                "provenance": "isr",
                "confidence_score": 0.6,
            }, "", 0.6, [f"isr:{hit.base_ticker or hit.name}",
                         *sc.source_urls])
            ctx.ev_claims.append(f"- {claim} [{ctx.company_names[cref]}] {url}")
            ctx.ev_pairs.append({cref})
            isr_evidence += 1
    if isr_evidence:
        ctx.note(f"  {isr_evidence} ISR evidence items proposed.")


def _stage_contradictions(ctx: StageCtx) -> None:
    """Stage 6 — contradiction pass (deep tier): one bounded call checks the
    claimed edges against the collected evidence; contradicted edges are
    downgraded to "weak" before they ever reach the graph."""
    if not (ctx.tier.contradiction_pass and ctx.staged_edges and ctx.ev_claims):
        return
    ctx.note("Checking for contradictions…")
    edges_block = "\n".join(
        f"- {ctx.company_names[f]} → {ctx.company_names[t]} ({level})"
        for f, t, level in ctx.staged_edges)
    try:
        data = ctx.llm(prompts.contradictions(edges_block, "\n".join(ctx.ev_claims)),
                       max_tokens=2000)
    except Exception as exc:
        ctx.skip("contradiction check", exc)
        data = {}
    # Fetch the run's edge proposals ONCE (was a full list_proposals per
    # contradiction); keep the updated objects so repeated contradictions of
    # the same edge still accumulate rationale.
    edge_by_ref = {p.ref_key: p
                   for p in repo.list_proposals(ctx.request_id, run_id=ctx.run.id)
                   if p.entity_type == "edge"}
    flagged = 0
    for item in data.get("contradictions", []):
        fref, tref = entity_slug(item.get("from", "")), entity_slug(item.get("to", ""))
        issue = item.get("issue", "conflicting evidence")
        p = edge_by_ref.get(f"{fref}->{tref}")
        if p is None:
            continue
        p.payload = {**p.payload, "evidence_level": "weak"}
        p.rationale = (p.rationale + " ⚠ contradicted: " + issue).strip()
        edge_by_ref[p.ref_key] = repo.update(p)
        ctx.note(f"  ⚠ contradiction: {ctx.company_names.get(fref, fref)} → "
                 f"{ctx.company_names.get(tref, tref)} — {issue}")
        flagged += 1
    ctx.note(f"  {flagged} edges downgraded." if flagged else "  none found.")


def _is_credentials_error(exc: Exception) -> bool:
    """Recognize provider credential failures without coupling to one SDK.
    Covers both missing keys ("API_KEY is not set") and rejected keys
    (401/403 Unauthorized / invalid api key / authentication)."""
    current: BaseException | None = exc
    while current is not None:
        text = str(current).casefold()
        if (
            ("api_key" in text or "api key" in text)
            and any(word in text for word in
                    ("not set", "missing", "not configured", "rejected",
                     "invalid", "check"))
        ) or "credential" in text or "unauthorized" in text \
                or "authentication" in text or "401" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _successful_outcome(
    proposals: list,
    skips: list[str],
    credential_blocked: bool,
) -> ResearchRunOutcome:
    if credential_blocked:
        return ResearchRunOutcome.NEEDS_CREDENTIALS
    if not any(p.entity_type == "process_node" for p in proposals):
        return ResearchRunOutcome.NO_CREDIBLE_RESULTS
    if skips:
        return ResearchRunOutcome.PARTIAL
    return ResearchRunOutcome.COMPLETE


def _finish(
    run: ResearchRun,
    outcome: ResearchRunOutcome,
    log: list,
    note: Progress,
    *,
    skips: Optional[list[str]] = None,
    prefix: str = "",
    usage: Optional[llm_factory.UsageTracker] = None,
    proposals: Optional[list] = None,
) -> ResearchRun:
    if proposals is None:
        proposals = repo.list_proposals(run.request_id, run_id=run.id)
    counts: dict[str, int] = {}
    for p in proposals:
        counts[p.entity_type] = counts.get(p.entity_type, 0) + 1
    count_text = ", ".join(f"{k} {v}" for k, v in counts.items())
    skipped = list(skips or [])
    if outcome == ResearchRunOutcome.COMPLETE:
        detail = count_text or "nothing proposed"
    elif outcome == ResearchRunOutcome.PARTIAL:
        detail = (count_text or "partial proposals saved")
        if skipped:
            detail += ". Skipped: " + "; ".join(skipped)
    elif outcome == ResearchRunOutcome.NEEDS_CREDENTIALS:
        detail = "Request saved"
        if skipped:
            detail += ". Unavailable: " + "; ".join(skipped)
    elif outcome == ResearchRunOutcome.NO_CREDIBLE_RESULTS:
        detail = "No usable process nodes were proposed; request saved for refinement"
    elif outcome == ResearchRunOutcome.CANCELLED:
        detail = (count_text + "; cancelled by user — staged items kept"
                  if count_text else "cancelled by user before anything was staged")
    else:
        detail = "No usable partial map was produced"
        if skipped:
            detail += ". Error: " + "; ".join(skipped)
    run.status = outcome.value
    run.counts = counts
    label = outcome.value.replace("_", " ")
    run.summary = f"{label}: " + (f"{prefix}; " if prefix else "") + detail
    # Cost/latency visibility — only when real provider calls happened, so
    # offline smoke summaries stay byte-identical.
    if usage is not None and usage.calls > 0:
        run.usage = usage.as_dict()
        elapsed = (datetime.now() - run.started_at).total_seconds()
        run.summary += f" · {int(elapsed // 60)}m{int(elapsed % 60):02d}s · {usage.calls} LLM calls"
    note(run.summary)
    run.log = log
    run.finished_at = datetime.now()
    return repo.update(run)
