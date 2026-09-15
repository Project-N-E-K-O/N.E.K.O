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
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import sys
import inspect
import subprocess
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator

import config as config_module
from config import APP_NAME, AUTOSTART_CSRF_TOKEN
from main_routers.system_router._shared import _validate_local_mutation_request
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
from utils.storage_location_bootstrap import (
    STORAGE_STARTUP_BLOCKING_REASONS,
    STORAGE_STATUS_POLL_INTERVAL_MS,
    build_storage_location_bootstrap_payload,
)
from utils.storage.layout import get_storage_recovery_mode
from utils.storage.entries import (
    RUNTIME_STORAGE_ENTRIES,
    RuntimeStorageEntryBoundaryError,
    checked_runtime_entry_path,
)
from utils.storage.community_private_state import (
    COMMUNITY_PRIVATE_STATE_FILENAMES,
    probe_retained_community_state,
    snapshot_retained_community_state,
)
from utils.storage_migration import (
    MIGRATED_RUNTIME_ENTRY_NAMES,
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
    create_pending_storage_migration,
    delete_storage_migration,
    is_retained_root_cleanup_available,
    is_storage_migration_rollback_required,
    load_storage_migration,
    save_storage_migration,
    storage_migration_retains_recovery_evidence,
)
from utils.storage_policy import (
    StoragePolicyError,
    StorageSelectionValidationError,
    compute_anchor_root,
    get_storage_policy_path,
    is_runtime_root_available,
    load_storage_policy,
    normalize_runtime_root,
    path_chain_has_symlink,
    paths_equal,
    save_storage_policy,
    validate_selected_root,
)
from utils.config_manager import get_config_manager as get_runtime_config_manager
from utils.root_state_lock import root_state_transaction

router = APIRouter(prefix="/api/storage/location", tags=["storage_location"])
logger = logging.getLogger(__name__)
_DIRECTORY_PICKER_TIMEOUT_SECONDS = 120.0
_storage_mutation_lock = asyncio.Lock()
_retained_cleanup_requests_in_flight = 0
_STORAGE_RESTART_OPERATION_TTL_SECONDS = 10 * 60
_storage_restart_operations: dict[str, dict[str, Any]] = {}

# _STORAGE_MUTATION_OFFLOAD_CONTRACT
#
# 这个文件的存储状态写序列**已经挪进工作线程**（#2598 之前写的
# _STORAGE_MUTATION_STAYS_ON_LOOP 说明作废）。#2598 当时列的两条阻塞理由不是被无视
# 了，是被逐条解掉的，改动前请先确认它们仍然成立：
#
# 一、取消原子性 —— 靠"整条序列进同一个 to_thread job"保住。
#     delete_storage_migration → save_storage_policy → set_root_mode 之间依旧一个
#     await 都没有：它们现在同在一个同步闭包里，由 _apply_storage_mutation_writes /
#     _run_locked_storage_job 送进 worker。to_thread 被取消时线程照样跑完，所以序列
#     不会被切成两半。守卫：tests/unit/test_root_state_write_lock.py 的
#     test_storage_write_primitives_never_sit_directly_in_an_async_body。
#
#     另外 _run_locked_storage_job 在取消时会**循环等到 worker 结束**再放
#     CancelledError 出去，否则 _storage_mutation_lock 会在工作线程还在写的时候松开，
#     下一个变更请求就能跟它交错。
#
# 二、root_state 的无锁写者 —— 已经不存在了。
#     build_storage_location_bootstrap_payload 现在默认 persist_reconcile=False，
#     GET /bootstrap、/status、/diagnostics、/retained-source、POST /exit 全是纯读；
#     只有已经拿着 _storage_mutation_lock 的 *_locked 路由才 opt-in 落盘。另外
#     root_state 有了真锁（utils/root_state_lock.py），读—改—写整段进锁、锁内重读。
#
# 仍然刻意留在循环上的只有**回滚**（_restore_storage_mutation_state 及
# /restart 的两处内联回滚）：它们全在 except handler 里，await 会让回滚自己变成取消
# 点，而 CancelledError 是 BaseException，外层 except Exception 接不住。


class StorageLocationSelectionRequest(BaseModel):
    selected_root: str = Field(..., min_length=1, max_length=4096)
    selection_source: str = Field(default="user_selected", min_length=1, max_length=64)
    confirm_existing_target_content: bool = False
    restart_operation_id: str = Field(default="", max_length=64)

    @field_validator("selected_root", "selection_source")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("value cannot be empty")
        return stripped

    @field_validator("restart_operation_id")
    @classmethod
    def _strip_optional_operation_id(cls, value: str) -> str:
        return str(value or "").strip()


class StorageRestartOperationRequest(BaseModel):
    restart_operation_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("restart_operation_id")
    @classmethod
    def _strip_operation_id(cls, value: str) -> str:
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


def _prune_storage_restart_operations() -> None:
    now = time.monotonic()
    for operation_id, operation in list(_storage_restart_operations.items()):
        age = now - float(operation.get("created_at") or now)
        if operation.get("state") == "prepared" and age >= _STORAGE_RESTART_OPERATION_TTL_SECONDS:
            operation["state"] = "expired"
            operation["updated_at"] = now
        if age >= _STORAGE_RESTART_OPERATION_TTL_SECONDS * 2:
            _storage_restart_operations.pop(operation_id, None)


def _prepare_storage_restart_operation(target_root: Path | str) -> str:
    _prune_storage_restart_operations()
    operation_id = uuid.uuid4().hex
    now = time.monotonic()
    _storage_restart_operations[operation_id] = {
        "operation_id": operation_id,
        "state": "prepared",
        "target_root": str(normalize_runtime_root(target_root)),
        "instance_id": str(config_module.INSTANCE_ID),
        "created_at": now,
        "updated_at": now,
        "error_code": "",
    }
    return operation_id


def _public_storage_restart_operation(operation_id: str) -> dict[str, Any]:
    normalized_id = str(operation_id or "").strip()
    if not normalized_id:
        return {}
    _prune_storage_restart_operations()
    operation = _storage_restart_operations.get(normalized_id)
    if not isinstance(operation, dict):
        return {
            "operation_id": normalized_id,
            "state": "not_found",
            "instance_id": str(config_module.INSTANCE_ID),
        }
    return {
        "operation_id": normalized_id,
        "state": str(operation.get("state") or "indeterminate"),
        "target_root": str(operation.get("target_root") or ""),
        "instance_id": str(operation.get("instance_id") or ""),
        "error_code": str(operation.get("error_code") or ""),
    }


def _begin_storage_restart_operation(operation_id: str, target_root: Path | str) -> str:
    normalized_id = str(operation_id or "").strip()
    if not normalized_id:
        return ""
    _prune_storage_restart_operations()
    operation = _storage_restart_operations.get(normalized_id)
    if not isinstance(operation, dict):
        return "restart_operation_not_found"
    if operation.get("state") != "prepared":
        return f"restart_operation_{operation.get('state') or 'invalid'}"
    if not paths_equal(operation.get("target_root") or "", target_root):
        return "restart_operation_target_mismatch"
    operation["state"] = "in_flight"
    operation["updated_at"] = time.monotonic()
    return ""


def _finish_storage_restart_operation(
    operation_id: str,
    state: str,
    *,
    error_code: str = "",
) -> None:
    operation = _storage_restart_operations.get(str(operation_id or "").strip())
    if not isinstance(operation, dict):
        return
    operation["state"] = str(state or "indeterminate")
    operation["error_code"] = str(error_code or "")
    operation["updated_at"] = time.monotonic()


def _reject_storage_mutation_when_startup_unavailable(response: Response) -> dict[str, Any] | None:
    recovery_mode = get_storage_recovery_mode()
    if recovery_mode in {"storage_policy_unavailable", "storage_status_unavailable"}:
        response.status_code = 503
        return {
            "ok": False,
            "error_code": recovery_mode,
            "blocking_reason": recovery_mode,
            "error": "存储启动状态无法可靠确认，当前只能安全退出，不能修改存储位置。",
        }
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


def _get_storage_anchor_root(config_manager, *, current_root: Path) -> Path:
    """Use the runtime's configured fixed anchor when one was exported by its owner."""

    configured_anchor_root = getattr(config_manager, "anchor_root", None)
    if configured_anchor_root:
        return normalize_runtime_root(configured_anchor_root)
    return compute_anchor_root(config_manager, current_root=current_root)


def _snapshot_storage_mutation_state(config_manager, *, anchor_root: Path) -> dict[str, Any]:
    return {
        "root_state": config_manager.load_root_state(),
        "policy": load_storage_policy(config_manager, anchor_root=anchor_root),
        "migration": load_storage_migration(config_manager, anchor_root=anchor_root),
    }


