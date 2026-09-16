import ast
import contextlib
import json
import os
import shutil
import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _role_state_from_session_managers(session_managers: dict) -> dict:
    """Build a role_state dict seeded with the given session_managers.

    Post-#855 consolidation: the old module-level ``session_manager`` dict
    became ``role_state[name].session_manager``. ``sync_shutdown_event`` /
    ``sync_process`` were later removed when cross_server moved from daemon
    thread to a main-loop ``asyncio.Task``. Tests that want to stub
    shutdown-time behavior construct RoleState stubs here (with live
    Queue/Lock so any adapter access does not explode).
    """
    from app.main_server import RoleState, _SyncMessageQueue
    return {
        name: RoleState(
            sync_message_queue=_SyncMessageQueue(),
            websocket_lock=asyncio.Lock(),
            session_manager=session_manager,
        )
        for name, session_manager in session_managers.items()
    }

import pytest

from utils.cloudsave_autocloud import CloudSaveManager
from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment
from utils.cloudsave_runtime import export_cloudsave_character_unit
from utils.cloudsave_runtime import import_cloudsave_character_unit
from utils.steam_cloud_bundle import (
    REMOTE_BUNDLE_FILENAME,
    REMOTE_META_FILENAME,
    download_cloudsave_bundle_from_steam,
    upload_cloudsave_bundle_to_steam,
)
from utils.config_manager import ConfigManager
from config import AUTOSTART_CSRF_TOKEN
from utils.file_utils import atomic_write_json
from utils.internal_http_auth import (
    INTERNAL_HTTP_AUTH_HEADER,
    internal_http_auth_headers,
)
from utils.storage_location_bootstrap import clear_runtime_storage_blocking_reason


@pytest.fixture(autouse=True)
def _reset_runtime_storage_blocking_overlay():
    clear_runtime_storage_blocking_reason()
    yield
    clear_runtime_storage_blocking_reason()


def _make_config_manager(tmp_root: Path):
    cleared_env = {
        "NEKO_STORAGE_SELECTED_ROOT": "",
        "NEKO_STORAGE_ANCHOR_ROOT": "",
        "NEKO_STORAGE_CLOUDSAVE_ROOT": "",
    }
    with patch.dict("os.environ", cleared_env, clear=False), patch.object(
        ConfigManager,
        "_get_documents_directory",
        return_value=tmp_root,
    ), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[tmp_root],
    ), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ), patch.object(
        ConfigManager,
        "_get_project_root",
        return_value=tmp_root,
    ):
        config_manager = ConfigManager("N.E.K.O")
    config_manager.get_legacy_app_root_candidates = lambda: []
    return config_manager


