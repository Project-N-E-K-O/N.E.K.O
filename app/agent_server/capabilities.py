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

"""Connectivity checks plus capability / flags / status snapshot helpers.

Split out of the former monolithic ``app/agent_server.py``. Note that
``_check_agent_api_gate`` intentionally stays on the package facade
(``__init__.py``): tests monkeypatch ``app.agent_server.get_config_manager``
and ``app.agent_server._check_agent_api_gate``, which only rebinds the
facade module dict — so both the gate itself and its consumers here resolve
through the facade at call time.
"""

import json
import asyncio
import time
from typing import Callable, Dict, Any, Optional, Tuple

from . import _shared
from ._shared import (
    logger,
    _set_capability,
    _bump_state_revision,
)
from ._shared import _now_iso
from .model_probe import AgentModelProbe
from .results import _emit_main_event

# How long the answer to "is there a browser on this computer" is kept. It is
# a handful of file checks, asked for from the status paths.
_BROWSER_SEARCH_TTL_SECONDS = 30.0
_browser_search: Optional[Tuple[float, bool, str]] = None

BROWSER_NOT_FOUND = "AGENT_BROWSER_NOT_FOUND"


def _browser_dependency_status() -> tuple[bool, str]:
    """Return whether there is a browser the browser tools can drive, and if not, the reason code.

    No browser comes with the application: the tools use one the user has
    (``nekoclaw/builtin/browser/launcher.py``). The answer is found the way
    the tools themselves pick their browser, so a browser the user named in
    the browser settings counts although the search would not find it. It is
    a settings file read and a handful of file checks (blocking: call it in
    a worker thread), and the answer is kept for a moment.
    """
    global _browser_search
    now = time.monotonic()
    if _browser_search is not None and now - _browser_search[0] < _BROWSER_SEARCH_TTL_SECONDS:
        return _browser_search[1], _browser_search[2]
    try:
        from nekoclaw.builtin.browser.launcher import NoBrowserError, pick_browser
        from nekoclaw.builtin.browser.settings import BrowserSettingsStore

        try:
            pick_browser(BrowserSettingsStore().load().executable)
            found = True
        except NoBrowserError:
            found = False
    except Exception as exc:
        logger.warning("[Agent] Searching for a browser failed: %s", exc)
        found = False
    _browser_search = (now, found, "" if found else BROWSER_NOT_FOUND)
    return _browser_search[1], _browser_search[2]


def _forget_browser_search() -> None:
    """Look for a browser afresh next time; for when the browser settings changed."""
    global _browser_search
    _browser_search = None


def _keyboard_mouse_availability() -> Dict[str, Any]:
    """Return whether the keyboard-and-mouse tools can be used: the agent model and input both have to work.

    Blocking (the first call imports pyautogui): call it in a worker thread.
    """
    from .nekoclaw_builtins import provider

    probe = _shared.Modules.model_probe
    status = probe.is_available() if probe is not None else {"ready": False, "reasons": ["AGENT_CU_MODULE_NOT_LOADED"], "model": ""}
    reasons = list(status.get("reasons") or [])
    input_ready, input_reason = provider.desktop().available()
    if not input_ready:
        reasons.append(input_reason)
    return {
        "enabled": True,
        "ready": bool(status.get("ready")) and input_ready,
        "reasons": reasons,
        "provider": "nekoclaw",
        "model": status.get("model", ""),
    }


