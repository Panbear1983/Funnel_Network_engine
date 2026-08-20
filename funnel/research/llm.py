"""Provider dispatch: bind a model id into the `llm(messages, online=False)`
callable the pipeline expects, using whichever backend FUNNEL_LLM_PROVIDER
selects — 'nvidia' (default for this repo), 'anthropic' (native Claude), or
'openrouter'."""

from __future__ import annotations

from typing import Callable

from funnel import config

# Which providers can fall back to their own web search when scraping is
# unavailable. NVIDIA's endpoint has none: `online=True` is accepted-and-ignored.
PROVIDER_WEB_SEARCH = {"nvidia": False, "openrouter": True, "anthropic": True}


def provider_web_capable() -> bool:
    """True when the active provider can search the web itself (so thin-scrape
    evidence is still source-backed rather than model-recalled)."""
    return PROVIDER_WEB_SEARCH.get(config.llm_provider(), False)


def make_llm(model: str) -> Callable[..., dict]:
    provider = config.llm_provider()
    if provider == "nvidia":
        from funnel.research import nvidia
        return nvidia.make_llm(model)
    if provider == "openrouter":
        from funnel.research import openrouter
        return openrouter.make_llm(model)
    from funnel.research import claude
    return claude.make_llm(model)