@pytest.mark.unit
def test_recovery_web_import_does_not_create_user_runtime_directories():
    source_path = Path("app/main_server/web_app.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    recovery_guard = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "_storage_recovery_mode"
    )
    guarded_calls = {
        node.func.attr
        for node in ast.walk(ast.Module(body=recovery_guard.body, type_ignores=[]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {
        "ensure_live2d_directory",
        "ensure_vrm_directory",
        "ensure_mmd_directory",
        "ensure_pngtuber_directory",
        "ensure_chara_directory",
        "initialize",
    } <= guarded_calls


def _write_runtime_state(cm, *, character_name: str, recent_message: str = "你好"):
    from utils.config_manager import set_reserved

    characters = cm.get_default_characters()
    template_name = next(iter(characters["猫娘"]))
    characters["猫娘"] = {
        character_name: characters["猫娘"][template_name]
    }
    characters["当前猫娘"] = character_name
    set_reserved(characters["猫娘"][character_name], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"][character_name], "avatar", "asset_source", "steam_workshop")
    set_reserved(characters["猫娘"][character_name], "avatar", "asset_source_id", "123456")
    set_reserved(characters["猫娘"][character_name], "avatar", "live2d", "model_path", "example/example.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    character_memory_dir = Path(cm.memory_dir) / character_name
    character_memory_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        character_memory_dir / "recent.json",
        [{"role": "user", "content": recent_message}],
        ensure_ascii=False,
        indent=2,
    )

    workshop_model_dir = Path(cm.workshop_dir) / "123456" / "example"
    workshop_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workshop_model_dir / "example.model3.json",
        {"Version": 3},
        ensure_ascii=False,
        indent=2,
    )


def _run_launcher_phase0(cm):
    from launcher_core import runtime as launcher

    emitted_events = []
    with patch.object(launcher, "get_config_manager", lambda _app_name, **_kwargs: cm), patch.object(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    ):
        result = launcher._prepare_cloudsave_runtime_for_launch()
    return result, emitted_events


def _build_in_memory_steam_bridge(storage: dict[str, bytes]):
    class _Bridge:
        def cloud_enabled(self) -> bool:
            return True

        def file_exists(self, remote_name: str) -> bool:
            return remote_name in storage

        def read_file(self, remote_name: str) -> bytes:
            if remote_name not in storage:
                raise FileNotFoundError(remote_name)
            return storage[remote_name]

        def write_file(self, remote_name: str, payload: bytes) -> None:
            storage[remote_name] = payload

        def delete_file(self, remote_name: str) -> bool:
            return storage.pop(remote_name, None) is not None

    @contextlib.contextmanager
    def _fake_bridge(*, steamworks=None):
        del steamworks
        yield _Bridge()

    return _fake_bridge


@pytest.mark.unit
def test_launcher_phase0_skips_import_when_cloud_snapshot_is_empty():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        result, emitted_events = _run_launcher_phase0(cm)

        assert result["import_result"]["action"] == "skipped"
        assert result["import_result"]["reason"] == "no_snapshot"
        assert emitted_events[-1][0] == "cloudsave_bootstrap_ready"
        event_import_result = emitted_events[-1][1]["import_result"]
        assert event_import_result["action"] == "skipped"
        assert event_import_result["requested_reason"] == "launcher_phase0_prelaunch_import"
        assert "reason" not in event_import_result


@pytest.mark.unit
def test_launcher_phase0_skips_import_when_snapshot_is_already_applied():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="已应用角色", recent_message="已应用快照")
        export_cloudsave_character_unit(cm, "已应用角色")

        result, emitted_events = _run_launcher_phase0(cm)

        assert result["import_result"]["action"] == "skipped"
        assert result["import_result"]["reason"] == "already_applied"
        status = result["import_result"]["status"]
        assert status["has_snapshot"] is True
        assert status["runtime_has_user_content"] is True
        assert status["last_applied_manifest_fingerprint"] == status["manifest_fingerprint"]
        assert emitted_events[-1][0] == "cloudsave_bootstrap_ready"


@pytest.mark.unit
def test_cloudsave_lifecycle_round_trip_across_two_devices():
    with TemporaryDirectory() as td:
        device_a = _make_config_manager(Path(td) / "device_a")
        device_b = _make_config_manager(Path(td) / "device_b")
        bootstrap_local_cloudsave_environment(device_a)
        bootstrap_local_cloudsave_environment(device_b)

        _write_runtime_state(device_a, character_name="设备A角色", recent_message="来自设备A")
        character_a = device_a.load_characters()["当前猫娘"]
        export_a = export_cloudsave_character_unit(device_a, character_a)
        assert export_a["character_name"] == character_a

        shutil.copytree(device_a.cloudsave_dir, device_b.cloudsave_dir, dirs_exist_ok=True)
        startup_b, _ = _run_launcher_phase0(device_b)
        assert startup_b["import_result"]["action"] == "imported"
        assert device_b.load_characters()["当前猫娘"] == "设备A角色"

        _write_runtime_state(device_b, character_name="设备B角色", recent_message="来自设备B")
        character_b = device_b.load_characters()["当前猫娘"]
        export_b = export_cloudsave_character_unit(device_b, character_b)
        assert export_b["character_name"] == character_b

        shutil.copytree(device_b.cloudsave_dir, device_a.cloudsave_dir, dirs_exist_ok=True)
        startup_a_again, _ = _run_launcher_phase0(device_a)
        assert startup_a_again["import_result"]["action"] == "skipped"
        assert startup_a_again["import_result"]["reason"] == "manual_download_required"
        manual_download = import_cloudsave_character_unit(device_a, "设备B角色")
        assert manual_download["character_name"] == "设备B角色"
        assert "设备B角色" in (device_a.load_characters().get("猫娘") or {})
        assert device_a.load_cloudsave_local_state()["last_successful_import_at"]


@pytest.mark.unit
def test_full_cloudsave_chain_runtime_snapshot_steam_cloud_and_manual_apply():
    with TemporaryDirectory() as td:
        device_a = _make_config_manager(Path(td) / "device_a")
        device_b = _make_config_manager(Path(td) / "device_b")
        bootstrap_local_cloudsave_environment(device_a)
        bootstrap_local_cloudsave_environment(device_b)

        # Device A: runtime truth -> user manual snapshot upload.
        _write_runtime_state(device_a, character_name="跨端角色", recent_message="来自设备A-v1")
        upload_a = export_cloudsave_character_unit(device_a, "跨端角色", overwrite=True)
        assert upload_a["character_name"] == "跨端角色"

        manifest_a = json.loads(device_a.cloudsave_manifest_path.read_text(encoding="utf-8"))
        assert manifest_a["fingerprint"]

        # Simulate Steam cloud on app close: upload local staged cloudsave to remote.
        remote_storage: dict[str, bytes] = {}
        fake_bridge = _build_in_memory_steam_bridge(remote_storage)
        with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
            "utils.steam_cloud_bundle.sys.platform",
            "win32",
        ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", fake_bridge):
            steam_upload = upload_cloudsave_bundle_to_steam(device_a)

        assert steam_upload["success"] is True
        assert steam_upload["action"] == "uploaded"
        assert REMOTE_BUNDLE_FILENAME in remote_storage
        assert REMOTE_META_FILENAME in remote_storage
        remote_meta_v1 = json.loads(remote_storage[REMOTE_META_FILENAME].decode("utf-8"))
        assert remote_meta_v1["manifest_fingerprint"] == manifest_a["fingerprint"]

        # Device B keeps local runtime content so startup should not auto-apply.
        _write_runtime_state(device_b, character_name="设备B本地角色", recent_message="设备B本地旧值")
        assert "跨端角色" not in (device_b.load_characters().get("猫娘") or {})

        # Simulate Steam cloud on app start: download remote staged cloudsave to local snapshot.
        with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
            "utils.steam_cloud_bundle.sys.platform",
            "win32",
        ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", fake_bridge):
            steam_download = download_cloudsave_bundle_from_steam(device_b)

        assert steam_download["success"] is True
        assert steam_download["action"] == "downloaded"
        downloaded_manifest = json.loads(device_b.cloudsave_manifest_path.read_text(encoding="utf-8"))
        assert downloaded_manifest["fingerprint"] == manifest_a["fingerprint"]

        manager_b = CloudSaveManager(device_b)
        startup_b = manager_b.import_if_needed(reason="device_b_startup_after_steam_download")
        assert startup_b["action"] == "skipped"
        assert startup_b["reason"] == "manual_download_required"
        assert "跨端角色" not in (device_b.load_characters().get("猫娘") or {})

        # User manual apply: local snapshot -> runtime truth.
        apply_b = import_cloudsave_character_unit(device_b, "跨端角色")
        assert apply_b["character_name"] == "跨端角色"
        assert "跨端角色" in (device_b.load_characters().get("猫娘") or {})
        restored_recent = json.loads(
            (Path(device_b.memory_dir) / "跨端角色" / "recent.json").read_text(encoding="utf-8")
        )
        assert restored_recent[0]["content"] == "来自设备A-v1"

        # Device B updates runtime truth, then user manually uploads a new snapshot.
        _write_runtime_state(device_b, character_name="跨端角色", recent_message="来自设备B-v2")
        upload_b = export_cloudsave_character_unit(device_b, "跨端角色", overwrite=True)
        assert upload_b["character_name"] == "跨端角色"
        manifest_b = json.loads(device_b.cloudsave_manifest_path.read_text(encoding="utf-8"))
        assert manifest_b["fingerprint"]

        # Simulate Steam cloud upload after normal exit on Device B.
        with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
            "utils.steam_cloud_bundle.sys.platform",
            "win32",
        ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", fake_bridge):
            steam_upload_v2 = upload_cloudsave_bundle_to_steam(device_b)

        assert steam_upload_v2["success"] is True
        assert steam_upload_v2["action"] == "uploaded"
        remote_meta_v2 = json.loads(remote_storage[REMOTE_META_FILENAME].decode("utf-8"))
        assert remote_meta_v2["manifest_fingerprint"] == manifest_b["fingerprint"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_manual_startup_performs_fallback_import_and_continues_boot():
    from app import main_server
    from utils import steam_state

    leaked_steamworks = SimpleNamespace(source="prior-test")
    main_server.steamworks = leaked_steamworks
    steam_state.set_steamworks(leaked_steamworks)

    fake_config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/N.E.K.O"),
        load_root_state=Mock(return_value={"mode": "normal"}),
    )
    fake_import_result = {"success": True, "action": "imported"}
    mock_bootstrap = Mock()
    run_cloudsave_action = AsyncMock(return_value=fake_import_result)
    fake_tracker = SimpleNamespace(
        resume_persistence=Mock(),
        start_periodic_save=Mock(),
        record_app_start=Mock(),
    )
    bridge_start = AsyncMock(return_value=None)

    async def _fake_background_preload():
        return None

    class _DummyBridge:
        def __init__(self, on_agent_event):
            self.on_agent_event = on_agent_event

        async def start(self):
            await bridge_start()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(main_server, "_IS_MAIN_PROCESS", True))
        stack.enter_context(patch.object(main_server, "_runtime_startup_init_completed", False))
        stack.enter_context(
            patch.object(main_server, "_main_runtime_background_tasks_started", False)
        )
        stack.enter_context(patch.object(main_server, "_preload_task", None))
        stack.enter_context(patch.object(main_server, "agent_event_bridge", None))
        stack.enter_context(patch.object(main_server, "steamworks", None))
        stack.enter_context(patch.object(main_server, "_config_manager", fake_config_manager))
        stack.enter_context(
            patch.object(main_server, "get_storage_startup_blocking_reason", Mock(return_value=""))
        )
        stack.enter_context(patch.object(main_server, "_run_cloudsave_manager_action", run_cloudsave_action))
        stack.enter_context(patch.object(main_server, "bootstrap_local_cloudsave_environment", mock_bootstrap))
        mock_init_chars = stack.enter_context(
            patch.object(main_server, "initialize_character_data", AsyncMock(return_value=None))
        )
        mock_sync_reload = stack.enter_context(
            patch.object(main_server, "_sync_memory_server_after_startup_import", AsyncMock(return_value=None))
        )
        mock_set_root_mode = stack.enter_context(
            patch.object(main_server, "set_root_mode", Mock(return_value={"mode": "normal"}))
        )
        mock_init_steam = stack.enter_context(
            patch.object(main_server, "initialize_steamworks", Mock(return_value=None))
        )
        mock_default_steam_info = stack.enter_context(
            patch.object(main_server, "get_default_steam_info", Mock())
        )
        stack.enter_context(patch.object(main_server, "_background_preload", _fake_background_preload))
        stack.enter_context(patch.object(main_server, "MainServerAgentBridge", _DummyBridge))
        mock_set_main_bridge = stack.enter_context(
            patch.object(main_server, "set_main_bridge", Mock())
        )
        mock_mount_workshop = stack.enter_context(
            patch.object(main_server, "_init_and_mount_workshop", AsyncMock(return_value=None))
        )
        mock_start_workers = stack.enter_context(
            patch.object(main_server, "_start_neko_servers_integration_workers", Mock())
        )
        mock_set_steamworks = stack.enter_context(
            patch("main_routers.shared_state.set_steamworks", Mock())
        )
        stack.enter_context(patch("utils.token_tracker.install_hooks", Mock()))
        stack.enter_context(
            patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker)
        )
        stack.enter_context(
            patch("utils.language_utils.initialize_global_language", Mock(return_value="zh-CN"))
        )
        await main_server.on_startup()
        await asyncio.sleep(0)
        mock_bootstrap.assert_called_once_with(fake_config_manager)
        run_cloudsave_action.assert_awaited_once_with(
            "import_if_needed",
            reason="main_server_startup",
            budget_seconds=10.0,
        )
        mock_init_chars.assert_awaited_once_with()
        mock_sync_reload.assert_awaited_once_with(fake_import_result)
        mock_set_root_mode.assert_called_once()
        mock_init_steam.assert_called_once_with()
        # set_steamworks is wired twice on startup: init_shared_state seeds the
        # shared registry with the current (None) handle, then runtime init
        # publishes the freshly initialized handle. Under this mock
        # initialize_steamworks returns None, so both calls carry None.
        assert mock_set_steamworks.call_args_list == [call(None), call(None)]
        mock_default_steam_info.assert_called_once_with()
        bridge_start.assert_awaited_once_with()
        mock_set_main_bridge.assert_called_once()
        mock_mount_workshop.assert_awaited_once_with()
        mock_start_workers.assert_called_once_with()
        fake_tracker.start_periodic_save.assert_called_once_with()
        fake_tracker.resume_persistence.assert_called_once_with("main_server")
        fake_tracker.record_app_start.assert_called_once_with(process="main_server")
        assert main_server._preload_task is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_does_not_reexport_runtime_into_cloudsave_snapshot():
    from app import main_server

    fake_tracker = SimpleNamespace(save=Mock())

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server.character_runtime, "role_state", _role_state_from_session_managers({})), \
         patch.object(main_server, "_run_cloudsave_manager_action", AsyncMock()) as run_cloudsave_action, \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker):
        await main_server.on_shutdown()

    fake_tracker.save.assert_called_once_with()
    run_cloudsave_action.assert_awaited_once_with(
        "upload_existing_snapshot",
        reason="main_server_shutdown_remote_upload",
        budget_seconds=5.0,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_limited_shutdown_skips_runtime_persistence_without_marker(
    monkeypatch,
):
    from app import main_server

    tracker = SimpleNamespace(save=Mock())
    cleanup = Mock()
    join_connectors = AsyncMock(return_value=[])
    stop_workers = AsyncMock()
    monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)
    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", True), \
         patch.object(main_server, "_runtime_startup_init_completed", True), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "_game_cleanup_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server.character_runtime, "role_state", _role_state_from_session_managers({})), \
         patch.object(main_server, "cleanup", cleanup), \
         patch.object(main_server, "join_sync_connector_threads", join_connectors), \
         patch.object(main_server, "_stop_neko_servers_integration_workers", stop_workers), \
         patch.object(main_server, "get_start_config", Mock(return_value={"shutdown_memory_server_on_exit": False})), \
         patch.object(main_server, "_run_cloudsave_manager_action", AsyncMock()) as run_cloudsave_action, \
         patch("app.main_server.voice_identity_runtime.close_voice_identity_runtime", AsyncMock()), \
         patch("utils.language_utils.aclose_translation_service", AsyncMock(return_value=None), create=True), \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.internal_http_client.aclose_internal_http_client", AsyncMock(return_value=None)), \
         patch("utils.external_http_client.aclose_external_http_client", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=tracker):
        await main_server.on_shutdown()

    tracker.save.assert_not_called()
    run_cloudsave_action.assert_not_awaited()
    cleanup.assert_called_once_with()
    join_connectors.assert_awaited_once_with(3.0)
    stop_workers.assert_awaited_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_startup_does_not_mark_normal_when_character_init_fails():
    from app import main_server

    fake_config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/neko"),
    )
    run_cloudsave_action = AsyncMock(return_value={"success": True, "action": "imported"})
    mock_set_root_mode = Mock()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(main_server, "_runtime_startup_init_completed", False))
        stack.enter_context(patch.object(main_server, "_config_manager", fake_config_manager))
        stack.enter_context(patch.object(main_server, "_run_cloudsave_manager_action", run_cloudsave_action))
        stack.enter_context(patch.object(main_server, "bootstrap_local_cloudsave_environment", Mock()))
        stack.enter_context(
            patch.object(main_server, "initialize_character_data", AsyncMock(side_effect=RuntimeError("character init failed")))
        )
        stack.enter_context(patch.object(main_server, "set_root_mode", mock_set_root_mode))

        with pytest.raises(RuntimeError, match="character init failed"):
            await main_server._ensure_main_server_runtime_initialized(reason="unit_test")

    mock_set_root_mode.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("root_states", "set_root_error"),
    [
        ([{"mode": "normal"}, {"mode": "normal"}], RuntimeError("root write failed")),
        ([{"mode": "normal"}, {"mode": "maintenance_readonly"}], None),
    ],
)
async def test_main_server_startup_aborts_when_root_state_cannot_publish_normal(
    root_states,
    set_root_error,
):
    from app import main_server

    fake_config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/neko"),
        load_root_state=Mock(side_effect=root_states),
    )
    fake_import_result = {"success": True, "action": "imported"}
    run_cloudsave_action = AsyncMock(return_value=fake_import_result)
    fake_tracker = SimpleNamespace(
        start_periodic_save=Mock(),
        record_app_start=Mock(),
    )
    bridge_start = AsyncMock(return_value=None)
    schedule_workshop_sync = Mock()

    async def _fake_background_preload():
        return None

    class _DummyBridge:
        def __init__(self, on_agent_event):
            self.on_agent_event = on_agent_event

        async def start(self):
            await bridge_start()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(main_server, "_IS_MAIN_PROCESS", True))
        stack.enter_context(patch.object(main_server, "_runtime_startup_init_completed", False))
        stack.enter_context(patch.object(main_server, "_preload_task", None))
        stack.enter_context(patch.object(main_server, "agent_event_bridge", None))
        stack.enter_context(patch.object(main_server, "_config_manager", fake_config_manager))
        stack.enter_context(
            patch.object(main_server, "get_storage_startup_blocking_reason", Mock(return_value=""))
        )
        stack.enter_context(patch.object(main_server, "_run_cloudsave_manager_action", run_cloudsave_action))
        stack.enter_context(patch.object(main_server, "bootstrap_local_cloudsave_environment", Mock()))
        stack.enter_context(
            patch.object(main_server, "set_root_mode", Mock(side_effect=set_root_error))
        )
        mock_init_chars = stack.enter_context(
            patch.object(main_server, "initialize_character_data", AsyncMock(return_value=None))
        )
        mock_sync_reload = stack.enter_context(
            patch.object(main_server, "_sync_memory_server_after_startup_import", AsyncMock(return_value=None))
        )
        mock_init_steam = stack.enter_context(
            patch.object(main_server, "initialize_steamworks", Mock(return_value=None))
        )
        stack.enter_context(
            patch.object(main_server, "get_default_steam_info", Mock())
        )
        stack.enter_context(patch.object(main_server, "_background_preload", _fake_background_preload))
        stack.enter_context(patch.object(main_server, "MainServerAgentBridge", _DummyBridge))
        stack.enter_context(
            patch.object(main_server, "set_main_bridge", Mock())
        )
        mock_mount_workshop = stack.enter_context(
            patch.object(main_server, "_init_and_mount_workshop", AsyncMock(return_value=None))
        )
        stack.enter_context(
            patch.object(main_server, "_schedule_workshop_sync", schedule_workshop_sync)
        )
        stack.enter_context(
            patch("main_routers.shared_state.set_steamworks", Mock())
        )
        cleanup_expired_sessions = stack.enter_context(
            patch("main_routers.game_router.cleanup_expired_sessions", AsyncMock())
        )
        stack.enter_context(patch("utils.token_tracker.install_hooks", Mock()))
        stack.enter_context(
            patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker)
        )
        stack.enter_context(
            patch("utils.language_utils.initialize_global_language", Mock(return_value="zh-CN"))
        )
        with pytest.raises(RuntimeError, match="failed to persist ROOT_MODE_NORMAL"):
            await main_server.on_startup()

    mock_init_chars.assert_awaited_once_with()
    mock_sync_reload.assert_awaited_once_with(fake_import_result)
    mock_init_steam.assert_called_once_with()
    mock_mount_workshop.assert_awaited_once_with()
    cleanup_expired_sessions.assert_not_called()
    schedule_workshop_sync.assert_not_called()
    fake_tracker.start_periodic_save.assert_not_called()
    fake_tracker.record_app_start.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_startup_stays_limited_when_storage_barrier_is_blocking():
    from app import main_server
    from main_routers import shared_state

    sentinel_templates = object()

    with patch.dict(shared_state._state, {"templates": shared_state._UNSET}), \
         patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "templates", sentinel_templates), \
         patch.object(main_server, "role_state", {}), \
         patch.object(main_server, "_config_manager", SimpleNamespace()), \
         patch.object(main_server, "get_storage_startup_blocking_reason", Mock(return_value="selection_required")), \
         patch.object(main_server, "_start_debug_health_watchdog", Mock()) as mock_start_watchdog, \
         patch.object(main_server, "_start_neko_servers_integration_workers", Mock()) as mock_start_workers, \
         patch.object(main_server, "_ensure_main_server_runtime_initialized", AsyncMock()) as mock_ensure_runtime:
        await main_server.on_startup()
        assert shared_state.get_templates() is sentinel_templates

    mock_start_watchdog.assert_called_once_with()
    mock_ensure_runtime.assert_not_awaited()
    mock_start_workers.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_starts_integration_workers_after_runtime_init():
    from app import main_server

    activation_order: list[str] = []

    def _observe_activation(name: str) -> None:
        assert main_server._main_runtime_limited_mode_enabled is True
        activation_order.append(name)

    with patch.object(
        main_server,
        "_request_memory_server_continue_startup",
        AsyncMock(return_value=None),
    ) as mock_memory_continue, patch.object(
        main_server,
        "_request_agent_server_continue_startup",
        AsyncMock(return_value=None),
    ) as mock_agent_continue, patch.object(
        main_server,
        "_ensure_main_server_runtime_initialized",
        AsyncMock(return_value=True),
    ) as mock_ensure, patch.object(
        main_server,
        "_request_memory_server_activate_startup",
        AsyncMock(side_effect=lambda _reason: _observe_activation("memory")),
    ) as mock_memory_activate, patch.object(
        main_server,
        "_request_agent_server_activate_startup",
        AsyncMock(side_effect=lambda _reason: _observe_activation("agent")),
        create=True,
    ) as mock_agent_activate, patch.object(
        main_server,
        "_activate_main_runtime_background_tasks",
        Mock(side_effect=lambda: _observe_activation("main")),
        create=True,
    ) as mock_main_activate, patch.object(
        main_server,
        "_start_neko_servers_integration_workers",
        Mock(),
    ) as mock_start_workers:
        result = await main_server.release_storage_startup_barrier(reason="unit_test")

    assert result == {"ok": True, "initialized": True}
    mock_memory_continue.assert_awaited_once_with("unit_test")
    mock_agent_continue.assert_awaited_once_with("unit_test")
    mock_ensure.assert_awaited_once_with(
        reason="unit_test",
        release_admission=False,
    )
    mock_agent_activate.assert_awaited_once_with("unit_test")
    mock_memory_activate.assert_awaited_once_with("unit_test")
    mock_main_activate.assert_called_once_with()
    assert activation_order == ["agent", "memory", "main"]
    mock_start_workers.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_clears_recovery_marker_before_all_runtime_init():
    from app import main_server

    observed_markers: list[str] = []

    async def _observe_marker(*_args, **_kwargs):
        observed_markers.append(os.environ.get("NEKO_STORAGE_RECOVERY_MODE", ""))
        return True

    with patch.dict(
        os.environ,
        {"NEKO_STORAGE_RECOVERY_MODE": "recovery_required"},
        clear=False,
    ), patch.object(
        main_server,
        "_request_memory_server_continue_startup",
        side_effect=_observe_marker,
    ), patch.object(
        main_server,
        "_request_agent_server_continue_startup",
        side_effect=_observe_marker,
    ), patch.object(
        main_server,
        "_ensure_main_server_runtime_initialized",
        side_effect=_observe_marker,
    ), patch.object(
        main_server,
        "_request_agent_server_activate_startup",
        side_effect=_observe_marker,
    ), patch.object(
        main_server,
        "_request_memory_server_activate_startup",
        side_effect=_observe_marker,
    ), patch.object(
        main_server,
        "_start_neko_servers_integration_workers",
        Mock(),
    ):
        result = await main_server.release_storage_startup_barrier(reason="unit_test")
        assert os.environ.get("NEKO_STORAGE_RECOVERY_MODE", "") == ""

    assert result == {"ok": True, "initialized": True}
    assert observed_markers == ["", "", "", "", ""]


