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

"""Local "forget this person": revocation logs, plans and a replayable executor.

Design: ``docs/design/visit-infrastructure.md`` OD-09 v3, section 3.7.6 item 5
and PR-06 ``forget.py``. There is no peer consent mechanism; forgetting is
always initiated on this machine and only touches this machine.

Persist first, then execute. A revocation log
``config_dir/visit_revocations/<id>.json`` is written atomically before any
step runs, with ``id = sha256(own_uid + '|' + peer_uid + '|' +
own_char_uid).hexdigest()[:32]``. The log also records ``own_uid``: the roster
is partitioned by community account, so two accounts forgetting the same
person under the same local character keep separate logs and never merge
subjects or ``remove_char`` progress. Repeating a forget for the same triple
reuses the same log.

Step order (each step idempotent, recorded in ``done_steps`` when done)::

    clear_last_summary          local roster write, no memory_server needed
    forget:<kind>:<subject_id>  one /scoped_forget per subject
    remove_char                 only after every forget step is done
    wipe_spool                  null peer fields in related spool state/headers
    void_pending                drop staged pending work (callback, PR-08)

The executor stops at the first failing step and leaves the log on disk;
startup replay (or a retry) resumes from ``done_steps``. Callers must hold
``peer_lock(own_char_uid, peer_uid)`` around :meth:`RevocationLog.open` and the
execution; the file-level read-modify-write is additionally serialized here.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.visit_settings import VISIT_MEMORY_PLATFORM, VISIT_REVOCATIONS_DIRNAME
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import (
    PeerRoster,
    derive_pair_id,
    derive_person_id,
    group_chat_subject,
    group_participant_subject,
    participant_subject,
    path_lock,
)
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.visit_wire import REVOCATION_ID_RE, revocation_path

logger = get_module_logger(__name__, "Main")

STEP_CLEAR_LAST_SUMMARY = "clear_last_summary"
STEP_REMOVE_CHAR = "remove_char"
STEP_WIPE_SPOOL = "wipe_spool"
STEP_VOID_PENDING = "void_pending"
_FORGET_PREFIX = "forget:"
_TRAILING_STEPS = (STEP_REMOVE_CHAR, STEP_WIPE_SPOOL, STEP_VOID_PENDING)
_LOG_VERSION = 1


def revocation_id(own_uid: str, peer_uid: str, own_char_uid: str) -> str:
    """Return ``sha256(own_uid + '|' + peer_uid + '|' + own_char_uid).hexdigest()[:32]``."""
    for name, value in (("own_uid", own_uid), ("peer_uid", peer_uid),
                        ("own_char_uid", own_char_uid)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
    raw = f"{own_uid}|{peer_uid}|{own_char_uid}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def forget_step_id(subject: Mapping[str, str]) -> str:
    """Return the step id of one subject's forget: ``forget:<subject_kind>:<subject_id>``."""
    return f"{_FORGET_PREFIX}{subject['subject_kind']}:{subject['subject_id']}"


def build_steps(subjects: Iterable[Mapping[str, str]]) -> list[str]:
    """Return the ordered step ids for ``subjects`` (see the module docstring)."""
    steps = [STEP_CLEAR_LAST_SUMMARY]
    for subject in subjects:
        step = forget_step_id(subject)
        if step not in steps:
            steps.append(step)
    steps.extend(_TRAILING_STEPS)
    return steps


def _clean_subject(subject: Mapping[str, Any]) -> dict[str, str]:
    kind = subject.get("subject_kind")
    sid = subject.get("subject_id")
    if not isinstance(kind, str) or not kind or not isinstance(sid, str) or not sid:
        raise ValueError("subject must carry subject_kind and subject_id strings")
    return {"subject_kind": kind, "subject_id": sid}


def _union(existing: list, extra: Iterable, key=lambda x: x) -> tuple[list, bool]:
    out = list(existing)
    seen = {key(x) for x in out}
    added = False
    for item in extra:
        k = key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
            added = True
    return out, added


def _subject_key(subject: Mapping[str, str]) -> tuple[str, str]:
    return (subject["subject_kind"], subject["subject_id"])


