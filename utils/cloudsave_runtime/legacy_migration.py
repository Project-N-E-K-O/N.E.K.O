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

"""One-shot bootstrap import of legacy runtime roots into the deterministic
app data root, including config/character merge heuristics.

Split out of the former monolithic ``utils/cloudsave_runtime.py``.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from config import DEFAULT_CONFIG_DATA
from utils.file_utils import atomic_write_json
from utils.storage.policy import PathIdentityUnavailable, paths_equal
from utils.storage_path_rewrite import rebase_runtime_bound_workshop_config_paths

from ._shared import (
    CloudsaveOperationError,
    LEGACY_OPTIONAL_STATE_FILES,
    LEGACY_RUNTIME_DIR_NAMES,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    ROOT_MODE_NORMAL,
    ROOT_CONFIG_MERGE_FILES,
    RUNTIME_CACHE_DIR_NAMES,
    RUNTIME_STORAGE_RELATIVE_PATHS,
    RUNTIME_USER_CONTENT_DIR_NAMES,
    TRANSACTIONAL_RUNTIME_ENTRY_PATTERNS,
    RUNTIME_ASSET_DIR_NAMES,
    TARGET_OPTIONAL_STATE_FILES,
)
from .staging import (
    _json_canonical_dumps,
    _load_json_if_exists,
    _load_tombstone_names_from_state_path,
)


_LEGACY_PREPARE_STATE_FIELD = "legacy_import_preparation"
_LEGACY_PREPARE_VERSION = 1
_LEGACY_PREPARE_PREFIXES = {
    "source": "legacy-source",
    "target": "legacy-target",
    "anchor": "legacy-anchor",
    "backup": "legacy-backup",
    "config": "legacy-config",
}


def _persist_legacy_import_preparation(
    config_manager,
    *,
    attempt_id: str,
    paths: dict[str, Path],
) -> None:
    from utils.root_state_lock import root_state_transaction

    with root_state_transaction():
        root_state = config_manager.load_root_state()
        existing = root_state.get(_LEGACY_PREPARE_STATE_FIELD)
        if isinstance(existing, dict):
            raise _unsafe_legacy_runtime_entry(
                Path(config_manager.app_docs_dir),
                "legacy_preparation_already_active",
            )
        root_state[_LEGACY_PREPARE_STATE_FIELD] = {
            "version": _LEGACY_PREPARE_VERSION,
            "attempt_id": attempt_id,
            "paths": {role: str(path) for role, path in paths.items()},
            "identities": {},
        }
        config_manager.save_root_state(root_state)


def _validate_prepared_legacy_directory(
    path: Path,
    expected_identity: os.stat_result,
) -> os.stat_result:
    try:
        current = path.lstat()
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(
            path,
            "legacy_preparation_path_changed",
        ) from exc
    if (
        _is_link_like_metadata(current)
        or not stat.S_ISDIR(current.st_mode)
        or not os.path.samestat(expected_identity, current)
    ):
        raise _unsafe_legacy_runtime_entry(
            path,
            "legacy_preparation_path_changed",
        )
    return current


def _record_legacy_prepared_directory_identity(
    config_manager,
    *,
    attempt_id: str,
    paths: dict[str, Path],
    role: str,
    identity: os.stat_result,
) -> None:
    from utils.root_state_lock import root_state_transaction

    if role not in paths:
        raise _unsafe_legacy_runtime_entry(
            Path(config_manager.app_docs_dir),
            "legacy_preparation_role_invalid",
        )
    with root_state_transaction():
        root_state = config_manager.load_root_state()
        preparation = root_state.get(_LEGACY_PREPARE_STATE_FIELD)
        if (
            not isinstance(preparation, dict)
            or preparation.get("attempt_id") != attempt_id
            or preparation.get("paths")
            != {role: str(path) for role, path in paths.items()}
        ):
            raise _unsafe_legacy_runtime_entry(
                Path(config_manager.app_docs_dir),
                "legacy_preparation_authority_changed",
            )
        persisted_identities = dict(preparation.get("identities") or {})
        if role in persisted_identities:
            raise _unsafe_legacy_runtime_entry(
                paths[role],
                "legacy_preparation_identity_already_recorded",
            )
        _validate_prepared_legacy_directory(paths[role], identity)
        persisted_identities[role] = [
            int(identity.st_dev),
            int(identity.st_ino),
        ]
        updated = dict(preparation)
        updated["identities"] = persisted_identities
        root_state[_LEGACY_PREPARE_STATE_FIELD] = updated
        config_manager.save_root_state(root_state)


def _record_all_legacy_prepared_directory_identities(
    config_manager,
    *,
    attempt_id: str,
    paths: dict[str, Path],
    identities: dict[str, os.stat_result],
) -> None:
    """Compatibility helper for tests and callers with already-created roots."""

    for role in paths:
        identity = identities.get(role)
        if identity is None:
            raise _unsafe_legacy_runtime_entry(
                paths[role],
                "legacy_preparation_identity_missing",
            )
        _record_legacy_prepared_directory_identity(
            config_manager,
            attempt_id=attempt_id,
            paths=paths,
            role=role,
            identity=identity,
        )


def _clear_legacy_import_preparation(config_manager, attempt_id: str) -> None:
    from utils.root_state_lock import root_state_transaction

    with root_state_transaction():
        root_state = config_manager.load_root_state()
        preparation = root_state.get(_LEGACY_PREPARE_STATE_FIELD)
        if not isinstance(preparation, dict):
            return
        if preparation.get("attempt_id") != attempt_id:
            raise _unsafe_legacy_runtime_entry(
                Path(config_manager.app_docs_dir),
                "legacy_preparation_authority_changed",
            )
        root_state.pop(_LEGACY_PREPARE_STATE_FIELD, None)
        config_manager.save_root_state(root_state)


def _remove_prepared_legacy_directory(
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> None:
    from utils.storage.migration import (
        _durable_rename_without_replacing,
        _private_directory_quarantine_path,
        _remove_owned_private_directory,
        _remove_private_directory_via_quarantine,
    )

    quarantine = _private_directory_quarantine_path(path)
    try:
        named = path.lstat()
    except FileNotFoundError:
        named = None
    try:
        quarantined = quarantine.lstat()
    except FileNotFoundError:
        quarantined = None
    if named is not None and quarantined is not None:
        raise _unsafe_legacy_runtime_entry(path, "legacy_preparation_cleanup_ambiguous")
    if expected_identity is None:
        if quarantined is not None:
            raise _unsafe_legacy_runtime_entry(
                quarantine,
                "legacy_preparation_identity_missing",
            )
        if named is None:
            return
        # A crash may happen after mkdir but before its inode reaches the
        # ledger.  The random name alone is never deletion authority: another
        # process may have replaced even an empty directory at that name.
        raise _unsafe_legacy_runtime_entry(
            path,
            "legacy_preparation_identity_missing",
        )
    if quarantined is not None:
        observed = (int(quarantined.st_dev), int(quarantined.st_ino))
        if (
            _is_link_like_metadata(quarantined)
            or not stat.S_ISDIR(quarantined.st_mode)
            or observed != expected_identity
        ):
            raise _unsafe_legacy_runtime_entry(
                quarantine,
                "legacy_preparation_path_changed",
            )
        _durable_rename_without_replacing(quarantine, path)
        named = path.lstat()
    if named is None:
        return
    observed = (int(named.st_dev), int(named.st_ino))
    if (
        _is_link_like_metadata(named)
        or not stat.S_ISDIR(named.st_mode)
        or observed != expected_identity
    ):
        raise _unsafe_legacy_runtime_entry(path, "legacy_preparation_path_changed")
    if not _remove_private_directory_via_quarantine(
        path,
        named,
        remove_quarantine=lambda owned_quarantine: _remove_owned_private_directory(
            owned_quarantine,
            named,
        ),
    ):
        raise _unsafe_legacy_runtime_entry(path, "legacy_preparation_cleanup_unverified")


def recover_abandoned_legacy_import_preparation(config_manager) -> None:
    """Remove only private workspaces whose exact identities are in root_state."""

    root_state = config_manager.load_root_state()
    preparation = root_state.get(_LEGACY_PREPARE_STATE_FIELD)
    if not isinstance(preparation, dict):
        return
    attempt_id = str(preparation.get("attempt_id") or "")
    raw_paths = preparation.get("paths")
    raw_identities = preparation.get("identities")
    if (
        preparation.get("version") != _LEGACY_PREPARE_VERSION
        or not attempt_id
        or not isinstance(raw_paths, dict)
        or set(raw_paths) != set(_LEGACY_PREPARE_PREFIXES)
        or not isinstance(raw_identities, dict)
    ):
        raise _unsafe_legacy_runtime_entry(
            Path(config_manager.app_docs_dir),
            "invalid_legacy_preparation",
        )

    target_root = Path(config_manager.app_docs_dir)
    prepared_paths: dict[str, Path] = {}
    prepared_identities: dict[str, tuple[int, int] | None] = {}
    for role, prefix in _LEGACY_PREPARE_PREFIXES.items():
        prepared = Path(str(raw_paths.get(role) or ""))
        try:
            parent_matches = paths_equal(prepared.parent, target_root.parent)
        except (OSError, PathIdentityUnavailable, ValueError):
            parent_matches = False
        if not parent_matches or not prepared.name.startswith(
            f".{target_root.name}.{prefix}-"
        ):
            raise _unsafe_legacy_runtime_entry(prepared, "invalid_legacy_preparation")
        identity = raw_identities.get(role)
        if identity is None:
            prepared_identities[role] = None
        elif (
            isinstance(identity, list)
            and len(identity) == 2
            and all(isinstance(value, int) for value in identity)
        ):
            prepared_identities[role] = (identity[0], identity[1])
        else:
            raise _unsafe_legacy_runtime_entry(prepared, "invalid_legacy_preparation")
        prepared_paths[role] = prepared

    from utils.storage.migration import is_storage_migration_pending, load_storage_migration

    migration = load_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    protected_roles: set[str] = set()
    if isinstance(migration, dict) and migration.get(
        "legacy_import_kind"
    ) == _LEGACY_IMPORT_CHECKPOINT_KIND:
        if str(migration.get("source_root") or "") != str(prepared_paths["source"]):
            raise _unsafe_legacy_runtime_entry(
                prepared_paths["source"],
                "legacy_preparation_checkpoint_mismatch",
            )
        if str(migration.get("legacy_import_backup_path") or "") != str(
            prepared_paths["backup"]
        ):
            raise _unsafe_legacy_runtime_entry(
                prepared_paths["backup"],
                "legacy_preparation_checkpoint_mismatch",
            )
        protected_roles.update({"source", "backup"})
    elif isinstance(migration, dict) and is_storage_migration_pending(migration):
        raise _unsafe_legacy_runtime_entry(
            target_root,
            "legacy_preparation_checkpoint_conflict",
        )

    for role, path in prepared_paths.items():
        if role not in protected_roles and prepared_identities[role] is not None:
            _remove_prepared_legacy_directory(path, prepared_identities[role])
        # A process can die after mkdir and before the directory identity is
        # durably recorded.  Such a path is not ours to delete, but it also
        # must not permanently pin startup to an attempt that can never prove
        # ownership.  Retire the ledger and leave the unauthenticated orphan
        # untouched; the next attempt uses a fresh random name.
    _clear_legacy_import_preparation(config_manager, attempt_id)


def _runtime_config_path_matches_pristine_default(config_manager, runtime_path: Path) -> bool:
    try:
        runtime_metadata = runtime_path.lstat()
    except OSError:
        return False
    if _is_link_like_metadata(runtime_metadata) or not stat.S_ISREG(runtime_metadata.st_mode):
        return False
    try:
        from utils.storage.community_private_state import _read_stable_regular_file

        runtime_bytes, _runtime_after = _read_stable_regular_file(
            runtime_path,
            runtime_metadata,
            max_bytes=16 * 1024 * 1024,
        )
    except OSError:
        return False

    source_path = None
    if runtime_path.name == "characters.json":
        localized_source = getattr(config_manager, "_get_localized_characters_source", lambda: None)()
        if localized_source:
            source_path = Path(localized_source)
    if source_path is None:
        project_config_dir = getattr(config_manager, "project_config_dir", None)
        if project_config_dir is not None:
            candidate = Path(project_config_dir) / runtime_path.name
            if candidate.exists():
                source_path = candidate

    if source_path is not None and source_path.exists():
        try:
            return runtime_bytes == source_path.read_bytes()
        except Exception:
            return False

    default_payload = DEFAULT_CONFIG_DATA.get(runtime_path.name)
    if default_payload is None:
        return False
    try:
        return json.loads(runtime_bytes.decode("utf-8")) == default_payload
    except Exception:
        return False


def _runtime_config_dir_has_user_content(
    config_manager,
    config_dir: Path | None = None,
) -> bool:
    config_dir = Path(config_dir or config_manager.config_dir)
    try:
        directory_before = config_dir.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if _is_link_like_metadata(directory_before) or not stat.S_ISDIR(directory_before.st_mode):
        return True
    try:
        with os.scandir(config_dir) as scanned:
            child_names = sorted(entry.name for entry in scanned)
        for child_name in child_names:
            child = config_dir / child_name
            if _is_ignorable_runtime_entry(child):
                continue
            try:
                child_metadata = child.lstat()
            except OSError:
                return True
            if _is_link_like_metadata(child_metadata) or stat.S_ISDIR(child_metadata.st_mode):
                return True
            if not stat.S_ISREG(child_metadata.st_mode):
                return True
            if not _runtime_config_path_matches_pristine_default(config_manager, child):
                return True
        directory_after = config_dir.lstat()
        with os.scandir(config_dir) as scanned:
            final_names = sorted(entry.name for entry in scanned)
    except OSError:
        return True
    return not (
        stat.S_ISDIR(directory_after.st_mode)
        and not _is_link_like_metadata(directory_after)
        and os.path.samestat(directory_before, directory_after)
        and child_names == final_names
    )


def _is_link_like_metadata(metadata: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(metadata.st_mode)
        or int(getattr(metadata, "st_file_attributes", 0) or 0)
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    )


def _unsafe_legacy_runtime_entry(path: Path, reason: str) -> CloudsaveOperationError:
    return CloudsaveOperationError(
        "LEGACY_RUNTIME_ENTRY_UNSAFE",
        f"legacy runtime entry is unsafe ({reason}): {path}",
    )


def _checked_legacy_runtime_entry(root: Path, name: str) -> Path:
    relative_path = Path(name)
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise _unsafe_legacy_runtime_entry(root / relative_path, "outside_runtime_root")
    root_path = Path(root).expanduser()
    candidate = root_path / relative_path
    current = root_path
    for part in relative_path.parts:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise _unsafe_legacy_runtime_entry(current, "uninspectable") from exc
        if _is_link_like_metadata(metadata):
            raise _unsafe_legacy_runtime_entry(current, "link_or_reparse_point")
        current /= part
    try:
        metadata = current.lstat()
    except FileNotFoundError:
        return candidate
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(current, "uninspectable") from exc
    if _is_link_like_metadata(metadata):
        raise _unsafe_legacy_runtime_entry(current, "link_or_reparse_point")
    return candidate


def _canonicalize_legacy_root_boundary(root: Path) -> Path:
    """Allow an OS-provided parent redirect while rejecting a redirected root."""

    try:
        before = root.lstat()
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(root, "uninspectable") from exc
    if _is_link_like_metadata(before) or not stat.S_ISDIR(before.st_mode):
        raise _unsafe_legacy_runtime_entry(root, "unsafe_root")
    try:
        canonical = root.resolve(strict=True)
        named_after = root.lstat()
        canonical_metadata = canonical.lstat()
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(root, "changed_root") from exc
    if (
        _is_link_like_metadata(named_after)
        or _is_link_like_metadata(canonical_metadata)
        or not stat.S_ISDIR(named_after.st_mode)
        or not stat.S_ISDIR(canonical_metadata.st_mode)
        or not os.path.samestat(before, named_after)
        or not os.path.samestat(named_after, canonical_metadata)
    ):
        raise _unsafe_legacy_runtime_entry(root, "changed_root")
    return canonical


def _legacy_root_may_have_user_content(root: Path) -> bool:
    """Conservatively detect a candidate without opening any user file.

    Exact scoring happens only after the candidate has crossed the safe-copy
    boundary. Treating an empty directory as a possible hit costs one harmless
    snapshot; opening JSON here could follow a raced link or block on a FIFO.
    """

    try:
        root_metadata = root.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(root, "uninspectable") from exc
    if _is_link_like_metadata(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _unsafe_legacy_runtime_entry(root, "unsafe_root")

    for name in RUNTIME_USER_CONTENT_DIR_NAMES:
        candidate = _checked_legacy_runtime_entry(root, name)
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _unsafe_legacy_runtime_entry(candidate, "uninspectable") from exc
        if _is_link_like_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise _unsafe_legacy_runtime_entry(candidate, "unsupported_file_type")
        return True
    return False


def _create_private_runtime_snapshot(
    source_root: Path,
    workspace_parent: Path,
    *,
    prefix: str,
    optional_names: tuple[str, ...],
    inspection_directory_names: tuple[str, ...] = (),
    include_runtime_directories: bool = True,
    snapshot_root: Path | None = None,
    snapshot_root_precreated: bool = False,
    expected_source_identity: os.stat_result | None = None,
    expected_snapshot_identity: os.stat_result | None = None,
    reserved_copy_bytes: int = 0,
    reserved_copy_entries: int = 0,
    future_copy_multiplier: int = 1,
    on_snapshot_created=None,
) -> tuple[Path, list[str], list[str], dict[str, Any]]:
    """Create one generation-consistent allowlisted snapshot.

    The source root remains pinned for the complete pre-hash/copy/post-hash
    cycle.  Comparing both the live post-image and the staged image with the
    pre-image prevents a raced in-place writer from publishing a torn copy,
    even if it later restores the original bytes.
    """

    from utils.storage.migration import (
        StorageMigrationError,
        _close_windows_directory_rename_guard,
        _copy_posix_directory_tree_durably,
        _copy_runtime_entry,
        _copy_staged_file_durably,
        _ensure_opened_directory_still_named,
        _open_or_create_posix_child_directory,
        _open_posix_existing_relative_parent,
        _open_posix_relative_parent,
        _open_verified_directory,
        _open_windows_directory_rename_guard,
        _open_windows_directory_rename_guard_chain,
        _opened_mount_identity,
        _snapshot_path,
        _snapshot_posix_relative_entry,
    )

    if expected_source_identity is not None:
        _validate_prepared_legacy_directory(source_root, expected_source_identity)
    source_root = _canonicalize_legacy_root_boundary(source_root)
    workspace_parent.mkdir(parents=True, exist_ok=True)
    externally_prepared = snapshot_root is not None
    if snapshot_root is None:
        snapshot_root = Path(
            tempfile.mkdtemp(prefix=prefix, dir=str(workspace_parent))
        ).resolve(strict=True)
    elif snapshot_root_precreated:
        if expected_snapshot_identity is None:
            raise _unsafe_legacy_runtime_entry(
                snapshot_root,
                "legacy_preparation_identity_missing",
            )
        _validate_prepared_legacy_directory(
            snapshot_root,
            expected_snapshot_identity,
        )
    else:
        snapshot_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        snapshot_root = snapshot_root.resolve(strict=True)
    if not snapshot_root_precreated:
        os.chmod(snapshot_root, stat.S_IRWXU)
    if expected_snapshot_identity is not None:
        _validate_prepared_legacy_directory(
            snapshot_root,
            expected_snapshot_identity,
        )
    if callable(on_snapshot_created):
        on_snapshot_created(snapshot_root, snapshot_root.lstat())
    directory_names = (
        *(LEGACY_RUNTIME_DIR_NAMES if include_runtime_directories else ()),
        *inspection_directory_names,
    )
    relative_paths = (
        *directory_names,
        *(f"state/{name}" for name in optional_names),
    )

    def _validate_shapes(
        snapshots: dict[str, dict[str, int | str]],
    ) -> None:
        for relative_path in directory_names:
            kind = str(snapshots[relative_path].get("kind") or "")
            if kind not in {"missing", "dir"}:
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"旧运行目录条目类型不受支持: {source_root / relative_path}",
                )
        for name in optional_names:
            relative_path = f"state/{name}"
            kind = str(snapshots[relative_path].get("kind") or "")
            if kind not in {"missing", "file"}:
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"旧运行状态条目类型不受支持: {source_root / relative_path}",
                )

    def _ensure_copy_capacity(
        snapshots: dict[str, dict[str, int | str]],
    ) -> tuple[int, int]:
        from utils.storage.migration import (
            StorageMigrationError,
            _filesystem_allocation_unit,
            _filesystem_free_entry_count,
        )

        if (
            reserved_copy_bytes < 0
            or reserved_copy_entries < 0
            or future_copy_multiplier < 1
        ):
            raise ValueError("invalid private snapshot capacity reservation")
        try:
            allocation_unit = _filesystem_allocation_unit(snapshot_root)
            free_bytes = int(shutil.disk_usage(str(snapshot_root)).free)
            free_entries = _filesystem_free_entry_count(snapshot_root)
        except OSError as exc:
            raise StorageMigrationError(
                "disk_space_unavailable",
                f"无法确认 phase-0 私有快照卷剩余空间: {exc}",
            ) from exc
        file_count = sum(int(item.get("file_count") or 0) for item in snapshots.values())
        directory_count = sum(
            int(item.get("directory_count") or 0) for item in snapshots.values()
        )
        logical_bytes = sum(int(item.get("total_bytes") or 0) for item in snapshots.values())
        # Logical bytes intentionally overestimate sparse files. Count every
        # copied file/directory plus fixed private metadata allocations.
        entry_count = file_count + directory_count + 16
        one_copy_bytes = logical_bytes + entry_count * allocation_unit
        required_bytes = (
            one_copy_bytes * future_copy_multiplier + reserved_copy_bytes
        )
        required_entries = (
            entry_count * future_copy_multiplier + reserved_copy_entries
        )
        safety_margin = max(64 * 1024 * 1024, int(required_bytes * 0.05))
        if required_bytes + safety_margin > free_bytes or (
            free_entries is not None
            and required_entries > free_entries
        ):
            raise StorageMigrationError(
                "insufficient_space",
                "目标卷剩余空间不足，无法安全创建 phase-0 私有快照。",
            )
        return one_copy_bytes, entry_count

    try:
        root_identity_tuple: tuple[int, int]
        if os.name == "nt":
            source_identity = source_root.lstat()
            if (
                expected_source_identity is not None
                and not os.path.samestat(expected_source_identity, source_identity)
            ):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "旧运行目录私有快照在复制前被替换。",
                )
            root_identity_tuple = (
                int(source_identity.st_dev),
                int(source_identity.st_ino),
            )
            snapshot_identity = snapshot_root.lstat()
            if (
                expected_snapshot_identity is not None
                and not os.path.samestat(
                    expected_snapshot_identity,
                    snapshot_identity,
                )
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    "旧运行目录安全快照在创建前被替换。",
                )
            source_guards = _open_windows_directory_rename_guard_chain(
                source_root,
                source_identity,
            )
            snapshot_guards: list[int] = []
            source_state_guard = -1
            snapshot_state_guard = -1
            try:
                snapshot_guards = _open_windows_directory_rename_guard_chain(
                    snapshot_root,
                    snapshot_identity,
                )

                def _windows_snapshots(root: Path) -> dict[str, dict[str, int | str]]:
                    return {
                        relative_path: _snapshot_path(
                            root / relative_path,
                            include_directory_count=True,
                        )
                        for relative_path in relative_paths
                    }

                before = _windows_snapshots(source_root)
                _validate_shapes(before)
                copy_required_bytes, copy_required_entries = _ensure_copy_capacity(
                    before
                )
                runtime_paths = [
                    name
                    for name in directory_names
                    if before[name]["kind"] != "missing"
                ]
                optional_paths = [
                    name
                    for name in optional_names
                    if before[f"state/{name}"]["kind"] != "missing"
                ]
                needs_state_directory = bool(optional_paths) or any(
                    name.startswith("state/") for name in runtime_paths
                )
                if needs_state_directory:
                    source_state = source_root / "state"
                    source_state_identity = source_state.lstat()
                    source_state_guard = _open_windows_directory_rename_guard(
                        source_state,
                        source_state_identity,
                    )
                    snapshot_state = snapshot_root / "state"
                    snapshot_state.mkdir(mode=0o700)
                    snapshot_state_identity = snapshot_state.lstat()
                    snapshot_state_guard = _open_windows_directory_rename_guard(
                        snapshot_state,
                        snapshot_state_identity,
                    )
                for name in runtime_paths:
                    _copy_runtime_entry(source_root / name, snapshot_root / name)

                if optional_paths:
                    snapshot_state = snapshot_root / "state"
                    source_state = source_root / "state"
                    for name in optional_paths:
                        _copy_runtime_entry(
                            source_state / name,
                            snapshot_state / name,
                        )

                after = _windows_snapshots(source_root)
                staged = _windows_snapshots(snapshot_root)
                if before != after or before != staged:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "旧运行目录在创建安全快照期间发生变化。",
                    )
            finally:
                if snapshot_state_guard >= 0:
                    _close_windows_directory_rename_guard(snapshot_state_guard)
                if source_state_guard >= 0:
                    _close_windows_directory_rename_guard(source_state_guard)
                while snapshot_guards:
                    _close_windows_directory_rename_guard(snapshot_guards.pop())
                while source_guards:
                    _close_windows_directory_rename_guard(source_guards.pop())
        else:
            source_fd = -1
            snapshot_fd = -1
            snapshot_state_fd = -1
            try:
                source_fd = _open_verified_directory(source_root)
                snapshot_fd = _open_verified_directory(snapshot_root)
                if (
                    expected_snapshot_identity is not None
                    and not os.path.samestat(
                        expected_snapshot_identity,
                        os.fstat(snapshot_fd),
                    )
                ):
                    raise StorageMigrationError(
                        "staging_entry_changed",
                        "旧运行目录安全快照在创建前被替换。",
                    )
                opened_source_root = os.fstat(source_fd)
                if (
                    expected_source_identity is not None
                    and not os.path.samestat(
                        expected_source_identity,
                        opened_source_root,
                    )
                ):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "旧运行目录私有快照在复制前被替换。",
                    )
                root_identity_tuple = (
                    int(opened_source_root.st_dev),
                    int(opened_source_root.st_ino),
                )
                source_mount = _opened_mount_identity(source_fd)
                snapshot_mount = _opened_mount_identity(snapshot_fd)

                def _posix_snapshots(
                    root_fd: int,
                    root: Path,
                    mount_identity: tuple[str, int],
                ) -> dict[str, dict[str, int | str]]:
                    return {
                        relative_path: _snapshot_posix_relative_entry(
                            root_fd,
                            root,
                            relative_path,
                            expected_mount_identity=mount_identity,
                            include_directory_count=True,
                        )
                        for relative_path in relative_paths
                    }

                before = _posix_snapshots(source_fd, source_root, source_mount)
                _validate_shapes(before)
                copy_required_bytes, copy_required_entries = _ensure_copy_capacity(
                    before
                )
                runtime_paths = [
                    name
                    for name in directory_names
                    if before[name]["kind"] != "missing"
                ]
                optional_paths = [
                    name
                    for name in optional_names
                    if before[f"state/{name}"]["kind"] != "missing"
                ]

                for name in runtime_paths:
                    opened_source_parent = _open_posix_existing_relative_parent(
                        source_fd,
                        source_root,
                        name,
                        expected_mount_identity=source_mount,
                    )
                    if opened_source_parent is None:
                        raise StorageMigrationError(
                            "source_changed_during_migration",
                            f"旧运行目录条目在复制前消失: {source_root / name}",
                        )
                    source_parent_fd, source_name, _source_parent_display = (
                        opened_source_parent
                    )
                    target_parent_fd, target_name, _target_parent_display = (
                        _open_posix_relative_parent(
                            snapshot_fd,
                            snapshot_root,
                            name,
                            expected_mount_identity=snapshot_mount,
                            create_missing=True,
                        )
                    )
                    try:
                        source_identity = os.stat(
                            source_name,
                            dir_fd=source_parent_fd,
                            follow_symlinks=False,
                        )
                        _copy_posix_directory_tree_durably(
                            source_root / name,
                            snapshot_root / name,
                            expected_mount_identity=source_mount,
                            source_parent_fd=source_parent_fd,
                            source_name=source_name,
                            expected_source_identity=source_identity,
                            target_parent_fd=target_parent_fd,
                            target_name=target_name,
                            expected_target_mount_identity=snapshot_mount,
                        )
                    finally:
                        os.close(target_parent_fd)
                        os.close(source_parent_fd)

                if optional_paths:
                    snapshot_state_fd = _open_or_create_posix_child_directory(
                        snapshot_fd,
                        "state",
                        snapshot_root / "state",
                        expected_mount_identity=snapshot_mount,
                        allow_existing=True,
                    )
                    for name in optional_paths:
                        opened_parent = _open_posix_existing_relative_parent(
                            source_fd,
                            source_root,
                            f"state/{name}",
                            expected_mount_identity=source_mount,
                        )
                        if opened_parent is None:
                            raise StorageMigrationError(
                                "source_changed_during_migration",
                                f"旧运行状态条目在复制前消失: {source_root / 'state' / name}",
                            )
                        source_state_fd, source_name, _source_state_display = opened_parent
                        try:
                            _copy_staged_file_durably(
                                source_root / "state" / name,
                                snapshot_root / "state" / name,
                                source_parent_fd=source_state_fd,
                                source_name=source_name,
                                target_parent_fd=snapshot_state_fd,
                                target_name=name,
                                expected_mount_identity=source_mount,
                                expected_target_mount_identity=snapshot_mount,
                            )
                        finally:
                            os.close(source_state_fd)

                after = _posix_snapshots(source_fd, source_root, source_mount)
                staged = _posix_snapshots(snapshot_fd, snapshot_root, snapshot_mount)
                if before != after or before != staged:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "旧运行目录在创建安全快照期间发生变化。",
                    )
                _ensure_opened_directory_still_named(
                    source_root,
                    source_fd,
                    error_code="source_changed_during_migration",
                    message="旧运行目录在创建安全快照期间被替换。",
                )
                _ensure_opened_directory_still_named(
                    snapshot_root,
                    snapshot_fd,
                    error_code="staging_entry_changed",
                    message="旧运行目录安全快照在创建期间被替换。",
                )
            finally:
                if snapshot_state_fd >= 0:
                    os.close(snapshot_state_fd)
                if snapshot_fd >= 0:
                    os.close(snapshot_fd)
                if source_fd >= 0:
                    os.close(source_fd)

        return (
            snapshot_root,
            runtime_paths,
            [f"state/{name}" for name in optional_paths],
            {
                "identity": root_identity_tuple,
                "entries": before,
                "copy_required_bytes": copy_required_bytes,
                "copy_required_entries": copy_required_entries,
            },
        )
    except StorageMigrationError as exc:
        if not externally_prepared:
            shutil.rmtree(snapshot_root, ignore_errors=True)
        raise _unsafe_legacy_runtime_entry(source_root, exc.error_code) from exc
    except BaseException:
        if not externally_prepared:
            shutil.rmtree(snapshot_root, ignore_errors=True)
        raise


def _validate_private_snapshot_source_boundary(
    source_root: Path,
    boundary: dict[str, Any],
    *,
    optional_names: tuple[str, ...],
    inspection_directory_names: tuple[str, ...] = (),
    include_runtime_directories: bool = True,
    root_parent_fd: int | None = None,
) -> os.stat_result:
    """Revalidate the live root against the generation copied earlier."""

    from utils.storage.migration import (
        StorageMigrationError,
        _close_windows_directory_rename_guard,
        _ensure_opened_directory_still_named,
        _open_or_create_posix_child_directory,
        _open_verified_directory,
        _open_windows_directory_rename_guard_chain,
        _opened_mount_identity,
        _snapshot_path,
        _snapshot_posix_relative_entry,
    )

    source_root = Path(source_root)
    if root_parent_fd is None:
        source_root = _canonicalize_legacy_root_boundary(source_root)
    relative_paths = (
        *(LEGACY_RUNTIME_DIR_NAMES if include_runtime_directories else ()),
        *inspection_directory_names,
        *(f"state/{name}" for name in optional_names),
    )
    expected_identity = tuple(boundary.get("identity") or ())
    expected_entries = boundary.get("entries")
    if len(expected_identity) != 2 or not isinstance(expected_entries, dict):
        raise _unsafe_legacy_runtime_entry(source_root, "invalid_snapshot_boundary")

    try:
        if os.name == "nt":
            named = source_root.lstat()
            if (int(named.st_dev), int(named.st_ino)) != expected_identity:
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "运行目录在安全快照后被替换。",
                )
            guards = _open_windows_directory_rename_guard_chain(source_root, named)
            try:
                observed = {
                    relative_path: _snapshot_path(
                        source_root / relative_path,
                        include_directory_count=True,
                    )
                    for relative_path in relative_paths
                }
                named_after = source_root.lstat()
                if not os.path.samestat(named, named_after):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "运行目录在安全快照复验期间被替换。",
                    )
            finally:
                while guards:
                    _close_windows_directory_rename_guard(guards.pop())
        else:
            if root_parent_fd is None:
                source_fd = _open_verified_directory(source_root)
            else:
                parent_mount_identity = _opened_mount_identity(root_parent_fd)
                source_fd = _open_or_create_posix_child_directory(
                    root_parent_fd,
                    source_root.name,
                    source_root,
                    expected_mount_identity=parent_mount_identity,
                    allow_existing=True,
                    create_missing=False,
                )
            try:
                named = os.fstat(source_fd)
                if (int(named.st_dev), int(named.st_ino)) != expected_identity:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "运行目录在安全快照后被替换。",
                    )
                mount_identity = _opened_mount_identity(source_fd)
                observed = {
                    relative_path: _snapshot_posix_relative_entry(
                        source_fd,
                        source_root,
                        relative_path,
                        expected_mount_identity=mount_identity,
                        include_directory_count=True,
                    )
                    for relative_path in relative_paths
                }
                observed_after = {
                    relative_path: _snapshot_posix_relative_entry(
                        source_fd,
                        source_root,
                        relative_path,
                        expected_mount_identity=mount_identity,
                        include_directory_count=True,
                    )
                    for relative_path in relative_paths
                }
                if observed_after != observed:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "运行目录在安全快照复验期间发生变化。",
                    )
                if root_parent_fd is None:
                    _ensure_opened_directory_still_named(
                        source_root,
                        source_fd,
                        error_code="source_changed_during_migration",
                        message="运行目录在安全快照复验期间被替换。",
                    )
                else:
                    named_after = os.stat(
                        source_root.name,
                        dir_fd=root_parent_fd,
                        follow_symlinks=False,
                    )
                    if not os.path.samestat(named, named_after):
                        raise StorageMigrationError(
                            "source_changed_during_migration",
                            "运行目录在安全快照复验期间被替换。",
                        )
            finally:
                os.close(source_fd)
        if observed != expected_entries:
            raise StorageMigrationError(
                "source_changed_during_migration",
                "运行目录在安全快照后发生变化。",
            )
        return named
    except StorageMigrationError as exc:
        raise _unsafe_legacy_runtime_entry(source_root, exc.error_code) from exc


def _merge_private_snapshot(
    base_root: Path,
    overlay_root: Path,
    names: tuple[str, ...] | None = None,
    *,
    expected_base_identity: os.stat_result | None = None,
    expected_overlay_identity: os.stat_result | None = None,
) -> None:
    """Rename-merge two private snapshots without resolving mutable parents."""

    from utils.storage.migration import (
        StorageMigrationError,
        _close_windows_directory_rename_guard,
        _copy_open_file_metadata,
        _ensure_opened_directory_still_named,
        _ensure_opened_entry_on_mount,
        _fsync_opened_migration_directory,
        _open_verified_directory,
        _open_windows_directory_rename_guard,
        _open_windows_directory_rename_guard_chain,
        _opened_mount_identity,
        _rename_entry_without_replacing_at,
    )

    if expected_base_identity is not None:
        _validate_prepared_legacy_directory(base_root, expected_base_identity)
    if expected_overlay_identity is not None:
        _validate_prepared_legacy_directory(overlay_root, expected_overlay_identity)
    base_root = _canonicalize_legacy_root_boundary(base_root)
    overlay_root = _canonicalize_legacy_root_boundary(overlay_root)

    try:
        if os.name == "nt":
            base_identity = base_root.lstat()
            overlay_identity = overlay_root.lstat()
            if (
                expected_base_identity is not None
                and not os.path.samestat(expected_base_identity, base_identity)
            ) or (
                expected_overlay_identity is not None
                and not os.path.samestat(expected_overlay_identity, overlay_identity)
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    "旧运行目录私有快照在合并前被替换。",
                )
            base_guards = _open_windows_directory_rename_guard_chain(
                base_root,
                base_identity,
            )
            overlay_guards: list[int] = []
            try:
                overlay_guards = _open_windows_directory_rename_guard_chain(
                    overlay_root,
                    overlay_identity,
                )

                def _merge_windows_directory(
                    base_directory: Path,
                    overlay_directory: Path,
                    selected_names: tuple[str, ...] | None = None,
                ) -> None:
                    with os.scandir(overlay_directory) as scanned:
                        available_names = tuple(sorted(entry.name for entry in scanned))
                    child_names = available_names if selected_names is None else selected_names
                    for child_name in child_names:
                        overlay_path = overlay_directory / child_name
                        try:
                            overlay_metadata = overlay_path.lstat()
                        except FileNotFoundError:
                            continue
                        if _is_link_like_metadata(overlay_metadata) or not (
                            stat.S_ISDIR(overlay_metadata.st_mode)
                            or stat.S_ISREG(overlay_metadata.st_mode)
                        ):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照包含不安全条目: {overlay_path}",
                            )
                        base_path = base_directory / child_name
                        try:
                            base_metadata = base_path.lstat()
                        except FileNotFoundError:
                            os.replace(overlay_path, base_path)
                            published = base_path.lstat()
                            if (
                                _is_link_like_metadata(published)
                                or not os.path.samestat(overlay_metadata, published)
                            ):
                                raise StorageMigrationError(
                                    "staging_entry_changed",
                                    f"旧运行目录安全快照条目在合并期间被替换: {base_path}",
                                )
                            continue
                        if _is_link_like_metadata(base_metadata) or not (
                            stat.S_ISDIR(base_metadata.st_mode)
                            or stat.S_ISREG(base_metadata.st_mode)
                        ):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照包含不安全条目: {base_path}",
                            )
                        if stat.S_ISREG(overlay_metadata.st_mode) and stat.S_ISREG(
                            base_metadata.st_mode
                        ):
                            os.replace(overlay_path, base_path)
                            published = base_path.lstat()
                            if not os.path.samestat(overlay_metadata, published):
                                raise StorageMigrationError(
                                    "staging_entry_changed",
                                    f"旧运行目录安全快照文件在合并期间被替换: {base_path}",
                                )
                            continue
                        if not (
                            stat.S_ISDIR(overlay_metadata.st_mode)
                            and stat.S_ISDIR(base_metadata.st_mode)
                        ):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照条目类型冲突: {overlay_path}",
                            )
                        base_guard = _open_windows_directory_rename_guard(
                            base_path,
                            base_metadata,
                        )
                        overlay_guard = -1
                        try:
                            overlay_guard = _open_windows_directory_rename_guard(
                                overlay_path,
                                overlay_metadata,
                            )
                            _merge_windows_directory(base_path, overlay_path)
                            shutil.copystat(
                                overlay_path,
                                base_path,
                                follow_symlinks=False,
                            )
                        finally:
                            if overlay_guard >= 0:
                                _close_windows_directory_rename_guard(overlay_guard)
                            _close_windows_directory_rename_guard(base_guard)
                        if not os.path.samestat(overlay_metadata, overlay_path.lstat()):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照目录在合并期间被替换: {overlay_path}",
                            )
                        overlay_path.rmdir()

                _merge_windows_directory(base_root, overlay_root, names)
            finally:
                while overlay_guards:
                    _close_windows_directory_rename_guard(overlay_guards.pop())
                while base_guards:
                    _close_windows_directory_rename_guard(base_guards.pop())
            return

        base_fd = -1
        overlay_fd = -1
        try:
            base_fd = _open_verified_directory(base_root)
            overlay_fd = _open_verified_directory(overlay_root)
            if (
                expected_base_identity is not None
                and not os.path.samestat(expected_base_identity, os.fstat(base_fd))
            ) or (
                expected_overlay_identity is not None
                and not os.path.samestat(
                    expected_overlay_identity,
                    os.fstat(overlay_fd),
                )
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    "旧运行目录私有快照在合并前被替换。",
                )
            base_mount = _opened_mount_identity(base_fd)
            overlay_mount = _opened_mount_identity(overlay_fd)

            def _merge_posix_directory(
                base_directory_fd: int,
                overlay_directory_fd: int,
                base_display: Path,
                overlay_display: Path,
                selected_names: tuple[str, ...] | None = None,
            ) -> None:
                _ensure_opened_entry_on_mount(
                    base_directory_fd,
                    base_mount,
                    base_display,
                )
                _ensure_opened_entry_on_mount(
                    overlay_directory_fd,
                    overlay_mount,
                    overlay_display,
                )
                with os.scandir(overlay_directory_fd) as scanned:
                    available_names = tuple(sorted(entry.name for entry in scanned))
                child_names = available_names if selected_names is None else selected_names
                directory_flags = (
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                )
                for child_name in child_names:
                    try:
                        overlay_metadata = os.stat(
                            child_name,
                            dir_fd=overlay_directory_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        continue
                    if _is_link_like_metadata(overlay_metadata) or not (
                        stat.S_ISDIR(overlay_metadata.st_mode)
                        or stat.S_ISREG(overlay_metadata.st_mode)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"旧运行目录安全快照包含不安全条目: {overlay_display / child_name}",
                        )
                    try:
                        base_metadata = os.stat(
                            child_name,
                            dir_fd=base_directory_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        _rename_entry_without_replacing_at(
                            overlay_directory_fd,
                            child_name,
                            base_directory_fd,
                            child_name,
                            target_display=base_display / child_name,
                        )
                        published = os.stat(
                            child_name,
                            dir_fd=base_directory_fd,
                            follow_symlinks=False,
                        )
                        if not os.path.samestat(overlay_metadata, published):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照条目在合并期间被替换: {base_display / child_name}",
                            )
                        _fsync_opened_migration_directory(
                            base_directory_fd,
                            base_display,
                        )
                        _fsync_opened_migration_directory(
                            overlay_directory_fd,
                            overlay_display,
                        )
                        continue
                    if _is_link_like_metadata(base_metadata) or not (
                        stat.S_ISDIR(base_metadata.st_mode)
                        or stat.S_ISREG(base_metadata.st_mode)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"旧运行目录安全快照包含不安全条目: {base_display / child_name}",
                        )
                    if stat.S_ISREG(overlay_metadata.st_mode) and stat.S_ISREG(
                        base_metadata.st_mode
                    ):
                        os.rename(
                            child_name,
                            child_name,
                            src_dir_fd=overlay_directory_fd,
                            dst_dir_fd=base_directory_fd,
                        )
                        published = os.stat(
                            child_name,
                            dir_fd=base_directory_fd,
                            follow_symlinks=False,
                        )
                        if not os.path.samestat(overlay_metadata, published):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照文件在合并期间被替换: {base_display / child_name}",
                            )
                        _fsync_opened_migration_directory(
                            base_directory_fd,
                            base_display,
                        )
                        _fsync_opened_migration_directory(
                            overlay_directory_fd,
                            overlay_display,
                        )
                        continue
                    if not (
                        stat.S_ISDIR(overlay_metadata.st_mode)
                        and stat.S_ISDIR(base_metadata.st_mode)
                    ):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"旧运行目录安全快照条目类型冲突: {overlay_display / child_name}",
                        )
                    base_child_fd = os.open(
                        child_name,
                        directory_flags,
                        dir_fd=base_directory_fd,
                    )
                    overlay_child_fd = -1
                    try:
                        overlay_child_fd = os.open(
                            child_name,
                            directory_flags,
                            dir_fd=overlay_directory_fd,
                        )
                        if (
                            not os.path.samestat(base_metadata, os.fstat(base_child_fd))
                            or not os.path.samestat(
                                overlay_metadata,
                                os.fstat(overlay_child_fd),
                            )
                        ):
                            raise StorageMigrationError(
                                "staging_entry_changed",
                                f"旧运行目录安全快照目录在合并期间被替换: {overlay_display / child_name}",
                            )
                        _merge_posix_directory(
                            base_child_fd,
                            overlay_child_fd,
                            base_display / child_name,
                            overlay_display / child_name,
                        )
                        _copy_open_file_metadata(
                            overlay_child_fd,
                            base_child_fd,
                            overlay_metadata,
                        )
                        _fsync_opened_migration_directory(
                            base_child_fd,
                            base_display / child_name,
                        )
                    finally:
                        if overlay_child_fd >= 0:
                            os.close(overlay_child_fd)
                        os.close(base_child_fd)
                    named_overlay = os.stat(
                        child_name,
                        dir_fd=overlay_directory_fd,
                        follow_symlinks=False,
                    )
                    if not os.path.samestat(overlay_metadata, named_overlay):
                        raise StorageMigrationError(
                            "staging_entry_changed",
                            f"旧运行目录安全快照目录在合并期间被替换: {overlay_display / child_name}",
                        )
                    os.rmdir(child_name, dir_fd=overlay_directory_fd)
                    _fsync_opened_migration_directory(
                        overlay_directory_fd,
                        overlay_display,
                    )

            _merge_posix_directory(
                base_fd,
                overlay_fd,
                base_root,
                overlay_root,
                names,
            )
            _ensure_opened_directory_still_named(
                base_root,
                base_fd,
                error_code="staging_entry_changed",
                message="旧运行目录安全快照在合并期间被替换。",
            )
            _ensure_opened_directory_still_named(
                overlay_root,
                overlay_fd,
                error_code="staging_entry_changed",
                message="旧运行目录叠加快照在合并期间被替换。",
            )
        finally:
            if overlay_fd >= 0:
                os.close(overlay_fd)
            if base_fd >= 0:
                os.close(base_fd)
    except StorageMigrationError as exc:
        raise _unsafe_legacy_runtime_entry(overlay_root, exc.error_code) from exc
    except (OSError, ValueError) as exc:
        raise _unsafe_legacy_runtime_entry(overlay_root, "staging_merge_failed") from exc


def _runtime_cache_has_unsafe_entry(root: Path) -> bool:
    for name in RUNTIME_CACHE_DIR_NAMES:
        try:
            candidate = _checked_legacy_runtime_entry(root, name)
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except (CloudsaveOperationError, OSError):
            return True
        if _is_link_like_metadata(metadata) or not stat.S_ISDIR(metadata.st_mode):
            return True
    return False


def _runtime_root_has_user_content(root: Path, *, config_manager=None) -> bool:
    root = Path(root)
    try:
        root.lstat()
    except FileNotFoundError:
        return False
    root = _canonicalize_legacy_root_boundary(root)
    # Cache bytes are rebuildable and must not suppress Cloud Save import, but
    # a cache name that redirects or names a special object is not an empty-root
    # fact. Treat it as authoritative/unsafe so legacy import keeps its backup
    # boundary and the copy validator below can fail closed.
    if _runtime_cache_has_unsafe_entry(root):
        return True
    config_dir = root / "config" if config_manager is not None else None
    for name in RUNTIME_USER_CONTENT_DIR_NAMES:
        candidate = _checked_legacy_runtime_entry(root, name)
        try:
            candidate_metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _unsafe_legacy_runtime_entry(candidate, "uninspectable") from exc
        if _is_link_like_metadata(candidate_metadata) or not stat.S_ISDIR(
            candidate_metadata.st_mode
        ):
            raise _unsafe_legacy_runtime_entry(candidate, "unsupported_file_type")
        if config_dir is not None and candidate == config_dir:
            if _runtime_config_dir_has_user_content(config_manager, candidate):
                return True
            continue
        transactional_pattern = TRANSACTIONAL_RUNTIME_ENTRY_PATTERNS.get(name)
        try:
            with os.scandir(candidate) as scanned:
                child_names = sorted(entry.name for entry in scanned)
            for child_name in child_names:
                child = candidate / child_name
                if _is_ignorable_runtime_entry(
                    child,
                    transactional_pattern=transactional_pattern,
                ):
                    continue
                return True
            candidate_after = candidate.lstat()
            with os.scandir(candidate) as scanned:
                final_names = sorted(entry.name for entry in scanned)
        except OSError as exc:
            raise _unsafe_legacy_runtime_entry(candidate, "changed_during_scan") from exc
        if (
            _is_link_like_metadata(candidate_after)
            or not stat.S_ISDIR(candidate_after.st_mode)
            or not os.path.samestat(candidate_metadata, candidate_after)
            or child_names != final_names
        ):
            raise _unsafe_legacy_runtime_entry(candidate, "changed_during_scan")
    return False


def runtime_root_has_user_content(root: Path, *, config_manager=None) -> bool:
    """Public wrapper for detecting user-owned runtime data in a storage root."""
    return _runtime_root_has_user_content(root, config_manager=config_manager)


def _is_ignorable_runtime_entry(path: Path, *, transactional_pattern=None) -> bool:
    name = path.name
    if name == ".gitkeep":
        return True
    if name.startswith("."):
        # 事务目录是崩溃恢复的唯一线索，判定「有没有用户内容」时它算内容。
        # 名字必须逐字命中该目录声明的事务模式，不能只看点前缀和后缀。
        return not (
            transactional_pattern is not None
            and transactional_pattern.fullmatch(name) is not None
        )
    if name == "__pycache__":
        return True
    return False


def _directory_has_meaningful_content(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if _is_ignorable_runtime_entry(child):
                continue
            return True
    except Exception:
        return False
    return False


def _collect_memory_character_names(root: Path) -> set[str]:
    memory_root = root / "memory"
    character_names: set[str] = set()
    if not memory_root.is_dir():
        return character_names
    try:
        for child in memory_root.iterdir():
            if _is_ignorable_runtime_entry(child):
                continue
            if child.is_dir() and _directory_has_meaningful_content(child):
                character_names.add(child.name)
            elif child.is_file():
                character_names.add(child.stem)
    except Exception:
        return character_names
    return character_names


def _load_seed_characters_payload(config_manager) -> dict[str, Any]:
    localized_source = None
    try:
        localized_source = config_manager._get_localized_characters_source()
    except Exception:
        localized_source = None
    if localized_source is not None:
        payload = _load_json_if_exists(Path(localized_source))
        if isinstance(payload, dict):
            return payload
    fallback_payload = config_manager.get_default_characters()
    return fallback_payload if isinstance(fallback_payload, dict) else {}


def _normalize_catgirl_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    normalized_payload = deepcopy(payload)
    try:
        from utils.config_manager import migrate_catgirl_reserved

        migrate_catgirl_reserved(normalized_payload)
    except Exception:
        pass
    return normalized_payload


def _character_payload_looks_default(config_manager, name: str, payload: Any) -> bool:
    normalized_payload = _normalize_catgirl_payload(payload)
    if normalized_payload is None:
        return False
    default_payload = _normalize_catgirl_payload((_load_seed_characters_payload(config_manager).get("猫娘") or {}).get(name))
    return default_payload is not None and normalized_payload == default_payload


def _master_payload_looks_default(config_manager, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    default_payload = _load_seed_characters_payload(config_manager).get("主人")
    return default_payload is not None and payload == default_payload


def _normalize_preferences_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return deepcopy(payload)
    if isinstance(payload, dict):
        return [deepcopy(payload)]
    return []


def _preferences_entry_key(entry: Any) -> str:
    if isinstance(entry, dict) and entry.get("model_path") is not None:
        return f"model_path:{entry.get('model_path')}"
    return _json_canonical_dumps(entry)


def _merge_preferences_payloads(legacy_payload: Any, current_payload: Any) -> list[Any]:
    merged_entries: dict[str, Any] = {}
    ordered_keys: list[str] = []
    for payload in (_normalize_preferences_payload(legacy_payload), _normalize_preferences_payload(current_payload)):
        for entry in payload:
            key = _preferences_entry_key(entry)
            if key not in merged_entries:
                ordered_keys.append(key)
            merged_entries[key] = deepcopy(entry)
    return [merged_entries[key] for key in ordered_keys]


def _deep_merge_json_dicts(legacy_payload: Any, current_payload: Any) -> dict[str, Any]:
    legacy_dict = deepcopy(legacy_payload) if isinstance(legacy_payload, dict) else {}
    current_dict = current_payload if isinstance(current_payload, dict) else {}
    for key, value in current_dict.items():
        if isinstance(legacy_dict.get(key), dict) and isinstance(value, dict):
            legacy_dict[key] = _deep_merge_json_dicts(legacy_dict[key], value)
        else:
            legacy_dict[key] = deepcopy(value)
    return legacy_dict


def _config_payload_looks_default(filename: str, payload: Any) -> bool:
    default_payload = DEFAULT_CONFIG_DATA.get(filename)
    if filename == "user_preferences.json":
        return _normalize_preferences_payload(payload) == _normalize_preferences_payload(default_payload)
    if isinstance(default_payload, dict):
        return isinstance(payload, dict) and deepcopy(payload) == deepcopy(default_payload)
    if isinstance(default_payload, list):
        return isinstance(payload, list) and deepcopy(payload) == deepcopy(default_payload)
    return False


def _config_payload_looks_seeded(config_manager, filename: str, payload: Any) -> bool:
    project_payload = _load_json_if_exists(Path(config_manager.project_config_dir) / filename)
    if project_payload is not None:
        if filename == "user_preferences.json":
            return _normalize_preferences_payload(payload) == _normalize_preferences_payload(project_payload)
        return deepcopy(payload) == deepcopy(project_payload)
    return _config_payload_looks_default(filename, payload)


def _merge_characters_payloads(
    config_manager,
    legacy_payload: Any,
    current_payload: Any,
    *,
    preserve_current_only_defaults: bool,
) -> dict[str, Any]:
    legacy_dict = deepcopy(legacy_payload) if isinstance(legacy_payload, dict) else {}
    current_dict = deepcopy(current_payload) if isinstance(current_payload, dict) else {}
    merged_payload = deepcopy(legacy_dict)

    for key, value in current_dict.items():
        if key not in {"猫娘", "主人", "当前猫娘"}:
            merged_payload[key] = deepcopy(value)

    legacy_catgirls = legacy_dict.get("猫娘") or {}
    current_catgirls = current_dict.get("猫娘") or {}
    merged_catgirls: dict[str, Any] = {}
    for name in sorted(set(legacy_catgirls) | set(current_catgirls)):
        legacy_character = legacy_catgirls.get(name)
        current_character = current_catgirls.get(name)
        if legacy_character is None:
            if not preserve_current_only_defaults and _character_payload_looks_default(config_manager, name, current_character):
                continue
            chosen = current_character
        elif current_character is None:
            chosen = legacy_character
        else:
            current_default = _character_payload_looks_default(config_manager, name, current_character)
            legacy_default = _character_payload_looks_default(config_manager, name, legacy_character)
            if current_default and not legacy_default:
                chosen = legacy_character
            elif legacy_default and not current_default:
                chosen = current_character
            else:
                chosen = current_character
        if chosen is not None:
            merged_catgirls[name] = deepcopy(chosen)
    merged_payload["猫娘"] = merged_catgirls

    legacy_master = legacy_dict.get("主人")
    current_master = current_dict.get("主人")
    if legacy_master is None:
        if current_master is not None:
            merged_payload["主人"] = deepcopy(current_master)
    elif current_master is None:
        merged_payload["主人"] = deepcopy(legacy_master)
    else:
        current_master_default = _master_payload_looks_default(config_manager, current_master)
        legacy_master_default = _master_payload_looks_default(config_manager, legacy_master)
        chosen_master = legacy_master if current_master_default and not legacy_master_default else current_master
        merged_payload["主人"] = deepcopy(chosen_master)

    current_current_name = str(current_dict.get("当前猫娘") or "")
    legacy_current_name = str(legacy_dict.get("当前猫娘") or "")
    if current_current_name and current_current_name in merged_catgirls:
        current_current_payload = current_catgirls.get(current_current_name)
        current_default = _character_payload_looks_default(config_manager, current_current_name, current_current_payload)
        if current_current_name not in legacy_catgirls and not preserve_current_only_defaults and current_default:
            current_current_name = ""
        elif current_current_name not in legacy_catgirls or not current_default:
            merged_payload["当前猫娘"] = current_current_name
        elif legacy_current_name and legacy_current_name in merged_catgirls:
            merged_payload["当前猫娘"] = legacy_current_name
        else:
            merged_payload["当前猫娘"] = current_current_name
    elif legacy_current_name and legacy_current_name in merged_catgirls:
        merged_payload["当前猫娘"] = legacy_current_name
    elif current_current_name and current_current_name in merged_catgirls:
        merged_payload["当前猫娘"] = current_current_name
    elif merged_catgirls:
        merged_payload["当前猫娘"] = next(iter(merged_catgirls))
    else:
        merged_payload["当前猫娘"] = ""

    return merged_payload


def _runtime_root_summary(
    config_manager,
    root: Path,
    *,
    expected_root_identity: os.stat_result | None = None,
) -> dict[str, Any]:
    if expected_root_identity is not None:
        _validate_prepared_legacy_directory(root, expected_root_identity)
    config_root = root / "config"
    characters_path = config_root / "characters.json"
    user_preferences_path = config_root / "user_preferences.json"
    voice_storage_path = config_root / "voice_storage.json"
    workshop_config_path = config_root / "workshop_config.json"
    core_config_path = config_root / "core_config.json"

    characters_payload = _load_json_if_exists(characters_path)
    user_preferences_payload = _load_json_if_exists(user_preferences_path)
    voice_storage_payload = _load_json_if_exists(voice_storage_path)
    core_config_payload = _load_json_if_exists(core_config_path)
    if not isinstance(characters_payload, dict):
        characters_payload = None
    character_names = set((characters_payload or {}).get("猫娘", {}) or {})
    default_character_names = set((_load_seed_characters_payload(config_manager).get("猫娘") or {}).keys())

    asset_dirs_with_content = {
        dir_name: _directory_has_meaningful_content(root / dir_name)
        for dir_name in RUNTIME_ASSET_DIR_NAMES
    }
    memory_character_names = _collect_memory_character_names(root)
    seeded_character_shell = (
        character_names.issubset(default_character_names)
        and not memory_character_names
        and not any(asset_dirs_with_content.values())
    )
    score = (
        len(character_names) * 3
        + len(memory_character_names) * 2
        + (3 if user_preferences_path.is_file() else 0)
        + (2 if voice_storage_path.is_file() else 0)
        + (1 if workshop_config_path.is_file() else 0)
        + (1 if core_config_path.is_file() else 0)
        + sum(2 for has_content in asset_dirs_with_content.values() if has_content)
    )

    summary = {
        "has_user_content": _runtime_root_has_user_content(root, config_manager=config_manager),
        "characters_payload": characters_payload,
        "character_names": character_names,
        "memory_character_names": memory_character_names,
        "has_user_preferences": user_preferences_path.is_file(),
        "has_voice_storage": voice_storage_path.is_file(),
        "has_workshop_config": workshop_config_path.is_file(),
        "has_core_config": core_config_path.is_file(),
        "asset_dirs_with_content": asset_dirs_with_content,
        "seeded_character_shell": seeded_character_shell,
        "looks_like_seeded": (
            bool(character_names)
            and character_names.issubset(default_character_names)
            and not memory_character_names
            and (
                not user_preferences_path.is_file()
                or _config_payload_looks_seeded(config_manager, "user_preferences.json", user_preferences_payload)
            )
            and (
                not voice_storage_path.is_file()
                or _config_payload_looks_seeded(config_manager, "voice_storage.json", voice_storage_payload)
            )
            and not workshop_config_path.is_file()
            and (
                not core_config_path.is_file()
                or _config_payload_looks_seeded(config_manager, "core_config.json", core_config_payload)
            )
            and not any(asset_dirs_with_content.values())
        ),
        "score": score,
    }
    if expected_root_identity is not None:
        _validate_prepared_legacy_directory(root, expected_root_identity)
    return summary


def _legacy_root_provides_repair_benefit(config_manager, source_summary: dict[str, Any], target_summary: dict[str, Any]) -> tuple[bool, str]:
    if not target_summary["has_user_content"]:
        return True, "target_missing"

    source_is_richer = source_summary["score"] > target_summary["score"]
    target_is_seed_shell = bool(target_summary.get("seeded_character_shell"))

    if target_is_seed_shell:
        if source_summary["character_names"] - target_summary["character_names"]:
            return True, "missing_characters"

        if source_summary["memory_character_names"] - target_summary["memory_character_names"]:
            return True, "missing_memory"

        for flag_name, reason in (
            ("has_user_preferences", "missing_user_preferences"),
            ("has_voice_storage", "missing_voice_storage"),
            ("has_workshop_config", "missing_workshop_config"),
            ("has_core_config", "missing_core_config"),
        ):
            if source_summary[flag_name] and not target_summary[flag_name]:
                return True, reason

        for dir_name, source_has_content in source_summary["asset_dirs_with_content"].items():
            if source_has_content and not target_summary["asset_dirs_with_content"].get(dir_name):
                return True, f"missing_{dir_name}"

    source_characters = (source_summary.get("characters_payload") or {}).get("猫娘", {}) or {}
    target_characters = (target_summary.get("characters_payload") or {}).get("猫娘", {}) or {}
    for name in sorted(set(source_characters) & set(target_characters)):
        if (
            _character_payload_looks_default(config_manager, name, target_characters.get(name))
            and not _character_payload_looks_default(config_manager, name, source_characters.get(name))
        ):
            return True, "upgrade_default_character"

    if target_is_seed_shell and source_is_richer:
        return True, "repair_seeded_target"

    return False, ""


def _write_prepared_config_payloads(
    temp_root: Path,
    payloads: dict[str, Any],
    *,
    expected_root_identity: os.stat_result,
) -> None:
    """Write private merge output without re-resolving its prepared root."""

    from utils.storage.migration import (
        StorageMigrationError,
        _close_windows_directory_rename_guard,
        _ensure_opened_directory_still_named,
        _fsync_opened_migration_directory,
        _open_or_create_posix_child_directory,
        _open_verified_directory,
        _open_windows_directory_rename_guard_chain,
        _opened_mount_identity,
    )

    if not payloads:
        return
    _validate_prepared_legacy_directory(temp_root, expected_root_identity)
    config_dir = temp_root / "config"
    if os.name == "nt":
        root_guards = _open_windows_directory_rename_guard_chain(
            temp_root,
            expected_root_identity,
        )
        config_guards: list[int] = []
        try:
            config_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
            config_identity = config_dir.lstat()
            config_guards = _open_windows_directory_rename_guard_chain(
                config_dir,
                config_identity,
            )
            for filename, payload in payloads.items():
                atomic_write_json(
                    config_dir / filename,
                    payload,
                    ensure_ascii=False,
                    indent=2,
                )
            _validate_prepared_legacy_directory(temp_root, expected_root_identity)
            if not os.path.samestat(config_identity, config_dir.lstat()):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    "旧运行目录合并配置暂存目录在写入期间被替换。",
                )
        finally:
            while config_guards:
                _close_windows_directory_rename_guard(config_guards.pop())
            while root_guards:
                _close_windows_directory_rename_guard(root_guards.pop())
        return

    root_fd = -1
    config_fd = -1
    temp_name = ""
    temp_fd = -1
    try:
        root_fd = _open_verified_directory(temp_root)
        if not os.path.samestat(expected_root_identity, os.fstat(root_fd)):
            raise StorageMigrationError(
                "staging_entry_changed",
                "旧运行目录合并配置暂存根在写入前被替换。",
            )
        mount_identity = _opened_mount_identity(root_fd)
        config_fd = _open_or_create_posix_child_directory(
            root_fd,
            "config",
            config_dir,
            expected_mount_identity=mount_identity,
            allow_existing=False,
            durable_creation=True,
        )
        for filename, payload in payloads.items():
            if Path(filename).name != filename:
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"旧运行目录合并配置文件名无效: {filename}",
                )
            content = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            temp_name = f".neko-legacy-config-{uuid.uuid4().hex}.tmp"
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=config_fd,
            )
            view = memoryview(content)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("short write while staging legacy config")
                view = view[written:]
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = -1
            os.replace(
                temp_name,
                filename,
                src_dir_fd=config_fd,
                dst_dir_fd=config_fd,
            )
            temp_name = ""
            published = os.stat(
                filename,
                dir_fd=config_fd,
                follow_symlinks=False,
            )
            if _is_link_like_metadata(published) or not stat.S_ISREG(
                published.st_mode
            ):
                raise StorageMigrationError(
                    "staging_entry_changed",
                    f"旧运行目录合并配置发布类型异常: {config_dir / filename}",
                )
            _fsync_opened_migration_directory(config_fd, config_dir)
        _ensure_opened_directory_still_named(
            config_dir,
            config_fd,
            error_code="staging_entry_changed",
            message="旧运行目录合并配置暂存目录在写入期间被替换。",
        )
        _ensure_opened_directory_still_named(
            temp_root,
            root_fd,
            error_code="staging_entry_changed",
            message="旧运行目录合并配置暂存根在写入期间被替换。",
        )
    except StorageMigrationError as exc:
        raise _unsafe_legacy_runtime_entry(temp_root, exc.error_code) from exc
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(
            temp_root,
            "staging_config_write_failed",
        ) from exc
    finally:
        if temp_fd >= 0:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if temp_name and config_fd >= 0:
            try:
                os.unlink(temp_name, dir_fd=config_fd)
            except OSError:
                pass
        if config_fd >= 0:
            os.close(config_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _stage_merged_runtime_configs(
    config_manager,
    *,
    source_root: Path,
    target_root: Path,
    target_state_root: Path | None = None,
    temp_root: Path,
    temp_root_identity: os.stat_result,
    target_summary: dict[str, Any],
    source_origin_root: Path | None = None,
    target_origin_root: Path | None = None,
    source_root_identity: os.stat_result | None = None,
    target_root_identity: os.stat_result | None = None,
    target_state_root_identity: os.stat_result | None = None,
) -> None:
    if source_root_identity is not None:
        _validate_prepared_legacy_directory(source_root, source_root_identity)
    if target_root_identity is not None:
        _validate_prepared_legacy_directory(target_root, target_root_identity)
    if target_state_root is not None and target_state_root_identity is not None:
        _validate_prepared_legacy_directory(
            target_state_root,
            target_state_root_identity,
        )
    staged_payloads: dict[str, Any] = {}
    target_tombstone_names = _load_tombstone_names_from_state_path(
        (target_state_root or target_root)
        / "state"
        / "character_tombstones.json"
    )

    source_characters = _load_json_if_exists(source_root / "config" / "characters.json")
    target_characters = _load_json_if_exists(target_root / "config" / "characters.json")
    if source_characters is not None or target_characters is not None:
        merged_characters = _merge_characters_payloads(
            config_manager,
            source_characters,
            target_characters,
            preserve_current_only_defaults=not bool(target_summary.get("seeded_character_shell")),
        )
        if target_tombstone_names:
            merged_catgirls = merged_characters.get("猫娘") or {}
            for deleted_name in target_tombstone_names:
                merged_catgirls.pop(deleted_name, None)
            merged_characters["猫娘"] = merged_catgirls
            current_name = str(merged_characters.get("当前猫娘") or "")
            if current_name in target_tombstone_names:
                merged_characters["当前猫娘"] = next(iter(merged_catgirls), "")
        staged_payloads["characters.json"] = merged_characters

    source_preferences = _load_json_if_exists(source_root / "config" / "user_preferences.json")
    target_preferences = _load_json_if_exists(target_root / "config" / "user_preferences.json")
    if source_preferences is not None or target_preferences is not None:
        merged_preferences = _merge_preferences_payloads(source_preferences, target_preferences)
        staged_payloads["user_preferences.json"] = merged_preferences

    for filename in ROOT_CONFIG_MERGE_FILES:
        source_payload = _load_json_if_exists(source_root / "config" / filename)
        target_payload = _load_json_if_exists(target_root / "config" / filename)
        if source_payload is None and target_payload is None:
            continue
        merged_payload = _deep_merge_json_dicts(source_payload, target_payload)
        if filename == "workshop_config.json":
            merged_payload = rebase_runtime_bound_workshop_config_paths(
                merged_payload,
                source_root=source_origin_root or source_root,
                target_root=target_origin_root or target_root,
            )
        staged_payloads[filename] = merged_payload
    _write_prepared_config_payloads(
        temp_root,
        staged_payloads,
        expected_root_identity=temp_root_identity,
    )
    if source_root_identity is not None:
        _validate_prepared_legacy_directory(source_root, source_root_identity)
    if target_root_identity is not None:
        _validate_prepared_legacy_directory(target_root, target_root_identity)
    if target_state_root is not None and target_state_root_identity is not None:
        _validate_prepared_legacy_directory(
            target_state_root,
            target_state_root_identity,
        )


def _stage_legacy_characters_with_anchor_tombstones(
    *,
    source_root: Path,
    anchor_snapshot: Path,
    temp_root: Path,
    temp_root_identity: os.stat_result,
    source_root_identity: os.stat_result | None = None,
    anchor_root_identity: os.stat_result | None = None,
) -> bool:
    """Apply fixed-anchor deletions without overlaying pristine target config."""

    if source_root_identity is not None:
        _validate_prepared_legacy_directory(source_root, source_root_identity)
    if anchor_root_identity is not None:
        _validate_prepared_legacy_directory(anchor_snapshot, anchor_root_identity)
    tombstone_names = _load_tombstone_names_from_state_path(
        anchor_snapshot / "state" / "character_tombstones.json"
    )
    if not tombstone_names:
        return False
    source_characters = _load_json_if_exists(
        source_root / "config" / "characters.json"
    )
    if not isinstance(source_characters, dict):
        return False
    filtered = deepcopy(source_characters)
    catgirls = filtered.get("猫娘")
    if not isinstance(catgirls, dict):
        catgirls = {}
    for deleted_name in tombstone_names:
        catgirls.pop(deleted_name, None)
    filtered["猫娘"] = catgirls
    if str(filtered.get("当前猫娘") or "") in tombstone_names:
        filtered["当前猫娘"] = next(iter(catgirls), "")
    _write_prepared_config_payloads(
        temp_root,
        {"characters.json": filtered},
        expected_root_identity=temp_root_identity,
    )
    if source_root_identity is not None:
        _validate_prepared_legacy_directory(source_root, source_root_identity)
    if anchor_root_identity is not None:
        _validate_prepared_legacy_directory(anchor_snapshot, anchor_root_identity)
    return True




_LEGACY_IMPORT_CHECKPOINT_KIND = "phase0_legacy_runtime_import_v1"


def _legacy_import_completion_from_checkpoint(
    config_manager,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate and expose only the phase-0 metadata owned by our checkpoint."""

    if payload.get("legacy_import_kind") != _LEGACY_IMPORT_CHECKPOINT_KIND:
        raise _unsafe_legacy_runtime_entry(
            Path(config_manager.app_docs_dir),
            "invalid_legacy_import_checkpoint",
        )
    target_root = Path(config_manager.app_docs_dir)
    checkpoint_target = str(payload.get("target_root") or "").strip()
    source = str(payload.get("legacy_import_source") or "").strip()
    legacy_private_state_source_identity = payload.get(
        "legacy_private_state_source_identity"
    )
    result = str(payload.get("legacy_import_result") or "").strip()
    repair_reason = str(payload.get("legacy_import_repair_reason") or "")
    copied_paths = payload.get("legacy_import_copied_paths")
    staged_snapshot = str(payload.get("source_root") or "").strip()
    staged_identity = payload.get("legacy_source_identity")
    backup_snapshot = str(payload.get("legacy_import_backup_path") or "").strip()
    backup_identity = payload.get("legacy_backup_identity")
    backup_baseline = payload.get("legacy_backup_baseline")
    if (
        not checkpoint_target
        or not paths_equal(checkpoint_target, target_root)
        or not source
        or not isinstance(legacy_private_state_source_identity, list)
        or len(legacy_private_state_source_identity) != 2
        or any(
            not isinstance(value, int)
            for value in legacy_private_state_source_identity
        )
        or result not in {"legacy_root_imported", "legacy_root_repaired_target"}
        or not isinstance(copied_paths, list)
        or any(not isinstance(path, str) for path in copied_paths)
        or not staged_snapshot
        or not isinstance(staged_identity, list)
        or len(staged_identity) != 2
        or any(not isinstance(value, int) for value in staged_identity)
        or not backup_snapshot
        or not isinstance(backup_identity, list)
        or len(backup_identity) != 2
        or any(not isinstance(value, int) for value in backup_identity)
        or not isinstance(backup_baseline, dict)
    ):
        raise _unsafe_legacy_runtime_entry(
            target_root,
            "invalid_legacy_import_checkpoint",
        )
    staged_path = Path(staged_snapshot)
    expected_prefix = f".{target_root.name}.legacy-source-"
    try:
        staged_parent_matches = paths_equal(staged_path.parent, target_root.parent)
    except (OSError, PathIdentityUnavailable, ValueError):
        staged_parent_matches = False
    if not staged_parent_matches or not staged_path.name.startswith(expected_prefix):
        raise _unsafe_legacy_runtime_entry(
            staged_path,
            "invalid_legacy_import_checkpoint",
        )
    backup_path = Path(backup_snapshot)
    try:
        backup_parent_matches = paths_equal(backup_path.parent, target_root.parent)
        backup_metadata = backup_path.lstat()
    except (OSError, PathIdentityUnavailable, ValueError):
        backup_parent_matches = False
        backup_metadata = None
    if (
        not backup_parent_matches
        or not backup_path.name.startswith(f".{target_root.name}.legacy-backup-")
        or backup_metadata is None
        or _is_link_like_metadata(backup_metadata)
        or not stat.S_ISDIR(backup_metadata.st_mode)
        or (
            int(backup_metadata.st_dev),
            int(backup_metadata.st_ino),
        )
        != tuple(backup_identity)
    ):
        raise _unsafe_legacy_runtime_entry(
            backup_path,
            "invalid_legacy_import_backup",
        )
    from utils.storage.migration import _snapshot_runtime_entries

    if _snapshot_runtime_entries(backup_path) != backup_baseline:
        raise _unsafe_legacy_runtime_entry(
            backup_path,
            "legacy_import_backup_changed",
        )
    return {
        "migrated": True,
        "source": source,
        "copied_paths": list(copied_paths),
        # Keep the exact pre-import target image visible to storage status and
        # cleanup.  The original legacy root is also left untouched, while the
        # shared engine's transaction remains private recovery authority.
        "backup_path": backup_snapshot,
        "repair_reason": repair_reason,
        "result": result,
        "_legacy_checkpoint": True,
        "_staged_snapshot": staged_snapshot,
        "_staged_identity": list(staged_identity),
        "_backup_identity": list(backup_identity),
        "_legacy_private_state_source_identity": list(
            legacy_private_state_source_identity
        ),
    }


