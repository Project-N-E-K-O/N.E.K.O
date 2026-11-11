"""Playback and deletion of recordings on the local NEKO instance."""

import asyncio
from sqlite3 import Error as SQLiteError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from services.chat_audio_history import history_for_character
from .card_drop_router import _local_request_source_allowed
from .local_access import is_local_oauth_status_request
from .shared_state import get_config_manager, get_session_manager


def local_audio_history_allowed(connection) -> bool:
    if not is_local_oauth_status_request(connection):
        return False
    if connection.scope["type"] == "websocket":
        # The browser Origin is HTTP(S) for a WebSocket handshake.
        scheme = "https" if connection.url.scheme == "wss" else "http"
        connection = Request({**connection.scope, "type": "http", "scheme": scheme})
    return _local_request_source_allowed(connection)


def _require_local(request: Request, response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    if not local_audio_history_allowed(request):
        raise HTTPException(403, "chat_audio_local_access_required", headers={"Cache-Control": "no-store"})


router = APIRouter(prefix="/api/chat-audio", dependencies=[Depends(_require_local)])


async def _history(character: str):
    history = await history_for_character(get_config_manager(), character)
    if history is None:
        raise HTTPException(404, "chat_audio_character_not_found")
    return history


@router.get("")
async def list_chat_audio(
    character: str, before: int | None = Query(None, ge=1, le=2**63 - 1), limit: int = Query(50, ge=1, le=100),
):
    history = await _history(character)
    return await asyncio.to_thread(history.page, before, limit)


@router.get("/{request_id}")
async def play_chat_audio(character: str, request_id: str):
    history = await _history(character)
    audio = await asyncio.to_thread(history.read_audio, request_id)
    if audio is None:
        raise HTTPException(404, "chat_audio_not_found")
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


async def _delete_audio(character: str, request_id: str | None) -> Response:
    history = await _history(character)
    try:
        await asyncio.to_thread(history.delete, request_id)
    except (OSError, SQLiteError):
        raise HTTPException(503, "chat_audio_storage_unavailable") from None
    manager = get_session_manager().get(character)
    if manager is not None and manager.websocket is not None:
        try:
            async with asyncio.timeout(1):
                await manager.websocket.send_json({"type": "chat_audio_changed", "character": character})
        except Exception:
            pass
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.delete("/{request_id}")
async def delete_chat_audio(character: str, request_id: str):
    return await _delete_audio(character, request_id)


@router.delete("")
async def clear_chat_audio(character: str):
    return await _delete_audio(character, None)
