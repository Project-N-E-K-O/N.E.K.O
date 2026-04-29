"""Scoring schema — P15 first-class evaluation configuration.

A ``ScoringSchema`` fully describes *how* a single judging pass should be
carried out: which dimensions to score, what weights / anchors each carries,
the raw + normalized score formulas, the pass rule, and the prompt template
that the judger model sees. Judgers are **schema-driven** (P16) — the same
``judge_runner`` code handles human-like conversation evaluation, single-turn
prompt-test evaluation, and comparative evaluation simply by swapping the
schema.

Design notes
------------
* **Storage** follows the same builtin+user dual-directory convention used
  by ``script_runner``: ``tests/testbench/scoring_schemas/builtin_*.json``
  ships with the code tree, ``tests/testbench_data/scoring_schemas/*.json``
  holds tester-authored customizations. When both exist at the same ``id``,
  user wins (same rule as dialog templates) and the UI flags it with an
  ``overriding_builtin`` badge.
* **Prompt rendering** uses ``str.format_map`` with a safe defaulting dict
  so that any placeholder the schema template references but the caller
  didn't provide simply resolves to empty string instead of blowing up mid
  request. We also pre-compute three convenience blocks
  (``dimensions_block`` / ``anchors_block`` / ``formula_block``) so simple
  schemas can just drop them in rather than re-implement the same repeated
  rendering logic.
* **Validation** is split in two layers: ``validate_schema_dict`` returns a
  *list* of ``{path, message}`` errors (so editor UIs can red-box multiple
  fields at once, matching the ``script_runner`` save flow), while
  ``ScoringSchema.validate()`` on an already-constructed dataclass raises on
  the first problem — good enough for programmatic callers that have
  already passed a round-trip through ``validate_schema_dict``.
* **Schema snapshot** — P16 will embed the full schema dict into every
  ``EvalResult`` so that later edits / deletions don't break historical
  reproducibility. That means the on-disk shape must be self-contained
  (no cross-schema references) and stable across versions.
"""

from __future__ import annotations

import ast
import copy
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Iterable, Literal

from tests.testbench import config as tb_config
from tests.testbench.logger import python_logger


# ── Errors ──────────────────────────────────────────────────────────


class ScoringSchemaError(RuntimeError):
    """Raised by schema IO / validation.

    Codes mirror :class:`ScriptError` for UX consistency:

    * ``SchemaNotFound`` — GET/DELETE/Read hits an unknown id (404).
    * ``SchemaInvalid`` — field-level validation failed (422); field-level
      errors hang off ``exc.errors`` for the router to forward into the
      response detail.
    * ``SchemaBuiltinProtected`` — attempt to delete or overwrite a builtin
      schema in-place (403).
    * ``SchemaTargetExists`` — duplicate-to-user would overwrite an existing
      user file and ``overwrite=False`` (409).
    """

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.errors: list[dict[str, str]] = []
        super().__init__(f"{code}: {message}")


# ── Types / constants ───────────────────────────────────────────────


ScoringMode = Literal["absolute", "comparative"]
ScoringGranularity = Literal["single", "conversation"]

_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_VALID_MODES: Final[frozenset[str]] = frozenset({"absolute", "comparative"})
_VALID_GRANULARITY: Final[frozenset[str]] = frozenset({"single", "conversation"})
_ANCHOR_RANGE_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\s*-\s*(\d+)$")

#: Schema file / id prefix marking a read-only bundled preset. Mirrors the
#: ``builtin_`` convention used for dialog templates (``sample_*``, but for
#: schemas the prefix is part of the id so that listings keep them grouped).
BUILTIN_PREFIX: Final[str] = "builtin_"

#: Placeholders the judger rendering layer understands natively. Schemas may
#: declare additional placeholder variables via ``extra_placeholders`` but
#: any unknown name in the actual template will still be caught by the
#: safe-format machinery (rendered as empty string, not a KeyError).
KNOWN_PLACEHOLDERS: Final[tuple[str, ...]] = (
    "system_prompt",
    "history",
    "user_input",
    "ai_response",
    "reference_response",
    "character_name",
    "master_name",
    "scenario_block",
    "dimensions_block",
    "anchors_block",
    "formula_block",
    "verdict_rule",
    "pass_rule",
    "max_raw_score",
)


# ── Dataclass ───────────────────────────────────────────────────────


@dataclass
class ScoringDimension:
    """One scoring axis on a :class:`ScoringSchema`.

    ``weight`` is the coefficient applied in the raw-score sum; the
    individual per-dimension score is always a 1-10 integer (judgers clamp
    out-of-range model outputs at parse time). ``anchors`` is a human-readable
    rubric keyed by score-range label (``"9-10"``, ``"7-8"``, …); the
    anchors are surfaced verbatim in the prompt and also shown in the
    Schemas editor UI.
    """

    key: str
    label: str
    weight: float
    description: str = ""
    anchors: dict[str, str] = field(default_factory=dict)


@dataclass
class AiNessPenalty:
    """Optional ``ai_ness_penalty`` dock.

    Not every schema needs it — ``builtin_comparative_basic`` might drop it
    because the gap metric already captures mechanical-feel differences —
    but the two ``absolute`` builtins reuse the existing
    ``human_like_judger`` / ``prompt_test_judger`` design of a 0-15 integer
    penalty subtracted from the raw score before normalization.
    """

    max: int = 15
    max_passable: int = 9
    anchors: dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass
