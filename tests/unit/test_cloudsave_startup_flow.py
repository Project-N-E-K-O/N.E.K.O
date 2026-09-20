from contextlib import contextmanager


import os


from pathlib import Path


from types import SimpleNamespace


from unittest.mock import AsyncMock, Mock, patch


import pytest


@pytest.mark.unit
def test_launcher_phase0_state_failure_starts_recovery_surface_without_rewriting_state(monkeypatch):
    from launcher_core import runtime as launcher

    started = []
    set_root_mode_calls = []
    reported_failures = []
    events = []
    browser_checks = []
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setenv("NEKO_LAUNCH_MODE", "")
    monkeypatch.setattr(launcher.os, "_exit", lambda _code: None)
    monkeypatch.setattr(launcher, "freeze_support", lambda: None)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event, payload=None: events.append((event, payload or {})),
    )
    monkeypatch.setattr(launcher, "install_parent_death_guard", lambda: None)
    monkeypatch.setattr(launcher, "_acquire_single_instance_ownership", lambda: True)
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: None)
    monkeypatch.setattr(launcher, "apply_port_strategy", lambda: True)
    monkeypatch.setattr(launcher, "publish_single_instance_state", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(launcher, "setup_job_object", lambda: None)
    monkeypatch.setattr(launcher, "_resolve_storage_layout_for_launch", lambda: {})
    monkeypatch.setattr(
        launcher,
        "_prepare_cloudsave_runtime_for_launch",
        lambda: (_ for _ in ()).throw(ValueError("malformed root state")),
    )
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(
        launcher,
        "set_root_mode",
        lambda *_args, **_kwargs: set_root_mode_calls.append((_args, _kwargs)),
    )
    monkeypatch.setattr(
        launcher,
        "report_startup_failure",
        lambda message, show_dialog=True: reported_failures.append((message, show_dialog)),
    )
    monkeypatch.setattr(launcher, "_ensure_playwright_browsers", lambda: browser_checks.append(True))
    monkeypatch.setattr(launcher, "_select_launcher_mode", lambda: ("merged", "configured_merged"))
    monkeypatch.setattr(launcher, "run_merged_servers", lambda: started.append(True) or 0)

    assert launcher.main() == 0
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_status_unavailable"
    assert started == [True]
    assert set_root_mode_calls == []
    assert browser_checks == []
    assert reported_failures == []
    assert events[-1][0] == "storage_migration_failed"
    assert events[-1][1]["error_code"] == "storage_status_unavailable"


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
    monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)

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
def test_launcher_consumes_cold_rebind_handoff_before_normal_startup(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    root_state = {
        "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
        "current_root": str(selected_root),
        "last_migration_source": str(selected_root),
        "last_migration_result": f"restart_rebind:{selected_root}",
    }
    config_manager = SimpleNamespace(
        app_docs_dir=selected_root,
        load_root_state=lambda: dict(root_state),
        save_root_state=lambda payload: (root_state.clear(), root_state.update(payload)),
    )
    layout = {
        "selected_root": str(selected_root),
        "anchor_root": str(anchor_root),
        "cloudsave_root": str(anchor_root / "cloudsave"),
        "source": "policy",
    }

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "load_storage_migration", lambda _manager: None)
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda _manager: {"attempted": False, "completed": False},
    )
    monkeypatch.setattr(launcher, "resolve_storage_layout", lambda _manager: layout)
    monkeypatch.setattr(launcher, "export_storage_layout_to_env", lambda _layout: None)

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is False
    assert result["startup_limited"] is False
    assert root_state["mode"] == launcher.ROOT_MODE_NORMAL
    assert root_state["current_root"] == str(selected_root)
    assert root_state["last_known_good_root"] == str(selected_root)
    assert root_state["last_migration_result"] == f"completed_rebind:{selected_root}"


@pytest.mark.unit
def test_launcher_policy_corruption_starts_anchor_only_recovery_generation(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher
    from utils.storage_policy import StoragePolicyError

    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    recovery_manager = SimpleNamespace(
        app_docs_dir=anchor_root,
        app_name="N.E.K.O",
    )
    manager_calls = []

    for key in (
        "NEKO_STORAGE_SELECTED_ROOT",
        "NEKO_STORAGE_ANCHOR_ROOT",
        "NEKO_STORAGE_CLOUDSAVE_ROOT",
        "NEKO_STORAGE_RECOVERY_MODE",
    ):
        monkeypatch.setenv(key, "")

    def _get_manager(*_args, **_kwargs):
        manager_calls.append(True)
        if len(manager_calls) == 1:
            raise StoragePolicyError("malformed")
        return recovery_manager

    monkeypatch.setattr(launcher, "get_config_manager", _get_manager)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "compute_anchor_root", lambda *_args, **_kwargs: anchor_root)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not read a checkpoint through an untrusted policy")
        ),
    )
    events = []
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event, payload=None: events.append((event, payload or {})),
    )

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is True
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "storage_policy_unavailable"
    assert result["layout"]["selected_root"] == str(anchor_root)
    assert result["layout"]["anchor_root"] == str(anchor_root)
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_policy_unavailable"
    assert launcher.os.environ["NEKO_STORAGE_SELECTED_ROOT"] == str(anchor_root)
    assert events[-1][0] == "storage_migration_failed"
    assert events[-1][1]["error_code"] == "storage_policy_unavailable"


