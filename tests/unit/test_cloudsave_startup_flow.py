from contextlib import contextmanager
import signal
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
def test_launcher_resolves_committed_storage_layout_and_exports_env(monkeypatch, tmp_path):
    import launcher

    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    config_manager = SimpleNamespace(app_docs_dir=tmp_path / "legacy" / "N.E.K.O")
    reset_calls = []
    original_selected_env = launcher.os.environ.get("NEKO_STORAGE_SELECTED_ROOT")
    original_anchor_env = launcher.os.environ.get("NEKO_STORAGE_ANCHOR_ROOT")
    original_cloudsave_env = launcher.os.environ.get("NEKO_STORAGE_CLOUDSAVE_ROOT")

    monkeypatch.delenv("NEKO_STORAGE_SELECTED_ROOT", raising=False)
    monkeypatch.delenv("NEKO_STORAGE_ANCHOR_ROOT", raising=False)
    monkeypatch.delenv("NEKO_STORAGE_CLOUDSAVE_ROOT", raising=False)

    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: reset_calls.append("reset"))
    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda _config_manager: {
            "attempted": True,
            "completed": True,
        },
    )
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _config_manager: {
            "selected_root": str(selected_root),
            "anchor_root": str(anchor_root),
            "cloudsave_root": str(anchor_root / "cloudsave"),
            "source": "policy",
        },
    )

    try:
        result = launcher._resolve_storage_layout_for_launch()

        assert result["migration_result"]["completed"] is True
        assert result["layout"]["selected_root"] == str(selected_root)
        assert result["layout"]["anchor_root"] == str(anchor_root)
        assert result["layout"]["cloudsave_root"] == str(anchor_root / "cloudsave")
        assert reset_calls == ["reset", "reset", "reset"]
        assert launcher.os.environ["NEKO_STORAGE_SELECTED_ROOT"] == str(selected_root)
        assert launcher.os.environ["NEKO_STORAGE_ANCHOR_ROOT"] == str(anchor_root)
        assert launcher.os.environ["NEKO_STORAGE_CLOUDSAVE_ROOT"] == str(anchor_root / "cloudsave")
    finally:
        if original_selected_env is None:
            launcher.os.environ.pop("NEKO_STORAGE_SELECTED_ROOT", None)
        else:
            launcher.os.environ["NEKO_STORAGE_SELECTED_ROOT"] = original_selected_env
        if original_anchor_env is None:
            launcher.os.environ.pop("NEKO_STORAGE_ANCHOR_ROOT", None)
        else:
            launcher.os.environ["NEKO_STORAGE_ANCHOR_ROOT"] = original_anchor_env
        if original_cloudsave_env is None:
            launcher.os.environ.pop("NEKO_STORAGE_CLOUDSAVE_ROOT", None)
        else:
            launcher.os.environ["NEKO_STORAGE_CLOUDSAVE_ROOT"] = original_cloudsave_env


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
def test_launcher_suppresses_startup_failure_events_during_expected_shutdown(monkeypatch):
    import launcher

    emitted_events = []

    monkeypatch.setattr(launcher, "_expected_launcher_shutdown", True)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )

    launcher.report_startup_failure("Startup failed: Memory Server exited early (exitcode=-15)")

    assert emitted_events == []


@pytest.mark.unit
def test_launcher_post_startup_root_state_preserves_non_normal_modes(monkeypatch):
    import launcher

    config_manager = SimpleNamespace(
        app_docs_dir="/tmp/runtime/N.E.K.O",
        load_root_state=lambda: {"mode": "deferred_init"},
    )
    set_root_mode_calls = []

    monkeypatch.setattr(launcher, "set_root_mode", lambda *_args, **_kwargs: set_root_mode_calls.append((_args, _kwargs)))

    launcher._persist_post_startup_root_state(config_manager)

    assert set_root_mode_calls == []


@pytest.mark.unit
def test_launcher_schedules_restart_for_rebind_only_shutdown_without_pending_migration(monkeypatch):
    import launcher

    emitted_events = []
    released = {"called": False}
    spawned = {"called": False}

    config_manager = SimpleNamespace(
        load_root_state=lambda: {
            "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
            "last_migration_result": "restart_rebind:/tmp/original-root/N.E.K.O",
        }
    )

    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "layout": {
                "selected_root": "/tmp/original-root/N.E.K.O",
                "anchor_root": "/tmp/anchor-root/N.E.K.O",
                "cloudsave_root": "/tmp/anchor-root/N.E.K.O/cloudsave",
            },
            "migration_result": {
                "attempted": False,
                "completed": False,
            },
        },
    )
    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )
    monkeypatch.setattr(launcher, "release_startup_lock", lambda: released.__setitem__("called", True))
    monkeypatch.setattr(launcher, "_spawn_restarted_launcher", lambda: spawned.__setitem__("called", True))

    result = launcher._maybe_schedule_storage_restart()

    assert result is True
    assert released["called"] is True
    assert spawned["called"] is True
    assert emitted_events == [
        (
            "storage_migration_restart",
            {
                "completed": True,
                "error_code": "",
                "error_message": "",
                "layout": {
                    "selected_root": "/tmp/original-root/N.E.K.O",
                    "anchor_root": "/tmp/anchor-root/N.E.K.O",
                    "cloudsave_root": "/tmp/anchor-root/N.E.K.O/cloudsave",
                },
                "restart_reason": "rebind_only",
            },
        )
    ]


