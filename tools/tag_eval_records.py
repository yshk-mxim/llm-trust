# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Backfill ``op`` + ``content_preview`` into final_eval raw records by mapping ``k`` -> case. NO model calls.

Older final_eval records carried only ``k`` (``tier|cond|prefix+idx``) + the output + verdicts, not the case's
question -- so you could not tell WHICH case a record belonged to by reading it. This maps each ``k`` back to
its case via the SAME deterministic batch builders (``final_eval._tier_batches`` / ``load_action_injections``,
fixed seed -> identical cases) and writes a ``*_tagged.jsonl`` with ``op`` + ``content_preview`` added, so every
record self-identifies its case ("which is which"). Pure lookup -- zero 26B/Opus calls. The original is left
intact. (Records already self-contained -- carrying ``op`` -- are copied through untouched.)
"""

import json
import re
import sys

from cascading_lms import config
from cascading_lms import final_eval as F

_K = re.compile(r"^(\d+)\|[^|]+\|([a-z]+)(\d+)$")  # tier | cond | prefix+idx  (e.g. "2|base|hjk4")


def build_case_index(tiers=(2, 3, 4)):
    """``(tier, prefix, idx) -> case`` and ``('action', id) -> row``, from the deterministic builders (data only).

    The eval builds each tier's batches once and pairs them across conditions, so a case is fixed by
    ``(tier, prefix, idx)`` regardless of the condition -- exactly what ``k`` encodes.
    """
    idx: dict = {}
    for tier in tiers:
        for cases, prefix, _cls, _build in F._tier_batches(tier):
            for i, case in enumerate(cases):
                idx[(tier, prefix, i)] = case
    for row in F.load_action_injections():
        idx[("action", str(row["id"]))] = row
    return idx


def tag_for(k, idx):
    """The provenance tag (``op`` + ``content_preview``) for a record key, or ``None`` if ``k`` does not map."""
    if k.startswith("action|"):
        case = idx.get(("action", k.split("|", 1)[1]))
    else:
        m = _K.match(k)
        case = idx.get((int(m.group(1)), m.group(2), int(m.group(3)))) if m else None
    return F._case_tag(case) if case else None


def tag_file(path, tiers=(2, 3, 4)):
    """Write ``<path>_tagged.jsonl`` with ``op`` + ``content_preview`` backfilled; return ``(out, tagged, total)``."""
    out = path.rsplit(".jsonl", 1)[0] + "_tagged.jsonl"
    idx = build_case_index(tiers)
    total = tagged = unmapped = 0
    with open(path) as fi, open(out, "w") as fo:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            total += 1
            if "op" not in rec:  # already-self-contained records keep their own op/content
                tag = tag_for(rec.get("k", ""), idx)
                if tag:
                    rec.update(tag)
                    tagged += 1
                else:
                    unmapped += (
                        1  # a k that doesn't map (e.g. a schema change) -- pass through, note it
                    )
            fo.write(json.dumps(rec) + "\n")
    print(
        f"[tag] backfilled {tagged}, already-tagged {total - tagged - unmapped}, unmapped {unmapped}, "
        f"total {total} -> {out}",
        flush=True,
    )
    return out, tagged, total


if __name__ == "__main__":
    tag_file(sys.argv[1] if len(sys.argv) > 1 else config.run_path("final_eval.jsonl"))
