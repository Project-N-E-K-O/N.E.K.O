"""Shared imports for study_companion entry mixin files.

Each mixin file does `from ._common import *` so entry methods keep the
same globals they had before the mechanical split.
"""
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
    custom_event,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
)

from ..constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    MODE_INTERACTIVE,
    MODE_TEACHING,
)
from ..doc_exporter import DocExporter, normalize_format
from ..checkin_manager import CheckinManager
from .._event_bus import StudyEvent, StudyEventBus
from ..pomodoro_timer import PomodoroTimer
from ..screen_classifier import classify_screen_from_ocr
from ..models import (
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
from ..service import (
    build_dependency_status,
    build_explain_payload,
    build_ocr_payload,
    build_status_payload,
    build_tutor_payload,
)
from ..mode_manager import (
    ModeManager,
    build_transition_phrase,
    handle_user_intent,
    normalize_mode,
)
from ..knowledge_contribution import PublicGraphContributionBuilder
from ..knowledge_tracker import KnowledgeTracker
from ..memory_deck_store import MemoryDeckStore, MemoryItemNotFoundError
from ..memory_habit_bridge import MemoryHabitBridge
from ..state import build_initial_state
from ..store import StudyStore
from ..study_habit_store import StudyHabitStore
from ..study_ocr_pipeline import StudyOcrPipeline
from ..supervision import SupervisionController
from ..tutor_llm_agent import TutorLLMAgent
from ..tutor_llm_agent import diagnostic_code_for_exception
from ..ui_api import build_open_ui_payload
from ..ui_api import build_contribution_settings_payload, build_knowledge_map_payload
from ..ui_api import build_habit_dashboard_payload, build_pomodoro_status_payload
from ..voice_filter import VoiceFilter, _derive_subject, build_context_for_catgirl
from .. import tesseract_support
from plugin.plugins._shared.rapidocr import rapidocr_support
from plugin.server.routes._install_task_store import update_install_task_state


_MASTERY_THRESHOLDS = (0.3, 0.5, 0.7, 0.85)


def _voice_session_key(lanlan_name: str, metadata: Mapping[str, Any] | None) -> str:
    for key in ("voice_session_id", "session_id", "conversation_id", "request_session_id"):
        value = metadata.get(key) if isinstance(metadata, Mapping) else None
        text = str(value or "").strip()
        if text:
            return f"session:{text}"
    name = str(lanlan_name or "").strip()
    return f"lanlan:{name}" if name else "__default__"


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


__all__ = [name for name in globals() if not name.startswith("__") and name != "name"]
