"""Filesystem locations + credentials. Data lives outside the repo so it never
gets committed. Override the root with FUNNEL_HOME. Secrets are read from the
environment, seeded from a .env file if present (never committed, never logged).
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env_file() -> None:
    """Seed os.environ from the first .env found (cwd, then repo root).
    Existing environment variables are never overridden."""
    for path in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if path.is_file():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            break


_load_env_file()

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_HOME = Path(os.environ.get("FUNNEL_HOME", Path.home() / ".funnel_network_engine"))
DB_PATH = APP_HOME / "funnel.db"
# Keep user-visible output beside the project by default, but keep temporary
# test/isolated homes self-contained. FUNNEL_EXPORT_DIR remains an explicit
# override for deployments that need a different destination.
EXPORT_DIR = Path(os.environ.get(
    "FUNNEL_EXPORT_DIR",
    APP_HOME / "exports" if "FUNNEL_HOME" in os.environ else REPO_ROOT / "exports",
))


def ensure_dirs() -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def isr_db_path() -> Path | None:
    """SQLite path of the ISR knowledge base (the sibling
    Investment_Strategy_Research_2026 repo), read-only. Resolution:
    FUNNEL_ISR_DB unset -> probe the known sibling location; set but empty ->
    explicitly disabled; set but missing -> disabled. None means every ISR
    feature silently no-ops."""
    raw = os.environ.get("FUNNEL_ISR_DB")
    if raw is not None:
        raw = raw.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.is_file() else None
    default = (REPO_ROOT.parent / "Investment_Strategy_Research_2026"
               / "scripts" / "research_workflow.sqlite3")
    return default if default.is_file() else None


def anthropic_api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "")


def tavily_api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "")


def openrouter_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def nvidia_api_key() -> str:
    return os.environ.get("NVIDIA_API_KEY", "")


def llm_provider() -> str:
    """Which LLM backend runs research: 'nvidia', 'anthropic' (native Claude),
    or 'openrouter'. Defaults to 'nvidia' for this repo."""
    return os.environ.get("FUNNEL_LLM_PROVIDER", "nvidia").strip().lower()


# Env var holding each provider's API key — the single source of truth for
# preflight checks and the "credentials missing" hints.
_PROVIDER_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def llm_key_name() -> str:
    """The env var name of the active provider's API key."""
    return _PROVIDER_KEY_ENV.get(llm_provider(), "ANTHROPIC_API_KEY")


def llm_api_key() -> str:
    """The API key for the active provider (for preflight checks)."""
    return os.environ.get(llm_key_name(), "")


# Per-tier model override env vars, per provider (single source of truth —
# tiers.py asks here instead of reading os.environ itself).
_MODEL_ENV = {
    "nvidia": {"wide": "NVIDIA_MODEL",
               "tiers": {"high_level": "NVIDIA_MODEL_HIGH",
                         "professional": "NVIDIA_MODEL_PRO",
                         "deep_research": "NVIDIA_MODEL_DEEP"}},
    "openrouter": {"wide": "OPENROUTER_MODEL",
                   "tiers": {"high_level": "OPENROUTER_MODEL_HIGH",
                             "professional": "OPENROUTER_MODEL_PRO",
                             "deep_research": "OPENROUTER_MODEL_DEEP"}},
    "anthropic": {"wide": "ANTHROPIC_MODEL",
                  "tiers": {"high_level": "ANTHROPIC_MODEL_HIGH",
                            "professional": "ANTHROPIC_MODEL_PRO",
                            "deep_research": "ANTHROPIC_MODEL_DEEP"}},
}


def model_override(provider: str, tier_key: str) -> str | None:
    """Env-configured model for (provider, tier): per-tier var wins over the
    provider-wide var; None when neither is set."""
    spec = _MODEL_ENV.get(provider)
    if not spec:
        return None
    return (os.environ.get(spec["tiers"].get(tier_key, ""), "")
            or os.environ.get(spec["wide"], "")) or None


def skip_enrich() -> bool:
    """FUNNEL_SKIP_ENRICH=1 disables all company enrichment passes."""
    return os.environ.get("FUNNEL_SKIP_ENRICH") == "1"


def available_providers() -> list[str]:
    """Providers whose API key is actually set — used only for GUIDANCE (the
    active provider is never switched silently; FUNNEL_LLM_PROVIDER decides)."""
    return [p for p, env in _PROVIDER_KEY_ENV.items() if os.environ.get(env, "")]


def other_provider_hint() -> str:
    """One-line hint when the active provider's key is missing but another
    provider's key exists. Empty string otherwise."""
    if llm_api_key():
        return ""
    others = [p for p in available_providers() if p != llm_provider()]
    if not others:
        return ""
    p = others[0]
    return (f"(a {_PROVIDER_KEY_ENV[p]} is present — set FUNNEL_LLM_PROVIDER={p} "
            f"in .env to use it)")
