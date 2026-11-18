from __future__ import annotations

from pathlib import Path
import re
import shutil
import zipfile

from .inspect import PackageInspector
from .models import InstalledPlugin, InstallResult
from .archive_utils import read_metadata, safe_archive_path

_SAFE_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PackageInstaller:
    """Deploy plugin code; package-level profiles are only exported by unpack."""

    def install_package(
        self,
        package_path: str | Path,
        *,
        plugins_root: str | Path,
        profiles_root: str | Path | None = None,
        on_conflict: str = "fail",
    ) -> InstallResult:
        """Install code without exporting package-level profiles.

        ``profiles_root`` is a deprecated compatibility argument and is ignored.
        The result's ``profiles_root`` and ``profile_dir`` are always ``None``.
        Use ``unpack_package()`` to explicitly export the complete package.
        """
        # Retained for callers using the old install signature. Complete
        # profile exports belong to unpack_package(), not runtime deployment.
        del profiles_root
        package_path = Path(package_path).expanduser().resolve()
        plugins_root_path = Path(plugins_root).expanduser().resolve()
        plugins_root_path.mkdir(parents=True, exist_ok=True)
        on_conflict = self.normalize_conflict_strategy(on_conflict)

        with zipfile.ZipFile(package_path) as archive:
            inspected = PackageInspector().inspect_archive(
                archive,
                package_path=package_path,
            )
            if inspected.payload_hash_verified is False:
                metadata = read_metadata(archive)
                payload_table = metadata.get("payload", {}) if metadata else {}
                expected = (
                    payload_table.get("hash", "<unknown>")
                    if isinstance(payload_table, dict)
                    else "<unknown>"
                )
                raise ValueError(
                    "payload hash mismatch: the archive content does not match the hash "
                    "recorded in metadata.toml.\n"
                    f"  expected (metadata.toml): {expected}\n"
                    f"  computed (archive):       {inspected.payload_hash}\n"
                    "This usually means the package was built on a different platform "
                    "(e.g. Windows vs Linux) with an older version of neko_plugin_cli "
                    "that had cross-platform sorting issues, or the archive was modified "
                    "after packaging. Try re-building the plugin with the latest "
                    "neko_plugin_cli."
                )
            package_id = self.validate_package_id(inspected.package_id)
            plugin_folders = [item.plugin_id for item in inspected.plugins]
            folder_mapping = self.plan_plugin_targets(
                plugin_folders,
                plugins_root_path,
                on_conflict=on_conflict,
            )
            # Inspection still validates and hashes the entire payload,
            # including package profiles. Runtime configuration comes from
            # plugin files and the independent state root, not these exports.
            self.extract_plugins(archive, folder_mapping)

        return InstallResult(
            package_path=package_path,
            package_type=inspected.package_type,
            package_id=package_id,
            plugins_root=plugins_root_path,
            profiles_root=None,
            installed_plugins=[
                InstalledPlugin(
                    source_folder=source_folder,
                    target_plugin_id=target_dir.name,
                    target_dir=target_dir,
                    renamed=(target_dir.name != source_folder),
                )
                for source_folder, target_dir in sorted(folder_mapping.items())
            ],
            profile_dir=None,
            metadata_found=inspected.metadata_found,
            payload_hash=inspected.payload_hash,
            payload_hash_verified=inspected.payload_hash_verified,
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

    def extract_member(self, archive: zipfile.ZipFile, member_name: str, target_path: Path) -> None:
        info = archive.getinfo(member_name)
        if info.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            return
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, target_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)

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
    profiles_root: str | Path | None = None,
    on_conflict: str = "fail",
) -> InstallResult:
    """Public convenience wrapper for archive extraction into runtime directories."""

    return PackageInstaller().install_package(
        package_path=package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
        on_conflict=on_conflict,
    )
