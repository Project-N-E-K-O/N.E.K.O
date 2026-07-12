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

"""Lightweight LLM client layer using the ``openai`` SDK directly.

Provides:
  - Message classes (SystemMessage, HumanMessage, AIMessage) compatible with
    the old langchain interface
  - ChatOpenAI wrapper with streaming, invoke, and resource management
  - ``create_chat_llm()`` factory that auto-resolves provider-specific config
  - ``create_chat_llm_async()`` async factory for offloaded client construction
  - Serialization helpers (messages_to_dict, messages_from_dict, convert_to_messages)
  - OpenAIEmbeddings / SQLChatMessageHistory for memory subsystem
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import json as _json
import os
import re
import ssl
import threading
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator
from urllib.parse import urlparse

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

# openai / anthropic SDK 一律惰性 import（构造点函数内 import + 下方 retry accessor）：
# 两者合计 ~0.7s 且大头是 pydantic 模型类构建（CPU-bound，frozen 下同样存在），而本模块
# 经 app/__init__→runtime_bindings→language_utils 坐在 merged 启动串行 import 链最前端，
# 顶层 import 会把这笔钱记到每次启动的端口就绪路径上（#1496 曾优化过、后被 openai 2.x
# types 变重静默吃回）。首次真实使用由 utils.module_warmup 在 ready 后后台预热兜底。

# 惰性缓存：None = 尚未构建。构建时机是首次 LLM 调用/异常处理，彼时 SDK 必已随
# client 构造加载，函数内 import 只是 sys.modules 字典查找。
_ANTHROPIC_RETRY_EXCEPTION_TYPES: tuple[type[BaseException], ...] | None = None
_OPENAI_RETRY_EXCEPTION_TYPES: tuple[type[BaseException], ...] | None = None

# 测试接缝：单测 monkeypatch 这两个模块属性注入 fake SDK 类（见
# test_llm_client_response_safety）。生产路径保持 None，ChatAnthropic 构造时
# 惰性 import 真 SDK——属性存在但为 None，不承担 import 成本。
Anthropic: Any = None
AsyncAnthropic: Any = None


def openai_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return OpenAI SDK error classes that should follow the chat retry path."""
    global _OPENAI_RETRY_EXCEPTION_TYPES
    if _OPENAI_RETRY_EXCEPTION_TYPES is None:
        from openai import APIConnectionError, InternalServerError, RateLimitError

        _OPENAI_RETRY_EXCEPTION_TYPES = (APIConnectionError, InternalServerError, RateLimitError)
    return _OPENAI_RETRY_EXCEPTION_TYPES


def chat_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return the union of OpenAI + Anthropic transient error classes for shared retry loops."""
    return (*openai_retry_error_types(), *anthropic_retry_error_types())


# ────────────────────────────────────────────────────────────────
# Reasoning-trace stripping (non-streaming defensive cleanup)
# ────────────────────────────────────────────────────────────────
# Well-formed <think>...</think> / <thinking>...</thinking> blocks.
_THINK_PAIRED_RE = re.compile(r"<think(?:ing)?\s*>.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)
# A *dangling* close tag with no matching open. This is the Qwen3.5/3.6
# OpenAI-compat leak shape: unlike qwen3-vl-* (which route reasoning to the
# ``reasoning_content`` field), the 3.5/3.6 hybrid models never populate
# ``reasoning_content`` — the whole chain-of-thought lands in ``content`` with
# only a lone ``</think>`` (implicit open) separating it from the real answer.
# A paired-tag regex alone can't catch this; we strip everything up to and
# including the first unmatched close tag.
_THINK_DANGLING_CLOSE_RE = re.compile(r"^.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)
_THINK_ANY_CLOSE_RE = re.compile(r"</think(?:ing)?\s*>", re.IGNORECASE)


def strip_thinking_segments(text: str | None) -> str:
    """Remove leaked chain-of-thought from a *non-streaming* model reply.

    Handles two shapes:
      1. Well-formed ``<think>...</think>`` blocks (any count).
      2. Qwen3.5/3.6 leak: reasoning dumped into ``content`` with only a
         dangling ``</think>`` (no opening tag) before the answer.

    Conservative — only acts when a think tag is present, so clean replies
    (qwen3-vl-*, gpt, claude, etc.) pass through untouched. Streaming is *not*
    covered here on purpose: when the chain-of-thought arrives token-by-token
    in ``delta.content`` with no delimiter there's nothing reliable to strip.
    """
    if not text:
        return text or ""
    s = str(text)
    # 1) drop well-formed blocks first
    s = _THINK_PAIRED_RE.sub("", s)
    # 2) any close tag still present is unmatched → preceding text is thinking
    if _THINK_ANY_CLOSE_RE.search(s):
        s = _THINK_DANGLING_CLOSE_RE.sub("", s, count=1)
    return s.strip()


class ThinkingStreamStripper:
    """Streaming-safe sibling of :func:`strip_thinking_segments`.

    ``strip_thinking_segments`` only runs on a *whole* non-streaming reply.
    Focus (thinking-on) turns stream token-by-token straight into TTS + the UI, so a
    provider that leaks chain-of-thought into ``content`` would speak its
    reasoning aloud. Only the Qwen3.5/3.6/3.7 hybrids do this: they dump the
    whole CoT into ``content`` terminated by a lone ``</think>`` (clean
    providers route reasoning to the separate ``reasoning_content`` field,
    which the streaming loop already withholds). So this holds **all** content
    until the first ``</think>`` (or a paired ``<think>...</think>``) is seen —
    dropping everything up to and including it — then passes the real answer
    through untouched, chunk by chunk.

    Engage it ONLY for ``thinking_on`` turns on a leak-prone model
    (``config.providers.leaks_thinking_in_content``): for clean providers the
    close tag never arrives, so holding-until-``</think>`` would withhold the
    whole answer until ``flush``. If a leak-prone model didn't think this turn
    (no close tag), ``flush`` returns the held buffer intact so nothing is lost.
    Split tags across chunks are safe — the buffer accumulates until matched.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._passthrough = False

    def feed(self, text: str) -> str:
        """Return the emittable slice of ``text`` (``""`` while still buffering)."""
        if self._passthrough:
            return text
        if not text:
            return ""
        self._buf += text
        m = _THINK_ANY_CLOSE_RE.search(self._buf)
        if m:
            # Everything up to and including the first close tag is the leaked
            # CoT (covers both the dangling shape and a paired <think>...</think>,
            # whose opening tag sits earlier in the buffer). Release the tail and
            # stream freely from here on.
            tail = self._buf[m.end():]
            self._buf = ""
            self._passthrough = True
            return tail
        return ""

    def flush(self) -> str:
        """Drain any held content at stream end (no close tag ever arrived)."""
        if self._passthrough:
            return ""
        residual = self._buf
        self._buf = ""
        return residual

    def reset(self) -> None:
        """Forget held state — used at a tool-round boundary, where the next
        segment is a fresh semantic unit and the one-shot CoT preamble (if any)
        is already behind us."""
        self._buf = ""
        self._passthrough = False


# ────────────────────────────────────────────────────────────────
# Active-character context — used by ChatOpenAI._params to substitute
# ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders that originated from
# plugin-supplied prompt fragments (the dialog LLM path already substitutes
# at ``main_logic.core._render_callback_inner_item``; brain pipeline LLM
# calls — analyzer / plugin LLM — used to leak the literal placeholder
# all the way to the wire, surfacing as a ``llm_prompt_leak_check``
# WARNING. Setting this contextvar at the brain entry point bridges the
# gap.) ContextVar is async-safe — values inherit through ``asyncio.gather``
# children automatically and don't bleed across unrelated tasks.
# ────────────────────────────────────────────────────────────────

_active_character: "contextvars.ContextVar[tuple[str, str] | None]" = contextvars.ContextVar(
    "_neko_active_character_master_lanlan", default=None
)

