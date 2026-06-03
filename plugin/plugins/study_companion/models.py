from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    recent_notes: list[dict[str, Any]]


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


def _clamp_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


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
    idle_warning_minutes: int = 5

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.snapshot_interval_seconds = _clamp_int(
            self.snapshot_interval_seconds, 1, 60, 5
        )
        self.context_window_minutes = _clamp_int(
            self.context_window_minutes, 1, 60, 5
        )
        mode = str(self.classify_mode or "title_first").strip().lower()
        self.classify_mode = (
            mode if mode in {"title_first", "ocr_text", "both"} else "title_first"
        )
        self.image_max_bytes = _clamp_int(self.image_max_bytes, 10_240, 512_000, 65_536)
        self.push_to_llm_interval_seconds = _clamp_int(
            self.push_to_llm_interval_seconds, 30, 300, 300
        )
        push_mode = str(self.push_to_llm_mode or "read").strip().lower()
        self.push_to_llm_mode = (
            push_mode if push_mode in {"blind", "read", "respond"} else "read"
        )
        self.os_signals_enabled = bool(self.os_signals_enabled)
        self.distraction_detection = bool(self.distraction_detection)
        self.idle_warning_minutes = _clamp_int(
            self.idle_warning_minutes, 1, 30, 5
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActivitySummary(TypedDict):
    current_app: str
    current_activity: str
    app_duration_seconds: float
    recent_apps: list[str]
    total_focus_minutes: float
    ocr_text_snippet: str
    app_distribution: dict[str, float]


@dataclass(slots=True)
class ActivitySnapshot:
    timestamp: float = 0.0
    first_seen_at: float = 0.0
    app_type: str = "other"
    activity_type: str = ""
    classify_method: str = "title"
    ocr_text_snippet: str = ""
    window_title: str = ""

    def __post_init__(self) -> None:
        self.timestamp = float(self.timestamp or 0.0)
        self.first_seen_at = float(self.first_seen_at or 0.0)
        self.app_type = str(self.app_type or "other").strip() or "other"
        self.activity_type = str(self.activity_type or "").strip()
        self.classify_method = str(self.classify_method or "title").strip() or "title"
        self.ocr_text_snippet = str(self.ocr_text_snippet or "").strip()
        self.window_title = str(self.window_title or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    last_reply: str = ""
    last_reply_at: str = ""
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
    from .config_loader import build_config as _build_config

    return _build_config(raw)
