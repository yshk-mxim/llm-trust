# Composable Trust for Language Models

Reference implementation for the paper *Composable Trust for Language Models*. A single frozen language model (`gemma-4-26B-A4B`, served OpenAI-compatible at
`localhost:9000`) is wrapped in a deterministic pipeline that enforces a Biba integrity lattice
`SYSTEM > USER > CONTENT > WEB` over its inputs. Each input takes the trust level -- its **ring** --
of the channel it arrived on, never inferred from content. A proved **authority monitor** holds all
authority: it binds the operation to the highest ring and gates outside actions on the trusted rings
alone, so no low-trust input can change the task or trigger an action (`harness.py`,
`prove_authority.py`). The same model, holding zero authority, then runs two attenuation stages over
untrusted content -- **passivation** (rewrite content so embedded instructions become inert) and a
nonce-delimited **wrapper** (ring tags that state the trust order). The passivation/wrapper prompt
text is tuned by an Opus-4.8 optimizer and every semantic verdict is graded by an Opus-4.8 judge; the
served 26B is the defended model only, never a judge of itself.

The single hard guarantee is authority non-interference, proved by exhaustion over the finite
lattice (`make proof`, offline). The residual obey/poison rates the cascade does not eliminate are
**attenuation** -- empirical numbers, not guarantees.

## Requirements

- **Python >= 3.12.** The runtime has **no third-party dependencies**: all HTTP (to the 26B and to
  the Anthropic API) goes through the standard-library `urllib`. The offline gate needs the dev
  tools in `requirements-dev.txt` (`ruff`, `pytest`, `mypy`, `hypothesis`, `mutmut`; `PyYAML` only
  to read a user-supplied `.yaml` spec, `tomli_w` only to regenerate the `.toml` data files).

  ```bash
  pip install -r requirements-dev.txt   # for `make check`; the runtime itself needs nothing
  ```

- **The defended 26B**, for any experiment (tune/eval/latency/conflict): `gemma-4-26B-A4B`
  (`mlx-community/gemma-4-26B-A4B-it-qat-8bit`) served OpenAI-compatible at
  `http://localhost:9000/v1/chat/completions`. The model id and endpoint are declared in
  `data/trust_model.toml` (`[models.defended]`) and read via `config.py`.

- **The Opus-4.8 API key**, for any *judged* evaluation (the tuner and every semantic judge use
  `claude-opus-4-8`). The shared client (`anthropic_api.py`) loads it at import from
  `~/semantic/env.json`, field `claude_api_key`:

  ```json
  { "claude_api_key": "sk-ant-..." }
  ```

  The path and field are set in `config.py` (`JUDGE_ENV_JSON`, `JUDGE_API_KEY_FIELD`).

Neither the 26B nor the key is needed for the proof or the unit gate.

## Quickstart

Everything offline first -- no GPU, no served model, no API key:

```bash
make check    # OFFLINE publish gate: ruff lint + format-check + pytest
make proof    # exhaustive authority non-interference proof -> data/authority_proof.json
```

`make proof` enumerates every input-ring configuration over the finite lattice (default: 8729
configurations) and checks the monitor's invariants; it writes a certificate
(`data/authority_proof.json`, `violations: 0`, `status: PROVED`). This is the paper's central claim
and needs nothing but Python.

Then the experiments (each needs the served 26B, and the judged ones also need the Opus key):

```bash
make moo      # multi-objective (Pareto) tuning of the conditioned cascade   [26B + Opus key]
make eval     # final held-out evaluation with the locked prompt vector      [26B + Opus key]
make regrade  # static genuine-leak re-grade (Opus reasoning judge)          [Opus key; --wiki also 26B]
```

## Config-driven TrustModel

The lattice is not hard-coded in the pipeline logic; it is a declarative spec in
`data/trust_model.toml`, loaded and validated by `trust_spec.py`. The spec declares:

- **rings** -- `name`, `integrity` (the lattice integer), `plane` (`control` / `request` / `data`),
  `active`, per-ring `prompt_key` / `goal` / `must_not`. The default has four active rings
  (`SYSTEM > USER > CONTENT > WEB`); `SYSTEM_MEMORY`, `RAG`, `UNTRUSTED` are inactive extensibility
  stubs.
