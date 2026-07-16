# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Clean + injection items from the v6e data pool.

load_v6e reads clean + injection rows (carrying base-authored oracles); build shuffles and draws pools.
Every caller uses only the clean pool (each builds its own attack cases), so the attack list is returned
for completeness but not consumed downstream.
"""

import json
import random
import re

from cascading_lms import config


def _inj_desc(r):
    """Human-readable description of a row's injection (first element when it is a list)."""
    v = r.get("injection")
    if isinstance(v, list) and v:
        return str(v[0])
    return str(v) if v else "an embedded instruction"


def _has_ctx(r):
    """True iff the row carries non-empty content text."""
    return bool((r.get("ctx") or "").strip())


_HEADING_MARKERS = ("title:", "heading:", "subject:", "abstract:")
_BLANK = re.compile(r"__+|\[[ _.]*]|\bfill in\b", re.IGNORECASE)  # fill-in-the-blank templates
_PLACEHOLDER = re.compile(
    r"\bperson [ab]\b", re.IGNORECASE
)  # abstract "Person A/Person B" stand-ins
_CODE = re.compile(r":=|==|[{};]|//|<=|>=|!=|&&|\|\||\+=|->")  # code/puzzle syntax prose never has


def has_body(content: str) -> bool:
    """True iff content is real summarizable PROSE with a coherent subject.

    Rejects the degenerate rows that clear the length band but have nothing to summarize -- so they can't
    tank Q for reasons unrelated to the defense: a bare title/heading, a fill-in-the-blank template, an
    abstract placeholder ("Person A blames Person B"), a single-clause note, or a code/dict/string puzzle
    (heavy on symbols/identifiers). Config-driven via ``content_bounds`` (min_body_chars/words, prose ratio).
    """
    text = content.strip()
    cb = config.CFG.content_bounds
    if len(text) < cb.min_body_chars or len(text.split()) < cb.min_body_words:
        return False
    if (
        text.lower().startswith(_HEADING_MARKERS)
        or _BLANK.search(text)
        or _PLACEHOLDER.search(text)
        or _CODE.search(text)
    ):
        return False
    prose = sum(c.isalpha() or c.isspace() for c in text) / len(
        text
    )  # letters+spaces vs symbols/digits
    return prose >= cb.min_prose_ratio


def _legit(i, r):
    """Clean-item dict for row ``i`` (op = SYSTEM ctrl, content = ctx, plus its oracle)."""
    return {
        "name": f"clean{i}",
        "op": r["ctrl"],
        "content": r["ctx"],
        "oracle": r.get("oracle"),
        "kind": "legit",
    }


def _attack(i, r):
    """Attack-item dict for row ``i`` (carries the injection-ignoring oracle and description)."""
    return {
        "name": f"inj{i}",
        "op": r["ctrl"],
        "content": r["ctx"],
        "oracle": r.get("oracle"),
        "injection": _inj_desc(r),
        "kind": "attack",
        "source": "v6e",
    }


def load_v6e(path=None):
    """Load pool rows into (legit, attack) dicts, carrying each row's injection-ignoring oracle.

    The pool path resolves at CALL time from ``config.V6E_DATASET`` (the spec's ``data`` section) unless a
    caller passes one -- so the data source is configuration, not baked in at import.
    """
    with open(path or config.V6E_DATASET) as fh:
        rows = [json.loads(line) for line in fh]
    legit = [_legit(i, r) for i, r in enumerate(rows) if r.get("regime") == "clean" and _has_ctx(r)]
    attacks = [
        _attack(i, r) for i, r in enumerate(rows) if r.get("regime") == "injection" and _has_ctx(r)
    ]
    return legit, attacks


def build(n_legit=40, n_attack=40, seed=0):
    """Shuffle both pools with ``seed``; return the first n_legit clean and n_attack v6e attack items."""
    legit, attacks = load_v6e()
    rng = random.Random(seed)
    rng.shuffle(legit)
    rng.shuffle(attacks)
    return legit[:n_legit], attacks[:n_attack]
