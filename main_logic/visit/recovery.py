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

"""Startup recovery of visit files (background task, never on the startup path).

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 item 7 and
PR-08 ``recovery.py``. :func:`visit_spool_recovery` is started with
``asyncio.create_task`` after startup (PR-09b) and runs, in order:

1. the character-rename reconciliation (``visit_peers.json.pending_rename``),
   first, so forgets resolve roster entries under their current name;
2. unfinished local forgets (clearing sentinels, then revocation logs);
3. startup cleanup: only leftover ``.outbox.jsonl`` files are deleted;
4. ``VisitSpool.sweep`` (7 days / 20 MB, pending uploads and unsettled
   visits are never reclaimed for size);
5. every visit's ``state.json``: a crashed visit (``finalized`` empty, not
   live) is marked ``crash``; a visit with digestable lines whose debrief
   is still open gets ``debrief_chip_pending`` (the chips are replayed on the
   next ``visit_bind``; nothing is written to private memory here); the
   visit-region digest and the last-visit summary are completed;
6. pending transcript uploads: every ``.upload.json`` is retried; a
   ``.upload.jsonl`` stream without one (a crash anywhere before finalize
   wrote it) is turned into one first, whatever ``finalized`` says;
7. queued reports in ``visit_reports/``, after their visit's upload.

Unreachable memory_server or Servers only leave files for the next start.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.visit_settings import VISIT_REPORTS_DIRNAME, VISIT_SPOOL_DIRNAME
from main_logic.visit import local_chars, memory_bridge
from main_logic.visit.forget_runner import LifecycleGuard, VoidPending, replay_forgets
from main_logic.visit.memory_commit import (
    ResolveCharName,
    SummaryLLM,
    commit_last_summary,
    commit_visit_region,
    line_order_key,
)
from main_logic.visit.spool import (
    OUTBOX_SUFFIX,
    SPOOL_SUFFIX,
    STATE_SUFFIX,
    UPLOAD_JSON_SUFFIX,
    UPLOAD_JSONL_SUFFIX,
    VisitSpool,
    is_digestable,
    is_spool_open,
    _read_header_strict,
)
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    clear_roster_marker,
    read_roster_marker,
)
from memory.scoped_client import ScopedMemoryClient
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE, visit_path

logger = get_module_logger(__name__, "Main")

UPLOAD_DOC_VERSION = 1
_USAGE_KEYS = ("llm_input_tokens", "llm_output_tokens", "tts_requests", "tts_chars")
_LINE_FIELDS = ("lp", "side", "from", "ts", "text", "truncated")

RenderChips = Callable[..., Awaitable[bool]]
"""``render_chips(visit_id, *, own_char, status) -> bool``: try to show the debrief block now.

``status`` is ``'interrupted'`` for a crashed visit, else ``None``. The
return value is only whether it reached a connected display; the pending
flag in ``state.json`` stays until the user decides either way."""

UploadTranscript = Callable[[str, dict], Awaitable[bool]]
"""``upload_transcript(visit_id, upload_doc) -> bool``: upload one pending transcript.

``upload_doc`` is the ``.upload.json`` document (``{v, own_visit_uid,
own_char_uid, transport, request}``; ``request`` is the Servers wire body).
Returns True when the local file may go (accepted, duplicate or a terminal
rejection); False keeps it for a later retry (network error, 429, account
mismatch: uploads happen only while the signed-in account is
``own_visit_uid``). The callback may rewrite the file with its chunk
progress."""

SubmitReport = Callable[[str, dict], Awaitable[bool]]
"""``submit_report(visit_id, report_doc) -> bool``: True once Servers accepted the queued report."""

SpawnBackground = Callable[[str, Callable[[], Awaitable[Any]]], Any]
"""``spawn_background(own_char_uid, factory)``: run ``factory()`` as the character's visit background task."""

ResumeDiaryCommit = Callable[[VisitSpool, dict], Awaitable[Any]]
"""``resume_diary_commit(spool, state)``: continue a ``committing:diary`` two-step write (PR-14)."""


@dataclass
class RecoveryReport:
    """What one recovery pass did (for logs and tests)."""

    forgets_clean: bool = True
    renamed: bool = False
    crashed: list[str] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)
    digests: dict[str, bool] = field(default_factory=dict)
    summaries: dict[str, bool] = field(default_factory=dict)
    uploads: dict[str, bool] = field(default_factory=dict)
    reports: dict[str, bool] = field(default_factory=dict)
    swept: int = 0


# ── 上传流水 → 上传文件 ───────────────────────────────────────────────


