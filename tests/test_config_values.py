# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""config.py parses data/config.toml into the pipeline constants; assert a representative set equals its
KNOWN pre-refactor value so a mis-parse or mis-typed yaml is caught (byte-identical).
"""

from cascading_lms import config


def test_tunable_constants_byte_identical():
    assert config.CFG.seeds.tune == 4242
    assert config.CFG.seeds.final == 2028
    assert config.CFG.api.timeout_s == 120
    assert config.CFG.api.retries == 8
    assert config.CFG.api.retry_codes == (429, 500, 503, 529)
    assert config.CFG.api.backoff_start == 2.0
    assert config.CFG.generation.max_tokens == 170
    assert config.CFG.generation.temperature == 0.7
    assert config.CFG.generation.llm_default_max_tokens == 256
    assert config.CFG.generation.judge_max_tokens == 32
    assert config.CFG.generation.joint_propose_max_tokens == 2500
    assert config.CFG.windows.judge_source_chars == 200
    assert config.CFG.windows.grounded_source_chars == 400
    assert config.CFG.windows.task_source_chars == 400  # derived == GROUNDED_SOURCE_CHARS
    assert config.CFG.cascade.nonce_bytes == 4


def test_dataset_and_optimizer_byte_identical():
    assert (config.CFG.dataset.tune_train_attack, config.CFG.dataset.tune_train_clean) == (100, 50)
    assert (config.CFG.dataset.tune_val_attack, config.CFG.dataset.tune_val_clean) == (60, 30)
    assert (config.CFG.dataset.wa_train, config.CFG.dataset.wa_val) == (7, 7)
    assert (
        config.CFG.dataset.final_poison_offset == 120
        and config.CFG.dataset.final_clean_offset == 160
    )
    assert (config.CFG.content_bounds.tune_len_min, config.CFG.content_bounds.tune_len_max) == (
        45,
        320,
    )
    assert config.CFG.optimizer.min_per_obj == 12 and config.CFG.optimizer.eval_batch == 12
    assert config.CFG.optimizer.pareto_q_floor == 0.80
    assert config.CFG.optimizer.tau_pareto == 0.02
    assert config.CFG.optimizer.moo_eval_cap == 60
    assert config.CFG.optimizer.final_validate_repeats == 2
    assert config.CFG.optimizer.accept_conf == 0.80 and config.CFG.optimizer.ood_tol == 0.05
    assert config.CFG.optimizer.len_max == {"composite": 2000, "wrapper_ctx": 1800}


def test_collections_byte_identical():
    assert config.CFG.canary.probe == "PWNED"
    assert config.CFG.canary.tokens[0] == "marigold" and len(config.CFG.canary.tokens) == 10
    assert (
        config.CFG.unserveable_op_markers[0] == "audio"
        and "python" in config.CFG.unserveable_op_markers
    )
    assert isinstance(config.CFG.api.retry_codes, tuple)
    assert isinstance(config.CFG.unserveable_op_markers, tuple)
