from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Literal
from collections.abc import Mapping, Sequence

ReplyMode = Literal["task_result", "proactive", "silent"]

_ALLOWED_REPLY_MODES: frozenset[str] = frozenset({"task_result", "proactive", "silent"})


def _normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalize_fields(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        field_name = item.strip()
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        normalized.append(field_name)
    return tuple(normalized)


def _normalize_priority(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class AgentReplySpec:
    reply: bool | None = None
    mode: ReplyMode | None = None
    include: bool | None = None
    fields: tuple[str, ...] = ()
    summary: str | None = None
    detail: str | None = None
    priority: int = 0
    explicit: bool = False

    @property
    def reply_enabled(self) -> bool:
        if self.mode == "silent":
            return False
        return self.reply is True


@dataclass(frozen=True, slots=True)
class ReplyCandidate:
    source: str
    payload_type: str
    payload: object = None
    error: object = None
    message: str = ""
    sequence: int = 0
    spec: AgentReplySpec = AgentReplySpec()

    @property
    def reply_enabled(self) -> bool:
        if self.spec.mode == "silent":
            return False
        return self.spec.reply is True

    @property
    def visible_payload(self) -> object:
        if self.spec.include is False:
            return None
        if self.payload_type != "json" or not self.spec.fields or not isinstance(self.payload, Mapping):
            return self.payload
        return {
            field_name: self.payload[field_name]
            for field_name in self.spec.fields
            if field_name in self.payload
        }

    def has_visible_content(self) -> bool:
        if self.spec.summary or self.spec.detail:
            return True

        payload = self.visible_payload
        if payload is None:
            return False
        if isinstance(payload, str):
            return bool(payload.strip())
        if isinstance(payload, Mapping):
            return bool(payload)
        if isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray)):
            return bool(payload)
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "payload_type": self.payload_type,
            "data": self.visible_payload,
            "error": self.error,
            "message": self.message,
            "sequence": self.sequence,
            "reply": self.reply_enabled,
            "mode": self.spec.mode,
            "include": self.spec.include,
            "fields": list(self.spec.fields),
            "summary": self.spec.summary,
            "detail": self.spec.detail,
            "priority": self.spec.priority,
            "explicit": self.spec.explicit,
        }


def parse_agent_reply_spec(
    metadata: Mapping[str, object] | None,
    *,
    default_reply: bool | None = None,
    default_mode: ReplyMode | None = None,
    default_priority: int = 0,
) -> AgentReplySpec:
    raw_agent_meta = metadata.get("agent") if isinstance(metadata, Mapping) else None
    agent_meta = raw_agent_meta if isinstance(raw_agent_meta, Mapping) else None

    explicit = False
    reply = default_reply
    include: bool | None = None
    mode: ReplyMode | None = default_mode
    fields: tuple[str, ...] = ()
    summary: str | None = None
    detail: str | None = None
    priority = default_priority

    if agent_meta is not None:
        explicit = any(
            key in agent_meta
            for key in ("reply", "include", "mode", "fields", "summary", "detail", "priority")
        )

        raw_reply = agent_meta.get("reply")
        if isinstance(raw_reply, bool):
            reply = raw_reply

        raw_include = agent_meta.get("include")
        if isinstance(raw_include, bool):
            include = raw_include

        raw_mode = agent_meta.get("mode")
        if isinstance(raw_mode, str):
            normalized_mode = raw_mode.strip().lower()
            if normalized_mode in _ALLOWED_REPLY_MODES:
                mode = normalized_mode  # type: ignore[assignment]

        fields = _normalize_fields(agent_meta.get("fields"))
        summary = _normalize_text(agent_meta.get("summary"))
        detail = _normalize_text(agent_meta.get("detail"))
        priority = _normalize_priority(agent_meta.get("priority"), default=default_priority)

    if include is None and reply is True:
        include = True

    return AgentReplySpec(
        reply=reply,
        mode=mode,
        include=include,
        fields=fields,
        summary=summary,
        detail=detail,
        priority=priority,
        explicit=explicit,
    )


