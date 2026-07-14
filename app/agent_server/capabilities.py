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
from typing import Dict, Any, Optional, Tuple

from . import _shared
from ._shared import (
    logger,
    BrowserUseAdapter,
    ComputerUseAdapter,
    ThrottledLogger,
    _set_capability,
    _bump_state_revision,
)
from .registry import _cleanup_task_registry, _now_iso
from .results import _emit_main_event


def _rewire_browser_use_dependents() -> None:
    """Keep task_executor in sync after BrowserUse lifecycle changes."""
    try:
        executor = _shared.Modules.task_executor
        if executor is not None and hasattr(executor, "browser_use"):
            executor.browser_use = _shared.Modules.browser_use
    except Exception:
        pass


async def _ensure_browser_use_adapter():
    """Build the heavy BrowserUse adapter only when the feature is requested."""
    current = _shared.Modules.browser_use
    if current is not None:
        return current

    if _shared.Modules.browser_use_init_lock is None:
        _shared.Modules.browser_use_init_lock = asyncio.Lock()

    async with _shared.Modules.browser_use_init_lock:
        current = _shared.Modules.browser_use
        if current is not None:
            return current
        try:
            current = await asyncio.to_thread(BrowserUseAdapter)
        except Exception as exc:
            logger.error("[Agent] BrowserUseAdapter on-demand init failed: %s", exc)
            _set_capability("browser_use", False, "AGENT_BU_MODULE_NOT_LOADED")
            return None

        _shared.Modules.browser_use = current
        _rewire_browser_use_dependents()
        logger.info("[Agent] BrowserUseAdapter ready (on-demand init)")
        return current


async def _close_browser_use_adapter(
    *,
    capability_reason: str = "AGENT_BU_MODULE_NOT_LOADED",
) -> None:
    """Close Chromium/browser-use resources and detach the singleton adapter."""
    if _shared.Modules.browser_use_init_lock is None:
        _shared.Modules.browser_use_init_lock = asyncio.Lock()

    async with _shared.Modules.browser_use_init_lock:
        current = _shared.Modules.browser_use
        if current is None:
            _rewire_browser_use_dependents()
            _set_capability("browser_use", False, capability_reason)
            return
        try:
            await current.close()
        except Exception as exc:
            logger.warning("[Agent] BrowserUseAdapter close failed: %s", exc)
        finally:
            _shared.Modules.browser_use = None
            _rewire_browser_use_dependents()
            _set_capability("browser_use", False, capability_reason)

def _rewire_computer_use_dependents() -> None:
    """Keep task_executor in sync after computer_use adapter refresh."""
    try:
        if _shared.Modules.task_executor is not None and hasattr(_shared.Modules.task_executor, "computer_use"):
            _shared.Modules.task_executor.computer_use = _shared.Modules.computer_use
    except Exception:
        pass


def _try_refresh_computer_use_adapter(force: bool = False) -> bool:
    """
    Best-effort refresh for computer-use adapter.
    Useful when API key/model settings were fixed after agent_server startup.
    Does NOT block on LLM connectivity — call ``_fire_agent_llm_connectivity_check``
    afterwards to probe the endpoint asynchronously.
    """
    current = _shared.Modules.computer_use
    if not force and current is not None and getattr(current, "init_ok", False):
        return True
    try:
        refreshed = ComputerUseAdapter()
        _shared.Modules.computer_use = refreshed
        _rewire_computer_use_dependents()
        logger.info("[Agent] ComputerUse adapter rebuilt (connectivity pending)")
        return True
    except Exception as e:
        logger.warning(f"[Agent] ComputerUse adapter refresh failed: {e}")
        return False


_llm_check_lock = asyncio.Lock()


