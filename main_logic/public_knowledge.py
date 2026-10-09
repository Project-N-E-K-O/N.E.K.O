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

"""Main-side client of the public-knowledge subsystem.

Public knowledge lives in the Memory Server (``/internal/knowledge/*``); Main
never imports the ``knowledge`` package. This module holds the two things Main
needs:

* the ``query_public_knowledge`` tool: an HTTP call with a timeout, written
  like ``recall_memory`` -> ``/query_memory/{name}``. Any failure reads as "no
  result", so a broken knowledge base never stalls a reply;
* a cached "is there anything to query" flag. The tool is registered only
  while it is true, so users without packs do not pay for its schema on every
  turn. The flag is refreshed in the background when stale, and pushed
  whenever Main's management proxy sees a fresh value; on a change, every
  session manager re-registers its builtin tools and syncs them.
"""

from __future__ import annotations

import asyncio
import functools
import time
import weakref
from typing import Any, Callable

from config.prompts.prompts_knowledge import (
    PUBLIC_KNOWLEDGE_MATERIAL_TYPE_DESCRIPTION,
    PUBLIC_KNOWLEDGE_MODE_DESCRIPTION,
    PUBLIC_KNOWLEDGE_NO_RESULT,
    PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION,
    PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION,
)
from config.prompts.prompts_sys import _loc, normalize_sys_prompt_locale
from main_logic.tool_calling import ToolDefinition
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Main")

TOOL_NAME = "query_public_knowledge"
QUERY_TIMEOUT_SECONDS = 3.0
QUERY_BUDGET_MS = 2_000
MAX_QUERY_CHARS = 500
MAX_TOOL_LIMIT = 3
AVAILABILITY_TIMEOUT_SECONDS = 1.0
AVAILABILITY_TTL_SECONDS = 60.0
AVAILABILITY_RETRY_SECONDS = 15.0

_tool_available = False
_next_check_at = 0.0
_refresh_task: asyncio.Task[None] | None = None
_refresh_timer: asyncio.TimerHandle | None = None
_listeners: "weakref.WeakSet[Any]" = weakref.WeakSet()


def tool_available() -> bool:
    """Whether ``query_public_knowledge`` should be offered to the model."""
    return _tool_available


def add_availability_listener(owner: Any) -> None:
    """Track a session manager; it must provide ``_on_public_knowledge_availability``."""
    _listeners.add(owner)


def note_availability(payload: object) -> None:
    """Take the ``tool_available`` flag from any Memory Server knowledge reply."""
    global _tool_available, _next_check_at
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_available"), bool):
        return
    value = payload["tool_available"]
    _next_check_at = time.monotonic() + AVAILABILITY_TTL_SECONDS
    if value == _tool_available:
        return
    _tool_available = value
    logger.info("[public-knowledge] tool availability -> %s", value)
    for owner in list(_listeners):
        try:
            owner._on_public_knowledge_availability()
        except Exception as exc:
            logger.warning(
                "[public-knowledge] availability listener failed: %s", type(exc).__name__
            )


def schedule_availability_refresh(memory_server_port: int, *, force: bool = False) -> None:
    """Refresh the flag in the background when it is stale; never blocks."""
    global _refresh_task
    if not force and time.monotonic() < _next_check_at:
        return
    if _refresh_task is not None and not _refresh_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refresh_task = loop.create_task(
        _refresh_availability(memory_server_port), name="public-knowledge-availability"
    )


async def _refresh_availability(memory_server_port: int) -> None:
    global _next_check_at
    delay = AVAILABILITY_RETRY_SECONDS
    try:
        from utils.internal_http_client import get_internal_http_client

        response = await get_internal_http_client().get(
            f"http://127.0.0.1:{memory_server_port}/internal/knowledge/availability",
            timeout=AVAILABILITY_TIMEOUT_SECONDS,
        )
        if response.is_success:
            payload = response.json()
            note_availability(payload)
            if isinstance(payload, dict) and payload.get("ready") is True:
                delay = AVAILABILITY_TTL_SECONDS
    except Exception as exc:
        logger.debug("[public-knowledge] availability check failed: %s", type(exc).__name__)
    # Keep the flag fresh on our own: an import finishes in the background and
    # may be started by a page that closes before seeing it complete, and a
    # session that is already up does not re-register its tools. Retry soon
    # while the runtime is starting or unreachable, otherwise once per TTL.
    _next_check_at = time.monotonic() + delay
    _arm_refresh_timer(memory_server_port, delay)


