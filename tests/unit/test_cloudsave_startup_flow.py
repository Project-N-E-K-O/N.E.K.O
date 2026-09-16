from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
def test_launcher_preserves_corrupt_root_state_when_committed_external_root_is_offline(
    monkeypatch,
    tmp_path,
):
    from launcher_core import runtime as launcher
    from utils.config_manager import ConfigManager, reset_config_manager_cache
    from utils.storage.policy import save_storage_policy

    standard_root = tmp_path / "anchor-base"
    monkeypatch.setattr(
        ConfigManager,
        "_get_documents_directory",
        lambda _self: tmp_path / "runtime-parent",
    )
    monkeypatch.setattr(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        lambda _self: [standard_root],
    )
    for key in (
        "NEKO_STORAGE_SELECTED_ROOT",
        "NEKO_STORAGE_ANCHOR_ROOT",
        "NEKO_STORAGE_CLOUDSAVE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    exported = []
    monkeypatch.setattr(
        launcher,
        "export_storage_layout_to_env",
        lambda layout: exported.append(layout),
    )

    initial_manager = ConfigManager("N.E.K.O")
    unavailable_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_root,
        selection_source="custom",
    )
    root_state_path = initial_manager.anchor_root / "state" / "root_state.json"
    root_state_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = b'{"mode":'
    root_state_path.write_bytes(corrupt_bytes)
    reset_config_manager_cache()

    try:
        result = launcher._resolve_storage_layout_for_launch()
    finally:
        reset_config_manager_cache()

    assert result["startup_blocked"] is True
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "storage_status_unavailable"
    assert result["layout"]["source"] == "storage_status_unavailable_recovery"
    assert exported == [result["layout"]]
    assert root_state_path.read_bytes() == corrupt_bytes


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


