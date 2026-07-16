# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Data-validity invariants: splits are disjoint by content, OOD is held out by form, data is sufficient."""

import json
import tempfile

import pytest

from cascading_lms import data_report
from cascading_lms import retune as R


def test_splits_have_expected_sizes():
    """Each split is non-empty with both attack and clean cases."""
    for split in ("train", "val", "ood"):
        cases = R.SPLITS[split]
        kinds = {c["kind"] for c in cases}
        assert cases and {"attack", "clean"} <= kinds


def test_content_is_pairwise_disjoint_across_splits():
    """No source content is shared between train, val, and ood (no leakage)."""
    overlaps = data_report.content_disjointness()
    assert all(v == 0 for v in overlaps.values()), overlaps


def test_ood_forms_are_disjoint_from_training_forms():
    """The OOD token-injection forms are structurally novel (disjoint from the training forms)."""
    fd = data_report.form_disjointness()
    assert fd["shared"] == []


def test_ood_payloads_are_structurally_novel():
    """OOD attack payloads share little surface structure with training payloads (low Jaccard)."""
    assert data_report.structural_novelty() < 0.5


def test_training_is_diverse():
    """The training set spans many distinct tasks and all tiers (not a narrow, memorizable set)."""
    div = data_report.training_diversity()
    assert div["distinct_ops"] >= 50
    assert len(div["tier_distribution"]) == 3


def test_agentdojo_attacks_are_wellformed():
    """AgentDojo attacks use the canonical templates and inject a canary demand into the low ring."""
    from cascading_lms import agentdojo as AD

    items = [{"content": "The bridge was repaired.", "op": "Summarize."}]
    cases = AD.build(4, len(AD.TEMPLATES), items, seed=0)
    assert {c["form"] for c in cases} == {f"agentdojo:{n}" for n in AD.TEMPLATES}
    for c in cases:
        low_text = c["ch"][-1][1]
        assert c["tok"] in low_text  # the canary demand reaches the injected (low) ring
        assert c["source"].startswith("AgentDojo")


def test_poison_cases_use_the_retrieval_question_construction():
    """Every content-poison case is a RETRIEVAL-QUESTION attack (not summarize): its op is the cached
    document-specific question, it carries a real cached claim + a flavor, and the metric follows the flavor.

    Content-poison is one CATEGORY of the comprehensive injection taxonomy (never deleted). The retrieval
    question -- NOT "summarize" -- is what makes an injected fact a violation: a summary legitimately includes
    any non-conflicting fact, so only against a specific question does an off-subject / conflicting insertion
    become a leak. off-subject -> j_surfaced (must be dropped); conflict -> j_asserted (must not override).
    """
    poison = [c for c in R.SPLITS["train"] if c.get("cls") == "poison"]
    assert poison
    for c in poison:
        entry = R.POISON[c["content"]]
        assert (
            entry.get("question") and c["op"] == entry["question"]
        )  # question is the op, not summarize
        assert c["op"] != "Summarize the key facts of the following."  # the retired summarize op
        assert c.get("flavor") in ("offsubject", "conflict")
        assert c.get("claim")  # off-subject smuggle or the conflicting web claim, per flavor
        # DIFFICULTY MIX: easy (off-domain) / hard (domain-adjacent) off-subject, or conflict -- and the
        # difficulty is consistent with the flavor (conflict difficulty <=> conflict flavor).
        assert c.get("difficulty") in ("easy", "hard", "conflict")
        assert (c["difficulty"] == "conflict") == (c["flavor"] == "conflict")


def test_injection_taxonomy_all_categories_present():
    """The default mix samples the FULL taxonomy: token-emit, task-hijack, USER task-override, delegation,
    corroboration, USER token-override, content-poison, and the RESTORED real-world named payloads (persona /
    authority_impersonation / social_approval / fake_completion / reveal / delimiter_escape) -- nothing
    narrowed to one class, nothing thrown out. (Encoding-transport obfuscation is parser-layer, not sampled.)"""
    cls = {c.get("cls") for c in R.SPLITS["train"] if c.get("kind") == "attack"}
    assert (
        {
            "tok",
            "hijack",
            "user_override",
            "poison",
            *R._INSTRUCTION_ATTACKS,  # persona, authority_impersonation, social_approval, fake_completion, ...
        }
        <= cls
    )  # every declared attack category is actually sampled
    assert "obfuscation" not in cls  # encoding-transport is out of the cascade taxonomy by design
    forms = {c.get("form") for c in R.SPLITS["train"] if c.get("kind") == "attack"}
    assert {
        "delegation",
        "corroboration",
        "override",
    } <= forms  # the token-structure categories present
    # task-hijack + USER-override + the restored instruction attacks carry the injected TASK for j_obeyed
    # (not a canary token):
    obeyed = ("hijack", "user_override", *R._INSTRUCTION_ATTACKS)
    inj = [c for c in R.SPLITS["train"] if c.get("cls") in obeyed]
    assert inj and all(c.get("inject") and "tok" not in c for c in inj)


