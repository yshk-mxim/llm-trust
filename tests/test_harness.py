# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Tests for harness.py (ADR-001). Acceptance gate: S_authority ≡ 0. Dependency-free."""

import random

import pytest

from cascading_lms.harness import (
    Labeled,
    Principal,
    PrincipalToken,
    Ring,
    authorize_action,
    derive,
    endorse,
    ingest,
    resolve_op,
    set_thresholds,
)


def test_ingest_stamps_by_channel():
    labeled = ingest("hi", Ring.WEB)
    assert labeled.ring == Ring.WEB and labeled.payload == "hi"


def test_derive_ratchet_never_raises():
    a, b = ingest("a", Ring.SYSTEM), ingest("b", Ring.WEB)
    assert derive("x", [a, b]).ring == Ring.WEB  # meet = lowest
    assert derive("y", []).ring == Ring.UNTRUSTED  # empty -> conservative


def test_forge_labeled_denied():
    with pytest.raises(PermissionError):
        Labeled("x", Ring.SYSTEM)


def test_forge_token_denied():
    with pytest.raises(PermissionError):
        PrincipalToken(Ring.SYSTEM, Ring.SYSTEM)


def test_endorse_requires_valid_token():
    web = ingest("x", Ring.WEB)
    for bad in (object(), None, "token"):
        with pytest.raises(PermissionError):
            endorse(web, Ring.SYSTEM, bad)


def test_endorse_capped_at_principal():
    web = ingest("x", Ring.WEB)
    tok = Principal(Ring.USER).authorize(Ring.SYSTEM)  # USER tries to endorse to SYSTEM
    assert endorse(web, Ring.SYSTEM, tok).ring == Ring.USER  # capped at endorser, not SYSTEM


def test_endorse_never_lowers():
    sysdoc = ingest("s", Ring.SYSTEM)
    tok = Principal(Ring.USER).authorize(Ring.WEB)
    assert endorse(sysdoc, Ring.WEB, tok).ring == Ring.SYSTEM  # raise-only


def test_actuator_gate():
    set_thresholds(Ring.USER)
    assert authorize_action(ingest("a", Ring.SYSTEM)) is True
    assert authorize_action(ingest("a", Ring.USER)) is True
    assert authorize_action(ingest("a", Ring.WEB)) is False


def test_resolve_op_binds_highest():
    r = resolve_op([ingest("translate", Ring.USER), ingest("summarize", Ring.SYSTEM)])
    assert r.op == "summarize" and r.ring == Ring.SYSTEM


def test_op_retype_from_below_denied():
    # user "ignore, translate" cannot re-type the SYSTEM "summarize"
    r = resolve_op([ingest("summarize", Ring.SYSTEM), ingest("ignore, translate", Ring.USER)])
    assert r.op == "summarize"


def test_legit_endorsement_enables_action():
    set_thresholds(Ring.USER)
    web = ingest("doc", Ring.WEB)
    assert authorize_action(web) is False
    up = endorse(web, Ring.USER, Principal(Ring.SYSTEM).authorize(Ring.USER))
    assert up.ring == Ring.USER and authorize_action(up) is True  # system works, not just denies


def test_s_authority_zero_sweep(n=30000):
    """A content-only adversary (controls WEB/UNTRUSTED channels, holds NO Principal) can
    never obtain an authorized action, raise a ring, or launder taint — across random
    sequences of ingest/derive/failed-endorse."""
    set_thresholds(Ring.USER)
    rng = random.Random(1)
    low = [Ring.WEB, Ring.UNTRUSTED]
    breaches = 0
    for _ in range(n):
        pool = [
            ingest(f"c{rng.randrange(9999)}", rng.choice(low)) for _ in range(rng.randint(1, 4))
        ]
        for _ in range(rng.randint(0, 6)):
            if rng.random() < 0.6:
                k = rng.randint(1, len(pool))
                pool.append(derive("d", rng.sample(pool, k)))
            else:  # forged/absent-token endorse MUST fail and not mutate the pool
                try:
                    endorse(rng.choice(pool), rng.choice(list(Ring)), object())
                    breaches += 1
                except PermissionError:
                    pass
        for item in pool:
            if authorize_action(item) or int(item.ring) > int(Ring.WEB):
                breaches += 1
    assert breaches == 0, f"S_authority breaches: {breaches}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\nALL {len(fns)} PASS — S_authority ≡ 0 over 30k-sequence sweep")
