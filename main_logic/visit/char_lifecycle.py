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

"""Visit data across a local character's rename and delete (design OD-13, section 3.2.6, PR-09b).

Both transactions leave a recoverable marker at the top level of
``visit_peers.json`` (outside every account partition) for as long as the
visit data is not consistent with the character config:

* rename -- ``pending_rename = {old, new, uid}``, written before the rename
  commits. :func:`settle_rename` then reads the config: the character now
  named ``new`` moves the roster ``by_char`` (with ``last_summary``) and the
  ``own_char`` of every spool header / ``state.json`` (the debrief replay
  queue lives in ``state.json``) to ``new``; a rolled-back rename moves
  whatever was already moved back to ``old``. Only then is the marker
  cleared, so a failure leaves it for startup reconciliation, which runs the
  same :func:`reconcile_rename`.
* delete -- ``pending_retire = [{name, character_uid}, ...]``, one item per
  deleted character, written before the delete commits.
  :func:`settle_retire` drops the item when the character is still
  configured (the delete rolled back) and otherwise retires its visit data:
  spools / ``state.json`` owned by ``character_uid`` (legacy files without
  an owner uid by name), every ``by_char[name]`` of the roster (peers left
  empty go) and the visit persona. ``.upload.json`` files and
  ``visit_reports/`` stay. The memory_server staging of unfinished digests
  lives under ``memory_dir/<character>/`` and goes with the character's
  memory directory in the delete transaction itself. While an item is
  pending, a new character may not take its name (:func:`name_marker_state`).
  A delete of a character whose own rename is still unreconciled settles
  that rename first (otherwise the entries left under its other name would
  outlive it), and a cloudsave import that removes local characters records
  them with :func:`record_removed_characters_sync` before it commits.

While either marker names a character, no new character may take that name
(:func:`name_marker_state`): the roster keys entries by name.

Machines without any visit data never get a marker: the transactions are
unchanged for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from config.visit_settings import VISIT_PEERS_FILENAME, VISIT_PERSONA_DIRNAME, VISIT_SPOOL_DIRNAME
from main_logic.visit import memory_bridge
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    add_roster_marker_item,
    add_roster_marker_item_sync,
    clear_roster_marker,
    promote_roster_marker,
    prune_roster_marker_items,
    read_roster_marker,
    read_roster_marker_sync,
    read_roster_markers,
    read_roster_markers_sync,
    rename_marker_names,
    replace_roster_marker,
    replace_roster_marker_sync,
    rotate_roster_marker,
    set_roster_marker_sync,
    remove_roster_marker_item,
    set_roster_marker,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

PENDING_RENAME = "pending_rename"
PENDING_RENAME_QUEUE = "pending_rename_queue"
PENDING_RETIRE = "pending_retire"
CLOUDSAVE_SOURCE = "cloudsave"
"""``source`` of a rename marker a cloudsave import wrote: nothing is migrated before startup recovery."""

RetirePersona = Callable[[str], Awaitable[Any]]
"""``retire_persona(character_uid)``: delete the visit persona of a deleted character."""

LoadNames = Callable[[], Awaitable[tuple[set[str], dict[str, str] | None]]]
"""Returns ``(every configured name, {name: character_uid})`` read from the character config."""


class RenamePendingElsewhere(RuntimeError):
    """An earlier rename's marker is still unreconciled, so a new rename cannot write its own."""


def _may_exist(path: Path) -> bool:
    # 只有「确实不存在」才算没有；stat 不了（权限、被占用）不能当成没有，否则改名 / 删除
    # 不写标记，这份数据权限恢复后也没人迁移 / 退役它
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _has_visit_files_sync(config_dir: Path, character_uid: str | None) -> bool:
    if _may_exist(config_dir / VISIT_PEERS_FILENAME):
        return True
    spool_dir = config_dir / VISIT_SPOOL_DIRNAME
    try:
        with os.scandir(spool_dir) as entries:
            if any(True for _ in entries):
                return True
    except (FileNotFoundError, NotADirectoryError):
        pass  # 没有 spool 目录：没有场次，接着看人设
    except OSError:
        # 列不出来不能当「没有串门数据」：照常写标记，由迁移 / 退役步骤自己报错保留它
        return True
    return bool(character_uid) and _may_exist(config_dir / VISIT_PERSONA_DIRNAME / f"{character_uid}.json")


async def has_visit_data(config_dir: str | Path, character_uid: str | None = None) -> bool:
    """Whether this machine has any visit data a rename / delete would have to follow.

    The roster, any file in the spool directory, or (with ``character_uid``)
    that character's visit persona.
    """
    return await asyncio.to_thread(_has_visit_files_sync, Path(config_dir), character_uid)


# ── 改名 ──────────────────────────────────────────────────────────────

ALL_NAMES = None
"""Result of :func:`reconcile_rename`: every name-dependent step must wait."""

MARKER_CHANGED = object()
"""Returned by :func:`reconcile_rename` when the marker is not the one the caller expected."""


