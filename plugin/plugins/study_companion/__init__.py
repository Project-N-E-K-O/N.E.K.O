from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from datetime import datetime
import math
from pathlib import Path
from types import SimpleNamespace
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


_REVIEW_DUE_INTERVAL_SECONDS = 1800.0


from .entry_tutor_context_support import _TutorContextSupportMixin
from .entry_communication_review_events import _CommunicationReviewEventsMixin
from .entry_communication_tutor_events import _CommunicationTutorEventsMixin
from .entry_export_support import _ExportSupportMixin
from .entry_status_entries import _StatusEntriesMixin
from .entry_memory_card_entries import _MemoryCardEntriesMixin
from .entry_memory_deck_entries import _MemoryDeckEntriesMixin
from .entry_memory_import_entries import _MemoryImportEntriesMixin
from .entry_memory_review_entries import _MemoryReviewEntriesMixin
from .entry_pomodoro_entries import _PomodoroEntriesMixin
from .entry_goal_entries import _GoalEntriesMixin
from .entry_checkin_entries import _CheckinEntriesMixin
from .entry_supervision_entries import _SupervisionEntriesMixin
from .entry_knowledge_entries import _KnowledgeEntriesMixin
from .entry_mode_entries import _ModeEntriesMixin
from .entry_tutor_explain_entries import _TutorExplainEntriesMixin
from .entry_tutor_question_entries import _TutorQuestionEntriesMixin
from .entry_tutor_answer_entries import _TutorAnswerEntriesMixin
from .entry_tutor_summary_entries import _TutorSummaryEntriesMixin
from .entry_ocr_entries import _OcrEntriesMixin
from .entry_neko_commands import (
    _INTERRUPT_COMMANDS,
    _NEKO_COMMAND_HANDLERS,
    _NekoCommandsMixin,
    _QUEUE_COMMANDS,
)


