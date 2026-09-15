# -- coding: utf-8 --
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
"""Opt-in structural trace of realtime wire events and arbiter decisions.

Enabled per client by ``NEKO_REALTIME_WIRE_TRACE`` (see
``_shared.realtime_wire_trace_enabled``). Every line is a fixed prefix followed
by compact JSON, so a Main log can be parsed offline.

Records hold structural fields: event types, ids, indexes, counts, tool and
function names, statuses and sizes. Conversation text, transcripts,
instructions, tool arguments and outputs, audio and image payloads, and
credentials are never read. The only free text is error text, each capped at
200 characters: the ``message`` of a provider ``error`` event here, and on the
arbiter side the exception text of a failed dispatch (which can repeat a
provider error message) and the reason a connection was marked lost. Every
recorder entry point swallows its own exceptions, so tracing cannot change what
the caller does.

This module imports nothing from the package on purpose: the arbiter imports
it, and the arbiter must stay importable without the transport stack.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

WIRE_TRACE_PREFIX = "[wire-trace] "
ARBITER_TRACE_PREFIX = "[arbiter-trace] "

# Ids are short in every provider seen so far; the cap only stops an
# unexpected field shape from dragging a long string into the log.
_MAX_SCALAR_CHARS = 160
_ERROR_MESSAGE_CHARS = 200
_FIRST_SEEN_KEY_LIMIT = 512
_DELTA_COUNT_KEY_LIMIT = 128

_AUDIO_APPEND_EVENT_TYPE = "input_audio_buffer.append"
_FUNCTION_ARGS_DELTA_EVENT_TYPE = "response.function_call_arguments.delta"
_ITEM_DELTA_TERMINAL_EVENT_TYPES = frozenset(
    {
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.failed",
    }
)
_RECV_TOP_LEVEL_ID_FIELDS = (
    "event_id",
    "response_id",
    "item_id",
    "previous_item_id",
    "output_index",
    "content_index",
    "call_id",
)
_OUTPUT_ITEM_FIELDS = ("type", "id", "call_id", "name", "status")


def _json_default(value: Any) -> str:
    """Name an unserializable value's type instead of rendering it."""

    return type(value).__name__


def trace_json(payload: dict[str, Any]) -> str:
    """Serialize one trace record compactly."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def trace_now() -> float:
    """Wall-clock timestamp shared by wire and arbiter records."""

    return round(time.time(), 3)


def _scalar(value: Any) -> Any:
    """Reduce an id-like field to a bounded JSON scalar."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_SCALAR_CHARS]
    return type(value).__name__