def _publish_legacy_runtime_snapshot(
    config_manager,
    staged_snapshot: Path,
    completion: dict[str, Any],
    *,
    source_baseline: dict[str, dict[str, int | str]],
    staged_snapshot_identity: os.stat_result,
    legacy_origin_identity: tuple[int, int],
    target_boundary: dict[str, Any],
    backup_snapshot: Path,
    backup_snapshot_identity: os.stat_result,
    backup_baseline: dict[str, dict[str, int | str]],
) -> dict[str, Any]:
    """Publish only canonical runtime entries through the shared transaction."""

    from utils.storage.migration import (
        StorageMigrationError,
        build_pending_storage_migration_payload,
        load_storage_migration,
        replace_storage_migration_if_unchanged,
        run_pending_storage_migration,
    )

    target_root = Path(config_manager.app_docs_dir)
    target_entries = target_boundary.get("entries")
    target_identity = target_boundary.get("identity")
    if (
        not isinstance(target_entries, dict)
        or not isinstance(target_identity, tuple)
        or len(target_identity) != 2
        or any(not isinstance(value, int) for value in target_identity)
    ):
        raise _unsafe_legacy_runtime_entry(
            target_root,
            "invalid_target_snapshot_boundary",
        )
    managed_target_baseline = {
        relative_path: {
            key: value
            for key, value in target_entries[relative_path].items()
            if key != "directory_count"
        }
        for relative_path in RUNTIME_STORAGE_RELATIVE_PATHS
        if isinstance(target_entries.get(relative_path), dict)
        and str(target_entries[relative_path].get("kind") or "") != "missing"
    }
    staged_identity = _validate_prepared_legacy_directory(
        staged_snapshot,
        staged_snapshot_identity,
    )
    backup_identity = _validate_prepared_legacy_directory(
        backup_snapshot,
        backup_snapshot_identity,
    )
    payload = build_pending_storage_migration_payload(
        source_root=staged_snapshot,
        target_root=target_root,
        selection_source="legacy",
        confirmed_existing_target_content=True,
    )
    payload.update(
        {
            "legacy_import_kind": _LEGACY_IMPORT_CHECKPOINT_KIND,
            "legacy_import_source": str(completion["source"]),
            "legacy_private_state_source_identity": list(legacy_origin_identity),
            "legacy_import_result": str(completion["result"]),
            "legacy_import_repair_reason": str(
                completion.get("repair_reason") or ""
            ),
            "legacy_import_copied_paths": list(
                completion.get("copied_paths") or ()
            ),
            "layout_commit_mode": "preserve_existing",
            # Bridge the already-validated phase-0 generation into the shared
            # transaction.  The generic builder must not redefine either side
            # by accepting writes that raced this handoff.
            "target_baseline": managed_target_baseline,
            "legacy_target_identity": list(target_identity),
            "legacy_source_identity": [
                int(staged_snapshot_identity.st_dev),
                int(staged_snapshot_identity.st_ino),
            ],
            "legacy_source_baseline": dict(source_baseline),
            "legacy_import_backup_path": str(backup_snapshot),
            "legacy_backup_identity": [
                int(backup_snapshot_identity.st_dev),
                int(backup_snapshot_identity.st_ino),
            ],
            "legacy_backup_baseline": dict(backup_baseline),
        }
    )
    try:
        replace_storage_migration_if_unchanged(
            config_manager,
            None,
            payload,
            anchor_root=config_manager.anchor_root,
        )
    except StorageMigrationError as exc:
        if exc.error_code == "migration_checkpoint_conflict":
            raise _unsafe_legacy_runtime_entry(
                Path(config_manager.anchor_root),
                "storage_migration_checkpoint_conflict",
            ) from exc
        raise
    migration_result = run_pending_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    if not bool(migration_result.get("completed")):
        error_code = str(
            migration_result.get("error_code") or "legacy_runtime_publish_failed"
        )
        raise CloudsaveOperationError(
            "LEGACY_RUNTIME_ENTRY_UNSAFE",
            f"legacy runtime publication failed ({error_code})",
        )
    completed_payload = migration_result.get("payload")
    if not isinstance(completed_payload, dict):
        raise _unsafe_legacy_runtime_entry(
            target_root,
            "invalid_legacy_import_checkpoint",
        )
    return _legacy_import_completion_from_checkpoint(
        config_manager,
        completed_payload,
    )


