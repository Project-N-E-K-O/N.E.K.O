from __future__ import annotations

from pathlib import Path
import zipfile

from .archive_utils import (
    collect_plugin_folders,
    collect_profile_names,
    compute_archive_payload_hash,
    read_dependency_manifest,
    read_manifest,
    read_metadata,
    validate_dependency_layout,
    validate_plugin_manifest_types,
    validate_package_type,
    validate_plugin_layout,
    verify_payload_hash,
)
from .models import (
    InspectedPackagePlugin,
    PackageDependencyPlugin,
    PackageDependencySummary,
    PackageInspectResult,
)


class PackageInspector:
    """Read-only package inspection and verification helpers."""

    def inspect_package(self, package_path: str | Path) -> PackageInspectResult:
        package_path = Path(package_path).expanduser().resolve()

        with zipfile.ZipFile(package_path) as archive:
            manifest = read_manifest(archive)
            metadata = read_metadata(archive)

            package_type = self.require_string(manifest, "package_type")
            package_id = self.require_string(manifest, "id")
            plugin_folders = collect_plugin_folders(archive)
            validate_package_type(package_type, plugin_folders)
            validate_plugin_layout(archive, plugin_folders)
            validate_plugin_manifest_types(archive, plugin_folders)
            validate_dependency_layout(archive, plugin_folders)

            payload_hash = compute_archive_payload_hash(archive)
            payload_hash_verified = verify_payload_hash(metadata, payload_hash)
            plugins = self.collect_plugins(archive, plugin_folders)
            profile_names = collect_profile_names(archive)
            dependencies = self.parse_dependency_manifest(read_dependency_manifest(archive))

        return PackageInspectResult(
            package_path=package_path,
            package_type=package_type,
            package_id=package_id,
            schema_version=self.read_optional_string(manifest, "schema_version"),
            package_name=self.read_optional_string(manifest, "package_name"),
            package_description=self.read_optional_string(manifest, "package_description"),
            version=self.read_optional_string(manifest, "version"),
            metadata_found=(metadata is not None),
            payload_hash=payload_hash,
            payload_hash_verified=payload_hash_verified,
            plugins=plugins,
            profile_names=profile_names,
            dependencies=dependencies,
        )

    def collect_plugins(
        self,
        archive: zipfile.ZipFile,
        plugin_folders: list[str],
    ) -> list[InspectedPackagePlugin]:
        file_names = set(archive.namelist())
        result: list[InspectedPackagePlugin] = []
        for plugin_id in sorted(plugin_folders):
            plugin_toml = f"payload/plugins/{plugin_id}/plugin.toml"
            result.append(
                InspectedPackagePlugin(
                    plugin_id=plugin_id,
                    archive_path=f"payload/plugins/{plugin_id}",
                    has_plugin_toml=(plugin_toml in file_names),
                )
            )
        return result

    def read_optional_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    def parse_dependency_manifest(
        self,
        data: dict[str, object] | None,
    ) -> PackageDependencySummary | None:
        if data is None:
            return None

        plugins_table = data.get("plugins")
        plugins: list[PackageDependencyPlugin] = []
        if isinstance(plugins_table, dict):
            for plugin_id, raw_item in sorted(plugins_table.items()):
                if not isinstance(raw_item, dict):
                    continue
                plugins.append(
                    PackageDependencyPlugin(
                        plugin_id=str(plugin_id),
                        python_requirements=self.read_optional_string_list(raw_item, "python_requirements"),
                        host_python_requirements=self.read_optional_string_list(raw_item, "host_python_requirements"),
                        plugin_dependencies=self.read_optional_string_list(raw_item, "plugin_dependencies"),
                        advanced_plugin_dependencies=self.read_optional_dict_list(
                            raw_item,
                            "advanced_plugin_dependencies",
                        ),
                        vendor_path=self.read_optional_string(raw_item, "vendor_path"),
                        vendor_present=bool(raw_item.get("vendor_present")),
                    )
                )
        return PackageDependencySummary(
            schema_version=self.read_optional_string(data, "schema_version"),
            plugins=plugins,
        )

    def read_optional_string_list(self, data: dict[str, object], key: str) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    def read_optional_dict_list(self, data: dict[str, object], key: str) -> list[dict[str, object]]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def require_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            actual = repr(value) if value is not None else "<missing>"
            raise ValueError(
                f"manifest.toml field '{key}' must be a non-empty string, got {actual}. "
                f"The package manifest may be malformed or was created by an incompatible tool."
            )
        return value.strip()


def inspect_package(package_path: str | Path) -> PackageInspectResult:
    """Public convenience wrapper for read-only package inspection."""

    return PackageInspector().inspect_package(package_path)
