from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Protocol
from uuid import uuid4

from .models import (
    ADVANCE_SPEED_FAST,
    ADVANCE_SPEED_MEDIUM,
    ADVANCE_SPEED_SLOW,
    ADVANCE_SPEEDS,
    DATA_SOURCE_OCR_READER,
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
    GalgameConfig,
    MENU_PREFIX_RE as _MENU_PREFIX_RE,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    json_copy,
    sanitize_screen_ui_elements,
    parse_ocr_capture_profile_bucket_key,
)
from .ocr_chrome_noise import (
    looks_like_temperature_status_line as _looks_like_temperature_status_line,
    looks_like_window_title_line as _looks_like_window_title_line,
)
from .dialogue_library import (
    DialogueLibraryMatch as _DialogueLibraryMatch,
    match_dialogue_library_for_target as _match_dialogue_library_for_target,
)
from .aihong_state import (
    AIHONG_CHOICES_REGION_PRESET as _AIHONG_CHOICES_REGION_PRESET,
    AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET as _AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET,
    AIHONG_DIALOGUE_STAGE as _AIHONG_DIALOGUE_STAGE,
    AIHONG_MENU_CAPTURE_PROFILE_PRESET as _AIHONG_MENU_CAPTURE_PROFILE_PRESET,
    AIHONG_MENU_MAX_LINES as _AIHONG_MENU_MAX_LINES,
    AIHONG_MENU_MAX_SIGNIFICANT_CHARS as _AIHONG_MENU_MAX_SIGNIFICANT_CHARS,
    AIHONG_MENU_STAGE as _AIHONG_MENU_STAGE,
    coerce_aihong_menu_choices as _coerce_aihong_menu_choices,
    levenshtein_distance as _levenshtein_distance,
    looks_like_aihong_menu_status_only_text as _looks_like_aihong_menu_status_only_text,
    matches_aihong_target as _matches_aihong_target_info,
    normalize_aihong_choice_box_text as _normalize_aihong_choice_box_text,
)
from plugin.plugins._shared.rapidocr.rapidocr_support import (
    inspect_rapidocr_installation,
    load_rapidocr_runtime,
)
from .reader import normalize_text
from .screen_classifier import (
    ScreenClassification,
    classify_screen_awareness_model,
    classify_screen_from_ocr,
    normalize_screen_type,
)
from .screen_classifier import analyze_screen_visual_features

try:
    from PIL import Image as _PIL_IMAGE_MODULE

    _PIL_RESAMPLING = getattr(_PIL_IMAGE_MODULE, "Resampling", None)
except ImportError:  # pragma: no cover - optional in non-visual test environments.
    _PIL_RESAMPLING = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .ocr_runtime_types import *
from .ocr_backend_interface import *

from .ocr_capture_backends import *

from .ocr_rapidocr_backend import *
from .ocr_input_hooks import *
from .ocr_bridge_writer import *
from . import ocr_reader as _ocr_reader_module

