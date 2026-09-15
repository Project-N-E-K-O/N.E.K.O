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
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_utils import (
    atomic_write_json,
    fsync_directory_best_effort,
    publish_without_replacing,
)
from utils.logger_config import get_module_logger
from .entries import (
    RUNTIME_STORAGE_ENTRIES,
    RUNTIME_STORAGE_RELATIVE_PATHS,
    RuntimeStorageEntry,
    RuntimeStorageEntryBoundaryError,
    checked_runtime_entry_path,
)
from .policy import (
    POLICY_SELECTION_SOURCE_RECOVERED,
    PathIdentityUnavailable,
    compute_anchor_root,
    normalize_runtime_root,
    path_chain_has_symlink,
    path_is_within,
    paths_equal,
    read_fixed_anchor_state_json,
    save_storage_policy,
)
from .path_rewrite import rebase_runtime_bound_workshop_config_paths

logger = get_module_logger(__name__)

STORAGE_MIGRATION_VERSION = 2

STORAGE_MIGRATION_MODE_COPY = "copy"

STORAGE_MIGRATION_STATUS_PENDING = "pending"
STORAGE_MIGRATION_STATUS_PREFLIGHT = "preflight"
STORAGE_MIGRATION_STATUS_COPYING = "copying"
STORAGE_MIGRATION_STATUS_VERIFYING = "verifying"
STORAGE_MIGRATION_STATUS_PUBLISHING = "publishing"
STORAGE_MIGRATION_STATUS_COMMITTING = "committing"
STORAGE_MIGRATION_STATUS_RETAINING_SOURCE = "retaining_source"
STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED = "rollback_required"
STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED = "recovery_required"
STORAGE_MIGRATION_STATUS_FAILED = "failed"
STORAGE_MIGRATION_STATUS_COMPLETED = "completed"

ACTIVE_STORAGE_MIGRATION_STATUSES = frozenset(
    {
        STORAGE_MIGRATION_STATUS_PENDING,
        STORAGE_MIGRATION_STATUS_PREFLIGHT,
        STORAGE_MIGRATION_STATUS_COPYING,
        STORAGE_MIGRATION_STATUS_VERIFYING,
        STORAGE_MIGRATION_STATUS_PUBLISHING,
        STORAGE_MIGRATION_STATUS_COMMITTING,
        STORAGE_MIGRATION_STATUS_RETAINING_SOURCE,
        STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
        STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
    }
)

MIGRATED_RUNTIME_ENTRY_NAMES = RUNTIME_STORAGE_RELATIVE_PATHS
_TRANSACTION_OWNER_MARKER_FILENAME = ".neko-storage-transaction-owner.json"
_TRANSACTION_OWNER_MARKER_VERSION = 1


class StorageMigrationError(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = str(error_code or "storage_migration_failed").strip() or "storage_migration_failed"
        self.message = str(message or "Storage migration failed.").strip() or "Storage migration failed."


def _is_link_like_metadata(metadata: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(metadata.st_mode)
        or int(getattr(metadata, "st_file_attributes", 0) or 0)
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    )


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
    return path_is_within(child, parent) and not paths_equal(parent, child)


def is_retained_root_cleanup_available(
    retained_root: Path | str | None,
    *,
    current_root: Path | str,
    anchor_root: Path | str,
    target_root: Path | str | None = None,
    require_exists: bool = True,
    allow_anchor_root: bool = False,
    anchor_has_managed_private_state: bool = False,
) -> bool:
    raw_retained_root = str(retained_root or "").strip()
    if not raw_retained_root:
        return False

    normalized_retained_root = normalize_runtime_root(raw_retained_root)
    if require_exists and not normalized_retained_root.exists():
        return False

    normalized_current_root = normalize_runtime_root(current_root)
    normalized_anchor_root = normalize_runtime_root(anchor_root)
    try:
        if paths_equal(normalized_retained_root, normalized_current_root):
            return False
        if _path_contains(
            normalized_retained_root, normalized_current_root
        ) or _path_contains(normalized_current_root, normalized_retained_root):
            return False
        if paths_equal(normalized_retained_root, normalized_anchor_root):
            if not allow_anchor_root:
                return False
            return bool(anchor_has_managed_private_state) or any(
                (normalized_retained_root / name).exists()
                for name in MIGRATED_RUNTIME_ENTRY_NAMES
            )
        if _path_contains(
            normalized_retained_root, normalized_anchor_root
        ) or _path_contains(normalized_anchor_root, normalized_retained_root):
            return False

        raw_target_root = str(target_root or "").strip()
        if raw_target_root:
            normalized_target_root = normalize_runtime_root(raw_target_root)
            if paths_equal(normalized_retained_root, normalized_target_root):
                return False
            if _path_contains(
                normalized_retained_root, normalized_target_root
            ) or _path_contains(normalized_target_root, normalized_retained_root):
                return False
    except PathIdentityUnavailable:
        # Cleanup is destructive.  Unknown identity is a refusal, never an
        # invitation to treat the path as unrelated to a protected root.
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
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        removal_root = Path(path)

        def _make_owned_directory_traversable(candidate: Path) -> None:
            try:
                candidate.relative_to(removal_root)
            except ValueError as exc:
                raise OSError("refusing to chmod outside owned removal tree") from exc
            metadata = candidate.lstat()
            file_attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
            is_link_like = stat.S_ISLNK(metadata.st_mode) or bool(
                file_attributes
                & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
            )
            if is_link_like or not stat.S_ISDIR(metadata.st_mode):
                raise OSError("refusing to chmod unsafe owned removal directory")
            mode = stat.S_IMODE(metadata.st_mode)
            os.chmod(
                candidate,
                mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
            )

        # Repair owned directories top-down before rmtree. Its onerror callback
        # cannot recover arbitrary consecutive 0400 directories in one walk:
        # once a parent scan is skipped, that traversal never revisits children.
        _make_owned_directory_traversable(removal_root)
        for walked_root, directory_names, _file_names in os.walk(
            removal_root,
            topdown=True,
            followlinks=False,
        ):
            walked_path = Path(walked_root)
            traversable_names: list[str] = []
            for directory_name in directory_names:
                candidate = walked_path / directory_name
                metadata = candidate.lstat()
                file_attributes = int(
                    getattr(metadata, "st_file_attributes", 0) or 0
                )
                if stat.S_ISLNK(metadata.st_mode) or bool(
                    file_attributes
                    & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
                ):
                    continue
                _make_owned_directory_traversable(candidate)
                traversable_names.append(directory_name)
            # topdown os.walk reads this list after yielding. Prune every link
            # and Windows reparse point explicitly so it never descends into an
            # external target that merely reports is_dir=True.
            directory_names[:] = traversable_names

        def _make_owned_entry_removable(func, raw_path, _exc_info) -> None:
            candidate = Path(raw_path)
            try:
                candidate.relative_to(removal_root)
            except ValueError as exc:
                raise OSError("refusing to chmod outside owned removal tree") from exc

            # A directory without owner execute permission can make lstat on
            # one of its children fail. Repair the owned parent first, while
            # still refusing to follow links or Windows reparse points.
            parent = candidate.parent
            if parent == removal_root or removal_root in parent.parents:
                parent_metadata = parent.lstat()
                parent_attributes = int(
                    getattr(parent_metadata, "st_file_attributes", 0) or 0
                )
                parent_is_link_like = stat.S_ISLNK(parent_metadata.st_mode) or bool(
                    parent_attributes
                    & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
                )
                if parent_is_link_like or not stat.S_ISDIR(parent_metadata.st_mode):
                    raise OSError("refusing to chmod unsafe owned removal parent")
                parent_mode = stat.S_IMODE(parent_metadata.st_mode)
                os.chmod(
                    parent,
                    parent_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
                )

            metadata = candidate.lstat()
            file_attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
            is_link_like = stat.S_ISLNK(metadata.st_mode) or bool(
                file_attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
            )

            # Never chmod a symlink/junction: chmod may follow it and mutate an
            # unrelated target.  Making its owned parent writable is sufficient
            # for unlink/rmdir to retry safely.
            if not is_link_like:
                candidate_mode = stat.S_IMODE(metadata.st_mode)
                writable_mode = candidate_mode | stat.S_IRUSR | stat.S_IWUSR
                if stat.S_ISDIR(metadata.st_mode):
                    writable_mode |= stat.S_IXUSR
                os.chmod(candidate, writable_mode)
            func(raw_path)

        shutil.rmtree(path, onerror=_make_owned_entry_removable)
    else:
        path.unlink()
    if path.exists() or path.is_symlink():
        raise OSError(f"storage path cleanup did not remove {path}")
    fsync_directory_best_effort(path.parent)


def _fsync_migration_directory(path: Path) -> None:
    """Require POSIX directory durability; retain Windows best-effort semantics."""

    if sys.platform == "win32":
        fsync_directory_best_effort(path)
        return
    handle = -1
    try:
        handle = os.open(
            os.fspath(path),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(handle)
    except OSError as exc:
        raise StorageMigrationError(
            "target_flush_failed",
            f"迁移数据无法可靠写入目标磁盘: {path}: {exc}",
        ) from exc
    finally:
        if handle >= 0:
            with suppress(OSError):
                os.close(handle)


def _durable_replace(source: Path, target: Path) -> None:
    """Rename and flush both directory-entry sides where supported."""

    os.replace(source, target)
    # Persist the destination name before the source-name removal. If the
    # second barrier fails or power is lost between them, recovery may see two
    # names, but it never has to recover an entry whose only durable name was
    # removed first.
    _fsync_migration_directory(target.parent)
    if source.parent != target.parent:
        _fsync_migration_directory(source.parent)


def _durable_publish_without_replacing(source: Path, target: Path) -> None:
    """Publish a staged runtime entry without erasing a late external write."""

    if source.is_dir() or os.name != "nt":
        # Linux renameat2(RENAME_NOREPLACE) and macOS renameatx_np(RENAME_EXCL)
        # work for both files and directories.  Keeping a file publication to
        # one rename avoids the POSIX hard-link/unlink crash window where both
        # staged and public names temporarily identify the same inode.
        _rename_entry_without_replacing(source, target)
    else:
        # Preserve the existing Windows sharing-violation retry contract.
        publish_without_replacing(source, target)
    _fsync_migration_directory(target.parent)
    if source.parent != target.parent:
        _fsync_migration_directory(source.parent)


def _rename_entry_without_replacing(source: Path, target: Path) -> None:
    """Atomically move one directory entry, never replacing a late winner."""
    if os.name == "nt":
        # CPython's Windows os.rename refuses every existing destination.
        os.rename(source, target)
        return

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace directory rename unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_bytes, -100, target_bytes, 1)  # RENAME_NOREPLACE
    elif sys.platform == "darwin":
        renameatx_np = getattr(library, "renameatx_np", None)
        if renameatx_np is None:
            raise OSError(errno.ENOTSUP, "atomic exclusive directory rename unavailable")
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(-2, source_bytes, -2, target_bytes, 0x00000004)  # RENAME_EXCL
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory rename unavailable")
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), target)


