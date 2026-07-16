# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""OpenAI-compatible client for the defended model (one served model performs every generation role).

Records per-call token counts and latency for the cost/energy analysis.
"""

import json
import os
import time
import urllib.request

from cascading_lms import config

ENDPOINT = os.environ.get("LLM_ENDPOINT", config.LOCAL_ENDPOINT)
_DEFAULT_MODEL = config.LOCAL_MODEL_DEFAULT
_usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}


def _served_model() -> str:
    """Model id auto-detected from the endpoint's /v1/models, or the default if the probe fails."""
    base = ENDPOINT.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        with urllib.request.urlopen(base, timeout=config.CFG.local.probe_timeout_s) as resp:
            return json.load(resp)["data"][0]["id"]
    except Exception:  # best-effort probe: fall back to the default model on any failure.
        return _DEFAULT_MODEL


def refresh_model() -> str:
    """Re-read the model currently served (call after swapping the model on :9000)."""
    global MODEL
    MODEL = os.environ.get("LLM_MODEL") or _served_model()
    return MODEL


MODEL = refresh_model()


def _record_usage(u: dict, seconds: float) -> None:
    """Fold one call's token counts and latency into the running totals."""
    _usage["calls"] += 1
    _usage["prompt_tokens"] += u.get("prompt_tokens", 0)
    _usage["completion_tokens"] += u.get("completion_tokens", 0)
    _usage["seconds"] += seconds


def complete(
    system: str,
    user: str,
    max_tokens: int = config.CFG.generation.llm_default_max_tokens,
    temperature: float = config.CFG.generation.temperature,
) -> str:
    """Return one chat completion's stripped text and accumulate its usage (at the deployment temperature)."""
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("LLM_API_KEY")  # set for OpenAI-compatible API endpoints; unset for local
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=config.CFG.local.timeout_s) as r:
        d = json.load(r)
    _record_usage(d.get("usage", {}), time.time() - t0)
    return (d["choices"][0]["message"].get("content") or "").strip()


def usage() -> dict:
    """Snapshot of cumulative token and latency usage."""
    return dict(_usage)


def reset_usage() -> None:
    """Zero the usage counters."""
    _usage.update({"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0})
