from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import init_cmd
from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir
from plugin.neko_plugin_cli.paths import CliDefaults
from plugin.neko_plugin_cli.templates.generator import PluginSpec, generate_plugin

pytestmark = pytest.mark.plugin_unit


def _make_plugin_dir(tmp_path: Path, plugin_id: str = "cli_demo") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "CLI Demo"',
                'version = "0.0.1"',
                'type = "plugin"',
                f'entry = "plugin.plugins.{plugin_id}:DemoPlugin"',
                "",
                "[plugin.sdk]",
                'recommended = ">=0.1.0,<0.2.0"',
                'supported = ">=0.1.0,<0.3.0"',
                "",
                "[plugin_runtime]",
                "auto_start = false",
                "",
                f"[{plugin_id}]",
                'token = "demo"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from plugin.sdk.plugin import neko_plugin\n\n"
        "@neko_plugin\n"
        "class DemoPlugin: pass\n",
        encoding="utf-8",
    )
    return plugin_dir


def _tamper_package(package_path: Path, target_name: str) -> None:
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(package_path) as src:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == target_name:
                data += b"\n# tampered\n"
            entries.append((info, data))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info, data in entries:
            dst.writestr(info, data)


def test_cli_build_inspect_verify_and_install(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    target_dir = tmp_path / "target"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"

    exit_code = neko_plugin_cli.main(
        ["build", str(plugin_dir), "-t", str(target_dir)]
    )
    assert exit_code == 0
    package_path = target_dir / "cli_demo.neko-plugin"
    assert package_path.is_file()

    inspect_exit = neko_plugin_cli.main(["inspect", str(package_path)])
    assert inspect_exit == 0

    verify_exit = neko_plugin_cli.main(["verify", str(package_path)])
    assert verify_exit == 0

    install_exit = neko_plugin_cli.main(
        [
            "install",
            str(package_path),
            "--plugins-root",
            str(plugins_root),
            "--profiles-root",
            str(profiles_root),
            "--on-conflict",
            "fail",
        ]
    )
    assert install_exit == 0
    assert (plugins_root / "cli_demo" / "plugin.toml").is_file()
    assert (profiles_root / "cli_demo" / "default.toml").is_file()

    captured = capsys.readouterr()
    assert "[OK] cli_demo" in captured.out
    assert "payload_hash_verified=True" in captured.out


def test_cli_verify_fails_when_package_hash_is_tampered(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "cli_demo.neko-plugin"
    neko_plugin_cli.main(["build", str(plugin_dir), "-o", str(package_path)])
    _tamper_package(package_path, "payload/profiles/default.toml")

    exit_code = neko_plugin_cli.main(["verify", str(package_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "payload_hash_verified=False" in captured.out


def test_cli_build_bundle_and_inspect(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_cli_one")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_cli_two")
    target_dir = tmp_path / "target"

    exit_code = neko_plugin_cli.main(
        [
            "build",
            str(first_plugin),
            str(second_plugin),
            "-b",
            "--bundle-id",
            "bundle_cli_demo",
            "--target-dir",
            str(target_dir),
        ]
    )
    assert exit_code == 0

    package_path = target_dir / "bundle_cli_demo.neko-bundle"
    assert package_path.is_file()

    inspect_exit = neko_plugin_cli.main(["inspect", str(package_path)])
    assert inspect_exit == 0

    captured = capsys.readouterr()
    assert "package_type=bundle" in captured.out
    assert "plugin_count=2" in captured.out
    assert "type=bundle" in captured.out


def test_cli_build_multiple_plugins_without_bundle_builds_individual_packages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="multi_one")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="multi_two")
    target_dir = tmp_path / "target"

    exit_code = neko_plugin_cli.main(["build", str(first_plugin), str(second_plugin), "-t", str(target_dir)])

    assert exit_code == 0
    assert (target_dir / "multi_one.neko-plugin").is_file()
    assert (target_dir / "multi_two.neko-plugin").is_file()
    assert not list(target_dir.glob("*.neko-bundle"))
    captured = capsys.readouterr()
    assert "Completed: built=2, failed=0" in captured.out


def test_cli_build_out_does_not_create_unused_target_dir(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "cli_demo.neko-plugin"
    unused_target = tmp_path / "unused-target"

    exit_code = neko_plugin_cli.main(["build", str(plugin_dir), "-o", str(package_path), "-t", str(unused_target)])

    assert exit_code == 0
    assert package_path.is_file()
    assert not unused_target.exists()


def test_cli_check_uses_new_label(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)

    exit_code = neko_plugin_cli.main(["check", str(plugin_dir)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[OK] cli_demo: check found" in captured.out


def test_cli_check_release_uses_release_check_flow(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)

    exit_code = neko_plugin_cli.main(["check", str(plugin_dir), "--release", "--skip-tests"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "check --release blocked by validation errors" in captured.err


@pytest.mark.parametrize("legacy_command", ["doctor", "release-check", "validate", "pack", "unpack"])
def test_cli_legacy_commands_are_removed(
    legacy_command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main([legacy_command, "cli_demo"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert f"invalid choice: '{legacy_command}'" in captured.err


def test_validate_plugin_dir_reports_invalid_toml_without_crashing(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "bad_toml"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text("[plugin\n", encoding="utf-8")

    issues = validate_plugin_dir(plugin_dir)

    assert any(level == "error" and "plugin.toml could not be read" in message for level, message in issues)


def test_validate_plugin_dir_reports_invalid_utf8_optional_files(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / ".vscode").mkdir()
    (plugin_dir / ".vscode" / "settings.json").write_bytes(b"\xff")
    (plugin_dir / ".gitignore").write_bytes(b"\xff")

    issues = validate_plugin_dir(plugin_dir, strict=False)
    messages = [message for _level, message in issues]

    assert any(".vscode/settings.json is not valid UTF-8" in message for message in messages)
    assert any(".gitignore is not valid UTF-8" in message for message in messages)


def test_validate_plugin_dir_accepts_previous_plugin_ids(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    manifest_path = plugin_dir / "plugin.toml"
    manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        manifest.replace('name = "CLI Demo"', 'name = "CLI Demo"\nprevious_ids = ["legacy_cli_demo"]'),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir, strict=False)

    assert not any("previous_ids is not a recognized" in message for _level, message in issues)


@pytest.mark.parametrize("removed_type", ["script", "extension"])
def test_validate_plugin_dir_rejects_removed_plugin_types(
    tmp_path: Path,
    removed_type: str,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    plugin_toml_path = plugin_dir / "plugin.toml"
    plugin_toml_path.write_text(
        plugin_toml_path.read_text(encoding="utf-8").replace(
            'type = "plugin"',
            f'type = "{removed_type}"',
        ),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    assert any(
        level == "error" and "[plugin].type must be one of" in message
        for level, message in issues
    )



def test_validate_plugin_dir_warns_for_each_literal_push_message_v1_keyword(
    tmp_path: Path,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        """from plugin.sdk.plugin import neko_plugin

@neko_plugin
class DemoPlugin:
    def emit(self):
        self.ctx.push_message(
            message_type="proactive_notification",
            description="label",
            content="payload",
            binary_data=b"image",
            binary_url="https://example.test/image.png",
            mime="image/png",
            unsafe=True,
            fast_mode=True,
            delivery="proactive",
            reply=True,
        )
""",
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    warnings = [
        message
        for level, message in issues
        if level == "warning" and "push_message v1 keyword" in message
    ]
    expected_fields = {
        "message_type",
        "description",
        "content",
        "binary_data",
        "binary_url",
        "mime",
        "unsafe",
        "fast_mode",
        "delivery",
        "reply",
    }
    assert len(warnings) == len(expected_fields)
    for field in expected_fields:
        warning = next(message for message in warnings if f"'{field}'" in message)
        path, line, detail = warning.split(":", 2)
        assert path == "__init__.py"
        assert line.isdigit()
        assert "migrate to" in detail
        assert "before removal in v0.9" in detail
        assert "已弃用" in detail
        assert "非推奨" in detail


def test_validate_plugin_dir_warns_for_existing_style_push_message_call(
    tmp_path: Path,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        """from plugin.sdk.plugin import neko_plugin

@neko_plugin
class DemoPlugin:
    def emit(self):
        self.ctx.push_message(source="demo", message_type="text", content="payload")
""",
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    warnings = [
        message
        for level, message in issues
        if level == "warning" and "push_message v1 keyword" in message
    ]
    assert len(warnings) == 2
    assert all(message.startswith("__init__.py:6:") for message in warnings)
    assert any("'message_type'" in message for message in warnings)
    assert any("'content'" in message for message in warnings)


def test_validate_plugin_dir_ignores_push_message_v2_and_inactive_legacy_defaults(
    tmp_path: Path,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        """from plugin.sdk.plugin import neko_plugin

def push_message(*, visibility, ai_behavior, parts):
    return visibility, ai_behavior, parts

@neko_plugin
class DemoPlugin:
    def emit(self):
        push_message(visibility=["chat"], ai_behavior="blind", parts=[])
        self.ctx.push_message(
            visibility=["chat"],
            ai_behavior="blind",
            parts=[{"type": "text", "text": "payload"}],
            content=None,
            unsafe=False,
            fast_mode=False,
        )
""",
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    assert not any("push_message v1 keyword" in message for _level, message in issues)


def test_validate_plugin_dir_warns_for_dynamic_legacy_boolean_flags(
    tmp_path: Path,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        """from plugin.sdk.plugin import neko_plugin

@neko_plugin
class DemoPlugin:
    def emit(self, enabled):
        self.ctx.push_message(parts=[], unsafe=enabled, fast_mode=enabled)
""",
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    warnings = [
        message
        for level, message in issues
        if level == "warning" and "push_message v1 keyword" in message
    ]
    assert len(warnings) == 2
    assert any("'unsafe'" in message for message in warnings)
    assert any("'fast_mode'" in message for message in warnings)


def test_validate_plugin_dir_ignores_unrelated_local_push_message_function(
    tmp_path: Path,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        """def push_message(*, content):
    return content

push_message(content="not an SDK call")
""",
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    assert not any("push_message v1 keyword" in message for _level, message in issues)


def test_validate_plugin_dir_warns_for_legacy_positional_push_message_fields(
    tmp_path: Path,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        """def emit(ctx):
    ctx.push_message(
        "demo", "text", "label", 5, "payload", None, None, {}, True, True
    )
""",
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir)

    warnings = [
        message
        for level, message in issues
        if level == "warning" and "push_message v1 keyword" in message
    ]
    assert len(warnings) == 5
    for field in ("message_type", "description", "content", "unsafe", "fast_mode"):
        assert any(f"'{field}'" in message for message in warnings)


@pytest.mark.parametrize("unsupported_scaffold_type", ["script", "extension"])
def test_init_rejects_removed_or_deprecated_scaffold_types(
    unsupported_scaffold_type: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        neko_plugin_cli.main(["init", "demo", "--type", unsupported_scaffold_type])

    assert exc_info.value.code == 2
    assert f"invalid choice: '{unsupported_scaffold_type}'" in capsys.readouterr().err


@pytest.mark.parametrize("unsupported_scaffold_type", ["script", "extension"])
def test_generator_rejects_removed_or_deprecated_scaffold_types(
    tmp_path: Path,
    unsupported_scaffold_type: str,
) -> None:
    target_dir = tmp_path / unsupported_scaffold_type

    with pytest.raises(ValueError) as exc_info:
        generate_plugin(
            PluginSpec(
                plugin_id="demo_plugin",
                plugin_type=unsupported_scaffold_type,
            ),
            target_dir,
        )

    message = str(exc_info.value)
    assert "不支持" in message and "not supported" in message and "サポートされていません" in message
    assert not target_dir.exists()


def test_validate_plugin_dir_accepts_startup_failure_policy(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    plugin_toml_path = plugin_dir / "plugin.toml"
    plugin_toml_path.write_text(
        plugin_toml_path.read_text(encoding="utf-8").replace(
            "auto_start = false",
            "auto_start = false\nstartup_failure = \"warn\"\ntimeout = 1.5",
        ),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir, strict=True)
    messages = [message for _level, message in issues]

    assert not any("[plugin_runtime].startup_failure is not a recognized" in message for message in messages)
    assert not any(level == "error" and "startup_failure" in message for level, message in issues)


def test_validate_plugin_dir_rejects_invalid_startup_failure_policy(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    plugin_toml_path = plugin_dir / "plugin.toml"
    plugin_toml_path.write_text(
        plugin_toml_path.read_text(encoding="utf-8").replace(
            "auto_start = false",
            "auto_start = false\nstartup_failure = \"strict\"",
        ),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir, strict=True)

    assert any(
        level == "error" and "[plugin_runtime].startup_failure must be one of" in message
        for level, message in issues
    )


def test_validate_plugin_dir_rejects_non_positive_runtime_timeout(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    plugin_toml_path = plugin_dir / "plugin.toml"
    plugin_toml_path.write_text(
        plugin_toml_path.read_text(encoding="utf-8").replace(
            "auto_start = false",
            "auto_start = false\ntimeout = 0",
        ),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir, strict=True)

    assert any(
        level == "error" and "[plugin_runtime].timeout must be > 0" in message
        for level, message in issues
    )


def test_validate_plugin_dir_rejects_too_large_runtime_timeout(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    plugin_toml_path = plugin_dir / "plugin.toml"
    plugin_toml_path.write_text(
        plugin_toml_path.read_text(encoding="utf-8").replace(
            "auto_start = false",
            "auto_start = false\ntimeout = 300.1",
        ),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir, strict=True)

    assert any(
        level == "error" and "[plugin_runtime].timeout must be <= 300" in message
        for level, message in issues
    )


@pytest.mark.parametrize("timeout_literal", ["nan", "inf", "-inf"])
def test_validate_plugin_dir_rejects_non_finite_runtime_timeout(
    tmp_path: Path,
    timeout_literal: str,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    plugin_toml_path = plugin_dir / "plugin.toml"
    plugin_toml_path.write_text(
        plugin_toml_path.read_text(encoding="utf-8").replace(
            "auto_start = false",
            f"auto_start = false\ntimeout = {timeout_literal}",
        ),
        encoding="utf-8",
    )

    issues = validate_plugin_dir(plugin_dir, strict=True)

    assert any(
        level == "error" and "[plugin_runtime].timeout must be finite" in message
        for level, message in issues
    )


def test_init_repo_uses_market_repository_name_and_keeps_plugin_id(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = neko_plugin_cli.main(
        [
            "init-repo",
            "market_demo",
            "--plugins-root",
            str(tmp_path),
            "--no-git",
            "--neko-repo",
            "Project-N-E-K-O/N.E.K.O",
        ]
    )

    repo_dir = tmp_path / "n.e.k.o_plugin_market_demo"
    assert exit_code == 0
    assert repo_dir.is_dir()
    assert not (tmp_path / "market_demo").exists()
    plugin_toml_text = (repo_dir / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "market_demo"' in plugin_toml_text
    assert 'entry = "plugin.plugins.market_demo:MarketDemoPlugin"' in plugin_toml_text
    assert "store.db" in (repo_dir / ".gitignore").read_text(encoding="utf-8")
    assert (repo_dir / ".github" / "workflows" / "verify.yml").is_file()
    release_workflow = repo_dir / ".github" / "workflows" / "release.yml"
    assert release_workflow.is_file()
    release_workflow_text = release_workflow.read_text(encoding="utf-8")
    assert "softprops/action-gh-release" in release_workflow_text
    assert "set -o pipefail" in release_workflow_text
    assert "fail_on_unmatched_files: true" in release_workflow_text

    messages = [message for _level, message in validate_plugin_dir(repo_dir, strict=True)]
    assert not any("does not match directory name" in message for message in messages)
    assert not any("plugin.entry should usually start with" in message for message in messages)

    check_exit = neko_plugin_cli.main(["check", "market_demo", "--plugins-root", str(tmp_path)])
    assert check_exit == 0
    captured = capsys.readouterr()
    assert "repo:   n.e.k.o_plugin_market_demo" in captured.out
    assert "[OK] market_demo: check found" in captured.out


def test_market_release_check_enforces_repo_and_tag_conventions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        neko_plugin_cli.main(
            [
                "init-repo",
                "market_demo",
                "--plugins-root",
                str(tmp_path),
                "--no-git",
                "--neko-repo",
                "Project-N-E-K-O/N.E.K.O",
            ]
        )
        == 0
    )

    monkeypatch.setenv("GITHUB_REPOSITORY", "alice/n.e.k.o_plugin_market_demo")
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.1.0")
    assert (
        neko_plugin_cli.main(
            [
                "check",
                "market_demo",
                "--plugins-root",
                str(tmp_path),
                "--release",
                "--market-release",
                "--skip-tests",
                "--target-dir",
                str(tmp_path / "target"),
            ]
        )
        == 0
    )

    monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9")
    assert (
        neko_plugin_cli.main(
            [
                "check",
                "market_demo",
                "--plugins-root",
                str(tmp_path),
                "--release",
                "--market-release",
                "--skip-tests",
                "--target-dir",
                str(tmp_path / "target-bad"),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "release tag v9.9.9 does not match plugin.toml version 0.1.0" in captured.err


def test_init_repo_rejects_uppercase_market_plugin_id(tmp_path: Path) -> None:
    exit_code = neko_plugin_cli.main(
        [
            "init-repo",
            "MarketDemo",
            "--plugins-root",
            str(tmp_path),
            "--no-git",
        ]
    )

    assert exit_code == 1
    assert not (tmp_path / "n.e.k.o_plugin_MarketDemo").exists()


def test_setup_repo_git_skips_when_inside_existing_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path / "repo")
    (tmp_path / "repo" / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_run_git(command: list[str], *, cwd: Path) -> None:
        calls.append(command)

    monkeypatch.setattr(init_cmd, "_run_git", fake_run_git)

    assert init_cmd._initialize_git_repo(plugin_dir) is False

    assert calls == []


def test_git_remote_requires_new_repository(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path / "repo")
    (tmp_path / "repo" / ".git").mkdir()

    with pytest.raises(RuntimeError, match="--remote"):
        init_cmd._initialize_git_repo(plugin_dir, remote="https://example.invalid/demo.git")


def test_git_preflight_remote_fails_before_writing_files(tmp_path: Path) -> None:
    target_dir = tmp_path / "repo" / "demo_plugin"
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="--remote"):
        init_cmd._preflight_git_request(
            target_dir,
            initialize_git=True,
            remote="https://example.invalid/demo.git",
        )

    assert not target_dir.exists()


def test_git_preflight_skips_git_binary_check_inside_existing_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "repo" / "demo_plugin"
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    monkeypatch.setattr(init_cmd.shutil, "which", lambda _: None)

    init_cmd._preflight_git_request(target_dir, initialize_git=True)


def test_interactive_handler_rejects_removed_extension_when_called_directly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    defaults = CliDefaults(
        plugin_root=tmp_path / "plugin",
        target_dir=tmp_path / "target",
        plugins_root=tmp_path / "plugins",
        profiles_root=tmp_path / "profiles",
    )
    args = argparse.Namespace(
        plugin_id="demo_ext",
        plugin_type="extension",
        name="Demo Extension",
        plugins_root=None,
        git=False,
        remote=None,
        github_actions=False,
        neko_repo="owner/N.E.K.O",
        neko_ref="main",
        no_readme=True,
        no_tests=True,
        no_gitignore=True,
        no_vscode=True,
    )

    assert init_cmd._handle_interactive(args, defaults=defaults) == 1
    assert not (defaults.plugins_root / "demo_ext").exists()
    error = capsys.readouterr().err
    assert "extension" in error
    assert "不支持" in error and "not supported" in error and "サポートされていません" in error
