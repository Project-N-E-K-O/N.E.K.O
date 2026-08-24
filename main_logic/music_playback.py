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

"""Record confirmed music-player state for later conversation context."""

from __future__ import annotations

import math
from typing import Any

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_PLAYBACK_STATES = frozenset({"playing", "paused", "ended", "error"})
_PLAYBACK_FAILURE_REASONS = frozenset(
    {
        "load_timeout",
        "media_error",
        "missing_audio",
        "player_error",
        "track_too_long",
    }
)


def _clean_playback_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_playback_started_at(value: Any) -> float | None:
    try:
        started_at = float(value)
    except (TypeError, ValueError):
        return None
    return started_at if math.isfinite(started_at) and started_at > 0 else None


def handle_music_playback_state(manager: Any, event: dict[str, Any]) -> bool:
    """Store a player-confirmed state without waking the LLM immediately."""
    state = _clean_playback_text(event.get("state"), 16).lower()
    if state not in _PLAYBACK_STATES:
        return False

    track = event.get("track")
    track = track if isinstance(track, dict) else {}
    name = _clean_playback_text(track.get("name"), 120)
    artist = _clean_playback_text(track.get("artist"), 120)
    playback_id = _clean_playback_text(event.get("playback_id"), 512)
    playback_window_id = _clean_playback_text(event.get("playback_window_id"), 128)
    playback_started_at = _clean_playback_started_at(event.get("playback_started_at"))
    failure_reason = _clean_playback_text(event.get("reason"), 32).lower()
    if state != "error":
        failure_reason = ""
    elif failure_reason not in _PLAYBACK_FAILURE_REASONS:
        failure_reason = "unknown"
    if not playback_id or not playback_window_id or playback_started_at is None:
        return False

    owner_key = (playback_window_id, playback_id)
    current_owner_key = getattr(manager, "_music_playback_owner_key", None)
    current_started_at = getattr(manager, "_music_playback_owner_started_at", None)
    if current_started_at is not None and (
        playback_started_at < current_started_at
        or (
            playback_started_at == current_started_at
            and owner_key != current_owner_key
        )
    ):
        return False
    if playback_started_at > (current_started_at or 0):
        manager._music_playback_owner_key = owner_key
        manager._music_playback_owner_started_at = playback_started_at

    event_key = (playback_id, state, playback_started_at)
    if getattr(manager, "_music_playback_event_key", None) == event_key:
        return False
    manager._music_playback_event_key = event_key

    if state == "error":
        logger.warning(
            "[%s] 音乐播放器报告失败: reason=%s",
            getattr(manager, "lanlan_name", "") or "unknown",
            failure_reason,
        )

    title = f"《{name}》" if name else "所选歌曲"
    by_artist = f"（{artist}）" if artist else ""
    facts = {
        "playing": f"播放器已确认开始播放{title}{by_artist}。",
        "paused": f"播放器当前已暂停{title}{by_artist}。",
        "ended": f"播放器已结束播放{title}{by_artist}。",
        "error": f"播放器未能正常播放{title}{by_artist}。",
    }
    detail = facts[state]
    callback = {
        "event": "agent_task_callback",
        "origin": "event",
        "task_id": playback_id or "music_playback",
        "channel": "music_playback",
        "status": "completed",
        "success": state != "error",
        "summary": detail,
        "detail": detail,
        "source_kind": "music",
        "source_name": "music_player",
        "delivery_mode": "passive",
        "priority": 10,
        "coalesce_key": f"music-playback-state:{getattr(manager, 'lanlan_name', '')}",
        "metadata": {
            "context_type": "music_playback",
            "state": state,
            "playback_id": playback_id,
            "playback_window_id": playback_window_id,
            "playback_started_at": playback_started_at,
            "failure_reason": failure_reason,
        },
        "context_type": "music_playback",
    }

    enqueue = getattr(manager, "enqueue_agent_callback", None)
    if not callable(enqueue):
        return False
    enqueue(callback)
    return True
