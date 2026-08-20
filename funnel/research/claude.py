"""Minimal native Anthropic (Claude) client for the research pipeline.

anthropic is imported lazily so importing this module — and therefore starting
the TUI — does not require the `research` extra to be installed.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from funnel import config


class ClaudeError(Exception):
    pass


def _parse_json(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group(0))
        raise


def _web_search_tool(model: str) -> dict:
    # Dynamic-filtering variant needs Opus 4.6+/Sonnet 4.6; Haiku takes the basic one.
    tool_type = "web_search_20250305" if "haiku" in model else "web_search_20260209"
    return {"type": tool_type, "name": "web_search", "max_uses": 3}


def chat_json(
    messages: list[dict],
    model: str,
    *,
    api_key: str | None = None,
    online: bool = False,
    timeout: float = 120.0,
    max_retries: int = 2,
    max_tokens: int = 16000,
    on_usage=None,
) -> dict:
    """One Messages API call, parsed as JSON. Set online=True to let Claude use
    its server-side web_search tool when locally scraped context is thin."""
    import anthropic

    key = api_key or config.anthropic_api_key()
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY is not set (add it to .env).")

    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    user_messages = [m for m in messages if m.get("role") != "system"]

    client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=max_retries)
    kwargs: dict = {}
    if system:
        kwargs["system"] = system
    if online:
        kwargs["tools"] = [_web_search_tool(model)]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=user_messages,
            **kwargs,
        )
    except anthropic.RateLimitError as exc:
        raise ClaudeError(f"Claude rate-limited: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError(f"Network error talking to Claude: {exc}") from exc

    if on_usage is not None:
        usage = getattr(response, "usage", None)
        on_usage({"calls": 1,
                  "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
                  "completion_tokens": getattr(usage, "output_tokens", 0) or 0})

    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request (stop_reason=refusal).")

    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return _parse_json(text)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"Claude returned non-JSON output: {text[:200]!r}") from exc


def make_llm(model: str, *, api_key: str | None = None,
             on_usage=None) -> Callable[..., dict]:
    """Bind a model into an `llm(messages, online=False) -> dict` callable, the
    shape the pipeline expects (and that tests replace with a fake)."""

    def llm(messages: list[dict], *, online: bool = False,
            max_tokens: int = 16000) -> dict:
        return chat_json(messages, model, api_key=api_key, online=online,
                         max_tokens=max_tokens, on_usage=on_usage)

    return llm
