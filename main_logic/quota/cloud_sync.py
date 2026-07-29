"""Send fire-and-forget ``POST /api/quotas/drop-hint`` calls to Servers.

Design notes:
- Fire-and-forget so synchronous hooks are not blocked.
- The idempotency key combines client id, UTC date, trigger type, a counter, and
  current time to avoid duplicate cloud grants on retries.
- Failures are logged only and never interrupt the main conversation flow.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("neko.quota.cloud_sync")

HTTP_TIMEOUT_SEC = 10.0

# 进程内 counter（每次 idem_key 都递增；与 utc_date + client_id + trigger_type 组合保证唯一）
_seq_counter = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


def make_idem_key(client_id: str, trigger_type: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seq = _next_seq()
    raw = f"{client_id}|{date_str}|{trigger_type}|{seq}|{time.time()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _social_base_url() -> str | None:
    raw = os.environ.get("NEKO_SOCIAL_BASE_URL", "").strip().rstrip("/")
    return raw or None


def _get_client_id() -> str | None:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        state = cm.load_cloudsave_local_state()
        if isinstance(state, dict):
            cid = state.get("client_id")
            if isinstance(cid, str) and cid:
                return cid
    except Exception as exc:  # noqa: BLE001
        logger.debug("cloud_sync: client_id read failed: %s", exc)
    return None


async def _post_drop_hint_async(
    base_url: str,
    client_id: str,
    lanlan_name: str | None,
    trigger_type: str,
    idem_key: str,
) -> None:
    url = f"{base_url}/api/quotas/drop-hint"
    headers = {
        "X-Client-Id": client_id,
        "Content-Type": "application/json",
    }
    payload = {
        "lanlan_name": lanlan_name,
        "trigger_type": trigger_type,
        "idem_key": idem_key,
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.post(url, headers=headers, json=payload)
    except (httpx.HTTPError, OSError) as exc:
        logger.info("cloud_sync: drop-hint HTTP failed (silent): %s", exc)
        return
    if r.status_code != 200:
        logger.info("cloud_sync: drop-hint %s status=%s body=%s", trigger_type, r.status_code, r.text[:160])
        return
    try:
        data = r.json()
        if data.get("accepted"):
            logger.info(
                "cloud_sync: drop-hint %s accepted; new_available=%s",
                trigger_type, data.get("new_available"),
            )
        else:
            logger.info(
                "cloud_sync: drop-hint %s rejected reason=%s",
                trigger_type, data.get("reason"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("cloud_sync: parse response failed: %s", exc)


def send_drop_hint(lanlan_name: str | None, trigger_type: str) -> None:
    """Schedule one outbound drop-hint call on the caller's running loop.

    If no loop is running, or the base URL/client id is missing, return quietly.
    """
    base_url = _social_base_url()
    if not base_url:
        return
    client_id = _get_client_id()
    if not client_id:
        return
    idem_key = make_idem_key(client_id, trigger_type)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("cloud_sync: no running loop, skipping drop-hint")
        return
    loop.create_task(
        _post_drop_hint_async(base_url, client_id, lanlan_name, trigger_type, idem_key)
    )
