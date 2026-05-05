from __future__ import annotations

from typing import Any

from .models import (
    ADVANCE_SPEEDS,
    ADVANCE_SPEED_MEDIUM,
    MODES,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    STORE_BOUND_GAME_ID,
    STORE_ADVANCE_SPEED,
    STORE_DEDUPE_WINDOW,
    STORE_EVENTS_BYTE_OFFSET,
    STORE_EVENTS_FILE_SIZE,
    STORE_LAST_ERROR,
    STORE_LAST_SEQ,
    STORE_MEMORY_READER_TARGET,
    STORE_MODE,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_CAPTURE_PROFILES,
    STORE_OCR_FAST_LOOP_ENABLED,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_OCR_WINDOW_TARGET,
    STORE_PUSH_NOTIFICATIONS,
    STORE_READER_MODE,
    STORE_SESSION_ID,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    parse_ocr_capture_profile_bucket_key,
)


class GalgameStore:
    def __init__(self, plugin_store, logger) -> None:
        self._store = plugin_store
        self._logger = logger

    def _read(self, key: str, default: Any) -> Any:
        if not getattr(self._store, "enabled", False):
            return default
        return self._store._read_value(key, default)  # type: ignore[attr-defined]

    def _write(self, key: str, value: Any) -> None:
        if not getattr(self._store, "enabled", False):
            return
        self._store._write_value(key, value)  # type: ignore[attr-defined]

    def load_config_overrides(self) -> dict[str, Any]:
        raw_backend = self._read(STORE_OCR_BACKEND_SELECTION, None)
        raw_capture = self._read(STORE_OCR_CAPTURE_BACKEND, None)
        raw_reader_mode = self._read(STORE_READER_MODE, None)
        raw_poll = self._read(STORE_OCR_POLL_INTERVAL_SECONDS, None)
        raw_trigger = self._read(STORE_OCR_TRIGGER_MODE, None)
        raw_fast_loop = self._read(STORE_OCR_FAST_LOOP_ENABLED, None)
        raw_vision = self._read(STORE_LLM_VISION_ENABLED, None)
        raw_px = self._read(STORE_LLM_VISION_MAX_IMAGE_PX, None)
        raw_templates = self._read(STORE_OCR_SCREEN_TEMPLATES, None)

        return {
            STORE_OCR_BACKEND_SELECTION: (
                str(raw_backend) if isinstance(raw_backend, str) and raw_backend else None
            ),
            STORE_OCR_CAPTURE_BACKEND: (
                str(raw_capture) if isinstance(raw_capture, str) and raw_capture else None
            ),
            STORE_READER_MODE: (
                str(raw_reader_mode)
                if isinstance(raw_reader_mode, str) and raw_reader_mode
                else None
            ),
            STORE_OCR_POLL_INTERVAL_SECONDS: (
                max(0.1, float(raw_poll))
                if isinstance(raw_poll, (int, float)) and not isinstance(raw_poll, bool)
                else None
            ),
            STORE_OCR_TRIGGER_MODE: (
                str(raw_trigger) if isinstance(raw_trigger, str) and raw_trigger else None
            ),
            STORE_OCR_FAST_LOOP_ENABLED: (
                bool(raw_fast_loop) if isinstance(raw_fast_loop, bool) else None
            ),
            STORE_LLM_VISION_ENABLED: (
                bool(raw_vision) if isinstance(raw_vision, bool) else None
            ),
            STORE_LLM_VISION_MAX_IMAGE_PX: (
                max(64, int(raw_px))
                if isinstance(raw_px, (int, float)) and not isinstance(raw_px, bool)
                else None
            ),
            STORE_OCR_SCREEN_TEMPLATES: raw_templates if isinstance(raw_templates, list) else None,
        }

    def persist_config_override(self, key: str, value: Any) -> None:
        self._write(key, value)

    @staticmethod
    def _sanitize_ratio_profile(raw_value: Any) -> dict[str, float] | None:
        if not isinstance(raw_value, dict):
            return None
        cleaned: dict[str, float] = {}
        for key in OCR_CAPTURE_PROFILE_RATIO_KEYS:
            value = raw_value.get(key)
            if isinstance(value, bool):
                return None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            if parsed < 0.0 or parsed >= 1.0:
                return None
            cleaned[key] = parsed
        return cleaned

    @classmethod
    def _sanitize_ocr_capture_profiles(cls, raw_value: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
        warnings: list[str] = []
        if raw_value in ({}, None):
            return {}, warnings
        if not isinstance(raw_value, dict):
            return {}, ["invalid ocr_capture_profiles dropped: non-object"]

        normalized: dict[str, dict[str, Any]] = {}
        for process_name, profile in raw_value.items():
            if not isinstance(process_name, str) or not process_name.strip():
                warnings.append("invalid ocr_capture_profiles item dropped: bad process name")
                continue
            if not isinstance(profile, dict):
                warnings.append(
                    f"invalid ocr_capture_profiles item dropped: {process_name!r} is not an object"
                )
                continue
            cleaned = cls._sanitize_ratio_profile(profile)
            if cleaned is not None:
                normalized[process_name.strip()] = cleaned
                continue

            stage_profiles: dict[str, dict[str, float]] = {}
            for stage_name, stage_profile in profile.items():
                normalized_stage_name = str(stage_name or "").strip()
                if normalized_stage_name == OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY:
                    continue
                if not normalized_stage_name:
                    warnings.append(
                        f"invalid ocr_capture_profiles stage dropped: {process_name!r} has empty stage name"
                    )
                    continue
                cleaned_stage = cls._sanitize_ratio_profile(stage_profile)
                if cleaned_stage is None:
                    warnings.append(
                        f"invalid ocr_capture_profiles stage dropped: {process_name!r}/{normalized_stage_name!r} has invalid ratios"
                    )
                    continue
                stage_profiles[normalized_stage_name] = cleaned_stage
            bucket_profiles: dict[str, dict[str, Any]] = {}
            raw_buckets = profile.get(OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY)
            if raw_buckets not in ({}, None):
                if not isinstance(raw_buckets, dict):
                    warnings.append(
                        f"invalid ocr_capture_profiles buckets dropped: {process_name!r}.__window_buckets__ is not an object"
                    )
                else:
                    for bucket_key, bucket_value in raw_buckets.items():
                        normalized_bucket_key = str(bucket_key or "").strip().lower()
                        parsed_dimensions = parse_ocr_capture_profile_bucket_key(normalized_bucket_key)
                        if parsed_dimensions is None:
                            warnings.append(
                                f"invalid ocr_capture_profiles bucket dropped: {process_name!r}/{bucket_key!r} has invalid bucket key"
                            )
                            continue
                        if not isinstance(bucket_value, dict):
                            warnings.append(
                                f"invalid ocr_capture_profiles bucket dropped: {process_name!r}/{normalized_bucket_key!r} is not an object"
                            )
                            continue
                        try:
                            width = int(bucket_value.get("width") or parsed_dimensions[0])
                            height = int(bucket_value.get("height") or parsed_dimensions[1])
                        except (TypeError, ValueError):
                            warnings.append(
                                f"invalid ocr_capture_profiles bucket dropped: {process_name!r}/{normalized_bucket_key!r} has invalid width/height"
                            )
                            continue
                        if width <= 0 or height <= 0:
                            warnings.append(
                                f"invalid ocr_capture_profiles bucket dropped: {process_name!r}/{normalized_bucket_key!r} has non-positive width/height"
                            )
                            continue
                        try:
                            aspect_ratio = float(
                                bucket_value.get("aspect_ratio")
                                or compute_ocr_window_aspect_ratio(width, height)
                            )
                        except (TypeError, ValueError):
                            aspect_ratio = compute_ocr_window_aspect_ratio(width, height)
                        raw_bucket_stages = bucket_value.get("stages")
                        if not isinstance(raw_bucket_stages, dict):
                            warnings.append(
                                f"invalid ocr_capture_profiles bucket dropped: {process_name!r}/{normalized_bucket_key!r} has no valid stages"
                            )
                            continue
                        bucket_stage_profiles: dict[str, dict[str, float]] = {}
                        for stage_name, stage_profile in raw_bucket_stages.items():
                            normalized_stage_name = str(stage_name or "").strip()
                            if not normalized_stage_name:
                                warnings.append(
                                    f"invalid ocr_capture_profiles bucket stage dropped: {process_name!r}/{normalized_bucket_key!r} has empty stage name"
                                )
                                continue
                            cleaned_stage = cls._sanitize_ratio_profile(stage_profile)
                            if cleaned_stage is None:
                                warnings.append(
                                    f"invalid ocr_capture_profiles bucket stage dropped: {process_name!r}/{normalized_bucket_key!r}/{normalized_stage_name!r} has invalid ratios"
                                )
                                continue
                            bucket_stage_profiles[normalized_stage_name] = cleaned_stage
                        if not bucket_stage_profiles:
                            warnings.append(
                                f"invalid ocr_capture_profiles bucket dropped: {process_name!r}/{normalized_bucket_key!r} has no valid stages"
                            )
                            continue
                        canonical_bucket_key = build_ocr_capture_profile_bucket_key(width, height).lower()
                        bucket_profiles[canonical_bucket_key] = {
                            "width": width,
                            "height": height,
                            "aspect_ratio": aspect_ratio,
                            "stages": bucket_stage_profiles,
                        }
            if not stage_profiles and not bucket_profiles:
                warnings.append(
                    f"invalid ocr_capture_profiles item dropped: {process_name!r} has invalid ratios"
                )
                continue
            if not bucket_profiles and len(stage_profiles) == 1 and OCR_CAPTURE_PROFILE_STAGE_DEFAULT in stage_profiles:
                normalized[process_name.strip()] = stage_profiles[OCR_CAPTURE_PROFILE_STAGE_DEFAULT]
            else:
                payload: dict[str, Any] = dict(stage_profiles)
                if bucket_profiles:
                    payload[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY] = bucket_profiles
                normalized[process_name.strip()] = payload
        return normalized, warnings

    @staticmethod
    def _sanitize_ocr_window_target(raw_value: Any) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        if raw_value in ({}, None):
            return {}, warnings
        if not isinstance(raw_value, dict):
            return {}, ["invalid ocr_window_target dropped: non-object"]

        mode = str(raw_value.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            warnings.append("invalid ocr_window_target mode dropped: fallback to auto")
            mode = "auto"

        normalized_title = str(raw_value.get("normalized_title") or "").strip().lower()
        process_name = str(raw_value.get("process_name") or "").strip()
        window_key = str(raw_value.get("window_key") or "").strip()
        selected_at = str(raw_value.get("selected_at") or "").strip()

        try:
            pid = int(raw_value.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
            warnings.append("invalid ocr_window_target pid dropped: fallback to 0")
        try:
            last_known_hwnd = int(raw_value.get("last_known_hwnd") or 0)
        except (TypeError, ValueError):
            last_known_hwnd = 0
            warnings.append("invalid ocr_window_target hwnd dropped: fallback to 0")

        normalized = {
            "mode": mode,
            "window_key": window_key,
            "process_name": process_name,
            "normalized_title": normalized_title,
            "pid": max(0, pid),
            "last_known_hwnd": max(0, last_known_hwnd),
            "selected_at": selected_at,
        }

        if mode == "manual" and not any(
            [
                normalized["window_key"],
                normalized["process_name"],
                normalized["normalized_title"],
                normalized["pid"],
                normalized["last_known_hwnd"],
            ]
        ):
            warnings.append("invalid ocr_window_target dropped: empty manual target")
            return {}, warnings
        return normalized, warnings

    @staticmethod
    def _sanitize_memory_reader_target(raw_value: Any) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        if raw_value in ({}, None):
            return {}, warnings
        if not isinstance(raw_value, dict):
            return {}, ["invalid memory_reader_target dropped: non-object"]

        mode = str(raw_value.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            warnings.append("invalid memory_reader_target mode dropped: fallback to auto")
            mode = "auto"

        try:
            pid = int(raw_value.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
            warnings.append("invalid memory_reader_target pid dropped: fallback to 0")
        try:
            create_time = float(raw_value.get("create_time") or 0.0)
        except (TypeError, ValueError):
            create_time = 0.0
            warnings.append("invalid memory_reader_target create_time dropped: fallback to 0")

        normalized = {
            "mode": mode,
            "process_key": str(raw_value.get("process_key") or "").strip(),
            "process_name": str(raw_value.get("process_name") or "").strip(),
            "exe_path": str(raw_value.get("exe_path") or "").strip(),
            "pid": max(0, pid),
            "engine": str(raw_value.get("engine") or raw_value.get("detected_engine") or "").strip().lower(),
            "detected_engine": str(
                raw_value.get("detected_engine") or raw_value.get("engine") or ""
            ).strip().lower(),
            "detection_reason": str(raw_value.get("detection_reason") or "").strip(),
            "create_time": max(0.0, create_time),
            "selected_at": str(raw_value.get("selected_at") or "").strip(),
        }

        if mode == "manual" and not any(
            [
                normalized["process_key"],
                normalized["process_name"],
                normalized["exe_path"],
                normalized["pid"],
            ]
        ):
            warnings.append("invalid memory_reader_target dropped: empty manual target")
            return {}, warnings
        return normalized, warnings

    def load(self) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        raw_mode = self._read(STORE_MODE, "")
        mode = raw_mode if isinstance(raw_mode, str) and raw_mode in MODES else "companion"
        if raw_mode not in ("", mode):
            warnings.append(f"invalid store mode dropped: {raw_mode!r}")
        raw_advance_speed = self._read(STORE_ADVANCE_SPEED, "")
        advance_speed = (
            raw_advance_speed
            if isinstance(raw_advance_speed, str) and raw_advance_speed in ADVANCE_SPEEDS
            else ADVANCE_SPEED_MEDIUM
        )
        if raw_advance_speed not in ("", advance_speed):
            warnings.append(f"invalid advance_speed dropped: {raw_advance_speed!r}")

        raw_window = self._read(STORE_DEDUPE_WINDOW, [])
        dedupe_window: list[dict[str, str]] = []
        if isinstance(raw_window, list):
            for item in raw_window:
                if not isinstance(item, dict):
                    warnings.append("invalid dedupe_window item dropped: non-object")
                    continue
                game_id = item.get("game_id")
                line_id = item.get("line_id")
                normalized_text = item.get("normalized_text")
                if not (
                    isinstance(game_id, str)
                    and isinstance(line_id, str)
                    and isinstance(normalized_text, str)
                ):
                    warnings.append("invalid dedupe_window item dropped: missing string fields")
                    continue
                dedupe_window.append(
                    {
                        "game_id": game_id,
                        "line_id": line_id,
                        "normalized_text": normalized_text,
                    }
                )
        elif raw_window not in (None, []):
            warnings.append("invalid dedupe_window dropped: non-array")

        raw_last_error = self._read(STORE_LAST_ERROR, {})
        last_error = dict(raw_last_error) if isinstance(raw_last_error, dict) else {}
        if raw_last_error not in ({}, last_error):
            warnings.append("invalid last_error dropped: non-object")
        ocr_capture_profiles, profile_warnings = self._sanitize_ocr_capture_profiles(
            self._read(STORE_OCR_CAPTURE_PROFILES, {})
        )
        warnings.extend(profile_warnings)
        ocr_window_target, target_warnings = self._sanitize_ocr_window_target(
            self._read(STORE_OCR_WINDOW_TARGET, {})
        )
        warnings.extend(target_warnings)
        memory_reader_target, memory_target_warnings = self._sanitize_memory_reader_target(
            self._read(STORE_MEMORY_READER_TARGET, {})
        )
        warnings.extend(memory_target_warnings)

        restored = {
            STORE_BOUND_GAME_ID: self._read(STORE_BOUND_GAME_ID, ""),
            STORE_MODE: mode,
            STORE_PUSH_NOTIFICATIONS: bool(self._read(STORE_PUSH_NOTIFICATIONS, True)),
            STORE_ADVANCE_SPEED: advance_speed,
            STORE_SESSION_ID: self._read(STORE_SESSION_ID, ""),
            STORE_EVENTS_BYTE_OFFSET: max(0, int(self._read(STORE_EVENTS_BYTE_OFFSET, 0) or 0)),
            STORE_EVENTS_FILE_SIZE: max(0, int(self._read(STORE_EVENTS_FILE_SIZE, 0) or 0)),
            STORE_LAST_SEQ: max(0, int(self._read(STORE_LAST_SEQ, 0) or 0)),
            STORE_DEDUPE_WINDOW: dedupe_window,
            STORE_LAST_ERROR: last_error,
            STORE_OCR_CAPTURE_PROFILES: ocr_capture_profiles,
            STORE_OCR_WINDOW_TARGET: ocr_window_target,
            STORE_MEMORY_READER_TARGET: memory_reader_target,
        }
        if not isinstance(restored[STORE_BOUND_GAME_ID], str):
            warnings.append("invalid bound_game_id dropped: non-string")
            restored[STORE_BOUND_GAME_ID] = ""
        if not isinstance(restored[STORE_SESSION_ID], str):
            warnings.append("invalid session_id dropped: non-string")
            restored[STORE_SESSION_ID] = ""
        return restored, warnings

    def persist_preferences(
        self,
        *,
        bound_game_id: str,
        mode: str,
        push_notifications: bool,
        advance_speed: str = ADVANCE_SPEED_MEDIUM,
    ) -> None:
        self._write(STORE_BOUND_GAME_ID, bound_game_id)
        self._write(STORE_MODE, mode)
        self._write(STORE_PUSH_NOTIFICATIONS, push_notifications)
        self._write(
            STORE_ADVANCE_SPEED,
            advance_speed if advance_speed in ADVANCE_SPEEDS else ADVANCE_SPEED_MEDIUM,
        )

    def persist_runtime(
        self,
        *,
        session_id: str,
        events_byte_offset: int,
        events_file_size: int,
        last_seq: int,
        dedupe_window: list[dict[str, str]],
        last_error: dict[str, Any],
    ) -> None:
        self._write(STORE_SESSION_ID, session_id)
        self._write(STORE_EVENTS_BYTE_OFFSET, max(0, int(events_byte_offset)))
        self._write(STORE_EVENTS_FILE_SIZE, max(0, int(events_file_size)))
        self._write(STORE_LAST_SEQ, max(0, int(last_seq)))
        self._write(STORE_DEDUPE_WINDOW, list(dedupe_window))
        self._write(STORE_LAST_ERROR, dict(last_error))

    def persist_ocr_capture_profiles(
        self,
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        payload, warnings = self._sanitize_ocr_capture_profiles(profiles)
        for warning in warnings:
            self._logger.warning(warning)
        self._write(
            STORE_OCR_CAPTURE_PROFILES,
            payload,
        )

    def persist_ocr_window_target(self, target: dict[str, Any]) -> None:
        payload = dict(target or {})
        self._write(
            STORE_OCR_WINDOW_TARGET,
            {
                "mode": str(payload.get("mode") or "auto"),
                "window_key": str(payload.get("window_key") or ""),
                "process_name": str(payload.get("process_name") or ""),
                "normalized_title": str(payload.get("normalized_title") or ""),
                "pid": max(0, int(payload.get("pid") or 0)),
                "last_known_hwnd": max(0, int(payload.get("last_known_hwnd") or 0)),
                "selected_at": str(payload.get("selected_at") or ""),
            },
        )

    def persist_memory_reader_target(self, target: dict[str, Any]) -> None:
        payload, warnings = self._sanitize_memory_reader_target(target)
        for warning in warnings:
            self._logger.warning(warning)
        self._write(STORE_MEMORY_READER_TARGET, payload)

    def clear_runtime(self) -> None:
        self.persist_runtime(
            session_id="",
            events_byte_offset=0,
            events_file_size=0,
            last_seq=0,
            dedupe_window=[],
            last_error={},
        )