class TextMixin:
    """OCR 文本提取、语言检测、文本去重、台词 emit"""

    @staticmethod
    def _safe_log_arg(value: Any) -> str:
        try:
            return repr(value)
        except Exception:
            try:
                return object.__repr__(value)
            except Exception:
                return f"<unrepresentable {type(value).__name__}>"

    def _call_log_method(self, method_name: str, message: str, *args: Any) -> None:
        logger = getattr(self, "_logger", None)
        method = getattr(logger, method_name, None)
        if not callable(method):
            return
        try:
            method(message, *args)
        except Exception:
            safe_args = tuple(self._safe_log_arg(arg) for arg in args)
            try:
                method(message, *safe_args)
            except Exception:
                return

    def _log_debug(self, message: str, *args: Any) -> None:
        self._call_log_method("debug", message, *args)

    def _log_warning(self, message: str, *args: Any) -> None:
        self._call_log_method("warning", message, *args)

    def _log_info(self, message: str, *args: Any) -> None:
        self._call_log_method("info", message, *args)

    def _emit_screen_classification_event(
        self,
        classification: ScreenClassification,
        *,
        now: float,
    ) -> bool:
        if classification.screen_type in {
            OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        }:
            if self._should_emit_dialogue_screen_transition(classification):
                return self._writer.emit_screen_classified(
                    screen_type=classification.screen_type,
                    confidence=classification.confidence,
                    ui_elements=classification.ui_elements,
                    raw_ocr_text=classification.raw_ocr_text,
                    screen_debug=classification.debug,
                    ts=utc_now_iso(now),
                )
            return False
        return self._writer.emit_screen_classified(
            screen_type=classification.screen_type,
            confidence=classification.confidence,
            ui_elements=classification.ui_elements,
            raw_ocr_text=classification.raw_ocr_text,
            screen_debug=classification.debug,
            ts=utc_now_iso(now),
        )

    def _classification_from_vision_result(
        self,
        result: dict[str, Any],
        *,
        extraction: OcrExtractionResult,
    ) -> ScreenClassification:
        debug = {
            "source": "cnn_primary",
            "reason": "cnn_high_confidence",
            "label": str(result.get("label") or ""),
            "model_name": str(result.get("model_name") or ""),
            "latency_ms": result.get("latency_ms"),
            "all_scores": json_copy(result.get("all_scores") or {}),
        }
        raw_lines = [
            line.strip()
            for line in str(extraction.text or "").splitlines()
            if line.strip()
        ][:20]
        return ScreenClassification(
            screen_type=normalize_screen_type(result.get("screen_type")),
            confidence=round(
                max(0.0, min(float(result.get("confidence") or 0.0), 0.99)),
                4,
            ),
            ui_elements=[],
            raw_ocr_text=raw_lines,
            debug=debug,
        )

    def _classify_screen_with_vision(
        self,
        extraction: OcrExtractionResult,
        *,
        image: Any | None,
    ) -> ScreenClassification | None:
        if not bool(getattr(self._config, "vision_classifier_enabled", False)):
            self._vision_classifier_detail = "disabled"
            return None
        classifier = getattr(self, "vision_classifier", None)
        if classifier is None:
            self._vision_classifier_detail = "unavailable"
            return None
        if image is None:
            self._vision_classifier_detail = "no_image"
            return None
        self._vision_classifier_tick_count = int(
            getattr(self, "_vision_classifier_tick_count", 0) or 0
        ) + 1
        interval = max(
            1,
            int(getattr(self._config, "vision_classifier_tick_interval", 1) or 1),
        )
        if (self._vision_classifier_tick_count - 1) % interval != 0:
            self._vision_classifier_detail = "skipped_interval"
            return None
        try:
            result = classifier.classify(image)
        except Exception as exc:
            self._vision_classifier_detail = "classify_failed"
            self._log_warning("galgame vision classifier failed: {}", exc)
            return None
        if not isinstance(result, dict):
            last_error = str(getattr(classifier, "last_error", "") or "").strip()
            self._vision_classifier_detail = (
                f"no_result:{last_error[:120]}" if last_error else "no_result"
            )
            if last_error:
                self._log_debug("galgame vision classifier returned no result: {}", last_error)
            return None
        try:
            raw_confidence = float(result.get("confidence") or 0.0)
        except (TypeError, ValueError):
            self._vision_classifier_detail = "invalid_confidence"
            return None
        if not math.isfinite(raw_confidence):
            self._vision_classifier_detail = "invalid_confidence"
            return None
        confidence = max(0.0, min(raw_confidence, 1.0))
        self._vision_classifier_last_label = str(result.get("label") or "")
        self._vision_classifier_last_confidence = confidence
        self._vision_classifier_last_latency_ms = max(
            0.0,
            float(result.get("latency_ms") or 0.0),
        )
        threshold = max(
            0.0,
            min(
                float(getattr(self._config, "vision_classifier_threshold", 0.75) or 0.75),
                0.99,
            ),
        )
        if confidence < threshold:
            self._vision_classifier_detail = "low_confidence"
            return None
        classification = self._classification_from_vision_result(result, extraction=extraction)
        if classification.screen_type == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            self._vision_classifier_detail = "unknown"
            return None
        self._vision_classifier_detail = "matched"
        return classification

    def _rapidocr_cache_key(self) -> tuple[str, str, str, str, str]:
        return _rapidocr_runtime_cache_key(
            install_target_dir_raw=self._config.rapidocr_install_target_dir,
            engine_type=self._config.rapidocr_engine_type,
            lang_type=self._config.rapidocr_lang_type,
            model_type=self._config.rapidocr_model_type,
            ocr_version=self._config.rapidocr_ocr_version,
        )


    def _rapidocr_backend_for_config(self) -> RapidOcrBackend:
        key = self._rapidocr_cache_key()
        if self._rapidocr_backend_cache_key == key and self._rapidocr_backend_cache is not None:
            return self._rapidocr_backend_cache
        backend = RapidOcrBackend(
            install_target_dir_raw=self._config.rapidocr_install_target_dir,
            engine_type=self._config.rapidocr_engine_type,
            lang_type=self._config.rapidocr_lang_type,
            model_type=self._config.rapidocr_model_type,
            ocr_version=self._config.rapidocr_ocr_version,
        )
        self._rapidocr_backend_cache_key = key
        self._rapidocr_backend_cache = backend
        return backend


    def _release_rapidocr_backend(self) -> None:
        backend = self._rapidocr_backend_cache
        self._rapidocr_backend_cache = None
        self._rapidocr_backend_cache_key = None
        if backend is None:
            return
        close = getattr(backend, "close", None)
        if callable(close):
            close()


    def _line_changed_repeat_threshold(self) -> int:
        if self._advance_speed == ADVANCE_SPEED_FAST:
            return 1
        if self._advance_speed == ADVANCE_SPEED_SLOW:
            return 3
        return 2


    def _mark_observed_progress(self, *, now: float) -> None:
        self._consecutive_no_text_polls = 0
        self._last_observed_at = utc_now_iso(now)


    def _mark_no_text_poll(self) -> None:
        self._consecutive_no_text_polls += 1


    def _record_accepted_ocr_text(self, raw_text: str) -> None:
        self._last_raw_ocr_text = str(raw_text or "")
        self._ocr_capture_content_trusted = True
        self._ocr_capture_rejected_reason = ""


    def _maybe_auto_switch_rapidocr_lang(
        self,
        text: str,
        *,
        rapidocr_active: bool = False,
    ) -> None:
        if not bool(getattr(self._config, "rapidocr_auto_detect_lang", False)):
            self._log_debug("rapidocr auto-lang skipped: auto_detect_disabled")
            return
        if (
            not rapidocr_active
            or not bool(getattr(self._config, "rapidocr_enabled", False))
            or self._configured_backend_selection() not in {"auto", "rapidocr"}
        ):
            self._log_debug("rapidocr auto-lang skipped: rapidocr_not_active")
            return
        if self._custom_ocr_backend:
            self._log_debug("rapidocr auto-lang skipped: custom_ocr_backend")
            return
        now = time.monotonic()
        last_switched_at = self._ocr_lang_detector.last_switched_at
        if (
            last_switched_at is not None
            and now - last_switched_at < self._ocr_lang_cooldown_seconds
        ):
            remaining = self._ocr_lang_cooldown_seconds - (now - last_switched_at)
            self._log_debug("rapidocr auto-lang skipped: cooldown {:.1f}s remaining", remaining)
            return
        detected_lang = self._ocr_lang_detector.feed(text)
        current_lang = str(getattr(self._config, "rapidocr_lang_type", "") or "").strip()
        if not detected_lang:
            self._log_debug("rapidocr auto-lang skipped: detection_unconfirmed")
            return
        if detected_lang == current_lang:
            self._log_debug("rapidocr auto-lang skipped: already_using {}", detected_lang)
            return
        try:
            inspection = _ocr_reader_module.inspect_rapidocr_installation(
                install_target_dir_raw=self._config.rapidocr_install_target_dir,
                engine_type=self._config.rapidocr_engine_type,
                lang_type=detected_lang,
                model_type=self._config.rapidocr_model_type,
                ocr_version=self._config.rapidocr_ocr_version,
            )
        except Exception as exc:
            self._log_warning("rapidocr auto-lang inspection failed: {}", exc)
            return
        if not bool(inspection.get("installed")):
            self._log_debug("rapidocr auto-lang skipped: model_missing {}", detected_lang)
            return
        if not bool(getattr(self._config, "rapidocr_auto_detect_lang", False)):
            self._log_debug("rapidocr auto-lang skipped: auto_detect_disabled_before_apply")
            return

        self._config.rapidocr_lang_type = detected_lang
        self._config.rapidocr_auto_detect_last_lang = detected_lang
        self._ocr_lang_detector._switched_at = time.monotonic()
        self._backend_plan_cache_key = None
        self._backend_plan_cache_at = 0.0
        self._backend_plan_cache = None
        self._rapidocr_backend_cache_key = None
        self._rapidocr_backend_cache = None
        self._ocr_lang_detector.reset()
        callback = self._rapidocr_lang_changed_callback
        if callable(callback):
            try:
                callback(detected_lang)
            except Exception as exc:
                self._log_warning("rapidocr auto-lang persist callback failed: {}", exc)
        self._log_info("RapidOCR auto-detected language switched to {}", detected_lang)


    def _record_rejected_ocr_text(
        self,
        raw_text: str,
        *,
        reason: str,
        now: float,
        capture_backend_kind: str = "",
    ) -> None:
        self._last_rejected_ocr_text = str(raw_text or "")
        self._last_rejected_ocr_reason = str(reason or "")
        self._last_rejected_ocr_at = utc_now_iso(now)
        self._last_rejected_capture_backend = str(capture_backend_kind or "")
        self._ocr_capture_content_trusted = False
        self._ocr_capture_rejected_reason = str(reason or "")


    def _line_payload_from_writer(self, *, stability: str) -> dict[str, Any]:
        state = getattr(self._writer, "_state", {})
        if not isinstance(state, dict):
            return {}
        text = str(state.get("text") or "")
        if not text:
            return {}
        return {
            "line_id": str(state.get("line_id") or ""),
            "speaker": str(state.get("speaker") or ""),
            "text": text,
            "scene_id": str(state.get("scene_id") or ""),
            "route_id": str(state.get("route_id") or ""),
            "stability": stability,
            "ts": str(state.get("ts") or ""),
        }


    def _ocr_context_state_for_detail(self, *, status: str, detail: str) -> str:
        detail = str(detail or "")
        if not self._runtime.enabled and not self._config.ocr_reader_enabled:
            return "disabled"
        if detail == "starting_capture":
            return "capture_pending"
        if detail == "capture_failed":
            return "capture_failed"
        if self._stale_capture_backend:
            return "stale_capture_backend"
        if detail == "ocr_capture_diagnostic_required" or self._ocr_capture_diagnostic_required():
            return "diagnostic_required"
        if detail in {"attached_no_text_yet", "self_ui_guard_blocked"}:
            return "no_text"
        state = getattr(self._writer, "_state", {})
        stability = str(state.get("stability") or "") if isinstance(state, dict) else ""
        if stability == "choices":
            return "choices"
        if detail == "receiving_text" or stability == "stable":
            return "stable"
        if detail == "receiving_observed_text" or stability == "tentative":
            return "observed"
        if detail in {"backend_unavailable", "capture_backend_unavailable"}:
            return "capture_failed"
        if str(status or "") == "starting":
            return "capture_pending"
        return detail or str(status or "")


    @staticmethod
    def _stabilize_text_key(
        text: str,
        *,
        state: _StableOcrTextState,
        repeat_threshold: int = 2,
    ) -> bool:
        cleaned = normalize_text(text).strip()
        text_key = _ocr_stability_key(cleaned)
        if not cleaned:
            state.last_block_reason = "empty_text"
            return False
        if not text_key:
            state.last_block_reason = "empty_stability_key"
            return False
        last_key = state.last_text_key or _ocr_stability_key(state.last_raw_text)
        if _ocr_stability_keys_match(text_key, last_key):
            state.repeat_count += 1
            state.last_raw_text = _prefer_ocr_stability_text(state.last_raw_text, cleaned)
            state.last_text_key = text_key if len(text_key) >= len(last_key) else last_key
        else:
            state.repeat_count = 1
            state.last_raw_text = cleaned
            state.last_text_key = text_key
        if state.repeat_count < max(1, int(repeat_threshold)):
            state.last_block_reason = "waiting_for_repeat"
            return False
        stable_key = state.stable_text_key or _ocr_stability_key(state.stable_text)
        if _ocr_stability_keys_match(state.last_text_key, stable_key):
            state.repeat_count = 0
            state.last_block_reason = "duplicate_stable_text"
            return False
        state.stable_text = state.last_raw_text
        state.stable_text_key = state.last_text_key
        state.last_block_reason = ""
        return True


    def _ocr_window_title_for_noise_filter(self) -> str:
        return str(
            (self._attached_window.title if self._attached_window is not None else "")
            or self._runtime.effective_window_title
            or self._runtime.window_title
            or ""
        )


    def _clean_ocr_dialogue_for_emit(self, raw_text: str) -> tuple[str, str]:
        content_text = _drop_ocr_chrome_noise_lines(
            raw_text,
            window_title=self._ocr_window_title_for_noise_filter(),
        )
        cleaned_text = _clean_ocr_dialogue_text(content_text)
        cleaned_text = _fix_ocr_punctuation_confusion(cleaned_text)
        return content_text, cleaned_text


    def _dialogue_library_match_for_cleaned_text(
        self, cleaned_text: str
    ) -> _DialogueLibraryMatch | None:
        target = self._attached_window
        process_name = (
            str(target.process_name or "")
            if target is not None
            else str(self._runtime.effective_process_name or self._runtime.process_name or "")
        )
        normalized_title = (
            str(target.normalized_title or "")
            if target is not None
            else _normalize_window_title(
                str(self._runtime.effective_window_title or self._runtime.window_title or "")
            )
        )
        return _match_dialogue_library_for_target(
            cleaned_text,
            process_name=process_name,
            normalized_title=normalized_title,
        )


    def _emit_line_from_ocr_text(
        self,
        raw_text: str,
        *,
        now: float,
        state: _StableOcrTextState | None = None,
        emit_observed: bool = True,
        repeat_threshold: int | None = None,
        ocr_confidence: float | None = None,
        text_source: str = "bottom_region",
        rapidocr_active: bool = False,
    ) -> bool:
        content_text, cleaned_text = self._clean_ocr_dialogue_for_emit(raw_text)
        if (
            _looks_like_noise_normalized_text(cleaned_text)
            or _looks_like_game_overlay_normalized_text(cleaned_text)
            or not _looks_like_ocr_dialogue_normalized_text(cleaned_text)
        ):
            return False
        self._record_accepted_ocr_text(content_text)
        self._maybe_auto_switch_rapidocr_lang(
            cleaned_text,
            rapidocr_active=rapidocr_active,
        )
        dialogue_library_match = self._dialogue_library_match_for_cleaned_text(cleaned_text)
        if dialogue_library_match is not None:
            cleaned_text = dialogue_library_match.canonical_text()
            text_source = "dialogue_library"
        speaker, text = OcrReaderBridgeWriter._split_speaker_text(cleaned_text)
        had_pending_visual_scene = bool(self._pending_visual_scene_hash)
        if self._pending_visual_scene_hash:
            self._resolve_pending_visual_scene_for_dialogue(
                cleaned_text=cleaned_text,
                speaker=speaker,
                text=text,
                now=now,
                commit_diagnostic=(
                    "pending_scene_committed_before_observed"
                    if emit_observed
                    else "pending_scene_committed_before_stable"
                ),
            )
        if self._pending_background_candidate_hash and not had_pending_visual_scene:
            self._resolve_pending_background_candidate_before_dialogue(
                cleaned_text=cleaned_text,
                speaker=speaker,
                text=text,
                now=now,
            )
        if emit_observed and self._writer.emit_line_observed(
            cleaned_text,
            ts=utc_now_iso(now),
            ocr_confidence=ocr_confidence,
            text_source=text_source,
        ):
            observed = self._line_payload_from_writer(stability="tentative")
            self._last_observed_line = observed
        tracker = state or self._default_ocr_state
        effective_repeat_threshold = (
            self._line_changed_repeat_threshold()
            if repeat_threshold is None
            else repeat_threshold
        )
        if dialogue_library_match is not None:
            effective_repeat_threshold = 1
        if tracker.stable_text and int(effective_repeat_threshold or 1) > 1:
            cleaned_key = _ocr_stability_key(cleaned_text)
            stable_key = tracker.stable_text_key or _ocr_stability_key(tracker.stable_text)
            if (
                cleaned_key
                and stable_key
                and not _ocr_stability_keys_match(cleaned_key, stable_key)
            ):
                effective_repeat_threshold = 1
        if not self._stabilize_text_key(
            cleaned_text,
            state=tracker,
            repeat_threshold=effective_repeat_threshold,
        ):
            return False
        emitted_text = tracker.stable_text or cleaned_text
        emitted = self._writer.emit_line(
            emitted_text,
            ts=utc_now_iso(now),
            ocr_confidence=ocr_confidence,
            text_source=text_source,
        )
        if emitted:
            stable_line = self._line_payload_from_writer(stability="stable")
            self._last_stable_line = stable_line
            self._last_observed_line = stable_line
        return emitted


    def _emit_choices_from_candidates(
        self,
        choices: list[str],
        *,
        now: float,
        state: _StableOcrTextState | None = None,
        repeat_threshold: int = 2,
        choice_bounds: list[dict[str, float] | None] | None = None,
        choice_bounds_metadata: dict[str, Any] | None = None,
    ) -> bool:
        tracker = state or self._default_ocr_state
        if not self._stabilize_text_key(
            _canonical_choice_candidate_text(choices),
            state=tracker,
            repeat_threshold=max(1, int(repeat_threshold or 1)),
        ):
            return False
        self._commit_pending_visual_scene(now=now)
        return self._writer.emit_choices(
            choices,
            ts=utc_now_iso(now),
            choice_bounds=choice_bounds,
            choice_bounds_metadata=choice_bounds_metadata,
        )


    def _should_attempt_followup_confirm(
        self,
        raw_text: str,
        *,
        state: _StableOcrTextState,
    ) -> bool:
        _, cleaned_text = self._clean_ocr_dialogue_for_emit(raw_text)
        cleaned = normalize_text(cleaned_text).strip()
        if not cleaned:
            return False
        cleaned_key = _ocr_stability_key(cleaned)
        last_key = state.last_text_key or _ocr_stability_key(state.last_raw_text)
        stable_key = state.stable_text_key or _ocr_stability_key(state.stable_text)
        return (
            bool(state.stable_text)
            and
            state.repeat_count >= 1
            and _ocr_stability_keys_match(cleaned_key, last_key)
            and not _ocr_stability_keys_match(cleaned_key, stable_key)
        )


    def _consume_aihong_menu_stage_text(
        self,
        raw_text: str,
        *,
        now: float,
        boxes: list[OcrTextBox] | None = None,
        choice_bounds_metadata: dict[str, Any] | None = None,
        choice_repeat_threshold: int = 2,
    ) -> _MenuConsumeResult:
        choice_boxes = list(boxes or [])
        if choice_boxes:
            source_height = _aihong_choices_region_source_height(
                choice_boxes,
                choice_bounds_metadata,
            )
            choice_boxes = _filter_boxes_to_region(
                choice_boxes,
                source_height=source_height,
                top_ratio=_AIHONG_CHOICES_REGION_PRESET["top_ratio"],
                bottom_inset_ratio=_AIHONG_CHOICES_REGION_PRESET[
                    "bottom_inset_ratio"
                ],
            )
            lines = _stripped_ocr_lines(
                "\n".join(str(getattr(box, "text", "") or "") for box in choice_boxes)
            )
        else:
            lines = _stripped_ocr_lines(raw_text)
        choices = _coerce_aihong_menu_choices(lines)
        if choices:
            return _MenuConsumeResult(
                emitted_kind="choices"
                if self._emit_choices_from_candidates(
                    choices,
                    now=now,
                    state=self._aihong_menu_ocr_state,
                    repeat_threshold=choice_repeat_threshold,
                    choice_bounds=_aihong_choice_boxes(choices, choice_boxes),
                    choice_bounds_metadata=choice_bounds_metadata,
                )
                else "",
                has_menu_candidate=True,
            )
        if _looks_like_aihong_menu_status_only_text(raw_text):
            return _MenuConsumeResult(emitted_kind="", has_menu_candidate=True)
        # Menu-stage capture intentionally scans a much larger region so option
        # OCR can find buttons anywhere on screen. Do not turn that full-screen
        # text into a dialogue line; switch back to dialogue-stage capture and
        # let the narrower profile read the next line.
        return _MenuConsumeResult(emitted_kind="", has_menu_candidate=False)


    def _rapidocr_descriptor(self, inspection: dict[str, Any], *, enabled: bool) -> OcrBackendDescriptor:
        detail = str(inspection.get("detail") or "missing")
        if not enabled:
            detail = "disabled_by_config"
        available = enabled and bool(inspection.get("installed"))
        return OcrBackendDescriptor(
            kind="rapidocr",
            backend=self._rapidocr_backend_for_config(),
            path=str(inspection.get("detected_path") or ""),
            model=str(
                inspection.get("selected_model")
                or f"{self._config.rapidocr_ocr_version}/{self._config.rapidocr_lang_type}/{self._config.rapidocr_model_type}"
            ),
            detail="selected_primary" if available else detail,
            available=available,
        )


    def _extract_text_from_image(
        self,
        image: Any,
        *,
        plan: SelectedOcrBackendPlan | None = None,
    ) -> OcrExtractionResult:
        if plan is not None:
            resolved_plan = plan
        elif self._custom_ocr_backend:
            resolved_plan = self._custom_ocr_backend_plan()
        else:
            resolved_plan = self._resolve_backend_plan()
        if self._custom_ocr_backend:
            return OcrExtractionResult(
                text=self._ocr_backend.extract_text(image),
                backend=resolved_plan.primary,
                backend_detail=resolved_plan.primary.detail or "custom_backend",
                text_source="bottom_region",
            )
        descriptors = [resolved_plan.primary]
        if resolved_plan.fallback.available:
            descriptors.append(resolved_plan.fallback)
        warnings: list[str] = []
        backend_errors: list[str] = []
        last_error: Exception | None = None
        for index, descriptor in enumerate(descriptors):
            if descriptor.backend is None:
                continue
            try:
                extract_with_boxes = getattr(descriptor.backend, "extract_text_with_boxes", None)
                if callable(extract_with_boxes):
                    try:
                        text, boxes = extract_with_boxes(image)
                        if not str(text or "").strip():
                            if not isinstance(descriptor.backend, RapidOcrBackend):
                                extract_text = getattr(descriptor.backend, "extract_text", None)
                                if callable(extract_text):
                                    fallback_text = extract_text(image)
                                    if str(fallback_text or "").strip():
                                        text = fallback_text
                                        boxes = []
                            elif index == 0:
                                warnings.append(
                                    f"ocr_reader {descriptor.kind} returned empty text "
                                    "(confidence filtering may have discarded all tokens)"
                                )
                                continue
                    except Exception as boxes_exc:
                        extract_text = getattr(descriptor.backend, "extract_text", None)
                        if not callable(extract_text):
                            raise
                        warnings.append(
                            f"ocr_reader {descriptor.kind} boxes unavailable: {boxes_exc}"
                        )
                        text = extract_text(image)
                        boxes = []
                else:
                    text = descriptor.backend.extract_text(image)
                    boxes = []
                return OcrExtractionResult(
                    text=text,
                    backend=descriptor,
                    backend_detail=(
                        "fallback_after_runtime_error"
                        if index > 0
                        else (descriptor.detail or "selected_primary")
                    ),
                    warnings=warnings,
                    backend_errors=backend_errors,
                    boxes=list(boxes),
                    ocr_confidence=_average_ocr_box_confidence(boxes),
                    text_source="bottom_region",
                )
            except Exception as exc:
                last_error = exc
                warning = f"ocr_reader {descriptor.kind} failed: {type(exc).__name__}: {exc}"
                warnings.append(warning)
                backend_errors.append(warning)
                self._log_warning("ocr_reader backend {} failed: {}", descriptor.kind, exc)
        if last_error is not None:
            detail = "; ".join(backend_errors) if backend_errors else str(last_error)
            raise RuntimeError(f"ocr_reader all configured backends failed: {detail}") from last_error
        return OcrExtractionResult(
            backend=resolved_plan.primary,
            warnings=warnings,
            backend_errors=backend_errors,
        )


    def _emit_screen_classification_from_extraction(
        self,
        extraction: OcrExtractionResult,
        *,
        target: DetectedGameWindow,
        now: float,
        image: Any | None = None,
    ) -> tuple[ScreenClassification, bool]:
        vision_image = (
            image
            if image is not None
            else getattr(extraction, "captured_image", None)
        )
        vision_classification = self._classify_screen_with_vision(
            extraction,
            image=vision_image,
        )
        if vision_classification is not None:
            vision_classification = self._apply_screen_classification_stability(
                vision_classification
            )
            self._screen_awareness_model_detail = "skipped_cnn_primary"
            self._update_capture_profile_recommendation(
                extraction,
                classification=vision_classification,
                target=target,
                now=now,
            )
            self._collect_screen_awareness_sample(
                extraction,
                classification=vision_classification,
                target=target,
                now=now,
            )
            emitted = self._emit_screen_classification_event(
                vision_classification,
                now=now,
            )
            return vision_classification, emitted

        classification = classify_screen_from_ocr(
            extraction.text,
            boxes=extraction.boxes,
            bounds_metadata=_extraction_choice_bounds_metadata(extraction),
            ocr_regions=extraction.screen_ocr_regions,
            visual_features=extraction.screen_visual_features,
            screen_templates=self._screen_templates_for_target(target),
            template_context=self._screen_template_context(target),
        )
        classification = self._apply_screen_awareness_model(
            extraction,
            classification=classification,
            target=target,
        )
        classification = self._apply_screen_classification_stability(classification)
        self._update_capture_profile_recommendation(
            extraction,
            classification=classification,
            target=target,
            now=now,
        )
        self._collect_screen_awareness_sample(
            extraction,
            classification=classification,
            target=target,
            now=now,
        )
        emitted = self._emit_screen_classification_event(
            classification,
            now=now,
        )
        return classification, emitted


    def _consume_ocr_text(
        self,
        raw_text: str,
        *,
        now: float,
        state: _StableOcrTextState | None = None,
        allow_choices: bool = True,
        allow_plain_text_choices: bool = False,
        emit_observed: bool = True,
        line_repeat_threshold: int | None = None,
        ocr_confidence: float | None = None,
        text_source: str = "bottom_region",
        rapidocr_active: bool = False,
    ) -> bool:
        tracker = state or self._default_ocr_state
        lines = _stripped_ocr_lines(raw_text)
        if allow_choices:
            choices = _coerce_choice_lines(lines, allow_plain_text=allow_plain_text_choices)
            if choices:
                return self._emit_choices_from_candidates(
                    choices,
                    now=now,
                    state=tracker,
                    repeat_threshold=(
                        line_repeat_threshold
                        if line_repeat_threshold is not None
                        else 2
                    ),
                )
        return self._emit_line_from_ocr_text(
            raw_text,
            now=now,
            state=tracker,
            emit_observed=emit_observed,
            repeat_threshold=line_repeat_threshold,
            ocr_confidence=ocr_confidence,
            text_source=text_source,
            rapidocr_active=rapidocr_active,
        )
