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

import random
import re
import time
from typing import Callable

from funnel import config
from funnel.research.claude import _parse_json

_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)

# Injectable for tests (smokes no-op it) — exponential backoff with jitter.
_sleep = time.sleep

# Models observed to reject response_format json mode (support is per-model on
# NVIDIA's OpenAI-compat endpoint); learned at runtime, skipped thereafter.
_NO_JSON_MODE: set[str] = set()


def _backoff(attempt: int) -> None:
    _sleep(min(8.0, 1.0 * (2 ** attempt)) * (0.5 + random.random()))


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
    max_tokens: int = 16000,
    on_usage=None,
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
        "max_tokens": max_tokens,
        "stream": False,
    }
    if model not in _NO_JSON_MODE:
        # Ask for strict JSON where the model supports it; learned-fallback
        # below strips it for models that 400 on the field.
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    last_error: Exception | None = None
    attempt = 0
    while attempt <= max_retries:
        try:
            response = httpx.post(_ENDPOINT, json=payload, headers=headers,
                                  timeout=timeout)
            if response.status_code in (401, 403):
                # Bad key: never retry — and word it so the pipeline
                # classifies it as a credentials problem, not a failure.
                raise NvidiaError(
                    f"NVIDIA API key rejected ({response.status_code} "
                    f"Unauthorized) — check NVIDIA_API_KEY in .env.")
            body_lower = response.text.lower() if response.status_code in (400, 422) else ""
            if ("response_format" in payload
                    and response.status_code in (400, 422)
                    and ("response_format" in body_lower or "json" in body_lower)):
                # This model rejects json mode — strip it, remember, retry
                # immediately (does not consume a backoff attempt).
                _NO_JSON_MODE.add(model)
                payload.pop("response_format", None)
                continue
            if response.status_code == 429:
                last_error = NvidiaError("NVIDIA API rate-limited (429).")
                attempt += 1
                if attempt <= max_retries:
                    _backoff(attempt)
                continue
            response.raise_for_status()
            data = response.json()
            # Parse INSIDE the retry loop: a truncated/chatty generation is
            # retryable (re-request), not fatal.
            choices = data.get("choices") or []
            if not choices:
                raise NvidiaError(f"NVIDIA returned no choices: {str(data)[:200]}")
            content = (choices[0].get("message") or {}).get("content") or ""
            content = _THINK_RE.sub("", content)  # drop reasoning traces
            try:
                parsed = _parse_json(content)
                if on_usage is not None:
                    u = data.get("usage") or {}
                    on_usage({"calls": 1,
                              "prompt_tokens": u.get("prompt_tokens", 0) or 0,
                              "completion_tokens": u.get("completion_tokens", 0) or 0})
                return parsed
            except Exception as exc:
                last_error = NvidiaError(
                    f"Non-JSON output from {model}: {content[:200]!r}")
                attempt += 1
                if attempt <= max_retries:
                    _backoff(attempt)
                continue
        except NvidiaError:
            raise  # 401/no-choices: deliberate, not retryable here
        except httpx.HTTPError as exc:
            last_error = exc
            attempt += 1
            if attempt <= max_retries:
                _backoff(attempt)
        except ValueError as exc:  # non-JSON body (gateway blip) — retryable
            last_error = NvidiaError(f"NVIDIA returned a non-JSON body: {exc}")
            attempt += 1
            if attempt <= max_retries:
                _backoff(attempt)
    raise NvidiaError(f"NVIDIA request failed: {last_error}") from last_error


def make_llm(model: str, *, api_key: str | None = None,
             on_usage=None) -> Callable[..., dict]:
    """Bind a model into the `llm(messages, online=False) -> dict` callable
    shape the pipeline expects."""

    def llm(messages: list[dict], *, online: bool = False,
            max_tokens: int = 16000) -> dict:
        return chat_json(messages, model, api_key=api_key, online=online,
                         max_tokens=max_tokens, on_usage=on_usage)

    return llm