@pytest.mark.unit
def test_launcher_schedules_restart_for_rebind_only_when_root_state_was_recovered_from_stale_maintenance(
    monkeypatch,
):
    import launcher

    released = {"called": False}
    spawned = {"called": False}

    config_manager = SimpleNamespace(
        load_root_state=lambda: {
            "mode": launcher.ROOT_MODE_NORMAL,
            "current_root": "/tmp/anchor-root/N.E.K.O",
            "last_migration_source": "/tmp/original-root/N.E.K.O",
            "last_migration_result": "recovered_stale_mode:maintenance_readonly",
        }
    )

    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "layout": {
                "selected_root": "/tmp/original-root/N.E.K.O",
                "anchor_root": "/tmp/anchor-root/N.E.K.O",
                "cloudsave_root": "/tmp/anchor-root/N.E.K.O/cloudsave",
            },
            "migration_result": {
                "attempted": False,
                "completed": False,
            },
        },
    )
    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "release_startup_lock", lambda: released.__setitem__("called", True))
    monkeypatch.setattr(launcher, "_spawn_restarted_launcher", lambda: spawned.__setitem__("called", True))

    result = launcher._maybe_schedule_storage_restart()

    assert result is True
    assert released["called"] is True
    assert spawned["called"] is True


@pytest.mark.unit
def test_spawn_restarted_launcher_detaches_stdio_when_current_session_is_tty(monkeypatch):
    import launcher

    popen_calls = []

    class _TTYStream:
        def isatty(self):
            return True

    monkeypatch.setattr(launcher.sys, "stdin", _TTYStream())
    monkeypatch.setattr(launcher.sys, "stdout", _TTYStream())
    monkeypatch.setattr(launcher.sys, "stderr", _TTYStream())
    monkeypatch.setattr(launcher, "_build_launcher_relaunch_command", lambda: ["python", "launcher.py"])
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: popen_calls.append((command, kwargs)),
    )

    launcher._spawn_restarted_launcher()

    assert len(popen_calls) == 1
    _, kwargs = popen_calls[0]
    assert kwargs["stdin"] is launcher.subprocess.DEVNULL
    assert kwargs["stdout"] is launcher.subprocess.DEVNULL
    assert kwargs["stderr"] is launcher.subprocess.DEVNULL


@pytest.mark.unit
def test_spawn_restarted_launcher_preserves_stdio_when_not_running_in_tty(monkeypatch):
    import launcher

    popen_calls = []

    class _PipeStream:
        def isatty(self):
            return False

    monkeypatch.setattr(launcher.sys, "stdin", _PipeStream())
    monkeypatch.setattr(launcher.sys, "stdout", _PipeStream())
    monkeypatch.setattr(launcher.sys, "stderr", _PipeStream())
    monkeypatch.setattr(launcher, "_build_launcher_relaunch_command", lambda: ["python", "launcher.py"])
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: popen_calls.append((command, kwargs)),
    )

    launcher._spawn_restarted_launcher()

    assert len(popen_calls) == 1
    _, kwargs = popen_calls[0]
    assert "stdin" not in kwargs
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs


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
@pytest.mark.asyncio
async def test_main_server_requests_launcher_shutdown_in_multi_process_mode(monkeypatch):
    import main_server

    scheduled = {}
    kill_calls = []

    class _DummyLoop:
        def call_later(self, delay, callback):
            scheduled["delay"] = delay
            scheduled["callback"] = callback

    shutdown_mock = AsyncMock()

    monkeypatch.setenv("NEKO_LAUNCH_MODE", "multi")
    monkeypatch.setenv("NEKO_LAUNCHER_PID", "54321")
    monkeypatch.setattr(main_server.asyncio, "get_running_loop", lambda: _DummyLoop())
    monkeypatch.setattr(main_server, "shutdown_server_async", shutdown_mock)
    monkeypatch.setattr(main_server.os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

    await main_server.request_application_shutdown_async()

    assert scheduled["delay"] == 1.0
    shutdown_mock.assert_not_awaited()
    scheduled["callback"]()
    assert kill_calls == [(54321, signal.SIGTERM)]


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
