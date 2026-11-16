# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Which runtime a commission's task agent runs in (design sections 7 and 9).

There are two: the built-in runtime, which is always there, and dsh, which
a user may have downloaded (``nekoclaw.dsh``). Each commission is given one
when its task agent is first started, and keeps it:

* A commission taken up again goes back to the runtime that holds what was
  said in its session (``begun_in``; the caller tells, from what is on the
  disk): no other runtime can read it. Only when that is dsh and dsh is gone
  (removed, or cannot be started) does it go to the built-in runtime, where
  it starts over from its commission.
* A task agent started by a task agent runs where the one that started it
  runs.
* Any other commission runs in dsh when dsh is installed, the user has not
  switched it off, and its model can be served there
  (``nekoclaw.dsh.forward.unsupported_reason``); otherwise in the built-in
  runtime.

dsh is made ready before a commission is given to it: its process is
brought up, and that it loaded the permission guard is confirmed
(``DshRuntime.prepare``). When that fails the commission runs in the
built-in runtime instead and the reason is kept; dsh is not tried again for
new commissions for a while (``DSH_RETRY_SECONDS``), so that a dsh that
cannot start does not hold up every commission for the time it takes to
find that out. The same holds when dsh, though it was made ready, then
opens no session for the commission: the host starts it in the built-in
runtime (``builtin``) and tells this (``dsh_refused``).

A user without dsh is not touched by any of this: dsh's driver is not
imported, no file is looked at beyond whether dsh is installed, and the
built-in runtime is created exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

RUNTIME_BUILTIN = "builtin"
RUNTIME_DSH = "dsh"

# Builds a runtime from the event sink and the host callbacks.
RuntimeFactory = Callable[[Callable[[dict[str, Any]], None], Any], Any]
# Builds the dsh runtime from the event sink, the host callbacks and the installation.
DshRuntimeFactory = Callable[[Callable[[dict[str, Any]], None], Any, Any], Any]


def default_dsh_runtime_factory(on_event: Callable[[dict[str, Any]], None], host: Any, installation: Any) -> Any:
    """Return the dsh runtime of an installation."""
    from nekoclaw.dsh import DshRuntime

    return DshRuntime(on_event, host, launch=installation.launch)


