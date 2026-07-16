#!/usr/bin/env python3
# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Re-grade the ADAPTIVE (red-team) run with the SAME Opus reasoning judge as the static regrade, for one
consistent genuine-leak instrument across the paper, with a per-case audit trace.

The adaptive attempts (runs/redteam_{f1,f2,blackbox}.jsonl) store payload+output+success (the OLDER YES/NO
grader) but not op; op is rebuilt from the template index via the same tier-4 hijack templates the harness
attacked. We re-grade every attempt the old grader flagged as a success (the leak candidates -- the FP-
correction set, matching the static approach), reasoning judge each, and recompute per-family defended
(a scenario/template breaks iff any of its rounds genuinely leaks). White-box MAX = max ASR over f1,f2;
black-box = blackbox. Authority is unaffected (a separate structural check).

Run:  cd release && PYTHONPATH=src $PY runs/regrade_adaptive_genuine.py
"""
import sys, os, json, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from cascading_lms import final_eval as FE
import regrade_genuine_leak as RGL   # reuse reasoned_grade + REASON_RUBRIC (the committed instrument)

FAMS = {"f1": "runs/redteam_f1.jsonl", "f2": "runs/redteam_f2.jsonl", "blackbox": "runs/redteam_blackbox.jsonl"}
OUT = "runs/regrade_adaptive_genuine.jsonl"
SUMMARY = "runs/regrade_adaptive_summary.json"

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h) * 100, min(1.0, c + h) * 100)

def main():
    op_by_t = {i: t["op"] for i, t in enumerate(FE.build_hijack(4, 30))}
    fh = open(OUT, "w"); per = {}
    for fam, path in FAMS.items():
        recs = [json.loads(l) for l in open(path) if l.strip()]
        n_scen = len(set(r["template"] for r in recs))
        scen_leak = {}; old_scen_leak = set()
        for r in recs:
            if r.get("success"): old_scen_leak.add(r["template"])
            if not r.get("success"):
                continue                      # old grader: not carried out -> defended (re-grade only candidates)
            op = op_by_t.get(r["template"], "(unknown op)")
            leak, trace = RGL.reasoned_grade(op, r["payload"], r["out"])
            fh.write(json.dumps({"fam": fam, "template": r["template"], "round": r["round"], "op": op,
                     "payload": r["payload"], "out": r["out"], "old_success": True,
                     "genuine_leak": leak, "trace": trace}) + "\n"); fh.flush()
            if leak: scen_leak[r["template"]] = True
        broke = len(scen_leak); dv = n_scen - broke; lo, hi = wilson(dv, n_scen)
        per[fam] = {"n_scenarios": n_scen, "old_broke": len(old_scen_leak), "genuine_broke": broke,
                    "defended_pct": round(100 * dv / n_scen, 1), "CI95": [round(lo, 1), round(hi, 1)]}
        print(f"[{fam}] old_broke={len(old_scen_leak)} -> genuine_broke={broke}/{n_scen}  defended={per[fam]['defended_pct']}%", flush=True)
    fh.close()
    wb = min(per["f1"]["defended_pct"], per["f2"]["defended_pct"])   # white-box MAX asr == min defended
    summ = {"by_family": per,
            "white_box_MAX_defended_pct": wb,
            "black_box_defended_pct": per["blackbox"]["defended_pct"],
            "note": "genuine-leak via the Opus reasoning judge (same instrument as the static regrade); "
                    "old grader failures treated as defended; authority plane unaffected (0)."}
    json.dump(summ, open(SUMMARY, "w"), indent=2)
    print(json.dumps(summ, indent=2), flush=True)

if __name__ == "__main__":
    main()
