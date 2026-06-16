from __future__ import annotations

from .entry_common import (
    Any,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    _normalize_submitted_image_payload,
    _validate_optional_vision_image_payload,
    _plugin_lock,
    plugin_entry,
    tr,
    ui,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    MODE_COMPANION,
    MODE_CONCEPT_EXPLAIN,
    handle_user_intent,
)


IMAGE_ONLY_EXPLAIN_PROMPT_EN = (
    "First identify the problem in the image, then provide a detailed solution "
    "process: list the givens, show the key derivation or construction, verify "
    "the result, and clearly state the final answer or option. When solving a "
    "problem, your response must include four headings: \"Problem Analysis\", "
    "\"Solution Process\", \"Answer\", and \"Transfer Practice\". Do not give "
    "only a brief \"analysis\". Do not only summarize the problem statement; "
    "if the image is not a problem, explain the image contents instead. If it "
    "is a choice question or item-by-item judgment question, do not assume it "
    "is single-choice; verify each item independently. If there are multiple "
    "correct options, output all correct options in \"Answer\"."
)
IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN = (
    "请先识别图片中的题目，给出详细的解答过程：列出已知条件、展示关键推导或构造、"
    "验证结果，并明确最终答案或选项。回答题目时必须包含“题目解析”“解题过程”“答案”"
    "和“举一反三”四个小标题，不要只给简短“解析”。不要只总结题意；如果图片不是题目，"
    "再解释图片内容。如果是选择题或逐项判断题，不要默认是单选题；必须逐项验证，"
    "若有多个正确选项，需在“答案”中输出全部正确选项。"
)
IMAGE_ONLY_EXPLAIN_PROMPT_ZH_TW = (
    "請先識別圖片中的題目，然後給出可見的解答過程：列出已知條件、展示關鍵推導或構造、"
    "驗證結果，並明確最終答案或選項。回答題目時必須包含「題目解析」「解題過程」「答案」"
    "和「舉一反三」四個小標題，不要只給簡短「解析」。不要只總結題意；如果圖片不是題目，"
    "再解釋圖片內容。如果是選擇題或逐項判斷題，不要預設是單選題；必須逐項驗證，"
    "不要找到一個正確選項就停止；若有多個正確選項，在「答案」中輸出全部正確選項。"
)


def _image_only_explain_prompt(language: str) -> str:
    normalized = str(language or "").strip().lower()
    if normalized.startswith(("zh-tw", "zh-hk", "zh-hant")):
        return IMAGE_ONLY_EXPLAIN_PROMPT_ZH_TW
    if normalized.startswith("zh"):
        return IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN
    return IMAGE_ONLY_EXPLAIN_PROMPT_EN