def _restore_storage_mutation_state(
    config_manager,
    snapshot: dict[str, Any],
    *,
    anchor_root: Path,
) -> None:
    """Roll three storage state files back to a snapshot synchronously.

    Keeping all three writes in one synchronous callable makes the sequence
    indivisible. Async callers submit the whole callable through
    ``_run_locked_storage_job``, which waits for the worker to finish before it
    propagates cancellation.
    """
    # ⚠️ 空快照绝不能往下走。下面的分支把"没有 migration / policy 键"读作"这两个文件
    # 本来就不存在"，于是删检查点、unlink 策略文件。而快照一旦真的取到，
    # _snapshot_storage_mutation_state 必定三个键齐全（值可以是 None）——所以
    # 「空 dict」只可能意味着快照压根没取成（例如 load_root_state 撞上 I/O 错误），
    # 这时候没有任何写发生过，回滚只会毁掉本来好好的文件。
    if not snapshot:
        logger.warning("skipping storage mutation rollback: snapshot was never taken")
        return

    previous_migration = snapshot.get("migration")
    if isinstance(previous_migration, dict):
        save_storage_migration(config_manager, previous_migration, anchor_root=anchor_root)
    else:
        delete_storage_migration(config_manager, anchor_root=anchor_root)

    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    previous_policy = snapshot.get("policy")
    if isinstance(previous_policy, dict):
        from utils.file_utils import atomic_write_json

        atomic_write_json(policy_path, previous_policy, ensure_ascii=False, indent=2)
    else:
        try:
            os.unlink(policy_path)
        except FileNotFoundError:
            pass

    previous_root_state = snapshot.get("root_state")
    if isinstance(previous_root_state, dict):
        config_manager.save_root_state(previous_root_state)


