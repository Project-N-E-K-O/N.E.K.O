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
import subprocess
import sys
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
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
_TRANSACTION_CAPACITY_ENTRY_RESERVE = 8
_MIGRATION_ERROR_MESSAGE_MAX_BYTES = 16 * 1024
# workshop_config.json is written through the desktop backend's bounded JSON
# request surface. Keep migration-side rebasing within the same 16 MiB memory
# contract so a damaged on-disk file fails closed instead of exhausting the
# packaged launcher while it holds the startup gate.
_WORKSHOP_CONFIG_REWRITE_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class _DirectoryModeRestoreEntry:
    relative_parts: tuple[str, ...]
    identity: os.stat_result
    original_mode: int


@dataclass
class _DirectoryModeRestorePlan:
    root_fd: int = -1
    entries: list[_DirectoryModeRestoreEntry] = field(default_factory=list)


@dataclass
class _CopyCapacity:
    required_bytes: int = 0
    entry_count: int = 0


@dataclass
class _PosixPublishRoots:
    target_root_fd: int
    transaction_root_fd: int
    staged_root_fd: int
    backup_root_fd: int
    mount_identity: tuple[str, int]

    def close(self) -> None:
        for field_name in (
            "backup_root_fd",
            "staged_root_fd",
            "transaction_root_fd",
            "target_root_fd",
        ):
            fd = int(getattr(self, field_name))
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
                setattr(self, field_name, -1)


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


def _bounded_migration_error_message(value: Any) -> str:
    message = str(value or "").strip()
    # POSIX exposes undecodable filename bytes through surrogateescape.  Error
    # text can therefore contain lone surrogates even though the fixed-anchor
    # JSON checkpoint must always be valid UTF-8. Preserve those bytes as their
    # explicit \udcXX spelling before applying the durable size bound.
    encoded = message.encode("utf-8", errors="backslashreplace")
    if len(encoded) <= _MIGRATION_ERROR_MESSAGE_MAX_BYTES:
        return encoded.decode("utf-8")
    suffix = "…[truncated]"
    suffix_bytes = suffix.encode("utf-8")
    prefix = encoded[: _MIGRATION_ERROR_MESSAGE_MAX_BYTES - len(suffix_bytes)]
    return prefix.decode("utf-8", errors="ignore") + suffix


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


def _opened_mount_identity(fd: int) -> tuple[str, int]:
    """Return the mount containing an already-open POSIX entry.

    Linux bind mounts deliberately keep the source device id, so ``st_dev``
    alone cannot prove that a recursive operation stays on one mount.
    """

    metadata = os.fstat(fd)
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as fdinfo:
                for line in fdinfo:
                    field, separator, value = line.partition(":")
                    if field == "mnt_id" and separator:
                        return "linux-mnt-id", int(value.strip())
        except (OSError, UnicodeError, ValueError) as exc:
            raise StorageMigrationError(
                "mount_identity_unavailable",
                "无法确认迁移路径的 Linux 挂载边界，已安全停止迁移。",
            ) from exc
        raise StorageMigrationError(
            "mount_identity_unavailable",
            "无法确认迁移路径的 Linux 挂载边界，已安全停止迁移。",
        )
    return "device", int(metadata.st_dev)


def _ensure_opened_entry_on_mount(
    fd: int,
    expected_mount_identity: tuple[str, int],
    path: Path | str,
) -> None:
    if _opened_mount_identity(fd) != expected_mount_identity:
        raise StorageMigrationError(
            "nested_mount_unsupported",
            f"迁移路径包含嵌套挂载，已停止以避免访问或删除挂载外数据: {path}",
        )


def _decode_mount_path(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _parse_macos_mount_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        source_and_mount = line.rsplit(" (", 1)[0]
        _source, separator, mount_path = source_and_mount.partition(" on ")
        if separator and mount_path:
            paths.append(mount_path)
    return paths


def _normalize_mount_paths(raw_paths: list[str]) -> list[Path]:
    return [
        Path(os.path.abspath(_decode_mount_path(value)))
        for value in raw_paths
    ]


def _mounted_paths() -> list[Path]:
    """List current mount points without traversing potentially locked trees."""

    raw_paths: list[str] = []
    if sys.platform.startswith("linux"):
        try:
            lines = Path("/proc/self/mountinfo").read_text(
                encoding="utf-8",
                errors="strict",
            ).splitlines()
            raw_paths = [
                fields[4]
                for line in lines
                if len(fields := line.split()) >= 5
            ]
        except (OSError, UnicodeError, ValueError) as exc:
            raise StorageMigrationError(
                "mount_identity_unavailable",
                "无法枚举 Linux 挂载边界，已安全停止迁移清理。",
            ) from exc
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/sbin/mount"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            raw_paths = _parse_macos_mount_paths(result.stdout)
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise StorageMigrationError(
                "mount_identity_unavailable",
                "无法枚举 macOS 挂载边界，已安全停止迁移清理。",
            ) from exc
    else:
        raise StorageMigrationError(
            "mount_identity_unavailable",
            "当前平台无法枚举迁移路径的挂载边界，已安全停止迁移清理。",
        )
    return _normalize_mount_paths(raw_paths)


def _preflight_named_mounts_below(path: Path) -> None:
    """Reject a nested mount before changing permissions or moving its parent."""

    if os.name == "nt":
        return
    normalized_root = Path(os.path.abspath(path))
    for mount_path in _mounted_paths():
        try:
            common = Path(os.path.commonpath((normalized_root, mount_path)))
        except ValueError:
            continue
        if common == normalized_root and mount_path != normalized_root:
            raise StorageMigrationError(
                "nested_mount_unsupported",
                f"迁移路径包含嵌套挂载，已停止以避免移动或删除挂载外数据: {mount_path}",
            )


def _open_verified_directory(path: Path) -> int:
    before = path.lstat()
    if _is_link_like_metadata(before) or not stat.S_ISDIR(before.st_mode):
        raise StorageMigrationError(
            "path_type_unsupported",
            f"迁移目录不是可安全遍历的真实目录: {path}",
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.fspath(path), flags)
    try:
        opened = os.fstat(fd)
        named = path.lstat()
        if (
            _is_link_like_metadata(opened)
            or _is_link_like_metadata(named)
            or not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, named)
        ):
            raise StorageMigrationError(
                "migration_path_changed",
                f"迁移目录在安全检查期间被替换: {path}",
            )
        return fd
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise


def _open_windows_directory_rename_guard(
    path: Path,
    expected_identity: os.stat_result,
) -> int:
    """Pin a real Windows directory while denying rename/delete sharing."""

    if os.name != "nt":
        raise OSError("Windows directory guards are unavailable on this platform")

    import ctypes
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

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
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    native_path = str(path)
    if not native_path.startswith("\\\\?\\"):
        if native_path.startswith("\\\\"):
            native_path = "\\\\?\\UNC\\" + native_path[2:]
        else:
            native_path = "\\\\?\\" + native_path

    file_list_directory = 0x0001
    file_read_attributes = 0x0080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_attribute_directory = 0x0010
    file_attribute_reparse_point = 0x0400
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle_value = ctypes.c_void_p(-1).value
    handle = kernel32.CreateFileW(
        native_path,
        # Attribute-only access is exempt from normal share accounting on
        # Windows. Request directory-list access as well so omitting
        # FILE_SHARE_DELETE actually prevents rename/delete while this guard
        # is alive, without requiring DELETE access ourselves.
        file_list_directory | file_read_attributes,
        # Deliberately omit FILE_SHARE_DELETE. Windows then refuses renaming or
        # deleting this directory until the migration releases the guard.
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        named_after = path.lstat()
        if (
            info.attributes & file_attribute_reparse_point
            or not info.attributes & file_attribute_directory
            or _is_link_like_metadata(named_after)
            or not stat.S_ISDIR(named_after.st_mode)
            or not os.path.samestat(expected_identity, named_after)
        ):
            raise StorageMigrationError(
                "transaction_ownership_changed",
                f"迁移事务目录在 Windows 句柄固定期间被替换: {path}",
            )
        return int(handle)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _close_windows_directory_rename_guard(handle: int) -> None:
    if handle < 0 or os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle(handle)


def _ensure_opened_directory_still_named(
    path: Path,
    fd: int,
    *,
    error_code: str = "migration_path_changed",
    message: str | None = None,
) -> None:
    """Require a pinned directory to remain the inode named by ``path``."""

    try:
        opened = os.fstat(fd)
        named = path.lstat()
    except OSError as exc:
        raise StorageMigrationError(
            error_code,
            message or f"迁移目录在操作期间发生变化: {path}: {exc}",
        ) from exc
    if (
        _is_link_like_metadata(opened)
        or _is_link_like_metadata(named)
        or not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or not os.path.samestat(opened, named)
    ):
        raise StorageMigrationError(
            error_code,
            message or f"迁移目录在操作期间被替换: {path}",
        )


def _open_or_create_posix_child_directory(
    parent_fd: int,
    name: str,
    display_path: Path,
    *,
    expected_mount_identity: tuple[str, int],
    allow_existing: bool,
    create_missing: bool = True,
    durable_creation: bool = False,
) -> int:
    """Create/open one child below a pinned POSIX parent without path re-resolution."""

    if not name or name in {".", ".."} or Path(name).name != name:
        raise StorageMigrationError(
            "staging_entry_changed",
            f"迁移暂存目录名称无效: {display_path}",
        )
    _ensure_opened_entry_on_mount(
        parent_fd,
        expected_mount_identity,
        display_path.parent,
    )
    if create_missing:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            if not allow_existing:
                raise StorageMigrationError(
                    "staging_entry_exists",
                    f"迁移暂存条目已存在，无法安全覆盖: {display_path}",
                ) from exc
        except OSError as exc:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存目录无法安全创建: {display_path}: {exc}",
            ) from exc
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    child_fd = -1
    try:
        named_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        child_fd = os.open(name, directory_flags, dir_fd=parent_fd)
        opened = os.fstat(child_fd)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            _is_link_like_metadata(named_before)
            or _is_link_like_metadata(opened)
            or _is_link_like_metadata(named_after)
            or not stat.S_ISDIR(named_before.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named_after.st_mode)
            or not os.path.samestat(named_before, opened)
            or not os.path.samestat(opened, named_after)
        ):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存目录在打开期间被替换: {display_path}",
            )
        _ensure_opened_entry_on_mount(
            child_fd,
            expected_mount_identity,
            display_path,
        )
        if create_missing and durable_creation:
            _fsync_opened_migration_directory(parent_fd, display_path.parent)
            named_after_flush = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                _is_link_like_metadata(named_after_flush)
                or not stat.S_ISDIR(named_after_flush.st_mode)
                or not os.path.samestat(opened, named_after_flush)
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存目录在持久化期间被替换: {display_path}",
                )
        return child_fd
    except StorageMigrationError:
        if child_fd >= 0:
            os.close(child_fd)
        raise
    except OSError as exc:
        if child_fd >= 0:
            os.close(child_fd)
        raise StorageMigrationError(
            "staging_entry_changed",
            f"迁移暂存目录在打开期间发生变化: {display_path}: {exc}",
        ) from exc


def _open_or_create_posix_directory_chain(
    root_fd: int,
    root_display: Path,
    relative_parts: tuple[str, ...],
    *,
    expected_mount_identity: tuple[str, int],
    create_missing: bool = True,
    durable_creation: bool = False,
) -> int:
    """Return a pinned parent below a trusted staging root, creating safe parents."""

    current_fd = os.dup(root_fd)
    current_display = root_display
    try:
        _ensure_opened_entry_on_mount(
            current_fd,
            expected_mount_identity,
            current_display,
        )
        for part in relative_parts:
            child_display = current_display / part
            child_fd = _open_or_create_posix_child_directory(
                current_fd,
                part,
                child_display,
                expected_mount_identity=expected_mount_identity,
                allow_existing=True,
                create_missing=create_missing,
                durable_creation=durable_creation,
            )
            os.close(current_fd)
            current_fd = child_fd
            current_display = child_display
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_posix_publish_roots(
    payload: dict[str, Any],
    target_root: Path,
    transaction_root: Path,
    txid: str,
    *,
    expected_target_identity: os.stat_result | None = None,
    expected_transaction_identity: os.stat_result | None = None,
) -> _PosixPublishRoots:
    """Pin every root used by POSIX publication and rollback."""

    if os.name == "nt":
        raise OSError("POSIX publish roots are unavailable on Windows")
    target_root_fd = -1
    transaction_root_fd = -1
    staged_root_fd = -1
    backup_root_fd = -1
    try:
        target_root_fd = _open_verified_directory(target_root)
        opened_target = os.fstat(target_root_fd)
        if (
            expected_target_identity is not None
            and not os.path.samestat(expected_target_identity, opened_target)
        ):
            raise StorageMigrationError(
                "migration_path_changed",
                "迁移目标根在发布固定前被替换。",
            )
        target_mount_identity = _opened_mount_identity(target_root_fd)
        if transaction_root.parent == target_root:
            transaction_root_fd = _open_or_create_posix_child_directory(
                target_root_fd,
                transaction_root.name,
                transaction_root,
                expected_mount_identity=target_mount_identity,
                allow_existing=True,
                create_missing=False,
            )
        else:
            # Recovery compatibility for version-2 checkpoints whose private
            # transaction was a sibling of the selected target root.
            transaction_root_fd = _open_verified_directory(transaction_root)
        opened_transaction = os.fstat(transaction_root_fd)
        if (
            expected_transaction_identity is not None
            and not os.path.samestat(
                expected_transaction_identity,
                opened_transaction,
            )
        ):
            raise StorageMigrationError(
                "transaction_ownership_changed",
                "迁移事务目录在发布固定前被替换。",
            )
        if not _transaction_directory_fd_is_owned(
            payload,
            transaction_root_fd,
            txid,
        ):
            raise StorageMigrationError(
                "transaction_ownership_changed",
                "迁移事务目录在发布固定时无法证明所有权。",
            )
        transaction_mount_identity = _opened_mount_identity(transaction_root_fd)
        if target_mount_identity != transaction_mount_identity:
            raise StorageMigrationError(
                "nested_mount_unsupported",
                "迁移事务目录与目标根不在同一挂载边界。",
            )
        staged_root = transaction_root / "staged"
        backup_root = transaction_root / "backup"
        staged_root_fd = _open_or_create_posix_child_directory(
            transaction_root_fd,
            "staged",
            staged_root,
            expected_mount_identity=transaction_mount_identity,
            allow_existing=True,
            create_missing=False,
        )
        backup_root_fd = _open_or_create_posix_child_directory(
            transaction_root_fd,
            "backup",
            backup_root,
            expected_mount_identity=transaction_mount_identity,
            allow_existing=True,
            create_missing=False,
        )
        return _PosixPublishRoots(
            target_root_fd=target_root_fd,
            transaction_root_fd=transaction_root_fd,
            staged_root_fd=staged_root_fd,
            backup_root_fd=backup_root_fd,
            mount_identity=transaction_mount_identity,
        )
    except BaseException:
        for fd in (
            backup_root_fd,
            staged_root_fd,
            transaction_root_fd,
            target_root_fd,
        ):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
        raise


def _ensure_posix_publish_roots_still_named(
    roots: _PosixPublishRoots,
    target_root: Path,
    transaction_root: Path,
) -> None:
    if path_chain_has_symlink(target_root) or path_chain_has_symlink(transaction_root):
        raise StorageMigrationError(
            "migration_path_changed",
            "迁移目标或事务路径的祖先在发布期间被替换为符号链接。",
        )
    _ensure_opened_directory_still_named(
        target_root,
        roots.target_root_fd,
        error_code="migration_path_changed",
        message=f"迁移目标根在发布或回滚期间被替换: {target_root}",
    )
    _ensure_opened_directory_still_named(
        transaction_root,
        roots.transaction_root_fd,
        error_code="transaction_ownership_changed",
        message=f"迁移事务目录在发布或回滚期间被替换: {transaction_root}",
    )
    _ensure_opened_directory_still_named(
        transaction_root / "staged",
        roots.staged_root_fd,
        error_code="staging_entry_changed",
        message=f"迁移暂存根在发布或回滚期间被替换: {transaction_root / 'staged'}",
    )
    _ensure_opened_directory_still_named(
        transaction_root / "backup",
        roots.backup_root_fd,
        error_code="staging_entry_changed",
        message=f"迁移备份根在发布或回滚期间被替换: {transaction_root / 'backup'}",
    )


def _open_posix_relative_parent(
    root_fd: int,
    root_display: Path,
    relative_path: str,
    *,
    expected_mount_identity: tuple[str, int],
    create_missing: bool,
) -> tuple[int, str, Path]:
    parts = Path(relative_path).parts
    if not parts:
        raise StorageMigrationError(
            "migration_path_changed",
            "迁移发布条目名称为空。",
        )
    parent_fd = _open_or_create_posix_directory_chain(
        root_fd,
        root_display,
        parts[:-1],
        expected_mount_identity=expected_mount_identity,
        create_missing=create_missing,
        durable_creation=create_missing,
    )
    return parent_fd, parts[-1], root_display.joinpath(*parts[:-1])


def _posix_named_entry_exists(parent_fd: int, name: str) -> bool:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if _is_link_like_metadata(metadata):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移发布条目被替换为符号链接或重解析点: {name}",
        )
    return True