class _TutorExplainEntriesMixin:
    @plugin_entry(
        id="study_submit_image",
        name=tr("entries.submit_image.name", default="Submit Study Image"),
        description=tr(
            "entries.submit_image.description",
            default="Accept a user image and explain it with the configured vision model.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "image_base64": {"type": "string"},
                "text": {"type": "string", "default": ""},
            },
            "required": ["image_base64"],
        },
        timeout=300.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_submit_image(self, image_base64: str, text: str = "", **_):
        try:
            image_payload = _normalize_submitted_image_payload(image_base64)
        except ValueError as exc:
            return _entry_exception_error(self, exc, operation="study_submit_image")
        if not bool(self._cfg.llm_vision_enabled):
            return Err(SdkError("llm_vision_enabled is not enabled"))
        normalized_text = str(text or "").strip()
        if normalized_text:
            async with _plugin_lock(self._lock):
                self._state.last_ocr_text = normalized_text
        source_text = normalized_text or _image_only_explain_prompt(
            self._cfg.language
        )
        return await self.study_explain_text(
            text=source_text,
            vision_image_base64=image_payload,
        )

    @ui.action()
    @plugin_entry(
        id="study_explain_text",
        name=tr("entries.explain_text.name", default="Explain Study Text"),
        description=tr(
            "entries.explain_text.description",
            default="Explain a concept from supplied text, or use the latest OCR text if text is omitted.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
                "vision_image_base64": {"type": "string", "default": ""},
            },
        },
        timeout=300.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_explain_text(
        self, text: str = "", vision_image_base64: str = "", **_
    ):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        raw_text = str(text or "").strip()
        # Phase 1: detect an explicit mode intent and switch first when present.
        intent = (
            handle_user_intent(raw_text, language=self._cfg.language)
            if raw_text
            else {
                "matched": False,
                "pure_switch": False,
                "mode": "",
                "remaining_text": "",
            }
        )
        async with _plugin_lock(self._lock):
            active_mode = self._state.active_mode
        mode_switch: dict[str, Any] = {}
        if intent.get("matched") and intent.get("kind") == "mode_switch":
            try:
                mode_switch = await self._apply_mode_switch(
                    str(intent.get("mode") or MODE_COMPANION),
                    f"intent:{intent.get('keyword') or 'text'}",
                    language=self._cfg.language,
                )
                active_mode = str(mode_switch.get("new_mode") or active_mode)
            except ValueError as exc:
                return _entry_exception_error(self, exc, operation="study_explain_text")
            if intent.get("pure_switch"):
                transition_phrase = str(
                    mode_switch.get("transition_phrase")
                    or intent.get("transition_phrase")
                    or ""
                )
                return Ok(
                    {
                        **mode_switch,
                        "reply": transition_phrase,
                        "summary": transition_phrase,
                        "operation": MODE_CONCEPT_EXPLAIN,
                        "input_text": raw_text,
                        "degraded": False,
                    }
                )
        # Phase 2: resolve the text to explain.
        intent_kind = str(intent.get("kind") or "")
        source_text = str(intent.get("remaining_text") or "").strip()
        if not source_text and intent_kind != "concept_explain":
            source_text = raw_text
        vision_image_payload = str(vision_image_base64 or "").strip()
        used_ocr_fallback = False
        if not source_text and not vision_image_payload:
            async with _plugin_lock(self._lock):
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        source_text = source_text.strip()
        if not source_text and not vision_image_payload:
            return Err(
                SdkError(
                    "study tutor requires text or a non-empty OCR snapshot",
                    code="MISSING_TEXT",
                )
            )
        # Phase 3: explain with the active mode selected above.
        try:
            image_only_source = False
            if vision_image_payload:
                validated_vision_image = _validate_optional_vision_image_payload(
                    self, vision_image_payload, operation="study_explain_text"
                )
                if isinstance(validated_vision_image, Err):
                    return validated_vision_image
                vision_image_payload = validated_vision_image
                if not source_text:
                    source_text = _image_only_explain_prompt(self._cfg.language)
                    image_only_source = True
            extra_context: dict[str, Any] = {
                "source": "ocr_snapshot"
                if used_ocr_fallback
                else ("vision_image" if image_only_source else "manual"),
                "mode": active_mode,
                "mode_switch": bool(mode_switch.get("changed")),
                "source_text": source_text,
            }
            if vision_image_payload:
                extra_context["vision_enabled"] = True
                extra_context["vision_image_base64"] = vision_image_payload
            tutor_context = await self._build_learning_context(
                LLM_OPERATION_CONCEPT_EXPLAIN,
                input_text=source_text,
                extra=extra_context,
            )
            reply = await self._agent.concept_explain(
                source_text,
                mode=active_mode,
                context=tutor_context,
            )
            payload = await self._finalize_tutor_call(
                LLM_OPERATION_CONCEPT_EXPLAIN,
                reply,
                history_kind=MODE_CONCEPT_EXPLAIN,
                metadata={
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                    "mode": active_mode,
                    "mode_switch": mode_switch,
                    "intent": intent,
                    "screen_classification": tutor_context.get("screen_classification")
                    or {},
                },
                extra_context=tutor_context,
            )
            if mode_switch:
                payload["mode_switch"] = mode_switch
            if intent.get("matched"):
                payload["intent"] = intent
                if intent.get("pure_switch"):
                    payload["transition_phrase"] = str(
                        mode_switch.get("transition_phrase")
                        or intent.get("transition_phrase")
                        or ""
                    )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_explain_text")
