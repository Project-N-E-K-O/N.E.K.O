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

"""Local forget operations: "forget this person", "forget everyone", and their replay.

Design: ``docs/design/visit-infrastructure.md`` section 3.7.6 item 5 and
section 4.6 ``POST /api/visit/memory/forget | forget_all``.

Persist first, then execute, at two levels:

1. A clearing sentinel (:class:`ClearingSentinels`) records the scope of the
   whole operation before the roster is even read.
2. Inside that scope every pair gets its revocation log (written under the
   pair's :func:`peer_lock`, and the pair's last-visit summary is removed right
   after the log is on disk), and only once every log is written does
   execution start (:func:`run_revocation`, again under the pair's lock).

The sentinel is deleted when every log in its scope is closed. A crash at any
point leaves enough on disk for :func:`replay_forgets` (startup recovery) to
finish the same scope. Only the local visit memory is touched: transcripts
uploaded to Servers, queued reports and the blocklist stay.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncContextManager

from main_logic.visit import memory_bridge
from main_logic.visit.forget import (
    ClearingSentinels,
    ForgetEpochsUnreadable,
    ForgetStepFailed,
    RevocationLog,
    RevocationLogUnreadable,
    plan_forget_person,
    run_revocation,
    sentinel_covers,
)
from main_logic.visit.memory_commit import ResolveCharName, peer_lock
from main_logic.visit.spool import (
    SpoolBusy,
    SpoolStateError,
    SpoolStateUnreadable,
    VisitSpool,
    STATE_SUFFIX,
)
from main_logic.visit.subjects import PeerRoster, RosterCorruptError, read_roster_marker
from memory.scoped_client import ScopedMemoryClient
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

VoidPending = Callable[[dict], Awaitable[None]]
AdmissionLock = Callable[[str], AsyncContextManager[Any]]
IsVisitActive = Callable[[str], bool]
LifecycleGuard = Callable[[list[str]], AsyncContextManager[Any]]
"""``lifecycle_guard(own_char_uids)``: held for a whole clearing so the characters cannot be renamed / deleted."""


class VisitActive(RuntimeError):
    """A character named by the clearing is visiting right now (checked under its admission lock)."""


class CharacterUnresolved(ValueError):
    """A character named by a clearing has no current name (deleted, or its config unreadable)."""


class RenamePending(CharacterUnresolved):
    """A character rename has not been reconciled yet (``visit_peers.json.pending_rename``)."""


async def _refuse_pending_rename(config_dir: str | Path) -> None:
    # 改名崩在「配置已是新名、名册还在旧名」之间：此时按新名展开会找不到条目，
    # 清除会「成功」地什么都没删，之后补录又把旧名下没清的数据搬出来。等启动补录对账完
    if await read_roster_marker(config_dir, "pending_rename") is not None:
        raise RenamePending("a character rename is not reconciled yet")

# 「清除这个人」时这些还没写任何私聊记忆的 debrief 一律作废（改记「不记」）：
# 否则用户之后点「记成日记」会把刚要求清除的这个人写进私聊记忆
_VOIDABLE_CHOICES = (None, "ask_later", "generating:diary", "preview:diary")
_STEP_ERRORS = (
    ForgetStepFailed, ForgetEpochsUnreadable, SpoolBusy, SpoolStateUnreadable,
    RosterCorruptError, OSError, ValueError,
)


@dataclass
class ForgetOutcome:
    """Result of one clearing operation: ``done`` once every log closed, ``forgotten`` persons."""

    done: bool
    forgotten: int = 0
    pending_logs: list[str] = field(default_factory=list)


def default_void_pending(config_dir: str | Path) -> VoidPending:
    """Return the ``void_pending`` step used by local forgets.

    Voids the not-yet-written debriefs of the forgotten person's visits under
    the log's local character: visits still naming one of the log's pairs, and
    visits whose peer identity was already wiped (``wipe_spool`` runs first,
    and a wiped visit belongs to some forgotten person). Their choice becomes
    ``forget``. Debriefs already committing or failed are left to their own
    retry / abandon flow.
    """

    async def void(record: dict) -> None:
        own_char_uid = record["own_char_uid"]
        pairs = set(record["pair_ids"])
        for visit_id in await VisitSpool.list_visit_ids(config_dir, (STATE_SUFFIX,)):
            spool = VisitSpool(config_dir, visit_id)
            try:
                state = await spool.read_state()
            except (OSError, ValueError) as exc:
                raise SpoolStateUnreadable([visit_id]) from exc
            if state is None or state["own_char_uid"] != own_char_uid:
                continue
            if state["pair_id"] is not None and state["pair_id"] not in pairs:
                continue
            # 不看 finalized：启动补录先重放清除、后标崩溃，崩溃场次此时 finalized 仍为空，
            # 漏掉它会让补录随后照样弹芯片。清除只在该角色没有在飞串门时进行
            if state["debrief_choice"] not in _VOIDABLE_CHOICES:
                continue
            try:
                await spool.mark_forget()
            except (SpoolStateError, FileNotFoundError):
                continue

    return void


async def open_person_log(
    config_dir: str | Path,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    current: tuple[str, str] | None = None,
) -> str:
    """Write (or merge into) the revocation log of one person, then drop their last-visit summary.

    Runs under the pair's :func:`peer_lock`. The summary removal is a local
    roster write and does not need memory_server.
    """
    async with peer_lock(own_char_uid, peer_uid):
        roster = PeerRoster(config_dir, own_uid=own_uid)
        plan = await plan_forget_person(roster, peer_uid, own_char, own_char_uid, current=current)
        rev_id = await RevocationLog(config_dir, own_uid=own_uid).open_plan(plan)
        await roster.clear_last_summary(peer_uid, own_char)
    return rev_id


async def execute_log(
    config_dir: str | Path,
    rev_id: str,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
) -> bool:
    """Run (or resume) one revocation log under the pair's lock; False leaves it for replay."""
    log = RevocationLog(config_dir, own_uid=own_uid)
    roster = PeerRoster(config_dir, own_uid=own_uid)

    async def forget_subject(subject: dict) -> bool:
        return await memory_bridge.post_visit_forget(
            own_char, [subject], config_dir=config_dir, client=client,
        )

    async with peer_lock(own_char_uid, peer_uid):
        try:
            await run_revocation(
                log, rev_id, roster=roster, forget_subject=forget_subject,
                void_pending=void_pending or default_void_pending(config_dir),
                own_char=own_char,
            )
        except _STEP_ERRORS as exc:
            logger.warning("visit forget %s not finished, kept for replay: %r", rev_id, exc)
            return False
    return True