@dataclass(frozen=True)
class ForgetPlan:
    """Everything one "forget this person" needs, expanded from the roster.

    ``subjects`` covers every visit subject of this person under ``own_char``
    (every pair, every peer character ever seen, the person-level subject,
    plus the in-flight visit's pair when given). ``steps`` is the ordered
    step list the revocation log will carry.
    """

    own_uid: str
    peer_uid: str
    own_char: str
    own_char_uid: str
    pair_ids: tuple[str, ...]
    subjects: tuple[dict, ...]
    steps: tuple[str, ...]

    @property
    def revocation_id(self) -> str:
        """The revocation log id this plan maps to."""
        return revocation_id(self.own_uid, self.peer_uid, self.own_char_uid)


async def plan_forget_person(
    roster: PeerRoster,
    peer_uid: str,
    own_char: str,
    own_char_uid: str,
    *,
    current: tuple[str, str] | None = None,
) -> ForgetPlan:
    """Expand the forget plan of one person under one local character.

    ``roster`` is the active account's :class:`PeerRoster` (it carries
    ``own_uid``). ``current`` is the in-flight visit's ``(pair_id,
    peer_char_id)``, merged in even when it has not reached the roster.
    Only ``by_char[own_char]`` contributes; other local characters' entries
    with the same person stay untouched.
    """
    subjects = await roster.expand_subjects(peer_uid, own_char, current)
    entry = await roster.get_char_entry(peer_uid, own_char, strict=True) or {}
    pair_ids = [p for p in entry.get("pairs") or [] if isinstance(p, str) and p]
    if current is not None and current[0] and current[0] not in pair_ids:
        pair_ids.append(current[0])
    return ForgetPlan(
        own_uid=roster.own_uid,
        peer_uid=peer_uid,
        own_char=own_char,
        own_char_uid=own_char_uid,
        pair_ids=tuple(pair_ids),
        subjects=tuple(subjects),
        steps=tuple(build_steps(subjects)),
    )


