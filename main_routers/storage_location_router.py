# -*- coding: utf-8 -*-
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

"""
Storage-location bootstrap API for the main web app.

Stage 3 keeps the same homepage bootstrap entry, adds the shutdown/restart
checkpoint flow, and exposes maintenance-state diagnostics for the web UI.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (API URLs omit trailing slashes) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import inspect
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator

from config import APP_NAME
from main_routers.shared_state import (
    get_config_manager,
    get_request_app_shutdown,
    get_release_storage_startup_barrier,
)
from utils.cloudsave_runtime import (
    ROOT_MODE_MAINTENANCE_READONLY,
    ROOT_MODE_NORMAL,
    cloudsave_disabled_reason,
    is_cloudsave_disabled_due_to_local_state_unavailable,
    set_root_mode,
)
from utils.config_manager import LocalStateDirectoryError
from utils.storage_location_bootstrap import (
    STORAGE_STARTUP_BLOCKING_REASONS,
    STORAGE_STATUS_POLL_INTERVAL_MS,
    build_storage_location_bootstrap_payload,
)
from utils.storage_migration import (
    MIGRATED_RUNTIME_ENTRY_NAMES,
    REGENERABLE_RUNTIME_ENTRY_NAMES,
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    V1_MIGRATED_RUNTIME_ENTRY_NAMES,
    StorageMigrationError,
    classify_entry_no_follow,
    copy_evidence_entries,
    create_pending_storage_migration,
    ensure_entry_parents,
    entry_parents_are_real_directories,
    delete_storage_migration,
    get_storage_migration_path,
    is_legacy_unproven_checkpoint,
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    load_storage_migration,
    metadata_fingerprint,
    move_entry_without_overwrite,
    private_cleanup_entry_name,
    private_cleanup_name,
    reconcile_finished_retained_cleanup,
    record_retained_cleanup_completed,
    record_retained_root_removal_started,
    clear_retained_root_removal_started,
    source_entries_referenced_by_config,
    v1_catch_up_skipped_entries,
    v1_catch_up_unfinished_entries,
    rewrite_migrated_config_paths,
    root_has_user_content,
    save_storage_migration,
    remove_runtime_entry,
    snapshot_runtime_entry,
)
from utils.storage_policy import (
    StorageSelectionValidationError,
    compute_anchor_root,
    get_storage_policy_path,
    is_runtime_root_available,
    load_storage_policy,
    normalize_runtime_root,
    paths_equal,
    save_storage_policy,
    validate_selected_root,
)
from utils.config_manager import get_config_manager as get_runtime_config_manager
from utils.root_state_lock import root_state_transaction

router = APIRouter(prefix="/api/storage/location", tags=["storage_location"])
logger = logging.getLogger(__name__)
_storage_mutation_lock = asyncio.Lock()

# _STORAGE_MUTATION_OFFLOAD_CONTRACT
#
# This file's storage-state write sequence already runs in a worker thread; pre-#2598
# _STORAGE_MUTATION_STAYS_ON_LOOP guidance is obsolete. #2598 resolved both original blockers
# individually rather than ignoring them; verify these guarantees before changing it:
#
# 1. Cancellation atomicity: put the entire sequence into one to_thread job.
#    delete_storage_migration -> save_storage_policy -> set_root_mode still contains
#    no await; all live in one synchronous closure submitted by _apply_storage_mutation_writes /
#    _run_locked_storage_job. Cancelling to_thread leaves its thread running to completion,
#    so the sequence cannot split. Guard: tests/unit/test_root_state_write_lock.py's
#     test_storage_write_primitives_never_sit_directly_in_an_async_body。
#
#    _run_locked_storage_job also loops until the worker finishes before propagating
#    CancelledError, or _storage_mutation_lock releases while writes continue,
#    allowing the next mutation request to interleave.
#
# 2. Unlocked root_state writers no longer exist.
#    build_storage_location_bootstrap_payload now defaults persist_reconcile=False;
#    GET /bootstrap,/status,/diagnostics,/retained-source and POST /exit are read-only;
#    only *_locked routes holding _storage_mutation_lock opt into persistence. Also,
#    root_state now uses utils/root_state_lock.py around full read-modify-write, rereading under lock.
#
# Rollback must also run in workers: ordinary write failures share their write transaction; recovery after
# successful writes but failed fencing/closing uses _run_locked_storage_job, awaiting worker termination before propagating cancellation.


class StorageLocationSelectionRequest(BaseModel):
    selected_root: str = Field(..., min_length=1, max_length=4096)
    selection_source: str = Field(default="user_selected", min_length=1, max_length=64)
    confirm_existing_target_content: bool = False

    @field_validator("selected_root", "selection_source")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("value cannot be empty")
        return stripped


class StorageLocationCleanupRequest(BaseModel):
    retained_root: str = Field(default="", min_length=0, max_length=4096)


class StorageLocationDirectoryPickerRequest(BaseModel):
    start_path: str = Field(default="", min_length=0, max_length=4096)


class _DirectoryPickerCancelled(Exception):
    pass


class _DirectoryPickerUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = str(error_code or "directory_picker_unavailable").strip() or "directory_picker_unavailable"
        self.message = str(message or "当前环境暂不支持系统目录选择，请手动输入路径。").strip() or "当前环境暂不支持系统目录选择，请手动输入路径。"


class _OpenStorageRootUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = str(error_code or "open_storage_root_unavailable").strip() or "open_storage_root_unavailable"
        self.message = str(message or "当前环境暂不支持直接打开目录。").strip() or "当前环境暂不支持直接打开目录。"


def _set_no_cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _reject_storage_mutation_when_cloudsave_disabled(response: Response) -> dict[str, Any] | None:
    if not is_cloudsave_disabled_due_to_local_state_unavailable():
        return None
    response.status_code = 409
    return {
        "ok": False,
        "error_code": "cloudsave_local_state_unavailable",
        "error": "本机状态目录不可用，当前会话已禁用云存档。请先修复本机 state 路径后重启应用，再进行存储位置变更。",
        "cloudsave_disabled": True,
        "cloudsave_disabled_reason": cloudsave_disabled_reason(),
    }


def _normalize_optional_path(value: Any) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    return str(normalize_runtime_root(raw_value))


def _path_is_within(candidate: Path | str | None, root: Path | str | None) -> bool:
    if not candidate or not root:
        return False
    candidate_path = normalize_runtime_root(candidate)
    root_path = normalize_runtime_root(root)
    try:
        candidate_path.relative_to(root_path)
        return True
    except ValueError:
        return False


def _dedupe_paths(paths: list[Path | str]) -> list[str]:
    normalized_paths: list[str] = []
    seen: set[str] = set()
    for candidate in paths:
        normalized = _normalize_optional_path(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_paths.append(normalized)
    return normalized_paths


def _get_storage_config_manager():
    try:
        return get_config_manager()
    except RuntimeError:
        # During limited startup, the storage bootstrap endpoints must stay usable
        # even if main_server shared_state has not been fully published yet.
        return get_runtime_config_manager(APP_NAME, migrate=False)


class _StorageStateUnreadable(RuntimeError):
    """The state file's raw bytes cannot be read right now.

    Deliberately kept apart from "the file does not exist": only
    ``FileNotFoundError`` proves absence, while every other read failure leaves
    existence unknown. Folding this class into "absent" makes the rollback
    unlink a state file that was merely unreadable for a moment -- the exact
    data loss this change fixes. Covers permission denied, file in use, path is
    a directory, unreachable directory and I/O errors.
    """

    def __init__(self, path: Path, cause: BaseException):
        super().__init__(f"storage state file cannot be read (existence unknown): {path}")
        self.path = Path(path)
        self.cause = cause


class _StorageRollbackPartialError(RuntimeError):
    """At least one rollback step failed, but the rest may have succeeded.

    ``_restore_storage_mutation_state`` is best-effort: the three state files
    (migration, policy, root_state) are restored independently, and a failing
    step does not stop the later ones. Once all three have run, this exception
    is raised whenever ``failures`` is non-empty, carrying the
    ``(step, path, exception)`` triple of every failed step for the caller's
    logging and error-code branch.

    Deliberately not fail-fast: when the migration checkpoint fails to restore,
    the policy file and root_state must still be pushed back to their
    pre-images -- "checkpoint not restored, policy left at the new value" is a
    worse half-state than "every step was attempted".
    """

    def __init__(self, failures: list[tuple[str, Path, BaseException]]):
        # failures is nonempty: use the first for the main message, retaining the full list in self.failures for logs.
        first_step, first_path, first_exc = failures[0]
        super().__init__(
            f"storage mutation rollback partially failed ({len(failures)} step(s)); "
            f"first: {first_step} @ {first_path}: {first_exc}"
        )
        self.failures = failures


class _StorageStateInvalid(RuntimeError):
    """Root state bytes cannot be decoded as a JSON object."""

    def __init__(self, path: Path, cause: BaseException):
        super().__init__(f"storage state file has invalid JSON or schema: {path}")
        self.path = Path(path)
        self.cause = cause


def _read_state_file_preimage(path: Path) -> dict[str, Any]:
    """Read one state file's rollback pre-image: whether it existed plus its raw bytes.

    A single rule decides existence: only ``FileNotFoundError`` means "did not
    exist" (so the rollback may delete it). Any other read failure only proves
    "cannot be read, existence unknown" and raises ``_StorageStateUnreadable``,
    stopping the whole write during the snapshot phase -- the snapshot and
    write() share one job, so write() never runs a single line.

    Deliberately bypasses ``load_storage_policy`` / ``load_storage_migration``:
    both fold "absent" and "unreadable" into None. Also stores bytes without
    parsing on purpose: a JSON file that is already corrupt must still be
    replayed byte-for-byte, and re-serializing would change the pre-image's
    formatting.
    """
    from utils.file_utils import read_bytes_tolerating_replace

    try:
        return {"existed": True, "bytes": read_bytes_tolerating_replace(path)}
    except FileNotFoundError:
        return {"existed": False, "bytes": None}
    except OSError as exc:
        raise _StorageStateUnreadable(path, exc) from exc


def _snapshot_storage_mutation_state(
    config_manager, *, anchor_root: Path, include_policy: bool = True, include_migration: bool = True,
) -> dict[str, Any]:
    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)

    # root_state preserves the strict loader's synthesized recovery state; classify all read failures as unreadable state.
    root_state, root_state_raw = _read_root_state_snapshot(config_manager)

    # Rollback needs only original bytes, without parsing or inventing another recovery basis.
    policy_preimage = _read_state_file_preimage(policy_path) if include_policy else None
    migration_preimage = _read_state_file_preimage(migration_path) if include_migration else None

    return {
        "root_state": root_state,
        "root_state_raw": root_state_raw,
        "include_policy": include_policy,
        "include_migration": include_migration,
        "policy_preimage": policy_preimage,
        "migration_preimage": migration_preimage,
    }


def _read_root_state_snapshot(config_manager) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        state, raw_state = config_manager.load_root_state_with_raw()
        if not isinstance(state, dict) or not isinstance(raw_state, dict):
            raise ValueError("root_state must be a JSON object")
        return state, raw_state
    except ValueError as exc:
        raise _StorageStateInvalid(
            Path(getattr(config_manager, "root_state_path", "root_state.json")), exc
        ) from exc
    except (OSError, LocalStateDirectoryError) as exc:
        raise _StorageStateUnreadable(
            Path(getattr(config_manager, "root_state_path", "root_state.json")), exc
        ) from exc


def _restore_state_file_from_preimage(path: Path, preimage: dict[str, Any]) -> None:
    """Restore one state file from its pre-image: replay the bytes if it existed, delete it if it did not.

    Only a snapshot that explicitly recorded "did not exist" may unlink.
    Unreadable files never reach this function -- the snapshot phase already
    stopped on ``_StorageStateUnreadable`` -- so a file that merely cannot be
    read is never deleted here.
    """
    if preimage.get("existed"):
        raw = preimage.get("bytes")
        if raw is None:
            # Construction should guarantee bytes when existed=True. Raise rather than silently skip,
            # which would falsely report successful rollback and leave partially restored state.
            raise RuntimeError("storage state pre-image exists but carries no bytes")
        # Skip writing when disk already equals the pre-image: either no write succeeded or earlier rollback
        # restored it. No write means no possible write failure, so first-step write failures honestly count
        # as successful rollback rather than failed restoration. Compare raw bytes without parsing.
        try:
            from utils.file_utils import read_bytes_tolerating_replace

            if read_bytes_tolerating_replace(path) == raw:
                logger.info("[storage_location] 状态文件已是快照内容，跳过回滚写入: %s", path)
                return
        except FileNotFoundError:
            # Currently absent after deletion: restore by writing.
            pass
        except OSError:
            # Unreadable current content cannot prove restoration; attempt writing below rather than skipping.
            pass
        # Local import matches this file's existing atomic-write imports.
        from utils.file_utils import atomic_write_bytes

        atomic_write_bytes(path, raw)
        return
    from utils.file_utils import unlink_tolerating_replace

    unlink_tolerating_replace(path, missing_ok=True)


def _restore_storage_mutation_state(
    config_manager, snapshot: dict[str, Any], *, anchor_root: Path,
) -> None:
    with root_state_transaction():
        _restore_storage_mutation_state_locked(config_manager, snapshot, anchor_root=anchor_root)


def _restore_storage_mutation_state_locked(
    config_manager,
    snapshot: dict[str, Any],
    *,
    anchor_root: Path,
) -> None:
    """Roll applicable storage state files back to a snapshot synchronously.

    Snapshot flags identify the files that participate in this rollback.

    Best-effort: migration / policy / root_state are each restored on their own,
    and a failing step does not stop the later ones. Once all three have run,
    any failure raises ``_StorageRollbackPartialError`` carrying the
    ``(step, path, exception)`` triple of every failed step.

    Deliberately not fail-fast: when the migration checkpoint fails to restore,
    the policy file and root_state must still be pushed back to their
    pre-images -- "checkpoint not restored, policy left at the new value" is a
    worse half-state than "every step was attempted".

    Keeping all three writes in one synchronous callable makes the sequence
    indivisible. Async callers submit the whole callable through
    ``_run_locked_storage_job``, which waits for the worker to finish before it
    propagates cancellation.
    """
    # Empty snapshots mean writing never started; nonempty snapshots require byte pre-images, with missing fields never implying absent files.
    if not snapshot:
        logger.warning("skipping storage mutation rollback: snapshot was never taken")
        return

    failures: list[tuple[str, Path, BaseException]] = []

    for step, path_fn in (
        ("migration", get_storage_migration_path),
        ("policy", get_storage_policy_path),
    ):
        if not snapshot.get(f"include_{step}", True):
            continue
        state_path = path_fn(config_manager, anchor_root=anchor_root)
        preimage = snapshot.get(f"{step}_preimage")
        try:
            if not isinstance(preimage, dict):
                raise RuntimeError(f"missing {step} pre-image")
            _restore_state_file_from_preimage(state_path, preimage)
        except Exception as exc:
            logger.exception("failed to restore %s, continuing remaining storage restores: %s", step, state_path)
            failures.append((step, Path(state_path), exc))

    # ---- Step three: root_state ----
    previous_root_state = snapshot.get("root_state")
    if isinstance(previous_root_state, dict):
        # root_state uses loaded-value comparison instead of byte snapshots because rollback restores synthesized state
        # reflecting original-path unavailability. Skip when disk already equals the snapshot,
        # as in _restore_state_file_from_preimage: avoiding writes avoids failure.
        # The shared recovery entry already holds root_state_transaction across reading and restoration writes.
        root_state_path = Path(getattr(config_manager, "root_state_path", ""))
        try:
            try:
                current_root_state = config_manager.load_raw_root_state(tolerate_replace=True)
            except Exception:
                current_root_state = None
            if current_root_state == previous_root_state or (
                "root_state_raw" in snapshot and current_root_state == snapshot["root_state_raw"]
            ):
                logger.info("[storage_location] root_state 已是快照内容，跳过回滚写入")
            else:
                config_manager.save_root_state(previous_root_state)
        except Exception as exc:
            logger.exception(
                "[storage_location] 回滚 root_state 失败: %s",
                root_state_path,
            )
            failures.append(("root_state", root_state_path, exc))

    # Attempt all three steps, then raise an aggregate error if any failed; return silently on full success.
    if failures:
        raise _StorageRollbackPartialError(failures)


async def _run_locked_storage_job(job: Callable[[], Any]) -> Any:
    """Run one storage job in a worker without letting the mutation lock slip.

    ``asyncio.to_thread`` cancellation only cancels the awaiting future — the
    worker keeps writing. Plain ``await asyncio.to_thread(...)`` therefore lets a
    client disconnect unwind the route's ``async with _storage_mutation_lock``
    while the worker is still mid-write, and the next mutation request walks
    straight into the lock and interleaves with it on the same three files.
    Before these writes moved off the loop they were uncancellable, so the lock
    really did cover them; this restores that.

    On cancellation we keep waiting for the worker and only then let the
    cancellation continue, so the lock is held for the worker's whole lifetime.
    """
    task = asyncio.ensure_future(asyncio.to_thread(job))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Loop until actual worker completion rather than suppressing once. A second cancellation (request
        # cancellation immediately followed by server shutdown) raises again at this await;
        # suppressing only once would release _storage_mutation_lock while writes continue,
        # defeating protection. Worker persistence is bounded, at worst adding 155ms backoff,
        # so this loop must terminate.
        while not task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({task})
        # Retrieve exceptions before leaving; otherwise asyncio emits
        # "Task exception was never retrieved" at GC, turning a write failure into an unattributable
        # log. Consume it only: cancellation already happened and the original CancelledError must propagate.
        if task.done() and not task.cancelled() and task.exception() is not None:
            logger.warning(
                "storage write job failed after the request was cancelled: %s",
                task.exception(),
            )
        raise


async def _apply_storage_mutation_writes(
    config_manager,
    *,
    anchor_root: Path,
    snapshot_out: dict[str, Any],
    write: Callable[[], Any],
    include_policy: bool = True,
    include_migration: bool = True,
) -> Any:
    """Take the rollback snapshot and run one storage-state write sequence off the loop.

    Snapshot and writes go into a single worker job on purpose. The sequences
    these routes run — ``delete_storage_migration`` → ``save_storage_policy`` →
    ``set_root_mode`` — have no await between them today, and that is load
    bearing: an await in the middle is a cancellation point that can leave the
    migration checkpoint deleted while root mode still says maintenance. One job
    keeps the sequence indivisible from the caller's point of view, because a
    cancelled ``to_thread`` still lets the worker run to completion.

    ``snapshot_out`` is filled in place rather than returned so the rollback
    snapshot survives a write that fails halfway through; returning it would
    lose it on exactly the path that needs it.
    """

    def _job() -> Any:
        # Snapshot, writes, and rollback must share one root_state_transaction().
        # Previously snapshot+writes shared a transaction, but rollback reacquired in another job; the gap
        # allowed third parties (cloudsave fence / cross-process launcher etc.) to update root_state,
        # then stale full-snapshot rollback overwrote their changes. Keeping rollback in the same transaction means
        # ordinary write() failures never release the lock, leaving no third-party insertion window.
        #
        # Also avoid snapshotting cloud_apply_fence's temporary mode and restoring it after fence exit;
        # snapshot and writes must share a transaction.
        with root_state_transaction():
            snapshot_out.clear()
            snapshot_out.update(_snapshot_storage_mutation_state(
                config_manager, anchor_root=anchor_root,
                include_policy=include_policy, include_migration=include_migration,
            ))
            # Snapshotting may raise _StorageStateUnreadable before write() runs at all;
            # the outer specific except handles that without entering try below.
            try:
                result = write()
            except Exception:
                # write() failure rolls back immediately under the same lock, eliminating third-party interleaving.
                try:
                    _restore_storage_mutation_state(
                        config_manager,
                        snapshot_out,
                        anchor_root=anchor_root,
                    )
                    snapshot_out["_write_outcome"] = "rolled_back"
                except Exception as rollback_exc:
                    # Rollback itself failed in at least one best-effort step: record outcome and exception;
                    # the outer except chooses an error code from _write_outcome.
                    snapshot_out["_write_outcome"] = "rollback_failed"
                    snapshot_out["_rollback_error"] = rollback_exc
                # Reraise write()'s original exception for outer error-code handling.
                raise
            snapshot_out["_write_outcome"] = "success"
            return result

    return await _run_locked_storage_job(_job)


async def _apply_storage_mutation_writes_or_rollback(
    config_manager,
    *,
    anchor_root: Path,
    snapshot_out: dict[str, Any],
    write: Callable[[], Any],
    include_policy: bool = True,
    include_migration: bool = True,
) -> tuple[Any, dict[str, Any] | None]:
    """Run one storage-state write; on failure roll back and return the shared failure body.

    Returns ``(policy_payload, write_error)``: on success ``write_error`` is
    None, on failure ``policy_payload`` is None. The three "select the current
    root" branches (recovering a failed migration, recovering an unavailable
    previous root, plain persistence) share identical write-failure semantics
    -- go back to the pre-image by the same rule and pick the error code by the
    same rule -- so the snapshot, the rollback and the error codes all live here
    instead of being written out three times, with two of them missing the
    rollback.

    The failure body carries only a stable user-safe message; the full
    exception always stays in the server log. The frontend picks its i18n
    string by ``error_code`` and never reads ``error``, so splicing ``exc`` in
    would only leak absolute paths and state-file names through the API
    response.

    ``snapshot_out`` is passed in and filled in place because the caller still
    needs it for ``_release_storage_startup_barrier_or_rollback``: a failed
    startup-barrier release also has to roll back from this same pre-image.
    """
    try:
        policy_payload = await _apply_storage_mutation_writes(
            config_manager,
            anchor_root=anchor_root,
            snapshot_out=snapshot_out,
            write=write,
            include_policy=include_policy,
            include_migration=include_migration,
        )
    except asyncio.CancelledError:
        # Worker finished. Successful writes still behind an uncleared fence require rollback; failed writes already
        # attempted rollback in their original transaction, so never submit a second job overwriting newer writers' state.
        if snapshot_out.get("_write_outcome") == "success":
            with suppress(Exception, asyncio.CancelledError):
                await _run_locked_storage_job(
                    partial(
                        _restore_storage_mutation_state,
                        config_manager,
                        snapshot_out,
                        anchor_root=anchor_root,
                    )
                )
        raise
    except _StorageStateInvalid as exc:
        logger.warning("invalid storage root state: %s", exc.path)
        return None, {
            "ok": False, "error_code": "storage_state_invalid",
            "error": "存储状态文件内容损坏或格式无效，未发生落盘改动，请检查或恢复状态文件。",
        }
    except _StorageStateUnreadable as exc:
        # State-file raw bytes are unreadable: permission denial, other-process locks, directory paths, inaccessible directories, or I/O errors.
        # Only FileNotFoundError proves absence, so existence here remains unknown.
        # Aborted during snapshot, before any write(): disk is unchanged from before the request. Neither roll back
        # nor claim restoration; honestly report unreadability and uncertain existence.
        # The old tolerant loader collapsed such files to None/absent, after which rollback unlinked them,
        # permanently deleting temporarily unreadable state. Give this its own error code.
        logger.warning(
            "[storage_location] 存储位置配置未写入：状态文件读不出来（是否还存在无法确认），已放弃本次落盘: %s",
            exc.path,
        )
        return None, {
            "ok": False,
            "error_code": "storage_state_unreadable",
            "error": "写入存储位置配置失败：状态文件当前无法读取（可能不存在，或所在目录不可访问），未发生落盘改动，请稍后重试或检查本机状态目录是否可访问。",
        }
    except Exception as exc:
        # Write failed, typically an unwritable local-state directory under sandbox / ransomware protection.
        # _job already performed synchronous rollback within the same root_state_transaction()
        # (see _apply_storage_mutation_writes). Never submit another rollback job here: the transaction gap
        # lets third parties update root_state before stale snapshots overwrite those changes.
        #
        # Four failure outcomes have different disk states and cannot share a restored-original-state claim:
        #   1. Snapshot found an unreadable state file: write() never ran, as handled above.
        #   2. Snapshot failed: write() never ran; disk remains at pre-image.
        #   3. Rollback succeeded: disk changed, then returned to pre-image.
        #   4. Rollback failed: disk may remain partially changed.
        # Frontend chooses fixed wording from error_code, not response error text; each outcome needs
        # a distinct code or users may wrongly decide immediate retries are safe.
        if not snapshot_out:
            # Specific branches above handle file read/format errors; do not attribute other pre-snapshot faults to write permissions.
            logger.exception("storage operation failed before a snapshot was taken")
            return None, {
                "ok": False,
                "error_code": "storage_operation_failed",
                "error": "提交存储位置操作失败，未发生落盘改动，请稍后重试。",
            }
        # _job records rollback results in snapshot_out["_write_outcome"]:
        #   - rolled_back: all three steps succeeded.
        #   - rollback_failed: at least one best-effort step failed; exception is in _rollback_error.
        outcome = snapshot_out.get("_write_outcome")
        if outcome != "rolled_back":
            # Rollback writes into the same unwritable directory and may fail too. Failed migration recovery is an
            # example: delete_storage_migration removed the checkpoint, and rollback's restoration write
            # is denied again. Disk has not returned to pre-image, so claiming restoration falsely encourages
            # retries against partially modified state.
            rollback_error = snapshot_out.get("_rollback_error")
            logger.warning(
                "[storage_location] 存储位置配置写入失败且回滚失败，盘上状态可能未回到 pre-image: "
                "写入异常=%s 回滚异常=%s",
                exc,
                rollback_error,
            )
            return None, {
                "ok": False,
                "error_code": "storage_policy_rollback_failed",
                "error": "写入存储位置配置失败，且未能恢复原有状态，请检查本机状态目录是否可写；若仍异常请手动确认状态文件。",
            }
        # Claim restoration only after verified rollback completion.
        logger.warning(
            "[storage_location] 存储位置配置写入失败，已回滚原有状态: %s",
            exc,
        )
        return None, {
            "ok": False,
            "error_code": "storage_policy_write_failed",
            "error": "写入存储位置配置失败，已恢复原有状态，请检查本机状态目录是否可写后重试。",
        }
    return policy_payload, None


async def _release_storage_startup_barrier_or_rollback(
    config_manager,
    *,
    snapshot: dict[str, Any],
    anchor_root: Path,
    reason: str,
) -> None:
    try:
        await _release_storage_startup_barrier_if_needed(reason=reason)
    except BaseException as release_exc:
        # Catch BaseException, not Exception: client disconnection produces CancelledError,
        # which would otherwise skip rollback precisely when writes persisted but fencing remains active.
        # The unconditional raise below preserves KeyboardInterrupt / SystemExit
        # semantics.
        try:
            restore = partial(_restore_storage_mutation_state, config_manager, snapshot, anchor_root=anchor_root)
            if isinstance(release_exc, asyncio.CancelledError):
                with suppress(asyncio.CancelledError):
                    await _run_locked_storage_job(restore)
            else:
                await _run_locked_storage_job(restore)
        except asyncio.CancelledError:
            snapshot["_write_outcome"] = "rollback_unknown"
            logger.warning("startup barrier rollback outcome unknown after cancellation")
            raise
        except Exception as rollback_exc:
            logger.exception(
                "failed to rollback storage mutation state after startup barrier release failed",
            )
            if isinstance(release_exc, Exception):
                if isinstance(rollback_exc, _StorageRollbackPartialError):
                    raise
                raise _StorageRollbackPartialError([
                    ("worker", Path(anchor_root), rollback_exc),
                ]) from rollback_exc
        raise


async def _release_storage_startup_barrier_result(
    config_manager, *, snapshot: dict[str, Any], anchor_root: Path, reason: str,
) -> tuple[int, dict[str, Any]] | None:
    try:
        await _release_storage_startup_barrier_or_rollback(
            config_manager, snapshot=snapshot, anchor_root=anchor_root, reason=reason,
        )
    except _StorageRollbackPartialError:
        return 500, {
            "ok": False, "error_code": "startup_release_rollback_failed",
            "phase": "startup_release",
            "error": "未能确认原有状态已恢复，请检查状态文件。",
        }
    except Exception:
        logger.exception("failed to release storage startup barrier")
        return 503, {
            "ok": False, "error_code": "startup_release_failed",
            "error": "当前会话暂时无法解除受限启动，请重试或刷新页面后再继续。",
        }
    return None


async def _complete_current_root_selection(
    config_manager, *, response: Response, anchor_root: Path, current_root: Path,
    write: Callable[[], Any], include_migration: bool = True,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    policy_payload, write_error = await _apply_storage_mutation_writes_or_rollback(
        config_manager, anchor_root=anchor_root, snapshot_out=snapshot,
        write=write, include_migration=include_migration,
    )
    if write_error is not None:
        response.status_code = 500
        return write_error
    release_error = await _release_storage_startup_barrier_result(
        config_manager, snapshot=snapshot, anchor_root=anchor_root,
        reason="storage_selection_continue_current_session",
    )
    if release_error is not None:
        response.status_code, error_body = release_error
        return error_body
    return {
        "ok": True, "result": "continue_current_session",
        "selected_root": str(current_root), "selection_source": policy_payload["selection_source"],
    }


def _safe_path_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return int(path.stat().st_size)
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if child.is_file():
                    total += int(child.stat().st_size)
            except OSError:
                continue
    return total


def _estimate_runtime_payload_bytes(source_root: Path) -> int:
    total = 0
    for name in MIGRATED_RUNTIME_ENTRY_NAMES:
        total += _safe_path_size(source_root / name)
    return total


def _target_root_has_user_content(target_root: Path, config_manager) -> bool:
    return root_has_user_content(target_root, config_manager=config_manager)


def _find_existing_ancestor(path: Path) -> Path:
    candidate = path.expanduser()
    while True:
        if candidate.exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            return candidate
        candidate = parent


def _path_chain_has_symlink(path: Path) -> bool:
    candidate = path.expanduser()
    while True:
        if candidate.exists():
            try:
                return candidate.is_symlink()
            except OSError:
                return False
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent


def _path_segments(path: Path) -> list[str]:
    return [
        segment.strip().lower()
        for segment in str(path).replace("\\", "/").split("/")
        if segment.strip()
    ]


def _is_cloud_sync_path_segment(segment: str) -> bool:
    normalized_segment = str(segment or "").strip().lower()

    def matches_client_folder(prefix: str) -> bool:
        if normalized_segment == prefix:
            return True
        if not normalized_segment.startswith(prefix):
            return False
        suffix = normalized_segment[len(prefix) :].lstrip()
        return bool(suffix) and suffix[0] in {"(", "-", "["}

    if any(
        matches_client_folder(prefix)
        for prefix in ("icloud drive", "google drive", "googledrive", "dropbox")
    ):
        return True
    return (
        normalized_segment == "onedrive"
        or normalized_segment.startswith("onedrive - ")
        or normalized_segment.startswith("onedrive (")
    )


def _collect_warning_codes(current_root: Path, target_root: Path) -> list[str]:
    warning_codes: list[str] = []
    raw_target = str(target_root)
    normalized_target = raw_target.replace("\\", "/").lower()

    if any(_is_cloud_sync_path_segment(segment) for segment in _path_segments(target_root)):
        warning_codes.append("sync_folder")
    if raw_target.startswith("\\\\") or normalized_target.startswith("//"):
        warning_codes.append("network_share")
    if _path_chain_has_symlink(target_root):
        warning_codes.append("symlink_path")

    if sys.platform == "win32":
        current_drive = str(current_root.drive or "").lower()
        target_drive = str(target_root.drive or "").lower()
        if current_drive and target_drive and current_drive != target_drive:
            warning_codes.append("external_volume")
    elif normalized_target.startswith("/volumes/") or normalized_target.startswith("/media/") or normalized_target.startswith("/mnt/"):
        warning_codes.append("external_volume")

    return sorted(set(warning_codes))


def _build_restart_preflight(
    current_root: Path,
    target_root: Path,
    *,
    config_manager=None,
    estimated_required_bytes: int | None = None,
    allow_existing_target_content: bool = False,
) -> dict[str, Any]:
    target_root = normalize_runtime_root(target_root)
    if estimated_required_bytes is None:
        estimated_required_bytes = _estimate_runtime_payload_bytes(current_root)
    existing_anchor = _find_existing_ancestor(target_root)

    target_free_bytes = 0
    try:
        target_free_bytes = int(shutil.disk_usage(str(existing_anchor)).free)
    except OSError:
        target_free_bytes = 0

    if target_root.exists():
        permission_probe = target_root
    else:
        permission_probe = existing_anchor
    permission_ok = os.access(str(permission_probe), os.W_OK)
    target_has_existing_content = bool(
        config_manager is not None
        and _target_root_has_user_content(target_root, config_manager)
    )
    requires_existing_target_confirmation = bool(
        target_has_existing_content
        and not allow_existing_target_content
    )

    blocking_error_code = ""
    blocking_error_message = ""
    if not permission_ok:
        blocking_error_code = "target_not_writable"
        blocking_error_message = "目标路径当前不可写，无法开始关闭后的迁移流程。"
    elif (
        estimated_required_bytes > 0
        and target_free_bytes > 0
        and target_free_bytes < estimated_required_bytes
    ):
        blocking_error_code = "insufficient_space"
        blocking_error_message = "目标卷剩余空间不足，无法安全执行关闭后的迁移。"

    return {
        "target_root": str(target_root),
        "estimated_required_bytes": estimated_required_bytes,
        "target_free_bytes": target_free_bytes,
        "permission_ok": permission_ok,
        "warning_codes": _collect_warning_codes(current_root, target_root),
        "target_has_existing_content": target_has_existing_content,
        "requires_existing_target_confirmation": requires_existing_target_confirmation,
        "existing_target_confirmation_message": (
            "目标路径已经包含现有数据。确认后迁移会覆盖目标中的同名运行时数据目录，"
            "目标目录中的其他文件会保留。请确认已选择正确目录。"
            if requires_existing_target_confirmation
            else ""
        ),
        "blocking_error_code": blocking_error_code,
        "blocking_error_message": blocking_error_message,
    }


def _load_committed_selected_root(config_manager, *, anchor_root: Path, fallback_root: Path) -> Path:
    policy = load_storage_policy(config_manager, anchor_root=anchor_root)
    if not isinstance(policy, dict):
        return fallback_root

    selected_root_value = str(policy.get("selected_root") or "").strip()
    if not selected_root_value:
        return fallback_root

    try:
        return normalize_runtime_root(selected_root_value)
    except Exception:
        return fallback_root


def _is_selected_root_missing_recovery(config_manager, *, current_root: Path, anchor_root: Path) -> bool:
    if not bool(getattr(config_manager, "recovery_committed_root_unavailable", False)):
        return False
    committed_selected_root = _load_committed_selected_root(
        config_manager,
        anchor_root=anchor_root,
        fallback_root=current_root,
    )
    return not paths_equal(committed_selected_root, current_root)


def _build_maintenance_message(bootstrap_payload: dict[str, Any]) -> str:
    blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
    last_error_summary = str(bootstrap_payload.get("last_error_summary") or "").strip()

    if blocking_reason == "migration_pending":
        return "正在优化存储布局，当前实例关闭后会继续迁移并自动恢复。"
    if blocking_reason == "recovery_required":
        return last_error_summary or "检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。"
    if blocking_reason == "selection_required":
        return "需要先确认本次运行使用的存储位置，主页主功能会继续保持阻断。"
    return ""


def _normalize_directory_picker_start_path(raw_value: str) -> str:
    candidate_text = str(raw_value or "").strip()
    if not candidate_text:
        return ""

    try:
        candidate = normalize_runtime_root(candidate_text)
    except Exception:
        candidate = Path(candidate_text).expanduser()
        if not candidate.is_absolute():
            return ""

    if candidate.exists() and candidate.is_dir():
        return str(candidate)

    current = candidate.parent
    while current != current.parent:
        if current.exists() and current.is_dir():
            return str(current)
        current = current.parent

    if current.exists() and current.is_dir():
        return str(current)
    return ""


def _resolve_executable_name(*candidates: str) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return candidates[0]


def _pick_directory_via_osascript(*, start_path: str) -> str:
    command = [_resolve_executable_name("/usr/bin/osascript", "osascript")]
    if start_path:
        safe_start_path = start_path.replace("\\", "\\\\").replace('"', '\\"')
        command.extend(
            [
                "-e",
                'tell application "Finder" to activate',
                "-e",
                f'set defaultLocation to POSIX file "{safe_start_path}"',
                "-e",
                'set selectedFolder to choose folder with prompt "请选择存储位置目录" default location defaultLocation',
            ]
        )
    else:
        command.extend(
            [
                "-e",
                'tell application "Finder" to activate',
                "-e",
                'set selectedFolder to choose folder with prompt "请选择存储位置目录"',
            ]
        )
    command.extend(["-e", "POSIX path of selectedFolder"])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前环境暂不支持系统目录选择，请手动输入路径。",
        ) from exc
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            f"打开系统目录选择器失败: {exc}",
        ) from exc

    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        if "User canceled" in stderr or "(-128)" in stderr:
            raise _DirectoryPickerCancelled()
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )

    selected_root = str(completed.stdout or "").strip()
    if not selected_root:
        raise _DirectoryPickerCancelled()
    return selected_root


def _pick_directory_via_powershell(*, start_path: str) -> str:
    powershell_executable = _resolve_executable_name(
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        "powershell.exe",
        "powershell",
        "pwsh.exe",
        "pwsh",
    )
    if not os.path.isabs(powershell_executable) and not shutil.which(powershell_executable):
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        )

    escaped_start_path = start_path.replace("'", "''")
    script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'N.E.K.O'
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.TopMost = $true
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '请选择存储位置目录'
$dialog.ShowNewFolderButton = $true
if ('{start_path}') {{
    $dialog.SelectedPath = '{start_path}'
}}
$owner.Show()
$owner.Activate()
$owner.BringToFront()
[System.Windows.Forms.Application]::DoEvents()
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $dialog.SelectedPath
    exit 0
}}
exit 2
""".strip().format(start_path=escaped_start_path)

    try:
        completed = subprocess.run(
            [powershell_executable, "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        ) from exc
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {exc}",
        ) from exc

    if completed.returncode == 2:
        raise _DirectoryPickerCancelled()
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )

    selected_root = str(completed.stdout or "").strip()
    if not selected_root:
        raise _DirectoryPickerCancelled()
    return selected_root


