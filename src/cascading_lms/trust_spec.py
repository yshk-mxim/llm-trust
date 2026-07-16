# Copyright (c) 2026 Yakov P. Shkolnikov
# SPDX-License-Identifier: MIT
"""Trust-model specification: the single declarative source for the lattice, roles, policy, and tuning.

Loads a TOML spec (default ``data/trust_model.toml``, parsed with the stdlib ``tomllib`` so the runtime
stays dependency-free; ``.json`` is also stdlib, ``.yaml`` needs the optional PyYAML), validates it against the ``harness.Ring``
enum -- so the specified lattice and the *proven* lattice can never silently disagree -- and DERIVES
everything the generation plane needs: the tier curriculum, each ring's passivation prompt-key and role, the
passivated set, the plane boundaries that replace hardcoded ``int(ring) < int(Ring.X)`` predicates, and the
rendered trust-order string. A different spec reconfigures the whole defense plane by config alone; the
paper's 4-ring lattice is just the default instance.

``plane`` decouples a ring's ROLE from its integrity integer -- ``control`` (task source, never passivated),
``request`` (an intent, forms the higher-trust basis), ``data`` (untrusted content, passivated). A
trusted-data ring (RAG / SYSTEM_MEMORY) is thus expressible as ``plane=data`` *above* USER in integrity,
which the old integer predicates could not represent.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from typing import Any

from cascading_lms.harness import Ring

PLANES = ("control", "request", "data")
# The default metric COMPOSITION (what Q/R mean), exposed for org configuration + auto-doc. The Q/R HOT PATH
# in skillopt_formal/final_eval is unchanged; a spec `metrics:` override records a different definition but
# wiring it into the hot path byte-identically is deferred (see the metric() note).
_DEFAULT_METRICS = {
    "Q": "task AND grounded (AND, for web_additional, the WEB-only fact attributed if surfaced)",
    "R": "token: deterministic canary; poison: (NOT asserted) AND task AND grounded",
}
_DEFAULT_PATH = os.path.join("data", "trust_model.toml")
_DEFAULT_POOL = os.path.join(
    "data", "v6e_combined_v2.jsonl"
)  #: the default synthetic source corpus.
_EXTERNAL_SPLITS = ("train", "val", "ood", "fresh")
# The org-configurable threat-model keys. attack_mix/ind_forms/ood_forms shape the SYNTHETIC TRAINING data
# (retune); eval_forms/eval_tok_mix/eval_tok_mix_low shape the held-out EVAL token batch and
# eval_hijack_mix/eval_hijack_mix_low the held-out EVAL instruction-attack batch (final_eval), so each held-out
# batch can be reshaped in step with training rather than pinned to a hardcoded list. Empty threat_model ->
# every key falls back to the pipeline default (byte-identical).
_THREAT_KEYS = (
    "attack_mix",
    "ind_forms",
    "ood_forms",
    "eval_forms",
    "eval_tok_mix",
    "eval_tok_mix_low",
    "eval_hijack_mix",
    "eval_hijack_mix_low",
)
SPEC_ENV = (
    "TRUST_MODEL_SPEC"  #: env var selecting the active spec; unset -> the default 4-ring lattice.
)


def active_path() -> str:
    """The active spec path: ``$TRUST_MODEL_SPEC`` if set, else the default 4-ring lattice.

    A library user (or a portability test) points the whole pipeline at a different trust model by setting this
    one env var before import -- no code edit. The default remains ``data/trust_model.toml``.
    """
    return os.environ.get(SPEC_ENV, _DEFAULT_PATH)


def _require(cond, msg):
    """Raise a ValueError with ``msg`` if ``cond`` is false (spec validation -- library-quality, not asserts)."""
    if not cond:
        raise ValueError(f"trust-model spec: {msg}")


@dataclass(frozen=True)
class RingSpec:
    """One ring's declared identity: integrity (must match the enum), plane, activity, and passivation role."""

    name: str
    integrity: int
    plane: str
    active: bool = True
    prompt_key: str | None = None
    goal: str = ""
    grounded_against: str = "self"
    must_not: tuple = ()

    @property
    def ring(self) -> Ring:
        """The harness.Ring this spec names."""
        return Ring[self.name]


