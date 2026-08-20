#!/usr/bin/env bash
# Launch the Funnel Network Engine TUI dashboard.
#
# Usage: ./run.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is not installed — see https://docs.astral.sh/uv/" >&2
    exit 1
fi

# Creates .venv on first run, then keeps it in sync with uv.lock.
uv sync --extra viz --extra research --extra intel

if [ ! -f .env ]; then
    echo "warning: no .env file — copy .env.example to .env and set NVIDIA_API_KEY (build.nvidia.com); TAVILY_API_KEY recommended." >&2
fi

exec uv run python -m funnel
