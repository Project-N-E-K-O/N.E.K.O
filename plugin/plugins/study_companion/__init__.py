from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import math
from pathlib import Path
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
)

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
from .doc_exporter import DocExporter, normalize_format
from .checkin_manager import CheckinManager
from ._event_bus import StudyEvent, StudyEventBus
from .pomodoro_timer import PomodoroTimer
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
from .mode_manager import (
    ModeManager,
    build_transition_phrase,
    handle_user_intent,
    normalize_mode,
)
from .knowledge_contribution import PublicGraphContributionBuilder
from .knowledge_tracker import KnowledgeTracker
from .memory_deck_store import MemoryDeckStore, MemoryItemNotFoundError
from .memory_habit_bridge import MemoryHabitBridge
from .state import build_initial_state
from .store import StudyStore
from .study_habit_store import StudyHabitStore
from .study_ocr_pipeline import StudyOcrPipeline
from .supervision import SupervisionController
from .tutor_llm_agent import TutorLLMAgent
from .tutor_llm_agent import diagnostic_code_for_exception
from .ui_api import build_open_ui_payload
from .ui_api import build_contribution_settings_payload, build_knowledge_map_payload
from .ui_api import build_habit_dashboard_payload, build_pomodoro_status_payload


def _register_install_routes() -> None:
    from plugin.server.install_registry import (
        InstallKindRegistration,
        register_install_plugin,
    )

    register_install_plugin(
        "study_companion",
        install_kinds={
            "rapidocr_models": InstallKindRegistration(
                entry_id="study_download_rapidocr_models",
                label="RapidOCR Models",
                queued_message="RapidOCR model download queued",
            ),
            "tesseract": InstallKindRegistration(
                entry_id="study_install_tesseract",
                label="Tesseract",
                queued_message="Tesseract install queued",
            ),
        },
        ui_i18n_dir=Path(__file__).resolve().parent / "i18n",
        tutorial_enabled=True,
    )


try:
    _register_install_routes()
except Exception:  # noqa: BLE001 - route registration should not block package import.
    from plugin.logging_config import get_logger

    get_logger("study.install_routes").warning(
        "study install route registration failed",
        exc_info=True,
    )


def _validated_pomodoro_focus_minutes(
    config: StudyConfig, focus_minutes: Any | None
) -> int:
    default = int(config.pomodoro.focus_minutes or 25)
    if not config.pomodoro.allow_custom_duration or focus_minutes is None:
        return default
    try:
        parsed = int(focus_minutes)
    except (TypeError, ValueError):
        return default
    return parsed if 1 <= parsed <= 120 else default


_MASTERY_THRESHOLDS = (0.3, 0.5, 0.7, 0.85)
_REVIEW_DUE_INTERVAL_SECONDS = 1800.0


def _detect_mastery_threshold_crossed(before: float, after: float) -> str | None:
    for threshold in _MASTERY_THRESHOLDS:
        if (before < threshold <= after) or (before >= threshold > after):
            return str(threshold)
    return None


def _event_ratio(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    if not math.isfinite(number):
        number = default
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _event_nonnegative_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, number)