def _snapshot_posix_entry_at(
    parent_fd: int,
    name: str,
    display_path: Path,
    *,
    expected_mount_identity: tuple[str, int],
) -> dict[str, int | str]:
    """Hash one entry without resolving any replaceable ancestor path."""

    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return {"kind": "missing", "file_count": 0, "total_bytes": 0}
    if _is_link_like_metadata(named):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移校验不支持符号链接: {display_path}",
        )

    def _hash_opened_file(file_fd: int, file_display: Path) -> tuple[int, str]:
        _ensure_opened_entry_on_mount(
            file_fd,
            expected_mount_identity,
            file_display,
        )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        return total, digest.hexdigest()

    def _stable_file_fields(metadata: os.stat_result) -> tuple[int, int, int]:
        return (
            int(metadata.st_size),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
        )

    if stat.S_ISREG(named.st_mode):
        file_fd = -1
        try:
            file_fd = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(file_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not os.path.samestat(named, opened)
                or _stable_file_fields(named) != _stable_file_fields(opened)
            ):
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移校验文件在打开期间被替换: {display_path}",
                )
            total_bytes, digest = _hash_opened_file(file_fd, display_path)
            named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened_after = os.fstat(file_fd)
            if (
                not os.path.samestat(opened, opened_after)
                or not os.path.samestat(opened_after, named_after)
                or _stable_file_fields(opened) != _stable_file_fields(opened_after)
                or _stable_file_fields(opened_after)
                != _stable_file_fields(named_after)
                or total_bytes != opened_after.st_size
            ):
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移校验文件在读取期间发生变化: {display_path}",
                )
            return {
                "kind": "file",
                "file_count": 1,
                "total_bytes": total_bytes,
                "sha256": digest,
            }
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    if not stat.S_ISDIR(named.st_mode):
        raise StorageMigrationError(
            "path_type_unsupported",
            f"迁移校验不支持该文件类型: {display_path}",
        )

    root_fd = -1
    try:
        root_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened_root = os.fstat(root_fd)
        if not os.path.samestat(named, opened_root):
            raise StorageMigrationError(
                "migration_path_changed",
                f"迁移校验目录在打开期间被替换: {display_path}",
            )
        _ensure_opened_entry_on_mount(
            root_fd,
            expected_mount_identity,
            display_path,
        )
        manifest_digest = hashlib.sha256()
        total_bytes = 0
        file_count = 0

        def _walk(directory_fd: int, relative_root: Path, directory_display: Path) -> None:
            nonlocal total_bytes, file_count
            with os.scandir(directory_fd) as scanned:
                names = sorted(entry.name for entry in scanned)
            directories: list[tuple[str, os.stat_result]] = []
            files: list[tuple[str, os.stat_result]] = []
            for child_name in names:
                child_metadata = os.stat(
                    child_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                child_display = directory_display / child_name
                if _is_link_like_metadata(child_metadata):
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"迁移校验不支持符号链接: {child_display}",
                    )
                if stat.S_ISDIR(child_metadata.st_mode):
                    directories.append((child_name, child_metadata))
                elif stat.S_ISREG(child_metadata.st_mode):
                    files.append((child_name, child_metadata))
                else:
                    raise StorageMigrationError(
                        "path_type_unsupported",
                        f"迁移校验不支持该文件类型: {child_display}",
                    )
            for child_name, _child_metadata in directories:
                manifest_digest.update(
                    b"D\0" + os.fsencode((relative_root / child_name).as_posix()) + b"\0"
                )
            for child_name, child_metadata in files:
                child_display = directory_display / child_name
                file_fd = -1
                try:
                    file_fd = os.open(
                        child_name,
                        os.O_RDONLY
                        | os.O_NONBLOCK
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    opened_file = os.fstat(file_fd)
                    if (
                        not os.path.samestat(child_metadata, opened_file)
                        or _stable_file_fields(child_metadata)
                        != _stable_file_fields(opened_file)
                    ):
                        raise StorageMigrationError(
                            "migration_path_changed",
                            f"迁移校验文件在打开期间被替换: {child_display}",
                        )
                    file_bytes, file_digest = _hash_opened_file(file_fd, child_display)
                    named_after = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    opened_after = os.fstat(file_fd)
                    if (
                        not os.path.samestat(opened_file, opened_after)
                        or not os.path.samestat(opened_after, named_after)
                        or _stable_file_fields(opened_file)
                        != _stable_file_fields(opened_after)
                        or _stable_file_fields(opened_after)
                        != _stable_file_fields(named_after)
                        or file_bytes != opened_after.st_size
                    ):
                        raise StorageMigrationError(
                            "migration_path_changed",
                            f"迁移校验文件在读取期间发生变化: {child_display}",
                        )
                finally:
                    if file_fd >= 0:
                        os.close(file_fd)
                relative_file = (relative_root / child_name).as_posix()
                manifest_digest.update(
                    b"F\0"
                    + os.fsencode(relative_file)
                    + b"\0"
                    + str(file_bytes).encode("ascii")
                    + b"\0"
                    + file_digest.encode("ascii")
                    + b"\0"
                )
                total_bytes += file_bytes
                file_count += 1
            for child_name, child_metadata in directories:
                child_fd = -1
                child_display = directory_display / child_name
                try:
                    child_fd = os.open(
                        child_name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    opened_child = os.fstat(child_fd)
                    if not os.path.samestat(child_metadata, opened_child):
                        raise StorageMigrationError(
                            "migration_path_changed",
                            f"迁移校验目录在打开期间被替换: {child_display}",
                        )
                    _ensure_opened_entry_on_mount(
                        child_fd,
                        expected_mount_identity,
                        child_display,
                    )
                    _walk(
                        child_fd,
                        relative_root / child_name,
                        child_display,
                    )
                    named_after = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if not os.path.samestat(opened_child, named_after):
                        raise StorageMigrationError(
                            "migration_path_changed",
                            f"迁移校验目录在遍历期间被替换: {child_display}",
                        )
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)

            with os.scandir(directory_fd) as final_scanned:
                final_names = sorted(entry.name for entry in final_scanned)
            if final_names != names:
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移校验目录在遍历期间发生变化: {directory_display}",
                )

        _walk(root_fd, Path(), display_path)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not os.path.samestat(opened_root, named_after):
            raise StorageMigrationError(
                "migration_path_changed",
                f"迁移校验目录在遍历期间被替换: {display_path}",
            )
        return {
            "kind": "dir",
            "file_count": file_count,
            "total_bytes": total_bytes,
            "sha256": manifest_digest.hexdigest(),
        }
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _open_posix_existing_relative_parent(
    root_fd: int,
    root_display: Path,
    relative_path: str,
    *,
    expected_mount_identity: tuple[str, int],
) -> tuple[int, str, Path] | None:
    """Pin an existing entry parent, or return ``None`` when a parent is absent."""

    parts = Path(relative_path).parts
    if not parts:
        raise StorageMigrationError(
            "migration_path_changed",
            "迁移发布条目名称为空。",
        )
    current_fd = os.dup(root_fd)
    current_display = root_display
    try:
        _ensure_opened_entry_on_mount(
            current_fd,
            expected_mount_identity,
            current_display,
        )
        for part in parts[:-1]:
            if not _posix_named_entry_exists(current_fd, part):
                os.close(current_fd)
                current_fd = -1
                return None
            child_display = current_display / part
            child_fd = _open_or_create_posix_child_directory(
                current_fd,
                part,
                child_display,
                expected_mount_identity=expected_mount_identity,
                allow_existing=True,
                create_missing=False,
            )
            os.close(current_fd)
            current_fd = child_fd
            current_display = child_display
        result = (current_fd, parts[-1], current_display)
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _snapshot_posix_relative_entry(
    root_fd: int,
    root_display: Path,
    relative_path: str,
    *,
    expected_mount_identity: tuple[str, int],
) -> dict[str, int | str]:
    opened_parent = _open_posix_existing_relative_parent(
        root_fd,
        root_display,
        relative_path,
        expected_mount_identity=expected_mount_identity,
    )
    if opened_parent is None:
        return {"kind": "missing", "file_count": 0, "total_bytes": 0}
    parent_fd, name, parent_display = opened_parent
    try:
        return _snapshot_posix_entry_at(
            parent_fd,
            name,
            parent_display / name,
            expected_mount_identity=expected_mount_identity,
        )
    finally:
        os.close(parent_fd)


def _stat_posix_relative_entry(
    root_fd: int,
    root_display: Path,
    relative_path: str,
    *,
    expected_mount_identity: tuple[str, int],
) -> os.stat_result | None:
    opened_parent = _open_posix_existing_relative_parent(
        root_fd,
        root_display,
        relative_path,
        expected_mount_identity=expected_mount_identity,
    )
    if opened_parent is None:
        return None
    parent_fd, name, _parent_display = opened_parent
    try:
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if _is_link_like_metadata(metadata):
            raise StorageMigrationError(
                "path_symlink_unsupported",
                f"迁移发布条目被替换为符号链接或重解析点: {relative_path}",
            )
        return metadata
    finally:
        os.close(parent_fd)


def _snapshot_posix_runtime_entries_at(
    root_fd: int,
    root_display: Path,
    *,
    expected_mount_identity: tuple[str, int],
) -> dict[str, dict[str, int | str]]:
    snapshots: dict[str, dict[str, int | str]] = {}
    for entry in RUNTIME_STORAGE_ENTRIES:
        snapshot = _snapshot_posix_relative_entry(
            root_fd,
            root_display,
            entry.relative_path,
            expected_mount_identity=expected_mount_identity,
        )
        if snapshot["kind"] != "missing":
            snapshots[entry.relative_path] = snapshot
    return snapshots


def _snapshot_posix_runtime_entries(
    roots: _PosixPublishRoots,
    target_root: Path,
) -> dict[str, dict[str, int | str]]:
    return _snapshot_posix_runtime_entries_at(
        roots.target_root_fd,
        target_root,
        expected_mount_identity=roots.mount_identity,
    )


def _ensure_directory_path_on_mount(
    path: Path,
    expected_mount_identity: tuple[str, int] | None,
) -> None:
    if expected_mount_identity is None or os.name == "nt":
        return
    fd = _open_verified_directory(path)
    try:
        _ensure_opened_entry_on_mount(fd, expected_mount_identity, path)
    finally:
        os.close(fd)


def _open_mode_restore_entry(
    root_fd: int,
    relative_parts: tuple[str, ...],
) -> int:
    """Open one recorded directory below a pinned root without following links."""

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    if not relative_parts:
        return os.dup(root_fd)
    current_fd = root_fd
    owns_current = False
    try:
        for part in relative_parts:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            if owns_current:
                os.close(current_fd)
            current_fd = next_fd
            owns_current = True
        return current_fd
    except BaseException:
        if owns_current:
            os.close(current_fd)
        raise


def _restore_recorded_directory_modes(
    root_fd: int,
    entries: list[_DirectoryModeRestoreEntry],
    path: Path,
) -> None:
    restore_errors: list[OSError] = []
    for entry in reversed(entries):
        restore_fd = -1
        try:
            restore_fd = _open_mode_restore_entry(root_fd, entry.relative_parts)
            if not os.path.samestat(entry.identity, os.fstat(restore_fd)):
                raise OSError("refusing to chmod a replaced migration directory")
            os.fchmod(restore_fd, entry.original_mode)
        except OSError as exc:
            restore_errors.append(exc)
        finally:
            if restore_fd >= 0:
                os.close(restore_fd)
    if restore_errors:
        raise StorageMigrationError(
            "permission_restore_failed",
            f"迁移清理失败后无法恢复事务目录权限: {path}",
        )


