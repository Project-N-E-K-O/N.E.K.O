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

"""Root mode state, the global cloudsave write fence and the cross-process
cloud apply lock.

The two module-level lock globals below are process-wide singletons; this
module is their only home so every consumer shares the same lock state.

Split out of the former monolithic ``utils/cloudsave_runtime.py``.
"""

from __future__ import annotations

import hashlib
import os
import sys
from contextlib import contextmanager
from typing import Any

# Late-bound package reference: tests monkeypatch
# ``utils.cloudsave_runtime.set_root_mode`` on the package facade, and
# ``cloud_apply_fence`` must see that patch, so the helper is resolved
# through the facade at call time instead of via this module's globals.
from utils import cloudsave_runtime as _facade

from ._shared import (
    MaintenanceModeError,
    ROOT_MODE_DEFERRED_INIT,
    ROOT_MODE_MAINTENANCE_READONLY,
    ROOT_MODE_NORMAL,
    WRITE_BLOCKING_MODES,
    _ensure_local_state_directory_or_raise,
    is_cloudsave_disabled_due_to_local_state_unavailable,
    logger,
)


_cloud_apply_lock_handle = None


_cloud_apply_lock_file = None


def get_root_state(config_manager) -> dict[str, Any]:
    return config_manager.load_root_state()


def get_root_mode(config_manager) -> str:
    state = get_root_state(config_manager)
    return str(state.get("mode") or ROOT_MODE_NORMAL)


def should_write_root_mode_normal_after_startup(root_state: dict[str, Any] | None) -> bool:
    """Return True only when startup bootstrap has already settled back to normal mode."""
    state = root_state if isinstance(root_state, dict) else {}
    return str(state.get("mode") or ROOT_MODE_NORMAL) == ROOT_MODE_NORMAL


def set_root_mode(config_manager, mode: str, **updates: Any) -> dict[str, Any]:
    state = get_root_state(config_manager)
    state["mode"] = str(mode or ROOT_MODE_NORMAL)
    for key, value in updates.items():
        if value is not None:
            state[key] = value
    config_manager.save_root_state(state)
    return state


def is_write_fence_active(config_manager) -> bool:
    return get_root_mode(config_manager) in WRITE_BLOCKING_MODES


def assert_cloudsave_writable(config_manager, *, operation: str = "write", target: str = "") -> None:
    if is_cloudsave_disabled_due_to_local_state_unavailable():
        return
    mode = get_root_mode(config_manager)
    if mode in WRITE_BLOCKING_MODES:
        raise MaintenanceModeError(mode, operation=operation, target=target)


def maintenance_error_payload(exc: MaintenanceModeError) -> dict[str, Any]:
    return {
        "success": False,
        "error": exc.code,
        "code": exc.code,
        "mode": exc.mode,
        "operation": exc.operation,
        "target": exc.target,
        "retryable": True,
    }


def _cloud_apply_mutex_name(config_manager) -> str:
    digest = hashlib.sha1(str(config_manager.app_docs_dir).encode("utf-8")).hexdigest()[:12]
    return rf"Global\NEKO_CLOUD_APPLY_LOCK_{digest}"


