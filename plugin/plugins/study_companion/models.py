from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any


PLUGIN_ID = "study_companion"

MODE_CONCEPT_EXPLAIN = "concept_explain"
SUPPORTED_MODES = frozenset({MODE_CONCEPT_EXPLAIN})

STATUS_READY = "ready"
STATUS_STOPPED = "stopped"
STATUS_ERROR = "error"

STORE_CONFIG = "config"
STORE_STATE = "state"


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    if isinstance(value, tuple):
        return [json_copy(item) for item in value]
    return value


@dataclass(slots=True)
class StudyConfig:
    mode: str = MODE_CONCEPT_EXPLAIN
    language: str = "zh-CN"
    history_limit: int = 50
    ocr_enabled: bool = True
    ocr_backend_selection: str = "rapidocr"
    ocr_capture_backend: str = "auto"
    ocr_tesseract_path: str = ""
    ocr_install_manifest_url: str = ""
    ocr_install_target_dir: str = ""
    ocr_install_timeout_seconds: float = 300.0
    ocr_languages: str = "chi_sim+jpn+eng"
    ocr_left_inset_ratio: float = 0.03
    ocr_right_inset_ratio: float = 0.03
    ocr_top_ratio: float = 0.0
    ocr_bottom_inset_ratio: float = 0.0
    rapidocr_install_target_dir: str = ""
    rapidocr_engine_type: str = "onnxruntime"
    rapidocr_lang_type: str = "ch"
    rapidocr_model_type: str = "mobile"
    rapidocr_ocr_version: str = "PP-OCRv4"
    llm_call_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 900

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StudyState:
    status: str = STATUS_STOPPED
    active_mode: str = MODE_CONCEPT_EXPLAIN
    last_error: str = ""
    last_started_at: str = ""
    last_ocr_text: str = ""
    last_ocr_at: str = ""
    last_reply: str = ""
    last_reply_at: str = ""
    checkpoint: dict[str, Any] = field(default_factory=dict)
    dependency_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OcrSnapshot:
    text: str = ""
    boxes: list[dict[str, Any]] = field(default_factory=list)
    status: str = "empty"
    backend: str = ""
    captured_at: str = ""
    diagnostic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TutorReply:
    operation: str
    input_text: str
    reply: str
    degraded: bool = False
    diagnostic: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["created_at"]:
            payload["created_at"] = utc_now_iso()
        return payload


def build_config(raw: dict[str, Any]) -> StudyConfig:
    study = raw.get("study") if isinstance(raw.get("study"), dict) else {}
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    ocr = raw.get("ocr_reader") if isinstance(raw.get("ocr_reader"), dict) else {}
    rapidocr = raw.get("rapidocr") if isinstance(raw.get("rapidocr"), dict) else {}

    def _raw(section: dict[str, Any], key: str, default: Any, flat_key: str | None = None) -> Any:
        if key in section:
            return section.get(key, default)
        if flat_key and flat_key in raw:
            return raw.get(flat_key, default)
        return default

    def _str(section: dict[str, Any], key: str, default: str, flat_key: str | None = None) -> str:
        return str(_raw(section, key, default, flat_key) or default)

    def _bool(section: dict[str, Any], key: str, default: bool, flat_key: str | None = None) -> bool:
        value = _raw(section, key, default, flat_key)
        return value if isinstance(value, bool) else default

    def _int(section: dict[str, Any], key: str, default: int, flat_key: str | None = None) -> int:
        try:
            return int(_raw(section, key, default, flat_key))
        except (TypeError, ValueError):
            return default

    def _float(section: dict[str, Any], key: str, default: float, flat_key: str | None = None) -> float:
        try:
            return float(_raw(section, key, default, flat_key))
        except (TypeError, ValueError):
            return default

    def _clamp(value: float, minimum: float, maximum: float, default: float) -> float:
        if not math.isfinite(value):
            value = default
        return max(minimum, min(maximum, value))

    mode = _str(study, "default_mode", MODE_CONCEPT_EXPLAIN, "mode").strip() or MODE_CONCEPT_EXPLAIN
    if mode not in SUPPORTED_MODES:
        mode = MODE_CONCEPT_EXPLAIN

    return StudyConfig(
        mode=mode,
        language=_str(study, "language", "zh-CN", "language"),
        history_limit=max(1, _int(study, "history_limit", 50, "history_limit")),
        ocr_enabled=_bool(ocr, "enabled", True, "ocr_enabled"),
        ocr_backend_selection=_str(ocr, "backend_selection", "rapidocr", "ocr_backend_selection"),
        ocr_capture_backend=_str(ocr, "capture_backend", "auto", "ocr_capture_backend"),
        ocr_tesseract_path=_str(ocr, "tesseract_path", "", "ocr_tesseract_path"),
        ocr_install_manifest_url=_str(ocr, "install_manifest_url", "", "ocr_install_manifest_url"),
        ocr_install_target_dir=_str(ocr, "install_target_dir", "", "ocr_install_target_dir"),
        ocr_install_timeout_seconds=_clamp(
            _float(ocr, "install_timeout_seconds", 300.0, "ocr_install_timeout_seconds"),
            1.0,
            3600.0,
            300.0,
        ),
        ocr_languages=_str(ocr, "languages", "chi_sim+jpn+eng", "ocr_languages"),
        ocr_left_inset_ratio=_clamp(_float(ocr, "left_inset_ratio", 0.03, "ocr_left_inset_ratio"), 0.0, 1.0, 0.03),
        ocr_right_inset_ratio=_clamp(_float(ocr, "right_inset_ratio", 0.03, "ocr_right_inset_ratio"), 0.0, 1.0, 0.03),
        ocr_top_ratio=_clamp(_float(ocr, "top_ratio", 0.0, "ocr_top_ratio"), 0.0, 1.0, 0.0),
        ocr_bottom_inset_ratio=_clamp(_float(ocr, "bottom_inset_ratio", 0.0, "ocr_bottom_inset_ratio"), 0.0, 1.0, 0.0),
        rapidocr_install_target_dir=_str(rapidocr, "install_target_dir", "", "rapidocr_install_target_dir"),
        rapidocr_engine_type=_str(rapidocr, "engine_type", "onnxruntime", "rapidocr_engine_type"),
        rapidocr_lang_type=_str(rapidocr, "lang_type", "ch", "rapidocr_lang_type"),
        rapidocr_model_type=_str(rapidocr, "model_type", "mobile", "rapidocr_model_type"),
        rapidocr_ocr_version=_str(rapidocr, "ocr_version", "PP-OCRv4", "rapidocr_ocr_version"),
        llm_call_timeout_seconds=_clamp(
            _float(llm, "llm_call_timeout_seconds", 30.0, "llm_call_timeout_seconds"),
            1.0,
            3600.0,
            30.0,
        ),
        llm_temperature=_clamp(_float(llm, "temperature", 0.2, "llm_temperature"), 0.0, 2.0, 0.2),
        llm_max_tokens=max(1, _int(llm, "max_tokens", 900, "llm_max_tokens")),
    )