def _restore_restart_schedule_state(
    config_manager,
    snapshot: dict[str, Any],
    *,
    anchor_root: Path,
    recovery_migration: dict[str, Any] | None = None,
) -> bool:
    """Restore the restart checkpoint and root state as one worker job."""
    previous_root_state = snapshot.get("root_state")
    previous_migration = snapshot.get("migration")
    try:
        if isinstance(previous_migration, dict):
            save_storage_migration(config_manager, previous_migration, anchor_root=anchor_root)
        else:
            delete_storage_migration(config_manager, anchor_root=anchor_root)
    except Exception:
        # 先前确有 checkpoint 时，save 失败后绝不能退化成 delete；原文件
        # 很可能仍由 atomic write 保留，删除反而把一次回滚失败扩大成数据丢失。
        logger.exception(
            "failed to restore migration checkpoint during restart-schedule rollback"
        )
        # The new pending checkpoint/root maintenance pair is still the safest
        # recoverable truth.  Do not restore the old normal root_state after its
        # checkpoint half failed, or business writes could resume beside a
        # migration intent whose outcome is unknown.
        return False

    try:
        if isinstance(previous_root_state, dict):
            config_manager.save_root_state(previous_root_state)
    except Exception:
        logger.exception(
            "failed to restore root_state during restart-schedule rollback"
        )
        if isinstance(recovery_migration, dict):
            try:
                save_storage_migration(
                    config_manager,
                    recovery_migration,
                    anchor_root=anchor_root,
                )
            except Exception:
                logger.exception(
                    "failed to retain pending checkpoint after root_state rollback failed"
                )
        return False
    return True


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
        # 循环等到 worker 真的结束，而不是"suppress 一次就走"。第二次 cancel（典型
        # 组合：请求先被取消，紧接着服务器关闭又取消一次）会让下面这个 await 再抛一
        # 次；只 suppress 一次的话就会在 worker 还在写的时候把 _storage_mutation_lock
        # 让出去，等于这段防护白做。worker 是一次有界的落盘（最坏再加 155ms 退避），
        # 所以这个循环一定会停。
        while not task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({task})
        # 取回异常再走人：没人 retrieve 的话 asyncio 会在 GC 时打
        # "Task exception was never retrieved"，把一次落盘失败变成一条谁也对不上的
        # 日志。这里只是消费掉它——取消已经发生，原来的 CancelledError 才是要传出去的。
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
    pre-image survives a write that fails halfway through.  A failure inside
    the storage-state write sequence is still locally reversible, so it is
    restored while the same root-state transaction is held.  Shutdown handoff
    failures are handled by the caller after the worker has reached a terminal
    write state.
    """

    def _job() -> Any:
        # The rollback pre-image and the mutation must observe one root-state
        # transaction. In particular, do not snapshot the temporary mode held
        # by cloud_apply_fence and then replay it after that fence has exited.
        with root_state_transaction():
            snapshot_out.clear()
            snapshot_out.update(
                _snapshot_storage_mutation_state(
                    config_manager,
                    anchor_root=anchor_root,
                )
            )
            try:
                return write()
            except BaseException:
                # The write order spans three independent files.  Keep their
                # observable fact atomic even when the second or third write
                # fails; otherwise a deleted checkpoint beside an old root mode
                # can strand the next launch in an unrecoverable mixed state.
                _restore_storage_mutation_state(
                    config_manager,
                    snapshot_out,
                    anchor_root=anchor_root,
                )
                # Empty now means the write never committed and its pre-image
                # has already been restored.  Outer shutdown-scheduling
                # handlers must not replay the same rollback and turn a safe
                # failure into another avoidable write sequence.
                snapshot_out.clear()
                raise

    return await _run_locked_storage_job(_job)


def _resolve_same_root_restart_plan(
    config_manager,
    *,
    anchor_root: Path,
    current_root: Path,
    selection_source: str,
    blocking_bootstrap: dict[str, Any],
) -> dict[str, Any]:
    """Re-evaluate the exact same-root write that a restart may commit."""

    if bool(blocking_bootstrap.get("migration_pending")):
        return {
            "error_code": "storage_bootstrap_blocking",
            "error": "当前存储状态仍需恢复或迁移，暂时不能继续当前会话。",
        }

    raw_blocking_migration = load_storage_migration(
        config_manager,
        anchor_root=anchor_root,
    )
    if storage_migration_retains_recovery_evidence(raw_blocking_migration):
        return {
            "error_code": "storage_recovery_evidence_retained",
            "error": "迁移事务仍保留可能唯一的数据副本，当前不能清除检查点或启动新的迁移。请恢复原数据路径，然后安全退出并重新启动以继续自动恢复。",
            "blocking_reason": "recovery_required",
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

    if bool(blocking_bootstrap.get("runtime_startup_blocked")):
        current_policy = load_storage_policy(
            config_manager,
            anchor_root=anchor_root,
        ) or {}
        return {
            "selection_source": str(
                current_policy.get("selection_source") or "user_selected"
            ),
            "write_selection": lambda: current_policy,
        }

    if bool(blocking_bootstrap.get("recovery_required")):
        if bool(blocking_bootstrap.get("restart_intent_recovery_required")):
            previous_checkpoint = load_storage_migration(
                config_manager,
                anchor_root=anchor_root,
            )
            previous_status = str(
                (previous_checkpoint or {}).get("status") or ""
            ).strip()
            if previous_checkpoint is None or previous_status == STORAGE_MIGRATION_STATUS_COMPLETED:
                def _recover_from_indeterminate_restart() -> dict[str, Any]:
                    recovered_policy = save_storage_policy(
                        config_manager,
                        selected_root=current_root,
                        selection_source=selection_source,
                        anchor_root=anchor_root,
                    )
                    set_root_mode(
                        config_manager,
                        ROOT_MODE_NORMAL,
                        current_root=str(current_root),
                        last_known_good_root=str(current_root),
                        last_migration_result=(
                            "recovered:restart_schedule_indeterminate:"
                            f"{current_root}"
                        ),
                    )
                    return recovered_policy

                return {
                    "selection_source": selection_source,
                    "write_selection": _recover_from_indeterminate_restart,
                }

        if not selected_root_missing_recovery:
            migration_payload = load_storage_migration(
                config_manager,
                anchor_root=anchor_root,
            ) or {}
            migration_failed_on_current_root = (
                str(migration_payload.get("status") or "").strip()
                == STORAGE_MIGRATION_STATUS_FAILED
                and paths_equal(migration_payload.get("source_root") or "", current_root)
            )
            if not migration_failed_on_current_root:
                return {
                    "error_code": "storage_bootstrap_blocking",
                    "error": "当前存储状态仍需恢复或迁移，暂时不能继续当前会话。",
                }

            def _recover_from_failed_migration() -> dict[str, Any]:
                delete_storage_migration(config_manager, anchor_root=anchor_root)
                recovered_policy = save_storage_policy(
                    config_manager,
                    selected_root=current_root,
                    selection_source=selection_source,
                    anchor_root=anchor_root,
                )
                set_root_mode(
                    config_manager,
                    ROOT_MODE_NORMAL,
                    current_root=str(current_root),
                    last_known_good_root=str(current_root),
                    last_migration_result=(
                        "recovered:failed_migration:"
                        f"{migration_payload.get('error_code') or 'unknown'}"
                    ),
                )
                return recovered_policy

            return {
                "selection_source": selection_source,
                "write_selection": _recover_from_failed_migration,
            }

        def _recover_from_unavailable_selected_root() -> dict[str, Any]:
            recovered_policy = save_storage_policy(
                config_manager,
                selected_root=current_root,
                selection_source=selection_source,
                anchor_root=anchor_root,
            )
            set_root_mode(
                config_manager,
                ROOT_MODE_NORMAL,
                current_root=str(current_root),
                last_known_good_root=str(current_root),
                last_migration_result=(
                    "recovered:selected_root_unavailable:"
                    f"{committed_selected_root}"
                ),
            )
            return recovered_policy

        return {
            "selection_source": selection_source,
            "write_selection": _recover_from_unavailable_selected_root,
        }

    def _persist_current_root_selection() -> dict[str, Any]:
        return save_storage_policy(
            config_manager,
            selected_root=current_root,
            selection_source=selection_source,
            anchor_root=anchor_root,
        )

    return {
        "selection_source": selection_source,
        "write_selection": _persist_current_root_selection,
    }


def _build_same_root_restart_offer(current_root: Path) -> dict[str, Any]:
    """Return a write-free rebind preview for the already active root."""

    return {
        "restart_mode": "rebind_only",
        "target_root": str(current_root),
        "estimated_required_bytes": 0,
        "safety_margin_bytes": 0,
        "estimated_required_with_margin_bytes": 0,
        "target_free_bytes": 0,
        "disk_space_available": True,
        "permission_ok": True,
        "warning_codes": [],
        "target_has_existing_content": False,
        "requires_existing_target_confirmation": False,
        "existing_target_confirmation_message": "",
        "blocking_error_code": "",
        "blocking_error_message": "",
    }


async def _schedule_same_root_storage_restart(
    *,
    response: Response,
    config_manager,
    anchor_root: Path,
    current_root: Path,
    selection_source: str,
    write_selection: Callable[[], Any],
) -> dict[str, Any]:
    """Commit a same-root decision and hand phase-0 to a fresh launcher.

    A recovery generation deliberately skipped Cloud Save/config phase-0.  It
    cannot safely release Memory and Agent before that phase has run, because
    both services cache runtime state during initialization.  Persist one
    rebind handoff instead; the replacement launcher applies phase-0 before any
    business service starts, exactly like an ordinary packaged startup.

    The policy/root-state write sequence is rollback-safe until shutdown is
    accepted.  Once accepted, the durable ``restart_rebind`` marker is the
    launcher's handoff authority and must be preserved.
    """
    request_app_shutdown = get_request_app_shutdown()
    if not callable(request_app_shutdown):
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "restart_unavailable",
            "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
        }

    state_snapshot: dict[str, Any] = {}

    def _commit_restart_handoff() -> Any:
        result = write_selection()
        set_root_mode(
            config_manager,
            ROOT_MODE_MAINTENANCE_READONLY,
            current_root=str(current_root),
            last_migration_source=str(current_root),
            last_migration_result=f"restart_rebind:{current_root}",
        )
        return result

    try:
        policy_payload = await _apply_storage_mutation_writes(
            config_manager,
            anchor_root=anchor_root,
            snapshot_out=state_snapshot,
            write=_commit_restart_handoff,
        )
        await _request_app_shutdown(request_app_shutdown)
    except _ShutdownAcceptedCancellation:
        raise
    except asyncio.CancelledError:
        if state_snapshot:
            with suppress(Exception, asyncio.CancelledError):
                await _run_locked_storage_job(
                    partial(
                        _restore_storage_mutation_state,
                        config_manager,
                        state_snapshot,
                        anchor_root=anchor_root,
                    )
                )
        raise
    except Exception as exc:
        rollback_completed = True
        if state_snapshot:
            try:
                await _run_locked_storage_job(
                    partial(
                        _restore_storage_mutation_state,
                        config_manager,
                        state_snapshot,
                        anchor_root=anchor_root,
                    )
                )
            except Exception:
                rollback_completed = False
                logger.exception(
                    "failed to rollback same-root storage restart after shutdown scheduling failed",
                )

        response.status_code = 500
        if not rollback_completed:
            return {
                "ok": False,
                "result": "result_unknown",
                "error_code": "restart_schedule_rollback_failed",
                "error": "受控关闭未完成，且无法确认存储状态已完全恢复。应用将保持阻断；请仅重试安全退出。",
                "migration_phase": "awaiting_shutdown",
                "shutdown_retry_allowed": True,
                "recovery_action": "retry_safe_exit",
            }
        return {
            "ok": False,
            "error_code": "restart_schedule_failed",
            "error": f"受控关闭启动失败: {exc}",
        }

    persisted_source = (
        (policy_payload or {}).get("selection_source")
        if isinstance(policy_payload, dict)
        else ""
    )
    normalized_source = str(persisted_source or selection_source).strip() or selection_source
    return {
        "ok": True,
        "result": "restart_initiated",
        "restart_mode": "rebind_only",
        "selected_root": str(current_root),
        "selection_source": normalized_source,
        "target_root": str(current_root),
        "migration_phase": "awaiting_shutdown",
        "shutdown_retry_allowed": True,
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
    try:
        from utils.cloudsave_runtime import runtime_root_has_user_content

        return bool(runtime_root_has_user_content(target_root, config_manager=config_manager))
    except Exception:
        if not target_root.exists() or not target_root.is_dir():
            return False
        try:
            return any(target_root.iterdir())
        except OSError:
            return False


def _find_existing_ancestor(path: Path) -> Path:
    candidate = path.expanduser()
    while True:
        if candidate.exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            return candidate
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
    if path_chain_has_symlink(target_root):
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
    disk_space_available = True
    try:
        target_free_bytes = int(shutil.disk_usage(str(existing_anchor)).free)
    except OSError:
        disk_space_available = False

    if target_root.exists():
        permission_probe = target_root
    else:
        permission_probe = existing_anchor
    permission_probe_path = permission_probe / f".neko-storage-preflight-{uuid.uuid4().hex}.tmp"
    try:
        permission_probe_path.write_bytes(b"")
        permission_probe_path.unlink()
        permission_ok = True
    except Exception:
        permission_ok = False
        with suppress(OSError):
            permission_probe_path.unlink()

    safety_margin_bytes = (
        max(64 * 1024 * 1024, int(estimated_required_bytes * 0.05))
        if estimated_required_bytes > 0
        else 0
    )
    estimated_required_with_margin_bytes = estimated_required_bytes + safety_margin_bytes
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
    elif not disk_space_available:
        blocking_error_code = "disk_space_unavailable"
        blocking_error_message = "无法确认目标卷剩余空间，已停止迁移以避免不完整复制。"
    elif (
        estimated_required_with_margin_bytes > 0
        and target_free_bytes < estimated_required_with_margin_bytes
    ):
        blocking_error_code = "insufficient_space"
        blocking_error_message = "目标卷剩余空间不足，无法安全执行关闭后的迁移。"

    return {
        "target_root": str(target_root),
        "estimated_required_bytes": estimated_required_bytes,
        "safety_margin_bytes": safety_margin_bytes,
        "estimated_required_with_margin_bytes": estimated_required_with_margin_bytes,
        "target_free_bytes": target_free_bytes,
        "disk_space_available": disk_space_available,
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
    migration_payload = bootstrap_payload.get("migration")

    if is_storage_migration_rollback_required(
        migration_payload if isinstance(migration_payload, dict) else None
    ):
        return last_error_summary or "迁移目标尚未完成安全回滚，当前不能更改存储位置。"

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
            timeout=_DIRECTORY_PICKER_TIMEOUT_SECONDS,
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
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
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
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=_DIRECTORY_PICKER_TIMEOUT_SECONDS,
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
    deadline = time.monotonic() + _DIRECTORY_PICKER_TIMEOUT_SECONDS
    for command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=remaining,
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
    # 项目策略：不带 Tk/Tcl。每个平台只信任其原生桥（osascript / PowerShell /
    # zenity-kdialog-yad），原生桥失败就直接 _DirectoryPickerUnavailable，让前端
    # 提示用户手填路径——而不是落到 tkinter 兜底（Nuitka 不带 tk-inter 时
    # tk.Tk() 抛 SystemExit 拖死后端）。检查由 scripts/check_no_tkinter.py 守门。
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
    if blocking_reason == "storage_policy_unavailable":
        return _build_storage_policy_unavailable_status()
    if blocking_reason == "storage_status_unavailable":
        return _build_storage_status_unavailable_status()
    migration_payload = bootstrap_payload.get("migration") if isinstance(bootstrap_payload.get("migration"), dict) else {}
    completion_notice = _build_completed_migration_notice(config_manager, bootstrap_payload=bootstrap_payload)

    migration_stage = str(migration_payload.get("status") or "").strip()
    migration_phase = str(bootstrap_payload.get("migration_phase") or "").strip()
    shutdown_retry_allowed = bool(bootstrap_payload.get("shutdown_retry_allowed"))
    lifecycle_state = "ready"
    if migration_stage == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED:
        lifecycle_state = "rollback_required"
    elif blocking_reason == "migration_pending":
        lifecycle_state = "maintenance"
    elif blocking_reason in {"recovery_required", "startup_release_failed"}:
        lifecycle_state = "recovery_required"
    elif blocking_reason == "selection_required":
        lifecycle_state = "selection_required"

    return {
        "ok": True,
        "instance_id": str(config_module.INSTANCE_ID),
        "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
        "ready": lifecycle_state == "ready",
        "status": lifecycle_state,
        "lifecycle_state": lifecycle_state,
        "migration_stage": migration_stage,
        "migration_phase": migration_phase,
        "shutdown_retry_allowed": shutdown_retry_allowed,
        "recovery_action": str(bootstrap_payload.get("recovery_action") or ""),
        "maintenance_message": (
            "当前服务尚未完成受控关闭，数据迁移还没有开始。可以安全地重试关闭请求。"
            if migration_phase == "awaiting_shutdown"
            else _build_maintenance_message(bootstrap_payload)
        ),
        "poll_interval_ms": int(bootstrap_payload.get("poll_interval_ms") or STORAGE_STATUS_POLL_INTERVAL_MS),
        "effective_root": str(normalize_runtime_root(config_manager.app_docs_dir)),
        "last_error_summary": str(bootstrap_payload.get("last_error_summary") or "").strip(),
        "blocking_reason": blocking_reason,
        "completion_notice": completion_notice,
        "storage": {
            "selection_required": bool(bootstrap_payload.get("selection_required")),
            "migration_pending": bool(bootstrap_payload.get("migration_pending")),
            "recovery_required": bool(bootstrap_payload.get("recovery_required")),
            "rollback_required": migration_stage == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
            "legacy_cleanup_pending": bool(bootstrap_payload.get("legacy_cleanup_pending")),
            "stage": bootstrap_payload.get("stage") or "",
            "migration_phase": migration_phase,
            "shutdown_retry_allowed": shutdown_retry_allowed,
        },
        "migration": migration_payload,
    }


def _build_storage_policy_unavailable_status() -> dict[str, Any]:
    return {
        "ok": True,
        "instance_id": str(config_module.INSTANCE_ID),
        "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
        "ready": False,
        "status": "storage_policy_unavailable",
        "lifecycle_state": "storage_policy_unavailable",
        "migration_stage": "",
        "migration_phase": "",
        "shutdown_retry_allowed": False,
        "recovery_action": "safe_exit",
        "maintenance_message": "无法安全读取存储位置策略，主功能将继续保持阻断。请安全退出后检查本机存储状态。",
        "poll_interval_ms": STORAGE_STATUS_POLL_INTERVAL_MS,
        "effective_root": "",
        "last_error_summary": "无法安全读取存储位置策略。",
        "blocking_reason": "storage_policy_unavailable",
        "storage_status_unavailable": True,
        "error_code": "storage_policy_unavailable",
        "completion_notice": {"completed": False},
        "storage": {
            "selection_required": False,
            "migration_pending": False,
            "recovery_required": True,
            "rollback_required": False,
            "legacy_cleanup_pending": False,
            "stage": "",
            "status_unavailable": True,
            "error_code": "storage_policy_unavailable",
        },
        "migration": {},
    }


def _build_storage_status_unavailable_status(error_code: str = "storage_status_unavailable") -> dict[str, Any]:
    normalized_error_code = str(error_code or "storage_status_unavailable").strip()
    return {
        "ok": True,
        "instance_id": str(config_module.INSTANCE_ID),
        "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
        "ready": False,
        "status": "storage_status_unavailable",
        "lifecycle_state": "storage_status_unavailable",
        "migration_stage": "",
        "migration_phase": "",
        "shutdown_retry_allowed": False,
        "recovery_action": "safe_exit",
        "maintenance_message": "暂时无法可靠读取存储状态。主功能将继续保持阻断，请安全退出后检查状态文件或存储挂载。",
        "poll_interval_ms": STORAGE_STATUS_POLL_INTERVAL_MS,
        "effective_root": "",
        "last_error_summary": "暂时无法读取存储状态，主界面将继续保持阻断。",
        "blocking_reason": "storage_status_unavailable",
        "storage_status_unavailable": True,
        "error_code": normalized_error_code,
        "completion_notice": {"completed": False},
        "storage": {
            "selection_required": False,
            "migration_pending": False,
            "recovery_required": False,
            "rollback_required": False,
            "legacy_cleanup_pending": False,
            "stage": "",
            "status_unavailable": True,
            "error_code": normalized_error_code,
        },
        "migration": {},
    }


def _reject_storage_mutation_for_unavailable_policy(
    response: Response,
) -> dict[str, Any]:
    response.status_code = 503
    return {
        "ok": False,
        "error_code": "storage_policy_unavailable",
        "error": "无法安全读取现有存储位置策略，已停止修改以保留恢复信息。请安全退出后检查本机存储状态。",
        "blocking_reason": "storage_policy_unavailable",
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

    runtime_entries: dict[str, dict[str, Any]] = {}
    for entry in RUNTIME_STORAGE_ENTRIES:
        write_root = (
            getattr(config_manager, entry.config_attribute)
            if entry.config_attribute and hasattr(config_manager, entry.config_attribute)
            else effective_root / entry.relative_path
        )
        read_roots = live2d_read_roots if entry.key == "live2d" else [write_root]
        notes = (
            ["windows_cfa_fallback_read_enabled"]
            if entry.key == "live2d"
            and bool(getattr(config_manager, "is_windows_cfa_fallback_active", False))
            else []
        )
        runtime_entries[entry.key] = _build_runtime_entry_diagnostic(
            name=entry.key,
            write_root=write_root,
            read_roots=read_roots,
            effective_root=effective_root,
            retained_source_root=retained_source_root,
            notes=notes,
        )

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


def _secure_retained_cleanup_supported() -> bool:
    return bool(os.name != "nt" and shutil.rmtree.avoids_symlink_attacks)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_completed_migration_notice(
    config_manager,
    *,
    bootstrap_payload: dict[str, Any] | None = None,
    require_existing_retained_root: bool = False,
    persist_reconcile: bool = False,
) -> dict[str, Any]:
    # persist_reconcile 只有已经拿着 _storage_mutation_lock 的调用方能传 True，
    # 见 build_storage_location_bootstrap_payload 的说明。
    bootstrap = (
        bootstrap_payload
        if isinstance(bootstrap_payload, dict)
        else build_storage_location_bootstrap_payload(
            config_manager,
            persist_reconcile=persist_reconcile,
        )
    )
    migration_payload = bootstrap.get("migration") if isinstance(bootstrap.get("migration"), dict) else {}
    if str(migration_payload.get("status") or "").strip() != STORAGE_MIGRATION_STATUS_COMPLETED:
        return {
            "completed": False,
        }
    if str(migration_payload.get("retained_source_mode") or "").strip() == "cleaned":
        return {
            "completed": False,
        }

    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = _get_storage_anchor_root(config_manager, current_root=current_root)
    target_root = str(migration_payload.get("target_root") or "").strip()
    source_root = str(migration_payload.get("source_root") or "").strip()
    retained_root = str(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or source_root
        or ""
    ).strip()
    retained_exists = bool(retained_root and Path(retained_root).exists())
    retained_has_runtime_entries = False
    secure_cleanup_supported = _secure_retained_cleanup_supported()
    retained_private_state = probe_retained_community_state(
        retained_root,
        classify_social_lock_process=secure_cleanup_supported,
    )
    retained_mode = str(migration_payload.get("retained_source_mode") or "").strip()
    retained_has_private_state = retained_private_state.has_managed_content
    if retained_mode == "cleanup_in_progress":
        retained_has_private_state = retained_private_state.has_expected_content(
            set(migration_payload.get("cleanup_private_names") or [])
        )
    if retained_exists:
        try:
            retained_has_runtime_entries = any(
                (
                    checked_runtime_entry_path(Path(retained_root), entry).exists()
                    or checked_runtime_entry_path(Path(retained_root), entry).is_symlink()
                )
                for entry in RUNTIME_STORAGE_ENTRIES
            )
        except RuntimeStorageEntryBoundaryError:
            # Unsafe existing content is still retained content; expose the
            # notice but keep cleanup_available false.
            retained_has_runtime_entries = True
    if not retained_exists or not (
        retained_has_runtime_entries
        or retained_has_private_state
    ):
        return {
            "completed": False,
        }
    cleanup_available = secure_cleanup_supported and is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        require_exists=True,
        allow_anchor_root=True,
        anchor_has_managed_private_state=retained_has_private_state,
    ) and not retained_private_state.cleanup_blocked
    if require_existing_retained_root and not cleanup_available:
        return {
            "completed": False,
        }
    return {
        "completed": True,
        "selection_source": str(migration_payload.get("selection_source") or "").strip(),
        "source_root": source_root,
        "target_root": target_root,
        "retained_root": retained_root,
        "retained_root_exists": retained_exists,
        "cleanup_available": cleanup_available,
        "completed_at": str(migration_payload.get("completed_at") or "").strip(),
        "message": "存储位置迁移已完成，旧数据目录当前仍保留，需手动清理。",
    }


def _open_directory_chain_no_follow(path: Path) -> int:
    """Open an absolute POSIX directory one component at a time without links."""
    if not path.is_absolute() or os.name == "nt":
        raise OSError("secure directory handles are unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise OSError("unsafe directory component")
            before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
                raise OSError("directory component is not a real directory")
            child_fd = os.open(component, flags, dir_fd=current_fd)
            after = os.fstat(child_fd)
            if not os.path.samestat(before, after):
                os.close(child_fd)
                raise OSError("directory component changed while opening")
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_retained_root_no_follow(
    retained_path: Path,
    initial_metadata,
) -> tuple[int, int]:
    parent_fd = _open_directory_chain_no_follow(retained_path.parent)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.stat(retained_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not os.path.samestat(initial_metadata, before):
            raise OSError("retained root changed before handle acquisition")
        root_fd = os.open(retained_path.name, flags, dir_fd=parent_fd)
        if not os.path.samestat(before, os.fstat(root_fd)):
            os.close(root_fd)
            raise OSError("retained root changed while opening")
        return parent_fd, root_fd
    except BaseException:
        os.close(parent_fd)
        raise


def _secure_remove_runtime_entry(root_fd: int, entry) -> None:
    """Delete one inventory entry relative to a pinned root directory handle."""
    relative_path = getattr(entry, "relative_path", entry)
    parts = Path(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid runtime entry: {relative_path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    base_fd = os.dup(root_fd)
    opened: list[tuple[int, str, int]] = []
    current_fd = base_fd
    try:
        for component in parts[:-1]:
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
                raise ValueError(f"运行时条目超出安全边界: {relative_path}")
            child_fd = os.open(component, flags, dir_fd=current_fd)
            if not os.path.samestat(before, os.fstat(child_fd)):
                os.close(child_fd)
                raise ValueError(f"runtime entry parent changed: {relative_path}")
            opened.append((current_fd, component, child_fd))
            current_fd = child_fd

        name = parts[-1]
        try:
            metadata = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"运行时条目超出安全边界: {relative_path}")
        if stat.S_ISDIR(metadata.st_mode):
            shutil.rmtree(name, dir_fd=current_fd)
        else:
            os.unlink(name, dir_fd=current_fd)

        for parent_fd, component, child_fd in reversed(opened):
            os.close(child_fd)
            current_fd = parent_fd
            with suppress(OSError):
                os.rmdir(component, dir_fd=parent_fd)
        opened.clear()
        with suppress(OSError):
            os.fsync(root_fd)
    finally:
        for _parent_fd, _component, child_fd in reversed(opened):
            with suppress(OSError):
                os.close(child_fd)
        with suppress(OSError):
            os.close(base_fd)


def _preflight_runtime_entry(root_fd: int, entry) -> None:
    """Reject unsafe retained content before the first cleanup mutation."""
    relative_path = getattr(entry, "relative_path", entry)
    parts = Path(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid runtime entry: {relative_path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)

    def _validate_tree(directory_fd: int, display_path: str) -> None:
        with os.scandir(directory_fd) as children:
            for child in children:
                metadata = child.stat(follow_symlinks=False)
                child_display = f"{display_path}/{child.name}"
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError(f"运行时条目超出安全边界: {child_display}")
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(child.name, flags, dir_fd=directory_fd)
                    try:
                        if not os.path.samestat(metadata, os.fstat(child_fd)):
                            raise ValueError(f"运行时条目在预检时发生变化: {child_display}")
                        _validate_tree(child_fd, child_display)
                    finally:
                        os.close(child_fd)
                elif not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(f"运行时条目包含不支持的文件类型: {child_display}")

    current_fd = os.dup(root_fd)
    try:
        for index, component in enumerate(parts):
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if stat.S_ISLNK(before.st_mode):
                raise ValueError(f"运行时条目超出安全边界: {relative_path}")
            is_leaf = index == len(parts) - 1
            if not stat.S_ISDIR(before.st_mode):
                if not is_leaf or not stat.S_ISREG(before.st_mode):
                    raise ValueError(f"运行时条目包含不支持的文件类型: {relative_path}")
                return
            child_fd = os.open(component, flags, dir_fd=current_fd)
            if not os.path.samestat(before, os.fstat(child_fd)):
                os.close(child_fd)
                raise ValueError(f"运行时条目在预检时发生变化: {relative_path}")
            os.close(current_fd)
            current_fd = child_fd
            if is_leaf:
                _validate_tree(current_fd, str(relative_path))
    finally:
        os.close(current_fd)


def _cleanup_retained_runtime_root(
    retained_path: Path,
    *,
    current_root: Path,
    anchor_root: Path,
    target_root: Path | str | None = None,
    config_manager=None,
    expected_private_snapshot: dict[str, str] | None = None,
    expected_root_identity: dict[str, int] | None = None,
) -> None:
    if os.name == "nt" or not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError("当前平台无法提供句柄锚定的无跟随删除，请手动清理保留目录。")
    try:
        initial_metadata = retained_path.lstat()
    except OSError as exc:
        raise ValueError("无法安全识别保留目录。") from exc
    if stat.S_ISLNK(initial_metadata.st_mode):
        raise ValueError("保留目录是符号链接，拒绝执行清理。")
    if not stat.S_ISDIR(initial_metadata.st_mode):
        raise ValueError("保留目录不是可安全清理的真实目录。")
    if expected_root_identity is not None:
        try:
            expected_device = int(expected_root_identity["device"])
            expected_inode = int(expected_root_identity["inode"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("清理意图缺少有效的保留目录身份。") from exc
        if (
            int(initial_metadata.st_dev) != expected_device
            or int(initial_metadata.st_ino) != expected_inode
        ):
            raise ValueError("保留目录与已持久化的清理意图不一致，已停止。")
    if path_chain_has_symlink(retained_path):
        raise ValueError("保留目录或其父路径包含符号链接，拒绝执行清理。")
    retained_private_state = probe_retained_community_state(retained_path)
    if retained_private_state.cleanup_blocked:
        raise ValueError(
            f"保留目录的社区私有状态无法安全清理: {retained_private_state.state}"
        )
    if not is_retained_root_cleanup_available(
        retained_path,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        require_exists=True,
        allow_anchor_root=True,
        anchor_has_managed_private_state=retained_private_state.has_managed_content,
    ):
        raise ValueError("保留目录当前不满足安全清理条件。")

    try:
        parent_fd, root_fd = _open_retained_root_no_follow(
            retained_path,
            initial_metadata,
        )
    except OSError as exc:
        raise ValueError("保留目录在清理前发生变化，已停止。") from exc
    try:
        from main_routers.card_drop_router import prepare_retained_community_state_cleanup

        for entry in RUNTIME_STORAGE_ENTRIES:
            _preflight_runtime_entry(root_fd, entry)
        prepare_retained_community_state_cleanup(
            retained_path,
            config_manager=config_manager,
            expected_snapshot=expected_private_snapshot,
            retained_dir_fd=root_fd,
        )
        for entry in RUNTIME_STORAGE_ENTRIES:
            _secure_remove_runtime_entry(root_fd, entry)

        if not paths_equal(retained_path, anchor_root):
            try:
                current_metadata = os.stat(
                    retained_path.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError as exc:
                raise ValueError("保留目录在清理期间被移动，已停止。") from exc
            if not os.path.samestat(current_metadata, os.fstat(root_fd)):
                raise ValueError("保留目录在清理期间被替换，已停止。")
            with suppress(OSError):
                os.rmdir(retained_path.name, dir_fd=parent_fd)
    finally:
        os.close(root_fd)
        os.close(parent_fd)


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


@router.get("/bootstrap")
async def get_storage_location_bootstrap(response: Response):
    _set_no_cache_headers(response)

    try:
        config_manager = _get_storage_config_manager()
        payload = await asyncio.to_thread(
            build_storage_location_bootstrap_payload,
            config_manager,
        )
        return {
            **payload,
            "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
        }
    except StoragePolicyError:
        return _build_storage_policy_unavailable_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage location bootstrap unavailable: %s", exc)
        return _build_storage_status_unavailable_status(
            getattr(exc, "error_code", "storage_status_unavailable")
        )


@router.get("/status")
async def get_storage_location_status(response: Response, request: Request):
    _set_no_cache_headers(response)

    try:
        config_manager = _get_storage_config_manager()
        payload = await asyncio.to_thread(_build_status_payload, config_manager)
    except StoragePolicyError:
        payload = _build_storage_policy_unavailable_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage location status unavailable: %s", exc)
        payload = _build_storage_status_unavailable_status(
            getattr(exc, "error_code", "storage_status_unavailable")
        )
    operation_id = str(request.query_params.get("restart_operation_id") or "").strip()
    if operation_id:
        payload["restart_operation"] = _public_storage_restart_operation(operation_id)
    return payload


@router.post("/exit")
async def post_storage_location_exit(request: Request, response: Response):
    _set_no_cache_headers(response)

    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error

    if request.headers.get("X-Neko-Storage-Action") != "exit":
        response.status_code = 403
        return {
            "ok": False,
            "error_code": "storage_exit_forbidden",
            "error": "缺少存储退出确认标记。",
        }

    recovery_mode = get_storage_recovery_mode()
    if recovery_mode in {"storage_policy_unavailable", "storage_status_unavailable"}:
        # The launcher already established the failure for this generation.
        # Safe exit must not re-read the state file that may have caused it.
        blocking_reason = recovery_mode
        root_mode = ""
    else:
        try:
            config_manager = _get_storage_config_manager()
            def _read_exit_storage_state() -> tuple[dict[str, Any], str]:
                bootstrap = build_storage_location_bootstrap_payload(config_manager)
                mode = str((config_manager.load_root_state() or {}).get("mode") or "").strip()
                return bootstrap, mode

            bootstrap_payload, root_mode = await asyncio.to_thread(
                _read_exit_storage_state
            )
            blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
        except Exception as exc:  # noqa: BLE001
            # A corrupt/unreadable policy, migration checkpoint, or root-state
            # file must still allow safe exit without rewriting evidence.
            logger.warning("storage status unavailable while requesting safe exit: %s", exc)
            blocking_reason = "storage_status_unavailable"
            root_mode = ""
    if (
        blocking_reason not in STORAGE_STARTUP_BLOCKING_REASONS
        and blocking_reason not in {
            "storage_policy_unavailable",
            "storage_status_unavailable",
        }
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
    return await asyncio.to_thread(
        _build_storage_location_diagnostics_payload,
        config_manager,
    )


@router.get("/retained-source")
async def get_storage_location_retained_source(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    notice = await asyncio.to_thread(
        _build_completed_migration_notice,
        config_manager,
        require_existing_retained_root=False,
    )
    return {
        "ok": True,
        **notice,
        # The cleanup request deliberately remains in flight until its worker has
        # reached a terminal result, even if the client-side fetch timed out.
        # Expose that operation fact so the UI cannot queue a second destructive
        # request merely because the retained directory is still visible while
        # the first deletion is running.
        "cleanup_in_progress": _retained_cleanup_requests_in_flight > 0,
    }


@router.post("/pick-directory")
async def post_storage_location_pick_directory(
    payload: StorageLocationDirectoryPickerRequest,
    request: Request,
    response: Response,
):
    _set_no_cache_headers(response)

    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error

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
async def post_storage_location_open_current(request: Request, response: Response):
    _set_no_cache_headers(response)

    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error

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
    request: Request,
    response: Response,
):
    global _retained_cleanup_requests_in_flight

    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error
    _retained_cleanup_requests_in_flight += 1
    try:
        async with _storage_mutation_lock:
            return await _post_storage_location_retained_source_cleanup_locked(payload, response)
    finally:
        _retained_cleanup_requests_in_flight -= 1


async def _post_storage_location_retained_source_cleanup_locked(
    payload: StorageLocationCleanupRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_startup_unavailable(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    notice = await _run_locked_storage_job(
        lambda: _build_completed_migration_notice(
            config_manager,
            require_existing_retained_root=True,
            persist_reconcile=True,
        )
    )
    if notice.get("completed") is not True:
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
    anchor_root = _get_storage_anchor_root(config_manager, current_root=current_root)

    def _persist_cleanup_intent() -> tuple[dict[str, str], dict[str, int]]:
        migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root)
        if not isinstance(migration_payload, dict):
            raise OSError("storage migration checkpoint is unavailable")
        mode = str(migration_payload.get("retained_source_mode") or "").strip()
        if mode == "cleanup_in_progress":
            snapshot = migration_payload.get("retained_private_snapshot")
            if not isinstance(snapshot, dict):
                raise OSError("cleanup intent is missing its private-state snapshot")
            root_identity = migration_payload.get("cleanup_root_identity")
            if not isinstance(root_identity, dict):
                raise OSError("cleanup intent is missing its retained-root identity")
            normalized_identity = {
                "device": int(root_identity["device"]),
                "inode": int(root_identity["inode"]),
            }
            normalized_snapshot = {
                str(filename): str(digest)
                for filename, digest in snapshot.items()
                if filename in COMMUNITY_PRIVATE_STATE_FILENAMES
            }
            return normalized_snapshot, normalized_identity

        try:
            initial_metadata = retained_path.lstat()
            parent_fd, root_fd = _open_retained_root_no_follow(
                retained_path,
                initial_metadata,
            )
        except OSError as exc:
            raise OSError("retained root changed before cleanup intent") from exc
        try:
            pinned_metadata = os.fstat(root_fd)
            snapshot = snapshot_retained_community_state(
                retained_path,
                dir_fd=root_fd,
            )
            root_identity = {
                "device": int(pinned_metadata.st_dev),
                "inode": int(pinned_metadata.st_ino),
            }
        finally:
            os.close(root_fd)
            os.close(parent_fd)
        updated_payload = dict(migration_payload)
        updated_payload["retained_source_mode"] = "cleanup_in_progress"
        updated_payload["retained_private_snapshot"] = snapshot
        updated_payload["cleanup_root_identity"] = root_identity
        updated_payload["cleanup_private_names"] = sorted(snapshot)
        updated_payload["cleanup_started_at"] = (
            str(updated_payload.get("cleanup_started_at") or "").strip()
            or _utc_now_iso()
        )
        updated_payload["updated_at"] = _utc_now_iso()
        save_storage_migration(config_manager, updated_payload, anchor_root=anchor_root)
        return snapshot, root_identity

    try:
        # The intent must survive before the first deletion. If final metadata
        # writes later fail and the same path is reused, compatibility reads can
        # distinguish it from the retained snapshot and refuse credential import.
        cleanup_private_snapshot, cleanup_root_identity = await _run_locked_storage_job(
            _persist_cleanup_intent
        )
    except Exception as exc:
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_intent_failed",
            "error": f"无法在清理前持久化安全意图，未删除任何数据: {exc}",
        }
    try:
        # 选择性清理同样不能在取消时把 _storage_mutation_lock 让出去。
        await _run_locked_storage_job(
            lambda: _cleanup_retained_runtime_root(
                retained_path,
                current_root=current_root,
                anchor_root=anchor_root,
                target_root=notice.get("target_root") or "",
                config_manager=config_manager,
                expected_private_snapshot=cleanup_private_snapshot,
                expected_root_identity=cleanup_root_identity,
            )
        )
    except Exception as exc:
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_failed",
            "error": f"清理旧数据保留目录失败: {exc}",
        }

    def _persist_cleanup_result() -> dict[str, bool]:
        # 迁移检查点和 root_state 两次落盘放同一个 job：中间插一个 await 就能造出
        # "检查点已标记 cleaned、root_state 还挂着 legacy_cleanup_pending" 的窗口，
        # 而这个窗口正好会被存储页那条 1200ms 的轮询看到。
        checkpoint_persisted = False
        try:
            migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root) or {}
            if isinstance(migration_payload, dict):
                updated_payload = dict(migration_payload)
                updated_payload["backup_root"] = ""
                updated_payload["retained_source_root"] = ""
                updated_payload["retained_source_mode"] = "cleaned"
                updated_payload.pop("retained_private_snapshot", None)
                updated_payload.pop("cleanup_private_names", None)
                updated_payload.pop("cleanup_root_identity", None)
                updated_payload["updated_at"] = _utc_now_iso()
                updated_payload["cleanup_completed_at"] = _utc_now_iso()
                save_storage_migration(config_manager, updated_payload, anchor_root=anchor_root)
            checkpoint_persisted = True
        except Exception:
            # The filesystem deletion is already committed. Status derives the
            # cleanup fact from the retained inventory, so a metadata write
            # failure must not turn success into a false "delete failed" result.
            logger.exception("failed to persist retained-source cleanup checkpoint")

        # root_state 这一半保持 best-effort（与改动前一致）：清理已经真的做完了，
        # 标记没落上不该把整个请求判失败。
        root_state_persisted = False
        try:
            with root_state_transaction():
                root_state = config_manager.load_root_state()
                if isinstance(root_state, dict):
                    updated_root_state = dict(root_state)
                    updated_root_state["legacy_cleanup_pending"] = False
                    if paths_equal(updated_root_state.get("last_migration_backup") or "", expected_retained_root):
                        updated_root_state["last_migration_backup"] = ""
                    config_manager.save_root_state(updated_root_state)
            root_state_persisted = True
        except Exception:
            # best-effort：清理本身已经做完了，标记没落上不该把整个请求判失败
            pass
        return {
            "checkpoint_persisted": checkpoint_persisted,
            "root_state_persisted": root_state_persisted,
        }

    persistence = await _run_locked_storage_job(_persist_cleanup_result)

    return {
        "ok": True,
        "cleaned_root": expected_retained_root,
        "metadata_persisted": bool(
            persistence.get("checkpoint_persisted")
            and persistence.get("root_state_persisted")
        ),
    }


@router.post("/select")
async def post_storage_location_select(
    payload: StorageLocationSelectionRequest,
    request: Request,
    response: Response,
):
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error
    async with _storage_mutation_lock:
        return await _post_storage_location_select_locked(payload, response)


async def _post_storage_location_select_locked(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_startup_unavailable(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = _get_storage_anchor_root(config_manager, current_root=current_root)

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

    try:
        blocking_bootstrap = await _run_locked_storage_job(
            partial(
                build_storage_location_bootstrap_payload,
                config_manager,
                persist_reconcile=True,
            )
        )
    except StoragePolicyError:
        return _reject_storage_mutation_for_unavailable_policy(response)
    blocking_migration = (
        blocking_bootstrap.get("migration")
        if isinstance(blocking_bootstrap.get("migration"), dict)
        else None
    )
    raw_blocking_migration = await _run_locked_storage_job(
        partial(
            load_storage_migration,
            config_manager,
            anchor_root=anchor_root,
        )
    )
    if is_storage_migration_rollback_required(blocking_migration):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "storage_rollback_required",
            "error": "迁移目标尚未完成安全回滚，当前不能更改存储位置。请先安全退出并重新启动应用以重试恢复。",
            "blocking_reason": "migration_pending",
        }
    if storage_migration_retains_recovery_evidence(raw_blocking_migration):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "storage_recovery_evidence_retained",
            "error": "迁移事务仍保留可能唯一的数据副本，当前不能清除检查点或启动新的迁移。请恢复原数据路径，然后安全退出并重新启动以继续自动恢复。",
            "blocking_reason": "recovery_required",
        }
    if paths_equal(normalized_selected_root, current_root):
        restart_plan = await _run_locked_storage_job(
            partial(
                _resolve_same_root_restart_plan,
                config_manager,
                anchor_root=anchor_root,
                current_root=current_root,
                selection_source=payload.selection_source,
                blocking_bootstrap=blocking_bootstrap,
            )
        )
        if restart_plan.get("error_code"):
            response.status_code = 409
            return {"ok": False, **restart_plan}
        if not callable(get_request_app_shutdown()):
            response.status_code = 503
            return {
                "ok": False,
                "error_code": "restart_unavailable",
                "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
            }
        return {
            "ok": True,
            "result": "restart_required",
            "restart_operation_id": _prepare_storage_restart_operation(current_root),
            "selected_root": str(current_root),
            "selection_source": str(
                restart_plan.get("selection_source") or payload.selection_source
            ),
            **_build_same_root_restart_offer(current_root),
        }

    if bool(blocking_bootstrap.get("migration_pending")):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "migration_already_pending",
            "error": "已有存储迁移正在等待执行，请先完成或恢复当前迁移后再发起新的存储位置变更。",
            "blocking_reason": "migration_pending",
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
        restart_preflight = await _run_locked_storage_job(
            partial(
                _build_restart_preflight,
                current_root,
                normalized_selected_root,
                config_manager=config_manager,
                estimated_required_bytes=0,
                allow_existing_target_content=True,
            )
        )
        return {
            "ok": True,
            "result": "restart_required",
            "restart_operation_id": _prepare_storage_restart_operation(
                normalized_selected_root
            ),
            "restart_mode": "rebind_only",
            "selected_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
            **restart_preflight,
        }

    restart_preflight = await _run_locked_storage_job(
        partial(
            _build_restart_preflight,
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
        )
    )
    return {
        "ok": True,
        "result": "restart_required",
        "restart_operation_id": _prepare_storage_restart_operation(
            normalized_selected_root
        ),
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        **restart_preflight,
    }


@router.post("/preflight")
async def post_storage_location_preflight(
    payload: StorageLocationSelectionRequest,
    request: Request,
    response: Response,
):
    _set_no_cache_headers(response)

    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error

    disabled_response = _reject_storage_mutation_when_startup_unavailable(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = _get_storage_anchor_root(config_manager, current_root=current_root)

    try:
        def _read_preflight_storage_state() -> tuple[dict[str, Any], str]:
            bootstrap = build_storage_location_bootstrap_payload(config_manager)
            state = config_manager.load_root_state()
            mode = str(state.get("mode") or ROOT_MODE_NORMAL).strip() or ROOT_MODE_NORMAL
            return bootstrap, mode

        blocking_bootstrap, root_mode = await asyncio.to_thread(
            _read_preflight_storage_state
        )
    except StoragePolicyError:
        return _reject_storage_mutation_for_unavailable_policy(response)
    blocking_reason = str(blocking_bootstrap.get("blocking_reason") or "").strip()
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
        normalized_selected_root = await asyncio.to_thread(
            validate_selected_root,
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

    restart_preflight = await _run_locked_storage_job(
        partial(
            _build_restart_preflight,
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
        )
    )
    return {
        "ok": True,
        "result": "restart_required",
        "restart_operation_id": _prepare_storage_restart_operation(
            normalized_selected_root
        ),
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        **restart_preflight,
    }


@router.post("/restart")
async def post_storage_location_restart(
    payload: StorageLocationSelectionRequest,
    request: Request,
    response: Response,
):
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error
    operation_id = str(payload.restart_operation_id or "").strip()
    operation_error = _begin_storage_restart_operation(
        operation_id,
        payload.selected_root,
    )
    if operation_error:
        response.status_code = 409
        return {
            "ok": False,
            "error_code": operation_error,
            "error": "本次存储重启预检已失效，请重新预检后再试。",
            "restart_operation": _public_storage_restart_operation(operation_id),
        }

    try:
        async with _storage_mutation_lock:
            result = await _post_storage_location_restart_locked(payload, response)
    except _ShutdownAcceptedCancellation:
        _finish_storage_restart_operation(operation_id, "accepted")
        raise
    except asyncio.CancelledError:
        # The locked route waits for every worker and rollback attempt before
        # propagating cancellation, so it is no longer in flight. Durable
        # status still decides whether the desktop may leave maintenance.
        _finish_storage_restart_operation(operation_id, "cancelled")
        raise
    except Exception:
        _finish_storage_restart_operation(operation_id, "rejected")
        raise

    if isinstance(result, dict) and result.get("ok") is True and result.get("result") == "restart_initiated":
        operation_state = "accepted"
    elif isinstance(result, dict) and result.get("error_code") == "restart_schedule_rollback_failed":
        operation_state = "indeterminate"
    elif isinstance(result, dict) and result.get("error_code") == "target_confirmation_required":
        # Existing target content can appear after /select preflight.  The
        # renderer confirms that exact fresh observation and retries the same
        # operation, so this recoverable answer must not consume its token.
        operation_state = "prepared"
    else:
        operation_state = "rejected"
    _finish_storage_restart_operation(
        operation_id,
        operation_state,
        error_code=str((result or {}).get("error_code") or "") if isinstance(result, dict) else "",
    )
    if isinstance(result, dict) and operation_id:
        result["restart_operation_id"] = operation_id
        result["restart_operation"] = _public_storage_restart_operation(operation_id)
    return result


@router.post("/restart/cancel")
async def post_storage_location_restart_cancel(
    payload: StorageRestartOperationRequest,
    request: Request,
    response: Response,
):
    _set_no_cache_headers(response)
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False},
    )
    if validation_error is not None:
        return validation_error

    operation_id = payload.restart_operation_id
    operation = _storage_restart_operations.get(operation_id)
    if isinstance(operation, dict) and operation.get("state") == "prepared":
        _finish_storage_restart_operation(operation_id, "cancelled")
    return {
        "ok": True,
        "restart_operation": _public_storage_restart_operation(operation_id),
    }


async def _post_storage_location_restart_locked(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    disabled_response = _reject_storage_mutation_when_startup_unavailable(response)
    if disabled_response is not None:
        return disabled_response

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = _get_storage_anchor_root(config_manager, current_root=current_root)

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
        try:
            blocking_bootstrap = await _run_locked_storage_job(
                partial(
                    build_storage_location_bootstrap_payload,
                    config_manager,
                    persist_reconcile=True,
                )
            )
        except StoragePolicyError:
            return _reject_storage_mutation_for_unavailable_policy(response)
        restart_plan = await _run_locked_storage_job(
            partial(
                _resolve_same_root_restart_plan,
                config_manager,
                anchor_root=anchor_root,
                current_root=current_root,
                selection_source=payload.selection_source,
                blocking_bootstrap=blocking_bootstrap,
            )
        )
        if restart_plan.get("error_code"):
            response.status_code = 409
            return {"ok": False, **restart_plan}
        return await _schedule_same_root_storage_restart(
            response=response,
            config_manager=config_manager,
            anchor_root=anchor_root,
            current_root=current_root,
            selection_source=str(
                restart_plan.get("selection_source") or payload.selection_source
            ),
            write_selection=restart_plan["write_selection"],
        )

    request_app_shutdown = get_request_app_shutdown()
    if not callable(request_app_shutdown):
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "restart_unavailable",
            "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
        }

    try:
        blocking_bootstrap = await _run_locked_storage_job(
            partial(
                build_storage_location_bootstrap_payload,
                config_manager,
                persist_reconcile=True,
            )
        )
    except StoragePolicyError:
        return _reject_storage_mutation_for_unavailable_policy(response)
    blocking_migration = (
        blocking_bootstrap.get("migration")
        if isinstance(blocking_bootstrap.get("migration"), dict)
        else None
    )
    raw_blocking_migration = await _run_locked_storage_job(
        partial(
            load_storage_migration,
            config_manager,
            anchor_root=anchor_root,
        )
    )
    if is_storage_migration_rollback_required(blocking_migration):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "storage_rollback_required",
            "error": "迁移目标尚未完成安全回滚，当前不能发起新的迁移。请先安全退出并重新启动应用以重试恢复。",
            "blocking_reason": "migration_pending",
        }
    if storage_migration_retains_recovery_evidence(raw_blocking_migration):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "storage_recovery_evidence_retained",
            "error": "迁移事务仍保留可能唯一的数据副本，当前不能清除检查点或启动新的迁移。请恢复原数据路径，然后安全退出并重新启动以继续自动恢复。",
            "blocking_reason": "recovery_required",
        }
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

        restart_preflight = await _run_locked_storage_job(
            partial(
                _build_restart_preflight,
                current_root,
                normalized_selected_root,
                config_manager=config_manager,
                estimated_required_bytes=0,
                allow_existing_target_content=True,
            )
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
        try:
            await _apply_storage_mutation_writes(
                config_manager,
                anchor_root=anchor_root,
                snapshot_out=state_snapshot,
                write=_rebind_to_selected_root,
            )
            await _request_app_shutdown(request_app_shutdown)
        except _ShutdownAcceptedCancellation:
            # Shutdown 已经被 launcher 接受；保留本次写入，让退出后的接力流程执行。
            raise
        except asyncio.CancelledError:
            # 取消也必须回滚。工作线程已经跑完（_run_locked_storage_job 保证了这点），
            # 也就是说检查点 / 策略 / maintenance_readonly 都已经落盘，而 shutdown
            # 尚未被接受——留着就是把应用钉死在受限态，用户看到一个永远不重启的
            # "正在迁移"。
            # CancelledError 是 BaseException，下面的 except Exception 接不住，所以
            # 必须单列。回滚 worker 即使再收到取消也会先跑到终态。
            if state_snapshot:
                with suppress(Exception, asyncio.CancelledError):
                    await _run_locked_storage_job(
                        partial(
                            _restore_storage_mutation_state,
                            config_manager,
                            state_snapshot,
                            anchor_root=anchor_root,
                        )
                    )
            raise
        except Exception as exc:
            rollback_failed = False
            if state_snapshot:
                try:
                    await _run_locked_storage_job(
                        partial(
                            _restore_storage_mutation_state,
                            config_manager,
                            state_snapshot,
                            anchor_root=anchor_root,
                        )
                    )
                except Exception:
                    rollback_failed = True
                    logger.exception(
                        "failed to rollback storage mutation state after restart scheduling failed",
                    )

            response.status_code = 500
            if rollback_failed:
                # The rebind writes are durable but their rollback is not.  Do
                # not describe this as an ordinary scheduling failure: callers
                # must preserve the restricted state and guide the user through
                # a safe exit/recovery instead of permitting another mutation.
                return {
                    "ok": False,
                    "result": "result_unknown",
                    "error_code": "restart_schedule_rollback_failed",
                    "error": (
                        "受控关闭启动失败，且无法确认存储状态是否已完整回滚。"
                        "应用将保持阻断，请仅重试安全退出。"
                    ),
                    "restart_mode": "rebind_only",
                    "migration_phase": "awaiting_shutdown",
                    "shutdown_retry_allowed": True,
                    "recovery_action": "retry_safe_exit",
                    **restart_preflight,
                }
            return {
                "ok": False,
                "error_code": "restart_schedule_failed",
                "error": f"受控关闭启动失败: {exc}",
                "restart_mode": "rebind_only",
                **restart_preflight,
            }
        return {
            "ok": True,
            "result": "restart_initiated",
            "restart_mode": "rebind_only",
            "selected_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
            **restart_preflight,
        }

    restart_preflight = await _run_locked_storage_job(
        partial(
            _build_restart_preflight,
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
        )
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

    # 回滚要用的两份 pre-image 与两次写同在一个 job 里：create_pending_storage_migration
    # 落检查点、set_root_mode 切 maintenance，中间一旦有 await，取消就能停在
    # "检查点已建、root mode 还是 normal" 上——启动时会当成一次凭空出现的待迁移。
    rollback_state: dict[str, Any] = {}

    def _schedule_pending_migration() -> dict[str, Any]:
        # The pre-images cannot be captured while cloud_apply_fence exposes a
        # temporary root mode. Keep them in the same transaction as both writes
        # so rollback always restores the state immediately preceding this job.
        with root_state_transaction():
            # 两份 pre-image 都读到之后再一起记进 rollback_state，这样
            # "rollback_state 非空" 就等价于 "两份都在手上"。分两次记的话，第二次读
            # 抛异常会留下 migration 键缺失，回滚分支就会把一份本来就存在的检查点删掉。
            previous_root_state = config_manager.load_root_state()
            previous_migration = load_storage_migration(config_manager, anchor_root=anchor_root)
            rollback_state["root_state"] = previous_root_state
            rollback_state["migration"] = previous_migration
            pending_payload = create_pending_storage_migration(
                config_manager,
                source_root=current_root,
                target_root=normalized_selected_root,
                selection_source=payload.selection_source,
                anchor_root=anchor_root,
                confirmed_existing_target_content=bool(payload.confirm_existing_target_content),
            )
            rollback_state["recovery_migration"] = pending_payload
            set_root_mode(
                config_manager,
                ROOT_MODE_MAINTENANCE_READONLY,
                last_migration_source=str(current_root),
                last_migration_result=f"restart_pending:{normalized_selected_root}",
            )
            return pending_payload

    migration_payload = None
    try:
        migration_payload = await _run_locked_storage_job(_schedule_pending_migration)
        await _request_app_shutdown(request_app_shutdown)
    except _ShutdownAcceptedCancellation:
        # Shutdown 已经被 launcher 接受；待迁移检查点正是退出后的接力依据。
        raise
    except asyncio.CancelledError:
        # 同上：写已落盘、shutdown 尚未被接受，不回滚就会留下一个没人执行的
        # 待迁移检查点 + maintenance_readonly。rollback_state 为空 = 什么都还没写。
        if rollback_state:
            with suppress(Exception, asyncio.CancelledError):
                await _run_locked_storage_job(
                    partial(
                        _restore_restart_schedule_state,
                        config_manager,
                        rollback_state,
                        anchor_root=anchor_root,
                        recovery_migration=(
                            migration_payload
                            if isinstance(migration_payload, dict)
                            else rollback_state.get("recovery_migration")
                        ),
                    )
                )
        raise
    except Exception as exc:
        # rollback_state 为空 = 两份 pre-image 还没读到，也就意味着
        # create_pending_storage_migration 一行都还没跑，没有东西需要回滚。这时候
        # 走下面的分支反而会把一份本来就在盘上的检查点删掉。
        rollback_completed = True
        if rollback_state:
            rollback_completed = bool(await _run_locked_storage_job(
                partial(
                    _restore_restart_schedule_state,
                    config_manager,
                    rollback_state,
                    anchor_root=anchor_root,
                    recovery_migration=(
                        migration_payload
                        if isinstance(migration_payload, dict)
                        else rollback_state.get("recovery_migration")
                    ),
                )
            ))
        response.status_code = 500
        if not rollback_completed:
            return {
                "ok": False,
                "result": "result_unknown",
                "error_code": "restart_schedule_rollback_failed",
                "error": "受控关闭未完成，且无法确认存储状态已完全恢复。应用将保持阻断；请仅重试安全退出。",
                "migration_phase": "awaiting_shutdown",
                "shutdown_retry_allowed": True,
                "recovery_action": "retry_safe_exit",
                **restart_preflight,
            }
        return {
            "ok": False,
            "error_code": "restart_schedule_failed",
            "error": f"受控关闭启动失败: {exc}",
            **restart_preflight,
        }

    return {
        "ok": True,
        "result": "restart_initiated",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        "migration": migration_payload,
        **restart_preflight,
    }
