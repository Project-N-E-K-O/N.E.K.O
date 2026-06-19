from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from .agent_consultation import (
    CONSULT_REASON_CHOICE,
    CONSULT_REASON_SCENE_CHANGE,
    ConsultInputs,
    MAX_CAT_OPINIONS,
    build_consult_prompt,
    decide_consultation,
    inject_cat_opinion,
    render_cat_opinions_for_strategy,
    summarize_character_voice,
)
from .cross_scene_memory import (
    empty_memory as _cross_scene_empty_memory,
    render_for_push as _render_cross_scene_memory_for_push,
    sanitize_memory as _cross_scene_sanitize,
)
from .host_agent_adapter import HostAgentAdapter, HostAgentError
from .context_builder import (
    _compute_dynamic_line_limit,
    _context_window_bounds,
    _fixed_character_pov_context as _build_fixed_character_pov_context,
    _matching_context_snapshot,
    _recency_ordered_context_lines,
    _scene_summary_seed_with_restored_context,
)
from .push_composer import PushComposer
from .local_input_actuator import (
    VIRTUAL_MOUSE_DIALOGUE_CANDIDATES,
    perform_local_input_actuation,
    try_focus_target_window,
)
from .models import (
    ADVANCE_SPEED_FAST,
    ADVANCE_SPEED_MEDIUM,
    ADVANCE_SPEED_SLOW,
    AGENT_STATUS_ACTIVE,
    AGENT_STATUS_ERROR,
    AGENT_STATUS_STANDBY,
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    GalgameLLMConfig,
    STORE_CROSS_SCENE_MEMORY,
    SharedStatePayload,
    json_copy,
    sanitize_snapshot_state,
)
from .service import (
    build_choice_signature,
    build_local_scene_summary,
    build_snapshot_signature,
    build_suggest_context,
    build_summarize_context,
    latest_selected_choice,
    mode_allows_agent_actuation,
    mode_allows_agent_push,
    mode_allows_choice_push,
    resolve_effective_current_line,
)

# Menu-choice text is short by nature; cap it tight so an over-long (e.g.
# injection) choice string is truncated before being quoted into the
# instruction. ~50 heuristic tokens ≈ 33 CJK / 200 ASCII chars. Tunable.
_CHOICE_INSTRUCTION_TEXT_MAX_TOKENS = 50
_CHOICE_INSTRUCTION_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TITLE_START_TEXT_MARKERS = (
    "start",
    "new game",
    "continue",
    "load game",
    "开始",
    "開始",
    "新游戏",
    "继续",
    "繼續",
    "はじめから",
    "つづきから",
    "スタート",
)
_TITLE_EXCLUDED_TEXT_MARKERS = (
    "config",
    "setting",
    "option",
    "settings",
    "quit",
    "exit",
    "设置",
    "設定",
    "选项",
    "選項",
    "退出",
    "終了",
    "コンフィグ",
    "オプション",
)

_SCREEN_RECOVERY_STAGES = frozenset(
    {
        "save_load",
        "config_screen",
        "gallery_screen",
        "game_over_screen",
    }
)
_SCREEN_ESCAPE_STRATEGY_IDS = frozenset(
    {
        "save_load_escape",
        "config_escape",
        "gallery_escape",
        "game_over_escape",
    }
)

__all__ = [name for name in globals() if not name.startswith("__")]
