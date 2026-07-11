# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pregame context defaults/normalization/formatting and the pregame
AI builders for soccer and badminton.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import (
    _is_repeated_neko_invite,
    _log_game_debug_material,
    _normalize_short_text,
    _strip_json_fence,
    logger,
)
from .badminton_scores import _normalize_badminton_mode
from .balance import _SOCCER_EMOTION_INERTIA
from .char_info import _get_character_info
from .game_context import _normalize_text_items

import json
from typing import Any
from config.prompts.prompts_minigame_common import PREGAME_CONTEXT_INPUT_WATERMARK
from config.prompts.prompts_soccer import (
    get_soccer_pregame_context_formatter_labels,
    get_soccer_pregame_context_prompt,
)
from config.prompts.prompts_badminton import (
    get_badminton_pregame_context_formatter_labels,
    get_badminton_pregame_context_prompt,
)


_SOCCER_MOODS = {"calm", "happy", "angry", "relaxed", "sad", "surprised"}


_SOCCER_DIFFICULTIES = {"max", "lv2", "lv3", "lv4"}


_SOCCER_DEFAULT_DIFFICULTIES = ("lv2", "lv3")


_SOCCER_GAME_STANCES = {
    "neutral_play",
    "teaching",
    "soft_teasing",
    "competitive",
    "punishing",
    "withdrawn",
}


def _soccer_random_default_difficulty() -> str:
    # 默认锁 lv2 与前端 DEFAULT_DIFFICULTY_INDEX / prompts_soccer initialDifficulty 对齐；
    # lv3 仍是 _SOCCER_DEFAULT_DIFFICULTIES 合法值，允许 upstream 显式 soften 一档。
    return "lv2"


def _default_soccer_pregame_context(*, initial_difficulty: str | None = None) -> dict:
    difficulty = initial_difficulty if initial_difficulty in _SOCCER_DEFAULT_DIFFICULTIES else _soccer_random_default_difficulty()
    return {
        "launchIntent": "unknown",
        "confidence": 0.0,
        "evidence": [],
        "nekoEmotion": "calm",
        "emotionIntensity": 0.0,
        "emotionInertia": "low",
        "gameStance": "neutral_play",
        "stanceNote": "证据不足，按普通陪玩开局。",
        "initialMood": "calm",
        "initialDifficulty": difficulty,
        "openingLine": "",
        "tonePolicy": "普通陪玩，轻松自然，不强行解释成哄开心或关系修复。",
        "difficultyPolicy": "普通陪玩默认中等难度；后续由局内互动和游戏 AI 自然调整。",
        "moodPolicy": "沿用普通陪玩表现；不引入强情绪惯性。",
        "softeningSignals": [],
        "hardeningSignals": [],
        "neutralEventPolicy": "普通比赛事件只产生即时反应，不强行改变关系状态。",
        "specialPolicies": [],
        "postgameCarryback": "赛后只按真实比赛过程和互动自然归档。",
    }


def _normalize_soccer_pregame_context(value: Any, *, neko_invite_text: str = "") -> tuple[dict, bool]:
    """Normalize model output. Returns (context, had_invalid_fields)."""
    base = _default_soccer_pregame_context()
    if not isinstance(value, dict):
        return base, True

    context = dict(base)
    invalid = False

    string_fields = (
        "launchIntent",
        "nekoEmotion",
        "stanceNote",
        "tonePolicy",
        "difficultyPolicy",
        "moodPolicy",
        "neutralEventPolicy",
        "postgameCarryback",
    )
    for field in string_fields:
        if field in value:
            text = _normalize_short_text(value.get(field), max_chars=220)
            if text:
                context[field] = text
            elif value.get(field) not in (None, ""):
                invalid = True

    if "confidence" in value:
        try:
            confidence = float(value.get("confidence"))
            if 0.0 <= confidence <= 1.0:
                context["confidence"] = confidence
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionIntensity" in value:
        try:
            intensity = float(value.get("emotionIntensity"))
            if 0.0 <= intensity <= 1.0:
                context["emotionIntensity"] = intensity
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionInertia" in value:
        inertia = str(value.get("emotionInertia") or "").strip()
        if inertia in _SOCCER_EMOTION_INERTIA:
            context["emotionInertia"] = inertia
        else:
            invalid = True

    if "gameStance" in value:
        stance = str(value.get("gameStance") or "").strip()
        if stance in _SOCCER_GAME_STANCES:
            context["gameStance"] = stance
        else:
            invalid = True

    if "initialMood" in value:
        mood = str(value.get("initialMood") or "").strip()
        if mood in _SOCCER_MOODS:
            context["initialMood"] = mood
        else:
            invalid = True

    if "initialDifficulty" in value:
        difficulty = str(value.get("initialDifficulty") or "").strip()
        if difficulty in _SOCCER_DIFFICULTIES:
            context["initialDifficulty"] = difficulty
        else:
            invalid = True

    if "openingLine" in value:
        opening_line = _normalize_short_text(value.get("openingLine"), max_chars=0)
        if len(opening_line) > 15:
            opening_line = ""
            invalid = True
        if opening_line and _is_repeated_neko_invite(opening_line, neko_invite_text):
            opening_line = ""
        context["openingLine"] = opening_line

    for field in ("evidence", "softeningSignals", "hardeningSignals", "specialPolicies"):
        if field in value:
            items = _normalize_text_items(value.get(field), max_items=5, max_chars=100)
            if items or value.get(field) in (None, "", []):
                context[field] = items
            else:
                invalid = True

    # 普通陪玩和任何被兜底出来的开局都不能默认 max。
    if context["gameStance"] == "neutral_play":
        if context["initialDifficulty"] not in _SOCCER_DEFAULT_DIFFICULTIES:
            invalid = True
        context["initialDifficulty"] = _soccer_random_default_difficulty()
        if not context.get("difficultyPolicy"):
            context["difficultyPolicy"] = base["difficultyPolicy"]

    return context, invalid


