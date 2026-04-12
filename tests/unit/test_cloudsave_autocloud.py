import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.cloudsave_autocloud import CloudSaveManager, STEAM_AUTO_CLOUD_SYNC_BACKEND
from utils.cloudsave_runtime import (
    CloudsaveDeadlineExceeded,
    bootstrap_local_cloudsave_environment,
    export_cloudsave_character_unit,
    export_local_cloudsave_snapshot,
)
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


def _write_runtime_state(cm, *, character_name="小满"):
    from utils.config_manager import set_reserved

    characters = cm.get_default_characters()
    characters["猫娘"] = {
        character_name: characters["猫娘"][next(iter(characters["猫娘"]))]
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
        [{"role": "user", "content": "你好"}],
        ensure_ascii=False,
        indent=2,
    )

    workshop_model_dir = Path(cm.workshop_dir) / "123456" / "example"
    workshop_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(workshop_model_dir / "example.model3.json", {"Version": 3}, ensure_ascii=False, indent=2)


def _make_dummy_steamworks(*, running=True, logged_on=True):
    return SimpleNamespace(
        IsSteamRunning=lambda: running,
        Users=SimpleNamespace(LoggedOn=lambda: logged_on),
    )


@pytest.mark.unit
def test_cloudsave_manager_imports_snapshot_when_runtime_is_empty():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="小满")
        export_result = export_local_cloudsave_snapshot(source_cm)
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        manager = CloudSaveManager(target_cm)
        result = manager.import_if_needed(reason="unit_test_startup_import")

        assert result["success"] is True
        assert result["action"] == "imported"
        assert result["status"]["backend"] == STEAM_AUTO_CLOUD_SYNC_BACKEND
        assert result["status"]["startup_import_required"] is False
        assert target_cm.load_characters()["当前猫娘"] == "小满"
        assert target_cm.load_cloudsave_local_state()["last_applied_manifest_fingerprint"] == export_result["manifest"]["fingerprint"]


@pytest.mark.unit
def test_cloudsave_manager_imports_snapshot_when_runtime_only_has_pristine_migrated_defaults():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="小满")
        export_result = export_local_cloudsave_snapshot(source_cm)

        # Simulate real launcher/main_server startup where config migration copies
        # bundled defaults into the runtime root before cloudsave status is read.
        target_cm.migrate_config_files()
        target_cm.migrate_memory_files()
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        manager = CloudSaveManager(target_cm)
        pre_status = manager.build_status()
        result = manager.import_if_needed(reason="unit_test_pristine_migrated_defaults")

        assert pre_status["runtime_has_user_content"] is False
        assert pre_status["startup_import_required"] is True
        assert result["success"] is True
        assert result["action"] == "imported"
        assert target_cm.load_characters()["当前猫娘"] == "小满"
        assert target_cm.load_cloudsave_local_state()["last_applied_manifest_fingerprint"] == export_result["manifest"]["fingerprint"]


@pytest.mark.unit
def test_cloudsave_manager_skips_import_when_manifest_was_already_applied():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="小满")
        export_local_cloudsave_snapshot(source_cm)
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        manager = CloudSaveManager(target_cm)
        first_result = manager.import_if_needed(reason="first_import")
        second_result = manager.import_if_needed(reason="second_import")

        assert first_result["action"] == "imported"
        assert second_result["success"] is True
        assert second_result["action"] == "skipped"
        assert second_result["reason"] == "already_applied"
        assert second_result["status"]["startup_import_required"] is False


@pytest.mark.unit
def test_cloudsave_manager_stages_new_snapshot_without_auto_import_when_runtime_has_user_content():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="云端角色")
        export_result = export_local_cloudsave_snapshot(source_cm)

        _write_runtime_state(target_cm, character_name="本地角色")
        local_export_result = export_local_cloudsave_snapshot(target_cm)
        assert target_cm.load_cloudsave_local_state()["last_applied_manifest_fingerprint"] == local_export_result["manifest"]["fingerprint"]

        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        manager = CloudSaveManager(target_cm)
        status = manager.build_status()
        result = manager.import_if_needed(reason="import_new_cloud_snapshot_over_stale_runtime")

        assert status["has_snapshot"] is True
        assert status["runtime_has_user_content"] is True
        assert status["manifest_fingerprint"] == export_result["manifest"]["fingerprint"]
        assert status["last_applied_manifest_fingerprint"] == local_export_result["manifest"]["fingerprint"]
        assert status["startup_import_required"] is False
        assert status["manual_download_required"] is True
        assert result["success"] is True
        assert result["action"] == "skipped"
        assert result["reason"] == "manual_download_required"
        assert "wait for an explicit download/apply action" in result["hint"]
        assert target_cm.load_characters()["当前猫娘"] == "本地角色"
        assert target_cm.load_cloudsave_local_state()["last_applied_manifest_fingerprint"] == local_export_result["manifest"]["fingerprint"]


