"""facts_sync incrementally pushes character facts to N.E.K.O.Servers.

Startup is wired from ``app/main_server.py`` via ``asyncio.create_task`` around
``start_facts_sync_worker()``. It is disabled by default
(``NEKO_FACTS_SYNC_ENABLED=0``) and must be explicitly enabled by deployment.

The detailed wire contract lives in the N.E.K.O.Servers repository at
``.claude/contracts/facts-sync-schema.md``.
"""
from main_logic.facts_sync.sync_worker import start_facts_sync_worker  # noqa: F401

__all__ = ["start_facts_sync_worker"]
