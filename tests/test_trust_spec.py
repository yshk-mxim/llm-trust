# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""TrustModel spec (offline): the DEFAULT spec reproduces the current hardcoded ring structure, and the
loader validates the spec against the harness.Ring enum. This is the equivalence proof that lets the pipeline
be rewired to derive from the spec without changing behavior.
"""

import pytest

from cascading_lms import config, ring_spec, trust_spec
from cascading_lms.harness import Ring


# The default spec must yield the KNOWN-GOOD 4-ring structure (hardcoded expected, so a spec change that would
# alter the default lattice is caught -- not tautological now that the consumers derive from the spec).
def test_default_yields_known_tier_rings():
    assert trust_spec.DEFAULT.tier_rings == {
        2: [Ring.CONTENT],
        3: [Ring.USER, Ring.CONTENT],
        4: [Ring.USER, Ring.CONTENT, Ring.WEB],
    }


def test_default_yields_known_pass_ctx_key():
    assert trust_spec.DEFAULT.pass_ctx_key == {
        "USER": "pass_ctx_USER",
        "CONTENT": "pass_ctx_data",
        "WEB": "pass_ctx_data",
    }


def test_default_yields_known_passr_and_blind_keys():
    assert trust_spec.DEFAULT.passr == {Ring.USER, Ring.CONTENT, Ring.WEB}
    assert trust_spec.DEFAULT.blind_pass_key == {
        Ring.USER: "pass_USER",
        Ring.CONTENT: "pass_CONTENT",
        Ring.WEB: "pass_WEB",
    }


def test_default_yields_known_role_spec():
    got = trust_spec.DEFAULT.role_spec
    assert set(got) == {Ring.USER, Ring.CONTENT, Ring.WEB}
    assert got[Ring.USER]["prompt_key"] == "pass_ctx_USER"
    assert got[Ring.CONTENT]["prompt_key"] == "pass_ctx_data"
    assert got[Ring.WEB]["prompt_key"] == "pass_ctx_data"
    assert "override a higher ring" in got[Ring.USER]["must_not"]


def test_default_yields_known_basis():
    # tier-4 basis: WEB conditions on USER (request) only; CONTENT on USER; USER on nothing (no higher request)
    assert trust_spec.DEFAULT.basis(4, Ring.WEB) == [Ring.USER]
    assert trust_spec.DEFAULT.basis(4, Ring.CONTENT) == [Ring.USER]
    assert trust_spec.DEFAULT.basis(4, Ring.USER) == []


def test_consumers_derive_from_spec():
    # the rewire: the module aliases equal the spec-derived values (proves nothing hardcodes the ring set)
    assert trust_spec.DEFAULT.tier_rings == ring_spec.TIER_RINGS
    assert trust_spec.DEFAULT.pass_ctx_key == config.PASS_CTX_KEY
    assert set(config.TIERS) == set(trust_spec.DEFAULT.tiers)


def test_integrity_matches_enum():
    for r in trust_spec.DEFAULT.rings:
        assert Ring[r.name].value == r.integrity


def test_trust_order_string():
    assert trust_spec.DEFAULT.trust_order_str() == "SYSTEM > USER > CONTENT > WEB"


def test_plane_boundaries_match_old_predicates():
    tm = trust_spec.DEFAULT
    for ring in (Ring.SYSTEM, Ring.USER, Ring.CONTENT, Ring.WEB):
        assert tm.is_data(ring) == (int(ring) < int(Ring.USER))
        assert tm.is_passivated(ring) == (int(ring) < int(Ring.SYSTEM))
        assert tm.in_basis(ring) == (int(ring) >= int(Ring.USER))
    assert tm.is_control(Ring.SYSTEM) and not tm.is_control(Ring.USER)


def test_mode_default_is_multivariate():
    assert trust_spec.DEFAULT.mode == "multivariate"


def test_describe_renders_the_lattice():
    md = trust_spec.DEFAULT.describe()
    assert "# Trust model: default-4ring" in md
    assert "SYSTEM > USER > CONTENT > WEB" in md
    assert "claude-opus-4-8" in md  # models section rendered
    assert "pass_ctx_USER" in md  # per-ring prompt key rendered


def test_validation_rejects_integrity_mismatch(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"name":"bad","rings":['
        '{"name":"USER","integrity":999,"plane":"request","prompt_key":"x"},'
        '{"name":"SYSTEM","integrity":100,"plane":"control"}],'
        '"actions":{"action_min":"USER"},"tiers":{"3":["USER"]},"tuning":{"mode":"multivariate"}}'
    )
    with pytest.raises(ValueError, match="must match the proven lattice"):
        trust_spec.TrustModel.load(str(bad))


def test_order_desync_guard_catches_a_contradicting_prompt():
    from cascading_lms import retune as R

    R.P["_desync_probe"] = (
        "for this test the order is WEB > CONTENT"  # contradicts spec (CONTENT > WEB)
    )
    try:
        assert any("_desync_probe" in f[0] for f in ring_spec._order_fails())
    finally:
        R.P.pop("_desync_probe", None)
    assert not any(
        "_desync_probe" in f[0] for f in ring_spec._order_fails()
    )  # cleaned up -> no fail


def test_validation_rejects_two_control_rings(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"name":"bad","rings":['
        '{"name":"SYSTEM","integrity":100,"plane":"control"},'
        '{"name":"USER","integrity":40,"plane":"control"}],'
        '"actions":{"action_min":"USER"},"tiers":{},"tuning":{"mode":"coordinate"}}'
    )
    with pytest.raises(ValueError, match="exactly one control ring"):
        trust_spec.TrustModel.load(str(bad))


# --- per-action authority (Chunk B) ------------------------------------------------------------------
def test_default_per_action_falls_back_to_global():
    assert trust_spec.DEFAULT.per_action == {}
    assert trust_spec.DEFAULT.action_threshold("send_email") == Ring.USER  # unlisted -> action_min
    assert trust_spec.DEFAULT.action_threshold() == Ring.USER


def test_example2_per_action_thresholds():
    tm = trust_spec.TrustModel.load("data/trust_model_example2.toml")
    assert tm.action_threshold("send_email") == Ring.USER
    assert tm.action_threshold("delete_db") == Ring.SYSTEM
    assert tm.action_threshold("unknown") == Ring.USER  # fallback to action_min


def test_per_action_authorize_denies_low_authority_destructive_action():
    from cascading_lms import harness

    tm = trust_spec.TrustModel.load("data/trust_model_example2.toml")
    user_authority = harness.ingest("x", Ring.USER)  # authority traces to USER
    assert harness.authorize_action(user_authority, min_ring=tm.action_threshold("send_email"))
    assert not harness.authorize_action(user_authority, min_ring=tm.action_threshold("delete_db"))


def test_validation_rejects_unknown_per_action_ring(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"name":"bad","rings":['
        '{"name":"SYSTEM","integrity":100,"plane":"control"},'
        '{"name":"USER","integrity":40,"plane":"request","prompt_key":"x"}],'
        '"actions":{"action_min":"USER","per_action":{"a":"NOPE"}},'
        '"tiers":{"3":["USER"]},"tuning":{"mode":"coordinate"}}'
    )
    with pytest.raises(ValueError, match="per_action"):
        trust_spec.TrustModel.load(str(bad))


# --- threat_model (org-configurable attack mix; default = pipeline mix, byte-identical) ---------------
def test_threat_model_default_is_comprehensive_taxonomy():
    from cascading_lms import retune

    # The default spec declares a COMPREHENSIVE injection taxonomy (nothing deleted): token-emit + task-hijack
    # + USER task-override + delegation + corroboration + USER token-override + content-poison + the real-world
    # named payloads (persona / authority_impersonation / social_approval / fake_completion / reveal /
    # delimiter_escape, RESTORED from archive). R dispatches scoring by class (canary / j_obeyed / j_asserted).
    # Encoding-transport obfuscation (base64/reversal/...) is deliberately EXCLUDED -- the parser/sanitizer
    # plugin layer handles it upstream, not the cascade. See docs/dataset_design.md.
    expected = [
        "tok",
        "hijack",
        "user_override",
        "deleg",
        "corrob",
        "override",
        "poison",
        "persona",
        "authority_impersonation",
        "social_approval",
        "fake_completion",
        "reveal",
        "delimiter_escape",
    ]
    mix = trust_spec.DEFAULT.threat_model.get("attack_mix")
    assert mix == expected
    assert mix == retune.ATTACK_MIX
    assert set(mix) == set(expected)
    # every declared instruction class is j_obeyed-scored; encoding-transport obfuscation is not a class:
    assert set(retune._INSTRUCTION_ATTACKS) <= set(mix)
    assert "obfuscation" not in set(mix)
    assert retune.IND_FORMS == ["instr", "redirect", "important", "conditional"]
    assert retune.OOD_FORMS == ["spanish", "codefence", "payloadsplit", "table"]


def test_threat_model_override(tmp_path):
    spec = tmp_path / "t.json"
    spec.write_text(
        '{"name":"t","rings":['
        '{"name":"SYSTEM","integrity":100,"plane":"control"},'
        '{"name":"USER","integrity":40,"plane":"request","prompt_key":"pass_ctx_USER"},'
        '{"name":"CONTENT","integrity":30,"plane":"data","prompt_key":"pass_ctx_data"}],'
        '"actions":{"action_min":"USER"},"tiers":{"3":["USER","CONTENT"]},'
        '"tuning":{"mode":"multivariate"},'
        '"threat_model":{"attack_mix":["poison","poison"],"ood_forms":["table"]}}'
    )
    tm = trust_spec.TrustModel.load(str(spec))
    assert tm.threat("attack_mix", ["x"]) == ["poison", "poison"]  # overridden
    assert tm.threat("ind_forms", ["dflt"]) == ["dflt"]  # unspecified -> pipeline default
    assert tm.threat("ood_forms", ["x"]) == ["table"]


def test_threat_model_accepts_eval_hijack_mix(tmp_path):
    # the held-out INSTRUCTION-attack batch (eval_hijack_mix[,_low]) must be reshapeable via the spec, just
    # like the token/train mixes -- a spec that sets it is ACCEPTED (not rejected as an unknown key) and the
    # auto-doc renders it.
    spec = tmp_path / "t.json"
    spec.write_text(
        '{"name":"t","rings":['
        '{"name":"SYSTEM","integrity":100,"plane":"control"},'
        '{"name":"USER","integrity":40,"plane":"request","prompt_key":"pass_ctx_USER"},'
        '{"name":"CONTENT","integrity":30,"plane":"data","prompt_key":"pass_ctx_data"}],'
        '"actions":{"action_min":"USER"},"tiers":{"3":["USER","CONTENT"]},'
        '"tuning":{"mode":"multivariate"},'
        '"threat_model":{"eval_hijack_mix":["reveal","persona"],"eval_hijack_mix_low":["hijack"]}}'
    )
    tm = trust_spec.TrustModel.load(str(spec))
    assert tm.threat("eval_hijack_mix", ["x"]) == ["reveal", "persona"]
    assert tm.threat("eval_hijack_mix_low", ["x"]) == ["hijack"]
    assert "eval_hijack_mix" in tm.describe()  # auto-doc renders every threat key


def test_threat_model_rejects_bad_key(tmp_path):
    spec = tmp_path / "t.json"
    spec.write_text(
        '{"name":"t","rings":[{"name":"SYSTEM","integrity":100,"plane":"control"}],'
        '"actions":{"action_min":"SYSTEM"},"tiers":{},"tuning":{"mode":"coordinate"},'
        '"threat_model":{"bogus":["x"]}}'
    )
    with pytest.raises(ValueError, match="threat_model key"):
        trust_spec.TrustModel.load(str(spec))
