"""Provider dispatch: bind a model id into the `llm(messages, online=False)`
callable the pipeline expects, using whichever backend FUNNEL_LLM_PROVIDER
selects — 'nvidia' (default for this repo), 'anthropic' (native Claude), or
'openrouter'."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from funnel import config


@dataclass
class UsageTracker:
    """Accumulates LLM usage across a run. Provider clients report each
    successful call via .add(); int += under the GIL is safe enough for the
    single-worker runs this app performs."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, usage: dict) -> None:
        self.calls += usage.get("calls", 0) or 0
        self.prompt_tokens += usage.get("prompt_tokens", 0) or 0
        self.completion_tokens += usage.get("completion_tokens", 0) or 0

    def as_dict(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens}

# Which providers can fall back to their own web search when scraping is
# unavailable. NVIDIA's endpoint has none: `online=True` is accepted-and-ignored.
PROVIDER_WEB_SEARCH = {"nvidia": False, "openrouter": True, "anthropic": True}


def provider_web_capable() -> bool:
    """True when the active provider can search the web itself (so thin-scrape
    evidence is still source-backed rather than model-recalled)."""
    return PROVIDER_WEB_SEARCH.get(config.llm_provider(), False)


def make_llm(model: str, *, usage: Optional[UsageTracker] = None) -> Callable[..., dict]:
    provider = config.llm_provider()
    on_usage = usage.add if usage is not None else None
    if provider == "nvidia":
        from funnel.research import nvidia
        return nvidia.make_llm(model, on_usage=on_usage)
    if provider == "openrouter":
        from funnel.research import openrouter
        return openrouter.make_llm(model, on_usage=on_usage)
    from funnel.research import claude
    return claude.make_llm(model, on_usage=on_usage)