def acquire_cloud_apply_lock(config_manager) -> bool:
    """Acquire the cross-process cloud apply lock used by maintenance mode."""
    global _cloud_apply_lock_handle, _cloud_apply_lock_file

    config_manager.ensure_local_state_directory()
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            ERROR_ALREADY_EXISTS = 183
            handle = kernel32.CreateMutexW(None, True, _cloud_apply_mutex_name(config_manager))
            last_err = kernel32.GetLastError()
            if handle != 0:
                if last_err != ERROR_ALREADY_EXISTS:
                    _cloud_apply_lock_handle = handle
                    return True
                kernel32.CloseHandle(handle)
                return False
            return False
        except Exception:
            return True

    lock_file = None
    try:
        import fcntl

        lock_path = config_manager.local_state_dir / "cloud_apply.lock"
        lock_file = open(lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file.write(str(os.getpid()))
            lock_file.flush()
        except (OSError, IOError):
            lock_file.close()
            return False
        _cloud_apply_lock_file = lock_file
        return True
    except Exception:
        if lock_file is not None and lock_file is not _cloud_apply_lock_file:
            try:
                lock_file.close()
            except Exception:
                # Best-effort cleanup only; the acquisition fallback below keeps
                # the existing fail-open behavior when cleanup itself fails.
                pass
        return True


def release_cloud_apply_lock(config_manager) -> None:
    global _cloud_apply_lock_handle, _cloud_apply_lock_file

    if sys.platform == "win32":
        if _cloud_apply_lock_handle is None:
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex(_cloud_apply_lock_handle)
            kernel32.CloseHandle(_cloud_apply_lock_handle)
        except Exception:
            pass
        _cloud_apply_lock_handle = None
        return

    if _cloud_apply_lock_file is None:
        return
    try:
        import fcntl

        fcntl.flock(_cloud_apply_lock_file.fileno(), fcntl.LOCK_UN)
        _cloud_apply_lock_file.close()
    except Exception:
        pass
    _cloud_apply_lock_file = None
    try:
        os.unlink(config_manager.local_state_dir / "cloud_apply.lock")
    except Exception:
        pass


def _process_holds_cloud_apply_lock() -> bool:
    return _cloud_apply_lock_handle is not None or _cloud_apply_lock_file is not None


def _should_preserve_write_blocking_mode(config_manager, root_state: dict[str, Any]) -> bool:
    current_mode = str(root_state.get("mode") or ROOT_MODE_NORMAL)
    if current_mode == ROOT_MODE_DEFERRED_INIT:
        # 恢复态必须显式交给存储引导流程处理，不能在启动 bootstrap 里静默放行为 normal。
        return True

    if current_mode != ROOT_MODE_MAINTENANCE_READONLY:
        return False

    # 真相源是 storage_migration.json 的 pending 状态：迁移真在跑就保留 readonly，
    # 否则视为孤儿态自愈。``last_migration_result`` 字段（含 ``restart_pending:``
    # 前缀）只是描述上一次操作意图，不该被当作"还在进行中"的硬证据——marker
    # 在 launcher 接力跑完迁移时才会被覆盖，任何让 launcher 跑不到那一步的事件
    # （shutdown fire-and-forget 后 launcher 被绕过 / 半途强杀 / 迁移文件已被
    # 善后删除）都会让 marker 残留，配合旧逻辑就把进程永久钉在 readonly 上、
    # memory server 所有写盘静默失败。
    try:
        from utils.storage_migration import is_storage_migration_pending, load_storage_migration

        migration_payload = load_storage_migration(config_manager)
    except Exception as exc:
        logger.warning("failed to load storage migration while preserving write-blocking mode: %s", exc)
        return True

    return bool(migration_payload) and is_storage_migration_pending(migration_payload)


def _recover_stale_write_blocking_mode(config_manager, root_state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    current_mode = str(root_state.get("mode") or ROOT_MODE_NORMAL)
    if current_mode not in WRITE_BLOCKING_MODES:
        return root_state, False

    if _should_preserve_write_blocking_mode(config_manager, root_state):
        return root_state, False

    if _process_holds_cloud_apply_lock():
        return root_state, False

    if not acquire_cloud_apply_lock(config_manager):
        return root_state, False

    try:
        recovered_state = dict(root_state)
        recovered_state["mode"] = ROOT_MODE_NORMAL
        recovered_state["last_migration_result"] = f"recovered_stale_mode:{current_mode}"
        config_manager.save_root_state(recovered_state)
        return recovered_state, True
    finally:
        release_cloud_apply_lock(config_manager)


@contextmanager
def cloud_apply_fence(config_manager, *, mode: str = ROOT_MODE_MAINTENANCE_READONLY, reason: str = ""):
    """Acquire the global cloud apply lock and switch root_state into maintenance."""
    _ensure_local_state_directory_or_raise(config_manager, "entering cloud_apply_fence")

    previous_state = get_root_state(config_manager)
    previous_mode = str(previous_state.get("mode") or ROOT_MODE_NORMAL)
    if not acquire_cloud_apply_lock(config_manager):
        raise MaintenanceModeError(
            get_root_mode(config_manager),
            operation="acquire_lock",
            target="cloud_apply_lock",
        )
    try:
        _facade.set_root_mode(
            config_manager,
            mode,
            last_migration_result=reason or previous_state.get("last_migration_result", ""),
        )
        yield get_root_state(config_manager)
    finally:
        try:
            _facade.set_root_mode(config_manager, previous_mode)
        finally:
            release_cloud_apply_lock(config_manager)
