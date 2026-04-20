"""Judge runner — P16 four-class evaluation pipeline.

Implements :class:`BaseJudger` + four concrete subclasses
(:class:`AbsoluteSingleJudger` / :class:`AbsoluteConversationJudger` /
:class:`ComparativeSingleJudger` / :class:`ComparativeConversationJudger`)
that all share the same "render prompt → call LLM → parse JSON → build
EvalResult" skeleton, but differ in:

1. **What ctx goes into ``schema.render_prompt``** — e.g. absolute-single
   needs ``{system_prompt, history, user_input, ai_response}``,
   comparative needs the extra ``{reference_response}``.
2. **What extra fields the parser pulls from the LLM reply** — absolute
   judgers read flat dimension scores + ``ai_ness_penalty`` + verdict +
   ``analysis``/``strengths``/``weaknesses``; comparative judgers read
   two parallel score dicts + ``score_diff`` + ``winner_reasons`` etc.

All other concerns (config resolution, LLM call + retry + timeout,
strip markdown fences, schema_snapshot embedding, result envelope
shape) live in :class:`BaseJudger` and are shared across modes, so each
subclass is small (60-120 lines).

Design notes
------------
* **Schema is authoritative for math**. Judgers delegate
  ``compute_raw_score`` / ``normalize_overall_score`` /
  ``clamp_dim_score`` / ``evaluate_pass_rule`` to :class:`ScoringSchema`
  (added in P15/P16 step 1). That way a future schema that introduces
  e.g. weighted-median or sigmoid normalization only needs to override
  there, not in every judger class.
* **Judge config resolution** re-uses ``chat_runner.resolve_group_config
  (session, "judge")``. All three fallback layers (form / preset-bundled
  / ``api_keys.json``) are free. ``judge_model_override`` on ``/judge/
  run`` lets a tester compare schemas across judge models without
  editing Settings, via a transient :class:`ModelGroupConfig` merged on
  top.
* **Schema snapshot is always embedded**. Every :class:`EvalResult`
  carries the full ``schema.to_dict()`` under ``schema_snapshot`` so
  later edits to the on-disk schema do NOT retroactively change
  historical scores (the P17 Results UI reads scores in the context
  they were generated, not "current" schema). This costs ~3 KB per
  result on average; negligible.
* **Error surface is the result, not the exception**. A judger call that
  fails mid-LLM or fails to parse JSON returns an :class:`EvalResult`
  with ``error`` populated and ``scores``/``verdict`` null rather than
  raising, so a batch run (many messages at once) can surface a mix of
  successes + failures in one shot. The router translates a *bulk*
  all-failed case to HTTP 502 itself; single-item failures just sit in
  the result list so the UI can offer "retry this one".
* **No streaming**. Unlike chat_runner, judger calls are one-shot
  request/response — the LLM answers a single JSON blob, and we need
  the entire blob before we can parse anything useful. Streaming would
  add complexity without benefit. Duration ~5-15s per call is
  acceptable at Testbench scale (humans wait, batch sizes are usually
  < 20).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
)
from tests.testbench.logger import python_logger
from tests.testbench.model_config import ModelGroupConfig
from tests.testbench.pipeline.chat_runner import (
    ChatConfigError,
    resolve_group_config,
)
from tests.testbench.pipeline.scoring_schema import (
    ScoringSchema,
    ScoringSchemaError,
    read_schema,
)
from tests.testbench.session_store import Session


# ── Errors ──────────────────────────────────────────────────────────


class JudgeRunError(RuntimeError):
    """Raised when the router-layer cannot even set up a judger run.

    Parse / LLM / format failures inside a single judger call are
    reported via :class:`EvalResult.error` instead; this exception is
    for precondition failures that abort the whole batch (unknown
    schema, malformed input, config missing).
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


# ── Eval result envelope ────────────────────────────────────────────


