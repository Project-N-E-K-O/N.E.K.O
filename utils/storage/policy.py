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
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

STORAGE_POLICY_VERSION = 1
CLOUDSAVE_STRATEGY_FIXED_ANCHOR = "fixed_anchor"
POLICY_SELECTION_SOURCE_DEFAULT = "default"
POLICY_SELECTION_SOURCE_USER_SELECTED = "user_selected"
POLICY_SELECTION_SOURCE_RECOVERED = "recovered"

# Both fixed-anchor JSON files have app-owned, bounded schemas.  The migration
# checkpoint currently contains snapshots for only the 16 declared runtime
# entries; 4 MiB also leaves ample room for legacy checkpoints and extended
# Windows paths while preventing a corrupt authority file from exhausting the
# process during startup.
_FIXED_ANCHOR_JSON_MAX_BYTES = 4 * 1024 * 1024
_FIXED_ANCHOR_JSON_READ_CHUNK_BYTES = 1024 * 1024


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

    raw_anchor_root, _stored_anchor_root = _normalize_policy_path(
        payload.get("anchor_root"),
        field="anchor_root",
    )
    try:
        if _path_chain_redirect_status(raw_anchor_root):
            _raise_invalid_storage_policy("anchor_root_redirect")
    except OSError as exc:
        raise StoragePolicyError("anchor_root_uninspectable") from exc

    raw_selected_root, selected_root = _normalize_policy_path(
        payload.get("selected_root"),
        field="selected_root",
    )
    try:
        selected_root_redirects = _path_chain_redirect_status(raw_selected_root)
    except OSError:
        # A committed external volume can be temporarily inaccessible.  Keep
        # the reference valid so ConfigManager's existing unavailable-root
        # recovery path can route the session to the anchor without forgetting
        # where the user's data lives.
        selected_root_redirects = False
    if selected_root_redirects:
        _raise_invalid_storage_policy("selected_root_redirect")
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


def _validate_storage_policy_anchor(value: Path | str) -> os.stat_result | None:
    """Allow an absent first-run anchor, but never an unsafe existing chain."""

    candidate = Path(value).expanduser()
    anchor = candidate
    anchor_metadata: os.stat_result | None = None
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
            if candidate == anchor:
                anchor_metadata = metadata

        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    if blocked_lookup:
        raise StoragePolicyError("anchor_root_uninspectable")
    return anchor_metadata


def _validate_fixed_anchor_json_size(size: int) -> None:
    if size < 0 or size > _FIXED_ANCHOR_JSON_MAX_BYTES:
        raise StoragePolicyError("policy_payload_too_large")


def _read_fixed_anchor_json_chunks(read_chunk: Callable[[int], bytes]) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        remaining_bytes = _FIXED_ANCHOR_JSON_MAX_BYTES - total_bytes
        chunk = read_chunk(
            min(_FIXED_ANCHOR_JSON_READ_CHUNK_BYTES, remaining_bytes + 1)
        )
        if not chunk:
            break
        if len(chunk) > remaining_bytes:
            raise StoragePolicyError("policy_payload_too_large")
        chunks.append(chunk)
        total_bytes += len(chunk)
    return b"".join(chunks)


def _read_open_file_descriptor(fd: int) -> bytes:
    return _read_fixed_anchor_json_chunks(lambda size: os.read(fd, size))


