from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json, read_json
from utils.logger_config import get_module_logger
from utils.storage_policy import (
    POLICY_SELECTION_SOURCE_RECOVERED,
    compute_anchor_root,
    normalize_runtime_root,
    paths_equal,
    save_storage_policy,
)
from utils.storage_path_rewrite import rebase_runtime_bound_workshop_config_paths

logger = get_module_logger(__name__)

STORAGE_MIGRATION_VERSION = 1

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
    "character_cards",
    "jukebox",
)


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
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


def _copy_runtime_entry(source_path: Path, target_path: Path) -> None:
    if source_path.is_symlink():
        raise StorageMigrationError("source_symlink_unsupported", "迁移源目录包含符号链接，当前阶段暂不自动迁移。")

    _remove_existing_path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if source_path.is_dir():
        shutil.copytree(source_path, target_path, copy_function=shutil.copy2)
        return

    if source_path.is_file():
        shutil.copy2(source_path, target_path)
        return

    raise StorageMigrationError("source_entry_missing", f"迁移源条目不存在: {source_path}")


def _rewrite_migrated_runtime_config_paths(*, source_root: Path, target_root: Path) -> None:
    workshop_config_path = target_root / "config" / "workshop_config.json"
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
    if not path.exists():
        return {"kind": "missing", "file_count": 0, "total_bytes": 0}
    if path.is_symlink():
        raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {path}")
    if path.is_file():
        return {
            "kind": "file",
            "file_count": 1,
            "total_bytes": int(path.stat().st_size),
        }

    total_bytes = 0
    file_count = 0
    for current_root, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if not Path(current_root, name).is_symlink()]
        for filename in filenames:
            current_file = Path(current_root) / filename
            if current_file.is_symlink():
                raise StorageMigrationError("path_symlink_unsupported", f"迁移校验不支持符号链接: {current_file}")
            total_bytes += int(current_file.stat().st_size)
            file_count += 1

    return {
        "kind": "dir",
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _iter_existing_runtime_entries(root: Path) -> list[str]:
    return [name for name in MIGRATED_RUNTIME_ENTRY_NAMES if (root / name).exists()]


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

    try:
        source_root = normalize_runtime_root(str(payload.get("source_root") or "").strip())
        target_root = normalize_runtime_root(str(payload.get("target_root") or "").strip())
        selection_source = _normalize_selection_source(str(payload.get("selection_source") or ""))

        if paths_equal(source_root, target_root):
            raise StorageMigrationError("target_matches_source", "目标路径与当前路径一致，不需要执行迁移。")
        if _path_contains(source_root, target_root) or _path_contains(target_root, source_root):
            raise StorageMigrationError("paths_nested", "源路径和目标路径不能互相包含，无法安全执行迁移。")
        if not source_root.exists() or not source_root.is_dir():
            raise StorageMigrationError("source_root_missing", "原始数据目录不存在，无法继续迁移。")

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

        target_has_user_content = _root_has_user_content(target_root, config_manager=config_manager)
        use_existing_target = target_has_user_content and selection_source in {
            "legacy",
            POLICY_SELECTION_SOURCE_RECOVERED,
        }
        confirmed_existing_target_content = bool(payload.get("confirmed_existing_target_content"))

        if target_has_user_content and not use_existing_target and not confirmed_existing_target_content:
            raise StorageMigrationError(
                "target_confirmation_required",
                "目标路径已经包含现有数据，需要先确认覆盖目标中的同名运行时数据目录。",
            )

        _ensure_target_root_writable(target_root)

        source_snapshots: dict[str, dict[str, int | str]] = {}
        existing_entries = _iter_existing_runtime_entries(source_root)

        if not use_existing_target:
            payload = _persist_migration_payload(
                config_manager,
                payload,
                anchor_root=normalized_anchor_root,
                status=STORAGE_MIGRATION_STATUS_COPYING,
            )
            for entry_name in existing_entries:
                source_entry = source_root / entry_name
                target_entry = target_root / entry_name
                source_snapshots[entry_name] = _snapshot_path(source_entry)
                _copy_runtime_entry(source_entry, target_entry)
            _rewrite_migrated_runtime_config_paths(source_root=source_root, target_root=target_root)
            if "config" in source_snapshots:
                source_snapshots["config"] = _snapshot_path(target_root / "config")

        payload = _persist_migration_payload(
            config_manager,
            payload,
            anchor_root=normalized_anchor_root,
            status=STORAGE_MIGRATION_STATUS_VERIFYING,
            backup_root=str(source_root),
        )

        if use_existing_target:
            if not _root_has_user_content(target_root, config_manager=config_manager):
                raise StorageMigrationError("target_missing_runtime", "目标路径没有可用数据，无法直接切换到现有目录。")
        else:
            for entry_name, expected_snapshot in source_snapshots.items():
                actual_snapshot = _snapshot_path(target_root / entry_name)
                if actual_snapshot != expected_snapshot:
                    logger.warning(
                        "Storage migration verification failed for %s: expected=%s actual=%s",
                        entry_name,
                        expected_snapshot,
                        actual_snapshot,
                    )
                    raise StorageMigrationError(
                        "verification_failed",
                        f"迁移校验失败：{entry_name} 未完整复制到目标路径。",
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

        try:
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
        except Exception as exc:
            logger.warning("Failed to persist successful storage migration root_state: %s", exc)

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
        return _finish_failure(exc.error_code, exc.message)
    except Exception as exc:
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