# ────────────────────────────────────────────────────────────────
# Dialog slop-reduction context — armed by the offline dialog entry points
# (``OmniOfflineClient.stream_text`` / ``prompt_ephemeral``) for the duration
# of a single text dialog turn. When set to a short language code,
# ``ChatOpenAI._params`` rewrites AI-writing clichés in the assistant turns of
# the *outgoing* messages (promptOnly — the persisted history is untouched). It
# stays ``None`` for every other LLM call (memory / agent / activity / realtime),
# so those paths are never touched. Async-safe like ``_active_character``;
# resolved + reset by a coroutine wrapper so the value never leaks past a turn.
# ────────────────────────────────────────────────────────────────
_dialog_slop_lang: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "_neko_dialog_slop_lang", default=None
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


def set_dialog_slop_lang(lang: "str | None") -> "contextvars.Token":
    """Arm slop reduction for the current dialog turn with a short language code
    (``zh`` / ``en`` / …). Subsequent ``ChatOpenAI._params`` calls on this async
    context rewrite the assistant turns of the outgoing messages. Pass ``None``
    to explicitly disarm for this turn (overriding any inherited value). Returns
    a token; pass it to ``reset_dialog_slop_lang`` in a ``finally``."""
    return _dialog_slop_lang.set(lang)


def reset_dialog_slop_lang(token: "contextvars.Token") -> None:
    _dialog_slop_lang.reset(token)


def peek_dialog_slop_lang() -> "str | None":
    """Read the currently-armed dialog slop language (or ``None``). For backends
    that build their payload outside ``ChatOpenAI._params`` / ``ChatAnthropic.
    _build_payload`` — e.g. the native-Gemini ``genai`` SDK path — and so must
    apply slop reduction to their own message copy explicitly."""
    return _dialog_slop_lang.get()


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


# Anthropic-style ephemeral cache marker. Fresh dict per attach site so two
# messages never alias the same mutable object.
_CACHE_CONTROL_EPHEMERAL = {"type": "ephemeral"}
_TEXT_PART_TYPES = ("text", "input_text", "output_text")


