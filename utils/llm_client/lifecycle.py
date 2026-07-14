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
"""Shared character context, SSL context, and client lifecycle helpers."""

from __future__ import annotations
import asyncio
import contextvars
import os
import ssl
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

_active_character: "contextvars.ContextVar[tuple[str, str] | None]" = contextvars.ContextVar(
    "_neko_active_character_master_lanlan", default=None
)

_DEFAULT_SSL_CONTEXT: ssl.SSLContext | None = None

_DEFAULT_SSL_CONTEXT_LOCK = threading.Lock()

_PENDING_CLIENT_CLOSE_TASKS: set[asyncio.Task[None]] = set()

def _create_httpx_default_ssl_context() -> ssl.SSLContext:
    """Create the same default verify context httpx uses without its deprecated helper."""
    import certifi

    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    return ssl.create_default_context(cafile=certifi.where())

def _get_default_ssl_context() -> ssl.SSLContext:
    """Return the process-wide default TLS context for short-lived LLM clients."""
    global _DEFAULT_SSL_CONTEXT
    if _DEFAULT_SSL_CONTEXT is not None:
        return _DEFAULT_SSL_CONTEXT

    with _DEFAULT_SSL_CONTEXT_LOCK:
        if _DEFAULT_SSL_CONTEXT is None:
            _DEFAULT_SSL_CONTEXT = _create_httpx_default_ssl_context()
        return _DEFAULT_SSL_CONTEXT

async def _close_async_openai_client_best_effort(aclient: AsyncOpenAI) -> None:
    try:
        await aclient.close()
    except Exception:
        # Finalizer-triggered cleanup must never surface async close failures.
        pass

def _schedule_async_openai_client_close_best_effort(
    aclient: AsyncOpenAI,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    close_coro = _close_async_openai_client_best_effort(aclient)
    try:
        task = loop.create_task(close_coro)
    except Exception:
        close_coro.close()
        # The loop may be closing; explicit aclose() remains the deterministic path.
    else:
        _PENDING_CLIENT_CLOSE_TASKS.add(task)
        task.add_done_callback(_PENDING_CLIENT_CLOSE_TASKS.discard)

def _close_async_openai_client_from_sync_best_effort(aclient: AsyncOpenAI) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(_close_async_openai_client_best_effort(aclient))
        except Exception:
            # Synchronous shutdown cleanup must never propagate close failures.
            pass
        return
    _schedule_async_openai_client_close_best_effort(aclient, loop=loop)

def _close_chat_openai_clients_best_effort(client: OpenAI, aclient: AsyncOpenAI) -> None:
    try:
        client.close()
    except Exception:
        # Destructors/finalizers must never raise during GC or interpreter shutdown.
        pass
    _schedule_async_openai_client_close_best_effort(aclient)

def _close_chat_clients_best_effort(client: Any, aclient: Any) -> None:
    try:
        client.close()
    except Exception:
        # Generic provider cleanup is intentionally best-effort during shutdown.
        pass
    _schedule_async_openai_client_close_best_effort(aclient)

def set_active_character(master_name: str, lanlan_name: str) -> "contextvars.Token":
    """Set ``(master_name, lanlan_name)`` on the active async context so
    subsequent ``ChatOpenAI._params`` invocations on this task substitute
    ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders in messages before
    the leak check + wire send. Returns a token; pass to
    ``reset_active_character`` to restore the previous value.

    Empty strings are tolerated (skipped at substitution time) so callers
    that only know one of the two can still set partial context.
    """
    return _active_character.set((master_name or "", lanlan_name or ""))

def reset_active_character(token: "contextvars.Token") -> None:
    _active_character.reset(token)

def _substitute_character_placeholders(messages: list, master: str, lanlan: str) -> list:
    """Return a NEW messages list with ``{MASTER_NAME}`` / ``{LANLAN_NAME}``
    replaced in every text-bearing field. Defensive copy — does not
    mutate the input. ``str.replace`` (not ``.format``) so JSON fragments
    or other braces in user content don't trigger KeyError.
    """
    if not master and not lanlan:
        return messages

    def _swap(text: str) -> str:
        if master:
            text = text.replace("{MASTER_NAME}", master)
        if lanlan:
            text = text.replace("{LANLAN_NAME}", lanlan)
        return text

    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        content = m.get("content")
        if isinstance(content, str):
            new_content: Any = _swap(content)
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    new_parts.append({**part, "text": _swap(part["text"])})
                else:
                    new_parts.append(part)
            new_content = new_parts
        else:
            new_content = content
        out.append({**m, "content": new_content})
    return out