def _durable_rename_without_replacing(source: Path, target: Path) -> None:
    """Move the named entry itself and flush both directory-entry sides."""
    _rename_entry_without_replacing(source, target)
    fsync_directory_best_effort(source.parent)
    if source.parent != target.parent:
        fsync_directory_best_effort(target.parent)


def _restore_quarantined_directory(quarantine: Path, original: Path) -> None:
    try:
        # Quarantine deals with an untrusted name that may have changed type.
        # Always rename the directory entry itself: the normal file publisher
        # may hard-link through a symlink on POSIX and thereby alter the object
        # we are trying to preserve.
        _durable_rename_without_replacing(quarantine, original)
    except Exception as exc:
        logger.error(
            "Preserving unverified storage directory at %s after restore failed: %s",
            quarantine,
            exc,
        )


def _private_directory_quarantine_path(path: Path) -> Path:
    return path.parent / f".{path.name}.deleting"


def _remove_private_directory_via_quarantine(
    path: Path,
    expected_identity: os.stat_result,
    *,
    verify_quarantine=None,
    remove_quarantine=None,
) -> bool:
    """Detach an owned name before recursive deletion and verify what moved."""
    quarantine = _private_directory_quarantine_path(path)
    if quarantine.exists() or quarantine.is_symlink():
        return False
    _durable_rename_without_replacing(path, quarantine)
    try:
        quarantined_identity = quarantine.lstat()
        valid = bool(os.path.samestat(expected_identity, quarantined_identity))
        if valid and callable(verify_quarantine):
            valid = bool(verify_quarantine(quarantine))
    except OSError:
        valid = False
    if not valid:
        _restore_quarantined_directory(quarantine, path)
        return False
    if callable(remove_quarantine):
        return bool(remove_quarantine(quarantine))
    _remove_existing_path(quarantine)
    return True


def _copy_runtime_entry(source_path: Path, target_path: Path) -> None:
    if path_chain_has_symlink(source_path):
        raise StorageMigrationError("source_symlink_unsupported", "迁移源目录包含符号链接，当前阶段暂不自动迁移。")

    if path_chain_has_symlink(target_path):
        raise StorageMigrationError(
            "target_symlink_unsupported",
            "迁移暂存目录包含符号链接或重解析点，已停止迁移。",
        )
    if target_path.exists() or target_path.is_symlink():
        raise StorageMigrationError(
            "staging_entry_exists",
            f"迁移暂存条目已存在，无法安全覆盖: {target_path}",
        )
    # Re-scan immediately before copy so a nested symlink/junction cannot be
    # dereferenced by copytree before the later staged verification sees it.
    _snapshot_path(source_path)
    if path_chain_has_symlink(target_path):
        raise StorageMigrationError(
            "target_symlink_unsupported",
            "迁移暂存目录包含符号链接或重解析点，已停止迁移。",
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if source_path.is_dir():
        # Preserve a link as a link if one appears after the pre-copy scan.  The
        # staged verification will then reject it instead of dereferencing an
        # external path into the migration.
        shutil.copytree(
            source_path,
            target_path,
            copy_function=_copy_staged_file_durably,
            symlinks=True,
        )
        return

    if source_path.is_file():
        _copy_staged_file_durably(source_path, target_path)
        return

    raise StorageMigrationError("source_entry_missing", f"迁移源条目不存在: {source_path}")


def _copy_open_file_xattrs(source_fd: int, target_fd: int) -> None:
    """Copy supported xattrs without resolving either file name again."""

    list_xattrs = getattr(os, "listxattr", None)
    get_xattr = getattr(os, "getxattr", None)
    set_xattr = getattr(os, "setxattr", None)
    if not all(callable(item) for item in (list_xattrs, get_xattr, set_xattr)):
        return
    ignored_errors = {
        getattr(errno, name)
        for name in ("ENOTSUP", "ENODATA", "EINVAL", "EPERM")
        if hasattr(errno, name)
    }
    try:
        names = list_xattrs(source_fd)
    except OSError as exc:
        if exc.errno in ignored_errors:
            return
        raise
    for name in names:
        try:
            set_xattr(target_fd, name, get_xattr(source_fd, name))
        except OSError as exc:
            if exc.errno not in ignored_errors:
                raise


def _windows_copied_file_attributes(
    target_attributes: int,
    source_attributes: int,
) -> int:
    file_attribute_readonly = 0x00000001
    file_attribute_normal = 0x00000080
    # FILE_ATTRIBUTE_NORMAL is valid only by itself. Remove it before adding a
    # copied READONLY bit, then restore NORMAL only when no other attribute is
    # present on the newly-created target.
    merged = int(target_attributes) & ~(
        file_attribute_readonly | file_attribute_normal
    )
    merged |= int(source_attributes) & file_attribute_readonly
    return merged or file_attribute_normal


def _copy_windows_open_file_metadata(source_fd: int, target_fd: int) -> None:
    """Copy the Win32 metadata that copystat preserves through exact handles."""

    import msvcrt
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("file_attributes", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_file_information = kernel32.GetFileInformationByHandleEx
    get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    get_file_information.restype = wintypes.BOOL
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_file_information.restype = wintypes.BOOL

    source_info = _FileBasicInfo()
    target_info = _FileBasicInfo()
    information_size = ctypes.sizeof(_FileBasicInfo)
    source_handle = msvcrt.get_osfhandle(source_fd)
    target_handle = msvcrt.get_osfhandle(target_fd)
    for handle, info in ((source_handle, source_info), (target_handle, target_info)):
        if not get_file_information(
            handle,
            0,  # FileBasicInfo
            ctypes.byref(info),
            information_size,
        ):
            error_number = ctypes.get_last_error()
            raise OSError(error_number, ctypes.FormatError(error_number).strip())

    target_info.last_access_time = source_info.last_access_time
    target_info.last_write_time = source_info.last_write_time
    target_info.file_attributes = _windows_copied_file_attributes(
        target_info.file_attributes,
        source_info.file_attributes,
    )
    if not set_file_information(
        target_handle,
        0,  # FileBasicInfo
        ctypes.byref(target_info),
        information_size,
    ):
        error_number = ctypes.get_last_error()
        raise OSError(error_number, ctypes.FormatError(error_number).strip())


def _copy_open_file_metadata(
    source_fd: int,
    target_fd: int,
    source_metadata: os.stat_result,
) -> None:
    """Restore file metadata without reopening a replaceable path."""

    if os.name == "nt":
        _copy_windows_open_file_metadata(source_fd, target_fd)
        return

    _copy_open_file_xattrs(source_fd, target_fd)
    os.utime(
        target_fd,
        ns=(source_metadata.st_atime_ns, source_metadata.st_mtime_ns),
    )
    os.fchmod(target_fd, stat.S_IMODE(source_metadata.st_mode))
    fchflags = getattr(os, "fchflags", None)
    if callable(fchflags) and hasattr(source_metadata, "st_flags"):
        fchflags(target_fd, source_metadata.st_flags)


def _copy_staged_file_durably(source_path: Path | str, target_path: Path | str) -> str:
    """Copy one private staged file and flush data before restoring source metadata."""

    source = Path(source_path)
    target = Path(target_path)
    try:
        named_source_before = source.lstat()
    except OSError as exc:
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移源文件在复制前已发生变化: {source}: {exc}",
        ) from exc
    if _is_link_like_metadata(named_source_before):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移源条目包含符号链接或重解析点: {source}",
        )
    if not stat.S_ISREG(named_source_before.st_mode):
        raise StorageMigrationError(
            "path_type_unsupported",
            f"迁移源条目包含不支持的文件类型: {source}",
        )

    source_fd = -1
    target_fd = -1
    try:
        source_flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0)
        )
        if os.name != "nt":
            # A FIFO swapped in after lstat must never block this maintenance
            # thread, and a symlink must not be followed even for an instant.
            source_flags |= os.O_NONBLOCK | os.O_NOFOLLOW
        try:
            source_fd = os.open(os.fspath(source), source_flags)
        except OSError as exc:
            error_code = (
                "path_symlink_unsupported"
                if exc.errno == errno.ELOOP
                else "source_changed_during_migration"
            )
            raise StorageMigrationError(
                error_code,
                f"迁移源文件无法安全打开: {source}: {exc}",
            ) from exc

        opened_source = os.fstat(source_fd)
        try:
            named_source_after = source.lstat()
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源文件在打开期间已发生变化: {source}: {exc}",
            ) from exc
        if _is_link_like_metadata(named_source_after):
            raise StorageMigrationError(
                "path_symlink_unsupported",
                f"迁移源条目包含符号链接或重解析点: {source}",
            )
        if (
            not stat.S_ISREG(opened_source.st_mode)
            or not stat.S_ISREG(named_source_after.st_mode)
        ):
            raise StorageMigrationError(
                "path_type_unsupported",
                f"迁移源条目包含不支持的文件类型: {source}",
            )
        if (
            not os.path.samestat(named_source_before, opened_source)
            or not os.path.samestat(opened_source, named_source_after)
        ):
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源文件在打开期间被替换: {source}",
            )

        target_flags = (
            # Windows FileBasicInfo metadata restoration needs both
            # FILE_READ_ATTRIBUTES and FILE_WRITE_ATTRIBUTES on this exact
            # handle; UCRT maps O_RDWR to the required GENERIC_READ|WRITE.
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0)
        )
        try:
            target_fd = os.open(os.fspath(target), target_flags, 0o600)
        except FileExistsError as exc:
            raise StorageMigrationError(
                "staging_entry_exists",
                f"迁移暂存条目已存在，无法安全覆盖: {target}",
            ) from exc
        created_target = os.fstat(target_fd)
        if not stat.S_ISREG(created_target.st_mode):
            raise StorageMigrationError(
                "path_type_unsupported",
                f"迁移暂存目标不是普通文件: {target}",
            )

        try:
            with os.fdopen(source_fd, "rb", closefd=False) as source_handle:
                with os.fdopen(target_fd, "wb", closefd=False) as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
                    target_handle.flush()
            # Flush content before restoring a potentially read-only mode, then
            # flush the exact same handle again after its metadata is applied.
            os.fsync(target_fd)
            _copy_open_file_metadata(source_fd, target_fd, opened_source)
            os.fsync(target_fd)
        except OSError as exc:
            raise StorageMigrationError(
                "target_flush_failed",
                f"迁移数据无法可靠写入目标磁盘: {target}: {exc}",
            ) from exc
        try:
            named_target_after = target.lstat()
        except OSError as exc:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存文件在复制期间已发生变化: {target}: {exc}",
            ) from exc
        if (
            _is_link_like_metadata(named_target_after)
            or not stat.S_ISREG(named_target_after.st_mode)
            or not os.path.samestat(created_target, named_target_after)
        ):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存文件在复制期间被替换: {target}",
            )
    finally:
        if source_fd >= 0:
            with suppress(OSError):
                os.close(source_fd)
        if target_fd >= 0:
            with suppress(OSError):
                os.close(target_fd)

    # Do not perform best-effort path cleanup here. On error the partial leaf is
    # recovery evidence inside the owner-marked private transaction and the
    # outer migration state machine removes or preserves that transaction. A
    # local stat-then-unlink cannot prove it is still deleting this exact inode.
    return str(target)