def _attach_cache_control(message: dict) -> dict | None:
    """Return a NEW message dict with ``cache_control: {"type": "ephemeral"}``
    attached to a text content block, or ``None`` if there's nothing markable.

    Anthropic (and Anthropic-compat gateways speaking OpenAI wire format) carry
    the cache breakpoint on a content *block*, not on the message itself. A
    plain-string ``content`` is promoted to a single text part so the marker
    has somewhere to live; an existing parts list gets the marker on its last
    text part. Defensive copy throughout — never mutates the input.

    Caveat for whoever flips a provider on: this promotes a string ``system``
    message into a content-parts array. Native Anthropic and most compat
    gateways accept that, but a few stricter OpenAI-compatible endpoints only
    allow array content on the ``user`` role — verify the target gateway, and
    if needed steer the breakpoint to the last user message for that provider.
    """
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        part = {"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        return {**message, "content": [part]}
    if isinstance(content, list):
        idx = next(
            (i for i in range(len(content) - 1, -1, -1)
             if isinstance(content[i], dict) and content[i].get("type") in _TEXT_PART_TYPES),
            None,
        )
        if idx is None:
            return None
        # Idempotent: if the breakpoint part already carries a marker, leave it
        # (and any richer TTL it may hold) untouched rather than clobbering it.
        if "cache_control" in content[idx]:
            return None
        new_parts = list(content)
        new_parts[idx] = {**new_parts[idx], "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        return {**message, "content": new_parts}
    return None


def _inject_cache_control(messages: list) -> list:
    """Return a NEW messages list with a single body-level cache breakpoint
    marker on the most stable prefix — the END of the leading contiguous run of
    ``system`` messages, falling back to the last message when there's no
    leading system message.

    The "leading contiguous" choice (rather than "last system message anywhere")
    is deliberate: some role-tagged histories in this codebase append a
    *trailing*, non-instructional system message later in the conversation
    (status notices, archive markers). Anchoring to the leading system block
    keeps the breakpoint on the large stable prefix instead of letting a tiny
    trailing system note steal it and cache almost nothing.

    Used only for providers whose caching needs a request-*body* flag rather
    than a header (see ``config.providers.CacheProviderConfig.requires_body_flag``
    / ``ChatOpenAI.enable_cache_control``). Header-based providers (DashScope)
    never reach here. Defensive copy — the input list and its dicts are left
    untouched, so repeated calls are idempotent. No-op (returns the input) when
    the list is empty or the chosen message has no markable text content.
    """
    if not messages:
        return messages
    target: int | None = None
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "system":
            target = i
        else:
            break
    if target is None:
        target = len(messages) - 1
    chosen = messages[target]
    if not isinstance(chosen, dict):
        return messages
    marked = _attach_cache_control(chosen)
    if marked is None:
        return messages
    out = list(messages)
    out[target] = marked
    return out


# ────────────────────────────────────────────────────────────────
# Message classes
# ────────────────────────────────────────────────────────────────

_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "system": "system"}
_ROLE_TO_TYPE = {"user": "human", "assistant": "ai", "system": "system"}


@dataclass
class BaseMessage:
    content: Any
    type: str = ""

    @property
    def role(self) -> str:
        return _TYPE_TO_ROLE.get(self.type, self.type)

    def to_openai(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class SystemMessage(BaseMessage):
    type: str = field(default="system", init=False)


@dataclass
class HumanMessage(BaseMessage):
    type: str = field(default="human", init=False)


@dataclass
class AIMessage(BaseMessage):
    type: str = field(default="ai", init=False)


_TYPE_CLS: dict[str, type[BaseMessage]] = {
    "human": HumanMessage,
    "ai": AIMessage,
    "system": SystemMessage,
}
_ROLE_CLS: dict[str, type[BaseMessage]] = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
}

# ────────────────────────────────────────────────────────────────
# Serialization helpers
# ────────────────────────────────────────────────────────────────


def messages_to_dict(messages: list) -> list[dict]:
    """Serialize message objects to the on-disk format.

    Output format per element::

        {"type": "human", "data": {"content": "hello"}}

    Backward-compatible with files written by the old langchain serializer.
    """
    result: list[dict] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            result.append({"type": msg.type, "data": {"content": msg.content}})
        elif isinstance(msg, dict):
            if "type" in msg and "data" in msg:
                result.append(msg)
            elif "role" in msg:
                t = _ROLE_TO_TYPE.get(msg["role"], msg["role"])
                result.append({"type": t, "data": {"content": msg.get("content", "")}})
            else:
                result.append(msg)
        else:
            t = getattr(msg, "type", "human")
            result.append({"type": t, "data": {"content": getattr(msg, "content", str(msg))}})
    return result


def messages_from_dict(dicts: list[dict]) -> list[BaseMessage]:
    """Deserialize on-disk dicts back to message objects.

    Accepts both legacy format (``type``/``data``) and OpenAI format
    (``role``/``content``) for robustness.
    """
    result: list[BaseMessage] = []
    for d in dicts:
        if "data" in d and "type" in d:
            cls = _TYPE_CLS.get(d["type"], HumanMessage)
            content = d["data"].get("content", "") if isinstance(d["data"], dict) else d["data"]
            result.append(cls(content=content))
        elif "role" in d and "content" in d:
            cls = _ROLE_CLS.get(d["role"], HumanMessage)
            result.append(cls(content=d["content"]))
        else:
            result.append(HumanMessage(content=str(d)))
    return result


def convert_to_messages(data: Any) -> list[BaseMessage]:
    """Convert various serialized formats to message objects.

    Handles the OpenAI dict format sent over HTTP from cross_server
    as well as the legacy on-disk format.
    """
    if isinstance(data, list):
        return messages_from_dict(data)
    return []


# ────────────────────────────────────────────────────────────────
# LLM response wrappers
# ────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    response_metadata: dict = field(default_factory=dict)


@dataclass
class LLMStreamChunk:
    content: str
    usage_metadata: dict | None = None
    response_metadata: dict | None = None
    # Streamed tool_calls fragment (OpenAI Chat Completions schema):
    # ``[{"index": 0, "id": "...", "type": "function",
    #     "function": {"name": "...", "arguments": "<json fragment>"}}]``
    # Multiple chunks may carry the same ``index`` — callers must
    # accumulate ``function.arguments`` strings before JSON-parsing.
    tool_call_deltas: list[dict] | None = None
    # Reason the model finished this stream segment: ``"stop"`` / ``"length"`` /
    # ``"tool_calls"`` / ``"content_filter"`` / None. ``"tool_calls"`` signals
    # the caller should run the tool then continue the conversation.
    finish_reason: str | None = None
    # Thinking-mode 模型（DeepSeek-R 系 / Qwen / GLM thinking 等 OpenAI-compat
    # 端点）在 ``delta`` 里单独流出的推理链文本。普通对话用不到，但 **多轮
    # tool calling** 时这些 provider 要求把发起 tool_calls 那条 assistant 消息的
    # ``reasoning_content`` 原样回填，否则下一轮报 400 "The `reasoning_content`
    # in the thinking mode must be passed back to the API."。tool 循环靠累积此
    # 字段把它写回 assistant 历史。
    reasoning_content: str | None = None
    # ``OmniOfflineClient`` tool-loop sentinel：``_astream_*_with_tools`` 在
    # 把当前 tool 轮（assistant tool_calls + tool result）inline 写进 history
    # 后会 yield 一个 ``LLMStreamChunk(content="", tool_round_persisted=True)``。
    # ``stream_text`` 看到就把 final-segment buffer 清掉，避免之后
    # ``_conversation_history.append(AIMessage(content=...))`` 把 pre-tool
    # 文本第二次写进 history（pre-tool 文本已经在 ``assistant.tool_calls.content``
    # 里了）。
    tool_round_persisted: bool = False


@dataclass
class ToolCallAggregate:
    """Fully-assembled tool call after streaming finished.

    Built by ``ChatOpenAI.collect_tool_calls()`` from the per-index
    fragments yielded across ``LLMStreamChunk.tool_call_deltas``."""

    index: int
    id: str
    name: str
    arguments: str  # JSON string; caller decides whether to ``json.loads``


# ────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────

def _normalize_messages(messages: Any) -> list[dict]:
    """Convert various message formats to openai-compatible dicts.

    ``BaseMessage`` subclasses pass their ``tool_calls`` / ``tool_call_id`` fields (when
    present) through to OpenAI Chat Completions — both fields are required when
    backfilling multi-turn tool-calling conversations: the assistant role carries
    tool_calls + the tool role carries tool_call_id."""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    out: list[dict] = []
    for msg in messages:
        if isinstance(msg, dict):
            if "role" in msg:
                out.append(msg)
            elif "type" in msg and "data" in msg:
                role = _TYPE_TO_ROLE.get(msg["type"], msg["type"])
                content = msg["data"].get("content", "") if isinstance(msg["data"], dict) else msg["data"]
                out.append({"role": role, "content": content})
            else:
                out.append(msg)
        elif isinstance(msg, BaseMessage):
            base = msg.to_openai()
            # Tool-calling round-trip fields — only attach when present so we
            # don't pollute non-tool conversations with empty arrays.
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                base["tool_calls"] = tool_calls
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id:
                base["tool_call_id"] = tool_call_id
            out.append(base)
        elif hasattr(msg, "type") and hasattr(msg, "content"):
            role = _TYPE_TO_ROLE.get(msg.type, msg.type)
            out.append({"role": role, "content": msg.content})
        else:
            out.append({"role": "user", "content": str(msg)})
    return out


# ────────────────────────────────────────────────────────────────
# ChatOpenAI — lightweight OpenAI-compatible LLM client
# ────────────────────────────────────────────────────────────────

class ChatOpenAI:
    """OpenAI-compatible chat client with streaming, invoke, and resource management."""

    def __init__(
        self,
        model: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        streaming: bool = False,
        max_retries: int = 2,
        extra_body: dict | None = None,
        max_completion_tokens: int | None = None,
        max_tokens: int | None = None,
        model_kwargs: dict | None = None,
        timeout: float | None = None,
        request_timeout: float | None = None,
        default_headers: dict | None = None,
        enable_cache_control: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        **_kwargs: Any,
    ):
        self.model = model
        self.base_url = base_url
        # Tool-calling defaults baked into instance; per-call ``overrides``
        # in ``_params()`` can still substitute a different list (e.g. when
        # the caller wants to suppress tools mid-conversation).
        self.tools = list(tools) if tools else None
        self.tool_choice = tool_choice
        # 项目级硬性约定：不再下发 temperature。default=None → 不写进请求体，
        # 由模型端自定。o1/o3/gpt-5-thinking/Claude extended-thinking 等拒绝该
        # 参数的模型可以直通；普通模型也走它们自己的默认值，避免不同 task 之间
        # 因为温度数值漂移引入难复现的回归。callers 不应再传 temperature=
        # （CI 由 scripts/check_no_temperature.py 守门）。
        self.temperature = temperature
        self.extra_body: dict = extra_body or {}
        self.max_completion_tokens = max_completion_tokens
        self.max_tokens = max_tokens
        # When True, _params() stamps a body-level Anthropic-style
        # cache_control marker onto the cache breakpoint message. Set by
        # create_chat_llm via config.providers.get_cache_kwargs for providers
        # with requires_body_flag (Anthropic direct / Anthropic-compat). Header
        # cache providers (DashScope) leave this False — their header does the job.
        self.enable_cache_control = enable_cache_control

        if model_kwargs and "extra_body" in model_kwargs:
            self.extra_body = {**self.extra_body, **model_kwargs["extra_body"]}

        _api_key = api_key or "sk-placeholder"
        _timeout = timeout or request_timeout
        client_kw: dict[str, Any] = dict(base_url=base_url, api_key=_api_key, max_retries=max_retries)
        if _timeout is not None:
            client_kw["timeout"] = _timeout
        if default_headers:
            client_kw["default_headers"] = default_headers
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

        ssl_context = _get_default_ssl_context()
        client_kw["http_client"] = DefaultAsyncHttpxClient(verify=ssl_context)
        self._aclient = AsyncOpenAI(**client_kw)
        client_kw["http_client"] = DefaultHttpxClient(verify=ssl_context)
        self._client = OpenAI(**client_kw)
        self._client_finalizer = weakref.finalize(
            self,
            _close_chat_openai_clients_best_effort,
            self._client,
            self._aclient,
        )

    def _is_anthropic(self) -> bool:
        return bool(self.base_url) and "api.anthropic.com" in str(self.base_url)

    def _params(self, messages: Any, *, stream: bool = False, **overrides: Any) -> dict:
        """Build the request body. ``overrides`` lets per-call invokers
        substitute ``max_completion_tokens`` / ``max_tokens`` / ``extra_body``
        (and any other SDK-accepted kwarg) without mutating the instance —
        critical when a single ChatOpenAI is shared across concurrent code
        paths (e.g. background ping vs. main task in computer_use)."""
        p: dict[str, Any] = {
            "model": self.model,
            "messages": _normalize_messages(messages),
            "stream": stream,
        }
        # 项目级约定：default=None → 不写 temperature 进请求体。本分支保留是
        # 为了向后兼容显式 `temperature=0` 这类 case（0.0 合法，所以判 None 而
        # 不是 truthy），实际 callers 不应再传该参数。
        if self.temperature is not None:
            p["temperature"] = self.temperature
        # Provider-aware routing of token-limit field:
        #   Anthropic SDK / Anthropic-compat endpoints → max_tokens
        #   Everyone else (OpenAI / OpenAI-compat / Gemini-compat / etc.) → max_completion_tokens
        # Per-call overrides take precedence over instance attrs so concurrent
        # callers on the same client don't corrupt each other's budgets.
        token_limit = overrides.pop("max_completion_tokens", None)
        if token_limit is None:
            token_limit = overrides.pop("max_tokens", None)
        if token_limit is None:
            token_limit = self.max_completion_tokens or self.max_tokens
        limit_field: str | None = None
        limit_value: int | None = None
        if token_limit:
            limit_field = "max_tokens" if self._is_anthropic() else "max_completion_tokens"
            limit_value = int(token_limit)
            p[limit_field] = limit_value
        extra_body = overrides.pop("extra_body", self.extra_body)
        if extra_body:
            p["extra_body"] = extra_body
        # Tool calling: per-call overrides take priority over instance default
        # so callers can disable tools (``tools=[]`` 或 ``tools=None``) for
        # special turns（如 prompt_ephemeral 中明确不要工具）。
        tools = overrides.pop("tools", self.tools)
        if tools:
            p["tools"] = tools
            tool_choice = overrides.pop("tool_choice", self.tool_choice)
            if tool_choice is not None:
                p["tool_choice"] = tool_choice
        else:
            # Don't leak tool_choice without tools — some endpoints 400 on it.
            overrides.pop("tool_choice", None)
        if stream:
            p["stream_options"] = {"include_usage": True}
        # Anything else the caller passed (e.g. timeout, logit_bias) goes
        # straight through to the SDK call.
        p.update(overrides)

        # Resolve {MASTER_NAME}/{LANLAN_NAME} placeholders that arrived
        # from plugin-supplied prompt fragments (cue text / nudge prompt /
        # plugin descriptions etc.) before the leak check and wire send.
        # The dialog LLM path resolves these in
        # ``main_logic.core._render_callback_inner_item``; brain pipeline
        # LLM calls don't go through that renderer, so without this step
        # the literal placeholder leaks all the way through.
        # ``set_active_character`` sets the contextvar at brain entry;
        # this reads + substitutes. No-op if no character is active
        # (e.g. callers outside the brain pipeline) — the leak check
        # below will then fire its WARNING the same as before.
        active = _active_character.get()
        if active is not None:
            master, lanlan = active
            if master or lanlan:
                p["messages"] = _substitute_character_placeholders(
                    p["messages"], master, lanlan
                )

        # Slop reduction (降低人机感): when the dialog entry point armed
        # ``_dialog_slop_lang`` for this turn, rewrite AI-writing clichés in the
        # assistant turns of the outgoing messages so the model stops imitating
        # its own stock phrases. promptOnly — ``p["messages"]`` is already a
        # normalized copy (``_normalize_messages``), so the persisted history and
        # the tool-loop's working list are untouched. ``None`` for every
        # non-dialog call → no-op. Must never break the LLM call. Runs before the
        # cache-control stamp so the marker lands on the final (rewritten) text.
        slop_lang = _dialog_slop_lang.get()
        if slop_lang:
            try:
                from utils.slop_filter import apply_slop_reduction
                p["messages"] = apply_slop_reduction(p["messages"], slop_lang)
            except Exception:
                # Slop reduction is a best-effort enhancement — never let a bug
                # in it break the LLM call. Swallow and send the original
                # messages unchanged.
                pass

        # Body-level cache flag: stamp an Anthropic-style cache_control marker
        # onto the cache breakpoint. Gated on the provider opting in via
        # requires_body_flag (no live provider does today, so this is a no-op
        # for current traffic) — header-cache providers leave this False. Runs
        # after character substitution so the marked text is already resolved,
        # and before the leak check (which transparently scans list-of-parts
        # content) so a promoted string still gets audited for placeholder leaks.
        if self.enable_cache_control:
            p["messages"] = _inject_cache_control(p["messages"])

        # Catch prompt-template leaks: literal {placeholder} that should have
        # been .format()-ed before reaching the wire. See
        # utils/llm_prompt_leak_check.py for the rationale and severity
        # contract (raise in tests, warn in prod).
        try:
            from utils import llm_prompt_leak_check
            llm_prompt_leak_check.check_messages_for_leaks(
                p["messages"], context=f"ChatOpenAI._params model={self.model}"
            )
        except AssertionError:
            raise
        except Exception:
            # Detector bugs must never break the LLM call itself.
            pass

        # TEMPORARY: prompt audit log (env NEKO_LLM_PROMPT_AUDIT=1). Remove with
        # utils/llm_prompt_audit.py once budget tuning is done.
        try:
            from utils import llm_prompt_audit
            if llm_prompt_audit.is_enabled():
                llm_prompt_audit.record_llm_request(
                    model=self.model,
                    base_url=self.base_url,
                    params=p,
                    field_name=limit_field,
                    field_value=limit_value,
                )
        except Exception:
            # Audit hook is debug-only and must never bubble up — a broken
            # logger should not break LLM calls. Intentionally swallowed;
            # this whole try/except disappears when the audit module is
            # removed.
            pass
        return p

    # --- sync / async invoke ---

    async def ainvoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        """``overrides`` (e.g. ``max_completion_tokens=1100``) flow through
        ``_params()`` so concurrent callers on the same client don't
        clobber each other's budgets. See ``ainvoke_raw`` for details."""
        resp = await self._aclient.chat.completions.create(**self._params(messages, **overrides))
        # 防御性读取：部分上游（如 free-agent-model）会返回 choices 非空但
        # message=None 的合法响应，直接 .message.content 会 NoneType 崩溃。
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        content = strip_thinking_segments(getattr(msg, "content", None))
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    def invoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        """Sync twin of ``ainvoke``. See its docstring for ``overrides``."""
        resp = self._client.chat.completions.create(**self._params(messages, **overrides))
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        content = strip_thinking_segments(getattr(msg, "content", None))
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    # --- raw-resp invoke (for callers needing reasoning_content / raw choices) ---

    async def ainvoke_raw(self, messages: Any, **overrides: Any):
        """Async invoke that returns the underlying SDK ChatCompletion
        response. Parameter routing still flows through `_params()`
        (Anthropic → max_tokens, others → max_completion_tokens). Use only
        when you need fields beyond `LLMResponse` (e.g. thinking models'
        ``reasoning_content``); prefer `ainvoke` otherwise.

        ``overrides`` lets the caller provide per-call values for
        ``max_completion_tokens`` / ``max_tokens`` / ``extra_body`` / SDK
        kwargs like ``timeout`` without touching ``self.*`` — required when
        a single client is shared across concurrent code paths."""
        return await self._aclient.chat.completions.create(
            **self._params(messages, **overrides)
        )

    def invoke_raw(self, messages: Any, **overrides: Any):
        """Sync twin of `ainvoke_raw`. See its docstring for ``overrides``."""
        return self._client.chat.completions.create(
            **self._params(messages, **overrides)
        )

    # --- async streaming ---

    async def astream(self, messages: Any, **overrides: Any) -> AsyncIterator[LLMStreamChunk]:
        """Stream chunks. Yields:

        - text-content chunks (``content`` non-empty)
        - tool_calls fragments (``tool_call_deltas`` non-None) — caller
          accumulates ``[].function.arguments`` per ``index`` until the
          chunk with ``finish_reason == "tool_calls"`` arrives, then runs
          the tools and appends a fresh assistant + tool turn before
          calling ``astream`` again.
        - terminal usage chunk
        """
        stream = await self._aclient.chat.completions.create(
            **self._params(messages, stream=True, **overrides)
        )
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice else None
            content = delta.content if delta and delta.content else ""
            tool_call_deltas: list[dict] | None = None
            if delta is not None:
                raw_tool_calls = getattr(delta, "tool_calls", None) or []
                if raw_tool_calls:
                    tool_call_deltas = []
                    for tc in raw_tool_calls:
                        # SDK objects → plain dicts. ``index`` ties fragments
                        # of the same call together across chunks.
                        fn = getattr(tc, "function", None)
                        tool_call_deltas.append({
                            "index": getattr(tc, "index", 0),
                            "id": getattr(tc, "id", "") or "",
                            "type": getattr(tc, "type", "function") or "function",
                            "function": {
                                "name": getattr(fn, "name", "") if fn else "",
                                "arguments": getattr(fn, "arguments", "") if fn else "",
                            },
                        })
            # Thinking 模型把推理链放在非标准的 ``delta.reasoning_content``
            # 字段（openai-python 的 BaseModel extra=allow，未知字段照样可
            # getattr）。普通端点没有这个字段，getattr 返回 None。
            reasoning_content = (
                getattr(delta, "reasoning_content", None) if delta else None
            )
            finish_reason = getattr(choice, "finish_reason", None) if choice else None
            if content or tool_call_deltas or finish_reason or reasoning_content:
                yield LLMStreamChunk(
                    content=content,
                    tool_call_deltas=tool_call_deltas,
                    finish_reason=finish_reason,
                    reasoning_content=reasoning_content,
                )
            # Terminal chunk with usage info (stream_options={"include_usage": True})
            if chunk.usage is not None:
                usage_dict = chunk.usage.model_dump()
                yield LLMStreamChunk(
                    content="",
                    usage_metadata=usage_dict,
                    response_metadata={"token_usage": usage_dict},
                )

    @staticmethod
    def collect_tool_calls(deltas_per_chunk: list[list[dict] | None]) -> list[ToolCallAggregate]:
        """Combine per-chunk tool_call deltas (in arrival order) into the
        final list of completed tool calls. Caller passes the
        ``tool_call_deltas`` field of every chunk in the order yielded.

        Multiple parallel calls are kept distinct via ``index`` (the OpenAI
        Chat Completions schema guarantees one ``index`` per call).

        ⚠️ Aggregation slots with an empty ``name`` are dropped — broken fragments
        produced by SDK bugs / prematurely interrupted streams / some small models.
        Written into the ``tool_calls`` history unfiltered, the next round's call
        would be rejected by the server as schema invalid. Dropping here lets the
        upper layer take the normal "the model called no tool this round" branch.
        """
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        merged: dict[int, dict] = {}
        for fragments in deltas_per_chunk:
            if not fragments:
                continue
            for frag in fragments:
                idx = int(frag.get("index", 0))
                slot = merged.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if frag.get("id"):
                    slot["id"] = frag["id"]
                fn = frag.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        out: list[ToolCallAggregate] = []
        for idx in sorted(merged.keys()):
            slot = merged[idx]
            if not slot["name"]:
                _logger.warning(
                    "ChatOpenAI.collect_tool_calls: dropping fragment with empty name "
                    "(idx=%d, id=%r) — likely a streaming SDK glitch",
                    idx, slot.get("id"),
                )
                continue
            out.append(ToolCallAggregate(
                index=idx,
                id=slot["id"],
                name=slot["name"],
                arguments=slot["arguments"],
            ))
        return out

    # --- resource management ---

    async def aclose(self) -> None:
        """Close underlying httpx clients (async path)."""
        await self._aclient.close()
        self._client.close()
        self._client_finalizer.detach()

    def close(self) -> None:
        """Close underlying httpx clients (sync path)."""
        self._client.close()
        _close_async_openai_client_from_sync_best_effort(self._aclient)
        self._client_finalizer.detach()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()


# ────────────────────────────────────────────────────────────────
# Anthropic-compatible chat client (Kimi Code / Anthropic)
# ────────────────────────────────────────────────────────────────

def _parse_base_url(base_url: str | None):
    if not base_url:
        return None
    raw = str(base_url).strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"//{raw}"
    return urlparse(raw)


def _is_kimi_code_anthropic_base_url(base_url: str | None) -> bool:
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return False
    return (parsed.hostname or "").lower() == "api.kimi.com" and parsed.path.rstrip("/") == "/coding"


def _normalize_anthropic_sdk_base_url(base_url: str | None) -> str | None:
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return base_url
    if (parsed.hostname or "").lower() == "api.anthropic.com" and parsed.path.rstrip("/") == "/v1":
        scheme = parsed.scheme or "https"
        return f"{scheme}://{parsed.netloc}"
    return base_url


def _is_anthropic_endpoint(base_url: str | None, provider_type: str | None = None) -> bool:
    """Detect endpoints that speak the Anthropic Messages API format."""
    if provider_type and str(provider_type).lower() == "anthropic":
        return True
    parsed = _parse_base_url(base_url)
    if parsed is None:
        return False
    if (parsed.hostname or "").lower() == "api.anthropic.com":
        return True
    return _is_kimi_code_anthropic_base_url(base_url)


def _detect_image_media_type(image_bytes: bytes) -> str:
    """Guess image media type from magic bytes."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"GIF":
        return "image/gif"
    if image_bytes[:2] == b"BM":
        return "image/bmp"
    if image_bytes[:4] == b"RIFF" and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/jpeg"


def _coerce_anthropic_max_tokens(value: Any, *, default: int = 2048) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _anthropic_usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        data = dict(usage)
    elif hasattr(usage, "model_dump"):
        data = usage.model_dump()
    else:
        data = {
            key: getattr(usage, key)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
            if hasattr(usage, key)
        }
    return {str(k): v for k, v in data.items() if v is not None}


def _merge_anthropic_usage(target: dict[str, Any], usage: Any) -> None:
    target.update(_anthropic_usage_to_dict(usage))


def anthropic_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return Anthropic SDK error classes that should follow the chat retry path."""
    global _ANTHROPIC_RETRY_EXCEPTION_TYPES
    if _ANTHROPIC_RETRY_EXCEPTION_TYPES is None:
        try:
            import anthropic as _anthropic
        except Exception:  # pragma: no cover - anthropic may be absent in minimal installs
            _anthropic = None
        _ANTHROPIC_RETRY_EXCEPTION_TYPES = tuple(
            exc_type
            for exc_type in (
                getattr(_anthropic, "APIConnectionError", None),
                getattr(_anthropic, "APITimeoutError", None),
                getattr(_anthropic, "AuthenticationError", None),
                getattr(_anthropic, "InternalServerError", None),
                getattr(_anthropic, "RateLimitError", None),
            )
            if isinstance(exc_type, type)
        )
    return _ANTHROPIC_RETRY_EXCEPTION_TYPES


def _record_anthropic_token_usage(model: str, usage_dict: dict[str, Any]) -> None:
    if not usage_dict:
        return
    try:
        from utils.token_tracker import record_anthropic_usage
        record_anthropic_usage(model=model, usage=usage_dict)
    except Exception:
        pass


def _anthropic_stop_reason_to_finish_reason(stop_reason: Any) -> str | None:
    if not stop_reason:
        return None
    reason = str(stop_reason)
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
    }.get(reason, reason)


