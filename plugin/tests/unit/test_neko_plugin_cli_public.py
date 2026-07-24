from __future__ import annotations

from pathlib import Path
import inspect
import shutil
import zipfile

import pytest

from plugin.neko_plugin_cli.public import (
    build_bundle,
    build_plugin,
    inspect_package,
    install_package,
    unpack_package,
)
from plugin.neko_plugin_cli.public.build_rules import BuildRuleSet, should_skip_path

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
                "[tool.neko.build]",
                'exclude = ["*.tmp"]',
                'exclude_dirs = ["cache_dir"]',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_vendor_dist(plugin_dir, "httpx", "0.27.0")
    _write_vendor_dist(plugin_dir, "pydantic", "2.0.0")

    (plugin_dir / "__init__.py").write_text('PLUGIN_NAME = "demo"\n', encoding="utf-8")
    (plugin_dir / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    (plugin_dir / "debug.tmp").write_text("skip me\n", encoding="utf-8")
    (plugin_dir / "cache_dir").mkdir()
    (plugin_dir / "cache_dir" / "cache.txt").write_text("skip dir\n", encoding="utf-8")
    (plugin_dir / "__pycache__").mkdir()
    (plugin_dir / "__pycache__" / "module.pyc").write_bytes(b"pyc")
    return plugin_dir


def _write_vendor_dist(plugin_dir: Path, name: str, version: str) -> None:
    dist_dir = plugin_dir / "vendor" / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )


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


def _rewrite_package_without_prefixes(package_path: Path, prefixes: list[str]) -> None:
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(package_path) as src:
        for info in src.infolist():
            if any(info.filename.startswith(prefix) for prefix in prefixes):
                continue
            entries.append((info, src.read(info.filename)))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info, data in entries:
            dst.writestr(info, data)


def _rewrite_package_member(package_path: Path, member_name: str, content: str) -> None:
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(package_path) as src:
        for info in src.infolist():
            data = content.encode("utf-8") if info.filename == member_name else src.read(info.filename)
            entries.append((info, data))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info, data in entries:
            dst.writestr(info, data)


def test_public_root_exports_legacy_result_aliases() -> None:
    from plugin.neko_plugin_cli import public
    from plugin.neko_plugin_cli.public.models import PackResult, UnpackResult, UnpackedPlugin

    assert public.PackResult is PackResult
    assert public.UnpackResult is UnpackResult
    assert public.UnpackedPlugin is UnpackedPlugin