@pytest.mark.unit
def test_launcher_checkpoint_corruption_starts_committed_layout_recovery_generation(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    config_manager = SimpleNamespace(app_docs_dir=selected_root)
    layout = {
        "selected_root": str(selected_root),
        "anchor_root": str(anchor_root),
        "cloudsave_root": str(anchor_root / "cloudsave"),
        "source": "policy",
    }
    for key in (
        "NEKO_STORAGE_SELECTED_ROOT",
        "NEKO_STORAGE_ANCHOR_ROOT",
        "NEKO_STORAGE_CLOUDSAVE_ROOT",
        "NEKO_STORAGE_RECOVERY_MODE",
    ):
        monkeypatch.setenv(key, "")

    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("malformed checkpoint")),
    )
    monkeypatch.setattr(launcher, "resolve_storage_layout", lambda _manager: layout)
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not execute an unreadable checkpoint")
        ),
    )
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is True
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "storage_status_unavailable"
    assert result["layout"] == layout
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_status_unavailable"
    assert launcher.os.environ["NEKO_STORAGE_SELECTED_ROOT"] == str(selected_root)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("migration_result", "terminal_event", "terminal_field"),
    [
        (
            {
                "attempted": True,
                "completed": True,
                "source_root": "/source/N.E.K.O",
                "target_root": "/target/N.E.K.O",
            },
            "storage_migration_completed",
            ("source_root", "/source/N.E.K.O"),
        ),
        (
            {
                "attempted": True,
                "completed": False,
                "error_code": "verification_failed",
                "error_message": "digest mismatch",
            },
            "storage_migration_failed",
            ("error_message", "digest mismatch"),
        ),
    ],
)
def test_launcher_reports_pending_storage_migration_progress(
    monkeypatch,
    migration_result,
    terminal_event,
    terminal_field,
):
    from launcher_core import runtime as launcher

    config_manager = SimpleNamespace(app_docs_dir=Path("/source/N.E.K.O"))
    events = []
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "clear_storage_layout_env", lambda: None)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda _config_manager: {
            "status": "pending",
            "source_root": "/source/N.E.K.O",
            "target_root": "/target/N.E.K.O",
        },
    )
    monkeypatch.setattr(launcher, "is_storage_migration_pending", lambda _payload: True)
    monkeypatch.setattr(launcher, "run_pending_storage_migration", lambda _manager: migration_result)
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _manager: {
            "selected_root": "/target/N.E.K.O",
            "anchor_root": "/anchor/N.E.K.O",
            "cloudsave_root": "/anchor/N.E.K.O/cloudsave",
        },
    )
    monkeypatch.setattr(launcher, "export_storage_layout_to_env", lambda _layout: None)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event, payload=None: events.append((event, payload or {})),
    )

    launcher._resolve_storage_layout_for_launch()

    assert [event for event, _payload in events] == [
        "storage_migration_processing",
        terminal_event,
    ]
    assert events[1][1][terminal_field[0]] == terminal_field[1]

@pytest.mark.unit
def test_launcher_prepares_cloudsave_runtime_before_starting_services(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

    config_manager = SimpleNamespace(
        app_docs_dir=tmp_path / "N.E.K.O",
        cloudsave_manifest_path=tmp_path / "N.E.K.O" / "cloudsave" / "manifest.json",
        load_root_state=lambda: call_order.append("load_root_state") or {"mode": "normal"},
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

    def _fake_recover(_config_manager):
        call_order.append("recover_interrupted_publication")
        return None

    def _fake_recover_stale(_config_manager, _root_state):
        call_order.append("recover_stale_mode")
        return None

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
    monkeypatch.setattr(
        launcher,
        "recover_interrupted_legacy_runtime_import",
        _fake_recover,
    )
    monkeypatch.setattr(
        launcher,
        "_recover_stale_write_blocking_mode",
        _fake_recover_stale,
    )
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
    state_load_index = call_order.index("load_root_state")
    stale_recovery_index = call_order.index("recover_stale_mode")
    recovery_index = call_order.index("recover_interrupted_publication")
    bootstrap_index = call_order.index("bootstrap")
    fence_enter_index = call_order.index(("fence_enter", launcher.ROOT_MODE_BOOTSTRAP_IMPORTING, "launcher_phase0_bootstrap"))
    import_index = call_order.index(("import", "launcher_phase0_prelaunch_import", True))
    fence_exit_index = call_order.index(("fence_exit", launcher.ROOT_MODE_BOOTSTRAP_IMPORTING, "launcher_phase0_bootstrap"))
    assert state_preflight_index < state_load_index < stale_recovery_index < recovery_index < fence_enter_index < bootstrap_index < import_index < fence_exit_index
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