def _convert_openai_content_to_anthropic(content: Any) -> list[dict]:
    """Convert an OpenAI message content value to Anthropic content blocks."""
    blocks: list[dict] = []
    if content is None:
        return blocks
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
        return blocks
    if not isinstance(content, list):
        blocks.append({"type": "text", "text": str(content)})
        return blocks

    for part in content:
        if part is None:
            continue
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                blocks.append({"type": "text", "text": text})
        elif part_type == "image_url":
            image_url_data = part.get("image_url", {}) or {}
            url = image_url_data.get("url", "") if isinstance(image_url_data, dict) else str(image_url_data)
            if url.startswith("data:"):
                try:
                    _, base64_data = url.split(",", 1)
                    image_bytes = base64.b64decode(base64_data)
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _detect_image_media_type(image_bytes),
                            "data": base64_data,
                        },
                    })
                except Exception:
                    blocks.append({"type": "text", "text": "[图片解析失败]"})
            elif url:
                blocks.append({"type": "text", "text": f"[图片: {url}]"})
        else:
            blocks.append(part)
    return blocks


def _anthropic_text_from_blocks(blocks: list[dict]) -> str:
    text_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "")
            if text:
                text_parts.append(text)
        elif block:
            text_parts.append(_json.dumps(block, ensure_ascii=False))
    return "\n".join(text_parts)


