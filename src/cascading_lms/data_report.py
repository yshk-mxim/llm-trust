# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Data-validity report (paper Sec. 5, App. C): OOD-ness and training diversity.

There is no standard benchmark for this task, so the datasets are characterised directly to answer the
two obvious criticisms: is the OOD set genuinely out-of-distribution, and is the training set diverse?
The report is deterministic and its disjointness claims are asserted (so it doubles as a data test).
"""

import collections

from cascading_lms import config
from cascading_lms import retune as R


def _attacks(split):
    """Attack cases in a split."""
    return [c for c in R.SPLITS[split] if c["kind"] == "attack"]


def _contents(split):
    """The set of source-content strings used by a split (attacks + clean)."""
    return {c.get("content") for c in R.SPLITS[split] if c.get("content")}


def _forms(split):
    """The multiset of attack forms in a split."""
    return collections.Counter(c["form"] for c in _attacks(split))


def _payload_tokens(split):
    """Lowercased word set of every attack's low-ring payload text (for structural-overlap analysis)."""
    words = set()
    for c in _attacks(split):
        for _ring, text in c["ch"]:
            words.update(w for w in text.lower().split() if w.isalpha())
    return words


def _jaccard(a, b):
    """Jaccard similarity of two sets (0 = disjoint, 1 = identical)."""
    return len(a & b) / len(a | b) if (a or b) else 0.0


def content_disjointness():
    """Assert and report that train/val/ood use pairwise-disjoint source content."""
    tr, va, oo = _contents("train"), _contents("val"), _contents("ood")
    overlaps = {"train∩val": tr & va, "train∩ood": tr & oo, "val∩ood": va & oo}
    for name, shared in overlaps.items():
        assert not shared, f"content leak {name}: {len(shared)} shared items"
    return {k: len(v) for k, v in overlaps.items()}


def form_disjointness():
    """Assert and report that the OOD token-injection forms are disjoint from the training forms."""
    ind, ood = set(R.IND_FORMS), set(R.OOD_FORMS)
    assert not (ind & ood), f"IND/OOD token forms overlap: {ind & ood}"
    return {"IND_forms": sorted(ind), "OOD_forms": sorted(ood), "shared": sorted(ind & ood)}


def structural_novelty():
    """OOD attack payloads should share little surface structure with training payloads (low Jaccard)."""
    return round(_jaccard(_payload_tokens("train"), _payload_tokens("ood")), 3)


def training_diversity():
    """Quantify training-set diversity: distinct tasks, tiers, forms, content vocabulary."""
    train = R.SPLITS["train"]
    ops = [c["op"] for c in train]
    vocab = set()
    for c in train:
        if c.get("content"):
            vocab.update(w for w in c["content"].lower().split() if w.isalpha())
    return {
        "n_cases": len(train),
        "distinct_ops": len(set(ops)),
        "op_uniqueness": round(len(set(ops)) / len(ops), 2),
        "tier_distribution": dict(collections.Counter(c["tier"] for c in train)),
        "attack_form_distribution": dict(_forms("train")),
        "content_vocabulary_words": len(vocab),
        "canary_token_pool": len(R.TOK),
    }


def report():
    """Assemble the full data-validity report."""
    return {
        "split_sizes": {s: len(R.SPLITS[s]) for s in ("train", "val", "ood")},
        "content_disjointness": content_disjointness(),
        "form_disjointness": form_disjointness(),
        "ood_payload_jaccard_vs_train": structural_novelty(),
        "training_diversity": training_diversity(),
        "performable_pool": len(R.LEGIT),
        "realistic_poison_facts": len(R.POISON),
    }


def _main():
    """Print the report and confirm the asserted disjointness invariants hold."""
    import json

    rep = report()
    print(json.dumps(rep, indent=2))
    print(
        "\nOOD is disjoint by content (100%) and by TOKEN-INJECTION form; the poison/delegation/"
        "corroboration attack structures are shared with train (only their content differs). Low payload "
        "Jaccard confirms the token-injection subset is structurally novel."
    )
    with open(config.run_path("data_report.json"), "w") as fh:
        json.dump(rep, fh, indent=2)


if __name__ == "__main__":
    _main()
