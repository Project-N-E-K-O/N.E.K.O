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
import sys
import inspect
import subprocess
import tempfile
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
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    classify_entry_no_follow,
    copy_evidence_entries,
    create_pending_storage_migration,
    delete_storage_migration,
    get_storage_migration_path,
    is_legacy_unproven_checkpoint,
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    load_storage_migration,
    rewrite_migrated_config_paths,
    save_storage_migration,
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
# 回滚也必须在 worker 中执行：普通写入失败与写入共用同一事务，写入成功后的
# 屏障/关闭失败恢复经 _run_locked_storage_job 提交，取消时等 worker 终态再传播。


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
        # failures 至少有一个元素；取第一个拼主消息，完整列表留在 self.failures 里供日志使用
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

    # root_state 保留严格加载器的合成恢复状态；读失败统一归为状态不可读。
    root_state, root_state_raw = _read_root_state_snapshot(config_manager)

    # 回滚只需要原始字节，不解析、也不生成第二套恢复依据。
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
            # 构造上不该出现（existed=True 必带 bytes）。宁可抛也不静默跳过：跳过会让
            # 回滚假装成功，把一个半还原的状态留给调用方。
            raise RuntimeError("storage state pre-image exists but carries no bytes")
        # 盘上已经是 pre-image 就跳过写入：说明这个文件从未被改动（写入一步都没成功），
        # 或已被前面的回滚步骤退回。不写就不可能失败，于是「写入第一步就失败」这种情况
        # 能如实判成回滚成功，不会误报「未能恢复原有状态」。比较原始字节，不解析内容。
        try:
            from utils.file_utils import read_bytes_tolerating_replace

            if read_bytes_tolerating_replace(path) == raw:
                logger.info("[storage_location] 状态文件已是快照内容，跳过回滚写入: %s", path)
                return
        except FileNotFoundError:
            # 当前不存在（被删过）→ 必须写回
            pass
        except OSError:
            # 读不出当前内容，无法确认是否已恢复 → 不跳过，交给下面的写入去尝试
            pass
        # 局部导入，和本文件既有的原子写导入保持一致
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
    # 空快照表示未开始写入；非空快照必须携带字节 pre-image，缺字段不能当作文件不存在。
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

    # ---- 第三步：root_state ----
    previous_root_state = snapshot.get("root_state")
    if isinstance(previous_root_state, dict):
        # root_state 不做字节快照（回滚要写回的是「原路径不可用」覆盖后的合成状态），
        # 改用加载值比较：盘上已是快照里的值就跳过写入，理由同
        # _restore_state_file_from_preimage —— 不写就不可能失败。
        # 公共恢复入口已持有 root_state_transaction，覆盖整段读取和恢复写入。
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

    # 三步全部跑完后，只要有任意一步失败就抛聚合异常；全成功则静默返回
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
        # 快照、写入、回滚必须在同一个 root_state_transaction() 内完成。
        # 原来的做法是「快照+写入」在一个事务，回滚在另一个 job 里重新取锁——两个事务
        # 之间的窗口会让第三方（cloudsave fence、跨进程 launcher 等）把 root_state 改
        # 成新值，然后回滚用整份旧快照把人家的改动整份盖掉。把回滚搬进同一个事务后，
        # write() 失败的常见路径从头到尾不释放锁，第三方零机会插入。
        #
        # 另一个原因：不要在 cloud_apply_fence 持有的临时 mode 下拍快照然后在 fence
        # 退出后回放——快照必须和写入在同一事务里。
        with root_state_transaction():
            snapshot_out.clear()
            snapshot_out.update(_snapshot_storage_mutation_state(
                config_manager, anchor_root=anchor_root,
                include_policy=include_policy, include_migration=include_migration,
            ))
            # 快照阶段可能抛 _StorageStateUnreadable：此时 write() 一行都没跑，
            # 外层 except _StorageStateUnreadable 分支负责处理，不进下面的 try。
            try:
                result = write()
            except Exception:
                # write() 抛异常 → 在同一把锁内立即回滚，消除第三方插入窗口
                try:
                    _restore_storage_mutation_state(
                        config_manager,
                        snapshot_out,
                        anchor_root=anchor_root,
                    )
                    snapshot_out["_write_outcome"] = "rolled_back"
                except Exception as rollback_exc:
                    # 回滚自身也失败（best-effort 下至少一步失败）→ 记录结果与异常，
                    # 由外层 except 分支根据 _write_outcome 判定错误码
                    snapshot_out["_write_outcome"] = "rollback_failed"
                    snapshot_out["_rollback_error"] = rollback_exc
                # 重新抛出 write() 的原始异常，让外层 except 分支走错误码判定
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
        # worker 已结束。成功写入但屏障尚未解除时必须回滚；写入失败已在原事务内
        # 尝试回滚，不能再提交第二个任务覆盖其他写者的新状态。
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
        # 状态文件当前读不出原始字节（权限拒绝、被别的进程占用、路径是目录、目录不可访问、I/O 错误）。
        # 只有 FileNotFoundError 才算「不存在」，所以这里无法确认文件到底在不在。
        # 快照阶段就终止了：write() 一行都没跑，盘上内容与请求前完全一致。所以这里既不回滚、
        # 也不能说「已恢复」—— 只需如实告诉用户「读不出来、是否还存在无法确认」。
        # 改动前这种文件会被宽容加载器折叠成 None（当作「不存在」），回滚顺手把它 unlink 掉：
        # 一份只是暂时读不到的状态文件被永久删除。所以它必须有自己的错误码。
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
        # 写入失败（典型场景：本机状态目录不可写，沙箱 / 反勒索防护下的固定症状）。
        # 回滚已经在 _job 内部、同一把 root_state_transaction() 里同步执行完毕（见
        # _apply_storage_mutation_writes），这里不再二次提交回滚 job——二次提交会在两个
        # 事务之间留出窗口，让第三方把 root_state 改成新值后被旧快照整份盖掉。
        #
        # 四种失败结局的盘上状态各不相同，绝不能共用一句「已恢复原有状态」：
        #   1) 快照读到不可读的状态文件 → write() 一行都没跑（见上面那个分支）；
        #   2) 快照没取成              → write() 一行都没跑，盘上就是 pre-image；
        #   3) 回滚成功                → 盘上被改过，又退回了 pre-image；
        #   4) 回滚失败                → 盘上可能停在半截。
        # 前端只按 error_code 取固定文案、不读响应体里的 error，所以四种结局必须各有
        # 一个错误码，否则用户会据此误判能不能直接重试。
        if not snapshot_out:
            # 文件读/格式错误已由上面的具体分支处理；其他快照前故障不归因为写权限。
            logger.exception("storage operation failed before a snapshot was taken")
            return None, {
                "ok": False,
                "error_code": "storage_operation_failed",
                "error": "提交存储位置操作失败，未发生落盘改动，请稍后重试。",
            }
        # 回滚结果由 _job 写入 snapshot_out["_write_outcome"]：
        #   - "rolled_back"   → 三步全部成功
        #   - "rollback_failed" → best-effort 下至少一步失败，异常在 _rollback_error
        outcome = snapshot_out.get("_write_outcome")
        if outcome != "rolled_back":
            # 回滚要往同一个「不可写」的目录里重新落盘，所以它自己也会失败：恢复失败的
            # 迁移分支就是现成的例子——检查点已被 delete_storage_migration 删掉，回滚
            # 要重新写回它，同样会被拒。此时盘上并没有回到 pre-image，绝不能对用户声称
            # 「已恢复原有状态」：用户会据此直接重试，而状态其实停在半截。
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
        # 只有确认回滚完成才可以声称已恢复。
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
        # BaseException 而不是 Exception：客户端断连时这里收到的是 CancelledError，
        # 只接 Exception 就会正好跳过这段回滚——而"写已落盘、屏障没解除"恰恰是最需要
        # 回滚的那个状态。下面无条件 raise，所以 KeyboardInterrupt / SystemExit 的
        # 语义不变。
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
        "selection_source": str(migration_payload.get("selection_source") or "").strip(),
        "source_root": source_root,
        "target_root": target_root,
        "retained_root": retained_root,
        "retained_root_exists": retained_exists,
        "cleanup_available": cleanup_available,
        "completed_at": str(migration_payload.get("completed_at") or "").strip(),
        "message": "存储位置迁移已完成，旧数据目录当前仍保留，需手动清理。",
    }