def _pick_directory_via_linux_dialog(*, start_path: str) -> str:
    commands: list[list[str]] = []
    zenity_executable = _resolve_executable_name("/usr/bin/zenity", "/bin/zenity", "zenity")
    if os.path.isabs(zenity_executable) and os.path.exists(zenity_executable) or shutil.which(zenity_executable):
        command = [zenity_executable, "--file-selection", "--directory", "--title=请选择存储位置目录"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)
    kdialog_executable = _resolve_executable_name("/usr/bin/kdialog", "/bin/kdialog", "kdialog")
    if os.path.isabs(kdialog_executable) and os.path.exists(kdialog_executable) or shutil.which(kdialog_executable):
        command = [kdialog_executable, "--getexistingdirectory"]
        if start_path:
            command.append(start_path)
        commands.append(command)
    yad_executable = _resolve_executable_name("/usr/bin/yad", "/bin/yad", "yad")
    if os.path.isabs(yad_executable) and os.path.exists(yad_executable) or shutil.which(yad_executable):
        command = [yad_executable, "--file-selection", "--directory", "--title=请选择存储位置目录"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)

    if not commands:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未安装可用的图形目录选择器。",
        )

    last_error = None
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except Exception as exc:
            last_error = exc
            continue

        if completed.returncode == 0:
            selected_root = str(completed.stdout or "").strip()
            if selected_root:
                return selected_root
            raise _DirectoryPickerCancelled()
        if completed.returncode in (1, 252):
            raise _DirectoryPickerCancelled()
        last_error = str(completed.stderr or "").strip() or completed.returncode

    raise _DirectoryPickerUnavailable(
        "directory_picker_failed",
        f"打开系统目录选择器失败: {last_error}",
    )


