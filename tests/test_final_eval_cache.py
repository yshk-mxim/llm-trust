# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""final_eval's greedy resume log is keyed by a fingerprint of the INSTALLED cascade, so a --deploy run does
not resume a DIFFERENT cascade's conditioned rows (the stale-reuse bug: the per-row key is tier|cond|case,
cascade-agnostic; base rows are cascade-independent but conditioned rows are not).
"""

import pytest

from cascading_lms import final_eval as F
from cascading_lms import retune as R


@pytest.fixture
def preserve_live_cascade():
    """Save/restore retune.P -- these tests mutate the live cascade and must not leak into other tests."""
    saved = dict(R.P)
    yield
    R.P.clear()
    R.P.update(saved)


def _set_cascade(vec):
    R.P.clear()
    R.P.update(vec)


def test_log_path_differs_by_cascade(preserve_live_cascade):
    _set_cascade({"wrapper_ctx": "AAA", "composite": "shared"})
    fp_a, log_a = F._cascade_fingerprint(), F._eval_log()
    _set_cascade({"wrapper_ctx": "BBB", "composite": "shared"})
    fp_b, log_b = F._cascade_fingerprint(), F._eval_log()
    assert fp_a != fp_b and log_a != log_b  # different cascades -> disjoint resume logs
    _set_cascade({"wrapper_ctx": "AAA", "composite": "shared"})
    assert (
        F._cascade_fingerprint() == fp_a
    )  # same cascade content -> stable log (a real crash resumes)


def test_resume_sets_are_disjoint_across_cascades(tmp_path, monkeypatch, preserve_live_cascade):
    # a done-key written under cascade A's log is NOT seen when resuming cascade B's log
    monkeypatch.setattr(F.config, "RUN_DIR", str(tmp_path))  # keep resume logs out of runs/
    _set_cascade({"wrapper_ctx": "AAA"})
    log_a = F._eval_log()
    _set_cascade({"wrapper_ctx": "BBB"})
    log_b = F._eval_log()
    assert log_a != log_b
    with open(log_a, "w") as fh:
        fh.write('{"k": "4|conditioned|tok0"}\n')  # a conditioned row scored under cascade A
    assert "4|conditioned|tok0" in F._resume_done(log_a)  # resumes for the SAME cascade
    assert F._resume_done(log_b) == set()  # but NOT for a different cascade -> recomputed fresh


def test_fingerprint_is_short_hex(preserve_live_cascade):
    _set_cascade({"wrapper_ctx": "x"})
    fp = F._cascade_fingerprint()
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)
