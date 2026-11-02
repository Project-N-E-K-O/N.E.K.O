#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
N.E.K.O Telemetry Collection Server

Anonymous LLM token usage collection. Security: HMAC signature + timestamp
anti-replay + rate limiting.

Deployment:
    pip install -r requirements.txt
    python server.py --port 8099 --admin-token YOUR_TOKEN

    # or Docker
    docker-compose up -d

Capacity: 20k DAU × 3 processes × 6 req/h × 8h ≈ 2.88M req/day ≈ 33 req/s peak
      SQLite WAL sustains ~500 write/s; a single instance is enough.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hmac
import io
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from models import TelemetrySubmission, SubmitResponse, model_to_dict, model_to_json, model_from_json
from security import verify_signature, verify_timestamp, RateLimiter, DEFAULT_HMAC_SECRET
from storage import TelemetryStorage, normalize_steam_id, normalize_device_hw

# ---------------------------------------------------------------------------
# Configuration.
# ---------------------------------------------------------------------------

HMAC_SECRET = os.getenv("TELEMETRY_HMAC_SECRET", DEFAULT_HMAC_SECRET)
DB_PATH = os.getenv("TELEMETRY_DB_PATH", "./data/telemetry.db")
ADMIN_TOKEN = os.getenv("TELEMETRY_ADMIN_TOKEN", "")
MAX_BODY_SIZE = 512 * 1024  # 512 KB wire-byte limit, usually <=50 KB after gzip.
# Decompressed-byte limit. Typical client payloads are 5-50 KB raw, and future telemetry still fits 1 MB;
# allow 2 MB headroom while bounding gzip bombs, even with 1:1000 ratios, to 2 MB.
MAX_DECOMPRESSED_SIZE = 2 * 1024 * 1024

# ---------------------------------------------------------------------------
# Initialization.
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telemetry")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
storage = TelemetryStorage(DB_PATH)
rate_limiter = RateLimiter(max_requests=120, window=3600.0)

# Serialize canonical edge construction/recomputation: deployment uses one worker (Dockerfile --workers 1 /
# single-process systemd), but manual /admin/canonical/rebuild can still overlap background
# _periodic_canonical_rebuild in that process. Competing build_edges calls share a cursor,
# duplicate processing, and double observe_count; an in-process asyncio.Lock removes this real race.
_canonical_lock = asyncio.Lock()

app = FastAPI(
    title="N.E.K.O Telemetry",
    version="1.0.0",
    docs_url="/docs" if os.getenv("TELEMETRY_ENABLE_DOCS") == "1" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "NEKO_TELEMETRY_CORS_ORIGINS", "http://localhost,http://127.0.0.1"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


def _decompress_if_gzip(body_bytes: bytes, content_encoding: str) -> bytes:
    """Decompress the request body according to the ``Content-Encoding`` header.

    Backward compat: when the header is missing or 'identity', pass the raw bytes
    through so old clients (v1 always sent bare JSON) keep working.

    Zip-bomb defense: streamed read; reject immediately once the output exceeds
    MAX_DECOMPRESSED_SIZE, never letting gzip.decompress expand arbitrarily large
    data in memory.
    """
    enc = (content_encoding or "").strip().lower()
    if enc in ("", "identity"):
        return body_bytes
    if enc != "gzip":
        raise HTTPException(415, f"Unsupported Content-Encoding: {enc}")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body_bytes), mode="rb") as gz:
            # Read one byte beyond cap to detect overflow and return 413 immediately rather than decompressing an entire
            # bomb into memory.
            decompressed = gz.read(MAX_DECOMPRESSED_SIZE + 1)
        if len(decompressed) > MAX_DECOMPRESSED_SIZE:
            raise HTTPException(413, "Decompressed payload too large")
        return decompressed
    except HTTPException:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        raise HTTPException(400, f"Invalid gzip body: {e}")