def _format_soccer_pregame_context_for_prompt(pre_game_context: Any, language: str | None = None) -> str:
    if not isinstance(pre_game_context, dict):
        return ""
    compact = json.dumps(pre_game_context, ensure_ascii=False, separators=(",", ":"))
    labels = get_soccer_pregame_context_formatter_labels(language)
    return (
        f"{labels['header']}\n"
        f"{compact}\n"
        f"{labels['usage']}\n"
    )


_BADMINTON_EXPRESSIONS = {"cheer", "shock", "hype", "anticipate", "bored", "tease"}


_BADMINTON_INTENSITIES = {"low", "medium", "high"}


def _default_badminton_pregame_context(*, initial_difficulty: str | None = None, mode: str = "spectator") -> dict:
    normalized_mode = _normalize_badminton_mode(mode)
    difficulty = initial_difficulty if initial_difficulty in _SOCCER_DIFFICULTIES else "lv2"
    mode_label = {
        "spectator": "自由练习",
        "duel": "羽毛球对拉",
    }.get(normalized_mode, "羽毛球挑战")
    return {
        "launchIntent": "unknown",
        "confidence": 0.0,
        "evidence": [],
        "nekoEmotion": "calm",
        "emotionIntensity": 0.0,
        "emotionInertia": "low",
        "gameStance": "neutral_play",
        "stanceNote": f"证据不足，按普通{mode_label}陪玩开局。",
        "initialMood": "calm",
        "initialExpression": "anticipate",
        "initialIntensity": "low",
        "initialDifficulty": difficulty,
        "openingLine": "",
        "tonePolicy": "普通陪玩，轻松自然，不强行解释成哄开心或关系修复。",
        "difficultyPolicy": "duel 默认 lv2；spectator 忽略初始 difficulty，由局内事件自然调整。",
        "moodPolicy": "沿用普通羽毛球陪玩表现；不引入强情绪惯性。",
        "expressionPolicy": "默认期待/轻吐槽；根据成功、失误、连中和对拉比分自然变化。",
        "softeningSignals": [],
        "hardeningSignals": [],
        "specialPolicies": [],
        "postgameCarryback": "赛后只按真实羽毛球过程和互动自然归档。",
    }


