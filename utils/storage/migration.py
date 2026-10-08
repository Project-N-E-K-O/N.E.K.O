# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import ctypes
import errno
import functools
import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from utils.file_utils import atomic_write_json, read_json
from utils.logger_config import get_module_logger
from .policy import (
    POLICY_SELECTION_SOURCE_RECOVERED,
    compute_anchor_root,
    normalize_runtime_root,
    paths_equal,
    get_storage_policy_path,
    load_storage_policy,
    save_storage_policy,
)
from .path_rewrite import WORKSHOP_CONFIG_PATH_FIELDS, rebase_runtime_bound_workshop_config_paths

logger = get_module_logger(__name__)

STORAGE_MIGRATION_VERSION = 2

STORAGE_MIGRATION_STATUS_PENDING = "pending"
STORAGE_MIGRATION_STATUS_PREFLIGHT = "preflight"
STORAGE_MIGRATION_STATUS_COPYING = "copying"
STORAGE_MIGRATION_STATUS_VERIFYING = "verifying"
STORAGE_MIGRATION_STATUS_COMMITTING = "committing"
STORAGE_MIGRATION_STATUS_RETAINING_SOURCE = "retaining_source"
STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED = "rollback_required"
STORAGE_MIGRATION_STATUS_FAILED = "failed"
STORAGE_MIGRATION_STATUS_COMPLETED = "completed"

ACTIVE_STORAGE_MIGRATION_STATUSES = frozenset(
    {
        STORAGE_MIGRATION_STATUS_PENDING,
        STORAGE_MIGRATION_STATUS_PREFLIGHT,
        STORAGE_MIGRATION_STATUS_COPYING,
        STORAGE_MIGRATION_STATUS_VERIFYING,
        STORAGE_MIGRATION_STATUS_COMMITTING,
        STORAGE_MIGRATION_STATUS_RETAINING_SOURCE,
        STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
    }
)

MIGRATED_RUNTIME_ENTRY_NAMES = (
    "config",
    "memory",
    "plugins",
    "live2d",
    "vrm",
    "mmd",
    "workshop",
    "theater",
    "character_cards",
    "card_faces",
    "jukebox",
    "avatar_tools",
    "pngtuber",
    "watch_together",
    # Downloaded models (RapidOCR runtimes, memory embedding models): moved
    # so they keep working at the new root without downloading them again.
    "runtimes",
    "embedding_models",
    # Mini-game scores. ``state`` itself never moves: at the anchor root it
    # holds the storage policy and this checkpoint. An entry below a top-level
    # directory is written with forward slashes and handled as one unit; the
    # directories above it are created as needed and must be real directories.
    "state/game_scores",
)

# What v1 builds migrated. A v1 checkpoint is judged against these only:
# entries added since were never copied by it, so their retained copy may be
# the only one and must not be reported as something to delete by hand.
V1_MIGRATED_RUNTIME_ENTRY_NAMES = (
    "config",
    "memory",
    "plugins",
    "live2d",
    "vrm",
    "mmd",
    "workshop",
    "theater",
    "character_cards",
    "card_faces",
    "jukebox",
    "avatar_tools",
)

# Top-level runtime directories the app recreates by itself. They are not
# migrated (logs follow the new root from the first start), and cleaning a
# non-anchor retained root removes them so the old root can go entirely.
REGENERABLE_RUNTIME_ENTRY_NAMES = (
    "logs",
    "plugin-runtime",
    # Scratch space of character edits and of config_manager's own data
    # migration; its ledger names paths under this root's memory only.
    ".rollback_tmp",
    ".mig-staging",
)

_WINDOWS_IO_REPARSE_TAG_NAME_SURROGATE = 0x20000000
# Every migrated file is staged under ``<target>/<this>/<txid>/stage/`` before
# it is published, so both segments add to each staged path. Windows without
# long-path support fails at 260 characters, and a deep model or avatar-tool
# file that fits at its final location must still fit while staged: keep the
# prefix short (see ``_TRANSACTION_ID_PATH_CHARS``).
_MIGRATION_TRANSACTION_DIR = ".smtx"
_TRANSACTION_ID_PATH_CHARS = 12


class StorageMigrationError(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = str(error_code or "storage_migration_failed").strip() or "storage_migration_failed"
        self.message = str(message or "Storage migration failed.").strip() or "Storage migration failed."


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_optional_path(value: Path | str | None) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    return str(normalize_runtime_root(raw_value))


def _normalize_selection_source(value: str) -> str:
    return str(value or "user_selected").strip() or "user_selected"


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return not paths_equal(parent, child)
    except ValueError:
        return False


CLEANUP_PRIVATE_PREFIX = ".neko-cleanup-"

# Stands for "/" in a private cleanup name, which always sits directly in the
# retained root; no migrated entry name contains it.
_PRIVATE_NAME_SEPARATOR = "+"


def private_cleanup_name(entry_name: str, suffix: str) -> str:
    """The private name a retained-root cleanup moves ``entry_name`` to."""
    return f"{CLEANUP_PRIVATE_PREFIX}{entry_name.replace('/', _PRIVATE_NAME_SEPARATOR)}-{suffix}"


def private_cleanup_entry_name(name: str) -> str | None:
    """The migrated entry a cleanup's private name belongs to; ``None`` otherwise.

    Retained-root cleanup renames an entry to ``.neko-cleanup-<entry>-<12 hex>``
    directly in the retained root (``/`` in a nested entry's name written as
    ``+``) before checking and deleting it, so a cleanup that stopped midway
    can leave one behind under that name.
    """
    if not name.startswith(CLEANUP_PRIVATE_PREFIX):
        return None
    entry_name, separator, suffix = name[len(CLEANUP_PRIVATE_PREFIX):].rpartition("-")
    if not separator or len(suffix) != 12 or any(char not in "0123456789abcdef" for char in suffix):
        return None
    entry_name = entry_name.replace(_PRIVATE_NAME_SEPARATOR, "/")
    return entry_name if entry_name in MIGRATED_RUNTIME_ENTRY_NAMES else None


def _entry_parents(root: Path, entry_name: str) -> list[Path]:
    """The directories between ``root`` and a (possibly nested) entry."""
    parts = entry_name.split("/")[:-1]
    return [root.joinpath(*parts[: index + 1]) for index in range(len(parts))]


def entry_parents_are_real_directories(root: Path, entry_name: str) -> bool:
    """Whether every directory above the entry is a real one, not a link.

    A link or junction there would take reads, writes and deletes of the
    entry into some other directory.
    """
    return all(classify_entry_no_follow(parent) == "dir" for parent in _entry_parents(root, entry_name))


def ensure_entry_parents(root: Path, entry_name: str) -> None:
    """Create the directories above a nested entry; refuse a link among them."""
    for parent in _entry_parents(root, entry_name):
        if not os.path.lexists(parent):
            with suppress(FileExistsError):
                parent.mkdir()
        if classify_entry_no_follow(parent) != "dir":
            raise StorageMigrationError(
                "entry_parent_not_directory",
                f"迁移条目的上级不是普通目录（可能是链接或 junction），已停止: {parent}",
            )


def _has_private_cleanup_leftover(root: Path) -> bool:
    try:
        return any(private_cleanup_entry_name(child.name) for child in root.iterdir())
    except OSError:
        # Not listable right now: a leftover may still hide there, and the
        # cleanup is what reports that and stays pending.
        return True


def is_retained_root_cleanup_available(
    retained_root: Path | str | None,
    *,
    current_root: Path | str,
    anchor_root: Path | str,
    target_root: Path | str | None = None,
    require_exists: bool = True,
    allow_anchor_root: bool = False,
) -> bool:
    raw_retained_root = str(retained_root or "").strip()
    if not raw_retained_root:
        return False

    normalized_retained_root = normalize_runtime_root(raw_retained_root)
    if require_exists and not normalized_retained_root.exists():
        return False

    normalized_current_root = normalize_runtime_root(current_root)
    normalized_anchor_root = normalize_runtime_root(anchor_root)
    if paths_equal(normalized_retained_root, normalized_current_root):
        return False
    if _path_contains(normalized_retained_root, normalized_current_root):
        return False
    if paths_equal(normalized_retained_root, normalized_anchor_root):
        if not allow_anchor_root:
            return False
        # An entry a stopped cleanup left under its private name still
        # needs this cleanup to be put back, even when it was the last one.
        return any(
            (normalized_retained_root / name).exists() for name in MIGRATED_RUNTIME_ENTRY_NAMES
        ) or _has_private_cleanup_leftover(normalized_retained_root)
    if _path_contains(normalized_retained_root, normalized_anchor_root):
        return False

    raw_target_root = str(target_root or "").strip()
    if raw_target_root:
        normalized_target_root = normalize_runtime_root(raw_target_root)
        if paths_equal(normalized_retained_root, normalized_target_root):
            return False
        if _path_contains(normalized_retained_root, normalized_target_root):
            return False

    return True


def _persist_migration_payload(
    config_manager,
    payload: dict[str, Any],
    *,
    anchor_root: Path | str | None = None,
    status: str | None = None,
    **updates: Any,
) -> dict[str, Any]:
    next_payload = dict(payload)
    if status is not None:
        next_payload["status"] = str(status or "").strip()
    for key, value in updates.items():
        if value is not None:
            next_payload[key] = value
    next_payload["updated_at"] = _utc_now_iso()
    return save_storage_migration(config_manager, next_payload, anchor_root=anchor_root)


def _remove_existing_path(path: Path) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return
    if _stat_is_reparse(path_stat) or stat.S_ISLNK(path_stat.st_mode):
        raise StorageMigrationError(
            "target_link_unsupported",
            f"迁移目标包含链接或重解析点，拒绝覆盖: {path}",
        )
    if stat.S_ISDIR(path_stat.st_mode):
        # The project pins Python 3.11, where rmtree takes ``onerror``.
        shutil.rmtree(path, onerror=_retry_after_clearing_read_only)
        return
    if stat.S_ISREG(path_stat.st_mode):
        try:
            path.unlink()
        except PermissionError:
            _retry_after_clearing_read_only(os.unlink, str(path), None)
        return
    raise StorageMigrationError(
        "target_special_file_unsupported",
        f"迁移目标不是普通文件或目录: {path}",
    )


def _retry_after_clearing_read_only(function: Callable[..., Any], path: str, _error: Any) -> None:
    """Make a failed removal possible, then retry it once.

    A read-only file cannot be deleted on Windows, and on POSIX an entry
    cannot be removed from a directory without write access to it. Copies
    keep the source's modes, so either can turn up mid-removal; stopping
    there would leave an entry half deleted and its evidence unmatchable.
    Only removals are retried; any other failure (say, a directory that
    cannot be listed) is raised as it was.
    """
    if function not in (os.unlink, os.remove, os.rmdir):
        error = _error[1] if isinstance(_error, tuple) else _error
        if isinstance(error, BaseException):
            raise error
        raise OSError(f"cannot remove {path}")
    failed = Path(path)
    for candidate in (failed.parent, failed):
        with suppress(OSError):
            candidate_stat = candidate.lstat()
            if stat.S_ISLNK(candidate_stat.st_mode) or _stat_is_reparse(candidate_stat):
                continue
            extra = stat.S_IWUSR | stat.S_IRUSR
            if stat.S_ISDIR(candidate_stat.st_mode):
                extra |= stat.S_IXUSR
            os.chmod(candidate, stat.S_IMODE(candidate_stat.st_mode) | extra)
    function(path)


_AT_FDCWD = -100
_LINUX_RENAME_NOREPLACE = 0x1
_DARWIN_RENAME_EXCL = 0x4
# What renameat2/renamex_np report when the filesystem cannot honour the flag,
# plus EPERM, which a seccomp filter (containers, sandboxes) returns for a
# blocked renameat2. A real permission problem fails the fallback as well.
_NO_REPLACE_UNSUPPORTED_ERRNOS = frozenset(
    {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP), errno.EPERM}
)


@functools.lru_cache(maxsize=1)
def _native_no_replace_rename() -> Callable[[bytes, bytes], int] | None:
    try:
        if sys.platform.startswith("linux"):
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = getattr(libc, "renameat2", None)
            if renameat2 is None:
                return None
            renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameat2.restype = ctypes.c_int
            return lambda source, target: renameat2(
                _AT_FDCWD, source, _AT_FDCWD, target, _LINUX_RENAME_NOREPLACE
            )
        if sys.platform == "darwin":
            libc = ctypes.CDLL(None, use_errno=True)
            renamex_np = getattr(libc, "renamex_np", None)
            if renamex_np is None:
                return None
            renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            renamex_np.restype = ctypes.c_int
            return lambda source, target: renamex_np(source, target, _DARWIN_RENAME_EXCL)
    except (OSError, AttributeError):
        return None
    return None