def _extract_token(request: Request) -> str:
    """Extract the admin token from the header or the URL ?token= param."""
    # Prefer URL parameters for direct browser dashboard access.
    url_token = request.query_params.get("token", "").strip()
    if url_token:
        return url_token
    # Then try the Authorization header.
    auth = request.headers.get("Authorization", "")
    return (auth[len("Bearer "):] if auth.startswith("Bearer ") else auth).strip()


def require_admin(request: Request):
    if not ADMIN_TOKEN:
        raise HTTPException(503, "Admin API not configured (set TELEMETRY_ADMIN_TOKEN env var on server)")
    token = _extract_token(request)
    # Constant-time comparison: the admin endpoint exposes device_id / steam_user_id,
    # and a plain != leaks byte-by-byte match progress to a timing side channel.
    # compare_digest eliminates that. Guard non-ASCII first: compare_digest raises
    # TypeError on non-ASCII str, otherwise ?token=é turns a 401 into a 500.
    if not token or not token.isascii() or not ADMIN_TOKEN.isascii() or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Invalid admin token")


# ---------------------------------------------------------------------------
# Public client reporting with HMAC verification.
# ---------------------------------------------------------------------------

@app.post("/api/v1/telemetry", response_model=SubmitResponse)
async def submit_telemetry(request: Request):
    """Receive telemetry. Validation flow: body size → decompress → timestamp → HMAC signature → rate limit → store."""
    # Wire-body limit: usually <=50 KB gzipped, with 512 KB headroom.
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_SIZE:
        raise HTTPException(413, "Payload too large")

    # Decompress Content-Encoding: gzip; pass older clients without the header through unchanged.
    body_bytes = _decompress_if_gzip(body_bytes, request.headers.get("Content-Encoding", ""))

    try:
        body_json = body_bytes.decode("utf-8")
        submission = model_from_json(TelemetrySubmission, body_json)
    except Exception as e:
        raise HTTPException(400, f"Invalid request: {e}")

    # Timestamp.
    if not verify_timestamp(submission.timestamp):
        raise HTTPException(403, "Timestamp out of range")

    # HMAC: verify using the same canonical JSON as clients, with sort_keys=True,
    # not Pydantic model_to_json, whose serializer may change key order/float formatting.
    try:
        body_dict = json.loads(body_bytes)
        payload_json = json.dumps(body_dict["payload"], ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(400, "Malformed payload")
    if not verify_signature(payload_json, submission.timestamp, submission.signature, HMAC_SECRET):
        raise HTTPException(403, "Invalid signature")

    # Rate limit.
    device_id = submission.payload.device_id
    if not rate_limiter.is_allowed(device_id):
        raise HTTPException(429, "Rate limit exceeded")

    # Idempotent deduplication: never accumulate the same batch_id twice.
    batch_id = submission.batch_id
    if storage.is_duplicate_batch(batch_id):
        return SubmitResponse(ok=True, message="duplicate, skipped")

    # Validate/normalize steam_user_id at the boundary using storage.normalize_steam_id, shared with canonical
    # edge construction from events.payload; divergent write paths would split one account across two
    # device/account identities. See its docstring for u64 limits, leading-zero removal,
    # sentinel exclusion, and type guards. Open-source clients expose HMAC secrets, making forged requests plausible and validation mandatory.
    steam_user_id = normalize_steam_id(submission.payload.steam_user_id)
    # Also allowlist device_hw at ingest: client strings are untrusted; normalize invalid/forged values to '',
    # maintaining low cardinality and zero PII for device profiles (see normalize_device_hw).
    device_hw = normalize_device_hw(submission.payload.device_hw)

    # Storage.
    try:
        daily_stats_dict = {k: model_to_dict(v) for k, v in submission.payload.daily_stats.items()}
        # instruments is optional; older clients omit it, yielding submission.payload.instruments=None.
        # For Pydantic v1/v2 compatibility, flatten nested models with model_to_dict.
        instruments_dict = (
            model_to_dict(submission.payload.instruments)
            if submission.payload.instruments is not None
            else None
        )
        storage.store_event(
            device_id=device_id,
            app_version=submission.payload.app_version,
            payload_json=payload_json,
            daily_stats=daily_stats_dict,
            batch_id=batch_id,
            branch=submission.payload.branch,
            locale=submission.payload.locale,
            timezone=submission.payload.timezone,
            distribution=submission.payload.distribution,
            steam_user_id=steam_user_id,
            device_hw=device_hw,
            instruments=instruments_dict,
        )
    except Exception as e:
        logger.error(f"Store failed for {device_id[:8]}...: {e}")
        raise HTTPException(500, "Storage error")

    logger.info(f"OK device={device_id[:8]}... v={submission.payload.app_version} days={len(submission.payload.daily_stats)}")
    return SubmitResponse()


# ---------------------------------------------------------------------------
# Public health check.
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "neko-telemetry"}