def _read_stream(path: Path) -> list[dict]:
    data = path.read_bytes()
    parts = data.split(b"\n")
    tail = parts.pop()
    if tail:
        logger.warning("visit upload stream %s: dropped a partial trailing line", path.name)
    out = []
    for raw in parts:
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _valid_line(record: dict) -> bool:
    # 能解析成对象但字段坏了的行（lp 为 null、side 不认识……）按损坏丢掉：
    # 它们会让排序抛错，整轮补录随之中断
    lp = record.get("lp")
    return (
        all(name in record for name in _LINE_FIELDS)
        and isinstance(lp, int) and not isinstance(lp, bool) and lp >= 0
        and record.get("side") in ("host", "guest")
        and record.get("from") in ("own_cat", "peer_cat", "own_human", "peer_human")
        and _number(record.get("ts")) is not None
        and isinstance(record.get("text"), str)
        and isinstance(record.get("truncated"), bool)
    )


def build_upload_doc(
    records: list[dict],
    *,
    visit_id: str,
    finalized_reason: str | None,
    fallback_own_visit_uid: str | None = None,
) -> dict | None:
    """Build the ``.upload.json`` document of a crashed visit from its upload stream.

    ``records`` are the stream's JSON objects. The first must be the upload
    header ``{kind:'header', visit_id, role, own_visit_uid, started_at,
    own_char_uid, app_version, transport}``; without it ``None`` is returned
    (corrupt stream). ``ended_at`` is the ``ts`` of the last record that has
    one (the header's ``started_at`` when there is none), ``usage`` the sum of
    every usage delta, ``anomalies`` the number of anomaly records, ``lines``
    every line record in ``(lp, side_rank)`` order, and ``finalized_reason``
    the given reason or ``'crash'``. A header without ``own_visit_uid`` (an
    older header layout) takes ``fallback_own_visit_uid`` (the visit's own
    ``state.json`` / spool header account) instead of being dropped.
    """
    if not records or records[0].get("kind") != "header":
        return None
    header = records[0]
    started_at = _number(header.get("started_at"))
    if (
        header.get("visit_id") != visit_id or started_at is None
        or header.get("role") not in ("host", "guest")
    ):
        return None
    usage = {key: 0 for key in _USAGE_KEYS}
    anomalies = 0
    lines: list[dict] = []
    ended_at = started_at
    for record in records[1:]:
        ts = _number(record.get("ts"))
        if ts is not None:
            ended_at = ts
        kind = record.get("kind")
        if kind == "line":
            if _valid_line(record):
                lines.append({name: record[name] for name in _LINE_FIELDS})
        elif kind == "usage":
            delta = record.get("d")
            if isinstance(delta, dict):
                for key in _USAGE_KEYS:
                    value = delta.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        usage[key] += value
        elif kind == "anomaly":
            anomalies += 1
    lines.sort(key=line_order_key)
    request = {
        "visit_id": visit_id,
        "role": header["role"],
        "started_at": started_at,
        "ended_at": ended_at,
        "finalized_reason": finalized_reason or "crash",
        "usage": {"duration_s": max(0, int(ended_at - started_at)), **usage},
        "lines": lines,
        "anomalies": anomalies,
        "app_version": str(header.get("app_version") or ""),
    }
    return {
        "v": UPLOAD_DOC_VERSION,
        # 设计稿较早的上传头定义没有这个字段：缺失或类型不对时改用同场 state.json /
        # 转录头行记的占房账号（上传只在登录账号与它一致时进行，记 None 就谁都传不了）；
        # 两处都没有才记 None，照样封存，不删唯一的流水副本
        "own_visit_uid": _owner_or_none(header.get("own_visit_uid")) or _owner_or_none(fallback_own_visit_uid),
        "own_char_uid": header.get("own_char_uid"),
        "transport": header.get("transport"),
        "request": request,
    }


# 与 identity 里票据核验认的传输方式同一集合
_UPLOAD_TRANSPORTS = frozenset({"trtc", "livekit"})


def _envelope_valid(own_char_uid: Any, transport: Any) -> bool:
    # 先判类型：对象 / 数组不可哈希，直接做集合成员判断会抛 TypeError、中断整轮补传
    return (
        isinstance(own_char_uid, str) and bool(own_char_uid)
        and isinstance(transport, str) and transport in _UPLOAD_TRANSPORTS
    )


