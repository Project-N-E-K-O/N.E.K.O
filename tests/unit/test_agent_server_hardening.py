# -*- coding: utf-8 -*-
"""Regression tests for the agent_server hardening follow-up (post PR #2265).

Covers the pre-existing defects surfaced by review on the package split:
1. ``plugin_execute_direct``'s ``_run_plugin`` left the registry entry stuck
   at "running" when result parsing raised inside the inner try.
2. ``_start_embedded_user_plugin_server`` left stale server/thread handles on
   startup failure, turning later start attempts into silent no-ops.
3. The MCP channel failure branch logged raw ``result.error`` text instead of
   metadata-only logging (privacy convention).
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from utils.internal_http_auth import internal_http_auth_headers

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Storage recovery generation: health-only and side-effect free
# ---------------------------------------------------------------------------


def test_agent_storage_control_routes_require_internal_auth(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import api_runtime as srv

    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", False)
    monkeypatch.setattr(srv, "_agent_storage_admission_generation", 30)

    client = TestClient(srv.app)
    for path in (
        "/internal/storage/startup/block",
        "/internal/storage/startup/continue",
    ):
        assert client.post(path, json={"reason": "attacker"}).status_code == 403
        assert client.post(
            path,
            json={"reason": "attacker"},
            headers={"X-CSRF-Token": "wrong-token"},
        ).status_code == 403
        assert client.post(
            path,
            json={"reason": "attacker"},
            headers={**internal_http_auth_headers(), "Origin": "https://attacker.example"},
        ).status_code == 403

    assert srv._agent_storage_blocked_after_init is False
    assert srv._agent_storage_admission_generation == 30

    response = client.post(
        "/internal/storage/startup/block",
        json={"reason": "main_server"},
        headers=internal_http_auth_headers(),
    )
    assert response.status_code == 200
    assert srv._agent_storage_blocked_after_init is True
    assert srv._agent_storage_admission_generation == 31

    monkeypatch.setattr(srv, "get_storage_recovery_mode", lambda: "")
    monkeypatch.setattr(srv, "get_config_manager", lambda: SimpleNamespace())
    monkeypatch.setattr(srv, "get_storage_startup_blocking_reason", lambda _cm: "")
    initialize = AsyncMock(return_value=False)
    monkeypatch.setattr(srv, "ensure_agent_server_runtime_initialized", initialize)
    response = client.post(
        "/internal/storage/startup/continue",
        json={"reason": "main_server"},
        headers=internal_http_auth_headers(),
    )
    assert response.status_code == 200
    assert srv._agent_storage_blocked_after_init is False
    initialize.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_agent_recovery_generation_skips_runtime_startup(monkeypatch: pytest.MonkeyPatch):
    from app.agent_server import api_runtime as srv

    start_plugin = AsyncMock(side_effect=AssertionError("plugin host must stay stopped"))
    monkeypatch.setattr(srv, "get_storage_recovery_mode", lambda: "storage_status_unavailable")
    monkeypatch.setattr(srv, "_start_embedded_user_plugin_server", start_plugin)
    monkeypatch.setattr(srv, "_agent_runtime_init_completed", False)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", False)

    await srv.startup()

    start_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_first_run_derives_durable_selection_gate_without_launcher_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import api_runtime as srv

    # Register this key with monkeypatch even when it starts absent: product
    # code writes it directly, and the storage-root guard verifies teardown.
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(srv, "get_config_manager", lambda: SimpleNamespace())
    monkeypatch.setattr(
        srv,
        "get_storage_startup_blocking_reason",
        lambda _cm: "selection_required",
    )
    initialize = AsyncMock(side_effect=AssertionError("first-run Agent must stay stopped"))
    monkeypatch.setattr(srv, "ensure_agent_server_runtime_initialized", initialize)
    monkeypatch.setattr(srv, "_agent_runtime_init_completed", False)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", False)

    await srv.startup()

    initialize.assert_not_awaited()
    assert os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "selection_required"


@pytest.mark.asyncio
async def test_agent_recovery_generation_allows_only_health(monkeypatch: pytest.MonkeyPatch):
    from app.agent_server import api_runtime as srv

    monkeypatch.setattr(srv, "get_storage_recovery_mode", lambda: "storage_policy_unavailable")
    monkeypatch.setattr(srv, "_agent_runtime_init_completed", False)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", False)

    async def _must_not_run(_request):
        raise AssertionError("blocked request reached a runtime route")

    blocked = await srv.storage_recovery_mode_guard(
        SimpleNamespace(url=SimpleNamespace(path="/plugin/execute")),
        _must_not_run,
    )
    assert blocked.status_code == 409
    assert b'"blocking_reason":"storage_policy_unavailable"' in blocked.body

    health_response = object()

    async def _health(_request):
        return health_response

    allowed = await srv.storage_recovery_mode_guard(
        SimpleNamespace(url=SimpleNamespace(path="/health")),
        _health,
    )
    assert allowed is health_response


@pytest.mark.asyncio
async def test_agent_shared_recovery_marker_closes_initialized_fast_path(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import api_runtime as srv

    monkeypatch.setattr(srv, "_agent_runtime_init_completed", True)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", False)
    monkeypatch.setattr(srv, "get_storage_recovery_mode", lambda: "recovery_required")
    call_next = AsyncMock(side_effect=AssertionError("blocked request reached runtime route"))

    response = await srv.storage_recovery_mode_guard(
        SimpleNamespace(url=SimpleNamespace(path="/plugin/execute")),
        call_next,
    )

    assert response.status_code == 409
    call_next.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovery_mode",
    ["selection_required", "migration_pending", "recovery_required"],
)
async def test_agent_recovery_generation_can_initialize_after_storage_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    recovery_mode: str,
):
    from app.agent_server import api_runtime as srv

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", recovery_mode)
    monkeypatch.setattr(srv, "get_storage_startup_blocking_reason", lambda _cm: "")
    monkeypatch.setattr(srv, "get_config_manager", lambda: SimpleNamespace())
    initialize = AsyncMock(return_value=True)
    monkeypatch.setattr(srv, "ensure_agent_server_runtime_initialized", initialize)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", True)

    response = await srv.continue_storage_startup(None)

    assert response == {"ok": True, "initialized": True}
    assert "NEKO_STORAGE_RECOVERY_MODE" not in os.environ
    initialize.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_agent_late_continue_cannot_override_newer_block(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import api_runtime as srv

    init_started = asyncio.Event()
    allow_init = asyncio.Event()

    async def _initialize():
        init_started.set()
        await allow_init.wait()
        return True

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "recovery_required")
    monkeypatch.setattr(srv, "get_storage_startup_blocking_reason", lambda _cm: "")
    monkeypatch.setattr(srv, "get_config_manager", lambda: SimpleNamespace())
    monkeypatch.setattr(srv, "ensure_agent_server_runtime_initialized", _initialize)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", True)
    monkeypatch.setattr(srv, "_agent_storage_admission_generation", 20)

    continue_task = asyncio.create_task(srv.continue_storage_startup(None))
    await init_started.wait()
    await srv.block_storage_startup(
        srv.AgentStorageStartupRequest(
            reason="compensate",
            recovery_mode="recovery_required",
        )
    )
    allow_init.set()
    response = await continue_task

    assert response.status_code == 409
    assert srv._agent_storage_blocked_after_init is True
    assert srv._agent_storage_admission_generation == 21


@pytest.mark.asyncio
async def test_agent_blocked_shutdown_skips_runtime_persistence_without_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import api_runtime as srv

    tracker = SimpleNamespace(save=MagicMock())
    monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)
    monkeypatch.setattr(srv, "_agent_runtime_init_completed", True)
    monkeypatch.setattr(srv, "_agent_storage_blocked_after_init", True)
    plugin_stop = AsyncMock()
    plugin_server_stop = AsyncMock()
    browser_stop = AsyncMock()
    emit_status = AsyncMock()
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: tracker)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_stopped", plugin_stop)
    monkeypatch.setattr(srv, "_stop_embedded_user_plugin_server", plugin_server_stop)
    monkeypatch.setattr(srv, "_close_browser_use_adapter", browser_stop)
    monkeypatch.setattr(srv, "_emit_agent_status_update", emit_status)
    monkeypatch.setattr(srv.Modules, "computer_use", None)
    monkeypatch.setattr(srv.Modules, "browser_use", None)
    monkeypatch.setattr(srv.Modules, "agent_bridge", None)
    monkeypatch.setattr(srv.Modules, "_persistent_tasks", set())
    monkeypatch.setattr(srv.Modules, "_background_tasks", set())
    monkeypatch.setattr(srv.Modules, "active_computer_use_async_task", None)

    await srv.shutdown()

    tracker.save.assert_not_called()
    plugin_stop.assert_awaited_once_with()
    plugin_server_stop.assert_awaited_once_with()
    browser_stop.assert_awaited_once_with()
    emit_status.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# 1. plugin_execute_direct: parse failure must not strand status="running"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_execute_direct_parse_failure_still_reaches_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import api_runtime as srv

    saved_registry = dict(srv.Modules.task_registry)
    saved_handles = dict(srv.Modules.task_async_handles)
    saved_executor = srv.Modules.task_executor
    saved_analyzer = srv.Modules.analyzer_enabled
    srv.Modules.task_registry.clear()
    srv.Modules.task_async_handles.clear()

    emitted: list[tuple[str, dict]] = []

    async def _emit_main_event(event_type, lanlan_name, **payload):
        emitted.append((event_type, payload))

    async def _friendly_name(plugin_id):
        return None

    executor = MagicMock()
    executor.execute_user_plugin_direct = AsyncMock(
        return_value=SimpleNamespace(success=True, result={"run_data": {}}, error=None)
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("parse blew up")

    try:
        srv.Modules.task_executor = executor
        srv.Modules.analyzer_enabled = True
        monkeypatch.setitem(srv.Modules.agent_flags, "user_plugin_enabled", True)
        monkeypatch.setattr(srv, "_emit_main_event", _emit_main_event)
        monkeypatch.setattr(srv, "_get_plugin_friendly_name", _friendly_name)
        # parse_plugin_result is resolved from the facade globals by _run_plugin
        monkeypatch.setattr(srv, "parse_plugin_result", _boom)

        resp = await srv.plugin_execute_direct({"plugin_id": "p1", "entry_id": "e1"})
        task_id = resp["task_id"]
        bg = srv.Modules.task_async_handles.get(task_id)
        assert bg is not None
        await bg

        info = srv.Modules.task_registry[task_id]
        # The whole point: parsing raised, yet the entry must not stay "running".
        assert info["status"] == "completed"
        terminal_updates = [
            p["task"]
            for t, p in emitted
            if t == "task_update" and p.get("task", {}).get("end_time")
        ]
        assert terminal_updates and terminal_updates[-1]["status"] == "completed"
    finally:
        srv.Modules.task_registry.clear()
        srv.Modules.task_registry.update(saved_registry)
        srv.Modules.task_async_handles.clear()
        srv.Modules.task_async_handles.update(saved_handles)
        srv.Modules.task_executor = saved_executor
        srv.Modules.analyzer_enabled = saved_analyzer


# ---------------------------------------------------------------------------
# 3. _start_embedded_user_plugin_server: failure must clear stale handles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedded_plugin_server_start_failure_clears_handles(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_server import plugin_host
    from app.agent_server import _shared

    M = _shared.Modules
    saved = (M.user_plugin_http_server, M.user_plugin_http_task, M.user_plugin_app, M._plugin_server_loop)
    M.user_plugin_http_server = None
    M.user_plugin_http_task = None
    # Pre-set the app so the real plugin http_app build is skipped.
    M.user_plugin_app = MagicMock()

    class _FakeServer:
        def __init__(self, config):
            self.config = config
            self.started = False  # never comes up -> failure branch
            self.should_exit = False
            self.install_signal_handlers = lambda: None

        async def serve(self):
            return None

    fake_uvicorn = types.SimpleNamespace(
        Config=lambda *a, **k: SimpleNamespace(),
        Server=_FakeServer,
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    try:
        with pytest.raises(RuntimeError, match="embedded user plugin server failed"):
            await plugin_host._start_embedded_user_plugin_server()
        # The fix under test: stale handles must be cleared so a later start
        # attempt is not silently swallowed by the top-of-function guard.
        assert M.user_plugin_http_server is None
        assert M.user_plugin_http_task is None
    finally:
        (
            M.user_plugin_http_server,
            M.user_plugin_http_task,
            M.user_plugin_app,
            M._plugin_server_loop,
        ) = saved


# ---------------------------------------------------------------------------
# 4. MCP channel failure branch: metadata-only logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_dispatch_failure_logs_metadata_not_raw_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from app.agent_server.channels import mcp as mcp_channel

    fake_logger = MagicMock()
    monkeypatch.setattr(mcp_channel, "logger", fake_logger)

    result = SimpleNamespace(
        success=False,
        error="SECRET user text",
        task_description="do things",
        result=None,
        task_id="t-mcp",
        execution_method="mcp",
    )
    await mcp_channel.dispatch(
        result,
        messages=[],
        lanlan_name="lanlan",
        conversation_id=None,
        trigger_user_msg_sig=None,
    )

    assert fake_logger.error.called
    # repr(call) covers positional AND keyword args, so a hypothetical
    # ``logger.error(..., error=raw_text)`` cannot slip past the assertion.
    logged = " ".join(repr(call) for call in fake_logger.error.call_args_list)
    assert "SECRET user text" not in logged
    assert "error_len" in logged
    # Raw text still reaches the local print fallback.
    assert "SECRET user text" in capsys.readouterr().out
