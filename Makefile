# Publication-quality gate for the cascading-LMs artifact.
# `make check` is the offline gate (no model, no API): lint + format + unit tests. It is what CI runs and
# what must be green before publishing. The experiment targets (tune/eval) need the served 26B and the
# Opus judge API and are therefore separate from the gate.

PY := python3
VERSION := $(shell grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')

.PHONY: check lint format test types tune moo eval regrade regrade-adaptive regrade-concordance proof clean docs dist idiom mutation mutation-full smoke trace wiring

check: clean lint format test    ## offline publish gate: lint + format-check + unit tests (clean first: stale .pyc after a git checkout can carry old Ring values and spuriously fail the spec==enum assert)

lint:                            ## ruff static checks (idiom, bugs, complexity, docstrings)
	ruff check src tests

idiom:                           ## focused idiom review (the enforced idiom rules, isolated)
	ruff check src tests --select RET,PIE,FLY,SIM,C4,PERF,B,RUF

mutation:                        ## fast deterministic mutation gate -- a surviving mutant = a vacuous test
	$(PY) tools/mutation_sanity.py

mutation-full:                   ## exhaustive mutmut sweep (slow; `pip install -r requirements-dev.txt`)
	mutmut run

format:                          ## verify formatting without rewriting
	ruff format --check src tests

test:                            ## unit tests (offline; no model or API)
	$(PY) -m pytest

types:                           ## advisory type check (not a gate yet)
	-mypy

proof:                           ## machine-check authority non-interference over the finite lattice
	PYTHONPATH=src $(PY) -m cascading_lms.prove_authority

tune:                            ## re-tune the prompt vector on the served 26B (Opus tuner + judge)
	PYTHONPATH=src $(PY) -m cascading_lms.skillopt_tuner

moo:                             ## multi-objective (Pareto) tuning run -- JOINT for the conditioned cascade
	PYTHONPATH=src $(PY) -m cascading_lms.moo_run

eval:                            ## final held-out evaluation with the locked vector
	PYTHONPATH=src $(PY) -m cascading_lms.final_eval

regrade:                         ## static genuine-leak re-grade: Opus reasoning judge + per-case audit trace (Opus API; --wiki also needs the 26B). Writes runs/regrade_genuine_leak{,_summary}.json*
	PYTHONPATH=src $(PY) runs/regrade_genuine_leak.py --wiki

regrade-adaptive:                ## same reasoning grader applied to the adaptive red-team run (consistent instrument). Writes runs/regrade_adaptive_*
	PYTHONPATH=src $(PY) runs/regrade_adaptive_genuine.py

regrade-concordance:             ## concordance for the genuine-leak grader: blind sheet + key + test-retest (validates it like j_obeyed's kappa). Writes concordance/regrade_concordance_*
	PYTHONPATH=src $(PY) runs/regrade_concordance.py

smoke:                           ## live tier-4 smoke (6 cases): Q/R/Q_relative + trust verdicts + passes fired
	$(PY) tools/trace.py --smoke 6

trace:                           ## full per-stage forward trace of tier-4 cases -> runs/trace_<ts>.jsonl
	$(PY) tools/trace.py --trace 6

wiring:                          ## static wiring/coverage map (no 26B): is every component reached?
	$(PY) tools/trace.py --wiring

docs:                            ## regenerate the trust-model doc from the spec (self-documenting)
	PYTHONPATH=src $(PY) -m cascading_lms.trust_spec > docs/trust_model.md

dist: check                      ## export ONLY git-tracked files (the publishable copy) to dist/: git decides what ships, not the filesystem, so caches/backups never leak. Archives committed HEAD -- commit first.
	@mkdir -p dist; \
	 top=$$(git rev-parse --show-toplevel); pfx=$$(git rev-parse --show-prefix); pfx=$${pfx%/}; \
	 [ -n "$$pfx" ] && ref="HEAD:$$pfx" || ref="HEAD"; \
	 git -C "$$top" archive --format=tar --prefix=cascading-lms-$(VERSION)/ "$$ref" | gzip > dist/cascading-lms-$(VERSION).tar.gz; \
	 echo "wrote dist/cascading-lms-$(VERSION).tar.gz ($$(git ls-files . | grep -c .) tracked files; caches/backups excluded by construction)"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
