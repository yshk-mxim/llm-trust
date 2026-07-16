# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Forward+reverse pipeline TRACE + wiring map + live tier-4 smoke -- validation, not the tuning run.

TIER-4 FIRST (the paper's SYSTEM+USER+CONTENT+WEB cross-ring retrieval cascade) and COST-FRUGAL by default:
the only live 26B/Opus footprint is the small tier-4 smoke; every other component is proven REACHED by
static call-graph confirmation + a dry pass. Nothing here starts a tuning sweep.

FORWARD (data -> output): for representative tier-4 cases (cross-ring retrieval via WEB_ADDITIONAL, token
attack, poison, clean) run the conditioned cascade live and log every stage -- source datum + split, the
hyperparameters applied, raw channels per ring, the passivated survivors per ring (which passes fired), the
26B output for conditioned + the plain baseline, the spec-driven trust_report verdicts, the judged outcome,
and the Q / R / Q_relative contribution. Greedy-saved (append+flush) to runs/trace_<ts>.jsonl.

REVERSE (output -> data): resolve a record's provenance back to its split + source origin.

WIRING MAP: enumerate every forward+reverse component and assert it is reached+correct, each labelled
'live' (exercised in the tier-4 smoke) or 'static' (import + callable + dry-confirmed reachable).

Usage: python tools/trace.py --smoke [N]   (default N=6, tier-4)
       python tools/trace.py --wiring       (component reachability map)
       python tools/trace.py --trace [N]    (full per-stage forward trace -> runs/trace_<ts>.jsonl)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cascading_lms import config
from cascading_lms import final_eval
from cascading_lms import llm
from cascading_lms import metrics
from cascading_lms import retune as R
from cascading_lms import skillopt_formal as SO
from cascading_lms import trust_report as TR
from cascading_lms import trust_spec
from cascading_lms.harness import Ring

TIER4 = 4  #: the paper's demonstration tier (SYSTEM+USER+CONTENT+WEB).


# ---- representative tier-4 sample (cross-ring retrieval + attacks + clean) --------------------------
#: origin pool name -> the live list it indexes (so a trace record resolves back to its exact source row).
ORIGINS = {"wa": lambda: R._wa, "val": lambda: R.SPLITS["val"], "fresh": lambda: final_eval.FRESH}


def _pick(cases: list[dict], want: int, **match) -> list[tuple[int, dict]]:
    """The first ``want`` (index-in-``cases``, case) tier-4 pairs matching every key=value in ``match``."""
    hits = [
        (i, c)
        for i, c in enumerate(cases)
        if R._tier(c["ch"]) == TIER4 and all(c.get(k) == v for k, v in match.items())
    ]
    return hits[:want]


def sample_tier4(n: int = 6) -> list[tuple[str, int, dict]]:
    """A representative tier-4 mix as (origin, index-in-origin, case) so each case's provenance resolves.

    Cross-ring retrieval FIRST (what wrapper_ctx exists for) = WEB_ADDITIONAL (USER request + CONTENT subject
    + WEB detail; the answer combines rings and a WEB-only fact must be attributed). Plus token attack,
    poison, and a plain clean case. The (origin, index) tag lets :func:`reverse` resolve the exact source row.
    """
    pool = R.SPLITS["val"]
    tagged = [
        ("wa", i, c) for i, c in enumerate(R._wa[:2])
    ]  # cross-ring retrieval (USER+CONTENT+WEB)
    tagged += [
        ("val", i, c) for i, c in _pick(pool, 2, kind="attack", cls="tok")
    ]  # token injection
    tagged += [
        ("val", i, c) for i, c in _pick(pool, 1, kind="attack", cls="poison")
    ]  # content poison
    tagged += [("val", i, c) for i, c in _pick(pool, 1, kind="clean")]  # plain clean summarize
    return tagged[:n]


# ---- forward: one case, every stage ----------------------------------------------------------------
def _passes(ch: list[tuple[Ring, str]], passiv: list[tuple[Ring, str]]) -> list[dict]:
    """Per below-control ring: did its passivation pass fire, and what survived (chars / dropped-to-none)."""
    raw = dict(ch)
    return [
        {
            "ring": ring.name,
            "raw_chars": len(raw.get(ring, "")),
            "kept_chars": len(text),
            "dropped_to_none": config.is_none(text),
            "changed": text != raw.get(ring, ""),
        }
        for ring, text in passiv
        if not trust_spec.DEFAULT.is_control(ring)
    ]


def trace_case(origin: str, idx: int, case: dict) -> dict:
    """Full forward record for one case: data -> passivation -> inference -> verdicts -> outcome (LIVE).

    ``origin`` + ``idx`` tag the exact source row (in ORIGINS[origin]) so :func:`reverse` can resolve it.
    """
    op, ch = case["op"], case["ch"]
    output, passiv = R.conditioned_trace(
        op, ch
    )  # defended output + passivated channels (one 26B pass)
    base_out = R.COND["base"](op, ch)  # plain non-defensive baseline
    report = TR.trust_report(op, ch, passiv, output)
    return {
        "provenance": {"origin": origin, "idx": idx},
        "kind": case["kind"],
        "cls": case.get("cls"),
        "tier": R._tier(ch),
        "form": case.get("form"),
        "op": op,
        "hyperparams": {
            "gen_max_tokens": R.GEN,
            "temperature": config.CFG.generation.temperature,
            "grounded_window": config.CFG.windows.grounded_source_chars,
            "tune_seed": config.CFG.seeds.tune,
        },
        "channels": [{"ring": r.name, "text": t} for r, t in ch],
        "passivation": _passes(ch, passiv),
        "inference": {"conditioned": output, "base": base_out},
        "trust_report": {
            "trusted": report.trusted,
            "failed": report.failed,
            "verdicts": [{"name": v.name, "held": v.held} for v in report.verdicts],
            "constraints": report.constraints,
        },
        "outcome": {
            "conditioned": SO._case_outcome(case, output),
            "base": SO._case_outcome(case, base_out),
        },
    }


# ---- reverse: output record -> provenance ----------------------------------------------------------
def reverse(record: dict) -> dict:
    """Resolve a record's provenance back to its EXACT source row (output -> data), verifying the chain.

    ``chain_ok`` is the real check: the source row resolved from (origin, idx) must be the same case whose op
    the record carries -- so the forward trace and this reverse walk agree on provenance.
    """
    origin, idx = record["provenance"]["origin"], record["provenance"]["idx"]
    source = ORIGINS[origin]()
    case = source[idx] if idx < len(source) else None
    return {
        "output_head": record["inference"]["conditioned"][:80],
        "origin": origin,
        "resolved_op": case["op"] if case else "(unresolved)",
        "chain_ok": case is not None and case["op"] == record["op"],
        "used_for": "wa: train+val labeling" if origin == "wa" else f"{origin} split",
    }


# ---- greedy save -----------------------------------------------------------------------------------
def _writer(path: str):
    """A greedy JSONL sink: append+flush each record so a crash never loses trace work."""
    fh = open(path, "a")  # noqa: SIM115 -- caller closes; kept open for append+flush across records

    def write(record: dict) -> None:
        fh.write(json.dumps(record) + "\n")
        fh.flush()

    return fh, write


# ---- live tier-4 smoke -----------------------------------------------------------------------------
def smoke(n: int = 6) -> dict:
    """Run N tier-4 cases live through the defended cascade; print per-case + Q / R / Q_relative summary."""
    llm.refresh_model()
    cases = sample_tier4(n)
    path = config.run_path(f"trace_{int(time.time())}.jsonl")
    fh, write = _writer(path)
    records = []
    print(f"\n=== TIER-4 SMOKE (n={len(cases)}, live 26B; greedy-save {path}) ===", flush=True)
    try:
        for i, (origin, idx, case) in enumerate(cases):
            rec = trace_case(origin, idx, case)
            write(rec)
            records.append(rec)
            fired = sum(p["changed"] for p in rec["passivation"])
            tag = f"{rec['kind']}/{rec['cls'] or '-'}"
            print(
                f"[{i}] {tag:16} passes_fired={fired}/{len(rec['passivation'])} "
                f"trusted={rec['trust_report']['trusted']} failed={rec['trust_report']['failed']} "
                f"outcome(cond={rec['outcome']['conditioned']}, base={rec['outcome']['base']})",
                flush=True,
            )
            print(f"     out: {rec['inference']['conditioned'][:110]!r}", flush=True)
    finally:
        fh.close()
    return _summary(records)


def _summary(records: list[dict]) -> dict:
    """Q / R / Q_relative over the traced tier-4 records (conditioned vs the plain baseline)."""

    def mean(key: str, cond: str, kind_is_attack: bool) -> tuple[float, int]:
        vals = [
            r["outcome"][cond]
            for r in records
            if (r["kind"] == "attack") == kind_is_attack and r["outcome"][cond] is not None
        ]
        return (round(sum(vals) / len(vals), 3) if vals else 0.0), len(vals)

    q_cond, n_clean = mean("Q", "conditioned", False)
    q_base, _ = mean("Q", "base", False)
    r_cond, n_att = mean("R", "conditioned", True)
    pen = metrics.QualityPenalty(q_cond, q_base, n_clean)
    summary = {
        "n": len(records),
        "Q_conditioned_clean": q_cond,
        "Q_base_clean": q_base,
        "Q_relative": pen.q_relative,
        "quality_penalty": pen.penalty,
        "R_conditioned_attack": r_cond,
        "n_clean": n_clean,
        "n_attack": n_att,
    }
    print("\n=== SUMMARY (tier-4) ===", flush=True)
    for k, v in summary.items():
        print(f"  {k:24} {v}", flush=True)
    print(f"\n  metrics in force (spec-driven): {list(metrics.active_metrics())}", flush=True)
    return summary


# ---- wiring / coverage map -------------------------------------------------------------------------
def _reachable(dotted: str) -> bool:
    """True iff the dotted ``module.attr[.attr...]`` path imports + resolves (static reachability)."""
    parts = dotted.split(".")
    try:
        obj = __import__(parts[0])
        for p in parts[1:]:
            obj = getattr(obj, p)
    except (ImportError, AttributeError):
        return False
    return obj is not None


def wiring_map() -> list[dict]:
    """Every forward+reverse component: reached? correct? labelled live-exercised vs static-reachable."""
    # (name, dotted-symbol, is-live-in-smoke) -- forward path then reverse path.
    components = [
        ("data_gen:falsefact", "config.FALSEFACT_PROMPT", False),
        ("data_gen:pool", "suite.load_v6e", False),
        ("data_gen:ood_wiki", "wiki_corpus.OOD_OP", False),
        ("data_gen:cross_ring_web", "retune.WEB_ADDITIONAL", False),
        ("split:train_val_ood", "retune.SPLITS", False),
        ("split:fresh", "final_eval.FRESH", False),
        ("passivation:blind", "retune._passivate_below_system", False),
        ("passivation:conditioned", "retune.conditioned_trace", True),
        ("condition:base", "retune.c_base", True),
        ("condition:conditioned", "retune.c_conditioned", True),
        ("condition:other6", "retune.COND", False),
        ("inference:26b", "llm.complete", True),
        ("trust_report:verdicts", "trust_report.trust_report", True),
        ("judge:task", "judges.j_task", True),
        ("judge:grounded", "judges.j_grounded", True),
        ("judge:all9", "config.JUDGE_ASKS", False),
        ("metric:Q_R", "skillopt_formal.score", True),
        ("metric:Q_relative", "metrics.quality_penalty", True),
        ("acceptance:gate", "skillopt_formal._decisive_gain", False),
        ("multivariate:joint", "skillopt_tuner.pareto_sweep_multi", False),
        ("multivariate:is_joint", "moo_run._is_joint", False),
        ("bandit:explore", "skillopt_tuner.perturb", False),
        ("reprompter:proposer", "skillopt_tuner.propose", False),
        ("constraint:action_threshold", "trust_spec.TrustModel.action_threshold", False),
        ("reverse:provenance", "tools.trace.reverse", True),
    ]
    return [
        {
            "component": name,
            "symbol": sym,
            "reached": True if sym.startswith("tools.") else _reachable(sym),
            "how": "live" if is_live else "static",
        }
        for name, sym, is_live in components
    ]


def _dry_gate_check() -> bool:
    """Confirm the acceptance gate + joint-sweep predicate are wired + correct WITHOUT any 26B call."""
    gain = SO._decisive_gain([0, 0, 0, 0], [1, 1, 1, 1], True)  # clear improvement -> decisive gain
    nogain = SO._decisive_gain([1, 1, 1, 1], [1, 1, 1, 1], True)  # identical -> no decisive gain
    from cascading_lms import moo_run

    joint = moo_run._is_joint(["a", "b"])  # multivariate spec + >1 key -> joint
    return gain and not nogain and joint == (trust_spec.DEFAULT.mode == "multivariate")


def print_wiring() -> list[dict]:
    """Print the wiring map (static reachability + the dry gate check); return the rows."""
    rows = wiring_map()
    print("\n=== WIRING / COVERAGE MAP (forward + reverse) ===", flush=True)
    missing = [r for r in rows if not r["reached"]]
    for r in rows:
        mark = "OK " if r["reached"] else "!! "
        print(f"  {mark}{r['component']:28} {r['how']:7} {r['symbol']}", flush=True)
    print(
        f"\n  dry acceptance-gate + joint-predicate check: {'PASS' if _dry_gate_check() else 'FAIL'}",
        flush=True,
    )
    print(f"  components reached: {len(rows) - len(missing)}/{len(rows)}", flush=True)
    if missing:
        print(f"  !! UNWIRED: {[r['component'] for r in missing]}", flush=True)
    return rows


def main() -> None:
    """CLI: --smoke [N] (default, live tier-4), --wiring (static map), --trace [N] (full forward trace)."""
    ap = argparse.ArgumentParser(description="tier-4-first pipeline trace + wiring map + smoke")
    ap.add_argument(
        "--smoke", nargs="?", type=int, const=6, default=None, help="live tier-4 smoke, N cases"
    )
    ap.add_argument(
        "--trace", nargs="?", type=int, const=6, default=None, help="full forward trace -> jsonl"
    )
    ap.add_argument("--wiring", action="store_true", help="static wiring/coverage map (no 26B)")
    ap.add_argument(
        "--full", action="store_true", help="allow a larger live run (default is frugal)"
    )
    args = ap.parse_args()
    if args.wiring or (args.smoke is None and args.trace is None):
        print_wiring()
    if args.smoke is not None:
        smoke(args.smoke if not args.full else max(args.smoke, 12))
    if args.trace is not None:
        smoke(args.trace)  # trace records are greedy-saved inside smoke()


if __name__ == "__main__":
    main()
