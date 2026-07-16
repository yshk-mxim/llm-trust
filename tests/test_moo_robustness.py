# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""MOO robustness + joint-sweep wiring (offline: no model, no API).

Covers the audit fixes: the joint (constrained multivariate) sweep entry point, the precise final
validation (``finalize_and_deploy``: re-measure + OOD + feasibility -- the winner's-curse fix that replaced
the noisy cap-scan ``arch.select()``), and the fail-loud guards (empty objective + whole-pass-errored) that
stop a dead backend from archiving or deploying a fabricated point. Every model/judge call is monkeypatched,
so this runs inside ``make check``.
"""

import json
import random
import types

import pytest

from cascading_lms import config
from cascading_lms import skillopt_formal as SO
from cascading_lms import skillopt_tuner as T


@pytest.fixture(autouse=True)
def _seed():
    """Deterministic bootstrap so the verdicts are reproducible."""
    random.seed(0)


# ---- _parse_joint: labeled fenced blocks -> {key: prompt}; missing/empty stage OMITTED ----------------
def test_parse_joint_extracts_each_labeled_block():
    resp = (
        "reasoning first...\n"
        "=== pass_ctx_USER ===\n```\nrestate the user\n```\n"
        "=== wrapper_ctx ===\n```text\nwrap and ground\n```\n"
    )
    edits = T._parse_joint(resp, ("pass_ctx_USER", "pass_ctx_data", "wrapper_ctx"))
    assert edits == {"pass_ctx_USER": "restate the user", "wrapper_ctx": "wrap and ground"}
    assert "pass_ctx_data" not in edits  # no block -> omitted (never corrupt a prompt)


def test_parse_joint_ignores_empty_block():
    assert T._parse_joint("=== k ===\n```\n\n```", ("k",)) == {}


# ---- pareto_verdict / _moo_stop: archive tradeoffs; stop only when DECIDED ----------------------------
def test_pareto_verdict_archives_tradeoff():
    # R up decisively, Q flat -> a non-dominated frontier point -> archive (the whole point of MOO)
    assert SO.pareto_verdict([1] * 30, [1] * 30, [0] * 20 + [1] * 40, [1] * 60) == "archive"


def test_pareto_verdict_rejects_no_gain():
    assert SO.pareto_verdict([1] * 30, [1] * 30, [1] * 30, [1] * 30) == "reject"


def test_moo_stop_true_on_decisive_gain():
    assert SO._moo_stop([1] * 30, [1] * 30, [0] * 20 + [1] * 40, [1] * 60)


def test_moo_stop_false_when_unresolved():
    # +1/60 on R is neither a decisive gain nor futile -> keep sampling (do NOT cap-and-reject a real gain)
    assert not SO._moo_stop([1] * 30, [1] * 30, [0] * 1 + [1] * 59, [1] * 60)


# ---- fail-loud guards: empty objective / whole pass errored ------------------------------------------
def test_assert_scorable_raises_on_empty_objective():
    with pytest.raises(RuntimeError, match="not scorable"):
        T._assert_scorable({"n_clean": 0, "n_att": 12}, "seed")
    with pytest.raises(RuntimeError, match="not scorable"):
        T._assert_scorable({"n_clean": 12, "n_att": 0}, "seed")


def test_assert_scorable_passes_when_both_populated():
    T._assert_scorable({"n_clean": 5, "n_att": 5}, "seed")  # no raise


def test_abort_if_all_errored_only_when_every_candidate_errored():
    with pytest.raises(RuntimeError, match="all 4 candidates errored"):
        T._abort_if_all_errored(4, 4, "p0")
    T._abort_if_all_errored(3, 4, "p0")  # a single survivor -> no abort
    T._abort_if_all_errored(0, 0, "p0")  # empty pass (converged) -> no abort


# ---- finalize_and_deploy: precise re-measure + OOD + feasibility, then deploy max-R >= floor ----------
class _Arch:
    """Minimal stand-in for pareto.ParetoArchive (only .points is read by finalize_and_deploy)."""

    def __init__(self, points):
        self.points = points


def _install_scores(monkeypatch, table, infeasible=(), base_q=0.80):
    """SO.score(vec, split, conds) -> table[(vec['id'], split)]; SC.check_key marks ``infeasible`` ids.

    Also stubs a live ``llm`` (finalize healthchecks the backend before re-measuring) and the base-model Q
    ceiling ``_precise_base_q`` (the Q_relative denominator; the deploy gate is Q_defended/base_q, not absolute Q).
    """

    def score(vec, split, conds, cap=None):
        q, r = table[(vec["id"], split)]
        return {conds[0]: {"Q": q, "R": r, "n_clean": 10, "n_att": 10}}

    monkeypatch.setattr(
        "cascading_lms.llm",
        types.SimpleNamespace(refresh_model=lambda: "x", complete=lambda *a, **k: "ok"),
        raising=False,
    )
    monkeypatch.setattr(T.SO, "score", score)
    monkeypatch.setattr(T, "_precise_base_q", lambda repeats: base_q)
    monkeypatch.setattr(
        T.SC, "check_key", lambda k, v: [("fail", "", "")] if v["id"] in infeasible else []
    )


def test_finalize_picks_max_r_above_floor_dropping_ood_and_infeasible(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "run_path", lambda name: str(tmp_path / name))
    written = []
    monkeypatch.setattr(T, "_write_prompts", lambda vec: written.append(vec))
    # seed OOD anchor (0.90, 0.90); OOD_TOL=0.05. A is OOD-ok + feasible + high R; B OOD-regresses R; C infeasible.
    table = {
        ("S", "ood"): (0.90, 0.90),
        ("S", "val"): (0.90, 0.70),
        ("A", "ood"): (0.90, 0.88),  # -0.02 within tol -> ok
        ("A", "val"): (0.85, 0.92),  # above floor, max R -> DEPLOY
        ("B", "ood"): (0.90, 0.80),  # -0.10 > tol -> dropped
        ("C", "ood"): (0.90, 0.90),  # feasibility fails first, OOD never consulted
    }
    _install_scores(monkeypatch, table, infeasible=("C",))
    arch = _Arch(
        [
            {"vec": {"id": "S"}, "Q": 0.9, "R": 0.7, "round": "SEED"},
            {"vec": {"id": "A"}, "Q": 0.88, "R": 0.95, "round": "p0#1"},
            {"vec": {"id": "B"}, "Q": 0.87, "R": 0.97, "round": "p0#2"},
            {"vec": {"id": "C"}, "Q": 0.9, "R": 0.99, "round": "p0#3"},
        ]
    )
    chosen = T.finalize_and_deploy(arch, "conditioned", ["k"], repeats=2)
    assert chosen["vec"] == {"id": "A"} and chosen["round"] == "p0#1"
    assert chosen["Q"] == 0.85 and chosen["R"] == 0.92
    assert written[-1] == {
        "id": "A"
    }  # the DEPLOY vector was persisted (precise, not the cap-noisy C)


def test_finalize_reverts_to_seed_when_none_clears_floor(monkeypatch, tmp_path):
    # No survivor (incl. the re-measured seed) clears the precise Q-floor -> REVERT to seed, never ship a
    # sub-floor experimental point (the original winner's-curse bug). Seed is always a survivor now.
    monkeypatch.setattr(config, "run_path", lambda name: str(tmp_path / name))
    written = []
    monkeypatch.setattr(T, "_write_prompts", lambda vec: written.append(vec))
    table = {
        ("S", "val"): (0.70, 0.90),  # seed below the 0.80 floor
        ("S", "ood"): (0.90, 0.90),
        ("A", "val"): (0.72, 0.99),  # candidate also below floor (higher R, but sub-floor)
        ("A", "ood"): (0.90, 0.90),
    }
    _install_scores(monkeypatch, table)
    arch = _Arch(
        [
            {"vec": {"id": "S"}, "Q": 0.9, "R": 0.7, "round": "SEED"},
            {"vec": {"id": "A"}, "Q": 0.9, "R": 0.99, "round": "p0#1"},
        ]
    )
    chosen = T.finalize_and_deploy(arch, "conditioned", ["k"], repeats=1)
    assert chosen["round"] == "SEED-FALLBACK" and chosen["vec"] == {
        "id": "S"
    }  # not the sub-floor A
    assert written[-1] == {"id": "S"}


def test_finalize_aborts_loud_on_backend_outage(monkeypatch, tmp_path):
    # A backend outage DURING finalize surfaces as a probe-unresolved feasibility failure -> abort LOUD,
    # never a silent SEED-FALLBACK. check_key on a non-seed point returns the probe-unresolved marker.
    monkeypatch.setattr(config, "run_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(T, "_write_prompts", lambda vec: None)
    table = {("S", "val"): (0.90, 0.90), ("S", "ood"): (0.90, 0.90), ("A", "ood"): (0.90, 0.90)}
    _install_scores(monkeypatch, table)
    monkeypatch.setattr(
        T.SC, "check_key", lambda k, v: [("k:probe-unresolved", "RuntimeError", "judge down")]
    )
    arch = _Arch(
        [
            {"vec": {"id": "S"}, "Q": 0.9, "R": 0.9, "round": "SEED"},
            {"vec": {"id": "A"}, "Q": 0.9, "R": 0.9, "round": "p0#1"},
        ]
    )
    with pytest.raises(RuntimeError, match="probe backend outage"):
        T.finalize_and_deploy(arch, "conditioned", ["k"], repeats=1)


def test_finalize_empty_archive_returns_none(monkeypatch):
    assert T.finalize_and_deploy(_Arch([]), "conditioned", ["k"]) is None


def test_pick_deploy_is_trust_gated_not_performance_gated():
    # THE conceptual fix: the deploy gate is TRUST (Q maintained vs the base model), not PERFORMANCE (an
    # absolute Q). A survivor with Q_defended=0.72 -- BELOW any absolute 0.8 floor -- but Q_relative=0.96
    # (retains 96% of the base model's own clean quality) IS deployable. The base 26B may not reach 0.8 itself.
    base_q = 0.75
    survivors = [
        T._survivor(
            {"id": "S"}, 0.60, 0.90, base_q, "SEED"
        ),  # Q_rel 0.80 -> degrades quality, rejected
        T._survivor(
            {"id": "A"}, 0.72, 1.00, base_q, "p0#1"
        ),  # Q_rel 0.96 -> retains + max R -> DEPLOY
    ]
    chosen = T._pick_deploy(survivors, {"id": "S"}, base_q)
    assert chosen["round"] == "p0#1" and chosen["R"] == 1.00
    assert chosen["Q"] == 0.72 and chosen["Q"] < 0.8  # deployed DESPITE sub-0.8 absolute Q
    assert (
        chosen["Q_relative"] >= config.CFG.optimizer.q_relative_floor
    )  # because trust is Q RETAINED


def test_pick_deploy_seed_fallback_when_quality_degrades():
    # SEED-FALLBACK still triggers when NO survivor retains base quality (Q_relative < floor) -- a defense that
    # drops Q from the base 0.90 to 0.72 (Q_rel 0.80) is NOT trustworthy, even though 0.72 is a real number.
    base_q = 0.90
    survivors = [
        T._survivor({"id": "S"}, 0.70, 0.90, base_q, "SEED"),  # Q_rel 0.78 < 0.95
        T._survivor({"id": "A"}, 0.72, 1.00, base_q, "p0#1"),  # Q_rel 0.80 < 0.95
    ]
    chosen = T._pick_deploy(survivors, {"id": "S"}, base_q)
    assert chosen["round"] == "SEED-FALLBACK" and chosen["Q_relative"] is None


def test_persist_validated_saves_vectors_for_redecision(monkeypatch, tmp_path):
    # The saved result carries the prompt VECTORS + Q_base/Q_relative, so a completed run is RE-DECIDABLE under
    # a changed gate WITHOUT re-sweeping (a sweep is hours).
    monkeypatch.setattr(config, "run_path", lambda name: str(tmp_path / name))
    surv = T._survivor({"id": "S", "wrapper_ctx": "w"}, 0.72, 1.0, 0.75, "SEED")
    T._persist_validated("conditioned", [surv], surv)
    with open(tmp_path / "pareto_validated_conditioned.json") as fh:
        saved = json.load(fh)
    assert saved["survivors"][0]["vec"] == {"id": "S", "wrapper_ctx": "w"}
    assert saved["deploy"]["vec"] == {
        "id": "S",
        "wrapper_ctx": "w",
    }  # deploy re-installable from the file
    assert saved["survivors"][0]["Q_relative"] == round(0.72 / 0.75, 3)
    assert saved["survivors"][0]["Q_base"] == 0.75


# ---- propose_joint: the flattened contract-failure gradient (regression for the dict/list KeyError) -----
def test_propose_joint_handles_contract_failures(monkeypatch):
    # Regression: propose_joint built contract_fx as a DICT while _format_gradient slices it as a LIST, so any
    # edited key WITH a stage failure (the feasibility-first case) raised KeyError(slice). It must not.
    keys = ("pass_ctx_USER", "pass_ctx_data", "wrapper_ctx")
    monkeypatch.setattr(T, "_load_prompts", lambda: {k: f"cur {k}" for k in keys})
    monkeypatch.setattr(T.SO, "train_failures", lambda prompts, cond: ([], [], []))
    monkeypatch.setattr(
        T, "contract_fails", lambda key: [(f"{key}:some-fail", "probe", "detail")]
    )  # every key infeasible
    monkeypatch.setattr(
        T,
        "opus_gen",
        lambda ask, max_tokens=0: "\n".join(f"=== {k} ===\n```\nnew {k}\n```" for k in keys),
    )
    edits = T.propose_joint(keys, "conditioned")
    assert edits == {k: f"new {k}" for k in keys}  # parsed all three, no KeyError from the gradient


# ---- pareto_sweep_multi end-to-end: joint archive advances + finalize deploys (fully mocked) ----------
def test_pareto_sweep_multi_archives_and_deploys(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "run_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(
        "cascading_lms.llm",
        types.SimpleNamespace(refresh_model=lambda: "fake", complete=lambda *a, **k: "ok"),
        raising=False,
    )
    keys = ("pass_ctx_USER", "pass_ctx_data", "wrapper_ctx")
    seed = {k: k for k in keys}
    seed["id"] = "S"
    cand = {**seed, "id": "cand", "wrapper_ctx": "improved wrapper"}
    monkeypatch.setattr(T, "_load_prompts", lambda: dict(seed))
    monkeypatch.setattr(T, "_snapshot_pre_run", lambda cond: None)
    written = []
    monkeypatch.setattr(T, "_write_prompts", lambda vec: written.append(dict(vec)))
    # cap seed score + precise val/ood scores; cand deploys (higher precise R, above floor, OOD-ok, feasible).
    table = {
        ("S", "val"): (0.90, 0.70),
        ("S", "ood"): (0.90, 0.90),
        ("cand", "val"): (0.88, 0.92),
        ("cand", "ood"): (0.90, 0.90),
    }
    _install_scores(monkeypatch, table)
    # first joint candidate ARCHIVES (advances the incumbent, Q>=floor); the rest REJECT.
    calls = {"n": 0}

    def _fake_multi(name, ks, cond, incumbent):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "round": name,
                "edit_keys": list(ks),
                "verdict": "archive",
                "Q": 0.85,
                "R": 0.92,
                "Q_inc": 0.90,
                "R_inc": 0.70,
                "vector": dict(cand),
            }
        return {"round": name, "edit_keys": list(ks), "verdict": "reject", "reason": "no gain"}

    monkeypatch.setattr(T, "_safe_pareto_multi", _fake_multi)
    arch, log = T.pareto_sweep_multi(keys, passes=2, cond="conditioned")
    assert len(arch.points) == 2  # SEED + the archived joint candidate
    assert written[-1] == cand  # finalize deployed the precise max-R point (the joint candidate)
    assert any(r["verdict"] == "archive" for r in log)


def test_pareto_sweep_coordinate_archives_and_deploys(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "run_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(
        "cascading_lms.llm",
        types.SimpleNamespace(refresh_model=lambda: "fake", complete=lambda *a, **k: "ok"),
        raising=False,
    )
    seed = {"defense": "base", "id": "S"}
    cand = {"defense": "stronger base", "id": "cand"}
    monkeypatch.setattr(T, "_load_prompts", lambda: dict(seed))
    monkeypatch.setattr(T, "_snapshot_pre_run", lambda cond: None)
    written = []
    monkeypatch.setattr(T, "_write_prompts", lambda vec: written.append(dict(vec)))
    table = {
        ("S", "val"): (0.90, 0.70),
        ("S", "ood"): (0.90, 0.90),
        ("cand", "val"): (0.88, 0.92),
        ("cand", "ood"): (0.90, 0.90),
    }
    _install_scores(monkeypatch, table)
    calls = {"n": 0}

    def _fake_step(name, key, cond, incumbent):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "round": name,
                "edit": key,
                "verdict": "archive",
                "Q": 0.85,
                "R": 0.92,
                "Q_inc": 0.90,
                "R_inc": 0.70,
                "vector": dict(cand),
            }
        return {"round": name, "edit": key, "verdict": "reject", "reason": "no gain"}

    monkeypatch.setattr(T, "_safe_pareto_step", _fake_step)
    arch, _log = T.pareto_sweep(keys=("defense",), passes=2, cond="base+prompt")
    assert len(arch.points) == 2  # SEED + archived coordinate candidate
    assert written[-1] == cand  # finalize deployed the precise max-R point