class ScoringSchema:
    """Typed view of one scoring schema. Round-trips through ``to_dict`` /
    ``from_dict`` so the on-disk JSON is the source of truth.
    """

    id: str
    name: str
    description: str
    mode: ScoringMode
    granularity: ScoringGranularity
    dimensions: list[ScoringDimension]
    prompt_template: str
    ai_ness_penalty: AiNessPenalty | None = None
    pass_rule: str = ""
    verdict_rule: str = ""
    raw_score_formula: str = ""
    normalize_formula: str = ""
    version: int = 1
    tags: list[str] = field(default_factory=list)

    # ── Convenience / derived ──────────────────────────────────────

    @property
    def max_raw_score(self) -> float:
        """Maximum positive raw score (each dim at 10, penalty at 0)."""
        return round(sum(d.weight * 10 for d in self.dimensions), 4)

    def has_reference(self) -> bool:
        """True for comparative schemas (they need a reference response)."""
        return self.mode == "comparative"

    # ── Round-trip ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "granularity": self.granularity,
            "dimensions": [
                {
                    "key": d.key,
                    "label": d.label,
                    "weight": d.weight,
                    "description": d.description,
                    "anchors": dict(d.anchors),
                }
                for d in self.dimensions
            ],
            "prompt_template": self.prompt_template,
            "pass_rule": self.pass_rule,
            "verdict_rule": self.verdict_rule,
            "raw_score_formula": self.raw_score_formula,
            "normalize_formula": self.normalize_formula,
            "version": self.version,
            "tags": list(self.tags),
        }
        if self.ai_ness_penalty is not None:
            out["ai_ness_penalty"] = {
                "max": self.ai_ness_penalty.max,
                "max_passable": self.ai_ness_penalty.max_passable,
                "anchors": dict(self.ai_ness_penalty.anchors),
                "description": self.ai_ness_penalty.description,
            }
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoringSchema":
        """Construct from a dict (usually freshly loaded JSON).

        Raises :class:`ScoringSchemaError(SchemaInvalid)` with ``exc.errors``
        populated if the dict has any field-level problems. Use
        :func:`validate_schema_dict` first if you want to preview errors
        without raising.
        """
        errors = _collect_schema_errors(data)
        if errors:
            exc = ScoringSchemaError(
                "SchemaInvalid",
                "schema 校验未通过, 无法解析.",
                status=422,
            )
            exc.errors = errors
            raise exc
        dims = [
            ScoringDimension(
                key=str(d["key"]),
                label=str(d.get("label", d["key"])),
                weight=float(d["weight"]),
                description=str(d.get("description", "")),
                anchors={str(k): str(v) for k, v in (d.get("anchors") or {}).items()},
            )
            for d in data["dimensions"]
        ]
        penalty_raw = data.get("ai_ness_penalty")
        penalty = None
        if isinstance(penalty_raw, dict):
            penalty = AiNessPenalty(
                max=int(penalty_raw.get("max", 15)),
                max_passable=int(penalty_raw.get("max_passable", 9)),
                anchors={
                    str(k): str(v)
                    for k, v in (penalty_raw.get("anchors") or {}).items()
                },
                description=str(penalty_raw.get("description", "")),
            )
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            description=str(data.get("description", "")),
            mode=str(data.get("mode", "absolute")),  # type: ignore[arg-type]
            granularity=str(data.get("granularity", "single")),  # type: ignore[arg-type]
            dimensions=dims,
            prompt_template=str(data["prompt_template"]),
            ai_ness_penalty=penalty,
            pass_rule=str(data.get("pass_rule", "")),
            verdict_rule=str(data.get("verdict_rule", "")),
            raw_score_formula=str(data.get("raw_score_formula", "")),
            normalize_formula=str(data.get("normalize_formula", "")),
            version=int(data.get("version") or 1),
            tags=[str(t) for t in (data.get("tags") or [])],
        )

    # ── Runtime validation (raising variant) ───────────────────────

    def validate(self) -> None:
        """Strict validation; raises :class:`ScoringSchemaError(SchemaInvalid)`.

        ``validate_schema_dict`` is the preferred API for UIs that want the
        full error list. This method is a convenience for callers that have
        already built the dataclass and want a fail-fast assertion.
        """
        errors = _collect_schema_errors(self.to_dict())
        if errors:
            exc = ScoringSchemaError(
                "SchemaInvalid",
                "schema dataclass 校验未通过.",
                status=422,
            )
            exc.errors = errors
            raise exc

    # ── Prompt rendering ───────────────────────────────────────────

    def render_prompt(self, ctx: dict[str, Any] | None = None) -> str:
        """Interpolate ``prompt_template`` with the given context.

        Unknown placeholders (not in ``ctx`` and not one of our precomputed
        blocks) collapse to empty string rather than raising — prompts stay
        robust across schema / context drift. If you need strict behavior
        for testing, call ``validate_schema_dict`` first which rejects
        templates referencing unresolvable names.

        Hardened for P21.3 F1 (prompt injection 最小化防御 pass):

        * Attribute access (``{x.__class__}``) and index access (``{x[0]}``)
          are disabled via :class:`_SafeFormatter`; user-authored templates
          cannot traverse Python object internals on ctx values for info
          leak purposes.
        * ``ValueError`` from malformed templates (unmatched braces / bad
          format spec) is caught and re-raised as
          :class:`ScoringSchemaError` (``SchemaInvalid``) with an
          actionable ``path=prompt_template`` message, so a corrupt or
          manually-edited-and-smuggled-past-validate schema surfaces as a
          clean 422 in the judger API instead of crashing the judge
          worker with a raw stack trace.
        """
        ctx = dict(ctx or {})
        ctx.setdefault("dimensions_block", self.format_dimensions_block())
        ctx.setdefault("anchors_block", self.format_anchors_block())
        ctx.setdefault("formula_block", self.format_formula_block())
        ctx.setdefault("verdict_rule", self.verdict_rule)
        ctx.setdefault("pass_rule", self.pass_rule)
        ctx.setdefault("max_raw_score", self.max_raw_score)
        try:
            return _SAFE_FORMATTER.vformat(self.prompt_template, (), ctx)
        except (ValueError, IndexError) as exc:
            raise ScoringSchemaError(
                "SchemaInvalid",
                f"prompt_template \u6e32\u67d3\u5931\u8d25: {exc}",
                status=422,
            ) from exc

    # ── Rendering helpers (precomputed blocks) ─────────────────────

    def format_dimensions_block(self) -> str:
        """Render ``- key: description`` lines for each dimension."""
        lines: list[str] = []
        for d in self.dimensions:
            if d.description:
                lines.append(f"- {d.key} ({d.label}): {d.description}")
            else:
                lines.append(f"- {d.key} ({d.label})")
        return "\n".join(lines)

    def format_anchors_block(self) -> str:
        """Render the anchor rubric text the judger prompt embeds verbatim.

        Format matches the existing ``prompt_test_judger.format_score_anchors``
        / ``human_like_eval_config.format_human_like_score_anchors`` output
        so that the builtin schemas produce byte-identical prompts to the
        pre-P15 judgers when the shared placeholders are wired up.
        """
        lines: list[str] = []
        for d in self.dimensions:
            lines.append(f"- {d.key}: {d.description}")
            for rng, desc in d.anchors.items():
                lines.append(f"  - {rng} \u5206: {desc}")
        if self.ai_ness_penalty is not None:
            anchors = self.ai_ness_penalty.anchors
            desc = self.ai_ness_penalty.description or "\u673a\u5668\u611f\u60e9\u7f5a\u5206"
            lines.append(f"- ai_ness_penalty: {desc}")
            for rng, text in anchors.items():
                lines.append(f"  - {rng} \u5206: {text}")
        return "\n".join(lines)

    def format_formula_block(self) -> str:
        """Render raw_score + normalize_formula ready to paste in a prompt.

        When the schema didn't override the formulas we autogenerate a
        human-readable default from the dimensions list so that the prompt
        always matches what ``judge_runner`` (P16) actually computes.
        """
        raw = self.raw_score_formula.strip()
        if not raw:
            terms = [f"{d.key} * {d.weight}" for d in self.dimensions]
            raw = "raw_score =\n" + " +\n".join(terms)
            if self.ai_ness_penalty is not None:
                raw += "\n- ai_ness_penalty"
        norm = self.normalize_formula.strip()
        if not norm:
            norm = f"overall_score = max(raw_score, 0) / {self.max_raw_score} * 100"
        return f"{raw}\n\n{norm}"

    # ── Scoring helpers (P16 judge_runner) ─────────────────────────
    #
    # These live on ScoringSchema (not on BaseJudger) because the schema
    # is the authoritative source for "how to compute raw/normalize/pass";
    # judgers only differ in "what prompt to render + what fields to
    # extract from the JSON reply". Keeping the math here means a future
    # schema with a custom formula can override ``compute_raw_score``
    # / ``evaluate_pass_rule`` without touching judger code.

    def clamp_dim_score(self, value: Any) -> int:
        """Round+clamp a per-dimension raw model output to ``[0, 10]`` int.

        Mirrors ``prompt_test_judger._clamp_score`` / ``human_like_judger.
        _clamp_score``: graceful on ``None`` / non-numeric strings /
        floats slightly over 10 ("9.5" → 10); returns 0 on hard garbage so
        downstream arithmetic doesn't blow up.
        """
        try:
            # OverflowError covers ``int(round(float('1e400')))`` =
            # ``int(round(inf))`` = OverflowError (a real path when an
            # LLM judger returns absurd scores). 2nd-batch AI review #4
            # family extension.
            score = int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, min(10, score))

    def clamp_penalty(self, value: Any) -> int:
        """Clamp AI-ness penalty to ``[0, penalty.max]`` int.

        When the schema has no ``ai_ness_penalty``, any value folds to 0.
        When value is garbage and a penalty is configured, we default to
        ``penalty.max`` (worst-case) rather than 0, matching the existing
        judger convention (prefer penalizing on parse failure).
        """
        if self.ai_ness_penalty is None:
            return 0
        pmax = int(self.ai_ness_penalty.max)
        try:
            # OverflowError covers ``int(round(float('1e400')))`` —
            # same path as ``clamp_score``. 2nd-batch AI review #4
            # family extension.
            penalty = int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            return pmax
        return max(0, min(pmax, penalty))

    def compute_raw_score(self, scores: dict[str, Any]) -> float:
        """Weighted sum of dimension scores, minus AI-ness penalty.

        ``scores`` is the post-clamp dict (one int per dim key, plus
        optional ``ai_ness_penalty``). Rounds to 2 decimals to match the
        pre-P15 output shape so existing report consumers don't drift.
        """
        total = 0.0
        for d in self.dimensions:
            total += float(scores.get(d.key, 0)) * float(d.weight)
        if self.ai_ness_penalty is not None:
            total -= float(scores.get("ai_ness_penalty", 0))
        return round(total, 2)

    def normalize_overall_score(self, raw_score: float) -> float:
        """Normalize raw score to ``[0.0, 100.0]``.

        Default formula is ``max(raw, 0) / max_raw_score * 100``; schemas
        that set ``normalize_formula`` to custom text do **not** get a
        different Python formula here — the template text is for the LLM
        prompt only. If a future schema needs a different actual math
        behavior it should subclass BaseJudger or we'll add a tiny
        expression-eval here; don't try to parse the free-text field.
        """
        max_raw = self.max_raw_score
        if max_raw <= 0:
            return 0.0
        normalized = max(float(raw_score), 0.0) / max_raw * 100.0
        return round(max(0.0, min(100.0, normalized)), 2)

    def evaluate_pass_rule(self, variables: dict[str, Any]) -> bool:
        """Evaluate ``self.pass_rule`` against a variables dict.

        ``pass_rule`` is a free-text expression like
        ``overall_score >= 75 AND naturalness >= 6``. Empty string means
        "always pass" (useful for comparative schemas where verdict is
        A/B/tie, not pass/fail). Returns ``True`` on parse failure rather
        than raising, matching the "permissive parse, strict log" stance
        — the caller gets a boolean and we log a warning so the schema
        author can fix their rule without breaking the pipeline. See
        :func:`_evaluate_pass_rule_expr` for the AST-whitelist approach
        that makes this safe against arbitrary-code-execution.
        """
        rule = (self.pass_rule or "").strip()
        if not rule:
            return True
        try:
            return _evaluate_pass_rule_expr(rule, variables)
        except Exception as exc:  # noqa: BLE001
            python_logger().warning(
                "[scoring_schema] pass_rule '%s' failed to evaluate (%s: %s);"
                " treating as pass.",
                rule, type(exc).__name__, exc,
            )
            return True


