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
    def __init__(self, status: int, body, text: str = ""):
        self.status_code = status
        self._body = body
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise httpx.HTTPStatusError("http error", request=None, response=None)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


# Backoff must never really sleep in a smoke; record the delays instead.
_SLEEPS: list[float] = []
nvidia._sleep = _SLEEPS.append


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

    # --- 429 then success (retry, with backoff between attempts) -----------
    _SLEEPS.clear()
    _install([_Resp(429, None), _msg('{"r": 1}')])
    assert nvidia.chat_json([{"role": "user", "content": "x"}], "m") == {"r": 1}
    assert len(_SLEEPS) == 1 and _SLEEPS[0] > 0, _SLEEPS

    # --- non-JSON generation retried (parse inside the loop) ---------------
    _SLEEPS.clear()
    _install([_msg("sorry, here is prose with no braces"),
              _msg('{"fixed": true}')])
    assert nvidia.chat_json([{"role": "user", "content": "x"}], "m") == {"fixed": True}
    assert len(_SLEEPS) == 1, _SLEEPS

    # --- 401 fails fast (exactly one HTTP call) and reads as credentials ---
    from funnel.research.pipeline import _is_credentials_error
    _CALLS.clear()
    _install([_Resp(401, None, text="Unauthorized")])
    try:
        nvidia.chat_json([{"role": "user", "content": "x"}], "m")
        raise AssertionError("expected NvidiaError for 401")
    except nvidia.NvidiaError as exc:
        assert len(_CALLS) == 1, "401 must not be retried"
        assert _is_credentials_error(exc), exc

    # --- response_format json mode: sent, and dropped on a 400 rejection ---
    nvidia._NO_JSON_MODE.clear()
    _install([_msg('{"j": 1}')])
    nvidia.chat_json([{"role": "user", "content": "x"}], "json-capable-model")
    assert _CALLS[-1]["json"].get("response_format") == {"type": "json_object"}
    _install([_Resp(400, None, text='{"error": "response_format is not supported"}'),
              _msg('{"j": 2}')])
    assert nvidia.chat_json([{"role": "user", "content": "x"}], "no-json-model") == {"j": 2}
    assert "response_format" not in _CALLS[-1]["json"], "json mode not stripped"
    assert "no-json-model" in nvidia._NO_JSON_MODE
    _install([_msg('{"j": 3}')])           # learned: never sent again
    nvidia.chat_json([{"role": "user", "content": "x"}], "no-json-model")
    assert "response_format" not in _CALLS[-1]["json"]
    nvidia._NO_JSON_MODE.clear()

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
