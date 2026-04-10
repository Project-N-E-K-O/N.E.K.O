import shutil
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from utils.cloudsave_autocloud import CloudSaveManager
from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment
from utils.config_manager import ConfigManager
from utils.file_utils import atomic_write_json


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
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
    with patch.object(launcher, "get_config_manager", lambda _app_name: cm), patch.object(
        launcher,
        "emit_frontend_event",
        lambda event_type, payload=None: emitted_events.append((event_type, payload)),
    ):
        result = launcher._prepare_cloudsave_runtime_for_launch()
    return result, emitted_events


@pytest.mark.unit
def test_launcher_phase0_skips_import_when_cloud_snapshot_is_empty():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        result, emitted_events = _run_launcher_phase0(cm)

        assert result["import_result"]["action"] == "skipped"
        assert result["import_result"]["reason"] == "no_snapshot"
        assert emitted_events[-1][0] == "cloudsave_bootstrap_ready"
        assert emitted_events[-1][1]["import_result"]["reason"] == "no_snapshot"


@pytest.mark.unit
def test_cloudsave_lifecycle_round_trip_across_two_devices():
    with TemporaryDirectory() as td:
        device_a = _make_config_manager(Path(td) / "device_a")
        device_b = _make_config_manager(Path(td) / "device_b")
        bootstrap_local_cloudsave_environment(device_a)
        bootstrap_local_cloudsave_environment(device_b)

        _write_runtime_state(device_a, character_name="设备A角色", recent_message="来自设备A")
        export_a = CloudSaveManager(device_a).export_snapshot(reason="device_a_shutdown")
        assert export_a["action"] == "exported"

        shutil.copytree(device_a.cloudsave_dir, device_b.cloudsave_dir, dirs_exist_ok=True)
        startup_b, _ = _run_launcher_phase0(device_b)
        assert startup_b["import_result"]["action"] == "imported"
        assert device_b.load_characters()["当前猫娘"] == "设备A角色"

        _write_runtime_state(device_b, character_name="设备B角色", recent_message="来自设备B")
        export_b = CloudSaveManager(device_b).export_snapshot(reason="device_b_shutdown")
        assert export_b["action"] == "exported"

        shutil.copytree(device_b.cloudsave_dir, device_a.cloudsave_dir, dirs_exist_ok=True)
        startup_a_again, _ = _run_launcher_phase0(device_a)
        assert startup_a_again["import_result"]["action"] == "imported"
        assert device_a.load_characters()["当前猫娘"] == "设备B角色"
        assert device_a.load_cloudsave_local_state()["last_applied_manifest_fingerprint"] == export_b["result"]["manifest"]["fingerprint"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_manual_startup_performs_fallback_import_and_continues_boot():
    import main_server

    fake_config_manager = SimpleNamespace(app_docs_dir=Path("/tmp/N.E.K.O"))
    fake_import_result = {"success": True, "action": "imported"}
    mock_bootstrap = Mock()
    fake_cloudsave_manager = SimpleNamespace(import_if_needed=Mock(return_value=fake_import_result))
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
         patch.object(main_server, "_cloudsave_manager", fake_cloudsave_manager), \
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
        fake_cloudsave_manager.import_if_needed.assert_called_once_with(reason="main_server_startup")
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
async def test_main_server_shutdown_tolerates_cloudsave_export_timeout():
    import main_server

    original_wait_for = main_server.asyncio.wait_for
    fake_tracker = SimpleNamespace(save=Mock())

    async def _wait_for_with_export_timeout(awaitable, timeout):
        if timeout == 3.0:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError
        return await original_wait_for(awaitable, timeout)

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "session_manager", {}), \
         patch.object(main_server.asyncio, "wait_for", side_effect=_wait_for_with_export_timeout), \
         patch.object(main_server, "_cloudsave_manager", SimpleNamespace(export_snapshot=Mock(return_value={"success": True}))), \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker), \
         patch.object(main_server.logger, "warning", Mock()) as mock_warning:
        await main_server.on_shutdown()

    fake_tracker.save.assert_called_once_with()
    mock_warning.assert_any_call("Steam Auto-Cloud shutdown export timed out after 3 seconds; Steam may upload the previous local snapshot")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_server_shutdown_releases_live_sessions_and_exports_cloudsave():
    import main_server

    fake_tracker = SimpleNamespace(save=Mock())
    export_snapshot = Mock(return_value={"success": True, "action": "exported"})
    manager_with_resampler = SimpleNamespace(audio_resampler=object())

    with patch.object(main_server, "_IS_MAIN_PROCESS", True), \
         patch.object(main_server, "_preload_task", None), \
         patch.object(main_server, "agent_event_bridge", None), \
         patch.object(main_server, "session_manager", {"角色A": manager_with_resampler, "角色B": object(), "空槽": None}), \
         patch.object(main_server, "_cloudsave_manager", SimpleNamespace(export_snapshot=export_snapshot)), \
         patch("main_routers.characters_router.release_memory_server_character", AsyncMock(return_value=True)) as mock_release, \
         patch("utils.language_utils.aclose_translation_service", AsyncMock(return_value=None), create=True), \
         patch("utils.music_crawlers.close_all_crawlers", AsyncMock(return_value=None)), \
         patch("utils.token_tracker.TokenTracker.get_instance", return_value=fake_tracker):
        await main_server.on_shutdown()

    assert manager_with_resampler.audio_resampler is None
    export_snapshot.assert_called_once_with(reason="main_server_shutdown")
    assert mock_release.await_count == 2
    mock_release.assert_any_await("角色A", reason="Steam Auto-Cloud export before shutdown: 角色A")
    mock_release.assert_any_await("角色B", reason="Steam Auto-Cloud export before shutdown: 角色B")
    fake_tracker.save.assert_called_once_with()