# ── pass_rule evaluator (AST-whitelist, no builtins) ─────────────
#
# Why AST-whitelist instead of regex?
#   pass_rule strings carried from pre-P15 code look like "overall_score
#   >= 75 AND naturalness >= 6 AND empathy >= 6 AND ai_ness_penalty <=
#   9". A naive regex split on " AND "/" OR " works for the common case
#   but breaks on parentheses, chained comparisons, or mixed and/or.
#   Python's ast module already has a fully-correct parser for a superset
#   of this grammar; we just restrict what node types are allowed and
#   refuse anything else. This blocks `__import__` / attribute access /
#   function calls / lambdas before eval ever runs.
#
# Why not ``eval(..., {"__builtins__": {}})``?
#   That by itself is not safe — plenty of CVE-tier escapes exist via
#   dunder methods on constants. The AST whitelist does the heavy
#   lifting; the stripped-builtins eval is the second line of defense.

_ALLOWED_PASS_RULE_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Name, ast.Load,
    ast.Constant,
    # Python < 3.8 had Num/Str; we only support 3.11+ so Constant covers
    # numeric and string literals alike.
)


def _evaluate_pass_rule_expr(expr: str, variables: dict[str, Any]) -> bool:
    """Parse and evaluate a single pass_rule expression.

    Raises :class:`ValueError` (disallowed syntax) / :class:`KeyError`
    (unknown variable). Callers should catch + log + fall back to "pass"
    to keep the pipeline resilient against schema authoring mistakes.
    """
    # Normalize human-friendly AND/OR/NOT to Python operators. The
    # uppercase forms are what the existing schemas ship with and what
    # testers type in the editor; Python's lowercase forms also work
    # because .replace on substrings that don't occur is a no-op.
    normalized = expr
    for human, pythonic in ((" AND ", " and "), (" OR ", " or "), (" NOT ", " not ")):
        normalized = normalized.replace(human, pythonic)

    tree = ast.parse(normalized, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_PASS_RULE_NODES):
            raise ValueError(
                f"pass_rule uses disallowed syntax node: {type(node).__name__}"
            )
        if isinstance(node, ast.Name) and node.id not in variables:
            raise KeyError(f"pass_rule references unknown variable: {node.id!r}")

    code = compile(tree, "<pass_rule>", "eval")
    result = eval(code, {"__builtins__": {}}, dict(variables))  # noqa: S307
    return bool(result)


