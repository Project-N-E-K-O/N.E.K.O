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
