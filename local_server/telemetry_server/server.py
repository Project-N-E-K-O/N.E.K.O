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
# 配置
# ---------------------------------------------------------------------------

HMAC_SECRET = os.getenv("TELEMETRY_HMAC_SECRET", DEFAULT_HMAC_SECRET)
DB_PATH = os.getenv("TELEMETRY_DB_PATH", "./data/telemetry.db")
ADMIN_TOKEN = os.getenv("TELEMETRY_ADMIN_TOKEN", "")
MAX_BODY_SIZE = 512 * 1024  # 512 KB（线路上的字节上限，gzip 后通常 ≤50KB）
# 解压后的字节上限。客户端典型 payload 5-50KB raw，未来加埋点也压得住 1MB；
# 设 2MB 给余量，同时挡 zip bomb（gzip 比 1:1000 也只能撑到 2MB）。
MAX_DECOMPRESSED_SIZE = 2 * 1024 * 1024

# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telemetry")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
storage = TelemetryStorage(DB_PATH)
rate_limiter = RateLimiter(max_requests=120, window=3600.0)

# 串行化 canonical 边构建 + 重算：部署是单 worker（Dockerfile --workers 1 /
# systemd 单进程），但手动 /admin/canonical/rebuild 仍可能与后台
# _periodic_canonical_rebuild 在同一进程并发跑，两个 build_edges 抢同一游标会
# 重复处理、把 observe_count 翻倍。进程内 asyncio.Lock 即可消除这条真实路径。
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
            # 多读 1 字节用来判定是否超过 cap —— 超了直接 413，省得把整个
            # bomb 解到内存里。
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
    # 优先 URL 参数（方便浏览器直接访问仪表盘）
    url_token = request.query_params.get("token", "").strip()
    if url_token:
        return url_token
    # 其次 Authorization Header
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
# 客户端上报（公开，HMAC 验证）
# ---------------------------------------------------------------------------

@app.post("/api/v1/telemetry", response_model=SubmitResponse)
async def submit_telemetry(request: Request):
    """Receive telemetry. Validation flow: body size → decompress → timestamp → HMAC signature → rate limit → store."""
    # Body 大小（wire size，gzip 后通常 ≤50KB，给 512KB 余量）
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_SIZE:
        raise HTTPException(413, "Payload too large")

    # 解压（如 Content-Encoding: gzip）；老客户端不带 header 直接透传
    body_bytes = _decompress_if_gzip(body_bytes, request.headers.get("Content-Encoding", ""))

    try:
        body_json = body_bytes.decode("utf-8")
        submission = model_from_json(TelemetrySubmission, body_json)
    except Exception as e:
        raise HTTPException(400, f"Invalid request: {e}")

    # 时间戳
    if not verify_timestamp(submission.timestamp):
        raise HTTPException(403, "Timestamp out of range")

    # HMAC — 使用与客户端相同的 canonical JSON（sort_keys=True）验签，
    # 而非 Pydantic model_to_json（其序列化器可能改变 key 顺序/float 格式）
    try:
        body_dict = json.loads(body_bytes)
        payload_json = json.dumps(body_dict["payload"], ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(400, "Malformed payload")
    if not verify_signature(payload_json, submission.timestamp, submission.signature, HMAC_SECRET):
        raise HTTPException(403, "Invalid signature")

    # 速率限制
    device_id = submission.payload.device_id
    if not rate_limiter.is_allowed(device_id):
        raise HTTPException(429, "Rate limit exceeded")

    # 幂等去重：相同 batch_id 不重复累加
    batch_id = submission.batch_id
    if storage.is_duplicate_batch(batch_id):
        return SubmitResponse(ok=True, message="duplicate, skipped")

    # steam_user_id 边界校验 + 归一化：复用 storage.normalize_steam_id，与 canonical
    # 边构建（扫 events.payload）共用同一份规则——否则两条写路径漂移会把同账号
    # 拆成两个 device↔account 维度。规则细节见该函数 docstring（u64 范围 / 去前导零 /
    # 排哨兵 / 类型守卫）。HMAC secret 在开源客户端可读，伪造请求是有概率事件，故必校验。
    steam_user_id = normalize_steam_id(submission.payload.steam_user_id)
    # device_hw 同样在 ingest 边界白名单化：客户端串不可信，非法/伪造一律归 ''，
    # 守"设备画像"低基数 + 零 PII 契约（见 normalize_device_hw）。
    device_hw = normalize_device_hw(submission.payload.device_hw)

    # 存储
    try:
        daily_stats_dict = {k: model_to_dict(v) for k, v in submission.payload.daily_stats.items()}
        # instruments 是 Optional —— 老客户端不带这个字段时 submission.payload.instruments
        # 为 None。Pydantic v1/v2 兼容：用 model_to_dict 拆掉嵌套 model。
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
# 健康检查（公开）
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "neko-telemetry"}


# ---------------------------------------------------------------------------
# 管理端 API（需 admin token）
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
# 导出（需 admin token）
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
# canonical identity（需 admin token）
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
    # 同步 SQLite/union-find 丢线程池，别卡住事件循环（拖慢公开上报接口）。
    # _canonical_lock 串行化，避免与后台周期任务并发抢游标重复处理。
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
# 定期维护
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
    # 不用内存脏标记：recompute 在本量级（几千边、全量 union-find）是毫秒级，每 tick
    # 无条件重算最简单也最稳，一次性覆盖所有"标记没设/丢失"的坑——
    #   启动即跑（sleep 放末尾），不必等 5 分钟；
    #   进程崩溃重启后无条件对齐 canonical_map 与现有边；
    #   admin /rebuild|/denylist 自身 recompute 失败时，下个 tick 必重算兜底，不依赖
    #   "恰好有新事件"或任何跨协程标记。
    # 代价仅是无变更时也跑一次重算，毫秒级，可忽略。
    while True:
        try:
            async with _canonical_lock:  # 与手动 /rebuild、/denylist 互斥，串行化重算
                await asyncio.to_thread(storage.build_all_pending_edges)
                await asyncio.to_thread(storage.recompute_canonical)
        except Exception:
            logger.exception("canonical rebuild failed")
        await asyncio.sleep(300)


@app.on_event("startup")
async def on_startup():
    rate_limiter.cleanup_stale()
    # 启动先同步对齐一次 canonical_map（只跑 recompute，毫秒级、只对现有边做
    # union-find，不扫事件）——重启后首个 /canonical/metrics 即读到与现有边一致的
    # 结果。故意**不**在这里同步跑 build_all_pending_edges：那是全量事件 drain，
    # 首次部署/长停机后可能耗时数分钟，会阻塞启动、拖垮 healthcheck 与 ingest。
    # 昂贵的事件建边留给下面的后台任务；建边完成前 metrics 由 COALESCE 回退到
    # device 口径（合理降级，非错误数据）。
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
# 入口
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
