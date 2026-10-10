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

"""``/api/public-knowledge/*``: thin proxy to the Memory Server's knowledge API.

Main is the only place that authenticates browser-facing knowledge requests:
writes need a valid local CSRF token (``X-CSRF-Token`` header) and an allowed
Origin, checked *before* any body is read; bodies are size-capped and streamed
through to ``/internal/knowledge/*`` without being buffered or parsed here, so
a multipart upload passes the same way as JSON. Only an explicit allowlist of
paths is forwarded. Main does not import the ``knowledge`` package.

Every reply that carries ``tool_available`` refreshes Main's cached flag that
decides whether the ``query_public_knowledge`` tool is offered.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main_logic import public_knowledge
from main_routers.system_router._shared import _validate_local_mutation_request
from utils.http.knowledge_proxy import (
    READ_PATHS,
    WRITE_PATHS,
    BodyTooLarge,
    capped_body,
    declared_size_problem,
    is_body_too_large,
)
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Main")
router = APIRouter(prefix="/api/public-knowledge", tags=["public-knowledge"])

_READ_TIMEOUT_SECONDS = 10.0
_WRITE_TIMEOUT_SECONDS = 30.0


def _target(path: str) -> str:
    from config import MEMORY_SERVER_PORT

    return f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/knowledge/{path}"


def _failure(reason: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason}, status_code=status_code)


def _relay(response: httpx.Response) -> JSONResponse:
    try:
        payload = response.json()
    except ValueError:
        return _failure("knowledge_invalid_response", 502)
    if not isinstance(payload, dict):
        return _failure("knowledge_invalid_response", 502)
    public_knowledge.note_availability(payload)
    return JSONResponse(payload, status_code=response.status_code)


def _transport_failure(exc: Exception, path: str) -> JSONResponse:
    if isinstance(exc, httpx.TimeoutException):
        logger.warning("[public-knowledge] proxy timeout path=%s", path)
        return _failure("knowledge_timeout", 504)
    logger.warning("[public-knowledge] proxy failed path=%s: %s", path, type(exc).__name__)
    return _failure("knowledge_unavailable", 503)


@router.get("/{path:path}")
async def read_public_knowledge(path: str, request: Request):
    path = path.strip("/")
    if path not in READ_PATHS:
        return _failure("not_found", 404)
    from utils.internal_http_client import get_internal_http_client

    try:
        response = await get_internal_http_client().get(
            _target(path),
            params=list(request.query_params.multi_items()),
            timeout=_READ_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return _transport_failure(exc, path)
    return _relay(response)


@router.post("/{path:path}")
async def write_public_knowledge(path: str, request: Request):
    path = path.strip("/")
    max_bytes = WRITE_PATHS.get(path)
    if max_bytes is None:
        return _failure("not_found", 404)
    rejected = _validate_local_mutation_request(
        request, error_defaults={"ok": False, "reason": "csrf_validation_failed"}
    )
    if rejected is not None:
        return rejected
    declared = request.headers.get("content-length")
    problem = declared_size_problem(declared, max_bytes)
    if problem is not None:
        return _failure(*problem)
    headers = {"content-type": request.headers.get("content-type", "application/json")}
    if declared is not None:
        headers["content-length"] = declared
    from utils.internal_http_client import get_internal_http_client

    try:
        response = await get_internal_http_client().post(
            _target(path),
            content=capped_body(request, max_bytes),
            headers=headers,
            timeout=_WRITE_TIMEOUT_SECONDS,
        )
    except BodyTooLarge:
        return _failure("payload_too_large", 413)
    except httpx.HTTPError as exc:
        if is_body_too_large(exc):
            return _failure("payload_too_large", 413)
        return _transport_failure(exc, path)
    return _relay(response)
