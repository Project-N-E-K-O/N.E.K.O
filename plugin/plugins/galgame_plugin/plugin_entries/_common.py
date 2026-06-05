"""Shared imports for plugin_entries mixin files.

Each mixin file does `from ._common import *` to inherit all names that
entry method bodies reference. This avoids per-file import bookkeeping
and keeps each mixin focused on its method definition.
"""
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

from ..character_profile import CharacterProfileManager
from ..game_llm_agent import GameLLMAgent
from ..host_agent_adapter import HostAgentAdapter
from ..llm_gateway import LLMGateway
from ..memory_reader import MemoryReaderManager
from ..ocr_reader import OcrReaderManager, utc_now_iso
from ..models import (
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
    normalize_rapidocr_ocr_version,
)
from ..dependency_status import (
    infer_inspection_failed_dependencies,
    infer_missing_dependencies,
)
from plugin.plugins._shared.rapidocr.rapidocr_support import (
    inspect_rapidocr_installation as _inspect_rapidocr_installation,
)
from ..dxcam_support import inspect_dxcam_installation
from ..reader import tail_events_jsonl, warmup_replay_events
from ..service import (
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
from ..state import GalgameSharedState, build_initial_state
from ..store import GalgameStore
from ..textractor_support import install_textractor
from ..ui_api import build_open_ui_payload
from ..screen_classifier import classify_screen_from_ocr, normalize_screen_type
from ..screen_awareness_training import (
    evaluate_screen_awareness_model,
    train_screen_awareness_model,
)
from ..plugin_util_helpers import (
    _log_plugin_noncritical,
    _package_public_attr,
    _public_context_snapshot,
    _migrate_legacy_capture_backend,
    _duration_percentile,
    _duration_summary,
    _open_url_in_browser,
)
from ..plugin_capture_profile_helpers import (
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
from ..plugin_constants import (
    _OCR_BACKEND_SELECTIONS,
    _OCR_CAPTURE_BACKEND_SELECTIONS,
)
from ..plugin_ocr_helpers import (
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


def inspect_rapidocr_installation(**kwargs):
    kwargs.setdefault("plugin_id", "galgame_plugin")
    return _inspect_rapidocr_installation(**kwargs)


def _coerce_int_range(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


# Auto-compute __all__ from the module's globals so that `from ._common import *`
# in mixin files exposes every imported name (including underscore-prefixed
# private helpers). Computing it here — instead of hand-maintaining a long list —
# prevents drift: any new import added above is automatically re-exported, and
# any removed import disappears from __all__ at the same time. The dunder filter
# keeps Python's own attributes (__name__, __doc__, __builtins__, ...) out of
# the star-import surface.
__all__ = [_name for _name in globals() if not _name.startswith("__") and _name != "_name"]
