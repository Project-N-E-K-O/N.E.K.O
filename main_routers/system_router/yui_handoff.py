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

"""Yui guide handoff token endpoints (/yui-guide/handoff/*).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import (
    _get_request_origin,
    _json_no_store_response,
    _normalize_origin_value,
    _read_json_object,
    _set_no_store_headers,
    _validate_local_mutation_request,
    router,
)
import asyncio
import hashlib
import hmac
import secrets
import time
from typing import Any
from fastapi import Request


_YUI_GUIDE_HANDOFF_TOKEN_VERSION = 1


_YUI_GUIDE_HANDOFF_FLOW_ID = "home_yui_guide_v1"


_YUI_GUIDE_HANDOFF_TTL_SECONDS = 5 * 60


_YUI_GUIDE_HANDOFF_MAX_RECORDS = 128


_YUI_GUIDE_HANDOFF_SECRET = secrets.token_bytes(32)


_yui_guide_handoff_lock = asyncio.Lock()


_yui_guide_handoff_tokens: dict[str, dict[str, Any]] = {}


def _normalize_yui_handoff_text(value: object, *, max_length: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def _build_yui_handoff_signature(record: dict[str, Any]) -> str:
    signed_fields = (
        str(record.get("token") or ""),
        str(record.get("token_version") or ""),
        str(record.get("flow_id") or ""),
        str(record.get("source_origin") or ""),
        str(record.get("source_page") or ""),
        str(record.get("source_path") or ""),
        str(record.get("target_page") or ""),
        str(record.get("target_path") or ""),
        str(record.get("resume_scene") or ""),
        str(record.get("expires_at") or ""),
    )
    message = "\n".join(signed_fields).encode("utf-8")
    return hmac.new(_YUI_GUIDE_HANDOFF_SECRET, message, hashlib.sha256).hexdigest()


def _public_yui_handoff_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": record.get("token", ""),
        "token_version": record.get("token_version", _YUI_GUIDE_HANDOFF_TOKEN_VERSION),
        "flow_id": record.get("flow_id", _YUI_GUIDE_HANDOFF_FLOW_ID),
        "source_page": record.get("source_page", ""),
        "source_path": record.get("source_path", ""),
        "target_page": record.get("target_page", ""),
        "target_path": record.get("target_path", ""),
        "resume_scene": record.get("resume_scene") or None,
        "created_at": record.get("created_at", 0),
        "expires_at": record.get("expires_at", 0),
        "consumed": bool(record.get("consumed_at")),
        "consumed_by": record.get("consumed_by", ""),
        "consumed_at": record.get("consumed_at", 0),
        "signature": record.get("signature", ""),
        "authority": "server",
    }


def _prune_yui_handoff_records(now_ms: int) -> None:
    expired_tokens = [
        token
        for token, record in _yui_guide_handoff_tokens.items()
        if int(record.get("expires_at", 0) or 0) <= now_ms
    ]
    for token in expired_tokens:
        _yui_guide_handoff_tokens.pop(token, None)

    if len(_yui_guide_handoff_tokens) <= _YUI_GUIDE_HANDOFF_MAX_RECORDS:
        return

    ordered_tokens = sorted(
        _yui_guide_handoff_tokens,
        key=lambda token: int(_yui_guide_handoff_tokens[token].get("created_at", 0) or 0),
    )
    overflow = len(_yui_guide_handoff_tokens) - _YUI_GUIDE_HANDOFF_MAX_RECORDS
    for token in ordered_tokens[:overflow]:
        _yui_guide_handoff_tokens.pop(token, None)


@router.post("/yui-guide/handoff/create")
async def create_yui_guide_handoff(request: Request):
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    target_page = _normalize_yui_handoff_text(payload.get("target_page"), max_length=80)
    if not target_page:
        return _json_no_store_response(
            {
                "ok": False,
                "error_code": "invalid_target_page",
                "error": "target_page is required",
            },
            status_code=400,
        )

    now_ms = int(time.time() * 1000)
    request_origin = _get_request_origin(request) or _normalize_origin_value(str(request.base_url))
    record: dict[str, Any] = {
        "token": secrets.token_urlsafe(24),
        "token_version": _YUI_GUIDE_HANDOFF_TOKEN_VERSION,
        "flow_id": _normalize_yui_handoff_text(payload.get("flow_id"), max_length=80) or _YUI_GUIDE_HANDOFF_FLOW_ID,
        "source_origin": request_origin,
        "source_page": _normalize_yui_handoff_text(payload.get("source_page"), max_length=80) or "home",
        "source_path": _normalize_yui_handoff_text(payload.get("source_path"), max_length=240),
        "target_page": target_page,
        "target_path": _normalize_yui_handoff_text(payload.get("target_path"), max_length=240),
        "resume_scene": _normalize_yui_handoff_text(payload.get("resume_scene"), max_length=120) or None,
        "created_at": now_ms,
        "expires_at": now_ms + (_YUI_GUIDE_HANDOFF_TTL_SECONDS * 1000),
        "consumed_at": 0,
        "consumed_by": "",
    }
    record["signature"] = _build_yui_handoff_signature(record)

    async with _yui_guide_handoff_lock:
        _prune_yui_handoff_records(now_ms)
        _yui_guide_handoff_tokens[record["token"]] = record

    return _json_no_store_response({"ok": True, "token": _public_yui_handoff_record(record)})


@router.post("/yui-guide/handoff/consume")
async def consume_yui_guide_handoff(request: Request):
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    token = _normalize_yui_handoff_text(payload.get("token"), max_length=128)
    signature = _normalize_yui_handoff_text(payload.get("signature"), max_length=128)
    expected_page = _normalize_yui_handoff_text(payload.get("expected_page"), max_length=80)
    consumed_by = _normalize_yui_handoff_text(payload.get("consumer_id"), max_length=120)
    request_origin = _get_request_origin(request) or _normalize_origin_value(str(request.base_url))
    now_ms = int(time.time() * 1000)

    if not token or not signature:
        return _json_no_store_response(
            {
                "ok": False,
                "error_code": "invalid_handoff_token",
                "error": "token and signature are required",
            },
            status_code=400,
        )

    if not expected_page:
        return _json_no_store_response(
            {
                "ok": False,
                "error_code": "invalid_expected_page",
                "error": "expected_page is required",
            },
            status_code=400,
        )

    async with _yui_guide_handoff_lock:
        _prune_yui_handoff_records(now_ms)
        record = _yui_guide_handoff_tokens.get(token)
        if not record:
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_token_not_found",
                    "error": "handoff token not found",
                },
                status_code=404,
            )

        stored_signature = str(record.get("signature") or "")
        if not hmac.compare_digest(signature, stored_signature):
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_signature_mismatch",
                    "error": "handoff signature mismatch",
                },
                status_code=403,
            )

        source_origin = str(record.get("source_origin") or "")
        if source_origin and request_origin and request_origin != source_origin:
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_origin_mismatch",
                    "error": "handoff origin mismatch",
                },
                status_code=403,
            )

        target_page = str(record.get("target_page") or "")
        if expected_page != target_page:
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_target_mismatch",
                    "error": "handoff target mismatch",
                },
                status_code=403,
            )

        if record.get("consumed_at"):
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_token_consumed",
                    "error": "handoff token already consumed",
                },
                status_code=409,
            )

        record["consumed_at"] = now_ms
        record["consumed_by"] = consumed_by or request_origin or "unknown"
        return _json_no_store_response({"ok": True, "token": _public_yui_handoff_record(record)})