async def _fire_agent_llm_connectivity_check(*, queue: bool = False) -> None:
    """Probe the shared Agent-LLM endpoint in a background thread.

    Both ComputerUse and BrowserUse rely on the same ``agent`` model config,
    so a single connectivity check covers both capabilities.  Updates
    ``init_ok`` on the CUA adapter and refreshes the capability cache for
    *both* computer_use and browser_use.

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
        adapter = _shared.Modules.computer_use
        if adapter is None:
            _set_capability("computer_use", False, "AGENT_CU_MODULE_NOT_LOADED")
            _set_capability("browser_use", False, "AGENT_CU_MODULE_NOT_LOADED")
            _bump_state_revision()
            await _emit_agent_status_update()
            return

        def _probe() -> Tuple[bool, str]:
            return adapter.check_connectivity()

        # If a real CUA/BU task is currently running, the LLM is demonstrably
        # reachable — the probe lost a race (shared _llm_client + rate limit /
        # transient timeout) and we must not flip flags off or post a bogus
        # "猫爪预检失败 / 已自动关闭" toast on top of a working task.
        def _has_running(kind: str) -> bool:
            try:
                for info in _shared.Modules.task_registry.values():
                    if info.get("type") == kind and info.get("status") in ("queued", "running"):
                        return True
            except Exception:
                pass
            return False

        try:
            probe_result = await asyncio.get_event_loop().run_in_executor(None, _probe)
            # Tolerate legacy bool returns in case some adapter implementation
            # hasn't been migrated yet (defense-in-depth: the only real probe
            # — computer_use.check_connectivity — already returns a tuple).
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
            _set_capability("computer_use", ok, reason)
            bu = _shared.Modules.browser_use
            if bu is None:
                _set_capability("browser_use", False, "AGENT_BU_MODULE_NOT_LOADED")
            else:
                if not ok:
                    _set_capability("browser_use", False, reason)
                elif not getattr(bu, "_ready_import", False):
                    _set_capability("browser_use", False, "AGENT_BROWSER_USE_NOT_INSTALLED")
                else:
                    _set_capability("browser_use", True, "")

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
                    await _close_browser_use_adapter(capability_reason=reason)

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
                await _close_browser_use_adapter(capability_reason="AGENT_LLM_UNREACHABLE")
            _shared.Modules.notification = json.dumps({"code": "AGENT_LLM_CHECK_ERROR"})
            _bump_state_revision()
            await _emit_agent_status_update()


def _agent_flags_snapshot() -> Dict[str, Any]:
    flags = dict(_shared.Modules.agent_flags or {})
    openclaw_capability = (_shared.Modules.capability_cache or {}).get("openclaw") or {}
    flags["openclaw_ready"] = bool(flags.get("openclaw_enabled")) and bool(
        openclaw_capability.get("ready")
    )
    return flags


def _collect_agent_status_snapshot() -> Dict[str, Any]:
    # Resolve through the facade at call time — tests monkeypatch
    # ``app.agent_server._check_agent_api_gate`` / ``get_config_manager``.
    from app import agent_server as _agent_server_facade
    gate = _agent_server_facade._check_agent_api_gate()
    flags = _agent_flags_snapshot()
    capabilities = dict(_shared.Modules.capability_cache or {})
    # Periodic cleanup of completed tasks to prevent memory leak
    # Note: _emit_agent_status_update also calls this and handles timed_out tasks
    _cleanup_task_registry()
    # Include active (queued/running) tasks so frontend can restore after page refresh
    active_tasks = []
    for tid, info in _shared.Modules.task_registry.items():
        try:
            st = info.get("status")
            if st in ("queued", "running"):
                active_tasks.append({
                    "id": tid,
                    "status": st,
                    "type": info.get("type"),
                    "start_time": info.get("start_time"),
                    "params": info.get("params", {}),
                    "session_id": info.get("session_id"),
                    "lanlan_name": info.get("lanlan_name"),
                })
        except Exception:
            continue
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
        # 先检查超时的 deferred 任务并发送 task_update 通知
        timed_out = _cleanup_task_registry()
        for task_info in timed_out:
            try:
                await _emit_main_event(
                    "task_update",
                    task_info.get("lanlan_name"),
                    task={
                        "id": task_info.get("id"),
                        "status": "failed",
                        "type": task_info.get("type"),
                        "start_time": task_info.get("start_time"),
                        "end_time": task_info.get("end_time"),
                        "error": task_info.get("error"),
                        "params": task_info.get("params", {}),
                    },
                )
            except Exception as e:
                logger.warning("[Agent] Failed to emit task_update for timed-out task %s: %s", task_info.get("id"), e)

        snapshot = _collect_agent_status_snapshot()
        await _emit_main_event(
            "agent_status_update",
            lanlan_name,
            snapshot=snapshot,
        )
    except Exception:
        pass
