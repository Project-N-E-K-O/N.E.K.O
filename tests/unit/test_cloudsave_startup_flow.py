from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
def test_launcher_prepares_cloudsave_runtime_before_starting_services(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

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

    def _fake_ensure_local_state_directory():
        call_order.append("state_preflight")
        return True

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
    config_manager.ensure_local_state_directory = _fake_ensure_local_state_directory
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )

    result = launcher._prepare_cloudsave_runtime_for_launch()

    state_preflight_index = call_order.index("state_preflight")
    bootstrap_index = call_order.index("bootstrap")
    fence_enter_index = call_order.index(("fence_enter", launcher.ROOT_MODE_BOOTSTRAP_IMPORTING, "launcher_phase0_bootstrap"))
    import_index = call_order.index(("import", "launcher_phase0_prelaunch_import", True))
    fence_exit_index = call_order.index(("fence_exit", launcher.ROOT_MODE_BOOTSTRAP_IMPORTING, "launcher_phase0_bootstrap"))
    assert state_preflight_index < fence_enter_index < bootstrap_index < import_index < fence_exit_index
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
def test_launcher_disables_cloudsave_when_local_state_directory_fails(monkeypatch):
    from launcher_core import runtime as launcher

    class _LocalStateFailure(OSError):
        local_state_directory_error = True

    set_root_mode_calls = []
    reported_failures = []

    monkeypatch.setattr(launcher, "freeze_support", lambda: None)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "acquire_startup_lock", lambda: True)
    monkeypatch.setattr(launcher, "apply_port_strategy", lambda: True)
    monkeypatch.setattr(launcher, "register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(launcher, "setup_job_object", lambda: None)
    monkeypatch.setattr(launcher, "_resolve_storage_layout_for_launch", lambda: {})
    monkeypatch.setattr(
        launcher,
        "_prepare_cloudsave_runtime_for_launch",
        lambda: (_ for _ in ()).throw(_LocalStateFailure("state directory unavailable")),
    )
    monkeypatch.setattr(launcher, "set_root_mode", lambda *_args, **_kwargs: set_root_mode_calls.append((_args, _kwargs)))
    monkeypatch.setattr(
        launcher,
        "report_startup_failure",
        lambda message, show_dialog=True: reported_failures.append((message, show_dialog)),
    )
    monkeypatch.setattr(launcher, "_ensure_playwright_browsers", lambda: None)
    monkeypatch.setattr(launcher, "_should_use_merged_mode", lambda: False)
    monkeypatch.setattr(launcher, "SERVERS", [{"name": "Main Server", "process": None}])
    monkeypatch.setattr(launcher, "start_server", lambda server: True)
    monkeypatch.setattr(
        launcher,
        "wait_for_servers",
        lambda timeout=60: launcher.STARTUP_WAIT_RESULT_STORAGE_RESTART,
    )
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: None)
    monkeypatch.delenv(launcher.CLOUDSAVE_DISABLED_ENV, raising=False)

    try:
        result = launcher.main()
        disabled_reason = launcher.os.environ.get(launcher.CLOUDSAVE_DISABLED_ENV)
    finally:
        launcher.os.environ.pop(launcher.CLOUDSAVE_DISABLED_ENV, None)

    assert result == 0
    assert disabled_reason == "local_state_unavailable"
    assert set_root_mode_calls == []
    assert reported_failures == []


