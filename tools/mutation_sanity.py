# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Fast, deterministic mutation gate against vacuous tests.

Inject a representative semantic mutant into each pure-logic core, run the tests that must catch it, and
confirm they FAIL (mutant KILLED) -- then revert. A SURVIVING mutant means a test passed against broken
code, i.e. the test is vacuous. This is the fast gate (`make mutation`); `mutmut` (requirements-dev,
`[tool.mutmut]`) does the exhaustive sweep (`make mutation-full`) for a deep pass.

Run from the release/ root: ``python3 tools/mutation_sanity.py``.
"""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path("src/cascading_lms")
# (file, anchor found in source, mutated replacement, test selector that must KILL it, label)
MUTANTS = [
    (
        "harness.py",
        "Ring(min(int(r) for r in rings))",
        "Ring(max(int(r) for r in rings))",
        "tests/test_properties.py -k meet",
        "harness.meet: min->max (breaks the GLB)",
    ),
    (
        "harness.py",
        "if rings else Ring.UNTRUSTED",
        "if rings else Ring.SYSTEM",
        "tests/test_properties.py -k meet",
        "harness.meet: empty meet not bottom",
    ),
    (
        "pareto.py",
        'p["Q"] = round((p["Q"] * (n - 1) + q) / n, 3)',
        'p["Q"] = round((p["Q"] * (n - 1) + q) / (n - 1), 3)',
        "tests/test_properties.py -k collision",
        "ParetoArchive: wrong running-mean divisor",
    ),
    (
        "pareto.py",
        'eligible = [p for p in self.points if p["Q"] >= q_floor] or self.points',
        'eligible = [p for p in self.points if p["Q"] <= q_floor] or self.points',
        "tests/test_properties.py -k select_is_max",
        "ParetoArchive.select: floor comparison flipped",
    ),
    (
        "pareto.py",
        "return not_worse and clearly_better",
        "return not_worse or clearly_better",
        "tests/test_properties.py -k dominance",
        "pareto._scalar_dominates: and->or (breaks antisymmetry)",
    ),
    (
        "trust_spec.py",
        "Ring[r.name].value == r.integrity",
        "Ring[r.name].value != r.integrity",
        "tests/test_properties.py -k validate",
        "trust_spec.validate: integrity==enum check inverted",
    ),
    (
        "policy.py",
        'return GuardResult(False, f"guard error: {type(exc).__name__}")',
        'return GuardResult(True, f"guard error: {type(exc).__name__}")',
        "tests/test_policy.py",
        "policy._verdict: raising guard no longer fails closed",
    ),
]


def _tests_pass(selector: str) -> bool:
    """True iff the selected tests PASS (against the mutated code -> the mutant SURVIVED = vacuous)."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *selector.split(), "-q", "--no-header"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    return r.returncode == 0


def main() -> int:
    """Run every mutant; return 1 if any survived (a vacuous test), else 0."""
    killed = survived = 0
    for fname, anchor, mutated, selector, label in MUTANTS:
        path = SRC / fname
        original = path.read_text()
        if anchor not in original:
            print(f"  ANCHOR DRIFT ({fname}): {anchor!r} -- update the mutant", flush=True)
            survived += 1
            continue
        try:
            path.write_text(original.replace(anchor, mutated, 1))
            passed = _tests_pass(selector)
        finally:
            path.write_text(original)  # always revert
        if passed:
            survived += 1
            print(f"  SURVIVED (VACUOUS TEST!): {label}", flush=True)
        else:
            killed += 1
            print(f"  killed: {label}", flush=True)
    print(f"\n=== mutation sanity: {killed}/{killed + survived} mutants killed ===", flush=True)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
