"""Provider dispatch: bind a model id into the `llm(messages, online=False)`
callable the pipeline expects, using whichever backend FUNNEL_LLM_PROVIDER
selects — 'nvidia' (default for this repo), 'anthropic' (native Claude), or
'openrouter'."""

from __future__ import annotations

from typing import Callable

from funnel import config


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
