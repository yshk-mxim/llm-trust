# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""MOO (Pareto-archive) optimization run -- the committed entry point (paper deliverable).

For each condition, run the multi-objective sweep: a JOINT (constrained multivariate) sweep when the
condition tunes several INTERACTING keys, else the single-key coordinate sweep. Each sweep maintains a
Pareto ARCHIVE and, at the end, ``finalize_and_deploy`` re-measures it PRECISELY on the full val (repeats +
OOD + per-key feasibility) and persists the deploy vector -- no noisy cap-scan pick, no fabricated point.
Same optimizer for every condition (optimizer-vs-optimizer fairness). Run: ``python src/moo_run.py`` (needs
the served 26B + the Opus judge API), or ``make moo``.
"""

import json
import os
import time
import traceback

from cascading_lms import config, trust_spec
from cascading_lms import skillopt_tuner as T

# Conditioned (the multivariate deliverable -- keys INTERACT, tuned JOINTLY) first, then each single-key
# baseline (coordinate sweep), all under the same optimizer for fairness.
PLAN = (
    (
        "conditioned",
        ("pass_ctx_USER", "pass_ctx_data", "wrapper_ctx"),
        config.CFG.moo_run.conditioned_passes,
    ),
    ("base+prompt", ("defense",), config.CFG.moo_run.baseline_passes),
    ("composite", ("composite",), config.CFG.moo_run.baseline_passes),
)


def _is_joint(keys) -> bool:
    """True iff this condition runs the JOINT sweep: the spec's tuning.mode is multivariate AND >1 interacting key.

    The mode is a spec knob (tuning.mode: coordinate | multivariate) -- setting it to ``coordinate`` runs the
    per-key sweep even for the conditioned cascade, which is the paper's 'you need interaction' ablation.
    """
    return trust_spec.DEFAULT.mode == "multivariate" and len(keys) > 1


def _sweep(cond, keys, passes):
    """JOINT (constrained multivariate) sweep when _is_joint, else the single-key coordinate sweep."""
    if _is_joint(keys):
        return T.pareto_sweep_multi(keys, passes, cond)
    return T.pareto_sweep(keys=keys, passes=passes, cond=cond)


def _validated_deploy(cond):
    """The PRECISE deploy point finalize_and_deploy persisted for ``cond`` (authoritative, not the cap-noisy select)."""
    try:
        with open(config.run_path(f"pareto_validated_{cond.replace('+', '_')}.json")) as fh:
            return json.load(fh).get("deploy")
    except FileNotFoundError:
        return None


def _run_condition(cond, keys, passes):
    """Run one condition's sweep; save its archive; return its summary entry (or an error entry, never raising)."""
    label = "JOINT" if _is_joint(keys) else "coordinate"
    print(f"[moo-run] === {cond}: {label} sweep {keys} x{passes} ===", flush=True)
    try:
        arch, _log = _sweep(cond, keys, passes)
        arch.save(config.run_path(f"pareto_archive_{cond.replace('+', '_')}.json"))
        return {
            "frontier": arch.frontier_points(),
            "n_points": len(arch.points),
            "deploy": _validated_deploy(
                cond
            ),  # precise, matches what was persisted to prompts.json
        }
    except Exception:
        tb = traceback.format_exc()
        print(f"[moo-run] {cond} ERROR:\n{tb}", flush=True)
        return {"error": tb}


def _sha256(text: str) -> str:
    """A short content hash for reproducibility fingerprints."""
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _manifest(plan: tuple) -> dict:
    """The reproducibility manifest: resolved spec identity + a content-hash of the active spec file + the plan."""
    with open(trust_spec.active_path()) as fh:
        spec_src = fh.read()
    return {
        "trust_model": trust_spec.DEFAULT.name,
        "trust_order": trust_spec.DEFAULT.trust_order_str(),
        "mode": trust_spec.DEFAULT.mode,
        "judge_model": trust_spec.DEFAULT.judge_model,
        "defended_model": trust_spec.DEFAULT.defended_model,
        "spec_path": trust_spec.active_path(),
        "spec_sha256": _sha256(spec_src),  # exact resolved spec content
        "plan": [{"cond": c, "keys": list(k), "passes": p} for c, k, p in plan],
    }


