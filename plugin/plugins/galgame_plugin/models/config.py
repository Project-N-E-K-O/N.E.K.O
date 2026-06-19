from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from .constants import (
    DEFAULT_VISION_CLASSIFIER_MODEL_DIR,
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
    MODE_COMPANION,
    OCR_TRIGGER_MODE_INTERVAL,
    READER_MODE_AUTO,
)


class _ConfigFieldProxy:
    def __init__(self, group_name: str, field_name: str) -> None:
        self._group_name = group_name
        self._field_name = field_name

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self
        return getattr(getattr(instance, self._group_name), self._field_name)

    def __set__(self, instance: Any, value: Any) -> None:
        setattr(getattr(instance, self._group_name), self._field_name, value)


@dataclass(slots=True)
class GalgameBridgeConfig:
    bridge_root: Path = Path()
    active_poll_interval_seconds: float = 1.0
    idle_poll_interval_seconds: float = 3.0
    stale_after_seconds: float = 15.0
    default_mode: str = MODE_COMPANION
    push_notifications: bool = True
    scene_change_cooldown_seconds: float = 15.0
    scene_push_half_threshold: int = 4
    scene_push_time_fallback_seconds: float = 120.0
    scene_merge_total_threshold: int = 12
    auto_open_ui: bool = False


@dataclass(slots=True)
class GalgameHistoryConfig:
    history_events_limit: int = 500
    history_lines_limit: int = 200
    history_choices_limit: int = 50
    dedupe_window_limit: int = 64
    warmup_replay_bytes_limit: int = 65536
    warmup_replay_events_limit: int = 50


@dataclass(slots=True)
class GalgameLLMConfig:
    llm_call_timeout_seconds: float = 15.0
    llm_max_in_flight: int = 2
    llm_request_cache_ttl_seconds: float = 2.0
    llm_explain_cache_ttl_seconds: float = 8.0
    llm_scene_summary_cache_ttl_seconds: float = 10.0
    llm_choice_cache_ttl_seconds: float = 4.0
    llm_near_match_cache_enabled: bool = False
    llm_near_match_cache_ttl_seconds: float = 15.0
    llm_target_entry_ref: str = ""
    llm_vision_enabled: bool = False
    llm_vision_max_image_px: int = 768
    llm_max_tokens_agent_reply: int = 900
    llm_max_tokens_default: int = 1200
    context_max_tokens: int = 6000
    context_metrics_enabled: bool = False
    context_counting_mode: str = "token"
    context_semantic_compression: bool = False
    context_explain_min_lines: int = 4
    context_explain_max_lines: int = 16
    context_window_target_tokens: int = 800
    context_scene_summary_mode: str = "rolling"
    context_cumulative_llm_trigger_lines: int = 30
    context_line_importance_enabled: bool = False
    context_persist_enabled: bool = False
    context_persist_max_age_seconds: float = 3600.0
    context_persist_require_game_id: bool = True
    llm_repeat_detection_enabled: bool = False
    llm_repeat_similarity_threshold: float = 0.85


@dataclass(slots=True)
class GalgameReaderConfig:
    reader_mode: str = READER_MODE_AUTO


@dataclass(slots=True)
class GalgameMemoryReaderConfig:
    memory_reader_enabled: bool = False
    memory_reader_textractor_path: str = ""
    memory_reader_textractor_proxy: str = ""
    memory_reader_install_release_api_url: str = ""
    memory_reader_install_target_dir: str = ""
    memory_reader_install_timeout_seconds: float = 600.0
    memory_reader_auto_detect: bool = True
    memory_reader_hook_codes: list[str] = field(default_factory=list)
    memory_reader_engine_hook_codes: dict[str, list[str]] = field(default_factory=dict)
    memory_reader_poll_interval_seconds: float = 1.0


