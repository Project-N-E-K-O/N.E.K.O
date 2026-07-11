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

"""Difficulty balance hints and anger-pressure caps for soccer and
badminton duels.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from .visible_events import _normalize_badminton_shot_type, _sanitize_badminton_duel_state

from typing import Any, Dict
from config.prompts.prompts_soccer import (
    get_soccer_anger_pressure_cap_message,
    get_soccer_anger_pressure_cap_reason,
)
from utils.language_utils import normalize_language_code


_SOCCER_ANGER_PRESSURE_CAP_WEAK = 8


_SOCCER_ANGER_PRESSURE_CAP_DEFAULT = 25


_SOCCER_ANGER_PRESSURE_CAP_STRONG = 50


_SOCCER_EMOTION_INERTIA = {"low", "medium", "high", "very_high"}


def _badminton_duel_difficulty_control_prompt(language: str | None = None) -> str:
    lang = normalize_language_code(language)
    if lang == "zh":
        return (
            "\n对战难度控制补充：duel 模式下你可以在台词后另起一行输出 "
            "{\"difficulty\":\"max|lv2|lv3|lv4\"}。"
            "max=最强/认真压制，lv2=强但略慢，lv3=明显放水，lv4=最弱/主要防守。"
            "只在局势、情绪或 balanceHint 需要时调整；spectator 不使用 difficulty。\n"
        )
    return (
        "\nDuel difficulty control addendum: in duel mode, you may output "
        "{\"difficulty\":\"max|lv2|lv3|lv4\"} on a separate line after the spoken line. "
        "max=strongest/serious pressure, lv2=strong but slightly slower, "
        "lv3=clear soft play, lv4=weakest/mostly defensive. Adjust only when the "
        "score, emotion, or balanceHint calls for it; spectator does not use difficulty.\n"
    )


def _build_soccer_balance_hint(event: Any) -> Dict[str, Any]:
    """Generate a soft hint from the score: reminds the LLM of the game state without making the control decision for it."""
    if not isinstance(event, dict):
        return {}

    score = event.get('score') or {}
    if not isinstance(score, dict):
        score = {}

    try:
        score_diff = int(event.get('scoreDiff', int(score.get('ai', 0)) - int(score.get('player', 0))))
    except (TypeError, ValueError):
        return {}

    abs_diff = abs(score_diff)
    if abs_diff < 3:
        return {
            'state': 'close_game',
            'scoreDiff': score_diff,
            'intensity': 'low',
            'message': '比分接近，通常可以自由发挥，不需要为了平衡而控制难度。',
        }

    ai_leading = score_diff > 0
    if abs_diff >= 10:
        intensity = 'extreme'
    elif abs_diff >= 6:
        intensity = 'high'
    else:
        intensity = 'medium'

    if ai_leading:
        return {
            'state': 'ai_leading',
            'scoreDiff': score_diff,
            'intensity': intensity,
            'suggestion': 'consider_easing',
            'recommendedDifficulty': 'lv4' if abs_diff >= 10 else 'lv3',
            'message': (
                '你已经明显领先玩家。可以考虑放水、逗玩家、撒娇、故意失误、降低难度，'
                '但如果你有明确情绪或关系理由，也可以继续压制；请在台词里表达原因。'
            ),
        }

    return {
        'state': 'player_leading',
        'scoreDiff': score_diff,
        'intensity': intensity,
        'suggestion': 'consider_trying_harder',
        'recommendedDifficulty': 'max' if abs_diff >= 6 else 'lv2',
        'message': '玩家明显领先你。可以考虑认真起来、提高难度、表现胜负欲或不甘心。',
    }


def _soccer_context_text_blob(value: Any) -> str:
    parts: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            text = _soccer_context_text_blob(item)
            if text:
                parts.append(text)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            text = _soccer_context_text_blob(item)
            if text:
                parts.append(text)
    elif value is not None:
        text = str(value).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _soccer_anger_pressure_cap_applicable(pre_game_context: Any) -> bool:
    if not isinstance(pre_game_context, dict):
        return False
    stance = str(pre_game_context.get("gameStance") or "").strip()
    if stance == "punishing":
        return True

    emotion_values = {
        str(pre_game_context.get("nekoEmotion") or "").strip(),
        str(pre_game_context.get("initialMood") or "").strip(),
    }
    if "angry" not in emotion_values:
        return False

    if stance == "withdrawn":
        return True

    text_blob = _soccer_context_text_blob(pre_game_context).lower()
    anger_context_keywords = (
        "生气", "发火", "愤怒", "爆发", "惩罚", "教训", "报复", "泄愤",
        "冷战", "冲突", "关系修复", "哄", "道歉", "补偿", "赔偿",
        "angry", "punish", "punishment", "repair", "apology", "compensation",
        "cold_war",
    )
    return any(keyword in text_blob for keyword in anger_context_keywords)


def _soccer_anger_pressure_cap_goals(pre_game_context: Any, lanlan_prompt: str = "") -> int:
    text_blob = f"{_soccer_context_text_blob(pre_game_context)} {lanlan_prompt}".lower()
    weak_keywords = (
        "不擅长运动", "运动差", "体力弱", "体弱", "虚弱", "病弱", "容易累",
        "缺乏运动", "宅", "懒得动", "weak", "frail", "sickly",
    )
    strong_keywords = (
        "擅长运动", "运动神经", "体育", "足球", "体力强", "耐力好", "精力充沛",
        "敏捷", "athletic", "sporty", "stamina", "energetic",
    )
    if any(keyword in text_blob for keyword in weak_keywords):
        return _SOCCER_ANGER_PRESSURE_CAP_WEAK
    if any(keyword in text_blob for keyword in strong_keywords):
        return _SOCCER_ANGER_PRESSURE_CAP_STRONG
    return _SOCCER_ANGER_PRESSURE_CAP_DEFAULT


def _build_soccer_anger_pressure_cap(
    event: Any,
    route_state: Any,
    *,
    lanlan_prompt: str = "",
    language: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(event, dict) or not isinstance(route_state, dict):
        return {}
    pre_game_context = route_state.get("preGameContext")
    if not _soccer_anger_pressure_cap_applicable(pre_game_context):
        return {}

    score = event.get("score") if isinstance(event.get("score"), dict) else {}
    try:
        ai_goals = int(score.get("ai", 0))
        player_goals = int(score.get("player", 0))
    except (TypeError, ValueError):
        return {}
    try:
        score_diff = int(event.get("scoreDiff", ai_goals - player_goals))
    except (TypeError, ValueError):
        score_diff = ai_goals - player_goals

    cap_goals = _soccer_anger_pressure_cap_goals(pre_game_context, lanlan_prompt)
    recommended_difficulty = "lv4" if score_diff >= 10 or ai_goals >= cap_goals + 5 else "lv3"
    reached = ai_goals >= cap_goals and score_diff > 0
    return {
        "applicable": True,
        "reached": reached,
        "capGoals": cap_goals,
        "aiGoals": ai_goals,
        "playerGoals": player_goals,
        "scoreDiff": score_diff,
        "recommendedDifficulty": recommended_difficulty,
        "message": get_soccer_anger_pressure_cap_message(language),
        "reason": get_soccer_anger_pressure_cap_reason(language),
    }


def _event_current_difficulty(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    difficulty = str(event.get("difficulty") or "").strip()
    if difficulty:
        return difficulty
    current_state = event.get("currentState")
    if isinstance(current_state, dict):
        return str(current_state.get("difficulty") or "").strip()
    return ""


def _apply_soccer_anger_pressure_cap(result: Dict[str, Any], event: Any) -> Dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(event, dict):
        return result
    cap = event.get("angerPressureCap") if isinstance(event.get("angerPressureCap"), dict) else {}
    if not cap or cap.get("reached") is not True:
        if cap:
            result["anger_pressure_cap"] = dict(cap, adjusted=False)
        return result

    control = dict(result.get("control") or {})
    requested_difficulty = str(control.get("difficulty") or "").strip()
    current_difficulty = _event_current_difficulty(event)
    should_clamp = requested_difficulty == "max" or (not requested_difficulty and current_difficulty == "max")
    adjusted = False
    if should_clamp:
        control["difficulty"] = str(cap.get("recommendedDifficulty") or "lv3")
        existing_reason = str(control.get("reason") or "").strip()
        cap_reason = str(cap.get("reason") or "").strip() or get_soccer_anger_pressure_cap_reason()
        if existing_reason:
            control["reason"] = f"{existing_reason}；{cap_reason}"
        elif event.get("requestControlReason") is True:
            control["reason"] = cap_reason
        result["control"] = control
        adjusted = True

    result["anger_pressure_cap"] = dict(cap, adjusted=adjusted)
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_badminton_duel_balance_hint(event: Any) -> Dict[str, Any]:
    """Build a soft balance hint from the Badminton duel score."""
    if not isinstance(event, dict):
        return {}
    current_state = event.get("currentState") if isinstance(event.get("currentState"), dict) else {}
    state_duel = _sanitize_badminton_duel_state(current_state.get("duel")) or {}
    event_duel = _sanitize_badminton_duel_state(event.get("duel")) or {}
    duel = {**state_duel, **event_duel}
    player_score = _safe_int(duel.get("player_score"), 0)
    neko_score = _safe_int(duel.get("neko_score"), 0)
    max_rounds = _safe_int(duel.get("max_rounds"), 0)
    round_num = _safe_int(duel.get("round"), 0)
    remaining_rounds = max(0, max_rounds - round_num) if max_rounds else 999
    max_misses = _safe_int(duel.get("max_misses"), 0)
    player_misses = _safe_int(duel.get("player_misses"), 0)
    neko_misses = _safe_int(duel.get("neko_misses"), 0)
    player_misses_left = max(0, max_misses - player_misses) if max_misses else 999
    neko_misses_left = max(0, max_misses - neko_misses) if max_misses else 999
    miss_context = (
        {
            "playerMisses": player_misses,
            "nekoMisses": neko_misses,
            "maxMisses": max_misses,
            "playerMissesLeft": player_misses_left,
            "nekoMissesLeft": neko_misses_left,
        }
        if max_misses
        else {}
    )

    diff = neko_score - player_score
    active_shooter = str(duel.get("active_shooter") or "").strip().lower()
    trailing_shooter = "player" if diff > 0 else ("neko" if diff < 0 else "")
    pending_shot_points = 2 if active_shooter == trailing_shooter else 0
    remaining_points = remaining_rounds * 2 + pending_shot_points if max_rounds else 999
    abs_diff = abs(diff)
    if max_misses and player_misses_left <= 0:
        return {
            "state": "player_eliminated",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "玩家已经三次投丢，按淘汰规则收尾。",
        }
    if max_misses and neko_misses_left <= 0:
        return {
            "state": "neko_eliminated",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "Neko 已经三次投丢，按淘汰规则收尾。",
        }
    if abs_diff <= 1:
        return {
            "state": "close",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "比分接近，自由发挥。",
        }

    neko_leading = diff > 0
    if not max_misses and neko_leading and diff > remaining_points:
        return {
            "state": "neko_leading_decided",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "已经锁定胜局，可以嘴硬庆祝或温和收尾。",
        }
    if not max_misses and not neko_leading and abs_diff > remaining_points:
        return {
            "state": "player_leading_decided",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "low",
            "message": "确定输定了，可以不服气、认输或要求再来一局。",
        }
    if neko_leading:
        return {
            "state": "neko_leading",
            "diff": diff,
            "remainingRounds": remaining_rounds,
            "remainingPoints": remaining_points,
            **miss_context,
            "intensity": "high" if abs_diff >= 5 else "medium",
            "message": "你领先中。可以考虑放水搞笑，也可以继续认真；台词里表达原因。",
        }
    return {
        "state": "player_leading",
        "diff": diff,
        "remainingRounds": remaining_rounds,
        "remainingPoints": remaining_points,
        **miss_context,
        "intensity": "high" if abs_diff >= 5 else "medium",
        "message": "玩家领先中。可以认真起来、不服气、要求重赛。",
    }


def _badminton_duel_event_shooter(event: dict) -> str:
    label = str(event.get("label") or "").lower()
    if "neko" in label:
        return "neko"
    if "player" in label:
        return "player"
    duel = event.get("duel") if isinstance(event.get("duel"), dict) else {}
    shooter = str(duel.get("active_shooter") or event.get("active_shooter") or "").strip().lower()
    return shooter if shooter in {"neko", "player"} else ""


def _build_badminton_duel_anger_pressure_cap(
    event: Any,
    route_state: Any,
    *,
    lanlan_prompt: str = "",
    language: str | None = None,
) -> Dict[str, Any]:
    """Build the anger pressure cap for angry and punishing duel mode."""
    if not isinstance(event, dict) or not isinstance(route_state, dict):
        return {}
    pre_game = route_state.get("preGameContext")
    if not isinstance(pre_game, dict):
        return {}

    stance = str(pre_game.get("gameStance") or "").strip()
    initial_mood = str(pre_game.get("initialMood") or "calm").strip()
    if not (stance == "punishing" and initial_mood == "angry"):
        return {}

    accumulated = _safe_int(route_state.get("anger_pressure_accumulated"), 0)
    kind = str(event.get("kind") or "").strip()
    shot_type = str(event.get("shot_type") or "").strip()
    shooter = _badminton_duel_event_shooter(event)
    duel = event.get("duel") if isinstance(event.get("duel"), dict) else {}
    diff = _safe_int(duel.get("neko_score"), 0) - _safe_int(duel.get("player_score"), 0)

    if kind in {"shot_result", "shot_missed"} and shooter == "neko":
        if kind == "shot_result":
            hit_streak = _safe_int(route_state.get("badminton_neko_hit_streak"), 0) + 1
            route_state["badminton_neko_hit_streak"] = hit_streak
            if hit_streak == 5:
                accumulated += 1
            if hit_streak == 10:
                accumulated += 2
            if _normalize_badminton_shot_type(shot_type) == "line_in":
                accumulated += 2
        else:
            route_state["badminton_neko_hit_streak"] = 0
    elif kind == "shot_missed" and shooter == "player":
        accumulated += 1

    prev_diff = _safe_int(route_state.get("anger_pressure_last_diff"), 0)
    if diff >= 5 and prev_diff < 5:
        accumulated += 3
    route_state["anger_pressure_last_diff"] = diff
    route_state["anger_pressure_accumulated"] = accumulated

    if accumulated < _SOCCER_ANGER_PRESSURE_CAP_WEAK:
        return {}

    cap = _soccer_anger_pressure_cap_goals(pre_game, lanlan_prompt)
    reached = accumulated >= cap
    return {
        "applicable": True,
        "accumulated": accumulated,
        "cap": cap,
        "reached": reached,
        "recommendedDifficulty": "lv3",
        "message": get_soccer_anger_pressure_cap_message(language),
        "reason": get_soccer_anger_pressure_cap_reason(language),
    }


def _apply_badminton_anger_pressure_cap(result: Dict[str, Any], event: Any) -> Dict[str, Any]:
    """Apply duel pressure cap post-processing to avoid continued max pressure."""
    if not isinstance(result, dict) or not isinstance(event, dict):
        return result
    cap = event.get("angerPressureCap") if isinstance(event.get("angerPressureCap"), dict) else {}
    if not cap or cap.get("reached") is not True:
        if cap:
            result["anger_pressure_cap"] = dict(cap, adjusted=False)
        return result

    control = dict(result.get("control") or {})
    requested_difficulty = str(control.get("difficulty") or "").strip()
    current_difficulty = _event_current_difficulty(event)
    should_clamp = requested_difficulty == "max" or (not requested_difficulty and current_difficulty == "max")
    adjusted = False
    if should_clamp:
        control["difficulty"] = str(cap.get("recommendedDifficulty") or "lv3")
        existing_reason = str(control.get("reason") or "").strip()
        cap_reason = str(cap.get("reason") or "").strip() or get_soccer_anger_pressure_cap_reason()
        control["reason"] = f"{existing_reason}；{cap_reason}" if existing_reason else cap_reason
        result["control"] = control
        adjusted = True
    result["anger_pressure_cap"] = dict(cap, adjusted=adjusted)
    return result
