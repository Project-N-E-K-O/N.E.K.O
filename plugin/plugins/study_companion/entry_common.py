"""Shared imports for study_companion entry mixin files.

Entry mixin files import their required shared names from here explicitly so the
mechanical split keeps a stable dependency boundary.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Mapping
from contextlib import asynccontextmanager
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
from . import tesseract_support
from plugin.plugins._shared.rapidocr import rapidocr_support
from plugin.server.routes._install_task_store import update_install_task_state


_MASTERY_THRESHOLDS = (0.3, 0.5, 0.7, 0.85)
_MAX_SUBMITTED_IMAGE_BASE64_LENGTH = 10 * 1024 * 1024
_MAX_SUBMITTED_IMAGE_BASE64_ENCODED_LENGTH = (
    (_MAX_SUBMITTED_IMAGE_BASE64_LENGTH + 2) // 3
) * 4 + 64
_SUPPORTED_SUBMITTED_IMAGE_MIME_BY_DATA_URL_PREFIX = {
    "data:image/jpeg;base64": "image/jpeg",
    "data:image/png;base64": "image/png",
}


@asynccontextmanager
async def _plugin_lock(lock: Any):
    if hasattr(lock, "__aenter__"):
        async with lock:
            yield
        return
    if hasattr(lock, "__enter__"):
        with lock:
            yield
        return
    yield


def _detect_submitted_image_mime(raw: bytes) -> str:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return ""


def _normalize_submitted_image_payload(image_base64: str) -> str:
    image_payload = str(image_base64 or "").strip()
    if not image_payload:
        raise ValueError("image_base64 is required")

    expected_mime = ""
    encoded_payload = image_payload
    if image_payload.lower().startswith("data:"):
        header, separator, encoded_payload = image_payload.partition(",")
        expected_mime = _SUPPORTED_SUBMITTED_IMAGE_MIME_BY_DATA_URL_PREFIX.get(
            header.strip().lower(),
            "",
        )
        if not separator or not encoded_payload.strip():
            raise ValueError("image_base64 data URL is malformed")
        if not expected_mime:
            raise ValueError("only JPEG/PNG data URLs are supported")
    encoded_payload = encoded_payload.strip()
    if len(encoded_payload) > _MAX_SUBMITTED_IMAGE_BASE64_ENCODED_LENGTH:
        raise ValueError("image_base64 is too large (max 10MB)")
    try:
        raw = base64.b64decode(encoded_payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image_base64 is not valid base64") from exc
    if not raw:
        raise ValueError("image_base64 is not valid base64")
    if len(raw) > _MAX_SUBMITTED_IMAGE_BASE64_LENGTH:
        raise ValueError("image_base64 is too large (max 10MB)")
    actual_mime = _detect_submitted_image_mime(raw)
    if not actual_mime:
        raise ValueError("only JPEG/PNG images are supported")
    if expected_mime and actual_mime != expected_mime:
        raise ValueError("image_base64 MIME does not match image data")
    return f"data:{actual_mime};base64,{encoded_payload}"


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
    crossed = [
        threshold
        for threshold in _MASTERY_THRESHOLDS
        if (before < threshold <= after) or (before >= threshold > after)
    ]
    return str(max(crossed)) if crossed else None


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


def _entry_exception_error(
    owner: Any,
    exc: BaseException,
    *,
    operation: str = "entry",
    message: str | None = None,
):
    logger = getattr(owner, "logger", None)
    if logger is not None and hasattr(logger, "warning"):
        try:
            logger.warning(
                "study entry failed: {}",
                operation,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        except Exception:
            pass
    return Err(SdkError(str(exc) if message is None else message))


__all__ = [
    "Any",
    "Mapping",
    "Path",
    "SimpleNamespace",
    "ZoneInfo",
    "ZoneInfoNotFoundError",
    "asyncio",
    "base64",
    "binascii",
    "datetime",
    "math",
    "time",
    "Err",
    "NekoPluginBase",
    "Ok",
    "SdkError",
    "lifecycle",
    "neko_plugin",
    "plugin_entry",
    "tr",
    "LLM_OPERATION_ANSWER_EVALUATE",
    "LLM_OPERATION_CONCEPT_EXPLAIN",
    "LLM_OPERATION_KNOWLEDGE_TRACK",
    "LLM_OPERATION_QUESTION_GENERATE",
    "LLM_OPERATION_SUMMARIZE_SESSION",
    "MODE_COMPANION",
    "MODE_INTERACTIVE",
    "MODE_TEACHING",
    "DocExporter",
    "normalize_format",
    "CheckinManager",
    "StudyEvent",
    "StudyEventBus",
    "PomodoroTimer",
    "classify_screen_from_ocr",
    "MODE_CONCEPT_EXPLAIN",
    "STATUS_ERROR",
    "STATUS_READY",
    "STATUS_STOPPED",
    "StudyConfig",
    "StudyState",
    "TutorReply",
    "build_config",
    "utc_now_iso",
    "build_dependency_status",
    "build_explain_payload",
    "build_ocr_payload",
    "build_status_payload",
    "build_tutor_payload",
    "ModeManager",
    "build_transition_phrase",
    "handle_user_intent",
    "normalize_mode",
    "PublicGraphContributionBuilder",
    "KnowledgeTracker",
    "MemoryDeckStore",
    "MemoryItemNotFoundError",
    "MemoryHabitBridge",
    "build_initial_state",
    "StudyStore",
    "StudyHabitStore",
    "StudyOcrPipeline",
    "SupervisionController",
    "TutorLLMAgent",
    "diagnostic_code_for_exception",
    "build_open_ui_payload",
    "build_contribution_settings_payload",
    "build_knowledge_map_payload",
    "build_habit_dashboard_payload",
    "build_pomodoro_status_payload",
    "tesseract_support",
    "rapidocr_support",
    "update_install_task_state",
    "_validated_pomodoro_focus_minutes",
    "_detect_mastery_threshold_crossed",
    "_normalize_submitted_image_payload",
    "_plugin_lock",
    "_entry_exception_error",
    "_event_ratio",
    "_event_nonnegative_float",
]
