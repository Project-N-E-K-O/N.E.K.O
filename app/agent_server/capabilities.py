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

"""Capability / flags / status snapshot helpers.

Split out of the former monolithic ``app/agent_server.py``. Note that
``_check_agent_api_gate`` intentionally stays on the package facade
(``__init__.py``): tests monkeypatch ``app.agent_server.get_config_manager``
and ``app.agent_server._check_agent_api_gate``, which only rebinds the
facade module dict — so both the gate itself and its consumers here resolve
through the facade at call time.
"""

import asyncio
import time
from typing import Callable, Dict, Any, Optional, Tuple

from . import _shared
from ._shared import (
    logger,
    _capability_reason_code,
    _set_capability,
    _bump_state_revision,
)
from ._shared import _now_iso
from .results import _emit_main_event

# How long the answer to "is there a browser on this computer" is kept. It is
# a handful of file checks, asked for from the status paths.
_BROWSER_SEARCH_TTL_SECONDS = 30.0
_browser_search: Optional[Tuple[float, bool, str]] = None

BROWSER_NOT_FOUND = "AGENT_BROWSER_NOT_FOUND"


def _browser_dependency_status() -> tuple[bool, str]:
    """Return whether there is a browser the browser tools can drive, and if not, the reason code.

    No browser comes with the application: the tools use one the user has
    (``nekopower/builtin/browser/launcher.py``). The answer is found the way
    the tools themselves pick their browser, so a browser the user named in
    the browser settings counts although the search would not find it. It is
    a settings file read and a handful of file checks (blocking: call it in
    a worker thread), and the answer is kept for a moment.
    """
    global _browser_search
    now = time.monotonic()
    if _browser_search is not None and now - _browser_search[0] < _BROWSER_SEARCH_TTL_SECONDS:
        return _browser_search[1], _browser_search[2]
    from .nekopower_builtins import provider

    try:
        found = provider.browser_installed()
    except Exception as exc:
        logger.warning("[Agent] Searching for a browser failed: %s", exc)
        found = False
    _browser_search = (now, found, "" if found else BROWSER_NOT_FOUND)
    return _browser_search[1], _browser_search[2]


def _forget_browser_search() -> None:
    """Look for a browser afresh next time; for when the browser settings changed."""
    global _browser_search
    _browser_search = None


def _agent_model_set_up() -> Tuple[bool, str]:
    """Return whether the agent model has an address, a model and a key, and if not, the reason code.

    Only what is filled in is looked at: nothing is sent to the model. Whether
    it answers shows when a task agent first uses it, and a failure then is
    reported with its reason (``nekopower.host.task_agents``). Reads the
    configuration: blocking, call it in a worker thread.
    """
    # Through the facade at call time: tests patch ``app.agent_server._check_agent_api_gate``.
    from app import agent_server as _agent_server_facade

    gate = _agent_server_facade._check_agent_api_gate()
    if gate.get("ready") is True:
        return True, ""
    reasons = gate.get("reasons") or []
    return False, _capability_reason_code(reasons[0] if reasons else "") or "AGENT_ENDPOINT_NOT_CONFIGURED"


def _keyboard_mouse_availability() -> Dict[str, Any]:
    """Return whether the keyboard-and-mouse tools can be used: the agent model is set up and input can be sent.

    Blocking (reads the configuration; the first call imports pyautogui): call it in a worker thread.
    """
    from app import agent_server as _agent_server_facade
    from .nekopower_builtins import provider

    reasons: list[str] = []
    model_ready, model_reason = _agent_model_set_up()
    if not model_ready:
        reasons.append(model_reason)
    input_ready, input_reason = provider.desktop().available()
    if not input_ready:
        reasons.append(input_reason)
    try:
        model = _agent_server_facade.get_config_manager().get_model_api_config("agent").get("model", "")
    except Exception:  # noqa: BLE001 - the name is only shown
        model = ""
    return {
        "enabled": True,
        "ready": model_ready and input_ready,
        "reasons": reasons,
        "provider": "nekopower",
        "model": model,
    }


def _browser_availability() -> Tuple[bool, str]:
    """Return whether the browser tools can be used: the agent model is set up and there is a browser to drive.

    Blocking (see ``_agent_model_set_up`` and ``_browser_dependency_status``): call it in a worker thread.
    """
    model_ready, model_reason = _agent_model_set_up()
    if not model_ready:
        return False, model_reason
    return _browser_dependency_status()


async def _refresh_tool_capabilities() -> None:
    """Say anew whether the keyboard-and-mouse and the browser tools can be used.

    For when that may have changed: the master switch came on, the
    configuration was saved. Asks no model anything.
    """
    keyboard = await asyncio.to_thread(_keyboard_mouse_availability)
    keyboard_reasons = keyboard.get("reasons") or []
    _set_capability("computer_use", bool(keyboard.get("ready")), keyboard_reasons[0] if keyboard_reasons else "")
    browser_ready, browser_reason = await asyncio.to_thread(_browser_availability)
    _set_capability("browser_use", browser_ready, browser_reason)