async def _model_images_status() -> Dict[str, Any]:
    """Return whether the agent model is sent pictures and, if it is not, why and until when.

    The keyboard-and-mouse tool and the screenshot tools are only given to a
    task agent whose model sees pictures. A model is taken not to when its
    provider said so (``nekoclaw/host/model_capabilities.py``); without this
    the tools would be missing with nothing to show for it.
    """
    probe = _shared.Modules.model_probe
    if probe is None:
        return {"sees": True}
    try:
        from nekoclaw.host import mount

        config = await asyncio.to_thread(probe.model_config)
        host = mount.peek_host()
        if host is not None:
            store = host.model_capabilities
        else:
            from nekoclaw.host.model_capabilities import ModelCapabilityStore
            from nekoclaw.host.paths import HostPaths

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
) -> None:
    """Close the browser the browser tools started, if they started one.

    A browser the user is using (attach mode) is only let go of. The
    provider waits for a call under way before it closes anything.
    """
    from .nekoclaw_builtins import provider

    def still_current() -> bool:
        return expected_lifecycle_seq is None or _shared.Modules.browser_use_lifecycle_seq == expected_lifecycle_seq

    cancelled = False
    try:
        await provider.close_browser()
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:
        logger.warning("[Agent] Closing the browser failed: %s", exc)
    finally:
        if not cancelled and update_capability and still_current():
            await _set_closed_browser_capability(capability_reason, still_current)


def _try_refresh_model_probe(force: bool = False) -> bool:
    """
    Best-effort refresh of the agent model probe.
    Useful when API key/model settings were fixed after agent_server startup.
    Does NOT block on LLM connectivity — call ``_fire_agent_llm_connectivity_check``
    afterwards to probe the endpoint asynchronously.
    """
    current = _shared.Modules.model_probe
    if not force and current is not None and getattr(current, "init_ok", False):
        return True
    try:
        _shared.Modules.model_probe = AgentModelProbe()
        logger.info("[Agent] Agent model probe rebuilt (connectivity pending)")
        return True
    except Exception as e:
        logger.warning(f"[Agent] Agent model probe refresh failed: {e}")
        return False


_llm_check_lock = asyncio.Lock()


def _tool_group_in_use(kind: str) -> bool:
    """Whether a commission is using the tools of ``computer_use`` or ``browser_use`` right now."""
    from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER, BUILTIN_GROUP_KEYBOARD_MOUSE

    from .nekoclaw_builtins import group_in_use

    return group_in_use(BUILTIN_GROUP_KEYBOARD_MOUSE if kind == "computer_use" else BUILTIN_GROUP_BROWSER)


def _probe_lost_a_race() -> bool:
    """Whether the probe says the agent model is unreachable while a commission is being answered by it.

    A probe that failed leaves its word on the probe (``init_ok``) whatever
    ``_fire_agent_llm_connectivity_check`` then made of it. Whoever reads
    that word to switch a tool off or to mark it lost asks this first, so
    that the one rule holds everywhere: nothing is taken from a task that is
    under way because a probe lost a race against it.
    """
    probe = _shared.Modules.model_probe
    if probe is None or getattr(probe, "init_ok", False):
        return False
    return _tool_group_in_use("computer_use") or _tool_group_in_use("browser_use")


