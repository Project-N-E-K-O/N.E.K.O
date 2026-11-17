# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/session/jsonl.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""JSONL serialization and Tau-v1 persisted-session migration."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from nekoclaw.runtime.loop.session.entries import SessionEntry

_SESSION_ENTRY_ADAPTER: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntry)


class SessionJsonlError(ValueError):
    """Raised when a session JSONL line cannot be decoded."""


def entry_to_json_line(entry: SessionEntry) -> str:
    """Serialize one entry in Tau's canonical persisted shape."""
    return _SESSION_ENTRY_ADAPTER.dump_json(entry, exclude_none=True).decode() + "\n"


def entry_from_json_line(line: str, *, line_number: int | None = None) -> SessionEntry:
    """Deserialize one entry, migrating persisted Tau-v1 messages first."""
    location = f" on line {line_number}" if line_number is not None else ""
    try:
        payload = json.loads(line)
        migrated = _migrate_session_entry(payload, legacy_label_target_id=None)
        return _SESSION_ENTRY_ADAPTER.validate_python(migrated)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise SessionJsonlError(f"Invalid session entry{location}: {exc}") from exc


def entries_from_json_lines(lines: list[str]) -> list[SessionEntry]:
    """Deserialize non-empty JSONL lines in order.

    Legacy session-level labels had no target. They deterministically become a
    bookmark on the earliest branchable entry, preserving the old value without
    conflating bookmarks with ``SessionInfoEntry.title``. If the transcript has
    no branchable entry, the earliest ordinary entry is used instead.
    """
    decoded: list[tuple[int, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            decoded.append((index, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise SessionJsonlError(f"Invalid session entry on line {index}: {exc}") from exc

    legacy_target_id = _legacy_label_target_id([value for _line, value in decoded])
    entries: list[SessionEntry] = []
    for line_number, value in decoded:
        try:
            migrated = _migrate_session_entry(value, legacy_label_target_id=legacy_target_id)
            entries.append(_SESSION_ENTRY_ADAPTER.validate_python(migrated))
        except (ValidationError, TypeError, ValueError) as exc:
            raise SessionJsonlError(f"Invalid session entry on line {line_number}: {exc}") from exc
    return entries


def _migrate_session_entry(value: Any, *, legacy_label_target_id: str | None) -> Any:
    """Return a canonical copy of one decoded persisted entry.

    The extension API may break in lockstep, but user session history must not.
    Migration is intentionally confined to this persistence boundary so runtime
    models and extension-facing constructors retain one strict protocol.
    """
    if not isinstance(value, dict):
        return value
    if value.get("type") == "label" and "target_id" not in value:
        migrated = dict(value)
        migrated["target_id"] = legacy_label_target_id or value.get("parent_id") or value.get("id")
        return migrated
    if value.get("type") == "custom_message":
        migrated = dict(value)
        if "customType" in migrated:
            migrated.setdefault("custom_type", migrated["customType"])
            migrated.pop("customType")
        return migrated
    if value.get("type") != "message":
        return value

    migrated = dict(value)
    message = _migrate_message(value.get("message"))
    if isinstance(message, dict) and message.get("role") == "custom":
        message_timestamp = message.get("timestamp")
        if isinstance(message_timestamp, int | float) and not isinstance(message_timestamp, bool):
            migrated["timestamp"] = message_timestamp / 1000
        migrated["type"] = "custom_message"
        migrated["custom_type"] = message.get("customType", message.get("custom_type"))
        migrated["content"] = message.get("content")
        migrated["display"] = message.get("display", True)
        migrated["details"] = message.get("details")
        migrated.pop("message", None)
        return migrated

    migrated["message"] = message
    return migrated


def _legacy_label_target_id(values: list[Any]) -> str | None:
    """Choose one stable target for target-less pre-bookmark label records."""
    branchable_types = {"message", "compaction", "branch_summary"}
    for value in values:
        if isinstance(value, dict) and value.get("type") in branchable_types:
            entry_id = value.get("id")
            if isinstance(entry_id, str):
                return entry_id
    for value in values:
        if isinstance(value, dict) and value.get("type") not in {"label", "leaf"}:
            entry_id = value.get("id")
            if isinstance(entry_id, str):
                return entry_id
    return None


def _migrate_message(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    message = dict(value)
    role = message.get("role")

    if role == "user" and ("custom_type" in message or "customType" in message):
        message["role"] = "custom"
        message["customType"] = message.pop("custom_type", message.get("customType"))
        message.pop("custom_type", None)
        message.setdefault("display", True)
        return message

    if role == "assistant":
        usage = message.get("usage")
        if isinstance(usage, dict) and usage.get("cost") is None:
            usage = dict(usage)
            usage["cost"] = {}
            message["usage"] = usage

        content = message.get("content", "")
        if isinstance(content, str):
            blocks: list[Any] = []
            if content:
                blocks.append({"type": "text", "text": content})
            blocks.extend(message.pop("tool_calls", message.pop("toolCalls", [])) or [])
            message["content"] = blocks
        elif "tool_calls" in message or "toolCalls" in message:
            blocks = list(content or [])
            blocks.extend(message.pop("tool_calls", message.pop("toolCalls", [])) or [])
            message["content"] = blocks
        return message

    if role == "tool":
        message["role"] = "toolResult"
        message["toolName"] = message.pop("name", message.get("toolName", "unknown"))
        message["toolCallId"] = message.pop("tool_call_id", message.get("toolCallId", ""))
        message["isError"] = not bool(message.pop("ok", True))
        content = message.get("content", "")
        if isinstance(content, str):
            message["content"] = [{"type": "text", "text": content}] if content else []
        data = message.pop("data", None)
        details = message.get("details")
        if isinstance(data, dict) and isinstance(details, dict):
            message["details"] = {**data, **details}
        elif details is None and data is not None:
            message["details"] = data
        error = message.pop("error", None)
        if error and not message["content"]:
            message["content"] = [{"type": "text", "text": str(error)}]
        return message

    return message
