from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Literal, TypedDict

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    MODE_CONCEPT_EXPLAIN,
    MODE_INTERACTIVE,
    MODE_TEACHING,
    SUPPORTED_LLM_OPERATIONS,
    SUPPORTED_MODES,
)
from .json_utils import json_copy
from .mode_manager import normalize_mode


PLUGIN_ID = "study_companion"
StudyMode = Literal["companion", "interactive", "teaching"]


class ModeIntentPayload(TypedDict, total=False):
    matched: bool
    pure_switch: bool
    kind: str
    mode: StudyMode
    remaining_text: str
    keyword: str
    transition_phrase: str


class ModeSwitchPayload(TypedDict, total=False):
    changed: bool
    old_mode: StudyMode
    new_mode: StudyMode
    reason: str
    transition_phrase: str
    locked: bool
    lock_reason: str
    lock_until: float
    checkpoint: dict[str, Any]


class StudyStatusPayload(TypedDict, total=False):
    status: str
    active_mode: StudyMode
    mode: StudyMode
    current_question: dict[str, Any]
    last_answer_evaluation: dict[str, Any]
    screen_classification: dict[str, Any]
    last_reply: str
    last_error: str
    history: list[dict[str, Any]]


class TutorReplyPayload(TypedDict, total=False):
    question: str
    answer: str
    hint: str
    difficulty: int
    topic: str
    verdict: str
    score: int
    error_type: str
    feedback: str
    next_action: str
    mastery_delta: float
    confidence: float
    weak_points: list[str]
    next_steps: list[str]
    summary: str
    highlights: list[str]
    next_actions: list[str]
    markdown: str

STATUS_READY = "ready"
STATUS_STOPPED = "stopped"
STATUS_ERROR = "error"

