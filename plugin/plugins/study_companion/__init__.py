from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    MODE_INTERACTIVE,
    MODE_TEACHING,
)
from .screen_classifier import classify_screen_from_ocr
from .models import (
    MODE_CONCEPT_EXPLAIN,
    STATUS_ERROR,
    STATUS_READY,
    STATUS_STOPPED,
    StudyConfig,
    StudyState,
    TutorReply,
    build_config,
    utc_now_iso,
)
from .service import (
    build_dependency_status,
    build_explain_payload,
    build_ocr_payload,
    build_status_payload,
    build_tutor_payload,
)
from .mode_manager import ModeManager, build_transition_phrase, handle_user_intent, normalize_mode
from .state import build_initial_state
from .store import StudyStore
from .study_ocr_pipeline import StudyOcrPipeline
from .tutor_llm_agent import TutorLLMAgent
from .tutor_llm_agent import diagnostic_code_for_exception
from .ui_api import build_open_ui_payload


@neko_plugin
class StudyCompanionPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.RLock()
        self._install_lock = threading.Lock()
        self._rapidocr_models_lock = threading.Lock()
        self._cfg = StudyConfig()
        self._state = build_initial_state(mode=MODE_COMPANION)
        self._store = StudyStore(
            self.data_path("study_companion.db"),
            self.config_dir / "data" / "study_seed.json",
            self.logger,
        )
        self._ocr_pipeline: StudyOcrPipeline | None = None
        self._agent: TutorLLMAgent | None = None
        self._mode_manager = ModeManager()

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            raw = await self.config.dump(timeout=5.0)
            self._cfg = build_config(raw if isinstance(raw, dict) else {})
            await asyncio.to_thread(self._store.open)
            self._cfg = await asyncio.to_thread(self._store.load_config, self._cfg)
            restored = await asyncio.to_thread(self._store.load_state, build_initial_state(mode=self._cfg.mode))
            with self._lock:
                self._state = restored
                self._state.status = STATUS_READY
                self._state.active_mode = normalize_mode(self._state.active_mode or self._cfg.mode)
                self._state.mode_started_at = float(self._state.mode_started_at or 0.0)
                self._state.mode_lock_until = float(self._state.mode_lock_until or 0.0)
                self._cfg.mode = self._state.active_mode
                self._state.last_started_at = utc_now_iso()
                self._state.last_error = ""
                self._mode_manager.restore(
                    {
                        "current_mode": self._state.active_mode,
                        "mode_started_at": self._state.mode_started_at,
                        "recent_mode_switches": self._state.recent_mode_switches,
                        "suggestion_cooldowns": self._state.suggestion_cooldowns,
                        "session_suggestions": self._state.session_suggestions,
                        "mode_lock_until": self._state.mode_lock_until,
                    }
            )
            self._ocr_pipeline = StudyOcrPipeline(logger=self.logger, config=self._cfg)
            self._agent = TutorLLMAgent(logger=self.logger, config=self._cfg)
            await asyncio.to_thread(self._refresh_dependency_status)
            self.register_static_ui("static")
            self.set_list_actions(
                [
                    {
                        "id": "open_ui",
                        "kind": "ui",
                        "target": f"/plugin/{self.plugin_id}/ui/",
                        "open_in": "new_tab",
                    }
                ]
            )
            await self._persist_state()
            status_payload = await asyncio.to_thread(self._status_payload)
            return Ok({"status": STATUS_READY, "result": status_payload})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("study plugin startup failed: {}", exc)
            await self._cleanup_after_failed_startup()
            with self._lock:
                self._state.status = STATUS_ERROR
                self._state.last_error = "startup_failed"
            return Err(SdkError("failed to start study_companion"))

    async def _cleanup_after_failed_startup(self) -> None:
        agent = self._agent
        self._agent = None
        self._ocr_pipeline = None
        try:
            self.clear_list_actions()
        except Exception as exc:
            self.logger.warning("study startup cleanup clear actions failed: {}", exc)
        try:
            self._static_ui_config = None
        except Exception as exc:
            self.logger.warning("study startup cleanup static UI failed: {}", exc)
        if agent is not None:
            try:
                await agent.shutdown()
            except Exception as exc:
                self.logger.warning("study startup cleanup agent shutdown failed: {}", exc)
        try:
            await asyncio.to_thread(self._store.close)
        except Exception as exc:
            self.logger.warning("study startup cleanup store close failed: {}", exc)

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._agent is not None:
            await self._agent.shutdown()
        with self._lock:
            self._state.status = STATUS_STOPPED
        await asyncio.to_thread(self._store.save_state, self._state)
        await asyncio.to_thread(self._store.close)
        return Ok({"status": STATUS_STOPPED})

    def _refresh_dependency_status(self) -> dict[str, Any]:
        status = build_dependency_status(self._cfg)
        with self._lock:
            self._state.dependency_status = status
        return status

    async def _persist_state(self) -> None:
        await asyncio.to_thread(self._store.save_config, self._cfg)
        await asyncio.to_thread(self._store.save_state, self._state)

    async def _apply_mode_switch(self, mode: str, reason: str, *, language: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._mode_manager.restore(
                {
                    "current_mode": self._state.active_mode,
                    "mode_started_at": self._state.mode_started_at,
                    "recent_mode_switches": self._state.recent_mode_switches,
                    "suggestion_cooldowns": self._state.suggestion_cooldowns,
                    "session_suggestions": self._state.session_suggestions,
                    "mode_lock_until": self._state.mode_lock_until,
                }
            )
            result = self._mode_manager.switch_to(mode, reason, language=language or self._cfg.language)
            checkpoint = result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {}
            self._state.active_mode = str(result.get("new_mode") or self._state.active_mode)
            self._state.mode_started_at = float(checkpoint.get("mode_started_at") or self._state.mode_started_at or 0.0)
            self._state.recent_mode_switches = checkpoint.get("recent_mode_switches") if isinstance(checkpoint.get("recent_mode_switches"), list) else self._state.recent_mode_switches
            self._state.suggestion_cooldowns = checkpoint.get("suggestion_cooldowns") if isinstance(checkpoint.get("suggestion_cooldowns"), dict) else self._state.suggestion_cooldowns
            self._state.session_suggestions = checkpoint.get("session_suggestions") if isinstance(checkpoint.get("session_suggestions"), list) else self._state.session_suggestions
            self._state.mode_lock_until = float(checkpoint.get("mode_lock_until") or self._state.mode_lock_until or 0.0)
            self._state.checkpoint = {
                **checkpoint,
                "changed": bool(result.get("changed")),
                "old_mode": result.get("old_mode"),
                "new_mode": result.get("new_mode"),
                "reason": result.get("reason"),
                "transition_phrase": result.get("transition_phrase"),
                "locked": bool(result.get("locked")),
                "lock_reason": result.get("lock_reason"),
                "lock_until": float(result.get("lock_until") or 0.0),
            }
            if result.get("changed"):
                self._cfg.mode = self._state.active_mode
        if result.get("changed") and self._agent is not None:
            self._agent.update_config(self._cfg)
        await self._persist_state()
        return result

    def _status_payload(self) -> dict[str, Any]:
        history = self._store.list_interactions(limit=10)
        return build_status_payload(config=self._cfg, state=self._state, history=history)

    def _state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def _merge_session_summary_seed(
        self,
        operation: str,
        *,
        payload: dict[str, Any] | None = None,
        seed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = dict(seed or {})
        payload = dict(payload or {})
        current["event_count"] = int(current.get("event_count") or 0) + 1
        current["last_operation"] = operation
        current["last_updated_at"] = utc_now_iso()
        screen_type = str(payload.get("screen_type") or current.get("last_screen_type") or "").strip()
        if screen_type:
            current["last_screen_type"] = screen_type
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            current["question_count"] = int(current.get("question_count") or 0) + 1
        elif operation == LLM_OPERATION_ANSWER_EVALUATE:
            current["answer_count"] = int(current.get("answer_count") or 0) + 1
            verdict = str(payload.get("verdict") or "").strip()
            if verdict:
                verdict_counts = dict(current.get("verdict_counts") or {})
                verdict_counts[verdict] = int(verdict_counts.get(verdict) or 0) + 1
                current["verdict_counts"] = verdict_counts
            weak_points = [item for item in payload.get("weak_points") or [] if str(item).strip()]
            if weak_points:
                current["weak_points"] = weak_points[:6]
        elif operation == LLM_OPERATION_CONCEPT_EXPLAIN:
            current["explain_count"] = int(current.get("explain_count") or 0) + 1
        elif operation == LLM_OPERATION_KNOWLEDGE_TRACK:
            current["track_count"] = int(current.get("track_count") or 0) + 1
        elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
            current["summary_count"] = int(current.get("summary_count") or 0) + 1
        topic = str(payload.get("topic") or "").strip()
        if topic:
            current["last_topic"] = topic
        weak_points = [item for item in payload.get("weak_points") or [] if str(item).strip()]
        if weak_points:
            current["weak_points"] = weak_points[:6]
        return current

    def _screen_classification_context(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state.last_screen_classification)

    def _update_screen_classification(self, text: str, *, window_title: str = "", update_empty: bool = True) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized and not update_empty:
            with self._lock:
                return dict(self._state.last_screen_classification)
        with self._lock:
            recent = list(self._state.recent_screen_classifications)
        classification = classify_screen_from_ocr(normalized, window_title=window_title, recent_classifications=recent)
        payload = classification.to_payload()
        with self._lock:
            if normalized or update_empty:
                self._state.last_screen_classification = payload
                recent_classifications = list(self._state.recent_screen_classifications)
                recent_classifications.append(payload)
                self._state.recent_screen_classifications = recent_classifications[-8:]
                self._state.session_summary_seed = self._merge_session_summary_seed(
                    "screen_classification",
                    payload=payload,
                    seed=self._state.session_summary_seed,
                )
        return payload

    async def _build_learning_context(
        self,
        operation: str,
        *,
        input_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self._state_snapshot()
        history_limit = max(5, min(12, int(self._cfg.history_limit or 10)))
        history = await asyncio.to_thread(self._store.list_interactions, history_limit)
        context = {
            "operation": operation,
            "input_text": input_text,
            "language": self._cfg.language,
            "mode": snapshot.get("active_mode") or self._cfg.mode,
            "screen_classification": snapshot.get("last_screen_classification") or {},
            "recent_screen_classifications": snapshot.get("recent_screen_classifications") or [],
            "current_question": snapshot.get("current_question") or {},
            "last_answer_evaluation": snapshot.get("last_answer_evaluation") or {},
            "session_summary_seed": snapshot.get("session_summary_seed") or {},
            "recent_learning_events": (snapshot.get("recent_learning_events") or [])[-8:],
            "last_ocr_text": snapshot.get("last_ocr_text") or "",
            "last_ocr_at": snapshot.get("last_ocr_at") or "",
            "history": history,
        }
        if extra:
            context.update(extra)
        return context

    def _record_tutor_result(self, operation: str, reply: TutorReply, *, extra: dict[str, Any] | None = None) -> None:
        payload = dict(reply.payload or {})
        summary = str(reply.reply or "").strip()
        event = {
            "operation": operation,
            "kind": operation,
            "input_text": reply.input_text,
            "summary": summary,
            "degraded": bool(reply.degraded),
            "diagnostic": reply.diagnostic,
            "at": time.time(),
            "created_at": reply.created_at or utc_now_iso(),
            "screen_type": str(payload.get("screen_type") or (extra or {}).get("screen_type") or self._screen_classification_context().get("screen_type") or ""),
        }
        with self._lock:
            seed = self._merge_session_summary_seed(operation, payload=payload, seed=self._state.session_summary_seed)
            self._state.session_summary_seed = seed
            self._state.recent_learning_events = (self._state.recent_learning_events + [event])[-16:]
            if operation != LLM_OPERATION_KNOWLEDGE_TRACK:
                self._state.last_reply = summary
                self._state.last_reply_at = reply.created_at or utc_now_iso()
                if operation == LLM_OPERATION_QUESTION_GENERATE:
                    if str(payload.get("question") or "").strip():
                        self._state.current_question = dict(payload)
                        self._state.last_question_at = reply.created_at or utc_now_iso()
                elif operation == LLM_OPERATION_ANSWER_EVALUATE:
                    self._state.last_answer_evaluation = dict(payload)
                    self._state.last_answer_evaluated_at = reply.created_at or utc_now_iso()
                elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
                    self._state.last_session_summary = str(payload.get("summary") or "").strip()
                    self._state.last_session_summary_at = reply.created_at or utc_now_iso()

    async def _finalize_tutor_call(
        self,
        operation: str,
        reply: TutorReply,
        *,
        history_kind: str,
        metadata: dict[str, Any],
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._record_tutor_result(operation, reply)
        diagnostic = str(reply.diagnostic or "")
        if diagnostic and reply.degraded:
            with self._lock:
                self._state.last_error = diagnostic
        await asyncio.to_thread(
            self._store.append_interaction,
            kind=history_kind,
            input_text=reply.input_text,
            output_text=reply.reply,
            metadata=metadata,
            history_limit=self._cfg.history_limit,
        )
        if operation != LLM_OPERATION_SUMMARIZE_SESSION:
            await self._track_learning(operation, reply, extra_context=extra_context)
        await self._persist_state()
        return build_tutor_payload(reply)

    async def _track_learning(
        self,
        operation: str,
        reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        if self._agent is None or not hasattr(self._agent, "knowledge_track"):
            return
        try:
            track_context = await self._build_learning_context(
                LLM_OPERATION_KNOWLEDGE_TRACK,
                input_text=reply.input_text,
                extra={
                    "operation": operation,
                    "result": reply.payload or {"reply": reply.reply},
                    "reply": reply.reply,
                    "degraded": reply.degraded,
                    "diagnostic": reply.diagnostic,
                    **(extra_context or {}),
                },
            )
            track_reply = await self._agent.knowledge_track(mode=self._state.active_mode, context=track_context)
        except Exception as exc:
            self.logger.warning("study knowledge track failed: {}", exc)
            track_reply = TutorReply(
                operation=LLM_OPERATION_KNOWLEDGE_TRACK,
                input_text=reply.input_text,
                reply="knowledge track updated",
                payload={
                    "topic": self._guess_track_topic(reply),
                    "mastery_delta": 0.0,
                    "confidence": 0.35,
                    "weak_points": [],
                    "next_steps": [],
                    "screen_type": self._screen_classification_context().get("screen_type") or "",
                },
                degraded=True,
                diagnostic=diagnostic_code_for_exception(exc),
                created_at=utc_now_iso(),
            )
        self._record_tutor_result(LLM_OPERATION_KNOWLEDGE_TRACK, track_reply)

    @staticmethod
    def _guess_track_topic(reply: TutorReply) -> str:
        payload = dict(reply.payload or {})
        topic = str(payload.get("topic") or "").strip()
        if topic:
            return topic
        text = str(reply.input_text or "").strip()
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return first_line[:48] or "general"

    def _resolve_current_run_id(self, extra_args: dict[str, Any] | None = None) -> str:
        current = str(getattr(self.ctx, "run_id", "") or "").strip()
        if current:
            return current
        if isinstance(extra_args, dict):
            ctx_obj = extra_args.get("_ctx")
            if isinstance(ctx_obj, dict):
                return str(ctx_obj.get("run_id") or "").strip()
        return ""

    def _resolve_install_progress_callback(self, current_run_id: str):
        async def _progress_update(event: dict[str, Any]) -> None:
            if not current_run_id:
                return
            try:
                await self.run_update(
                    run_id=current_run_id,
                    progress=float(event.get("progress") or 0.0),
                    stage=str(event.get("phase") or ""),
                    message=str(event.get("message") or ""),
                    metrics={
                        "phase": str(event.get("phase") or ""),
                        "downloaded_bytes": int(event.get("downloaded_bytes") or 0),
                        "total_bytes": int(event.get("total_bytes") or 0),
                        "resume_from": int(event.get("resume_from") or 0),
                        "asset_name": str(event.get("asset_name") or ""),
                        "release_name": str(event.get("release_name") or ""),
                    },
                )
            except Exception as exc:
                self.logger.warning("study install progress run_update failed: {}", exc)

        return _progress_update

    @plugin_entry(
        id="study_open_ui",
        name=tr("entries.open_ui.name", default="Open Study Companion UI"),
        description=tr("entries.open_ui.description", default="Return the static UI path for study_companion."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "path", "message_key"],
    )
    async def study_open_ui(self, **_):
        return Ok(build_open_ui_payload(plugin_id=self.plugin_id, available=self.get_static_ui_config() is not None))

    @plugin_entry(
        id="study_status",
        name=tr("entries.status.name", default="Study Companion Status"),
        description=tr("entries.status.description", default="Return runtime status, dependencies, and recent study interactions."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["status", "active_mode", "screen_classification", "current_question", "last_answer_evaluation"],
    )
    async def study_status(self, **_):
        payload = await asyncio.to_thread(self._status_payload)
        return Ok(payload)

    @plugin_entry(
        id="study_detect_mode_intent",
        name=tr("entries.detect_mode_intent.name", default="Detect Study Mode Intent"),
        description=tr("entries.detect_mode_intent.description", default="Detect whether a text snippet contains a study mode switch intent."),
        input_schema={"type": "object", "properties": {"text": {"type": "string", "default": ""}}},
        llm_result_fields=["mode", "pure_switch", "transition_phrase"],
    )
    async def study_detect_mode_intent(self, text: str = "", **_):
        return Ok(handle_user_intent(text, language=self._cfg.language))

    @plugin_entry(
        id="study_set_mode",
        name=tr("entries.set_mode.name", default="Set Study Mode"),
        description=tr("entries.set_mode.description", default="Switch the study companion between companion, interactive, and teaching modes."),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": [MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING]},
                "reason": {"type": "string", "default": "ui"},
            },
            "required": ["mode"],
        },
        llm_result_fields=["changed", "new_mode", "transition_phrase"],
    )
    async def study_set_mode(self, mode: str, reason: str = "ui", **_):
        try:
            result = await self._apply_mode_switch(mode, reason, language=self._cfg.language)
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        return Ok(result)

    @plugin_entry(
        id="study_dependency_status",
        name=tr("entries.dependency_status.name", default="Study OCR Dependency Status"),
        description=tr("entries.dependency_status.description", default="Inspect RapidOCR, Tesseract, and capture dependencies used by study_companion."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["missing_installable"],
    )
    async def study_dependency_status(self, **_):
        status = await asyncio.to_thread(self._refresh_dependency_status)
        await self._persist_state()
        return Ok(status)

    @plugin_entry(
        id="study_ocr_snapshot",
        name=tr("entries.ocr_snapshot.name", default="Study OCR Snapshot"),
        description=tr("entries.ocr_snapshot.description", default="Run a lightweight OCR snapshot. Phase 1 attempts fullscreen capture and returns diagnostics on failure."),
        input_schema={"type": "object", "properties": {}},
        timeout=45.0,
        llm_result_fields=["summary", "status", "diagnostic"],
    )
    async def study_ocr_snapshot(self, **_):
        if self._ocr_pipeline is None:
            return Err(SdkError("study OCR pipeline is not initialized"))
        snapshot = await asyncio.to_thread(self._ocr_pipeline.capture_snapshot)
        payload = build_ocr_payload(snapshot)
        if snapshot.text.strip():
            with self._lock:
                self._state.last_ocr_text = snapshot.text
                self._state.last_ocr_at = snapshot.captured_at
            payload["screen_classification"] = self._update_screen_classification(snapshot.text, update_empty=False)
        elif snapshot.status == "empty":
            payload["screen_classification"] = self._update_screen_classification("", update_empty=True)
        await self._persist_state()
        return Ok(payload)

    @plugin_entry(
        id="study_explain_text",
        name=tr("entries.explain_text.name", default="Explain Study Text"),
        description=tr("entries.explain_text.description", default="Explain a concept from supplied text, or use the latest OCR text if text is omitted."),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
            },
        },
        timeout=45.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_explain_text(self, text: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        raw_text = str(text or "").strip()
        # Phase 1: detect an explicit mode intent and switch first when present.
        intent = handle_user_intent(raw_text, language=self._cfg.language) if raw_text else {"matched": False, "pure_switch": False, "mode": "", "remaining_text": ""}
        with self._lock:
            active_mode = self._state.active_mode
        mode_switch: dict[str, Any] = {}
        if intent.get("matched") and intent.get("kind") == "mode_switch":
            try:
                mode_switch = await self._apply_mode_switch(str(intent.get("mode") or MODE_COMPANION), f"intent:{intent.get('keyword') or 'text'}", language=self._cfg.language)
                active_mode = str(mode_switch.get("new_mode") or active_mode)
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            if intent.get("pure_switch"):
                transition_phrase = str(mode_switch.get("transition_phrase") or intent.get("transition_phrase") or "")
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
        used_ocr_fallback = False
        if not source_text:
            with self._lock:
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        # Phase 3: explain with the active mode selected above.
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_CONCEPT_EXPLAIN,
            input_text=source_text,
            extra={
                "source": "ocr_snapshot" if used_ocr_fallback or not raw_text else "manual",
                "mode": active_mode,
                "mode_switch": bool(mode_switch.get("changed")),
                "source_text": source_text,
            },
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
                "screen_classification": tutor_context.get("screen_classification") or {},
            },
            extra_context=tutor_context,
        )
        if mode_switch:
            payload["mode_switch"] = mode_switch
        if intent.get("matched"):
            payload["intent"] = intent
            if intent.get("pure_switch"):
                payload["transition_phrase"] = str(mode_switch.get("transition_phrase") or intent.get("transition_phrase") or "")
        return Ok(payload)

    @plugin_entry(
        id="study_generate_question",
        name=tr("entries.generate_question.name", default="Generate Study Question"),
        description=tr("entries.generate_question.description", default="Generate one study question from supplied text or the latest OCR text."),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
                "topic": {"type": "string", "default": ""},
            },
        },
        timeout=60.0,
        llm_result_fields=["summary", "question", "answer", "hint", "difficulty", "topic"],
    )
    async def study_generate_question(self, text: str = "", topic: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        source_text = str(text or "").strip()
        used_ocr_fallback = False
        if not source_text:
            with self._lock:
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        with self._lock:
            active_mode = self._state.active_mode
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_QUESTION_GENERATE,
            input_text=source_text,
            extra={
                "source": "ocr_snapshot" if used_ocr_fallback or not text else "manual",
                "source_text": source_text,
                "topic_hint": str(topic or "").strip(),
                "mode": active_mode,
            },
        )
        reply = await self._agent.question_generate(source_text, mode=active_mode, context=tutor_context)
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_QUESTION_GENERATE,
            reply,
            history_kind=LLM_OPERATION_QUESTION_GENERATE,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "payload": reply.payload,
                "screen_classification": tutor_context.get("screen_classification") or {},
            },
            extra_context=tutor_context,
        )
        payload["screen_classification"] = tutor_context.get("screen_classification") or {}
        return Ok(payload)

    @plugin_entry(
        id="study_evaluate_answer",
        name=tr("entries.evaluate_answer.name", default="Evaluate Study Answer"),
        description=tr("entries.evaluate_answer.description", default="Evaluate an answer against the current generated question or a supplied question."),
        input_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string", "default": ""},
                "question": {"type": "string", "default": ""},
                "expected_answer": {"type": "string", "default": ""},
            },
        },
        timeout=60.0,
        llm_result_fields=["summary", "verdict", "score", "error_type", "feedback", "next_action"],
    )
    async def study_evaluate_answer(self, answer: str = "", question: str = "", expected_answer: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        with self._lock:
            current_question = dict(self._state.current_question)
            active_mode = self._state.active_mode
        supplied_question = str(question or "").strip()
        supplied_expected = str(expected_answer or "").strip()
        state_question = str(current_question.get("question") or "").strip()
        state_expected = str(current_question.get("answer") or "").strip()
        resolved_question = supplied_question or state_question
        if not resolved_question:
            return Err(SdkError("study tutor requires a question to evaluate against"))
        resolved_expected = supplied_expected
        if not resolved_expected and (not supplied_question or supplied_question == state_question):
            resolved_expected = state_expected
        answer_text = str(answer or "").strip()
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_ANSWER_EVALUATE,
            input_text=answer_text,
            extra={
                "question": resolved_question,
                "expected_answer": resolved_expected,
                "answer": answer_text,
                "current_question": current_question,
                "mode": active_mode,
            },
        )
        reply = await self._agent.answer_evaluate(
            question=resolved_question,
            answer=answer_text,
            expected_answer=resolved_expected,
            mode=active_mode,
            context=tutor_context,
        )
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_ANSWER_EVALUATE,
            reply,
            history_kind=LLM_OPERATION_ANSWER_EVALUATE,
            metadata={
                "question": resolved_question,
                "expected_answer": resolved_expected,
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "payload": reply.payload,
                "screen_classification": tutor_context.get("screen_classification") or {},
            },
            extra_context=tutor_context,
        )
        payload["question"] = resolved_question
        payload["screen_classification"] = tutor_context.get("screen_classification") or {}
        return Ok(payload)

    @plugin_entry(
        id="study_summarize_session",
        name=tr("entries.summarize_session.name", default="Summarize Study Session"),
        description=tr("entries.summarize_session.description", default="Summarize recent study interactions into compact study notes."),
        input_schema={"type": "object", "properties": {"focus": {"type": "string", "default": ""}}},
        timeout=75.0,
        llm_result_fields=["summary", "markdown", "highlights", "weak_points", "next_actions"],
    )
    async def study_summarize_session(self, focus: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        with self._lock:
            active_mode = self._state.active_mode
        history = await asyncio.to_thread(self._store.list_interactions, max(5, min(30, self._cfg.history_limit)))
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_SUMMARIZE_SESSION,
            input_text="session",
            extra={
                "focus": str(focus or "").strip(),
                "history": history,
                "mode": active_mode,
            },
        )
        reply = await self._agent.summarize_session(history, mode=active_mode, context=tutor_context)
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_SUMMARIZE_SESSION,
            reply,
            history_kind=LLM_OPERATION_SUMMARIZE_SESSION,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "payload": reply.payload,
                "screen_classification": tutor_context.get("screen_classification") or {},
            },
        )
        payload["screen_classification"] = tutor_context.get("screen_classification") or {}
        return Ok(payload)

    @plugin_entry(
        id="study_install_tesseract",
        name=tr("entries.install_tesseract.name", default="Install Tesseract for Study OCR"),
        description=tr("entries.install_tesseract.description", default="Install local Tesseract OCR for study_companion and refresh dependency status."),
        input_schema={"type": "object", "properties": {"force": {"type": "boolean", "default": False}}},
        timeout=300.0,
        llm_result_fields=["summary"],
    )
    async def study_install_tesseract(self, force: bool = False, **kwargs):
        if not self._install_lock.acquire(blocking=False):
            return Err(SdkError("Tesseract install is already running"))
        run_id = self._resolve_current_run_id(kwargs)
        try:
            from plugin.plugins.galgame_plugin.tesseract_support import install_tesseract

            result = await install_tesseract(
                logger=self.logger,
                configured_path=self._cfg.ocr_tesseract_path,
                install_target_dir_raw=self._cfg.ocr_install_target_dir,
                manifest_url=self._cfg.ocr_install_manifest_url,
                timeout_seconds=self._cfg.ocr_install_timeout_seconds,
                languages=self._cfg.ocr_languages,
                force=bool(force),
                task_id=run_id or None,
                plugin_id=self.plugin_id,
                progress_callback=self._resolve_install_progress_callback(run_id),
            )
            self._refresh_dependency_status()
            await self._persist_state()
            return Ok({"summary": str(result.get("summary") or "Tesseract is ready"), "install_result": result})
        except Exception as exc:
            return Err(SdkError(f"Tesseract install failed: {exc}"))
        finally:
            self._install_lock.release()

    @plugin_entry(
        id="study_download_rapidocr_models",
        name=tr("entries.download_rapidocr_models.name", default="Download RapidOCR Models for Study OCR"),
        description=tr("entries.download_rapidocr_models.description", default="Download missing RapidOCR model files for the configured study_companion OCR language."),
        input_schema={"type": "object", "properties": {"force": {"type": "boolean", "default": False}}},
        timeout=600.0,
        llm_result_fields=["summary"],
    )
    async def study_download_rapidocr_models(self, force: bool = False, **kwargs):
        if not self._rapidocr_models_lock.acquire(blocking=False):
            return Err(SdkError("RapidOCR model download is already running"))
        run_id = self._resolve_current_run_id(kwargs)
        try:
            from plugin.plugins.galgame_plugin.rapidocr_support import download_rapidocr_models

            result = await download_rapidocr_models(
                logger=self.logger,
                install_target_dir_raw=self._cfg.rapidocr_install_target_dir,
                ocr_version=self._cfg.rapidocr_ocr_version,
                lang_type=self._cfg.rapidocr_lang_type,
                timeout_seconds=float(self._cfg.ocr_install_timeout_seconds or 180.0),
                force=bool(force),
                task_id=run_id or None,
                plugin_id=self.plugin_id,
                progress_callback=self._resolve_install_progress_callback(run_id),
                before_completed_callback=lambda: None,
            )
            self._refresh_dependency_status()
            await self._persist_state()
            downloaded = result.get("downloaded") or []
            return Ok(
                {
                    "summary": (
                        f"RapidOCR models ready ({len(downloaded)} file(s) downloaded)"
                        if downloaded
                        else "RapidOCR models already present"
                    ),
                    "download_result": result,
                }
            )
        except Exception as exc:
            return Err(SdkError(f"RapidOCR model download failed: {exc}"))
        finally:
            self._rapidocr_models_lock.release()


StudyCompanionBridgePlugin = StudyCompanionPlugin
