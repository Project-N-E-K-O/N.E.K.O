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

"""Runtime adapter for user-initiated music playback."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any

from config.prompts.prompts_proactive import get_music_request_pending_prompt
from main_logic.agent_event_bus import register_user_utterance_sink
from main_logic.proactive_delivery import DELIVERY_RETRACTED_KEY
from main_logic.music_requests import (
    MusicRequest,
    SingCoverRequest,
    fetch_music_request,
    is_explicit_music_cancellation,
    mark_music_request_query,
    parse_explicit_sing_cover_request,
    parse_explicit_user_music_request,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_session_manager_getter: Callable[[str], Any | None] | None = None
_PLAYBACK_STATES = frozenset({"playing", "paused", "ended", "error"})
_REPLY_START_GRACE_SECONDS = 1.0
_REPLY_WAIT_TIMEOUT_SECONDS = 5.0
_REPLY_WAIT_POLL_SECONDS = 0.05
_RVC_COVER_PLUGIN_ID = "rvc_cover"
_RVC_COVER_ENTRY_ID = "sing_cover"


def register_music_session_manager_getter(
    getter: Callable[[str], Any | None],
) -> None:
    global _session_manager_getter
    _session_manager_getter = getter


def queue_user_music_request(manager: Any, request: MusicRequest) -> int:
    """Cancel any in-flight search and queue networked playback. Returns request_id."""
    previous_task = getattr(manager, "_music_request_task", None)
    if previous_task is not None and not previous_task.done():
        previous_task.cancel()
    epoch = _next_music_request_epoch(manager)
    _enqueue_music_request_context(manager, epoch)
    fire_task = getattr(manager, "_fire_task", None)
    if callable(fire_task):
        manager._music_request_task = fire_task(
            _execute_music_request(manager, request, epoch)
        )
    return epoch


async def cancel_user_music_playback(manager: Any) -> int:
    """Cancel in-flight search and tell the frontend to stop playback."""
    previous_task = getattr(manager, "_music_request_task", None)
    if previous_task is not None and not previous_task.done():
        previous_task.cancel()
    epoch = _next_music_request_epoch(manager)
    pending_context = getattr(manager, "_music_request_pending_context", None)
    if isinstance(pending_context, dict):
        pending_context[DELIVERY_RETRACTED_KEY] = True
        manager._music_request_pending_context = None
    await _push_music_payload(
        manager,
        {
            "type": "music_request_cancelled",
            "request_id": epoch,
        },
    )
    return epoch


async def control_user_music_playback(manager: Any, action: str) -> dict[str, Any]:
    """Map control_music tool actions onto cancel / frontend transport events."""
    normalized = str(action or "").strip().lower()
    if normalized == "stop":
        request_id = await cancel_user_music_playback(manager)
        return {"status": "ok", "action": "stop", "request_id": request_id}
    if normalized in {"pause", "resume"}:
        delivered = await _push_music_payload(
            manager,
            {
                "type": "music_control",
                "action": normalized,
            },
        )
        return {
            "status": "ok" if delivered else "playback_unavailable",
            "action": normalized,
        }
    if normalized == "next":
        return {
            "status": "unsupported",
            "action": "next",
            "message": "next track is not supported for networked playback yet",
        }
    return {
        "status": "unsupported",
        "action": normalized,
        "message": f"unknown music control action: {normalized}",
    }


def _plugin_server_origin() -> str:
    import os

    for key in (
        "NEKO_PLUGIN_SERVER_ORIGIN",
        "NEKO_USER_PLUGIN_SERVER_ORIGIN",
        "NEKO_SERVER_ORIGIN",
    ):
        val = str(os.getenv(key, "") or "").strip().rstrip("/")
        if val.startswith("http://") or val.startswith("https://"):
            return val
    try:
        env_port = int(str(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "") or "").strip())
        if 1 <= env_port <= 65535:
            return f"http://127.0.0.1:{env_port}"
    except Exception:
        pass
    try:
        from config import USER_PLUGIN_SERVER_PORT

        port = int(USER_PLUGIN_SERVER_PORT)
        if 1 <= port <= 65535:
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass
    return "http://127.0.0.1:48916"


def _resolve_rvc_model_hint(hint: str) -> str:
    text = str(hint or "").strip()
    if not text:
        return ""
    if text.lower().endswith(".pth"):
        return text
    try:
        from pathlib import Path

        weights = Path(__file__).resolve().parents[1] / "vendor" / "rvc" / "assets" / "weights"
        if not weights.is_dir():
            return ""
        needle = text.casefold()
        for path in sorted(weights.glob("*.pth")):
            stem = path.stem.casefold()
            if needle in stem or stem in needle:
                return path.name
    except Exception:
        return ""
    return ""


_RVC_COVER_RUN_POLL_SECONDS = 0.4
_RVC_COVER_RUN_TIMEOUT_SECONDS = 45.0
_RVC_COVER_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "canceled", "timeout"}
)


def _unwrap_run_export_value(raw: Any) -> Any:
    """Unwrap nested Run Protocol export payloads (json / data / value / error)."""
    current = raw
    while (
        isinstance(current, dict)
        and "data" in current
        and isinstance(current.get("data"), dict)
        and any(key in current["data"] for key in ("success", "error", "value"))
    ):
        current = current["data"]
    if isinstance(current, dict) and isinstance(current.get("value"), dict):
        return current["value"]
    return current


async def trigger_rvc_cover_plugin(
    request: SingCoverRequest,
    *,
    target_lanlan: str = "",
) -> dict[str, Any]:
    """HTTP-trigger the rvc_cover plugin via Run Protocol (uses vendor/rvc)."""
    import httpx

    args: dict[str, Any] = {
        "query": request.display_query or request.query,
        "song": request.song_name,
        "artist": request.song_artist,
        "target_lanlan": target_lanlan,
    }
    model_name = _resolve_rvc_model_hint(request.model_hint) or str(
        request.model_hint or ""
    ).strip()
    if model_name:
        args["model_name"] = model_name
    origin = _plugin_server_origin()
    payload = {
        "plugin_id": _RVC_COVER_PLUGIN_ID,
        "entry_id": _RVC_COVER_ENTRY_ID,
        "args": args,
    }
    try:
        timeout = httpx.Timeout(15.0, connect=3.0)
        async with httpx.AsyncClient(
            timeout=timeout, proxy=None, trust_env=False
        ) as client:
            resp = await client.post(f"{origin}/runs", json=payload)
            body: dict[str, Any] = {}
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    body = parsed
            except Exception:
                body = {"raw": (resp.text or "")[:300]}
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "error": "trigger_http_error",
                    "status_code": resp.status_code,
                    "body": body,
                }
            run_id = str(body.get("run_id") or body.get("id") or "").strip()
            if not run_id:
                return {
                    "ok": False,
                    "error": "missing_run_id",
                    "status_code": resp.status_code,
                    "body": body,
                }

            deadline = asyncio.get_running_loop().time() + _RVC_COVER_RUN_TIMEOUT_SECONDS
            last_status = str(body.get("status") or "")
            while True:
                if last_status in _RVC_COVER_TERMINAL_STATUSES:
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return {
                        "ok": False,
                        "error": "run_timeout",
                        "run_id": run_id,
                        "status": last_status or "timeout",
                    }
                await asyncio.sleep(
                    min(_RVC_COVER_RUN_POLL_SECONDS, max(0.05, remaining))
                )
                poll = await client.get(f"{origin}/runs/{run_id}")
                if poll.status_code in {404, 410}:
                    return {
                        "ok": False,
                        "error": "run_not_found",
                        "status_code": poll.status_code,
                        "run_id": run_id,
                    }
                if poll.status_code != 200:
                    continue
                try:
                    run_body = poll.json()
                except Exception:
                    continue
                if isinstance(run_body, dict):
                    last_status = str(run_body.get("status") or last_status)

            if last_status != "succeeded":
                return {
                    "ok": False,
                    "error": "run_failed",
                    "run_id": run_id,
                    "status": last_status,
                    "body": body,
                }

            export_body: dict[str, Any] = {}
            try:
                exported = await client.get(f"{origin}/runs/{run_id}/export")
                if exported.status_code == 200:
                    parsed_export = exported.json()
                    if isinstance(parsed_export, dict):
                        export_body = parsed_export
            except Exception:
                export_body = {}

            items = export_body.get("items") if isinstance(export_body, dict) else None
            value: Any = {}
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "json" and item.get("json") is not None:
                        value = _unwrap_run_export_value(item.get("json"))
                        break
                if not value and items:
                    first = items[0]
                    if isinstance(first, dict):
                        value = _unwrap_run_export_value(
                            first.get("json") if "json" in first else first
                        )

            if isinstance(value, dict) and value.get("error"):
                return {
                    "ok": False,
                    "error": "entry_error",
                    "run_id": run_id,
                    "body": value,
                    "message": str(
                        value.get("message")
                        or value.get("error")
                        or "sing_cover failed"
                    ),
                }
            if isinstance(value, dict) and value.get("ok") is False:
                return {
                    "ok": False,
                    "error": str(value.get("error") or "sing_cover_failed"),
                    "run_id": run_id,
                    "body": value,
                    "message": str(value.get("message") or ""),
                }
            return {
                "ok": True,
                "run_id": run_id,
                "status": last_status,
                "body": value if isinstance(value, dict) else {"value": value},
            }
    except Exception as exc:
        logger.warning("rvc_cover trigger failed: %s", exc)
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def _enqueue_sing_cover_pending_context(manager: Any, query: str) -> None:
    enqueue = getattr(manager, "enqueue_agent_callback", None)
    if not callable(enqueue):
        return
    title = f"「{query}」" if query else "这首歌"
    detail = (
        f"用户已明确要求翻唱{title}。"
        "RVC 翻唱任务已开始联网搜歌并转换音色，请简短确认正在准备翻唱，"
        "不要再改口成普通点歌，也不要假装已经唱完。"
    )
    enqueue(
        {
            "event": "agent_task_callback",
            "origin": "event",
            "task_id": f"rvc_cover:{int(getattr(manager, '_music_request_epoch', 0) or 0)}",
            "channel": "music_playback",
            "status": "in_progress",
            "success": True,
            "summary": detail,
            "detail": detail,
            "source_kind": "music",
            "source_name": "rvc_cover",
            "delivery_mode": "passive",
            "priority": 10,
            "coalesce_key": f"rvc-cover:{getattr(manager, 'lanlan_name', '')}",
            "metadata": {"context_type": "rvc_cover_pending", "query": query},
            "context_type": "rvc_cover_pending",
        }
    )


def _enqueue_sing_cover_failure_context(
    manager: Any,
    query: str,
    detail: str,
) -> None:
    enqueue = getattr(manager, "enqueue_agent_callback", None)
    if not callable(enqueue):
        return
    text = detail or "RVC 翻唱触发失败"
    if query:
        text = f"{text}（{query}）"
    enqueue(
        {
            "event": "agent_task_callback",
            "origin": "event",
            "task_id": f"rvc_cover:{int(getattr(manager, '_music_request_epoch', 0) or 0)}",
            "channel": "music_playback",
            "status": "failed",
            "success": False,
            "summary": text,
            "detail": text,
            "source_kind": "music",
            "source_name": "rvc_cover",
            "delivery_mode": "passive",
            "priority": 10,
            "coalesce_key": f"rvc-cover:{getattr(manager, 'lanlan_name', '')}",
            "metadata": {"context_type": "rvc_cover_failed", "query": query},
            "context_type": "rvc_cover_failed",
        }
    )


async def _execute_sing_cover_request(
    manager: Any,
    request: SingCoverRequest,
) -> dict[str, Any]:
    query = request.display_query
    result = await trigger_rvc_cover_plugin(
        request,
        target_lanlan=str(getattr(manager, "lanlan_name", "") or ""),
    )
    if result.get("ok"):
        _enqueue_sing_cover_pending_context(manager, query)
    else:
        _enqueue_sing_cover_failure_context(
            manager,
            query,
            "RVC 翻唱插件未就绪或触发失败，请确认 rvc_cover 已启用",
        )
    return result


def _on_user_utterance(bucket: str, event: dict[str, Any]) -> None:
    lanlan_name = str(event.get("lanlan") or "")
    if not lanlan_name or bucket != lanlan_name or _session_manager_getter is None:
        return
    manager = _session_manager_getter(lanlan_name)
    if manager is None:
        return
    content = str(event.get("content") or "")
    cover_request = parse_explicit_sing_cover_request(content)
    if cover_request is not None:
        fire_task = getattr(manager, "_fire_task", None)
        if callable(fire_task):
            fire_task(_execute_sing_cover_request(manager, cover_request))
        return
    request = parse_explicit_user_music_request(content)
    if request is None:
        if is_explicit_music_cancellation(content):
            fire_task = getattr(manager, "_fire_task", None)
            if callable(fire_task):
                fire_task(cancel_user_music_playback(manager))
        return
    queue_user_music_request(manager, request)


def _next_music_request_epoch(manager: Any) -> int:
    epoch = int(getattr(manager, "_music_request_epoch", 0) or 0) + 1
    manager._music_request_epoch = epoch
    return epoch


def _is_current_music_request(manager: Any, epoch: int) -> bool:
    return int(getattr(manager, "_music_request_epoch", 0) or 0) == epoch


def _enqueue_music_request_context(
    manager: Any,
    epoch: int,
) -> None:
    enqueue = getattr(manager, "enqueue_agent_callback", None)
    if not callable(enqueue):
        return
    detail = get_music_request_pending_prompt(
        getattr(manager, "user_language", None)
    )
    callback = {
        "event": "agent_task_callback",
        "origin": "event",
        "task_id": f"music_request:{epoch}",
        "channel": "music_playback",
        "status": "in_progress",
        "success": True,
        "summary": detail,
        "detail": detail,
        "source_kind": "music",
        "source_name": "music_request",
        "delivery_mode": "passive",
        "priority": 10,
        "coalesce_key": (
            f"music-playback-state:{getattr(manager, 'lanlan_name', '')}"
        ),
        "metadata": {
            "context_type": "music_request_pending",
            "request_id": epoch,
        },
        "context_type": "music_request_pending",
    }
    enqueue(callback)
    manager._music_request_pending_context = callback


def _enqueue_music_request_failure_context(
    manager: Any,
    epoch: int,
    query: str,
    error_code: str,
) -> None:
    enqueue = getattr(manager, "enqueue_agent_callback", None)
    if not callable(enqueue):
        return
    detail = f"音乐请求未能完成（{error_code}）"
    if query:
        detail += f"：{query}"
    enqueue(
        {
            "event": "agent_task_callback",
            "origin": "event",
            "task_id": f"music_request:{epoch}",
            "channel": "music_playback",
            "status": "failed",
            "success": False,
            "summary": detail,
            "detail": detail,
            "source_kind": "music",
            "source_name": "music_request",
            "delivery_mode": "passive",
            "priority": 10,
            "coalesce_key": (
                f"music-playback-state:{getattr(manager, 'lanlan_name', '')}"
            ),
            "metadata": {
                "context_type": "music_request_failed",
                "request_id": epoch,
                "error_code": error_code,
            },
            "context_type": "music_request_failed",
        }
    )


def _reply_in_progress(manager: Any) -> bool:
    if getattr(manager, "_active_text_request_id", None):
        return True
    if bool(getattr(manager, "_voice_playback_active", False)):
        return True
    session = getattr(manager, "session", None)
    is_active_response = getattr(session, "is_active_response", None)
    if callable(is_active_response):
        try:
            return bool(is_active_response())
        except Exception:
            return False
    return False


async def _wait_for_current_reply(
    manager: Any,
    epoch: int,
    search_elapsed_seconds: float,
) -> None:
    if not _reply_in_progress(manager):
        grace = _REPLY_START_GRACE_SECONDS - search_elapsed_seconds
        if grace > 0:
            await asyncio.sleep(grace)

    deadline = asyncio.get_running_loop().time() + _REPLY_WAIT_TIMEOUT_SECONDS
    while (
        _is_current_music_request(manager, epoch)
        and _reply_in_progress(manager)
    ):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(_REPLY_WAIT_POLL_SECONDS, remaining))


def _clean_playback_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_playback_started_at(value: Any) -> float | None:
    try:
        started_at = float(value)
    except (TypeError, ValueError):
        return None
    return started_at if math.isfinite(started_at) and started_at > 0 else None


def _clean_music_request_id(value: Any) -> int | None:
    try:
        request_id = int(value)
    except (TypeError, ValueError):
        return None
    return request_id if request_id > 0 else None


def handle_music_playback_state(manager: Any, event: dict[str, Any]) -> bool:
    """Feed a player-confirmed state into the existing callback delivery path."""
    state = _clean_playback_text(event.get("state"), 16).lower()
    if state not in _PLAYBACK_STATES:
        return False

    track = event.get("track")
    track = track if isinstance(track, dict) else {}
    name = _clean_playback_text(track.get("name"), 120)
    artist = _clean_playback_text(track.get("artist"), 120)
    playback_id = _clean_playback_text(event.get("playback_id"), 512)
    playback_window_id = _clean_playback_text(
        event.get("playback_window_id"), 128
    )
    playback_started_at = _clean_playback_started_at(
        event.get("playback_started_at")
    )
    request_id = _clean_playback_text(event.get("request_id"), 64)
    source = _clean_playback_text(event.get("source"), 16).lower()
    if not playback_id or not playback_window_id or playback_started_at is None:
        return False

    owner_key = (playback_window_id, playback_id)
    current_owner_key = getattr(manager, "_music_playback_owner_key", None)
    current_started_at = getattr(manager, "_music_playback_owner_started_at", None)
    is_current_owner = (
        owner_key == current_owner_key
        and playback_started_at == current_started_at
    )
    current_request_epoch = getattr(manager, "_music_request_epoch", None)
    if request_id and current_request_epoch is not None:
        if request_id != str(current_request_epoch) and not is_current_owner:
            return False
    elif source == "user":
        return False

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

    event_key = (playback_id, request_id, state, playback_started_at)
    if getattr(manager, "_music_playback_event_key", None) == event_key:
        return False
    manager._music_playback_event_key = event_key

    title = f"《{name}》" if name else "所选歌曲"
    by_artist = f"（{artist}）" if artist else ""
    facts = {
        "playing": f"播放器已确认开始播放{title}{by_artist}。",
        "paused": f"播放器当前已暂停{title}{by_artist}。",
        "ended": f"播放器已结束播放{title}{by_artist}。",
        "error": f"播放器未能正常播放{title}{by_artist}。",
    }
    detail = facts[state]
    acknowledge_key = (playback_id, request_id)
    should_respond = (
        state == "playing"
        and source == "user"
        and getattr(manager, "_music_playback_acknowledged_key", None)
        != acknowledge_key
    )
    if should_respond:
        manager._music_playback_acknowledged_key = acknowledge_key
        detail += " 请简短自然地确认已经开始播放，不要再次调用音乐播放工具。"

    callback = {
        "event": "agent_task_callback",
        "origin": "event",
        "task_id": playback_id or request_id or "music_playback",
        "channel": "music_playback",
        "status": "completed",
        "success": state != "error",
        "summary": detail,
        "detail": detail,
        "source_kind": "music",
        "source_name": "music_player",
        "delivery_mode": "proactive" if should_respond else "passive",
        "priority": 10,
        "coalesce_key": f"music-playback-state:{getattr(manager, 'lanlan_name', '')}",
        "metadata": {
            "context_type": "music_playback",
            "state": state,
            "playback_id": playback_id,
            "playback_window_id": playback_window_id,
            "playback_started_at": playback_started_at,
            "request_id": request_id,
        },
        "context_type": "music_playback",
    }

    if should_respond and callable(getattr(manager, "submit_proactive_callback", None)):
        manager.submit_proactive_callback(
            callback,
            priority=callback["priority"],
            coalesce_key=callback["coalesce_key"],
        )
        return True

    enqueue = getattr(manager, "enqueue_agent_callback", None)
    if not callable(enqueue):
        return False
    enqueue(callback)
    if should_respond:
        trigger = getattr(manager, "trigger_agent_callbacks", None)
        fire_task = getattr(manager, "_fire_task", None)
        if callable(trigger) and callable(fire_task):
            fire_task(trigger())
    return True


def handle_music_request_playback_failed(
    manager: Any,
    event: dict[str, Any],
) -> bool:
    """Replace a pending request cue after all browser candidates fail."""
    request_id = _clean_music_request_id(event.get("request_id"))
    if request_id is None or not _is_current_music_request(manager, request_id):
        return False
    _enqueue_music_request_failure_context(
        manager,
        request_id,
        "",
        "playback_failed",
    )
    return True


async def _execute_music_request(
    manager: Any,
    request: MusicRequest,
    epoch: int,
) -> dict:
    await _push_music_payload(
        manager,
        {
            "type": "music_request_started",
            "request_id": epoch,
        },
    )
    if not _is_current_music_request(manager, epoch):
        return {"status": "superseded"}
    loop = asyncio.get_running_loop()
    search_started_at = loop.time()
    result = await fetch_music_request(
        request,
        limit=5,
        source_locale=getattr(manager, "user_language", None),
        include_failure=True,
        bypass_recommendation_dedupe=True,
    )
    if not _is_current_music_request(manager, epoch):
        return {"status": "superseded"}
    if result and result.get("success") and result.get("data"):
        mark_music_request_query(getattr(manager, "lanlan_name", ""), request)

    tracks = result.get("data", []) if result else []
    candidates = [
        {
            "name": track.get("name", ""),
            "artist": track.get("artist", ""),
            "url": track.get("url", ""),
            "cover": track.get("cover", ""),
            "source": track.get("source", ""),
        }
        for track in tracks
        if isinstance(track, dict) and track.get("url")
    ][:3]
    if not candidates:
        error_code = str((result or {}).get("error_code") or "track_not_found")
        _enqueue_music_request_failure_context(
            manager,
            epoch,
            request.display_query,
            error_code,
        )
        await _send_music_request_failure(
            manager,
            request.display_query,
            error_code,
            epoch,
        )
        return {
            "status": "failed",
            "reason": error_code,
            "query": request.display_query,
        }

    payload = {
        "type": "music_play_candidates",
        "request_id": epoch,
        "tracks": candidates,
    }
    await _wait_for_current_reply(
        manager,
        epoch,
        loop.time() - search_started_at,
    )
    if not _is_current_music_request(manager, epoch):
        return {"status": "superseded"}
    if await _push_music_payload(manager, payload):
        return {
            "status": "queued",
            "candidates": len(candidates),
        }
    _enqueue_music_request_failure_context(
        manager,
        epoch,
        request.display_query,
        "playback_unavailable",
    )
    return {"status": "playback_unavailable"}


async def _send_music_request_failure(
    manager: Any,
    query: str,
    error_code: str,
    request_id: int,
) -> None:
    await _push_music_payload(
        manager,
        {
            "type": "music_request_failed",
            "request_id": request_id,
            "query": query,
            "error_code": error_code,
        },
    )


async def _push_music_payload(manager: Any, payload: dict[str, Any]) -> bool:
    websocket = getattr(manager, "websocket", None)
    targets = [websocket]
    broadcast = payload.get("type") in {
        "music_request_started",
        "music_request_cancelled",
        "music_control",
    }
    for candidate in tuple(
        getattr(manager, "_music_playback_websockets", ()) or ()
    ):
        if candidate is not websocket:
            targets.append(candidate)

    delivered = False
    for target in targets:
        if target is None or not hasattr(target, "send_json"):
            continue
        ws_state = getattr(target, "client_state", None)
        if ws_state is not None and ws_state != ws_state.CONNECTED:
            continue
        try:
            await target.send_json(payload)
            delivered = True
            if not broadcast:
                break
        except Exception as exc:
            logger.warning(
                "[%s] user music payload push failed: %s",
                getattr(manager, "lanlan_name", ""),
                exc,
            )

    if delivered:
        manager.sync_message_queue.put({"type": "json", "data": payload})
    return delivered


register_user_utterance_sink(_on_user_utterance)
