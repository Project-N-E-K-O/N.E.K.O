from __future__ import annotations

from typing import Any

from .models import (
    compute_ocr_window_aspect_ratio,
    json_copy,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_SAVE_SCOPES,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGES,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    parse_ocr_capture_profile_bucket_key,
)


def _normalize_ocr_capture_profile_stage(stage: str | None) -> str:
    normalized = str(stage or OCR_CAPTURE_PROFILE_STAGE_DEFAULT).strip().lower()
    if normalized not in OCR_CAPTURE_PROFILE_STAGES:
        raise ValueError(f"invalid OCR capture profile stage: {stage!r}")
    return normalized


def _normalize_ocr_capture_profile_save_scope(save_scope: str | None) -> str:
    normalized = str(save_scope or "").strip().lower()
    if not normalized:
        return ""
    if normalized not in OCR_CAPTURE_PROFILE_SAVE_SCOPES:
        raise ValueError(f"invalid OCR capture profile save_scope: {save_scope!r}")
    return normalized


def _is_ratio_profile_payload(value: object) -> bool:
    return isinstance(value, dict) and all(key in value for key in OCR_CAPTURE_PROFILE_RATIO_KEYS)


def _normalize_ocr_capture_profile_payload(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("capture_profile must be an object")
    normalized: dict[str, float] = {}
    for key in OCR_CAPTURE_PROFILE_RATIO_KEYS:
        raw = value.get(key)
        if isinstance(raw, bool):
            raise ValueError(f"{key} must be a number")
        try:
            parsed = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        if parsed < 0.0 or parsed >= 1.0:
            raise ValueError(f"{key} must be >= 0.0 and < 1.0")
        normalized[key] = parsed
    if normalized["left_inset_ratio"] + normalized["right_inset_ratio"] >= 1.0:
        raise ValueError("left_inset_ratio + right_inset_ratio must be < 1.0")
    if normalized["top_ratio"] + normalized["bottom_inset_ratio"] >= 1.0:
        raise ValueError("top_ratio + bottom_inset_ratio must be < 1.0")
    return normalized


def _capture_profile_entry_to_stage_map(value: object) -> dict[str, dict[str, float]]:
    if _is_ratio_profile_payload(value):
        try:
            return {OCR_CAPTURE_PROFILE_STAGE_DEFAULT: _normalize_ocr_capture_profile_payload(value)}
        except ValueError:
            return {}
    raw = value if isinstance(value, dict) else {}
    stage_map: dict[str, dict[str, float]] = {}
    for stage_name, profile in raw.items():
        normalized_stage_name = str(stage_name or "").strip().lower()
        if (
            not normalized_stage_name
            or normalized_stage_name == OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY
            or not _is_ratio_profile_payload(profile)
        ):
            continue
        try:
            normalized_stage_name = _normalize_ocr_capture_profile_stage(normalized_stage_name)
            normalized_profile = _normalize_ocr_capture_profile_payload(profile)
        except ValueError:
            continue
        stage_map[normalized_stage_name] = normalized_profile
    return stage_map


def _capture_profile_bucket_entry_to_stage_map(value: object) -> dict[str, dict[str, float]]:
    raw = value if isinstance(value, dict) else {}
    stage_map: dict[str, dict[str, float]] = {}
    raw_stages = raw.get("stages")
    if not isinstance(raw_stages, dict):
        return stage_map
    for stage_name, profile in raw_stages.items():
        normalized_stage_name = str(stage_name or "").strip().lower()
        if not normalized_stage_name or not _is_ratio_profile_payload(profile):
            continue
        try:
            normalized_stage_name = _normalize_ocr_capture_profile_stage(normalized_stage_name)
            normalized_profile = _normalize_ocr_capture_profile_payload(profile)
        except ValueError:
            continue
        stage_map[normalized_stage_name] = normalized_profile
    return stage_map


def _capture_profile_entry_to_window_bucket_map(value: object) -> dict[str, dict[str, Any]]:
    raw = value if isinstance(value, dict) else {}
    raw_buckets = raw.get(OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY)
    if not isinstance(raw_buckets, dict):
        return {}
    bucket_map: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_value in raw_buckets.items():
        normalized_bucket_key = str(bucket_key or "").strip().lower()
        parsed_dimensions = parse_ocr_capture_profile_bucket_key(normalized_bucket_key)
        if not normalized_bucket_key or parsed_dimensions is None or not isinstance(bucket_value, dict):
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
                bucket_value.get("aspect_ratio") or compute_ocr_window_aspect_ratio(width, height)
            )
        except (TypeError, ValueError):
            aspect_ratio = compute_ocr_window_aspect_ratio(width, height)
        stage_map = _capture_profile_bucket_entry_to_stage_map(bucket_value)
        if not stage_map:
            continue
        bucket_map[normalized_bucket_key] = {
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "stages": stage_map,
        }
    return bucket_map


def _window_bucket_map_to_capture_profile_payload(
    bucket_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_value in bucket_map.items():
        normalized_bucket_key = str(bucket_key or "").strip().lower()
        if not normalized_bucket_key or not isinstance(bucket_value, dict):
            continue
        try:
            width = int(bucket_value.get("width") or 0)
            height = int(bucket_value.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        try:
            aspect_ratio = float(
                bucket_value.get("aspect_ratio") or compute_ocr_window_aspect_ratio(width, height)
            )
        except (TypeError, ValueError):
            aspect_ratio = compute_ocr_window_aspect_ratio(width, height)
        stage_map = _capture_profile_bucket_entry_to_stage_map(bucket_value)
        if not stage_map:
            continue
        payload[normalized_bucket_key] = {
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "stages": {
                stage_name: json_copy(profile)
                for stage_name, profile in stage_map.items()
            },
        }
    return payload


def _capture_profile_components_to_entry(
    stage_map: dict[str, dict[str, float]],
    window_bucket_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not window_bucket_map and len(stage_map) == 1 and OCR_CAPTURE_PROFILE_STAGE_DEFAULT in stage_map:
        return json_copy(stage_map[OCR_CAPTURE_PROFILE_STAGE_DEFAULT])
    payload = {stage_name: json_copy(profile) for stage_name, profile in stage_map.items()}
    bucket_payload = _window_bucket_map_to_capture_profile_payload(window_bucket_map)
    if bucket_payload:
        payload[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY] = bucket_payload
    return payload
