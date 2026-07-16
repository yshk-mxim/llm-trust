# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Scale the conflict-poison set: fetch MORE and LONGER obscure Wikipedia articles (full extracts, not just
intros) and generate conflict-poison entries on them, appending to data/poison_conflict_cache.json.

Obscure random articles isolate the DEFENSE (CONTENT-vs-WEB reliance) from the model's world knowledge; full
extracts (exchars, not exintro) give realistic length. Greedy-saved + resilient: fetched articles cache to
data/poison_conflict_wiki_long.json; each validated poison entry is written immediately; a re-run resumes.

Run:  cd release && PYTHONPATH=src python runs/poison_conflict_fetch_gen.py [N_NEW] [MIN_LEN]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "src")
from cascading_lms import build_data  # noqa: E402

API = "https://en.wikipedia.org/w/api.php"
UA = "cqwen-research/1.0 (academic prompt-injection eval)"
CACHE = "data/poison_conflict_cache.json"           # merges with the existing conflict entries
ART = "data/poison_conflict_wiki_long.json"          # fetched long obscure articles (raw)
BATCH = 20


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else ({} if path == CACHE else [])


def _fetch_batch(k):
    q = urllib.parse.urlencode({
        "action": "query", "generator": "random", "grnnamespace": "0", "grnlimit": str(k),
        "prop": "extracts|revisions", "explaintext": "1", "exchars": "3000", "exsectionformat": "plain",
        "rvprop": "ids", "format": "json",
    })
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    out = []
    for page in data.get("query", {}).get("pages", {}).values():
        text = (page.get("extract", "") or "").strip()
        out.append({"title": page.get("title", ""), "n_chars": len(text), "text": text})
    return out


def main():
    n_new = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    min_len = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    cache = _load(CACHE)
    arts = _load(ART)
    seen_titles = {a["title"] for a in arts} | {c.split(".")[0] for c in cache}
    start = len(cache)
    batches = 0
    while len([c for c in cache]) - start < n_new and batches < 60:
        batches += 1
        try:
            got = _fetch_batch(BATCH)
        except Exception as exc:  # noqa: BLE001
            print(f"  fetch err: {type(exc).__name__}; retrying")
            time.sleep(1.0)
            continue
        for a in got:
            if not a["title"] or a["title"] in seen_titles:
                continue
            seen_titles.add(a["title"])
            arts.append(a)
            json.dump(arts, open(ART, "w"), indent=1)
            if a["n_chars"] < min_len:
                continue
            content = a["title"] + ". " + a["text"]
            if content in cache:
                continue
            try:
                entry = build_data.make_poison_entry(content, "conflict")
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR  {a['n_chars']:>5}c {a['title'][:38]}: {type(exc).__name__}")
                continue
            if not entry:
                continue
            entry.update(title=a["title"], n_chars=len(content))
            cache[content] = entry
            json.dump(cache, open(CACHE, "w"), indent=1)
            print(f"  OK {len(cache):>3} ({len(cache)-start} new) {a['n_chars']:>5}c {a['title'][:34]:36} Q={entry['question'][:40]}")
        time.sleep(0.4)  # be polite to the API
    print(f"\n{len(cache)} total conflict-poison entries ({len(cache)-start} new); {len(arts)} articles fetched")


if __name__ == "__main__":
    main()