async def _open_logs_in_scope(
    config_dir: Path,
    sentinel: Mapping[str, Any],
    *,
    resolve_char_name: ResolveCharName,
) -> list[tuple[str, str, str, str]]:
    """Expand a sentinel's scope from the roster and write every log; return ``(rev_id, name, uid, peer)``."""
    own_uid = sentinel["own_uid"]
    roster = PeerRoster(config_dir, own_uid=own_uid)
    opened: list[tuple[str, str, str, str]] = []
    for own_char_uid in sentinel["own_char_uids"]:
        name = await resolve_char_name(own_char_uid)
        if not name:
            # 名字解析不出可能只是角色配置一时读不出（被替换成默认值）：当作「范围没展开」
            # 留住哨兵，不能当作「这个角色没有要清的人」把哨兵删掉、清除永远不再执行
            raise CharacterUnresolved(f"character {own_char_uid} of {sentinel['op_id']} has no name")
        if sentinel["scope"] == "person":
            peers = [sentinel["peer_uid"]]
        else:
            # 严格读：名册读不出、或某个人的条目结构坏了，都不能当作「没有这个人」，
            # 否则清除全部会漏掉他、照样删哨兵报完成
            peers = await roster.peers_of_char(name)
        for peer_uid in peers:
            rev_id = await open_person_log(
                config_dir, own_uid=own_uid, own_char=name, own_char_uid=own_char_uid,
                peer_uid=peer_uid,
            )
            opened.append((rev_id, name, own_char_uid, peer_uid))
    return opened


