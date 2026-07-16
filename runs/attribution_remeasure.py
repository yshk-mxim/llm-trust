# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Re-measure web-fact attribution on the REAL deployed pipeline, honest (non-compound) metric.

Uses the ~22 NEWLY curated web_relevant=True cases appended to the end of
data/data_web_additional.json (indices [21:43], all disjoint from the frozen wa_train/wa_val
slices used to tune prompts.json -- see release/data/data_web_additional.json). For each new case,
builds the case with retune._web_additional_case, generates BOTH the base and conditioned outputs
with the REAL cascade conditions (retune.COND), and scores each with the REAL scorer
final_eval._wa_record (which calls the real Opus judges via judges.HT and the real grounding check
via stage_check.SC -- no judge/metric is reimplemented here).

Greedy-save: appends one JSON record per (case, condition) to runs/attribution_remeasure.jsonl and
flushes immediately, so a hang mid-run loses nothing already scored. Resumable: skips any (k) key
already present in the output file.

Does NOT touch data/prompts.json (frozen deployed vector, read-only) and does NOT start/stop any
server -- assumes the 26B is already served at :9000 and the Opus judge key is already configured
(release/src/cascading_lms/llm.py + config.JUDGE_ENV_JSON), exactly as the rest of the harness does.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cascading_lms import config  # noqa: E402
from cascading_lms import final_eval as FE  # noqa: E402
from cascading_lms import retune as R  # noqa: E402

OUT_PATH = os.path.join(os.path.dirname(__file__), "attribution_remeasure.jsonl")
CONDS = ("base", "conditioned")
TIER = 4


def _new_cases():
    """The NEWLY curated cases: everything past the original 21-entry file (train+val+old-held = 21)."""
    n_original = 21
    return R.WEB_ADDITIONAL[n_original:]


def _done_keys(path):
    done = set()
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                done.add(json.loads(line)["k"])
    return done


def main():
    new_entries = _new_cases()
    print(f"{len(new_entries)} newly curated web_additional entries", flush=True)
    assert all(e.get("web_relevant") for e in new_entries), "all new curated cases must be web_relevant=True"

    done = _done_keys(OUT_PATH)
    print(f"{len(done)} records already done (resuming)", flush=True)

    with open(OUT_PATH, "a") as fh:
        for idx, e in enumerate(new_entries):
            case = R._web_additional_case(e)
            for cname in CONDS:
                k = f"new_wa|{idx}|{cname}"
                if k in done:
                    continue
                cf = R.COND[cname]
                try:
                    out = cf(case["op"], case["ch"])
                    rec = FE._wa_record(k, TIER, cname, case, out)
                except Exception as exc:  # greedy-save must survive a single bad case
                    rec = {
                        "k": k,
                        "tier": TIER,
                        "cond": cname,
                        "cls": "web_additional",
                        "error": type(exc).__name__,
                        "error_msg": str(exc),
                    }
                rec["web_relevant"] = e.get("web_relevant", False)
                rec["subject_idx"] = idx
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                print(f"{k}: task={rec.get('task')} grounded={rec.get('grounded')} "
                      f"surfaced={rec.get('web_surfaced')} attributed={rec.get('web_attributed')} "
                      f"defended={rec.get('defended')} err={rec.get('error')}", flush=True)

    print("done", flush=True)


if __name__ == "__main__":
    main()
