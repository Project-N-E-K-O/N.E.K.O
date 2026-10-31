"""Bounded current conversation facts for reply and interaction reads."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import asdict
from typing import Any, Callable

from plugin.sdk.shared.contracts.conversations import (
    InputSubmission,
    ReplyOrigin,
    message_from_event,
)
from plugin.server.domain.errors import ServerDomainError

_MAX_ENTRIES = 512
_MAX_PLAYBACKS = 32
_MAX_FACT_BYTES = 64 * 1024
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_RESULT_BYTES = 1024 * 1024
_MAX_ID_LENGTH = 256
_TTL_SECONDS = 600.0


def _id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_ID_LENGTH:
        return None
    if any(ord(c) < 32 for c in value):
        return None
    return value


def _timestamp(value: object) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return None
    return float(value)


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _overflow() -> ServerDomainError:
    return ServerDomainError(
        code="CONVERSATION_DETAIL_TOO_LARGE",
        message="Conversation detail exceeds size limit",
        status_code=413,
    )


class ConversationService:
    def __init__(
        self,
        read_message: Callable[[str], dict | None],
        update_message: Callable[[str, dict[str, Any]], dict | None] | None = None,
    ) -> None:
        self._read_message = read_message
        self._update_message = update_message
        self._lock = threading.RLock()
        self._inputs: dict[str, dict] = {}
        self._replies: dict[str, dict] = {}
        self._messages_by_reply: dict[str, dict] = {}
        self._entered_inputs: dict[str, bool] = {}
        self._early_playbacks: dict[str, list[dict]] = {}
        self._incomplete: dict[str, dict] = {}
        self._turn_aliases: dict[str, str | None] = {}
        # One shared budget and expiry order for every retained value.
        self._retained: OrderedDict[tuple[str, str], tuple[float, int]] = OrderedDict()
        self._retained_bytes = 0
        self._buckets = {
            "input": self._inputs,
            "reply": self._replies,
            "message": self._messages_by_reply,
            "entered": self._entered_inputs,
            "playback": self._early_playbacks,
            "incomplete": self._incomplete,
            "turn_alias": self._turn_aliases,
        }

    def _remove(self, bucket: str, key: str) -> None:
        marker = (bucket, key)
        info = self._retained.pop(marker, None)
        if info is not None:
            self._retained_bytes -= info[1]
        self._buckets[bucket].pop(key, None)

    def _prune(self) -> None:
        now = time.monotonic()
        while self._retained:
            marker, (expires, _) = next(iter(self._retained.items()))
            if expires > now:
                break
            self._remove(*marker)

    def _put(self, bucket: str, key: str, value: object) -> None:
        size = _size(value) + len(key.encode("utf-8"))
        if size > _MAX_FACT_BYTES:
            raise _overflow()
        self._remove(bucket, key)
        while self._retained and (
            len(self._retained) >= _MAX_ENTRIES
            or self._retained_bytes + size > _MAX_CACHE_BYTES
        ):
            self._remove(*next(iter(self._retained)))
        self._buckets[bucket][key] = value
        self._retained[(bucket, key)] = (time.monotonic() + _TTL_SECONDS, size)
        self._retained_bytes += size

    @staticmethod
    def _turn_key(character: str, turn_id: str) -> str:
        # JSON encodes both values without ambiguous separators.
        return json.dumps([character, turn_id], ensure_ascii=False)

    def _remember_turn(self, character: object, turn_id: object, reply_id: str) -> None:
        character = _id(character)
        turn_id = _id(turn_id)
        if character is None or turn_id is None:
            return
        key = self._turn_key(character, turn_id)
        if key in self._turn_aliases and self._turn_aliases[key] != reply_id:
            self._put("turn_alias", key, None)
        else:
            self._put("turn_alias", key, reply_id)

    def _mark_incomplete(self, reply_id: str, *, reason: str = "other") -> None:
        reply = self._replies.get(reply_id)
        input_id = reply.get("input_id") if reply else None
        if reason == "text" and reply and reply["playbacks"]:
            self._put("playback", reply_id, reply["playbacks"])
        self._remove("reply", reply_id)
        if reason != "text":
            self._remove("playback", reply_id)
        self._put("incomplete", reply_id, {"input_id": input_id, "reason": reason})

    def close(self) -> None:
        with self._lock:
            for bucket in self._buckets.values():
                bucket.clear()
            self._retained.clear()
            self._retained_bytes = 0

    def accept_fact(self, fact: str, payload: dict) -> tuple[str, dict] | None:
        if not isinstance(fact, str) or not isinstance(payload, dict):
            return None
        try:
            if _size(payload) > _MAX_FACT_BYTES:
                reply_id = _id(payload.get("reply_id"))
                if reply_id and (
                    fact.startswith("reply.") or fact.startswith("playback.")
                ):
                    with self._lock:
                        self._prune()
                        without_text = {k: v for k, v in payload.items() if k != "text"}
                        reason = (
                            "text"
                            if fact.startswith("reply.")
                            and isinstance(payload.get("text"), str)
                            and _size(without_text) <= _MAX_FACT_BYTES
                            else "other"
                        )
                        self._mark_incomplete(reply_id, reason=reason)
                raise _overflow()
        except (TypeError, ValueError, UnicodeError):
            return None
        with self._lock:
            self._prune()
        if fact == "input.submitted":
            return self._accept_input(payload)
        if fact in ("reply.started", "reply.closed", "reply.updated"):
            return self._accept_reply(fact, payload)
        if fact in ("playback.started", "playback.stopped"):
            return self._accept_playback(fact, payload)
        return None

    def _accept_input(self, payload: dict) -> tuple[str, dict] | None:
        value = {
            "id": payload.get("input_id"),
            "character": payload.get("character"),
            "text": payload.get("text"),
            "input_source": payload.get("input_source"),
            "observed_at": payload.get("observed_at"),
        }
        try:
            submission = InputSubmission.from_payload(value)
        except (TypeError, ValueError):
            return None
        if _id(submission.id) is None or (
            submission.character is not None and _id(submission.character) is None
        ):
            return None
        with self._lock:
            self._prune()
            previous = self._inputs.get(submission.id)
            if previous is not None and any(previous[k] != value[k] for k in value):
                return None
            outcome = (
                "entered_conversation"
                if submission.id in self._entered_inputs
                or (previous and previous["observed_outcome"] == "entered_conversation")
                else "unknown"
            )
            self._put("input", submission.id, {**value, "observed_outcome": outcome})
        return "input.submitted", {"submission": value}

    def _accept_reply(self, fact: str, payload: dict) -> tuple[str, dict] | None:
        reply_id = _id(payload.get("reply_id"))
        if reply_id is None:
            return None
        character, input_id = payload.get("character"), payload.get("input_id")
        if (character is not None and _id(character) is None) or (
            input_id is not None and _id(input_id) is None
        ):
            return None
        text, audio, reason = (
            payload.get("text"),
            payload.get("audio_expected"),
            payload.get("close_reason"),
        )
        if text is not None and not isinstance(text, str):
            return None
        if audio is not None and not isinstance(audio, bool):
            return None
        if reason is not None and reason not in (
            "completed",
            "interrupted",
            "discarded",
            "failed",
            "unknown",
        ):
            return None
        try:
            origin = (
                asdict(ReplyOrigin.from_payload(payload["origin"]))
                if payload.get("origin") is not None
                else None
            )
        except (TypeError, ValueError):
            return None
        # Phase timestamps describe the producer boundary, not how long the
        # observation spent in the bridge. Missing evidence stays unknown.
        occurred = _timestamp(payload.get("occurred_at"))
        fields = {}
        if text is not None:
            fields["text"] = text
        if origin is not None:
            fields["origin"] = origin
        with self._lock:
            self._prune()
            if reply_id in self._incomplete:
                raise _overflow()
            previous = self._replies.get(reply_id)
            if previous is None:
                reply = self._new_reply(reply_id, character, input_id)
                reply["origin"] = origin
                reply["audio_expected"] = audio
                known = self._messages_by_reply.get(reply_id)
                if (
                    known
                    and (
                        character is None
                        or known["character"] is None
                        or character == known["character"]
                    )
                    and (
                        input_id is None
                        or known["input_id"] is None
                        or input_id == known["input_id"]
                    )
                ):
                    reply["_message_id"] = known["id"]
                    reply["character"] = reply["character"] or known["character"]
                    reply["input_id"] = reply["input_id"] or known["input_id"]
                reply["playbacks"] = [
                    copy.deepcopy(item)
                    for item in self._early_playbacks.get(reply_id, [])
                    if not item.get("character")
                    or not reply["character"]
                    or item["character"] == reply["character"]
                ]
            else:
                reply = copy.deepcopy(previous)
                if (
                    character and reply["character"] and character != reply["character"]
                ) or (input_id and reply["input_id"] and input_id != reply["input_id"]):
                    return None
                if (
                    fact in ("reply.started", "reply.closed")
                    and reply["text_state"] == "closed"
                ):
                    return None
            reply["character"] = reply["character"] or character
            reply["input_id"] = reply["input_id"] or input_id
            if origin is not None:
                reply["origin"] = origin
            if audio is not None:
                reply["audio_expected"] = audio
            if text is not None and reply["_message_id"] is None:
                reply["text"] = text
            if fact == "reply.started":
                reply["text_state"] = "started"
                if reply["started_at"] is None:
                    reply["started_at"] = occurred
            elif fact == "reply.closed":
                reply["text_state"] = "closed"
                if reply["closed_at"] is None:
                    reply["closed_at"] = occurred
                reply["close_reason"] = reason or "unknown"
            message_id = reply["_message_id"]
            try:
                self._put("reply", reply_id, reply)
            except ServerDomainError:
                without_text = {**reply, "text": None}
                reason = (
                    "text"
                    if reply["_message_id"] is None
                    and reply["text"] is not None
                    and _size(without_text) <= _MAX_FACT_BYTES
                    else "other"
                )
                self._mark_incomplete(reply_id, reason=reason)
                raise
            self._remove("playback", reply_id)
            # Only these producer facts carry a verified presentation turn.
            if fact in ("reply.started", "reply.closed"):
                self._remember_turn(character, payload.get("turn_id"), reply_id)
        if fact == "reply.updated" and message_id and fields:
            if self._update_message is None:
                raise ServerDomainError(
                    code="CAPABILITY_UNAVAILABLE",
                    message="Message correction is unavailable",
                    status_code=503,
                )
            if self._update_message(message_id, fields) is None:
                raise ServerDomainError(
                    code="CONVERSATION_MESSAGE_UNAVAILABLE",
                    message="Message is no longer retained",
                    status_code=409,
                )
        return fact, {"reply": self._reply_snapshot(reply)}

    @staticmethod
    def _merge_playback(playbacks: list[dict], event: dict) -> list[dict]:
        values = copy.deepcopy(playbacks)
        pid, presenter = event["presentation_id"], event["presenter_id"]
        index = next(
            (
                i
                for i, item in enumerate(values)
                if pid
                and item["presentation_id"] == pid
                and item["presenter_id"] == presenter
            ),
            None,
        )
        if index is None:
            if len(values) >= _MAX_PLAYBACKS:
                raise _overflow()
            values.append(event.copy())
        elif not (values[index]["state"] == "stopped" and event["state"] == "playing"):
            values[index] = event.copy()
        return values

    def _accept_playback(self, fact: str, payload: dict) -> tuple[str, dict] | None:
        state = "playing" if fact == "playback.started" else "stopped"
        if payload.get("state") not in (None, state):
            return None
        keys = ("reply_id", "turn_id", "presentation_id", "presenter_id", "character")
        for key in keys:
            if payload.get(key) is not None and _id(payload[key]) is None:
                return None
        reason = payload.get("stop_reason")
        if reason is not None and reason not in (
            "completed",
            "interrupted",
            "canceled",
            "error",
            "fallback",
            "unknown",
        ):
            return None
        event = {key: payload.get(key) for key in keys if key != "character"}
        event.update(
            state=state,
            stop_reason=(reason or "unknown") if state == "stopped" else None,
        )
        if payload.get("character") is not None:
            event["character"] = payload["character"]
        with self._lock:
            self._prune()
            rid = event["reply_id"]
            character = _id(payload.get("character"))
            turn_id = _id(payload.get("turn_id"))
            if rid is None and character is not None and turn_id is not None:
                rid = self._turn_aliases.get(self._turn_key(character, turn_id))
                event["reply_id"] = rid
            if rid:
                if rid in self._incomplete:
                    raise _overflow()
                reply = self._replies.get(rid)
                if (
                    reply
                    and character
                    and reply.get("character")
                    and character != reply["character"]
                ):
                    return None
                try:
                    if reply is None:
                        values = self._merge_playback(
                            self._early_playbacks.get(rid, []), event
                        )
                        self._put("playback", rid, values)
                    else:
                        reply = copy.deepcopy(reply)
                        reply["playbacks"] = self._merge_playback(
                            reply["playbacks"], event
                        )
                        self._put("reply", rid, reply)
                except ServerDomainError:
                    self._mark_incomplete(rid)
                    raise
        return fact, event

    def accept_message(self, raw_store_event: dict) -> None:
        try:
            msg = message_from_event(raw_store_event)
        except (TypeError, ValueError, KeyError):
            return
        input_id, rid, mid = _id(msg["input_id"]), _id(msg["reply_id"]), _id(msg["id"])
        if mid is None:
            return
        with self._lock:
            self._prune()
            if msg["role"] == "user" and input_id:
                self._put("entered", input_id, True)
                if input_id in self._inputs:
                    submission = copy.deepcopy(self._inputs[input_id])
                    submission["observed_outcome"] = "entered_conversation"
                    self._put("input", input_id, submission)
            if not rid:
                return
            current = self._replies.get(rid)
            if current and (
                (
                    current["character"]
                    and msg["character"]
                    and current["character"] != msg["character"]
                )
                or (
                    current["input_id"] and input_id and current["input_id"] != input_id
                )
            ):
                return
            known = {"id": mid, "character": msg["character"], "input_id": input_id}
            self._put("message", rid, known)
            incomplete = self._incomplete.get(rid)
            if incomplete and incomplete["reason"] == "text":
                self._remove("incomplete", rid)
            if current is None:
                return
            reply = copy.deepcopy(current)
            reply["_message_id"] = mid
            reply["character"] = reply["character"] or msg["character"]
            reply["input_id"] = reply["input_id"] or input_id
            reply["text"] = None
            if msg["origin"] is not None:
                reply["origin"] = copy.deepcopy(msg["origin"])
            self._put("reply", rid, reply)

    def _reply_snapshot(self, reply: dict) -> dict:
        result = copy.deepcopy(reply)
        message_id = result.pop("_message_id")
        if message_id:
            message = self._read_message(message_id)
            result["text"] = message.get("text") if isinstance(message, dict) else None
            if isinstance(message, dict):
                result["origin"] = copy.deepcopy(message.get("origin"))
        if _size(result) > _MAX_RESULT_BYTES:
            raise _overflow()
        return result

    @staticmethod
    def _new_reply(reply_id: str, character: str | None, input_id: str | None) -> dict:
        return {
            "id": reply_id,
            "character": character,
            "input_id": input_id,
            "origin": None,
            "text": None,
            "text_state": "unknown",
            "close_reason": None,
            "audio_expected": None,
            "started_at": None,
            "closed_at": None,
            "playbacks": [],
            "_message_id": None,
        }

    def _formal_reply_snapshot(
        self, reply_id: str, known: dict, playbacks: list[dict]
    ) -> dict | None:
        message = self._read_message(known["id"])
        if not isinstance(message, dict) or message.get("reply_id") != reply_id:
            return None
        reply = self._new_reply(
            reply_id, message.get("character"), message.get("input_id")
        )
        reply.pop("_message_id")
        reply["text"] = message.get("text")
        reply["origin"] = copy.deepcopy(message.get("origin"))
        reply["playbacks"] = [
            item
            for item in playbacks
            if not item.get("character")
            or not reply["character"]
            or item["character"] == reply["character"]
        ]
        if _size(reply) > _MAX_RESULT_BYTES:
            raise _overflow()
        return reply

    def get_reply(self, reply_id: str) -> dict | None:
        if _id(reply_id) is None:
            raise ValueError("reply_id must be nonempty and bounded")
        with self._lock:
            self._prune()
            if reply_id in self._incomplete:
                raise _overflow()
            reply = copy.deepcopy(self._replies.get(reply_id))
            known = copy.deepcopy(self._messages_by_reply.get(reply_id))
            playbacks = copy.deepcopy(self._early_playbacks.get(reply_id, []))
        if reply is not None:
            return self._reply_snapshot(reply)
        if known is not None:
            return self._formal_reply_snapshot(reply_id, known, playbacks)
        return None

    def get_interaction(self, input_id: str) -> dict | None:
        if _id(input_id) is None:
            raise ValueError("input_id must be nonempty and bounded")
        with self._lock:
            self._prune()
            if any(item["input_id"] == input_id for item in self._incomplete.values()):
                raise _overflow()
            submission = copy.deepcopy(self._inputs.get(input_id))
            replies = [
                copy.deepcopy(reply)
                for reply in self._replies.values()
                if reply["input_id"] == input_id
            ]
            reply_ids = {reply["id"] for reply in replies}
            formal = [
                (
                    rid,
                    copy.deepcopy(known),
                    copy.deepcopy(self._early_playbacks.get(rid, [])),
                )
                for rid, known in self._messages_by_reply.items()
                if known["input_id"] == input_id
                and rid not in reply_ids
                and rid not in self._incomplete
            ]
        if submission is None:
            return None
        value = {"input": submission, "replies": []}
        size = _size(submission)

        def snapshots():
            for reply in replies:
                yield self._reply_snapshot(reply)
            for rid, known, playbacks in formal:
                item = self._formal_reply_snapshot(rid, known, playbacks)
                if item is not None and item["input_id"] == input_id:
                    yield item

        for item in snapshots():
            size += _size(item) + 2
            if size > _MAX_RESULT_BYTES:
                raise _overflow()
            value["replies"].append(item)
        return value
