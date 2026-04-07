from __future__ import annotations

from pathlib import Path
import sys
import zipfile

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

import cli as neko_plugin_cli

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