def _pick_storage_location_directory(*, start_path: str) -> str:
    # Project policy excludes Tk/Tcl. Trust only platform-native bridges (osascript / PowerShell /
    # zenity-kdialog-yad); failure raises _DirectoryPickerUnavailable so the frontend requests manual paths,
    # never tkinter fallback, whose tk.Tk() raises SystemExit and kills the backend when Nuitka lacks tk-inter.
    # scripts/check_no_tkinter.py enforces this.
    normalized_start_path = _normalize_directory_picker_start_path(start_path)
    if sys.platform == "darwin":
        return _pick_directory_via_osascript(start_path=normalized_start_path)
    if sys.platform == "win32":
        return _pick_directory_via_powershell(start_path=normalized_start_path)
    return _pick_directory_via_linux_dialog(start_path=normalized_start_path)


def _open_path_in_file_manager(path: Path | str) -> None:
    target_path = normalize_runtime_root(path)
    if not target_path.exists() or not target_path.is_dir():
        raise _OpenStorageRootUnavailable(
            "storage_root_unavailable",
            "当前数据目录不存在或不可访问。",
        )

    try:
        if sys.platform == "win32":
            os.startfile(str(target_path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target_path)])
            return

        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise _OpenStorageRootUnavailable(
                "open_storage_root_unavailable",
                "当前系统未找到可用的文件管理器打开命令。",
            )
        if os.path.basename(opener) == "gio":
            subprocess.Popen([opener, "open", str(target_path)])
        else:
            subprocess.Popen([opener, str(target_path)])
    except _OpenStorageRootUnavailable:
        raise
    except Exception as exc:
        raise _OpenStorageRootUnavailable(
            "open_storage_root_failed",
            f"打开当前数据目录失败: {exc}",
        ) from exc