# ── Safe formatting ────────────────────────────────────────────────


class _SafeDict(dict):
    """Dict subclass that never KeyErrors on ``str.format_map`` lookup.

    Unknown keys resolve to empty string so that schemas can keep
    placeholders they don't always want to populate (``{reference_response}``
    on absolute schemas, extra debug locals, etc.).
    """

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


# ── P21.3 F1: hardened formatter ────────────────────────────────────


class _SafeFormatter(__import__("string").Formatter):
    """``string.Formatter`` that locks down attribute/index access.

    Why: user-authored ``prompt_template`` can, with default Python
    ``format_map`` semantics, do ``{history.__class__.__mro__}`` or
    ``{ctx[0]}`` to traverse Python object internals on ctx values.
    The leak is minor in testbench today (ctx is plain strings), but
    prompt injection is a "user-editable field feeds into AI" surface
    we want to close systematically (PLAN §13 F1). Locking at the
    formatter layer is cheap and defence-in-depth vs. any future ctx
    value that happens to be a dataclass / model / session object.

    Unknown top-level names resolve to ``""`` (matches legacy
    ``_SafeDict`` UX — schemas can keep optional placeholders like
    ``{reference_response}`` without breaking absolute-mode).
    """

    def get_field(self, field_name: str, args: tuple, kwargs: dict) -> tuple:
        # We deliberately skip the base class's ast-walking dispatch so
        # neither ``.attr`` nor ``[idx]`` after the first token can reach
        # Python's getattr / getitem. Only the first identifier counts.
        first = field_name.split(".", 1)[0].split("[", 1)[0]
        return kwargs.get(first, ""), first

    def format_field(self, value: Any, format_spec: str) -> str:
        # Empty-string fallback for None / unset ctx values.
        if value is None:
            value = ""
        return super().format_field(value, format_spec)


_SAFE_FORMATTER = _SafeFormatter()


# ── Validation (soft / collecting) ─────────────────────────────────


