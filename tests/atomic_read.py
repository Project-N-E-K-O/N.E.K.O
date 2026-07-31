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
"""Read an artifact that a background writer may be replacing right now.

Production writes go through ``utils.file_utils.atomic_write_*``: stage a temp
file, then ``os.replace`` it onto the target. On Windows that replace and a
concurrent ``open()`` of the same target are mutually exclusive -- whoever
loses gets ``PermissionError`` (WinError 5 / 32, surfaced to ``open()`` as
errno 13). The writer side already backs off (see ``_replace_with_busy_retry``
in ``utils/file_utils.py``); a test that reads the artifact while a
``to_thread`` worker is persisting it needs the same courtesy, or it fails on a
millisecond-wide window a few runs in a thousand -- which is what reddened CI
run 30549810820 on an unrelated PR.

``path.exists()`` is NOT a usable gate here: it can report True while the
replace is still in flight, and the read right behind it still loses. The only
reliable signal that the artifact is there is a read that actually succeeded.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

# 与写入侧同一个量级：撞上的是毫秒级窗口，不是长期占用。总预算保持在亚秒级，
# 免得一个真的写不出来的用例要靠超时才失败。
_RETRY_ATTEMPTS = 6
_RETRY_BACKOFF_S = 0.005


def read_text_tolerating_replace(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
    attempts: int = _RETRY_ATTEMPTS,
    backoff: float = _RETRY_BACKOFF_S,
) -> str:
    """Read a file that a background thread may be atomically replacing.

    The file must already exist; a missing file raises straight through. Only
    the transient "the target is busy" refusal is retried, and the final
    attempt re-raises the real error rather than a wrapped one.
    """
    target = Path(path)
    for attempt in range(attempts):
        try:
            return target.read_text(encoding=encoding)
        except PermissionError:
            if attempt == attempts - 1:
                raise
        time.sleep(backoff)
    raise AssertionError("unreachable")


async def read_text_when_readable(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
    timeout: float = 5.0,
    poll: float = 0.01,
) -> str:
    """Await until the artifact exists AND opens, then return its text.

    Subsumes the "poll exists(), then read" idiom: a loaded CI runner needs
    more than a fixed sleep just to hand the write off to its worker thread,
    and existence alone does not mean readable.
    """
    target = Path(path)
    deadline = time.monotonic() + timeout
    last: OSError | None = None
    while True:
        try:
            return target.read_text(encoding=encoding)
        except (FileNotFoundError, PermissionError) as exc:
            last = exc
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"{target} never became readable within {timeout}s (last: {last!r})"
            )
        await asyncio.sleep(poll)


async def read_json_when_readable(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
    timeout: float = 5.0,
    poll: float = 0.01,
) -> Any:
    """``read_text_when_readable`` plus a JSON parse."""
    return json.loads(
        await read_text_when_readable(path, encoding=encoding, timeout=timeout, poll=poll)
    )