def _build_status_payload(config_manager) -> dict[str, Any]:
    bootstrap_payload = build_storage_location_bootstrap_payload(config_manager)
    blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
    migration_payload = bootstrap_payload.get("migration") if isinstance(bootstrap_payload.get("migration"), dict) else {}
    completion_notice = _build_completed_migration_notice(config_manager, bootstrap_payload=bootstrap_payload)

    lifecycle_state = "ready"
    if blocking_reason == "migration_pending":
        lifecycle_state = "maintenance"
    elif blocking_reason == "recovery_required":
        lifecycle_state = "recovery_required"
    elif blocking_reason == "selection_required":
        lifecycle_state = "selection_required"

    return {
        "ok": True,
        "ready": lifecycle_state == "ready",
        "status": lifecycle_state,
        "lifecycle_state": lifecycle_state,
        "migration_stage": str(migration_payload.get("status") or "").strip(),
        "maintenance_message": _build_maintenance_message(bootstrap_payload),
        "poll_interval_ms": int(bootstrap_payload.get("poll_interval_ms") or STORAGE_STATUS_POLL_INTERVAL_MS),
        "effective_root": str(normalize_runtime_root(config_manager.app_docs_dir)),
        "last_error_summary": str(bootstrap_payload.get("last_error_summary") or "").strip(),
        "blocking_reason": blocking_reason,
        "completion_notice": completion_notice,
        "storage": {
            "selection_required": bool(bootstrap_payload.get("selection_required")),
            "migration_pending": bool(bootstrap_payload.get("migration_pending")),
            "recovery_required": bool(bootstrap_payload.get("recovery_required")),
            "legacy_cleanup_pending": bool(bootstrap_payload.get("legacy_cleanup_pending")),
            "stage": bootstrap_payload.get("stage") or "",
        },
        "migration": migration_payload,
    }


