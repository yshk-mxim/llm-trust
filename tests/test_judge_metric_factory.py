# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Judge + metric trust-definition factories (org-configurable), offline.

Default = byte-identical passthrough: the default spec has no ``judges:``/``metrics:`` section, so ``judge(name)``
returns the EXACT hardcoded function and ``metric(name)`` the default composition. A spec that adds a section
overrides -- an org redefines what "task-correct"/"quality" mean without editing code.
"""

from cascading_lms import judges, trust_spec

_ALL = (
    "j_task",
    "j_grounded",
    "j_asserted",
    "j_surfaced",
    "j_attributed",
    "j_restate",
    "j_hascommand",
    "j_obeyed",
)


def test_default_judges_are_the_exact_existing_functions():
    assert trust_spec.DEFAULT.judges == {}  # default spec has no judges: section
    for name in _ALL:
        assert judges.judge(name) is getattr(judges, name)  # identity -> byte-identical


def test_default_metric_is_the_default_composition():
    assert "task AND grounded" in trust_spec.DEFAULT.metric("Q")
    assert "canary" in trust_spec.DEFAULT.metric("R")


def test_spec_override_builds_a_custom_judge(monkeypatch):
    monkeypatch.setitem(
        trust_spec.DEFAULT.judges,
        "j_task",
        {"name": "j_task", "asks": "STRICT: fully correct with NO omission?"},
    )
    custom = judges.judge("j_task")
    assert custom is not judges.j_task  # a factory-built judge, not the default

    seen = {}

    def fake_yn(prompt):
        seen["p"] = prompt
        return True

    monkeypatch.setattr(judges, "yn", fake_yn)
    custom("op", "content", "out")
    # same evidence layout + suffix as the default, only the criteria swapped
    assert "TASK: op" in seen["p"]
    assert "STRICT: fully correct with NO omission?" in seen["p"]
    assert seen["p"].endswith("Answer YES or NO only.")


def test_metric_override_changes_the_descriptor(monkeypatch):
    monkeypatch.setitem(trust_spec.DEFAULT.metrics, "Q", "STRICT org Q definition")
    assert trust_spec.DEFAULT.metric("Q") == "STRICT org Q definition"


def test_override_reaches_the_measurement_path(monkeypatch):
    # the WIRING: measurement-path callers now resolve judges via judges.judge(...), so a spec override
    # actually reaches the live metric (not just judge() in isolation). stage_check.grounded is one such caller.
    from cascading_lms import stage_check

    monkeypatch.setitem(
        trust_spec.DEFAULT.judges, "j_grounded", {"name": "j_grounded", "asks": "ORG-GROUNDING?"}
    )
    seen = {}

    def fake_yn(prompt):
        seen["p"] = prompt
        return True

    monkeypatch.setattr(judges, "yn", fake_yn)
    stage_check.grounded("the sources", "the output")  # not (none) -> calls the judge
    assert "ORG-GROUNDING?" in seen["p"]  # the override reached the live measurement path


def test_example2_encodes_a_different_trust_definition():
    ex2 = trust_spec.TrustModel.load("data/trust_model_example2.toml")
    default = trust_spec.TrustModel.load("data/trust_model.toml")
    assert ex2.judges.get("j_task", {}).get("asks")  # a stricter task judge
    assert ex2.metric("Q") != default.metric("Q")  # a different quality definition
    assert default.judges == {} and default.metrics == {}  # the default itself is un-overridden