class Runtimes:
    """The runtimes of one host, each created when first needed, and the choice between them."""

    def __init__(
        self,
        *,
        on_event: Callable[[dict[str, Any]], None],
        callbacks: Callable[[], Any],
        builtin_factory: RuntimeFactory,
        dsh: Any = None,
        dsh_factory: DshRuntimeFactory = default_dsh_runtime_factory,
    ) -> None:
        self._on_event = on_event
        self._callbacks = callbacks
        self._builtin_factory = builtin_factory
        # ``nekoclaw.dsh.DshInstallation``, or None: then there is only the built-in runtime.
        self._dsh = dsh
        self._dsh_factory = dsh_factory
        self._runtimes: dict[str, Any] = {}
        # Held while dsh is made ready for a commission, and while dsh is
        # ended: one at a time. A commission that runs in the built-in
        # runtime never waits for it.
        self._dsh_lock = asyncio.Lock()
        # Why dsh could not be used the last time it was tried, and when (monotonic).
        self.dsh_error: str | None = None
        self._dsh_failed_at: float | None = None

    # -- state ------------------------------------------------------------

    @property
    def started(self) -> bool:
        """Whether any runtime was created."""
        return bool(self._runtimes)

    def get(self, name: str | None) -> Any:
        """Return a runtime that was created, or None."""
        return self._runtimes.get(name or "")

    def created(self) -> list[tuple[str, Any]]:
        return list(self._runtimes.items())

    def builtin(self) -> Any:
        """Return the built-in runtime, creating it when this is its first use."""
        runtime = self._runtimes.get(RUNTIME_BUILTIN)
        if runtime is None:
            runtime = self._builtin_factory(self._on_event, self._callbacks())
            self._runtimes[RUNTIME_BUILTIN] = runtime
        return runtime

    # -- choosing ---------------------------------------------------------

    async def _wanted(self, begun_in: str | None, parent_runtime: str | None) -> tuple[str, bool]:
        """Return the runtime a commission is to run in, and whether that is where it began.

        Reading whether dsh is installed looks at the disk, so it is done in
        a worker thread.
        """
        dsh = self._dsh
        if dsh is None:
            return RUNTIME_BUILTIN, begun_in == RUNTIME_BUILTIN
        if begun_in in (RUNTIME_BUILTIN, RUNTIME_DSH):
            # Where it began, whatever the user has set since.
            if begun_in == RUNTIME_DSH and not await asyncio.to_thread(dsh.installed):
                return RUNTIME_BUILTIN, False
            return str(begun_in), True
        if parent_runtime in (RUNTIME_BUILTIN, RUNTIME_DSH):
            if parent_runtime == RUNTIME_DSH and not await asyncio.to_thread(dsh.installed):
                return RUNTIME_BUILTIN, False
            return str(parent_runtime), False
        return (RUNTIME_DSH if await asyncio.to_thread(dsh.wanted) else RUNTIME_BUILTIN), False

    def _dsh_rested(self) -> bool:
        """Whether dsh may be tried for a new commission: it did not just fail."""
        from config.nekoclaw_settings import DSH_RETRY_SECONDS

        failed_at = self._dsh_failed_at
        return failed_at is None or time.monotonic() - failed_at >= DSH_RETRY_SECONDS

    async def choose(
        self, model: Any, *, begun_in: str | None = None, parent_runtime: str | None = None
    ) -> tuple[str, Any, str | None]:
        """Return the runtime a commission's task agent is started in.

        ``model`` is the session's ``ModelConfig``; ``begun_in`` the runtime
        the commission ran in before, if it did; ``parent_runtime`` the
        runtime of the task agent that started it, if one did. Returns the
        runtime's name, the runtime, and, when the commission was to run in
        dsh and runs in the built-in runtime instead, why.
        """
        wanted, resumes = await self._wanted(begun_in, parent_runtime)
        if wanted != RUNTIME_DSH:
            note = "dsh is no longer installed" if begun_in == RUNTIME_DSH else None
            return RUNTIME_BUILTIN, self.builtin(), note
        from nekoclaw.dsh import DshUnavailable
        from nekoclaw.dsh.forward import unsupported_reason

        unsupported = unsupported_reason(model)
        if unsupported is not None:
            # Nothing is wrong with dsh: this model is not served there.
            return RUNTIME_BUILTIN, self.builtin(), unsupported
        async with self._dsh_lock:
            # Looked at here, after waiting: dsh may have been removed
            # meanwhile (``retire_dsh``), which is no failure of dsh.
            if not await asyncio.to_thread(self._dsh.installed):
                note = "dsh is no longer installed" if begun_in == RUNTIME_DSH else None
                return RUNTIME_BUILTIN, self.builtin(), note
            # And when dsh just failed for the commission before this one,
            # this one does not try it again.
            if not resumes and not self._dsh_rested():
                return RUNTIME_BUILTIN, self.builtin(), f"dsh could not be used a moment ago: {self.dsh_error}"
            try:
                runtime = self._runtimes.get(RUNTIME_DSH)
                if runtime is None:
                    runtime = self._dsh_factory(self._on_event, self._callbacks(), self._dsh)
                    self._runtimes[RUNTIME_DSH] = runtime
                # Brings its process up and confirms the permission guard.
                await runtime.prepare(model)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - whatever keeps dsh from working, the commission is still done
                reason = str(exc) if isinstance(exc, DshUnavailable) else f"dsh failed: {type(exc).__name__}: {exc}"
                self._dsh_failed_at = time.monotonic()
                self.dsh_error = reason
                logger.warning(
                    "dsh cannot be used, the built-in runtime takes the commission: %s",
                    reason,
                    exc_info=not isinstance(exc, DshUnavailable),
                )
                return RUNTIME_BUILTIN, self.builtin(), reason
            self._dsh_failed_at = None
            self.dsh_error = None
            return RUNTIME_DSH, runtime, None

    def dsh_refused(self, reason: str) -> None:
        """Note that dsh, though it was made ready, opened no session for a commission.

        dsh is then not tried for new commissions for a while, as when it
        could not be made ready.
        """
        self._dsh_failed_at = time.monotonic()
        self.dsh_error = reason

    # -- shutting down ----------------------------------------------------

    async def retire_dsh(
        self, in_use: Callable[[], bool], then: Callable[[], Awaitable[Any]] | None = None
    ) -> bool:
        """End the dsh runtime and its process unless ``in_use()`` says a session is in it; returns whether it is ended.

        Waits for a commission that is being given dsh right now, and asks
        ``in_use`` after that: the caller notes a session's runtime as soon
        as ``choose`` has returned it, so that commission is seen. ``then``
        is awaited once dsh is ended, before any commission may start it
        again: removing or installing its program. The next commission for
        dsh creates the runtime anew.
        """
        async with self._dsh_lock:
            if in_use():
                return False
            runtime = self._runtimes.pop(RUNTIME_DSH, None)
            if runtime is not None:
                await runtime.shutdown()
            if then is not None:
                await then()
            return True

    def take_all(self) -> list[tuple[str, Any]]:
        """Return the created runtimes and forget them; the next use creates new ones."""
        runtimes, self._runtimes = list(self._runtimes.items()), {}
        return runtimes
