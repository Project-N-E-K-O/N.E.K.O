from __future__ import annotations

from pathlib import Path
import sys
import zipfile

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from public import inspect_package, pack_bundle, pack_plugin, unpack_package
from public.pack_rules import PackRuleSet, should_skip_path

pytestmark = pytest.mark.plugin_unit


def _make_plugin_dir(tmp_path: Path, plugin_id: str = "demo_plugin") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Demo Plugin"',
                'description = "A plugin used by unit tests."',
                'version = "1.2.3"',
                'type = "plugin"',
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = true",
                "",
                f"[{plugin_id}]",
                'token = "secret-token"',
                "retry = 3",
                "",
                "[extra_table]",
                'ignored = "yes"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (plugin_dir / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo-plugin"',
                'version = "1.2.3"',
                'dependencies = ["httpx>=0.27", "pydantic>=2.0"]',
                "",
                "[tool.neko.pack]",
                'exclude = ["*.tmp"]',
                'exclude_dirs = ["cache_dir"]',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (plugin_dir / "__init__.py").write_text('PLUGIN_NAME = "demo"\n', encoding="utf-8")
    (plugin_dir / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    (plugin_dir / "debug.tmp").write_text("skip me\n", encoding="utf-8")
    (plugin_dir / "cache_dir").mkdir()
    (plugin_dir / "cache_dir" / "cache.txt").write_text("skip dir\n", encoding="utf-8")
    (plugin_dir / "__pycache__").mkdir()
    (plugin_dir / "__pycache__" / "module.pyc").write_bytes(b"pyc")
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


def _rewrite_package_without_member(package_path: Path, member_name: str) -> None:
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(package_path) as src:
        for info in src.infolist():
            if info.filename == member_name:
                continue
            entries.append((info, src.read(info.filename)))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info, data in entries:
            dst.writestr(info, data)


def test_pack_rules_apply_include_and_exclude() -> None:
    rules = PackRuleSet(
        include=["src/*.py", "plugin.toml"],
        exclude=["*.tmp"],
        exclude_dirs=["cache_dir"],
        exclude_files=["secret.txt"],
    )

    assert should_skip_path(Path("src/main.py"), is_dir=False, rules=rules) is False
    assert should_skip_path(Path("plugin.toml"), is_dir=False, rules=rules) is False
    assert should_skip_path(Path("notes.tmp"), is_dir=False, rules=rules) is True
    assert should_skip_path(Path("cache_dir"), is_dir=True, rules=rules) is True
    assert should_skip_path(Path("secret.txt"), is_dir=False, rules=rules) is True
    assert should_skip_path(Path("README.md"), is_dir=False, rules=rules) is True


def test_pack_plugin_writes_expected_profile_and_skips_runtime_artifacts(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"

    result = pack_plugin(plugin_dir, package_path)

    assert result.plugin_id == "demo_plugin"
    assert result.package_path == package_path.resolve()
    assert result.staging_dir is None
    assert result.packaged_file_count == 0
    assert result.profile_file_count == 0

    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "payload/plugins/demo_plugin/plugin.toml" in names
        assert "payload/plugins/demo_plugin/runtime.txt" in names
        assert "payload/plugins/demo_plugin/debug.tmp" not in names
        assert "payload/plugins/demo_plugin/cache_dir/cache.txt" not in names
        assert "payload/plugins/demo_plugin/__pycache__/module.pyc" not in names

        profile_text = archive.read("payload/profiles/default.toml").decode("utf-8")
        assert 'enabled_plugins = ["demo_plugin"]' in profile_text
        assert "auto_start = true" in profile_text
        assert 'token = "secret-token"' in profile_text
        assert "retry = 3" in profile_text
        assert "extra_table" not in profile_text


def test_inspect_package_reports_metadata_and_profiles(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)

    result = inspect_package(package_path)

    assert result.package_type == "plugin"
    assert result.package_id == "demo_plugin"
    assert result.package_name == "Demo Plugin"
    assert result.version == "1.2.3"
    assert result.metadata_found is True
    assert result.payload_hash_verified is True
    assert result.plugin_count == 1
    assert result.profile_names == ["default.toml"]
    assert result.plugins[0].plugin_id == "demo_plugin"


def test_unpack_package_supports_rename_and_fail_conflict_modes(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    pack_plugin(plugin_dir, package_path)

    first = unpack_package(
        package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        on_conflict="rename",
    )
    second = unpack_package(
        package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        on_conflict="rename",
    )

    assert first.unpacked_plugins[0].target_plugin_id == "demo_plugin"
    assert first.unpacked_plugins[0].renamed is False
    assert second.unpacked_plugins[0].target_plugin_id == "demo_plugin_1"
    assert second.unpacked_plugins[0].renamed is True

    with pytest.raises(FileExistsError):
        unpack_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
            on_conflict="fail",
        )


def test_unpack_package_rejects_payload_hash_mismatch(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    _tamper_package(package_path, "payload/profiles/default.toml")

    with pytest.raises(ValueError, match="payload hash mismatch"):
        unpack_package(
            package_path,
            plugins_root=tmp_path / "plugins",
            profiles_root=tmp_path / "profiles",
            on_conflict="rename",
        )


def test_pack_plugin_keep_staging_preserves_artifact_paths(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"

    result = pack_plugin(plugin_dir, package_path, keep_staging=True)

    assert result.staging_dir is not None
    assert result.staging_dir.exists()
    assert result.packaged_file_count >= 3
    assert result.profile_file_count == 1
    assert any(path.name == "plugin.toml" for path in result.packaged_files)
    assert result.profile_files[0].name == "default.toml"


def test_inspect_package_fails_when_manifest_is_missing(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(package_path, "manifest.toml")

    with pytest.raises(FileNotFoundError, match="manifest.toml"):
        inspect_package(package_path)


def test_inspect_package_fails_when_plugin_toml_is_missing(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(package_path, "payload/plugins/demo_plugin/plugin.toml")

    with pytest.raises(ValueError, match="does not contain plugin.toml"):
        inspect_package(package_path)


def test_inspect_package_without_metadata_reports_unverified_hash(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(package_path, "metadata.toml")

    result = inspect_package(package_path)

    assert result.metadata_found is False
    assert result.payload_hash
    assert result.payload_hash_verified is None


def test_pack_bundle_writes_multi_plugin_archive_and_unpacks(tmp_path: Path) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_one")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_two")
    package_path = tmp_path / "demo_bundle.neko-bundle"

    result = pack_bundle(
        [first_plugin, second_plugin],
        package_path,
        bundle_id="demo_bundle",
        package_name="Demo Bundle",
        version="0.2.0",
    )

    assert result.package_type == "bundle"
    assert result.plugin_id == "demo_bundle"
    assert result.plugin_ids == ["bundle_one", "bundle_two"]
    assert result.package_path == package_path.resolve()

    inspect_result = inspect_package(package_path)
    assert inspect_result.package_type == "bundle"
    assert inspect_result.package_id == "demo_bundle"
    assert inspect_result.package_name == "Demo Bundle"
    assert inspect_result.plugin_count == 2
    assert [item.plugin_id for item in inspect_result.plugins] == ["bundle_one", "bundle_two"]

    unpack_result = unpack_package(
        package_path,
        plugins_root=tmp_path / "plugins",
        profiles_root=tmp_path / "profiles",
        on_conflict="rename",
    )
    assert unpack_result.package_type == "bundle"
    assert unpack_result.unpacked_plugin_count == 2
    assert (tmp_path / "plugins" / "bundle_one" / "plugin.toml").is_file()
    assert (tmp_path / "plugins" / "bundle_two" / "plugin.toml").is_file()
