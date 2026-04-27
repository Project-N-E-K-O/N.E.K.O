import io
import json
import shutil
import contextlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

import pytest

from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.cloudsave_runtime import (
    _atomic_copy_file as _runtime_atomic_copy_file,
    CloudsaveDeadlineExceeded,
    bootstrap_local_cloudsave_environment,
    export_local_cloudsave_snapshot,
    import_local_cloudsave_snapshot,
)
from utils.config_manager import ConfigManager, set_reserved
from utils.file_utils import atomic_write_json
from utils.steam_cloud_bundle import (
    REMOTE_BUNDLE_FILENAME,
    REMOTE_META_FILENAME,
    _apply_bundle_to_local_cloudsave,
    _cloudsave_manifest_matches_local_files,
    _write_remote_bundle,
    download_cloudsave_bundle_from_steam,
    upload_cloudsave_bundle_to_steam,
)


VALID_I18N_CASES = [
    ("Alice", "hello from english"),
    ("\u5c0f\u6ee1", "\u4f60\u597d\uff0c\u6765\u81ea\u7b80\u4f53\u4e2d\u6587"),
    ("\u5c0f\u6eff", "\u4f60\u597d\uff0c\u4f86\u81ea\u7e41\u9ad4\u4e2d\u6587"),
    ("\u3055\u304f\u3089", "\u3053\u3093\u306b\u3061\u306f\u3001\u65e5\u672c\u8a9e\u3067\u3059"),
    ("\ubbfc\uc11c", "\uc548\ub155\ud558\uc138\uc694, \ud55c\uad6d\uc5b4\uc785\ub2c8\ub2e4"),
]

VALID_BOUNDARY_CASES = [
    ("Luna-01", "hyphen"),
    ("Mina (JP)", "ascii parentheses"),
    ("\u6797\u00b7Mina", "middle dot"),
    ("Ari\u30fbSora", "katakana middle dot"),
    ("O\u2019Neil", "curly apostrophe"),
    ("Seo'Yun", "ascii apostrophe"),
    ("\u5168\u89d2\uff08JP\uff09", "full width parentheses"),
    ("Han\u2022Seo", "bullet separator"),
]

INVALID_NAME_CASES = [
    ("", "empty"),
    (".", "unsafe_dot"),
    ("foo.", "unsafe_dot"),
    ("foo/bar", "contains_path_separator"),
    ("..", "path_traversal"),
    ("api", "reserved_route_name"),
    ("AUX", "reserved_device_name"),
    ("bad*", "invalid_character"),
]


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[tmp_root],
    ), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ):
        config_manager = ConfigManager("N.E.K.O")
    config_manager.get_legacy_app_root_candidates = lambda: []
    return config_manager


def _write_runtime_state(cm, *, character_name: str, recent_message: str):
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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("character_name", "recent_message"),
    VALID_I18N_CASES + VALID_BOUNDARY_CASES,
)
def test_multilingual_character_names_roundtrip_through_local_snapshot(character_name: str, recent_message: str):
    validation = validate_character_name(character_name, max_units=PROFILE_NAME_MAX_UNITS)
    assert validation.ok, validation

    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)

        _write_runtime_state(source_cm, character_name=character_name, recent_message=recent_message)
        export_local_cloudsave_snapshot(source_cm)
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        import_local_cloudsave_snapshot(target_cm)

        restored_characters = target_cm.load_characters()
        restored_recent = json.loads(
            (Path(target_cm.memory_dir) / character_name / "recent.json").read_text(encoding="utf-8")
        )
        assert restored_characters["当前猫娘"] == character_name
        assert restored_recent[0]["content"] == recent_message


