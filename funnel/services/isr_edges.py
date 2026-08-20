"""Phase 5 — mine supplier/customer/competitor relationships out of the ISR
dossier's Traditional-Chinese prose ("Clients & Orders", "Key Competitors")
for one company, as reviewable edge Proposals.

Design constraints that make this correct in this codebase:
- `ingest.slugify` collapses every pure-CJK name to "x", so counterparty
  ref_keys are derived from TICKERS, never raw Chinese names (the same trick
  Phase 3 evidence uses with its ASCII claim prefix).
- `commit_proposals` silently SKIPS any edge whose endpoints aren't both in
  `company_map`, so this miner co-proposes BOTH endpoints as company proposals
  (the subject merges with the existing node; a counterparty either merges with
  an existing request company matched by ticker, or is created).
- Everything is staged as pending Proposals behind the existing human review +
  idempotent merge; edges are capped at evidence_level "inferred", confidence
  <= 0.5. Counterparties with no resolvable ticker are dropped (name-only ISR
  resolution is unreliable), so speculative CJK-name companies never enter.

`mine_isr_relationships` is PURE (returns proposal dicts) so it runs offline in
tests; `run_isr_edge_mining` orchestrates a real run (open ISR, call the LLM,
stage + commit).
"""

from __future__ import annotations

from typing import Callable, Optional

from funnel import config
from funnel.db import repo
from funnel.db.models import CompanyNode, Proposal, ResearchRun
from funnel.knowledge.isr import IsrKnowledge, get_isr, split_ticker, yahoo_ticker
from funnel.research import llm as llm_factory
from funnel.research import prompts
from funnel.research.ingest import commit_proposals, entity_slug, slugify
from funnel.research.tiers import get_tier, resolve_model

Progress = Callable[[str], None]

# counterparty relationship -> a valid CompanyNode.role for the created company.
_ROLE_FOR_REL = {
    "customer": "customer", "supplier": "supplier",
    "distributor": "distributor", "partner": "partner", "contractor": "supplier",
}


