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
  pending, a new character may not take its name (:func:`is_name_retiring`).
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
    read_roster_marker,
    read_roster_marker_sync,
    remove_roster_marker_item,
    set_roster_marker,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

PENDING_RENAME = "pending_rename"
PENDING_RETIRE = "pending_retire"

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
    dropped. An empty set means no rename is pending; ``{old, new}`` that
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
        reused = (old in uid_of and uid_of[old] != uid) or (new in uid_of and uid_of[new] != uid)
        forward = new in current and not reused
        backward = old in current and not forward and not reused
        deleted = not current
    else:
        forward = new in names and old not in names
        backward = old in names and new not in names
        deleted = old not in names and new not in names
    if forward:
        await roster.rename_char(old, new)
        await VisitSpool.rename_own_char(config_dir, old, new)
    elif backward and not undo_moves:
        # 事务自己收尾：迁移只在提交之后做，回滚时什么都没动过。不能反向改写——新名下
        # 若有已删除角色的残留数据，会被错挂到这个角色名下
        pass
    elif backward:
        # 改名没生效：把已经改写成新名的场次改回旧名
        await VisitSpool.rename_own_char(config_dir, new, old)
        await roster.rename_char(new, old)
    elif deleted:
        # 两个名字都不在：这个角色已被删除，它的名册条目与场次由删除的退役步骤按 uid
        # 处理。标记不再有可对账的对象，留着只会永远挡住清除与逐场补录
        logger.warning("visit recovery: pending_rename %r -> %r names a deleted character, dropped", old, new)
    else:
        logger.warning("visit recovery: pending_rename %r -> %r is ambiguous, kept", old, new)
        return frozenset({old, new})
    return frozenset() if await clear_roster_marker(config_dir, PENDING_RENAME, marker) else ALL_NAMES


async def begin_rename(
    config_dir: str | Path, old: str, new: str, character_uid: str | None, *,
    names: set[str], uid_of: dict[str, str] | None,
) -> dict | None:
    """Write ``pending_rename`` before the rename commits; return the marker (``None``: no visit data).

    Runs inside the rename transaction (under the character-config lock),
    with ``names`` / ``uid_of`` read from the config before the rename. An
    earlier marker is reconciled first; when it cannot be (ambiguous,
    unreadable spools) :class:`RenamePendingElsewhere` is raised and nothing
    is written. An unreadable roster raises :class:`RosterCorruptError`.
    """
    config_dir = Path(config_dir)
    if not await has_visit_data(config_dir):
        return None
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
) -> None:
    """Reconcile a pending rename of the character about to be deleted; raise when it cannot be.

    Retirement goes by the character's current name only: entries a failed
    rename migration left under its other name would outlive it, and a new
    character taking that name later would inherit them. An unsettleable
    rename (unreadable spools) raises :class:`RenamePendingElsewhere`; an
    ambiguous one (the other name was taken by another character meanwhile)
    lets the delete go ahead, nothing being separable any more.
    """
    marker = await read_roster_marker(config_dir, PENDING_RENAME)
    if not _rename_names_of(marker, name, character_uid):
        return
    try:
        names, uid_of = await load_names()
        unsettled = await reconcile_rename(config_dir, names, uid_of)
    except Exception as exc:  # noqa: BLE001 - 对不完账就拒绝这次删除，标记留给启动对账
        raise RenamePendingElsewhere("this character's rename is still being migrated") from exc
    if unsettled is ALL_NAMES:
        raise RenamePendingElsewhere("this character's rename is still being migrated")
    if unsettled:
        # 另一个名字已被别的角色占用：两边的条目分不开了，删掉这个角色不会让情况更糟；
        # 它的 uid 一消失，下次对账就按「已删除」清掉标记
        logger.warning("visit retire: ambiguous pending_rename of %r left as is, delete goes ahead", name)


async def begin_retire(
    config_dir: str | Path, name: str, character_uid: str | None, *, load_names: LoadNames | None = None,
) -> dict | None:
    """Add the ``pending_retire`` item before the delete commits; return it (``None``: no visit data).

    With ``load_names`` (the config before the delete), a pending rename of
    this very character is reconciled first; when it cannot be,
    :class:`RenamePendingElsewhere` is raised and nothing is written. An
    unreadable roster raises :class:`RosterCorruptError` (the delete is
    refused rather than leaving visit data no marker points at).
    """
    config_dir = Path(config_dir)
    if not await has_visit_data(config_dir, character_uid):
        return None
    if load_names is not None:
        await _settle_own_rename(config_dir, name, character_uid, load_names)
    item = retire_item(name, character_uid)
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
                  retire_persona: RetirePersona | None) -> None:
    failures: list[BaseException] = []
    try:
        # 头行不带 own_char_uid 的旧场次按名字认：只在没有同名新角色时
        await VisitSpool.retire_char(config_dir, uid or "", legacy_name=None if name in names else name)
    except Exception as exc:  # noqa: BLE001 - 各步互不连累，最后统一报
        failures.append(exc)
    try:
        await PeerRoster(config_dir, own_uid="pending-retire").retire_char(name)
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
    if failures:
        raise failures[0]


