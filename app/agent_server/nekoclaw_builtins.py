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

_FLAG_OF = {
    BUILTIN_KEYBOARD_MOUSE: ("computer_use_enabled", "computer_use"),
    BUILTIN_BROWSER: ("browser_use_enabled", "browser_use"),
}


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
            _count("cua")
            loop = asyncio.get_running_loop()
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
                        pass
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
            _count("browser")
            self._running[BUILTIN_BROWSER] = commission_id
            try:
                result = await adapter.run_instruction(instruction)
            except asyncio.CancelledError:
                try:
                    adapter.cancel_running()
                except Exception as exc:
                    logger.debug("[Builtin] browser cancel flag failed: %s", exc)
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


# One per process; its state (what is running) is this process's.
provider = BuiltinCapabilityProvider()