def _anthropic_tool_use_ids_from_blocks(blocks: list[dict]) -> set[str]:
    ids: set[str] = set()
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            raw_id = block.get("id")
            if raw_id:
                ids.add(str(raw_id))
    return ids


def _convert_openai_tool_call_to_anthropic(tool_call: Any) -> dict[str, Any] | None:
    if not isinstance(tool_call, dict):
        tool_call = {
            "id": getattr(tool_call, "id", ""),
            "type": getattr(tool_call, "type", ""),
            "function": getattr(tool_call, "function", None),
        }
    fn = tool_call.get("function")
    if fn is not None and not isinstance(fn, dict):
        fn = {
            "name": getattr(fn, "name", ""),
            "arguments": getattr(fn, "arguments", ""),
        }
    fn = fn if isinstance(fn, dict) else {}
    name = str(fn.get("name") or tool_call.get("name") or "").strip()
    if not name:
        return None
    raw_args = fn.get("arguments", {})
    if isinstance(raw_args, str):
        try:
            parsed_args = _json.loads(raw_args) if raw_args.strip() else {}
        except Exception:
            parsed_args = {"arguments": raw_args}
    elif isinstance(raw_args, dict):
        parsed_args = raw_args
    else:
        parsed_args = {}
    tool_use_id = str(tool_call.get("id") or name)
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": parsed_args,
    }