@dataclass
class TrustModel:
    """A loaded, validated trust-model spec; the pipeline derives its ring structure from this object."""

    name: str
    rings: tuple
    action_min: str
    tiers: dict
    policy: dict
    tuning: dict
    models: dict
    per_action: dict  #: {action_name: min-ring-name}; an unlisted action falls back to action_min.
    judges: dict  #: {judge_name: {"asks": criteria}} -- overrides a judge's criteria; empty = the defaults.
    metrics: (
        dict  #: {"Q"|"R": composition} -- overrides the metric composition; empty = the defaults.
    )
    threat_model: dict  #: {attack_mix, ind_forms, ood_forms (train) + eval_forms, eval_tok_mix[,_low], eval_hijack_mix[,_low] (held-out eval)}; empty = defaults.
    data: dict  #: {source: synthetic|external, synthetic:{pool}, external:{train,val,ood,fresh}}; empty = synthetic default.

    # --- loading -------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str = _DEFAULT_PATH) -> TrustModel:
        """Load + validate a spec from YAML (or JSON by ``.json`` extension)."""
        raw = read_data_file(path)
        rings = tuple(
            RingSpec(
                name=r["name"],
                integrity=int(r["integrity"]),
                plane=r["plane"],
                active=bool(r.get("active", True)),
                prompt_key=r.get("prompt_key"),
                goal=r.get("goal", ""),
                grounded_against=r.get("grounded_against", "self"),
                must_not=tuple(r.get("must_not", ())),
            )
            for r in raw["rings"]
        )
        tm = cls(
            name=raw["name"],
            rings=rings,
            action_min=raw["actions"]["action_min"],
            tiers={int(k): list(v) for k, v in raw["tiers"].items()},
            policy=raw.get("policy", {}),
            tuning=raw.get("tuning", {}),
            models=raw.get("models", {}),
            per_action=dict(raw["actions"].get("per_action", {})),
            judges={j["name"]: j for j in raw.get("judges", [])},
            metrics=dict(raw.get("metrics", {})),
            threat_model=dict(raw.get("threat_model", {})),
            data=dict(raw.get("data", {})),
        )
        tm.validate()
        return tm

    # --- validation: the consistency guard (spec lattice == proven lattice) --------------------------
    def validate(self) -> None:
        """Fail LOUD on any spec inconsistency, especially a spec integrity that disagrees with the enum."""
        names = [r.name for r in self.rings]
        _require(len(names) == len(set(names)), f"duplicate ring names in {self.name!r}")
        for r in self.rings:
            _require(r.plane in PLANES, f"{r.name}: bad plane {r.plane!r}")
            _require(
                Ring[r.name].value == r.integrity,
                f"{r.name}: spec integrity {r.integrity} != harness.Ring {Ring[r.name].value} "
                "(the specified lattice must match the proven lattice)",
            )
            if r.active and r.plane != "control":
                _require(
                    bool(r.prompt_key), f"{r.name}: an active passivated ring needs a prompt_key"
                )
        controls = [r for r in self.rings if r.plane == "control"]
        _require(
            len(controls) == 1,
            f"exactly one control ring required, got {[c.name for c in controls]}",
        )
        _require(self.action_min in names, f"action_min {self.action_min!r} is not a declared ring")
        for act, rn in self.per_action.items():
            _require(rn in names, f"per_action[{act!r}] threshold {rn!r} is not a declared ring")
            # A per-action threshold BELOW the global floor LOWERS an action's trust bar. It is a legitimate
            # org override (the mechanism is symmetric -- normally used to RAISE, e.g. delete_db: SYSTEM), so
            # this is a loud WARNING, not a hard failure, to surface an accidental weakening.
            if Ring[rn].value < Ring[self.action_min].value:
                print(
                    f"[trust-model] WARNING: per_action[{act!r}]={rn} is BELOW the global action_min "
                    f"({self.action_min}) -- this LOWERS the trust bar for {act!r} (allowed as an explicit "
                    "org override; flagged so it is not accidental).",
                    flush=True,
                )
        active = {r.name for r in self.rings if r.active}
        for tier, trings in self.tiers.items():
            for rn in trings:
                _require(rn in active, f"tier {tier} references inactive/unknown ring {rn!r}")
        _require(self.mode in ("coordinate", "multivariate"), f"bad tuning.mode {self.mode!r}")
        self._validate_override_keys()
        self._validate_data()

    def _validate_override_keys(self) -> None:
        """Validate the optional override sections' keys (metrics, threat_model)."""
        for k in self.metrics:
            _require(k in ("Q", "R"), f"metrics override key {k!r} must be 'Q' or 'R'")
        for k in self.threat_model:
            _require(
                k in _THREAT_KEYS,
                f"threat_model key {k!r} must be one of {', '.join(_THREAT_KEYS)}",
            )

    def _validate_data(self) -> None:
        """Validate the optional data section: source in {synthetic, external}; external needs all 4 split paths."""
        src = self.data_source()
        _require(
            src in ("synthetic", "external"),
            f"data.source {src!r} must be 'synthetic' or 'external'",
        )
        if src == "external":
            ext = self.external_paths()
            missing = [s for s in _EXTERNAL_SPLITS if not ext.get(s)]
            _require(
                not missing,
                f"data.source='external' requires data.external paths for {missing} "
                "(train, val, ood, fresh -- the org supplies its own pre-built, pristine-disjoint splits)",
            )

    # --- lookups -------------------------------------------------------------------------------------
    def spec(self, ring) -> RingSpec:
        """The RingSpec for a Ring or a ring name."""
        name = ring.name if isinstance(ring, Ring) else ring
        return next(r for r in self.rings if r.name == name)

    @property
    def active_rings(self) -> tuple:
        """Active RingSpecs, descending integrity (the trust order)."""
        return tuple(sorted((r for r in self.rings if r.active), key=lambda r: -r.integrity))

    @property
    def control_ring(self) -> Ring:
        """The single control-plane ring (the task source)."""
        return next(r.ring for r in self.rings if r.plane == "control")

    @property
    def mode(self) -> str:
        """The tuning strategy: ``coordinate`` or ``multivariate``."""
        return self.tuning.get("mode", "multivariate")

    def metric(self, name: str) -> str:
        """The composition descriptor for metric ``name`` (Q|R): the spec override, else the default.

        Exposes the metric DEFINITION for org configuration + auto-doc. The default Q/R hot path
        (skillopt_formal/final_eval) is UNCHANGED; wiring an overridden composition into that hot path
        byte-identically is deferred.
        """
        return self.metrics.get(name, _DEFAULT_METRICS.get(name, ""))

    @property
    def policy_input(self) -> list:
        """Ordered INPUT parser/guard names (spec.policy.input): the built-in ``passivate`` + user plugins."""
        return list(self.policy.get("input", ["passivate"]))

    @property
    def policy_output(self) -> list:
        """Ordered OUTPUT guard names (spec.policy.output): the built-in monitor portions + user guards."""
        return list(self.policy.get("output", ["obeyed_only_system", "grounded", "relevant"]))

    @property
    def enforce_output(self) -> bool:
        """Whether an OUTPUT guard failure is a HARD, fail-closed refusal (spec.policy.enforce_output)."""
        return bool(self.policy.get("enforce_output", False))

    def threat(self, key: str, default):
        """The org's declared threat-model list for ``key`` (attack_mix / ind_forms / ood_forms), else ``default``.

        The default spec has no ``threat_model`` section, so every key falls back to the pipeline's own value
        (byte-identical). An org that declares a threat_model reshapes which attacks are generated + evaluated.
        """
        return self.threat_model.get(key, default)

    # --- data: the org's corpus (synthetic generation from a pool, or pre-built external splits) ------
    def data_source(self) -> str:
        """``synthetic`` (generate attacks from a source pool -- the default) or ``external`` (pre-built splits)."""
        return self.data.get("source", "synthetic")

    def data_pool(self) -> str:
        """The synthetic source-corpus path (spec.data.synthetic.pool), else the default v6e corpus.

        The default spec has no ``data`` section, so this is the current corpus (byte-identical). An org points
        it at THEIR corpus and the SAME synthetic attack-generation (shaped by threat_model/tiers/lattice) runs.
        """
        return self.data.get("synthetic", {}).get("pool", _DEFAULT_POOL)

    def external_paths(self) -> dict:
        """{train,val,ood,fresh: path} pre-built split files -- only meaningful when data_source == 'external'."""
        return dict(self.data.get("external", {}))

    def action_threshold(self, action: str = "") -> Ring:
        """The minimum-integrity ring an ACTION needs to fire (I3).

        ``spec.actions.per_action`` maps {action_name: ring}; an unlisted or omitted action falls back to the
        global ``action_min`` -- so a spec without ``per_action`` resolves EVERY action to ``action_min``
        (byte-identical to the single-threshold behaviour). Returns a Ring.
        """
        return Ring[self.per_action.get(action, self.action_min)]

    @property
    def judge_model(self) -> str:
        """The API tuner + judge model id (the 'Opus' role; swappable via spec.models.judge)."""
        return self.models.get("judge", {}).get("id", "claude-opus-4-8")

    @property
    def defended_model(self) -> str:
        """The served model-under-test id (spec.models.defended)."""
        return self.models.get("defended", {}).get(
            "id", "mlx-community/gemma-4-26B-A4B-it-qat-8bit"
        )

    @property
    def defended_endpoint(self) -> str:
        """The served model-under-test endpoint (spec.models.defended)."""
        return self.models.get("defended", {}).get(
            "endpoint", "http://localhost:9000/v1/chat/completions"
        )

    # --- plane boundaries: replace hardcoded int(ring) < int(Ring.X) predicates ----------------------
    def is_data(self, ring) -> bool:
        """Untrusted data ring (was ``int(ring) < int(Ring.USER)``)."""
        return self.spec(ring).plane == "data"

    def is_request(self, ring) -> bool:
        """A request-plane ring (the operator's intent, e.g. USER) -- not control, not untrusted data."""
        return self.spec(ring).plane == "request"

    def is_control(self, ring) -> bool:
        """The control ring (was ``ring == Ring.SYSTEM``)."""
        return self.spec(ring).plane == "control"

    def is_passivated(self, ring) -> bool:
        """A ring that gets passivated -- everything below control (was ``int(r) < int(Ring.SYSTEM)``)."""
        return self.spec(ring).plane != "control"

    def in_basis(self, ring) -> bool:
        """A higher-trust basis ring (request or control plane; was ``int(r) >= int(Ring.USER)``)."""
        return self.spec(ring).plane in ("request", "control")

    # --- named ring ROLES for data/attack construction (so builders place by ROLE, not by fixed name) ----
    @property
    def data_rings(self) -> list:
        """Active DATA-plane rings, descending trust (default: [CONTENT, WEB]).

        The attack/channel construction places injections and content by these ROLES rather than the fixed
        names CONTENT/WEB, so a lattice with a different data ring set still builds.
        """
        return [r.ring for r in self.active_rings if r.plane == "data"]

    @property
    def primary_data_ring(self) -> Ring:
        """The highest-trust DATA ring (default CONTENT) -- the attached-document channel."""
        return self.data_rings[0]

    @property
    def secondary_data_ring(self) -> Ring | None:
        """The next DATA ring below primary (default WEB), or None if the lattice has only one data ring."""
        rings = self.data_rings
        return rings[1] if len(rings) > 1 else None

    @property
    def request_ring(self) -> Ring:
        """The request-plane ring -- the operator's own intent channel (default USER)."""
        return next(r.ring for r in self.active_rings if r.plane == "request")

    # --- derived pipeline structures (replace the scattered literals) --------------------------------
    @property
    def tier_rings(self) -> dict:
        """ring_spec.TIER_RINGS -- the active rings carrying untrusted input at each tier."""
        return {t: [Ring[n] for n in names] for t, names in self.tiers.items()}

    @property
    def pass_ctx_key(self) -> dict:
        """config.PASS_CTX_KEY -- {ring-name: passivation prompt key} for active passivated rings."""
        return {r.name: r.prompt_key for r in self.active_rings if r.plane != "control"}

    @property
    def passr(self) -> set:
        """retune.PASSR -- the set of active passivated rings."""
        return {r.ring for r in self.active_rings if r.plane != "control"}

    @property
    def role_spec(self) -> dict:
        """ring_spec.ROLE_SPEC -- per-ring passivation goal, grounding, and must-not contract."""
        return {
            r.ring: {
                "prompt_key": r.prompt_key,
                "goal": r.goal,
                "grounded_against": r.grounded_against,
                "must_not": list(r.must_not),
            }
            for r in self.active_rings
            if r.plane != "control"
        }

    @property
    def blind_pass_key(self) -> dict:
        """retune.PASSR -- {ring: blind per-ring passivation key 'pass_<RING>'} for active passivated rings."""
        return {r.ring: f"pass_{r.name}" for r in self.active_rings if r.plane != "control"}

    def basis(self, tier: int, ring) -> list:
        """ring_spec.basis_rings -- higher-integrity BASIS rings (request/control) present at ``tier``, descending.

        A ring conditions only on higher-trust request/context rings present at its tier; data rings never
        enter another ring's basis (relevance + the data trust-order are the wrapper's job).
        """
        present = set(self.tiers.get(tier, ()))
        floor = self.spec(ring).integrity
        return [
            r.ring
            for r in self.active_rings
            if r.name in present and r.integrity > floor and self.in_basis(r.ring)
        ]

    def trust_order_str(self) -> str:
        """The trust order rendered from the spec, e.g. 'SYSTEM > USER > CONTENT > WEB'."""
        return " > ".join(r.name for r in self.active_rings)

    # --- auto-documentation (the spec self-documents; no hand-maintained drift) -----------------------
    def _md_rings(self) -> list:
        return [
            f"| {r.name} | {r.integrity} | {r.plane} | {r.active} | {r.prompt_key or '-'} | "
            f"{'; '.join(r.must_not) or '-'} |"
            for r in sorted(self.rings, key=lambda x: -x.integrity)
        ]

    def _md_tiers(self) -> list:
        return [f"- tier {t}: {', '.join(self.tiers[t])}" for t in sorted(self.tiers)]

    def describe(self) -> str:
        """Render the trust model as markdown -- the spec documents itself, no hand-maintained drift."""
        pol = self.policy
        return "\n".join(
            [
                f"# Trust model: {self.name}",
                "",
                f"Trust order: **{self.trust_order_str()}**  |  action fires at meet >= **{self.action_min}**",
                "",
                "## Rings",
                "",
                "| ring | integrity | plane | active | prompt key | must-not |",
                "|---|---|---|---|---|---|",
                *self._md_rings(),
                "",
                "## Tiers",
                "",
                *self._md_tiers(),
                "",
                "## Policy / guards",
                "",
                f"- input checks: {', '.join(pol.get('input', [])) or '(none)'}",
                f"- output checks: {', '.join(pol.get('output', [])) or '(none)'} "
                f"(enforced={pol.get('enforce_output', False)})",
                "",
                "## Models",
                "",
                f"- judge (API / 'Opus' role): `{self.judge_model}`",
                f"- defended (under test): `{self.defended_model}` @ `{self.defended_endpoint}`",
                "",
                "## Tuning",
                "",
                f"- mode: **{self.mode}**  |  q_floor: {self.tuning.get('q_floor')}",
                "",
                "## Threat model (attacks generated + evaluated)",
                "",
                *(
                    f"- {key}: {', '.join(self.threat_model.get(key, [])) or '(pipeline default)'}"
                    for key in _THREAT_KEYS
                ),
                "",
                "## Data",
                "",
                f"- source: **{self.data_source()}**",
                f"- synthetic pool: `{self.data_pool()}`"
                if self.data_source() == "synthetic"
                else f"- external splits: {self.external_paths()}",
                "",
            ]
        )


