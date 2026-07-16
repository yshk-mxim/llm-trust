# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
import json, sys
sys.path.insert(0, "src")
import final_eval as F, wiki_corpus
F.install_deploy("runs/seed_robustness/result_cold.json")
res = wiki_corpus.score_conditions()
json.dump(res, open("runs/wiki_ood_results.json", "w"), indent=2)
print("CORRECTED WIKI LENGTH-OOD (full-content grounding):", flush=True)
for cond, m in res.items():
    print(f"  {cond:12} Q_long={m['Q_long']} R_token_long={m['R_token_long']} poison_long={m['poison_long']}", flush=True)
print("DONE", flush=True)