def _rename_no_replace(source: Path, target: Path) -> bool:
    """Rename atomically, refusing an existing target, where the system can.

    Returns ``False`` when neither the OS nor the filesystem offers such a
    rename, and raises ``FileExistsError`` when ``target`` already exists.
    """
    rename = _native_no_replace_rename()
    if rename is None:
        return False
    if rename(os.fsencode(source), os.fsencode(target)) == 0:
        return True
    error = ctypes.get_errno()
    if error in _NO_REPLACE_UNSUPPORTED_ERRNOS:
        return False
    # OSError picks the subclass from errno: EEXIST raises FileExistsError.
    raise OSError(error, os.strerror(error), str(source), None, str(target))


def _publish_without_overwrite(
    staged: Path,
    target: Path,
    reserved: Callable[[os.stat_result], None] | None = None,
) -> None:
    """Move a staged entry into place, failing instead of replacing anything.

    Raises ``FileExistsError`` when something already occupies ``target`` --
    including an entry that appeared after the caller last checked.

    Where the name has to be reserved first, ``reserved`` gets the
    reservation's stat right after it is made, so a recovery can later tell
    that reservation from an empty entry someone else created. If it fails,
    the reservation is taken back before the error is passed on.
    """

    def _report_reservation(take_back: Callable[[Path], None]) -> None:
        if reserved is None:
            return
        try:
            reserved(target.lstat())
        except BaseException:
            with suppress(OSError):
                take_back(target)
            raise
    if os.name == "nt":
        # MoveFileEx without REPLACE_EXISTING refuses an existing target.
        os.rename(staged, target)
        return
    if stat.S_ISDIR(staged.lstat().st_mode):
        # A no-replace rename refuses even an empty directory, leaving no
        # window at all, where the kernel offers one for this filesystem.
        if _rename_no_replace(staged, target):
            return
        # rename(2) silently replaces an empty directory, so reserve the name
        # first: mkdir(2) is atomic and refuses anything already there. The
        # rename then replaces only our own empty reservation, and fails if
        # something was written into it meanwhile.
        os.mkdir(target)
        # rmdir only removes it while it is still empty.
        _report_reservation(os.rmdir)
        try:
            os.rename(staged, target)
        except OSError as exc:
            if exc.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR, errno.EISDIR}:
                raise FileExistsError(errno.EEXIST, "migration target already exists", str(target)) from exc
            raise
        return
    try:
        # link(2) is atomic and refuses an existing target.
        os.link(staged, target)
    except FileExistsError:
        raise
    except OSError:
        # Filesystems without hard links (FAT, exFAT): a rename that refuses
        # an existing target, where the kernel offers one for this
        # filesystem (Linux renameat2, macOS renamex_np).
        if _rename_no_replace(staged, target):
            return
        # Last resort: reserve the name with O_EXCL, which is atomic and
        # refuses anything already there, then replace that reservation.
        # A writer that opens the reservation in the instant before the
        # rename would lose its write; nothing atomic is left to use here.
        os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        _report_reservation(_unlink_if_empty)
        os.rename(staged, target)
        return
    os.unlink(staged)


def _unlink_if_empty(path: Path) -> None:
    if path.lstat().st_size == 0:
        os.unlink(path)


