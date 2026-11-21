"""Local music recommendation decisions and account-scoped evidence export."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from utils.proactive_preferences.personalization import LocalPreferences, RecommendationContext
from main_logic.recommendation_feedback import FeedbackConflict, FeedbackDecisionConflict, RecommendationStore, RecommendationEventKind
from services.chat_sync import host
from services.proactive_feedback.host import owner_from_snapshot
from utils.config_manager import get_config_manager
from utils.preferences import load_global_conversation_settings

router = APIRouter(prefix="/recommendations", tags=["recommendations"])
STORE_PROTOCOL_VERSION = 1


def install_store_protocol(version: int):
    """Selected once by startup assembly, not by an untrusted request."""
    global STORE_PROTOCOL_VERSION
    if version not in (1, 2):
        raise ValueError("unsupported_feedback_protocol")
    STORE_PROTOCOL_VERSION = version


def _control(owner):
    if STORE_PROTOCOL_VERSION != 2:
        return None
    from services.proactive_feedback.capture import get_control_snapshot
    return get_control_snapshot(owner)

CATEGORY_SETTINGS = {
    "music": "proactiveMusicEnabled", "news": "proactiveNewsChatEnabled",
    "community": "proactiveCommunityChatEnabled", "video": "proactiveVideoChatEnabled",
    "personal": "proactivePersonalChatEnabled", "meme": "proactiveMemeEnabled",
    "vision": "proactiveVisionChatEnabled", "window": "proactiveVisionChatEnabled",
    "chat": "proactiveChatEnabled", "web": "proactiveNewsChatEnabled",
    "work_break": "proactiveChatEnabled", "anti_slack": "proactiveChatEnabled",
}


def _owner():
    return owner_from_snapshot(host.saved_session_snapshot())


def _operate(operation, *args):
    # Serialize the identity check and evidence commit against OAuth account
    # replacement/logout. Never attribute a late result to the next account.
    with host.saved_session_writer_fence():
        owner = _owner()
        path = Path(get_config_manager().get_runtime_config_path("recommendation_feedback.sqlite3"))
        return operation(RecommendationStore(path, protocol_version=STORE_PROTOCOL_VERSION), owner, *args)


async def capture_owner():
    def read_owner():
        with host.saved_session_writer_fence():
            return _owner()

    return await asyncio.to_thread(read_owner)


async def load_recommendation_context(expected_owner: dict) -> RecommendationContext:
    async def load_chat_sources(character):
        def read(store, owner):
            return store.chat_source_digests(owner["key"], character) if owner == expected_owner else {}

        try:
            return await asyncio.to_thread(_operate, read)
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            return {}

    async def save_chat(evidence):
        def save(store, owner):
            if owner == expected_owner:
                store.save_chat_preferences(owner["key"], evidence)
        try:
            await asyncio.to_thread(_operate, save)
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            pass

    def load():
        with host.saved_session_writer_fence():
            snapshot = host.saved_session_snapshot() or {}
            owner = owner_from_snapshot(snapshot)
            if owner != expected_owner:
                return RecommendationContext(community_identity_known=False)
            try:
                path = Path(get_config_manager().get_runtime_config_path("recommendation_feedback.sqlite3"))
                store = RecommendationStore(path, protocol_version=STORE_PROTOCOL_VERSION)
                preferences = store.local_preferences(owner)
            except (OSError, sqlite3.Error, ValueError, RuntimeError):
                preferences = LocalPreferences()
            return RecommendationContext(
                preferences=preferences,
                community=owner["community"] or "",
                bearer=snapshot["access_token"] if owner["community"] else "",
                account_id=str(owner["user_id"] or ""),
                community_identity_known=bool(owner["community"]) or not snapshot.get("access_token"),
                save_chat=save_chat,
                load_chat_sources=load_chat_sources,
            )

    try:
        return await asyncio.to_thread(load)
    except (OSError, sqlite3.Error, ValueError, RuntimeError):
        return RecommendationContext(community_identity_known=False)


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
        return store.create(owner, character, str(body.get("turn_id") or ""), track, settings, control=_control(owner))

    body["music_recommendation"] = await asyncio.to_thread(_operate, create)


async def attach_proactive_recommendations(body: dict, expected_owner: dict, character: str,
                                          *, community_tags: tuple[str, ...] = ()):
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
        candidate = {"name": str(link.get("title") or "")[:240],
                     "artist": str(link.get("source") or parsed.hostname)[:120], "url": raw_url, "cover": ""}
        if category == "community" and community_tags:
            candidate["local_tags"] = community_tags
        candidates.append((category, candidate))
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
            record = store.create(owner, character, str(body.get("turn_id") or ""), metadata, settings, category=category, control=_control(owner))
            result.append({"id": record["id"], "category": category, "state": record["state"],
                           "expires_at": record["expires_at"], "content": {
                               "title": content["name"], "source": content["artist"], "url": content["url"],
                           }, **({"offer_context": record["offer_context"]} if "offer_context" in record else {})})
        return result

    body["proactive_recommendations"] = await asyncio.to_thread(_operate, create)


class RecommendationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)
    kind: RecommendationEventKind | Literal["measurement"]
    occurred_at: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False, strict=True)
    playback_id: str | None = Field(default=None, min_length=1, max_length=512)
    listened_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False, strict=True)
    position_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False, strict=True)
    reason: str = Field(default="", max_length=64)
    context: dict | None = None
    offer_context: dict | None = None
    measurement: dict | None = None


def _validate(request):
    from main_routers.system_router._shared import _validate_local_mutation_request
    return _validate_local_mutation_request(request)


@router.post("/{recommendation_id}/events")
async def recommendation_event(request: Request, recommendation_id: str, event: RecommendationEvent):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    try:
        # Preserve JSON number spelling/types for the shared JS handoff hash;
        # Pydantic's float fields otherwise turn an original integer into 1.0.
        raw_event = await request.json()
        def record(store, owner):
            settings = load_global_conversation_settings() if event.kind == "accepted" else {}
            disabled = frozenset(category for category, setting in CATEGORY_SETTINGS.items()
                                 if settings.get("proactiveChatEnabled") is False or settings.get(setting) is False
                                 or (category in {"vision", "window"} and settings.get("proactiveVisionEnabled") is False))
            value = raw_event if store.protocol_version == 2 else event.model_dump(exclude={"context", "offer_context", "measurement"})
            return store.record(owner, recommendation_id, value, disabled_categories=disabled, control=_control(owner),
                                canonical_header=request.headers.get("X-NEKO-Feedback-Canonical"))
        result = await asyncio.to_thread(_operate, record)
        return {"success": True, **result}
    except FeedbackDecisionConflict as exc:
        return JSONResponse({"success": False, "error_code": str(exc), "telemetry": exc.telemetry}, status_code=409)
    except FeedbackConflict as exc:
        return JSONResponse({"success": False, "error_code": str(exc)}, status_code=409)
    except (OSError, sqlite3.Error):
        return JSONResponse({"success": False, "error_code": "feedback_unavailable"}, status_code=503)


class DispatchClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)


@router.post("/{recommendation_id}/dispatch")
async def recommendation_dispatch(request: Request, recommendation_id: str, claim: DispatchClaim):
    rejected = _validate(request)
    if rejected is not None:
        return rejected
    try:
        claimed = await asyncio.to_thread(_operate, lambda store, owner: store.claim_dispatch(owner, recommendation_id, claim.event_id))
        return {"success": True, "dispatch_grant": claimed}
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