def _posix_directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _posix_file_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _fsync_policy_directory_required(path: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = -1
    try:
        directory_fd = os.open(path, _posix_directory_open_flags())
        os.fsync(directory_fd)
    except OSError as exc:
        raise StoragePolicyError("policy_flush_failed") from exc
    finally:
        if directory_fd >= 0:
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _fsync_opened_policy_directory_required(directory_fd: int) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise StoragePolicyError("policy_flush_failed") from exc


def _open_posix_anchor_directory(
    anchor_root: Path,
    expected_anchor: os.stat_result | None,
) -> int:
    """Open every anchor component relative to its already-open parent."""

    if not anchor_root.is_absolute() or not anchor_root.anchor:
        raise StoragePolicyError("anchor_root_uninspectable")

    directory_fd = -1
    try:
        directory_fd = os.open(anchor_root.anchor, _posix_directory_open_flags())
        for component in anchor_root.parts[1:]:
            child_fd = os.open(
                component,
                _posix_directory_open_flags(),
                dir_fd=directory_fd,
            )
            parent_fd = directory_fd
            directory_fd = child_fd
            os.close(parent_fd)
    except FileNotFoundError as exc:
        if directory_fd >= 0:
            os.close(directory_fd)
        if expected_anchor is None:
            # Absence is an authority fact too.  If the anchor appeared after
            # the failed component open, returning a first-run/default result
            # could hide a checkpoint published by another process.
            if _validate_storage_policy_anchor(anchor_root) is not None:
                raise StoragePolicyError("anchor_root_changed") from exc
            raise
        raise StoragePolicyError("anchor_root_changed") from exc
    except OSError as exc:
        if directory_fd >= 0:
            os.close(directory_fd)
        raise StoragePolicyError("anchor_root_changed") from exc

    opened_anchor = os.fstat(directory_fd)
    if (
        expected_anchor is None
        or not stat.S_ISDIR(opened_anchor.st_mode)
        or not os.path.samestat(expected_anchor, opened_anchor)
    ):
        os.close(directory_fd)
        raise StoragePolicyError("anchor_root_changed")
    return directory_fd


def _open_posix_child_directory(parent_fd: int, name: str) -> int:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise StoragePolicyError("policy_path_redirect")

    try:
        child_fd = os.open(name, _posix_directory_open_flags(), dir_fd=parent_fd)
    except FileNotFoundError as exc:
        raise StoragePolicyError("policy_path_changed") from exc
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    opened = os.fstat(child_fd)
    if not stat.S_ISDIR(opened.st_mode) or not os.path.samestat(named, opened):
        os.close(child_fd)
        raise StoragePolicyError("policy_path_changed")
    return child_fd


def _open_posix_policy_file(
    state_fd: int,
    filename: str,
) -> tuple[int, os.stat_result]:
    named = os.stat(filename, dir_fd=state_fd, follow_symlinks=False)
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
        raise StoragePolicyError("policy_path_redirect")
    try:
        policy_fd = os.open(filename, _posix_file_open_flags(), dir_fd=state_fd)
    except FileNotFoundError as exc:
        raise StoragePolicyError("policy_changed_during_read") from exc
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    opened = os.fstat(policy_fd)
    if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(named, opened):
        os.close(policy_fd)
        raise StoragePolicyError("policy_changed_during_read")
    return policy_fd, opened


def _revalidate_posix_policy_handles(
    anchor_root: Path,
    anchor_fd: int,
    state_fd: int,
    policy_fd: int,
    policy_before: os.stat_result,
    filename: str,
) -> None:
    _validate_storage_policy_anchor(anchor_root)

    try:
        named_anchor = anchor_root.lstat()
        named_state = (anchor_root / "state").lstat()
        named_policy = (anchor_root / "state" / filename).lstat()
    except OSError as exc:
        raise StoragePolicyError("policy_changed_during_read") from exc
    opened_anchor = os.fstat(anchor_fd)
    opened_state = os.fstat(state_fd)
    policy_after = os.fstat(policy_fd)
    if (
        stat.S_ISLNK(named_anchor.st_mode)
        or not stat.S_ISDIR(named_anchor.st_mode)
        or not os.path.samestat(named_anchor, opened_anchor)
    ):
        raise StoragePolicyError("anchor_root_changed")
    if (
        stat.S_ISLNK(named_state.st_mode)
        or not stat.S_ISDIR(named_state.st_mode)
        or not os.path.samestat(named_state, opened_state)
        or stat.S_ISLNK(named_policy.st_mode)
        or not stat.S_ISREG(named_policy.st_mode)
        or not os.path.samestat(named_policy, policy_after)
        or not os.path.samestat(policy_before, policy_after)
        or policy_before.st_size != policy_after.st_size
        or policy_before.st_mtime_ns != policy_after.st_mtime_ns
    ):
        raise StoragePolicyError("policy_changed_during_read")


def _revalidate_posix_policy_absence(
    anchor_root: Path,
    anchor_fd: int,
    state_fd: int,
    filename: str,
) -> None:
    if state_fd < 0:
        parent_fd = anchor_fd
        missing_name = "state"
    else:
        parent_fd = state_fd
        missing_name = filename

    try:
        os.stat(missing_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    else:
        raise StoragePolicyError("policy_changed_during_read")

    # The named identity must be the final observation. Otherwise the anchor
    # could be replaced after the absence check and the detached directory's
    # missing child would be misclassified as a genuine first run.
    _validate_storage_policy_anchor(anchor_root)
    try:
        named_anchor = anchor_root.lstat()
    except OSError as exc:
        raise StoragePolicyError("anchor_root_changed") from exc
    opened_anchor = os.fstat(anchor_fd)
    if (
        stat.S_ISLNK(named_anchor.st_mode)
        or not stat.S_ISDIR(named_anchor.st_mode)
        or not os.path.samestat(named_anchor, opened_anchor)
    ):
        raise StoragePolicyError("anchor_root_changed")
    if state_fd >= 0:
        try:
            named_state = (anchor_root / "state").lstat()
        except OSError as exc:
            raise StoragePolicyError("policy_changed_during_read") from exc
        if (
            stat.S_ISLNK(named_state.st_mode)
            or not stat.S_ISDIR(named_state.st_mode)
            or not os.path.samestat(named_state, os.fstat(state_fd))
        ):
            raise StoragePolicyError("policy_changed_during_read")


def _revalidate_posix_state_directories(
    anchor_root: Path,
    anchor_fd: int,
    state_fd: int,
) -> None:
    """Require the pinned fixed-anchor directories to keep their public names."""

    try:
        named_anchor = anchor_root.lstat()
        named_state = (anchor_root / "state").lstat()
        opened_anchor = os.fstat(anchor_fd)
        opened_state = os.fstat(state_fd)
    except OSError as exc:
        raise StoragePolicyError("policy_path_changed") from exc
    if (
        stat.S_ISLNK(named_anchor.st_mode)
        or not stat.S_ISDIR(named_anchor.st_mode)
        or not os.path.samestat(named_anchor, opened_anchor)
    ):
        raise StoragePolicyError("anchor_root_changed")
    if (
        stat.S_ISLNK(named_state.st_mode)
        or not stat.S_ISDIR(named_state.st_mode)
        or not os.path.samestat(named_state, opened_state)
    ):
        raise StoragePolicyError("policy_path_changed")


def _restore_storage_policy_snapshot_posix(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    expected_state: os.stat_result | None,
    encoded_payload: bytes | None,
) -> None:
    anchor_fd = -1
    state_fd = -1
    temp_fd = -1
    temp_name = f".neko-policy-restore-{uuid.uuid4().hex}.tmp"
    temp_created = False
    try:
        anchor_fd = _open_posix_anchor_directory(anchor_root, expected_anchor)
        try:
            state_fd = _open_posix_child_directory(anchor_fd, "state")
        except FileNotFoundError:
            if encoded_payload is None:
                _revalidate_posix_policy_absence(
                    anchor_root,
                    anchor_fd,
                    -1,
                    "storage_policy.json",
                )
                return
            raise StoragePolicyError("policy_path_changed")
        if expected_state is None or not os.path.samestat(
            expected_state,
            os.fstat(state_fd),
        ):
            raise StoragePolicyError("policy_path_changed")

        _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
        if encoded_payload is None:
            try:
                os.unlink("storage_policy.json", dir_fd=state_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StoragePolicyError("policy_restore_delete_failed") from exc
        else:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            temp_fd = os.open(temp_name, flags, 0o600, dir_fd=state_fd)
            temp_created = True
            view = memoryview(encoded_payload)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("short write while restoring storage policy")
                view = view[written:]
            os.fsync(temp_fd)
            opened_temp = os.fstat(temp_fd)
            named_temp = os.stat(
                temp_name,
                dir_fd=state_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(opened_temp.st_mode)
                or not stat.S_ISREG(named_temp.st_mode)
                or not os.path.samestat(opened_temp, named_temp)
            ):
                raise StoragePolicyError("policy_path_changed")
            _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
            os.replace(
                temp_name,
                "storage_policy.json",
                src_dir_fd=state_fd,
                dst_dir_fd=state_fd,
            )
            temp_created = False
            named_policy = os.stat(
                "storage_policy.json",
                dir_fd=state_fd,
                follow_symlinks=False,
            )
            if not os.path.samestat(opened_temp, named_policy):
                raise StoragePolicyError("policy_path_changed")

        _fsync_opened_policy_directory_required(state_fd)
        _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
    except StoragePolicyError:
        raise
    except OSError as exc:
        raise StoragePolicyError("policy_restore_failed") from exc
    finally:
        if temp_fd >= 0:
            with suppress(OSError):
                os.close(temp_fd)
        if temp_created and state_fd >= 0:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=state_fd)
        for fd in (state_fd, anchor_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


def _open_windows_policy_directory_guards(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    expected_state: os.stat_result | None,
) -> list[int]:
    """Pin every named directory component while denying rename/delete sharing."""

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
    handles: list[int] = []

    def _native_path(path: Path) -> str:
        value = str(path)
        if value.startswith("\\\\?\\"):
            return value
        if value.startswith("\\\\"):
            return "\\\\?\\UNC\\" + value[2:]
        return "\\\\?\\" + value

    def _open_directory(path: Path) -> int:
        handle = kernel32.CreateFileW(
            _native_path(path),
            # Attribute-only access does not reliably participate in Windows
            # share accounting. Directory-list access makes omission of
            # FILE_SHARE_DELETE an effective rename/delete guard.
            file_list_directory | file_read_attributes,
            file_share_read | file_share_write,
            None,
            open_existing,
            file_flag_backup_semantics | file_flag_open_reparse_point,
            None,
        )
        if handle == invalid_handle_value:
            error = ctypes.get_last_error()
            if error in {2, 3}:
                raise FileNotFoundError(error, "path is absent", str(path))
            raise ctypes.WinError(error)
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        if (
            info.attributes & file_attribute_reparse_point
            or not info.attributes & file_attribute_directory
        ):
            kernel32.CloseHandle(handle)
            raise StoragePolicyError("policy_path_redirect")
        return int(handle)

    try:
        if not anchor_root.is_absolute() or not anchor_root.anchor:
            raise StoragePolicyError("anchor_root_uninspectable")
        current = Path(anchor_root.anchor)
        for component in (None, *anchor_root.parts[1:]):
            if component is not None:
                current /= component
            handles.append(_open_directory(current))

        named_anchor = anchor_root.lstat()
        if (
            stat.S_ISLNK(named_anchor.st_mode)
            or not stat.S_ISDIR(named_anchor.st_mode)
            or not os.path.samestat(expected_anchor, named_anchor)
        ):
            raise StoragePolicyError("anchor_root_changed")

        state_path = anchor_root / "state"
        try:
            state_handle = _open_directory(state_path)
        except FileNotFoundError:
            if expected_state is not None:
                raise StoragePolicyError("policy_path_changed")
            try:
                state_path.lstat()
            except FileNotFoundError:
                return handles
            raise StoragePolicyError("policy_path_changed")
        handles.append(state_handle)
        named_state = state_path.lstat()
        if (
            expected_state is None
            or stat.S_ISLNK(named_state.st_mode)
            or not stat.S_ISDIR(named_state.st_mode)
            or not os.path.samestat(expected_state, named_state)
        ):
            raise StoragePolicyError("policy_path_changed")
        return handles
    except BaseException:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)
        raise


def _close_windows_policy_directory_guards(handles: list[int]) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    for handle in reversed(handles):
        kernel32.CloseHandle(handle)


def _revalidate_windows_state_directories(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    expected_state: os.stat_result,
) -> None:
    try:
        named_anchor = anchor_root.lstat()
        named_state = (anchor_root / "state").lstat()
    except OSError as exc:
        raise StoragePolicyError("policy_path_changed") from exc
    if not os.path.samestat(expected_anchor, named_anchor):
        raise StoragePolicyError("anchor_root_changed")
    if not os.path.samestat(expected_state, named_state):
        raise StoragePolicyError("policy_path_changed")


def _validate_fixed_anchor_state_filename(filename: str) -> str:
    normalized = str(filename or "")
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
    ):
        raise StoragePolicyError("policy_path_uninspectable")
    return normalized


def _open_or_create_posix_directory_at(
    parent_fd: int,
    name: str,
    *,
    create: bool,
) -> int:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            _fsync_opened_policy_directory_required(parent_fd)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileExistsError:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise StoragePolicyError("policy_path_uninspectable") from exc
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise StoragePolicyError("policy_path_redirect")
    try:
        directory_fd = os.open(name, _posix_directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    opened = os.fstat(directory_fd)
    if not stat.S_ISDIR(opened.st_mode) or not os.path.samestat(named, opened):
        os.close(directory_fd)
        raise StoragePolicyError("policy_path_changed")
    return directory_fd


def _open_or_create_posix_anchor_state_directories(
    anchor_root: Path,
) -> tuple[int, int]:
    """Create and pin the fixed anchor/state chain without following redirects."""

    if not anchor_root.is_absolute() or not anchor_root.anchor:
        raise StoragePolicyError("anchor_root_uninspectable")
    directory_fd = -1
    anchor_fd = -1
    state_fd = -1
    try:
        directory_fd = os.open(anchor_root.anchor, _posix_directory_open_flags())
        for component in anchor_root.parts[1:]:
            child_fd = _open_or_create_posix_directory_at(
                directory_fd,
                component,
                create=True,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        anchor_fd = directory_fd
        directory_fd = -1
        state_fd = _open_or_create_posix_directory_at(
            anchor_fd,
            "state",
            create=True,
        )
        _revalidate_posix_state_directories(
            anchor_root,
            anchor_fd,
            state_fd,
        )
        return anchor_fd, state_fd
    except BaseException:
        for fd in (state_fd, anchor_fd, directory_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
        raise


def _write_fixed_anchor_state_json_posix(
    anchor_root: Path,
    filename: str,
    encoded_payload: bytes,
) -> None:
    anchor_fd = -1
    state_fd = -1
    temp_fd = -1
    temp_name = f".neko-state-{uuid.uuid4().hex}.tmp"
    temp_created = False
    try:
        anchor_fd, state_fd = _open_or_create_posix_anchor_state_directories(
            anchor_root
        )
        try:
            existing = os.stat(filename, dir_fd=state_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StoragePolicyError("policy_path_uninspectable") from exc
        else:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise StoragePolicyError("policy_path_redirect")

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=state_fd)
        temp_created = True
        view = memoryview(encoded_payload)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OSError("short write while publishing fixed-anchor state")
            view = view[written:]
        os.fsync(temp_fd)
        opened_temp = os.fstat(temp_fd)
        named_temp = os.stat(
            temp_name,
            dir_fd=state_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened_temp.st_mode)
            or not stat.S_ISREG(named_temp.st_mode)
            or not os.path.samestat(opened_temp, named_temp)
        ):
            raise StoragePolicyError("policy_path_changed")
        _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
        os.replace(
            temp_name,
            filename,
            src_dir_fd=state_fd,
            dst_dir_fd=state_fd,
        )
        temp_created = False
        published = os.stat(
            filename,
            dir_fd=state_fd,
            follow_symlinks=False,
        )
        if not os.path.samestat(opened_temp, published):
            raise StoragePolicyError("policy_path_changed")
        _fsync_opened_policy_directory_required(state_fd)
        _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
    except StoragePolicyError:
        raise
    except OSError as exc:
        raise StoragePolicyError("policy_write_failed") from exc
    finally:
        if temp_fd >= 0:
            with suppress(OSError):
                os.close(temp_fd)
        if temp_created and state_fd >= 0:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=state_fd)
        for fd in (state_fd, anchor_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


def _write_fixed_anchor_state_json_windows(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    expected_state: os.stat_result,
    filename: str,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
) -> None:
    handles = _open_windows_policy_directory_guards(
        anchor_root,
        expected_anchor,
        expected_state,
    )
    state_path = anchor_root / "state"
    target_path = state_path / filename
    try:
        _revalidate_windows_state_directories(
            anchor_root,
            expected_anchor,
            expected_state,
        )
        try:
            existing = target_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StoragePolicyError("policy_path_uninspectable") from exc
        else:
            is_reparse_point = bool(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                & getattr(existing, "st_file_attributes", 0)
            )
            if (
                stat.S_ISLNK(existing.st_mode)
                or is_reparse_point
                or not stat.S_ISREG(existing.st_mode)
            ):
                raise StoragePolicyError("policy_path_redirect")
        atomic_write_json(
            target_path,
            payload,
            ensure_ascii=ensure_ascii,
            indent=indent,
        )
        _fsync_policy_directory_required(state_path)
        _revalidate_windows_state_directories(
            anchor_root,
            expected_anchor,
            expected_state,
        )
    except StoragePolicyError:
        raise
    except OSError as exc:
        raise StoragePolicyError("policy_write_failed") from exc
    finally:
        _close_windows_policy_directory_guards(handles)


def write_fixed_anchor_state_json(
    configured_anchor_root: Path | str,
    filename: str,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
) -> None:
    """Durably publish one JSON authority file inside the fixed anchor state dir."""

    filename = _validate_fixed_anchor_state_filename(filename)
    raw_anchor_root = Path(configured_anchor_root).expanduser()
    _validate_storage_policy_anchor(raw_anchor_root)
    anchor_root = Path(os.path.abspath(os.fspath(raw_anchor_root)))
    encoded_payload = json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
    ).encode("utf-8")
    _validate_fixed_anchor_json_size(len(encoded_payload))
    if os.name != "nt":
        _write_fixed_anchor_state_json_posix(
            anchor_root,
            filename,
            encoded_payload,
        )
        return

    anchor_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_path = anchor_root / "state"
    state_path.mkdir(mode=0o700, exist_ok=True)
    expected_anchor = _validate_storage_policy_anchor(anchor_root)
    if expected_anchor is None:
        raise StoragePolicyError("anchor_root_changed")
    try:
        expected_state = state_path.lstat()
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    is_reparse_point = bool(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        & getattr(expected_state, "st_file_attributes", 0)
    )
    if (
        stat.S_ISLNK(expected_state.st_mode)
        or is_reparse_point
        or not stat.S_ISDIR(expected_state.st_mode)
    ):
        raise StoragePolicyError("policy_path_redirect")
    _write_fixed_anchor_state_json_windows(
        anchor_root,
        expected_anchor,
        expected_state,
        filename,
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
    )


def _delete_fixed_anchor_state_json_posix(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    filename: str,
) -> None:
    anchor_fd = -1
    state_fd = -1
    try:
        anchor_fd = _open_posix_anchor_directory(anchor_root, expected_anchor)
        try:
            state_fd = _open_posix_child_directory(anchor_fd, "state")
        except FileNotFoundError:
            _revalidate_posix_policy_absence(
                anchor_root,
                anchor_fd,
                -1,
                filename,
            )
            return
        _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
        try:
            existing = os.stat(filename, dir_fd=state_fd, follow_symlinks=False)
        except FileNotFoundError:
            _revalidate_posix_policy_absence(
                anchor_root,
                anchor_fd,
                state_fd,
                filename,
            )
            return
        except OSError as exc:
            raise StoragePolicyError("policy_path_uninspectable") from exc
        if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
            raise StoragePolicyError("policy_path_redirect")
        os.unlink(filename, dir_fd=state_fd)
        _fsync_opened_policy_directory_required(state_fd)
        _revalidate_posix_state_directories(anchor_root, anchor_fd, state_fd)
    except StoragePolicyError:
        raise
    except OSError as exc:
        raise StoragePolicyError("policy_delete_failed") from exc
    finally:
        for fd in (state_fd, anchor_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


def _delete_fixed_anchor_state_json_windows(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    expected_state: os.stat_result,
    filename: str,
) -> None:
    handles = _open_windows_policy_directory_guards(
        anchor_root,
        expected_anchor,
        expected_state,
    )
    target_path = anchor_root / "state" / filename
    try:
        _revalidate_windows_state_directories(
            anchor_root,
            expected_anchor,
            expected_state,
        )
        try:
            existing = target_path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StoragePolicyError("policy_path_uninspectable") from exc
        is_reparse_point = bool(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            & getattr(existing, "st_file_attributes", 0)
        )
        if (
            stat.S_ISLNK(existing.st_mode)
            or is_reparse_point
            or not stat.S_ISREG(existing.st_mode)
        ):
            raise StoragePolicyError("policy_path_redirect")
        os.unlink(target_path)
        _fsync_policy_directory_required(target_path.parent)
        _revalidate_windows_state_directories(
            anchor_root,
            expected_anchor,
            expected_state,
        )
    except StoragePolicyError:
        raise
    except OSError as exc:
        raise StoragePolicyError("policy_delete_failed") from exc
    finally:
        _close_windows_policy_directory_guards(handles)


def _confirm_windows_fixed_anchor_state_absent(
    anchor_root: Path,
    expected_anchor: os.stat_result,
) -> None:
    """Prove state is absent while the original anchor name cannot be swapped."""

    handles = _open_windows_policy_directory_guards(
        anchor_root,
        expected_anchor,
        None,
    )
    try:
        refreshed_anchor = _validate_storage_policy_anchor(anchor_root)
        if refreshed_anchor is None or not os.path.samestat(
            expected_anchor,
            refreshed_anchor,
        ):
            raise StoragePolicyError("anchor_root_changed")
        try:
            (anchor_root / "state").lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StoragePolicyError("policy_path_uninspectable") from exc
        raise StoragePolicyError("policy_path_changed")
    finally:
        _close_windows_policy_directory_guards(handles)


def delete_fixed_anchor_state_json(
    configured_anchor_root: Path | str,
    filename: str,
) -> None:
    """Durably remove one JSON authority file without following a replaced state dir."""

    filename = _validate_fixed_anchor_state_filename(filename)
    raw_anchor_root = Path(configured_anchor_root).expanduser()
    expected_anchor = _validate_storage_policy_anchor(raw_anchor_root)
    anchor_root = Path(os.path.abspath(os.fspath(raw_anchor_root)))
    if expected_anchor is None:
        if _validate_storage_policy_anchor(raw_anchor_root) is not None:
            raise StoragePolicyError("anchor_root_changed")
        return
    if os.name != "nt":
        _delete_fixed_anchor_state_json_posix(
            anchor_root,
            expected_anchor,
            filename,
        )
        return

    state_path = anchor_root / "state"
    try:
        expected_state = state_path.lstat()
    except FileNotFoundError:
        _confirm_windows_fixed_anchor_state_absent(
            anchor_root,
            expected_anchor,
        )
        return
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    is_reparse_point = bool(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        & getattr(expected_state, "st_file_attributes", 0)
    )
    if (
        stat.S_ISLNK(expected_state.st_mode)
        or is_reparse_point
        or not stat.S_ISDIR(expected_state.st_mode)
    ):
        raise StoragePolicyError("policy_path_redirect")
    _delete_fixed_anchor_state_json_windows(
        anchor_root,
        expected_anchor,
        expected_state,
        filename,
    )


def _restore_storage_policy_snapshot_windows(
    anchor_root: Path,
    expected_anchor: os.stat_result,
    expected_state: os.stat_result | None,
    policy_path: Path,
    policy_payload: dict[str, Any] | None,
) -> None:
    handles = _open_windows_policy_directory_guards(
        anchor_root,
        expected_anchor,
        expected_state,
    )
    try:
        if expected_state is None:
            if policy_payload is not None:
                raise StoragePolicyError("policy_path_changed")
            return
        _revalidate_windows_state_directories(
            anchor_root,
            expected_anchor,
            expected_state,
        )
        if policy_payload is None:
            try:
                os.unlink(policy_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StoragePolicyError("policy_restore_delete_failed") from exc
        else:
            atomic_write_json(
                policy_path,
                policy_payload,
                ensure_ascii=False,
                indent=2,
            )
        _fsync_policy_directory_required(policy_path.parent)
        _revalidate_windows_state_directories(
            anchor_root,
            expected_anchor,
            expected_state,
        )
    finally:
        _close_windows_policy_directory_guards(handles)


def _read_storage_policy_json_posix(
    anchor_root: Path,
    expected_anchor: os.stat_result | None,
    filename: str,
) -> Any:
    anchor_fd = -1
    state_fd = -1
    policy_fd = -1
    try:
        anchor_fd = _open_posix_anchor_directory(anchor_root, expected_anchor)
        try:
            state_fd = _open_posix_child_directory(anchor_fd, "state")
        except FileNotFoundError:
            _revalidate_posix_policy_absence(anchor_root, anchor_fd, -1, filename)
            raise
        try:
            policy_fd, policy_before = _open_posix_policy_file(state_fd, filename)
        except FileNotFoundError:
            _revalidate_posix_policy_absence(anchor_root, anchor_fd, state_fd, filename)
            raise

        _validate_fixed_anchor_json_size(policy_before.st_size)
        raw = _read_open_file_descriptor(policy_fd)
        _revalidate_posix_policy_handles(
            anchor_root,
            anchor_fd,
            state_fd,
            policy_fd,
            policy_before,
            filename,
        )
        return json.loads(raw.decode("utf-8"))
    finally:
        for fd in (policy_fd, state_fd, anchor_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _read_storage_policy_json_windows(
    anchor_root: Path,
    expected_anchor: os.stat_result | None,
    filename: str,
) -> Any:
    """Read through stable Win32 handles and reject every reparse component."""

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
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    generic_read = 0x80000000
    file_read_attributes = 0x0080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_attribute_directory = 0x0010
    file_attribute_reparse_point = 0x0400
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle_value = ctypes.c_void_p(-1).value
    absent_errors = {2, 3}
    handles: list[int] = []

    def _native_path(path: Path) -> str:
        value = str(path)
        if value.startswith("\\\\?\\"):
            return value
        if value.startswith("\\\\"):
            return "\\\\?\\UNC\\" + value[2:]
        return "\\\\?\\" + value

    def _snapshot(handle: int) -> tuple[int, int, int, int, int]:
        info = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (
            int(info.attributes),
            int(info.volume_serial_number),
            (int(info.file_index_high) << 32) | int(info.file_index_low),
            (int(info.file_size_high) << 32) | int(info.file_size_low),
            (int(info.last_write_time.high) << 32) | int(info.last_write_time.low),
        )

    def _open_handle(path: Path, *, directory: bool) -> tuple[int, tuple[int, int, int, int, int]]:
        flags = file_flag_open_reparse_point
        if directory:
            flags |= file_flag_backup_semantics
        access = file_read_attributes if directory else generic_read | file_read_attributes
        # Denying DELETE sharing on directories keeps every opened path
        # component from being renamed while the full child path is opened.
        share_mode = file_share_read | file_share_write
        if not directory:
            share_mode |= file_share_delete
        handle = kernel32.CreateFileW(
            _native_path(path),
            access,
            share_mode,
            None,
            open_existing,
            flags,
            None,
        )
        if handle == invalid_handle_value:
            error = ctypes.get_last_error()
            if error in absent_errors:
                raise FileNotFoundError(error, "path is absent", str(path))
            raise ctypes.WinError(error)
        try:
            snapshot = _snapshot(handle)
        except Exception:
            kernel32.CloseHandle(handle)
            raise
        is_directory = bool(snapshot[0] & file_attribute_directory)
        if snapshot[0] & file_attribute_reparse_point or is_directory != directory:
            kernel32.CloseHandle(handle)
            raise StoragePolicyError("policy_path_redirect")
        return handle, snapshot

    def _close_handle(handle: int) -> None:
        if handle != invalid_handle_value:
            kernel32.CloseHandle(handle)

    def _named_snapshot(path: Path, *, directory: bool) -> tuple[int, int, int, int, int]:
        handle, snapshot = _open_handle(path, directory=directory)
        try:
            return snapshot
        finally:
            _close_handle(handle)

    def _identity(snapshot: tuple[int, int, int, int, int]) -> tuple[int, int]:
        return snapshot[1], snapshot[2]

    def _revalidate_absence(
        missing_path: Path,
        *,
        directory: bool,
        anchor_snapshot: tuple[int, int, int, int, int],
        state_path: Path | None = None,
        state_snapshot: tuple[int, int, int, int, int] | None = None,
    ) -> None:
        # Check absence first and finish on the stable parent identity. Reversing
        # this order would recreate the detached-anchor first-run race.
        try:
            _named_snapshot(missing_path, directory=directory)
        except FileNotFoundError:
            pass
        else:
            raise StoragePolicyError("policy_changed_during_read")

        refreshed_anchor = _validate_storage_policy_anchor(anchor_root)
        if (
            refreshed_anchor is None
            or expected_anchor is None
            or not os.path.samestat(expected_anchor, refreshed_anchor)
            or _identity(anchor_snapshot)
            != _identity(_named_snapshot(anchor_root, directory=True))
        ):
            raise StoragePolicyError("anchor_root_changed")
        if state_path is not None and state_snapshot is not None and (
            _identity(state_snapshot)
            != _identity(_named_snapshot(state_path, directory=True))
        ):
            raise StoragePolicyError("policy_changed_during_read")

    try:
        if not anchor_root.is_absolute() or not anchor_root.anchor:
            raise StoragePolicyError("anchor_root_uninspectable")

        current = Path(anchor_root.anchor)
        try:
            handle, _root_snapshot = _open_handle(current, directory=True)
        except FileNotFoundError as exc:
            raise StoragePolicyError("anchor_root_changed") from exc
        handles.append(handle)
        anchor_snapshot = _root_snapshot
        for component in anchor_root.parts[1:]:
            current /= component
            try:
                handle, anchor_snapshot = _open_handle(current, directory=True)
            except FileNotFoundError as exc:
                if expected_anchor is None:
                    if _validate_storage_policy_anchor(anchor_root) is not None:
                        raise StoragePolicyError("anchor_root_changed") from exc
                    raise
                raise StoragePolicyError("anchor_root_changed") from exc
            handles.append(handle)

        if expected_anchor is None:
            raise StoragePolicyError("anchor_root_changed")
        try:
            current_anchor = anchor_root.lstat()
        except OSError as exc:
            raise StoragePolicyError("anchor_root_changed") from exc
        if (
            not os.path.samestat(expected_anchor, current_anchor)
            or _identity(anchor_snapshot)
            != _identity(_named_snapshot(anchor_root, directory=True))
        ):
            raise StoragePolicyError("anchor_root_changed")

        state_path = anchor_root / "state"
        try:
            state_handle, state_snapshot = _open_handle(state_path, directory=True)
        except FileNotFoundError:
            _revalidate_absence(
                state_path,
                directory=True,
                anchor_snapshot=anchor_snapshot,
            )
            raise
        handles.append(state_handle)

        policy_path = state_path / filename
        try:
            policy_handle, policy_before = _open_handle(policy_path, directory=False)
        except FileNotFoundError:
            _revalidate_absence(
                policy_path,
                directory=False,
                anchor_snapshot=anchor_snapshot,
                state_path=state_path,
                state_snapshot=state_snapshot,
            )
            raise
        handles.append(policy_handle)

        _validate_fixed_anchor_json_size(policy_before[3])

        def _read_chunk(size: int) -> bytes:
            buffer = ctypes.create_string_buffer(size)
            read_count = wintypes.DWORD()
            if not kernel32.ReadFile(
                policy_handle,
                buffer,
                size,
                ctypes.byref(read_count),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer.raw[: read_count.value]

        raw = _read_fixed_anchor_json_chunks(_read_chunk)

        policy_after = _snapshot(policy_handle)
        refreshed_anchor = _validate_storage_policy_anchor(anchor_root)
        if refreshed_anchor is None or not os.path.samestat(expected_anchor, refreshed_anchor):
            raise StoragePolicyError("anchor_root_changed")
        if (
            _identity(anchor_snapshot) != _identity(_named_snapshot(anchor_root, directory=True))
            or _identity(state_snapshot) != _identity(_named_snapshot(state_path, directory=True))
            or _identity(policy_after) != _identity(_named_snapshot(policy_path, directory=False))
            or policy_before[3:] != policy_after[3:]
        ):
            raise StoragePolicyError("policy_changed_during_read")
        return json.loads(raw.decode("utf-8"))
    finally:
        for handle in reversed(handles):
            _close_handle(handle)


def read_fixed_anchor_state_json(
    configured_anchor_root: Path | str,
    filename: str,
) -> Any:
    """Read one fixed-anchor state JSON through a verified directory chain."""

    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise StoragePolicyError("policy_path_uninspectable")
    expected_anchor = _validate_storage_policy_anchor(configured_anchor_root)
    anchor_root = normalize_runtime_root(configured_anchor_root)
    if os.name == "nt":
        return _read_storage_policy_json_windows(anchor_root, expected_anchor, filename)
    return _read_storage_policy_json_posix(anchor_root, expected_anchor, filename)


def _read_storage_policy_json(
    anchor_root: Path,
    expected_anchor: os.stat_result | None,
) -> Any:
    """Preserve the policy reader seam while sharing its anchored implementation."""

    if os.name == "nt":
        return _read_storage_policy_json_windows(
            anchor_root,
            expected_anchor,
            "storage_policy.json",
        )
    return _read_storage_policy_json_posix(
        anchor_root,
        expected_anchor,
        "storage_policy.json",
    )


def load_storage_policy(
    config_manager,
    *,
    anchor_root: Path | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    configured_anchor_root = anchor_root or compute_anchor_root(config_manager)
    expected_anchor = _validate_storage_policy_anchor(configured_anchor_root)
    normalized_anchor_root = normalize_runtime_root(configured_anchor_root)
    policy_path = normalized_anchor_root / "state" / "storage_policy.json"
    try:
        payload = _read_storage_policy_json(normalized_anchor_root, expected_anchor)
    except FileNotFoundError:
        return default
    except StoragePolicyError:
        raise
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
            anchor_root=normalized_anchor_root,
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
    raw_selected_root = Path(str(selected_root or "")).expanduser()
    try:
        if _path_chain_redirect_status(raw_selected_root):
            raise StoragePolicyError("selected_root_redirect")
    except OSError as exc:
        raise StoragePolicyError("selected_root_uninspectable") from exc
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
    missing_directories: list[Path] = []
    candidate = policy_path.parent
    while not candidate.exists() and candidate.parent != candidate:
        missing_directories.append(candidate)
        candidate = candidate.parent
    atomic_write_json(policy_path, policy_payload, ensure_ascii=False, indent=2)
    _fsync_policy_directory_required(policy_path.parent)
    for created_directory in missing_directories:
        _fsync_policy_directory_required(created_directory.parent)
    return policy_payload


def restore_storage_policy_snapshot(
    config_manager,
    policy_payload: dict[str, Any] | None,
    *,
    anchor_root: Path | str | None = None,
) -> None:
    """Restore an exact pre-mutation policy fact with a required directory barrier."""

    configured_anchor_root = anchor_root or compute_anchor_root(config_manager)
    expected_anchor = _validate_storage_policy_anchor(configured_anchor_root)
    normalized_anchor_root = normalize_runtime_root(configured_anchor_root)
    policy_path = get_storage_policy_path(
        config_manager,
        anchor_root=normalized_anchor_root,
    )
    encoded_payload: bytes | None = None
    if isinstance(policy_payload, dict):
        _validate_storage_policy_payload(
            config_manager,
            policy_payload,
            anchor_root=normalized_anchor_root,
        )
        encoded_payload = json.dumps(
            policy_payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        _validate_fixed_anchor_json_size(len(encoded_payload))
    elif policy_payload is not None:
        raise StoragePolicyError("invalid_policy_snapshot")

    if expected_anchor is None:
        if encoded_payload is not None:
            raise StoragePolicyError("anchor_root_changed")
        if _validate_storage_policy_anchor(configured_anchor_root) is not None:
            raise StoragePolicyError("anchor_root_changed")
        return

    state_path = normalized_anchor_root / "state"
    try:
        expected_state = state_path.lstat()
    except FileNotFoundError:
        expected_state = None
    except OSError as exc:
        raise StoragePolicyError("policy_path_uninspectable") from exc
    else:
        is_reparse_point = bool(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            & getattr(expected_state, "st_file_attributes", 0)
        )
        if (
            stat.S_ISLNK(expected_state.st_mode)
            or is_reparse_point
            or not stat.S_ISDIR(expected_state.st_mode)
        ):
            raise StoragePolicyError("policy_path_redirect")

    if os.name == "nt":
        _restore_storage_policy_snapshot_windows(
            normalized_anchor_root,
            expected_anchor,
            expected_state,
            policy_path,
            policy_payload,
        )
        return
    _restore_storage_policy_snapshot_posix(
        normalized_anchor_root,
        expected_anchor,
        expected_state,
        encoded_payload,
    )


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
    if expanded_selected_root.is_absolute() and path_chain_has_symlink(expanded_selected_root):
        raise StorageSelectionValidationError(
            "selected_root_symlink_unsupported",
            "目标路径及其父路径不能包含符号链接或重解析点。",
        )

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