async def _refresh_tool_capabilities_and_tell(lanlan_name: Optional[str] = None) -> None:
    """``_refresh_tool_capabilities`` in the background, then the status is sent; never raises."""
    try:
        await _refresh_tool_capabilities()
    except Exception as exc:  # noqa: BLE001 - the tools stay as they were
        logger.warning("[Agent] Working out whether the tools can be used failed: %s", exc)
        return
    _bump_state_revision()
    await _emit_agent_status_update(lanlan_name=lanlan_name)


async def _model_images_status() -> Dict[str, Any]:
    """Return whether the agent model is sent pictures and, if it is not, why and until when.

    The keyboard-and-mouse tool and the screenshot tools are only given to a
    task agent whose model sees pictures. A model is taken not to when its
    provider said so (``nekopower/host/model_capabilities.py``); without this
    the tools would be missing with nothing to show for it.
    """
    try:
        from app import agent_server as _agent_server_facade
        from nekopower.host import mount

        config = await asyncio.to_thread(_agent_server_facade.get_config_manager().get_model_api_config, "agent")
        host = mount.peek_host()
        if host is not None:
            store = host.model_capabilities
        else:
            from nekopower.host.model_capabilities import ModelCapabilityStore
            from nekopower.host.paths import HostPaths

            store = ModelCapabilityStore(HostPaths().model_capabilities_json)
        return await store.describe(config.get("base_url"), config.get("model"))
    except Exception as exc:
        logger.debug("[Agent] What the agent model can see is not known: %s", exc)
        return {"sees": True}


async def _set_closed_browser_capability(capability_reason: Optional[str], still_current: Callable[[], bool]) -> None:
    """Say what the browser tools can do now that the browser is closed.

    ``still_current`` is asked again after the search for a browser, which
    runs in a worker thread (it reads the settings and looks at files): the
    switch may have been set anew meanwhile, and that setting's word stands.
    """
    if capability_reason is not None:
        _set_capability("browser_use", False, capability_reason)
        return
    dependency_ready, dependency_error = await asyncio.to_thread(_browser_dependency_status)
    if still_current():
        _set_capability("browser_use", dependency_ready, dependency_error)


async def _close_browser(
    *,
    capability_reason: Optional[str] = None,
    expected_lifecycle_seq: Optional[int] = None,
    update_capability: bool = True,
    leave_window: bool = False,
) -> None:
    """Close the browser the browser tools started, if they started one.

    A browser the user is using (attach mode) is only let go of. The
    provider waits for a call under way before it closes anything.

    ``leave_window`` is for when the tools stop being used (the user
    switched them off, or the agent model is no longer set up and the switch
    was put off for them): a browser window they can see is left open and
    connected to, as it is after a task that ended, and they may go on
    reading what is in it. It is closed when they close it, when they
    change the browser settings, or when the application quits. One without
    a window is closed all the same. Decided once a call that is under way
    is over.
    """
    from .nekopower_builtins import provider

    def still_current() -> bool:
        return expected_lifecycle_seq is None or _shared.Modules.browser_use_lifecycle_seq == expected_lifecycle_seq

    cancelled = False
    try:
        if leave_window:
            await provider.close_browser(keep_window=True)
        else:
            await provider.close_browser()
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:
        logger.warning("[Agent] Closing the browser failed: %s", exc)
    finally:
        if not cancelled and update_capability and still_current():
            await _set_closed_browser_capability(capability_reason, still_current)


def _agent_flags_snapshot() -> Dict[str, Any]:
    return dict(_shared.Modules.agent_flags or {})


def _collect_agent_status_snapshot() -> Dict[str, Any]:
    # Resolve through the facade at call time — tests monkeypatch
    # ``app.agent_server._check_agent_api_gate`` / ``get_config_manager``.
    from app import agent_server as _agent_server_facade
    gate = _agent_server_facade._check_agent_api_gate()
    flags = _agent_flags_snapshot()
    capabilities = dict(_shared.Modules.capability_cache or {})
    # The commissions under way, so the frontend can restore its task HUD
    # after a page refresh. Read from this process's view of the ledger;
    # nothing is opened or fetched here.
    from .nekopower_mount import hud
    from app.agent_task_hud import get_visible as _task_hud_visible

    active_tasks = hud.active()
    note = _shared.Modules.notification
    if _shared.Modules.notification:
        _shared.Modules.notification = None
    return {
        "revision": _shared.Modules.state_revision,
        "server_online": True,
        "analyzer_enabled": bool(_shared.Modules.analyzer_enabled),
        "flags": flags,
        "gate": gate,
        "capabilities": capabilities,
        "active_tasks": active_tasks,
        # Whether the main page shows the task HUD; None when never set here (``app.agent_task_hud``).
        # Memory only once the agent process has read it at start (``api_runtime``, off the event
        # loop); only if that read failed does the first snapshot read the file here.
        "task_hud_visible": _task_hud_visible(),
        "notification": note,
        "updated_at": _now_iso(),
    }


async def _emit_agent_status_update(lanlan_name: Optional[str] = None) -> None:
    try:
        snapshot = _collect_agent_status_snapshot()
        await _emit_main_event(
            "agent_status_update",
            lanlan_name,
            snapshot=snapshot,
        )
    except Exception:
        pass