@pytest.mark.unit
def test_cloudsave_manager_exports_snapshot_with_steam_status():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")

        manager = CloudSaveManager(cm)
        result = manager.export_snapshot(
            reason="unit_test_shutdown_export",
            steamworks=_make_dummy_steamworks(),
        )

        manifest = json.loads((cm.cloudsave_dir / "manifest.json").read_text(encoding="utf-8"))
        assert result["success"] is True
        assert result["action"] == "exported"
        assert result["status"]["backend"] == STEAM_AUTO_CLOUD_SYNC_BACKEND
        assert result["status"]["has_snapshot"] is True
        assert result["status"]["startup_import_required"] is False
        assert result["status"]["steam_available"] is True
        assert result["status"]["last_applied_manifest_fingerprint"] == manifest["fingerprint"]
        assert result["status"]["last_successful_export_at"]
        assert manifest["fingerprint"]


@pytest.mark.unit
def test_cloudsave_manager_status_includes_autocloud_configuration_hints():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        status = manager.build_status()

        assert status["backend"] == STEAM_AUTO_CLOUD_SYNC_BACKEND
        assert status["app_id"] == "4099310"
        assert status["runtime_root"].endswith("N.E.K.O")
        assert status["cloudsave_root"].endswith("cloudsave")
        assert status["manifest_path"].endswith("manifest.json")
        assert status["snapshot_sequence_number"] == 0
        assert status["snapshot_exported_at_utc"] == ""
        assert status["source_launch"] is False
        assert status["steam_session_ready"] is False
        assert status["recommended_paths"]["primary_root"]["root"] == "WinAppDataLocal"
        assert status["recommended_paths"]["primary_root"]["subdirectory"] == "N.E.K.O/cloudsave"
        assert status["current_platform_rule"]["subdirectory"] == "N.E.K.O/cloudsave"


@pytest.mark.unit
def test_cloudsave_manager_status_reports_snapshot_metadata_after_export():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="快照状态角色")
        export_result = export_local_cloudsave_snapshot(cm)

        manager = CloudSaveManager(cm)
        status = manager.build_status()

        assert status["snapshot_sequence_number"] == export_result["manifest"]["sequence_number"]
        assert status["snapshot_exported_at_utc"] == export_result["manifest"]["exported_at_utc"]


@pytest.mark.unit
def test_cloudsave_manager_status_does_not_treat_source_launch_as_autocloud_ready():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        with patch("utils.cloudsave_autocloud.is_source_launch", return_value=True):
            status = manager.build_status(steamworks=_make_dummy_steamworks())

        assert status["source_launch"] is True
        assert status["steam_available"] is True
        assert status["steam_session_ready"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("platform_name", "expected_platform", "expected_root"),
    [
        ("win32", "Windows", "WinAppDataLocal"),
        ("darwin", "macOS", "MacAppSupport"),
        ("linux", "Linux", "LinuxXdgDataHome"),
    ],
)
def test_cloudsave_manager_status_reports_current_platform_rule(platform_name: str, expected_platform: str, expected_root: str):
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        with patch("utils.cloudsave_autocloud.sys.platform", platform_name):
            status = manager.build_status()

        assert status["current_platform_rule"]["platform"] == expected_platform
        assert status["current_platform_rule"]["root"] == expected_root


@pytest.mark.unit
def test_cloudsave_manager_no_snapshot_result_includes_diagnostic_hint():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        result = manager.import_if_needed(reason="unit_test_no_snapshot")

        assert result["success"] is True
        assert result["action"] == "skipped"
        assert result["reason"] == "no_snapshot"
        assert str(cm.cloudsave_dir) in result["hint"]
        assert "Steam" in result["hint"]


