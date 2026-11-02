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

from main_logic import client_registration
from utils.config_manager import get_config_manager

logger = logging.getLogger("neko.facts_sync")

# ---- tunables ----
SYNC_INTERVAL_SEC = 5 * 60  # 5 min
BATCH_SIZE = 50
# NEKO scores importance as 0-10 integers (memory/facts.py safe_importance defaults to 5);
# Servers schema requires [0,1] floats. Use NEKO's threshold 5 (medium or higher),
# normalizing to [0,1] in _select_unsynced_facts before pushing.
MIN_IMPORTANCE = 5.0  # NEKO's 0-10 scale; the server also checks importance >=0.3 after normalization.
MAX_FAILED_ATTEMPTS = 5
HTTP_TIMEOUT_SEC = 15.0

# ---- M2-i fix: bootstrap register with Servers before pushing facts ----
# Servers X-Client-Id authentication requires prior client registration, otherwise 401.
# Registration moved to main_logic.client_registration (coupon deductions / OAuth binding share
# one cache). Alias that same dict directly: 401 invalidation must affect the shared cache, or the next
# sweep reads a stale registered value written elsewhere.
_client_registered = client_registration._registered


def _enabled() -> bool:
    """Read the environment flag that enables the worker; default is off."""
    return os.environ.get("NEKO_FACTS_SYNC_ENABLED", "0") in ("1", "true", "TRUE", "yes")


def _social_base_url() -> str | None:
    """Facts sync stays opt-in: no configured URL means no outbound traffic."""
    return client_registration.configured_social_base_url()


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
        # Normalize NEKO 0-10 -> Servers 0-1; values already within [0,1] are unaffected.
        # Monotonic, preserving order; clamp to [0,1] for occasional out-of-range values.
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

    Registration itself now lives in ``main_logic.client_registration`` so the
    forge debit and OAuth bind paths share one cache and one implementation;
    this wrapper only keeps the sweep's call site and logging intact.
    """
    registered = await client_registration.ensure_client_registered(
        base_url, client_id, client_proof
    )
    if not registered:
        logger.warning(
            "facts_sync: client %s… not registered with Servers", client_id[:8]
        )
    return registered


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
        # Add Authorization here when M3+ passes the NEKO-PC JWT over IPC.
    }
    payload = {"lanlan_name": lanlan_name, "facts": batch}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.post(url, headers=headers, json=payload)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("facts_sync: HTTP failed: %s", exc)
        return False, None
    if r.status_code == 401:
        # The server no longer recognizes this registration. Let the next
        # sweep register again instead of trusting the process-lifetime cache.
        _client_registered.pop(f"{base_url}|{client_id}", None)
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

    # CodeRabbit Major: synchronous facts/state JSON disk reads go through asyncio.to_thread
    # to avoid blocking the event loop (also a NEKO async-blocking lint requirement).
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

    # Split into batches.
    for i in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[i : i + BATCH_SIZE]
        ok, _resp = await _post_facts_batch(base_url, client_id, lanlan_name, batch)
        if ok:
            for f in batch:
                synced_set.add(f["fact_hash"])
                failed_counts.pop(f["fact_hash"], None)
        else:
            # Keep failed facts eligible for the next sweep.  The pending file
            # is diagnostic only; it must never turn a failed upload into a
            # "synced" fact.
            for f in batch:
                h = f["fact_hash"]
                previous_attempts = failed_counts.get(h, 0)
                attempts = min(previous_attempts + 1, MAX_FAILED_ATTEMPTS)
                failed_counts[h] = attempts
                if (
                    attempts == MAX_FAILED_ATTEMPTS
                    and previous_attempts < MAX_FAILED_ATTEMPTS
                ):
                    await asyncio.to_thread(
                        _append_jsonl,
                        pending_path,
                        {
                            "fact_hash": h,
                            "attempts": attempts,
                            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                    )

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

    # M2-i fix: bootstrap registration before pushing; skip this cycle on failure and retry next time.
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
    # Delay the first startup by 30 seconds, avoiding the main service's busy startup period.
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
