# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Keep a runtime action in its original host audit context across IPC callbacks."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nekopower.host import call_audit
from nekopower.host.ledger import Ledger


@dataclass(frozen=True)
class ExecutionOutcome:
    delivered: bool
    command_completed: bool

    def __bool__(self) -> bool:
        return self.delivered


@dataclass
class _Action:
    commission: str
    session: str
    kind: str
    ended: asyncio.Future[str]
    worker: asyncio.Task[bool]
    record: call_audit.Call
    tool: str
    live: Callable[[], bool]


class ExecutionAudit:
    """One owner task per in-flight action. Only metadata crosses the bridge."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger
        self._actions: dict[str, _Action] = {}

    async def begin(
        self, commission: str, session: str, kind: str, target: str,
        permit: Callable[[call_audit.Call], Awaitable[dict[str, Any]]],
        deliver: Callable[[call_audit.Call], Awaitable[bool]],
        live: Callable[[], bool],
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        ended = loop.create_future()

        async def run() -> bool:
            async with call_audit.call(self._ledger, commission, kind, target) as record:
                ruling = await permit(record) if live() else {"allow": False}
                if not ruling.get("allow") or not live():
                    record.status = "denied"
                    record.reason = "execution_denied"
                    ready.set_result({**ruling, "allow": False})
                    return False
                self._actions[record.call_id] = _Action(commission, session, kind, ended, worker, record, target, live)
                ready.set_result({"allow": True, "receipt": record.call_id})
                try:
                    record.status = await ended
                    if record.status == "cancelled":
                        return False
                    # Failures can carry paths, stdout and other sensitive details too.
                    try:
                        allowed = await deliver(record)
                    except (sqlite3.Error, OSError, RuntimeError):
                        # The action already ran. Only its delivery failed.
                        allowed = False
                finally:
                    self._actions.pop(record.call_id, None)
            return allowed and record.closed and live()

        worker = asyncio.create_task(run())
        try:
            await asyncio.wait((ready, worker), return_when=asyncio.FIRST_COMPLETED)
            if not ready.done():
                await worker
                return {"allow": False}
            ruling = ready.result()
            if not ruling.get("allow"):
                await worker
            return ruling
        except BaseException:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise

    def active(self, commission: str, session: str, receipt: str, *, kind: str) -> bool:
        action = self._actions.get(receipt)
        return bool(action and (action.commission, action.session, action.kind) == (commission, session, kind)
                    and not action.ended.done() and action.live())

    async def finish(self, commission: str, session: str, receipt: str, status: str, *, kind: str) -> ExecutionOutcome | None:
        action = self._actions.get(receipt)
        if (
            action is None or (action.commission, action.session, action.kind) != (commission, session, kind)
            or action.ended.done() or status not in {"completed", "failed", "cancelled"}
        ):
            return None
        action.ended.set_result(status)
        delivered = await action.worker
        command_completed = action.record.closed and status == "completed" and action.tool in {"pwsh", "bash"}
        return ExecutionOutcome(delivered, command_completed)

    async def end_run(self, commission: str, discard_source: Callable[[str], None] | None = None) -> None:
        pending = [action for action in self._actions.values() if action.commission == commission]
        for action in pending:
            if not action.ended.done():
                if discard_source is not None:
                    discard_source(action.record.call_id)
                action.ended.set_result("cancelled")
        if pending:
            await asyncio.gather(*(action.worker for action in pending), return_exceptions=True)