@pytest.mark.unit
def test_single_character_export_keeps_manifest_marked_as_already_applied():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

        export_cloudsave_character_unit(cm, "小满", overwrite=True)

        manager = CloudSaveManager(cm)
        status = manager.build_status()
        result = manager.import_if_needed(reason="post_single_export_check")

        assert status["manifest_fingerprint"]
        assert status["manifest_fingerprint"] == status["last_applied_manifest_fingerprint"]
        assert status["startup_import_required"] is False
        assert result["action"] == "skipped"
        assert result["reason"] == "already_applied"


@pytest.mark.unit
def test_cloudsave_manager_import_deadline_exceeded_before_apply_preserves_local_runtime():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)

        _write_runtime_state(source_cm, character_name="云端角色")
        export_local_cloudsave_snapshot(source_cm)

        _write_runtime_state(target_cm, character_name="本地角色")
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        manager = CloudSaveManager(target_cm)
        with pytest.raises(CloudsaveDeadlineExceeded):
            manager.import_if_needed(
                reason="budget_exhausted_before_apply",
                deadline_monotonic=0.0,
                force=True,
            )

        assert target_cm.load_characters()["当前猫娘"] == "本地角色"


@pytest.mark.unit
def test_cloudsave_manager_reraises_remote_bundle_download_deadline_exceeded():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        with patch(
            "utils.cloudsave_autocloud.download_cloudsave_bundle_from_steam",
            side_effect=CloudsaveDeadlineExceeded("steam_remote_download", stage="initialize"),
        ):
            with pytest.raises(CloudsaveDeadlineExceeded):
                manager.import_if_needed(reason="deadline_propagation_check", force=True)


@pytest.mark.unit
def test_cloudsave_manager_reraises_remote_bundle_upload_deadline_exceeded():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="超时上传角色")

        manager = CloudSaveManager(cm)
        with patch(
            "utils.cloudsave_autocloud.upload_cloudsave_bundle_to_steam",
            side_effect=CloudsaveDeadlineExceeded("steam_remote_upload", stage="write_remote"),
        ):
            with pytest.raises(CloudsaveDeadlineExceeded):
                manager.export_snapshot(reason="deadline_propagation_check")


@pytest.mark.unit
def test_cloudsave_manager_import_downloads_source_launch_bundle_before_local_import():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="云端Bundle角色")
        export_result = export_local_cloudsave_snapshot(source_cm)

        def _fake_download_bundle(config_manager, *, steamworks=None, deadline_monotonic=None):
            shutil.copytree(source_cm.cloudsave_dir, config_manager.cloudsave_dir, dirs_exist_ok=True)
            return {
                "success": True,
                "action": "downloaded",
                "meta": {
                    "manifest_fingerprint": export_result["manifest"]["fingerprint"],
                },
            }

        with patch("utils.cloudsave_autocloud.download_cloudsave_bundle_from_steam", side_effect=_fake_download_bundle):
            manager = CloudSaveManager(target_cm)
            result = manager.import_if_needed(reason="source_launch_remote_bundle")

        assert result["success"] is True
        assert result["action"] == "imported"
        assert result["remote_bundle_result"]["action"] == "downloaded"
        assert target_cm.load_characters()["当前猫娘"] == "云端Bundle角色"
        assert target_cm.load_cloudsave_local_state()["last_applied_manifest_fingerprint"] == export_result["manifest"]["fingerprint"]


@pytest.mark.unit
@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_cloudsave_manager_import_source_launch_on_desktop_uses_remote_bundle_helper(platform_name: str):
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        with patch(
            "utils.cloudsave_autocloud.download_cloudsave_bundle_from_steam",
            return_value={
                "success": True,
                "action": "skipped",
                "reason": "cloud_disabled",
            },
        ) as mock_download, patch("utils.cloudsave_autocloud.sys.platform", platform_name):
            result = manager.import_if_needed(reason=f"{platform_name}_source_launch_remote_bundle_gate")

        mock_download.assert_called_once()
        assert result["success"] is True
        assert result["action"] == "skipped"
        assert result["reason"] == "no_snapshot"
        assert result["remote_bundle_result"]["success"] is True
        assert result["remote_bundle_result"]["action"] == "skipped"
        assert result["remote_bundle_result"]["reason"] == "cloud_disabled"


