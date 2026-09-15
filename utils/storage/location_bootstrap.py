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

from pathlib import Path
from typing import Any

from utils.cloudsave_runtime import (
    ROOT_MODE_DEFERRED_INIT,
    ROOT_MODE_MAINTENANCE_READONLY,
    cloudsave_disabled_reason,
    is_cloudsave_disabled_due_to_local_state_unavailable,
    runtime_root_has_user_content,
)
from .policy import StoragePolicyError, compute_anchor_root, should_require_storage_selection
from .entries import (
    RUNTIME_STORAGE_ENTRIES,
    RuntimeStorageEntryBoundaryError,
    checked_runtime_entry_path,
)
from .community_private_state import (
    COMMUNITY_PRIVATE_STATE_FILENAMES,
    probe_retained_community_state,
)
from .migration import (
    STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    load_storage_migration,
)
from .layout import get_storage_recovery_mode
from utils.logger_config import get_module_logger
from utils.root_state_lock import root_state_transaction

logger = get_module_logger(__name__)

# 正常模式：
# 仅在真正首次需要选择、存在待迁移检查点或进入恢复态时，才要求网页端阻断并显示存储位置选择流程。
DEVELOPMENT_ALWAYS_REQUIRE_SELECTION = False
STORAGE_LOCATION_STAGE = "stage3_web_restart"
STORAGE_STATUS_POLL_INTERVAL_MS = 1200
STORAGE_STARTUP_BLOCKING_REASONS = frozenset(
    {
        "selection_required",
        "migration_pending",
        "recovery_required",
        "startup_release_failed",
        "storage_policy_unavailable",
        "storage_status_unavailable",
    }
)

# Disk state is the durable authority for storage selection and migration.  One
# failure is intentionally process-local, though: a same-session release may
# fail *after* the validated root was committed and after one child runtime
# already observed it.  Rolling the root back would split storage authority,
# while reporting the now-normal disk state as ready would reopen business
# traffic.  This overlay keeps status/bootstrap/exit aligned with the admission
# gate until a retry succeeds or the process exits.
_runtime_storage_blocking_reason = ""


def set_runtime_storage_blocking_reason(reason: str) -> None:
    global _runtime_storage_blocking_reason
    _runtime_storage_blocking_reason = str(reason or "").strip()


def clear_runtime_storage_blocking_reason() -> None:
    set_runtime_storage_blocking_reason("")


def get_runtime_storage_blocking_reason() -> str:
    return _runtime_storage_blocking_reason


def _normalize_path(value: Path | str) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _collect_legacy_sources(
    config_manager,
    *,
    current_root: Path,
    anchor_root: Path,
    actual_current_root: Path | None = None,
) -> list[str]:
    legacy_sources: list[str] = []
    seen: set[str] = {_normalize_path(current_root), _normalize_path(anchor_root)}
    if actual_current_root is not None:
        seen.add(_normalize_path(actual_current_root))

    try:
        candidates = config_manager.get_legacy_app_root_candidates()
    except Exception as exc:
        logger.warning("Failed to collect legacy storage root candidates: %s", exc)
        return legacy_sources

    for candidate in candidates:
        try:
            path = Path(candidate)
            normalized = _normalize_path(path)
            if normalized in seen:
                continue
            if not runtime_root_has_user_content(path, config_manager=config_manager):
                continue
        except Exception as exc:
            logger.warning("Skipping legacy storage root candidate %r: %s", candidate, exc)
            continue
        seen.add(normalized)
        legacy_sources.append(normalized)

    return legacy_sources


def _extract_last_error(last_migration_result: str) -> str:
    result = (last_migration_result or "").strip()
    lowered = result.lower()
    token = lowered.split(":", 1)[0]
    if token in {"failed", "unavailable", "selected_root_unavailable"}:
        return result
    return ""


def derive_storage_blocking_reason(
    *,
    selection_required: bool,
    migration_pending: bool,
    recovery_required: bool,
) -> str:
    if migration_pending:
        return "migration_pending"
    if recovery_required:
        return "recovery_required"
    if selection_required:
        return "selection_required"
    return ""


