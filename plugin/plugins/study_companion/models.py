from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import math
from typing import Any, Literal, TypedDict

from .constants import (
    MODE_COMPANION,
    MODE_CONCEPT_EXPLAIN,
    MODE_INTERACTIVE,
    MODE_TEACHING,
    SUPPORTED_MODES,
)
from .json_utils import json_copy
from .mode_manager import normalize_mode


PLUGIN_ID = "study_companion"
StudyMode = Literal["companion", "interactive", "teaching"]
STUDY_EXPORT_FORMATS = ("markdown", "pdf", "docx", "xmind")
STUDY_EXPORT_STYLES = ("neko", "academic", "compact")
_LOGGER = logging.getLogger(__name__)
OCR_SNIPPET_MAX_CHARS = 200
PRIVATE_CURRENT_QUESTION_FIELDS = frozenset(
    {
        "answer",
        "reference_answer",
        "accepted_answers",
        "key_points",
        "rubric",
        "solution_steps",
        "math_equivalence_engine",
        "internal_private_payload",
        "current_question_private",
        "attempt_evaluated",
    }
)


def public_current_question_payload(
    value: dict[str, Any] | None,
    *,
    include_reference_answer: bool = False,
) -> dict[str, Any]:
    payload = json_copy(value or {})
    if not isinstance(payload, dict):
        return {}
    private_fields = set(PRIVATE_CURRENT_QUESTION_FIELDS)
    if include_reference_answer:
        private_fields.discard("reference_answer")
    for field_name in private_fields:
        payload.pop(field_name, None)
    payload.pop("answer_evaluation_cache", None)
    return payload


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


class ActivitySummary(TypedDict):
    current_app: str
    current_activity: str
    app_duration_seconds: float
    recent_apps: list[str]
    total_focus_minutes: float
    ocr_text_snippet: str
    app_distribution: dict[str, float]


@dataclass(frozen=True, slots=True)
class NotebookMeta:
    id: str
    name: str
    description: str = ""
    sort_order: int = 0
    created_at: str = ""
    updated_at: str = ""
    note_count: int = 0


@dataclass(frozen=True, slots=True)
class NoteItem:
    id: str
    notebook_id: str | None
    title: str
    content: str
    content_plain: str
    snippet: str
    is_ai_generated: bool = False
    source_type: str = "manual"
    source_ref: str = ""
    topic_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    edited_at: str = ""


class NoteSearchResult(TypedDict, total=False):
    notes: list[dict[str, Any]]
    topics: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    wrong_questions: list[dict[str, Any]]
    query: str


STATUS_READY = "ready"
STATUS_STOPPED = "stopped"
STATUS_ERROR = "error"

STORE_CONFIG = "config"
STORE_STATE = "state"


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _range_or_default(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if minimum <= number <= maximum else default


def _clamp_int_or_default(
    value: object,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, number))


