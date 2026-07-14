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
    cloudsave_disabled_reason,
    is_cloudsave_disabled_due_to_local_state_unavailable,
    runtime_root_has_user_content,
)
from .policy import compute_anchor_root, should_require_storage_selection
from .migration import (
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    load_storage_migration,
)
from utils.logger_config import get_module_logger

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
    }
)


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


def _build_migration_payload(migration_checkpoint: dict[str, Any] | None, last_migration_result: str) -> dict[str, Any]:
    checkpoint = migration_checkpoint if isinstance(migration_checkpoint, dict) else {}
    error_message = str(checkpoint.get("error_message") or "").strip()

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
    if bool(root_state.get("legacy_cleanup_pending")):
        return True
    if str(root_state.get("last_migration_backup") or "").strip():
        return True

    retained_root = str(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or root_state.get("last_migration_backup")
        or ""
    ).strip()
    if not is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=str(migration_payload.get("target_root") or "").strip(),
        require_exists=True,
        allow_anchor_root=True,
    ):
        return False

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

    updated_root_state = dict(root_state)
    updated_root_state["legacy_cleanup_pending"] = bool(derived_legacy_cleanup_pending)
    try:
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


def build_storage_location_bootstrap_payload(config_manager) -> dict[str, Any]:
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
    migration_pending = is_storage_migration_pending(migration_checkpoint)
    recovery_required = root_mode == ROOT_MODE_DEFERRED_INIT
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
        "migration": migration_payload,
        "stage": STORAGE_LOCATION_STAGE,
        "poll_interval_ms": STORAGE_STATUS_POLL_INTERVAL_MS,
    }


def get_storage_startup_blocking_reason_readonly(config_manager) -> str:
    if is_cloudsave_disabled_due_to_local_state_unavailable():
        return ""

    current_root = Path(config_manager.app_docs_dir).expanduser().resolve(strict=False)
    anchor_root = _get_configured_anchor_root(config_manager, current_root=current_root)
    root_state = config_manager.load_root_state()
    root_mode = str(root_state.get("mode") or "")
    migration_checkpoint = load_storage_migration(
        config_manager,
        anchor_root=anchor_root,
    )

    return derive_storage_blocking_reason(
        selection_required=_should_require_selection(
            config_manager,
            current_root=current_root,
            anchor_root=anchor_root,
        ),
        migration_pending=is_storage_migration_pending(migration_checkpoint),
        recovery_required=root_mode == ROOT_MODE_DEFERRED_INIT,
    )


def get_storage_startup_blocking_reason(config_manager) -> str:
    return str(get_storage_startup_blocking_reason_readonly(config_manager) or "").strip()


def is_storage_startup_blocked(config_manager) -> bool:
    return get_storage_startup_blocking_reason(config_manager) in STORAGE_STARTUP_BLOCKING_REASONS
