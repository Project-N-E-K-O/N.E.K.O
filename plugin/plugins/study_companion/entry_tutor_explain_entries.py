from __future__ import annotations

import asyncio
from time import monotonic

from ._general_narration import prepare_general_narration_content
from ._solution_structure import (
    extract_solution_narration_sections,
    is_solution_structure_candidate,
    parse_solution_structure,
    render_solution_structure,
)
from .tutor_llm_agent_concept_explain import repair_solution_structure
from .entry_tutor_context_support import _TutorFinalizeProgress
from .entry_common import (
    Any,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    _normalize_submitted_image_payload,
    _validate_optional_vision_image_payload,
    _plugin_lock,
    build_tutor_payload,
    plugin_entry,
    tr,
    ui,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    MODE_COMPANION,
    MODE_CONCEPT_EXPLAIN,
    handle_user_intent,
)


IMAGE_ONLY_EXPLAIN_PROMPT_EN = (
    "First identify the problem in the image, then provide a concise, reproducible "
    "solution. Output only the verified formal derivation. When solving a problem, use "
    "exactly these four headings once and in this order: \"Problem Analysis\", "
    "\"Solution Process\", \"Answer\", and \"Transfer Practice\". Put any "
    "givens, target, and core rule only in \"Problem Analysis\". State the givens, "
    "target, and applicable rules, including formulas or theorems, but no secondary "
    "detail. In \"Solution "
    "Process\", keep only verified key derivations, numbered by sub-question, and "
    "show key substitutions; check units, boundaries, or the result when necessary. "
    "Put supplementary work only as numbered body text and never add another heading. "
    "Make \"Answer\" self-contained and cover every "
    "sub-question. Give exactly one short variant in \"Transfer Practice\". Reserve "
    "output budget for a complete \"Answer\" and \"Transfer Practice\"; if "
    "secondary process details might crowd out either section, omit those details. "
    "\"Transfer Practice\" must be the final section with nothing after it. Do not "
    "add a preface, epilogue, note, fifth heading, draft-like exploration, "
    "self-correction, reconsideration, or a repeated problem statement. If image "
    "information is insufficient, state the missing information in \"Answer\"; do "
    "not guess geometry, labels, or repeatedly hypothesize about the figure. If the "
    "image is not a problem, explain the image contents instead. "
    "If it is a choice question or item-by-item judgment question, do not assume "
    "it is single-choice; verify each item independently. If there are multiple "
    "correct options, output all correct options in \"Answer\"."
)
IMAGE_ONLY_EXPLAIN_PROMPT_ZH_CN = (
    "请先识别图片中的题目，再给出精简、可复算的解答，只输出核验后的正式推导。回答题目时必须且"
    "只能按固定顺序各使用一次"
    "“题目解析”“解题过程”“答案”和“举一反三”四个小标题。补充推导只能作为“解题过程”下的"
    "编号正文，不得另设标题。“题目解析”只列出已知条件、目标和核心规律；“解题过程”只保留核验后的"
    "关键推导，注明公式或定理的依据和关键代入，并按小问编号；必要时检查单位、边界并进行必要验算；"
    "“答案”必须自足并覆盖所有小问；“举一反三”恰好给出一道简短变式。必须为"
    "完整的“答案”和“举一反三”预留输出预算，"
    "若次要过程细节可能挤掉其中任一完整部分，就省略这些次要细节。“举一反三”必须是最后一节，"
    "之后不得有任何内容。禁止前言、尾注、第五标题、草稿式探索、自我修正、重新审视和重复题干。"
    "若图像信息不足，必须在“答案”中明确缺失条件；禁止猜测几何关系、标注或反复假设图形。如果图片"
    "不是题目，再解释图片内容。如果是选择题或逐项判断题，不要默认是单选题；"
    "必须逐项验证，若有多个正确选项，需在“答案”中输出全部正确选项。"
)
IMAGE_ONLY_EXPLAIN_PROMPT_ZH_TW = (
    "請先識別圖片中的題目，再給出精簡、可復算的解答，只輸出核驗後的正式推導。回答題目時必須且"
    "只能按固定順序各使用一次"
    "「題目解析」「解題過程」「答案」和「舉一反三」四個小標題。補充推導只能作為「解題過程」下的"
    "編號正文，不得另設標題。「題目解析」只列出已知條件、目標和核心規律；「解題過程」只保留核驗後的"
    "關鍵推導，註明公式或定理的依據和關鍵代入，並按小問編號；必要時檢查單位、邊界並進行必要驗算；"
    "「答案」必須自足並涵蓋所有小問；「舉一反三」恰好給出一道簡短變式。必須為"
    "完整的「答案」和「舉一反三」預留輸出預算，"
    "若次要過程細節可能擠掉其中任一完整部分，就省略這些次要細節。「舉一反三」必須是最後一節，"
    "之後不得有任何內容。禁止前言、尾註、第五標題、草稿式探索、自我修正、重新審視和重複題幹。"
    "若圖像資訊不足，必須在「答案」中明確缺失條件；禁止猜測幾何關係、標註或反覆假設圖形。如果圖片"
    "不是題目，再解釋圖片內容。如果是選擇題或逐項判斷題，不要預設是單選題；"
    "必須逐項驗證，不要找到一個正確選項就停止；若有多個正確選項，在「答案」中輸出全部正確選項。"
)

