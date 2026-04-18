"""Single-active-session store with an asyncio lock + state machine.

``utils.config_manager.ConfigManager`` is a **process-wide singleton**, so
the testbench can only run one session at a time. :class:`SessionStore`
exposes a tiny process-level registry with exactly one slot (``_session``)
and a coordinating :class:`asyncio.Lock` that every mutating operation
(session create/delete, sandbox swap, future save/load/rewind/reset) must
acquire before touching shared state.

State enum values (``idle / busy / loading / saving / rewinding /
resetting``) line up with the states listed in ``PLAN.md §技术点 1``. The
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
from tests.testbench.sandbox import Sandbox
from tests.testbench.virtual_clock import VirtualClock


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
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    eval_results: list[dict[str, Any]] = field(default_factory=list)
    model_config: dict[str, Any] = field(default_factory=dict)
    # Filled by /api/persona in P05; empty dict means "never edited, form blank".
    persona: dict[str, Any] = field(default_factory=dict)
    stage: str = "persona_setup"

    # Mutated directly by SessionStore under its own lock.
    state: SessionState = SessionState.IDLE
    busy_op: str | None = None

    def describe(self) -> dict[str, Any]:
        """Small JSON-safe dict for ``GET /api/session`` + ``/state``."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "state": self.state.value,
            "busy_op": self.busy_op,
            "message_count": len(self.messages),
            "snapshot_count": len(self.snapshots),
            "eval_count": len(self.eval_results),
            "stage": self.stage,
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

    async def create(self, *, name: str | None = None) -> Session:
        """Build a fresh session + sandbox and make it the active slot.

        Destroys the current session first (restoring ConfigManager and
        rmtree-ing the old sandbox) so the singleton invariant holds.
        """
        async with self._registry_lock:
            if self._session is not None:
                await self._destroy_locked(purge_sandbox=True)

            session_id = uuid4().hex[:12]
            sandbox = Sandbox(session_id=session_id).create()
            sandbox.apply()

            session = Session(
                id=session_id,
                name=name or f"session-{session_id[:6]}",
                created_at=datetime.now(),
                sandbox=sandbox,
                clock=VirtualClock(),
                logger=SessionLogger(session_id),
            )
            self._session = session
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

        self._session = None

    # ── per-session operation helper ────────────────────────────────

    @contextlib.asynccontextmanager
    async def session_operation(
        self,
        op_name: str,
        *,
        state: SessionState = SessionState.BUSY,
    ) -> AsyncIterator[Session]:
        """Acquire the per-session lock and set an op label for the UI.

        Use for any endpoint that mutates session state::

            async with store.session_operation("chat.send"):
                ...

        Raises :class:`LookupError` if no session is active and
        :class:`SessionConflictError` if the session is already busy.
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
            try:
                yield session
            finally:
                session.state = prev_state
                session.busy_op = prev_op


# ── module-level singleton ──────────────────────────────────────────

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Return the process-wide :class:`SessionStore` instance."""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
