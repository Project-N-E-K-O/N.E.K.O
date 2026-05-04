"""Tests for neko_plugin_cli.paths — CLI default path resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko_plugin_cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.paths import CliDefaults, resolve_default_paths

pytestmark = pytest.mark.plugin_unit


def test_resolve_default_paths_from_repo_layout() -> None:
    """Default resolution should find the real repo paths."""
    defaults = resolve_default_paths()
    assert defaults.plugins_root.name == "plugins"
    assert defaults.target_dir.name == "target"
    assert defaults.profiles_root.name == ".neko-package-profiles"
    assert defaults.plugin_root.name == "plugin"


def test_resolve_default_paths_with_custom_cli_root(tmp_path: Path) -> None:
    """Custom cli_root should shift all derived paths accordingly."""
    fake_cli = tmp_path / "repo" / "plugin" / "neko_plugin_cli"
    fake_cli.mkdir(parents=True)

    defaults = resolve_default_paths(cli_root=fake_cli)

    assert defaults.target_dir == fake_cli / "target"
    assert defaults.plugins_root == tmp_path / "repo" / "plugin" / "plugins"
    assert defaults.profiles_root == tmp_path / "repo" / "plugin" / ".neko-package-profiles"
    assert defaults.repo_root == tmp_path / "repo"


def test_cli_defaults_repo_root_property() -> None:
    defaults = CliDefaults(
        plugin_root=Path("/fake/plugin"),
        target_dir=Path("/fake/plugin/neko_plugin_cli/target"),
        plugins_root=Path("/fake/plugin/plugins"),
        profiles_root=Path("/fake/plugin/.neko-package-profiles"),
    )
    assert defaults.repo_root == Path("/fake")