def _owner_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _spool_header_owner_sync(spool_dir: Path, visit_id: str) -> str | None:
    try:
        header = _read_header_strict(visit_path(spool_dir, visit_id, SPOOL_SUFFIX), validate=False)
    except (OSError, ValueError):
        return None
    if not header or header.get("visit_id") != visit_id:
        # 被换过 / 复制过的头行：别的场次的账号不能套到这一场的上传上
        return None
    return _owner_or_none(header.get("own_uid"))


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sealed_doc_belongs(doc: Any, visit_id: str) -> bool:
    """Whether a ``.upload.json`` is this visit's well-formed sealed upload.

    Only then may the stream next to it be deleted; anything else (another
    visit's document, a wrong version, a broken request) is resealed from
    the stream, the only intact copy.
    """
    if not isinstance(doc, dict) or doc.get("v") != UPLOAD_DOC_VERSION:
        return False
    owner = doc.get("own_visit_uid")
    if owner is not None and (not isinstance(owner, str) or not owner):
        # 上传只在登录账号与它一致时进行：坏值会让这份文件永远传不出去
        return False
    if not _envelope_valid(doc.get("own_char_uid"), doc.get("transport")):
        # 只剩上传文件时没有流水可比：角色 id 与传输方式也要在这里核对，坏信封交给上传回调
        # 只会每次启动都失败到过期
        return False
    request = doc.get("request")
    usage = request.get("usage") if isinstance(request, dict) else None
    return (
        isinstance(request, dict)
        and request.get("visit_id") == visit_id
        and request.get("role") in ("host", "guest")
        and _number(request.get("started_at")) is not None
        and _number(request.get("ended_at")) is not None
        and isinstance(request.get("finalized_reason"), str) and bool(request["finalized_reason"])
        and _count(request.get("anomalies"))
        and isinstance(request.get("app_version"), str)
        # 与 build_upload_doc 产出的完整结构同口径：用量各计数都得是非负整数
        and isinstance(usage, dict)
        and all(_count(usage.get(key)) for key in ("duration_s", *_USAGE_KEYS))
        and isinstance(request.get("lines"), list)
        # 逐行核对：转录行坏了的上传文件同样不能顶替完整的流水
        and all(isinstance(line, dict) and _valid_line(line) for line in request["lines"])
        # 顺序也要与 build_upload_doc 排出来的一致：乱序的文件替掉流水会把乱序当成正本上传
        and request["lines"] == sorted(request["lines"], key=line_order_key)
    )


def _stream_doc_sync(
    spool_dir: Path, visit_id: str, finalized_reason: str | None, owner: str | None,
) -> dict | None:
    """The document resealing the stream would produce, or None when the stream is gone or corrupt.

    Any other read error (permission, a locked file) is raised: the sealed
    document cannot be checked then, so neither file may be acted on.
    """
    try:
        records = _read_stream(visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX))
    except FileNotFoundError:
        return None
    if owner is None:
        owner = _spool_header_owner_sync(spool_dir, visit_id)
    return build_upload_doc(records, visit_id=visit_id, finalized_reason=finalized_reason,
                            fallback_own_visit_uid=owner)


def _write_private_json(path: Path, doc: dict) -> None:
    atomic_write_json(path, doc)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        # 与凭证文件同一立场：权限位尽力而为（Windows 上本就无效），设不上不影响上传
        logger.debug("visit recovery: chmod 0600 failed for %s: %s", path.name, exc)


def _seal_stream_sync(
    spool_dir: Path, visit_id: str, finalized_reason: str | None, owner: str | None = None,
) -> dict | None:
    stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
    sealed = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    if owner is None:
        owner = _spool_header_owner_sync(spool_dir, visit_id)
    doc = build_upload_doc(_read_stream(stream), visit_id=visit_id, finalized_reason=finalized_reason,
                           fallback_own_visit_uid=owner)
    if doc is None:
        memory_bridge.diag("upload_stream_corrupt", visit_id=visit_id)
        stream.unlink(missing_ok=True)
        return None
    # 与 finalize 同一顺序：先原子写 .upload.json，再删流水
    _write_private_json(sealed, doc)
    try:
        stream.unlink(missing_ok=True)
    except OSError as exc:
        # 上传文件已经封好：流水删不掉也照常上传与提交举报（Servers 按 visit_id + role
        # 幂等，下次再封只会得到 duplicate），不能让一个删不掉的文件卡住这场
        logger.warning("visit recovery: sealed %s but cannot delete its stream: %s", sealed.name, exc)
    return doc


# ── 主流程 ────────────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


_ALL_NAMES = None
"""Sentinel of :func:`_reconcile_rename`: every name-dependent step must wait."""