def _path_is_absent(path: Path) -> bool:
    """``True`` only when ``path`` is known not to exist.

    ``os.path.lexists`` also says ``False`` when the lookup itself fails (an
    ACL, a locked volume); that must not pass for "never written".
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _holds_only_own_publish_reservation(
    target: Path,
    staged: Path,
    expected_manifest: dict | None = None,
    reservation: dict | None = None,
) -> bool:
    """Whether an interrupted publish left nothing but its own traces at ``target``.

    While the staged copy is still in place the final move never happened,
    so the target holds at most the empty name reservation made just before
    it, or a hard link to the staged file. Anything else was put there from
    outside after the interruption and is not ours to delete.

    Windows publishes with one plain rename and never reserves or links, so
    anything there at all is someone else's. On POSIX a reservation is made
    only where the kernel lacks a no-replace rename for the filesystem, and
    ``reservation`` -- its device and inode, recorded right after it was
    made -- is what tells it from an empty entry someone else created. With
    no record (the process stopped in the instant between the two) an empty
    entry is someone else's.
    """
    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        return True
    if os.name == "nt":
        return False
    if _stat_is_reparse(target_stat) or stat.S_ISLNK(target_stat.st_mode):
        return False
    is_own_reservation = (
        isinstance(reservation, dict)
        and reservation.get("dev") == target_stat.st_dev
        and reservation.get("ino") == target_stat.st_ino
    )
    if stat.S_ISDIR(target_stat.st_mode):
        return is_own_reservation and not any(target.iterdir())
    if not stat.S_ISREG(target_stat.st_mode):
        return False
    staged_stat = staged.lstat()
    if (target_stat.st_dev, target_stat.st_ino) == (staged_stat.st_dev, staged_stat.st_ino):
        # Our hard link (an empty staged file included) -- but the file was
        # visible under the target name, and a write through it would change
        # both names alike: it is still ours only while it holds exactly what
        # was staged.
        return expected_manifest is None or _snapshot_path(target) == expected_manifest
    # Otherwise only an empty file can be ours: the O_EXCL reservation.
    return target_stat.st_size == 0 and is_own_reservation


def _move_entry_keeping_mode(source: Path, destination: Path) -> None:
    """``os.replace`` an entry to another parent directory, keeping its mode.

    On POSIX, moving a directory to a different parent needs write access to
    the directory itself (to update ``..``). A read-only directory -- one a
    previous migration published with the source's mode -- borrows owner
    write access for the move and gets its mode back afterwards.
    """
    source_stat = source.lstat()
    mode = stat.S_IMODE(source_stat.st_mode)
    borrowed = stat.S_ISDIR(source_stat.st_mode) and not mode & stat.S_IWUSR
    if borrowed:
        os.chmod(source, mode | stat.S_IWUSR)
    try:
        os.replace(source, destination)
    except BaseException:
        if borrowed:
            with suppress(OSError):
                os.chmod(source, mode)
        raise
    if borrowed:
        os.chmod(destination, mode)


def move_entry_without_overwrite(source: Path, target: Path) -> None:
    """Move an entry into place; ``FileExistsError`` instead of replacing anything."""
    _publish_without_overwrite(source, target)


def remove_runtime_entry(path: Path) -> None:
    """Remove a real file or directory tree; links and special files raise."""
    _remove_existing_path(path)


def _stat_is_reparse(path_stat: os.stat_result) -> bool:
    """Whether a Windows ``lstat`` result is a link-like reparse point.

    Only name surrogates (symlinks, junctions, mount points) redirect to
    another path. Cloud-sync placeholders (OneDrive Files On-Demand), dedup
    files and app execution aliases also carry the reparse attribute but are
    ordinary data, so the attribute alone must not reject them.
    """
    tag = int(getattr(path_stat, "st_reparse_tag", 0) or 0)
    return bool(tag & _WINDOWS_IO_REPARSE_TAG_NAME_SURROGATE)


def _classify_no_follow(path: Path) -> tuple[str, os.stat_result]:
    try:
        path_stat = path.lstat()
    except FileNotFoundError as exc:
        raise StorageMigrationError(
            "source_entry_missing",
            f"迁移条目不存在: {path}",
        ) from exc
    if _stat_is_reparse(path_stat) or stat.S_ISLNK(path_stat.st_mode):
        raise StorageMigrationError(
            "path_link_unsupported",
            f"迁移不支持链接、junction 或重解析点: {path}",
        )
    if stat.S_ISDIR(path_stat.st_mode):
        return "dir", path_stat
    if stat.S_ISREG(path_stat.st_mode):
        return "file", path_stat
    raise StorageMigrationError(
        "path_special_file_unsupported",
        f"迁移不支持特殊文件: {path}",
    )


def _hash_regular_file(path: Path) -> tuple[int, str]:
    kind, before = _classify_no_follow(path)
    if kind != "file":
        raise StorageMigrationError("path_not_file", f"迁移清单需要普通文件: {path}")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise StorageMigrationError(
                "path_not_file",
                f"迁移清单打开的对象不是普通文件: {path}",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    finally:
        os.close(descriptor)
    after = path.lstat()
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or size != int(after.st_size):
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移源文件在读取期间发生变化: {path}",
        )
    return size, digest.hexdigest()


def _manifest_path(path: Path) -> dict[str, int | str]:
    kind, _root_stat = _classify_no_follow(path)
    records: list[tuple[str, str, int, str]] = []
    if kind == "file":
        size, digest = _hash_regular_file(path)
        records.append(("", "file", size, digest))
    else:
        records.append(("", "dir", 0, ""))
        pending = [path]
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as iterator:
                    children = sorted(iterator, key=lambda item: item.name)
            except OSError as exc:
                raise StorageMigrationError(
                    "manifest_read_failed",
                    f"无法读取迁移目录: {current}",
                ) from exc
            for child in children:
                child_path = Path(child.path)
                child_kind, _child_stat = _classify_no_follow(child_path)
                relative = child_path.relative_to(path).as_posix()
                if child_kind == "dir":
                    records.append((relative, "dir", 0, ""))
                    pending.append(child_path)
                else:
                    size, digest = _hash_regular_file(child_path)
                    records.append((relative, "file", size, digest))
    return _manifest_from_records(kind, records)


def _manifest_from_records(kind: str, records: list[tuple[str, str, int, str]]) -> dict[str, int | str]:
    records = sorted(records, key=lambda record: (record[0], record[1]))
    # A POSIX name that is not valid UTF-8 arrives as surrogate escapes;
    # surrogateescape turns those back into the original bytes, so every
    # name a filesystem allows gets a manifest (valid names are unaffected).
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8", "surrogateescape")
    return {
        "kind": kind,
        "file_count": sum(1 for record in records if record[1] == "file"),
        "total_bytes": sum(record[2] for record in records if record[1] == "file"),
        "manifest_digest": hashlib.sha256(encoded).hexdigest(),
    }


def _copy_regular_file_no_follow(source_path: Path, target_path: Path) -> tuple[int, str]:
    """Copy one file; returns the size and SHA-256 of the bytes copied."""
    _kind, before = _classify_no_follow(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_fd = os.open(source_path, flags)
    try:
        opened = os.fstat(source_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise StorageMigrationError(
                "path_not_file",
                f"迁移源对象不是普通文件: {source_path}",
            )
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(os.dup(source_fd), "rb") as source_stream:
            with target_path.open("xb") as target_stream:
                # Hashed as it is copied: the manifest of what was copied
                # comes without reading the source a second time.
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
                    target_stream.write(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
        shutil.copystat(source_path, target_path, follow_symlinks=False)
    finally:
        os.close(source_fd)
    after = source_path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or size != int(after.st_size):
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移源文件在复制期间发生变化: {source_path}",
        )
    return size, digest.hexdigest()


def _copy_runtime_entry(
    source_path: Path,
    target_path: Path,
    manifest_out: dict[str, Any] | None = None,
) -> list[tuple[Path, int]]:
    """Copy one runtime entry without following links.

    Returns the directories (relative to ``target_path``) given owner write
    access only for the move, with the mode to put back once published.
    ``manifest_out["manifest"]`` receives the manifest of what was copied,
    hashed while copying.
    """
    _remove_existing_path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    kind, _source_stat = _classify_no_follow(source_path)
    records: list[tuple[str, str, int, str]] = []
    if kind == "file":
        size, digest = _copy_regular_file_no_follow(source_path, target_path)
        if manifest_out is not None:
            manifest_out["manifest"] = _manifest_from_records(kind, [("", "file", size, digest)])
        return []
    records.append(("", "dir", 0, ""))
    target_path.mkdir()
    pending = [(source_path, target_path)]
    created_dirs = [(source_path, target_path)]
    while pending:
        source_dir, target_dir = pending.pop()
        with os.scandir(source_dir) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            child_source = Path(child.path)
            child_target = target_dir / child.name
            child_kind, _child_stat = _classify_no_follow(child_source)
            relative = child_source.relative_to(source_path).as_posix()
            if child_kind == "dir":
                child_target.mkdir()
                pending.append((child_source, child_target))
                created_dirs.append((child_source, child_target))
                records.append((relative, "dir", 0, ""))
            else:
                size, digest = _copy_regular_file_no_follow(child_source, child_target)
                records.append((relative, "file", size, digest))
    # Keep directory modes and timestamps as ``copytree`` did. Apply them
    # children first, after every file is in place: a read-only directory
    # could not receive its children, and writing a child would bump the
    # parent's mtime again. Until published, the owner keeps write access to
    # each directory: on POSIX, moving a directory to another parent needs
    # it (to update ``..``); the source mode is put back after publishing.
    widened: list[tuple[Path, int]] = []
    for source_dir, target_dir in reversed(created_dirs):
        shutil.copystat(source_dir, target_dir, follow_symlinks=False)
        mode = stat.S_IMODE(target_dir.lstat().st_mode)
        if not mode & stat.S_IWUSR:
            os.chmod(target_dir, mode | stat.S_IWUSR)
            widened.append((target_dir.relative_to(target_path), mode))
    if manifest_out is not None:
        manifest_out["manifest"] = _manifest_from_records(kind, records)
    return widened


def _copy_and_verify_entry(
    source_path: Path,
    staged_path: Path,
    *,
    expected_manifest: dict | None = None,
) -> tuple[dict[str, int | str], list[tuple[Path, int]]]:
    """Stage a copy and check it: the source is read once, the copy once.

    Returns the manifest of what was copied -- which the staged copy is
    verified to match -- and the widened directory modes. With
    ``expected_manifest`` (the source already read for a comparison) the
    copy must match that too.
    """
    copy_record: dict[str, Any] = {}
    widened = _copy_runtime_entry(source_path, staged_path, manifest_out=copy_record) or []
    copied_manifest = copy_record.get("manifest")
    if not isinstance(copied_manifest, dict) or _snapshot_path(staged_path) != copied_manifest:
        raise StorageMigrationError(
            "verification_failed",
            f"迁移 staging 校验失败：{staged_path.name}。",
        )
    if expected_manifest is not None and copied_manifest != expected_manifest:
        raise StorageMigrationError(
            "verification_failed",
            f"迁移 staging 校验失败：{staged_path.name}。",
        )
    return copied_manifest, widened


def _rewrite_migrated_runtime_config_paths(
    *,
    source_root: Path,
    target_root: Path,
    config_root: Path | None = None,
) -> None:
    workshop_config_path = (config_root or target_root) / "config" / "workshop_config.json"
    if not workshop_config_path.is_file():
        return

    try:
        payload = read_json(workshop_config_path)
    except Exception as exc:
        logger.warning("Failed to read migrated workshop_config for path rewrite: %s", exc)
        return

    rewritten_payload = rebase_runtime_bound_workshop_config_paths(
        payload,
        source_root=source_root,
        target_root=target_root,
    )
    if rewritten_payload is payload:
        return

    # The copy keeps the source's mode, and Windows refuses to replace a
    # read-only file; lift it for the write. The replacement is created
    # 0600, so the original mode goes back on afterwards in every case.
    original_mode = stat.S_IMODE(workshop_config_path.stat().st_mode)
    if not original_mode & stat.S_IWUSR:
        os.chmod(workshop_config_path, original_mode | stat.S_IWUSR)
    atomic_write_json(workshop_config_path, rewritten_payload, ensure_ascii=False, indent=2)
    os.chmod(workshop_config_path, original_mode)


def source_entries_referenced_by_config(*, config_root: Path, source_root: Path) -> set[str]:
    """Source entries the workshop paths in ``config_root`` point into."""
    return _source_entries_referenced_by_config(config_root=config_root, source_root=source_root)


def _source_entries_referenced_by_config(*, config_root: Path, source_root: Path) -> set[str]:
    """Source entries that ``config_root``'s workshop paths point into.

    Deleting any of them from the retained source would break this config.
    A path at the source root itself, or a config that cannot be read,
    counts as pointing at every entry.
    """
    workshop_config_path = config_root / "workshop_config.json"
    if not os.path.lexists(workshop_config_path):
        return set()
    try:
        payload = read_json(workshop_config_path)
    except Exception:
        return set(MIGRATED_RUNTIME_ENTRY_NAMES)
    # Rebasing onto a marker root shows which values sit under the source.
    marker_root = source_root / ".neko-config-reference-probe"
    rebased = rebase_runtime_bound_workshop_config_paths(
        payload,
        source_root=source_root,
        target_root=marker_root,
    )
    if rebased is payload or not isinstance(rebased, dict):
        return set()
    referenced: set[str] = set()
    for field in WORKSHOP_CONFIG_PATH_FIELDS:
        value = rebased.get(field)
        if value == (payload.get(field) if isinstance(payload, dict) else None):
            continue
        relative = Path(str(value)).relative_to(normalize_runtime_root(marker_root)).parts
        if not relative:
            return set(MIGRATED_RUNTIME_ENTRY_NAMES)
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES:
            entry_parts = tuple(entry_name.split("/"))
            if relative[: len(entry_parts)] == entry_parts:
                referenced.add(entry_name)
    return referenced


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", ctypes.c_ulong),
    ]


@functools.lru_cache(maxsize=1)
def _windows_file_info_api() -> Any:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


_BACKSLASH = chr(92)


def _windows_extended_path(path: Path) -> str:
    raw = str(path)
    extended_prefix = _BACKSLASH * 2 + "?" + _BACKSLASH
    if raw.startswith(extended_prefix):
        return raw
    if raw.startswith(_BACKSLASH * 2):
        return extended_prefix + "UNC" + _BACKSLASH + raw[2:]
    return extended_prefix + raw


def _windows_change_time_ns(path: Path) -> int:
    """NTFS change time: updated by every write and every metadata change.

    Unlike the modification time, setting the timestamps back afterwards
    (SetFileTime) does not restore it -- that is itself a change.
    """
    kernel32 = _windows_file_info_api()
    handle = kernel32.CreateFileW(
        _windows_extended_path(path),
        0x0080,  # FILE_READ_ATTRIBUTES: no data access needed
        0x0001 | 0x0002 | 0x0004,  # share read, write and delete
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS (directories), OPEN_REPARSE_POINT
        None,
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _FileBasicInfo()
        if not kernel32.GetFileInformationByHandleEx(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(info.ChangeTime) * 100
    finally:
        kernel32.CloseHandle(handle)


def _metadata_fingerprint(path: Path, *, across_move: bool = False) -> str:
    """Digest of an entry's metadata, without reading any file.

    ``across_move`` leaves out what moving the entry itself changes -- the
    change time of the entry, and the mtime of a directory whose ``..`` a
    move rewrites -- so a staged entry can be compared with itself once
    published; everything inside still counts.

    Writing, creating, removing or renaming anything inside changes a size or
    an mtime, so comparing two fingerprints tells whether the entry was
    touched in between -- far cheaper than another content manifest. Neither
    can a write hide by setting the mtime back afterwards: the POSIX ctime
    (``st_ctime_ns``) and, on Windows, where that is the creation time, the
    NTFS change time are included, and setting timestamps updates both.
    """
    records: list[tuple[str, int, int, int, int, int]] = []
    pending = [(path, "")]
    try:
        while pending:
            current, relative = pending.pop()
            current_stat = current.lstat()
            is_dir = stat.S_ISDIR(current_stat.st_mode)
            moved_itself = across_move and relative == ""
            records.append(
                (
                    relative,
                    current_stat.st_mode,
                    0 if is_dir else current_stat.st_size,
                    0 if moved_itself and is_dir else current_stat.st_mtime_ns,
                    0 if moved_itself else current_stat.st_ctime_ns,
                    _windows_change_time_ns(current) if os.name == "nt" and not moved_itself else 0,
                )
            )
            if is_dir and not _stat_is_reparse(current_stat):
                with os.scandir(current) as iterator:
                    for child in iterator:
                        pending.append((Path(child.path), f"{relative}/{child.name}"))
    except OSError as exc:
        raise StorageMigrationError(
            "manifest_read_failed",
            f"无法读取迁移目录: {path}",
        ) from exc
    records.sort()
    encoded = json.dumps(records, separators=(",", ":")).encode("utf-8", "surrogateescape")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_path(path: Path) -> dict[str, int | str]:
    if not os.path.lexists(path):
        return {
            "kind": "missing",
            "file_count": 0,
            "total_bytes": 0,
            "manifest_digest": "",
        }
    return _manifest_path(path)


def classify_entry_no_follow(path: Path) -> str | None:
    """Return ``"file"``/``"dir"`` for a real entry, ``None`` for anything else.

    Links, junctions, special files and missing paths all give ``None``.
    """
    if not os.path.lexists(path):
        return None
    try:
        kind, _entry_stat = _classify_no_follow(path)
    except StorageMigrationError:
        return None
    return kind


def snapshot_runtime_entry(path: Path) -> dict[str, int | str]:
    """Content manifest of one runtime entry, the form copy evidence uses."""
    return _snapshot_path(path)


def metadata_fingerprint(path: Path) -> str:
    """Digest of an entry's metadata: whether it was touched since, unread."""
    return _metadata_fingerprint(path)


def rewrite_migrated_config_paths(*, source_root: Path, target_root: Path, config_root: Path) -> None:
    """Rebase workshop paths in ``config_root/config`` the way migration does."""
    _rewrite_migrated_runtime_config_paths(
        source_root=source_root,
        target_root=target_root,
        config_root=config_root,
    )


def copy_evidence_entries(copied_entries: Any) -> dict[str, dict[str, Any]]:
    """The per-entry copy evidence a checkpoint's ``copied_entries`` carries."""
    if not isinstance(copied_entries, dict):
        return {}
    return {
        str(entry_name): proof
        for entry_name, proof in copied_entries.items()
        if entry_name in MIGRATED_RUNTIME_ENTRY_NAMES and isinstance(proof, dict)
    }


def _transaction_path(target_root: Path, txid: str) -> Path:
    # The txid comes from the checkpoint on disk and the result is deleted
    # recursively, so accept only the hex form this module generates.
    if len(txid) < _TRANSACTION_ID_PATH_CHARS or any(
        char not in "0123456789abcdef" for char in txid
    ):
        raise StorageMigrationError(
            "transaction_id_invalid",
            f"迁移检查点的事务编号无效: {txid!r}",
        )
    # Only one migration runs per target at a time, so a txid prefix is enough
    # to keep an interrupted transaction apart from the next one.
    return target_root / _MIGRATION_TRANSACTION_DIR / txid[:_TRANSACTION_ID_PATH_CHARS]


def _remove_transaction(transaction_root: Path) -> None:
    _ensure_transaction_parent(transaction_root)
    _remove_existing_path(transaction_root)
    # Drop the shared transaction parent too once nothing else is in it, so a
    # finished or rolled-back migration leaves no empty directory behind. A
    # parent that still holds another transaction refuses rmdir and stays.
    with suppress(OSError):
        transaction_root.parent.rmdir()


def _ensure_transaction_dirs_not_linked(transaction_root: Path) -> None:
    """Refuse a transaction whose own directories were replaced by links.

    Rollback moves entries out of ``backup`` and deletes from ``stage``;
    through a link or junction either would reach into another directory.
    Only the shared ``.smtx`` parent is checked elsewhere.
    """
    for path in (
        transaction_root,
        transaction_root / "stage",
        transaction_root / "backup",
        transaction_root / "trash",
    ):
        if os.path.lexists(path) and classify_entry_no_follow(path) != "dir":
            raise StorageMigrationError(
                "migration_rollback_required",
                f"迁移事务目录不是普通目录（可能是链接或 junction），已原样保留等待人工处理: {path}",
            )


