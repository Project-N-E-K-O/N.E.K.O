from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import json

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
        call_order.append(("fence_enter", mode, reason))
        try:
            yield {"mode": mode}
        finally:
            call_order.append(("fence_exit", mode, reason))

    def _fake_bootstrap(_config_manager):
        call_order.append("bootstrap")
        return {"bootstrap": True}

    class _DummyCloudsaveManager:
        def import_if_needed(self, *, reason: str, fence_already_active: bool = False, **_kwargs):
            call_order.append(("import", reason, fence_already_active))
            return {"success": True, "action": "imported", "requested_reason": reason}

    def _fake_set_root_mode(_config_manager, mode, **updates):
        call_order.append(("set_root_mode", mode, updates))
        return {"mode": mode, **updates}

    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
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
    import_index = call_order.index(("import", "launcher_phase0_prelaunch_import", True))
    fence_exit_index = call_order.index(("fence_exit", launcher.ROOT_MODE_BOOTSTRAP_IMPORTING, "launcher_phase0_bootstrap"))
    assert bootstrap_index < import_index < fence_exit_index
    assert result["import_result"]["action"] == "imported"
    assert emitted_events[-1][0] == "cloudsave_bootstrap_ready"
    event_import_result = emitted_events[-1][1]["import_result"]
    assert set(event_import_result.keys()) == {"success", "action", "requested_reason"}
    assert event_import_result["requested_reason"] == "launcher_phase0_prelaunch_import"
    assert emitted_events[-1][1]["manifest_name"] == "manifest.json"
    assert emitted_events[-1][1]["manifest_exists"] is False
    root_state_payload = emitted_events[-1][1]["root_state"]
    assert root_state_payload["mode"] == launcher.ROOT_MODE_NORMAL
    assert root_state_payload["is_normal"] is True
    assert "current_root" not in root_state_payload
    assert "last_known_good_root" not in root_state_payload


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
def test_launcher_runs_storage_probe_before_cloudsave_bootstrap(monkeypatch):
    import launcher

    call_order = []

    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "acquire_startup_lock", lambda: True)
    monkeypatch.setattr(launcher, "apply_port_strategy", lambda: True)
    monkeypatch.setattr(launcher, "_run_storage_location_first_run_probe", lambda: call_order.append("probe") or "skipped")
    monkeypatch.setattr(launcher, "register_shutdown_hooks", lambda: call_order.append("register"))
    monkeypatch.setattr(launcher, "setup_job_object", lambda: call_order.append("job"))
    monkeypatch.setattr(
        launcher,
        "_prepare_cloudsave_runtime_for_launch",
        lambda: call_order.append("bootstrap"),
    )
    monkeypatch.setattr(launcher, "_ensure_playwright_browsers", lambda: None)
    monkeypatch.setattr(launcher, "_should_use_merged_mode", lambda: True)
    monkeypatch.setattr(launcher, "run_merged_servers", lambda: 0)
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: None)
    monkeypatch.setattr(launcher, "release_startup_lock", lambda: None)
    monkeypatch.setattr(launcher, "SERVERS", [], raising=False)

    assert launcher.main() == 0
    assert call_order.index("probe") < call_order.index("bootstrap")


@pytest.mark.unit
def test_storage_location_probe_persists_state_and_emits_completion(monkeypatch, tmp_path):
    import launcher

    runtime_root = tmp_path / "runtime"
    state_path = runtime_root / "state" / launcher.STORAGE_LOCATION_PROBE_STATE_FILENAME
    captured_events = []

    monkeypatch.setattr(
        launcher,
        "_detect_storage_location_probe_context",
        lambda: {
            "recommended_runtime_root": str(runtime_root),
            "cloudsave_root": str(runtime_root / "cloudsave"),
            "has_existing_runtime_content": False,
            "legacy_sources": [{"path": str(tmp_path / "legacy"), "score": 4}],
            "state_path": str(state_path),
        },
    )
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda event_type, payload=None: captured_events.append((event_type, payload)))
    monkeypatch.setattr(
        launcher,
        "_show_storage_location_probe_dialog",
        lambda _context: {"action": "use_default", "selected_path": str(runtime_root)},
    )

    result = launcher._run_storage_location_first_run_probe()

    assert result == "completed"
    assert state_path.is_file()
    with open(state_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["completed"] is True
    assert payload["probe_only"] is True
    assert payload["action"] == "use_default"
    assert captured_events[0][0] == "storage_location_required"
    assert captured_events[-1][0] == "storage_location_probe_completed"
    assert captured_events[-1][1]["status"] == "completed"


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

    assert call_order == ["main", "memory", "agent"]


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
