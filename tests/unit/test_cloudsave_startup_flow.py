from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
def test_launcher_prepares_cloudsave_runtime_before_starting_services(monkeypatch, tmp_path):
    import launcher

    config_manager = SimpleNamespace(
        app_docs_dir=tmp_path / "N.E.K.O",
        cloudsave_manifest_path=tmp_path / "N.E.K.O" / "cloudsave" / "manifest.json",
    )
    call_order = []
    emitted_events = []

    @contextmanager
    def _fake_fence(_config_manager, *, mode, reason):
        call_order.append(("fence", mode, reason))
        yield {"mode": mode}

    def _fake_bootstrap(_config_manager):
        call_order.append("bootstrap")
        return {"bootstrap": True}

    class _DummyCloudsaveManager:
        def import_if_needed(self, *, reason: str):
            call_order.append(("import", reason))
            return {"success": True, "action": "imported", "requested_reason": reason}

    def _fake_set_root_mode(_config_manager, mode, **updates):
        call_order.append(("set_root_mode", mode, updates))
        return {"mode": mode, **updates}

    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name: config_manager)
    monkeypatch.setattr(launcher, "cloud_apply_fence", _fake_fence)
    monkeypatch.setattr(launcher, "bootstrap_local_cloudsave_environment", _fake_bootstrap)
    monkeypatch.setattr(launcher, "get_cloudsave_manager", lambda _config_manager: _DummyCloudsaveManager())
    monkeypatch.setattr(launcher, "set_root_mode", _fake_set_root_mode)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )

    result = launcher._prepare_cloudsave_runtime_for_launch()

    bootstrap_index = call_order.index("bootstrap")
    import_index = call_order.index(("import", "launcher_phase0_prelaunch_import"))
    assert bootstrap_index < import_index
    assert result["import_result"]["action"] == "imported"
    assert emitted_events[-1][0] == "cloudsave_bootstrap_ready"
    assert emitted_events[-1][1]["import_result"]["requested_reason"] == "launcher_phase0_prelaunch_import"
    assert emitted_events[-1][1]["manifest_name"] == "manifest.json"
    assert emitted_events[-1][1]["manifest_exists"] is False


@pytest.mark.unit
def test_launcher_uses_multi_process_mode_by_default_in_source(monkeypatch):
    import launcher

    monkeypatch.delenv("NEKO_MERGED", raising=False)
    monkeypatch.setattr(launcher, "IS_FROZEN", False)

    assert launcher._should_use_merged_mode() is False


@pytest.mark.unit
def test_launcher_uses_merged_mode_by_default_when_frozen(monkeypatch):
    import launcher

    monkeypatch.delenv("NEKO_MERGED", raising=False)
    monkeypatch.setattr(launcher, "IS_FROZEN", True)

    assert launcher._should_use_merged_mode() is True


@pytest.mark.unit
def test_launcher_env_override_beats_default_process_mode(monkeypatch):
    import launcher

    monkeypatch.setattr(launcher, "IS_FROZEN", False)
    monkeypatch.setenv("NEKO_MERGED", "1")
    assert launcher._should_use_merged_mode() is True

    monkeypatch.setattr(launcher, "IS_FROZEN", True)
    monkeypatch.setenv("NEKO_MERGED", "0")
    assert launcher._should_use_merged_mode() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_syncs_memory_server_after_startup_import():
    import main_server

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        await main_server._sync_memory_server_after_startup_import({"action": "imported"})

    mock_reload.assert_awaited_once_with(reason="Steam Auto-Cloud startup import")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_skips_memory_reload_when_startup_import_did_not_run():
    import main_server

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        await main_server._sync_memory_server_after_startup_import({"action": "skipped"})

    mock_reload.assert_not_called()


