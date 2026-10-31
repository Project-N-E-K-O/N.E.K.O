"""Conversation message values and their wire projection."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _string(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _time(value: object, name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite timestamp")
    return float(value)


@dataclass(frozen=True, slots=True)
class ReplyOrigin:
    kind: str
    is_proactive: bool | None = None
    plugin_id: str | None = None
    task_id: str | None = None

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> ReplyOrigin:
        if not isinstance(value, Mapping) or not {
            "kind",
            "is_proactive",
            "plugin_id",
            "task_id",
        }.issubset(value):
            raise ValueError("invalid origin payload")
        proactive = value["is_proactive"]
        if proactive is not None and not isinstance(proactive, bool):
            raise ValueError("is_proactive must be a boolean or None")
        return cls(
            kind=_string(value["kind"], "origin.kind"),
            is_proactive=proactive,
            plugin_id=_string(value["plugin_id"], "origin.plugin_id", optional=True),
            task_id=_string(value["task_id"], "origin.task_id", optional=True),
        )


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: str
    character: str | None
    kind: str
    role: str
    text: str | None
    input_id: str | None
    reply_id: str | None
    input_source: str
    origin: ReplyOrigin | None
    observed_at: float
    occurred_at: float | None

    def dump(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> ConversationMessage:
        fields = {
            "id",
            "character",
            "kind",
            "role",
            "text",
            "input_id",
            "reply_id",
            "input_source",
            "origin",
            "observed_at",
            "occurred_at",
        }
        if not isinstance(value, Mapping) or not fields.issubset(value):
            raise ValueError("invalid conversation message payload")
        text = value["text"]
        if text is not None and not isinstance(text, str):
            raise ValueError("text must be a string or None")
        origin = value["origin"]
        return cls(
            id=_string(value["id"], "id"),
            character=_string(value["character"], "character", optional=True),
            kind=_string(value["kind"], "kind"),
            role=_string(value["role"], "role"),
            text=text,
            input_id=_string(value["input_id"], "input_id", optional=True),
            reply_id=_string(value["reply_id"], "reply_id", optional=True),
            input_source=_string(value["input_source"], "input_source"),
            origin=ReplyOrigin.from_payload(origin) if origin is not None else None,
            observed_at=_time(value["observed_at"], "observed_at"),
            occurred_at=_time(value["occurred_at"], "occurred_at", optional=True),
        )

    from_raw = from_payload

    def key(self) -> str:
        return self.id


def message_from_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise ValueError("invalid conversation event")
    payload = event.get("payload")
    index = event.get("index")
    if not isinstance(payload, Mapping) or not isinstance(index, Mapping):
        raise ValueError("invalid conversation event payload or index")
    meta = payload.get("metadata")
    if not isinstance(meta, Mapping):
        raise ValueError("invalid conversation metadata")
    kind = _string(meta.get("turn_type"), "kind")
    role = {
        "user_message": "user",
        "assistant_message": "assistant",
        "proactive_reply": "assistant",
        "proactive_instruction": "instruction",
    }.get(kind, "unknown")
    voice = meta.get("is_voice")
    if voice is not None and not isinstance(voice, bool):
        raise ValueError("is_voice must be a boolean or None")
    source = "unknown"
    if role == "user" and voice is not None:
        source = "voice" if voice else "text"
    origin = payload.get("origin")
    if origin is not None:
        if not isinstance(origin, Mapping):
            raise ValueError("invalid origin")
        origin = {
            key: origin.get(key)
            for key in ("kind", "is_proactive", "plugin_id", "task_id")
        }
    data = {
        "id": index.get("id"),
        "character": meta.get("lanlan_name"),
        "kind": kind,
        "role": role,
        "text": payload.get("content"),
        "input_id": payload.get("input_id") or meta.get("input_id"),
        "reply_id": payload.get("reply_id"),
        "input_source": source,
        "origin": origin,
        "observed_at": index.get("timestamp"),
        "occurred_at": payload.get("occurred_at"),
    }
    return ConversationMessage.from_payload(data).dump()


@dataclass(frozen=True, slots=True)
class ConversationChange:
    """A snapshot of a recent-message window and its changes."""

    current: tuple[ConversationMessage, ...]
    added: tuple[ConversationMessage, ...]
    updated: tuple[ConversationMessage, ...]
    removed: tuple[ConversationMessage, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class InputSubmission:
    id: str
    character: str | None
    text: str
    input_source: str
    observed_at: float

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> InputSubmission:
        if not isinstance(value, Mapping) or set(value) != {
            "id",
            "character",
            "text",
            "input_source",
            "observed_at",
        }:
            raise ValueError("invalid input submission")
        text = value["text"]
        if not isinstance(text, str) or not text:
            raise ValueError("invalid submission text")
        return cls(
            _string(value["id"], "id"),
            _string(value["character"], "character", optional=True),
            text,
            _string(value["input_source"], "input_source"),
            _time(value["observed_at"], "observed_at"),
        )


@dataclass(frozen=True, slots=True)
class InputSubmittedEvent:
    submission: InputSubmission

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> InputSubmittedEvent:
        if not isinstance(value, Mapping) or set(value) != {"submission"}:
            raise ValueError("invalid submission event")
        return cls(InputSubmission.from_payload(value["submission"]))


@dataclass(frozen=True, slots=True)
class PlaybackDetail:
    reply_id: str | None
    turn_id: str | None
    presentation_id: str | None
    presenter_id: str | None
    state: str
    stop_reason: str | None

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> PlaybackDetail:
        if not isinstance(value, Mapping):
            raise ValueError("invalid playback")
        return cls(
            *(
                _string(value.get(k), k, optional=True)
                for k in ("reply_id", "turn_id", "presentation_id", "presenter_id")
            ),
            _string(value.get("state"), "state"),
            _string(value.get("stop_reason"), "stop_reason", optional=True),
        )


@dataclass(frozen=True, slots=True)
class ReplyDetail:
    id: str
    character: str | None
    input_id: str | None
    origin: ReplyOrigin | None
    text: str | None
    text_state: str
    close_reason: str | None
    audio_expected: bool | None
    started_at: float | None
    closed_at: float | None
    playbacks: tuple[PlaybackDetail, ...]

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> ReplyDetail:
        if not isinstance(value, Mapping) or not isinstance(
            value.get("playbacks"), list
        ):
            raise ValueError("invalid reply detail")
        audio = value.get("audio_expected")
        if audio is not None and not isinstance(audio, bool):
            raise ValueError("invalid audio_expected")
        text = value.get("text")
        if text is not None and not isinstance(text, str):
            raise ValueError("invalid reply text")
        origin = value.get("origin")
        return cls(
            _string(value.get("id"), "id"),
            _string(value.get("character"), "character", optional=True),
            _string(value.get("input_id"), "input_id", optional=True),
            ReplyOrigin.from_payload(origin) if origin is not None else None,
            text,
            _string(value.get("text_state"), "text_state"),
            _string(value.get("close_reason"), "close_reason", optional=True),
            audio,
            _time(value.get("started_at"), "started_at", optional=True),
            _time(value.get("closed_at"), "closed_at", optional=True),
            tuple(PlaybackDetail.from_payload(p) for p in value["playbacks"]),
        )


@dataclass(frozen=True, slots=True)
class ReplyEvent:
    reply: ReplyDetail

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> ReplyEvent:
        if not isinstance(value, Mapping) or "reply" not in value:
            raise ValueError("invalid reply event")
        return cls(ReplyDetail.from_payload(value["reply"]))


@dataclass(frozen=True, slots=True)
class InteractionInput:
    id: str
    character: str | None
    text: str
    input_source: str
    observed_at: float
    observed_outcome: str

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> InteractionInput:
        if not isinstance(value, Mapping):
            raise ValueError("invalid interaction input")
        submission = InputSubmission.from_payload(
            {
                k: value.get(k)
                for k in ("id", "character", "text", "input_source", "observed_at")
            }
        )
        return cls(
            submission.id,
            submission.character,
            submission.text,
            submission.input_source,
            submission.observed_at,
            _string(value.get("observed_outcome"), "observed_outcome"),
        )


@dataclass(frozen=True, slots=True)
class InteractionDetail:
    input: InteractionInput
    replies: tuple[ReplyDetail, ...]

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> InteractionDetail:
        if not isinstance(value, Mapping) or not isinstance(value.get("replies"), list):
            raise ValueError("invalid interaction detail")
        return cls(
            InteractionInput.from_payload(value.get("input")),
            tuple(ReplyDetail.from_payload(r) for r in value["replies"]),
        )