def _legacy_snapshot_has_durable_checkpoint(
    config_manager,
    snapshot: Path,
    *,
    checkpoint_field: str = "source_root",
) -> bool:
    """Keep a private snapshot whenever checkpoint ownership is uncertain."""

    from utils.storage.migration import load_storage_migration

    try:
        payload = load_storage_migration(
            config_manager,
            anchor_root=config_manager.anchor_root,
        )
    except Exception:
        return True
    return bool(
        isinstance(payload, dict)
        and payload.get("legacy_import_kind") == _LEGACY_IMPORT_CHECKPOINT_KIND
        and str(payload.get(checkpoint_field) or "") == str(snapshot)
    )


def recover_interrupted_legacy_runtime_import(config_manager) -> dict[str, Any] | None:
    """Resume only a checkpoint created by phase-0 legacy import."""

    from utils.storage.migration import (
        is_storage_migration_pending,
        load_storage_migration,
        run_pending_storage_migration,
    )

    payload = load_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    if not isinstance(payload, dict) or payload.get(
        "legacy_import_kind"
    ) != _LEGACY_IMPORT_CHECKPOINT_KIND:
        return None
    if is_storage_migration_pending(payload):
        migration_result = run_pending_storage_migration(
            config_manager,
            anchor_root=config_manager.anchor_root,
        )
        if not bool(migration_result.get("completed")):
            error_code = str(
                migration_result.get("error_code")
                or "legacy_runtime_recovery_required"
            )
            raise CloudsaveOperationError(
                "LEGACY_RUNTIME_ENTRY_UNSAFE",
                f"legacy runtime recovery failed ({error_code})",
            )
        recovered_payload = migration_result.get("payload")
        if not isinstance(recovered_payload, dict):
            raise _unsafe_legacy_runtime_entry(
                Path(config_manager.app_docs_dir),
                "invalid_legacy_import_checkpoint",
            )
        payload = recovered_payload
    if str(payload.get("status") or "") != "completed":
        raise _unsafe_legacy_runtime_entry(
            Path(config_manager.app_docs_dir),
            "legacy_import_recovery_required",
        )
    return _legacy_import_completion_from_checkpoint(config_manager, payload)