@pytest.mark.unit
def test_launcher_disables_cloudsave_when_local_state_directory_fails(monkeypatch):
    from launcher_core import runtime as launcher

    class _LocalStateFailure(OSError):
        local_state_directory_error = True

    set_root_mode_calls = []
    reported_failures = []

    monkeypatch.setattr(launcher, "freeze_support", lambda: None)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "install_parent_death_guard", lambda: None)
    monkeypatch.setattr(launcher, "_acquire_single_instance_ownership", lambda: True)
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
def test_launcher_promotes_first_run_layout_to_pre_phase0_limited_generation(
    monkeypatch,
    tmp_path,
):
    from launcher_core import runtime as launcher

    default_root = (tmp_path / "default" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    config_manager = SimpleNamespace(app_docs_dir=default_root)
    layout = {
        "selected_root": str(default_root),
        "anchor_root": str(anchor_root),
        "cloudsave_root": str(anchor_root / "cloudsave"),
        "source": "runtime_default",
    }
    # Register this key with monkeypatch even when it starts absent: launcher
    # code writes it directly, and the storage-root guard verifies teardown.
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(
        launcher,
        "get_config_manager",
        lambda _app_name, **_kwargs: config_manager,
    )
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
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "selection_required"
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "selection_required"


@pytest.mark.unit
@pytest.mark.parametrize("checkpoint_status", (None, "completed", "failed"))
def test_launcher_blocks_phase0_when_restart_intent_lost_its_checkpoint(
    monkeypatch,
    tmp_path,
    checkpoint_status,
):
    from launcher_core import runtime as launcher

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    root_state = {
        "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
        "last_known_good_root": str(selected_root),
        "last_migration_result": f"restart_pending:{selected_root}",
    }
    config_manager = SimpleNamespace(
        app_docs_dir=selected_root,
        load_root_state=lambda: dict(root_state),
    )
    layout = launcher.build_storage_layout(
        selected_root=selected_root,
        anchor_root=anchor_root,
        source="policy",
    )

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "clear_storage_layout_env", lambda: None)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(
        launcher,
        "get_config_manager",
        lambda *_args, **_kwargs: config_manager,
    )
    old_checkpoint = (
        None
        if checkpoint_status is None
        else {
            "status": checkpoint_status,
            "source_root": str(selected_root),
            "target_root": str(tmp_path / "old-target" / "N.E.K.O"),
        }
    )
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda _manager: old_checkpoint,
    )
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda _manager: {"attempted": False, "completed": False},
    )
    monkeypatch.setattr(launcher, "resolve_storage_layout", lambda _manager: layout)
    monkeypatch.setattr(
        launcher,
        "_consume_storage_rebind_handoff",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(launcher, "export_storage_layout_to_env", lambda _layout: None)

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "recovery_required"
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "recovery_required"


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
def test_launcher_keeps_cold_rebind_handoff_limited_when_consume_fails(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    root_state = {
        "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
        "last_migration_source": str(selected_root),
        "last_migration_result": f"restart_rebind:{selected_root}",
    }
    config_manager = SimpleNamespace(
        app_docs_dir=selected_root,
        load_root_state=lambda: dict(root_state),
        save_root_state=lambda _payload: (_ for _ in ()).throw(OSError("read only")),
    )
    layout = {
        "selected_root": str(selected_root),
        "anchor_root": str(anchor_root),
        "cloudsave_root": str(anchor_root / "cloudsave"),
        "source": "policy",
    }
    events = []

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
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event, payload=None: events.append((event, payload or {})),
    )

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is True
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "storage_status_unavailable"
    assert root_state["mode"] == launcher.ROOT_MODE_MAINTENANCE_READONLY
    assert root_state["last_migration_result"] == f"restart_rebind:{selected_root}"
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_status_unavailable"
    assert events[-1][1]["error_code"] == "storage_rebind_finalize_failed"


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
def test_launcher_policy_corruption_during_checkpoint_recovery_uses_anchor_only_generation(
    monkeypatch,
    tmp_path,
):
    from launcher_core import runtime as launcher
    from utils.storage_policy import StoragePolicyError

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    committed_manager = SimpleNamespace(app_docs_dir=selected_root)
    recovery_manager = SimpleNamespace(app_docs_dir=anchor_root, app_name="N.E.K.O")
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
        return committed_manager if len(manager_calls) == 1 else recovery_manager

    monkeypatch.setattr(launcher, "get_config_manager", _get_manager)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "compute_anchor_root", lambda *_args, **_kwargs: anchor_root)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("malformed checkpoint")),
    )
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _manager: (_ for _ in ()).throw(StoragePolicyError("changed after first read")),
    )
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)

    result = launcher._resolve_storage_layout_for_launch()

    assert result["limited_mode_reason"] == "storage_policy_unavailable"
    assert result["layout"]["selected_root"] == str(anchor_root)
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_policy_unavailable"


@pytest.mark.unit
def test_launcher_policy_corruption_during_final_layout_resolution_uses_anchor_only_generation(
    monkeypatch,
    tmp_path,
):
    from launcher_core import runtime as launcher
    from utils.storage_policy import StoragePolicyError

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    committed_manager = SimpleNamespace(app_docs_dir=selected_root)
    recovery_manager = SimpleNamespace(app_docs_dir=anchor_root, app_name="N.E.K.O")
    managers = iter((committed_manager, committed_manager, recovery_manager))

    for key in (
        "NEKO_STORAGE_SELECTED_ROOT",
        "NEKO_STORAGE_ANCHOR_ROOT",
        "NEKO_STORAGE_CLOUDSAVE_ROOT",
        "NEKO_STORAGE_RECOVERY_MODE",
    ):
        monkeypatch.setenv(key, "")

    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: next(managers))
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "compute_anchor_root", lambda *_args, **_kwargs: anchor_root)
    monkeypatch.setattr(launcher, "load_storage_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda *_args, **_kwargs: {"attempted": False, "completed": False},
    )
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _manager: (_ for _ in ()).throw(StoragePolicyError("changed before final routing")),
    )
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)

    result = launcher._resolve_storage_layout_for_launch()

    assert result["limited_mode_reason"] == "storage_policy_unavailable"
    assert result["layout"]["selected_root"] == str(anchor_root)
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_policy_unavailable"


