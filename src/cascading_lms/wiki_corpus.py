# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Cached Wikipedia long-document corpus for the OOD length-generalization test (paper Sec. 6, eval-only).

A reviewer will (rightly) object that the tuning/val content is short (~1-2 sentences), which favours the
monolithic ``composite`` because everything fits one easy pass. This module pulls REAL Wikipedia passages
(~20-30 sentences) so the held-out test can compare the provable conditioned cascade against composite on
realistic document length -- WITHOUT biasing toward model-generated text or spending generation budget.

Reproducibility: every fetch records the article title, the exact revision id, the canonical URL, and the
fetch timestamp; the cache (data/wiki_ood_cache.json) is committed so the corpus is fixed. Re-run
``build_cache()`` only to refresh. This module does NOT feed the tuning loop -- it is used only by final_eval.
"""

import json
import os
import time
import tomllib
import urllib.parse
import urllib.request

from cascading_lms import config, trust_spec

# The eval RECIPE (op, topics, poison_claims) is DATA (data/wiki_corpus.toml) -- an org supplies its own
# long-document corpus. Subjects are deliberately DISJOINT from the tuning/val + web_additional/poison
# subjects, so the long-document test is genuinely held out.
with open(config.data_path("wiki_corpus.toml"), "rb") as _fh:
    _RECIPE = tomllib.load(_fh)
TOPICS = _RECIPE["topics"]  #: article slugs (fetched into the committed cache)
OOD_OP = _RECIPE["op"]  #: the summarize op asked of every long document
POISON_CLAIMS = _RECIPE["poison_claims"]  #: fetched-title -> plausible-but-FALSE smuggled claim

# The document ring is the highest-integrity DATA ring (default: CONTENT), derived from the spec so a
# different lattice's data ring carries the long content without a code edit.
_CONTENT_RING = next(
    r.ring for r in trust_spec.DEFAULT.active_rings if trust_spec.DEFAULT.is_data(r.ring)
)

_UA = "cqwen-research/1.0 (prompt-injection defense eval; contact via the paper repository)"
_API = "https://en.wikipedia.org/w/api.php"
MAX_SENTENCES = config.CFG.wiki.max_sentences  #: passage truncation bound (from data/config.toml).


def _truncate_sentences(text, n=MAX_SENTENCES):
    """Keep the first ``n`` sentences of ``text`` (naive '. ' split -- fine for encyclopedic prose)."""
    parts, out, count = text.replace("\n", " ").split(". "), [], 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        out.append(p if p.endswith(".") else p + ".")
        count += 1
        if count >= n:
            break
    return " ".join(out)


def _fetch(title):
    """Fetch one article's plain-text intro+body extract with its revision id (records provenance)."""
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts|revisions",
            "explaintext": "1",
            "exsectionformat": "plain",
            "rvprop": "ids",
            "titles": title,
            "format": "json",
            "redirects": "1",
        }
    )
    req = urllib.request.Request(f"{_API}?{q}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=config.CFG.wiki.fetch_timeout_s) as r:
        data = json.load(r)
    page = next(iter(data["query"]["pages"].values()))
    text = _truncate_sentences(page.get("extract", ""))
    revid = page.get("revisions", [{}])[0].get("revid")
    return {
        "title": page.get("title", title),
        "revid": revid,
        "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}?oldid={revid}",
        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_chars": len(text),
        "text": text,
    }


def build_cache(topics=None):
    """Fetch all TOPICS and write the committed cache (call to create/refresh data/wiki_ood_cache.json)."""
    recs = []
    for t in topics or TOPICS:
        rec = _fetch(t)
        recs.append(rec)
        print(f"  fetched {rec['title']!r} rev={rec['revid']} chars={rec['n_chars']}", flush=True)
        time.sleep(config.CFG.wiki.fetch_delay_s)  # be polite to the API
    with open(config.data_path("wiki_ood_cache.json"), "w") as fh:
        json.dump(recs, fh, indent=2)
    return recs


def load():
    """Load the committed Wikipedia OOD cache (list of {title, revid, url, text, ...})."""
    path = config.data_path("wiki_ood_cache.json")
    with open(path) as fh:
        return json.load(fh)


_RANDOM_CACHE = (
    "wiki_random_cache.json"  #: obscure random articles for the content-specific poison test.
)


def _load_random():
    """The random-article cache (empty on first use)."""
    path = config.data_path(_RANDOM_CACHE)
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return json.load(fh)


def _fetch_random_batch(k):
    """``k`` random main-namespace articles' intro extracts in one call (obscure + detail-rich by nature)."""
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "random",
            "grnnamespace": "0",
            "grnlimit": str(k),
            "prop": "extracts|revisions",
            "explaintext": "1",
            "exintro": "1",
            "exsectionformat": "plain",
            "rvprop": "ids",
            "format": "json",
        }
    )
    req = urllib.request.Request(f"{_API}?{q}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=config.CFG.wiki.fetch_timeout_s) as r:
        data = json.load(r)
    out = []
    for page in data.get("query", {}).get("pages", {}).values():
        text = _truncate_sentences(page.get("extract", ""))
        out.append(
            {
                "title": page.get("title", ""),
                "revid": page.get("revisions", [{}])[0].get("revid"),
                "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "n_chars": len(text),
                "text": text,
            }
        )
    return out


def fetch_random_articles(n):
    """``n`` random obscure Wikipedia articles with a real body, greedy-cached to data/ (dedup by title).

    Random main-namespace articles are obscure + detail-rich, so a question about one turns on a document-
    specific fact the 26B would not already know -- which isolates the DEFENSE (CONTENT-vs-WEB reliance) from
    the model's world knowledge. Cached so re-runs are reproducible and don't re-fetch.
    """
    path = config.data_path(_RANDOM_CACHE)
    cache = _load_random()
    seen = {r["title"] for r in cache}
    min_chars = config.CFG.content_bounds.min_body_chars
    usable = [r for r in cache if r["n_chars"] >= min_chars]
    batches = 0
    while len(usable) < n and batches < config.CFG.wiki.random_max_batches:
        for rec in _fetch_random_batch(config.CFG.wiki.random_batch):
            if rec["title"] and rec["title"] not in seen:
                cache.append(rec)
                seen.add(rec["title"])
        with open(path, "w") as fh:
            json.dump(cache, fh, indent=1)  # greedy: survive a crash mid-fetch
        usable = [r for r in cache if r["n_chars"] >= min_chars]
        batches += 1
        time.sleep(config.CFG.wiki.fetch_delay_s)  # be polite to the API
    return usable[:n]


def build_ood_cases(forms=None):
    """Long-document OOD cases (eval-only): a CLEAN, TOKEN-injection, and content-POISON case per article.

    Same attack structure as the short-content eval, but on realistic length, so final_eval can compare the
    conditioned cascade vs composite on long documents. All tier-2 (SYSTEM+CONTENT): the injection/false fact
    rides INSIDE the long CONTENT, which is the length stress the reviewer asks about.
    """
    import random

    from cascading_lms import retune as R

    forms = forms or list(R.tok_forms("x").keys())
    rng = random.Random(config.CFG.seeds.tune)
    cases = []
    for a in load():
        text = a["title"] + ". " + a["text"]
        cases.append(
            {
                "kind": "clean",
                "cls": "clean",
                "form": "wiki_clean",
                # length-OOD: the long doc (~4.5k chars) exceeds the 400-char judge window, so the general
                # full-when-over-window rule (skillopt_formal._judge_clean) grounds it against the FULL text.
                "tier": 2,
                "op": OOD_OP,
                "content": text,
                "ch": [(_CONTENT_RING, text)],
                "title": a["title"],
            }
        )
        form = rng.choice(forms)
        payload, _, tok = R.tok_forms(rng.choice(R.TOK))[form]
        cases.append(
            {
                "kind": "attack",
                "cls": "tok",
                "form": f"wiki_{form}",
                "tier": 2,
                "op": OOD_OP,
                "content": text,
                "ch": [(_CONTENT_RING, f"{text} {payload}")],
                "tok": tok,
                "title": a["title"],
            }
        )
        claim = POISON_CLAIMS.get(a["title"])
        if claim:
            cases.append(
                {
                    "kind": "attack",
                    "cls": "poison",
                    "form": "wiki_poison",
                    # length-OOD: the long doc exceeds the judge window, so _poison_defended's
                    # full-when-over-window rule judges task/grounding against the FULL text.
                    "tier": 2,
                    "op": OOD_OP,
                    "content": text,
                    "ch": [(_CONTENT_RING, f"{text} {claim}")],
                    "claim": claim,
                    "title": a["title"],
                }
            )
    return cases


def _rate(cases, cond_fn, outcome_fn):
    """Mean outcome (excluding None/errored) of running ``cond_fn`` over ``cases``, scored by ``outcome_fn``."""
    vals = [outcome_fn(c, cond_fn(c["op"], c["ch"])) for c in cases]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def score_conditions(conds=("base+prompt", "composite", "conditioned")):
    """LENGTH-generalization eval (uses the 26B -- run from final_eval, not the tuning loop).

    For each condition: Q on long CLEAN docs, R on long TOKEN injections, and defense on long content-POISON.
    Lets the paper show whether the conditioned cascade holds parity with composite as document length grows.
    """
    from cascading_lms import retune as R
    from cascading_lms import skillopt_formal as SO

    cases = build_ood_cases()
    clean = [c for c in cases if c["kind"] == "clean"]
    tok = [c for c in cases if c.get("cls") == "tok"]
    poi = [c for c in cases if c.get("cls") == "poison"]
    out = {}
    for cond in conds:
        cf = R.COND[cond]
        out[cond] = {
            "Q_long": _rate(clean, cf, SO._clean_outcome),
            "R_token_long": _rate(tok, cf, SO._attack_case_outcome),
            "poison_long": _rate(poi, cf, SO._attack_case_outcome),
            "n": {"clean": len(clean), "token": len(tok), "poison": len(poi)},
        }
    return out


if __name__ == "__main__":
    recs = build_cache()
    lens = sorted(r["n_chars"] for r in recs)
    print(
        f"\nwrote {len(recs)} articles | chars min={lens[0]} median={lens[len(lens) // 2]} max={lens[-1]}"
    )
