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
"""Shared token-tracker persistence primitives."""

import copy
import os
import time
from contextlib import contextmanager
from pathlib import Path
from utils.logger_config import get_module_logger

logger = get_module_logger("utils.token_tracker")

def _deep_copy_day(day: dict) -> dict:
    """Deep-copy one day's statistics."""
    return copy.deepcopy(day)

def _merge_day_stats(target: dict, source: dict):
    """Accumulate source's statistics into target (modifying target in place)."""
    for k in ("total_prompt_tokens", "total_completion_tokens", "total_tokens",
              "cached_tokens", "total_prompt_chars", "call_count", "error_count"):
        target[k] = target.get(k, 0) + source.get(k, 0)

    # by_model
    t_bm = target.setdefault("by_model", {})
    for model, bucket in source.get("by_model", {}).items():
        if model not in t_bm:
            t_bm[model] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                           "cached_tokens": 0, "prompt_chars": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                  "cached_tokens", "prompt_chars", "call_count"):
            t_bm[model][k] = t_bm[model].get(k, 0) + bucket.get(k, 0)

    # by_call_type
    t_bt = target.setdefault("by_call_type", {})
    for ct, bucket in source.get("by_call_type", {}).items():
        if ct not in t_bt:
            t_bt[ct] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                        "cached_tokens": 0, "prompt_chars": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                  "cached_tokens", "prompt_chars", "call_count"):
            t_bt[ct][k] = t_bt[ct].get(k, 0) + bucket.get(k, 0)

@contextmanager
def _file_lock(lock_path: Path, timeout: float = 10.0):
    """File-system based cross-process mutex.

    Atomically creates the lock file with O_CREAT | O_EXCL, ensuring only one process
    holds the lock at a time. PID + timestamp are written into the lock file for
    stale-lock detection after timeouts.
    """
    fd: int | None = None
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            # 写入 PID 便于调试
            os.write(fd, f"{os.getpid()},{time.time()}".encode())
            break
        except (FileExistsError, PermissionError, OSError):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    # Cleanup is best-effort after a failed lock-file write.
                    pass
                fd = None
                try:
                    os.unlink(str(lock_path))
                except OSError:
                    # Another process may already have removed the partial lock.
                    pass
            # 检测过期锁（持有超过 30 秒视为进程崩溃后的残留）
            try:
                lock_age = time.time() - os.path.getmtime(str(lock_path))
                if lock_age > 30:
                    try:
                        os.unlink(str(lock_path))
                    except OSError:
                        # Concurrent stale-lock cleanup may win this race.
                        pass
                    continue
            except OSError:
                # The lock can disappear while its age is being inspected.
                pass

            if time.monotonic() >= deadline:
                logger.warning("Token tracker: file lock timeout, force removing stale lock")
                try:
                    os.unlink(str(lock_path))
                except OSError:
                    time.sleep(0.1)
                raise TimeoutError(f"file lock timeout after {timeout}s: {lock_path}")

            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                # Releasing the lock file below remains the final cleanup path.
                pass
        for _retry in range(3):
            try:
                os.unlink(str(lock_path))
                break
            except OSError:
                if _retry < 2:
                    time.sleep(0.05)
