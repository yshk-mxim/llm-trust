# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Self-consistency of the pipeline against the canonical (ring, tier) role/basis spec (offline, no model).

Guards the invariants that end-to-end (Q, R) optimisation cannot see: every occurring (ring, tier) cell has
a defined role prompt and constraint, each ring's basis is exactly its higher-integrity rings (no read-down),
and the tier->ring map matches how the pipeline derives a tier.
"""

from cascading_lms import ring_spec, trust_spec


def test_pipeline_conforms_to_spec():
    """The deterministic self-consistency check finds no spec/code mismatch."""
    fails = ring_spec.check_consistency()
    assert not fails, f"spec/code mismatch: {fails}"


def test_no_ring_reads_below():
    """Every (ring, tier) basis holds only strictly-higher-integrity rings (Biba no-read-down)."""
    for tier, rings in ring_spec.TIER_RINGS.items():
        for ring in rings:
            for higher in ring_spec.basis_rings(ring, tier):
                assert int(higher) > int(ring), f"{higher.name} not higher than {ring.name}@t{tier}"


def test_tier_ring_map_matches_pipeline():
    """ring_spec.TIER_RINGS agrees with retune._tier for each canonical channel set."""
    from cascading_lms import retune as R

    for tier, rings in ring_spec.TIER_RINGS.items():
        assert R._tier([(r, "x") for r in rings]) == tier


def test_ring_roles_are_pure_for_any_lattice():
    """PORTABLE role-purity invariants (hold for the active lattice, whatever it is): every passivated ring
    declares a prompt_key and a non-empty must-not, and a request ring's key differs from a data ring's."""
    tm = trust_spec.DEFAULT
    for spec in ring_spec.ROLE_SPEC.values():
        assert spec["prompt_key"], "every passivated ring needs a passivation prompt key"
        assert spec["must_not"], "every ring must declare the roles it does NOT play"
    req = [r.ring for r in tm.active_rings if tm.in_basis(r.ring) and not tm.is_control(r.ring)]
    data = [r.ring for r in tm.active_rings if tm.is_data(r.ring)]
    if req and data:  # a request ring's role/key stays distinct from a data ring's
        assert (
            ring_spec.ROLE_SPEC[req[0]]["prompt_key"] != ring_spec.ROLE_SPEC[data[0]]["prompt_key"]
        )


def test_default_spec_pins_the_4_ring_lattice():
    """The DEFAULT spec (loaded explicitly) is specifically the paper's 4-ring set with shared data passivation
    -- the reference instance stays pinned even though the pipeline is now lattice-portable."""
    tm = trust_spec.TrustModel.load("data/trust_model.toml")
    keys = {r.name: r.prompt_key for r in tm.active_rings if r.plane != "control"}
    assert set(keys) == {"USER", "CONTENT", "WEB"}
    assert keys["USER"] == "pass_ctx_USER"
    assert keys["CONTENT"] == keys["WEB"] == "pass_ctx_data"  # shared, by design