async def settle_retire(
    config_dir: str | Path, item: Mapping[str, Any], *, names: set[str], uid_of: Mapping[str, str] | None,
    retire_persona: RetirePersona | None,
) -> bool:
    """Finish one ``pending_retire`` item against the current config; True once the item is removed.

    Still configured (by uid, or by name for a character without one or
    when ``uid_of`` is not known): the
    delete did not commit, the item is dropped and nothing is retired.
    Otherwise every retirement step runs (idempotent) and the item is
    removed only when all of them succeeded.
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
        try:
            await _retire(config_dir, name, uid, names, retire_persona)
        except Exception as exc:  # noqa: BLE001 - 角色不回滚：标记保留，启动对账补完
            logger.warning("visit retire: visit data of a deleted character not fully retired: %r", exc)
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
            clean = await settle_retire(config_dir, item, names=names, uid_of=uid_of,
                                        retire_persona=retire_persona) and clean
    return clean


async def is_name_retiring(config_dir: str | Path, name: str) -> bool:
    """Whether a deleted character called ``name`` still has a ``pending_retire`` item.

    A new character may not take the name meanwhile: the roster keys entries
    by name, so the pending retirement would delete the new character's
    entries (or leave the old ones under it). An unreadable roster answers
    False: it blocks no character creation (the retirement cannot run
    either until the file is readable again).
    """
    try:
        marker = await read_roster_marker(config_dir, PENDING_RETIRE)
    except RosterCorruptError as exc:
        logger.warning("visit retire: roster unreadable, name %r not checked: %s", name, exc)
        return False
    return isinstance(marker, list) and any(
        (_item_fields(item) or ("", None))[0] == name for item in marker
    )


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
    callers creating characters let it pass (see :func:`is_name_retiring`).
    """
    try:
        retiring = await read_roster_marker(config_dir, PENDING_RETIRE)
        renaming = await read_roster_marker(config_dir, PENDING_RENAME)
    except RosterCorruptError as exc:
        logger.warning("visit lifecycle: roster unreadable, name %r not checked: %s", name, exc)
        return NAME_UNREADABLE
    if isinstance(retiring, list) and any((_item_fields(item) or ("", None))[0] == name for item in retiring):
        return NAME_RETIRING
    if isinstance(renaming, dict) and name in (renaming.get("old"), renaming.get("new")):
        return NAME_RENAMING
    return NAME_FREE


def _rename_names_of(marker: Any, name: str, character_uid: str | None) -> tuple[str, ...]:
    """The ``old`` / ``new`` names of ``marker`` when it is a pending rename of this character, else ``()``."""
    if not isinstance(marker, dict):
        return ()
    old, new = marker.get("old"), marker.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        return ()
    marker_uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
    involved = marker_uid == character_uid if marker_uid and character_uid else name in (old, new)
    return (old, new) if involved else ()


def record_removed_characters_sync(
    config_dir: str | Path, removed: Iterable[Mapping[str, Any]], kept_names: Iterable[str] = (),
) -> int:
    """Add a ``pending_retire`` item for each character a cloudsave import is about to remove.

    Called from the import thread right before it commits, with the
    ``{name, character_uid}`` of every local character absent from the
    snapshot and the names the snapshot keeps. Startup recovery then settles
    the items against the committed config like any delete (an import that
    rolled back only drops them). A removed character whose own rename is
    still pending gets an item for its other name too (unless the snapshot
    keeps that name): recovery drops the rename marker of a character that
    is gone, so the entries a failed migration left under that name would
    otherwise outlive it. Machines without visit data get nothing; returns
    the number of items added.
    """
    config_dir = Path(config_dir)
    kept = set(kept_names)
    try:
        rename_marker = read_roster_marker_sync(config_dir, PENDING_RENAME)
    except RosterCorruptError:
        rename_marker = None  # 名册读不出：写标记那一步同样会失败，由回调方记日志
    added = 0
    for entry in removed:
        fields = _item_fields(retire_item(entry.get("name"), entry.get("character_uid")))
        if fields is None or not _has_visit_files_sync(config_dir, fields[1]):
            continue
        name, uid = fields
        names = [name] + [
            other for other in _rename_names_of(rename_marker, name, uid) if other != name and other not in kept
        ]
        for retiring in names:
            added += bool(add_roster_marker_item_sync(config_dir, PENDING_RETIRE, retire_item(retiring, uid)))
    return added


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

