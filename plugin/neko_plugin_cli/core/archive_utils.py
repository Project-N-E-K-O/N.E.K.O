from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import zipfile

from plugin._types.plugin_types import require_supported_plugin_type
from plugin.core.python_dependencies import (
    collect_project_python_requirements,
    find_missing_python_requirements_from_versions,
    split_host_provided_requirements,
)

from .dependencies import collect_dependency_manifest_python_requirements
from .normalize import normalize_archive_key, validate_archive_entry_name

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def read_archive_toml(archive: zipfile.ZipFile, member_name: str, *, required: bool) -> dict[str, object] | None:
    archive_name = getattr(archive, "filename", None) or "<archive>"
    try:
        raw = archive.read(member_name)
    except KeyError:
        if required:
            raise FileNotFoundError(
                f"required file '{member_name}' not found in package archive '{archive_name}'. "
                f"The archive may be corrupted or was not created by neko_plugin_cli."
            ) from None
        return None

    data = load_toml_from_bytes(raw, source_name=member_name, archive_name=archive_name)
    return data


def load_toml_from_bytes(raw: bytes, *, source_name: str, archive_name: str = "<archive>") -> dict[str, object]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"'{source_name}' in '{archive_name}' is not valid UTF-8: {exc}"
        ) from exc
    try:
        data = tomllib.loads(text)
    except Exception as exc:
        raise ValueError(
            f"'{source_name}' in '{archive_name}' contains invalid TOML: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"'{source_name}' in '{archive_name}' root must be a TOML table, "
            f"got {type(data).__name__}"
        )
    return data


def read_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    data = read_archive_toml(archive, "manifest.toml", required=True)
    assert data is not None
    return data


def read_metadata(archive: zipfile.ZipFile) -> dict[str, object] | None:
    return read_archive_toml(archive, "metadata.toml", required=False)


def read_dependency_manifest(archive: zipfile.ZipFile) -> dict[str, object] | None:
    return read_archive_toml(archive, "payload/dependencies.toml", required=False)


def safe_archive_path(name: str) -> PurePosixPath:
    """Validate an archive entry name and return a ``PurePosixPath``.

    Delegates to :func:`normalize.validate_archive_entry_name` which performs
    comprehensive cross-platform safety checks (control chars, Windows reserved
    names, component length, traversal, etc.).
    """
    return validate_archive_entry_name(name)


def collect_plugin_folders(archive: zipfile.ZipFile) -> list[str]:
    archive_name = getattr(archive, "filename", None) or "<archive>"
    plugin_folders: set[str] = set()
    for name in archive.namelist():
        path = safe_archive_path(name)
        if path.parts[:2] != ("payload", "plugins"):
            continue
        if len(path.parts) >= 4 or (name.endswith("/") and len(path.parts) == 3):
            plugin_folders.add(path.parts[2])
    if not plugin_folders:
        raise ValueError(
            f"package archive '{archive_name}' does not contain any entries under "
            f"'payload/plugins/'. Expected at least one plugin directory. "
            f"The archive may be empty, corrupted, or not a valid neko package."
        )
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
        raise ValueError(
            f"manifest declares package_type='plugin' which requires exactly one plugin "
            f"directory under payload/plugins/, but found {len(plugin_folders)}: "
            f"{', '.join(plugin_folders)}. "
            f"Use package_type='bundle' for multi-plugin packages."
        )
    if package_type == "bundle" and not plugin_folders:
        raise ValueError(
            "manifest declares package_type='bundle' which requires at least one plugin "
            "directory under payload/plugins/, but none were found."
        )
    if package_type not in {"plugin", "bundle"}:
        raise ValueError(
            f"manifest declares package_type='{package_type}', but only 'plugin' and "
            f"'bundle' are supported. Check the manifest.toml in the package."
        )


def validate_plugin_layout(archive: zipfile.ZipFile, plugin_folders: list[str]) -> None:
    file_names = set(archive.namelist())
    missing: list[str] = []
    for folder in plugin_folders:
        plugin_toml = f"payload/plugins/{folder}/plugin.toml"
        if plugin_toml not in file_names:
            missing.append(folder)
    if missing:
        raise ValueError(
            f"the following plugin folder(s) are missing the required 'plugin.toml': "
            f"{', '.join(missing)}. "
            f"Every plugin directory under payload/plugins/ must contain a plugin.toml file."
        )


def validate_plugin_manifest_types(
    archive: zipfile.ZipFile,
    plugin_folders: list[str],
) -> None:
    """Reject removed plugin types before an archive is inspected or extracted."""

    for folder in plugin_folders:
        member_name = f"payload/plugins/{folder}/plugin.toml"
        manifest = read_archive_toml(archive, member_name, required=True)
        assert manifest is not None
        plugin_table = manifest.get("plugin")
        if not isinstance(plugin_table, dict):
            raise ValueError(
                f"'{member_name}' 必须包含 [plugin] 表，才能检查或安装该包。 / "
                f"'{member_name}' must contain a [plugin] table before the package "
                "can be inspected or installed. / "
                f"'{member_name}' を検査またはインストールするには "
                "[plugin] テーブルが必要です。"
            )
        require_supported_plugin_type(
            plugin_table.get("type", "plugin"),
            label=f"{member_name} plugin.type",
        )


