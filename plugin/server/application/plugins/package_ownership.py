from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Mapping
import zipfile


_STATE_DIRECTORY_NAMES = frozenset({"config", "data", "cache"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PackageStateConflictError(RuntimeError):
    code = "PLUGIN_PACKAGE_STATE_CONFLICT"

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            f"package-owned state file was modified locally: {relative_path}"
        )
        self.relative_path = relative_path


def validate_package_state_files(
    value: Mapping[str, str],
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for raw_path, raw_digest in value.items():
        if not isinstance(raw_path, str) or not isinstance(raw_digest, str):
            raise ValueError("package state ownership must map paths to sha256 strings")
        path = PurePosixPath(raw_path)
        if (
            path.is_absolute()
            or not path.parts
            or path.parts[0] not in _STATE_DIRECTORY_NAMES
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"invalid package state ownership path: {raw_path!r}")
        normalized_path = path.as_posix()
        digest = raw_digest.strip().lower()
        if not _SHA256_PATTERN.fullmatch(digest):
            raise ValueError(
                f"invalid package state ownership sha256 for {normalized_path!r}"
            )
        if normalized_path in validated:
            raise ValueError(
                f"duplicate package state ownership path: {normalized_path!r}"
            )
        validated[normalized_path] = digest
    return dict(sorted(validated.items()))


def collect_package_state_files(
    package_path: str | Path,
) -> dict[str, dict[str, str]]:
    """Hash package-owned files under each plugin's config/data/cache tree."""

    result: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(Path(package_path).expanduser().resolve()) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            archive_path = PurePosixPath(member.filename)
            parts = archive_path.parts
            if len(parts) < 5 or parts[:2] != ("payload", "plugins"):
                continue
            plugin_id = parts[2]
            relative_path = PurePosixPath(*parts[3:])
            if relative_path.parts[0] not in _STATE_DIRECTORY_NAMES:
                continue
            digest = hashlib.sha256()
            with archive.open(member, "r") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            result.setdefault(plugin_id, {})[relative_path.as_posix()] = digest.hexdigest()
    return {
        plugin_id: validate_package_state_files(files)
        for plugin_id, files in sorted(result.items())
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