def _cleanup_retained_runtime_root(
    retained_path: Path,
    *,
    current_root: Path,
    anchor_root: Path,
    target_root: Path | str | None = None,
    copied_entries: dict | None = None,
    legacy_checkpoint: bool = False,
) -> tuple[str, ...]:
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

    proofs = copy_evidence_entries(copied_entries)
    normalized_target = normalize_runtime_root(target_root) if str(target_root or "").strip() else None
    proved_entries = list(proofs.items())
    # A checkpoint from before copy evidence existed (v1) has no record of
    # what was copied, so an entry is removed only when the target holds the
    # same entry with the same content right now. Anything else -- never
    # copied, copied partially, or changed in the target since -- stays for
    # the user to look at.
    legacy_entries = [
        entry_name
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES
        if legacy_checkpoint
        and normalized_target is not None
        and entry_name not in proofs
        and os.path.lexists(retained_path / entry_name)
        and os.path.lexists(normalized_target / entry_name)
    ]
    if normalized_target is None or (not proved_entries and not legacy_checkpoint):
        raise ValueError("迁移检查点没有可验证的复制证据，拒绝清理。")
    # An entry already gone from the retained root -- removed by an earlier
    # partial cleanup, or by the user after one was reported -- needs nothing
    # more. It deletes nothing, so it cannot weaken the checks on the rest; a
    # repeated request then finishes the checkpoint instead of failing on it.
    proved_entries = [
        (entry_name, proof)
        for entry_name, proof in proved_entries
        if os.path.lexists(retained_path / entry_name)
    ]

    # The evidence proves each entry was copied completely. What is deleted is
    # the retained copy, so it must still be exactly what was copied. The
    # target only has to still hold a real entry of the copied kind: running
    # the app on it since is the normal case and does not make the old copy
    # worth keeping, but a link (even a dangling one) or a different kind of
    # entry is no longer that copy.
    for entry_name, proof in proved_entries:
        source_entry = retained_path / entry_name
        target_entry = normalized_target / entry_name
        target_manifest = proof.get("target_manifest")
        expected_kind = target_manifest.get("kind") if isinstance(target_manifest, dict) else None
        if (
            not os.path.lexists(source_entry)
            or classify_entry_no_follow(target_entry) != expected_kind
            or snapshot_runtime_entry(source_entry) != proof.get("source_manifest")
        ):
            raise ValueError(f"保留目录条目证据已变化，拒绝清理: {entry_name}")

    def _legacy_entry_matches(entry_name: str) -> bool:
        retained_entry = retained_path / entry_name
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
            rewrite_migrated_config_paths(
                source_root=retained_path,
                target_root=normalized_target,
                config_root=scratch_root,
            )
            return snapshot_runtime_entry(scratch_root / "config") == target_manifest

    legacy_entries = [
        entry_name for entry_name in legacy_entries if _legacy_entry_matches(entry_name)
    ]
    for entry_name in [name for name, _proof in proved_entries] + legacy_entries:
        entry_path = retained_path / entry_name
        if entry_path.is_dir() and not entry_path.is_symlink():
            shutil.rmtree(entry_path)
        elif entry_path.exists():
            entry_path.unlink()

    if not paths_equal(retained_path, anchor_root):
        # Only an emptied retained root goes; anything left in it stays put.
        with suppress(OSError):
            retained_path.rmdir()
    return tuple(
        entry_name
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES
        if os.path.lexists(retained_path / entry_name)
    )


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
    try:
        # 这一步 rmtree 保留目录，同样不能在取消时把 _storage_mutation_lock 让出去
        remaining_entries = await _run_locked_storage_job(
            lambda: _cleanup_retained_runtime_root(
                retained_path,
                current_root=current_root,
                anchor_root=anchor_root,
                target_root=notice.get("target_root") or "",
                copied_entries=cleanup_checkpoint.get("copied_entries"),
                legacy_checkpoint=is_legacy_unproven_checkpoint(cleanup_checkpoint),
            )
        )
    except Exception as exc:
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_failed",
            "error": f"清理旧数据保留目录失败: {exc}",
        }

    if remaining_entries:
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_incomplete",
            "error": "旧数据目录仍含缺少复制证据的运行时条目，已保留供人工确认。",
            "retained_root": expected_retained_root,
            "remaining_entries": list(remaining_entries),
        }

    def _persist_cleanup_result() -> None:
        # 迁移检查点和 root_state 两次落盘放同一个 job：中间插一个 await 就能造出
        # "检查点已标记 cleaned、root_state 还挂着 legacy_cleanup_pending" 的窗口，
        # 而这个窗口正好会被存储页那条 1200ms 的轮询看到。
        migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root) or {}
        if isinstance(migration_payload, dict):
            updated_payload = dict(migration_payload)
            updated_payload["backup_root"] = ""
            updated_payload["retained_source_root"] = ""
            updated_payload["retained_source_mode"] = "cleaned"
            updated_payload["updated_at"] = _utc_now_iso()
            updated_payload["cleanup_completed_at"] = _utc_now_iso()
            save_storage_migration(config_manager, updated_payload, anchor_root=anchor_root)

        # root_state 这一半保持 best-effort（与改动前一致）：清理已经真的做完了，
        # 标记没落上不该把整个请求判失败。
        try:
            with root_state_transaction():
                root_state = config_manager.load_root_state()
                if isinstance(root_state, dict):
                    updated_root_state = dict(root_state)
                    updated_root_state["legacy_cleanup_pending"] = False
                    if paths_equal(updated_root_state.get("last_migration_backup") or "", expected_retained_root):
                        updated_root_state["last_migration_backup"] = ""
                    config_manager.save_root_state(updated_root_state)
        except Exception:
            # best-effort：清理本身已经做完了，标记没落上不该把整个请求判失败
            pass

    await _run_locked_storage_job(_persist_cleanup_result)

    return {
        "ok": True,
        "cleaned_root": expected_retained_root,
        # A non-anchor retained root is removed once emptied; other files the
        # user kept in it stay, and the UI should not claim it is gone.
        "retained_root_kept": (
            not paths_equal(retained_path, anchor_root) and os.path.lexists(retained_path)
        ),
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
        # 写入阶段的异常/取消由共享入口完整处理；关闭阶段独立处理，避免再次回滚。
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

    # 回滚要用的两份 pre-image 与两次写同在一个 job 里：create_pending_storage_migration
    # 落检查点、set_root_mode 切 maintenance，中间一旦有 await，取消就能停在
    # "检查点已建、root mode 还是 normal" 上——启动时会当成一次凭空出现的待迁移。
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