def _is_awaiting_controlled_shutdown(
    *,
    root_mode: str,
    last_migration_result: str,
    migration_pending: bool,
) -> bool:
    if root_mode != ROOT_MODE_MAINTENANCE_READONLY:
        return False
    restart_marker = str(last_migration_result or "").strip().startswith(
        ("restart_pending:", "restart_rebind:")
    )
    # The restart marker is itself a durable shutdown intent.  A missing
    # checkpoint can mean the rollback of a failed scheduling attempt also
    # failed; treating that combination as ready would reopen normal writes.
    return restart_marker


def _build_migration_payload(migration_checkpoint: dict[str, Any] | None, last_migration_result: str) -> dict[str, Any]:
    checkpoint = migration_checkpoint if isinstance(migration_checkpoint, dict) else {}
    error_message = str(checkpoint.get("error_message") or "").strip()
    raw_cleanup_private_names = checkpoint.get("cleanup_private_names")
    cleanup_private_names = (
        [
            str(name)
            for name in raw_cleanup_private_names
            if isinstance(name, str) and name in COMMUNITY_PRIVATE_STATE_FILENAMES
        ]
        if isinstance(raw_cleanup_private_names, list)
        else []
    )

    def _opt_normalize(value: Any) -> str:
        raw_value = str(value or "").strip()
        return _normalize_path(raw_value) if raw_value else ""

    return {
        "status": str(checkpoint.get("status") or "").strip(),
        "source_root": _opt_normalize(checkpoint.get("source_root")),
        "target_root": _opt_normalize(checkpoint.get("target_root")),
        "selection_source": str(checkpoint.get("selection_source") or "").strip(),
        "requested_at": str(checkpoint.get("requested_at") or "").strip(),
        "started_at": str(checkpoint.get("started_at") or "").strip(),
        "updated_at": str(checkpoint.get("updated_at") or "").strip(),
        "committed_at": str(checkpoint.get("committed_at") or "").strip(),
        "completed_at": str(checkpoint.get("completed_at") or "").strip(),
        "backup_root": _opt_normalize(checkpoint.get("backup_root")),
        "retained_source_root": _opt_normalize(checkpoint.get("retained_source_root")),
        "retained_source_mode": str(checkpoint.get("retained_source_mode") or "").strip(),
        "cleanup_private_names": cleanup_private_names,
        "error_code": str(checkpoint.get("error_code") or "").strip(),
        "error_message": error_message,
        "last_error": error_message or _extract_last_error(last_migration_result),
    }


def _get_configured_anchor_root(config_manager, *, current_root: Path) -> Path:
    anchor_root = getattr(config_manager, "anchor_root", None)
    if anchor_root:
        return Path(anchor_root).expanduser().resolve(strict=False)
    return compute_anchor_root(config_manager, current_root=current_root)


def _derive_legacy_cleanup_pending(
    *,
    root_state: dict[str, Any],
    migration_payload: dict[str, Any],
    current_root: Path,
    anchor_root: Path,
) -> bool:
    retained_root = str(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or root_state.get("last_migration_backup")
        or ""
    ).strip()
    if not retained_root:
        return False

    retained_path = Path(retained_root).expanduser()
    try:
        retained_path.lstat()
    except FileNotFoundError:
        # Filesystem truth wins over stale cleanup metadata after a successful
        # delete whose checkpoint/root_state update was interrupted.
        return False
    except OSError:
        # Inaccessible is not absent: retain the cleanup intent fail-closed.
        return True

    try:
        has_retained_runtime_entries = any(
            (
                checked_runtime_entry_path(retained_path, entry).exists()
                or checked_runtime_entry_path(retained_path, entry).is_symlink()
            )
            for entry in RUNTIME_STORAGE_ENTRIES
        )
    except RuntimeStorageEntryBoundaryError:
        # Existing unsafe entries are still pending cleanup, but the mutation
        # route will refuse to follow them.
        has_retained_runtime_entries = True
    retained_private_state = probe_retained_community_state(
        retained_path,
        classify_social_lock_process=False,
    )
    retained_mode = str(migration_payload.get("retained_source_mode") or "").strip()
    has_retained_private_state = retained_private_state.has_managed_content
    if retained_mode == "cleanup_in_progress":
        has_retained_private_state = retained_private_state.has_expected_content(
            set(migration_payload.get("cleanup_private_names") or [])
        )
    if not (
        has_retained_runtime_entries
        or has_retained_private_state
    ):
        return False

    if bool(root_state.get("legacy_cleanup_pending")):
        return True
    if str(root_state.get("last_migration_backup") or "").strip():
        return True

    migration_completed = str(migration_payload.get("status") or "").strip() == "completed"
    return bool(migration_completed)