def _convert_openai_tool_schema_to_anthropic(tool: Any) -> dict[str, Any] | None:
    """Convert an OpenAI Chat Completions tool definition to Anthropic schema."""
    if tool is None:
        return None
    if not isinstance(tool, dict):
        if hasattr(tool, "model_dump"):
            tool = tool.model_dump()
        else:
            tool = {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "input_schema": getattr(tool, "input_schema", None) or getattr(tool, "parameters", None),
            }

    fn = tool.get("function") if isinstance(tool, dict) else None
    if fn is not None and not isinstance(fn, dict):
        fn = {
            "name": getattr(fn, "name", ""),
            "description": getattr(fn, "description", ""),
            "parameters": getattr(fn, "parameters", None),
        }
    fn = fn if isinstance(fn, dict) else {}

    name = str(fn.get("name") or tool.get("name") or "").strip()
    if not name:
        return None

    input_schema = fn.get("parameters") or tool.get("input_schema") or tool.get("parameters")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    input_schema = dict(input_schema)
    input_schema.setdefault("type", "object")

    converted: dict[str, Any] = {
        "name": name,
        "input_schema": input_schema,
    }
    description = fn.get("description") or tool.get("description")
    if description:
        converted["description"] = str(description)
    return converted


def _convert_openai_tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    if not tools:
        return []
    if isinstance(tools, dict):
        tools = [tools]
    converted = []
    for tool in tools:
        anthropic_tool = _convert_openai_tool_schema_to_anthropic(tool)
        if anthropic_tool:
            converted.append(anthropic_tool)
    return converted


def _convert_openai_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        choice = tool_choice.strip().lower()
        if choice in {"auto", "any", "none"}:
            return {"type": choice}
        if choice in {"required", "required_auto"}:
            return {"type": "any"}
        return None
    if not isinstance(tool_choice, dict):
        return None

    if isinstance(tool_choice.get("type"), str):
        choice_type = tool_choice["type"].strip().lower()
        if choice_type in {"auto", "any", "none"}:
            return {"type": choice_type}
        if choice_type == "tool" and tool_choice.get("name"):
            return {"type": "tool", "name": str(tool_choice["name"])}
        if choice_type == "function":
            fn = tool_choice.get("function")
            if isinstance(fn, dict) and fn.get("name"):
                return {"type": "tool", "name": str(fn["name"])}
    return None


def _convert_openai_tool_result_to_anthropic(msg: dict) -> dict[str, Any]:
    blocks = _convert_openai_content_to_anthropic(msg.get("content"))
    tool_use_id = str(msg.get("tool_call_id") or msg.get("id") or msg.get("name") or "tool_result")
    content: str | list[dict] = _anthropic_text_from_blocks(blocks)
    if any(isinstance(block, dict) and block.get("type") != "text" for block in blocks):
        content = blocks
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


def _convert_orphan_tool_result_to_user_blocks(msg: dict) -> list[dict]:
    blocks = _convert_openai_content_to_anthropic(msg.get("content"))
    text = _anthropic_text_from_blocks(blocks)
    if text:
        return [{"type": "text", "text": f"[tool result] {text}"}]
    return [{"type": "text", "text": "[tool result]"}]


def _normalize_messages_to_anthropic(messages: Any) -> tuple[str, list[dict]]:
    """Convert OpenAI-format messages to Anthropic (system, messages).

    Anthropic requires:
      - ``system`` as a top-level string (not a message).
      - ``messages`` alternating user/assistant only.
    """
    normalized = _normalize_messages(messages)
    system_parts: list[str] = []
    anthropic_messages: list[dict] = []
    pending_tool_use_ids: set[str] = set()

    for msg in normalized:
        role = msg.get("role", "")
        content = msg.get("content", None)
        if role == "system":
            if content is None:
                continue
            if isinstance(content, str):
                system_parts.append(content)
            else:
                system_text = _anthropic_text_from_blocks(
                    _convert_openai_content_to_anthropic(content)
                )
                if system_text:
                    system_parts.append(system_text)
            continue
        if role == "tool":
            tool_use_id = str(msg.get("tool_call_id") or msg.get("id") or msg.get("name") or "tool_result")
            if tool_use_id not in pending_tool_use_ids:
                anthropic_messages.append({
                    "role": "user",
                    "content": _convert_orphan_tool_result_to_user_blocks(msg),
                })
                continue
            pending_tool_use_ids.discard(tool_use_id)
            anthropic_messages.append({
                "role": "user",
                "content": [_convert_openai_tool_result_to_anthropic(msg)],
            })
            continue
        if role not in ("user", "assistant"):
            role = "user"
        blocks = _convert_openai_content_to_anthropic(content)
        if role == "assistant":
            seen_tool_use_ids = _anthropic_tool_use_ids_from_blocks(blocks)
            for tool_call in msg.get("tool_calls") or []:
                converted = _convert_openai_tool_call_to_anthropic(tool_call)
                if converted and converted.get("id") not in seen_tool_use_ids:
                    blocks.append(converted)
                    seen_tool_use_ids.add(str(converted.get("id")))
            pending_tool_use_ids |= seen_tool_use_ids
        elif role == "user":
            pending_tool_use_ids = set()
        anthropic_messages.append({"role": role, "content": blocks or [{"type": "text", "text": "..."}]})

    # Enforce strict user/assistant alternation.
    fixed: list[dict] = []
    for msg in anthropic_messages:
        if not fixed:
            if msg["role"] != "user":
                fixed.append({"role": "user", "content": [{"type": "text", "text": "..."}]})
            fixed.append(msg)
            continue
        if msg["role"] == fixed[-1]["role"]:
            # Merge consecutive same-role turns into one content list.
            fixed[-1]["content"] = list(fixed[-1]["content"]) + list(msg["content"])
        else:
            fixed.append(msg)

    if not fixed:
        fixed.append({"role": "user", "content": [{"type": "text", "text": "..."}]})

    system = "\n".join(system_parts).strip()
    return system, fixed


_ANTHROPIC_BODY_OVERRIDE_KEYS = {
    "metadata",
    "stop_sequences",
    "system",
    "temperature",
    "thinking",
    "top_k",
    "top_p",
    "service_tier",
}
_ANTHROPIC_REQUEST_OPTION_KEYS = {"timeout", "extra_headers", "extra_query"}


def _sanitize_anthropic_metadata(metadata: Any) -> dict[str, str] | None:
    if not isinstance(metadata, dict):
        return None
    user_id = metadata.get("user_id")
    if user_id is None or user_id == "":
        return None
    return {"user_id": str(user_id)}


def _apply_anthropic_body_fields(payload: dict[str, Any], fields: Any) -> None:
    if not isinstance(fields, dict):
        return
    for key, value in fields.items():
        if value is None:
            continue
        if key == "max_tokens":
            payload["max_tokens"] = _coerce_anthropic_max_tokens(
                value, default=int(payload.get("max_tokens") or 2048)
            )
        elif key == "max_completion_tokens":
            payload["max_tokens"] = _coerce_anthropic_max_tokens(
                value, default=int(payload.get("max_tokens") or 2048)
            )
        elif key == "metadata":
            metadata = _sanitize_anthropic_metadata(value)
            if metadata:
                payload["metadata"] = metadata
        elif key in _ANTHROPIC_BODY_OVERRIDE_KEYS:
            payload[key] = value