@dataclass
class EvalResult:
    """One judger output, ready to be appended to ``session.eval_results``.

    We ship a dataclass here (vs. a raw dict) so forgetting a field is
    caught at construction time and so ``to_dict`` can enforce a stable
    serialization shape — the P17 Results UI and the P17 export report
    both read from this shape. Dict round-trip for storage / HTTP is
    handled by :meth:`to_dict`.
    """

    id: str
    schema_id: str
    mode: str  # "absolute" | "comparative"
    granularity: str  # "single" | "conversation"
    scope: str  # "conversation" | "messages"
    target_message_ids: list[str]
    target_preview: dict[str, Any]
    schema_snapshot: dict[str, Any]
    judge_model: dict[str, Any]
    created_at: str
    scores: dict[str, Any] = field(default_factory=dict)
    verdict: str | None = None
    passed: bool = False
    analysis: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    # Comparative-specific (kept at top-level rather than nested so
    # filtering in Results is one key-lookup away).
    gap: float | None = None
    relative_advantage: str | None = None
    diff_analysis: str = ""
    problem_patterns: list[str] = field(default_factory=list)
    # Diagnostics
    prompt_char_count: int = 0
    response_char_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_id": self.schema_id,
            "mode": self.mode,
            "granularity": self.granularity,
            "scope": self.scope,
            "target_message_ids": list(self.target_message_ids),
            "target_preview": dict(self.target_preview),
            "schema_snapshot": self.schema_snapshot,
            "judge_model": dict(self.judge_model),
            "created_at": self.created_at,
            "scores": dict(self.scores),
            "verdict": self.verdict,
            "passed": self.passed,
            "analysis": self.analysis,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "gap": self.gap,
            "relative_advantage": self.relative_advantage,
            "diff_analysis": self.diff_analysis,
            "problem_patterns": list(self.problem_patterns),
            "prompt_char_count": self.prompt_char_count,
            "response_char_count": self.response_char_count,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def _new_eval_id() -> str:
    return f"ev_{uuid.uuid4().hex[:12]}"


# ── Base judger ──────────────────────────────────────────────────────


@dataclass
class JudgeInputs:
    """Input bundle passed from the router into a specific judger.

    Not all fields are used by every judger; each subclass validates
    what it needs in ``_validate_inputs``. Using a single struct (vs.
    one kwarg per field) keeps the router glue simple — the router
    assembles JudgeInputs once from the request body + session state.
    """

    system_prompt: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)  # full session history
    user_input: str = ""
    ai_response: str = ""
    reference_response: str = ""
    # Whole-conversation target (for granularity=conversation).
    conversation: list[dict[str, Any]] = field(default_factory=list)
    reference_conversation: list[dict[str, Any]] = field(default_factory=list)
    # Target message attribution — stored on EvalResult for back-linking.
    target_message_ids: list[str] = field(default_factory=list)
    # Cosmetic metadata used in prompt rendering.
    character_name: str = ""
    master_name: str = ""
    extra_context: dict[str, Any] = field(default_factory=dict)
    scope: str = "messages"  # "conversation" | "messages"


