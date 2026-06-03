from __future__ import annotations

import math
from typing import Any

from .constants import MODE_COMPANION
from .mode_manager import normalize_mode
from .models import (
    AwarenessConfig,
    CheckinConfig,
    CommunicationConfig,
    DocExportConfig,
    PomodoroConfig,
    StudyConfig,
    SupervisionConfig,
)

def build_config(raw: dict[str, Any]) -> StudyConfig:
    study = raw.get("study") if isinstance(raw.get("study"), dict) else {}
    study_companion = (
        raw.get("study_companion")
        if isinstance(raw.get("study_companion"), dict)
        else {}
    )
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    ocr = raw.get("ocr_reader") if isinstance(raw.get("ocr_reader"), dict) else {}
    rapidocr = raw.get("rapidocr") if isinstance(raw.get("rapidocr"), dict) else {}
    fsrs = raw.get("fsrs") if isinstance(raw.get("fsrs"), dict) else {}
    contribution = (
        raw.get("knowledge_contribution")
        if isinstance(raw.get("knowledge_contribution"), dict)
        else {}
    )
    doc_export = (
        raw.get("doc_export") if isinstance(raw.get("doc_export"), dict) else {}
    )
    pomodoro = study.get("pomodoro") if isinstance(study.get("pomodoro"), dict) else {}
    supervision = (
        study.get("supervision") if isinstance(study.get("supervision"), dict) else {}
    )
    checkin = study.get("checkin") if isinstance(study.get("checkin"), dict) else {}
    awareness = (
        study.get("awareness")
        if isinstance(study.get("awareness"), dict)
        else study_companion.get("awareness")
        if isinstance(study_companion.get("awareness"), dict)
        else raw.get("awareness")
        if isinstance(raw.get("awareness"), dict)
        else {}
    )
    communication = (
        study_companion.get("communication")
        if isinstance(study_companion.get("communication"), dict)
        else raw.get("communication")
        if isinstance(raw.get("communication"), dict)
        else {}
    )

    def _raw(
        section: dict[str, Any], key: str, default: Any, flat_key: str | None = None
    ) -> Any:
        if key in section:
            return section.get(key, default)
        if flat_key and flat_key in raw:
            return raw.get(flat_key, default)
        return default

    def _str(
        section: dict[str, Any], key: str, default: str, flat_key: str | None = None
    ) -> str:
        return str(_raw(section, key, default, flat_key) or default)

    def _bool(
        section: dict[str, Any], key: str, default: bool, flat_key: str | None = None
    ) -> bool:
        value = _raw(section, key, default, flat_key)
        return value if isinstance(value, bool) else default

    def _int(
        section: dict[str, Any], key: str, default: int, flat_key: str | None = None
    ) -> int:
        try:
            return int(_raw(section, key, default, flat_key))
        except (TypeError, ValueError):
            return default

    def _float(
        section: dict[str, Any], key: str, default: float, flat_key: str | None = None
    ) -> float:
        try:
            return float(_raw(section, key, default, flat_key))
        except (TypeError, ValueError):
            return default

    def _float_alias(
        section: dict[str, Any],
        keys: tuple[str, ...],
        default: float,
        flat_key: str | None = None,
    ) -> float:
        for key in keys:
            if key in section:
                try:
                    return float(section.get(key, default))
                except (TypeError, ValueError):
                    return default
        if flat_key and flat_key in raw:
            try:
                return float(raw.get(flat_key, default))
            except (TypeError, ValueError):
                return default
        return default

    def _clamp(value: float, minimum: float, maximum: float, default: float) -> float:
        if not math.isfinite(value):
            value = default
        return max(minimum, min(maximum, value))

    default_mode = (
        _str(
            study,
            "default_mode",
            _str(study, "mode", MODE_COMPANION, "mode"),
            "default_mode",
        ).strip()
        or MODE_COMPANION
    )
    default_mode = normalize_mode(default_mode)
    mode = normalize_mode(_str(study, "mode", default_mode, "mode"))

    return StudyConfig(
        mode=mode,
        default_mode=default_mode,
        language=_str(study, "language", "zh-CN", "language"),
        history_limit=max(1, _int(study, "history_limit", 50, "history_limit")),
        ocr_enabled=_bool(ocr, "enabled", True, "ocr_enabled"),
        ocr_backend_selection=_str(
            ocr, "backend_selection", "rapidocr", "ocr_backend_selection"
        ),
        ocr_capture_backend=_str(ocr, "capture_backend", "auto", "ocr_capture_backend"),
        ocr_tesseract_path=_str(ocr, "tesseract_path", "", "ocr_tesseract_path"),
        ocr_install_manifest_url=_str(
            ocr, "install_manifest_url", "", "ocr_install_manifest_url"
        ),
        ocr_install_target_dir=_str(
            ocr, "install_target_dir", "", "ocr_install_target_dir"
        ),
        ocr_install_timeout_seconds=_clamp(
            _float(
                ocr, "install_timeout_seconds", 300.0, "ocr_install_timeout_seconds"
            ),
            1.0,
            3600.0,
            300.0,
        ),
        ocr_languages=_str(ocr, "languages", "chi_sim+jpn+eng", "ocr_languages"),
        ocr_left_inset_ratio=_clamp(
            _float(ocr, "left_inset_ratio", 0.03, "ocr_left_inset_ratio"),
            0.0,
            1.0,
            0.03,
        ),
        ocr_right_inset_ratio=_clamp(
            _float(ocr, "right_inset_ratio", 0.03, "ocr_right_inset_ratio"),
            0.0,
            1.0,
            0.03,
        ),
        ocr_top_ratio=_clamp(
            _float(ocr, "top_ratio", 0.0, "ocr_top_ratio"), 0.0, 1.0, 0.0
        ),
        ocr_bottom_inset_ratio=_clamp(
            _float(ocr, "bottom_inset_ratio", 0.0, "ocr_bottom_inset_ratio"),
            0.0,
            1.0,
            0.0,
        ),
        rapidocr_install_target_dir=_str(
            rapidocr, "install_target_dir", "", "rapidocr_install_target_dir"
        ),
        rapidocr_engine_type=_str(
            rapidocr, "engine_type", "onnxruntime", "rapidocr_engine_type"
        ),
        rapidocr_lang_type=_str(rapidocr, "lang_type", "ch", "rapidocr_lang_type"),
        rapidocr_model_type=_str(
            rapidocr, "model_type", "mobile", "rapidocr_model_type"
        ),
        rapidocr_ocr_version=_str(
            rapidocr, "ocr_version", "PP-OCRv4", "rapidocr_ocr_version"
        ),
        llm_call_timeout_seconds=_clamp(
            _float_alias(
                llm,
                ("call_timeout_seconds", "llm_call_timeout_seconds"),
                30.0,
                "llm_call_timeout_seconds",
            ),
            1.0,
            3600.0,
            30.0,
        ),
        llm_vision_enabled=_bool(
            llm, "llm_vision_enabled", False, "llm_vision_enabled"
        ),
        llm_vision_max_image_px=max(
            64,
            min(
                4096,
                _int(llm, "llm_vision_max_image_px", 768, "llm_vision_max_image_px"),
            ),
        ),
        fsrs_retention_target=_clamp(
            _float(fsrs, "retention_target", 0.90, "fsrs_retention_target"),
            0.1,
            0.99,
            0.90,
        ),
        fsrs_auto_optimize_interval_days=max(
            1,
            _int(
                fsrs,
                "auto_optimize_interval_days",
                30,
                "fsrs_auto_optimize_interval_days",
            ),
        ),
        knowledge_contribution_opt_in=_bool(
            contribution,
            "opt_in",
            False,
            "knowledge_contribution_opt_in",
        ),
        knowledge_contribution_min_sample_count=max(
            1,
            _int(
                contribution,
                "min_sample_count",
                3,
                "knowledge_contribution_min_sample_count",
            ),
        ),
        doc_export=DocExportConfig(
            enabled=_bool(doc_export, "enabled", False, "doc_export_enabled"),
            pdf_backend=_str(
                doc_export, "pdf_backend", "reportlab", "doc_export_pdf_backend"
            ),
            default_style=_str(
                doc_export, "default_style", "neko", "doc_export_default_style"
            ),
            xmind_enabled=_bool(
                doc_export, "xmind_enabled", False, "doc_export_xmind_enabled"
            ),
        ),
        pomodoro=PomodoroConfig(
            focus_minutes=_int(pomodoro, "focus_minutes", 25, "pomodoro_focus_minutes"),
            short_break_minutes=_int(
                pomodoro, "short_break_minutes", 5, "pomodoro_short_break_minutes"
            ),
            long_break_minutes=_int(
                pomodoro, "long_break_minutes", 15, "pomodoro_long_break_minutes"
            ),
            long_break_interval=_int(
                pomodoro, "long_break_interval", 4, "pomodoro_long_break_interval"
            ),
            allow_skip_break=_bool(
                pomodoro, "allow_skip_break", True, "pomodoro_allow_skip_break"
            ),
            allow_custom_duration=_bool(
                pomodoro,
                "allow_custom_duration",
                True,
                "pomodoro_allow_custom_duration",
            ),
        ),
        supervision=SupervisionConfig(
            enabled=_bool(supervision, "enabled", True, "supervision_enabled"),
            remind_interval_minutes=_int(
                supervision,
                "remind_interval_minutes",
                10,
                "supervision_remind_interval_minutes",
            ),
            inactivity_timeout_minutes=_int(
                supervision,
                "inactivity_timeout_minutes",
                5,
                "supervision_inactivity_timeout_minutes",
            ),
            idle_away_seconds=_int(
                supervision,
                "idle_away_seconds",
                900,
                "supervision_idle_away_seconds",
            ),
            allow_disable_by_chat=_bool(
                supervision,
                "allow_disable_by_chat",
                True,
                "supervision_allow_disable_by_chat",
            ),
        ),
        checkin=CheckinConfig(
            streak_timezone=_str(
                checkin, "streak_timezone", "local", "checkin_streak_timezone"
            ),
            makeup_window_days=_int(
                checkin, "makeup_window_days", 3, "checkin_makeup_window_days"
            ),
            auto_derive_from_session=_bool(
                checkin,
                "auto_derive_from_session",
                True,
                "checkin_auto_derive_from_session",
            ),
        ),
        communication=CommunicationConfig(
            enabled=_bool(
                communication,
                "enabled",
                True,
                "communication_enabled",
            ),
        ),
        awareness=AwarenessConfig(
            enabled=_bool(awareness, "enabled", False, "awareness_enabled"),
            snapshot_interval_seconds=_int(
                awareness,
                "snapshot_interval_seconds",
                5,
                "awareness_snapshot_interval_seconds",
            ),
            context_window_minutes=_int(
                awareness,
                "context_window_minutes",
                5,
                "awareness_context_window_minutes",
            ),
            classify_mode=_str(
                awareness,
                "classify_mode",
                "title_first",
                "awareness_classify_mode",
            ),
            image_max_bytes=_int(
                awareness,
                "image_max_bytes",
                65_536,
                "awareness_image_max_bytes",
            ),
            push_to_llm_interval_seconds=_int(
                awareness,
                "push_to_llm_interval_seconds",
                300,
                "awareness_push_to_llm_interval_seconds",
            ),
            push_to_llm_mode=_str(
                awareness,
                "push_to_llm_mode",
                "read",
                "awareness_push_to_llm_mode",
            ),
            os_signals_enabled=_bool(
                awareness,
                "os_signals_enabled",
                True,
                "awareness_os_signals_enabled",
            ),
            distraction_detection=_bool(
                awareness,
                "distraction_detection",
                True,
                "awareness_distraction_detection",
            ),
            idle_warning_minutes=_int(
                awareness,
                "idle_warning_minutes",
                5,
                "awareness_idle_warning_minutes",
            ),
        ),
    )
