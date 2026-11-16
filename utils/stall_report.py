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

"""Rate-limited stack dumps for waits that look stuck.

A lock wait that crosses its threshold calls ``report_stall``. Each call logs
one WARNING line (at most one per ``_LINE_MIN_INTERVAL_SECONDS``, the rest are
counted and reported with the next line). A full dump of every thread's stack,
plus the event loop's tasks when a loop is given, is attached at most once per
``_DUMP_MIN_INTERVAL_SECONDS`` and at most ``_MAX_DUMPS_PER_PROCESS`` times per
process, each capped at ``_MAX_DUMP_CHARS``. A lock that stays stuck all day
therefore adds well under a megabyte to the rotating service log.

Records go through the service's own log file (``N.E.K.O_<Service>_<date>.log``
under the storage root's ``logs/``), so a user can send that one file.
"""

from __future__ import annotations

import asyncio
import io
import sys
import threading
import time
import traceback

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

_LINE_MIN_INTERVAL_SECONDS = 60.0
_DUMP_MIN_INTERVAL_SECONDS = 600.0
_MAX_DUMPS_PER_PROCESS = 10
_MAX_FRAMES_PER_STACK = 30
_MAX_DUMP_CHARS = 60_000

_guard = threading.Lock()
_last_line_at: float | None = None
_last_dump_at: float | None = None
_dump_count = 0
_suppressed_lines = 0


def _format_threads(holder_thread_id: int | None) -> str:
    frames = sys._current_frames()
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    current = threading.get_ident()
    # The holder first: it is the one thread the reader needs to see.
    order = sorted(frames, key=lambda ident: (ident != holder_thread_id, ident))
    parts = []
    for ident in order:
        tags = []
        if ident == holder_thread_id:
            tags.append("HOLDER")
        if ident == current:
            tags.append("REPORTER")
        tag = f" [{', '.join(tags)}]" if tags else ""
        stack = "".join(traceback.format_stack(frames[ident], limit=_MAX_FRAMES_PER_STACK))
        parts.append(f"--- thread {names.get(ident, '?')} ({ident}){tag}\n{stack}")
    return "".join(parts)


def _await_chain(task: asyncio.Task) -> list[traceback.FrameSummary]:
    """Walk a suspended task's ``cr_await`` chain, outermost frame first.

    ``Task.print_stack`` shows only the outermost coroutine frame; the code
    actually holding or waiting sits several awaits deeper.
    """
    frames = []
    awaitable: object = task.get_coro()
    while len(frames) < _MAX_FRAMES_PER_STACK:
        frame = getattr(awaitable, "cr_frame", None) or getattr(awaitable, "gi_frame", None)
        if frame is None:
            break
        frames.extend(traceback.extract_stack(frame, limit=1))
        inner = getattr(awaitable, "cr_await", None)
        if inner is None:
            inner = getattr(awaitable, "gi_yieldfrom", None)
        if inner is None:
            break
        awaitable = inner
    return frames


def _format_tasks(loop: asyncio.AbstractEventLoop) -> str:
    parts = []
    for task in asyncio.all_tasks(loop):
        frames = _await_chain(task)
        if frames:
            stack = "".join(traceback.format_list(frames))
        else:
            buffer = io.StringIO()
            task.print_stack(limit=_MAX_FRAMES_PER_STACK, file=buffer)
            stack = buffer.getvalue()
        # The repr names the future the innermost frame is parked on (wait_for=...).
        parts.append(f"--- task {task.get_name()}: {task!r}\n{stack}")
    return "".join(parts)


def report_stall(
    message: str,
    *,
    holder_thread_id: int | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Log one stuck wait, attaching a stack dump when the budget allows.

    Pass ``loop`` only from that loop's own thread: listing its tasks from
    another thread is not safe.
    """
    global _last_line_at, _last_dump_at, _dump_count, _suppressed_lines
    now = time.monotonic()
    with _guard:
        if _last_line_at is not None and now - _last_line_at < _LINE_MIN_INTERVAL_SECONDS:
            _suppressed_lines += 1
            return
        _last_line_at = now
        suppressed, _suppressed_lines = _suppressed_lines, 0
        want_dump = _dump_count < _MAX_DUMPS_PER_PROCESS and (
            _last_dump_at is None or now - _last_dump_at >= _DUMP_MIN_INTERVAL_SECONDS
        )
        if want_dump:
            _last_dump_at = now
            _dump_count += 1
            dump_index = _dump_count
    if suppressed:
        message = f"{message} (另有 {suppressed} 次同类等待未单独记录)"
    if not want_dump:
        logger.warning("[StallReport] %s", message)
        return
    try:
        dump = _format_threads(holder_thread_id)
        if loop is not None:
            dump += _format_tasks(loop)
    except Exception as error:  # noqa: BLE001 - diagnostics must never break the waiter
        dump = f"(stack dump failed: {error!r})\n"
    if len(dump) > _MAX_DUMP_CHARS:
        dump = dump[:_MAX_DUMP_CHARS] + f"\n... (truncated, {len(dump)} chars total)\n"
    logger.warning(
        "[StallReport] %s — stack dump %d/%d:\n%s",
        message, dump_index, _MAX_DUMPS_PER_PROCESS, dump,
    )
