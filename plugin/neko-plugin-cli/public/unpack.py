from __future__ import annotations

from pathlib import Path
import zipfile

from .models import UnpackedPlugin, UnpackResult
from .archive_utils import (
    collect_plugin_folders,
    compute_archive_payload_hash,
    read_manifest,
    read_metadata,
    safe_archive_path,
    validate_package_type,
    validate_plugin_layout,
    verify_payload_hash,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PLUGINS_ROOT = _REPO_ROOT / "plugin" / "plugins"
_DEFAULT_PROFILES_ROOT = _REPO_ROOT / "plugin" / ".neko-package-profiles"


class PackageUnpacker:
    """Extract packaged plugins into the runtime plugin directory safely."""

    def unpack_package(
        self,
        package_path: str | Path,
        *,
        plugins_root: str | Path | None = None,
        profiles_root: str | Path | None = None,
        on_conflict: str = "rename",
    ) -> UnpackResult:
        package_path = Path(package_path).expanduser().resolve()
        plugins_root_path = Path(plugins_root).expanduser().resolve() if plugins_root is not None else _DEFAULT_PLUGINS_ROOT
        profiles_root_path = Path(profiles_root).expanduser().resolve() if profiles_root is not None else _DEFAULT_PROFILES_ROOT
        plugins_root_path.mkdir(parents=True, exist_ok=True)
        profiles_root_path.mkdir(parents=True, exist_ok=True)
        on_conflict = self.normalize_conflict_strategy(on_conflict)

        with zipfile.ZipFile(package_path) as archive:
            manifest = read_manifest(archive)
            package_type = self.require_string(manifest, "package_type")
            package_id = self.require_string(manifest, "id")
            metadata = read_metadata(archive)
            plugin_folders = collect_plugin_folders(archive)
            validate_package_type(package_type, plugin_folders)
            validate_plugin_layout(archive, plugin_folders)
            payload_hash = compute_archive_payload_hash(archive)
            payload_hash_verified = verify_payload_hash(metadata, payload_hash)
            if payload_hash_verified is False:
                raise ValueError("payload hash mismatch between archive payload and metadata.toml")
            folder_mapping = self.plan_plugin_targets(
                plugin_folders,
                plugins_root_path,
                on_conflict=on_conflict,
            )
            self.extract_plugins(archive, folder_mapping)
            profile_dir = self.extract_profiles(
                archive,
                profiles_root=profiles_root_path,
                package_id=package_id,
                on_conflict=on_conflict,
            )

        return UnpackResult(
            package_path=package_path,
            package_type=package_type,
            package_id=package_id,
            plugins_root=plugins_root_path,
            profiles_root=profiles_root_path,
            unpacked_plugins=[
                UnpackedPlugin(
                    source_folder=source_folder,
                    target_plugin_id=target_dir.name,
                    target_dir=target_dir,
                    renamed=(target_dir.name != source_folder),
                )
                for source_folder, target_dir in sorted(folder_mapping.items())
            ],
            profile_dir=profile_dir,
            metadata_found=(metadata is not None),
            payload_hash=payload_hash,
            payload_hash_verified=payload_hash_verified,
            conflict_strategy=on_conflict,
        )

    def plan_plugin_targets(
        self,
        plugin_folders: list[str],
        plugins_root: Path,
        *,
        on_conflict: str,
    ) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        for folder in plugin_folders:
            target_dir = self.resolve_target_dir(plugins_root / folder, on_conflict=on_conflict)
            mapping[folder] = target_dir
        return mapping

    def extract_plugins(self, archive: zipfile.ZipFile, folder_mapping: dict[str, Path]) -> None:
        for name in archive.namelist():
            path = safe_archive_path(name)
            if len(path.parts) < 4 or path.parts[:2] != ("payload", "plugins"):
                continue
            source_folder = path.parts[2]
            target_root = folder_mapping.get(source_folder)
            if target_root is None:
                continue
            relative_parts = path.parts[3:]
            if not relative_parts:
                continue
            target_path = target_root.joinpath(*relative_parts)
            self.extract_member(archive, name, target_path)

    def extract_profiles(
        self,
        archive: zipfile.ZipFile,
        *,
        profiles_root: Path,
        package_id: str,
        on_conflict: str,
    ) -> Path | None:
        profile_names = [
            name for name in archive.namelist()
            if len(safe_archive_path(name).parts) >= 3 and safe_archive_path(name).parts[:2] == ("payload", "profiles")
        ]
        if not profile_names:
            return None

        target_dir = self.resolve_target_dir(profiles_root / package_id, on_conflict=on_conflict)
        for name in profile_names:
            path = safe_archive_path(name)
            relative_parts = path.parts[2:]
            if not relative_parts:
                continue
            target_path = target_dir.joinpath(*relative_parts)
            self.extract_member(archive, name, target_path)
        return target_dir

    def extract_member(self, archive: zipfile.ZipFile, member_name: str, target_path: Path) -> None:
        info = archive.getinfo(member_name)
        if info.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            return
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, target_path.open("wb") as dst:
            dst.write(src.read())

    def resolve_target_dir(self, desired: Path, *, on_conflict: str) -> Path:
        if on_conflict == "fail" and desired.exists():
            raise FileExistsError(f"target already exists: {desired}")
        if on_conflict == "rename":
            return self.resolve_unique_dir(desired)
        raise ValueError(f"unsupported conflict strategy: {on_conflict}")

    def resolve_unique_dir(self, desired: Path) -> Path:
        if not desired.exists():
            return desired.resolve()
        counter = 1
        while True:
            candidate = desired.with_name(f"{desired.name}_{counter}")
            if not candidate.exists():
                return candidate.resolve()
            counter += 1

    def require_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"manifest field '{key}' must be a non-empty string")
        return value.strip()

    def normalize_conflict_strategy(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"rename", "fail"}:
            raise ValueError("on_conflict must be either 'rename' or 'fail'")
        return normalized


def unpack_package(
    package_path: str | Path,
    *,
    plugins_root: str | Path | None = None,
    profiles_root: str | Path | None = None,
    on_conflict: str = "rename",
) -> UnpackResult:
    """Public convenience wrapper for archive extraction into runtime directories."""

    return PackageUnpacker().unpack_package(
        package_path=package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        on_conflict=on_conflict,
    )
