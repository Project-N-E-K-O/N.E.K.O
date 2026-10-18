"""Execute real startup hooks with every app/storage/network boundary replaced."""

import ast
import asyncio
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from typing import Any

import pytest

from services.chat_sync import runtime as chat_sync_runtime


def hooks(monkeypatch, *, blocked=False, fail_initialization=False):
    """Load only the real hooks, avoiding import-time application or user state."""
    path = Path(__file__).resolve().parents[2] / "app/main_server/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name in {"on_startup", "release_storage_startup_barrier"}]
    for function in functions:
        function.decorator_list = []
    order = []

    async def initialize(**_kwargs):
        order.append("initialize")
        if fail_initialization:
            raise RuntimeError("synthetic initialization failure")
        return True

    async def continuation(_reason):
        order.append("continue")

    async def compensation(_reason):
        order.append("compensate")

    async def voice(_manager):
        order.append("voice")

    voice_module = ModuleType("app.main_server.voice_identity_runtime")
    voice_module.initialize_voice_identity_runtime = voice
    monkeypatch.setitem(sys.modules, voice_module.__name__, voice_module)
    monkeypatch.setattr(chat_sync_runtime, "schedule_desktop_chat_sync_start", lambda _manager: order.append("chat_sync"))
    namespace = {
        "__package__": "app.main_server", "Any": Any, "asyncio": asyncio, "_IS_MAIN_PROCESS": True,
        "importlib": SimpleNamespace(import_module=lambda _: SimpleNamespace(runtime=SimpleNamespace())),
        "configure_default_executor": lambda *_args, **_kwargs: None, "logger": Mock(),
        "init_shared_state": lambda **_kwargs: None, "set_steamworks_initializer": lambda _: None,
        "os": SimpleNamespace(environ={}), "_start_debug_health_watchdog": lambda: None,
        "get_storage_startup_blocking_reason": lambda _: "selection_required" if blocked else None,
        "_enable_main_storage_limited_mode": lambda _: order.append("blocked"),
        "_disable_main_storage_limited_mode": lambda: order.append("released"),
        "_ensure_main_server_runtime_initialized": initialize,
        "_request_memory_server_continue_startup": continuation,
        "_request_memory_server_block_startup": compensation,
        "_start_neko_servers_integration_workers": lambda: order.append("integrations"),
    }
    for name in ("role_state", "steamworks", "templates", "_config_manager", "initialize_character_data",
                 "switch_current_catgirl_fast", "init_one_catgirl", "remove_one_catgirl", "ensure_steamworks_initialized"):
        namespace[name] = None
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace, order


@pytest.mark.parametrize("blocked", [False, True])
def test_startup_starts_chat_sync_only_after_storage_gate(monkeypatch, blocked):
    """A selection-required launch never constructs the optional sync runtime."""
    namespace, order = hooks(monkeypatch, blocked=blocked)
    asyncio.run(namespace["on_startup"]())
    if blocked:
        assert "chat_sync" not in order and "initialize" not in order
    else:
        assert order.index("initialize") < order.index("integrations") < order.index("chat_sync")


@pytest.mark.parametrize("fail", [False, True])
def test_storage_continue_starts_sync_only_after_successful_release(monkeypatch, fail):
    """Failed initialization compensates its storage guard without starting sync."""
    namespace, order = hooks(monkeypatch, fail_initialization=fail)
    if fail:
        with pytest.raises(RuntimeError, match="synthetic initialization failure"):
            asyncio.run(namespace["release_storage_startup_barrier"]())
        assert order == ["continue", "initialize", "blocked", "compensate"]
    else:
        assert asyncio.run(namespace["release_storage_startup_barrier"]()) == {"ok": True, "initialized": True}
        assert order == ["continue", "initialize", "released", "integrations", "chat_sync"]