def _rewrite_migrated_runtime_config_paths(
    *,
    source_root: Path,
    content_root: Path,
    target_root: Path,
) -> None:
    workshop_config_path = content_root / "config" / "workshop_config.json"
    if not workshop_config_path.is_file():
        return

    try:
        payload = _read_json_from_verified_regular_file(workshop_config_path)
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

    atomic_write_json(workshop_config_path, rewritten_payload, ensure_ascii=False, indent=2)


def _verify_opened_regular_file(
    path: Path,
    fd: int,
    expected_identity: os.stat_result,
) -> os.stat_result:
    try:
        opened = os.fstat(fd)
        named = path.lstat()
    except OSError as exc:
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移只读文件在核验期间已发生变化: {path}: {exc}",
        ) from exc
    if _is_link_like_metadata(opened) or _is_link_like_metadata(named):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移只读文件不支持符号链接或重解析点: {path}",
        )
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode):
        raise StorageMigrationError(
            "path_type_unsupported",
            f"迁移只读文件不支持该文件类型: {path}",
        )
    stable_fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
    if (
        not os.path.samestat(expected_identity, opened)
        or not os.path.samestat(opened, named)
        or any(
            getattr(expected_identity, field) != getattr(opened, field)
            for field in stable_fields
        )
        or any(getattr(opened, field) != getattr(named, field) for field in stable_fields)
    ):
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移只读文件在核验期间被替换或修改: {path}",
        )
    return opened


def _open_verified_regular_file(path: Path) -> tuple[int, os.stat_result]:
    """Open one named regular file without following or blocking on a late special file."""

    named_before = path.lstat()
    if _is_link_like_metadata(named_before):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移只读文件不支持符号链接或重解析点: {path}",
        )
    if not stat.S_ISREG(named_before.st_mode):
        raise StorageMigrationError(
            "path_type_unsupported",
            f"迁移只读文件不支持该文件类型: {path}",
        )

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    if os.name != "nt":
        flags |= os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise StorageMigrationError(
                "path_symlink_unsupported",
                f"迁移只读文件无法无跟随打开: {path}: {exc}",
            ) from exc
        raise
    try:
        return fd, _verify_opened_regular_file(path, fd, named_before)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise


def _read_json_from_verified_regular_file(path: Path) -> Any:
    fd = -1
    try:
        fd, opened_before = _open_verified_regular_file(path)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        _verify_opened_regular_file(path, fd, opened_before)
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)


def _hash_file(path: Path) -> tuple[int, str]:
    source_fd = -1
    try:
        try:
            source_fd, opened_before = _open_verified_regular_file(path)
        except StorageMigrationError:
            raise
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移校验文件无法安全打开: {path}: {exc}",
            ) from exc

        digest = hashlib.sha256()
        total_bytes = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            digest.update(chunk)

        try:
            opened_after = _verify_opened_regular_file(
                path,
                source_fd,
                opened_before,
            )
        except StorageMigrationError:
            raise
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移校验文件在读取期间发生变化: {path}: {exc}",
            ) from exc
        if total_bytes != opened_after.st_size:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移校验文件在读取期间发生变化: {path}",
            )
        return total_bytes, digest.hexdigest()
    finally:
        if source_fd >= 0:
            with suppress(OSError):
                os.close(source_fd)


def _snapshot_path(path: Path) -> dict[str, int | str]:
    if path_chain_has_symlink(path):
        raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接路径: {path}")
    if not path.exists():
        if path.is_symlink():
            raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {path}")
        return {"kind": "missing", "file_count": 0, "total_bytes": 0}
    if path.is_symlink():
        raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {path}")
    if path.is_file():
        total_bytes, digest = _hash_file(path)
        return {
            "kind": "file",
            "file_count": 1,
            "total_bytes": total_bytes,
            "sha256": digest,
        }
    if not path.is_dir():
        raise StorageMigrationError("path_type_unsupported", f"迁移校验不支持该文件类型: {path}")

    total_bytes = 0
    file_count = 0
    manifest_digest = hashlib.sha256()
    for current_root, dirnames, filenames in os.walk(path):
        dirnames.sort()
        filenames.sort()
        relative_root = Path(current_root).relative_to(path)
        for dirname in dirnames:
            current_dir = Path(current_root) / dirname
            if path_chain_has_symlink(current_dir):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移校验不支持符号链接: {current_dir}",
                )
            manifest_digest.update(
                b"D\0" + (relative_root / dirname).as_posix().encode("utf-8") + b"\0"
            )
        for filename in filenames:
            current_file = Path(current_root) / filename
            if path_chain_has_symlink(current_file):
                raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {current_file}")
            if not current_file.is_file():
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"迁移校验不支持该文件类型: {current_file}",
                )
            file_bytes, file_digest = _hash_file(current_file)
            relative_file = (relative_root / filename).as_posix()
            manifest_digest.update(
                b"F\0"
                + relative_file.encode("utf-8")
                + b"\0"
                + str(file_bytes).encode("ascii")
                + b"\0"
                + file_digest.encode("ascii")
                + b"\0"
            )
            total_bytes += file_bytes
            file_count += 1

    return {
        "kind": "dir",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "sha256": manifest_digest.hexdigest(),
    }


def _checked_migration_entry_path(
    root: Path,
    entry: RuntimeStorageEntry | str,
) -> Path:
    try:
        return checked_runtime_entry_path(root, entry)
    except RuntimeStorageEntryBoundaryError as exc:
        raise StorageMigrationError(
            "runtime_entry_path_unsafe",
            f"迁移运行时条目路径包含符号链接、重解析点或越出存储根目录: {exc}",
        ) from exc


def _snapshot_runtime_entries(root: Path) -> dict[str, dict[str, int | str]]:
    snapshots: dict[str, dict[str, int | str]] = {}
    for entry in RUNTIME_STORAGE_ENTRIES:
        entry_path = _checked_migration_entry_path(root, entry)
        if entry_path.exists() or entry_path.is_symlink():
            snapshots[entry.relative_path] = _snapshot_path(entry_path)
    return snapshots


def _validate_txid(value: Any) -> str:
    txid = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", txid) is None:
        raise StorageMigrationError("invalid_checkpoint", "存储迁移检查点的事务编号无效。")
    return txid


def _transaction_root_for(target_root: Path, txid: str) -> Path:
    # The selected root may itself be a mount point.  Its parent can therefore
    # live on a different filesystem, making the publish-time os.replace calls
    # fail with EXDEV.  Keep stage and backup under the selected root so every
    # rename remains on the target filesystem.  Runtime snapshots only include
    # the declared storage entries, so this private transaction directory is
    # never mistaken for user content.
    return target_root / f".neko-storage-migration-{txid}"