async def _reconcile_rename_guarded(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None,
    lifecycle_guard: LifecycleGuard | None,
    reload: Callable[[], Awaitable[tuple[set[str], dict[str, str] | None]]] | None = None,
) -> frozenset[str] | None:
    """:func:`_reconcile_rename` under the renamed character's lifecycle guard.

    Recovery runs in the background: without the guard a live rename of the
    same character could interleave between the roster and the spool
    migrations and strand spools under an intermediate name.
    """
    if lifecycle_guard is None:
        return await _reconcile_rename(config_dir, names, uid_of)
    for _attempt in range(3):
        try:
            marker = await read_roster_marker(config_dir, "pending_rename")
        except RosterCorruptError:
            return await _reconcile_rename(config_dir, names, uid_of)
        if not isinstance(marker, dict):
            return await _reconcile_rename(config_dir, names, uid_of)
        uids = {marker["uid"]} if isinstance(marker.get("uid"), str) and marker.get("uid") else set()
        for name in (marker.get("old"), marker.get("new")):
            if uid_of is not None and isinstance(name, str) and name in uid_of:
                uids.add(uid_of[name])
        if not uids:
            return await _reconcile_rename(config_dir, names, uid_of)
        async with lifecycle_guard(sorted(uids)):
            # 等守卫期间标记可能已被另一个角色的改名换掉：守卫是按旧标记拿的，锁的不是
            # 新标记的角色。标记变了就放开，按新标记重新拿守卫
            try:
                current = await read_roster_marker(config_dir, "pending_rename")
            except RosterCorruptError:
                return _ALL_NAMES
            if current != marker:
                if reload is not None:
                    names, uid_of = await reload()
                continue
            # 守卫内重读名单：等守卫期间角色可能又被改名或删除，拿守卫之前的快照会算错方向
            if reload is not None:
                names, uid_of = await reload()
            # 对账时再按拿守卫时的那份标记核一次（重读名单期间标记也可能被换掉）
            result = await _reconcile_rename(config_dir, names, uid_of, expected=marker)
            if result is _MARKER_CHANGED:
                continue
            return result
    logger.warning("visit recovery: pending_rename kept changing while waiting for its guard, deferred")
    return _ALL_NAMES


_MARKER_CHANGED = object()
"""Returned by :func:`_reconcile_rename` when the marker is not the one the caller guarded."""


