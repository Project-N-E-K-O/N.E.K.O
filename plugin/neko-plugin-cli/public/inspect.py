from __future__ import annotations

from pathlib import Path
import zipfile

from .archive_utils import (
    collect_plugin_folders,
    collect_profile_names,
    compute_archive_payload_hash,
    read_manifest,
    read_metadata,
    validate_package_type,
    validate_plugin_layout,
    verify_payload_hash,
)
from .models import InspectedPackagePlugin, PackageInspectResult


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

            payload_hash = compute_archive_payload_hash(archive)
            payload_hash_verified = verify_payload_hash(metadata, payload_hash)
            plugins = self.collect_plugins(archive, plugin_folders)
            profile_names = collect_profile_names(archive)

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

    def require_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"manifest field '{key}' must be a non-empty string")
        return value.strip()


def inspect_package(package_path: str | Path) -> PackageInspectResult:
    """Public convenience wrapper for read-only package inspection."""

    return PackageInspector().inspect_package(package_path)