def finalize_legacy_runtime_import_completion(
    config_manager,
    completion: dict[str, Any],
) -> None:
    """Retire the checkpoint only after root_state records the retained source."""

    from utils.storage.migration import (
        StorageMigrationError,
        _durable_rename_without_replacing,
        _private_directory_quarantine_path,
        _remove_owned_private_directory,
        _remove_private_directory_via_quarantine,
        _remove_transaction_root_if_owned,
        _validate_txid,
        load_storage_migration,
        replace_storage_migration_if_unchanged,
    )

    if not completion.get("_legacy_checkpoint"):
        return
    payload = load_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    if (
        not isinstance(payload, dict)
        or payload.get("legacy_import_kind") != _LEGACY_IMPORT_CHECKPOINT_KIND
        or str(payload.get("status") or "") != "completed"
    ):
        raise _unsafe_legacy_runtime_entry(
            Path(config_manager.app_docs_dir),
            "legacy_import_checkpoint_changed",
        )
    authoritative_completion = _legacy_import_completion_from_checkpoint(
        config_manager,
        payload,
    )
    for field_name in (
        "source",
        "backup_path",
        "repair_reason",
        "result",
        "_staged_snapshot",
    ):
        if completion.get(field_name) != authoritative_completion.get(field_name):
            raise _unsafe_legacy_runtime_entry(
                Path(config_manager.app_docs_dir),
                "legacy_import_completion_changed",
            )
    if (
        completion.get("copied_paths")
        != authoritative_completion.get("copied_paths")
        or completion.get("_staged_identity")
        != authoritative_completion.get("_staged_identity")
        or completion.get("_backup_identity")
        != authoritative_completion.get("_backup_identity")
        or completion.get("_legacy_private_state_source_identity")
        != authoritative_completion.get(
            "_legacy_private_state_source_identity"
        )
    ):
        raise _unsafe_legacy_runtime_entry(
            Path(config_manager.app_docs_dir),
            "legacy_import_completion_changed",
        )
    staged_snapshot = Path(str(completion.get("_staged_snapshot") or ""))
    staged_identity = completion.get("_staged_identity")
    target_root = Path(config_manager.app_docs_dir)
    try:
        staged_parent_matches = paths_equal(staged_snapshot.parent, target_root.parent)
    except (OSError, PathIdentityUnavailable, ValueError):
        staged_parent_matches = False
    if (
        not staged_parent_matches
        or not staged_snapshot.name.startswith(f".{target_root.name}.legacy-source-")
        or not isinstance(staged_identity, list)
        or len(staged_identity) != 2
        or any(not isinstance(value, int) for value in staged_identity)
    ):
        raise _unsafe_legacy_runtime_entry(
            staged_snapshot,
            "invalid_legacy_import_checkpoint",
        )
    quarantine = _private_directory_quarantine_path(staged_snapshot)
    try:
        current_identity = staged_snapshot.lstat()
    except FileNotFoundError:
        current_identity = None
    try:
        quarantine_identity = quarantine.lstat()
    except FileNotFoundError:
        quarantine_identity = None
    if current_identity is not None and quarantine_identity is not None:
        raise _unsafe_legacy_runtime_entry(
            staged_snapshot,
            "legacy_snapshot_cleanup_ambiguous",
        )
    if quarantine_identity is not None:
        quarantine_identity_tuple = (
            int(quarantine_identity.st_dev),
            int(quarantine_identity.st_ino),
        )
        if (
            _is_link_like_metadata(quarantine_identity)
            or not stat.S_ISDIR(quarantine_identity.st_mode)
            or quarantine_identity_tuple != tuple(staged_identity)
        ):
            raise _unsafe_legacy_runtime_entry(
                quarantine,
                "legacy_snapshot_identity_changed",
            )
        _durable_rename_without_replacing(quarantine, staged_snapshot)
        current_identity = staged_snapshot.lstat()
    if current_identity is not None:
        current_identity_tuple = (
            int(current_identity.st_dev),
            int(current_identity.st_ino),
        )
        if current_identity_tuple != tuple(staged_identity):
            raise _unsafe_legacy_runtime_entry(
                staged_snapshot,
                "legacy_snapshot_identity_changed",
            )
        if not _remove_private_directory_via_quarantine(
            staged_snapshot,
            current_identity,
            remove_quarantine=lambda owned_quarantine: (
                _remove_owned_private_directory(
                    owned_quarantine,
                    current_identity,
                )
            ),
        ):
            raise _unsafe_legacy_runtime_entry(
                staged_snapshot,
                "legacy_snapshot_cleanup_unverified",
            )
    # Keep the completed checkpoint until the private data copy is gone.  Then
    # convert it into the ordinary retained-source fact consumed by the
    # storage UI and cleanup endpoint.  Removing the checkpoint here would
    # leave the exact pre-import backup invisible and uncleanable.
    _legacy_import_completion_from_checkpoint(config_manager, payload)
    txid = _validate_txid(payload.get("txid"))
    raw_transaction_root = str(payload.get("transaction_root") or "").strip()
    if not raw_transaction_root:
        raise _unsafe_legacy_runtime_entry(
            target_root,
            "legacy_import_transaction_missing",
        )
    transaction_root = Path(raw_transaction_root)
    transaction_quarantine = _private_directory_quarantine_path(transaction_root)
    transaction_removed = _remove_transaction_root_if_owned(
        payload,
        transaction_root,
        txid,
    )
    if not transaction_removed and (
        transaction_root.exists()
        or transaction_root.is_symlink()
        or transaction_quarantine.exists()
        or transaction_quarantine.is_symlink()
    ):
        raise _unsafe_legacy_runtime_entry(
            transaction_root,
            "legacy_import_transaction_cleanup_unverified",
        )
    # The retained target preimage remains part of the completion contract.
    # Never downgrade the internal recovery checkpoint to an ordinary success
    # if it disappeared while the last rollback transaction was being retired.
    _legacy_import_completion_from_checkpoint(config_manager, payload)
    retained_checkpoint = dict(payload)
    retained_checkpoint["source_root"] = str(completion["source"])
    retained_checkpoint["backup_root"] = str(completion["backup_path"])
    retained_checkpoint["retained_source_root"] = str(completion["backup_path"])
    backup_identity = completion.get("_backup_identity")
    if (
        not isinstance(backup_identity, list)
        or len(backup_identity) != 2
        or any(not isinstance(value, int) for value in backup_identity)
    ):
        raise _unsafe_legacy_runtime_entry(
            Path(str(completion["backup_path"])),
            "invalid_legacy_import_backup",
        )
    retained_checkpoint["retained_source_identity"] = {
        "device": int(backup_identity[0]),
        "inode": int(backup_identity[1]),
    }
    retained_checkpoint["retained_source_mode"] = "manual_retention"
    retained_checkpoint["legacy_private_state_source_root"] = str(
        completion["source"]
    )
    for field_name in (
        "legacy_import_kind",
        "legacy_import_source",
        "legacy_import_result",
        "legacy_import_repair_reason",
        "legacy_import_copied_paths",
        "legacy_import_backup_path",
        "legacy_backup_identity",
        "legacy_backup_baseline",
        "legacy_target_identity",
        "legacy_source_identity",
        "legacy_source_baseline",
        "layout_commit_mode",
    ):
        retained_checkpoint.pop(field_name, None)
    try:
        replace_storage_migration_if_unchanged(
            config_manager,
            payload,
            retained_checkpoint,
            anchor_root=config_manager.anchor_root,
        )
    except StorageMigrationError as exc:
        if exc.error_code == "migration_checkpoint_conflict":
            raise _unsafe_legacy_runtime_entry(
                target_root,
                "legacy_import_checkpoint_changed",
            ) from exc
        raise


