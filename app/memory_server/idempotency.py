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

"""Idempotency-key bookkeeping shared by every keyed memory_server write.

Three per-character files live under ``memory_dir/<character>/``:

* ``idempotency_keys.json`` -- ``{key: {state, written_at, ...}}`` with
  ``state`` one of ``pending`` / ``done`` / ``cancelled``. Records are kept
  forever: the caller retries without a bound, so the proof that a key was
  already written must not expire before the retries do.
* ``idempotency_staging/<sha256(key)[:32]>.json`` -- the "generate first,
  apply later" journal of one keyed request (the raw key contains ``:``,
  which Windows file names reject, so the file name is a digest and the key
  itself is stored inside the document).
* ``scoped_tombstones.json`` -- ``{subject_key: {forgotten_at,
  forget_epoch}}``, the largest forget epoch each subject ever received.

Locking contract (see docs/design/visit-infrastructure.md section 4.6):

* ``idempotency_lock(name)`` is the per-character lock. It only ever wraps
  one read-modify-write of a small JSON file (``update_key`` and the
  tombstone writer) and never an LLM call or a memory-store write, so two
  different keys of one character can never overwrite each other's record.
* ``key_lock(name, key)`` is the per-key lock that a keyed request holds for
  its whole duration, so a retry that arrives while the first attempt is
  still running waits and then sees the final state.
* Order is fixed: key lock first, character lock second (the character lock
  is only taken inside the helpers below). Nothing here takes a key lock
  while holding the character lock, so the two cannot deadlock.

All file I/O runs in worker threads (``scripts/check_async_blocking.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import weakref
from collections.abc import Callable, Iterable
from typing import Any

from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.file_utils import atomic_write_json

from ._shared import logger

IDEMPOTENCY_KEYS_FILENAME = "idempotency_keys.json"
STAGING_DIRNAME = "idempotency_staging"
TOMBSTONES_FILENAME = "scoped_tombstones.json"

KEY_STATE_PENDING = "pending"
KEY_STATE_DONE = "done"
KEY_STATE_CANCELLED = "cancelled"
TERMINAL_KEY_STATES = frozenset({KEY_STATE_DONE, KEY_STATE_CANCELLED})
_KNOWN_KEY_STATES = frozenset({KEY_STATE_PENDING}) | TERMINAL_KEY_STATES

STAGING_STATE_GENERATED = "generated"


class IdempotencyStateError(RuntimeError):
    """A bookkeeping file exists but cannot be read as the expected shape.

    Never degraded to "empty": an unreadable key file read as ``{}`` and then
    written back would erase every ``done`` record of the character, and the
    next retry of an already-applied digest would apply it again.
    """


# ── locks ────────────────────────────────────────────────────────────────
# asyncio.Lock binds to the loop that first contends on it. Registries are
# keyed per running loop so a lock created under one loop (a previous test,
# a restarted server loop) is never awaited from another.
_character_locks: dict[tuple[int, str], asyncio.Lock] = {}
# 键级锁按弱引用登记：持有或等待它的协程都引用着它，空闲的键随即被回收，
# 注册表不会随请求总数无限增长
_key_locks: "weakref.WeakValueDictionary[tuple[int, str, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def _loop_id() -> int:
    return id(asyncio.get_running_loop())


def idempotency_lock(lanlan_name: str) -> asyncio.Lock:
    """Return the per-character read-modify-write lock (same object per name)."""
    registry_key = (_loop_id(), lanlan_name)
    lock = _character_locks.get(registry_key)
    if lock is None:
        lock = asyncio.Lock()
        _character_locks[registry_key] = lock
    return lock


def key_lock(lanlan_name: str, key: str) -> asyncio.Lock:
    """Return the per-key lock (the same object for the same character and key).

    Registered weakly: callers keep the returned lock referenced while they
    hold or wait on it (``async with key_lock(...)`` does), and an idle key's
    lock is dropped.
    """
    registry_key = (_loop_id(), lanlan_name, key)
    lock = _key_locks.get(registry_key)
    if lock is None:
        lock = asyncio.Lock()
        _key_locks[registry_key] = lock
    return lock


def forget_fence(lanlan_name: str, subject_key: str) -> asyncio.Lock:
    """Return the per-subject lock that serializes epoch-tagged forgets of one subject.

    Held from the completed-epoch check through the erase, both cancellation
    passes and the completion marker, so the next forget of the subject only
    checks once the previous one has published (or failed to publish) its
    completed epoch. Taken before any other lock, by forgets only.
    """
    return key_lock(lanlan_name, "forget-fence:" + subject_key)


# ── paths ────────────────────────────────────────────────────────────────

def _config_manager():
    # 晚绑定：测试与 reload 都通过替换 runtime._config_manager 生效。
    from . import runtime

    return runtime._config_manager


def _character_dir(lanlan_name: str) -> str:
    # 只拼路径不建目录：读路径不能把一个已删除角色的目录重新建出来；
    # 写路径由 atomic_write_json 自己建父目录。
    return os.path.join(str(_config_manager().memory_dir), lanlan_name)


def key_digest(key: str) -> str:
    """The 32-hex-char digest used for staging file names and effect keys."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def effect_key_for(key: str, ordinal: int) -> str:
    """Per-effect dedup key: ``sha256(key)[:32] + ':' + ordinal``."""
    return f"{key_digest(key)}:{int(ordinal)}"


