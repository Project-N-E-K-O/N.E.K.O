from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def read_archive_toml(archive: zipfile.ZipFile, member_name: str, *, required: bool) -> dict[str, object] | None:
    try:
        raw = archive.read(member_name)
    except KeyError:
        if required:
            raise FileNotFoundError(f"{member_name} not found in package archive") from None
        return None

    data = load_toml_from_bytes(raw, source_name=member_name)
    return data


def load_toml_from_bytes(raw: bytes, *, source_name: str) -> dict[str, object]:
    data = tomllib.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{source_name} root must be a table")
    return data


def read_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    data = read_archive_toml(archive, "manifest.toml", required=True)
    assert data is not None
    return data


def read_metadata(archive: zipfile.ZipFile) -> dict[str, object] | None:
    return read_archive_toml(archive, "metadata.toml", required=False)


def safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError(f"archive entry must not be absolute: {name}")
    if ".." in path.parts:
        raise ValueError(f"archive entry must not contain parent traversal: {name}")
    return path


def collect_plugin_folders(archive: zipfile.ZipFile) -> list[str]:
    plugin_folders: set[str] = set()
    for name in archive.namelist():
        path = safe_archive_path(name)
        if len(path.parts) >= 3 and path.parts[:2] == ("payload", "plugins"):
            plugin_folders.add(path.parts[2])
    if not plugin_folders:
        raise ValueError("package archive does not contain payload/plugins entries")
    return sorted(plugin_folders)


def collect_profile_names(archive: zipfile.ZipFile) -> list[str]:
    names: set[str] = set()
    for raw_name in archive.namelist():
        path = safe_archive_path(raw_name)
        if len(path.parts) < 3 or path.parts[:2] != ("payload", "profiles"):
            continue
        if raw_name.endswith("/"):
            continue
        names.add("/".join(path.parts[2:]))
    return sorted(names)


def validate_package_type(package_type: str, plugin_folders: list[str]) -> None:
    package_type = package_type.strip().lower()
    if package_type == "plugin" and len(plugin_folders) != 1:
        raise ValueError("plugin package must contain exactly one plugin folder")
    if package_type == "bundle" and not plugin_folders:
        raise ValueError("bundle package must contain one or more plugin folders")
    if package_type not in {"plugin", "bundle"}:
        raise ValueError("package_type must be either plugin or bundle")


def validate_plugin_layout(archive: zipfile.ZipFile, plugin_folders: list[str]) -> None:
    file_names = set(archive.namelist())
    for folder in plugin_folders:
        plugin_toml = f"payload/plugins/{folder}/plugin.toml"
        if plugin_toml not in file_names:
            raise ValueError(f"packaged plugin folder '{folder}' does not contain plugin.toml")


def compute_archive_payload_hash(archive: zipfile.ZipFile) -> str:
    digest = hashlib.sha256()
    payload_entries = []
    for name in archive.namelist():
        path = safe_archive_path(name)
        if len(path.parts) < 2 or path.parts[0] != "payload":
            continue
        if name.endswith("/"):
            continue
        payload_entries.append((name, path))

    for name, path in sorted(payload_entries, key=lambda item: item[1].as_posix()):
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(archive.read(name))
        digest.update(b"\0")
    return digest.hexdigest()


def verify_payload_hash(metadata: dict[str, object] | None, payload_hash: str) -> bool | None:
    if metadata is None:
        return None

    payload_table = metadata.get("payload")
    if not isinstance(payload_table, dict):
        raise ValueError("metadata.toml must contain a [payload] table")

    algorithm = payload_table.get("hash_algorithm")
    expected_hash = payload_table.get("hash")
    if not isinstance(algorithm, str) or algorithm.strip().lower() != "sha256":
        raise ValueError("metadata payload hash_algorithm must be 'sha256'")
    if not isinstance(expected_hash, str) or not expected_hash.strip():
        raise ValueError("metadata payload hash must be a non-empty string")
    return expected_hash.strip().lower() == payload_hash
