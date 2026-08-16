from __future__ import annotations

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
    reset_detected: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EventStreamBoundary:
    offset: int = 0
    file_size: int = 0
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
) -> EventStreamBoundary:
    if not events_path.exists():
        return EventStreamBoundary()

    try:
        with events_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            if file_size <= 0:
                return EventStreamBoundary()

            checkpoint_seq = max(0, int(last_seq or 0))
            if session_id and checkpoint_seq > 0:
                matched_offset = 0
                cursor = 0
                buffer = b""
                buffer_start = 0
                while cursor < file_size:
                    handle.seek(cursor)
                    chunk = handle.read(min(64 * 1024, file_size - cursor))
                    if not chunk:
                        break
                    cursor += len(chunk)
                    data = buffer + chunk
                    data_start = buffer_start
                    line_start = 0
                    while True:
                        newline_index = data.find(b"\n", line_start)
                        if newline_index < 0:
                            break
                        event, _error = _parse_jsonl_line(data[line_start:newline_index])
                        if event is not None and str(event.get("session_id") or "") == session_id:
                            try:
                                seq = int(event.get("seq") or 0)
                            except (TypeError, ValueError):
                                seq = 0
                            if 0 < seq <= checkpoint_seq:
                                matched_offset = data_start + newline_index + 1
                        line_start = newline_index + 1
                    buffer = data[line_start:]
                    buffer_start = data_start + line_start
                return EventStreamBoundary(
                    offset=matched_offset,
                    file_size=file_size,
                )

            cursor = file_size
            while cursor > 0:
                chunk_size = min(cursor, 64 * 1024)
                cursor -= chunk_size
                handle.seek(cursor)
                chunk = handle.read(chunk_size)
                newline_index = chunk.rfind(b"\n")
                if newline_index >= 0:
                    return EventStreamBoundary(
                        offset=cursor + newline_index + 1,
                        file_size=file_size,
                    )
            return EventStreamBoundary(offset=0, file_size=file_size)
    except OSError as exc:
        return EventStreamBoundary(error=f"read events.jsonl boundary failed: {exc}")


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
        result.next_offset = offset
        result.line_buffer = line_buffer
        return result

    try:
        with events_path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
            result.next_offset = handle.tell()
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