@pytest.mark.unit
def test_facts_sync_worker_start_failure_is_logged_as_warning():
    from app import main_server

    facts_error = RuntimeError("facts worker failed")
    create_task = Mock(side_effect=facts_error)

    with (
        patch(
            "main_logic.facts_sync.start_facts_sync_worker",
            Mock(return_value=object()),
        ),
        patch(
            "main_logic.client_registration.ensure_client_registered",
            Mock(return_value=object()),
        ),
        patch.object(main_server.asyncio, "create_task", create_task),
        patch.object(main_server.logger, "warning") as warning,
        patch.object(main_server, "_facts_sync_worker_task", None),
        patch.object(main_server, "_client_registration_task", None),
    ):
        main_server._start_neko_servers_integration_workers()

    # Both workers share the same create_task mock, both log warnings
    assert warning.call_count == 2
    warning.assert_any_call(
        "[client_registration] bootstrap failed: %s",
        facts_error,
    )
    warning.assert_any_call(
        "[facts_sync] start worker failed: %s",
        facts_error,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_integration_workers_cancels_facts_sync_task():
    from app import main_server

    async def wait_forever():
        await asyncio.Event().wait()

    facts_task = asyncio.create_task(wait_forever())
    await asyncio.sleep(0)

    with patch.object(main_server, "_facts_sync_worker_task", facts_task):
        await main_server._stop_neko_servers_integration_workers()
        assert main_server._facts_sync_worker_task is None

    assert facts_task.cancelled()


@pytest.mark.unit
@pytest.mark.parametrize(
    "method",
    ("GET", "POST", "OPTIONS"),
)
def test_card_drop_active_character_is_allowed_during_limited_mode(method):
    from app import main_server

    assert main_server._is_main_limited_mode_allowed_path(
        "/api/card-drop/active-character",
        method,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "expected"),
    (("GET", True), ("HEAD", True), ("POST", False)),
)
def test_chat_full_shell_is_available_during_limited_mode(method, expected):
    from app import main_server

    assert (
        main_server._is_main_limited_mode_allowed_path("/chat_full", method)
        is expected
    )
    assert not main_server._is_main_limited_mode_allowed_path(
        "/api/config/page_config",
        method,
    )


