# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""E2E smoke (offline, fully mocked): spec -> TrustModel -> pipeline -> a tiny mocked tune ->
finalize_and_deploy -> deploy, run deterministically for BOTH the default lattice AND example2 (a genuinely
different trust model). example2 must be the IMPORT-TIME active spec, so each case runs in a subprocess with
TRUST_MODEL_SPEC set. The 26B, the Opus judges, and scoring are all mocked -- no model, no API, no file writes.
"""

import os
import subprocess
import sys

_RELEASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Runs the joint sweep end-to-end with every external call mocked, then asserts a FULL vector was deployed.
_SCRIPT = r"""
import sys, types
sys.path.insert(0, "src")
from cascading_lms import config, skillopt_formal as SO, skillopt_tuner as T
from cascading_lms import trust_spec

# mock the 26B (a live, scorable backend) + scoring (always feasible, above the Q-floor)
import cascading_lms as _clms
_clms.llm = types.SimpleNamespace(refresh_model=lambda: None, complete=lambda *a, **k: "ok")
SO.score = lambda vec, split, conds, cap=None: {conds[0]: {"Q": 0.9, "R": 0.9, "n_clean": 10, "n_att": 10}}
T.SC.check_key = lambda k, v: []                       # every tuned key feasible
config.atomic_write_json = lambda *a, **k: None        # no file writes
config.run_path = lambda name: "/tmp/_e2e_" + name.replace("/", "_")

# a FULL seed vector; capture what gets deployed
KEYS = ("pass_ctx_USER", "pass_ctx_data", "wrapper_ctx")
seed = {k: f"seed {k}" for k in (*KEYS, "defense", "composite")}
seed["id"] = "S"
T._load_prompts = lambda: dict(seed)
T._snapshot_pre_run = lambda cond: None
written = {}
T._write_prompts = lambda vec: written.update({"vec": dict(vec)})

# a TINY tune: the first joint candidate archives, the rest reject
calls = {"n": 0}
cand = {**seed, "id": "cand", "wrapper_ctx": "improved wrapper"}
def fake_multi(name, keys, cond, incumbent):
    calls["n"] += 1
    if calls["n"] == 1:
        return {"round": name, "edit_keys": list(keys), "verdict": "archive",
                "Q": 0.9, "R": 0.95, "Q_inc": 0.9, "R_inc": 0.7, "vector": dict(cand)}
    return {"round": name, "edit_keys": list(keys), "verdict": "reject", "reason": "no gain"}
T._safe_pareto_multi = fake_multi

arch, log = T.pareto_sweep_multi(KEYS, passes=1, cond="conditioned")
assert "vec" in written, "nothing was deployed"
assert set(seed) <= set(written["vec"]), "deploy is not a FULL vector"
assert len(arch.points) >= 2, "seed + archived candidate expected"
print("E2E-OK", trust_spec.DEFAULT.name)
"""


def _run(spec: str | None) -> str:
    env = dict(os.environ)
    if spec:
        env["TRUST_MODEL_SPEC"] = spec
    r = subprocess.run(
        [sys.executable, "-c", _SCRIPT], cwd=_RELEASE, env=env, capture_output=True, text=True
    )
    assert "E2E-OK" in r.stdout, f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    return r.stdout


def test_e2e_smoke_default_lattice():
    assert "default-4ring" in _run(None)


def test_e2e_smoke_example2_lattice():
    assert "example2" in _run("data/trust_model_example2.toml")