def _put(record: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        record[key] = _scalar(value)


def _tool_name(tool: Any) -> Any:
    if not isinstance(tool, dict):
        return type(tool).__name__
    function = tool.get("function")
    if isinstance(function, dict) and function.get("name") is not None:
        return _scalar(function.get("name"))
    if tool.get("name") is not None:
        return _scalar(tool.get("name"))
    return _scalar(tool.get("type"))


def _session_fields(record: dict[str, Any], session: Any) -> None:
    if not isinstance(session, dict):
        return
    tools = session.get("tools")
    if isinstance(tools, list):
        record["tools_count"] = len(tools)
        record["tool_names"] = [_tool_name(tool) for tool in tools]
    else:
        record["tools_count"] = None
        record["tool_names"] = None
    turn_detection = session.get("turn_detection")
    record["turn_detection"] = (
        _scalar(turn_detection.get("type"))
        if isinstance(turn_detection, dict)
        else None
    )
    modalities = session.get("modalities")
    if isinstance(modalities, list):
        record["modalities"] = [_scalar(modality) for modality in modalities]


def _item_fields(record: dict[str, Any], item: Any) -> None:
    if not isinstance(item, dict):
        return
    _put(record, "item.id", item.get("id"))
    _put(record, "item.type", item.get("type"))
    _put(record, "item.role", item.get("role"))
    _put(record, "item.call_id", item.get("call_id"))
    _put(record, "item.name", item.get("name"))
    _put(record, "item.status", item.get("status"))


def _content_part_types(content: Any) -> list[Any] | None:
    if not isinstance(content, list):
        return None
    return [
        _scalar(part.get("type")) if isinstance(part, dict) else type(part).__name__
        for part in content
    ]


def _output_item(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {"type": type(entry).__name__}
    record: dict[str, Any] = {}
    for key in _OUTPUT_ITEM_FIELDS:
        _put(record, key, entry.get(key))
    return record


def summarize_send_event(event: Any) -> dict[str, Any]:
    """Structural fields of one outgoing client event."""

    if not isinstance(event, dict):
        return {"type": None, "malformed": type(event).__name__}
    event_type = event.get("type")
    record: dict[str, Any] = {"type": _scalar(event_type)}
    _put(record, "event_id", event.get("event_id"))
    if event_type == "conversation.item.create":
        item = event.get("item")
        _item_fields(record, item)
        if isinstance(item, dict):
            content_types = _content_part_types(item.get("content"))
            if content_types is not None:
                record["content_types"] = content_types
        _put(record, "previous_item_id", event.get("previous_item_id"))
    elif event_type == "response.create":
        response = event.get("response")
        record["response_keys"] = (
            sorted(str(key)[:_MAX_SCALAR_CHARS] for key in response)
            if isinstance(response, dict)
            else None
        )
    elif event_type == "response.cancel":
        _put(record, "response_id", event.get("response_id"))
    elif event_type == "session.update":
        _session_fields(record, event.get("session"))
    elif event_type in ("conversation.item.delete", "conversation.item.truncate"):
        _put(record, "item_id", event.get("item_id"))
    return record


def summarize_recv_event(event: Any) -> dict[str, Any]:
    """Structural fields of one provider event."""

    if not isinstance(event, dict):
        return {"type": None, "malformed": type(event).__name__}
    event_type = event.get("type")
    type_text = event_type if isinstance(event_type, str) else ""
    record: dict[str, Any] = {"type": _scalar(event_type)}
    for key in _RECV_TOP_LEVEL_ID_FIELDS:
        _put(record, key, event.get(key))
    if "function_call" in type_text:
        # Function name only; ``arguments`` is never read.
        _put(record, "name", event.get("name"))
    response = event.get("response")
    if isinstance(response, dict):
        _put(record, "response.id", response.get("id"))
        _put(record, "response.status", response.get("status"))
        details = response.get("status_details")
        if isinstance(details, dict):
            _put(record, "response.status_details.type", details.get("type"))
            _put(record, "response.status_details.reason", details.get("reason"))
            detail_error = details.get("error")
            if isinstance(detail_error, dict):
                _put(
                    record,
                    "response.status_details.error.type",
                    detail_error.get("type"),
                )
                _put(
                    record,
                    "response.status_details.error.code",
                    detail_error.get("code"),
                )
        if type_text == "response.done":
            output = response.get("output")
            if isinstance(output, list):
                record["output"] = [_output_item(entry) for entry in output]
    _item_fields(record, event.get("item"))
    if type_text in ("session.created", "session.updated"):
        _session_fields(record, event.get("session"))
    if type_text == "error":
        error = event.get("error")
        if isinstance(error, dict):
            _put(record, "error.type", error.get("type"))
            _put(record, "error.code", error.get("code"))
            _put(record, "error.event_id", error.get("event_id"))
            message = error.get("message")
            if message is not None:
                record["error.message"] = str(message)[:_ERROR_MESSAGE_CHARS]
        elif error is not None:
            record["error.message"] = str(error)[:_ERROR_MESSAGE_CHARS]
    return record


def _remember_bounded(keys: set[Any], key: Any) -> None:
    if len(keys) >= _FIRST_SEEN_KEY_LIMIT:
        keys.clear()
    keys.add(key)


class RealtimeWireTrace:
    """Per-client recorder for ``[wire-trace]`` lines.

    Owns the rate limiting that keeps a trace readable. Audio appends are
    counted, and the count rides on the next logged send. Streaming deltas log
    their first frame per (type, response) and fold the rest into counts that
    ride on that response's ``response.done`` line as its full total. Any other
    response still pending at that moment rides along in
    ``other_delta_counts`` as a running-total snapshot (only when it grew since
    last shown), so a delta stream whose id never reaches a terminal is still
    visible. Function-call argument deltas log their first frame per call id,
    or per (response id, item id or output index) when the call id is absent.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        # Random per-client tag so lines from concurrent sessions sharing one
        # log can be told apart. It identifies nothing outside the log.
        self._client_tag = uuid.uuid4().hex[:8]
        self._audio_appends = 0
        self._audio_appends_total = 0
        self._first_delta_keys: set[tuple[Any, ...]] = set()
        self._first_call_keys: set[tuple[Any, ...]] = set()
        # (generation, "response" | "item", id) -> {event type: count}
        self._delta_counts: dict[tuple[Any, ...], dict[str, int]] = {}
        # Same keys -> running total last reported in ``other_delta_counts``,
        # so an unchanged pending stream is not repeated on every done line.
        self._other_reported: dict[tuple[Any, ...], int] = {}

    @property
    def client_tag(self) -> str:
        """The per-client tag stamped on every record as ``cid``."""

        return self._client_tag

    def _emit(self, record: dict[str, Any]) -> None:
        self._logger.info("%s%s", WIRE_TRACE_PREFIX, trace_json(record))

    def record_send(
        self,
        event: Any,
        *,
        generation: Any = None,
        size: int | None = None,
    ) -> None:
        """Record one event that the transport has just written."""

        try:
            if (
                isinstance(event, dict)
                and event.get("type") == _AUDIO_APPEND_EVENT_TYPE
            ):
                self._audio_appends += 1
                self._audio_appends_total += 1
                return
            record: dict[str, Any] = {
                "dir": "send",
                "t": trace_now(),
                "cid": self._client_tag,
            }
            if generation is not None:
                record["gen"] = _scalar(generation)
            record.update(summarize_send_event(event))
            if size is not None:
                record["bytes"] = size
            if self._audio_appends:
                record["audio_appends"] = self._audio_appends
                record["audio_appends_total"] = self._audio_appends_total
                self._audio_appends = 0
            self._emit(record)
        except Exception:
            return

    def _count_delta(self, key: tuple[Any, ...], event_type: str) -> None:
        counts = self._delta_counts.get(key)
        if counts is None:
            if len(self._delta_counts) >= _DELTA_COUNT_KEY_LIMIT:
                evicted = next(iter(self._delta_counts))
                del self._delta_counts[evicted]
                self._other_reported.pop(evicted, None)
            counts = self._delta_counts[key] = {}
        counts[event_type] = counts.get(event_type, 0) + 1

    def record_recv(self, event: Any, *, generation: Any = None) -> None:
        """Record one provider event before any dispatch or filtering."""

        try:
            record: dict[str, Any] = {
                "dir": "recv",
                "t": trace_now(),
                "cid": self._client_tag,
            }
            if generation is not None:
                record["gen"] = _scalar(generation)
            if not isinstance(event, dict):
                record.update(summarize_recv_event(event))
                self._emit(record)
                return
            event_type = event.get("type")
            type_text = event_type if isinstance(event_type, str) else ""
            gen_key = _scalar(generation)
            is_delta = type_text.endswith(".delta")
            if is_delta:
                response_id = _scalar(event.get("response_id"))
                if response_id is None and event.get("item_id") is not None:
                    count_key = (gen_key, "item", _scalar(event.get("item_id")))
                else:
                    count_key = (gen_key, "response", response_id)
                self._count_delta(count_key, type_text)
                if type_text == _FUNCTION_ARGS_DELTA_EVENT_TYPE:
                    call_id = _scalar(event.get("call_id"))
                    if call_id is not None:
                        first_key = (gen_key, "call", call_id)
                    else:
                        # Providers that send no call_id (glm) would otherwise
                        # share one key, hiding every call after the first.
                        item_id = _scalar(event.get("item_id"))
                        first_key = (
                            gen_key,
                            "no_call_id",
                            _scalar(event.get("response_id")),
                            item_id
                            if item_id is not None
                            else _scalar(event.get("output_index")),
                        )
                    seen = self._first_call_keys
                else:
                    first_key = (gen_key, type_text) + count_key[1:]
                    seen = self._first_delta_keys
                if first_key in seen:
                    return
                _remember_bounded(seen, first_key)
                record["first_delta"] = True
            record.update(summarize_recv_event(event))
            if type_text == "response.done":
                response = event.get("response")
                done_id = (
                    _scalar(response.get("id")) if isinstance(response, dict) else None
                )
                if done_id is None:
                    done_id = _scalar(event.get("response_id"))
                own_key = (gen_key, "response", done_id)
                own = self._delta_counts.pop(own_key, None)
                self._other_reported.pop(own_key, None)
                if own:
                    record["delta_counts"] = own
                # Other pending streams are reported as a snapshot and keep
                # counting, so their own done still carries their full total.
                # A stream is repeated only when it has grown since last shown.
                others: dict[str, dict[str, int]] = {}
                for key, counts in self._delta_counts.items():
                    if key[0] != gen_key or key[1] != "response":
                        continue
                    total = sum(counts.values())
                    if self._other_reported.get(key) == total:
                        continue
                    self._other_reported[key] = total
                    others[str(key[2])] = dict(counts)
                if others:
                    record["other_delta_counts"] = others
            elif type_text in _ITEM_DELTA_TERMINAL_EVENT_TYPES:
                own = self._delta_counts.pop(
                    (gen_key, "item", _scalar(event.get("item_id"))), None
                )
                if own:
                    record["delta_counts"] = own
            self._emit(record)
        except Exception:
            return
