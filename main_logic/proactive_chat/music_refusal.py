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

"""Per-character music cooldown driven by explicit refusals.

The player-side cooldown in ``static/jukebox/music_ui.js`` only sees a player
closed right after it started. Recommendation cards are refused before any
playback, and a verbal "stop playing songs" never touches the player, so both
are counted here instead. The proactive request treats an active cooldown the
same way as the frontend ``music_cooldown`` flag.

State lives in memory and is lost on restart, matching the frontend cooldown
which only lasts twenty minutes anyway.
"""

import time
from typing import Any, Dict

from config.prompts.prompts_music_refusal import detect_music_refusal
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# Mirrors SKIP_CONFIG in static/jukebox/music_ui.js.
MUSIC_CARD_SKIPS_TO_TRIGGER = 2
MUSIC_REFUSAL_COOLDOWN_SECONDS = 20 * 60

_music_card_skip_counts: dict[str, int] = {}
_music_refusal_cooldown_until: dict[str, float] = {}


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def enter_music_refusal_cooldown(lanlan_name: str, reason: str, *, now: float | None = None) -> float:
    """Pause music recommendations for ``lanlan_name``; returns the deadline."""
    until = _now(now) + MUSIC_REFUSAL_COOLDOWN_SECONDS
    _music_refusal_cooldown_until[lanlan_name] = until
    _music_card_skip_counts.pop(lanlan_name, None)
    logger.info(
        "[%s] 用户拒绝音乐推荐（%s），音乐推荐暂停 %d 分钟",
        lanlan_name,
        reason,
        MUSIC_REFUSAL_COOLDOWN_SECONDS // 60,
    )
    return until


def is_music_refusal_cooldown_active(lanlan_name: str, *, now: float | None = None) -> bool:
    """Return whether a refusal cooldown currently blocks music for ``lanlan_name``."""
    until = _music_refusal_cooldown_until.get(lanlan_name)
    if until is None:
        return False
    if _now(now) >= until:
        _music_refusal_cooldown_until.pop(lanlan_name, None)
        return False
    return True


def record_music_card_skipped(lanlan_name: str, *, now: float | None = None) -> bool:
    """Count a skipped recommendation card; returns True when it starts a cooldown."""
    count = _music_card_skip_counts.get(lanlan_name, 0) + 1
    if count >= MUSIC_CARD_SKIPS_TO_TRIGGER:
        enter_music_refusal_cooldown(lanlan_name, f"连续跳过 {count} 张音乐推荐卡片", now=now)
        return True
    _music_card_skip_counts[lanlan_name] = count
    logger.info("[%s] 音乐推荐卡片被跳过 #%d", lanlan_name, count)
    return False


def reset_music_card_skips(lanlan_name: str) -> None:
    """Break the consecutive-skip streak after the user took a song."""
    _music_card_skip_counts.pop(lanlan_name, None)


def on_user_utterance(bucket: str, event: Dict[str, Any]) -> None:
    """user_utterance sink: a verbal refusal starts the cooldown right away.

    Same bucket dedup as ``memory.user_directives._on_user_utterance``: one
    message is dispatched to both the "default" bucket and the character bucket.
    """
    if not isinstance(event, dict):
        return
    canonical = event.get("lanlan")
    if not isinstance(canonical, str):
        canonical = ""
    if canonical and canonical != "default":
        if bucket != canonical:
            return
        lanlan_name = canonical
    else:
        if not bucket:
            return
        lanlan_name = bucket
    content = event.get("content")
    if isinstance(content, list):
        content = " ".join(
            part if isinstance(part, str) else str(part.get("text") or "")
            for part in content
            if isinstance(part, (dict, str))
        )
    if not isinstance(content, str) or not detect_music_refusal(content):
        return
    enter_music_refusal_cooldown(lanlan_name, "口头拒绝")
