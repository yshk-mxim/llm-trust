# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Empirical judge validation on COMBINED multi-ring information (known-answer concordance).

Each case is a (judge, args, expected) triple where the CORRECT verdict is unambiguous, focused on the
combined-info situations the cascade actually produces: an output that mixes a CONTENT fact with an
attributed WEB fact, a connectivity chain, poison inside combined content, a restated combined request.
The cases are DATA (``data/judge_cases_combined.toml``) so an org can supply its own; this module runs
each through the live judge and prints agreement, confirming the judges handle combined information
before the tune trusts them.
"""

import tomllib
from collections import defaultdict

from cascading_lms import config
from cascading_lms import judges as HT


def load_cases() -> list[dict]:
    """The known-answer cases ({judge, args, expected}) from data/."""
    with open(config.data_path("judge_cases_combined.toml"), "rb") as fh:
        return tomllib.load(fh)["case"]


def agreement(cases: list[dict]) -> dict[str, list[bool]]:
    """Run each case through its live judge; return {judge: [agreed, ...]}, printing any mismatch."""
    by_judge: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        name, args, expected = case["judge"], case["args"], case["expected"]
        got = bool(HT.judge(name)(*args))
        if got != expected:
            print(f"  MISMATCH {name}({args[0]!r}...) expected={expected} got={got}", flush=True)
        by_judge[name].append(got == expected)
    return by_judge


def main() -> None:
    """Print per-judge and overall known-answer agreement."""
    by_judge = agreement(load_cases())
    print("\n=== combined-info judge agreement (known-answer) ===", flush=True)
    for name, res in sorted(by_judge.items()):
        print(f"  {name:14} {sum(res)}/{len(res)}", flush=True)
    agreed = [ok for res in by_judge.values() for ok in res]
    print(f"  OVERALL {sum(agreed)}/{len(agreed)}", flush=True)


if __name__ == "__main__":
    main()
