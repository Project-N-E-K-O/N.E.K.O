"""Tests that the new neko_plugin_cli package is importable and functional.

These tests verify the restructured import paths work correctly,
independent of the backward-compatible public/ shim.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

pytestmark = pytest.mark.plugin_unit


def test_core_package_exports_all_public_functions() -> None:
    from neko_plugin_cli.core import (
        PackResult,
        analyze_bundle_plugins,
        inspect_package,
        pack_bundle,
        pack_plugin,
        unpack_package,
    )
    assert callable(pack_plugin)
    assert callable(pack_bundle)
    assert callable(inspect_package)
    assert callable(unpack_package)
    assert callable(analyze_bundle_plugins)
    assert PackResult is not None


def test_core_submodules_are_importable() -> None:
    from neko_plugin_cli.core import normalize, archive_utils, pack_rules, plugin_source
    assert hasattr(normalize, "validate_archive_entry_name")
    assert hasattr(archive_utils, "compute_archive_payload_hash")
    assert hasattr(pack_rules, "should_skip_path")
    assert hasattr(plugin_source, "load_plugin_source")


def test_cli_module_is_importable() -> None:
    from neko_plugin_cli.cli import main, build_parser
    assert callable(main)
    assert callable(build_parser)


def test_paths_module_is_importable() -> None:
    from neko_plugin_cli.paths import CliDefaults, resolve_default_paths
    assert callable(resolve_default_paths)
    defaults = resolve_default_paths()
    assert isinstance(defaults, CliDefaults)


def test_pack_verify_roundtrip_via_new_imports(tmp_path: Path) -> None:
    """Full pack → inspect → verify roundtrip using the new import path."""
    from neko_plugin_cli.core import inspect_package, pack_plugin

    plugin_dir = tmp_path / "roundtrip_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nid = "roundtrip_plugin"\nname = "Roundtrip"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("X = 1\n", encoding="utf-8")

    package_path = tmp_path / "roundtrip_plugin.neko-plugin"
    result = pack_plugin(plugin_dir, package_path)
    assert result.plugin_id == "roundtrip_plugin"

    inspect_result = inspect_package(package_path)
    assert inspect_result.payload_hash_verified is True
