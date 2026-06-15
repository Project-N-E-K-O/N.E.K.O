from __future__ import annotations

from .entry_common import (
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    _validate_optional_vision_image_payload,
    plugin_entry,
    tr,
    ui,
    LLM_OPERATION_QUESTION_GENERATE,
)


IMAGE_ONLY_QUESTION_PROMPT_EN = "Generate a study question from the pasted image."
IMAGE_ONLY_QUESTION_PROMPT_ZH_CN = "请根据这张图片生成一道学习题。"
IMAGE_ONLY_QUESTION_PROMPT_ZH_TW = "請根據這張圖片生成一道學習題。"


def _image_only_question_prompt(language: str) -> str:
    normalized = str(language or "").strip().lower()
    if normalized.startswith(("zh-tw", "zh-hk", "zh-hant")):
        return IMAGE_ONLY_QUESTION_PROMPT_ZH_TW
    if normalized.startswith("zh"):
        return IMAGE_ONLY_QUESTION_PROMPT_ZH_CN
    return IMAGE_ONLY_QUESTION_PROMPT_EN


class _TutorQuestionEntriesMixin:
    @ui.action()
    @plugin_entry(
        id="study_generate_question",
        name=tr("entries.generate_question.name", default="Generate Study Question"),
        description=tr(
            "entries.generate_question.description",
            default="Generate one study question from supplied text or the latest OCR text.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
                "topic": {"type": "string", "default": ""},
                "vision_image_base64": {"type": "string", "default": ""},
            },
        },
        timeout=300.0,
        llm_result_fields=[
            "summary",
            "question",
            "answer",
            "hint",
            "difficulty",
            "topic",
        ],
    )
    async def study_generate_question(
        self,
        text: str = "",
        topic: str = "",
        vision_image_base64: str = "",
        **_,
    ):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        source_text = str(text or "").strip()
        vision_image_payload = str(vision_image_base64 or "").strip()
        used_ocr_fallback = False
        if not source_text and not vision_image_payload:
            async with self._lock:
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        source_text = source_text.strip()
        if not source_text and not vision_image_payload:
            return Err(
                SdkError(
                    "study tutor requires text, an image, or a non-empty OCR snapshot",
                    code="MISSING_TEXT",
                )
            )
        validated_vision_image = _validate_optional_vision_image_payload(
            self, vision_image_payload, operation="study_generate_question"
        )
        if isinstance(validated_vision_image, Err):
            return validated_vision_image
        vision_image_payload = validated_vision_image
        try:
            image_only_source = False
            if not source_text and vision_image_payload:
                source_text = _image_only_question_prompt(self._cfg.language)
                image_only_source = True
            async with self._lock:
                active_mode = self._state.active_mode
            tutor_context = await self._build_learning_context(
                LLM_OPERATION_QUESTION_GENERATE,
                input_text=source_text,
                extra={
                    "source": "ocr_snapshot"
                    if used_ocr_fallback
                    else ("vision_image" if image_only_source else "manual"),
                    "source_text": source_text,
                    "topic_hint": str(topic or "").strip(),
                    "mode": active_mode,
                    **(
                        {
                            "vision_enabled": True,
                            "vision_image_base64": vision_image_payload,
                        }
                        if vision_image_payload
                        else {}
                    ),
                },
            )
            reply = await self._agent.question_generate(
                source_text, mode=active_mode, context=tutor_context
            )
            payload = await self._finalize_tutor_call(
                LLM_OPERATION_QUESTION_GENERATE,
                reply,
                history_kind=LLM_OPERATION_QUESTION_GENERATE,
                metadata={
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                    "payload": reply.payload,
                    "screen_classification": tutor_context.get("screen_classification")
                    or {},
                },
                extra_context=tutor_context,
            )
            payload["screen_classification"] = (
                tutor_context.get("screen_classification") or {}
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_generate_question"
            )
