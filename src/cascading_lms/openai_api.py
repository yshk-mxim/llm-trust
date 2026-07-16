# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Minimal OpenAI Chat Completions client for the INDEPENDENT judge.

A second-vendor model (default gpt-5.6, OpenAI's latest flagship) grades the same cases the Opus judge grades,
so the genuine-leak grader can be validated against a model from a DIFFERENT family (addressing the one-model
tuner+judge circularity). Key from the same ~/semantic/env.json (config.JUDGE_ENV_JSON), field ``openai_token``.
Reasoning models use ``max_completion_tokens`` and reject a custom temperature, so neither is sent beyond the cap.
"""

import json
import time
import urllib.error
import urllib.request

from cascading_lms import config

MODEL = "gpt-5.6"  # latest flagship available on this key (gpt-5.6-sol / gpt-5.5 / gpt-5.4 also callable)
URL = "https://api.openai.com/v1/chat/completions"
API_KEY_FIELD = "openai_token"
RETRY_CODES = (429, 500, 502, 503, 529)

try:
    with open(config.JUDGE_ENV_JSON) as _fh:
        KEY: str | None = json.load(_fh).get(API_KEY_FIELD)
except FileNotFoundError:
    KEY = None  # a fresh clone without the key file can still import the package + run the offline proof/gate


def complete(prompt, max_completion_tokens=3000, model=MODEL):
    """Return one completion's text; retry transient (429/5xx) errors with exponential backoff."""
    if KEY is None:
        raise RuntimeError(f"no OpenAI key: set {API_KEY_FIELD!r} in {config.JUDGE_ENV_JSON}")
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": max_completion_tokens,
        }
    ).encode()
    delay = 2.0
    for _ in range(config.CFG.api.retries):
        req = urllib.request.Request(
            URL,
            data=body,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=config.CFG.api.timeout_s) as resp:
                choice = json.load(resp)["choices"][0]
                return (choice["message"].get("content") or "").strip()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_CODES:
                raise
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(delay)
        delay = min(delay * 2, 30.0)
    raise RuntimeError("OpenAI API failed after retries")
