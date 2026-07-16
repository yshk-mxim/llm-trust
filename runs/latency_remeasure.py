# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Re-measure tab:config latency (base / base+tuned-prompt / cascade-conditioned) on the served 26B.

tab:config reports the TIER-2 wall-clock mean per condition (base 1 call, base+prompt 1 call, cascade 2 calls
at tier 2). This times each condition on the SAME K clean tier-2 cases at the deployed generation config
(max_tokens=170, temperature=0.7). Run it ALONE (no other 26B load) or the numbers are contended and useless.

Run:  cd release && PYTHONPATH=src python runs/latency_remeasure.py [K]
Out:  runs/latency_remeasure.json  (per-condition mean/median/n + per-case timings)
"""
import json
import statistics
import sys
import time

sys.path.insert(0, "src")
from cascading_lms import config, llm, retune  # noqa: E402

CONDS = [("base", "base"), ("base+prompt", "base + tuned prompt"), ("conditioned", "cascade (conditioned)")]
TIER = 2


def _clean_tier2(n):
    """The first ``n`` LEGIT docs as clean tier-2 cases (channel skeleton, no injection)."""
    cases = []
    for e in retune.LEGIT[:n]:
        ch, _ = retune._tierch(TIER, e)
        cases.append({"op": e["op"], "ch": ch})
    return cases


def main():
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    llm.refresh_model()
    llm.complete("hi", "hi")  # warm the server so the first timed call is not cold-start
    cases = _clean_tier2(k)
    out = {"tier": TIER, "k": k, "max_tokens": config.CFG.generation.max_tokens,
           "temperature": config.CFG.generation.temperature, "conditions": {}}
    for cname, label in CONDS:
        times = []
        for c in cases:
            t0 = time.perf_counter()
            retune.COND[cname](c["op"], c["ch"])
            times.append(time.perf_counter() - t0)
        out["conditions"][cname] = {
            "label": label, "n": len(times),
            "mean_s": round(statistics.mean(times), 3),
            "median_s": round(statistics.median(times), 3),
            "stdev_s": round(statistics.pstdev(times), 3),
            "times_s": [round(t, 3) for t in times],
        }
        print(f"{label:<28} mean {out['conditions'][cname]['mean_s']:.3f}s  "
              f"median {out['conditions'][cname]['median_s']:.3f}s  (n={len(times)})")
    json.dump(out, open("runs/latency_remeasure.json", "w"), indent=1)
    print("wrote runs/latency_remeasure.json")


if __name__ == "__main__":
    main()
