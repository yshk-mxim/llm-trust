# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Generate a PROPER content-poison set: CONFLICT flavor, on obscure LONGER documents (n>>4).

The discriminating content-poison test is the CONFLICT flavor (a WEB claim that directly contradicts the
trusted source's answer to a specific question; metric j_asserted), on documents OBSCURE enough that the
answer needs the source (j_needs_content), not the model's priors. The committed poison cache is only 14
entries on median-79-char docs; this uses the obscure wiki_random articles (up to ~2.7k chars) instead.

Greedy-saved + resumable: each validated entry is written immediately, and a re-run skips done documents.

Run:  cd release && PYTHONPATH=src python runs/poison_conflict_gen.py [N_TARGET]
Out:  data/poison_conflict_cache.json  {content -> {question, answer, claim, contradicts, flavor, title, n_chars}}
"""
import json
import os
import sys

sys.path.insert(0, "src")
from cascading_lms import build_data  # noqa: E402

WIKI = "data/wiki_random_cache.json"
OUT = "data/poison_conflict_cache.json"
MIN_CHARS = 400  # skip stubs; keep the genuinely longer obscure articles


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    arts = sorted(json.load(open(WIKI)), key=lambda r: -len(r.get("text", "")))
    cache = _load(OUT)
    tried = 0
    for r in arts:
        if len([k for k in cache]) >= n_target:
            break
        content = r["title"] + ". " + r["text"]
        if len(content) < MIN_CHARS or content in cache:
            continue
        tried += 1
        try:
            entry = build_data.make_poison_entry(content, "conflict")
        except Exception as exc:  # noqa: BLE001 — an intermittent API response-shape hiccup must not kill the run
            print(f"  ERR   {len(content):>5}c {r['title'][:40]}: {type(exc).__name__}")
            continue
        if not entry:
            print(f"  none  {len(content):>5}c {r['title'][:40]}")
            continue
        entry.update(title=r["title"], n_chars=len(content))
        cache[content] = entry
        json.dump(cache, open(OUT, "w"), indent=1)  # greedy: survive a crash mid-generation
        print(f"  OK {len(cache):>2}  {len(content):>5}c {r['title'][:34]:36} Q={entry['question'][:44]}")
    print(f"\n{len(cache)} conflict-poison entries (tried {tried} new) -> {OUT}")


if __name__ == "__main__":
    main()