def _legacy_transaction_root_for(target_root: Path, txid: str) -> Path:
    """Return the version-2 layout used before target-mount staging."""
    safe_name = target_root.name or "root"
    return target_root.parent / f".{safe_name}.neko-storage-migration-{txid}"


def _checkpoint_transaction_root(
    payload: dict[str, Any],
    target_root: Path,
    txid: str,
) -> tuple[Path, bool]:
    """Resolve a checkpoint-owned transaction directory without trusting an arbitrary path."""
    current = _transaction_root_for(target_root, txid)
    raw = str(payload.get("transaction_root") or "").strip()
    if not raw:
        return current, False

    candidate = Path(raw).expanduser()
    if path_chain_has_symlink(candidate):
        raise StorageMigrationError(
            "transaction_path_symlink_unsupported",
            "迁移事务目录或其父路径包含符号链接，无法确认事务归属。",
        )
    candidate = Path(os.path.abspath(candidate))
    allowed = {
        os.path.normcase(str(Path(os.path.abspath(path))))
        for path in (current, _legacy_transaction_root_for(target_root, txid))
    }
    if os.path.normcase(str(candidate)) not in allowed:
        raise StorageMigrationError(
            "invalid_checkpoint",
            "存储迁移检查点中的事务目录不属于当前迁移。",
        )
    return candidate, True


def _transaction_owner_token(payload: dict[str, Any]) -> str:
    token = str(payload.get("transaction_owner_token") or "").strip().lower()
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else ""


def _transaction_root_is_owned(
    payload: dict[str, Any],
    transaction_root: Path,
    txid: str,
) -> bool:
    """Prove a checkpoint-bound transaction directory carries our durable marker."""
    owner_token = _transaction_owner_token(payload)
    if not owner_token:
        return False
    marker = transaction_root / _TRANSACTION_OWNER_MARKER_FILENAME
    fd = -1
    try:
        root_before = transaction_root.lstat()
        if not stat.S_ISDIR(root_before.st_mode) or stat.S_ISLNK(root_before.st_mode):
            return False
        if path_chain_has_symlink(transaction_root):
            return False
        fd, marker_opened = _open_verified_regular_file(marker)
        raw = os.read(fd, 4097)
        _verify_opened_regular_file(marker, fd, marker_opened)
        root_after = transaction_root.lstat()
        if (
            len(raw) > 4096
            or not os.path.samestat(root_before, root_after)
        ):
            return False
        marker_payload = json.loads(raw.decode("utf-8"))
    except (OSError, StorageMigrationError, UnicodeError, ValueError, TypeError):
        return False
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)
    return bool(
        isinstance(marker_payload, dict)
        and marker_payload.get("version") == _TRANSACTION_OWNER_MARKER_VERSION
        and str(marker_payload.get("txid") or "").strip().lower() == txid
        and secrets.compare_digest(
            str(marker_payload.get("owner_token") or "").strip().lower(),
            owner_token,
        )
    )


def _create_owned_transaction_root(
    payload: dict[str, Any],
    transaction_root: Path,
    txid: str,
) -> None:
    """Publish a complete marked directory without replacing a late occupant."""
    owner_token = _transaction_owner_token(payload)
    if not owner_token:
        raise StorageMigrationError(
            "transaction_owner_missing",
            "迁移检查点缺少事务目录所有权凭据，已停止迁移。",
        )
    prepared_root = transaction_root.parent / f".{transaction_root.name}.{uuid.uuid4().hex}.tmp"
    prepared_root.mkdir(parents=False, exist_ok=False)
    prepared_identity = prepared_root.lstat()
    try:
        _write_transaction_owner_marker(payload, prepared_root, txid)
        if (
            not os.path.samestat(prepared_identity, prepared_root.lstat())
            or not _transaction_root_is_owned(payload, prepared_root, txid)
        ):
            raise OSError("migration preparation directory identity changed")
        # The owner marker is the authority for every later cleanup. Persist
        # its name inside the prepared directory before publishing that
        # directory under the checkpoint-bound transaction name.
        _fsync_migration_directory(prepared_root)
        _durable_publish_without_replacing(prepared_root, transaction_root)
    except BaseException:
        if prepared_root.exists() or prepared_root.is_symlink():
            try:
                if not _remove_private_directory_via_quarantine(
                    prepared_root,
                    prepared_identity,
                ):
                    logger.warning(
                        "Preserving replaced migration preparation directory: %s",
                        prepared_root,
                    )
            except OSError as cleanup_exc:
                logger.warning(
                    "Failed to clean migration preparation directory %s: %s",
                    prepared_root,
                    cleanup_exc,
                )
        raise
    if (
        not os.path.samestat(prepared_identity, transaction_root.lstat())
        or not _transaction_root_is_owned(payload, transaction_root, txid)
    ):
        raise OSError("published migration transaction ownership is unverifiable")


def _write_transaction_owner_marker(
    payload: dict[str, Any],
    transaction_root: Path,
    txid: str,
) -> None:
    """Persist the complete marker before a prepared transaction is published."""
    owner_token = _transaction_owner_token(payload)
    if not owner_token:
        raise StorageMigrationError(
            "transaction_owner_missing",
            "迁移检查点缺少事务目录所有权凭据，已停止迁移。",
        )
    marker = transaction_root / _TRANSACTION_OWNER_MARKER_FILENAME
    atomic_write_json(
        marker,
        {
            "version": _TRANSACTION_OWNER_MARKER_VERSION,
            "txid": txid,
            "owner_token": owner_token,
        },
        ensure_ascii=True,
    )
    with suppress(OSError):
        marker.chmod(0o600)
    fsync_directory_best_effort(transaction_root)


def _remove_owned_transaction_quarantine(
    payload: dict[str, Any],
    quarantine: Path,
    txid: str,
) -> bool:
    """Delete owned transaction contents while keeping their proof until last."""
    marker = quarantine / _TRANSACTION_OWNER_MARKER_FILENAME
    try:
        quarantine_identity = quarantine.lstat()
    except OSError:
        return False
    if (
        not stat.S_ISDIR(quarantine_identity.st_mode)
        or _is_link_like_metadata(quarantine_identity)
        or path_chain_has_symlink(quarantine)
    ):
        return False

    if not marker.exists() and not marker.is_symlink():
        # The only unauthenticated recoverable state is the empty directory
        # left by a crash after deleting the marker but before rmdir. rmdir is
        # itself the emptiness check, so a concurrent or unrelated entry is
        # preserved rather than recursively deleted.
        try:
            current_identity = quarantine.lstat()
            if (
                not os.path.samestat(quarantine_identity, current_identity)
                or not stat.S_ISDIR(current_identity.st_mode)
                or _is_link_like_metadata(current_identity)
            ):
                return False
            quarantine.rmdir()
        except OSError:
            return False
        fsync_directory_best_effort(quarantine.parent)
        return True

    if not _transaction_root_is_owned(payload, quarantine, txid):
        return False
    try:
        children = list(quarantine.iterdir())
    except OSError:
        return False
    for child in children:
        if child.name == _TRANSACTION_OWNER_MARKER_FILENAME:
            continue
        _remove_existing_path(child)

    try:
        if (
            not os.path.samestat(quarantine_identity, quarantine.lstat())
            or not _transaction_root_is_owned(payload, quarantine, txid)
        ):
            return False
        remaining = list(quarantine.iterdir())
        if len(remaining) != 1 or remaining[0].name != _TRANSACTION_OWNER_MARKER_FILENAME:
            return False
        marker.unlink()
        fsync_directory_best_effort(quarantine)
        quarantine.rmdir()
    except OSError:
        return False
    fsync_directory_best_effort(quarantine.parent)
    return True


def _remove_transaction_root_if_owned(
    payload: dict[str, Any],
    transaction_root: Path,
    txid: str,
) -> bool:
    quarantine = _private_directory_quarantine_path(transaction_root)
    if not transaction_root.exists() and not transaction_root.is_symlink():
        try:
            quarantine.lstat()
        except OSError:
            return False
        # This is already the detached, deterministic quarantine left by an
        # interrupted cleanup. Deleting it in place avoids creating recursive
        # ``.deleting`` names that a later generation could not rediscover. The
        # helper authenticates non-empty content and accepts an unauthenticated
        # directory only when rmdir itself proves it is empty.
        return _remove_owned_transaction_quarantine(payload, quarantine, txid)
    try:
        owned_identity = transaction_root.lstat()
    except OSError:
        return False
    if not _transaction_root_is_owned(payload, transaction_root, txid):
        return False
    return _remove_private_directory_via_quarantine(
        transaction_root,
        owned_identity,
        verify_quarantine=lambda quarantine: _transaction_root_is_owned(
            payload,
            quarantine,
            txid,
        ),
        remove_quarantine=lambda quarantine: _remove_owned_transaction_quarantine(
            payload,
            quarantine,
            txid,
        ),
    )


