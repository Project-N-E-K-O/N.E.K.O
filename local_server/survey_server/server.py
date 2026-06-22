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
import io
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from models import SurveySubmission, SubmitResponse, model_to_dict, model_from_json
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
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid admin token")


# ---------------------------------------------------------------------------
# 客户端上报（公开，HMAC 验证）
# ---------------------------------------------------------------------------

@app.post("/api/v1/survey", response_model=SubmitResponse)
async def submit_survey(request: Request):
    """Receive a survey response. Validation: body size → decompress → timestamp → HMAC → rate limit → store."""
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

    device_id = submission.payload.device_id
    if not rate_limiter.is_allowed(device_id):
        raise HTTPException(429, "Rate limit exceeded")

    batch_id = submission.batch_id
    if storage.is_duplicate_batch(batch_id):
        return SubmitResponse(ok=True, message="duplicate, skipped")

    action = submission.payload.action if submission.payload.action in ("submit", "skip") else "submit"

    # steam_user_id 边界白名单化：客户端串不可信，非法/伪造一律归 ''（纯十进制
    # Steam64，<= 20 位）。与 telemetry 同口径，守零 PII 之外的低基数契约。
    raw_sid = submission.payload.steam_user_id or ""
    steam_user_id = raw_sid if (raw_sid.isascii() and raw_sid.isdigit() and 0 < len(raw_sid) <= 20) else ""

    try:
        storage.store_response(
            device_id=device_id,
            app_version=submission.payload.app_version,
            survey_version=submission.payload.survey_version,
            locale=submission.payload.locale,
            branch=submission.payload.branch,
            distribution=submission.payload.distribution,
            steam_user_id=steam_user_id,
            action=action,
            answers=submission.payload.answers or {},
            batch_id=batch_id,
        )
    except Exception as e:
        logger.error(f"Store failed for {device_id[:8]}...: {e}")
        raise HTTPException(500, "Storage error")

    logger.info(
        f"OK device={device_id[:8]}... survey={submission.payload.survey_version} action={action}"
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
