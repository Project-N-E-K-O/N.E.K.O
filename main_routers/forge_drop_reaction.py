"""Send confirmed forge-credit drops through ordinary conversational replies."""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from config.prompts.prompts_forge_drop import build_forge_drop_chat_context
from main_logic.proactive_delivery import CALLBACK_EXPIRES_AT_KEY
from main_routers.shared_state import get_config_manager, get_session_manager
from utils.language_utils import get_global_language_full

router = APIRouter()
logger = logging.getLogger(__name__)
_seen_events: OrderedDict[str, None] = OrderedDict()
_DROP_REASONS = frozenset({"emotion_combo", "5rounds", "idle", "minigame", "like_daily"})


@router.post("/credits/react")
async def react_to_credit_drop(request: Request):
    from main_routers.system_router import _validate_local_mutation_request

    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid_body")
    if not isinstance(data, dict):
        raise HTTPException(400, "invalid_body")
    denied = _validate_local_mutation_request(request, payload=data)
    if denied is not None:
        return denied
    try:
        event_id = str(UUID(str(data.get("event_id") or "")))
    except ValueError:
        raise HTTPException(400, "invalid_event_id")
    rarity = data.get("rarity")
    if not isinstance(rarity, str) or rarity not in {"N", "R", "SR", "SSR", "UR"}:
        raise HTTPException(400, "invalid_rarity")
    if event_id in _seen_events:
        return {"ok": True, "skipped": "duplicate"}

    characters = await get_config_manager().aload_characters()
    current = characters.get("当前猫娘")
    if not current or data.get("lanlan_name") != current:
        return {"ok": True, "skipped": "character_changed"}
    manager = get_session_manager().get(current)
    submit = getattr(manager, "submit_proactive_callback", None)
    if not callable(submit) or not getattr(manager, "websocket", None):
        return {"ok": True, "skipped": "no_frontend"}
    # Only supported drop fields enter the context; callers cannot supply a reply or prompt.
    reason = data.get("reason")
    facts = {
        "event": "forge_credit_dropped", "quantity": 1,
        "rarity": rarity,
        "reason": reason if isinstance(reason, str) and reason in _DROP_REASONS else "unknown",
        "is_career_first": data.get("is_career_first") is True,
    }
    active_count = data.get("active_count")
    if type(active_count) is int and 0 <= active_count <= 100000:
        facts["active_count"] = active_count
    expires_at = data.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is not None:
                facts["expires_at"] = expiry.astimezone().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    language = getattr(manager, "user_language", None) or get_global_language_full()
    guidance = build_forge_drop_chat_context(language)
    callback = {
        "event": "agent_task_callback", "origin": "event", "channel": "forge_drop",
        "task_id": event_id, "status": "completed", "success": True,
        "source_kind": "system", "source_name": "forge_drop",
        # Both text and voice render the summary; detail can be omitted.
        "summary": f"{guidance}\n{json.dumps(facts, ensure_ascii=False)}",
        "detail": "",
        "delivery_mode": "proactive", "context_type": "forge_drop",
        "metadata": {"context_type": "forge_drop", "event_id": event_id},
        CALLBACK_EXPIRES_AT_KEY: time.monotonic() + 45,
    }
    # The normal delivery manager owns pacing, the live persona/context and the
    # text/voice response. There is no fixed assistant line or direct TTS here.
    if event_id in _seen_events:
        return {"ok": True, "skipped": "duplicate"}
    _seen_events[event_id] = None
    while len(_seen_events) > 256:
        _seen_events.popitem(last=False)
    try:
        submit(callback, priority=0, coalesce_key="forge_drop:" + event_id)
    except Exception:
        _seen_events.pop(event_id, None)
        logger.warning("锻造券到账事件提交失败", exc_info=True)
        return {"ok": False, "reason": "chat_unavailable"}
    return {"ok": True, "queued": True}