def _rollback_interrupted_publish(
    *,
    payload: dict[str, Any],
    target_root: Path,
    transaction_root: Path,
    mark_restoring: Callable[[str], None] | None = None,
) -> None:
    """Restore the exact pre-publish target state recorded by the checkpoint.

    Whatever the target holds is moved whole into the transaction's trash --
    one rename, so the target is never left half removed -- right after it
    was checked, and the trash goes with the transaction.

    ``mark_restoring`` records an entry in the checkpoint after the target
    went to the trash and before its backup is moved back. A backup only
    disappears through that move, so on a later run a marked entry without a
    backup is already restored, while an unmarked one has really lost its
    backup; a marked entry whose backup is still there has its target in the
    trash, and anything at the target was put there since.
    """
    _ensure_transaction_dirs_not_linked(transaction_root)
    backup_root = transaction_root / "backup"
    trash_root = transaction_root / "trash"

    def _move_into_trash(entry_name: str, target_entry: Path) -> None:
        if not os.path.lexists(target_entry):
            return
        trash_entry = trash_root / entry_name
        trash_root.mkdir(exist_ok=True)
        ensure_entry_parents(trash_root, entry_name)
        # Left by an earlier attempt that stopped right after this move, with
        # an identical copy put back at the target since: nothing to keep.
        _remove_existing_path(trash_entry)
        # Same filesystem: the transaction lives inside the target root.
        _move_entry_keeping_mode(target_entry, trash_entry)
    restoring_entries = {
        str(entry) for entry in payload.get("restoring_entries") or []
    }
    published_entries = [
        str(entry)
        for entry in payload.get("published_entries") or []
        if str(entry) in MIGRATED_RUNTIME_ENTRY_NAMES
    ]
    publishing_entry = str(payload.get("publishing_entry") or "").strip()
    if publishing_entry not in MIGRATED_RUNTIME_ENTRY_NAMES:
        publishing_entry = ""
    original_entries = {
        str(entry)
        for entry in payload.get("original_target_entries") or []
        if str(entry) in MIGRATED_RUNTIME_ENTRY_NAMES
    }
    raw_modes = payload.get("original_target_modes")
    original_modes = {
        str(entry): mode
        for entry, mode in (raw_modes.items() if isinstance(raw_modes, dict) else [])
        if str(entry) in MIGRATED_RUNTIME_ENTRY_NAMES and isinstance(mode, int)
    }

    def _restore_original_mode(entry_name: str, target_entry: Path) -> None:
        mode = original_modes.get(entry_name)
        if mode is not None and classify_entry_no_follow(target_entry) == "dir":
            os.chmod(target_entry, stat.S_IMODE(mode))

    published_manifests = {
        entry_name: proof.get("target_manifest")
        for entry_name, proof in copy_evidence_entries(payload.get("copied_entries")).items()
    }
    raw_staged_targets = payload.get("staged_target_manifests")
    staged_target_manifests = raw_staged_targets if isinstance(raw_staged_targets, dict) else {}
    raw_reservation = payload.get("publish_reservation")
    reservation = raw_reservation if isinstance(raw_reservation, dict) else {}
    candidates = list(
        dict.fromkeys(
            entry_name
            for entry_name in [*published_entries, publishing_entry]
            if entry_name
        )
    )
    for entry_name in reversed(candidates):
        target_entry = target_root / entry_name
        backup_entry = backup_root / entry_name
        was_published = entry_name in published_entries
        target_existed = entry_name in original_entries
        if entry_name == publishing_entry and not was_published:
            target_existed = bool(payload.get("publishing_target_existed"))
        # The staged copy still being there means the final move never
        # happened, so what sits at the target now is not that copy.
        staged_entry = transaction_root / "stage" / entry_name

        def foreign_at_target() -> bool:
            # Evaluated only where it decides something: an original target
            # still in place may not even be listable, and is never touched.
            return (
                not was_published
                and os.path.lexists(staged_entry)
                and not _holds_only_own_publish_reservation(
                    target_entry,
                    staged_entry,
                    staged_target_manifests.get(entry_name),
                    reservation if reservation.get("entry") == entry_name else None,
                )
            )

        # Moved in but not yet recorded as published (the checkpoint write
        # failed or the process stopped right after the move): the staged
        # copy is gone, and the target must still be exactly that copy.
        moved_unrecorded = entry_name == publishing_entry and not was_published and not os.path.lexists(staged_entry)
        expected_manifest = (
            published_manifests.get(entry_name) if was_published else staged_target_manifests.get(entry_name)
        )
        if (
            (was_published or moved_unrecorded)
            # Once marked as restoring, the target is the restored original or
            # something put there since; both are handled below.
            and entry_name not in restoring_entries
            and isinstance(expected_manifest, dict)
            and os.path.lexists(target_entry)
            and _snapshot_path(target_entry) != expected_manifest
        ):
            # Written since it was published (a sync client while the app
            # was down): rolling back would delete those writes. Keep the
            # target, the backup and the transaction for a person to decide.
            raise StorageMigrationError(
                "migration_publish_conflict",
                f"迁移目标在发布后又被改动，已保留目标、备份与事务目录，等待人工处理: {entry_name}",
            )

        if target_existed and os.path.lexists(backup_entry):
            if foreign_at_target():
                # The original is in the backup and something new took its
                # place -- the conflict the publish step records, reached
                # here when recording it failed or the process stopped.
                # Restoring would delete the newcomer; keep both.
                raise StorageMigrationError(
                    "migration_publish_conflict",
                    f"迁移目标在发布期间被重新创建，原目标已在事务备份中，等待人工处理: {entry_name}",
                )
            if entry_name in restoring_entries and os.path.lexists(target_entry):
                # Ours went to the trash before the mark and the backup is
                # still here: this was put at the target since.
                raise StorageMigrationError(
                    "migration_publish_conflict",
                    f"迁移目标在回滚期间被重新创建，原目标仍在事务备份中，等待人工处理: {entry_name}",
                )
            _move_into_trash(entry_name, target_entry)
            if mark_restoring is not None and entry_name not in restoring_entries:
                mark_restoring(entry_name)
                restoring_entries.add(entry_name)
            ensure_entry_parents(target_root, entry_name)
            _move_entry_keeping_mode(backup_entry, target_entry)
            _restore_original_mode(entry_name, target_entry)
            continue
        if target_existed:
            if entry_name in restoring_entries and os.path.lexists(target_entry):
                # An earlier rollback moved this backup back and stopped
                # before removing the transaction.
                _restore_original_mode(entry_name, target_entry)
                continue
            if was_published or not os.path.lexists(target_entry):
                # Neither the backup nor the original at its place: it may
                # have been moved into the backup and lost since. Retrying
                # would treat it as never touched; keep the transaction.
                raise StorageMigrationError(
                    "migration_rollback_required",
                    f"迁移事务缺少目标备份，拒绝继续: {entry_name}",
                )
            # The checkpoint can precede the first replace. With no backup, the
            # target is still the original and must remain untouched -- apart
            # from its mode, which the move may have widened before stopping.
            _restore_original_mode(entry_name, target_entry)
            continue
        if foreign_at_target():
            # Interrupted between reserving the name and moving the copy in,
            # and something was written there since: leave it, as a publish
            # that fails on a newcomer does.
            continue
        _move_into_trash(entry_name, target_entry)

    _remove_transaction(transaction_root)


def _rollback_publish_or_require_recovery(
    *,
    payload: dict[str, Any],
    target_root: Path,
    transaction_root: Path,
    mark_restoring: Callable[[str], None] | None = None,
) -> None:
    """Roll back, turning any failure into the retryable rollback state.

    A failed rollback can leave original target entries only in the
    transaction backup. ``migration_rollback_required`` keeps the checkpoint
    active so the next start tries the rollback again instead of treating the
    migration as finished.
    A publish conflict found on the way passes through unchanged: it needs a
    person, not another rollback attempt.
    """
    try:
        _rollback_interrupted_publish(
            payload=payload,
            target_root=target_root,
            transaction_root=transaction_root,
            mark_restoring=mark_restoring,
        )
    except StorageMigrationError as exc:
        if exc.error_code in {"migration_rollback_required", "migration_publish_conflict"}:
            raise
        raise StorageMigrationError(
            "migration_rollback_required",
            f"迁移目标回滚未完成: {exc.message}",
        ) from exc
    except Exception as exc:
        raise StorageMigrationError(
            "migration_rollback_required",
            f"迁移目标回滚未完成: {exc}",
        ) from exc


def _remove_completed_transaction_leftover(payload: dict[str, Any] | None) -> None:
    """Retry removing a finished migration's transaction directory.

    Completion, and the cleanup after a PREFLIGHT/COPYING failure, only log a
    failed removal (a file may be locked on Windows), and a completed or
    failed checkpoint is never run again, so without this the overwritten
    original target or a staged copy could stay under ``.smtx`` forever.
    A failed checkpoint's transaction is removed only while its backup is
    empty: original target data in there is never thrown away.
    """
    if not isinstance(payload, dict):
        return
    status = str(payload.get("status") or "").strip().lower()
    if status not in {STORAGE_MIGRATION_STATUS_COMPLETED, STORAGE_MIGRATION_STATUS_FAILED}:
        return
    raw_target_root = str(payload.get("target_root") or "").strip()
    txid = str(payload.get("txid") or "").strip()
    if not raw_target_root or not txid:
        return
    try:
        transaction_root = _transaction_path(normalize_runtime_root(raw_target_root), txid)
        if not os.path.lexists(transaction_root):
            return
        if status == STORAGE_MIGRATION_STATUS_FAILED:
            backup_root = transaction_root / "backup"
            if os.path.lexists(backup_root) and any(backup_root.iterdir()):
                return
        _remove_transaction(transaction_root)
    except Exception as exc:
        logger.warning("Failed to remove leftover storage migration transaction: %s", exc)


def _transaction_entries_diverged_from_source(
    *,
    payload: dict[str, Any],
    source_root: Path,
    transaction_root: Path,
) -> list[str]:
    """Entries this transaction published or staged that the source no
    longer holds as they were copied: gone, or changed since (a file inside
    removed or edited). The checkpoint records each entry's source manifest
    once staged, so this is a comparison, not a guess."""
    entries = [str(entry) for entry in payload.get("published_entries") or []]
    entries.append(str(payload.get("publishing_entry") or ""))
    recorded: dict[str, Any] = {}
    staged_records = payload.get("staged_source_manifests")
    if isinstance(staged_records, dict):
        recorded.update({str(name): manifest for name, manifest in staged_records.items() if isinstance(manifest, dict)})
    # Staged entries are recorded once staged; the listing adds the one whose
    # record may not have landed yet.
    entries.extend(recorded)
    stage_root = transaction_root / "stage"
    if os.path.lexists(stage_root):
        try:
            listed = {child.name for child in stage_root.iterdir()}
            entries.extend(listed)
            for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES:
                if "/" in entry_name and entry_name.split("/")[0] in listed and not _path_is_absent(
                    stage_root / entry_name
                ):
                    entries.append(entry_name)
        except OSError as exc:
            # Cannot tell what the stage holds; it may be the only copy left.
            raise StorageMigrationError(
                "migration_stage_unreadable",
                f"迁移事务的暂存目录无法读取，迁移未完成，已保留目标与事务目录，可以读取后会继续处理: {stage_root}",
            ) from exc
    for entry_name, proof in copy_evidence_entries(payload.get("copied_entries")).items():
        if isinstance(proof.get("source_manifest"), dict):
            recorded[entry_name] = proof["source_manifest"]
    diverged: list[str] = []
    for entry_name in dict.fromkeys(entries):
        if entry_name not in MIGRATED_RUNTIME_ENTRY_NAMES:
            continue
        source_entry = source_root / entry_name
        if not os.path.lexists(source_entry):
            diverged.append(entry_name)
            continue
        expected = recorded.get(entry_name)
        if expected is None:
            continue
        try:
            changed = _snapshot_path(source_entry) != expected
        except (StorageMigrationError, OSError):
            # Unreadable right now (a Windows sharing violation, say) is no
            # proof the source is intact: keep everything and retry later.
            changed = True
        if changed:
            diverged.append(entry_name)
    return diverged


def _ensure_transaction_parent(transaction_root: Path) -> None:
    parent = transaction_root.parent
    if not os.path.lexists(parent):
        return
    # ``mkdir(parents=True)`` would follow a link or junction here and stage
    # the migration somewhere else entirely.
    kind, _parent_stat = _classify_no_follow(parent)
    if kind != "dir":
        raise StorageMigrationError(
            "target_special_file_unsupported",
            f"迁移事务目录不是普通目录: {parent}",
        )


def _iter_existing_runtime_entries(root: Path) -> list[str]:
    return [
        name
        for name in MIGRATED_RUNTIME_ENTRY_NAMES
        if os.path.lexists(root / name)
    ]


def _is_ignorable_content_name(name: str) -> bool:
    # The same names the cloud-save probe skips: dot-named files (.DS_Store,
    # atomic-write temporaries, locks) and Python caches are never user data.
    return name.startswith(".") or name == "__pycache__"