@pytest.mark.parametrize("outcome", ["success", "rejected", "unexpected", "cancelled"])
@pytest.mark.parametrize("drain_callback", [False, True])
def test_completed_bootstrap_does_not_break_shutdown_on_a_new_loop(outcome, drain_callback):
    """Use genuine event loops so pytest's nested-loop patch cannot hide leakage."""
    script = r'''
import asyncio
from pathlib import Path
import sys
from unittest.mock import patch
from services.chat_sync import runtime as module

outcome = sys.argv[1]
drain_callback = sys.argv[2] == "True"

class Runtime:
    closed = 0

    async def close(self):
        self.closed += 1

runtime = Runtime()
module._runtimes[Path("synthetic")] = runtime

async def start(_manager):
    if not drain_callback:
        # Stop before the task's completion callbacks get another loop turn.
        asyncio.get_running_loop().stop()
    if outcome == "rejected":
        raise ValueError("synthetic private initialization detail")
    if outcome == "unexpected":
        raise RuntimeError("synthetic private initialization detail")

async def complete():
    module.schedule_desktop_chat_sync_start(object())
    task = module._scheduler_bootstrap
    if outcome == "cancelled":
        task.cancel()
        if not drain_callback:
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)
    if not drain_callback:
        return task
    await asyncio.gather(task, return_exceptions=True)
    return task

loop = asyncio.new_event_loop()
with patch.object(module, "start_desktop_chat_sync_scheduler", start), patch.object(
    module.httpx, "AsyncClient", side_effect=AssertionError("Unexpected network allocation")
):
    try:
        if drain_callback:
            task = loop.run_until_complete(complete())
        else:
            launch = loop.create_task(complete())
            loop.run_forever()
            task = launch.result()
    finally:
        loop.close()
    assert task.done() and task.get_loop().is_closed()
    asyncio.run(module.close_desktop_chat_sync_runtimes())
    asyncio.run(module.close_desktop_chat_sync_runtimes())
    assert module._scheduler_bootstrap is None and module._runtimes == {}
    assert runtime.closed == 1
'''
    result = subprocess.run([sys.executable, "-c", script, outcome, str(drain_callback)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "synthetic private initialization detail" not in result.stdout + result.stderr
    if outcome in {"rejected", "unexpected"}:
        assert result.stdout.strip() == "[chat-sync] scheduler_start_unavailable"
    else:
        assert result.stdout == ""


def test_shutdown_drains_pending_bootstrap_without_clearing_a_replacement(monkeypatch):
    """A delayed old callback cannot orphan the next startup task at shutdown."""
    monkeypatch.setattr(chat_sync_runtime, "_scheduler_bootstrap", None)
    monkeypatch.setattr(chat_sync_runtime, "_runtimes", {})

    async def scenario():
        started, drained = asyncio.Event(), asyncio.Event()
        calls = 0

        async def start(_manager):
            nonlocal calls
            calls += 1
            if calls == 1:
                return
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                drained.set()

        with patch.object(chat_sync_runtime, "start_desktop_chat_sync_scheduler", start):
            chat_sync_runtime.schedule_desktop_chat_sync_start(object())
            old = chat_sync_runtime._scheduler_bootstrap
            await asyncio.sleep(0)
            assert old.done()
            # Its done callback is queued behind this caller's current turn.
            chat_sync_runtime.schedule_desktop_chat_sync_start(object())
            replacement = chat_sync_runtime._scheduler_bootstrap
            await started.wait()
            assert replacement is not old and chat_sync_runtime._scheduler_bootstrap is replacement
            chat_sync_runtime.schedule_desktop_chat_sync_start(object())
            assert chat_sync_runtime._scheduler_bootstrap is replacement
            await chat_sync_runtime.close_desktop_chat_sync_runtimes()
            assert replacement.cancelled() and drained.is_set()
            assert calls == 2 and chat_sync_runtime._scheduler_bootstrap is None

    asyncio.run(scenario())


def test_shutdown_does_not_swallow_caller_cancellation(monkeypatch):
    """Keep the existing cancellation contract while detaching startup ownership."""
    monkeypatch.setattr(chat_sync_runtime, "_scheduler_bootstrap", None)
    monkeypatch.setattr(chat_sync_runtime, "_runtimes", {})

    async def scenario():
        started, cancelling = asyncio.Event(), asyncio.Event()

        async def start(_manager):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await asyncio.Event().wait()

        with patch.object(chat_sync_runtime, "start_desktop_chat_sync_scheduler", start):
            chat_sync_runtime.schedule_desktop_chat_sync_start(object())
            bootstrap = chat_sync_runtime._scheduler_bootstrap
            await started.wait()
            closing = asyncio.create_task(chat_sync_runtime.close_desktop_chat_sync_runtimes())
            await cancelling.wait()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert bootstrap.cancelled() and chat_sync_runtime._scheduler_bootstrap is None

    asyncio.run(scenario())
