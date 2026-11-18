"""Forward memory-process write hints to the existing local sync host."""

import asyncio

import httpx

from utils.internal_http_client import get_internal_http_client
from utils.recent_file import subscribe_recent_changes


async def forward_recent_memory_changes():
    """Coalesce writes without passing chat contents or waiting in the save path."""
    from config import MAIN_SERVER_PORT

    loop = asyncio.get_running_loop()
    changed = asyncio.Event()
    unsubscribe = subscribe_recent_changes(lambda path: loop.call_soon_threadsafe(changed.set))
    from services.chat_sync.long_term_events import subscribe
    unsubscribe_long_term = subscribe(lambda: loop.call_soon_threadsafe(changed.set))
    try:
        while True:
            await changed.wait()
            await asyncio.sleep(0.2)
            changed.clear()
            try:
                await get_internal_http_client().post(
                    f'http://127.0.0.1:{MAIN_SERVER_PORT}/api/chat-sync/recent-changed', timeout=2,
                )
            except httpx.HTTPError:
                # A missed hint is repaired by the host's low-frequency fallback.
                pass
    finally:
        unsubscribe()
        unsubscribe_long_term()