@pytest.mark.unit
def test_launcher_cleanup_waits_for_main_server_shutdown_completion(monkeypatch):
    import launcher

    class _DummyEvent:
        def __init__(self, *, wait_result=True):
            self.wait_result = wait_result
            self.set_called = False
            self.wait_calls = []

        def set(self):
            self.set_called = True

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            return self.wait_result

    class _DummyProcess:
        def __init__(self):
            self.alive = True
            self.join_calls = []
            self.terminate_called = False
            self.kill_called = False
            self.pid = 43210

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.join_calls.append(timeout)
            if timeout == 2:
                self.alive = False

        def terminate(self):
            self.terminate_called = True

        def kill(self):
            self.kill_called = True

    shutdown_event = _DummyEvent()
    shutdown_complete_event = _DummyEvent(wait_result=True)
    process = _DummyProcess()

    monkeypatch.setattr(launcher, "_cleanup_done", False)
    monkeypatch.setattr(launcher, "JOB_HANDLE", None)
    monkeypatch.setattr(
        launcher,
        "SERVERS",
        [
            {
                "name": "Main Server",
                "module": "main_server",
                "port": launcher.MAIN_SERVER_PORT,
                "process": process,
                "shutdown_event": shutdown_event,
                "shutdown_complete_event": shutdown_complete_event,
                "graceful_shutdown_timeout": 20,
            }
        ],
        raising=False,
    )

    launcher.cleanup_servers()

    assert shutdown_event.set_called is True
    assert shutdown_complete_event.wait_calls == [20]
    assert process.join_calls == [2]
    assert process.terminate_called is False
    assert process.kill_called is False


@pytest.mark.unit
def test_launcher_cleanup_requests_main_before_memory(monkeypatch):
    import launcher

    call_order = []

    class _DummyEvent:
        def __init__(self, name: str):
            self.name = name

        def set(self):
            call_order.append(self.name)

        def wait(self, timeout=None):
            return True

    class _DummyProcess:
        def __init__(self, pid: int):
            self.alive = True
            self.pid = pid

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            if timeout == 2:
                self.alive = False

        def terminate(self):
            self.alive = False

        def kill(self):
            self.alive = False

    monkeypatch.setattr(launcher, "_cleanup_done", False)
    monkeypatch.setattr(launcher, "JOB_HANDLE", None)
    monkeypatch.setattr(
        launcher,
        "SERVERS",
        [
            {
                "name": "Memory Server",
                "module": "memory_server",
                "port": launcher.MEMORY_SERVER_PORT,
                "process": _DummyProcess(1001),
                "shutdown_event": _DummyEvent("memory"),
                "shutdown_complete_event": _DummyEvent("memory_complete"),
                "graceful_shutdown_timeout": 12,
            },
            {
                "name": "Main Server",
                "module": "main_server",
                "port": launcher.MAIN_SERVER_PORT,
                "process": _DummyProcess(1002),
                "shutdown_event": _DummyEvent("main"),
                "shutdown_complete_event": _DummyEvent("main_complete"),
                "graceful_shutdown_timeout": 20,
            },
            {
                "name": "Agent Server",
                "module": "agent_server",
                "port": launcher.TOOL_SERVER_PORT,
                "process": _DummyProcess(1003),
                "shutdown_event": _DummyEvent("agent"),
                "shutdown_complete_event": _DummyEvent("agent_complete"),
                "graceful_shutdown_timeout": 8,
            },
        ],
        raising=False,
    )

    launcher.cleanup_servers()

    assert call_order[:3] == ["main", "memory", "agent"]


@pytest.mark.unit
def test_launcher_cleanup_survives_keyboardinterrupt_during_shutdown_wait(monkeypatch):
    import launcher

    class _InterruptingEvent:
        def set(self):
            return None

        def wait(self, timeout=None):
            raise KeyboardInterrupt()

    class _DummyProcess:
        def __init__(self):
            self.alive = True
            self.pid = 24680
            self.terminate_called = False

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            return None

        def terminate(self):
            self.terminate_called = True
            self.alive = False

        def kill(self):
            self.alive = False

    process = _DummyProcess()

    monkeypatch.setattr(launcher, "_cleanup_done", False)
    monkeypatch.setattr(launcher, "JOB_HANDLE", None)
    monkeypatch.setattr(
        launcher,
        "SERVERS",
        [
            {
                "name": "Main Server",
                "module": "main_server",
                "port": launcher.MAIN_SERVER_PORT,
                "process": process,
                "shutdown_event": _InterruptingEvent(),
                "shutdown_complete_event": _InterruptingEvent(),
                "graceful_shutdown_timeout": 20,
            }
        ],
        raising=False,
    )

    launcher.cleanup_servers()

    assert process.terminate_called is True