def _rollback_published_entries(
    target_root: Path,
    transaction_root: Path,
    original_target_entries: list[str],
    publish_entry_names: list[str],
    target_baseline: dict[str, dict[str, int | str]],
    publish_entry_snapshots: dict[str, dict[str, int | str]],
) -> None:
    if path_chain_has_symlink(target_root) or path_chain_has_symlink(transaction_root):
        raise StorageMigrationError(
            "rollback_path_symlink_unsupported",
            "迁移路径已被符号链接替换，无法安全自动回滚。",
        )
    staged_root = transaction_root / "staged"
    backup_root = transaction_root / "backup"
    original_entries = set(original_target_entries)
    publish_entries = set(publish_entry_names)
    baseline_entries = set(target_baseline)
    if original_entries != baseline_entries:
        raise StorageMigrationError(
            "rollback_checkpoint_inconsistent",
            "迁移回滚检查点与目标基线不一致，无法证明原目标数据可以完整恢复。",
        )
    if set(publish_entry_snapshots) != publish_entries:
        raise StorageMigrationError(
            "rollback_checkpoint_inconsistent",
            "迁移回滚检查点缺少已发布数据清单，无法排除目标数据被并发改写。",
        )

    for entry in reversed(RUNTIME_STORAGE_ENTRIES):
        relative_path = entry.relative_path
        if relative_path not in publish_entries:
            continue
        target_path = _checked_migration_entry_path(target_root, relative_path)
        staged_path = _checked_migration_entry_path(staged_root, relative_path)
        backup_path = _checked_migration_entry_path(backup_root, relative_path)
        target_exists = target_path.exists() or target_path.is_symlink()
        staged_exists = staged_path.exists() or staged_path.is_symlink()
        if target_exists and staged_exists:
            # Compatibility with checkpoints created by the earlier POSIX
            # link-then-unlink publisher.  A process loss between those two
            # syscalls leaves two names for our one verified file.  Collapse
            # only that exact same-inode state; equal content at a different
            # inode remains an external collision and stays fail-closed.
            try:
                target_metadata = target_path.lstat()
                staged_metadata = staged_path.lstat()
                interrupted_file_publish = bool(
                    stat.S_ISREG(target_metadata.st_mode)
                    and stat.S_ISREG(staged_metadata.st_mode)
                    and os.path.samestat(target_metadata, staged_metadata)
                    and _snapshot_path(target_path)
                    == publish_entry_snapshots[relative_path]
                    and _snapshot_path(staged_path)
                    == publish_entry_snapshots[relative_path]
                )
            except OSError:
                interrupted_file_publish = False
            if interrupted_file_publish:
                staged_path.unlink()
                fsync_directory_best_effort(staged_path.parent)
                staged_exists = False
        if relative_path in original_entries:
            if backup_path.exists() or backup_path.is_symlink():
                if _snapshot_path(backup_path) != target_baseline[relative_path]:
                    raise StorageMigrationError(
                        "rollback_backup_mismatch",
                        f"迁移回滚备份与目标基线不一致，已保留事务目录: {relative_path}",
                    )
                if staged_exists:
                    if target_exists:
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移发布中断窗口出现未记录的目标数据，无法安全回滚: {relative_path}",
                        )
                    if _snapshot_path(staged_path) != publish_entry_snapshots[relative_path]:
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                        )
                else:
                    if (
                        not target_exists
                        or _snapshot_path(target_path)
                        != publish_entry_snapshots[relative_path]
                    ):
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移已发布数据被改写或缺失，无法安全覆盖: {relative_path}",
                        )
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    _durable_publish_without_replacing(target_path, staged_path)
                    if _snapshot_path(staged_path) != publish_entry_snapshots[relative_path]:
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                        )
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _durable_publish_without_replacing(backup_path, target_path)
            elif _snapshot_path(target_path) != target_baseline[relative_path]:
                # A prior rollback attempt may already have restored this entry
                # and then crashed before deleting the transaction directory. A
                # missing backup is only safe in that idempotent, baseline-equal
                # case; otherwise the original target cannot be proven intact.
                raise StorageMigrationError(
                    "rollback_backup_missing",
                    f"迁移回滚备份缺失，无法恢复原目标数据: {relative_path}",
                )
        elif staged_exists:
            if target_exists:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移发布前目标位置出现了未记录的数据，无法安全回滚: {relative_path}",
                )
            if _snapshot_path(staged_path) != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                )
        elif target_exists:
            # Move our verified published copy back under the owned transaction
            # instead of deleting it in place. Both the pre- and post-move
            # snapshots are required so a replacement race is preserved as
            # recovery evidence rather than recursively erased.
            if _snapshot_path(target_path) != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移已发布数据被并发改写，无法安全删除: {relative_path}",
                )
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            _durable_publish_without_replacing(target_path, staged_path)
            if _snapshot_path(staged_path) != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                )

    if _snapshot_runtime_entries(target_root) != target_baseline:
        raise StorageMigrationError(
            "rollback_verification_failed",
            "目标路径回滚后的数据清单与迁移前基线不一致，已保留事务目录等待恢复。",
        )


def _fsync_staged_tree(path: Path) -> None:
    paths: list[Path] = []
    directories: list[Path] = []
    if path.is_file():
        paths.append(path)
        directories.append(path.parent)
    elif path.is_dir():
        for current_root, dirnames, filenames in os.walk(path):
            directories.append(Path(current_root))
            for dirname in dirnames:
                current_dir = Path(current_root) / dirname
                if path_chain_has_symlink(current_dir):
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"迁移暂存区包含符号链接: {current_dir}",
                    )
            for filename in filenames:
                staged_file = Path(current_root) / filename
                if path_chain_has_symlink(staged_file):
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"迁移暂存区包含符号链接: {staged_file}",
                    )
                paths.append(staged_file)

    if sys.platform != "win32":
        for staged_file in paths:
            fd = -1
            try:
                fd, opened_before = _open_verified_regular_file(staged_file)
                os.fsync(fd)
                _verify_opened_regular_file(staged_file, fd, opened_before)
            except (OSError, StorageMigrationError) as exc:
                raise StorageMigrationError(
                    "target_flush_failed",
                    f"迁移数据无法可靠写入目标磁盘: {staged_file}: {exc}",
                ) from exc
            finally:
                if fd >= 0:
                    with suppress(OSError):
                        os.close(fd)
        for staged_directory in reversed(directories):
            _fsync_migration_directory(staged_directory)
    else:
        # Python cannot portably open/flush directory handles on Windows. Each
        # copied file was already flushed through a writable handle before its
        # source metadata was restored, so keep only the existing best-effort
        # directory barrier here instead of reopening read-only files/dirs.
        for staged_directory in reversed(directories):
            fsync_directory_best_effort(staged_directory)


def _iter_existing_runtime_entries(root: Path) -> list[str]:
    entries: list[str] = []
    for entry in RUNTIME_STORAGE_ENTRIES:
        entry_path = _checked_migration_entry_path(root, entry)
        if entry_path.exists() or entry_path.is_symlink():
            entries.append(entry.relative_path)
    return entries


def _root_has_user_content(root: Path, *, config_manager) -> bool:
    try:
        from utils.cloudsave_runtime import runtime_root_has_user_content

        return bool(runtime_root_has_user_content(root, config_manager=config_manager))
    except Exception:
        if not root.exists() or not root.is_dir():
            return False
        try:
            return any(root.iterdir())
        except OSError:
            return False


def _ensure_target_root_writable(target_root: Path) -> None:
    missing_directories: list[Path] = []
    candidate = target_root
    while not candidate.exists() and candidate.parent != candidate:
        missing_directories.append(candidate)
        candidate = candidate.parent
    target_root.mkdir(parents=True, exist_ok=True)
    for created_directory in missing_directories:
        _fsync_migration_directory(created_directory.parent)
    probe_path = target_root / f".neko-storage-migration-write-probe-{uuid.uuid4().hex}.tmp"
    try:
        probe_path.write_bytes(b"")
        probe_path.unlink()
    except Exception as exc:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageMigrationError("target_not_writable", "目标路径当前不可写，无法执行关闭后的迁移。")


def get_storage_migration_path(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
) -> Path:
    configured_anchor_root = getattr(config_manager, "anchor_root", None)
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or configured_anchor_root or compute_anchor_root(config_manager)
    )
    return normalized_anchor_root / "state" / "storage_migration.json"