def _preflight_opened_directory_tree_mounts(
    path: Path,
    *,
    root_fd: int,
    expected_mount_identity: tuple[str, int] | None = None,
    expected_root_identity: os.stat_result | None = None,
    make_traversable: bool = False,
    retained_mode_plan: _DirectoryModeRestorePlan | None = None,
) -> tuple[str, int] | None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    tree_mount_identity = expected_mount_identity or _opened_mount_identity(root_fd)
    modified_modes: list[_DirectoryModeRestoreEntry] = []
    retained_root_fd = -1

    if retained_mode_plan is not None and (
        retained_mode_plan.root_fd >= 0 or retained_mode_plan.entries
    ):
        raise ValueError("directory mode restore plan must be empty")

    if expected_root_identity is not None and not os.path.samestat(
        expected_root_identity,
        os.fstat(root_fd),
    ):
        raise StorageMigrationError(
            "migration_path_changed",
            f"迁移目录在安全检查前被替换: {path}",
        )

    def _make_traversable(
        directory_fd: int,
        relative_parts: tuple[str, ...],
    ) -> None:
        identity = os.fstat(directory_fd)
        mode = stat.S_IMODE(identity.st_mode)
        updated_mode = mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        if updated_mode == mode:
            return
        os.fchmod(directory_fd, updated_mode)
        modified_modes.append(
            _DirectoryModeRestoreEntry(relative_parts, identity, mode)
        )

    def _open_child_directory(
        directory_fd: int,
        child_name: str,
        before: os.stat_result,
        child_display: Path,
        relative_parts: tuple[str, ...],
    ) -> int:
        try:
            return os.open(child_name, directory_flags, dir_fd=directory_fd)
        except PermissionError:
            if not make_traversable:
                raise

        # The system mount table was checked before cleanup permission repair.
        # Recheck this exact inaccessible entry before chmod so a mount created
        # after that snapshot is still refused rather than modified.
        if sys.platform.startswith("linux"):
            path_flag = getattr(os, "O_PATH", 0)
            if not path_flag:
                raise StorageMigrationError(
                    "mount_identity_unavailable",
                    f"无法安全检查不可访问的迁移目录: {child_display}",
                )
            probe_fd = os.open(
                child_name,
                path_flag | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                if not os.path.samestat(before, os.fstat(probe_fd)):
                    raise StorageMigrationError(
                        "migration_path_changed",
                        f"迁移目录在权限恢复前被替换: {child_display}",
                    )
                _ensure_opened_entry_on_mount(
                    probe_fd,
                    tree_mount_identity,
                    child_display,
                )
            finally:
                os.close(probe_fd)
        elif tree_mount_identity != ("device", int(before.st_dev)):
            raise StorageMigrationError(
                "nested_mount_unsupported",
                f"迁移路径包含嵌套挂载，拒绝修改其权限: {child_display}",
            )

        named = os.stat(
            child_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            _is_link_like_metadata(named)
            or not stat.S_ISDIR(named.st_mode)
            or not os.path.samestat(before, named)
        ):
            raise StorageMigrationError(
                "migration_path_changed",
                f"迁移目录在权限恢复前被替换: {child_display}",
            )
        original_mode = stat.S_IMODE(named.st_mode)
        updated_mode = original_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        os.chmod(
            child_name,
            updated_mode,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        child_fd = -1
        try:
            child_fd = os.open(child_name, directory_flags, dir_fd=directory_fd)
            if not os.path.samestat(before, os.fstat(child_fd)):
                os.close(child_fd)
                child_fd = -1
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移目录在权限恢复期间被替换: {child_display}",
                )
        except BaseException:
            if child_fd >= 0:
                os.close(child_fd)
            current = os.stat(
                child_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if os.path.samestat(before, current):
                os.chmod(
                    child_name,
                    original_mode,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            raise
        modified_modes.append(
            _DirectoryModeRestoreEntry(relative_parts, before, original_mode)
        )
        return child_fd

    def _inspect(
        directory_fd: int,
        display_path: Path,
        relative_parts: tuple[str, ...],
    ) -> None:
        _ensure_opened_entry_on_mount(
            directory_fd,
            tree_mount_identity,
            display_path,
        )
        if make_traversable:
            _make_traversable(directory_fd, relative_parts)
        with os.scandir(directory_fd) as children:
            child_names = sorted(child.name for child in children)
        for child_name in child_names:
            child_display = display_path / child_name
            before = os.stat(
                child_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if _is_link_like_metadata(before):
                continue
            if stat.S_ISDIR(before.st_mode):
                child_fd = _open_child_directory(
                    directory_fd,
                    child_name,
                    before,
                    child_display,
                    relative_parts + (child_name,),
                )
                try:
                    if not os.path.samestat(before, os.fstat(child_fd)):
                        raise StorageMigrationError(
                            "migration_path_changed",
                            f"迁移目录在安全检查期间被替换: {child_display}",
                        )
                    _inspect(
                        child_fd,
                        child_display,
                        relative_parts + (child_name,),
                    )
                finally:
                    os.close(child_fd)

    try:
        _inspect(root_fd, path, ())
        if expected_root_identity is not None:
            try:
                named_root = path.lstat()
            except OSError as exc:
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移目录在安全检查期间被替换: {path}",
                ) from exc
            if not os.path.samestat(expected_root_identity, named_root):
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移目录在安全检查期间被替换: {path}",
                )
        if retained_mode_plan is not None and modified_modes:
            retained_root_fd = os.dup(root_fd)
    except BaseException as exc:
        if retained_root_fd >= 0:
            os.close(retained_root_fd)
        try:
            _restore_recorded_directory_modes(root_fd, modified_modes, path)
        except StorageMigrationError as restore_exc:
            raise restore_exc from exc
        raise
    else:
        if retained_mode_plan is None:
            _restore_recorded_directory_modes(root_fd, modified_modes, path)
        else:
            retained_mode_plan.root_fd = retained_root_fd
            retained_mode_plan.entries.extend(modified_modes)
        return tree_mount_identity


def _preflight_directory_tree_mounts(
    path: Path,
    *,
    expected_mount_identity: tuple[str, int] | None = None,
    expected_root_identity: os.stat_result | None = None,
    make_traversable: bool = False,
    retained_mode_plan: _DirectoryModeRestorePlan | None = None,
) -> tuple[str, int] | None:
    """Inspect a complete tree without following links or crossing mounts."""

    if os.name == "nt":
        # Windows volume mount points and directory junctions are reparse
        # points. The existing link-like checks reject them without relying on
        # POSIX directory descriptors, which Python cannot portably open there.
        return None

    root_fd = _open_verified_directory(path)
    try:
        return _preflight_opened_directory_tree_mounts(
            path,
            root_fd=root_fd,
            expected_mount_identity=expected_mount_identity,
            expected_root_identity=expected_root_identity,
            make_traversable=make_traversable,
            retained_mode_plan=retained_mode_plan,
        )
    finally:
        os.close(root_fd)


def _close_mode_restore_plan(plan: _DirectoryModeRestorePlan) -> None:
    plan.entries.clear()
    if plan.root_fd >= 0:
        os.close(plan.root_fd)
        plan.root_fd = -1


def _restore_directory_modes(plan: _DirectoryModeRestorePlan, path: Path) -> None:
    try:
        if plan.root_fd >= 0:
            _restore_recorded_directory_modes(plan.root_fd, plan.entries, path)
    finally:
        _close_mode_restore_plan(plan)


def _remove_posix_directory_children(
    directory_fd: int,
    *,
    mount_identity: tuple[str, int],
    display_path: Path,
    preserve_names: frozenset[str] = frozenset(),
) -> None:
    """Delete children through one pinned directory without crossing mounts."""

    _ensure_opened_entry_on_mount(directory_fd, mount_identity, display_path)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    with os.scandir(directory_fd) as children:
        child_names = sorted(child.name for child in children)
    for child_name in child_names:
        if child_name in preserve_names:
            continue
        child_display = display_path / child_name
        before = os.stat(
            child_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(before.st_mode) and not _is_link_like_metadata(before):
            child_fd = os.open(child_name, directory_flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                if not os.path.samestat(before, opened):
                    raise StorageMigrationError(
                        "migration_path_changed",
                        f"迁移事务目录在清理期间被替换: {child_display}",
                    )
                _ensure_opened_entry_on_mount(
                    child_fd,
                    mount_identity,
                    child_display,
                )
                _remove_posix_directory_children(
                    child_fd,
                    mount_identity=mount_identity,
                    display_path=child_display,
                )
                named = os.stat(
                    child_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if not os.path.samestat(opened, named):
                    raise StorageMigrationError(
                        "migration_path_changed",
                        f"迁移事务目录在清理期间被替换: {child_display}",
                    )
            finally:
                os.close(child_fd)
            os.rmdir(child_name, dir_fd=directory_fd)
        else:
            os.unlink(child_name, dir_fd=directory_fd)


def _remove_posix_directory_tree(path: Path) -> None:
    """Remove one owned tree through pinned handles without crossing mounts."""

    root_identity = path.lstat()
    _preflight_named_mounts_below(path)
    mount_identity = _runtime_root_mount_identity(path.parent)
    if mount_identity is None:
        raise StorageMigrationError(
            "mount_identity_unavailable",
            f"无法确认迁移事务目录所在的挂载边界: {path}",
        )
    # Complete preflight precedes the first unlink/rmdir. This both preserves
    # all transaction evidence when a mount already exists and repairs only
    # directories already proven to remain on the transaction mount.
    mode_restore_plan = _DirectoryModeRestorePlan()
    _preflight_directory_tree_mounts(
        path,
        expected_mount_identity=mount_identity,
        expected_root_identity=root_identity,
        make_traversable=True,
        retained_mode_plan=mode_restore_plan,
    )
    try:
        root_fd = _open_verified_directory(path)
        try:
            if not os.path.samestat(root_identity, os.fstat(root_fd)):
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移事务目录在清理期间被替换: {path}",
                )
            _remove_posix_directory_children(
                root_fd,
                mount_identity=mount_identity,
                display_path=path,
            )
            if not os.path.samestat(root_identity, path.lstat()):
                raise StorageMigrationError(
                    "migration_path_changed",
                    f"迁移事务目录在清理期间被替换: {path}",
                )
        finally:
            os.close(root_fd)
        path.rmdir()
    except BaseException as exc:
        try:
            _restore_directory_modes(mode_restore_plan, path)
        except StorageMigrationError as restore_exc:
            raise restore_exc from exc
        raise
    else:
        _close_mode_restore_plan(mode_restore_plan)


def _remove_existing_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        if os.name != "nt":
            _remove_posix_directory_tree(path)
            if path.exists() or path.is_symlink():
                raise OSError(f"storage path cleanup did not remove {path}")
            fsync_directory_best_effort(path.parent)
            return
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


def _fsync_opened_migration_directory(fd: int, path: Path) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        raise StorageMigrationError(
            "target_flush_failed",
            f"迁移数据无法可靠写入目标磁盘: {path}: {exc}",
        ) from exc


def _rename_entry_without_replacing_at(
    source_parent_fd: int,
    source_name: str,
    target_parent_fd: int,
    target_name: str,
    *,
    target_display: Path,
) -> None:
    """POSIX no-replace rename rooted at already verified parent descriptors."""

    if os.name == "nt":
        raise OSError(errno.ENOTSUP, "descriptor-relative rename is unavailable")
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source_name)
    target_bytes = os.fsencode(target_name)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_parent_fd,
            source_bytes,
            target_parent_fd,
            target_bytes,
            1,  # RENAME_NOREPLACE
        )
    elif sys.platform == "darwin":
        renameatx_np = getattr(library, "renameatx_np", None)
        if renameatx_np is None:
            raise OSError(errno.ENOTSUP, "atomic exclusive rename unavailable")
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_parent_fd,
            source_bytes,
            target_parent_fd,
            target_bytes,
            0x00000004,  # RENAME_EXCL
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename unavailable")
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(
            error_number,
            os.strerror(error_number),
            os.fspath(target_display),
        )


def _durable_publish_without_replacing_at(
    source_parent_fd: int,
    source_name: str,
    source_parent_display: Path,
    target_parent_fd: int,
    target_name: str,
    target_parent_display: Path,
) -> None:
    _rename_entry_without_replacing_at(
        source_parent_fd,
        source_name,
        target_parent_fd,
        target_name,
        target_display=target_parent_display / target_name,
    )
    _fsync_opened_migration_directory(target_parent_fd, target_parent_display)
    if source_parent_fd != target_parent_fd:
        _fsync_opened_migration_directory(source_parent_fd, source_parent_display)


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
    try:
        current_identity = path.lstat()
    except OSError:
        return False
    if (
        _is_link_like_metadata(current_identity)
        or not stat.S_ISDIR(current_identity.st_mode)
        or not os.path.samestat(expected_identity, current_identity)
    ):
        return False
    mode_restore_plan = _DirectoryModeRestorePlan()
    if os.name != "nt":
        _preflight_named_mounts_below(path)
        parent_mount_identity = _runtime_root_mount_identity(path.parent)
        if parent_mount_identity is None:
            return False
        _preflight_directory_tree_mounts(
            path,
            expected_mount_identity=parent_mount_identity,
            expected_root_identity=expected_identity,
            make_traversable=True,
            retained_mode_plan=mode_restore_plan,
        )
    try:
        if not os.path.samestat(expected_identity, path.lstat()):
            _restore_directory_modes(mode_restore_plan, path)
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
            removed = False
        elif callable(remove_quarantine):
            removed = bool(remove_quarantine(quarantine))
        else:
            _remove_existing_path(quarantine)
            removed = True
    except BaseException as exc:
        try:
            _restore_directory_modes(mode_restore_plan, path)
        except StorageMigrationError as restore_exc:
            raise restore_exc from exc
        raise
    if removed:
        _close_mode_restore_plan(mode_restore_plan)
    else:
        _restore_directory_modes(mode_restore_plan, path)
    return removed


def _copy_posix_directory_tree_durably(
    source_path: Path,
    target_path: Path,
    *,
    expected_mount_identity: tuple[str, int],
    target_parent_fd: int | None = None,
    target_name: str | None = None,
    expected_target_mount_identity: tuple[str, int] | None = None,
) -> None:
    """Copy one directory through pinned source descriptors without crossing mounts."""

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    target_mount_identity: tuple[str, int] | None = None
    try:
        source_root_fd = _open_verified_directory(source_path)
    except StorageMigrationError:
        raise
    except OSError as exc:
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移源目录在复制前发生变化: {source_path}: {exc}",
        ) from exc

    def _copy_directory(
        source_fd: int,
        source_display: Path,
        target_parent_fd: int,
        target_name: str,
        target_display: Path,
    ) -> None:
        _ensure_opened_entry_on_mount(
            source_fd,
            expected_mount_identity,
            source_display,
        )
        assert target_mount_identity is not None
        target_fd = _open_or_create_posix_child_directory(
            target_parent_fd,
            target_name,
            target_display,
            expected_mount_identity=target_mount_identity,
            allow_existing=False,
        )
        try:
            opened_target = os.fstat(target_fd)
            with os.scandir(source_fd) as children:
                child_names = sorted(child.name for child in children)
            for child_name in child_names:
                child_display = source_display / child_name
                target_child = target_display / child_name
                try:
                    named_before = os.stat(
                        child_name,
                        dir_fd=source_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        f"迁移源条目在复制前已发生变化: {child_display}: {exc}",
                    ) from exc
                if _is_link_like_metadata(named_before):
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"迁移源条目包含符号链接或重解析点: {child_display}",
                    )
                if stat.S_ISDIR(named_before.st_mode):
                    child_fd = -1
                    try:
                        try:
                            child_fd = os.open(
                                child_name,
                                directory_flags,
                                dir_fd=source_fd,
                            )
                        except OSError as exc:
                            error_code = (
                                "path_symlink_unsupported"
                                if exc.errno == errno.ELOOP
                                else "source_changed_during_migration"
                            )
                            raise StorageMigrationError(
                                error_code,
                                f"迁移源目录无法安全打开: {child_display}: {exc}",
                            ) from exc
                        opened_child = os.fstat(child_fd)
                        named_after_open = os.stat(
                            child_name,
                            dir_fd=source_fd,
                            follow_symlinks=False,
                        )
                        if (
                            _is_link_like_metadata(opened_child)
                            or _is_link_like_metadata(named_after_open)
                            or not stat.S_ISDIR(opened_child.st_mode)
                            or not stat.S_ISDIR(named_after_open.st_mode)
                            or not os.path.samestat(named_before, opened_child)
                            or not os.path.samestat(opened_child, named_after_open)
                        ):
                            raise StorageMigrationError(
                                "source_changed_during_migration",
                                f"迁移源目录在打开期间被替换: {child_display}",
                            )
                        _ensure_opened_entry_on_mount(
                            child_fd,
                            expected_mount_identity,
                            child_display,
                        )
                        _copy_directory(
                            child_fd,
                            child_display,
                            target_fd,
                            child_name,
                            target_child,
                        )
                        try:
                            named_after_copy = os.stat(
                                child_name,
                                dir_fd=source_fd,
                                follow_symlinks=False,
                            )
                        except OSError as exc:
                            raise StorageMigrationError(
                                "source_changed_during_migration",
                                f"迁移源目录在复制期间发生变化: {child_display}: {exc}",
                            ) from exc
                        if not os.path.samestat(opened_child, named_after_copy):
                            raise StorageMigrationError(
                                "source_changed_during_migration",
                                f"迁移源目录在复制期间被替换: {child_display}",
                            )
                    finally:
                        if child_fd >= 0:
                            os.close(child_fd)
                    continue
                if stat.S_ISREG(named_before.st_mode):
                    _copy_staged_file_durably(
                        child_display,
                        target_child,
                        source_parent_fd=source_fd,
                        source_name=child_name,
                        target_parent_fd=target_fd,
                        target_name=child_name,
                        expected_mount_identity=expected_mount_identity,
                        expected_target_mount_identity=target_mount_identity,
                    )
                    continue
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"迁移源条目包含不支持的文件类型: {child_display}",
                )

            try:
                _copy_open_file_metadata(
                    source_fd,
                    target_fd,
                    os.fstat(source_fd),
                )
            except OSError as exc:
                raise StorageMigrationError(
                    "target_flush_failed",
                    f"迁移目录元数据无法可靠写入目标磁盘: {target_display}: {exc}",
                ) from exc
            named_target_after_copy = os.stat(
                target_name,
                dir_fd=target_parent_fd,
                follow_symlinks=False,
            )
            if (
                _is_link_like_metadata(named_target_after_copy)
                or not stat.S_ISDIR(named_target_after_copy.st_mode)
                or not os.path.samestat(opened_target, named_target_after_copy)
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存目录在复制期间被替换: {target_display}",
                )
            _ensure_opened_directory_still_named(
                target_display,
                target_fd,
                error_code="staging_entry_changed",
                message=f"迁移暂存目录在复制期间被替换: {target_display}",
            )
        except StorageMigrationError:
            raise
        except OSError as exc:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存目录在复制期间发生变化: {target_display}: {exc}",
            ) from exc
        finally:
            os.close(target_fd)

    owned_target_parent_fd = -1
    try:
        _ensure_opened_entry_on_mount(
            source_root_fd,
            expected_mount_identity,
            source_path,
        )
        if target_parent_fd is None:
            owned_target_parent_fd = _open_verified_directory(target_path.parent)
            root_target_name = target_path.name
        else:
            owned_target_parent_fd = os.dup(target_parent_fd)
            root_target_name = target_name or target_path.name
        target_mount_identity = (
            expected_target_mount_identity
            or _opened_mount_identity(owned_target_parent_fd)
        )
        _copy_directory(
            source_root_fd,
            source_path,
            owned_target_parent_fd,
            root_target_name,
            target_path,
        )
        _ensure_opened_directory_still_named(
            target_path.parent,
            owned_target_parent_fd,
            error_code="staging_entry_changed",
            message=f"迁移暂存父目录在复制期间被替换: {target_path.parent}",
        )
        try:
            named_source_after = source_path.lstat()
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录在复制期间发生变化: {source_path}: {exc}",
            ) from exc
        if not os.path.samestat(os.fstat(source_root_fd), named_source_after):
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录在复制期间被替换: {source_path}",
            )
    finally:
        if owned_target_parent_fd >= 0:
            os.close(owned_target_parent_fd)
        os.close(source_root_fd)


def _copy_windows_directory_tree_durably(
    source_path: Path,
    target_path: Path,
    source_identity: os.stat_result,
) -> None:
    """Copy one directory tree while every created target directory is pinned."""

    if os.name != "nt":
        raise OSError(errno.ENOTSUP, "Windows directory guards are unavailable")
    owned_guards: list[int] = []

    def _guard_created_directory(path: Path) -> None:
        identity = path.lstat()
        handle = _open_windows_directory_rename_guard(path, identity)
        try:
            owned_guards.append(handle)
        except BaseException:
            _close_windows_directory_rename_guard(handle)
            raise

    def _copy_directory(
        source_directory: Path,
        target_directory: Path,
        expected_source_identity: os.stat_result,
    ) -> None:
        try:
            named_before = source_directory.lstat()
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录在复制前发生变化: {source_directory}: {exc}",
            ) from exc
        if (
            _is_link_like_metadata(named_before)
            or not stat.S_ISDIR(named_before.st_mode)
            or not os.path.samestat(expected_source_identity, named_before)
        ):
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录在复制前被替换: {source_directory}",
            )

        try:
            with os.scandir(source_directory) as scanned:
                names = sorted(entry.name for entry in scanned)
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录无法安全枚举: {source_directory}: {exc}",
            ) from exc

        children: list[tuple[str, os.stat_result]] = []
        for name in names:
            child = source_directory / name
            try:
                child_identity = child.lstat()
            except OSError as exc:
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    f"迁移源条目在枚举期间发生变化: {child}: {exc}",
                ) from exc
            if _is_link_like_metadata(child_identity):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移源条目包含符号链接或重解析点: {child}",
                )
            if not (
                stat.S_ISDIR(child_identity.st_mode)
                or stat.S_ISREG(child_identity.st_mode)
            ):
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"迁移源条目包含不支持的文件类型: {child}",
                )
            children.append((name, child_identity))

        for name, child_identity in children:
            source_child = source_directory / name
            target_child = target_directory / name
            if stat.S_ISDIR(child_identity.st_mode):
                try:
                    target_child.mkdir()
                except FileExistsError as exc:
                    raise StorageMigrationError(
                        "staging_entry_exists",
                        f"迁移暂存条目已存在，无法安全覆盖: {target_child}",
                    ) from exc
                _guard_created_directory(target_child)
                _copy_directory(source_child, target_child, child_identity)
            else:
                _copy_staged_file_durably(source_child, target_child)

        try:
            with os.scandir(source_directory) as final_scanned:
                final_names = sorted(entry.name for entry in final_scanned)
            named_after = source_directory.lstat()
        except OSError as exc:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录在复制期间发生变化: {source_directory}: {exc}",
            ) from exc
        if (
            final_names != names
            or _is_link_like_metadata(named_after)
            or not stat.S_ISDIR(named_after.st_mode)
            or not os.path.samestat(named_before, named_after)
        ):
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"迁移源目录在复制期间被替换或修改: {source_directory}",
            )
        try:
            shutil.copystat(source_directory, target_directory, follow_symlinks=False)
        except OSError as exc:
            raise StorageMigrationError(
                "target_flush_failed",
                f"迁移目录元数据无法可靠写入目标磁盘: {target_directory}: {exc}",
            ) from exc

    try:
        target_path.mkdir()
        _guard_created_directory(target_path)
        _copy_directory(source_path, target_path, source_identity)
    finally:
        while owned_guards:
            _close_windows_directory_rename_guard(owned_guards.pop())


def _copy_runtime_entry(
    source_path: Path,
    target_path: Path,
    *,
    expected_mount_identity: tuple[str, int] | None = None,
    target_parent_fd: int | None = None,
    target_name: str | None = None,
    expected_target_mount_identity: tuple[str, int] | None = None,
) -> None:
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
    copy_mount_identity = expected_mount_identity
    if os.name != "nt" and copy_mount_identity is None:
        # Direct callers still bind an entry to its containing storage mount;
        # production passes the identity captured before the size gate.
        copy_mount_identity = _runtime_root_mount_identity(source_path.parent)
        if copy_mount_identity is None:
            raise StorageMigrationError(
                "mount_identity_unavailable",
                f"无法确认迁移源目录的挂载边界: {source_path}",
            )

    # Re-scan immediately before copy so existing links, reparse points, and
    # mount crossings are refused before creating staged content.
    _snapshot_path(
        source_path,
        expected_mount_identity=copy_mount_identity,
    )
    if path_chain_has_symlink(target_path):
        raise StorageMigrationError(
            "target_symlink_unsupported",
            "迁移暂存目录包含符号链接或重解析点，已停止迁移。",
        )
    if os.name == "nt" or target_parent_fd is None:
        # Windows lacks portable directory-relative creation. POSIX production
        # passes a parent descriptor rooted at the transaction staging inode;
        # direct helper callers retain the legacy path setup before the helper
        # pins and verifies that parent.
        target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        source_metadata = source_path.lstat()
    except OSError as exc:
        raise StorageMigrationError(
            "source_changed_during_migration",
            f"迁移源条目在复制前发生变化: {source_path}: {exc}",
        ) from exc
    if _is_link_like_metadata(source_metadata):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移源条目包含符号链接或重解析点: {source_path}",
        )
    if stat.S_ISDIR(source_metadata.st_mode):
        if os.name != "nt":
            assert copy_mount_identity is not None
            _copy_posix_directory_tree_durably(
                source_path,
                target_path,
                expected_mount_identity=copy_mount_identity,
                target_parent_fd=target_parent_fd,
                target_name=target_name,
                expected_target_mount_identity=expected_target_mount_identity,
            )
            return
        _copy_windows_directory_tree_durably(
            source_path,
            target_path,
            source_metadata,
        )
        return

    if stat.S_ISREG(source_metadata.st_mode):
        _copy_staged_file_durably(
            source_path,
            target_path,
            target_parent_fd=target_parent_fd,
            target_name=target_name,
            expected_mount_identity=copy_mount_identity,
            expected_target_mount_identity=expected_target_mount_identity,
        )
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
        # Staged entries must remain movable and removable until commit and
        # rollback are both impossible. Copy harmless BSD flags, but never
        # make private staging data immutable, append-only, or unlink-proof;
        # those flags would make publication and recovery permanently fail.
        blocking_flags = 0
        for flag_name in (
            "UF_IMMUTABLE",
            "SF_IMMUTABLE",
            "UF_APPEND",
            "SF_APPEND",
            "UF_NOUNLINK",
            "SF_NOUNLINK",
        ):
            blocking_flags |= int(getattr(stat, flag_name, 0) or 0)
        fchflags(target_fd, int(source_metadata.st_flags) & ~blocking_flags)


