# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Aggregate runs/attribution_remeasure.jsonl into honest (non-compound) attribution statistics.

For each condition (base, conditioned) and each case group (the 22 newly curated web_relevant=True
cases; optionally the 3 pre-existing relevant cases from the original contaminated n=6 slice), reports:
  - pure attribution rate  = attributed / surfaced        (the labeling capability itself)
  - defended (compound)    = task AND grounded AND attributed   (matches the paper's prior compound gate,
                              recomputed honestly on an adequate, all-relevant sample)
each with n and a 95% Wilson score interval (no third-party stats dependency, matches the project's
stdlib-only policy -- see release/requirements.txt).

Reads-only; writes nothing. Run after runs/attribution_remeasure.py has produced records.
"""

import json
import math
import os

PATH = os.path.join(os.path.dirname(__file__), "attribution_remeasure.jsonl")
Z = 1.959963985  # 95% two-sided normal quantile


def wilson(k, n):
    """Wilson score 95% CI for a binomial rate k/n. Returns (phat, lo, hi); (nan, nan, nan) if n==0."""
    if n == 0:
        return (float("nan"),) * 3
    phat = k / n
    denom = 1 + Z**2 / n
    center = phat + Z**2 / (2 * n)
    margin = Z * math.sqrt(phat * (1 - phat) / n + Z**2 / (4 * n**2))
    return phat, (center - margin) / denom, (center + margin) / denom


def load(path=PATH):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def group_key(rec):
    k = rec["k"]
    return "old_relevant_3" if k.startswith("old_wa|") else "new_22"


def summarize(records, cname, group):
    rows = [r for r in records if r.get("cond") == cname and group_key(r) == group and not r.get("error")]
    n = len(rows)
    surfaced_rows = [r for r in rows if r.get("web_surfaced")]
    n_surf = len(surfaced_rows)
    k_attr_given_surf = sum(1 for r in surfaced_rows if r.get("web_attributed"))
    k_defended = sum(
        1 for r in rows if r.get("task") and r.get("grounded") and r.get("web_attributed")
    )
    k_defended_field = sum(1 for r in rows if r.get("defended"))
    pure_p, pure_lo, pure_hi = wilson(k_attr_given_surf, n_surf)
    def_p, def_lo, def_hi = wilson(k_defended, n)
    return {
        "group": group,
        "cond": cname,
        "n": n,
        "n_surfaced": n_surf,
        "pure_attribution": {"k": k_attr_given_surf, "n": n_surf, "rate": pure_p, "ci95": (pure_lo, pure_hi)},
        "defended_compound": {"k": k_defended, "n": n, "rate": def_p, "ci95": (def_lo, def_hi)},
        "defended_field_agrees": k_defended == k_defended_field,
    }


def main():
    records = load()
    n_errors = sum(1 for r in records if r.get("error"))
    if n_errors:
        print(f"WARNING: {n_errors} records errored (excluded from stats):")
        for r in records:
            if r.get("error"):
                print(" ", r["k"], r["error"], r.get("error_msg"))

    groups = sorted({group_key(r) for r in records})
    for group in groups:
        for cname in ("base", "conditioned"):
            s = summarize(records, cname, group)
            pa, dc = s["pure_attribution"], s["defended_compound"]
            print(
                f"[{group:16s}] {cname:12s} "
                f"pure_attr={pa['k']}/{pa['n']}={pa['rate']:.1%} "
                f"CI95=({pa['ci95'][0]:.1%},{pa['ci95'][1]:.1%})   "
                f"defended={dc['k']}/{dc['n']}={dc['rate']:.1%} "
                f"CI95=({dc['ci95'][0]:.1%},{dc['ci95'][1]:.1%})  "
                f"(defended-field agrees: {s['defended_field_agrees']})"
            )

    out = {
        "n_errors": n_errors,
        "summaries": [
            summarize(records, cname, group) for group in groups for cname in ("base", "conditioned")
        ],
    }
    with open(os.path.join(os.path.dirname(__file__), "attribution_summary.json"), "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print("\nwrote runs/attribution_summary.json")


if __name__ == "__main__":
    main()
