"""Facts sync worker for ``POST /api/facts/sync`` to N.E.K.O.Servers.

Design notes:
- Disabled by default; it starts only when ``NEKO_FACTS_SYNC_ENABLED=1`` and
  ``NEKO_SOCIAL_BASE_URL`` is configured.
- Each batch contains at most 50 facts to match the server contract.
- A sweep runs every five minutes. Each character stores synced hashes in
  ``memory/<lanlan>/facts_sync_state.json`` to avoid duplicate POSTs.
- Private facts, redacted facts, and low-importance facts are filtered locally
  before upload.
- Network and non-2xx HTTP failures leave hashes pending for the next sweep;
  hashes that fail too often are written to ``facts_sync_pending.jsonl``.
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

logger = logging.getLogger("neko.facts_sync")

# ---- tunables ----
SYNC_INTERVAL_SEC = 5 * 60  # 5 min
BATCH_SIZE = 50
# NEKO 端 importance 用 0-10 int 评分（memory/facts.py safe_importance default=5）；
# Servers schema 限 [0,1] float。这里阈值按 NEKO scale 走 5（中等以上），
# 推送前在 _select_unsynced_facts 里归一化到 [0,1]。
MIN_IMPORTANCE = 5.0  # NEKO 0-10 scale；服务端再过 importance >= 0.3（归一化后）
MAX_FAILED_ATTEMPTS = 5
HTTP_TIMEOUT_SEC = 15.0

# ---- M2-i fix: bootstrap register with Servers before pushing facts ----
# Servers 端 X-Client-Id 鉴权要求 client 已 register 过；否则 401。
# 这里在 sweep 开头幂等地注册一次，缓存成功状态避免每轮重复。
_client_registered: dict[str, bool] = {}
_register_lock = asyncio.Lock()


def _enabled() -> bool:
    """Read the environment flag that enables the worker; default is off."""
    return os.environ.get("NEKO_FACTS_SYNC_ENABLED", "0") in ("1", "true", "TRUE", "yes")


def _social_base_url() -> str | None:
    raw = os.environ.get("NEKO_SOCIAL_BASE_URL", "").strip().rstrip("/")
    return raw or None


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("facts_sync: read %s failed: %s", path, exc)
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _get_client_credentials() -> tuple[str, str] | None:
    """Return persisted client credentials before any cloud request."""
    try:
        cm = get_config_manager()
        client_id, client_proof = cm.ensure_cloudsave_client_credentials()
        if not client_id or not client_proof:
            return None
        return client_id, client_proof
    except Exception as exc:  # noqa: BLE001
        logger.warning("facts_sync: failed to load or persist client credentials: %s", exc)
    return None


def _get_client_id() -> str | None:
    credentials = _get_client_credentials()
    return credentials[0] if credentials else None


def _enumerate_lanlan_dirs(memory_dir: Path) -> list[Path]:
    """Return character directories under ``memory/``."""
    if not memory_dir.exists():
        return []
    try:
        return [d for d in memory_dir.iterdir() if d.is_dir()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("facts_sync: enumerate memory_dir failed: %s", exc)
        return []


def _coerce_importance(value: Any) -> float:
    """Defensively coerce importance; malformed values become 0 (skipped)."""
    try:
        return float(value or 0.0)
    except (OverflowError, TypeError, ValueError):
        return 0.0


def _select_unsynced_facts(
    facts_data: list[dict],
    already_synced_hashes: set[str],
) -> list[dict]:
    """Select unsynced facts that pass local filters for upload."""
    out: list[dict] = []
    for fact in facts_data:
        if not isinstance(fact, dict):
            continue
        if fact.get("private") is True or fact.get("redacted") is True:
            continue
        importance_raw = _coerce_importance(fact.get("importance"))
        if importance_raw < MIN_IMPORTANCE:
            continue
        # NEKO 0-10 → Servers 0-1 归一化；对已经在 [0,1] 范围的值无影响。
        # 单调，不破坏排序；clamp 到 [0,1] 应对偶发越界值。
        if importance_raw > 1.0:
            importance = min(importance_raw / 10.0, 1.0)
        else:
            importance = max(0.0, importance_raw)
        fact_hash = fact.get("hash") or fact.get("fact_hash")
        if not isinstance(fact_hash, str) or len(fact_hash) < 8:
            continue
        if fact_hash in already_synced_hashes:
            continue
        text = fact.get("text") or ""
        if not text or not isinstance(text, str):
            continue
        out.append({
            "fact_hash": fact_hash,
            "text": text,
            "importance": importance,
            "redacted": bool(fact.get("redacted", False)),
        })
    return out


async def _ensure_client_registered(
    base_url: str,
    client_id: str,
    client_proof: str,
) -> bool:
    """Idempotently register the current client_id with Servers.

    The first sweep must call ``POST /api/clients/register`` before facts are
    pushed; otherwise Servers cannot resolve ``X-Client-Id`` and returns 401.
    """
    cache_key = f"{base_url}|{client_id}"
    if _client_registered.get(cache_key):
        return True
    async with _register_lock:
        if _client_registered.get(cache_key):  # double-check inside lock
            return True
        url = f"{base_url}/api/clients/register"
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
                r = await client.post(
                    url,
                    json={"client_id": client_id, "client_proof": client_proof},
                )
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("facts_sync: client register HTTP failed: %s", exc)
            return False
        if r.status_code < 300:
            _client_registered[cache_key] = True
            logger.info("facts_sync: client %s… registered with Servers", client_id[:8])
            return True
        logger.warning(
            "facts_sync: client register %s returned %s: %s",
            url, r.status_code, r.text[:200],
        )
        return False


async def _post_facts_batch(
    base_url: str,
    client_id: str,
    lanlan_name: str,
    batch: list[dict],
) -> tuple[bool, dict | None]:
    url = f"{base_url}/api/facts/sync"
    headers = {
        "X-Client-Id": client_id,
        "Content-Type": "application/json",
        # 后续 M3+ 把 NEKO-PC 拿到的 JWT 通过 IPC 传过来时这里加 Authorization
    }
    payload = {"lanlan_name": lanlan_name, "facts": batch}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.post(url, headers=headers, json=payload)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("facts_sync: HTTP failed: %s", exc)
        return False, None
    if r.status_code >= 300:
        logger.warning("facts_sync: %s returned %s: %s", url, r.status_code, r.text[:200])
        return False, None
    # Any 2xx response confirms the mutation. The caller does not depend on a
    # response body, and valid 201/204 responses may be empty or non-JSON.
    try:
        response_payload = r.json()
    except (TypeError, ValueError):
        response_payload = None
    return True, response_payload if isinstance(response_payload, dict) else None


async def _sync_one_lanlan(
    memory_dir: Path,
    lanlan_dir: Path,
    client_id: str,
    base_url: str,
) -> None:
    """Sync one character directory."""
    facts_path = lanlan_dir / "facts.json"
    if not facts_path.exists():
        return

    # CodeRabbit Major: facts/state JSON read 是同步磁盘 IO，包到 asyncio.to_thread
    # 防止卡事件循环（同时也是 NEKO repo async-blocking lint 红线）。
    facts_data = await asyncio.to_thread(_read_json, facts_path, [])
    if not isinstance(facts_data, list):
        logger.warning("facts_sync: %s is not a list, skipping", facts_path)
        return

    state_path = lanlan_dir / "facts_sync_state.json"
    state = await asyncio.to_thread(
        _read_json, state_path, {"synced": [], "failed_counts": {}},
    )
    if not isinstance(state, dict):
        state = {"synced": [], "failed_counts": {}}
    synced_set: set[str] = set(state.get("synced") or [])
    failed_counts: dict[str, int] = dict(state.get("failed_counts") or {})

    candidates = _select_unsynced_facts(facts_data, synced_set)
    if not candidates:
        return

    lanlan_name = lanlan_dir.name
    logger.info(
        "facts_sync: %s — %d facts to push (already synced %d)",
        lanlan_name, len(candidates), len(synced_set),
    )

    pending_path = lanlan_dir / "facts_sync_pending.jsonl"

    # 拆批
    for i in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[i : i + BATCH_SIZE]
        ok, _resp = await _post_facts_batch(base_url, client_id, lanlan_name, batch)
        if ok:
            for f in batch:
                synced_set.add(f["fact_hash"])
                failed_counts.pop(f["fact_hash"], None)
        else:
            # 累加失败次数，到上限的扔 pending.jsonl 让人查
            for f in batch:
                h = f["fact_hash"]
                failed_counts[h] = failed_counts.get(h, 0) + 1
                if failed_counts[h] >= MAX_FAILED_ATTEMPTS:
                    await asyncio.to_thread(
                        _append_jsonl,
                        pending_path,
                        {
                            "fact_hash": h,
                            "text_preview": f["text"][:80],
                            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                    )
                    failed_counts.pop(h, None)
                    synced_set.add(h)  # 标记为"放弃同步"避免无限重试

    state["synced"] = sorted(synced_set)
    state["failed_counts"] = failed_counts
    state["last_sweep_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        await asyncio.to_thread(_write_json_atomic, state_path, state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("facts_sync: write state %s failed: %s", state_path, exc)


async def _sweep_once() -> None:
    base_url = _social_base_url()
    if not base_url:
        return
    credentials = _get_client_credentials()
    if not credentials:
        logger.warning("facts_sync: no client credentials available, skipping sweep")
        return
    client_id, client_proof = credentials

    # M2-i fix: bootstrap register before pushing；失败则跳过本轮，下次再试。
    if not await _ensure_client_registered(base_url, client_id, client_proof):
        logger.warning("facts_sync: client not registered with Servers; skipping sweep")
        return

    try:
        cm = get_config_manager()
        memory_dir = Path(cm.memory_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("facts_sync: get memory_dir failed: %s", exc)
        return

    for lanlan_dir in _enumerate_lanlan_dirs(memory_dir):
        try:
            await _sync_one_lanlan(memory_dir, lanlan_dir, client_id, base_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("facts_sync: sweep %s failed: %s", lanlan_dir, exc)


async def start_facts_sync_worker() -> None:
    """Run the long-lived facts sync worker scheduled by main_server startup.

    The entry gate returns quietly when the feature flag is off or the social
    base URL is missing.
    """
    if not _enabled():
        logger.info("facts_sync: disabled (set NEKO_FACTS_SYNC_ENABLED=1 to enable)")
        return
    if not _social_base_url():
        logger.warning("facts_sync: NEKO_SOCIAL_BASE_URL not set; skipping")
        return

    logger.info(
        "facts_sync: starting (interval=%ds, batch=%d, min_importance=%.2f)",
        SYNC_INTERVAL_SEC, BATCH_SIZE, MIN_IMPORTANCE,
    )
    # 首次延迟启动 30 秒，避开主服务启动忙时
    try:
        await asyncio.sleep(30.0)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            logger.info("facts_sync: cancelled")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("facts_sync: sweep raised: %s", exc)

        try:
            await asyncio.sleep(SYNC_INTERVAL_SEC)
        except asyncio.CancelledError:
            return