def _legacy_source_was_already_imported(
    root_state: Any,
    *,
    source_root: Path,
    target_root: Path,
) -> bool:
    """Treat legacy root import as a one-shot bootstrap repair per source root.

    Once publication is durably recorded (or the migrated target has completed
    a boot), future startups treat the current runtime root as authoritative.
    Otherwise, deletions in the new root can be resurrected from the stale one.
    """
    if not isinstance(root_state, dict):
        return False
    current_root = str(root_state.get("current_root") or "").strip()
    previous_source = str(root_state.get("last_migration_source") or "").strip()
    if not current_root or not previous_source:
        return False
    last_result = str(root_state.get("last_migration_result") or "")
    if not last_result.startswith("legacy_root_"):
        return False
    completed_boot = bool(str(root_state.get("last_successful_boot_at") or "").strip())
    backup_recorded = False
    backup_value = str(root_state.get("last_migration_backup") or "").strip()
    if backup_value:
        try:
            backup_metadata = Path(backup_value).lstat()
            backup_recorded = not _is_link_like_metadata(
                backup_metadata
            ) and stat.S_ISDIR(backup_metadata.st_mode)
        except OSError:
            backup_recorded = False
    if not completed_boot and not backup_recorded:
        return False

    def _lexically_same(left: str | Path, right: str | Path) -> bool:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
            os.path.abspath(os.fspath(right))
        )

    try:
        current_matches = paths_equal(current_root, target_root)
    except (OSError, PathIdentityUnavailable, ValueError):
        current_matches = _lexically_same(current_root, target_root)
    if not current_matches:
        return False
    try:
        return paths_equal(previous_source, source_root)
    except (OSError, PathIdentityUnavailable, ValueError):
        # The current root is already authoritative.  An unavailable identity
        # for the previously imported source must not authorize resurrection.
        # Skip every uncertain legacy candidate until identity is available.
        return True


