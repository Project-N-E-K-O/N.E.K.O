"""Path-containment security tests for cloudsave character binding resolution (F0517)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import cloudsave_runtime
from utils.cloudsave_runtime import bindings


def _make_config_manager(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    live2d_dir = tmp_path / "user_live2d"
    readable_live2d_dir = tmp_path / "readable_live2d"
    vrm_dir = tmp_path / "user_vrm"
    mmd_dir = tmp_path / "user_mmd"
    workshop_dir = tmp_path / "workshop"
    for d in (
        project_root / "static" / "vrm",
        project_root / "static" / "mmd",
        live2d_dir,
        readable_live2d_dir,
        vrm_dir,
        mmd_dir,
        workshop_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        project_root=project_root,
        live2d_dir=live2d_dir,
        readable_live2d_dir=readable_live2d_dir,
        vrm_dir=vrm_dir,
        mmd_dir=mmd_dir,
        get_workshop_path=lambda: str(workshop_dir),
    )


def test_cloudsave_runtime_facade_exports_remain_intact():
    """Package-split facade must continue exporting binding helpers."""
    assert cloudsave_runtime._resolve_binding_file_path is bindings._resolve_binding_file_path
    assert cloudsave_runtime._is_path_within is bindings._is_path_within
    assert cloudsave_runtime._derive_character_binding_summary is bindings._derive_character_binding_summary


@pytest.mark.parametrize(
    ("model_type", "model_ref", "asset_source", "asset_source_id"),
    [
        ("live2d", "/user_live2d/../../secret.model3.json", "local_imported", ""),
        ("live2d", "/user_live2d//secret.model3.json", "local_imported", ""),
        ("live2d", "/user_live2d_local/../../secret.model3.json", "local_imported", ""),
        ("live2d", "/workshop/../../secret.model3.json", "steam_workshop", "123"),
        ("live2d", "../../secret/m.model3.json", "local_imported", ""),
        ("live2d", "m.model3.json", "steam_workshop", "../../secret"),
        ("live2d", "m.model3.json\x00.txt", "local_imported", ""),
        ("vrm", "/user_vrm/../../secret.vrm", "local_imported", ""),
        ("vrm", "/static/vrm/../../secret.vrm", "builtin", ""),
        ("vrm", "/workshop/../../secret.vrm", "steam_workshop", "123"),
        ("mmd", "/user_mmd/../../secret.pmx", "local_imported", ""),
        ("mmd", "/static/mmd/../../secret.pmx", "builtin", ""),
        ("mmd", "/workshop/../../secret.pmx", "steam_workshop", "123"),
    ],
)
def test_resolve_binding_file_path_rejects_traversal_and_outside_files(
    tmp_path: Path,
    model_type: str,
    model_ref: str,
    asset_source: str,
    asset_source_id: str,
):
    """F0517: traversal or root-discarding model_ref/asset_source_id must never resolve outside its root."""
    cm = _make_config_manager(tmp_path)
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir(exist_ok=True)
    for fname in ("secret.model3.json", "m.model3.json", "secret.vrm", "secret.pmx"):
        (secret_dir / fname).write_text("secret-data", encoding="utf-8")
    (tmp_path / "secret.model3.json").write_text("secret-data", encoding="utf-8")
    (tmp_path / "secret.vrm").write_text("secret-data", encoding="utf-8")
    (tmp_path / "secret.pmx").write_text("secret-data", encoding="utf-8")

    # Also test leading-slash /user_live2d//<abs> targeting a real temp file.
    abs_secret = (tmp_path / "secret.model3.json").as_posix()
    for ref in (model_ref, f"/user_live2d//{abs_secret}" if model_ref == "/user_live2d//secret.model3.json" else model_ref):
        resolved = bindings._resolve_binding_file_path(
            cm,
            model_type=model_type,
            model_ref=ref,
            asset_source=asset_source,
            asset_source_id=asset_source_id,
        )
        assert resolved is None


def test_resolve_binding_file_path_rejects_symlink_escape(tmp_path: Path):
    """F0517: symlinked model files pointing outside the allowed root must be rejected."""
    cm = _make_config_manager(tmp_path)
    outside = tmp_path / "outside_secret.model3.json"
    outside.write_text('{"Version": 3}', encoding="utf-8")
    model_dir = cm.live2d_dir / "escape_model"
    model_dir.mkdir(parents=True)
    link = model_dir / "escape_model.model3.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    resolved = bindings._resolve_binding_file_path(
        cm,
        model_type="live2d",
        model_ref="/user_live2d/escape_model/escape_model.model3.json",
        asset_source="local_imported",
        asset_source_id="",
    )
    assert resolved is None

    summary = bindings._derive_character_binding_summary(
        cm,
        "test_char",
        {"live2d": "/user_live2d/escape_model/escape_model.model3.json"},
    )
    assert summary["asset_fingerprint"] == ""


def test_resolve_binding_file_path_allows_legitimate_assets(tmp_path: Path):
    """F0517: valid in-root Live2D, VRM, MMD, and Workshop assets still resolve cleanly."""
    cm = _make_config_manager(tmp_path)
    model_file = cm.live2d_dir / "haru" / "haru.model3.json"
    model_file.parent.mkdir(parents=True)
    model_file.write_text('{"Version": 3}', encoding="utf-8")

    vrm_file = cm.vrm_dir / "avatar.vrm"
    vrm_file.write_bytes(b"glTF")

    resolved_l2d = bindings._resolve_binding_file_path(
        cm,
        model_type="live2d",
        model_ref="/user_live2d/haru/haru.model3.json",
        asset_source="local_imported",
        asset_source_id="",
    )
    assert resolved_l2d == model_file

    resolved_vrm = bindings._resolve_binding_file_path(
        cm,
        model_type="vrm",
        model_ref="/user_vrm/avatar.vrm",
        asset_source="local_imported",
        asset_source_id="",
    )
    assert resolved_vrm == vrm_file


def test_is_plain_component_rejects_backslash_and_slash_separators(tmp_path: Path):
    """F0517: _is_plain_component must reject both '/' and '\\' separators."""
    assert bindings._is_plain_component("123\\sub") is False
    assert bindings._is_plain_component("123/sub") is False
    assert bindings._is_plain_component("123") is True

    cm = _make_config_manager(tmp_path)
    assert (
        bindings._resolve_binding_file_path(
            cm,
            model_type="live2d",
            model_ref="m.model3.json",
            asset_source="steam_workshop",
            asset_source_id="123\\sub",
        )
        is None
    )


def test_local_live2d_fallback_rejects_symlinked_workshop_item_dir(tmp_path: Path):
    """F0517: symlinked workshop item directories pointing outside workshop_root must be skipped."""
    cm = _make_config_manager(tmp_path)
    outside_dir = tmp_path / "outside_item"
    outside_dir.mkdir(parents=True)
    outside_model = outside_dir / "evil.model3.json"
    outside_model.write_text('{"Version": 3}', encoding="utf-8")

    workshop_dir = Path(cm.get_workshop_path())
    escape_item = workshop_dir / "escape_item"
    try:
        escape_item.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    resolved = bindings._resolve_binding_file_path(
        cm,
        model_type="live2d",
        model_ref="escape_item/evil.model3.json",
        asset_source="local_imported",
        asset_source_id="",
    )
    assert resolved is None
