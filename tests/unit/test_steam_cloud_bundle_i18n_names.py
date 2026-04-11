import io
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

import pytest

from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.cloudsave_runtime import (
    bootstrap_local_cloudsave_environment,
    export_local_cloudsave_snapshot,
    import_local_cloudsave_snapshot,
)
from utils.config_manager import ConfigManager, set_reserved
from utils.file_utils import atomic_write_json
from utils.steam_cloud_bundle import _apply_bundle_to_local_cloudsave, _write_remote_bundle


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
