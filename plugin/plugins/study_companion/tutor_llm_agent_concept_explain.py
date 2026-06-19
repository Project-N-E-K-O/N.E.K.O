from __future__ import annotations

from .tutor_llm_agent_common import (
    Any,
    asyncio,
    STUDY_FALLBACK_EXPLANATION_DEFAULT,
    SdkError,
    MODE_COMPANION,
    MODE_TEACHING,
    build_concept_explain_messages,
    build_transition_phrase,
    normalize_mode,
    MODE_CONCEPT_EXPLAIN,
    TutorReply,
    utc_now_iso,
    diagnostic_code_for_exception,
)


VISION_FALLBACK_EXPLANATION_EN = (
    "I could not reach the configured vision-capable model, so I cannot "
    "reliably identify or solve the problem in the image. Please check the "
    "model configuration, or paste the problem text into the text box and try "
    "again."
)
VISION_FALLBACK_EXPLANATION_ZH_CN = (
    "我无法连接到已配置的视觉模型，因此不能可靠识别或解答图片中的题目。"
    "请检查模型配置，或把题目文字粘贴到文本框后再试。"
)
VISION_FALLBACK_EXPLANATION_ZH_TW = (
    "我無法連接到已配置的視覺模型，因此不能可靠識別或解答圖片中的題目。"
    "請檢查模型配置，或把題目文字貼到文字框後再試。"
)

ZH_TRANSFER_FALLBACK = (
    "可以把题目中的条件、数值或问法换成同类型设定，"
    "仍按“题目解析 → 解题过程 → 答案”的顺序梳理。"
)
ZH_TW_TRANSFER_FALLBACK = (
    "可以把題目中的條件、數值或問法換成同類型設定，"
    "仍按「題目解析 → 解題過程 → 答案」的順序梳理。"
)


def _vision_fallback_explanation(language: str | None) -> str:
    normalized = str(language or "").strip().lower()
    if normalized.startswith(("zh-tw", "zh-hk", "zh-hant")):
        return VISION_FALLBACK_EXPLANATION_ZH_TW
    if normalized.startswith("zh"):
        return VISION_FALLBACK_EXPLANATION_ZH_CN
    return VISION_FALLBACK_EXPLANATION_EN


def _ensure_transfer_section(
    reply: str, language: str | None, source_text: str = ""
) -> str:
    normalized_language = str(language or "").strip().lower()
    if "举一反三" in reply or "舉一反三" in reply:
        return reply
    reply_is_zh_solution = any(
        token in reply
        for token in (
            "解析",
            "题目",
            "題目",
            "解题",
            "解題",
            "验证",
            "驗證",
            "结论",
            "結論",
            "答案",
            "选项",
            "選項",
        )
    )
    source_requires_transfer = "举一反三" in source_text or "舉一反三" in source_text
    should_check_transfer = (
        normalized_language.startswith("zh")
        or reply_is_zh_solution
        or source_requires_transfer
    )
    if not should_check_transfer:
        return reply
    has_structured_solution = (
        ("题目解析" in reply or "題目解析" in reply)
        and ("解题过程" in reply or "解題過程" in reply)
        and "答案" in reply
    )
    has_numbered_solution = "答案" in reply and any(
        token in reply for token in ("计算", "選項", "选项", "最终答案", "最終答案")
    )
    has_option_verification = (
        ("结论" in reply or "結論" in reply)
        and ("验证" in reply or "驗證" in reply)
        and any(token in reply for token in ("正确", "正確", "错误", "錯誤"))
    )
    if not (
        source_requires_transfer
        or has_structured_solution
        or has_numbered_solution
        or has_option_verification
    ):
        return reply
    if normalized_language.startswith(("zh-tw", "zh-hk", "zh-hant")):
        return f"{reply.rstrip()}\n\n舉一反三\n{ZH_TW_TRANSFER_FALLBACK}"
    return f"{reply.rstrip()}\n\n举一反三\n{ZH_TRANSFER_FALLBACK}"


async def concept_explain(
    self,
    text: str,
    *,
    mode: str = MODE_COMPANION,
    context: dict[str, Any] | None = None,
) -> TutorReply:
    normalized = str(text or "").strip()
    if not normalized:
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text="",
            reply=self._localize_reply(self._config.language, "empty_input"),
            degraded=True,
            diagnostic="empty_input",
            created_at=utc_now_iso(),
        )
    selected_mode = normalize_mode(mode)
    teaching_prefix = (
        build_transition_phrase(
            MODE_TEACHING, language=self._config.language, outcome="changed"
        )
        if selected_mode == MODE_TEACHING
        else ""
    )
    messages = build_concept_explain_messages(
        text=normalized,
        language=self._config.language,
        mode=selected_mode,
        context=context,
    )
    vision_image_base64 = (
        str(context.get("vision_image_base64") or "") if context else ""
    )
    if vision_image_base64:
        messages = self._attach_vision_image(messages, vision_image_base64)
    try:
        content = await self._call_model(messages)
        reply = content.strip()
        if not reply:
            raise SdkError("empty model response")
        if teaching_prefix and not reply.startswith(teaching_prefix):
            reply = f"{teaching_prefix}\n\n{reply}"
        reply = _ensure_transfer_section(reply, self._config.language, normalized)
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text=normalized,
            reply=reply,
            degraded=False,
            created_at=utc_now_iso(),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self._logger.warning("study concept_explain degraded: {}", exc)
        if vision_image_base64:
            fallback_reply = _vision_fallback_explanation(self._config.language)
        else:
            fallback_reply = self._localize_reply(
                self._config.language,
                "fallback_explanation",
                default=STUDY_FALLBACK_EXPLANATION_DEFAULT,
                first_line=next(
                    (line.strip() for line in normalized.splitlines() if line.strip()),
                    normalized[:120],
                ),
            )
        if teaching_prefix and not fallback_reply.startswith(teaching_prefix):
            fallback_reply = f"{teaching_prefix}\n\n{fallback_reply}"
        return TutorReply(
            operation=MODE_CONCEPT_EXPLAIN,
            input_text=normalized,
            reply=fallback_reply,
            degraded=True,
            diagnostic=diagnostic_code_for_exception(exc),
            created_at=utc_now_iso(),
        )
