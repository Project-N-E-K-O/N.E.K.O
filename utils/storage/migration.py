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
import shutil
import sqlite3
import stat
import tempfile
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json, read_json
from utils.logger_config import get_module_logger
from .knowledge_contract import assert_supported_schema, knowledge_root_barrier
from .policy import (
    POLICY_SELECTION_SOURCE_RECOVERED,
    compute_anchor_root,
    normalize_runtime_root,
    paths_equal,
    load_storage_policy,
    save_storage_policy,
)
from .path_rewrite import rebase_runtime_bound_workshop_config_paths

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
STORAGE_MIGRATION_STATUS_KNOWLEDGE_REPAIR_REQUIRED = (
    "knowledge_migration_repair_required"
)

ACTIVE_STORAGE_MIGRATION_STATUSES = frozenset(
    {
        STORAGE_MIGRATION_STATUS_PENDING,
        STORAGE_MIGRATION_STATUS_PREFLIGHT,
        STORAGE_MIGRATION_STATUS_COPYING,
        STORAGE_MIGRATION_STATUS_VERIFYING,
        STORAGE_MIGRATION_STATUS_COMMITTING,
        STORAGE_MIGRATION_STATUS_RETAINING_SOURCE,
        STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
        STORAGE_MIGRATION_STATUS_KNOWLEDGE_REPAIR_REQUIRED,
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
    "character_cards",
    "card_faces",
    "jukebox",
    "knowledge",
    "avatar_tools",
)

_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
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
        return any((normalized_retained_root / name).exists() for name in MIGRATED_RUNTIME_ENTRY_NAMES)
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
        shutil.rmtree(path)
        return
    if stat.S_ISREG(path_stat.st_mode):
        path.unlink()
        return
    raise StorageMigrationError(
        "target_special_file_unsupported",
        f"迁移目标不是普通文件或目录: {path}",
    )


def _stat_is_reparse(path_stat: os.stat_result) -> bool:
    return bool(
        int(getattr(path_stat, "st_file_attributes", 0))
        & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    )


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
        if not stat.S_ISREG(opened.st_mode) or _stat_is_reparse(opened):
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
    total_bytes = 0
    file_count = 0
    if kind == "file":
        size, digest = _hash_regular_file(path)
        records.append(("", "file", size, digest))
        total_bytes = size
        file_count = 1
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
                    total_bytes += size
                    file_count += 1
    records.sort(key=lambda record: (record[0], record[1]))
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "kind": kind,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "manifest_digest": hashlib.sha256(encoded).hexdigest(),
    }


