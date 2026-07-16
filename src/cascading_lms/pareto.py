# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Q-R Pareto frontier of the tuner's candidates (paper result + gate-validation).

Every tuning candidate is a measured (Q, R) point (skillopt_formal logs prompt + val_old + val_new). The
acceptance gate keeps only candidates that decisively dominate on the noise-calibrated bootstrap, so it
DISCARDS the frontier -- but the frontier itself is a paper result (the achievable quality-vs-defense
tradeoff of the conditioned cascade). This module (1) EXTRACTS the frontier from the tuner log, and (2)
VALIDATES the non-dominated points by RE-MEASURING each candidate's (Q, R) independently -- so we can tell a
TRUE Pareto point from single-val noise, and flag any candidate the gate rejected that actually dominates.
"""

import json

from cascading_lms import config


def load_candidates(log_path=None):
    """Every logged candidate as {round, key, cond, prompt, len, Q, R, Q_old, R_old, accept}."""
    path = log_path or config.TUNER_SWEEP_LOG
    out = []
    with open(path) as fh:
        # the log is either a JSON array (final) or JSONL (streamed) -- handle both
        text = fh.read().strip()
    records = (
        json.loads(text)
        if text.startswith("[")
        else [json.loads(x) for x in text.splitlines() if x]
    )
    for r in records:
        vn, vo = r.get("val_new") or {}, r.get("val_old") or {}
        if vn.get("Q") is None or vn.get("R") is None:
            continue  # length-reject / errored candidate -- no measured point
        out.append(
            {
                "round": r.get("round"),
                "key": r.get("edit"),
                "cond": r.get("cond"),
                "prompt": r.get("cand"),
                "len": r.get("len"),
                "Q": vn["Q"],
                "R": vn["R"],
                "Q_old": vo.get("Q"),
                "R_old": vo.get("R"),
                "accept": bool(r.get("accept")),
            }
        )
    return out


def _dominated(p, pts):
    """True iff some other point is >= p on BOTH Q and R and strictly greater on at least one."""
    return any(
        (q["Q"] >= p["Q"] and q["R"] >= p["R"] and (q["Q"] > p["Q"] or q["R"] > p["R"]))
        for q in pts
        if q is not p
    )


def frontier(candidates, seed=None):
    """The non-dominated (Q, R) candidates (the Pareto frontier). Include ``seed`` (Q,R) as an anchor point."""
    pts = list(candidates)
    if seed:
        pts = [*pts, {**seed, "round": "SEED", "key": "-", "prompt": None, "accept": None}]
    return sorted((p for p in pts if not _dominated(p, pts)), key=lambda p: (-p["Q"], -p["R"]))


def beats_seed(candidates, seed, tol=0.0):
    """Candidates that (single-val) look like a Pareto gain over the seed.

    >= on both, strictly > on one (within ``tol`` slack on the non-improving axis). Worth VALIDATING first:
    a real one means the noise-calibrated gate was too conservative.
    """
    sq, sr = seed["Q"], seed["R"]
    return [
        c
        for c in candidates
        if (c["Q"] >= sq - tol and c["R"] >= sr - tol and (c["Q"] > sq or c["R"] > sr))
    ]


def _scalar_dominates(a, b, tau=None):
    """Scalar (Q,R) domination with a noise margin.

    a dominates b iff a is not-worse than b within ``tau`` on BOTH axes and clearly-better (>= tau) on at
    least one. Keeps single-val noise from spuriously pruning.
    """
    tau = config.CFG.optimizer.tau_pareto if tau is None else tau
    not_worse = a["Q"] >= b["Q"] - tau and a["R"] >= b["R"] - tau
    clearly_better = (
        a["Q"] > b["Q"] + tau or a["R"] > b["R"] + tau
    )  # strict: keeps antisymmetry on the diagonal
    return not_worse and clearly_better


class ParetoArchive:
    """Live archive of non-dominated (vector, Q, R) points -- the frontier the MOO sweep builds.

    add() keeps a point unless the archive already scalar-dominates it, and prunes any archive point the
    newcomer dominates. select() returns the deploy point (max R subject to Q >= floor).
    """

    def __init__(self):
        """Empty archive (points accumulate as add() finds non-dominated frontier points)."""
        self.points = []  # each: {vec, Q, R, round, key, n}

    def dominated_by_archive(self, pt):
        """True iff some archive point scalar-dominates ``pt`` (so ``pt`` would not extend the frontier)."""
        return any(_scalar_dominates(p, pt) for p in self.points)

    def add(self, vec, q, r, rnd="", key="", prune=True):
        """Add a point. Returns True if a new point was appended (False = collision fold-in or dominated).

        COLLISION HANDLING: if an archive point already has the SAME prompt vector (identical language -- Opus is
        near-deterministic, so a re-proposal can repeat text, and compound edits can converge), it is the SAME
        solution, not a new frontier point. Fold the repeat in as another MEASUREMENT (running mean of Q,R),
        which reduces the single-val noise rather than bloating the frontier with a noise-duplicate.

        ``prune=True`` maintains a live non-dominated frontier (reject a dominated newcomer, prune what the
        newcomer dominates) -- the clean semantics used offline (tests, final validation). The MOO SWEEP passes
        ``prune=False`` to ACCUMULATE every point (dedup only): the capped-scan (Q,R) is too noisy (~0.08) for
        scalar domination at TAU_PARETO=0.02 to prune safely, so noise must never discard a genuine frontier
        point during the sweep. The authoritative deploy is chosen at the end from the PRECISE re-measure
        (skillopt_tuner.finalize_and_deploy: repeats-averaged full val + ood + feasibility).
        """
        for p in self.points:
            if p["vec"] == vec:
                n = p.get("n", 1) + 1
                p["Q"] = round((p["Q"] * (n - 1) + q) / n, 3)
                p["R"] = round((p["R"] * (n - 1) + r) / n, 3)
                p["n"] = n
                return False
        pt = {"vec": vec, "Q": q, "R": r, "round": rnd, "key": key, "n": 1}
        if prune:
            if self.dominated_by_archive(pt):
                return False
            self.points = [p for p in self.points if not _scalar_dominates(pt, p)]
        self.points.append(pt)
        return True

    def select(self, q_floor=None):
        """The deploy point: max R among points with Q >= q_floor (tie-break max Q). Falls back to max-R overall if nothing clears the floor."""
        q_floor = config.CFG.optimizer.pareto_q_floor if q_floor is None else q_floor
        if not self.points:
            return None
        eligible = [p for p in self.points if p["Q"] >= q_floor] or self.points
        return max(eligible, key=lambda p: (p["R"], p["Q"]))

    def frontier_points(self):
        """The archived (Q, R, round, key) points, sorted for reporting (no vectors)."""
        return sorted(
            ({"Q": p["Q"], "R": p["R"], "round": p["round"], "key": p["key"]} for p in self.points),
            key=lambda p: (-p["R"], -p["Q"]),
        )

    def save(self, path):
        """Persist the archive (points + q_floor) as JSON, atomically (crash-safe)."""
        config.atomic_write_json(
            path, {"points": self.points, "q_floor": config.CFG.optimizer.pareto_q_floor}
        )


def validate(
    candidates, split="val", repeats=config.CFG.optimizer.final_validate_repeats, cond="conditioned"
):
    """RE-MEASURE each candidate's (Q, R) ``repeats`` times independently (uses the 26B).

    Returns each with the original single-val point plus the re-measured mean/spread, so a TRUE Pareto point
    (stable) is distinguishable from noise (re-measure regresses to the seed).
    """
    from cascading_lms import llm
    from cascading_lms import retune as R
    from cascading_lms import skillopt_formal as SO

    llm.refresh_model()
    base = dict(R.P)
    results = []
    try:
        for c in candidates:
            qs, rs = [], []
            for _ in range(repeats):
                R.P.clear()
                R.P.update(base)
                R.P[c["key"]] = c["prompt"]
                sc = SO.score(dict(R.P), split, [cond])[cond]
                qs.append(sc["Q"])
                rs.append(sc["R"])
            results.append(
                {
                    "round": c["round"],
                    "key": c["key"],
                    "orig": {"Q": c["Q"], "R": c["R"]},
                    "remeasure_Q": qs,
                    "remeasure_R": rs,
                    "mean_Q": round(sum(qs) / len(qs), 3),
                    "mean_R": round(sum(rs) / len(rs), 3),
                }
            )
    finally:
        R.P.clear()
        R.P.update(base)
    return results


if __name__ == "__main__":
    import sys

    cands = load_candidates(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"candidates with a measured (Q,R): {len(cands)}")
    fr = frontier(cands)
    print(f"\nPARETO FRONTIER ({len(fr)} non-dominated points):")
    for p in fr:
        print(f"  Q={p['Q']:.3f} R={p['R']:.3f}  {p['key']:14} accept={p['accept']}  {p['round']}")