def _tree_has_user_file(path: Path) -> bool:
    """Whether a directory holds a user file anywhere below it.

    Empty subdirectories (an interrupted download, scaffolding) do not count;
    a link does, and so does a tree that cannot be read.
    """

    def _raise(error: OSError) -> None:
        raise error

    try:
        for dirpath, dirnames, filenames in os.walk(path, onerror=_raise):
            if any(not _is_ignorable_content_name(name) for name in filenames):
                return True
            kept: list[str] = []
            for name in dirnames:
                if _is_ignorable_content_name(name):
                    continue
                if os.path.islink(os.path.join(dirpath, name)):
                    return True
                kept.append(name)
            dirnames[:] = kept
    except OSError:
        return True
    return False


def root_has_migrated_entry_content(root: Path, names: Any = MIGRATED_RUNTIME_ENTRY_NAMES) -> bool:
    """Whether any of ``names`` under ``root`` holds user data."""
    try:
        from utils.cloudsave_runtime._shared import TRANSACTIONAL_RUNTIME_ENTRY_PATTERNS as transactional_patterns
    except Exception:
        transactional_patterns = {}
    for entry_name in names:
        entry = root / entry_name
        try:
            if not os.path.lexists(entry):
                continue
            if entry.is_symlink() or not entry.is_dir():
                return True
        except OSError:
            return True
        # As in the cloud-save probe, a dot-named transaction entry directly
        # under the entry (an interrupted update's backup) may be the only
        # copy of something, so it counts.
        pattern = transactional_patterns.get(entry_name)
        if pattern is not None:
            try:
                if any(pattern.fullmatch(child.name) for child in entry.iterdir()):
                    return True
            except OSError:
                return True
        if _tree_has_user_file(entry):
            return True
    return False


def root_has_user_content(root: Path, *, config_manager) -> bool:
    """Whether ``root`` already holds runtime data a migration would replace."""
    return _root_has_user_content(root, config_manager=config_manager)


def _root_has_user_content(root: Path, *, config_manager) -> bool:
    try:
        from utils.cloudsave_runtime import LEGACY_RUNTIME_DIR_NAMES, runtime_root_has_user_content

        if runtime_root_has_user_content(root, config_manager=config_manager):
            return True
        # Only the entries the probe above does not know: it has its own
        # rules for config and theater defaults that must stay in force.
        extra = [name for name in MIGRATED_RUNTIME_ENTRY_NAMES if name not in LEGACY_RUNTIME_DIR_NAMES]
        return root_has_migrated_entry_content(root, extra)
    except Exception:
        if not root.exists() or not root.is_dir():
            return False
        try:
            # Dot-named entries (such as the migration transaction directory)
            # are not user content, matching runtime_root_has_user_content.
            return any(not child.name.startswith(".") for child in root.iterdir())
        except OSError:
            return False


def _ensure_target_root_writable(target_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    probe_parent = target_root if target_root.exists() else target_root.parent
    if not os.access(str(probe_parent), os.R_OK | os.W_OK | os.X_OK):
        raise StorageMigrationError("target_not_writable", "目标路径当前不可写，无法执行关闭后的迁移。")


def get_storage_migration_path(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
) -> Path:
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager)
    )
    return normalized_anchor_root / "state" / "storage_migration.json"


