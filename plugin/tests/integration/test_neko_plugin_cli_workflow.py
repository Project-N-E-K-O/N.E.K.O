from __future__ import annotations

from pathlib import Path
import shutil
import sys
import zipfile

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

import cli as neko_plugin_cli
from public import analyze_bundle_plugins, inspect_package, pack_bundle, pack_plugin, unpack_package

FIXTURE_PLUGINS_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "neko_plugin_cli" / "plugins"


def _copy_fixture_plugin(tmp_path: Path, fixture_name: str) -> Path:
    source = FIXTURE_PLUGINS_ROOT / fixture_name
    target = tmp_path / fixture_name
    shutil.copytree(source, target)
    return target


def _read_archive_toml(package_path: Path, member_name: str) -> dict[str, object]:
    with zipfile.ZipFile(package_path) as archive:
        return tomllib.loads(archive.read(member_name).decode("utf-8"))


def _archive_names(package_path: Path) -> set[str]:
    with zipfile.ZipFile(package_path) as archive:
        return set(archive.namelist())


def _manifest_snapshot(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": manifest.get("schema_version"),
        "package_type": manifest.get("package_type"),
        "id": manifest.get("id"),
        "package_name": manifest.get("package_name"),
        "version": manifest.get("version"),
        "package_description": manifest.get("package_description"),
    }


def _metadata_snapshot(metadata: dict[str, object]) -> dict[str, object]:
    payload = metadata.get("payload")
    source = metadata.get("source")
    assert isinstance(payload, dict)
    assert isinstance(source, dict)
    return {
        "payload": {
            "hash_algorithm": payload.get("hash_algorithm"),
            "hash": payload.get("hash"),
        },
        "source": {
            "kind": source.get("kind"),
            "path_count": len(source.get("paths", [])) if isinstance(source.get("paths"), list) else None,
            "paths": source.get("paths"),
        },
    }


@pytest.mark.plugin_integration
def test_public_single_plugin_workflow_matches_real_package_layout(tmp_path: Path) -> None:
    plugin_dir = _copy_fixture_plugin(tmp_path, "plugin_with_rules")
    package_path = tmp_path / "plugin_with_rules.neko-plugin"

    pack_result = pack_plugin(plugin_dir, package_path)
    inspect_result = inspect_package(package_path)

    assert pack_result.plugin_id == "plugin_with_rules"
    assert inspect_result.package_type == "plugin"
    assert inspect_result.package_name == "Plugin With Rules"
    assert inspect_result.payload_hash_verified is True
    assert inspect_result.profile_names == ["default.toml"]

    manifest = _read_archive_toml(package_path, "manifest.toml")
    metadata = _read_archive_toml(package_path, "metadata.toml")
    names = _archive_names(package_path)

    assert _manifest_snapshot(manifest) == {
        "schema_version": "1.0",
        "package_type": "plugin",
        "id": "plugin_with_rules",
        "package_name": "Plugin With Rules",
        "version": "1.4.2",
        "package_description": "Fixture plugin with realistic pack rules and runtime config.",
    }
    assert _metadata_snapshot(metadata) == {
        "payload": {
            "hash_algorithm": "sha256",
            "hash": inspect_result.payload_hash,
        },
        "source": {
            "kind": "local",
            "path_count": 1,
            "paths": [str(plugin_dir.resolve())],
        },
    }

    assert "payload/plugins/plugin_with_rules/plugin.toml" in names
    assert "payload/plugins/plugin_with_rules/pyproject.toml" in names
    assert "payload/plugins/plugin_with_rules/main.py" in names
    assert "payload/plugins/plugin_with_rules/runtime.txt" in names
    assert "payload/profiles/default.toml" in names
    assert "payload/plugins/plugin_with_rules/debug.tmp" not in names
    assert "payload/plugins/plugin_with_rules/secret.txt" not in names
    assert "payload/plugins/plugin_with_rules/cache_dir/cache.txt" not in names

    unpack_result = unpack_package(
        package_path,
        plugins_root=tmp_path / "runtime_plugins",
        profiles_root=tmp_path / "runtime_profiles",
        on_conflict="rename",
    )

    unpacked_dir = unpack_result.unpacked_plugins[0].target_dir
    assert (unpacked_dir / "plugin.toml").is_file()
    assert (unpacked_dir / "main.py").is_file()
    assert not (unpacked_dir / "debug.tmp").exists()
    assert not (unpacked_dir / "secret.txt").exists()
    assert (unpack_result.profile_dir / "default.toml").is_file()


