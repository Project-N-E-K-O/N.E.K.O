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

"""OpenRouter decision model catalog: a built-in snapshot plus a daily refresh.

OpenRouter serves dozens of decision models through ``/api/alpha/decisions``
and adds new ones often, so a fixed prefix table goes stale. The public model
list filtered by ``output_modalities=decisions`` names them all. The process
keeps the last fetched list for :data:`CATALOG_TTL_SECONDS`; until the first
fetch (or when OpenRouter is unreachable) the snapshot below answers.

An ID counts as a decision model when it is in the snapshot or the fetched
list, or belongs to one of the long-lived families in
:data:`DECISION_MODEL_FAMILIES`. Chat models never qualify: on OpenRouter
``openai/gpt-6-luna`` is a chat model and its decision variant is
``openai/gpt-6-luna-decisions``.
"""

from __future__ import annotations

import asyncio
import time
import urllib.parse
from typing import Any

import httpx

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

OPENROUTER_HOST = "openrouter.ai"
OPENROUTER_DECISION_MODELS_URL = "https://openrouter.ai/api/v1/models?output_modalities=decisions"
CATALOG_TTL_SECONDS = 24 * 60 * 60
# After a failed fetch, wait this long before the next background attempt.
CATALOG_RETRY_SECONDS = 60 * 60
CATALOG_FETCH_TIMEOUT_SECONDS = 10.0

# GET /api/v1/models?output_modalities=decisions on 2026-11-25 (44 models).
SNAPSHOT_DECISION_MODELS = frozenset({
    "baseten/bison-1",
    "baseten/elephant-1",
    "baseten/groundhog-1",
    "baseten/joey-1",
    "baseten/mammoth-1",
    "cloudflare/clairic",
    "cloudflare/clef",
    "cloudflare/clef-flash",
    "cloudflare/clef-omni",
    "google/gemini-decision-1",
    "heygen/heygen-decision-1",
    "heygen/heygen-decision-1-turbo",
    "jaredpalmer/kev-4b",
    "liquid/d1-ultra",
    "liquid/d1.2",
    "liquid/d1.2-ultra",
    "microsoft/microsoft-decision-1",
    "microsoft/microsoft-decision-1.1",
    "morph/reflex",
    "nace-ai/drex-max",
    "nace-ai/drex-max-finance",
    "nace-ai/drex-v1.5",
    "nace-ai/drex-v2.0",
    "openai/gpt-6-luna-decisions",
    "parallel/decisions-fast",
    "perplexity/pplx-decider-flash-v1.2",
    "perplexity/pplx-decider-flash-v1.3:free",
    "perplexity/pplx-decider-v1.2",
    "perplexity/pplx-decider-v1.3:free",
    "respan/span-01",
    "respan/span-01-lite",
    "respan/span-01-lite:free",
    "respan/span-02",
    "scaledown/classify-1",
    "togethercomputer/tev1-4b-experimental",
    "typesafe/jev-1.13",
    "typesafe/jev-1.14",
    "upstage/solar-decide",
    "upstage/solar-decide-flash",
    "~liquid/d1-latest",
    "~liquid/d1-ultra-latest",
    "~perplexity/pplx-decider-flash-latest",
    "~perplexity/pplx-decider-latest",
    "~typesafe/jev-latest",
})

# Families whose every member is a decision model, so new versions match
# before the next refresh. Jev matches by version only: typesafe/jev-router is
# a chat router.
DECISION_MODEL_FAMILIES: tuple[str, ...] = (
    "google/gemini-decision-",
    "typesafe/jev-1.",
    "liquid/d1",
    "cloudflare/clef",
)

_fetched_models: frozenset[str] = frozenset()
_fetched_at: float | None = None
_last_attempt_at: float | None = None
_refresh_in_flight = False
# Strong references to running refresh tasks; the loop keeps only weak ones.
_refresh_tasks: set[asyncio.Task] = set()


def is_openrouter_url(url: Any) -> bool:
    """Return True when *url* points at OpenRouter."""
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        return (urllib.parse.urlsplit(raw).hostname or "").lower() == OPENROUTER_HOST
    except ValueError:
        return False


def is_openrouter_decision_model(model: Any) -> bool:
    """Return True when *model* is an OpenRouter decision model ID."""
    m = str(model or "").strip().lower()
    if not m:
        return False
    if m in SNAPSHOT_DECISION_MODELS or m in _fetched_models:
        return True
    bare = m.lstrip("~")
    return any(bare.startswith(prefix) for prefix in DECISION_MODEL_FAMILIES)


def catalog_is_stale(now: float | None = None) -> bool:
    """Return True when the fetched list is missing or older than the TTL and no retry is pending."""
    current = time.monotonic() if now is None else now
    if _fetched_at is not None and current - _fetched_at < CATALOG_TTL_SECONDS:
        return False
    if _last_attempt_at is not None and current - _last_attempt_at < CATALOG_RETRY_SECONDS:
        return False
    return True


def _parse_models(body: Any) -> list[dict[str, str]]:
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("OpenRouter model list has no 'data' array")
    entries: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        entry = {"id": model_id}
        name = str(item.get("name") or "").strip()
        if name and name != model_id:
            entry["name"] = name
        entries.append(entry)
    return entries


def _remember(entries: list[dict[str, str]], now: float) -> None:
    global _fetched_models, _fetched_at
    _fetched_models = frozenset(entry["id"].lower() for entry in entries)
    _fetched_at = now


async def fetch_openrouter_decision_models(timeout: float = CATALOG_FETCH_TIMEOUT_SECONDS) -> list[dict[str, str]]:
    """Fetch the current OpenRouter decision models and remember them for the TTL.

    The model list is public, so no API key is sent. Raises on network or
    format errors; the cached list is left as it was.
    """
    global _last_attempt_at
    _last_attempt_at = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(OPENROUTER_DECISION_MODELS_URL)
    resp.raise_for_status()
    entries = _parse_models(resp.json())
    if not entries:
        raise ValueError("OpenRouter returned no decision models")
    _remember(entries, time.monotonic())
    return entries


async def _refresh_quietly() -> None:
    global _refresh_in_flight
    try:
        entries = await fetch_openrouter_decision_models()
        logger.debug("[OpenRouterCatalog] refreshed %d decision models", len(entries))
    except Exception as exc:
        # The snapshot keeps answering; the next attempt waits CATALOG_RETRY_SECONDS.
        logger.debug("[OpenRouterCatalog] refresh failed: %s", type(exc).__name__)
    finally:
        _refresh_in_flight = False


def schedule_refresh_if_stale() -> None:
    """Start a background refresh on the running loop when the list is stale.

    Never awaits the network, so a decision call is not slowed down; the
    refreshed list applies from the next call on.
    """
    global _refresh_in_flight
    if _refresh_in_flight or not catalog_is_stale():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _refresh_in_flight = True
    task = loop.create_task(_refresh_quietly())
    _refresh_tasks.add(task)
    task.add_done_callback(_refresh_tasks.discard)


def clear_openrouter_catalog() -> None:
    """Forget the fetched list and the refresh timers (the snapshot stays)."""
    global _fetched_models, _fetched_at, _last_attempt_at, _refresh_in_flight
    _fetched_models = frozenset()
    _fetched_at = None
    _last_attempt_at = None
    _refresh_in_flight = False
