"""Card cache puller for ``GET /api/cards/mine`` from N.E.K.O.Servers.

Design notes:
- Disabled by default; it starts only when ``NEKO_CARD_CACHE_ENABLED=1`` and
  ``NEKO_SOCIAL_BASE_URL`` is configured.
- The M5 version pulls the latest ``limit=100`` cards each sweep, skips existing
  local ``memory/<lanlan>/cards/<id>.json`` files, and writes new cards. M6+
  can add ``?since=ISO`` plus ETag support.
- Network and non-2xx HTTP failures are logged and retried on the next sweep.
- Guest identity uses ``X-Client-Id`` like facts_sync. Authenticated JWT wiring
  from NEKO-PC IPC is left for M6.
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
        needs_persist = not cm.cloudsave_local_state_path.exists()
        state = cm.load_cloudsave_local_state()
        cid = state.get("client_id") if isinstance(state, dict) else None
        if not isinstance(cid, str) or not cid:
            state = cm.build_default_cloudsave_local_state()
            cid = state.get("client_id")
            needs_persist = True
        if not isinstance(cid, str) or not cid:
            return None
        if needs_persist:
            cm.save_cloudsave_local_state(state)
        return cid
    except Exception as exc:  # noqa: BLE001
        logger.warning("card_cache: failed to load or persist client_id: %s", exc)
    return None


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


async def _pull_once() -> int:
    """Run one sweep and return the number of newly written cards."""
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
        # 简单 sanitize 防路径穿越：去掉分隔符 / .. / Windows 盘符冒号（C:foo 这类盘限定段）
        safe_lanlan = (
            lanlan_name.replace("/", "_").replace("\\", "_").replace(":", "_").replace("..", "_")[:64]
        )
        # card_id 来自云端：Path(...).name 去掉任意目录分隔符与 ..，杜绝写到 cards/ 目录外。
        safe_card_id = Path(card_id).name
        if not safe_card_id or safe_card_id.startswith("."):
            continue
        path = memory_dir / safe_lanlan / "cards" / f"{safe_card_id}.json"
        # 兜底：解析后必须仍在 memory_dir 之下（防任何残留穿越）
        try:
            if memory_dir.resolve() not in path.resolve().parents:
                continue
        except OSError:
            continue
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
    """Run the long-lived card cache worker scheduled by main_server startup."""
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
