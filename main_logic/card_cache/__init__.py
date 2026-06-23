"""card_cache incrementally syncs this client's cloud cards to local storage.

Startup is wired from ``app/main_server.py`` via ``asyncio.create_task`` around
``start_card_cache_puller()``. It is disabled by default
(``NEKO_CARD_CACHE_ENABLED=0``) and must be explicitly enabled by deployment.

Cards are stored under ``memory/<lanlan_name>/cards/<card_id>.json`` using an
atomic tmp-file-and-rename write.
"""

from main_logic.card_cache.puller import start_card_cache_puller  # noqa: F401

__all__ = ["start_card_cache_puller"]