# ---------------------------------------------------------------------------
# Admin API, requiring an admin token.
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/stats", dependencies=[Depends(require_admin)])
async def admin_global_stats(days: int = 30):
    """Global stats JSON."""
    return storage.get_global_stats(days=min(days, 365))


@app.get("/api/v1/admin/devices", dependencies=[Depends(require_admin)])
async def admin_devices(days: int = 7):
    """Active device list."""
    return storage.get_active_devices(days=min(days, 90))


@app.post("/api/v1/admin/prune", dependencies=[Depends(require_admin)])
async def admin_prune(max_days: int = 180):
    """Prune old event logs + instrument aggregates (daily_aggregates are kept forever)."""
    days = max(max_days, 30)
    deleted_events = storage.prune_old_events(max_days=days)
    deleted_instruments = storage.prune_old_instruments(max_days=days)
    return {"deleted_events": deleted_events, "deleted_instruments": deleted_instruments}


# ---------------------------------------------------------------------------
# Export, requiring an admin token.
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/export/daily.csv", dependencies=[Depends(require_admin)])
async def export_daily_csv(days: int = 90):
    """Export per-day aggregated CSV."""
    csv_text = storage.export_daily_csv(days=min(days, 365))
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=daily_stats.csv"})


@app.get("/api/v1/admin/export/model.csv", dependencies=[Depends(require_admin)])
async def export_model_csv(days: int = 90):
    """Export per-model aggregated CSV."""
    csv_text = storage.export_model_csv(days=min(days, 365))
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=model_stats.csv"})


@app.get("/api/v1/admin/instruments", dependencies=[Depends(require_admin)])
async def admin_instruments(days: int = 7):
    """Instrument metrics JSON: top counters + histogram p50/p95 summaries.

    The public repo no longer ships an HTML dashboard (see README); this is the data
    endpoint for internal dashboards, on which operators build their own
    visualization. Counter/histogram semantics: see storage.get_top_counters /
    get_histogram_summary.
    """
    days = min(days, 365)
    return {
        "counters": storage.get_top_counters(days=days, limit=50),
        "histograms": storage.get_histogram_summary(days=days, limit=50),
    }


# ---------------------------------------------------------------------------
# Canonical identity, requiring an admin token.
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/canonical/metrics", dependencies=[Depends(require_admin)])
async def admin_canonical_metrics(days: int = 30):
    """User metrics JSON: device-based (install count) and canonical (deduped per real person) side by side."""
    days = min(days, 365)
    return {
        "device": storage.get_user_metrics(days=days),
        "canonical": storage.get_canonical_metrics(days=days),
    }


@app.post("/api/v1/admin/canonical/rebuild", dependencies=[Depends(require_admin)])
async def admin_canonical_rebuild():
    """Manual trigger: scan events to build edges (drain until caught up) + recompute canonical connected components."""
    # Offload synchronous SQLite/union-find to the thread pool to avoid blocking the loop and slowing public ingest.
    # Serialize with _canonical_lock to avoid concurrent cursor consumption with background maintenance.
    async with _canonical_lock:
        processed = await asyncio.to_thread(storage.build_all_pending_edges)
        canonicals = await asyncio.to_thread(storage.recompute_canonical)
    return {"events_processed": processed, "canonical_count": canonicals}


