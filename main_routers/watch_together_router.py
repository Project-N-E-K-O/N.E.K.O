"""Trusted media library endpoints used exclusively by the official SDK host."""
import asyncio
import math
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from main_logic.watch_together.library import application_library

router = APIRouter(prefix="/api/watch-together", tags=["watch-together"])


@router.get("/history")
async def history():
    library = application_library()
    return {"analyses": await asyncio.to_thread(library.history),
            "watches": await asyncio.to_thread(library.watches)}


@router.get("/jobs/{job}/{version}")
async def timeline(job: str, version: str):
    try:
        return await asyncio.to_thread(application_library().timeline, job, version)
    except (KeyError, ValueError, OSError):
        raise HTTPException(404, "Timeline unavailable")


@router.get("/media/{job}/{version}/{filename:path}")
async def media(job: str, version: str, filename: str):
    try:
        path = await asyncio.to_thread(application_library().resource, job, version, filename)
    except KeyError:
        raise HTTPException(404)
    # Resource names come from the immutable manifest, never a filesystem join.
    import mimetypes
    return FileResponse(path, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")


@router.post("/watch")
async def watch(request: Request):
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Origin mismatch")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected an object")
    from main_routers.game_router.runtime import _sdk_route_instance_error
    from utils.game_route_state import _get_active_game_route_state
    state = _get_active_game_route_state(str(data.get("lanlan_name", "")), "watch-together")
    if (not state or not state.get("game_route_active")
            or str(state.get("session_id")) != str(data.get("session_id"))
            or _sdk_route_instance_error(state, data)):
        raise HTTPException(409, "Scene session is no longer active")
    library = application_library()
    try:
        if data.get("action") == "start":
            identifier = await asyncio.to_thread(library.start_watch, data["job"], data["version"], data["lanlan_name"])
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
        await asyncio.to_thread(library.record_watch, identifier, position, safe)
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
    url = data.get("url")
    if not isinstance(url, str) or not 5 <= len(url) <= 1000:
        raise HTTPException(400, "Invalid video URL")
    try:
        from main_logic.watch_together.discovery import inspect_video, enforce_policy
        info = await inspect_video(url)
        automatic = data.get("source") == "discovery"
        confirmed = data.get("confirmed_duration")
        if not enforce_policy(info, automatic=automatic, confirmed_duration=confirmed):
            return {"confirmation_required": True, "video": info}
        return await prepare(info["url"], manager, name, automatic=automatic, confirmed_duration=confirmed)
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
    if len(topic) > 200:
        raise HTTPException(400, "Topic too long")
    from main_logic.watch_together.discovery import discover
    try:
        return await discover(topic)
    except (ValueError, TimeoutError):
        raise HTTPException(502, "Video search unavailable")
