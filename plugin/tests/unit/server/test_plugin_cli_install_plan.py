from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from plugin.neko_plugin_cli.public import build_bundle, build_plugin
from plugin.server.application.plugin_cli.install_plan import build_install_plan, confirmation_token

pytestmark = pytest.mark.plugin_unit


def _write_plugin(
    root: Path,
    plugin_id: str,
    version: str,
    previous_ids: tuple[str, ...] = (),
    entry: str | None = None,
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
                f'entry = "{entry or f"plugin.plugins.{plugin_id}:Plugin"}"\n',
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


def test_plan_blocks_direct_replacement_of_existing_builtin_override(tmp_path: Path) -> None:
    package = _single_package(tmp_path, "demo", version="2.0.0")
    _write_plugin(tmp_path / "plugins", plugin_id="demo", version="1.5.0")
    _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "plugins",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "blocked"
    assert plan.reason == "plugin_builtin_override_market_required"
    assert plan.confirmation_token == ""


@pytest.mark.parametrize(
    ("current_version", "target_version", "expected_action"),
    [
        ("1.1.0", "1.1.0", "reinstall"),
        ("2.0.0", "0.9.0", "downgrade"),
        ("1.0.0rc1", "1.0.0", "upgrade"),
        ("nightly", "nightly", "reinstall"),
        ("nightly-a", "nightly-b", "upgrade"),
    ],
)
def test_plan_classifies_plugin_replacements_by_version(
    tmp_path: Path,
    current_version: str,
    target_version: str,
    expected_action: str,
) -> None:
    package = _single_package(tmp_path, "demo", version=target_version)
    _write_plugin(tmp_path / "plugins", plugin_id="demo", version=current_version)

    plan = build_install_plan(package_path=package, plugins_root=tmp_path / "plugins")

    assert plan.action == expected_action
    assert plan.current_version == current_version
    assert plan.target_version == target_version
    assert len(plan.confirmation_token) == 64


def test_plan_allows_exact_single_plugin_to_override_builtin(tmp_path: Path) -> None:
    package = _single_package(tmp_path, "demo", version="2.0.0")
    builtin = _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "override_builtin"
    assert plan.plugin_id == "demo"
    assert plan.directory_name == "demo"
    assert plan.current_source == "builtin"
    assert plan.target_source == "market"
    assert plan.current_version == "1.0.0"
    assert plan.target_version == "2.0.0"
    assert len(plan.confirmation_token) == 64
    assert builtin.is_dir()


def test_manifestless_user_state_blocks_builtin_override_before_install(tmp_path: Path) -> None:
    package = _single_package(tmp_path, "demo", version="2.0.0")
    builtin = _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")
    state_dir = tmp_path / "user" / "demo"
    (state_dir / "data").mkdir(parents=True)
    sentinel = state_dir / "data" / "database.sqlite"
    sentinel.write_bytes(b"persistent-state")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "blocked"
    assert plan.reason == "override_manifestless_state_conflict"
    assert plan.confirmation_token == ""
    assert plan.target_version == "2.0.0"
    assert sentinel.read_bytes() == b"persistent-state"
    assert builtin.is_dir()


def test_plan_allows_runtime_user_entry_namespace_for_builtin_override(
    tmp_path: Path,
) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "demo",
        "2.0.0",
        entry="plugins.demo:Plugin",
    )
    package = tmp_path / "demo.neko-plugin"
    build_plugin(source, package)
    _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "override_builtin"
    assert plan.reason == ""


@pytest.mark.parametrize(
    "entry",
    ["demo:Plugin", "main:Plugin", "demo.main:Plugin"],
)
def test_plan_rejects_package_local_entries_for_builtin_override(
    tmp_path: Path,
    entry: str,
) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "demo",
        "2.0.0",
        entry=entry,
    )
    (source / "main.py").write_text("class Plugin: pass\n", encoding="utf-8")
    package = tmp_path / "demo.neko-plugin"
    build_plugin(source, package)
    _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "blocked"
    assert plan.reason == "override_entry_missing"
    assert plan.confirmation_token == ""