def _copy_staged_file_durably(
    source_path: Path | str,
    target_path: Path | str,
    *,
    source_parent_fd: int | None = None,
    source_name: str | None = None,
    target_parent_fd: int | None = None,
    target_name: str | None = None,
    expected_mount_identity: tuple[str, int] | None = None,
    expected_target_mount_identity: tuple[str, int] | None = None,
) -> str:
    """Copy one private staged file and flush data before restoring source metadata."""

    source = Path(source_path)
    target = Path(target_path)
    if os.name != "nt" and target_parent_fd is None:
        pinned_target_parent_fd = _open_verified_directory(target.parent)
        try:
            pinned_target_mount_identity = _opened_mount_identity(
                pinned_target_parent_fd
            )
            result = _copy_staged_file_durably(
                source,
                target,
                source_parent_fd=source_parent_fd,
                source_name=source_name,
                target_parent_fd=pinned_target_parent_fd,
                target_name=target.name,
                expected_mount_identity=expected_mount_identity,
                expected_target_mount_identity=pinned_target_mount_identity,
            )
            _ensure_opened_directory_still_named(
                target.parent,
                pinned_target_parent_fd,
                error_code="staging_entry_changed",
                message=f"迁移暂存父目录在复制期间被替换: {target.parent}",
            )
            return result
        finally:
            os.close(pinned_target_parent_fd)
    if target_parent_fd is not None and not target_name:
        raise StorageMigrationError(
            "staging_entry_changed",
            f"迁移暂存文件缺少目录内名称: {target}",
        )
    if target_parent_fd is not None and expected_target_mount_identity is None:
        expected_target_mount_identity = _opened_mount_identity(target_parent_fd)
    try:
        if source_parent_fd is None:
            named_source_before = source.lstat()
        else:
            if not source_name:
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    f"迁移源文件缺少目录内名称: {source}",
                )
            named_source_before = os.stat(
                source_name,
                dir_fd=source_parent_fd,
                follow_symlinks=False,
            )
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
            source_fd = os.open(
                os.fspath(source) if source_parent_fd is None else source_name,
                source_flags,
                dir_fd=source_parent_fd,
            )
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
            if source_parent_fd is None:
                named_source_after = source.lstat()
            else:
                named_source_after = os.stat(
                    source_name,
                    dir_fd=source_parent_fd,
                    follow_symlinks=False,
                )
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
        if expected_mount_identity is not None and os.name != "nt":
            _ensure_opened_entry_on_mount(
                source_fd,
                expected_mount_identity,
                source,
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
            if target_parent_fd is None:
                target_fd = os.open(os.fspath(target), target_flags, 0o600)
            else:
                target_fd = os.open(
                    target_name,
                    target_flags,
                    0o600,
                    dir_fd=target_parent_fd,
                )
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
        if expected_target_mount_identity is not None and os.name != "nt":
            _ensure_opened_entry_on_mount(
                target_fd,
                expected_target_mount_identity,
                target,
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
            if target_parent_fd is None:
                named_target_after = target.lstat()
            else:
                named_target_after = os.stat(
                    target_name,
                    dir_fd=target_parent_fd,
                    follow_symlinks=False,
                )
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


def _capture_opened_posix_tree_identity_manifest(
    root_fd: int,
    root_path: Path,
    expected_mount_identity: tuple[str, int],
) -> dict[str, tuple[str, int, int, int, int, int, int]]:
    """Capture stable staged-entry identities below one pinned POSIX directory."""

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    manifest: dict[str, tuple[str, int, int, int, int, int, int]] = {}

    def _stable_fields(metadata: os.stat_result) -> tuple[int, int, int, int]:
        return (
            stat.S_IMODE(metadata.st_mode),
            int(metadata.st_size),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
        )

    def _walk(directory_fd: int, relative_root: Path, display_root: Path) -> None:
        _ensure_opened_entry_on_mount(
            directory_fd,
            expected_mount_identity,
            display_root,
        )
        with os.scandir(directory_fd) as entries:
            child_names = sorted(entry.name for entry in entries)
        for child_name in child_names:
            child_path = display_root / child_name
            relative_path = (relative_root / child_name).as_posix()
            named_before = os.stat(
                child_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if _is_link_like_metadata(named_before):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移暂存区包含符号链接: {child_path}",
                )
            if stat.S_ISDIR(named_before.st_mode):
                child_fd = -1
                try:
                    child_fd = os.open(
                        child_name,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                    opened = os.fstat(child_fd)
                    named_after_open = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        _is_link_like_metadata(opened)
                        or _is_link_like_metadata(named_after_open)
                        or not stat.S_ISDIR(opened.st_mode)
                        or not stat.S_ISDIR(named_after_open.st_mode)
                        or not os.path.samestat(named_before, opened)
                        or not os.path.samestat(opened, named_after_open)
                        or _stable_fields(named_before) != _stable_fields(opened)
                        or _stable_fields(opened) != _stable_fields(named_after_open)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存目录在清单核验期间被替换或修改: {child_path}",
                        )
                    _ensure_opened_entry_on_mount(
                        child_fd,
                        expected_mount_identity,
                        child_path,
                    )
                    manifest[relative_path] = (
                        "dir",
                        int(opened.st_dev),
                        int(opened.st_ino),
                        *_stable_fields(opened),
                    )
                    _walk(child_fd, relative_root / child_name, child_path)
                    opened_after = os.fstat(child_fd)
                    named_after_walk = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        not os.path.samestat(opened, opened_after)
                        or not os.path.samestat(opened_after, named_after_walk)
                        or _stable_fields(opened) != _stable_fields(opened_after)
                        or _stable_fields(opened_after)
                        != _stable_fields(named_after_walk)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存目录在清单核验期间被替换或修改: {child_path}",
                        )
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
                continue
            if stat.S_ISREG(named_before.st_mode):
                child_fd = -1
                try:
                    child_fd = os.open(
                        child_name,
                        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    opened = os.fstat(child_fd)
                    named_after_open = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or not stat.S_ISREG(named_after_open.st_mode)
                        or not os.path.samestat(named_before, opened)
                        or not os.path.samestat(opened, named_after_open)
                        or _stable_fields(named_before) != _stable_fields(opened)
                        or _stable_fields(opened) != _stable_fields(named_after_open)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存文件在清单核验期间被替换或修改: {child_path}",
                        )
                    _ensure_opened_entry_on_mount(
                        child_fd,
                        expected_mount_identity,
                        child_path,
                    )
                    manifest[relative_path] = (
                        "file",
                        int(opened.st_dev),
                        int(opened.st_ino),
                        *_stable_fields(opened),
                    )
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
                continue
            raise StorageMigrationError(
                "path_type_unsupported",
                f"迁移暂存区包含不支持的文件类型: {child_path}",
            )

        with os.scandir(directory_fd) as final_entries:
            final_names = sorted(entry.name for entry in final_entries)
        if final_names != child_names:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存目录在清单核验期间发生变化: {display_root}",
            )

    _walk(root_fd, Path(), root_path)
    return manifest


def _rewrite_migrated_runtime_config_paths(
    *,
    source_root: Path,
    content_root: Path,
    target_root: Path,
    content_root_fd: int | None = None,
    expected_target_mount_identity: tuple[str, int] | None = None,
) -> dict[str, int | str] | None:
    workshop_config_path = content_root / "config" / "workshop_config.json"
    if os.name != "nt" and content_root_fd is not None:
        if expected_target_mount_identity is None:
            expected_target_mount_identity = _opened_mount_identity(content_root_fd)
        try:
            os.stat(
                "config",
                dir_fd=content_root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        config_fd = _open_or_create_posix_child_directory(
            content_root_fd,
            "config",
            content_root / "config",
            expected_mount_identity=expected_target_mount_identity,
            allow_existing=True,
            create_missing=False,
        )
        source_fd = -1
        temp_fd = -1
        temp_name = f".neko-storage-rewrite-{secrets.token_hex(16)}.tmp"
        temp_created = False
        config_read_completed = False
        try:
            config_manifest_before = _capture_opened_posix_tree_identity_manifest(
                config_fd,
                content_root / "config",
                expected_target_mount_identity,
            )
            try:
                named_before = os.stat(
                    "workshop_config.json",
                    dir_fd=config_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            if _is_link_like_metadata(named_before) or not stat.S_ISREG(
                named_before.st_mode
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置不是普通文件: {workshop_config_path}",
                )
            read_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            source_fd = os.open(
                "workshop_config.json",
                read_flags,
                dir_fd=config_fd,
            )
            opened_before = os.fstat(source_fd)
            named_after_open = os.stat(
                "workshop_config.json",
                dir_fd=config_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(opened_before.st_mode)
                or not stat.S_ISREG(named_after_open.st_mode)
                or not os.path.samestat(named_before, opened_before)
                or not os.path.samestat(opened_before, named_after_open)
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置在打开期间被替换: {workshop_config_path}",
                )
            _ensure_opened_entry_on_mount(
                source_fd,
                expected_target_mount_identity,
                workshop_config_path,
            )
            if opened_before.st_size > _WORKSHOP_CONFIG_REWRITE_MAX_BYTES:
                raise StorageMigrationError(
                    "workshop_config_too_large",
                    "工坊配置超过迁移重写的安全大小上限，已保留原数据并停止迁移。",
                )
            chunks: list[bytes] = []
            total_bytes = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _WORKSHOP_CONFIG_REWRITE_MAX_BYTES:
                    raise StorageMigrationError(
                        "workshop_config_too_large",
                        "工坊配置在迁移读取期间超过安全大小上限，已保留原数据并停止迁移。",
                    )
                chunks.append(chunk)
            opened_after = os.fstat(source_fd)
            named_after_read = os.stat(
                "workshop_config.json",
                dir_fd=config_fd,
                follow_symlinks=False,
            )
            stable_fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
            if (
                not os.path.samestat(opened_before, opened_after)
                or not os.path.samestat(opened_after, named_after_read)
                or any(
                    getattr(opened_before, field) != getattr(opened_after, field)
                    for field in stable_fields
                )
                or any(
                    getattr(opened_after, field) != getattr(named_after_read, field)
                    for field in stable_fields
                )
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置在读取期间被替换或修改: {workshop_config_path}",
                )
            payload = json.loads(b"".join(chunks).decode("utf-8"))
            config_read_completed = True
            rewritten_payload = rebase_runtime_bound_workshop_config_paths(
                payload,
                source_root=source_root,
                target_root=target_root,
            )
            if rewritten_payload is payload:
                return None

            encoded = json.dumps(
                rewritten_payload,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=config_fd,
            )
            temp_created = True
            created_temp = os.fstat(temp_fd)
            _ensure_opened_entry_on_mount(
                temp_fd,
                expected_target_mount_identity,
                workshop_config_path.parent / temp_name,
            )
            view = memoryview(encoded)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("short write while rewriting migrated workshop config")
                view = view[written:]
            os.fsync(temp_fd)
            named_before_replace = os.stat(
                "workshop_config.json",
                dir_fd=config_fd,
                follow_symlinks=False,
            )
            if not os.path.samestat(opened_after, named_before_replace):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置在重写前被替换: {workshop_config_path}",
                )
            os.replace(
                temp_name,
                "workshop_config.json",
                src_dir_fd=config_fd,
                dst_dir_fd=config_fd,
            )
            temp_created = False
            named_after_replace = os.stat(
                "workshop_config.json",
                dir_fd=config_fd,
                follow_symlinks=False,
            )
            if (
                _is_link_like_metadata(named_after_replace)
                or not stat.S_ISREG(named_after_replace.st_mode)
                or not os.path.samestat(created_temp, named_after_replace)
                or not os.path.samestat(os.fstat(temp_fd), named_after_replace)
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置在原子重写期间被替换: {workshop_config_path}",
                )
            os.fsync(config_fd)
            named_after_directory_flush = os.stat(
                "workshop_config.json",
                dir_fd=config_fd,
                follow_symlinks=False,
            )
            if (
                _is_link_like_metadata(named_after_directory_flush)
                or not stat.S_ISREG(named_after_directory_flush.st_mode)
                or not os.path.samestat(created_temp, named_after_directory_flush)
                or not os.path.samestat(
                    os.fstat(temp_fd),
                    named_after_directory_flush,
                )
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置在目录持久化期间被替换: {workshop_config_path}",
                )
            config_manifest_after = _capture_opened_posix_tree_identity_manifest(
                config_fd,
                content_root / "config",
                expected_target_mount_identity,
            )
            config_manifest_before.pop("workshop_config.json", None)
            config_manifest_after.pop("workshop_config.json", None)
            if config_manifest_after != config_manifest_before:
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置目录在路径重写期间发生额外变化: {content_root / 'config'}",
                )
            opened_config = os.fstat(config_fd)
            named_config_before_snapshot = os.stat(
                "config",
                dir_fd=content_root_fd,
                follow_symlinks=False,
            )
            if not os.path.samestat(opened_config, named_config_before_snapshot):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置目录在生成清单前被替换: {content_root / 'config'}",
                )
            rewritten_snapshot = _snapshot_path(
                content_root / "config",
                expected_mount_identity=expected_target_mount_identity,
            )
            named_config_after_snapshot = os.stat(
                "config",
                dir_fd=content_root_fd,
                follow_symlinks=False,
            )
            final_config_manifest = _capture_opened_posix_tree_identity_manifest(
                config_fd,
                content_root / "config",
                expected_target_mount_identity,
            )
            final_config_manifest.pop("workshop_config.json", None)
            if (
                not os.path.samestat(opened_config, named_config_after_snapshot)
                or final_config_manifest != config_manifest_after
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置目录在生成清单期间发生变化: {content_root / 'config'}",
                )
            return rewritten_snapshot
        except StorageMigrationError:
            raise
        except Exception as exc:
            if not config_read_completed:
                logger.warning(
                    "Failed to read migrated workshop_config for path rewrite: %s",
                    exc,
                )
                return None
            raise StorageMigrationError(
                "target_flush_failed",
                f"迁移暂存配置无法可靠重写到目标磁盘: {workshop_config_path}: {exc}",
            ) from exc
        finally:
            if source_fd >= 0:
                with suppress(OSError):
                    os.close(source_fd)
            if temp_fd >= 0:
                with suppress(OSError):
                    os.close(temp_fd)
            if temp_created:
                with suppress(OSError):
                    os.unlink(temp_name, dir_fd=config_fd)
            os.close(config_fd)

    if not workshop_config_path.is_file():
        return None

    windows_config_guard = -1
    if os.name == "nt":
        try:
            config_directory = workshop_config_path.parent
            config_identity = config_directory.lstat()
            if (
                _is_link_like_metadata(config_identity)
                or not stat.S_ISDIR(config_identity.st_mode)
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置目录不是可固定的真实目录: {config_directory}",
                )
            windows_config_guard = _open_windows_directory_rename_guard(
                config_directory,
                config_identity,
            )
            return _rewrite_windows_workshop_config_paths(
                source_root=source_root,
                content_root=content_root,
                target_root=target_root,
                workshop_config_path=workshop_config_path,
            )
        finally:
            _close_windows_directory_rename_guard(windows_config_guard)

    try:
        payload = _read_json_from_verified_regular_file(
            workshop_config_path,
            max_bytes=_WORKSHOP_CONFIG_REWRITE_MAX_BYTES,
        )
    except StorageMigrationError as exc:
        _close_windows_directory_rename_guard(windows_config_guard)
        if exc.error_code == "workshop_config_too_large":
            raise
        logger.warning("Failed to read migrated workshop_config for path rewrite: %s", exc)
        return None
    except Exception as exc:
        _close_windows_directory_rename_guard(windows_config_guard)
        logger.warning("Failed to read migrated workshop_config for path rewrite: %s", exc)
        return None

    try:
        rewritten_payload = rebase_runtime_bound_workshop_config_paths(
            payload,
            source_root=source_root,
            target_root=target_root,
        )
        if rewritten_payload is payload:
            return None
        atomic_write_json(workshop_config_path, rewritten_payload, ensure_ascii=False, indent=2)
        return _snapshot_path(content_root / "config")
    finally:
        _close_windows_directory_rename_guard(windows_config_guard)


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


def _read_json_from_verified_regular_file(
    path: Path,
    *,
    max_bytes: int | None = None,
) -> Any:
    fd = -1
    try:
        fd, opened_before = _open_verified_regular_file(path)
        if max_bytes is not None and opened_before.st_size > max_bytes:
            raise StorageMigrationError(
                "workshop_config_too_large",
                "工坊配置超过迁移重写的安全大小上限，已保留原数据并停止迁移。",
            )
        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if max_bytes is not None and total_bytes > max_bytes:
                raise StorageMigrationError(
                    "workshop_config_too_large",
                    "工坊配置在迁移读取期间超过安全大小上限，已保留原数据并停止迁移。",
                )
            chunks.append(chunk)
        _verify_opened_regular_file(path, fd, opened_before)
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)