def load_storage_migration(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    configured_anchor_root = (
        anchor_root
        or getattr(config_manager, "anchor_root", None)
        or compute_anchor_root(config_manager)
    )
    migration_path = Path(configured_anchor_root).expanduser() / "state" / "storage_migration.json"
    try:
        payload = read_fixed_anchor_state_json(
            configured_anchor_root,
            "storage_migration.json",
        )
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StorageMigrationError(
            "migration_checkpoint_malformed",
            f"存储迁移检查点格式损坏，无法安全判断迁移状态: {migration_path}",
        ) from exc
    except Exception as exc:
        raise StorageMigrationError(
            "migration_checkpoint_unreadable",
            f"存储迁移检查点当前不可读，无法安全判断迁移状态: {migration_path}",
        ) from exc

    if not isinstance(payload, dict):
        raise StorageMigrationError(
            "migration_checkpoint_malformed",
            f"存储迁移检查点不是 JSON 对象，无法安全判断迁移状态: {migration_path}",
        )

    return payload


def is_storage_migration_pending(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False

    status = str(payload.get("status") or "").strip().lower()
    if status not in ACTIVE_STORAGE_MIGRATION_STATUSES:
        return False

    source_root = str(payload.get("source_root") or "").strip()
    target_root = str(payload.get("target_root") or "").strip()
    return bool(source_root and target_root)


def is_storage_migration_rollback_required(payload: dict[str, Any] | None) -> bool:
    """Return whether a checkpoint still owns an unfinished target rollback.

    This deliberately checks the persisted status even when an older or damaged
    checkpoint is missing one of its path fields.  Once publish rollback failed,
    callers must fail closed instead of treating the checkpoint as an ordinary
    failed migration that may be replaced by a new selection.
    """
    if not isinstance(payload, dict):
        return False
    return str(payload.get("status") or "").strip().lower() == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED


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
    normalized_target_root = normalize_runtime_root(target_root)
    return {
        "version": STORAGE_MIGRATION_VERSION,
        "txid": str(txid or uuid.uuid4().hex),
        "transaction_owner_token": secrets.token_hex(32),
        "status": STORAGE_MIGRATION_STATUS_PENDING,
        "source_root": str(normalize_runtime_root(source_root)),
        "target_root": str(normalized_target_root),
        "selection_source": _normalize_selection_source(selection_source),
        # Migration behavior is server-owned.  ``selection_source`` remains
        # presentation/audit metadata and must never turn a copy into an adopt.
        "migration_mode": STORAGE_MIGRATION_MODE_COPY,
        "confirmed_existing_target_content": bool(confirmed_existing_target_content),
        "target_baseline": _snapshot_runtime_entries(normalized_target_root),
        "original_target_entries": [],
        "publish_entry_names": [],
        "backup_root": _normalize_optional_path(backup_root),
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
    # The generic writer is intentionally best-effort for directory handles so
    # ordinary application writes remain portable. Migration checkpoints are
    # recovery authority, so POSIX must not report success until the replace is
    # also durable in its parent directory.
    _fsync_migration_directory(migration_path.parent)
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


def run_pending_storage_migration(
    config_manager,
    *,
    anchor_root: Path | str | None = None,
) -> dict[str, Any]:
    configured_anchor_root = getattr(config_manager, "anchor_root", None)
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or configured_anchor_root or compute_anchor_root(config_manager)
    )
    if hasattr(config_manager, "anchor_root"):
        config_manager.anchor_root = normalized_anchor_root

    migration_payload = load_storage_migration(
        config_manager,
        anchor_root=normalized_anchor_root,
    )
    if (
        isinstance(migration_payload, dict)
        and str(migration_payload.get("status") or "").strip()
        == STORAGE_MIGRATION_STATUS_FAILED
        and _migration_transaction_evidence_is_present(migration_payload)
    ):
        # Upgrade legacy terminal checkpoints that still own staged recovery
        # data.  Keeping them active lets a later launch revalidate the restored
        # source and securely retire the transaction instead of leaving the UI
        # behind a permanent 409 with no recovery path.
        migration_payload = _persist_migration_payload(
            config_manager,
            migration_payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
        )
    if not is_storage_migration_pending(migration_payload):
        if isinstance(migration_payload, dict):
            try:
                completed_target = normalize_runtime_root(
                    str(migration_payload.get("target_root") or "").strip()
                )
                completed_txid = _validate_txid(migration_payload.get("txid"))
                completed_transaction_root, checkpoint_owned = _checkpoint_transaction_root(
                    migration_payload,
                    completed_target,
                    completed_txid,
                )
                terminal_status = str(migration_payload.get("status") or "").strip()
                completed_quarantine = _private_directory_quarantine_path(
                    completed_transaction_root
                )
                if (
                    terminal_status == STORAGE_MIGRATION_STATUS_COMPLETED
                    and checkpoint_owned
                    and (
                        completed_transaction_root.exists()
                        or completed_transaction_root.is_symlink()
                        or completed_quarantine.exists()
                        or completed_quarantine.is_symlink()
                    )
                ):
                    if not _remove_transaction_root_if_owned(
                        migration_payload,
                        completed_transaction_root,
                        completed_txid,
                    ):
                        logger.warning(
                            "Preserving unverified completed migration transaction: %s",
                            completed_transaction_root,
                        )
            except Exception as exc:
                logger.warning("Failed to clean completed storage migration transaction: %s", exc)
        return {
            "attempted": False,
            "completed": False,
            "payload": migration_payload,
            "anchor_root": str(normalized_anchor_root),
        }

    payload = dict(migration_payload or {})
    source_root: Path | None = None
    target_root: Path | None = None
    transaction_root: Path | None = None
    transaction_owned = False
    original_target_entries: list[str] = []
    publish_entry_names: list[str] = []
    source_snapshots: dict[str, dict[str, int | str]] = {}
    source_runtime_baseline: dict[str, dict[str, int | str]] | None = None
    target_baseline: dict[str, dict[str, int | str]] | None = None
    publish_entry_snapshots: dict[str, dict[str, int | str]] | None = None
    publish_started = False
    policy_payload: dict[str, Any] | None = None

    def _finish_failure(
        error_code: str,
        error_message: str,
        *,
        rollback_required: bool = False,
    ) -> dict[str, Any]:
        nonlocal payload, policy_payload
        raw_payload_source_root = str(payload.get("source_root") or "").strip()
        if source_root is not None:
            recovery_source_root = str(source_root)
        else:
            fallback_root = str(getattr(config_manager, "app_docs_dir", "") or "").strip()
            recovery_source_root = raw_payload_source_root or fallback_root or str(normalized_anchor_root)
        # Restore both durable references to the source before terminalising the
        # checkpoint.  Otherwise a successful target-policy commit followed by a
        # rollback can leave the next launcher selecting the rolled-back target.
        # The checkpoint is written last and records partial recovery metadata so
        # future generations can fail closed even when policy/root_state did not.
        policy_payload = None
        policy_persisted = False
        try:
            policy_payload = save_storage_policy(
                config_manager,
                selected_root=recovery_source_root,
                selection_source=POLICY_SELECTION_SOURCE_RECOVERED,
                anchor_root=normalized_anchor_root,
            )
            policy_persisted = True
        except Exception as policy_exc:
            logger.warning("Failed to persist recovered storage policy after migration failure: %s", policy_exc)

        root_state_persisted = False
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
            root_state_persisted = True
        except Exception as root_state_exc:
            logger.warning("Failed to persist recovery root_state after migration failure: %s", root_state_exc)

        next_payload = dict(payload)
        next_status = (
            STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
            if rollback_required
            else STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
            if _migration_transaction_evidence_is_present(next_payload)
            else STORAGE_MIGRATION_STATUS_FAILED
        )
        next_payload.update(
            {
                "status": next_status,
                "backup_root": recovery_source_root,
                "error_code": error_code,
                "error_message": error_message,
                "failed_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
                "recovery_policy_persisted": policy_persisted,
                "recovery_root_state_persisted": root_state_persisted,
                "recovery_metadata_degraded": not (policy_persisted and root_state_persisted),
            }
        )
        checkpoint_persisted = False
        try:
            payload = save_storage_migration(
                config_manager,
                next_payload,
                anchor_root=normalized_anchor_root,
            )
            checkpoint_persisted = True
        except Exception as checkpoint_exc:
            # Keep the in-memory result actionable for this generation.  The old
            # active checkpoint and transaction evidence remain on disk so a
            # future generation can retry/verify rather than assuming success.
            payload = next_payload
            logger.warning("Failed to persist terminal storage migration checkpoint: %s", checkpoint_exc)

        recovery_metadata_persisted = (
            policy_persisted and root_state_persisted and checkpoint_persisted
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
            "recovery_policy_persisted": policy_persisted,
            "recovery_root_state_persisted": root_state_persisted,
            "recovery_checkpoint_persisted": checkpoint_persisted,
            "recovery_metadata_persisted": recovery_metadata_persisted,
            "force_recovery_layout": not recovery_metadata_persisted,
        }

    def _source_matches_recovery_baseline() -> bool:
        if source_root is None or source_runtime_baseline is None:
            return False
        try:
            return bool(
                source_root.exists()
                and source_root.is_dir()
                and _snapshot_runtime_entries(source_root)
                == source_runtime_baseline
            )
        except Exception:
            return False

    def _finish_rolled_back_failure(
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        nonlocal payload
        result = _finish_failure(error_code, error_message)
        if (
            transaction_root is not None
            and transaction_owned
            and bool(result.get("recovery_checkpoint_persisted"))
            and _source_matches_recovery_baseline()
        ):
            try:
                removed = _remove_transaction_root_if_owned(
                    payload,
                    transaction_root,
                    txid,
                )
                if not removed:
                    logger.warning(
                        "Preserving migration transaction after ownership changed: %s",
                        transaction_root,
                    )
                elif str(payload.get("status") or "").strip() == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED:
                    # The durable failed checkpoint existed before destructive
                    # cleanup.  Only after the owned transaction is gone may it
                    # become a replaceable terminal failure.
                    payload = _persist_migration_payload(
                        config_manager,
                        payload,
                        anchor_root=normalized_anchor_root,
                        status=STORAGE_MIGRATION_STATUS_FAILED,
                    )
                    result["payload"] = payload
            except Exception as cleanup_exc:
                logger.warning("Failed to clean rolled-back migration transaction: %s", cleanup_exc)
        return result

    try:
        raw_source_root = Path(str(payload.get("source_root") or "").strip()).expanduser()
        raw_target_root = Path(str(payload.get("target_root") or "").strip()).expanduser()
        if path_chain_has_symlink(raw_source_root):
            raise StorageMigrationError(
                "source_path_symlink_unsupported",
                "迁移源路径或其父路径已变为符号链接，已停止迁移。",
            )
        if path_chain_has_symlink(raw_target_root):
            raise StorageMigrationError(
                "target_path_symlink_unsupported",
                "迁移目标路径或其父路径已变为符号链接，已停止迁移。",
            )
        source_root = normalize_runtime_root(raw_source_root)
        target_root = normalize_runtime_root(raw_target_root)
        selection_source = _normalize_selection_source(str(payload.get("selection_source") or ""))
        migration_mode = str(payload.get("migration_mode") or STORAGE_MIGRATION_MODE_COPY).strip()
        if migration_mode != STORAGE_MIGRATION_MODE_COPY:
            raise StorageMigrationError("invalid_migration_mode", "存储迁移检查点包含不支持的迁移模式。")
        txid = _validate_txid(payload.get("txid"))
        transaction_root, transaction_checkpoint_bound = _checkpoint_transaction_root(
            payload,
            target_root,
            txid,
        )
        transaction_owned = bool(
            transaction_checkpoint_bound
            and transaction_root.exists()
            and _transaction_root_is_owned(payload, transaction_root, txid)
        )
        original_target_entries = [
            str(value)
            for value in payload.get("original_target_entries", [])
            if str(value) in MIGRATED_RUNTIME_ENTRY_NAMES
        ]
        publish_entry_names = [
            str(value)
            for value in payload.get("publish_entry_names", [])
            if str(value) in MIGRATED_RUNTIME_ENTRY_NAMES
        ]
        raw_target_baseline = payload.get("target_baseline")
        if isinstance(raw_target_baseline, dict):
            target_baseline = raw_target_baseline
        raw_publish_entry_snapshots = payload.get("publish_entry_snapshots")
        if isinstance(raw_publish_entry_snapshots, dict):
            publish_entry_snapshots = raw_publish_entry_snapshots
        raw_source_runtime_baseline = payload.get("source_runtime_baseline")
        if isinstance(raw_source_runtime_baseline, dict):
            source_runtime_baseline = raw_source_runtime_baseline
        publish_started = str(payload.get("status") or "").strip() in {
            STORAGE_MIGRATION_STATUS_PUBLISHING,
            STORAGE_MIGRATION_STATUS_COMMITTING,
            STORAGE_MIGRATION_STATUS_RETAINING_SOURCE,
            STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
        }

        try:
            if paths_equal(source_root, target_root):
                raise StorageMigrationError("target_matches_source", "目标路径与当前路径一致，不需要执行迁移。")
            if _path_contains(source_root, target_root) or _path_contains(target_root, source_root):
                raise StorageMigrationError("paths_nested", "源路径和目标路径不能互相包含，无法安全执行迁移。")
        except PathIdentityUnavailable as exc:
            raise StorageMigrationError(
                "path_identity_uninspectable",
                "无法确认迁移源路径与目标路径的物理关系，已安全停止迁移。",
            ) from exc
        if not source_root.exists() or not source_root.is_dir():
            raise StorageMigrationError("source_root_missing", "原始数据目录不存在，无法继续迁移。")

        transaction_quarantine = _private_directory_quarantine_path(transaction_root)
        if transaction_root.is_symlink():
            return _finish_failure(
                "transaction_path_symlink_unsupported",
                "迁移事务目录已被替换为符号链接，无法自动确认回滚边界。",
                rollback_required=publish_started,
            )
        if publish_started and not transaction_root.exists():
            return _finish_failure(
                "rollback_transaction_missing",
                "迁移发布已经开始，但回滚事务目录缺失，无法证明原目标数据完整。",
                rollback_required=True,
            )
        if transaction_root.exists():
            if not transaction_owned:
                return _finish_failure(
                    "transaction_path_occupied",
                    "迁移事务目录已被其他内容占用，已停止迁移以避免删除未知数据。",
                    rollback_required=publish_started,
                )
            if publish_started:
                if target_baseline is None:
                    return _finish_failure(
                        "rollback_baseline_missing",
                        "迁移检查点缺少目标基线，无法安全证明回滚结果。",
                        rollback_required=True,
                    )
                if publish_entry_snapshots is None:
                    return _finish_failure(
                        "rollback_publish_manifest_missing",
                        "迁移检查点缺少已发布数据清单，无法排除目标路径被并发改写。",
                        rollback_required=True,
                    )
                try:
                    _rollback_published_entries(
                        target_root,
                        transaction_root,
                        original_target_entries,
                        publish_entry_names,
                        target_baseline,
                        publish_entry_snapshots,
                    )
                except Exception as rollback_exc:
                    return _finish_failure(
                        "rollback_failed",
                        f"迁移目标回滚未完成: {rollback_exc}",
                        rollback_required=True,
                    )
                payload = _persist_migration_payload(
                    config_manager,
                    payload,
                    anchor_root=normalized_anchor_root,
                    status=STORAGE_MIGRATION_STATUS_PREFLIGHT,
                    original_target_entries=[],
                    publish_entry_names=[],
                    publish_entry_snapshots={},
                )
                publish_started = False
            if not _source_matches_recovery_baseline():
                return _finish_failure(
                    "source_recovery_unverifiable",
                    "无法证明迁移源仍与事务暂存前一致；已保留事务副本。",
                )
            if not _remove_transaction_root_if_owned(
                payload,
                transaction_root,
                txid,
            ):
                return _finish_failure(
                    "transaction_ownership_changed",
                    "迁移事务目录的所有权标记在清理前发生变化，已停止迁移。",
                )
        elif transaction_quarantine.exists() or transaction_quarantine.is_symlink():
            if not transaction_checkpoint_bound:
                return _finish_failure(
                    "transaction_ownership_changed",
                    "迁移事务隔离目录的所有权无法验证，已停止迁移。",
                )
            if not _source_matches_recovery_baseline():
                return _finish_failure(
                    "source_recovery_unverifiable",
                    "无法证明迁移源仍与事务暂存前一致；已保留隔离副本。",
                )
            if not _remove_transaction_root_if_owned(
                payload,
                transaction_root,
                txid,
            ):
                return _finish_failure(
                    "transaction_ownership_changed",
                    "迁移事务隔离目录的所有权在清理前发生变化，已停止迁移。",
                )

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_PREFLIGHT,
            started_at=str(payload.get("started_at") or _utc_now_iso()),
            source_root=str(source_root),
            target_root=str(target_root),
            error_code="",
            error_message="",
        )

        current_target_snapshot = _snapshot_runtime_entries(target_root)
        if isinstance(target_baseline, dict) and current_target_snapshot != target_baseline:
            raise StorageMigrationError(
                "target_changed_since_confirmation",
                "目标路径中的数据在确认后发生了变化，已停止迁移以避免覆盖新数据。",
            )
        if not isinstance(target_baseline, dict):
            # Version-1 checkpoints did not bind confirmation to target state.
            # Capturing it now is safe, but they never get legacy/recovered
            # adopt semantics: all migrations remain copies from source.
            target_baseline = current_target_snapshot
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                target_baseline=target_baseline,
                migration_mode=STORAGE_MIGRATION_MODE_COPY,
            )

        target_has_user_content = _root_has_user_content(target_root, config_manager=config_manager)
        confirmed_existing_target_content = bool(payload.get("confirmed_existing_target_content"))

        if target_has_user_content and not confirmed_existing_target_content:
            raise StorageMigrationError(
                "target_confirmation_required",
                "目标路径已经包含现有数据，需要先确认覆盖目标中的同名运行时数据目录。",
            )

        _ensure_target_root_writable(target_root)

        existing_entries = _iter_existing_runtime_entries(source_root)
        source_snapshots: dict[str, dict[str, int | str]] = {
            entry_name: _snapshot_path(_checked_migration_entry_path(source_root, entry_name))
            for entry_name in existing_entries
        }
        source_runtime_baseline = dict(source_snapshots)
        required_bytes = sum(
            int(snapshot.get("total_bytes") or 0) for snapshot in source_snapshots.values()
        )
        safety_margin_bytes = max(64 * 1024 * 1024, int(required_bytes * 0.05)) if required_bytes else 0
        try:
            target_free_bytes = int(shutil.disk_usage(str(target_root)).free)
        except OSError as exc:
            raise StorageMigrationError(
                "disk_space_unavailable",
                f"无法确认目标卷剩余空间，已停止迁移: {exc}",
            ) from exc
        if required_bytes + safety_margin_bytes > target_free_bytes:
            raise StorageMigrationError(
                "insufficient_space",
                "目标卷剩余空间不足，无法安全执行迁移。",
            )

        # Bind the random path and owner token before publishing anything at
        # the final name. Build a complete marker in a private sibling, then
        # atomically publish that directory without replacing a late occupant.
        # A checkpoint path alone is never deletion authority after restart.
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_PREFLIGHT,
            transaction_root=str(transaction_root),
            source_runtime_baseline=source_runtime_baseline,
        )
        try:
            _create_owned_transaction_root(payload, transaction_root, txid)
        except FileExistsError as exc:
            raise StorageMigrationError(
                "transaction_path_occupied",
                "迁移事务目录已被其他内容占用，已停止迁移以避免删除未知数据。",
            ) from exc
        transaction_owned = True
        staged_root = transaction_root / "staged"
        backup_root = transaction_root / "backup"
        staged_root.mkdir()
        backup_root.mkdir()
        # These names are the only destinations for copied data and original
        # target backups. Persist them before advancing to COPYING; the
        # transaction name itself was flushed by _create_owned_transaction_root.
        _fsync_migration_directory(transaction_root)

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_COPYING,
        )
        for entry_name in existing_entries:
            if path_chain_has_symlink(source_root) or path_chain_has_symlink(target_root):
                raise StorageMigrationError(
                    "migration_path_changed",
                    "迁移期间源路径或目标路径的文件系统边界发生变化，已停止迁移。",
                )
            source_entry = _checked_migration_entry_path(source_root, entry_name)
            staged_entry = _checked_migration_entry_path(staged_root, entry_name)
            source_snapshot_before = source_snapshots[entry_name]
            _copy_runtime_entry(source_entry, staged_entry)
            source_snapshot_after = _snapshot_path(source_entry)
            if source_snapshot_after != source_snapshot_before:
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    f"迁移期间源数据发生变化，已停止迁移: {entry_name}",
                )
            staged_snapshot = _snapshot_path(staged_entry)
            if staged_snapshot != source_snapshot_before:
                raise StorageMigrationError(
                    "verification_failed",
                    f"迁移暂存校验失败：{entry_name} 未完整复制。",
                )
            source_snapshots[entry_name] = staged_snapshot

        _rewrite_migrated_runtime_config_paths(
            source_root=source_root,
            content_root=staged_root,
            target_root=target_root,
        )
        if "config" in source_snapshots:
            source_snapshots["config"] = _snapshot_path(
                _checked_migration_entry_path(staged_root, "config")
            )
        # Flush the complete staging tree once so durability is requested from
        # each copied leaf through nested runtime parents and the staging root
        # itself before the VERIFYING checkpoint is written.
        _fsync_staged_tree(staged_root)

        if _snapshot_runtime_entries(source_root) != source_runtime_baseline:
            raise StorageMigrationError(
                "source_changed_during_migration",
                "迁移期间源数据清单发生变化，已停止迁移。",
            )

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_VERIFYING,
            backup_root=str(source_root),
        )

        publish_target_snapshot = _snapshot_runtime_entries(target_root)
        if publish_target_snapshot != target_baseline:
            raise StorageMigrationError(
                "target_changed_since_confirmation",
                "目标路径中的数据在迁移期间发生了变化，已停止迁移以避免覆盖新数据。",
            )

        original_target_entries = list(publish_target_snapshot)
        publish_entry_names = list(existing_entries)
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_PUBLISHING,
            original_target_entries=original_target_entries,
            publish_entry_names=publish_entry_names,
            publish_entry_snapshots=source_snapshots,
        )
        publish_started = True

        for entry_name in existing_entries:
            if path_chain_has_symlink(target_root) or path_chain_has_symlink(transaction_root):
                raise StorageMigrationError(
                    "migration_path_changed",
                    "发布期间目标路径或事务路径的文件系统边界发生变化，已停止迁移。",
                )
            staged_entry = _checked_migration_entry_path(staged_root, entry_name)
            target_entry = _checked_migration_entry_path(target_root, entry_name)
            backup_entry = _checked_migration_entry_path(backup_root, entry_name)
            target_exists = target_entry.exists() or target_entry.is_symlink()
            expected_target_snapshot = publish_target_snapshot.get(entry_name)
            if expected_target_snapshot is None:
                target_unchanged = not target_exists
            else:
                target_unchanged = bool(
                    target_exists
                    and _snapshot_path(target_entry) == expected_target_snapshot
                )
            if not target_unchanged:
                raise StorageMigrationError(
                    "target_changed_during_publish",
                    f"目标路径在发布前发生了变化，已停止迁移以保留新数据: {entry_name}",
                )

            if target_exists:
                target_entry = _checked_migration_entry_path(target_root, entry_name)
                backup_entry = _checked_migration_entry_path(backup_root, entry_name)
                backup_entry.parent.mkdir(parents=True, exist_ok=True)
                fsync_directory_best_effort(backup_entry.parent.parent)
                _durable_replace(target_entry, backup_entry)
                if _snapshot_path(backup_entry) != expected_target_snapshot:
                    raise StorageMigrationError(
                        "target_changed_during_publish",
                        f"目标路径在备份切换窗口发生了变化，已保留事务证据: {entry_name}",
                    )
            target_entry = _checked_migration_entry_path(target_root, entry_name)
            target_entry.parent.mkdir(parents=True, exist_ok=True)
            fsync_directory_best_effort(target_entry.parent.parent)
            staged_entry = _checked_migration_entry_path(staged_root, entry_name)
            try:
                _durable_publish_without_replacing(staged_entry, target_entry)
            except OSError as exc:
                if target_entry.exists() or target_entry.is_symlink():
                    raise StorageMigrationError(
                        "target_changed_during_publish",
                        f"目标路径在发布切换窗口出现了新数据，已停止迁移: {entry_name}",
                    ) from exc
                raise

        for entry_name, expected_snapshot in source_snapshots.items():
            actual_snapshot = _snapshot_path(
                _checked_migration_entry_path(target_root, entry_name)
            )
            if actual_snapshot != expected_snapshot:
                logger.warning(
                    "Storage migration verification failed for %s: expected=%s actual=%s",
                    entry_name,
                    expected_snapshot,
                    actual_snapshot,
                )
                raise StorageMigrationError(
                    "verification_failed",
                    f"迁移校验失败：{entry_name} 未完整发布到目标路径。",
                )

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_COMMITTING,
        )

        policy_payload = save_storage_policy(
            config_manager,
            selected_root=target_root,
            selection_source=selection_source,
            anchor_root=normalized_anchor_root,
        )

        from utils.cloudsave_runtime import ROOT_MODE_NORMAL, set_root_mode

        legacy_cleanup_pending = is_retained_root_cleanup_available(
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

        completed_at = _utc_now_iso()
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
            committed_at=completed_at,
            completed_at=completed_at,
        )
        try:
            if not _remove_transaction_root_if_owned(
                payload,
                transaction_root,
                txid,
            ):
                logger.warning(
                    "Preserving completed migration transaction after ownership changed: %s",
                    transaction_root,
                )
        except Exception as cleanup_exc:
            logger.warning("Failed to clean completed migration transaction: %s", cleanup_exc)
        return {
            "attempted": True,
            "completed": True,
            "payload": payload,
            "policy": policy_payload,
            "source_root": str(source_root),
            "target_root": str(target_root),
            "anchor_root": str(normalized_anchor_root),
        }
    except StorageMigrationError as exc:
        rollback_error: Exception | None = None
        if (
            target_root is not None
            and transaction_root is not None
            and (transaction_root.exists() or transaction_root.is_symlink())
        ):
            try:
                if publish_started:
                    if not _transaction_root_is_owned(payload, transaction_root, txid):
                        raise StorageMigrationError(
                            "rollback_transaction_unowned",
                            "迁移事务目录所有权无法验证，已停止自动回滚。",
                        )
                    _rollback_published_entries(
                        target_root,
                        transaction_root,
                        original_target_entries,
                        publish_entry_names,
                        target_baseline or {},
                        publish_entry_snapshots or source_snapshots,
                    )
            except Exception as caught_rollback_error:
                rollback_error = caught_rollback_error
                logger.exception("Failed to roll back storage migration target")
        if rollback_error is not None:
            return _finish_failure(
                "rollback_failed",
                f"迁移失败且目标回滚未完成: {rollback_error}",
                rollback_required=True,
            )
        return _finish_rolled_back_failure(exc.error_code, exc.message)
    except Exception as exc:
        logger.exception("Unexpected storage migration failure")
        rollback_error: Exception | None = None
        if (
            target_root is not None
            and transaction_root is not None
            and (transaction_root.exists() or transaction_root.is_symlink())
        ):
            try:
                if publish_started:
                    if not _transaction_root_is_owned(payload, transaction_root, txid):
                        raise StorageMigrationError(
                            "rollback_transaction_unowned",
                            "迁移事务目录所有权无法验证，已停止自动回滚。",
                        )
                    _rollback_published_entries(
                        target_root,
                        transaction_root,
                        original_target_entries,
                        publish_entry_names,
                        target_baseline or {},
                        publish_entry_snapshots or source_snapshots,
                    )
            except Exception as caught_rollback_error:
                rollback_error = caught_rollback_error
                logger.exception("Failed to roll back unexpected storage migration failure")
        if rollback_error is not None:
            return _finish_failure(
                "rollback_failed",
                f"迁移发生未预期错误且目标回滚未完成: {rollback_error}",
                rollback_required=True,
            )
        wrapped_exc = StorageMigrationError("storage_migration_unexpected", f"执行存储迁移时发生未预期错误: {exc}")
        return _finish_rolled_back_failure(wrapped_exc.error_code, wrapped_exc.message)


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
    # A deleted checkpoint is itself recovery authority: callers may restore
    # the normal root state immediately after this returns.  On POSIX, do not
    # report that rollback as complete until the directory entry removal is
    # durable; otherwise a failed pending intent can reappear after power loss.
    _fsync_migration_directory(migration_path.parent)