_PRIMARY_EXPLAIN_TIMEOUT_SECONDS = 70.0
_SOLUTION_REPAIR_TIMEOUT_SECONDS = 15.0
_SOLUTION_REPAIR_MIN_REMAINING_SECONDS = 10.0
_FINALIZE_TIMEOUT_SECONDS = 5.0


def _build_finalize_failure_payload(
    reply: Any, *, diagnostic: str, history_persisted: bool = False
) -> dict[str, Any]:
    payload = build_tutor_payload(reply)
    payload["history_persisted"] = history_persisted
    payload["diagnostic"] = diagnostic
    return payload


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
        timeout=105.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_submit_image(self, image_base64: str, text: str = "", **kwargs):
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
            **kwargs,
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
        timeout=105.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_explain_text(
        self, text: str = "", vision_image_base64: str = "", **kwargs
    ):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        target_lanlan = self._resolve_study_target_lanlan(kwargs)
        started_monotonic = monotonic()
        primary_deadline_monotonic = (
            started_monotonic + _PRIMARY_EXPLAIN_TIMEOUT_SECONDS
        )
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
                        "solution_narration_scheduled": False,
                        "solution_narration_status": "not_applicable",
                        "solution_narration_reason": "",
                        "solution_repair_attempted": False,
                        "solution_narration_missing_sections": [],
                        "general_narration_scheduled": False,
                        "general_narration_status": "not_applicable",
                        "general_narration_reason": "unsupported_response_mode",
                        "general_narration_response_mode": "unknown",
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
                "deadline_monotonic": primary_deadline_monotonic,
            }
            if vision_image_payload:
                extra_context["vision_enabled"] = True
                extra_context["vision_image_base64"] = vision_image_payload
            tutor_context = await self._build_learning_context(
                LLM_OPERATION_CONCEPT_EXPLAIN,
                input_text=source_text,
                extra=extra_context,
            )
            primary_remaining = primary_deadline_monotonic - monotonic()
            if primary_remaining <= 0:
                raise asyncio.TimeoutError
            reply = await asyncio.wait_for(
                self._agent.concept_explain(
                    source_text,
                    mode=active_mode,
                    context=tutor_context,
                ),
                timeout=primary_remaining,
            )
            communication = getattr(self._cfg, "communication", None)
            narration_requested = bool(
                getattr(communication, "enabled", False)
            ) and bool(getattr(communication, "solution_narration_enabled", True))
            response_mode = (
                str(tutor_context.get("study_response_mode") or "unknown")
                .strip()
                .lower()
            )
            semantic_status = (
                str(tutor_context.get("study_semantic_status") or "").strip().lower()
            )
            current_question = tutor_context.get("current_question")
            trusted_internal_question_context = bool(
                isinstance(current_question, dict)
                and str(current_question.get("question") or "").strip()
                and source_text == str(current_question.get("question") or "").strip()
            )
            solution_contract_required = (
                response_mode == "problem_solving" or trusted_internal_question_context
            )
            solution_structure = (
                parse_solution_structure(reply.reply)
                if solution_contract_required
                else None
            )
            solution_candidate = bool(
                solution_structure
                and is_solution_structure_candidate(solution_structure)
            )
            truncated_problem_solution = bool(
                solution_contract_required
                and response_mode == "problem_solving"
                and reply.diagnostic == "output_truncated"
            )
            repair_attempted = False
            repair_invalid_response = False
            repair_time_budget_insufficient = False
            repair_eligible = bool(
                not reply.degraded
                and narration_requested
                and solution_structure is not None
                and not solution_structure.complete
                and (solution_candidate or truncated_problem_solution)
            )
            if not reply.degraded and narration_requested and solution_structure:
                if solution_structure is not None and solution_structure.complete:
                    if extract_solution_narration_sections(reply.reply) is None:
                        reply.reply = render_solution_structure(
                            solution_structure,
                            language=self._cfg.language,
                        )
                elif repair_eligible:
                    repair_started_monotonic = monotonic()
                    repair_deadline_monotonic = min(
                        started_monotonic
                        + _PRIMARY_EXPLAIN_TIMEOUT_SECONDS
                        + _SOLUTION_REPAIR_TIMEOUT_SECONDS,
                        repair_started_monotonic + _SOLUTION_REPAIR_TIMEOUT_SECONDS,
                    )
                    remaining = repair_deadline_monotonic - repair_started_monotonic
                    if remaining < _SOLUTION_REPAIR_MIN_REMAINING_SECONDS:
                        repair_time_budget_insufficient = True
                    else:
                        repair_attempted = True
                        repair_context = dict(tutor_context)
                        repair_context["deadline_monotonic"] = (
                            repair_deadline_monotonic
                        )
                        try:
                            repaired_structure = await asyncio.wait_for(
                                repair_solution_structure(
                                    self._agent,
                                    source_text=source_text,
                                    incomplete_reply=reply.reply,
                                    language=self._cfg.language,
                                    mode=active_mode,
                                    context=repair_context,
                                ),
                                timeout=min(
                                    remaining, _SOLUTION_REPAIR_TIMEOUT_SECONDS
                                ),
                            )
                        except asyncio.TimeoutError:
                            repaired_structure = None
                            repair_time_budget_insufficient = True
                        except Exception:
                            self.logger.warning(
                                "study explanation structure repair failed"
                            )
                            repaired_structure = None
                        if repaired_structure is not None and not isinstance(
                            repaired_structure, type(solution_structure)
                        ):
                            self.logger.warning(
                                "study explanation structure repair returned invalid data"
                            )
                            repaired_structure = None
                        if repaired_structure is None:
                            repair_finished_after_deadline = (
                                monotonic() >= repair_deadline_monotonic
                            )
                            repair_invalid_response = (
                                not repair_time_budget_insufficient
                                and not repair_finished_after_deadline
                            )
                            repair_time_budget_insufficient = (
                                repair_time_budget_insufficient
                                or repair_finished_after_deadline
                            )
                        else:
                            solution_structure = repaired_structure
                            solution_candidate = is_solution_structure_candidate(
                                repaired_structure
                            )
                            if repaired_structure.complete:
                                reply.reply = render_solution_structure(
                                    repaired_structure,
                                    language=self._cfg.language,
                                )
            finalize_progress = _TutorFinalizeProgress()
            try:
                payload = await asyncio.wait_for(
                    self._finalize_tutor_call(
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
                        finalize_progress=finalize_progress,
                    ),
                    timeout=_FINALIZE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self.logger.warning("study explanation history persistence timed out")
                payload = _build_finalize_failure_payload(
                    reply,
                    diagnostic="history_persist_timeout",
                    history_persisted=finalize_progress.history_persisted.is_set(),
                )
            except Exception:
                self.logger.warning("study explanation history persistence failed")
                payload = _build_finalize_failure_payload(
                    reply,
                    diagnostic="history_persist_failed",
                    history_persisted=finalize_progress.history_persisted.is_set(),
                )
            narration_scheduled = False
            narration_status = "disabled"
            narration_reason = ""
            live_solution_communication = getattr(self._cfg, "communication", None)
            live_solution_narration_requested = bool(
                getattr(live_solution_communication, "enabled", False)
            ) and bool(
                getattr(
                    live_solution_communication,
                    "solution_narration_enabled",
                    True,
                )
            )
            if not solution_contract_required:
                narration_status = "not_applicable"
            elif reply.degraded:
                narration_status = "degraded"
            elif not live_solution_narration_requested:
                narration_status = "disabled"
            elif not (solution_candidate or truncated_problem_solution):
                narration_status = "not_applicable"
            elif solution_structure is not None and not solution_structure.complete:
                if repair_time_budget_insufficient:
                    narration_status = "incomplete"
                    narration_reason = "insufficient_time_budget"
                else:
                    narration_status = "incomplete"
                    narration_reason = (
                        "invalid_repair_response"
                        if repair_invalid_response
                        else f"missing_{solution_structure.missing_sections[0]}"
                    )
            else:
                sections = extract_solution_narration_sections(
                    str(payload.get("reply") or "")
                )
                if sections is not None:
                    narration_scheduled = await self._emit_solution_completed_event(
                        sections,
                        target_lanlan=target_lanlan,
                    )
                if narration_scheduled:
                    narration_status = "scheduled"
                elif getattr(self, "_event_bus", None) is None:
                    narration_status = "runtime_unavailable"
                    narration_reason = "event_bus_unavailable"
                else:
                    narration_status = "delivery_failed"
                    narration_reason = "event_delivery_failed"
            payload["solution_narration_scheduled"] = narration_scheduled
            payload["solution_narration_status"] = narration_status
            payload["solution_narration_reason"] = narration_reason
            payload["solution_repair_attempted"] = repair_attempted
            payload["solution_narration_missing_sections"] = (
                list(solution_structure.missing_sections)
                if solution_contract_required and solution_structure is not None
                else []
            )
            general_narration_scheduled = False
            general_narration_status = "not_applicable"
            general_narration_reason = "unsupported_response_mode"
            routing_fallback_allowed = bool(
                response_mode == "unknown"
                and semantic_status == "routing_unavailable"
                and not solution_contract_required
            )
            general_response_mode = (
                response_mode
                if response_mode in {"general_explanation", "general_discussion"}
                else "general_fallback"
                if routing_fallback_allowed
                else "unknown"
            )
            general_mode_allowed = bool(
                general_response_mode != "unknown" and not solution_contract_required
            )
            live_general_communication = getattr(self._cfg, "communication", None)
            live_general_communication_enabled = bool(
                getattr(live_general_communication, "enabled", False)
            )
            general_narration_enabled = bool(
                getattr(live_general_communication, "general_narration_enabled", True)
            )
            prepared_general_content = (
                prepare_general_narration_content(str(payload.get("reply") or ""))
                if general_mode_allowed and not reply.degraded
                else ""
            )
            if general_mode_allowed:
                if reply.degraded:
                    general_narration_status = "degraded"
                    general_narration_reason = "degraded_reply"
                elif not live_general_communication_enabled:
                    general_narration_status = "disabled"
                    general_narration_reason = "communication_disabled"
                elif not general_narration_enabled:
                    general_narration_status = "disabled"
                    general_narration_reason = "general_narration_disabled"
                elif not prepared_general_content:
                    general_narration_status = "not_applicable"
                    general_narration_reason = "empty_reply"
                elif getattr(self, "_event_bus", None) is None:
                    general_narration_status = "runtime_unavailable"
                    general_narration_reason = "event_bus_unavailable"
                else:
                    general_narration_scheduled = (
                        await self._emit_general_response_completed_event(
                            response_mode=general_response_mode,
                            content=prepared_general_content,
                            target_lanlan=target_lanlan,
                        )
                    )
                    if general_narration_scheduled:
                        general_narration_status = "scheduled"
                        general_narration_reason = ""
                    else:
                        general_narration_status = "delivery_failed"
                        general_narration_reason = "event_delivery_failed"
            payload["general_narration_scheduled"] = general_narration_scheduled
            payload["general_narration_status"] = general_narration_status
            payload["general_narration_reason"] = general_narration_reason
            payload["general_narration_response_mode"] = general_response_mode
            payload["study_response_mode"] = response_mode
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