def _copy_regular_file_no_follow(source_path: Path, target_path: Path) -> None:
    _classify_no_follow(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_fd = os.open(source_path, flags)
    try:
        opened = os.fstat(source_fd)
        if not stat.S_ISREG(opened.st_mode) or _stat_is_reparse(opened):
            raise StorageMigrationError(
                "path_not_file",
                f"迁移源对象不是普通文件: {source_path}",
            )
        with os.fdopen(os.dup(source_fd), "rb") as source_stream:
            with target_path.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
        shutil.copystat(source_path, target_path, follow_symlinks=False)
    finally:
        os.close(source_fd)


def _copy_runtime_entry(source_path: Path, target_path: Path) -> None:
    _remove_existing_path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    kind, _source_stat = _classify_no_follow(source_path)
    if kind == "file":
        _copy_regular_file_no_follow(source_path, target_path)
        return
    target_path.mkdir()
    pending = [(source_path, target_path)]
    while pending:
        source_dir, target_dir = pending.pop()
        with os.scandir(source_dir) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
        for child in children:
            child_source = Path(child.path)
            child_target = target_dir / child.name
            child_kind, _child_stat = _classify_no_follow(child_source)
            if child_kind == "dir":
                child_target.mkdir()
                pending.append((child_source, child_target))
            else:
                _copy_regular_file_no_follow(child_source, child_target)


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

    atomic_write_json(workshop_config_path, rewritten_payload, ensure_ascii=False, indent=2)


def _snapshot_path(path: Path) -> dict[str, int | str]:
    if not os.path.lexists(path):
        return {
            "kind": "missing",
            "file_count": 0,
            "total_bytes": 0,
            "manifest_digest": "",
        }
    return _manifest_path(path)


def _verify_knowledge_database(knowledge_root: Path) -> None:
    database_path = knowledge_root / "knowledge.db"
    if not database_path.is_file():
        return
    connection: sqlite3.Connection | None = None
    # Verify a throwaway copy, never the tree being migrated. Opening a WAL
    # database -- every KnowledgeStore writer switches it to WAL -- creates
    # -wal/-shm sidecars that a read-only connection cannot remove on close, so
    # checking in place changed the directory whose manifest proves the copy.
    scratch = tempfile.TemporaryDirectory(prefix="neko-knowledge-verify-")
    try:
        scratch_path = Path(scratch.name) / database_path.name
        shutil.copy2(database_path, scratch_path)
        for suffix in ("-wal", "-shm"):
            sidecar = database_path.with_name(database_path.name + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, scratch_path.with_name(scratch_path.name + suffix))
        connection = sqlite3.connect(scratch_path)
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        quick_check_result = (
            str(quick_check[0]) if quick_check and quick_check[0] is not None else ""
        )
        if quick_check_result.lower() != "ok":
            raise StorageMigrationError(
                "knowledge_integrity_check_failed",
                f"知识库完整性校验失败: {quick_check_result or 'unknown'}",
            )
        assert_supported_schema(connection)
    except StorageMigrationError:
        raise
    except Exception as exc:
        raise StorageMigrationError(
            "knowledge_database_invalid",
            f"知识库只读校验失败: {exc}",
        ) from exc
    finally:
        if connection is not None:
            connection.close()
        scratch.cleanup()


def _transaction_path(target_root: Path, txid: str) -> Path:
    # Only one migration runs per target at a time, so a txid prefix is enough
    # to keep an interrupted transaction apart from the next one.
    return target_root / _MIGRATION_TRANSACTION_DIR / txid[:_TRANSACTION_ID_PATH_CHARS]


def _rollback_interrupted_publish(
    *,
    payload: dict[str, Any],
    target_root: Path,
    transaction_root: Path,
) -> None:
    """Restore the exact pre-publish target state recorded by the checkpoint."""
    backup_root = transaction_root / "backup"
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

        if target_existed and os.path.lexists(backup_entry):
            _remove_existing_path(target_entry)
            os.replace(backup_entry, target_entry)
            continue
        if target_existed:
            if was_published:
                raise StorageMigrationError(
                    "migration_rollback_required",
                    f"迁移事务缺少目标备份，拒绝继续: {entry_name}",
                )
            # The checkpoint can precede the first replace. With no backup, the
            # target is still the original and must remain untouched.
            continue
        _remove_existing_path(target_entry)

    _remove_existing_path(transaction_root)


def _committed_target_matches_checkpoint(
    *,
    payload: dict[str, Any],
    target_root: Path,
) -> bool:
    copied_entries = payload.get("copied_entries")
    if not isinstance(copied_entries, dict) or not copied_entries:
        return False
    for entry_name, proof in copied_entries.items():
        if entry_name not in MIGRATED_RUNTIME_ENTRY_NAMES or not isinstance(proof, dict):
            return False
        target_manifest = proof.get("target_manifest")
        if not isinstance(target_manifest, dict):
            return False
        target_entry = target_root / entry_name
        if not os.path.lexists(target_entry) or _snapshot_path(target_entry) != target_manifest:
            return False
    return True


def _iter_existing_runtime_entries(root: Path) -> list[str]:
    return [
        name
        for name in MIGRATED_RUNTIME_ENTRY_NAMES
        if os.path.lexists(root / name)
    ]


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


def is_storage_migration_pending(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False

    status = str(payload.get("status") or "").strip().lower()
    if status == STORAGE_MIGRATION_STATUS_COMPLETED:
        try:
            version = int(payload.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        raw_source_root = str(payload.get("source_root") or "").strip()
        source_root = Path(raw_source_root) if raw_source_root else None
        copied_entries = payload.get("copied_entries")
        if (
            version < STORAGE_MIGRATION_VERSION
            and source_root is not None
            and (source_root / "knowledge").exists()
            and not (
                isinstance(copied_entries, dict)
                and isinstance(copied_entries.get("knowledge"), dict)
            )
        ):
            return True
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
        "repairing_v1_knowledge": False,
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
    barrier_stack = ExitStack()
    knowledge_barriers_entered = False

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
            "knowledge_migration_repair_required": (
                STORAGE_MIGRATION_STATUS_KNOWLEDGE_REPAIR_REQUIRED
            ),
            "migration_rollback_required": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
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

    def _enter_knowledge_barriers() -> None:
        nonlocal knowledge_barriers_entered
        if knowledge_barriers_entered:
            return
        assert source_root is not None and target_root is not None
        try:
            for knowledge_root in sorted(
                {source_root / "knowledge", target_root / "knowledge"},
                key=lambda item: os.path.normcase(str(item.resolve(strict=False))),
            ):
                barrier_stack.enter_context(knowledge_root_barrier(knowledge_root))
        except Exception as exc:
            raise StorageMigrationError(
                "knowledge_mutation_busy",
                "知识库仍有写入或迁移锁被占用，请稍后重试。",
            ) from exc
        knowledge_barriers_entered = True

    def _finish_success(
        *,
        copied_entries: dict[str, dict[str, Any]],
        transaction_root: Path,
        persist_policy: bool,
        selection_source: str,
    ) -> dict[str, Any]:
        nonlocal payload, policy_payload
        assert source_root is not None and target_root is not None
        if persist_policy:
            try:
                policy_payload = save_storage_policy(
                    config_manager,
                    selected_root=target_root,
                    selection_source=selection_source,
                    anchor_root=normalized_anchor_root,
                )
            except Exception as exc:
                try:
                    _rollback_interrupted_publish(
                        payload=payload,
                        target_root=target_root,
                        transaction_root=transaction_root,
                    )
                except Exception as rollback_exc:
                    raise StorageMigrationError(
                        "migration_rollback_required",
                        f"存储策略提交失败且目标回滚未完成: {rollback_exc}",
                    ) from rollback_exc
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
            from utils.cloudsave_runtime import ROOT_MODE_NORMAL, set_root_mode

            legacy_cleanup_pending = bool(copied_entries) and is_retained_root_cleanup_available(
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
                version=STORAGE_MIGRATION_VERSION,
                copied_entries=copied_entries,
                published_entries=[],
                original_target_entries=[],
                publishing_entry="",
                publishing_target_existed=False,
                repairing_v1_knowledge=False,
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
        try:
            _remove_existing_path(transaction_root)
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
            _remove_existing_path(transaction_root)
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
        repairing_v1_knowledge = (
            bool(payload.get("repairing_v1_knowledge"))
            or (
                checkpoint_status == STORAGE_MIGRATION_STATUS_COMPLETED
                and checkpoint_version < STORAGE_MIGRATION_VERSION
                and (source_root / "knowledge").exists()
            )
        )

        if paths_equal(source_root, target_root):
            raise StorageMigrationError("target_matches_source", "目标路径与当前路径一致，不需要执行迁移。")
        if _path_contains(source_root, target_root) or _path_contains(target_root, source_root):
            raise StorageMigrationError("paths_nested", "源路径和目标路径不能互相包含，无法安全执行迁移。")
        if not source_root.exists() or not source_root.is_dir():
            raise StorageMigrationError("source_root_missing", "原始数据目录不存在，无法继续迁移。")

        transaction_root = _transaction_path(
            target_root,
            str(payload.get("txid") or uuid.uuid4().hex),
        )
        interrupted_entries = {
            str(entry) for entry in payload.get("published_entries") or []
        }
        interrupted_entries.add(str(payload.get("publishing_entry") or ""))
        interrupted_entries.update(
            str(entry) for entry in (payload.get("copied_entries") or {})
        )
        if (
            "knowledge" in interrupted_entries
            and checkpoint_status
            in {
                STORAGE_MIGRATION_STATUS_VERIFYING,
                STORAGE_MIGRATION_STATUS_COMMITTING,
            }
        ):
            _enter_knowledge_barriers()
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
            and _committed_target_matches_checkpoint(
                payload=payload,
                target_root=target_root,
            )
        ):
            return _finish_success(
                copied_entries=dict(copied_checkpoint),
                transaction_root=transaction_root,
                persist_policy=False,
                selection_source=selection_source,
            )
        if transaction_root.exists():
            _rollback_interrupted_publish(
                payload=payload,
                target_root=target_root,
                transaction_root=transaction_root,
            )
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                status=STORAGE_MIGRATION_STATUS_PENDING,
                copied_entries={},
                published_entries=[],
                original_target_entries=[],
                publishing_entry="",
                publishing_target_existed=False,
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
            repairing_v1_knowledge=repairing_v1_knowledge,
            error_code="",
            error_message="",
        )

        target_has_user_content = _root_has_user_content(target_root, config_manager=config_manager)
        use_existing_target = repairing_v1_knowledge or (
            target_has_user_content
            and selection_source in {"legacy", POLICY_SELECTION_SOURCE_RECOVERED}
        )
        confirmed_existing_target_content = bool(payload.get("confirmed_existing_target_content"))

        if target_has_user_content and not use_existing_target and not confirmed_existing_target_content:
            raise StorageMigrationError(
                "target_confirmation_required",
                "目标路径已经包含现有数据，需要先确认覆盖目标中的同名运行时数据目录。",
            )

        _ensure_target_root_writable(target_root)

        source_snapshots: dict[str, dict[str, int | str]] = {}
        existing_entries = (
            ["knowledge"]
            if repairing_v1_knowledge
            else _iter_existing_runtime_entries(source_root)
        )
        if "knowledge" in existing_entries:
            _enter_knowledge_barriers()

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
        copied_entries: dict[str, dict[str, Any]] = {}
        entries_to_publish: list[str] = []
        original_target_entries: list[str] = []
        for entry_name in existing_entries:
            source_entry = source_root / entry_name
            target_entry = target_root / entry_name
            if entry_name == "knowledge":
                _verify_knowledge_database(source_entry)
            source_manifest = _snapshot_path(source_entry)
            source_snapshots[entry_name] = source_manifest
            if use_existing_target and os.path.lexists(target_entry):
                target_manifest = _snapshot_path(target_entry)
                if target_manifest == source_manifest:
                    copied_entries[entry_name] = {
                        "source_manifest": source_manifest,
                        "target_manifest": target_manifest,
                        "transaction": str(payload.get("txid") or ""),
                    }
                    continue
                if repairing_v1_knowledge and entry_name == "knowledge":
                    raise StorageMigrationError(
                        "knowledge_migration_repair_required",
                        "旧迁移的源与目标知识库内容不同，需人工确认后再修复，旧源保持不可清理。",
                    )
                # Existing legacy/recovered entries are authoritative. They do
                # not prove the source copy and therefore are not cleanup-safe.
                continue
            staged_entry = stage_root / entry_name
            _copy_runtime_entry(source_entry, staged_entry)
            if entry_name == "config":
                _rewrite_migrated_runtime_config_paths(
                    source_root=source_root,
                    target_root=target_root,
                    config_root=stage_root,
                )
            staged_manifest = _snapshot_path(staged_entry)
            if entry_name == "knowledge":
                _verify_knowledge_database(staged_entry)
            if entry_name != "config" and staged_manifest != source_manifest:
                raise StorageMigrationError(
                    "verification_failed",
                    f"迁移 staging 校验失败：{entry_name}。",
                )
            staged_manifests[entry_name] = staged_manifest
            entries_to_publish.append(entry_name)
            if os.path.lexists(target_entry):
                original_target_entries.append(entry_name)

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
        try:
            for entry_name in entries_to_publish:
                target_entry = target_root / entry_name
                backup_entry = backup_root / entry_name
                target_existed = os.path.lexists(target_entry)
                payload = _persist_migration_payload(
                    config_manager,
                    payload,
                    anchor_root=normalized_anchor_root,
                    publishing_entry=entry_name,
                    publishing_target_existed=target_existed,
                )
                if target_existed:
                    _classify_no_follow(target_entry)
                    os.replace(target_entry, backup_entry)
                os.replace(stage_root / entry_name, target_entry)
                published_entries.append(entry_name)
                actual_manifest = _snapshot_path(target_entry)
                expected_manifest = staged_manifests[entry_name]
                if actual_manifest != expected_manifest:
                    raise StorageMigrationError(
                        "verification_failed",
                        f"迁移发布校验失败：{entry_name}。",
                    )
                if entry_name == "knowledge":
                    _verify_knowledge_database(target_entry)
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
        except Exception:
            _rollback_interrupted_publish(
                payload=payload,
                target_root=target_root,
                transaction_root=transaction_root,
            )
            raise

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_VERIFYING,
            backup_root=str(source_root),
            copied_entries=copied_entries,
        )

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
            status=STORAGE_MIGRATION_STATUS_COMMITTING,
            committed_at=_utc_now_iso(),
        )
        return _finish_success(
            copied_entries=copied_entries,
            transaction_root=transaction_root,
            persist_policy=True,
            selection_source=selection_source,
        )
    except StorageMigrationError as exc:
        _cleanup_unpublished_transaction()
        if exc.error_code in {
            "knowledge_mutation_busy",
            "knowledge_migration_repair_required",
            "migration_rollback_required",
        }:
            return _finish_retryable(exc.error_code, exc.message)
        return _finish_failure(exc.error_code, exc.message)
    except Exception as exc:
        _cleanup_unpublished_transaction()
        logger.exception("Unexpected storage migration failure")
        wrapped_exc = StorageMigrationError("storage_migration_unexpected", f"执行存储迁移时发生未预期错误: {exc}")
        return _finish_failure(wrapped_exc.error_code, wrapped_exc.message)
    finally:
        barrier_stack.close()


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
