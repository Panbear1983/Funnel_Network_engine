"""Research/resolution tiers. A tier governs both how much research is done and
how detailed the tree chart is. Models are env-overridable per provider:
Anthropic (default) uses Claude aliases matched to depth; OpenRouter
(FUNNEL_LLM_PROVIDER=openrouter) defaults every tier to DeepSeek V3."""

from __future__ import annotations

from dataclasses import dataclass

from funnel import config


@dataclass(frozen=True)
class TierConfig:
    key: str
    label: str
    max_layers: int
    companies_per_node: int
    sources_per_query: int
    max_queries_per_node: int
    extract_evidence: bool
    extract_deals: bool
    expand_inputs: bool
    contradiction_pass: bool
    model_env: str
    default_model: str
    chart_resolution: str  # "high" | "professional" | "deep"


TIERS: dict[str, TierConfig] = {
    "high_level": TierConfig(
        "high_level", "High-level", max_layers=4, companies_per_node=3,
        sources_per_query=2, max_queries_per_node=1, extract_evidence=False,
        extract_deals=False, expand_inputs=False, contradiction_pass=False,
        model_env="ANTHROPIC_MODEL_HIGH", default_model="claude-haiku-4-5",
        chart_resolution="high",
    ),
    "professional": TierConfig(
        "professional", "Professional", max_layers=6, companies_per_node=6,
        sources_per_query=3, max_queries_per_node=2, extract_evidence=True,
        extract_deals=False, expand_inputs=True, contradiction_pass=False,
        model_env="ANTHROPIC_MODEL_PRO", default_model="claude-sonnet-4-6",
        chart_resolution="professional",
    ),
    "deep_research": TierConfig(
        "deep_research", "Deep Research", max_layers=9, companies_per_node=8,
        sources_per_query=4, max_queries_per_node=3, extract_evidence=True,
        extract_deals=True, expand_inputs=True, contradiction_pass=True,
        model_env="ANTHROPIC_MODEL_DEEP", default_model="claude-opus-4-8",
        chart_resolution="deep",
    ),
}

TIER_ORDER = ["high_level", "professional", "deep_research"]


def get_tier(key: str) -> TierConfig:
    return TIERS.get(key, TIERS["professional"])


# Non-Anthropic per-tier model DEFAULTS (env overrides live in config._MODEL_ENV;
# resolve_model asks config.model_override first). NVIDIA defaults are
# Llama-3.x instruct models sized to each tier; OpenRouter keeps DeepSeek.
_ALT_DEFAULTS = {
    "openrouter": {"high_level": "deepseek/deepseek-chat",
                   "professional": "deepseek/deepseek-chat",
                   "deep_research": "deepseek/deepseek-chat"},
    "nvidia": {"high_level": "meta/llama-3.1-8b-instruct",
               "professional": "meta/llama-3.3-70b-instruct",
               "deep_research": "meta/llama-3.1-405b-instruct"},
}


def resolve_model(tier: TierConfig) -> str:
    """Provider-aware: tier env override → provider-wide env → tier default.
    All env reads go through config.model_override (the config SSOT)."""
    provider = config.llm_provider()
    override = config.model_override(provider, tier.key)
    if override:
        return override
    defaults = _ALT_DEFAULTS.get(provider)
    if defaults:
        return defaults.get(tier.key, defaults["professional"])
    return tier.default_model
