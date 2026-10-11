"""Locked read-modify-write for JSON config files.

Several call sites used to do ``load_json_config -> edit -> save_json_config``
without excluding each other.  When two of them interleave, the later writer
saves its stale snapshot and silently drops the other writer's change.  This
module is the single entry point that serializes the whole read/mutate/write
sequence per file.

Locking model (see ``.agent/rules/neko-guide.md``, "single process + zero
event-loop blocking"):

* One ``threading.Lock`` per filename covers both the synchronous startup
  path and the async request path.  The async variant hands the *entire*
  locked section to ``asyncio.to_thread``, so the lock is only ever acquired
  inside a worker thread: it is never held across an ``await`` and never
  blocks the event loop.
* The critical section is pure disk IO plus an in-memory mutator.  Slow work
  (network probes, cross-server HTTP calls) must stay outside: compute the
  result first, then apply it here against the freshly read file.
* A missing file starts from ``{}``.  A file that exists but cannot be read
  or parsed, or whose top level is not an object, raises and is left
  untouched -- it is never replaced by a partial document.

The guarantee is in-process only.  A second process writing the same file
(for example ``app/monitor.py`` run on its own) is not covered.
"""

from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
# 记录当前线程正在改写的文件名：mutator 里再对同一文件发起 update 会在
# 非重入锁上自锁死；而换成 RLock 又会让内层写入被外层的旧快照覆盖（正是本模块
# 要消除的丢失更新），所以直接报错。
_held = threading.local()


def json_config_lock(filename: str) -> threading.Lock:
    """Return the process-wide lock that serializes updates of ``filename``."""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(filename)
        if lock is None:
            lock = _LOCKS[filename] = threading.Lock()
        return lock


def json_values_equal(a: Any, b: Any) -> bool:
    """Compare as serialized JSON, so ``1``/``True``/``1.0`` stay distinct.

    Plain ``==`` treats ``True == 1``; repairing a legacy ``1`` to ``True``
    would then look like a no-op and never reach the disk.
    """
    try:
        return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
            b, sort_keys=True, ensure_ascii=False
        )
    except (TypeError, ValueError):
        return False


def load_json_config_for_update(manager: Any, filename: str) -> dict:
    """Read ``filename`` for a write: only a missing file defaults to ``{}``.

    ``default_value`` is deliberately not passed: the loader would turn a parse
    or permission error into the default, and the caller would then overwrite
    the damaged file with a near-empty document.
    """
    try:
        data = manager.load_json_config(filename)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{filename} top-level value is not a JSON object")
    return data


def load_json_config_snapshot(manager: Any, filename: str) -> dict:
    """``load_json_config_for_update`` taken under the update lock.

    Readers that decide on the snapshot (``/core_api``, the memory toggles)
    use this so an in-process writer's ``os.replace`` can never overlap the
    open: on Windows that overlap can fail the read with a sharing violation,
    which would surface as a misleading "file may be corrupt" refusal.
    """
    held: set[str] = _held.__dict__.setdefault("filenames", set())
    if filename in held:
        raise RuntimeError(f"snapshot read of {filename} from inside its own mutator")
    with json_config_lock(filename):
        return load_json_config_for_update(manager, filename)


def update_json_config(manager: Any, filename: str, mutator: Callable[[dict], T]) -> T:
    """Atomically read ``filename``, let ``mutator`` edit it in place, then save.

    ``mutator`` receives the current document and must be synchronous and
    quick; it must not touch the same file again.  Its return value is
    returned.  The file is written only when the document actually changed.
    A no-op update still goes through the manager's write fence (when it has
    one), so a maintenance-mode request is refused exactly like a real write
    instead of reporting a success that a pending restore would undo.
    If ``mutator`` raises, nothing is written.
    """
    held: set[str] = _held.__dict__.setdefault("filenames", set())
    if filename in held:
        raise RuntimeError(f"nested update of {filename} from inside its own mutator")
    with json_config_lock(filename):
        held.add(filename)
        try:
            data = load_json_config_for_update(manager, filename)
            before = deepcopy(data)
            result = mutator(data)
            if not json_values_equal(data, before):
                manager.save_json_config(filename, data)
            else:
                assert_writable = getattr(manager, "assert_json_config_writable", None)
                if assert_writable is not None:
                    assert_writable(filename)
            return result
        finally:
            held.discard(filename)


async def aupdate_json_config(manager: Any, filename: str, mutator: Callable[[dict], T]) -> T:
    """Async twin of :func:`update_json_config`; the locked section runs in a worker thread.

    Cancelling the caller cannot stop a worker that is already reading or
    writing, so cancellation is held back until that worker finishes.  This
    keeps any request-level lock the caller holds (``async with ...``) until
    the write has really landed or failed.
    """
    worker = asyncio.ensure_future(asyncio.to_thread(update_json_config, manager, filename, mutator))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # 取消只停得掉这层 await，线程里的读改写照样会落盘；等它真正结束再把取消往上抛，
        # 否则调用方的请求级锁会在写盘前放掉，下一个请求就会读到还没落盘前的快照。
        while not worker.done():
            try:
                await asyncio.wait({worker})
            except asyncio.CancelledError:
                continue
        if not worker.cancelled():
            worker.exception()  # 取走结果，免得事件循环报 "exception was never retrieved"
        raise
