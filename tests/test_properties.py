# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Property-based UNIT tests for the pure-logic cores (hypothesis): assert INVARIANTS over generated
inputs, not hand-picked examples. Scope is the deterministic logic units -- the lattice meet, Pareto
domination/archive, spec validation, the external-row shape check, and config -- NOT the model-dependent
pipeline (those stay example/integration tests). Deterministic (derandomized) so the offline gate is stable.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cascading_lms import config, pareto, trust_spec
from cascading_lms.harness import Ring, meet

settings.register_profile("deterministic", derandomize=True)
settings.load_profile("deterministic")

rings = st.sampled_from(list(Ring))
qr = st.builds(lambda q, r: {"Q": q, "R": r}, st.floats(0, 1), st.floats(0, 1))


# ---- harness.meet: integrity greatest-lower-bound -------------------------------------------------
@given(st.lists(rings))
def test_meet_is_greatest_lower_bound(rs):
    m = meet(rs)
    if not rs:
        assert m is Ring.UNTRUSTED  # empty meet = bottom
    else:
        assert all(int(m) <= int(r) for r in rs)  # a lower bound
        assert m in rs  # and the GREATEST one (it is one of the inputs, the min)


@given(rings, rings)
def test_meet_commutative(a, b):
    assert meet([a, b]) == meet([b, a])


@given(st.lists(rings, min_size=1))
def test_meet_idempotent_and_order_invariant(rs):
    assert meet(rs) == meet(list(reversed(rs))) == meet(rs + rs)  # order- and duplicate-invariant


# ---- pareto domination: a strict partial order under the noise margin ------------------------------
@given(qr)
def test_scalar_dominance_irreflexive(p):
    assert not pareto._scalar_dominates(p, p)  # nothing dominates itself


@given(qr, qr)
def test_scalar_dominance_antisymmetric(a, b):
    assert not (pareto._scalar_dominates(a, b) and pareto._scalar_dominates(b, a))


@given(qr, qr)
def test_dominance_implies_not_worse_on_both_axes(a, b):
    if pareto._scalar_dominates(a, b):
        tau = config.CFG.optimizer.tau_pareto
        assert a["Q"] >= b["Q"] - tau and a["R"] >= b["R"] - tau  # not-worse on both
        assert a["Q"] > b["Q"] + tau or a["R"] > b["R"] + tau  # clearly-better on one


# ---- ParetoArchive: select respects the floor + maximises R; collisions fold as a running mean -----
@given(st.lists(st.tuples(st.floats(0, 1), st.floats(0, 1)), min_size=1))
def test_archive_select_is_max_r_above_floor(points):
    arc = pareto.ParetoArchive()
    for i, (q, r) in enumerate(points):
        arc.add(vec=("v", i), q=q, r=r)  # distinct vecs -> no fold
    floor = 0.5
    sel = arc.select(q_floor=floor)
    eligible = [p for p in arc.points if p["Q"] >= floor]
    if eligible:
        assert sel["Q"] >= floor
        assert sel["R"] == max(p["R"] for p in eligible)  # max-R among those clearing the floor
    else:  # nothing clears the floor -> falls back to the max-R point overall
        assert sel["R"] == max(p["R"] for p in arc.points)


@given(st.lists(st.tuples(st.floats(0, 1), st.floats(0, 1)), min_size=1, max_size=8))
def test_archive_collision_is_running_mean(measurements):
    arc = pareto.ParetoArchive()
    for q, r in measurements:
        arc.add(vec="same", q=q, r=r)  # identical vector -> fold as another measurement
    p = arc.points[0]
    n = len(measurements)
    assert len(arc.points) == 1 and p["n"] == n
    assert p["Q"] == pytest.approx(sum(q for q, _ in measurements) / n, abs=0.01)


