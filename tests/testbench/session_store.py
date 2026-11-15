"""Single-active-session store with an asyncio lock + state machine.

``utils.config_manager.ConfigManager`` is a **process-wide singleton**, so
the testbench can only run one session at a time. :class:`SessionStore`
exposes a tiny process-level registry with exactly one slot (``_session``)
and a coordinating :class:`asyncio.Lock` that every mutating operation
(session create/delete, sandbox swap, future save/load/rewind/reset) must
acquire before touching shared state.

State enum values (``idle / busy / loading / saving / rewinding /
resetting``) line up with the states listed in ``PLAN.md section Technical Point 1``. The
UI reads :meth:`SessionStore.get_state` to disable risky buttons while an
operation is in flight, and long-running endpoints return HTTP 409 when
they'd collide with another owner of the lock.

P02 only implements the **minimum** needed to create/read/destroy the
slot. Snapshots (``snapshots`` list, autosave, rewind hooks) land in
later phases; the dataclass carries the fields as empty lists so later
phases don't need a schema migration.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator
from uuid import uuid4

from tests.testbench.logger import SessionLogger, python_logger
from tests.testbench.pipeline.snapshot_store import SnapshotStore
from tests.testbench.sandbox import Sandbox
from tests.testbench.virtual_clock import VirtualClock

# P22 autosave scheduler. Imported at the module level so the dataclass
# field annotation resolves cleanly; the actual runtime dependency only
# kicks in inside ``SessionStore.create`` / ``_destroy_locked`` /
# ``session_operation``.
from tests.testbench.pipeline.autosave import AutosaveScheduler


class SessionState(str, Enum):
    """Lifecycle state surfaced to the UI.

    Using :class:`str`-backed enum keeps JSON serialization straightforward
    (``state.value`` round-trips cleanly through FastAPI).
    """

    IDLE = "idle"
    BUSY = "busy"            # Short-lived ops (send/edit/memory).
    LOADING = "loading"
    SAVING = "saving"
    REWINDING = "rewinding"
    RESETTING = "resetting"


class SessionConflictError(RuntimeError):
    """Raised when a caller tries to acquire the lock but another owner
    is still in a non-idle state. Routers translate this to HTTP 409.
    """

    def __init__(self, state: SessionState, busy_op: str | None) -> None:
        self.state = state
        self.busy_op = busy_op
        super().__init__(
            f"Session busy (state={state.value}, op={busy_op or '-'}); retry shortly",
        )


# NOTE: ``Session`` is a plain dataclass — no validation here, the store is
# the only writer. Down-phase fields (messages / snapshots / eval_results /
# model_config / stage) live here as empty collections so that persistence
# code written later can serialize the whole object without guarding for
# missing attributes.
@dataclass
class Session:
    """The live testbench session.

    Fields not yet wired (messages, snapshots, …) are kept at their empty
    defaults so later phases can append without a schema bump.
    """

    id: str
    name: str
    created_at: datetime
    sandbox: Sandbox
    clock: VirtualClock
    logger: SessionLogger
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Reserved for later phases; kept here to freeze the shape.
    messages: list[dict[str, Any]] = field(default_factory=list)
    # P02-era legacy field kept as empty list for backwards compat; the real
    # snapshot timeline lives in ``snapshot_store`` (P18). Nothing in the
    # codebase should read this directly anymore — routers/UI go through
    # ``session.snapshot_store.list_metadata()``. Kept here so saved_sessions
    # files from pre-P18 code still deserialize without a migration step.
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    eval_results: list[dict[str, Any]] = field(default_factory=list)
    model_config: dict[str, Any] = field(default_factory=dict)
    # Filled by /api/persona in P05; empty dict means "never edited, form blank".
    persona: dict[str, Any] = field(default_factory=dict)

    # P14 Stage Coach guidance state. Shape:
    #   {
    #     "current": Stage,                  # One of six stages.
    #     "updated_at": iso_str,             # Latest advance/skip/rewind's
    #                                        # real-clock time, not virtual time.
    #     "history": [                       # Append-only, unbounded; rewind also appends.
    #        {"stage": Stage, "at": iso_str, "action": "init"|"advance"|
    #         "skip"|"rewind", "op_id": str | None, "skipped": bool,
    #         "note": str | None},
    #        ...,
    #     ],
    #   }
    # SessionStore.create initializes from stage_coordinator.initial_stage_state,
    # setting current=persona_setup plus an action="init" history root anchor.
    # P21 archives retain it; loaded Stage chips restore the previous stage.
    # Written only by /api/stage/{advance,skip,rewind}; chat/auto/script
    # endpoints never advance automatically, per PLAN P14: suggest, preview, await confirmation.
    stage_state: dict[str, Any] = field(default_factory=dict)

    # P10: memory op preview cache. Key = op id (e.g. "facts.extract"), value =
    # a dict ``{created_at: datetime, payload: dict, params: dict}``. A given
    # op has at most one pending preview; re-triggering overwrites. Entries
    # older than ``MEMORY_PREVIEW_TTL_SECONDS`` are evicted on access. Lives
    # in-memory only (not persisted) — previews are intentionally transient.
    memory_previews: dict[str, dict[str, Any]] = field(default_factory=dict)

    # P12 current dialog-script state; None means no loaded script. Shape:
    #   {
    #     "template_name": str,              # Loaded script name.
    #     "template_source": "builtin"|"user",
    #     "turns": [{role, content?, expected?, time?}, ...],  # Complete turns.
    #     "cursor": int,                     # Zero-based next pending turn.
    #     "turns_count": int,                # len(turns), displaying N in frontend.
    #     "pending_reference": str | None,   # Expected text accumulated across
    #                                        # role=assistant turns, populating
    #                                        # reference_content after the next LLM reply persists.
    #     "loaded_at": ISO str (virtual clock),
    #     "description": str,
    #     "user_persona_hint": str,
    #   }
    # Keep outside sandbox, since bootstrap already affects clocks, without automatic persistence;
    # script state is one-shot runtime data, cleared on session destruction/recreation.
    script_state: dict[str, Any] | None = None

    # P13 active dual-AI Auto-Dialog runtime state; None means stopped. Shape:
    #   {
    #     "total_turns": int,              # Target reply count.
    #     "completed_turns": int,          # Completed target replies, progress numerator.
    #     "next_kind": "simuser"|"target", # Next actor.
    #     "config": dict,                  # AutoDialogConfig.to_dict() snapshot.
    #     "started_at_real": float,        # time.monotonic() monitoring origin.
    #     "running_event": asyncio.Event,  # Set runs; clear pauses at gate.
    #     "stop_event": asyncio.Event,     # Set requests stop.
    #   }
    # describe()/external serializers filter both non-JSON asyncio.Event objects.
    # The dict lives only within pipeline/auto_dialog.py generator try/finally;
    # external observers read only and modify Events via pause/resume/stop HTTP
    # endpoints. Direct dict mutation races the running generator.
    auto_state: dict[str, Any] | None = None

    # P18: per-session snapshot timeline. Concrete SnapshotStore instance
    # is attached by :meth:`SessionStore.create`; we ``field(default=None)``
    # here only because dataclass field order forces the optional marker
    # (non-default fields must come before defaults). In practice every
    # live session has a non-None snapshot_store — a ``None`` means the
    # dataclass was constructed outside the store (tests) and those tests
    # must set it explicitly before any capture/rewind call.
    snapshot_store: SnapshotStore | None = None

    # P22: debounced autosave scheduler. Attached by SessionStore.create
    # and stopped from SessionStore._destroy_locked. Same "None for raw
    # dataclass in tests" convention as snapshot_store. Mutating ops go
    # through ``session_operation`` which pings notify() on exit (when
    # the op succeeded); direct writers must call notify() themselves.
    autosave_scheduler: AutosaveScheduler | None = None

    # P25 Day 2 polish r4, L36 section 7.25: preview equals reality.
    # Latest wire snapshot actually sent to LLM, not history. It represents
    # the last ground truth because some LLM paths, such as external-event
    # instruction injection/Auto-Dialog ephemeral nudges, append one-shot user
    # messages to wire without session.messages persistence. In those cases,
    # build_prompt_bundle deriving previews from session.messages
    # omits that user message and differs from actual LLM input. Prompt
    # Preview promises testers the complete actual AI-bound wire,
    # not estimated next-send wire. Thus every LLM path records
    # imminent wire here before calling; Prompt Preview Raw wire reads
    # this field instead of deriving session.messages.
    #
    # Shape; None means no LLM call yet in this session:
    #     {
    #       "wire_messages": list[dict],  # OpenAI-style, role+content
    #       "source": str,                # "chat.send" / "avatar_event" /
    #                                     # "agent_callback" / "proactive_chat" /
    #                                     # "auto_dialog_target" / "auto_dialog_simuser" ...
    #       "recorded_at_real": iso_str,     # Real clock for timestamp comparison.
    #       "recorded_at_virtual": iso_str,  # Virtual session.clock.
    #       "reply_chars": int,             # Reply characters; -1 means pending/failure.
    #       "note": str | None,             # Optional extra tags, e.g.
    #                                     # "avatar:fist+context+reward")
    #     }
    #
    # Runtime only, omitted from saves/snapshots/exports. Reloaded session
    # archives keep None until another LLM call, intentionally:
    # archives restore session contents, not previous external runtime LLM interactions,
    # whose prompts may expose complete system_prompt text.
    last_llm_wire: dict[str, Any] | None = None

    # Mutated directly by SessionStore under its own lock.
    state: SessionState = SessionState.IDLE
    busy_op: str | None = None

    def describe(self) -> dict[str, Any]:
        """Small JSON-safe dict for ``GET /api/session`` + ``/state``."""
        snap_count = (
            len(self.snapshot_store.list_metadata())
            if self.snapshot_store is not None else 0
        )
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "state": self.state.value,
            "busy_op": self.busy_op,
            "message_count": len(self.messages),
            "snapshot_count": snap_count,
            "eval_count": len(self.eval_results),
            "stage": (self.stage_state or {}).get("current", "persona_setup"),
            "stage_history_count": len((self.stage_state or {}).get("history") or []),
            "sandbox": self.sandbox.describe(),
            "clock": self.clock.to_dict(),
        }


class SessionStore:
    """Process-level single-slot session registry.

    Do not instantiate directly outside this module; use the module-level
    :func:`get_session_store` accessor so every caller shares the same
    lock + slot.
    """

    def __init__(self) -> None:
        self._session: Session | None = None
        # Guards ``self._session`` transitions (create / destroy). Short-held;
        # the per-session ``Session.lock`` is what long-running ops should use.
        self._registry_lock = asyncio.Lock()

    # ── accessors ───────────────────────────────────────────────────

    def get(self) -> Session | None:
        """Return the active session, or ``None`` if no session exists."""
        return self._session

    def require(self) -> Session:
        """Return the active session, raising if none is active."""
        if self._session is None:
            raise LookupError("No active session. POST /api/session to create one.")
        return self._session

    def get_state(self) -> dict[str, Any]:
        """Compact state dict for ``GET /api/session/state``."""
        if self._session is None:
            return {
                "has_session": False,
                "state": SessionState.IDLE.value,
                "busy_op": None,
            }
        return {
            "has_session": True,
            "session_id": self._session.id,
            "state": self._session.state.value,
            "busy_op": self._session.busy_op,
        }

    # ── creation / destruction ──────────────────────────────────────

    async def create(
        self,
        *,
        name: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Build a fresh session + sandbox and make it the active slot.

        Destroys the current session first (restoring ConfigManager and
        rmtree-ing the old sandbox) so the singleton invariant holds.

        ``session_id`` (P24 2026-04-21, user-reported): when provided
        (e.g. by load / autosave-restore paths that want to reuse the
        archive's original session_id), we pin the new session to that
        id instead of generating a fresh uuid. This keeps downstream
        per-session artifacts (autosave rolling slots, sandbox dir)
        continuous with the restored archive's history — otherwise each
        restore leaked a new session_id's worth of rolling slots on
        disk (visible to the user as "6 autosaves when I only allow 3").
        When ``None`` (default: New session, Reset), uuid4.hex[:12].

        The singleton invariant still holds: if the current active
        session happens to already share the requested id, it gets
        destroyed above (purge_sandbox=True rmtree's the old data),
        and a fresh sandbox is created under the same id below —
        ``Sandbox.create()`` is idempotent so re-using a directory
        that lingered from a prior run is also safe.
        """
        async with self._registry_lock:
            if self._session is not None:
                await self._destroy_locked(purge_sandbox=True)

            chosen_id = session_id or uuid4().hex[:12]
            sandbox = Sandbox(session_id=chosen_id).create()
            sandbox.apply()

            from tests.testbench.pipeline.stage_coordinator import (
                initial_stage_state,
            )
            session = Session(
                id=chosen_id,
                name=name or f"session-{chosen_id[:6]}",
                created_at=datetime.now(),
                sandbox=sandbox,
                clock=VirtualClock(),
                logger=SessionLogger(chosen_id),
                stage_state=initial_stage_state(),
                snapshot_store=SnapshotStore(sandbox_root=sandbox.root),
            )
            self._session = session
            # t0:init anchor — never debounced, never truncated.
            session.snapshot_store.capture(
                session, trigger="init", label="t0:init",
            )
            # P22: autosave scheduler. Attached *after* snapshot anchor
            # so the first notify() doesn't race with the capture above.
            # We don't notify on create — an empty session isn't worth
            # autosaving; the first user op will flip it dirty.
            session.autosave_scheduler = AutosaveScheduler(session)
            session.autosave_scheduler.start()
            session.logger.log_sync(
                "session.create",
                payload={"name": session.name, "sandbox": str(sandbox.root)},
            )
            python_logger().info("Session %s created (name=%s)", session.id, session.name)
            return session

    async def destroy(self, *, purge_sandbox: bool = True) -> None:
        """Destroy the active session; safe to call with no active session."""
        async with self._registry_lock:
            if self._session is None:
                return
            await self._destroy_locked(purge_sandbox=purge_sandbox)

    async def _destroy_locked(self, *, purge_sandbox: bool) -> None:
        """Internal helper; caller holds ``self._registry_lock``."""
        session = self._session
        assert session is not None

        # P22: stop the autosave scheduler *before* we grab session.lock.
        # ``close()`` does a best-effort last-gasp flush (which itself
        # takes session.lock briefly) and then cancels the background
        # task. Doing this before the outer ``async with session.lock``
        # avoids deadlock — if we tried it inside the block, the flush
        # couldn't acquire the lock we already hold.
        scheduler = session.autosave_scheduler
        if scheduler is not None:
            try:
                await scheduler.close()
            except Exception as exc:  # noqa: BLE001 - best-effort
                python_logger().warning(
                    "Session %s: autosave scheduler close failed: %s",
                    session.id, exc,
                )
            session.autosave_scheduler = None

        # Wait for any in-flight per-session op to complete. If another
        # coroutine is still inside ``session_operation``, this blocks until
        # it releases; that's intentional — tearing down a session mid-op
        # would corrupt its sandbox.
        async with session.lock:
            try:
                session.sandbox.restore()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                python_logger().exception(
                    "Session %s: sandbox restore failed: %s", session.id, exc,
                )
            if purge_sandbox:
                try:
                    session.sandbox.destroy()
                except Exception as exc:  # noqa: BLE001
                    python_logger().warning(
                        "Session %s: sandbox destroy failed: %s", session.id, exc,
                    )
            session.logger.log_sync(
                "session.destroy",
                payload={"purge_sandbox": purge_sandbox},
            )

        # P25 Day 1: drop per-session in-memory caches held by pipeline
        # modules. Deferred import so session_store stays free of
        # pipeline-layer references (it's imported during server boot
        # before external_events is usable). Best-effort: a stale cache
        # entry is harmless (at worst 100 dedupe rows * a few bytes), so
        # a failure here must never block teardown.
        try:
            from tests.testbench.pipeline.external_events import (
                discard_session_caches as _discard_external_caches,
            )
            _discard_external_caches(session.id)
        except Exception as exc:  # noqa: BLE001 - best-effort teardown
            python_logger().debug(
                "Session %s: external_events cache discard skipped: %s",
                session.id, exc,
            )

        self._session = None

    # ── per-session operation helper ────────────────────────────────

    @contextlib.asynccontextmanager
    async def session_operation(
        self,
        op_name: str,
        *,
        state: SessionState = SessionState.BUSY,
        autosave_notify: bool = True,
    ) -> AsyncIterator[Session]:
        """Acquire the per-session lock and set an op label for the UI.

        Use for any endpoint that mutates session state::

            async with store.session_operation("chat.send"):
                ...

        Raises :class:`LookupError` if no session is active and
        :class:`SessionConflictError` if the session is already busy.

        P22 — ``autosave_notify``: if True (default), calls
        ``session.autosave_scheduler.notify(op_name)`` on **successful**
        exit. Set False for read-only ops that pass through this
        context manager for state labeling (none currently; listed for
        future-proofing) and for the autosave path itself to avoid
        re-entrant "autosave notifies autosave" loops.
        """
        session = self.require()
        if session.lock.locked():
            # Fail fast instead of waiting; the UI expects a 409 so the user
            # can retry explicitly rather than hang on the request.
            raise SessionConflictError(session.state, session.busy_op)

        async with session.lock:
            prev_state = session.state
            prev_op = session.busy_op
            session.state = state
            session.busy_op = op_name
            mutation_succeeded = False
            try:
                yield session
                mutation_succeeded = True
            finally:
                session.state = prev_state
                session.busy_op = prev_op
        # ``notify`` runs *outside* the session.lock to avoid holding the
        # lock a single tick longer than needed (scheduler.notify is sync
        # and <1 μs but "after release" is a cleaner invariant for anyone
        # debugging lock contention). It's also outside the "if succeeded"
        # check? — we only notify on success: an op that raised likely
        # left partial state; autosaving it would persist that mess. The
        # caller who aborted surfaces the failure; if they then retry,
        # the next successful op notifies and covers the delta.
        if (
            mutation_succeeded
            and autosave_notify
            and session.autosave_scheduler is not None
        ):
            try:
                session.autosave_scheduler.notify(op_name)
            except Exception:  # noqa: BLE001 — scheduler never bubbles
                python_logger().exception(
                    "session_operation: autosave notify() failed (non-fatal)",
                )


# ── module-level singleton ──────────────────────────────────────────

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Return the process-wide :class:`SessionStore` instance."""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
