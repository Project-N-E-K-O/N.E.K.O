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

import asyncio
import hashlib
import os
import sys
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import Any

# Late-bound package reference: tests monkeypatch
# ``utils.cloudsave_runtime.set_root_mode`` on the package facade, and
# ``cloud_apply_fence`` must see that patch, so the helper is resolved
# through the facade at call time instead of via this module's globals.
from utils import cloudsave_runtime as _facade
from utils.root_state_lock import root_state_lifecycle_transaction, root_state_transaction

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


_cloud_apply_process_guard = threading.RLock()


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
    # Lock the complete read-modify-write: atomic final saves alone are insufficient; two writers reading the same
    # preimage let the later writer overwrite all of the earlier writer's fields.
    with root_state_transaction():
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


@contextmanager
def cloudsave_writable_transaction(
    config_manager,
    *,
    operation: str = "write",
    target: str = "",
):
    """Keep the cloud-apply fence closed across a final file mutation."""
    if is_cloudsave_disabled_due_to_local_state_unavailable():
        yield
        return
    _ensure_local_state_directory_or_raise(
        config_manager,
        "starting cloudsave_writable_transaction",
    )
    with _cloud_apply_process_guard:
        if not acquire_cloud_apply_lock(config_manager):
            raise MaintenanceModeError(
                get_root_mode(config_manager),
                operation=operation,
                target=target,
            )
        try:
            with root_state_transaction():
                assert_cloudsave_writable(
                    config_manager,
                    operation=operation,
                    target=target,
                )
                yield
        finally:
            release_cloud_apply_lock(config_manager)


def _cloud_apply_mutex_name(config_manager) -> str:
    digest = hashlib.sha1(str(config_manager.app_docs_dir).encode("utf-8")).hexdigest()[:12]
    return rf"Global\NEKO_CLOUD_APPLY_LOCK_{digest}"