@neko_plugin
# MRO notes:
# - _TutorContextSupportMixin owns tutor finalization and learning tracking.
# - Tutor entry mixins call context/finalization helpers from that support mixin.
# Keep the support mixin before tutor entry mixins unless those helpers move.
class StudyCompanionPlugin(
    _TutorContextSupportMixin,
    _CommunicationReviewEventsMixin,
    _CommunicationTutorEventsMixin,
    _ExportSupportMixin,
    _StatusEntriesMixin,
    _MemoryCardEntriesMixin,
    _MemoryDeckEntriesMixin,
    _MemoryImportEntriesMixin,
    _MemoryReviewEntriesMixin,
    _PomodoroEntriesMixin,
    _GoalEntriesMixin,
    _CheckinEntriesMixin,
    _SupervisionEntriesMixin,
    _KnowledgeEntriesMixin,
    _ModeEntriesMixin,
    _TutorExplainEntriesMixin,
    _TutorQuestionEntriesMixin,
    _TutorAnswerEntriesMixin,
    _TutorSummaryEntriesMixin,
    _OcrEntriesMixin,
    _NekoCommandsMixin,
    NekoPluginBase,
):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = asyncio.Lock()
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
        self._command_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._command_worker_task: asyncio.Task[None] | None = None
        self._interruptible_task: asyncio.Task[None] | None = None
        self._neko_command_transport: Any | None = None
        self._neko_command_handler: Any | None = None
        self._neko_command_watcher: Any | None = None
        self._worker_crash_count = 0
        self._worker_last_crash_time = 0.0

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
            async with self._lock:
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
            await self._refresh_dependency_status()
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
            if self._event_bus is not None:
                await self._subscribe_neko_commands()
                self._start_command_worker()
            status_payload = await asyncio.to_thread(self._status_payload)
            return Ok({"status": STATUS_READY, "result": status_payload})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("study plugin startup failed: {}", exc)
            await self._cleanup_after_failed_startup()
            async with self._lock:
                self._state.status = STATUS_ERROR
                self._state.last_error = "startup_failed"
            return Err(SdkError("failed to start study_companion"))

    async def _cleanup_after_failed_startup(self) -> None:
        await self._unsubscribe_neko_commands()
        await self._cancel_command_worker()
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
        await self._unsubscribe_neko_commands()
        await self._cancel_command_worker()
        await self._cancel_review_due_task()
        try:
            self.unregister_dynamic_entry("study_export_notes")
        except Exception as exc:
            self.logger.warning("study shutdown dynamic entry cleanup failed: {}", exc)
        if self._agent is not None:
            await self._agent.shutdown()
        async with self._lock:
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

    def _start_command_worker(self) -> None:
        if self._event_bus is None:
            return
        if self._command_worker_task is not None and not self._command_worker_task.done():
            return
        if self._worker_crash_count >= 3:
            now = time.monotonic()
            if now - self._worker_last_crash_time < 10.0:
                self.logger.error(
                    "_command_worker auto-restart disabled after {} crashes",
                    self._worker_crash_count,
                )
                return
            self._worker_crash_count = 0
        self._command_worker_task = asyncio.create_task(self._run_command_worker())
        self._command_worker_task.add_done_callback(self._on_command_worker_done)

    async def _cancel_command_worker(self) -> None:
        worker = self._command_worker_task
        self._command_worker_task = None
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.logger.warning("study command worker cleanup failed: {}", exc)

        while True:
            try:
                self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        task = self._interruptible_task
        self._interruptible_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.logger.warning("study command task cleanup failed: {}", exc)

    def _on_command_worker_done(self, task: asyncio.Task[None]) -> None:
        if self._command_worker_task is task:
            self._command_worker_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            self.logger.exception("_command_worker exited with error")
            now = time.monotonic()
            if now - self._worker_last_crash_time < 10.0:
                self._worker_crash_count += 1
            else:
                self._worker_crash_count = 1
            self._worker_last_crash_time = now
            if self._worker_crash_count >= 3:
                self.logger.error(
                    "_command_worker crashed {} times in 10s; disabling auto-restart",
                    self._worker_crash_count,
                )

    def _on_command_task_done(self, task: asyncio.Task[None]) -> None:
        if self._interruptible_task is task:
            self._interruptible_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            self.logger.exception("command task failed")

    async def _run_command_worker(self) -> None:
        while True:
            try:
                cmd, payload = await self._command_queue.get()
            except asyncio.CancelledError:
                while True:
                    try:
                        self._command_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                raise

            try:
                await self._execute_command(cmd, payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("_command_worker failed to execute: {}", cmd)

    async def _execute_command(self, cmd: str, payload: dict[str, Any]) -> None:
        if cmd not in _QUEUE_COMMANDS or cmd in _INTERRUPT_COMMANDS:
            return
        handler_name = _NEKO_COMMAND_HANDLERS.get(cmd)
        handler = getattr(self, handler_name or "", None)
        if handler is None:
            return

        worker_task = asyncio.current_task()
        while True:
            current = self._interruptible_task
            if current is None or current.done():
                break
            try:
                await current
                if worker_task is not None and worker_task.cancelling():
                    raise asyncio.CancelledError
            except asyncio.CancelledError:
                if worker_task is not None and worker_task.cancelling():
                    raise
            except Exception:
                pass

        async def _run() -> None:
            try:
                await handler(payload)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_run())
        self._interruptible_task = task
        task.add_done_callback(self._on_command_task_done)
        try:
            await task
            if worker_task is not None and worker_task.cancelling():
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            if worker_task is not None and worker_task.cancelling():
                raise
        except Exception:
            pass

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

    async def _refresh_dependency_status(self) -> dict[str, Any]:
        status = await asyncio.to_thread(build_dependency_status, self._cfg)
        async with self._lock:
            self._state.dependency_status = status
        return status

    async def _persist_state(self) -> None:
        await asyncio.to_thread(self._store.save_config, self._cfg)
        await asyncio.to_thread(self._store.save_state, self._state)

    async def _apply_mode_switch(
        self, mode: str, reason: str, *, language: str | None = None
    ) -> dict[str, Any]:
        async with self._lock:
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
                self._state.session_suggestions = checkpoint.get("session_suggestions")
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
                self.logger.warning(
                    "invalid study checkin timezone configured: {}",
                    timezone_name[:64],
                )
        return datetime.now().astimezone().date().isoformat()

    def _state_snapshot(self) -> dict[str, Any]:
        return self._state.to_dict()

    def _screen_classification_context(self) -> dict[str, Any]:
        return dict(self._state.last_screen_classification)

    async def _update_screen_classification(
        self, text: str, *, window_title: str = "", update_empty: bool = True
    ) -> dict[str, Any]:
        normalized = str(text or "").strip()
        async with self._lock:
            if not normalized and not update_empty:
                return dict(self._state.last_screen_classification)
            recent = list(self._state.recent_screen_classifications)
            previous = dict(self._state.last_screen_classification)
            classification = classify_screen_from_ocr(
                normalized, window_title=window_title, recent_classifications=recent
            )
            payload = classification.to_payload()
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
                and self._event_bus.should_schedule_screen_context(
                    new_type, previous_type
                )
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


StudyCompanionBridgePlugin = StudyCompanionPlugin
