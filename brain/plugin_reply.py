from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping, Sequence

from .result_parser import parse_plugin_result, _get_lang, _phrase


@dataclass(frozen=True, slots=True)
class PluginReplyEvent:
    emit: bool
    event_type: str = "task_result"
    summary: str = ""
    detail: str = ""
    error_message: str = ""


def _reply_fields(reply: Mapping[str, object] | None) -> list[str] | None:
    if not isinstance(reply, Mapping):
        return None
    raw_fields = reply.get("fields")
    if not isinstance(raw_fields, Sequence) or isinstance(raw_fields, (str, bytes, bytearray)):
        return None
    fields: list[str] = []
    for item in raw_fields:
        if isinstance(item, str) and item:
            fields.append(item)
    return fields or None


def build_plugin_reply_event(
    *,
    plugin_id: str,
    completion: Mapping[str, object] | None,
    success: bool,
    fallback_error: object = None,
    lang: str | None = None,
) -> PluginReplyEvent:
    result = completion if isinstance(completion, Mapping) else {}
    reply = result.get("reply")
    has_reply_contract = isinstance(reply, Mapping)
    if result.get("reply_suppressed") is True:
        return PluginReplyEvent(emit=False)

    reply_obj = reply if isinstance(reply, Mapping) else {}
    if reply_obj.get("reply") is False:
        return PluginReplyEvent(emit=False)

    event_type = "proactive_message" if reply_obj.get("mode") == "proactive" else "task_result"
    visible_data = reply_obj.get("data")
    payload_type = reply_obj.get("payload_type")
    explicit_summary = str(reply_obj.get("summary") or "").strip()
    explicit_detail = str(reply_obj.get("detail") or "").strip()
    reply_message = str(reply_obj.get("message") or result.get("reply_message") or "").strip()
    llm_result_fields = _reply_fields(reply_obj)

    error_obj = None if success else (
        reply_obj.get("error")
        or result.get("run_error")
        or fallback_error
    )
    base_payload = visible_data if has_reply_contract else result.get("run_data")
    if (
        has_reply_contract
        and error_obj is None
        and visible_data is None
        and not explicit_summary
        and not explicit_detail
        and not reply_message
    ):
        return PluginReplyEvent(emit=False)

    detail = explicit_detail
    if not detail:
        if payload_type in ("text", "url", "binary_url") and success:
            detail = str(base_payload or reply_message or "").strip()
        else:
            detail = parse_plugin_result(
                base_payload,
                llm_result_fields=llm_result_fields,
                plugin_message=reply_message,
                error=error_obj,
                lang=lang,
            )
    detail = detail.strip()

    if explicit_summary:
        summary = explicit_summary
    else:
        normalized_lang = _get_lang(lang)
        if success:
            summary = (
                _phrase("plugin_done_with", normalized_lang, id=plugin_id, detail=detail)
                if detail
                else _phrase("plugin_done", normalized_lang, id=plugin_id)
            )
        else:
            summary = (
                _phrase("plugin_failed_with", normalized_lang, id=plugin_id, detail=detail)
                if detail
                else _phrase("plugin_failed", normalized_lang, id=plugin_id)
            )

    if not summary and not detail:
        return PluginReplyEvent(emit=False)

    return PluginReplyEvent(
        emit=True,
        event_type=event_type,
        summary=summary.strip(),
        detail=detail,
        error_message="" if success else (detail or str(error_obj or "")).strip(),
    )


__all__ = ["PluginReplyEvent", "build_plugin_reply_event"]