# ---- trust_spec.validate: accepts every shipped lattice, rejects malformed --------------------------
@pytest.mark.parametrize(
    "path",
    [
        "data/trust_model.toml",
        "data/trust_model_example2.toml",
        "tests/fixtures/test_trust_model.toml",
    ],
)
def test_validate_accepts_shipped_lattices(path):
    tm = trust_spec.TrustModel.load(path)  # load() calls validate(); a bad spec would raise
    assert tm.control_ring and tm.trust_order_str()


def _tm(rings, **over):
    """A TrustModel with default scaffolding, overridable, for validation tests."""
    base = {
        "name": "t",
        "rings": rings,
        "action_min": "USER",
        "tiers": {2: ["CONTENT"]},
        "policy": {},
        "tuning": {"mode": "multivariate"},
        "models": {},
        "per_action": {},
        "judges": {},
        "metrics": {},
        "threat_model": {},
        "data": {},
    }
    base.update(over)
    return trust_spec.TrustModel(**base)


def _ring(name, integ, plane, **kw):
    return trust_spec.RingSpec(name=name, integrity=integ, plane=plane, **kw)


GOOD = (
    _ring("SYSTEM", 100, "control"),
    _ring("USER", 40, "request", prompt_key="pass_ctx_USER"),
    _ring("CONTENT", 30, "data", prompt_key="pass_ctx_data"),
)


def test_validate_rejects_integrity_mismatch():
    bad = (_ring("SYSTEM", 100, "control"), _ring("CONTENT", 999, "data", prompt_key="k"))
    with pytest.raises(ValueError, match="must match the proven lattice"):
        _tm(bad).validate()


def test_validate_rejects_two_control_rings():
    bad = (_ring("SYSTEM", 100, "control"), _ring("USER", 40, "control"))
    with pytest.raises(ValueError, match="exactly one control ring"):
        _tm(bad).validate()


def test_validate_rejects_duplicate_ring():
    with pytest.raises(ValueError, match="duplicate ring"):
        _tm((*GOOD, _ring("CONTENT", 30, "data", prompt_key="k"))).validate()


def test_validate_rejects_bad_plane():
    bad = (_ring("SYSTEM", 100, "control"), _ring("USER", 40, "nonsense", prompt_key="k"))
    with pytest.raises(ValueError, match="bad plane"):
        _tm(bad).validate()


# ---- retune._external_missing: reports exactly the absent scoring fields ----------------------------
@given(st.sets(st.sampled_from(["op", "kind", "ch", "content"])))
def test_external_missing_reports_absent_fields_for_clean_row(present):
    from cascading_lms import retune

    full = {"op": "o", "kind": "clean", "ch": [], "content": "c"}
    row = {k: full[k] for k in present}
    assert set(retune._external_missing(row)) == ({"op", "kind", "ch", "content"} - present)


def test_external_missing_attack_needs_class_specific_field():
    from cascading_lms import retune

    base = {"op": "o", "kind": "attack", "ch": [], "content": "c"}
    assert "tok" in retune._external_missing(
        {**base, "cls": "tok"}
    )  # token attack needs its canary
    assert "claim" in retune._external_missing({**base, "cls": "poison"})  # poison needs its claim
    assert (
        retune._external_missing({**base, "cls": "tok", "tok": "x"}) == []
    )  # well-shaped -> empty


# ---- config: dataclass defaults == committed values; the none-sentinel check -----------------------
def test_config_section_defaults_equal_loaded_values():
    # a section constructed with NO args must equal the TOML-loaded section (the defaults ARE the values).
    for section in (config.CFG.seeds, config.CFG.optimizer, config.CFG.api, config.CFG.generation):
        assert type(section)() == section


@given(st.sampled_from(["(none)", "none", "", "(NONE)", " (none) ", "(none).", "None."]))
def test_is_none_true_on_sentinels(text):
    assert config.is_none(text)


@given(
    st.text(min_size=1).filter(lambda s: s.strip().lower().strip(".") not in {"(none)", "none", ""})
)
def test_is_none_false_on_content(text):
    assert not config.is_none(text)