def keys_path(lanlan_name: str) -> str:
    return os.path.join(_character_dir(lanlan_name), IDEMPOTENCY_KEYS_FILENAME)


def staging_dir(lanlan_name: str) -> str:
    return os.path.join(_character_dir(lanlan_name), STAGING_DIRNAME)


def staging_path(lanlan_name: str, key: str) -> str:
    return os.path.join(staging_dir(lanlan_name), f"{key_digest(key)}.json")


def tombstones_path(lanlan_name: str) -> str:
    return os.path.join(_character_dir(lanlan_name), TOMBSTONES_FILENAME)


# ── raw file helpers (run in worker threads) ─────────────────────────────

def _read_json_object(path: str) -> dict:
    """Read a dict-rooted JSON file; missing means empty, anything else raises."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise IdempotencyStateError(f"{os.path.basename(path)} unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise IdempotencyStateError(f"{os.path.basename(path)} is not an object")
    return data


def _write_json_object(path: str, data: dict) -> None:
    assert_cloudsave_writable(
        _config_manager(),
        operation="save",
        target=f"memory/{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}",
    )
    atomic_write_json(path, data, ensure_ascii=False, indent=2)


def _remove_file(path: str) -> bool:
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


async def _update_json_object(
    lanlan_name: str,
    path: str,
    mutate: Callable[[dict], bool],
) -> dict:
    """Character-locked read-modify-write of one dict file.

    ``mutate`` edits the freshly read mapping in place and returns whether
    anything changed; nothing is written when it did not.
    """
    async with idempotency_lock(lanlan_name):
        data = await asyncio.to_thread(_read_json_object, path)
        if mutate(data):
            await asyncio.to_thread(_write_json_object, path, data)
        return data


# ── key records ──────────────────────────────────────────────────────────

async def read_key(lanlan_name: str, key: str) -> dict | None:
    """Return the current record of ``key`` (a copy), or ``None``."""
    data = await asyncio.to_thread(_read_json_object, keys_path(lanlan_name))
    if key not in data:
        return None
    record = data[key]
    state = record.get("state") if isinstance(record, dict) else None
    if not isinstance(state, str) or state not in _KNOWN_KEY_STATES:
        # 键在但记录不是对象、或状态缺失 / 不认识：不能当作「没有这个键」或「未完成」——
        # 它可能原本是 done / cancelled，重新生成会把已完成或已清除的产物再写一遍
        raise IdempotencyStateError(f"idempotency record of {key!r} is malformed")
    return dict(record)


async def update_key(
    lanlan_name: str,
    key: str,
    fn: Callable[[dict | None], dict | None],
) -> dict | None:
    """Change exactly one key record under the character lock.

    Reads the LATEST file, hands ``fn`` a copy of this key's record (or
    ``None``), and writes back only that entry: ``fn`` returning ``None``
    leaves the file untouched, returning an equal record skips the write.
    Every other key in the file is preserved byte-for-byte as read inside
    the lock, so concurrent transitions of different keys cannot lose each
    other. Returns the record now stored for ``key``.
    """
    result: dict[str, Any] = {}

    def _mutate(data: dict) -> bool:
        old = data.get(key)
        old_copy = dict(old) if isinstance(old, dict) else None
        new = fn(None if old_copy is None else dict(old_copy))
        if new is None:
            result["record"] = old_copy
            return False
        new = dict(new)
        result["record"] = new
        if new == old_copy:
            return False
        data[key] = new
        return True

    await _update_json_object(lanlan_name, keys_path(lanlan_name), _mutate)
    return result.get("record")


def transition(state: str, **extra: Any) -> Callable[[dict | None], dict | None]:
    """An ``update_key`` callback moving a record to ``state``.

    Terminal records are never moved again (``done`` stays ``done`` even if a
    late forget tries to cancel it, and a cancelled key is never revived).
    """
    def _fn(old: dict | None) -> dict | None:
        if old is not None and old.get("state") in TERMINAL_KEY_STATES:
            return None
        record = dict(old or {})
        record["state"] = state
        record.setdefault("written_at", time.time())
        record["updated_at"] = time.time()
        for name, value in extra.items():
            if value is not None:
                record[name] = value
        return record

    return _fn


# ── staging ──────────────────────────────────────────────────────────────

def _read_staging_sync(path: str, key: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise IdempotencyStateError(f"staging unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("key") != key:
        # 文件名只是摘要：内容里的原键对不上（截断碰撞 / 手改）时绝不套用
        # 别的键的产物。
        raise IdempotencyStateError("staging document does not belong to this key")
    return data


async def read_staging(lanlan_name: str, key: str) -> dict | None:
    """Return the staging document of ``key`` or ``None`` when there is none."""
    return await asyncio.to_thread(
        _read_staging_sync, staging_path(lanlan_name, key), key,
    )


async def write_staging(lanlan_name: str, key: str, document: dict) -> None:
    """Atomically (re)write the staging document of ``key``.

    Callers hold ``key_lock(lanlan_name, key)``: one writer per key.
    """
    doc = dict(document)
    doc["key"] = key
    await asyncio.to_thread(
        _write_json_object, staging_path(lanlan_name, key), doc,
    )


def is_staging_path_of(lanlan_name: str, key: str, path: str) -> bool:
    """Whether ``path`` (from :func:`list_staging`) is the staging file of ``key``."""
    return (
        os.path.normcase(os.path.abspath(staging_path(lanlan_name, key)))
        == os.path.normcase(os.path.abspath(path))
    )


def _read_staging_file(path: str) -> tuple[bool, Any]:
    """``(missing, document_or_None)`` of one staging file read by path."""
    try:
        with open(path, encoding="utf-8") as handle:
            return False, json.load(handle)
    except FileNotFoundError:
        return True, None
    except (OSError, ValueError, RecursionError):
        return False, None


async def scrub_misplaced_staging(
    lanlan_name: str, path: str, scrub: Callable[[dict], dict],
) -> bool:
    """Rewrite one staging file whose embedded key does not match its name, in place.

    The file's real owner is looked up by name in the key records (best
    effort) and its key lock held, so an in-flight apply of that key never
    races the rewrite. Returns False when the file changed or vanished meanwhile.
    """
    try:
        records = await asyncio.to_thread(_read_json_object, keys_path(lanlan_name))
    except IdempotencyStateError:
        records = {}
    owner = next(
        (key for key in records if isinstance(key, str) and key and is_staging_path_of(lanlan_name, key, path)),
        None,
    )
    async with (key_lock(lanlan_name, owner) if owner else contextlib.nullcontext()):
        missing, current = await asyncio.to_thread(_read_staging_file, path)
        if missing:
            return False
        if not isinstance(current, dict) or (
            isinstance(current.get("key"), str) and is_staging_path_of(lanlan_name, current["key"], path)
        ):
            # 已被它真正的键重写成正常暂存（或已读不出）：交给常规流程
            return False
        await asyncio.to_thread(_write_json_object, path, scrub(current))
    return True


async def delete_staging(lanlan_name: str, key: str) -> bool:
    return await asyncio.to_thread(_remove_file, staging_path(lanlan_name, key))


def _list_staging_sync(directory: str) -> list[tuple[str, dict | None, float]]:
    rows: list[tuple[str, dict | None, float]] = []
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return rows
    for entry in entries:
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        try:
            with open(entry.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = None
        rows.append((entry.path, data if isinstance(data, dict) else None, mtime))
    return rows


async def list_staging(lanlan_name: str) -> list[tuple[str, dict | None, float]]:
    """``[(path, document_or_None, mtime)]`` for every staging file."""
    return await asyncio.to_thread(_list_staging_sync, staging_dir(lanlan_name))


# ── tombstones ───────────────────────────────────────────────────────────

async def read_tombstones(lanlan_name: str) -> dict:
    return await asyncio.to_thread(
        _read_json_object, tombstones_path(lanlan_name),
    )


async def record_tombstones(
    lanlan_name: str,
    subject_keys: Iterable[str],
    forget_epoch: int,
    *,
    now: float | None = None,
) -> dict:
    """Raise each subject's tombstone to at least ``forget_epoch``.

    Monotonic: a smaller epoch never lowers an existing tombstone (a late or
    replayed forget must not reopen a window a newer forget closed).
    """
    stamp = time.time() if now is None else float(now)
    keys = sorted({str(key) for key in subject_keys if key})
    epoch = int(forget_epoch)

    def _mutate(data: dict) -> bool:
        changed = False
        for subject_key in keys:
            current = data.get(subject_key)
            current_epoch = (
                current.get("forget_epoch")
                if isinstance(current, dict) else None
            )
            if subject_key in data and (
                not isinstance(current_epoch, int)
                or isinstance(current_epoch, bool)
                or current_epoch < 0
            ):
                # 墓碑在但内容坏了：原值可能是更高的围栏，不能用这次较低的代数覆盖掉；
                # 也不能因此挡住这次清除（擦除照常进行）。原样留着它：读路径对它
                # fail closed，这个 subject 的带键写入在修好之前一律 503
                logger.warning(f"[Idempotency] {lanlan_name}: 墓碑 {subject_key!r} 内容损坏，保留不覆盖")
                continue
            if (
                isinstance(current_epoch, int)
                and not isinstance(current_epoch, bool)
                and current_epoch >= epoch
            ):
                continue
            data[subject_key] = {"forgotten_at": stamp, "forget_epoch": epoch}
            changed = True
        return changed

    return await _update_json_object(
        lanlan_name, tombstones_path(lanlan_name), _mutate,
    )


async def mark_tombstone_erased(lanlan_name: str, subject_key: str, forget_epoch: int) -> None:
    """Record that the erase of ``forget_epoch`` for ``subject_key`` completed (monotonic).

    The tombstone is written BEFORE the erase, so it alone does not prove the
    erase finished; this marker does, and lets a replayed or stale forget of
    an epoch at or below it skip re-erasing writes made after it.
    """
    epoch = int(forget_epoch)

    def _mutate(data: dict) -> bool:
        row = data.get(subject_key)
        fence = row.get("forget_epoch") if isinstance(row, dict) else None
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
            # 坏墓碑原样留着（读路径对它 fail closed），不往上记完成标记
            return False
        current = row.get("erased_epoch")
        if isinstance(current, int) and not isinstance(current, bool) and current >= epoch:
            return False
        row["erased_epoch"] = epoch
        return True

    await _update_json_object(lanlan_name, tombstones_path(lanlan_name), _mutate)


def erased_epoch(tombstones: dict, subject_key: str) -> int | None:
    """Highest forget epoch whose erase completed for ``subject_key`` (None when unknown)."""
    row = tombstones.get(subject_key)
    value = row.get("erased_epoch") if isinstance(row, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def tombstone_epoch(tombstones: dict, subject_keys: Iterable[str]) -> int | None:
    """Largest forget epoch recorded for any of ``subject_keys`` (or None)."""
    best: int | None = None
    for subject_key in subject_keys:
        if subject_key not in tombstones:
            continue
        row = tombstones[subject_key]
        epoch = row.get("forget_epoch") if isinstance(row, dict) else None
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            # 墓碑在但内容坏了：不能当作没有墓碑放行，旧请求会借此写回已清除的记忆
            raise IdempotencyStateError(f"tombstone of {subject_key!r} is malformed")
        if best is None or epoch > best:
            best = epoch
    return best


# ── startup cleanup ──────────────────────────────────────────────────────

async def cleanup_expired(
    lanlan_names: Iterable[str],
    *,
    ttl_s: float | None = None,
    now: float | None = None,
) -> dict:
    """Drop staging leftovers and tombstones older than the retention period.

    Key records are never touched (``done`` / ``cancelled`` / ``pending`` are
    kept forever), and the staging of a ``pending`` key is kept too: it is
    the only copy of the generated products and of the apply progress; so
    are the tombstones of subjects such a staging document references.
    Characters being released (rename / delete draining) are skipped through
    the same lifecycle admission the write endpoints use. Best-effort per
    character: one unreadable file is logged and skipped, never aborts the
    sweep.
    """
    if ttl_s is None:
        from config import MEMORY_IDEMPOTENCY_TTL_S

        ttl_s = MEMORY_IDEMPOTENCY_TTL_S
    current = time.time() if now is None else float(now)
    cutoff = current - float(ttl_s)
    report = {"staging_removed": 0, "tombstones_removed": 0}
    from . import runtime

    for name in lanlan_names:
        # 与写端点同一套角色生命周期准入：删除 / 改名正在排空时不碰这个角色，
        # 否则写回墓碑文件会把刚删掉的角色目录重新建出来
        lease = runtime._begin_character_request(name)
        if lease is None:
            continue
        try:
            await _cleanup_one(name, cutoff, report)
        finally:
            runtime._end_character_request(name, lease)
    if report["staging_removed"] or report["tombstones_removed"]:
        logger.info(
            "[Idempotency] 启动清理：暂存 %d、墓碑 %d",
            report["staging_removed"],
            report["tombstones_removed"],
        )
    return report


async def _cleanup_one(name: str, cutoff: float, report: dict) -> None:
    protected: set[str] = set()
    if not await asyncio.to_thread(os.path.isdir, _character_dir(name)):
        return
    try:
        # 按文件名反查它属于哪个键：文件名是键的摘要，内容里的键可能坏了 / 被改过，
        # 只有键记录能说明这份暂存还是不是某个 pending 键唯一的副本
        records = await asyncio.to_thread(_read_json_object, keys_path(name))
        owner_of_path = {
            os.path.normcase(os.path.abspath(staging_path(name, key))): key
            for key in records
            if isinstance(key, str) and key
        }
        for path, document, mtime in await list_staging(name):
            created = (
                document.get("created_at") if isinstance(document, dict) else None
            )
            age_anchor = (
                float(created)
                if isinstance(created, (int, float)) and not isinstance(created, bool)
                else mtime
            )
            if age_anchor >= cutoff:
                continue
            key = owner_of_path.get(os.path.normcase(os.path.abspath(path)))
            if key is None:
                # 没有任何键记录对应这个文件（孤儿：读不出、内嵌键坏了也一样）：不是任何
                # pending 键的副本，过期即删，抽取原文不长期留在磁盘上
                if await asyncio.to_thread(_remove_file, path):
                    report["staging_removed"] += 1
                continue
            # 与在飞的同键请求互斥：它可能正要补应用这份暂存。
            async with key_lock(name, key):
                record = await read_key(name, key)
                if record is not None and record.get("state") == KEY_STATE_PENDING:
                    # pending 键的暂存是已生成产物与应用进度的唯一副本（读不出 / 内嵌键
                    # 坏了也一样：同键重试读它会 fail closed）：删了重试只能重新生成，
                    # 序号对不上的 effect_key 会挡错事实、漏掉没应用的
                    continue
                if await asyncio.to_thread(_remove_file, path):
                    report["staging_removed"] += 1
    except Exception as exc:  # noqa: BLE001 - startup sweep is best-effort
        logger.warning(f"[Idempotency] {name}: 暂存清理失败（跳过）: {exc}")
    try:
        removed: list[str] = []

        # 还被保留着的暂存（pending）引用的 subject：它们的墓碑是「这份暂存早于清除」
        # 的唯一持久证据，不能先于暂存过期
        for _path, document, _mtime in await list_staging(name):
            protected.update(_staged_subjects(document))
        # 先记 pending、后写暂存：崩在两步之间的 pending 键没有暂存，清除也取消不了它，
        # 它的请求 subject 的墓碑同样是唯一的证据
        try:
            records = await asyncio.to_thread(_read_json_object, keys_path(name))
        except Exception:  # noqa: BLE001 - 读不出键记录就不删任何墓碑
            return
        for record in records.values():
            if isinstance(record, dict) and record.get("state") == KEY_STATE_PENDING:
                request = record.get("request")
                wire_keys = request.get("wire_keys") if isinstance(request, dict) else None
                if isinstance(wire_keys, list):
                    protected.update(str(k) for k in wire_keys)
                routed = record.get("routed_keys")
                if isinstance(routed, list):
                    protected.update(str(k) for k in routed)

        def _drop_expired(data: dict) -> bool:
            for subject_key, row in list(data.items()):
                if subject_key in protected:
                    continue
                stamp = row.get("forgotten_at") if isinstance(row, dict) else None
                if (
                    isinstance(stamp, (int, float))
                    and not isinstance(stamp, bool)
                    and stamp < cutoff
                ):
                    data.pop(subject_key, None)
                    removed.append(subject_key)
            return bool(removed)

        if await asyncio.to_thread(os.path.exists, tombstones_path(name)):
            await _update_json_object(name, tombstones_path(name), _drop_expired)
        report["tombstones_removed"] += len(removed)
    except Exception as exc:  # noqa: BLE001 - startup sweep is best-effort
        logger.warning(f"[Idempotency] {name}: 墓碑清理失败（跳过）: {exc}")


def _staged_subjects(document: Any) -> set[str]:
    subjects = document.get("subjects") if isinstance(document, dict) else None
    return {str(s) for s in subjects} if isinstance(subjects, list) else set()
