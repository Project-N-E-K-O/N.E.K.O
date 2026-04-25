from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json, read_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

STORAGE_POLICY_VERSION = 1
CLOUDSAVE_STRATEGY_FIXED_ANCHOR = "fixed_anchor"
POLICY_SELECTION_SOURCE_DEFAULT = "default"
POLICY_SELECTION_SOURCE_USER_SELECTED = "user_selected"
POLICY_SELECTION_SOURCE_RECOVERED = "recovered"


class StorageSelectionValidationError(ValueError):
    """Raised when a requested storage root violates Stage 2 constraints."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_compare_string(path: Path) -> str:
    value = str(path)
    if os.name == "nt":
        return os.path.normcase(value)
    return value


def paths_equal(left: Path | str, right: Path | str) -> bool:
    normalized_left = normalize_runtime_root(left)
    normalized_right = normalize_runtime_root(right)
    return _normalize_compare_string(normalized_left) == _normalize_compare_string(normalized_right)


def _paths_equal(left: Path, right: Path) -> bool:
    return _normalize_compare_string(left) == _normalize_compare_string(right)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_runtime_root(value: Path | str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def is_runtime_root_available(value: Path | str) -> bool:
    path = normalize_runtime_root(value)
    try:
        return path.exists() and path.is_dir() and os.access(str(path), os.R_OK | os.X_OK)
    except OSError:
        return False


def normalize_selected_root(value: Path | str) -> Path:
    raw_value = str(value or "").strip()
    if not raw_value:
        raise StorageSelectionValidationError("selected_root_empty", "目标路径不能为空。")

    expanded = Path(raw_value).expanduser()
    if not expanded.is_absolute():
        raise StorageSelectionValidationError("selected_root_not_absolute", "目标路径必须是绝对路径。")

    return expanded.resolve(strict=False)


def compute_anchor_root(config_manager, *, current_root: Path | None = None) -> Path:
    normalized_current_root = normalize_runtime_root(current_root or config_manager.app_docs_dir)
    getter = getattr(config_manager, "_get_standard_data_directory_candidates", None)
    if callable(getter):
        try:
            candidates = getter()
        except Exception as exc:
            logger.warning("Failed to query standard data directory candidates: %s", exc)
            candidates = []

        for candidate in candidates:
            try:
                return normalize_runtime_root(Path(candidate) / config_manager.app_name)
            except Exception:
                continue

    return normalized_current_root


def get_storage_policy_path(config_manager, *, anchor_root: Path | None = None) -> Path:
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager)
    )
    return normalized_anchor_root / "state" / "storage_policy.json"


def load_storage_policy(
    config_manager,
    *,
    anchor_root: Path | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    try:
        payload = read_json(policy_path)
    except FileNotFoundError:
        return default

    if not isinstance(payload, dict):
        logger.warning("storage_policy payload is not a dict: %s", policy_path)
        return default

    return payload


def _coerce_policy_selection_source(
    requested_source: str,
    *,
    selected_root: Path,
    recommended_root: Path,
) -> str:
    source = str(requested_source or "").strip().lower()
    if source == POLICY_SELECTION_SOURCE_RECOVERED:
        return POLICY_SELECTION_SOURCE_RECOVERED

    if _paths_equal(selected_root, recommended_root):
        return POLICY_SELECTION_SOURCE_DEFAULT

    return POLICY_SELECTION_SOURCE_USER_SELECTED


def save_storage_policy(
    config_manager,
    *,
    selected_root: Path | str,
    selection_source: str,
    anchor_root: Path | None = None,
) -> dict[str, Any]:
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager)
    )
    normalized_selected_root = normalize_runtime_root(selected_root)
    policy_payload = {
        "version": STORAGE_POLICY_VERSION,
        "anchor_root": str(normalized_anchor_root),
        "selected_root": str(normalized_selected_root),
        "selection_source": _coerce_policy_selection_source(
            selection_source,
            selected_root=normalized_selected_root,
            recommended_root=normalized_anchor_root,
        ),
        "cloudsave_strategy": CLOUDSAVE_STRATEGY_FIXED_ANCHOR,
        "first_run_completed": True,
        "updated_at": _utc_now_iso(),
    }

    policy_path = get_storage_policy_path(config_manager, anchor_root=normalized_anchor_root)
    atomic_write_json(policy_path, policy_payload, ensure_ascii=False, indent=2)
    return policy_payload


def should_require_storage_selection(
    config_manager,
    *,
    current_root: Path | None = None,
    anchor_root: Path | None = None,
) -> bool:
    normalized_current_root = normalize_runtime_root(current_root or config_manager.app_docs_dir)
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager, current_root=normalized_current_root)
    )

    try:
        policy = load_storage_policy(config_manager, anchor_root=normalized_anchor_root)
    except Exception as exc:
        logger.warning("Failed to load storage_policy; fallback to requiring selection: %s", exc)
        return True

    if not isinstance(policy, dict):
        return True

    if not bool(policy.get("first_run_completed")):
        return True

    selected_root = str(policy.get("selected_root") or "").strip()
    if not selected_root:
        return True

    try:
        normalized_selected_root = normalize_runtime_root(selected_root)
    except Exception:
        return True

    return not _paths_equal(normalized_selected_root, normalized_current_root)


def _find_existing_parent(path: Path) -> Path | None:
    candidate = path
    while True:
        if candidate.exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


def _can_write_existing_directory(directory: Path) -> bool:
    try:
        if not directory.exists() or not directory.is_dir():
            return False
        if not os.access(str(directory), os.R_OK | os.W_OK | os.X_OK):
            return False

        probe_path = directory / f".neko-storage-location-{uuid.uuid4().hex}.tmp"
        probe_path.write_text("", encoding="utf-8")
        probe_path.unlink()
        return True
    except Exception:
        return False


def validate_selected_root(
    config_manager,
    selected_root: Path | str,
    *,
    current_root: Path | None = None,
    anchor_root: Path | None = None,
) -> Path:
    normalized_current_root = normalize_runtime_root(current_root or config_manager.app_docs_dir)
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager, current_root=normalized_current_root)
    )
    normalized_target_root = normalize_selected_root(selected_root)

    if _paths_equal(normalized_target_root, normalized_current_root):
        return normalized_current_root

    project_root = normalize_runtime_root(Path(__file__).resolve().parents[1])
    if _paths_equal(normalized_target_root, project_root) or _is_relative_to(
        normalized_target_root,
        project_root,
    ):
        raise StorageSelectionValidationError(
            "selected_root_inside_project",
            "目标路径不能位于项目目录内。",
        )

    reserved_roots = (
        (normalized_anchor_root / "cloudsave", "selected_root_inside_cloudsave"),
        (normalized_anchor_root / "state", "selected_root_inside_state"),
        (normalized_anchor_root / ".cloudsave_staging", "selected_root_inside_staging"),
        (normalized_anchor_root / "cloudsave_backups", "selected_root_inside_backups"),
    )
    for reserved_root, error_code in reserved_roots:
        if _paths_equal(normalized_target_root, reserved_root) or _is_relative_to(
            normalized_target_root,
            reserved_root,
        ):
            raise StorageSelectionValidationError(
                error_code,
                "目标路径不能位于锚点目录保留区域内。",
            )

    if _is_relative_to(normalized_target_root, normalized_anchor_root) and not _paths_equal(
        normalized_target_root,
        normalized_anchor_root,
    ):
        raise StorageSelectionValidationError(
            "selected_root_inside_anchor_root",
            "目标路径不能是锚点目录的子目录，除非它与锚点目录本身完全相同。",
        )

    if normalized_target_root.exists():
        if normalized_target_root.is_file():
            raise StorageSelectionValidationError(
                "selected_root_is_file",
                "目标路径不能是文件。",
            )
        if not normalized_target_root.is_dir():
            raise StorageSelectionValidationError(
                "selected_root_not_directory",
                "目标路径必须是目录。",
            )
        if not _can_write_existing_directory(normalized_target_root):
            raise StorageSelectionValidationError(
                "selected_root_not_writable",
                "目标路径当前不可写。",
            )
        return normalized_target_root

    existing_parent = _find_existing_parent(normalized_target_root)
    if existing_parent is None or not existing_parent.is_dir():
        raise StorageSelectionValidationError(
            "selected_root_parent_missing",
            "目标路径的父目录不存在，无法创建。",
        )

    if not _can_write_existing_directory(existing_parent):
        raise StorageSelectionValidationError(
            "selected_root_parent_not_writable",
            "目标路径的父目录不可写，无法创建。",
        )

    return normalized_target_root
