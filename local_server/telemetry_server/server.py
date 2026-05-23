#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N.E.K.O Telemetry Collection Server

匿名 LLM token 用量收集。安全机制：HMAC 签名 + 时间戳防重放 + 速率限制。

部署：
    pip install -r requirements.txt
    python server.py --port 8099 --admin-token YOUR_TOKEN

    # 或 Docker
    docker-compose up -d

容量：20k DAU × 3 进程 × 6 req/h × 8h ≈ 2.88M req/day ≈ 33 req/s peak
      SQLite WAL 可承载 ~500 write/s，单实例足够。
"""
from __future__ import annotations

import argparse
import asyncio
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
from storage import TelemetryStorage, normalize_steam_id

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HMAC_SECRET = os.getenv("TELEMETRY_HMAC_SECRET", DEFAULT_HMAC_SECRET)
DB_PATH = os.getenv("TELEMETRY_DB_PATH", "./data/telemetry.db")
ADMIN_TOKEN = os.getenv("TELEMETRY_ADMIN_TOKEN", "")
MAX_BODY_SIZE = 512 * 1024  # 512 KB

# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telemetry")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
storage = TelemetryStorage(DB_PATH)
rate_limiter = RateLimiter(max_requests=120, window=3600.0)

app = FastAPI(
    title="N.E.K.O Telemetry",
    version="1.0.0",
    docs_url="/docs" if os.getenv("TELEMETRY_ENABLE_DOCS") == "1" else None,
    redoc_url=None,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"], allow_headers=["*"])


def _extract_token(request: Request) -> str:
    """从 Header 或 URL ?token= 中提取 admin token。"""
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
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid admin token")


# ---------------------------------------------------------------------------
# 客户端上报（公开，HMAC 验证）
# ---------------------------------------------------------------------------

@app.post("/api/v1/telemetry", response_model=SubmitResponse)
async def submit_telemetry(request: Request):
    """接收遥测数据。验证流程：body 大小 → 时间戳 → HMAC 签名 → 速率限制 → 存储。"""
    # Body 大小
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_SIZE:
        raise HTTPException(413, "Payload too large")

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

    # 存储
    try:
        daily_stats_dict = {k: model_to_dict(v) for k, v in submission.payload.daily_stats.items()}
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
    """全局统计 JSON。"""
    return storage.get_global_stats(days=min(days, 365))


@app.get("/api/v1/admin/devices", dependencies=[Depends(require_admin)])
async def admin_devices(days: int = 7):
    """活跃设备列表。"""
    return storage.get_active_devices(days=min(days, 90))


@app.post("/api/v1/admin/prune", dependencies=[Depends(require_admin)])
async def admin_prune(max_days: int = 180):
    """清理旧事件日志（聚合数据保留）。"""
    deleted = storage.prune_old_events(max_days=max(max_days, 30))
    return {"deleted_events": deleted}


# ---------------------------------------------------------------------------
# 导出（需 admin token）
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/export/daily.csv", dependencies=[Depends(require_admin)])
async def export_daily_csv(days: int = 90):
    """按日汇总导出 CSV。"""
    csv_text = storage.export_daily_csv(days=min(days, 365))
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=daily_stats.csv"})


@app.get("/api/v1/admin/export/model.csv", dependencies=[Depends(require_admin)])
async def export_model_csv(days: int = 90):
    """按模型汇总导出 CSV。"""
    csv_text = storage.export_model_csv(days=min(days, 365))
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=model_stats.csv"})


# ---------------------------------------------------------------------------
# canonical identity（需 admin token）
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/canonical/metrics", dependencies=[Depends(require_admin)])
async def admin_canonical_metrics(days: int = 30):
    """用户指标 JSON：device 口径（装机量）与 canonical 口径（按真人去重）并列。"""
    days = min(days, 365)
    return {
        "device": storage.get_user_metrics(days=days),
        "canonical": storage.get_canonical_metrics(days=days),
    }


@app.post("/api/v1/admin/canonical/rebuild", dependencies=[Depends(require_admin)])
async def admin_canonical_rebuild():
    """手动触发：扫 events 产边（drain 到追平）+ 重算 canonical 连通分量。"""
    # 同步 SQLite/union-find 丢线程池，别卡住事件循环（拖慢公开上报接口）。
    processed = await asyncio.to_thread(storage.build_all_pending_edges)
    canonicals = await asyncio.to_thread(storage.recompute_canonical)
    return {"events_processed": processed, "canonical_count": canonicals}


@app.post("/api/v1/admin/canonical/denylist", dependencies=[Depends(require_admin)])
async def admin_canonical_denylist(steam_user_id: str):
    """删号：Steam64 入 denylist（防复活）+ 脱敏源数据 + 删边 + 重算。"""
    sid = await asyncio.to_thread(storage.add_steam_id_to_denylist, steam_user_id)
    if not sid:
        raise HTTPException(400, "invalid steam_user_id")
    await asyncio.to_thread(storage.recompute_canonical)
    return {"denylisted": sid}


# ---------------------------------------------------------------------------
# 定期维护
# ---------------------------------------------------------------------------

async def _periodic_rate_limiter_cleanup():
    """每小时清理不活跃设备的速率限制记录，防止内存缓慢膨胀。"""
    while True:
        await asyncio.sleep(3600)
        try:
            rate_limiter.cleanup_stale()
        except Exception:
            pass


async def _periodic_canonical_rebuild():
    """每 5 分钟增量扫 events 产边 + 重算 canonical 落表。

    阻塞 SQLite 调用丢线程池跑，避免占着事件循环。只有真产了新边才重算。
    """
    while True:
        await asyncio.sleep(300)
        try:
            processed = await asyncio.to_thread(storage.build_all_pending_edges)
            if processed:
                await asyncio.to_thread(storage.recompute_canonical)
        except Exception:
            logger.exception("canonical rebuild failed")


@app.on_event("startup")
async def on_startup():
    rate_limiter.cleanup_stale()
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