def _build_runtime_entry_diagnostic(
    *,
    name: str,
    write_root: Path | str,
    read_roots: list[Path | str],
    effective_root: Path | str,
    retained_source_root: Path | str | None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    normalized_write_root = _normalize_optional_path(write_root)
    normalized_read_roots = _dedupe_paths(read_roots)
    reads_outside_effective_root = [
        path for path in normalized_read_roots if not _path_is_within(path, effective_root)
    ]
    reads_from_retained_source_root = [
        path for path in normalized_read_roots if _path_is_within(path, retained_source_root)
    ]
    return {
        "name": name,
        "write_root": normalized_write_root,
        "read_roots": normalized_read_roots,
        "write_within_effective_root": _path_is_within(normalized_write_root, effective_root),
        "reads_outside_effective_root": reads_outside_effective_root,
        "reads_from_retained_source_root": reads_from_retained_source_root,
        "all_reads_within_effective_root": not reads_outside_effective_root,
        "notes": list(notes or []),
    }


def _build_storage_location_diagnostics_payload(config_manager) -> dict[str, Any]:
    bootstrap_payload = build_storage_location_bootstrap_payload(config_manager)
    migration_payload = (
        bootstrap_payload.get("migration")
        if isinstance(bootstrap_payload.get("migration"), dict)
        else {}
    )
    effective_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = normalize_runtime_root(config_manager.anchor_root)
    committed_selected_root = normalize_runtime_root(
        getattr(config_manager, "committed_selected_root", config_manager.app_docs_dir)
    )
    retained_source_root = _normalize_optional_path(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or ""
    )

    live2d_lookup = getattr(config_manager, "get_live2d_lookup_roots", None)
    if callable(live2d_lookup):
        live2d_read_roots = list(live2d_lookup())
    else:
        live2d_read_roots = [getattr(config_manager, "live2d_dir", effective_root / "live2d")]

    runtime_entries = {
        "config": _build_runtime_entry_diagnostic(
            name="config",
            write_root=config_manager.config_dir,
            read_roots=[config_manager.config_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "memory": _build_runtime_entry_diagnostic(
            name="memory",
            write_root=config_manager.memory_dir,
            read_roots=[config_manager.memory_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "plugins": _build_runtime_entry_diagnostic(
            name="plugins",
            write_root=config_manager.plugins_dir,
            read_roots=[config_manager.plugins_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "live2d": _build_runtime_entry_diagnostic(
            name="live2d",
            write_root=config_manager.live2d_dir,
            read_roots=live2d_read_roots,
            effective_root=effective_root,
            retained_source_root=retained_source_root,
            notes=(
                ["windows_cfa_fallback_read_enabled"]
                if bool(getattr(config_manager, "is_windows_cfa_fallback_active", False))
                else []
            ),
        ),
        "vrm": _build_runtime_entry_diagnostic(
            name="vrm",
            write_root=config_manager.vrm_dir,
            read_roots=[config_manager.vrm_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "mmd": _build_runtime_entry_diagnostic(
            name="mmd",
            write_root=config_manager.mmd_dir,
            read_roots=[config_manager.mmd_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "workshop": _build_runtime_entry_diagnostic(
            name="workshop",
            write_root=config_manager.workshop_dir,
            read_roots=[config_manager.workshop_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "character_cards": _build_runtime_entry_diagnostic(
            name="character_cards",
            write_root=config_manager.chara_dir,
            read_roots=[config_manager.chara_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "jukebox": _build_runtime_entry_diagnostic(
            name="jukebox",
            write_root=Path(config_manager.app_docs_dir) / "jukebox",
            read_roots=[Path(config_manager.app_docs_dir) / "jukebox"],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "avatar_tools": _build_runtime_entry_diagnostic(
            name="avatar_tools",
            write_root=config_manager.avatar_tools_dir,
            read_roots=[config_manager.avatar_tools_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
    }

    entries_with_reads_outside_effective_root = [
        name
        for name, payload in runtime_entries.items()
        if payload["reads_outside_effective_root"]
    ]
    entries_reading_retained_source_root = [
        name
        for name, payload in runtime_entries.items()
        if payload["reads_from_retained_source_root"]
    ]

    return {
        "ok": True,
        "layout": {
            "effective_root": str(effective_root),
            "committed_selected_root": str(committed_selected_root),
            "reported_current_root": _normalize_optional_path(
                getattr(config_manager, "reported_current_root", config_manager.app_docs_dir)
            ),
            "anchor_root": str(anchor_root),
            "retained_source_root": retained_source_root,
            "cloudsave_root": str(config_manager.cloudsave_dir),
            "state_root": str(config_manager.local_state_dir),
            "recovery_committed_root_unavailable": bool(
                getattr(config_manager, "recovery_committed_root_unavailable", False)
            ),
            "windows_cfa_fallback_active": bool(
                getattr(config_manager, "is_windows_cfa_fallback_active", False)
            ),
        },
        "runtime_entries": runtime_entries,
        "anchored_entries": {
            "cloudsave": {
                "root": _normalize_optional_path(config_manager.cloudsave_dir),
                "anchored_to": "anchor_root",
            },
            "state": {
                "root": _normalize_optional_path(config_manager.local_state_dir),
                "anchored_to": "anchor_root",
            },
        },
        "summary": {
            "runtime_entries_checked": len(runtime_entries),
            "entries_with_reads_outside_effective_root": entries_with_reads_outside_effective_root,
            "entries_reading_retained_source_root": entries_reading_retained_source_root,
            "all_runtime_entries_read_from_effective_root_only": not entries_with_reads_outside_effective_root,
        },
        "storage": {
            "selection_required": bool(bootstrap_payload.get("selection_required")),
            "migration_pending": bool(bootstrap_payload.get("migration_pending")),
            "recovery_required": bool(bootstrap_payload.get("recovery_required")),
            "blocking_reason": str(bootstrap_payload.get("blocking_reason") or "").strip(),
            "last_error_summary": str(bootstrap_payload.get("last_error_summary") or "").strip(),
        },
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_completed_migration_notice(
    config_manager,
    *,
    bootstrap_payload: dict[str, Any] | None = None,
    require_existing_retained_root: bool = False,
    persist_reconcile: bool = False,
) -> dict[str, Any]:
    # Only callers already holding _storage_mutation_lock may set persist_reconcile=True;
    # see build_storage_location_bootstrap_payload.
    bootstrap = (
        bootstrap_payload
        if isinstance(bootstrap_payload, dict)
        else build_storage_location_bootstrap_payload(
            config_manager,
            persist_reconcile=persist_reconcile,
        )
    )
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)
    persisted_migration = load_storage_migration(
        config_manager,
        anchor_root=anchor_root,
    )
    migration_payload = (
        persisted_migration
        if isinstance(persisted_migration, dict)
        else bootstrap.get("migration")
        if isinstance(bootstrap.get("migration"), dict)
        else {}
    )
    if str(migration_payload.get("status") or "").strip() != STORAGE_MIGRATION_STATUS_COMPLETED:
        return {
            "completed": False,
        }
    if str(migration_payload.get("retained_source_mode") or "").strip() == "cleaned":
        return {
            "completed": False,
        }

    target_root = str(migration_payload.get("target_root") or "").strip()
    source_root = str(migration_payload.get("source_root") or "").strip()
    retained_root = str(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or source_root
        or ""
    ).strip()
    retained_exists = bool(retained_root and Path(retained_root).exists())
    has_cleanup_proof = bool(copy_evidence_entries(migration_payload.get("copied_entries")))
    cleanup_available = (
        (has_cleanup_proof or is_legacy_unproven_checkpoint(migration_payload))
        and not is_storage_migration_pending(migration_payload)
        and is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        require_exists=True,
        allow_anchor_root=True,
        )
    )
    if require_existing_retained_root and not cleanup_available:
        return {
            "completed": False,
        }

    return {
        "completed": True,
        # Still in the old root because the new root had its own: the user
        # decides what to keep.
        "v1_catch_up_skipped": [
            entry_name
            for entry_name in v1_catch_up_skipped_entries(migration_payload)
            if retained_root and _entry_may_exist(Path(retained_root) / entry_name)
        ],
        "selection_source": str(migration_payload.get("selection_source") or "").strip(),
        "source_root": source_root,
        "target_root": target_root,
        "retained_root": retained_root,
        "retained_root_exists": retained_exists,
        "cleanup_available": cleanup_available,
        "completed_at": str(migration_payload.get("completed_at") or "").strip(),
        "message": "存储位置迁移已完成，旧数据目录当前仍保留，需手动清理。",
    }


def _entry_may_exist(path: Path) -> bool:
    """``False`` only when ``path`` is known to be gone.

    ``os.path.lexists`` also answers ``False`` when the lookup itself fails
    (a directory ACL or execute bit removed); reporting such an entry as
    cleaned would end the cleanup with the data still there.
    """
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        # A parent that is a plain file (a file named "state") proves a
        # nested entry below it cannot exist either.
        return False
    except OSError:
        return True
    return True


def _private_cleanup_name(entry_name: str) -> str:
    # The entry's own name stays readable in it, so an entry left behind by a
    # cleanup that stopped midway can always be told apart and put back.
    return private_cleanup_name(entry_name, uuid.uuid4().hex[:12])


_UNLISTABLE_RETAINED_ROOT = ".neko-cleanup-*"


def _nested_entry_parents(root: Path, entry_name: str) -> list[Path]:
    parts = entry_name.split("/")[:-1]
    return [root.joinpath(*parts[: index + 1]) for index in range(len(parts))]


def _private_cleanup_leftovers(retained_path: Path) -> list[tuple[str, Path]] | None:
    """Entries left under a private cleanup name; ``None`` if they cannot be listed.

    Their random suffix is only discoverable by listing the directory, so a
    directory that can be entered but not listed may still hide one.
    """
    try:
        children = sorted(retained_path.iterdir(), key=lambda child: child.name)
    except OSError:
        return None
    leftovers = []
    for child in children:
        entry_name = private_cleanup_entry_name(child.name)
        if entry_name is not None:
            leftovers.append((entry_name, child))
    return leftovers


def _restore_private_cleanup_leftovers(retained_path: Path) -> None:
    """Put entries a stopped cleanup left under a private name back under their own.

    Only while their own name is free; otherwise both stay, and the entry is
    reported under its own name until the user has sorted it out.
    """
    for entry_name, private in _private_cleanup_leftovers(retained_path) or []:
        if classify_entry_no_follow(private) is None:
            continue
        entry = retained_path / entry_name
        try:
            # A nested entry goes back below its own parent, recreated if
            # the cleanup removed it; never through a link.
            ensure_entry_parents(retained_path, entry_name)
        except (StorageMigrationError, OSError) as exc:
            logger.warning("Retained root cleanup could not put %s back from %s: %s", entry_name, private.name, exc)
            continue
        # Putting a directory back without a no-replace rename first reserves
        # the name with an empty directory; a restore stopped right there
        # leaves that empty reservation in the way. An empty directory holds
        # nothing to keep, so it gives way to the real entry.
        if classify_entry_no_follow(entry) == "dir":
            with suppress(OSError):
                if not any(entry.iterdir()):
                    entry.rmdir()
        try:
            move_entry_without_overwrite(private, entry)
        except OSError as exc:
            logger.warning("Retained root cleanup could not put %s back from %s: %s", entry_name, private.name, exc)


def _cleanup_retained_runtime_root(
    retained_path: Path,
    *,
    current_root: Path,
    anchor_root: Path,
    target_root: Path | str | None = None,
    copied_entries: dict | None = None,
    legacy_checkpoint: bool = False,
    catch_up_skipped: list[str] | tuple[str, ...] = (),
    before_root_removal: Callable[[], None] | None = None,
) -> tuple[tuple[str, ...], bool]:
    if not is_retained_root_cleanup_available(
        retained_path,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        require_exists=True,
        allow_anchor_root=True,
    ):
        raise ValueError("保留目录当前不满足安全清理条件。")

    # Everything below deletes children of ``retained_path``. Through a link or
    # junction that would reach into whatever directory it points at.
    if classify_entry_no_follow(retained_path) != "dir":
        raise ValueError("保留目录不是普通目录（可能是链接或 junction），拒绝清理。")
    # A cleanup stopped midway may have left entries under their private
    # names; they go back first and are then handled like any other entry.
    _restore_private_cleanup_leftovers(retained_path)

    proofs = copy_evidence_entries(copied_entries)
    normalized_target = normalize_runtime_root(target_root) if str(target_root or "").strip() else None
    proved_entries = list(proofs.items())
    # A checkpoint from before copy evidence existed (v1) has no record of
    # what was copied, so an entry is removed only when the target holds the
    # same entry with the same content right now. Anything else -- never
    # copied, copied partially, or changed in the target since -- stays for
    # the user to look at.
    # A v1 migration copied only the v1 list; entries added since were never
    # copied and stay where they are.
    migrated_names = V1_MIGRATED_RUNTIME_ENTRY_NAMES if legacy_checkpoint else MIGRATED_RUNTIME_ENTRY_NAMES
    legacy_entries = [
        entry_name
        for entry_name in migrated_names
        if legacy_checkpoint
        and normalized_target is not None
        and entry_name not in proofs
        and os.path.lexists(retained_path / entry_name)
        and os.path.lexists(normalized_target / entry_name)
    ]
    if normalized_target is None or (not proved_entries and not legacy_checkpoint):
        raise ValueError("迁移检查点没有可验证的复制证据，拒绝清理。")
    # The live config may have been pointed into the retained root since the
    # migration (a workshop folder, say); what it uses there stays.
    def _live_config_fingerprint() -> str | None:
        try:
            return metadata_fingerprint(normalized_target / "config")
        except (StorageMigrationError, OSError):
            return None

    live_config = {
        "fingerprint": _live_config_fingerprint(),
        "references": source_entries_referenced_by_config(
            config_root=normalized_target / "config",
            source_root=retained_path,
        ),
    }

    def _referenced_by_live_config(entry_name: str) -> bool:
        config_root = normalized_target / "config"
        if os.path.lexists(config_root) and classify_entry_no_follow(config_root) != "dir":
            # A link (or anything but a real directory): its fingerprint would
            # not change when what it points at is edited, so every entry
            # counts as possibly referenced and stays.
            return True
        # Scanned again when the config changed meanwhile: cleaning a large
        # entry takes a while, and an edit could point at one not done yet.
        fingerprint = _live_config_fingerprint()
        if fingerprint is None or fingerprint != live_config["fingerprint"]:
            live_config["fingerprint"] = fingerprint
            live_config["references"] = source_entries_referenced_by_config(
                config_root=normalized_target / "config",
                source_root=retained_path,
            )
        return entry_name in live_config["references"]

    # The evidence proves each entry was copied completely. What is deleted is
    # the retained copy, so it must still be exactly what was copied. The
    # target only has to still hold a real entry of the copied kind: running
    # the app on it since is the normal case and does not make the old copy
    # worth keeping, but a link (even a dangling one) or a different kind of
    # entry is no longer that copy. An entry that fails any check -- or
    # cannot be read right now -- is simply kept and reported among the
    # remaining entries; one bad entry must not block cleaning the others.
    # An entry already gone from the retained root -- removed by an earlier
    # partial cleanup, or by the user after one was reported -- needs nothing
    # more and is not reported, so a repeated request finishes the checkpoint.
    #
    # Each entry is renamed to a private name next to it before its content
    # is compared, and only that private copy is deleted: a writer still
    # using the old path (a sync client, say) can then only create a new
    # entry there, which stays, instead of losing what it wrote together
    # with the copy that was checked.
    def _target_still_holds_copy(entry_name: str, proof: dict) -> bool:
        target_manifest = proof.get("target_manifest")
        expected_kind = target_manifest.get("kind") if isinstance(target_manifest, dict) else None
        if not entry_parents_are_real_directories(normalized_target, entry_name):
            # Through a linked parent the target's entry is somewhere else.
            return False
        try:
            return classify_entry_no_follow(normalized_target / entry_name) == expected_kind
        except (StorageMigrationError, OSError):
            return False

    # The target as it was when a legacy entry was compared against it; it has
    # to be the same still when the retained copy goes.
    compared_target_fingerprints: dict[str, str] = {}

    def _matches_target(entry_name: str, retained_entry: Path) -> bool:
        compared_target_fingerprints[entry_name] = metadata_fingerprint(normalized_target / entry_name)
        retained_manifest = snapshot_runtime_entry(retained_entry)
        target_manifest = snapshot_runtime_entry(normalized_target / entry_name)
        if entry_name != "config" or retained_manifest["kind"] != "dir":
            return retained_manifest == target_manifest
        # A v1 migration rebased workshop paths under the source root onto the
        # target while copying config, so compare the retained copy after the
        # same rewrite instead of byte for byte.
        with tempfile.TemporaryDirectory(prefix="neko-cleanup-") as scratch:
            scratch_root = Path(scratch)
            shutil.copytree(retained_entry, scratch_root / "config", symlinks=True)
            # copytree keeps read-only modes; the rewrite below must be able
            # to replace workshop_config.json in this throwaway copy.
            # copytree kept links as links; chmod would follow one to a file
            # outside the retained root, so links are left alone (the
            # manifest comparison rejects them anyway).
            for scratch_dir, _dir_names, file_names in os.walk(scratch_root / "config"):
                os.chmod(scratch_dir, stat.S_IMODE(os.stat(scratch_dir).st_mode) | stat.S_IRWXU)
                for file_name in file_names:
                    scratch_file = os.path.join(scratch_dir, file_name)
                    file_stat = os.lstat(scratch_file)
                    if not stat.S_ISREG(file_stat.st_mode):
                        continue
                    os.chmod(scratch_file, stat.S_IMODE(file_stat.st_mode) | stat.S_IRUSR | stat.S_IWUSR)
            rewrite_migrated_config_paths(
                source_root=retained_path,
                target_root=normalized_target,
                config_root=scratch_root,
            )
            return snapshot_runtime_entry(scratch_root / "config") == target_manifest

    def _target_unchanged_since_compared(entry_name: str) -> bool:
        # Comparing a large entry (config is even copied and rewritten first)
        # takes a while; a target changed meanwhile no longer proves the copy.
        expected = compared_target_fingerprints.get(entry_name)
        return expected is not None and metadata_fingerprint(normalized_target / entry_name) == expected

    def _delete_if_still_matching(
        entry_name: str,
        matches: Callable[[Path], bool],
        target_ok: Callable[[], bool],
    ) -> None:
        entry = retained_path / entry_name
        if not entry_parents_are_real_directories(retained_path, entry_name):
            # Through a linked parent the entry is somewhere else entirely.
            logger.warning("Retained root cleanup kept %s: a parent is not a real directory", entry_name)
            return
        if classify_entry_no_follow(entry) is None:
            # A link or special file: putting it back later could turn it
            # into a hard link to what it points at. Leave it in place.
            logger.warning("Retained root cleanup kept %s: not a regular file or directory", entry_name)
            return
        private = retained_path / _private_cleanup_name(entry_name)
        try:
            os.rename(entry, private)
        except OSError as exc:
            # In use (Windows refuses to rename a directory with open files):
            # it stays and shows up in remaining_entries.
            logger.warning("Retained root cleanup kept %s: %s", entry_name, exc)
            return
        try:
            # The target is checked again last: comparing a large entry takes
            # a while, and the target must still be there when its retained
            # copy goes.
            still_matches = matches(private) and target_ok()
        except (StorageMigrationError, OSError):
            # Unreadable now (an app writing to the target, a locked file).
            still_matches = False
        if still_matches:
            try:
                remove_runtime_entry(private)
                return
            except (StorageMigrationError, OSError) as exc:
                # A file locked by an antivirus scan or Explorer preview: what
                # is left goes back under its name and is reported, so the
                # user knows what to finish by hand.
                logger.warning("Retained root cleanup kept %s: %s", entry_name, exc)
        try:
            move_entry_without_overwrite(private, entry)
        except OSError as exc:
            # Something new took the name meanwhile: keep both; the entry is
            # reported under its own name and put back once that is free.
            logger.warning("Retained root cleanup left %s under %s: %s", entry_name, private.name, exc)

    for entry_name, proof in proved_entries:
        if _referenced_by_live_config(entry_name):
            continue
        if os.path.lexists(retained_path / entry_name) and _target_still_holds_copy(entry_name, proof):
            source_manifest = proof.get("source_manifest")
            _delete_if_still_matching(
                entry_name,
                lambda path, expected=source_manifest: snapshot_runtime_entry(path) == expected,
                # Still the recorded kind, not merely present: a directory
                # replaced by a file meanwhile is no longer the copy.
                lambda name=entry_name, recorded=proof: (
                    _target_still_holds_copy(name, recorded) and not _referenced_by_live_config(name)
                ),
            )
    for entry_name in legacy_entries:
        if _referenced_by_live_config(entry_name):
            continue
        _delete_if_still_matching(
            entry_name,
            lambda path, name=entry_name: _matches_target(name, path),
            lambda name=entry_name: _target_unchanged_since_compared(name) and not _referenced_by_live_config(name),
        )

    # The anchor root holds more than runtime data (state, cloud saves) and
    # always stays. Any other retained root goes once emptied; files the user
    # kept in it stay put.
    def _collect_remaining() -> tuple[str, ...]:
        leftovers = _private_cleanup_leftovers(retained_path)
        return tuple(
            dict.fromkeys(
                # A v1 checkpoint also carries evidence for what was copied
                # over later; those entries are reported like any other.
                [
                    entry_name
                    # So is what it had to leave behind because the new root
                    # had its own: never deleted here, only reported.
                    # Every migrated name counts here, a v1 checkpoint's too:
                    # one restored in the old root after its catch-up (a sync
                    # client) is data the cleanup must not pass over.
                    for entry_name in dict.fromkeys([*MIGRATED_RUNTIME_ENTRY_NAMES, *proofs, *catch_up_skipped])
                    if _entry_may_exist(retained_path / entry_name)
                ]
                + [entry_name for entry_name, _private in leftovers or []]
                # Not listable: an entry may still hide under a private name,
                # so the cleanup must stay pending rather than be recorded as
                # done.
                + ([_UNLISTABLE_RETAINED_ROOT] if leftovers is None else [])
            )
        )

    remaining_entries = _collect_remaining()
    retained_root_kept = paths_equal(retained_path, anchor_root)
    if not retained_root_kept:
        # Directories the app recreates (old logs, plugin install records)
        # would otherwise keep the old root alive. They go only when nothing
        # migrated is left: while the user still has entries to sort out
        # here, the old logs may help. The anchor root holds the storage
        # policy and cloud saves and is never removed, so there is nothing
        # to gain from emptying its regenerable directories.
        if not remaining_entries:
            if before_root_removal is not None:
                before_root_removal()
            # Parents of nested entries, now empty, would keep it too -- a v1
            # catch-up's (state/game_scores) as well; only empty ones go.
            for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES:
                for parent in reversed(_nested_entry_parents(retained_path, entry_name)):
                    with suppress(OSError):
                        parent.rmdir()
            for entry_name in REGENERABLE_RUNTIME_ENTRY_NAMES:
                try:
                    remove_runtime_entry(retained_path / entry_name)
                except (StorageMigrationError, OSError) as exc:
                    logger.warning("Retained root cleanup kept %s: %s", entry_name, exc)
        if remaining_entries:
            # Not even tried: had the last of them gone just now (a sync
            # client), the root would be removed while this result still says
            # it stays, and with no removal record a later run could not
            # tell it apart from an unmounted disk. The next cleanup removes it.
            retained_root_kept = True
        else:
            try:
                retained_path.rmdir()
            except FileNotFoundError:
                # Already gone, e.g. removed by the user meanwhile: nothing is kept.
                pass
            except OSError:
                retained_root_kept = True
                # Removing large regenerable directories takes a while; an
                # entry put back meanwhile (a sync client) must keep the
                # cleanup pending rather than be left there unannounced.
                remaining_entries = _collect_remaining()
    return remaining_entries, retained_root_kept


async def _release_storage_startup_barrier_if_needed(*, reason: str) -> None:
    callback = get_release_storage_startup_barrier()
    if not callable(callback):
        return

    result = callback(reason=reason)
    if inspect.isawaitable(result):
        await result


class _ShutdownAcceptedCancellation(asyncio.CancelledError):
    """Cancellation delivered after the shutdown callback completed successfully."""


async def _request_app_shutdown(request_app_shutdown) -> None:
    result = request_app_shutdown()
    if inspect.isawaitable(result):
        # Keep a handle so a cancellation queued after callback completion but
        # before this waiter resumes can be distinguished from cancellation of
        # an in-flight request. The former means shutdown is already committed
        # and the pending migration must not be rolled back.
        shutdown_task = asyncio.ensure_future(result)
        try:
            await shutdown_task
        except asyncio.CancelledError as exc:
            if (
                shutdown_task.done()
                and not shutdown_task.cancelled()
                and shutdown_task.exception() is None
            ):
                raise _ShutdownAcceptedCancellation(*exc.args) from exc
            raise


def _restart_write_error(
    write_error: dict[str, Any], *, restart_mode: str, preflight: dict[str, Any],
) -> dict[str, Any]:
    payload = {**write_error, "restart_mode": restart_mode, **preflight}
    if write_error.get("error_code") == "storage_policy_rollback_failed":
        payload.update(
            error_code="restart_rollback_failed",
            error="受控重启失败且未能确认原有状态已恢复，请检查或恢复状态文件。",
        )
    return payload


async def _request_shutdown_or_rollback(
    config_manager, request_app_shutdown, *, snapshot: dict[str, Any],
    anchor_root: Path, restart_mode: str, preflight: dict[str, Any],
) -> dict[str, Any] | None:
    restore = partial(_restore_storage_mutation_state, config_manager, snapshot, anchor_root=anchor_root)
    try:
        await _request_app_shutdown(request_app_shutdown)
    except _ShutdownAcceptedCancellation:
        raise
    except asyncio.CancelledError:
        with suppress(Exception, asyncio.CancelledError):
            await _run_locked_storage_job(restore)
        raise
    except Exception:
        logger.exception("failed to schedule storage shutdown: %s", restart_mode)
        try:
            await _run_locked_storage_job(restore)
        except Exception:
            logger.exception("storage shutdown rollback failed or outcome unknown: %s", restart_mode)
            return {
                "ok": False, "error_code": "restart_rollback_failed",
                "error": "受控关闭启动失败，未能确认原有状态已恢复，请检查状态文件。",
                "restart_mode": restart_mode, **preflight,
            }
        return {
            "ok": False, "error_code": "restart_schedule_failed",
            "error": "受控关闭启动失败，请稍后重试。",
            "restart_mode": restart_mode, **preflight,
        }
    return None


@router.get("/bootstrap")
async def get_storage_location_bootstrap(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    return build_storage_location_bootstrap_payload(config_manager)


@router.get("/status")
async def get_storage_location_status(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    return _build_status_payload(config_manager)


@router.post("/exit")
async def post_storage_location_exit(request: Request, response: Response):
    _set_no_cache_headers(response)

    if request.headers.get("X-Neko-Storage-Action") != "exit":
        response.status_code = 403
        return {
            "ok": False,
            "error_code": "storage_exit_forbidden",
            "error": "缺少存储退出确认标记。",
        }

    disabled_response = _reject_storage_mutation_when_cloudsave_disabled(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    bootstrap_payload = build_storage_location_bootstrap_payload(config_manager)
    blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
    root_mode = str((config_manager.load_root_state() or {}).get("mode") or "").strip()
    if (
        blocking_reason not in STORAGE_STARTUP_BLOCKING_REASONS
        and root_mode != ROOT_MODE_MAINTENANCE_READONLY
    ):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "storage_exit_not_required",
            "error": "当前没有需要阻断启动的存储状态。",
            "blocking_reason": blocking_reason,
        }

    request_app_shutdown = get_request_app_shutdown()
    if not callable(request_app_shutdown):
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "restart_unavailable",
            "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
        }

    try:
        await _request_app_shutdown(request_app_shutdown)
    except Exception as exc:
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "restart_schedule_failed",
            "error": f"受控关闭启动失败: {exc}",
        }

    return {
        "ok": True,
        "result": "shutdown_initiated",
    }


@router.get("/diagnostics")
async def get_storage_location_diagnostics(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    return _build_storage_location_diagnostics_payload(config_manager)


@router.get("/retained-source")
async def get_storage_location_retained_source(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    notice = _build_completed_migration_notice(
        config_manager,
        require_existing_retained_root=False,
    )
    return {
        "ok": True,
        **notice,
    }


@router.post("/pick-directory")
async def post_storage_location_pick_directory(
    payload: StorageLocationDirectoryPickerRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    try:
        selected_root = await asyncio.to_thread(
            _pick_storage_location_directory,
            start_path=payload.start_path,
        )
    except _DirectoryPickerCancelled:
        return {
            "ok": True,
            "cancelled": True,
            "selected_root": "",
        }
    except _DirectoryPickerUnavailable as exc:
        response.status_code = 503
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    return {
        "ok": True,
        "cancelled": False,
        "selected_root": str(normalize_runtime_root(selected_root)),
    }


@router.post("/open-current")
async def post_storage_location_open_current(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    try:
        await asyncio.to_thread(_open_path_in_file_manager, current_root)
    except _OpenStorageRootUnavailable as exc:
        response.status_code = 503
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
            "current_root": str(current_root),
        }

    return {
        "ok": True,
        "current_root": str(current_root),
    }


@router.post("/retained-source/cleanup")
async def post_storage_location_retained_source_cleanup(
    payload: StorageLocationCleanupRequest,
    response: Response,
):
    async with _storage_mutation_lock:
        return await _post_storage_location_retained_source_cleanup_locked(payload, response)


async def _post_storage_location_retained_source_cleanup_locked(
    payload: StorageLocationCleanupRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_cloudsave_disabled(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    notice = _build_completed_migration_notice(
        config_manager,
        require_existing_retained_root=True,
        persist_reconcile=True,
    )
    if notice.get("completed") is not True:
        # A cleanup that finished but could not record it (a full or
        # read-only disk) left the checkpoint pointing at a root with nothing
        # left to clean; a retained root that is still there is handled by
        # the cleanup below instead.
        reconciled_root = await _run_locked_storage_job(
            partial(
                reconcile_finished_retained_cleanup,
                config_manager,
                anchor_root=compute_anchor_root(
                    config_manager,
                    current_root=normalize_runtime_root(config_manager.app_docs_dir),
                ),
            )
        )
        if reconciled_root:
            return {
                "ok": True,
                "cleaned_root": reconciled_root,
                "retained_root_kept": _entry_may_exist(Path(reconciled_root)),
            }
        response.status_code = 404
        return {
            "ok": False,
            "error_code": "retained_source_not_found",
            "error": "当前没有可清理的旧数据保留目录。",
        }

    expected_retained_root = str(notice.get("retained_root") or "").strip()
    requested_retained_root = str(payload.retained_root or "").strip() or expected_retained_root
    if not paths_equal(requested_retained_root, expected_retained_root):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "retained_source_mismatch",
            "error": "请求的清理路径与当前保留目录不一致，请刷新后重试。",
        }

    retained_path = Path(expected_retained_root)
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)
    cleanup_checkpoint = load_storage_migration(config_manager, anchor_root=anchor_root) or {}

    def _persist_cleanup_result() -> None:
        record_retained_cleanup_completed(
            config_manager,
            anchor_root=anchor_root,
            retained_root=expected_retained_root,
        )

    def _cleanup_and_record() -> tuple[tuple[str, ...], bool]:
        # Deleting and recording the result are one job: _run_locked_storage_job
        # lets the worker finish when the request is cancelled but then raises,
        # so a separate second job would never run and leave the checkpoint
        # pointing at a retained root that is already gone.
        remaining, kept = _cleanup_retained_runtime_root(
            retained_path,
            current_root=current_root,
            anchor_root=anchor_root,
            target_root=notice.get("target_root") or "",
            copied_entries=cleanup_checkpoint.get("copied_entries"),
            legacy_checkpoint=is_legacy_unproven_checkpoint(cleanup_checkpoint),
            # Left behind by the catch-up, or not handled by it yet: reported,
            # never deleted, and the root is not recorded as cleaned.
            catch_up_skipped=[
                *v1_catch_up_skipped_entries(cleanup_checkpoint),
                *v1_catch_up_unfinished_entries(cleanup_checkpoint),
            ],
            # A retained root found gone later counts as cleaned only once a
            # cleanup really got to removing it.
            before_root_removal=partial(record_retained_root_removal_started, config_manager, anchor_root=anchor_root),
        )
        if not remaining:
            _persist_cleanup_result()
        else:
            # Something came back while the root was being removed: it stays,
            # and must not later pass for removed (an unmounted disk).
            clear_retained_root_removal_started(config_manager, anchor_root=anchor_root)
        return remaining, kept

    try:
        # This rmtree retention-directory step likewise must not release _storage_mutation_lock on cancellation.
        remaining_entries, retained_root_kept = await _run_locked_storage_job(_cleanup_and_record)
    except Exception as exc:
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_failed",
            "error": f"清理旧数据保留目录失败: {exc}",
        }

    if remaining_entries:
        # The placeholder is not an entry the user can look for: the old
        # directory could not be listed, so one may still hide under a private
        # cleanup name. It is reported as a flag of its own.
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_incomplete",
            "error": "旧数据目录仍含缺少复制证据的运行时条目，已保留供人工确认。",
            "retained_root": expected_retained_root,
            "remaining_entries": [
                entry_name for entry_name in remaining_entries if entry_name != _UNLISTABLE_RETAINED_ROOT
            ],
            "retained_root_unlistable": _UNLISTABLE_RETAINED_ROOT in remaining_entries,
        }


    return {
        "ok": True,
        "cleaned_root": expected_retained_root,
        # A non-anchor retained root is removed once emptied; other files the
        # user kept in it stay, and the UI should not claim it is gone.
        "retained_root_kept": retained_root_kept,
    }


@router.post("/select")
async def post_storage_location_select(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    async with _storage_mutation_lock:
        return await _post_storage_location_select_locked(payload, response)


async def _post_storage_location_select_locked(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_cloudsave_disabled(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
            selection_source=payload.selection_source,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    blocking_bootstrap = await _run_locked_storage_job(
        partial(
            build_storage_location_bootstrap_payload,
            config_manager,
            persist_reconcile=True,
        )
    )
    selected_root_missing_recovery = _is_selected_root_missing_recovery(
        config_manager,
        current_root=current_root,
        anchor_root=anchor_root,
    )
    committed_selected_root = _load_committed_selected_root(
        config_manager,
        anchor_root=anchor_root,
        fallback_root=current_root,
    )
    if paths_equal(normalized_selected_root, current_root):
        if bool(blocking_bootstrap.get("migration_pending")):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "storage_bootstrap_blocking",
                "error": "当前存储状态仍需恢复或迁移，暂时不能继续当前会话。",
            }
        if bool(blocking_bootstrap.get("recovery_required")):
            if not selected_root_missing_recovery:
                migration_payload = load_storage_migration(
                    config_manager,
                    anchor_root=anchor_root,
                ) or {}
                migration_failed_on_current_root = (
                    str(migration_payload.get("status") or "").strip() == STORAGE_MIGRATION_STATUS_FAILED
                    and paths_equal(migration_payload.get("source_root") or "", current_root)
                )
                if not migration_failed_on_current_root:
                    response.status_code = 409
                    return {
                        "ok": False,
                        "error_code": "storage_bootstrap_blocking",
                        "error": "当前存储状态仍需恢复或迁移，暂时不能继续当前会话。",
                    }

                def _recover_from_failed_migration() -> dict[str, Any]:
                    delete_storage_migration(config_manager, anchor_root=anchor_root)
                    recovered_policy = save_storage_policy(
                        config_manager,
                        selected_root=current_root,
                        selection_source=payload.selection_source,
                        anchor_root=anchor_root,
                    )
                    set_root_mode(
                        config_manager,
                        ROOT_MODE_NORMAL,
                        current_root=str(current_root),
                        last_known_good_root=str(current_root),
                        last_migration_result=f"recovered:failed_migration:{migration_payload.get('error_code') or 'unknown'}",
                    )
                    return recovered_policy

                return await _complete_current_root_selection(
                    config_manager, response=response,
                    anchor_root=anchor_root, current_root=current_root,
                    write=_recover_from_failed_migration,
                )

            def _recover_from_unavailable_selected_root() -> dict[str, Any]:
                recovered_policy = save_storage_policy(
                    config_manager,
                    selected_root=current_root,
                    selection_source=payload.selection_source,
                    anchor_root=anchor_root,
                )
                set_root_mode(
                    config_manager,
                    ROOT_MODE_NORMAL,
                    current_root=str(current_root),
                    last_known_good_root=str(current_root),
                    last_migration_result=f"recovered:selected_root_unavailable:{committed_selected_root}",
                )
                return recovered_policy

            return await _complete_current_root_selection(
                config_manager, response=response,
                anchor_root=anchor_root, current_root=current_root,
                write=_recover_from_unavailable_selected_root,
                include_migration=False,
            )
        def _persist_current_root_selection() -> dict[str, Any]:
            return save_storage_policy(
                config_manager,
                selected_root=current_root,
                selection_source=payload.selection_source,
                anchor_root=anchor_root,
            )

        return await _complete_current_root_selection(
            config_manager, response=response,
            anchor_root=anchor_root, current_root=current_root,
            write=_persist_current_root_selection,
            include_migration=False,
        )

    if bool(blocking_bootstrap.get("recovery_required")) and selected_root_missing_recovery:
        if not paths_equal(normalized_selected_root, committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "recovery_source_unavailable",
                "error": "原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。",
            }
        if not is_runtime_root_available(committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "selected_root_unavailable",
                "error": "原始数据路径当前仍不可用，请先恢复该路径后再重试。",
            }
        restart_preflight = _build_restart_preflight(
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
            estimated_required_bytes=0,
            allow_existing_target_content=True,
        )
        return {
            "ok": True,
            "result": "restart_required",
            "restart_mode": "rebind_only",
            "selected_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
            **restart_preflight,
        }

    restart_preflight = _build_restart_preflight(
        current_root,
        normalized_selected_root,
        config_manager=config_manager,
    )
    return {
        "ok": True,
        "result": "restart_required",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        **restart_preflight,
    }


@router.post("/preflight")
async def post_storage_location_preflight(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_cloudsave_disabled(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    blocking_bootstrap = build_storage_location_bootstrap_payload(config_manager)
    blocking_reason = str(blocking_bootstrap.get("blocking_reason") or "").strip()
    root_state = config_manager.load_root_state()
    root_mode = str(root_state.get("mode") or ROOT_MODE_NORMAL).strip() or ROOT_MODE_NORMAL
    if blocking_reason or root_mode == ROOT_MODE_MAINTENANCE_READONLY:
        response.status_code = 409
        if blocking_reason == "migration_pending" or root_mode == ROOT_MODE_MAINTENANCE_READONLY:
            return {
                "ok": False,
                "error_code": "migration_already_pending",
                "error": "当前存储状态仍需恢复或迁移，暂时不能发起新的存储位置变更。",
                "blocking_reason": blocking_reason or "maintenance_readonly",
            }
        return {
            "ok": False,
            "error_code": "storage_bootstrap_blocking",
            "error": "当前存储状态仍需恢复或迁移，暂时不能发起新的存储位置变更。",
            "blocking_reason": blocking_reason,
        }

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
            selection_source=payload.selection_source,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    if paths_equal(normalized_selected_root, current_root):
        return {
            "ok": True,
            "result": "restart_not_required",
            "selected_root": str(normalized_selected_root),
            "target_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
        }

    restart_preflight = _build_restart_preflight(
        current_root,
        normalized_selected_root,
        config_manager=config_manager,
    )
    return {
        "ok": True,
        "result": "restart_required",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        **restart_preflight,
    }


@router.post("/restart")
async def post_storage_location_restart(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    async with _storage_mutation_lock:
        return await _post_storage_location_restart_locked(payload, response)


async def _post_storage_location_restart_locked(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_cloudsave_disabled(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
            selection_source=payload.selection_source,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    if paths_equal(normalized_selected_root, current_root):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "restart_not_required",
            "error": "目标路径与当前路径一致，不需要关闭当前实例。",
        }

    request_app_shutdown = get_request_app_shutdown()
    if not callable(request_app_shutdown):
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "restart_unavailable",
            "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
        }

    blocking_bootstrap = await _run_locked_storage_job(
        partial(
            build_storage_location_bootstrap_payload,
            config_manager,
            persist_reconcile=True,
        )
    )
    if bool(blocking_bootstrap.get("migration_pending")):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "migration_already_pending",
            "error": "已有存储迁移正在等待执行，请先完成或恢复当前迁移后再发起新的重启迁移。",
        }
    selected_root_missing_recovery = _is_selected_root_missing_recovery(
        config_manager,
        current_root=current_root,
        anchor_root=anchor_root,
    )
    committed_selected_root = _load_committed_selected_root(
        config_manager,
        anchor_root=anchor_root,
        fallback_root=current_root,
    )
    if bool(blocking_bootstrap.get("recovery_required")) and selected_root_missing_recovery:
        if not paths_equal(normalized_selected_root, committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "recovery_source_unavailable",
                "error": "原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。",
            }
        if not is_runtime_root_available(committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "selected_root_unavailable",
                "error": "原始数据路径当前仍不可用，请先恢复该路径后再重试。",
            }

        restart_preflight = _build_restart_preflight(
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
            estimated_required_bytes=0,
            allow_existing_target_content=True,
        )
        if restart_preflight["blocking_error_code"]:
            response.status_code = 409
            return {
                "ok": False,
                "error_code": restart_preflight["blocking_error_code"],
                "error": restart_preflight["blocking_error_message"],
                "restart_mode": "rebind_only",
                **restart_preflight,
            }

        def _rebind_to_selected_root() -> None:
            delete_storage_migration(config_manager, anchor_root=anchor_root)
            save_storage_policy(
                config_manager,
                selected_root=normalized_selected_root,
                selection_source=payload.selection_source,
                anchor_root=anchor_root,
            )
            set_root_mode(
                config_manager,
                ROOT_MODE_MAINTENANCE_READONLY,
                last_migration_source=str(normalized_selected_root),
                last_migration_result=f"restart_rebind:{normalized_selected_root}",
            )

        state_snapshot = {}
        # Shared entry handles all write-phase errors/cancellation; treat closing independently to avoid duplicate rollback.
        _, write_error = await _apply_storage_mutation_writes_or_rollback(
            config_manager,
            anchor_root=anchor_root,
            snapshot_out=state_snapshot,
            write=_rebind_to_selected_root,
        )
        if write_error is not None:
            response.status_code = 500
            return _restart_write_error(
                write_error, restart_mode="rebind_only", preflight=restart_preflight,
            )
        shutdown_error = await _request_shutdown_or_rollback(
            config_manager, request_app_shutdown, snapshot=state_snapshot,
            anchor_root=anchor_root, restart_mode="rebind_only", preflight=restart_preflight,
        )
        if shutdown_error is not None:
            response.status_code = 500
            return shutdown_error
        return {
            "ok": True,
            "result": "restart_initiated",
            "restart_mode": "rebind_only",
            "selected_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
            **restart_preflight,
        }

    restart_preflight = _build_restart_preflight(
        current_root,
        normalized_selected_root,
        config_manager=config_manager,
    )
    if restart_preflight["blocking_error_code"]:
        response.status_code = 409
        return {
            "ok": False,
            "error_code": restart_preflight["blocking_error_code"],
            "error": restart_preflight["blocking_error_message"],
            **restart_preflight,
        }
    if restart_preflight["requires_existing_target_confirmation"] and not payload.confirm_existing_target_content:
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "target_confirmation_required",
            "error": restart_preflight["existing_target_confirmation_message"],
            **restart_preflight,
        }

    # Both rollback pre-images and writes share one job: create_pending_storage_migration creates
    # the checkpoint, then set_root_mode enters maintenance. An intervening await allows cancellation with
    # checkpoint created but root still normal, appearing at startup as an unexplained pending migration.
    rollback_state: dict[str, Any] = {}

    def _schedule_pending_migration() -> dict[str, Any]:
        # The pre-images cannot be captured while cloud_apply_fence exposes a
        # temporary root mode. Keep them in the same transaction as both writes
        # so rollback always restores the state immediately preceding this job.
        pending_payload = create_pending_storage_migration(
            config_manager,
            source_root=current_root,
            target_root=normalized_selected_root,
            selection_source=payload.selection_source,
            anchor_root=anchor_root,
            confirmed_existing_target_content=bool(payload.confirm_existing_target_content),
        )
        set_root_mode(
            config_manager,
            ROOT_MODE_MAINTENANCE_READONLY,
            last_migration_source=str(current_root),
            last_migration_result=f"restart_pending:{normalized_selected_root}",
        )
        return pending_payload

    migration_payload, write_error = await _apply_storage_mutation_writes_or_rollback(
        config_manager, anchor_root=anchor_root, snapshot_out=rollback_state,
        write=_schedule_pending_migration, include_policy=False,
    )
    if write_error is not None:
        response.status_code = 500
        return _restart_write_error(
            write_error, restart_mode="migrate_after_shutdown", preflight=restart_preflight,
        )
    shutdown_error = await _request_shutdown_or_rollback(
        config_manager, request_app_shutdown, snapshot=rollback_state,
        anchor_root=anchor_root, restart_mode="migrate_after_shutdown", preflight=restart_preflight,
    )
    if shutdown_error is not None:
        response.status_code = 500
        return shutdown_error

    return {
        "ok": True,
        "result": "restart_initiated",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        "migration": migration_payload,
        **restart_preflight,
    }