def _native_windows_path(path: Path) -> str:
    value = str(path)
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _windows_rewrite_stable_fields(metadata: os.stat_result) -> tuple[int, int, int]:
    return (
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _snapshot_windows_config_with_open_workshop(
    config_directory: Path,
    workshop_config_path: Path,
    workshop_fd: int,
) -> dict[str, int | str]:
    """Hash config while the rewritten workshop file remains write/delete guarded."""

    if path_chain_has_symlink(config_directory):
        raise StorageMigrationError(
            "path_symlink_unsupported",
            f"迁移校验不支持符号链接路径: {config_directory}",
        )
    tree_mount_identity = _preflight_directory_tree_mounts(config_directory)
    total_bytes = 0
    file_count = 0
    manifest_digest = hashlib.sha256()
    guarded_identity = os.fstat(workshop_fd)
    for current_root, dirnames, filenames in os.walk(config_directory):
        current_root_path = Path(current_root)
        _ensure_directory_path_on_mount(current_root_path, tree_mount_identity)
        dirnames.sort()
        filenames.sort()
        relative_root = current_root_path.relative_to(config_directory)
        for dirname in dirnames:
            current_dir = current_root_path / dirname
            if path_chain_has_symlink(current_dir):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移校验不支持符号链接: {current_dir}",
                )
            _ensure_directory_path_on_mount(current_dir, tree_mount_identity)
            manifest_digest.update(
                b"D\0" + os.fsencode((relative_root / dirname).as_posix()) + b"\0"
            )
        for filename in filenames:
            current_file = current_root_path / filename
            if path_chain_has_symlink(current_file):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移校验不支持符号链接: {current_file}",
                )
            if current_file == workshop_config_path:
                named_before = current_file.lstat()
                opened_before = os.fstat(workshop_fd)
                if (
                    not os.path.samestat(guarded_identity, opened_before)
                    or not os.path.samestat(opened_before, named_before)
                    or _windows_rewrite_stable_fields(guarded_identity)
                    != _windows_rewrite_stable_fields(opened_before)
                ):
                    raise StorageMigrationError(
                        "staging_entry_changed",
                        f"迁移暂存配置在生成清单前被替换或修改: {current_file}",
                    )
                os.lseek(workshop_fd, 0, os.SEEK_SET)
                digest = hashlib.sha256()
                file_bytes = 0
                while True:
                    chunk = os.read(workshop_fd, 1024 * 1024)
                    if not chunk:
                        break
                    file_bytes += len(chunk)
                    digest.update(chunk)
                opened_after = os.fstat(workshop_fd)
                named_after = current_file.lstat()
                if (
                    file_bytes != opened_after.st_size
                    or not os.path.samestat(opened_before, opened_after)
                    or not os.path.samestat(opened_after, named_after)
                    or _windows_rewrite_stable_fields(opened_before)
                    != _windows_rewrite_stable_fields(opened_after)
                ):
                    raise StorageMigrationError(
                        "staging_entry_changed",
                        f"迁移暂存配置在生成清单期间被替换或修改: {current_file}",
                    )
                file_digest = digest.hexdigest()
            else:
                if not current_file.is_file():
                    raise StorageMigrationError(
                        "path_type_unsupported",
                        f"迁移校验不支持该文件类型: {current_file}",
                    )
                file_bytes, file_digest, _allocated_bytes = _hash_file(
                    current_file,
                    expected_mount_identity=tree_mount_identity,
                )
            relative_file = (relative_root / filename).as_posix()
            manifest_digest.update(
                b"F\0"
                + os.fsencode(relative_file)
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


def _open_windows_rewrite_file(
    path: Path,
    *,
    create_new: bool,
) -> tuple[int, os.stat_result]:
    """Open one exact file while denying concurrent write, rename, and delete."""

    if os.name != "nt":
        raise OSError("Windows rewrite handles are unavailable on this platform")
    import msvcrt
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
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    generic_read = 0x80000000
    generic_write = 0x40000000
    delete_access = 0x00010000
    file_write_attributes = 0x00000100
    file_share_read = 0x00000001
    create_new_disposition = 1
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    invalid_handle_value = ctypes.c_void_p(-1).value
    desired_access = generic_read | delete_access | file_write_attributes
    os_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if create_new:
        desired_access |= generic_write
        os_flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    handle = kernel32.CreateFileW(
        _native_windows_path(path),
        desired_access,
        # Holding this handle freezes the bytes and the name we verified.
        file_share_read,
        None,
        create_new_disposition if create_new else open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        message = ctypes.FormatError(error).strip()
        if error in {80, 183}:
            raise FileExistsError(error, message, str(path))
        if error in {2, 3}:
            if not create_new:
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"迁移暂存配置在独占打开前消失: {path}",
                )
            raise FileNotFoundError(error, message, str(path))
        if error == 32 and not create_new:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置正被并发修改，无法固定: {path}",
            )
        raise OSError(error, message, str(path))
    try:
        fd = msvcrt.open_osfhandle(int(handle), os_flags)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    try:
        opened = os.fstat(fd)
        named = path.lstat()
        if (
            _is_link_like_metadata(opened)
            or _is_link_like_metadata(named)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or not os.path.samestat(opened, named)
        ):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置在独占打开期间被替换: {path}",
            )
        return fd, opened
    except BaseException:
        os.close(fd)
        raise


def _rename_windows_open_file(fd: int, target_path: Path) -> None:
    """Rename the exact open file to an absent absolute target name."""

    if os.name != "nt":
        raise OSError("Windows handle rename is unavailable on this platform")
    import msvcrt
    from ctypes import wintypes

    class _FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("flags", wintypes.DWORD),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    encoded_name = _native_windows_path(target_path).encode("utf-16-le")
    filename_offset = _FileRenameInfo.file_name.offset
    buffer_size = max(ctypes.sizeof(_FileRenameInfo), filename_offset + len(encoded_name))
    buffer = ctypes.create_string_buffer(buffer_size)
    header = _FileRenameInfo.from_buffer(buffer)
    header.flags = 0  # ReplaceIfExists = FALSE
    header.root_directory = None
    header.file_name_length = len(encoded_name)
    ctypes.memmove(ctypes.addressof(buffer) + filename_offset, encoded_name, len(encoded_name))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    if not kernel32.SetFileInformationByHandle(
        msvcrt.get_osfhandle(fd),
        3,  # FileRenameInfo
        buffer,
        buffer_size,
    ):
        error = ctypes.get_last_error()
        message = ctypes.FormatError(error).strip()
        if error in {80, 183}:
            raise FileExistsError(error, message, str(target_path))
        raise OSError(error, message, str(target_path))


def _delete_windows_open_file_on_close(fd: int) -> None:
    """Delete the exact verified file rather than re-resolving its private name."""

    import msvcrt
    from ctypes import wintypes

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("file_attributes", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(fd)
    basic_info = _FileBasicInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        0,  # FileBasicInfo
        ctypes.byref(basic_info),
        ctypes.sizeof(basic_info),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error).strip())
    file_attribute_readonly = 0x00000001
    file_attribute_normal = 0x00000080
    if basic_info.file_attributes & file_attribute_readonly:
        basic_info.file_attributes &= ~file_attribute_readonly
        if not basic_info.file_attributes:
            basic_info.file_attributes = file_attribute_normal
        if not kernel32.SetFileInformationByHandle(
            handle,
            0,  # FileBasicInfo
            ctypes.byref(basic_info),
            ctypes.sizeof(basic_info),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error).strip())
    disposition = _FileDispositionInfo(True)
    if not kernel32.SetFileInformationByHandle(
        handle,
        4,  # FileDispositionInfo
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error).strip())


def _rewrite_windows_workshop_config_paths(
    *,
    source_root: Path,
    content_root: Path,
    target_root: Path,
    workshop_config_path: Path,
) -> dict[str, int | str] | None:
    """CAS-rewrite the staged config without overwriting a concurrent winner."""

    source_fd = -1
    temp_fd = -1
    source_renamed = False
    temp_published = False
    config_read_completed = False
    temp_path = workshop_config_path.parent / f".neko-storage-rewrite-{uuid.uuid4().hex}.tmp"
    backup_path = workshop_config_path.parent / f".neko-storage-rewrite-{uuid.uuid4().hex}.old"
    try:
        try:
            source_fd, source_before = _open_windows_rewrite_file(
                workshop_config_path,
                create_new=False,
            )
        except StorageMigrationError:
            raise
        except OSError as exc:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置无法取得独占写保护: {workshop_config_path}: {exc}",
            ) from exc
        if source_before.st_size > _WORKSHOP_CONFIG_REWRITE_MAX_BYTES:
            raise StorageMigrationError(
                "workshop_config_too_large",
                "工坊配置超过迁移重写的安全大小上限，已保留原数据并停止迁移。",
            )
        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > _WORKSHOP_CONFIG_REWRITE_MAX_BYTES:
                raise StorageMigrationError(
                    "workshop_config_too_large",
                    "工坊配置在迁移读取期间超过安全大小上限，已保留原数据并停止迁移。",
                )
            chunks.append(chunk)
        source_after = os.fstat(source_fd)
        if (
            not os.path.samestat(source_before, source_after)
            or _windows_rewrite_stable_fields(source_before)
            != _windows_rewrite_stable_fields(source_after)
        ):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置在读取期间被修改: {workshop_config_path}",
            )
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        config_read_completed = True
        rewritten_payload = rebase_runtime_bound_workshop_config_paths(
            payload,
            source_root=source_root,
            target_root=target_root,
        )
        if rewritten_payload is payload:
            return None

        encoded = json.dumps(
            rewritten_payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        temp_fd, _temp_before = _open_windows_rewrite_file(temp_path, create_new=True)
        view = memoryview(encoded)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OSError("short write while rewriting migrated workshop config")
            view = view[written:]
        os.fsync(temp_fd)
        temp_identity = os.fstat(temp_fd)
        if not os.path.samestat(temp_identity, temp_path.lstat()):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置临时文件在发布前被替换: {temp_path}",
            )

        _rename_windows_open_file(source_fd, backup_path)
        source_renamed = True
        try:
            _rename_windows_open_file(temp_fd, workshop_config_path)
        except FileExistsError as exc:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置在原子重写窗口被并发替换: {workshop_config_path}",
            ) from exc
        temp_published = True
        if not os.path.samestat(temp_identity, workshop_config_path.lstat()):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置在原子重写期间被替换: {workshop_config_path}",
            )

        _delete_windows_open_file_on_close(source_fd)
        os.close(source_fd)
        source_fd = -1
        source_renamed = False

        rewritten_snapshot = _snapshot_windows_config_with_open_workshop(
            content_root / "config",
            workshop_config_path,
            temp_fd,
        )
        named_after_snapshot = workshop_config_path.lstat()
        opened_after_snapshot = os.fstat(temp_fd)
        if (
            not os.path.samestat(temp_identity, opened_after_snapshot)
            or not os.path.samestat(opened_after_snapshot, named_after_snapshot)
            or _windows_rewrite_stable_fields(temp_identity)
            != _windows_rewrite_stable_fields(opened_after_snapshot)
        ):
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存配置在生成清单期间被替换或修改: {workshop_config_path}",
            )
        return rewritten_snapshot
    except StorageMigrationError:
        raise
    except Exception as exc:
        if not config_read_completed:
            logger.warning(
                "Failed to read migrated workshop_config for path rewrite: %s",
                exc,
            )
            return None
        raise StorageMigrationError(
            "target_flush_failed",
            f"迁移暂存配置无法可靠重写到目标磁盘: {workshop_config_path}: {exc}",
        ) from exc
    finally:
        if temp_fd >= 0:
            if not temp_published:
                with suppress(OSError):
                    _delete_windows_open_file_on_close(temp_fd)
            with suppress(OSError):
                os.close(temp_fd)
        if source_fd >= 0:
            if source_renamed:
                public_name_exists = True
                try:
                    workshop_config_path.lstat()
                except FileNotFoundError:
                    public_name_exists = False
                    try:
                        _rename_windows_open_file(source_fd, workshop_config_path)
                        source_renamed = False
                    except OSError:
                        # The exact old file remains under the private backup
                        # name as recovery evidence. Never delete it merely
                        # because compensating publication also failed.
                        pass
                if source_renamed and public_name_exists:
                    with suppress(OSError):
                        _delete_windows_open_file_on_close(source_fd)
            with suppress(OSError):
                os.close(source_fd)


def _hash_file(
    path: Path,
    *,
    expected_mount_identity: tuple[str, int] | None = None,
) -> tuple[int, str, int]:
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
        if expected_mount_identity is not None and os.name != "nt":
            _ensure_opened_entry_on_mount(
                source_fd,
                expected_mount_identity,
                path,
            )

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
        allocated_bytes = max(
            total_bytes,
            int(getattr(opened_after, "st_blocks", 0) or 0) * 512,
        )
        return total_bytes, digest.hexdigest(), allocated_bytes
    finally:
        if source_fd >= 0:
            with suppress(OSError):
                os.close(source_fd)


def _filesystem_allocation_unit(path: Path) -> int:
    """Return the target filesystem's minimum allocation unit in bytes."""

    if os.name != "nt":
        filesystem = os.statvfs(path)
        allocation_unit = int(filesystem.f_frsize or filesystem.f_bsize or 0)
    else:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_volume_path = kernel32.GetVolumePathNameW
        get_volume_path.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        get_volume_path.restype = wintypes.BOOL
        get_disk_free_space = kernel32.GetDiskFreeSpaceW
        get_disk_free_space.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        get_disk_free_space.restype = wintypes.BOOL

        volume_path = ctypes.create_unicode_buffer(32768)
        if not get_volume_path(str(path.resolve()), volume_path, len(volume_path)):
            error_number = ctypes.get_last_error()
            raise OSError(error_number, ctypes.FormatError(error_number).strip())
        sectors_per_cluster = wintypes.DWORD()
        bytes_per_sector = wintypes.DWORD()
        free_clusters = wintypes.DWORD()
        total_clusters = wintypes.DWORD()
        if not get_disk_free_space(
            volume_path.value,
            ctypes.byref(sectors_per_cluster),
            ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters),
            ctypes.byref(total_clusters),
        ):
            error_number = ctypes.get_last_error()
            raise OSError(error_number, ctypes.FormatError(error_number).strip())
        allocation_unit = int(sectors_per_cluster.value) * int(bytes_per_sector.value)

    if allocation_unit <= 0:
        raise OSError(errno.EIO, "target filesystem returned an invalid allocation unit")
    return allocation_unit


def _filesystem_free_entry_count(path: Path) -> int | None:
    """Return free inode-like entries when the filesystem reports a fixed pool."""

    if os.name == "nt":
        return None
    filesystem = os.statvfs(path)
    if int(filesystem.f_files) <= 0:
        return None
    return max(0, int(filesystem.f_favail))


def _snapshot_path(
    path: Path,
    *,
    expected_mount_identity: tuple[str, int] | None = None,
    copy_capacity: _CopyCapacity | None = None,
    copy_allocation_unit: int = 0,
) -> dict[str, int | str]:
    if copy_capacity is not None and copy_allocation_unit <= 0:
        raise ValueError("copy allocation unit must be positive")
    if path_chain_has_symlink(path):
        raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接路径: {path}")
    if not path.exists():
        if path.is_symlink():
            raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {path}")
        return {"kind": "missing", "file_count": 0, "total_bytes": 0}
    if path.is_symlink():
        raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {path}")
    if path.is_file():
        if expected_mount_identity is None:
            total_bytes, digest, allocated_bytes = _hash_file(path)
        else:
            total_bytes, digest, allocated_bytes = _hash_file(
                path,
                expected_mount_identity=expected_mount_identity,
            )
        snapshot = {
            "kind": "file",
            "file_count": 1,
            "total_bytes": total_bytes,
            "sha256": digest,
        }
        if copy_capacity is not None:
            copy_capacity.required_bytes += allocated_bytes + copy_allocation_unit
            copy_capacity.entry_count += 1
        return snapshot
    if not path.is_dir():
        raise StorageMigrationError("path_type_unsupported", f"迁移校验不支持该文件类型: {path}")

    tree_mount_identity = _preflight_directory_tree_mounts(
        path,
        expected_mount_identity=expected_mount_identity,
    )
    total_bytes = 0
    file_count = 0
    copy_required_bytes = copy_allocation_unit if copy_capacity is not None else 0
    copy_entry_count = 1 if copy_capacity is not None else 0
    manifest_digest = hashlib.sha256()
    for current_root, dirnames, filenames in os.walk(path):
        _ensure_directory_path_on_mount(
            Path(current_root),
            tree_mount_identity,
        )
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
            _ensure_directory_path_on_mount(
                current_dir,
                tree_mount_identity,
            )
            manifest_digest.update(
                b"D\0" + os.fsencode((relative_root / dirname).as_posix()) + b"\0"
            )
            if copy_capacity is not None:
                copy_required_bytes += copy_allocation_unit
                copy_entry_count += 1
        for filename in filenames:
            current_file = Path(current_root) / filename
            if path_chain_has_symlink(current_file):
                raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {current_file}")
            if not current_file.is_file():
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"迁移校验不支持该文件类型: {current_file}",
                )
            file_bytes, file_digest, allocated_bytes = _hash_file(
                current_file,
                expected_mount_identity=tree_mount_identity,
            )
            relative_file = (relative_root / filename).as_posix()
            manifest_digest.update(
                b"F\0"
                + os.fsencode(relative_file)
                + b"\0"
                + str(file_bytes).encode("ascii")
                + b"\0"
                + file_digest.encode("ascii")
                + b"\0"
            )
            total_bytes += file_bytes
            file_count += 1
            if copy_capacity is not None:
                copy_required_bytes += allocated_bytes + copy_allocation_unit
                copy_entry_count += 1

    snapshot = {
        "kind": "dir",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "sha256": manifest_digest.hexdigest(),
    }
    if copy_capacity is not None:
        copy_capacity.required_bytes += copy_required_bytes
        copy_capacity.entry_count += copy_entry_count
    return snapshot


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


def validate_storage_migration_preflight_boundaries(
    source_root: Path | str,
    target_root: Path | str,
) -> None:
    """Reject unsafe runtime-entry boundaries without traversing their trees.

    Live preflight must not enter a nested mount before it has written a
    recovery checkpoint. Enumerate the POSIX mount table once, then compare
    names lexically so a stalled child mount can be rejected without touching
    it. Mounts elsewhere below either root are intentionally allowed because
    migration never reads or writes those unrelated paths.

    The canonical runtime-entry check remains the cross-platform boundary for
    symlinks and Windows reparse points. It runs only after the mount-table
    check so POSIX never has to inspect a known nested mount first.
    """

    roots = tuple(
        Path(os.path.abspath(os.fspath(Path(root).expanduser())))
        for root in (source_root, target_root)
    )
    entry_paths_by_root = tuple(
        (
            root,
            tuple(root / entry.relative_path for entry in RUNTIME_STORAGE_ENTRIES),
        )
        for root in roots
    )

    if os.name != "nt":
        mounted_paths = _mounted_paths()
        for root, entry_paths in entry_paths_by_root:
            for mount_path in mounted_paths:
                if mount_path == root:
                    # The selected storage root may itself be an external
                    # volume. Only mounts nested below that root are unsafe.
                    continue
                try:
                    mount_path.relative_to(root)
                except ValueError:
                    continue
                for entry_path in entry_paths:
                    try:
                        mount_path.relative_to(entry_path)
                        intersects_entry = True
                    except ValueError:
                        try:
                            entry_path.relative_to(mount_path)
                            intersects_entry = True
                        except ValueError:
                            intersects_entry = False
                    if intersects_entry:
                        raise StorageMigrationError(
                            "nested_mount_unsupported",
                            "迁移运行时条目路径包含嵌套挂载，"
                            f"已停止以避免访问挂载外数据: {mount_path}",
                        )

    for root, _entry_paths in entry_paths_by_root:
        for entry in RUNTIME_STORAGE_ENTRIES:
            _checked_migration_entry_path(root, entry)