async def _run_scope(
    config_dir: Path,
    sentinel: Mapping[str, Any],
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None,
    void_pending: VoidPending | None,
) -> ForgetOutcome:
    opened = await _open_logs_in_scope(config_dir, sentinel, resolve_char_name=resolve_char_name)
    # 名册只列还在的人：上一次尝试已做完 remove_char、却在之后的步骤失败的人不在名册里，
    # 但他的日志还开着。把哨兵范围内已有的开着的日志一并续跑，否则重试会「全部完成」
    # 删掉哨兵，残留的转录身份或预览要等下次启动才处理
    seen = {rev_id for rev_id, *_rest in opened}
    for record in await RevocationLog.list_all_open(config_dir):
        if (
            record["id"] in seen or record["own_uid"] != sentinel["own_uid"]
            or not sentinel_covers(sentinel, record["own_char_uid"], record["peer_uid"])
        ):
            continue
        name = await resolve_char_name(record["own_char_uid"])
        if not name:
            raise CharacterUnresolved(f"character of {record['id']} has no name")
        opened.append((record["id"], name, record["own_char_uid"], record["peer_uid"]))
        seen.add(record["id"])
    # 全部日志落盘之后才开始逐对执行：中途崩溃时还没轮到的人也已有日志可重放
    pending: list[str] = []
    for rev_id, name, own_char_uid, peer_uid in opened:
        ok = await execute_log(
            config_dir, rev_id, own_uid=sentinel["own_uid"], own_char=name,
            own_char_uid=own_char_uid, peer_uid=peer_uid, client=client, void_pending=void_pending,
        )
        if not ok:
            pending.append(rev_id)
    # 「清除全部」里同一个人可能在几个本机角色下各有一份日志：按人计数，
    # 只有这个人的每份日志都完成才算清掉了
    persons = {peer for *_rest, peer in opened}
    unfinished = {peer for rev_id, *_rest, peer in opened if rev_id in pending}
    if pending:
        return ForgetOutcome(done=False, forgotten=len(persons - unfinished), pending_logs=pending)
    store = ClearingSentinels(config_dir)
    await store.remove(sentinel["op_id"])
    await _remove_covered_sentinels(config_dir, store, sentinel)
    return ForgetOutcome(done=True, forgotten=len(persons))


def _sentinel_within(outer: Mapping[str, Any], inner: Mapping[str, Any]) -> bool:
    """Whether ``inner``'s whole scope lies inside ``outer``'s (same account)."""
    if inner["own_uid"] != outer["own_uid"] or not set(inner["own_char_uids"]) <= set(outer["own_char_uids"]):
        return False
    return outer["scope"] == "chars" or (
        inner["scope"] == "person" and inner.get("peer_uid") == outer.get("peer_uid")
    )


async def _remove_covered_sentinels(
    config_dir: Path, store: ClearingSentinels, done: Mapping[str, Any],
) -> None:
    # 上一次尝试留下的、范围落在这次已全部完成的范围之内的旧哨兵（比如两次尝试之间
    # 新建了角色，哨兵的角色集合变了、没能复用）：它们的日志已随这次一并跑完，不删就会
    # 一直挡着准入与记忆装配直到下次启动。尽力而为：读不出就留给启动重放
    try:
        others = await store.list_open()
        remaining = await RevocationLog.list_all_open(config_dir)
    except RevocationLogUnreadable:
        return
    for other in others:
        if other["op_id"] == done["op_id"] or not _sentinel_within(done, other):
            continue
        if any(
            log["own_uid"] == other["own_uid"]
            and sentinel_covers(other, log["own_char_uid"], log["peer_uid"])
            for log in remaining
        ):
            continue
        await store.remove(other["op_id"])


async def _with_admission_locks(
    admission_lock: AdmissionLock | None, own_char_uids: Iterable[str],
) -> contextlib.AsyncExitStack:
    stack = contextlib.AsyncExitStack()
    if admission_lock is not None:
        try:
            for uid in sorted(set(own_char_uids)):
                await stack.enter_async_context(admission_lock(uid))
        except BaseException:
            # 拿到一半失败 / 被取消：已拿到的锁必须放掉，否则这些角色再也进不了串门
            await stack.aclose()
            raise
    return stack


def _refuse_active(is_visit_active: IsVisitActive | None, names: Iterable[str]) -> None:
    # 在准入锁内复查：锁外检查之后、哨兵落盘之前开场的串门也要挡下
    if is_visit_active is not None and any(is_visit_active(name) for name in names):
        raise VisitActive("a character in scope is visiting")


