# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Dry-run decision tests for the optimiser (no model calls): assert every accept / reject / select
mechanism makes the RIGHT call on known-good and known-bad candidates, catching BOTH failure modes --
(a) rejecting everything including good edits, and (b) accepting bad edits. This is the regression test
that would have caught the ``P[delta > TAU]`` accept-stall bug.
"""

import random

import pytest

from cascading_lms import skillopt_formal as SO
from cascading_lms import skillopt_tuner as T


def _sc(clean, atk):
    """A score()-style dict from 0/1 clean + attack vectors (paired by position)."""
    return {
        "clean_vec": clean,
        "atk_vec": atk,
        "clean_idx": list(range(len(clean))),
        "atk_idx": list(range(len(atk))),
        "Q": SO._mean(clean),
        "R": SO._mean(atk),
    }


def _paired(old_clean, new_clean, old_atk, new_atk):
    """The four paired vectors for a candidate vs the incumbent."""
    return SO.paired_vectors(_sc(old_clean, old_atk), _sc(new_clean, new_atk))


@pytest.fixture(autouse=True)
def _seed():
    """Deterministic bootstrap so the verdicts are reproducible."""
    random.seed(0)


# ---- verdict: accept GOOD, reject BAD, never accept NOISE ------------------------------------------
def test_verdict_accepts_large_gain():
    assert (
        SO.verdict(*_paired([0] * 6 + [1] * 24, [1] * 6 + [1] * 24, [1] * 30, [1] * 30)) == "accept"
    )


def test_verdict_accepts_modest_gain():
    # +0.067 (4/60 fixed) -- the TAU bug rejected exactly this; the optimiser MUST accept modest gains.
    assert (
        SO.verdict(*_paired([1] * 30, [1] * 30, [0] * 4 + [1] * 56, [1] * 4 + [1] * 56)) == "accept"
    )


def test_verdict_rejects_regression():
    assert SO.verdict(*_paired([1] * 30, [0] * 6 + [1] * 24, [1] * 30, [1] * 30)) == "reject"


def test_verdict_never_accepts_noise():
    # identical outcomes -> no gain -> must NOT accept (None or reject).
    assert (
        SO.verdict(*_paired([0] * 15 + [1] * 15, [0] * 15 + [1] * 15, [1] * 30, [1] * 30))
        != "accept"
    )


def test_verdict_rejects_clean_gain_that_tanks_attack():
    # +clean but -attack -> a bad edit dressed as mixed -> must reject, never accept.
    assert (
        SO.verdict(*_paired([0] * 3 + [1] * 27, [1] * 3 + [1] * 27, [1] * 30, [0] * 9 + [1] * 21))
        == "reject"
    )


# ---- the primitives -------------------------------------------------------------------------------
def test_decisive_gain_true_on_modest_gain():
    assert SO._decisive_gain([0] * 4 + [1] * 56, [1] * 4 + [1] * 56, True)


def test_decisive_gain_false_on_neutral():
    assert not SO._decisive_gain([0] * 30 + [1] * 30, [0] * 30 + [1] * 30, True)


def test_decisive_regression_true_on_real_loss():
    assert SO._decisive_regression([1] * 30, [0] * 9 + [1] * 21, True)


def test_decisive_regression_false_within_slack():
    assert not SO._decisive_regression([1] * 60, [0] * 1 + [1] * 59, True)  # -0.017 within TAU


# ---- identity pairing: align by example identity, not position ------------------------------------
def test_paired_vectors_aligns_by_identity_not_position():
    """paired_vectors must pair by EXAMPLE IDENTITY (split index), not position: when the two runs excluded
    DIFFERENT examples, only the intersection forms pairs (a positional zip would misalign the deltas)."""
    # old scored clean examples [0,1,2]; new EXCLUDED example 1 (kept [0,2]); attack [0,1] both.
    old = {"clean_idx": [0, 1, 2], "clean_vec": [1, 0, 1], "atk_idx": [0, 1], "atk_vec": [1, 1]}
    new = {
        "clean_idx": [0, 2],
        "clean_vec": [1, 1],
        "atk_idx": [1, 0],
        "atk_vec": [0, 1],
    }  # note reordered
    oc, nc, oa, na = SO.paired_vectors(old, new)
    assert oc == [1, 1] and nc == [
        1,
        1,
    ]  # only examples 0 and 2 pair; example 1 (new-excluded) dropped
    assert len(oc) == len(nc) == 2
    # attack: identity pairing must survive the reordering -> old ex0<->new ex0, old ex1<->new ex1
    assert oa == [1, 1] and na == [1, 0]


# ---- futility: stop only clearly-worse arms, never a real gain ------------------------------------
def test_futile_on_clearly_worse_both():
    assert SO._futile(
        [0] * 20 + [1] * 40, [0] * 40 + [1] * 20, [0] * 20 + [1] * 40, [0] * 40 + [1] * 20
    )


def test_not_futile_on_real_gain():
    assert not SO._futile([0] * 4 + [1] * 56, [1] * 4 + [1] * 56, [1] * 60, [1] * 60)


# ---- OOD guard is PER-OBJECTIVE (Q and R separately), not the Q+R sum ------------------------------
def test_ood_guard_is_per_objective(monkeypatch):
    """The OOD non-regression guard must revert on a decisive drop in EITHER objective, not the Q+R sum: a
    Q-gain that erodes R past OOD_TOL must revert (a sum-guard would let the Q gain mask the R loss). Catches
    a regression back to summing, or a Q/R index transposition."""
    monkeypatch.setattr(SO, "_stage_gate", lambda keys, cand: (True, {}))
    monkeypatch.setattr(SO, "_persist", lambda cand: None)
    # R 0.90->0.80 (drop 0.10 > OOD_TOL) while Q rises: sum 1.70->1.70 unchanged, but per-objective must REVERT
    rev = SO._gate_ood_and_stage(["k"], {}, (0.8, 0.9), (0.9, 0.80), {})
    assert rev["accept"] is False and rev["reason"] == "ood_guard_revert"
    # symmetric: Q drops past tol while R rises -> revert
    assert SO._gate_ood_and_stage(["k"], {}, (0.9, 0.8), (0.80, 0.9), {})["accept"] is False
    # both within tolerance -> passes the guard (stage-gate stubbed True)
    assert SO._gate_ood_and_stage(["k"], {}, (0.8, 0.9), (0.9, 0.87), {})["accept"] is True


# ---- feasibility-first: drive to feasibility, but not by tanking val ------------------------------
def _mock_check(monkeypatch):
    """SC.check_key returns [] (feasible) iff vector['ok'] is True."""
    monkeypatch.setattr(SO.SC, "check_key", lambda k, v: [] if v.get("ok") else [("fail", "", "")])


def test_feasibility_first_accepts_restored_no_regression(monkeypatch):
    _mock_check(monkeypatch)
    assert SO._feasibility_first(
        ["k"], {"ok": False}, {"ok": True}, [1] * 30, [1] * 30, [1] * 30, [1] * 30
    )


def test_feasibility_first_rejects_if_it_would_regress(monkeypatch):
    _mock_check(monkeypatch)
    # candidate restores feasibility but tanks clean -> do NOT accept.
    assert not SO._feasibility_first(
        ["k"], {"ok": False}, {"ok": True}, [1] * 30, [0] * 9 + [1] * 21, [1] * 30, [1] * 30
    )


def test_feasibility_first_skips_when_incumbent_feasible(monkeypatch):
    monkeypatch.setattr(SO.SC, "check_key", lambda k, v: [])
    assert not SO._feasibility_first(["k"], {}, {}, [1] * 30, [1] * 30, [1] * 30, [1] * 30)


def test_feasibility_first_rejects_when_candidate_still_infeasible(monkeypatch):
    monkeypatch.setattr(SO.SC, "check_key", lambda k, v: [("fail", "", "")])
    assert not SO._feasibility_first(["k"], {}, {}, [1] * 30, [1] * 30, [1] * 30, [1] * 30)


# ---- selection: best-of-trajectory + best-of-restarts pick the BEST, never revert ----------------
def _mock_score(monkeypatch, table):
    """SO.score(v, split, conds) -> the mock score for v['t'] on conds[0] (used by both selectors)."""
    monkeypatch.setattr(T.SO, "score", lambda v, split, conds: {conds[0]: table[v["t"]]})


def test_best_incumbent_picks_best_not_seed(monkeypatch):
    snaps = [{"t": "seed"}, {"t": "best"}, {"t": "last"}]
    _mock_score(
        monkeypatch,
        {
            "seed": _sc([1] * 30, [0] * 20 + [1] * 40),
            "best": _sc([1] * 30, [1] * 60),
            "last": _sc([1] * 30, [0] * 10 + [1] * 50),
        },
    )
    monkeypatch.setattr(T.SC, "check_key", lambda k, v: [])
    assert T._best_incumbent(snaps, ("k",), "cond")["t"] == "best"


def test_best_final_picks_best_restart(monkeypatch):
    finals = [{"t": "r0"}, {"t": "r1"}]
    _mock_score(
        monkeypatch, {"r0": _sc([1] * 30, [0] * 20 + [1] * 40), "r1": _sc([1] * 30, [1] * 60)}
    )
    best, _sc_best, _results = T._best_final(finals)
    assert best["t"] == "r1"
