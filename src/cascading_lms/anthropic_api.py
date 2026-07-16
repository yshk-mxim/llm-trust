# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Minimal Anthropic Messages API client (paper: Opus 4.8 is the tuner and every semantic judge).

One place for the HTTP transport used by the tuner (long generations) and the judges (5-token YES/NO), so
retry policy, endpoint, and the API key are defined once. No temperature is sent: it is deprecated for the
judge model and returns HTTP 400.
"""

import json
import time
import urllib.error
import urllib.request

from cascading_lms import config

try:
    with open(config.JUDGE_ENV_JSON) as _fh:
        KEY: str | None = json.load(_fh)[config.JUDGE_API_KEY_FIELD]
except (FileNotFoundError, KeyError):
    # A fresh clone without the key file can still import the package and run the offline proof + gate; a
    # judged eval fails loudly in _request when it actually needs the key.
    KEY = None


def _request(prompt, max_tokens):
    """Build the POST request for a single-message completion."""
    if KEY is None:
        raise RuntimeError(
            f"no judge API key: set {config.JUDGE_API_KEY_FIELD!r} in {config.JUDGE_ENV_JSON} (judged evals only)"
        )
    body = {
        "model": config.JUDGE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": KEY,
        "anthropic-version": config.ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    return urllib.request.Request(
        config.ANTHROPIC_URL, data=json.dumps(body).encode(), headers=headers
    )


def _read(request):
    """Read one completion's text, or None if the response cannot be parsed."""
    with urllib.request.urlopen(request, timeout=config.CFG.api.timeout_s) as resp:
        blocks = json.load(resp).get("content") or []
        # first TEXT block; robust to a non-text leading block (e.g. a thinking block -> KeyError on ["text"])
        # and to an empty content list (a refusal -> "").
        for b in blocks:
            if "text" in b:
                return (b.get("text") or "").strip()
        return ""


def _attempt(request):
    """One attempt: return the text on success, or None to signal a transient failure worth retrying.

    A non-transient HTTP error re-raises immediately (the caller should not mask it as a retry).
    """
    try:
        return _read(request)
    except urllib.error.HTTPError as exc:
        if exc.code not in config.CFG.api.retry_codes:
            raise
        return None
    except (urllib.error.URLError, TimeoutError):
        return None  # network-level transient (reset/timeout) -> retry; a parse/structural bug propagates


def complete(prompt, max_tokens, *, raise_on_exhaust=True):
    """Return one completion's text; retry transient (429/5xx) errors with exponential backoff.

    On exhausting all retries, raise ``RuntimeError`` (default) or return ``""`` when ``raise_on_exhaust``
    is False.
    """
    request = _request(prompt, max_tokens)
    delay = config.CFG.api.backoff_start
    for _ in range(config.CFG.api.retries):
        text = _attempt(request)
        if text is not None:
            return text
        time.sleep(delay)
        delay = min(delay * 2, config.CFG.api.backoff_max)
    if raise_on_exhaust:
        raise RuntimeError("Anthropic API failed after retries")
    return ""
