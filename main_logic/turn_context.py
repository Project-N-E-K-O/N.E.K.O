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

"""Main-side providers of passive turn context, and the blocks they make.

When a text turn arrives, the session manager starts every provider at once
(``start_turn_context``) and collects them right before the reply is sent
(``collect_turn_context``), under one shared budget counted from the start
(``TURN_CONTEXT_BUDGET_SECONDS``). A provider that is not back by then is
dropped; the reply never waits past the budget.

Providers live in the Memory Server and are reached over HTTP:

* knowledge and corpus - ``/internal/knowledge/turn_context`` (only packs
  whose own switch is on, under the global knowledge switch);
* memories - ``/internal/memory/{name}/turn_context`` (the conversation
  setting ``autoMemoryRecallEnabled``, on by default).

What comes back becomes at most two user-role messages for the offline
client (``omni_offline_client.TurnContext``): a resident knowledge block and
an ephemeral turn block. Neither is ever saved anywhere.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from config.prompts.prompts_sys import _loc, normalize_sys_prompt_locale
from config.prompts.prompts_turn_context import (
    TURN_CONTEXT_CORPUS_BEGIN,
    TURN_CONTEXT_CORPUS_END,
    TURN_CONTEXT_CORPUS_NOTE,
    TURN_CONTEXT_KNOWLEDGE_BEGIN,
    TURN_CONTEXT_KNOWLEDGE_END,
    TURN_CONTEXT_KNOWLEDGE_NOTE,
    TURN_CONTEXT_MEMORY_BEGIN,
    TURN_CONTEXT_MEMORY_END,
    TURN_CONTEXT_MEMORY_NOTE,
    TURN_CONTEXT_TURN_BEGIN,
    TURN_CONTEXT_TURN_CLOSING,
    TURN_CONTEXT_TURN_END,
    TURN_CONTEXT_TURN_NOTE,
)
from config.turn_context_settings import (
    TURN_CONTEXT_BUDGET_SECONDS,
    TURN_CONTEXT_MIN_REMAINING_SECONDS,
    TURN_CONTEXT_QUERY_MAX_CHARS,
)
from main_logic.omni_offline_client import TurnContext
from utils.llm_client import HumanMessage
from utils.logger_config import get_module_logger
from utils.prompt_fence import fence_safe
from utils.tokenize import count_tokens

logger = get_module_logger(__name__, "Main")

MEMORY_SETTING_KEY = "autoMemoryRecallEnabled"
# Slack on top of the remaining budget for the HTTP timeout itself; the
# collector stops waiting at the budget anyway and cancels the call.
_HTTP_SLACK_SECONDS = 0.25
_MIN_PROVIDER_BUDGET_MS = 50


@dataclass
class TurnContextJob:
    """Provider calls of one text turn, all under one deadline."""

    started: float
    deadline: float
    language: str
    tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    # The session the lookup was made for: it excluded that session's
    # resident cards, so its results fit no other session.
    session: Any = None

    def cancel(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()


def _remaining_ms(deadline: float) -> int:
    return max(int((deadline - time.monotonic()) * 1000), 0)


async def _post_json(url: str, payload: dict, *, deadline: float) -> dict | None:
    from utils.internal_http_client import get_internal_http_client

    timeout = max(deadline - time.monotonic(), 0.0) + _HTTP_SLACK_SECONDS
    response = await get_internal_http_client().post(url, json=payload, timeout=timeout)
    if not response.is_success:
        logger.info("[turn-context] %s returned status=%s", url.rsplit("/", 1)[-1], response.status_code)
        return None
    decoded = response.json()
    return decoded if isinstance(decoded, dict) else None


async def _fetch_knowledge(
    port: int, text: str, exclude_card_ids: list[str], language: str, deadline: float
) -> dict | None:
    payload = await _post_json(
        f"http://127.0.0.1:{port}/internal/knowledge/turn_context",
        {
            "text": text,
            "exclude_card_ids": exclude_card_ids,
            "budget_ms": max(_remaining_ms(deadline), _MIN_PROVIDER_BUDGET_MS),
            "language": language,
        },
        deadline=deadline,
    )
    if payload is not None:
        from main_logic import public_knowledge

        public_knowledge.note_availability(payload)
    return payload


async def _fetch_memory(
    port: int,
    lanlan_name: str,
    text: str,
    language: str,
    session_started_at: float | None,
    deadline: float,
    settings_loader,
) -> dict | None:
    try:
        settings = await settings_loader()
    except Exception:  # noqa: BLE001 - a broken settings read keeps the default
        settings = {}
    if not bool((settings or {}).get(MEMORY_SETTING_KEY, True)):
        return None
    return await _post_json(
        f"http://127.0.0.1:{port}/internal/memory/{lanlan_name}/turn_context",
        {
            "text": text,
            "budget_ms": max(_remaining_ms(deadline), _MIN_PROVIDER_BUDGET_MS),
            "language": language,
            "session_started_at": session_started_at,
        },
        deadline=deadline,
    )


def start_turn_context(
    *,
    port: int,
    lanlan_name: str,
    text: str,
    language: Optional[str],
    exclude_card_ids: Iterable[str] = (),
    knowledge_available: bool,
    session_started_at: float | None,
    settings_loader,
    started: float | None = None,
) -> TurnContextJob | None:
    """Start every provider for one text turn; None when there is no text.

    The budget counts from ``started`` (a monotonic time, default now: when
    the message reached Main), so time spent starting or handing over the
    session comes out of it; the providers still get at least
    ``TURN_CONTEXT_MIN_REMAINING_SECONDS``.
    """
    query = str(text or "").strip()[:TURN_CONTEXT_QUERY_MAX_CHARS]
    if not query:
        return None
    now = time.monotonic()
    started = now if started is None else min(started, now)
    deadline = max(started + TURN_CONTEXT_BUDGET_SECONDS, now + TURN_CONTEXT_MIN_REMAINING_SECONDS)
    job = TurnContextJob(
        started=started,
        deadline=deadline,
        language=normalize_sys_prompt_locale(language),
    )
    if knowledge_available:
        job.tasks["knowledge"] = asyncio.create_task(
            _fetch_knowledge(port, query, list(exclude_card_ids), language or job.language, job.deadline)
        )
    job.tasks["memory"] = asyncio.create_task(
        _fetch_memory(
            port, lanlan_name, query, language or job.language,
            session_started_at, job.deadline, settings_loader,
        )
    )
    for task in job.tasks.values():
        task.add_done_callback(_consume_result)
    return job


def _consume_result(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def collect_turn_context(job: TurnContextJob | None) -> dict[str, dict | None]:
    """What each provider returned by the deadline (None for the rest)."""
    if job is None or not job.tasks:
        return {}
    remaining = max(job.deadline - time.monotonic(), 0.0)
    done, pending = await asyncio.wait(set(job.tasks.values()), timeout=remaining)
    results: dict[str, dict | None] = {}
    for name, task in job.tasks.items():
        if task in done and not task.cancelled() and task.exception() is None:
            results[name] = task.result()
        else:
            results[name] = None
            if task in pending:
                task.cancel()
                logger.info("[turn-context] %s provider missed the %.2fs budget", name, TURN_CONTEXT_BUDGET_SECONDS)
            elif not task.cancelled() and task.exception() is not None:
                logger.info(
                    "[turn-context] %s provider failed: %s", name, type(task.exception()).__name__
                )
    return results


def _section(payload: dict | None, key: str) -> tuple[str, list[str]]:
    section = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(section, dict):
        return "", []
    context = section.get("context")
    ids = section.get("card_ids")
    return (
        context.strip() if isinstance(context, str) else "",
        [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
    )


def build_turn_context(results: dict[str, dict | None], language: str) -> TurnContext | None:
    """The blocks for this reply, or None when nothing came back."""
    lang = normalize_sys_prompt_locale(language)
    knowledge = results.get("knowledge")
    memory = results.get("memory")
    knowledge_text, knowledge_ids = _section(knowledge, "knowledge")
    corpus_text, _corpus_ids = _section(knowledge, "corpus")
    memory_text = ""
    if isinstance(memory, dict) and memory.get("result") == "matched":
        raw = memory.get("context")
        memory_text = raw.strip() if isinstance(raw, str) else ""

    resident = None
    resident_tokens = 0
    if knowledge_text and knowledge_ids:
        text = "\n".join((
            _loc(TURN_CONTEXT_KNOWLEDGE_BEGIN, lang),
            _loc(TURN_CONTEXT_KNOWLEDGE_NOTE, lang),
            fence_safe(knowledge_text),
            _loc(TURN_CONTEXT_KNOWLEDGE_END, lang),
        ))
        resident = HumanMessage(content=text, metadata={"turn_context": "resident"})
        resident_tokens = count_tokens(text)

    ephemeral = None
    if corpus_text or memory_text:
        lines = [_loc(TURN_CONTEXT_TURN_BEGIN, lang), _loc(TURN_CONTEXT_TURN_NOTE, lang)]
        if corpus_text:
            lines += [
                _loc(TURN_CONTEXT_CORPUS_BEGIN, lang),
                _loc(TURN_CONTEXT_CORPUS_NOTE, lang),
                fence_safe(corpus_text),
                _loc(TURN_CONTEXT_CORPUS_END, lang),
            ]
        if memory_text:
            lines += [
                _loc(TURN_CONTEXT_MEMORY_BEGIN, lang),
                _loc(TURN_CONTEXT_MEMORY_NOTE, lang),
                fence_safe(memory_text),
                _loc(TURN_CONTEXT_MEMORY_END, lang),
            ]
        lines += [_loc(TURN_CONTEXT_TURN_END, lang), _loc(TURN_CONTEXT_TURN_CLOSING, lang)]
        ephemeral = HumanMessage(content="\n".join(lines), metadata={"turn_context": "ephemeral"})

    if resident is None and ephemeral is None:
        return None
    return TurnContext(
        resident=resident,
        resident_ids=tuple(knowledge_ids) if resident is not None else (),
        resident_tokens=resident_tokens,
        ephemeral=ephemeral,
    )


def describe(results: dict[str, dict | None], context: TurnContext | None) -> str:
    """One metadata line for the log (no content)."""
    parts = []
    for name in ("knowledge", "memory"):
        payload = results.get(name)
        if name not in results:
            continue
        parts.append(f"{name}={payload.get('result') if isinstance(payload, dict) else 'none'}")
    if context is not None:
        parts.append(f"resident_ids={len(context.resident_ids)}")
        parts.append(f"ephemeral={'yes' if context.ephemeral is not None else 'no'}")
    return " ".join(parts)
