"""Deterministic research pipeline. A fixed sequence of stages; each LLM call
returns strict JSON. The `llm` and `gather` callables are injected so the whole
thing runs offline in tests. Everything is written as pending Proposals for
human review — nothing touches the real graph here."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from funnel.db import repo
from funnel.db.models import Proposal, ResearchRequest, ResearchRun
from funnel.research import llm as llm_factory
from funnel.research import prompts, screen, smartsearch, websearch
from funnel.research.ingest import slugify
from funnel.research.smartsearch import SmartGather, TopicProfile
from funnel.research.tiers import get_tier, resolve_model

# llm(messages, *, online=False) -> dict ; gather(query, k) -> (text, urls)
LLM = Callable[..., dict]
Gather = Callable[[str, int], tuple]
Progress = Callable[[str], None]


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
) -> ResearchRun:
    tier = get_tier(tier_key)
    model = resolve_model(tier)
    if llm is None:
        llm = llm_factory.make_llm(model)
    if gather is None:
        gather = websearch.gather_context
    smart = SmartGather(gather, profile)
    log: list[str] = []

    def note(msg: str) -> None:
        log.append(msg)
        if progress:
            progress(msg)

    request = repo.get(ResearchRequest, request_id)
    run = repo.add(ResearchRun(request_id=request_id, tier=tier_key, model=model, status="running"))

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
        note(f"Brief built for '{request.title or request.industry_of_interest or request_id}'.")

        # Stage 2 — process stack
        note("Discovering process stack…")
        data = llm(prompts.process_stack(brief, tier.max_layers, tier.expand_inputs))
        nodes = data.get("process_nodes", [])[: tier.max_layers]
        node_refs = []
        for n in nodes:
            ref = slugify(n.get("name", ""))
            node_refs.append((ref, n.get("name", "")))
            add_proposal("process_node", ref, {
                "name": n.get("name", ""), "layer": n.get("layer", "midstream"),
                "description": n.get("description", ""), "keywords": n.get("keywords", []),
                "bottleneck_risk": n.get("bottleneck_risk", "unknown"), "confidence_score": 0.5,
            }, n.get("rationale", ""), 0.5, [])
        note(f"  {len(node_refs)} process nodes proposed.")

        if dry_run:
            return _finish(run, "complete (dry-run)", log, note)

        # Stage 2.5 — smart source planning: intent-tagged queries generated
        # from the topic (and its alias/jargon profile), not fixed templates.
        note("Planning smart search queries…")
        query_plan = smartsearch.plan_queries(llm, brief, node_refs, tier, profile=profile)
        note(f"  smart queries planned for {len(query_plan)} nodes"
             + ("" if query_plan else " (falling back to templates)") + ".")

        # Stage 3 — companies per node (dedup companies found under multiple nodes)
        company_names: dict[str, str] = {}  # ref_key -> name
        companies_acc: dict[str, dict] = {}  # ref_key -> accumulated proposal info
        context_pool: list[str] = []
        for ref, name in node_refs:
            note(f"Researching companies for '{name}'…")
            text, urls = smart.gather_node(name, query_plan.get(ref),
                                           tier.sources_per_query,
                                           _queries(name, request, tier))
            if text:
                context_pool.append(text)
            online = online_fallback and not text
            data = llm(
                prompts.company_discovery(name, brief, text or "(no scraped text available)",
                                          tier.companies_per_node),
                online=online,
            )
            comps = data.get("companies", [])[: tier.companies_per_node]
            for c in comps:
                cref = slugify(c.get("name", ""))
                if not cref:
                    continue
                company_names[cref] = c.get("name", "")
                conf = _f(c.get("confidence"), 0.5)
                if cref in companies_acc:
                    payload = companies_acc[cref]["payload"]
                    if ref not in payload["process_node_ids"]:
                        payload["process_node_ids"].append(ref)
                    companies_acc[cref]["confidence"] = max(companies_acc[cref]["confidence"], conf)
                    payload["confidence_score"] = companies_acc[cref]["confidence"]
                else:
                    companies_acc[cref] = {
                        "payload": {
                            "name": c.get("name", ""), "ticker": c.get("ticker"),
                            "country": c.get("country"), "role": c.get("role", "operator"),
                            "process_node_ids": [ref], "confidence_score": conf,
                        },
                        "rationale": c.get("rationale", ""), "confidence": conf,
                        "sources": c.get("sources", urls),
                    }
            note(f"  {len(comps)} companies found for '{name}'.")
        screened = 0
        for cref, info in companies_acc.items():
            reason = screen.screen_company(info["payload"])
            if reason:
                # Stage it visibly as rejected (restorable) and keep it out of
                # the relationship/evidence stages entirely.
                add_proposal("company", cref, info["payload"],
                             f"auto-rejected: {reason}", info["confidence"],
                             info["sources"], status="rejected")
                company_names.pop(cref, None)
                screened += 1
                continue
            add_proposal("company", cref, info["payload"], info["rationale"],
                         info["confidence"], info["sources"])
        note(f"  {len(companies_acc) - screened} unique companies proposed"
             + (f" ({screened} auto-rejected as non-companies)" if screened else "") + ".")

        # Stage 4 — relationships
        if len(company_names) >= 2:
            note("Inferring relationships…")
            lines = "\n".join(f"- {name}" for name in company_names.values())
            data = llm(prompts.relationships(lines, brief))
            edges = data.get("edges", [])
            kept = 0
            for e in edges:
                fref, tref = slugify(e.get("from", "")), slugify(e.get("to", ""))
                if fref not in company_names or tref not in company_names or fref == tref:
                    continue
                conf = _f(e.get("confidence"), 0.4)
                add_proposal("edge", f"{fref}->{tref}", {
                    "from_company": fref, "to_company": tref,
                    "relationship_type": e.get("relationship_type", "supplier"),
                    "evidence_level": e.get("evidence_level", "inferred"), "confidence_score": conf,
                }, e.get("rationale", ""), conf, [])
                kept += 1
            note(f"  {kept} relationships proposed.")

        # Stage 5 — evidence + deals. With no scraped context (e.g. no Tavily
        # key) the model's own web search stands in, so evidence is never
        # silently skipped on evidence-bearing tiers.
        context = "\n\n".join(context_pool)[:12000]
        evidence_online = False
        if tier.extract_evidence and not context and online_fallback:
            context = "(no scraped text available — search the web for evidence)"
            evidence_online = True
        if tier.extract_evidence and context and company_names:
            note("Extracting evidence…" + (" (model web search)" if evidence_online else ""))
            names = ", ".join(company_names.values())
            data = llm(prompts.evidence_and_deals(context, names, tier.extract_deals),
                       online=evidence_online)
            for ev in data.get("evidence", []):
                related = [r for r in (slugify(x) for x in ev.get("related_companies", [])) if r in company_names]
                url = ev.get("source_url", "")
                add_proposal("evidence", (slugify(ev.get("claim", ""))[:40] or "ev"), {
                    "source_type": ev.get("source_type", "news"), "source_url": url,
                    "publisher": ev.get("publisher", ""), "date": ev.get("date", ""),
                    "claim": ev.get("claim", ""), "quote": ev.get("quote", ""),
                    "related_companies": related, "evidence_strength": ev.get("evidence_strength", "moderate"),
                    "confidence_score": 0.6,
                }, "", 0.6, [url] if url else [])
            for dl in data.get("deals", []):
                add_proposal("deal", (slugify(dl.get("buyer", "") + "_" + dl.get("supplier", "")) or "deal"), {
                    "deal_type": dl.get("deal_type", "partnership"),
                    "buyer": slugify(dl.get("buyer", "")), "supplier": slugify(dl.get("supplier", "")),
                    "product_or_service": dl.get("product_or_service", ""),
                    "announced_date": dl.get("announced_date", ""), "deal_value": dl.get("deal_value"),
                    "confidence_score": 0.5,
                }, "", 0.5, [])
            note("  evidence/deals proposed.")

        return _finish(run, "complete", log, note)
    except Exception as exc:
        run.status = "failed"
        run.summary = f"{type(exc).__name__}: {exc}"
        run.log = log
        run.finished_at = datetime.now()
        return repo.update(run)


def _finish(run: ResearchRun, status: str, log: list, note: Progress) -> ResearchRun:
    proposals = repo.list_proposals(run.request_id, run_id=run.id)
    counts: dict[str, int] = {}
    for p in proposals:
        counts[p.entity_type] = counts.get(p.entity_type, 0) + 1
    run.status = "complete"
    run.counts = counts
    run.summary = f"{status}: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "nothing proposed")
    run.log = log
    run.finished_at = datetime.now()
    saved = repo.update(run)
    note(run.summary)
    return saved
