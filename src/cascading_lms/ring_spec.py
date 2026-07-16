# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Canonical role/basis specification for every (ring, tier) cell, and a self-consistency checker.

This is the SINGLE SOURCE OF TRUTH for what each ring's conditioned passivation must do, at each tier,
and against which higher-trust basis. The pipeline (``retune``) and the constraints (``stage_check``) are
checked against it by :func:`check_consistency`, which is deterministic (no model calls) so it runs in the
offline gate. End-to-end (Q, R) optimisation cannot see per-stage correctness -- a ring can answer from the
world model instead of grounding in content, or "blab" rules for a role it does not play and waste the
prompt budget the layers above need -- so each cell's role is pinned here and enforced as a hard contract.

Integrity order (Biba): SYSTEM(100) > USER(40) > CONTENT(30) > WEB(20). A ring's basis is exactly the
higher-integrity rings PRESENT AT ITS TIER, in descending integrity; a ring never reads BELOW (no-read-down).
"""

from __future__ import annotations

import itertools
import re

from cascading_lms import trust_spec
from cascading_lms.harness import Ring

# The ring structure is DERIVED from the trust-model spec (trust_spec.DEFAULT) -- these names are kept as thin
# aliases so existing callers don't churn, but the single source of truth is data/trust_model.toml.
TIER_RINGS = trust_spec.DEFAULT.tier_rings  # rings carrying untrusted input at each tier
ROLE_SPEC = (
    trust_spec.DEFAULT.role_spec
)  # per-ring conditioned-passivation goal / grounding / must-not


def basis_rings(ring: Ring, tier: int) -> list[Ring]:
    """The higher-integrity REQUEST/context rings that form ``ring``'s basis at ``tier`` (descending). DATA rings (CONTENT/WEB) do NOT enter another ring's basis: relevance and the CONTENT>WEB order are the wrapper's job (and cross-ring relevance connectivity needs the full picture, which only the wrapper has), so a data ring conditions on the request only, never on other data. Derived from the spec (plane=request/control basis rings)."""
    return trust_spec.DEFAULT.basis(tier, ring)


def _basis_fails() -> list[tuple]:
    """Every ring's basis, as the pipeline accumulates it top-down, must equal the spec's basis_rings."""
    fails = []
    for tier, rings in TIER_RINGS.items():
        higher: list = []
        for ring in sorted(rings, key=lambda r: -int(r)):  # pipeline's top-down order
            got = [r for r, _ in higher]
            want = basis_rings(ring, tier)
            if got != want:
                fails.append(
                    (f"basis:{ring.name}@t{tier}", [r.name for r in got], [r.name for r in want])
                )
            if trust_spec.DEFAULT.in_basis(
                ring
            ):  # only REQUEST/context rings enter the basis (matches pipeline)
                higher.append((ring, "passivated"))
    return fails


def _coverage_fails() -> list[tuple]:
    """Every ring that occurs has a role prompt and a constraint; no occurring cell is left undefined."""
    from cascading_lms import config
    from cascading_lms import retune as R
    from cascading_lms import stage_check as SC

    fails: list[tuple] = []  # heterogeneous diagnostic tuples (name, value|None, detail)
    occurring = {r for rings in TIER_RINGS.values() for r in rings}
    for ring in occurring:
        key = ROLE_SPEC[ring]["prompt_key"]
        if not R.P.get(key):  # require the per-ring key explicitly (the pass_ctx fallback is dead)
            fails.append((f"prompt-missing:{ring.name}", key, "no per-ring role prompt"))
        # CONSUMPTION check (not just existence): the key the runtime actually resolves for this ring
        # (config.PASS_CTX_KEY, used by retune._passivate_conditioned) must equal the spec's prompt_key.
        # This is exactly the blind spot that let the pass_ctx_data wiring bug hide.
        if config.PASS_CTX_KEY.get(ring.name) != key:
            fails.append(
                (f"prompt-key-mismatch:{ring.name}", config.PASS_CTX_KEY.get(ring.name), key)
            )
    if not hasattr(SC, "check_conditioned"):
        fails.append(("constraint-missing", "check_conditioned", "no conditioned stage contract"))
    return fails


def _seq_pairs(seq: str) -> list[tuple]:
    """Consecutive (A, B) pairs of a 'A > B > C' ring-order chain."""
    toks = [t.strip() for t in seq.split(">")]
    return list(itertools.pairwise(toks))  # consecutive (A, B) pairs


def _order_pairs(text: str, names: str) -> list[tuple]:
    """(A, B) ring pairs an order is STATED over in ``text``.

    Covers both the canonical 'A > B' chain form and the prose 'A outranks B' / 'A takes precedence over B'
    form the seed prompts actually use.
    """
    chain = re.compile(rf"(?:\b(?:{names})\b\s*>\s*)+\b(?:{names})\b")
    verb = re.compile(
        rf"\b({names})\b\s+(?:outranks?|takes? precedence over|is higher(?:-| )trust(?:ed)? than)\s+\b({names})\b",
        re.I,
    )
    pairs = [p for seq in chain.findall(text) for p in _seq_pairs(seq)]
    pairs += [(m.group(1).upper(), m.group(2).upper()) for m in verb.finditer(text)]
    return pairs


def _order_fails() -> list[tuple]:
    """Any ring order STATED in a seed prompt must agree with the spec's integrity order.

    Kills the silent desync the portability audit flagged: reorder the lattice (spec + enum) but leave a
    prompt's prose stale, and the model is instructed with the wrong hierarchy while the proof stays green.
    Covers the canonical 'A > B' AND the prose 'A outranks B' forms (the live wrapper_ctx states CONTENT>WEB in
    prose). Only a genuine CONTRADICTION (a pair the spec ranks the other way) fails; a correct order does not.
    """
    from cascading_lms import retune as R

    integ = {r.name: r.integrity for r in trust_spec.DEFAULT.rings}
    names = "|".join(integ)
    fails: list[tuple] = []
    for key, text in R.P.items():
        if not isinstance(text, str):
            continue
        fails += [
            (f"order-desync:{key}", f"{a}>{b}", "prompt states an order the spec contradicts")
            for a, b in _order_pairs(text, names)
            if integ[a] <= integ[b]
        ]
    return fails


def check_consistency() -> list[tuple]:
    """Deterministic self-consistency check of the code against this spec (empty list = consistent)."""
    return _basis_fails() + _coverage_fails() + _order_fails()