async def forget_person(
    config_dir: str | Path,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
    admission_lock: AdmissionLock | None = None,
    is_visit_active: IsVisitActive | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    resolve_char_name: ResolveCharName | None = None,
) -> ForgetOutcome:
    """"Forget this person" under one local character (``scope='person'``).

    ``admission_lock(own_char_uid)`` (optional, the visit admission lock of a
    character) is held only while the sentinel is written, so a visit admitted
    before it is visible either already exists or sees the sentinel.
    ``is_visit_active(name)`` is checked again under that lock and raises
    :class:`VisitActive` before anything is written. ``lifecycle_guard``
    (optional) is held for the whole operation, so the character cannot be
    renamed or deleted while its name is used for the roster and memory_server.
    ``resolve_char_name`` (optional) re-reads the character's current name
    once the guard is held (a rename may have landed before it was taken);
    :class:`CharacterUnresolved` when the character is gone.
    """
    async with (lifecycle_guard([own_char_uid]) if lifecycle_guard else contextlib.nullcontext()):
        await _refuse_pending_rename(config_dir)
        if resolve_char_name is not None:
            own_char = await resolve_char_name(own_char_uid)
            if not own_char:
                raise CharacterUnresolved(f"character {own_char_uid} has no name")
        return await _forget_person(
            Path(config_dir), own_uid=own_uid, own_char=own_char, own_char_uid=own_char_uid,
            peer_uid=peer_uid, client=client, void_pending=void_pending,
            admission_lock=admission_lock, is_visit_active=is_visit_active,
        )


async def _forget_person(
    config_dir: Path,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    client: ScopedMemoryClient | None,
    void_pending: VoidPending | None,
    admission_lock: AdmissionLock | None,
    is_visit_active: IsVisitActive | None,
) -> ForgetOutcome:
    stack = await _with_admission_locks(admission_lock, [own_char_uid])
    async with stack:
        _refuse_active(is_visit_active, [own_char])
        sentinel = await ClearingSentinels(config_dir).find_or_create(
            own_uid=own_uid, scope="person", own_char_uids=[own_char_uid], peer_uid=peer_uid,
        )

    async def resolve(uid: str) -> str | None:
        return own_char if uid == own_char_uid else None

    return await _run_scope(config_dir, sentinel, resolve_char_name=resolve,
                            client=client, void_pending=void_pending)


async def forget_all(
    config_dir: str | Path,
    *,
    own_uid: str,
    chars: Mapping[str, str],
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
    admission_lock: AdmissionLock | None = None,
    is_visit_active: IsVisitActive | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    resolve_char_name: ResolveCharName | None = None,
) -> ForgetOutcome:
    """"Forget everyone" under the local characters ``chars`` (``{name: character_uid}``).

    One sentinel (``scope='chars'``) names every character; the roster is
    expanded only after it is on disk, every person's log is written before
    any is executed. ``lifecycle_guard`` is held for the whole operation;
    ``resolve_char_name`` (optional) re-reads every current name under it and
    raises :class:`CharacterUnresolved` when any of them has none (deleted in
    between, or the character config unreadable: indistinguishable here, so
    the caller answers a retryable error instead of reporting success).
    """
    if not chars:
        return ForgetOutcome(done=True)
    async with (lifecycle_guard(sorted(chars.values())) if lifecycle_guard
                else contextlib.nullcontext()):
        await _refuse_pending_rename(config_dir)
        if resolve_char_name is not None:
            current: dict[str, str] = {}
            for uid in chars.values():
                name = await resolve_char_name(uid)
                if not name:
                    # 解析不出：可能刚被删，也可能角色配置一时读不出（被替换成默认值）。
                    # 两者在这里分不清，跳过它就会对一个没写下任何哨兵 / 日志的角色报「清除成功」
                    raise CharacterUnresolved(f"character {uid} has no name")
                current[name] = uid
            chars = current
        return await _forget_all(
            Path(config_dir), own_uid=own_uid, chars=chars, client=client,
            void_pending=void_pending, admission_lock=admission_lock,
            is_visit_active=is_visit_active,
        )


async def _forget_all(
    config_dir: Path,
    *,
    own_uid: str,
    chars: Mapping[str, str],
    client: ScopedMemoryClient | None,
    void_pending: VoidPending | None,
    admission_lock: AdmissionLock | None,
    is_visit_active: IsVisitActive | None,
) -> ForgetOutcome:
    by_uid = {uid: name for name, uid in chars.items()}
    stack = await _with_admission_locks(admission_lock, by_uid)
    async with stack:
        _refuse_active(is_visit_active, chars)
        sentinel = await ClearingSentinels(config_dir).find_or_create(
            own_uid=own_uid, scope="chars", own_char_uids=list(by_uid),
        )

    async def resolve(uid: str) -> str | None:
        return by_uid.get(uid)

    return await _run_scope(config_dir, sentinel, resolve_char_name=resolve,
                            client=client, void_pending=void_pending)


