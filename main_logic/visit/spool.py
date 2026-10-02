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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.visit_settings import (
    VISIT_SPOOL_DIR_CAP_BYTES,
    VISIT_SPOOL_DIRNAME,
    VISIT_SPOOL_FSYNC_S,
    VISIT_SPOOL_LINE_MAX_BYTES,
    VISIT_SPOOL_RETENTION_DAYS,
    VISIT_TEXT_MAX_BYTES,
)
from main_logic.visit.subjects import derive_pair_id, path_lock
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
    "diary",
    "forget",
)
STATE_FIELDS = frozenset({
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
    if header["v"] != 1:
        raise ValueError("spool header v must be 1")
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
    if not _is_int(line["lp"]):
        raise ValueError("spool line lp must be an int")
    if not isinstance(line["side"], str):
        raise ValueError("spool line side must be a string")
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


def new_state(
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    pair_id: str | None,
    peer_uid: str | None,
    peer_char_id: str | None,
    memory_enabled: bool,
) -> dict:
    """Return a fresh canonical ``state.json`` document for one visit.

    ``memory_enabled`` is the ``visitMemoryEnabled`` value read once when the
    visit activates; it stays fixed for the whole visit. ``own_uid`` is this
    side's verified ``visit_uid`` (the community account the visit ran under):
    visit data is partitioned by account, and crash recovery needs it to
    derive the person-level subject even after the user switched accounts.
    It is not a peer field and survives "forget this person".
    """
    state = {
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
        "debrief_writes": {"facts": False, "cache": False},
        "debrief_chip_pending": False,
        "last_summary_done": False,
        "memory_enabled": memory_enabled,
        "digest_writes": {},
    }
    return validate_state(state)


def _check_batch_map(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise SpoolStateError(f"{where} must be an object")
    for key, done in value.items():
        if not (isinstance(key, str) and key.isdigit()):
            raise SpoolStateError(f"{where} keys must be batch numbers")
        if not isinstance(done, bool):
            raise SpoolStateError(f"{where}[{key}] must be a bool")


def validate_state(state: Any) -> dict:
    """Check ``state`` against the canonical schema and return a deep copy.

    The field set must match ``STATE_FIELDS`` exactly. ``debrief_choice`` is
    restricted to ``DEBRIEF_CHOICES``; ``preview:diary`` and
    ``committing:diary`` require a non-empty ``debrief_pending`` (it is
    persisted before either state is entered) and ``generating:diary``
    requires it to be empty. Raises :class:`SpoolStateError`.
    """
    if not isinstance(state, Mapping):
        raise SpoolStateError("state must be an object")
    keys = set(state)
    if keys != STATE_FIELDS:
        raise SpoolStateError(
            "state fields mismatch: missing=%s extra=%s"
            % (sorted(STATE_FIELDS - keys), sorted(keys - STATE_FIELDS))
        )
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
    if choice in ("preview:diary", "committing:diary") and pending_empty:
        raise SpoolStateError(f"{choice} requires a persisted debrief_pending")
    if choice == "generating:diary" and pending is not None:
        raise SpoolStateError("generating:diary requires an empty debrief_pending")
    writes = state["debrief_writes"]
    if (
        not isinstance(writes, Mapping)
        or set(writes) != {"facts", "cache"}
        or not all(isinstance(v, bool) for v in writes.values())
    ):
        raise SpoolStateError("debrief_writes must be {facts: bool, cache: bool}")
    for name in ("debrief_chip_pending", "last_summary_done", "memory_enabled"):
        if not isinstance(state[name], bool):
            raise SpoolStateError(f"{name} must be a bool")
    runs = state["digest_writes"]
    if not isinstance(runs, Mapping):
        raise SpoolStateError("digest_writes must be an object")
    for run, record in runs.items():
        if not (isinstance(run, str) and run.isdigit()):
            raise SpoolStateError("digest_writes keys must be run numbers")
        if not isinstance(record, Mapping) or set(record) != {
            "requested_at", "through_lp", "group", "segments",
        }:
            raise SpoolStateError(
                f"digest_writes[{run}] must be {{requested_at, through_lp, group, segments}}"
            )
        if not _is_number(record["requested_at"]):
            raise SpoolStateError(f"digest_writes[{run}].requested_at must be a number")
        if not _is_int(record["through_lp"]):
            raise SpoolStateError(f"digest_writes[{run}].through_lp must be an int")
        _check_batch_map(record["group"], f"digest_writes[{run}].group")
        _check_batch_map(record["segments"], f"digest_writes[{run}].segments")
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

    With memory on, at least one digest run must be registered and every
    group / segments batch of every run must be complete.
    """
    if state.get("last_summary_done") is not True:
        return False
    if not is_digestable(state):
        return True
    runs = state.get("digest_writes") or {}
    if not runs:
        return False
    for record in runs.values():
        for part in ("group", "segments"):
            if not all((record.get(part) or {}).values()):
                return False
    return True


def debrief_settled(state: Mapping[str, Any]) -> bool:
    """Whether the debrief reached a final outcome (or never applies: memory off)."""
    if not is_digestable(state):
        return True
    return state.get("debrief_choice") in ("diary", "forget")


def visit_settled(state: Mapping[str, Any]) -> bool:
    """Region settled and debrief final: the visit's spool may be reclaimed."""
    return region_settled(state) and debrief_settled(state)


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


def _scan(spool_dir: Path) -> list[tuple[str, str, Path, os.stat_result]]:
    out = []
    try:
        names = os.listdir(spool_dir)
    except FileNotFoundError:
        return out
    for name in names:
        parsed = _split_name(name)
        if parsed is None:
            continue
        path = spool_dir / name
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        out.append((parsed[0], parsed[1], path, st))
    return out


def _read_state_file(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    return validate_state(data)


def _retention_exempt(state_path: Path, age_s: float) -> bool:
    """Whether a visit's files must survive the 7-day expiry this round.

    True for an in-flight diary commit (``committing:diary``). When
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
    return bool(state and state.get("debrief_choice") == "committing:diary")


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


def _read_header(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            first = f.readline()
    except FileNotFoundError:
        return None
    if not first.endswith(b"\n"):
        return None
    try:
        header = json.loads(first)
    except ValueError:
        return None
    return header if isinstance(header, dict) else None


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
    header = json.loads(first)
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
        except ValueError:
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


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
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
        except ValueError:
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
            os.close(fd)
            with _OPEN_SPOOLS_LOCK:
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
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
                with _OPEN_SPOOLS_LOCK:
                    _OPEN_SPOOLS.discard(_spool_key(self.jsonl_path))

    def _discard_orphan_open(self, fut: "concurrent.futures.Future[int]") -> None:
        """Close the fd of an ``_open_sync`` whose awaiting ``open`` was cancelled."""
        if fut.cancelled() or fut.exception() is not None:
            return
        try:
            os.close(fut.result())
        except OSError as exc:
            # fd 已失效也无妨：这里只负责不泄漏；登记照样要撤销
            logger.debug("visit spool: closing orphan fd failed: %s", exc)
        with _OPEN_SPOOLS_LOCK:
            _OPEN_SPOOLS.discard(_spool_key(self.jsonl_path))

    async def open(self, header: Mapping[str, Any], *, now: float | None = None) -> None:
        """Create ``<visit_id>.jsonl`` (``O_EXCL``, ``0o600``) and write the header line.

        ``header`` must carry exactly ``HEADER_FIELDS`` with ``v == 1`` and this
        spool's ``visit_id``. ``now`` seeds the fsync cadence (defaults to
        ``started_at``).
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
        self._last_fsync = clean["started_at"] if now is None else now
        self._dirty = False

    async def append(self, line: Mapping[str, Any]) -> None:
        """Append one spoken line (see :func:`encode_spool_line`) as a single write."""
        data = encode_spool_line(line)
        fut = self._submit(self._append_sync, data)
        self._dirty = True
        # 已接受并提交的一行不能因调用方被取消而撤回（排在别的写入后面时会被直接取消）
        await asyncio.shield(fut)

    def fsync_due(self, now: float) -> bool:
        """Whether unsynced lines exist and ``VISIT_SPOOL_FSYNC_S`` passed since the last fsync."""
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
        does not parse are dropped and logged as diagnostics; every complete
        line before them is returned.
        """

        def read() -> bytes | None:
            try:
                return self.jsonl_path.read_bytes()
            except FileNotFoundError:
                return None

        data = await asyncio.to_thread(read)
        if data is None:
            return SpoolContents(header=None)
        return _parse_spool_bytes(data, self.visit_id)

    # ── state.json ──

    def _write_state_sync(self, state: Mapping[str, Any]) -> dict:
        clean = validate_state(state)
        with path_lock(self.state_path):
            atomic_write_json(self.state_path, clean)
        return clean

    async def write_state(self, state: Mapping[str, Any]) -> dict:
        """Validate and atomically write ``state.json``; return the written copy."""
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
            clean = validate_state(state)
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
            if state is None or not visit_settled(state):
                return False
            return _unlink(self.jsonl_path)

    async def delete_if_settled(self) -> bool:
        """Delete ``.jsonl`` once the region is settled and the debrief is final.

        Called by ``mark_forget`` and, later, by whichever of the digest commit
        and the last-summary commit finishes last. Returns whether it deleted.
        """
        if self._fd is not None:
            return False
        return await asyncio.to_thread(self._delete_if_settled_sync)

    async def mark_forget(self) -> bool:
        """Record the debrief choice ``forget`` (no private memory is written).

        The ``.jsonl`` is deleted right away only when every digest batch of
        every run and the last-visit summary are done; otherwise it is kept
        and deleted by :meth:`delete_if_settled` once they are, because
        "do not record" never cancels the visit-region digest. Returns whether
        the ``.jsonl`` was deleted now.
        """

        def mutate(state: dict) -> None:
            if state["debrief_choice"] in ("committing:diary", "diary"):
                raise SpoolStateError(
                    f"cannot forget from debrief_choice={state['debrief_choice']!r}"
                )
            state["debrief_choice"] = "forget"
            state["debrief_pending"] = None
            state["debrief_chip_pending"] = False

        await asyncio.to_thread(self._update_state_sync, mutate)
        return await self.delete_if_settled()

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
    def _visit_ids(cls, spool_dir: Path, suffixes: Iterable[str]) -> list[str]:
        wanted = set(suffixes)
        return sorted({vid for vid, suffix, _p, _s in _scan(spool_dir) if suffix in wanted})

    @classmethod
    def _retire_char_sync(
        cls, config_dir: Path, character_uid: str, legacy_name: str | None
    ) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        retired = []
        unreadable: list[str] = []
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
            with path_lock(jsonl), path_lock(state_path):
                _unlink(jsonl)
                _unlink(state_path)
            retired.append(visit_id)
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
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
        Raises :class:`SpoolStateUnreadable` (after handling every readable
        visit) when some visit's ownership cannot be read.
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
            except (OSError, ValueError, SpoolStateUnreadable):
                unreadable.append(visit_id)
            if changed:
                renamed.append(visit_id)
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
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
        cls, config_dir: Path, own_char_uid: str, pair_ids: frozenset[str]
    ) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        found = []
        unreadable: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            # 清除路径要严格读：已结清的场次常常只剩 state.json，读不出来就跳过
            # 会让 wipe_spool 记完成、撤销日志被删，而 peer 字段仍留在文件里
            try:
                state = _read_state_file(visit_path(spool_dir, visit_id, STATE_SUFFIX))
            except FileNotFoundError:
                state = None
            except (OSError, ValueError):
                unreadable.append(visit_id)
                continue
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
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
        return found

    @classmethod
    async def find_visits_for_pairs(
        cls, config_dir: str | Path, own_char_uid: str, pair_ids: Iterable[str]
    ) -> list[str]:
        """Return visit ids of ``own_char_uid`` whose state or header still names one of ``pair_ids``.

        Matching on ``pair_id`` (which embeds both community accounts) rather
        than ``peer_uid`` keeps another local account's visits with the same
        person untouched. Raises :class:`SpoolStateUnreadable` when a
        ``state.json`` exists but cannot be read (the forget step then stays
        pending instead of silently missing that visit).
        """
        return await asyncio.to_thread(
            cls._find_visits_sync, Path(config_dir), own_char_uid, frozenset(pair_ids)
        )

    @classmethod
    def _sweep_sync(cls, config_dir: Path, now: float) -> list[Path]:
        spool_dir = _spool_dir(config_dir).resolve()
        deleted: list[Path] = []
        remaining = []
        scanned = _scan(spool_dir)
        # 豁免宽限按 state.json 自己的年龄算：同场较旧的 .jsonl 可能先被扫到
        state_age = {v: now - st.st_mtime for v, sfx, _p, st in scanned if sfx == STATE_SUFFIX}
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
                # 「记成日记」写到一半（committing:diary）不设期限：state.json 里的
                # debrief_writes / debrief_pending 是补写的唯一依据，删了就永远半截
                if suffix not in _UPLOAD_SUFFIXES and _retention_exempt(
                    state_path, state_age.get(visit_id, now - st.st_mtime),
                ):
                    remaining.append((visit_id, suffix, path, st))
                    continue
                if _unlink(path):
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
            state = _try_read_state(visit_path(spool_dir, visit_id, STATE_SUFFIX))
            if state is None or not visit_settled(state):
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
                if state is None or not visit_settled(state):
                    continue
                for path, st in files:
                    if _unlink(path):
                        deleted.append(path)
                        total -= st.st_size
        return deleted

    @classmethod
    async def sweep(cls, config_dir: str | Path, now: float) -> list[Path]:
        """Reclaim spool directory space; return the deleted paths.

        1. Every file older than ``VISIT_SPOOL_RETENTION_DAYS`` (by mtime) is
           deleted, pending uploads included (their seven-day limit), except
           the files of a visit whose diary commit is in flight
           (``committing:diary``): those stay until both writes finish.
        2. If the directory still exceeds ``VISIT_SPOOL_DIR_CAP_BYTES``, only
           settled visits (digest and last summary done, debrief final) are
           reclaimed, oldest first, until it fits. Unsettled visits, however
           large, and pending ``.upload.json`` / ``.upload.jsonl`` files are
           never deleted for size; the admission cap
           ``VISIT_UPLOAD_PENDING_CAP_BYTES`` bounds them instead.
        """
        return await asyncio.to_thread(cls._sweep_sync, Path(config_dir), now)
