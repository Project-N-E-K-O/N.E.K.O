import contextlib
import json
import shutil
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
from utils.file_utils import atomic_write_json


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
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
    import launcher

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
    import main_server

    fake_config_manager = SimpleNamespace(app_docs_dir=Path("/tmp/N.E.K.O"))
    fake_import_result = {"success": True, "action": "imported"}
    mock_bootstrap = Mock()
    run_cloudsave_action = AsyncMock(return_value=fake_import_result)
    fake_tracker = SimpleNamespace(
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

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_config_manager", fake_config_manager), \
         patch.object(main_server, "_run_cloudsave_manager_action", run_cloudsave_action), \
         patch.object(main_server, "bootstrap_local_cloudsave_environment", mock_bootstrap), \
         patch.object(main_server, "initialize_character_data", AsyncMock(return_value=None)) as mock_init_chars, \
         patch.object(main_server, "_sync_memory_server_after_startup_import", AsyncMock(return_value=None)) as mock_sync_reload, \
         patch.object(main_server, "set_root_mode", Mock(return_value={"mode": "normal"})) as mock_set_root_mode, \
         patch.object(main_server, "initialize_steamworks", Mock(return_value=None)) as mock_init_steam, \
         patch.object(main_server, "get_default_steam_info", Mock()) as mock_default_steam_info, \
         patch.object(main_server, "_background_preload", _fake_background_preload), \
         patch.object(main_server, "MainServerAgentBridge", _DummyBridge), \
         patch.object(main_server, "set_main_bridge", Mock()) as mock_set_main_bridge, \
         patch.object(main_server, "_init_and_mount_workshop", AsyncMock(return_value=None)) as mock_mount_workshop, \
         patch("main_routers.shared_state.set_steamworks", Mock()) as mock_set_steamworks, \
         patch("utils.token_tracker.install_hooks", Mock()), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker), \
        patch("utils.language_utils.initialize_global_language", Mock(return_value="zh-CN")):
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
        mock_set_steamworks.assert_called_once_with(None)
        mock_default_steam_info.assert_called_once_with()
        bridge_start.assert_awaited_once_with()
        mock_set_main_bridge.assert_called_once()
        mock_mount_workshop.assert_awaited_once_with()
        fake_tracker.start_periodic_save.assert_called_once_with()
        fake_tracker.record_app_start.assert_called_once_with()
        assert main_server._preload_task is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_does_not_reexport_runtime_into_cloudsave_snapshot():
    import main_server

    fake_tracker = SimpleNamespace(save=Mock())

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "session_manager", {}), \
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
async def test_main_server_startup_aborts_when_root_mode_persist_fails():
    import main_server

    fake_config_manager = SimpleNamespace(app_docs_dir=Path("/tmp/neko"))
    fake_import_result = {"success": True, "action": "imported"}
    run_cloudsave_action = AsyncMock(return_value=fake_import_result)

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_config_manager", fake_config_manager), \
         patch.object(main_server, "_run_cloudsave_manager_action", run_cloudsave_action), \
         patch.object(main_server, "bootstrap_local_cloudsave_environment", Mock()), \
         patch.object(main_server, "set_root_mode", Mock(side_effect=RuntimeError("root write failed"))), \
         patch.object(main_server, "initialize_character_data", AsyncMock(return_value=None)) as mock_init_chars, \
         patch.object(main_server, "_sync_memory_server_after_startup_import", AsyncMock(return_value=None)) as mock_sync_reload:
        with pytest.raises(RuntimeError, match="failed to persist ROOT_MODE_NORMAL"):
            await main_server.on_startup()

    mock_init_chars.assert_not_awaited()
    mock_sync_reload.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_releases_live_sessions_then_uploads_existing_snapshot():
    import main_server

    fake_tracker = SimpleNamespace(save=Mock())
    run_cloudsave_action = AsyncMock(return_value={"success": True, "action": "uploaded"})
    manager_with_resampler = SimpleNamespace(audio_resampler=object())

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "session_manager", {"角色A": manager_with_resampler, "角色B": object(), "空槽": None}), \
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
    )
    assert mock_release.await_count == 2
    mock_release.assert_any_await("角色A", reason="Steam Auto-Cloud pre-shutdown release: 角色A")
    mock_release.assert_any_await("角色B", reason="Steam Auto-Cloud pre-shutdown release: 角色B")
    fake_tracker.save.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_continues_when_memory_release_returns_false():
    import main_server

    fake_tracker = SimpleNamespace(save=Mock())
    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "session_manager", {"角色A": object(), "角色B": object()}), \
         patch.object(main_server, "_run_cloudsave_manager_action", AsyncMock()) as run_cloudsave_action, \
         patch("main_routers.characters_router.release_memory_server_character", AsyncMock(side_effect=[True, False])) as mock_release, \
         patch("utils.language_utils.aclose_translation_service", AsyncMock(return_value=None), create=True), \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker), \
         patch.object(main_server.logger, "warning", Mock()) as mock_warning:
        await main_server.on_shutdown()

    assert mock_release.await_count == 2
    run_cloudsave_action.assert_awaited_once_with(
        "upload_existing_snapshot",
        reason="main_server_shutdown_remote_upload",
        budget_seconds=5.0,
    )
    mock_warning.assert_any_call(
        "Steam Auto-Cloud pre-shutdown release failed for %s: returned False",
        "角色B",
    )
    fake_tracker.save.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_server_async_defers_memory_server_stop_until_main_shutdown():
    import main_server

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
    import main_server

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
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "session_manager", {}), \
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