def _snapshot_runtime_entries(
    root: Path,
    *,
    expected_mount_identity: tuple[str, int] | None = None,
) -> dict[str, dict[str, int | str]]:
    snapshots: dict[str, dict[str, int | str]] = {}
    runtime_mount_identity = (
        expected_mount_identity
        if expected_mount_identity is not None
        else _runtime_root_mount_identity(root)
    )
    for entry in RUNTIME_STORAGE_ENTRIES:
        entry_path = _checked_migration_entry_path(root, entry)
        if entry_path.exists() or entry_path.is_symlink():
            snapshots[entry.relative_path] = _snapshot_path(
                entry_path,
                expected_mount_identity=runtime_mount_identity,
            )
    return snapshots


def _runtime_root_mount_identity(root: Path) -> tuple[str, int] | None:
    if os.name == "nt" or not root.exists():
        return None
    root_fd = _open_verified_directory(root)
    try:
        return _opened_mount_identity(root_fd)
    finally:
        os.close(root_fd)


def _snapshot_path_within_root(
    root: Path,
    path: Path,
) -> dict[str, int | str]:
    return _snapshot_path(
        path,
        expected_mount_identity=_runtime_root_mount_identity(root),
    )


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


def _transaction_marker_payload_is_owned(
    marker_payload: Any,
    *,
    owner_token: str,
    txid: str,
) -> bool:
    return bool(
        isinstance(marker_payload, dict)
        and marker_payload.get("version") == _TRANSACTION_OWNER_MARKER_VERSION
        and str(marker_payload.get("txid") or "").strip().lower() == txid
        and secrets.compare_digest(
            str(marker_payload.get("owner_token") or "").strip().lower(),
            owner_token,
        )
    )


