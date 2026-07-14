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

APP_HOME = Path(os.environ.get("FUNNEL_HOME", Path.home() / ".funnel_network_engine"))
DB_PATH = APP_HOME / "funnel.db"
EXPORT_DIR = APP_HOME / "exports"


def ensure_dirs() -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def anthropic_api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "")


def tavily_api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "")


def openrouter_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def llm_provider() -> str:
    """Which LLM backend runs research: 'anthropic' (default) or 'openrouter'."""
    return os.environ.get("FUNNEL_LLM_PROVIDER", "anthropic").strip().lower()


def llm_api_key() -> str:
    """The API key for the active provider (for preflight checks)."""
    return openrouter_api_key() if llm_provider() == "openrouter" else anthropic_api_key()
