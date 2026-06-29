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
N.E.K.O Survey Collection Server

Anonymous in-app questionnaire responses. Security mirrors the telemetry server:
HMAC signature + timestamp anti-replay + rate limiting + batch idempotency.

Deployment:
    pip install -r requirements.txt
    python server.py --port 8100 --admin-token YOUR_TOKEN

    # or Docker
    docker-compose up -d
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import hmac
import io
import json
import logging
import math
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from models import SurveySubmission, SubmitResponse, model_from_json
from security import verify_signature, verify_timestamp, RateLimiter, DEFAULT_HMAC_SECRET
from storage import SurveyStorage

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HMAC_SECRET = os.getenv("SURVEY_HMAC_SECRET", DEFAULT_HMAC_SECRET)
DB_PATH = os.getenv("SURVEY_DB_PATH", "./data/survey.db")
ADMIN_TOKEN = os.getenv("SURVEY_ADMIN_TOKEN", "")
MAX_BODY_SIZE = 256 * 1024            # 线路字节上限（gzip 后通常 ≤10KB）
MAX_DECOMPRESSED_SIZE = 1024 * 1024   # 解压上限，挡 zip bomb

# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("survey")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
storage = SurveyStorage(DB_PATH)
rate_limiter = RateLimiter(max_requests=20, window=3600.0)

app = FastAPI(
    title="N.E.K.O Survey",
    version="1.0.0",
    docs_url="/docs" if os.getenv("SURVEY_ENABLE_DOCS") == "1" else None,
    redoc_url=None,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"], allow_headers=["*"])


def _decompress_if_gzip(body_bytes: bytes, content_encoding: str) -> bytes:
    """Decompress the request body according to ``Content-Encoding`` (gzip-bomb guarded)."""
    enc = (content_encoding or "").strip().lower()
    if enc in ("", "identity"):
        return body_bytes
    if enc != "gzip":
        raise HTTPException(415, f"Unsupported Content-Encoding: {enc}")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body_bytes), mode="rb") as gz:
            decompressed = gz.read(MAX_DECOMPRESSED_SIZE + 1)
        if len(decompressed) > MAX_DECOMPRESSED_SIZE:
            raise HTTPException(413, "Decompressed payload too large")
        return decompressed
    except HTTPException:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        raise HTTPException(400, f"Invalid gzip body: {e}")


def _extract_token(request: Request) -> str:
    url_token = request.query_params.get("token", "").strip()
    if url_token:
        return url_token
    auth = request.headers.get("Authorization", "")
    return (auth[len("Bearer "):] if auth.startswith("Bearer ") else auth).strip()


def require_admin(request: Request):
    if not ADMIN_TOKEN:
        raise HTTPException(503, "Admin API not configured (set SURVEY_ADMIN_TOKEN env var on server)")
    token = _extract_token(request)
    # 常数时间比较：admin 口暴露 device_id / steam_user_id，普通 != 会泄漏逐字节
    # 匹配进度给时序侧信道，compare_digest 消除该差异。先挡非 ASCII：compare_digest
    # 对含非 ASCII 的 str 抛 TypeError，否则一个 ?token=é 就把 401 变成 500。
    if not token or not token.isascii() or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Invalid admin token")


def _sanitize_answers(answers) -> dict:
    """Whitelist + cap the answer dict at ingest (defense-in-depth for the public collector).

    The local backend (/api/survey/submit) already caps answers, but this public
    endpoint accepts any HMAC-signed body (the secret ships in the open-source
    client), so a crafted client could push oversized / deeply-nested answers. Bound
    them here independently: <= 50 keys, key <= 64 chars, string <= 2000 chars, list
    <= 50 items of <= 200 chars each; anything else is dropped.
    """
    out: dict = {}
    if not isinstance(answers, dict):
        return out
    for i, (k, v) in enumerate(answers.items()):
        if i >= 50:
            break
        if not isinstance(k, str) or not k:
            continue
        key = k[:64]
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, str):
            out[key] = v[:2000]
        elif isinstance(v, int):
            out[key] = v
        elif isinstance(v, float):
            # 拒非有限浮点：JSON 允许 NaN/Infinity，但存回去后 FastAPI 渲染
            # /api/v1/admin/responses 会对 non-finite 直接 500，一条投毒答案就能
            # 整页挂掉（仓库既有约定：存前 math.isfinite 拒 NaN/Inf）。
            if math.isfinite(v):
                out[key] = v
        elif isinstance(v, list):
            out[key] = [str(x)[:200] for x in v[:50] if isinstance(x, (str, int, float, bool))]
    return out