def _transaction_directory_fd_is_owned(
    payload: dict[str, Any],
    directory_fd: int,
    txid: str,
) -> bool:
    """Authenticate the owner marker relative to one pinned POSIX root."""

    owner_token = _transaction_owner_token(payload)
    if not owner_token:
        return False
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    marker_fd = -1
    try:
        before = os.stat(
            _TRANSACTION_OWNER_MARKER_FILENAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if _is_link_like_metadata(before) or not stat.S_ISREG(before.st_mode):
            return False
        marker_fd = os.open(
            _TRANSACTION_OWNER_MARKER_FILENAME,
            flags,
            dir_fd=directory_fd,
        )
        opened = os.fstat(marker_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(before, opened)
            or int(opened.st_size) > 4096
        ):
            return False
        raw = os.read(marker_fd, 4097)
        after = os.fstat(marker_fd)
        named = os.stat(
            _TRANSACTION_OWNER_MARKER_FILENAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            len(raw) > 4096
            or not os.path.samestat(opened, after)
            or not os.path.samestat(after, named)
            or int(after.st_size) != len(raw)
        ):
            return False
        marker_payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    finally:
        if marker_fd >= 0:
            with suppress(OSError):
                os.close(marker_fd)
    return _transaction_marker_payload_is_owned(
        marker_payload,
        owner_token=owner_token,
        txid=txid,
    )


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
    return _transaction_marker_payload_is_owned(
        marker_payload,
        owner_token=owner_token,
        txid=txid,
    )


def _create_owned_posix_transaction_root_at(
    payload: dict[str, Any],
    target_root_fd: int,
    target_root: Path,
    transaction_name: str,
    txid: str,
) -> os.stat_result:
    """Create and publish a marked transaction below one pinned target root."""

    if os.name == "nt":
        raise OSError("descriptor-relative transaction creation is unavailable")
    if Path(transaction_name).name != transaction_name:
        raise StorageMigrationError(
            "invalid_checkpoint",
            "迁移事务目录名称无效。",
        )
    owner_token = _transaction_owner_token(payload)
    if not owner_token:
        raise StorageMigrationError(
            "transaction_owner_missing",
            "迁移检查点缺少事务目录所有权凭据，已停止迁移。",
        )

    mount_identity = _opened_mount_identity(target_root_fd)
    prepared_name = f".{transaction_name}.{uuid.uuid4().hex}.tmp"
    prepared_display = target_root / prepared_name
    prepared_fd = -1
    prepared_identity: os.stat_result | None = None
    marker_fd = -1
    try:
        os.mkdir(prepared_name, mode=0o700, dir_fd=target_root_fd)
        prepared_identity = os.stat(
            prepared_name,
            dir_fd=target_root_fd,
            follow_symlinks=False,
        )
        prepared_fd = _open_or_create_posix_child_directory(
            target_root_fd,
            prepared_name,
            prepared_display,
            expected_mount_identity=mount_identity,
            allow_existing=True,
            create_missing=False,
        )
        opened_prepared_identity = os.fstat(prepared_fd)
        if not os.path.samestat(prepared_identity, opened_prepared_identity):
            raise StorageMigrationError(
                "transaction_ownership_changed",
                "迁移准备目录在创建后被替换。",
            )
        with os.scandir(prepared_fd) as initial_entries:
            if next(initial_entries, None) is not None:
                raise StorageMigrationError(
                    "transaction_path_occupied",
                    "迁移准备目录在所有权标记写入前已包含未知内容。",
                )
        marker_payload = {
            "version": _TRANSACTION_OWNER_MARKER_VERSION,
            "txid": txid,
            "owner_token": owner_token,
        }
        marker_bytes = (
            json.dumps(marker_payload, ensure_ascii=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        marker_fd = os.open(
            _TRANSACTION_OWNER_MARKER_FILENAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=prepared_fd,
        )
        view = memoryview(marker_bytes)
        while view:
            written = os.write(marker_fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short transaction marker write")
            view = view[written:]
        os.fsync(marker_fd)
        os.close(marker_fd)
        marker_fd = -1
        _fsync_opened_migration_directory(prepared_fd, prepared_display)
        if not _transaction_directory_fd_is_owned(payload, prepared_fd, txid):
            raise OSError("migration preparation directory ownership is unverifiable")
        _durable_publish_without_replacing_at(
            target_root_fd,
            prepared_name,
            target_root,
            target_root_fd,
            transaction_name,
            target_root,
        )
        published_identity = os.stat(
            transaction_name,
            dir_fd=target_root_fd,
            follow_symlinks=False,
        )
        if (
            _is_link_like_metadata(published_identity)
            or not stat.S_ISDIR(published_identity.st_mode)
            or not os.path.samestat(prepared_identity, published_identity)
            or not _transaction_directory_fd_is_owned(payload, prepared_fd, txid)
        ):
            raise OSError("published migration transaction ownership is unverifiable")
        return prepared_identity
    except BaseException:
        if prepared_fd >= 0 and prepared_identity is not None:
            try:
                named = os.stat(
                    prepared_name,
                    dir_fd=target_root_fd,
                    follow_symlinks=False,
                )
                with os.scandir(prepared_fd) as scanned:
                    entries = list(scanned)
                removable = not entries
                if (
                    len(entries) == 1
                    and entries[0].name == _TRANSACTION_OWNER_MARKER_FILENAME
                    and entries[0].is_file(follow_symlinks=False)
                ):
                    os.unlink(
                        _TRANSACTION_OWNER_MARKER_FILENAME,
                        dir_fd=prepared_fd,
                    )
                    _fsync_opened_migration_directory(prepared_fd, prepared_display)
                    removable = True
                if os.path.samestat(prepared_identity, named) and removable:
                    os.rmdir(prepared_name, dir_fd=target_root_fd)
                    _fsync_opened_migration_directory(target_root_fd, target_root)
            except FileNotFoundError:
                pass
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to clean migration preparation directory %s: %s",
                    prepared_display,
                    cleanup_exc,
                )
        raise
    finally:
        if marker_fd >= 0:
            with suppress(OSError):
                os.close(marker_fd)
        if prepared_fd >= 0:
            with suppress(OSError):
                os.close(prepared_fd)


def _create_owned_transaction_root(
    payload: dict[str, Any],
    transaction_root: Path,
    txid: str,
) -> os.stat_result:
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
    return prepared_identity


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


def _remove_owned_transaction_quarantine_posix(
    payload: dict[str, Any],
    quarantine: Path,
    txid: str,
    quarantine_identity: os.stat_result,
) -> bool:
    """Keep one authenticated root fd pinned through marker-last deletion."""

    _preflight_named_mounts_below(quarantine)
    parent_mount_identity = _runtime_root_mount_identity(quarantine.parent)
    if parent_mount_identity is None:
        return False
    mode_restore_plan = _DirectoryModeRestorePlan()
    _preflight_directory_tree_mounts(
        quarantine,
        expected_mount_identity=parent_mount_identity,
        expected_root_identity=quarantine_identity,
        make_traversable=True,
        retained_mode_plan=mode_restore_plan,
    )

    removed = False
    parent_fd = -1
    root_fd = -1
    try:
        parent_fd = _open_verified_directory(quarantine.parent)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        root_fd = os.open(quarantine.name, directory_flags, dir_fd=parent_fd)
        opened_root = os.fstat(root_fd)
        if (
            not os.path.samestat(quarantine_identity, opened_root)
            or _opened_mount_identity(parent_fd) != parent_mount_identity
        ):
            return False
        _ensure_opened_entry_on_mount(
            root_fd,
            parent_mount_identity,
            quarantine,
        )
        if not _transaction_directory_fd_is_owned(payload, root_fd, txid):
            return False

        _remove_posix_directory_children(
            root_fd,
            mount_identity=parent_mount_identity,
            display_path=quarantine,
            preserve_names=frozenset({_TRANSACTION_OWNER_MARKER_FILENAME}),
        )
        if (
            not os.path.samestat(quarantine_identity, os.fstat(root_fd))
            or not _transaction_directory_fd_is_owned(payload, root_fd, txid)
        ):
            return False
        with os.scandir(root_fd) as remaining_children:
            remaining = [child.name for child in remaining_children]
        if remaining != [_TRANSACTION_OWNER_MARKER_FILENAME]:
            return False
        named_root = os.stat(
            quarantine.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not os.path.samestat(opened_root, named_root):
            return False

        os.unlink(_TRANSACTION_OWNER_MARKER_FILENAME, dir_fd=root_fd)
        os.fsync(root_fd)
        named_root = os.stat(
            quarantine.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not os.path.samestat(opened_root, named_root):
            return False
        os.rmdir(quarantine.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        removed = True
        return True
    except OSError:
        return False
    finally:
        if root_fd >= 0:
            with suppress(OSError):
                os.close(root_fd)
        if parent_fd >= 0:
            with suppress(OSError):
                os.close(parent_fd)
        if removed:
            _close_mode_restore_plan(mode_restore_plan)
        else:
            _restore_directory_modes(mode_restore_plan, quarantine)


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
        if os.name != "nt":
            parent_fd = -1
            try:
                parent_fd = _open_verified_directory(quarantine.parent)
                current_identity = os.stat(
                    quarantine.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if (
                    not os.path.samestat(quarantine_identity, current_identity)
                    or not stat.S_ISDIR(current_identity.st_mode)
                    or _is_link_like_metadata(current_identity)
                ):
                    return False
                os.rmdir(quarantine.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                return False
            finally:
                if parent_fd >= 0:
                    with suppress(OSError):
                        os.close(parent_fd)
            return True
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
    if os.name != "nt":
        return _remove_owned_transaction_quarantine_posix(
            payload,
            quarantine,
            txid,
            quarantine_identity,
        )
    try:
        children = list(quarantine.iterdir())
    except OSError:
        return False
    for child in children:
        if child.name == _TRANSACTION_OWNER_MARKER_FILENAME:
            continue
        if (
            not os.path.samestat(quarantine_identity, quarantine.lstat())
            or not _transaction_root_is_owned(payload, quarantine, txid)
        ):
            return False
        _remove_existing_path(child)
        if (
            not os.path.samestat(quarantine_identity, quarantine.lstat())
            or not _transaction_root_is_owned(payload, quarantine, txid)
        ):
            return False

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


def _publish_posix_runtime_entry(
    roots: _PosixPublishRoots,
    target_root: Path,
    transaction_root: Path,
    relative_path: str,
    expected_target_snapshot: dict[str, int | str] | None,
) -> None:
    staged_root = transaction_root / "staged"
    backup_root = transaction_root / "backup"
    _ensure_posix_publish_roots_still_named(roots, target_root, transaction_root)
    target_parent_fd = -1
    staged_parent_fd = -1
    backup_parent_fd = -1
    try:
        target_parent_fd, target_name, target_parent_display = (
            _open_posix_relative_parent(
                roots.target_root_fd,
                target_root,
                relative_path,
                expected_mount_identity=roots.mount_identity,
                create_missing=True,
            )
        )
        staged_parent_fd, staged_name, staged_parent_display = (
            _open_posix_relative_parent(
                roots.staged_root_fd,
                staged_root,
                relative_path,
                expected_mount_identity=roots.mount_identity,
                create_missing=False,
            )
        )
        backup_parent_fd, backup_name, backup_parent_display = (
            _open_posix_relative_parent(
                roots.backup_root_fd,
                backup_root,
                relative_path,
                expected_mount_identity=roots.mount_identity,
                create_missing=True,
            )
        )
        target_exists = _posix_named_entry_exists(target_parent_fd, target_name)
        if expected_target_snapshot is None:
            target_unchanged = not target_exists
        else:
            target_unchanged = bool(
                target_exists
                and _snapshot_posix_entry_at(
                    target_parent_fd,
                    target_name,
                    target_parent_display / target_name,
                    expected_mount_identity=roots.mount_identity,
                )
                == expected_target_snapshot
            )
        if not target_unchanged:
            raise StorageMigrationError(
                "target_changed_during_publish",
                f"目标路径在发布前发生了变化，已停止迁移以保留新数据: {relative_path}",
            )
        if target_exists:
            _durable_publish_without_replacing_at(
                target_parent_fd,
                target_name,
                target_parent_display,
                backup_parent_fd,
                backup_name,
                backup_parent_display,
            )
            if _snapshot_posix_entry_at(
                backup_parent_fd,
                backup_name,
                backup_parent_display / backup_name,
                expected_mount_identity=roots.mount_identity,
            ) != expected_target_snapshot:
                raise StorageMigrationError(
                    "target_changed_during_publish",
                    f"目标路径在备份切换窗口发生了变化，已保留事务证据: {relative_path}",
                )
        try:
            _durable_publish_without_replacing_at(
                staged_parent_fd,
                staged_name,
                staged_parent_display,
                target_parent_fd,
                target_name,
                target_parent_display,
            )
        except OSError as exc:
            if _posix_named_entry_exists(target_parent_fd, target_name):
                raise StorageMigrationError(
                    "target_changed_during_publish",
                    f"目标路径在发布切换窗口出现了新数据，已停止迁移: {relative_path}",
                ) from exc
            raise
        _ensure_opened_directory_still_named(
            target_parent_display,
            target_parent_fd,
            error_code="migration_path_changed",
            message=f"迁移目标父目录在发布期间被替换: {target_parent_display}",
        )
        _ensure_opened_directory_still_named(
            staged_parent_display,
            staged_parent_fd,
            error_code="staging_entry_changed",
            message=f"迁移暂存父目录在发布期间被替换: {staged_parent_display}",
        )
        _ensure_opened_directory_still_named(
            backup_parent_display,
            backup_parent_fd,
            error_code="staging_entry_changed",
            message=f"迁移备份父目录在发布期间被替换: {backup_parent_display}",
        )
    finally:
        for fd in (backup_parent_fd, staged_parent_fd, target_parent_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
    _ensure_posix_publish_roots_still_named(roots, target_root, transaction_root)


def _rollback_published_entries(
    target_root: Path,
    transaction_root: Path,
    original_target_entries: list[str],
    publish_entry_names: list[str],
    target_baseline: dict[str, dict[str, int | str]],
    publish_entry_snapshots: dict[str, dict[str, int | str]],
    *,
    payload: dict[str, Any] | None = None,
    txid: str | None = None,
    posix_roots: _PosixPublishRoots | None = None,
) -> None:
    owned_roots: _PosixPublishRoots | None = None
    if os.name != "nt" and posix_roots is None:
        if payload is None or txid is None:
            raise StorageMigrationError(
                "rollback_checkpoint_inconsistent",
                "迁移回滚缺少事务所有权凭据。",
            )
        owned_roots = _open_posix_publish_roots(
            payload,
            target_root,
            transaction_root,
            txid,
        )
        posix_roots = owned_roots
    try:
        _rollback_published_entries_impl(
            target_root,
            transaction_root,
            original_target_entries,
            publish_entry_names,
            target_baseline,
            publish_entry_snapshots,
            posix_roots=posix_roots,
        )
    finally:
        if owned_roots is not None:
            owned_roots.close()


def _rollback_published_entries_impl(
    target_root: Path,
    transaction_root: Path,
    original_target_entries: list[str],
    publish_entry_names: list[str],
    target_baseline: dict[str, dict[str, int | str]],
    publish_entry_snapshots: dict[str, dict[str, int | str]],
    *,
    posix_roots: _PosixPublishRoots | None,
) -> None:
    if posix_roots is None and (
        path_chain_has_symlink(target_root)
        or path_chain_has_symlink(transaction_root)
    ):
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

    def _root_descriptor(scope: str) -> tuple[int, Path]:
        assert posix_roots is not None
        if scope == "target":
            return posix_roots.target_root_fd, target_root
        if scope == "staged":
            return posix_roots.staged_root_fd, staged_root
        if scope == "backup":
            return posix_roots.backup_root_fd, backup_root
        raise ValueError(f"unknown migration root scope: {scope}")

    def _move_without_replacing(
        source_scope: str,
        source_path: Path,
        target_scope: str,
        target_path: Path,
        relative_path: str,
    ) -> None:
        if posix_roots is None:
            _durable_publish_without_replacing(source_path, target_path)
            return
        source_root_fd, source_root_display = _root_descriptor(source_scope)
        target_root_fd, target_root_display = _root_descriptor(target_scope)
        source_parent_fd = -1
        target_parent_fd = -1
        try:
            source_parent_fd, source_name, source_parent_display = (
                _open_posix_relative_parent(
                    source_root_fd,
                    source_root_display,
                    relative_path,
                    expected_mount_identity=posix_roots.mount_identity,
                    create_missing=False,
                )
            )
            target_parent_fd, target_name, target_parent_display = (
                _open_posix_relative_parent(
                    target_root_fd,
                    target_root_display,
                    relative_path,
                    expected_mount_identity=posix_roots.mount_identity,
                    create_missing=True,
                )
            )
            _durable_publish_without_replacing_at(
                source_parent_fd,
                source_name,
                source_parent_display,
                target_parent_fd,
                target_name,
                target_parent_display,
            )
        finally:
            if target_parent_fd >= 0:
                os.close(target_parent_fd)
            if source_parent_fd >= 0:
                os.close(source_parent_fd)

    def _unlink_staged(relative_path: str, staged_path: Path) -> None:
        if posix_roots is None:
            staged_path.unlink()
            fsync_directory_best_effort(staged_path.parent)
            return
        parent_fd = -1
        try:
            parent_fd, name, parent_display = _open_posix_relative_parent(
                posix_roots.staged_root_fd,
                staged_root,
                relative_path,
                expected_mount_identity=posix_roots.mount_identity,
                create_missing=False,
            )
            os.unlink(name, dir_fd=parent_fd)
            _fsync_opened_migration_directory(parent_fd, parent_display)
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)

    def _entry_snapshot(
        scope: str,
        relative_path: str,
        path: Path,
    ) -> dict[str, int | str]:
        if posix_roots is None:
            root = target_root if scope == "target" else transaction_root
            return _snapshot_path_within_root(root, path)
        root_fd, root_display = _root_descriptor(scope)
        return _snapshot_posix_relative_entry(
            root_fd,
            root_display,
            relative_path,
            expected_mount_identity=posix_roots.mount_identity,
        )

    def _entry_stat(
        scope: str,
        relative_path: str,
        path: Path,
    ) -> os.stat_result | None:
        if posix_roots is None:
            try:
                return path.lstat()
            except FileNotFoundError:
                return None
        root_fd, root_display = _root_descriptor(scope)
        return _stat_posix_relative_entry(
            root_fd,
            root_display,
            relative_path,
            expected_mount_identity=posix_roots.mount_identity,
        )

    for entry in reversed(RUNTIME_STORAGE_ENTRIES):
        relative_path = entry.relative_path
        if relative_path not in publish_entries:
            continue
        if posix_roots is None:
            target_path = _checked_migration_entry_path(target_root, relative_path)
            staged_path = _checked_migration_entry_path(staged_root, relative_path)
            backup_path = _checked_migration_entry_path(backup_root, relative_path)
        else:
            # Display-only paths: all POSIX I/O below is rooted at pinned FDs.
            target_path = target_root / relative_path
            staged_path = staged_root / relative_path
            backup_path = backup_root / relative_path
        target_snapshot = _entry_snapshot("target", relative_path, target_path)
        staged_snapshot = _entry_snapshot("staged", relative_path, staged_path)
        backup_snapshot = _entry_snapshot("backup", relative_path, backup_path)
        target_exists = target_snapshot["kind"] != "missing"
        staged_exists = staged_snapshot["kind"] != "missing"
        backup_exists = backup_snapshot["kind"] != "missing"
        if target_exists and staged_exists:
            # Compatibility with checkpoints created by the earlier POSIX
            # link-then-unlink publisher.  A process loss between those two
            # syscalls leaves two names for our one verified file.  Collapse
            # only that exact same-inode state; equal content at a different
            # inode remains an external collision and stays fail-closed.
            try:
                target_metadata = _entry_stat("target", relative_path, target_path)
                staged_metadata = _entry_stat("staged", relative_path, staged_path)
                interrupted_file_publish = bool(
                    target_metadata is not None
                    and staged_metadata is not None
                    and stat.S_ISREG(target_metadata.st_mode)
                    and stat.S_ISREG(staged_metadata.st_mode)
                    and os.path.samestat(target_metadata, staged_metadata)
                    and target_snapshot == publish_entry_snapshots[relative_path]
                    and staged_snapshot == publish_entry_snapshots[relative_path]
                )
            except OSError:
                interrupted_file_publish = False
            if interrupted_file_publish:
                _unlink_staged(relative_path, staged_path)
                staged_exists = False
                staged_snapshot = {
                    "kind": "missing",
                    "file_count": 0,
                    "total_bytes": 0,
                }
        if relative_path in original_entries:
            if backup_exists:
                if backup_snapshot != target_baseline[relative_path]:
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
                    if staged_snapshot != publish_entry_snapshots[relative_path]:
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                        )
                else:
                    if not target_exists or (
                        target_snapshot != publish_entry_snapshots[relative_path]
                    ):
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移已发布数据被改写或缺失，无法安全覆盖: {relative_path}",
                        )
                    _move_without_replacing(
                        "target",
                        target_path,
                        "staged",
                        staged_path,
                        relative_path,
                    )
                    staged_snapshot = _entry_snapshot(
                        "staged",
                        relative_path,
                        staged_path,
                    )
                    if staged_snapshot != publish_entry_snapshots[relative_path]:
                        raise StorageMigrationError(
                            "rollback_target_changed",
                            f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                        )
                _move_without_replacing(
                    "backup",
                    backup_path,
                    "target",
                    target_path,
                    relative_path,
                )
            elif target_snapshot != target_baseline[relative_path]:
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
            if staged_snapshot != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                )
        elif target_exists:
            # Move our verified published copy back under the owned transaction
            # instead of deleting it in place. Both the pre- and post-move
            # snapshots are required so a replacement race is preserved as
            # recovery evidence rather than recursively erased.
            if target_snapshot != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移已发布数据被并发改写，无法安全删除: {relative_path}",
                )
            _move_without_replacing(
                "target",
                target_path,
                "staged",
                staged_path,
                relative_path,
            )
            staged_snapshot = _entry_snapshot(
                "staged",
                relative_path,
                staged_path,
            )
            if staged_snapshot != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移回滚暂存内容与已发布摘要不一致: {relative_path}",
                )

    restored_target = (
        _snapshot_posix_runtime_entries(posix_roots, target_root)
        if posix_roots is not None
        else _snapshot_runtime_entries(target_root)
    )
    if restored_target != target_baseline:
        raise StorageMigrationError(
            "rollback_verification_failed",
            "目标路径回滚后的数据清单与迁移前基线不一致，已保留事务目录等待恢复。",
        )


def _fsync_opened_posix_staged_tree(
    directory_fd: int,
    path: Path,
    expected_mount_identity: tuple[str, int],
) -> None:
    """Flush a staged tree without re-resolving its replaceable root path."""

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    _ensure_opened_entry_on_mount(directory_fd, expected_mount_identity, path)
    try:
        with os.scandir(directory_fd) as entries:
            child_names = sorted(entry.name for entry in entries)
        for child_name in child_names:
            child_path = path / child_name
            named_before = os.stat(
                child_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if _is_link_like_metadata(named_before):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移暂存区包含符号链接: {child_path}",
                )
            if stat.S_ISDIR(named_before.st_mode):
                child_fd = -1
                try:
                    child_fd = os.open(
                        child_name,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                    opened = os.fstat(child_fd)
                    named_after_open = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        _is_link_like_metadata(opened)
                        or _is_link_like_metadata(named_after_open)
                        or not stat.S_ISDIR(opened.st_mode)
                        or not stat.S_ISDIR(named_after_open.st_mode)
                        or not os.path.samestat(named_before, opened)
                        or not os.path.samestat(opened, named_after_open)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存目录在持久化期间被替换: {child_path}",
                        )
                    _ensure_opened_entry_on_mount(
                        child_fd,
                        expected_mount_identity,
                        child_path,
                    )
                    _fsync_opened_posix_staged_tree(
                        child_fd,
                        child_path,
                        expected_mount_identity,
                    )
                    named_after_flush = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if not os.path.samestat(opened, named_after_flush):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存目录在持久化期间被替换: {child_path}",
                        )
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
                continue
            if stat.S_ISREG(named_before.st_mode):
                child_fd = -1
                try:
                    child_fd = os.open(
                        child_name,
                        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    opened = os.fstat(child_fd)
                    named_after_open = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or not stat.S_ISREG(named_after_open.st_mode)
                        or not os.path.samestat(named_before, opened)
                        or not os.path.samestat(opened, named_after_open)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存文件在持久化期间被替换: {child_path}",
                        )
                    _ensure_opened_entry_on_mount(
                        child_fd,
                        expected_mount_identity,
                        child_path,
                    )
                    os.fsync(child_fd)
                    named_after_flush = os.stat(
                        child_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if not os.path.samestat(opened, named_after_flush):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"迁移暂存文件在持久化期间被替换: {child_path}",
                        )
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
                continue
            raise StorageMigrationError(
                "path_type_unsupported",
                f"迁移暂存区包含不支持的文件类型: {child_path}",
            )

        with os.scandir(directory_fd) as final_entries:
            final_names = sorted(entry.name for entry in final_entries)
        if final_names != child_names:
            raise StorageMigrationError(
                "staging_entry_changed",
                f"迁移暂存目录在持久化期间发生变化: {path}",
            )
        os.fsync(directory_fd)
    except StorageMigrationError:
        raise
    except OSError as exc:
        raise StorageMigrationError(
            "target_flush_failed",
            f"迁移数据无法可靠写入目标磁盘: {path}: {exc}",
        ) from exc


def _fsync_staged_tree(
    path: Path,
    *,
    root_fd: int | None = None,
    expected_mount_identity: tuple[str, int] | None = None,
) -> None:
    if os.name != "nt" and root_fd is not None:
        mount_identity = expected_mount_identity or _opened_mount_identity(root_fd)
        _fsync_opened_posix_staged_tree(root_fd, path, mount_identity)
        return

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


def _ensure_target_root_writable(target_root: Path) -> os.stat_result:
    missing_directories: list[Path] = []
    candidate = target_root
    while not candidate.exists() and candidate.parent != candidate:
        missing_directories.append(candidate)
        candidate = candidate.parent
    target_root.mkdir(parents=True, exist_ok=True)
    for created_directory in missing_directories:
        _fsync_migration_directory(created_directory.parent)
    target_identity = target_root.lstat()
    if _is_link_like_metadata(target_identity) or not stat.S_ISDIR(
        target_identity.st_mode
    ):
        raise StorageMigrationError(
            "target_not_writable",
            "目标路径不是可安全固定的目录。",
        )
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
    try:
        verified_identity = target_root.lstat()
    except OSError as exc:
        raise StorageMigrationError(
            "migration_path_changed",
            "目标路径在可写性检查期间发生变化。",
        ) from exc
    if (
        _is_link_like_metadata(verified_identity)
        or not stat.S_ISDIR(verified_identity.st_mode)
        or not os.path.samestat(target_identity, verified_identity)
    ):
        raise StorageMigrationError(
            "migration_path_changed",
            "目标路径在可写性检查期间被替换。",
        )
    return verified_identity


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
    normalized_source_root = normalize_runtime_root(source_root)
    normalized_target_root = normalize_runtime_root(target_root)
    # This builder is also used outside the HTTP router.  Keep checkpoint
    # creation itself from entering a known nested mount so every caller gets
    # the same pre-shutdown safety boundary.
    validate_storage_migration_preflight_boundaries(
        normalized_source_root,
        normalized_target_root,
    )
    return {
        "version": STORAGE_MIGRATION_VERSION,
        "txid": str(txid or uuid.uuid4().hex),
        "transaction_owner_token": secrets.token_hex(32),
        "status": STORAGE_MIGRATION_STATUS_PENDING,
        "source_root": str(normalized_source_root),
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
    persisted_payload = dict(payload)
    if "error_message" in persisted_payload:
        persisted_payload["error_message"] = _bounded_migration_error_message(
            persisted_payload.get("error_message")
        )
    atomic_write_json(migration_path, persisted_payload, ensure_ascii=False, indent=2)
    # The generic writer is intentionally best-effort for directory handles so
    # ordinary application writes remain portable. Migration checkpoints are
    # recovery authority, so POSIX must not report success until the replace is
    # also durable in its parent directory.
    _fsync_migration_directory(migration_path.parent)
    return persisted_payload


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
    windows_directory_guards: list[int] = []
    windows_guarded_directory_paths: set[str] = set()
    posix_publish_roots: _PosixPublishRoots | None = None
    expected_target_identity: os.stat_result | None = None
    pinned_target_root_fd = -1

    def _release_windows_directory_guards() -> None:
        while windows_directory_guards:
            _close_windows_directory_rename_guard(windows_directory_guards.pop())
        windows_guarded_directory_paths.clear()

    def _retain_windows_directory_guard(
        path: Path,
        *,
        error_code: str,
        message: str,
        expected_identity: os.stat_result | None = None,
    ) -> None:
        if os.name != "nt":
            return
        key = os.path.normcase(os.path.abspath(os.fspath(path)))
        if key in windows_guarded_directory_paths:
            return
        try:
            identity = path.lstat()
        except OSError as exc:
            raise StorageMigrationError(error_code, f"{message}: {exc}") from exc
        if (
            _is_link_like_metadata(identity)
            or not stat.S_ISDIR(identity.st_mode)
            or (
                expected_identity is not None
                and not os.path.samestat(expected_identity, identity)
            )
        ):
            raise StorageMigrationError(error_code, message)
        handle = _open_windows_directory_rename_guard(path, identity)
        try:
            windows_directory_guards.append(handle)
            windows_guarded_directory_paths.add(key)
        except BaseException:
            _close_windows_directory_rename_guard(handle)
            raise

    def _retain_windows_relative_parent_guards(
        root: Path,
        relative_path: str,
        *,
        create_missing: bool,
        error_code: str,
    ) -> None:
        current = root
        for part in Path(relative_path).parts[:-1]:
            current = current / part
            if create_missing:
                current.mkdir(exist_ok=True)
            _retain_windows_directory_guard(
                current,
                error_code=error_code,
                message=f"迁移发布父目录在 Windows 固定期间发生变化: {current}",
            )

    def _release_posix_publish_roots() -> None:
        nonlocal posix_publish_roots
        if posix_publish_roots is not None:
            posix_publish_roots.close()
            posix_publish_roots = None

    def _release_pinned_target_root() -> None:
        nonlocal pinned_target_root_fd
        if pinned_target_root_fd >= 0:
            with suppress(OSError):
                os.close(pinned_target_root_fd)
            pinned_target_root_fd = -1

    def _finish_failure(
        error_code: str,
        error_message: str,
        *,
        rollback_required: bool = False,
    ) -> dict[str, Any]:
        nonlocal payload, policy_payload
        error_message = _bounded_migration_error_message(error_message)
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

        # The desktop preflight is advisory and a mount can appear after it.
        # Re-run the non-traversing boundary check in the packaged launcher
        # before any source/target snapshot can enter a nested filesystem.  A
        # publish-stage failure must preserve the transaction for recovery
        # rather than attempting a rollback through the same unsafe boundary.
        try:
            validate_storage_migration_preflight_boundaries(source_root, target_root)
        except StorageMigrationError as boundary_exc:
            return _finish_failure(
                boundary_exc.error_code,
                boundary_exc.message,
                rollback_required=publish_started,
            )

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
                if os.name != "nt":
                    try:
                        _preflight_named_mounts_below(transaction_root)
                    except StorageMigrationError as boundary_exc:
                        return _finish_failure(
                            boundary_exc.error_code,
                            boundary_exc.message,
                            rollback_required=True,
                        )
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
                    if os.name == "nt":
                        _retain_windows_directory_guard(
                            target_root,
                            error_code="rollback_target_changed",
                            message="迁移目标根在 Windows 回滚固定期间发生变化。",
                        )
                        recovery_transaction_identity = transaction_root.lstat()
                        windows_directory_guards.append(
                            _open_windows_directory_rename_guard(
                                transaction_root,
                                recovery_transaction_identity,
                            )
                        )
                        for recovery_directory in (
                            transaction_root / "staged",
                            transaction_root / "backup",
                        ):
                            recovery_identity = recovery_directory.lstat()
                            windows_directory_guards.append(
                                _open_windows_directory_rename_guard(
                                    recovery_directory,
                                    recovery_identity,
                                )
                            )
                        if not _transaction_root_is_owned(
                            payload,
                            transaction_root,
                            txid,
                        ):
                            raise StorageMigrationError(
                                "rollback_transaction_unowned",
                                "迁移事务目录在 Windows 回滚固定后失去所有权。",
                            )
                        recovery_staged_root = transaction_root / "staged"
                        recovery_backup_root = transaction_root / "backup"
                        for relative_path in publish_entry_names:
                            for root in (
                                target_root,
                                recovery_staged_root,
                                recovery_backup_root,
                            ):
                                parent = root.joinpath(*Path(relative_path).parts[:-1])
                                if parent == root:
                                    continue
                                if parent.exists() or parent.is_symlink():
                                    _retain_windows_relative_parent_guards(
                                        root,
                                        relative_path,
                                        create_missing=False,
                                        error_code="rollback_target_changed",
                                    )
                    _rollback_published_entries(
                        target_root,
                        transaction_root,
                        original_target_entries,
                        publish_entry_names,
                        target_baseline,
                        publish_entry_snapshots,
                        payload=payload,
                        txid=txid,
                    )
                except Exception as rollback_exc:
                    return _finish_failure(
                        "rollback_failed",
                        f"迁移目标回滚未完成: {rollback_exc}",
                        rollback_required=True,
                    )
                finally:
                    _release_windows_directory_guards()
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

        # Once legacy recovery evidence is safely retired, start the retry in
        # the current target-contained layout. Descriptor-relative creation
        # deliberately cannot recreate the old sibling transaction path.
        transaction_root = _transaction_root_for(target_root, txid)
        transaction_checkpoint_bound = False

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

        writable_target_identity = _ensure_target_root_writable(target_root)
        if os.name == "nt":
            expected_target_identity = writable_target_identity
            _retain_windows_directory_guard(
                target_root,
                error_code="migration_path_changed",
                message="迁移目标根在 Windows 预检固定期间发生变化。",
                expected_identity=expected_target_identity,
            )
            current_target_snapshot = _snapshot_runtime_entries(target_root)
        else:
            pinned_target_root_fd = _open_verified_directory(target_root)
            expected_target_identity = os.fstat(pinned_target_root_fd)
            if not os.path.samestat(
                writable_target_identity,
                expected_target_identity,
            ):
                raise StorageMigrationError(
                    "migration_path_changed",
                    "迁移目标根在可写性检查后被替换。",
                )
            pinned_target_mount_identity = _opened_mount_identity(
                pinned_target_root_fd
            )
            current_target_snapshot = _snapshot_posix_runtime_entries_at(
                pinned_target_root_fd,
                target_root,
                expected_mount_identity=pinned_target_mount_identity,
            )
            _ensure_opened_directory_still_named(
                target_root,
                pinned_target_root_fd,
                error_code="migration_path_changed",
                message=f"迁移目标根在预检快照期间被替换: {target_root}",
            )
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

        try:
            copy_allocation_unit = _filesystem_allocation_unit(target_root)
        except OSError as exc:
            raise StorageMigrationError(
                "disk_space_unavailable",
                f"无法确认目标卷分配单元，已停止迁移: {exc}",
            ) from exc

        source_mount_identity = _runtime_root_mount_identity(source_root)
        existing_entries = _iter_existing_runtime_entries(source_root)
        copy_capacity = _CopyCapacity()
        source_snapshots: dict[str, dict[str, int | str]] = {}
        for entry_name in existing_entries:
            source_snapshots[entry_name] = _snapshot_path(
                _checked_migration_entry_path(source_root, entry_name),
                expected_mount_identity=source_mount_identity,
                copy_capacity=copy_capacity,
                copy_allocation_unit=copy_allocation_unit,
            )
        source_runtime_baseline = dict(source_snapshots)
        # The transaction root, prepared root, owner marker, staged/backup
        # roots and atomic transaction metadata need a small fixed allocation
        # in addition to the source tree. Count both bytes and entries: Windows
        # has no portable free-inode probe, and allocation units can be large.
        required_bytes = (
            copy_capacity.required_bytes
            + _TRANSACTION_CAPACITY_ENTRY_RESERVE * copy_allocation_unit
        )
        # Even an empty managed source still creates the prepared transaction
        # directory, owner marker, staged/backup roots and transaction metadata.
        # Keep the fixed reserve independent of source entry count so migration
        # cannot pass preflight and then strand startup while writing metadata.
        safety_margin_bytes = max(64 * 1024 * 1024, int(required_bytes * 0.05))
        try:
            target_free_bytes = int(shutil.disk_usage(str(target_root)).free)
            target_free_entries = _filesystem_free_entry_count(target_root)
        except OSError as exc:
            raise StorageMigrationError(
                "disk_space_unavailable",
                f"无法确认目标卷剩余空间，已停止迁移: {exc}",
            ) from exc
        required_entries = (
            copy_capacity.entry_count + _TRANSACTION_CAPACITY_ENTRY_RESERVE
        )
        if required_bytes + safety_margin_bytes > target_free_bytes or (
            target_free_entries is not None
            and required_entries > target_free_entries
        ):
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
        if pinned_target_root_fd >= 0:
            _ensure_opened_directory_still_named(
                target_root,
                pinned_target_root_fd,
                error_code="migration_path_changed",
                message=f"迁移目标根在事务创建前被替换: {target_root}",
            )
        try:
            if pinned_target_root_fd >= 0:
                created_transaction_identity = (
                    _create_owned_posix_transaction_root_at(
                        payload,
                        pinned_target_root_fd,
                        target_root,
                        transaction_root.name,
                        txid,
                    )
                )
            else:
                created_transaction_identity = _create_owned_transaction_root(
                    payload,
                    transaction_root,
                    txid,
                )
        except FileExistsError as exc:
            raise StorageMigrationError(
                "transaction_path_occupied",
                "迁移事务目录已被其他内容占用，已停止迁移以避免删除未知数据。",
            ) from exc
        transaction_owned = True
        staged_root = transaction_root / "staged"
        backup_root = transaction_root / "backup"
        transaction_root_fd = -1
        staged_root_fd = -1
        staged_target_mount_identity: tuple[str, int] | None = None
        try:
            if os.name != "nt":
                assert pinned_target_root_fd >= 0
                pinned_target_mount_identity = _opened_mount_identity(
                    pinned_target_root_fd
                )
                transaction_root_fd = _open_or_create_posix_child_directory(
                    pinned_target_root_fd,
                    transaction_root.name,
                    transaction_root,
                    expected_mount_identity=pinned_target_mount_identity,
                    allow_existing=True,
                    create_missing=False,
                )
                if (
                    not os.path.samestat(
                        created_transaction_identity,
                        os.fstat(transaction_root_fd),
                    )
                    or not _transaction_directory_fd_is_owned(
                        payload,
                        transaction_root_fd,
                        txid,
                    )
                ):
                    raise StorageMigrationError(
                        "transaction_ownership_changed",
                        "迁移事务目录在暂存初始化前被替换，已安全停止迁移。",
                    )
                staged_target_mount_identity = _opened_mount_identity(
                    transaction_root_fd
                )
                staged_root_fd = _open_or_create_posix_child_directory(
                    transaction_root_fd,
                    "staged",
                    staged_root,
                    expected_mount_identity=staged_target_mount_identity,
                    allow_existing=False,
                )
                backup_root_fd = _open_or_create_posix_child_directory(
                    transaction_root_fd,
                    "backup",
                    backup_root,
                    expected_mount_identity=staged_target_mount_identity,
                    allow_existing=False,
                )
                os.close(backup_root_fd)
                _ensure_opened_directory_still_named(
                    transaction_root,
                    transaction_root_fd,
                    error_code="staging_entry_changed",
                    message=f"迁移事务目录在暂存初始化期间被替换: {transaction_root}",
                )
            else:
                transaction_guard = _open_windows_directory_rename_guard(
                    transaction_root,
                    created_transaction_identity,
                )
                windows_directory_guards.append(transaction_guard)
                if not _transaction_root_is_owned(payload, transaction_root, txid):
                    raise StorageMigrationError(
                        "transaction_ownership_changed",
                        "迁移事务目录在暂存初始化前被替换，已安全停止迁移。",
                    )
                staged_root.mkdir()
                staged_identity = staged_root.lstat()
                windows_directory_guards.append(
                    _open_windows_directory_rename_guard(
                        staged_root,
                        staged_identity,
                    )
                )
                backup_root.mkdir()
                backup_identity = backup_root.lstat()
                windows_directory_guards.append(
                    _open_windows_directory_rename_guard(
                        backup_root,
                        backup_identity,
                    )
                )
                if not _transaction_root_is_owned(payload, transaction_root, txid):
                    raise StorageMigrationError(
                        "transaction_ownership_changed",
                        "迁移事务目录在暂存初始化期间失去所有权，已安全停止迁移。",
                    )
            # These names are the only destinations for copied data and original
            # target backups. Persist them before advancing to COPYING; the
            # transaction name itself was flushed by _create_owned_transaction_root.
            if os.name != "nt":
                try:
                    os.fsync(transaction_root_fd)
                except OSError as exc:
                    raise StorageMigrationError(
                        "target_flush_failed",
                        f"迁移数据无法可靠写入目标磁盘: {transaction_root}: {exc}",
                    ) from exc
                _ensure_opened_directory_still_named(
                    transaction_root,
                    transaction_root_fd,
                    error_code="staging_entry_changed",
                    message=f"迁移事务目录在持久化期间被替换: {transaction_root}",
                )
            else:
                _fsync_migration_directory(transaction_root)

            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                status=STORAGE_MIGRATION_STATUS_COPYING,
            )
            if os.name != "nt":
                _ensure_opened_directory_still_named(
                    transaction_root,
                    transaction_root_fd,
                    error_code="staging_entry_changed",
                    message=f"迁移事务目录在复制检查点持久化期间被替换: {transaction_root}",
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
                _snapshot_path(
                    source_entry,
                    expected_mount_identity=source_mount_identity,
                )
                staged_parent_fd = -1
                try:
                    if os.name != "nt":
                        assert staged_root_fd >= 0
                        assert staged_target_mount_identity is not None
                        entry_parts = Path(entry_name).parts
                        staged_parent_fd = _open_or_create_posix_directory_chain(
                            staged_root_fd,
                            staged_root,
                            entry_parts[:-1],
                            expected_mount_identity=staged_target_mount_identity,
                        )
                        _copy_runtime_entry(
                            source_entry,
                            staged_entry,
                            expected_mount_identity=source_mount_identity,
                            target_parent_fd=staged_parent_fd,
                            target_name=entry_parts[-1],
                            expected_target_mount_identity=staged_target_mount_identity,
                        )
                    else:
                        _retain_windows_relative_parent_guards(
                            staged_root,
                            entry_name,
                            create_missing=True,
                            error_code="staging_entry_changed",
                        )
                        _copy_runtime_entry(
                            source_entry,
                            staged_entry,
                            expected_mount_identity=source_mount_identity,
                        )
                finally:
                    if staged_parent_fd >= 0:
                        os.close(staged_parent_fd)
                source_snapshot_after = _snapshot_path(
                    source_entry,
                    expected_mount_identity=source_mount_identity,
                )
                if source_snapshot_after != source_snapshot_before:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        f"迁移期间源数据发生变化，已停止迁移: {entry_name}",
                    )
                if os.name != "nt":
                    _ensure_opened_directory_still_named(
                        staged_root,
                        staged_root_fd,
                        error_code="staging_entry_changed",
                        message=f"迁移暂存根在复制期间被替换: {staged_root}",
                    )
                    assert staged_target_mount_identity is not None
                    staged_snapshot = _snapshot_posix_relative_entry(
                        staged_root_fd,
                        staged_root,
                        entry_name,
                        expected_mount_identity=staged_target_mount_identity,
                    )
                else:
                    staged_snapshot = _snapshot_path_within_root(
                        transaction_root,
                        staged_entry,
                    )
                if staged_snapshot != source_snapshot_before:
                    raise StorageMigrationError(
                        "verification_failed",
                        f"迁移暂存校验失败：{entry_name} 未完整复制。",
                    )
                source_snapshots[entry_name] = staged_snapshot

            rewritten_config_snapshot = _rewrite_migrated_runtime_config_paths(
                source_root=source_root,
                content_root=staged_root,
                target_root=target_root,
                content_root_fd=staged_root_fd if os.name != "nt" else None,
                expected_target_mount_identity=staged_target_mount_identity,
            )
            if os.name != "nt":
                _ensure_opened_directory_still_named(
                    staged_root,
                    staged_root_fd,
                    error_code="staging_entry_changed",
                    message=f"迁移暂存根在复制期间被替换: {staged_root}",
                )
                _fsync_staged_tree(
                    staged_root,
                    root_fd=staged_root_fd,
                    expected_mount_identity=staged_target_mount_identity,
                )
                if (
                    rewritten_config_snapshot is not None
                    and "config" in source_snapshots
                ):
                    _ensure_opened_directory_still_named(
                        staged_root,
                        staged_root_fd,
                        error_code="staging_entry_changed",
                        message=f"迁移暂存根在配置校验前被替换: {staged_root}",
                    )
                    assert staged_target_mount_identity is not None
                    current_rewritten_snapshot = _snapshot_posix_relative_entry(
                        staged_root_fd,
                        staged_root,
                        "config",
                        expected_mount_identity=staged_target_mount_identity,
                    )
                    if current_rewritten_snapshot != rewritten_config_snapshot:
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            "迁移暂存配置在重写清单固定后发生变化。",
                        )
                    source_snapshots["config"] = rewritten_config_snapshot
                    _ensure_opened_directory_still_named(
                        staged_root,
                        staged_root_fd,
                        error_code="staging_entry_changed",
                        message=f"迁移暂存根在配置校验期间被替换: {staged_root}",
                    )
        finally:
            if staged_root_fd >= 0:
                os.close(staged_root_fd)
            if transaction_root_fd >= 0:
                os.close(transaction_root_fd)
        if (
            os.name == "nt"
            and rewritten_config_snapshot is not None
            and "config" in source_snapshots
        ):
            current_rewritten_snapshot = _snapshot_path_within_root(
                transaction_root,
                _checked_migration_entry_path(staged_root, "config"),
            )
            if current_rewritten_snapshot != rewritten_config_snapshot:
                raise StorageMigrationError(
                    "staging_entry_changed",
                    "迁移暂存配置在重写清单固定后发生变化。",
                )
            source_snapshots["config"] = rewritten_config_snapshot
        if os.name == "nt":
            # POSIX flushed through the pinned staging descriptor above. Windows
            # retains its best-effort directory barriers after each copied file
            # was flushed through its writable handle.
            _fsync_staged_tree(staged_root)

        if _snapshot_runtime_entries(
            source_root,
            expected_mount_identity=source_mount_identity,
        ) != source_runtime_baseline:
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

        if os.name != "nt":
            posix_publish_roots = _open_posix_publish_roots(
                payload,
                target_root,
                transaction_root,
                txid,
                expected_target_identity=expected_target_identity,
                expected_transaction_identity=created_transaction_identity,
            )
            _ensure_posix_publish_roots_still_named(
                posix_publish_roots,
                target_root,
                transaction_root,
            )
            _release_pinned_target_root()
            publish_target_snapshot = _snapshot_posix_runtime_entries(
                posix_publish_roots,
                target_root,
            )
            _ensure_posix_publish_roots_still_named(
                posix_publish_roots,
                target_root,
                transaction_root,
            )
        else:
            _retain_windows_directory_guard(
                target_root,
                error_code="migration_path_changed",
                message="迁移目标根在 Windows 发布固定期间发生变化。",
                expected_identity=expected_target_identity,
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
            if posix_publish_roots is not None:
                _publish_posix_runtime_entry(
                    posix_publish_roots,
                    target_root,
                    transaction_root,
                    entry_name,
                    publish_target_snapshot.get(entry_name),
                )
                continue
            if path_chain_has_symlink(target_root) or path_chain_has_symlink(transaction_root):
                raise StorageMigrationError(
                    "migration_path_changed",
                    "发布期间目标路径或事务路径的文件系统边界发生变化，已停止迁移。",
                )
            staged_entry = _checked_migration_entry_path(staged_root, entry_name)
            target_entry = _checked_migration_entry_path(target_root, entry_name)
            backup_entry = _checked_migration_entry_path(backup_root, entry_name)
            _retain_windows_relative_parent_guards(
                staged_root,
                entry_name,
                create_missing=False,
                error_code="staging_entry_changed",
            )
            _retain_windows_relative_parent_guards(
                target_root,
                entry_name,
                create_missing=True,
                error_code="target_changed_during_publish",
            )
            _retain_windows_relative_parent_guards(
                backup_root,
                entry_name,
                create_missing=True,
                error_code="staging_entry_changed",
            )
            target_exists = target_entry.exists() or target_entry.is_symlink()
            expected_target_snapshot = publish_target_snapshot.get(entry_name)
            if expected_target_snapshot is None:
                target_unchanged = not target_exists
            else:
                target_unchanged = bool(
                    target_exists
                    and _snapshot_path_within_root(target_root, target_entry)
                    == expected_target_snapshot
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
                _durable_publish_without_replacing(target_entry, backup_entry)
                if (
                    _snapshot_path_within_root(transaction_root, backup_entry)
                    != expected_target_snapshot
                ):
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

        if posix_publish_roots is not None:
            _ensure_posix_publish_roots_still_named(
                posix_publish_roots,
                target_root,
                transaction_root,
            )
        for entry_name, expected_snapshot in source_snapshots.items():
            if posix_publish_roots is not None:
                actual_snapshot = _snapshot_posix_relative_entry(
                    posix_publish_roots.target_root_fd,
                    target_root,
                    entry_name,
                    expected_mount_identity=posix_publish_roots.mount_identity,
                )
            else:
                actual_snapshot = _snapshot_path_within_root(
                    target_root,
                    _checked_migration_entry_path(target_root, entry_name),
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

        if posix_publish_roots is not None:
            _ensure_posix_publish_roots_still_named(
                posix_publish_roots,
                target_root,
                transaction_root,
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
        if posix_publish_roots is not None:
            _ensure_posix_publish_roots_still_named(
                posix_publish_roots,
                target_root,
                transaction_root,
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
        if posix_publish_roots is not None:
            _ensure_posix_publish_roots_still_named(
                posix_publish_roots,
                target_root,
                transaction_root,
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
        _release_windows_directory_guards()
        _release_posix_publish_roots()
        _release_pinned_target_root()
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
            and (
                posix_publish_roots is not None
                or transaction_root.exists()
                or transaction_root.is_symlink()
            )
        ):
            try:
                if publish_started:
                    if (
                        posix_publish_roots is None
                        and not _transaction_root_is_owned(payload, transaction_root, txid)
                    ):
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
                        payload=payload,
                        txid=txid,
                        posix_roots=posix_publish_roots,
                    )
            except Exception as caught_rollback_error:
                rollback_error = caught_rollback_error
                logger.exception("Failed to roll back storage migration target")
        _release_windows_directory_guards()
        _release_posix_publish_roots()
        _release_pinned_target_root()
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
            and (
                posix_publish_roots is not None
                or transaction_root.exists()
                or transaction_root.is_symlink()
            )
        ):
            try:
                if publish_started:
                    if (
                        posix_publish_roots is None
                        and not _transaction_root_is_owned(payload, transaction_root, txid)
                    ):
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
                        payload=payload,
                        txid=txid,
                        posix_roots=posix_publish_roots,
                    )
            except Exception as caught_rollback_error:
                rollback_error = caught_rollback_error
                logger.exception("Failed to roll back unexpected storage migration failure")
        _release_windows_directory_guards()
        _release_posix_publish_roots()
        _release_pinned_target_root()
        if rollback_error is not None:
            return _finish_failure(
                "rollback_failed",
                f"迁移发生未预期错误且目标回滚未完成: {rollback_error}",
                rollback_required=True,
            )
        wrapped_exc = StorageMigrationError("storage_migration_unexpected", f"执行存储迁移时发生未预期错误: {exc}")
        return _finish_rolled_back_failure(wrapped_exc.error_code, wrapped_exc.message)
    finally:
        # Crash-simulation tests raise BaseException in-process; production
        # process loss also relies on the OS to release these handles. Keep the
        # in-process semantics equivalent so a retry can recover immediately.
        _release_windows_directory_guards()
        _release_posix_publish_roots()
        _release_pinned_target_root()


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
