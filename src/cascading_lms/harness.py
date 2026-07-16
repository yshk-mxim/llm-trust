# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Harness authority core: the deterministic monitor that holds all authority (paper Sec. 3).

No model calls happen here, so authority non-interference is a property of this code alone (unit-tested in
``tests/test_authority.py`` and machine-checked exhaustively in ``prove_authority.py``). Generators are
authority-free: they may call the public labeling primitives but never hold a ``Principal``, so they
cannot mint authority.

Invariants:
  I1 monotonic  : only ``endorse`` raises a ring (capped at the endorser); ``derive`` = meet.
  I2 endorse OOB: raising a ring requires a ``PrincipalToken``, minted only by a ``Principal``.
  I3 actuator   : ``authorize_action`` allows iff ring >= ACTION_MIN (ring == meet(provenance)).
  I4 taint      : payloads exist only inside ``Labeled`` (guarded construction) -- no bare path.
  I5 op-binding : ``resolve_op`` binds the op to the highest-ring instruction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum


class Ring(IntEnum):
    """Integrity ring: an input's trust level, set by its channel at ingress. Higher is more trusted."""

    # These integers ARE the lattice (not magic constants): the integrity order the whole system enforces.
    # data/trust_model.toml declares the same values; trust_spec validates spec==enum so they can't diverge.
    UNTRUSTED = 0  # no provenance / the empty meet -- the bottom of the lattice
    WEB = 20  # externally retrieved / tool output (lowest trust data)
    CONTENT = 30  # a document the user attached (its content is untrusted data)
    USER = 40  # the user's own request (an intent, not a fact source)
    RAG = 60  # retrieved trusted knowledge
    SYSTEM_MEMORY = 80  # trusted persistent memory
    SYSTEM = 100  # the operator's instruction / the task -- the top of the lattice


# Actuator threshold, spec-derived (prove_authority sets it from the trust model's action_min). Default: acting needs USER+.
ACTION_MIN: Ring = Ring.USER


def set_thresholds(action_min: Ring) -> None:
    """Set the actuator authority ring threshold (an effect authorises iff its ring >= ACTION_MIN)."""
    global ACTION_MIN
    ACTION_MIN = Ring(action_min)


# Provenance-record tags: the operation that stamped a Labeled (named, not bare string literals). They are
# audit metadata inside the provenance tuple -- authority decisions read only the RING, never these tags.
_PROV_INGEST = "ingest"
_PROV_DERIVE = "derive"
_PROV_ENDORSE = "endorse"

# Private construction capability: only this module's factories hold `_KEY`, so neither
# content nor models can forge a stamp (I4) or a token (I2) by direct construction.
_KEY = object()


@dataclass(frozen=True)
class Labeled:
    """Immutable (payload, ring, provenance). Build ONLY via ingest/derive/endorse -- direct construction is refused."""

    payload: str
    ring: Ring
    provenance: tuple = ()
    _key: object = field(default=None, repr=False, compare=False, hash=False)

    def __post_init__(self):
        """Refuse a forged stamp: a Labeled must be built through the module's factories (I4)."""
        if self._key is not _KEY:
            raise PermissionError(
                "Labeled must be built via ingest/derive/endorse (stamp forging is not permitted)"
            )


def _make(payload: str, ring, provenance: tuple = ()) -> Labeled:
    """Construct a Labeled through the private capability (the only path that mints a stamp)."""
    return Labeled(str(payload), Ring(ring), tuple(provenance), _KEY)


def meet(rings: Sequence[Ring]) -> Ring:
    """Integrity greatest-lower-bound: the least-trusted (minimum) ring, or UNTRUSTED if empty."""
    rings = [Ring(r) for r in rings]
    return Ring(min(int(r) for r in rings)) if rings else Ring.UNTRUSTED


def ingest(payload: str, channel_ring: Ring) -> Labeled:
    """Ingress: the one trusted labeling boundary, stamping a payload with its channel's ring."""
    return _make(payload, channel_ring, provenance=((_PROV_INGEST, int(Ring(channel_ring))),))


def derive(payload: str, inputs: Sequence[Labeled]) -> Labeled:
    """Stamp a generator output at meet(inputs), so authority only ever drops (I1)."""
    inputs = list(inputs)
    r = meet([i.ring for i in inputs]) if inputs else Ring.UNTRUSTED
    prov = (_PROV_DERIVE, tuple(int(i.ring) for i in inputs))
    return _make(payload, r, provenance=(prov,))


@dataclass(frozen=True)
class BoundOp:
    """An operation bound to the ring of the instruction that specified it."""

    op: str
    ring: Ring


def resolve_op(op_instructions: Sequence[Labeled]) -> BoundOp:
    """Bind the operation to the highest-ring instruction (I5); a lower ring can never re-bind it."""
    ops = list(op_instructions)
    if not ops:
        raise ValueError("resolve_op: no operation instruction provided")
    top = max(ops, key=lambda x: int(x.ring))  # bind to highest ring; no conflict detection
    return BoundOp(op=top.payload, ring=Ring(top.ring))


@dataclass(frozen=True)
class PrincipalToken:
    """An out-of-band capability authorizing a ring raise, minted only by a Principal (I2)."""

    principal_ring: Ring
    target: Ring
    _key: object = field(default=None, repr=False, compare=False, hash=False)

    def __post_init__(self):
        """Refuse a forged token: a PrincipalToken is minted only by Principal.authorize (I2)."""
        if self._key is not _KEY:
            raise PermissionError("PrincipalToken is minted only by Principal.authorize")


class Principal:
    """An out-of-band authority holder (human approval / policy); only it can mint tokens."""

    def __init__(self, ring: Ring):
        """Hold authority at ``ring``."""
        self.ring = Ring(ring)

    def authorize(self, target: Ring) -> PrincipalToken:
        """Mint a token endorsing content up toward ``target`` (capped at this principal's ring)."""
        return PrincipalToken(self.ring, Ring(target), _KEY)


def endorse(content: Labeled, target: Ring, token: PrincipalToken) -> Labeled:
    """Raise a ring, only up to min(target, endorser); never above the endorser, never demoting (I1/I2)."""
    if not isinstance(token, PrincipalToken) or token._key is not _KEY:
        raise PermissionError("endorse requires a valid PrincipalToken")
    target = Ring(target)
    grant = min(int(target), int(token.principal_ring))
    result = Ring(max(int(content.ring), grant))
    prov = (*content.provenance, (_PROV_ENDORSE, int(token.principal_ring), int(target)))
    return _make(content.payload, result, provenance=prov)


def authorize_action(authority: Labeled, min_ring: Ring | None = None) -> bool:
    """Allow an effect iff its authority ring meets the action threshold (I3).

    ``min_ring`` lets a caller pass a PER-ACTION threshold (resolved from the spec's ``actions.per_action``);
    when omitted it is the global ``ACTION_MIN``, so existing callers and the exhaustive proof are unchanged.

    The caller MUST build ``authority`` from control-plane inputs only; operands are passed separately and
    never widen it. Tainting authority with data only pushes its ring DOWN (the derive ratchet), so the
    failure mode is always safe: a data-tainted authority is denied, never wrongly allowed.
    """
    threshold = ACTION_MIN if min_ring is None else Ring(min_ring)
    return int(authority.ring) >= int(threshold)