async def _reconcile_rename(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None = None,
    expected: Any = None,
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
    """
    try:
        marker = await read_roster_marker(config_dir, "pending_rename")
    except RosterCorruptError as exc:
        logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
        return _ALL_NAMES
    if expected is not None and marker != expected:
        # 调用方是按另一份标记拿的守卫：这次读到的标记属于别的角色，不能拿着错的守卫去迁
        return _MARKER_CHANGED
    if marker is None:
        return frozenset()
    old = marker.get("old") if isinstance(marker, dict) else None
    new = marker.get("new") if isinstance(marker, dict) else None
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        # 格式坏了的标记已没有可对账的信息，留着只会永久挡住补录与清除：记诊断后清掉
        memory_bridge.diag("pending_rename_malformed")
        logger.error("visit recovery: malformed pending_rename %r dropped", marker)
        return frozenset() if await clear_roster_marker(config_dir, "pending_rename", marker) else _ALL_NAMES
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
    return frozenset() if await clear_roster_marker(config_dir, "pending_rename", marker) else _ALL_NAMES


async def _cleanup_outboxes(config_dir: Path, live: Callable[[str], bool]) -> None:
    spool_dir = Path(config_dir) / VISIT_SPOOL_DIRNAME
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (OUTBOX_SUFFIX,)):
        if live(visit_id):
            continue
        path = visit_path(spool_dir, visit_id, OUTBOX_SUFFIX)
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            logger.warning("visit recovery: cannot delete %s: %s", path.name, exc)


async def _has_digestable_lines(spool: VisitSpool, state: dict) -> bool:
    if not is_digestable(state):
        return False
    contents = await spool.read_back()
    return contents.header is not None and any(
        str(line.get("text") or "").strip() for line in contents.lines
    )


async def _recover_visit(
    visit_id: str,
    *,
    config_dir: Path,
    render_chips: RenderChips,
    resolve_char_name: ResolveCharName,
    spawn_background: SpawnBackground | None,
    summary_llm: SummaryLLM | None,
    family_names: Iterable[str],
    resume_diary_commit: ResumeDiaryCommit | None,
    client: ScopedMemoryClient | None,
    report: RecoveryReport,
    skip_names: frozenset[str] = frozenset(),
) -> None:
    spool = VisitSpool(config_dir, visit_id)
    state = await spool.read_state()
    if state is None:
        return
    own_char = await resolve_char_name(state["own_char_uid"])
    if not own_char:
        # 角色已删：退役流程负责这场的文件，这里不出芯片、不写任何东西
        return
    if own_char in skip_names or state["own_char"] in skip_names:
        # 这个角色的改名还没对账清楚：按名字找名册条目可能找错，留到下次启动
        return
    choice = state["debrief_choice"]
    status = None
    show_chip = False
    if state["finalized"] is None:
        # 只在有可 digest 句时出芯片（记忆关的场次没有 .jsonl）；不写任何私聊记忆。
        # 崩溃标记与芯片标记同一次原子写：两步之间被杀，下次就再也判不出要弹芯片
        show_chip = choice in (None, "ask_later") and await _has_digestable_lines(spool, state)
        changes: dict[str, Any] = {"finalized": "crash"}
        if show_chip:
            changes["debrief_chip_pending"] = True
        state = await spool.update_state(**changes)
        report.crashed.append(visit_id)
        status = "interrupted"
    elif state["finalized"] == "shutdown" and choice is None:
        # 兜底：老版本关机没写 ask_later，或写之前就被杀
        if await _has_digestable_lines(spool, state):
            state = await spool.update_state(debrief_choice="ask_later")
            show_chip = True
    elif choice is None:
        # 已收口、用户还没决定：芯片标记在就每次启动都重弹（与 ask_later 一致，崩溃场次
        # 不只弹第一次）；标记不在——任何收口原因（wrap_up / peer_left …）写完 finalized、
        # 还没来得及记芯片就被杀，或旧版本留下的崩溃场次——有可 digest 句就补记并弹出
        show_chip = state["debrief_chip_pending"] or await _has_digestable_lines(spool, state)
    elif choice == "ask_later":
        show_chip = True
    if choice in ("generating:diary", "preview:diary", "committing:diary", "commit_failed:diary"):
        # 生成预览时崩溃 / 已落盘的预览待确认 / 两步写入进行中（可能还在退避等待）/ 永久性
        # 写入失败：零 LLM、零写入，经 bind 重放对应的块（committing 显示「写入中」）
        show_chip = True
    if show_chip and state["finalized"] == "crash":
        # 崩溃场次每次重放都带上「意外中断」，不只在第一次标崩溃时
        status = "interrupted"
    if show_chip:
        if not state["debrief_chip_pending"]:
            state = await spool.update_state(debrief_chip_pending=True)
        report.chips.append(visit_id)
        try:
            await render_chips(visit_id, own_char=own_char, status=status)
        except Exception as exc:  # noqa: BLE001 - 发不出去就等 bind 重放
            logger.warning("visit recovery: render_chips failed for %s: %r", visit_id, exc)
    if choice == "committing:diary" and resume_diary_commit is not None:
        await _maybe_await(resume_diary_commit(spool, state))

    async def digest() -> Any:
        return await commit_visit_region(
            spool, resolve_char_name=resolve_char_name, client=client, family_names=family_names,
        )

    async def summary() -> Any:
        return await commit_last_summary(
            spool, llm=summary_llm, resolve_char_name=resolve_char_name, family_names=family_names,
        )

    jobs: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
    if is_digestable(state):
        jobs.append(("digest", digest))
    if not state["last_summary_done"] and summary_llm is not None:
        jobs.append(("summary", summary))
    for kind, factory in jobs:
        if spawn_background is not None:
            result = await _maybe_await(spawn_background(state["own_char_uid"], factory))
        else:
            result = await factory()
        ok = bool(getattr(result, "ok", result))
        (report.digests if kind == "digest" else report.summaries)[visit_id] = ok


async def _upload_pending(
    config_dir: Path,
    *,
    live: Callable[[str], bool],
    upload_transcript: UploadTranscript | None,
    submit_report: SubmitReport | None,
    report: RecoveryReport,
) -> set[str]:
    """Retry pending uploads; return the visit ids whose upload is still pending."""
    spool_dir = config_dir / VISIT_SPOOL_DIRNAME
    pending: set[str] = set()
    sealed = set(await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSON_SUFFIX,)))
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSONL_SUFFIX,)):
        if live(visit_id):
            continue
        stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
        state = None
        try:
            state = await VisitSpool(config_dir, visit_id).read_state()
        except (OSError, ValueError) as exc:
            logger.warning("visit recovery: state of %s unreadable, upload marked crash: %s", visit_id, exc)
        reason = state["finalized"] if state else None
        owner = state["own_uid"] if state else None
        if visit_id in sealed:
            # 封存时「已写 .upload.json、还没删流水」就崩了：上传文件才是这场的那份，
            # 留着流水会在上传成功后被再封一次、重复上传。先确认上传文件读得出来再删流水；
            # 上传文件坏了就从流水重新封存（覆盖坏文件），流水是这时唯一完整的副本
            try:
                sealed_doc = await asyncio.to_thread(
                    _load_json, visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX),
                )
            except (OSError, ValueError):
                sealed_doc = None
            belongs = _sealed_doc_belongs(sealed_doc, visit_id)
            if belongs:
                # 重封会产出的每个字段都要与文件一致（信封、转录行、用量、时间戳）：缺行 / 改过的
                # 文件替掉完整的流水，删了流水就再也重封不回来。上传回调额外写进文件的分片进度
                # 不在比较之列，否则删不掉流水时每次补录都会重封、把进度清零。
                # 结束原因：state 记的是确定的原因时必须一致；state 读不出、或是 crash（正常收口
                # 写完上传文件后、写 state.finalized 前崩溃，本轮补录先把它标成了 crash）时
                # 沿用文件里记的，不把正常结束的场次重封成 crash
                sealed_reason = sealed_doc["request"]["finalized_reason"]
                try:
                    expected = await asyncio.to_thread(
                        _stream_doc_sync, spool_dir, visit_id,
                        sealed_reason if reason in (None, "crash") else reason, owner,
                    )
                except OSError as exc:
                    # 流水还在却一时读不出（权限、被占用）：比对做不了，两份都不动、这轮不上传。
                    # 不能当成「流水没了」放行——删掉完整的流水、传上去的可能是缺行的文件
                    logger.warning("visit recovery: stream of %s unreadable, upload deferred: %s", visit_id, exc)
                    sealed.discard(visit_id)
                    pending.add(visit_id)
                    continue
                belongs = expected is None or all(sealed_doc.get(name) == value for name, value in expected.items())
            if belongs:
                try:
                    await asyncio.to_thread(stream.unlink, True)
                except OSError as exc:
                    # 删不掉就照常上传：Servers 按 visit_id + role 幂等，下次再从残留流水
                    # 封存上传只会得到 duplicate；若因此挡住上传，一个长期删不掉的文件
                    # 就让这场的转录与排队举报永远交不上去
                    logger.warning("visit recovery: cannot delete stale stream %s: %s", stream.name, exc)
                continue
            # 先从待上传集合里拿掉：重封失败时不能把这份坏的 / 别场的文件交给上传回调
            sealed.discard(visit_id)
            logger.warning("visit recovery: sealed upload of %s unreadable or not this visit's, resealing from its stream",
                           visit_id)
        # 转录补传与 finalized 无关：流水还在、上传文件没写成，就从流水构建
        try:
            doc = await asyncio.to_thread(_seal_stream_sync, spool_dir, visit_id, reason, owner)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            # 一份流水读写不了只跳过它自己，不能挡住其余场次的补传与举报
            logger.warning("visit recovery: cannot seal %s: %s", stream.name, exc)
            pending.add(visit_id)
            continue
        if doc is not None:
            sealed.add(visit_id)
        elif not await _drop_corrupt_sealed(spool_dir, visit_id):
            # 流水坏了、旁边那份上传文件也不是本场的有效文件：它删不掉就先挡住举报
            pending.add(visit_id)
        else:
            # 流水坏了、也没有有效的封存文件：这场转录再也传不上去，排队的举报记下原因
            await _mark_report_transcript_unavailable(config_dir, visit_id, "corrupt")
    for visit_id in sorted(sealed):
        if live(visit_id):
            # 在飞场次的转录还没传：它排队的举报也不能先交
            pending.add(visit_id)
            continue
        path = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
        try:
            doc = await asyncio.to_thread(_load_json, path)
        except OSError as exc:
            logger.warning("visit recovery: pending upload %s unreadable: %s", path.name, exc)
            pending.add(visit_id)
            continue
        except ValueError:
            doc = False
        if doc is None:
            continue
        if not _sealed_doc_belongs(doc, visit_id):
            # 没有流水可重封的坏 / 别场上传文件：与损坏流水同一处理，转录已无法恢复，
            # 删掉它（不交给上传回调），排队的举报随后照常提交
            if not await _drop_corrupt_sealed(spool_dir, visit_id):
                pending.add(visit_id)
            else:
                await _mark_report_transcript_unavailable(config_dir, visit_id, "corrupt")
            continue
        if upload_transcript is None:
            pending.add(visit_id)
            continue
        try:
            ok = bool(await upload_transcript(visit_id, doc))
        except Exception as exc:  # noqa: BLE001 - 任何失败都留文件下次再试
            logger.warning("visit recovery: upload of %s failed: %r", visit_id, exc)
            ok = False
        report.uploads[visit_id] = ok
        if not ok:
            pending.add(visit_id)
            continue
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            # 已传上去但本地删不掉：不挡其余场次，也不算「转录未上传」——Servers 已受理，
            # 排队的举报照常提交；文件留到下次（届时 duplicate 后再删）
            logger.warning("visit recovery: uploaded %s but cannot delete it: %s", path.name, exc)
        # 该场转录上传成功后，接着提交它排队的举报
        await _submit_report(config_dir, visit_id, submit_report, report)
    return pending


async def _drop_corrupt_sealed(spool_dir: Path, visit_id: str) -> bool:
    """Delete an invalid ``.upload.json`` that has no stream to reseal from; False if it is still there."""
    path = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    if not await asyncio.to_thread(path.exists):
        return True
    memory_bridge.diag("upload_doc_corrupt", visit_id=visit_id)
    try:
        await asyncio.to_thread(path.unlink, True)
    except OSError as exc:
        logger.warning("visit recovery: cannot delete corrupt upload %s: %s", path.name, exc)
        return False
    return True


def _load_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except RecursionError as exc:
        raise ValueError("too deeply nested") from exc


async def _submit_report(
    config_dir: Path, visit_id: str, submit_report: SubmitReport | None, report: RecoveryReport,
    *, transcript_gated: bool = False,
) -> None:
    if submit_report is None:
        return
    path = visit_path(config_dir / VISIT_REPORTS_DIRNAME, visit_id, ".json")
    try:
        doc = await asyncio.to_thread(_load_json, path)
    except (OSError, ValueError) as exc:
        logger.warning("visit recovery: queued report %s unreadable: %s", path.name, exc)
        return
    if doc is None:
        return
    if transcript_gated and not (isinstance(doc, dict) and doc.get("include_transcript") is False):
        # 转录还没传上去：附转录的举报等它；明确不附转录的举报不受转录上传的闸
        return
    try:
        ok = bool(await submit_report(visit_id, doc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit recovery: report of %s failed: %r", visit_id, exc)
        ok = False
    report.reports[visit_id] = ok
    if ok:
        # 举报文件只在 Servers 受理后删
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            logger.warning("visit recovery: report %s accepted but cannot delete it: %s", path.name, exc)


def _expired_upload_visits(deleted: Iterable[Path]) -> set[str]:
    out = set()
    for path in deleted:
        for suffix in (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX):
            if path.name.endswith(suffix):
                out.add(path.name[: -len(suffix)])
                break
    return out


async def _mark_report_transcript_unavailable(config_dir: Path, visit_id: str, reason: str) -> None:
    """Record on a queued report why its transcript will never be uploaded (diagnostics)."""
    path = visit_path(config_dir / VISIT_REPORTS_DIRNAME, visit_id, ".json")
    try:
        doc = await asyncio.to_thread(_load_json, path)
        if not isinstance(doc, dict) or doc.get("transcript_unavailable"):
            return
        await asyncio.to_thread(_write_private_json, path, {**doc, "transcript_unavailable": reason})
    except (OSError, ValueError) as exc:
        # 只是诊断字段：记不上也照常提交举报
        logger.warning("visit recovery: cannot mark report %s transcript_unavailable: %s", path.name, exc)


async def _submit_reports(
    config_dir: Path, *, skip: set[str], submit_report: SubmitReport | None, report: RecoveryReport,
) -> None:
    directory = config_dir / VISIT_REPORTS_DIRNAME
    try:
        names = await asyncio.to_thread(os.listdir, directory)
    except FileNotFoundError:
        return
    for name in sorted(names):
        visit_id = name[: -len(".json")] if name.endswith(".json") else ""
        if not VISIT_ID_RE.fullmatch(visit_id) or visit_id in report.reports:
            continue
        await _submit_report(config_dir, visit_id, submit_report, report, transcript_gated=visit_id in skip)


async def visit_spool_recovery(
    render_chips: RenderChips,
    upload_transcript: UploadTranscript | None = None,
    *,
    is_live: Callable[[str], bool],
    spawn_background: SpawnBackground | None = None,
    config_dir: str | Path | None = None,
    resolve_char_name: ResolveCharName | None = None,
    list_char_names: Callable[[], Awaitable[Iterable[str]]] | None = None,
    summary_llm: SummaryLLM | None = None,
    family_names: Iterable[str] = (),
    submit_report: SubmitReport | None = None,
    resume_diary_commit: ResumeDiaryCommit | None = None,
    void_pending: VoidPending | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    client: ScopedMemoryClient | None = None,
    now: float | None = None,
) -> RecoveryReport:
    """Run one startup recovery pass over the visit files (see the module docstring).

    ``is_live(visit_id)`` (required) tells visits of a ``VisitRuntime`` alive
    in this process (their files are never touched): recovery runs as a
    background task, so a visit may start before it reaches the uploads, and
    sealing its stream or deleting its outbox would truncate it. ``spawn_background`` routes the
    digest / summary commits through the character's visit background-task
    entry. ``summary_llm`` is required for last-visit summaries (without it
    they wait for a later pass). ``lifecycle_guard`` is the clearing
    endpoints' rename / delete guard, held while forgets are replayed. The
    other callbacks are optional and their
    steps are skipped (files kept) when missing. Independent of
    ``visitMemoryEnabled`` and of the ``NEKO_VISIT_ENABLED`` release switch.
    """
    report = RecoveryReport()
    if config_dir is None:
        from utils.config_manager import get_config_manager

        config_dir = get_config_manager().config_dir
    config_dir = Path(config_dir)
    resolve = resolve_char_name or local_chars.resolve_char_name
    live = is_live
    # 改名对账先于清除重放：清除按角色当前名字找名册条目，改名迁移没做完时条目还在
    # 旧名字下，remove_char 会「成功」地什么都没删，随后迁移又把条目连同摘要搬到新名字
    try:
        # 不论名单从哪来都先严格检查角色配置：常规加载会静默滤掉坏条目、返回部分名单，
        # 改名对账会据此误判方向，下面的清除重放也会把「解析不出名字」当成「角色已删」。
        # 配置读不出 / 条目坏了就整段推迟（抛错走下面的分支）
        async def load_names() -> tuple[set[str], dict[str, str] | None]:
            await local_chars.ensure_characters_readable()
            uid_of = None if list_char_names is not None else await local_chars.load_local_characters()
            names = set(await list_char_names()) if list_char_names is not None else set(uid_of)
            return names, uid_of

        names, uid_of = await load_names()
        unsettled = await _reconcile_rename_guarded(config_dir, names, uid_of, lifecycle_guard, load_names)
    except Exception as exc:  # noqa: BLE001 - 补录各段互不连累
        logger.error("visit recovery: rename reconciliation failed: %r", exc)
        unsettled = _ALL_NAMES
    names_settled = unsettled is not _ALL_NAMES
    report.renamed = unsettled == frozenset()
    if names_settled:
        try:
            # 还没对账清楚的改名只涉及它的两个名字：清除重放对这两个名字自己会推迟
            report.forgets_clean = await replay_forgets(
                config_dir, resolve_char_name=resolve, client=client, void_pending=void_pending,
                lifecycle_guard=lifecycle_guard,
                # 走到这里时角色配置已确认读得出（上面的严格检查），解析不出名字就是已删除
                drop_deleted_chars=True,
            ) and report.renamed
        except Exception as exc:  # noqa: BLE001
            logger.error("visit recovery: forget replay failed: %r", exc)
            report.forgets_clean = False
    else:
        # 改名还没对账清楚：清除与逐场补录都按角色当前名字找名册条目，此时条目可能还
        # 在旧名下，清除会「成功」地什么都没删。留到下次启动（转录补传与举报不依赖名字）
        logger.warning("visit recovery: pending rename unresolved, forget replay and visit recovery deferred")
        report.forgets_clean = False
    await _cleanup_outboxes(config_dir, live)
    try:
        swept = await VisitSpool.sweep(config_dir, time.time() if now is None else now, is_live=live)
        report.swept = len(swept)
        # 待传转录 7 天到期被放弃：它排队的举报随后照常提交（设计 §4.7），先在举报文件里记下
        # transcript_unavailable，不能当作从没有过待传转录
        spool_dir = config_dir / VISIT_SPOOL_DIRNAME
        for visit_id in sorted(_expired_upload_visits(swept)):
            # 流水与封存文件按各自的 mtime 到期：只删掉其中一份时另一份本轮仍可能传上去，
            # 两份都没了才算这场转录不可用
            remaining = [visit_path(spool_dir, visit_id, suffix) for suffix in (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX)]
            if await asyncio.to_thread(lambda paths=remaining: any(path.exists() for path in paths)):
                continue
            await _mark_report_transcript_unavailable(config_dir, visit_id, "expired")
    except Exception as exc:  # noqa: BLE001
        logger.error("visit recovery: sweep failed: %r", exc)
    for visit_id in (await VisitSpool.list_visit_ids(config_dir, (STATE_SUFFIX,)) if names_settled else []):
        if live(visit_id) or is_spool_open(visit_path(config_dir / VISIT_SPOOL_DIRNAME, visit_id, ".jsonl")):
            continue
        try:
            await _recover_visit(
                visit_id, config_dir=config_dir, render_chips=render_chips, skip_names=unsettled,
                resolve_char_name=resolve, spawn_background=spawn_background,
                summary_llm=summary_llm, family_names=family_names,
                resume_diary_commit=resume_diary_commit, client=client, report=report,
            )
        except Exception as exc:  # noqa: BLE001 - 一场坏了不挡其余场次
            logger.warning("visit recovery: visit %s skipped: %r", visit_id, exc)
    pending = await _upload_pending(
        config_dir, live=live, upload_transcript=upload_transcript,
        submit_report=submit_report, report=report,
    )
    # 另外独立扫描举报队列：转录已上传（.upload.json 已删）而举报还没提交的也继续提交
    streams = set(await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSONL_SUFFIX,)))
    await _submit_reports(config_dir, skip=pending | streams, submit_report=submit_report, report=report)
    return report