async def reconcile_rename(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None = None,
    expected: Any = None, *, undo_moves: bool = True,
) -> Any:
    """Finish or roll back a pending character rename; return the names still unsettled.

    The marker is ``{old, new}`` plus, when the rename transaction wrote it,
    the renamed character's ``uid``: with ``uid_of`` (current name -> uid)
    the direction is decided by which name that uid has now. Without a uid,
    by which of the two names exists. When neither name exists the
    character was deleted (its data is retired by uid): the marker is
    dropped. When the uid now has a third name (a cloudsave import renamed
    it again while this marker held the slot) and neither old name is reused,
    entries under both names move to the current one. A migration into a
    name that still has a ``pending_retire`` item waits for that retirement
    (the marker is kept). An empty set means no rename is pending; ``{old, new}`` that
    the marker is kept as genuinely ambiguous (only those two names wait);
    ``None`` that the roster or marker is unreadable (everything waits).
    ``undo_moves=False`` (the rename transaction itself, which migrates only
    after it committed) settles a rolled-back rename by clearing the marker
    without rewriting anything.
    """
    config_dir = Path(config_dir)
    try:
        marker = await read_roster_marker(config_dir, PENDING_RENAME)
    except RosterCorruptError as exc:
        logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
        return ALL_NAMES
    if expected is not None and marker != expected:
        # 调用方是按另一份标记拿的守卫：这次读到的标记属于别的角色，不能拿着错的守卫去迁
        return MARKER_CHANGED
    if marker is None:
        return frozenset()
    old = marker.get("old") if isinstance(marker, dict) else None
    new = marker.get("new") if isinstance(marker, dict) else None
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        # 格式坏了的标记已没有可对账的信息，留着只会永久挡住补录与清除：记诊断后清掉
        memory_bridge.diag("pending_rename_malformed")
        logger.error("visit recovery: malformed pending_rename %r dropped", marker)
        return frozenset() if await clear_roster_marker(config_dir, PENDING_RENAME, marker) else ALL_NAMES
    # rename_char 按机器上的角色名改写全部账号分区，与 own_uid 无关
    roster = PeerRoster(config_dir, own_uid="pending-rename")
    uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
    if uid is not None and uid_of is not None:
        # 有 uid 就按它现在叫什么定方向：新旧两个名字同时存在（旧名被新建角色占用）也分得清
        current = {name for name, value in uid_of.items() if value == uid}
        # 另一个名字被别的角色占着（旧名被新建角色复用）：名册条目只按名字存，迁移会把
        # 两个角色的记录混到一起。分不开就不迁，留着标记、只挡这两个名字
        # 云快照导入把名字交给了本机原来没有的角色（handed_over）：导入那一刻这个名字下的条目
        # 都还是这个角色的，新角色不能继承，照常迁走
        handed = _handed_over_names(marker)
        held = rename_marker_names(marker) or (old, new)
        if {name for name, value in uid_of.items() if value == uid} & {old, new} and marker.get("migrated") is not True:
            # 角色还停在这笔改名本身的两个名字上：chained 里的名字是一次回滚了的云快照导入记下的
            # （或它又改回来了），不再算它的，也不拿来判断复用。已经开始迁过（migrated）就不行：
            # 条目可能已挪进某个 chained 名字，得一起迁回来
            held = (old, new)
        elif len(current) == 1 and marker.get("migrated") is not True and next(iter(current)) in held[2:]:
            # 同理，角色还停在一个 chained 中间名字上：之后追加的名字来自回滚了的导入，不再算它的
            held = held[:held.index(next(iter(current))) + 1]
        reused = any(name in uid_of and uid_of[name] != uid and name not in handed for name in held)
        forward = new in current and not reused
        backward = old in current and not forward and not reused
        deleted = not current
        # 这个角色又被改到别的名字（云快照导入带来的改名，标记位被这笔占着、没能另记，名字记在
        # chained 里）：标记涉及的每个名字下的条目都还是它的，一起迁到它现在的名字
        moved = len(current) == 1 and not current & {old, new} and not reused
    else:
        held = (old, new)
        forward = new in names and old not in names
        backward = old in names and new not in names
        deleted = old not in names and new not in names
        moved = False
    # 云快照导入记下的改名：迁移只在启动对账里做，导入回滚时什么都没迁过。不能按普通改名
    # 反向改写——新名下是本机另一个角色（导入要删它、但跟着一起回滚了）的真实数据
    # 已经开始迁过（migrated）的就不再是「什么都没迁过」：导入肯定已提交，之后又改回旧名要照常反向迁回
    migrated = marker.get("migrated") is True
    from_cloudsave = marker.get("source") == CLOUDSAVE_SOURCE and not migrated
    undo = undo_moves and not from_cloudsave
    if from_cloudsave and uid is not None and uid_of is not None and old in current and not forward:
        # 导入回滚了（这个角色还叫旧名）：什么都没迁过，新名被别的角色占着也无所谓，直接撤标记
        backward = True
    target = new if forward else next(iter(current)) if moved else old if backward and undo else None
    if target is not None:
        try:
            retiring = await read_roster_marker(config_dir, PENDING_RETIRE)
        except RosterCorruptError as exc:
            logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
            return ALL_NAMES
        # 按名字清名册那一步已做完的退役项（roster_done）不再碰这个名字：剩下的按 uid 的步骤
        # 补不完也不影响迁进来的条目，不能让改名跟着一直等
        if isinstance(retiring, list) and any(
                (_item_fields(item) or ("", None))[0] == target and item.get("roster_done") is not True
                for item in retiring):
            # 目标名字下还有已删除角色的残留没退役完：先迁进去，之后按名字退役会连这个角色的条目一起删。
            # 留着标记等退役补完（启动恢复先补退役、再对账）
            logger.warning("visit recovery: pending_rename %r -> %r waits for the retirement of %r", old, new, target)
            # 目标名可能是第三个名字（不在 held 里）：一起报出去，名字占用才认得出这是「在等退役」而不是歧义
            return frozenset(held) | {target}
    if (forward or moved) and not migrated and (from_cloudsave or len(held) > 2):
        # 迁移是几次各自独立的写入：中途失败时标记还在、条目已经挪了一部分。先记下「已开始迁」，
        # 之后角色再被改回旧名时按普通改名反向迁回（含 chained 名字），不能当成导入回滚直接撤标记
        started = {**marker, "migrated": True}
        if not await replace_roster_marker(config_dir, PENDING_RENAME, marker, started):
            logger.warning("visit recovery: pending_rename %r -> %r changed before migrating, deferred", old, new)
            return ALL_NAMES
        marker = started
    if forward or moved:
        # 标记涉及的每个名字（含 chained 记下的中间名字）下的条目都迁到它现在的名字
        for name in held:
            if name != target:
                await roster.rename_char(name, target)
                await VisitSpool.rename_own_char(config_dir, name, target)
    elif backward and not undo:
        # 事务自己收尾：迁移只在提交之后做，回滚时什么都没动过。不能反向改写——新名下
        # 若有已删除角色的残留数据，会被错挂到这个角色名下
        pass
    elif backward:
        # 改名没生效：把已经改写成新名的场次改回旧名（迁过一部分的 chained 名字一起）
        for name in held:
            if name != old:
                await VisitSpool.rename_own_char(config_dir, name, old)
                await roster.rename_char(name, old)
    elif deleted:
        # 两个名字都不在：这个角色已被删除，它的名册条目与场次由删除的退役步骤按 uid
        # 处理。标记不再有可对账的对象，留着只会永远挡住清除与逐场补录
        logger.warning("visit recovery: pending_rename %r -> %r names a deleted character, dropped", old, new)
    else:
        logger.warning("visit recovery: pending_rename %r -> %r is ambiguous, kept", old, new)
        # 标记涉及的每个名字（含 chained）都还挂着：覆盖下载其中任一个都要答「请先删除其中一个」
        return frozenset(held)
    return frozenset() if await clear_roster_marker(config_dir, PENDING_RENAME, marker) else ALL_NAMES