@pytest.mark.unit
def test_main_server_limited_mode_middleware_blocks_runtime_routes():
    from app import main_server

    with patch.object(main_server, "_IS_MAIN_PROCESS", False), \
         patch.object(main_server, "_runtime_startup_init_completed", False), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", True), \
         patch.object(main_server, "_main_runtime_limited_mode_reason", "selection_required"):
        with TestClient(main_server.app) as client:
            blocked_response = client.get("/api/config/page_config")
            health_response = client.get("/health")
            steam_language_response = client.get("/api/config/steam_language")
            active_character_response = client.get(
                "/api/card-drop/active-character"
            )

    assert blocked_response.status_code == 409
    payload = blocked_response.json()
    assert payload["error_code"] == "storage_startup_blocked"
    assert payload["blocking_reason"] == "selection_required"
    assert payload["limited_mode"] is True
    assert health_response.status_code == 200
    assert steam_language_response.status_code == 200
    assert "uiLanguage" in steam_language_response.json()
    assert active_character_response.status_code == 200


@pytest.mark.unit
def test_main_server_limited_mode_rejects_new_websocket_before_business_route():
    from app import main_server
    from main_routers import websocket_router

    with patch.object(main_server, "_IS_MAIN_PROCESS", False), \
         patch.object(main_server, "_runtime_startup_init_completed", False), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", True), \
         patch.object(main_server, "_main_runtime_limited_mode_reason", "selection_required"), \
         patch.object(websocket_router, "get_config_manager", return_value=SimpleNamespace()), \
         patch.object(websocket_router, "get_session_manager", return_value={}):
        with TestClient(main_server.app) as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/not-a-runtime-character"):
                    pass

    assert exc_info.value.code == 1013