class BaseJudger(ABC):
    """Template method base — subclasses fill ``_build_ctx`` + ``_parse``.

    Runtime flow:

    1. :meth:`run` — top-level entry point.
    2. :meth:`_resolve_config` — pull judge group config (+ apply any
       per-run override).
    3. :meth:`_build_ctx` (subclass) — shape inputs into the placeholder
       dict that ``schema.render_prompt`` needs.
    4. :meth:`_render_and_call` — interpolate + invoke LLM + strip
       markdown fences.
    5. :meth:`_parse` (subclass) — pull structured fields from JSON into
       an :class:`EvalResult` (without the envelope — just the
       judger-specific payload).
    6. :meth:`_finalize` — merge payload into the envelope, run
       ``schema.evaluate_pass_rule`` to fill ``passed``, return.
    """

    #: "absolute" or "comparative" — used to pick which pass-rule
    #: evaluation path applies + to stamp EvalResult.mode.
    MODE: str = "absolute"
    #: "single" or "conversation".
    GRANULARITY: str = "single"

    def __init__(
        self,
        *,
        session: Session,
        schema: ScoringSchema,
        judge_model_override: ModelGroupConfig | None = None,
    ) -> None:
        self.session = session
        self.schema = schema
        self.judge_model_override = judge_model_override

    # ── public entrypoint ──────────────────────────────────────

    async def run(self, inputs: JudgeInputs) -> EvalResult:
        """Run one judging call end-to-end and return an :class:`EvalResult`.

        Always returns (never raises on LLM/parse failures); on error
        fills ``result.error`` and leaves ``scores`` empty so the UI can
        show a clearly-failed row with a "[retry]" affordance.
        """
        started_ms = time.perf_counter()
        created_at = datetime.now().isoformat(timespec="seconds")

        try:
            self._validate_inputs(inputs)
        except JudgeRunError as exc:
            python_logger().warning(
                "[judge_runner] input validation failed (%s): %s",
                exc.code, exc.message,
            )
            return EvalResult(
                id=_new_eval_id(),
                schema_id=self.schema.id,
                mode=self.MODE,
                granularity=self.GRANULARITY,
                scope=inputs.scope,
                target_message_ids=list(inputs.target_message_ids),
                target_preview=self._build_target_preview(inputs),
                schema_snapshot=self.schema.to_dict(),
                judge_model={},
                created_at=created_at,
                error=f"{exc.code}: {exc.message}",
            )

        try:
            cfg = self._resolve_config()
        except ChatConfigError as exc:
            return EvalResult(
                id=_new_eval_id(),
                schema_id=self.schema.id,
                mode=self.MODE,
                granularity=self.GRANULARITY,
                scope=inputs.scope,
                target_message_ids=list(inputs.target_message_ids),
                target_preview=self._build_target_preview(inputs),
                schema_snapshot=self.schema.to_dict(),
                judge_model={},
                created_at=created_at,
                error=f"{exc.code}: {exc.message}",
            )

        ctx = self._build_ctx(inputs)
        prompt = self.schema.render_prompt(ctx)
        prompt_char_count = len(prompt)

        try:
            raw = await self._call_llm(cfg=cfg, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - started_ms) * 1000)
            python_logger().warning(
                "[judge_runner] LLM call failed (%s): %s",
                type(exc).__name__, exc,
            )
            return EvalResult(
                id=_new_eval_id(),
                schema_id=self.schema.id,
                mode=self.MODE,
                granularity=self.GRANULARITY,
                scope=inputs.scope,
                target_message_ids=list(inputs.target_message_ids),
                target_preview=self._build_target_preview(inputs),
                schema_snapshot=self.schema.to_dict(),
                judge_model=_judge_model_summary(cfg),
                created_at=created_at,
                error=f"LlmFailed: {type(exc).__name__}: {exc}",
                prompt_char_count=prompt_char_count,
                duration_ms=duration_ms,
            )

        duration_ms = int((time.perf_counter() - started_ms) * 1000)
        cleaned = _strip_json_fence(raw or "")

        result = EvalResult(
            id=_new_eval_id(),
            schema_id=self.schema.id,
            mode=self.MODE,
            granularity=self.GRANULARITY,
            scope=inputs.scope,
            target_message_ids=list(inputs.target_message_ids),
            target_preview=self._build_target_preview(inputs),
            schema_snapshot=self.schema.to_dict(),
            judge_model=_judge_model_summary(cfg),
            created_at=created_at,
            prompt_char_count=prompt_char_count,
            response_char_count=len(cleaned),
            duration_ms=duration_ms,
            raw_response=cleaned[:4000],  # capped so eval_results doesn't explode
        )

        try:
            data = json.loads(cleaned)
            if not isinstance(data, dict):
                raise TypeError(
                    f"JSON root must be an object, got {type(data).__name__}"
                )
        except (json.JSONDecodeError, TypeError) as exc:
            result.error = f"JsonParseFailed: {exc}"
            result.analysis = cleaned[:800]
            return result

        try:
            self._parse(data=data, into=result, inputs=inputs)
        except Exception as exc:  # noqa: BLE001
            python_logger().warning(
                "[judge_runner] parse failed (%s): %s",
                type(exc).__name__, exc,
            )
            result.error = f"ParseFailed: {type(exc).__name__}: {exc}"
            return result

        self._finalize(result)
        return result

    # ── subclass hooks ─────────────────────────────────────────

    def _validate_inputs(self, inputs: JudgeInputs) -> None:
        """Raise :class:`JudgeRunError` when required fields are missing.

        Default: single-granularity judgers want a non-empty
        ``ai_response`` and ``user_input``; conversation-granularity
        judgers want a non-empty ``conversation``. Subclasses can
        tighten this further (comparative → require reference).
        """
        if self.GRANULARITY == "single":
            if not inputs.ai_response.strip():
                raise JudgeRunError(
                    "MissingAiResponse",
                    "ai_response 为空, 无法评分单条回复.",
                    status=422,
                )
        else:
            if not inputs.conversation:
                raise JudgeRunError(
                    "EmptyConversation",
                    "conversation 为空, 无法评分整段对话.",
                    status=422,
                )

    @abstractmethod
    def _build_ctx(self, inputs: JudgeInputs) -> dict[str, Any]:
        """Shape inputs into the ``ctx`` dict that ``schema.render_prompt`` consumes.

        Subclasses must populate the placeholders their own schema
        templates reference (``{system_prompt}`` / ``{user_input}`` /
        ``{ai_response}`` / ``{reference_response}`` / ``{history}`` /
        etc.). Extra ``extra_context`` provided by the caller is
        overlaid last so testers can inject custom ``{my_tag}`` values
        via the ``POST /judge/run`` body.
        """

    @abstractmethod
    def _parse(
        self,
        *,
        data: dict[str, Any],
        into: EvalResult,
        inputs: JudgeInputs,
    ) -> None:
        """Pull judger-specific fields from the JSON reply.

        Mutates ``into.scores`` / ``.verdict`` / ``.analysis`` / etc.
        For absolute: flat dim scores + penalty + YES/NO verdict.
        For comparative: two parallel score dicts + A/B/tie verdict +
        gap + winner reasons etc.
        """

    # ── shared helpers ─────────────────────────────────────────

    def _resolve_config(self) -> ModelGroupConfig:
        """Return a ready-to-send :class:`ModelGroupConfig` for the judge group.

        Per-run overrides (``judge_model_override``) merge field-by-
        field on top of the session config: only non-default override
        fields win, so a caller that wants "same config but different
        model" can pass just ``{"model": "gpt-4o-mini"}`` without having
        to re-specify base_url / api_key.
        """
        base = resolve_group_config(self.session, "judge")
        override = self.judge_model_override
        if override is None:
            return base
        merged: dict[str, Any] = {}
        for fld, val in override.model_dump(exclude_unset=True).items():
            if val is not None and val != "":
                merged[fld] = val
        if not merged:
            return base
        merged_cfg = base.model_copy(update=merged)
        return merged_cfg

    async def _call_llm(self, *, cfg: ModelGroupConfig, prompt: str) -> str:
        """Issue a non-streaming ChatCompletion and return the raw content.

        Uses the upstream ``utils.llm_client.ChatOpenAI`` helper the
        chat/simuser/memory runners all share, so OpenAI / Anthropic /
        Qwen / Lanlan-free all "just work" as long as the base_url is
        OpenAI-compatible. ``aclose()`` in finally for httpx pool hygiene.
        """
        from utils.llm_client import ChatOpenAI, HumanMessage

        client = ChatOpenAI(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout or 90.0,  # judgers get a bit more than chat
            max_retries=1,
            streaming=False,
        )
        try:
            resp = await client.ainvoke([HumanMessage(content=prompt.strip())])
            return (resp.content or "").strip()
        finally:
            try:
                await client.aclose()
            except Exception as close_exc:  # noqa: BLE001
                python_logger().debug(
                    "[judge_runner] ChatOpenAI.aclose failed: %s", close_exc,
                )

    def _build_target_preview(self, inputs: JudgeInputs) -> dict[str, Any]:
        """Lean preview dict for the UI (avoid bloating Results drawer).

        Truncated long fields so eval_results stays tractable even if
        the user runs 100 judges in a row. Full context is already in
        ``schema_snapshot`` + raw_response for debugging.
        """
        def _trim(s: str, n: int) -> str:
            s = (s or "").strip()
            return s if len(s) <= n else s[:n] + "…"

        preview: dict[str, Any] = {
            "system_prompt": _trim(inputs.system_prompt, 200),
            "user_input": _trim(inputs.user_input, 400),
            "ai_response": _trim(inputs.ai_response, 800),
            "history_len": len(inputs.history),
            "conversation_len": len(inputs.conversation),
        }
        if inputs.reference_response:
            preview["reference_response"] = _trim(inputs.reference_response, 800)
        return preview

    def _finalize(self, result: EvalResult) -> None:
        """Apply ``schema.evaluate_pass_rule`` to fill ``result.passed``.

        Absolute mode uses pass_rule against the flat ``scores`` dict
        (which at this point contains per-dim ints + raw_score +
        overall_score + ai_ness_penalty). Comparative mode derives
        ``passed`` from ``verdict == 'A_better' or 'tie'`` as a
        reasonable default when pass_rule is empty — a comparative
        schema that wants its own rule (e.g. "A must win by >= 10")
        can encode it using ``score_diff``/``gap`` as the variable.
        """
        if self.MODE == "absolute":
            variables: dict[str, Any] = {**result.scores}
            result.passed = self.schema.evaluate_pass_rule(variables)
        elif self.MODE == "comparative":
            pass_rule = (self.schema.pass_rule or "").strip()
            if pass_rule:
                variables = {
                    **result.scores,
                    "gap": result.gap if result.gap is not None else 0.0,
                    "score_diff": result.gap if result.gap is not None else 0.0,
                }
                result.passed = self.schema.evaluate_pass_rule(variables)
            else:
                result.passed = result.verdict in {"A_better", "tie"}
        else:  # pragma: no cover - unreachable with current MODE values
            result.passed = False