class ChatAnthropic:
    """Anthropic Messages API client with a ChatOpenAI-compatible surface.

    Used for Kimi Code (``https://api.kimi.com/coding``) and native Anthropic
    endpoints. Text chat, streaming, and OpenAI-style tool calling are adapted
    to the Anthropic Messages API schema.
    """

    def __init__(
        self,
        model: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        streaming: bool = False,
        max_retries: int = 2,
        extra_body: dict | None = None,
        max_completion_tokens: int | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        request_timeout: float | None = None,
        default_headers: dict | None = None,
        enable_cache_control: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        **_kwargs: Any,
    ):
        anthropic_cls, async_anthropic_cls = Anthropic, AsyncAnthropic
        if anthropic_cls is None or async_anthropic_cls is None:
            try:
                from anthropic import Anthropic as anthropic_cls, AsyncAnthropic as async_anthropic_cls
            except Exception as exc:
                raise RuntimeError("anthropic package is required for Anthropic-compatible providers") from exc
        self.model = model
        self.base_url = _normalize_anthropic_sdk_base_url(base_url)
        self.temperature = temperature
        self.extra_body = dict(extra_body) if extra_body else {}
        self._max_tokens = _coerce_anthropic_max_tokens(
            max_tokens if max_tokens is not None else max_completion_tokens
        )
        self.enable_cache_control = enable_cache_control
        self.tools = list(tools) if tools else None
        self.tool_choice = tool_choice

        _api_key = api_key or "sk-placeholder"
        _timeout = timeout or request_timeout
        client_kw: dict[str, Any] = dict(api_key=_api_key, max_retries=max_retries)
        if self.base_url:
            client_kw["base_url"] = self.base_url
        if _timeout is not None:
            client_kw["timeout"] = _timeout
        if default_headers:
            client_kw["default_headers"] = default_headers

        self._client = anthropic_cls(**client_kw)
        self._aclient = async_anthropic_cls(**client_kw)
        self._client_finalizer = weakref.finalize(
            self,
            _close_chat_clients_best_effort,
            self._client,
            self._aclient,
        )

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value: Any) -> None:
        self._max_tokens = _coerce_anthropic_max_tokens(value)

    @property
    def max_completion_tokens(self) -> int:
        return self._max_tokens

    @max_completion_tokens.setter
    def max_completion_tokens(self, value: Any) -> None:
        self._max_tokens = _coerce_anthropic_max_tokens(value)

    def _build_payload(self, messages: Any, *, include_default_extra_body: bool = True) -> dict:
        system, anthropic_messages = _normalize_messages_to_anthropic(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
        }
        if system:
            payload["system"] = system
        payload["max_tokens"] = int(self.max_tokens)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if include_default_extra_body and self.extra_body:
            _apply_anthropic_body_fields(payload, self.extra_body)
        anthropic_tools = _convert_openai_tools_to_anthropic(self.tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            anthropic_tool_choice = _convert_openai_tool_choice_to_anthropic(self.tool_choice)
            if anthropic_tool_choice:
                payload["tool_choice"] = anthropic_tool_choice
        # Substitute character placeholders the same way ChatOpenAI does.
        active = _active_character.get()
        if active is not None and (active[0] or active[1]):
            payload["messages"] = _substitute_character_placeholders(
                payload["messages"], active[0], active[1]
            )
            if payload.get("system"):
                payload["system"] = _substitute_character_placeholders(
                    [{"role": "system", "content": payload["system"]}], active[0], active[1]
                )[0]["content"]

        # Slop reduction — mirror of ChatOpenAI._params so Anthropic-compat
        # dialog backends get the same promptOnly rewriting. ``payload["messages"]``
        # is the Anthropic-format copy from ``_normalize_messages_to_anthropic``,
        # so this never touches the caller's history. Only active when the dialog
        # entry point armed ``_dialog_slop_lang``; must never break the call.
        slop_lang = _dialog_slop_lang.get()
        if slop_lang:
            try:
                from utils.slop_filter import apply_slop_reduction
                payload["messages"] = apply_slop_reduction(payload["messages"], slop_lang)
            except Exception:
                # Best-effort enhancement — swallow and send originals on any error.
                pass
        return payload

    def _apply_overrides(self, payload: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        overrides = dict(overrides)
        max_tokens = overrides.pop("max_tokens", None)
        if max_tokens is None:
            max_tokens = overrides.pop("max_completion_tokens", None)
        else:
            overrides.pop("max_completion_tokens", None)
        if max_tokens is not None:
            payload["max_tokens"] = _coerce_anthropic_max_tokens(
                max_tokens, default=int(payload.get("max_tokens") or self.max_tokens)
            )
        overrides.pop("stream", None)
        extra_body = overrides.pop("extra_body", _SENTINEL)
        if extra_body:
            _apply_anthropic_body_fields(payload, extra_body)
        tools = overrides.pop("tools", _SENTINEL)
        if tools is not _SENTINEL:
            anthropic_tools = _convert_openai_tools_to_anthropic(tools)
            if anthropic_tools:
                payload["tools"] = anthropic_tools
            else:
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
        tool_choice = overrides.pop("tool_choice", _SENTINEL)
        if tool_choice is not _SENTINEL:
            anthropic_tool_choice = _convert_openai_tool_choice_to_anthropic(tool_choice)
            if anthropic_tool_choice:
                payload["tool_choice"] = anthropic_tool_choice
            else:
                payload.pop("tool_choice", None)
        for key, value in overrides.items():
            if value is None:
                continue
            if key in _ANTHROPIC_REQUEST_OPTION_KEYS:
                payload[key] = value
            elif key in _ANTHROPIC_BODY_OVERRIDE_KEYS:
                _apply_anthropic_body_fields(payload, {key: value})
        return payload

    def _build_payload_for_call(self, messages: Any, overrides: dict[str, Any]) -> dict[str, Any]:
        include_default_extra_body = "extra_body" not in overrides
        payload = self._build_payload(
            messages,
            include_default_extra_body=include_default_extra_body,
        )
        return self._apply_overrides(payload, overrides)

    async def ainvoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        payload = self._build_payload_for_call(messages, overrides)
        resp = await self._aclient.messages.create(**payload)
        text_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        content = strip_thinking_segments("".join(text_parts))
        usage = getattr(resp, "usage", None)
        usage_dict = _anthropic_usage_to_dict(usage)
        _record_anthropic_token_usage(self.model, usage_dict)
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    def invoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        payload = self._build_payload_for_call(messages, overrides)
        resp = self._client.messages.create(**payload)
        text_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        content = strip_thinking_segments("".join(text_parts))
        usage = getattr(resp, "usage", None)
        usage_dict = _anthropic_usage_to_dict(usage)
        _record_anthropic_token_usage(self.model, usage_dict)
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    async def astream(self, messages: Any, **overrides: Any) -> AsyncIterator[LLMStreamChunk]:
        payload = self._build_payload_for_call(messages, overrides)
        stream = self._aclient.messages.stream(**payload)
        usage_dict: dict[str, Any] = {}
        async with stream as response_stream:
            async for event in response_stream:
                event_type = getattr(event, "type", "")
                if event_type == "message_start":
                    message = getattr(event, "message", None)
                    _merge_anthropic_usage(usage_dict, getattr(message, "usage", None))
                elif event_type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block and getattr(block, "type", "") == "tool_use":
                        index = int(getattr(event, "index", 0) or 0)
                        raw_input = getattr(block, "input", None)
                        arguments = ""
                        if raw_input:
                            arguments = _json.dumps(raw_input, ensure_ascii=False)
                        yield LLMStreamChunk(
                            content="",
                            tool_call_deltas=[{
                                "index": index,
                                "id": getattr(block, "id", "") or "",
                                "type": "function",
                                "function": {
                                    "name": getattr(block, "name", "") or "",
                                    "arguments": arguments,
                                },
                            }],
                        )
                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta and getattr(delta, "type", "") == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield LLMStreamChunk(content=text)
                    elif delta and getattr(delta, "type", "") == "input_json_delta":
                        partial_json = getattr(delta, "partial_json", "") or ""
                        if partial_json:
                            yield LLMStreamChunk(
                                content="",
                                tool_call_deltas=[{
                                    "index": int(getattr(event, "index", 0) or 0),
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": partial_json},
                                }],
                            )
                elif event_type == "message_delta":
                    finish_reason = None
                    delta = getattr(event, "delta", None)
                    if delta:
                        finish_reason = getattr(delta, "stop_reason", None)
                    _merge_anthropic_usage(usage_dict, getattr(delta, "usage", None))
                    _merge_anthropic_usage(usage_dict, getattr(event, "usage", None))
                    if finish_reason:
                        mapped_reason = _anthropic_stop_reason_to_finish_reason(finish_reason)
                        yield LLMStreamChunk(content="", finish_reason=mapped_reason)
                elif event_type == "message_stop":
                    message = getattr(event, "message", None)
                    _merge_anthropic_usage(usage_dict, getattr(message, "usage", None))
        if usage_dict:
            _record_anthropic_token_usage(self.model, usage_dict)
            yield LLMStreamChunk(
                content="",
                usage_metadata=usage_dict,
                response_metadata={"token_usage": usage_dict},
            )

    async def ainvoke_raw(self, messages: Any, **overrides: Any):
        """Return the raw Anthropic Message object."""
        payload = self._build_payload_for_call(messages, overrides)
        resp = await self._aclient.messages.create(**payload)
        _record_anthropic_token_usage(self.model, _anthropic_usage_to_dict(getattr(resp, "usage", None)))
        return resp

    def invoke_raw(self, messages: Any, **overrides: Any):
        """Return the raw Anthropic Message object (sync)."""
        payload = self._build_payload_for_call(messages, overrides)
        resp = self._client.messages.create(**payload)
        _record_anthropic_token_usage(self.model, _anthropic_usage_to_dict(getattr(resp, "usage", None)))
        return resp

    async def aclose(self) -> None:
        await self._aclient.close()
        self._client.close()
        self._client_finalizer.detach()

    def close(self) -> None:
        self._client.close()
        _close_async_openai_client_from_sync_best_effort(self._aclient)
        self._client_finalizer.detach()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()


# ────────────────────────────────────────────────────────────────
# create_chat_llm — factory with automatic provider config
# ────────────────────────────────────────────────────────────────

_SENTINEL = object()


def create_chat_llm(
    model: str,
    base_url: str | None,
    api_key: str | None,
    *,
    temperature: float | None = None,
    streaming: bool = False,
    max_retries: int = 2,
    max_completion_tokens: int | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    extra_body: Any = _SENTINEL,
    model_kwargs: dict | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    provider_type: str | None = None,
    **kw: Any,
) -> "ChatOpenAI | ChatAnthropic":
    """Create a chat client with automatic provider-specific configuration.

    Returns either a :class:`ChatOpenAI` (OpenAI-compatible endpoints) or a
    :class:`ChatAnthropic` (Anthropic Messages API endpoints such as Kimi Code).

    Provider cache headers and extra_body (thinking-disable etc.) are resolved
    automatically from ``config.providers``.  Pass ``extra_body=None`` to
    explicitly skip the auto-resolved extra_body (e.g. when thinking should
    remain enabled).

    Args:
        model: Model name (e.g. "qwen-flash", "gpt-4.1-mini", "kimi-for-coding").
        base_url: Provider API base URL.
        api_key: API key.
        provider_type: Optional provider type hint ("anthropic" selects the
            Anthropic Messages API path).
        extra_body: Override auto-resolved extra_body.  ``_SENTINEL`` (default)
            means "auto-resolve from model name"; ``None`` means "no extra_body".
        **kw: Forwarded to the selected client class.
    """
    from config.providers import get_cache_kwargs, get_extra_body

    is_anthropic = _is_anthropic_endpoint(base_url, provider_type)

    cache_kw = get_cache_kwargs(base_url)

    if extra_body is _SENTINEL:
        resolved = get_extra_body(model)
        extra_body = resolved or None

    if is_anthropic:
        # Kimi Code 要求 User-Agent 为 claude-code/0.1.0，参考 AstrBot / LingChat 实现。
        cache_default_headers = cache_kw.pop("default_headers", None) or {}
        default_headers = dict(cache_default_headers)
        default_headers.update(dict(kw.pop("default_headers", {}) or {}))
        if _is_kimi_code_anthropic_base_url(base_url):
            default_headers.setdefault("User-Agent", "claude-code/0.1.0")
        return ChatAnthropic(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            streaming=streaming,
            max_retries=max_retries,
            max_completion_tokens=max_completion_tokens,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body=extra_body,
            model_kwargs=model_kwargs,
            tools=tools,
            tool_choice=tool_choice,
            default_headers=default_headers or None,
            **cache_kw,
            **kw,
        )

    # Anthropic API 使用 x-api-key 而非 Bearer token，需要注入专用 headers
    _api_key = api_key
    if base_url and "api.anthropic.com" in base_url:
        anthropic_headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
        # 合并 cache_kw / kw / anthropic 的 default_headers，避免重复关键字
        merged_headers = {
            **cache_kw.pop("default_headers", {}),
            **kw.pop("default_headers", {}),
            **anthropic_headers,
        }
        kw["default_headers"] = merged_headers
        # OpenAI SDK 要求 api_key 非空，给占位值（实际鉴权走 x-api-key header）
        _api_key = "anthropic-via-header"

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=_api_key,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
        max_completion_tokens=max_completion_tokens,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_body=extra_body,
        model_kwargs=model_kwargs,
        tools=tools,
        tool_choice=tool_choice,
        **cache_kw,
        **kw,
    )


async def create_chat_llm_async(*args: Any, **kwargs: Any) -> "ChatOpenAI | ChatAnthropic":
    """Create a chat client without blocking the running event loop."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(asyncio.to_thread(create_chat_llm, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(
            lambda done: loop.create_task(_close_cancelled_chat_llm_result(done))
        )
        raise


async def _close_cancelled_chat_llm_result(task: asyncio.Task["ChatOpenAI | ChatAnthropic"]) -> None:
    try:
        llm = task.result()
    except Exception:
        return
    try:
        await llm.aclose()
    except Exception:
        return


# ────────────────────────────────────────────────────────────────
# OpenAIEmbeddings
# ────────────────────────────────────────────────────────────────

class OpenAIEmbeddings:
    """Lightweight OpenAI embeddings client."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "",
        api_key: str | None = None,
        **_kwargs: Any,
    ):
        from openai import AsyncOpenAI, OpenAI

        self.model = model
        _api_key = api_key or "sk-placeholder"
        self._client = OpenAI(base_url=base_url, api_key=_api_key)
        self._aclient = AsyncOpenAI(base_url=base_url, api_key=_api_key)

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]

    async def aembed_query(self, text: str) -> list[float]:
        resp = await self._aclient.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding


