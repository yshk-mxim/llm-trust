# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Trust-AGNOSTIC tests: the trust-sensitive logic must be correct for a DIFFERENT lattice, not just the
default. Loads tests/fixtures/test_trust_model.toml (a 3-ring SYSTEM/USER/CONTENT model) and asserts the
derived structure -- then builds the WHOLE pipeline under it -- proving the code is driven by the spec, not
the hardcoded 4-ring default. (The user asked for a test lattice "created and loaded just for testing items
where you need a trust lattice".)
"""

import os
import subprocess
import sys

import pytest

from cascading_lms import trust_spec
from cascading_lms.harness import Ring

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "test_trust_model.toml")


@pytest.fixture(scope="module")
def tm():
    """The 3-ring test lattice, loaded + validated (a genuinely different model from the default)."""
    return trust_spec.TrustModel.load(FIXTURE)


def test_trust_order_and_control_request(tm):
    assert tm.trust_order_str() == "SYSTEM > USER > CONTENT"
    assert tm.control_ring is Ring.SYSTEM
    assert tm.request_ring is Ring.USER


def test_planes_partition_the_active_rings(tm):
    # every active ring is in EXACTLY one plane -- the predicates partition, they don't overlap or gap.
    for r in (rr.ring for rr in tm.active_rings):
        assert tm.is_control(r) + tm.is_request(r) + tm.is_data(r) == 1
        assert tm.is_passivated(r) == (not tm.is_control(r))
        assert tm.in_basis(r) == (tm.is_control(r) or tm.is_request(r))


def test_single_data_ring_roles(tm):
    # the 3-ring lattice has ONE data ring -> secondary is None (the edge the default 4-ring never exercises).
    assert [r.name for r in tm.data_rings] == ["CONTENT"]
    assert tm.primary_data_ring is Ring.CONTENT
    assert tm.secondary_data_ring is None
    assert tm.passr == {Ring.USER, Ring.CONTENT}


def test_derived_tier_and_passivation_structure(tm):
    assert {t: [r.name for r in rs] for t, rs in tm.tier_rings.items()} == {
        2: ["CONTENT"],
        3: ["USER", "CONTENT"],
    }
    assert tm.pass_ctx_key == {"USER": "pass_ctx_USER", "CONTENT": "pass_ctx_data"}
    assert tm.action_threshold() is Ring.USER


def test_pipeline_builds_and_stays_consistent_under_the_test_lattice():
    # The WHOLE pipeline (dataset construction + the deterministic self-consistency contract) must build and
    # hold for the 3-ring lattice, not just the default -- the real proof that construction + contracts are
    # spec-driven. Runs in a subprocess because trust_spec.DEFAULT is import-time (TRUST_MODEL_SPEC selects it).
    code = (
        "from cascading_lms import trust_spec, ring_spec, retune;"
        "assert trust_spec.DEFAULT.trust_order_str() == 'SYSTEM > USER > CONTENT';"
        "assert len(retune.TRAIN) and len(retune.VAL) and len(retune.OOD);"
        "assert ring_spec.check_consistency() == [], ring_spec.check_consistency();"
        "print('AGN-OK')"
    )
    env = {**os.environ, "TRUST_MODEL_SPEC": FIXTURE, "PYTHONPATH": "src"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd="."
    )
    assert "AGN-OK" in out.stdout, out.stderr
