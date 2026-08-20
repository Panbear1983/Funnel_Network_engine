"""OpenRouter client (OpenAI-compatible chat completions) — the alternative
LLM provider to the native Anthropic client, useful for cheap models like
DeepSeek. httpx is imported lazily so the TUI starts without the research
extra. Select it with FUNNEL_LLM_PROVIDER=openrouter in .env."""

from __future__ import annotations

import random
import time
from typing import Callable

from funnel import config
from funnel.research.claude import _parse_json

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Injectable for tests — exponential backoff with jitter between retries.
_sleep = time.sleep


def _backoff(attempt: int) -> None:
    _sleep(min(8.0, 1.0 * (2 ** attempt)) * (0.5 + random.random()))
_HEADERS_META = {
    "HTTP-Referer": "https://github.com/Panbear1983/Funnel_Network_engine",
    "X-Title": "Funnel Network Engine",
}


class OpenRouterError(Exception):
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
    """One chat-completions call, parsed as JSON. online=True appends
    OpenRouter's `:online` web plugin (extra per-result fee applies)."""
    import httpx

    key = api_key or config.openrouter_api_key()
    if not key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set (add it to .env).")

    payload = {
        "model": f"{model}:online" if online and ":" not in model else model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 16000,  # explicit: default caps can truncate large edge lists mid-JSON
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", **_HEADERS_META}

    last_error: Exception | None = None
    attempt = 0
    while attempt <= max_retries:
        try:
            response = httpx.post(_ENDPOINT, json=payload, headers=headers, timeout=timeout)
            if response.status_code in (401, 403):
                # Bad key: never retry; word it as a credentials problem.
                raise OpenRouterError(
                    f"OpenRouter API key rejected ({response.status_code} "
                    f"Unauthorized) — check OPENROUTER_API_KEY in .env.")
            if response.status_code == 429:
                last_error = OpenRouterError("OpenRouter rate-limited (429).")
                attempt += 1
                if attempt <= max_retries:
                    _backoff(attempt)
                continue
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise OpenRouterError(f"OpenRouter returned no choices: {str(data)[:200]}")
            content = (choices[0].get("message") or {}).get("content") or ""
            try:
                # Parse inside the loop: truncated/chatty output re-requests.
                return _parse_json(content)
            except Exception:
                last_error = OpenRouterError(
                    f"Non-JSON output from {model}: {content[:200]!r}")
                attempt += 1
                if attempt <= max_retries:
                    _backoff(attempt)
                continue
        except OpenRouterError:
            raise  # 401/no-choices: deliberate, not retryable
        except httpx.HTTPError as exc:
            last_error = exc
            attempt += 1
            if attempt <= max_retries:
                _backoff(attempt)
        except ValueError as exc:  # non-JSON body (gateway blip) — retryable
            last_error = OpenRouterError(f"OpenRouter returned a non-JSON body: {exc}")
            attempt += 1
            if attempt <= max_retries:
                _backoff(attempt)
    raise OpenRouterError(f"OpenRouter request failed: {last_error}") from last_error


def make_llm(model: str, *, api_key: str | None = None) -> Callable[..., dict]:
    """Bind a model into the `llm(messages, online=False) -> dict` callable
    shape the pipeline expects."""

    def llm(messages: list[dict], *, online: bool = False) -> dict:
        return chat_json(messages, model, api_key=api_key, online=online)

    return llm
