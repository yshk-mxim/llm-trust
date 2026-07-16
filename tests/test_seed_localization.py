# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""The seed/role DEFENSE prose is authored in the default ring names and spec-localized to the ACTIVE lattice
at load time (config._ring_localizer / _localize_prompts). Two guarantees:

1. DEFAULT lattice -> byte-identical (the committed tuned deploy + every test + the concordance stay valid).
2. A DIFFERENT lattice -> the seed NAMES that lattice's rings + renders its TRUE trust order (so the tuner
   departs from a coherent, correctly-named seed; the prose is a starting point, deploying re-tunes from it).
"""

from cascading_lms import config, trust_spec


def test_default_localization_is_byte_identical():
    # rendering with the DEFAULT spec must reproduce the authored prose EXACTLY (zero diff) for every string
    render = config._ring_localizer(trust_spec.DEFAULT)
    for section in ("seed", "roles"):
        for key, text in config._prompts[section].items():
            assert render(text) == text, (
                f"{section}.{key} not byte-identical under default localization"
            )


def test_loaded_seed_and_roles_match_raw_default():
    # the module-level SEED_PROMPTS / ROLE_PROMPTS equal the raw pack under the default lattice
    assert config._prompts["seed"] == config.SEED_PROMPTS
    assert config._prompts["roles"] == config.ROLE_PROMPTS


def _example2():
    return trust_spec.TrustModel.load(config.data_path("trust_model_example2.toml"))


def test_example2_seed_names_its_rings_and_true_order():
    ex2 = _example2()
    render = config._ring_localizer(ex2)
    wrapper = render(config._prompts["seed"]["wrapper_ctx"])
    composite = render(config._prompts["seed"]["composite"])
    # example2's primary data ring (RAG) is named where the authored prose said the default primary (CONTENT)
    assert "RAG" in wrapper
    # the composite renders example2's TRUE multi-ring order, not the default 4-ring phrase
    assert ex2.trust_order_str() in composite
    assert "SYSTEM > USER > CONTENT > WEB" not in composite


def test_localization_changes_something_off_default():
    # a non-default lattice must actually transform the prose (guards against an accidental no-op)
    ex2 = _example2()
    render = config._ring_localizer(ex2)
    assert render(config._prompts["seed"]["wrapper_ctx"]) != config._prompts["seed"]["wrapper_ctx"]
