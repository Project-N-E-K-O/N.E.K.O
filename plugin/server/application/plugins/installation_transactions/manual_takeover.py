"""Bound confirmation evidence for replacing a manually maintained plugin."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from plugin.server.application.install_source.models import LockEntry


def is_manual_takeover_entry(entry: LockEntry | None) -> bool:
    """Return whether ``entry`` is the active manual user candidate."""

    return bool(
        entry is not None
        and not entry.removed
        and entry.root_id == "user"
        and entry.channel == "manual"
    )


def manual_takeover_snapshot_sha256(*, entry: LockEntry, target_dir: Path) -> str:
    """Fingerprint exact ownership plus all on-disk content being replaced."""

    evidence = {
        "root_id": entry.root_id,
        "channel": entry.channel,
        "directory_name": entry.directory_name,
        "plugin_id": entry.plugin_id,
        "updated_at": entry.updated_at,
        "removed": entry.removed,
        "content_sha256": _replaceable_content_sha256(target_dir),
    }
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def local_manual_takeover_confirmation_token(
    *,
    package_path: Path,
    target_dir: Path,
    entry: LockEntry,
    snapshot_sha256: str | None = None,
) -> str:
    """Bind a local-package confirmation to package, target and ownership."""

    digest = hashlib.sha256()
    with package_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    for value in (
        str(target_dir.resolve()),
        snapshot_sha256
        or manual_takeover_snapshot_sha256(entry=entry, target_dir=target_dir),
    ):
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
    return digest.hexdigest()


def _replaceable_content_sha256(target_dir: Path) -> str:
    digest = hashlib.sha256()
    root = target_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)

    def visit(directory: Path, relative_parent: Path) -> None:
        with os.scandir(directory) as scanner:
            entries = sorted(
                scanner,
                key=lambda item: (item.name.casefold(), item.name),
            )
        for item in entries:
            relative_path = relative_parent / item.name
            metadata = item.stat(follow_symlinks=False)
            file_attributes = getattr(metadata, "st_file_attributes", 0)
            reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            is_link = item.is_symlink() or bool(file_attributes & reparse_attribute)

            digest.update(b"\0path\0")
            digest.update(relative_path.as_posix().encode("utf-8"))
            if is_link:
                digest.update(b"\0link\0")
                try:
                    target = os.readlink(item.path)
                except OSError:
                    target = (
                        f"{metadata.st_mode}:{metadata.st_size}:{metadata.st_mtime_ns}"
                    )
                digest.update(str(target).encode("utf-8"))
            elif item.is_dir(follow_symlinks=False):
                digest.update(b"\0dir")
                visit(Path(item.path), relative_path)
            elif item.is_file(follow_symlinks=False):
                digest.update(b"\0file\0")
                with open(item.path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
            else:
                digest.update(
                    f"\0special\0{metadata.st_mode}:{metadata.st_size}".encode("utf-8")
                )

    visit(root, Path())
    return digest.hexdigest()
