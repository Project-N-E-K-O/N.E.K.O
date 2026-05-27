"""card_cache — 把 N.E.K.O.Servers 上属于本 client 的卡片增量同步到本地。

启动方式：在 ``app/main_server.py`` 的 ``on_startup`` 钩子里 ``asyncio.create_task``
拉起 ``start_card_cache_puller()``。默认 **不启动**（NEKO_CARD_CACHE_ENABLED=0），
需要部署者显式打开。

落盘位置：``memory/<lanlan_name>/cards/<card_id>.json``，写入是原子的（tmp + rename）。
"""

from main_logic.card_cache.puller import start_card_cache_puller  # noqa: F401

__all__ = ["start_card_cache_puller"]
