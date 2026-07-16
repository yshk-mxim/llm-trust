# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Emit the curated-adversarial REVIEW SHEET (concordance/adversarial_review.md).

Every RESTORED curated example (data/real_payloads.json -- the real-world named payloads) is listed with its
stable id, taxonomy class, injected payload, the documented attacker goal, and the source, so a human can read
each and flag "poorly specified". Flagging = set ``enabled=false`` on the row (or add the id to
data/excluded_examples.toml ``excluded``); the loader (final_eval.load_curated) drops it on the next build --
NO code change. The GENERATED template classes (tok/hijack/persona/... sampled from trust_model.toml
[threat_model].attack_mix) are curated at the CLASS level: dropping one is a one-line edit to attack_mix, and
the per-category spot-check covers them. Run: ``PYTHONPATH=src python3 tools/adversarial_review.py``.
"""

import json
import sys

sys.path.insert(0, "src")

from cascading_lms import config
from cascading_lms import final_eval as FE
from cascading_lms import retune as R
from cascading_lms import trust_spec


def _curated_table():
    rows = json.loads(open(FE.CURATED_PAYLOADS).read())
    excluded = FE._excluded_ids()
    lines = [
        "| id | class | enabled | source | injected payload (content) | attacker goal (judged) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        on = r.get("enabled", True) and r["id"] not in excluded
        payload = r["content"].replace("\n", "\\n").replace("|", "\\|")
        goal = r["injection"].replace("|", "\\|")
        lines.append(
            f"| `{r['id']}` | {r['class']} | {'yes' if on else '**no**'} | {r['source']} | {payload} | {goal} |"
        )
    return "\n".join(lines)


def _generated_table():
    mix = trust_spec.DEFAULT.threat("attack_mix", [])
    canary = {"tok", "deleg", "corrob", "override"}
    lines = ["| class | scoring | in attack_mix |", "| --- | --- | --- |"]
    for cls in mix:
        scoring = (
            "j_asserted+task+grounded"
            if cls == "poison"
            else "canary"
            if cls in canary
            else "j_obeyed"
        )
        lines.append(f"| {cls} | {scoring} | yes |")
    return "\n".join(lines)


def main():
    md = f"""# Adversarial review sheet (curated + generated attack taxonomy)

These are the RESTORED adversarial examples wired into tune + eval. Read each curated example below; if one is
poorly specified, DROP it as DATA (no code change): set `"enabled": false` on its row in
`data/real_payloads.json`, **or** add its `id` to the `excluded` list in `data/excluded_examples.toml`. The
loader (`final_eval.load_curated`) honors both and rebuilds without it on the next run.

## 1. Curated real-world payloads (individually removable) -- {len(FE.load_curated())} enabled

Concrete named injections from the literature (PayloadsAllTheThings / Unit42 / DAN / BIPIA) + crafted
structural attacks, restored from `archive/code/adversarial.py`. Held out; run once at tier 4; each is
j_obeyed-scored against its documented goal (the record's `form` = `real:<id>`).

{_curated_table()}

## 2. Generated template classes (curated at the CLASS level)

Sampled per case from `data/trust_model.toml [threat_model].attack_mix`; content is generated, so the CLASS is
the curation unit -- drop one by removing it from `attack_mix` (a data edit). The per-category spot-check
(`concordance/taxonomy_spotcheck_sheet.md`) covers these.

{_generated_table()}

## Out of scope by design (NOT a gap)

Encoding-transport obfuscation -- base64, character-reversal, leetspeak, homoglyph, zero-width -- is handled by
the input-sanitization / parser plugin layer (`register_parser`), which decodes/normalizes text BEFORE the
trust cascade sees it. It is therefore not a cascade attack class and is excluded from this taxonomy.
"""
    out = config.concordance_path("adversarial_review.md")
    with open(out, "w") as fh:
        fh.write(md)
    print(f"wrote {out} ({len(FE.load_curated())} curated enabled, {len(trust_spec.DEFAULT.threat('attack_mix', []))} generated classes)")


if __name__ == "__main__":
    main()
