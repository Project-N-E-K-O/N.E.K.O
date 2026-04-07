from __future__ import annotations

from pathlib import Path
import zipfile

from .models import InspectedPackagePlugin, PackageInspectResult
from .unpack import PackageUnpacker


class PackageInspector:
    """Read-only package inspection and verification helpers."""

    def __init__(self) -> None:
        # Reuse the unpacker's parsing and validation helpers so package rules
        # stay aligned across inspect and unpack code paths.
        self._unpacker = PackageUnpacker()

    def inspect_package(self, package_path: str | Path) -> PackageInspectResult:
        package_path = Path(package_path).expanduser().resolve()

        with zipfile.ZipFile(package_path) as archive:
            manifest = self._unpacker.read_manifest(archive)
            metadata = self._unpacker.read_metadata(archive)

            package_type = self._unpacker.require_string(manifest, "package_type")
            package_id = self._unpacker.require_string(manifest, "id")
            plugin_folders = self._unpacker.collect_plugin_folders(archive)
            self._unpacker.validate_package_type(package_type, plugin_folders)
            self._unpacker.validate_plugin_layout(archive, plugin_folders)

            payload_hash = self._unpacker.compute_archive_payload_hash(archive)
            payload_hash_verified = self._unpacker.verify_payload_hash(metadata, payload_hash)
            plugins = self.collect_plugins(archive, plugin_folders)
            profile_names = self.collect_profile_names(archive)

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

    def collect_profile_names(self, archive: zipfile.ZipFile) -> list[str]:
        names: set[str] = set()
        for raw_name in archive.namelist():
            path = self._unpacker.safe_archive_path(raw_name)
            if len(path.parts) < 3 or path.parts[:2] != ("payload", "profiles"):
                continue
            if raw_name.endswith("/"):
                continue
            names.add("/".join(path.parts[2:]))
        return sorted(names)

    def read_optional_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""


def inspect_package(package_path: str | Path) -> PackageInspectResult:
    """Public convenience wrapper for read-only package inspection."""

    return PackageInspector().inspect_package(package_path)