@pytest.mark.unit
def test_main_server_limited_mode_blocks_business_frames_on_connected_websocket():
    from app import main_server
    from main_routers import websocket_router

    class _Manager:
        pending_agent_callbacks = []
        websocket = None
        _voice_lease_connection_id = None

        async def cleanup(self, *, expected_websocket=None):
            if self.websocket is expected_websocket:
                self.websocket = None

    manager = _Manager()
    session_ids = {}
    with patch.object(main_server, "_IS_MAIN_PROCESS", False), \
         patch.object(main_server, "_runtime_startup_init_completed", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_main_runtime_limited_mode_reason", ""), \
         patch.object(websocket_router, "get_config_manager", return_value=SimpleNamespace()), \
         patch.object(websocket_router, "get_session_manager", return_value={"test-cat": manager}), \
         patch.object(websocket_router, "get_session_id", return_value=session_ids):
        with TestClient(main_server.app) as client:
            with client.websocket_connect("/ws/test-cat") as websocket:
                main_server._enable_main_storage_limited_mode("migration_pending")
                websocket.send_json({"action": "ping"})
                blocked = websocket.receive()

    assert blocked["type"] == "websocket.close"
    assert blocked["code"] == 1013


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_limited_mode_emits_only_one_websocket_close():
    from app import main_server

    sent = []

    async def downstream(_scope, _receive, send):
        await send({"type": "websocket.accept"})
        main_server._enable_main_storage_limited_mode("migration_pending")
        await send({"type": "websocket.send", "text": "must-not-pass"})
        await send({"type": "websocket.close", "code": 1000})

    async def receive():
        return {"type": "websocket.receive", "text": "unused"}

    async def send(message):
        sent.append(message)

    with patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_main_runtime_limited_mode_reason", ""):
        middleware = main_server.MainStorageLimitedModeWebSocketMiddleware(downstream)
        await middleware({"type": "websocket", "path": "/ws/test-cat"}, receive, send)

    assert sent == [
        {"type": "websocket.accept"},
        {"type": "websocket.close", "code": 1013},
    ]


@pytest.mark.unit
def test_main_server_reblocked_mode_wins_after_runtime_initialization():
    from app import main_server

    with patch.object(main_server, "_IS_MAIN_PROCESS", False), \
         patch.object(main_server, "_runtime_startup_init_completed", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", True), \
         patch.object(main_server, "_main_runtime_limited_mode_reason", "runtime_initialization_failed"):
        with TestClient(main_server.app) as client:
            response = client.get("/api/config/page_config")

    assert response.status_code == 409
    assert response.json()["blocking_reason"] == "runtime_initialization_failed"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("encoded_path", "escaped_control", "raw_control"),
    [
        ("/blocked/%1B%5B31m", r"\x1b", "\x1b"),
        ("/blocked/%00", r"\x00", "\x00"),
        ("/blocked/%C2%85", r"\x85", "\x85"),
        ("/blocked/%E2%80%A8", r"\u2028", "\u2028"),
    ],
)
def test_main_server_limited_mode_log_escapes_control_characters(
    encoded_path,
    escaped_control,
    raw_control,
):
    from app import main_server

    with (
        patch.object(main_server, "_IS_MAIN_PROCESS", False),
        patch.object(main_server, "_runtime_startup_init_completed", False),
        patch.object(main_server, "_main_runtime_limited_mode_enabled", True),
        patch.object(
            main_server,
            "_main_runtime_limited_mode_reason",
            "selection_required",
        ),
        patch.object(main_server.logger, "info") as mock_info,
    ):
        with TestClient(main_server.app) as client:
            response = client.get(encoded_path)

    log_call = next(
        call
        for call in mock_info.call_args_list
        if call.args
        and call.args[0].startswith("[Main] limited-mode blocks request path=")
    )
    rendered = log_call.args[0] % log_call.args[1:]

    assert response.status_code == 409
    assert raw_control not in rendered
    assert escaped_control in rendered


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_restores_memory_limited_mode_when_main_init_fails():
    from app import main_server

    init_error = RuntimeError("main init failed")
    with patch.object(main_server, "_request_memory_server_continue_startup", AsyncMock(return_value=None)) as mock_continue, \
         patch.object(main_server, "_request_agent_server_continue_startup", AsyncMock(return_value=None)) as mock_agent_continue, \
         patch.object(main_server, "_ensure_main_server_runtime_initialized", AsyncMock(side_effect=init_error)) as mock_ensure, \
         patch.object(main_server, "_request_memory_server_activate_startup", AsyncMock()) as mock_activate, \
         patch.object(main_server, "_request_runtime_services_block_startup", AsyncMock(return_value=None)) as mock_block, \
         patch.object(main_server, "_rollback_partial_main_runtime_startup", AsyncMock(return_value=None)), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_main_runtime_limited_mode_reason", ""):
        with pytest.raises(RuntimeError, match="main init failed"):
            await main_server.release_storage_startup_barrier(reason="unit_test")
        assert main_server._main_runtime_limited_mode_enabled is True
        assert main_server._main_runtime_limited_mode_reason == "startup_release_failed"

    mock_continue.assert_awaited_once_with("unit_test")
    mock_agent_continue.assert_awaited_once_with("unit_test")
    mock_ensure.assert_awaited_once_with(
        reason="unit_test",
        release_admission=False,
    )
    mock_activate.assert_not_awaited()
    mock_block.assert_awaited_once_with(
        "unit_test:main_server_init_failed",
        recovery_mode="",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_restores_recovery_marker_on_failure():
    from app import main_server

    init_error = RuntimeError("agent init failed")
    with patch.dict(
        os.environ,
        {"NEKO_STORAGE_RECOVERY_MODE": "recovery_required"},
        clear=False,
    ), patch.object(
        main_server,
        "_request_memory_server_continue_startup",
        AsyncMock(return_value=None),
    ), patch.object(
        main_server,
        "_request_agent_server_continue_startup",
        AsyncMock(side_effect=init_error),
    ), patch.object(
        main_server,
        "_ensure_main_server_runtime_initialized",
        AsyncMock(),
    ) as mock_main_init, patch.object(
        main_server,
        "_request_memory_server_activate_startup",
        AsyncMock(),
    ) as mock_activate, patch.object(
        main_server,
        "_request_runtime_services_block_startup",
        AsyncMock(return_value=None),
    ) as mock_block:
        with pytest.raises(RuntimeError, match="agent init failed"):
            await main_server.release_storage_startup_barrier(reason="unit_test")

        assert os.environ["NEKO_STORAGE_RECOVERY_MODE"] == "recovery_required"

    mock_main_init.assert_not_awaited()
    mock_activate.assert_not_awaited()
    mock_block.assert_awaited_once_with(
        "unit_test:main_server_init_failed",
        recovery_mode="recovery_required",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_reblocks_after_activation_failure():
    from app import main_server

    activation_error = RuntimeError("activation response lost")
    async def _mark_main_initialized(**_kwargs):
        main_server._runtime_startup_init_completed = True
        return True

    with patch.object(
        main_server,
        "_request_memory_server_continue_startup",
        AsyncMock(return_value=None),
    ), patch.object(
        main_server,
        "_request_agent_server_continue_startup",
        AsyncMock(return_value=None),
    ), patch.object(
        main_server,
        "_ensure_main_server_runtime_initialized",
        AsyncMock(side_effect=_mark_main_initialized),
    ), patch.object(
        main_server,
        "_request_agent_server_activate_startup",
        AsyncMock(return_value=None),
    ), patch.object(
        main_server,
        "_request_memory_server_activate_startup",
        AsyncMock(side_effect=activation_error),
    ), patch.object(
        main_server,
        "_activate_main_runtime_background_tasks",
        Mock(),
        create=True,
    ) as mock_main_activate, patch.object(
        main_server,
        "_request_runtime_services_block_startup",
        AsyncMock(return_value=None),
    ) as mock_block, patch.object(
        main_server,
        "_rollback_partial_main_runtime_startup",
        AsyncMock(return_value=None),
    ) as mock_rollback, patch.object(
        main_server,
        "_runtime_startup_init_completed",
        False,
    ), patch.object(
        main_server,
        "_main_runtime_limited_mode_enabled",
        True,
    ), patch.object(
        main_server,
        "_main_runtime_limited_mode_reason",
        "",
    ):
        with pytest.raises(RuntimeError, match="activation response lost"):
            await main_server.release_storage_startup_barrier(reason="unit_test")

        assert main_server._main_runtime_limited_mode_enabled is True
        assert main_server._runtime_startup_init_completed is False

    mock_block.assert_awaited_once_with(
        "unit_test:main_server_init_failed",
        recovery_mode="",
    )
    mock_rollback.assert_awaited_once_with()
    mock_main_activate.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_reblocks_memory_before_propagating_cancellation():
    """Cancellation must restore admission before reporting a failed release."""
    from app import main_server

    init_started = asyncio.Event()
    block_started = asyncio.Event()
    allow_block = asyncio.Event()

    async def _wait_for_cancel(*, reason: str, release_admission: bool):
        assert release_admission is False
        init_started.set()
        await asyncio.Event().wait()

    async def _block_services(reason: str, *, recovery_mode: str):
        assert recovery_mode == ""
        block_started.set()
        await allow_block.wait()

    with patch.object(
        main_server,
        "_request_memory_server_continue_startup",
        AsyncMock(return_value=None),
    ), patch.object(
        main_server,
        "_request_agent_server_continue_startup",
        AsyncMock(return_value=None),
    ), patch.object(
        main_server,
        "_ensure_main_server_runtime_initialized",
        side_effect=_wait_for_cancel,
    ), patch.object(
        main_server,
        "_request_runtime_services_block_startup",
        side_effect=_block_services,
    ) as mock_block, patch.object(
        main_server,
        "_rollback_partial_main_runtime_startup",
        AsyncMock(return_value=None),
    ) as mock_rollback, patch.object(
        main_server,
        "_main_runtime_limited_mode_enabled",
        False,
    ), patch.object(
        main_server,
        "_main_runtime_limited_mode_reason",
        "",
    ):
        task = asyncio.create_task(
            main_server.release_storage_startup_barrier(reason="unit_test")
        )
        await init_started.wait()
        task.cancel()
        await block_started.wait()

        # A second cancellation must not finish the failed release response
        # before both child-service guards have been restored.
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        allow_block.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    mock_block.assert_awaited_once_with(
        "unit_test:main_server_init_failed",
        recovery_mode="",
    )
    mock_rollback.assert_awaited_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_storage_startup_barrier_compensates_ambiguous_continue_cancellation():
    """A cancelled continue response may hide an already-applied server request."""
    from app import main_server

    continue_applied = asyncio.Event()
    block_started = asyncio.Event()

    async def _continue_without_response(reason: str):
        continue_applied.set()
        await asyncio.Event().wait()

    async def _block_services(reason: str, *, recovery_mode: str):
        assert recovery_mode == ""
        block_started.set()

    with patch.object(
        main_server,
        "_request_memory_server_continue_startup",
        side_effect=_continue_without_response,
    ), patch.object(
        main_server,
        "_ensure_main_server_runtime_initialized",
        AsyncMock(),
    ) as mock_ensure, patch.object(
        main_server,
        "_request_runtime_services_block_startup",
        side_effect=_block_services,
    ) as mock_block, patch.object(
        main_server,
        "_main_runtime_limited_mode_enabled",
        False,
    ), patch.object(
        main_server,
        "_main_runtime_limited_mode_reason",
        "",
    ):
        task = asyncio.create_task(
            main_server.release_storage_startup_barrier(reason="unit_test")
        )
        await continue_applied.wait()
        task.cancel()

        [outcome] = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome, asyncio.CancelledError)

        assert block_started.is_set()
        assert main_server._main_runtime_limited_mode_enabled is True
        assert main_server._main_runtime_limited_mode_reason == "startup_release_failed"

    mock_ensure.assert_not_awaited()
    mock_block.assert_awaited_once_with(
        "unit_test:main_server_init_failed",
        recovery_mode="",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_continue_startup_preserves_409_blocking_payload():
    import httpx
    from app import main_server

    payload = {
        "ok": False,
        "error_code": "storage_startup_blocked",
        "blocking_reason": "migration_pending",
    }

    class _Client:
        async def post(self, *args, **kwargs):
            return httpx.Response(
                409,
                json=payload,
                request=httpx.Request("POST", "http://127.0.0.1/internal/storage/startup/continue"),
            )

    with patch("utils.internal_http_client.get_internal_http_client", return_value=_Client()):
        with pytest.raises(main_server.MemoryServerStartupBlocked) as exc_info:
            await main_server._request_memory_server_continue_startup("unit_test")

    assert exc_info.value.payload == payload
    assert exc_info.value.blocking_reason == "migration_pending"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("_request_memory_server_continue_startup", ("unit_test",), {}),
        ("_request_memory_server_activate_startup", ("unit_test",), {}),
        ("_request_agent_server_continue_startup", ("unit_test",), {}),
        (
            "_request_memory_server_block_startup",
            ("unit_test",),
            {"recovery_mode": "recovery_required"},
        ),
        (
            "_request_agent_server_block_startup",
            ("unit_test",),
            {"recovery_mode": "recovery_required"},
        ),
    ],
)
async def test_main_storage_control_calls_include_internal_auth(
    method_name,
    args,
    kwargs,
):
    import httpx
    from app import main_server

    observed = []

    class _Client:
        async def post(self, url, **request_kwargs):
            observed.append((url, request_kwargs))
            return httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("POST", url),
            )

    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=_Client(),
    ):
        await getattr(main_server, method_name)(*args, **kwargs)

    assert len(observed) == 1
    assert observed[0][1]["headers"] == internal_http_auth_headers()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_activation_timeout_precedes_first_persistent_background_write():
    import httpx
    from app import main_server, memory_server

    observed_timeout = None

    class _Client:
        async def post(self, url, **request_kwargs):
            nonlocal observed_timeout
            observed_timeout = request_kwargs["timeout"]
            return httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("POST", url),
            )

    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=_Client(),
    ):
        await main_server._request_memory_server_activate_startup("unit_test")

    assert observed_timeout == 10.0
    assert memory_server.gates._INITIAL_DELAY_IDLE_MAINT >= 20


