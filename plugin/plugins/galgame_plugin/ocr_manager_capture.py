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
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from ctypes import wintypes
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
    matches_aihong_target as _matches_aihong_target,
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

class CaptureMixin:
    """抓帧调度、backend 选择、capture profile 管理"""

    def reset_capture_runtime_diagnostics(self) -> None:
        self._consecutive_no_text_polls = 0
        self._last_capture_error = ""
        self._last_capture_image_hash = ""
        self._last_capture_timing = {}
        self._consecutive_same_capture_frames = 0
        self._stale_capture_backend = False
        self._last_rejected_ocr_text = ""
        self._last_rejected_ocr_reason = ""
        self._last_rejected_ocr_at = ""
        self._last_rejected_capture_backend = ""
        self._ocr_capture_content_trusted = True
        self._ocr_capture_rejected_reason = ""
        self._runtime.last_capture_error = ""
        self._runtime.last_capture_image_hash = ""
        self._runtime.consecutive_same_capture_frames = 0
        self._runtime.stale_capture_backend = False
        self._runtime.consecutive_no_text_polls = 0
        self._runtime.ocr_capture_diagnostic_required = False
        self._runtime.last_rejected_ocr_text = ""
        self._runtime.last_rejected_ocr_reason = ""
        self._runtime.last_rejected_ocr_at = ""
        self._runtime.last_rejected_capture_backend = ""
        self._runtime.ocr_capture_content_trusted = True
        self._runtime.ocr_capture_rejected_reason = ""
        self._background_capture_pause_until = 0.0
        self._background_capture_pause_reason = ""
        self._reset_known_screen_stuck_tracking()
        if self._runtime.ocr_context_state in {
            "capture_failed",
            "diagnostic_required",
            "stale_capture_backend",
        }:
            self._runtime.ocr_context_state = ""


    def _should_emit_observed_lines_for_capture(self, *, after_advance_trigger_mode: bool) -> bool:
        return True


    def _ocr_capture_diagnostic_required(self) -> bool:
        return self._consecutive_no_text_polls >= 3


    def _record_capture_attempt(self, *, now: float) -> None:
        self._last_capture_attempt_at = utc_now_iso(now)
        self._last_capture_error = ""


    def _record_capture_completed(self, *, now: float, raw_text: str = "", image_hash: str = "") -> None:
        del raw_text
        self._last_capture_completed_at = utc_now_iso(now)
        self._last_capture_error = ""
        if image_hash:
            if image_hash == self._last_capture_image_hash:
                self._consecutive_same_capture_frames += 1
            else:
                self._last_capture_image_hash = image_hash
                self._consecutive_same_capture_frames = 1
            self._stale_capture_backend = (
                self._consecutive_same_capture_frames >= _STALE_CAPTURE_FRAME_THRESHOLD
            )


    def _background_capture_pause_error(
        self,
        target: DetectedGameWindow,
        *,
        now: float,
    ) -> RuntimeError | None:
        if bool(getattr(target, "is_foreground", False)):
            self._background_capture_pause_until = 0.0
            self._background_capture_pause_reason = ""
            return None
        if self._background_capture_pause_until <= 0.0:
            return None
        if now >= self._background_capture_pause_until:
            self._background_capture_pause_until = 0.0
            self._background_capture_pause_reason = ""
            return None
        reason = self._background_capture_pause_reason or "recent_invalid_background_frame"
        remaining = max(0.0, self._background_capture_pause_until - now)
        return RuntimeError(
            f"backend_not_suitable_for_background: {reason}; paused {remaining:.1f}s"
        )


    def _pause_background_capture_backend(
        self,
        *,
        reason: str,
        now: float,
    ) -> None:
        self._background_capture_pause_until = max(
            self._background_capture_pause_until,
            now + _BACKGROUND_CAPTURE_BACKEND_PAUSE_SECONDS,
        )
        self._background_capture_pause_reason = str(reason or "invalid_background_frame")


    def _record_capture_geometry(self, extraction: OcrExtractionResult) -> None:
        self._last_capture_source_size = dict(extraction.source_size or {})
        self._last_capture_rect = dict(extraction.capture_rect or {})
        self._last_capture_window_rect = dict(extraction.window_rect or {})


    def _record_capture_error(self, *, now: float, error: Exception) -> None:
        if not self._last_capture_attempt_at:
            self._last_capture_attempt_at = utc_now_iso(now)
        self._last_capture_error = str(error)


    @staticmethod
    def _capture_image_hash(frame: Any) -> str:
        if frame is None:
            return ""
        try:
            if hasattr(frame, "resize") and hasattr(frame, "tobytes"):
                source = frame.convert("RGB") if hasattr(frame, "convert") else frame
                small = source.resize((64, 64))
                return hashlib.sha1(small.tobytes()).hexdigest()[:16]
        except (AttributeError, OSError, ValueError):
            return ""
        if isinstance(frame, str):
            return hashlib.sha1(frame.encode("utf-8", "ignore")).hexdigest()[:16]
        if isinstance(frame, bytes | bytearray):
            return hashlib.sha1(bytes(frame)).hexdigest()[:16]
        return ""


    @staticmethod
    def _capture_quality_detail(frame: Any) -> str:
        if frame is None or not hasattr(frame, "convert"):
            return ""
        try:
            from PIL import Image, ImageStat

            resampling = getattr(Image, "Resampling", Image)
            image = frame.convert("L").resize((32, 32), resampling.BILINEAR)
            extrema = image.getextrema()
            if not isinstance(extrema, tuple) or len(extrema) != 2:
                return ""
            if int(extrema[1]) - int(extrema[0]) <= 2:
                return "blank_frame"
            stat = ImageStat.Stat(image)
            stddev = float((stat.stddev or [0.0])[0] or 0.0)
            if stddev < 3.0:
                return "low_information_frame"
        except (AttributeError, ImportError, OSError, ValueError, TypeError):
            return ""
        return ""


    @staticmethod
    def _background_capture_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=_BACKGROUND_HASH_BOTTOM_INSET_RATIO,
        )


    def update_capture_profiles(self, profiles: dict[str, dict[str, Any]]) -> None:
        self._capture_profiles = _parse_configured_capture_profiles(profiles, self._logger)


    @staticmethod
    def _scan_ratio_values(
        current_value: float,
        *,
        delta_start: float,
        delta_end: float,
        step: float,
    ) -> list[float]:
        values: list[float] = []
        seen: set[int] = set()
        basis = 100
        start = int(round((current_value + delta_start) * basis))
        end = int(round((current_value + delta_end) * basis))
        step_value = max(1, int(round(step * basis)))
        for raw in range(start, end + 1, step_value):
            normalized = max(0.0, min(raw / basis, 0.98))
            key = int(round(normalized * basis))
            if key in seen:
                continue
            seen.add(key)
            values.append(round(normalized, 2))
        return values


    @staticmethod
    def _crop_box_for_profile_size(
        *,
        width: int,
        height: int,
        profile: OcrCaptureProfile,
    ) -> tuple[int, int, int, int]:
        left = int(width * profile.left_inset_ratio)
        right = int(width * (1.0 - profile.right_inset_ratio))
        top = int(height * profile.top_ratio)
        bottom = int(height * (1.0 - profile.bottom_inset_ratio))
        left = max(0, min(left, width))
        right = max(left, min(right, width))
        top = max(0, min(top, height))
        bottom = max(top, min(bottom, height))
        return (left, top, right, bottom)


    def auto_recalibrate_dialogue_profile(self) -> dict[str, Any]:
        if not self._config.ocr_reader_enabled:
            raise ValueError("ocr_reader 未启用，无法自动重校准对白区")
        if not self._platform_fn():
            raise ValueError("当前平台不是 Windows，无法自动重校准对白区")
        if not self._capture_backend.is_available():
            raise ValueError("当前截图后端不可用，无法自动重校准对白区")
        attached_target = self._attached_window
        if attached_target is None:
            raise ValueError("当前没有已附着的 OCR 目标窗口，无法自动重校准对白区")
        target = replace(attached_target)
        foreground_hwnd = 0
        try:
            foreground_hwnd = int(_ocr_reader_module._foreground_window_handle())
        except Exception:
            foreground_hwnd = 0
        foreground_matches_target = False
        if foreground_hwnd:
            try:
                foreground_matches_target = bool(
                    _ocr_reader_module._foreground_matches_target(
                        foreground_hwnd,
                        target,
                    )[0]
                )
            except Exception:
                foreground_matches_target = foreground_hwnd == int(target.hwnd or 0)
        if not foreground_matches_target:
            raise ValueError("请先将目标窗口切到前台后再自动重校准对白区")
        process_name = str(target.process_name or "").strip()
        if not process_name:
            raise ValueError("当前 OCR 目标缺少进程名，无法自动重校准对白区")

        full_window_profile = OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
        )
        full_image = self._capture_backend.capture_frame(target, full_window_profile)
        # Verify the screen is static by capturing a second frame and comparing hashes
        time.sleep(0.15)
        verify_image = self._capture_backend.capture_frame(target, full_window_profile)
        hash_a = hashlib.blake2b(
            full_image.resize((64, 64)).tobytes() if hasattr(full_image, "resize") else b"", digest_size=16
        ).hexdigest() if full_image is not None else ""
        hash_b = hashlib.blake2b(
            verify_image.resize((64, 64)).tobytes() if hasattr(verify_image, "resize") else b"", digest_size=16
        ).hexdigest() if verify_image is not None else ""
        if hash_a != hash_b:
            raise ValueError("画面未静止，自动重校准中止（请在稳定画面重试）")
        image_size = getattr(full_image, "size", None)
        if (
            not isinstance(image_size, tuple)
            or len(image_size) < 2
            or int(image_size[0]) <= 0
            or int(image_size[1]) <= 0
            or not hasattr(full_image, "crop")
        ):
            raise ValueError("当前截图后端不支持自动重校准所需的整窗截图")

        image_width = int(image_size[0])
        image_height = int(image_size[1])
        if target.width <= 0 or target.height <= 0:
            target = replace(
                target,
                width=target.width if target.width > 0 else image_width,
                height=target.height if target.height > 0 else image_height,
            )

        base_selection = self._capture_profile_selection_for_target(
            target,
            stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        )
        base_profile = base_selection.profile
        is_aihong_target = _matches_aihong_target(target)

        def _append_ratio_values(values: list[float], additions: Iterable[float]) -> list[float]:
            merged = list(values)
            seen = {int(round(value * 100)) for value in merged}
            for raw in additions:
                normalized = round(max(0.0, min(float(raw), 0.98)), 2)
                key = int(round(normalized * 100))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
            return sorted(merged)

        horizontal_pairs: list[tuple[float, float]] = []

        def _add_horizontal_pair(left_ratio: float, right_ratio: float) -> None:
            left_ratio = round(max(0.0, min(float(left_ratio), 0.45)), 2)
            right_ratio = round(max(0.0, min(float(right_ratio), 0.45)), 2)
            if left_ratio + right_ratio >= 0.95:
                return
            pair = (left_ratio, right_ratio)
            if pair not in horizontal_pairs:
                horizontal_pairs.append(pair)

        if is_aihong_target:
            _add_horizontal_pair(0.0, 0.0)
            _add_horizontal_pair(0.02, 0.02)
            _add_horizontal_pair(0.05, 0.05)
        _add_horizontal_pair(base_profile.left_inset_ratio, base_profile.right_inset_ratio)
        if not is_aihong_target and (
            base_profile.left_inset_ratio > 0.0 or base_profile.right_inset_ratio > 0.0
        ):
            _add_horizontal_pair(
                max(0.0, base_profile.left_inset_ratio - 0.05),
                max(0.0, base_profile.right_inset_ratio - 0.05),
            )

        top_values = self._scan_ratio_values(
            base_profile.top_ratio,
            delta_start=-0.14,
            delta_end=0.08,
            step=0.02,
        )
        bottom_values = self._scan_ratio_values(
            base_profile.bottom_inset_ratio,
            delta_start=-0.04,
            delta_end=0.08,
            step=0.02,
        )
        if is_aihong_target:
            aihong_preset = OcrCaptureProfile.from_dict(_AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET)
            top_values = _append_ratio_values(
                top_values,
                self._scan_ratio_values(
                    aihong_preset.top_ratio,
                    delta_start=-0.08,
                    delta_end=0.08,
                    step=0.02,
                ),
            )
            bottom_values = _append_ratio_values(
                bottom_values,
                self._scan_ratio_values(
                    aihong_preset.bottom_inset_ratio,
                    delta_start=-0.05,
                    delta_end=0.08,
                    step=0.01,
                ),
            )
        backend_plan = None if self._custom_ocr_backend else self._resolve_backend_plan()
        if backend_plan is not None and not backend_plan.primary.available:
            raise ValueError("当前 OCR backend 不可用，无法自动重校准对白区")

        min_top_ratio = 0.04
        try:
            client_rect = _ocr_reader_module._target_client_rect(target)
            client_height = int(client_rect[3] - client_rect[1])
            if client_height > 0 and image_height > client_height:
                title_bar_height = image_height - client_height
                min_top_ratio = max(
                    min_top_ratio,
                    round(title_bar_height / image_height + 0.02, 2),
                )
        except Exception as exc:
            log_debug = getattr(self, "_log_debug", None)
            if callable(log_debug):
                log_debug("ocr_reader auto recalibrate client rect unavailable: {}", exc)
        top_values = [value for value in top_values if value >= min_top_ratio]
        if not top_values:
            top_values = [min_top_ratio]

        best_candidate: dict[str, Any] | None = None
        current_distance_basis = (
            round(base_profile.top_ratio, 2),
            round(base_profile.bottom_inset_ratio, 2),
        )
        min_height = max(24, int(image_height * 0.08))
        max_height = max(min_height, int(image_height * 0.45))
        visited_pairs: set[tuple[float, float, float, float]] = set()

        def _consider_candidate(
            top_ratio: float,
            bottom_inset_ratio: float,
            left_inset_ratio: float,
            right_inset_ratio: float,
        ) -> None:
            nonlocal best_candidate
            key = (
                round(top_ratio, 2),
                round(bottom_inset_ratio, 2),
                round(left_inset_ratio, 2),
                round(right_inset_ratio, 2),
            )
            if key in visited_pairs:
                return
            visited_pairs.add(key)
            if top_ratio + bottom_inset_ratio >= 1.0 or left_inset_ratio + right_inset_ratio >= 1.0:
                return
            candidate_profile = OcrCaptureProfile(
                left_inset_ratio=left_inset_ratio,
                right_inset_ratio=right_inset_ratio,
                top_ratio=top_ratio,
                bottom_inset_ratio=bottom_inset_ratio,
            )
            left_px, top_px, right_px, bottom_px = self._crop_box_for_profile_size(
                width=image_width,
                height=image_height,
                profile=candidate_profile,
            )
            crop_height = bottom_px - top_px
            if crop_height < min_height or crop_height > max_height:
                return
            if right_px - left_px < 10:
                return
            extracted = self._extract_text_from_image(
                full_image.crop((left_px, top_px, right_px, bottom_px)),
                plan=backend_plan,
            )
            sample_text = str(extracted.text or "").strip()
            if not sample_text or _looks_like_self_ui_text(sample_text):
                return
            score, cjk_count, significant_chars = _score_ocr_text(sample_text)
            if significant_chars < 8 or cjk_count <= 0:
                return
            distance = abs(round(top_ratio, 2) - current_distance_basis[0]) + abs(
                round(bottom_inset_ratio, 2) - current_distance_basis[1]
            )
            width_ratio = max(0.0, 1.0 - left_inset_ratio - right_inset_ratio)
            candidate = {
                "profile": candidate_profile,
                "sample_text": sample_text,
                "score": score,
                "cjk_count": cjk_count,
                "significant_chars": significant_chars,
                "distance": distance,
                "width_ratio": width_ratio,
            }
            if best_candidate is None:
                best_candidate = candidate
                return
            if (
                (candidate["score"], candidate["cjk_count"], candidate["significant_chars"])
                > (
                    best_candidate["score"],
                    best_candidate["cjk_count"],
                    best_candidate["significant_chars"],
                )
                or (
                    (
                        candidate["score"],
                        candidate["cjk_count"],
                        candidate["significant_chars"],
                    )
                    == (
                        best_candidate["score"],
                        best_candidate["cjk_count"],
                        best_candidate["significant_chars"],
                    )
                    and (
                        candidate["width_ratio"] > best_candidate["width_ratio"]
                        or (
                            candidate["width_ratio"] == best_candidate["width_ratio"]
                            and candidate["distance"] < best_candidate["distance"]
                        )
                    )
                )
            ):
                best_candidate = candidate

        preferred_bottom_values: list[float] = []
        for delta in (0.0, 0.02, -0.02, 0.04):
            candidate_value = round(base_profile.bottom_inset_ratio + delta, 2)
            if candidate_value in bottom_values and candidate_value not in preferred_bottom_values:
                preferred_bottom_values.append(candidate_value)
        if not preferred_bottom_values:
            preferred_bottom_values = list(bottom_values)

        for top_ratio in top_values:
            for bottom_inset_ratio in preferred_bottom_values:
                for left_inset_ratio, right_inset_ratio in horizontal_pairs:
                    _consider_candidate(
                        top_ratio,
                        bottom_inset_ratio,
                        left_inset_ratio,
                        right_inset_ratio,
                    )

        if best_candidate is not None:
            refine_top_values: list[float] = []
            best_top_ratio = round(float(best_candidate["profile"].top_ratio), 2)
            for delta in (-0.02, 0.0, 0.02):
                candidate_value = round(best_top_ratio + delta, 2)
                if candidate_value in top_values and candidate_value not in refine_top_values:
                    refine_top_values.append(candidate_value)
            for top_ratio in refine_top_values:
                for bottom_inset_ratio in bottom_values:
                    for left_inset_ratio, right_inset_ratio in horizontal_pairs:
                        _consider_candidate(
                            top_ratio,
                            bottom_inset_ratio,
                            left_inset_ratio,
                            right_inset_ratio,
                        )
        else:
            for top_ratio in top_values:
                for bottom_inset_ratio in bottom_values:
                    for left_inset_ratio, right_inset_ratio in horizontal_pairs:
                        _consider_candidate(
                            top_ratio,
                            bottom_inset_ratio,
                            left_inset_ratio,
                            right_inset_ratio,
                        )

        if best_candidate is None:
            raise ValueError("自动重校准失败：请先停在稳定对白界面再重试")

        window_width = max(0, int(target.width or image_width))
        window_height = max(0, int(target.height or image_height))
        bucket_key = (
            build_ocr_capture_profile_bucket_key(window_width, window_height).lower()
            if window_width > 0 and window_height > 0
            else ""
        )
        capture_profile = best_candidate["profile"].to_dict()
        sample_text = str(best_candidate["sample_text"] or "")
        return {
            "process_name": process_name,
            "stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "save_scope": "window_bucket",
            "bucket_key": bucket_key,
            "window_width": window_width,
            "window_height": window_height,
            "capture_profile": capture_profile,
            "sample_text": sample_text,
            "summary": (
                f"已自动重校准对白区：{process_name}"
                + (f" / {bucket_key}" if bucket_key else "")
                + f" / 示例文本：{sample_text[:24]}"
            ),
        }


    def _has_manual_capture_profile(self, target: DetectedGameWindow) -> bool:
        return _uses_manual_capture_profile(self._capture_profiles, target)


    def _should_use_aihong_two_stage(self, target: DetectedGameWindow) -> bool:
        return _matches_aihong_target(target)


    def _drain_completed_abandoned_capture_workers_locked(self) -> list[ThreadPoolExecutor]:
        executors: list[ThreadPoolExecutor] = []
        active: list[tuple[ThreadPoolExecutor, Future[OcrExtractionResult]]] = []
        for executor, future in self._abandoned_capture_workers:
            if future.done():
                executors.append(executor)
                try:
                    future.result()
                except Exception as exc:
                    self._logger.debug("ocr_reader abandoned timed-out capture eventually failed: {}", exc)
            else:
                active.append((executor, future))
        self._abandoned_capture_workers = active
        return executors


    def _shutdown_capture_worker(self) -> None:
        executors: list[ThreadPoolExecutor] = []
        with self._capture_worker_lock:
            future = self._capture_future
            if future is not None and not future.done():
                future.cancel()
            if self._capture_executor is not None:
                executors.append(self._capture_executor)
            for executor, abandoned_future in self._abandoned_capture_workers:
                if not abandoned_future.done():
                    abandoned_future.cancel()
                executors.append(executor)
            self._abandoned_capture_workers = []
            self._capture_executor = None
            self._capture_future = None
            self._capture_future_started_at = 0.0
            self._capture_future_timed_out = False
        for executor in executors:
            # Project requires Python 3.11; cancel_futures is available on >=3.9.
            executor.shutdown(wait=False, cancel_futures=True)


    def _clear_completed_capture_worker(self) -> None:
        future: Future[OcrExtractionResult] | None = None
        timed_out = False
        executors_to_shutdown: list[ThreadPoolExecutor] = []
        with self._capture_worker_lock:
            executors_to_shutdown.extend(self._drain_completed_abandoned_capture_workers_locked())
            current = self._capture_future
            if current is None or not current.done():
                future = None
            else:
                future = current
                timed_out = bool(self._capture_future_timed_out)
                self._capture_future = None
                self._capture_future_started_at = 0.0
                self._capture_future_timed_out = False
        if timed_out and future is not None:
            try:
                future.result()
            except Exception as exc:
                self._logger.debug("ocr_reader previous timed-out capture eventually failed: {}", exc)
        for executor in executors_to_shutdown:
            executor.shutdown(wait=False, cancel_futures=True)


    def _submit_capture_worker(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        backend_plan: SelectedOcrBackendPlan,
        collect_background_hash: bool,
        allow_separate_background_capture: bool,
    ) -> Future[OcrExtractionResult]:
        executors_to_shutdown: list[ThreadPoolExecutor] = []
        recovered_elapsed = 0.0
        cancel_requested = False
        timeout_error: _CaptureTimedOut | None = None
        future: Future[OcrExtractionResult] | None = None
        with self._capture_worker_lock:
            executors_to_shutdown.extend(self._drain_completed_abandoned_capture_workers_locked())
            current = self._capture_future
            if current is not None and not current.done():
                elapsed = max(0.0, time.monotonic() - float(self._capture_future_started_at or 0.0))
                if self._capture_future_timed_out:
                    timeout_seconds = float(_ocr_reader_module._OCR_CAPTURE_TIMEOUT_SECONDS)
                    if timeout_seconds <= 0.0:
                        timeout_seconds = 12.0
                    recovery_after = timeout_seconds + max(timeout_seconds, 0.25)
                    if elapsed >= recovery_after:
                        cancel_requested = current.cancel()
                        executor = self._capture_executor
                        if (
                            not cancel_requested
                            and not current.done()
                            and len(self._abandoned_capture_workers)
                            >= _OCR_MAX_ABANDONED_CAPTURE_WORKERS
                        ):
                            timeout_error = _CaptureTimedOut(
                                f"previous ocr_reader capture/OCR timed out and is still running after {elapsed:.1f}s; "
                                "stuck capture worker recovery limit reached"
                            )
                        elif executor is not None:
                            if not cancel_requested and not current.done():
                                self._abandoned_capture_workers.append((executor, current))
                            executors_to_shutdown.append(executor)
                        if timeout_error is None:
                            self._capture_executor = None
                            self._capture_future = None
                            self._capture_future_started_at = 0.0
                            self._capture_future_timed_out = False
                            recovered_elapsed = elapsed
                    else:
                        raise _CaptureStillRunning(
                            f"previous ocr_reader capture/OCR timed out and is still running after {elapsed:.1f}s; "
                            "skipping new capture to avoid accumulating blocked OCR threads"
                        )
                else:
                    raise _CaptureStillRunning(
                        f"previous ocr_reader capture/OCR is still running after {elapsed:.1f}s; "
                        "skipping new capture to avoid overlapping OCR work"
                    )
            if timeout_error is None:
                executor = self._capture_executor
                if executor is None:
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="galgame-ocr-capture")
                    self._capture_executor = executor
                future = executor.submit(
                    self._capture_and_extract_text,
                    target,
                    profile,
                    backend_plan,
                    collect_background_hash,
                    allow_separate_background_capture,
                )
                self._capture_executor = executor
                self._capture_future = future
                self._capture_future_started_at = time.monotonic()
                self._capture_future_timed_out = False
        if recovered_elapsed > 0.0:
            self._logger.warning(
                "ocr_reader rotating timed-out capture executor after {:.1f}s; cancel_requested={}",
                recovered_elapsed,
                cancel_requested,
            )
        for executor_to_shutdown in executors_to_shutdown:
            executor_to_shutdown.shutdown(wait=False, cancel_futures=True)
        if timeout_error is not None:
            raise timeout_error
        assert future is not None
        return future


    async def _capture_and_extract_text_with_timeout(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        backend_plan: SelectedOcrBackendPlan,
        collect_background_hash: bool = True,
        allow_separate_background_capture: bool = True,
    ) -> OcrExtractionResult:
        timeout_seconds = float(_ocr_reader_module._OCR_CAPTURE_TIMEOUT_SECONDS)
        if timeout_seconds <= 0.0:
            timeout_seconds = 12.0
        self._clear_completed_capture_worker()
        future = self._submit_capture_worker(
            target,
            profile,
            backend_plan,
            collect_background_hash,
            allow_separate_background_capture,
        )
        try:
            # State machine: _submit_capture_worker → wrap_future → shield → wait_for.
            #   on success: result returned, _capture_future cleared by _clear_completed_capture_worker.
            #   on timeout: _capture_future_timed_out set under lock; shield keeps
            #     the ThreadPoolExecutor future alive so later cleanup can observe completion.
            #   on cancel: shield prevents cancellation from propagating into the worker thread.
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            with self._capture_worker_lock:
                if self._capture_future is future:
                    self._capture_future_timed_out = True
            raise _CaptureTimedOut(
                f"ocr_reader capture/OCR timed out after {timeout_seconds:.1f}s"
            ) from exc
        finally:
            if future.done():
                self._clear_completed_capture_worker()


    async def _capture_followup_text(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        backend_plan: SelectedOcrBackendPlan,
        *,
        elapsed_since_capture: float = 0.0,
        collect_background_hash: bool = True,
        allow_separate_background_capture: bool = True,
    ) -> OcrExtractionResult:
        remaining = _OCR_FOLLOWUP_CONFIRM_DELAY_SECONDS - elapsed_since_capture
        if remaining > 0:
            await asyncio.sleep(remaining)
        return await self._capture_and_extract_text_with_timeout(
            target,
            profile,
            backend_plan,
            collect_background_hash=collect_background_hash,
            allow_separate_background_capture=allow_separate_background_capture,
        )


    def _configured_backend_selection(self) -> str:
        selection = str(self._config.ocr_reader_backend_selection or "auto").strip().lower()
        if selection in {"auto", "rapidocr"}:
            return selection
        return "auto"


    def _capture_profile_selection_for_target(
        self,
        target: DetectedGameWindow,
        *,
        stage: str = _AIHONG_DIALOGUE_STAGE,
    ) -> ResolvedOcrCaptureSelection:
        configured_profile = _lookup_capture_profile(
            self._capture_profiles,
            target,
            stage=stage,
        )
        if configured_profile is not None:
            return configured_profile

        builtin_profile = _builtin_capture_profile_for_target_stage(target, stage=stage)
        if builtin_profile is not None:
            return ResolvedOcrCaptureSelection(
                profile=builtin_profile,
                match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET,
            )

        return ResolvedOcrCaptureSelection(
            profile=OcrCaptureProfile(
                left_inset_ratio=self._config.ocr_reader_left_inset_ratio,
                right_inset_ratio=self._config.ocr_reader_right_inset_ratio,
                top_ratio=self._config.ocr_reader_top_ratio,
                bottom_inset_ratio=self._config.ocr_reader_bottom_inset_ratio,
            ),
            match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT,
        )


    def _capture_profile_for_target(
        self,
        target: DetectedGameWindow,
        *,
        stage: str = _AIHONG_DIALOGUE_STAGE,
    ) -> OcrCaptureProfile:
        return self._capture_profile_selection_for_target(target, stage=stage).profile


    @staticmethod
    def _full_window_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
        )


    @staticmethod
    def _top_region_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.02,
            right_inset_ratio=0.02,
            top_ratio=0.0,
            bottom_inset_ratio=0.55,
        )


    @staticmethod
    def _menu_region_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.08,
            right_inset_ratio=0.08,
            top_ratio=0.18,
            bottom_inset_ratio=0.08,
        )


    @staticmethod
    def _capture_profile_key(profile: OcrCaptureProfile) -> tuple[float, float, float, float]:
        return (
            round(float(profile.left_inset_ratio), 4),
            round(float(profile.right_inset_ratio), 4),
            round(float(profile.top_ratio), 4),
            round(float(profile.bottom_inset_ratio), 4),
        )


    def _capture_and_extract_text(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        plan: SelectedOcrBackendPlan,
        collect_background_hash: bool = True,
        allow_separate_background_capture: bool = True,
    ) -> OcrExtractionResult:
        started_at = self._time_fn()
        background_hash = self._last_background_hash
        background_duration = 0.0
        background_hash_skipped = True
        capture_started_at = self._time_fn()
        frame = self._capture_backend.capture_frame(target, profile)
        capture_frame_duration = max(0.0, self._time_fn() - capture_started_at)
        frame_info = getattr(frame, "info", {}) if frame is not None else {}
        embedded_background_hash = (
            str(frame_info.get("galgame_source_background_hash") or "")
            if isinstance(frame_info, dict)
            else ""
        )
        if collect_background_hash and embedded_background_hash:
            background_hash = embedded_background_hash
            background_hash_skipped = False
            self._last_background_hash_capture_at = started_at
        hash_started_at = self._time_fn()
        capture_hash = self._capture_image_hash(frame)
        capture_hash_duration = max(0.0, self._time_fn() - hash_started_at)
        ocr_started_at = self._time_fn()
        extraction = self._extract_text_from_image(frame, plan=plan)
        ocr_duration = max(0.0, self._time_fn() - ocr_started_at)
        vision_frame = frame
        if isinstance(frame_info, dict):
            source_frame = frame_info.get("galgame_full_frame_image")
            if source_frame is not None and hasattr(source_frame, "convert"):
                vision_frame = source_frame
        extraction.captured_image = vision_frame
        primary_text = str(extraction.text or "").strip()
        primary_text_is_dialogue = bool(
            primary_text and _looks_like_ocr_dialogue_text(primary_text)
        )
        background_hash_interval = (
            _BACKGROUND_HASH_DIALOGUE_SAMPLE_INTERVAL_SECONDS
            if primary_text_is_dialogue
            else _BACKGROUND_HASH_MIN_INTERVAL_SECONDS
        )
        last_background_hash_capture_at = float(self._last_background_hash_capture_at or 0.0)
        if primary_text_is_dialogue and last_background_hash_capture_at <= 0.0:
            self._last_background_hash_capture_at = started_at
            last_background_hash_capture_at = started_at
        if (
            collect_background_hash
            and not embedded_background_hash
            and allow_separate_background_capture
            and not (primary_text and _looks_like_self_ui_text(primary_text))
            and started_at - last_background_hash_capture_at >= background_hash_interval
        ):
            try:
                background_started_at = self._time_fn()
                background_frame = self._capture_backend.capture_frame(
                    target,
                    self._background_capture_profile(),
                )
                background_hash = self._background_perceptual_hash(background_frame)
                background_duration = max(0.0, self._time_fn() - background_started_at)
                background_hash_skipped = False
                self._last_background_hash_capture_at = started_at
            except Exception as exc:
                self._logger.debug("ocr_reader background scene hash skipped: {}", exc)
        extraction.capture_image_hash = capture_hash
        extraction.background_hash = background_hash
        extraction.timing = {
            "total_duration_seconds": max(0.0, self._time_fn() - started_at),
            "capture_frame_duration_seconds": capture_frame_duration,
            "background_hash_duration_seconds": background_duration,
            "capture_image_hash_duration_seconds": capture_hash_duration,
            "ocr_extract_duration_seconds": ocr_duration,
            "background_hash_skipped": background_hash_skipped,
        }
        if isinstance(frame_info, dict):
            extraction.capture_backend_kind = str(
                frame_info.get("galgame_capture_backend_kind")
                or getattr(self._capture_backend, "last_backend_kind", "")
                or getattr(self._capture_backend, "selection", "")
            )
            extraction.capture_backend_detail = str(
                frame_info.get("galgame_capture_backend_detail")
                or getattr(self._capture_backend, "last_backend_detail", "")
                or ""
            )
            extraction.bounds_coordinate_space = str(
                frame_info.get("galgame_bounds_coordinate_space") or ""
            )
            source_size = frame_info.get("galgame_source_size")
            if isinstance(source_size, dict):
                extraction.source_size = dict(source_size)
            capture_rect = frame_info.get("galgame_capture_rect")
            if isinstance(capture_rect, dict):
                extraction.capture_rect = dict(capture_rect)
            window_rect = frame_info.get("galgame_window_rect")
            if isinstance(window_rect, dict):
                extraction.window_rect = dict(window_rect)
        else:
            extraction.capture_backend_kind = str(
                getattr(self._capture_backend, "last_backend_kind", "")
                or getattr(self._capture_backend, "selection", "")
                or ""
            )
            extraction.capture_backend_detail = str(
                getattr(self._capture_backend, "last_backend_detail", "") or ""
            )
        capture_quality_detail = self._capture_quality_detail(frame)
        if (
            extraction.capture_backend_kind == _CAPTURE_BACKEND_PRINTWINDOW
            and capture_quality_detail
        ):
            extraction.warnings.append(f"printwindow capture quality: {capture_quality_detail}")
            if extraction.capture_backend_detail in {"", "selected"}:
                extraction.capture_backend_detail = capture_quality_detail
            extraction.timing["capture_quality_detail"] = capture_quality_detail
            if (
                not bool(getattr(target, "is_foreground", False))
                and not str(extraction.text or "").strip()
            ):
                extraction.warnings.append(
                    f"backend_not_suitable_for_background: {capture_quality_detail}"
                )
                extraction.capture_backend_detail = "backend_not_suitable_for_background"
                extraction.timing["background_capture_backend_unsuitable"] = True
        if extraction.text and _looks_like_self_ui_text(extraction.text):
            extraction.timing["screen_awareness_skipped"] = True
            extraction.timing["screen_awareness_skip_reason"] = "rejected_primary_text"
            extraction.timing["screen_awareness_region_count"] = 0.0
            extraction.timing["screen_awareness_capture_duration_seconds"] = 0.0
            extraction.timing["screen_awareness_ocr_duration_seconds"] = 0.0
            extraction.timing["total_duration_seconds"] = max(0.0, self._time_fn() - started_at)
            return extraction
        self._augment_extraction_with_screen_awareness(
            extraction,
            target=target,
            primary_profile=profile,
            plan=plan,
            now=started_at,
        )
        extraction.timing["total_duration_seconds"] = max(0.0, self._time_fn() - started_at)
        return extraction


    def _update_capture_profile_recommendation(
        self,
        extraction: OcrExtractionResult,
        *,
        classification: ScreenClassification,
        target: DetectedGameWindow,
        now: float,
    ) -> None:
        if (
            classification.screen_type not in {OCR_CAPTURE_PROFILE_STAGE_DEFAULT, OCR_CAPTURE_PROFILE_STAGE_DIALOGUE}
            and classification.confidence >= 0.5
        ):
            self._recommended_capture_profile = {}
            return
        if (
            classification.screen_type != OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            or classification.confidence < 0.55
            or not str(extraction.text or "").strip()
        ):
            return
        bounds: list[dict[str, float]] = []
        for element in classification.ui_elements:
            normalized = element.get("normalized_bounds")
            if not isinstance(normalized, dict):
                continue
            try:
                left = float(normalized.get("left"))
                top = float(normalized.get("top"))
                right = float(normalized.get("right"))
                bottom = float(normalized.get("bottom"))
            except (TypeError, ValueError):
                continue
            if right <= left or bottom <= top:
                continue
            bounds.append({"left": left, "top": top, "right": right, "bottom": bottom})
        if not bounds:
            return

        min_top = max(0.0, min(item["top"] for item in bounds))
        max_bottom = min(1.0, max(item["bottom"] for item in bounds))
        text_height = max_bottom - min_top
        if text_height <= 0.02:
            return
        current_selection = self._capture_profile_selection_for_target(
            target,
            stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        )
        current_profile = current_selection.profile
        top_ratio = round(max(0.0, min_top - 0.08), 2)
        bottom_inset_ratio = round(max(0.0, 1.0 - max_bottom - 0.08), 2)
        if 1.0 - top_ratio - bottom_inset_ratio < 0.16:
            top_ratio = round(max(0.0, 1.0 - bottom_inset_ratio - 0.16), 2)
        if top_ratio + bottom_inset_ratio >= 0.98:
            return
        candidate_profile = OcrCaptureProfile(
            left_inset_ratio=current_profile.left_inset_ratio,
            right_inset_ratio=current_profile.right_inset_ratio,
            top_ratio=top_ratio,
            bottom_inset_ratio=bottom_inset_ratio,
        )
        current_payload = current_profile.to_dict()
        candidate_payload = candidate_profile.to_dict()
        delta = sum(
            abs(float(candidate_payload[key]) - float(current_payload.get(key, 0.0)))
            for key in OCR_CAPTURE_PROFILE_RATIO_KEYS
        )
        if delta < 0.06:
            return
        bucket_key = (
            build_ocr_capture_profile_bucket_key(int(target.width or 0), int(target.height or 0)).lower()
            if int(target.width or 0) > 0 and int(target.height or 0) > 0
            else ""
        )
        sample_text = " ".join(_stripped_ocr_lines(extraction.text))[:120]
        self._recommended_capture_profile = {
            "process_name": str(target.process_name or ""),
            "stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "save_scope": "window_bucket",
            "bucket_key": bucket_key,
            "capture_profile": candidate_payload,
            "current_capture_profile": current_payload,
            "confidence": min(0.95, max(0.0, float(classification.confidence))),
            "reason": "dialogue_text_bounds_offset",
            "sample_text": sample_text,
            "manual_profile_present": self._has_manual_capture_profile(target),
            "created_at": utc_now_iso(now),
        }