# ────────────────────────────────────────────────────────────────
# SQLChatMessageHistory
# ────────────────────────────────────────────────────────────────

class SQLChatMessageHistory:
    """Minimal SQLite message store for memory/timeindex.py.

    Table schema::

        id          INTEGER PRIMARY KEY AUTOINCREMENT
        session_id  TEXT
        message     TEXT   -- JSON-serialized {"type": ..., "data": {"content": ...}}
    """

    _engine_cache: dict = {}

    def __init__(self, connection_string: str, session_id: str, table_name: str = "message_store"):
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine

        self.session_id = session_id
        self.table_name = table_name

        if connection_string not in self.__class__._engine_cache:
            self.__class__._engine_cache[connection_string] = create_engine(connection_string)
        self._engine = self.__class__._engine_cache[connection_string]

        metadata = MetaData()
        self._table = Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("session_id", String),
            Column("message", Text),
        )
        metadata.create_all(self._engine)

    def _serialize(self, message: Any) -> str:
        if isinstance(message, BaseMessage):
            return _json.dumps({"type": message.type, "data": {"content": message.content}}, ensure_ascii=False)
        if isinstance(message, dict):
            return _json.dumps(message, ensure_ascii=False)
        return _json.dumps({"type": "system", "data": {"content": str(message)}}, ensure_ascii=False)

    def add_message(self, message: Any) -> None:
        from sqlalchemy import insert

        with self._engine.connect() as conn:
            conn.execute(
                insert(self._table).values(
                    session_id=self.session_id,
                    message=self._serialize(message),
                )
            )
            conn.commit()

    def add_messages(self, messages: list) -> None:
        from sqlalchemy import insert

        rows = [
            {"session_id": self.session_id, "message": self._serialize(m)}
            for m in messages
        ]
        if rows:
            with self._engine.connect() as conn:
                conn.execute(insert(self._table), rows)
                conn.commit()
