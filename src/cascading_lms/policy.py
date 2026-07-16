# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Plugin registry for external PARSERS (input transforms) and GUARDS (in/out checks).

A library user registers their own -- a LlamaGuard API client, a PII redactor, a topic-ban classifier, an
output-policy check -- WITHOUT forking the pipeline. The spec's ``policy.input`` / ``policy.output`` list
parsers and guards BY NAME (ordered); the registry resolves each name to the registered callable.

  register_parser(name, fn)                 fn(text, ring, ctx) -> text        # transform INPUT (sanitize/redact)
  register_guard(name, fn, plane, enforce)  fn(ctx) -> GuardResult|bool        # a pass/fail check, IN or OUT

A guard with ``enforce=True`` fails CLOSED -- a failure is a hard, external refusal (the runtime dual of the
optimiser's feasibility constraint). ``enforce=False`` is advisory (feeds the metric / tuning gradient only).
Built-ins wired by the pipeline: ``passivate`` (the cascade's own input transform) and the three monitor
portions ``obeyed_only_system`` / ``grounded`` / ``relevant`` (output guards). Names in a spec list that are
not in the registry are treated as pipeline built-ins and skipped here (so ``[passivate, llamaguard]`` runs
the built-in passivation and the user's registered LlamaGuard).

A guard that RAISES is conservative: treated as a failure (fail-closed for a safety net), never crashing the
pipeline.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    """A guard's verdict: ``ok`` (passed) plus a human-readable ``reason`` when it failed."""

    ok: bool
    reason: str = ""


_PARSERS: dict[str, Callable] = {}  #: name -> fn(text, ring, ctx) -> text
_GUARDS: dict[str, dict] = {}  #: name -> {"fn", "plane", "enforce"}

# Built-in names the PIPELINE computes (not the registry): the cascade's own passivation, and the three
# monitor portions. A spec.policy list may reference these for ordering/documentation; run_parsers/run_guards
# skip any name not in the user registry, so a built-in name is a no-op here and the pipeline handles it.
BUILTIN_PARSERS = ("passivate",)
BUILTIN_GUARDS = ("obeyed_only_system", "grounded", "relevant")


def is_builtin(name: str) -> bool:
    """True iff ``name`` is a pipeline-computed built-in (passivation / a monitor portion), not a user plugin."""
    return name in BUILTIN_PARSERS or name in BUILTIN_GUARDS


def register_parser(name: str, fn: Callable) -> None:
    """Register an INPUT parser ``fn(text, ring, ctx) -> text`` (sanitize / redact / transform). Runs in the IN chain.

    Registering a name that matches a pipeline built-in (e.g. ``passivate``) SHADOWS it: run_parsers then runs
    this callable for that name instead of the built-in being a no-op.
    """
    _PARSERS[name] = fn


def register_guard(name: str, fn: Callable, plane: str, enforce: bool = False) -> None:
    """Register a GUARD ``fn(ctx) -> GuardResult|bool``. ``plane`` in {input, output}; ``enforce`` -> fail-closed."""
    if plane not in ("input", "output"):
        raise ValueError(f"guard {name!r}: plane must be 'input' or 'output', got {plane!r}")
    _GUARDS[name] = {"fn": fn, "plane": plane, "enforce": bool(enforce)}


def registered_parsers() -> dict:
    """A copy of the parser registry (name -> fn)."""
    return dict(_PARSERS)


def registered_guards() -> dict:
    """A copy of the guard registry (name -> {fn, plane, enforce})."""
    return {k: dict(v) for k, v in _GUARDS.items()}


def clear() -> None:
    """Reset the registry (tests / re-initialisation)."""
    _PARSERS.clear()
    _GUARDS.clear()


# --- library-provided parsers (opt-in; not built-ins) -------------------------------------------------
# A parser is a pure INPUT transform on the SANITISATION layer -- it runs BEFORE the trust cascade so the
# cascade always sees plaintext. Encoding-transport attacks (a base64/obfuscated instruction) are opaque to
# the cascade's passivation (which filters VISIBLE instructions), so they belong HERE, not as a cascade attack
# class. This module ships ``base64_decode`` as the flagship example; a user opts in with
#   ``register_parser("base64_decode", policy.base64_decode)`` and lists it before ``passivate`` in policy.input.

_B64_MIN_CHARS = (
    16  #: shortest base64 run we touch (~12 decoded bytes) -- below this, leave legit tokens alone
)
_B64_PRINTABLE_MIN = (
    0.9  #: decode only if the bytes are >=90% printable text (else it is not a smuggled string)
)
_B64_SPAN = re.compile(
    rf"[A-Za-z0-9+/]{{{_B64_MIN_CHARS},}}={{0,2}}"
)  #: a base64-charset run, optional padding


def _b64_printable(raw: bytes) -> str | None:
    """Decode ``raw`` as ASCII and return it iff it is mostly-printable text, else None (not a text payload)."""
    try:
        decoded = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    printable = sum(c.isprintable() or c in " \t\n" for c in decoded)
    if decoded and printable / len(decoded) >= _B64_PRINTABLE_MIN:
        return decoded
    return None


def base64_decode(
    text: str, ring=None, ctx=None
) -> str:  # parser signature: fn(text, ring, ctx) -> text
    """A LIBRARY-PROVIDED input parser: decode base64-obfuscated spans INLINE so the cascade sees plaintext.

    Conservative by construction -- a span is replaced by its decoded text ONLY when it is on the base64
    charset, a whole number of quanta (length a multiple of 4), at least ``_B64_MIN_CHARS`` long, AND decodes
    to mostly-printable ASCII. Anything else (short tokens, non-base64 words, binary payloads) is left EXACTLY
    as-is, so legitimate content is never mangled. This is the encoding-transport countermeasure that lets the
    trust cascade stay a pure semantic/structural defence.
    """

    def _sub(m: re.Match) -> str:
        span = m.group(0)
        if len(span) % 4:
            return span
        try:
            raw = base64.b64decode(span, validate=True)
        except ValueError:
            return span
        return _b64_printable(raw) or span

    return _B64_SPAN.sub(_sub, text)


def run_parsers(text: str, ring, ctx, names) -> str:
    """Thread ``text`` through the named INPUT parsers in order.

    A name not in the registry is a pipeline built-in (e.g. ``passivate``) and is skipped here.
    """
    for name in names:
        fn = _PARSERS.get(name)
        if fn is not None:
            text = fn(text, ring, ctx)
    return text


def _verdict(fn, ctx) -> GuardResult:
    """Evaluate one guard, coercing bool->GuardResult and failing CLOSED on a raise (safety net)."""
    try:
        res = fn(ctx)
    except Exception as exc:
        return GuardResult(False, f"guard error: {type(exc).__name__}")
    if isinstance(res, GuardResult):
        return res
    return GuardResult(bool(res), "" if res else "guard returned false")


def run_guards(ctx, names, plane: str) -> tuple[bool, list, bool]:
    """Run the named guards for ``plane``; return (all_ok, [(name, GuardResult), ...] failures, enforced_fail).

    ``enforced_fail`` is True iff any ENFORCED guard failed -- the caller must then refuse (a hard, external
    constraint failure). A name not registered for this plane is skipped (built-in / other-plane).
    """
    fails: list = []
    enforced = False
    for name in names:
        g = _GUARDS.get(name)
        if g is None or g["plane"] != plane:
            continue
        res = _verdict(g["fn"], ctx)
        if not res.ok:
            fails.append((name, res))
            enforced = enforced or g["enforce"]
    return (not fails), fails, enforced
