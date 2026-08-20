"""Company intelligence enrichment (Phase C, v2.0-lite).

Three independent passes, all graceful no-ops when their backing is absent:

0. ISR knowledge — the sibling ISR research DB (read-only) fills empty
   ticker/exchange/country and stashes the company dossier under
   `financials["isr"]` (write-once; never overwrites).
1. Fundamentals — yfinance (optional `intel` extra) fills
   `CompanyNode.financials` (market cap, revenue, gross margin) for tickered
   companies. Lazy import; any failure leaves `{}`.
2. Estimates — one LLM call proposes `revenue_purity_estimate` and
   `market_share_estimate` for the highest-confidence companies that lack
   them. Manual values are never overwritten. The existing scorer already
   weights both fields, so relevance rankings sharpen automatically.

Skip everything with FUNNEL_SKIP_ENRICH=1.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Callable, Optional

from funnel.db import repo
from funnel.db.models import CompanyNode
from funnel.knowledge.isr import IsrKnowledge, exchange_label
from funnel.research import prompts

Progress = Callable[[str], None]

_FIN_KEYS = (("marketCap", "market_cap"), ("totalRevenue", "revenue"),
             ("grossMargins", "gross_margin"), ("currency", "currency"))


def fetch_financials(ticker: str) -> dict:
    """Best-effort fundamentals for one ticker via yfinance. {} on any failure
    (missing extra, network trouble, unknown symbol)."""
    try:
        import yfinance
    except ImportError:
        return {}
    for symbol in _ticker_variants(ticker):
        try:
            info = yfinance.Ticker(symbol).info or {}
        except Exception:
            continue
        out = {dst: info[src] for src, dst in _FIN_KEYS
               if info.get(src) is not None}
        if out.get("market_cap") or out.get("revenue"):
            out["symbol"] = symbol
            return out
    return {}


# Model-emitted exchange suffixes -> Yahoo Finance suffixes.
_SUFFIX_MAP = {"KR": "KS", "JP": "T", "FR": "PA", "SE": "ST", "UK": "L", "GB": "L",
               "TW": "TW", "HK": "HK", "DE": "DE", "IT": "MI", "NL": "AS"}
_EXCHANGE_PREFIX = {"SZSE": "SZ", "SSE": "SS", "TSE": "T", "KRX": "KS"}


def enrich_from_isr(companies: list[CompanyNode],
                    isr: Optional[IsrKnowledge],
                    progress: Optional[Progress] = None) -> int:
    """Pass 0 — fill empty identity fields from the ISR knowledge base and
    stash the dossier under financials['isr'] (write-once)."""
    if isr is None:
        return 0
    filled = 0
    for c in companies:
        hit = None
        if c.ticker:
            hit = isr.lookup(c.ticker, country=c.country)
        if hit is None and c.name:
            hit = isr.lookup(c.name, country=c.country)
        if hit is None:
            continue
        changed = False
        if not c.ticker and hit.yahoo_ticker:
            c.ticker = hit.yahoo_ticker
            changed = True
        if not c.country and hit.country:
            c.country = hit.country
            changed = True
        label = exchange_label(hit.ticker) or exchange_label(hit.yahoo_ticker)
        if not c.exchange and label:
            c.exchange = label
            changed = True
        financials = dict(c.financials or {})
        if "isr" not in financials:
            financials["isr"] = {
                "ticker": hit.ticker,
                "tier": hit.tier,
                "industry": hit.industry,
                "dossier": isr.dossier(hit) or {},
                "dossier_updated_at": hit.dossier_updated_at,
                "retrieved_at": datetime.now().isoformat(timespec="seconds"),
            }
            c.financials = financials
            changed = True
        if changed:
            repo.update(c)
            filled += 1
    if filled and progress:
        progress(f"ISR knowledge filled {filled} companies.")
    return filled


def _ticker_variants(ticker: str) -> list[str]:
    """Yahoo-compatible candidates for a model-emitted ticker: the raw form,
    dash→dot, country-suffix remaps ('005930-KR' → '005930.KS'), and
    exchange-prefix forms ('SZSE: 300953' → '300953.SZ')."""
    ticker = ticker.strip()
    variants = [ticker]
    prefix = re.match(r"^([A-Z]{2,5})\s*:\s*(\S+)$", ticker)
    if prefix and prefix.group(1) in _EXCHANGE_PREFIX:
        variants.append(f"{prefix.group(2)}.{_EXCHANGE_PREFIX[prefix.group(1)]}")
    suffix = re.match(r"^(.+?)[-.]([A-Z]{2})$", ticker)
    if suffix:
        base, cc = suffix.groups()
        mapped = _SUFFIX_MAP.get(cc)
        if mapped:
            variants.append(f"{base}.{mapped}")
        variants.append(f"{base}.{cc}")
    return list(dict.fromkeys(variants))  # dedup, keep order


def enrich_companies(
    request_id: str,
    *,
    llm=None,
    progress: Optional[Progress] = None,
    fetch=fetch_financials,
    top_n: int = 8,
) -> dict:
    """Run both passes over a request's committed companies. Returns counts
    {'financials': n, 'estimates': m}."""
    if os.environ.get("FUNNEL_SKIP_ENRICH") == "1":
        return {"financials": 0, "estimates": 0}

    def note(msg: str) -> None:
        if progress:
            progress(msg)

    companies = repo.list_for_request(CompanyNode, request_id)
    counts = {"isr": 0, "financials": 0, "estimates": 0}

    # Pass 0 — ISR knowledge base (runs first so ISR-filled tickers feed
    # the fundamentals pass).
    counts["isr"] = enrich_from_isr(companies, IsrKnowledge.open_default(),
                                    progress=note)

    # Pass 1 — fundamentals for tickered companies missing them. The stored
    # ISR dossier doesn't count as fundamentals, and fetched data merges so
    # it never clobbers the "isr" key.
    for c in companies:
        has_fundamentals = any(k != "isr" for k in (c.financials or {}))
        if not c.ticker or has_fundamentals:
            continue
        data = fetch(c.ticker)
        if data:
            c.financials = {**(c.financials or {}), **data}
            repo.update(c)
            counts["financials"] += 1
    if counts["financials"]:
        note(f"Fundamentals fetched for {counts['financials']} companies.")

    # Pass 2 — LLM estimates for the top candidates lacking them.
    if llm is None:
        return counts
    candidates = sorted(
        (c for c in companies
         if c.revenue_purity_estimate is None or c.market_share_estimate is None),
        key=lambda c: c.confidence_score, reverse=True)[:top_n]
    if not candidates:
        return counts
    try:
        block = "\n".join(f"- {c.name} ({c.role})" for c in candidates)
        data = llm(prompts.company_estimates(block), max_tokens=2000)
    except Exception as exc:
        note(f"Estimates skipped ({type(exc).__name__}).")
        return counts
    by_name = {c.name.strip().lower(): c for c in candidates}
    for est in data.get("estimates", []):
        c = by_name.get(str(est.get("name", "")).strip().lower())
        if c is None:
            continue
        changed = False
        for src, attr in (("revenue_purity", "revenue_purity_estimate"),
                          ("market_share", "market_share_estimate")):
            value = est.get(src)
            if value is None or getattr(c, attr) is not None:
                continue  # never overwrite an existing (possibly manual) value
            try:
                setattr(c, attr, max(0.0, min(1.0, float(value))))
                changed = True
            except (TypeError, ValueError):
                pass
        if changed:
            repo.update(c)
            counts["estimates"] += 1
    if counts["estimates"]:
        note(f"Purity/share estimated for {counts['estimates']} companies.")
    return counts