@neko_plugin
class StudyCompanionPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.RLock()
        self._install_in_progress = False
        self._rapidocr_models_in_progress = False
        self._cfg = StudyConfig()
        self._state = build_initial_state(mode=MODE_COMPANION)
        self._store = StudyStore(
            self.data_path("study_companion.db"),
            self.config_dir / "data" / "study_seed.json",
            self.logger,
            Path(__file__).resolve().parent / "static" / "knowledge_graph_seed.json",
        )
        self._ocr_pipeline: StudyOcrPipeline | None = None
        self._agent: TutorLLMAgent | None = None
        self._mode_manager = ModeManager()
        self._knowledge_tracker = KnowledgeTracker(
            self._store,
            retention_target=self._cfg.fsrs_retention_target,
            logger=self.logger,
        )
        self._memory_deck_store = MemoryDeckStore(
            self._store,
            retention_target=self._cfg.fsrs_retention_target,
        )
        self._knowledge_tracker.set_memory_deck_summary_provider(
            self._memory_deck_store.status_summary
        )
        self._habit_store: StudyHabitStore | None = None
        self._checkin_manager: CheckinManager | None = None
        self._pomodoro_timer: PomodoroTimer | None = None
        self._supervision: SupervisionController | None = None
        self._memory_habit_bridge: MemoryHabitBridge | None = None
        self._event_bus: StudyEventBus | None = None
        self._review_due_task: asyncio.Task[None] | None = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            raw = await self.config.dump(timeout=5.0)
            self._cfg = build_config(raw if isinstance(raw, dict) else {})
            await asyncio.to_thread(self._store.open)
            self._cfg = await asyncio.to_thread(self._store.load_config, self._cfg)
            self._knowledge_tracker = KnowledgeTracker(
                self._store,
                retention_target=self._cfg.fsrs_retention_target,
                logger=self.logger,
            )
            self._memory_deck_store = MemoryDeckStore(
                self._store,
                retention_target=self._cfg.fsrs_retention_target,
            )
            self._knowledge_tracker.set_memory_deck_summary_provider(
                self._memory_deck_store.status_summary
            )
            self._habit_store = StudyHabitStore(self._store)
            self._checkin_manager = CheckinManager(
                self._habit_store,
                makeup_window_days=self._cfg.checkin.makeup_window_days,
            )
            self._pomodoro_timer = PomodoroTimer(
                self._habit_store,
                config=self._cfg.pomodoro,
                auto_derive_from_session=self._cfg.checkin.auto_derive_from_session,
                checkin_timezone=self._cfg.checkin.streak_timezone,
            )
            self._supervision = SupervisionController(self._cfg.supervision)
            self._memory_habit_bridge = MemoryHabitBridge(
                store=self._store,
                memory=self._memory_deck_store,
                habits=self._habit_store,
                checkin_timezone=self._cfg.checkin.streak_timezone,
            )
            self._event_bus = (
                StudyEventBus(plugin_ctx=self.ctx)
                if self._cfg.communication.enabled
                else None
            )
            restored = await asyncio.to_thread(
                self._store.load_state, build_initial_state(mode=self._cfg.mode)
            )
            with self._lock:
                self._state = restored
                self._state.status = STATUS_READY
                self._state.active_mode = normalize_mode(
                    self._state.active_mode or self._cfg.mode
                )
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
            self._sync_doc_export_entry()
            await self._persist_state()
            self._start_review_due_task()
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
        await self._cancel_review_due_task()
        agent = self._agent
        self._agent = None
        self._ocr_pipeline = None
        self._knowledge_tracker = None
        self._memory_deck_store = None
        self._habit_store = None
        self._checkin_manager = None
        self._pomodoro_timer = None
        self._supervision = None
        self._memory_habit_bridge = None
        self._event_bus = None
        try:
            self.clear_list_actions()
        except Exception as exc:
            self.logger.warning("study startup cleanup clear actions failed: {}", exc)
        try:
            self.unregister_dynamic_entry("study_export_notes")
        except Exception as exc:
            self.logger.warning("study startup cleanup dynamic entry failed: {}", exc)
        try:
            self._static_ui_config = None
        except Exception as exc:
            self.logger.warning("study startup cleanup static UI failed: {}", exc)
        if agent is not None:
            try:
                await agent.shutdown()
            except Exception as exc:
                self.logger.warning(
                    "study startup cleanup agent shutdown failed: {}", exc
                )
        try:
            await asyncio.to_thread(self._store.close)
        except Exception as exc:
            self.logger.warning("study startup cleanup store close failed: {}", exc)

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self._cancel_review_due_task()
        try:
            self.unregister_dynamic_entry("study_export_notes")
        except Exception as exc:
            self.logger.warning("study shutdown dynamic entry cleanup failed: {}", exc)
        if self._agent is not None:
            await self._agent.shutdown()
        with self._lock:
            self._state.status = STATUS_STOPPED
        await asyncio.to_thread(self._store.save_state, self._state)
        await asyncio.to_thread(self._store.close)
        return Ok({"status": STATUS_STOPPED})

    def _start_review_due_task(self) -> None:
        if self._event_bus is None:
            return
        if self._review_due_task is not None and not self._review_due_task.done():
            return
        self._review_due_task = asyncio.create_task(self._run_review_due_loop())
        self._review_due_task.add_done_callback(self._on_review_due_task_done)

    async def _cancel_review_due_task(self) -> None:
        task = self._review_due_task
        self._review_due_task = None
        if task is None:
            return
        if task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.logger.warning("study review due task cleanup failed: {}", exc)
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.logger.warning("study review due task cleanup failed: {}", exc)

    def _on_review_due_task_done(self, task: asyncio.Task[None]) -> None:
        if self._review_due_task is task:
            self._review_due_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self.logger.warning("study review due task failed: {}", exc)

    async def _run_review_due_loop(self) -> None:
        while True:
            await self._emit_review_due_if_needed()
            await asyncio.sleep(max(0.0, _REVIEW_DUE_INTERVAL_SECONDS))

    def _refresh_dependency_status(self) -> dict[str, Any]:
        status = build_dependency_status(self._cfg)
        with self._lock:
            self._state.dependency_status = status
        return status

    async def _persist_state(self) -> None:
        await asyncio.to_thread(self._store.save_config, self._cfg)
        await asyncio.to_thread(self._store.save_state, self._state)

    async def _apply_mode_switch(
        self, mode: str, reason: str, *, language: str | None = None
    ) -> dict[str, Any]:
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
            result = self._mode_manager.switch_to(
                mode, reason, language=language or self._cfg.language
            )
            checkpoint = (
                result.get("checkpoint")
                if isinstance(result.get("checkpoint"), dict)
                else {}
            )
            self._state.active_mode = str(
                result.get("new_mode") or self._state.active_mode
            )
            if "mode_started_at" in checkpoint:
                self._state.mode_started_at = float(
                    checkpoint.get("mode_started_at") or 0.0
                )
            if isinstance(checkpoint.get("recent_mode_switches"), list):
                self._state.recent_mode_switches = checkpoint.get(
                    "recent_mode_switches"
                )
            if isinstance(checkpoint.get("suggestion_cooldowns"), dict):
                self._state.suggestion_cooldowns = checkpoint.get(
                    "suggestion_cooldowns"
                )
            if isinstance(checkpoint.get("session_suggestions"), list):
                self._state.session_suggestions = checkpoint.get(
                    "session_suggestions"
                )
            if "mode_lock_until" in checkpoint:
                self._state.mode_lock_until = float(
                    checkpoint.get("mode_lock_until") or 0.0
                )
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
        is_first_run = not bool(self._store.list_interactions(limit=1))
        today = self._today()
        habit_payload = self._habit_status_payload(today)
        knowledge = {
            "knowledge_summary": self._knowledge_tracker.get_status_summary(limit=8),
            "knowledge_quality_summary": self._knowledge_tracker.quality.status_summary(
                limit=8
            ),
            "anonymous_knowledge_stats_summary": self._store.anonymous_knowledge_stats_summary(),
            "review_queue": self._knowledge_tracker.get_review_queue(limit=8),
            "memory_deck": self._memory_deck_store.status_summary(limit=8),
            "weak_topics": self._knowledge_tracker.get_weak_topics(limit=8),
            "mastery_overview": self._store.list_mastery_overview(limit=8),
        }
        return build_status_payload(
            config=self._cfg,
            state=self._state,
            history=history,
            knowledge={**knowledge, "habit": habit_payload},
            is_first_run=is_first_run,
        )

    def _habit_status_payload(self, today: str) -> dict[str, Any]:
        if (
            self._habit_store is None
            or self._checkin_manager is None
            or self._pomodoro_timer is None
        ):
            return {
                "available": False,
                "error": "study habit system is not initialized",
            }
        try:
            payload = build_habit_dashboard_payload(
                goals=self._habit_store.list_goals(date=today),
                checkin=self._checkin_manager.checkin_status(date=today, today=today),
                pomodoro=self._pomodoro_timer.status(),
                summary=self._checkin_manager.daily_summary(date=today),
                supervision=self._supervision.status()
                if self._supervision is not None
                else {},
            )
            if self._memory_habit_bridge is not None:
                payload["summary"]["memory_summary"] = (
                    self._memory_habit_bridge.memory_summary(date=today)
                )
            payload["available"] = True
            return payload
        except Exception as exc:
            self.logger.warning("study habit status payload degraded: {}", exc)
            return {"available": False, "error": str(exc)}

    def _today(self) -> str:
        timezone_name = str(self._cfg.checkin.streak_timezone or "local").strip()
        if timezone_name and timezone_name.lower() != "local":
            try:
                return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
            except ZoneInfoNotFoundError:
                self.logger.warning("invalid study checkin timezone: {}", timezone_name)
        return datetime.now().astimezone().date().isoformat()

    def _sync_doc_export_entry(self) -> None:
        self.unregister_dynamic_entry("study_export_notes")
        if not bool(self._cfg.doc_export.enabled):
            return
        export_formats = ["markdown", "pdf", "docx"]
        if bool(self._cfg.doc_export.xmind_enabled):
            export_formats.append("xmind")
        export_format_names = "Markdown, PDF, DOCX"
        if bool(self._cfg.doc_export.xmind_enabled):
            export_format_names = f"{export_format_names}, or XMind"
        self.register_dynamic_entry(
            "study_export_notes",
            self._study_export_notes_entry,
            name="Export Study Notes",
            description=f"Export recent study notes as {export_format_names}.",
            input_schema={
                "type": "object",
                "properties": {
                    "fmt": {
                        "type": "string",
                        "enum": export_formats,
                        "default": "markdown",
                    },
                    "style": {
                        "type": "string",
                        "enum": ["neko", "academic", "compact"],
                        "default": self._cfg.doc_export.default_style,
                    },
                    "title": {"type": "string", "default": "Study Notes"},
                    "preview_only": {"type": "boolean", "default": False},
                    "time_range": {"type": "string", "default": "recent"},
                    "recent_limit": {"type": "integer", "default": 30},
                    "topic_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
            },
            timeout=75.0,
            llm_result_fields=[
                "filename",
                "content_type",
                "format",
                "style",
                "markdown",
            ],
        )

    async def _study_export_notes_entry(
        self,
        fmt: str = "markdown",
        style: str | None = None,
        title: str | None = "Study Notes",
        preview_only: bool = False,
        time_range: str | None = "recent",
        recent_limit: int | None = 30,
        topic_ids: list[str] | None = [],
        **_,
    ):
        try:
            if not bool(self._cfg.doc_export.enabled):
                return Err(
                    SdkError("study note export is disabled by doc_export.enabled")
                )
            normalize_format(fmt)
            exporter = DocExporter(self._store, config=self._cfg.doc_export)
            exported = await asyncio.to_thread(
                exporter.export,
                fmt=fmt,
                style=style,
                title=title,
                preview_only=bool(preview_only),
                time_range=time_range,
                recent_limit=recent_limit,
                topic_ids=topic_ids if isinstance(topic_ids, list) else [],
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok(
            {
                "content_base64": base64.b64encode(exported.content).decode("ascii"),
                "filename": exported.filename,
                "content_type": exported.content_type,
                "markdown": exported.markdown,
                "format": exported.format,
                "style": exported.style,
            }
        )

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
        screen_type = str(
            payload.get("screen_type") or current.get("last_screen_type") or ""
        ).strip()
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
            weak_points = [
                item for item in payload.get("weak_points") or [] if str(item).strip()
            ]
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
        weak_points = [
            item for item in payload.get("weak_points") or [] if str(item).strip()
        ]
        if weak_points:
            current["weak_points"] = weak_points[:6]
        return current

    def _screen_classification_context(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state.last_screen_classification)

    def _update_screen_classification(
        self, text: str, *, window_title: str = "", update_empty: bool = True
    ) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized and not update_empty:
            with self._lock:
                return dict(self._state.last_screen_classification)
        with self._lock:
            recent = list(self._state.recent_screen_classifications)
            previous = dict(self._state.last_screen_classification)
        classification = classify_screen_from_ocr(
            normalized, window_title=window_title, recent_classifications=recent
        )
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
        previous_type = str(previous.get("screen_type") or "").strip()
        new_type = str(payload.get("screen_type") or "").strip()
        if (
            self._event_bus is not None
            and self._event_bus.should_schedule_screen_context(new_type, previous_type)
        ):
            self._event_bus.schedule_emit(
                StudyEvent(
                    name="screen_context_changed",
                    payload={
                        "screen_type": new_type,
                        "confidence": payload.get("confidence", 0.0),
                        "ocr_summary": normalized[:200],
                        "previous_type": previous_type,
                    },
                )
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
            "recent_screen_classifications": snapshot.get(
                "recent_screen_classifications"
            )
            or [],
            "current_question": snapshot.get("current_question") or {},
            "last_answer_evaluation": snapshot.get("last_answer_evaluation") or {},
            "session_summary_seed": snapshot.get("session_summary_seed") or {},
            "recent_learning_events": (snapshot.get("recent_learning_events") or [])[
                -8:
            ],
            "last_ocr_text": snapshot.get("last_ocr_text") or "",
            "last_ocr_at": snapshot.get("last_ocr_at") or "",
            "history": history,
        }
        if operation == LLM_OPERATION_QUESTION_GENERATE:
            hint = ""
            if extra:
                hint = str(extra.get("topic_hint") or extra.get("topic") or "").strip()
            context["knowledge_question_params"] = await asyncio.to_thread(
                self._knowledge_tracker.get_next_question_params,
                hint,
            )
        elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
            context["knowledge_session_summary"] = await asyncio.to_thread(
                self._knowledge_tracker.get_session_summary
            )
        else:
            context["knowledge_summary"] = await asyncio.to_thread(
                self._knowledge_tracker.get_status_summary,
                limit=5,
            )
        if extra:
            context.update(extra)
        return context

    def _record_tutor_result(
        self, operation: str, reply: TutorReply, *, extra: dict[str, Any] | None = None
    ) -> None:
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
            "screen_type": str(
                payload.get("screen_type")
                or (extra or {}).get("screen_type")
                or self._screen_classification_context().get("screen_type")
                or ""
            ),
        }
        with self._lock:
            seed = self._merge_session_summary_seed(
                operation, payload=payload, seed=self._state.session_summary_seed
            )
            self._state.session_summary_seed = seed
            self._state.recent_learning_events = (
                self._state.recent_learning_events + [event]
            )[-16:]
            if operation != LLM_OPERATION_KNOWLEDGE_TRACK:
                self._state.last_reply = summary
                self._state.last_reply_at = reply.created_at or utc_now_iso()
                if operation == LLM_OPERATION_QUESTION_GENERATE:
                    if str(payload.get("question") or "").strip():
                        self._state.current_question = dict(payload)
                        self._state.last_question_at = reply.created_at or utc_now_iso()
                elif operation == LLM_OPERATION_ANSWER_EVALUATE:
                    self._state.last_answer_evaluation = dict(payload)
                    self._state.last_answer_evaluated_at = (
                        reply.created_at or utc_now_iso()
                    )
                elif operation == LLM_OPERATION_SUMMARIZE_SESSION:
                    self._state.last_session_summary = str(
                        payload.get("summary") or ""
                    ).strip()
                    self._state.last_session_summary_at = (
                        reply.created_at or utc_now_iso()
                    )

    async def _finalize_tutor_call(
        self,
        operation: str,
        reply: TutorReply,
        *,
        history_kind: str,
        metadata: dict[str, Any],
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._record_tutor_result(operation, reply, extra=extra_context)
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
            track_reply = await self._agent.knowledge_track(
                mode=self._state.active_mode, context=track_context
            )
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
                    "screen_type": self._screen_classification_context().get(
                        "screen_type"
                    )
                    or "",
                },
                degraded=True,
                diagnostic=diagnostic_code_for_exception(exc),
                created_at=utc_now_iso(),
            )
        self._record_tutor_result(LLM_OPERATION_KNOWLEDGE_TRACK, track_reply)
        if operation == LLM_OPERATION_ANSWER_EVALUATE:
            await self._record_answer_knowledge(
                reply, track_reply, extra_context=extra_context
            )

    async def _record_answer_knowledge(
        self,
        eval_reply: TutorReply,
        track_reply: TutorReply,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        context = dict(extra_context or {})
        track_payload = dict(track_reply.payload or {})
        eval_payload = dict(eval_reply.payload or {})
        current_question = dict(context.get("current_question") or {})
        question_payload = dict(context.get("question_payload") or current_question)
        question_text = str(
            context.get("question")
            or question_payload.get("question")
            or current_question.get("question")
            or ""
        ).strip()
        question_payload["question"] = question_text
        question_payload["answer"] = str(
            context.get("expected_answer")
            or question_payload.get("answer")
            or current_question.get("answer")
            or ""
        )
        topic = str(
            question_payload.get("topic")
            or track_payload.get("topic")
            or eval_payload.get("topic")
            or self._guess_track_topic(track_reply)
        ).strip()
        if topic:
            question_payload.setdefault("topic", topic)
        eval_result = {
            **eval_payload,
            "topic": topic,
            "track": track_payload,
        }
        session_id = (
            str(
                context.get("session_id")
                or context.get("run_id")
                or getattr(self._state, "run_id", "")
                or getattr(self.ctx, "run_id", "")
                or "default"
            ).strip()
            or "default"
        )
        mastery_before: float | None = 0.0
        if topic:
            try:
                mastery_before = await asyncio.to_thread(
                    self._knowledge_tracker.get_mastery, topic
                )
            except Exception as exc:
                self.logger.warning(
                    "study knowledge tracker mastery-before read failed: {}", exc
                )
                mastery_before = None
        try:
            tracking_result = await asyncio.to_thread(
                self._knowledge_tracker.on_answer,
                topic_id=topic,
                question=question_payload,
                user_answer=str(context.get("answer") or eval_reply.input_text or ""),
                eval_result=eval_result,
                mode=str(context.get("mode") or self._state.active_mode),
                session_id=session_id,
            )
        except Exception as exc:
            self.logger.warning("study knowledge tracker persistence failed: {}", exc)
            return
        tracked_topic = str(tracking_result.get("topic_id") or topic).strip()
        mastery_after: float | None = None
        if tracked_topic:
            try:
                mastery_after = await asyncio.to_thread(
                    self._knowledge_tracker.get_mastery, tracked_topic
                )
            except Exception as exc:
                self.logger.warning(
                    "study knowledge tracker mastery-after read failed: {}", exc
                )
        crossed = (
            _detect_mastery_threshold_crossed(mastery_before, mastery_after)
            if mastery_before is not None and mastery_after is not None
            else None
        )
        if (
            self._event_bus is not None
            and crossed is not None
            and mastery_before is not None
            and mastery_after is not None
        ):
            self._event_bus.schedule_emit(
                StudyEvent(
                    name="mastery_updated",
                    payload={
                        "topic": tracked_topic,
                        "mastery": mastery_after,
                        "mastery_before": mastery_before,
                        "direction": "up"
                        if mastery_after > mastery_before
                        else "down",
                        "crossed_threshold": crossed,
                        "evidence_count": 1,
                    },
                )
            )

    @staticmethod
    def _guess_track_topic(reply: TutorReply) -> str:
        payload = dict(reply.payload or {})
        topic = str(payload.get("topic") or "").strip()
        if topic:
            return topic
        text = str(reply.input_text or "").strip()
        first_line = next(
            (line.strip() for line in text.splitlines() if line.strip()), ""
        )
        return first_line[:48] or "general"

    def _resolve_current_run_id(self, extra_args: dict[str, Any] | None = None) -> str:
        if isinstance(extra_args, dict):
            direct = str(extra_args.get("run_id") or "").strip()
            if direct:
                return direct
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
        description=tr(
            "entries.open_ui.description",
            default="Return the static UI path for study_companion.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "path", "message_key"],
    )
    async def study_open_ui(self, **_):
        return Ok(
            build_open_ui_payload(
                plugin_id=self.plugin_id,
                available=self.get_static_ui_config() is not None,
            )
        )

    @plugin_entry(
        id="study_status",
        name=tr("entries.status.name", default="Study Companion Status"),
        description=tr(
            "entries.status.description",
            default="Return runtime status, dependencies, and recent study interactions.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=[
            "status",
            "active_mode",
            "screen_classification",
            "current_question",
            "last_answer_evaluation",
        ],
    )
    async def study_status(self, **_):
        payload = await asyncio.to_thread(self._status_payload)
        return Ok(payload)

    def _require_event_bus(self) -> StudyEventBus:
        if self._event_bus is None:
            raise RuntimeError(
                "Neko communication is not enabled (communication.enabled=false)"
            )
        return self._event_bus

    async def _emit_review_due_if_needed(self) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            payload = await asyncio.to_thread(self._build_review_due_payload)
            if not payload:
                return
            bus.schedule_emit(StudyEvent(name="review_due", payload=payload))
        except Exception as exc:
            self.logger.warning("study review due event emit failed: {}", exc)

    def _build_review_due_payload(self) -> dict[str, Any]:
        memory_due_count = int(self._memory_deck_store.count_due_reviews() or 0)
        topic_due_count = int(self._knowledge_tracker.count_due_reviews() or 0)
        due_count = memory_due_count + topic_due_count
        if due_count <= 0:
            return {}
        memory_reviews = self._memory_deck_store.due_reviews(limit=50)
        topic_reviews = self._knowledge_tracker.get_review_queue(limit=50)
        urgent_count = self._count_urgent_due(memory_reviews) + self._count_urgent_due(
            topic_reviews
        )
        topics = self._get_due_topics(memory_reviews, topic_reviews)
        return {
            "due_count": due_count,
            "urgent_count": urgent_count,
            "topics": topics,
            "suggestion": (
                f"Suggested review time: "
                f"{max(5, due_count * 2)} minutes for "
                f"{due_count} card(s)."
            ),
        }

    @staticmethod
    def _count_urgent_due(reviews: list[dict[str, Any]]) -> int:
        return sum(
            1 for item in reviews if float(item.get("overdue_days") or 0.0) > 0
        )

    def _get_due_topics(
        self,
        memory_reviews: list[dict[str, Any]] | None = None,
        topic_reviews: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        topics: list[str] = []
        memory_items = (
            memory_reviews
            if memory_reviews is not None
            else self._memory_deck_store.due_reviews(limit=50)
        )
        for item in memory_items:
            deck = item.get("deck") or {}
            topic = str(deck.get("name") or item.get("topic_id") or "").strip()
            if topic and topic not in topics:
                topics.append(topic)
            if len(topics) >= 5:
                return topics
        topic_items = (
            topic_reviews
            if topic_reviews is not None
            else self._knowledge_tracker.get_review_queue(limit=50)
        )
        for item in topic_items:
            topic_payload = (
                item.get("topic") if isinstance(item.get("topic"), dict) else {}
            )
            topic = str(
                topic_payload.get("name")
                or topic_payload.get("id")
                or item.get("topic_id")
                or ""
            ).strip()
            if topic and topic not in topics:
                topics.append(topic)
            if len(topics) >= 5:
                return topics
        return topics

    async def _emit_answer_evaluated_event(
        self,
        *,
        verdict: str,
        score: Any,
        question_summary: str,
        user_answer_summary: str,
        correction_hint: str = "",
        topic: str = "",
        mastery_before: float = -1.0,
        mastery_after: float = -1.0,
    ) -> None:
        bus = self._event_bus
        if bus is None:
            return
        bus.schedule_emit(
            StudyEvent(
                name="answer_evaluated",
                payload={
                    "verdict": str(verdict or "").strip(),
                    "score": _event_ratio(score),
                    "question_summary": str(question_summary or "").strip()[:200],
                    "user_answer_summary": str(user_answer_summary or "").strip()[
                        :200
                    ],
                    "correction_hint": str(correction_hint or "").strip()[:200],
                    "topic": str(topic or "").strip(),
                    "mastery_before": mastery_before,
                    "mastery_after": mastery_after,
                },
            )
        )

    async def _emit_memory_review_answer_event(
        self, payload: dict[str, Any]
    ) -> None:
        item = payload.get("item") or {}
        review = payload.get("review_record") or {}
        deck = await asyncio.to_thread(
            self._memory_deck_store.get_deck, str(item.get("deck_id") or "")
        ) or {}
        correct = bool(review.get("correct"))
        rating = payload.get("rating") or review.get("rating") or ""
        await self._emit_answer_evaluated_event(
            verdict="correct" if correct else "incorrect",
            score=1.0 if correct else 0.0,
            question_summary=str(item.get("prompt") or item.get("front") or ""),
            user_answer_summary=f"rating={rating}",
            correction_hint=str(review.get("error_type") or ""),
            topic=str(deck.get("subject") or deck.get("name") or ""),
        )

    async def _emit_recitation_answer_event(
        self, payload: dict[str, Any]
    ) -> None:
        diff_data = payload.get("diff") or {}
        review = payload.get("review") or {}
        item = review.get("item") or {}
        deck = await asyncio.to_thread(
            self._memory_deck_store.get_deck, str(item.get("deck_id") or "")
        ) or {}
        score = _event_ratio(diff_data.get("score"))
        if score >= 0.8:
            verdict = "correct"
        elif score >= 0.5:
            verdict = "partial"
        else:
            verdict = "incorrect"
        attempt = payload.get("attempt") or {}
        await self._emit_answer_evaluated_event(
            verdict=verdict,
            score=score,
            question_summary=str(item.get("prompt") or item.get("front") or ""),
            user_answer_summary=str(attempt.get("user_input_text") or ""),
            correction_hint=(
                f"Missing: {diff_data.get('missing_count', 0)}, "
                f"extra: {diff_data.get('extra_count', 0)}"
            ),
            topic=str(deck.get("subject") or deck.get("name") or ""),
        )

    async def _emit_session_summarized_event(
        self, payload: dict[str, Any]
    ) -> None:
        bus = self._event_bus
        if bus is None:
            return
        with self._lock:
            seed = dict(self._state.session_summary_seed)
        answer_count = int(seed.get("answer_count") or 0)
        verdict_counts = dict(seed.get("verdict_counts") or {})
        correct = int(verdict_counts.get("correct") or 0)
        fallback_correct_rate = (correct / answer_count) if answer_count > 0 else 0.0
        bus.schedule_emit(
            StudyEvent(
                name="session_summarized",
                payload={
                    "duration_minutes": _event_nonnegative_float(
                        payload.get("duration_minutes"), 0.0
                    ),
                    "questions_attempted": int(
                        _event_nonnegative_float(
                            payload.get("questions_attempted"), float(answer_count)
                        )
                    ),
                    "correct_rate": _event_ratio(
                        payload.get("correct_rate", fallback_correct_rate)
                    ),
                    "topics_studied": [seed.get("last_topic")]
                    if seed.get("last_topic")
                    else [],
                    "key_insight": str(
                        payload.get("key_insight")
                        or payload.get("summary")
                        or payload.get("reply")
                        or ""
                    ).strip()[:240],
                },
            )
        )

    @plugin_entry(
        id="study_neko_communication_status",
        name=tr(
            "entries.neko_communication_status.name",
            default="Neko Communication Status",
        ),
        description=tr(
            "entries.neko_communication_status.description",
            default="Return whether real-time neko communication is active.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "events_emitted", "events_blocked"],
    )
    async def study_neko_communication_status(self, **_):
        bus = self._event_bus
        return Ok(
            {
                "available": bus is not None,
                "events_emitted": bus.emit_count if bus is not None else 0,
                "events_blocked": bus.block_count if bus is not None else 0,
            }
        )

    def _require_habit_components(
        self,
    ) -> tuple[StudyHabitStore, CheckinManager, PomodoroTimer, SupervisionController]:
        if (
            self._habit_store is None
            or self._checkin_manager is None
            or self._pomodoro_timer is None
            or self._supervision is None
        ):
            raise RuntimeError("study habit system is not initialized")
        return (
            self._habit_store,
            self._checkin_manager,
            self._pomodoro_timer,
            self._supervision,
        )

    def _require_memory_habit_bridge(self) -> MemoryHabitBridge:
        if self._memory_habit_bridge is None:
            raise RuntimeError("memory habit bridge is not initialized")
        return self._memory_habit_bridge

    @plugin_entry(
        id="study_memory_habit_status",
        name=tr("entries.memory_habit_status.name", default="Memory Habit Bridge Status"),
        description=tr(
            "entries.memory_habit_status.description",
            default="Return whether memory deck habit integration is available.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "supports_deck_goals", "supports_deck_focus"],
    )
    async def study_memory_habit_status(self, **_):
        try:
            self._require_habit_components()
            return Ok(self._require_memory_habit_bridge().status())
        except Exception as exc:
            return Ok({"available": False, "error": str(exc)})

    @plugin_entry(
        id="study_pomodoro_status",
        name="Study Pomodoro Status",
        description="Return the current Study Companion pomodoro timer status.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "mode", "remaining_seconds", "session_count"],
    )
    async def study_pomodoro_status(self, **_):
        try:
            _, _, timer, supervision = self._require_habit_components()
            before_status = await asyncio.to_thread(timer.status)
            before_state = str(before_status.get("state") or "")
            status = await asyncio.to_thread(timer.tick)
            after_state = str(status.get("state") or "")
            reminder: dict[str, Any] = {}
            if before_state == "focusing" and after_state in {
                "short_break",
                "long_break",
                "completed",
            }:
                supervision.on_focus_end()
            elif after_state == "focusing":
                reminder = supervision.due_reminder()
            payload = build_pomodoro_status_payload(status)
            if reminder:
                payload["supervision_reminder"] = reminder
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_pomodoro_start",
        name="Start Study Pomodoro",
        description=(
            "Start a focus pomodoro. goal_id is used as-is when provided; "
            "deck_id resolves a memory deck minutes goal only when goal_id is empty."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "focus_minutes": {
                    "type": "integer",
                    "description": "Focus duration in minutes.",
                },
                "goal_id": {
                    "type": "string",
                    "default": "",
                    "description": "Existing daily goal id. Takes precedence over deck_id.",
                },
                "deck_id": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "Memory deck id used to create or reuse a minutes goal when "
                        "goal_id is empty."
                    ),
                },
            },
        },
        llm_result_fields=["state", "remaining_seconds", "goal_id"],
    )
    async def study_pomodoro_start(
        self,
        focus_minutes: int | None = None,
        goal_id: str = "",
        deck_id: str = "",
        **_,
    ):
        try:
            habits, _, timer, supervision = self._require_habit_components()
            planned_focus_minutes = _validated_pomodoro_focus_minutes(
                self._cfg, focus_minutes
            )
            before_status = await asyncio.to_thread(timer.status)
            before_session_id = str(
                before_status.get("current_focus_session", {}).get("id") or ""
            )
            before_state = str(before_status.get("state") or "")
            if (
                deck_id
                and not goal_id
                and before_state not in {"focusing", "paused", "short_break", "long_break"}
            ):
                bridge = self._require_memory_habit_bridge()
                goal_payload = await asyncio.to_thread(
                    bridge.resolve_focus_goal,
                    date=self._today(),
                    deck_id=deck_id,
                    focus_minutes=float(planned_focus_minutes),
                )
                goal_id = str((goal_payload.get("goal") or {}).get("id") or "")
            status = await asyncio.to_thread(
                timer.start, goal_id=goal_id, focus_minutes=planned_focus_minutes
            )
            after_session_id = str(
                status.get("current_focus_session", {}).get("id") or ""
            )
            if (
                str(status.get("state") or "") == "focusing"
                and after_session_id
                and after_session_id != before_session_id
            ):
                goal = (
                    await asyncio.to_thread(habits.get_goal, str(goal_id or ""))
                    if goal_id
                    else {}
                )
                supervision.on_focus_start(
                    goal=goal or {},
                    planned_minutes=float(
                        status.get("config", {}).get("focus_minutes") or focus_minutes or 0
                    ),
                )
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_pomodoro_pause",
        name="Pause Study Pomodoro",
        description="Pause the active focus pomodoro.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "remaining_seconds"],
    )
    async def study_pomodoro_pause(self, **_):
        try:
            _, _, timer, _ = self._require_habit_components()
            return Ok(build_pomodoro_status_payload(timer.pause()))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_pomodoro_resume",
        name="Resume Study Pomodoro",
        description="Resume a paused focus pomodoro.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "remaining_seconds"],
    )
    async def study_pomodoro_resume(self, **_):
        try:
            _, _, timer, _ = self._require_habit_components()
            return Ok(build_pomodoro_status_payload(timer.resume()))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_pomodoro_stop",
        name="Stop Study Pomodoro",
        description="Stop the active focus or break timer.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "current_focus_session"],
    )
    async def study_pomodoro_stop(self, **_):
        try:
            _, _, timer, supervision = self._require_habit_components()
            status = await asyncio.to_thread(timer.stop)
            supervision.on_focus_end()
            return Ok(build_pomodoro_status_payload(status))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_pomodoro_skip_break",
        name="Skip Study Pomodoro Break",
        description="Skip the current short or long break when allowed.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["state", "remaining_seconds"],
    )
    async def study_pomodoro_skip_break(self, **_):
        try:
            _, _, timer, _ = self._require_habit_components()
            return Ok(build_pomodoro_status_payload(timer.skip_break()))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_goals",
        name="Study Daily Goals",
        description="Return daily study habit goals for a date.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "default": ""}},
        },
        llm_result_fields=["goals"],
    )
    async def study_goals(self, date: str = "", **_):
        try:
            habits, _, _, _ = self._require_habit_components()
            target_date = str(date or self._today())[:10]
            return Ok(
                {
                    "date": target_date,
                    "goals": await asyncio.to_thread(
                        habits.list_goals, date=target_date
                    ),
                }
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_goal_create",
        name="Create Study Daily Goal",
        description="Create a local daily study goal.",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "default": ""},
                "target_type": {"type": "string", "default": "custom"},
                "target_id": {"type": "string", "default": ""},
                "subject": {"type": "string", "default": ""},
                "target_amount": {"type": "number", "default": 1},
                "unit": {"type": "string", "default": "task"},
            },
        },
        llm_result_fields=["goal"],
    )
    async def study_goal_create(
        self,
        date: str = "",
        target_type: str = "custom",
        target_id: str = "",
        subject: str = "",
        target_amount: float = 1,
        unit: str = "task",
        **_,
    ):
        try:
            _, manager, _, _ = self._require_habit_components()
            goal = await asyncio.to_thread(
                manager.create_goal,
                date=str(date or self._today())[:10],
                target_type=target_type,
                target_id=target_id,
                subject=subject,
                target_amount=target_amount,
                unit=unit,
            )
            return Ok({"goal": goal})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_set_deck_goal",
        name=tr("entries.memory_set_deck_goal.name", default="Set Memory Deck Goal"),
        description=tr(
            "entries.memory_set_deck_goal.description",
            default="Create or update today's daily goal for a memory deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string"},
                "date": {"type": "string", "default": ""},
                "target_amount": {"type": "number", "default": 10},
                "unit": {
                    "type": "string",
                    "enum": ["cards", "minutes", "attempts"],
                    "default": "cards",
                },
            },
            "required": ["deck_id"],
        },
        llm_result_fields=["goal", "deck", "created"],
    )
    async def study_memory_set_deck_goal(
        self,
        deck_id: str,
        date: str = "",
        target_amount: float = 10,
        unit: str = "cards",
        **_,
    ):
        try:
            self._require_habit_components()
            bridge = self._require_memory_habit_bridge()
            payload = await asyncio.to_thread(
                bridge.create_deck_goal,
                date=str(date or self._today())[:10],
                deck_id=deck_id,
                target_amount=target_amount,
                unit=unit,
            )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_goal_update",
        name="Update Study Daily Goal",
        description="Update a local daily study goal.",
        input_schema={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "target_amount": {"type": "number"},
                "progress_amount": {"type": "number"},
                "status": {"type": "string"},
            },
            "required": ["goal_id"],
        },
        llm_result_fields=["goal"],
    )
    async def study_goal_update(
        self,
        goal_id: str,
        target_amount: float | None = None,
        progress_amount: float | None = None,
        status: str | None = None,
        **_,
    ):
        try:
            _, manager, _, _ = self._require_habit_components()
            goal = await asyncio.to_thread(
                manager.update_goal,
                goal_id,
                target_amount=target_amount,
                progress_amount=progress_amount,
                status=status,
            )
            return Ok({"goal": goal})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_goal_delete",
        name="Delete Study Daily Goal",
        description="Delete a local daily study goal and associated focus sessions.",
        input_schema={
            "type": "object",
            "properties": {"goal_id": {"type": "string"}},
            "required": ["goal_id"],
        },
        llm_result_fields=["deleted"],
    )
    async def study_goal_delete(self, goal_id: str, **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            deleted = await asyncio.to_thread(manager.delete_goal, goal_id)
            return Ok({"deleted": bool(deleted), "goal_id": goal_id})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_checkin_status",
        name="Study Check-In Status",
        description="Return current check-in status and streak.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "default": ""}},
        },
        llm_result_fields=["checked_in", "streak_days"],
    )
    async def study_checkin_status(self, date: str = "", **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            target_date = str(date or self._today())[:10]
            return Ok(
                await asyncio.to_thread(
                    manager.checkin_status,
                    date=target_date,
                    today=self._today(),
                )
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_checkin_manual",
        name="Manual Study Check-In",
        description="Record a manual study check-in or makeup check-in.",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "default": ""},
                "note": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["checkin"],
    )
    async def study_checkin_manual(self, date: str = "", note: str = "", **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            checkin = await asyncio.to_thread(
                manager.manual_checkin,
                date=str(date or self._today())[:10],
                today=self._today(),
                note=note,
            )
            return Ok({"checkin": checkin})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_session_summary",
        name="Study Habit Session Summary",
        description="Return the daily habit summary for focus minutes and goal completion.",
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "default": ""}},
        },
        llm_result_fields=[
            "total_focus_minutes",
            "completed_goal_count",
            "incomplete_goal_count",
        ],
    )
    async def study_session_summary(self, date: str = "", **_):
        try:
            _, manager, _, _ = self._require_habit_components()
            target_date = str(date or self._today())[:10]
            summary = await asyncio.to_thread(
                manager.daily_summary,
                date=target_date,
            )
            if self._memory_habit_bridge is not None:
                summary["memory_summary"] = await asyncio.to_thread(
                    self._memory_habit_bridge.memory_summary,
                    date=target_date,
                )
            return Ok(summary)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_supervision_status",
        name="Study Supervision Status",
        description="Return focus supervision state and sensor availability.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["enabled", "sensor_available", "reminder_level"],
    )
    async def study_supervision_status(self, **_):
        try:
            _, _, _, supervision = self._require_habit_components()
            return Ok(supervision.status())
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_supervision_toggle",
        name="Toggle Study Supervision",
        description="Enable or disable low-frequency focus supervision reminders.",
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        llm_result_fields=["enabled", "reminder_level"],
    )
    async def study_supervision_toggle(self, enabled: bool, **_):
        try:
            _, _, _, supervision = self._require_habit_components()
            if not bool(enabled) and not self._cfg.supervision.allow_disable_by_chat:
                return Err(SdkError("study supervision disable is blocked by config"))
            return Ok(supervision.set_enabled(bool(enabled)))
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_knowledge_quality_status",
        name=tr(
            "entries.knowledge_quality_status.name",
            default="Study Knowledge Quality Status",
        ),
        description=tr(
            "entries.knowledge_quality_status.description",
            default="Return candidate knowledge quality counts and recent evidence.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
        llm_result_fields=["total", "by_status", "recent_evidence"],
    )
    async def study_knowledge_quality_status(self, limit: int = 20, **_):
        payload = await asyncio.to_thread(
            self._knowledge_tracker.quality.status_summary,
            limit=max(1, int(limit or 20)),
        )
        return Ok(payload)

    @plugin_entry(
        id="study_anonymous_knowledge_preview",
        name=tr(
            "entries.anonymous_knowledge_preview.name",
            default="Study Anonymous Knowledge Preview",
        ),
        description=tr(
            "entries.anonymous_knowledge_preview.description",
            default="Build and return a local anonymized knowledge contribution preview. Phase 4 does not upload it.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
        },
        llm_result_fields=["summary", "stats", "opt_in"],
    )
    async def study_anonymous_knowledge_preview(self, limit: int = 100, **_):
        try:
            builder = PublicGraphContributionBuilder(self._store, self._cfg)
            payload = await asyncio.to_thread(
                builder.preview, limit=max(1, int(limit or 100))
            )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_knowledge_map",
        name=tr("entries.knowledge_map.name", default="Study Knowledge Map"),
        description=tr(
            "entries.knowledge_map.description",
            default="Return topics, relationships, mastery, weak topics, and wrong-question summaries for the study knowledge map.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 200}},
        },
        llm_result_fields=["summary", "nodes", "edges"],
    )
    async def study_knowledge_map(self, limit: int = 200, **_):
        try:
            safe_limit = max(1, min(1000, int(limit or 200)))
            topics, mastery, weak_topics, wrong_questions = await asyncio.gather(
                asyncio.to_thread(self._store.list_topics, safe_limit),
                asyncio.to_thread(self._store.list_mastery_overview, safe_limit),
                asyncio.to_thread(
                    self._knowledge_tracker.get_weak_topics, limit=min(50, safe_limit)
                ),
                asyncio.to_thread(
                    self._store.list_wrong_questions, limit=min(50, safe_limit)
                ),
            )
            return Ok(
                build_knowledge_map_payload(
                    topics=topics,
                    mastery_overview=mastery,
                    weak_topics=weak_topics,
                    wrong_questions=wrong_questions,
                )
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_card_upsert",
        name=tr("entries.memory_card_upsert.name", default="Upsert Study Memory Card"),
        description=tr(
            "entries.memory_card_upsert.description",
            default="Create or update a spaced-repetition memory card in the study deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "front": {"type": "string", "default": ""},
                "back": {"type": "string", "default": ""},
                "topic_id": {"type": "string", "default": ""},
                "subject": {"type": "string", "default": "memory"},
                "chapter": {"type": "string", "default": "memory_deck"},
                "difficulty": {"type": "number", "default": 0.5},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "source": {"type": "string", "default": "manual"},
            },
            "required": ["front", "back"],
        },
        llm_result_fields=["created", "card"],
    )
    async def study_memory_card_upsert(
        self,
        front: str = "",
        back: str = "",
        topic_id: str = "",
        subject: str = "memory",
        chapter: str = "memory_deck",
        difficulty: float = 0.5,
        tags: list[str] | None = None,
        source: str = "manual",
        **_,
    ):
        try:
            topic_key = str(topic_id or "").strip()
            deck = await asyncio.to_thread(
                self._memory_deck_store.get_or_create_default_deck,
                deck_type="custom",
            )
            result = await asyncio.to_thread(
                self._memory_deck_store.upsert_item,
                deck_id=str(deck.get("id") or ""),
                item_type="custom",
                prompt=front,
                answer=back,
                dedupe_metadata_key=("topic_id", "legacy_topic_id") if topic_key else "",
                dedupe_metadata_value=topic_key,
                metadata={
                    "topic_id": topic_key,
                    "legacy_topic_id": topic_key,
                    "subject": str(subject or "memory"),
                    "chapter": str(chapter or "memory_deck"),
                    "difficulty": 0.5 if difficulty is None else float(difficulty),
                    "tags": tags if isinstance(tags, list) else [],
                    "source": str(source or "manual"),
                },
            )
            item = result.get("item") if isinstance(result, dict) else {}
            return Ok(
                {
                    "created": bool(result.get("created"))
                    if isinstance(result, dict)
                    else False,
                    "card": self._memory_deck_store.compat_card_payload(item),
                }
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_deck",
        name=tr("entries.memory_deck.name", default="Study Memory Deck"),
        description=tr(
            "entries.memory_deck.description",
            default="Return memory cards and due spaced-repetition cards for the study deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "due_only": {"type": "boolean", "default": False},
                "include_topic_cards": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["card_count", "due_count", "cards"],
    )
    async def study_memory_deck(
        self,
        limit: int = 20,
        due_only: bool = False,
        include_topic_cards: bool = False,
        **_,
    ):
        try:
            safe_limit = max(1, min(200, int(limit or 20)))
            if bool(include_topic_cards):
                topic_cards = await asyncio.to_thread(
                    self._knowledge_tracker.list_memory_cards,
                    limit=safe_limit,
                    due_only=bool(due_only),
                    include_topic_cards=True,
                )
                if bool(due_only):
                    payload = await asyncio.to_thread(
                        self._memory_deck_store.status_summary, limit=safe_limit
                    )
                    due_reviews = payload.get("due_reviews") if isinstance(payload, dict) else []
                    due_cards = [
                        self._memory_deck_store.compat_card_payload(item.get("item") or {})
                        for item in due_reviews
                        if isinstance(item, dict)
                    ]
                    cards = (due_cards + topic_cards)[:safe_limit]
                    topic_due_count = await asyncio.to_thread(
                        self._knowledge_tracker.count_due_reviews
                    )
                    merged = {
                        k: v
                        for k, v in payload.items()
                        if k != "due_reviews"
                    } if isinstance(payload, dict) else {}
                    return Ok(
                        {
                            **merged,
                            "card_count": len(cards),
                            "due_count": int(payload.get("due_count") or 0)
                            + int(topic_due_count or 0),
                            "cards": cards,
                            "due_cards": cards,
                        }
                    )
                items = await asyncio.to_thread(
                    self._memory_deck_store.list_items,
                    limit=safe_limit,
                    include_archived=False,
                )
                cards = [
                    self._memory_deck_store.compat_card_payload(item) for item in items
                ] + topic_cards
                due_cards = [item for item in cards if item.get("is_due")]
                cards = cards[:safe_limit]
                return Ok(
                    {
                        "card_count": len(cards),
                        "due_count": len(due_cards),
                        "cards": due_cards if bool(due_only) else cards,
                        "due_cards": due_cards,
                    }
                )
            payload = await asyncio.to_thread(
                self._memory_deck_store.status_summary, limit=safe_limit
            )
            all_items = await asyncio.to_thread(
                self._memory_deck_store.list_items,
                limit=safe_limit,
                include_archived=False,
            )
            due_reviews = (
                payload.get("due_reviews") if isinstance(payload, dict) else []
            )
            due_cards = [
                self._memory_deck_store.compat_card_payload(item.get("item") or {})
                for item in due_reviews
                if isinstance(item, dict)
            ]
            cards = [
                self._memory_deck_store.compat_card_payload(item) for item in all_items
            ]
            payload = {**payload, "cards": cards, "due_cards": due_cards}
            if bool(due_only):
                payload = {**payload, "cards": payload.get("due_cards") or []}
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_card_review",
        name=tr("entries.memory_card_review.name", default="Review Study Memory Card"),
        description=tr(
            "entries.memory_card_review.description",
            default="Grade a study memory card with FSRS ratings: again, hard, good, or easy.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "default": ""},
                "rating": {
                    "type": "string",
                    "enum": ["again", "hard", "good", "easy"],
                    "default": "good",
                },
                "answer": {"type": "string", "default": ""},
            },
            "required": ["topic_id", "rating"],
        },
        llm_result_fields=["topic_id", "rating", "schedule", "card"],
    )
    async def study_memory_card_review(
        self, topic_id: str = "", rating: str = "good", answer: str = "", **_
    ):
        try:
            topic_key = str(topic_id or "").strip()
            deck = await asyncio.to_thread(
                self._memory_deck_store.get_or_create_default_deck,
                deck_type="custom",
            )
            try:
                payload = await asyncio.to_thread(
                    self._memory_deck_store.review_item,
                    item_id=topic_key,
                    rating=rating,
                    deck_id=str(deck.get("id") or ""),
                )
            except MemoryItemNotFoundError:
                # Not a memory/custom item: a knowledge-graph topic card surfaced
                # via study_memory_deck(include_topic_cards=True) is reviewed through
                # the topic FSRS backend instead.
                return Ok(
                    await asyncio.to_thread(
                        self._knowledge_tracker.review_memory_card,
                        topic_id=topic_key,
                        rating=rating,
                        answer=answer,
                    )
                )
            item = payload.get("item") if isinstance(payload, dict) else {}
            return Ok(
                {
                    "topic_id": topic_key,
                    "rating": int(payload.get("rating") or 0)
                    if isinstance(payload, dict)
                    else 0,
                    "answer": str(answer or ""),
                    "schedule": payload.get("schedule")
                    if isinstance(payload, dict)
                    else {},
                    "card": self._memory_deck_store.compat_card_payload(item),
                }
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_create_deck",
        name=tr("entries.memory_create_deck.name", default="Create Study Memory Deck"),
        description=tr(
            "entries.memory_create_deck.description",
            default="Create a word, passage, formula, or custom memory deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": ""},
                "deck_type": {
                    "type": "string",
                    "enum": ["word", "passage", "formula", "custom"],
                    "default": "custom",
                },
                "subject": {"type": "string", "default": ""},
                "language": {"type": "string", "default": ""},
                "source": {"type": "string", "default": "manual"},
            },
            "required": ["name"],
        },
        llm_result_fields=["id", "name", "deck_type"],
    )
    async def study_memory_create_deck(
        self,
        name: str = "",
        deck_type: str = "custom",
        subject: str = "",
        language: str = "",
        source: str = "manual",
        **_,
    ):
        try:
            deck = await asyncio.to_thread(
                self._memory_deck_store.create_deck,
                name=name,
                deck_type=deck_type,
                subject=subject,
                language=language,
                source=source,
            )
            return Ok(deck)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_list_decks",
        name=tr("entries.memory_list_decks.name", default="List Study Memory Decks"),
        description=tr(
            "entries.memory_list_decks.description",
            default="List local memory decks and item counts.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
        },
        llm_result_fields=["decks"],
    )
    async def study_memory_list_decks(self, limit: int = 100, **_):
        try:
            decks = await asyncio.to_thread(
                self._memory_deck_store.list_decks,
                limit=max(1, min(500, int(limit or 100))),
            )
            return Ok({"decks": decks})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_delete_deck",
        name=tr("entries.memory_delete_deck.name", default="Delete Study Memory Deck"),
        description=tr(
            "entries.memory_delete_deck.description",
            default="Delete a memory deck and cascade its memory items and review data.",
        ),
        input_schema={
            "type": "object",
            "properties": {"deck_id": {"type": "string", "default": ""}},
            "required": ["deck_id"],
        },
        llm_result_fields=["deleted", "cascade"],
    )
    async def study_memory_delete_deck(self, deck_id: str = "", **_):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.delete_deck, deck_id
            )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_import_words",
        name=tr(
            "entries.memory_import_words.name", default="Import Study Memory Words"
        ),
        description=tr(
            "entries.memory_import_words.description",
            default="Import word cards into a memory deck from CSV or JSON.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string", "default": ""},
                "content": {"type": "string", "default": ""},
                "fmt": {"type": "string", "enum": ["csv", "json"], "default": "csv"},
            },
            "required": ["deck_id", "content"],
        },
        llm_result_fields=[
            "imported_count",
            "updated_count",
            "skipped_rows",
            "preview",
        ],
    )
    async def study_memory_import_words(
        self, deck_id: str = "", content: str = "", fmt: str = "csv", **_
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.import_words,
                deck_id=deck_id,
                content=content,
                fmt=fmt,
            )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_import_passage",
        name=tr(
            "entries.memory_import_passage.name", default="Import Study Memory Passage"
        ),
        description=tr(
            "entries.memory_import_passage.description",
            default="Split passage text into paragraph memory items and FSRS cards.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string", "default": ""},
                "text": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
            },
            "required": ["deck_id", "text"],
        },
        llm_result_fields=["imported_count", "items"],
    )
    async def study_memory_import_passage(
        self, deck_id: str = "", text: str = "", title: str = "", **_
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.import_passage,
                deck_id=deck_id,
                text=text,
                title=title,
            )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_due_reviews",
        name=tr("entries.memory_due_reviews.name", default="Study Memory Due Reviews"),
        description=tr(
            "entries.memory_due_reviews.description",
            default="Return due memory reviews sorted by deck and retrievability.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deck_id": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
                "item_type": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["due_reviews"],
    )
    async def study_memory_due_reviews(
        self, deck_id: str = "", limit: int = 50, item_type: str = "", **_
    ):
        try:
            reviews = await asyncio.to_thread(
                self._memory_deck_store.due_reviews,
                deck_id=deck_id,
                limit=max(1, min(500, int(limit or 50))),
                item_type=item_type,
            )
            return Ok({"due_reviews": reviews})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_review_item",
        name=tr("entries.memory_review_item.name", default="Review Study Memory Item"),
        description=tr(
            "entries.memory_review_item.description",
            default="Record a memory item review and update its dedicated FSRS card.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "default": ""},
                "rating": {
                    "type": "string",
                    "enum": ["again", "hard", "good", "easy"],
                },
                "correct": {"type": "boolean"},
                "error_type": {"type": "string", "default": ""},
                "elapsed_ms": {"type": "integer", "default": 0},
                "session_id": {"type": "string", "default": ""},
            },
            "required": ["item_id"],
        },
        llm_result_fields=["item", "rating", "schedule", "review_record"],
    )
    async def study_memory_review_item(
        self,
        item_id: str = "",
        rating: str | None = None,
        correct: bool | None = None,
        error_type: str = "",
        elapsed_ms: int = 0,
        session_id: str = "",
        **_,
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.review_item,
                item_id=item_id,
                rating=rating,
                correct=correct if isinstance(correct, bool) else None,
                error_type=error_type,
                elapsed_ms=int(elapsed_ms or 0) or None,
                session_id=session_id,
            )
            if self._memory_habit_bridge is not None:
                try:
                    payload["habit_progress"] = await asyncio.to_thread(
                        self._memory_habit_bridge.apply_review_progress,
                        payload,
                        date=self._today(),
                    )
                except Exception as bridge_exc:
                    self.logger.warning(
                        "memory habit review progress degraded: {}", bridge_exc
                    )
                    payload["habit_progress"] = {
                        "applied": 0,
                        "error": str(bridge_exc),
                    }
            try:
                await self._emit_memory_review_answer_event(payload)
            except Exception as emit_exc:
                self.logger.warning(
                    "memory review event emission degraded: {}", emit_exc
                )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_recitation_attempt",
        name=tr(
            "entries.memory_recitation_attempt.name",
            default="Submit Study Memory Recitation",
        ),
        description=tr(
            "entries.memory_recitation_attempt.description",
            default="Diff a passage recitation attempt and record the resulting FSRS review.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "default": ""},
                "user_input_text": {"type": "string", "default": ""},
                "hint_count": {"type": "integer", "default": 0},
                "elapsed_ms": {"type": "integer", "default": 0},
                "session_id": {"type": "string", "default": ""},
            },
            "required": ["item_id", "user_input_text"],
        },
        llm_result_fields=["attempt", "diff", "review"],
    )
    async def study_memory_recitation_attempt(
        self,
        item_id: str = "",
        user_input_text: str = "",
        hint_count: int = 0,
        elapsed_ms: int = 0,
        session_id: str = "",
        **_,
    ):
        try:
            payload = await asyncio.to_thread(
                self._memory_deck_store.add_recitation_attempt,
                item_id=item_id,
                user_input_text=user_input_text,
                hint_count=max(0, int(hint_count or 0)),
                elapsed_ms=int(elapsed_ms or 0) or None,
                session_id=session_id,
            )
            if self._memory_habit_bridge is not None:
                try:
                    payload["habit_progress"] = await asyncio.to_thread(
                        self._memory_habit_bridge.apply_recitation_progress,
                        payload,
                        date=self._today(),
                    )
                except Exception as bridge_exc:
                    self.logger.warning(
                        "memory habit recitation progress degraded: {}", bridge_exc
                    )
                    payload["habit_progress"] = {
                        "applied": 0,
                        "error": str(bridge_exc),
                    }
            try:
                await self._emit_recitation_answer_event(payload)
            except Exception as emit_exc:
                self.logger.warning(
                    "memory recitation event emission degraded: {}", emit_exc
                )
            return Ok(payload)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_memory_generate_draft",
        name=tr(
            "entries.memory_generate_draft.name", default="Generate Study Memory Draft"
        ),
        description=tr(
            "entries.memory_generate_draft.description",
            default="Generate a candidate memory draft without saving it to a deck.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "draft_type": {
                    "type": "string",
                    "enum": ["word_example", "sentence_cloze", "recitation_error"],
                    "default": "word_example",
                },
                "word": {"type": "string", "default": ""},
                "meaning": {"type": "string", "default": ""},
                "sentence": {"type": "string", "default": ""},
                "expected": {"type": "string", "default": ""},
                "actual": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["id", "payload", "status"],
    )
    async def study_memory_generate_draft(
        self,
        draft_type: str = "word_example",
        word: str = "",
        meaning: str = "",
        sentence: str = "",
        expected: str = "",
        actual: str = "",
        **_,
    ):
        try:
            normalized = str(draft_type or "word_example")
            if normalized == "sentence_cloze":
                candidate = await asyncio.to_thread(
                    self._memory_deck_store.create_cloze_draft,
                    sentence=sentence,
                )
            elif normalized == "recitation_error":
                candidate = await asyncio.to_thread(
                    self._memory_deck_store.create_recitation_error_draft,
                    expected=expected,
                    actual=actual,
                )
            else:
                candidate = await asyncio.to_thread(
                    self._memory_deck_store.create_word_draft,
                    word=word,
                    meaning=meaning,
                )
            return Ok(candidate)
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_set_knowledge_contribution_opt_in",
        name=tr(
            "entries.set_knowledge_contribution_opt_in.name",
            default="Set Study Knowledge Contribution Opt-In",
        ),
        description=tr(
            "entries.set_knowledge_contribution_opt_in.description",
            default="Enable or disable local opt-in for anonymous study knowledge contribution queueing.",
        ),
        input_schema={
            "type": "object",
            "properties": {"opt_in": {"type": "boolean", "default": False}},
            "required": ["opt_in"],
        },
        llm_result_fields=["opt_in", "summary", "queue"],
    )
    async def study_set_knowledge_contribution_opt_in(self, opt_in: bool = False, **_):
        try:
            desired_opt_in = bool(opt_in)
            preview_config = StudyConfig(**self._cfg.to_dict())
            preview_config.knowledge_contribution_opt_in = desired_opt_in
            builder = PublicGraphContributionBuilder(self._store, preview_config)
            preview = await asyncio.to_thread(builder.preview, limit=100)
            self._cfg.knowledge_contribution_opt_in = desired_opt_in
            await self._persist_state()
            return Ok(
                build_contribution_settings_payload(
                    opt_in=desired_opt_in, preview=preview
                )
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_clear_knowledge_contribution_queue",
        name=tr(
            "entries.clear_knowledge_contribution_queue.name",
            default="Clear Study Knowledge Contribution Queue",
        ),
        description=tr(
            "entries.clear_knowledge_contribution_queue.description",
            default="Clear the local anonymous knowledge contribution queue.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["cleared_count"],
    )
    async def study_clear_knowledge_contribution_queue(self, **_):
        try:
            builder = PublicGraphContributionBuilder(self._store, self._cfg)
            cleared = await asyncio.to_thread(builder.clear_queue)
            return Ok({"cleared_count": cleared})
        except Exception as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="study_detect_mode_intent",
        name=tr("entries.detect_mode_intent.name", default="Detect Study Mode Intent"),
        description=tr(
            "entries.detect_mode_intent.description",
            default="Detect whether a text snippet contains a study mode switch intent.",
        ),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "default": ""}},
        },
        llm_result_fields=["mode", "pure_switch", "transition_phrase"],
    )
    async def study_detect_mode_intent(self, text: str = "", **_):
        return Ok(handle_user_intent(text, language=self._cfg.language))

    @plugin_entry(
        id="study_set_mode",
        name=tr("entries.set_mode.name", default="Set Study Mode"),
        description=tr(
            "entries.set_mode.description",
            default="Switch the study companion between companion, interactive, and teaching modes.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING],
                },
                "reason": {"type": "string", "default": "ui"},
            },
            "required": ["mode"],
        },
        llm_result_fields=["changed", "new_mode", "transition_phrase"],
    )
    async def study_set_mode(self, mode: str, reason: str = "ui", **_):
        try:
            result = await self._apply_mode_switch(
                mode, reason, language=self._cfg.language
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        return Ok(result)

    @plugin_entry(
        id="study_dependency_status",
        name=tr(
            "entries.dependency_status.name", default="Study OCR Dependency Status"
        ),
        description=tr(
            "entries.dependency_status.description",
            default="Inspect RapidOCR, Tesseract, and capture dependencies used by study_companion.",
        ),
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
        description=tr(
            "entries.ocr_snapshot.description",
            default="Run a lightweight OCR snapshot. Phase 1 attempts fullscreen capture and returns diagnostics on failure.",
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=45.0,
        llm_result_fields=["summary", "status", "diagnostic"],
    )
    async def study_ocr_snapshot(self, **_):
        if self._ocr_pipeline is None:
            return Err(SdkError("study OCR pipeline is not initialized"))
        snapshot = await asyncio.to_thread(self._ocr_pipeline.capture_snapshot)
        payload = build_ocr_payload(snapshot)
        if self._supervision is not None:
            sensor_available = snapshot.status in {"ok", "empty"}
            payload["supervision"] = self._supervision.observe_activity(
                ocr_text=snapshot.text,
                sensor_available=sensor_available,
            )
        if snapshot.text.strip():
            with self._lock:
                self._state.last_ocr_text = snapshot.text
                self._state.last_ocr_at = snapshot.captured_at
            payload["screen_classification"] = self._update_screen_classification(
                snapshot.text, update_empty=False
            )
        elif snapshot.status == "empty":
            payload["screen_classification"] = self._update_screen_classification(
                "", update_empty=True
            )
        await self._persist_state()
        return Ok(payload)

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
        with self._lock:
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
                return Err(SdkError(str(exc)))
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
                "source": "ocr_snapshot"
                if used_ocr_fallback or not raw_text
                else "manual",
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
            },
        },
        timeout=60.0,
        llm_result_fields=[
            "summary",
            "question",
            "answer",
            "hint",
            "difficulty",
            "topic",
        ],
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

    @plugin_entry(
        id="study_evaluate_answer",
        name=tr("entries.evaluate_answer.name", default="Evaluate Study Answer"),
        description=tr(
            "entries.evaluate_answer.description",
            default="Evaluate an answer against the current generated question or a supplied question.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string", "default": ""},
                "question": {"type": "string", "default": ""},
                "expected_answer": {"type": "string", "default": ""},
            },
        },
        timeout=60.0,
        llm_result_fields=[
            "summary",
            "verdict",
            "score",
            "error_type",
            "feedback",
            "next_action",
        ],
    )
    async def study_evaluate_answer(
        self, answer: str = "", question: str = "", expected_answer: str = "", **kwargs
    ):
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
        if not resolved_expected and (
            not supplied_question or supplied_question == state_question
        ):
            resolved_expected = state_expected
        answer_text = str(answer or "").strip()
        using_current_question = (
            not supplied_question or supplied_question == state_question
        )
        question_payload = dict(current_question) if using_current_question else {}
        question_payload.update(
            {
                "question": resolved_question,
                "answer": resolved_expected,
            }
        )
        run_id = self._resolve_current_run_id(kwargs)
        session_id = str(kwargs.get("session_id") or "").strip()
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_ANSWER_EVALUATE,
            input_text=answer_text,
            extra={
                "question": resolved_question,
                "expected_answer": resolved_expected,
                "answer": answer_text,
                "current_question": current_question if using_current_question else {},
                "question_payload": question_payload,
                "question_source": "current_question"
                if using_current_question
                else "supplied",
                "run_id": run_id,
                "session_id": session_id,
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
                "screen_classification": tutor_context.get("screen_classification")
                or {},
            },
            extra_context=tutor_context,
        )
        payload["question"] = resolved_question
        payload["screen_classification"] = (
            tutor_context.get("screen_classification") or {}
        )
        topic = str(
            payload.get("topic")
            or question_payload.get("topic")
            or tutor_context.get("topic")
            or ""
        ).strip()
        try:
            mastery_after = (
                await asyncio.to_thread(self._knowledge_tracker.get_mastery, topic)
                if topic
                else -1.0
            )
        except Exception as exc:
            self.logger.warning("study answer mastery enrichment failed: {}", exc)
            mastery_after = -1.0
        await self._emit_answer_evaluated_event(
            verdict=str(payload.get("verdict") or ""),
            score=payload.get("score", 0.0),
            question_summary=resolved_question,
            user_answer_summary=answer_text,
            correction_hint=str(
                payload.get("correction_hint")
                or payload.get("feedback")
                or payload.get("next_action")
                or ""
            ),
            topic=topic,
            mastery_after=mastery_after,
        )
        return Ok(payload)

    @plugin_entry(
        id="study_summarize_session",
        name=tr("entries.summarize_session.name", default="Summarize Study Session"),
        description=tr(
            "entries.summarize_session.description",
            default="Summarize recent study interactions into compact study notes.",
        ),
        input_schema={
            "type": "object",
            "properties": {"focus": {"type": "string", "default": ""}},
        },
        timeout=75.0,
        llm_result_fields=[
            "summary",
            "markdown",
            "highlights",
            "weak_points",
            "next_actions",
        ],
    )
    async def study_summarize_session(self, focus: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        with self._lock:
            active_mode = self._state.active_mode
        history = await asyncio.to_thread(
            self._store.list_interactions, max(5, min(30, self._cfg.history_limit))
        )
        tutor_context = await self._build_learning_context(
            LLM_OPERATION_SUMMARIZE_SESSION,
            input_text="session",
            extra={
                "focus": str(focus or "").strip(),
                "history": history,
                "mode": active_mode,
            },
        )
        reply = await self._agent.summarize_session(
            history, mode=active_mode, context=tutor_context
        )
        payload = await self._finalize_tutor_call(
            LLM_OPERATION_SUMMARIZE_SESSION,
            reply,
            history_kind=LLM_OPERATION_SUMMARIZE_SESSION,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "payload": reply.payload,
                "screen_classification": tutor_context.get("screen_classification")
                or {},
            },
        )
        payload["screen_classification"] = (
            tutor_context.get("screen_classification") or {}
        )
        await self._emit_session_summarized_event(payload)
        return Ok(payload)

    @plugin_entry(
        id="study_install_tesseract",
        name=tr(
            "entries.install_tesseract.name", default="Install Tesseract for Study OCR"
        ),
        description=tr(
            "entries.install_tesseract.description",
            default="Install local Tesseract OCR for study_companion and refresh dependency status.",
        ),
        input_schema={
            "type": "object",
            "properties": {"force": {"type": "boolean", "default": False}},
        },
        timeout=300.0,
        llm_result_fields=["summary"],
    )
    async def study_install_tesseract(self, force: bool = False, **kwargs):
        with self._lock:
            if self._install_in_progress:
                return Err(SdkError("Tesseract install is already running"))
            self._install_in_progress = True
        run_id = self._resolve_current_run_id(kwargs)
        try:
            from .tesseract_support import install_tesseract

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
            return Ok(
                {
                    "summary": str(result.get("summary") or "Tesseract is ready"),
                    "install_result": result,
                }
            )
        except Exception as exc:
            return Err(SdkError(f"Tesseract install failed: {exc}"))
        finally:
            with self._lock:
                self._install_in_progress = False

    @plugin_entry(
        id="study_download_rapidocr_models",
        name=tr(
            "entries.download_rapidocr_models.name",
            default="Download RapidOCR Models for Study OCR",
        ),
        description=tr(
            "entries.download_rapidocr_models.description",
            default="Download missing RapidOCR model files for the configured study_companion OCR language.",
        ),
        input_schema={
            "type": "object",
            "properties": {"force": {"type": "boolean", "default": False}},
        },
        timeout=600.0,
        llm_result_fields=["summary"],
    )
    async def study_download_rapidocr_models(self, force: bool = False, **kwargs):
        with self._lock:
            if self._rapidocr_models_in_progress:
                return Err(SdkError("RapidOCR model download is already running"))
            self._rapidocr_models_in_progress = True
        run_id = self._resolve_current_run_id(kwargs)
        try:
            from plugin.plugins._shared.rapidocr.rapidocr_support import (
                download_rapidocr_models,
            )
            from plugin.server.routes._install_task_store import update_install_task_state

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
                install_state_updater=update_install_task_state,
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
            with self._lock:
                self._rapidocr_models_in_progress = False


StudyCompanionBridgePlugin = StudyCompanionPlugin
