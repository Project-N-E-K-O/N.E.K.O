from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .aihong_state import (
    AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET as _AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET,
    AIHONG_DIALOGUE_STAGE as _AIHONG_DIALOGUE_STAGE,
    AIHONG_MENU_CAPTURE_PROFILE_PRESET as _AIHONG_MENU_CAPTURE_PROFILE_PRESET,
    AIHONG_MENU_STAGE as _AIHONG_MENU_STAGE,
    matches_aihong_target as _matches_aihong_target_info,
)
from .models import (
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    parse_ocr_capture_profile_bucket_key,
)

__all__ = [
    "OcrCaptureProfile",
    "ParsedOcrCaptureBucket",
    "ParsedOcrCaptureProcessConfig",
    "ResolvedOcrCaptureSelection",
    "_builtin_capture_profile_for_target",
    "_builtin_capture_profile_for_target_stage",
    "_lookup_capture_profile",
    "_matches_aihong_target",
    "_parse_configured_capture_profiles",
    "_resolve_stage_capture_profile",
    "_uses_manual_capture_profile",
]

@dataclass(frozen=True, slots=True)
class OcrCaptureProfile:
    left_inset_ratio: float = DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO
    right_inset_ratio: float = DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO
    top_ratio: float = DEFAULT_OCR_CAPTURE_TOP_RATIO
    bottom_inset_ratio: float = DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO

    def to_dict(self) -> dict[str, float]:
        return {
            "left_inset_ratio": self.left_inset_ratio,
            "right_inset_ratio": self.right_inset_ratio,
            "top_ratio": self.top_ratio,
            "bottom_inset_ratio": self.bottom_inset_ratio,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OcrCaptureProfile:
        profile = cls(
            left_inset_ratio=float(
                value.get("left_inset_ratio", DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO)
            ),
            right_inset_ratio=float(
                value.get("right_inset_ratio", DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO)
            ),
            top_ratio=float(value.get("top_ratio", DEFAULT_OCR_CAPTURE_TOP_RATIO)),
            bottom_inset_ratio=float(
                value.get("bottom_inset_ratio", DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO)
            ),
        )
        ratios = {
            "left_inset_ratio": profile.left_inset_ratio,
            "right_inset_ratio": profile.right_inset_ratio,
            "top_ratio": profile.top_ratio,
            "bottom_inset_ratio": profile.bottom_inset_ratio,
        }
        for field_name, ratio in ratios.items():
            if not math.isfinite(ratio):
                raise ValueError(f"{field_name} must be finite")
            if ratio < 0.0 or ratio > 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if profile.left_inset_ratio + profile.right_inset_ratio >= 1.0:
            raise ValueError(
                "left_inset_ratio and right_inset_ratio must sum to less than 1"
            )
        if profile.top_ratio + profile.bottom_inset_ratio >= 1.0:
            raise ValueError("top_ratio and bottom_inset_ratio must sum to less than 1")
        return profile


def _matches_aihong_target(target: DetectedGameWindow | None) -> bool:
    if target is None:
        return False
    return _matches_aihong_target_info(
        process_name=target.process_name,
        normalized_title=target.normalized_title,
    )


def _builtin_capture_profile_for_target(target: DetectedGameWindow) -> OcrCaptureProfile | None:
    return _builtin_capture_profile_for_target_stage(target, stage=_AIHONG_DIALOGUE_STAGE)


def _builtin_capture_profile_for_target_stage(
    target: DetectedGameWindow,
    *,
    stage: str,
) -> OcrCaptureProfile | None:
    if not _matches_aihong_target(target):
        return None
    if stage == _AIHONG_MENU_STAGE:
        return OcrCaptureProfile.from_dict(_AIHONG_MENU_CAPTURE_PROFILE_PRESET)
    return OcrCaptureProfile.from_dict(_AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET)


@dataclass(slots=True)
class ParsedOcrCaptureBucket:
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    stages: dict[str, OcrCaptureProfile] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedOcrCaptureProcessConfig:
    stages: dict[str, OcrCaptureProfile] = field(default_factory=dict)
    window_buckets: dict[str, ParsedOcrCaptureBucket] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedOcrCaptureSelection:
    profile: OcrCaptureProfile = field(default_factory=OcrCaptureProfile)
    match_source: str = OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT
    bucket_key: str = ""


def _resolve_stage_capture_profile(
    stage_profiles: dict[str, OcrCaptureProfile],
    *,
    stage: str,
) -> OcrCaptureProfile | None:
    return stage_profiles.get(stage) or stage_profiles.get(OCR_CAPTURE_PROFILE_STAGE_DEFAULT)


def _uses_manual_capture_profile(
    profiles: dict[str, ParsedOcrCaptureProcessConfig],
    target: DetectedGameWindow,
) -> bool:
    process_name = str(target.process_name or "").strip().lower()
    if not process_name:
        return False
    return process_name in profiles


def _lookup_capture_profile(
    profiles: dict[str, ParsedOcrCaptureProcessConfig],
    target: DetectedGameWindow,
    *,
    stage: str,
) -> ResolvedOcrCaptureSelection | None:
    process_name = str(target.process_name or "").strip().lower()
    if not process_name:
        return None
    configured = profiles.get(process_name)
    if configured is None:
        return None

    if target.width > 0 and target.height > 0:
        exact_bucket_key = build_ocr_capture_profile_bucket_key(target.width, target.height).lower()
        exact_bucket = configured.window_buckets.get(exact_bucket_key)
        if exact_bucket is not None:
            exact_profile = _resolve_stage_capture_profile(exact_bucket.stages, stage=stage)
            if exact_profile is not None:
                return ResolvedOcrCaptureSelection(
                    profile=exact_profile,
                    match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT,
                    bucket_key=exact_bucket_key,
                )

        target_aspect_ratio = target.aspect_ratio
        if target_aspect_ratio > 0:
            nearest_bucket_key = ""
            nearest_profile: OcrCaptureProfile | None = None
            nearest_size_delta: int | None = None
            nearest_aspect_delta: float | None = None
            for bucket_key, bucket in configured.window_buckets.items():
                profile = _resolve_stage_capture_profile(bucket.stages, stage=stage)
                if profile is None:
                    continue
                aspect_delta = abs(float(bucket.aspect_ratio or 0.0) - target_aspect_ratio)
                if aspect_delta > 0.03:
                    continue
                size_delta = abs(int(bucket.width or 0) - target.width) + abs(
                    int(bucket.height or 0) - target.height
                )
                if (
                    nearest_size_delta is None
                    or size_delta < nearest_size_delta
                    or (
                        size_delta == nearest_size_delta
                        and (
                            nearest_aspect_delta is None
                            or aspect_delta < nearest_aspect_delta
                        )
                    )
                ):
                    nearest_bucket_key = bucket_key
                    nearest_profile = profile
                    nearest_size_delta = size_delta
                    nearest_aspect_delta = aspect_delta
            if nearest_profile is not None:
                return ResolvedOcrCaptureSelection(
                    profile=nearest_profile,
                    match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST,
                    bucket_key=nearest_bucket_key,
                )

    fallback_profile = _resolve_stage_capture_profile(configured.stages, stage=stage)
    if fallback_profile is not None:
        return ResolvedOcrCaptureSelection(
            profile=fallback_profile,
            match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_PROCESS_FALLBACK,
        )
    return None


def _parse_configured_capture_profiles(
    profiles: dict[str, dict[str, Any]],
    logger,
) -> dict[str, ParsedOcrCaptureProcessConfig]:
    parsed_profiles: dict[str, ParsedOcrCaptureProcessConfig] = {}
    for process_name, profile_value in profiles.items():
        normalized_process_name = str(process_name or "").strip().lower()
        if not normalized_process_name or not isinstance(profile_value, dict):
            continue
        stage_profiles: dict[str, OcrCaptureProfile] = {}
        if all(key in profile_value for key in OCR_CAPTURE_PROFILE_RATIO_KEYS):
            try:
                stage_profiles[OCR_CAPTURE_PROFILE_STAGE_DEFAULT] = OcrCaptureProfile.from_dict(
                    profile_value
                )
            except Exception as exc:
                logger.warning(
                    "ocr_reader failed to parse capture profile for {}: {}",
                    normalized_process_name,
                    exc,
                )
        else:
            for stage_name, stage_profile in profile_value.items():
                normalized_stage_name = str(stage_name or "").strip()
                if normalized_stage_name == OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY:
                    continue
                if not normalized_stage_name or not isinstance(stage_profile, dict):
                    continue
                try:
                    stage_profiles[normalized_stage_name] = OcrCaptureProfile.from_dict(stage_profile)
                except Exception as exc:
                    logger.warning(
                        "ocr_reader failed to parse capture profile for {}/{}: {}",
                        normalized_process_name,
                        normalized_stage_name,
                        exc,
                    )
        window_buckets: dict[str, ParsedOcrCaptureBucket] = {}
        raw_buckets = profile_value.get(OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY)
        if isinstance(raw_buckets, dict):
            for bucket_key, bucket_value in raw_buckets.items():
                normalized_bucket_key = str(bucket_key or "").strip().lower()
                parsed_dimensions = parse_ocr_capture_profile_bucket_key(normalized_bucket_key)
                if parsed_dimensions is None or not isinstance(bucket_value, dict):
                    continue
                try:
                    width = int(bucket_value.get("width") or parsed_dimensions[0])
                    height = int(bucket_value.get("height") or parsed_dimensions[1])
                except (TypeError, ValueError):
                    continue
                if width <= 0 or height <= 0:
                    continue
                try:
                    aspect_ratio = float(
                        bucket_value.get("aspect_ratio")
                        or compute_ocr_window_aspect_ratio(width, height)
                    )
                except (TypeError, ValueError):
                    aspect_ratio = compute_ocr_window_aspect_ratio(width, height)
                raw_stages = bucket_value.get("stages")
                if not isinstance(raw_stages, dict):
                    continue
                bucket_stages: dict[str, OcrCaptureProfile] = {}
                for stage_name, stage_profile in raw_stages.items():
                    normalized_stage_name = str(stage_name or "").strip()
                    if not normalized_stage_name or not isinstance(stage_profile, dict):
                        continue
                    try:
                        bucket_stages[normalized_stage_name] = OcrCaptureProfile.from_dict(
                            stage_profile
                        )
                    except Exception as exc:
                        logger.warning(
                            "ocr_reader failed to parse capture profile for {}/{}/{}: {}",
                            normalized_process_name,
                            normalized_bucket_key,
                            normalized_stage_name,
                            exc,
                        )
                if bucket_stages:
                    canonical_bucket_key = build_ocr_capture_profile_bucket_key(width, height).lower()
                    window_buckets[canonical_bucket_key] = ParsedOcrCaptureBucket(
                        width=width,
                        height=height,
                        aspect_ratio=aspect_ratio,
                        stages=bucket_stages,
                    )
        if stage_profiles or window_buckets:
            parsed_profiles[normalized_process_name] = ParsedOcrCaptureProcessConfig(
                stages=stage_profiles,
                window_buckets=window_buckets,
            )
    return parsed_profiles