def _collect_schema_errors(raw: Any) -> list[dict[str, str]]:
    """Return a list of ``{path, message}`` errors; empty = valid.

    Used by both the soft-validate endpoint (``POST /judge/schemas/validate``)
    and internally by :meth:`ScoringSchema.from_dict`. Error messages are
    in zh-CN because they surface in the editor UI.
    """
    errors: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(raw, dict):
        add("", f"schema \u987a\u5c42\u5fc5\u987b\u662f JSON object, \u6536\u5230 {type(raw).__name__}.")
        return errors

    schema_id = raw.get("id")
    if not isinstance(schema_id, str) or not schema_id.strip():
        add("id", "\u7f3a\u5c11\u975e\u7a7a 'id' \u5b57\u6bb5 (\u540c\u65f6\u662f\u6587\u4ef6\u540d).")
    elif not _ID_RE.match(schema_id.strip()):
        add("id", "id \u53ea\u80fd\u5305\u542b\u5b57\u6bcd/\u6570\u5b57/\u4e0b\u5212\u7ebf/\u77ed\u6a2a\u7ebf, \u9996\u5b57\u4e0d\u80fd\u662f\u7b26\u53f7, \u957f\u5ea6 \u2264 64.")

    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        add("name", "name \u82e5\u5b58\u5728\u5fc5\u987b\u662f\u5b57\u7b26\u4e32.")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        add("description", "description \u82e5\u5b58\u5728\u5fc5\u987b\u662f\u5b57\u7b26\u4e32.")

    mode = raw.get("mode")
    if mode not in _VALID_MODES:
        add("mode", f"mode \u5fc5\u987b\u4e3a {sorted(_VALID_MODES)} \u4e4b\u4e00, \u6536\u5230 {mode!r}.")

    gran = raw.get("granularity")
    if gran not in _VALID_GRANULARITY:
        add(
            "granularity",
            f"granularity \u5fc5\u987b\u4e3a {sorted(_VALID_GRANULARITY)} \u4e4b\u4e00, \u6536\u5230 {gran!r}.",
        )

    dims = raw.get("dimensions")
    if not isinstance(dims, list) or not dims:
        add("dimensions", "dimensions \u5fc5\u987b\u662f\u975e\u7a7a\u6570\u7ec4.")
    else:
        seen_keys: set[str] = set()
        for i, d in enumerate(dims):
            path = f"dimensions[{i}]"
            if not isinstance(d, dict):
                add(path, "\u6bcf\u4e2a\u7ef4\u5ea6\u5fc5\u987b\u662f object.")
                continue
            key = d.get("key")
            if not isinstance(key, str) or not key.strip():
                add(f"{path}.key", "\u7f3a\u5c11\u975e\u7a7a key.")
            elif not re.match(r"^[a-z][a-z0-9_]{0,63}$", key):
                add(f"{path}.key", "key \u53ea\u80fd\u4e3a\u5c0f\u5199\u5b57\u6bcd/\u6570\u5b57/\u4e0b\u5212\u7ebf, \u9996\u5b57\u6bcd\u5c0f\u5199, \u957f\u5ea6 \u2264 64.")
            elif key in seen_keys:
                add(f"{path}.key", f"key={key!r} \u91cd\u590d; \u7ef4\u5ea6 key \u5fc5\u987b\u552f\u4e00.")
            else:
                seen_keys.add(key)
            weight = d.get("weight")
            try:
                w = float(weight)
                # math.isfinite catches NaN **and** ±inf in one call; the
                # bare ``w != w`` only caught NaN, so ``weight: 1e309``
                # (= float('inf')) used to slip past validation, then later
                # render with ``int(inf)`` blow up at OverflowError far
                # downstream. (GH AI-review issue, 2nd batch #4.)
                if not math.isfinite(w) or w < 0:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                add(f"{path}.weight", f"weight={weight!r} \u5fc5\u987b\u662f \u2265 0 \u7684\u6709\u9650\u6570\u5b57.")
            label = d.get("label")
            if label is not None and not isinstance(label, str):
                add(f"{path}.label", "label \u82e5\u5b58\u5728\u5fc5\u987b\u662f\u5b57\u7b26\u4e32.")
            desc = d.get("description")
            if desc is not None and not isinstance(desc, str):
                add(f"{path}.description", "description \u82e5\u5b58\u5728\u5fc5\u987b\u662f\u5b57\u7b26\u4e32.")
            anchors = d.get("anchors")
            if anchors is not None and not isinstance(anchors, dict):
                add(f"{path}.anchors", "anchors \u82e5\u5b58\u5728\u5fc5\u987b\u662f object.")
            elif isinstance(anchors, dict):
                for rng in anchors.keys():
                    if not isinstance(rng, str) or not _ANCHOR_RANGE_RE.match(rng):
                        add(
                            f"{path}.anchors[{rng!r}]",
                            "anchor key \u5fc5\u987b\u662f '9-10' / '7-8' \u8fd9\u79cd\u533a\u95f4\u683c\u5f0f.",
                        )
        if isinstance(dims, list) and dims:
            try:
                total_w = sum(float(d.get("weight", 0)) for d in dims if isinstance(d, dict))
                if total_w <= 0:
                    add("dimensions", "\u6240\u6709\u7ef4\u5ea6\u7684 weight \u603b\u548c\u5fc5\u987b > 0.")
            except (TypeError, ValueError, OverflowError):
                pass  # 已在上面逐条报过

    penalty = raw.get("ai_ness_penalty")
    if penalty is not None:
        if not isinstance(penalty, dict):
            add("ai_ness_penalty", "ai_ness_penalty \u82e5\u5b58\u5728\u5fc5\u987b\u662f object.")
        else:
            pmax = penalty.get("max")
            try:
                # OverflowError protects against YAML-parsed floats like
                # ``1e400`` (= ±inf) reaching ``int(inf)`` (raises
                # OverflowError, not ValueError, and would otherwise
                # bubble up as 500). (GH AI-review issue, 2nd batch #4.)
                if int(pmax) <= 0:
                    add("ai_ness_penalty.max", f"max={pmax!r} \u5fc5\u987b\u662f > 0 \u7684\u6574\u6570.")
            except (TypeError, ValueError, OverflowError):
                add("ai_ness_penalty.max", f"max={pmax!r} \u5fc5\u987b\u662f\u6709\u9650\u6574\u6570.")
            pok = penalty.get("max_passable")
            try:
                pok_i = int(pok)
                if pok_i < 0:
                    add("ai_ness_penalty.max_passable", "max_passable \u5fc5\u987b \u2265 0.")
                else:
                    # Try to coerce ``pmax`` independently so the
                    # ``max_passable <= max`` check fires even when
                    # ``pmax`` arrived as a numeric float / numeric string
                    # (e.g. ``"5"`` from a YAML config). Restricting the
                    # comparison to ``isinstance(pmax, int)`` previously
                    # let ``max_passable=20, max=5.0`` slip through —
                    # the renderer then divides by ``int(pmax)=5`` and
                    # ``max_passable`` is silently nonsensical (GH AI-
                    # review issue #12).
                    try:
                        pmax_i = int(pmax)
                    except (TypeError, ValueError, OverflowError):
                        pmax_i = None
                    if pmax_i is not None and pok_i > pmax_i:
                        add(
                            "ai_ness_penalty.max_passable",
                            "max_passable \u4e0d\u80fd\u5927\u4e8e max.",
                        )
            except (TypeError, ValueError, OverflowError):
                add("ai_ness_penalty.max_passable", f"max_passable={pok!r} \u5fc5\u987b\u662f\u6709\u9650\u6574\u6570.")

    prompt = raw.get("prompt_template")
    if not isinstance(prompt, str) or not prompt.strip():
        add("prompt_template", "prompt_template \u5fc5\u987b\u662f\u975e\u7a7a\u5b57\u7b26\u4e32.")
    else:
        # 兜底: 检测未闭合 { / } 之类的低级语法错 (format_map 对单花括号会抛).
        try:
            ScoringSchema._probe_format(prompt)
        except ValueError as exc:  # 来自 str.Formatter
            add("prompt_template", f"prompt_template \u683c\u5f0f\u975e\u6cd5: {exc}")

    for field_name in ("pass_rule", "verdict_rule", "raw_score_formula", "normalize_formula"):
        v = raw.get(field_name)
        if v is not None and not isinstance(v, str):
            add(field_name, f"{field_name} \u82e5\u5b58\u5728\u5fc5\u987b\u662f\u5b57\u7b26\u4e32.")

    version = raw.get("version")
    if version is not None:
        try:
            # OverflowError catches a YAML-parsed ``version: 1e400``
            # (= ±inf) reaching ``int(inf)``. 2nd-batch AI review #4
            # family extension.
            int(version)
        except (TypeError, ValueError, OverflowError):
            add("version", f"version={version!r} \u5fc5\u987b\u662f\u6574\u6570.")

    tags = raw.get("tags")
    if tags is not None and not isinstance(tags, list):
        add("tags", "tags \u82e5\u5b58\u5728\u5fc5\u987b\u662f\u5b57\u7b26\u4e32\u6570\u7ec4.")

    return errors


