"""Local history viewer, isolated from memory editing and conversation replay."""
import asyncio
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from services.chat_history.store import HistoryUnavailable, browse
from utils.config_manager import get_config_manager


async def local_access(request: Request, response: Response):
    from main_routers.card_drop_router import _local_request_source_allowed
    from main_routers.local_access import is_local_oauth_status_request
    response.headers['Cache-Control'] = 'no-store'
    if not is_local_oauth_status_request(request) or not _local_request_source_allowed(request):
        raise HTTPException(403, 'local_access_required')


router = APIRouter(prefix='/api/chat-history', tags=['chat-history'], dependencies=[Depends(local_access)])


async def characters():
    data = await get_config_manager().aload_characters()
    return sorted(data.get('猫娘', {}))


@router.get('/characters')
async def list_characters():
    return {'characters': await characters()}


@router.get('/messages')
async def messages(character: str = Query(min_length=1, max_length=200), day: date | None = None,
                   q: str = Query(default='', max_length=200), role: Literal['all', 'user', 'assistant'] = 'all',
                   cursor: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100),
                   order: Literal['asc', 'desc'] = 'desc', anchor: int | None = Query(default=None, ge=1)):
    if character not in await characters():
        raise HTTPException(404, 'character_not_found')
    try:
        return await asyncio.to_thread(browse, get_config_manager().memory_dir, character,
                                      day=day.isoformat() if day else None, query=q.strip(), role=role,
                                      cursor=cursor, limit=limit, order=order, anchor=anchor)
    except ValueError:
        raise HTTPException(400, 'invalid_character') from None
    except LookupError:
        raise HTTPException(404, 'message_not_found') from None
    except (HistoryUnavailable, OSError):
        raise HTTPException(503, 'history_unavailable') from None
