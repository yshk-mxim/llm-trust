# Datasheet — Language Circuit injection datasets

Datasets for evaluating multi-channel prompt-injection defenses on a single frozen LLM. Follows the
"Datasheets for Datasets" structure. Full construction detail: `METHODOLOGY.md`.

## Motivation
To measure, and to enable others to measure, how well a defense *reduces* (not eliminates) the
impact of prompt injection across (a) attack FORM, (b) trust CHANNEL, and (c) in-distribution vs
out-of-distribution generalization — under a single frozen model, no agentic tools.

## Composition
- **Tiered review sets** (`review_{2,3,4}tier.md` + keys): blind, human-rateable items. Trust
  SYSTEM > USER > CONTENT > WEB. 2-tier injects in CONTENT; 3-tier in USER/CONTENT (incl.
  user->content delegation); 4-tier in USER/CONTENT/WEB and combinations, incl. the multi-hop
  delegation (user asks the model to follow a web page that carries the payload). Each item labels
  which channel holds the injection and the exact "obeyed" criterion; keys give the hidden condition
  (base / base+prompt / wrapper / passivation / both).
- **Attack generators** (`selfopt.py`, `make_reviews_v3.py`): compose a FORM x PAYLOAD over content.
  Forms: in-distribution (instruction-in-content, false-content, redirection, persona);
  out-of-distribution, novel in STRUCTURE (base64, non-English, code-fenced, payload-split,
  conditional-trigger, table-field); reused AgentDojo templates (important-instructions,
  ignore-previous, TODO, system-message, IMPORTANT!!!). Payloads use neutral lowercase tokens.
- **Content substrate**: v6e instruction/content pairs (diverse ops), filtered to feasible text
  tasks (40-400 chars).

## Collection & preprocessing
Content is reused from the v6e suite; injections are generated programmatically. Neutral lowercase
tokens and natural-prose embedding (no bracket fencing, no ALL-CAPS markers) avoid tipping model
defenses. No human subjects; no PII (the "PHI/PII" records are synthetic).

## Recommended uses
Benchmarking injection defenses by attack form, channel, and OOD generalization; the OOD split is
held out from any prompt/skill tuning to detect memorization. Report per-class attenuation and the
residual leak per condition; do not tune to force any class to zero.

## Limitations
Single model family and language focus; small per-condition human-rated n (use for judge
concordance, scale per-condition numbers with the validated automated judge). Text-only: tool/exfil
actions are out of scope by design.

## Distribution & maintenance
Released with the code. Regenerable and contamination-resistant: fresh instances via new
tokens/contents/seeds. Versioned in git alongside `METHODOLOGY.md`.
