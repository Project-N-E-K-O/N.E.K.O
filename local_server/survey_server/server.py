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
# Configuration.
# ---------------------------------------------------------------------------

HMAC_SECRET = os.getenv("SURVEY_HMAC_SECRET", DEFAULT_HMAC_SECRET)
DB_PATH = os.getenv("SURVEY_DB_PATH", "./data/survey.db")
ADMIN_TOKEN = os.getenv("SURVEY_ADMIN_TOKEN", "")
MAX_BODY_SIZE = 256 * 1024            # Request body byte limit (usually <=10 KB after gzip).
MAX_DECOMPRESSED_SIZE = 1024 * 1024   # Decompression limit to block zip bombs.

# ---------------------------------------------------------------------------
# Initialization.
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "NEKO_SURVEY_CORS_ORIGINS", "http://localhost,http://127.0.0.1"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


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
    # Constant-time comparison: the admin endpoint exposes device_id / steam_user_id; ordinary != leaks a byte-by-byte comparison
    # progress through a timing side channel. compare_digest removes that difference. First reject non-ASCII: compare_digest
    # raises TypeError for strings containing non-ASCII characters; otherwise a single ?token=é would turn a 401 into a 500.
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
            # Reject non-finite floats: JSON allows NaN/Infinity, but after storing them, FastAPI rendering
            # /api/v1/admin/responses will return a 500 for non-finite values; one poisoned answer could
            # take down the entire page (repository convention: reject NaN/Inf with math.isfinite before storing).
            if math.isfinite(v):
                out[key] = v
        elif isinstance(v, list):
            out[key] = [str(x)[:200] for x in v[:50] if isinstance(x, (str, int, float, bool))]
    return out


# ---------------------------------------------------------------------------
# Client submissions (public, HMAC-verified).
# ---------------------------------------------------------------------------

@app.post("/api/v1/survey", response_model=SubmitResponse)
async def submit_survey(request: Request):
    """Receive a survey response. Validation: body size → decompress → timestamp → HMAC → rate limit → store."""
    # Content-Length preflight: well-behaved clients send this header, so reject oversized requests with 413 immediately instead of first buffering the entire body
    # into memory and then rejecting it. Requests without CL / using chunked transfer fall through to the actual read check below (uvicorn also provides
    # a frame-size limit as a safeguard).
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                raise HTTPException(413, "Payload too large")
        except ValueError:
            # The CL header is not a valid integer: skip this preflight and rely on the actual read-length check below as a safeguard.
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

    # HMAC — verify the signature using the same canonical JSON as the client (sort_keys=True).
    try:
        body_dict = json.loads(body_bytes)
        payload_json = json.dumps(body_dict["payload"], ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(400, "Malformed payload")
    if not verify_signature(payload_json, submission.timestamp, submission.signature, HMAC_SECRET):
        raise HTTPException(403, "Invalid signature")

    # Metadata is stored raw, with no Pydantic length limit: a forged request made with the open-source key could make fields such as survey_version
    # (which is also indexed) approach the 1 MB decompression limit. Apply a reasonable length cap at the entry point to prevent a small number of
    # forged submissions from bloating SQLite / admin responses. device_id is capped as well (it is included in the rate-limit key and idempotency key).
    device_id = (submission.payload.device_id or "")[:128]
    app_version = (submission.payload.app_version or "")[:32]
    survey_version = (submission.payload.survey_version or "")[:32]
    locale = (submission.payload.locale or "")[:35]
    branch = (submission.payload.branch or "")[:64]
    distribution = (submission.payload.distribution or "")[:32]

    if not rate_limiter.is_allowed(device_id):
        raise HTTPException(429, "Rate limit exceeded")

    action = submission.payload.action if submission.payload.action in ("submit", "skip") else "submit"

    # Derive the idempotency key from a field in the **signed** payload; do not trust batch_id in the envelope — it is not covered by
    # the HMAC and can be changed by a man-in-the-middle or a client holding the key, turning the same submission into a new row
    # and polluting the submit/skip funnel. The (device_id, survey_version, action) tuple matches the client's
    # idempotency semantics: count each device/version/action only once.
    batch_id = hashlib.sha256(
        f"{device_id}|{survey_version}|{action}".encode("utf-8")
    ).hexdigest()[:32]

    # Normalize steam_user_id to the canonical decimal Steam64 form, using the same rules as telemetry normalize_steam_id:
    # ASCII digits only + <= 20 digits + 0 < int < 2^64 + strip leading zeros with str(int()). Otherwise,
    # a forged request containing '0' / leading zeros / a value above u64 can pollute account-level JOINs between survey and telemetry.
    raw_sid = submission.payload.steam_user_id or ""
    steam_user_id = ""
    if raw_sid.isascii() and raw_sid.isdigit() and len(raw_sid) <= 20:
        _sid_val = int(raw_sid)
        if 0 < _sid_val < (1 << 64):
            steam_user_id = str(_sid_val)

    # Legacy algorithm device ID (used to JOIN the same person across tables during migration). Untrusted string; cap its length.
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

    # !r escapes control characters: even after capping device_id/survey_version length, they could contain forged backslash-n and similar characters;
    # raw logging would let a single submission inject extra log lines into journald (log forging).
    logger.info(
        f"OK device={device_id[:8]!r} survey={survey_version!r} action={action}"
    )
    return SubmitResponse()


# ---------------------------------------------------------------------------
# Health check (public).
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "neko-survey"}


# ---------------------------------------------------------------------------
# Admin API (requires admin token).
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
# Periodic maintenance.
# ---------------------------------------------------------------------------

async def _periodic_rate_limiter_cleanup():
    """Hourly cleanup of rate-limit records for inactive devices."""
    while True:
        await asyncio.sleep(3600)
        try:
            rate_limiter.cleanup_stale()
        except Exception:
            # Background cleanup is a pure optimization (to prevent gradual memory growth); failure does not affect correctness. Try again on the next tick,
            # and do not let one exception kill the cleanup loop.
            pass


@app.on_event("startup")
async def on_startup():
    rate_limiter.cleanup_stale()
    asyncio.create_task(_periodic_rate_limiter_cleanup())
    logger.info(f"Survey server started. DB={DB_PATH}")
    if not ADMIN_TOKEN:
        logger.warning("⚠ SURVEY_ADMIN_TOKEN not set — admin API disabled")


# ---------------------------------------------------------------------------
# Entrypoint.
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