# probe formatting — used by validate to detect `{oops` style syntax errors.
def _probe_format_impl(template: str) -> None:
    """Dry-run the template through :data:`_SAFE_FORMATTER`.

    Only raises for low-level template *syntax* problems (unbalanced braces,
    bad format spec) — unknown names still collapse to empty via the
    hardened formatter. Delegated to a module-level helper so we can expose
    it on the class without shadowing the instance scope.

    Kept in sync with :meth:`ScoringSchema.render_prompt` so probe and render
    agree on what counts as "valid" (P21.3 F1).
    """
    _SAFE_FORMATTER.vformat(template, (), {})


# Attach as a staticmethod. Declaring the helper at module scope keeps the
# `_collect_schema_errors` body readable while still letting us call it
# through ``ScoringSchema._probe_format`` from validate code paths.
ScoringSchema._probe_format = staticmethod(_probe_format_impl)  # type: ignore[attr-defined]


def validate_schema_dict(raw: Any) -> dict[str, Any]:
    """Soft-validation entrypoint for the editor.

    Returns ``{"ok": bool, "errors": [...], "normalized": dict?}``.
    ``normalized`` is present only when ``ok=True`` and round-trips through
    :meth:`ScoringSchema.from_dict` → :meth:`to_dict` so the caller can use
    it as the canonical save payload (default fields filled, extra fields
    dropped). UI rejects save when ``ok=False`` and red-boxes the listed
    paths.
    """
    errors = _collect_schema_errors(raw)
    if errors:
        return {"ok": False, "errors": errors}
    try:
        schema = ScoringSchema.from_dict(raw)
    except ScoringSchemaError as exc:  # pragma: no cover — double check
        return {"ok": False, "errors": exc.errors or [{"path": "", "message": exc.message}]}
    return {"ok": True, "errors": [], "normalized": schema.to_dict()}


# ── Disk IO ─────────────────────────────────────────────────────────


def _is_safe_id(schema_id: str) -> bool:
    """``_ID_RE`` gate — prevents path traversal / illegal filenames."""
    return bool(_ID_RE.match(schema_id or ""))


def _scan_dir(directory: Path, *, source: str) -> list[dict[str, Any]]:
    """Read every ``*.json`` in ``directory``; skip (warn) broken entries.

    Matches ``script_runner._scan_dir`` — a single corrupt file never empties
    the entire listing, it just logs a warning and moves on.
    """
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            python_logger().warning(
                "[scoring_schema] failed to read %s: %s", path, exc,
            )
            continue
        errors = _collect_schema_errors(raw)
        if errors:
            python_logger().warning(
                "[scoring_schema] skipping invalid schema %s: %s",
                path,
                "; ".join(f"{e['path']}: {e['message']}" for e in errors[:3]),
            )
            continue
        raw["_source"] = source
        raw["_path"] = str(path)
        out.append(raw)
    return out


def list_schemas() -> list[dict[str, Any]]:
    """Return merged builtin + user schema meta list for the UI.

    User schemas with the same ``id`` as a builtin shadow the builtin and
    carry ``overriding_builtin: True`` so the editor can surface a warning.
    Meta shape is compact (dims list is summarized as ``dimensions_count``)
    to keep the list payload small; full schema is fetched on demand via
    :func:`read_schema`.
    """
    builtin_list = _scan_dir(tb_config.BUILTIN_SCHEMAS_DIR, source="builtin")
    user_list = _scan_dir(tb_config.USER_SCHEMAS_DIR, source="user")

    merged: dict[str, dict[str, Any]] = {}
    for s in builtin_list:
        merged[s["id"]] = s
    for s in user_list:
        overriding = s["id"] in merged
        s["_overriding_builtin"] = overriding
        merged[s["id"]] = s

    meta: list[dict[str, Any]] = []
    for s in merged.values():
        meta.append({
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "description": s.get("description", ""),
            "mode": s.get("mode"),
            "granularity": s.get("granularity"),
            "dimensions_count": len(s.get("dimensions") or []),
            "has_ai_ness_penalty": isinstance(s.get("ai_ness_penalty"), dict),
            "version": s.get("version", 1),
            "tags": list(s.get("tags") or []),
            "source": s["_source"],
            "path": s["_path"],
            "overriding_builtin": bool(s.get("_overriding_builtin", False)),
        })
    meta.sort(key=lambda m: (m["source"] != "user", m["id"]))
    return meta


