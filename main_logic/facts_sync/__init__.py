"""facts_sync — 把猫娘 facts 增量推送到 N.E.K.O.Servers 云端社交平台。

启动方式：在 ``app/main_server.py`` 的 ``on_startup`` 钩子里 ``asyncio.create_task``
拉起 ``start_facts_sync_worker()``。默认 **不启动**（NEKO_FACTS_SYNC_ENABLED=0），
需要部署者显式打开。

详细契约见 N.E.K.O.Servers 仓库的 ``.claude/contracts/facts-sync-schema.md``。
"""
from main_logic.facts_sync.sync_worker import start_facts_sync_worker  # noqa: F401

__all__ = ["start_facts_sync_worker"]