def _reconcile_legacy_cleanup_pending_root_state(
    config_manager,
    *,
    root_state: dict[str, Any],
    derived_legacy_cleanup_pending: bool,
) -> dict[str, Any]:
    if bool(root_state.get("legacy_cleanup_pending")) == bool(derived_legacy_cleanup_pending):
        return root_state

    try:
        with root_state_transaction():
            # 锁内重读一次，别拿调用方手里那份 pre-image 去盖。调用方 load 到这里之间
            # 变更路由可能已经写过一轮 root_state（回滚就是典型），直接 dict(root_state)
            # 会把那一轮整份抹掉——PR #2598 里"回滚被 /status 盖掉"就是这个形状。
            current_state = config_manager.load_root_state()
            if not isinstance(current_state, dict):
                current_state = root_state
            if bool(current_state.get("legacy_cleanup_pending")) == bool(derived_legacy_cleanup_pending):
                return current_state

            updated_root_state = dict(current_state)
            updated_root_state["legacy_cleanup_pending"] = bool(derived_legacy_cleanup_pending)
            config_manager.save_root_state(updated_root_state)
            return updated_root_state
    except Exception as exc:
        logger.warning(
            "_reconcile_legacy_cleanup_pending_root_state: config_manager.save_root_state failed: %s",
            exc,
        )
        return root_state


def _should_require_selection(config_manager, *, current_root: Path, anchor_root: Path) -> bool:
    # 仅保留一个可选的开发调试开关；默认走正式逻辑，
    # 即只在真正首次或恢复态下要求选择。
    if DEVELOPMENT_ALWAYS_REQUIRE_SELECTION:
        return True
    return should_require_storage_selection(
        config_manager,
        current_root=current_root,
        anchor_root=anchor_root,
    )