@dataclass(slots=True)
class DocExportConfig:
    enabled: bool = False
    pdf_backend: str = "reportlab"
    default_style: str = "neko"
    xmind_enabled: bool = False

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.pdf_backend = str(self.pdf_backend or "reportlab").strip() or "reportlab"
        style = str(self.default_style or "neko").strip().lower() or "neko"
        self.default_style = style if style in STUDY_EXPORT_STYLES else "neko"
        self.xmind_enabled = bool(self.xmind_enabled)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PomodoroConfig:
    focus_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 15
    long_break_interval: int = 4
    allow_skip_break: bool = True
    allow_custom_duration: bool = True

    def __post_init__(self) -> None:
        self.focus_minutes = _range_or_default(self.focus_minutes, 1, 120, 25)
        self.short_break_minutes = _range_or_default(self.short_break_minutes, 1, 30, 5)
        self.long_break_minutes = _range_or_default(self.long_break_minutes, 1, 60, 15)
        self.long_break_interval = _range_or_default(self.long_break_interval, 1, 10, 4)
        self.allow_skip_break = bool(self.allow_skip_break)
        self.allow_custom_duration = bool(self.allow_custom_duration)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SupervisionConfig:
    enabled: bool = True
    remind_interval_minutes: int = 10
    inactivity_timeout_minutes: int = 5
    idle_away_seconds: int = 900
    allow_disable_by_chat: bool = True

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.remind_interval_minutes = _range_or_default(
            self.remind_interval_minutes, 1, 60, 10
        )
        self.inactivity_timeout_minutes = _range_or_default(
            self.inactivity_timeout_minutes, 1, 30, 5
        )
        self.idle_away_seconds = _range_or_default(
            self.idle_away_seconds, 60, 3600, 900
        )
        self.allow_disable_by_chat = bool(self.allow_disable_by_chat)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CheckinConfig:
    streak_timezone: str = "local"
    makeup_window_days: int = 3
    auto_derive_from_session: bool = True

    def __post_init__(self) -> None:
        self.streak_timezone = str(self.streak_timezone or "local").strip() or "local"
        self.makeup_window_days = _range_or_default(self.makeup_window_days, 0, 7, 3)
        self.auto_derive_from_session = bool(self.auto_derive_from_session)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CommunicationConfig:
    enabled: bool = True

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AwarenessConfig:
    enabled: bool = False
    snapshot_interval_seconds: int = 5
    context_window_minutes: int = 5
    classify_mode: str = "title_first"
    image_max_bytes: int = 65_536
    push_to_llm_interval_seconds: int = 300
    push_to_llm_mode: str = "read"
    os_signals_enabled: bool = True
    distraction_detection: bool = True

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.snapshot_interval_seconds = _clamp_int_or_default(
            self.snapshot_interval_seconds, 1, 60, 5
        )
        self.context_window_minutes = _clamp_int_or_default(
            self.context_window_minutes, 1, 60, 5
        )
        classify_mode = str(self.classify_mode or "title_first").strip()
        if classify_mode not in {"title_first", "ocr_text", "both"}:
            _LOGGER.warning(
                "AwarenessConfig: invalid classify_mode=%r, falling back to 'title_first'",
                classify_mode,
            )
            classify_mode = "title_first"
        self.classify_mode = classify_mode
        self.image_max_bytes = _clamp_int_or_default(
            self.image_max_bytes, 10240, 512_000, 65_536
        )
        self.push_to_llm_interval_seconds = _clamp_int_or_default(
            self.push_to_llm_interval_seconds, 30, 300, 300
        )
        push_mode = str(self.push_to_llm_mode or "read").strip()
        if push_mode not in {"read", "blind", "respond"}:
            _LOGGER.warning(
                "AwarenessConfig: invalid push_to_llm_mode=%r, falling back to 'read'",
                push_mode,
            )
            push_mode = "read"
        self.push_to_llm_mode = push_mode
        self.os_signals_enabled = bool(self.os_signals_enabled)
        self.distraction_detection = bool(self.distraction_detection)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StudyConfig:
    mode: StudyMode = MODE_COMPANION
    default_mode: StudyMode = MODE_COMPANION
    language: str = "zh-CN"
    history_limit: int = 50
    auto_open_ui: bool = True
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
    llm_vision_enabled: bool = False
    llm_vision_max_image_px: int = 768
    fsrs_retention_target: float = 0.90
    fsrs_auto_optimize_interval_days: int = 30
    knowledge_contribution_opt_in: bool = False
    knowledge_contribution_min_sample_count: int = 3
    doc_export: DocExportConfig = field(default_factory=DocExportConfig)
    pomodoro: PomodoroConfig = field(default_factory=PomodoroConfig)
    supervision: SupervisionConfig = field(default_factory=SupervisionConfig)
    checkin: CheckinConfig = field(default_factory=CheckinConfig)
    communication: CommunicationConfig = field(default_factory=CommunicationConfig)
    awareness: AwarenessConfig = field(default_factory=AwarenessConfig)

    def __post_init__(self) -> None:
        self.mode = normalize_mode(self.mode)
        self.default_mode = normalize_mode(self.default_mode or self.mode)
        self.language = str(self.language or "zh-CN").strip() or "zh-CN"
        self.history_limit = max(1, self._coerce_int(self.history_limit, 50))
        self.auto_open_ui = bool(self.auto_open_ui)
        self.ocr_install_timeout_seconds = self._clamp_float(
            self.ocr_install_timeout_seconds, 1.0, 3600.0, 300.0
        )
        self.ocr_left_inset_ratio = self._clamp_float(
            self.ocr_left_inset_ratio, 0.0, 1.0, 0.03
        )
        self.ocr_right_inset_ratio = self._clamp_float(
            self.ocr_right_inset_ratio, 0.0, 1.0, 0.03
        )
        self.ocr_top_ratio = self._clamp_float(self.ocr_top_ratio, 0.0, 1.0, 0.0)
        self.ocr_bottom_inset_ratio = self._clamp_float(
            self.ocr_bottom_inset_ratio, 0.0, 1.0, 0.0
        )
        self.llm_call_timeout_seconds = self._clamp_float(
            self.llm_call_timeout_seconds, 1.0, 3600.0, 30.0
        )
        self.llm_vision_enabled = bool(self.llm_vision_enabled)
        self.llm_vision_max_image_px = max(
            64, min(4096, self._coerce_int(self.llm_vision_max_image_px, 768))
        )
        self.fsrs_retention_target = self._clamp_float(
            self.fsrs_retention_target, 0.1, 0.99, 0.90
        )
        self.fsrs_auto_optimize_interval_days = max(
            1, self._coerce_int(self.fsrs_auto_optimize_interval_days, 30)
        )
        self.knowledge_contribution_opt_in = bool(self.knowledge_contribution_opt_in)
        self.knowledge_contribution_min_sample_count = max(
            1,
            self._coerce_int(self.knowledge_contribution_min_sample_count, 3),
        )
        if not isinstance(self.doc_export, DocExportConfig):
            self.doc_export = (
                DocExportConfig(**self.doc_export)
                if isinstance(self.doc_export, dict)
                else DocExportConfig()
            )
        if not isinstance(self.pomodoro, PomodoroConfig):
            self.pomodoro = (
                PomodoroConfig(**self.pomodoro)
                if isinstance(self.pomodoro, dict)
                else PomodoroConfig()
            )
        if not isinstance(self.supervision, SupervisionConfig):
            self.supervision = (
                SupervisionConfig(**self.supervision)
                if isinstance(self.supervision, dict)
                else SupervisionConfig()
            )
        if not isinstance(self.checkin, CheckinConfig):
            self.checkin = (
                CheckinConfig(**self.checkin)
                if isinstance(self.checkin, dict)
                else CheckinConfig()
            )
        if not isinstance(self.communication, CommunicationConfig):
            self.communication = (
                CommunicationConfig(**self.communication)
                if isinstance(self.communication, dict)
                else CommunicationConfig()
            )
        if not isinstance(self.awareness, AwarenessConfig):
            self.awareness = (
                AwarenessConfig(**self.awareness)
                if isinstance(self.awareness, dict)
                else AwarenessConfig()
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def _coerce_int(value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _clamp_float(
        value: object, minimum: float, maximum: float, default: float
    ) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = default
        if not math.isfinite(number):
            number = default
        return max(minimum, min(maximum, number))


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
    last_vision_image_base64: str = ""
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
    checkpoint: dict[str, Any] = field(default_factory=dict)
    dependency_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("last_vision_image_base64", None)
        return payload


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


@dataclass(slots=True, frozen=True)
class ActivitySnapshot:
    timestamp: float
    first_seen_at: float
    app_type: str
    activity_type: str
    classify_method: str
    ocr_text_snippet: str
    window_title: str
    has_content_change: bool
    _thumbnail_hash: str = ""


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
        auto_open_ui=_bool(study, "auto_open_ui", True, "auto_open_ui"),
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
        ),
    )
