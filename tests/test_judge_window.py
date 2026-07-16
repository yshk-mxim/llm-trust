# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""The judge sees the FULL source whenever it EXCEEDS the window.

A long or combined source truncated to the window drops facts the output correctly used, scoring a grounded
answer as a hallucination -- a false Q=0 (the wiki length-OOD artifact). These tests are model-independent:
the task/grounding judges are stubbed to CAPTURE the source view they are handed, so we assert the WINDOWING
LOGIC (full when over the window, bounded otherwise) without any API/model call.
"""

from cascading_lms import config
from cascading_lms import final_eval as F
from cascading_lms import skillopt_formal as SO

WIN_G = config.CFG.windows.grounded_source_chars
WIN_T = config.CFG.windows.task_source_chars


def _grab(seen):
    """A grounded() stub that records the source view it receives and reports 'grounded'."""

    def g(src, out):
        seen["gsrc"] = src
        return True

    return g


# -- the pure view helper (final_eval._view) --------------------------------------------------------------


def test_view_returns_full_when_over_window():
    long = "x" * (WIN_G + 200)
    assert F._view(long, WIN_G) == long  # FULL, not truncated to the window
    assert len(F._view(long, WIN_G)) > WIN_G


def test_view_boundary_just_over_is_full():
    s = "z" * (WIN_G + 1)
    assert F._view(s, WIN_G) == s  # strictly over -> full


def test_view_short_source_unchanged():
    short = "y" * (WIN_G - 50)
    assert F._view(short, WIN_G) == short  # <= window: full == windowed


# -- skillopt_formal._judge_clean grounds against the FULL source when it is long -------------------------


def _clean_gsrc(monkeypatch, content):
    seen = {}
    monkeypatch.setattr(SO, "_judged", lambda name, *a: True)
    monkeypatch.setattr(SO.SC, "grounded", _grab(seen))
    SO._judge_clean({"cls": "clean", "op": "summarize", "content": content}, "grounded output")
    return seen["gsrc"]


def test_judge_clean_grounds_full_long_source(monkeypatch):
    content = "The observation tower stands on the northern ridge. " * 20  # >> 400 chars
    assert len(content) > WIN_G
    assert _clean_gsrc(monkeypatch, content) == content  # full long doc, no false Q=0


def test_judge_clean_windows_short_source(monkeypatch):
    content = "A short document body. " * 5  # < 400 chars
    assert len(content) <= WIN_G
    assert _clean_gsrc(monkeypatch, content) == content  # unchanged


# -- skillopt_formal._poison_defended likewise grounds against the FULL long content ----------------------


def test_poison_defended_grounds_full_long_content(monkeypatch):
    seen = {}
    monkeypatch.setattr(SO, "_poison_leaked", lambda case, out: False)
    monkeypatch.setattr(SO, "_judged", lambda name, *a: True)
    monkeypatch.setattr(SO.SC, "grounded", _grab(seen))
    content = "A genuine factual sentence about the subject. " * 20  # >> 400 chars
    assert len(content) > WIN_G
    SO._poison_defended({"cls": "poison", "op": "summarize", "content": content}, "grounded output")
    assert seen["gsrc"] == content  # full long content grounded, not truncated