@dataclass(slots=True)
class GalgameOcrReaderConfig:
    ocr_reader_enabled: bool = False
    ocr_reader_enabled_explicit: bool = False
    ocr_reader_backend_selection: str = "auto"
    ocr_reader_backend_selection_explicit: bool = False
    ocr_reader_capture_backend: str = "smart"
    ocr_reader_capture_backend_explicit: bool = False
    ocr_reader_install_manifest_url: str = ""
    ocr_reader_install_target_dir: str = ""
    ocr_reader_install_timeout_seconds: float = 300.0
    ocr_reader_poll_interval_seconds: float = 0.5
    ocr_reader_trigger_mode: str = OCR_TRIGGER_MODE_INTERVAL
    ocr_reader_fast_loop_enabled: bool = True
    ocr_reader_no_text_takeover_after_seconds: float = 30.0
    ocr_reader_background_scene_change_distance: int = 28
    ocr_reader_languages: str = "chi_sim+jpn+eng"
    ocr_reader_left_inset_ratio: float = DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO
    ocr_reader_right_inset_ratio: float = DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO
    ocr_reader_top_ratio: float = DEFAULT_OCR_CAPTURE_TOP_RATIO
    ocr_reader_bottom_inset_ratio: float = DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO
    ocr_reader_screen_awareness_full_frame_ocr: bool = False
    ocr_reader_screen_awareness_multi_region_ocr: bool = False
    ocr_reader_screen_awareness_visual_rules: bool = False
    ocr_reader_screen_awareness_latency_mode: str = "balanced"
    ocr_reader_screen_awareness_min_interval_seconds: float = 2.0
    ocr_reader_screen_awareness_sample_collection_enabled: bool = False
    ocr_reader_screen_awareness_sample_dir: str = ""
    ocr_reader_screen_awareness_model_enabled: bool = False
    ocr_reader_screen_awareness_model_path: str = ""
    ocr_reader_screen_awareness_model_min_confidence: float = 0.55
    ocr_reader_screen_templates: list[dict[str, Any]] = field(default_factory=list)
    ocr_reader_screen_type_transition_emit: bool = True
    ocr_reader_known_screen_timeout_seconds: float = 5.0
    ocr_reader_max_unobserved_advances_before_hold: int = 3
    ocr_reader_unobserved_advance_hold_duration_seconds: float = 0.0


@dataclass(slots=True)
class GalgameVisionConfig:
    vision_classifier_enabled: bool = False
    vision_classifier_model_dir: str = DEFAULT_VISION_CLASSIFIER_MODEL_DIR
    vision_classifier_model_name: str = "v1_galgame"
    vision_classifier_threshold: float = 0.75
    vision_classifier_tick_interval: int = 1
    vision_classifier_inference_timeout_ms: float = 200.0
    vision_classifier_input_size: list[int] = field(default_factory=lambda: [224, 224])
    vision_classifier_input_size_low: list[int] = field(default_factory=lambda: [160, 160])


@dataclass(slots=True)
class GalgameRapidOcrConfig:
    rapidocr_enabled: bool = False
    rapidocr_enabled_explicit: bool = False
    # `rapidocr_install_target_dir` survived the install-removal because
    # ocr_reader still treats it as the runtime model cache root path
    # (where rapidocr writes downloaded model files). Name is misleading
    # post-refactor — TODO rename to `rapidocr_model_cache_root` in a
    # follow-up. `rapidocr_install_manifest_url` and
    # `rapidocr_install_timeout_seconds` are gone — they only fed the
    # deleted runtime install machinery.
    rapidocr_install_target_dir: str = ""
    rapidocr_engine_type: str = "onnxruntime"
    # Default to the bundled Chinese PP-OCRv4 model. Japanese games can opt
    # back into `japan`; existing configs that explicitly set other values are
    # preserved by the loader.
    rapidocr_lang_type: str = "ch"
    rapidocr_model_type: str = "mobile"
    rapidocr_ocr_version: str = "PP-OCRv4"
    rapidocr_auto_detect_lang: bool = True
    rapidocr_auto_detect_last_lang: str = ""


