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
