"""Trusted media library endpoints used exclusively by the official SDK host."""
import asyncio
import math
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from main_logic.watch_together.library import application_library

router = APIRouter(prefix="/api/watch-together", tags=["watch-together"])
# One live-line generation per scene route at a time.
_LIVE_BUSY_KEY = "_watch_live_busy"


def _library_call(method, *args):
    return getattr(application_library(), method)(*args)


@router.get("/history")
async def history(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    def read():
        library = application_library()
        return {**library.history_page(limit, offset), "watches": library.watches()}
    return await asyncio.to_thread(read)


@router.get("/watches")
async def watches(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return await asyncio.to_thread(_library_call, "watch_page", limit, offset)


@router.get("/jobs/{job}/{version}")
async def timeline(job: str, version: str):
    try:
        return await asyncio.to_thread(_library_call, "timeline", job, version)
    except (KeyError, ValueError, OSError):
        raise HTTPException(404, "Timeline unavailable")


@router.get("/media/{job}/{version}/{filename:path}")
async def media(job: str, version: str, filename: str):
    try:
        path = await asyncio.to_thread(_library_call, "resource", job, version, filename)
    except KeyError:
        raise HTTPException(404)
    # Resource names come from the immutable manifest, never a filesystem join.
    from pathlib import PurePosixPath
    media_types = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.wav': 'audio/wav',
                   '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4',
                   '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                   '.webp': 'image/webp', '.gif': 'image/gif', '.avif': 'image/avif'}
    media_type = media_types.get(PurePosixPath(filename).suffix.lower())
    return FileResponse(path, media_type=media_type or 'application/octet-stream',
                        filename=None if media_type else PurePosixPath(filename).name,
                        headers={'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "sandbox; default-src 'none'"})


async def _same_origin_object(request: Request) -> dict:
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Origin mismatch")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected an object")
    return data


def _active_scene_state(data: dict) -> dict:
    from main_routers.game_router.runtime import _sdk_route_instance_error
    from utils.game_route_state import _get_active_game_route_state
    state = _get_active_game_route_state(str(data.get("lanlan_name", "")), "watch-together")
    if (not state or not state.get("game_route_active")
            or str(state.get("session_id")) != str(data.get("session_id"))
            or _sdk_route_instance_error(state, data)):
        raise HTTPException(409, "Scene session is no longer active")
    return state


def _finite_number(value, low, high):
    if isinstance(value, bool):
        raise ValueError()
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError()
    return number


@router.post("/live")
async def live(request: Request):
    """Speak held plugin responses in a reaction gap, or an automatic-mode intermission."""
    data = await _same_origin_object(request)
    state = _active_scene_state(data)
    action = data.get("action")
    job, version = data.get("job"), data.get("version")
    if (action not in ("interject", "intermission")
            or not all(isinstance(value, str) and 0 < len(value) <= 128 for value in (job, version))):
        raise HTTPException(400, "Invalid live request")
    from main_logic.watch_together import live as live_lines
    position = seconds = 0.0
    if action == "interject":
        try:
            position = _finite_number(data.get("position"), 0, 86400)
            seconds = _finite_number(data.get("gap"), 0, 600)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "Invalid live request")
    from main_routers.game_router.route_lifecycle import _TAKEOVER_CALLBACK_INBOX_KEY
    inbox = state.get(_TAKEOVER_CALLBACK_INBOX_KEY)
    if action == "interject" and (inbox is None or not inbox.pending):
        return {"lines": []}
    if state.get(_LIVE_BUSY_KEY):
        return {"lines": [], "busy": True}
    from main_routers.shared_state import get_session_manager
    from main_routers.game_router.char_info import _extract_request_render_language_full
    from main_logic.watch_together.preparation import session_language
    manager = get_session_manager().get(str(data.get("lanlan_name", "")))
    if not manager:
        raise HTTPException(409, "Character session unavailable")
    state[_LIVE_BUSY_KEY] = True
    callbacks = []
    try:
        try:
            video = await asyncio.to_thread(lambda: live_lines.video_context(application_library(), job, version))
        except (KeyError, ValueError, OSError):
            raise HTTPException(404, "Timeline unavailable")
        if inbox is not None:
            callbacks = inbox.take(live_lines.INTERJECT_TAKE if action == "interject" else live_lines.INTERMISSION_TAKE)
        language = session_language(manager, _extract_request_render_language_full(data))
        # Leave a second of margin so the line ends before the next reaction.
        speakable = max(2.0, min(10.0, seconds - 1.0))

        async def produce():
            lines = await live_lines.compose(manager, mode=action, callbacks=callbacks, video=video,
                                             language=language, position=position, seconds=speakable)
            return await live_lines.speak(manager, lines, language)
        spoken = await asyncio.wait_for(produce(), 90)
    except (HTTPException, asyncio.CancelledError):
        live_lines.settle(callbacks, False)
        raise
    except Exception as exc:
        live_lines.settle(callbacks, False)
        print(f"Watch live {action} failed: {type(exc).__name__}")
        return {"lines": [], "error": type(exc).__name__}
    finally:
        state[_LIVE_BUSY_KEY] = False
    from main_logic.proactive_delivery import callback_is_expired
    # A gap line exists only to answer its cues; if they all expired while the
    # model and TTS ran (danmaku replies are short-lived), it would be stale.
    stale = action == "interject" and callbacks and all(callback_is_expired(callback) for callback in callbacks)
    if not state.get("game_route_active") or stale:
        live_lines.settle(callbacks, False)
        return {"lines": []}
    live_lines.settle(callbacks, True)
    return {"lines": spoken}


@router.get("/live-audio/{token}")
async def live_audio(token: str):
    from fastapi.responses import Response
    from main_logic.watch_together.live import read_audio
    data = read_audio(token)
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="audio/wav",
                    headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
                             'Content-Security-Policy': "sandbox; default-src 'none'"})


