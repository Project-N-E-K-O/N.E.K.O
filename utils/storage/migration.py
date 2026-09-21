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

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json, fsync_directory_best_effort, read_json
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
    path_is_within,
    paths_equal,
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
    }
)
KNOWN_STORAGE_MIGRATION_STATUSES = ACTIVE_STORAGE_MIGRATION_STATUSES | {
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
}

MIGRATED_RUNTIME_ENTRY_NAMES = RUNTIME_STORAGE_RELATIVE_PATHS

_checkpoint_lock = threading.RLock()


@contextmanager
def storage_migration_checkpoint_transaction():
    """Serialize checkpoint read-modify-write sequences inside this process."""

    with _checkpoint_lock:
        yield


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
    return path_is_within(child, parent) and not paths_equal(parent, child)


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
            return any((normalized_retained_root / name).exists() for name in MIGRATED_RUNTIME_ENTRY_NAMES)
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
        shutil.rmtree(path)
    else:
        path.unlink()
    fsync_directory_best_effort(path.parent)


def _durable_replace(source: Path, target: Path) -> None:
    """Rename and flush both directory-entry sides where supported."""

    os.replace(source, target)
    fsync_directory_best_effort(source.parent)
    if source.parent != target.parent:
        fsync_directory_best_effort(target.parent)


def _copy_staged_file(source: Path | str, target: Path | str) -> str:
    """Copy and flush file contents before restoring source metadata."""

    source_path = Path(source)
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("rb") as source_handle, target_path.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    shutil.copystat(source_path, target_path, follow_symlinks=False)
    return str(target_path)


def _merge_runtime_entry(source_path: Path, target_path: Path) -> None:
    """Overlay one legacy/current entry into the staged target."""

    _snapshot_path(source_path)
    if source_path.is_dir():
        if target_path.exists() and not target_path.is_dir():
            _remove_existing_path(target_path)
        shutil.copytree(
            source_path,
            target_path,
            copy_function=_copy_staged_file,
            dirs_exist_ok=True,
            symlinks=True,
        )
        return
    if source_path.is_file():
        if target_path.exists() and target_path.is_dir():
            _remove_existing_path(target_path)
        _copy_staged_file(source_path, target_path)
        return
    raise StorageMigrationError("source_entry_missing", f"迁移源条目不存在: {source_path}")


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

    atomic_write_json(workshop_config_path, rewritten_payload, ensure_ascii=False, indent=2)


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total_bytes = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            digest.update(chunk)
    return total_bytes, digest.hexdigest()


def _snapshot_path(path: Path) -> dict[str, int | str]:
    if path.is_symlink():
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
            if current_dir.is_symlink():
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"迁移校验不支持符号链接: {current_dir}",
                )
            manifest_digest.update(
                b"D\0" + (relative_root / dirname).as_posix().encode("utf-8") + b"\0"
            )
        for filename in filenames:
            current_file = Path(current_root) / filename
            if current_file.is_symlink():
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
            f"迁移运行时条目越出存储根目录: {exc}",
        ) from exc


def validate_storage_migration_preflight_boundaries(
    source_root: Path | str,
    target_root: Path | str,
) -> None:
    """Validate only the canonical relative entry boundaries."""

    for root in (normalize_runtime_root(source_root), normalize_runtime_root(target_root)):
        for entry in RUNTIME_STORAGE_ENTRIES:
            _checked_migration_entry_path(root, entry)


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
    safe_name = target_root.name or "root"
    return target_root.parent / f".{safe_name}.neko-storage-migration-{txid}"


