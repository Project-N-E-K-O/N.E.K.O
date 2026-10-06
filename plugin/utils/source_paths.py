"""Paths reserved for plugin dependency synchronization work files."""

from __future__ import annotations

import re
from pathlib import Path

# `neko-plugin sync` swaps vendor/ through sibling work directories at the
# plugin root. Each one holds a full third-party tree and is never plugin
# source, so every scan (build, pack, publish, ruff, check) must skip them.
#
# Only the exact generated names count (prefix + 8 hex digits, plus the
# ".pending" marker beside a backup), so a plugin's own directory that merely
# shares the prefix (".vendor.backup-notes") stays plugin source.
VENDOR_SYNC_STAGING_PREFIX = ".vendor.staging-"
VENDOR_SYNC_BACKUP_PREFIX = ".vendor.backup-"
VENDOR_SYNC_PENDING_SUFFIX = ".pending"
_VENDOR_SYNC_TOKEN_GLOB = "[0-9a-f]" * 8
# The same names as globs; fnmatch, gitignore, git pathspecs and ruff all
# accept "[...]" character classes.
VENDOR_SYNC_GLOBS = (
    f"{VENDOR_SYNC_STAGING_PREFIX}{_VENDOR_SYNC_TOKEN_GLOB}",
    f"{VENDOR_SYNC_BACKUP_PREFIX}{_VENDOR_SYNC_TOKEN_GLOB}",
    f"{VENDOR_SYNC_BACKUP_PREFIX}{_VENDOR_SYNC_TOKEN_GLOB}{VENDOR_SYNC_PENDING_SUFFIX}",
)
# Only a backup has a pending marker; staging never does.
_VENDOR_SYNC_NAME_RE = re.compile(
    rf"{re.escape(VENDOR_SYNC_STAGING_PREFIX)}[0-9a-f]{{8}}"
    rf"|{re.escape(VENDOR_SYNC_BACKUP_PREFIX)}[0-9a-f]{{8}}"
    rf"(?:{re.escape(VENDOR_SYNC_PENDING_SUFFIX)})?"
)
_VENDOR_SYNC_STAGING_RE = re.compile(rf"{re.escape(VENDOR_SYNC_STAGING_PREFIX)}[0-9a-f]{{8}}")
_METADATA_PROBE_RE = re.compile(r"\.metadata_probe_[a-z0-9_]{8}(?:\.ready)?")


def is_metadata_probe_path(relative_path: Path) -> bool:
    """Recognize only host-generated probe files at the plugin root."""
    return len(relative_path.parts) == 1 and bool(
        _METADATA_PROBE_RE.fullmatch(relative_path.name)
    )


def is_vendor_sync_path(relative_path: Path) -> bool:
    """Whether a plugin-relative path lives in a sync staging/backup dir: at
    the plugin root, or the staging dir an in-place --clean of a linked or
    mounted vendor/ creates inside it (half-installed until it finishes)."""
    parts = relative_path.parts
    if parts and _VENDOR_SYNC_NAME_RE.fullmatch(parts[0]):
        return True
    return len(parts) > 1 and parts[0] == "vendor" and bool(
        _VENDOR_SYNC_STAGING_RE.fullmatch(parts[1])
    )