_CLOUDSAVE_FACT_MANIFEST_MAX_BYTES = 4 * 1024 * 1024


def _snapshot_staged_cloudsave_fact(
    anchor_root: Path,
    *,
    expected_root_identity: tuple[int, int],
) -> dict[str, Any]:
    """Capture the fixed-anchor cloudsave gate without copying its payload.

    A non-manifest regular file anywhere below the declared directory proves
    staged content; the normal empty prefix-directory skeleton does not.  The
    negative cases (missing/empty/empty manifest) are revalidated at the same
    pinned root generation before publication.
    """

    from utils.storage.migration import (
        StorageMigrationError,
        _close_windows_directory_rename_guard,
        _ensure_opened_directory_still_named,
        _ensure_opened_entry_on_mount,
        _open_verified_directory,
        _open_windows_directory_rename_guard,
        _open_windows_directory_rename_guard_chain,
        _opened_mount_identity,
    )

    anchor_root = _canonicalize_legacy_root_boundary(anchor_root)

    def _manifest_fact_from_bytes(payload_bytes: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            payload = None
        files = payload.get("files") if isinstance(payload, dict) else None
        return {
            "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "size": len(payload_bytes),
            "has_files": bool(isinstance(files, dict) and files),
        }

    def _windows_tree_has_payload(directory: Path, *, root: Path) -> bool:
        directory_identity = directory.lstat()
        if _is_link_like_metadata(directory_identity) or not stat.S_ISDIR(
            directory_identity.st_mode
        ):
            raise StorageMigrationError(
                "path_type_unsupported",
                f"固定锚点 cloudsave 条目类型不安全: {directory}",
            )
        directory_guard = _open_windows_directory_rename_guard(
            directory,
            directory_identity,
        )
        try:
            with os.scandir(directory) as scanned:
                names = sorted(entry.name for entry in scanned)
            for name in names:
                if directory == root and name == "manifest.json":
                    continue
                child = directory / name
                child_identity = child.lstat()
                if _is_link_like_metadata(child_identity):
                    raise StorageMigrationError(
                        "path_symlink_unsupported",
                        f"固定锚点 cloudsave 包含链接或重解析点: {child}",
                    )
                if stat.S_ISREG(child_identity.st_mode):
                    return True
                if not stat.S_ISDIR(child_identity.st_mode):
                    raise StorageMigrationError(
                        "path_type_unsupported",
                        f"固定锚点 cloudsave 条目类型不安全: {child}",
                    )
                if _windows_tree_has_payload(child, root=root):
                    return True
            with os.scandir(directory) as scanned:
                final_names = sorted(entry.name for entry in scanned)
            if names != final_names or not os.path.samestat(
                directory_identity,
                directory.lstat(),
            ):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    f"固定锚点 cloudsave 目录在检查期间发生变化: {directory}",
                )
            return False
        finally:
            _close_windows_directory_rename_guard(directory_guard)

    def _posix_tree_has_payload(
        directory_fd: int,
        directory: Path,
        *,
        root_fd: int,
        root: Path,
        mount_identity: tuple[str, int],
    ) -> bool:
        with os.scandir(directory_fd) as scanned:
            names = sorted(entry.name for entry in scanned)
        directory_flags = (
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        )
        for name in names:
            if directory_fd == root_fd and name == "manifest.json":
                continue
            child = directory / name
            child_identity = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if _is_link_like_metadata(child_identity):
                raise StorageMigrationError(
                    "path_symlink_unsupported",
                    f"固定锚点 cloudsave 包含链接或重解析点: {child}",
                )
            if stat.S_ISREG(child_identity.st_mode):
                return True
            if not stat.S_ISDIR(child_identity.st_mode):
                raise StorageMigrationError(
                    "path_type_unsupported",
                    f"固定锚点 cloudsave 条目类型不安全: {child}",
                )
            child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
            try:
                if not os.path.samestat(child_identity, os.fstat(child_fd)):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        f"固定锚点 cloudsave 目录在打开期间被替换: {child}",
                    )
                _ensure_opened_entry_on_mount(
                    child_fd,
                    mount_identity,
                    child,
                )
                if _posix_tree_has_payload(
                    child_fd,
                    child,
                    root_fd=root_fd,
                    root=root,
                    mount_identity=mount_identity,
                ):
                    return True
                named_after = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if not os.path.samestat(child_identity, named_after):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        f"固定锚点 cloudsave 目录在检查期间被替换: {child}",
                    )
            finally:
                os.close(child_fd)
        with os.scandir(directory_fd) as scanned:
            final_names = sorted(entry.name for entry in scanned)
        if names != final_names:
            raise StorageMigrationError(
                "source_changed_during_migration",
                f"固定锚点 cloudsave 目录在检查期间发生变化: {directory}",
            )
        return False

    try:
        if os.name == "nt":
            anchor_identity = anchor_root.lstat()
            if (
                int(anchor_identity.st_dev),
                int(anchor_identity.st_ino),
            ) != tuple(expected_root_identity):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "固定锚点在 cloudsave 检查前被替换。",
                )
            anchor_guards = _open_windows_directory_rename_guard_chain(
                anchor_root,
                anchor_identity,
            )
            cloud_guard = -1
            try:
                cloudsave_root = anchor_root / "cloudsave"
                try:
                    cloud_identity = cloudsave_root.lstat()
                except FileNotFoundError:
                    return {
                        "staged": False,
                        "anchor_identity": list(expected_root_identity),
                        "cloudsave": "missing",
                    }
                if _is_link_like_metadata(cloud_identity) or not stat.S_ISDIR(
                    cloud_identity.st_mode
                ):
                    raise StorageMigrationError(
                        "path_type_unsupported",
                        "固定锚点 cloudsave 目录类型不安全。",
                    )
                cloud_guard = _open_windows_directory_rename_guard(
                    cloudsave_root,
                    cloud_identity,
                )
                with os.scandir(cloudsave_root) as scanned:
                    names = sorted(entry.name for entry in scanned)
                if _windows_tree_has_payload(
                    cloudsave_root,
                    root=cloudsave_root,
                ):
                    return {
                        "staged": True,
                        "anchor_identity": list(expected_root_identity),
                        "cloudsave_identity": [
                            int(cloud_identity.st_dev),
                            int(cloud_identity.st_ino),
                        ],
                        "reason": "payload_entry",
                    }
                with os.scandir(cloudsave_root) as scanned:
                    final_names = sorted(entry.name for entry in scanned)
                named_cloud_after_scan = cloudsave_root.lstat()
                named_anchor_after_scan = anchor_root.lstat()
                if (
                    names != final_names
                    or not os.path.samestat(cloud_identity, named_cloud_after_scan)
                    or (
                        int(named_anchor_after_scan.st_dev),
                        int(named_anchor_after_scan.st_ino),
                    )
                    != tuple(expected_root_identity)
                ):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "固定锚点 cloudsave 目录在检查期间发生变化。",
                    )
                if "manifest.json" not in names:
                    return {
                        "staged": False,
                        "anchor_identity": list(expected_root_identity),
                        "cloudsave_identity": [
                            int(cloud_identity.st_dev),
                            int(cloud_identity.st_ino),
                        ],
                        "manifest": "missing",
                    }
                manifest_path = cloudsave_root / "manifest.json"
                manifest_before = manifest_path.lstat()
                if _is_link_like_metadata(manifest_before) or not stat.S_ISREG(
                    manifest_before.st_mode
                ):
                    raise StorageMigrationError(
                        "path_type_unsupported",
                        "固定锚点 cloudsave manifest 类型不安全。",
                    )
                from utils.storage.community_private_state import (
                    _read_stable_regular_file,
                )

                try:
                    manifest_bytes, manifest_after = _read_stable_regular_file(
                        manifest_path,
                        manifest_before,
                        max_bytes=_CLOUDSAVE_FACT_MANIFEST_MAX_BYTES,
                    )
                except OSError as exc:
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "固定锚点 cloudsave manifest 无法稳定读取。",
                    ) from exc
                if not os.path.samestat(manifest_before, manifest_after):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "固定锚点 cloudsave manifest 在读取期间被替换。",
                    )
                with os.scandir(cloudsave_root) as scanned:
                    names_after_manifest = sorted(entry.name for entry in scanned)
                named_cloud_after_manifest = cloudsave_root.lstat()
                named_anchor_after_manifest = anchor_root.lstat()
                if (
                    names != names_after_manifest
                    or not os.path.samestat(
                        cloud_identity,
                        named_cloud_after_manifest,
                    )
                    or (
                        int(named_anchor_after_manifest.st_dev),
                        int(named_anchor_after_manifest.st_ino),
                    )
                    != tuple(expected_root_identity)
                ):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "固定锚点 cloudsave 目录在 manifest 检查期间发生变化。",
                    )
                manifest_fact = _manifest_fact_from_bytes(manifest_bytes)
                return {
                    "staged": bool(manifest_fact["has_files"]),
                    "anchor_identity": list(expected_root_identity),
                    "cloudsave_identity": [
                        int(cloud_identity.st_dev),
                        int(cloud_identity.st_ino),
                    ],
                    "manifest": manifest_fact,
                }
            finally:
                if cloud_guard >= 0:
                    _close_windows_directory_rename_guard(cloud_guard)
                while anchor_guards:
                    _close_windows_directory_rename_guard(anchor_guards.pop())

        anchor_fd = -1
        cloud_fd = -1
        manifest_fd = -1
        try:
            anchor_fd = _open_verified_directory(anchor_root)
            anchor_identity = os.fstat(anchor_fd)
            if (
                int(anchor_identity.st_dev),
                int(anchor_identity.st_ino),
            ) != tuple(expected_root_identity):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "固定锚点在 cloudsave 检查前被替换。",
                )
            mount_identity = _opened_mount_identity(anchor_fd)
            try:
                cloud_identity = os.stat(
                    "cloudsave",
                    dir_fd=anchor_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                _ensure_opened_directory_still_named(anchor_root, anchor_fd)
                return {
                    "staged": False,
                    "anchor_identity": list(expected_root_identity),
                    "cloudsave": "missing",
                }
            if _is_link_like_metadata(cloud_identity) or not stat.S_ISDIR(
                cloud_identity.st_mode
            ):
                raise StorageMigrationError(
                    "path_type_unsupported",
                    "固定锚点 cloudsave 目录类型不安全。",
                )
            cloud_fd = os.open(
                "cloudsave",
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=anchor_fd,
            )
            if not os.path.samestat(cloud_identity, os.fstat(cloud_fd)):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "固定锚点 cloudsave 目录在打开期间被替换。",
                )
            _ensure_opened_entry_on_mount(
                cloud_fd,
                mount_identity,
                anchor_root / "cloudsave",
            )
            with os.scandir(cloud_fd) as scanned:
                names = sorted(entry.name for entry in scanned)
            cloud_identity_fact = [
                int(cloud_identity.st_dev),
                int(cloud_identity.st_ino),
            ]
            if _posix_tree_has_payload(
                cloud_fd,
                anchor_root / "cloudsave",
                root_fd=cloud_fd,
                root=anchor_root / "cloudsave",
                mount_identity=mount_identity,
            ):
                return {
                    "staged": True,
                    "anchor_identity": list(expected_root_identity),
                    "cloudsave_identity": cloud_identity_fact,
                    "reason": "payload_entry",
                }
            if "manifest.json" not in names:
                named_cloud_identity = os.stat(
                    "cloudsave",
                    dir_fd=anchor_fd,
                    follow_symlinks=False,
                )
                if not os.path.samestat(cloud_identity, named_cloud_identity):
                    raise StorageMigrationError(
                        "source_changed_during_migration",
                        "固定锚点 cloudsave 目录在空目录校验期间被替换。",
                    )
                _ensure_opened_directory_still_named(
                    anchor_root,
                    anchor_fd,
                    error_code="source_changed_during_migration",
                    message="固定锚点在空 cloudsave 目录校验期间被替换。",
                )
                return {
                    "staged": False,
                    "anchor_identity": list(expected_root_identity),
                    "cloudsave_identity": cloud_identity_fact,
                    "manifest": "missing",
                }
            manifest_before = os.stat(
                "manifest.json",
                dir_fd=cloud_fd,
                follow_symlinks=False,
            )
            if _is_link_like_metadata(manifest_before) or not stat.S_ISREG(
                manifest_before.st_mode
            ):
                raise StorageMigrationError(
                    "path_type_unsupported",
                    "固定锚点 cloudsave manifest 类型不安全。",
                )
            manifest_fd = os.open(
                "manifest.json",
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=cloud_fd,
            )
            opened_manifest = os.fstat(manifest_fd)
            if not os.path.samestat(manifest_before, opened_manifest):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "固定锚点 cloudsave manifest 在打开期间被替换。",
                )
            if opened_manifest.st_size > _CLOUDSAVE_FACT_MANIFEST_MAX_BYTES:
                raise StorageMigrationError(
                    "path_type_unsupported",
                    "固定锚点 cloudsave manifest 超出安全读取上限。",
                )
            manifest_bytes = b""
            while len(manifest_bytes) <= _CLOUDSAVE_FACT_MANIFEST_MAX_BYTES:
                chunk = os.read(manifest_fd, min(1024 * 1024, _CLOUDSAVE_FACT_MANIFEST_MAX_BYTES + 1 - len(manifest_bytes)))
                if not chunk:
                    break
                manifest_bytes += chunk
            manifest_after = os.fstat(manifest_fd)
            manifest_named_after = os.stat(
                "manifest.json",
                dir_fd=cloud_fd,
                follow_symlinks=False,
            )
            with os.scandir(cloud_fd) as scanned:
                final_names = sorted(entry.name for entry in scanned)
            if (
                len(manifest_bytes) > _CLOUDSAVE_FACT_MANIFEST_MAX_BYTES
                or names != final_names
                or not os.path.samestat(opened_manifest, manifest_after)
                or not os.path.samestat(manifest_after, manifest_named_after)
                or opened_manifest.st_size != len(manifest_bytes)
            ):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "固定锚点 cloudsave manifest 在读取期间发生变化。",
                )
            named_cloud_after = os.stat(
                "cloudsave",
                dir_fd=anchor_fd,
                follow_symlinks=False,
            )
            if not os.path.samestat(cloud_identity, named_cloud_after):
                raise StorageMigrationError(
                    "source_changed_during_migration",
                    "固定锚点 cloudsave 目录在检查期间被替换。",
                )
            _ensure_opened_directory_still_named(anchor_root, anchor_fd)
            manifest_fact = _manifest_fact_from_bytes(manifest_bytes)
            return {
                "staged": bool(manifest_fact["has_files"]),
                "anchor_identity": list(expected_root_identity),
                "cloudsave_identity": cloud_identity_fact,
                "manifest": manifest_fact,
            }
        finally:
            if manifest_fd >= 0:
                os.close(manifest_fd)
            if cloud_fd >= 0:
                os.close(cloud_fd)
            if anchor_fd >= 0:
                os.close(anchor_fd)
    except StorageMigrationError as exc:
        raise _unsafe_legacy_runtime_entry(anchor_root, exc.error_code) from exc
    except OSError as exc:
        raise _unsafe_legacy_runtime_entry(
            anchor_root,
            "source_changed_during_migration",
        ) from exc


