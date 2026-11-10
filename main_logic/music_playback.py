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

import asyncio
import math
import re
import time
from typing import Any

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_PLAYBACK_STATES = frozenset({"playing", "paused", "ended", "error", "buffering"})
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


_queue_callbacks = set()


async def _deliver_queue_terminal(target, ticket, reason):
    from config.network import USER_PLUGIN_SERVER_PORT
    from utils.internal_http_client import get_internal_http_client

    client = get_internal_http_client()
    for attempt in range(2):
        try:
            response = await client.post(
                f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs",
                json={
                    "plugin_id": "netease_music",
                    "entry_id": "playlist_terminal",
                    "args": {
                        "_ctx": {"lanlan_name": target},
                        "ticket": ticket,
                        "reason": reason,
                    },
                    "idempotency_key": "music-terminal-" + ticket + "-" + reason + "-" + str(attempt),
                },
                timeout=5,
            )
            response.raise_for_status()
            run = response.json()
            # Acceptance is not execution: the plugin may still be starting.
            # Poll within the entry timeout before deciding whether to retry.
            async with asyncio.timeout(95):
                while run.get("status") in ("queued", "running", "cancel_requested"):
                    await asyncio.sleep(0.5)
                    status = await client.get(
                        f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs/{run['run_id']}",
                        timeout=5,
                    )
                    status.raise_for_status()
                    run = status.json()
            if run.get("status") != "succeeded":
                raise RuntimeError("music terminal run did not succeed")
            return
        except Exception as exc:
            if attempt:
                logger.warning("音乐队列回执未送达: %s", type(exc).__name__)
            else:
                await asyncio.sleep(0.25)


def clear_music_playback_connection(manager: Any, websocket: Any) -> None:
    if getattr(manager, "_music_playback_websocket", None) is websocket:
        manager._current_netease_track = None
        manager._music_confirmed_state = None
        manager._music_playback_websocket = None
        manager._music_playback_event_key = None


def handle_music_playback_state(manager: Any, event: dict[str, Any], *, websocket: Any = None) -> bool:
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

    event_key = (playback_id, state, playback_started_at, event.get("queue_closed") is True)
    if (
        getattr(manager, "_music_playback_event_key", None) == event_key
        and (websocket is None or getattr(manager, "_music_playback_websocket", None) is websocket)
    ):
        return False
    manager._music_playback_event_key = event_key

    # Only the window that owns playback can confirm or clear a collectible track.
    song_id = event.get("netease_id")
    manager._current_netease_track = (
        {"song_id": song_id, "name": name, "artist": artist}
        if state in ("playing", "paused", "buffering")
        and event.get("source") == "music_play_url"
        and type(song_id) is int
        and song_id > 0
        and name
        and artist
        else None
    )
    manager._music_playback_websocket = websocket
    manager._music_confirmed_state = {"state": state, "song_id": song_id, "name": name, "artist": artist, "confirmed_at": time.time()}
    ticket = event.get("queue_ticket")
    if event.get("source") == "music_play_url":
        logger.info("Music player confirmed: state=%s queue=%s", state, bool(ticket))
    if (
        event.get("source") == "music_play_url"
        and state in ("ended", "error")
        and isinstance(ticket, str)
        and re.fullmatch("[0-9a-f]{32}", ticket)
    ):
        task = asyncio.create_task(
            _deliver_queue_terminal(
                manager.lanlan_name,
                ticket,
                "closed" if event.get("queue_closed") is True else state,
            )
        )
        _queue_callbacks.add(task)
        task.add_done_callback(_queue_callbacks.discard)

    if state == "error":
        logger.warning(
            "[%s] 音乐播放器报告失败: reason=%s",
            getattr(manager, "lanlan_name", "") or "unknown",
            failure_reason,
        )

    title = f"《{name}》" if name else "所选歌曲"
    by_artist = f"（{artist}）" if artist else ""
    facts = {
        "buffering": f"{title}{by_artist}还在缓冲。",
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
