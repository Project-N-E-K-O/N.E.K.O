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
import html
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse

from models import TelemetrySubmission, SubmitResponse, model_to_dict, model_to_json, model_from_json
from security import verify_signature, verify_timestamp, RateLimiter, DEFAULT_HMAC_SECRET
from storage import TelemetryStorage

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

    # 存储
    try:
        daily_stats_dict = {k: model_to_dict(v) for k, v in submission.payload.daily_stats.items()}
        storage.store_event(
            device_id=device_id,
            app_version=submission.payload.app_version,
            payload_json=payload_json,
            daily_stats=daily_stats_dict,
            batch_id=batch_id,
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
# 仪表盘（需 admin token）
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/dashboard", dependencies=[Depends(require_admin)])
async def admin_dashboard(request: Request, days: int = 30):
    """HTML 仪表盘 — 浏览器直接访问 ?token=YOUR_TOKEN。"""
    days = min(days, 365)
    stats = storage.get_global_stats(days=days)
    devices = storage.get_active_devices(days=7, limit=20)
    user_metrics = storage.get_user_metrics(days=days)

    # 传递 token 到导出链接
    tk = _extract_token(request)

    # 按日期排序
    sorted_days = sorted(stats.get("daily_totals", {}).items(), reverse=True)

    # --- DAU 趋势（最近的放右边） ---
    dau_trend = user_metrics.get("dau_trend", {})
    dau_sorted = sorted(dau_trend.items())  # 日期升序
    dau_labels = [d[5:] for d, _ in dau_sorted]  # MM-DD
    dau_values = [v for _, v in dau_sorted]
    dau_max = max(dau_values) if dau_values else 1

    # DAU 柱状图（纯 CSS，无 JS 依赖）
    dau_bars = ""
    for i, (label, val) in enumerate(zip(dau_labels, dau_values)):
        pct = val / dau_max * 100 if dau_max > 0 else 0
        dau_bars += (
            f'<div class="bar-col" title="{label}: {val}">'
            f'<div class="bar-val">{val}</div>'
            f'<div class="bar" style="height:{pct}%"></div>'
            f'<div class="bar-label">{label}</div>'
            f'</div>'
        )

    # --- 新增设备趋势 ---
    new_trend = user_metrics.get("new_device_trend", {})
    new_sorted = sorted(new_trend.items())
    new_labels = [d[5:] for d, _ in new_sorted]
    new_values = [v for _, v in new_sorted]
    new_max = max(new_values) if new_values else 1

    new_bars = ""
    for label, val in zip(new_labels, new_values):
        pct = val / new_max * 100 if new_max > 0 else 0
        new_bars += (
            f'<div class="bar-col" title="{label}: {val}">'
            f'<div class="bar-val">{val}</div>'
            f'<div class="bar" style="height:{pct}%"></div>'
            f'<div class="bar-label">{label}</div>'
            f'</div>'
        )

    # 构建表格
    daily_rows = ""
    for d, s in sorted_days:
        day_dau = dau_trend.get(d, 0)
        daily_rows += f"""<tr>
            <td>{d}</td>
            <td>{day_dau}</td>
            <td>{s['call_count']:,}</td>
            <td>{s['prompt_tokens']:,}</td>
            <td>{s['cached_tokens']:,}</td>
            <td>{s['completion_tokens']:,}</td>
            <td>{s['total_tokens']:,}</td>
            <td>{s['error_count']:,}</td>
        </tr>"""

    esc = html.escape

    model_rows = ""
    for m, s in sorted(stats.get("by_model", {}).items(), key=lambda x: -x[1]["total_tokens"]):
        model_rows += f"""<tr>
            <td>{esc(m)}</td>
            <td>{s['call_count']:,}</td>
            <td>{s['prompt_tokens']:,}</td>
            <td>{s['cached_tokens']:,}</td>
            <td>{s['completion_tokens']:,}</td>
            <td>{s['total_tokens']:,}</td>
        </tr>"""

    type_rows = ""
    for t, s in sorted(stats.get("by_call_type", {}).items(), key=lambda x: -x[1]["total_tokens"]):
        type_rows += f"""<tr>
            <td>{esc(t)}</td>
            <td>{s['call_count']:,}</td>
            <td>{s['prompt_tokens']:,}</td>
            <td>{s['cached_tokens']:,}</td>
            <td>{s['completion_tokens']:,}</td>
            <td>{s['total_tokens']:,}</td>
        </tr>"""

    device_rows = ""
    for d in devices:
        did = esc(d['device_id'])
        device_rows += f"""<tr>
            <td title="{did}">{did[:12]}...</td>
            <td>{esc(d['app_version'])}</td>
            <td>{esc(d['last_seen'][:19])}</td>
            <td>{d['recent_calls']:,}</td>
            <td>{d['recent_tokens']:,}</td>
        </tr>"""

    page_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>N.E.K.O Telemetry Dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 1200px; margin: 0 auto; padding: 20px; background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; }} h2 {{ color: #8b949e; margin-top: 2em; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 16px 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 16px 24px; min-width: 140px; }}
  .card .value {{ font-size: 2em; font-weight: bold; color: #58a6ff; }}
  .card .label {{ color: #8b949e; font-size: 0.9em; }}
  .card.green .value {{ color: #3fb950; }}
  .card.orange .value {{ color: #d29922; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0; }}
  th, td {{ padding: 8px 12px; text-align: right; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; font-weight: 600; }} td:first-child, th:first-child {{ text-align: left; }}
  tr:hover {{ background: #161b22; }}
  .export {{ margin: 16px 0; }}
  .export a {{ color: #58a6ff; margin-right: 16px; }}
  .chart {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
            padding: 16px; margin: 16px 0; overflow-x: auto; }}
  .chart-title {{ color: #8b949e; font-size: 0.9em; margin-bottom: 8px; }}
  .bar-chart {{ display: flex; align-items: flex-end; gap: 2px; height: 120px; min-width: max-content; }}
  .bar-col {{ display: flex; flex-direction: column; align-items: center; min-width: 28px; height: 100%; justify-content: flex-end; }}
  .bar {{ background: #58a6ff; width: 20px; border-radius: 2px 2px 0 0; min-height: 2px; transition: height 0.3s; }}
  .bar-val {{ font-size: 0.7em; color: #8b949e; margin-bottom: 2px; }}
  .bar-label {{ font-size: 0.65em; color: #484f58; margin-top: 4px; writing-mode: vertical-rl; text-orientation: mixed; height: 40px; }}
  .chart.new-devices .bar {{ background: #3fb950; }}
</style>
</head><body>
<h1>N.E.K.O Telemetry Dashboard</h1>

<div class="cards">
  <div class="card"><div class="value">{user_metrics['dau_today']:,}</div><div class="label">DAU (Today)</div></div>
  <div class="card"><div class="value">{user_metrics['wau']:,}</div><div class="label">WAU (7d)</div></div>
  <div class="card"><div class="value">{user_metrics['mau']:,}</div><div class="label">MAU (30d)</div></div>
  <div class="card"><div class="value">{stats['total_devices']:,}</div><div class="label">Total Devices</div></div>
  <div class="card green"><div class="value">{user_metrics['d1_retention']}%</div><div class="label">D1 Retention</div></div>
  <div class="card orange"><div class="value">{user_metrics['d7_retention']}%</div><div class="label">D7 Retention</div></div>
  <div class="card"><div class="value">{stats['total_events']:,}</div><div class="label">Total Events</div></div>
</div>

<div class="chart">
  <div class="chart-title">DAU Trend (last {days} days)</div>
  <div class="bar-chart">{dau_bars}</div>
</div>

<div class="chart new-devices">
  <div class="chart-title">New Devices (last {days} days)</div>
  <div class="bar-chart">{new_bars}</div>
</div>

<div class="export">
  Export:
  <a href="/api/v1/admin/export/daily.csv?days={days}&token={tk}">Daily CSV</a>
  <a href="/api/v1/admin/export/model.csv?days={days}&token={tk}">Model CSV</a>
</div>

<h2>Daily Totals (last {days} days)</h2>
<table>
  <tr><th>Date</th><th>DAU</th><th>Calls</th><th>Prompt</th><th>Cached</th><th>Completion</th><th>Total</th><th>Errors</th></tr>
  {daily_rows}
</table>

<h2>By Model</h2>
<table>
  <tr><th>Model</th><th>Calls</th><th>Prompt</th><th>Cached</th><th>Completion</th><th>Total</th></tr>
  {model_rows}
</table>

<h2>By Call Type</h2>
<table>
  <tr><th>Type</th><th>Calls</th><th>Prompt</th><th>Cached</th><th>Completion</th><th>Total</th></tr>
  {type_rows}
</table>

<h2>Active Devices (7d, top 20)</h2>
<table>
  <tr><th>Device</th><th>Version</th><th>Last Seen</th><th>Calls</th><th>Tokens</th></tr>
  {device_rows}
</table>

</body></html>"""

    return HTMLResponse(page_html)


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


@app.on_event("startup")
async def on_startup():
    rate_limiter.cleanup_stale()
    asyncio.create_task(_periodic_rate_limiter_cleanup())
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