@pytest.mark.unit
def test_launcher_resolves_committed_storage_layout_and_exports_env(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

    monkeypatch.delenv("NEKO_MERGED", raising=False)
    monkeypatch.setattr(launcher, "IS_FROZEN", False)

    assert launcher._should_use_merged_mode() is False


@pytest.mark.unit
def test_launcher_uses_merged_mode_by_default_when_frozen(monkeypatch):
    from launcher_core import runtime as launcher

    monkeypatch.delenv("NEKO_MERGED", raising=False)
    monkeypatch.setattr(launcher, "IS_FROZEN", True)

    assert launcher._should_use_merged_mode() is True


@pytest.mark.unit
def test_launcher_env_override_beats_default_process_mode(monkeypatch):
    from launcher_core import runtime as launcher

    monkeypatch.setattr(launcher, "IS_FROZEN", False)
    monkeypatch.setenv("NEKO_MERGED", "1")
    assert launcher._should_use_merged_mode() is True

    monkeypatch.setattr(launcher, "IS_FROZEN", True)
    monkeypatch.setenv("NEKO_MERGED", "0")
    assert launcher._should_use_merged_mode() is False


@pytest.mark.unit
def test_runtime_config_reload_preserves_negotiated_fallback_ports(monkeypatch):
    from launcher_core import runtime as launcher

    network_config = launcher.importlib.import_module("config.network")
    selected_ports = {
        "MAIN_SERVER_PORT": 53111,
        "MEMORY_SERVER_PORT": 53112,
        "TOOL_SERVER_PORT": 53115,
        "USER_PLUGIN_SERVER_PORT": 53116,
        "AGENT_MQ_PORT": 53117,
        "MAIN_AGENT_EVENT_PORT": 53118,
    }
    stale_ports = {name: port - 1000 for name, port in selected_ports.items()}

    for module in (network_config, launcher.config_module):
        for name, port in stale_ports.items():
            monkeypatch.setattr(module, name, port)
        monkeypatch.setattr(module, "INSTANCE_ID", "stale-instance")
        monkeypatch.setattr(module, "USER_PLUGIN_BASE", "http://127.0.0.1:52116")
        monkeypatch.setattr(module, "AUTOSTART_ALLOWED_ORIGINS", ())

    monkeypatch.setattr(launcher, "INSTANCE_ID", "stale-instance")
    monkeypatch.setattr(launcher, "MAIN_SERVER_PORT", stale_ports["MAIN_SERVER_PORT"])
    monkeypatch.setattr(launcher, "MEMORY_SERVER_PORT", stale_ports["MEMORY_SERVER_PORT"])
    monkeypatch.setattr(launcher, "TOOL_SERVER_PORT", stale_ports["TOOL_SERVER_PORT"])
    monkeypatch.setenv("NEKO_INSTANCE_ID", "fallback-instance")
    for name, port in selected_ports.items():
        monkeypatch.setenv(f"NEKO_{name}", str(port))

    launcher._reload_runtime_config_from_env()

    for module in (network_config, launcher.config_module):
        for name, port in selected_ports.items():
            assert getattr(module, name) == port
        assert module.INSTANCE_ID == "fallback-instance"
        assert module.USER_PLUGIN_BASE == "http://127.0.0.1:53116"
        assert f"http://127.0.0.1:{selected_ports['MAIN_SERVER_PORT']}" in (
            module.AUTOSTART_ALLOWED_ORIGINS
        )

    assert launcher.INSTANCE_ID == "fallback-instance"
    assert launcher.MAIN_SERVER_PORT == selected_ports["MAIN_SERVER_PORT"]
    assert launcher.MEMORY_SERVER_PORT == selected_ports["MEMORY_SERVER_PORT"]
    assert launcher.TOOL_SERVER_PORT == selected_ports["TOOL_SERVER_PORT"]


@pytest.mark.unit
@pytest.mark.parametrize("footprint", ["partial", "mixed"])
def test_launcher_partial_existing_services_force_multi_mode(monkeypatch, footprint):
    from launcher_core import runtime as launcher

    public_ports = {
        "MAIN_SERVER_PORT": 43111,
        "MEMORY_SERVER_PORT": 43112,
        "TOOL_SERVER_PORT": 43115,
    }
    internal_ports = {
        "USER_PLUGIN_SERVER_PORT": 43116,
        "AGENT_MQ_PORT": 43117,
        "MAIN_AGENT_EVENT_PORT": 43118,
    }
    expected_roles = {
        "MAIN_SERVER_PORT": "main",
        "MEMORY_SERVER_PORT": "memory",
        "TOOL_SERVER_PORT": "agent",
    }
    conflicting_keys = (
        {"MEMORY_SERVER_PORT"}
        if footprint == "partial"
        else set(public_ports)
    )
    health_by_port = {
        public_ports[key]: {
            "service": expected_roles[key],
            "instance_id": "existing-a" if key != "TOOL_SERVER_PORT" else "existing-b",
        }
        for key in conflicting_keys
    }
    emitted_events = []

    monkeypatch.setattr(launcher, "_should_use_merged_mode", lambda: True)
    monkeypatch.setattr(launcher, "DEFAULT_PORTS", public_ports)
    monkeypatch.setattr(launcher, "INTERNAL_DEFAULT_PORTS", internal_ports)
    for name, port in {**public_ports, **internal_ports}.items():
        monkeypatch.setenv(f"NEKO_{name}", str(port))
    for name, port in public_ports.items():
        monkeypatch.setattr(launcher, name, port)
    monkeypatch.setattr(
        launcher,
        "SERVERS",
        [
            {"name": "Memory Server", "module": "memory_server", "port": 43112},
            {"name": "Agent Server", "module": "agent_server", "port": 43115},
            {"name": "Main Server", "module": "main_server", "port": 43111},
        ],
    )
    monkeypatch.setattr(launcher, "_existing_neko_services", set())
    monkeypatch.setattr(launcher, "_partial_or_mixed_existing_backend", False)
    monkeypatch.setattr(launcher, "get_hyperv_excluded_ranges", lambda: [])
    monkeypatch.setattr(
        launcher,
        "_is_port_bindable",
        lambda port: port not in health_by_port,
    )
    monkeypatch.setattr(
        launcher,
        "_classify_port_conflict",
        lambda _port, _ranges: ("neko", [123]),
    )
    monkeypatch.setattr(
        launcher,
        "probe_neko_health",
        lambda port: health_by_port.get(port),
    )
    monkeypatch.setattr(
        launcher,
        "_pick_fallback_port",
        lambda preferred, _reserved: preferred + 1000,
    )
    monkeypatch.setattr(launcher, "_sync_runtime_config_globals", lambda *_args: None)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda name, payload: emitted_events.append((name, payload)),
    )
    monkeypatch.setattr(
        launcher,
        "report_startup_failure",
        lambda message: pytest.fail(message),
    )

    assert launcher.apply_port_strategy() is True

    assert launcher._select_launcher_mode() == (
        "multi",
        "partial_existing_services",
    )
    assert launcher._partial_or_mixed_existing_backend is True
    assert launcher._existing_neko_services == set()
    selected = dict(emitted_events)["port_plan"]["selected"]
    for key in conflicting_keys:
        assert selected[key] == public_ports[key] + 1000