def _normalize_badminton_pregame_context(
    value: Any,
    *,
    neko_invite_text: str = "",
    mode: str = "spectator",
) -> tuple[dict, bool]:
    """Normalize badminton pregame model output. Returns (context, had_invalid_fields)."""
    base = _default_badminton_pregame_context(mode=mode)
    if not isinstance(value, dict):
        return base, True

    context = dict(base)
    invalid = False

    string_fields = (
        "launchIntent",
        "nekoEmotion",
        "stanceNote",
        "tonePolicy",
        "difficultyPolicy",
        "moodPolicy",
        "expressionPolicy",
        "postgameCarryback",
    )
    for field in string_fields:
        if field in value:
            text = _normalize_short_text(value.get(field), max_chars=220)
            if text:
                context[field] = text
            elif value.get(field) not in (None, ""):
                invalid = True

    if "confidence" in value:
        try:
            confidence = float(value.get("confidence"))
            if 0.0 <= confidence <= 1.0:
                context["confidence"] = confidence
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionIntensity" in value:
        try:
            intensity = float(value.get("emotionIntensity"))
            if 0.0 <= intensity <= 1.0:
                context["emotionIntensity"] = intensity
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionInertia" in value:
        inertia = str(value.get("emotionInertia") or "").strip()
        if inertia in _SOCCER_EMOTION_INERTIA:
            context["emotionInertia"] = inertia
        else:
            invalid = True

    if "gameStance" in value:
        stance = str(value.get("gameStance") or "").strip()
        if stance in _SOCCER_GAME_STANCES:
            context["gameStance"] = stance
        else:
            invalid = True

    if "initialMood" in value:
        mood = str(value.get("initialMood") or "").strip()
        if mood in _SOCCER_MOODS:
            context["initialMood"] = mood
        else:
            invalid = True

    if "initialExpression" in value:
        expression = str(value.get("initialExpression") or "").strip()
        if expression in _BADMINTON_EXPRESSIONS:
            context["initialExpression"] = expression
        else:
            invalid = True

    if "initialIntensity" in value:
        intensity = str(value.get("initialIntensity") or "").strip()
        if intensity in _BADMINTON_INTENSITIES:
            context["initialIntensity"] = intensity
        else:
            invalid = True

    if "initialDifficulty" in value:
        difficulty = str(value.get("initialDifficulty") or "").strip()
        if difficulty in _SOCCER_DIFFICULTIES:
            context["initialDifficulty"] = difficulty
        else:
            invalid = True

    if "openingLine" in value:
        opening_line = _normalize_short_text(value.get("openingLine"), max_chars=0)
        if len(opening_line) > 15:
            opening_line = ""
            invalid = True
        if opening_line and _is_repeated_neko_invite(opening_line, neko_invite_text):
            opening_line = ""
        context["openingLine"] = opening_line

    for field in ("evidence", "softeningSignals", "hardeningSignals", "specialPolicies"):
        if field in value:
            items = _normalize_text_items(value.get(field), max_items=5, max_chars=100)
            if items or value.get(field) in (None, "", []):
                context[field] = items
            else:
                invalid = True

    if context["gameStance"] == "neutral_play":
        if context["initialDifficulty"] not in _SOCCER_DEFAULT_DIFFICULTIES:
            invalid = True
        context["initialDifficulty"] = "lv2"
        if context["initialMood"] == "angry":
            context["initialMood"] = "calm"
            invalid = True
        if context["initialIntensity"] == "high":
            context["initialIntensity"] = "low"
            invalid = True

    return context, invalid


def _format_badminton_pregame_context_for_prompt(
    pre_game_context: Any,
    language: str | None = None,
    *,
    mode: str = "spectator",
) -> str:
    if not isinstance(pre_game_context, dict):
        return ""
    compact = json.dumps(pre_game_context, ensure_ascii=False, separators=(",", ":"))
    labels = get_badminton_pregame_context_formatter_labels(language)
    mode_label = {
        "spectator": "羽毛球自由练习",
        "duel": "羽毛球对拉",
    }.get(_normalize_badminton_mode(mode), "羽毛球挑战")
    return (
        f"{labels['header']}\n"
        f"mode={mode_label}\n"
        f"{compact}\n"
        f"{labels['usage']}\n"
    )


async def _fetch_recent_history_for_pregame(lanlan_name: str) -> tuple[str, str]:
    try:
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        client = get_internal_http_client()
        response = await client.get(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/get_recent_history/{lanlan_name}",
            timeout=5.0,
        )
        if not response.is_success:
            return "", "recent_history_failed"
        return str(response.text or ""), ""
    except Exception as exc:
        logger.warning("🎮 开局近期记录读取失败，使用空历史: lanlan=%s err=%s", lanlan_name, exc)
        return "", "recent_history_failed"