def validate_dependency_layout(archive: zipfile.ZipFile, plugin_folders: list[str]) -> None:
    file_names = set(archive.namelist())
    dependency_manifest = read_dependency_manifest(archive)
    manifest_requirements = collect_dependency_manifest_python_requirements(
        dependency_manifest,
        plugin_folders,
    )

    for folder in plugin_folders:
        requirements_name = f"payload/plugins/{folder}/requirements.txt"
        if requirements_name in file_names:
            raise ValueError(
                f"plugin '{folder}' contains unsupported requirements.txt. "
                "Python runtime dependencies must be declared in pyproject.toml "
                "[project].dependencies and vendored under vendor/."
            )

        pyproject_name = f"payload/plugins/{folder}/pyproject.toml"
        pyproject_toml = read_archive_toml(archive, pyproject_name, required=False)
        python_requirements = _merge_requirement_lists(
            manifest_requirements.get(folder, []),
            collect_project_python_requirements(pyproject_toml),
        )
        external_requirements, _host_requirements = split_host_provided_requirements(python_requirements)
        if not external_requirements:
            continue

        vendor_prefix = f"payload/plugins/{folder}/vendor/"
        vendor_has_files = any(
            name.startswith(vendor_prefix) and not name.endswith("/")
            for name in file_names
        )
        if not vendor_has_files:
            raise ValueError(
                f"plugin '{folder}' declares Python runtime dependencies "
                f"({', '.join(external_requirements)}) but the package does not contain "
                "vendored dependency files under vendor/."
            )
        vendor_versions = _collect_archive_vendor_distribution_versions(
            archive,
            vendor_prefix,
            file_names,
        )
        missing_requirements = find_missing_python_requirements_from_versions(
            external_requirements,
            vendor_versions,
        )
        if missing_requirements:
            raise ValueError(
                f"plugin '{folder}' vendor/ does not satisfy Python runtime dependencies: "
                f"{', '.join(missing_requirements)}"
            )


def _merge_requirement_lists(*groups: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            requirement = str(item or "").strip()
            if not requirement:
                continue
            key = requirement.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(requirement)
    return result


def _collect_archive_vendor_distribution_versions(
    archive: zipfile.ZipFile,
    vendor_prefix: str,
    file_names: set[str],
) -> dict[str, str | None]:
    installed: dict[str, str | None] = {}
    archive_name = getattr(archive, "filename", None) or "<archive>"
    for name in file_names:
        if not name.startswith(vendor_prefix) or not name.endswith("/METADATA"):
            continue
        path = safe_archive_path(name)
        if len(path.parts) < 6 or path.parts[-1] != "METADATA":
            continue
        if not path.parts[-2].endswith(".dist-info"):
            continue

        dist_name, dist_version = _parse_distribution_metadata(
            archive.read(name),
            source_name=name,
            archive_name=archive_name,
        )
        if dist_name:
            installed[dist_name] = dist_version
    return installed


def _parse_distribution_metadata(
    raw: bytes,
    *,
    source_name: str,
    archive_name: str,
) -> tuple[str | None, str | None]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"'{source_name}' in '{archive_name}' is not valid UTF-8: {exc}"
        ) from exc

    dist_name: str | None = None
    dist_version: str | None = None
    for raw_line in text.splitlines():
        if not raw_line or raw_line[0].isspace():
            continue
        key, separator, value = raw_line.partition(":")
        if not separator:
            continue
        normalized_key = key.strip().lower()
        if normalized_key == "name":
            dist_name = value.strip() or None
        elif normalized_key == "version":
            dist_version = value.strip() or None
        if dist_name is not None and dist_version is not None:
            break
    return dist_name, dist_version


def compute_archive_payload_hash(archive: zipfile.ZipFile) -> str:
    """Compute SHA-256 over all ``payload/`` entries in the archive.

    The hash is computed by iterating payload entries in NFC-normalized
    posix-path order (case-sensitive).  For each entry the canonical relative
    path (without the leading ``payload/`` prefix) and the raw file bytes are
    fed into the digest separated by NUL bytes.
    """
    digest = hashlib.sha256()
    payload_entries: list[tuple[str, str]] = []  # (archive_name, canonical_relative)
    for name in archive.namelist():
        path = safe_archive_path(name)
        if len(path.parts) < 2 or path.parts[0] != "payload":
            continue
        if name.endswith("/"):
            continue
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        canonical = normalize_archive_key(relative)
        payload_entries.append((name, canonical))

    for archive_name, canonical in sorted(payload_entries, key=lambda item: item[1]):
        digest.update(canonical.encode("utf-8"))
        digest.update(b"\0")
        digest.update(archive.read(archive_name))
        digest.update(b"\0")
    return digest.hexdigest()


def verify_payload_hash(metadata: dict[str, object] | None, payload_hash: str) -> bool | None:
    if metadata is None:
        return None

    payload_table = metadata.get("payload")
    if not isinstance(payload_table, dict):
        raise ValueError(
            "metadata.toml is missing the required [payload] table. "
            "Expected a table with 'hash_algorithm' and 'hash' fields."
        )

    algorithm = payload_table.get("hash_algorithm")
    expected_hash = payload_table.get("hash")
    if not isinstance(algorithm, str) or algorithm.strip().lower() != "sha256":
        actual = repr(algorithm) if algorithm is not None else "<missing>"
        raise ValueError(
            f"metadata.toml [payload].hash_algorithm must be 'sha256', got {actual}. "
            f"Other hash algorithms are not currently supported."
        )
    if not isinstance(expected_hash, str) or not expected_hash.strip():
        raise ValueError(
            "metadata.toml [payload].hash must be a non-empty hex string. "
            "The package may have been created with an incompatible tool version."
        )
    return expected_hash.strip().lower() == payload_hash