@pytest.mark.unit
def test_existing_backend_attach_requires_roles_and_one_instance():
    from launcher_core import runtime as launcher

    healthy = {
        "MAIN_SERVER_PORT": {"service": "main", "instance_id": "existing"},
        "MEMORY_SERVER_PORT": {"service": "memory", "instance_id": "existing"},
        "TOOL_SERVER_PORT": {"service": "agent", "instance_id": "existing"},
    }
    assert launcher._validated_existing_backend_instance(healthy) == "existing"

    partial = dict(healthy)
    partial.pop("TOOL_SERVER_PORT")
    assert launcher._validated_existing_backend_instance(partial) is None

    mixed = dict(healthy)
    mixed["TOOL_SERVER_PORT"] = {"service": "agent", "instance_id": "other"}
    assert launcher._validated_existing_backend_instance(mixed) is None

    wrong_role = dict(healthy)
    wrong_role["TOOL_SERVER_PORT"] = {
        "service": "main",
        "instance_id": "existing",
    }
    assert launcher._validated_existing_backend_instance(wrong_role) is None


@pytest.mark.unit
def test_existing_backend_attach_events_identify_selected_backend(monkeypatch):
    from launcher_core import runtime as launcher

    health_by_port = {
        launcher.DEFAULT_PORTS["MAIN_SERVER_PORT"]: {
            "service": "main",
            "instance_id": "existing-instance",
        },
        launcher.DEFAULT_PORTS["MEMORY_SERVER_PORT"]: {
            "service": "memory",
            "instance_id": "existing-instance",
        },
        launcher.DEFAULT_PORTS["TOOL_SERVER_PORT"]: {
            "service": "agent",
            "instance_id": "existing-instance",
        },
    }
    events = []
    monkeypatch.setattr(launcher, "INSTANCE_ID", "launcher-instance")
    monkeypatch.setattr(launcher, "_existing_neko_services", set())
    monkeypatch.setattr(launcher, "get_hyperv_excluded_ranges", lambda: [])
    monkeypatch.setattr(launcher, "_is_port_bindable", lambda _port: False)
    monkeypatch.setattr(
        launcher,
        "_classify_port_conflict",
        lambda _port, _ranges: ("neko", []),
    )
    monkeypatch.setattr(
        launcher,
        "probe_neko_health",
        lambda port: health_by_port.get(port),
    )
    monkeypatch.setattr(launcher, "_sync_runtime_config_globals", lambda *_args: None)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda name, payload: events.append((name, payload)),
    )
    for key, port in launcher.DEFAULT_PORTS.items():
        monkeypatch.setenv(f"NEKO_{key}", str(port))

    assert launcher.apply_port_strategy() == "attach"

    payload_by_event = dict(events)
    assert payload_by_event["port_plan"]["instance_id"] == "existing-instance"
    assert payload_by_event["port_plan"]["launcher_instance_id"] == "launcher-instance"
    assert payload_by_event["attach_existing"]["instance_id"] == "existing-instance"


