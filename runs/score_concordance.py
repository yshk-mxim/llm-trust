# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Score the FILLED genuine-leak concordance sheet against the grader key: % agreement + Cohen's kappa.

Offline, stdlib only -- NO model, NO API, NO GPU. It parses the human "YOUR VERDICT" fields from
concordance/regrade_concordance_sheet.md and compares them to grader_verdict in
concordance/regrade_concordance_key.json (both LEAK/DEFENDED, matched by case index).

Workflow:
  1. Open concordance/regrade_concordance_sheet.md and, for each `## case N`, replace `YOUR VERDICT: ____`
     with `YOUR VERDICT: LEAK` or `YOUR VERDICT: DEFENDED` -- BLIND (do not open the key while rating).
  2. cd release && python runs/score_concordance.py
It prints n rated, % agreement, Cohen's kappa, and every disagreement for inspection. This is the direct human
validation of the genuine-leak grader that produces the 27->94 headline (distinct from the kappa=0.93 that
validated the `obeyed` label).
"""
import json
import re

SHEET = "concordance/regrade_concordance_sheet.md"
KEY = "concordance/regrade_concordance_key.json"


def norm(v):
    v = (v or "").strip().upper()
    if v.startswith("LEAK") or v == "L":
        return "LEAK"
    if v.startswith("DEFEND") or v == "D":
        return "DEFENDED"
    return None  # unfilled / unrecognised


def parse_sheet(path):
    text = open(path).read()
    out = {}
    for block in re.split(r"\n##\s*case\s*", text)[1:]:
        idx = block.split("\n", 1)[0].strip().split()[0]
        m = re.search(r"YOUR VERDICT:\s*([^\n]+)", block)  # to end of line only
        if m:
            out[idx] = norm(m.group(1).strip().strip("*_ "))
    return out


def cohen_kappa(human, grader):
    """human, grader: parallel lists of 'LEAK'/'DEFENDED'."""
    n = len(human)
    if n == 0:
        return None, 0
    cats = ("LEAK", "DEFENDED")
    po = sum(h == g for h, g in zip(human, grader)) / n
    pe = sum((human.count(c) / n) * (grader.count(c) / n) for c in cats)
    kappa = (po - pe) / (1 - pe) if pe != 1 else 1.0
    return kappa, n


def main():
    human = parse_sheet(SHEET)
    key = json.load(open(KEY))
    pairs, unfilled, disagree = [], [], []
    for idx, meta in sorted(key.items(), key=lambda kv: int(kv[0])):
        g = meta["grader_verdict"].strip().upper()
        h = human.get(idx)
        if h is None:
            unfilled.append(idx)
            continue
        pairs.append((h, g))
        if h != g:
            disagree.append((idx, h, g, meta.get("source", ""), meta.get("trace", "")[:120]))
    if unfilled:
        print(f"[warn] {len(unfilled)} of {len(key)} cases not yet rated (unfilled/unrecognised): "
              f"{','.join(unfilled)}")
    if not pairs:
        print("No rated cases found. Fill in the YOUR VERDICT fields with LEAK/DEFENDED first.")
        return
    hs = [h for h, _ in pairs]
    gs = [g for _, g in pairs]
    agree = sum(h == g for h, g in pairs)
    kappa, n = cohen_kappa(hs, gs)
    print(f"human vs genuine-leak grader: {agree}/{n} = {100 * agree / n:.1f}% agreement, "
          f"Cohen's kappa = {kappa:.3f}")
    if disagree:
        print(f"\n{len(disagree)} disagreement(s) (case: human / grader | source | grader trace):")
        for idx, h, g, src, tr in disagree:
            print(f"  case {idx}: {h} / {g} | {src} | {tr}")


if __name__ == "__main__":
    main()
