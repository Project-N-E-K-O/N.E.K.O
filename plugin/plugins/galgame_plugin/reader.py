from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import sanitize_event, sanitize_session_snapshot


@dataclass(slots=True)
class SessionReadResult:
    session: dict[str, Any] | None
    error: str = ""


@dataclass(slots=True)
class TailReadResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    next_offset: int = 0
    file_size: int = 0
    line_buffer: bytes = b""
    checkpoint: str = ""
    reset_detected: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EventStreamBoundary:
    offset: int = 0
    file_size: int = 0
    last_seq: int = 0
    checkpoint: str = ""
    error: str = ""


def expand_bridge_root(raw_path: str) -> Path:
    candidate = (raw_path or "").strip()
    if not candidate:
        raise ValueError("bridge_root must be non-empty")
    if "://" in candidate:
        raise ValueError("bridge_root must be a local path")
    if candidate.startswith(("\\\\", "//")):
        raise ValueError("bridge_root must be a local path")
    expanded = os.path.expanduser(candidate)
    expanded = re.sub(
        r"%([^%]+)%",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        expanded,
    )
    expanded = os.path.expandvars(expanded)
    path = Path(expanded)
    if not path.is_absolute():
        raise ValueError("bridge_root must be an absolute local path")
    return path


def normalize_text(value: str) -> str:
    text = value
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    for char in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(char, "")
    kept: list[str] = []
    for ch in text:
        codepoint = ord(ch)
        if ch == "\n":
            kept.append(ch)
            continue
        if 0 <= codepoint <= 0x1F:
            continue
        kept.append(ch)
    return "".join(kept)


def read_session_json(session_path: Path) -> SessionReadResult:
    if not session_path.exists():
        return SessionReadResult(session=None)
    try:
        raw_bytes = session_path.read_bytes()
    except OSError as exc:
        return SessionReadResult(session=None, error=f"read session.json failed: {exc}")
    if not raw_bytes:
        return SessionReadResult(session=None, error="session.json is empty")
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return SessionReadResult(session=None, error=f"parse session.json failed: {exc}")
    if not isinstance(payload, dict):
        return SessionReadResult(session=None, error="session.json must be an object")
    return SessionReadResult(session=sanitize_session_snapshot(payload))


