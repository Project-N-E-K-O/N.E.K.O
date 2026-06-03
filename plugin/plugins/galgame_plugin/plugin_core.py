from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
)

from .character_profile import CharacterProfileManager
from .game_llm_agent import GameLLMAgent
from .host_agent_adapter import HostAgentAdapter
from .llm_gateway import LLMGateway
from .memory_reader import MemoryReaderManager
from .ocr_reader import OcrReaderManager, utc_now_iso
from .models import (
    ADVANCE_SPEEDS,
    ADVANCE_SPEED_MEDIUM,
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_NONE,
    DATA_SOURCE_OCR_READER,
    MODE_CHOICE_ADVISOR,
    MODE_COMPANION,
    MODES,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_SAVE_SCOPES,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGES,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    OCR_TRIGGER_MODES,
    parse_ocr_capture_profile_bucket_key,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    READER_MODE_OCR,
    READER_MODES,
    STATE_ACTIVE,
    STATE_ERROR,
    STORE_BOUND_GAME_ID,
    STORE_ADVANCE_SPEED,
    STORE_CHARACTER_FIXED_NAME,
    STORE_CHARACTER_MODE,
    STORE_CHARACTER_PROFILE_VERSION,
    STORE_CHARACTER_PROFILES,
    STORE_DEDUPE_WINDOW,
    STORE_CROSS_SCENE_MEMORY,
    STORE_EVENTS_BYTE_OFFSET,
    STORE_EVENTS_FILE_SIZE,
    STORE_LAST_ERROR,
    STORE_LAST_SEQ,
    STORE_CHARACTER_RUNTIME_STATE,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_MEMORY_READER_TARGET,
    STORE_MODE,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_CAPTURE_PROFILES,
    STORE_OCR_FAST_LOOP_ENABLED,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_OCR_WINDOW_TARGET,
    STORE_RAPIDOCR_AUTO_DETECT_LANG,
    STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG,
    STORE_RAPIDOCR_LANG_TYPE,
    STORE_RAPIDOCR_OCR_VERSION,
    STORE_PUSH_NOTIFICATIONS,
    STORE_READER_MODE,
    STORE_SESSION_ID,
    json_copy,
    make_error,
)
from .dependency_status import (
    infer_inspection_failed_dependencies,
    infer_missing_dependencies,
)
from plugin.plugins._shared.rapidocr.rapidocr_support import inspect_rapidocr_installation
from .dxcam_support import inspect_dxcam_installation
from .reader import tail_events_jsonl, warmup_replay_events
from .service import (
    apply_event_to_histories,
    apply_event_to_snapshot,
    apply_input_degraded_result,
    build_active_session_meta,
    build_config,
    build_explain_degraded_result,
    build_explain_context,
    build_history_payload,
    build_ocr_context_diagnostic,
    build_ocr_background_status,
    build_primary_diagnosis,
    build_snapshot_payload,
    build_status_payload,
    build_suggest_context,
    build_suggest_degraded_result,
    build_summarize_degraded_result,
    build_summarize_context,
    choose_candidate,
    clear_install_inspection_cache,
    derive_connection_state,
    filter_memory_reader_candidates,
    filter_ocr_reader_candidates,
    mode_allows_agent_actuation,
    next_poll_interval_for_state,
    rebuild_histories_from_events,
    scan_session_candidates,
)
from .state import GalgameSharedState, build_initial_state
from .store import GalgameStore
from .textractor_support import install_textractor
from .ui_api import build_open_ui_payload
from .screen_classifier import classify_screen_from_ocr, normalize_screen_type
from .screen_awareness_training import (
    evaluate_screen_awareness_model,
    train_screen_awareness_model,
)


from .plugin_util_helpers import (
    _log_plugin_noncritical,
    _package_public_attr,
    _public_context_snapshot,
    _migrate_legacy_capture_backend,
    _duration_percentile,
    _duration_summary,
    _open_url_in_browser,
)

from .plugin_constants import (
    _OCR_BACKEND_SELECTIONS,
    _OCR_CAPTURE_BACKEND_SELECTIONS,
)


_BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS = 45.0
_BRIDGE_TICK_INTERVAL_SECONDS = 1.0
# Foreground refresh TTL: repeated calls within two seconds return early so
# bridge_tick, advance monitor, and status payload refreshes stay idempotent.
_OCR_FOREGROUND_REFRESH_TTL_SECONDS = 2.0
_LATENCY_SAMPLE_LIMIT = 120
_LATENCY_MIN_SAMPLES_FOR_P95 = 5
_OCR_POLL_P95_DEGRADE_THRESHOLD_SECONDS = 3.0
_OCR_FOREGROUND_ADVANCE_MONITOR_INTERVAL_SECONDS = 0.05
_OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS = 0.15
_OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS = 0.15
_OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS = 2.0


from .plugin_ocr_helpers import (
    _normalize_ocr_trigger_mode,
    _normalize_reader_mode,
    _session_candidate_has_text,
    _pending_data_source_for_reader_mode,
    _AFTER_ADVANCE_SCREEN_REFRESH_STAGES,
    _after_advance_screen_refresh_needed,
    _companion_after_advance_ocr_refresh_needed,
    _ocr_reader_allowed_block_reason,
    _ocr_tick_block_reason,
    _ocr_emit_block_reason,
    _apply_ocr_decision_diagnostics,
    _OCR_BRIDGE_DIAGNOSTIC_RUNTIME_KEYS,
    _merge_ocr_runtime_preserving_bridge_diagnostics,
)


from .plugin_capture_profile_helpers import (
    _normalize_ocr_capture_profile_stage,
    _normalize_ocr_capture_profile_save_scope,
    _is_ratio_profile_payload,
    _normalize_ocr_capture_profile_payload,
    _capture_profile_entry_to_stage_map,
    _capture_profile_bucket_entry_to_stage_map,
    _capture_profile_entry_to_window_bucket_map,
    _window_bucket_map_to_capture_profile_payload,
    _capture_profile_components_to_entry,
)


from .plugin_config_service import GalgamePluginConfigService


# Mixin imports for GalgamePlugin entries — sorted alphabetically by mixin
# class name so the order here matches the class bases list below. Adding a
# new entry means: (1) drop a file under plugin_entries/, (2) add its import
# here, and (3) insert the mixin into the GalgamePlugin bases list — both in
# alphabetical position.
from .plugin_entries.galgame_agent_command import _GalgameAgentCommandMixin
from .plugin_entries.galgame_apply_recommended_ocr_capture_profile import _GalgameApplyRecommendedOcrCaptureProfileMixin
from .plugin_entries.galgame_auto_recalibrate_ocr_dialogue_profile import _GalgameAutoRecalibrateOcrDialogueProfileMixin
from .plugin_entries.galgame_bind_game import _GalgameBindGameMixin
from .plugin_entries.galgame_build_ocr_screen_template_draft import _GalgameBuildOcrScreenTemplateDraftMixin
from .plugin_entries.galgame_continue_auto_advance import _GalgameContinueAutoAdvanceMixin
from .plugin_entries.galgame_download_rapidocr_models import _GalgameDownloadRapidocrModelsMixin
from .plugin_entries.galgame_evaluate_ocr_screen_awareness_model import _GalgameEvaluateOcrScreenAwarenessModelMixin
from .plugin_entries.galgame_explain_line import _GalgameExplainLineMixin
from .plugin_entries.galgame_get_character_list import _GalgameGetCharacterListMixin
from .plugin_entries.galgame_get_character_profile import _GalgameGetCharacterProfileMixin
from .plugin_entries.galgame_get_history import _GalgameGetHistoryMixin
from .plugin_entries.galgame_get_ocr_screen_awareness_snapshot import _GalgameGetOcrScreenAwarenessSnapshotMixin
from .plugin_entries.galgame_get_push_history import _GalgameGetPushHistoryMixin
from .plugin_entries.galgame_get_recent_lines import _GalgameGetRecentLinesMixin
from .plugin_entries.galgame_get_scene_context import _GalgameGetSceneContextMixin
from .plugin_entries.galgame_get_snapshot import _GalgameGetSnapshotMixin
from .plugin_entries.galgame_get_status import _GalgameGetStatusMixin
from .plugin_entries.galgame_get_story_so_far import _GalgameGetStorySoFarMixin
from .plugin_entries.galgame_import_character_data import _GalgameImportCharacterDataMixin
from .plugin_entries.galgame_install_textractor import _GalgameInstallTextractorMixin
from .plugin_entries.galgame_list_memory_reader_processes import _GalgameListMemoryReaderProcessesMixin
from .plugin_entries.galgame_list_ocr_windows import _GalgameListOcrWindowsMixin
from .plugin_entries.galgame_open_ui import _GalgameOpenUiMixin
from .plugin_entries.galgame_rollback_ocr_capture_profile import _GalgameRollbackOcrCaptureProfileMixin
from .plugin_entries.galgame_set_character_mode import _GalgameSetCharacterModeMixin
from .plugin_entries.galgame_set_llm_vision import _GalgameSetLlmVisionMixin
from .plugin_entries.galgame_set_memory_reader_target import _GalgameSetMemoryReaderTargetMixin
from .plugin_entries.galgame_set_mode import _GalgameSetModeMixin
from .plugin_entries.galgame_set_ocr_backend import _GalgameSetOcrBackendMixin
from .plugin_entries.galgame_set_ocr_capture_profile import _GalgameSetOcrCaptureProfileMixin
from .plugin_entries.galgame_set_ocr_screen_templates import _GalgameSetOcrScreenTemplatesMixin
from .plugin_entries.galgame_set_ocr_timing import _GalgameSetOcrTimingMixin
from .plugin_entries.galgame_set_ocr_window_target import _GalgameSetOcrWindowTargetMixin
from .plugin_entries.galgame_set_rapidocr_lang import _GalgameSetRapidocrLangMixin
from .plugin_entries.galgame_suggest_choice import _GalgameSuggestChoiceMixin
from .plugin_entries.galgame_summarize_scene import _GalgameSummarizeSceneMixin
from .plugin_entries.galgame_train_ocr_screen_awareness_model import _GalgameTrainOcrScreenAwarenessModelMixin
from .plugin_entries.galgame_validate_ocr_screen_templates import _GalgameValidateOcrScreenTemplatesMixin


def _package_json_copy(value: Any) -> Any:
    package = sys.modules.get(__package__)
    copy_fn = getattr(package, "json_copy", json_copy) if package is not None else json_copy
    return copy_fn(value)