def _configure_win32_mutex_apis(kernel32) -> None:
    from ctypes import c_void_p, wintypes

    kernel32.CreateMutexW.argtypes = [
        c_void_p,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def acquire_cloud_apply_lock(config_manager, *, blocking: bool = False) -> bool:
    """Acquire the cross-process cloud apply lock used by maintenance mode."""
    global _cloud_apply_lock_handle, _cloud_apply_lock_file

    config_manager.ensure_local_state_directory()
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            _configure_win32_mutex_apis(kernel32)
            handle = kernel32.CreateMutexW(
                None,
                False,
                _cloud_apply_mutex_name(config_manager),
            )
            if not handle:
                return False
            wait_ms = 0xFFFFFFFF if blocking else 0
            wait_result = kernel32.WaitForSingleObject(handle, wait_ms)
            if wait_result in {0x00000000, 0x00000080}:
                _cloud_apply_lock_handle = handle
                return True
            kernel32.CloseHandle(handle)
            return False
        except Exception:
            return True

    lock_file = None
    try:
        import fcntl

        lock_path = config_manager.local_state_dir / "cloud_apply.lock"
        lock_file = open(lock_path, "w", encoding="utf-8")
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(lock_file.fileno(), flags)
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
            _configure_win32_mutex_apis(kernel32)
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
    # Deliberately retain lock files: another process could flock the old inode between unlock and unlink,
    # then replacing the path with a new inode creates two holders. Retain the file permanently
    # (local_state_dir is excluded from cloud saves, so harmless); each acquire reopens the same path with open("w").


def _process_holds_cloud_apply_lock() -> bool:
    return _cloud_apply_lock_handle is not None or _cloud_apply_lock_file is not None


def _should_preserve_write_blocking_mode(config_manager, root_state: dict[str, Any]) -> bool:
    current_mode = str(root_state.get("mode") or ROOT_MODE_NORMAL)
    if current_mode == ROOT_MODE_DEFERRED_INIT:
        # Recovery states must explicitly enter storage guidance, never silently become normal during startup bootstrap.
        return True

    if current_mode != ROOT_MODE_MAINTENANCE_READONLY:
        return False

    # storage_migration.json pending state is authoritative: keep readonly if migration really runs;
    # otherwise self-heal an orphan state. ``last_migration_result`` (including ``restart_pending:``)
    # describes the previous operation's intent, not hard proof of ongoing activity. The marker
    # changes only after the launcher completes migration; any event preventing that step
    # (bypassing the launcher after fire-and-forget shutdown / force-killing midway / already cleaning up
    # the migration file) leaves a stale marker, formerly pinning the process to readonly forever
    # and silently failing every memory-server disk write.
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
        # Share set_root_mode's lock: this is read-modify-write too, but **the caller read root_state
        # outside the lock**. Cross-process cloud-apply locking cannot block another writer in this process, so
        # reread while locked; writing dict(root_state) directly overwrites mode /
        # current_root / last_known_good_root committed by others meanwhile (Greptile P1).
        #
        # Lock order is always cloud_apply_lock -> root_state_transaction, matching cloud_apply_fence;
        # never reverse it anywhere.
        with root_state_transaction():
            latest_state = config_manager.load_root_state()
            if not isinstance(latest_state, dict):
                latest_state = root_state

            # Reevaluate self-healing from this locked state too: since the caller's outside-lock check, someone may
            # already have recovered it or created a real pending migration.
            latest_mode = str(latest_state.get("mode") or ROOT_MODE_NORMAL)
            if latest_mode not in WRITE_BLOCKING_MODES:
                return latest_state, False
            if _should_preserve_write_blocking_mode(config_manager, latest_state):
                return latest_state, False

            recovered_state = dict(latest_state)
            recovered_state["mode"] = ROOT_MODE_NORMAL
            recovered_state["last_migration_result"] = f"recovered_stale_mode:{latest_mode}"
            config_manager.save_root_state(recovered_state)
            return recovered_state, True
    finally:
        release_cloud_apply_lock(config_manager)


@contextmanager
def _cloud_apply_fence_state(
    config_manager,
    *,
    mode: str = ROOT_MODE_MAINTENANCE_READONLY,
    reason: str = "",
):
    """Switch root state while the caller owns both cloud-apply locks."""
    # Hold the process-wide root_state transaction for the *whole* fence
    # lifetime, not only for its enter/exit writes. Storage-location writes
    # now run in worker threads; without this lifecycle lock they can commit
    # MAINTENANCE_READONLY while a cloud operation is active, only for the
    # fence's stale exit snapshot to restore NORMAL over the pending migration.
    #
    # Lock order stays cloud_apply_lock -> root_state_transaction everywhere.
    # The lifecycle scope also hands its logical ownership to to_thread
    # workers, while unrelated storage writers remain excluded.
    with root_state_lifecycle_transaction():
        # Read the pre-image only after both locks are held. Reading it before
        # acquire_cloud_apply_lock would still allow a storage writer to land
        # between the read and the acquisition and make this snapshot stale.
        previous_state = get_root_state(config_manager)
        previous_mode = str(previous_state.get("mode") or ROOT_MODE_NORMAL)
        _facade.set_root_mode(
            config_manager,
            mode,
            last_migration_result=reason or previous_state.get("last_migration_result", ""),
        )
        try:
            yield get_root_state(config_manager)
        finally:
            _facade.set_root_mode(config_manager, previous_mode)


@contextmanager
def cloud_apply_fence(
    config_manager,
    *,
    mode: str = ROOT_MODE_MAINTENANCE_READONLY,
    reason: str = "",
):
    """Acquire the global cloud apply lock and switch root_state into maintenance."""
    _ensure_local_state_directory_or_raise(config_manager, "entering cloud_apply_fence")

    with _cloud_apply_process_guard:
        if not acquire_cloud_apply_lock(config_manager, blocking=True):
            raise MaintenanceModeError(
                get_root_mode(config_manager),
                operation="acquire_lock",
                target="cloud_apply_lock",
            )
        try:
            with _cloud_apply_fence_state(
                config_manager,
                mode=mode,
                reason=reason,
            ) as state:
                yield state
        finally:
            release_cloud_apply_lock(config_manager)


@asynccontextmanager
async def async_cloud_apply_fence(
    config_manager,
    *,
    mode: str = ROOT_MODE_MAINTENANCE_READONLY,
    reason: str = "",
    poll_interval: float = 0.05,
):
    """Enter the cloud apply fence without blocking the event-loop thread.

    Windows mutex ownership is thread-affine, so acquisition and release stay
    on the event-loop thread. Contention is handled by short non-blocking polls
    instead of an infinite OS wait that would stall every task on that loop.
    """
    _ensure_local_state_directory_or_raise(
        config_manager,
        "entering async_cloud_apply_fence",
    )
    while True:
        with _cloud_apply_process_guard:
            if acquire_cloud_apply_lock(config_manager, blocking=False):
                try:
                    with _cloud_apply_fence_state(
                        config_manager,
                        mode=mode,
                        reason=reason,
                    ) as state:
                        yield state
                finally:
                    release_cloud_apply_lock(config_manager)
                return
        await asyncio.sleep(poll_interval)
