"""Reconcile the injected recommendation owner with confirmed settings writes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from main_logic.topic.recommendation.registry import get_recommendation_service
from utils.preferences import aload_global_conversation_settings_snapshot

_pending_saves: set[asyncio.Task] = set()
_reconciliations: set[asyncio.Task] = set()


async def refresh_recommendation_controls() -> None:
    service = get_recommendation_service()
    if service is None:
        return
    if _pending_saves:
        service.pause_controls()
        return
    try:
        current = await aload_global_conversation_settings_snapshot(strict=True)
    except Exception:
        service.pause_controls()
        return
    if service is not get_recommendation_service() or _pending_saves:
        return
    await service.apply_controls(
        master_enabled=current.settings.get('proactiveChatEnabled') is True,
        beta_enabled=current.settings.get('proactiveTopicRecommendationEnabled') is True,
        revision=current.revision,
    )


async def recommendation_aware_save(save: Callable, *args, **kwargs):
    """A cancelled HTTP waiter does not retire its physical settings writer.

    Keep output paused until every accepted writer has really exited, then
    strictly reread the latest revision. The callback owns cancelled waiters'
    reconciliation; it never blindly reapplies the request payload.
    """
    service = get_recommendation_service()
    if service is None:
        return await asyncio.to_thread(save, *args, **kwargs)
    payload = args[0] if args else kwargs.get('settings', {})
    pause_required = not service.controls_match_payload(payload, full_snapshot=kwargs.get('full_snapshot', False))
    if pause_required:
        service.pause_controls()
    task = asyncio.create_task(asyncio.to_thread(save, *args, **kwargs))
    if pause_required:
        _pending_saves.add(task)

    def settled(completed: asyncio.Task) -> None:
        _pending_saves.discard(completed)
        # Retrieve cancelled-waiter exceptions without exposing their payload.
        if not completed.cancelled():
            completed.exception()
        reconcile = asyncio.create_task(refresh_recommendation_controls())
        _reconciliations.add(reconcile)
        reconcile.add_done_callback(_reconciliations.discard)

    task.add_done_callback(settled)
    result = await asyncio.shield(task)
    await refresh_recommendation_controls()
    return result