def read_data_file(path: str) -> dict[str, Any]:
    """Read a spec/data file as a dict, dispatching on extension so the DEFAULT path is stdlib-only.

    ``.toml`` -> ``tomllib`` (stdlib, the default), ``.json`` -> ``json`` (stdlib), ``.yaml``/``.yml`` ->
    PyYAML, which is an OPTIONAL dependency (dev + a user who supplies a YAML spec) lazily imported here with
    a clear message if it is absent. The runtime never needs PyYAML for the shipped ``.toml`` files.
    """
    if path.endswith(".toml"):
        with open(path, "rb") as fh:  # tomllib requires binary mode
            return tomllib.load(fh)
    with open(path) as fh:
        text = fh.read()
    if path.endswith(".json"):
        return json.loads(text)
    try:
        import yaml
    except ModuleNotFoundError as exc:  # optional: only a user-supplied .yaml spec needs it
        raise RuntimeError(
            f"reading a YAML spec ({path}) needs PyYAML (pip install pyyaml); the default .toml/.json paths are stdlib-only"
        ) from exc
    return yaml.safe_load(text)


DEFAULT = TrustModel.load(
    active_path()
)  #: the active lattice ($TRUST_MODEL_SPEC or the default 4-ring).


if __name__ == "__main__":  # `python src/trust_spec.py [spec.yaml] > docs/trust_model.md`
    import sys

    print(TrustModel.load(sys.argv[1] if len(sys.argv) > 1 else active_path()).describe())