@pytest.mark.unit
def test_launcher_forces_source_layout_when_recovery_metadata_is_degraded(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

    source_root = (tmp_path / "source" / "N.E.K.O").resolve()
    target_root = (tmp_path / "target" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "configured-anchor" / "N.E.K.O").resolve()
    platform_anchor = (tmp_path / "platform-anchor" / "N.E.K.O").resolve()
    config_manager = SimpleNamespace(
        app_name="N.E.K.O",
        app_docs_dir=target_root,
        anchor_root=anchor_root,
        _get_standard_data_directory_candidates=lambda: [platform_anchor.parent],
    )
    exported = []
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "clear_storage_layout_env", lambda: None)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda _manager: {
            "status": "committing",
            "source_root": str(source_root),
            "target_root": str(target_root),
        },
    )
    monkeypatch.setattr(launcher, "is_storage_migration_pending", lambda _payload: True)
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda _manager: {
            "attempted": True,
            "completed": False,
            "source_root": str(source_root),
            "target_root": str(target_root),
            "force_recovery_layout": True,
            "payload": {
                "status": "failed",
                "source_root": str(source_root),
                "target_root": str(target_root),
                "recovery_metadata_degraded": True,
            },
        },
    )
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _manager: (_ for _ in ()).throw(AssertionError("must not select stale target policy")),
    )
    monkeypatch.setattr(launcher, "export_storage_layout_to_env", lambda layout: exported.append(layout))
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is True
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "recovery_required"
    assert result["layout"]["selected_root"] == str(source_root)
    assert result["layout"]["anchor_root"] == str(anchor_root)
    assert result["layout"]["cloudsave_root"] == str(anchor_root / "cloudsave")
    assert result["layout"]["source"] == "migration_failure_recovery"
    assert exported == [result["layout"]]


@pytest.mark.unit
def test_launcher_keeps_terminal_migration_failure_behind_recovery_gate(monkeypatch, tmp_path):
    from launcher_core import runtime as launcher

    source_root = (tmp_path / "source" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "anchor" / "N.E.K.O").resolve()
    checkpoint = {
        "status": "failed",
        "source_root": str(source_root),
        "target_root": str(tmp_path / "target" / "N.E.K.O"),
        "recovery_metadata_degraded": False,
    }
    config_manager = SimpleNamespace(
        app_docs_dir=source_root,
        load_root_state=lambda: {"mode": launcher.ROOT_MODE_DEFERRED_INIT},
    )
    exported = []
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "clear_storage_layout_env", lambda: None)
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "load_storage_migration", lambda *_args, **_kwargs: checkpoint)
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda *_args, **_kwargs: {
            "attempted": False,
            "completed": False,
            "payload": checkpoint,
        },
    )
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _manager: launcher.build_storage_layout(
            selected_root=source_root,
            anchor_root=anchor_root,
            source="policy",
        ),
    )
    monkeypatch.setattr(launcher, "_consume_storage_rebind_handoff", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "export_storage_layout_to_env", lambda layout: exported.append(layout))

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is False
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "recovery_required"
    assert result["layout"]["selected_root"] == str(source_root)
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "recovery_required"
    assert exported == [result["layout"]]


@pytest.mark.unit
def test_launcher_does_not_relaunch_when_recovery_metadata_is_degraded(monkeypatch):
    from launcher_core import runtime as launcher

    config_manager = SimpleNamespace(load_root_state=lambda: {"mode": launcher.ROOT_MODE_NORMAL})
    released = []
    spawned = []
    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "load_storage_migration", lambda _manager: None)
    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "startup_blocked": True,
            "layout": {"selected_root": "/source/N.E.K.O"},
            "migration_result": {"attempted": True, "force_recovery_layout": True},
        },
    )
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: released.append(True))
    monkeypatch.setattr(launcher, "_spawn_restarted_launcher", lambda: spawned.append(True))

    assert launcher._maybe_schedule_storage_restart() is False
    assert released == []
    assert spawned == []


