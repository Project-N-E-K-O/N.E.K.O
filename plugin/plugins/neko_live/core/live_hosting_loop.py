"""Auto-loop helpers for warmup, active engagement, and idle hosting."""

from __future__ import annotations

import asyncio
from typing import Any


def start_idle_hosting_loop(director: Any) -> None:
    runtime = director.runtime
    task = runtime._idle_hosting_task
    if task is not None and not task.done():
        return
    runtime._idle_hosting_task = asyncio.create_task(idle_hosting_loop(director))


async def stop_idle_hosting_loop(runtime: Any) -> None:
    task = runtime._idle_hosting_task
    runtime._idle_hosting_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def idle_hosting_loop(director: Any) -> None:
    runtime = director.runtime
    while True:
        await runtime._idle_hosting_sleep(runtime._IDLE_HOSTING_CHECK_INTERVAL_SECONDS)
        try:
            result = await director.maybe_trigger_warmup_hosting()
            if result is not None and result.status in {"dry_run", "pushed"}:
                continue
            result = await _maybe_trigger_active_engagement(runtime)
            if result is not None and result.status in {"dry_run", "pushed"}:
                continue
            await director.maybe_trigger_idle_hosting()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = f"idle_hosting_loop_failed: {type(exc).__name__}"
            runtime.audit.record("idle_hosting_loop_failed", message, level="warning")


async def _maybe_trigger_active_engagement(runtime: Any) -> Any:
    trigger = getattr(runtime, "maybe_trigger_active_engagement", None)
    if not callable(trigger):
        return None
    return await trigger()
