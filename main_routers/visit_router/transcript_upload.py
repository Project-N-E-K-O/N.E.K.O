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
import contextlib
import json
import math
import os
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from config.visit_settings import (
    VISIT_REPORTS_DIRNAME,
    VISIT_REPORT_NOTE_MAX_CHARS,
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
from main_logic.visit.recovery import (
    REPORT_IDENTITY,
    build_upload_doc,
    reseal_orphan_stream,
    sealed_upload_doc_from_another_version,
    sealed_upload_doc_matches_state,
    sealed_upload_doc_usable,
)
from main_logic.visit.spool import UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX, VisitSpool
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
    # Login account was verified against the transcript's room-owning account before upload; Servers still reports wrong role / no issuance for this visit, so retry cannot help.
    (403, "not_participant"),
    (404, "unknown_visit"),
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
        # Same policy as credentials / spool files: permissions are best effort (ineffective on Windows anyway).
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


# ── Upload log ───────────────────────────────────────────────────────────


_open_streams: set[str] = set()
"""visit_ids whose upload stream this process still has open (being written): never resealed by a retry round."""


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
        self._seal_write: asyncio.Future | None = None
        self._open_abandoned = False
        self._abandoned_tail: list[bytes] = []
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

    # Synchronous operations on the writer thread.

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
        _open_streams.discard(self.visit_id)

    def _seal_sync(self, doc: dict) -> None:
        # Atomically write .upload.json before deleting the log (§3.2.6 item 22 step 3): a crash between leaves both,
        # and recovery recognizes the sealed copy and deletes the log.
        try:
            _write_private_json(self.sealed_path, doc)
        finally:
            # On write failure, retain log unchanged for sealing during next startup recovery; still close the fd.
            self._close_sync()
        try:
            self.stream_path.unlink(missing_ok=True)
        except OSError as exc:
            # Upload file already written: upload even if log deletion fails (Servers idempotency by visit_id + role).
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
        # Memory copy remains complete: seal .upload.json from memory; only crash recovery misses those rows.
        logger.warning("visit upload %s: stream write failed: %s", self.visit_id, type(fut.exception()).__name__)

    # Public interface.

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
        # Register before creating the file: once the log appears on disk, retry passes must know it remains open, never resealing during file creation.
        added = self.visit_id not in _open_streams
        _open_streams.add(self.visit_id)
        open_job = executor.submit(self._open_sync, _encode_record(header))
        opening = asyncio.wrap_future(open_job)
        try:
            self._fd = await asyncio.shield(opening)
        except BaseException:
            # Cancellation may leave file creation running in its thread: await completion despite repeated cancellation, close the obtained fd,
            # delete the header-only log, then unregister; otherwise retry would reseal an open log as orphaned.
            # Do not wait on shutdown (abandon_open): process exits immediately without more retry passes; next startup recovers remaining logs.
            # This only frees the event loop; pool threads stuck creating files are not daemons, so interpreter exit still waits for them.
            while not opening.done() and not self._open_abandoned:
                try:
                    await asyncio.shield(opening)
                except asyncio.CancelledError:
                    continue
                except BaseException:  # noqa: BLE001 - File creation itself failed: handle below as no fd.
                    break
            fd = opening.result() if opening.done() and not opening.cancelled() \
                and opening.exception() is None else None
            if self._open_abandoned:
                # Shutdown abandonment: queue records accumulated while awaiting upload header (possibly none) after file creation on the same single-writer queue,
                # header before rows, then close fd. Keep every created log for next startup recovery, even header-only (empty transcripts and usage still upload);
                # if creation failed, write nothing. Retain registration until the queued recovery write closes fd, preventing upload workers from
                # resealing a still-writing log as orphaned (keep registration if creation stalls; process exits soon and next startup recovers normally).
                finishing = executor.submit(self._finish_abandoned_open, open_job, list(self._abandoned_tail))
                if added:
                    visit_id = self.visit_id
                    finishing.add_done_callback(lambda _f: _open_streams.discard(visit_id))
            else:
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                    with contextlib.suppress(OSError):
                        self.stream_path.unlink(missing_ok=True)
                if added:
                    _open_streams.discard(self.visit_id)
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
        record = self._book(lp=lp, side=side, speaker=speaker, ts=ts, text=text, truncated=truncated)
        # Do not withdraw an accepted row because its caller was cancelled.
        await asyncio.shield(self._submit(self._append_sync, _encode_record(record)))

    def book_line(
        self, *, lp: int, side: str, speaker: str, ts: float, text: str, truncated: bool,
    ) -> None:
        """Record one final line now and write it in the background (lines buffered before the header).

        Synchronous like :meth:`note_usage`: the in-memory copy (what
        :meth:`seal` writes) has the line at once; the stream write keeps its
        order on the single writer thread.
        """
        self._fire(self._book(lp=lp, side=side, speaker=speaker, ts=ts, text=text, truncated=truncated))

    def remember_line(
        self, *, lp: int, side: str, speaker: str, ts: float, text: str, truncated: bool,
    ) -> None:
        """Keep a line in the in-memory copy only (the stream could not be created; nothing is written).

        ``lines()`` (the ``GET /state`` replay, the debrief) still sees it;
        there is no upload without a stream.
        """
        if self._executor is not None or self._sealed:
            return
        if side not in SIDE_RANK or speaker not in SPEAKERS:
            raise ValueError("bad line side / speaker")
        self._records.append({"kind": "line", "lp": int(lp), "side": side, "from": speaker, "ts": float(ts),
                              "text": str(text), "truncated": bool(truncated)})

    def abandon_open(self, records: Iterable[Mapping[str, Any]] = ()) -> None:
        """Shutdown: a cancelled :meth:`open` stops at once instead of waiting for the stalled create.

        The process is about to exit; whatever the writer thread leaves on
        disk is a stream the startup recovery seals. ``records`` (stream
        records buffered while the header was being written) are queued on
        the writer thread right after the create, so that stream has them.
        """
        self._open_abandoned = True
        self._abandoned_tail = [_encode_record(record) for record in records]

    @staticmethod
    def _finish_abandoned_open(open_job: concurrent.futures.Future, tail: list[bytes]) -> None:
        # On writer thread, queued after creation: if successful, append accumulated records then close fd; otherwise do nothing.
        try:
            fd = open_job.result()
        except Exception:  # noqa: BLE001 - Creation failed / cancelled (concurrent CancelledError also inherits Exception): no log to complete.
            return
        try:
            for data in tail:
                _write_all(fd, data)
        except OSError as exc:
            # Recovered transcripts miss these rows; leave a log for later investigation.
            logger.warning("visit upload: buffered records not written after an abandoned open: %s",
                           type(exc).__name__)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def _book(self, *, lp: int, side: str, speaker: str, ts: float, text: str, truncated: bool) -> dict:
        """Validate one final line and add it to the in-memory copy; returns the record to write."""
        if not self.is_open:
            raise RuntimeError("upload stream is not open")
        if side not in SIDE_RANK or speaker not in SPEAKERS:
            raise ValueError("bad line side / speaker")
        record = {"kind": "line", "lp": int(lp), "side": side, "from": speaker, "ts": float(ts),
                  "text": str(text), "truncated": bool(truncated)}
        self._records.append(record)
        self._last_ts = float(ts)
        return record

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
            # Telemetry records low-cardinality dimensions only, without visit_id (transcript upload is the sole usage record with visit_id).
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

    @property
    def seal_write(self) -> "asyncio.Future | None":
        """The (single) seal write started by :meth:`seal`, or None before it."""
        return self._seal_write

    async def seal(self, finalized_reason: str, *, ended_at: float | None = None) -> dict | None:
        """Write ``.upload.json`` from memory, then delete the stream; returns the document.

        Idempotent: a call while the seal is being written (or after it was)
        waits for that same write and returns the same document, so no caller
        mistakes an unwritten seal for a finished one. None when the journal
        was never opened. Called by finalize (before ``state.json.finalized``)
        and by the shutdown hook.
        Nothing is uploaded here: once the runtime is unregistered (``is_live``
        false) the caller must call :func:`schedule_visit_retry`, otherwise a
        report queued during the visit waits for the next start's recovery.
        ``ended_at`` (default: now) is the finalize time: a quiet tail after
        the last record still counts toward the duration. Crash recovery,
        which has no finalize time, uses the last record instead.
        """
        if self._seal_write is not None:
            # Already sealing/sealed: await the same persistence operation instead of launching another or returning None as though complete.
            return await asyncio.shield(self._seal_write)
        if self._sealed or self._executor is None:
            return None
        doc = build_upload_doc(self._records, visit_id=self.visit_id, finalized_reason=finalized_reason)
        if doc is None:
            raise RuntimeError("upload journal has no valid header")
        _stamp_end(doc["request"], time.time() if ended_at is None else ended_at)
        remember_anomalies(self.visit_id, doc["request"].get("anomalies"), doc.get("own_visit_uid"))
        self._sealed = True
        write = self._seal_write = asyncio.ensure_future(self._write_seal(doc, finalized_reason))
        write.add_done_callback(lambda t: t.cancelled() or t.exception())  # Retrieve exceptions even when all callers were cancelled.
        return await asyncio.shield(write)

    async def _write_seal(self, doc: dict, finalized_reason: str) -> dict:
        executor = self._executor
        try:
            await asyncio.wrap_future(executor.submit(self._seal_sync, doc))
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
_recent_anomalies: "OrderedDict[str, tuple[int, str | None]]" = OrderedDict()
"""Anomaly counts of visits sealed or uploaded in this process.

An uploaded transcript's files are deleted, but a report filed afterwards
still carries the count. (Servers keeps the count of every uploaded
transcript as well, so one lost across a restart is not lost evidence.)
"""


def remember_anomalies(visit_id: str, count: Any, owner: Any = None) -> None:
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return
    _recent_anomalies[visit_id] = (count, owner if isinstance(owner, str) and owner else None)
    _recent_anomalies.move_to_end(visit_id)
    while len(_recent_anomalies) > _RECENT_ANOMALIES_MAX:
        _recent_anomalies.popitem(last=False)


def _write_all(fd: int, data: bytes) -> None:
    # Write a whole line at once; regular files rarely short-write, but write the remainder if they do.
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _encode_record(record: Mapping[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def build_visit_usage(journal: UploadJournal, *, now: float | None = None) -> dict:
    """Usage of one visit so far (the ``usage`` object of the transcript upload)."""
    return journal.usage(now=now)


# ── Chunking ───────────────────────────────────────────────────────────────


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


# ── Upload ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UploadResult:
    """One upload attempt: ``done`` (accepted / duplicate), ``terminal`` (reason) or retry later."""

    done: bool = False
    terminal: str | None = None
    retry_after_s: int | None = None
    login_required: bool = False
    """The owning account's Servers session is gone (not signed in / ``401``)."""
    sent: bool = False
    """A request to Servers was made (False: not signed in / another account, nothing sent)."""

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
            # File deleted elsewhere (upload succeeded / abandoned): do not recreate.
            return
        _write_private_json(path, doc)
        try:
            # Chunk progress is not a retry; calculate the 7-day retention from original mtime.
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        except OSError as exc:
            logger.warning("visit upload: cannot keep the age of %s: %s", path.name, exc)


def _persist_owner_sync(path: Path, owner: str) -> None:
    """Write ``own_visit_uid`` into a sealed upload that lacks it (age kept)."""
    with path_lock(path):
        try:
            before = path.stat()
        except FileNotFoundError:
            return
        doc = _load_json(path)
        if not isinstance(doc, dict) or doc.get("own_visit_uid"):
            return
        _write_private_json(path, {**doc, "own_visit_uid": owner})
        try:
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
        # Only the account owning this role may upload (Servers recognizes only that account); retain on logout/account switch until it returns.
        memory_bridge.diag("upload_account_mismatch", visit_id=visit_id)
        return UploadResult()
    return replace(await _send_upload(visit_id, doc, config_dir, session), sent=True)


async def _send_upload(visit_id: str, doc: dict, config_dir: Path, session: Any) -> UploadResult:
    request = doc["request"]
    remember_anomalies(visit_id, request.get("anomalies"), doc.get("own_visit_uid"))
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
            # All chunks accepted but no complete seen (earlier receipts or progress saved by a previous retry): only Servers'
            # assembled group counts as complete. Resend the last chunk idempotently for a complete receipt instead of wasting a pass.
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
                    # Noncontractual 2xx (204, proxy HTML, etc.) is not acceptance; retain file and retry later.
                    logger.warning("visit upload %s: status=%s without a valid receipt, kept for retry",
                                   visit_id, status)
                    return UploadResult()
                if parts == 1:
                    return UploadResult(done=True)
                server_view = _accepted_parts(body, parts)
                if server_view is None:
                    # Every successful chunk response includes accepted_parts; do not infer Servers acceptance when missing.
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
                # New chunk generation: rechunk and resend the whole group, clearing accepted set (parts in idempotency key prevents collision with old generation).
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
                # Original account's login expired: retain file and prompt login (reports attaching transcript are also waiting).
                return UploadResult(login_required=True)
            if status < 500:
                logger.warning("visit upload %s: status=%s code=%s, kept for retry", visit_id, status, cr._diag_code(code))
            return UploadResult()
    # No complete receipt after several rounds: retain for another attempt.
    return UploadResult()


async def upload_visit_transcript(visit_id: str, upload_doc: dict) -> bool | str:
    """PR-08's ``upload_transcript`` callback: one attempt for a sealed ``.upload.json`` document.

    True when Servers has the transcript (accepted / duplicate), the Servers
    code of a terminal rejection, False to keep the file for a later retry
    (network, 5xx, 429, ``visit_not_started{final:false}``, not signed in or
    another account signed in). Chunk progress is written into the file.
    """
    visit_id = require_visit_id(visit_id)
    config_dir = Path(config_dir_provider())
    # Share per-visit lock with retry passes; chunk-progress persistence and upload settlement cannot interleave with another pass.
    async with visit_lock(visit_id):
        owner = upload_doc.get("own_visit_uid")
        if isinstance(owner, str) and owner:
            # Recovery-added ownership for legacy files is memory-only: persist first so background rereads identify the original account; if persistence fails,
            # retain in process for background passes to use and retry writing.
            await _record_owner(config_dir, visit_id, owner)
        result = await _upload(visit_id, upload_doc, config_dir)
    _note_upload_retry_after(visit_id, result.retry_after_s, owner)
    if result.terminal:
        # After recovery deletes sealed file, this is the only remaining reason; later reports attaching transcript must include it too.
        remember_terminal_reason(visit_id, result.terminal, owner if isinstance(owner, str) and owner else None)
    # Recovery runs once; background checks continue in this process. Continue unfinished uploads; if deletion after upload / terminal settlement
    # fails due to file locking, the next pass resends for duplicate acknowledgement and deletes (do nothing if already deleted).
    schedule_visit_retry(visit_id, config_dir=config_dir,
                         initial_delay_s=max(VISIT_UPLOAD_RETRY_BACKOFF_S[0], result.retry_after_s or 0))
    return result.callback_value


_pending_owners: dict[str, str] = {}
"""visit_id -> recovered ``own_visit_uid`` not yet written into its ownerless sealed file."""


async def _record_owner(config_dir: Path, visit_id: str, owner: str) -> bool:
    try:
        await asyncio.to_thread(
            _persist_owner_sync, visit_path(_spool_dir(config_dir), visit_id, UPLOAD_JSON_SUFFIX), owner)
    except (OSError, ValueError) as exc:
        logger.warning("visit upload %s: cannot record the owner: %s", visit_id, type(exc).__name__)
        _pending_owners[visit_id] = owner
        return False
    _pending_owners.pop(visit_id, None)
    return True


# ── Report queue ───────────────────────────────────────────────────────────


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
            # Corrupt / wrong-visit report is invisible in queue listings and cannot be retried or abandoned; it must not permanently block
            # new reports for this visit. Rename as evidence and enqueue normally (propagate OSError on reading as persistence failure).
            if move_aside(path, "invalid") is None:
                raise OSError(f"cannot move {path.name} aside")
            logger.warning("visit report queue: unreadable %s moved aside", path.name)
        _write_private_json(path, doc)


async def queue_report(config_dir: Path, doc: dict) -> None:
    """Write a new queued report (before any request is sent); raises if one is queued already."""
    await asyncio.to_thread(_queue_report_sync, report_path(config_dir, doc["visit_id"]), doc)
    # New report: retry_after from the prior abandoned / accepted report no longer applies.
    restart_retry_deadline(doc["visit_id"])


def _valid_report(doc: Any, visit_id: str) -> bool:
    # Reports without usable ownership (both account and visit_uid absent) are invisible, unsubmitable, and unabandonable: treat as corrupt files.
    return (
        isinstance(doc, dict) and doc.get("visit_id") == visit_id
        and doc.get("reason") in REPORT_REASONS and isinstance(doc.get("include_transcript"), bool)
        and any(isinstance(doc.get(k), str) and doc.get(k) for k in ("own_account", "own_visit_uid"))
        # queued_at is part of report identity: NaN differs from itself, making one report appear replaced.
        and _finite_or_absent(doc.get("queued_at"))
        # Apply the same note validation as report intake: notes invalid for requests in legacy/corrupt files would make every submission fail locally.
        and _valid_note(doc.get("note"))
    )


def _valid_note(note: Any) -> bool:
    if note is None:
        return True
    if not isinstance(note, str) or len(note) > VISIT_REPORT_NOTE_MAX_CHARS:
        return False
    try:
        note.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _finite_or_absent(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))


async def load_report(config_dir: Path, visit_id: str) -> dict | None:
    """The queued report of ``visit_id`` (None when none, or not this visit's)."""
    return (await _read_report(config_dir, visit_id))[0]


async def _read_report(config_dir: Path, visit_id: str) -> tuple[dict | None, bool]:
    """``(report, unreadable)``: ``unreadable`` when the file exists but cannot be read right now.

    Damaged content is not "right now": it is reported as no report (the next
    queueing moves it aside).
    """
    try:
        doc = await asyncio.to_thread(_load_json, report_path(config_dir, visit_id))
    except OSError as exc:
        logger.warning("visit report queue: %s unreadable: %s", visit_id, type(exc).__name__)
        return None, True
    except ValueError as exc:
        logger.warning("visit report queue: %s unreadable: %s", visit_id, type(exc).__name__)
        return None, False
    return (doc if _valid_report(doc, visit_id) else None), False


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


def _mark_unavailable_sync(path: Path, visit_id: str, reason: str, owner: str | None = None) -> None:
    with path_lock(path):
        doc = _load_json(path)
        if not _valid_report(doc, visit_id) or doc.get("transcript_unavailable") or not doc["include_transcript"]:
            # Reports without transcript attachment do not record transcript absence reasons.
            return
        if owner is not None and doc.get("own_visit_uid") and doc["own_visit_uid"] != owner:
            # Report queued by another account on a shared computer: this transcript is not its side; do not record reasons for it (record if ownership unknown).
            return
        _write_private_json(path, {**doc, "transcript_unavailable": reason})


def same_report(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether two loaded copies are the same queued report (not a later one for the same visit)."""
    return (
        a is not None and b is not None
        and all(a.get(k) == b.get(k) for k in REPORT_IDENTITY)
    )


def _set_rejected_sync(path: Path, visit_id: str, reason: str | None, expect: Mapping[str, Any] | None) -> None:
    with path_lock(path):
        doc = _load_json(path)
        if not _valid_report(doc, visit_id) or doc.get("rejected") == reason:
            return
        if expect is not None and not same_report(doc, expect):
            # Report abandoned and replaced during submission (other account / new report): do not mark the replacement rejected.
            return
        doc = {k: v for k, v in doc.items() if k != "rejected"}
        if reason is not None:
            doc["rejected"] = reason
        _write_private_json(path, doc)


async def rejection_recorded(config_dir: Path, visit_id: str) -> bool:
    """Whether the queued report of ``visit_id`` carries its ``rejected`` marker (or is gone).

    A file that exists but cannot be read right now does not count: the
    marker may well be missing, so the caller keeps a retry pending.
    """
    try:
        doc = await asyncio.to_thread(_load_json, report_path(config_dir, visit_id))
    except (OSError, ValueError):
        return False
    return not _valid_report(doc, visit_id) or bool(doc.get("rejected"))


async def set_report_rejected(
    config_dir: Path, visit_id: str, reason: str | None, *, expect: Mapping[str, Any] | None = None,
) -> bool:
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
        return False
    return True


async def mark_report_transcript_unavailable(
    config_dir: Path, visit_id: str, reason: str, owner: str | None = None,
) -> bool:
    """Record on the queued report why its transcript will never reach Servers.

    False only when a queued report exists but could not be rewritten (no
    report, one already marked, or -- with ``owner`` -- another account's
    report, counts as done).
    """
    try:
        await asyncio.to_thread(_mark_unavailable_sync, report_path(config_dir, visit_id), visit_id, reason, owner)
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
    if body["include_transcript"] and isinstance(unavailable, str) and unavailable:
        # Only reports attaching transcripts explain their absence; adding this field without attachment would contradict the request.
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
        # Submit only with the account logged in at reporting time (Servers recognizes actual participants); retain on account switch/logout.
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
        # Noncontractual 2xx is not acceptance: retain queued file and retry later.
        logger.warning("visit report: status=%s without a valid receipt, kept queued", resp.status_code)
        return ReportResult(attempted=True)
    if resp.status_code == 404 and _code(body) == "unknown_visit":
        memory_bridge.diag("report_unknown_visit", visit_id=str(doc.get("visit_id")))
        return ReportResult(unknown_visit=True, attempted=True)
    if resp.status_code == 401:
        return ReportResult(login_required=True, attempted=True)
    if resp.status_code == 429:
        retry_after = cr._retry_after(body, resp)
        _note_report_retry_after(doc, retry_after)
        return ReportResult(attempted=True, retry_after_s=retry_after)
    return ReportResult(attempted=True)


_report_not_before: dict[str, tuple[float, Any]] = {}
"""visit_id -> (monotonic deadline, ``queued_at`` of the report that got the ``Retry-After``)."""


def _note_report_retry_after(doc: Mapping[str, Any], retry_after_s: int | None) -> None:
    visit_id = doc.get("visit_id")
    if retry_after_s and isinstance(visit_id, str):
        _report_not_before[visit_id] = (time.monotonic() + retry_after_s, doc.get("queued_at"))


def report_deferred_s(visit_id: str, doc: Mapping[str, Any]) -> float:
    """Seconds left before this queued report may be resent (its own ``Retry-After``); 0 when none.

    Keyed to the report's ``queued_at``: a deadline of an abandoned report
    does not hold back the one queued after it.
    """
    noted = _report_not_before.get(visit_id)
    if noted is None or noted[1] != doc.get("queued_at"):
        return 0.0
    return max(0.0, noted[0] - time.monotonic())


async def submit_queued_report(visit_id: str, report_doc: dict) -> bool:
    """PR-08's ``submit_report`` callback: True once Servers accepted the queued report.

    A report already refused (``rejected``) is not resent; a new ``404
    unknown_visit`` marks it so (the file stays for the user to decide).
    """
    visit_id = require_visit_id(visit_id)
    config_dir = Path(config_dir_provider())
    async with visit_lock(visit_id):
        # Share per-visit lock with endpoint abandon / retry; report may be abandoned or replaced while waiting.
        current, unreadable = await _read_report(config_dir, visit_id)
        if unreadable:
            # Temporarily unreadable (locked) does not mean absent: recovery runs once; background rechecks and submits when readable.
            schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=VISIT_UPLOAD_RETRY_BACKOFF_S[0])
            return False
        if current is None or not same_report(current, report_doc) or current.get("rejected"):
            return False
        result = await send_report(report_doc)
        if result.accepted:
            # On acceptance delete under lock (later recovery deletion finds nothing), closing the window for deleting a new report outside lock.
            await _delete_accepted_report(config_dir, visit_id)
            return True
        if result.unknown_visit:
            if not await set_report_rejected(config_dir, visit_id, "unknown_visit", expect=report_doc):
                # Rejection marker write failed (disk / permissions): recovery runs once; let the next background pass retry recording.
                schedule_visit_retry(visit_id, config_dir=config_dir,
                                     initial_delay_s=VISIT_UPLOAD_RETRY_BACKOFF_S[0])
            return False
    # Network / 5xx / 429 / expired login: recovery retains file; background retries in this process rather than waiting for restart.
    schedule_visit_retry(visit_id, config_dir=config_dir,
                         initial_delay_s=max(VISIT_UPLOAD_RETRY_BACKOFF_S[0], result.retry_after_s or 0))
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
        # Sealed file retained because rejection reason could not be recorded: report now accepted, so it is useless; free pending-upload capacity.
        if not await asyncio.to_thread(_drop_rejected_sealed_sync, config_dir, visit_id,
                                       _terminal_reasons.get(visit_id)):
            # Cannot delete (locked): mark settled-pending-deletion; subsequent passes retry deletion only, without upload.
            _settled_leftovers.add(visit_id)
            schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=VISIT_UPLOAD_RETRY_BACKOFF_S[0])
        return True
    if result.unknown_visit:
        await set_report_rejected(config_dir, visit_id, "unknown_visit", expect=doc)
    return False


# ── In-process retry ─────────────────────────────────────────────────────────


def _file_age_s(path: Path, now: float) -> float | None:
    try:
        return now - path.stat().st_mtime
    except FileNotFoundError:
        return None


def _drop_rejected_sealed_sync(
    config_dir: Path, visit_id: str, known_terminal: tuple[str, str | None] | None = None,
) -> bool:
    """Delete a sealed upload kept only as the record of a rejection; False when it is still there."""
    path = visit_path(_spool_dir(config_dir), visit_id, UPLOAD_JSON_SUFFIX)
    with path_lock(path):
        try:
            doc = _load_json(path)
        except OSError:
            return False
        except ValueError:
            return True
        marked = isinstance(doc, dict) and isinstance(doc.get("rejected"), str) and doc["rejected"]
        # Unknown ownership (None) is not a match: retain files without rejection markers whose ownership cannot be matched.
        same_owner = (
            isinstance(doc, dict) and known_terminal is not None and known_terminal[1] is not None
            and doc.get("own_visit_uid") == known_terminal[1]
        )
        if marked or same_owner:
            # Also delete when rejection marking failed but this process knows terminal settlement (known_terminal, matching ownership; do not touch
            # another account's side of the transcript on shared computers).
            # Any log left from sealing belongs to this rejected transcript too; delete to prevent resealing/reupload. Delete log first and marked
            # sealed file last, so failed log deletion retains the marker and startup recovery cannot reseal it as unuploaded.
            stream = visit_path(_spool_dir(config_dir), visit_id, UPLOAD_JSONL_SUFFIX)
            for target in (stream, path):
                try:
                    target.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("visit upload %s: cannot delete rejected %s: %s", visit_id, target.name, exc)
                    return False
    return True


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
        # Recording a marker is not a retry; preserve the 7-day deadline from original mtime.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


async def _settle_upload(
    config_dir: Path, visit_id: str, path: Path, result: UploadResult, owner: str | None = None,
) -> str | None:
    """Delete a finished sealed upload; return a terminal reason the queued report could not record.

    A terminal rejection (or expiry) is written into the queued report
    first. The sealed file is the only durable record of it, so when the
    report cannot be rewritten the file stays, marked ``rejected`` (the next
    round and PR-08 recovery mark the report, then delete the file), and
    the reason is returned so the report submitted in this round carries it.
    """
    if result.terminal is not None:
        memory_bridge.diag("upload_rejected", visit_id=visit_id, reason=result.terminal)
        if not await mark_report_transcript_unavailable(config_dir, visit_id, result.terminal, owner):
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
    unknown_visit: bool = False
    """Servers rejected the queued report again (``404 unknown_visit``) this round."""


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
    owner: str | None = None
    """``own_visit_uid`` of the transcript this round looked at (None when unknown)."""

    def for_report(self, report: Mapping[str, Any] | None) -> "UploadRound":
        """This round as seen by ``report``: another account's transcript neither gates it nor lends it a reason.

        On a computer shared by several community accounts the sealed file of
        a visit can be the other participant's side; a report only waits for
        (and carries the unavailability of) its own account's transcript.
        """
        report_owner = report.get("own_visit_uid") if report is not None else None
        if report is None or self.owner is None or not report_owner or self.owner == report_owner:
            # Unknown ownership on either side (no local visit_uid mapping when report queued): treat as the same side.
            return self
        return replace(self, pending=False, unavailable=None, login_required=False)


async def attempt_upload(visit_id: str, *, config_dir: Path, now: float | None = None) -> UploadRound:
    """One upload attempt of ``visit_id``'s sealed transcript; settles the file when done.

    A sealed file older than ``VISIT_SPOOL_RETENTION_DAYS`` is given up (its
    queued report records ``transcript_unavailable:'expired'``), as is a
    terminal rejection (with the Servers code).
    """
    now = time.time() if now is None else now
    spool_dir = _spool_dir(config_dir)
    sealed = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
    if visit_id in _settled_leftovers:
        # Settled, prior deletion merely failed: retry deletion directly without upload (logout/account switching prevents upload and would leave capacity occupied).
        if not await _drop_settled_files(visit_id, sealed, stream):
            return UploadRound(pending=False, retryable=True)
        _settled_leftovers.discard(visit_id)
        return UploadRound(pending=False)
    age = await asyncio.to_thread(_file_age_s, sealed, now)
    if age is None:
        if not await asyncio.to_thread(stream.exists):
            # Transcript already settled: terminal rejection / expiry reasons must also reach reports queued afterward.
            reason, owner = _terminal_reasons.get(visit_id, (None, None))
            return UploadRound(pending=False, unavailable=reason, owner=owner)
        if is_live(visit_id) or visit_id in _open_streams:
            # Log still being written during active visit: wait for final sealing; never reseal early.
            return UploadRound(pending=True)
        # Visit ended with only log left (failed upload-file write during sealing): reseal from log using recovery rules, then upload.
        status, stream_owner = await reseal_orphan_stream(config_dir, visit_id)
        if status == "corrupt":
            # Carry ownership from log header / state.json so shared computers do not record this transcript's failure on another account's report.
            remember_terminal_reason(visit_id, "corrupt", stream_owner)
            marked = await mark_report_transcript_unavailable(config_dir, visit_id, "corrupt", stream_owner)
            return UploadRound(pending=False, unavailable=None if marked else "corrupt", owner=stream_owner)
        age = await asyncio.to_thread(_file_age_s, sealed, now) if status == "sealed" else None
        if age is None:
            # Temporarily cannot seal: use ownership from header / state.json; another account's transcript-attaching report need not wait on this file.
            return UploadRound(pending=True, retryable=True, owner=stream_owner)
    try:
        doc = await asyncio.to_thread(_load_json, sealed)
    except OSError as exc:
        # Temporarily unreadable (lock / permissions) is not corruption: count as not uploaded this pass and let worker retry.
        logger.warning("visit upload %s: cannot read %s: %s", visit_id, sealed.name, type(exc).__name__)
        return UploadRound(pending=True, retryable=True)
    except ValueError:
        doc = None
    if sealed_upload_doc_usable(doc, visit_id):
        matches, state_owner = await sealed_upload_doc_matches_state(config_dir, visit_id, doc)
        if matches is None:
            # state.json exists but is temporarily unreadable/unrecognized: verification is impossible; do not upload as though verified; retain this pass.
            logger.warning("visit upload %s: visit state unreadable, upload deferred", visit_id)
            return UploadRound(pending=True, retryable=True)
        if not matches:
            # Matching visit format but character/account disagrees with state.json (copied / another account's file): do not upload or trust its account;
            # report state's account and let startup recovery decide quarantine/reseal.
            logger.warning("visit upload %s: sealed file disagrees with the visit state, left to recovery", visit_id)
            return UploadRound(pending=True, owner=state_owner)
        if not doc.get("own_visit_uid") and state_owner:
            # Legacy ownerless sealed file: supplement from state.json and persist like startup recovery, or no logged-in account can upload.
            await _record_owner(config_dir, visit_id, state_owner)
            doc = {**doc, "own_visit_uid": state_owner}
    # Trust accounts only in files for this visit (including newer-version files retained unchanged); wrong-visit / corrupt accounts are unknown.
    doc_trusted = sealed_upload_doc_usable(doc, visit_id) or sealed_upload_doc_from_another_version(doc, visit_id)
    owner = doc.get("own_visit_uid") if doc_trusted else None
    owner = owner if isinstance(owner, str) and owner else None
    if owner is None and doc_trusted and visit_id in _pending_owners:
        # Recovery-supplied ownership not yet persisted: use memory ownership and retry persistence opportunistically.
        owner = _pending_owners[visit_id]
        await _record_owner(config_dir, visit_id, owner)
        doc = {**doc, "own_visit_uid": owner}
    aged = age > VISIT_SPOOL_RETENTION_DAYS * 86400
    if (aged and visit_id not in _aged_attempted and not sealed_upload_doc_usable(doc, visit_id)
            and not (is_live(visit_id) or visit_id in _open_streams)
            and await asyncio.to_thread(stream.exists)):
        # Expired sealed file corrupt, with intact log beside it (log deletion failed after sealing, then sealed file corrupted): like startup recovery,
        # reseal from log first, then attempt upload once before expiry; do not delete the only recoverable transcript untried.
        status, stream_owner = await reseal_orphan_stream(config_dir, visit_id)
        if status == "failed":
            # Temporarily cannot seal (read/write errors): retain both for next pass, avoiding expiry deletion of a recoverable log below.
            # If corrupt file cannot provide ownership, use header / state.json.
            return UploadRound(pending=True, retryable=True, owner=owner or stream_owner)
        if status == "corrupt":
            # Adjacent log also corrupt (resealer deleted it): transcript is unrecoverable; settle as corrupt, taking ownership from header /
            # state.json because corrupt sealed file cannot supply it.
            return await _settled_round(config_dir, visit_id, sealed, UploadResult(terminal="corrupt"),
                                        owner=owner or stream_owner)
        if status == "sealed":
            try:
                doc = await asyncio.to_thread(_load_json, sealed)
            except OSError as exc:
                # Newly resealed file temporarily unreadable (locked): not corrupt; retain past expiry and reread next pass.
                logger.warning("visit upload %s: cannot read %s: %s", visit_id, sealed.name, type(exc).__name__)
                return UploadRound(pending=True, retryable=True, owner=owner or stream_owner)
            except ValueError:
                doc = None
            owner = doc.get("own_visit_uid") if isinstance(doc, dict) else None
            owner = owner if isinstance(owner, str) and owner else None
    if aged and (visit_id in _aged_attempted or not sealed_upload_doc_usable(doc, visit_id)):
        memory_bridge.diag("upload_expired", visit_id=visit_id)
        return await _settled_round(config_dir, visit_id, sealed, UploadResult(terminal="expired"), owner=owner)
    if not sealed_upload_doc_usable(doc, visit_id):
        # Unreadable / invalid structure / wrong-visit or different-version sealed file: do not upload directly; let startup recovery decide
        # by comparing log and state.json to reseal, quarantine, or retain for newer versions. Wrong-visit / corrupt file accounts are untrusted;
        # report unknown ownership (transcript-attaching reports keep waiting). Recovery retains this visit's newer-version files; their account is trusted.
        return UploadRound(pending=True, owner=owner)
    rejected = doc.get("rejected")
    if isinstance(rejected, str) and rejected:
        # Previous pass reached terminal rejection but could not record the reason in report: no full reupload; continue reason recording and deletion.
        result = UploadResult(terminal=rejected)
    else:
        result = await _upload(visit_id, doc, config_dir)
    attempted = result.sent or result.done or result.terminal is not None
    if aged and attempted:
        # Try uploading once before expiring retained transcripts (same order as startup recovery: after long app downtime, a report submitted on startup
        # must not lose its only transcript untried). Only an actual request counts: local preparation failure, no login, or another account
        # means no upload attempt; retain for original account.
        _aged_attempted.add(visit_id)
    if result.done or result.terminal is not None:
        return await _settled_round(config_dir, visit_id, sealed, result, owner=owner)
    if aged and attempted:
        memory_bridge.diag("upload_expired", visit_id=visit_id)
        return await _settled_round(config_dir, visit_id, sealed, UploadResult(terminal="expired"), owner=owner)
    # Record transcript's own Retry-After in upload deadline table (shared by synchronous endpoint attempts and retry passes).
    _note_upload_retry_after(visit_id, result.retry_after_s, owner)
    return UploadRound(pending=True, retryable=True, retry_after_s=result.retry_after_s,
                       login_required=result.login_required, owner=owner)


async def _settled_round(
    config_dir: Path, visit_id: str, sealed: Path, result: UploadResult, *, owner: str | None = None,
) -> UploadRound:
    if result.terminal is not None:
        remember_terminal_reason(visit_id, result.terminal, owner)
    unmarked = await _settle_upload(config_dir, visit_id, sealed, result, owner)
    # Settled (uploaded, expired, or terminal reason saved in report), but sealed file / leftover log remains locked: transcript no longer blocks
    # report, but retry cleanup this pass or they occupy pending-upload capacity forever. Exclude intentionally retained files whose reason is not yet saved
    # (marked rejected).
    stream = visit_path(_spool_dir(config_dir), visit_id, UPLOAD_JSONL_SUFFIX)
    leftover = False
    if unmarked is None:
        stream_gone = await _drop_settled_files(visit_id, stream)
        leftover = not stream_gone or await asyncio.to_thread(sealed.exists)
    if leftover:
        _settled_leftovers.add(visit_id)
    return UploadRound(pending=False, retryable=leftover, unavailable=unmarked, owner=owner)


async def _drop_settled_files(visit_id: str, *paths: Path | None) -> bool:
    """Delete what is left of a settled upload; False when something is still there."""
    ok = True
    for path in paths:
        if path is None:
            continue
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            logger.warning("visit upload %s: still cannot delete %s: %s", visit_id, path.name, exc)
            ok = False
    return ok


_aged_attempted: set[str] = set()
"""visit_ids whose past-retention transcript was already tried once in this process (next time: expire)."""

_terminal_reasons: "OrderedDict[str, tuple[str, str | None]]" = OrderedDict()
"""Terminal reasons (rejection code / ``expired``) of transcripts settled in this process.

The sealed file is deleted once settled; a report with ``include_transcript``
filed afterwards still carries the reason. Kept in memory only, like the
anomaly counts: after a restart such a report goes without it.
"""


def remember_terminal_reason(visit_id: str, reason: str, owner: str | None = None) -> None:
    _terminal_reasons[visit_id] = (reason, owner)
    _terminal_reasons.move_to_end(visit_id)
    while len(_terminal_reasons) > _RECENT_ANOMALIES_MAX:
        _terminal_reasons.popitem(last=False)


_VISIT_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def visit_lock(visit_id: str) -> asyncio.Lock:
    """Per-visit lock serializing upload / report rounds (endpoint, background retry, manual retry)."""
    lock = _VISIT_LOCKS.get(visit_id)
    if lock is None:
        lock = asyncio.Lock()
        _VISIT_LOCKS[visit_id] = lock
    return lock


async def _upload_round(visit_id: str, config_dir: Path, now: float | None) -> UploadRound:
    try:
        return await attempt_upload(visit_id, config_dir=config_dir, now=now)
    except (OSError, ValueError) as exc:
        # Local upload bookkeeping fails (e.g. full disk): count transcript as not uploaded this pass, but still submit reports without attachments.
        logger.warning("visit upload %s: attempt failed: %s", visit_id, type(exc).__name__)
        return UploadRound(pending=True, retryable=True)


async def retry_visit_once(
    visit_id: str, *, config_dir: Path | None = None, now: float | None = None, manual: bool = False,
    owner: str | None = None, expect: Mapping[str, Any] | None = None,
) -> RetryRound:
    """One round for ``visit_id``: upload its sealed transcript, then submit its queued report.

    A report with ``include_transcript:true`` waits for the transcript
    (accepted, or terminally rejected / expired); ``false`` does not. A
    report Servers refused (``rejected``) is only resent when the user asks
    (``manual``); it no longer keeps the background loop going. ``owner``
    (the account that asked for a manual retry) is checked under the lock:
    a report queued meanwhile by another account is left alone. ``expect``
    (the report the user asked to retry): when it cannot be read this round,
    the retry stays pending for that same report.
    """
    visit_id = require_visit_id(visit_id)
    config_dir = Path(config_dir_provider() if config_dir is None else config_dir)
    async with visit_lock(visit_id):
        deferred = upload_deferred_s(visit_id)
        if deferred > 0:
            # Transcript's Retry-After not reached (manual retry too): skip upload this pass, remain pending, and retry after remaining delay.
            upload = UploadRound(pending=True, retryable=True, retry_after_s=math.ceil(deferred),
                                 owner=_upload_deadline_owner.get(visit_id))
        else:
            upload = await _upload_round(visit_id, config_dir, now)
        # Report temporarily unreadable (same read result): unfinished work remains; worker must stay alive and submit / record when readable.
        report, unreadable = await _read_report(config_dir, visit_id)
        upload = upload.for_report(report)
        if report is not None and manual and expect is not None and not same_report(report, expect):
            # While awaiting lock, manually retried report was abandoned and replaced: do not submit replacement (reason / attachment selection may differ).
            report = None
        if report is not None and owner is not None and not await report_belongs_to(report, owner):
            # While awaiting lock, original report replaced by another account's: neither submit nor change its rejection marker.
            report = None
        # User requested retry but clearing rejection marker failed (disk / permissions): background treats the same report as manual retry despite
        # retained marker. Match that specific report only; invalidate if absent/replaced.
        pending_manual = _manual_retries.get(visit_id)
        if unreadable:
            # Unreadable is not absent: retain until identifiable; remember this manually retried report for continuing background manual-retry handling.
            if manual and expect is not None:
                _manual_retries[visit_id] = dict(expect)
        elif report is None or not same_report(report, pending_manual):
            _manual_retries.pop(visit_id, None)
        elif not manual:
            manual = True
        if report is not None and report.get("rejected") and not manual:
            report = None
        report_pending = report is not None
        # Transcript-attaching report waits for transcript: expired login blocking upload must also prompt login.
        login_required = upload.login_required and report_pending and bool(report["include_transcript"])
        report_retry_after: int | None = None
        rejected_again = False
        report_wait = report_deferred_s(visit_id, report) if report is not None else 0.0
        if report is not None and report_wait > 0 and not (upload.pending and report["include_transcript"]):
            # This report's own Retry-After not reached (manual retry too): do not resubmit this pass; wait remaining time.
            report_retry_after = math.ceil(report_wait)
        elif report is not None and not (upload.pending and report["include_transcript"]):
            if upload.unavailable and report["include_transcript"] and not report.get("transcript_unavailable"):
                # Reason absent from report file (prior persistence failed, or transcript settled first with reason in memory only): persist first,
                # but include it in submission even if persistence fails.
                await mark_report_transcript_unavailable(config_dir, visit_id, upload.unavailable)
                report = {**report, "transcript_unavailable": upload.unavailable}
            result = await send_report(report)
            if result.accepted or result.unknown_visit:
                # Accepted or rejected again (awaiting user's next choice): this manual retry ends here.
                _manual_retries.pop(visit_id, None)
            login_required = login_required or result.login_required
            rejected_again = result.unknown_visit
            report_retry_after = result.retry_after_s
            if await finish_report(config_dir, visit_id, result, report) or (
                    result.unknown_visit and await rejection_recorded(config_dir, visit_id)):
                # Accepted but local deletion failed (locked): worker stays alive, resubmits next pass for duplicate receipt, then deletes.
                # Unreadable (locked) also means file still exists.
                current, still_unreadable = await _read_report(config_dir, visit_id)
                report_pending = result.accepted and (still_unreadable or same_report(current, report))
            elif (manual and report.get("rejected") and result.attempted and not result.login_required
                  and not result.unknown_visit):
                # Manual retry sent a request without another rejection (network / 5xx): return to normal queued retries.
                # Preserve rejection marker if unsent (logged out / different account) or login expired.
                if await set_report_rejected(config_dir, visit_id, None, expect=report):
                    _manual_retries.pop(visit_id, None)
                else:
                    _manual_retries[visit_id] = dict(report)
            elif report.get("rejected"):
                report_pending = False
    delays = [d for d in (upload.retry_after_s, report_retry_after) if d is not None]
    return RetryRound(pending=report_pending or upload.retryable or unreadable or visit_id in _settled_leftovers,
                      retry_after_s=max(delays) if delays else None,
                      login_required=login_required, unknown_visit=rejected_again)


_workers: dict[str, asyncio.Task] = {}

_manual_retries: dict[str, dict] = {}
"""visit_id -> the report the user asked to retry whose ``rejected`` marker could not be cleared yet."""

_settled_leftovers: set[str] = set()
"""visit_ids whose sealed upload is settled but could not be deleted yet (the next round only deletes it)."""


def cancel_retry_workers() -> list[asyncio.Task]:
    """Cancel every background upload / report retry worker (startup rollback); returns them."""
    tasks = [task for task in _workers.values() if not task.done()]
    for task in tasks:
        task.cancel()
    return tasks


def _reset_for_tests() -> None:
    cancel_retry_workers()
    _workers.clear()
    _recent_anomalies.clear()
    _terminal_reasons.clear()
    _aged_attempted.clear()
    _pending_owners.clear()
    _open_streams.clear()
    _manual_retries.clear()
    # Previous test's event loop may leave tasks holding locks: replace lock table instead of sharing across event loops.
    _VISIT_LOCKS.clear()
    _not_before.clear()
    _upload_not_before.clear()
    _upload_deadline_owner.clear()
    _report_not_before.clear()
    _wakeups.clear()
    _settled_leftovers.clear()


_not_before: dict[str, float] = {}
"""visit_id -> monotonic time before which no background round may run (Servers ``retry_after``)."""


_upload_not_before: dict[str, float] = {}
"""visit_id -> monotonic time before which the transcript must not be resent (its own ``Retry-After``)."""


_upload_deadline_owner: dict[str, str | None] = {}
"""visit_id -> ``own_visit_uid`` of the transcript that got the ``Retry-After`` (None: unknown)."""


def upload_deferred_s(visit_id: str, owner: str | None = None) -> float:
    """Seconds left before the transcript of ``visit_id`` may be resent (its own ``Retry-After``); 0 when none.

    With ``owner`` (a report's ``own_visit_uid``): 0 when the rate-limited
    transcript is another known account's side (a shared computer).
    """
    noted = _upload_deadline_owner.get(visit_id)
    if owner and noted and noted != owner:
        return 0.0
    return max(0.0, _upload_not_before.get(visit_id, 0.0) - time.monotonic())


def _note_upload_retry_after(visit_id: str, retry_after_s: int | None, owner: str | None = None) -> None:
    if retry_after_s:
        _upload_not_before[visit_id] = max(_upload_not_before.get(visit_id, 0.0),
                                           time.monotonic() + retry_after_s)
        _upload_deadline_owner[visit_id] = owner if isinstance(owner, str) and owner else None


_wakeups: dict[str, asyncio.Event] = {}
"""visit_id -> event that cuts a waiting worker's sleep short (its deadline was replaced)."""


def restart_retry_deadline(visit_id: str) -> None:
    """New work for ``visit_id`` (a newly queued report): the old ``retry_after`` no longer applies.

    The old deadline is dropped; a worker already waiting on it is woken and
    waits the first backoff step instead (not at once: the caller is about
    to make its own attempt, which then defers it as usual). A report
    abandoned under a long ``retry_after`` does not hold back the next one
    for the same visit.
    """
    # Honor transcript's own Retry-After: each pass uploads transcript first; a new report cannot trigger early retransmission.
    upload_floor = _upload_not_before.get(visit_id, 0.0)
    now = time.monotonic()
    task = _workers.get(visit_id)
    if task is None or task.done():
        if upload_floor > now:
            _not_before[visit_id] = upload_floor
        else:
            _not_before.pop(visit_id, None)
        return
    _not_before[visit_id] = max(now + VISIT_UPLOAD_RETRY_BACKOFF_S[0], upload_floor)
    event = _wakeups.get(visit_id)
    if event is not None:
        event.set()


async def _wait(visit_id: str, seconds: float) -> None:
    """Sleep ``seconds``, or less when :func:`restart_retry_deadline` replaces the deadline meanwhile."""
    event = _wakeups.setdefault(visit_id, asyncio.Event())
    event.clear()
    sleeper = asyncio.ensure_future(_sleep(seconds))
    waker = asyncio.ensure_future(event.wait())
    try:
        await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sleeper, waker):
            task.cancel()


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
                await _wait(visit_id, wait)
            if _not_before.get(visit_id, deadline) != deadline:
                # Deadline extended while waiting (manual retry received longer retry_after): wait for the new time.
                continue
            _not_before.pop(visit_id, None)
        if is_live(visit_id):
            # In-flight visit's transcript not yet sealed: finalize requeues it after sealing.
            return
        try:
            outcome = await retry_visit_once(visit_id, config_dir=config_dir)
        except Exception as exc:  # noqa: BLE001 - Background retry: record and retry next pass.
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

    A worker exits at once while the visit is live, so a visit's runtime
    calls this after it has sealed its journal and unregistered (reports
    queued during the visit are sent from here).

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
            # Wake events serve waiting background tasks only: remove on task completion instead of accumulating with visit count.
            _wakeups.pop(visit_id, None)
            _upload_not_before.pop(visit_id, None)
            _upload_deadline_owner.pop(visit_id, None)
            _report_not_before.pop(visit_id, None)

    task.add_done_callback(_done)
    return task


# ── Queries for endpoints and runtime ───────────────────────────────────────────────


def upload_pending_sync(config_dir: Path, visit_id: str) -> bool:
    """Whether this visit's transcript has not reached Servers yet (stream or sealed file present)."""
    spool_dir = _spool_dir(config_dir)
    return any(visit_path(spool_dir, visit_id, s).exists() for s in (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX))


async def upload_backlog_full(config_dir: Path, *, is_live: Callable[[str], bool] | None = None) -> bool:
    """True once unreclaimable visit files reach ``VISIT_UPLOAD_PENDING_CAP_BYTES`` (rooms / join refuse).

    Counts pending uploads and unsettled spools alike (``VisitSpool.unreclaimable_bytes``):
    with uploads succeeding but memory_server down for long, only the spools pile up.
    """
    total = await VisitSpool.unreclaimable_bytes(config_dir, is_live=is_live)
    return total >= VISIT_UPLOAD_PENDING_CAP_BYTES


def _anomalies_sync(config_dir: Path, visit_id: str) -> tuple[int, str | None, bool] | None:
    """``(count, own_visit_uid of the transcript, counted from the stream)`` of the pending upload.

    None when there is none. A count from the stream may be short (an append
    that failed); the caller prefers the count this process recorded at seal.
    """
    spool_dir = _spool_dir(config_dir)
    try:
        doc = _load_json(visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX))
    except (OSError, ValueError):
        # Sealed file corrupt / temporarily unreadable: if a log remains (not deleted after sealing), count rows from it.
        doc = None
    if not (sealed_upload_doc_usable(doc, visit_id) or sealed_upload_doc_from_another_version(doc, visit_id)):
        # Counts in wrong-visit / malformed files do not belong here: treat as no sealed file and count from log instead.
        # Still use counts from this visit's newer-version files retained after downgrade.
        doc = None
    request = doc.get("request") if isinstance(doc, dict) else None
    if isinstance(request, dict) and isinstance(request.get("anomalies"), int):
        owner = doc.get("own_visit_uid")
        return max(0, request["anomalies"]), owner if isinstance(owner, str) and owner else None, False
    try:
        with open(visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX), "rb") as handle:
            owner = None
            count = 0
            for index, raw in enumerate(handle):
                if index == 0:
                    try:
                        header = json.loads(raw)
                    except (ValueError, RecursionError):
                        header = None
                    value = header.get("own_visit_uid") if isinstance(header, dict) else None
                    owner = value if isinstance(value, str) and value else None
                if b'"kind":"anomaly"' in raw:
                    count += 1
            return count, owner, True
    except FileNotFoundError:
        return None


async def visit_anomalies(config_dir: Path, visit_id: str, owner: str | None = None) -> int:
    """Anomaly count of a visit: its pending upload, else what this process saw sealed / uploaded.

    With ``owner`` (the reporting account's ``own_visit_uid``), a count that
    belongs to another known account's side of the visit (a computer shared
    by several community accounts) is not used.
    """
    try:
        pending = await asyncio.to_thread(_anomalies_sync, config_dir, visit_id)
    except (OSError, ValueError):
        pending = None
    found = pending[:2] if pending is not None else None
    recent = _recent_anomalies.get(visit_id)
    if found is None:
        found = recent
    elif pending[2] and recent is not None and (not recent[1] or not found[1] or recent[1] == found[1]):
        # Only log count available (possibly missing rows): prefer the complete same-side count recorded when sealing in this process.
        # Do not overwrite this log with a recorded count from another account's side on a shared computer.
        found = recent
    if found is None:
        return 0
    count, source = found
    if owner and source and source != owner:
        return 0
    return count


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