@pytest.mark.unit
def test_launcher_missing_recovery_source_starts_anchor_only_limited_generation(
    monkeypatch,
    tmp_path,
):
    from launcher_core import runtime as launcher

    selected_root = (tmp_path / "selected" / "N.E.K.O").resolve()
    anchor_root = (tmp_path / "configured-anchor" / "N.E.K.O").resolve()
    platform_anchor = (tmp_path / "platform-anchor" / "N.E.K.O").resolve()
    config_manager = SimpleNamespace(
        app_docs_dir=selected_root,
        anchor_root=anchor_root,
    )
    exported = []
    events = []

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "")
    monkeypatch.setattr(launcher, "reset_config_manager_cache", lambda: None)
    monkeypatch.setattr(launcher, "get_config_manager", lambda *_args, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda _manager: {"status": "failed", "recovery_metadata_degraded": True},
    )
    monkeypatch.setattr(
        launcher,
        "run_pending_storage_migration",
        lambda _manager: {
            "attempted": False,
            "completed": False,
            "force_recovery_layout": True,
            "payload": {"status": "failed", "recovery_metadata_degraded": True},
        },
    )
    monkeypatch.setattr(
        launcher,
        "resolve_storage_layout",
        lambda _manager: (_ for _ in ()).throw(
            AssertionError("must not select an unproven policy root")
        ),
    )
    monkeypatch.setattr(
        launcher,
        "compute_anchor_root",
        lambda *_args, **_kwargs: platform_anchor,
    )
    monkeypatch.setattr(launcher, "export_storage_layout_to_env", lambda layout: exported.append(layout))
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event, payload=None: events.append((event, payload or {})),
    )

    result = launcher._resolve_storage_layout_for_launch()

    assert result["startup_blocked"] is True
    assert result["startup_limited"] is True
    assert result["limited_mode_reason"] == "storage_status_unavailable"
    assert result["layout"]["selected_root"] == str(anchor_root)
    assert result["layout"]["anchor_root"] == str(anchor_root)
    assert result["layout"]["cloudsave_root"] == str(anchor_root / "cloudsave")
    assert result["layout"]["source"] == "storage_status_unavailable_recovery"
    assert exported == [result["layout"]]
    assert launcher.os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "storage_status_unavailable"
    assert events[-1][1]["error_code"] == "storage_recovery_source_unavailable"


