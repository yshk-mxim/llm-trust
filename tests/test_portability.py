# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Portability acceptance (offline): a genuinely DIFFERENT lattice reconfigures the whole trust model by
config alone -- it loads, validates against the enum, derives its full structure, and the authority proof
still holds. This is the evidence behind the paper's "revised as an organisation's definition of authority
changes" claim.
"""

import os
import subprocess
import sys

from cascading_lms import config, trust_spec
from cascading_lms import prove_authority as PA
from cascading_lms.harness import Ring


def _example2():
    return trust_spec.TrustModel.load(config.data_path("trust_model_example2.toml"))


def test_example2_loads_and_validates_by_config_alone():
    tm = _example2()  # validate() runs inside load(); a raise here would fail the test
    assert tm.name == "example2-5ring-rag"
    assert tm.mode == "coordinate"  # the mode toggle took effect


def test_example2_activates_a_trusted_data_ring_above_user():
    tm = _example2()
    # RAG (integrity 60) is ABOVE USER (40) yet on the DATA plane -- the case the old int-predicates couldn't do
    assert Ring.RAG.value > Ring.USER.value
    assert tm.is_data(Ring.RAG) and not tm.in_basis(Ring.RAG) and tm.is_passivated(Ring.RAG)
    assert Ring.RAG in tm.passr
    assert Ring.RAG in tm.tier_rings[4]
    assert tm.pass_ctx_key["RAG"] == "pass_ctx_data"  # shares the data passivation key
    assert (
        tm.trust_order_str() == "SYSTEM > RAG > USER > CONTENT > WEB"
    )  # rendered from the new lattice


def test_example2_derived_structure_differs_from_default():
    ex2, dflt = _example2(), trust_spec.DEFAULT
    assert ex2.tier_rings[4] != dflt.tier_rings[4]  # RAG added at tier 4
    assert Ring.RAG in ex2.role_spec and Ring.RAG not in dflt.role_spec


def test_authority_proof_still_holds():
    # the proof enumerates the shared Ring enum, so it is unaffected by which spec is loaded; call the sub-checks
    # directly (prove() would rewrite the committed certificate as a side effect).
    violations = PA._check_a()[0] + PA._check_b()[0] + PA._check_c()[0] + PA._check_d()[0]
    assert violations == []


# The GENERATION plane binds the ring structure at import (module-level aliases derive from trust_spec.DEFAULT),
# so exercising a DIFFERENT active lattice needs a fresh interpreter with the spec-selector env var set.
_GEN_PLANE_PROBE = """
from cascading_lms import trust_spec, retune, ring_spec, stage_check, final_eval
from cascading_lms.harness import Ring
tm = trust_spec.DEFAULT
assert tm.name == "example2-5ring-rag", tm.name
ch, low = retune._tierch(4, {"content": "DOC"})           # channel builder includes the added RAG ring
assert Ring.RAG in {r for r, _ in ch} and low is Ring.WEB
assert Ring.RAG in {r for r, _ in final_eval.skel(4, {"content": "DOC"})[0]}  # eval channel too
assert retune._blind_pass(Ring.RAG, 4)                     # blind passivation prompt resolves (generic fallback)
assert len(stage_check._ctx_probes(Ring.RAG)) == 1  # a NEW data ring gets a REAL generic contract (not inert)
assert ring_spec.check_consistency() == [], ring_spec.check_consistency()
assert Ring.RAG in tm.passr
print("GEN-PLANE-OK")
"""


def test_example2_generation_plane_runs_by_config_alone():
    """With example2 the ACTIVE spec ($TRUST_MODEL_SPEC), the GENERATION plane builds every structure for the
    added RAG ring -- channel/eval-case builders, blind + conditioned passivation prompts, and the stage
    contract -- with NO KeyError and check_consistency()==[]. This is the generation-plane portability the
    lattice/proof/derivation tests above do not, on their own, establish. Offline; a fresh interpreter because
    the ring aliases bind at import.
    """
    env = {
        **os.environ,
        trust_spec.SPEC_ENV: config.data_path("trust_model_example2.toml"),
        "PYTHONPATH": "src",
    }
    r = subprocess.run(
        [sys.executable, "-c", _GEN_PLANE_PROBE],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "GEN-PLANE-OK" in r.stdout, (
        f"rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr[-2000:]}"
    )