@pytest.mark.unit
def test_memory_storage_control_routes_require_internal_auth(monkeypatch):
    from app import memory_server

    runtime = memory_server.runtime
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(runtime, "_memory_storage_admission_generation", 70)

    client = TestClient(memory_server.app)
    for path in (
        "/internal/storage/startup/block",
        "/internal/storage/startup/continue",
        "/internal/storage/startup/activate",
    ):
        assert client.post(path, json={"reason": "attacker"}).status_code == 403
        assert client.post(
            path,
            json={"reason": "attacker"},
            headers={"X-CSRF-Token": "wrong-token"},
        ).status_code == 403
        assert client.post(
            path,
            json={"reason": "browser-csrf-token"},
            headers={INTERNAL_HTTP_AUTH_HEADER: AUTOSTART_CSRF_TOKEN},
        ).status_code == 403
        assert client.post(
            path,
            json={"reason": "attacker"},
            headers={**internal_http_auth_headers(), "Origin": "https://attacker.example"},
        ).status_code == 403

    assert runtime._memory_storage_blocked_after_init is False
    assert runtime._memory_storage_admission_generation == 70

    response = client.post(
        "/internal/storage/startup/block",
        json={"reason": "main_server"},
        headers=internal_http_auth_headers(),
    )
    assert response.status_code == 200
    assert runtime._memory_storage_blocked_after_init is True
    assert runtime._memory_storage_admission_generation == 71

    monkeypatch.setattr(runtime, "get_storage_recovery_mode", lambda: "")
    monkeypatch.setattr(runtime, "get_storage_startup_blocking_reason", lambda _cm: "")
    initialize = AsyncMock(return_value=False)
    monkeypatch.setattr(runtime, "ensure_memory_server_runtime_initialized", initialize)
    response = client.post(
        "/internal/storage/startup/continue",
        json={"reason": "main_server"},
        headers=internal_http_auth_headers(),
    )
    assert response.status_code == 200
    assert runtime._memory_storage_blocked_after_init is True
    assert runtime._memory_runtime_prepared_generation == 71
    initialize.assert_awaited_once_with(reason="main_server")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_startup_stays_limited_when_storage_barrier_is_blocking():
    from app import memory_server

    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="selection_required")), \
         patch.object(memory_server.runtime, "ensure_memory_server_runtime_initialized", AsyncMock()) as mock_ensure_runtime:
        await memory_server.startup_event_handler()

    mock_ensure_runtime.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_continue_startup_refuses_active_storage_barrier():
    from app import memory_server

    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="migration_pending")), \
         patch.object(memory_server.runtime, "ensure_memory_server_runtime_initialized", AsyncMock()) as mock_ensure_runtime:
        response = await memory_server.continue_storage_startup(None)

    assert response.status_code == 409
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["blocking_reason"] == "migration_pending"
    mock_ensure_runtime.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovery_mode",
    ["selection_required", "migration_pending", "recovery_required"],
)
async def test_memory_server_continue_startup_clears_recoverable_generation_marker(
    monkeypatch,
    recovery_mode,
):
    from app import memory_server

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", recovery_mode)
    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="")), \
         patch.object(memory_server.runtime, "ensure_memory_server_runtime_initialized", AsyncMock(return_value=True)) as mock_ensure_runtime, \
         patch.object(memory_server.runtime, "_memory_storage_blocked_after_init", True):
        response = await memory_server.continue_storage_startup(None)

    assert response == {"ok": True, "initialized": True}
    assert "NEKO_STORAGE_RECOVERY_MODE" not in os.environ
    mock_ensure_runtime.assert_awaited_once_with(
        reason="storage_selection_continue_current_session"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_late_continue_cannot_override_newer_block(monkeypatch):
    from app import memory_server

    init_started = asyncio.Event()
    allow_init = asyncio.Event()

    async def _initialize(*, reason: str):
        init_started.set()
        await allow_init.wait()
        return True

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "recovery_required")
    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="")), \
         patch.object(memory_server.runtime, "ensure_memory_server_runtime_initialized", side_effect=_initialize), \
         patch.object(memory_server.runtime, "_memory_storage_blocked_after_init", True), \
         patch.object(memory_server.runtime, "_memory_storage_admission_generation", 10):
        continue_task = asyncio.create_task(memory_server.continue_storage_startup(None))
        await init_started.wait()
        await memory_server.block_storage_startup(
            memory_server.runtime.ContinueStorageStartupRequest(
                reason="compensate",
                recovery_mode="recovery_required",
            )
        )
        allow_init.set()
        response = await continue_task

        assert response.status_code == 409
        assert memory_server.runtime._memory_storage_blocked_after_init is True
        assert memory_server.runtime._memory_storage_admission_generation == 11


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_late_startup_cannot_activate_a_newer_block_generation(
    monkeypatch,
):
    from app import memory_server

    init_started = asyncio.Event()
    allow_init = asyncio.Event()

    async def _initialize(*, reason: str):
        assert reason == "startup"
        init_started.set()
        await allow_init.wait()
        return True

    activate = Mock(return_value=True)
    monkeypatch.setattr(
        memory_server.runtime,
        "get_storage_startup_blocking_reason",
        lambda _cm: "",
    )
    monkeypatch.setattr(
        memory_server.runtime,
        "ensure_memory_server_runtime_initialized",
        _initialize,
    )
    monkeypatch.setattr(
        memory_server.runtime,
        "_activate_memory_runtime_background_tasks",
        activate,
    )
    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_init_task", None)
    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_prepared_generation", None)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_admission_generation", 50)

    startup_task = asyncio.create_task(memory_server.runtime.startup_event_handler())
    await init_started.wait()
    await memory_server.block_storage_startup(
        memory_server.runtime.ContinueStorageStartupRequest(reason="compensate")
    )
    allow_init.set()
    await startup_task

    activate.assert_not_called()
    assert memory_server.runtime._memory_runtime_prepared_generation is None
    assert memory_server.runtime._memory_storage_blocked_after_init is True
    assert memory_server.runtime._memory_storage_admission_generation == 51


@pytest.mark.unit
def test_memory_activation_fails_closed_when_normal_state_cannot_be_persisted(
    monkeypatch,
):
    from app import memory_server

    runtime = memory_server.runtime
    config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/neko"),
        load_root_state=Mock(return_value={"mode": "normal"}),
    )
    monkeypatch.setattr(runtime, "_config_manager", config_manager)
    monkeypatch.setattr(runtime, "get_storage_recovery_mode", lambda: "")
    monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    monkeypatch.setattr(runtime, "_memory_runtime_bootstrap_ok", True)
    monkeypatch.setattr(runtime, "_memory_runtime_prepared_generation", 60)
    monkeypatch.setattr(runtime, "_memory_storage_admission_generation", 60)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", True)
    monkeypatch.setattr(runtime, "_memory_background_tasks_started", False)
    monkeypatch.setattr(runtime, "_memory_activation_tasks", set())
    monkeypatch.setattr(
        runtime,
        "set_root_mode",
        Mock(side_effect=OSError("disk full")),
    )

    with pytest.raises(RuntimeError, match="failed to persist ROOT_MODE_NORMAL"):
        runtime._activate_memory_runtime_background_tasks(expected_generation=60)

    assert runtime._memory_storage_blocked_after_init is True
    assert runtime._memory_background_tasks_started is False
    assert runtime._memory_activation_tasks == set()


@pytest.mark.unit
def test_memory_activation_does_not_reopen_a_blocking_root_mode(monkeypatch):
    from app import memory_server

    runtime = memory_server.runtime
    config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/neko"),
        load_root_state=Mock(return_value={"mode": "maintenance_readonly"}),
    )
    monkeypatch.setattr(runtime, "_config_manager", config_manager)
    monkeypatch.setattr(runtime, "get_storage_recovery_mode", lambda: "")
    monkeypatch.setattr(runtime, "is_cloudsave_disabled", lambda: False)
    monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    monkeypatch.setattr(runtime, "_memory_runtime_bootstrap_ok", True)
    monkeypatch.setattr(runtime, "_memory_runtime_prepared_generation", 61)
    monkeypatch.setattr(runtime, "_memory_storage_admission_generation", 61)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", True)
    monkeypatch.setattr(runtime, "_memory_background_tasks_started", True)

    activated = runtime._activate_memory_runtime_background_tasks(
        expected_generation=61
    )

    assert activated is False
    assert runtime._memory_storage_blocked_after_init is True


