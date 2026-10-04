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

"""Per-visit line spool and its ``state.json`` (OD-17 v2).

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 and PR-06
``spool.py``.

Layout, all under ``config_dir/visit_spool/`` (shared with the outbox)::

    <visit_id>.jsonl          header line + one line per spoken line
                              (only when visit memory is on for this visit)
    <visit_id>.state.json     canonical state, every visit, no transcript text
    <visit_id>.upload.json    pending transcript upload (never touched here
    <visit_id>.upload.jsonl   except by the seven-day age rule of ``sweep``)
    <visit_id>.outbox.jsonl   reliable outbox (owned by ``outbox.py``)

Steam cloud save only syncs ``MANAGED_MEMORY_FILENAMES``
(``utils/cloudsave_runtime/snapshots.py``), so nothing here is ever synced.

Writer model: each open :class:`VisitSpool` owns one dedicated writer thread
(a single-worker executor) and one ``O_APPEND`` file descriptor. ``append``
encodes and size-checks the line on the caller's thread, then submits one
``os.write`` of the whole encoded line to that thread. Submission happens
before the coroutine first suspends, so lines land in call order even when
callers do not await each other. The fd is ``fsync``-ed when
:meth:`VisitSpool.fsync_due` says so (every ``VISIT_SPOOL_FSYNC_S``) and once
on :meth:`VisitSpool.close`; a process crash loses nothing (page cache), a
power cut at most one fsync interval. The interface is plain data: dicts in,
dicts out, no event-loop objects are kept.

``state.json`` is written with ``atomic_write_json`` in a worker thread and is
validated against the canonical schema on every read and write. Files are
created owner-only (``0o600``; no effect on Windows, same stance as the
credential files).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import math
import os
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.visit_settings import (
    VISIT_LP_MAX,
    VISIT_SPOOL_DIR_CAP_BYTES,
    VISIT_SPOOL_DIRNAME,
    VISIT_SPOOL_FSYNC_S,
    VISIT_SPOOL_LINE_MAX_BYTES,
    VISIT_SPOOL_RETENTION_DAYS,
    VISIT_TEXT_MAX_BYTES,
)
from main_logic.visit.subjects import derive_pair_id, derive_peer_char_id, path_lock
from utils.file_utils import atomic_write_bytes, atomic_write_json
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE, require_visit_id, visit_path

logger = get_module_logger(__name__, "Main")

SPOOL_SUFFIX = ".jsonl"
STATE_SUFFIX = ".state.json"
UPLOAD_JSON_SUFFIX = ".upload.json"
UPLOAD_JSONL_SUFFIX = ".upload.jsonl"
OUTBOX_SUFFIX = ".outbox.jsonl"
_KNOWN_SUFFIXES = (
    SPOOL_SUFFIX,
    STATE_SUFFIX,
    UPLOAD_JSON_SUFFIX,
    UPLOAD_JSONL_SUFFIX,
    OUTBOX_SUFFIX,
)
_UPLOAD_SUFFIXES = (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX)
_VISIT_ID_LEN = 22
_RETENTION_S = VISIT_SPOOL_RETENTION_DAYS * 86400
_O_BINARY = getattr(os, "O_BINARY", 0)

HEADER_FIELDS = (
    "v",
    "visit_id",
    "role",
    "own_uid",
    "own_char",
    "own_char_uid",
    "pair_id",
    "peer_uid",
    "peer_char_id",
    "peer_char_tag",
    "started_at",
    "lang",
)
LINE_REQUIRED_FIELDS = ("lp", "side", "ts", "from", "text")
LINE_OPTIONAL_FIELDS = ("ln", "truncated")
LINE_SPEAKERS = ("own_cat", "peer_cat", "peer_human", "own_human")
_PEER_IDENTITY_FIELDS = ("peer_uid", "pair_id", "peer_char_id")
# 头行还多一个对端的稳定角色标签（state.json 没有这个键），「清除这个人」时一并抹掉
_HEADER_PEER_FIELDS = _PEER_IDENTITY_FIELDS + ("peer_char_tag",)

DEBRIEF_CHOICES = (
    None,
    "ask_later",
    "generating:diary",
    "preview:diary",
    "committing:diary",
    "commit_failed:diary",
    "diary",
    "forget",
    "abandoned",
)
# 「记成日记」提交中 / 永久性失败待用户处理：state.json 里的 debrief_pending 与写入进度是
# 补写的唯一依据，两种清理都不删它（.jsonl 不因此豁免，重试只需要 state.json）
_COMMIT_PINNED_CHOICES = ("committing:diary", "commit_failed:diary")
_DEBRIEF_WRITE_FLAGS = (
    "facts", "cache", "facts_unconfirmed", "cache_unconfirmed", "facts_inflight", "cache_inflight",
)
# mark_forget 的两种终态各自允许从哪些状态进入（同一终态重复记录幂等）
_FORGET_SOURCES = {
    "forget": (None, "ask_later", "generating:diary", "preview:diary", "forget"),
    "abandoned": ("commit_failed:diary", "abandoned"),
}
STATE_FIELDS = frozenset({
    "visit_id",
    "own_uid",
    "own_char",
    "own_char_uid",
    "pair_id",
    "peer_uid",
    "peer_char_id",
    "digested_through_lp",
    "digest_runs",
    "finalized",
    "debrief_choice",
    "debrief_pending",
    "debrief_writes",
    "debrief_retry",
    "debrief_commit_error",
    "debrief_chip_pending",
    "last_summary_done",
    "memory_enabled",
    "digest_writes",
})


class SpoolLineTooLarge(ValueError):
    """Raised when one encoded spool line exceeds ``VISIT_SPOOL_LINE_MAX_BYTES``."""


class SpoolStateUnreadable(RuntimeError):
    """One or more ``state.json`` files exist but cannot be read (forget paths fail closed)."""

    def __init__(self, visit_ids: list[str]) -> None:
        self.visit_ids = list(visit_ids)
        super().__init__(f"unreadable visit state: {', '.join(self.visit_ids)}")


class SpoolBusy(RuntimeError):
    """Raised when a header rewrite targets a spool that is still open for appends."""


# 进程级「仍在写」登记：在飞串门持有 .jsonl 的 O_APPEND fd，此时整文件替换式改写头行
# 会让后续追加写进被替换掉的旧 inode（Windows 上替换还可能直接失败）。清除 / 改名
# 遇到在飞场次时报 SpoolBusy，撤销日志保留未完成步骤，等这场结束后重放。
_OPEN_SPOOLS: set[str] = set()
_OPEN_SPOOLS_LOCK = threading.Lock()


def _spool_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def is_spool_open(path: Path) -> bool:
    """True while some ``VisitSpool`` in this process holds ``path`` open for appends."""
    with _OPEN_SPOOLS_LOCK:
        return _spool_key(path) in _OPEN_SPOOLS


class SpoolStateError(ValueError):
    """Raised when a ``state.json`` document violates the canonical schema."""


# ── 编码与校验（纯函数）────────────────────────────────────────────────


def _encode_line(obj: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_header(header: Mapping[str, Any]) -> dict:
    """Check a spool header line and return a plain dict copy of it."""
    if not isinstance(header, Mapping):
        raise ValueError("spool header must be a mapping")
    keys = set(header)
    if keys != set(HEADER_FIELDS):
        raise ValueError(
            "spool header fields mismatch: missing=%s extra=%s"
            % (sorted(set(HEADER_FIELDS) - keys), sorted(keys - set(HEADER_FIELDS)))
        )
    if type(header["v"]) is not int or header["v"] != 1:
        # true / 1.0 与 1 相等：按类型判，只认整数 1
        raise ValueError("spool header v must be the integer 1")
    require_visit_id(header["visit_id"])
    if header["role"] not in ("host", "guest"):
        raise ValueError("spool header role must be host or guest")
    for name in ("own_uid", "own_char", "own_char_uid"):
        if not isinstance(header[name], str) or not header[name]:
            raise ValueError(f"spool header {name} must be a non-empty string")
    for name in _HEADER_PEER_FIELDS:
        value = header[name]
        # 对端字段可被「清除这个人」置空；否则必须是非空字符串（类型坏了不能当「不是这一对」）
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"spool header {name} must be a non-empty string or null")
    if len({header[name] is None for name in _HEADER_PEER_FIELDS}) > 1:
        # 「清除这个人」一次抹掉全部对端字段；只剩一半说明抹到一半或被改坏，
        # 按 pair 找不到它，留下的 peer_uid 却永远不会再被清掉
        raise ValueError("spool header peer fields must be all set or all null")
    if header["pair_id"] is not None and header["pair_id"] != derive_pair_id(
        header["own_uid"], header["peer_uid"]
    ):
        # pair_id 必须是 (own_uid, peer_uid) 推出的那一对，否则清除按 pair 找不到它
        raise ValueError("spool header pair_id does not match own_uid / peer_uid")
    if header["peer_char_id"] is not None and header["peer_char_id"] != derive_peer_char_id(
        header["peer_uid"], header["peer_char_tag"]
    ):
        # 对端猫的 id 必须由 (peer_uid, peer_char_tag) 推出：错的 id 会被抄进不带 tag 的
        # state.json，补录 / debrief 按它把 digest 与召回写进别的猫的主体，名册也清不到
        raise ValueError("spool header peer_char_id does not match peer_uid / peer_char_tag")
    if not _is_number(header["started_at"]):
        raise ValueError("spool header started_at must be a number")
    return dict(header)


def encode_spool_line(line: Mapping[str, Any]) -> bytes:
    """Validate one spoken line and return its encoded JSONL bytes.

    Required fields: ``lp`` (int), ``side`` (str), ``ts`` (number), ``from``
    (one of ``LINE_SPEAKERS``), ``text`` (str, at most ``VISIT_TEXT_MAX_BYTES``
    UTF-8 bytes, already sanitized by the caller). Optional: ``ln`` (str) and
    ``truncated`` (bool). The encoded line, newline included, must fit in
    ``VISIT_SPOOL_LINE_MAX_BYTES``; an oversized line raises
    :class:`SpoolLineTooLarge` and is never truncated.
    """
    if not isinstance(line, Mapping):
        raise ValueError("spool line must be a mapping")
    keys = set(line)
    missing = set(LINE_REQUIRED_FIELDS) - keys
    extra = keys - set(LINE_REQUIRED_FIELDS) - set(LINE_OPTIONAL_FIELDS)
    if missing or extra:
        raise ValueError(
            "spool line fields mismatch: missing=%s extra=%s" % (sorted(missing), sorted(extra))
        )
    if not _is_int(line["lp"]) or not 0 <= line["lp"] <= VISIT_LP_MAX:
        # 与 wire / room 同一值域：越界的 lp 会把补录与上传的排序搞乱
        raise ValueError("spool line lp must be an int in 0..VISIT_LP_MAX")
    if line["side"] not in ("host", "guest"):
        # 只有两种协议角色：别的字符串会在补录排序时被错归属、上传时过不了 schema
        raise ValueError("spool line side must be host or guest")
    if not _is_number(line["ts"]):
        raise ValueError("spool line ts must be a number")
    if line["from"] not in LINE_SPEAKERS:
        raise ValueError(f"spool line from must be one of {LINE_SPEAKERS}")
    text = line["text"]
    if not isinstance(text, str):
        raise ValueError("spool line text must be a string")
    if len(text.encode("utf-8")) > VISIT_TEXT_MAX_BYTES:
        raise ValueError("spool line text exceeds VISIT_TEXT_MAX_BYTES")
    if "ln" in line and not isinstance(line["ln"], str):
        raise ValueError("spool line ln must be a string")
    if "truncated" in line and not isinstance(line["truncated"], bool):
        raise ValueError("spool line truncated must be a bool")
    data = _encode_line(line)
    if len(data) > VISIT_SPOOL_LINE_MAX_BYTES:
        raise SpoolLineTooLarge(
            f"encoded spool line is {len(data)} bytes > {VISIT_SPOOL_LINE_MAX_BYTES}"
        )
    return data


def new_debrief_writes() -> dict:
    """Return the initial ``debrief_writes`` progress record (nothing written, nothing in flight)."""
    record: dict[str, Any] = {name: False for name in _DEBRIEF_WRITE_FLAGS}
    record["facts_written"] = 0
    return record


def new_state(
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    pair_id: str | None,
    peer_uid: str | None,
    peer_char_id: str | None,
    memory_enabled: bool,
    visit_id: str | None = None,
) -> dict:
    """Return a fresh canonical ``state.json`` document for one visit.

    ``visit_id`` may be left ``None``: :meth:`VisitSpool.write_state` binds
    the document to its own visit when writing it.

    ``memory_enabled`` is the ``visitMemoryEnabled`` value read once when the
    visit activates; it stays fixed for the whole visit. ``own_uid`` is this
    side's verified ``visit_uid`` (the community account the visit ran under):
    visit data is partitioned by account, and crash recovery needs it to
    derive the person-level subject even after the user switched accounts.
    It is not a peer field and survives "forget this person".
    """
    state = {
        "visit_id": visit_id,
        "own_uid": own_uid,
        "own_char": own_char,
        "own_char_uid": own_char_uid,
        "pair_id": pair_id,
        "peer_uid": peer_uid,
        "peer_char_id": peer_char_id,
        "digested_through_lp": -1,
        "digest_runs": 0,
        "finalized": None,
        "debrief_choice": None,
        "debrief_pending": None,
        "debrief_writes": new_debrief_writes(),
        "debrief_retry": None,
        "debrief_commit_error": None,
        "debrief_chip_pending": False,
        "last_summary_done": False,
        "memory_enabled": memory_enabled,
        "digest_writes": {},
    }
    return validate_state(state)


def _check_batch_map(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise SpoolStateError(f"{where} must be an object")
    # 写入方按 0..N-1 建批次表：有缺口（{"1": true}）或非规范写法（"01"）说明丢了批次，
    # 结清判定只看剩下的值，会在缺的那批从未确认时删掉转录
    if set(value) != {str(i) for i in range(len(value))}:
        raise SpoolStateError(f"{where} keys must be the batch numbers 0..N-1")
    for key, done in value.items():
        if not isinstance(done, bool):
            raise SpoolStateError(f"{where}[{key}] must be a bool")


def validate_state(state: Any, *, visit_id: str | None = None) -> dict:
    """Check ``state`` against the canonical schema and return a deep copy.

    The field set must match ``STATE_FIELDS`` exactly. ``debrief_choice`` is
    restricted to ``DEBRIEF_CHOICES``; ``preview:diary``,
    ``committing:diary`` and ``commit_failed:diary`` require a non-empty
    ``debrief_pending`` (it is persisted before any of them is entered),
    ``commit_failed:diary`` also a ``debrief_commit_error``, and
    ``generating:diary`` an empty one. ``debrief_writes`` carries the
    two-step progress (``facts`` / ``cache`` done, ``facts_written``,
    ``*_unconfirmed``, ``*_inflight``); ``debrief_retry`` is null or
    ``{attempts, next_at}``; ``debrief_commit_error`` is null or
    ``{step, status, at, seq}``. Raises :class:`SpoolStateError`.

    ``visit_id`` is ``None`` only in a document not yet written; with the
    ``visit_id`` argument (every read and write) it must equal it.
    """
    if not isinstance(state, Mapping):
        raise SpoolStateError("state must be an object")
    keys = set(state)
    if keys != STATE_FIELDS:
        raise SpoolStateError(
            "state fields mismatch: missing=%s extra=%s"
            % (sorted(STATE_FIELDS - keys), sorted(keys - STATE_FIELDS))
        )
    stored = state["visit_id"]
    if stored is not None:
        try:
            require_visit_id(stored)
        except ValueError as exc:
            raise SpoolStateError("state visit_id is malformed") from exc
    if visit_id is not None and stored != visit_id:
        # 被换过 / 复制过的 state.json：别的场次的归属不能套到这一场的文件上
        raise SpoolStateError("state.json belongs to another visit")
    for name in ("own_uid", "own_char", "own_char_uid"):
        if not isinstance(state[name], str) or not state[name]:
            raise SpoolStateError(f"{name} must be a non-empty string")
    for name in _PEER_IDENTITY_FIELDS:
        value = state[name]
        if value is not None and (not isinstance(value, str) or not value):
            raise SpoolStateError(f"{name} must be a non-empty string or null")
    if len({state[name] is None for name in _PEER_IDENTITY_FIELDS}) > 1:
        # 同头行：只抹了一半的对端身份按 pair 找不到，剩下的 peer_uid 会永久留在磁盘上
        raise SpoolStateError("peer_uid / pair_id / peer_char_id must be all set or all null")
    if state["pair_id"] is not None and state["pair_id"] != derive_pair_id(
        state["own_uid"], state["peer_uid"]
    ):
        # 同头行：pair_id 与身份对不上的场次，清除这个人时按 pair 找不到
        raise SpoolStateError("pair_id does not match own_uid / peer_uid")
    if not _is_int(state["digested_through_lp"]) or state["digested_through_lp"] < -1:
        raise SpoolStateError("digested_through_lp must be an int >= -1")
    if not _is_int(state["digest_runs"]) or state["digest_runs"] < 0:
        raise SpoolStateError("digest_runs must be an int >= 0")
    finalized = state["finalized"]
    if finalized is not None and (not isinstance(finalized, str) or not finalized):
        raise SpoolStateError("finalized must be null or a reason string")
    choice = state["debrief_choice"]
    if choice not in DEBRIEF_CHOICES:
        raise SpoolStateError(f"debrief_choice {choice!r} is not allowed")
    pending = state["debrief_pending"]
    if pending is not None:
        if not isinstance(pending, Mapping) or set(pending) != {"diary", "facts"}:
            raise SpoolStateError("debrief_pending must be null or {diary, facts}")
        if not isinstance(pending["diary"], str):
            raise SpoolStateError("debrief_pending.diary must be a string")
        facts = pending["facts"]
        if not isinstance(facts, list) or not all(isinstance(f, str) for f in facts):
            raise SpoolStateError("debrief_pending.facts must be a list of strings")
    pending_empty = pending is None or (not pending["diary"] and not pending["facts"])
    if choice in ("preview:diary",) + _COMMIT_PINNED_CHOICES and pending_empty:
        raise SpoolStateError(f"{choice} requires a persisted debrief_pending")
    if choice == "generating:diary" and pending is not None:
        raise SpoolStateError("generating:diary requires an empty debrief_pending")
    writes_raw = state["debrief_writes"]
    if choice == "diary" and not (
        isinstance(writes_raw, Mapping) and writes_raw.get("facts") is True
        and writes_raw.get("cache") is True
    ):
        # 「记成日记」是两步提交，两步都成才进入 diary；半截的 diary 会被当成已结清删掉转录，
        # 启动补录也不会再补缺的那一步
        raise SpoolStateError("diary requires both debrief writes to be done")
    writes = state["debrief_writes"]
    if (
        not isinstance(writes, Mapping)
        or set(writes) != set(_DEBRIEF_WRITE_FLAGS) | {"facts_written"}
        or not all(isinstance(writes[name], bool) for name in _DEBRIEF_WRITE_FLAGS)
        or not _is_int(writes["facts_written"]) or writes["facts_written"] < 0
    ):
        raise SpoolStateError(
            "debrief_writes must be {facts, cache, *_unconfirmed, *_inflight: bool, facts_written: int >= 0}"
        )
    retry = state["debrief_retry"]
    if retry is not None and not (
        isinstance(retry, Mapping) and set(retry) == {"attempts", "next_at"}
        and _is_int(retry["attempts"]) and retry["attempts"] >= 0 and _is_number(retry["next_at"])
    ):
        # 退避计数与下次时间持久化，重启接着算、不绕过退避
        raise SpoolStateError("debrief_retry must be null or {attempts: int >= 0, next_at: number}")
    error = state["debrief_commit_error"]
    if error is not None and not (
        isinstance(error, Mapping) and set(error) == {"step", "status", "at", "seq"}
        and error["step"] in ("facts", "cache") and _is_int(error["status"])
        and _is_number(error["at"]) and _is_int(error["seq"]) and error["seq"] >= 1
    ):
        raise SpoolStateError(
            "debrief_commit_error must be null or {step: facts|cache, status: int, at: number, seq: int >= 1}"
        )
    if choice == "commit_failed:diary" and error is None:
        # 「写入失败」块要据它说明哪步失败、按 seq 换新块；没有它就重放不出失败块
        raise SpoolStateError("commit_failed:diary requires a debrief_commit_error")
    for name in ("debrief_chip_pending", "last_summary_done", "memory_enabled"):
        if not isinstance(state[name], bool):
            raise SpoolStateError(f"{name} must be a bool")
    runs = state["digest_writes"]
    if not isinstance(runs, Mapping):
        raise SpoolStateError("digest_writes must be an object")
    # 轮次按 0..N-1 登记；一轮全部批次成功后才 digest_runs += 1，所以最后一轮可以还在跑。
    # 缺了某一轮（{"1": ...}）的状态会让结清判定只看剩下的轮次，在缺的那轮从未完成时删转录
    if set(runs) != {str(i) for i in range(len(runs))}:
        raise SpoolStateError("digest_writes keys must be the run numbers 0..N-1")
    if state["digest_runs"] not in (len(runs), len(runs) - 1):
        raise SpoolStateError("digest_runs does not match the registered digest_writes runs")
    for run, record in runs.items():
        if not isinstance(record, Mapping) or set(record) - {"epochs", "plan", "membership"} != {
            "requested_at", "through_lp", "group", "segments",
        }:
            raise SpoolStateError(
                f"digest_writes[{run}] must be {{requested_at, through_lp, group, segments[, epochs, plan, membership]}}"
            )
        membership = record.get("membership")
        # 开轮时每个批次的成员指纹：续跑逐批核对，批数相同而边界挪了（中间某行后来读不出）也认得出
        if membership is not None and not (
            isinstance(membership, Mapping) and set(membership) == {"group", "segments"}
            and all(
                isinstance(membership[part], list)
                and isinstance(record[part], Mapping) and len(membership[part]) == len(record[part])
                and all(isinstance(value, str) and value for value in membership[part])
                for part in ("group", "segments")
            )
        ):
            raise SpoolStateError(
                f"digest_writes[{run}].membership must be {{group, segments}} lists matching the batch counts"
            )
        plan = record.get("plan", {})
        # 开轮时的切批参数（句数上限、每批句数）：升级改了常量之后续跑仍按原计划切批
        if not isinstance(plan, Mapping) or set(plan) - {"max_lines", "batch_size"} or not all(
            _is_int(value) and value >= 1 for value in plan.values()
        ):
            raise SpoolStateError(f"digest_writes[{run}].plan must be {{max_lines, batch_size}} ints >= 1")
        epochs = record.get("epochs", {})
        # 开轮时记下的各 subject 清除代数：同键重试沿用，服务端按它丢弃清除之前发起的产物
        if not isinstance(epochs, Mapping) or not all(
            isinstance(key, str) and key and _is_int(value) and value >= 0
            for key, value in epochs.items()
        ):
            raise SpoolStateError(f"digest_writes[{run}].epochs must map subject keys to ints >= 0")
        if not _is_number(record["requested_at"]):
            raise SpoolStateError(f"digest_writes[{run}].requested_at must be a number")
        if not _is_int(record["through_lp"]) or not 0 <= record["through_lp"] <= VISIT_LP_MAX:
            # 越界的水位没有任何合法行够得着；digested_through_lp 必须等于某轮的 through_lp
            # （或 -1），所以它也随之限定在 -1..VISIT_LP_MAX
            raise SpoolStateError(f"digest_writes[{run}].through_lp must be an int in 0..VISIT_LP_MAX")
        _check_batch_map(record["group"], f"digest_writes[{run}].group")
        _check_batch_map(record["segments"], f"digest_writes[{run}].segments")
    # 每轮从上一轮的水位之后开始，through_lp 严格递增；digested_through_lp 只在一轮全部
    # 完成时推进到该轮 through_lp。对不上的水位会让结清判定与补录各说各话：恢复以为
    # 已抽到 999，却没有任何一轮覆盖那些句子，转录照样被删
    previous = -1
    for i in range(len(runs)):
        through = runs[str(i)]["through_lp"]
        if through <= previous:
            raise SpoolStateError("digest_writes through_lp must increase run by run")
        previous = through
    completed = state["digest_runs"]
    expected = runs[str(completed - 1)]["through_lp"] if completed else -1
    if state["digested_through_lp"] != expected:
        raise SpoolStateError("digested_through_lp does not match the last completed digest run")
    return copy.deepcopy(dict(state))


def is_digestable(state: Mapping[str, Any]) -> bool:
    """Whether this visit's lines may be digested into the visit memory region.

    Exactly ``state.json.memory_enabled``: the ``visitMemoryEnabled`` value
    frozen when the visit activated. The current configuration is never
    consulted, so a mid-visit change only affects the next visit.
    """
    return state.get("memory_enabled") is True


def region_settled(state: Mapping[str, Any]) -> bool:
    """Whether the visit-region digest and the last-visit summary are both done.

    With memory on, at least one digest run must be registered, every
    registered run must be counted in ``digest_runs`` (a run still in
    progress is not settled), and every run must have at least one group and
    one segments batch, all complete. An empty batch map means the batches
    are not registered yet, not that they are done.
    """
    if state.get("last_summary_done") is not True:
        return False
    if not is_digestable(state):
        return True
    runs = state.get("digest_writes") or {}
    if not runs or len(runs) != state.get("digest_runs"):
        return False
    for record in runs.values():
        for part in ("group", "segments"):
            batches = record.get(part) or {}
            # 空表 = 批次还没登记，不是「全部完成」：先登记 run 再拆批次的写入顺序下，
            # 两步之间点「不记」不能把还没抽取的转录删掉
            if not batches or not all(batches.values()):
                return False
    return True


def debrief_final(state: Mapping[str, Any]) -> bool:
    """Whether the debrief reached a final outcome (or never applies: memory off).

    Final means ``diary`` with both writes done, ``forget`` or ``abandoned``.
    ``committing:diary`` / ``commit_failed:diary`` are *not* final: a retry is
    pending or the user still has to choose.
    """
    if not is_digestable(state):
        return True
    choice = state.get("debrief_choice")
    if choice == "diary":
        writes = state.get("debrief_writes") or {}
        return writes.get("facts") is True and writes.get("cache") is True
    return choice in ("forget", "abandoned")


def debrief_releases_transcript(state: Mapping[str, Any]) -> bool:
    """Whether the debrief no longer needs the ``.jsonl``.

    True once :func:`debrief_final`, and also in ``committing:diary`` /
    ``commit_failed:diary``: from then on retries only use
    ``state.json.debrief_pending``, which stays pinned (see
    :func:`debrief_pins_state`).
    """
    return debrief_final(state) or debrief_pins_state(state)


def debrief_pins_state(state: Mapping[str, Any] | None) -> bool:
    """Whether ``state.json`` must survive both sweeps: a diary commit is in flight or failed."""
    return bool(state) and state.get("debrief_choice") in _COMMIT_PINNED_CHOICES


def transcript_releasable(state: Mapping[str, Any]) -> bool:
    """Region settled and the debrief done with the transcript: the ``.jsonl`` may be reclaimed.

    Not "the visit is over": a visit still committing (or failed) its diary
    is releasable while :func:`debrief_final` is False and
    :func:`debrief_pins_state` keeps its ``state.json``.
    """
    return region_settled(state) and debrief_releases_transcript(state)


@dataclass
class SpoolContents:
    """What a spool replay recovered: the header, the complete lines, the drops."""

    header: dict | None
    lines: list[dict] = field(default_factory=list)
    dropped_lines: int = 0


# ── 文件辅助（工作线程内）──────────────────────────────────────────────


def _spool_dir(config_dir: str | Path) -> Path:
    return Path(config_dir) / VISIT_SPOOL_DIRNAME


def _split_name(name: str) -> tuple[str, str] | None:
    """Return ``(visit_id, suffix)`` for a spool directory file name, else ``None``."""
    visit_id, suffix = name[:_VISIT_ID_LEN], name[_VISIT_ID_LEN:]
    if suffix not in _KNOWN_SUFFIXES or not VISIT_ID_RE.fullmatch(visit_id):
        return None
    return visit_id, suffix


def _list_names(spool_dir: Path) -> list[tuple[str, str, Path]]:
    """``(visit_id, suffix, path)`` of every spool-shaped name, without touching the files."""
    try:
        names = os.listdir(spool_dir)
    except FileNotFoundError:
        return []
    out = []
    for name in names:
        parsed = _split_name(name)
        if parsed is not None:
            out.append((parsed[0], parsed[1], spool_dir / name))
    return out


def _scan(spool_dir: Path) -> list[tuple[str, str, Path, os.stat_result]]:
    """:func:`_list_names` plus ``stat``; an entry whose ``stat`` fails is logged and skipped."""
    out = []
    for visit_id, suffix, path in _list_names(spool_dir):
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            # 符号链接环 / 无权限之类的坏项只跳过它自己：整轮清扫、outbox 清理不能被一个
            # 长期坏掉的条目卡住
            logger.warning("visit spool: cannot stat %s (%s); skipping it this pass", path.name, exc)
            continue
        out.append((visit_id, suffix, path, st))
    return out


def _raw_state_may_name(
    path: Path, own_char_uid: str, pair_ids: frozenset[str], own_uid: str | None = None,
) -> bool:
    """Whether a parseable but schema-invalid ``state.json`` may belong to this (character, pair).

    False only when its raw fields clearly name another character or another
    pair; anything unclear counts as "may be ours" (fail closed).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, RecursionError):
        return True
    if not isinstance(raw, dict):
        return True
    char = raw.get("own_char_uid")
    if isinstance(char, str) and char and char != own_char_uid:
        return False
    account = raw.get("own_uid")
    if own_uid is not None and isinstance(account, str) and account and account != own_uid:
        # 别的账号下的场次：与这次清除无关（对端身份已抹、pair_id 为空时也能凭它排除）
        return False
    pair = raw.get("pair_id")
    return not (isinstance(pair, str) and pair and pair not in pair_ids)