async def _run_pregame_context_ai(
    *,
    lanlan_name: str,
    master_name: str,
    lanlan_prompt: str,
    recent_history: str,
    neko_initiated: bool,
    neko_invite_text: str,
    prompt_template: str,
    extra_payload: dict | None = None,
) -> dict:
    char_info = _get_character_info(lanlan_name)
    user_payload = {
        "lanlanName": lanlan_name,
        "masterName": master_name,
        "recentHistory": recent_history or "开始聊天前，没有历史记录。",
        "nekoInitiated": bool(neko_initiated),
        "nekoInviteText": neko_invite_text,
        "characterPromptExcerpt": str(lanlan_prompt or "")[:1200],
    }
    if isinstance(extra_payload, dict):
        user_payload.update(extra_payload)

    try:
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
        from utils.token_tracker import set_call_type

        set_call_type("game_pregame_context")
        llm = await create_chat_llm_async(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            provider_type=char_info.get("provider_type"),
            max_completion_tokens=900,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # game-session-scoped input (snapshot / history / archive / config), bounded by a single finite game; not external free-text. Deeper per-field truncation tracked as a game-domain follow-up.
                SystemMessage(content=prompt_template),
                HumanMessage(
                    content=f"{json.dumps(user_payload, ensure_ascii=False)}\n{PREGAME_CONTEXT_INPUT_WATERMARK}"
                ),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
    except Exception as exc:
        logger.warning("🎮 开局上下文分析失败: lanlan=%s err=%s", lanlan_name, exc)
        raise

    if not isinstance(parsed, dict):
        raise ValueError("pregame_context_json_not_object")
    return parsed


async def _run_soccer_pregame_context_ai(
    *,
    lanlan_name: str,
    master_name: str,
    lanlan_prompt: str,
    recent_history: str,
    neko_initiated: bool,
    neko_invite_text: str,
) -> dict:
    char_info = _get_character_info(lanlan_name)
    return await _run_pregame_context_ai(
        lanlan_name=lanlan_name,
        master_name=master_name,
        lanlan_prompt=lanlan_prompt,
        recent_history=recent_history,
        neko_initiated=neko_initiated,
        neko_invite_text=neko_invite_text,
        prompt_template=get_soccer_pregame_context_prompt(char_info.get("user_language")),
        extra_payload={"gameType": "soccer"},
    )


async def _build_soccer_pregame_context(
    *,
    game_type: str,
    session_id: str,
    lanlan_name: str,
    neko_initiated: bool,
    neko_invite_text: str,
) -> tuple[dict, str, str]:
    char_info = _get_character_info(lanlan_name)
    recent_history, history_error = await _fetch_recent_history_for_pregame(lanlan_name)
    _log_game_debug_material(
        "pregame_recent_history",
        recent_history or "开始聊天前，没有历史记录。",
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="memory_server_recent_history" if not history_error else "fallback_empty_history",
    )

    try:
        raw_context = await _run_soccer_pregame_context_ai(
            lanlan_name=lanlan_name,
            master_name=str(char_info.get("master_name") or "玩家"),
            lanlan_prompt=str(char_info.get("lanlan_prompt") or ""),
            recent_history=recent_history,
            neko_initiated=neko_initiated,
            neko_invite_text=neko_invite_text,
        )
    except ValueError as exc:
        logger.warning("🎮 开局上下文 JSON 非法，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
        context = _default_soccer_pregame_context()
        return context, "fallback", "invalid_json"
    except Exception:
        context = _default_soccer_pregame_context()
        return context, "fallback", "ai_failed"

    context, invalid_fields = _normalize_soccer_pregame_context(
        raw_context,
        neko_invite_text=neko_invite_text,
    )
    source = "ai"
    error = "invalid_fields" if invalid_fields else history_error
    _log_game_debug_material(
        "pregame_context",
        {
            "source": source,
            "error": error,
            "context": context,
        },
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="game_pregame_context",
    )
    return context, source, error


async def _build_badminton_pregame_context(
    *,
    game_type: str,
    session_id: str,
    lanlan_name: str,
    neko_initiated: bool,
    neko_invite_text: str,
    mode: str = "spectator",
) -> tuple[dict, str, str]:
    normalized_mode = _normalize_badminton_mode(mode)
    char_info = _get_character_info(lanlan_name)
    recent_history, history_error = await _fetch_recent_history_for_pregame(lanlan_name)
    _log_game_debug_material(
        "pregame_recent_history",
        recent_history or "开始聊天前，没有历史记录。",
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="memory_server_recent_history" if not history_error else "fallback_empty_history",
    )

    try:
        raw_context = await _run_pregame_context_ai(
            lanlan_name=lanlan_name,
            master_name=str(char_info.get("master_name") or "玩家"),
            lanlan_prompt=str(char_info.get("lanlan_prompt") or ""),
            recent_history=recent_history,
            neko_initiated=neko_initiated,
            neko_invite_text=neko_invite_text,
            prompt_template=get_badminton_pregame_context_prompt(char_info.get("user_language")),
            extra_payload={"gameType": game_type, "mode": normalized_mode},
        )
    except ValueError as exc:
        logger.warning("🎮 羽毛球开局上下文 JSON 非法，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
        context = _default_badminton_pregame_context(mode=normalized_mode)
        return context, "fallback", "invalid_json"
    except Exception:
        context = _default_badminton_pregame_context(mode=normalized_mode)
        return context, "fallback", "ai_failed"

    context, invalid_fields = _normalize_badminton_pregame_context(
        raw_context,
        neko_invite_text=neko_invite_text,
        mode=normalized_mode,
    )
    source = "ai"
    error = "invalid_fields" if invalid_fields else history_error
    _log_game_debug_material(
        "pregame_context",
        {
            "source": source,
            "error": error,
            "mode": normalized_mode,
            "context": context,
        },
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="game_pregame_context",
    )
    return context, source, error