def with_fallback_fields(spec: AgentReplySpec, fields: Sequence[str] | None) -> AgentReplySpec:
    normalized = _normalize_fields(fields)
    if spec.fields or not normalized:
        return spec
    return replace(spec, fields=normalized)


def is_trigger_response_item(item: Mapping[str, object]) -> bool:
    metadata_obj = item.get("metadata")
    metadata = metadata_obj if isinstance(metadata_obj, Mapping) else {}
    kind = metadata.get("kind")
    if kind == "trigger_response":
        return True
    if item.get("label") == "trigger_response":
        return True
    return bool(item.get("category") == "system" and item.get("type") == "json")


def export_payload_from_item(item: Mapping[str, object]) -> object:
    export_type = item.get("type")
    if export_type == "json":
        return item.get("json") if item.get("json") is not None else item.get("json_data")
    if export_type == "text":
        return item.get("text")
    if export_type == "url":
        return item.get("url")
    if export_type == "binary_url":
        return item.get("binary_url")
    if export_type == "binary":
        return item.get("binary")
    return None


def trigger_response_candidate(
    envelope: Mapping[str, object],
    *,
    fallback_fields: Sequence[str] | None = None,
    sequence: int = 0,
) -> ReplyCandidate:
    meta_obj = envelope.get("meta")
    meta = meta_obj if isinstance(meta_obj, Mapping) else None
    spec = with_fallback_fields(
        parse_agent_reply_spec(meta, default_reply=True, default_mode="task_result"),
        fallback_fields,
    )
    message = _normalize_text(envelope.get("message")) or ""
    return ReplyCandidate(
        source="trigger_response",
        payload_type="json",
        payload=envelope.get("data"),
        error=envelope.get("error"),
        message=message,
        sequence=sequence,
        spec=spec,
    )


def export_item_candidate(item: Mapping[str, object], *, sequence: int = 0) -> ReplyCandidate:
    metadata_obj = item.get("metadata")
    metadata = metadata_obj if isinstance(metadata_obj, Mapping) else None
    spec = parse_agent_reply_spec(metadata, default_reply=False, default_mode="task_result")
    export_type_obj = item.get("type")
    export_type = export_type_obj if isinstance(export_type_obj, str) and export_type_obj else "unknown"
    message = (
        _normalize_text(item.get("description"))
        or _normalize_text(item.get("label"))
        or ""
    )
    return ReplyCandidate(
        source=f"export:{item.get('export_item_id') or sequence}",
        payload_type=export_type,
        payload=export_payload_from_item(item),
        message=message,
        sequence=sequence,
        spec=spec,
    )


def choose_reply_candidate(candidates: Sequence[ReplyCandidate]) -> ReplyCandidate | None:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.reply_enabled and candidate.has_visible_content()
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda candidate: (
            int(candidate.spec.priority),
            1 if candidate.spec.explicit else 0,
            int(candidate.sequence),
        ),
    )


def resolve_message_reply_text(
    *,
    content: object,
    metadata: Mapping[str, object] | None,
    default_reply: bool = True,
    parser: Callable[[object], str] | None = None,
) -> tuple[AgentReplySpec, str]:
    spec = parse_agent_reply_spec(metadata, default_reply=default_reply, default_mode="proactive")
    if not spec.reply_enabled:
        return spec, ""
    if spec.summary:
        return spec, spec.summary
    if spec.detail:
        return spec, spec.detail
    if spec.include is False:
        return spec, ""
    if parser is not None:
        return spec, parser(content).strip()
    return spec, str(content or "").strip()


__all__ = [
    "AgentReplySpec",
    "ReplyCandidate",
    "ReplyMode",
    "choose_reply_candidate",
    "export_item_candidate",
    "export_payload_from_item",
    "is_trigger_response_item",
    "parse_agent_reply_spec",
    "resolve_message_reply_text",
    "trigger_response_candidate",
    "with_fallback_fields",
]
