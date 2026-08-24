from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.public import build_bundle, build_plugin
from plugin.server.application.plugin_cli.install_plan import build_install_plan, confirmation_token

pytestmark = pytest.mark.plugin_unit


def _write_plugin(
    root: Path,
    plugin_id: str,
    version: str,
    previous_ids: tuple[str, ...] = (),
) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    previous_line = f"previous_ids = {json.dumps(list(previous_ids))}\n" if previous_ids else ""
    (plugin_dir / "plugin.toml").write_text(
        "".join(
            [
                "[plugin]\n",
                f'id = "{plugin_id}"\n',
                f'name = "{plugin_id}"\n',
                f'version = "{version}"\n',
                'type = "plugin"\n',
                previous_line,
                f"\n[{plugin_id}]\n",
                "enabled = true\n",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return plugin_dir


def _single_package(tmp_path: Path, plugin_id: str, version: str = "2.0.0") -> Path:
    package_path = tmp_path / f"{plugin_id}-{version}.neko-plugin"
    build_plugin(_write_plugin(tmp_path / "source", plugin_id, version), package_path)
    return package_path


def test_plan_marks_new_single_plugin_as_install(tmp_path: Path) -> None:
    plan = build_install_plan(
        package_path=_single_package(tmp_path, "demo"),
        plugins_root=tmp_path / "plugins",
    )

    assert plan.action == "install"
    assert plan.plugin_id == "demo"
    assert plan.confirmation_token == ""


def test_plan_marks_matching_existing_plugin_as_upgrade(tmp_path: Path) -> None:
    package = _single_package(tmp_path, "demo", version="2.0.0")
    _write_plugin(tmp_path / "plugins", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(package_path=package, plugins_root=tmp_path / "plugins")

    assert plan.action == "upgrade"
    assert plan.current_version == "1.0.0"
    assert plan.target_version == "2.0.0"
    assert len(plan.confirmation_token) == 64


def test_plan_blocks_bundle_with_any_existing_plugin(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    package_path = tmp_path / "demo-bundle.neko-bundle"
    build_bundle(
        [_write_plugin(source_root, "demo", "2.0.0"), _write_plugin(source_root, "other", "2.0.0")],
        package_path,
        bundle_id="demo_bundle",
        package_name="Demo Bundle",
        version="2.0.0",
    )
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")

    plan = build_install_plan(package_path=package_path, plugins_root=plugins_root)

    assert plan.action == "blocked"
    assert plan.reason == "bundle_conflict"


def test_plan_blocks_an_installed_declared_previous_id(tmp_path: Path) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "neko_live",
        "1.0.0",
        previous_ids=("neko_roast",),
    )
    package_path = tmp_path / "neko-live.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "neko_roast", "0.1.0")

    plan = build_install_plan(package_path=package_path, plugins_root=plugins_root)

    assert plan.action == "blocked"
    assert plan.reason == "legacy_plugin_present"
    assert plan.legacy_plugin_ids == ("neko_roast",)


def test_confirmation_token_streams_package_instead_of_reading_it_all_at_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_path = tmp_path / "demo.neko-plugin"
    package_bytes = b"package-content" * 1024
    package_path.write_bytes(package_bytes)
    target_dir = _write_plugin(tmp_path / "plugins", "demo", "1.0.0")
    manifest_bytes = (target_dir / "plugin.toml").read_bytes()
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == package_path:
            raise AssertionError("package hash must use bounded reads")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    expected = hashlib.sha256()
    expected.update(package_bytes)
    expected.update(b"\0")
    expected.update(str(target_dir.resolve()).encode("utf-8"))
    expected.update(b"\0")
    expected.update(manifest_bytes)

    assert confirmation_token(package_path=package_path, target_dir=target_dir) == expected.hexdigest()