@router.post("/watch")
async def watch(request: Request):
    data = await _same_origin_object(request)
    state = _active_scene_state(data)
    try:
        if data.get("action") == "start":
            identifier = await asyncio.to_thread(_library_call, "start_watch", data["job"], data["version"], data["lanlan_name"])
            state["watch_together_id"] = identifier
            return {"id": identifier}
        identifier = data["id"]
        if state.get("watch_together_id") != identifier:
            raise HTTPException(409, "Viewing session mismatch")
        position = float(data["position"])
        if not math.isfinite(position) or not 0 <= position <= 86400:
            raise ValueError()
        event = data["event"]
        if not isinstance(event, dict):
            raise ValueError()
        if event.get("type") not in {"progress", "play", "pause", "seek", "rate", "ended", "audio-started", "audio-ended", "exit"}:
            raise ValueError()
        safe = {"type": event["type"], "cue": str(event.get("cue", ""))[:80]}
        await asyncio.to_thread(_library_call, "record_watch", identifier, position, safe)
        return {"ok": True}
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "Invalid viewing record")


@router.post("/prepare")
async def prepare_video(request: Request):
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Origin mismatch")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected an object")
    from main_routers.shared_state import get_session_manager, get_config_manager
    from main_logic.watch_together.preparation import prepare
    characters = await asyncio.to_thread(get_config_manager().load_characters)
    name = str(data.get("lanlan_name") or characters.get("当前猫娘", ""))
    manager = get_session_manager().get(name)
    if not manager:
        raise HTTPException(409, "Character session unavailable")
    if "confirmation_job" in data:
        from main_logic.watch_together.preparation import confirm_preparation
        if not isinstance(data["confirmation_job"], str):
            raise HTTPException(400, "Invalid preparation job")
        try:
            return confirm_preparation(data["confirmation_job"], manager,
                                       data.get("accepted"), data.get("confirmed_duration"))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    url = data.get("url")
    if not isinstance(url, str) or not 5 <= len(url) <= 1000:
        raise HTTPException(400, "Invalid video URL")
    try:
        from main_logic.watch_together.discovery import inspect_video, enforce_policy
        try:
            info = await inspect_video(url)
        except ValueError:
            raise
        except Exception as exc:
            raise HTTPException(502, "Video metadata unavailable") from exc
        automatic = data.get("source") == "discovery"
        confirmed = data.get("confirmed_duration")
        if not enforce_policy(info, automatic=automatic, confirmed_duration=confirmed):
            return {"confirmation_required": True, "video": info}
        from main_routers.game_router.char_info import _extract_request_render_language_full
        return await prepare(info["url"], manager, name, automatic=automatic,
                             confirmed_duration=confirmed, render_language=_extract_request_render_language_full(data))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.get("/preparation/{job}")
async def preparation(job: str):
    from main_logic.watch_together.preparation import jobs
    if job not in jobs:
        raise HTTPException(404)
    return jobs[job]


@router.post("/discover")
async def discover_video(request: Request):
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Origin mismatch")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(data, dict) or not isinstance(data.get("topic", ""), str):
        raise HTTPException(400, "Invalid topic")
    topic = data.get("topic", "").strip()
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list) or len(exclude) > 128 or any(not isinstance(item, str) or len(item) > 32 for item in exclude):
        raise HTTPException(400, "Invalid video exclusions")
    if len(topic) > 200:
        raise HTTPException(400, "Topic too long")
    from main_logic.watch_together.discovery import discover
    try:
        async with asyncio.timeout(180):
            return await discover(topic, exclude)
    except Exception:
        raise HTTPException(502, "Video search unavailable")
