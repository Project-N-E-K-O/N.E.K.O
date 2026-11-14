# -*- coding: utf-8 -*-
"""Regression tests for the agent_server hardening follow-up (post PR #2265).

Covers the pre-existing defect surfaced by review on the package split that
is still part of this process: ``_start_embedded_user_plugin_server`` left
stale server/thread handles on startup failure, turning later start attempts
into silent no-ops. (The direct plugin-execute route and the MCP channel the
other two cases guarded were removed with the per-turn analyzer.)
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _start_embedded_user_plugin_server: failure must clear stale handles
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


@pytest.mark.asyncio
async def test_embedded_plugin_server_ready_after_startup_and_stops(monkeypatch):
    import threading
    from app.agent_server import plugin_host, _shared

    modules = _shared.Modules
    fields = ("user_plugin_http_server", "user_plugin_http_task", "user_plugin_app", "_plugin_server_loop")
    saved = {name: getattr(modules, name) for name in fields}
    modules.user_plugin_http_server = None
    modules.user_plugin_http_task = None
    modules.user_plugin_app = MagicMock()
    exit_event = threading.Event()

    class FakeServer:
        def __init__(self, config):
            self.started = False
            self.should_exit = False

        async def startup(self, sockets=None):
            # This represents lifespan completion and socket binding.
            self.started = True

        async def serve(self):
            await self.startup()
            await asyncio.to_thread(exit_event.wait)

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(
        Config=lambda *a, **k: SimpleNamespace(), Server=FakeServer
    ))
    thread = None
    try:
        await plugin_host._start_embedded_user_plugin_server()
        thread = modules.user_plugin_http_task
        assert modules.user_plugin_http_server.started
        assert thread.is_alive()
        exit_event.set()
        await plugin_host._stop_embedded_user_plugin_server()
        assert not thread.is_alive()
        assert modules.user_plugin_http_task is None
        assert modules.user_plugin_http_server is None
    finally:
        exit_event.set()
        if thread is not None:
            await asyncio.to_thread(thread.join, 5.0)
        for name, value in saved.items():
            setattr(modules, name, value)
