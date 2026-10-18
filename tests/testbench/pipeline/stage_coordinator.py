"""Stage Coach — pipeline-stage guidance (P14).

PLAN §P14 specifies a read-only guidance layer containing current stage + recommended next operation + context
snapshot, shown in a Stage chip in the UI header. **Every side-effecting action**
(actual persona editing / memory writing / message sending / scoring) is performed manually by testers
in other workspaces. This module never executes operations automatically; it only:

    1. Identifies the current one of six stages
    2. Recommends the next operation through a **static** stage mapping, independent of message/memory counts
    3. Supplies side-effect-free context_snapshot numbers so testers decide whether to execute
    4. Lets advance / skip / rewind change only ``session.stage_state``, never running
       business logic

Design principles
--------
* **Purely static recommendations**: operations use a one-to-one constant stage → op mapping, independent of messages
  count / memory items / clock state or other runtime data. Return those values in
  ``context_snapshot`` for the UI, leaving **testers' judgment** to choose execution
  versus skipping. This avoids duplicating memory_runner thresholds in the coordinator
  or silently changing recommendations when thresholds change. PLAN §P14 states:
  'Only suggest, preview, and wait for confirmation; never execute automatically.'
* **/stage/preview unsupported throughout P14**: Stage does not rewrap dry runs;
  memory dry runs already use P10 ``/api/memory/trigger/{op}`` plus modal
  (testers click Trigger under Setup → Memory). Evaluation dry runs mean
  scoring once without saving, provided by P16 ``/api/judge/run``'s
  ``persist=false`` branch under Evaluation → Run
  (schema/target/reference parameters are assembled entirely by that subpage,
  while Stage chips only link there). This iteration's
  ``preview_suggested_op`` always returns ``PreviewUnsupported`` with guidance
  directing testers to the relevant preview subpage.
* **advance / skip / rewind are idempotent stage switches**: they trigger no
  LLM calls / disk writes / clock advances. The only side effects change current / history / updated_at
  in ``session.stage_state`` and append one JSONL log record.
* **context_snapshot must be side-effect-free**: failed field collection yields an empty field
  with a warning, never an exception preventing the whole Stage chip from rendering; the chip
  must still show the stage name when something breaks.
* **Use fixed i18n keys, never hardcoded copy**: return op label/description/when_to_run/
  when_to_skip as i18n keys for frontend ``i18n(key)`` rendering,
  so language changes require no backend edits.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from tests.testbench.logger import python_logger
from tests.testbench.session_store import Session


# ── errors ──────────────────────────────────────────────────────────


class StageError(RuntimeError):
    """Raised when a stage operation can't proceed.

    Code → HTTP status is mapped centrally in the router:
      - NoSession          → 404
      - UnknownStage       → 400 (invalid rewind target)
      - PreviewUnsupported → 412 (preview unavailable for the current stage)
      - InvalidTransition  → 400 (rare, reserved)
    """

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


# ── stage enum + order ──────────────────────────────────────────────


Stage = Literal[
    "persona_setup",
    "memory_build",
    "prompt_assembly",
    "chat_turn",
    "post_turn_memory_update",
    "evaluation",
]

STAGE_ORDER: Final[tuple[Stage, ...]] = (
    "persona_setup",
    "memory_build",
    "prompt_assembly",
    "chat_turn",
    "post_turn_memory_update",
    "evaluation",
)

# evaluation → chat_turn loop-back corresponds to PLAN §9's state-diagram
# ``evaluation --> chat_turn: continue conversation`` edge. Another advance() on evaluation
# returns to chat_turn instead of getting stuck at the end.
_LOOP_AFTER: Final[Stage] = "chat_turn"

VALID_STAGES: Final[frozenset[Stage]] = frozenset(STAGE_ORDER)


def next_stage_of(current: Stage) -> Stage:
    """Return the stage that ``advance`` would land on from ``current``."""
    if current not in VALID_STAGES:
        raise StageError("UnknownStage", f"未知阶段: {current!r}", status=400)
    idx = STAGE_ORDER.index(current)
    if idx + 1 < len(STAGE_ORDER):
        return STAGE_ORDER[idx + 1]
    return _LOOP_AFTER


# ── suggested op catalog ────────────────────────────────────────────


UiAction = Literal[
    "nav_to_setup_persona",
    "nav_to_setup_memory",
    "nav_to_chat_preview",
    "chat_send_hint",
    "memory_trigger_hint",
    "nav_to_evaluation_run",
]


@dataclass(frozen=True)
class SuggestedOp:
    """One recommended action for a stage. Pure metadata — no executable.

    Fields below **are all stable string IDs / i18n keys**. UI renders
    ``i18n(label_i18n_key)`` etc.; if the key is missing the UI falls
    back to the key itself, which is still readable ("stage.op.foo.label").
    """

    op_id: str
    stage: Stage
    ui_action: UiAction
    label_i18n_key: str
    description_i18n_key: str
    when_to_run_i18n_key: str
    when_to_skip_i18n_key: str
    # P14: every operation has dry_run=False; memory dry runs use P10
    # /api/memory/trigger, while evaluation waits for P15/P16. Chip Preview buttons
    # are disabled, with tooltips directing users to actual preview entry points.
    dry_run_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


_OP_PERSONA_EDIT: Final = SuggestedOp(
    op_id="persona.edit",
    stage="persona_setup",
    ui_action="nav_to_setup_persona",
    label_i18n_key="stage.op.persona_edit.label",
    description_i18n_key="stage.op.persona_edit.description",
    when_to_run_i18n_key="stage.op.persona_edit.when_to_run",
    when_to_skip_i18n_key="stage.op.persona_edit.when_to_skip",
)

_OP_MEMORY_EDIT: Final = SuggestedOp(
    op_id="memory.edit",
    stage="memory_build",
    ui_action="nav_to_setup_memory",
    label_i18n_key="stage.op.memory_edit.label",
    description_i18n_key="stage.op.memory_edit.description",
    when_to_run_i18n_key="stage.op.memory_edit.when_to_run",
    when_to_skip_i18n_key="stage.op.memory_edit.when_to_skip",
)

_OP_PROMPT_PREVIEW: Final = SuggestedOp(
    op_id="prompt.preview",
    stage="prompt_assembly",
    ui_action="nav_to_chat_preview",
    label_i18n_key="stage.op.prompt_preview.label",
    description_i18n_key="stage.op.prompt_preview.description",
    when_to_run_i18n_key="stage.op.prompt_preview.when_to_run",
    when_to_skip_i18n_key="stage.op.prompt_preview.when_to_skip",
)

_OP_CHAT_SEND: Final = SuggestedOp(
    op_id="chat.send",
    stage="chat_turn",
    ui_action="chat_send_hint",
    label_i18n_key="stage.op.chat_send.label",
    description_i18n_key="stage.op.chat_send.description",
    when_to_run_i18n_key="stage.op.chat_send.when_to_run",
    when_to_skip_i18n_key="stage.op.chat_send.when_to_skip",
)

_OP_MEMORY_TRIGGER: Final = SuggestedOp(
    op_id="memory.trigger",
    stage="post_turn_memory_update",
    ui_action="memory_trigger_hint",
    label_i18n_key="stage.op.memory_trigger.label",
    description_i18n_key="stage.op.memory_trigger.description",
    when_to_run_i18n_key="stage.op.memory_trigger.when_to_run",
    when_to_skip_i18n_key="stage.op.memory_trigger.when_to_skip",
)

# After P15/P16, replace ``evaluation.pending`` (a placeholder only showing 'not available')
# with ``evaluation.run``, pointing to Evaluation → Run where testers select
# schema/targets and Run scoring. dry_run remains False because stage chips do not assemble
# schema/target parameters; the Run subpage handles actual dry runs through ``/api/judge/run``'s
# ``persist=false`` branch, or a future preview-without-saving button there.
_OP_EVALUATION_RUN: Final = SuggestedOp(
    op_id="evaluation.run",
    stage="evaluation",
    ui_action="nav_to_evaluation_run",
    label_i18n_key="stage.op.evaluation_run.label",
    description_i18n_key="stage.op.evaluation_run.description",
    when_to_run_i18n_key="stage.op.evaluation_run.when_to_run",
    when_to_skip_i18n_key="stage.op.evaluation_run.when_to_skip",
)

STAGE_SUGGESTIONS: Final[dict[Stage, SuggestedOp]] = {
    "persona_setup": _OP_PERSONA_EDIT,
    "memory_build": _OP_MEMORY_EDIT,
    "prompt_assembly": _OP_PROMPT_PREVIEW,
    "chat_turn": _OP_CHAT_SEND,
    "post_turn_memory_update": _OP_MEMORY_TRIGGER,
    "evaluation": _OP_EVALUATION_RUN,
}


def suggested_op_for(stage: Stage) -> SuggestedOp:
    if stage not in STAGE_SUGGESTIONS:
        raise StageError("UnknownStage", f"未知阶段: {stage!r}", status=400)
    return STAGE_SUGGESTIONS[stage]


# ── stage_state lifecycle ───────────────────────────────────────────


def initial_stage_state() -> dict[str, Any]:
    """Fresh stage_state for a brand-new session.

    ``action=\"init\"`` leaves a root anchor so P18 timelines can show 'start from persona_setup'.
    """
    now = _iso_now()
    return {
        "current": "persona_setup",
        "updated_at": now,
        "history": [
            {
                "stage": "persona_setup",
                "at": now,
                "action": "init",
                "op_id": None,
                "skipped": False,
                "note": None,
            },
        ],
    }


def ensure_stage_state(session: Session) -> dict[str, Any]:
    """Lazy-init stage_state on legacy sessions (should not normally hit).

    Any session created after P14 gets ``stage_state`` set by
    :class:`SessionStore.create`; this helper only kicks in for the rare
    path where a pre-P14 session was loaded via P21 persistence without
    the field. Keeping it lenient keeps Stage chip from blank-screening
    on legacy save files.
    """
    ss = getattr(session, "stage_state", None)
    if not isinstance(ss, dict) or "current" not in ss:
        ss = initial_stage_state()
        session.stage_state = ss  # type: ignore[attr-defined]
    return ss


def current_stage(session: Session) -> Stage:
    ss = ensure_stage_state(session)
    current = ss.get("current")
    if current not in VALID_STAGES:
        python_logger().warning(
            "Session %s: stage_state.current=%r 非法, 重置为 persona_setup",
            session.id, current,
        )
        ss["current"] = "persona_setup"
        current = "persona_setup"
    return current  # type: ignore[return-value]


# ── actions ─────────────────────────────────────────────────────────


def advance(
    session: Session,
    *,
    op_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Move to the next stage. ``op_id`` is optional metadata for history.

    No business side-effect — neither LLM nor disk nor clock is touched.
    Just mutates ``session.stage_state`` and writes a JSONL log line.
    """
    ss = ensure_stage_state(session)
    from_stage = current_stage(session)
    to_stage = next_stage_of(from_stage)
    now = _iso_now()
    ss["current"] = to_stage
    ss["updated_at"] = now
    entry = {
        "stage": to_stage,
        "at": now,
        "action": "advance",
        "op_id": op_id or suggested_op_for(from_stage).op_id,
        "skipped": False,
        "note": note,
    }
    ss["history"].append(entry)
    _log(session, "stage.advance", from_stage, to_stage, entry)
    return _describe(session)