def read_schema(schema_id: str) -> dict[str, Any]:
    """Return the full active schema for an id, plus co-existence flags.

    Shape mirrors :func:`script_runner.read_template` so the editor UI can
    reuse the same "active + has_builtin + has_user + overriding_builtin"
    decision flow.
    """
    if not _is_safe_id(schema_id):
        raise ScoringSchemaError(
            "SchemaInvalid",
            f"id={schema_id!r} \u975e\u6cd5.",
            status=422,
        )
    builtin_list = _scan_dir(tb_config.BUILTIN_SCHEMAS_DIR, source="builtin")
    user_list = _scan_dir(tb_config.USER_SCHEMAS_DIR, source="user")
    builtin = next((s for s in builtin_list if s["id"] == schema_id), None)
    user = next((s for s in user_list if s["id"] == schema_id), None)
    if builtin is None and user is None:
        raise ScoringSchemaError(
            "SchemaNotFound",
            f"\u627e\u4e0d\u5230 id={schema_id!r} \u7684\u8bc4\u5206 schema.",
            status=404,
        )
    active = user if user is not None else builtin
    return {
        "active": _strip_meta(active),
        "has_builtin": builtin is not None,
        "has_user": user is not None,
        "overriding_builtin": (user is not None and builtin is not None),
    }