async def promote_queued_rename(config_dir: str | Path) -> bool:
    """Move the next queued rename into the empty ``pending_rename`` slot; True when one moved."""
    return await promote_roster_marker(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE) is not None


async def drop_deleted_queued_renames(config_dir: str | Path, uid_of: dict[str, str] | None) -> None:
    """Drop the queued renames of characters no longer configured (needs ``uid_of``).

    The same outcome as the slot's deleted branch: nothing moves, the
    delete's retirement handles the data. Done without waiting for the slot,
    which an ambiguous rename may hold for good.
    """
    if uid_of is None:
        return
    configured = set(uid_of.values())
    for item in _queued_renames(await read_roster_marker(config_dir, PENDING_RENAME_QUEUE)):
        uid = item.get("uid") if isinstance(item, dict) else None
        if isinstance(uid, str) and uid and uid not in configured:
            logger.warning("visit recovery: queued rename %r names a deleted character, dropped", item)
            await remove_roster_marker_item(config_dir, PENDING_RENAME_QUEUE, item)


async def drop_malformed_queued_renames(config_dir: str | Path) -> None:
    """Drop the ``pending_rename_queue`` items that carry no ``old`` / ``new`` (a non-list queue entirely).

    The same outcome as the slot's malformed branch: nothing is left to
    reconcile, and keeping them would defer every clearing and replay (the
    queued names cannot be read) for good.
    """
    dropped = await prune_roster_marker_items(
        config_dir, PENDING_RENAME_QUEUE, lambda item: rename_marker_names(item) is not None)
    if dropped:
        memory_bridge.diag("pending_rename_queue_malformed")
        logger.error("visit recovery: malformed pending_rename_queue items %r dropped", dropped)


async def reconcile_renames(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None = None,
    reconcile: Callable[[], Awaitable[Any]] | None = None, pending_groups: list[frozenset[str]] | None = None,
    drop_deleted: bool = True,
) -> Any:
    """:func:`reconcile_rename` for the slot and every queued rename; return the names still unsettled.

    A cloudsave import that renamed several characters queues all but the
    first (``pending_rename_queue``); the slot takes them one at a time.
    Malformed and deleted characters' queued renames are dropped first. A
    rename that stays pending (ambiguous, or waiting for a retirement) moves
    to the end of the queue so the others still settle; one that shares a
    name with a pending one keeps its turn behind it (migrating it first
    would mix both characters' entries). Each marker is tried once per call.
    Returns the names of the renames reconciled and still pending (empty when
    all settled), or ``ALL_NAMES`` when the roster is unreadable.
    ``reconcile`` (startup recovery: under the character's lifecycle guard)
    replaces the plain :func:`reconcile_rename` of the slot.
    ``pending_groups`` (optional) receives the names of each rename still
    pending, one set per rename. ``drop_deleted=False`` skips dropping the
    deleted characters' queued renames (startup recovery already did it
    under the character-config lock, with a fresher map).
    """
    await drop_malformed_queued_renames(config_dir)
    if drop_deleted:
        await drop_deleted_queued_renames(config_dir, uid_of)
    run = reconcile or (lambda: reconcile_rename(config_dir, names, uid_of))
    pending: set[str] = set()
    waiting: set[str] = set()
    seen: list[Any] = []
    while True:
        try:
            marker = await read_roster_marker(config_dir, PENDING_RENAME)
        except RosterCorruptError as exc:
            logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
            return ALL_NAMES
        if marker is None:
            if await promote_queued_rename(config_dir):
                continue
            break
        if marker in seen:
            # 一圈都试过了：剩下的都还挂着，最早卡住的那笔留在位置上
            break
        seen.append(marker)
        held = set(rename_marker_names(marker) or ())
        if held & (pending | waiting):
            # 跟前面一笔还挂着的改名共用名字（比如链式改名 B -> C 之后的 A -> B）：先迁它会把
            # 两个角色的条目混到一起，排在那笔后面等它
            waiting |= held
        else:
            unsettled = await run()
            if not isinstance(unsettled, frozenset):
                return unsettled
            if not unsettled:
                continue
            pending |= unsettled
            if pending_groups is not None:
                pending_groups.append(unsettled)
        # 这笔还挂着（永久歧义 / 在等退役）：挪到队尾，让队列里其它角色的改名照常对账
        if await rotate_roster_marker(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE, marker) is None:
            break
    return frozenset(pending)