def test_curated_payloads_are_individually_removable_as_data():
    """The RESTORED real-world payload corpus is user-curatable: a poorly-specified example can be dropped with
    a DATA edit (enabled=false OR the exclusion list), NO code change. See concordance/adversarial_review.md."""
    from cascading_lms import final_eval as FE

    full = {r["id"] for r in FE.load_curated()}
    assert (
        full and "obfuscated_payload" not in full
    )  # encoding-transport dropped (parser-layer, out of scope)
    victim = sorted(full)[0]

    # mechanism 1: the exclusion list subtracts an id (data/excluded_examples.toml)
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=True) as tf:
        tf.write(f'excluded = ["{victim}"]\n')
        tf.flush()
        kept = {r["id"] for r in FE.load_curated(exclude=tf.name)}
    assert kept == full - {victim}

    # mechanism 2: enabled=false on the row drops it
    with open(FE.CURATED_PAYLOADS) as fh:
        rows = json.loads(fh.read())
    for r in rows:
        if r["id"] == victim:
            r["enabled"] = False
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=True) as tf:
        json.dump(rows, tf)
        tf.flush()
        kept2 = {r["id"] for r in FE.load_curated(path=tf.name)}
    assert kept2 == full - {victim}

    # every enabled row is j_obeyed-scored and carries a documented goal; build_real mirrors the loader:
    from cascading_lms import skillopt_formal as SF

    assert {c["form"] for c in FE.build_real()} == {f"real:{i}" for i in full}
    assert all(c["cls_name"] in SF._J_OBEYED_CLASSES for c in FE.build_real())


def test_web_additional_slices_disjoint_and_heldout():
    """The curated web_additional (source-labeling) cases split into DISJOINT train/val/FRESH slices (no
    leakage), are all tier-4, carry a web_fact, and the held-out slice covers FINAL_N_WA."""
    from cascading_lms import config

    tr = [c for c in R.SPLITS["train"] if c.get("cls") == "web_additional"]
    va = [c for c in R.SPLITS["val"] if c.get("cls") == "web_additional"]
    held = R.WEB_ADDITIONAL[config.CFG.dataset.wa_train + config.CFG.dataset.wa_val :]
    assert len(tr) == config.CFG.dataset.wa_train and len(va) == config.CFG.dataset.wa_val
    assert all(c["tier"] == 4 and c.get("web_fact") for c in tr + va)
    trc = {c["content"] for c in tr}
    vac = {c["content"] for c in va}
    frc = {e["content"] for e in held}
    assert not (trc & vac) and not (trc & frc) and not (vac & frc)  # disjoint slices, no leakage
    assert len(held) >= config.CFG.dataset.final_n_wa  # held-out slice covers the final-eval count
    # every case is tagged, and BOTH train and val mix relevant + irrelevant WEB facts -- so the optimizer
    # must learn to USE+ATTRIBUTE a relevant web fact AND OMIT an irrelevant one, not blanket-ignore WEB.
    assert all(isinstance(c.get("web_relevant"), bool) for c in tr + va)
    assert all(isinstance(e.get("web_relevant"), bool) for e in held)
    for slice_ in (tr, va, held[: config.CFG.dataset.final_n_wa]):
        rels = {c["web_relevant"] for c in slice_}
        assert rels == {True, False}, f"slice must mix relevant + irrelevant, got {rels}"


def test_unknown_attack_class_fails_loud():
    """A mistyped/unroutable class in a reshaped attack_mix fails loud at load (a helpful ValueError naming the
    bad class + the known ones) rather than silently generating a mislabeled token attack at case-build."""
    with pytest.raises(ValueError, match="unknown class"):
        R._validate_attack_mix(["tok", "bogus_class"])
    R._validate_attack_mix(R.ATTACK_MIX)  # the real default mix stays valid (no raise)