def _build_storage_location_bootstrap_payload_from_disk(
    config_manager,
    *,
    persist_reconcile: bool = False,
) -> dict[str, Any]:
    """Build the storage-location bootstrap payload.

    ``persist_reconcile`` decides whether a drifted ``legacy_cleanup_pending``
    flag is written back to root_state.json. It defaults to False so that read
    endpoints stay read-only: this payload backs ``GET /bootstrap``,
    ``GET /status`` (the storage page polls it on a 1200ms timer), ``GET /diagnostics``,
    ``GET /retained-source`` and ``POST /exit``, none of which hold
    ``_storage_mutation_lock``. A write from there races the mutation routes'
    rollback and can clobber it wholesale, which is what stopped the offload in
    PR #2598. Only callers already holding that lock pass True.

    The returned payload carries the freshly derived flag either way — turning
    persistence off changes what lands on disk, not what the client sees.
    """
    recovery_mode = get_storage_recovery_mode()
    if recovery_mode in {"storage_policy_unavailable", "storage_status_unavailable"}:
        # The launcher owns this generation-level failure.  A phase-0 failure
        # need not change root_state/policy on disk, so re-deriving readiness
        # from those files can falsely report ready and reject safe exit.
        policy_unavailable = recovery_mode == "storage_policy_unavailable"
        return {
            "current_root": "",
            "recommended_root": "",
            "legacy_sources": [],
            "anchor_root": "",
            "cloudsave_root": "",
            "selection_required": False,
            "migration_pending": False,
            "recovery_required": policy_unavailable,
            "blocking_reason": recovery_mode,
            "legacy_cleanup_pending": False,
            "last_known_good_root": "",
            "last_error_summary": (
                "无法安全读取存储位置策略。"
                if policy_unavailable
                else "暂时无法读取存储状态，主界面将继续保持阻断。"
            ),
            "migration_phase": "",
            "shutdown_retry_allowed": False,
            "recovery_action": "safe_exit",
            "migration": {},
            "stage": "",
            "poll_interval_ms": STORAGE_STATUS_POLL_INTERVAL_MS,
            "storage_status_unavailable": True,
            "error_code": recovery_mode,
        }

    current_root = Path(config_manager.app_docs_dir).expanduser().resolve(strict=False)
    display_current_root = Path(
        getattr(config_manager, "reported_current_root", current_root)
    ).expanduser().resolve(strict=False)
    anchor_root = _get_configured_anchor_root(config_manager, current_root=current_root)
    if is_cloudsave_disabled_due_to_local_state_unavailable():
        migration_payload = _build_migration_payload(None, "")
        return {
            "current_root": _normalize_path(display_current_root),
            "recommended_root": _normalize_path(anchor_root),
            "legacy_sources": [],
            "anchor_root": _normalize_path(anchor_root),
            "cloudsave_root": _normalize_path(anchor_root / "cloudsave"),
            "selection_required": False,
            "migration_pending": False,
            "recovery_required": False,
            "blocking_reason": "",
            "legacy_cleanup_pending": False,
            "last_known_good_root": _normalize_path(current_root),
            "last_error_summary": "",
            "migration_phase": "",
            "shutdown_retry_allowed": False,
            "recovery_action": "",
            "migration": migration_payload,
            "stage": STORAGE_LOCATION_STAGE,
            "poll_interval_ms": STORAGE_STATUS_POLL_INTERVAL_MS,
            "cloudsave_disabled": True,
            "cloudsave_disabled_reason": cloudsave_disabled_reason(),
        }

    root_state = config_manager.load_root_state()
    root_mode = str(root_state.get("mode") or "")
    last_migration_result = str(root_state.get("last_migration_result") or "")
    migration_checkpoint = load_storage_migration(
        config_manager,
        anchor_root=anchor_root,
    )

    selection_required = _should_require_selection(
        config_manager,
        current_root=current_root,
        anchor_root=anchor_root,
    )
    migration_status = str((migration_checkpoint or {}).get("status") or "").strip()
    migration_recovery_required = (
        migration_status == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    )
    # The checkpoint remains active for launcher recovery, but there is no
    # live migration worker while the limited service is showing the recovery
    # gate. Expose that distinction so the UI cannot wait forever on progress.
    migration_pending = (
        is_storage_migration_pending(migration_checkpoint)
        and not migration_recovery_required
    )
    restart_intent_recovery_required = bool(
        recovery_mode == "recovery_required"
        and root_mode == ROOT_MODE_MAINTENANCE_READONLY
        and last_migration_result.startswith("restart_pending:")
        and not migration_pending
    )
    awaiting_shutdown = not restart_intent_recovery_required and _is_awaiting_controlled_shutdown(
        root_mode=root_mode,
        last_migration_result=last_migration_result,
        migration_pending=migration_pending,
    )
    migration_pending = migration_pending or awaiting_shutdown
    recovery_required = bool(
        root_mode == ROOT_MODE_DEFERRED_INIT
        or restart_intent_recovery_required
        or migration_recovery_required
    )
    migration_payload = _build_migration_payload(
        migration_checkpoint,
        last_migration_result,
    )
    last_error_summary = migration_payload.get("last_error", "")
    legacy_cleanup_pending = _derive_legacy_cleanup_pending(
        root_state=root_state,
        migration_payload=migration_payload,
        current_root=current_root,
        anchor_root=anchor_root,
    )
    if persist_reconcile:
        root_state = _reconcile_legacy_cleanup_pending_root_state(
            config_manager,
            root_state=root_state,
            derived_legacy_cleanup_pending=legacy_cleanup_pending,
        )

    return {
        "current_root": _normalize_path(display_current_root),
        "recommended_root": _normalize_path(anchor_root),
        "legacy_sources": _collect_legacy_sources(
            config_manager,
            current_root=display_current_root,
            actual_current_root=current_root,
            anchor_root=anchor_root,
        ),
        "anchor_root": _normalize_path(anchor_root),
        "cloudsave_root": _normalize_path(anchor_root / "cloudsave"),
        "selection_required": selection_required,
        "migration_pending": migration_pending,
        "recovery_required": recovery_required,
        "blocking_reason": derive_storage_blocking_reason(
            selection_required=selection_required,
            migration_pending=migration_pending,
            recovery_required=recovery_required,
        ),
        "legacy_cleanup_pending": legacy_cleanup_pending,
        "last_known_good_root": _normalize_path(root_state.get("last_known_good_root") or current_root),
        "last_error_summary": str(last_error_summary or "").strip(),
        "migration_phase": "awaiting_shutdown" if awaiting_shutdown else "",
        "shutdown_retry_allowed": awaiting_shutdown,
        "recovery_action": "retry_safe_exit" if awaiting_shutdown else "",
        "restart_intent_recovery_required": restart_intent_recovery_required,
        "migration": migration_payload,
        "stage": STORAGE_LOCATION_STAGE,
        "poll_interval_ms": STORAGE_STATUS_POLL_INTERVAL_MS,
    }