def _raw_state_names_pair(
    path: Path, own_char_uid: str, pair_ids: frozenset[str], own_uid: str | None = None,
) -> bool:
    """Whether a parseable but schema-invalid ``state.json`` explicitly names this (character, pair)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, RecursionError):
        return False
    if not isinstance(raw, dict) or not _names_pair(raw, own_char_uid, pair_ids):
        return False
    account = raw.get("own_uid")
    return own_uid is None or not (isinstance(account, str) and account and account != own_uid)


def _read_state_file(path: Path) -> dict | None:
    # 文件名就是场次：state 里没有对得上的 visit_id 时（文件被换过 / 复制过）不能信它的归属，
    # 退役、清除、改名都会按它去动别的场次的文件
    expected = path.name[: -len(STATE_SUFFIX)] if path.name.endswith(STATE_SUFFIX) else None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except RecursionError as exc:
        # 深层嵌套的 state.json：归入已有的「损坏」处理（ValueError），不能冲断整轮清扫 / 重放
        raise SpoolStateError(f"{path.name} is too deeply nested") from exc
    if expected is None:
        raise SpoolStateError(f"{path.name} is not a state file name")
    return validate_state(data, visit_id=expected)


def _retention_exempt(state_path: Path, age_s: float) -> bool:
    """Whether a visit's ``state.json`` must survive the 7-day expiry this round.

    True while a diary commit is in flight or failed permanently
    (``committing:diary`` / ``commit_failed:diary``): ``debrief_pending``
    and the write progress are the only basis of the retry. When
    ``state.json`` exists but cannot be read (``OSError``, e.g. locked by
    antivirus or backup) the visit is kept for at most one more retention
    period (``age_s`` below twice the retention), so a transient lock never
    deletes a half-committed diary while a permanently unreadable state
    cannot pin its files forever. A missing or corrupt (schema-invalid)
    state follows the normal expiry.
    """
    try:
        state = _read_state_file(state_path)
    except ValueError:
        return False
    except OSError as exc:
        keep = age_s < 2 * _RETENTION_S
        logger.warning("visit spool: state %s unreadable (%s); %s",
                       state_path.name, exc, "keeping it this sweep" if keep else "reclaiming")
        return keep
    return debrief_pins_state(state)


def _names_pair(doc: dict | None, own_char_uid: str, pair_ids: frozenset[str]) -> bool:
    return (
        doc is not None
        and doc.get("own_char_uid") == own_char_uid
        and doc.get("pair_id") in pair_ids
    )


def _try_read_state(path: Path) -> dict | None:
    try:
        return _read_state_file(path)
    except (OSError, ValueError) as exc:
        logger.warning("visit spool: unreadable state %s: %s", path.name, exc)
        return None


def _read_header_strict(path: Path, *, validate: bool = True) -> dict | None:
    """Read a spool header for the forget path: ``None`` only when the file is absent.

    A truncated (no newline), unparseable or non-object first line raises
    ``ValueError``; other read errors raise ``OSError``. ``validate=False``
    only requires a well-formed object (ownership lookups must still accept
    headers of older versions that lack ``own_char_uid``).
    """
    try:
        with open(path, "rb") as f:
            first = f.readline()
    except FileNotFoundError:
        return None
    if not first.endswith(b"\n"):
        raise ValueError(f"spool header of {path.name} is truncated")
    try:
        header = json.loads(first)
    except RecursionError as exc:
        raise ValueError(f"spool header of {path.name} is too deeply nested") from exc
    if not isinstance(header, dict):
        raise ValueError(f"spool header of {path.name} is not an object")
    # 完整 schema 校验（peer 字段允许被「清除这个人」置空）：{} 之类缺字段的头行
    # 不能被当作「不是这一对」
    return validate_header(header) if validate else header


def _rewrite_header(path: Path, mutate, *, strict: bool = False) -> bool:
    """Atomically rewrite the first line of a spool file; return whether it changed.

    Raises :class:`SpoolBusy` when the file is still open for appends in this
    process (an in-flight visit). The check and the replacement run under
    the same lock as the writer's register-then-open, so a spool cannot be
    opened in between.
    """
    with _OPEN_SPOOLS_LOCK:
        if _spool_key(path) in _OPEN_SPOOLS:
            raise SpoolBusy(f"spool {path.name} is still being written")
        return _rewrite_header_locked(path, mutate, strict)


def _rewrite_header_locked(path: Path, mutate, strict: bool) -> bool:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return False
    idx = data.find(b"\n")
    header: Any = None
    if idx >= 0:
        try:
            header = json.loads(data[:idx])
        except (ValueError, RecursionError):
            header = None
    if not isinstance(header, dict):
        if strict:
            # 清除路径：头行坏了就不能当作「已抹掉」，留给重放（文件 7 天后被 sweep 回收）
            raise SpoolStateUnreadable([path.name])
        return False
    if not mutate(header):
        return False
    atomic_write_bytes(path, _encode_line(header) + data[idx + 1:])
    return True


def _unlink_unless_open(path: Path) -> bool:
    """Unlink a spool ``.jsonl`` unless some writer in this process holds it open.

    The registry lock is held through the unlink, the same exclusion the
    header rewrite uses, so a writer cannot register in between.
    """
    with _OPEN_SPOOLS_LOCK:
        if _spool_key(path) in _OPEN_SPOOLS:
            return False
        return _unlink(path)


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _sweep_unlink(path: Path) -> bool:
    """:func:`_unlink` for the sweep: any other ``OSError`` is logged and the file kept."""
    try:
        return _unlink(path)
    except OSError as exc:
        # 被占用（Windows 共享冲突）/ 没权限 / 名字像转录的目录：留到下一轮，不能中断整轮清扫，
        # 否则一个长期锁住的旧文件会让所有其他回收都做不了
        logger.warning("visit spool: could not delete %s (%s); keeping it for a later sweep",
                       path.name, exc)
        return False


def _parse_spool_bytes(data: bytes, visit_id: str) -> SpoolContents:
    parts = data.split(b"\n")
    tail = parts.pop()  # 以换行结尾时为空串
    dropped = 0
    if tail:
        dropped += 1
        logger.warning(
            "visit spool %s: dropped a partial trailing line (%d bytes)", visit_id, len(tail)
        )
    header: dict | None = None
    lines: list[dict] = []
    for index, raw in enumerate(parts):
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            # 能解析的对象也要过写入时同一套 schema：缺字段 / 说话人非法 / lp 越界的行
            # 不能当作恢复出来的转录交给补录与上传
            if index == 0:
                obj = validate_header(obj)
                if obj["visit_id"] != visit_id:
                    # 被改名 / 换过的文件：别的场次的头行与转录不能挂到这一场名下
                    logger.error("visit spool %s: header belongs to another visit, ignoring the file",
                                 visit_id)
                    return SpoolContents(header=None, lines=[], dropped_lines=len(parts) + (1 if tail else 0))
            else:
                encode_spool_line(obj)
        except (ValueError, RecursionError):
            dropped += 1
            logger.warning("visit spool %s: dropped an unreadable line #%d", visit_id, index)
            continue
        if index == 0:
            header = obj
        else:
            lines.append(obj)
    return SpoolContents(header=header, lines=lines, dropped_lines=dropped)


# ── VisitSpool ──────────────────────────────────────────────────────────


class VisitSpool:
    """The spool and ``state.json`` of one visit.

    Lifecycle of the transcript part: :meth:`open` once, :meth:`append` per
    spoken line, :meth:`fsync` whenever :meth:`fsync_due`, :meth:`close` at
    finalize. ``state.json`` methods work whether or not the spool is open.
    """

    def __init__(self, config_dir: str | Path, visit_id: str) -> None:
        self.config_dir = Path(config_dir)
        self.visit_id = require_visit_id(visit_id)
        self.spool_dir = _spool_dir(config_dir)
        self.jsonl_path = visit_path(self.spool_dir, self.visit_id, SPOOL_SUFFIX)
        self.state_path = visit_path(self.spool_dir, self.visit_id, STATE_SUFFIX)
        self._fd: int | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._last_fsync: float = 0.0
        self._dirty = False

    # ── 转录写入 ──

    @property
    def is_open(self) -> bool:
        """Whether this instance currently holds the spool's writer fd."""
        return self._fd is not None

    def _submit(self, fn, *args) -> asyncio.Future:
        if self._executor is None:
            raise RuntimeError("visit spool is not open")
        return asyncio.wrap_future(self._executor.submit(fn, *args))

    def _open_sync(self, data: bytes) -> int:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | _O_BINARY
        key = _spool_key(self.jsonl_path)
        # 先登记再打开、与头行改写同一把锁：改写方要么看到登记而报 SpoolBusy，
        # 要么在登记之前就已替换完文件（此时 O_EXCL 打开会失败）
        with _OPEN_SPOOLS_LOCK:
            if key in _OPEN_SPOOLS:
                # 别的实例正在写这一场：不能先 add 再在失败分支里 discard——那会撤掉
                # 对方的登记，之后的清除 / 改名就会在对方写入时替换掉文件
                raise SpoolBusy(f"spool {self.jsonl_path.name} is already open for appends")
            _OPEN_SPOOLS.add(key)
            try:
                fd = os.open(self.jsonl_path, flags, 0o600)
            except BaseException:
                _OPEN_SPOOLS.discard(key)
                raise
        try:
            self._write_all(fd, data)
            os.fsync(fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError as exc:
                # close 自己报错（网络盘 / U 盘 EIO）不能跳过下面的删文件与撤登记——否则这场
                # 在进程存活期间永远 SpoolBusy；也不能盖掉原来的异常
                logger.warning("visit spool %s: closing the failed new spool failed: %s",
                               self.visit_id, exc)
            with _OPEN_SPOOLS_LOCK:
                # 登记还在时删掉刚建的文件：留着（可能只有半截头行）的话，同一场重试的
                # O_EXCL 打开永远 FileExistsError，一次暂时性磁盘错误就让这场再也开不了
                try:
                    os.unlink(self.jsonl_path)
                except OSError as exc:
                    logger.warning("visit spool %s: could not remove the failed new spool: %s",
                                   self.visit_id, exc)
                _OPEN_SPOOLS.discard(key)
            raise
        return fd

    @staticmethod
    def _write_all(fd: int, data: bytes) -> None:
        # 一次 write 写完整行；普通文件极少短写，短写时续写剩余部分。
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]

    def _append_sync(self, data: bytes) -> None:
        fd = self._fd
        if fd is None:
            raise RuntimeError("visit spool is closed")
        self._write_all(fd, data)

    def _fsync_sync(self) -> None:
        if self._fd is not None:
            os.fsync(self._fd)

    def _close_sync(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            # 撤登记放在最外层 finally：fsync / close 任一报错（网络盘、U 盘 EIO）都不能
            # 跳过它——_fd 已清空、执行器随后丢弃，这个实例没有第二次机会，这场会在进程
            # 存活期间一直 SpoolBusy。fsync 已失败时 close 的报错只记日志，抛原来的异常
            try:
                try:
                    os.fsync(fd)
                except BaseException:
                    try:
                        os.close(fd)
                    except OSError as exc:
                        logger.warning("visit spool %s: close after a failed fsync failed: %s",
                                       self.visit_id, exc)
                    raise
                os.close(fd)
            finally:
                with _OPEN_SPOOLS_LOCK:
                    _OPEN_SPOOLS.discard(_spool_key(self.jsonl_path))

    def _discard_orphan_open(self, fut: "concurrent.futures.Future[int]") -> None:
        """Undo an ``_open_sync`` whose awaiting ``open`` was cancelled.

        Closes the fd, deletes the file (it was created by this very
        ``O_EXCL`` open, so it holds only the header) and drops the in-flight
        registration, so a retry with the same visit id can open again.
        """
        if fut.cancelled() or fut.exception() is not None:
            return
        try:
            os.close(fut.result())
        except OSError as exc:
            # fd 已失效也无妨：这里只负责不泄漏；登记照样要撤销
            logger.debug("visit spool: closing orphan fd failed: %s", exc)
        with _OPEN_SPOOLS_LOCK:
            # 文件是这次 O_EXCL 新建的、只有头行（含对端身份）：留着会让同 visit 重试
            # 抛 FileExistsError，也会把对端信息留到 7 天回收
            try:
                _unlink(self.jsonl_path)
            except OSError as exc:
                logger.warning("visit spool: removing orphan %s failed: %s",
                               self.jsonl_path.name, exc)
            _OPEN_SPOOLS.discard(_spool_key(self.jsonl_path))

    async def open(self, header: Mapping[str, Any], *, now: float) -> None:
        """Create ``<visit_id>.jsonl`` (``O_EXCL``, ``0o600``) and write the header line.

        ``header`` must carry exactly ``HEADER_FIELDS`` with ``v == 1`` and this
        spool's ``visit_id``. ``now`` (required) seeds the fsync cadence and
        must come from the same clock later passed to :meth:`fsync_due` /
        :meth:`fsync` (any clock, used consistently; the header's
        ``started_at`` is wall time and is not used for this).
        """
        if self._executor is not None:
            raise RuntimeError("visit spool already open")
        clean = validate_header(header)
        if clean["visit_id"] != self.visit_id:
            raise ValueError("spool header visit_id does not match this spool")
        data = _encode_line(clean)
        if len(data) > VISIT_SPOOL_LINE_MAX_BYTES:
            raise SpoolLineTooLarge("spool header too large")
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"visit-spool-{self.visit_id[:6]}"
        )
        fut = executor.submit(self._open_sync, data)
        try:
            self._fd = await asyncio.wrap_future(fut)
        except BaseException:
            # 取消时 worker 可能已经在跑 _open_sync：它返回的 fd 没人接，
            # 也已登记成「在写」。等它结束后关掉 fd、撤销登记，免得之后的
            # 清除 / 改名一直报 SpoolBusy
            fut.add_done_callback(self._discard_orphan_open)
            executor.shutdown(wait=False)
            raise
        self._executor = executor
        # 计时只用调用方的时钟：started_at 是墙钟，runtime 若用单调时钟驱动，
        # 拿它做起点会让 fsync_due 整场返回 False
        self._last_fsync = float(now)
        self._dirty = False

    async def append(self, line: Mapping[str, Any]) -> None:
        """Append one spoken line (see :func:`encode_spool_line`) as a single write."""
        data = encode_spool_line(line)
        fut = self._submit(self._append_sync, data)
        self._dirty = True
        # 已接受并提交的一行不能因调用方被取消而撤回（排在别的写入后面时会被直接取消）
        await asyncio.shield(fut)

    def fsync_due(self, now: float) -> bool:
        """Whether unsynced lines exist and ``VISIT_SPOOL_FSYNC_S`` passed since the last fsync.

        ``now`` must come from the same clock as the ``now`` given to :meth:`open`.
        """
        return (
            self._fd is not None
            and self._dirty
            and now - self._last_fsync >= VISIT_SPOOL_FSYNC_S
        )

    async def fsync(self, now: float) -> None:
        """Flush the spool to disk on the writer thread (after every queued append).

        On failure the spool stays dirty and the cadence is not advanced, so
        :meth:`fsync_due` keeps asking for a retry.
        """
        fut = self._submit(self._fsync_sync)
        # 提交前先清标志：提交之后才到的 append 会重新置脏，不会被这次成功误清
        self._dirty = False
        previous = self._last_fsync
        self._last_fsync = now
        try:
            await fut
        except BaseException:
            self._dirty = True
            self._last_fsync = previous
            raise

    async def close(self) -> None:
        """Fsync and close the spool (the finalize fsync); idempotent.

        Cancelling the caller does not cancel the close itself: the worker
        task may still be queued behind an append, and dropping it would leak
        the fd and leave the path registered as in flight (later forget /
        rename would report ``SpoolBusy`` until restart).
        """
        executor = self._executor
        if executor is None:
            return
        try:
            await asyncio.shield(asyncio.wrap_future(executor.submit(self._close_sync)))
        finally:
            self._executor = None
            executor.shutdown(wait=False)

    async def read_back(self) -> SpoolContents:
        """Replay the spool from disk.

        A partial trailing line left by a crash (no newline) and any line that
        does not parse or fails the schema (:func:`validate_header` for the
        first line, :func:`encode_spool_line` for the others) are dropped,
        counted in ``dropped_lines`` and logged; every valid line is returned.
        A file whose header names another ``visit_id`` (renamed or swapped)
        is ignored as a whole: ``header`` is None, no line is returned and
        every line counts as dropped.
        """

        def read() -> SpoolContents:
            # 读与解析都在工作线程：长场次的转录可能有几十 MB，逐行 JSON 解码与校验放在
            # 事件循环上会卡住在飞的 WebSocket
            try:
                data = self.jsonl_path.read_bytes()
            except FileNotFoundError:
                return SpoolContents(header=None)
            return _parse_spool_bytes(data, self.visit_id)

        return await asyncio.to_thread(read)

    # ── state.json ──

    def _write_state_sync(self, state: Mapping[str, Any]) -> dict:
        if not isinstance(state, Mapping):
            validate_state(state)    # 抛出统一的 SpoolStateError
        given = state.get("visit_id")
        if given is not None and given != self.visit_id:
            raise SpoolStateError("state visit_id does not match this spool")
        if "visit_id" in state:
            state = {**state, "visit_id": self.visit_id}
        clean = validate_state(state, visit_id=self.visit_id)
        with path_lock(self.state_path):
            atomic_write_json(self.state_path, clean)
        return clean

    async def write_state(self, state: Mapping[str, Any]) -> dict:
        """Validate and atomically write ``state.json``; return the written copy.

        The document is bound to this spool's visit: a ``None``
        ``visit_id`` is filled in (the key itself is required like every
        other field), a different one raises
        :class:`SpoolStateError`. Reads require the stored ``visit_id`` to
        match the file name, so a swapped or copied ``state.json`` is treated
        as corrupt instead of lending its ownership to another visit.
        """
        return await asyncio.to_thread(self._write_state_sync, state)

    async def read_state(self) -> dict | None:
        """Read and validate ``state.json``; ``None`` when it does not exist."""
        return await asyncio.to_thread(_read_state_file, self.state_path)

    def _update_state_sync(self, mutate) -> dict:
        with path_lock(self.state_path):
            state = _read_state_file(self.state_path)
            if state is None:
                raise FileNotFoundError(str(self.state_path))
            mutate(state)
            clean = validate_state(state, visit_id=self.visit_id)
            atomic_write_json(self.state_path, clean)
            return clean

    async def update_state(self, **changes: Any) -> dict:
        """Read-modify-write ``state.json`` with ``changes``; the result is validated."""

        def mutate(state: dict) -> None:
            state.update(copy.deepcopy(changes))

        return await asyncio.to_thread(self._update_state_sync, mutate)

    def _delete_if_settled_sync(self) -> bool:
        # 与头行改写同一套逐路径锁（先 jsonl 后 state）：改写方读完转录、还没替换时
        # 这里删掉，它随后的原子替换会把刚判定可删的转录复活
        with path_lock(self.jsonl_path), path_lock(self.state_path):
            state = _read_state_file(self.state_path)
            if state is None or not transcript_releasable(state):
                return False
            # 另一个实例可能还持有这场的写入 fd（关闭排在队列里）：删掉后它的追加
            # 会写进已删除的 inode、关闭时一起消失
            return _unlink_unless_open(self.jsonl_path)

    async def delete_if_settled(self) -> bool:
        """Delete ``.jsonl`` once :func:`transcript_releasable` holds.

        Called by ``mark_forget`` and, later, by whichever of the digest commit
        and the last-summary commit finishes last. Returns whether it deleted.
        """
        if self._fd is not None:
            return False
        return await asyncio.to_thread(self._delete_if_settled_sync)

    async def mark_forget(self, *, final_choice: str = "forget") -> bool:
        """Record a final debrief choice that writes nothing more: ``forget`` or ``abandoned``.

        ``final_choice='forget'`` (default) records "do not record" (no
        private memory is written). ``final_choice='abandoned'`` records the
        user abandoning a permanently
        failed diary commit instead: allowed only from ``commit_failed:diary``
        (or again from ``abandoned``); the step already written is not undone
        and the spool is deleted by the same rule, the final choice staying
        ``abandoned`` meanwhile. ``forget`` is allowed from ``null`` /
        ``ask_later`` / ``generating:diary`` / ``preview:diary`` (or again
        from ``forget``). Any other state raises :class:`SpoolStateError`.

        The ``.jsonl`` is deleted right away only when every digest batch of
        every run and the last-visit summary are done; otherwise it is kept
        and deleted by :meth:`delete_if_settled` once they are, because
        "do not record" never cancels the visit-region digest. Returns whether
        the ``.jsonl`` was deleted now.
        """

        if final_choice not in _FORGET_SOURCES:
            raise ValueError("final_choice must be 'forget' or 'abandoned'")

        def mutate(state: dict) -> None:
            if state["debrief_choice"] not in _FORGET_SOURCES[final_choice]:
                # 已开始 / 已完成的提交不能改记成「不记」；放弃只对永久性失败开放
                raise SpoolStateError(
                    f"cannot record {final_choice} from debrief_choice={state['debrief_choice']!r}"
                )
            state["debrief_choice"] = final_choice
            state["debrief_pending"] = None
            state["debrief_retry"] = None
            state["debrief_chip_pending"] = False

        async def txn() -> bool:
            await asyncio.to_thread(self._update_state_sync, mutate)
            return await self.delete_if_settled()

        # 记「不记」与删已结清转录是一个事务：调用方在写 state 途中被取消时照常做完，
        # 否则 forget 已落盘、转录却没人再删（已结清的场次没有后续回调会重试）
        return await asyncio.shield(txn())

    def _delete_peer_fields_sync(self) -> None:
        def clear_header(header: dict) -> bool:
            changed = False
            for name in _HEADER_PEER_FIELDS:
                if header.get(name) is not None:
                    header[name] = None
                    changed = True
            return changed

        with path_lock(self.jsonl_path):
            _rewrite_header(self.jsonl_path, clear_header, strict=True)
        with path_lock(self.state_path):
            state = _read_state_file(self.state_path)
            if state is not None and any(state[n] is not None for n in _PEER_IDENTITY_FIELDS):
                for name in _PEER_IDENTITY_FIELDS:
                    state[name] = None
                atomic_write_json(self.state_path, validate_state(state))

    async def delete_peer_fields(self) -> None:
        """Erase ``peer_uid / pair_id / peer_char_id`` from ``state.json`` and the spool header.

        The local "forget this person" counterpart of removing the roster
        entry. Idempotent. Must not run while this instance holds the writer
        fd (the header rewrite replaces the file).
        """
        if self._fd is not None:
            raise RuntimeError("cannot rewrite the header of an open spool")
        await asyncio.to_thread(self._delete_peer_fields_sync)

    # ── 目录级操作（类方法）──

    @classmethod
    def _owner_of(cls, spool_dir: Path, visit_id: str) -> tuple[str | None, str | None]:
        """Return ``(own_char_uid, own_char)`` from ``state.json``, else from the header.

        Strict: an existing but unreadable / schema-invalid ``state.json`` or
        header raises (``OSError`` / ``ValueError``) instead of reading as
        "owned by nobody", so transactional callers keep their marker.
        """
        state = _read_state_file(visit_path(spool_dir, visit_id, STATE_SUFFIX))
        if state is not None:
            return state["own_char_uid"], state["own_char"]
        header = _read_header_strict(visit_path(spool_dir, visit_id, SPOOL_SUFFIX),
                                     validate=False)
        if header is not None:
            uid = header.get("own_char_uid")
            name = header.get("own_char")
            # 旧版本头行可以没有 own_char_uid（按名字回退），但有就必须是非空字符串；
            # own_char 也必须是非空字符串——坏值既匹配不上 uid 也走不了名字回退，
            # 退役会「成功」结束而把这个角色的转录留在磁盘上
            if "own_char_uid" in header and (not isinstance(uid, str) or not uid):
                raise ValueError(f"spool header of {visit_id} has a malformed own_char_uid")
            if not isinstance(name, str) or not name:
                raise ValueError(f"spool header of {visit_id} has a malformed own_char")
            return uid, name
        return None, None

    @classmethod
    async def list_visit_ids(cls, config_dir: str | Path, suffixes: Iterable[str]) -> list[str]:
        """Return the sorted visit ids that have a file with one of ``suffixes`` (names only, no stat)."""
        return await asyncio.to_thread(cls._visit_ids, _spool_dir(config_dir), tuple(suffixes))

    @classmethod
    def _visit_ids(cls, spool_dir: Path, suffixes: Iterable[str]) -> list[str]:
        wanted = set(suffixes)
        # 只看名字、不 stat：stat 不了的场次也要列出来，清除 / 退役 / 改名在逐场处理时
        # 各自 fail closed，不能因为扫描跳过它而漏掉
        return sorted({vid for vid, suffix, _p in _list_names(spool_dir) if suffix in wanted})

    @classmethod
    def _retire_char_sync(
        cls, config_dir: Path, character_uid: str, legacy_name: str | None
    ) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        retired = []
        unreadable: list[str] = []
        busy: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            try:
                owner_uid, owner_name = cls._owner_of(spool_dir, visit_id)
            except (OSError, ValueError):
                # 读不出归属不能当「不是这个角色的」：上抛让删除事务保留退役标记、启动对账重试
                unreadable.append(visit_id)
                continue
            if owner_uid:
                match = owner_uid == character_uid
            else:
                match = legacy_name is not None and owner_name == legacy_name
            if not match:
                continue
            jsonl = visit_path(spool_dir, visit_id, SPOOL_SUFFIX)
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # 与 state / 头行写者同一套逐路径锁（先 jsonl 后 state）：正在读改写
            # state.json 的更新要么先做完再被删掉，要么之后读到「不存在」而报错，
            # 不会在退役之后把文件重新写回来
            with path_lock(jsonl), path_lock(state_path), _OPEN_SPOOLS_LOCK:
                # 与改名 / 清除同一语义：仍在写的场次跳过，最后报 SpoolBusy 让事务保留
                # 标记、等这场结束后重放；删除本身失败（Windows 上被占用的 PermissionError）
                # 并入读不出，不能冲出循环卡住其余场次
                if _spool_key(jsonl) in _OPEN_SPOOLS:
                    busy.append(visit_id)
                    continue
                try:
                    _unlink(jsonl)
                    _unlink(state_path)
                except OSError:
                    unreadable.append(visit_id)
                    continue
            retired.append(visit_id)
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
        if busy:
            raise SpoolBusy(f"spools still being written: {', '.join(busy)}")
        return retired

    @classmethod
    async def retire_char(
        cls,
        config_dir: str | Path,
        character_uid: str,
        *,
        legacy_name: str | None = None,
    ) -> list[str]:
        """Delete the ``.jsonl`` and ``state.json`` of every visit owned by a deleted character.

        Ownership is ``own_char_uid == character_uid``. Visits whose files
        lack ``own_char_uid`` (older versions) fall back to ``own_char ==
        legacy_name``; the caller passes ``legacy_name`` only while no new
        character of the same name exists. ``.upload.json(l)`` and
        ``visit_reports/`` are never touched. Returns the retired visit ids.
        After handling every other visit, raises :class:`SpoolStateUnreadable`
        when some visit's ownership cannot be read or its files cannot be
        deleted, else :class:`SpoolBusy` when some owned visit is still open
        for appends (retry after it closes).
        """
        return await asyncio.to_thread(
            cls._retire_char_sync, Path(config_dir), character_uid, legacy_name
        )

    @classmethod
    def _rename_own_char_sync(cls, config_dir: Path, old: str, new: str) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        renamed = []

        def fix_header(header: dict) -> bool:
            if header.get("own_char") == old:
                header["own_char"] = new
                return True
            return False

        unreadable: list[str] = []
        busy: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            changed = False
            jsonl = visit_path(spool_dir, visit_id, SPOOL_SUFFIX)
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # 改名是事务的一步：读不出来的场次不能静默跳过，否则 pending_rename 被清掉、
            # 这场留在已不存在的旧名下。先改完能改的，最后上抛让标记保留、对账重跑
            try:
                with path_lock(jsonl):
                    changed |= _rewrite_header(jsonl, fix_header, strict=True)
                with path_lock(state_path):
                    state = _read_state_file(state_path)
                    if state is not None and state["own_char"] == old:
                        state["own_char"] = new
                        atomic_write_json(state_path, validate_state(state))
                        changed = True
            except SpoolBusy:
                busy.append(visit_id)
            except (OSError, ValueError, SpoolStateUnreadable):
                unreadable.append(visit_id)
            if changed:
                renamed.append(visit_id)
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
        if busy:
            raise SpoolBusy(f"spools still being written: {', '.join(busy)}")
        return renamed

    @classmethod
    async def rename_own_char(cls, config_dir: str | Path, old: str, new: str) -> list[str]:
        """Rewrite ``own_char`` from ``old`` to ``new`` in every spool header and ``state.json``.

        Visits whose ``.jsonl`` is already gone are found through
        ``state.json.own_char``. Visits already carrying ``new`` are skipped,
        so the call is idempotent and safe to rerun from startup
        reconciliation (and to run as ``new -> old`` for a rollback). Returns
        the visit ids that changed. Only allowed while no visit is in flight.
        Raises :class:`SpoolStateUnreadable` (after rewriting every readable
        visit) when some header or ``state.json`` cannot be read.
        """
        if not old or not new or old == new:
            return []
        return await asyncio.to_thread(cls._rename_own_char_sync, Path(config_dir), old, new)

    @classmethod
    def _find_visits_sync(
        cls, config_dir: Path, own_char_uid: str, pair_ids: frozenset[str],
        corrupt_wiped: list[str] | None = None, own_uid: str | None = None,
        strict: bool = True,
    ) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        found = []
        unreadable: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            # 清除路径要严格读：已结清的场次常常只剩 state.json，读不出来就跳过
            # 会让 wipe_spool 记完成、撤销日志被删，而 peer 字段仍留在文件里
            state_unreadable = False
            state_corrupt = False
            schema_excludes = False
            schema_names = False
            try:
                state = _read_state_file(visit_path(spool_dir, visit_id, STATE_SUFFIX))
            except FileNotFoundError:
                state = None
            except SpoolStateError:
                # 能解析、只是不合当前 schema（比如降级后读到新版本写的 state）：别的版本还读得了，
                # 不是「谁都用不了」，绝不删。先记下它的原始字段是否明确排除这次清除，
                # 头行照常检查（头行仍指认这一对时照样要清它的对端字段）
                schema_excludes = not _raw_state_may_name(
                    visit_path(spool_dir, visit_id, STATE_SUFFIX), own_char_uid, pair_ids, own_uid,
                )
                schema_names = _raw_state_names_pair(
                    visit_path(spool_dir, visit_id, STATE_SUFFIX), own_char_uid, pair_ids, own_uid,
                )
                state = None
                state_unreadable = True
            except ValueError:
                # JSON 本身坏了：谁都用不了它（与 OSError 的一时读不出不同）
                state = None
                state_unreadable = True
                state_corrupt = True
            except OSError:
                # state 读不出时先看头行：头行明确属于别的角色、或指认的是别的一对，就不是
                # 这次要清的场次——一份无关的坏文件不能把所有清除永远卡住。头行也读不出、
                # 或身份已被抹掉而角色相同（分不清是不是这个人）时才按读不出处理
                state = None
                state_unreadable = True
            if _names_pair(state, own_char_uid, pair_ids):
                found.append(visit_id)
                continue
            # 只剩 .jsonl（或 state 已不指认）时靠头行认场次：头行坏了不能当作
            # 「不是这一对」，否则 wipe_spool 记完成而原始 peer 字段留在文件里
            try:
                header = _read_header_strict(visit_path(spool_dir, visit_id, SPOOL_SUFFIX))
            except (OSError, ValueError):
                unreadable.append(visit_id)
                continue
            if _names_pair(header, own_char_uid, pair_ids):
                found.append(visit_id)
            elif schema_names:
                # 不合 schema 的 state 原始字段明确指认这一对，头行却指向别处（或缺）：对端字段
                # 仍在 state 里，不能凭头行把它排除掉，按读不出处理（fail closed）
                unreadable.append(visit_id)
            elif state_unreadable and (
                header is None
                or (header.get("own_char_uid") == own_char_uid and header.get("pair_id") is None)
            ):
                if schema_excludes:
                    # 头行没指认这一对（或不在 / 已抹），而不合 schema 的 state 原始字段明确属于
                    # 别的角色 / 别的一对 / 别的账号：两处都排除，跳过（不挡、不删）
                    continue
                if state_corrupt and header is None:
                    # 只剩 state.json（已结清的场次常常如此）且内容损坏：谁都用不了，也认不出
                    # 是谁的。挡住会让本机每个角色的每次清除都卡死；它若正属于被清的人，删掉
                    # 本就是清除要做的事。同样只报给调用方，由清除路径删
                    if corrupt_wiped is not None:
                        corrupt_wiped.append(visit_id)
                    continue
                if state_corrupt and header is not None:
                    # 头行身份已抹（同一角色）而 state.json 内容损坏：这份 state 谁都用不了，
                    # 不能让这个角色之后每一次清除都卡死。这里只是查找（开场交接也调用），
                    # 不删任何文件；单独报给调用方，由清除的抹身份步骤把它（连同可能残留
                    # 的对端字段）删掉
                    # 只报同一账号下的（头行的 own_uid 抹身份时保留）：别的账号的场次与这次清除无关
                    if corrupt_wiped is not None and (own_uid is None or header.get("own_uid") == own_uid):
                        corrupt_wiped.append(visit_id)
                    continue
                unreadable.append(visit_id)
        if unreadable and strict:
            raise SpoolStateUnreadable(unreadable)
        return found

    @classmethod
    async def find_visits_for_pairs(
        cls, config_dir: str | Path, own_char_uid: str, pair_ids: Iterable[str],
        *, corrupt_wiped: list[str] | None = None, own_uid: str | None = None,
        strict: bool = True,
    ) -> list[str]:
        """Return visit ids of ``own_char_uid`` whose state or header still names one of ``pair_ids``.

        Matching on ``pair_id`` (which embeds both community accounts) rather
        than ``peer_uid`` keeps another local account's visits with the same
        person untouched. Raises :class:`SpoolStateUnreadable` when a
        ``state.json`` exists but cannot be read (the forget step then stays
        pending instead of silently missing that visit).
        """
        return await asyncio.to_thread(
            cls._find_visits_sync, Path(config_dir), own_char_uid, frozenset(pair_ids), corrupt_wiped,
            own_uid, strict,
        )

    @classmethod
    async def drop_corrupt_state(cls, config_dir: str | Path, visit_id: str) -> None:
        """Delete a content-corrupt ``state.json`` (unusable by anyone) during a forget."""
        path = visit_path(_spool_dir(config_dir), visit_id, STATE_SUFFIX)

        def drop() -> None:
            # 与 state.json 的其他写入者同一把文件锁：不与并发的 update_state 交错
            with path_lock(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        json.load(f)
                except FileNotFoundError:
                    # 已经不在了：没什么可删
                    return
                except (ValueError, RecursionError):
                    # 仍是 JSON 本身坏了才删；能解析的（哪怕不合当前 schema）一律不动
                    path.unlink(missing_ok=True)

        await asyncio.to_thread(drop)

    @classmethod
    def _sweep_sync(
        cls, config_dir: Path, now: float, is_live: Callable[[str], bool] | None = None,
    ) -> list[Path]:
        spool_dir = _spool_dir(config_dir).resolve()
        deleted: list[Path] = []
        remaining = []
        scanned = _scan(spool_dir)
        for visit_id, suffix, path, st in scanned:
            if now - st.st_mtime <= _RETENTION_S:
                remaining.append((visit_id, suffix, path, st))
                continue
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # 与 state / 头行写者同一套逐路径锁（先 jsonl 后 state），锁内重判是否过期：
            # 改写方读完过期转录、还没原子替换时删掉，它的替换会以新 mtime 把转录复活
            with path_lock(visit_path(spool_dir, visit_id, SPOOL_SUFFIX)), path_lock(state_path):
                try:
                    st = path.stat()
                except FileNotFoundError:
                    continue
                except OSError:
                    remaining.append((visit_id, suffix, path, st))
                    continue
                if now - st.st_mtime <= _RETENTION_S:
                    remaining.append((visit_id, suffix, path, st))
                    continue
                # 「记成日记」提交中 / 永久性失败待处理（committing / commit_failed:diary）
                # 不设期限：state.json 里的 debrief_writes / debrief_pending 是补写的唯一依据，
                # 删了就永远半截。只豁免 state.json：.jsonl 照常到期，否则几场被忽略的
                # 永久失败就能把转录一直堆着
                # 只对 state.json 本身判豁免，宽限就按它自己的年龄算
                if suffix == STATE_SUFFIX and _retention_exempt(state_path, now - st.st_mtime):
                    remaining.append((visit_id, suffix, path, st))
                    continue
                # 还开着写的场次（墙钟往前跳过 7 天也会显得过期）整场不动：逐路径锁挡不住
                # append，删掉后追加写进已删除的 inode、关闭时连同恢复数据一起消失。
                # 判定与删除在同一把登记锁里，open 不能夹在中间登记
                # 关了记忆的在飞场次只有上传流水、没有登记的记忆 spool：靠调用方的在飞判断兜住
                live = is_live is not None and is_live(visit_id)
                with _OPEN_SPOOLS_LOCK:
                    busy = live or _spool_key(visit_path(spool_dir, visit_id, SPOOL_SUFFIX)) in _OPEN_SPOOLS
                    unlinked = False if busy else _sweep_unlink(path)
                if not unlinked:
                    if busy or path.exists():
                        remaining.append((visit_id, suffix, path, st))
                else:
                    deleted.append(path)
                    if suffix in _UPLOAD_SUFFIXES:
                        logger.warning(
                            "visit spool: gave up pending upload %s after %d days",
                            path.name, VISIT_SPOOL_RETENTION_DAYS,
                        )
        total = sum(st.st_size for _v, _s, _p, st in remaining)
        if total <= VISIT_SPOOL_DIR_CAP_BYTES:
            return deleted
        # 超过回收阈值：只回收已结清场次（最旧优先），待传文件与未结清场次一律不动。
        by_visit: dict[str, list[tuple[str, Path, os.stat_result]]] = {}
        for visit_id, suffix, path, st in remaining:
            by_visit.setdefault(visit_id, []).append((suffix, path, st))
        candidates = []
        for visit_id, files in by_visit.items():
            if is_live is not None and is_live(visit_id):
                continue
            state = _try_read_state(visit_path(spool_dir, visit_id, STATE_SUFFIX))
            if state is None or not transcript_releasable(state):
                continue
            reclaimable = [(p, st) for suffix, p, st in files if suffix not in _UPLOAD_SUFFIXES]
            if reclaimable:
                oldest = min(st.st_mtime for _p, st in reclaimable)
                candidates.append((oldest, visit_id, reclaimable))
        candidates.sort()
        for _oldest, visit_id, files in candidates:
            if total <= VISIT_SPOOL_DIR_CAP_BYTES:
                break
            jsonl = visit_path(spool_dir, visit_id, SPOOL_SUFFIX)
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # 与 state / 头行写者同一套逐路径锁（先 jsonl 后 state），锁内重判是否结清：
            # 改写方读完转录、还没原子替换时删掉，它随后的替换会把文件复活
            with path_lock(jsonl), path_lock(state_path):
                state = _try_read_state(state_path)
                if state is None or not transcript_releasable(state):
                    continue
                # 提交中 / 永久性失败的场次（锁内重判）：转录可以回收，state.json 必须留着
                if debrief_pins_state(state):
                    files = [(p, st) for p, st in files if p != state_path]
                # 这场还被某个实例开着写（关闭排在队列里）：整场跳过，不能只删 state.json
                # 留下转录，下一轮就再也判不出它已结清
                with _OPEN_SPOOLS_LOCK:
                    if _spool_key(jsonl) in _OPEN_SPOOLS:
                        continue
                    # state.json 最后删，且前面有任何一个删不掉就留着它：先删了 state、转录却
                    # 删不掉，下一轮读不到 state 就再也判不出这场已结清，转录一直占着容量
                    failed = False
                    for path, st in sorted(files, key=lambda f: f[0] == state_path):
                        if path == state_path and failed:
                            break
                        if _sweep_unlink(path):
                            deleted.append(path)
                            total -= st.st_size
                        else:
                            # 不再用 exists() 区分「本来就没了」：访问出错时它也返回 False。
                            # 偏保守：这一轮留下 state，下一轮读到它再回收
                            failed = True
        return deleted

    @classmethod
    async def sweep(
        cls, config_dir: str | Path, now: float, *, is_live: Callable[[str], bool] | None = None,
    ) -> list[Path]:
        """Reclaim spool directory space; return the deleted paths.

        ``now`` must be wall-clock epoch seconds (``time.time()``): file ages
        are ``now - st_mtime``. Unlike :meth:`fsync_due` / :meth:`fsync`
        (any clock, used consistently), a monotonic clock here makes every
        file look fresh, so nothing would ever expire.

        1. Every file older than ``VISIT_SPOOL_RETENTION_DAYS`` (by mtime) is
           deleted, pending uploads included (their seven-day limit), except
           the ``state.json`` of a visit whose diary commit is in flight or
           failed permanently (``committing:diary`` / ``commit_failed:diary``):
           it stays until both writes finish or the user abandons.
        2. If the directory still exceeds ``VISIT_SPOOL_DIR_CAP_BYTES``, only
           settled visits (digest and last summary done, debrief final or
           committing) are reclaimed, oldest first, until it fits; a
           committing / failed visit keeps its ``state.json``. Unsettled visits, however
           large, and pending ``.upload.json`` / ``.upload.jsonl`` files are
           never deleted for size; the admission cap
           ``VISIT_UPLOAD_PENDING_CAP_BYTES`` bounds them instead.

        A file that cannot be deleted (locked, no permission, a directory) is
        logged and kept for a later sweep; the rest of the sweep continues.
        Visits for which ``is_live`` answers True (in flight, possibly with
        only an upload stream) are never touched.
        """
        return await asyncio.to_thread(cls._sweep_sync, Path(config_dir), now, is_live)