@pytest.mark.unit
@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_download_cloudsave_bundle_uses_bridge_on_desktop_source_launch(platform_name: str, tmp_path):
    cm = _make_config_manager(tmp_path / platform_name)
    bootstrap_local_cloudsave_environment(cm)
    observed = {"entered": False}

    class _DummyBridge:
        def cloud_enabled(self):
            observed["entered"] = True
            return False

    @contextlib.contextmanager
    def _fake_bridge(*, steamworks=None):
        yield _DummyBridge()

    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
        "utils.steam_cloud_bundle.sys.platform", platform_name
    ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", _fake_bridge):
        result = download_cloudsave_bundle_from_steam(cm)

    assert observed["entered"] is True
    assert result["success"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "cloud_disabled"


@pytest.mark.unit
def test_download_cloudsave_bundle_skips_on_windows_frozen_launch():
    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=False), patch(
        "utils.steam_cloud_bundle.sys.platform", "win32"
    ):
        result = download_cloudsave_bundle_from_steam(object())

    assert result["success"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "not_source_launch"


@pytest.mark.unit
def test_download_cloudsave_bundle_uses_bridge_on_windows_source_launch(tmp_path):
    cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(cm)
    observed = {"entered": False}

    class _DummyBridge:
        def cloud_enabled(self):
            observed["entered"] = True
            return False

    @contextlib.contextmanager
    def _fake_bridge(*, steamworks=None):
        yield _DummyBridge()

    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
        "utils.steam_cloud_bundle.sys.platform", "win32"
    ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", _fake_bridge):
        result = download_cloudsave_bundle_from_steam(cm)

    assert observed["entered"] is True
    assert result["success"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "cloud_disabled"


@pytest.mark.unit
def test_download_cloudsave_bundle_continues_when_remote_meta_is_not_json_object(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)
    _write_runtime_state(source_cm, character_name="无效元数据角色", recent_message="bundle fallback")
    export_result = export_local_cloudsave_snapshot(source_cm)

    bundle_path = tmp_path / "meta_invalid_bundle.zip"
    _write_remote_bundle(bundle_path, source_cm)
    bundle_bytes = bundle_path.read_bytes()

    class _DummyBridge:
        def cloud_enabled(self):
            return True

        def file_exists(self, filename):
            return filename in {"__neko_cloudsave_bundle_meta__.json", "__neko_cloudsave_bundle__.zip"}

        def read_file(self, filename):
            if filename == "__neko_cloudsave_bundle_meta__.json":
                return b"[]"
            if filename == "__neko_cloudsave_bundle__.zip":
                return bundle_bytes
            raise FileNotFoundError(filename)

    @contextlib.contextmanager
    def _fake_bridge(*, steamworks=None):
        yield _DummyBridge()

    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
        "utils.steam_cloud_bundle.sys.platform", "win32"
    ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", _fake_bridge):
        result = download_cloudsave_bundle_from_steam(target_cm)

    assert result["success"] is True
    assert result["action"] == "downloaded"
    assert result.get("meta") is None
    imported_manifest = json.loads((target_cm.cloudsave_dir / "manifest.json").read_text(encoding="utf-8"))
    assert imported_manifest["fingerprint"] == export_result["manifest"]["fingerprint"]


@pytest.mark.unit
@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_upload_cloudsave_bundle_uses_bridge_on_desktop_source_launch(platform_name: str, tmp_path):
    cm = _make_config_manager(tmp_path / platform_name)
    bootstrap_local_cloudsave_environment(cm)
    _write_runtime_state(cm, character_name=f"{platform_name}-bundle", recent_message="upload")
    export_local_cloudsave_snapshot(cm)
    observed = {"entered": False}

    class _DummyBridge:
        def cloud_enabled(self):
            observed["entered"] = True
            return False

    @contextlib.contextmanager
    def _fake_bridge(*, steamworks=None):
        yield _DummyBridge()

    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
        "utils.steam_cloud_bundle.sys.platform", platform_name
    ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", _fake_bridge):
        result = upload_cloudsave_bundle_to_steam(cm)

    assert observed["entered"] is True
    assert result["success"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "cloud_disabled"


@pytest.mark.unit
def test_upload_cloudsave_bundle_skips_on_windows_frozen_launch(tmp_path):
    cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(cm)
    _write_runtime_state(cm, character_name="冻结上传角色", recent_message="hello")
    export_local_cloudsave_snapshot(cm)

    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=False), patch(
        "utils.steam_cloud_bundle.sys.platform", "win32"
    ):
        result = upload_cloudsave_bundle_to_steam(cm)

    assert result["success"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "not_source_launch"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("character_name", "recent_message"),
    [
        *[(name, f"bundle::{message}") for name, message in VALID_I18N_CASES],
        *[(name, f"bundle::{message}") for name, message in VALID_BOUNDARY_CASES],
    ],
)
def test_multilingual_character_names_roundtrip_through_bundle_archive(character_name: str, recent_message: str, tmp_path):
    validation = validate_character_name(character_name, max_units=PROFILE_NAME_MAX_UNITS)
    assert validation.ok, validation

    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)

    _write_runtime_state(source_cm, character_name=character_name, recent_message=recent_message)
    export_result = export_local_cloudsave_snapshot(source_cm)

    bundle_path = tmp_path / "cloudsave_bundle.zip"
    bundle_info = _write_remote_bundle(bundle_path, source_cm)
    bundle_bytes = bundle_path.read_bytes()

    assert bundle_info["meta"]["manifest_fingerprint"] == export_result["manifest"]["fingerprint"]

    import zipfile

    with zipfile.ZipFile(bundle_path, "r") as archive:
        names = archive.namelist()
        assert any(character_name in name for name in names), names
        assert any(name.endswith("recent.json") and character_name in name for name in names), names

    apply_result = _apply_bundle_to_local_cloudsave(target_cm, bundle_bytes, bundle_info["meta"])
    import_local_cloudsave_snapshot(target_cm)

    restored_characters = target_cm.load_characters()
    restored_recent = json.loads(
        (Path(target_cm.memory_dir) / character_name / "recent.json").read_text(encoding="utf-8")
    )

    assert apply_result["manifest_fingerprint"] == export_result["manifest"]["fingerprint"]
    assert restored_characters["当前猫娘"] == character_name
    assert restored_recent[0]["content"] == recent_message


@pytest.mark.unit
@pytest.mark.parametrize(("character_name", "expected_code"), INVALID_NAME_CASES)
def test_invalid_character_names_are_rejected_by_validation(character_name: str, expected_code: str):
    validation = validate_character_name(character_name, max_units=PROFILE_NAME_MAX_UNITS)
    assert validation.ok is False
    assert validation.code == expected_code


@pytest.mark.unit
def test_apply_bundle_removes_stale_files_from_previous_local_snapshot(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)

    _write_runtime_state(source_cm, character_name="云端角色", recent_message="remote message")
    _write_runtime_state(target_cm, character_name="本地旧角色", recent_message="stale local message")

    source_export = export_local_cloudsave_snapshot(source_cm)
    export_local_cloudsave_snapshot(target_cm)

    stale_paths = [
        target_cm.cloudsave_dir / "memory" / "本地旧角色" / "recent.json",
        target_cm.cloudsave_dir / "bindings" / "本地旧角色.json",
        target_cm.cloudsave_dir / "characters" / "本地旧角色" / "profile.json",
    ]
    for stale_path in stale_paths:
        assert stale_path.exists(), f"expected stale path before apply: {stale_path}"

    bundle_path = tmp_path / "cloudsave_bundle.zip"
    bundle_info = _write_remote_bundle(bundle_path, source_cm)

    apply_result = _apply_bundle_to_local_cloudsave(target_cm, bundle_path.read_bytes(), bundle_info["meta"])

    assert apply_result["manifest_fingerprint"] == source_export["manifest"]["fingerprint"]
    for stale_path in stale_paths:
        assert not stale_path.exists(), f"expected stale path to be removed: {stale_path}"
    assert (target_cm.cloudsave_dir / "memory" / "云端角色" / "recent.json").exists()


@pytest.mark.unit
def test_apply_bundle_rejects_archive_entries_outside_cloudsave_root(tmp_path):
    cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(cm)

    malicious_bytes = io.BytesIO()
    with zipfile.ZipFile(malicious_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "fingerprint": "malicious-fingerprint",
                    "sequence_number": 1,
                    "files": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr("../escaped.txt", "should-not-be-written")

    stage_root = tmp_path / "staging-root"
    escaped_path = stage_root / "escaped.txt"
    with patch("utils.steam_cloud_bundle._create_staging_workspace", return_value=stage_root):
        with pytest.raises(ValueError, match="unsafe archive entry"):
            _apply_bundle_to_local_cloudsave(
                cm,
                malicious_bytes.getvalue(),
                {"manifest_fingerprint": "malicious-fingerprint"},
            )

    assert not escaped_path.exists()


@pytest.mark.unit
def test_apply_bundle_does_not_touch_live_cloudsave_when_staging_copy_fails(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)

    _write_runtime_state(source_cm, character_name="云端角色", recent_message="remote message")
    _write_runtime_state(target_cm, character_name="本地旧角色", recent_message="stale local message")
    export_local_cloudsave_snapshot(source_cm)
    export_local_cloudsave_snapshot(target_cm)

    bundle_path = tmp_path / "cloudsave_bundle.zip"
    bundle_info = _write_remote_bundle(bundle_path, source_cm)

    original_manifest = (target_cm.cloudsave_dir / "manifest.json").read_text(encoding="utf-8")
    original_recent_exists = (target_cm.cloudsave_dir / "memory" / "本地旧角色" / "recent.json").exists()

    def _fail_on_manifest_copy(src: Path, dst: Path):
        if dst.name == "manifest.json":
            raise OSError("simulated manifest stage copy failure")
        return _runtime_atomic_copy_file(src, dst)

    with patch("utils.steam_cloud_bundle._atomic_copy_file", side_effect=_fail_on_manifest_copy):
        with pytest.raises(OSError, match="simulated manifest stage copy failure"):
            _apply_bundle_to_local_cloudsave(target_cm, bundle_path.read_bytes(), bundle_info["meta"])

    assert (target_cm.cloudsave_dir / "manifest.json").read_text(encoding="utf-8") == original_manifest
    assert (target_cm.cloudsave_dir / "memory" / "本地旧角色" / "recent.json").exists() is original_recent_exists


@pytest.mark.unit
def test_upload_bundle_restores_previous_remote_files_when_meta_write_fails(tmp_path):
    cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(cm)
    _write_runtime_state(cm, character_name="上传回滚角色", recent_message="upload rollback")
    export_local_cloudsave_snapshot(cm)

    previous_bundle_bytes = b"previous-bundle"
    previous_meta_bytes = b'{"manifest_fingerprint":"previous"}'

    class _FailingMetaWriteBridge:
        def __init__(self):
            self.storage = {
                REMOTE_BUNDLE_FILENAME: previous_bundle_bytes,
                REMOTE_META_FILENAME: previous_meta_bytes,
            }

        def cloud_enabled(self) -> bool:
            return True

        def file_exists(self, remote_name: str) -> bool:
            return remote_name in self.storage

        def read_file(self, remote_name: str) -> bytes:
            return self.storage[remote_name]

        def write_file(self, remote_name: str, payload: bytes) -> None:
            if remote_name == REMOTE_META_FILENAME:
                raise RuntimeError("meta write failed")
            self.storage[remote_name] = payload

        def delete_file(self, remote_name: str) -> bool:
            self.storage.pop(remote_name, None)
            return True

    bridge = _FailingMetaWriteBridge()

    @contextlib.contextmanager
    def _fake_bridge(*, steamworks=None):
        del steamworks
        yield bridge

    with patch("utils.steam_cloud_bundle.is_source_launch", return_value=True), patch(
        "utils.steam_cloud_bundle.sys.platform", "win32"
    ), patch("utils.steam_cloud_bundle.steam_cloud_bundle_bridge", _fake_bridge):
        with pytest.raises(RuntimeError, match="meta write failed"):
            upload_cloudsave_bundle_to_steam(cm)

    assert bridge.storage[REMOTE_BUNDLE_FILENAME] == previous_bundle_bytes
    assert bridge.storage[REMOTE_META_FILENAME] == previous_meta_bytes


@pytest.mark.unit
def test_manifest_matcher_returns_false_when_payload_file_is_tampered(tmp_path):
    cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(cm)
    _write_runtime_state(cm, character_name="篡改校验角色", recent_message="digest check")
    export_result = export_local_cloudsave_snapshot(cm)

    manifest_files = export_result["manifest"]["files"]
    tamper_relative_path = next(iter(manifest_files.keys()))
    tamper_path = cm.cloudsave_dir / tamper_relative_path
    tamper_path.write_bytes(b"tampered-cloudsave-payload")

    assert _cloudsave_manifest_matches_local_files(cm, export_result["manifest"]["fingerprint"]) is False


@pytest.mark.unit
def test_write_remote_bundle_checks_deadline_during_per_file_loop(tmp_path):
    cm = _make_config_manager(tmp_path / "source")
    bootstrap_local_cloudsave_environment(cm)
    _write_runtime_state(cm, character_name="上传超时角色", recent_message="deadline")
    export_local_cloudsave_snapshot(cm)
    bundle_path = tmp_path / "deadline_bundle.zip"

    def _raise_on_per_file_write(deadline_monotonic, *, operation, stage):
        if stage.startswith("bundle_write_start:"):
            raise CloudsaveDeadlineExceeded(operation, stage=stage)

    with patch("utils.steam_cloud_bundle._assert_deadline_not_exceeded", side_effect=_raise_on_per_file_write):
        with pytest.raises(CloudsaveDeadlineExceeded):
            _write_remote_bundle(bundle_path, cm, deadline_monotonic=1.0)


@pytest.mark.unit
def test_apply_bundle_checks_deadline_during_per_file_copy(tmp_path):
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)
    _write_runtime_state(source_cm, character_name="下载超时角色", recent_message="deadline")
    export_local_cloudsave_snapshot(source_cm)

    bundle_path = tmp_path / "deadline_apply_bundle.zip"
    bundle_info = _write_remote_bundle(bundle_path, source_cm)
    bundle_bytes = bundle_path.read_bytes()

    def _raise_on_per_file_copy(deadline_monotonic, *, operation, stage):
        if stage.startswith("apply_copy_start:"):
            raise CloudsaveDeadlineExceeded(operation, stage=stage)

    with patch("utils.steam_cloud_bundle._assert_deadline_not_exceeded", side_effect=_raise_on_per_file_copy):
        with pytest.raises(CloudsaveDeadlineExceeded):
            _apply_bundle_to_local_cloudsave(
                target_cm,
                bundle_bytes,
                bundle_info["meta"],
                deadline_monotonic=1.0,
            )