def _arm_refresh_timer(memory_server_port: int, delay: float) -> None:
    global _refresh_timer
    if _refresh_timer is not None:
        _refresh_timer.cancel()
    _refresh_timer = asyncio.get_running_loop().call_later(
        delay, functools.partial(schedule_availability_refresh, memory_server_port, force=True)
    )


def reset_for_tests() -> None:
    global _tool_available, _next_check_at, _refresh_task, _refresh_timer
    _tool_available = False
    _next_check_at = 0.0
    _refresh_task = None
    if _refresh_timer is not None:
        _refresh_timer.cancel()
    _refresh_timer = None
    _listeners.clear()


def _clean_arguments(arguments: object) -> dict[str, Any] | None:
    args = arguments if isinstance(arguments, dict) else {}
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return None
    mode = args.get("mode") if args.get("mode") in ("lookup", "sample") else "lookup"
    material_type = args.get("material_type")
    if material_type not in ("auto", "knowledge", "corpus"):
        material_type = "auto"
    limit = args.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = MAX_TOOL_LIMIT
    return {
        "query": query.strip()[:MAX_QUERY_CHARS],
        "mode": mode,
        "material_type": material_type,
        "limit": min(max(limit, 1), MAX_TOOL_LIMIT),
    }


async def query_public_knowledge(
    arguments: object, *, memory_server_port: int, language: str | None
) -> str:
    """Run one tool call against the Memory Server; failures read as no result.

    Logging keeps to metadata at INFO (mode, result, hit count, elapsed);
    query text stays out of the persisted log.
    """
    lang = normalize_sys_prompt_locale(language)
    no_result = _loc(PUBLIC_KNOWLEDGE_NO_RESULT, lang)
    body = _clean_arguments(arguments)
    if body is None:
        return no_result
    started = time.monotonic()
    payload: dict[str, Any] = {}
    try:
        from utils.internal_http_client import get_internal_http_client

        response = await get_internal_http_client().post(
            f"http://127.0.0.1:{memory_server_port}/internal/knowledge/query",
            json={**body, "budget_ms": QUERY_BUDGET_MS, "language": language},
            timeout=QUERY_TIMEOUT_SECONDS,
        )
        if response.is_success:
            decoded = response.json()
            payload = decoded if isinstance(decoded, dict) else {}
            note_availability(payload)
        else:
            logger.warning("[public-knowledge] query returned status=%s", response.status_code)
    except Exception as exc:
        logger.warning("[public-knowledge] query failed (%s); no result", type(exc).__name__)
    result = str(payload.get("result") or "error")
    context = payload.get("context")
    hits = payload.get("hits") if isinstance(payload.get("hits"), list) else []
    logger.info(
        "[public-knowledge] tool mode=%s result=%s hits=%d elapsed=%dms",
        body["mode"], result, len(hits), int((time.monotonic() - started) * 1000),
    )
    if result == "matched" and isinstance(context, str) and context.strip():
        return context
    return no_result


def build_tool_definition(
    language: str | None, handler: Callable[[dict], Any]
) -> ToolDefinition:
    lang = normalize_sys_prompt_locale(language)
    return ToolDefinition(
        name=TOOL_NAME,
        description=_loc(PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION, lang),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": _loc(PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION, lang),
                },
                "mode": {
                    "type": "string",
                    "enum": ["lookup", "sample"],
                    "description": _loc(PUBLIC_KNOWLEDGE_MODE_DESCRIPTION, lang),
                },
                "material_type": {
                    "type": "string",
                    "enum": ["auto", "knowledge", "corpus"],
                    "description": _loc(PUBLIC_KNOWLEDGE_MATERIAL_TYPE_DESCRIPTION, lang),
                },
            },
            "required": ["query"],
        },
        handler=handler,
        metadata={"source": "builtin", "domain": "public_knowledge"},
    )
