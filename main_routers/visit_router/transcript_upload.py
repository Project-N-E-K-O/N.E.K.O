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

"""Transcript upload and queued reports (OD-26 v3; design §4.6 report, §4.7 transcripts / reports).

Three parts, all independent of ``visitMemoryEnabled``:

* :class:`UploadJournal` -- the ``<visit_id>.upload.jsonl`` stream of a live
  visit (header first, then every ``text{final}`` of both sides, usage
  deltas and anomalies), kept in memory as well; :meth:`UploadJournal.seal`
  turns it into ``<visit_id>.upload.json`` (atomic, ``0o600``) and only then
  deletes the stream. The document is built by the same
  ``recovery.build_upload_doc`` that rebuilds a crashed visit's stream, so
  a sealed and a recovered upload are the same bytes for the same records.
  Usage recorded after the seal is dropped: usage counts up to finalize.
* :func:`upload_visit_transcript` -- one upload attempt of a sealed document
  (the ``upload_transcript`` callback of PR-08 recovery): only while the
  signed-in account is the document's ``own_visit_uid``; split into
  ``parts`` when the body exceeds ``VISIT_UPLOAD_CHUNK_BYTES``; ``413
  too_large`` doubles ``parts`` and resends the whole group (never the same
  chunk again); chunk progress is written back into the file.
* Queued reports -- ``config_dir/visit_reports/<visit_id>.json`` is written
  before every report request and deleted only once Servers accepted it (or
  the user gives it up); :func:`submit_queued_report` is PR-08's
  ``submit_report`` callback.

:func:`schedule_visit_retry` runs both in the background after a visit ends
or a report is queued, with ``VISIT_UPLOAD_RETRY_BACKOFF_S`` between rounds;
the next start's recovery pass retries whatever is left. Logs never carry
transcript text, notes or tickets.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import os
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from config.visit_settings import (
    VISIT_REPORTS_DIRNAME,
    VISIT_REPORT_STALE_S,
    VISIT_SPOOL_DIRNAME,
    VISIT_SPOOL_RETENTION_DAYS,
    VISIT_UPLOAD_CHUNK_BYTES,
    VISIT_UPLOAD_MAX_PARTS,
    VISIT_UPLOAD_PENDING_CAP_BYTES,
    VISIT_UPLOAD_RETRY_BACKOFF_S,
)
from main_logic.visit import memory_bridge
from main_logic.visit.memory_commit import SIDE_RANK
from main_logic.visit.recovery import build_upload_doc, sealed_upload_doc_usable
from main_logic.visit.spool import UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX
from main_logic.visit.subjects import path_lock
from main_routers.visit_router import accounts
from main_routers.visit_router import credentials as cr
from utils.file_utils import atomic_write_json, move_aside
from utils.instrument import counter, histogram
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE, require_visit_id, visit_path

logger = get_module_logger(__name__, "Main")

USAGE_KEYS = ("llm_input_tokens", "llm_output_tokens", "tts_requests", "tts_chars")
SPEAKERS = ("own_cat", "peer_cat", "own_human", "peer_human")
REPORT_REASONS = ("harassment", "sexual", "privacy", "spam", "other")
REPORT_FIELDS = (
    "visit_id", "own_visit_uid", "own_account", "reason", "note", "include_transcript", "anomalies",
    "app_version", "queued_at",
)
"""Fields of ``visit_reports/<visit_id>.json`` (design §4.6 report, plus ``own_account``)."""

TERMINAL_UPLOAD_REPLIES: frozenset[tuple[int, str]] = frozenset({
    (413, "transcript_budget_exceeded"),
    (400, "parts_out_of_range"),
})
"""Upload rejections that never change on retry (plus ``409 visit_not_started{final:true}``)."""

_UPLOAD_TIMEOUT_S = 30.0
_REPORT_TIMEOUT_S = 10.0
_MAX_SEND_ROUNDS = 3
_O_BINARY = getattr(os, "O_BINARY", 0)


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


config_dir_provider: Callable[[], Path] = _default_config_dir
"""Where visit files live (tests point it at ``tmp_path``; PR-09b keeps the default)."""

is_live: Callable[[str], bool] = lambda _visit_id: False  # noqa: E731
"""Whether a visit is still running in this process (wired to the runtime in PR-09a's runtime PR)."""

_sleep: Callable[[float], Any] = asyncio.sleep
"""Back-off sleep (tests replace it)."""


def _spool_dir(config_dir: Path) -> Path:
    return Path(config_dir) / VISIT_SPOOL_DIRNAME


def _reports_dir(config_dir: Path) -> Path:
    return Path(config_dir) / VISIT_REPORTS_DIRNAME


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        # 与凭证 / spool 文件同一立场：权限位尽力而为（Windows 上本就无效）
        logger.debug("visit upload: chmod 0600 failed for %s: %s", path.name, exc)


def _write_private_json(path: Path, doc: Mapping[str, Any]) -> None:
    atomic_write_json(path, dict(doc))
    _chmod_private(path)


def _load_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except RecursionError as exc:
        raise ValueError("too deeply nested") from exc


def _pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


# ── 上传流水 ───────────────────────────────────────────────────────────


class UploadJournal:
    """The upload stream of one live visit plus its in-memory copy.

    :meth:`open` writes the header (when the visit is entered: the host's
    room exists / the guest joined) before any other record. Lines are
    awaited (written before the caller enqueues the line); usage deltas and
    anomalies are fire-and-forget (the TTS enqueue hook is synchronous) but
    keep their order on the single writer thread. After :meth:`seal`
    nothing is recorded any more.
    """

    def __init__(self, config_dir: str | os.PathLike[str], visit_id: str) -> None:
        self.visit_id = require_visit_id(visit_id)
        spool_dir = _spool_dir(Path(config_dir))
        self.stream_path = visit_path(spool_dir, self.visit_id, UPLOAD_JSONL_SUFFIX)
        self.sealed_path = visit_path(spool_dir, self.visit_id, UPLOAD_JSON_SUFFIX)
        self._records: list[dict] = []
        self._usage = {key: 0 for key in USAGE_KEYS}
        self._anomalies = 0
        self._role: str | None = None
        self._started_at: float | None = None
        self._last_ts: float | None = None
        self._fd: int | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._sealed = False
        self._failed_writes = 0

    @property
    def is_open(self) -> bool:
        return self._executor is not None and not self._sealed

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def role(self) -> str | None:
        return self._role

    # 写线程上的同步操作

    def _open_sync(self, data: bytes) -> int:
        self.stream_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.stream_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | _O_BINARY, 0o600)
        try:
            _write_all(fd, data)
        except BaseException:
            os.close(fd)
            self.stream_path.unlink(missing_ok=True)
            raise
        return fd

    def _append_sync(self, data: bytes) -> None:
        if self._fd is None:
            raise RuntimeError("upload stream is not open")
        _write_all(self._fd, data)

    def _close_sync(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def _seal_sync(self, doc: dict) -> None:
        # 先原子写 .upload.json、再删流水（§3.2.6 第 22 条第 3 步）：两步之间崩溃时两份都在，
        # 补录认得已封存的那份并删掉流水
        try:
            _write_private_json(self.sealed_path, doc)
        finally:
            # 写失败时流水原样留着（下次启动补录从它封存），fd 照样关掉
            self._close_sync()
        try:
            self.stream_path.unlink(missing_ok=True)
        except OSError as exc:
            # 上传文件已写好：流水删不掉也照常上传（Servers 按 visit_id + role 幂等）
            logger.warning("visit upload %s: sealed but cannot delete the stream: %s", self.visit_id, exc)

    def _submit(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future:
        if self._executor is None:
            raise RuntimeError("upload stream is not open")
        return asyncio.wrap_future(self._executor.submit(fn, *args))

    def _fire(self, record: dict) -> None:
        data = _encode_record(record)
        fut = self._executor.submit(self._append_sync, data)  # type: ignore[union-attr]
        fut.add_done_callback(self._log_failed_write)

    def _log_failed_write(self, fut: concurrent.futures.Future) -> None:
        if fut.cancelled() or fut.exception() is None:
            return
        self._failed_writes += 1
        # 内存副本仍完整：封存时按内存写 .upload.json，丢的只是崩溃补录能看到的那几行
        logger.warning("visit upload %s: stream write failed: %s", self.visit_id, type(fut.exception()).__name__)

    # 公开接口

    async def open(
        self,
        *,
        role: str,
        own_visit_uid: str,
        own_char_uid: str,
        transport: str,
        started_at: float,
        app_version: str,
    ) -> None:
        """Create the stream (``O_EXCL``, ``0o600``) with its header record."""
        if self._executor is not None or self._sealed:
            raise RuntimeError("upload stream already opened")
        if role not in SIDE_RANK:
            raise ValueError("role must be 'host' or 'guest'")
        header = {
            "kind": "header", "visit_id": self.visit_id, "role": role, "own_visit_uid": own_visit_uid,
            "started_at": float(started_at), "own_char_uid": own_char_uid, "app_version": str(app_version),
            "transport": transport,
        }
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="visit-upload")
        try:
            self._fd = await asyncio.wrap_future(executor.submit(self._open_sync, _encode_record(header)))
        except BaseException:
            executor.shutdown(wait=False)
            raise
        self._executor = executor
        self._records = [header]
        self._role = role
        self._started_at = float(started_at)

    async def append_line(
        self, *, lp: int, side: str, speaker: str, ts: float, text: str, truncated: bool,
    ) -> None:
        """Record one final line (either side) and wait until it is written."""
        if not self.is_open:
            raise RuntimeError("upload stream is not open")
        if side not in SIDE_RANK or speaker not in SPEAKERS:
            raise ValueError("bad line side / speaker")
        record = {"kind": "line", "lp": int(lp), "side": side, "from": speaker, "ts": float(ts),
                  "text": str(text), "truncated": bool(truncated)}
        self._records.append(record)
        self._last_ts = float(ts)
        # 已登记的一行不因调用方被取消而撤回
        await asyncio.shield(self._submit(self._append_sync, _encode_record(record)))

    def note_usage(self, delta: Mapping[str, Any], *, ts: float | None = None) -> None:
        """Record a usage delta (positive integers only); a no-op once sealed or never opened.

        Synchronous so the TTS enqueue hook can call it; call it on the event
        loop thread (the in-memory copy is not locked).
        """
        if not self.is_open:
            return
        clean = {k: int(delta[k]) for k in USAGE_KEYS if _pos_int(delta.get(k))}
        if not clean:
            return
        when = time.time() if ts is None else float(ts)
        record = {"kind": "usage", "ts": when, "d": clean}
        self._records.append(record)
        self._last_ts = when
        for key, value in clean.items():
            self._usage[key] += value
            # 遥测只记低基数维度，不带 visit_id（转录上传才是唯一带 visit_id 的用量记录）
            counter(f"visit_{key}", value, role=self._role or "-")
        self._fire(record)

    def note_anomaly(self, *, ts: float | None = None) -> None:
        """Record one anomaly (counted in the upload's ``anomalies``)."""
        if not self.is_open:
            return
        when = time.time() if ts is None else float(ts)
        record = {"kind": "anomaly", "ts": when}
        self._records.append(record)
        self._last_ts = when
        self._anomalies += 1
        self._fire(record)

    @property
    def anomalies(self) -> int:
        return self._anomalies

    def lines(self) -> list[dict]:
        """Every recorded line, ``(lp, side_rank)`` order (the in-memory transcript)."""
        rows = [{k: r[k] for k in ("lp", "side", "from", "ts", "text", "truncated")}
                for r in self._records if r.get("kind") == "line"]
        rows.sort(key=lambda r: (r["lp"], SIDE_RANK[r["side"]]))
        return rows

    def usage(self, *, now: float | None = None) -> dict:
        """``{duration_s, llm_input_tokens, llm_output_tokens, tts_requests, tts_chars}`` so far."""
        start = self._started_at
        end = (time.time() if now is None else now) if start is not None else None
        duration = max(0, int(end - start)) if start is not None and end is not None else 0
        return {"duration_s": duration, **self._usage}

    async def seal(self, finalized_reason: str, *, ended_at: float | None = None) -> dict | None:
        """Write ``.upload.json`` from memory, then delete the stream; returns the document.

        Idempotent; None when the journal was never opened. Called by
        finalize (before ``state.json.finalized``) and by the shutdown hook.
        ``ended_at`` (default: now) is the finalize time: a quiet tail after
        the last record still counts toward the duration. Crash recovery,
        which has no finalize time, uses the last record instead.
        """
        if self._sealed or self._executor is None:
            return None
        doc = build_upload_doc(self._records, visit_id=self.visit_id, finalized_reason=finalized_reason)
        if doc is None:
            raise RuntimeError("upload journal has no valid header")
        _stamp_end(doc["request"], time.time() if ended_at is None else ended_at)
        remember_anomalies(self.visit_id, doc["request"].get("anomalies"))
        self._sealed = True
        executor = self._executor
        try:
            await asyncio.shield(asyncio.wrap_future(executor.submit(self._seal_sync, doc)))
        finally:
            self._executor = None
            executor.shutdown(wait=False)
        request = doc["request"]
        histogram("visit_duration_s", float(request["usage"]["duration_s"]), role=request["role"])
        counter("visit_transcript_sealed", 1, reason=str(finalized_reason)[:24])
        return doc


def _stamp_end(request: dict, ended_at: float) -> None:
    """Move ``ended_at`` / ``duration_s`` to the finalize time when it is later than the last record."""
    started = request.get("started_at")
    if not _finite(ended_at) or not _finite(started) or ended_at <= request.get("ended_at", started):
        return
    request["ended_at"] = ended_at
    request["usage"]["duration_s"] = max(0, int(ended_at - started))


_RECENT_ANOMALIES_MAX = 64
_recent_anomalies: "OrderedDict[str, int]" = OrderedDict()
"""Anomaly counts of visits sealed or uploaded in this process.

An uploaded transcript's files are deleted, but a report filed afterwards
still carries the count. (Servers keeps the count of every uploaded
transcript as well, so one lost across a restart is not lost evidence.)
"""


def remember_anomalies(visit_id: str, count: Any) -> None:
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return
    _recent_anomalies[visit_id] = count
    _recent_anomalies.move_to_end(visit_id)
    while len(_recent_anomalies) > _RECENT_ANOMALIES_MAX:
        _recent_anomalies.popitem(last=False)


def _write_all(fd: int, data: bytes) -> None:
    # 一次 write 写完整行；普通文件极少短写，短写时续写剩余部分
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _encode_record(record: Mapping[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def build_visit_usage(journal: UploadJournal, *, now: float | None = None) -> dict:
    """Usage of one visit so far (the ``usage`` object of the transcript upload)."""
    return journal.usage(now=now)


# ── 分块 ───────────────────────────────────────────────────────────────


def _encode_body(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def chunk_bounds(n_lines: int, parts: int, part: int) -> tuple[int, int]:
    """Lines ``[start, end)`` of chunk ``part``: ``ceil(k*N/parts)`` .. ``ceil((k+1)*N/parts)`` (§4.7)."""
    return -(-part * n_lines // parts), -(-(part + 1) * n_lines // parts)


def chunk_body(request: Mapping[str, Any], part: int, parts: int) -> dict:
    """The request body of chunk ``part`` of ``parts`` (the full request when ``parts == 1``)."""
    if parts == 1:
        return dict(request)
    lines = request["lines"]
    start, end = chunk_bounds(len(lines), parts, part)
    return {**request, "lines": lines[start:end], "part": part, "parts": parts}


def plan_parts(request: Mapping[str, Any], *, start: int = 1, chunk_bytes: int = VISIT_UPLOAD_CHUNK_BYTES) -> int:
    """Smallest power-of-two ``parts`` >= ``start`` whose every chunk fits ``chunk_bytes``.

    Capped at ``VISIT_UPLOAD_MAX_PARTS`` (one line is at most 4096 B, so a
    legal transcript always fits well before that).
    """
    parts = max(1, int(start))
    n_lines = len(request["lines"])
    while parts < VISIT_UPLOAD_MAX_PARTS and parts < max(1, n_lines):
        if all(len(_encode_body(chunk_body(request, k, parts))) <= chunk_bytes for k in range(parts)):
            break
        parts *= 2
    return min(parts, VISIT_UPLOAD_MAX_PARTS)


# ── 上传 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UploadResult:
    """One upload attempt: ``done`` (accepted / duplicate), ``terminal`` (reason) or retry later."""

    done: bool = False
    terminal: str | None = None
    retry_after_s: int | None = None
    login_required: bool = False
    """The owning account's Servers session is gone (not signed in / ``401``)."""

    @property
    def callback_value(self) -> bool | str:
        """The value of PR-08's ``upload_transcript`` contract."""
        if self.done:
            return True
        return self.terminal or False


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (ValueError, RecursionError):
        return None


def _code(body: Any) -> str | None:
    code = body.get("code") if isinstance(body, Mapping) else None
    return code if isinstance(code, str) else None


def _upload_receipt(status: int, body: Any) -> bool:
    """A contract receipt: ``201 {ok:true}`` or ``200 {ok:true, duplicate:true}``."""
    if not isinstance(body, Mapping) or body.get("ok") is not True:
        return False
    return status == 201 or (status == 200 and body.get("duplicate") is True)


def _accepted_parts(body: Any, parts: int) -> set[int] | None:
    raw = body.get("accepted_parts") if isinstance(body, Mapping) else None
    if not isinstance(raw, list):
        return None
    return {p for p in raw if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < parts}


def _persist_progress(path: Path, doc: dict, parts: int, accepted: set[int]) -> None:
    doc["parts"] = parts
    doc["accepted_parts"] = sorted(accepted)
    with path_lock(path):
        try:
            before = path.stat()
        except FileNotFoundError:
            # 文件已被别处删掉（上传成功 / 放弃）：不重建
            return
        _write_private_json(path, doc)
        try:
            # 分片进度不是一次重试：7 天保留期照旧按原来的 mtime 算
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        except OSError as exc:
            logger.warning("visit upload: cannot keep the age of %s: %s", path.name, exc)


def _valid_progress(doc: Mapping[str, Any]) -> tuple[int, set[int]] | None:
    parts = doc.get("parts")
    if not isinstance(parts, int) or isinstance(parts, bool) or not 1 <= parts <= VISIT_UPLOAD_MAX_PARTS:
        return None
    raw = doc.get("accepted_parts")
    accepted = {p for p in raw if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < parts} \
        if isinstance(raw, list) else set()
    return parts, accepted


async def _upload(visit_id: str, doc: dict, config_dir: Path) -> UploadResult:
    try:
        session = await cr._servers_session()
    except cr.VisitLoginRequired:
        return UploadResult(login_required=True)
    except cr.VisitServersError:
        return UploadResult()
    owner = doc.get("own_visit_uid")
    if not owner or await accounts.lookup_visit_uid(session.account) != owner:
        # 只有占这个 role 的账号能上传（Servers 只认原账号）：换号 / 登出时原样保留，等原账号回来
        memory_bridge.diag("upload_account_mismatch", visit_id=visit_id)
        return UploadResult()
    request = doc["request"]
    remember_anomalies(visit_id, request.get("anomalies"))
    path = visit_path(_spool_dir(config_dir), visit_id, UPLOAD_JSON_SUFFIX)
    progress = _valid_progress(doc)
    if progress is None:
        parts, accepted = await asyncio.to_thread(plan_parts, request), set()
        await asyncio.to_thread(_persist_progress, path, doc, parts, accepted)
    else:
        parts, accepted = progress
    url = f"{session.base_url}/api/visit/transcripts"
    for _round in range(_MAX_SEND_ROUNDS):
        if len(accepted) >= parts:
            # 块都收下了却还没见到 complete（本轮之前的回执、或上次重试落盘的进度）：只有 Servers
            # 拼好整组才算完成。重发最后一块（按块幂等）换一份带 complete 的回执，不空转一轮
            accepted.discard(parts - 1)
        for part in range(parts):
            if part in accepted:
                continue
            content = await asyncio.to_thread(_encode_body, chunk_body(request, part, parts))
            try:
                resp = await cr._send("POST", url, op="transcripts", headers=session.headers(),
                                      content=content, timeout=_UPLOAD_TIMEOUT_S)
            except cr.VisitServersUnreachable:
                return UploadResult()
            status, body = resp.status_code, _body(resp)
            code = _code(body)
            if 200 <= status < 300:
                if not _upload_receipt(status, body):
                    # 不合契约的 2xx（204、代理的 HTML 页……）不是受理回执：文件留着、稍后重试
                    logger.warning("visit upload %s: status=%s without a valid receipt, kept for retry",
                                   visit_id, status)
                    return UploadResult()
                if parts == 1:
                    return UploadResult(done=True)
                server_view = _accepted_parts(body, parts)
                if server_view is None:
                    # 分块上传的每个成功响应都带 accepted_parts：缺了就不替 Servers 认定
                    logger.warning("visit upload %s: chunk receipt without accepted_parts, kept for retry",
                                   visit_id)
                    return UploadResult()
                accepted = server_view
                if isinstance(body, Mapping) and body.get("complete") is True and len(accepted) >= parts:
                    return UploadResult(done=True)
                await asyncio.to_thread(_persist_progress, path, doc, parts, accepted)
                continue
            if status == 413 and code == "too_large":
                if parts >= VISIT_UPLOAD_MAX_PARTS:
                    return UploadResult(terminal="too_large")
                # 新一代分块：整组重切重传，已受理集合清空（幂等键含 parts，不会与上一代撞）
                parts, accepted = parts * 2, set()
                await asyncio.to_thread(_persist_progress, path, doc, parts, accepted)
                break
            if (status, code) in TERMINAL_UPLOAD_REPLIES:
                return UploadResult(terminal=code)
            if status == 409 and code == "visit_not_started":
                final = isinstance(body, Mapping) and body.get("final") is True
                return UploadResult(terminal="visit_not_started") if final else UploadResult()
            if status == 429:
                return UploadResult(retry_after_s=cr._retry_after(body, resp))
            if status == 401:
                # 原账号的登录失效：文件留着，提示重新登录（附转录的举报也在等它）
                return UploadResult(login_required=True)
            if status < 500:
                logger.warning("visit upload %s: status=%s code=%s, kept for retry", visit_id, status, cr._diag_code(code))
            return UploadResult()
    # 几轮都没拿到带 complete 的回执：留着下次再试
    return UploadResult()


async def upload_visit_transcript(visit_id: str, upload_doc: dict) -> bool | str:
    """PR-08's ``upload_transcript`` callback: one attempt for a sealed ``.upload.json`` document.

    True when Servers has the transcript (accepted / duplicate), the Servers
    code of a terminal rejection, False to keep the file for a later retry
    (network, 5xx, 429, ``visit_not_started{final:false}``, not signed in or
    another account signed in). Chunk progress is written into the file.
    """
    visit_id = require_visit_id(visit_id)
    # 与重试轮次同一把逐场锁：分块进度的落盘与上传结清不能和另一轮交错
    async with visit_lock(visit_id):
        return (await _upload(visit_id, upload_doc, Path(config_dir_provider()))).callback_value


# ── 举报队列 ───────────────────────────────────────────────────────────


def report_path(config_dir: Path, visit_id: str) -> Path:
    return visit_path(_reports_dir(config_dir), visit_id, ".json")


class ReportAlreadyQueued(Exception):
    """``visit_reports/<visit_id>.json`` already exists (409 ``already_queued``)."""


def _queue_report_sync(path: Path, doc: dict) -> None:
    with path_lock(path):
        if path.exists():
            try:
                queued = _load_json(path)
            except ValueError:
                queued = None
            if _valid_report(queued, doc["visit_id"]):
                raise ReportAlreadyQueued(path.name)
            # 内容坏了 / 不是这一场的举报：队列列表看不到它、也无法重试或放弃，不能让它永远挡住
            # 这场的新举报。改名留底后照常入队（读文件出 OSError 时原样抛出，按落盘失败处理）
            if move_aside(path, "invalid") is None:
                raise OSError(f"cannot move {path.name} aside")
            logger.warning("visit report queue: unreadable %s moved aside", path.name)
        _write_private_json(path, doc)


async def queue_report(config_dir: Path, doc: dict) -> None:
    """Write a new queued report (before any request is sent); raises if one is queued already."""
    await asyncio.to_thread(_queue_report_sync, report_path(config_dir, doc["visit_id"]), doc)


def _valid_report(doc: Any, visit_id: str) -> bool:
    # 没有可用的归属（账号 / visit_uid 都缺）的举报谁都看不到、提交不了也放弃不了：按坏文件处理
    return (
        isinstance(doc, dict) and doc.get("visit_id") == visit_id
        and doc.get("reason") in REPORT_REASONS and isinstance(doc.get("include_transcript"), bool)
        and any(isinstance(doc.get(k), str) and doc.get(k) for k in ("own_account", "own_visit_uid"))
    )


async def load_report(config_dir: Path, visit_id: str) -> dict | None:
    """The queued report of ``visit_id`` (None when none, or not this visit's)."""
    try:
        doc = await asyncio.to_thread(_load_json, report_path(config_dir, visit_id))
    except (OSError, ValueError) as exc:
        logger.warning("visit report queue: %s unreadable: %s", visit_id, type(exc).__name__)
        return None
    return doc if _valid_report(doc, visit_id) else None


def _delete_report_sync(path: Path) -> bool:
    with path_lock(path):
        try:
            path.unlink()
        except FileNotFoundError:
            return False
    return True


async def delete_report(config_dir: Path, visit_id: str) -> bool:
    """Drop a queued report (accepted by Servers, or given up by the user)."""
    return await asyncio.to_thread(_delete_report_sync, report_path(config_dir, visit_id))


def _mark_unavailable_sync(path: Path, visit_id: str, reason: str) -> None:
    with path_lock(path):
        doc = _load_json(path)
        if not _valid_report(doc, visit_id) or doc.get("transcript_unavailable"):
            return
        _write_private_json(path, {**doc, "transcript_unavailable": reason})


def same_report(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether two loaded copies are the same queued report (not a later one for the same visit)."""
    return (
        a is not None and b is not None
        and all(a.get(k) == b.get(k) for k in ("visit_id", "own_account", "own_visit_uid", "queued_at"))
    )


def _set_rejected_sync(path: Path, visit_id: str, reason: str | None, expect: Mapping[str, Any] | None) -> None:
    with path_lock(path):
        doc = _load_json(path)
        if not _valid_report(doc, visit_id) or doc.get("rejected") == reason:
            return
        if expect is not None and not same_report(doc, expect):
            # 提交期间这份已被放弃、换成了另一份（别的账号 / 重新举报）：不能给新的那份记拒收
            return
        doc = {k: v for k, v in doc.items() if k != "rejected"}
        if reason is not None:
            doc["rejected"] = reason
        _write_private_json(path, doc)


async def rejection_recorded(config_dir: Path, visit_id: str) -> bool:
    """Whether the queued report of ``visit_id`` carries its ``rejected`` marker (or is gone)."""
    report = await load_report(config_dir, visit_id)
    return report is None or bool(report.get("rejected"))


async def set_report_rejected(
    config_dir: Path, visit_id: str, reason: str | None, *, expect: Mapping[str, Any] | None = None,
) -> None:
    """Mark (or, with None, unmark) a queued report Servers refused for good.

    A refused report is never deleted on its own -- only acceptance or the
    user giving it up removes the file (design §4.6 report) -- but it is not
    resubmitted automatically either: the queue lists it with ``rejected``
    and the user chooses retry or abandon. With ``expect`` the file is only
    touched while it still holds that same report (:func:`same_report`).
    """
    try:
        await asyncio.to_thread(_set_rejected_sync, report_path(config_dir, visit_id), visit_id, reason, expect)
    except (OSError, ValueError) as exc:
        logger.warning("visit report queue: cannot mark %s: %s", visit_id, type(exc).__name__)


async def mark_report_transcript_unavailable(config_dir: Path, visit_id: str, reason: str) -> bool:
    """Record on the queued report why its transcript will never reach Servers.

    False only when a queued report exists but could not be rewritten (no
    report, or one already marked, counts as done).
    """
    try:
        await asyncio.to_thread(_mark_unavailable_sync, report_path(config_dir, visit_id), visit_id, reason)
    except (OSError, ValueError) as exc:
        logger.warning("visit report queue: cannot mark %s: %s", visit_id, type(exc).__name__)
        return False
    return True


@dataclass(frozen=True)
class ReportResult:
    """One report submission: ``accepted`` (with ``report_id``), ``unknown_visit``, or retry later."""

    accepted: bool = False
    report_id: str | None = None
    unknown_visit: bool = False
    login_required: bool = False
    attempted: bool = False
    """The request went out as the owning account (network errors included)."""
    retry_after_s: int | None = None
    """Servers ``429`` delay of the report endpoint."""


def report_request(doc: Mapping[str, Any]) -> dict:
    """Servers ``POST /api/visit/reports`` body rebuilt from a queued report (never ``peer_uid``)."""
    body: dict[str, Any] = {
        "visit_id": doc["visit_id"],
        "reason": doc["reason"],
        "include_transcript": bool(doc["include_transcript"]),
        "anomalies": doc.get("anomalies") if _pos_int(doc.get("anomalies")) else 0,
        "app_version": str(doc.get("app_version") or ""),
    }
    note = doc.get("note")
    if isinstance(note, str) and note:
        body["note"] = note
    unavailable = doc.get("transcript_unavailable")
    if isinstance(unavailable, str) and unavailable:
        body["transcript_unavailable"] = unavailable
    return body


async def _report_owner_signed_in(doc: Mapping[str, Any], account: str) -> bool:
    """Whether ``account`` is the one that queued the report.

    ``own_account`` (the community account id, always known locally) decides;
    a report without it falls back to ``own_visit_uid`` through the local
    account map. A visit reported from another device's history has no map
    entry on this machine, which is why the account id is stored at all.
    """
    own_account = doc.get("own_account")
    if isinstance(own_account, str) and own_account:
        return own_account == account
    owner = doc.get("own_visit_uid")
    return bool(owner) and await accounts.lookup_visit_uid(account) == owner


async def send_report(doc: Mapping[str, Any]) -> ReportResult:
    """One ``POST /api/visit/reports`` for a queued report, as the account that queued it."""
    try:
        session = await cr._servers_session()
    except cr.VisitLoginRequired:
        return ReportResult(login_required=True)
    except cr.VisitServersError:
        return ReportResult()
    if not await _report_owner_signed_in(doc, session.account):
        # 只用举报时登录的那个账号提交（Servers 只认参与者本人）：换号 / 登出时原样保留
        memory_bridge.diag("report_account_mismatch", visit_id=str(doc.get("visit_id")))
        return ReportResult()
    try:
        resp = await cr._send("POST", f"{session.base_url}/api/visit/reports", op="reports",
                              headers=session.headers(), json_body=report_request(doc), timeout=_REPORT_TIMEOUT_S)
    except cr.VisitServersUnreachable:
        return ReportResult(attempted=True)
    body = _body(resp)
    report_id = body.get("report_id") if isinstance(body, Mapping) else None
    receipt = isinstance(report_id, str) and bool(report_id) and (
        resp.status_code == 201 or (resp.status_code == 200 and body.get("duplicate") is True))
    if receipt:
        return ReportResult(accepted=True, report_id=report_id, attempted=True)
    if 200 <= resp.status_code < 300:
        # 不合契约的 2xx 不算受理：排队文件留着、稍后重提
        logger.warning("visit report: status=%s without a valid receipt, kept queued", resp.status_code)
        return ReportResult(attempted=True)
    if resp.status_code == 404 and _code(body) == "unknown_visit":
        memory_bridge.diag("report_unknown_visit", visit_id=str(doc.get("visit_id")))
        return ReportResult(unknown_visit=True, attempted=True)
    if resp.status_code == 401:
        return ReportResult(login_required=True, attempted=True)
    if resp.status_code == 429:
        return ReportResult(attempted=True, retry_after_s=cr._retry_after(body, resp))
    return ReportResult(attempted=True)


async def submit_queued_report(visit_id: str, report_doc: dict) -> bool:
    """PR-08's ``submit_report`` callback: True once Servers accepted the queued report.

    A report already refused (``rejected``) is not resent; a new ``404
    unknown_visit`` marks it so (the file stays for the user to decide).
    """
    visit_id = require_visit_id(visit_id)
    config_dir = Path(config_dir_provider())
    async with visit_lock(visit_id):
        # 与端点的放弃 / 重试同一把逐场锁；等锁期间这份可能已被放弃或换成另一份
        current = await load_report(config_dir, visit_id)
        if current is None or not same_report(current, report_doc) or current.get("rejected"):
            return False
        result = await send_report(report_doc)
        if result.accepted:
            # 受理即在锁内删（补录随后的删除只会扑空），不给锁外的新举报留被误删的窗口
            await _delete_accepted_report(config_dir, visit_id)
            return True
        if result.unknown_visit:
            await set_report_rejected(config_dir, visit_id, "unknown_visit", expect=report_doc)
    return False


async def _delete_accepted_report(config_dir: Path, visit_id: str) -> None:
    """Drop a report Servers accepted; a failed delete never turns the acceptance into an error.

    The file stays queued and a later round resubmits it, which Servers
    answers as a duplicate, and deletes it then.
    """
    try:
        await delete_report(config_dir, visit_id)
    except OSError as exc:
        logger.warning("visit report %s: accepted but the queued file cannot be deleted yet: %s",
                       visit_id, type(exc).__name__)
        schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=VISIT_UPLOAD_RETRY_BACKOFF_S[0])


async def finish_report(config_dir: Path, visit_id: str, result: ReportResult, doc: Mapping[str, Any]) -> bool:
    """Apply one submission's outcome to the queued file; True when it is gone (accepted)."""
    if result.accepted:
        await _delete_accepted_report(config_dir, visit_id)
        # 拒收原因没能记进举报而留着的封存文件：举报已受理，它再没有用处，别占待上传容量
        await asyncio.to_thread(_drop_rejected_sealed_sync, config_dir, visit_id)
        return True
    if result.unknown_visit:
        await set_report_rejected(config_dir, visit_id, "unknown_visit", expect=doc)
    return False


# ── 进程内重试 ─────────────────────────────────────────────────────────


def _file_age_s(path: Path, now: float) -> float | None:
    try:
        return now - path.stat().st_mtime
    except FileNotFoundError:
        return None


def _drop_rejected_sealed_sync(config_dir: Path, visit_id: str) -> None:
    path = visit_path(_spool_dir(config_dir), visit_id, UPLOAD_JSON_SUFFIX)
    with path_lock(path):
        try:
            doc = _load_json(path)
        except (OSError, ValueError):
            return
        if isinstance(doc, dict) and isinstance(doc.get("rejected"), str) and doc["rejected"]:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("visit upload %s: cannot delete rejected %s: %s", visit_id, path.name, exc)


def _mark_sealed_rejected_sync(path: Path, reason: str) -> None:
    with path_lock(path):
        try:
            before = path.stat()
        except FileNotFoundError:
            return
        doc = _load_json(path)
        if not isinstance(doc, dict):
            return
        _write_private_json(path, {**doc, "rejected": reason})
        # 记标记不是一次重试：7 天期限照旧按原来的 mtime 算
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


async def _settle_upload(config_dir: Path, visit_id: str, path: Path, result: UploadResult) -> str | None:
    """Delete a finished sealed upload; return a terminal reason the queued report could not record.

    A terminal rejection (or expiry) is written into the queued report
    first. The sealed file is the only durable record of it, so when the
    report cannot be rewritten the file stays, marked ``rejected`` (the next
    round and PR-08 recovery mark the report, then delete the file), and
    the reason is returned so the report submitted in this round carries it.
    """
    if result.terminal is not None:
        memory_bridge.diag("upload_rejected", visit_id=visit_id, reason=result.terminal)
        if not await mark_report_transcript_unavailable(config_dir, visit_id, result.terminal):
            try:
                await asyncio.to_thread(_mark_sealed_rejected_sync, path, result.terminal)
            except (OSError, ValueError) as exc:
                logger.warning("visit upload %s: cannot mark %s rejected: %s", visit_id, path.name,
                               type(exc).__name__)
            return result.terminal
    try:
        await asyncio.to_thread(path.unlink, True)
    except OSError as exc:
        logger.warning("visit upload %s: done but cannot delete %s: %s", visit_id, path.name, exc)
    return None


@dataclass(frozen=True)
class RetryRound:
    """What one round left behind: whether anything is still pending and a Servers ``retry_after``."""

    pending: bool
    retry_after_s: int | None = None
    login_required: bool = False
    """The queued report could not be sent: the owning account's session is gone."""


@dataclass(frozen=True)
class UploadRound:
    """State of a visit's transcript after one attempt.

    ``pending``: not at Servers yet (a stream or a sealed file is left);
    ``retryable``: a sealed file this process can retry (a stream alone
    belongs to a crashed visit and waits for the next start's recovery).
    """

    pending: bool
    retryable: bool = False
    retry_after_s: int | None = None
    unavailable: str | None = None
    """Terminal reason the queued report could not record; the caller submits it in memory."""
    login_required: bool = False
    """The upload could not go out: the owning account's session is gone."""


async def attempt_upload(visit_id: str, *, config_dir: Path, now: float | None = None) -> UploadRound:
    """One upload attempt of ``visit_id``'s sealed transcript; settles the file when done.

    A sealed file older than ``VISIT_SPOOL_RETENTION_DAYS`` is given up (its
    queued report records ``transcript_unavailable:'expired'``), as is a
    terminal rejection (with the Servers code).
    """
    now = time.time() if now is None else now
    spool_dir = _spool_dir(config_dir)
    sealed = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    if visit_id in _settled_leftovers:
        # 已结清、只是上次没删掉：直接再删，不重传（换号 / 登出时重传进不去，文件会一直占着容量）
        try:
            await asyncio.to_thread(sealed.unlink, True)
        except OSError as exc:
            logger.warning("visit upload %s: still cannot delete %s: %s", visit_id, sealed.name, exc)
            return UploadRound(pending=False, retryable=True)
        _settled_leftovers.discard(visit_id)
        return UploadRound(pending=False)
    age = await asyncio.to_thread(_file_age_s, sealed, now)
    if age is None:
        stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
        return UploadRound(pending=await asyncio.to_thread(stream.exists))
    if age > VISIT_SPOOL_RETENTION_DAYS * 86400:
        memory_bridge.diag("upload_expired", visit_id=visit_id)
        return await _settled_round(config_dir, visit_id, sealed, UploadResult(terminal="expired"))
    try:
        doc = await asyncio.to_thread(_load_json, sealed)
    except (OSError, ValueError):
        doc = None
    if not sealed_upload_doc_usable(doc, visit_id):
        # 读不出 / 结构不对 / 别的场次或别的版本的封存文件：不直接传，交给启动补录判
        # （它能对照流水与 state.json 重封、隔离，或留给新版本）
        return UploadRound(pending=True)
    rejected = doc.get("rejected")
    if isinstance(rejected, str) and rejected:
        # 上一轮已终态拒收、只是原因没记进举报：不再整份重传，接着记原因、删文件
        result = UploadResult(terminal=rejected)
    else:
        result = await _upload(visit_id, doc, config_dir)
    if result.done or result.terminal is not None:
        return await _settled_round(config_dir, visit_id, sealed, result)
    return UploadRound(pending=True, retryable=True, retry_after_s=result.retry_after_s,
                       login_required=result.login_required)


async def _settled_round(config_dir: Path, visit_id: str, sealed: Path, result: UploadResult) -> UploadRound:
    unmarked = await _settle_upload(config_dir, visit_id, sealed, result)
    # 已结清（传上去、过期，或终态原因已记进举报）但封存文件没删掉（被占用）：转录不再挡举报，但这一轮
    # 仍要重来清理，否则它一直占着待上传容量。原因没记进举报而有意留着的那份不算（它带 rejected 标记）
    leftover = unmarked is None and await asyncio.to_thread(sealed.exists)
    if leftover:
        _settled_leftovers.add(visit_id)
    return UploadRound(pending=False, retryable=leftover, unavailable=unmarked)


_VISIT_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def visit_lock(visit_id: str) -> asyncio.Lock:
    """Per-visit lock serializing upload / report rounds (endpoint, background retry, manual retry)."""
    lock = _VISIT_LOCKS.get(visit_id)
    if lock is None:
        lock = asyncio.Lock()
        _VISIT_LOCKS[visit_id] = lock
    return lock


async def retry_visit_once(
    visit_id: str, *, config_dir: Path | None = None, now: float | None = None, manual: bool = False,
    owner: str | None = None,
) -> RetryRound:
    """One round for ``visit_id``: upload its sealed transcript, then submit its queued report.

    A report with ``include_transcript:true`` waits for the transcript
    (accepted, or terminally rejected / expired); ``false`` does not. A
    report Servers refused (``rejected``) is only resent when the user asks
    (``manual``); it no longer keeps the background loop going. ``owner``
    (the account that asked for a manual retry) is checked under the lock:
    a report queued meanwhile by another account is left alone.
    """
    visit_id = require_visit_id(visit_id)
    config_dir = Path(config_dir_provider() if config_dir is None else config_dir)
    async with visit_lock(visit_id):
        try:
            upload = await attempt_upload(visit_id, config_dir=config_dir, now=now)
        except (OSError, ValueError) as exc:
            # 上传的本地记账出错（磁盘满等）：转录这一轮算没传上去，但不附转录的举报照样提交
            logger.warning("visit upload %s: attempt failed: %s", visit_id, type(exc).__name__)
            upload = UploadRound(pending=True, retryable=True)
        report = await load_report(config_dir, visit_id)
        if report is not None and owner is not None and not await report_belongs_to(report, owner):
            # 等锁期间原举报没了、换成了另一账号排的：不替它提交，也不动它的拒收标记
            report = None
        if report is not None and report.get("rejected") and not manual:
            report = None
        report_pending = report is not None
        # 附转录的举报在等转录：转录因登录失效传不上去时同样要提示重新登录
        login_required = upload.login_required and report_pending and bool(report["include_transcript"])
        report_retry_after: int | None = None
        if report is not None and not (upload.pending and report["include_transcript"]):
            if upload.unavailable and not report.get("transcript_unavailable"):
                # 原因没写进举报文件（磁盘 / 权限）：提交的这份照样带上
                report = {**report, "transcript_unavailable": upload.unavailable}
            result = await send_report(report)
            login_required = login_required or result.login_required
            report_retry_after = result.retry_after_s
            if await finish_report(config_dir, visit_id, result, report) or (
                    result.unknown_visit and await rejection_recorded(config_dir, visit_id)):
                # 受理了但本地文件没删掉（被占用）：这一轮的 worker 别退出，下一轮重提拿 duplicate 回执再删
                report_pending = result.accepted and same_report(await load_report(config_dir, visit_id), report)
            elif manual and report.get("rejected") and result.attempted and not result.login_required:
                # 用户手动重试、请求发出去了且这回没被拒（网络 / 5xx）：回到普通的排队重试。
                # 没发出去（未登录 / 换了账号）或登录失效时拒收标记照留
                await set_report_rejected(config_dir, visit_id, None, expect=report)
            elif report.get("rejected"):
                report_pending = False
    delays = [d for d in (upload.retry_after_s, report_retry_after) if d is not None]
    return RetryRound(pending=report_pending or upload.retryable, retry_after_s=max(delays) if delays else None,
                      login_required=login_required)


_workers: dict[str, asyncio.Task] = {}

_settled_leftovers: set[str] = set()
"""visit_ids whose sealed upload is settled but could not be deleted yet (the next round only deletes it)."""


def _reset_for_tests() -> None:
    for task in _workers.values():
        task.cancel()
    _workers.clear()
    _recent_anomalies.clear()
    _not_before.clear()
    _settled_leftovers.clear()


_not_before: dict[str, float] = {}
"""visit_id -> monotonic time before which no background round may run (Servers ``retry_after``)."""


def _defer(visit_id: str, delay_s: float) -> None:
    """Push the next background round of ``visit_id`` back by at least ``delay_s`` (never earlier)."""
    if delay_s > 0:
        _not_before[visit_id] = max(_not_before.get(visit_id, 0.0), time.monotonic() + delay_s)


async def _retry_loop(visit_id: str, config_dir: Path | None) -> None:
    delay_index = 0
    while True:
        deadline = _not_before.get(visit_id)
        if deadline is not None:
            wait = deadline - time.monotonic()
            if wait > 0:
                await _sleep(wait)
            if _not_before.get(visit_id, deadline) != deadline:
                # 等待期间又被推后（手动重试拿到了更长的 retry_after）：按新时间再等
                continue
            _not_before.pop(visit_id, None)
        if is_live(visit_id):
            # 在飞场次的转录还没封存：finalize 封存后会重新排上
            return
        try:
            outcome = await retry_visit_once(visit_id, config_dir=config_dir)
        except Exception as exc:  # noqa: BLE001 - 后台重试：记一笔，下一轮再来
            logger.warning("visit upload %s: retry round failed: %s", visit_id, type(exc).__name__)
            outcome = RetryRound(pending=True)
        if not outcome.pending:
            return
        delay = VISIT_UPLOAD_RETRY_BACKOFF_S[min(delay_index, len(VISIT_UPLOAD_RETRY_BACKOFF_S) - 1)]
        delay_index += 1
        _defer(visit_id, max(delay, outcome.retry_after_s or 0))


def schedule_visit_retry(visit_id: str, *, config_dir: Path | None = None,
                         initial_delay_s: float = 0.0) -> asyncio.Task:
    """Upload / report retries of ``visit_id`` in the background (one task per visit).

    ``initial_delay_s``: the caller just made an attempt; no background round
    runs before this long (at least the Servers ``retry_after``). It also
    applies to a worker that is already waiting -- a longer delay pushes its
    next round back, a shorter one never pulls it forward.
    """
    visit_id = require_visit_id(visit_id)
    _defer(visit_id, initial_delay_s)
    task = _workers.get(visit_id)
    if task is not None and not task.done():
        return task
    task = asyncio.create_task(_retry_loop(visit_id, config_dir), name=f"visit-upload-{visit_id[:6]}")
    _workers[visit_id] = task

    def _done(t: asyncio.Task) -> None:
        if _workers.get(visit_id) is t:
            del _workers[visit_id]

    task.add_done_callback(_done)
    return task


# ── 给端点与运行时的查询 ───────────────────────────────────────────────


def upload_pending_sync(config_dir: Path, visit_id: str) -> bool:
    """Whether this visit's transcript has not reached Servers yet (stream or sealed file present)."""
    spool_dir = _spool_dir(config_dir)
    return any(visit_path(spool_dir, visit_id, s).exists() for s in (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX))


def _pending_upload_bytes_sync(config_dir: Path) -> int:
    total = 0
    try:
        entries = list(os.scandir(_spool_dir(config_dir)))
    except FileNotFoundError:
        return 0
    for entry in entries:
        if entry.name.endswith((UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX)):
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


async def upload_backlog_full(config_dir: Path) -> bool:
    """True once pending transcripts reach ``VISIT_UPLOAD_PENDING_CAP_BYTES`` (rooms / join refuse)."""
    return await asyncio.to_thread(_pending_upload_bytes_sync, config_dir) >= VISIT_UPLOAD_PENDING_CAP_BYTES


def _anomalies_sync(config_dir: Path, visit_id: str) -> int | None:
    spool_dir = _spool_dir(config_dir)
    doc = _load_json(visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX))
    request = doc.get("request") if isinstance(doc, dict) else None
    if isinstance(request, dict) and isinstance(request.get("anomalies"), int):
        return max(0, request["anomalies"])
    try:
        with open(visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX), "rb") as handle:
            return sum(1 for raw in handle if b'"kind":"anomaly"' in raw)
    except FileNotFoundError:
        return None


async def visit_anomalies(config_dir: Path, visit_id: str) -> int:
    """Anomaly count of a visit: its pending upload, else what this process saw sealed / uploaded."""
    try:
        count = await asyncio.to_thread(_anomalies_sync, config_dir, visit_id)
    except (OSError, ValueError):
        count = None
    return count if count is not None else _recent_anomalies.get(visit_id, 0)


def _list_reports_sync(config_dir: Path) -> list[tuple[str, Any]]:
    directory = _reports_dir(config_dir)
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    out = []
    for name in names:
        visit_id = name[:-len(".json")] if name.endswith(".json") else ""
        if not VISIT_ID_RE.fullmatch(visit_id):
            continue
        try:
            out.append((visit_id, _load_json(directory / name)))
        except (OSError, ValueError):
            continue
    return out


async def report_belongs_to(doc: Mapping[str, Any], account: str | None) -> bool:
    """Whether community ``account`` filed the queued report (see ``_report_owner_signed_in``)."""
    return bool(account) and await _report_owner_signed_in(doc, str(account))


async def list_queued_reports(config_dir: Path, account: str | None, *, now: float | None = None) -> list[dict]:
    """Queued reports of ``account`` for the UI: ``{visit_id, reason, include_transcript, queued_at, rejected, stale}``.

    ``rejected`` (Servers refused it, e.g. ``'unknown_visit'``) and ``stale``
    (a week old) both mean the UI offers retry / give up right away.

    Reports another account filed on this machine are not listed: every
    account that signs in here shares the queue directory.
    """
    now = time.time() if now is None else now
    rows = []
    for visit_id, doc in await asyncio.to_thread(_list_reports_sync, config_dir):
        if not _valid_report(doc, visit_id) or not await report_belongs_to(doc, account):
            continue
        queued_at = doc.get("queued_at") if _finite(doc.get("queued_at")) else None
        rejected = doc.get("rejected") if isinstance(doc.get("rejected"), str) else None
        rows.append({
            "visit_id": visit_id,
            "reason": doc["reason"],
            "include_transcript": doc["include_transcript"],
            "queued_at": queued_at,
            "rejected": rejected,
            "stale": queued_at is not None and now - queued_at >= VISIT_REPORT_STALE_S,
        })
    return rows