STORE_CONFIG = "config"
STORE_STATE = "state"


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
@dataclass(slots=True)
class StudyConfig:
    mode: StudyMode = MODE_COMPANION
    default_mode: StudyMode = MODE_COMPANION
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
    llm_temperature_concept_explain: float = 0.2
    llm_max_tokens_concept_explain: int = 900
    llm_temperature_question_generate: float = 0.35
    llm_max_tokens_question_generate: int = 720
    llm_temperature_answer_evaluate: float = 0.12
    llm_max_tokens_answer_evaluate: int = 720
    llm_temperature_knowledge_track: float = 0.08
    llm_max_tokens_knowledge_track: int = 480
    llm_temperature_summarize_session: float = 0.18
    llm_max_tokens_summarize_session: int = 1200

    def __post_init__(self) -> None:
        self.mode = normalize_mode(self.mode)
        self.default_mode = normalize_mode(self.default_mode or self.mode)
        self.language = str(self.language or "zh-CN").strip() or "zh-CN"
        self.history_limit = max(1, self._coerce_int(self.history_limit, 50))
        self.ocr_install_timeout_seconds = self._clamp_float(self.ocr_install_timeout_seconds, 1.0, 3600.0, 300.0)
        self.ocr_left_inset_ratio = self._clamp_float(self.ocr_left_inset_ratio, 0.0, 1.0, 0.03)
        self.ocr_right_inset_ratio = self._clamp_float(self.ocr_right_inset_ratio, 0.0, 1.0, 0.03)
        self.ocr_top_ratio = self._clamp_float(self.ocr_top_ratio, 0.0, 1.0, 0.0)
        self.ocr_bottom_inset_ratio = self._clamp_float(self.ocr_bottom_inset_ratio, 0.0, 1.0, 0.0)
        self.llm_call_timeout_seconds = self._clamp_float(self.llm_call_timeout_seconds, 1.0, 3600.0, 30.0)
        self.llm_temperature = self._clamp_float(self.llm_temperature, 0.0, 2.0, 0.2)
        self.llm_max_tokens = max(1, self._coerce_int(self.llm_max_tokens, 900))
        self.llm_temperature_concept_explain = self._clamp_float(self.llm_temperature_concept_explain, 0.0, 2.0, 0.2)
        self.llm_max_tokens_concept_explain = max(1, self._coerce_int(self.llm_max_tokens_concept_explain, 900))
        self.llm_temperature_question_generate = self._clamp_float(self.llm_temperature_question_generate, 0.0, 2.0, 0.35)
        self.llm_max_tokens_question_generate = max(1, self._coerce_int(self.llm_max_tokens_question_generate, 720))
        self.llm_temperature_answer_evaluate = self._clamp_float(self.llm_temperature_answer_evaluate, 0.0, 2.0, 0.12)
        self.llm_max_tokens_answer_evaluate = max(1, self._coerce_int(self.llm_max_tokens_answer_evaluate, 720))
        self.llm_temperature_knowledge_track = self._clamp_float(self.llm_temperature_knowledge_track, 0.0, 2.0, 0.08)
        self.llm_max_tokens_knowledge_track = max(1, self._coerce_int(self.llm_max_tokens_knowledge_track, 480))
        self.llm_temperature_summarize_session = self._clamp_float(self.llm_temperature_summarize_session, 0.0, 2.0, 0.18)
        self.llm_max_tokens_summarize_session = max(1, self._coerce_int(self.llm_max_tokens_summarize_session, 1200))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def _coerce_int(value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _clamp_float(value: object, minimum: float, maximum: float, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = default
        if not math.isfinite(number):
            number = default
        return max(minimum, min(maximum, number))

    def llm_limits_for_operation(self, operation: str) -> tuple[float, int]:
        normalized = operation if operation in SUPPORTED_LLM_OPERATIONS else LLM_OPERATION_CONCEPT_EXPLAIN
        if normalized == LLM_OPERATION_QUESTION_GENERATE:
            return self.llm_temperature_question_generate, self.llm_max_tokens_question_generate
        if normalized == LLM_OPERATION_ANSWER_EVALUATE:
            return self.llm_temperature_answer_evaluate, self.llm_max_tokens_answer_evaluate
        if normalized == LLM_OPERATION_KNOWLEDGE_TRACK:
            return self.llm_temperature_knowledge_track, self.llm_max_tokens_knowledge_track
        if normalized == LLM_OPERATION_SUMMARIZE_SESSION:
            return self.llm_temperature_summarize_session, self.llm_max_tokens_summarize_session
        if normalized == LLM_OPERATION_CONCEPT_EXPLAIN:
            return self.llm_temperature_concept_explain, self.llm_max_tokens_concept_explain
        return self.llm_temperature, self.llm_max_tokens


@dataclass(slots=True)
class StudyState:
    status: str = STATUS_STOPPED
    active_mode: str = MODE_COMPANION
    mode_started_at: float = 0.0
    recent_mode_switches: list[dict[str, Any]] = field(default_factory=list)
    suggestion_cooldowns: dict[str, float] = field(default_factory=dict)
    session_suggestions: list[dict[str, Any]] = field(default_factory=list)
    mode_lock_until: float = 0.0
    last_error: str = ""
    last_started_at: str = ""
    last_ocr_text: str = ""
    last_ocr_at: str = ""
    last_screen_classification: dict[str, Any] = field(default_factory=dict)
    recent_screen_classifications: list[dict[str, Any]] = field(default_factory=list)
    current_question: dict[str, Any] = field(default_factory=dict)
    last_answer_evaluation: dict[str, Any] = field(default_factory=dict)
    session_summary_seed: dict[str, Any] = field(default_factory=dict)
    recent_learning_events: list[dict[str, Any]] = field(default_factory=list)
    last_question_at: str = ""
    last_answer_evaluated_at: str = ""
    last_session_summary: str = ""
    last_session_summary_at: str = ""
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
    payload: dict[str, Any] = field(default_factory=dict)
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

    def _float_alias(section: dict[str, Any], keys: tuple[str, ...], default: float, flat_key: str | None = None) -> float:
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

    default_mode = _str(study, "default_mode", _str(study, "mode", MODE_COMPANION, "mode"), "default_mode").strip() or MODE_COMPANION
    default_mode = normalize_mode(default_mode)
    mode = normalize_mode(_str(study, "mode", default_mode, "mode"))
    generic_llm_temperature = _clamp(_float(llm, "temperature", 0.2, "llm_temperature"), 0.0, 2.0, 0.2)
    generic_llm_max_tokens = max(1, _int(llm, "max_tokens", 900, "llm_max_tokens"))

    return StudyConfig(
        mode=mode,
        default_mode=default_mode,
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
            _float_alias(llm, ("call_timeout_seconds", "llm_call_timeout_seconds"), 30.0, "llm_call_timeout_seconds"),
            1.0,
            3600.0,
            30.0,
        ),
        llm_temperature=generic_llm_temperature,
        llm_max_tokens=generic_llm_max_tokens,
        llm_temperature_concept_explain=_clamp(
            _float_alias(llm, ("temperature_concept_explain", "llm_temperature_concept_explain"), generic_llm_temperature, "llm_temperature_concept_explain"),
            0.0,
            2.0,
            generic_llm_temperature,
        ),
        llm_max_tokens_concept_explain=max(
            1,
            _int(llm, "max_tokens_concept_explain", generic_llm_max_tokens, "llm_max_tokens_concept_explain"),
        ),
        llm_temperature_question_generate=_clamp(
            _float_alias(llm, ("temperature_question_generate", "llm_temperature_question_generate"), generic_llm_temperature, "llm_temperature_question_generate"),
            0.0,
            2.0,
            generic_llm_temperature,
        ),
        llm_max_tokens_question_generate=max(
            1,
            _int(llm, "max_tokens_question_generate", generic_llm_max_tokens, "llm_max_tokens_question_generate"),
        ),
        llm_temperature_answer_evaluate=_clamp(
            _float_alias(llm, ("temperature_answer_evaluate", "llm_temperature_answer_evaluate"), generic_llm_temperature, "llm_temperature_answer_evaluate"),
            0.0,
            2.0,
            generic_llm_temperature,
        ),
        llm_max_tokens_answer_evaluate=max(
            1,
            _int(llm, "max_tokens_answer_evaluate", generic_llm_max_tokens, "llm_max_tokens_answer_evaluate"),
        ),
        llm_temperature_knowledge_track=_clamp(
            _float_alias(llm, ("temperature_knowledge_track", "llm_temperature_knowledge_track"), generic_llm_temperature, "llm_temperature_knowledge_track"),
            0.0,
            2.0,
            generic_llm_temperature,
        ),
        llm_max_tokens_knowledge_track=max(
            1,
            _int(llm, "max_tokens_knowledge_track", generic_llm_max_tokens, "llm_max_tokens_knowledge_track"),
        ),
        llm_temperature_summarize_session=_clamp(
            _float_alias(llm, ("temperature_summarize_session", "llm_temperature_summarize_session"), generic_llm_temperature, "llm_temperature_summarize_session"),
            0.0,
            2.0,
            generic_llm_temperature,
        ),
        llm_max_tokens_summarize_session=max(
            1,
            _int(llm, "max_tokens_summarize_session", generic_llm_max_tokens, "llm_max_tokens_summarize_session"),
        ),
    )
