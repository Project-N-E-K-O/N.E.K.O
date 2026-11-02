"""Auto-Dialog runner: alternating two-AI conversation pipeline (P13).

PLAN §P13: Composer's fourth conversation mode. Given total_turns /SimUser
style +persona_hint /step_mode=fixed|off /step_seconds, SimUser and
Target AI **alternate** for N turns, persisting ``session.messages`` and streaming SSE progress,
with graceful **Pause / Resume / Stop** controls.

Design points
--------
* **One turn means one target AI reply**: increment ``completed_turns`` after every assistant
  completion by ``+1``; it is progress ``N/M``'s numerator. ``_decide_first_kind``
  adaptively chooses the first step:
    - ``session.messages[-1].role == 'user'`` ->target first to supply its missing reply,
      **without a paired sim step**, still counting one turn.
    - Otherwise (assistant/system/empty tail), SimUser starts, followed by target.
* **SimUser calls do not persist**: ``simulated_user.generate_simuser_message`` only creates
  SimUserDraft; its ``content`` feeds ``stream_send(user_content=
  draft.content, source=SOURCE_AUTO)``, while ``stream_send`` centrally appends
  user messages and yields ``{event:'user'}``, avoiding duplicate writes.
* **Adaptive target-first skips user append**: ``stream_send(user_content
  =None)`` (added in P13's chat_runner) infers directly from existing
  messages without another user message, cleanly supplying the outstanding assistant
  reply.
* **Fixed-step clock advancement**: before each target response call
  ``session.clock.stage_next_turn(delta=timedelta(seconds=step_seconds))``;
  ``stream_send`` consumes pending changes, placing assistant timestamps near
  user_ts +step_seconds (actual advancement precedes user append).
  Off mode leaves clocks untouched.
* **Graceful pause**: ``running_event: asyncio.Event`` defaults set; pause
  endpoints ``clear()``, while loop heads block on ``await running_event.wait()``. Crucially,
  pause **never interrupts a step** (SimUser generation/target stream), because gates
  run only between steps. Pause latency equals remaining step duration;
  benefits are intact assistant messages and no httpx aclose boundary.
* **Stop sets stop_event**: loop heads check it; generators blocked on pause
  need ``running_event.set()`` followed by ``stop_event.set()`` to wake
  and enter stop branches.
* **External controls never take session locks**: Auto-Dialog holds BUSY throughout; pause/resume/
  stop would deadlock acquiring it. Controls only read event references from ``session.auto_state``
  and call ``.set()/.clear()`` atomically under GIL, without racing runner writes
  to other fields.
* **auto_state lifetime**: only inside ``run_auto_dialog`` try/finally. Generator
  completion/errors/stop clear it to ``None``, making ``GET /state`` report idle.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, AsyncIterator, Final, Literal

from tests.testbench.chat_messages import (
    ROLE_USER,
    SOURCE_AUTO,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.pipeline.chat_runner import get_chat_backend
from tests.testbench.pipeline.messages_writer import append_message
from tests.testbench.pipeline.simulated_user import (
    STYLE_PRESETS,
    SimUserError,
    generate_simuser_message,
)
from tests.testbench.session_store import Session


# ── errors ──────────────────────────────────────────────────────────


class AutoDialogError(RuntimeError):
    """Raised when auto-dialog start / pause / stop can't proceed.

    Router ``_auto_error_to_http`` centrally maps code ->HTTP status; frontend
    ``code`` selects toast messages.

    P24 Day 7 (§12.3.F) added ``errors: list[str] | None`` for
    ``from_request`` **batch validation**, collecting multiple failures so frontends
    present individual expandable errors instead of unreadable long toast strings,
    truncated beyond 280 characters by ``_truncate``. This field is
    presentation-only, never changing HTTP status routing.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 500,
        *,
        errors: list[str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.errors = list(errors) if errors else None
        super().__init__(f"{code}: {message}")


# ── config ──────────────────────────────────────────────────────────


STEP_MODES: Final[tuple[str, ...]] = ("fixed", "off")
#: ``fixed`` stages ``step_seconds`` before each target turn; ``off`` leaves the clock unchanged.
#: PLAN originally listed fixed / linear / random / off; this phase implements fixed / off only
#: (covering the main needs: equal-length N-turn stress tests and conversation continuity without advancing time);
#: add linear / random when concrete scoring needs arise; the protocol and UI reserve space for them.

MAX_TOTAL_TURNS: Final[int] = 50
#: At most 50 turns per Auto-Dialog run, preventing an accidental 999 from exhausting api_key credit. Longer
#: stress tests can use successive runs (manually continuing with N=50 each time).

MIN_STEP_SECONDS: Final[int] = 1
MAX_STEP_SECONDS: Final[int] = 7 * 24 * 3600  # One week, sufficient even for cross-month/quarter stress tests.


@dataclass
class AutoDialogConfig:
    """Complete configuration for one Auto-Dialog start.

    Not persisted: runtime snapshot in ``session.auto_state['config']`` only. Frontends may
    read ``/state`` and refill prior config on later starts, but backends never automatically
    retain it, avoiding repeated failure configurations becoming defaults.
    """

    total_turns: int
    simuser_style: str
    step_mode: Literal["fixed", "off"]
    step_seconds: int | None = None
    simuser_persona_hint: str = ""
    simuser_extra_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_request(cls, body: dict[str, Any]) -> "AutoDialogConfig":
        """Validate/build config; failure raises :class:`AutoDialogError`.

        Router passes JSON bodies directly; validate all fields together so frontends
        see every error at once. P24 Day 7 (§12.3.F) **forwards the whole error list**:
        combined message for legacy toast compatibility plus ``errors: list[str]``
        entries shown individually in newer expandable panels.
        """
        errors: list[str] = []

        total_turns = body.get("total_turns")
        if not isinstance(total_turns, int) or total_turns < 1:
            errors.append("total_turns 必须是 ≥1 的整数")
        elif total_turns > MAX_TOTAL_TURNS:
            errors.append(f"total_turns 超过上限 {MAX_TOTAL_TURNS}")

        style = body.get("simuser_style") or ""
        if not isinstance(style, str) or style not in STYLE_PRESETS:
            errors.append(
                f"simuser_style 必须是预设之一 ({', '.join(STYLE_PRESETS)})",
            )

        step_mode = body.get("step_mode") or "off"
        if step_mode not in STEP_MODES:
            errors.append(
                f"step_mode 必须是 {'/'.join(STEP_MODES)}, got {step_mode!r}",
            )

        step_seconds = body.get("step_seconds")
        if step_mode == "fixed":
            if not isinstance(step_seconds, int):
                errors.append("step_mode=fixed 时 step_seconds 必须是整数秒")
            elif not (MIN_STEP_SECONDS <= step_seconds <= MAX_STEP_SECONDS):
                errors.append(
                    f"step_seconds 超出范围 [{MIN_STEP_SECONDS},"
                    f" {MAX_STEP_SECONDS}]"
                )
        else:
            # Ignore supplied values in off mode and normalize to zero.
            step_seconds = None

        persona_hint = body.get("simuser_persona_hint") or ""
        extra_hint = body.get("simuser_extra_hint") or ""
        if not isinstance(persona_hint, str):
            errors.append("simuser_persona_hint 必须是字符串")
        if not isinstance(extra_hint, str):
            errors.append("simuser_extra_hint 必须是字符串")

        if errors:
            raise AutoDialogError(
                "InvalidConfig",
                "Auto-Dialog 配置无效: " + "; ".join(errors),
                status=400,
                errors=errors,
            )

        # All fields have already passed validation here.
        return cls(
            total_turns=total_turns,  # type: ignore[arg-type]
            simuser_style=style,
            step_mode=step_mode,  # type: ignore[arg-type]
            step_seconds=step_seconds,
            simuser_persona_hint=persona_hint,
            simuser_extra_hint=extra_hint,
        )


# ── helpers ─────────────────────────────────────────────────────────


def _decide_first_kind(session: Session) -> Literal["simuser", "target"]:
    """Determine the first actor.

    User tail ->supply target reply without consuming a SimUser generation;
    otherwise (assistant/system/empty), let SimUser open.

    Inspect only ``role``, never ``content``: even empty placeholder users,
    unexpected because chat_runner yields only after successful append and pops failures,
    still count as existing users and defensively receive target replies.
    """
    if not session.messages:
        return "simuser"
    last = session.messages[-1]
    if last.get("role") == ROLE_USER:
        return "target"
    return "simuser"


def _public_auto_state(session: Session) -> dict[str, Any] | None:
    """External ``/state`` snapshot excluding non-JSON values such as asyncio.Event.

    Keep ``paused`` (``running_event.is_set() == False``) so frontends
    distinguish running versus paused without maintaining their own state machine.
    """
    st = session.auto_state
    if st is None:
        return None
    running: asyncio.Event | None = st.get("running_event")
    stopping: asyncio.Event | None = st.get("stop_event")
    return {
        "total_turns": st.get("total_turns"),
        "completed_turns": st.get("completed_turns", 0),
        "next_kind": st.get("next_kind"),
        "config": st.get("config"),
        "paused": bool(running is not None and not running.is_set()),
        "stopping": bool(stopping is not None and stopping.is_set()),
        "started_at_real": st.get("started_at_real"),
    }


# ── public API ──────────────────────────────────────────────────────


async def run_auto_dialog(
    session: Session,
    config: AutoDialogConfig,
) -> AsyncIterator[dict[str, Any]]:
    """Auto-Dialog main-loop async generator.

    Precondition: router holds ``session_operation(\"chat.auto_dialog.start\",
    state=BUSY)`` and validated no-session/already-running. This generator exclusively
    writes ``session.messages`` / ``session.clock`` / ``session.
    auto_state`` throughout its lifetime.

    Yields
    ------
    dict
        SSE events (router adds json.dumps and \"data: \" prefixes):

        * ``{event:'start', total_turns, config, first_kind, auto_state}``
        * ``{event:'turn_begin', index, kind, step_seconds?}``
        * ``{event:'simuser_done', message, warnings}`` - SimUser persisted.
        * ``{event:'user', message}`` - forward stream_send user events,
          the same message as simuser_done in sim mode.
        * ``{event:'wire_built', ...}`` / ``{event:'assistant_start', ...}``
          / ``{event:'delta', ...}`` / ``{event:'usage', ...}`` / ``{event:
          'assistant', message}`` - forward target stream_send events.
        * ``{event:'turn_done', completed_turns, total_turns}`` - one successful turn.
        * ``{event:'paused'}`` / ``{event:'resumed'}`` - control-state transitions.
        * ``{event:'stopped', reason: 'completed'|'user_stop'|'error',
          completed_turns, total_turns}`` - termination, the final event.
        * ``{event:'error', error:{type,message}}`` - followed by stopped(reason=
          error), for either SimUser or target failure.
    """
    if session.auto_state is not None:
        raise AutoDialogError(
            "AutoAlreadyRunning",
            "当前会话已有 Auto-Dialog 在跑, 先 stop 再 start",
            status=409,
        )

    running_event = asyncio.Event()
    running_event.set()  # Running by default; clear when paused.
    stop_event = asyncio.Event()

    first_kind = _decide_first_kind(session)

    session.auto_state = {
        "total_turns": config.total_turns,
        "completed_turns": 0,
        "next_kind": first_kind,
        "config": config.to_dict(),
        "started_at_real": time.monotonic(),
        "running_event": running_event,
        "stop_event": stop_event,
    }

    session.logger.log_sync(
        "auto_dialog.start",
        payload={
            "config": config.to_dict(),
            "first_kind": first_kind,
            "starting_message_count": len(session.messages),
        },
    )
    python_logger().info(
        "auto_dialog: session=%s N=%d style=%s step=%s first=%s",
        session.id, config.total_turns, config.simuser_style,
        f"{config.step_mode}/{config.step_seconds or 0}s",
        first_kind,
    )

    yield {
        "event": "start",
        "total_turns": config.total_turns,
        "config": config.to_dict(),
        "first_kind": first_kind,
        "auto_state": _public_auto_state(session),
    }

    reason: str = "completed"
    error_payload: dict[str, Any] | None = None
    # Snapshot terminal completion count independently of session.auto_state: finally clears
    # auto_state to None to release runtime state, but subsequent error /
    # stopped yields report actual completed_turns. Reading auto_state directly once caused
    # UI to show 0/10 in stopped events even after all 10/10 turns finished.
    final_completed: int = 0

    try:
        while True:
            # 1) Pause gate: check after the current step, never interrupting active streams.
            if not running_event.is_set():
                yield {"event": "paused"}
                # Await resume/stop; stop also sets running_event to unblock it.
                await running_event.wait()
                if not stop_event.is_set():
                    yield {"event": "resumed"}

            # 2) Stop gate: higher priority than pause; stop may follow pause.
            #    Finish the current turn before stopping. If next_kind=target and messages ends
            #    in user (SimUser appended without target reply), run one final
            #    target response before breaking, avoiding half-turns with visible user
            #    messages but no AI reply, misleading users into thinking messages never reached
            #    AI. Final-turn completion ignores stop_event because stop is already acknowledged;
            #    only clean finalization remains. For next_kind=simuser or empty messages,
            #    break directly: no outstanding reply.
            if stop_event.is_set():
                needs_final_target = (
                    session.auto_state["next_kind"] == "target"
                    and bool(session.messages)
                    and session.messages[-1].get("role") == ROLE_USER
                )
                if needs_final_target:
                    try:
                        async for ev in _run_target_turn(
                            session, config,
                            final_before_stop=True,
                        ):
                            yield ev
                    except SimUserError as exc:
                        reason = "error"
                        error_payload = {
                            "type": exc.code,
                            "message": exc.message,
                        }
                        break
                    except AutoDialogError as exc:
                        reason = "error"
                        error_payload = {
                            "type": exc.code,
                            "message": exc.message,
                        }
                        break
                    except Exception as exc:  # noqa: BLE001 - Defensive fallback.
                        reason = "error"
                        error_payload = {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        }
                        python_logger().exception(
                            "auto_dialog stop-final-turn error"
                            " (session=%s): %s",
                            session.id, exc,
                        )
                        break
                reason = "user_stop"
                break

            # 3) Already finished?
            completed = session.auto_state["completed_turns"]
            if completed >= config.total_turns:
                reason = "completed"
                break

            kind = session.auto_state["next_kind"]
            # Progress numerator counts completed targets. If target is next, completion
            # adds 1 consistently; with simuser next, only the following target completion
            # adds 1, so UI shows turn completed+1 generating during simuser steps.

            try:
                if kind == "simuser":
                    async for ev in _run_simuser_step(session, config):
                        yield ev
                    # Sim success always makes target next.
                    session.auto_state["next_kind"] = "target"
                    # Stop immediately after SimUser completion reaches the next loop's stop
                    # gate needs_final_target branch, completing target before breaking,
                    # leaving no unfinished half-turn.
                else:
                    async for ev in _run_target_turn(session, config):
                        yield ev
            except SimUserError as exc:
                reason = "error"
                error_payload = {
                    "type": exc.code,
                    "message": exc.message,
                }
                break
            except AutoDialogError as exc:
                reason = "error"
                error_payload = {
                    "type": exc.code,
                    "message": exc.message,
                }
                break
            except Exception as exc:  # noqa: BLE001 - Defensive fallback.
                reason = "error"
                error_payload = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                python_logger().exception(
                    "auto_dialog unexpected error (session=%s): %s",
                    session.id, exc,
                )
                break
    finally:
        # Snapshot final completed_turns for later stopped/error yields before
        # logging/clearing auto_state. Order matters: log_sync immediately changes auto_state
        # to None so GET /state reports idle; snapshot every field needed after
        # clearing into local variables first.
        if session.auto_state is not None:
            final_completed = session.auto_state.get("completed_turns", 0)
        session.logger.log_sync(
            "auto_dialog.end",
            payload={
                "reason": reason,
                "completed_turns": final_completed,
                "total_turns": config.total_turns,
                "error": error_payload,
            },
        )
        # 2026-04-22 Day 8 acceptance feedback: RateLimitError (429) /SimUser LlmFailed
        # and other runtime errors previously reached frontends **only through SSE**, bypassing
        # diagnostics ring buffers, topbar Err badges, and Diagnostics ->Errors.
        # Central finally handling covers all three except branches and automatically
        # benefits future ones. level="error" increments badges; warnings are lightweight advisories.
        if error_payload is not None:
            try:
                diagnostics_store.record_internal(
                    op="auto_dialog_error",
                    message=(
                        f"[auto_dialog] {error_payload.get('type', 'Error')}: "
                        f"{error_payload.get('message', '')}"
                    ),
                    level="error",
                    session_id=session.id,
                    detail={
                        "type": error_payload.get("type"),
                        "completed_turns": final_completed,
                        "total_turns": config.total_turns,
                    },
                )
            except Exception as rec_exc:  # noqa: BLE001 - Logging failures must not block.
                python_logger().warning(
                    "diagnostics_store.record_internal failed"
                    " (session=%s): %s", session.id, rec_exc,
                )
        session.auto_state = None

    if error_payload is not None:
        yield {"event": "error", "error": error_payload}

    yield {
        "event": "stopped",
        "reason": reason,
        "completed_turns": final_completed,
        "total_turns": config.total_turns,
    }


# ── step runners ────────────────────────────────────────────────────


async def _run_simuser_step(
    session: Session,
    config: AutoDialogConfig,
) -> AsyncIterator[dict[str, Any]]:
    """SimUser generates a user message, then stream_send runs target responses.

    **Only SimUser runs here**; subsequent target-step ``stream_send`` persists
    and yields user messages. Separating SimUser clarifies
    failure semantics: sim failures precede target work and leave no partial
    messages.

    Sim generation and target streaming could be **combined**: after content,
    immediately stage clocks and call stream_send. But pause gates then lose
    the ability to pause between sim and target. Keep the two
    steps independently scheduled: sim yields ``simuser_done`` without stream_send;
    next loop's target branch appends to messages.

    ⚠ This creates a partial-result window: SimUser content is not yet in messages,
    so target ``user_content=None`` sees assistant/empty tails rather than users
    and rejects. Remedy: **SimUser itself appends user messages
    to messages**, yielding ``{event:'simuser_done', message}``;
    target then uses ``user_content=None`` with an actual
    user tail. Steps remain decoupled, allowing intervening pause/stop with clear semantics.
    """
    draft = await generate_simuser_message(
        session,
        style=config.simuser_style,
        user_persona_prompt=config.simuser_persona_hint,
        extra_hint=config.simuser_extra_hint,
    )
    # P25 r7: SimUser LLM no longer stamps ``session.last_llm_wire``;
    # see ``simulated_user.py::generate_simuser_message``. Target
    # (``stream_send`` with ``source=SOURCE_AUTO``) still stamps
    # ``auto_dialog_target``, the wire testers actually care about.

    # r5 T8 prompt-injection audit: Auto-Dialog takes
    # ``stream_send(user_content=None)`` replay paths, so chat_runner scans
    # cannot see SimUser-generated text or tester-customized
    # hints inside ``simuser_persona_hint`` / ``simuser_extra_hint``.
    # One of five P0 gaps in Subagent C's r5 audit. Scan three fields
    # independently, recording each so Diagnostics ->Errors distinguishes
    # SimUser-generated output from tester-authored persona_hint matches.
    try:
        from tests.testbench.pipeline import injection_audit as _ia
        _ia.scan_many(
            {
                "draft_content": draft.content or "",
                "persona_hint": config.simuser_persona_hint or "",
                "extra_hint": config.simuser_extra_hint or "",
            },
            source_prefix="auto_dialog.simuser",
            session_id=getattr(session, "id", None),
            extra={"style": config.simuser_style},
        )
    except Exception as exc:  # noqa: BLE001
        from tests.testbench.logger import python_logger
        python_logger().warning(
            "auto_dialog simuser injection_audit skipped: %s: %s",
            type(exc).__name__, exc,
        )

    # Manually append user messages to session.messages, mirroring chat_runner stream_send's
    # make_message/append flow, but source=SOURCE_AUTO distinguishes automatically
    # generated SimUser messages from drafts generated into textarea
    # then manually sent, which use SOURCE_SIMUSER.
    from tests.testbench.chat_messages import make_message  # Lazy to avoid cycles.

    # Auto user timestamps align with current cursors, without separate clock advancement.
    # Target stream_send consumes pending deltas and may advance, putting later
    # assistant timestamps at user t0 +
    # step_seconds, matching intended semantics.
    user_msg = make_message(
        role=ROLE_USER,
        content=draft.content,
        timestamp=session.clock.now(),
        source=SOURCE_AUTO,
    )
    # P24 §12.5: Auto-Dialog uses coerce like SSE chat path. Dialog can't
    # fail mid-stream; if clock was rewound, monotonicity is preserved by
    # bumping ts forward. For Auto-Dialog the user-visible surfacing is
    # the op=timestamp_coerced diagnostics entry; auto_dialog's own SSE
    # frames are per-turn progress events and don't carry per-message
    # warnings, so users find this via Diagnostics → Errors.
    _auto_result = append_message(session, user_msg, on_violation="coerce")
    user_msg = _auto_result.msg

    session.logger.log_sync(
        "auto_dialog.simuser.done",
        payload={
            "message_id": user_msg["id"],
            "content_chars": len(draft.content),
            "style": draft.style,
            "elapsed_ms": draft.elapsed_ms,
            "token_usage": draft.token_usage,
            "warnings": draft.warnings,
        },
    )

    yield {
        "event": "simuser_done",
        "message": user_msg,
        "warnings": draft.warnings,
    }


async def _run_target_turn(
    session: Session,
    config: AutoDialogConfig,
    *,
    final_before_stop: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Run a full target turn: stage clock ->turn_begin ->_run_target_step
    (complete stream_send stream) ->completed_turns +=1 +next_kind='simuser'
    ->turn_done.

    Shared by the normal kind=='target' main-loop branch and stop-gate
    complete-current-turn branch, avoiding drift between equivalent implementations
    (formerly one main-loop copy would require another stop copy, inevitably diverging).

    ``final_before_stop=True`` only forwards an SSE ``turn_begin`` field
    enabling optional final-turn-completion UI banners. Backend
    behavior matches ``False``: once final-turn completion starts, it must finish,
    ignoring stop_event, which is already True.
    """
    completed_before = session.auto_state["completed_turns"]
    step_seconds: int | None = None
    if config.step_mode == "fixed":
        step_seconds = config.step_seconds
        session.clock.stage_next_turn(
            delta=timedelta(seconds=step_seconds),
        )
    yield {
        "event": "turn_begin",
        "index": completed_before + 1,
        "kind": "target",
        "step_seconds": step_seconds,
        "final_before_stop": final_before_stop,
    }
    # Target-first steps already end in user messages, so append no new user;
    # after sim appends user, also avoid duplicates via user_content=None.
    # Share both paths; see chat_runner.stream_send's None-branch guard.
    async for ev in _run_target_step(session):
        yield ev
    session.auto_state["completed_turns"] += 1
    session.auto_state["next_kind"] = "simuser"
    yield {
        "event": "turn_done",
        "completed_turns": session.auto_state["completed_turns"],
        "total_turns": config.total_turns,
    }


async def _run_target_step(session: Session) -> AsyncIterator[dict[str, Any]]:
    """Run target AI against the user message currently ending messages.

    Requires a role=user tail from _run_simuser_step or adaptive
    outstanding-user startup; otherwise chat_runner's user_content
    =None guard raises ValueError.

    Forward every stream_send SSE event: frontend message_stream.js already handles wire_
    built /assistant_start /delta /assistant /done /error, requiring
    no adapters. Target rendering during Auto-Dialog therefore matches manual Send
    exactly.

    stream_send failures yield ``{event:'error'}`` rather than raising; forward
    them for outer-loop ``sentinel`` termination. To avoid treating successful
    empty assistant outputs as errors, mark failed only on ``event=
    error``.
    """
    backend = get_chat_backend()
    failed_error: dict[str, Any] | None = None

    async for ev in backend.stream_send(
        session,
        user_content=None,
        source=SOURCE_AUTO,
    ):
        yield ev
        if ev.get("event") == "error":
            failed_error = ev.get("error") or {
                "type": "ChatStreamError",
                "message": "target stream failed without detail",
            }
            # Without break, stream_send already returns after error, leaving no
            # further events; break defensively anyway.
            break

    if failed_error is not None:
        raise AutoDialogError(
            failed_error.get("type") or "ChatStreamError",
            failed_error.get("message") or "Target AI 回复失败",
            status=502,
        )


# ── control (pause / resume / stop) ─────────────────────────────────


def request_pause(session: Session) -> dict[str, Any]:
    """Clear running_event, blocking the next loop head.

    No running Auto-Dialog -> ``AutoDialogError('AutoNotRunning', ...)``.
    Already paused ->idempotent; clearing twice equals once.
    """
    if session.auto_state is None:
        raise AutoDialogError(
            "AutoNotRunning",
            "当前没有正在运行的 Auto-Dialog",
            status=409,
        )
    event: asyncio.Event | None = session.auto_state.get("running_event")
    if event is None:  # Theoretically unreachable: start always sets it.
        raise AutoDialogError(
            "AutoStateCorrupted",
            "auto_state 缺 running_event, 请 stop 后重启",
            status=500,
        )
    event.clear()
    session.logger.log_sync("auto_dialog.pause.request", payload={})
    return {"status": "paused"}


def request_resume(session: Session) -> dict[str, Any]:
    """Set running_event to unblock pause gates, idempotently."""
    if session.auto_state is None:
        raise AutoDialogError(
            "AutoNotRunning",
            "当前没有正在运行的 Auto-Dialog",
            status=409,
        )
    event: asyncio.Event | None = session.auto_state.get("running_event")
    if event is None:
        raise AutoDialogError(
            "AutoStateCorrupted",
            "auto_state 缺 running_event, 请 stop 后重启",
            status=500,
        )
    event.set()
    session.logger.log_sync("auto_dialog.resume.request", payload={})
    return {"status": "running"}


def request_stop(session: Session) -> dict[str, Any]:
    """Set stop_event and running_event to awaken potentially paused generators.

    Idempotent. Actual graceful stop occurs at the next loop head: finish current
    steps, then break on stop checks. Preserve all completed events yielded meanwhile.
    """
    if session.auto_state is None:
        raise AutoDialogError(
            "AutoNotRunning",
            "当前没有正在运行的 Auto-Dialog",
            status=409,
        )
    running: asyncio.Event | None = session.auto_state.get("running_event")
    stop: asyncio.Event | None = session.auto_state.get("stop_event")
    if running is None or stop is None:
        raise AutoDialogError(
            "AutoStateCorrupted",
            "auto_state 缺 event, 请杀服务重启",
            status=500,
        )
    stop.set()
    running.set()  # Unblock generators waiting on pause.
    session.logger.log_sync("auto_dialog.stop.request", payload={})
    return {"status": "stopping"}


# ── observation ─────────────────────────────────────────────────────


def describe_auto_state(session: Session) -> dict[str, Any]:
    """Payload for ``GET /api/chat/auto_dialog/state``.

    ``is_running`` tells frontends whether to reconnect SSE or display banners.
    """
    snap = _public_auto_state(session)
    return {
        "is_running": snap is not None,
        "auto_state": snap,
    }


__all__ = [
    "AutoDialogConfig",
    "AutoDialogError",
    "MAX_STEP_SECONDS",
    "MAX_TOTAL_TURNS",
    "MIN_STEP_SECONDS",
    "STEP_MODES",
    "describe_auto_state",
    "request_pause",
    "request_resume",
    "request_stop",
    "run_auto_dialog",
]
