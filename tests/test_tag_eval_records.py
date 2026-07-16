# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""The final_eval record backfill maps ``k`` -> case deterministically (data-only, no model calls), so a raw
record can be made self-contained (carry its question) after the fact."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import tag_eval_records as T


def test_k_maps_to_its_case_op(tmp_path):
    idx = T.build_case_index(tiers=(4,))
    # a token key at tier 4 resolves to a real case with a non-empty question
    tag = T.tag_for("4|base|tok0", idx)
    assert tag and tag["op"], "a token key must map to a case with a non-empty op"
    # backfilling a bare record adds exactly that case's op (traceability, no model call)
    case = idx[(4, "tok", 0)]
    f = tmp_path / "r.jsonl"
    f.write_text(json.dumps({"k": "4|base|tok0", "cls": "token", "out": "x"}) + "\n")
    out, tagged, total = T.tag_file(str(f), tiers=(4,))
    with open(out) as fh:
        rec = json.loads(fh.read().strip())
    assert rec["op"] == case["op"] and tagged == 1 and total == 1


def test_already_tagged_record_is_left_alone(tmp_path):
    # a record that already carries op (poison/clean/web_additional) is passed through unchanged
    f = tmp_path / "r.jsonl"
    f.write_text(json.dumps({"k": "4|base|cln0", "cls": "clean", "op": "MINE", "out": "y"}) + "\n")
    out, tagged, _ = T.tag_file(str(f), tiers=(4,))
    with open(out) as fh:
        rec = json.loads(fh.read().strip())
    assert rec["op"] == "MINE" and tagged == 0
