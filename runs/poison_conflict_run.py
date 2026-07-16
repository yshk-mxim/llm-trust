# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Run the six conditions on the CONFLICT-poison set (tier-4 cross-ring: article in CONTENT, contradicting
claim in WEB). Same canonical constructors + live tuned vector as everything else (retune._poison_case +
retune.COND, reading data/prompts.json). Greedy-saved; a re-run resumes on (title, cond).

Run:  cd release && PYTHONPATH=src python runs/poison_conflict_run.py
In:   data/poison_conflict_cache.json
Out:  results/poison_conflict_dump.jsonl  (self-contained: carries op/claim/content/answer for grading)
"""
import json
import os
import sys

sys.path.insert(0, "src")
from cascading_lms import config, llm, retune  # noqa: E402

CONDS = ["base", "base+prompt", "wrapper", "passivation", "both", "conditioned"]
TIER = 4  # conflict is a cross-ring test: the false claim rides WEB (lowest ring), the source is CONTENT
DUMP = "results/poison_conflict_dump.jsonl"


def _done():
    if not os.path.exists(DUMP):
        return set()
    return {(json.loads(l)["title"], json.loads(l)["cond"]) for l in open(DUMP)}


def main():
    entries = json.load(open("data/poison_conflict_cache.json"))
    retune.POISON.update(entries)  # in-memory only; the committed data_poison.json is untouched
    done = _done()
    llm.refresh_model()
    llm.complete("hi", "hi")
    fh = open(DUMP, "a")
    n = 0
    for content, e in entries.items():
        case = retune._poison_case({"content": content}, TIER, "conflict")
        for cond in CONDS:
            if (e["title"], cond) in done:
                continue
            out = retune._safe_run(cond, case)
            fh.write(json.dumps({
                "title": e["title"], "n_chars": e.get("n_chars"), "tier": TIER, "cond": cond,
                "flavor": "conflict", "op": case["op"], "claim": case["claim"],
                "contradicts": e.get("contradicts", ""), "answer": e.get("answer", ""),
                # store the FULL generated output verbatim (max_tokens=170 is a TOKEN limit on generation;
                # slicing to max_tokens CHARACTERS truncated verbose base answers mid-sentence -> corrupt grade).
                "content": content, "out": out,
            }) + "\n")
            fh.flush()
            n += 1
        print(f"  {e['title'][:40]:42} done ({n} rows)")
    fh.close()
    print(f"wrote {DUMP}: {len(entries)} docs x {len(CONDS)} conds")


if __name__ == "__main__":
    main()
