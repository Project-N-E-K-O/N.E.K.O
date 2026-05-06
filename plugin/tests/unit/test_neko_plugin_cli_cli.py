from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.commands import init_cmd
from plugin.neko_plugin_cli.paths import CliDefaults

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
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
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


def test_cli_pack_inspect_verify_and_unpack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    target_dir = tmp_path / "target"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"

    exit_code = neko_plugin_cli.main(
        ["pack", str(plugin_dir), "--target-dir", str(target_dir)]
    )
    assert exit_code == 0
    package_path = target_dir / "cli_demo.neko-plugin"
    assert package_path.is_file()

    inspect_exit = neko_plugin_cli.main(["inspect", str(package_path)])
    assert inspect_exit == 0

    verify_exit = neko_plugin_cli.main(["verify", str(package_path)])
    assert verify_exit == 0

    unpack_exit = neko_plugin_cli.main(
        [
            "unpack",
            str(package_path),
            "--plugins-root",
            str(plugins_root),
            "--profiles-root",
            str(profiles_root),
            "--on-conflict",
            "fail",
        ]
    )
    assert unpack_exit == 0
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
    neko_plugin_cli.main(["pack", str(plugin_dir), "--out", str(package_path)])
    _tamper_package(package_path, "payload/profiles/default.toml")

    exit_code = neko_plugin_cli.main(["verify", str(package_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "payload_hash_verified=False" in captured.out


def test_cli_pack_bundle_and_inspect(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_cli_one")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_cli_two")
    target_dir = tmp_path / "target"

    exit_code = neko_plugin_cli.main(
        [
            "pack",
            str(first_plugin),
            str(second_plugin),
            "--bundle",
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


def test_interactive_extension_cannot_skip_host_prompt_with_quick_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    def fake_ask_confirm(message: str, *, default: bool = True) -> bool:
        assert not message.startswith("快速开始")
        return True

    text_answers = iter(["", "", "host_plugin", "/extra"])
    monkeypatch.setattr(init_cmd, "ask_confirm", fake_ask_confirm)
    monkeypatch.setattr(init_cmd, "ask_text", lambda *_, **__: next(text_answers))
    monkeypatch.setattr(init_cmd, "ask_checkbox", lambda *_, **__: ["lifecycle", "entry_point"])

    assert init_cmd._handle_interactive(args, defaults=defaults) == 0

    plugin_toml = (defaults.plugins_root / "demo_ext" / "plugin.toml").read_text(encoding="utf-8")
    assert "[plugin.host]" in plugin_toml
    assert 'plugin_id = "host_plugin"' in plugin_toml
