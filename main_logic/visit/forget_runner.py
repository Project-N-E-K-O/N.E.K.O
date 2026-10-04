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
from main_logic.visit.subjects import PeerRoster, RosterCorruptError
from memory.scoped_client import ScopedMemoryClient
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

VoidPending = Callable[[dict], Awaitable[None]]
AdmissionLock = Callable[[str], AsyncContextManager[Any]]

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
            if state["finalized"] is None or state["debrief_choice"] not in _VOIDABLE_CHOICES:
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
            continue
        if sentinel["scope"] == "person":
            peers = [sentinel["peer_uid"]]
        else:
            peers = [
                peer_uid for peer_uid, peer in (await roster.list_peers()).items()
                if isinstance(peer.get("by_char"), dict) and name in peer["by_char"]
            ]
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
    # 全部日志落盘之后才开始逐对执行：中途崩溃时还没轮到的人也已有日志可重放
    pending: list[str] = []
    for rev_id, name, own_char_uid, peer_uid in opened:
        ok = await execute_log(
            config_dir, rev_id, own_uid=sentinel["own_uid"], own_char=name,
            own_char_uid=own_char_uid, peer_uid=peer_uid, client=client, void_pending=void_pending,
        )
        if not ok:
            pending.append(rev_id)
    persons = len({peer for *_rest, peer in opened})
    if pending:
        return ForgetOutcome(done=False, forgotten=persons - len(pending), pending_logs=pending)
    await ClearingSentinels(config_dir).remove(sentinel["op_id"])
    return ForgetOutcome(done=True, forgotten=persons)


async def _with_admission_locks(
    admission_lock: AdmissionLock | None, own_char_uids: Iterable[str],
) -> contextlib.AsyncExitStack:
    stack = contextlib.AsyncExitStack()
    if admission_lock is not None:
        for uid in sorted(set(own_char_uids)):
            await stack.enter_async_context(admission_lock(uid))
    return stack


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
) -> ForgetOutcome:
    """"Forget this person" under one local character (``scope='person'``).

    ``admission_lock(own_char_uid)`` (optional, the visit admission lock of a
    character) is held only while the sentinel is written, so a visit admitted
    before it is visible either already exists or sees the sentinel.
    """
    config_dir = Path(config_dir)
    stack = await _with_admission_locks(admission_lock, [own_char_uid])
    async with stack:
        sentinel = await ClearingSentinels(config_dir).create(
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
) -> ForgetOutcome:
    """"Forget everyone" under the local characters ``chars`` (``{name: character_uid}``).

    One sentinel (``scope='chars'``) names every character; the roster is
    expanded only after it is on disk, every person's log is written before
    any is executed.
    """
    config_dir = Path(config_dir)
    if not chars:
        return ForgetOutcome(done=True)
    by_uid = {uid: name for name, uid in chars.items()}
    stack = await _with_admission_locks(admission_lock, by_uid)
    async with stack:
        sentinel = await ClearingSentinels(config_dir).create(
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
) -> bool:
    """Finish every unfinished clearing operation (startup recovery); True when nothing is left.

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
    for sentinel in sentinels:
        try:
            await _open_logs_in_scope(config_dir, sentinel, resolve_char_name=resolve_char_name)
        except _STEP_ERRORS as exc:
            logger.warning("visit forget replay: cannot expand %s: %r", sentinel["op_id"], exc)
            clean = False
    try:
        logs = await RevocationLog.list_all_open(config_dir)
    except RevocationLogUnreadable as exc:
        logger.error("visit forget replay: unreadable revocation logs %s", exc.ids)
        return False
    for record in logs:
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
    try:
        remaining = await RevocationLog.list_all_open(config_dir)
    except RevocationLogUnreadable:
        return False
    for sentinel in sentinels:
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
