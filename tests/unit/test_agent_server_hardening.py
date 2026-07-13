# -*- coding: utf-8 -*-
"""Regression tests for the agent_server hardening follow-up (post PR #2265).

Covers the four pre-existing defects surfaced by review on the package split:
1. ``_patch_usage`` raised TypeError on explicit JSON null token fields.
2. ``plugin_execute_direct``'s ``_run_plugin`` left the registry entry stuck
   at "running" when result parsing raised inside the inner try.
3. ``_start_embedded_user_plugin_server`` left stale server/thread handles on
   startup failure, turning later start attempts into silent no-ops.
4. The MCP channel failure branch logged raw ``result.error`` text instead of
   metadata-only logging (privacy convention).
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. _patch_usage: explicit nulls must not raise and must be zero-filled
# ---------------------------------------------------------------------------


def test_patch_usage_tolerates_explicit_null_token_fields():
    from app.agent_server.channels.openfang import _patch_usage

    data = {"usage": {"prompt_tokens": None, "completion_tokens": None}}
    _patch_usage(data)
    assert data["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_patch_usage_recomputes_total_from_partial_nulls():
    from app.agent_server.channels.openfang import _patch_usage

    data = {"usage": {"prompt_tokens": 3, "completion_tokens": None, "total_tokens": None}}
    _patch_usage(data)
    assert data["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 0,
        "total_tokens": 3,
    }


def test_patch_usage_fills_missing_usage_object():
    from app.agent_server.channels.openfang import _patch_usage

    data = {"usage": None}
    _patch_usage(data)
    assert data["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_patch_usage_keeps_existing_values():
    from app.agent_server.channels.openfang import _patch_usage

    data = {"usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    _patch_usage(data)
    assert data["usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 7,
        "total_tokens": 12,
    }


# ---------------------------------------------------------------------------
# 2. plugin_execute_direct: parse failure must not strand status="running"
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
