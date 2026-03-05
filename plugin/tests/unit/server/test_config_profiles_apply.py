from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from plugin.server.infrastructure.config_profiles import apply_user_config_profiles


@pytest.mark.plugin_unit
def test_apply_user_config_profiles_uses_active_profile_from_plugin_section(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    profile_file = plugin_dir / "profiles" / "dev.toml"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("[runtime]\nlevel=2\n", encoding="utf-8")

    base_config: dict[str, object] = {
        "plugin": {
            "id": "demo",
            "config_profiles": {
                "active": "dev",
                "files": {"dev": "profiles/dev.toml"},
            },
        },
        "runtime": {"enabled": True, "level": 1},
    }

    merged = apply_user_config_profiles(
        plugin_id="demo",
        base_config=base_config,
        config_path=config_path,
    )
    assert merged["runtime"] == {"enabled": True, "level": 2}


@pytest.mark.plugin_unit
def test_apply_user_config_profiles_supports_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    prod_file = plugin_dir / "profiles" / "prod.toml"
    prod_file.parent.mkdir(parents=True, exist_ok=True)
    prod_file.write_text("[runtime]\nregion='prod'\n", encoding="utf-8")

    monkeypatch.setenv("NEKO_PLUGIN_DEMO_PROFILE", "prod")

    base_config: dict[str, object] = {
        "plugin": {
            "id": "demo",
            "config_profiles": {
                "active": "dev",
                "files": {"dev": "profiles/dev.toml", "prod": "profiles/prod.toml"},
            },
        },
        "runtime": {"region": "default"},
    }

    merged = apply_user_config_profiles(
        plugin_id="demo",
        base_config=base_config,
        config_path=config_path,
    )
    assert merged["runtime"] == {"region": "prod"}


@pytest.mark.plugin_unit
def test_apply_user_config_profiles_rejects_profile_with_plugin_section(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    profile_file = plugin_dir / "profiles" / "bad.toml"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("[plugin]\nname='hijack'\n", encoding="utf-8")

    base_config: dict[str, object] = {
        "plugin": {
            "id": "demo",
            "config_profiles": {
                "active": "bad",
                "files": {"bad": "profiles/bad.toml"},
            },
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        apply_user_config_profiles(plugin_id="demo", base_config=base_config, config_path=config_path)

    assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
def test_apply_user_config_profiles_rejects_path_escape(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    base_config: dict[str, object] = {
        "plugin": {
            "id": "demo",
            "config_profiles": {
                "active": "bad",
                "files": {"bad": "../outside.toml"},
            },
        },
        "runtime": {"safe": True},
    }

    merged = apply_user_config_profiles(plugin_id="demo", base_config=base_config, config_path=config_path)
    assert merged["runtime"] == {"safe": True}