@pytest.mark.unit
def test_memory_activation_skips_root_state_when_cloudsave_is_disabled(monkeypatch):
    from app import memory_server

    runtime = memory_server.runtime
    config_manager = SimpleNamespace(
        load_root_state=Mock(
            side_effect=AssertionError("disabled session must not read root_state")
        ),
    )
    monkeypatch.setattr(runtime, "_config_manager", config_manager)
    monkeypatch.setattr(runtime, "get_storage_recovery_mode", lambda: "")
    monkeypatch.setattr(runtime, "is_cloudsave_disabled", lambda: True)
    monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    monkeypatch.setattr(runtime, "_memory_runtime_prepared_generation", 611)
    monkeypatch.setattr(runtime, "_memory_storage_admission_generation", 611)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", True)
    monkeypatch.setattr(runtime, "_memory_background_tasks_started", True)

    assert runtime._activate_memory_runtime_background_tasks(
        expected_generation=611
    ) is True
    assert runtime._memory_storage_blocked_after_init is False
    config_manager.load_root_state.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_root_writer_cannot_enter_between_check_and_activation_publish(
    monkeypatch,
):
    from app import memory_server
    from utils.root_state_lock import root_state_transaction

    runtime = memory_server.runtime
    config_manager = SimpleNamespace(
        app_docs_dir=Path("/tmp/neko"),
        load_root_state=Mock(return_value={"mode": "normal"}),
    )
    writer_acquired = threading.Event()
    writer_thread = None
    acquired_during_publish: list[bool] = []
    real_spawn = runtime._spawn_background_task

    def _observe_first_publish(coro):
        nonlocal writer_thread
        if writer_thread is None:
            def _writer():
                with root_state_transaction():
                    writer_acquired.set()

            writer_thread = threading.Thread(target=_writer, daemon=True)
            writer_thread.start()
            acquired_during_publish.append(writer_acquired.wait(timeout=0.2))
        return real_spawn(coro)

    monkeypatch.setattr(runtime, "_config_manager", config_manager)
    monkeypatch.setattr(runtime, "get_storage_recovery_mode", lambda: "")
    monkeypatch.setattr(runtime, "is_cloudsave_disabled", lambda: False)
    monkeypatch.setattr(runtime, "set_root_mode", Mock())
    monkeypatch.setattr(runtime, "_spawn_background_task", _observe_first_publish)
    monkeypatch.setattr(runtime, "_memory_runtime_init_completed", True)
    monkeypatch.setattr(runtime, "_memory_runtime_bootstrap_ok", True)
    monkeypatch.setattr(runtime, "_memory_runtime_prepared_generation", 62)
    monkeypatch.setattr(runtime, "_memory_storage_admission_generation", 62)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", True)
    monkeypatch.setattr(runtime, "_memory_background_tasks_started", False)
    monkeypatch.setattr(runtime, "_memory_activation_tasks", set())
    monkeypatch.setattr(runtime, "_memory_token_tracker_task", None)
    monkeypatch.setattr(runtime, "embedding_warmup_worker", None)

    assert runtime._activate_memory_runtime_background_tasks(
        expected_generation=62
    ) is True
    assert writer_thread is not None
    writer_thread.join(timeout=2)
    assert writer_acquired.is_set()
    assert acquired_during_publish == [False]

    # Let the admitted wrappers take ownership of their inner coroutine before
    # cancellation so teardown does not leave never-awaited coroutine objects.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await runtime._quiesce_memory_activation_tasks()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_initializer_survives_cancelled_continue_waiter(monkeypatch):
    from app import memory_server

    init_started = asyncio.Event()
    allow_init = asyncio.Event()
    init_finished = asyncio.Event()

    async def _initialize(*, reason: str):
        assert reason == "cancelled-waiter"
        init_started.set()
        try:
            await allow_init.wait()
            return True
        finally:
            init_finished.set()

    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_init_completed", False)
    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_init_task", None)
    monkeypatch.setattr(
        memory_server.runtime,
        "_initialize_memory_server_runtime",
        _initialize,
    )

    waiter = asyncio.create_task(
        memory_server.runtime.ensure_memory_server_runtime_initialized(
            reason="cancelled-waiter"
        )
    )
    await init_started.wait()
    initializer_task = memory_server.runtime._memory_runtime_init_task
    assert initializer_task is not None

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert not initializer_task.cancelled()
    assert not initializer_task.done()
    allow_init.set()
    assert await initializer_task is True
    assert init_finished.is_set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_block_waits_for_inflight_to_thread_without_cancelling(
    monkeypatch,
):
    from app import memory_server

    worker_started = threading.Event()
    allow_worker = threading.Event()
    worker_finished = threading.Event()
    init_lock = asyncio.Lock()

    def _write_in_thread():
        worker_started.set()
        allow_worker.wait(timeout=5)
        worker_finished.set()

    async def _initialize():
        async with init_lock:
            await asyncio.to_thread(_write_in_thread)

    initializer_task = asyncio.create_task(_initialize())
    assert await asyncio.to_thread(worker_started.wait, 2)

    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_init_lock", init_lock)
    monkeypatch.setattr(
        memory_server.runtime,
        "_memory_runtime_init_task",
        initializer_task,
    )
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_admission_generation", 20)

    block_task = asyncio.create_task(
        memory_server.block_storage_startup(
            memory_server.runtime.ContinueStorageStartupRequest(reason="compensate")
        )
    )
    await asyncio.sleep(0.05)

    assert not block_task.done()
    assert not initializer_task.cancelled()
    assert not worker_finished.is_set()

    allow_worker.set()
    response = await asyncio.wait_for(block_task, timeout=2)

    assert response["ok"] is True
    assert worker_finished.is_set()
    assert initializer_task.done()
    assert not initializer_task.cancelled()
    assert memory_server.runtime._memory_storage_blocked_after_init is True
    assert memory_server.runtime._memory_storage_admission_generation == 21


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_block_cancelled_during_quiesce_still_waits_for_initializer(
    monkeypatch,
):
    from app import memory_server

    quiesce_started = asyncio.Event()
    allow_quiesce = asyncio.Event()
    initializer_started = asyncio.Event()
    allow_initializer = asyncio.Event()
    init_lock = asyncio.Lock()

    async def _quiesce():
        quiesce_started.set()
        await allow_quiesce.wait()

    async def _initialize():
        async with init_lock:
            initializer_started.set()
            await allow_initializer.wait()

    initializer_task = asyncio.create_task(_initialize())
    await initializer_started.wait()
    monkeypatch.setattr(
        memory_server.runtime,
        "_quiesce_memory_activation_tasks",
        _quiesce,
    )
    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_init_lock", init_lock)
    monkeypatch.setattr(
        memory_server.runtime,
        "_memory_runtime_init_task",
        initializer_task,
    )
    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_prepared_generation", 70)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_admission_generation", 70)

    block_task = asyncio.create_task(
        memory_server.block_storage_startup(
            memory_server.runtime.ContinueStorageStartupRequest(reason="cancelled")
        )
    )
    await quiesce_started.wait()
    block_task.cancel()
    allow_quiesce.set()
    await asyncio.sleep(0)

    assert not block_task.done()
    assert not initializer_task.done()

    allow_initializer.set()
    with pytest.raises(asyncio.CancelledError):
        await block_task
    assert initializer_task.done()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_block_quiesces_activated_background_tasks(monkeypatch):
    from app import memory_server

    worker_started = asyncio.Event()
    worker_stopped = asyncio.Event()

    async def _long_lived_worker():
        worker_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            worker_stopped.set()

    worker = asyncio.create_task(_long_lived_worker())
    await worker_started.wait()
    monkeypatch.setattr(memory_server.runtime, "_memory_activation_tasks", {worker})
    monkeypatch.setattr(memory_server.runtime, "_memory_background_tasks_started", True)
    monkeypatch.setattr(memory_server.runtime, "_memory_runtime_init_task", None)
    monkeypatch.setattr(memory_server.runtime, "embedding_warmup_worker", None)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(memory_server.runtime, "_memory_storage_admission_generation", 30)

    response = await memory_server.block_storage_startup(
        memory_server.runtime.ContinueStorageStartupRequest(reason="compensate")
    )

    assert response["ok"] is True
    assert worker.cancelled()
    assert worker_stopped.is_set()
    assert memory_server.runtime._memory_background_tasks_started is False
    assert memory_server.runtime._memory_storage_blocked_after_init is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_tracker_activation_resumes_before_start_and_record(monkeypatch):
    from app import memory_server

    events = []
    tracker = SimpleNamespace(_save_task=None)

    def _resume(owner):
        events.append(("resume", owner))

    def _start():
        events.append(("start", None))

    def _record(**_kwargs):
        events.append(("record", None))

    tracker.resume_persistence = _resume
    tracker.start_periodic_save = _start
    tracker.record_app_start = _record
    monkeypatch.setattr("utils.token_tracker.install_hooks", Mock())
    monkeypatch.setattr(
        "utils.token_tracker.TokenTracker.get_instance",
        lambda: tracker,
    )
    monkeypatch.setattr(memory_server.runtime, "_memory_token_tracker_task", None)

    await memory_server.runtime._bootstrap_memory_token_tracker()

    assert events == [
        ("resume", "memory_server"),
        ("start", None),
        ("record", None),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_block_suspends_tracker_without_cancelling_another_service_task(
    monkeypatch,
):
    from app import memory_server

    async def _foreign_periodic_save():
        await asyncio.Event().wait()

    foreign_task = asyncio.create_task(_foreign_periodic_save())
    tracker = SimpleNamespace(
        _save_task=foreign_task,
        suspend_persistence=Mock(),
    )
    runtime = memory_server.runtime
    monkeypatch.setattr(runtime, "_memory_activation_tasks", set())
    monkeypatch.setattr(runtime, "_memory_token_tracker_task", None)
    monkeypatch.setattr(runtime, "embedding_warmup_worker", None)
    monkeypatch.setattr(runtime, "_memory_background_tasks_started", True)
    monkeypatch.setattr(
        "utils.token_tracker.TokenTracker.get_existing_instance",
        lambda: tracker,
        raising=False,
    )
    monkeypatch.setattr(
        "utils.token_tracker.TokenTracker.get_instance",
        Mock(side_effect=AssertionError("block must not construct TokenTracker")),
    )

    await runtime._quiesce_memory_activation_tasks()

    tracker.suspend_persistence.assert_called_once_with("memory_server")
    assert not foreign_task.cancelled()
    assert not foreign_task.done()
    foreign_task.cancel()
    await asyncio.gather(foreign_task, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_rollback_suspends_tracker_without_cancelling_another_service_task(
    monkeypatch,
):
    from app import main_server

    async def _foreign_periodic_save():
        await asyncio.Event().wait()

    foreign_task = asyncio.create_task(_foreign_periodic_save())
    tracker = SimpleNamespace(
        _save_task=foreign_task,
        suspend_persistence=Mock(),
    )
    monkeypatch.setattr(main_server, "_preload_task", None)
    monkeypatch.setattr(main_server, "_game_cleanup_task", None)
    monkeypatch.setattr(main_server, "_main_token_tracker_task", None)
    monkeypatch.setattr(main_server, "agent_event_bridge", None)
    monkeypatch.setattr(main_server, "steamworks", None)
    monkeypatch.setattr(
        main_server,
        "_cancel_workshop_background_tasks_for_startup_rollback",
        AsyncMock(),
    )
    monkeypatch.setattr(main_server, "cleanup", Mock())
    monkeypatch.setattr(main_server, "join_sync_connector_threads", AsyncMock())
    monkeypatch.setattr(main_server, "_reset_sync_connector_shutdown_events", Mock())
    monkeypatch.setattr(
        "main_routers.shared_state.set_steamworks",
        Mock(),
    )
    monkeypatch.setattr(
        "utils.token_tracker.TokenTracker.get_existing_instance",
        lambda: tracker,
        raising=False,
    )
    monkeypatch.setattr(
        "utils.token_tracker.TokenTracker.get_instance",
        Mock(side_effect=AssertionError("rollback must not construct TokenTracker")),
    )

    await main_server._rollback_partial_main_runtime_startup()

    tracker.suspend_persistence.assert_called_once_with("main_server")
    assert not foreign_task.cancelled()
    assert not foreign_task.done()
    foreign_task.cancel()
    await asyncio.gather(foreign_task, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_core_joins_spawned_startup_replay_tasks(monkeypatch):
    from app import memory_server

    replay_started = asyncio.Event()
    allow_replay = asyncio.Event()
    replay_finished = asyncio.Event()

    async def _replay_write():
        replay_started.set()
        await allow_replay.wait()
        replay_finished.set()

    replay_task = asyncio.create_task(_replay_write())
    monkeypatch.setattr(
        memory_server.outbox_infra,
        "_replay_pending_outbox",
        AsyncMock(return_value=[replay_task]),
    )

    join_task = asyncio.create_task(
        memory_server.runtime._replay_startup_outbox_to_completion()
    )
    await replay_started.wait()
    await asyncio.sleep(0)
    assert not join_task.done()

    allow_replay.set()
    await join_task
    assert replay_finished.is_set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_failed_continue_remains_blocked_without_recovery_marker(
    monkeypatch,
):
    from app import memory_server

    monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)
    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="")), \
         patch.object(memory_server.runtime, "ensure_memory_server_runtime_initialized", AsyncMock(side_effect=RuntimeError("init failed"))), \
         patch.object(memory_server.runtime, "_memory_storage_blocked_after_init", False):
        response = await memory_server.continue_storage_startup(None)
        assert response.status_code == 500
        assert memory_server.runtime._memory_storage_blocked_after_init is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_blocked_shutdown_skips_runtime_persistence(monkeypatch):
    from app import memory_server

    tracker = SimpleNamespace(save=Mock())
    monkeypatch.delenv("NEKO_STORAGE_RECOVERY_MODE", raising=False)
    manager = SimpleNamespace(cleanup=Mock())
    release_embedding = AsyncMock()
    with patch.object(memory_server.runtime, "_memory_runtime_init_completed", True), \
         patch.object(memory_server.runtime, "_memory_storage_blocked_after_init", True), \
         patch.object(memory_server.runtime, "embedding_warmup_worker", None), \
         patch.object(memory_server.runtime, "_deferred_time_managers", [manager]), \
         patch.object(memory_server.runtime, "time_manager", None), \
         patch.object(memory_server.runtime, "_reload_lock", asyncio.Lock()), \
         patch("memory.embeddings.release_embedding_service", release_embedding), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=tracker):
        await memory_server.shutdown_event_handler()

    tracker.save.assert_not_called()
    manager.cleanup.assert_called_once_with()
    release_embedding.assert_awaited_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_server_shared_recovery_marker_closes_initialized_fast_path(
    monkeypatch,
):
    from app import memory_server

    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "recovery_required")
    call_next = AsyncMock(side_effect=AssertionError("blocked request reached memory route"))
    with patch.object(memory_server.runtime, "_memory_runtime_init_completed", True), \
         patch.object(memory_server.runtime, "_memory_storage_blocked_after_init", False):
        response = await memory_server.storage_limited_mode_guard(
            SimpleNamespace(url=SimpleNamespace(path="/get_settings/test")),
            call_next,
        )

    assert response.status_code == 409
    call_next.assert_not_awaited()