@pytest.mark.unit
def test_launcher_starts_only_recovery_surface_when_recovery_metadata_is_degraded(monkeypatch):
    from launcher_core import runtime as launcher

    prepared = []
    started = []
    failures = []
    monkeypatch.setenv("NEKO_LAUNCH_MODE", "")
    monkeypatch.setattr(launcher.os, "_exit", lambda _code: None)
    monkeypatch.setattr(launcher, "_cleanup_done", False)
    monkeypatch.setattr(launcher, "_owner_death_in_progress", False)
    monkeypatch.setattr(launcher, "freeze_support", lambda: None)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "install_parent_death_guard", lambda: None)
    monkeypatch.setattr(launcher, "_acquire_single_instance_ownership", lambda: True)
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: None)
    monkeypatch.setattr(launcher, "apply_port_strategy", lambda: True)
    monkeypatch.setattr(launcher, "publish_single_instance_state", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(launcher, "setup_job_object", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "startup_blocked": True,
            "startup_limited": True,
            "limited_mode_reason": "recovery_required",
            "layout": {"selected_root": "/source/N.E.K.O"},
        },
    )
    monkeypatch.setattr(launcher, "_prepare_cloudsave_runtime_for_launch", lambda: prepared.append(True))
    monkeypatch.setattr(launcher, "_select_launcher_mode", lambda: ("merged", "configured_merged"))
    monkeypatch.setattr(launcher, "run_merged_servers", lambda: started.append("recovery") or 0)
    monkeypatch.setattr(launcher, "report_startup_failure", lambda message, **_kwargs: failures.append(message))
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: None)
    monkeypatch.setattr(launcher, "SERVERS", [])

    assert launcher.main() == 0
    assert prepared == []
    assert started == ["recovery"]
    assert failures == []


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
        pytest.fail,
    )

    assert launcher.apply_port_strategy() is True

    assert launcher._select_launcher_mode() == (
        "multi",
        "partial_existing_services",
    )
    assert launcher._partial_or_mixed_existing_backend is True
    assert launcher._existing_neko_services == set()
    selected = dict(emitted_events)["port_plan"]["selected"]
    for key in public_ports:
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
def test_start_server_delivers_internal_control_token_as_private_process_argument(monkeypatch):
    from launcher_core import runtime as launcher

    captured = {}

    class _Process:
        pid = 123

        def __init__(self, *, target, args, daemon):
            captured.update(target=target, args=args, daemon=daemon)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(launcher, "Process", _Process)
    monkeypatch.setattr(launcher, "Event", object)
    monkeypatch.setattr(launcher, "check_port", lambda _port: False)
    monkeypatch.setattr(launcher, "get_internal_http_auth_token", lambda: "t" * 43)

    server = {
        "name": "Memory Server",
        "module": "memory_server",
        "port": 43112,
    }

    assert launcher.start_server(server) is True
    assert captured["target"] is launcher.run_memory_server
    assert isinstance(captured["args"][-1], bytearray)
    assert captured["args"][-1] == b"\0" * 43
    assert captured["daemon"] is False
    assert captured["started"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "runner_name",
    ("run_memory_server", "run_agent_server", "run_main_server"),
)
def test_launcher_owned_server_installs_control_token_before_child_setup(
    monkeypatch,
    runner_name,
):
    from launcher_core import runtime as launcher

    class _StopBeforeImports(BaseException):
        pass

    installed = []

    monkeypatch.setattr(launcher, "install_internal_http_auth_token", installed.append)
    monkeypatch.setattr(
        launcher,
        "_apply_child_process_signal_policy",
        lambda: (_ for _ in ()).throw(_StopBeforeImports),
    )

    token_buffer = bytearray(b"c" * 43)
    with pytest.raises(_StopBeforeImports):
        getattr(launcher, runner_name)(None, None, None, None, token_buffer)

    assert installed == ["c" * 43]
    assert token_buffer == b"\0" * 43


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
    root_state = {
        "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
        "last_migration_result": "restart_rebind:/tmp/original-root/N.E.K.O",
    }

    config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/original-root/N.E.K.O"),
        anchor_root=Path("/tmp/anchor-root/N.E.K.O"),
        load_root_state=lambda: dict(root_state),
        save_root_state=lambda payload: (root_state.clear(), root_state.update(payload)),
    )

    def _resolve_after_consuming_rebind():
        root_state.update(
            {
                "mode": launcher.ROOT_MODE_NORMAL,
                "current_root": "/tmp/original-root/N.E.K.O",
                "last_known_good_root": "/tmp/original-root/N.E.K.O",
                "last_migration_result": "completed_rebind:/tmp/original-root/N.E.K.O",
            }
        )
        return {
            "layout": {
                "selected_root": "/tmp/original-root/N.E.K.O",
                "anchor_root": "/tmp/anchor-root/N.E.K.O",
                "cloudsave_root": "/tmp/anchor-root/N.E.K.O/cloudsave",
            },
            "migration_result": {
                "attempted": False,
                "completed": False,
            },
        }

    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        _resolve_after_consuming_rebind,
    )
    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "load_storage_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: released.__setitem__("called", True))
    monkeypatch.setattr(launcher, "_spawn_restarted_launcher", lambda: spawned.__setitem__("called", True))

    result = launcher._maybe_schedule_storage_restart()

    assert result is True
    assert released["called"] is True
    assert spawned["called"] is True
    assert root_state["mode"] == launcher.ROOT_MODE_NORMAL
    assert root_state["last_migration_result"] == "completed_rebind:/tmp/original-root/N.E.K.O"
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
                "relaunch": "self",
            },
        )
    ]


