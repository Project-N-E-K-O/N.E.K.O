from __future__ import annotations

from plugin.server.application.plugins.event_contracts import (
    VOICE_TRANSCRIPT_EVENT_TYPE,
)

from .entry_common import (
    Any,
    SimpleNamespace,
    Ok,
    custom_event,
    STATUS_READY,
    _derive_subject,
    build_context_for_catgirl,
    _voice_session_key,
)


class _VoiceBridgeMixin:
    @custom_event(
        event_type=VOICE_TRANSCRIPT_EVENT_TYPE,
        id="handle_transcript",
        name="Handle study voice transcript",
        description="Filter realtime study voice transcripts and return a voice-session action.",
        input_schema={
            "type": "object",
            "properties": {
                "transcript": {"type": "string"},
                "lanlan_name": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["transcript"],
        },
        trigger_method="manual",
    )
    async def handle_voice_transcript(
        self,
        transcript: str = "",
        lanlan_name: str = "",
        metadata: dict[str, Any] | None = None,
        **_,
    ):
        text = str(transcript or "").strip()
        if not text:
            return Ok({"action": "noop", "reason": "empty_transcript"})
        metadata_payload = metadata if isinstance(metadata, dict) else {}
        session_key = _voice_session_key(lanlan_name, metadata_payload)

        async with self._lock:
            if self._state.status != STATUS_READY:
                return Ok({"action": "noop", "reason": "not_ready"})
            state_snapshot_payload = self._state.to_dict()

        # Voice filtering only needs a point-in-time view; avoid holding the
        # plugin lock while building OCR context or applying filter rules.
        screen_text = str(state_snapshot_payload.get("last_ocr_text") or "")
        screen_classification = (
            state_snapshot_payload.get("last_screen_classification")
            if isinstance(
                state_snapshot_payload.get("last_screen_classification"), dict
            )
            else {}
        )
        screen_type = str(screen_classification.get("screen_type") or "")
        session_seed = (
            state_snapshot_payload.get("session_summary_seed")
            if isinstance(state_snapshot_payload.get("session_summary_seed"), dict)
            else {}
        )
        screen_context = {
            "topic": str(session_seed.get("last_topic") or "").strip(),
            "subject": _derive_subject(screen_text),
        }
        filter_result = self._voice_filter.filter(
            text,
            screen_text=screen_text,
            screen_type=screen_type,
            subject=screen_context["subject"],
            session_key=session_key,
            extra_names=[lanlan_name],
        )
        if filter_result is None:
            return Ok({"action": "noop", "reason": "not_matched"})
        if not bool(filter_result.get("should_relay")):
            return Ok({"action": "cancel_response", "filter": dict(filter_result)})

        state_snapshot = SimpleNamespace(**state_snapshot_payload)
        context_text = build_context_for_catgirl(
            text,
            state_snapshot,
            screen_context,
            filter_result,
        ).strip()
        if not context_text:
            return Ok(
                {
                    "action": "noop",
                    "reason": "empty_context",
                    "filter": dict(filter_result),
                }
            )
        context_payload: dict[str, Any] = {
            "schema": "study_companion.voice_context.v1",
            "source": "study_companion",
            "user_transcript": text,
            "question": str(filter_result.get("question") or text).strip(),
            "pre_context": str(filter_result.get("pre_context") or "").strip(),
            "screen_ocr": screen_text.strip(),
            "screen_type": screen_type,
            "subject": screen_context["subject"],
            "topic": screen_context["topic"],
            "filter_method": str(filter_result.get("method") or "").strip(),
        }
        context_payload = {
            key: value for key, value in context_payload.items() if value != ""
        }
        return Ok(
            {
                "action": "prime_context",
                "context": context_text,
                "context_payload": context_payload,
                "skipped": False,
                "filter": dict(filter_result),
                "lanlan_name": str(lanlan_name or ""),
            }
        )