def snapshot_events_boundary(
    events_path: Path,
    *,
    session_id: str = "",
    last_seq: int | None = None,
    bytes_limit: int | None = None,
    events_limit: int | None = None,
    snapshot_file_size: int | None = None,
) -> EventStreamBoundary:
    if not events_path.exists():
        return EventStreamBoundary()

    try:
        with events_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            if file_size <= 0:
                return EventStreamBoundary()

            def _boundary_at(
                offset: int,
                *,
                boundary_last_seq: int = 0,
            ) -> EventStreamBoundary:
                normalized_offset = max(0, min(file_size, int(offset)))
                checkpoint = ""
                if normalized_offset > 0:
                    checkpoint_start = max(0, normalized_offset - 256)
                    handle.seek(checkpoint_start)
                    sample = handle.read(normalized_offset - checkpoint_start)
                    if len(sample) == normalized_offset - checkpoint_start:
                        checkpoint = hashlib.sha256(sample).hexdigest()
                return EventStreamBoundary(
                    offset=normalized_offset,
                    file_size=file_size,
                    last_seq=max(0, int(boundary_last_seq or 0)),
                    checkpoint=checkpoint,
                )

            def _checkpoint_seq_at_boundary(offset: int) -> int:
                normalized_offset = max(0, min(file_size, int(offset)))
                if normalized_offset <= 0:
                    return 0
                handle.seek(normalized_offset - 1)
                if handle.read(1) != b"\n":
                    return 0
                record_end = normalized_offset - 1
                cursor = record_end
                record_start = 0
                while cursor > 0:
                    chunk_size = min(cursor, 64 * 1024)
                    cursor -= chunk_size
                    handle.seek(cursor)
                    chunk = handle.read(chunk_size)
                    newline_index = chunk.rfind(b"\n")
                    if newline_index >= 0:
                        record_start = cursor + newline_index + 1
                        break
                handle.seek(record_start)
                raw_line = handle.read(record_end - record_start)
                event, _error = _parse_jsonl_line(raw_line)
                if event is None or str(event.get("session_id") or "") != session_id:
                    return 0
                try:
                    seq = int(event.get("seq") or 0)
                except (TypeError, ValueError):
                    return 0
                return seq if 0 < seq <= checkpoint_seq else 0

            snapshot_size = file_size
            if snapshot_file_size is not None:
                snapshot_size = max(0, min(file_size, int(snapshot_file_size)))

            checkpoint_seq = max(0, int(last_seq or 0))
            if session_id and checkpoint_seq > 0:
                scan_size = snapshot_size
                if bytes_limit is not None:
                    scan_size = min(snapshot_size, max(1, int(bytes_limit)))
                scan_start = snapshot_size - scan_size
                starts_on_record_boundary = False
                if scan_start > 0:
                    handle.seek(scan_start - 1)
                    starts_on_record_boundary = handle.read(1) == b"\n"
                handle.seek(scan_start)
                data = handle.read(scan_size)
                data_start = scan_start
                if scan_start > 0 and not starts_on_record_boundary:
                    newline_index = data.find(b"\n")
                    if newline_index < 0:
                        fallback_offset = _complete_line_boundary_at_or_before(
                            handle,
                            scan_start,
                        )
                        return _boundary_at(
                            fallback_offset,
                            boundary_last_seq=_checkpoint_seq_at_boundary(
                                fallback_offset
                            ),
                        )
                    data_start += newline_index + 1
                    data = data[newline_index + 1 :]

                complete_lines: list[tuple[int, int, bytes]] = []
                line_start = 0
                while True:
                    newline_index = data.find(b"\n", line_start)
                    if newline_index < 0:
                        break
                    complete_lines.append(
                        (
                            data_start + line_start,
                            data_start + newline_index + 1,
                            data[line_start:newline_index],
                        )
                    )
                    line_start = newline_index + 1
                if events_limit is not None:
                    complete_lines = complete_lines[-max(1, int(events_limit)) :]

                # Keep an in-progress record behind the tail cursor.  This is
                # also required when the scan starts at byte zero: advancing
                # to EOF would make the next tail read begin in the record's
                # suffix after the writer appends its terminating newline.
                fallback_offset = complete_lines[0][0] if complete_lines else data_start
                matched_offset = 0
                matched_seq = 0
                for _line_offset, line_end, raw_line in complete_lines:
                    event, _error = _parse_jsonl_line(raw_line)
                    if event is None or str(event.get("session_id") or "") != session_id:
                        continue
                    try:
                        seq = int(event.get("seq") or 0)
                    except (TypeError, ValueError):
                        seq = 0
                    if 0 < seq <= checkpoint_seq:
                        matched_offset = line_end
                        matched_seq = max(matched_seq, seq)
                return _boundary_at(
                    matched_offset or fallback_offset,
                    boundary_last_seq=matched_seq if matched_offset else 0,
                )

            cursor = snapshot_size
            while cursor > 0:
                chunk_size = min(cursor, 64 * 1024)
                cursor -= chunk_size
                handle.seek(cursor)
                chunk = handle.read(chunk_size)
                newline_index = chunk.rfind(b"\n")
                if newline_index >= 0:
                    return _boundary_at(cursor + newline_index + 1)
            return _boundary_at(0)
    except OSError as exc:
        return EventStreamBoundary(error=f"read events.jsonl boundary failed: {exc}")


def _complete_line_boundary_at_or_before(handle: Any, offset: int) -> int:
    cursor = max(0, int(offset))
    while cursor > 0:
        chunk_size = min(cursor, 64 * 1024)
        cursor -= chunk_size
        handle.seek(cursor)
        chunk = handle.read(chunk_size)
        newline_index = chunk.rfind(b"\n")
        if newline_index >= 0:
            return cursor + newline_index + 1
    return 0