@pytest.mark.unit
def test_start_server_never_reuses_a_partial_existing_service(monkeypatch):
    from launcher_core import runtime as launcher

    monkeypatch.setattr(
        launcher,
        "_existing_neko_services",
        {"MEMORY_SERVER_PORT"},
    )
    monkeypatch.setattr(launcher, "check_port", lambda _port: True)
    monkeypatch.setattr(launcher, "get_port_owners", lambda _port: [123])
    failures = []
    monkeypatch.setattr(launcher, "report_startup_failure", failures.append)

    server = {
        "name": "Memory Server",
        "module": "memory_server",
        "port": 43112,
    }
    assert launcher.start_server(server) is False
    assert failures and "already in use" in failures[0]


@pytest.mark.unit
def test_merged_health_requires_expected_services_and_current_instance(monkeypatch):
    from launcher_core import runtime as launcher

    monkeypatch.setattr(launcher, "INSTANCE_ID", "current-instance")
    apps = [
        (object(), 43112, "Memory"),
        (object(), 43115, "Agent"),
        (object(), 43111, "Main"),
    ]
    healthy = {
        43112: {"service": "memory", "instance_id": "current-instance"},
        43115: {"service": "agent", "instance_id": "current-instance"},
        43111: {"service": "main", "instance_id": "current-instance"},
    }

    assert launcher._merged_health_issues(apps, healthy) == []

    wrong = dict(healthy)
    wrong[43115] = {"service": "main", "instance_id": "current-instance"}
    wrong[43111] = {"service": "main", "instance_id": "old-instance"}
    assert launcher._merged_health_issues(apps, wrong) == [
        "Agent:43115:wrong_service",
        "Main:43111:wrong_instance",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_ready_rejects_early_server_exit(monkeypatch):
    import asyncio

    from launcher_core import runtime as launcher

    monkeypatch.setattr(launcher, "INSTANCE_ID", "current-instance")
    monkeypatch.setattr(launcher, "probe_neko_health", lambda *_args, **_kwargs: None)

    async def _exit_early():
        return None

    async def _keep_running():
        await asyncio.Event().wait()

    tasks = {
        "Memory": asyncio.create_task(_exit_early()),
        "Agent": asyncio.create_task(_keep_running()),
        "Main": asyncio.create_task(_keep_running()),
    }
    await asyncio.sleep(0)
    try:
        with pytest.raises(RuntimeError, match="Memory server task exited"):
            await launcher._wait_for_merged_servers_ready(
                [(object(), 43112, "Memory")],
                tasks,
                timeout=0.1,
                poll_interval=0.01,
            )
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_shutdown_preserves_main_memory_agent_order():
    import asyncio
    from types import SimpleNamespace

    from launcher_core import runtime as launcher

    servers = {
        name: SimpleNamespace(should_exit=False)
        for name in launcher.MERGED_SERVER_SHUTDOWN_ORDER
    }
    exit_order = []

    async def _serve_until_exit(name):
        while not servers[name].should_exit:
            await asyncio.sleep(0)
        exit_order.append(name)

    tasks = {
        name: asyncio.create_task(_serve_until_exit(name))
        for name in launcher.MERGED_SERVER_SHUTDOWN_ORDER
    }

    failures = await launcher._shutdown_merged_servers_in_order(servers, tasks)

    assert failures == []
    assert exit_order == ["Main", "Memory", "Agent"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_shutdown_timeout_still_advances_to_later_services():
    import asyncio
    from types import SimpleNamespace

    from launcher_core import runtime as launcher

    servers = {
        name: SimpleNamespace(should_exit=False)
        for name in launcher.MERGED_SERVER_SHUTDOWN_ORDER
    }
    exit_order = []

    async def _stuck_main():
        await asyncio.Event().wait()

    async def _serve_until_exit(name):
        while not servers[name].should_exit:
            await asyncio.sleep(0)
        exit_order.append(name)

    tasks = {
        "Main": asyncio.create_task(_stuck_main()),
        "Memory": asyncio.create_task(_serve_until_exit("Memory")),
        "Agent": asyncio.create_task(_serve_until_exit("Agent")),
    }
    failures = await launcher._shutdown_merged_servers_in_order(
        servers,
        tasks,
        timeouts={"Main": 0.01, "Memory": 0.1, "Agent": 0.1},
    )

    assert failures == ["Main:shutdown_timeout"]
    assert exit_order == ["Memory", "Agent"]
    await asyncio.gather(*tasks.values(), return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_server_converts_uvicorn_system_exit():
    from launcher_core import runtime as launcher

    class _Server:
        async def serve(self):
            raise SystemExit(2)

    with pytest.raises(RuntimeError, match="Main server exited.*code=2"):
        await launcher._serve_merged_server(_Server(), "Main")


@pytest.mark.unit
def test_merged_disables_new_and_legacy_uvicorn_signal_hooks():
    from contextlib import contextmanager
    from types import SimpleNamespace

    from launcher_core import runtime as launcher

    @contextmanager
    def _capturing():
        raise AssertionError("signal capture should have been replaced")
        yield

    server = SimpleNamespace(
        install_signal_handlers=lambda: (_ for _ in ()).throw(
            AssertionError("legacy signal hook should have been replaced")
        ),
        capture_signals=_capturing,
    )

    launcher._disable_uvicorn_signal_handlers(server)

    server.install_signal_handlers()
    with server.capture_signals():
        pass


@pytest.mark.unit
def test_launcher_suppresses_startup_failure_events_during_expected_shutdown(monkeypatch):
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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
def test_spawn_restarted_launcher_clears_main_server_init_marker_from_relaunch_env(monkeypatch):
    from launcher_core import runtime as launcher

    popen_calls = []

    class _PipeStream:
        def isatty(self):
            return False

    monkeypatch.setattr(launcher.sys, "stdin", _PipeStream())
    monkeypatch.setattr(launcher.sys, "stdout", _PipeStream())
    monkeypatch.setattr(launcher.sys, "stderr", _PipeStream())
    monkeypatch.setattr(launcher, "_build_launcher_relaunch_command", lambda: ["python", "launcher.py"])
    monkeypatch.setenv("_NEKO_MAIN_SERVER_INITIALIZED", "1")
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: popen_calls.append((command, kwargs)),
    )

    launcher._spawn_restarted_launcher()

    assert len(popen_calls) == 1
    _, kwargs = popen_calls[0]
    assert kwargs["env"].get("_NEKO_MAIN_SERVER_INITIALIZED") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_syncs_memory_server_after_startup_import():
    from app import main_server

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        await main_server._sync_memory_server_after_startup_import({"action": "imported"})

    mock_reload.assert_awaited_once_with(reason="Steam Auto-Cloud startup import")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_skips_memory_reload_when_startup_import_did_not_run():
    from app import main_server

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=True),
    ) as mock_reload:
        await main_server._sync_memory_server_after_startup_import({"action": "skipped"})

    mock_reload.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_requests_local_server_shutdown_in_multi_process_mode(monkeypatch):
    from app import main_server

    shutdown_mock = AsyncMock()
    start_config = {
        "browser_mode_enabled": False,
        "browser_page": "",
        "shutdown_memory_server_on_exit": False,
        "request_runtime_shutdown": None,
        "server": object(),
    }

    monkeypatch.setenv("NEKO_LAUNCH_MODE", "multi")
    monkeypatch.setenv("NEKO_LAUNCHER_PID", "54321")
    monkeypatch.setattr(main_server, "get_start_config", lambda: start_config)
    monkeypatch.setattr(main_server, "shutdown_server_async", shutdown_mock)

    await main_server.request_application_shutdown_async()

    shutdown_mock.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_uses_runtime_shutdown_bridge_when_available(monkeypatch):
    from app import main_server

    callback_calls = []

    def _request_runtime_shutdown():
        callback_calls.append("called")

    monkeypatch.setattr(
        main_server,
        "get_start_config",
        lambda: {
            "browser_mode_enabled": False,
            "browser_page": "",
            "shutdown_memory_server_on_exit": False,
            "request_runtime_shutdown": _request_runtime_shutdown,
            "server": None,
        },
    )
    monkeypatch.setattr(main_server, "shutdown_server_async", AsyncMock())

    await main_server.request_application_shutdown_async()

    assert callback_calls == ["called"]


