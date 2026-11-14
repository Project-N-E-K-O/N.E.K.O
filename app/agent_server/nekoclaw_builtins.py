# -*- coding: utf-8 -*-
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

"""Keyboard-and-mouse and browser control as capabilities of the NekoClaw gateway.

The two adapters in ``brain/`` (``ComputerUseAdapter``, ``BrowserUseAdapter``)
are kept as they are: each takes one instruction and carries it out with a loop
of its own. This module offers them to the host as built-in capabilities, so
task agents reach them through the capability gateway and nothing else does.
They are no longer channels the task brain routes to.

A capability is available while the master switch and its own switch are on
and its last readiness check passed. One call runs at a time per capability:
there is one desktop and one browser session. Cancelling a call stops what it
started.
"""

import asyncio
from typing import Any, Dict, List, Optional

from nekoclaw.host.capabilities import (
    BUILTIN_BROWSER,
    BUILTIN_KEYBOARD_MOUSE,
    BuiltinOutcome,
    Capability,
    builtin_capability,
)
from utils.result_parser import parse_browser_use_result, parse_computer_use_result

from . import _shared
from ._shared import logger

# How long a cancelled keyboard-and-mouse call waits for its worker thread to
# stop before the next call may start; pyautogui must not be driven by two.
_KEYBOARD_STOP_WAIT_SECONDS = 15.0
# How long a cancelled browser call waits for the adapter to let go and for the
# browser session to be closed, and how long the next call waits for what is
# still going after that. The adapter's own hard cancel is bounded at 12 seconds.
_BROWSER_STOP_WAIT_SECONDS = 15.0

_FLAG_OF = {
    BUILTIN_KEYBOARD_MOUSE: ("computer_use_enabled", "computer_use"),
    BUILTIN_BROWSER: ("browser_use_enabled", "browser_use"),
}


def _drop_outcome(future: "asyncio.Future[Any]") -> None:
    """Take a stopped call's late result or failure, which is of no use to anyone by now."""
    if not future.cancelled():
        future.exception()


def _count(agent_type: str) -> None:
    try:
        from utils.instrument import counter

        counter("agent_invoked", agent_type=agent_type)
    except Exception:
        pass  # Counting is best effort and must not affect the call.


