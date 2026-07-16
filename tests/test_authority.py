# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Authority non-interference invariants of the deterministic monitor (paper Sec. 3, invariants I1-I5).

These are offline (no model): the guarantee is a property of the harness code, not the model.
"""

import pytest

from cascading_lms.harness import (
    Labeled,
    Principal,
    Ring,
    authorize_action,
    derive,
    endorse,
    ingest,
    meet,
    resolve_op,
    set_thresholds,
)


def test_ingest_stamps_channel_ring():
    """ingest labels a payload with the ring of the channel it arrived on."""
    assert ingest("hi", Ring.WEB).ring is Ring.WEB
    assert ingest("hi", Ring.SYSTEM).ring is Ring.SYSTEM


def test_meet_is_the_minimum_ring():
    """meet (the integrity greatest-lower-bound) is the least-trusted ring."""
    assert meet([Ring.SYSTEM, Ring.WEB]) is Ring.WEB
    assert meet([Ring.USER, Ring.CONTENT]) is Ring.CONTENT
    assert meet([]) is Ring.UNTRUSTED


def test_i1_derive_ratchets_authority_down():
    """I1: a value derived from others takes the meet of their rings; authority only drops."""
    high = ingest("trusted", Ring.SYSTEM)
    low = ingest("web", Ring.WEB)
    assert derive("combined", [high, low]).ring is Ring.WEB
    assert derive("just-high", [high]).ring is Ring.SYSTEM


def test_i5_resolve_op_binds_to_highest_ring():
    """I5: the operation binds to the highest-ring instruction, never a lower one."""
    ops = [ingest("web says do X", Ring.WEB), ingest("do the real task", Ring.SYSTEM)]
    bound = resolve_op(ops)
    assert bound.ring is Ring.SYSTEM
    assert bound.op == "do the real task"


def test_i3_actuator_gates_on_authority_threshold():
    """I3: an effect fires only when its authority ring meets ACTION_MIN."""
    set_thresholds(Ring.USER)
    assert authorize_action(ingest("op", Ring.USER)) is True
    assert authorize_action(ingest("op", Ring.SYSTEM)) is True
    assert authorize_action(ingest("op", Ring.CONTENT)) is False
    assert authorize_action(ingest("op", Ring.WEB)) is False


def test_data_tainted_authority_is_denied_not_escalated():
    """Tainting an authority with low-ring data only pushes its ring DOWN (fail-safe): it is denied."""
    set_thresholds(Ring.USER)
    control = ingest("send the email", Ring.USER)
    tainted = derive("send the email", [control, ingest("web data", Ring.WEB)])
    assert authorize_action(control) is True
    assert authorize_action(tainted) is False  # data taint cannot ENABLE an effect.


def test_i4_labeled_cannot_be_forged():
    """I4: a Labeled stamp cannot be minted by direct construction (only ingest/derive/endorse)."""
    with pytest.raises(PermissionError):
        Labeled("payload", Ring.SYSTEM)


def test_i2_endorse_requires_a_principal_token():
    """I2: raising a ring needs an out-of-band PrincipalToken; content cannot mint one."""
    low = ingest("data", Ring.WEB)
    with pytest.raises((PermissionError, TypeError)):
        endorse(low, Ring.SYSTEM, token=None)


def test_endorse_caps_at_the_endorser_ring():
    """endorse raises only up to min(target, endorser ring); it can never exceed the endorser."""
    low = ingest("data", Ring.WEB)
    operator = Principal(Ring.USER)
    raised = endorse(low, Ring.SYSTEM, operator.authorize(Ring.SYSTEM))
    assert raised.ring is Ring.USER  # capped at the USER endorser, not SYSTEM.


def test_endorse_never_lowers_an_already_higher_ring():
    """endorse takes the max of the current and granted ring; it cannot demote."""
    high = ingest("data", Ring.SYSTEM)
    operator = Principal(Ring.USER)
    raised = endorse(high, Ring.USER, operator.authorize(Ring.USER))
    assert raised.ring is Ring.SYSTEM


def test_ring_order_matches_the_trust_lattice():
    """The lattice order used throughout is SYSTEM > USER > CONTENT > WEB > UNTRUSTED."""
    assert (
        int(Ring.SYSTEM) > int(Ring.USER) > int(Ring.CONTENT) > int(Ring.WEB) > int(Ring.UNTRUSTED)
    )


# --- action plane: the MEASURED companion to prove_authority (I3+I5 on concrete injection cases) -----------
def test_action_plane_actuator_fires_zero_on_injected_effects():
    """Every injected effect (an action requested by untrusted content) is DENIED and never re-binds the op.

    This is the measured form of prove_authority's exhaustive 8729/0: the actuator fires 0x and op_hijacked is
    0x across the whole corpus, because each effect's authority traces to the data ring it was injected into.
    """
    from cascading_lms import final_eval as F

    recs = F.run_action_plane()
    assert recs, "action-injection corpus is empty"
    assert all(r["defended"] for r in recs)
    assert sum(r["action_fired"] for r in recs) == 0
    assert sum(r["op_hijacked"] for r in recs) == 0


def test_action_plane_gate_is_authorize_action_not_a_judge():
    """The gate is the deterministic harness boundary, not a model verdict: it DISCRIMINATES by ring.

    A data-ring-derived effect is denied, but the SAME action authorised from the request ring FIRES -- so the
    0x result is the boundary working, not a stuck always-deny.
    """
    from cascading_lms import final_eval as F
    from cascading_lms import trust_spec

    row = F.load_action_injections()[0]
    threshold = trust_spec.DEFAULT.action_threshold(row.get("action_name", ""))
    data_authority = derive(row["action"], [ingest(row["content"], F._inject_ring(row))])
    assert not authorize_action(data_authority, min_ring=threshold)  # injected -> denied
    user_authority = ingest("the operator's own request", trust_spec.DEFAULT.request_ring)
    assert authorize_action(user_authority, min_ring=threshold)  # legitimately authorised -> fires
