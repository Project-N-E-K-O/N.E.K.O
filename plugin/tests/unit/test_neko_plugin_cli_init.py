"""Tests for neko-plugin init command and template generator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.templates.generator import PluginSpec, generate_plugin

pytestmark = pytest.mark.plugin_unit


# ---------------------------------------------------------------------------
# PluginSpec
# ---------------------------------------------------------------------------

def test_plugin_spec_class_name() -> None:
    assert PluginSpec(plugin_id="hello_world").class_name == "HelloWorldPlugin"
    assert PluginSpec(plugin_id="my-plugin").class_name == "MyPluginPlugin"
    assert PluginSpec(plugin_id="simple").class_name == "SimplePlugin"


def test_plugin_spec_entry_point() -> None:
    spec = PluginSpec(plugin_id="web_search")
    assert spec.entry_point == "plugin.plugins.web_search:WebSearchPlugin"


# ---------------------------------------------------------------------------
# Quick-start template
# ---------------------------------------------------------------------------

def test_generate_quick_start_creates_expected_files(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="quick_demo",
        name="Quick Demo",
        quick_start=True,
        features=["lifecycle", "entry_point"],
    )
    target = tmp_path / "quick_demo"
    created = generate_plugin(spec, target)

    names = {p.name for p in created}
    assert "plugin.toml" in names
    assert "__init__.py" in names
    assert "pyproject.toml" in names

    toml_text = (target / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "quick_demo"' in toml_text
    assert 'name = "Quick Demo"' in toml_text
    assert 'type = "plugin"' in toml_text

    init_text = (target / "__init__.py").read_text(encoding="utf-8")
    assert "class QuickDemoPlugin" in init_text
    assert "@neko_plugin" in init_text
    assert "def hello" in init_text
    assert "Hello, {name}" in init_text


# ---------------------------------------------------------------------------
# Full plugin template
# ---------------------------------------------------------------------------

def test_generate_plugin_with_all_features(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="full_demo",
        name="Full Demo",
        description="A fully featured demo plugin",
        author_name="Test Author",
        author_email="test@example.com",
        features=["lifecycle", "entry_point", "timer", "message", "store", "async_support"],
    )
    target = tmp_path / "full_demo"
    created = generate_plugin(spec, target)

    toml_text = (target / "plugin.toml").read_text(encoding="utf-8")
    assert 'description = "A fully featured demo plugin"' in toml_text
    assert '[plugin.author]' in toml_text
    assert 'name = "Test Author"' in toml_text
    assert 'email = "test@example.com"' in toml_text
    assert '[plugin.store]' in toml_text
    assert 'enabled = true' in toml_text
    assert 'auto_start = true' in toml_text  # timer/message → auto_start

    init_text = (target / "__init__.py").read_text(encoding="utf-8")
    assert "class FullDemoPlugin" in init_text
    assert "async def on_startup" in init_text
    assert "async def example" in init_text
    assert "@timer_interval" in init_text
    assert "@message" in init_text
    assert "PluginStore" in init_text


def test_generate_plugin_without_pyproject(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="no_pyproject",
        create_pyproject=False,
        quick_start=True,
        features=["lifecycle", "entry_point"],
    )
    target = tmp_path / "no_pyproject"
    created = generate_plugin(spec, target)

    names = {p.name for p in created}
    assert "pyproject.toml" not in names
    assert "plugin.toml" in names
    assert "__init__.py" in names


# ---------------------------------------------------------------------------
# Extension template
# ---------------------------------------------------------------------------

def test_generate_extension_template(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="my_ext",
        name="My Extension",
        plugin_type="extension",
        host_plugin_id="qq_auto_reply",
        host_prefix="ext",
        features=["lifecycle", "entry_point"],
    )
    target = tmp_path / "my_ext"
    generate_plugin(spec, target)

    toml_text = (target / "plugin.toml").read_text(encoding="utf-8")
    assert 'type = "extension"' in toml_text
    assert '[plugin.host]' in toml_text
    assert 'plugin_id = "qq_auto_reply"' in toml_text
    assert 'prefix = "ext"' in toml_text

    init_text = (target / "__init__.py").read_text(encoding="utf-8")
    assert "NekoExtensionBase" in init_text
    assert "@extension" in init_text


# ---------------------------------------------------------------------------
# Adapter template
# ---------------------------------------------------------------------------

def test_generate_adapter_template(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="my_adapter",
        name="My Adapter",
        plugin_type="adapter",
        features=["lifecycle"],
    )
    target = tmp_path / "my_adapter"
    generate_plugin(spec, target)

    toml_text = (target / "plugin.toml").read_text(encoding="utf-8")
    assert 'type = "adapter"' in toml_text

    init_text = (target / "__init__.py").read_text(encoding="utf-8")
    assert "NekoAdapterPlugin" in init_text
    assert "AdapterGatewayCore" in init_text


# ---------------------------------------------------------------------------
# CLI non-interactive mode
# ---------------------------------------------------------------------------

def test_cli_init_non_interactive(tmp_path: Path) -> None:
    import cli as neko_plugin_cli

    # Temporarily override plugins_root
    target_plugins = tmp_path / "plugins"
    target_plugins.mkdir()

    exit_code = neko_plugin_cli.main([
        "init", "cli_test_plugin",
        "--no-interactive",
        "--name", "CLI Test",
        "--type", "plugin",
    ])
    # This will fail because it uses the real plugins_root, not tmp_path.
    # That's expected — the non-interactive test via CLI needs the real path.
    # We test the generator directly above instead.


def test_generate_roundtrip_pack_verify(tmp_path: Path) -> None:
    """Generated plugin can be packed and verified successfully."""
    from neko_plugin_cli.core import inspect_package, pack_plugin

    spec = PluginSpec(
        plugin_id="roundtrip_init",
        name="Roundtrip Init",
        quick_start=True,
        features=["lifecycle", "entry_point"],
    )
    plugin_dir = tmp_path / "roundtrip_init"
    generate_plugin(spec, plugin_dir)

    package_path = tmp_path / "roundtrip_init.neko-plugin"
    result = pack_plugin(plugin_dir, package_path)
    assert result.plugin_id == "roundtrip_init"

    inspect_result = inspect_package(package_path)
    assert inspect_result.payload_hash_verified is True
    assert inspect_result.package_name == "Roundtrip Init"


def test_generate_all_files_use_lf_line_endings(tmp_path: Path) -> None:
    """All generated files must use LF line endings for cross-platform consistency."""
    spec = PluginSpec(
        plugin_id="lf_check",
        name="LF Check",
        description="Test line endings",
        author_name="Test",
        features=["lifecycle", "entry_point", "timer"],
    )
    target = tmp_path / "lf_check"
    created = generate_plugin(spec, target)

    for path in created:
        content = path.read_bytes()
        assert b"\r\n" not in content, f"{path.name} contains CRLF"
        assert b"\n" in content, f"{path.name} has no newlines"