@pytest.mark.unit
def test_cloudsave_manager_export_uploads_source_launch_bundle_after_local_export():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="Bundle上传角色")

        observed = {}

        def _fake_upload_bundle(config_manager, *, steamworks=None, deadline_monotonic=None):
            manifest = json.loads((config_manager.cloudsave_dir / "manifest.json").read_text(encoding="utf-8"))
            observed["fingerprint"] = manifest.get("fingerprint")
            observed["sequence_number"] = manifest.get("sequence_number")
            return {
                "success": True,
                "action": "uploaded",
                "meta": {
                    "manifest_fingerprint": manifest.get("fingerprint"),
                },
            }

        with patch("utils.cloudsave_autocloud.upload_cloudsave_bundle_to_steam", side_effect=_fake_upload_bundle):
            manager = CloudSaveManager(cm)
            result = manager.export_snapshot(reason="source_launch_remote_bundle_upload")

        assert result["success"] is True
        assert result["action"] == "exported"
        assert result["remote_bundle_result"]["action"] == "uploaded"
        assert observed["fingerprint"] == result["result"]["manifest"]["fingerprint"]
        assert observed["sequence_number"] == result["result"]["manifest"]["sequence_number"]


@pytest.mark.unit
def test_cloudsave_manager_export_keeps_local_export_success_when_remote_bundle_helper_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="本地导出角色")

        with patch(
            "utils.cloudsave_autocloud.upload_cloudsave_bundle_to_steam",
            side_effect=RuntimeError("remote helper failed"),
        ):
            manager = CloudSaveManager(cm)
            result = manager.export_snapshot(reason="remote_bundle_failure_does_not_fail_local_export")

        assert result["success"] is True
        assert result["action"] == "exported"
        assert result["remote_bundle_result"]["success"] is False
        assert result["remote_bundle_result"]["reason"] == "remote_bundle_upload_failed"
        assert result["result"]["manifest"]["fingerprint"]


@pytest.mark.unit
def test_cloudsave_manager_export_marks_partial_failure_when_remote_upload_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="远端失败角色")

        with patch(
            "utils.cloudsave_autocloud.upload_cloudsave_bundle_to_steam",
            return_value={
                "success": False,
                "action": "failed",
                "reason": "remote_bundle_upload_failed",
            },
        ):
            manager = CloudSaveManager(cm)
            result = manager.export_snapshot(reason="source_launch_remote_bundle_upload")

        assert result["success"] is True
        assert result["action"] == "exported"
        assert result["remote_bundle_result"]["success"] is False
        assert result["remote_bundle_result"]["reason"] == "remote_bundle_upload_failed"
        assert result["result"]["manifest"]["fingerprint"]


@pytest.mark.unit
def test_cloudsave_manager_upload_existing_snapshot_uses_remote_bundle_without_reexport():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="鍏抽棴涓婁紶瑙掕壊")
        export_result = export_local_cloudsave_snapshot(cm)

        observed = {}

        def _fake_upload_bundle(config_manager, *, steamworks=None, deadline_monotonic=None):
            manifest = json.loads((config_manager.cloudsave_dir / "manifest.json").read_text(encoding="utf-8"))
            observed["fingerprint"] = manifest.get("fingerprint")
            observed["sequence_number"] = manifest.get("sequence_number")
            return {
                "success": True,
                "action": "uploaded",
                "meta": {
                    "manifest_fingerprint": manifest.get("fingerprint"),
                },
            }

        with patch("utils.cloudsave_autocloud.upload_cloudsave_bundle_to_steam", side_effect=_fake_upload_bundle):
            manager = CloudSaveManager(cm)
            result = manager.upload_existing_snapshot(reason="main_server_shutdown_remote_upload")

        assert result["success"] is True
        assert result["action"] == "uploaded"
        assert result["remote_bundle_result"]["action"] == "uploaded"
        assert observed["fingerprint"] == export_result["manifest"]["fingerprint"]
        assert observed["sequence_number"] == export_result["manifest"]["sequence_number"]


@pytest.mark.unit
def test_cloudsave_manager_upload_existing_snapshot_skips_when_no_local_snapshot():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        manager = CloudSaveManager(cm)
        result = manager.upload_existing_snapshot(reason="main_server_shutdown_remote_upload")

        assert result["success"] is True
        assert result["action"] == "skipped"
        assert result["reason"] == "no_local_snapshot"