def _f(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ticker_slug(ticker: str) -> str:
    """Stable ASCII ref_key from a ticker, e.g. '2330.TW' -> '2330_tw'."""
    return slugify(ticker) or "x"


def _ykey(ticker: str, country: str = "") -> str:
    """Comparable normalized ticker for dedup against existing companies."""
    return yahoo_ticker((ticker or "").strip(), country or "").upper()


def mine_isr_relationships(
    isr: IsrKnowledge,
    subject_company: CompanyNode,
    dossier: dict,
    llm: Callable[..., dict],
    *,
    request_companies: list[CompanyNode],
    subject_hit,
) -> list[dict]:
    """Return proposal dicts (company + edge) to route through add_proposal.
    Pure: no DB writes, no network beyond the injected `llm`."""
    core = str(dossier.get("Core Business", "") or "")
    clients = str(dossier.get("Clients & Orders", "") or "")
    competitors = str(dossier.get("Key Competitors", "") or "")
    blob = " ".join((core, clients, competitors))

    subj_ref = entity_slug(subject_company.name)
    subj_yk = (_ykey(subject_company.ticker, subject_company.country)
               if subject_company.ticker else None)
    isr_tag = f"isr:{subject_hit.base_ticker or subject_hit.name}"

    # existing request companies, indexed by normalized ticker, for dedup.
    existing_by_ticker: dict[str, CompanyNode] = {
        _ykey(c.ticker, c.country): c for c in request_companies if c.ticker
    }

    companies: list[dict] = [{
        "entity_type": "company", "ref_key": subj_ref,
        "payload": {
            "name": subject_company.name, "ticker": subject_company.ticker,
            "country": subject_company.country,
            "role": subject_company.role or "operator", "process_node_ids": [],
            "confidence_score": subject_company.confidence_score,
        },
        "rationale": "", "confidence": subject_company.confidence_score,
        "sources": [],
    }]
    edges: list[dict] = []
    seen_company_refs = {subj_ref}
    seen_edge_keys: set = set()

    data = llm(prompts.isr_relationships(subject_hit.label, core, clients, competitors), max_tokens=4000)

    for e in data.get("edges", []):
        quote = str(e.get("quote") or "").strip()
        # Hallucination guard: require a verbatim quote from the source text.
        # An edge with no quote has no textual support, so drop it too.
        if not quote or quote not in blob:
            continue
        cp_name = str(e.get("counterparty_name") or "").strip()
        cp_ticker = str(e.get("counterparty_ticker") or "").strip()
        rel = e.get("relationship_type", "competitor")

        # Resolve the counterparty to an ISR company (ticker wins over name).
        cp_hit = isr.lookup(cp_ticker) if cp_ticker else None
        if cp_hit is None and cp_name:
            nh = isr.lookup(cp_name)
            # collision guard: reject a name hit that disagrees with an inline ticker
            if (nh is not None and cp_ticker and nh.base_ticker
                    and split_ticker(cp_ticker)[0].upper() != nh.base_ticker.upper()):
                nh = None
            cp_hit = nh

        # Prefer the ISR canonical yahoo ticker so bare/suffixed forms unify.
        usable_ticker = (cp_hit.yahoo_ticker if cp_hit else "") or cp_ticker
        if not usable_ticker:
            continue  # no resolvable ticker -> drop (never invent a CJK company)

        yk = _ykey(usable_ticker, cp_hit.country if cp_hit else "")
        if subj_yk and yk == subj_yk:
            continue  # the subject named itself as a counterparty
        existing = existing_by_ticker.get(yk)
        if existing is not None:
            cp_ref = entity_slug(existing.name)
            cp_payload = {"name": existing.name, "ticker": existing.ticker,
                          "country": existing.country,
                          "role": existing.role or "operator"}
        else:
            cp_ref = _ticker_slug(cp_hit.yahoo_ticker if cp_hit else usable_ticker)
            label = (cp_hit.label if cp_hit
                     else (f"{cp_name} ({cp_ticker})" if cp_ticker else cp_name))
            cp_payload = {"name": label,
                          "ticker": (cp_hit.yahoo_ticker if cp_hit else usable_ticker),
                          "country": (cp_hit.country if cp_hit else ""),
                          "role": _ROLE_FOR_REL.get(rel, "operator")}

        if cp_ref == subj_ref:
            continue  # self-edge

        # co-propose the counterparty so commit_proposals can resolve the edge
        if cp_ref not in seen_company_refs:
            seen_company_refs.add(cp_ref)
            companies.append({
                "entity_type": "company", "ref_key": cp_ref,
                "payload": {**cp_payload, "ticker": cp_payload["ticker"] or None,
                            "country": cp_payload["country"] or None,
                            "process_node_ids": [], "confidence_score": 0.4},
                "rationale": f"[ISR-edge {subject_hit.base_ticker}] {quote}"[:200],
                "confidence": 0.4, "sources": [isr_tag],
            })

        # Direction: competitor is symmetric -> canonical ordering makes
        # cross-dossier mining idempotent; others keep their real direction.
        if rel == "competitor":
            fref, tref = sorted((subj_ref, cp_ref))
        elif e.get("direction") == "counterparty_to_subject":
            fref, tref = cp_ref, subj_ref
        else:
            fref, tref = subj_ref, cp_ref

        ekey = (fref, tref, rel)
        if ekey in seen_edge_keys:
            continue
        seen_edge_keys.add(ekey)
        conf = min(_f(e.get("confidence"), 0.4), 0.5)
        edges.append({
            "entity_type": "edge", "ref_key": f"{fref}->{tref}",
            "payload": {"from_company": fref, "to_company": tref,
                        "relationship_type": rel, "evidence_level": "inferred",
                        "confidence_score": conf},
            "rationale": f"[ISR-edge {subject_hit.base_ticker}] {quote}"[:200],
            "confidence": conf, "sources": [isr_tag],
        })

    return companies + edges


def run_isr_edge_mining(
    request_id: str,
    company_id: str,
    *,
    llm: Optional[Callable[..., dict]] = None,
    isr: Optional[IsrKnowledge] = None,
    progress: Optional[Progress] = None,
) -> dict:
    """Mine one company's ISR dossier and commit the resulting edge/company
    proposals. Returns {status, ...counts}. Graceful when ISR/dossier/keys are
    absent (status set, nothing committed)."""
    def note(msg: str) -> None:
        if progress:
            progress(msg)

    isr = isr or get_isr()
    if isr is None:
        return {"status": "no_isr", "company": 0, "edge": 0}
    subject = repo.get(CompanyNode, company_id)
    if subject is None:
        return {"status": "no_company", "company": 0, "edge": 0}

    subject_hit = isr.lookup_company(subject.name, subject.ticker,
                                     country=subject.country)
    dossier = isr.dossier(subject_hit) if subject_hit else None
    if not dossier:
        return {"status": "no_dossier", "company": 0, "edge": 0}

    if llm is None:
        if not config.llm_api_key():
            return {"status": "needs_credentials", "company": 0, "edge": 0}
        llm = llm_factory.make_llm(resolve_model(get_tier("professional")))

    request_companies = repo.list_for_request(CompanyNode, request_id)
    note(f"Mining ISR relationships for {subject_hit.label}…")
    try:
        proposals = mine_isr_relationships(
            isr, subject, dossier, llm,
            request_companies=request_companies, subject_hit=subject_hit)
    except Exception as exc:  # one bad LLM response must not corrupt anything
        note(f"ISR mining failed: {type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc), "company": 0, "edge": 0}

    # Fresh run so only these proposals commit (no stragglers).
    run = repo.add(ResearchRun(request_id=request_id, tier="manual",
                               model="isr-edges", status="complete"))
    for pr in proposals:
        repo.add(Proposal(
            run_id=run.id, request_id=request_id, entity_type=pr["entity_type"],
            ref_key=pr["ref_key"], payload=pr["payload"],
            rationale=pr["rationale"], confidence=pr["confidence"],
            sources=pr["sources"], status="pending"))
    counts = commit_proposals(request_id, run_id=run.id)
    note(f"ISR mining: +{counts.get('edge', 0)} edges, "
         f"+{counts.get('company', 0)} companies "
         f"({counts.get('merged', 0)} merged).")
    return {"status": "ok", **counts}