- **actions** -- `action_min`, the ring an effect must reach to fire (default `USER`).
- **tiers** -- the trust-depth curriculum (tier 2 = CONTENT, 3 = +USER, 4 = +WEB).
- **policy** -- input/output guard order and `enforce_output` (default `false`: the monitor-out
  verdict is advisory, so raw output is byte-identical).
- **tuning** -- `mode` (`multivariate` / `coordinate`) and `q_floor`.
- **threat_model** -- the injection-class mix for training and held-out eval.
- **models** -- the judge, defended, and (redteam-only) attacker model ids/endpoints.
- **judges** / **metrics** -- optional per-judge criteria and Q/R composition overrides.

`trust_spec.py` validates the spec's ring integers against the `harness.Ring` enum, so the
*specified* lattice and the *proved* lattice can never silently diverge. The whole generation plane
(passivation prompt-keys, plane predicates, trust-order string, tiers) is derived from this object.
Point the pipeline at a different lattice by setting `TRUST_MODEL_SPEC=<path>` before import;
overlay alternate prompt text with `PROMPT_PACK=<path>`. `make docs` regenerates `docs/trust_model.md`
from the live spec. On the default spec every derivation is byte-identical to the committed deploy.

## Repository layout

```
src/cascading_lms/       the package (stdlib only)
  harness.py             authority core: Ring lattice, Labeled values, ingest/derive/resolve_op,
                         endorse, authorize_action; invariants I1-I5. No model calls.
  prove_authority.py     exhaustive non-interference proof over the finite lattice (make proof).
  monitor.py             monitor-OUT: per-trust-portion output verdict (obeyed_only_system /
                         grounded / relevant); advisory by default, fail-closed gate optional.
  passifier.py, llm.py   the only calls to the frozen 26B (zero authority): passivate/perform + client.
  retune.py              the eight conditions incl. the deployed `conditioned` cascade
                         (pass_ctx_USER, pass_ctx_data, wrapper_ctx); dataset splits.
  skillopt_formal.py     noise-calibrated, constrained multi-objective acceptance (Q, R).
  skillopt_tuner.py      the search policy: Opus proposes one bounded prompt edit per round (make tune).
  moo_run.py             the committed Pareto-archive tuning run + finalize_and_deploy (make moo).
  stage_check.py         univariate in->out stage contracts (feasibility constraint).
  final_eval.py          final held-out evaluation with the locked vector -- the main table (make eval).
  trust_conflict.py      positive test of the ordering: on contradicting cross-ring facts, which ring wins.
  trust_spec.py, config.py   the config-driven TrustModel spec + the typed hyperparameter object.
  judges.py, anthropic_api.py   the Opus-4.8 semantic judges + the shared API client.
  build_data.py, suite.py, wiki_corpus.py, agentdojo.py   data construction + external suites.
  policy.py, ring_spec.py, metrics.py, ...   plugins, ring helpers, metric helpers.
data/                    spec + prompts + inputs/caches (see below).
tests/                   offline unit + property tests (pytest; no model, no API).
runs/                    reproducibility scripts + saved run outputs (logs, dumps, grades).
docs/                    generated trust-model doc and notes.
```

### `data/`

- `trust_model.toml` -- the TrustModel spec (the lattice; see above). `trust_model_example2.toml`
  is an alternate-lattice example for the portability test.
- `config.toml` -- the tunable hyperparameters (seeds, sizes, budgets, windows), parsed into the
  frozen `config.CFG` object.