def _save_manifest(plan, run_dir: str) -> None:
    """Persist the reproducibility manifest + the human-readable resolved spec (atomic), top-level + in the run dir."""
    man = _manifest(plan)
    for path in (config.run_path("run_manifest.json"), f"{run_dir}/run_manifest.json"):
        config.atomic_write_json(path, man)
    with open(
        f"{run_dir}/trust_model.md", "w"
    ) as fh:  # the human-readable resolved spec, beside the run
        fh.write(trust_spec.DEFAULT.describe())


def _finalize_run(plan, run_dir: str, summary: dict) -> None:
    """Complete the run record: the deployed prompt-vector identity + summary + the archives, under the run dir (atomic)."""
    import shutil

    with open(config.PROMPTS_JSON) as fh:
        deployed = fh.read()
    man = _manifest(plan)
    man["deployed_prompts_sha256"] = _sha256(deployed)  # the exact vector this run shipped
    man["summary"] = {c: {k: s.get(k) for k in ("n_points", "deploy")} for c, s in summary.items()}
    config.atomic_write_json(f"{run_dir}/run_manifest.json", man)
    config.atomic_write_json(f"{run_dir}/deploy_prompts.json", json.loads(deployed))
    for c in (
        summary
    ):  # preserve each condition's archive AND its precisely-validated survivors into the run
        cc = c.replace(
            "+", "_"
        )  # dir: both carry the prompt VECTORS, so a run is RE-DECIDABLE (under a
        for stem in (
            f"pareto_archive_{cc}",
            f"pareto_validated_{cc}",
        ):  # changed gate/floor) WITHOUT re-sweeping.
            src = config.run_path(f"{stem}.json")
            if os.path.exists(src):
                shutil.copy(src, f"{run_dir}/{stem}.json")


def run(plan=PLAN):
    """Run every condition's MOO sweep; save each archive + a combined summary (atomic). Returns the summary.

    SEED_MODE (env) starts every sweep from a chosen seed for the seed-robustness demonstration -- ``cold``
    (empty defense-guidance) or ``wrong`` (anti-defense) should still CONVERGE to a strong deploy vector,
    evidencing the method not the seed. Unset (or ``hand``-with-no-env) is byte-identical: the sweep starts
    from the persisted vector as before.
    """
    from cascading_lms import llm
    from cascading_lms import retune as R

    seed_mode = os.environ.get("SEED_MODE")
    if (
        seed_mode
    ):  # demo: reseed the working vector so the sweeps start from build_seed's cold/wrong/hand
        seed = T.build_seed()  # reads SEED_MODE
        T._write_prompts(seed)
        R.P.clear()
        R.P.update(seed)
        print(
            f"[moo-run] SEED_MODE={seed_mode}: working vector reseeded from build_seed()",
            flush=True,
        )
    llm.refresh_model()
    run_dir = config.run_path(time.strftime("run_%Y%m%d_%H%M%S"))
    os.makedirs(
        run_dir, exist_ok=True
    )  # a per-run dir: manifest + resolved spec + archives + deploy
    _save_manifest(plan, run_dir)  # resolved spec + content hash + plan, so the run is reproducible
    t0 = time.time()
    summary = {}
    for cond, keys, passes in plan:
        summary[cond] = _run_condition(cond, keys, passes)
        print(f"[moo-run] === {cond} DONE (@{int((time.time() - t0) / 60)}m) ===\n", flush=True)
    _finalize_run(
        plan, run_dir, summary
    )  # + deployed-vector hash + archives, all under the run dir
    config.atomic_write_json(config.run_path("moo_run_summary.json"), summary)
    print(f"[moo-run] ALL SWEEPS COMPLETE ({int((time.time() - t0) / 60)}m)", flush=True)
    for cond, s in summary.items():
        print(
            f"[moo-run] {cond:12} n_points={s.get('n_points')} deploy={s.get('deploy')}", flush=True
        )
    return summary


if __name__ == "__main__":
    run()