@pytest.mark.unit
def test_memory_server_limited_mode_middleware_blocks_runtime_routes():
    from app import memory_server

    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="selection_required")):
        with TestClient(memory_server.app) as client:
            response = client.get("/get_settings/小满")

    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "storage_startup_blocked"
    assert payload["blocking_reason"] == "selection_required"
    assert payload["limited_mode"] is True


@pytest.mark.unit
def test_memory_server_limited_mode_middleware_blocks_until_runtime_init_completes():
    from app import memory_server

    with patch.object(memory_server.runtime, "_config_manager", SimpleNamespace()), \
         patch.object(memory_server.runtime, "_memory_runtime_init_completed", False), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(side_effect=["selection_required", ""])):
        with TestClient(memory_server.app) as client:
            response = client.get("/get_settings/小满")

    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "storage_startup_blocked"
    assert payload["blocking_reason"] == "runtime_initializing"
    assert payload["limited_mode"] is True


@pytest.mark.unit
def test_memory_server_block_startup_endpoint_restores_limited_mode():
    from app import memory_server

    with patch.object(memory_server.runtime, "_memory_runtime_init_completed", True), \
         patch.object(memory_server.runtime, "_memory_storage_blocked_after_init", False), \
         patch.object(memory_server.runtime, "get_storage_startup_blocking_reason", Mock(return_value="")):
        with TestClient(memory_server.app) as client:
            response = client.post(
                "/internal/storage/startup/block",
                json={"reason": "main_failed"},
                headers=internal_http_auth_headers(),
            )
            blocked_response = client.get("/get_settings/小满")
            runtime_completed_during_block = memory_server.runtime._memory_runtime_init_completed

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["limited_mode"] is True
    assert payload["reason"] == "main_failed"
    assert blocked_response.status_code == 409
    assert blocked_response.json()["blocking_reason"] == "storage_startup_blocked_after_init"
    assert runtime_completed_during_block is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_cancel_workshop_background_tasks_uses_public_api():
    from app import main_server

    calls = []
    workshop_module = SimpleNamespace(
        cancel_background_tasks=AsyncMock(side_effect=lambda *, timeout: calls.append(timeout)),
        _ugc_warmup_task=None,
        _ugc_sync_task=None,
    )

    with patch.object(main_server.importlib, "import_module", return_value=workshop_module):
        await main_server._cancel_workshop_background_tasks(timeout=2.5)

    assert calls == [2.5]
    workshop_module.cancel_background_tasks.assert_awaited_once_with(timeout=2.5)


@pytest.mark.unit
def test_main_server_resets_sync_shutdown_events_after_startup_rollback():
    """``_reset_sync_connector_shutdown_events`` 现在是 no-op：cross_server 改成
    主 loop 上的 asyncio.Task 后，没有 ThreadEvent 可以 reset。函数保留是为了
    避免改动文件内众多调用点，本测试只验证它仍可调用且不会抛异常。
    """
    from app import main_server
    from app.main_server import _SyncMessageQueue

    role_state = {
        "小满": main_server.RoleState(
            sync_message_queue=_SyncMessageQueue(),
            websocket_lock=asyncio.Lock(),
        )
    }

    with patch.object(main_server.character_runtime, "role_state", role_state):
        # 不抛异常即视为通过；旧版会清 threading.Event，新版无状态可清。
        # 显式断言返回值为 None，未来若改成有副作用返回时能更早暴露。
        assert main_server._reset_sync_connector_shutdown_events() is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_releases_live_sessions_then_uploads_existing_snapshot():
    from app import main_server

    fake_tracker = SimpleNamespace(save=Mock())
    run_cloudsave_action = AsyncMock(return_value={"success": True, "action": "uploaded"})
    manager_with_resampler = SimpleNamespace(audio_resampler=object())
    existing_steamworks = SimpleNamespace()

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "steamworks", existing_steamworks), \
         patch.object(main_server.character_runtime, "role_state", _role_state_from_session_managers({"角色A": manager_with_resampler, "角色B": object(), "空槽": None})), \
         patch.object(main_server, "_run_cloudsave_manager_action", run_cloudsave_action), \
         patch("main_routers.characters_router.release_memory_server_character", AsyncMock(return_value=True)) as mock_release, \
         patch("utils.language_utils.aclose_translation_service", AsyncMock(return_value=None), create=True), \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker):
        await main_server.on_shutdown()

    assert manager_with_resampler.audio_resampler is None
    run_cloudsave_action.assert_awaited_once_with(
        "upload_existing_snapshot",
        reason="main_server_shutdown_remote_upload",
        budget_seconds=5.0,
        steamworks=existing_steamworks,
    )
    assert mock_release.await_count == 2
    mock_release.assert_any_await("角色A", reason="Steam Auto-Cloud pre-shutdown release: 角色A")
    mock_release.assert_any_await("角色B", reason="Steam Auto-Cloud pre-shutdown release: 角色B")
    fake_tracker.save.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_continues_when_memory_release_returns_false():
    from app import main_server

    fake_tracker = SimpleNamespace(save=Mock())
    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "steamworks", None), \
         patch.object(main_server.character_runtime, "role_state", _role_state_from_session_managers({"角色A": object(), "角色B": object()})), \
         patch.object(main_server, "_run_cloudsave_manager_action", AsyncMock()) as run_cloudsave_action, \
         patch("main_routers.characters_router.release_memory_server_character", AsyncMock(side_effect=[True, False])) as mock_release, \
         patch("utils.language_utils.aclose_translation_service", AsyncMock(return_value=None), create=True), \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker), \
         patch.object(main_server.logger, "warning", Mock()) as mock_warning:
        await main_server.on_shutdown()

    assert mock_release.await_count == 2
    run_cloudsave_action.assert_not_awaited()
    mock_warning.assert_any_call(
        "Steam Auto-Cloud pre-shutdown release failed for %s: returned False; uploaded snapshot may be stale/incomplete",
        "角色B",
    )
    mock_warning.assert_any_call(
        "Steam Auto-Cloud shutdown staged snapshot upload skipped because pre-shutdown release failed for: %s",
        "角色B",
    )
    fake_tracker.save.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_server_async_defers_memory_server_stop_until_main_shutdown():
    from app import main_server

    server = SimpleNamespace(should_exit=False)
    start_config = {
        "browser_mode_enabled": True,
        "browser_page": "",
        "shutdown_memory_server_on_exit": False,
        "server": server,
    }
    workshop_state = SimpleNamespace(_ugc_warmup_task=None, _ugc_sync_task=None)

    with patch.object(main_server.asyncio, "sleep", AsyncMock(return_value=None)), \
         patch.object(main_server, "get_start_config", Mock(return_value=start_config)), \
         patch.object(main_server.importlib, "import_module", return_value=workshop_state):
        await main_server.shutdown_server_async()

    assert start_config["shutdown_memory_server_on_exit"] is True
    assert server.should_exit is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_requests_memory_server_stop_after_snapshot_upload_when_deferred():
    from app import main_server

    fake_tracker = SimpleNamespace(save=Mock())
    call_order = []
    start_config = {
        "browser_mode_enabled": True,
        "browser_page": "",
        "shutdown_memory_server_on_exit": True,
        "server": None,
    }

    async def _fake_request_shutdown():
        call_order.append("memory_shutdown")

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_main_runtime_limited_mode_enabled", False), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "steamworks", None), \
         patch.object(main_server.character_runtime, "role_state", _role_state_from_session_managers({})), \
         patch.object(main_server, "_run_cloudsave_manager_action", AsyncMock()) as run_cloudsave_action, \
         patch.object(main_server, "get_start_config", Mock(return_value=start_config)), \
         patch.object(main_server, "_request_memory_server_shutdown", AsyncMock(side_effect=_fake_request_shutdown)) as mock_request_shutdown, \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker):
        await main_server.on_shutdown()

    assert call_order == ["memory_shutdown"]
    run_cloudsave_action.assert_awaited_once_with(
        "upload_existing_snapshot",
        reason="main_server_shutdown_remote_upload",
        budget_seconds=5.0,
    )
    assert start_config["shutdown_memory_server_on_exit"] is False
    mock_request_shutdown.assert_awaited_once_with()