def read_stream_checkpoint(
    events_path: Path,
    *,
    offset: int,
    bytes_limit: int = 256,
) -> str:
    normalized_offset = max(0, int(offset))
    if normalized_offset <= 0 or not events_path.exists():
        return ""
    try:
        with events_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            if normalized_offset > handle.tell():
                return ""
            start = max(0, normalized_offset - max(1, int(bytes_limit)))
            handle.seek(start)
            sample = handle.read(normalized_offset - start)
    except OSError:
        return ""
    if len(sample) != normalized_offset - start:
        return ""
    return hashlib.sha256(sample).hexdigest()


def _parse_jsonl_line(raw_line: bytes) -> tuple[dict[str, Any] | None, str]:
    if raw_line.endswith(b"\r"):
        raw_line = raw_line[:-1]
    if not raw_line:
        return None, ""
    try:
        payload = json.loads(raw_line.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"parse events.jsonl line failed: {exc}"
    event = sanitize_event(payload)
    if event is None:
        return None, "events.jsonl line must be an object"
    return event, ""


def tail_events_jsonl(
    events_path: Path,
    *,
    offset: int,
    line_buffer: bytes,
    expected_checkpoint: str = "",
) -> TailReadResult:
    result = TailReadResult(next_offset=max(0, offset))
    if not events_path.exists():
        result.file_size = 0
        result.reset_detected = offset > 0
        return result

    try:
        file_size = events_path.stat().st_size
    except OSError as exc:
        result.errors.append(f"stat events.jsonl failed: {exc}")
        return result

    result.file_size = file_size
    if file_size == 0:
        result.reset_detected = True
        result.line_buffer = b""
        return result
    if file_size < offset:
        result.reset_detected = True
        result.line_buffer = b""
        return result

    try:
        with events_path.open("rb") as handle:
            if offset > 0 and expected_checkpoint:
                start = max(0, offset - 256)
                handle.seek(start)
                sample = handle.read(offset - start)
                if (
                    len(sample) != offset - start
                    or hashlib.sha256(sample).hexdigest() != expected_checkpoint
                ):
                    result.reset_detected = True
                    result.line_buffer = b""
                    return result
            handle.seek(offset)
            chunk = handle.read()
            result.next_offset = handle.tell()
            checkpoint_start = max(0, result.next_offset - 256)
            handle.seek(checkpoint_start)
            checkpoint_sample = handle.read(result.next_offset - checkpoint_start)
            if len(checkpoint_sample) == result.next_offset - checkpoint_start:
                result.checkpoint = hashlib.sha256(checkpoint_sample).hexdigest()
    except OSError as exc:
        result.errors.append(f"read events.jsonl failed: {exc}")
        return result

    payload = line_buffer + chunk
    if not payload:
        return result

    lines = payload.split(b"\n")
    if payload.endswith(b"\n"):
        complete_lines = lines[:-1]
        result.line_buffer = b""
    else:
        complete_lines = lines[:-1]
        result.line_buffer = lines[-1]

    for raw_line in complete_lines:
        event, error = _parse_jsonl_line(raw_line)
        if error:
            result.errors.append(error)
            continue
        if event is not None:
            result.events.append(event)
    return result


def warmup_replay_events(
    events_path: Path,
    *,
    bytes_limit: int,
    events_limit: int,
    end_offset: int | None = None,
) -> list[dict[str, Any]]:
    if bytes_limit <= 0 or events_limit <= 0 or not events_path.exists():
        return []

    try:
        file_size = events_path.stat().st_size
    except OSError:
        return []

    effective_end = file_size if end_offset is None else max(0, min(file_size, end_offset))
    start = max(0, effective_end - bytes_limit)
    try:
        with events_path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(effective_end - start)
    except OSError:
        return []

    if not chunk:
        return []

    if start > 0:
        newline_index = chunk.find(b"\n")
        if newline_index < 0:
            return []
        chunk = chunk[newline_index + 1 :]

    lines = chunk.split(b"\n")
    if chunk and not chunk.endswith(b"\n"):
        lines = lines[:-1]

    events: list[dict[str, Any]] = []
    for raw_line in lines:
        event, _ = _parse_jsonl_line(raw_line)
        if event is not None:
            events.append(event)
    if len(events) > events_limit:
        return events[-events_limit:]
    return events
