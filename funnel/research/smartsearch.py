"""Smart OSINT search: topic-aware query planning and gathering.

Builds on the same DI seams as the pipeline — any `gather(query, k)` callable
(real Tavily+scraper or a test fake) can sit underneath. This layer adds:

- a TopicProfile (aliases / jargon / key products) extracted by the auto-brief,
- intent-tagged queries (news / filings / forums / market) so each search is
  phrased for the kind of source it targets,
- cross-node URL de-duplication within one run,
- one bounded alias-retry when a node's gathered text comes back thin.

Everything is deterministic: no loops, fixed call counts per node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from funnel.research import prompts
from funnel.research.ingest import slugify

Gather = Callable[[str, int], tuple]

# How each intent re-phrases its query for the open web. Encoded purely in the
# query string so any base gather (Tavily, a fake, anything) benefits.
INTENT_SUFFIX = {
    "news": "latest news",
    "filings": "investor relations annual report 10-K",
    "forums": "forum discussion reddit",
    "market": "market share leading suppliers report",
}

THIN_TEXT_CHARS = 400  # below this, a node's gather is considered thin


@dataclass
class TopicProfile:
    """Topic vocabulary from the auto-brief, used to sharpen search queries."""
    aliases: list[str] = field(default_factory=list)
    jargon: list[str] = field(default_factory=list)
    key_products: list[str] = field(default_factory=list)

    @classmethod
    def from_brief(cls, data: dict) -> "TopicProfile":
        def strs(key: str) -> list[str]:
            return [s.strip() for s in (data.get(key) or [])
                    if isinstance(s, str) and s.strip()]
        return cls(aliases=strs("aliases"), jargon=strs("jargon"),
                   key_products=strs("key_products"))

    def as_dict(self) -> dict:
        return {"aliases": self.aliases, "jargon": self.jargon,
                "key_products": self.key_products}


@dataclass
class PlannedQuery:
    q: str
    intent: str = "market"

    def phrased(self) -> str:
        suffix = INTENT_SUFFIX.get(self.intent, "")
        return f"{self.q} {suffix}".strip()


def parse_plans(data: dict, max_queries: int) -> dict[str, list[PlannedQuery]]:
    """Tolerant parse of the search-plan JSON: query entries may be plain
    strings (legacy shape) or {"q", "intent"} objects."""
    plans: dict[str, list[PlannedQuery]] = {}
    for plan in data.get("plans", []):
        ref = slugify(plan.get("node", ""))
        queries: list[PlannedQuery] = []
        for entry in plan.get("queries", []):
            if isinstance(entry, str) and entry.strip():
                queries.append(PlannedQuery(q=entry.strip()))
            elif isinstance(entry, dict):
                q = str(entry.get("q") or "").strip()
                if q:
                    intent = str(entry.get("intent") or "market").strip().lower()
                    queries.append(PlannedQuery(q=q, intent=intent))
        if ref and queries:
            plans[ref] = queries[:max_queries]
    return plans


def plan_queries(llm, brief: str, node_refs: list, tier,
                 profile: TopicProfile | None = None) -> dict[str, list[PlannedQuery]]:
    """LLM-planned, intent-tagged queries per node ref. Empty dict on any
    failure (callers fall back to static templates)."""
    try:
        data = llm(prompts.search_plan(
            brief, [name for _, name in node_refs], tier.max_queries_per_node,
            profile=profile.as_dict() if profile else None))
    except Exception:
        return {}
    return parse_plans(data, tier.max_queries_per_node)


class SmartGather:
    """Wrap a base gather callable with intent phrasing, cross-node URL dedup,
    recency biasing, and a single alias-retry for thin nodes."""

    def __init__(self, base: Gather, profile: TopicProfile | None = None,
                 recent_year: int | None = None,
                 seen: set[str] | None = None) -> None:
        self.base = base
        self.profile = profile
        # When set, queries are biased toward recent coverage (the intake's
        # "allow older sources" answer clears this).
        self.recent_year = recent_year
        self._seen: set[str] = seen if seen is not None else set()

    def _run(self, query: str, k: int) -> tuple[str, list[str]]:
        if self.recent_year:
            query = f"{query} {self.recent_year}"
        try:
            text, urls = self.base(query, k)
        except Exception:
            return "", []
        fresh = [u for u in urls if u not in self._seen]
        self._seen.update(urls)
        return text or "", fresh

    def gather_query(self, query: str, k: int) -> tuple[str, list[str]]:
        """One targeted query (e.g. per-edge evidence hunting) with the same
        dedup/recency behavior as node gathering."""
        return self._run(query, k)

    def gather_node(self, node_name: str, queries: list[PlannedQuery] | None,
                    k: int, static_queries: list[str]) -> tuple[str, list[str]]:
        """Gather context for one node. `queries` are the planned smart queries;
        `static_queries` is the template fallback when planning produced none."""
        planned = queries or [PlannedQuery(q=q) for q in static_queries]
        texts: list[str] = []
        urls: list[str] = []
        for pq in planned:
            text, fresh = self._run(pq.phrased(), k)
            if text:
                texts.append(text)
            urls.extend(fresh)
        combined = "\n\n".join(texts)
        # Bounded retry: one extra search using the topic's first alias when
        # this node's context is too thin to extract companies from.
        if len(combined) < THIN_TEXT_CHARS and self.profile and self.profile.aliases:
            alias = self.profile.aliases[0]
            text, fresh = self._run(f"{node_name} {alias} suppliers companies", k)
            if text:
                texts.append(text)
            urls.extend(fresh)
            combined = "\n\n".join(texts)
        return combined[:8000], urls