async def _fire_agent_llm_connectivity_check(*, queue: bool = False) -> None:
    """Probe the shared Agent-LLM endpoint in a background thread.

    The keyboard-and-mouse and browser tools are driven by task agents, which
    use the ``agent`` model config, so a single connectivity check covers
    both capabilities.  Updates ``init_ok`` on the probe and refreshes the
    capability cache for *both* computer_use and browser_use.

    Uses a lock to prevent concurrent probes from racing.

    ``queue=False`` (default): early-return if another probe is in flight.
      Right for spammy event-driven callers (UI toggles / flag flips) where a
      second probe would just duplicate the in-flight one.

    ``queue=True``: wait for the lock and run anyway.  Right when the caller
      represents a *state change* that must be reflected on capability (e.g.
      BrowserUse just became available), where early-return would silently
      drop the refresh.
    """
    if not queue and _llm_check_lock.locked():
        return

    async with _llm_check_lock:
        probe = _shared.Modules.model_probe
        if probe is None:
            _set_capability("computer_use", False, "AGENT_CU_MODULE_NOT_LOADED")
            _set_capability("browser_use", False, "AGENT_CU_MODULE_NOT_LOADED")
            _bump_state_revision()
            await _emit_agent_status_update()
            return

        def _probe() -> Tuple[bool, str]:
            return probe.check_connectivity()

        # If a commission is using the keyboard and mouse or the browser right
        # now, the LLM is demonstrably reachable — the probe lost a race (rate
        # limit / transient timeout) and we must not flip flags off or post a
        # bogus "猫爪预检失败 / 已自动关闭" toast on top of a working task.
        _has_running = _tool_group_in_use

        try:
            probe_result = await asyncio.get_event_loop().run_in_executor(None, _probe)
            # Tolerate legacy bool returns in case some probe implementation
            # hasn't been migrated yet (defense-in-depth: the only real probe
            # — AgentModelProbe.check_connectivity — already returns a tuple).
            if isinstance(probe_result, tuple):
                ok, probe_reason = probe_result
            else:
                ok = bool(probe_result)
                probe_reason = "" if ok else "AGENT_LLM_UNREACHABLE"
            cu_in_flight = _has_running("computer_use")
            bu_in_flight = _has_running("browser_use")

            if not ok and (cu_in_flight or bu_in_flight):
                logger.info(
                    "[Agent] Agent-LLM probe failed but a real task is running "
                    "(cu=%s bu=%s); treating as transient and skipping demote.",
                    cu_in_flight, bu_in_flight,
                )
                _bump_state_revision()
                await _emit_agent_status_update()
                return

            reason = "" if ok else (probe_reason or "AGENT_LLM_UNREACHABLE")
            if not ok:
                _set_capability("computer_use", False, reason)
                _set_capability("browser_use", False, reason)
            else:
                # The model answers; each tool group also needs its own means:
                # input that can be sent, a browser that can be found.
                keyboard = await asyncio.to_thread(_keyboard_mouse_availability)
                keyboard_reasons = keyboard.get("reasons") or []
                _set_capability("computer_use", bool(keyboard.get("ready")), keyboard_reasons[0] if keyboard_reasons else "")
                dependency_ready, dependency_error = await asyncio.to_thread(_browser_dependency_status)
                _set_capability("browser_use", dependency_ready, "" if dependency_ready else dependency_error)

            if ok:
                logger.info("[Agent] Agent-LLM connectivity check passed")
            else:
                logger.warning("[Agent] Agent-LLM connectivity check failed: %s", reason)
                if _shared.Modules.agent_flags.get("computer_use_enabled"):
                    _shared.Modules.agent_flags["computer_use_enabled"] = False
                    _shared.Modules.notification = json.dumps({"code": "AGENT_AUTO_DISABLED_COMPUTER", "details": {"reason_code": reason}})
                if _shared.Modules.agent_flags.get("browser_use_enabled"):
                    _shared.Modules.agent_flags["browser_use_enabled"] = False
                    _shared.Modules.notification = json.dumps({"code": "AGENT_AUTO_DISABLED_BROWSER", "details": {"reason_code": reason}})
                    await _close_browser(capability_reason=reason)

            _bump_state_revision()
            await _emit_agent_status_update()
        except Exception as e:
            logger.warning("[Agent] Agent-LLM connectivity check error: %s", e)
            if _has_running("computer_use") or _has_running("browser_use"):
                # Same protection in the outer-exception path.
                _bump_state_revision()
                await _emit_agent_status_update()
                return
            _set_capability("computer_use", False, "AGENT_LLM_UNREACHABLE")
            _set_capability("browser_use", False, "AGENT_LLM_UNREACHABLE")
            if _shared.Modules.agent_flags.get("computer_use_enabled"):
                _shared.Modules.agent_flags["computer_use_enabled"] = False
            if _shared.Modules.agent_flags.get("browser_use_enabled"):
                _shared.Modules.agent_flags["browser_use_enabled"] = False
                await _close_browser(capability_reason="AGENT_LLM_UNREACHABLE")
            _shared.Modules.notification = json.dumps({"code": "AGENT_LLM_CHECK_ERROR"})
            _bump_state_revision()
            await _emit_agent_status_update()


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
    from .nekoclaw_mount import hud

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