def test_build_rules_apply_include_and_exclude() -> None:
    rules = BuildRuleSet(
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

    dir_rules = BuildRuleSet(exclude_dirs=["cache_dir"])
    assert should_skip_path(Path("cache_dir"), is_dir=True, rules=dir_rules) is True
    assert should_skip_path(Path("nested/cache_dir/data.txt"), is_dir=False, rules=dir_rules) is True
    assert should_skip_path(Path("cache_dir"), is_dir=False, rules=dir_rules) is False


def test_build_rules_keep_vendored_packages_named_build_or_dist() -> None:
    rules = BuildRuleSet()

    assert should_skip_path(Path("build"), is_dir=True, rules=rules) is True
    assert should_skip_path(Path("dist/artifact.zip"), is_dir=False, rules=rules) is True
    assert should_skip_path(Path("vendor/build"), is_dir=True, rules=rules) is False
    assert should_skip_path(Path("vendor/build/__init__.py"), is_dir=False, rules=rules) is False
    assert should_skip_path(Path("vendor/dist"), is_dir=True, rules=rules) is False
    assert should_skip_path(Path("vendor/dist/__init__.py"), is_dir=False, rules=rules) is False


def test_build_plugin_writes_expected_profile_and_skips_runtime_artifacts(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    vendored_build = plugin_dir / "vendor" / "build"
    vendored_build.mkdir(parents=True)
    (vendored_build / "__init__.py").write_text("VALUE = 'vendored build package'\n", encoding="utf-8")
    package_path = tmp_path / "demo_plugin.neko-plugin"

    result = build_plugin(plugin_dir, package_path)

    assert result.plugin_id == "demo_plugin"
    assert result.package_path == package_path.resolve()
    assert result.staging_dir is None
    assert result.staged_file_count == 0
    assert result.profile_file_count == 0

    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        assert "payload/plugins/demo_plugin/plugin.toml" in names
        assert "payload/plugins/demo_plugin/runtime.txt" in names
        assert "payload/plugins/demo_plugin/vendor/build/__init__.py" in names
        assert "payload/plugins/demo_plugin/vendor/httpx-0.27.0.dist-info/METADATA" in names
        assert "payload/dependencies.toml" in names
        assert "payload/plugins/demo_plugin/debug.tmp" not in names
        assert "payload/plugins/demo_plugin/cache_dir/cache.txt" not in names
        assert "payload/plugins/demo_plugin/__pycache__/module.pyc" not in names

        profile_text = archive.read("payload/profiles/default.toml").decode("utf-8")
        assert 'enabled_plugins = ["demo_plugin"]' in profile_text
        assert "auto_start = true" in profile_text
        assert 'token = "secret-token"' in profile_text
        assert "retry = 3" in profile_text
        assert "extra_table" not in profile_text

        dependency_text = archive.read("payload/dependencies.toml").decode("utf-8")
        assert 'python_requirements = ["httpx>=0.27", "pydantic>=2.0"]' in dependency_text
        assert 'vendor_path = "plugins/demo_plugin/vendor"' in dependency_text


def test_build_plugin_rejects_pyproject_dependencies_without_vendor(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    shutil.rmtree(plugin_dir / "vendor")

    with pytest.raises(ValueError, match="vendor/ is missing"):
        build_plugin(plugin_dir, tmp_path / "demo_plugin.neko-plugin")


def test_build_plugin_rejects_requirements_txt(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "requirements.txt").write_text("httpx>=0.27\n", encoding="utf-8")

    with pytest.raises(ValueError, match="requirements.txt is not supported"):
        build_plugin(plugin_dir, tmp_path / "demo_plugin.neko-plugin")


def test_build_plugin_rejects_include_rules_that_drop_vendor(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo-plugin"',
                'version = "1.2.3"',
                'dependencies = ["httpx>=0.27", "pydantic>=2.0"]',
                "",
                "[tool.neko.build]",
                'include = ["plugin.toml", "pyproject.toml", "runtime.txt"]',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="package payload declares Python runtime dependencies"):
        build_plugin(plugin_dir, tmp_path / "demo_plugin.neko-plugin")


def test_inspect_package_reports_metadata_and_profiles(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)

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
    assert result.dependencies is not None
    assert result.dependencies.plugins[0].python_requirements == ["httpx>=0.27", "pydantic>=2.0"]


def test_inspect_package_uses_dependency_manifest_when_pyproject_is_missing(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _rewrite_package_without_prefixes(
        package_path,
        [
            "payload/plugins/demo_plugin/pyproject.toml",
            "payload/plugins/demo_plugin/vendor/",
        ],
    )

    with pytest.raises(ValueError, match="vendor/"):
        inspect_package(package_path)


def test_install_package_never_renames_existing_plugin_directory(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    build_plugin(plugin_dir, package_path)

    first = install_package(
        package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        on_conflict="rename",
    )

    assert first.installed_plugins[0].target_plugin_id == "demo_plugin"
    assert first.installed_plugins[0].renamed is False

    with pytest.raises(FileExistsError, match="demo_plugin"):
        install_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
            on_conflict="rename",
        )

    assert not (plugins_root / "demo_plugin_1").exists()


def test_executable_install_entry_points_default_to_fail_closed() -> None:
    assert inspect.signature(install_package).parameters["on_conflict"].default == "fail"
    assert inspect.signature(unpack_package).parameters["on_conflict"].default == "fail"


def test_unpack_package_never_renames_existing_plugin_directory(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    build_plugin(plugin_dir, package_path)
    (plugins_root / "demo_plugin").mkdir(parents=True)

    with pytest.raises(FileExistsError, match="demo_plugin"):
        unpack_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
            on_conflict="rename",
        )

    assert not (plugins_root / "demo_plugin_1").exists()


def test_unpack_package_preflights_profile_conflict_before_extracting_plugin(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    build_plugin(plugin_dir, package_path)
    profile_dir = profiles_root / "demo_plugin"
    profile_dir.mkdir(parents=True)
    (profile_dir / "default.toml").write_text("existing = true\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="demo_plugin"):
        unpack_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
        )

    assert not (plugins_root / "demo_plugin").exists()
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "existing = true\n"


def test_install_package_rejects_payload_hash_mismatch(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _tamper_package(package_path, "payload/profiles/default.toml")

    with pytest.raises(ValueError, match="payload hash mismatch"):
        install_package(
            package_path,
            plugins_root=tmp_path / "plugins",
            profiles_root=tmp_path / "profiles",
            on_conflict="rename",
        )


def test_install_package_rejects_vendor_missing_required_dist_metadata(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(
        package_path,
        "payload/plugins/demo_plugin/vendor/httpx-0.27.0.dist-info/METADATA",
    )

    with pytest.raises(ValueError, match="httpx>=0.27"):
        install_package(
            package_path,
            plugins_root=tmp_path / "plugins",
            profiles_root=tmp_path / "profiles",
            on_conflict="rename",
        )


def test_install_package_rejects_unsafe_profile_package_id(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _rewrite_package_member(
        package_path,
        "manifest.toml",
        "\n".join([
            'schema_version = "1.0"',
            'package_type = "plugin"',
            'id = "../outside"',
            'package_name = "Bad Package"',
            'version = "1.0.0"',
        ]) + "\n",
    )

    with pytest.raises(ValueError, match="manifest.toml field 'id'"):
        install_package(
            package_path,
            plugins_root=tmp_path / "plugins",
            profiles_root=tmp_path / "profiles",
            on_conflict="rename",
        )

    assert not (tmp_path / "outside").exists()


def test_build_plugin_keep_staging_preserves_artifact_paths(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"

    result = build_plugin(plugin_dir, package_path, keep_staging=True)

    assert result.staging_dir is not None
    assert result.staging_dir.exists()
    assert result.staged_file_count >= 3
    assert result.profile_file_count == 1
    assert any(path.name == "plugin.toml" for path in result.staged_files)
    assert result.profile_files[0].name == "default.toml"


def test_inspect_package_fails_when_manifest_is_missing(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(package_path, "manifest.toml")

    with pytest.raises(FileNotFoundError, match="manifest.toml"):
        inspect_package(package_path)


def test_inspect_package_fails_when_plugin_toml_is_missing(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(package_path, "payload/plugins/demo_plugin/plugin.toml")

    with pytest.raises(ValueError, match="plugin.toml"):
        inspect_package(package_path)


def test_inspect_package_rejects_removed_script_plugin_type(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    plugin_toml = (plugin_dir / "plugin.toml").read_text(encoding="utf-8").replace(
        'type = "plugin"',
        'type = "script"',
    )
    _rewrite_package_member(
        package_path,
        "payload/plugins/demo_plugin/plugin.toml",
        plugin_toml,
    )

    with pytest.raises(ValueError, match="plugin.type"):
        inspect_package(package_path)


def test_install_package_rejects_removed_script_type_before_extraction(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    plugin_toml = (plugin_dir / "plugin.toml").read_text(encoding="utf-8").replace(
        'type = "plugin"',
        'type = "script"',
    )
    _rewrite_package_member(
        package_path,
        "payload/plugins/demo_plugin/plugin.toml",
        plugin_toml,
    )
    plugins_root = tmp_path / "installed-plugins"

    with pytest.raises(ValueError, match="plugin.type"):
        install_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=tmp_path / "installed-profiles",
        )

    assert not plugins_root.exists() or not any(plugins_root.iterdir())


def test_unpack_package_rejects_removed_script_type_before_extraction(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    plugin_toml = (plugin_dir / "plugin.toml").read_text(encoding="utf-8").replace(
        'type = "plugin"',
        'type = "script"',
    )
    _rewrite_package_member(
        package_path,
        "payload/plugins/demo_plugin/plugin.toml",
        plugin_toml,
    )
    plugins_root = tmp_path / "unpacked-plugins"

    with pytest.raises(ValueError, match="plugin.type"):
        unpack_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=tmp_path / "unpacked-profiles",
        )

    assert not plugins_root.exists() or not any(plugins_root.iterdir())


def test_inspect_package_without_metadata_reports_unverified_hash(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)
    _rewrite_package_without_member(package_path, "metadata.toml")

    result = inspect_package(package_path)

    assert result.metadata_found is False
    assert result.payload_hash
    assert result.payload_hash_verified is None


def test_build_bundle_writes_multi_plugin_archive_and_installs(tmp_path: Path) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_one")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_two")
    package_path = tmp_path / "demo_bundle.neko-bundle"

    result = build_bundle(
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

    install_result = install_package(
        package_path,
        plugins_root=tmp_path / "plugins",
        profiles_root=tmp_path / "profiles",
        on_conflict="rename",
    )
    assert install_result.package_type == "bundle"
    assert install_result.installed_plugin_count == 2
    assert (tmp_path / "plugins" / "bundle_one" / "plugin.toml").is_file()
    assert (tmp_path / "plugins" / "bundle_two" / "plugin.toml").is_file()


def test_install_bundle_rejects_existing_plugin_without_partial_promotion(tmp_path: Path) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="foo")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="bar")
    package_path = tmp_path / "bundle.neko-bundle"
    build_bundle(
        [first_plugin, second_plugin],
        package_path,
        bundle_id="bundle",
        package_name="Bundle",
        version="1.0.0",
    )
    plugins_root = tmp_path / "plugins"
    (plugins_root / "foo").mkdir(parents=True)

    with pytest.raises(FileExistsError, match="foo"):
        install_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=tmp_path / "profiles",
        )

    assert not (plugins_root / "bar").exists()


def test_build_bundle_rejects_unsafe_bundle_id(tmp_path: Path) -> None:
    first_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_one")
    second_plugin = _make_plugin_dir(tmp_path, plugin_id="bundle_two")

    with pytest.raises(ValueError, match="bundle_id"):
        build_bundle(
            [first_plugin, second_plugin],
            tmp_path / "bad.neko-bundle",
            bundle_id="../bad",
        )


def test_build_metadata_does_not_store_absolute_source_paths(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path, plugin_id="metadata_demo")
    package_path = tmp_path / "metadata_demo.neko-plugin"

    build_plugin(plugin_dir, package_path)

    with zipfile.ZipFile(package_path) as archive:
        metadata = archive.read("metadata.toml").decode("utf-8")

    assert str(plugin_dir.resolve()) not in metadata
    assert 'paths = ["metadata_demo"]' in metadata
