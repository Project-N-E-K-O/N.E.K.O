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

import json
import os
import stat
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


class StoragePolicyError(RuntimeError):
    """A persisted storage policy cannot be trusted as routing authority."""

    error_code = "storage_policy_unavailable"

    def __init__(self, reason: str, message: str = "无法安全读取存储位置策略。"):
        super().__init__(message)
        self.reason = str(reason or "invalid").strip() or "invalid"
        self.message = str(message or "无法安全读取存储位置策略。").strip()


class PathIdentityUnavailable(OSError):
    """A path exists or may exist, but its physical identity is uninspectable."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_compare_string(path: Path) -> str:
    value = str(path)
    if os.name == "nt":
        return os.path.normcase(value)
    return value


def _existing_path_identity(path: Path) -> tuple[int, int] | None:
    """Return a followed filesystem identity, or ``None`` only when absent."""

    try:
        result = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise PathIdentityUnavailable(str(path)) from exc
    return (int(result.st_dev), int(result.st_ino))


def _lexical_paths_equal(left: Path, right: Path) -> bool:
    return _normalize_compare_string(left) == _normalize_compare_string(right)


def _existing_paths_equal(
    left: Path,
    right: Path,
    left_identity: tuple[int, int],
    right_identity: tuple[int, int],
) -> bool:
    try:
        return os.path.samefile(left, right)
    except (AttributeError, NotImplementedError):
        return left_identity == right_identity
    except OSError as exc:
        raise PathIdentityUnavailable(f"{left} <-> {right}") from exc


def paths_equal(left: Path | str, right: Path | str) -> bool:
    normalized_left = normalize_runtime_root(left)
    normalized_right = normalize_runtime_root(right)
    left_identity = _existing_path_identity(normalized_left)
    right_identity = _existing_path_identity(normalized_right)
    if left_identity is not None and right_identity is not None:
        return _existing_paths_equal(
            normalized_left,
            normalized_right,
            left_identity,
            right_identity,
        )
    return _lexical_paths_equal(normalized_left, normalized_right)


def path_is_within(path: Path | str, parent: Path | str) -> bool:
    """Return whether ``path`` is equal to or physically below ``parent``.

    Existing ancestors are compared by device/inode so case aliases on default
    APFS and equivalent Windows names cannot bypass containment checks.  When
    the parent does not exist there is no physical identity to consult, so the
    normalized platform lexical relationship remains authoritative.
    """

    normalized_path = normalize_runtime_root(path)
    normalized_parent = normalize_runtime_root(parent)
    parent_identity = _existing_path_identity(normalized_parent)
    if parent_identity is None:
        try:
            normalized_path.relative_to(normalized_parent)
            return True
        except ValueError:
            return False

    candidate = normalized_path
    while True:
        candidate_identity = _existing_path_identity(candidate)
        if candidate_identity is not None and _existing_paths_equal(
            candidate,
            normalized_parent,
            candidate_identity,
            parent_identity,
        ):
            return True
        candidate_parent = candidate.parent
        if candidate_parent == candidate:
            return False
        candidate = candidate_parent


def _paths_equal(left: Path, right: Path) -> bool:
    return paths_equal(left, right)


def _is_relative_to(path: Path, parent: Path) -> bool:
    return path_is_within(path, parent)


def normalize_runtime_root(value: Path | str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def is_runtime_root_available(value: Path | str) -> bool:
    path = normalize_runtime_root(value)
    try:
        return _can_write_existing_directory(path)
    except OSError:
        return False


def _path_name_matches_app_name(path: Path, app_name: str) -> bool:
    if not app_name:
        return False
    if path.name == app_name:
        return True
    if os.name == "nt":
        return os.path.normcase(path.name) == os.path.normcase(app_name)
    # A default APFS volume is case-insensitive but preserves the spelling the
    # caller supplied.  Only treat a differently-cased name as the app folder
    # when both spellings resolve to the same existing filesystem object; this
    # keeps case-sensitive APFS/Linux behavior lexical for missing/distinct dirs.
    return paths_equal(path, path.with_name(app_name))


def normalize_selected_root(
    value: Path | str,
    *,
    app_name: str = "",
    selection_source: str = "",
) -> Path:
    raw_value = str(value or "").strip()
    if not raw_value:
        raise StorageSelectionValidationError("selected_root_empty", "目标路径不能为空。")

    expanded = Path(raw_value).expanduser()
    if not expanded.is_absolute():
        raise StorageSelectionValidationError("selected_root_not_absolute", "目标路径必须是绝对路径。")

    if (
        str(selection_source or "").strip().lower() == "custom"
        and app_name
        and not _path_name_matches_app_name(expanded, app_name)
    ):
        expanded = expanded / app_name

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


def _raise_invalid_storage_policy(reason: str) -> None:
    raise StoragePolicyError(reason)


def _normalize_policy_path(value: Any, *, field: str) -> tuple[Path, Path]:
    raw_value = str(value or "").strip()
    if not raw_value:
        _raise_invalid_storage_policy(f"{field}_missing")
    raw_path = Path(raw_value).expanduser()
    if not raw_path.is_absolute():
        _raise_invalid_storage_policy(f"{field}_not_absolute")
    try:
        normalized_path = normalize_runtime_root(raw_path)
    except Exception as exc:
        raise StoragePolicyError(f"{field}_invalid") from exc
    return raw_path, normalized_path


def _path_chain_redirect_status(value: Path | str) -> bool:
    """Inspect links/reparse points while preserving metadata errors for callers."""

    candidate = Path(value).expanduser()
    while True:
        try:
            stat_result = candidate.lstat()
            is_reparse_point = bool(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                & getattr(stat_result, "st_file_attributes", 0)
            )
            if stat.S_ISLNK(stat_result.st_mode) or is_reparse_point:
                return True
        except FileNotFoundError:
            pass
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent


def _validate_storage_policy_payload(
    config_manager,
    payload: dict[str, Any],
    *,
    anchor_root: Path,
) -> dict[str, Any]:
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != STORAGE_POLICY_VERSION:
        _raise_invalid_storage_policy("version_invalid")
    if payload.get("cloudsave_strategy") != CLOUDSAVE_STRATEGY_FIXED_ANCHOR:
        _raise_invalid_storage_policy("cloudsave_strategy_invalid")
    if payload.get("first_run_completed") is not True:
        _raise_invalid_storage_policy("first_run_completed_invalid")
    if str(payload.get("selection_source") or "").strip() not in {
        POLICY_SELECTION_SOURCE_DEFAULT,
        POLICY_SELECTION_SOURCE_USER_SELECTED,
        POLICY_SELECTION_SOURCE_RECOVERED,
    }:
        _raise_invalid_storage_policy("selection_source_invalid")
    if not isinstance(payload.get("updated_at"), str) or not payload["updated_at"].strip():
        _raise_invalid_storage_policy("updated_at_invalid")

    _raw_anchor_root, _stored_anchor_root = _normalize_policy_path(
        payload.get("anchor_root"),
        field="anchor_root",
    )

    _raw_selected_root, selected_root = _normalize_policy_path(
        payload.get("selected_root"),
        field="selected_root",
    )
    if selected_root == selected_root.parent:
        _raise_invalid_storage_policy("selected_root_filesystem_root")

    normalized_anchor_root = normalize_runtime_root(anchor_root)
    project_root = normalize_runtime_root(Path(__file__).resolve().parents[2])
    try:
        if _paths_equal(selected_root, project_root) or _is_relative_to(selected_root, project_root):
            _raise_invalid_storage_policy("selected_root_inside_project")

        reserved_roots = (
            normalized_anchor_root / "cloudsave",
            normalized_anchor_root / "state",
            normalized_anchor_root / ".cloudsave_staging",
            normalized_anchor_root / "cloudsave_backups",
        )
        if any(
            _paths_equal(selected_root, reserved_root)
            or _is_relative_to(selected_root, reserved_root)
            for reserved_root in reserved_roots
        ):
            _raise_invalid_storage_policy("selected_root_inside_reserved_root")
        if _is_relative_to(selected_root, normalized_anchor_root) and not _paths_equal(
            selected_root,
            normalized_anchor_root,
        ):
            _raise_invalid_storage_policy("selected_root_inside_anchor_root")
    except PathIdentityUnavailable as exc:
        raise StoragePolicyError("selected_root_identity_uninspectable") from exc

    try:
        if selected_root.exists() and (
            selected_root.is_file() or not selected_root.is_dir()
        ):
            _raise_invalid_storage_policy("selected_root_not_directory")
    except OSError:
        # As above, accessibility is runtime recovery state, not schema damage.
        pass

    # The stored anchor remains audit data and may be stale when an explicit
    # NEKO_STORAGE_ANCHOR_ROOT override moved the fixed anchor.  It still must
    # be a structurally safe absolute path, but the caller owns precedence.
    return payload


def _validate_storage_policy_anchor(value: Path | str) -> None:
    """Allow an absent first-run anchor, but never an unsafe existing chain."""

    candidate = Path(value).expanduser()
    blocked_lookup = False
    while True:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            pass
        except NotADirectoryError:
            # Continue upward so the existing regular-file ancestor can be
            # classified explicitly instead of becoming a platform-dependent
            # "policy absent" result.
            blocked_lookup = True
        except OSError as exc:
            raise StoragePolicyError("anchor_root_uninspectable") from exc
        else:
            is_reparse_point = bool(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                & getattr(metadata, "st_file_attributes", 0)
            )
            if stat.S_ISLNK(metadata.st_mode) or is_reparse_point:
                raise StoragePolicyError("anchor_root_redirect")
            if not stat.S_ISDIR(metadata.st_mode):
                raise StoragePolicyError("anchor_root_not_directory")

        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    if blocked_lookup:
        raise StoragePolicyError("anchor_root_uninspectable")


def load_storage_policy(
    config_manager,
    *,
    anchor_root: Path | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    configured_anchor_root = anchor_root or compute_anchor_root(config_manager)
    _validate_storage_policy_anchor(configured_anchor_root)

    policy_path = get_storage_policy_path(
        config_manager,
        anchor_root=normalize_runtime_root(configured_anchor_root),
    )
    try:
        if policy_path.is_symlink():
            raise StoragePolicyError("policy_path_redirect")
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    try:
        payload = read_json(policy_path)
    except FileNotFoundError:
        # Windows maps a child lookup below a newly replaced regular file to
        # FileNotFoundError. Recheck the authority boundary before deciding
        # this is the legitimate first-run "policy absent" state.
        _validate_storage_policy_anchor(configured_anchor_root)
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Malformed storage_policy at %s: %s", policy_path, exc)
        raise StoragePolicyError("malformed") from exc
    except Exception as exc:
        logger.warning("Unreadable storage_policy at %s: %s", policy_path, exc)
        raise StoragePolicyError("unreadable") from exc

    if not isinstance(payload, dict):
        logger.warning("storage_policy payload is not a dict: %s", policy_path)
        raise StoragePolicyError("not_object")

    try:
        return _validate_storage_policy_payload(
            config_manager,
            payload,
            anchor_root=normalize_runtime_root(
                anchor_root or compute_anchor_root(config_manager)
            ),
        )
    except StoragePolicyError as exc:
        logger.warning("Invalid storage_policy at %s: %s", policy_path, exc.reason)
        raise


def _coerce_policy_selection_source(
    requested_source: str,
    *,
    selected_root: Path,
    recommended_root: Path,
) -> str:
    source = str(requested_source or "").strip().lower()
    if source == POLICY_SELECTION_SOURCE_RECOVERED:
        return POLICY_SELECTION_SOURCE_RECOVERED

    try:
        if _paths_equal(selected_root, recommended_root):
            return POLICY_SELECTION_SOURCE_DEFAULT
    except PathIdentityUnavailable as exc:
        raise StoragePolicyError("selected_root_identity_uninspectable") from exc

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
    try:
        policy_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StoragePolicyError("existing_policy_uninspectable") from exc
    else:
        # Never use a normal selection/migration write as an implicit repair for
        # corrupted authority.  Recovery must preserve the original evidence.
        load_storage_policy(config_manager, anchor_root=normalized_anchor_root)

    _validate_storage_policy_payload(
        config_manager,
        policy_payload,
        anchor_root=normalized_anchor_root,
    )
    atomic_write_json(policy_path, policy_payload, ensure_ascii=False, indent=2)
    return policy_payload


def restore_storage_policy_snapshot(
    config_manager,
    policy_payload: dict[str, Any] | None,
    *,
    anchor_root: Path | str | None = None,
) -> None:
    """Restore the policy value captured before a failed scheduling attempt."""

    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager)
    )
    policy_path = get_storage_policy_path(
        config_manager,
        anchor_root=normalized_anchor_root,
    )
    if policy_payload is None:
        try:
            policy_path.unlink()
        except FileNotFoundError:
            pass
        return
    if not isinstance(policy_payload, dict):
        raise StoragePolicyError("invalid_policy_snapshot")
    _validate_storage_policy_payload(
        config_manager,
        policy_payload,
        anchor_root=normalized_anchor_root,
    )
    atomic_write_json(policy_path, policy_payload, ensure_ascii=False, indent=2)


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

    policy = load_storage_policy(config_manager, anchor_root=normalized_anchor_root)

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

    try:
        return not _paths_equal(normalized_selected_root, normalized_current_root)
    except PathIdentityUnavailable:
        return True


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


def path_chain_has_symlink(value: Path | str) -> bool:
    """Return whether a lexical path or parent redirects through a link.

    Missing descendants are allowed because a selected root may not exist yet.
    Windows reparse points (including junctions) are links for this safety
    boundary too. Other metadata failures are treated as unsafe instead of
    silently following an uninspectable path during a destructive operation.
    """

    try:
        return _path_chain_redirect_status(value)
    except OSError:
        return True


def validate_selected_root(
    config_manager,
    selected_root: Path | str,
    *,
    current_root: Path | None = None,
    anchor_root: Path | None = None,
    selection_source: str = "",
) -> Path:
    raw_selected_root = str(selected_root or "").strip()
    expanded_selected_root = Path(raw_selected_root).expanduser()
    try:
        if (
            expanded_selected_root.is_absolute()
            and str(selection_source or "").strip().lower() == "custom"
            and str(getattr(config_manager, "app_name", "") or "")
            and not _path_name_matches_app_name(
                expanded_selected_root,
                str(getattr(config_manager, "app_name", "") or ""),
            )
        ):
            expanded_selected_root /= str(getattr(config_manager, "app_name", "") or "")
    except PathIdentityUnavailable as exc:
        raise StorageSelectionValidationError(
            "selected_root_identity_uninspectable",
            "无法确认目标路径的物理身份。",
        ) from exc
    normalized_current_root = normalize_runtime_root(current_root or config_manager.app_docs_dir)
    normalized_anchor_root = normalize_runtime_root(
        anchor_root or compute_anchor_root(config_manager, current_root=normalized_current_root)
    )
    try:
        normalized_target_root = normalize_selected_root(
            selected_root,
            app_name=str(getattr(config_manager, "app_name", "") or ""),
            selection_source=selection_source,
        )
    except PathIdentityUnavailable as exc:
        raise StorageSelectionValidationError(
            "selected_root_identity_uninspectable",
            "无法确认目标路径的物理身份。",
        ) from exc

    if normalized_target_root == normalized_target_root.parent:
        raise StorageSelectionValidationError(
            "selected_root_filesystem_root",
            "目标路径不能是文件系统根目录。",
        )

    try:
        if _paths_equal(normalized_target_root, normalized_current_root):
            return normalized_current_root

        project_root = normalize_runtime_root(Path(__file__).resolve().parents[2])
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
    except PathIdentityUnavailable as exc:
        raise StorageSelectionValidationError(
            "selected_root_identity_uninspectable",
            "无法确认目标路径与受保护目录的物理关系。",
        ) from exc

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
