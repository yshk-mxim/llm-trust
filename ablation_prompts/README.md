# Ablation-baseline tuned prompts (development stage)

`ablation_baseline_prompts.json` preserves the tuned prompts for the **ablation baselines** so the
development-set ablation (the paper's `tab:tiers`) is inspectable and live-reproducible, not only
regrade-reproducible.

## Why they are here and not in `data/prompts.json`

`data/prompts.json` is the **deployed CONDITIONED cascade** — the system the paper deploys and that
`make eval` scores for the held-out table (`tab:main`, base vs conditioned). By design it carries only
the conditioned keys (`pass_ctx_*`, `wrapper_ctx`); the ablation-baseline keys are **empty** in it,
because the deployed cascade never reads them. This file supplies exactly those missing keys.

## Contents (the keys empty in the deployed vector)

| key | condition | chars | what it is |
| --- | --- | --- | --- |
| `defense`   | base+prompt  | 734  | the tuned defensive **system prompt** (delivered in the chat `system` role) |
| `wrapper`   | wrapper      | 450  | the wrapper condition's tuned trust-order prompt |
| `pass_USER` | passivation  | 441  | the passivation condition's tuned `USER`-channel prompt |
| `composite` | composite    | 1708 | the single-prompt composite condition |

## Provenance and caveats (read before using)

- These are the values behind `tab:tiers` (base+prompt genuinely defends: **0/25/33%** obeyed vs base
  **67/67/92%**). They were verified to be delivered as real chat **system** prompts, not concatenated
  into the user turn.
- This is a **development-stage** snapshot. The *conditioned* keys from that stage differ from the final
  deployed `data/prompts.json`; only the four ablation keys above are provided here, so this file **cannot**
  be mistaken for, or used as, the deploy. Merge it over the live vector to re-run an ablation; do not
  replace the deploy with it.
- The **authoritative** reproduction of `tab:tiers` is `make regrade-tier` — a deterministic genuine-leak
  regrade of the committed development dump in `runs/`, which needs neither the served model nor these
  prompts. This directory is for transparency/inspection and for anyone re-running an ablation baseline live.
- The full tuning history (19 successive vectors) lives in the paper's monorepo, not in this artifact.

## Re-running an ablation baseline live (optional)

Merge these keys into the live prompt vector before scoring the ablation condition, e.g.:

```python
import json
from cascading_lms import retune as R, final_eval as F
R.P.update(json.load(open("ablation_prompts/ablation_baseline_prompts.json")))  # restore ablation prompts
# then score, e.g., FINAL_EVAL_CONDS=base+prompt python -m cascading_lms.final_eval
```

`final_eval` otherwise prints a notice when it scores an ablation whose prompt is empty in the loaded vector.