# ── Concrete judgers ─────────────────────────────────────────────────


class AbsoluteSingleJudger(BaseJudger):
    """Score a single AI response in its full conversation context.

    Replicates :mod:`tests.utils.prompt_test_judger` but driven by
    ``builtin_prompt_test`` (or any user schema with same shape).
    Expected reply JSON (matches the builtin template):

    .. code-block:: json

        {
          "verdict": "YES"|"NO",
          "<dim_key>": 1-10,            # one entry per schema.dimensions
          "ai_ness_penalty": 0-15,       # optional, only if schema has penalty
          "overall_score": 0-100,        # informational; we recompute
          "strengths": [...],
          "weaknesses": [...],
          "analysis": "…"
        }
    """

    MODE = "absolute"
    GRANULARITY = "single"

    def _build_ctx(self, inputs: JudgeInputs) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "system_prompt": inputs.system_prompt,
            "history": _format_history_for_prompt(
                inputs.history,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "user_input": inputs.user_input,
            "ai_response": inputs.ai_response,
            "character_name": inputs.character_name or "AI",
            "master_name": inputs.master_name or "用户",
        }
        ctx.update(inputs.extra_context or {})
        return ctx

    def _parse(
        self,
        *,
        data: dict[str, Any],
        into: EvalResult,
        inputs: JudgeInputs,
    ) -> None:
        scores = _parse_absolute_scores(self.schema, data)
        raw_score = self.schema.compute_raw_score(scores)
        overall_score = self.schema.normalize_overall_score(raw_score)
        scores["raw_score"] = raw_score
        scores["overall_score"] = overall_score

        into.scores = scores
        into.verdict = _normalize_absolute_verdict(data.get("verdict"))
        into.analysis = str(data.get("analysis", "")).strip()
        into.strengths = _normalize_str_list(data.get("strengths"))
        into.weaknesses = _normalize_str_list(data.get("weaknesses"))


