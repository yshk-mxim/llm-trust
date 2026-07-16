# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Passifier / Generator: the analog layer (ADR-002), authority-free and ring-agnostic.

It classifies no provenance (the harness does that structurally, by channel); it only applies
bounded transforms: passivate() re-expresses text as inert reported content and perform() runs an
operation on content treated purely as data. Salience between rings is the cascade STRUCTURE (the
number of passivation passes), not the model reading trust tags. A jailbroken generator is a quality
failure, never a breach.
"""

import re

from cascading_lms import config, llm
from cascading_lms.harness import Labeled, derive

_SENT = re.compile(r"(?<=[.!?])\s+")

# Default element skills are DATA (translatable / rebrandable), loaded from the prompt pack; set_prompts
# installs tuned artifacts at runtime, and PROMPT_PACK selects an alternate (e.g. non-English) pack.
_PASSIVATE_SYS = config.PASSIVATE_PROMPT_DEFAULT
_PERFORM_SYS = config.PERFORM_PROMPT_DEFAULT


def set_prompts(passivate: str | None = None, perform: str | None = None) -> None:
    """Install tuned element skills (best_skill artifacts); any falsy value leaves a skill unchanged."""
    global _PASSIVATE_SYS, _PERFORM_SYS
    if passivate:
        _PASSIVATE_SYS = passivate
    if perform:
        _PERFORM_SYS = perform


def split_sentences(item: Labeled):
    """Split into per-sentence Labeled items, each inheriting the parent ring via derive."""
    stripped = item.payload.strip()
    sents = [s.strip() for s in _SENT.split(stripped) if s.strip()] or [stripped]
    return [derive(s, [item]) for s in sents]


def passivate(
    item: Labeled, passes: int = 1, max_tokens: int = config.CFG.generation.llm_default_max_tokens
) -> Labeled:
    """Re-express text as inert reported content over ``passes`` cascade depth; ring == parent."""
    text = item.payload
    for _ in range(max(1, passes)):
        text = llm.complete(_PASSIVATE_SYS, text, max_tokens=max_tokens)
    return derive(text, [item])


def perform(
    bound_op, items, max_tokens: int = config.CFG.generation.llm_default_max_tokens
) -> Labeled:
    """Perform the bound operation on already-passivated content; output ring == meet(inputs)."""
    content = "\n".join(it.payload for it in items)
    out = llm.complete(
        _PERFORM_SYS, f"OPERATION: {bound_op.op}\n\nCONTENT:\n{content}", max_tokens=max_tokens
    )
    return derive(out, list(items))