def load_storage_migration(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    try:
        payload = read_json(migration_path)
    except FileNotFoundError:
        return default
    except Exception as exc:
        logger.warning("Failed to read storage_migration checkpoint: %s", exc)
        return default

    if not isinstance(payload, dict):
        logger.warning("storage_migration payload is not a dict: %s", migration_path)
        return default

    return payload


def is_legacy_unproven_checkpoint(payload: dict[str, Any] | None) -> bool:
    """Whether a completed checkpoint predates per-entry copy evidence.

    v1 builds migrated without recording ``copied_entries``. Retained-root
    cleanup cannot demand evidence such a checkpoint never had.
    """
    if not isinstance(payload, dict):
        return False
    try:
        version = int(payload.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    return version < STORAGE_MIGRATION_VERSION


def is_storage_migration_pending(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False

    status = str(payload.get("status") or "").strip().lower()
    if status not in ACTIVE_STORAGE_MIGRATION_STATUSES:
        return False

    source_root = str(payload.get("source_root") or "").strip()
    target_root = str(payload.get("target_root") or "").strip()
    return bool(source_root and target_root)


def build_pending_storage_migration_payload(
    *,
    source_root: Path | str,
    target_root: Path | str,
    selection_source: str,
    backup_root: Path | str | None = None,
    confirmed_existing_target_content: bool = False,
    txid: str | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now_iso()
    return {
        "version": STORAGE_MIGRATION_VERSION,
        "txid": str(txid or uuid.uuid4().hex),
        "status": STORAGE_MIGRATION_STATUS_PENDING,
        "source_root": str(normalize_runtime_root(source_root)),
        "target_root": str(normalize_runtime_root(target_root)),
        "selection_source": _normalize_selection_source(selection_source),
        "confirmed_existing_target_content": bool(confirmed_existing_target_content),
        "backup_root": _normalize_optional_path(backup_root),
        "copied_entries": {},
        "published_entries": [],
        "publishing_entry": "",
        "publishing_target_existed": False,
        "restoring_entries": [],
        "error_code": "",
        "error_message": "",
        "requested_at": timestamp,
        "started_at": "",
        "updated_at": timestamp,
    }


def save_storage_migration(
    config_manager,
    payload: dict[str, Any],
    *,
    anchor_root: Path | str | None = None,
) -> dict[str, Any]:
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    atomic_write_json(migration_path, payload, ensure_ascii=False, indent=2)
    return payload


def create_pending_storage_migration(
    config_manager,
    *,
    source_root: Path | str,
    target_root: Path | str,
    selection_source: str,
    anchor_root: Path | str | None = None,
    backup_root: Path | str | None = None,
    confirmed_existing_target_content: bool = False,
) -> dict[str, Any]:
    payload = build_pending_storage_migration_payload(
        source_root=source_root,
        target_root=target_root,
        selection_source=selection_source,
        backup_root=backup_root,
        confirmed_existing_target_content=confirmed_existing_target_content,
    )
    return save_storage_migration(config_manager, payload, anchor_root=anchor_root)


def record_retained_cleanup_started(config_manager, *, anchor_root: Path) -> None:
    """Record, before anything is deleted, that a retained-root cleanup runs.

    A retained root found gone later counts as cleaned only with this record:
    an unmounted disk can make it vanish too, and comes back with the data.
    """
    migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root)
    if not isinstance(migration_payload, dict):
        return
    updated_payload = dict(migration_payload)
    updated_payload["cleanup_started_at"] = _utc_now_iso()
    save_storage_migration(config_manager, updated_payload, anchor_root=anchor_root)


def record_retained_cleanup_completed(config_manager, *, anchor_root: Path, retained_root: str) -> None:
    """Record that nothing migrated is left in the retained root."""
    from utils.root_state_lock import root_state_transaction

    # The checkpoint and root_state are written in one go, without an await in
    # between: the storage page polls on a 1200ms timer and must not see the
    # checkpoint cleaned while root_state still has cleanup pending.
    migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root) or {}
    if isinstance(migration_payload, dict):
        updated_payload = dict(migration_payload)
        updated_payload["backup_root"] = ""
        updated_payload["retained_source_root"] = ""
        updated_payload["retained_source_mode"] = "cleaned"
        updated_payload["updated_at"] = _utc_now_iso()
        updated_payload["cleanup_completed_at"] = _utc_now_iso()
        save_storage_migration(config_manager, updated_payload, anchor_root=anchor_root)

    # Best effort: the cleanup itself is done, a flag that did not land must
    # not turn it into a failure.
    try:
        with root_state_transaction():
            root_state = config_manager.load_root_state()
            if isinstance(root_state, dict):
                updated_root_state = dict(root_state)
                updated_root_state["legacy_cleanup_pending"] = False
                if paths_equal(updated_root_state.get("last_migration_backup") or "", retained_root):
                    updated_root_state["last_migration_backup"] = ""
                config_manager.save_root_state(updated_root_state)
    except Exception as exc:
        logger.warning("Failed to clear the pending cleanup in root_state: %s", exc)


def _retained_root_holds_no_migrated_entries(retained_root: Path, *, cleanup_started: bool) -> bool:
    """Whether the retained root is known to hold nothing left to clean up.

    Not knowing counts as holding something: a retained root that cannot be
    reached or listed may still hold data.
    """
    try:
        retained_stat = os.lstat(retained_root)
    except FileNotFoundError:
        # Gone counts as removed only after a cleanup started: an unmounted
        # disk leaves its mount point behind while the root vanishes.
        if not cleanup_started:
            return False
        # Removed, not out of reach: an unplugged drive or an offline share
        # takes the parent directory with it.
        try:
            return stat.S_ISDIR(os.stat(retained_root.parent).st_mode)
        except OSError:
            return False
    except OSError:
        return False
    if not stat.S_ISDIR(retained_stat.st_mode) or _stat_is_reparse(retained_stat):
        return False
    try:
        with os.scandir(retained_root) as iterator:
            names = [child.name for child in iterator]
    except OSError:
        return False
    if any(name in MIGRATED_RUNTIME_ENTRY_NAMES or private_cleanup_entry_name(name) is not None for name in names):
        return False
    return all(
        _path_is_absent(retained_root / entry_name)
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES
        if "/" in entry_name
    )


def reconcile_finished_retained_cleanup(config_manager, *, anchor_root: Path | str) -> str:
    """Record a retained-root cleanup that finished without being recorded.

    The cleanup deletes first and records afterwards; a failed final write
    (a full or read-only disk) leaves the checkpoint pointing at a retained
    root with nothing left to clean, and every later cleanup request finds
    nothing to do. Returns the retained root it recorded as cleaned, or "".
    """
    normalized_anchor_root = normalize_runtime_root(anchor_root)
    payload = load_storage_migration(config_manager, anchor_root=normalized_anchor_root)
    if not isinstance(payload, dict) or is_storage_migration_pending(payload):
        return ""
    if str(payload.get("status") or "").strip() != STORAGE_MIGRATION_STATUS_COMPLETED:
        return ""
    if str(payload.get("retained_source_mode") or "").strip() == "cleaned":
        return ""
    retained_root = str(
        payload.get("retained_source_root")
        or payload.get("backup_root")
        or payload.get("source_root")
        or ""
    ).strip()
    if not retained_root:
        return ""
    current_root = str(getattr(config_manager, "app_docs_dir", "") or "").strip()
    if current_root and paths_equal(normalize_runtime_root(retained_root), normalize_runtime_root(current_root)):
        return ""
    if not _retained_root_holds_no_migrated_entries(
        normalize_runtime_root(retained_root),
        cleanup_started=bool(str(payload.get("cleanup_started_at") or "").strip()),
    ):
        return ""
    record_retained_cleanup_completed(
        config_manager,
        anchor_root=normalized_anchor_root,
        retained_root=retained_root,
    )
    return retained_root


def catch_up_v1_migration(config_manager, *, anchor_root: Path | str) -> list[str]:
    """Copy over what a completed v1 migration never knew about.

    v1 builds migrated only ``V1_MIGRATED_RUNTIME_ENTRY_NAMES``. Entries added
    since (pngtuber, watch_together, ...) stayed in the old root while the app
    moved on to the new one, so to the user they were gone. Each is staged,
    checked and published without overwriting, as a migration does, and its
    copy evidence lets cleanup remove the old copy.

    The app creates some of these directories empty at every start, so an
    entry the new root holds without a single user file is only scaffolding:
    it goes to the transaction's trash and the old data takes its place. One
    with real data there is left alone on both sides and recorded in
    ``v1_catch_up_skipped``, for the storage page to point the user at.

    Runs once; an attempt stopped midway is safe to repeat (what was already
    published is found in the new root and left alone). Returns the entries
    copied.
    """
    normalized_anchor_root = normalize_runtime_root(anchor_root)
    payload = load_storage_migration(config_manager, anchor_root=normalized_anchor_root)
    if not isinstance(payload, dict) or is_storage_migration_pending(payload):
        return []
    if str(payload.get("status") or "").strip() != STORAGE_MIGRATION_STATUS_COMPLETED:
        return []
    if not is_legacy_unproven_checkpoint(payload) or str(payload.get("v1_catch_up_completed_at") or "").strip():
        return []
    if str(payload.get("retained_source_mode") or "").strip() == "cleaned":
        return []
    raw_source = str(
        payload.get("retained_source_root") or payload.get("backup_root") or payload.get("source_root") or ""
    ).strip()
    raw_target = str(payload.get("target_root") or "").strip()
    if not raw_source or not raw_target:
        return []
    source_root = normalize_runtime_root(raw_source)
    target_root = normalize_runtime_root(raw_target)
    if paths_equal(source_root, target_root) or _path_contains(source_root, target_root) or _path_contains(
        target_root, source_root
    ):
        return []
    # Only into the root the app really runs on now.
    committed_policy = load_storage_policy(config_manager, anchor_root=normalized_anchor_root)
    selected_root = str(committed_policy.get("selected_root") or "").strip() if isinstance(committed_policy, dict) else ""
    if not selected_root or not paths_equal(normalize_runtime_root(selected_root), target_root):
        return []
    # Out of reach now (an unplugged drive): try again on a later launch.
    if classify_entry_no_follow(source_root) != "dir" or classify_entry_no_follow(target_root) != "dir":
        return []

    copied_entries = dict(copy_evidence_entries(payload.get("copied_entries")))
    candidates = [
        entry_name
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES
        if entry_name not in V1_MIGRATED_RUNTIME_ENTRY_NAMES
        and entry_name not in copied_entries
        and entry_parents_are_real_directories(source_root, entry_name)
        and os.path.lexists(source_root / entry_name)
    ]
    copied: list[str] = []
    skipped: list[str] = []
    if candidates:
        # Recorded first, so a stopped attempt's stage is found and removed
        # as a finished checkpoint's transaction leftover.
        txid = uuid.uuid4().hex
        payload = _persist_migration_payload(config_manager, payload, anchor_root=normalized_anchor_root, txid=txid)
        transaction_root = _transaction_path(target_root, txid)
        _ensure_transaction_parent(transaction_root)
        stage_root = transaction_root / "stage"
        try:
            for entry_name in candidates:
                target_entry = target_root / entry_name
                replaces_scaffold = os.path.lexists(target_entry)
                if replaces_scaffold and (
                    classify_entry_no_follow(target_entry) != "dir" or _tree_has_user_file(target_entry)
                ):
                    skipped.append(entry_name)
                    continue
                source_entry = source_root / entry_name
                fingerprint = _metadata_fingerprint(source_entry)
                staged_entry = stage_root / entry_name
                source_manifest, widened = _copy_and_verify_entry(source_entry, staged_entry)
                if _metadata_fingerprint(source_entry) != fingerprint:
                    raise StorageMigrationError(
                        "verification_failed",
                        f"补迁旧版本未迁移的数据时校验失败，已停止，原数据未受影响：{entry_name}。",
                    )
                ensure_entry_parents(target_root, entry_name)
                scaffold_in_trash = transaction_root / "trash" / entry_name
                if replaces_scaffold:
                    # Copied and verified first, so a failed copy never
                    # leaves the new root without the directory.
                    (transaction_root / "trash").mkdir(exist_ok=True)
                    ensure_entry_parents(transaction_root / "trash", entry_name)
                    _move_entry_keeping_mode(target_entry, scaffold_in_trash)
                    # Checked again now that nothing can write into it: a file
                    # that arrived since makes it the new root's own data.
                    if _tree_has_user_file(scaffold_in_trash):
                        _publish_without_overwrite(scaffold_in_trash, target_entry)
                        skipped.append(entry_name)
                        continue
                staged_fingerprint = _metadata_fingerprint(staged_entry, across_move=True)
                try:
                    _publish_without_overwrite(staged_entry, target_entry)
                except FileExistsError:
                    # Appeared meanwhile: the new root's own, left alone.
                    skipped.append(entry_name)
                    continue
                except BaseException:
                    if replaces_scaffold:
                        with suppress(OSError):
                            _publish_without_overwrite(scaffold_in_trash, target_entry)
                    raise
                if _metadata_fingerprint(target_entry, across_move=True) != staged_fingerprint:
                    # Written to the moment it went live: no proof of the copy,
                    # so the old copy stays.
                    for relative_dir, original_mode in widened:
                        with suppress(OSError):
                            os.chmod(target_entry / relative_dir, original_mode)
                    continue
                for relative_dir, original_mode in widened:
                    os.chmod(target_entry / relative_dir, original_mode)
                # The verified staged copy itself was moved into place.
                target_manifest = source_manifest
                copied_entries[entry_name] = {
                    "source_manifest": source_manifest,
                    "target_manifest": target_manifest,
                    "transaction": txid,
                }
                payload = _persist_migration_payload(
                    config_manager,
                    payload,
                    anchor_root=normalized_anchor_root,
                    copied_entries=dict(copied_entries),
                )
                copied.append(entry_name)
        finally:
            try:
                _remove_transaction(transaction_root)
            except Exception as exc:
                logger.warning("Failed to remove the v1 catch-up transaction: %s", exc)
    # One that turned up in the old root meanwhile (a sync client) would
    # otherwise be neither copied nor named; the next launch takes it.
    turned_up = [
        entry_name
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES
        if entry_name not in V1_MIGRATED_RUNTIME_ENTRY_NAMES
        and entry_name not in copied_entries
        and entry_name not in skipped
        and entry_name not in candidates
        and os.path.lexists(source_root / entry_name)
    ]
    if turned_up:
        _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            v1_catch_up_skipped=skipped,
        )
        return copied
    _persist_migration_payload(
        config_manager,
        payload,
        anchor_root=normalized_anchor_root,
        v1_catch_up_completed_at=_utc_now_iso(),
        v1_catch_up_skipped=skipped,
    )
    return copied


def v1_catch_up_skipped_entries(payload: dict[str, Any] | None) -> list[str]:
    """Entries the v1 catch-up left in the old root: the new root had its own."""
    if not isinstance(payload, dict):
        return []
    return [
        str(entry_name)
        for entry_name in payload.get("v1_catch_up_skipped") or []
        if str(entry_name) in MIGRATED_RUNTIME_ENTRY_NAMES
    ]


def run_pending_storage_migration(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
) -> dict[str, Any]:
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager)
    )
    if hasattr(config_manager, "anchor_root"):
        config_manager.anchor_root = normalized_anchor_root

    migration_payload = load_storage_migration(
        config_manager,
        anchor_root=normalized_anchor_root,
    )
    if not is_storage_migration_pending(migration_payload):
        _remove_completed_transaction_leftover(migration_payload)
        try:
            if catch_up_v1_migration(config_manager, anchor_root=normalized_anchor_root):
                logger.info("Copied entries a v1 storage migration had left in the old root")
        except Exception as exc:
            logger.warning("Failed to copy what a v1 storage migration left behind: %s", exc)
        try:
            reconcile_finished_retained_cleanup(config_manager, anchor_root=normalized_anchor_root)
        except Exception as exc:
            logger.warning("Failed to reconcile a finished retained-root cleanup: %s", exc)
        # Both steps above may have updated the checkpoint.
        migration_payload = load_storage_migration(config_manager, anchor_root=normalized_anchor_root)
        return {
            "attempted": False,
            "completed": False,
            "payload": migration_payload,
            "anchor_root": str(normalized_anchor_root),
        }

    payload = dict(migration_payload or {})
    source_root: Path | None = None
    target_root: Path | None = None
    policy_payload: dict[str, Any] | None = None

    def _finish_failure(error_code: str, error_message: str) -> dict[str, Any]:
        nonlocal payload, policy_payload
        raw_payload_source_root = str(payload.get("source_root") or "").strip()
        if source_root is not None:
            recovery_source_root = str(source_root)
        else:
            fallback_root = str(getattr(config_manager, "app_docs_dir", "") or "").strip()
            recovery_source_root = raw_payload_source_root or fallback_root or str(normalized_anchor_root)
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_FAILED,
            backup_root=recovery_source_root,
            error_code=error_code,
            error_message=error_message,
            failed_at=_utc_now_iso(),
        )

        if source_root is not None:
            try:
                policy_payload = save_storage_policy(
                    config_manager,
                    selected_root=source_root,
                    selection_source=POLICY_SELECTION_SOURCE_RECOVERED,
                    anchor_root=normalized_anchor_root,
                )
            except Exception as policy_exc:
                logger.warning("Failed to persist recovered storage policy after migration failure: %s", policy_exc)

        try:
            from utils.cloudsave_runtime import ROOT_MODE_DEFERRED_INIT, set_root_mode

            set_root_mode(
                config_manager,
                ROOT_MODE_DEFERRED_INIT,
                current_root=recovery_source_root,
                last_known_good_root=recovery_source_root,
                last_migration_source=recovery_source_root,
                last_migration_result=f"failed:{error_code}",
                last_migration_backup=recovery_source_root,
                legacy_cleanup_pending=False,
            )
        except Exception as root_state_exc:
            logger.warning("Failed to persist recovery root_state after migration failure: %s", root_state_exc)

        return {
            "attempted": True,
            "completed": False,
            "payload": payload,
            "policy": policy_payload,
            "source_root": str(source_root) if source_root else "",
            "target_root": str(target_root) if target_root else "",
            "anchor_root": str(normalized_anchor_root),
            "error_code": error_code,
            "error_message": error_message,
        }

    def _finish_retryable(error_code: str, error_message: str) -> dict[str, Any]:
        nonlocal payload
        status = {
            "migration_rollback_required": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
            "migration_source_missing": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
            "migration_stage_unreadable": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
            "migration_publish_conflict": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
            # Keep COMMITTING so the next start decides again from the policy.
            "migration_commit_ambiguous": STORAGE_MIGRATION_STATUS_COMMITTING,
        }.get(error_code, STORAGE_MIGRATION_STATUS_PENDING)
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=status,
            error_code=error_code,
            error_message=error_message,
        )
        return {
            "attempted": True,
            "completed": False,
            "payload": payload,
            "policy": policy_payload,
            "source_root": str(source_root) if source_root else "",
            "target_root": str(target_root) if target_root else "",
            "anchor_root": str(normalized_anchor_root),
            "error_code": error_code,
            "error_message": error_message,
        }

    def _mark_restoring(entry_name: str) -> None:
        nonlocal payload
        restoring = [str(entry) for entry in payload.get("restoring_entries") or []]
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            restoring_entries=list(dict.fromkeys([*restoring, entry_name])),
        )

    def _finish_success(
        *,
        copied_entries: dict[str, dict[str, Any]],
        transaction_root: Path | None,
        persist_policy: bool,
        selection_source: str,
    ) -> dict[str, Any]:
        nonlocal payload, policy_payload
        assert source_root is not None and target_root is not None
        if persist_policy:
            assert transaction_root is not None
            try:
                policy_payload = save_storage_policy(
                    config_manager,
                    selected_root=target_root,
                    selection_source=selection_source,
                    anchor_root=normalized_anchor_root,
                )
            except Exception as exc:
                _rollback_publish_or_require_recovery(
                    payload=payload,
                    target_root=target_root,
                    transaction_root=transaction_root,
                    mark_restoring=_mark_restoring,
                )
                raise StorageMigrationError(
                    "policy_commit_failed",
                    f"存储策略提交失败，目标已恢复: {exc}",
                ) from exc
        else:
            policy_payload = load_storage_policy(
                config_manager,
                anchor_root=normalized_anchor_root,
            )

        try:
            prior_version = int(payload.get("version") or 1)
        except (TypeError, ValueError):
            prior_version = 1
        # A v1 checkpoint finished from COMMITTING carries no copy evidence.
        # Keep it classified as v1 so retained-root cleanup can still prove
        # entries by comparing both sides instead of refusing for good.
        has_copy_evidence = bool(copy_evidence_entries(copied_entries))
        completed_version = (
            STORAGE_MIGRATION_VERSION
            if has_copy_evidence or prior_version >= STORAGE_MIGRATION_VERSION
            else prior_version
        )
        has_cleanup_basis = (
            has_copy_evidence or completed_version < STORAGE_MIGRATION_VERSION
        )

        try:
            from utils.cloudsave_runtime import ROOT_MODE_NORMAL, set_root_mode

            legacy_cleanup_pending = has_cleanup_basis and is_retained_root_cleanup_available(
                source_root,
                current_root=target_root,
                anchor_root=normalized_anchor_root,
                target_root=target_root,
                require_exists=False,
                allow_anchor_root=True,
            )
            set_root_mode(
                config_manager,
                ROOT_MODE_NORMAL,
                current_root=str(target_root),
                last_known_good_root=str(target_root),
                last_migration_source=str(source_root),
                last_migration_result=f"completed:{target_root}",
                last_migration_backup=str(source_root),
                legacy_cleanup_pending=legacy_cleanup_pending,
            )
        except Exception as exc:
            logger.warning("Failed to persist successful storage migration root_state: %s", exc)

        completed_at = _utc_now_iso()
        try:
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                status=STORAGE_MIGRATION_STATUS_COMPLETED,
                backup_root=str(source_root),
                retained_source_root=str(source_root),
                retained_source_mode="manual_retention",
                error_code="",
                error_message="",
                committed_at=str(payload.get("committed_at") or completed_at),
                completed_at=completed_at,
                version=completed_version,
                copied_entries=copied_entries,
                published_entries=[],
                original_target_entries=[],
                original_target_modes={},
                publishing_entry="",
                publishing_target_existed=False,
                restoring_entries=[],
                publish_conflict_entry="",
                publish_reservation={},
                resuming_v1_copy=False,
                staged_source_manifests={},
                staged_target_manifests={},
            )
        except Exception as exc:
            logger.warning(
                "Storage policy committed but completion checkpoint is pending: %s",
                exc,
            )
            return {
                "attempted": True,
                "completed": False,
                "payload": payload,
                "policy": policy_payload,
                "source_root": str(source_root),
                "target_root": str(target_root),
                "anchor_root": str(normalized_anchor_root),
                "error_code": "migration_commit_pending",
                "error_message": "存储策略已提交，等待补齐迁移完成检查点。",
            }
        if transaction_root is not None:
            try:
                _remove_transaction(transaction_root)
            except Exception as exc:
                logger.warning("Failed to remove completed storage migration transaction: %s", exc)
        return {
            "attempted": True,
            "completed": True,
            "payload": payload,
            "policy": policy_payload,
            "source_root": str(source_root),
            "target_root": str(target_root),
            "anchor_root": str(normalized_anchor_root),
        }

    transaction_root: Path | None = None

    def _cleanup_unpublished_transaction() -> None:
        if transaction_root is None:
            return
        status = str(payload.get("status") or "").strip().lower()
        if status not in {
            STORAGE_MIGRATION_STATUS_PREFLIGHT,
            STORAGE_MIGRATION_STATUS_COPYING,
        }:
            return
        try:
            _remove_transaction(transaction_root)
        except Exception as cleanup_exc:
            logger.warning(
                "Failed to remove unpublished storage migration transaction: %s",
                cleanup_exc,
            )

    try:
        source_root = normalize_runtime_root(str(payload.get("source_root") or "").strip())
        target_root = normalize_runtime_root(str(payload.get("target_root") or "").strip())
        selection_source = _normalize_selection_source(str(payload.get("selection_source") or ""))
        checkpoint_status = str(payload.get("status") or "").strip().lower()
        try:
            checkpoint_version = int(payload.get("version") or 1)
        except (TypeError, ValueError):
            checkpoint_version = 1
        # v1 copied straight into the target. A v1 run that stopped in COPYING
        # left only its own partial copy there: never reuse it as an existing
        # target, overwrite it (replaced entries still go to the backup).
        # The first v2 attempt rewrites version and status, so the finding is
        # kept in the checkpoint: a second interruption must not lose it.
        resuming_v1_copy = payload.get("resuming_v1_copy") is True or (
            checkpoint_version < STORAGE_MIGRATION_VERSION
            and checkpoint_status == STORAGE_MIGRATION_STATUS_COPYING
        )

        if paths_equal(source_root, target_root):
            raise StorageMigrationError("target_matches_source", "目标路径与当前路径一致，不需要执行迁移。")
        if _path_contains(source_root, target_root) or _path_contains(target_root, source_root):
            raise StorageMigrationError("paths_nested", "源路径和目标路径不能互相包含，无法安全执行迁移。")

        txid = str(payload.get("txid") or "").strip()
        if not txid:
            # A v1 checkpoint has no transaction id. Record the new one before
            # any transaction directory exists: a later launch must find this
            # attempt's .smtx/<id> again rather than invent another id and
            # strand the staged copies and backups under the first.
            txid = uuid.uuid4().hex
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                txid=txid,
            )
        committed_policy = load_storage_policy(
            config_manager,
            anchor_root=normalized_anchor_root,
        )
        policy_selected_root = (
            str(committed_policy.get("selected_root") or "").strip()
            if isinstance(committed_policy, dict)
            else ""
        )
        copied_checkpoint = payload.get("copied_entries")
        if (
            checkpoint_status == STORAGE_MIGRATION_STATUS_COMMITTING
            and policy_selected_root
            and paths_equal(policy_selected_root, target_root)
        ):
            # COMMITTING is written only after every entry is published, and
            # the policy already points at the target: the launcher may have
            # run services on it since. Never roll that back -- only finish
            # the checkpoint. Cleanup re-checks each entry before deleting.
            # The retained source is not needed for this, so a deleted or
            # unplugged source must not send the policy back to it.
            try:
                committed_transaction_root: Path | None = _transaction_path(target_root, txid)
            except StorageMigrationError:
                committed_transaction_root = None
            return _finish_success(
                copied_entries=(
                    dict(copied_checkpoint)
                    if isinstance(copied_checkpoint, dict)
                    else {}
                ),
                transaction_root=committed_transaction_root,
                persist_policy=False,
                selection_source=selection_source,
            )
        if checkpoint_status == STORAGE_MIGRATION_STATUS_COMMITTING:
            # Rolling back is safe only while the policy demonstrably still
            # selects the source (or was never written). An unreadable policy
            # or one naming another root may already be committed -- with
            # services writing to the target -- so wait instead of guessing.
            policy_absent = committed_policy is None and _path_is_absent(
                get_storage_policy_path(config_manager, anchor_root=normalized_anchor_root)
            )
            policy_selects_source = bool(policy_selected_root) and paths_equal(
                policy_selected_root, source_root
            )
            if not (policy_absent or policy_selects_source):
                raise StorageMigrationError(
                    "migration_commit_ambiguous",
                    "无法确认存储策略是否已提交，暂不回滚，等待下次启动重试。",
                )
        transaction_root = _transaction_path(target_root, txid)
        _ensure_transaction_parent(transaction_root)
        source_root_present = source_root.exists() and source_root.is_dir()
        if os.path.lexists(transaction_root):
            _ensure_transaction_dirs_not_linked(transaction_root)
            # Rolling back restores the state before this migration, and that
            # state lived in the source. If the source -- or just one of the
            # entries this transaction published or staged -- is gone or no
            # longer what was copied, those copies may be the only complete
            # ones left: keep them all and stay retryable until the source is
            # restored or someone sorts it out.
            missing_source_entries = _transaction_entries_diverged_from_source(
                payload=payload,
                source_root=source_root,
                transaction_root=transaction_root,
            )
            if not source_root_present or missing_source_entries:
                raise StorageMigrationError(
                    "migration_source_missing",
                    "原始数据目录或其中的条目不存在，迁移未完成，已保留目标与事务目录，恢复后会继续处理: "
                    + (", ".join(missing_source_entries) or str(source_root)),
                )
        if not source_root_present:
            raise StorageMigrationError("source_root_missing", "原始数据目录不存在，无法继续迁移。")
        conflict_entry = str(payload.get("publish_conflict_entry") or "")
        if conflict_entry and os.path.lexists(transaction_root):
            # A rollback would delete what was recreated at the target; leave
            # every copy alone until someone resolves the conflict by hand.
            raise StorageMigrationError(
                "migration_publish_conflict",
                f"迁移目标在发布期间被重新创建，原目标已在事务备份中，等待人工处理: {conflict_entry}",
            )
        if os.path.lexists(transaction_root):
            _rollback_publish_or_require_recovery(
                payload=payload,
                target_root=target_root,
                transaction_root=transaction_root,
                mark_restoring=_mark_restoring,
            )
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                status=STORAGE_MIGRATION_STATUS_PENDING,
                copied_entries={},
                published_entries=[],
                original_target_entries=[],
                original_target_modes={},
                publishing_entry="",
                publishing_target_existed=False,
                restoring_entries=[],
                error_code="",
                error_message="",
            )

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_PREFLIGHT,
            started_at=str(payload.get("started_at") or _utc_now_iso()),
            source_root=str(source_root),
            target_root=str(target_root),
            resuming_v1_copy=resuming_v1_copy,
            # Reaching here means no transaction is left (rolled back, or removed
            # by hand after a conflict): this attempt starts from a clean record,
            # so nothing a previous attempt left behind can steer a later rollback.
            publish_conflict_entry="",
            publish_reservation={},
            staged_source_manifests={},
            staged_target_manifests={},
            copied_entries={},
            published_entries=[],
            original_target_entries=[],
            original_target_modes={},
            publishing_entry="",
            publishing_target_existed=False,
            restoring_entries=[],
            error_code="",
            error_message="",
        )

        target_has_user_content = _root_has_user_content(target_root, config_manager=config_manager)
        use_existing_target = (
            not resuming_v1_copy
            and target_has_user_content
            and selection_source in {"legacy", POLICY_SELECTION_SOURCE_RECOVERED}
        )
        confirmed_existing_target_content = resuming_v1_copy or bool(
            payload.get("confirmed_existing_target_content")
        )

        if target_has_user_content and not use_existing_target and not confirmed_existing_target_content:
            raise StorageMigrationError(
                "target_confirmation_required",
                "目标路径已经包含现有数据，需要先确认覆盖目标中的同名运行时数据目录。",
            )

        _ensure_target_root_writable(target_root)

        source_snapshots: dict[str, dict[str, int | str]] = {}
        existing_entries = _iter_existing_runtime_entries(source_root)

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_COPYING,
            version=STORAGE_MIGRATION_VERSION,
        )
        _remove_existing_path(transaction_root)
        stage_root = transaction_root / "stage"
        backup_root = transaction_root / "backup"
        stage_root.mkdir(parents=True)
        backup_root.mkdir(parents=True)
        staged_manifests: dict[str, dict[str, int | str]] = {}
        widened_modes: dict[str, list[tuple[Path, int]]] = {}
        staged_source_records: dict[str, dict[str, int | str]] = {}
        copied_entries: dict[str, dict[str, Any]] = {}
        entries_to_publish: list[str] = []
        original_target_entries: list[str] = []
        original_target_modes: dict[str, int] = {}
        identical_entries: dict[str, dict[str, Any]] = {}
        reused_target_manifests: dict[str, dict[str, int | str]] = {}
        # Target entries the migration keeps as they are instead of staging:
        # they must still be there when the roots are switched.
        reused_target_entries: set[str] = set()
        if use_existing_target:
            # Entries only the target has never enter the loop below, yet are
            # kept just the same.
            reused_target_entries.update(
                set(_iter_existing_runtime_entries(target_root)) - set(existing_entries)
            )
        source_fingerprints: dict[str, str] = {}
        for entry_name in existing_entries:
            source_entry = source_root / entry_name
            target_entry = target_root / entry_name
            # Taken before anything reads the entry, and compared once all
            # entries are staged: a write in between (a sync client, say)
            # would otherwise publish the copy taken before it.
            # Reusing a target, an entry it already holds is staged only when
            # it is config; nothing else there needs the fingerprint.
            if not (use_existing_target and os.path.lexists(target_entry)) or entry_name == "config":
                source_fingerprints[entry_name] = _metadata_fingerprint(source_entry)
            # Read up front only where it is compared before copying (an entry
            # the reused target already has); otherwise the copy hashes it.
            source_manifest: dict[str, int | str] | None = None
            if use_existing_target and os.path.lexists(target_entry):
                source_manifest = _snapshot_path(source_entry)
                source_snapshots[entry_name] = source_manifest
                target_manifest = _snapshot_path(target_entry)
                if target_manifest != source_manifest:
                    # Existing legacy/recovered entries are authoritative. They
                    # do not prove the source copy and are not cleanup-safe.
                    reused_target_entries.add(entry_name)
                    continue
                if entry_name != "config":
                    reused_target_entries.add(entry_name)
                    identical_entries[entry_name] = {
                        "source_manifest": source_manifest,
                        "target_manifest": target_manifest,
                        "transaction": str(payload.get("txid") or ""),
                    }
                    continue
                # An identical config still carries workshop paths bound to the
                # source: stage and publish it like any copy so they are rebased
                # (the target's own copy goes to the backup as usual).
                reused_target_manifests[entry_name] = target_manifest
            staged_entry = stage_root / entry_name
            source_manifest, widened_modes[entry_name] = _copy_and_verify_entry(
                source_entry, staged_entry, expected_manifest=source_manifest
            )
            source_snapshots[entry_name] = source_manifest
            staged_manifest = source_manifest
            if entry_name == "config":
                # Verify the verbatim copy first; only then apply the one
                # intended change and take the manifest the target must match.
                _rewrite_migrated_runtime_config_paths(
                    source_root=source_root,
                    target_root=target_root,
                    config_root=stage_root,
                )
                staged_manifest = _snapshot_path(staged_entry)
            staged_manifests[entry_name] = staged_manifest
            staged_source_records[entry_name] = source_manifest
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                staged_source_manifests=dict(staged_source_records),
                # What the entry must look like once published: recovery
                # compares a target it moved in but had not yet recorded.
                staged_target_manifests=dict(staged_manifests),
            )
            entries_to_publish.append(entry_name)
            if os.path.lexists(target_entry):
                original_target_entries.append(entry_name)

        # Taken right before the kept target config is scanned for paths into
        # the source; set once that scan has run.
        kept_config_fingerprint: str | None = None

        def _require_sources_unchanged() -> None:
            appeared = set(_iter_existing_runtime_entries(source_root)) - set(existing_entries)
            if appeared:
                # Absent when the source was first listed, so never staged: the
                # target would go live without it.
                raise StorageMigrationError(
                    "verification_failed",
                    "迁移期间原始数据目录出现了新条目，已停止迁移，原始数据未受影响："
                    + ", ".join(sorted(appeared)) + "。",
                )
            for entry_name in entries_to_publish:
                if not os.path.lexists(source_root / entry_name):
                    # Gone from the source: the staged or published copy may
                    # be the only one left, so keep the transaction for
                    # recovery instead of undoing it.
                    raise StorageMigrationError(
                        "migration_source_missing",
                        "原始数据目录中的条目在迁移期间消失，迁移未完成，已保留目标与事务目录，恢复后会继续处理: "
                        + entry_name,
                    )
                try:
                    unchanged = _metadata_fingerprint(source_root / entry_name) == source_fingerprints.get(entry_name)
                except StorageMigrationError:
                    unchanged = False
                if not unchanged:
                    raise StorageMigrationError(
                        "verification_failed",
                        f"迁移期间原始数据被修改，已停止迁移，原始数据未受影响：{entry_name}。",
                    )
            for entry_name in sorted(reused_target_entries):
                if not os.path.lexists(target_root / entry_name):
                    # Reused as the target's own copy, not staged: gone now,
                    # switching roots would leave it out.
                    raise StorageMigrationError(
                        "target_changed_during_migration",
                        f"沿用的目标在迁移期间少了条目，已停止迁移: {entry_name}",
                    )
            if kept_config_fingerprint is not None:
                # Edited after it was scanned, the kept config may now point
                # at a source entry recorded as copy evidence, which cleanup
                # would then delete from under it.
                try:
                    config_unchanged = _metadata_fingerprint(target_root / "config") == kept_config_fingerprint
                except StorageMigrationError:
                    config_unchanged = False
                if not config_unchanged:
                    raise StorageMigrationError(
                        "target_changed_during_migration",
                        "沿用的目标 config 在迁移期间被修改，已停止迁移。",
                    )

        _require_sources_unchanged()

        # Identical entries become copy evidence, so cleanup may delete their
        # source copy -- except where the target's own config, kept as it is
        # (it differs from the source, or the source has none), still points
        # into the source: that copy is still in use.
        referenced_by_kept_config: set[str] = set()
        if use_existing_target and "config" not in entries_to_publish:
            if os.path.lexists(target_root / "config"):
                kept_config_fingerprint = _metadata_fingerprint(target_root / "config")
            referenced_by_kept_config = _source_entries_referenced_by_config(
                config_root=target_root / "config",
                source_root=source_root,
            )
        for entry_name, proof in identical_entries.items():
            if entry_name not in referenced_by_kept_config:
                copied_entries[entry_name] = proof

        # Reusing an existing target only makes sense if it still holds runtime
        # data. Check again here, after staging (whose conflict checks give the
        # more specific errors) and before anything is published: the target's
        # own entries -- the ones staging left alone -- may have gone in the
        # meantime, and a failure from VERIFYING on keeps the transaction for
        # recovery, so published entries and their backup would be stranded.
        # The dot-named transaction directory does not count as content.
        if use_existing_target and not _root_has_user_content(
            target_root, config_manager=config_manager
        ):
            raise StorageMigrationError(
                "target_missing_runtime",
                "目标路径没有可用数据，无法直接切换到现有目录。",
            )

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_VERIFYING,
            backup_root=str(source_root),
            copied_entries=copied_entries,
            original_target_entries=original_target_entries,
            published_entries=[],
        )

        published_entries: list[str] = []
        target_entries_at_staging = set(original_target_entries)
        try:
            for entry_name in entries_to_publish:
                target_entry = target_root / entry_name
                backup_entry = backup_root / entry_name
                ensure_entry_parents(target_root, entry_name)
                target_existed = os.path.lexists(target_entry)
                reused_manifest = reused_target_manifests.get(entry_name)
                if reused_manifest is not None and (
                    not target_existed or _snapshot_path(target_entry) != reused_manifest
                ):
                    # The reused target's own config was staged only because
                    # it matched the source; changed or gone since (a sync
                    # client), it must not be replaced by the staged copy.
                    raise StorageMigrationError(
                        "target_changed_during_migration",
                        f"沿用的目标在迁移期间被改动，已停止迁移: {entry_name}",
                    )
                if use_existing_target and target_existed and entry_name not in target_entries_at_staging:
                    # Reusing a target makes its entries authoritative; one
                    # that appeared after staging (a sync client) must not be
                    # replaced by the source copy and then dropped with the
                    # backup. Stop instead; nothing is published yet for it.
                    raise StorageMigrationError(
                        "target_changed_during_migration",
                        f"沿用的目标在迁移期间出现了新条目，已停止迁移: {entry_name}",
                    )
                # Record what the target holds right now, not what staging saw:
                # rollback restores from this, and an entry that appeared since
                # would otherwise be deleted together with its backup.
                if target_existed and entry_name not in original_target_entries:
                    original_target_entries.append(entry_name)
                elif not target_existed and entry_name in original_target_entries:
                    original_target_entries.remove(entry_name)
                # Moving a read-only directory into the backup widens its mode
                # for the move; record the original first, so a process exit
                # in between cannot leave it widened for good.
                original_target_modes.pop(entry_name, None)
                if target_existed and classify_entry_no_follow(target_entry) == "dir":
                    original_target_modes[entry_name] = stat.S_IMODE(target_entry.lstat().st_mode)
                payload = _persist_migration_payload(
                    config_manager,
                    payload,
                    anchor_root=normalized_anchor_root,
                    original_target_entries=list(original_target_entries),
                    original_target_modes=dict(original_target_modes),
                    publishing_entry=entry_name,
                    publishing_target_existed=target_existed,
                    publish_reservation={},
                )
                if target_existed:
                    _classify_no_follow(target_entry)
                    ensure_entry_parents(backup_root, entry_name)
                    _move_entry_keeping_mode(target_entry, backup_entry)

                def _record_reservation(reservation_stat: os.stat_result, entry_name: str = entry_name) -> None:
                    nonlocal payload
                    payload = _persist_migration_payload(
                        config_manager,
                        payload,
                        anchor_root=normalized_anchor_root,
                        publish_reservation={
                            "entry": entry_name,
                            "dev": reservation_stat.st_dev,
                            "ino": reservation_stat.st_ino,
                        },
                    )

                # Nothing writes into the private stage; what the target holds
                # right after the move must still be exactly this.
                staged_fingerprint = _metadata_fingerprint(stage_root / entry_name, across_move=True)
                try:
                    _publish_without_overwrite(stage_root / entry_name, target_entry, reserved=_record_reservation)
                except FileExistsError as exc:
                    if target_existed:
                        # The original is in the backup and something new now
                        # sits at its place. Rolling back would delete the
                        # newcomer to restore the original; keep both and wait
                        # for a person to decide instead.
                        payload = _persist_migration_payload(
                            config_manager,
                            payload,
                            anchor_root=normalized_anchor_root,
                            publish_conflict_entry=entry_name,
                        )
                        raise StorageMigrationError(
                            "migration_publish_conflict",
                            f"迁移目标在发布期间被重新创建，原目标已在事务备份中，已停止迁移等待人工处理: {entry_name}",
                        ) from exc
                    if not target_existed:
                        # Nothing of ours is at the target: what is there
                        # appeared after the check above, so rollback must
                        # leave it alone rather than delete it as ours.
                        payload = _persist_migration_payload(
                            config_manager,
                            payload,
                            anchor_root=normalized_anchor_root,
                            publishing_entry="",
                            publishing_target_existed=False,
                        )
                    raise StorageMigrationError(
                        "target_changed_during_migration",
                        f"迁移目标在发布期间出现了新条目，已停止迁移: {entry_name}",
                    ) from exc
                published_entries.append(entry_name)
                # The publish moved the staged copy itself, verified just
                # before; reading it a third time would prove nothing more.
                actual_manifest = staged_manifests[entry_name]
                # A write right after it went live (a sync client) shows in the
                # metadata: then the staged manifest no longer describes it.
                if (
                    classify_entry_no_follow(target_entry) != actual_manifest.get("kind")
                    or _metadata_fingerprint(target_entry, across_move=True) != staged_fingerprint
                ):
                    raise StorageMigrationError(
                        "verification_failed",
                        f"迁移发布校验失败：{entry_name}。",
                    )
                # Children were recorded first, so a read-only parent is
                # restored only after everything below it.
                for relative_dir, original_mode in widened_modes.get(entry_name, []):
                    os.chmod(target_entry / relative_dir, original_mode)
                # Rollback still needs the published manifest; cleanup must not
                # delete a source entry the kept target config points into.
                if entry_name not in referenced_by_kept_config:
                    copied_entries[entry_name] = {
                        "source_manifest": source_snapshots[entry_name],
                        "target_manifest": actual_manifest,
                        "transaction": str(payload.get("txid") or ""),
                    }
                payload = _persist_migration_payload(
                    config_manager,
                    payload,
                    anchor_root=normalized_anchor_root,
                    copied_entries=dict(copied_entries),
                    published_entries=list(published_entries),
                    publishing_entry="",
                    publishing_target_existed=False,
                )
            # Publishing hashes every target and takes a while: a source
            # written meanwhile must not go live as its older copy. Raised
            # here, the publish is rolled back like any other failure.
            _require_sources_unchanged()
            # Until the policy points at the target, a failed checkpoint
            # write must undo the publish like any other failure here.
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                status=STORAGE_MIGRATION_STATUS_COMMITTING,
                committed_at=_utc_now_iso(),
            )
        except Exception as exc:
            if isinstance(exc, StorageMigrationError) and exc.error_code in {
                "migration_publish_conflict",
                "migration_source_missing",
            }:
                raise
            _rollback_publish_or_require_recovery(
                payload=payload,
                target_root=target_root,
                transaction_root=transaction_root,
                mark_restoring=_mark_restoring,
            )
            raise

        return _finish_success(
            copied_entries=copied_entries,
            transaction_root=transaction_root,
            persist_policy=True,
            selection_source=selection_source,
        )
    except StorageMigrationError as exc:
        if exc.error_code not in {"migration_source_missing", "migration_stage_unreadable"}:
            _cleanup_unpublished_transaction()
        if exc.error_code in {
            "migration_rollback_required",
            "migration_commit_ambiguous",
            "migration_source_missing",
            "migration_stage_unreadable",
            "migration_publish_conflict",
        }:
            return _finish_retryable(exc.error_code, exc.message)
        return _finish_failure(exc.error_code, exc.message)
    except Exception as exc:
        _cleanup_unpublished_transaction()
        logger.exception("Unexpected storage migration failure")
        wrapped_exc = StorageMigrationError("storage_migration_unexpected", f"执行存储迁移时发生未预期错误: {exc}")
        return _finish_failure(wrapped_exc.error_code, wrapped_exc.message)


def delete_storage_migration(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
) -> None:
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    try:
        os.unlink(migration_path)
    except FileNotFoundError:
        return