@pytest.mark.unit
def test_launcher_cleanup_waits_for_main_server_shutdown_completion(monkeypatch):
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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
    from launcher_core import runtime as launcher

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


@pytest.mark.unit
def test_wait_for_servers_treats_main_server_exit_as_storage_restart_during_startup(monkeypatch):
    from launcher_core import runtime as launcher

    marked_shutdown = []
    startup_failures = []

    class _DummyEvent:
        def set(self):
            return None

    class _DummyThread:
        def __init__(self, *args, **kwargs):
            self.daemon = False

        def start(self):
            return None

        def join(self):
            return None

    class _DummyProcess:
        exitcode = 0

        def is_alive(self):
            return False

    monkeypatch.setattr(launcher.threading, "Event", _DummyEvent)
    monkeypatch.setattr(launcher.threading, "Thread", _DummyThread)
    monkeypatch.setattr(launcher, "SERVERS", [{"name": "Main Server", "module": "main_server", "port": 43111, "process": _DummyProcess()}], raising=False)
    monkeypatch.setattr(launcher, "check_port", lambda _port: False)
    monkeypatch.setattr(launcher, "_is_pending_storage_restart_request", lambda: True)
    monkeypatch.setattr(launcher, "_mark_expected_launcher_shutdown", lambda: marked_shutdown.append("marked"))
    monkeypatch.setattr(launcher, "report_startup_failure", lambda message, show_dialog=True: startup_failures.append(message))

    result = launcher.wait_for_servers(timeout=1)

    assert result == launcher.STARTUP_WAIT_RESULT_STORAGE_RESTART
    assert marked_shutdown == ["marked"]
    assert startup_failures == []


