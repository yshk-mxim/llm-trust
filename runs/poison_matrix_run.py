# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Generate the CONTENT-POISON half of tab:tiers (the committed dump holds token forms only).

3 tiers x 5 ablation conditions x the 14 poison documents (all POISON entries carrying a retrieval question).
The SAME 14 cases are run under every condition (component-isolation: one variable changed). Each poison case
places the fabricated claim in the tier's lowest-integrity ring (via retune._poison_case). Raw outputs are
greedy-saved (append+flush); grading is a SEPARATE step (poison_matrix_grade.py) so a judge/API failure never
loses the expensive 26B outputs. Deployed generation config (max_tokens=170, temperature=0.7).

Run:  cd release && PYTHONPATH=src python runs/poison_matrix_run.py
Out:  results/poison_matrix_dump.jsonl  (one row per tier x cond x doc)
"""
import json
import sys

sys.path.insert(0, "src")
from cascading_lms import config, llm, retune  # noqa: E402

CONDS = ["base", "base+prompt", "wrapper", "passivation", "both"]
TIERS = (2, 3, 4)
OUT = "results/poison_matrix_dump.jsonl"


def _poison_pool():
    """The poison documents usable as cases (carry a cached retrieval question), in a fixed order."""
    return [e for e in retune.LEGIT if retune.POISON.get(e["content"], {}).get("question")]


def main():
    pool = _poison_pool()
    llm.refresh_model()
    llm.complete("hi", "hi")
    n_done = 0
    with open(OUT, "w") as fh:
        for tier in TIERS:
            for i, pe in enumerate(pool):
                flavor = retune._poison_flavor(retune.POISON[pe["content"]], i)
                case = retune._poison_case(pe, tier, flavor)
                for cond in CONDS:
                    out = retune._safe_run(cond, case)
                    fh.write(json.dumps({
                        "tier": tier, "cond": cond, "doc": i, "flavor": flavor,
                        "difficulty": retune.POISON[pe["content"]].get("difficulty"),
                        "claim": case["claim"], "op": case["op"],
                        # store the FULL output (max_tokens=170 is a TOKEN limit; slicing to 170 CHARS truncated
                        # verbose answers before grading -> corrupt). NOTE: this OFF-SUBJECT matrix is SUPERSEDED
                        # by the conflict-flavor poison_conflict_* pipeline (the discriminating content-poison test).
                        "out": out,
                    }) + "\n")
                    fh.flush()
                    n_done += 1
            print(f"tier {tier}: {len(pool)} docs x {len(CONDS)} conds done ({n_done} total)")
    print(f"wrote {OUT} ({n_done} rows = {len(TIERS)} tiers x {len(CONDS)} conds x {len(pool)} docs)")


if __name__ == "__main__":
    main()
