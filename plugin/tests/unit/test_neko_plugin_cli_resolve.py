"""Tests for neko_plugin_cli.commands._resolve — CLI path resolution helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko_plugin_cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.commands._resolve import (
    resolve_package_path,
    resolve_plugin_dir_candidate,
    resolve_plugin_dirs,
)
from neko_plugin_cli.paths import CliDefaults

pytestmark = pytest.mark.plugin_unit


def _make_defaults(tmp_path: Path) -> CliDefaults:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    return CliDefaults(
        plugin_root=tmp_path,
        target_dir=target_dir,
        plugins_root=plugins_root,
        profiles_root=tmp_path / "profiles",
    )


def _make_plugin(plugins_root: Path, plugin_id: str) -> Path:
    plugin_dir = plugins_root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nid = "{plugin_id}"\nname = "{plugin_id}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return plugin_dir


# ---------------------------------------------------------------------------
# resolve_plugin_dirs
# ---------------------------------------------------------------------------

def test_resolve_plugin_dirs_pack_all(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    _make_plugin(defaults.plugins_root, "alpha")
    _make_plugin(defaults.plugins_root, "beta")

    result = resolve_plugin_dirs(plugin_names=[], pack_all=True, defaults=defaults)
    names = [p.name for p in result]
    assert "alpha" in names
    assert "beta" in names


def test_resolve_plugin_dirs_pack_all_empty_raises(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    with pytest.raises(FileNotFoundError, match="no plugin.toml files found"):
        resolve_plugin_dirs(plugin_names=[], pack_all=True, defaults=defaults)


def test_resolve_plugin_dirs_no_names_raises(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    with pytest.raises(ValueError, match="no plugin names provided"):
        resolve_plugin_dirs(plugin_names=[], pack_all=False, defaults=defaults)


def test_resolve_plugin_dirs_by_name(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    _make_plugin(defaults.plugins_root, "my_plugin")

    result = resolve_plugin_dirs(plugin_names=["my_plugin"], pack_all=False, defaults=defaults)
    assert len(result) == 1
    assert result[0].name == "my_plugin"


# ---------------------------------------------------------------------------
# resolve_plugin_dir_candidate
# ---------------------------------------------------------------------------

def test_resolve_plugin_dir_candidate_by_name(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    _make_plugin(defaults.plugins_root, "test_plugin")

    result = resolve_plugin_dir_candidate("test_plugin", defaults=defaults)
    assert result.name == "test_plugin"
    assert (result / "plugin.toml").is_file()


def test_resolve_plugin_dir_candidate_by_absolute_path(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    plugin_dir = _make_plugin(tmp_path / "elsewhere", "external_plugin")

    result = resolve_plugin_dir_candidate(str(plugin_dir), defaults=defaults)
    assert result == plugin_dir.resolve()


def test_resolve_plugin_dir_candidate_missing_raises(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    with pytest.raises(FileNotFoundError, match="plugin.toml not found"):
        resolve_plugin_dir_candidate("nonexistent", defaults=defaults)


# ---------------------------------------------------------------------------
# resolve_package_path
# ---------------------------------------------------------------------------

def test_resolve_package_path_absolute(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    pkg = tmp_path / "my.neko-plugin"
    pkg.touch()

    result = resolve_package_path(str(pkg), defaults=defaults)
    assert result == pkg.resolve()


def test_resolve_package_path_in_target_dir(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    pkg = defaults.target_dir / "demo.neko-plugin"
    pkg.touch()

    result = resolve_package_path("demo.neko-plugin", defaults=defaults)
    assert result == pkg.resolve()


def test_resolve_package_path_missing_raises(tmp_path: Path) -> None:
    defaults = _make_defaults(tmp_path)
    with pytest.raises(FileNotFoundError, match="package file not found"):
        resolve_package_path("nonexistent.neko-plugin", defaults=defaults)
