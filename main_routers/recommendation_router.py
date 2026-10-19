"""Local music recommendation decisions and account-scoped evidence export."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore, RecommendationEventKind
from services.chat_sync import host
from utils.config_manager import get_config_manager
from utils.preferences import load_global_conversation_settings

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

CATEGORY_SETTINGS = {
    "music": "proactiveMusicEnabled", "news": "proactiveNewsChatEnabled",
    "community": "proactiveCommunityChatEnabled", "video": "proactiveVideoChatEnabled",
    "personal": "proactivePersonalChatEnabled", "meme": "proactiveMemeEnabled",
    "vision": "proactiveVisionChatEnabled", "window": "proactiveVisionChatEnabled",
    "chat": "proactiveChatEnabled", "web": "proactiveNewsChatEnabled",
    "work_break": "proactiveChatEnabled", "anti_slack": "proactiveChatEnabled",
}


def _owner():
    snapshot = host.saved_session_snapshot() or {}
    user_id = snapshot.get("local_user_id") if snapshot.get("access_token") else None
    base = snapshot.get("base_url", "") if user_id else ""
    key = hashlib.sha256(f"{base}\n{user_id or ''}".encode()).hexdigest()
    return {"key": key, "user_id": user_id, "community": base or None}


def _operate(operation, *args):
    # Serialize the identity check and evidence commit against OAuth account
    # replacement/logout. Never attribute a late result to the next account.
    with host.saved_session_writer_fence():
        owner = _owner()
        path = Path(get_config_manager().get_runtime_config_path("recommendation_feedback.sqlite3"))
        return operation(RecommendationStore(path), owner, *args)


async def capture_owner():
    return await asyncio.to_thread(_operate, lambda store, owner: owner)


async def attach_music_recommendation(body: dict, expected_owner: dict, character: str):
    if not body.get("success") or body.get("action") != "chat" or body.get("source_mode") not in {"music", "both"}:
        return
    links = [link for link in body.get("source_links", []) if isinstance(link, dict) and link.get("url")]
    music = [link for link in links if link.get("artist") or link.get("source") == "音乐推荐"] or links
    if not music:
        return
    link = music[0]
    track = {"name": str(link.get("title") or "")[:120], "artist": str(link.get("artist") or "")[:120],
             "url": str(link["url"])[:4096], "cover": str(link.get("cover") or "")[:4096]}

    def create(store, owner):
        if owner != expected_owner:
            return None
        settings = {key: value for key, value in load_global_conversation_settings().items()
                    if key.startswith("proactive") and isinstance(value, (bool, int))}
        return store.create(owner, character, str(body.get("turn_id") or ""), track, settings)

    body["music_recommendation"] = await asyncio.to_thread(_operate, create)


async def attach_proactive_recommendations(body: dict, expected_owner: dict, character: str):
    """Register committed content; screen text is UI-only, never evidence."""
    if not body.get("success") or body.get("action") != "chat":
        return
    channel = body.get("channel")
    if channel in {"mini_game", "work_break_game_invite"}:
        return  # Invitation choices already own these surfaces.
    mode = str(body.get("source_mode") or channel or "chat").lower()
    if mode not in CATEGORY_SETTINGS:
        channels = body.get("active_channels") or []
        mode = next((value for value in channels if value in CATEGORY_SETTINGS and value != "music"), "chat")
    music_url = ((body.get("music_recommendation") or {}).get("track") or {}).get("url")
    candidates = []
    for link in body.get("source_links") or []:
        if not isinstance(link, dict) or link.get("artist") or link.get("source") == "音乐推荐" or (music_url and link.get("url") == music_url):
            continue
        raw_url = str(link.get("url") or "")[:4096]
        try:
            parsed = urlsplit(raw_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        category = str(link.get("mode") or mode)
        if link.get("type") in {"meme", "gif"}:
            category = "meme"
        if category not in CATEGORY_SETTINGS or category in {"music", "chat"}:
            category = "web"
        candidates.append((category, {"name": str(link.get("title") or "")[:240],
                            "artist": str(link.get("source") or parsed.hostname)[:120], "url": raw_url, "cover": ""}))
        if len(candidates) == 3:
            break
    observation = body.get("screen_observation")
    screen_text = observation.get("text") if isinstance(observation, dict) else None
    if not candidates and isinstance(screen_text, str) and screen_text.strip():
        candidates.append(("vision", {"name": screen_text.strip()[:240],
                                     "artist": "", "url": "", "cover": ""}))
    # Unmarked ordinary dialogue still has no card. A committed visual turn
    # carries explicit display text, but the ledger retains only its category.

    def create(store, owner):
        if owner != expected_owner:
            return []
        settings = {key: value for key, value in load_global_conversation_settings().items()
                    if key.startswith("proactive") and isinstance(value, (bool, int))}
        result = []
        for category, content in candidates:
            metadata = {"name": "", "artist": "", "url": ""} if category == "vision" and not content["url"] else content
            record = store.create(owner, character, str(body.get("turn_id") or ""), metadata, settings, category=category)
            result.append({"id": record["id"], "category": category, "state": record["state"],
                           "expires_at": record["expires_at"], "content": {
                               "title": content["name"], "source": content["artist"], "url": content["url"],
                           }})
        return result

    body["proactive_recommendations"] = await asyncio.to_thread(_operate, create)


class RecommendationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)
    kind: RecommendationEventKind
    occurred_at: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False, strict=True)
    playback_id: str | None = Field(default=None, min_length=1, max_length=512)
    listened_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False, strict=True)
    position_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False, strict=True)
    reason: str = Field(default="", max_length=64)


def _validate(request):
    from main_routers.system_router._shared import _validate_local_mutation_request
    return _validate_local_mutation_request(request)


@router.post("/{recommendation_id}/events")
async def recommendation_event(request: Request, recommendation_id: str, event: RecommendationEvent):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    try:
        def record(store, owner):
            settings = load_global_conversation_settings() if event.kind == "accepted" else {}
            disabled = frozenset(category for category, setting in CATEGORY_SETTINGS.items()
                                 if settings.get("proactiveChatEnabled") is False or settings.get(setting) is False
                                 or (category in {"vision", "window"} and settings.get("proactiveVisionEnabled") is False))
            return store.record(owner, recommendation_id, event.model_dump(), disabled_categories=disabled)
        result = await asyncio.to_thread(_operate, record)
        return {"success": True, **result}
    except FeedbackConflict as exc:
        return JSONResponse({"success": False, "error_code": str(exc)}, status_code=409)
    except (OSError, sqlite3.Error):
        return JSONResponse({"success": False, "error_code": "feedback_unavailable"}, status_code=503)


@router.get("/events")
async def recommendation_events(request: Request, after: int = Query(0, ge=0, le=2**53-1), limit: int = Query(100, ge=1, le=500)):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    try:
        result = await asyncio.to_thread(_operate, lambda store, owner: store.events(owner, after, limit))
    except (FeedbackConflict, OSError, sqlite3.Error):
        return JSONResponse({"success": False, "error_code": "feedback_unavailable"}, status_code=503)
    return JSONResponse({"success": True, **result}, headers={"Cache-Control": "no-store"})