class BuiltinCapabilityProvider:
    """``nekoclaw.host.capabilities.BuiltinCapabilities`` for this process."""

    def __init__(self) -> None:
        self._keyboard_lock: Optional[asyncio.Lock] = None
        # capability id -> commission whose call is being carried out.
        self._running: Dict[str, str] = {}
        # What a cancelled browser call left going when it stopped waiting:
        # the call itself, or the closing of the browser session it used.
        self._browser_leftovers: "set[asyncio.Future[Any]]" = set()
        # The adapter those were left on. Once it is closed or replaced, a
        # new call gets a session of its own, and they no longer matter.
        self._browser_leftovers_adapter: Any = None

    # -- availability -----------------------------------------------------

    def _state(self, capability_id: str) -> tuple[bool, str]:
        flag, capability = _FLAG_OF[capability_id]
        modules = _shared.Modules
        if not modules.analyzer_enabled or not (modules.agent_flags or {}).get(flag, False):
            return False, "switched off"
        cached = (modules.capability_cache or {}).get(capability) or {}
        if not cached.get("ready"):
            return False, f"not ready ({cached.get('reason') or 'unknown'})"
        return True, ""

    def available(self) -> List[Capability]:
        """Return both capabilities with their availability as of now. Does not block."""
        items = []
        for capability_id in (BUILTIN_KEYBOARD_MOUSE, BUILTIN_BROWSER):
            ready, reason = self._state(capability_id)
            items.append(builtin_capability(capability_id, available=ready, unavailable_reason=reason))
        return items

    def is_running(self, capability_id: Optional[str] = None) -> bool:
        """Whether a call is being carried out right now (of one capability, or of any)."""
        if capability_id is None:
            return bool(self._running)
        return capability_id in self._running

    # -- calls ------------------------------------------------------------

    async def run(self, capability_id: str, *, commission_id: str, arguments: Dict[str, Any]) -> BuiltinOutcome:
        """Carry out one instruction. Cancelling this stops what it started."""
        instruction = str((arguments or {}).get("instruction") or "").strip()
        if not instruction:
            return BuiltinOutcome(False, "an instruction is required")
        if capability_id == BUILTIN_KEYBOARD_MOUSE:
            return await self._run_keyboard_mouse(commission_id, instruction)
        if capability_id == BUILTIN_BROWSER:
            return await self._run_browser(commission_id, instruction)
        return BuiltinOutcome(False, f"unknown capability {capability_id}")

    async def _run_keyboard_mouse(self, commission_id: str, instruction: str) -> BuiltinOutcome:
        if self._keyboard_lock is None:
            self._keyboard_lock = asyncio.Lock()
        async with self._keyboard_lock:
            # Checked again: it may have been switched off while this waited its turn.
            ready, reason = self._state(BUILTIN_KEYBOARD_MOUSE)
            adapter = _shared.Modules.computer_use
            if not ready or adapter is None or not hasattr(adapter, "run_instruction"):
                return BuiltinOutcome(False, f"keyboard and mouse control is {reason or 'not loaded'}")
            loop = asyncio.get_running_loop()
            if hasattr(adapter, "wait_for_completion"):
                # A cancelled call gives the lock back after a bounded wait
                # even if its worker thread is still going. Two threads must
                # not drive the mouse, so this one starts only once the
                # earlier one has stopped. An idle adapter returns at once.
                stopped = await loop.run_in_executor(None, adapter.wait_for_completion, _KEYBOARD_STOP_WAIT_SECONDS)
                if not stopped:
                    return BuiltinOutcome(False, "keyboard and mouse control is still busy with an earlier call")
                # That wait can take a while; it may have been switched off meanwhile.
                ready, reason = self._state(BUILTIN_KEYBOARD_MOUSE)
                if not ready:
                    return BuiltinOutcome(False, f"keyboard and mouse control is {reason}")
            _count("cua")
            self._running[BUILTIN_KEYBOARD_MOUSE] = commission_id
            try:
                # run_instruction blocks until the task is over.
                result = await loop.run_in_executor(None, adapter.run_instruction, instruction)
            except asyncio.CancelledError:
                adapter.cancel_running()
                # The worker thread is still going; the next call must not
                # start while it drives the mouse.
                if hasattr(adapter, "wait_for_completion"):
                    try:
                        stopped = await asyncio.shield(
                            loop.run_in_executor(None, adapter.wait_for_completion, _KEYBOARD_STOP_WAIT_SECONDS)
                        )
                        if not stopped:
                            logger.warning(
                                "[Builtin] keyboard and mouse worker did not stop within %.0fs",
                                _KEYBOARD_STOP_WAIT_SECONDS,
                            )
                    except asyncio.CancelledError:
                        pass  # Cancelled again while waiting; the cancellation is raised just below.
                raise
            except Exception as exc:
                # What an adapter raises can quote the screen or the model; only its type is logged.
                logger.error("[Builtin] keyboard and mouse call failed (exc_type=%s)", type(exc).__name__)
                return BuiltinOutcome(False, f"keyboard and mouse control failed: {type(exc).__name__}")
            finally:
                if self._running.get(BUILTIN_KEYBOARD_MOUSE) == commission_id:
                    del self._running[BUILTIN_KEYBOARD_MOUSE]
        if result is None:
            result = {"success": True}
        elif isinstance(result, dict) and "success" not in result:
            result["success"] = True
        success = bool(result.get("success", False)) if isinstance(result, dict) else False
        _ok, detail = parse_computer_use_result(result)
        return BuiltinOutcome(success, detail or ("done" if success else "it did not finish"))

    async def _run_browser(self, commission_id: str, instruction: str) -> BuiltinOutcome:
        # Late import: ``capabilities`` imports this module's provider.
        from .capabilities import _ensure_browser_use_adapter

        if _shared.Modules.browser_use_dispatch_lock is None:
            _shared.Modules.browser_use_dispatch_lock = asyncio.Lock()
        async with _shared.Modules.browser_use_dispatch_lock:
            ready, reason = self._state(BUILTIN_BROWSER)
            if not ready:
                return BuiltinOutcome(False, f"browser control is {reason}")
            adapter = await _ensure_browser_use_adapter()
            if adapter is None:
                return BuiltinOutcome(False, "browser control is not available")
            # A cancelled call may still be closing the browser session it
            # used. A call that started now would be given that same session
            # and have it closed under it.
            if not await self._earlier_browser_call_is_over(adapter):
                return BuiltinOutcome(False, "browser control is still busy with an earlier call")
            # Building the adapter and that wait take a while; it may have been switched off meanwhile.
            ready, reason = self._state(BUILTIN_BROWSER)
            if not ready:
                return BuiltinOutcome(False, f"browser control is {reason}")
            _count("browser")
            self._running[BUILTIN_BROWSER] = commission_id
            # Run apart from this coroutine: a cancelled call must be able to
            # act at once instead of waiting for the adapter to unwind first.
            run = asyncio.ensure_future(adapter.run_instruction(instruction))
            try:
                result = await asyncio.shield(run)
            except asyncio.CancelledError:
                if not run.done():
                    await self._stop_browser_run(adapter, run)
                raise
            except Exception as exc:
                logger.warning("[Builtin] browser call failed (exc_type=%s)", type(exc).__name__)
                return BuiltinOutcome(False, f"browser control failed: {type(exc).__name__}")
            finally:
                if self._running.get(BUILTIN_BROWSER) == commission_id:
                    del self._running[BUILTIN_BROWSER]
        success = bool(result.get("success", False)) if isinstance(result, dict) else False
        _ok, detail = parse_browser_use_result(result)
        return BuiltinOutcome(success, detail or ("done" if success else "it did not finish"))

    async def _stop_browser_run(self, adapter: Any, run: "asyncio.Future[Any]") -> None:
        """Stop a browser call that was cancelled while the adapter was still at work.

        The flag alone only takes effect at the adapter's next step. A call
        stuck in the browser (a stalled connection, a page that never loads)
        would not see it and would keep the lock every later browser call
        needs. So the adapter's hard cancel is started as well: it closes the
        browser session, which makes whatever the call is waiting on fail.

        The lock is held until the call has let go and the session is closed,
        for a bounded time: a call that started while the session was still
        being closed would have it closed under it. What is still going when
        this stops waiting is remembered, and the next call waits for it.
        """
        try:
            adapter.cancel_running()
        except Exception as exc:
            logger.debug("[Builtin] browser cancel flag failed: %s", exc)
        closing = None
        hard_cancel = getattr(adapter, "cancel", None)
        if callable(hard_cancel):
            try:
                closing = _shared._create_tracked_task(hard_cancel())
            except Exception as exc:
                logger.warning("[Builtin] browser hard cancel not started (exc_type=%s)", type(exc).__name__)
        run.cancel()
        run.add_done_callback(_drop_outcome)
        going = {run} if closing is None else {run, closing}
        # Remembered before the wait: it may run out of time, or be cancelled once more.
        if self._browser_leftovers_adapter is not adapter:
            self._browser_leftovers = set()
            self._browser_leftovers_adapter = adapter
        self._browser_leftovers |= going
        try:
            _done, going = await asyncio.shield(asyncio.wait(going, timeout=_BROWSER_STOP_WAIT_SECONDS))
        except asyncio.CancelledError:
            return
        if going:
            logger.warning(
                "[Builtin] browser call or the closing of its session still going after %.0fs;"
                " the next call waits for it",
                _BROWSER_STOP_WAIT_SECONDS,
            )

    async def _earlier_browser_call_is_over(self, adapter: Any) -> bool:
        """Wait, for a bounded time, for what a cancelled browser call left going on ``adapter``; False if it still is.

        What was left on an adapter that has since been closed or replaced
        is dropped: ``adapter`` is a new one with a session of its own, which
        nothing left going can close. Without this, a call that never ends
        would hold up every later one until a restart.
        """
        if self._browser_leftovers_adapter is not adapter:
            self._browser_leftovers = set()
            self._browser_leftovers_adapter = None
            return True
        going = {item for item in self._browser_leftovers if not item.done()}
        if going:
            _done, going = await asyncio.wait(going, timeout=_BROWSER_STOP_WAIT_SECONDS)
        self._browser_leftovers = set(going)
        if not going:
            self._browser_leftovers_adapter = None
        return not going


# One per process; its state (what is running) is this process's.
provider = BuiltinCapabilityProvider()