@pytest.mark.plugin_integration
def test_public_bundle_workflow_covers_analysis_pack_and_unpack(tmp_path: Path) -> None:
    alpha_dir = _copy_fixture_plugin(tmp_path, "bundle_alpha")
    beta_dir = _copy_fixture_plugin(tmp_path, "bundle_beta")
    package_path = tmp_path / "fixture_bundle.neko-bundle"

    analysis = analyze_bundle_plugins(
        [alpha_dir, beta_dir],
        current_sdk_version="2.3.0",
    )
    assert analysis.plugin_ids == ["bundle_alpha", "bundle_beta"]
    assert analysis.shared_dependencies[0].name == "shared-lib"
    assert analysis.common_dependencies[0].name == "shared-lib"
    assert analysis.sdk_supported_analysis is not None
    assert analysis.sdk_supported_analysis.has_overlap is True
    assert "2.3.0" in analysis.sdk_supported_analysis.matching_versions
    assert analysis.sdk_supported_analysis.current_sdk_supported_by_all is True

    pack_result = pack_bundle(
        [alpha_dir, beta_dir],
        package_path,
        bundle_id="fixture_bundle",
        package_name="Fixture Bundle",
        package_description="Bundle workflow fixture package.",
        version="0.9.0",
    )
    inspect_result = inspect_package(package_path)
    manifest = _read_archive_toml(package_path, "manifest.toml")
    metadata = _read_archive_toml(package_path, "metadata.toml")

    assert pack_result.package_type == "bundle"
    assert pack_result.plugin_ids == ["bundle_alpha", "bundle_beta"]
    assert inspect_result.package_type == "bundle"
    assert inspect_result.package_name == "Fixture Bundle"
    assert inspect_result.plugin_count == 2
    assert inspect_result.payload_hash_verified is True
    assert _manifest_snapshot(manifest) == {
        "schema_version": "1.0",
        "package_type": "bundle",
        "id": "fixture_bundle",
        "package_name": "Fixture Bundle",
        "version": "0.9.0",
        "package_description": "Bundle workflow fixture package.",
    }
    assert _metadata_snapshot(metadata) == {
        "payload": {
            "hash_algorithm": "sha256",
            "hash": inspect_result.payload_hash,
        },
        "source": {
            "kind": "local_bundle",
            "path_count": 2,
            "paths": [str(alpha_dir.resolve()), str(beta_dir.resolve())],
        },
    }

    unpack_result = unpack_package(
        package_path,
        plugins_root=tmp_path / "runtime_plugins",
        profiles_root=tmp_path / "runtime_profiles",
        on_conflict="rename",
    )

    unpacked_ids = [item.target_plugin_id for item in unpack_result.unpacked_plugins]
    assert unpacked_ids == ["bundle_alpha", "bundle_beta"]
    assert (tmp_path / "runtime_plugins" / "bundle_alpha" / "plugin.toml").is_file()
    assert (tmp_path / "runtime_plugins" / "bundle_beta" / "plugin.toml").is_file()
    assert (unpack_result.profile_dir / "default.toml").is_file()


@pytest.mark.plugin_integration
def test_cli_workflow_can_pack_verify_and_repeatedly_unpack_without_manual_steps(tmp_path: Path) -> None:
    plugin_dir = _copy_fixture_plugin(tmp_path, "simple_plugin")
    target_dir = tmp_path / "target"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"

    assert neko_plugin_cli.main(["pack", str(plugin_dir), "--target-dir", str(target_dir)]) == 0

    package_path = target_dir / "simple_plugin.neko-plugin"
    assert package_path.is_file()
    assert neko_plugin_cli.main(["verify", str(package_path)]) == 0

    assert (
        neko_plugin_cli.main(
            [
                "unpack",
                str(package_path),
                "--plugins-root",
                str(plugins_root),
                "--profiles-root",
                str(profiles_root),
                "--on-conflict",
                "rename",
            ]
        )
        == 0
    )
    assert (
        neko_plugin_cli.main(
            [
                "unpack",
                str(package_path),
                "--plugins-root",
                str(plugins_root),
                "--profiles-root",
                str(profiles_root),
                "--on-conflict",
                "rename",
            ]
        )
        == 0
    )

    assert (plugins_root / "simple_plugin" / "plugin.toml").is_file()
    assert (plugins_root / "simple_plugin_1" / "plugin.toml").is_file()
    assert (profiles_root / "simple_plugin" / "default.toml").is_file()
    assert (profiles_root / "simple_plugin_1" / "default.toml").is_file()
