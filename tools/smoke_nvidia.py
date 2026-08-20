"""Offline test for the NVIDIA LLM provider (no network — httpx.post is
monkeypatched). Covers JSON parsing, reasoning-<think> stripping, 429 retry,
missing-key error, provider dispatch, and per-tier model resolution.
Run: `python tools/smoke_nvidia.py`."""

from __future__ import annotations

import os
import sys
import traceback

# Pin provider + a fake key before importing funnel (repo .env is loaded via
# setdefault, so these hard-set values win).
os.environ["FUNNEL_LLM_PROVIDER"] = "nvidia"
os.environ["NVIDIA_API_KEY"] = "nvapi-test"

import httpx  # noqa: E402

from funnel import config  # noqa: E402
from funnel.research import llm as llm_factory  # noqa: E402
from funnel.research import nvidia  # noqa: E402
from funnel.research.tiers import TIER_ORDER, get_tier, resolve_model  # noqa: E402

_CALLS: list[dict] = []


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise httpx.HTTPStatusError("http error", request=None, response=None)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _install(sequence):
    it = iter(sequence)

    def post(url, json=None, headers=None, timeout=None):
        _CALLS.append({"url": url, "json": json, "headers": headers})
        return next(it)

    httpx.post = post


def _msg(content):
    return _Resp(200, {"choices": [{"message": {"content": content}}]})


def run() -> None:
    # --- basic JSON call + payload/headers shape --------------------------
    _install([_msg('{"ok": true, "n": 3}')])
    out = nvidia.chat_json([{"role": "user", "content": "x"}],
                           "meta/llama-3.3-70b-instruct")
    assert out == {"ok": True, "n": 3}, out
    call = _CALLS[-1]
    assert call["url"].startswith("https://integrate.api.nvidia.com"), call["url"]
    assert call["json"]["model"] == "meta/llama-3.3-70b-instruct"
    assert call["json"]["max_tokens"] == 16000 and call["json"]["stream"] is False
    assert call["headers"]["Authorization"] == "Bearer nvapi-test"

    # --- reasoning <think> block stripped before parsing ------------------
    _install([_msg('<think>let me reason step by step...</think>\n{"edges": []}')])
    assert nvidia.chat_json([{"role": "user", "content": "x"}], "m") == {"edges": []}

    # --- online=True accepted and ignored (no :online suffix) -------------
    _install([_msg('{"a": 1}')])
    assert nvidia.chat_json([{"role": "user", "content": "x"}], "m", online=True) == {"a": 1}
    assert _CALLS[-1]["json"]["model"] == "m", "online must not mangle the model id"

    # --- 429 then success (retry) -----------------------------------------
    _install([_Resp(429, None), _msg('{"r": 1}')])
    assert nvidia.chat_json([{"role": "user", "content": "x"}], "m") == {"r": 1}

    # --- missing key raises a clear error ---------------------------------
    os.environ["NVIDIA_API_KEY"] = ""
    try:
        nvidia.chat_json([{"role": "user", "content": "x"}], "m")
        raise AssertionError("expected NvidiaError for missing key")
    except nvidia.NvidiaError as exc:
        assert "NVIDIA_API_KEY" in str(exc), exc
    os.environ["NVIDIA_API_KEY"] = "nvapi-test"

    # --- config plumbing ---------------------------------------------------
    assert config.llm_provider() == "nvidia"
    assert config.llm_key_name() == "NVIDIA_API_KEY"
    assert config.llm_api_key() == "nvapi-test"

    # --- per-tier model resolution ----------------------------------------
    assert resolve_model(get_tier("high_level")) == "meta/llama-3.1-8b-instruct"
    assert resolve_model(get_tier("professional")) == "meta/llama-3.3-70b-instruct"
    assert resolve_model(get_tier("deep_research")) == "meta/llama-3.1-405b-instruct"
    # tier override wins
    os.environ["NVIDIA_MODEL_PRO"] = "nvidia/llama-3.3-nemotron-super-49b-v1"
    assert resolve_model(get_tier("professional")) == "nvidia/llama-3.3-nemotron-super-49b-v1"
    del os.environ["NVIDIA_MODEL_PRO"]
    # provider-wide override wins over tier default
    os.environ["NVIDIA_MODEL"] = "meta/llama-3.1-70b-instruct"
    assert resolve_model(get_tier("high_level")) == "meta/llama-3.1-70b-instruct"
    del os.environ["NVIDIA_MODEL"]

    # --- dispatch: make_llm routes to the NVIDIA client -------------------
    fn = llm_factory.make_llm("meta/llama-3.3-70b-instruct")
    _install([_msg('{"dispatch": "ok"}')])
    assert fn([{"role": "user", "content": "x"}]) == {"dispatch": "ok"}

    # --- openrouter path is untouched -------------------------------------
    os.environ["FUNNEL_LLM_PROVIDER"] = "openrouter"
    assert config.llm_key_name() == "OPENROUTER_API_KEY"
    assert resolve_model(get_tier("professional")).startswith("deepseek/")
    os.environ["FUNNEL_LLM_PROVIDER"] = "nvidia"

    print("NVIDIA_OK parse + think-strip + retry + missing-key + dispatch + "
          "tier-models(", ", ".join(resolve_model(get_tier(t)) for t in TIER_ORDER), ")")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