# ---------------------------------------------------------------------------
# 客户端上报（公开，HMAC 验证）
# ---------------------------------------------------------------------------

@app.post("/api/v1/survey", response_model=SubmitResponse)
async def submit_survey(request: Request):
    """Receive a survey response. Validation: body size → decompress → timestamp → HMAC → rate limit → store."""
    # Content-Length 预检：诚实客户端会带这个头，超限直接 413，省得先把整个 body
    # buffer 进内存再拒。无 CL / chunked 的请求落到下面的实读检查（uvicorn 自身也有
    # 帧上限兜底）。
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                raise HTTPException(413, "Payload too large")
        except ValueError:
            # CL 头不是合法整数：忽略这个预检，交给下面的实读 len 检查兜底。
            pass

    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_SIZE:
        raise HTTPException(413, "Payload too large")

    body_bytes = _decompress_if_gzip(body_bytes, request.headers.get("Content-Encoding", ""))

    try:
        body_json = body_bytes.decode("utf-8")
        submission = model_from_json(SurveySubmission, body_json)
    except Exception as e:
        raise HTTPException(400, f"Invalid request: {e}")

    if not verify_timestamp(submission.timestamp):
        raise HTTPException(403, "Timestamp out of range")

    # HMAC —— 用与客户端相同的 canonical JSON（sort_keys=True）验签
    try:
        body_dict = json.loads(body_bytes)
        payload_json = json.dumps(body_dict["payload"], ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(400, "Malformed payload")
    if not verify_signature(payload_json, submission.timestamp, submission.signature, HMAC_SECRET):
        raise HTTPException(403, "Invalid signature")

    # 元数据裸存、无 Pydantic 长度上限：持开源密钥的伪造请求可把 survey_version
    # （还建了索引）等字段塞到接近 1MB 解压上限。入口统一封顶到合理长度，挡少量
    # 伪造提交把 SQLite / admin 响应灌大。device_id 同样封顶（它进限流键 + 幂等 key）。
    device_id = (submission.payload.device_id or "")[:128]
    app_version = (submission.payload.app_version or "")[:32]
    survey_version = (submission.payload.survey_version or "")[:32]
    locale = (submission.payload.locale or "")[:35]
    branch = (submission.payload.branch or "")[:64]
    distribution = (submission.payload.distribution or "")[:32]

    if not rate_limiter.is_allowed(device_id):
        raise HTTPException(429, "Rate limit exceeded")

    action = submission.payload.action if submission.payload.action in ("submit", "skip") else "submit"

    # 幂等 key 从**已签名**的 payload 字段派生，不信任信封里的 batch_id——后者不在
    # HMAC 覆盖范围内，可被中间人/持密钥客户端改值，从而把同一份提交伪造成新行、
    # 污染 submit/skip 漏斗。(device_id, survey_version, action) 三元组与客户端的
    # 幂等语义一致：每个设备每版本每动作只算一次。
    batch_id = hashlib.sha256(
        f"{device_id}|{survey_version}|{action}".encode("utf-8")
    ).hexdigest()[:32]

    # steam_user_id 归一到 canonical 十进制 Steam64，与 telemetry normalize_steam_id
    # 同规则：纯 ASCII 数字 + <= 20 位 + 0 < int < 2^64 + str(int()) 去前导零。否则
    # 伪造请求塞 '0' / 前导零 / 超 u64 的串会污染 survey↔telemetry 的账号级 JOIN。
    raw_sid = submission.payload.steam_user_id or ""
    steam_user_id = ""
    if raw_sid.isascii() and raw_sid.isdigit() and len(raw_sid) <= 20:
        _sid_val = int(raw_sid)
        if 0 < _sid_val < (1 << 64):
            steam_user_id = str(_sid_val)

    # 旧算法 device id（迁移期跨表 JOIN 同一个人用）。不可信串，封顶长度即可。
    device_id_legacy = (submission.payload.device_id_legacy or "")[:128]

    try:
        stored = storage.store_response(
            device_id=device_id,
            device_id_legacy=device_id_legacy,
            app_version=app_version,
            survey_version=survey_version,
            locale=locale,
            branch=branch,
            distribution=distribution,
            steam_user_id=steam_user_id,
            action=action,
            answers=_sanitize_answers(submission.payload.answers),
            batch_id=batch_id,
        )
    except Exception as e:
        logger.error(f"Store failed for {device_id[:8]}...: {e}")
        raise HTTPException(500, "Storage error")

    if not stored:
        return SubmitResponse(ok=True, message="duplicate, skipped")

    # !r 转义控制符：device_id/survey_version 即便已封顶长度仍可能含伪造的 \n 等，
    # 裸 log 会让一次提交往 journald 注入额外日志行（log forging）。
    logger.info(
        f"OK device={device_id[:8]!r} survey={survey_version!r} action={action}"
    )
    return SubmitResponse()


# ---------------------------------------------------------------------------
# 健康检查（公开）
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "neko-survey"}


