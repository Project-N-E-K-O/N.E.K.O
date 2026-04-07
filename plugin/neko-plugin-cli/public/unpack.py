from __future__ import annotations

from pathlib import Path, PurePosixPath
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from .models import UnpackedPlugin, UnpackResult

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
    ) -> UnpackResult:
        package_path = Path(package_path).expanduser().resolve()
        plugins_root_path = Path(plugins_root).expanduser().resolve() if plugins_root is not None else _DEFAULT_PLUGINS_ROOT
        profiles_root_path = Path(profiles_root).expanduser().resolve() if profiles_root is not None else _DEFAULT_PROFILES_ROOT
        plugins_root_path.mkdir(parents=True, exist_ok=True)
        profiles_root_path.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(package_path) as archive:
            manifest = self.read_manifest(archive)
            package_type = self.require_string(manifest, "package_type")
            package_id = self.require_string(manifest, "id")

            plugin_folders = self.collect_plugin_folders(archive)
            self.validate_package_type(package_type, plugin_folders)
            folder_mapping = self.plan_plugin_targets(plugin_folders, plugins_root_path)
            self.extract_plugins(archive, folder_mapping)
            profile_dir = self.extract_profiles(
                archive,
                profiles_root=profiles_root_path,
                package_id=package_id,
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
        )

    def read_manifest(self, archive: zipfile.ZipFile) -> dict[str, object]:
        try:
            raw = archive.read("manifest.toml")
        except KeyError as exc:
            raise FileNotFoundError("manifest.toml not found in package archive") from exc
        data = tomllib.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest.toml root must be a table")
        return data

    def collect_plugin_folders(self, archive: zipfile.ZipFile) -> list[str]:
        plugin_folders: set[str] = set()
        for name in archive.namelist():
            path = self.safe_archive_path(name)
            if len(path.parts) >= 3 and path.parts[:2] == ("payload", "plugins"):
                plugin_folders.add(path.parts[2])
        if not plugin_folders:
            raise ValueError("package archive does not contain payload/plugins entries")
        return sorted(plugin_folders)

    def validate_package_type(self, package_type: str, plugin_folders: list[str]) -> None:
        package_type = package_type.strip().lower()
        if package_type == "plugin" and len(plugin_folders) != 1:
            raise ValueError("plugin package must contain exactly one plugin folder")
        if package_type == "bundle" and not plugin_folders:
            raise ValueError("bundle package must contain one or more plugin folders")
        if package_type not in {"plugin", "bundle"}:
            raise ValueError("package_type must be either plugin or bundle")

    def plan_plugin_targets(self, plugin_folders: list[str], plugins_root: Path) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        for folder in plugin_folders:
            target_dir = self.resolve_unique_dir(plugins_root / folder)
            mapping[folder] = target_dir
        return mapping

    def extract_plugins(self, archive: zipfile.ZipFile, folder_mapping: dict[str, Path]) -> None:
        for name in archive.namelist():
            path = self.safe_archive_path(name)
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

    def extract_profiles(self, archive: zipfile.ZipFile, *, profiles_root: Path, package_id: str) -> Path | None:
        profile_names = [
            name for name in archive.namelist()
            if len(self.safe_archive_path(name).parts) >= 3 and self.safe_archive_path(name).parts[:2] == ("payload", "profiles")
        ]
        if not profile_names:
            return None

        target_dir = self.resolve_unique_dir(profiles_root / package_id)
        for name in profile_names:
            path = self.safe_archive_path(name)
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

    def resolve_unique_dir(self, desired: Path) -> Path:
        if not desired.exists():
            return desired.resolve()
        counter = 1
        while True:
            candidate = desired.with_name(f"{desired.name}_{counter}")
            if not candidate.exists():
                return candidate.resolve()
            counter += 1

    def safe_archive_path(self, name: str) -> PurePosixPath:
        path = PurePosixPath(name)
        if path.is_absolute():
            raise ValueError(f"archive entry must not be absolute: {name}")
        if ".." in path.parts:
            raise ValueError(f"archive entry must not contain parent traversal: {name}")
        return path

    def require_string(self, data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"manifest field '{key}' must be a non-empty string")
        return value.strip()


def unpack_package(
    package_path: str | Path,
    *,
    plugins_root: str | Path | None = None,
    profiles_root: str | Path | None = None,
) -> UnpackResult:
    """Public convenience wrapper for archive extraction into runtime directories."""

    return PackageUnpacker().unpack_package(
        package_path=package_path,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