def import_legacy_runtime_root_if_needed(config_manager) -> dict[str, Any]:
    """One-time bootstrap import from legacy roots into the deterministic app data root."""
    recover_abandoned_legacy_import_preparation(config_manager)
    target_root = Path(config_manager.app_docs_dir)
    from utils.storage.migration import load_storage_migration

    existing_checkpoint = load_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    if isinstance(existing_checkpoint, dict):
        checkpoint_status = str(existing_checkpoint.get("status") or "").strip()
        checkpoint_target = str(
            existing_checkpoint.get("target_root") or ""
        ).strip()
        try:
            target_matches = bool(
                checkpoint_target and paths_equal(checkpoint_target, target_root)
            )
        except (OSError, PathIdentityUnavailable, ValueError):
            target_matches = False
        if checkpoint_status == "completed" and target_matches:
            # A completed explicit migration already made this target the
            # authoritative generation.  Re-importing any historical root can
            # resurrect user-deleted data and would overwrite its cleanup
            # checkpoint, so phase-0 is permanently out of this launch.
            return {
                "migrated": False,
                "source": "",
                "copied_paths": [],
                "backup_path": "",
                "repair_reason": "",
                "result": "target_root_already_initialized",
            }
        # Any other current checkpoint remains authoritative as well.  The
        # launcher may be waiting in maintenance for its recovery/restart
        # flow; phase-0 simply stays out and must never replace that record.
        return {
            "migrated": False,
            "source": "",
            "copied_paths": [],
            "backup_path": "",
            "repair_reason": "",
            "result": "storage_migration_checkpoint_active",
        }
    workspace_parent: Path | None = None
    target_snapshot: Path | None = None
    target_runtime_paths: list[str] = []
    target_optional_paths: list[str] = []
    target_snapshot_boundary: dict[str, Any] | None = None
    target_summary: dict[str, Any] | None = None
    # Preserve the old observable result even when there is no usable legacy
    # candidate. This is a shallow, non-blocking probe; the full target copy is
    # still deferred until an import candidate actually needs comparison.
    target_has_user_content = _runtime_root_has_user_content(
        target_root,
        config_manager=config_manager,
    )
    existing_root_state = None
    seen_source_roots: set[str] = set()

    try:
        for raw_source_root in config_manager.get_legacy_app_root_candidates():
            raw_source_root = Path(raw_source_root)
            if not _legacy_root_may_have_user_content(raw_source_root):
                continue
            source_root = _canonicalize_legacy_root_boundary(raw_source_root)
            source_key = (
                os.path.normcase(str(source_root))
                if os.name == "nt"
                else str(source_root)
            )
            if source_key in seen_source_roots:
                continue
            seen_source_roots.add(source_key)
            if workspace_parent is None:
                workspace_parent = target_root.parent.resolve(strict=True)
            target_snapshot = None
            target_runtime_paths = []
            target_optional_paths = []
            target_snapshot_boundary = None
            target_summary = None
            source_snapshot: Path | None = None
            anchor_snapshot: Path | None = None
            anchor_snapshot_boundary: dict[str, Any] | None = None
            staged_cloudsave_fact: dict[str, Any] | None = None
            merged_config_snapshot: Path | None = None
            target_backup_snapshot: Path | None = None
            attempt_id = uuid.uuid4().hex
            prepared_paths = {
                role: workspace_parent
                / f".{target_root.name}.{prefix}-{attempt_id}"
                for role, prefix in _LEGACY_PREPARE_PREFIXES.items()
            }
            _persist_legacy_import_preparation(
                config_manager,
                attempt_id=attempt_id,
                paths=prepared_paths,
            )
            try:
                prepared_identities: dict[str, os.stat_result] = {}
                for role, prepared_path in prepared_paths.items():
                    prepared_path.mkdir(mode=0o700, parents=False, exist_ok=False)
                    prepared_identity = prepared_path.lstat()
                    if (
                        _is_link_like_metadata(prepared_identity)
                        or not stat.S_ISDIR(prepared_identity.st_mode)
                    ):
                        raise _unsafe_legacy_runtime_entry(
                            prepared_path,
                            "legacy_preparation_path_changed",
                        )
                    prepared_identities[role] = prepared_identity
                    # Persist deletion authority immediately.  A later mkdir
                    # must not widen an earlier crash window in which a random
                    # name exists but no inode is authorized for cleanup.
                    _record_legacy_prepared_directory_identity(
                        config_manager,
                        attempt_id=attempt_id,
                        paths=prepared_paths,
                        role=role,
                        identity=prepared_identity,
                    )
                (
                    source_snapshot,
                    source_runtime_paths,
                    source_optional_paths,
                    _source_snapshot_boundary,
                ) = (
                    _create_private_runtime_snapshot(
                        source_root,
                        workspace_parent,
                        prefix=f".{target_root.name}.legacy-source-",
                        optional_names=LEGACY_OPTIONAL_STATE_FILES,
                        snapshot_root=prepared_paths["source"],
                        snapshot_root_precreated=True,
                        expected_snapshot_identity=prepared_identities["source"],
                        future_copy_multiplier=2,
                    )
                )
                source_summary = _runtime_root_summary(
                    config_manager,
                    source_snapshot,
                    expected_root_identity=prepared_identities["source"],
                )
                if not source_summary["has_user_content"]:
                    continue

                if target_snapshot is None:
                    (
                        target_snapshot,
                        target_runtime_paths,
                        target_optional_paths,
                        target_snapshot_boundary,
                    ) = (
                        _create_private_runtime_snapshot(
                            target_root,
                            workspace_parent,
                            prefix=f".{target_root.name}.legacy-target-",
                            optional_names=(),
                            snapshot_root=prepared_paths["target"],
                            snapshot_root_precreated=True,
                            expected_snapshot_identity=prepared_identities["target"],
                            reserved_copy_bytes=int(
                                _source_snapshot_boundary["copy_required_bytes"]
                            ),
                            reserved_copy_entries=int(
                                _source_snapshot_boundary["copy_required_entries"]
                            ),
                            # target snapshot + rollback backup + the target
                            # portion of the final merged shared-transaction
                            # source can coexist at the peak.
                            future_copy_multiplier=3,
                        )
                    )
                    target_summary = _runtime_root_summary(
                        config_manager,
                        target_snapshot,
                        expected_root_identity=prepared_identities["target"],
                    )
                    target_has_user_content = bool(target_summary["has_user_content"])
                    (
                        anchor_snapshot,
                        _anchor_runtime_paths,
                        _anchor_optional_paths,
                        anchor_snapshot_boundary,
                    ) = _create_private_runtime_snapshot(
                        Path(config_manager.anchor_root),
                        workspace_parent,
                        prefix=f".{target_root.name}.legacy-anchor-",
                        optional_names=TARGET_OPTIONAL_STATE_FILES,
                        include_runtime_directories=False,
                        snapshot_root=prepared_paths["anchor"],
                        snapshot_root_precreated=True,
                        expected_snapshot_identity=prepared_identities["anchor"],
                        reserved_copy_bytes=(
                            int(_source_snapshot_boundary["copy_required_bytes"])
                            + 2
                            * int(target_snapshot_boundary["copy_required_bytes"])
                        ),
                        reserved_copy_entries=(
                            int(_source_snapshot_boundary["copy_required_entries"])
                            + 2
                            * int(target_snapshot_boundary["copy_required_entries"])
                        ),
                    )
                    existing_root_state = _load_json_if_exists(
                        anchor_snapshot / "state" / "root_state.json"
                    )
                    staged_cloudsave_fact = _snapshot_staged_cloudsave_fact(
                        Path(config_manager.anchor_root),
                        expected_root_identity=tuple(
                            anchor_snapshot_boundary["identity"]
                        ),
                    )

                    if (
                        bool(staged_cloudsave_fact.get("staged"))
                        and not target_has_user_content
                    ):
                        return {
                            "migrated": False,
                            "source": "",
                            "copied_paths": [],
                            "backup_path": "",
                            "repair_reason": "",
                            "result": "target_root_preserves_staged_cloudsave_snapshot",
                        }

                if target_summary is None:
                    raise _unsafe_legacy_runtime_entry(
                        target_root,
                        "missing_target_snapshot_summary",
                    )
                if _legacy_source_was_already_imported(
                    existing_root_state,
                    source_root=source_root,
                    target_root=target_root,
                ):
                    continue

                should_repair, repair_reason = _legacy_root_provides_repair_benefit(
                    config_manager,
                    source_summary,
                    target_summary,
                )
                if target_has_user_content and not should_repair:
                    continue

                (
                    target_backup_snapshot,
                    _backup_runtime_paths,
                    _backup_optional_paths,
                    _backup_boundary,
                ) = _create_private_runtime_snapshot(
                    target_snapshot,
                    workspace_parent,
                    prefix=f".{target_root.name}.legacy-backup-",
                    optional_names=(),
                    snapshot_root=prepared_paths["backup"],
                    snapshot_root_precreated=True,
                    expected_source_identity=prepared_identities["target"],
                    expected_snapshot_identity=prepared_identities["backup"],
                    reserved_copy_bytes=int(
                        _source_snapshot_boundary["copy_required_bytes"]
                    )
                    + int(target_snapshot_boundary["copy_required_bytes"]),
                    reserved_copy_entries=int(
                        _source_snapshot_boundary["copy_required_entries"]
                    )
                    + int(target_snapshot_boundary["copy_required_entries"]),
                )

                merged_config_snapshot = prepared_paths["config"]
                _validate_prepared_legacy_directory(
                    merged_config_snapshot,
                    prepared_identities["config"],
                )
                if target_has_user_content:
                    _stage_merged_runtime_configs(
                        config_manager,
                        source_root=source_snapshot,
                        target_root=target_snapshot,
                        target_state_root=anchor_snapshot,
                        temp_root=merged_config_snapshot,
                        temp_root_identity=prepared_identities["config"],
                        target_summary=target_summary,
                        source_origin_root=source_root,
                        target_origin_root=target_root,
                        source_root_identity=prepared_identities["source"],
                        target_root_identity=prepared_identities["target"],
                        target_state_root_identity=prepared_identities["anchor"],
                    )
                    _merge_private_snapshot(
                        source_snapshot,
                        target_snapshot,
                        expected_base_identity=prepared_identities["source"],
                        expected_overlay_identity=prepared_identities["target"],
                    )
                    _merge_private_snapshot(
                        source_snapshot,
                        merged_config_snapshot,
                        expected_base_identity=prepared_identities["source"],
                        expected_overlay_identity=prepared_identities["config"],
                    )
                else:
                    # Only rebuildable caches and fixed state may overlay a
                    # missing target. Other seeded target data must not hide or
                    # overwrite the legacy source selected for bootstrap.
                    _merge_private_snapshot(
                        source_snapshot,
                        target_snapshot,
                        (*RUNTIME_CACHE_DIR_NAMES, "state"),
                        expected_base_identity=prepared_identities["source"],
                        expected_overlay_identity=prepared_identities["target"],
                    )
                    if _stage_legacy_characters_with_anchor_tombstones(
                        source_root=source_snapshot,
                        anchor_snapshot=anchor_snapshot,
                        temp_root=merged_config_snapshot,
                        temp_root_identity=prepared_identities["config"],
                        source_root_identity=prepared_identities["source"],
                        anchor_root_identity=prepared_identities["anchor"],
                    ):
                        _merge_private_snapshot(
                            source_snapshot,
                            merged_config_snapshot,
                            expected_base_identity=prepared_identities["source"],
                            expected_overlay_identity=prepared_identities["config"],
                        )

                from utils.storage.migration import (
                    StorageMigrationError,
                    _fsync_staged_tree,
                    _runtime_root_mount_identity,
                    _snapshot_path,
                )

                try:
                    _validate_prepared_legacy_directory(
                        source_snapshot,
                        prepared_identities["source"],
                    )
                    snapshot_mount = _runtime_root_mount_identity(source_snapshot)
                    _snapshot_path(
                        source_snapshot,
                        expected_mount_identity=snapshot_mount,
                    )
                    from utils.storage.migration import _snapshot_runtime_entries

                    source_publish_baseline = _snapshot_runtime_entries(
                        source_snapshot,
                        expected_mount_identity=snapshot_mount,
                    )
                    _fsync_staged_tree(source_snapshot)
                    _validate_prepared_legacy_directory(
                        source_snapshot,
                        prepared_identities["source"],
                    )
                except StorageMigrationError as exc:
                    raise _unsafe_legacy_runtime_entry(
                        source_snapshot,
                        exc.error_code,
                    ) from exc

                if target_snapshot_boundary is None:
                    raise _unsafe_legacy_runtime_entry(
                        target_root,
                        "missing_target_snapshot_boundary",
                    )
                _validate_private_snapshot_source_boundary(
                    source_root,
                    _source_snapshot_boundary,
                    optional_names=LEGACY_OPTIONAL_STATE_FILES,
                )
                _validate_private_snapshot_source_boundary(
                    target_root,
                    target_snapshot_boundary,
                    optional_names=(),
                )
                if anchor_snapshot_boundary is None:
                    raise _unsafe_legacy_runtime_entry(
                        Path(config_manager.anchor_root),
                        "missing_anchor_snapshot_boundary",
                    )
                _validate_private_snapshot_source_boundary(
                    Path(config_manager.anchor_root),
                    anchor_snapshot_boundary,
                    optional_names=TARGET_OPTIONAL_STATE_FILES,
                    include_runtime_directories=False,
                )
                if staged_cloudsave_fact is None:
                    raise _unsafe_legacy_runtime_entry(
                        Path(config_manager.anchor_root),
                        "missing_staged_cloudsave_fact",
                    )
                if not bool(staged_cloudsave_fact.get("staged")):
                    current_cloudsave_fact = _snapshot_staged_cloudsave_fact(
                        Path(config_manager.anchor_root),
                        expected_root_identity=tuple(
                            anchor_snapshot_boundary["identity"]
                        ),
                    )
                    if current_cloudsave_fact != staged_cloudsave_fact:
                        raise _unsafe_legacy_runtime_entry(
                            Path(config_manager.anchor_root) / "cloudsave",
                            "staged_cloudsave_fact_changed",
                        )

                copied_paths = sorted(source_publish_baseline)
                publication_result = (
                    "legacy_root_repaired_target"
                    if target_has_user_content
                    else "legacy_root_imported"
                )
                completion = {
                    "source": str(source_root),
                    "copied_paths": copied_paths,
                    "repair_reason": repair_reason,
                    "result": publication_result,
                }

                legacy_import = _publish_legacy_runtime_snapshot(
                    config_manager,
                    source_snapshot,
                    completion,
                    source_baseline=source_publish_baseline,
                    staged_snapshot_identity=prepared_identities["source"],
                    legacy_origin_identity=tuple(
                        _source_snapshot_boundary["identity"]
                    ),
                    target_boundary=target_snapshot_boundary,
                    backup_snapshot=target_backup_snapshot,
                    backup_snapshot_identity=prepared_identities["backup"],
                    backup_baseline={
                        relative_path: {
                            key: value
                            for key, value in snapshot.items()
                            if key != "directory_count"
                        }
                        for relative_path, snapshot in _backup_boundary[
                            "entries"
                        ].items()
                        if str(snapshot.get("kind") or "") != "missing"
                    },
                )
                return legacy_import
            finally:
                recover_abandoned_legacy_import_preparation(config_manager)
                target_snapshot = None
    finally:
        if target_snapshot is not None:
            recover_abandoned_legacy_import_preparation(config_manager)

    result = (
        "target_root_already_initialized"
        if target_has_user_content
        else "no_legacy_root_found"
    )

    return {
        "migrated": False,
        "source": "",
        "copied_paths": [],
        "backup_path": "",
        "repair_reason": "",
        "result": result,
    }
