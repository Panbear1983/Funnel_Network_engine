"""Company intelligence enrichment (Phase C, v2.0-lite).

Two independent passes, both graceful no-ops when their backing is absent:

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
from typing import Callable, Optional

from funnel.db import repo
from funnel.db.models import CompanyNode
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


def _ticker_variants(ticker: str) -> list[str]:
    """The raw ticker plus a dash→dot exchange-suffix variant models emit
    ('6680-HK' → '6680.HK')."""
    variants = [ticker]
    alt = re.sub(r"-([A-Z]{1,3})$", r".\1", ticker)
    if alt != ticker:
        variants.append(alt)
    return variants


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
    counts = {"financials": 0, "estimates": 0}

    # Pass 1 — fundamentals for tickered companies missing them.
    for c in companies:
        if not c.ticker or c.financials:
            continue
        data = fetch(c.ticker)
        if data:
            c.financials = data
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
        data = llm(prompts.company_estimates(block))
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