@pytest.mark.unit
def test_launcher_keeps_rebind_blocked_when_handoff_resolution_fails(monkeypatch):
    from launcher_core import runtime as launcher

    emitted_events = []
    released = {"called": False}
    spawned = {"called": False}
    root_state = {
        "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
        "last_migration_result": "restart_rebind:/tmp/runtime/N.E.K.O",
        "last_migration_source": "/tmp/runtime/N.E.K.O",
    }
    config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/runtime/N.E.K.O"),
        anchor_root=Path("/tmp/anchor/N.E.K.O"),
        load_root_state=lambda: dict(root_state),
    )

    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(launcher, "load_storage_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "layout": {"selected_root": "/tmp/runtime/N.E.K.O"},
            "migration_result": {"attempted": False, "completed": False},
            "startup_blocked": True,
            "startup_limited": True,
            "limited_mode_reason": "storage_status_unavailable",
        },
    )
    monkeypatch.setattr(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    )
    monkeypatch.setattr(
        launcher,
        "release_single_instance_ownership",
        lambda: released.__setitem__("called", True),
    )
    monkeypatch.setattr(
        launcher,
        "_spawn_restarted_launcher",
        lambda: spawned.__setitem__("called", True),
    )

    assert launcher._maybe_schedule_storage_restart() is False
    assert root_state["mode"] == launcher.ROOT_MODE_MAINTENANCE_READONLY
    assert released["called"] is False
    assert spawned["called"] is False
    assert emitted_events == []


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
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: released.__setitem__("called", True))
    monkeypatch.setattr(launcher, "_spawn_restarted_launcher", lambda: spawned.__setitem__("called", True))

    result = launcher._maybe_schedule_storage_restart()

    assert result is True
    assert released["called"] is True
    assert spawned["called"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("checkpoint_status", "error_code"),
    (
        ("rollback_required", "rollback_failed"),
        ("recovery_required", "source_recovery_unverifiable"),
    ),
)
def test_launcher_does_not_relaunch_recovery_generation_when_recovery_still_fails(
    monkeypatch,
    checkpoint_status,
    error_code,
):
    from launcher_core import runtime as launcher

    released = {"called": False}
    spawned = {"called": False}
    events = []
    config_manager = SimpleNamespace(
        load_root_state=lambda: {
            # Even if root_state persistence failed and the old mode survived,
            # the pre-existing recovery checkpoint must stop another handoff.
            "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
            "last_migration_result": f"failed:{error_code}",
        }
    )
    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda _config_manager: {
            "status": checkpoint_status,
            "source_root": "/tmp/source/N.E.K.O",
            "target_root": "/tmp/target/N.E.K.O",
        },
    )
    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "layout": {"selected_root": "/tmp/source/N.E.K.O"},
            "migration_result": {
                "attempted": True,
                "completed": False,
                "error_code": error_code,
                "payload": {
                    "status": checkpoint_status,
                    "source_root": "/tmp/source/N.E.K.O",
                    "target_root": "/tmp/target/N.E.K.O",
                },
            },
        },
    )
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *args: events.append(args))
    monkeypatch.setattr(
        launcher,
        "release_single_instance_ownership",
        lambda: released.__setitem__("called", True),
    )
    monkeypatch.setattr(
        launcher,
        "_spawn_restarted_launcher",
        lambda: spawned.__setitem__("called", True),
    )

    assert launcher._maybe_schedule_storage_restart() is False
    assert released["called"] is False
    assert spawned["called"] is False
    assert events == []


