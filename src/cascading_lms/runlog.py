# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Structured, greppable progress logging for the sweep / finalize / eval.

Purely additive: emits ``[progress] <phase> <event> k=v ...`` lines with wall-clock timings; it changes no
computed value and asserts nothing. Grep a run log for the ``[progress]`` prefix to follow
phase/candidate/archive progress and per-phase durations.
"""

from __future__ import annotations

import time

_START: dict[str, float] = {}


def _emit(phase: str, event: str, **kv) -> None:
    fields = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"[progress] {phase} {event} {fields}".rstrip(), flush=True)


def start(phase: str, **kv) -> None:
    """Mark the start of a phase (records t0 for the matching done())."""
    _START[phase] = time.time()
    _emit(phase, "start", **kv)


def tick(phase: str, **kv) -> None:
    """Progress within a phase (e.g. candidate index + archive size), with seconds since its start."""
    _emit(phase, "tick", t=round(time.time() - _START.get(phase, time.time()), 1), **kv)


def done(phase: str, **kv) -> None:
    """Mark the end of a phase with its total wall-clock duration."""
    _emit(phase, "done", elapsed_s=round(time.time() - _START.get(phase, time.time()), 1), **kv)