@dataclass(slots=True, init=False)
class GalgameConfig:
    bridge: GalgameBridgeConfig
    history: GalgameHistoryConfig
    llm: GalgameLLMConfig
    reader: GalgameReaderConfig
    memory_reader: GalgameMemoryReaderConfig
    ocr_reader: GalgameOcrReaderConfig
    vision: GalgameVisionConfig
    rapidocr: GalgameRapidOcrConfig

    _FIELD_MAP: ClassVar[dict[str, tuple[str, str]]] = {
        "bridge_root": ("bridge", "bridge_root"),
        "active_poll_interval_seconds": ("bridge", "active_poll_interval_seconds"),
        "idle_poll_interval_seconds": ("bridge", "idle_poll_interval_seconds"),
        "stale_after_seconds": ("bridge", "stale_after_seconds"),
        "default_mode": ("bridge", "default_mode"),
        "push_notifications": ("bridge", "push_notifications"),
        "scene_change_cooldown_seconds": ("bridge", "scene_change_cooldown_seconds"),
        "scene_push_half_threshold": ("bridge", "scene_push_half_threshold"),
        "scene_push_time_fallback_seconds": ("bridge", "scene_push_time_fallback_seconds"),
        "scene_merge_total_threshold": ("bridge", "scene_merge_total_threshold"),
        "auto_open_ui": ("bridge", "auto_open_ui"),
        "history_events_limit": ("history", "history_events_limit"),
        "history_lines_limit": ("history", "history_lines_limit"),
        "history_choices_limit": ("history", "history_choices_limit"),
        "dedupe_window_limit": ("history", "dedupe_window_limit"),
        "warmup_replay_bytes_limit": ("history", "warmup_replay_bytes_limit"),
        "warmup_replay_events_limit": ("history", "warmup_replay_events_limit"),
        "llm_call_timeout_seconds": ("llm", "llm_call_timeout_seconds"),
        "llm_max_in_flight": ("llm", "llm_max_in_flight"),
        "llm_request_cache_ttl_seconds": ("llm", "llm_request_cache_ttl_seconds"),
        "llm_explain_cache_ttl_seconds": ("llm", "llm_explain_cache_ttl_seconds"),
        "llm_scene_summary_cache_ttl_seconds": ("llm", "llm_scene_summary_cache_ttl_seconds"),
        "llm_choice_cache_ttl_seconds": ("llm", "llm_choice_cache_ttl_seconds"),
        "llm_near_match_cache_enabled": ("llm", "llm_near_match_cache_enabled"),
        "llm_near_match_cache_ttl_seconds": ("llm", "llm_near_match_cache_ttl_seconds"),
        "llm_target_entry_ref": ("llm", "llm_target_entry_ref"),
        "llm_vision_enabled": ("llm", "llm_vision_enabled"),
        "llm_vision_max_image_px": ("llm", "llm_vision_max_image_px"),
        "llm_max_tokens_agent_reply": ("llm", "llm_max_tokens_agent_reply"),
        "llm_max_tokens_default": ("llm", "llm_max_tokens_default"),
        "context_max_tokens": ("llm", "context_max_tokens"),
        "context_metrics_enabled": ("llm", "context_metrics_enabled"),
        "context_counting_mode": ("llm", "context_counting_mode"),
        "context_semantic_compression": ("llm", "context_semantic_compression"),
        "context_explain_min_lines": ("llm", "context_explain_min_lines"),
        "context_explain_max_lines": ("llm", "context_explain_max_lines"),
        "context_window_target_tokens": ("llm", "context_window_target_tokens"),
        "context_scene_summary_mode": ("llm", "context_scene_summary_mode"),
        "context_cumulative_llm_trigger_lines": (
            "llm",
            "context_cumulative_llm_trigger_lines",
        ),
        "context_line_importance_enabled": ("llm", "context_line_importance_enabled"),
        "context_persist_enabled": ("llm", "context_persist_enabled"),
        "context_persist_max_age_seconds": ("llm", "context_persist_max_age_seconds"),
        "context_persist_require_game_id": ("llm", "context_persist_require_game_id"),
        "llm_repeat_detection_enabled": ("llm", "llm_repeat_detection_enabled"),
        "llm_repeat_similarity_threshold": ("llm", "llm_repeat_similarity_threshold"),
        "reader_mode": ("reader", "reader_mode"),
        "memory_reader_enabled": ("memory_reader", "memory_reader_enabled"),
        "memory_reader_textractor_path": ("memory_reader", "memory_reader_textractor_path"),
        "memory_reader_textractor_proxy": (
            "memory_reader",
            "memory_reader_textractor_proxy",
        ),
        "memory_reader_install_release_api_url": (
            "memory_reader",
            "memory_reader_install_release_api_url",
        ),
        "memory_reader_install_target_dir": ("memory_reader", "memory_reader_install_target_dir"),
        "memory_reader_install_timeout_seconds": (
            "memory_reader",
            "memory_reader_install_timeout_seconds",
        ),
        "memory_reader_auto_detect": ("memory_reader", "memory_reader_auto_detect"),
        "memory_reader_hook_codes": ("memory_reader", "memory_reader_hook_codes"),
        "memory_reader_engine_hook_codes": (
            "memory_reader",
            "memory_reader_engine_hook_codes",
        ),
        "memory_reader_poll_interval_seconds": (
            "memory_reader",
            "memory_reader_poll_interval_seconds",
        ),
        "ocr_reader_enabled": ("ocr_reader", "ocr_reader_enabled"),
        "ocr_reader_enabled_explicit": ("ocr_reader", "ocr_reader_enabled_explicit"),
        "ocr_reader_backend_selection": ("ocr_reader", "ocr_reader_backend_selection"),
        "ocr_reader_backend_selection_explicit": (
            "ocr_reader",
            "ocr_reader_backend_selection_explicit",
        ),
        "ocr_reader_capture_backend": ("ocr_reader", "ocr_reader_capture_backend"),
        "ocr_reader_capture_backend_explicit": (
            "ocr_reader",
            "ocr_reader_capture_backend_explicit",
        ),
        "ocr_reader_install_manifest_url": ("ocr_reader", "ocr_reader_install_manifest_url"),
        "ocr_reader_install_target_dir": ("ocr_reader", "ocr_reader_install_target_dir"),
        "ocr_reader_install_timeout_seconds": (
            "ocr_reader",
            "ocr_reader_install_timeout_seconds",
        ),
        "ocr_reader_poll_interval_seconds": ("ocr_reader", "ocr_reader_poll_interval_seconds"),
        "ocr_reader_trigger_mode": ("ocr_reader", "ocr_reader_trigger_mode"),
        "ocr_reader_fast_loop_enabled": ("ocr_reader", "ocr_reader_fast_loop_enabled"),
        "ocr_reader_no_text_takeover_after_seconds": (
            "ocr_reader",
            "ocr_reader_no_text_takeover_after_seconds",
        ),
        "ocr_reader_background_scene_change_distance": (
            "ocr_reader",
            "ocr_reader_background_scene_change_distance",
        ),
        "ocr_reader_languages": ("ocr_reader", "ocr_reader_languages"),
        "ocr_reader_left_inset_ratio": ("ocr_reader", "ocr_reader_left_inset_ratio"),
        "ocr_reader_right_inset_ratio": ("ocr_reader", "ocr_reader_right_inset_ratio"),
        "ocr_reader_top_ratio": ("ocr_reader", "ocr_reader_top_ratio"),
        "ocr_reader_bottom_inset_ratio": ("ocr_reader", "ocr_reader_bottom_inset_ratio"),
        "ocr_reader_screen_awareness_full_frame_ocr": (
            "ocr_reader",
            "ocr_reader_screen_awareness_full_frame_ocr",
        ),
        "ocr_reader_screen_awareness_multi_region_ocr": (
            "ocr_reader",
            "ocr_reader_screen_awareness_multi_region_ocr",
        ),
        "ocr_reader_screen_awareness_visual_rules": (
            "ocr_reader",
            "ocr_reader_screen_awareness_visual_rules",
        ),
        "ocr_reader_screen_awareness_latency_mode": (
            "ocr_reader",
            "ocr_reader_screen_awareness_latency_mode",
        ),
        "ocr_reader_screen_awareness_min_interval_seconds": (
            "ocr_reader",
            "ocr_reader_screen_awareness_min_interval_seconds",
        ),
        "ocr_reader_screen_awareness_sample_collection_enabled": (
            "ocr_reader",
            "ocr_reader_screen_awareness_sample_collection_enabled",
        ),
        "ocr_reader_screen_awareness_sample_dir": (
            "ocr_reader",
            "ocr_reader_screen_awareness_sample_dir",
        ),
        "ocr_reader_screen_awareness_model_enabled": (
            "ocr_reader",
            "ocr_reader_screen_awareness_model_enabled",
        ),
        "ocr_reader_screen_awareness_model_path": (
            "ocr_reader",
            "ocr_reader_screen_awareness_model_path",
        ),
        "ocr_reader_screen_awareness_model_min_confidence": (
            "ocr_reader",
            "ocr_reader_screen_awareness_model_min_confidence",
        ),
        "ocr_reader_screen_templates": ("ocr_reader", "ocr_reader_screen_templates"),
        "ocr_reader_screen_type_transition_emit": (
            "ocr_reader",
            "ocr_reader_screen_type_transition_emit",
        ),
        "ocr_reader_known_screen_timeout_seconds": (
            "ocr_reader",
            "ocr_reader_known_screen_timeout_seconds",
        ),
        "ocr_reader_max_unobserved_advances_before_hold": (
            "ocr_reader",
            "ocr_reader_max_unobserved_advances_before_hold",
        ),
        "ocr_reader_unobserved_advance_hold_duration_seconds": (
            "ocr_reader",
            "ocr_reader_unobserved_advance_hold_duration_seconds",
        ),
        "vision_classifier_enabled": ("vision", "vision_classifier_enabled"),
        "vision_classifier_model_dir": ("vision", "vision_classifier_model_dir"),
        "vision_classifier_model_name": ("vision", "vision_classifier_model_name"),
        "vision_classifier_threshold": ("vision", "vision_classifier_threshold"),
        "vision_classifier_tick_interval": ("vision", "vision_classifier_tick_interval"),
        "vision_classifier_inference_timeout_ms": (
            "vision",
            "vision_classifier_inference_timeout_ms",
        ),
        "vision_classifier_input_size": ("vision", "vision_classifier_input_size"),
        "vision_classifier_input_size_low": (
            "vision",
            "vision_classifier_input_size_low",
        ),
        "rapidocr_enabled": ("rapidocr", "rapidocr_enabled"),
        "rapidocr_enabled_explicit": ("rapidocr", "rapidocr_enabled_explicit"),
        "rapidocr_install_target_dir": ("rapidocr", "rapidocr_install_target_dir"),
        "rapidocr_engine_type": ("rapidocr", "rapidocr_engine_type"),
        "rapidocr_lang_type": ("rapidocr", "rapidocr_lang_type"),
        "rapidocr_model_type": ("rapidocr", "rapidocr_model_type"),
        "rapidocr_ocr_version": ("rapidocr", "rapidocr_ocr_version"),
        "rapidocr_auto_detect_lang": ("rapidocr", "rapidocr_auto_detect_lang"),
        "rapidocr_auto_detect_last_lang": ("rapidocr", "rapidocr_auto_detect_last_lang"),
    }

    def __init__(
        self,
        *,
        bridge: GalgameBridgeConfig | None = None,
        history: GalgameHistoryConfig | None = None,
        llm: GalgameLLMConfig | None = None,
        reader: GalgameReaderConfig | None = None,
        memory_reader: GalgameMemoryReaderConfig | None = None,
        ocr_reader: GalgameOcrReaderConfig | None = None,
        vision: GalgameVisionConfig | None = None,
        rapidocr: GalgameRapidOcrConfig | None = None,
        **legacy_fields: Any,
    ) -> None:
        self.bridge = bridge if bridge is not None else GalgameBridgeConfig()
        self.history = history if history is not None else GalgameHistoryConfig()
        self.llm = llm if llm is not None else GalgameLLMConfig()
        self.reader = reader if reader is not None else GalgameReaderConfig()
        self.memory_reader = (
            memory_reader if memory_reader is not None else GalgameMemoryReaderConfig()
        )
        self.ocr_reader = ocr_reader if ocr_reader is not None else GalgameOcrReaderConfig()
        self.vision = vision if vision is not None else GalgameVisionConfig()
        self.rapidocr = rapidocr if rapidocr is not None else GalgameRapidOcrConfig()

        for field_name in self._FIELD_MAP:
            if field_name in legacy_fields:
                setattr(self, field_name, legacy_fields.pop(field_name))
        if legacy_fields:
            unexpected = ", ".join(sorted(legacy_fields))
            raise TypeError(f"unexpected GalgameConfig field(s): {unexpected}")


for _field_name, (_group_name, _field_group_attr) in GalgameConfig._FIELD_MAP.items():
    setattr(GalgameConfig, _field_name, _ConfigFieldProxy(_group_name, _field_group_attr))
del _field_name, _group_name, _field_group_attr
