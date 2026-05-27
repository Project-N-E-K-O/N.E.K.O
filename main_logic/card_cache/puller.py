"""Card cache puller（NEKO → N.E.K.O.Servers GET /api/cards/mine）。

设计要点：
- **默认禁用**：仅当 ``NEKO_CARD_CACHE_ENABLED=1`` 且 ``NEKO_SOCIAL_BASE_URL`` 已配
  才真启动。
- **简单版（M5）**：每次 sweep 拉最近 ``limit=100`` 张，与本地 ``memory/<lanlan>/cards/``
  下的 ``<id>.json`` 文件对比。已存在的跳过；新出现的写入。**M6+** 引入 ``?since=ISO``
  增量参数 + ETag。
- **失败不阻塞**：网络/HTTP 非 2xx 时只记日志，下次 sweep 再试。
- **身份**：游客模式用 ``X-Client-Id``（与 facts_sync 一致）；登录态 JWT 由 NEKO-PC
  通过 IPC 推到 NEKO 后再加，M6 工作。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from utils.config_manager import get_config_manager

logger = logging.getLogger("neko.card_cache")

SYNC_INTERVAL_SEC = 5 * 60
HTTP_TIMEOUT_SEC = 15.0
MAX_CARDS_PER_PULL = 100
STARTUP_DELAY_SEC = 60.0


def _enabled() -> bool:
    return os.environ.get("NEKO_CARD_CACHE_ENABLED", "0") in ("1", "true", "TRUE", "yes")


def _social_base_url() -> str | None:
    raw = os.environ.get("NEKO_SOCIAL_BASE_URL", "").strip().rstrip("/")
    return raw or None


def _get_client_id() -> str | None:
    try:
        cm = get_config_manager()
        state = cm.load_cloudsave_local_state()
        if isinstance(state, dict):
            cid = state.get("client_id")
            if isinstance(cid, str) and cid:
                return cid
    except Exception as exc:  # noqa: BLE001
        logger.warning("card_cache: read client_id failed: %s", exc)
    return None


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


async def _pull_once() -> int:
    """单次 sweep；返回本次新写入的卡片数。"""
    base_url = _social_base_url()
    if not base_url:
        return 0
    client_id = _get_client_id()
    if not client_id:
        logger.warning("card_cache: no client_id, skipping")
        return 0

    url = f"{base_url}/api/cards/mine?limit={MAX_CARDS_PER_PULL}"
    headers = {"X-Client-Id": client_id}

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        logger.info("card_cache: HTTP failed (silent): %s", exc)
        return 0
    if r.status_code == 401:
        # 游客模式但 client_id 还没在 Servers 注册（首次启动需先 POST /api/clients/register）
        # M5 puller 不主动注册（这是 NEKO-PC 启动时做的事），保持安静
        logger.debug("card_cache: 401 (client not registered yet)")
        return 0
    if r.status_code != 200:
        logger.info("card_cache: %s status=%s body=%s", url, r.status_code, r.text[:200])
        return 0

    try:
        cards = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("card_cache: parse response failed: %s", exc)
        return 0
    if not isinstance(cards, list):
        return 0

    cm = get_config_manager()
    memory_dir = Path(cm.memory_dir)

    written = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_id = card.get("id")
        lanlan_name = card.get("lanlan_name")
        if not isinstance(card_id, str) or not isinstance(lanlan_name, str):
            continue
        # 简单 sanitize 防路径穿越
        safe_lanlan = lanlan_name.replace("/", "_").replace("\\", "_").replace("..", "_")[:64]
        path = memory_dir / safe_lanlan / "cards" / f"{card_id}.json"
        if path.exists():
            continue  # M5：已存在不覆写；M6 加 updated_at 比对再覆写
        try:
            _write_json_atomic(path, card)
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("card_cache: write %s failed: %s", path, exc)

    if written:
        logger.info("card_cache: sweep wrote %d new cards", written)
    return written


async def start_card_cache_puller() -> None:
    """长期运行的 worker。被 main_server.on_startup 包在 asyncio.create_task 里调。"""
    if not _enabled():
        logger.info("card_cache: disabled (set NEKO_CARD_CACHE_ENABLED=1 to enable)")
        return
    if not _social_base_url():
        logger.warning("card_cache: NEKO_SOCIAL_BASE_URL not set; skipping")
        return

    logger.info(
        "card_cache: starting (interval=%ds, max_per_pull=%d)",
        SYNC_INTERVAL_SEC, MAX_CARDS_PER_PULL,
    )
    try:
        await asyncio.sleep(STARTUP_DELAY_SEC)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _pull_once()
        except asyncio.CancelledError:
            logger.info("card_cache: cancelled")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("card_cache: sweep raised: %s", exc)
        try:
            await asyncio.sleep(SYNC_INTERVAL_SEC)
        except asyncio.CancelledError:
            return