def _rollback_published_entries(
    target_root: Path,
    transaction_root: Path,
    original_target_entries: list[str],
    publish_entry_names: list[str],
    target_baseline: dict[str, dict[str, int | str]],
    publish_entry_snapshots: dict[str, dict[str, int | str]],
) -> None:
    if target_root.is_symlink() or transaction_root.is_symlink():
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
                elif (
                    not target_exists
                    or _snapshot_path(target_path) != publish_entry_snapshots[relative_path]
                ):
                    raise StorageMigrationError(
                        "rollback_target_changed",
                        f"迁移已发布数据被改写或缺失，无法安全覆盖: {relative_path}",
                    )
                target_path = _checked_migration_entry_path(target_root, relative_path)
                backup_path = _checked_migration_entry_path(backup_root, relative_path)
                _remove_existing_path(target_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                fsync_directory_best_effort(target_path.parent.parent)
                _durable_replace(backup_path, target_path)
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
        elif target_exists:
            # No original entry and no staged entry means publication moved the
            # staged copy into place.  Prove it is still exactly our copy before
            # deleting it; otherwise a concurrent writer would be erased.
            if _snapshot_path(target_path) != publish_entry_snapshots[relative_path]:
                raise StorageMigrationError(
                    "rollback_target_changed",
                    f"迁移已发布数据被并发改写，无法安全删除: {relative_path}",
                )
            target_path = _checked_migration_entry_path(target_root, relative_path)
            _remove_existing_path(target_path)

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
                if current_dir.is_symlink():
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"迁移暂存区包含符号链接: {current_dir}",
                    )
            for filename in filenames:
                staged_file = Path(current_root) / filename
                if staged_file.is_symlink():
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"迁移暂存区包含符号链接: {staged_file}",
                    )
                paths.append(staged_file)

    # Windows cannot FlushFileBuffers through the read-only handles used by
    # ``open('rb')``. _copy_staged_file already flushed every destination while
    # it was writable; only POSIX needs the second flush after copystat.
    if os.name != "nt":
        for staged_file in paths:
            try:
                with staged_file.open("rb") as handle:
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise StorageMigrationError(
                    "target_flush_failed",
                    f"迁移数据无法可靠写入目标磁盘: {staged_file}: {exc}",
                ) from exc
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
    target_existed = target_root.exists()
    target_root.mkdir(parents=True, exist_ok=True)
    if not target_existed:
        fsync_directory_best_effort(target_root.parent)
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
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    try:
        payload = read_json(migration_path)
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

    version = payload.get("version")
    status = payload.get("status")
    txid = payload.get("txid")
    source_root = payload.get("source_root")
    target_root = payload.get("target_root")
    selection_source = payload.get("selection_source")
    migration_mode = payload.get("migration_mode")
    confirmed = payload.get("confirmed_existing_target_content")
    additional_sources = payload.get("additional_source_roots", [])
    valid = (
        not isinstance(version, bool)
        and version in {1, STORAGE_MIGRATION_VERSION}
        and isinstance(status, str)
        and status in KNOWN_STORAGE_MIGRATION_STATUSES
        and isinstance(txid, str)
        and re.fullmatch(r"[0-9a-fA-F]{32}", txid) is not None
        and isinstance(source_root, str)
        and bool(source_root.strip())
        and Path(source_root).expanduser().is_absolute()
        and isinstance(target_root, str)
        and bool(target_root.strip())
        and Path(target_root).expanduser().is_absolute()
        and isinstance(selection_source, str)
        and bool(selection_source.strip())
        and isinstance(confirmed, bool)
        and isinstance(additional_sources, list)
        and all(
            isinstance(value, str)
            and bool(value.strip())
            and Path(value).expanduser().is_absolute()
            for value in additional_sources
        )
        and (
            migration_mode == STORAGE_MIGRATION_MODE_COPY
            if version == STORAGE_MIGRATION_VERSION
            else migration_mode in {None, STORAGE_MIGRATION_MODE_COPY}
        )
    )
    if not valid:
        raise StorageMigrationError(
            "migration_checkpoint_malformed",
            f"存储迁移检查点字段无效，无法安全判断迁移状态: {migration_path}",
        )
    if version == STORAGE_MIGRATION_VERSION and not isinstance(
        payload.get("target_baseline"), dict
    ):
        raise StorageMigrationError(
            "migration_checkpoint_malformed",
            f"存储迁移检查点缺少目标基线: {migration_path}",
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
    additional_source_roots: list[Path | str] | tuple[Path | str, ...] = (),
    txid: str | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now_iso()
    normalized_target_root = normalize_runtime_root(target_root)
    return {
        "version": STORAGE_MIGRATION_VERSION,
        "txid": str(txid or uuid.uuid4().hex),
        "status": STORAGE_MIGRATION_STATUS_PENDING,
        "source_root": str(normalize_runtime_root(source_root)),
        "additional_source_roots": [
            str(normalize_runtime_root(value))
            for value in additional_source_roots
            if str(value or "").strip()
        ],
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
    return payload


def replace_storage_migration_if_unchanged(
    config_manager,
    expected_payload: dict[str, Any] | None,
    replacement_payload: dict[str, Any],
    *,
    anchor_root: Path | str | None = None,
) -> dict[str, Any]:
    with storage_migration_checkpoint_transaction():
        if load_storage_migration(config_manager, anchor_root=anchor_root) != expected_payload:
            raise StorageMigrationError(
                "migration_checkpoint_conflict",
                "存储迁移检查点已被另一项操作更新，已停止覆盖。",
            )
        return save_storage_migration(
            config_manager,
            replacement_payload,
            anchor_root=anchor_root,
        )


def delete_storage_migration_if_unchanged(
    config_manager,
    expected_payload: dict[str, Any],
    *,
    anchor_root: Path | str | None = None,
) -> None:
    with storage_migration_checkpoint_transaction():
        if load_storage_migration(config_manager, anchor_root=anchor_root) != expected_payload:
            raise StorageMigrationError(
                "migration_checkpoint_conflict",
                "存储迁移检查点已被另一项操作更新，已停止删除。",
            )
        delete_storage_migration(config_manager, anchor_root=anchor_root)


def create_pending_storage_migration(
    config_manager,
    *,
    source_root: Path | str,
    target_root: Path | str,
    selection_source: str,
    anchor_root: Path | str | None = None,
    backup_root: Path | str | None = None,
    confirmed_existing_target_content: bool = False,
    additional_source_roots: list[Path | str] | tuple[Path | str, ...] = (),
) -> dict[str, Any]:
    payload = build_pending_storage_migration_payload(
        source_root=source_root,
        target_root=target_root,
        selection_source=selection_source,
        backup_root=backup_root,
        confirmed_existing_target_content=confirmed_existing_target_content,
        additional_source_roots=additional_source_roots,
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
    if not is_storage_migration_pending(migration_payload):
        if isinstance(migration_payload, dict):
            try:
                completed_target = normalize_runtime_root(
                    str(migration_payload.get("target_root") or "").strip()
                )
                completed_txid = _validate_txid(migration_payload.get("txid"))
                completed_transaction_root = _transaction_root_for(completed_target, completed_txid)
                if (
                    str(migration_payload.get("status") or "").strip()
                    == STORAGE_MIGRATION_STATUS_COMPLETED
                    and completed_transaction_root.exists()
                ):
                    _remove_existing_path(completed_transaction_root)
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
    original_target_entries: list[str] = []
    publish_entry_names: list[str] = []
    source_snapshots: dict[str, dict[str, int | str]] = {}
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
            from utils.cloudsave_runtime import (
                ROOT_MODE_DEFERRED_INIT,
                ROOT_MODE_NORMAL,
                set_root_mode,
            )

            set_root_mode(
                config_manager,
                ROOT_MODE_DEFERRED_INIT if rollback_required else ROOT_MODE_NORMAL,
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
        next_payload.update(
            {
                "status": (
                    STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
                    if rollback_required
                    else STORAGE_MIGRATION_STATUS_FAILED
                ),
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
            "force_recovery_layout": rollback_required or not recovery_metadata_persisted,
        }

    def _finish_rolled_back_failure(
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        result = _finish_failure(error_code, error_message)
        if transaction_root is not None and bool(result.get("recovery_checkpoint_persisted")):
            try:
                _remove_existing_path(transaction_root)
            except Exception as cleanup_exc:
                logger.warning("Failed to clean rolled-back migration transaction: %s", cleanup_exc)
        return result

    try:
        source_root = normalize_runtime_root(str(payload.get("source_root") or "").strip())
        target_root = normalize_runtime_root(str(payload.get("target_root") or "").strip())
        additional_source_roots: list[Path] = []
        for value in payload.get("additional_source_roots", []):
            candidate = normalize_runtime_root(value)
            if not candidate.is_dir():
                continue
            if paths_equal(candidate, source_root) or paths_equal(candidate, target_root):
                continue
            if any(paths_equal(candidate, existing) for existing in additional_source_roots):
                continue
            additional_source_roots.append(candidate)
        selection_source = _normalize_selection_source(str(payload.get("selection_source") or ""))
        migration_mode = str(payload.get("migration_mode") or STORAGE_MIGRATION_MODE_COPY).strip()
        if migration_mode != STORAGE_MIGRATION_MODE_COPY:
            raise StorageMigrationError("invalid_migration_mode", "存储迁移检查点包含不支持的迁移模式。")
        txid = _validate_txid(payload.get("txid"))
        transaction_root = _transaction_root_for(target_root, txid)
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
            raise StorageMigrationError("source_root_missing", "原存储目录不存在，无法继续迁移。")

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
            _remove_existing_path(transaction_root)
            publish_started = False

        if not isinstance(target_baseline, dict):
            target_baseline = _snapshot_runtime_entries(target_root)
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_PREFLIGHT,
            version=STORAGE_MIGRATION_VERSION,
            migration_mode=STORAGE_MIGRATION_MODE_COPY,
            target_baseline=target_baseline,
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

        source_roots = [*additional_source_roots, source_root]
        source_entry_snapshots: dict[tuple[str, str], dict[str, int | str]] = {}
        existing_entries: list[str] = []
        for candidate_root in source_roots:
            for entry_name in _iter_existing_runtime_entries(candidate_root):
                if entry_name not in existing_entries:
                    existing_entries.append(entry_name)
                source_entry_snapshots[(str(candidate_root), entry_name)] = _snapshot_path(
                    _checked_migration_entry_path(candidate_root, entry_name)
                )
        retained_source_root = next(
            (
                candidate_root
                for candidate_root in additional_source_roots
                if any(
                    root_name == str(candidate_root)
                    for root_name, _entry_name in source_entry_snapshots
                )
            ),
            source_root,
        )
        required_bytes = sum(
            int(snapshot.get("total_bytes") or 0)
            for snapshot in source_entry_snapshots.values()
        )
        safety_margin_bytes = max(64 * 1024 * 1024, int(required_bytes * 0.05)) if required_bytes else 0
        try:
            target_free_bytes = int(shutil.disk_usage(str(target_root.parent)).free)
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

        transaction_root.mkdir(parents=False, exist_ok=False)
        fsync_directory_best_effort(transaction_root.parent)
        staged_root = transaction_root / "staged"
        backup_root = transaction_root / "backup"
        staged_root.mkdir()
        backup_root.mkdir()
        fsync_directory_best_effort(transaction_root)

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_COPYING,
            transaction_root=str(transaction_root),
        )
        for candidate_root in source_roots:
            for entry_name in _iter_existing_runtime_entries(candidate_root):
                _merge_runtime_entry(
                    _checked_migration_entry_path(candidate_root, entry_name),
                    _checked_migration_entry_path(staged_root, entry_name),
                )

        source_snapshots = {
            entry_name: _snapshot_path(
                _checked_migration_entry_path(staged_root, entry_name)
            )
            for entry_name in existing_entries
        }

        _rewrite_migrated_runtime_config_paths(
            source_root=source_root,
            content_root=staged_root,
            target_root=target_root,
        )
        if "config" in source_snapshots:
            source_snapshots["config"] = _snapshot_path(
                _checked_migration_entry_path(staged_root, "config")
            )
        for entry_name in existing_entries:
            _fsync_staged_tree(_checked_migration_entry_path(staged_root, entry_name))

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_VERIFYING,
            backup_root=str(source_root),
        )

        if _snapshot_runtime_entries(target_root) != target_baseline:
            raise StorageMigrationError(
                "target_changed_since_confirmation",
                "目标路径中的数据在迁移期间发生了变化，已停止迁移以避免覆盖新数据。",
            )

        original_target_entries = list(_snapshot_runtime_entries(target_root))
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
            staged_entry = _checked_migration_entry_path(staged_root, entry_name)
            target_entry = _checked_migration_entry_path(target_root, entry_name)
            backup_entry = _checked_migration_entry_path(backup_root, entry_name)
            if target_entry.exists() or target_entry.is_symlink():
                target_entry = _checked_migration_entry_path(target_root, entry_name)
                backup_entry = _checked_migration_entry_path(backup_root, entry_name)
                backup_entry.parent.mkdir(parents=True, exist_ok=True)
                fsync_directory_best_effort(backup_entry.parent.parent)
                _durable_replace(target_entry, backup_entry)
            target_entry = _checked_migration_entry_path(target_root, entry_name)
            target_entry.parent.mkdir(parents=True, exist_ok=True)
            fsync_directory_best_effort(target_entry.parent.parent)
            staged_entry = _checked_migration_entry_path(staged_root, entry_name)
            _durable_replace(staged_entry, target_entry)

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
            retained_source_root,
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
            last_migration_backup=str(retained_source_root),
            legacy_cleanup_pending=legacy_cleanup_pending,
        )

        completed_at = _utc_now_iso()
        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_COMPLETED,
            backup_root=str(retained_source_root),
            retained_source_root=str(retained_source_root),
            retained_source_mode="manual_retention",
            error_code="",
            error_message="",
            committed_at=completed_at,
            completed_at=completed_at,
        )
        try:
            _remove_existing_path(transaction_root)
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


def storage_migration_retains_recovery_evidence(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    return str(payload.get("status") or "").strip() in {
        STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
        STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
    }