async def replay_forgets(
    config_dir: str | Path,
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
) -> bool:
    """Finish every unfinished clearing operation (startup recovery); True when nothing is left.

    ``lifecycle_guard`` (optional, the one the clearing endpoints use) is held
    around each sentinel expansion and each log replay, from resolving the
    character name by uid until the replay ends.

    Leftover sentinels are re-expanded within their own scope first (covers a
    crash before their logs were written), then every open revocation log of
    every account is resumed from its ``done_steps``, and finally each
    sentinel whose scope has no open log left is deleted. Unreadable logs or
    sentinels stay (fail closed) and are reported as unfinished.
    """
    config_dir = Path(config_dir)
    sentinels_store = ClearingSentinels(config_dir)
    try:
        sentinels = await sentinels_store.list_open()
    except RevocationLogUnreadable as exc:
        logger.error("visit forget replay: unreadable clearing sentinels %s", exc.ids)
        sentinels = []
        clean = False
    else:
        clean = True
    unexpanded: set[str] = set()
    def guarded(uids: list[str]):
        return lifecycle_guard(sorted(uids)) if lifecycle_guard else contextlib.nullcontext()

    for sentinel in sentinels:
        try:
            async with guarded(sentinel["own_char_uids"]):
                # 补录开头对过一次改名，但之后、拿到守卫之前可能又有改名崩在半路：守卫内再查
                await _refuse_pending_rename(config_dir)
                await _open_logs_in_scope(config_dir, sentinel, resolve_char_name=resolve_char_name)
        except _STEP_ERRORS as exc:
            logger.warning("visit forget replay: cannot expand %s: %r", sentinel["op_id"], exc)
            # 范围没展开成功（名册读不出等）：哨兵是这次清除唯一的记录，必须留到下次
            unexpanded.add(sentinel["op_id"])
            clean = False
    try:
        logs = await RevocationLog.list_all_open(config_dir)
    except RevocationLogUnreadable as exc:
        logger.error("visit forget replay: unreadable revocation logs %s", exc.ids)
        return False
    for record in logs:
        # 与端点同一把生命周期守卫：从按 uid 解析名字到重放结束都持有，期间角色
        # 改不了名、删不掉，不会把清除发到已经迁走的旧名字上又把日志当完成关掉
        async with guarded([record["own_char_uid"]]):
            try:
                await _refuse_pending_rename(config_dir)
            except (RenamePending, RosterCorruptError) as exc:
                # 改名没对账完：按新名重放会对着空条目把日志关掉，留到下次启动
                logger.warning("visit forget replay: %s deferred: %r", record["id"], exc)
                clean = False
                continue
            name = await resolve_char_name(record["own_char_uid"])
            if not name:
                # 角色已删：它的数据由删除事务的退役步骤处理，这份日志留给退役对账
                logger.warning("visit forget replay: character of %s no longer exists", record["id"])
                clean = False
                continue
            ok = await execute_log(
                config_dir, record["id"], own_uid=record["own_uid"], own_char=name,
                own_char_uid=record["own_char_uid"], peer_uid=record["peer_uid"],
                client=client, void_pending=void_pending,
            )
        clean = clean and ok
    for sentinel in sentinels:
        if sentinel["op_id"] in unexpanded:
            clean = False
            continue
        # 最后的复查与删除也在该哨兵的守卫里：否则端点在这之间复用同一个哨兵、
        # 开始展开新日志时，这里按旧快照把哨兵删掉，准入就看不见「正在清除」
        async with guarded(sentinel["own_char_uids"]):
            try:
                remaining = await RevocationLog.list_all_open(config_dir)
            except RevocationLogUnreadable:
                return False
            busy = any(
                log["own_uid"] == sentinel["own_uid"]
                and sentinel_covers(sentinel, log["own_char_uid"], log["peer_uid"])
                for log in remaining
            )
            if busy:
                clean = False
            else:
                await sentinels_store.remove(sentinel["op_id"])
    return clean