def _migration_transaction_evidence_is_present(
    payload: dict[str, Any] | None,
) -> bool:
    """Return true unless a checkpoint proves its transaction is absent.

    A terminal ``failed`` state can still own the only staged copy after the
    source disappeared or changed.  Such a checkpoint is not replaceable: its
    txid, owner token and transaction path are the authority needed for later
    recovery.  Presence is intentionally independent of marker validity; an
    untrusted or unreadable entry is a reason to preserve the checkpoint, not
    permission to orphan it.
    """

    if not isinstance(payload, dict):
        return False
    raw_txid = str(payload.get("txid") or "").strip()
    raw_transaction_root = str(payload.get("transaction_root") or "").strip()
    if not raw_txid and not raw_transaction_root:
        # Pre-transaction failures (including legacy checkpoints) have no
        # owned staging location to orphan.
        return False
    try:
        txid = _validate_txid(raw_txid)
        target_root = normalize_runtime_root(str(payload.get("target_root") or "").strip())
        transaction_root, checkpoint_bound = _checkpoint_transaction_root(
            payload,
            target_root,
            txid,
        )
    except Exception:
        return True

    candidates = [transaction_root]
    if not checkpoint_bound:
        candidates.append(_legacy_transaction_root_for(target_root, txid))
    candidates.extend(_private_directory_quarantine_path(path) for path in tuple(candidates))
    for candidate in candidates:
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return False


def storage_migration_retains_recovery_evidence(
    payload: dict[str, Any] | None,
) -> bool:
    """Return whether a failed/recovery checkpoint still maps recovery data."""

    return bool(
        isinstance(payload, dict)
        and str(payload.get("status") or "").strip()
        in {
            STORAGE_MIGRATION_STATUS_FAILED,
            STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
        }
        and _migration_transaction_evidence_is_present(payload)
    )


def failed_storage_migration_retains_recovery_evidence(
    payload: dict[str, Any] | None,
) -> bool:
    """Compatibility predicate for legacy terminal failed checkpoints."""

    return bool(
        isinstance(payload, dict)
        and str(payload.get("status") or "").strip()
        == STORAGE_MIGRATION_STATUS_FAILED
        and _migration_transaction_evidence_is_present(payload)
    )
