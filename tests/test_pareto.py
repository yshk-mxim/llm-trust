# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Pareto-archive (MOO tuner) invariants -- checked FORWARD and REVERSE.

The domination predicate must be a strict partial order: never a>a (irreflexive) and never both a>b AND b>a
(antisymmetric). The audit caught a real antisymmetry bug (both bounds inclusive made diagonal-tau points
mutually dominate, silently dropping a tradeoff); these tests prove it stays fixed and that the archive keeps
a genuinely non-dominated frontier.
"""

import itertools
import random

from cascading_lms import config, pareto


def _grid(lo=0.70, hi=1.00, step=0.01):
    vals = [round(lo + step * i, 3) for i in range(int((hi - lo) / step) + 1)]
    return [{"Q": q, "R": r} for q in vals for r in vals]


def test_scalar_dominates_is_irreflexive():
    """No point dominates itself (forward = reverse = self)."""
    assert not any(pareto._scalar_dominates(a, a) for a in _grid())


def test_scalar_dominates_is_antisymmetric_forward_and_reverse():
    """Over a grid that hits the +/-tau boundary exactly, never a>b AND b>a (the audit's mutual-domination bug)."""
    grid = _grid()
    assert config.CFG.optimizer.tau_pareto > 0
    for a, b in itertools.combinations(grid, 2):
        assert not (pareto._scalar_dominates(a, b) and pareto._scalar_dominates(b, a)), (a, b)


def test_archive_stays_non_dominated_frontier():
    """After random adds (distinct vectors), no archived point dominates another -- checked both directions."""
    random.seed(11)
    for _ in range(100):
        ar = pareto.ParetoArchive()
        for k in range(15):
            ar.add(
                {"v": k},
                round(random.uniform(0.6, 1.0), 3),
                round(random.uniform(0.6, 1.0), 3),
                rnd=str(k),
            )
        for a, b in itertools.permutations(ar.points, 2):
            assert not pareto._scalar_dominates(a, b), (a, b)


def test_archive_collision_folds_into_running_mean():
    """A repeated vector is NOT a new point -- it updates the existing point's running-mean (Q,R), reducing noise."""
    ar = pareto.ParetoArchive()
    assert ar.add({"w": "same"}, 0.84, 0.87) is True
    assert ar.add({"w": "same"}, 0.86, 0.85) is False  # collision -> fold in
    assert len(ar.points) == 1
    p = ar.points[0]
    assert p["n"] == 2 and p["Q"] == 0.85 and p["R"] == 0.86


def test_archive_keeps_tradeoff_rejects_dominated_prunes_on_improvement():
    """A tradeoff is kept; a dominated point is rejected; an improvement prunes what it dominates."""
    ar = pareto.ParetoArchive()
    ar.add({"v": 0}, 0.84, 0.87, rnd="seed")
    assert (
        ar.add({"v": 1}, 0.72, 0.95, rnd="tradeoff") is True
    )  # non-dominated (R up, Q down) -> kept
    assert ar.add({"v": 2}, 0.70, 0.80, rnd="dominated") is False  # worse on both -> rejected
    assert ar.add({"v": 3}, 0.90, 0.90, rnd="improve") is True  # dominates seed -> prunes it
    rounds = {p["round"] for p in ar.points}
    assert rounds == {"tradeoff", "improve"}  # seed pruned, tradeoff + improvement remain


def test_select_is_max_r_subject_to_q_floor():
    """Deploy = max R among points with Q >= floor; empty archive is safe."""
    ar = pareto.ParetoArchive()
    assert ar.select() is None
    ar.add({"v": 0}, 0.90, 0.85, rnd="a")  # above floor
    ar.add({"v": 1}, 0.72, 0.97, rnd="b")  # below 0.80 floor, higher R
    ar.add({"v": 2}, 0.83, 0.90, rnd="c")  # above floor, higher R than a
    assert ar.select(0.80)["round"] == "c"  # max R among Q>=0.80 (not b, which is below floor)