def _strip_meta(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy with the internal ``_source`` / ``_path`` fields dropped."""
    if schema is None:
        return None
    out = {k: v for k, v in schema.items() if not k.startswith("_")}
    return out


def _write_user_schema_atomic(path: Path, data: dict[str, Any]) -> None:
    """Crash-safe write of a user-authored scoring schema JSON.

    P24 §4.1.2 (2026-04-21): delegates to unified ``atomic_io`` helper
    which adds ``fsync`` (previously missing here).
    """
    from tests.testbench.pipeline.atomic_io import atomic_write_json
    atomic_write_json(path, data)


def save_user_schema(raw: Any) -> dict[str, Any]:
    """Create or overwrite a user schema at ``USER_SCHEMAS_DIR/<id>.json``.

    Returns a ``{schema, overriding_builtin, path}`` payload so the UI can
    flash a "now overriding builtin" toast without another round-trip.
    On validation failure raises :class:`ScoringSchemaError(SchemaInvalid)`
    with ``exc.errors`` populated (the router forwards as 422 detail).
    """
    result = validate_schema_dict(raw)
    if not result["ok"]:
        exc = ScoringSchemaError(
            "SchemaInvalid",
            "schema \u6821\u9a8c\u672a\u901a\u8fc7, \u8bf7\u4fee\u6b63\u540e\u91cd\u65b0\u4fdd\u5b58.",
            status=422,
        )
        exc.errors = result["errors"]
        raise exc
    normalized = result["normalized"]
    schema_id = normalized["id"]
    if not _is_safe_id(schema_id):  # defense in depth
        raise ScoringSchemaError(
            "SchemaInvalid",
            f"id={schema_id!r} \u4e0d\u662f\u5408\u6cd5\u6587\u4ef6\u540d.",
            status=422,
        )
    target = tb_config.USER_SCHEMAS_DIR / f"{schema_id}.json"
    _write_user_schema_atomic(target, normalized)
    python_logger().info(
        "[scoring_schema] saved user schema %s -> %s", schema_id, target,
    )
    details = read_schema(schema_id)
    return {
        "schema": details["active"],
        "overriding_builtin": details["overriding_builtin"],
        "path": str(target),
    }


def delete_user_schema(schema_id: str) -> dict[str, Any]:
    """Delete a user schema.  Builtin is protected.

    Returns ``{deleted_id, resurfaces_builtin}``. Raises
    :class:`ScoringSchemaError(SchemaNotFound)` if the id doesn't exist in
    the user directory (builtin-only ids can't be "deleted" because the
    user never saved them; they'd need a user override first).
    """
    if not _is_safe_id(schema_id):
        raise ScoringSchemaError(
            "SchemaInvalid",
            f"id={schema_id!r} \u975e\u6cd5, \u4e0d\u80fd\u5220.",
            status=422,
        )
    target = tb_config.USER_SCHEMAS_DIR / f"{schema_id}.json"
    if not target.exists():
        raise ScoringSchemaError(
            "SchemaNotFound",
            f"user \u76ee\u5f55\u4e0b\u6ca1\u6709 {schema_id!r} schema \u53ef\u5220 (\u5185\u7f6e schema \u4e0d\u53ef\u5220).",
            status=404,
        )
    target.unlink()
    python_logger().info(
        "[scoring_schema] deleted user schema %s (was at %s)", schema_id, target,
    )
    builtin_list = _scan_dir(tb_config.BUILTIN_SCHEMAS_DIR, source="builtin")
    resurfaces = any(s["id"] == schema_id for s in builtin_list)
    return {"deleted_id": schema_id, "resurfaces_builtin": resurfaces}


def duplicate_schema(
    source_id: str, target_id: str, *, overwrite: bool = False,
) -> dict[str, Any]:
    """Copy an existing schema (usually builtin) to a new user id.

    Same semantics as ``duplicate_builtin_to_user`` in ``script_runner``:
    target must not exist in user dir unless ``overwrite=True`` (else 409).
    target with the same id as a builtin is allowed — that's the
    "override builtin" workflow.
    """
    if not _is_safe_id(target_id):
        raise ScoringSchemaError(
            "SchemaInvalid",
            f"target_id={target_id!r} \u975e\u6cd5.",
            status=422,
        )
    source_details = read_schema(source_id)  # raises if unknown
    src = source_details["active"]
    target_path = tb_config.USER_SCHEMAS_DIR / f"{target_id}.json"
    if target_path.exists() and not overwrite:
        raise ScoringSchemaError(
            "SchemaTargetExists",
            f"user \u76ee\u5f55\u5df2\u6709\u540c id {target_id!r}. \u8bf7\u6362\u4e2a id, \u6216\u5e26 overwrite=true \u8986\u76d6.",
            status=409,
        )
    new_raw: dict[str, Any] = copy.deepcopy(src)
    new_raw["id"] = target_id
    if not new_raw.get("name") or src["id"] == new_raw.get("name"):
        new_raw["name"] = target_id
    for meta_key in ("_source", "_path", "_overriding_builtin"):
        new_raw.pop(meta_key, None)
    return save_user_schema(new_raw)


# ── Prompt preview helper (used by /schemas/{id}/preview_prompt) ────


def _extract_template_placeholder_names(template: str) -> set[str]:
    """Return the set of top-level placeholder identifiers used in
    ``template``, mirroring the same "first identifier wins" rule that
    :class:`_SafeFormatter.get_field` enforces at render time.

    Why not ``f"{{{name}}}" in template``: that substring check only
    matches the bare ``{name}`` form and silently misses every formatted
    variant Python ``str.format`` accepts — ``{name!r}`` (conversion),
    ``{name:>10}`` (format spec), ``{name.attr}`` (attribute access),
    ``{name[0]}`` (subscript). The renderer **does** evaluate those
    forms, so the editor's "missing placeholders" badge would lie about
    coverage and the "used placeholders" list would under-report (GH
    AI-review issue #13).

    Uses ``string.Formatter().parse()`` which is the same parser
    ``str.format`` runs internally, so we match the renderer's
    behaviour byte-for-byte. ``parse`` yields ``(literal_text,
    field_name, format_spec, conversion)`` per chunk; literal-only
    chunks (no ``{}``) have ``field_name=None``. Empty/auto-numbered
    fields (``{}``, ``{0}``) and pure subscript-of-positional
    (``{0[k]}``) are skipped — schemas don't use them and counting them
    as "used" would be confusing.
    """
    import string  # local import keeps module-load cost minimal
    formatter = string.Formatter()
    names: set[str] = set()
    try:
        for _literal, field_name, _spec, _conversion in formatter.parse(template):
            if not field_name:
                continue
            # Match _SafeFormatter.get_field's first-identifier rule.
            first = field_name.split(".", 1)[0].split("[", 1)[0]
            if first and not first.isdigit():
                names.add(first)
    except (ValueError, IndexError):
        # Malformed template (unbalanced braces, etc.) — preview_prompt's
        # caller will see the formatter's real error during render; here
        # we just return whatever we did manage to parse.
        pass
    return names


def preview_prompt(schema_id: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render a schema's prompt with a sample (or user-provided) context.

    Returns ``{prompt, char_count, used_placeholders, missing_placeholders}``
    so the editor can show both the rendered string *and* which placeholders
    were supplied / missing. ``missing_placeholders`` only lists the known
    ones (``KNOWN_PLACEHOLDERS``); unknown names the template references
    collapse to empty silently.
    """
    details = read_schema(schema_id)
    schema = ScoringSchema.from_dict(details["active"])
    # Merge the **full** caller-provided ctx so render_prompt sees
    # exactly what the real rendering path would see (custom
    # placeholders the schema author added but that aren't in
    # KNOWN_PLACEHOLDERS used to be silently dropped here, making
    # preview ≠ truth — GH AI-review issue, 2nd batch #5). The
    # KNOWN_PLACEHOLDERS / sample sets are still used **for
    # reporting** (missing / used_report) but no longer gate which
    # keys get rendered.
    sample = _sample_context(schema)
    used_ctx: dict[str, Any] = {**sample, **(ctx or {})}
    # render_prompt auto-populates these from the schema itself — they are
    # never "missing" even when the caller didn't supply them.
    auto_provided = {
        "dimensions_block",
        "anchors_block",
        "formula_block",
        "verdict_rule",
        "pass_rule",
        "max_raw_score",
    }
    rendered = schema.render_prompt(used_ctx)
    template_placeholders = _extract_template_placeholder_names(schema.prompt_template)
    missing = [
        p for p in KNOWN_PLACEHOLDERS
        if p not in used_ctx
        and p not in auto_provided
        and p in template_placeholders
    ]
    used_report = sorted(
        k for k in (set(used_ctx) | auto_provided)
        if k in template_placeholders
    )
    return {
        "prompt": rendered,
        "char_count": len(rendered),
        "used_placeholders": used_report,
        "missing_placeholders": missing,
    }


def _sample_context(schema: ScoringSchema) -> dict[str, Any]:
    """Return a minimal fake context for prompt preview.

    We deliberately keep values short and clearly fake-looking ("…")
    so testers don't confuse the preview output with a real judge run.
    """
    ctx: dict[str, Any] = {
        "system_prompt": "(\u793a\u4f8b system_prompt \u6458\u8981\u2026)",
        "history": "[\u7528\u6237]: \u4eca\u5929\u4e0b\u73ed\u6709\u70b9\u7d2f.\n[AI]: \u561b\u3002\u8981\u4e0d\u8981\u5148\u5c0f\u574e\u4f1a\u513f?",
        "user_input": "\u6211\u4e5f\u4e0d\u77e5\u9053\u4e3a\u4ec0\u4e48, \u5c31\u662f\u6109\u5feb\u4e0d\u8d77\u6765.",
        "ai_response": "(\u793a\u4f8b AI \u56de\u590d\u2026)",
        "reference_response": "(\u793a\u4f8b\u53c2\u8003\u56de\u590d\u2026)" if schema.has_reference() else "",
        "character_name": "N.E.K.O.",
        "master_name": "\u4e3b\u4eba",
        "scenario_block": "",
    }
    return ctx


# ── Ensure dirs on module import — cheap idempotent ────────────────


def ensure_schema_dirs() -> None:
    """Create both schema directories if missing. Idempotent; safe to call
    on every server boot (and :func:`config.ensure_data_dirs` already covers
    the user dir; builtin dir is covered by :func:`config.ensure_code_support_dirs`).
    """
    tb_config.USER_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    tb_config.BUILTIN_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)


__all__ = [
    "AiNessPenalty",
    "BUILTIN_PREFIX",
    "KNOWN_PLACEHOLDERS",
    "ScoringDimension",
    "ScoringSchema",
    "ScoringSchemaError",
    "delete_user_schema",
    "duplicate_schema",
    "ensure_schema_dirs",
    "list_schemas",
    "preview_prompt",
    "read_schema",
    "save_user_schema",
    "validate_schema_dict",
]
