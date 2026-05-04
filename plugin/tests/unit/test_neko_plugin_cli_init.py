"""Tests for neko-plugin init command and template generator."""

from __future__ import annotations

import sys
from pathlib import Path
import shutil

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko_plugin_cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.templates.generator import PluginSpec, generate_plugin, generate_repo_support_files

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


def test_plugin_spec_entry_point_override() -> None:
    spec = PluginSpec(
        plugin_id="lifekit",
        entry_point_override="plugin.plugins.lifekit:LifeKitPlugin",
    )
    assert spec.entry_point == "plugin.plugins.lifekit:LifeKitPlugin"


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
    assert "README.md" in names
    assert "test_smoke.py" in names
    assert ".gitignore" in names
    assert "settings.json" in names
    assert "tasks.json" in names

    toml_text = (target / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "quick_demo"' in toml_text
    assert 'name = "Quick Demo"' in toml_text
    assert 'type = "plugin"' in toml_text

    init_text = (target / "__init__.py").read_text(encoding="utf-8")
    assert "class QuickDemoPlugin" in init_text
    assert "@neko_plugin" in init_text
    assert "def hello" in init_text
    assert "Hello, {name}" in init_text
    assert "Err" not in init_text
    assert "SdkError" not in init_text

    readme_text = (target / "README.md").read_text(encoding="utf-8")
    assert "uv run python neko_plugin_cli/cli.py doctor quick_demo" in readme_text
    assert "uv run python neko_plugin_cli/cli.py release-check quick_demo" in readme_text
    assert 'entry = "plugin.plugins.quick_demo:QuickDemoPlugin"' in readme_text
    assert (target / "tests" / "test_smoke.py").is_file()
    assert (target / ".vscode" / "settings.json").is_file()
    assert (target / ".vscode" / "tasks.json").is_file()

    tasks_text = (target / ".vscode" / "tasks.json").read_text(encoding="utf-8")
    assert "N.E.K.O: doctor quick_demo" in tasks_text
    assert "N.E.K.O: release-check quick_demo" in tasks_text
    assert "N.E.K.O: pack quick_demo" in tasks_text
    assert "uv run python neko_plugin_cli/cli.py verify quick_demo.neko-plugin" not in tasks_text

    settings_text = (target / ".vscode" / "settings.json").read_text(encoding="utf-8")
    assert '"nekoPlugin.pluginRoot": "../.."' in settings_text


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


def test_generate_plugin_without_vscode(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="no_vscode",
        quick_start=True,
        create_vscode=False,
    )
    target = tmp_path / "no_vscode"
    generate_plugin(spec, target)

    assert not (target / ".vscode" / "settings.json").exists()
    assert not (target / ".vscode" / "tasks.json").exists()


def test_generate_repo_support_files_preserves_existing_core_files(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "existing_plugin"
    plugin_dir.mkdir()
    plugin_toml = plugin_dir / "plugin.toml"
    init_py = plugin_dir / "__init__.py"
    plugin_toml.write_text("[plugin]\nid = \"existing_plugin\"\n", encoding="utf-8")
    init_py.write_text("ORIGINAL = True\n", encoding="utf-8")

    created = generate_repo_support_files(
        PluginSpec(plugin_id="existing_plugin", name="Existing Plugin", create_github_actions=True),
        plugin_dir,
    )

    assert plugin_toml.read_text(encoding="utf-8") == "[plugin]\nid = \"existing_plugin\"\n"
    assert init_py.read_text(encoding="utf-8") == "ORIGINAL = True\n"
    assert plugin_dir / "README.md" in created
    assert plugin_dir / ".github" / "workflows" / "verify.yml" in created
    assert (plugin_dir / ".vscode" / "tasks.json").is_file()


def test_generate_repo_support_files_skips_existing_without_overwrite(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "existing_plugin"
    plugin_dir.mkdir()
    readme = plugin_dir / "README.md"
    readme.write_text("custom\n", encoding="utf-8")

    generate_repo_support_files(PluginSpec(plugin_id="existing_plugin"), plugin_dir)

    assert readme.read_text(encoding="utf-8") == "custom\n"


def test_generate_repo_verification_workflow_when_requested(tmp_path: Path) -> None:
    spec = PluginSpec(
        plugin_id="verify_demo",
        name="Verify Demo",
        quick_start=True,
        create_github_actions=True,
        neko_repository="neko-org/N.E.K.O",
        neko_ref="v1.2.3",
    )
    target = tmp_path / "verify_demo"
    created = generate_plugin(spec, target)

    assert target / ".github" / "workflows" / "verify.yml" in created
    workflow_text = (target / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "name: Verify N.E.K.O Plugin" in workflow_text
    assert "PLUGIN_ID: verify_demo" in workflow_text
    assert "NEKO_REPOSITORY: neko-org/N.E.K.O" in workflow_text
    assert "NEKO_REF: v1.2.3" in workflow_text
    assert "Checkout N.E.K.O" in workflow_text
    assert 'release-check "${PLUGIN_ID}"' in workflow_text
    assert "${PLUGIN_ID}.release-check.txt" in workflow_text
    assert "N.E.K.O Plugin Verification" in workflow_text
    assert "Release Check" in workflow_text
    assert "GITHUB_STEP_SUMMARY" in workflow_text
    assert "Upload verification artifact" in workflow_text


def test_cli_init_non_interactive_supports_plugins_root(tmp_path: Path, capsys) -> None:
    from neko_plugin_cli.cli import main

    plugins_root = tmp_path / "plugins"
    exit_code = main(
        [
            "init",
            "cli_init_demo",
            "--no-interactive",
            "--plugins-root",
            str(plugins_root),
            "--name",
            "CLI Init Demo",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[OK]" in captured.out
    plugin_dir = plugins_root / "cli_init_demo"
    assert (plugin_dir / "plugin.toml").is_file()
    assert (plugin_dir / "README.md").is_file()
    assert (plugin_dir / "tests" / "test_smoke.py").is_file()
    assert (plugin_dir / ".vscode" / "tasks.json").is_file()


def test_cli_init_remote_requires_git(tmp_path: Path, capsys) -> None:
    from neko_plugin_cli.cli import main

    exit_code = main(
        [
            "init",
            "remote_without_git",
            "--no-interactive",
            "--plugins-root",
            str(tmp_path / "plugins"),
            "--remote",
            "git@example.com:demo/remote_without_git.git",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--remote requires --git" in captured.err


def test_cli_init_can_initialize_git_repository(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    from neko_plugin_cli.cli import main

    plugins_root = tmp_path / "plugins"
    exit_code = main(
        [
            "init",
            "git_demo",
            "--no-interactive",
            "--plugins-root",
            str(plugins_root),
            "--git",
            "--remote",
            "git@example.com:demo/git_demo.git",
        ]
    )

    assert exit_code == 0
    plugin_dir = plugins_root / "git_demo"
    assert (plugin_dir / ".git").is_dir()


def test_cli_init_repo_is_one_click_repo_scaffold(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    from neko_plugin_cli.cli import main

    plugins_root = tmp_path / "plugins"
    exit_code = main(
        [
            "init-repo",
            "repo_demo",
            "--plugins-root",
            str(plugins_root),
            "--name",
            "Repo Demo",
            "--remote",
            "git@example.com:demo/repo_demo.git",
            "--neko-repo",
            "neko-org/N.E.K.O",
            "--neko-ref",
            "v1.0.0",
        ]
    )

    assert exit_code == 0
    plugin_dir = plugins_root / "repo_demo"
    assert (plugin_dir / ".git").is_dir()
    assert (plugin_dir / "README.md").is_file()
    assert (plugin_dir / ".vscode" / "tasks.json").is_file()
    assert (plugin_dir / ".github" / "workflows" / "verify.yml").is_file()
    workflow_text = (plugin_dir / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "NEKO_REPOSITORY: neko-org/N.E.K.O" in workflow_text
    assert "NEKO_REF: v1.0.0" in workflow_text


def test_cli_init_repo_supports_no_git_and_no_actions(tmp_path: Path) -> None:
    from neko_plugin_cli.cli import main

    plugins_root = tmp_path / "plugins"
    exit_code = main(
        [
            "init-repo",
            "repo_no_git",
            "--plugins-root",
            str(plugins_root),
            "--no-git",
            "--no-github-actions",
        ]
    )

    assert exit_code == 0
    plugin_dir = plugins_root / "repo_no_git"
    assert not (plugin_dir / ".git").exists()
    assert not (plugin_dir / ".github" / "workflows" / "verify.yml").exists()
    assert (plugin_dir / ".vscode" / "tasks.json").is_file()


def test_cli_init_repo_rejects_remote_without_git(tmp_path: Path, capsys) -> None:
    from neko_plugin_cli.cli import main

    exit_code = main(
        [
            "init-repo",
            "repo_bad_remote",
            "--plugins-root",
            str(tmp_path / "plugins"),
            "--no-git",
            "--remote",
            "git@example.com:demo/repo_bad_remote.git",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--remote requires git initialization" in captured.err


def test_cli_setup_repo_adds_support_files_to_existing_plugin(tmp_path: Path) -> None:
    from neko_plugin_cli.cli import main

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "existing_life"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "existing_life"',
                'name = "Existing Life"',
                'description = "Existing plugin"',
                'version = "0.2.0"',
                'entry = "plugin.plugins.existing_life:ExistingLifePlugin"',
                "",
                "[plugin.author]",
                'name = "Tester"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("ORIGINAL = True\n", encoding="utf-8")

    exit_code = main(
        [
            "setup-repo",
            "existing_life",
            "--plugins-root",
            str(plugins_root),
            "--github-actions",
            "--neko-repo",
            "neko-org/N.E.K.O",
            "--neko-ref",
            "v1",
        ]
    )

    assert exit_code == 0
    assert (plugin_dir / "__init__.py").read_text(encoding="utf-8") == "ORIGINAL = True\n"
    assert (plugin_dir / "README.md").is_file()
    assert (plugin_dir / ".vscode" / "tasks.json").is_file()
    workflow_text = (plugin_dir / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "PLUGIN_ID: existing_life" in workflow_text
    assert "NEKO_REF: v1" in workflow_text
    smoke_text = (plugin_dir / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    assert 'entry = "plugin.plugins.existing_life:ExistingLifePlugin"' in smoke_text


def test_cli_setup_repo_skips_existing_support_files_without_overwrite(tmp_path: Path) -> None:
    from neko_plugin_cli.cli import main

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "existing_skip"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nid = "existing_skip"\nname = "Existing Skip"\nversion = "0.1.0"\nentry = "plugin.plugins.existing_skip:ExistingSkipPlugin"\n',
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text("custom\n", encoding="utf-8")

    exit_code = main(["setup-repo", "existing_skip", "--plugins-root", str(plugins_root)])

    assert exit_code == 0
    assert (plugin_dir / "README.md").read_text(encoding="utf-8") == "custom\n"


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


def test_non_interactive_extension_requires_interactive_host_id(tmp_path: Path, capsys) -> None:
    from argparse import Namespace

    from neko_plugin_cli.commands.init_cmd import _handle_non_interactive
    from neko_plugin_cli.paths import CliDefaults

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    defaults = CliDefaults(
        plugin_root=tmp_path,
        target_dir=tmp_path / "target",
        plugins_root=plugins_root,
        profiles_root=tmp_path / "profiles",
    )

    exit_code = _handle_non_interactive(
        Namespace(plugin_id="demo_ext", plugin_type="extension", name="Demo Extension"),
        defaults=defaults,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "requires interactive setup" in captured.err


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