@app.post("/api/v1/admin/canonical/denylist", dependencies=[Depends(require_admin)])
async def admin_canonical_denylist(steam_user_id: str):
    """Account deletion: Steam64 into denylist (prevents resurrection) + redact source data + delete edges + recompute."""
    async with _canonical_lock:
        sid = await asyncio.to_thread(storage.add_steam_id_to_denylist, steam_user_id)
        if not sid:
            raise HTTPException(400, "invalid steam_user_id")
        await asyncio.to_thread(storage.recompute_canonical)
    return {"denylisted": sid}


# ---------------------------------------------------------------------------
# Periodic maintenance.
# ---------------------------------------------------------------------------

async def _periodic_rate_limiter_cleanup():
    """Hourly cleanup of rate-limit records for inactive devices, preventing slow memory bloat."""
    while True:
        await asyncio.sleep(3600)
        try:
            rate_limiter.cleanup_stale()
        except Exception:
            pass


async def _periodic_canonical_rebuild():
    """Every 5 minutes, incrementally scan events to build edges + recompute canonical and persist.

    Blocking SQLite calls run in the thread pool to keep off the event loop. The
    need_recompute dirty flag guarantees "edge production must be followed by one
    successful recompute": build_all_pending_edges has already advanced the cursor,
    so if the recompute right after it throws (e.g. a transient SQLite lock), the
    dirty flag stays True and the next tick retries even without new events — the
    canonical_map never gets stuck permanently in the inconsistent state where build
    advanced but recompute didn't follow.
    """
    # No in-memory dirty flag: at a few thousand edges, full union-find recomputation takes milliseconds. Unconditional
    # recomputation each tick is simplest and robust against missing/lost flags:
    #   Run at startup, sleeping afterward rather than waiting 5 minutes.
    #   Realign canonical_map with existing edges after crashes/restarts.
    #   If admin /rebuild or /denylist recomputation fails, the next tick retries unconditionally, without relying
    #   on coincidental new events or cross-coroutine flags.
    # The only cost is millisecond-scale recomputation without changes, which is negligible.
    while True:
        try:
            async with _canonical_lock:  # Serialize recomputation against manual /rebuild and /denylist.
                await asyncio.to_thread(storage.build_all_pending_edges)
                await asyncio.to_thread(storage.recompute_canonical)
        except Exception:
            logger.exception("canonical rebuild failed")
        await asyncio.sleep(300)


@app.on_event("startup")
async def on_startup():
    rate_limiter.cleanup_stale()
    # Synchronize canonical_map at startup using only millisecond-scale recomputation/union-find over existing edges,
    # without scanning events, so the first post-restart /canonical/metrics matches current edges.
    # Deliberately avoid synchronous build_all_pending_edges here: full event draining
    # may take minutes after initial deployment/long downtime, blocking startup, health checks, and ingest.
    # Leave expensive edge building to background work below; until complete, metrics COALESCE falls back to
    # device-based counts, a reasonable fallback rather than erroneous data.
    try:
        await asyncio.to_thread(storage.recompute_canonical)
    except Exception:
        logger.exception("startup canonical recompute failed")
    asyncio.create_task(_periodic_rate_limiter_cleanup())
    asyncio.create_task(_periodic_canonical_rebuild())
    logger.info(f"Telemetry server started. DB={DB_PATH}")
    if not ADMIN_TOKEN:
        logger.warning("⚠ TELEMETRY_ADMIN_TOKEN not set — admin API disabled")


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="N.E.K.O Telemetry Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--db", default=None)
    parser.add_argument("--admin-token", default=None, help="Admin API token")
    args = parser.parse_args()

    if args.db:
        DB_PATH = args.db
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        storage = TelemetryStorage(DB_PATH)
    if args.admin_token:
        ADMIN_TOKEN = args.admin_token

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
