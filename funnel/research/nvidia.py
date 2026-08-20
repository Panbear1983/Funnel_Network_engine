"""NVIDIA API client (OpenAI-compatible chat completions) — the LLM provider
for this repo. Talks to NVIDIA's hosted model endpoint
(https://integrate.api.nvidia.com/v1); auth is a single NVIDIA_API_KEY
(`nvapi-...` from build.nvidia.com). httpx is imported lazily so the TUI starts
without the research extra. Select it with FUNNEL_LLM_PROVIDER=nvidia in .env.

Notes vs the other providers:
- No built-in web search. NVIDIA's endpoint has no `:online`-style plugin, so
  `online` is accepted-and-ignored; scraped Tavily context is the source of
  truth (set TAVILY_API_KEY). Without Tavily the model answers from its own
  training knowledge.
- Reasoning models (nemotron/deepseek-r1) prepend a <think>…</think> block;
  it is stripped before JSON parsing so strict-JSON stages stay robust even if
  a reasoning model is chosen via NVIDIA_MODEL*.
"""

from __future__ import annotations

import re
from typing import Callable

from funnel import config
from funnel.research.claude import _parse_json

_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class NvidiaError(Exception):
    pass


def chat_json(
    messages: list[dict],
    model: str,
    *,
    api_key: str | None = None,
    online: bool = False,
    temperature: float = 0.2,
    timeout: float = 180.0,
    max_retries: int = 2,
) -> dict:
    """One chat-completions call, parsed as JSON. `online` is ignored (NVIDIA
    has no web-search plugin) — kept for the shared llm() signature."""
    import httpx

    key = api_key or config.nvidia_api_key()
    if not key:
        raise NvidiaError("NVIDIA_API_KEY is not set (add it to .env; "
                          "get one at build.nvidia.com).")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": 0.9,
        "max_tokens": 16000,  # explicit: default caps can truncate large JSON
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        try:
            response = httpx.post(_ENDPOINT, json=payload, headers=headers,
                                  timeout=timeout)
            if response.status_code == 429:
                last_error = NvidiaError("NVIDIA API rate-limited (429).")
                continue
            response.raise_for_status()
            data = response.json()
            break
        except httpx.HTTPError as exc:
            last_error = exc
        except ValueError as exc:  # non-JSON body (gateway blip) — retryable
            last_error = NvidiaError(f"NVIDIA returned a non-JSON body: {exc}")
    else:
        raise NvidiaError(f"NVIDIA request failed: {last_error}") from last_error

    choices = data.get("choices") or []
    if not choices:
        raise NvidiaError(f"NVIDIA returned no choices: {str(data)[:200]}")
    content = (choices[0].get("message") or {}).get("content") or ""
    content = _THINK_RE.sub("", content)  # drop reasoning traces before parsing
    try:
        return _parse_json(content)
    except Exception as exc:
        raise NvidiaError(f"Non-JSON output from {model}: {content[:200]!r}") from exc


def make_llm(model: str, *, api_key: str | None = None) -> Callable[..., dict]:
    """Bind a model into the `llm(messages, online=False) -> dict` callable
    shape the pipeline expects."""

    def llm(messages: list[dict], *, online: bool = False) -> dict:
        return chat_json(messages, model, api_key=api_key, online=online)

    return llm