def _queued_renames(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _handed_over_names(marker: Any) -> frozenset[str]:
    names = marker.get("handed_over") if isinstance(marker, dict) else None
    if not isinstance(names, list):
        return frozenset()
    return frozenset(name for name in names if isinstance(name, str) and name)


async def begin_rename(
    config_dir: str | Path, old: str, new: str, character_uid: str | None, *,
    names: set[str], uid_of: dict[str, str] | None,
) -> dict | None:
    """Write ``pending_rename`` before the rename commits; return the marker (``None``: no visit data).

    Runs inside the rename transaction (under the character-config lock),
    with ``names`` / ``uid_of`` read from the config before the rename. An
    earlier marker is reconciled first, and the whole queue when this
    character has a queued rename or ``old`` was handed over to it by another
    character's pending rename; when it cannot be (ambiguous, unreadable
    spools, this character's own or the handing rename still pending)
    :class:`RenamePendingElsewhere` is raised and nothing is written. An unreadable roster raises :class:`RosterCorruptError`.
    """
    config_dir = Path(config_dir)
    if not await has_visit_data(config_dir):
        return None
    # 旧名是云快照刚交给这个角色的、原来那个角色的改名还没对完账：迁移会把人家的条目一起带走

    async def current() -> tuple[set[str], dict[str, str] | None]:
        return names, uid_of

    if await _settle_handed_over(config_dir, old, character_uid, current):
        # 交出旧名的那笔改名永久歧义：改名会把原角色留在旧名下的条目一起迁走，只能等歧义解开
        # （删除这个角色可以照常进行，见 begin_retire）
        raise RenamePendingElsewhere("another character's rename still owns this name's visit data")
    # 这个角色自己还有一笔排在队里的改名（云快照导入记下的）：位置那笔对得完不代表它也对完了。
    # 先把整个队列对一遍；它还挂着就拒绝这次改名，否则那笔只记着旧的两个名字，新名字没人占着
    if await _own_rename_pending(config_dir, old, character_uid, queued_only=True):
        try:
            await reconcile_renames(config_dir, names, uid_of)
        except Exception as exc:  # noqa: BLE001 - 对不完账就拒绝这次改名，标记留给启动对账
            logger.warning("visit rename: queued rename of %r not reconciled: %r", old, exc)
            raise RenamePendingElsewhere("this character's queued rename is still being migrated") from exc
        if await _own_rename_pending(config_dir, old, character_uid, queued_only=False):
            raise RenamePendingElsewhere("this character's queued rename is still being migrated")
    marker: dict[str, str] = {"old": old, "new": new}
    if character_uid:
        marker["uid"] = character_uid
    for attempt in range(2):
        written, _existing = await set_roster_marker(config_dir, PENDING_RENAME, marker)
        if written:
            return marker
        if attempt:
            break
        # 上一次改名的迁移没做完（失败 / 崩溃）：标记只有一个位置，先按现在的配置把它对完账
        try:
            unsettled = await reconcile_rename(config_dir, names, uid_of)
        except Exception as exc:  # noqa: BLE001 - 对不完账就拒绝这次改名，标记留给启动对账
            logger.warning("visit rename: earlier pending_rename not reconciled: %r", exc)
            break
        if unsettled != frozenset():
            break
    raise RenamePendingElsewhere("an earlier rename's visit data is still being migrated")


async def settle_rename(config_dir: str | Path, marker: Mapping[str, Any], load_names: LoadNames) -> bool:
    """Migrate (or roll back) the visit data of a finished rename transaction; True once the marker is gone.

    ``load_names`` re-reads the config after the transaction: the direction
    is whatever the character is called now (a rolled-back rename moves
    nothing forward). Any failure leaves the marker for startup recovery.
    """
    try:
        names, uid_of = await load_names()
        result = await reconcile_rename(Path(config_dir), names, uid_of, expected=dict(marker), undo_moves=False)
    except Exception as exc:  # noqa: BLE001 - 迁移失败不影响已提交的改名，标记留给启动对账
        logger.warning("visit rename: visit data not migrated yet, startup recovery retries: %r", exc)
        return False
    if result is MARKER_CHANGED:
        # 标记已被别的流程（启动对账）处理掉：它按同一套规则迁移过了
        return True
    return result == frozenset()


# ── 删除退役 ──────────────────────────────────────────────────────────


def retire_item(name: str, character_uid: str | None) -> dict:
    """The ``pending_retire`` entry of one deleted character."""
    return {"name": name, "character_uid": character_uid or None}


async def _settle_own_rename(
    config_dir: Path, name: str, character_uid: str | None, load_names: LoadNames,
) -> tuple[str, ...]:
    """Reconcile a pending rename of the character about to be deleted; raise when it cannot be.

    Retirement goes by the character's current name only: entries a failed
    rename migration left under its other name would outlive it, and a new
    character taking that name later would inherit them. An unsettleable
    rename (unreadable spools) raises :class:`RenamePendingElsewhere`; an
    ambiguous one (the other name was taken by another character meanwhile)
    lets the delete go ahead, nothing being separable any more. Returns the
    other names of every rename of this character still pending afterwards
    (ambiguous, waiting for a retirement, or queued behind a pending rename
    sharing one of its names) that
    no other local character holds: they are retired with the character.
    """
    marker, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    queued = _queued_renames(queued)
    if not _rename_names_of(marker, name, character_uid) and not any(
            _rename_names_of(item, name, character_uid) for item in queued):
        return ()
    try:
        names, uid_of = await load_names()
        unsettled = await reconcile_renames(config_dir, names, uid_of)
    except Exception as exc:  # noqa: BLE001 - 对不完账就拒绝这次删除，标记留给启动对账
        raise RenamePendingElsewhere("this character's rename is still being migrated") from exc
    if unsettled is ALL_NAMES:
        raise RenamePendingElsewhere("this character's rename is still being migrated")
    if unsettled:
        # 另一个名字已被别的角色占用：两边的条目分不开了，删掉这个角色不会让情况更糟；
        # 它的 uid 一消失，下次对账就按「已删除」清掉标记
        logger.warning("visit retire: ambiguous pending_rename of %r left as is, delete goes ahead", name)
    # 对账后仍挂着的这个角色的改名（在等某个名字的退役、或排在一笔永久歧义的改名后面）：
    # 另一个名字没被别的角色占着，就随角色一起退役，之后这笔改名按「已删除」清掉。
    # 被别的角色占着的名字（真正的歧义）条目分不开，照旧不动
    held = set(names) - {name}
    marker, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    return tuple(dict.fromkeys(
        other
        for item in (marker, *_queued_renames(queued))
        for other in _rename_names_of(item, name, character_uid)
        if other != name and other not in held
    ))


async def begin_retire(
    config_dir: str | Path, name: str, character_uid: str | None, *, load_names: LoadNames | None = None,
) -> dict | None:
    """Add the ``pending_retire`` item before the delete commits; return it (``None``: no visit data).

    With ``load_names`` (the config before the delete), a pending rename of
    this very character, or one that handed ``name`` over to it, is
    reconciled first; when it cannot be,
    :class:`RenamePendingElsewhere` is raised and nothing is written. An
    unreadable roster raises :class:`RosterCorruptError` (the delete is
    refused rather than leaving visit data no marker points at).
    """
    config_dir = Path(config_dir)
    if not await has_visit_data(config_dir, character_uid):
        return None
    others: tuple[str, ...] = ()
    keep_entries = False
    if load_names is not None:
        # 名字是云快照刚交给这个角色的、原来那个角色的改名还没对完账：按名字退役会删掉人家的条目
        keep_entries = await _settle_handed_over(config_dir, name, character_uid, load_names)
        others = await _settle_own_rename(config_dir, name, character_uid, load_names)
    for other in others:
        await add_roster_marker_item(config_dir, PENDING_RETIRE, retire_item(other, character_uid))
    item = retire_item(name, character_uid)
    if keep_entries:
        # 那笔改名永久歧义：照常删除，但不按名字清名册（记成已做完），名下条目留给原角色；
        # 名字仍被那笔改名占着，新角色继承不到
        item = {**item, "roster_done": True}
    await add_roster_marker_item(config_dir, PENDING_RETIRE, item)
    return item


def _item_fields(item: Any) -> tuple[str, str | None] | None:
    if not isinstance(item, dict):
        return None
    name, uid = item.get("name"), item.get("character_uid")
    if not isinstance(name, str) or not name or not (uid is None or (isinstance(uid, str) and uid)):
        return None
    return name, uid


async def _retire(config_dir: Path, name: str, uid: str | None, names: set[str],
                  retire_persona: RetirePersona | None, *, roster_done: bool = False,
                  ) -> tuple[list[BaseException], bool]:
    """Run every retirement step; return the failures and whether the roster step is done."""
    failures: list[BaseException] = []
    try:
        # 头行不带 own_char_uid 的旧场次按名字认：只在没有同名新角色时
        await VisitSpool.retire_char(config_dir, uid or "", legacy_name=None if name in names else name)
    except Exception as exc:  # noqa: BLE001 - 各步互不连累，最后统一报
        failures.append(exc)
    roster_ok = roster_done
    if not roster_done:
        try:
            await PeerRoster(config_dir, own_uid="pending-retire").retire_char(name)
            roster_ok = True
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
    if uid and retire_persona is not None:
        try:
            await retire_persona(uid)
        except ValueError:
            # 不是合法 uid：不可能有对应的人设文件
            pass
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
    return failures, roster_ok


async def settle_retire(
    config_dir: str | Path, item: Mapping[str, Any], *, names: set[str], uid_of: Mapping[str, str] | None,
    retire_persona: RetirePersona | None,
) -> bool:
    """Finish one ``pending_retire`` item against the current config; True once the item is removed.

    Still configured (by uid, or by name for a character without one or
    when ``uid_of`` is not known): the
    delete did not commit, the item is dropped and nothing is retired.
    Otherwise every retirement step runs (idempotent) and the item is
    removed only when all of them succeeded. The roster step goes by name
    and runs once: when only other steps failed, the item is marked
    ``roster_done`` so a retry never clears what a later character of that
    name wrote meanwhile.
    """
    config_dir = Path(config_dir)
    fields = _item_fields(item)
    if fields is None:
        memory_bridge.diag("pending_retire_malformed")
        logger.error("visit retire: malformed pending_retire item %r dropped", item)
        await remove_roster_marker_item(config_dir, PENDING_RETIRE, dict(item) if isinstance(item, Mapping) else item)
        return True
    name, uid = fields
    configured = uid in set(uid_of.values()) if uid and uid_of is not None else name in names
    if not configured:
        roster_done = item.get("roster_done") is True
        failures, roster_ok = await _retire(config_dir, name, uid, names, retire_persona, roster_done=roster_done)
        if failures:
            # 角色不回滚：标记保留，启动对账补完
            logger.warning("visit retire: visit data of a deleted character not fully retired: %r", failures[0])
            if roster_ok and not roster_done:
                # 按名字清名册这一步已做完：记下来，之后重试不再按名字清——这个名字之后可能
                # 归了新角色（云快照导入不经过名字占用判定），它写下的条目不能被反复清掉
                await replace_roster_marker(config_dir, PENDING_RETIRE, dict(item), {**item, "roster_done": True})
            return False
    await remove_roster_marker_item(config_dir, PENDING_RETIRE, dict(item))
    return True


async def replay_retires(
    config_dir: str | Path, load_names: LoadNames, *, retire_persona: RetirePersona | None,
    config_lock: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
) -> bool:
    """Startup recovery: settle every ``pending_retire`` item; True when none is left.

    Each item is decided and retired under ``config_lock`` (the
    character-config mutation lock), so it never races a delete whose
    marker is written but whose config is not committed yet.
    """
    config_dir = Path(config_dir)
    try:
        marker = await read_roster_marker(config_dir, PENDING_RETIRE)
    except RosterCorruptError as exc:
        logger.error("visit recovery: roster unreadable, retirements not replayed: %s", exc)
        return False
    if marker is None:
        return True
    if not isinstance(marker, list):
        memory_bridge.diag("pending_retire_malformed")
        logger.error("visit recovery: malformed pending_retire %r dropped", marker)
        return await clear_roster_marker(config_dir, PENDING_RETIRE, marker)
    clean = True
    for item in marker:
        async with (config_lock() if config_lock is not None else contextlib.nullcontext()):
            # 锁内重读标记：等锁期间这一项可能已被别处处理掉、同名新角色也已建好，
            # 拿着旧快照再退役会删掉新角色的名册条目
            try:
                current = await read_roster_marker(config_dir, PENDING_RETIRE)
            except RosterCorruptError:
                clean = False
                continue
            if not isinstance(current, list) or item not in current:
                continue
            # 锁内重读配置：等锁期间删除事务可能刚提交 / 刚回滚
            names, uid_of = await load_names()
            try:
                clean = await settle_retire(config_dir, item, names=names, uid_of=uid_of,
                                            retire_persona=retire_persona) and clean
            except (OSError, RosterCorruptError) as exc:
                # 一项写不进去（记 roster_done / 撤标记时磁盘出错）不能中断整轮重放：这一项留着下次再补
                logger.error("visit recovery: retirement of %r not settled: %r", item, exc)
                clean = False
    return clean


NAME_FREE = "free"
NAME_RETIRING = "retiring"
NAME_RENAMING = "renaming"
NAME_UNREADABLE = "unreadable"


async def name_marker_state(config_dir: str | Path, name: str) -> str:
    """Whether a pending marker still holds ``name``, as one of the ``NAME_*`` states.

    ``NAME_RETIRING``: a deleted character of that name is being retired.
    ``NAME_RENAMING``: it is the old or the new name of an unreconciled
    rename (the roster entries under it are not settled yet).
    ``NAME_UNREADABLE``: the roster cannot be read, so nothing is known;
    callers creating characters let it pass: the retirement cannot run either
    until the file is readable again.
    """
    try:
        # 一次读出三个标记：分开读可能拼出从未存在过的状态（读完位置后恢复刚好把队首挪进位置、
        # 再读队列已空），把排队中的名字误判成空闲
        retiring, renaming, queued = await read_roster_markers(
            config_dir, PENDING_RETIRE, PENDING_RENAME, PENDING_RENAME_QUEUE)
        queued = _queued_renames(queued)
    except RosterCorruptError as exc:
        logger.warning("visit lifecycle: roster unreadable, name %r not checked: %s", name, exc)
        return NAME_UNREADABLE
    if isinstance(retiring, list) and any((_item_fields(item) or ("", None))[0] == name for item in retiring):
        return NAME_RETIRING
    if any(name in (rename_marker_names(marker) or ()) for marker in (renaming, *queued)):
        return NAME_RENAMING
    return NAME_FREE


async def names_retiring(config_dir: str | Path, names: Iterable[str]) -> bool:
    """Whether a ``pending_retire`` item still clearing its roster names one of ``names`` (unreadable: True)."""
    wanted = set(names)
    try:
        retiring = await read_roster_marker(config_dir, PENDING_RETIRE)
    except RosterCorruptError:
        return True
    # 按名字清名册那一步已做完的退役项（roster_done）不再挡改名迁移，这里同样不算
    return isinstance(retiring, list) and any(
        (_item_fields(item) or ("", None))[0] in wanted and item.get("roster_done") is not True
        for item in retiring)


async def is_name_renaming(config_dir: str | Path, name: str) -> bool:
    """Whether a pending or queued rename holds ``name`` (unreadable roster: False)."""
    try:
        marker, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    except RosterCorruptError:
        return False
    return any(name in (rename_marker_names(item) or ()) for item in (marker, *_queued_renames(queued)))


async def is_name_retiring(config_dir: str | Path, name: str) -> bool:
    """Whether a deleted character called ``name`` still has a ``pending_retire`` item (unreadable: False)."""
    return await name_marker_state(config_dir, name) == NAME_RETIRING


async def _handed_over_pending(config_dir: Path, name: str, character_uid: str | None) -> bool:
    """Whether another character's pending rename lists ``name`` as handed over (to this character)."""
    slot, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    for item in (slot, *_queued_renames(queued)):
        uid = item.get("uid") if isinstance(item, dict) else None
        if name in _handed_over_names(item) and not (uid and character_uid and uid == character_uid):
            return True
    return False


async def _settle_handed_over(
    config_dir: Path, name: str, character_uid: str | None, load_names: LoadNames,
) -> bool:
    """Reconcile the renames that handed ``name`` over before it is renamed or deleted.

    A cloudsave import gave ``name`` to this character while another
    character's rename still owns the entries under it: retiring or moving
    them by name would destroy or carry off that character's data. Returns
    True when that rename stays ambiguous for good (the entries must stay
    under ``name``, which the rename keeps holding); raises
    :class:`RenamePendingElsewhere` while it can still settle (unreadable
    spools, waiting for a retirement or for another rename).
    """
    if not await _handed_over_pending(config_dir, name, character_uid):
        return False
    groups: list[frozenset[str]] = []
    try:
        names, uid_of = await load_names()
        unsettled = await reconcile_renames(config_dir, names, uid_of, pending_groups=groups)
    except Exception as exc:  # noqa: BLE001 - 对不完账就拒绝，标记留给启动对账
        logger.warning("visit lifecycle: rename handing %r over not reconciled: %r", name, exc)
        raise RenamePendingElsewhere("another character's rename still owns this name's visit data") from exc
    if not await _handed_over_pending(config_dir, name, character_uid):
        return False
    held = next((group for group in groups if name in group), None) if isinstance(unsettled, frozenset) else None
    if held is not None and not await names_retiring(config_dir, held):
        # 交出名字的那笔改名永久歧义：等也等不到。照旧挡住只会让接手的角色永远删不掉
        logger.warning("visit lifecycle: rename handing %r over is ambiguous; its entries stay under the name", name)
        return True
    raise RenamePendingElsewhere("another character's rename still owns this name's visit data")


async def _own_rename_pending(config_dir: Path, name: str, character_uid: str | None, *, queued_only: bool) -> bool:
    """Whether a rename of this character is queued (or, unless ``queued_only``, in the slot either)."""
    slot, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    markers = [*_queued_renames(queued)] if queued_only else [slot, *_queued_renames(queued)]
    return any(_rename_names_of(item, name, character_uid) for item in markers)


def _rename_names_of(marker: Any, name: str, character_uid: str | None) -> tuple[str, ...]:
    """The names ``marker`` holds when it is a pending rename of this character, else ``()``."""
    held = rename_marker_names(marker)
    if held is None:
        return ()
    marker_uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
    involved = marker_uid == character_uid if marker_uid and character_uid else name in held
    return held if involved else ()


def record_removed_characters_sync(
    config_dir: str | Path, removed: Iterable[Mapping[str, Any]], kept_local_names: Iterable[str] = (),
    replaced: Iterable[Mapping[str, Any]] = (),
) -> int:
    """Add a ``pending_retire`` item for each character a cloudsave import is about to remove.

    Called from the import thread right before it commits, with the
    ``{name, character_uid}`` of every local character absent from the
    snapshot and the names of the local characters it keeps. Startup
    recovery then settles the items against the committed config like any
    delete (an import that rolled back only drops them). A removed character
    whose own rename is still pending gets an item for its other name too:
    recovery drops the rename marker of a character that is gone, so the
    entries a failed migration left under that name would otherwise outlive
    it, and a character the snapshot brings under that name would inherit
    them. Only a name another local character already holds is left alone:
    its entries are mixed with that character's and cannot be told apart
    (the same rule as an ordinary delete). ``replaced`` characters (kept by
    name under a new uid: their entries under that name stay with it) get
    items for those other names only. Machines without visit data get
    nothing; returns the number of items added.
    """
    config_dir = Path(config_dir)
    kept = set(kept_local_names)
    try:
        slot, queued = read_roster_markers_sync(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
        rename_markers = [slot, *_queued_renames(queued)]
    except RosterCorruptError:
        rename_markers = []  # 名册读不出：写标记那一步同样会失败，由回调方记日志
    added = 0
    entries = [(entry, True) for entry in removed] + [(entry, False) for entry in replaced]
    for entry, own_name in entries:
        fields = _item_fields(retire_item(entry.get("name"), entry.get("character_uid")))
        if fields is None or not _has_visit_files_sync(config_dir, fields[1]):
            continue
        name, uid = fields
        if not own_name and uid is None:
            continue  # 没有 uid 就认不出它的改名标记
        names = list(dict.fromkeys(([name] if own_name else []) + [
            other for marker in rename_markers for other in _rename_names_of(marker, name, uid)
            if other != name and other not in kept
        ]))
        for retiring in names:
            added += bool(add_roster_marker_item_sync(config_dir, PENDING_RETIRE, retire_item(retiring, uid)))
    return added


def record_renamed_characters_sync(config_dir: str | Path, renamed: Iterable[Mapping[str, Any]]) -> int:
    """Write ``pending_rename`` for a character a cloudsave import keeps under another name.

    Called from the import thread right before it commits, with the
    ``{old, new, uid}`` of every local character the snapshot renamed (on
    another device). Startup recovery then reconciles the marker by uid like
    any rename: the roster entries and spools follow the character to its new
    name (or stay, when the import rolled back). The marker has one slot:
    a rename finding it taken by another character waits in
    ``pending_rename_queue`` and takes the slot once it clears; one finding it
    taken by the same character's unfinished rename is left to that one
    (recovery sees the character under a third name and moves both). Machines
    without visit data get nothing; returns the number of renames recorded.
    An old name the snapshot gives to a new character is marked afterwards
    by :func:`record_handed_over_names_sync`.
    """
    config_dir = Path(config_dir)
    written = 0
    for entry in renamed:
        old, new, uid = entry.get("old"), entry.get("new"), entry.get("uid")
        if not all(isinstance(value, str) and value for value in (old, new, uid)) or old == new:
            continue
        if not _has_visit_files_sync(config_dir, uid):
            continue
        marker = {"old": old, "new": new, "uid": uid, "source": CLOUDSAVE_SOURCE}
        handed = _handed_over_names(entry)
        if handed:
            # 链式改名：旧名接着归了本机另一个角色（它的改名排在后面），对账时不算被复用
            marker["handed_over"] = sorted(handed)
        ok, existing = set_roster_marker_sync(config_dir, PENDING_RENAME, marker)
        if ok:
            written += 1
        elif isinstance(existing, dict) and existing.get("uid") == uid:
            # 这个角色自己的上一笔改名还没对完账：对账见它到了第三个名字，会把两个旧名下的条目一起迁过去。
            # 新名字记进标记（chained），对账前同样算占用：否则覆盖下载这个名字会换掉角色的 uid，
            # 对账就按「已删除」清掉标记，两个旧名下的条目没人认领
            chained = list(existing.get("chained")) if isinstance(existing.get("chained"), list) else []
            value = dict(existing)
            if new not in chained and new not in (existing.get("old"), existing.get("new")):
                value["chained"] = [*chained, new]
            # 这一笔若记了 handed_over（链式改名里旧名接着归了下一个本机角色），一并带进已有标记
            handed = _handed_over_names(existing) | _handed_over_names(marker)
            if handed:
                value["handed_over"] = sorted(handed)
            if value != existing:
                written += bool(replace_roster_marker_sync(config_dir, PENDING_RENAME, existing, value))
            logger.info("visit rename: cloudsave renamed %r to %r on top of pending %r; recovery chains it",
                        old, new, existing)
        else:
            # 标记只有一个位置：排进队列，启动对账在这一笔清掉之后依次处理（同一次导入改了几个角色）
            written += bool(add_roster_marker_item_sync(config_dir, PENDING_RENAME_QUEUE, marker))
            logger.info("visit rename: cloudsave renamed %r to %r queued behind pending %r", old, new, existing)
    return written


def record_handed_over_names_sync(
    config_dir: str | Path, fresh_names: Mapping[str, str | None] | Iterable[str],
    removed: Iterable[Mapping[str, Any]] = (),
) -> int:
    """Mark the names a cloudsave import gives to new characters on the pending renames still holding them.

    A pending rename (the slot or a queued one) of a character the import
    keeps still has entries under its two names; when the snapshot hands one
    of them to a character new to this machine, those entries remain the
    renamed character's and the marker records the name as ``handed_over``
    (otherwise the reconciliation would see the name reused and keep the
    entries there, for the new character to inherit). ``fresh_names`` maps
    each such name to the uid of its holder before the import (``None``: no
    local character had it): a name another local character held stays as
    it is, its entries mixed with that character's. Renames of removed
    characters (``{name, character_uid}``; a uid-less one by name) are left
    to their retirement. Returns the markers updated.
    """
    config_dir = Path(config_dir)
    fresh = dict(fresh_names) if isinstance(fresh_names, Mapping) else dict.fromkeys(fresh_names)
    removed = list(removed)
    gone_uids = {entry.get("character_uid") for entry in removed if entry.get("character_uid")}
    gone_names = {entry.get("name") for entry in removed if not entry.get("character_uid")}
    if not fresh or not _may_exist(config_dir / VISIT_PEERS_FILENAME):
        return 0
    updated = 0
    slot, queued = read_roster_markers_sync(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    queued = _queued_renames(queued)
    for key, marker in [(PENDING_RENAME, slot)] + [(PENDING_RENAME_QUEUE, item) for item in queued]:
        held = rename_marker_names(marker)
        if held is None:
            continue
        marker_uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
        # 被删角色的改名交给它的退役；没有 uid 的标记按名字认它是不是被删的那个
        if (marker_uid in gone_uids) if marker_uid else bool(gone_names & set(held)):
            continue
        handed = _handed_over_names(marker)
        # 导入前这个名字归本机另一个角色：条目早和它的混在一起，不能整个迁走
        adding = [name for name in held
                  if name in fresh and name not in handed and fresh[name] in (None, marker_uid)]
        if not adding:
            continue
        value = {**marker, "handed_over": sorted(handed | set(adding))}
        updated += bool(replace_roster_marker_sync(config_dir, key, marker, value))
    return updated


def names_of(characters: Any) -> tuple[set[str], dict[str, str]]:
    """``(names, {name: character_uid})`` of a loaded ``characters.json`` document."""
    from utils.config_manager import get_character_uid

    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    names: set[str] = set()
    uid_of: dict[str, str] = {}
    for name, data in (catgirls or {}).items():
        if isinstance(name, str) and name:
            names.add(name)
            uid = get_character_uid(data) if isinstance(data, dict) else None
            if uid:
                uid_of[name] = uid
    return names, uid_of