@pytest.mark.unit
def test_launcher_allows_one_recovery_handoff_after_requested_migration_rollback_fails(monkeypatch):
    from launcher_core import runtime as launcher

    released = {"called": False}
    spawned = {"called": False}
    config_manager = SimpleNamespace(
        load_root_state=lambda: {
            "mode": launcher.ROOT_MODE_MAINTENANCE_READONLY,
            "last_migration_result": "restart_pending:/tmp/target/N.E.K.O",
        }
    )
    monkeypatch.setattr(launcher, "get_config_manager", lambda _app_name, **_kwargs: config_manager)
    monkeypatch.setattr(
        launcher,
        "load_storage_migration",
        lambda _config_manager: {
            "status": "pending",
            "source_root": "/tmp/source/N.E.K.O",
            "target_root": "/tmp/target/N.E.K.O",
        },
    )
    monkeypatch.setattr(
        launcher,
        "_resolve_storage_layout_for_launch",
        lambda: {
            "layout": {"selected_root": "/tmp/source/N.E.K.O"},
            "migration_result": {
                "attempted": True,
                "completed": False,
                "error_code": "rollback_failed",
                "payload": {
                    "status": "rollback_required",
                    "source_root": "/tmp/source/N.E.K.O",
                    "target_root": "/tmp/target/N.E.K.O",
                },
            },
        },
    )
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "release_single_instance_ownership",
        lambda: released.__setitem__("called", True),
    )
    monkeypatch.setattr(
        launcher,
        "_spawn_restarted_launcher",
        lambda: spawned.__setitem__("called", True),
    )

    assert launcher._maybe_schedule_storage_restart() is True
    assert released["called"] is True
    assert spawned["called"] is True


@pytest.mark.unit
def test_spawn_restarted_launcher_keeps_stdio_even_in_a_tty_session(monkeypatch):
    """A tty session is still an owner, so the replacement stays in the foreground.

    This used to send the replacement's stdio to ``/dev/null`` whenever any
    standard stream was a tty. Combined with the ``start_new_session`` that used
    to accompany it, that produced a runtime nobody could see and nobody owned.
    Foreground residency means the replacement keeps the owner's stdio: its
    ``NEKO_EVENT`` stream stays readable and, on POSIX, an inherited stdin pipe
    is itself the parent-death signal.
    """
    from launcher_core import runtime as launcher

    popen_calls = []

    class _TTYStream:
        def isatty(self):
            return True

    monkeypatch.setattr(launcher.sys, "stdin", _TTYStream())
    monkeypatch.setattr(launcher.sys, "stdout", _TTYStream())
    monkeypatch.setattr(launcher.sys, "stderr", _TTYStream())
    monkeypatch.setattr(launcher, "_build_launcher_relaunch_command", lambda: ["python", "launcher.py"])
    monkeypatch.setattr(launcher, "_relax_job_kill_on_close", lambda: None)
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
    from config import MEMORY_SERVER_PORT
    from utils.internal_http_auth import internal_http_auth_headers

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "success"},
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=client,
    ):
        await main_server._sync_memory_server_after_startup_import({"action": "imported"})

    client.post.assert_awaited_once_with(
        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/reload",
        json={},
        headers=internal_http_auth_headers(),
        timeout=5.0,
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "request_error"),
    [
        (SimpleNamespace(status_code=503, json=lambda: {"status": "error"}), None),
        (SimpleNamespace(status_code=200, json=lambda: {"status": "error"}), None),
        (None, TimeoutError("reload timed out")),
    ],
)
async def test_main_server_startup_import_fails_when_memory_reload_is_not_confirmed(
    response,
    request_error,
):
    from app import main_server

    post = AsyncMock(
        return_value=response,
        side_effect=request_error,
    )
    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=SimpleNamespace(post=post),
    ):
        with pytest.raises(RuntimeError, match="memory_server reload"):
            await main_server._sync_memory_server_after_startup_import(
                {"action": "imported"}
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_skips_memory_reload_when_startup_import_did_not_run():
    from app import main_server

    client = SimpleNamespace(post=AsyncMock())
    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=client,
    ):
        await main_server._sync_memory_server_after_startup_import({"action": "skipped"})

    client.post.assert_not_awaited()


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

    def _request_runtime_shutdown(*, reason):
        callback_calls.append(reason)

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

    await main_server.request_application_shutdown_async(reason="desktop_owner_exit")

    assert callback_calls == ["desktop_owner_exit"]


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
    monkeypatch.setattr(launcher, "install_parent_death_guard", lambda: None)
    monkeypatch.setattr(launcher, "_acquire_single_instance_ownership", lambda: True)
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: release_calls.append("released"))
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