class AbsoluteConversationJudger(BaseJudger):
    """Score a full multi-turn conversation as a whole.

    Replicates :mod:`tests.utils.human_like_judger`. The conversation
    is serialized into ``{history}`` and the template skips
    ``{user_input}`` / ``{ai_response}`` (they stay empty via SafeDict).
    Parse shape matches AbsoluteSingle.
    """

    MODE = "absolute"
    GRANULARITY = "conversation"

    def _build_ctx(self, inputs: JudgeInputs) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "system_prompt": inputs.system_prompt,
            "history": _format_history_for_prompt(
                inputs.conversation,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "conversation": _format_history_for_prompt(
                inputs.conversation,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "character_name": inputs.character_name or "AI",
            "master_name": inputs.master_name or "用户",
        }
        ctx.update(inputs.extra_context or {})
        return ctx

    def _parse(
        self,
        *,
        data: dict[str, Any],
        into: EvalResult,
        inputs: JudgeInputs,
    ) -> None:
        # Same reply shape as AbsoluteSingle — we re-use the helpers.
        AbsoluteSingleJudger._parse(self, data=data, into=into, inputs=inputs)  # type: ignore[arg-type]


class ComparativeSingleJudger(BaseJudger):
    """Pairwise A/B comparison for one user input.

    Expected reply JSON:

    .. code-block:: json

        {
          "verdict": "A_better"|"B_better"|"tie",
          "scores_a": { "<dim>": 1-10, ..., "overall_score": 0-100 },
          "scores_b": { "<dim>": 1-10, ..., "overall_score": 0-100 },
          "score_diff": -100..100,
          "winner_reasons": [...],
          "loser_issues": [...],
          "diff_analysis": "…",
          "analysis": "…"
        }
    """

    MODE = "comparative"
    GRANULARITY = "single"

    def _validate_inputs(self, inputs: JudgeInputs) -> None:
        super()._validate_inputs(inputs)
        if not inputs.reference_response.strip():
            raise JudgeRunError(
                "MissingReference",
                "比较模式下必须提供 reference_response (参考回复 B).",
                status=422,
            )

    def _build_ctx(self, inputs: JudgeInputs) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "system_prompt": inputs.system_prompt,
            "history": _format_history_for_prompt(
                inputs.history,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "user_input": inputs.user_input,
            "ai_response": inputs.ai_response,
            "reference_response": inputs.reference_response,
            "character_name": inputs.character_name or "AI",
            "master_name": inputs.master_name or "用户",
        }
        ctx.update(inputs.extra_context or {})
        return ctx

    def _parse(
        self,
        *,
        data: dict[str, Any],
        into: EvalResult,
        inputs: JudgeInputs,
    ) -> None:
        scores_a = _parse_absolute_scores(self.schema, data.get("scores_a") or {})
        scores_b = _parse_absolute_scores(self.schema, data.get("scores_b") or {})
        # Let the schema recompute both overall scores so the front-end
        # can trust one source of truth, even if the LLM's own
        # overall_score disagrees.
        raw_a = self.schema.compute_raw_score(scores_a)
        raw_b = self.schema.compute_raw_score(scores_b)
        overall_a = self.schema.normalize_overall_score(raw_a)
        overall_b = self.schema.normalize_overall_score(raw_b)
        scores_a["raw_score"] = raw_a
        scores_a["overall_score"] = overall_a
        scores_b["raw_score"] = raw_b
        scores_b["overall_score"] = overall_b

        # Per-dimension gap (A - B). Useful for UI bars + aggregate
        # trend analysis without re-reading scores_a/b everywhere.
        per_dim_gap = {
            d.key: int(scores_a.get(d.key, 0)) - int(scores_b.get(d.key, 0))
            for d in self.schema.dimensions
        }

        into.scores = {
            "a": scores_a,
            "b": scores_b,
            "per_dim_gap": per_dim_gap,
            # Flat short-cuts for filtering / aggregate:
            "overall_a": overall_a,
            "overall_b": overall_b,
        }

        verdict_raw = str(data.get("verdict", "")).strip()
        into.verdict = _normalize_comparative_verdict(verdict_raw)

        llm_diff = _safe_float(data.get("score_diff"))
        computed_diff = round(overall_a - overall_b, 2)
        # The LLM's self-reported diff can go a bit off; prefer the
        # derived one but keep the LLM's for audit if they disagree.
        into.gap = computed_diff
        if llm_diff is not None and abs(llm_diff - computed_diff) > 2.0:
            into.scores["_llm_reported_diff"] = llm_diff

        into.relative_advantage = _derive_relative_advantage(
            verdict=into.verdict, per_dim_gap=per_dim_gap,
        )
        into.diff_analysis = str(data.get("diff_analysis", "")).strip()
        into.analysis = str(data.get("analysis", "")).strip()
        into.strengths = _normalize_str_list(data.get("winner_reasons"))
        into.weaknesses = _normalize_str_list(data.get("loser_issues"))


class ComparativeConversationJudger(BaseJudger):
    """Whole-conversation pairwise comparison (A-trajectory vs B-trajectory).

    Expected reply JSON (builtin templates don't yet ship a
    conversation-level comparative schema — this parser is written
    defensively for the schema shape we *expect* P17 to introduce):

    .. code-block:: json

        {
          "verdict": "A_better"|"B_better"|"tie",
          "scores_a": {...},
          "scores_b": {...},
          "score_diff": -100..100,
          "per_turn_gap": [<float>, ...],
          "problem_patterns": [...],
          "diff_analysis": "...",
          "analysis": "..."
        }

    Conversation-level fields (``per_turn_gap``, ``problem_patterns``)
    surface in the Results Aggregate tab later; absent fields degrade
    gracefully to empty lists.
    """

    MODE = "comparative"
    GRANULARITY = "conversation"

    def _validate_inputs(self, inputs: JudgeInputs) -> None:
        if not inputs.conversation:
            raise JudgeRunError(
                "EmptyConversation",
                "conversation 为空, 无法进行整段对比评分.",
                status=422,
            )
        if not inputs.reference_conversation:
            raise JudgeRunError(
                "MissingReferenceConversation",
                "对比整段对话需要 reference_conversation (参考轨迹 B).",
                status=422,
            )

    def _build_ctx(self, inputs: JudgeInputs) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "system_prompt": inputs.system_prompt,
            "history": _format_history_for_prompt(
                inputs.conversation,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "conversation": _format_history_for_prompt(
                inputs.conversation,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "reference_conversation": _format_history_for_prompt(
                inputs.reference_conversation,
                character_name=inputs.character_name,
                master_name=inputs.master_name,
            ),
            "character_name": inputs.character_name or "AI",
            "master_name": inputs.master_name or "用户",
        }
        ctx.update(inputs.extra_context or {})
        return ctx

    def _parse(
        self,
        *,
        data: dict[str, Any],
        into: EvalResult,
        inputs: JudgeInputs,
    ) -> None:
        # Re-use the single-comparative parsing for scores_a/b + gap,
        # then overlay conversation-specific fields.
        ComparativeSingleJudger._parse(self, data=data, into=into, inputs=inputs)  # type: ignore[arg-type]

        raw_turn_gaps = data.get("per_turn_gap") or data.get("turn_gaps") or []
        if isinstance(raw_turn_gaps, list):
            gaps: list[float] = []
            for item in raw_turn_gaps:
                val = _safe_float(item)
                if val is not None:
                    gaps.append(round(val, 2))
            if gaps:
                into.scores["per_turn_gap"] = gaps
        into.problem_patterns = _normalize_str_list(data.get("problem_patterns"))


# ── Shared parse helpers ────────────────────────────────────────────


_FENCE_RE = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)


def _strip_json_fence(text: str) -> str:
    """Remove leading/trailing ``` fences if present.

    Robust to both ` ```json\n...\n``` ` and ` ```\n...\n``` ` and
    the common "no trailing newline before closing fence" shape.
    """
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    s = _FENCE_RE.sub("", s, count=1).strip()
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s


def _parse_absolute_scores(
    schema: ScoringSchema, data: dict[str, Any],
) -> dict[str, Any]:
    """Pull + clamp per-dimension ints + penalty from a flat JSON dict.

    Missing dims default to 0 (clamped), so a model that returns a
    partial response still parses cleanly — the pass_rule check will
    catch "everything is 0" as a fail if the schema cares.
    """
    scores: dict[str, Any] = {}
    for d in schema.dimensions:
        scores[d.key] = schema.clamp_dim_score(data.get(d.key))
    if schema.ai_ness_penalty is not None:
        scores["ai_ness_penalty"] = schema.clamp_penalty(data.get("ai_ness_penalty"))
    return scores


def _normalize_str_list(value: Any) -> list[str]:
    """Coerce an unknown-shape JSON field into ``list[str]``.

    Accept list[str], list[dict-with-one-str-value], or a single string
    (common when the LLM forgets the brackets); discard blanks. Keeps
    the UI cards simple ("zero-or-more bullet lines").
    """
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                # One-key dicts like {"point": "..."}; take the first
                # string value we find so we don't silently drop the
                # content.
                for v in item.values():
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
                        break
        return out
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_absolute_verdict(raw: Any) -> str:
    """Coerce absolute verdict to ``"YES"`` / ``"NO"``.

    Matches the lenient handling in pre-P15 judgers: "YES.", "yes!",
    " YES " all map to YES; everything else falls back to NO. Returning
    a pure two-value enum means the UI badge can render a known set.
    """
    s = str(raw or "").upper().strip()
    s = s.replace(".", "").replace("!", "").replace("'", "").replace('"', "").strip()
    if s.startswith("YES"):
        return "YES"
    return "NO"


def _normalize_comparative_verdict(raw: str) -> str:
    """Map free-form verdict strings to the canonical comparative enum.

    The schemas ship with verdict strings like ``"A_better"`` /
    ``"B_better"`` / ``"tie"``, but LLMs sometimes emit the Chinese
    ``"A 更好"`` or uppercase ``"TIE"``. We normalize to the canonical
    form used everywhere else in the codebase.
    """
    s = str(raw or "").strip()
    lower = s.lower()
    if not s:
        return "tie"
    if "tie" in lower or lower in {"平", "平手", "相等"}:
        return "tie"
    if lower.startswith("a") or "a_better" in lower or "a更好" in s or "a 更好" in s:
        return "A_better"
    if lower.startswith("b") or "b_better" in lower or "b更好" in s or "b 更好" in s:
        return "B_better"
    return "tie"


def _derive_relative_advantage(
    *, verdict: str, per_dim_gap: dict[str, int],
) -> str | None:
    """Pick the single dimension that best summarizes who won + where.

    Returns a compact string like ``"A_better_empathy"`` (A wins mainly
    on the empathy axis) for UI chip rendering. None when there's no
    clear winner (tie or all zero). The Aggregate page later consumes
    this to show "across 10 rounds, A was strongest in 6 empathy cases".
    """
    if verdict == "tie" or not per_dim_gap:
        return None
    if verdict == "A_better":
        biggest = max(per_dim_gap.items(), key=lambda kv: kv[1], default=None)
        if biggest and biggest[1] > 0:
            return f"A_better_{biggest[0]}"
    elif verdict == "B_better":
        smallest = min(per_dim_gap.items(), key=lambda kv: kv[1], default=None)
        if smallest and smallest[1] < 0:
            return f"B_better_{smallest[0]}"
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_history_for_prompt(
    messages: Iterable[dict[str, Any]],
    *,
    character_name: str,
    master_name: str,
) -> str:
    """Serialize history messages into the pre-P15 text format.

    Matches ``prompt_test_judger`` / ``human_like_judger`` exactly
    (``[角色名]: 内容`` per line) so the three existing builtin
    schemas produce byte-identical prompts to their pre-P15 ancestors
    when evaluated through this code path. System messages get a
    ``[系统消息]`` label; roles outside the known three are skipped
    (they're never produced by our own pipeline but defensive coding
    keeps us safe against imported/restored sessions).
    """
    character_name = character_name or "AI"
    master_name = master_name or "用户"
    lines: list[str] = []
    for turn in messages or []:
        role = turn.get("role")
        content = str(turn.get("content") or "")
        if role == ROLE_SYSTEM:
            lines.append(f"[系统消息]: {content}")
        elif role == ROLE_USER:
            lines.append(f"[{master_name}]: {content}")
        elif role == ROLE_ASSISTANT:
            lines.append(f"[{character_name}]: {content}")
    return "\n".join(lines)


def _judge_model_summary(cfg: ModelGroupConfig) -> dict[str, Any]:
    """Lean, api-key-free model config dict for embedding in EvalResult."""
    return {
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "timeout": cfg.timeout,
    }


# ── Factory ─────────────────────────────────────────────────────────


_JUDGER_REGISTRY: dict[tuple[str, str], type[BaseJudger]] = {
    ("absolute", "single"): AbsoluteSingleJudger,
    ("absolute", "conversation"): AbsoluteConversationJudger,
    ("comparative", "single"): ComparativeSingleJudger,
    ("comparative", "conversation"): ComparativeConversationJudger,
}


def make_judger(
    *,
    session: Session,
    schema: ScoringSchema,
    judge_model_override: ModelGroupConfig | None = None,
) -> BaseJudger:
    """Pick the right concrete judger based on ``schema.mode`` + ``.granularity``.

    Exposed as a factory (rather than letting callers pick the class
    directly) so future schemas / granularities can be registered
    without touching the router.
    """
    key = (schema.mode, schema.granularity)
    cls = _JUDGER_REGISTRY.get(key)
    if cls is None:
        raise JudgeRunError(
            "UnsupportedSchemaShape",
            f"不支持的 schema 形态: mode={schema.mode!r}, "
            f"granularity={schema.granularity!r}.",
            status=422,
        )
    return cls(
        session=session,
        schema=schema,
        judge_model_override=judge_model_override,
    )


def load_schema_by_id(schema_id: str) -> ScoringSchema:
    """Read a schema from disk and parse into the dataclass.

    Raises :class:`JudgeRunError(SchemaNotFound / SchemaInvalid)` with
    HTTP-friendly status codes so the router can forward the error
    without a second try/except layer.
    """
    try:
        raw = read_schema(schema_id)
    except ScoringSchemaError as exc:
        raise JudgeRunError(exc.code, exc.message, status=exc.status) from exc
    try:
        return ScoringSchema.from_dict(raw["active"])
    except ScoringSchemaError as exc:
        raise JudgeRunError(
            exc.code, exc.message, status=exc.status,
        ) from exc


__all__ = [
    "AbsoluteConversationJudger",
    "AbsoluteSingleJudger",
    "BaseJudger",
    "ComparativeConversationJudger",
    "ComparativeSingleJudger",
    "EvalResult",
    "JudgeInputs",
    "JudgeRunError",
    "load_schema_by_id",
    "make_judger",
]