- `prompts.json` -- the **deployed prompt vector** (the tuned **conditioned** cascade) the pipeline reads
  on every call. It carries the conditioned keys (`pass_ctx_*`, `wrapper_ctx`); the ablation-baseline keys
  (`defense` for base+prompt, `wrapper`, `pass_USER`, ...) are **empty here by design** -- the deployed
  cascade never reads them. Those baselines (the paper's dev-set ablation, `tab:tiers`) are reproduced by `make regrade-tier`
  (a deterministic regrade of the committed development dump), **not** by re-scoring them live against this
  vector -- a live score of an ablation condition would look like `base` and `final_eval` prints a notice
  saying so. The tuned ablation prompts themselves are preserved in `ablation_prompts/` (with a README
  disclosing their provenance) for inspection and optional live reproduction.
- `prompts_default.toml` -- the cold-start seed prompt pack (all default prompt/rule/judge text as
  data; the optimizer's starting point).
- `skill_pass_CONTENT.md`, `skill_pass_USER.md`, `skill_pass_WEB.md` -- the deployed per-ring
  passivation skill text (the instruction each untrusted ring's passivation runs; loaded by `retune.py`).
- `trust_conflict_cases.toml` -- the conflict-attribute pool for the ordering test.
- `data_perf.json`, `data_poison.json` -- Opus-authored, content-keyed caches (a performability
  screen and the plausible-but-false claims); reruns are instant and provenance is a committed file.
- `poison_conflict_cache.json`, `poison_conflict_wiki_long.json`, `wiki_corpus.toml`,
  `wiki_*_cache.json` -- conflict-poison and Wikipedia length-OOD inputs (`wiki_corpus.toml` is the
  recipe: article slugs plus planted false claims; the caches hold the fetched articles).
- `v6e_combined_v2.jsonl` -- the synthetic source corpus.
- `action_injections.json`, `real_payloads.json`, `data_web_additional.json` -- attack payloads.
- `judge_cases_combined.toml` -- known-answer judge-validation cases (the judge-metric concordance pool).
- `excluded_examples.toml` -- a curated exclusion list; drops an example from tune and eval as a data
  edit, with no code change.
- `authority_proof.json` -- the certificate written by `make proof`.

The **test** split is never used for tuning.

## Reproducing the paper's numbers

Targets and scripts, and what each needs:

| result | command | needs 26B | needs Opus key |
| --- | --- | :---: | :---: |
| Authority non-interference (the proof) | `make proof` | no | no |
| Offline gate (lint + format + unit tests) | `make check` | no | no |
| Tuning: conditioned cascade (Pareto) | `make moo` | yes | yes |
| Final held-out eval (main table) | `make eval` | yes | yes |
| Genuine-leak re-grade (static) | `make regrade` | `--wiki` only | yes |
| Genuine-leak re-grade (adaptive red-team) | `make regrade-adaptive` | no | yes |
| Genuine-leak grader concordance | `make regrade-concordance` | no | yes |
| tab:tiers token-injection rows | `PYTHONPATH=src python runs/regrade_tier.py` | no | yes |
| Conflict-poison run + grade | `runs/poison_conflict_run.py`, then `runs/poison_conflict_grade.py` | run: yes | grade: yes |
| Trust-conflict (positive ordering test) | `PYTHONPATH=src python -m cascading_lms.trust_conflict` | yes | residue judge: yes |
| Latency (inference cost, Sec. 2) | `PYTHONPATH=src python runs/latency_remeasure.py [K]` | yes | no |

The static regrade/latency scripts grade or time already-saved outputs and so do not need the 26B
(except where noted). Run latency alone -- a contended 26B makes the timings useless. Judged
scripts load the Opus key from `~/semantic/env.json` (as above).

## Caveats

- **The proof is the only hard guarantee.** Authority non-interference is a property of the monitor
  code, enumerated exhaustively over the finite lattice and independent of model quality: no
  low-ring channel can change the operation or cause an action. A jailbroken generator is a quality
  failure, never an authority breach.
- **The soft plane is tuned prompt text.** Passivation and the wrapper are prompt strings in
  `data/prompts.json`; the obey/poison rates they achieve are empirical attenuation, not guarantees,
  and depend on the specific 26B and the tuned vector.
- **Judged numbers are not offline-reproducible.** Every semantic result (the main table, the
  regrades, concordance) needs the paid Opus-4.8 API key *and* the privately served 26B. The
  trust-conflict and latency numbers need the 26B but no key (trust-conflict's attribution-fair
  residue judge does use the key). Only the proof and the unit gate need neither.

## Citation

If you use this code or the paper's ideas, please cite:

```bibtex
@article{shkolnikov2026composable,
  title         = {Composable Trust for Language Models: A proven boundary and a measured defense},
  author        = {Shkolnikov, Yakov Pyotr},
  year          = {2026},
  eprint        = {2607.13149},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CR},
  doi           = {10.48550/arXiv.2607.13149},
  url           = {https://arxiv.org/abs/2607.13149}
}
```

Paper: <https://arxiv.org/abs/2607.13149> (arXiv:2607.13149 [cs.CR]).

## License

MIT -- see [LICENSE](LICENSE).
- **Q and R depend on the Opus judge.** The judge concordance is validated (blind sheet + key +
  test-retest; `make regrade-concordance`), but the numbers are only as good as that instrument. The
  defended 26B never grades itself.