@pytest.mark.parametrize(
    "entry",
    [
        "plugins.demo.:Plugin",
        "plugins.demo..escape:Plugin",
        "demo...escape:Plugin",
    ],
)
def test_plan_rejects_malformed_supported_entry_namespace_paths(
    tmp_path: Path,
    entry: str,
) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "demo",
        "2.0.0",
        entry=entry,
    )
    package = tmp_path / "demo.neko-plugin"
    build_plugin(source, package)
    _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "blocked"
    assert plan.reason == "override_entry_missing"


def test_builtin_override_token_changes_with_builtin_manifest(tmp_path: Path) -> None:
    package = _single_package(tmp_path, "demo", version="2.0.0")
    builtin = _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")
    kwargs = {
        "package_path": package,
        "plugins_root": tmp_path / "user",
        "builtin_plugins_root": tmp_path / "builtin",
    }
    first = build_install_plan(**kwargs)

    manifest = builtin / "plugin.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('version = "1.0.0"', 'version = "1.0.1"'),
        encoding="utf-8",
    )
    second = build_install_plan(**kwargs)

    assert first.confirmation_token != second.confirmation_token
    assert second.current_version == "1.0.1"


def test_plan_blocks_builtin_override_with_previous_ids(tmp_path: Path) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "demo",
        "2.0.0",
        previous_ids=("old_demo",),
    )
    package = tmp_path / "demo.neko-plugin"
    build_plugin(source, package)
    _write_plugin(tmp_path / "builtin", plugin_id="demo", version="1.0.0")

    plan = build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=tmp_path / "builtin",
    )

    assert plan.action == "blocked"
    assert plan.reason == "override_previous_ids_not_supported"
    assert plan.confirmation_token == ""


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


def test_plan_blocks_bundle_that_contains_a_builtin_plugin(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    package_path = tmp_path / "builtin-bundle.neko-bundle"
    build_bundle(
        [
            _write_plugin(source_root, "study_companion", "0.1.6"),
            _write_plugin(source_root, "other", "2.0.0"),
        ],
        package_path,
        bundle_id="builtin_bundle",
        package_name="Builtin Bundle",
        version="2.0.0",
    )
    builtin_root = tmp_path / "builtin"
    _write_plugin(builtin_root, "study_companion", "0.1.5")

    plan = build_install_plan(
        package_path=package_path,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=builtin_root,
    )

    assert plan.action == "blocked"
    assert plan.reason == "bundle_conflict"
    assert plan.confirmation_token == ""


def test_plan_blocks_an_installed_declared_previous_id(tmp_path: Path) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "modern_demo",
        "1.0.0",
        previous_ids=("legacy_demo",),
    )
    package_path = tmp_path / "modern-demo.neko-plugin"
    build_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "legacy_demo", "0.1.0")

    plan = build_install_plan(package_path=package_path, plugins_root=plugins_root)

    assert plan.action == "blocked"
    assert plan.reason == "legacy_plugin_present"
    assert plan.legacy_plugin_ids == ("legacy_demo",)


def test_plan_blocks_a_builtin_declared_previous_id(tmp_path: Path) -> None:
    source = _write_plugin(
        tmp_path / "source",
        "modern_demo",
        "1.0.0",
        previous_ids=("legacy_demo",),
    )
    package_path = tmp_path / "modern-demo.neko-plugin"
    build_plugin(source, package_path)
    builtin_root = tmp_path / "builtin"
    _write_plugin(builtin_root, "legacy_demo", "0.1.0")

    plan = build_install_plan(
        package_path=package_path,
        plugins_root=tmp_path / "user",
        builtin_plugins_root=builtin_root,
    )

    assert plan.action == "blocked"
    assert plan.reason == "legacy_plugin_present"
    assert plan.legacy_plugin_ids == ("legacy_demo",)


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


def test_install_plan_streams_packaged_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _single_package(tmp_path, "streamed")

    def forbidden_read(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("packaged manifests must use bounded streaming reads")

    monkeypatch.setattr(zipfile.ZipFile, "read", forbidden_read)

    assert build_install_plan(
        package_path=package,
        plugins_root=tmp_path / "plugins",
    ).plugin_id == "streamed"
