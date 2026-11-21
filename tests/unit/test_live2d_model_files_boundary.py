"""Model file listings (/model_files, /model_files_by_id) stay inside the model folder.

Links whose real path points inside the model folder keep working as before;
file or directory links pointing outside it are not followed or returned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from main_routers import live2d_router


def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as e:  # e.g. Windows without symlink privilege
        pytest.skip(f"symlinks unavailable: {e}")


def _write(path: Path, payload: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or {"Version": 3}), encoding="utf-8")


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """Files and folders next to (not inside) the model folder."""
    root = tmp_path / "outside"
    _write(root / "secret_dir" / "leak.motion3.json")
    _write(root / "secret_dir" / "deep" / "leak.exp3.json")
    _write(root / "leak_file.motion3.json")
    _write(root / "leak_file.exp3.json")
    _write(root / "other" / "other.model3.json")
    return root


def _build_model(model_dir: Path, outside: Path) -> None:
    """A normal model plus inside links (kept) and outside links (dropped)."""
    _write(model_dir / "m.model3.json")
    _write(model_dir / "motions" / "idle.motion3.json")
    _write(model_dir / "motions" / "sub" / "wave.motion3.json")
    _write(model_dir / "expressions" / "smile.exp3.json")
    # Links resolving inside the model folder: listed exactly as before.
    _link(model_dir / "motions", model_dir / "alias_motions")
    _link(model_dir / "expressions" / "smile.exp3.json", model_dir / "alias.exp3.json")
    # Links resolving outside the model folder: not followed / not returned.
    _link(outside / "secret_dir", model_dir / "out_dir")
    _link(outside / "leak_file.motion3.json", model_dir / "out_file.motion3.json")
    _link(outside / "leak_file.exp3.json", model_dir / "expressions" / "out_file.exp3.json")


_EXPECTED_MOTIONS = [
    "alias_motions/idle.motion3.json",
    "alias_motions/sub/wave.motion3.json",
    "motions/idle.motion3.json",
    "motions/sub/wave.motion3.json",
]
_EXPECTED_EXPRESSIONS = [
    "alias.exp3.json",
    "expressions/smile.exp3.json",
]


def _by_name(monkeypatch, model_dir: Path, name: str = "m") -> dict:
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda _name: (str(model_dir), f"/user_live2d/{name}")
    )
    return live2d_router.get_model_files(name)


def _by_id(monkeypatch, item_dir: Path, item_id: str = "123") -> dict:
    monkeypatch.setattr(
        live2d_router, "find_workshop_item_by_id", lambda _id: (str(item_dir), "/workshop")
    )
    return live2d_router.get_model_files_by_id(item_id)


def _assert_listing(result: dict) -> None:
    assert result["success"] is True, result
    assert sorted(result["motion_files"]) == _EXPECTED_MOTIONS
    assert sorted(result["expression_files"]) == _EXPECTED_EXPRESSIONS


def test_model_files_by_name_skips_links_leaving_model_dir(monkeypatch, tmp_path, outside):
    model_dir = tmp_path / "user_live2d" / "m"
    _build_model(model_dir, outside)
    _assert_listing(_by_name(monkeypatch, model_dir))


def test_model_files_by_id_skips_links_leaving_item_dir(monkeypatch, tmp_path, outside):
    item_dir = tmp_path / "workshop" / "123"
    _build_model(item_dir, outside)
    result = _by_id(monkeypatch, item_dir)
    _assert_listing(result)
    assert result["model_config_url"] == "/workshop/123/m.model3.json"


def test_model_files_by_id_nested_model_skips_links_leaving_item_dir(monkeypatch, tmp_path, outside):
    item_dir = tmp_path / "workshop" / "456"
    _build_model(item_dir / "m", outside)
    result = _by_id(monkeypatch, item_dir, "456")
    _assert_listing(result)
    assert result["model_config_url"] == "/workshop/456/m/m.model3.json"


def test_linked_model_folder_itself_still_lists(monkeypatch, tmp_path, outside):
    """The boundary is the model folder's real path, so a model folder that is itself a link works."""
    real_dir = tmp_path / "elsewhere" / "m"
    _build_model(real_dir, outside)
    linked = tmp_path / "user_live2d" / "m"
    linked.parent.mkdir(parents=True)
    _link(real_dir, linked)
    _assert_listing(_by_name(monkeypatch, linked))


def test_root_config_link_leaving_model_dir_is_not_used(monkeypatch, tmp_path, outside):
    model_dir = tmp_path / "user_live2d" / "m"
    model_dir.mkdir(parents=True)
    _link(outside / "other" / "other.model3.json", model_dir / "other.model3.json")
    assert live2d_router._locate_model_config(str(model_dir)) == (None, None, None)
    assert _by_name(monkeypatch, model_dir)["success"] is False


def test_subdir_link_leaving_model_dir_is_not_used(monkeypatch, tmp_path, outside):
    item_dir = tmp_path / "workshop" / "789"
    item_dir.mkdir(parents=True)
    _link(outside / "other", item_dir / "other")
    assert live2d_router._locate_model_config(str(item_dir)) == (None, None, None)
    assert _by_id(monkeypatch, item_dir, "789")["success"] is False


def test_nested_config_link_leaving_model_dir_is_not_used(tmp_path, outside):
    item_dir = tmp_path / "workshop" / "790"
    (item_dir / "m").mkdir(parents=True)
    _link(outside / "other" / "other.model3.json", item_dir / "m" / "m.model3.json")
    assert live2d_router._locate_model_config(str(item_dir)) == (None, None, None)


def test_inside_links_locate_config_as_before(tmp_path):
    """A subfolder link and a config link that resolve inside the folder are still found."""
    item_dir = tmp_path / "workshop" / "791"
    _write(item_dir / "real" / "m.model3.json")
    _link(item_dir / "real" / "m.model3.json", item_dir / "m.model3.json")
    assert live2d_router._locate_model_config(str(item_dir)) == (str(item_dir), "m.model3.json", None)

    # store/deep is two levels down, so only the inside link "alias" makes it reachable.
    nested = tmp_path / "workshop" / "792"
    _write(nested / "store" / "deep" / "m.model3.json")
    _link(nested / "store" / "deep", nested / "alias")
    assert live2d_router._locate_model_config(str(nested)) == (
        str(nested / "alias"), "m.model3.json", "alias"
    )


def test_subdir_link_leaving_model_dir_is_not_entered_even_if_config_links_back(tmp_path):
    """The folder link itself is checked, not only the config file found through it."""
    item_dir = tmp_path / "workshop" / "793"
    _write(item_dir / "store" / "deep" / "m.model3.json")
    elsewhere = tmp_path / "elsewhere_793"
    elsewhere.mkdir()
    _link(item_dir / "store" / "deep" / "m.model3.json", elsewhere / "m.model3.json")
    _link(elsewhere, item_dir / "out")
    assert live2d_router._locate_model_config(str(item_dir)) == (None, None, None)