# ---------------------------------------------------------------------------
# 管理端 API（需 admin token）
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/summary", dependencies=[Depends(require_admin)])
async def admin_summary(survey_version: str = ""):
    """Funnel per survey version: submit/skip counts + unique devices."""
    return storage.get_summary(survey_version=survey_version)


@app.get("/api/v1/admin/responses", dependencies=[Depends(require_admin)])
async def admin_responses(survey_version: str = "", limit: int = 1000):
    """Raw submitted answers JSON (skips excluded)."""
    return {"responses": storage.get_responses(survey_version=survey_version, limit=limit)}


@app.get("/api/v1/admin/export/responses.csv", dependencies=[Depends(require_admin)])
async def export_responses_csv(survey_version: str = ""):
    """Export submissions as CSV (answers JSON-encoded in one column)."""
    csv_text = storage.export_responses_csv(survey_version=survey_version)
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=survey_responses.csv"})


@app.post("/api/v1/admin/prune", dependencies=[Depends(require_admin)])
async def admin_prune(max_days: int = 365):
    """Prune submissions older than max_days."""
    deleted = storage.prune_old_responses(max_days=max_days)
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# 定期维护
# ---------------------------------------------------------------------------

async def _periodic_rate_limiter_cleanup():
    """Hourly cleanup of rate-limit records for inactive devices."""
    while True:
        await asyncio.sleep(3600)
        try:
            rate_limiter.cleanup_stale()
        except Exception:
            # 后台清理是纯优化（防内存缓慢膨胀），失败无碍正确性；下个 tick 再试，
            # 不能让一次异常杀掉清理循环。
            pass


@app.on_event("startup")
async def on_startup():
    rate_limiter.cleanup_stale()
    asyncio.create_task(_periodic_rate_limiter_cleanup())
    logger.info(f"Survey server started. DB={DB_PATH}")
    if not ADMIN_TOKEN:
        logger.warning("⚠ SURVEY_ADMIN_TOKEN not set — admin API disabled")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="N.E.K.O Survey Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--db", default=None)
    parser.add_argument("--admin-token", default=None, help="Admin API token")
    args = parser.parse_args()

    if args.db:
        DB_PATH = args.db
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        storage = SurveyStorage(DB_PATH)
    if args.admin_token:
        ADMIN_TOKEN = args.admin_token

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