@neko_plugin
class GalgamePlugin(
    _GalgameAgentCommandMixin,
    _GalgameApplyRecommendedOcrCaptureProfileMixin,
    _GalgameAutoRecalibrateOcrDialogueProfileMixin,
    _GalgameBindGameMixin,
    _GalgameBuildOcrScreenTemplateDraftMixin,
    _GalgameContinueAutoAdvanceMixin,
    _GalgameDownloadRapidocrModelsMixin,
    _GalgameEvaluateOcrScreenAwarenessModelMixin,
    _GalgameExplainLineMixin,
    _GalgameGetCharacterListMixin,
    _GalgameGetCharacterProfileMixin,
    _GalgameGetHistoryMixin,
    _GalgameGetOcrScreenAwarenessSnapshotMixin,
    _GalgameGetPushHistoryMixin,
    _GalgameGetRecentLinesMixin,
    _GalgameGetSceneContextMixin,
    _GalgameGetSnapshotMixin,
    _GalgameGetStatusMixin,
    _GalgameGetStorySoFarMixin,
    _GalgameImportCharacterDataMixin,
    _GalgameInstallTextractorMixin,
    _GalgameListMemoryReaderProcessesMixin,
    _GalgameListOcrWindowsMixin,
    _GalgameOpenUiMixin,
    _GalgameRollbackOcrCaptureProfileMixin,
    _GalgameSetCharacterModeMixin,
    _GalgameSetLlmVisionMixin,
    _GalgameSetMemoryReaderTargetMixin,
    _GalgameSetModeMixin,
    _GalgameSetOcrBackendMixin,
    _GalgameSetOcrCaptureProfileMixin,
    _GalgameSetOcrScreenTemplatesMixin,
    _GalgameSetOcrTimingMixin,
    _GalgameSetOcrWindowTargetMixin,
    _GalgameSetRapidocrLangMixin,
    _GalgameSuggestChoiceMixin,
    _GalgameSummarizeSceneMixin,
    _GalgameTrainOcrScreenAwarenessModelMixin,
    _GalgameValidateOcrScreenTemplatesMixin,
    NekoPluginBase,
):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._state_lock = threading.Lock()
        self._poll_bridge_locks: dict[int, asyncio.Lock] = {}
        self._poll_bridge_thread_lock = threading.Lock()
        self._bridge_poll_task_lock = threading.RLock()
        self._textractor_install_lock = threading.Lock()
        # rapidocr/dxcam *install* locks removed: both bundled into main program.
        # rapidocr_models download lock is separate — it's not installing the
        # package, it's pulling the user-selected language pack into the
        # plugin model cache so RapidOCR can serve a non-bundled (lang, version)
        # combo (e.g. japan + PP-OCRv4).
        self._rapidocr_models_lock = threading.Lock()
        self._cfg = None
        self._state = build_initial_state(
            mode=MODE_COMPANION,
            push_notifications=True,
            advance_speed=ADVANCE_SPEED_MEDIUM,
        )
        self._persist = GalgameStore(
            self.data_path("galgame_store.json"),
            self.logger,
        )
        self._config_service = GalgamePluginConfigService(self)
        self._host_agent_adapter: HostAgentAdapter | None = None
        self._llm_gateway: LLMGateway | None = None
        self._game_agent: GameLLMAgent | None = None
        self._memory_reader_manager: MemoryReaderManager | None = None
        self._ocr_reader_manager: OcrReaderManager | None = None
        self._ocr_foreground_advance_monitor_task: asyncio.Task[None] | None = None
        self._ocr_fast_loop_task: asyncio.Task[None] | None = None
        self._ocr_fast_loop_started_at = 0.0
        self._ocr_fast_loop_last_duration_seconds = 0.0
        self._ocr_fast_loop_last_run_at = 0.0
        self._ocr_fast_loop_iteration_count = 0
        self._fast_loop_auto_enabled = False
        self._fast_loop_consecutive_errors = 0
        self._ocr_reader_tick_lock = threading.Lock()
        self._ocr_poll_duration_samples: deque[float] = deque(maxlen=_LATENCY_SAMPLE_LIMIT)
        self._bridge_poll_duration_samples: deque[float] = deque(maxlen=_LATENCY_SAMPLE_LIMIT)
        self._ocr_auto_degrade_reason = ""
        self._ocr_auto_degrade_at = ""
        self._ocr_auto_degrade_count = 0
        self._bridge_poll_task: asyncio.Task[None] | Future[None] | None = None
        self._bridge_poll_loop: asyncio.AbstractEventLoop | None = None
        self._bridge_poll_thread: threading.Thread | None = None
        self._bridge_poll_thread_stop = threading.Event()
        self._bridge_poll_started_at = 0.0
        self._bridge_poll_finished_at = 0.0
        self._last_bridge_poll_duration_seconds = 0.0
        self._last_bridge_poll_launch_at = 0.0
        self._bridge_poll_launch_count = 0
        self._last_agent_tick_at = 0.0
        self._bridge_tick_last_started_at = 0.0
        self._bridge_tick_last_finished_at = 0.0
        self._bridge_tick_last_duration_seconds = 0.0
        self._bridge_tick_last_error = ""
        self._bridge_tick_launch_count = 0
        self._bridge_tick_shutdown_requested = False
        self._pending_ocr_advance_captures = 0
        self._last_ocr_advance_capture_requested_at = 0.0
        self._last_ocr_advance_capture_reason = ""
        self._last_ocr_foreground_refresh_at = 0.0
        self._last_memory_reader_text_game_id = ""
        self._last_memory_reader_text_session_id = ""
        self._last_memory_reader_text_seq = 0
        self._last_memory_reader_text_seen_at_monotonic = 0.0
        self._ocr_capture_profile_auto_apply_enabled = False
        self._ocr_capture_profile_pending_rollback: dict[str, Any] = {}
        self._ocr_capture_profile_last_rollback_reason = ""
        self._state_dirty = True
        self._cached_snapshot: dict[str, Any] | None = None
        self._character_profile_manager: CharacterProfileManager | None = None
        # host-play-mode plan step 19: query entries + rate limit windows.
        # Each entry id maps to a deque of recent timestamps; entries trim past
        # the 60-second window before each call.
        self._story_so_far: str = ""
        self._story_last_updated_seq: int = 0
        self._push_history: deque[dict[str, Any]] = deque(maxlen=64)
        self._query_rate_limits: dict[str, deque[float]] = {
            "galgame_get_recent_lines": deque(maxlen=2),
            "galgame_get_scene_context": deque(maxlen=3),
            "galgame_get_story_so_far": deque(maxlen=1),
            "galgame_get_push_history": deque(maxlen=10),
        }

    def _not_configured_message(self) -> str:
        return self.i18n.t(
            "errors.not_configured",
            default="galgame_plugin 未配置",
        )

    def _install_in_progress_message(self, component: str) -> str:
        return self.i18n.t(
            "errors.install_in_progress",
            default="{component} 安装正在进行中",
            component=component,
        )

    def _install_ok_message(self, component_key: str, component: str) -> str:
        return self.i18n.t(
            f"install.{component_key}.ok",
            default=f"{component} 安装完成",
        )

    def _format_install_entry_error(self, component_key: str, component: str, exc: Exception) -> str:
        message = str(exc or "").strip()
        prefix = self.i18n.t(
            f"install.{component_key}.fail",
            default=f"{component} 安装失败",
        )
        if not message:
            return prefix
        if message.startswith(f"{component} 安装失败"):
            return message
        return f"{prefix}: {message}"

    def _update_memory_reader_text_freshness(
        self,
        runtime: dict[str, Any],
        *,
        now_monotonic: float,
    ) -> bool:
        if self._cfg is None:
            return False
        status = str(runtime.get("status") or "")
        game_id = str(runtime.get("game_id") or "")
        session_id = str(runtime.get("session_id") or "")
        try:
            last_text_seq = int(runtime.get("last_text_seq") or 0)
        except (TypeError, ValueError):
            last_text_seq = 0
        received_text_this_tick = (
            str(runtime.get("detail") or "") == "receiving_text" and last_text_seq > 0
        )
        try:
            threshold = max(
                0.0,
                float(self._cfg.ocr_reader_no_text_takeover_after_seconds),
            )
        except (TypeError, ValueError):
            threshold = 0.0

        with self._state_lock:
            if status not in {"attaching", "active"} or not game_id or not session_id:
                self._last_memory_reader_text_game_id = ""
                self._last_memory_reader_text_session_id = ""
                self._last_memory_reader_text_seq = 0
                self._last_memory_reader_text_seen_at_monotonic = 0.0
                runtime["last_text_recent"] = False
                runtime["last_text_age_seconds"] = 0.0
                return False

            tracked_changed = (
                game_id != self._last_memory_reader_text_game_id
                or session_id != self._last_memory_reader_text_session_id
                or last_text_seq < self._last_memory_reader_text_seq
            )
            if tracked_changed:
                self._last_memory_reader_text_game_id = game_id
                self._last_memory_reader_text_session_id = session_id
                self._last_memory_reader_text_seq = last_text_seq
                self._last_memory_reader_text_seen_at_monotonic = (
                    now_monotonic if received_text_this_tick else 0.0
                )
            elif last_text_seq > self._last_memory_reader_text_seq:
                self._last_memory_reader_text_seq = last_text_seq
                self._last_memory_reader_text_seen_at_monotonic = now_monotonic

            last_seen = self._last_memory_reader_text_seen_at_monotonic
            recent = (
                last_text_seq > 0
                and last_seen > 0.0
                and now_monotonic - last_seen <= threshold
            )
            runtime["last_text_recent"] = recent
            runtime["last_text_age_seconds"] = (
                max(0.0, now_monotonic - last_seen) if last_seen > 0.0 else 0.0
            )
            return recent

    def should_request_ocr_after_advance_capture(self) -> bool:
        return (
            self._cfg is not None
            and bool(self._cfg.ocr_reader_enabled)
            and getattr(self._cfg, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
        )

    def request_ocr_after_advance_capture(self, *, reason: str = "agent_advance") -> None:
        self._request_ocr_after_advance_capture_at(
            requested_at_monotonic=time.monotonic(),
            reason=reason,
        )

    def _request_ocr_after_advance_capture_for_event_age(
        self,
        *,
        event_age_seconds: float,
        reason: str,
        coalesced_count: int = 0,
    ) -> None:
        try:
            event_age = max(0.0, float(event_age_seconds or 0.0))
        except (TypeError, ValueError):
            event_age = 0.0
        self._request_ocr_after_advance_capture_at(
            requested_at_monotonic=time.monotonic() - event_age,
            reason=reason,
            coalesced_count=coalesced_count,
        )

    def _request_ocr_after_advance_capture_at(
        self,
        *,
        requested_at_monotonic: float,
        reason: str,
        coalesced_count: int = 0,
    ) -> None:
        del coalesced_count
        if not self.should_request_ocr_after_advance_capture():
            return
        with self._state_lock:
            self._pending_ocr_advance_captures = min(
                self._pending_ocr_advance_captures + 1,
                8,
            )
            self._last_ocr_advance_capture_requested_at = float(
                requested_at_monotonic or time.monotonic()
            )
            self._last_ocr_advance_capture_reason = str(reason or "agent_advance")
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None
        # _state_lock → _bridge_poll_task_lock 路径；反向路径在
        # _start_background_bridge_poll:2215-2218。均运行在 asyncio 单线程下安全，
        # 新增后台线程代码路径时需审计锁序。
        self._start_background_bridge_poll()

    def latest_ocr_vision_snapshot(self) -> dict[str, Any]:
        if self._ocr_reader_manager is None:
            return {}
        snapshot_getter = getattr(self._ocr_reader_manager, "latest_vision_snapshot", None)
        if not callable(snapshot_getter):
            return {}
        return snapshot_getter()

    def _has_pending_ocr_advance_capture(self) -> bool:
        with self._state_lock:
            return self._pending_ocr_advance_captures > 0

    def _pending_ocr_advance_capture_delay_remaining(self) -> float:
        with self._state_lock:
            if self._pending_ocr_advance_captures <= 0:
                return 0.0
            requested_at = float(self._last_ocr_advance_capture_requested_at or 0.0)
        if requested_at <= 0.0:
            return 0.0
        elapsed = max(0.0, time.monotonic() - requested_at)
        return max(0.0, _OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS - elapsed)

    def _pending_ocr_advance_capture_age(self) -> float:
        with self._state_lock:
            if self._pending_ocr_advance_captures <= 0:
                return 0.0
            requested_at = float(self._last_ocr_advance_capture_requested_at or 0.0)
        if requested_at <= 0.0:
            return 0.0
        return max(0.0, time.monotonic() - requested_at)

    def _consume_ocr_advance_capture(self) -> None:
        with self._state_lock:
            if self._pending_ocr_advance_captures > 0:
                self._pending_ocr_advance_captures -= 1

    def _clear_pending_ocr_advance_captures_locked(self) -> None:
        self._pending_ocr_advance_captures = 0
        self._last_ocr_advance_capture_requested_at = 0.0
        self._last_ocr_advance_capture_reason = ""

    def _clear_pending_ocr_advance_captures(self) -> None:
        with self._state_lock:
            self._clear_pending_ocr_advance_captures_locked()

    def _snapshot_state(
        self,
        *,
        fresh: bool = False,
        include_private_context: bool = False,
    ) -> dict[str, Any]:
        with self._state_lock:
            if (
                not include_private_context
                and not fresh
                and not self._state_dirty
                and self._cached_snapshot is not None
            ):
                return self._cached_snapshot
            state = self._state
            raw = {
                "bound_game_id": state.bound_game_id,
                "available_game_ids": list(state.available_game_ids),
                "mode": state.mode,
                "push_notifications": state.push_notifications,
                "advance_speed": state.advance_speed,
                "active_game_id": state.active_game_id,
                "active_session_id": state.active_session_id,
                "active_session_meta": dict(state.active_session_meta),
                "active_data_source": state.active_data_source,
                "latest_snapshot": dict(state.latest_snapshot),
                "history_events": list(state.history_events),
                "history_lines": list(state.history_lines),
                "history_observed_lines": list(state.history_observed_lines),
                "history_choices": list(state.history_choices),
                "screen_type": state.screen_type,
                "screen_ui_elements": list(state.screen_ui_elements),
                "screen_confidence": state.screen_confidence,
                "screen_debug": dict(state.screen_debug),
                "dedupe_window": list(state.dedupe_window),
                "line_buffer": state.line_buffer,
                "stream_reset_pending": state.stream_reset_pending,
                "last_error": dict(state.last_error),
                "next_poll_at_monotonic": state.next_poll_at_monotonic,
                "current_connection_state": state.current_connection_state,
                "events_byte_offset": state.events_byte_offset,
                "events_file_size": state.events_file_size,
                "last_seq": state.last_seq,
                "last_seen_data_monotonic": state.last_seen_data_monotonic,
                "warmup_session_id": state.warmup_session_id,
                "memory_reader_runtime": dict(state.memory_reader_runtime),
                "memory_reader_target": dict(state.memory_reader_target),
                "ocr_reader_runtime": dict(state.ocr_reader_runtime),
                "ocr_capture_profiles": dict(state.ocr_capture_profiles),
                "ocr_window_target": dict(state.ocr_window_target),
                "context_snapshot": dict(state.context_snapshot),
                "character_profiles": dict(state.character_profiles),
                "active_scene_characters": list(state.active_scene_characters),
                "character_profile_version": state.character_profile_version,
                "character_profile_game_id": state.character_profile_game_id,
                "character_profile_match_reason": state.character_profile_match_reason,
                "character_mode": state.character_mode,
                "character_fixed_name": state.character_fixed_name,
                "character_mode_stale": state.character_mode_stale,
                "cross_scene_memory": dict(state.cross_scene_memory),
                "character_runtime_state": dict(state.character_runtime_state),
                "last_push_seq": state.last_push_seq,
                "plugin_error": state.plugin_error,
                "dependency_status": dict(state.dependency_status),
            }
            should_cache = not fresh and not include_private_context
            if should_cache:
                self._state_dirty = False
                self._cached_snapshot = None
        snap = {
            "bound_game_id": raw["bound_game_id"],
            "available_game_ids": raw["available_game_ids"],
            "mode": raw["mode"],
            "push_notifications": raw["push_notifications"],
            "advance_speed": raw["advance_speed"],
            "active_game_id": raw["active_game_id"],
            "active_session_id": raw["active_session_id"],
            "active_session_meta": json_copy(raw["active_session_meta"]),
            "active_data_source": raw["active_data_source"],
            "latest_snapshot": json_copy(raw["latest_snapshot"]),
            "history_events": json_copy(raw["history_events"]),
            "history_lines": json_copy(raw["history_lines"]),
            "history_observed_lines": json_copy(raw["history_observed_lines"]),
            "history_choices": json_copy(raw["history_choices"]),
            "screen_type": raw["screen_type"],
            "screen_ui_elements": json_copy(raw["screen_ui_elements"]),
            "screen_confidence": raw["screen_confidence"],
            "screen_debug": json_copy(raw["screen_debug"]),
            "dedupe_window": json_copy(raw["dedupe_window"]),
            "line_buffer": raw["line_buffer"],
            "stream_reset_pending": raw["stream_reset_pending"],
            "last_error": json_copy(raw["last_error"]),
            "next_poll_at_monotonic": raw["next_poll_at_monotonic"],
            "current_connection_state": raw["current_connection_state"],
            "events_byte_offset": raw["events_byte_offset"],
            "events_file_size": raw["events_file_size"],
            "last_seq": raw["last_seq"],
            "last_seen_data_monotonic": raw["last_seen_data_monotonic"],
            "warmup_session_id": raw["warmup_session_id"],
            "memory_reader_runtime": json_copy(raw["memory_reader_runtime"]),
            "memory_reader_target": json_copy(raw["memory_reader_target"]),
            "ocr_reader_runtime": json_copy(raw["ocr_reader_runtime"]),
            "ocr_capture_profiles": json_copy(raw["ocr_capture_profiles"]),
            "ocr_window_target": json_copy(raw["ocr_window_target"]),
            "context_snapshot": json_copy(raw["context_snapshot"])
            if include_private_context
            else _public_context_snapshot(raw["context_snapshot"]),
            "character_profiles": json_copy(raw["character_profiles"]),
            "active_scene_characters": json_copy(raw["active_scene_characters"]),
            "character_profile_version": raw["character_profile_version"],
            "character_profile_game_id": raw["character_profile_game_id"],
            "character_profile_match_reason": raw["character_profile_match_reason"],
            "character_mode": raw["character_mode"],
            "character_fixed_name": raw["character_fixed_name"],
            "character_mode_stale": raw["character_mode_stale"],
            "cross_scene_memory": json_copy(raw["cross_scene_memory"]),
            "character_runtime_state": json_copy(raw["character_runtime_state"]),
            "last_push_seq": raw["last_push_seq"],
            "plugin_error": raw["plugin_error"],
            "dependency_status": json_copy(raw["dependency_status"]),
        }
        if should_cache:
            with self._state_lock:
                if not self._state_dirty:
                    self._cached_snapshot = snap
            return snap
        return snap

    def _mark_state_dirty(self) -> None:
        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

    @staticmethod
    def _ocr_capture_scope_label(save_scope: str) -> str:
        if save_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            return "当前窗口分辨率"
        return "进程通用回退"

    @staticmethod
    def _ocr_capture_stage_label(stage: str) -> str:
        labels = {
            OCR_CAPTURE_PROFILE_STAGE_DEFAULT: "通用区域",
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: "对白区",
            OCR_CAPTURE_PROFILE_STAGE_MENU: "菜单区",
            OCR_CAPTURE_PROFILE_STAGE_TITLE: "标题/主菜单",
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD: "存读档",
            OCR_CAPTURE_PROFILE_STAGE_CONFIG: "设置",
            OCR_CAPTURE_PROFILE_STAGE_TRANSITION: "转场",
            OCR_CAPTURE_PROFILE_STAGE_GALLERY: "回想/鉴赏",
            OCR_CAPTURE_PROFILE_STAGE_MINIGAME: "小游戏",
            OCR_CAPTURE_PROFILE_STAGE_GAME_OVER: "Game Over",
        }
        return labels.get(stage, stage)

    @staticmethod
    def _process_name_matches(left: str, right: str) -> bool:
        return bool(left.strip()) and left.strip().lower() == right.strip().lower()

    def _resolve_ocr_capture_profile_save_context(
        self,
        *,
        process_name: str,
        save_scope: str | None,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
        runtime_process_name = str(runtime.get("process_name") or "").strip()
        runtime_width = max(0, int(runtime.get("width") or 0))
        runtime_height = max(0, int(runtime.get("height") or 0))
        resolved_width = max(0, int(width or runtime_width))
        resolved_height = max(0, int(height or runtime_height))
        normalized_scope = _normalize_ocr_capture_profile_save_scope(save_scope)
        if not normalized_scope:
            explicit_window_size = int(width or 0) > 0 and int(height or 0) > 0
            normalized_scope = (
                OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET
                if explicit_window_size
                and self._process_name_matches(process_name, runtime_process_name)
                and resolved_width > 0
                and resolved_height > 0
                else OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK
            )
        bucket_key = ""
        aspect_ratio = 0.0
        if normalized_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            if resolved_width <= 0 or resolved_height <= 0:
                raise ValueError("当前没有可用的 OCR 窗口尺寸，无法保存到当前窗口分辨率")
            bucket_key = build_ocr_capture_profile_bucket_key(resolved_width, resolved_height).lower()
            aspect_ratio = compute_ocr_window_aspect_ratio(resolved_width, resolved_height)
        return {
            "save_scope": normalized_scope,
            "width": resolved_width,
            "height": resolved_height,
            "bucket_key": bucket_key,
            "aspect_ratio": aspect_ratio,
            "runtime": runtime,
        }

    async def _save_ocr_capture_profile_payload(
        self,
        *,
        process_name: str,
        stage: str,
        capture_profile: dict[str, float] | None,
        clear: bool,
        save_scope: str | None,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        normalized_process_name = str(process_name or "").strip()
        if not normalized_process_name:
            raise ValueError("process_name is required")
        normalized_stage = _normalize_ocr_capture_profile_stage(stage)
        context = self._resolve_ocr_capture_profile_save_context(
            process_name=normalized_process_name,
            save_scope=save_scope,
            width=width,
            height=height,
        )
        with self._state_lock:
            profiles = json_copy(self._state.ocr_capture_profiles)
        existing_entry = profiles.get(normalized_process_name)
        process_stage_map = _capture_profile_entry_to_stage_map(existing_entry)
        window_bucket_map = _capture_profile_entry_to_window_bucket_map(existing_entry)
        normalized_profile = json_copy(capture_profile or {})
        resolved_scope = str(context["save_scope"] or OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK)
        bucket_key = str(context.get("bucket_key") or "")
        if resolved_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK:
            target_stage_map = process_stage_map
        else:
            bucket_entry = window_bucket_map.get(bucket_key) or {
                "width": int(context.get("width") or 0),
                "height": int(context.get("height") or 0),
                "aspect_ratio": float(context.get("aspect_ratio") or 0.0),
                "stages": {},
            }
            target_stage_map = _capture_profile_bucket_entry_to_stage_map(bucket_entry)
        if clear:
            target_stage_map.pop(normalized_stage, None)
        else:
            target_stage_map[normalized_stage] = normalized_profile
        if resolved_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            if target_stage_map:
                window_bucket_map[bucket_key] = {
                    "width": int(context.get("width") or 0),
                    "height": int(context.get("height") or 0),
                    "aspect_ratio": float(context.get("aspect_ratio") or 0.0),
                    "stages": target_stage_map,
                }
            else:
                window_bucket_map.pop(bucket_key, None)
        if not process_stage_map and not window_bucket_map:
            profiles.pop(normalized_process_name, None)
        else:
            profiles[normalized_process_name] = _capture_profile_components_to_entry(
                process_stage_map,
                window_bucket_map,
            )
        self._persist.persist_ocr_capture_profiles(profiles)
        with self._state_lock:
            self._state.ocr_capture_profiles = json_copy(profiles)
            self._state_dirty = True
            self._cached_snapshot = None
        if self._ocr_reader_manager is not None:
            self._ocr_reader_manager.update_capture_profiles(profiles)
            try:
                refreshed_runtime = (
                    self._ocr_reader_manager.refresh_runtime_capture_profile_selection()
                )
            except Exception as exc:
                self.logger.warning(
                    "galgame_plugin failed to refresh OCR runtime after saving capture profile: {}",
                    exc,
                )
            else:
                with self._state_lock:
                    self._state.ocr_reader_runtime = (
                        _merge_ocr_runtime_preserving_bridge_diagnostics(
                            refreshed_runtime,
                            self._state.ocr_reader_runtime,
                        )
                    )
                    self._state_dirty = True
                    self._cached_snapshot = None
        payload = {
            "process_name": normalized_process_name,
            "stage": normalized_stage,
            "capture_profile": normalized_profile if not clear else {},
            "cleared": bool(clear),
            "save_scope": resolved_scope,
            "bucket_key": bucket_key,
            "window_width": int(context.get("width") or 0),
            "window_height": int(context.get("height") or 0),
        }
        scope_label = self._ocr_capture_scope_label(resolved_scope)
        stage_label = self._ocr_capture_stage_label(normalized_stage)
        if clear:
            payload["summary"] = (
                f"OCR 截图校准已清空：{normalized_process_name} / {stage_label} / {scope_label}"
                + (f" / {bucket_key}" if bucket_key else "")
            )
        else:
            payload["summary"] = (
                f"OCR 截图校准已保存：{normalized_process_name} / {stage_label} / {scope_label}"
                + (f" / {bucket_key}" if bucket_key else "")
            )
        payload["status"] = await self._build_status_payload_async()
        return payload

    def _ocr_screen_template_runtime_context(self) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
            screen_type = str(self._state.screen_type or "")
            ui_elements = json_copy(self._state.screen_ui_elements)
        return runtime, screen_type, ui_elements if isinstance(ui_elements, list) else []

    @staticmethod
    def _ocr_template_keyword_candidates(
        runtime: dict[str, Any],
        ui_elements: list[dict[str, Any]],
    ) -> list[str]:
        candidates: list[str] = []
        for element in ui_elements[:10]:
            if not isinstance(element, dict):
                continue
            text = str(element.get("text") or "").strip()
            if text:
                candidates.append(text)
        raw_text = str(runtime.get("last_raw_ocr_text") or "")
        for line in raw_text.splitlines():
            text = line.strip()
            if text:
                candidates.append(text)
        result: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text = re.sub(r"\s+", " ", item).strip()
            if len(text) < 2 or len(text) > 48:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= 8:
                break
        return result

    @staticmethod
    def _ocr_screen_template_id(
        *,
        process_name: str,
        stage: str,
        width: int,
        height: int,
    ) -> str:
        stem = Path(process_name).stem if process_name else "current"
        base = f"{stem}-{stage}"
        if width > 0 and height > 0:
            base = f"{base}-{width}x{height}"
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-").lower()
        return (normalized or "screen-template")[:80]

    @staticmethod
    def _normalize_ocr_template_region_payload(region: object) -> dict[str, Any]:
        if not isinstance(region, dict):
            return {}
        try:
            left = float(region.get("left"))
            top = float(region.get("top"))
            right = float(region.get("right"))
            bottom = float(region.get("bottom"))
        except (TypeError, ValueError):
            return {}
        left = max(0.0, min(left, 1.0))
        top = max(0.0, min(top, 1.0))
        right = max(0.0, min(right, 1.0))
        bottom = max(0.0, min(bottom, 1.0))
        if right <= left or bottom <= top:
            return {}
        return {
            "id": str(region.get("id") or "visual-region-1").strip()[:80],
            "role": str(region.get("role") or "ui_region").strip()[:40],
            "left": round(left, 4),
            "top": round(top, 4),
            "right": round(right, 4),
            "bottom": round(bottom, 4),
            "min_overlap": 0.35,
        }

    def _build_ocr_screen_template_draft_payload(
        self,
        *,
        stage: str | None = None,
        region: object = None,
    ) -> dict[str, Any]:
        runtime, screen_type, ui_elements = self._ocr_screen_template_runtime_context()
        process_name = str(
            runtime.get("process_name")
            or runtime.get("effective_process_name")
            or ""
        ).strip()
        window_title = str(
            runtime.get("window_title")
            or runtime.get("effective_window_title")
            or ""
        ).strip()
        try:
            width = int(runtime.get("width") or 0)
            height = int(runtime.get("height") or 0)
        except (TypeError, ValueError):
            width = 0
            height = 0
        resolved_stage = normalize_screen_type(stage)
        if not resolved_stage or resolved_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            resolved_stage = normalize_screen_type(screen_type)
        if not resolved_stage or resolved_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            resolved_stage = normalize_screen_type(runtime.get("capture_stage"))
        if not resolved_stage or resolved_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            resolved_stage = OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
        keywords = self._ocr_template_keyword_candidates(runtime, ui_elements)
        normalized_region = self._normalize_ocr_template_region_payload(region)
        draft: dict[str, Any] = {
            "id": self._ocr_screen_template_id(
                process_name=process_name,
                stage=resolved_stage,
                width=width,
                height=height,
            ),
            "stage": resolved_stage,
            "priority": 100,
            "keywords": keywords,
            "min_keyword_hits": 1 if keywords else 0,
        }
        if normalized_region:
            draft["regions"] = [normalized_region]
            draft["min_region_hits"] = 1
        if process_name:
            draft["process_names"] = [process_name]
        if window_title:
            draft["window_title_contains"] = [window_title[:80]]
        if width > 0 and height > 0:
            draft["width"] = width
            draft["height"] = height
            draft["resolution_tolerance"] = 8
        if not keywords:
            draft["match_without_keywords"] = True
        sanitized = build_config({"ocr_reader": {"screen_templates": [draft]}}).ocr_reader_screen_templates
        if sanitized:
            draft = sanitized[0]
        return {
            "template": draft,
            "context": {
                "process_name": process_name,
                "window_title": window_title,
                "width": width,
                "height": height,
                "screen_type": screen_type,
                "capture_stage": str(runtime.get("capture_stage") or ""),
            },
        }

    def _resolve_screen_awareness_data_path(
        self,
        raw_path: str,
        *,
        default_filename: str,
    ) -> Path:
        if self._cfg is None:
            raise ValueError(self._not_configured_message())
        raw = str(raw_path or "").strip()
        if raw:
            path = Path(os.path.expandvars(os.path.expanduser(raw)))
            if not path.is_absolute():
                path = Path(self._cfg.bridge_root) / path
            return path
        sample_path = ""
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
        if isinstance(runtime, dict):
            sample_path = str(runtime.get("screen_awareness_sample_last_path") or "")
        if sample_path:
            return Path(sample_path)
        return Path(self._cfg.bridge_root) / "_screen_awareness_samples" / default_filename

    def _current_ocr_screen_template_validation_payload(
        self,
        screen_templates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sanitized = build_config(
            {"ocr_reader": {"screen_templates": screen_templates}}
        ).ocr_reader_screen_templates
        runtime, _screen_type, _ui_elements = self._ocr_screen_template_runtime_context()
        context = {
            "process_name": str(
                runtime.get("process_name")
                or runtime.get("effective_process_name")
                or ""
            ),
            "window_title": str(
                runtime.get("window_title")
                or runtime.get("effective_window_title")
                or ""
            ),
            "width": int(runtime.get("width") or 0),
            "height": int(runtime.get("height") or 0),
            "game_id": str(runtime.get("game_id") or ""),
        }
        classification = classify_screen_from_ocr(
            str(runtime.get("last_raw_ocr_text") or ""),
            screen_templates=sanitized,
            template_context=context,
        )
        return {
            "screen_templates": json_copy(sanitized),
            "classification": classification.to_payload(),
            "context": context,
            "summary": (
                f"OCR screen templates validated={len(sanitized)} "
                f"result={classification.screen_type} "
                f"confidence={classification.confidence:.2f}"
            ),
        }

    def _saved_ocr_capture_profile_payload(
        self,
        *,
        process_name: str,
        stage: str,
        save_scope: str,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        normalized_process_name = str(process_name or "").strip()
        normalized_stage = _normalize_ocr_capture_profile_stage(stage)
        context = self._resolve_ocr_capture_profile_save_context(
            process_name=normalized_process_name,
            save_scope=save_scope,
            width=width,
            height=height,
        )
        with self._state_lock:
            profiles = json_copy(self._state.ocr_capture_profiles)
        existing_entry = profiles.get(normalized_process_name)
        if context["save_scope"] == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            bucket = _capture_profile_entry_to_window_bucket_map(existing_entry).get(
                str(context.get("bucket_key") or "")
            )
            stage_map = _capture_profile_bucket_entry_to_stage_map(bucket)
        else:
            stage_map = _capture_profile_entry_to_stage_map(existing_entry)
        profile = stage_map.get(normalized_stage)
        return {
            "profile": json_copy(profile) if isinstance(profile, dict) else {},
            "exists": isinstance(profile, dict),
            "context": context,
        }

    def _recommended_ocr_capture_profile_from_runtime(
        self,
        runtime: dict[str, Any],
        *,
        allow_manual_override: bool,
    ) -> dict[str, Any]:
        profile = runtime.get("recommended_capture_profile")
        if not isinstance(profile, dict) or not profile:
            profile = (runtime.get("profile") or {}).get("recommended_capture_profile") if isinstance(runtime.get("profile"), dict) else {}
        normalized_profile = _normalize_ocr_capture_profile_payload(profile)
        manual_present = bool(
            runtime.get("recommended_capture_profile_manual_present")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_manual_present")
                if isinstance(runtime.get("profile"), dict)
                else False
            )
        )
        if manual_present and not allow_manual_override:
            raise ValueError("当前已有手动 OCR 截图校准；推荐不会自动覆盖手动 profile")
        stage = str(
            runtime.get("recommended_capture_profile_stage")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_stage")
                if isinstance(runtime.get("profile"), dict)
                else ""
            )
            or OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
        )
        save_scope = str(
            runtime.get("recommended_capture_profile_save_scope")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_save_scope")
                if isinstance(runtime.get("profile"), dict)
                else ""
            )
            or OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET
        )
        process_name = str(
            runtime.get("recommended_capture_profile_process_name")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_process_name")
                if isinstance(runtime.get("profile"), dict)
                else ""
            )
            or runtime.get("process_name")
            or runtime.get("effective_process_name")
            or ""
        ).strip()
        if not process_name:
            raise ValueError("当前推荐缺少 process_name")
        return {
            "process_name": process_name,
            "stage": _normalize_ocr_capture_profile_stage(stage),
            "save_scope": _normalize_ocr_capture_profile_save_scope(save_scope)
            or OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
            "capture_profile": normalized_profile,
            "width": int(runtime.get("width") or 0),
            "height": int(runtime.get("height") or 0),
            "reason": str(runtime.get("recommended_capture_profile_reason") or ""),
            "confidence": float(runtime.get("recommended_capture_profile_confidence") or 0.0),
        }

    async def _apply_recommended_ocr_capture_profile_payload(
        self,
        runtime: dict[str, Any],
        *,
        allow_manual_override: bool,
        reason: str,
    ) -> dict[str, Any]:
        recommendation = self._recommended_ocr_capture_profile_from_runtime(
            runtime,
            allow_manual_override=allow_manual_override,
        )
        previous = self._saved_ocr_capture_profile_payload(
            process_name=recommendation["process_name"],
            stage=recommendation["stage"],
            save_scope=recommendation["save_scope"],
            width=int(recommendation["width"] or 0),
            height=int(recommendation["height"] or 0),
        )
        payload = await self._save_ocr_capture_profile_payload(
            process_name=recommendation["process_name"],
            stage=recommendation["stage"],
            capture_profile=dict(recommendation["capture_profile"]),
            clear=False,
            save_scope=recommendation["save_scope"],
            width=int(recommendation["width"] or 0),
            height=int(recommendation["height"] or 0),
        )
        self._ocr_capture_profile_pending_rollback = {
            "process_name": recommendation["process_name"],
            "stage": recommendation["stage"],
            "save_scope": recommendation["save_scope"],
            "width": int(recommendation["width"] or 0),
            "height": int(recommendation["height"] or 0),
            "previous_profile": json_copy(previous["profile"]),
            "previous_exists": bool(previous["exists"]),
            "applied_profile": json_copy(recommendation["capture_profile"]),
            "applied_at": time.monotonic(),
            "failure_count": 0,
            "reason": reason or recommendation["reason"] or "recommended_capture_profile",
        }
        self._ocr_capture_profile_last_rollback_reason = ""
        payload["rollback_pending"] = True
        payload["auto_apply_enabled"] = bool(self._ocr_capture_profile_auto_apply_enabled)
        payload["summary"] = (
            f"OCR 推荐截图校准已应用：{recommendation['process_name']} / "
            f"{self._ocr_capture_stage_label(recommendation['stage'])}"
        )
        return payload

    async def _rollback_pending_ocr_capture_profile(
        self,
        *,
        reason: str,
    ) -> dict[str, Any] | None:
        pending = dict(self._ocr_capture_profile_pending_rollback or {})
        if not pending:
            self._ocr_capture_profile_last_rollback_reason = reason or "no_pending_rollback"
            return None
        previous_profile = pending.get("previous_profile")
        previous_exists = bool(pending.get("previous_exists"))
        payload = await self._save_ocr_capture_profile_payload(
            process_name=str(pending.get("process_name") or ""),
            stage=str(pending.get("stage") or OCR_CAPTURE_PROFILE_STAGE_DIALOGUE),
            capture_profile=dict(previous_profile or {}) if previous_exists else None,
            clear=not previous_exists,
            save_scope=str(pending.get("save_scope") or OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET),
            width=int(pending.get("width") or 0),
            height=int(pending.get("height") or 0),
        )
        self._ocr_capture_profile_pending_rollback = {}
        self._ocr_capture_profile_last_rollback_reason = reason or "recommended_profile_rollback"
        payload["rollback_reason"] = self._ocr_capture_profile_last_rollback_reason
        payload["summary"] = f"OCR 推荐截图校准已回滚：{payload['rollback_reason']}"
        return payload

    async def _maybe_auto_apply_recommended_ocr_capture_profile(
        self,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._ocr_capture_profile_auto_apply_enabled:
            return runtime
        if self._ocr_capture_profile_pending_rollback:
            return runtime
        profile = runtime.get("recommended_capture_profile")
        if not isinstance(profile, dict) or not profile:
            return runtime
        if bool(runtime.get("recommended_capture_profile_manual_present")):
            return runtime
        confidence = float(runtime.get("recommended_capture_profile_confidence") or 0.0)
        if confidence < 0.65:
            return runtime
        try:
            payload = await self._apply_recommended_ocr_capture_profile_payload(
                runtime,
                allow_manual_override=False,
                reason="auto_apply_recommended_capture_profile",
            )
        except Exception as exc:
            self._ocr_capture_profile_last_rollback_reason = f"auto_apply_failed: {exc}"
            return runtime
        status = payload.get("status")
        if isinstance(status, dict) and isinstance(status.get("ocr_reader_runtime"), dict):
            return json_copy(status["ocr_reader_runtime"])
        return runtime

    async def _update_ocr_capture_profile_rollback_state(
        self,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        pending = self._ocr_capture_profile_pending_rollback
        if not pending:
            return runtime
        detail = str(runtime.get("detail") or "")
        diagnostic_required = bool(runtime.get("ocr_capture_diagnostic_required"))
        consecutive_no_text = int(runtime.get("consecutive_no_text_polls") or 0)
        stable_line = runtime.get("last_stable_line")
        observed_line = runtime.get("last_observed_line")
        has_text = (
            isinstance(stable_line, dict)
            and bool(str(stable_line.get("text") or "").strip())
        ) or (
            isinstance(observed_line, dict)
            and bool(str(observed_line.get("text") or "").strip())
        )
        if detail == "receiving_text" and has_text and consecutive_no_text <= 0:
            self._ocr_capture_profile_pending_rollback = {}
            self._ocr_capture_profile_last_rollback_reason = "recommended_profile_confirmed"
            return runtime
        failed = (
            detail == "capture_failed"
            or diagnostic_required
            or consecutive_no_text >= 3
        )
        if not failed:
            return runtime
        pending["failure_count"] = int(pending.get("failure_count") or 0) + 1
        if int(pending["failure_count"]) < 2:
            return runtime
        payload = await self._rollback_pending_ocr_capture_profile(
            reason=f"recommended_profile_failed:{detail or 'no_text'}",
        )
        if isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, dict) and isinstance(status.get("ocr_reader_runtime"), dict):
                return json_copy(status["ocr_reader_runtime"])
        return runtime

    def _commit_state(self, payload: dict[str, Any]) -> None:
        cross_scene_memory_to_persist: dict[str, Any] | None = None
        character_runtime_state_to_persist: dict[str, Any] | None = None
        copy_json = _package_json_copy
        with self._state_lock:
            state = self._state
            changed = False

            def assign(name: str, value: Any) -> None:
                nonlocal changed
                if getattr(state, name) != value:
                    setattr(state, name, value)
                    changed = True

            def assign_json(name: str, value: Any) -> None:
                nonlocal changed
                if getattr(state, name) != value:
                    setattr(state, name, copy_json(value))
                    changed = True

            commit_base = payload.get("_commit_base")
            if not isinstance(commit_base, dict):
                commit_base = {}

            def live_changed_since_snapshot(name: str) -> bool:
                return name in commit_base and getattr(state, name) != commit_base.get(name)

            def assign_if_live_unchanged(name: str, value: Any) -> None:
                if live_changed_since_snapshot(name):
                    return
                assign(name, value)

            def assign_json_if_live_unchanged(name: str, value: Any) -> None:
                if live_changed_since_snapshot(name):
                    return
                assign_json(name, value)

            assign_if_live_unchanged("bound_game_id", str(payload["bound_game_id"]))
            assign("available_game_ids", list(payload["available_game_ids"]))
            # Preferences can be changed through plugin entries while a bridge poll is in
            # flight. Keep the live values instead of restoring the poll's stale snapshot.
            if not live_changed_since_snapshot("mode"):
                assign("mode", state.mode if state.mode in MODES else str(payload["mode"]))
            if not live_changed_since_snapshot("push_notifications"):
                assign("push_notifications", bool(state.push_notifications))
            if not live_changed_since_snapshot("advance_speed"):
                assign("advance_speed", (
                    state.advance_speed
                    if state.advance_speed in ADVANCE_SPEEDS
                    else str(payload.get("advance_speed") or ADVANCE_SPEED_MEDIUM)
                ))
            character_mode = str(payload.get("character_mode", state.character_mode) or "off")
            if character_mode not in {"off", "fixed"}:
                character_mode = "off"
            assign_json_if_live_unchanged(
                "character_profiles",
                payload.get("character_profiles", state.character_profiles),
            )
            assign_json_if_live_unchanged(
                "active_scene_characters",
                payload.get("active_scene_characters", state.active_scene_characters),
            )
            assign_if_live_unchanged(
                "character_profile_version",
                str(payload.get("character_profile_version", state.character_profile_version) or ""),
            )
            assign_if_live_unchanged(
                "character_profile_game_id",
                str(payload.get("character_profile_game_id", state.character_profile_game_id) or ""),
            )
            assign_if_live_unchanged(
                "character_profile_match_reason",
                str(
                    payload.get(
                        "character_profile_match_reason",
                        state.character_profile_match_reason,
                    )
                    or ""
                ),
            )
            assign_if_live_unchanged("character_mode", character_mode)
            assign_if_live_unchanged(
                "character_fixed_name",
                str(payload.get("character_fixed_name", state.character_fixed_name) or ""),
            )
            assign_if_live_unchanged(
                "character_mode_stale",
                bool(payload.get("character_mode_stale", state.character_mode_stale)),
            )
            if not live_changed_since_snapshot("cross_scene_memory"):
                cross_scene_memory_value = payload.get(
                    "cross_scene_memory",
                    state.cross_scene_memory,
                )
                if state.cross_scene_memory != cross_scene_memory_value:
                    cross_scene_memory_to_persist = copy_json(cross_scene_memory_value)
                    state.cross_scene_memory = copy_json(cross_scene_memory_to_persist)
                    changed = True
            if not live_changed_since_snapshot("character_runtime_state"):
                character_runtime_state_value = payload.get(
                    "character_runtime_state",
                    state.character_runtime_state,
                )
                if state.character_runtime_state != character_runtime_state_value:
                    character_runtime_state_to_persist = copy_json(
                        character_runtime_state_value
                    )
                    state.character_runtime_state = copy_json(
                        character_runtime_state_to_persist
                    )
                    changed = True
            try:
                last_push_seq = max(0, int(payload.get("last_push_seq", state.last_push_seq) or 0))
            except (TypeError, ValueError):
                last_push_seq = state.last_push_seq
            assign_if_live_unchanged("last_push_seq", last_push_seq)
            assign("active_game_id", str(payload["active_game_id"]))
            assign("active_session_id", str(payload["active_session_id"]))
            assign_json("active_session_meta", payload["active_session_meta"])
            assign_if_live_unchanged("active_data_source", str(payload["active_data_source"]))
            assign_json("latest_snapshot", payload["latest_snapshot"])
            snapshot_obj = payload.get("latest_snapshot")
            snapshot_state = snapshot_obj if isinstance(snapshot_obj, dict) else {}
            assign("screen_type", str(snapshot_state.get("screen_type") or ""))
            assign_json(
                "screen_ui_elements",
                snapshot_state.get("screen_ui_elements") if isinstance(snapshot_state.get("screen_ui_elements"), list) else [],
            )
            try:
                screen_confidence = float(snapshot_state.get("screen_confidence") or 0.0)
            except (TypeError, ValueError):
                screen_confidence = 0.0
            assign("screen_confidence", screen_confidence)
            assign_json(
                "screen_debug",
                snapshot_state.get("screen_debug") if isinstance(snapshot_state.get("screen_debug"), dict) else {},
            )
            assign_json("history_events", payload["history_events"])
            assign_json("history_lines", payload["history_lines"])
            assign_json("history_observed_lines", payload.get("history_observed_lines", []))
            assign_json("history_choices", payload["history_choices"])
            assign_json("dedupe_window", payload["dedupe_window"])
            assign("line_buffer", payload["line_buffer"])
            assign("stream_reset_pending", bool(payload["stream_reset_pending"]))
            assign_json("last_error", payload["last_error"])
            assign("next_poll_at_monotonic", float(payload["next_poll_at_monotonic"]))
            assign("current_connection_state", str(payload["current_connection_state"]))
            assign("events_byte_offset", int(payload["events_byte_offset"]))
            assign("events_file_size", int(payload["events_file_size"]))
            assign("last_seq", int(payload["last_seq"]))
            assign("last_seen_data_monotonic", float(payload["last_seen_data_monotonic"]))
            assign("warmup_session_id", str(payload["warmup_session_id"]))
            assign_json("memory_reader_runtime", payload["memory_reader_runtime"])
            assign_json_if_live_unchanged("memory_reader_target", payload["memory_reader_target"])
            assign_json("ocr_reader_runtime", payload["ocr_reader_runtime"])
            assign_json_if_live_unchanged("ocr_capture_profiles", payload["ocr_capture_profiles"])
            assign_json_if_live_unchanged("ocr_window_target", payload["ocr_window_target"])
            context_snapshot = payload.get("context_snapshot", state.context_snapshot)
            if isinstance(context_snapshot, dict):
                preserve_private_context = (
                    context_snapshot
                    and "summary_seed" not in context_snapshot
                    and "stable_line_ids" not in context_snapshot
                )
                existing_context_snapshot = state.context_snapshot
                has_private_context = isinstance(existing_context_snapshot, dict) and (
                    str(existing_context_snapshot.get("summary_seed") or "").strip()
                    or list(existing_context_snapshot.get("stable_line_ids") or [])
                )
                if not (preserve_private_context and has_private_context):
                    assign_json("context_snapshot", context_snapshot)
            assign("plugin_error", str(payload["plugin_error"]))
            assign_json_if_live_unchanged(
                "dependency_status",
                payload.get("dependency_status", self._state.dependency_status),
            )
            if changed:
                self._state_dirty = True
                self._cached_snapshot = None
        if cross_scene_memory_to_persist is not None:
            try:
                self._persist.persist_config_override(
                    STORE_CROSS_SCENE_MEMORY,
                    cross_scene_memory_to_persist,
                )
            except Exception:  # noqa: BLE001
                self.logger.warning(
                    "failed to persist galgame strategy memory state key=%s",
                    STORE_CROSS_SCENE_MEMORY,
                    exc_info=True,
                )
        if character_runtime_state_to_persist is not None:
            try:
                self._persist.persist_config_override(
                    STORE_CHARACTER_RUNTIME_STATE,
                    character_runtime_state_to_persist,
                )
            except Exception:  # noqa: BLE001
                self.logger.warning(
                    "failed to persist galgame strategy memory state key=%s",
                    STORE_CHARACTER_RUNTIME_STATE,
                    exc_info=True,
                )

    def _record_error(self, error: dict[str, Any]) -> None:
        with self._state_lock:
            self._state.last_error = json_copy(error)
            self._state_dirty = True
            self._cached_snapshot = None

    def _record_ocr_poll_duration(self, runtime: dict[str, Any]) -> None:
        try:
            duration = float(runtime.get("last_poll_duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0.0:
            return
        with self._state_lock:
            self._ocr_poll_duration_samples.append(duration)
        self._maybe_auto_degrade_screen_awareness()

    def _record_bridge_poll_duration(self, duration: float) -> None:
        if duration <= 0.0:
            return
        with self._state_lock:
            self._bridge_poll_duration_samples.append(float(duration))

    def _maybe_auto_degrade_screen_awareness(self) -> None:
        if self._cfg is None:
            return
        mode = str(
            getattr(self._cfg, "ocr_reader_screen_awareness_latency_mode", "balanced")
            or "balanced"
        ).strip().lower()
        if mode == "aggressive":
            mode = "full"
        if mode != "full":
            return
        with self._state_lock:
            samples = list(self._ocr_poll_duration_samples)
        if len(samples) < _LATENCY_MIN_SAMPLES_FOR_P95:
            return
        p95 = _duration_percentile(samples, 0.95)
        if p95 <= _OCR_POLL_P95_DEGRADE_THRESHOLD_SECONDS:
            return
        reason = (
            "ocr_poll_p95_exceeded_3s; "
            f"p95={p95:.2f}s; screen_awareness_latency_mode full->balanced"
        )
        self._cfg.ocr_reader_screen_awareness_latency_mode = "balanced"
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._record_error(
                    make_error(
                        f"apply OCR latency auto-degrade failed: {exc}",
                        source="ocr_reader",
                        kind="warning",
                    )
                )
                return
        with self._state_lock:
            self._ocr_auto_degrade_reason = reason
            self._ocr_auto_degrade_at = utc_now_iso()
            self._ocr_auto_degrade_count += 1

    def _bridge_poll_debug_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._bridge_poll_task_lock:
            with self._state_lock:
                bridge_poll_task = self._bridge_poll_task
                bridge_poll_started_at = float(self._bridge_poll_started_at or 0.0)
                last_bridge_poll_duration_seconds = float(
                    self._last_bridge_poll_duration_seconds or 0.0
                )
                last_agent_tick_at = float(self._last_agent_tick_at or 0.0)
                bridge_tick_last_started_at = float(self._bridge_tick_last_started_at or 0.0)
                bridge_tick_last_finished_at = float(self._bridge_tick_last_finished_at or 0.0)
                bridge_tick_last_duration_seconds = float(
                    self._bridge_tick_last_duration_seconds or 0.0
                )
                bridge_tick_last_error = str(self._bridge_tick_last_error or "")
                bridge_tick_launch_count = int(self._bridge_tick_launch_count or 0)
                last_bridge_poll_launch_at = float(self._last_bridge_poll_launch_at or 0.0)
                bridge_poll_launch_count = int(self._bridge_poll_launch_count or 0)
                next_poll_at = float(self._state.next_poll_at_monotonic or 0.0)
                pending_ocr_advance_captures = int(self._pending_ocr_advance_captures or 0)
                ocr_fast_loop_task = self._ocr_fast_loop_task
                ocr_fast_loop_started_at = float(self._ocr_fast_loop_started_at or 0.0)
                ocr_fast_loop_last_duration_seconds = float(
                    self._ocr_fast_loop_last_duration_seconds or 0.0
                )
                ocr_fast_loop_last_run_at = float(self._ocr_fast_loop_last_run_at or 0.0)
                ocr_fast_loop_iteration_count = int(
                    self._ocr_fast_loop_iteration_count or 0
                )
                ocr_poll_duration_summary = _duration_summary(
                    list(self._ocr_poll_duration_samples)
                )
                bridge_poll_duration_summary = _duration_summary(
                    list(self._bridge_poll_duration_samples)
                )
                ocr_auto_degrade_reason = str(self._ocr_auto_degrade_reason or "")
                ocr_auto_degrade_at = str(self._ocr_auto_degrade_at or "")
                ocr_auto_degrade_count = int(self._ocr_auto_degrade_count or 0)
                last_ocr_advance_capture_requested_at = float(
                    self._last_ocr_advance_capture_requested_at or 0.0
                )
                last_ocr_advance_capture_reason = str(
                    self._last_ocr_advance_capture_reason or ""
                )
        poll_running = bridge_poll_task is not None and not bridge_poll_task.done()
        ocr_foreground_advance_monitor_running = (
            self._ocr_foreground_advance_monitor_task is not None
            and not self._ocr_foreground_advance_monitor_task.done()
        )
        ocr_fast_loop_running = (
            ocr_fast_loop_task is not None and not ocr_fast_loop_task.done()
        )
        ocr_fast_loop_inflight_seconds = (
            max(0.0, now - ocr_fast_loop_started_at)
            if ocr_fast_loop_running and ocr_fast_loop_started_at > 0.0
            else 0.0
        )
        inflight_seconds = (
            max(0.0, now - bridge_poll_started_at)
            if poll_running and bridge_poll_started_at > 0.0
            else 0.0
        )
        next_poll_in_seconds = max(0.0, next_poll_at - now) if next_poll_at > 0.0 else 0.0
        last_agent_tick_age_seconds = (
            max(0.0, now - last_agent_tick_at) if last_agent_tick_at > 0.0 else 0.0
        )
        bridge_tick_last_age_seconds = (
            max(0.0, now - bridge_tick_last_started_at)
            if bridge_tick_last_started_at > 0.0
            else 0.0
        )
        pending_ocr_advance_capture_age_seconds = (
            max(0.0, now - last_ocr_advance_capture_requested_at)
            if pending_ocr_advance_captures > 0
            and last_ocr_advance_capture_requested_at > 0.0
            else 0.0
        )
        pending_ocr_delay_remaining = (
            max(0.0, _OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS - pending_ocr_advance_capture_age_seconds)
            if pending_ocr_advance_captures > 0
            else 0.0
        )
        pending_manual_foreground_ocr_capture = (
            pending_ocr_advance_captures > 0
            and last_ocr_advance_capture_reason
            in {"manual_foreground_advance", "foreground_target_activated"}
        )
        return {
            "bridge_poll_running": poll_running,
            "bridge_poll_inflight_seconds": inflight_seconds,
            "last_bridge_poll_duration_seconds": last_bridge_poll_duration_seconds,
            "next_bridge_poll_in_seconds": next_poll_in_seconds,
            "last_agent_tick_at": last_agent_tick_at,
            "last_agent_tick_age_seconds": last_agent_tick_age_seconds,
            "bridge_tick_last_started_at": bridge_tick_last_started_at,
            "bridge_tick_last_finished_at": bridge_tick_last_finished_at,
            "bridge_tick_last_duration_seconds": bridge_tick_last_duration_seconds,
            "bridge_tick_last_error": bridge_tick_last_error,
            "bridge_tick_launch_count": bridge_tick_launch_count,
            "bridge_tick_auto_running": (
                bridge_tick_launch_count > 0
                and not bridge_tick_last_error
                and bridge_tick_last_age_seconds < 5.0
            ),
            "bridge_tick_last_age_seconds": bridge_tick_last_age_seconds,
            "last_bridge_poll_launch_at": last_bridge_poll_launch_at,
            "bridge_poll_launch_count": bridge_poll_launch_count,
            "ocr_foreground_advance_monitor_running": ocr_foreground_advance_monitor_running,
            "ocr_fast_loop_enabled": bool(
                self._cfg is not None
                and getattr(self._cfg, "ocr_reader_fast_loop_enabled", False)
            ),
            "ocr_fast_loop_running": ocr_fast_loop_running,
            "ocr_fast_loop_inflight_seconds": ocr_fast_loop_inflight_seconds,
            "ocr_fast_loop_last_duration_seconds": ocr_fast_loop_last_duration_seconds,
            "ocr_fast_loop_last_run_at": ocr_fast_loop_last_run_at,
            "ocr_fast_loop_iteration_count": ocr_fast_loop_iteration_count,
            "ocr_poll_latency": ocr_poll_duration_summary,
            "ocr_poll_latency_sample_count": ocr_poll_duration_summary["sample_count"],
            "ocr_poll_duration_p50_seconds": ocr_poll_duration_summary["p50_seconds"],
            "ocr_poll_duration_p95_seconds": ocr_poll_duration_summary["p95_seconds"],
            "bridge_poll_latency": bridge_poll_duration_summary,
            "bridge_poll_latency_sample_count": bridge_poll_duration_summary["sample_count"],
            "bridge_poll_duration_p50_seconds": bridge_poll_duration_summary["p50_seconds"],
            "bridge_poll_duration_p95_seconds": bridge_poll_duration_summary["p95_seconds"],
            "ocr_auto_degrade_reason": ocr_auto_degrade_reason,
            "ocr_auto_degrade_at": ocr_auto_degrade_at,
            "ocr_auto_degrade_count": ocr_auto_degrade_count,
            "pending_ocr_advance_captures": pending_ocr_advance_captures,
            "pending_ocr_advance_capture": pending_ocr_advance_captures > 0,
            "pending_manual_foreground_ocr_capture": pending_manual_foreground_ocr_capture,
            "pending_ocr_delay_remaining": pending_ocr_delay_remaining,
            "pending_ocr_advance_capture_age_seconds": pending_ocr_advance_capture_age_seconds,
            "pending_ocr_advance_reason": last_ocr_advance_capture_reason,
            "last_ocr_advance_capture_reason": last_ocr_advance_capture_reason,
        }

    def _clear_completed_background_bridge_poll(
        self,
        completed_task: asyncio.Task[None] | Future[None] | None = None,
    ) -> None:
        with self._bridge_poll_task_lock:
            task = self._bridge_poll_task
            if task is None or not task.done():
                return
            if completed_task is not None and task is not completed_task:
                return
            self._bridge_poll_task = None
        if task.cancelled():
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
        except Exception as exc:
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
            self._record_error(
                make_error(
                    f"bridge background poll failed after completion: {exc}",
                    source="bridge_reader",
                    kind="error",
                )
            )

    def _ensure_bridge_poll_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._bridge_poll_loop
        thread = self._bridge_poll_thread
        if loop is not None and thread is not None and thread.is_alive() and not loop.is_closed():
            return loop

        ready = threading.Event()
        holder: dict[str, asyncio.AbstractEventLoop] = {}
        self._bridge_poll_thread_stop.clear()

        def _run_loop() -> None:
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            holder["loop"] = worker_loop
            ready.set()
            try:
                worker_loop.run_forever()
            finally:
                pending = [task for task in asyncio.all_tasks(worker_loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    worker_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                worker_loop.close()

        thread = threading.Thread(
            target=_run_loop,
            name="galgame-bridge-poll",
            daemon=True,
        )
        thread.start()
        if not ready.wait(timeout=2.0):
            self._record_error(
                make_error(
                    "bridge background poll loop failed to start",
                    source="bridge_reader",
                    kind="error",
                )
            )
            return None
        self._bridge_poll_loop = holder.get("loop")
        self._bridge_poll_thread = thread
        return self._bridge_poll_loop

    async def _cancel_bridge_poll_loop_tasks_before_stop(self) -> None:
        current_task = asyncio.current_task()
        pending = [
            task
            for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    def _stop_bridge_poll_loop(self) -> None:
        loop = self._bridge_poll_loop
        thread = self._bridge_poll_thread
        loop_key = id(loop) if loop is not None else None
        self._bridge_poll_loop = None
        self._bridge_poll_thread = None
        self._bridge_poll_thread_stop.set()
        if loop is not None and not loop.is_closed():
            try:
                stop_future = asyncio.run_coroutine_threadsafe(
                    self._cancel_bridge_poll_loop_tasks_before_stop(),
                    loop,
                )
                stop_future.result(timeout=2.0)
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame bridge poll loop graceful stop failed: {}",
                    exc,
                )
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
        if loop_key is not None:
            with self._bridge_poll_task_lock:
                self._poll_bridge_locks.pop(loop_key, None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
            if thread.is_alive():
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame bridge poll loop thread did not stop within timeout",
                )

    def _background_bridge_poll_stale_timeout_seconds(self) -> float:
        if self._cfg is None:
            return _BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS
        interval = max(
            float(self._cfg.active_poll_interval_seconds),
            float(self._cfg.idle_poll_interval_seconds),
            float(self._cfg.ocr_reader_poll_interval_seconds),
            1.0,
        )
        return max(_BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS, interval * 12.0)

    def _add_bridge_poll_debug_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        enriched.update(self._bridge_poll_debug_payload())
        runtime = dict(enriched.get("ocr_reader_runtime") or {})
        if runtime:
            pending_count = int(enriched.get("pending_ocr_advance_captures") or 0)
            pending_reason = str(enriched.get("pending_ocr_advance_reason") or "")
            runtime.update(
                {
                    "pending_ocr_advance_capture": pending_count > 0,
                    "pending_manual_foreground_ocr_capture": bool(
                        enriched.get("pending_manual_foreground_ocr_capture")
                    ),
                    "pending_ocr_delay_remaining": float(
                        enriched.get("pending_ocr_delay_remaining") or 0.0
                    ),
                    "pending_ocr_advance_capture_age_seconds": float(
                        enriched.get("pending_ocr_advance_capture_age_seconds") or 0.0
                    ),
                    "pending_ocr_advance_reason": pending_reason,
                }
            )
            enriched["pending_ocr_advance_capture"] = pending_count > 0
            enriched["pending_manual_foreground_ocr_capture"] = bool(
                enriched.get("pending_manual_foreground_ocr_capture")
            )
            enriched["pending_ocr_delay_remaining"] = float(
                enriched.get("pending_ocr_delay_remaining") or 0.0
            )
            enriched["pending_ocr_advance_reason"] = pending_reason
            pending_rollback = dict(self._ocr_capture_profile_pending_rollback or {})
            runtime["capture_profile_auto_apply_enabled"] = bool(
                self._ocr_capture_profile_auto_apply_enabled
            )
            runtime["capture_profile_pending_rollback"] = bool(pending_rollback)
            runtime["capture_profile_rollback_failure_count"] = int(
                pending_rollback.get("failure_count") or 0
            )
            runtime["capture_profile_last_rollback_reason"] = str(
                self._ocr_capture_profile_last_rollback_reason or ""
            )
            runtime["capture_profile_pending_rollback_reason"] = str(
                pending_rollback.get("reason") or ""
            )
            enriched["ocr_reader_runtime"] = runtime
            context_state = str(runtime.get("ocr_context_state") or "")
            poll_running = bool(enriched.get("bridge_poll_running"))
            has_capture_attempt = bool(str(runtime.get("last_capture_attempt_at") or ""))
            if context_state == "capture_pending" and not poll_running and not has_capture_attempt:
                runtime["ocr_context_state"] = "poll_not_running"
                enriched["ocr_reader_runtime"] = runtime
                enriched["ocr_capture_diagnostic_required"] = True
                enriched["ocr_capture_diagnostic"] = (
                    "OCR 轮询未继续执行，尚未完成首次截图；请检查插件 timer、后端重载状态或刷新运行中的插件。"
                )
            enriched["ocr_context_state"] = str(runtime.get("ocr_context_state") or context_state)
        ocr_background_status = build_ocr_background_status(enriched)
        enriched["ocr_background_status"] = ocr_background_status
        enriched["ocr_background_state"] = str(ocr_background_status.get("state") or "")
        enriched["ocr_background_message"] = str(ocr_background_status.get("message") or "")
        enriched["ocr_background_polling"] = bool(
            ocr_background_status.get("background_polling")
        )
        enriched["ocr_foreground_resume_pending"] = bool(
            ocr_background_status.get("foreground_resume_pending")
        )
        enriched["ocr_capture_backend_blocked"] = bool(
            ocr_background_status.get("capture_backend_blocked")
        )
        enriched["primary_diagnosis"] = build_primary_diagnosis(enriched)
        return enriched

    def _start_background_bridge_poll(self) -> bool:
        if self._cfg is None:
            return False
        self._clear_completed_background_bridge_poll()
        with self._bridge_poll_task_lock:
            if self._bridge_poll_task is not None:
                if not self._bridge_poll_task.done():
                    # 此处为 _bridge_poll_task_lock → _state_lock 路径；
                    # 反向路径 (_state_lock → _bridge_poll_task_lock) 在
                    # _request_ocr_after_advance_capture_at:920。asyncio 单线程下安全。
                    with self._state_lock:
                        bridge_poll_started_at = float(self._bridge_poll_started_at or 0.0)
                    inflight_seconds = (
                        max(0.0, time.monotonic() - bridge_poll_started_at)
                        if bridge_poll_started_at > 0.0
                        else 0.0
                    )
                    if inflight_seconds >= self._background_bridge_poll_stale_timeout_seconds():
                        self._record_error(
                            make_error(
                                (
                                    "bridge background poll timed out; canceling stale OCR poll "
                                    f"after {inflight_seconds:.1f}s"
                                ),
                                source="bridge_reader",
                                kind="warning",
                            )
                        )
                        self._bridge_poll_task.cancel()
                        with self._state_lock:
                            self._clear_pending_ocr_advance_captures_locked()
                            self._state.next_poll_at_monotonic = 0.0
                            self._state_dirty = True
                            self._cached_snapshot = None
                    return False
                self._bridge_poll_task = None
            started_at = time.monotonic()
            with self._state_lock:
                self._bridge_poll_started_at = started_at
                self._last_bridge_poll_launch_at = started_at
                self._bridge_poll_launch_count += 1
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is not None and not running_loop.is_closed():
                task = running_loop.create_task(self._run_background_bridge_poll())
                self._bridge_poll_task = task
                task.add_done_callback(
                    lambda completed: self._clear_completed_background_bridge_poll(completed)
                )
                return True
            loop = self._ensure_bridge_poll_loop()
            if loop is None:
                return False
            task = asyncio.run_coroutine_threadsafe(self._run_background_bridge_poll(), loop)
            self._bridge_poll_task = task
            task.add_done_callback(
                lambda completed: self._clear_completed_background_bridge_poll(completed)
            )
            return True

    async def _run_background_bridge_poll(self) -> None:
        task_started_at = time.monotonic()
        with self._state_lock:
            self._bridge_poll_started_at = task_started_at
        try:
            while not self._bridge_poll_thread_stop.is_set():
                poll_started_at = time.monotonic()
                with self._state_lock:
                    self._bridge_poll_started_at = poll_started_at
                await self._poll_bridge(force=False)
                poll_finished_at = time.monotonic()
                with self._state_lock:
                    self._bridge_poll_started_at = 0.0
                    self._bridge_poll_finished_at = poll_finished_at
                    self._last_bridge_poll_duration_seconds = max(
                        0.0,
                        poll_finished_at - poll_started_at,
                    )
                if self._bridge_poll_thread_stop.is_set():
                    break
                delay = self._background_bridge_poll_sleep_seconds()
                if delay is None:
                    break
                await asyncio.sleep(delay)
        except Exception as exc:
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
            self._record_error(
                make_error(
                    f"bridge background poll failed: {exc}",
                    source="bridge_reader",
                    kind="error",
                )
            )
        finally:
            finished_at = time.monotonic()
            with self._state_lock:
                self._bridge_poll_started_at = 0.0
                self._bridge_poll_finished_at = finished_at
                if self._last_bridge_poll_duration_seconds <= 0.0:
                    self._last_bridge_poll_duration_seconds = max(
                        0.0,
                        finished_at - task_started_at,
                    )

    def _background_bridge_poll_sleep_seconds(self) -> float | None:
        if self._has_pending_ocr_advance_capture():
            delay = self._pending_ocr_advance_capture_delay_remaining()
            if delay <= 0.0:
                delay = _OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS
            return min(delay, _OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS)
        if self._cfg is None or not self._cfg.ocr_reader_enabled:
            return None
        if self._cfg.ocr_reader_trigger_mode != OCR_TRIGGER_MODE_INTERVAL:
            return None
        with self._state_lock:
            active_data_source = str(self._state.active_data_source or "")
            ocr_reader_runtime = json_copy(self._state.ocr_reader_runtime)
            next_poll_at = float(self._state.next_poll_at_monotonic or 0.0)
        if active_data_source != DATA_SOURCE_OCR_READER:
            return None
        if str(ocr_reader_runtime.get("status") or "") not in {"starting", "active"}:
            return None
        if next_poll_at <= 0.0:
            return 0.0
        return max(0.0, next_poll_at - time.monotonic())

    def _ocr_fast_loop_should_run(self) -> bool:
        return (
            self._cfg is not None
            and self._ocr_reader_manager is not None
            and bool(getattr(self._cfg, "ocr_reader_fast_loop_enabled", False))
            and bool(getattr(self._cfg, "ocr_reader_enabled", False))
            and getattr(self._cfg, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_INTERVAL
        )

    def _start_ocr_fast_loop(self) -> bool:
        # 读-检查-写 _ocr_fast_loop_task 无锁保护。调用者均在主 asyncio 线程，
        # 协作式调度下无竞态。若改为多线程或拆分 await 点，需加锁。
        if not self._ocr_fast_loop_should_run():
            return False
        task = self._ocr_fast_loop_task
        if task is not None and not task.done():
            return True
        try:
            task = asyncio.create_task(self._run_ocr_fast_loop())
        except RuntimeError:
            return False
        self._ocr_fast_loop_task = task
        return True

    async def _cancel_ocr_fast_loop(self) -> None:
        task = self._ocr_fast_loop_task
        if task is None:
            return
        self._ocr_fast_loop_task = None
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame OCR fast loop cancellation failed: {}",
                exc,
            )

    async def _acquire_ocr_tick_lock(self, *, wait: bool) -> bool:
        if self._ocr_reader_tick_lock.acquire(blocking=False):
            return True
        if not wait:
            return False
        while not self._ocr_reader_tick_lock.acquire(blocking=False):
            await asyncio.sleep(0.05)
        return True

    def _ocr_fast_loop_sleep_seconds(self, *, elapsed_seconds: float) -> float:
        if self._cfg is None:
            return 1.0
        interval = max(0.1, float(self._cfg.ocr_reader_poll_interval_seconds or 0.5))
        return max(0.0, interval - max(0.0, elapsed_seconds))

    def _ocr_fast_loop_capture_allowed_snapshot(self, state_snapshot: dict[str, Any]) -> bool:
        if self._cfg is None:
            return False
        reader_mode = _normalize_reader_mode(getattr(self._cfg, "reader_mode", READER_MODE_AUTO))
        if reader_mode == READER_MODE_OCR:
            return True
        active_data_source = str(state_snapshot.get("active_data_source") or "")
        runtime = state_snapshot.get("ocr_reader_runtime")
        runtime_obj = runtime if isinstance(runtime, dict) else {}
        runtime_status = str(runtime_obj.get("status") or "")
        if active_data_source == DATA_SOURCE_OCR_READER:
            return True
        return runtime_status in {"starting", "active"}

    async def _run_ocr_fast_loop_once(self) -> bool:
        if not self._ocr_fast_loop_should_run() or self._ocr_reader_manager is None:
            return False
        if not await self._acquire_ocr_tick_lock(wait=False):
            return False
        started_at = time.monotonic()
        with self._state_lock:
            self._ocr_fast_loop_started_at = started_at
        should_start_bridge_poll = False
        try:
            state_snapshot = self._snapshot_state(fresh=True)
            if not self._ocr_fast_loop_capture_allowed_snapshot(state_snapshot):
                return False
            self._ocr_reader_manager.update_config(self._cfg)
            update_advance_speed = getattr(
                self._ocr_reader_manager,
                "update_advance_speed",
                None,
            )
            if callable(update_advance_speed):
                update_advance_speed(str(state_snapshot.get("advance_speed") or ADVANCE_SPEED_MEDIUM))
            memory_reader_runtime = json_copy(
                state_snapshot.get("memory_reader_runtime") or {}
            )
            bridge_sdk_available = (
                str(state_snapshot.get("active_data_source") or "") == DATA_SOURCE_BRIDGE_SDK
            )
            tick = await self._ocr_reader_manager.tick(
                bridge_sdk_available=bridge_sdk_available,
                memory_reader_runtime=memory_reader_runtime,
            )
            self._record_ocr_poll_duration(tick.runtime)
            ocr_reader_runtime = await self._update_ocr_capture_profile_rollback_state(
                tick.runtime
            )
            ocr_reader_runtime = await self._maybe_auto_apply_recommended_ocr_capture_profile(
                ocr_reader_runtime
            )
            resolved_window_target = self._ocr_reader_manager.current_window_target()
            with self._state_lock:
                self._state.ocr_reader_runtime = (
                    _merge_ocr_runtime_preserving_bridge_diagnostics(
                        ocr_reader_runtime,
                        self._state.ocr_reader_runtime,
                    )
                )
                if resolved_window_target != json_copy(self._state.ocr_window_target):
                    self._state.ocr_window_target = json_copy(resolved_window_target)
                    try:
                        self._persist.persist_ocr_window_target(resolved_window_target)
                    except Exception as exc:
                        self._state.last_error = make_error(
                            f"persist OCR window target failed: {exc}",
                            source="ocr_reader",
                            kind="warning",
                        )
                if tick.should_rescan or tick.stable_event_emitted:
                    self._state.next_poll_at_monotonic = 0.0
                    should_start_bridge_poll = True
                self._state_dirty = True
                self._cached_snapshot = None
            self._fast_loop_consecutive_errors = 0
            return True
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader fast loop failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )
            self._fast_loop_consecutive_errors += 1
            if self._fast_loop_consecutive_errors >= 5:
                self.logger.warning(
                    f"ocr fast loop paused after {self._fast_loop_consecutive_errors} consecutive errors"
                )
                if self._cfg is not None:
                    self._cfg.ocr_reader_fast_loop_enabled = False
                self._fast_loop_auto_enabled = False
            return False
        finally:
            finished_at = time.monotonic()
            with self._state_lock:
                self._ocr_fast_loop_started_at = 0.0
                self._ocr_fast_loop_last_run_at = finished_at
                self._ocr_fast_loop_last_duration_seconds = max(0.0, finished_at - started_at)
                self._ocr_fast_loop_iteration_count += 1
            self._ocr_reader_tick_lock.release()
            if should_start_bridge_poll:
                self._start_background_bridge_poll()

    async def _run_ocr_fast_loop(self) -> None:
        try:
            while self._ocr_fast_loop_should_run():
                started_at = time.monotonic()
                await self._run_ocr_fast_loop_once()
                if not self._ocr_fast_loop_should_run():
                    break
                await asyncio.sleep(
                    self._ocr_fast_loop_sleep_seconds(
                        elapsed_seconds=time.monotonic() - started_at
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader fast loop stopped: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )

    async def _cancel_background_bridge_poll(self) -> None:
        with self._bridge_poll_task_lock:
            task = self._bridge_poll_task
            if task is None:
                return
            self._bridge_poll_task = None
        if not task.done():
            try:
                if isinstance(task, Future):
                    wrapped = asyncio.wrap_future(task)
                    wrapped.cancel()
                    await wrapped
                else:
                    task.cancel()
                    await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame bridge background poll cancellation failed: {}",
                    exc,
                )
        self._stop_bridge_poll_loop()

    def _ocr_foreground_advance_monitor_should_run(self) -> bool:
        return (
            self._cfg is not None
            and self._ocr_reader_manager is not None
            and bool(self._cfg.ocr_reader_enabled)
            and getattr(self._cfg, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
        )

    def _ocr_foreground_advance_monitor_active(self) -> bool:
        task = self._ocr_foreground_advance_monitor_task
        return (
            self._ocr_foreground_advance_monitor_should_run()
            and task is not None
            and not task.done()
        )

    async def _run_ocr_foreground_advance_monitor(self) -> None:
        try:
            while self._ocr_foreground_advance_monitor_should_run():
                self._refresh_ocr_foreground_state()
                self._trigger_ocr_for_manual_foreground_advance()
                await asyncio.sleep(_OCR_FOREGROUND_ADVANCE_MONITOR_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader foreground advance async monitor failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )

    async def _ensure_ocr_foreground_advance_monitor(self) -> bool:
        task = self._ocr_foreground_advance_monitor_task
        if task is not None and task.done():
            self._ocr_foreground_advance_monitor_task = None
        if not self._ocr_foreground_advance_monitor_should_run():
            await self._cancel_ocr_foreground_advance_monitor()
            return False
        task = self._ocr_foreground_advance_monitor_task
        if task is not None and not task.done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._ocr_foreground_advance_monitor_task = loop.create_task(
            self._run_ocr_foreground_advance_monitor()
        )
        return True

    async def _cancel_ocr_foreground_advance_monitor(self) -> None:
        task = self._ocr_foreground_advance_monitor_task
        self._ocr_foreground_advance_monitor_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame OCR foreground advance monitor cancellation failed: {}",
                exc,
            )

    def _set_runtime_from_store(self, restored: dict[str, Any], warnings: list[str]) -> None:
        with self._state_lock:
            self._state = build_initial_state(
                mode=str(restored.get(STORE_MODE, MODE_COMPANION)),
                push_notifications=bool(restored.get(STORE_PUSH_NOTIFICATIONS, True)),
                advance_speed=str(restored.get(STORE_ADVANCE_SPEED, ADVANCE_SPEED_MEDIUM)),
            )
            self._state.bound_game_id = str(restored.get(STORE_BOUND_GAME_ID, ""))
            self._state.active_session_id = str(restored.get(STORE_SESSION_ID, ""))
            self._state.events_byte_offset = int(restored.get(STORE_EVENTS_BYTE_OFFSET, 0))
            self._state.events_file_size = int(restored.get(STORE_EVENTS_FILE_SIZE, 0))
            self._state.last_seq = int(restored.get(STORE_LAST_SEQ, 0))
            self._state.dedupe_window = json_copy(restored.get(STORE_DEDUPE_WINDOW, []))
            self._state.last_error = json_copy(restored.get(STORE_LAST_ERROR, {}))
            self._state.active_data_source = DATA_SOURCE_NONE
            self._state.memory_reader_runtime = {}
            self._state.memory_reader_target = json_copy(
                restored.get(STORE_MEMORY_READER_TARGET, {})
            )
            self._state.ocr_reader_runtime = {}
            self._state.ocr_capture_profiles = json_copy(
                restored.get(STORE_OCR_CAPTURE_PROFILES, {})
            )
            self._state.ocr_window_target = json_copy(restored.get(STORE_OCR_WINDOW_TARGET, {}))
            self._state.context_snapshot = self._load_context_snapshot_for_state()
            self._state.character_profiles = json_copy(restored.get(STORE_CHARACTER_PROFILES, {}))
            self._state.character_profile_version = str(
                restored.get(STORE_CHARACTER_PROFILE_VERSION, "")
            )
            self._state.character_mode = str(restored.get(STORE_CHARACTER_MODE, "off") or "off")
            self._state.character_fixed_name = str(
                restored.get(STORE_CHARACTER_FIXED_NAME, "")
            )
            self._state.cross_scene_memory = json_copy(restored.get(STORE_CROSS_SCENE_MEMORY, {}))
            self._state.character_runtime_state = json_copy(
                restored.get(STORE_CHARACTER_RUNTIME_STATE, {})
            )
            if warnings and not self._state.last_error:
                self._state.last_error = make_error(
                    "; ".join(warnings),
                    source="store",
                    kind="warning",
                )
            self._state_dirty = True
            self._cached_snapshot = None

        self._apply_config_overrides_from_store()

    def _get_character_profile_manager(self) -> CharacterProfileManager:
        if self._character_profile_manager is None:
            manager_cls = _package_public_attr("CharacterProfileManager", CharacterProfileManager)
            self._character_profile_manager = manager_cls(
                data_dir=Path(__file__).parent / "character_data",
                logger=self.logger,
            )
        return self._character_profile_manager

    def _activate_character_profiles(
        self,
        game_id: str,
        *,
        match_reason: str = "exact_game_id",
    ) -> dict[str, Any]:
        """Lazy-load preset+user profiles for ``game_id`` into shared state.

        Initializes runtime overlay entries for any newly seen characters from
        the preset baseline (so first push has emotion / arc data even before
        the first scene-switch LLM update). Persists to the plugin store.
        """
        normalized = (game_id or "").strip()
        manager = self._get_character_profile_manager()
        load = manager.load_game_profiles(normalized)
        profiles = load.get("profiles") or {}
        version = str(load.get("version") or "")
        with self._state_lock:
            previous_profile_game_id = str(self._state.character_profile_game_id or "")
            previous_runtime = dict(self._state.character_runtime_state or {})
            self._state.character_profiles = json_copy(profiles)
            self._state.character_profile_version = version
            self._state.character_profile_game_id = normalized if profiles else ""
            self._state.character_profile_match_reason = match_reason if profiles else ""
            runtime: dict[str, Any] = {}
            for name, profile in profiles.items():
                existing = previous_runtime.get(name)
                existing_game_id = (
                    str(existing.get("game_id") or existing.get("profile_game_id") or "")
                    if isinstance(existing, dict)
                    else ""
                )
                can_reuse = isinstance(existing, dict) and (
                    existing_game_id == normalized
                    or (not existing_game_id and previous_profile_game_id == normalized)
                )
                if can_reuse:
                    runtime[name] = json_copy(existing)
                else:
                    runtime[name] = manager.init_runtime_state_from_profile(name, profile)
                if isinstance(runtime[name], dict):
                    runtime[name]["game_id"] = normalized
            self._state.character_runtime_state = runtime
            runtime_state = json_copy(self._state.character_runtime_state)
            self._state_dirty = True
            self._cached_snapshot = None
        try:
            self._persist.persist_config_override(
                STORE_CHARACTER_PROFILES, json_copy(profiles)
            )
            self._persist.persist_config_override(
                STORE_CHARACTER_PROFILE_VERSION, version
            )
            self._persist.persist_config_override(
                STORE_CHARACTER_RUNTIME_STATE,
                runtime_state,
            )
        except Exception:  # noqa: BLE001 — persistence failure must not crash the call
            self.logger.warning(
                "failed to persist character profile activation for %r",
                normalized,
                exc_info=True,
            )
        if load.get("errors"):
            self.logger.warning(
                "character profile load reported errors for %r: %s",
                normalized,
                "; ".join(load["errors"]),
            )
        if load.get("warnings"):
            self.logger.info(
                "character profile load warnings for %r: %s",
                normalized,
                "; ".join(load["warnings"]),
            )
        return load

    def _clear_character_profiles(self) -> None:
        with self._state_lock:
            self._state.character_profiles = {}
            self._state.character_profile_version = ""
            self._state.character_profile_game_id = ""
            self._state.character_profile_match_reason = ""
            self._state.character_runtime_state = {}
            self._state.character_mode = "off"
            self._state.character_fixed_name = ""
            self._state.character_mode_stale = False
            self._state_dirty = True
            self._cached_snapshot = None
        try:
            self._persist.persist_config_override(STORE_CHARACTER_PROFILES, {})
            self._persist.persist_config_override(STORE_CHARACTER_PROFILE_VERSION, "")
            self._persist.persist_config_override(STORE_CHARACTER_RUNTIME_STATE, {})
            self._persist.persist_config_override(STORE_CHARACTER_MODE, "off")
            self._persist.persist_config_override(STORE_CHARACTER_FIXED_NAME, "")
        except Exception:  # noqa: BLE001
            self.logger.warning("failed to persist character profile clear", exc_info=True)

    @staticmethod
    def _append_character_profile_signal(
        signals: list[dict[str, Any]],
        *,
        game_id: object = "",
        game_title: object = "",
        process_name: object = "",
        window_title: object = "",
    ) -> None:
        signal = {
            "game_id": str(game_id or "").strip(),
            "game_title": str(game_title or "").strip(),
            "process_name": str(process_name or "").strip(),
            "window_title": str(window_title or "").strip(),
        }
        if any(signal.values()) and signal not in signals:
            signals.append(signal)

    def _character_profile_match_signals(self) -> list[dict[str, Any]]:
        with self._state_lock:
            bound_game_id = str(self._state.bound_game_id or "")
            active_game_id = str(self._state.active_game_id or "")
            active_meta = json_copy(self._state.active_session_meta or {})
            ocr_runtime = json_copy(self._state.ocr_reader_runtime or {})
            memory_runtime = json_copy(self._state.memory_reader_runtime or {})

        signals: list[dict[str, Any]] = []
        self._append_character_profile_signal(signals, game_id=bound_game_id)
        self._append_character_profile_signal(signals, game_id=active_game_id)
        if isinstance(active_meta, dict):
            metadata = active_meta.get("metadata")
            metadata_obj = metadata if isinstance(metadata, dict) else {}
            self._append_character_profile_signal(
                signals,
                game_id=active_meta.get("game_id"),
                game_title=active_meta.get("game_title"),
                process_name=metadata_obj.get("game_process_name"),
                window_title=metadata_obj.get("window_title"),
            )
        if isinstance(ocr_runtime, dict):
            self._append_character_profile_signal(
                signals,
                game_id=ocr_runtime.get("game_id"),
                game_title=ocr_runtime.get("game_title"),
                process_name=(
                    ocr_runtime.get("effective_process_name")
                    or ocr_runtime.get("process_name")
                ),
                window_title=(
                    ocr_runtime.get("effective_window_title")
                    or ocr_runtime.get("window_title")
                ),
            )
        if isinstance(memory_runtime, dict):
            self._append_character_profile_signal(
                signals,
                game_id=memory_runtime.get("game_id"),
                game_title=memory_runtime.get("game_title"),
                process_name=memory_runtime.get("process_name"),
            )
        return signals

    def _load_character_profiles_for_current_context(
        self,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        manager = self._get_character_profile_manager()
        signals = self._character_profile_match_signals()
        match = manager.resolve_profile_match(signals)
        with self._state_lock:
            current_profiles = dict(self._state.character_profiles or {})
            current_profile_game_id = str(self._state.character_profile_game_id or "")
            current_match_reason = str(self._state.character_profile_match_reason or "")
            bound_game_id = str(self._state.bound_game_id or "")
        if (
            current_profiles
            and not force
            and current_profile_game_id
            and match is not None
            and match.game_id == current_profile_game_id
        ):
            return {
                "profiles": current_profiles,
                "version": "",
                "errors": [],
                "warnings": [],
                "resolved_game_id": current_profile_game_id,
                "match_reason": current_match_reason,
                "matched": True,
                "cached": True,
            }
        if match is None:
            if current_profiles and not signals:
                return {
                    "profiles": current_profiles,
                    "version": "",
                    "errors": [],
                    "warnings": ["character profile match pending"],
                    "resolved_game_id": current_profile_game_id,
                    "match_reason": current_match_reason,
                    "matched": False,
                    "cached": True,
                    "pending_match": True,
                }
            if current_profiles and not bound_game_id:
                self._clear_character_profiles()
            return {
                "profiles": {},
                "version": "",
                "errors": ["no matching character profile"],
                "warnings": [],
                "resolved_game_id": "",
                "match_reason": "",
                "matched": False,
            }
        load = self._activate_character_profiles(
            match.game_id,
            match_reason=match.reason,
        )
        load["resolved_game_id"] = match.game_id
        load["match_reason"] = match.reason
        load["matched"] = True
        return load

    def _load_context_snapshot_for_game(self, current_game_id: str = "") -> dict[str, Any]:
        if self._cfg is None or not bool(getattr(self._cfg, "context_persist_enabled", False)):
            return {}
        snapshot = self._persist.load_context_snapshot(
            current_game_id=str(current_game_id or ""),
            max_age_seconds=float(getattr(self._cfg, "context_persist_max_age_seconds", 3600.0)),
            require_game_id=bool(getattr(self._cfg, "context_persist_require_game_id", True)),
        )
        return json_copy(snapshot) if isinstance(snapshot, dict) else {}

    def _load_context_snapshot_for_state(self) -> dict[str, Any]:
        bound_game_id = str(self._state.bound_game_id or "")
        active_game_id = str(self._state.active_game_id or "")
        require_game_id = bool(getattr(self._cfg, "context_persist_require_game_id", True))
        game_ids: list[str] = []
        for game_id in (bound_game_id, active_game_id):
            if game_id and game_id not in game_ids:
                game_ids.append(game_id)
        if not require_game_id and not game_ids:
            game_ids.append("")
        for game_id in game_ids:
            if require_game_id and not game_id:
                continue
            snapshot = self._load_context_snapshot_for_game(game_id)
            if snapshot:
                return snapshot
        return {}

    def _context_snapshot_needs_reload(
        self,
        snapshot: object,
        *,
        current_game_id: str,
    ) -> bool:
        if not isinstance(snapshot, dict) or not snapshot:
            return True
        if not bool(getattr(self._cfg, "context_persist_require_game_id", True)):
            return False
        return str(snapshot.get("game_id") or "").strip() != str(current_game_id or "").strip()

    def _active_game_id_for_context_persist(self) -> str:
        with self._state_lock:
            return str(self._state.active_game_id or "")

    def _context_snapshot_liveness_matches(
        self,
        *,
        snapshot: dict[str, Any],
        game_id: str,
        session_id: str,
    ) -> bool:
        latest_snapshot = getattr(self._state, "latest_snapshot", {})
        live_snapshot = (
            latest_snapshot
            if isinstance(latest_snapshot, dict)
            else {}
        )
        if session_id:
            return session_id == str(getattr(self._state, "active_session_id", "") or "")
        return (
            (not game_id or game_id == str(getattr(self._state, "active_game_id", "") or ""))
            and (
                not snapshot["scene_id"]
                or snapshot["scene_id"] == str(live_snapshot.get("scene_id") or "")
            )
            and (
                not snapshot["route_id"]
                or snapshot["route_id"] == str(live_snapshot.get("route_id") or "")
            )
        )

    def _persist_context_snapshot_from_summary(
        self,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        if self._cfg is None or not bool(getattr(self._cfg, "context_persist_enabled", False)):
            return
        if bool(payload.get("degraded")):
            return
        game_id = str(context.get("game_id") or "").strip()
        session_id = str(context.get("session_id") or "").strip()
        if not game_id:
            game_id = self._active_game_id_for_context_persist().strip()
        if not game_id and bool(
            getattr(self._cfg, "context_persist_require_game_id", True)
        ):
            return
        stable_line_ids = [
            str(item.get("line_id") or "").strip()
            for item in context.get("stable_lines", [])
            if isinstance(item, dict) and str(item.get("line_id") or "").strip()
        ]
        summary_seed = str(payload.get("summary") or context.get("scene_summary_seed") or "").strip()
        snapshot = {
            "scene_id": str(context.get("scene_id") or "").strip(),
            "game_id": game_id,
            "route_id": str(context.get("route_id") or "").strip(),
            "summary_seed": summary_seed,
            "stable_line_ids": stable_line_ids[-64:],
            "saved_at": time.time(),
        }
        with self._state_lock:
            live_matches = self._context_snapshot_liveness_matches(
                snapshot=snapshot,
                game_id=game_id,
                session_id=session_id,
            )
        if not live_matches:
            self.logger.warning("galgame saved snapshot persist skipped: stale summary payload")
            return
        with self._state_lock:
            if not self._context_snapshot_liveness_matches(
                snapshot=snapshot,
                game_id=game_id,
                session_id=session_id,
            ):
                self.logger.warning(
                    "galgame context_snapshot persist skipped: stale summary context"
                )
                return
            self._persist.persist_context_snapshot(snapshot)
            self._state.context_snapshot = json_copy(snapshot)
            self._state_dirty = True
            self._cached_snapshot = None

    def _apply_config_overrides_from_store(self) -> None:
        if self._cfg is None:
            return
        overrides = self._persist.load_config_overrides()

        value = overrides.get(STORE_READER_MODE)
        if value is not None and value in READER_MODES:
            self._cfg.reader.reader_mode = value

        value = overrides.get(STORE_OCR_BACKEND_SELECTION)
        if value is not None and value in _OCR_BACKEND_SELECTIONS:
            self._cfg.ocr_reader.ocr_reader_backend_selection = value

        value = _migrate_legacy_capture_backend(overrides.get(STORE_OCR_CAPTURE_BACKEND))
        if value is not None and value in _OCR_CAPTURE_BACKEND_SELECTIONS:
            self._cfg.ocr_reader.ocr_reader_capture_backend = value

        value = overrides.get(STORE_OCR_POLL_INTERVAL_SECONDS)
        if value is not None:
            self._cfg.ocr_reader.ocr_reader_poll_interval_seconds = value

        value = overrides.get(STORE_OCR_TRIGGER_MODE)
        if value is not None and value in OCR_TRIGGER_MODES:
            self._cfg.ocr_reader.ocr_reader_trigger_mode = value

        value = overrides.get(STORE_OCR_FAST_LOOP_ENABLED)
        if value is not None:
            self._cfg.ocr_reader.ocr_reader_fast_loop_enabled = bool(value)

        value = overrides.get(STORE_LLM_VISION_ENABLED)
        if value is not None:
            self._cfg.llm.llm_vision_enabled = bool(value)

        value = overrides.get(STORE_LLM_VISION_MAX_IMAGE_PX)
        if value is not None:
            self._cfg.llm.llm_vision_max_image_px = value

        value = overrides.get(STORE_OCR_SCREEN_TEMPLATES)
        if value is not None:
            self._cfg.ocr_reader.ocr_reader_screen_templates = json_copy(value)

        value = overrides.get(STORE_RAPIDOCR_AUTO_DETECT_LANG)
        if value is not None:
            self._cfg.rapidocr.rapidocr_auto_detect_lang = bool(value)

        value = overrides.get(STORE_RAPIDOCR_LANG_TYPE)
        if value is not None and value in {"ch", "japan", "korean", "en"}:
            self._cfg.rapidocr.rapidocr_lang_type = value
            self._cfg.rapidocr.rapidocr_auto_detect_last_lang = value

        value = overrides.get(STORE_RAPIDOCR_OCR_VERSION)
        if value is not None and value in {"PP-OCRv4", "PP-OCRv5"}:
            self._cfg.rapidocr.rapidocr_ocr_version = value

        value = overrides.get(STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG)
        if value is not None and value in {"ch", "japan", "korean", "en"}:
            self._cfg.rapidocr.rapidocr_auto_detect_last_lang = value

    def _on_rapidocr_auto_lang_changed(self, lang_type: str) -> None:
        if self._cfg is None:
            return
        normalized = str(lang_type or "").strip().lower()
        if normalized not in {"ch", "japan", "korean", "en"}:
            _log_plugin_noncritical(
                self.logger,
                "debug",
                "galgame rapidocr auto-lang ignored invalid lang_type: {}",
                lang_type,
            )
            return
        self._cfg.rapidocr.rapidocr_lang_type = normalized
        self._cfg.rapidocr.rapidocr_auto_detect_last_lang = normalized
        auto_detect_lang = bool(self._cfg.rapidocr.rapidocr_auto_detect_lang)
        try:
            self._config_service.persist_rapidocr_lang(
                lang_type=normalized,
                auto_detect_lang=auto_detect_lang,
                auto_detect_last_lang=normalized,
            )
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame rapidocr auto-lang persist failed for {}: {}",
                normalized,
                exc,
            )
        self._refresh_dependency_status()
        with self._state_lock:
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None

    def _current_status_payload(self) -> dict[str, Any]:
        if self._cfg is None:
            return self._add_bridge_poll_debug_payload({
                "connection_state": "error",
                "mode": MODE_COMPANION,
                "push_notifications": True,
                "bound_game_id": "",
                "available_game_ids": [],
                "active_session_id": "",
                "active_data_source": DATA_SOURCE_NONE,
                "stream_reset_pending": False,
                "last_seq": 0,
                "last_error": {},
                "summary": "config_not_loaded",
                "phase": "phase_1",
                "memory_reader_enabled": False,
                "memory_reader_runtime": {},
                "memory_reader_target": {},
                "ocr_reader_enabled": False,
                "ocr_reader_runtime": {},
                "ocr_capture_profiles": {},
                "dxcam": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "package_name": "dxcam",
                    "target_dir": "",
                    "detail": "config_not_loaded",
                    "runtime_error": "",
                },
                "rapidocr_enabled": False,
                "rapidocr": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "target_dir": "",
                    "runtime_dir": "",
                    "site_packages_dir": "",
                    "model_cache_dir": "",
                    "selected_model": "",
                    "engine_type": "",
                    "lang_type": "",
                    "model_type": "",
                    "ocr_version": "",
                    "detail": "config_not_loaded",
                    "runtime_error": "",
                },
                "textractor": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "target_dir": "",
                    "expected_executable_path": "",
                    "detail": "config_not_loaded",
                },
            })
        state_snapshot = self._snapshot_state()
        state = SimpleNamespace(**state_snapshot)
        return self._add_bridge_poll_debug_payload(
            build_status_payload(state, config=self._cfg, state_is_snapshot=True)
        )

    def _refresh_dependency_status(self) -> None:
        """Recompute galgame dependency status (rapidocr/dxcam inspection).

        After PR #1188 + #1191 the rapidocr/dxcam packages are bundled into
        the main program and runtime pip-install was removed; both inspectors
        now return ``can_install=False``, so missing-cohort dev environments
        no longer surface a user-actionable warning here. The bundled_hint
        banner from #1191 covers the source-install case directly. What this
        method still buys us:

        - ``inspection_failed`` detection — when rapidocr/dxcam imports raise
          unexpectedly (e.g. corrupt wheel after a partial sync), the diag
          surfaces a "依赖状态检查失败" warning instead of a confusing nothing.
        - Snapshot of "checked_at / degraded" so the UI can show staleness.
        """
        if self._cfg is None:
            return
        clear_install_inspection_cache()
        try:
            rapidocr = inspect_rapidocr_installation(
                install_target_dir_raw=self._cfg.rapidocr_install_target_dir,
                engine_type=self._cfg.rapidocr_engine_type,
                lang_type=self._cfg.rapidocr_lang_type,
                model_type=self._cfg.rapidocr_model_type,
                ocr_version=self._cfg.rapidocr_ocr_version,
                plugin_id="galgame_plugin",
            )
            rapidocr["auto_detect_lang"] = bool(
                getattr(self._cfg, "rapidocr_auto_detect_lang", True)
            )
            rapidocr["auto_detect_last_lang"] = str(
                getattr(self._cfg, "rapidocr_auto_detect_last_lang", "") or ""
            )
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame rapidocr dependency inspection failed: {}",
                exc,
            )
            rapidocr = {
                "installed": False,
                "install_supported": True,
                "can_install": False,
                "detail": "inspection_failed",
                "runtime_error": str(exc),
            }
        try:
            dxcam = inspect_dxcam_installation()
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame dxcam dependency inspection failed: {}",
                exc,
            )
            dxcam = {
                "installed": False,
                "install_supported": True,
                "can_install": False,
                "detail": "inspection_failed",
                "runtime_error": str(exc),
            }

        dependencies = (
            ("rapidocr", rapidocr),
            ("dxcam", dxcam),
        )
        missing_dependencies = infer_missing_dependencies(dependencies)
        inspection_failed_dependencies = infer_inspection_failed_dependencies(dependencies)
        dependency_status = {
            "checked_at": time.time(),
            "degraded": bool(missing_dependencies or inspection_failed_dependencies),
            "missing": missing_dependencies,
        }
        if inspection_failed_dependencies:
            dependency_status["inspection_failed"] = inspection_failed_dependencies

        with self._state_lock:
            self._state.dependency_status = dependency_status
            self._state_dirty = True
            self._cached_snapshot = None
        if missing_dependencies:
            self.logger.warning(
                "GalgamePlugin dependency check: optional dependencies missing {}; degraded mode enabled",
                missing_dependencies,
            )
        if inspection_failed_dependencies:
            self.logger.warning(
                "GalgamePlugin dependency check: dependency inspections failed {}; degraded mode enabled",
                inspection_failed_dependencies,
            )

    async def _build_status_payload_async(self) -> dict[str, Any]:
        if self._cfg is None:
            return self._current_status_payload()
        self._refresh_ocr_foreground_state()
        state_snapshot = self._snapshot_state()
        config = self._cfg
        state = SimpleNamespace(**state_snapshot)
        payload = await asyncio.to_thread(
            build_status_payload,
            state,
            config=config,
            state_is_snapshot=True,
        )
        payload = self._add_bridge_poll_debug_payload(payload)
        if self._game_agent is not None:
            try:
                agent_payload = await self._game_agent.peek_status(state_snapshot)
                payload["agent"] = json_copy(agent_payload)
                payload["agent_status"] = str(agent_payload.get("status") or "")
                payload["agent_user_status"] = str(agent_payload.get("agent_user_status") or "")
                payload["agent_pause_kind"] = str(agent_payload.get("agent_pause_kind") or "")
                payload["agent_pause_message"] = str(
                    agent_payload.get("agent_pause_message") or ""
                )
                payload["agent_can_resume_by_button"] = bool(
                    agent_payload.get("agent_can_resume_by_button")
                )
                payload["agent_can_resume_by_focus"] = bool(
                    agent_payload.get("agent_can_resume_by_focus")
                )
                payload["agent_activity"] = str(agent_payload.get("activity") or "")
                payload["agent_reason"] = str(agent_payload.get("reason") or "")
                payload["agent_error"] = str(agent_payload.get("error") or "")
                payload["agent_inbound_queue_size"] = int(
                    agent_payload.get("inbound_queue_size") or 0
                )
                payload["agent_outbound_queue_size"] = int(
                    agent_payload.get("outbound_queue_size") or 0
                )
                payload["agent_last_interruption"] = json_copy(
                    agent_payload.get("last_interruption") or {}
                )
                payload["agent_last_outbound_message"] = json_copy(
                    agent_payload.get("last_outbound_message") or {}
                )
                agent_debug = agent_payload.get("debug")
                agent_diagnostic = (
                    str(
                        (agent_debug or {}).get("target_window_diagnostic")
                        or (agent_debug or {}).get("ocr_capture_diagnostic")
                        or ""
                    )
                    if isinstance(agent_debug, dict)
                    else ""
                )
                payload["agent_diagnostic"] = agent_diagnostic
                payload["agent_diagnostic_required"] = bool(
                    agent_diagnostic
                    or payload["agent_reason"]
                    in {
                        "ocr_context_unavailable",
                        "input_advance_unconfirmed",
                        "target_window_not_foreground",
                        "hard_error",
                    }
                )
            except Exception as exc:
                payload["agent_status"] = "unknown"
                payload["agent_user_status"] = "error"
                payload["agent_pause_kind"] = "none"
                payload["agent_pause_message"] = ""
                payload["agent_can_resume_by_button"] = False
                payload["agent_can_resume_by_focus"] = False
                payload["agent_activity"] = ""
                payload["agent_reason"] = "agent_status_unavailable"
                payload["agent_error"] = str(exc)
                payload["agent_diagnostic"] = f"agent_status_unavailable: {exc}"
                payload["agent_diagnostic_required"] = True
        payload["primary_diagnosis"] = build_primary_diagnosis(payload)
        return payload

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
                self.logger.warning("install progress run_update failed: {}", exc)

        return _progress_update

    async def _load_config(self) -> None:
        raw = await self.config.dump(timeout=5.0)
        raw_config = raw if isinstance(raw, dict) else {}
        self._cfg = build_config(raw_config)

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            await self._load_config()
        except Exception as exc:
            self._record_error(
                make_error(f"load config failed: {exc}", source="config", kind="error")
            )
            return Err(SdkError(f"failed to load galgame_plugin config: {exc}"))

        try:
            restored, warnings = self._persist.load()
            self._set_runtime_from_store(restored, warnings)
        except Exception as exc:
            self._record_error(
                make_error(f"restore store failed: {exc}", source="store", kind="error")
            )
            return Err(SdkError(f"failed to restore galgame_plugin store: {exc}"))

        host_agent_adapter_cls = _package_public_attr("HostAgentAdapter", HostAgentAdapter)
        llm_gateway_cls = _package_public_attr("LLMGateway", LLMGateway)
        game_agent_cls = _package_public_attr("GameLLMAgent", GameLLMAgent)
        memory_reader_manager_cls = _package_public_attr("MemoryReaderManager", MemoryReaderManager)
        ocr_reader_manager_cls = _package_public_attr("OcrReaderManager", OcrReaderManager)

        self._host_agent_adapter = host_agent_adapter_cls(self.logger)
        self._llm_gateway = llm_gateway_cls(self, self.logger, self._cfg)
        self._game_agent = game_agent_cls(
            plugin=self,
            logger=self.logger,
            llm_gateway=self._llm_gateway,
            host_adapter=self._host_agent_adapter,
            config=self._cfg,
        )
        self._memory_reader_manager = memory_reader_manager_cls(
            logger=self.logger,
            config=self._cfg,
        )
        self._memory_reader_manager.update_process_target(self._state.memory_reader_target)
        self._ocr_reader_manager = ocr_reader_manager_cls(
            logger=self.logger,
            config=self._cfg,
            rapidocr_lang_changed_callback=self._on_rapidocr_auto_lang_changed,
        )
        self._ocr_reader_manager.update_capture_profiles(self._state.ocr_capture_profiles)
        self._ocr_reader_manager.update_window_target(self._state.ocr_window_target)

        self._refresh_dependency_status()

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

        if self._cfg.bridge.auto_open_ui:
            port = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "48916")
            url = f"http://127.0.0.1:{port}/plugin/{self.plugin_id}/ui/"
            try:
                open_url_in_browser = _package_public_attr(
                    "_open_url_in_browser",
                    _open_url_in_browser,
                )
                await asyncio.to_thread(open_url_in_browser, url)
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame auto-open UI failed: {}",
                    exc,
                )

        await self._poll_bridge(force=True)
        self._start_ocr_fast_loop()
        await self._ensure_ocr_foreground_advance_monitor()
        return Ok({"status": "ready", "result": await self._build_status_payload_async()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._bridge_tick_shutdown_requested = True
        await self._cancel_ocr_fast_loop()
        await self._cancel_ocr_foreground_advance_monitor()
        await self._cancel_background_bridge_poll()
        if self._memory_reader_manager is not None:
            try:
                await self._memory_reader_manager.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame memory reader shutdown failed: {}",
                    exc,
                )
        if self._ocr_reader_manager is not None:
            try:
                await self._ocr_reader_manager.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame OCR reader shutdown failed: {}",
                    exc,
                )
        if self._game_agent is not None:
            try:
                await self._game_agent.drain_summary_tasks(timeout=5.0)
                await self._game_agent.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame agent shutdown failed: {}",
                    exc,
                )
        if self._llm_gateway is not None:
            try:
                await self._llm_gateway.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame LLM gateway shutdown failed: {}",
                    exc,
                )
        if self._host_agent_adapter is not None:
            try:
                await self._host_agent_adapter.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame host agent adapter shutdown failed: {}",
                    exc,
                )
        try:
            await self.store.close()
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame store shutdown failed: {}",
                exc,
            )
        return Ok({"status": "stopped"})

    @timer_interval(id="bridge_tick", seconds=1, auto_start=True)
    async def bridge_tick(self, **_):
        if self._bridge_tick_shutdown_requested:
            return Ok({"status": "stopped"})
        tick_started_at = time.monotonic()
        with self._state_lock:
            self._bridge_tick_last_started_at = tick_started_at
            self._bridge_tick_launch_count += 1
            self._bridge_tick_last_error = ""
        try:
            self._clear_completed_background_bridge_poll()
            self._refresh_ocr_foreground_state()
            if not self._ocr_foreground_advance_monitor_active():
                self._trigger_ocr_for_manual_foreground_advance()
            if self._game_agent is not None:
                with self._state_lock:
                    self._last_agent_tick_at = time.monotonic()
                try:
                    await self._game_agent.tick(
                        self._snapshot_state(include_private_context=True)
                    )
                    await self._game_agent.drain_summary_tasks(
                        timeout=self._bridge_tick_summary_drain_timeout_seconds()
                    )
                except Exception as exc:
                    with self._state_lock:
                        self._bridge_tick_last_error = f"game_agent_tick_failed: {exc}"
                    self._record_error(
                        make_error(
                            f"game agent tick failed: {exc}",
                            source="game_agent",
                            kind="error",
                        )
                    )
            self._start_background_bridge_poll()
            self._start_ocr_fast_loop()
            await asyncio.sleep(0)
            return Ok({"status": "tick"})
        except Exception as exc:
            with self._state_lock:
                self._bridge_tick_last_error = str(exc)
            raise
        finally:
            tick_finished_at = time.monotonic()
            with self._state_lock:
                self._bridge_tick_last_finished_at = tick_finished_at
                self._bridge_tick_last_duration_seconds = max(
                    0.0,
                    tick_finished_at - tick_started_at,
                )

    def _bridge_tick_summary_drain_timeout_seconds(self) -> float:
        if self._cfg is None:
            return 30.0
        try:
            configured = float(getattr(self._cfg, "llm_call_timeout_seconds", 30.0) or 30.0)
        except (TypeError, ValueError):
            configured = 30.0
        return max(1.0, configured + 2.0)

    def _refresh_ocr_foreground_state(self, *, force: bool = False) -> None:
        if self._cfg is None or not self._cfg.ocr_reader_enabled:
            return
        if getattr(self._cfg, "reader_mode", READER_MODE_AUTO) == READER_MODE_MEMORY:
            return
        if self._ocr_reader_manager is None:
            return
        refresh = getattr(self._ocr_reader_manager, "refresh_foreground_state", None)
        if not callable(refresh):
            return
        now = time.monotonic()
        # TTL gate: prevent bridge_tick, advance monitor, and build_status_payload
        # paths from refreshing foreground state repeatedly in a short window.
        if (
            not force
            and self._last_ocr_foreground_refresh_at > 0.0
            and now - self._last_ocr_foreground_refresh_at < _OCR_FOREGROUND_REFRESH_TTL_SECONDS
        ):
            return
        try:
            runtime = refresh()
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader foreground refresh failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )
            return
        self._last_ocr_foreground_refresh_at = now
        with self._state_lock:
            self._state.ocr_reader_runtime = (
                _merge_ocr_runtime_preserving_bridge_diagnostics(
                    runtime,
                    self._state.ocr_reader_runtime,
                )
            )
            self._state_dirty = True
            self._cached_snapshot = None

    def _trigger_ocr_for_manual_foreground_advance(self) -> None:
        if self._cfg is None or self._ocr_reader_manager is None:
            return
        if not self._cfg.ocr_reader_enabled:
            return
        if getattr(self._cfg, "reader_mode", READER_MODE_AUTO) == READER_MODE_MEMORY:
            return
        if self._cfg.ocr_reader_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
            return
        consume = getattr(self._ocr_reader_manager, "consume_foreground_advance_inputs", None)
        structured_result = True
        if not callable(consume):
            consume = getattr(self._ocr_reader_manager, "consume_foreground_advance_input", None)
            structured_result = False
        if not callable(consume):
            return
        try:
            consume_result = consume()
            should_capture = (
                bool(getattr(consume_result, "triggered", False))
                if structured_result
                else bool(consume_result)
            )
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader foreground advance monitor failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )
            return
        runtime_getter = getattr(self._ocr_reader_manager, "runtime", None)
        if callable(runtime_getter):
            try:
                runtime = runtime_getter()
            except Exception as exc:
                self._record_error(
                    make_error(
                        f"ocr_reader foreground advance runtime sync failed: {exc}",
                        source="ocr_reader",
                        kind="warning",
                    )
                )
            else:
                if isinstance(runtime, dict):
                    with self._state_lock:
                        self._state.ocr_reader_runtime = (
                            _merge_ocr_runtime_preserving_bridge_diagnostics(
                                runtime,
                                self._state.ocr_reader_runtime,
                            )
                        )
                        self._state_dirty = True
                        self._cached_snapshot = None
        if should_capture:
            event_age_seconds = (
                getattr(consume_result, "last_event_age_seconds", 0.0)
                if structured_result
                else 0.0
            )
            coalesced_count = (
                getattr(consume_result, "coalesced_count", 0)
                if structured_result
                else 0
            )
            self._request_ocr_after_advance_capture_for_event_age(
                event_age_seconds=event_age_seconds,
                reason="manual_foreground_advance",
                coalesced_count=int(coalesced_count or 0),
            )

    def _poll_bridge_async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        loop_key = id(loop)
        with self._bridge_poll_task_lock:
            lock = self._poll_bridge_locks.get(loop_key)
            if lock is None:
                lock = asyncio.Lock()
                self._poll_bridge_locks[loop_key] = lock
            return lock

    async def _poll_bridge(self, *, force: bool) -> None:
        if self._cfg is None:
            return

        poll_started_at = time.monotonic()
        async with self._poll_bridge_async_lock():
            while not self._poll_bridge_thread_lock.acquire(blocking=False):
                await asyncio.sleep(0.05)
            try:
                await self._poll_bridge_locked(force=force)
            finally:
                self._poll_bridge_thread_lock.release()
                poll_finished_at = time.monotonic()
                duration = max(0.0, poll_finished_at - poll_started_at)
                with self._state_lock:
                    self._last_bridge_poll_duration_seconds = duration
                self._record_bridge_poll_duration(duration)

    async def _scan_candidates(self) -> tuple[list[str], dict[str, Any], list[str]]:
        if self._cfg is None:
            return [], {}, []
        return await asyncio.to_thread(scan_session_candidates, self._cfg.bridge_root)

    def _commit_bridge_scan_failure(
        self,
        local: dict[str, Any],
        *,
        now_monotonic: float,
        exc: Exception,
    ) -> None:
        if self._cfg is None:
            return
        local["plugin_error"] = f"scan bridge root failed: {exc}"
        local["available_game_ids"] = []
        local["current_connection_state"] = STATE_ERROR
        local["last_error"] = make_error(
            local["plugin_error"], source="bridge_scan", kind="error"
        )
        interval = next_poll_interval_for_state(
            local["current_connection_state"],
            stream_reset_pending=bool(local["stream_reset_pending"]),
            config=self._cfg,
        )
        local["next_poll_at_monotonic"] = now_monotonic + interval
        self._commit_state(local)
        try:
            self._config_service.persist_runtime_state(local)
        except Exception as persist_exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame persist runtime state after bridge scan failure failed: {}",
                persist_exc,
            )

    async def _tick_memory_reader_for_poll(
        self,
        *,
        memory_reader_allowed: bool,
        bridge_sdk_candidate_available: bool,
        raw_available_game_ids: list[str],
        raw_candidates: dict[str, Any],
        memory_reader_runtime: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any], dict[str, Any], list[str]]:
        warnings: list[str] = []
        if (
            self._cfg is None
            or self._memory_reader_manager is None
            or not memory_reader_allowed
        ):
            return raw_available_game_ids, raw_candidates, memory_reader_runtime, warnings
        self._memory_reader_manager.update_config(self._cfg)
        try:
            memory_reader_tick = await self._memory_reader_manager.tick(
                bridge_sdk_available=bridge_sdk_candidate_available,
            )
            warnings.extend(memory_reader_tick.warnings)
            memory_reader_runtime = memory_reader_tick.runtime
            if memory_reader_tick.should_rescan:
                (
                    raw_available_game_ids,
                    raw_candidates,
                    rescan_warnings,
                ) = await self._scan_candidates()
                warnings.extend(rescan_warnings)
        except Exception as exc:
            warnings.append(f"memory_reader tick failed: {exc}")
        return raw_available_game_ids, raw_candidates, memory_reader_runtime, warnings

    async def _refresh_ocr_foreground_for_poll(
        self,
        *,
        ocr_reader_runtime: dict[str, Any],
        ocr_reader_allowed: bool,
        ocr_trigger_mode: str,
        pending_ocr_advance_capture: bool,
        pending_ocr_delay_remaining: float,
    ) -> tuple[dict[str, Any], bool, float, list[str]]:
        warnings: list[str] = []
        foreground_refresh_skipped_reason = ""
        if self._ocr_reader_manager is None:
            foreground_refresh_skipped_reason = "ocr_reader_manager_missing"
        elif not ocr_reader_allowed:
            foreground_refresh_skipped_reason = "ocr_reader_not_allowed"
        elif ocr_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
            foreground_refresh_skipped_reason = "trigger_mode_not_after_advance"
        if foreground_refresh_skipped_reason:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            ocr_reader_runtime.update(
                {
                    "foreground_refresh_attempted": False,
                    "foreground_refresh_skipped_reason": foreground_refresh_skipped_reason,
                }
            )
            return (
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                pending_ocr_delay_remaining,
                warnings,
            )

        was_foreground = bool(ocr_reader_runtime.get("target_is_foreground"))
        refresh_foreground_state = getattr(
            self._ocr_reader_manager,
            "refresh_foreground_state",
            None,
        )
        if not callable(refresh_foreground_state):
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            ocr_reader_runtime.update(
                {
                    "foreground_refresh_attempted": False,
                    "foreground_refresh_skipped_reason": "refresh_method_missing",
                }
            )
            return (
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                pending_ocr_delay_remaining,
                warnings,
            )

        try:
            refreshed_runtime = await asyncio.to_thread(refresh_foreground_state)
            if isinstance(refreshed_runtime, dict):
                ocr_reader_runtime = json_copy(refreshed_runtime)
                ocr_reader_runtime.update(
                    {
                        "foreground_refresh_attempted": True,
                        "foreground_refresh_skipped_reason": "",
                    }
                )
                if (
                    not was_foreground
                    and bool(ocr_reader_runtime.get("target_is_foreground"))
                ):
                    if not self._has_pending_ocr_advance_capture():
                        with self._state_lock:
                            self._pending_ocr_advance_captures = min(
                                self._pending_ocr_advance_captures + 1,
                                8,
                            )
                            self._last_ocr_advance_capture_requested_at = time.monotonic()
                            self._last_ocr_advance_capture_reason = "foreground_target_activated"
                            self._state.next_poll_at_monotonic = 0.0
                    pending_ocr_advance_capture = True
                    pending_ocr_delay_remaining = 0.0
        except Exception as exc:
            warnings.append(f"ocr_reader foreground refresh failed: {exc}")
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            ocr_reader_runtime.update(
                {
                    "foreground_refresh_attempted": True,
                    "foreground_refresh_skipped_reason": "refresh_failed",
                }
            )
        return (
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            pending_ocr_delay_remaining,
            warnings,
        )

    async def _tick_ocr_reader_for_poll(
        self,
        *,
        local: dict[str, Any],
        raw_available_game_ids: list[str],
        raw_candidates: dict[str, Any],
        memory_reader_runtime: dict[str, Any],
        ocr_reader_runtime: dict[str, Any],
        bridge_sdk_candidate_available: bool,
        ocr_tick_allowed: bool,
        pending_manual_foreground_ocr_capture: bool,
        pending_ocr_advance_capture: bool,
        force: bool,
    ) -> tuple[list[str], dict[str, Any], dict[str, Any], bool, bool, list[str]]:
        warnings: list[str] = []
        ocr_reader_stable_event_emitted = False
        tick_execution_diagnostics: dict[str, Any] = {
            "ocr_tick_entered": False,
            "ocr_tick_lock_acquired": False,
            "ocr_fast_loop_delegated": False,
            "ocr_tick_skipped_reason": "",
        }
        if self._cfg is None:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "plugin_config_missing"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if self._ocr_reader_manager is None:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "ocr_reader_manager_missing"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if not ocr_tick_allowed:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "tick_gate_closed"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if self._ocr_fast_loop_should_run() and not force:
            self._start_ocr_fast_loop()
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics.update(
                {
                    "ocr_fast_loop_delegated": True,
                    "ocr_tick_skipped_reason": "ocr_fast_loop_started",
                }
            )
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if not await self._acquire_ocr_tick_lock(wait=force):
            warnings.append("ocr_reader tick skipped: previous OCR tick is still running")
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "ocr_tick_lock_busy"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )

        ocr_reader_tick = None
        tick_execution_diagnostics.update(
            {
                "ocr_tick_entered": True,
                "ocr_tick_lock_acquired": True,
            }
        )
        try:
            self._ocr_reader_manager.update_config(self._cfg)
            update_advance_speed = getattr(
                self._ocr_reader_manager,
                "update_advance_speed",
                None,
            )
            if callable(update_advance_speed):
                update_advance_speed(str(local.get("advance_speed") or ADVANCE_SPEED_MEDIUM))
            ocr_memory_reader_runtime = (
                {}
                if pending_manual_foreground_ocr_capture
                else memory_reader_runtime
            )
            ocr_reader_tick = await self._ocr_reader_manager.tick(
                bridge_sdk_available=bridge_sdk_candidate_available,
                memory_reader_runtime=ocr_memory_reader_runtime,
            )
            self._record_ocr_poll_duration(ocr_reader_tick.runtime)
            warnings.extend(ocr_reader_tick.warnings)
            ocr_reader_runtime = ocr_reader_tick.runtime
            ocr_reader_runtime = await self._update_ocr_capture_profile_rollback_state(
                ocr_reader_runtime
            )
            ocr_reader_runtime = await self._maybe_auto_apply_recommended_ocr_capture_profile(
                ocr_reader_runtime
            )
            if ocr_reader_tick.should_rescan:
                (
                    raw_available_game_ids,
                    raw_candidates,
                    rescan_warnings,
                ) = await self._scan_candidates()
                warnings.extend(rescan_warnings)
            resolved_window_target = self._ocr_reader_manager.current_window_target()
            if resolved_window_target != json_copy(local.get("ocr_window_target") or {}):
                local["ocr_window_target"] = json_copy(resolved_window_target)
                try:
                    self._persist.persist_ocr_window_target(resolved_window_target)
                except Exception as exc:
                    warnings.append(f"persist OCR window target failed: {exc}")
        except Exception as exc:
            warnings.append(f"ocr_reader tick failed: {exc}")
        finally:
            pending_capture_settled = bool(
                ocr_reader_tick is not None
                and getattr(ocr_reader_tick, "stable_event_emitted", False)
            )
            ocr_reader_stable_event_emitted = pending_capture_settled
            ocr_reader_capture_failed = bool(
                ocr_reader_tick is not None
                and isinstance(getattr(ocr_reader_tick, "runtime", None), dict)
                and str(ocr_reader_tick.runtime.get("detail") or "") == "capture_failed"
            )
            pending_capture_expired = (
                self._pending_ocr_advance_capture_age()
                >= _OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS
            )
            if pending_ocr_advance_capture and pending_capture_expired:
                self._clear_pending_ocr_advance_captures()
                pending_ocr_advance_capture = False
            elif (
                pending_ocr_advance_capture
                and not ocr_reader_capture_failed
                and (force or pending_capture_settled)
            ):
                self._consume_ocr_advance_capture()
                pending_ocr_advance_capture = self._has_pending_ocr_advance_capture()
            self._ocr_reader_tick_lock.release()
        ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
        ocr_reader_runtime.update(tick_execution_diagnostics)
        return (
            raw_available_game_ids,
            raw_candidates,
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            ocr_reader_stable_event_emitted,
            warnings,
        )

    def _filter_candidates_for_reader_mode(
        self,
        *,
        raw_available_game_ids: list[str],
        raw_candidates: dict[str, Any],
        memory_reader_runtime: dict[str, Any],
        ocr_reader_runtime: dict[str, Any],
        reader_mode: str,
    ) -> tuple[list[str], dict[str, Any]]:
        available_game_ids, candidates = filter_memory_reader_candidates(
            raw_available_game_ids,
            raw_candidates,
            runtime=memory_reader_runtime,
        )
        available_game_ids, candidates = filter_ocr_reader_candidates(
            available_game_ids,
            candidates,
            runtime=ocr_reader_runtime,
        )
        if reader_mode == READER_MODE_MEMORY:
            candidates = {
                game_id: candidate
                for game_id, candidate in candidates.items()
                if candidate.data_source != DATA_SOURCE_OCR_READER
            }
            available_game_ids = [game_id for game_id in available_game_ids if game_id in candidates]
        elif reader_mode == READER_MODE_OCR:
            candidates = {
                game_id: candidate
                for game_id, candidate in candidates.items()
                if candidate.data_source != DATA_SOURCE_MEMORY_READER
            }
            available_game_ids = [game_id for game_id in available_game_ids if game_id in candidates]
        return available_game_ids, candidates

    def _finalize_bridge_poll_state(
        self,
        local: dict[str, Any],
        *,
        warnings: list[str],
        now_monotonic: float,
        ocr_trigger_mode: str,
        ocr_reader_runtime: dict[str, Any],
        after_advance_screen_refresh_needed: bool,
        companion_after_advance_ocr_refresh_needed: bool,
    ) -> None:
        if self._cfg is None:
            return
        if warnings:
            local["last_error"] = make_error(
                "; ".join(warnings[:3]),
                source="bridge_reader",
                kind="warning",
            )
        elif (
            isinstance(local.get("last_error"), dict)
            and str(local["last_error"].get("kind") or "") == "warning"
            and not str(local.get("plugin_error") or "")
        ):
            local["last_error"] = {}

        local["current_connection_state"] = derive_connection_state(
            bridge_root=self._cfg.bridge_root,
            plugin_error=str(local["plugin_error"]),
            active_session_id=str(local["active_session_id"]),
            last_seen_data_monotonic=float(local["last_seen_data_monotonic"]),
            now_monotonic=now_monotonic,
            stale_after_seconds=self._cfg.stale_after_seconds,
            stream_reset_pending=bool(local["stream_reset_pending"]),
        )
        interval = next_poll_interval_for_state(
            local["current_connection_state"],
            stream_reset_pending=bool(local["stream_reset_pending"]),
            config=self._cfg,
        )
        if (
            self._cfg.ocr_reader_enabled
            and ocr_trigger_mode == OCR_TRIGGER_MODE_INTERVAL
            and str(ocr_reader_runtime.get("status") or "") in {"starting", "active"}
            and str(local.get("active_data_source") or "") == DATA_SOURCE_OCR_READER
        ):
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        elif (
            self._cfg.ocr_reader_enabled
            and ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            and str(ocr_reader_runtime.get("status") or "") == "starting"
        ):
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        elif self._cfg.ocr_reader_enabled and after_advance_screen_refresh_needed:
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        elif self._cfg.ocr_reader_enabled and companion_after_advance_ocr_refresh_needed:
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        if self._has_pending_ocr_advance_capture():
            next_pending_delay = self._pending_ocr_advance_capture_delay_remaining()
            interval = min(
                interval,
                next_pending_delay
                if next_pending_delay > 0.0
                else _OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS,
            )
        local["next_poll_at_monotonic"] = now_monotonic + interval
        self._commit_state(local)

        try:
            self._config_service.persist_runtime_state(local)
        except Exception as exc:
            self._record_error(
                make_error(
                    f"persist runtime failed: {exc}",
                    source="store",
                    kind="error",
                )
            )

    async def _apply_bridge_candidate_session(
        self,
        *,
        local: dict[str, Any],
        candidate: Any,
        warnings: list[str],
        now_monotonic: float,
    ) -> None:
        if self._cfg is None:
            return

        session = candidate.session
        session_id = str(session.get("session_id") or "")
        session_changed = (
            candidate.game_id != local["active_game_id"]
            or session_id != local["active_session_id"]
        )
        restore_cursor = (
            not session_changed
            and local["events_byte_offset"] > 0
            and local["active_session_id"] == session_id
        )
        warmup_needed = session_id != local["warmup_session_id"] or session_changed

        local["active_game_id"] = candidate.game_id
        local["active_session_id"] = session_id
        local["active_session_meta"] = build_active_session_meta(candidate)
        local["active_data_source"] = candidate.data_source
        local["latest_snapshot"] = json_copy(session.get("state", {}))
        if self._context_snapshot_needs_reload(
            local.get("context_snapshot"),
            current_game_id=candidate.game_id,
        ):
            local["context_snapshot"] = await asyncio.to_thread(
                self._load_context_snapshot_for_game,
                candidate.game_id,
            )

        if warmup_needed:
            end_offset = int(local["events_byte_offset"]) if restore_cursor else None
            warmup_events = await asyncio.to_thread(
                warmup_replay_events,
                candidate.events_path,
                bytes_limit=self._cfg.warmup_replay_bytes_limit,
                events_limit=self._cfg.warmup_replay_events_limit,
                end_offset=end_offset,
            )
            base_dedupe = list(local["dedupe_window"]) if restore_cursor else []
            (
                local["history_events"],
                local["history_lines"],
                local["history_observed_lines"],
                local["history_choices"],
                local["dedupe_window"],
                local["latest_snapshot"],
            ) = rebuild_histories_from_events(
                events=warmup_events,
                snapshot=local["latest_snapshot"],
                dedupe_window=base_dedupe,
                config=self._cfg,
                game_id=candidate.game_id,
            )
            try:
                file_size = await asyncio.to_thread(lambda: candidate.events_path.stat().st_size)
            except OSError:
                file_size = 0
            if restore_cursor and int(local["events_byte_offset"]) <= file_size:
                local["events_file_size"] = file_size
                local["last_seq"] = int(local["last_seq"])
            else:
                local["events_byte_offset"] = file_size
                local["events_file_size"] = file_size
                local["last_seq"] = max(
                    int(session.get("last_seq") or 0),
                    max((int(event.get("seq") or 0) for event in warmup_events), default=0),
                )
            local["line_buffer"] = b""
            local["stream_reset_pending"] = False
            local["warmup_session_id"] = session_id
            local["last_seen_data_monotonic"] = now_monotonic

        if int(session.get("last_seq") or 0) > int(local["last_seq"]):
            local["last_seen_data_monotonic"] = now_monotonic

        read_offset = 0 if local["stream_reset_pending"] else int(local["events_byte_offset"])
        read_buffer = b"" if local["stream_reset_pending"] else bytes(local["line_buffer"])
        tail = await asyncio.to_thread(
            tail_events_jsonl,
            candidate.events_path,
            offset=read_offset,
            line_buffer=read_buffer,
        )
        warnings.extend(tail.errors)

        if tail.reset_detected:
            local["stream_reset_pending"] = True
            local["line_buffer"] = b""
            local["events_file_size"] = tail.file_size
            return

        confirm_reset = False
        if local["stream_reset_pending"] and tail.events:
            first = tail.events[0]
            first_seq = int(first.get("seq") or 0)
            first_session_id = str(first.get("session_id") or "")
            confirm_reset = first_seq == 1 and (
                first_session_id != local["active_session_id"]
                or int(local["last_seq"]) > 0
            )

        if confirm_reset:
            local["history_events"] = []
            local["history_lines"] = []
            local["history_observed_lines"] = []
            local["history_choices"] = []
            local["dedupe_window"] = []
            local["line_buffer"] = b""
            local["events_byte_offset"] = 0
            local["last_seq"] = 0
            local["stream_reset_pending"] = False

        if local["stream_reset_pending"]:
            return

        for event in tail.events:
            if str(event.get("session_id") or "") != local["active_session_id"]:
                continue
            seq = int(event.get("seq") or 0)
            if seq <= int(local["last_seq"]):
                continue
            apply_event_to_histories(
                history_events=local["history_events"],
                history_lines=local["history_lines"],
                history_observed_lines=local["history_observed_lines"],
                history_choices=local["history_choices"],
                dedupe_window=local["dedupe_window"],
                event=event,
                config=self._cfg,
                game_id=candidate.game_id,
            )
            local["latest_snapshot"] = apply_event_to_snapshot(
                local["latest_snapshot"], event
            )
            local["last_seq"] = seq
            local["last_seen_data_monotonic"] = now_monotonic

        local["events_byte_offset"] = tail.next_offset
        local["events_file_size"] = tail.file_size
        local["line_buffer"] = tail.line_buffer

    def _clear_bridge_candidate_session(
        self,
        *,
        local: dict[str, Any],
        reader_mode: str,
        memory_reader_allowed: bool,
        ocr_reader_allowed: bool,
        memory_reader_candidate_available: bool,
    ) -> None:
        local["active_data_source"] = _pending_data_source_for_reader_mode(
            reader_mode,
            memory_reader_allowed=memory_reader_allowed,
            ocr_reader_allowed=ocr_reader_allowed,
            memory_reader_candidate_available=memory_reader_candidate_available,
        )
        if not local["bound_game_id"]:
            local["active_game_id"] = ""
            local["active_session_id"] = ""
            local["active_session_meta"] = {}
        local["line_buffer"] = b""

    async def _poll_bridge_locked(self, *, force: bool) -> None:
        if self._cfg is None:
            return

        now_monotonic = time.monotonic()
        local = self._snapshot_state(fresh=True)
        local["_commit_base"] = {
            "bound_game_id": str(local.get("bound_game_id") or ""),
            "mode": str(local.get("mode") or ""),
            "push_notifications": bool(local.get("push_notifications")),
            "advance_speed": str(local.get("advance_speed") or ""),
            "active_data_source": str(local.get("active_data_source") or ""),
            "character_mode": str(local.get("character_mode") or "off"),
            "character_fixed_name": str(local.get("character_fixed_name") or ""),
            "character_profiles": json_copy(local.get("character_profiles") or {}),
            "character_runtime_state": json_copy(local.get("character_runtime_state") or {}),
            "cross_scene_memory": json_copy(local.get("cross_scene_memory") or {}),
            "last_push_seq": int(local.get("last_push_seq") or 0),
            "ocr_capture_profiles": json_copy(local.get("ocr_capture_profiles") or {}),
            "ocr_window_target": json_copy(local.get("ocr_window_target") or {}),
            # Track dependency_status in the snapshot base so a parallel
            # _refresh_dependency_status() call (e.g. from install_textractor)
            # isn't clobbered by the stale poll-snapshot when the bridge tick
            # commits its payload.
            "dependency_status": json_copy(local.get("dependency_status") or {}),
        }
        next_poll_at = float(local["next_poll_at_monotonic"])
        max_reasonable_interval = max(
            float(self._cfg.active_poll_interval_seconds),
            float(self._cfg.idle_poll_interval_seconds),
            float(self._cfg.ocr_reader_poll_interval_seconds),
            1.0,
        ) * 5.0
        if not force and next_poll_at > now_monotonic + max_reasonable_interval:
            local["next_poll_at_monotonic"] = 0.0
            next_poll_at = 0.0
        if not force and now_monotonic < next_poll_at:
            return

        warnings: list[str] = []
        raw_available_game_ids: list[str] = []
        raw_candidates: dict[str, Any] = {}
        memory_reader_runtime = json_copy(local.get("memory_reader_runtime") or {})
        ocr_reader_runtime = json_copy(local.get("ocr_reader_runtime") or {})
        reader_mode = _normalize_reader_mode(getattr(self._cfg, "reader_mode", READER_MODE_AUTO))
        memory_reader_allowed = reader_mode in {READER_MODE_AUTO, READER_MODE_MEMORY}
        ocr_reader_allowed = reader_mode in {READER_MODE_AUTO, READER_MODE_OCR}
        ocr_reader_allowed_block_reason = (
            "reader_mode_memory_only" if reader_mode == READER_MODE_MEMORY else ""
        )

        try:
            raw_available_game_ids, raw_candidates, scan_warnings = await self._scan_candidates()
            warnings.extend(scan_warnings)
        except Exception as exc:
            self._commit_bridge_scan_failure(
                local,
                now_monotonic=now_monotonic,
                exc=exc,
            )
            return

        memory_reader_candidate_available = any(
            candidate.data_source == DATA_SOURCE_MEMORY_READER
            and _session_candidate_has_text(candidate)
            for candidate in raw_candidates.values()
        )
        bridge_sdk_candidate_available = any(
            candidate.data_source == DATA_SOURCE_BRIDGE_SDK
            and _session_candidate_has_text(candidate)
            for candidate in raw_candidates.values()
        )
        ocr_trigger_mode = str(
            getattr(self._cfg, "ocr_reader_trigger_mode", OCR_TRIGGER_MODE_INTERVAL)
            or OCR_TRIGGER_MODE_INTERVAL
        )
        ocr_context_state = str(ocr_reader_runtime.get("ocr_context_state") or "")
        ocr_bootstrap_capture_needed = (
            ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            and (
                ocr_context_state in {"", "capture_pending", "observed"}
                or (
                    ocr_context_state == "no_text"
                    and int(ocr_reader_runtime.get("consecutive_no_text_polls") or 0) < 3
                )
            )
        )
        pending_ocr_advance_capture = self._has_pending_ocr_advance_capture()
        with self._state_lock:
            pending_ocr_advance_reason = str(self._last_ocr_advance_capture_reason or "")
        pending_manual_foreground_ocr_capture = (
            pending_ocr_advance_capture
            and ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            and pending_ocr_advance_reason in {
                "manual_foreground_advance",
                "foreground_target_activated",
            }
        )
        pending_ocr_delay_remaining = (
            self._pending_ocr_advance_capture_delay_remaining()
            if pending_ocr_advance_capture and not force
            else 0.0
        )
        (
            raw_available_game_ids,
            raw_candidates,
            memory_reader_runtime,
            memory_tick_warnings,
        ) = await self._tick_memory_reader_for_poll(
            memory_reader_allowed=memory_reader_allowed,
            bridge_sdk_candidate_available=bridge_sdk_candidate_available,
            raw_available_game_ids=raw_available_game_ids,
            raw_candidates=raw_candidates,
            memory_reader_runtime=memory_reader_runtime,
        )
        warnings.extend(memory_tick_warnings)
        current_memory_target = (
            getattr(self._memory_reader_manager, "current_process_target", None)
            if self._memory_reader_manager is not None
            else None
        )
        if callable(current_memory_target):
            resolved_memory_target = current_memory_target()
            if resolved_memory_target != json_copy(local.get("memory_reader_target") or {}):
                local["memory_reader_target"] = json_copy(resolved_memory_target)
                try:
                    self._persist.persist_memory_reader_target(resolved_memory_target)
                except Exception as exc:
                    warnings.append(f"persist memory reader target failed: {exc}")
        memory_reader_candidate_available = any(
            candidate.data_source == DATA_SOURCE_MEMORY_READER
            and _session_candidate_has_text(candidate)
            for candidate in raw_candidates.values()
        )
        if memory_reader_allowed:
            memory_reader_recent_text_available = self._update_memory_reader_text_freshness(
                memory_reader_runtime,
                now_monotonic=now_monotonic,
            )
        else:
            self._update_memory_reader_text_freshness({}, now_monotonic=now_monotonic)
            memory_reader_recent_text_available = False
        ocr_reader_explicitly_configured = bool(
            (
                bool(getattr(self._cfg, "ocr_reader_enabled", False))
                and bool(getattr(self._cfg, "ocr_reader_enabled_explicit", False))
            )
            or str(getattr(self._cfg, "ocr_reader_install_target_dir", "") or "").strip()
            or str(getattr(self._cfg, "rapidocr_install_target_dir", "") or "").strip()
            or (
                bool(getattr(self._cfg, "rapidocr_enabled", False))
                and bool(getattr(self._cfg, "rapidocr_enabled_explicit", False))
            )
            or (
                bool(getattr(self._cfg, "ocr_reader_backend_selection_explicit", False))
                and str(getattr(self._cfg, "ocr_reader_backend_selection", "") or "")
                .strip()
                .lower()
                in {"rapidocr"}
            )
            or (
                bool(getattr(self._cfg, "ocr_reader_capture_backend_explicit", False))
                and str(getattr(self._cfg, "ocr_reader_capture_backend", "") or "")
                .strip()
                .lower()
                in {"smart", "dxcam", "mss", "printwindow"}
            )
        )
        memory_reader_default_is_unavailable = (
            reader_mode == READER_MODE_AUTO
            and memory_reader_allowed
            and bool(getattr(self._cfg, "memory_reader_enabled", False))
            and not memory_reader_candidate_available
            and str(memory_reader_runtime.get("status") or "") in {"idle", "backoff"}
            and str(memory_reader_runtime.get("detail") or "")
            in {"invalid_textractor_path", "no_detected_game_process"}
            and not ocr_reader_explicitly_configured
            and not pending_manual_foreground_ocr_capture
            and not (
                str(local.get("active_data_source") or "") == DATA_SOURCE_OCR_READER
                and bool(str(local.get("active_session_id") or ""))
            )
        )
        if memory_reader_default_is_unavailable:
            ocr_reader_allowed = False
            ocr_reader_allowed_block_reason = _ocr_reader_allowed_block_reason(
                reader_mode=reader_mode,
                memory_reader_default_is_unavailable=memory_reader_default_is_unavailable,
                memory_reader_recent_text_available=False,
            )
        if (
            reader_mode == READER_MODE_AUTO
            and memory_reader_recent_text_available
            and not pending_manual_foreground_ocr_capture
        ):
            ocr_reader_allowed = False
            ocr_reader_allowed_block_reason = _ocr_reader_allowed_block_reason(
                reader_mode=reader_mode,
                memory_reader_default_is_unavailable=memory_reader_default_is_unavailable,
                memory_reader_recent_text_available=True,
            )
            with self._state_lock:
                self._clear_pending_ocr_advance_captures_locked()

        ocr_reader_stable_event_emitted = False
        (
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            pending_ocr_delay_remaining,
            foreground_refresh_warnings,
        ) = await self._refresh_ocr_foreground_for_poll(
            ocr_reader_runtime=ocr_reader_runtime,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode=ocr_trigger_mode,
            pending_ocr_advance_capture=pending_ocr_advance_capture,
            pending_ocr_delay_remaining=pending_ocr_delay_remaining,
        )
        warnings.extend(foreground_refresh_warnings)
        after_advance_screen_refresh_tick_needed = _after_advance_screen_refresh_needed(
            local=local,
            ocr_reader_runtime=ocr_reader_runtime,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode=ocr_trigger_mode,
        )
        companion_after_advance_ocr_refresh_tick_needed = (
            _companion_after_advance_ocr_refresh_needed(
                local=local,
                ocr_reader_runtime=ocr_reader_runtime,
                ocr_reader_allowed=ocr_reader_allowed,
                ocr_trigger_mode=ocr_trigger_mode,
            )
        )
        ocr_tick_gate_allowed = (
            ocr_reader_allowed
            and (
                ocr_trigger_mode == OCR_TRIGGER_MODE_INTERVAL
                or force
                or ocr_bootstrap_capture_needed
                or after_advance_screen_refresh_tick_needed
                or companion_after_advance_ocr_refresh_tick_needed
                or (pending_ocr_advance_capture and pending_ocr_delay_remaining <= 0.0)
                or str(ocr_reader_runtime.get("status") or "") not in {"active"}
                or str(local.get("active_data_source") or "") != DATA_SOURCE_OCR_READER
            )
        )
        ocr_tick_allowed = bool(ocr_tick_gate_allowed and self._ocr_reader_manager is not None)
        ocr_tick_block_reason = _ocr_tick_block_reason(
            ocr_tick_allowed=ocr_tick_allowed,
            ocr_reader_manager_available=self._ocr_reader_manager is not None,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_reader_allowed_block_reason=ocr_reader_allowed_block_reason,
            ocr_trigger_mode=ocr_trigger_mode,
            pending_ocr_advance_capture=pending_ocr_advance_capture,
            pending_ocr_delay_remaining=pending_ocr_delay_remaining,
            ocr_bootstrap_capture_needed=ocr_bootstrap_capture_needed,
            after_advance_screen_refresh_needed=after_advance_screen_refresh_tick_needed,
            companion_after_advance_ocr_refresh_needed=(
                companion_after_advance_ocr_refresh_tick_needed
            ),
            ocr_reader_runtime=ocr_reader_runtime,
            active_data_source=str(local.get("active_data_source") or ""),
            mode=str(local.get("mode") or ""),
        )
        pending_ocr_advance_clear_reason = ""
        pending_ocr_advance_age = self._pending_ocr_advance_capture_age()
        ocr_reader_capture_failed_pending = (
            str(ocr_reader_runtime.get("detail") or "") == "capture_failed"
            or str(ocr_reader_runtime.get("ocr_context_state") or "") == "capture_failed"
        )
        if (
            pending_ocr_advance_capture
            and not ocr_tick_allowed
            and not ocr_reader_capture_failed_pending
            and pending_ocr_advance_age >= _OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS
        ):
            self._clear_pending_ocr_advance_captures()
            pending_ocr_advance_capture = False
            pending_manual_foreground_ocr_capture = False
            pending_ocr_delay_remaining = 0.0
            pending_ocr_advance_clear_reason = "tick_gate_timeout"
            pending_ocr_advance_reason = ""
        ocr_tick_gate_diagnostics = {
            "ocr_tick_gate_allowed": bool(ocr_tick_gate_allowed),
            "ocr_reader_manager_available": self._ocr_reader_manager is not None,
            "pending_ocr_advance_capture": bool(pending_ocr_advance_capture),
            "pending_manual_foreground_ocr_capture": bool(
                pending_manual_foreground_ocr_capture
            ),
            "pending_ocr_advance_reason": str(pending_ocr_advance_reason or ""),
            "pending_ocr_delay_remaining": float(pending_ocr_delay_remaining or 0.0),
            "pending_ocr_advance_capture_age_seconds": float(
                pending_ocr_advance_age or 0.0
            ),
            "pending_ocr_advance_clear_reason": pending_ocr_advance_clear_reason,
            "ocr_bootstrap_capture_needed": bool(ocr_bootstrap_capture_needed),
            "after_advance_screen_refresh_tick_needed": bool(
                after_advance_screen_refresh_tick_needed
            ),
            "companion_after_advance_ocr_refresh_tick_needed": bool(
                companion_after_advance_ocr_refresh_tick_needed
            ),
            "ocr_runtime_status": str(ocr_reader_runtime.get("status") or ""),
            "active_data_source": str(local.get("active_data_source") or ""),
            "mode": str(local.get("mode") or ""),
            "foreground_refresh_attempted": bool(
                ocr_reader_runtime.get("foreground_refresh_attempted")
            ),
            "foreground_refresh_skipped_reason": str(
                ocr_reader_runtime.get("foreground_refresh_skipped_reason") or ""
            ),
        }

        (
            raw_available_game_ids,
            raw_candidates,
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            ocr_reader_stable_event_emitted,
            ocr_tick_warnings,
        ) = await self._tick_ocr_reader_for_poll(
            local=local,
            raw_available_game_ids=raw_available_game_ids,
            raw_candidates=raw_candidates,
            memory_reader_runtime=memory_reader_runtime,
            ocr_reader_runtime=ocr_reader_runtime,
            bridge_sdk_candidate_available=bridge_sdk_candidate_available,
            ocr_tick_allowed=ocr_tick_allowed,
            pending_manual_foreground_ocr_capture=pending_manual_foreground_ocr_capture,
            pending_ocr_advance_capture=pending_ocr_advance_capture,
            force=force,
        )
        warnings.extend(ocr_tick_warnings)
        if not pending_ocr_advance_capture and not pending_ocr_advance_clear_reason:
            pending_ocr_advance_reason = ""
            pending_ocr_delay_remaining = 0.0
        ocr_tick_gate_diagnostics.update(
            {
                "pending_ocr_advance_capture": bool(pending_ocr_advance_capture),
                "pending_manual_foreground_ocr_capture": bool(
                    pending_manual_foreground_ocr_capture
                    and pending_ocr_advance_capture
                ),
                "pending_ocr_advance_reason": str(pending_ocr_advance_reason or ""),
                "pending_ocr_delay_remaining": float(pending_ocr_delay_remaining or 0.0),
                "ocr_runtime_status": str(ocr_reader_runtime.get("status") or ""),
                "foreground_refresh_attempted": bool(
                    ocr_reader_runtime.get("foreground_refresh_attempted")
                ),
                "foreground_refresh_skipped_reason": str(
                    ocr_reader_runtime.get("foreground_refresh_skipped_reason") or ""
                ),
            }
        )
        ocr_emit_block_reason = _ocr_emit_block_reason(
            ocr_tick_allowed=ocr_tick_allowed,
            ocr_reader_stable_event_emitted=ocr_reader_stable_event_emitted,
            ocr_reader_runtime=ocr_reader_runtime,
        )
        ocr_reader_runtime = _apply_ocr_decision_diagnostics(
            ocr_reader_runtime,
            ocr_tick_allowed=ocr_tick_allowed,
            ocr_tick_block_reason=ocr_tick_block_reason,
            ocr_emit_block_reason=ocr_emit_block_reason,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_reader_allowed_block_reason=ocr_reader_allowed_block_reason,
            ocr_trigger_mode=ocr_trigger_mode,
            active_data_source=str(local.get("active_data_source") or ""),
            ocr_tick_gate_diagnostics=ocr_tick_gate_diagnostics,
        )

        local["memory_reader_runtime"] = memory_reader_runtime
        local["ocr_reader_runtime"] = ocr_reader_runtime
        available_game_ids, candidates = self._filter_candidates_for_reader_mode(
            raw_available_game_ids=raw_available_game_ids,
            raw_candidates=raw_candidates,
            memory_reader_runtime=memory_reader_runtime,
            ocr_reader_runtime=ocr_reader_runtime,
            reader_mode=reader_mode,
        )
        local["available_game_ids"] = available_game_ids
        candidate_reader_mode = reader_mode
        if (
            reader_mode == READER_MODE_AUTO
            and pending_manual_foreground_ocr_capture
            and ocr_reader_stable_event_emitted
            and not bridge_sdk_candidate_available
        ):
            candidate_reader_mode = READER_MODE_OCR
        elif (
            reader_mode == READER_MODE_AUTO
            and not memory_reader_recent_text_available
            and not bridge_sdk_candidate_available
            and any(
                candidate.data_source == DATA_SOURCE_OCR_READER
                for candidate in candidates.values()
            )
        ):
            candidate_reader_mode = READER_MODE_OCR

        keep_current = (
            not local["bound_game_id"]
            and local["current_connection_state"] == STATE_ACTIVE
            and bool(local["active_game_id"])
        )
        candidate = choose_candidate(
            candidates,
            bound_game_id=str(local["bound_game_id"]),
            current_game_id=str(local["active_game_id"]),
            keep_current=keep_current,
            reader_mode=candidate_reader_mode,
        )

        if candidate is not None:
            await self._apply_bridge_candidate_session(
                local=local,
                candidate=candidate,
                warnings=warnings,
                now_monotonic=now_monotonic,
            )
        else:
            self._clear_bridge_candidate_session(
                local=local,
                reader_mode=reader_mode,
                memory_reader_allowed=memory_reader_allowed,
                ocr_reader_allowed=ocr_reader_allowed,
                memory_reader_candidate_available=memory_reader_recent_text_available,
            )

        after_advance_screen_refresh_schedule_needed = _after_advance_screen_refresh_needed(
            local=local,
            ocr_reader_runtime=ocr_reader_runtime,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode=ocr_trigger_mode,
        )
        companion_after_advance_ocr_refresh_schedule_needed = (
            _companion_after_advance_ocr_refresh_needed(
                local=local,
                ocr_reader_runtime=ocr_reader_runtime,
                ocr_reader_allowed=ocr_reader_allowed,
                ocr_trigger_mode=ocr_trigger_mode,
            )
        )
        self._finalize_bridge_poll_state(
            local,
            warnings=warnings,
            now_monotonic=now_monotonic,
            ocr_trigger_mode=ocr_trigger_mode,
            ocr_reader_runtime=ocr_reader_runtime,
            after_advance_screen_refresh_needed=after_advance_screen_refresh_schedule_needed,
            companion_after_advance_ocr_refresh_needed=(
                companion_after_advance_ocr_refresh_schedule_needed
            ),
        )



    # NOTE: RapidOCR / DXcam runtime SDK install actions removed —
    # both packages are now bundled into the main program (see pyproject.toml
    # [dependency-groups] galgame). Run `uv sync --group galgame` for source
    # installs; packaged builds always include them.































    # ------------------------------------------------------------------
    # Query / debug entries (galgame-host-play-mode plan, step 19 + G4)
    # ------------------------------------------------------------------

    def _check_query_rate_limit(
        self, entry_id: str, *, window_seconds: float = 60.0
    ) -> dict[str, Any] | None:
        """Return a throttle payload when ``entry_id`` exceeds its budget; else
        record the current call and return ``None``.
        """
        bucket = self._query_rate_limits.get(entry_id)
        if bucket is None:
            return None
        now = time.monotonic()
        cutoff = now - max(1.0, float(window_seconds))
        # Drop expired timestamps from the head; deque has no slice removal so
        # we filter and rebuild in place.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= bucket.maxlen:
            retry_after = max(0.0, bucket[0] + window_seconds - now)
            return {
                "throttled": True,
                "retry_after_seconds": round(retry_after, 2),
                "entry": entry_id,
            }
        bucket.append(now)
        return None

    def _layer1_scene_summaries(self) -> list[dict[str, Any]]:
        """Best-effort access to Layer 1 scene summaries (existing scene_memory)."""
        agent = self._game_agent
        if agent is None:
            return []
        tracker = getattr(agent, "_scene_tracker", None)
        if tracker is None:
            return []
        scene_memory = getattr(tracker, "scene_memory", None)
        if not isinstance(scene_memory, list):
            return []
        return [dict(entry) for entry in scene_memory if isinstance(entry, dict)]

    def _compose_story_so_far_from_scene_summaries(
        self,
        scenes: list[dict[str, Any]],
        *,
        limit: int = 6,
        max_chars: int = 1200,
    ) -> tuple[str, int]:
        recent = [
            dict(item)
            for item in scenes
            if isinstance(item, dict) and str(item.get("summary") or "").strip()
        ][-max(1, int(limit or 6)) :]
        if not recent:
            return "", 0
        parts: list[str] = []
        last_seq = 0
        for index, entry in enumerate(recent, 1):
            summary = str(entry.get("summary") or "").strip()
            if not summary:
                continue
            scene_id = str(entry.get("scene_id") or "").strip()
            prefix = f"{index}. "
            if scene_id:
                prefix += f"{scene_id}: "
            parts.append(prefix + summary)
            try:
                last_seq = max(last_seq, int(entry.get("push_seq") or 0))
            except (TypeError, ValueError):
                pass
        story = "\n".join(parts).strip()
        if len(story) > max_chars:
            story = story[-max_chars:].lstrip()
        return story, last_seq

    def _refresh_story_so_far_from_scene_summaries(self) -> bool:
        story, last_seq = self._compose_story_so_far_from_scene_summaries(
            self._layer1_scene_summaries()
        )
        if not story:
            with self._state_lock:
                self._story_so_far = ""
                self._story_last_updated_seq = 0
            return False
        with self._state_lock:
            current_story = str(self._story_so_far or "").strip()
            current_seq = int(self._story_last_updated_seq or 0)
            next_seq = int(last_seq or 0)
            if current_story:
                if next_seq <= 0:
                    if current_seq > 0 or story == current_story:
                        return False
                elif next_seq <= current_seq:
                    return False
            self._story_so_far = story
            self._story_last_updated_seq = max(
                int(self._story_last_updated_seq or 0),
                next_seq,
            )
        return True

    def _record_story_progress_from_scene_summary(
        self,
        *,
        scene_id: str,
        route_id: str = "",
        summary: str,
        push_seq: int = 0,
    ) -> None:
        normalized_summary = str(summary or "").strip()
        if not normalized_summary:
            return
        normalized_scene_id = str(scene_id or "").strip()
        scenes = self._layer1_scene_summaries()
        merged: list[dict[str, Any]] = []
        replaced = False
        for entry in scenes:
            item = dict(entry)
            if normalized_scene_id and str(item.get("scene_id") or "") == normalized_scene_id:
                item["summary"] = normalized_summary
                if route_id:
                    item["route_id"] = str(route_id or "")
                try:
                    existing_push_seq = int(item.get("push_seq") or 0)
                except (TypeError, ValueError):
                    existing_push_seq = 0
                item["push_seq"] = max(existing_push_seq, int(push_seq or 0))
                replaced = True
            merged.append(item)
        if not replaced:
            merged.append(
                {
                    "scene_id": normalized_scene_id,
                    "route_id": str(route_id or ""),
                    "summary": normalized_summary,
                    "push_seq": int(push_seq or 0),
                }
            )
        story, last_seq = self._compose_story_so_far_from_scene_summaries(merged)
        if not story:
            return
        with self._state_lock:
            self._story_so_far = story
            self._story_last_updated_seq = max(
                int(self._story_last_updated_seq or 0),
                int(last_seq or 0),
                int(push_seq or 0),
            )


GalgameBridgePlugin = GalgamePlugin
