# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Sanity checks on the central configuration (paths well-formed, key constants present and typed)."""

from cascading_lms import config


def test_path_helpers_are_relative_to_their_dirs():
    """data_path / run_path build paths under the configured directories (no absolute hard-coding)."""
    assert config.data_path("x.json") == f"{config.DATA_DIR}/x.json"
    assert config.run_path("y.jsonl") == f"{config.RUN_DIR}/y.jsonl"
    assert config.PROMPTS_JSON.startswith(config.DATA_DIR)
    assert config.SKILLOPT_LOG.startswith(config.RUN_DIR)


def test_models_are_named_not_hardcoded_elsewhere():
    """The judge and defended model ids live here as the single source of truth."""
    assert config.JUDGE_MODEL == "claude-opus-4-8"
    assert "gemma" in config.LOCAL_MODEL_DEFAULT


def test_optimizer_hyperparameters_are_in_range():
    """The acceptance/exploration constants are valid probabilities / tolerances."""
    assert 0.0 < config.CFG.optimizer.accept_conf <= 1.0
    assert 0.0 <= config.CFG.optimizer.tau_explore < 1.0
    assert 0.0 <= config.CFG.optimizer.ood_tol < 1.0
    assert config.CFG.optimizer.min_per_obj >= 1
    assert config.CFG.optimizer.len_min < config.CFG.optimizer.len_max_default


def test_canary_tokens_are_distinct_lowercase_words():
    """Canary tokens must be distinct single lowercase words (so the whole-word regex is unambiguous)."""
    toks = config.CFG.canary.tokens
    assert len(toks) == len(set(toks))
    assert all(t.isalpha() and t.islower() for t in toks)


def test_ind_and_ood_dataset_sizes_are_positive():
    """Every split has a positive attack and clean budget."""
    for attacks, clean in [
        (config.CFG.dataset.tune_train_attack, config.CFG.dataset.tune_train_clean),
        (config.CFG.dataset.tune_val_attack, config.CFG.dataset.tune_val_clean),
        (config.CFG.dataset.tune_ood_attack, config.CFG.dataset.tune_ood_clean),
    ]:
        assert attacks > 0 and clean > 0


def test_optimizer_sample_invariants():
    """Load-bearing sample invariants so a bad ratchet can't silently break decidability: MIN_PER_OBJ must fit
    BOTH val objectives (else the smaller objective on val is permanently undecidable and accept/reject
    collapses onto the other alone); the gradient caps must fit the minibatch."""
    assert (
        min(config.CFG.dataset.tune_val_clean, config.CFG.dataset.tune_val_attack)
        >= config.CFG.optimizer.min_per_obj
    )
    assert (
        config.CFG.optimizer.min_eval
        <= config.CFG.dataset.tune_val_clean + config.CFG.dataset.tune_val_attack
    )
    assert config.CFG.optimizer.grad_max_clean <= config.CFG.optimizer.grad_minibatch
    assert config.CFG.optimizer.grad_max_attack <= config.CFG.optimizer.grad_minibatch


def test_final_class_offsets_monotonic():
    """Final-eval per-class offsets are monotonic (poison < clean < agentdojo); the ACTUAL pool coverage of
    the highest offset is enforced by the runtime assert in final_eval."""
    assert (
        config.CFG.dataset.final_poison_offset
        < config.CFG.dataset.final_clean_offset
        < config.CFG.dataset.final_agentdojo_offset
    )