def skip(
    session: Session,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Same as advance but records ``skipped=True`` for the history entry.

    Semantically: "I acknowledge the suggested op for this stage but am
    explicitly not doing it". Useful for diagnostic sessions where the
    tester wants to jump past memory_build straight to chat_turn.
    """
    ss = ensure_stage_state(session)
    from_stage = current_stage(session)
    to_stage = next_stage_of(from_stage)
    now = _iso_now()
    ss["current"] = to_stage
    ss["updated_at"] = now
    entry = {
        "stage": to_stage,
        "at": now,
        "action": "skip",
        "op_id": suggested_op_for(from_stage).op_id,
        "skipped": True,
        "note": note,
    }
    ss["history"].append(entry)
    _log(session, "stage.skip", from_stage, to_stage, entry)
    return _describe(session)


def rewind(
    session: Session,
    target_stage: str,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Jump back (or forward) to an arbitrary stage without clearing history.

    Does **not** undo any data side-effects — messages / memory / clock
    stay put. Purely repositions the UI pointer. Intended for "I want
    to re-preview prompt_assembly before sending" type moves; actual
    data rewind belongs to P18's snapshot/rewind system.
    """
    if target_stage not in VALID_STAGES:
        raise StageError(
            "UnknownStage",
            f"未知阶段: {target_stage!r}; 合法值: {', '.join(STAGE_ORDER)}",
            status=400,
        )
    ss = ensure_stage_state(session)
    from_stage = current_stage(session)
    now = _iso_now()
    ss["current"] = target_stage
    ss["updated_at"] = now
    entry = {
        "stage": target_stage,
        "at": now,
        "action": "rewind",
        "op_id": None,
        "skipped": False,
        "note": note,
    }
    ss["history"].append(entry)
    _log(session, "stage.rewind", from_stage, target_stage, entry)
    return _describe(session)


# ── preview (P14: always unsupported) ───────────────────────────────


def preview_suggested_op(
    session: Session,
    op_id: str | None = None,
) -> dict[str, Any]:
    """Stage does not wrap dry runs; every operation raises ``PreviewUnsupported``.

    Rationale (see module docstring):
      - Memory previews use ``/api/memory/trigger/{op}`` (P10).
      - Evaluation previews use ``/api/judge/run {persist: false}``
        (P16), accessed through Evaluation → Run.
      - Persona / chat / prompt operations lack preview-without-execution semantics
        (persona editing is always safe, chat sends should not have pretend versions, and prompt preview
        already appears normally in Chat's right panel).

    Endpoints raise ``PreviewUnsupported(412)`` with directional wording so the frontend
    toast tells users to preview in the appropriate subpage.
    """
    stage = current_stage(session)
    op = suggested_op_for(stage)
    target_op = op_id or op.op_id
    if stage == "evaluation":
        redirect_hint = (
            "evaluation 的 dry-run 请去 Evaluation → Run 子页, 选 schema + 目标"
            " 消息后点 [运行评分]; 如需不写盘的一次性预览可以调"
            " /api/judge/run {persist: false}"
        )
    else:
        redirect_hint = (
            "memory op 请去 Setup → Memory 子页点 Trigger (P10 modal 预览),"
            " 其它 op 见推荐卡上的指引"
        )
    raise StageError(
        "PreviewUnsupported",
        f"阶段 {stage!r} 的推荐 op {target_op!r} 不提供 stage 层 dry-run; "
        + redirect_hint,
        status=412,
    )


# ── context snapshot ────────────────────────────────────────────────


def collect_context_snapshot(session: Session) -> dict[str, Any]:
    """Collect read-only numbers / flags that help the tester decide.

    **Never raises.** Any per-field failure is swallowed + logged +
    surfaced as an entry in ``warnings`` so that Stage chip keeps
    rendering even on a half-broken session.
    """
    warnings: list[str] = []

    def _safe(fn, fallback, label: str):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            python_logger().exception(
                "collect_context_snapshot: %s 失败: %s", label, exc,
            )
            warnings.append(f"{label}: {type(exc).__name__}")
            return fallback

    messages = getattr(session, "messages", None) or []
    user_count = _safe(
        lambda: sum(1 for m in messages if m.get("role") == "user"),
        0,
        "user_messages_count",
    )
    assistant_count = _safe(
        lambda: sum(1 for m in messages if m.get("role") == "assistant"),
        0,
        "assistant_messages_count",
    )
    last = messages[-1] if messages else None
    last_role = last.get("role") if last else None
    last_ts = last.get("timestamp") if last else None

    memory_counts = _safe(
        lambda: _scan_memory_counts(session),
        {"recent": 0, "facts": 0, "reflections": 0, "persona_facts": 0},
        "memory_counts",
    )

    persona = getattr(session, "persona", None) or {}
    persona_configured = bool(
        persona.get("character_name") or persona.get("master_name"),
    )

    pending_previews = _safe(
        lambda: sorted(
            list((getattr(session, "memory_previews", None) or {}).keys()),
        ),
        [],
        "pending_memory_previews",
    )

    script_state = getattr(session, "script_state", None)
    script_loaded = bool(script_state)
    auto_state = getattr(session, "auto_state", None)
    auto_running = bool(auto_state)

    clock = getattr(session, "clock", None)
    virtual_now = None
    pending_advance_seconds: int | None = None
    if clock is not None:
        virtual_now = _safe(
            lambda: clock.now().isoformat(timespec="seconds"),
            None,
            "virtual_now",
        )
        pending_advance_seconds = _safe(
            lambda: _clock_pending_seconds(clock),
            None,
            "virtual_pending_advance_seconds",
        )

    return {
        "messages_count": len(messages),
        "user_messages_count": user_count,
        "assistant_messages_count": assistant_count,
        "last_message_role": last_role,
        "last_message_timestamp": last_ts,
        "memory_counts": memory_counts,
        "persona_configured": persona_configured,
        "pending_memory_previews": pending_previews,
        "script_loaded": script_loaded,
        "auto_running": auto_running,
        "virtual_now": virtual_now,
        "virtual_pending_advance_seconds": pending_advance_seconds,
        "warnings": warnings,
    }


def _scan_memory_counts(session: Session) -> dict[str, int]:
    """Best-effort count of memory entries without triggering loaders.

    We read JSON files directly (same approach as memory_router P07) so
    this never kicks off any PersonaManager / FactStore first-load side
    effect. Missing files count as 0.
    """
    persona = getattr(session, "persona", None) or {}
    character = persona.get("character_name") or ""
    if not character:
        return {"recent": 0, "facts": 0, "reflections": 0, "persona_facts": 0}

    import json
    from pathlib import Path

    sandbox_root = getattr(session.sandbox, "root", None)
    if sandbox_root is None:
        return {"recent": 0, "facts": 0, "reflections": 0, "persona_facts": 0}
    mem_dir = Path(sandbox_root) / character / "memory"
    counts = {"recent": 0, "facts": 0, "reflections": 0, "persona_facts": 0}
    mapping = {
        "recent": "recent.json",
        "facts": "facts.json",
        "reflections": "reflections.json",
    }
    for key, fname in mapping.items():
        p = mem_dir / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            counts[key] = len(data)
        elif isinstance(data, dict):
            counts[key] = len(data)

    persona_path = mem_dir / "persona.json"
    if persona_path.exists():
        try:
            pd = json.loads(persona_path.read_text(encoding="utf-8"))
            if isinstance(pd, dict):
                facts_block = pd.get("facts") or []
                if isinstance(facts_block, list):
                    counts["persona_facts"] = len(facts_block)
        except Exception:
            pass
    return counts


def _clock_pending_seconds(clock: Any) -> int | None:
    """Extract ``pending_advance`` in whole seconds; None if nothing staged."""
    if clock is None:
        return None
    data = clock.to_dict() if hasattr(clock, "to_dict") else None
    if not isinstance(data, dict):
        return None
    pending = data.get("pending_advance_seconds")
    if pending is None:
        pending = data.get("pending_advance")
    if isinstance(pending, (int, float)):
        return int(pending)
    return None


# ── composite response ──────────────────────────────────────────────


def describe_stage(session: Session) -> dict[str, Any]:
    """Bundle current stage + next suggested op + context snapshot + history.

    Shape returned to ``GET /api/stage`` — kept stable; UI depends on
    field names being exactly these. If a new field is added, append
    rather than rename.
    """
    return _describe(session)


def _describe(session: Session) -> dict[str, Any]:
    stage = current_stage(session)
    op = suggested_op_for(stage)
    ss = ensure_stage_state(session)
    return {
        "current": stage,
        "updated_at": ss.get("updated_at"),
        "suggested_op": op.to_dict(),
        "context_snapshot": collect_context_snapshot(session),
        "history": list(ss.get("history") or []),
        "stages": list(STAGE_ORDER),
    }


# ── utils ───────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(
    session: Session,
    op: str,
    from_stage: Stage,
    to_stage: Stage,
    entry: dict[str, Any],
) -> None:
    try:
        session.logger.log_sync(
            op,
            payload={
                "from": from_stage,
                "to": to_stage,
                "entry": entry,
            },
        )
    except Exception as exc:  # noqa: BLE001
        python_logger().exception(
            "stage log %s failed: %s", op, exc,
        )


__all__ = [
    "STAGE_ORDER",
    "STAGE_SUGGESTIONS",
    "Stage",
    "StageError",
    "SuggestedOp",
    "VALID_STAGES",
    "advance",
    "collect_context_snapshot",
    "current_stage",
    "describe_stage",
    "ensure_stage_state",
    "initial_stage_state",
    "next_stage_of",
    "preview_suggested_op",
    "rewind",
    "skip",
    "suggested_op_for",
]