@pytest.mark.unit
def test_launcher_main_schedules_restart_for_storage_restart_requested_during_startup(monkeypatch):
    from launcher_core import runtime as launcher

    started_modules = []
    cleanup_calls = []
    restart_schedule_calls = []
    release_calls = []
    startup_failures = []
    cleanup_state = {"done": False}

    monkeypatch.setattr(launcher, "_cleanup_done", False)
    monkeypatch.setattr(launcher, "_expected_launcher_shutdown", False)
    monkeypatch.setattr(launcher, "freeze_support", lambda: None)
    monkeypatch.setattr(launcher, "acquire_startup_lock", lambda: True)
    monkeypatch.setattr(launcher, "release_startup_lock", lambda: release_calls.append("released"))
    monkeypatch.setattr(launcher, "apply_port_strategy", lambda: True)
    monkeypatch.setattr(launcher, "register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(launcher, "setup_job_object", lambda: None)
    monkeypatch.setattr(launcher, "_resolve_storage_layout_for_launch", lambda: {})
    monkeypatch.setattr(launcher, "_prepare_cloudsave_runtime_for_launch", lambda: {})
    monkeypatch.setattr(launcher, "_ensure_playwright_browsers", lambda: None)
    monkeypatch.setattr(launcher, "_should_use_merged_mode", lambda: False)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        launcher,
        "start_server",
        lambda server: started_modules.append(server["module"]) or True,
    )
    monkeypatch.setattr(
        launcher,
        "SERVERS",
        [
            {"name": "Memory Server", "module": "memory_server", "import_event": None},
            {"name": "Main Server", "module": "main_server", "import_event": None},
            {"name": "Agent Server", "module": "agent_server", "import_event": None},
        ],
        raising=False,
    )
    monkeypatch.setattr(launcher, "wait_for_servers", lambda timeout=60: launcher.STARTUP_WAIT_RESULT_STORAGE_RESTART)
    def _cleanup_once():
        if cleanup_state["done"]:
            return
        cleanup_state["done"] = True
        cleanup_calls.append("cleanup")

    monkeypatch.setattr(launcher, "cleanup_servers", _cleanup_once)
    monkeypatch.setattr(
        launcher,
        "_maybe_schedule_storage_restart",
        lambda: restart_schedule_calls.append("scheduled") or True,
    )
    monkeypatch.setattr(
        launcher,
        "report_startup_failure",
        lambda message, show_dialog=True: startup_failures.append(message),
    )

    result = launcher.main()

    assert result == 0
    assert started_modules == ["memory_server", "main_server", "agent_server"]
    assert cleanup_calls == ["cleanup"]
    assert restart_schedule_calls == ["scheduled"]
    assert release_calls == []
    assert startup_failures == []
