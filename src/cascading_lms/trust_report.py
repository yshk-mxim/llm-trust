# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Inference-time TRUST REPORT: the individual per-output verdicts, derived from the trust model spec.

When the defended model produces an output, this reports -- per trust portion the specified lattice
guarantees -- whether the output honoured it, and names what failed. The verdict SET and the constraints
in force come from the trust model (:mod:`trust_spec`), never hardcoded to a fixed ring set: the
data-authority verdict scans the spec's ``is_data`` rings, the request-authority verdict its ``is_request``
rings (only when a request ring exists distinct from control), and the action constraint reads the spec's
``action_threshold``. So a different trust model yields a different report. The per-portion checks reuse
:func:`monitor.detect` / :func:`monitor._user_override_ok` (one source of truth, not forked).
"""

from __future__ import annotations

from dataclasses import dataclass

from cascading_lms import monitor, trust_spec
from cascading_lms.harness import Ring


@dataclass(frozen=True, slots=True)
class Verdict:
    """One trust portion's verdict on the output: whether it held, and the evidence it covers."""

    name: str
    held: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class TrustReport:
    """The per-output trust verdicts + the spec-derived constraints in force, for one inference."""

    verdicts: list[Verdict]
    constraints: dict

    @property
    def trusted(self) -> bool:
        """True iff every verdict held (the output honoured the whole specified lattice)."""
        return all(v.held for v in self.verdicts)

    @property
    def failed(self) -> list[str]:
        """Names of the portions that failed (empty = the output honoured every guaranteed portion)."""
        return [v.name for v in self.verdicts if not v.held]


def trust_report(
    op: str,
    ch: list[tuple[Ring, str]],
    passivated: list[tuple[Ring, str]],
    output: str,
    action: str = "",
    spec: trust_spec.TrustModel | None = None,
) -> TrustReport:
    """The inference-time trust verdicts for ``output``, driven by the spec's planes + constraints.

    ``grounded`` and ``relevant`` always apply (they concern the output vs the sources/task).
    ``obeyed_only_system`` is included only when the spec has untrusted DATA rings, and ``no_user_override``
    only when a REQUEST ring exists distinct from control -- so the verdict set adapts to the trust model.
    """
    spec = spec or trust_spec.DEFAULT
    portions = monitor.detect(op, ch, passivated, output)
    data_rings = [r.name for r in spec.data_rings]
    verdicts = [
        Verdict(
            "grounded",
            portions["grounded"],
            "output adds no fact absent from the passivated sources",
        ),
        Verdict(
            "relevant", portions["relevant"], "output addresses the request (not a refusal/blab)"
        ),
    ]
    if spec.data_rings:
        verdicts.insert(
            0,
            Verdict(
                "obeyed_only_system",
                portions["obeyed_only_system"],
                f"no command from data rings {data_rings} drove the output",
            ),
        )
    if spec.request_ring != spec.control_ring:
        verdicts.append(
            Verdict(
                "no_user_override",
                monitor._user_override_ok(op, ch, output),
                f"no task-preserving override from the request ring {spec.request_ring.name}",
            )
        )
    constraints = {
        "action_threshold": spec.action_threshold(action).name,
        "trust_order": spec.trust_order_str(),
    }
    return TrustReport(verdicts, constraints)
