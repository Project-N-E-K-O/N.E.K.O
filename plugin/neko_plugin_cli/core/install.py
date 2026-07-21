from __future__ import annotations

from pathlib import Path
import re
import zipfile

from .models import InstalledPlugin, InstallResult
from .archive_utils import (
    collect_plugin_folders,
    compute_archive_payload_hash,
    read_manifest,
    read_metadata,
    safe_archive_path,
    validate_dependency_layout,
    validate_plugin_manifest_types,
    validate_package_type,
    validate_plugin_layout,
    verify_payload_hash,
)

_SAFE_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PackageInstaller:
    """Extract packaged plugins into the runtime plugin directory safely."""

    def install_package(
        self,
        package_path: str | Path,
        *,
        plugins_root: str | Path,
        profiles_root: str | Path,
        on_conflict: str = "fail",
    ) -> InstallResult:
        package_path = Path(package_path).expanduser().resolve()
        plugins_root_path = Path(plugins_root).expanduser().resolve()
        profiles_root_path = Path(profiles_root).expanduser().resolve()
        plugins_root_path.mkdir(parents=True, exist_ok=True)
        profiles_root_path.mkdir(parents=True, exist_ok=True)
        on_conflict = self.normalize_conflict_strategy(on_conflict)

        with zipfile.ZipFile(package_path) as archive:
            manifest = read_manifest(archive)
            package_type = self.require_string(manifest, "package_type")
            package_id = self.validate_package_id(self.require_string(manifest, "id"))
            metadata = read_metadata(archive)
            plugin_folders = collect_plugin_folders(archive)
            validate_package_type(package_type, plugin_folders)
            validate_plugin_layout(archive, plugin_folders)
            validate_plugin_manifest_types(archive, plugin_folders)
            validate_dependency_layout(archive, plugin_folders)
            payload_hash = compute_archive_payload_hash(archive)
            payload_hash_verified = verify_payload_hash(metadata, payload_hash)
            if payload_hash_verified is False:
                meta_payload = metadata.get("payload", {}) if metadata else {}
                expected = meta_payload.get("hash", "<unknown>") if isinstance(meta_payload, dict) else "<unknown>"
                raise ValueError(
                    f"payload hash mismatch: the archive content does not match the hash "
                    f"recorded in metadata.toml.\n"
                    f"  expected (metadata.toml): {expected}\n"
                    f"  computed (archive):       {payload_hash}\n"
                    f"This usually means the package was built on a different platform "
                    f"(e.g. Windows vs Linux) with an older version of neko_plugin_cli "
                    f"that had cross-platform sorting issues, or the archive was modified "
                    f"after packaging. Try re-building the plugin with the latest "
                    f"neko_plugin_cli."
                )
            folder_mapping = self.plan_plugin_targets(
                plugin_folders,
                plugins_root_path,
                on_conflict=on_conflict,
            )
            # Preflight profile target before extracting anything so that
            # on_conflict='fail' aborts without leaving partial installs.
            profile_dir = self.preflight_profile_target(
                archive,
                profiles_root=profiles_root_path,
                package_id=package_id,
                on_conflict=on_conflict,
            )
            self.extract_plugins(archive, folder_mapping)
            profile_dir = self.extract_profiles(
                archive,
                profiles_root=profiles_root_path,
                package_id=package_id,
                on_conflict=on_conflict,
                preflighted_target=profile_dir,
            )

        return InstallResult(
            package_path=package_path,
            package_type=package_type,
            package_id=package_id,
            plugins_root=plugins_root_path,
            profiles_root=profiles_root_path,
            installed_plugins=[
                InstalledPlugin(
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
        reserved_names: set[str] = set()
        for folder in plugin_folders:
            target_dir = self.resolve_plugin_target_dir(
                plugins_root / folder,
                reserved_names=reserved_names,
            )
            mapping[folder] = target_dir
            reserved_names.add(target_dir.name)
        return mapping

    @staticmethod
    def resolve_plugin_target_dir(
        target_dir: Path,
        *,
        reserved_names: set[str] | None = None,
    ) -> Path:
        if target_dir.exists() or (reserved_names is not None and target_dir.name in reserved_names):
            raise FileExistsError(f"plugin target already exists: {target_dir.name}")
        return target_dir.resolve()

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

    def preflight_profile_target(
        self,
        archive: zipfile.ZipFile,
        *,
        profiles_root: Path,
        package_id: str,
        on_conflict: str,
    ) -> Path | None:
        """Check profile target feasibility without writing anything."""
        profile_names = [
            name for name in archive.namelist()
            if len(safe_archive_path(name).parts) >= 3 and safe_archive_path(name).parts[:2] == ("payload", "profiles")
        ]
        if not profile_names:
            return None
        # This will raise FileExistsError early if on_conflict='fail' and target exists.
        return self.resolve_target_dir(profiles_root / package_id, on_conflict=on_conflict)

    def extract_profiles(
        self,
        archive: zipfile.ZipFile,
        *,
        profiles_root: Path,
        package_id: str,
        on_conflict: str,
        preflighted_target: Path | None = None,
    ) -> Path | None:
        profile_names = [
            name for name in archive.namelist()
            if len(safe_archive_path(name).parts) >= 3 and safe_archive_path(name).parts[:2] == ("payload", "profiles")
        ]
        if not profile_names:
            return None

        target_dir = preflighted_target or self.resolve_target_dir(profiles_root / package_id, on_conflict=on_conflict)
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

    def resolve_target_dir(
        self,
        desired: Path,
        *,
        on_conflict: str,
        reserved_names: set[str] | None = None,
    ) -> Path:
        if on_conflict == "fail":
            if desired.exists() or (reserved_names is not None and desired.name in reserved_names):
                raise FileExistsError(f"target already exists: {desired}")
            return desired.resolve()
        if on_conflict == "rename":
            return self.resolve_unique_dir(desired, reserved_names=reserved_names)
        raise ValueError(f"unsupported conflict strategy: {on_conflict}")

    def resolve_unique_dir(self, desired: Path, *, reserved_names: set[str] | None = None) -> Path:
        reserved_names = reserved_names or set()
        if desired.name not in reserved_names and not desired.exists():
            return desired.resolve()
        counter = 1
        while True:
            candidate = desired.with_name(f"{desired.name}_{counter}")
            if candidate.name not in reserved_names and not candidate.exists():
                return candidate.resolve()
            counter += 1

    def require_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            actual = repr(value) if value is not None else "<missing>"
            raise ValueError(
                f"manifest.toml field '{key}' must be a non-empty string, got {actual}. "
                f"The package manifest may be malformed or was created by an incompatible tool."
            )
        return value.strip()

    def validate_package_id(self, package_id: str) -> str:
        if (
            package_id in {".", ".."}
            or "/" in package_id
            or "\\" in package_id
            or not _SAFE_PACKAGE_ID_RE.fullmatch(package_id)
        ):
            raise ValueError(
                "manifest.toml field 'id' must be a safe package id containing only "
                "A-Z, a-z, 0-9, '.', '_' or '-'. Path separators and traversal segments "
                f"are not allowed, got {package_id!r}."
            )
        return package_id

    def normalize_conflict_strategy(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"rename", "fail"}:
            raise ValueError(
                f"on_conflict must be either 'rename' or 'fail', got '{value}'. "
                f"'rename' appends a numeric suffix to avoid overwriting existing directories; "
                f"'fail' aborts if the target directory already exists."
            )
        return normalized


def install_package(
    package_path: str | Path,
    *,
    plugins_root: str | Path,
    profiles_root: str | Path,
    on_conflict: str = "fail",
) -> InstallResult:
    """Public convenience wrapper for archive extraction into runtime directories."""

    return PackageInstaller().install_package(
        package_path=package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        on_conflict=on_conflict,
    )