def _validate_record(record: Any, rev_id: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError("revocation log is not an object")
    for name in ("own_uid", "peer_uid", "own_char_uid"):
        if not isinstance(record.get(name), str) or not record[name]:
            raise ValueError(f"revocation log {name} missing")
    if record.get("id") != rev_id or revocation_id(
        record["own_uid"], record["peer_uid"], record["own_char_uid"]
    ) != rev_id:
        raise ValueError("revocation log id does not match its identity fields")
    for name in ("pair_ids", "subjects", "steps", "done_steps"):
        if not isinstance(record.get(name), list):
            raise ValueError(f"revocation log {name} must be a list")
    # 步骤计划必须能从 subjects 原样推出：损坏成 steps:[] 的日志若被放行，重放会
    # 什么都不清就把日志删掉，清除请求就此丢失
    subjects = [_clean_subject(s) if isinstance(s, Mapping) else _bad_subject() for s in record["subjects"]]
    # subject 只许 subject_kind / subject_id 两个键：多出的 scope 会被原样转发给
    # /scoped_forget，换掉删除的作用域，清的不是本该清的那一份
    if any(set(s) != {"subject_kind", "subject_id"} for s in record["subjects"]):
        raise ValueError("revocation log subjects carry unexpected fields")
    if record["steps"] != build_steps(subjects):
        raise ValueError("revocation log steps do not match its subjects")
    # 执行器严格按序记完成、合并只撤掉尾部标记，所以 done_steps 必是 steps 的无重复前缀；
    # 只查成员关系会放过 ["wipe_spool"] 之类不可能的进度，重放时跳过真正没做的步骤
    done = record["done_steps"]
    if done != record["steps"][: len(done)]:
        raise ValueError("revocation log done_steps are not a prefix of its steps")
    # pair_ids 驱动 wipe_spool 找场次：必须是非空字符串，且覆盖每个按 pair 存的 subject，
    # 否则 pair_ids:[] 之类的日志会清完记忆却漏抹 spool 里的对端字段
    pair_ids = record["pair_ids"]
    if not all(isinstance(p, str) and p for p in pair_ids):
        raise ValueError("revocation log pair_ids must be non-empty strings")
    # 撤销日志只清串门记忆：subject 必须在 neko_visit 平台下，否则 qq:<pair> 之类的
    # 会被转发给 /scoped_forget，按平台前缀删掉别处的记忆
    if any(s["subject_id"].split(":", 1)[0] != VISIT_MEMORY_PLATFORM for s in subjects):
        raise ValueError("revocation log subjects must belong to the visit platform")
    # 每个 subject 都必须是 MemorySubject 会产出的规范形态：未知 kind、空分量
    # （neko_visit::）之类在 memory_server 那边每次重放都 422，清除就永远关不掉
    subject_pairs = set()
    for subject in subjects:
        kind = subject["subject_kind"]
        parts = subject["subject_id"].split(":")
        if kind == "group_chat" and len(parts) == 2:
            canonical = group_chat_subject(parts[1])
        elif kind == "group_participant" and len(parts) == 3:
            canonical = group_participant_subject(parts[1], parts[2])
        elif kind == "participant":
            canonical = subject            # 下面另外要求等于这个人的人级主体
        else:
            raise ValueError(f"revocation log subject {kind!r} is not a visit subject")
        if subject != canonical:
            raise ValueError("revocation log subject is not in canonical form")
        if kind != "participant":
            subject_pairs.add(parts[1])
    # 双向相等：多出的无关 pair 会让 wipe_spool 去抹别人的场次
    if set(pair_ids) != subject_pairs or len(pair_ids) != len(set(pair_ids)):
        raise ValueError("revocation log pair_ids do not match its pair subjects")
    # 再绑定到日志自己的身份：pair 只能是 (own_uid, peer_uid) 那一对，人级主体只能是
    # 这个人。否则把 pair 在两处一起换掉的日志也能自洽通过、去清别人的记忆
    own_pair = derive_pair_id(record["own_uid"], record["peer_uid"])
    if subject_pairs - {own_pair}:
        raise ValueError("revocation log pairs do not belong to its own / peer uid")
    own_person = participant_subject(derive_person_id(record["own_uid"], record["peer_uid"]))
    # 每份合法计划都含这个人的人级主体：缺了它的日志即便内部自洽（subjects:[]），
    # 重放也只会删名册、不清任何记忆
    if own_person not in subjects:
        raise ValueError("revocation log lacks the person subject of its peer")
    # 每个记录在案的 pair 都必须带它的群主体：只丢了 group_chat 的日志会在重放后
    # 把这一对的群记忆永久留下
    for pair in pair_ids:
        if group_chat_subject(pair) not in subjects:
            raise ValueError("revocation log lacks the group subject of a recorded pair")
        # 名册里每个 pair 至少关联一只对方猫娘（upsert 必带 peer_char_id），所以每个 pair
        # 都必须有 group_participant；一只都没有的日志会把对方猫娘的记忆留下
        prefix = group_chat_subject(pair)["subject_id"] + ":"
        if not any(s["subject_kind"] == "group_participant" and s["subject_id"].startswith(prefix)
                   for s in subjects):
            raise ValueError("revocation log lacks the participant subjects of a recorded pair")
    for subject in subjects:
        if subject["subject_kind"] == "participant" and subject != own_person:
            raise ValueError("revocation log participant does not belong to its peer")
    return record


def _bad_subject() -> dict:
    raise ValueError("revocation log subject must be an object")


class RevocationLog:
    """Revocation logs of one community account under ``config_dir/visit_revocations/``.

    Log document::

        {v, id, own_uid, peer_uid, own_char_uid, own_char, pair_ids, subjects,
         steps, done_steps, requested_at, updated_at}

    ``own_uid`` is a constructor argument (dual of :class:`PeerRoster`): the
    design's ``open(peer_uid, own_char_uid, pair_ids, subjects)`` does not name
    the account, but the id and the log both depend on it.
    """

    def __init__(self, config_dir: str | Path, *, own_uid: str) -> None:
        if not isinstance(own_uid, str) or not own_uid:
            raise ValueError("own_uid must be a non-empty string")
        self.config_dir = Path(config_dir)
        self.own_uid = own_uid
        self.dir = self.config_dir / VISIT_REVOCATIONS_DIRNAME

    def path_for(self, rev_id: str) -> Path:
        """Return the log path of ``rev_id`` (format-checked, confined to the directory)."""
        return revocation_path(self.dir, rev_id)

    # ── 工作线程内 ──

    def _load_sync(self, rev_id: str) -> dict | None:
        path = self.path_for(rev_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        return _validate_record(data, rev_id)

    def _open_sync(
        self,
        peer_uid: str,
        own_char_uid: str,
        pair_ids: list[str],
        subjects: list[dict],
        own_char: str | None,
        now: float,
    ) -> str:
        rev_id = revocation_id(self.own_uid, peer_uid, own_char_uid)
        path = self.path_for(rev_id)
        with path_lock(path):
            record = self._load_sync(rev_id)
            if record is None:
                record = {
                    "v": _LOG_VERSION,
                    "id": rev_id,
                    "own_uid": self.own_uid,
                    "peer_uid": peer_uid,
                    "own_char_uid": own_char_uid,
                    "own_char": own_char,
                    "pair_ids": list(dict.fromkeys(pair_ids)),
                    "subjects": [],
                    "steps": [],
                    "done_steps": [],
                    "requested_at": now,
                    "updated_at": now,
                }
                record["subjects"], _ = _union([], subjects, _subject_key)
                record["steps"] = build_steps(record["subjects"])
                # 写盘前按读取时的同一套规则校验：写进去就读不出的日志会卡住清除，
                # 还会让 list_all_open 全局 fail closed、挡住之后所有串门
                _validate_record(record, rev_id)
                atomic_write_json(path, record)
                return rev_id
            # 同 id 未完成日志：合并新展开的 pair / subject，done_steps 原样保留。
            merged_pairs, pairs_added = _union(record["pair_ids"], pair_ids)
            merged_subjects, subjects_added = _union(
                [_clean_subject(s) for s in record["subjects"]], subjects, _subject_key
            )
            if not pairs_added and not subjects_added and (
                own_char is None or own_char == record.get("own_char")
            ):
                return rev_id
            record["pair_ids"] = merged_pairs
            record["subjects"] = merged_subjects
            record["steps"] = build_steps(merged_subjects)
            done = [s for s in record["done_steps"] if s in record["steps"]]
            if pairs_added or subjects_added:
                # 新加入的 forget 未做：remove_char 及其后的本地步骤必须重新排在它们之后，
                # 名册里新出现的条目 / 新 pair 的 spool 也要重新清一遍（各步都幂等）。
                done = [s for s in done if s not in _TRAILING_STEPS]
            record["done_steps"] = done
            if own_char is not None:
                record["own_char"] = own_char
            record["updated_at"] = now
            _validate_record(record, rev_id)
            atomic_write_json(path, record)
            return rev_id

    def _mark_done_sync(self, rev_id: str, step: str, now: float) -> None:
        path = self.path_for(rev_id)
        with path_lock(path):
            record = self._load_sync(rev_id)
            if record is None:
                raise FileNotFoundError(str(path))
            if step not in record["steps"]:
                raise ValueError(f"unknown revocation step {step!r}")
            if step in record["done_steps"]:
                return
            record["done_steps"].append(step)
            record["updated_at"] = now
            atomic_write_json(path, record)

    def _close_sync(self, rev_id: str) -> bool:
        path = self.path_for(rev_id)
        with path_lock(path):
            # 日志是唯一的重放依据：还有步骤没做完（或读不出来）就不能删
            record = self._load_sync(rev_id)
            if record is None:
                return False
            if record["done_steps"] != record["steps"]:
                raise ValueError("revocation log still has pending steps")
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

    @staticmethod
    def _list_dir_sync(directory: Path, own_uid: str | None) -> list[dict]:
        out = []
        unreadable: list[str] = []
        try:
            names = sorted(os.listdir(directory))
        except FileNotFoundError:
            return out
        for name in names:
            if not name.endswith(".json"):
                continue
            rev_id = name[: -len(".json")]
            if not REVOCATION_ID_RE.fullmatch(rev_id):
                continue
            try:
                with open(directory / name, "r", encoding="utf-8") as f:
                    record = _validate_record(json.load(f), rev_id)
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                logger.error("visit revocation log %s unreadable, cannot replay: %s", name, exc)
                unreadable.append(rev_id)
                continue
            if own_uid is None or record["own_uid"] == own_uid:
                out.append(record)
        if unreadable:
            # 读不出来的日志不能当作「没有未完成的清除」：补录与建房闸都靠这份列表，
            # 跳过它等于放任新记忆写进正在清除的范围。属于哪个账号也读不出，一律上抛
            raise RevocationLogUnreadable(unreadable)
        return out

    # ── 公开 API ──

    async def open(
        self,
        peer_uid: str,
        own_char_uid: str,
        pair_ids: Iterable[str],
        subjects: Iterable[Mapping[str, str]],
        *,
        own_char: str | None = None,
        now: float | None = None,
    ) -> str:
        """Persist (or merge into) the revocation log of one person; return its id.

        An unfinished log with the same id is never overwritten: new pair ids
        and subjects are merged in, ``done_steps`` is kept, and because new
        forget steps were added, ``remove_char`` and the later local steps are
        re-armed so they still run after every forget. Callers hold
        ``peer_lock(own_char_uid, peer_uid)``.
        """
        if not isinstance(peer_uid, str) or not peer_uid:
            raise ValueError("peer_uid must be a non-empty string")
        clean_subjects = [_clean_subject(s) for s in subjects]
        clean_pairs = [p for p in pair_ids if isinstance(p, str) and p]
        return await asyncio.to_thread(
            self._open_sync,
            peer_uid,
            own_char_uid,
            clean_pairs,
            clean_subjects,
            own_char,
            time.time() if now is None else now,
        )

    async def open_plan(self, plan: ForgetPlan, *, now: float | None = None) -> str:
        """Open the log for a :class:`ForgetPlan` built for this account."""
        if plan.own_uid != self.own_uid:
            raise ValueError("forget plan belongs to another community account")
        return await self.open(
            plan.peer_uid, plan.own_char_uid, plan.pair_ids, plan.subjects,
            own_char=plan.own_char, now=now,
        )

    async def load(self, rev_id: str) -> dict | None:
        """Return the log document, or ``None`` once it is closed."""
        record = await asyncio.to_thread(self._load_sync, rev_id)
        return copy.deepcopy(record)

    async def mark_done(self, rev_id: str, step: str, *, now: float | None = None) -> None:
        """Record ``step`` as done (idempotent)."""
        await asyncio.to_thread(
            self._mark_done_sync, rev_id, step, time.time() if now is None else now
        )

    async def close(self, rev_id: str) -> bool:
        """Delete the log once every step is done; return whether a file was removed.

        Raises ``ValueError`` when a step is still pending (or the log cannot
        be read): the log is the only replay record, so it is kept.
        """
        return await asyncio.to_thread(self._close_sync, rev_id)

    async def list_open(self) -> list[dict]:
        """Return this account's unfinished logs.

        Raises :class:`RevocationLogUnreadable` when any log file exists but
        cannot be read (fail closed).
        """
        return await asyncio.to_thread(self._list_dir_sync, self.dir, self.own_uid)

    @classmethod
    async def list_all_open(cls, config_dir: str | Path) -> list[dict]:
        """Return every unfinished log of every account (startup replay, admission gates).

        Raises :class:`RevocationLogUnreadable` when any log file exists but
        cannot be read (fail closed).
        """
        directory = Path(config_dir) / VISIT_REVOCATIONS_DIRNAME
        return await asyncio.to_thread(cls._list_dir_sync, directory, None)


ForgetSubject = Callable[[dict], Awaitable[bool]]


class RevocationLogUnreadable(RuntimeError):
    """A revocation log exists but cannot be read; callers must fail closed.

    Raised by the listing methods (startup replay and the admission gates
    read them), so an unreadable pending erase blocks new visits instead of
    silently disappearing. ``ids`` names the affected log ids.
    """

    def __init__(self, ids: list[str]) -> None:
        self.ids = list(ids)
        super().__init__(f"unreadable visit revocation logs: {', '.join(self.ids)}")


class ForgetStepFailed(RuntimeError):
    """A ``forget_subject`` callback did not confirm the erase (returned anything but ``True``)."""
VoidPending = Callable[[dict], Awaitable[None]]


async def run_revocation(
    log: RevocationLog,
    rev_id: str,
    *,
    roster: PeerRoster,
    forget_subject: ForgetSubject,
    void_pending: VoidPending,
    own_char: str | None = None,
) -> bool:
    """Execute (or resume) one revocation log step by step.

    Steps already in ``done_steps`` are skipped. Each step is recorded as
    soon as it succeeds; the first exception propagates and leaves the log
    on disk, so a later call resumes exactly there. ``remove_char`` therefore
    never runs before every forget step is recorded. ``forget_subject``
    receives one ``{subject_kind, subject_id}`` dict (the memory_server
    ``/scoped_forget`` call, injected, e.g. ``ScopedMemoryClient.post_forget``)
    and must return ``True`` once the erase is confirmed; any other return
    value raises :class:`ForgetStepFailed` before the step is recorded, so a
    failed erase is never marked done. ``void_pending`` (required) drops
    the staged pending work of this person (diary facts, previews, ...); a
    caller with nothing staged passes a no-op explicitly, so that step is
    never recorded as done without the cleanup having run. ``own_char`` is the character's
    current name (resolved from ``own_char_uid`` by the caller); it defaults
    to the name stored in the log. When every step is done the log is
    closed and ``True`` is returned; ``False`` means the log was already gone.
    """
    if roster.own_uid != log.own_uid:
        raise ValueError("roster and revocation log belong to different accounts")
    record = await log.load(rev_id)
    if record is None:
        return False
    if record["own_uid"] != log.own_uid:
        raise ValueError("revocation log belongs to another community account")
    peer_uid = record["peer_uid"]
    char_name = own_char or record.get("own_char")
    if not isinstance(char_name, str) or not char_name:
        raise ValueError("the local character name of this revocation is unknown")
    if STEP_REMOVE_CHAR not in record["done_steps"]:
        # 名册条目还在时，以它为准对账：日志里丢了某只对方猫娘的 group_participant
        # （或某个 pair）也能自洽通过校验，重放会清掉其余主体、删名册、关日志，
        # 漏掉的那份记忆就再也没有重放入口。名册展开得到而日志缺的部分先并回日志
        # （合并会把 remove_char 及其后的步骤重新排到新 forget 之后）
        plan = await plan_forget_person(roster, peer_uid, char_name, record["own_char_uid"])
        known = {_subject_key(_clean_subject(s)) for s in record["subjects"]}
        if any(_subject_key(s) not in known for s in plan.subjects) or (
            set(plan.pair_ids) - set(record["pair_ids"])
        ):
            await log.open_plan(plan)
            record = await log.load(rev_id)
            if record is None:
                return False
    subjects = {forget_step_id(s): s for s in record["subjects"]}
    done = set(record["done_steps"])
    for step in record["steps"]:
        if step in done:
            continue
        if step == STEP_CLEAR_LAST_SUMMARY:
            await roster.clear_last_summary(peer_uid, char_name)
        elif step.startswith(_FORGET_PREFIX):
            subject = subjects.get(step)
            if subject is None:
                raise ValueError(f"revocation step {step!r} has no subject")
            # 只认明确的 True：post_forget 失败返回 False，不检查就会记完成、
            # 随后删名册与日志，残留记忆再也没有重放入口
            if await forget_subject(dict(subject)) is not True:
                raise ForgetStepFailed(f"scoped_forget not confirmed for {step!r}")
        elif step == STEP_REMOVE_CHAR:
            await roster.remove_char(peer_uid, char_name)
        elif step == STEP_WIPE_SPOOL:
            visit_ids = await VisitSpool.find_visits_for_pairs(
                log.config_dir, record["own_char_uid"], record["pair_ids"]
            )
            for visit_id in visit_ids:
                await VisitSpool(log.config_dir, visit_id).delete_peer_fields()
        elif step == STEP_VOID_PENDING:
            # 必填：缺省时静默记完成会让暂存的日记事实 / 预览在清除后照样被提交
            await void_pending(copy.deepcopy(record))
        else:
            raise ValueError(f"unknown revocation step {step!r}")
        await log.mark_done(rev_id, step)
    await log.close(rev_id)
    return True