def build_storage_location_bootstrap_payload(
    config_manager,
    *,
    persist_reconcile: bool = False,
) -> dict[str, Any]:
    payload = _build_storage_location_bootstrap_payload_from_disk(
        config_manager,
        persist_reconcile=persist_reconcile,
    )
    runtime_blocking_reason = get_runtime_storage_blocking_reason()
    if not runtime_blocking_reason:
        return payload

    # Preserve paths and the durable migration evidence from disk, but never
    # let a committed normal root masquerade as an initialized runtime.
    overlaid = dict(payload)
    overlaid.update(
        {
            "selection_required": False,
            "migration_pending": False,
            "recovery_required": True,
            "blocking_reason": runtime_blocking_reason,
            "last_error_summary": (
                "存储位置已安全保存，但当前会话未能完成运行时初始化。"
                "可以重试继续，或安全退出后重新启动。"
            ),
            "migration_phase": "",
            "shutdown_retry_allowed": False,
            "recovery_action": "safe_exit",
            "runtime_startup_blocked": True,
            "error_code": runtime_blocking_reason,
        }
    )
    return overlaid


def get_storage_startup_blocking_reason_readonly(config_manager) -> str:
    # Admission rechecks in Memory/Agent must use durable storage authority.
    # In the packaged merged topology they share this module with Main, whose
    # process-local release-failure overlay intentionally keeps only the HTTP
    # recovery surface blocked.  Letting that overlay enter this disk gate
    # would make the next continue request reject itself forever.
    recovery_mode = get_storage_recovery_mode()
    if recovery_mode:
        return recovery_mode
    if is_cloudsave_disabled_due_to_local_state_unavailable():
        return ""

    try:
        current_root = Path(config_manager.app_docs_dir).expanduser().resolve(strict=False)
        anchor_root = _get_configured_anchor_root(config_manager, current_root=current_root)
        root_state = config_manager.load_root_state()
        root_mode = str(root_state.get("mode") or "")
        migration_checkpoint = load_storage_migration(
            config_manager,
            anchor_root=anchor_root,
        )

        migration_pending = is_storage_migration_pending(migration_checkpoint)
        migration_pending = migration_pending or _is_awaiting_controlled_shutdown(
            root_mode=root_mode,
            last_migration_result=str(root_state.get("last_migration_result") or ""),
            migration_pending=migration_pending,
        )

        return derive_storage_blocking_reason(
            selection_required=_should_require_selection(
                config_manager,
                current_root=current_root,
                anchor_root=anchor_root,
            ),
            migration_pending=migration_pending,
            recovery_required=root_mode == ROOT_MODE_DEFERRED_INIT,
        )
    except StoragePolicyError as exc:
        logger.warning("Storage policy unavailable during startup gate: %s", exc)
        return "storage_policy_unavailable"
    except Exception as exc:  # noqa: BLE001
        # Keep the HTTP limited-mode process alive.  If this escapes the lifespan
        # hook, the renderer loses both diagnostics and the only safe-exit path.
        logger.warning("Storage status unavailable during startup gate: %s", exc)
        return "storage_status_unavailable"


def get_storage_startup_blocking_reason(config_manager) -> str:
    return str(get_storage_startup_blocking_reason_readonly(config_manager) or "").strip()


def is_storage_startup_blocked(config_manager) -> bool:
    return get_storage_startup_blocking_reason(config_manager) in STORAGE_STARTUP_BLOCKING_REASONS
