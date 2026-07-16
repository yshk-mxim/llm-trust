# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Exhaustive authority non-interference check over the FINITE ring lattice.

A proof by exhaustion: the monitor is deterministic and the label space is finite (7 rings), so enumerating
all input-ring configurations up to a bounded depth is exhaustive over the per-step label algebra.
Generalisation to arbitrary input depth rests on one lemma: meet=min is monotone (adding a ring only LOWERS
the meet), so a bounded-depth configuration is representative of any deeper one -- the enumeration covers the
algebra, it is not a random sample. Runs only when invoked (``prove()`` / ``__main__``): no import-time
side effect. Checks:
  A  actuator decision  = (meet(sources) >= ACTION_MIN)             [I3 correct]
  B  actuator NI        : adding ANY ring to ANY config never flips deny->allow  [sub-threshold
                          content can never ENABLE an effect -- the safe direction]
  C  op-binding         = highest ring; adding a strictly-lower instruction never re-binds  [I5, NI]
  D  endorsement        = max(content, min(target, principal)); content cannot self-raise  [I1/I2].
"""

import json
from itertools import product

from cascading_lms import config, trust_spec
from cascading_lms.harness import (
    Principal,
    Ring,
    authorize_action,
    derive,
    endorse,
    ingest,
    resolve_op,
)

RINGS = list(Ring)
# The action threshold the proof enumerates is DERIVED from the active spec (the trust model:
# actions.action_min), the SAME source harness.authorize_action resolves at runtime -- so the proven
# property and the runtime enforcement can never diverge. Default action_min: USER == Ring.USER, so the
# 8729/0 certificate is byte-identical.
ACTION_MIN = Ring[trust_spec.DEFAULT.action_min]

# Bounded representative depth for the exhaustive enumeration: meet=min is monotone (adding a ring only
# LOWERS the meet), so a configuration of this depth is representative of any DEEPER one -- the enumeration
# covers the label algebra, it is not a sample. Checks A/C enumerate to this depth; B's base goes one less,
# since _check_b_base then adds a ring (reaching this depth).
_PROOF_DEPTH = 4


def _auth(rings):
    """Authority derived from a config of source rings (each ingested): its ring = meet(rings)."""
    return derive("a", [ingest("x", r) for r in rings])


def _check_a():
    """A: the actuator fires iff meet(sources) >= ACTION_MIN, over source-ring tuples to _PROOF_DEPTH."""
    fails, n = [], 0
    for k in range(1, _PROOF_DEPTH + 1):
        for combo in product(RINGS, repeat=k):
            n += 1
            # prove the SPEC's threshold explicitly (min_ring=ACTION_MIN) -- the same value the runtime resolves
            if authorize_action(_auth(combo), min_ring=ACTION_MIN) != (
                min(int(r) for r in combo) >= int(ACTION_MIN)
            ):
                fails.append(("A", combo))
    return fails, n


def _check_b_base(base):
    """Non-interference for one base config: adding any single ring must not flip deny->allow."""
    base_ok = authorize_action(_auth(base))
    fails = [("B", base, r) for r in RINGS if (not base_ok) and authorize_action(_auth([*base, r]))]
    return fails, len(RINGS)


def _check_b():
    """B: adding any ring to any config never flips deny->allow (sub-threshold content can't enable)."""
    fails, n = [], 0
    for k in range(1, _PROOF_DEPTH):  # base one less than _PROOF_DEPTH; _check_b_base adds a ring
        for base in product(RINGS, repeat=k):
            base_fails, count = _check_b_base(base)
            fails += base_fails
            n += count
    return fails, n


def _check_c():
    """C: op-binding = highest ring, and a strictly-lower instruction never re-binds it."""
    fails, n = [], 0
    for k in range(2, _PROOF_DEPTH + 1):
        for combo in product(RINGS, repeat=k):
            n += 1
            instrs = [ingest(f"op{i}", r) for i, r in enumerate(combo)]
            top = max(int(r) for r in combo)
            if int(resolve_op(instrs).ring) != top:
                fails.append(("C-bind", combo))
            fails.extend(
                ("C-NI", combo, r)
                for r in RINGS
                if int(r) < top and int(resolve_op([*instrs, ingest("x", r)]).ring) != top
            )
    return fails, n


def _check_d():
    """D: endorsement caps at min(target, principal) and never below the current content ring."""
    fails, n = [], 0
    for cr, pr, tgt in product(RINGS, RINGS, RINGS):
        n += 1
        res = endorse(ingest("c", cr), tgt, Principal(pr).authorize(tgt))
        if int(res.ring) != max(int(cr), min(int(tgt), int(pr))):
            fails.append(("D", cr, pr, tgt))
    return fails, n


def prove():
    """Run every check, write the certificate to config.AUTHORITY_PROOF, and return it."""
    violations, n = [], 0
    for check in (_check_a, _check_b, _check_c, _check_d):
        fails, count = check()
        violations += fails
        n += count
    status = (
        "PROVED (exhaustive over the finite lattice, no counterexample)"
        if not violations
        else f"VIOLATIONS: {violations[:5]}"
    )
    out = {
        "rings": [r.name for r in RINGS],
        "action_min": ACTION_MIN.name,
        "configurations_checked": n,
        "violations": len(violations),
        "status": status,
    }
    with open(config.AUTHORITY_PROOF, "w") as fh:
        json.dump(out, fh, indent=2)
    return out


if __name__ == "__main__":
    print(json.dumps(prove(), indent=2))
