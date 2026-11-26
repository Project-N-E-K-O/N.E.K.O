from __future__ import annotations

import copy
from pathlib import Path

import pytest

from plugin.server.infrastructure import config_paths, config_profiles_write
from plugin.server.infrastructure.config_merge import deep_merge
from plugin.server.infrastructure.config_profiles import apply_user_config_profiles
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
from plugin.server.infrastructure.config_updates import update_plugin_config


@pytest.mark.plugin_unit
def test_deep_merge_preserves_overlay_markers_for_missing_sections_until_base_merge() -> None:
    delete_updates = {"sec": {"x": "__DELETE__"}}
    delete_overlay = deep_merge({}, delete_updates)
    assert delete_overlay == {"sec": {"x": "__DELETE__"}}
    assert deep_merge({"sec": {"x": 1, "y": 2}}, delete_overlay) == {"sec": {"y": 2}}

    delete_add_updates = {"sec": {"x": "__DELETE__", "z": 3}}
    delete_add_overlay = deep_merge({}, delete_add_updates)
    assert delete_add_overlay == {"sec": {"x": "__DELETE__", "z": 3}}
    assert deep_merge({"sec": {"x": 1, "y": 2}}, delete_add_overlay) == {
        "sec": {"y": 2, "z": 3}
    }

    replace_updates = {"sec": {"sub": {"a": 9, "__replace__": True}}}
    replace_overlay = deep_merge({}, replace_updates)
    assert replace_overlay == {"sec": {"sub": {"a": 9, "__replace__": True}}}
    assert deep_merge({"sec": {"sub": {"a": 1, "b": 2}}}, replace_overlay) == {
        "sec": {"sub": {"a": 9}}
    }

    base: dict[str, object] = {"sec": {"keep": 1, "drop": 2, "nested": {"x": 1, "y": 2}}}
    updates: dict[str, object] = {
        "sec": {
            "drop": "__DELETE__",
            "add": 3,
            "nested": {"__replace__": True, "z": 9},
            "cleared": {},
        }
    }
    base_before = copy.deepcopy(base)
    updates_before = copy.deepcopy(updates)
    assert deep_merge(base, updates) == {
        "sec": {"keep": 1, "add": 3, "nested": {"z": 9}, "cleared": {}}
    }
    assert deep_merge({"sec": {"k": 1}}, {"sec": {}}) == {"sec": {}}
    assert base == base_before
    assert updates == updates_before


@pytest.mark.plugin_unit
def test_runtime_overlay_update_and_resolve_preserves_delete_and_replace_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    plugin_root = tmp_path / "plugins"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    def _setup_plugin(plugin_id: str) -> Path:
        installed_dir = plugin_root / plugin_id
        installed_dir.mkdir(parents=True)
        manifest_path = installed_dir / "plugin.toml"
        manifest_path.write_text(
            (
                "[plugin]\n"
                f"id = '{plugin_id}'\n"
                "entry = 'demo:Plugin'\n"
                "\n[sec]\n"
                "x = 1\n"
                "y = 2\n"
                "\n[sec.sub]\n"
                "a = 1\n"
                "b = 2\n"
            ),
            encoding="utf-8",
        )
        # Runtime starts from config.example.toml, which omits [sec].
        (installed_dir / "config.example.toml").write_text(
            f"[plugin]\nid = '{plugin_id}'\nentry = 'demo:Plugin'\n",
            encoding="utf-8",
        )
        return manifest_path

    delete_manifest = _setup_plugin("overlay_delete_demo")
    delete_res = update_plugin_config("overlay_delete_demo", {"sec": {"x": "__DELETE__"}})
    assert delete_res["config"]["sec"] == {"y": 2, "sub": {"a": 1, "b": 2}}
    delete_resolved = resolve_plugin_config_from_path(
        "overlay_delete_demo", config_path=delete_manifest
    )
    assert delete_resolved["effective_config"]["sec"] == {
        "y": 2,
        "sub": {"a": 1, "b": 2},
    }

    replace_manifest = _setup_plugin("overlay_replace_demo")
    replace_res = update_plugin_config(
        "overlay_replace_demo", {"sec": {"sub": {"a": 9, "__replace__": True}}}
    )
    assert replace_res["config"]["sec"]["sub"] == {"a": 9}
    replace_resolved = resolve_plugin_config_from_path(
        "overlay_replace_demo", config_path=replace_manifest
    )
    assert replace_resolved["effective_config"]["sec"]["sub"] == {"a": 9}


@pytest.mark.plugin_unit
def test_sparse_profile_update_and_apply_preserves_nested_delete_and_replace_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "sparse_profile_demo"
    plugin_dir = tmp_path / plugin_id
    profiles_dir = plugin_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    manifest_path = plugin_dir / "plugin.toml"
    manifest_path.write_text(
        (
            "[plugin]\n"
            f"id = '{plugin_id}'\n"
            "entry = 'demo:Plugin'\n"
            "\n[plugin.config_profiles]\n"
            "active = 'dev'\n"
            "\n[plugin.config_profiles.files]\n"
            "dev = 'profiles/dev.toml'\n"
        ),
        encoding="utf-8",
    )
    (profiles_dir / "dev.toml").write_text("", encoding="utf-8")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(config_profiles_write, "get_plugin_config_path", lambda _pid: manifest_path)

    config_profiles_write.update_profile_config(
        plugin_id=plugin_id,
        profile_name="dev",
        updates={
            "sec": {
                "sub": {"x": "__DELETE__"},
                "rep": {"a": 9, "__replace__": True},
            }
        },
    )

    base_config: dict[str, object] = {
        "plugin": {
            "id": plugin_id,
            "config_profiles": {
                "active": "dev",
                "files": {"dev": "profiles/dev.toml"},
            },
        },
        "sec": {
            "sub": {"x": 1, "y": 2},
            "rep": {"a": 1, "b": 2},
        },
    }
    effective = apply_user_config_profiles(
        plugin_id=plugin_id,
        base_config=base_config,
        config_path=manifest_path,
    )
    assert effective["sec"]["sub"] == {"y": 2}
    assert effective["sec"]["rep"] == {"a": 9}
