# -- coding: utf-8 --
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


import asyncio
import json
import re
import time
from typing import Optional, Callable, Dict, Any, Awaitable, List
from utils.llm_client import (
    SystemMessage,
    HumanMessage,
    AIMessage,
    LLMStreamChunk,
    ThinkingStreamStripper,
    chat_retry_error_types,
    strip_thinking_segments,
    create_chat_llm,
    create_chat_llm_async,
)
from utils.frontend_utils import calculate_text_similarity
from utils.tokenize import count_tokens, truncate_to_tokens
from config import (
    OMNI_RECENT_RESPONSES_MAX,
    DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
    FOCUS_THINKING_EXTRA_TOKENS,
)
from main_logic.tool_calling import (
    OnToolCallCallback,
    ToolCall,
    ToolDefinition,
    ToolResult,
    parse_arguments_json,
)
from utils.llm_tool_leak_filter import ToolLeakFilter, log_tool_leak_filtered

# 惰性缓存（None = 尚未构建）：openai/anthropic SDK 已从启动 import 链移除（见
# utils/llm_client），模块级立即展开会把两个 SDK 拉回启动关键路径。首次求值发生在
# LLM 调用的异常处理处，彼时 SDK 必已随 client 构造加载。
_LLM_RETRY_ERROR_TYPES: tuple[type[BaseException], ...] | None = None


def _llm_retry_error_types() -> tuple[type[BaseException], ...]:
    global _LLM_RETRY_ERROR_TYPES
    if _LLM_RETRY_ERROR_TYPES is None:
        from openai import AuthenticationError

        _LLM_RETRY_ERROR_TYPES = (
            AuthenticationError,
            *chat_retry_error_types(),
        )
    return _LLM_RETRY_ERROR_TYPES

# google-genai 懒加载。该 SDK import 很重（~0.6s），且在 import 时会捎带 mcp
# （~0.5s），但 offline 路径只有用户用 native-Gemini 端点时才需要它。改成首次使用
# 时再 import，并由 utils.module_warmup 在 ready 后用后台线程提前预热，所以常规
# 启动（OpenAI-compat 端点 / greeting）完全不付这笔钱。
_genai = None
_genai_types = None
_GENAI_AVAILABLE: bool | None = None  # None = 尚未尝试导入


def _ensure_genai() -> bool:
    """Import google-genai on first call and cache the result (success or failure).

    Returns whether the SDK is available. Under a concurrent race the worst case is one
    duplicate import; Python's module cache makes it idempotent with no side effects.
    """
    global _genai, _genai_types, _GENAI_AVAILABLE
    # 显式强制不可用优先级最高（测试用它当强制降级开关）→ 即便对象已塞进全局也降级。
    if _GENAI_AVAILABLE is False:
        return False
    # 对象已就位（真 import 过 / 测试注入了 mock）→ 直接信任，不重导入。
    if _genai is not None and _genai_types is not None:
        _GENAI_AVAILABLE = True
        return True
    try:
        from google import genai as genai_mod
        from google.genai import types as genai_types_mod
        # 只补缺失的，保住测试可能注入的 _genai mock。
        if _genai is None:
            _genai = genai_mod
        if _genai_types is None:
            _genai_types = genai_types_mod
        _GENAI_AVAILABLE = True
    except Exception:  # pragma: no cover — environment-specific
        # 不覆盖外部强制设过的可用性标志；也不清空可能被测试注入的 _genai/_genai_types
        # （只补缺失原则——导入失败时保留已注入的部分 mock）。
        if _GENAI_AVAILABLE is None:
            _GENAI_AVAILABLE = False
    # 只有可用标志为真且对象确实就位才算可用——避免 forced True 但 import 失败时
    # 谎报可用、让调用点在 None 上解引用 _genai_types。
    return bool(_GENAI_AVAILABLE) and _genai is not None and _genai_types is not None


# Hostname / model fragments that indicate the request should go through
# google-genai SDK directly (the OpenAI-compat Gemini endpoint silently
# drops the ``tools`` field, so tools fundamentally do not work there).
# 用户明确说：lanlan.app 国际版走的是 OpenAI-compat Gemini，所以那个 base_url
# 不在这里——它只能走 fallback 路径，工具不可用，已留 TODO。
_GENAI_NATIVE_BASE_URL_HINTS = (
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
)
_GENAI_NATIVE_MODEL_HINTS = ("gemini",)

# Sentence-final terminators used to recover from a length-overflow when
# rerolls have been exhausted. Commas, semicolons, and colons are NOT
# included on purpose — those would leave the kept text mid-thought.
_SENTENCE_END_CHARS = '.!?。！？…'

# Broader pause-causing punctuation used to pick the TTS cutover point in
# the "long-but-readable" summary path. Once ``_total`` crosses
# ``max_response_length`` we look for the first character in this set so
# the part TTS already enqueued ends at a natural breath, not mid-clause.
# Commas / semicolons / colons are intentional here (the summary takes
# over speaking right after).
_SUMMARY_TERMINATOR_CHARS = _SENTENCE_END_CHARS + ',，;；:：'

# Slack after which the summary path stops trying to ABANDON the summary.
# If the model only writes a tiny bit past the budget (less than this many
# tokens), the tail is small enough to just keep reading — no summary,
# no UI/TTS divergence. Larger overshoot → summary kicks in.
_SUMMARY_LATE_FINISH_SLACK = 25

# How often (in tail-token count) to recheck gibberish during summary
# mode. If the tail buffer crosses one of these thresholds we re-run
# ``_is_gibberish_response`` on the accumulated tail; on positive hit we
# abandon the summary path and fall back to the existing gibberish
# discard flow.
_SUMMARY_GIBBERISH_RECHECK_TOKENS = 100

# Hard token cap on the summary LLM output. Defensive backstop: prompt
# asks for "1-2 sentences" but small models drift. After stripping quotes
# and slicing to the first 2 sentence-end-delimited segments, we still
# hard-truncate to this many tokens (tiktoken, same unit as the budget)
# before feeding TTS. Token-based so the cap behaves consistently across
# languages (a char cap punishes CJK, where one char ≈ one token, far
# harder than Latin scripts). 50 tokens keeps the summary in the
# "quick wrap-up" zone regardless of model obedience.
_SUMMARY_HARD_TOKEN_CAP = 50


# Floor on API-side ``max_completion_tokens`` when summary mode is on.
# NOT a hard ceiling — if the caller's ``max_response_length`` already
# exceeds this value, we let API budget grow with it (budget + slack).
# The floor only kicks in to lift the default-300-budget chat case up
# to ~3000 so the model has room to finish its thought before we
# summarize. 3000 tokens is roughly 2000 Chinese chars / 1500 English
# words, well above any "took a long detour" reply we want to recover
# from.
_SUMMARY_API_BUDGET_FLOOR = 3000
_API_KEY_REJECTED_KEYWORDS = (
    "incorrect api key",
    "incorect api key",
    "invalid_api_key",
    "invalid api key",
    "invalid key",
    "api key is invalid",
)
_SAFETY_VIOLATION_KEYWORDS = (
    "safety",
    "content_filter",
    "content filter",
    "policy violation",
    "policy_violation",
    "blocklist",
    "prohibited",
    "prohibited_content",
    "recitation",
    "spii",
    "language",
    "image_safety",
    "image_prohibited_content",
    "image_recitation",
    "responsibleaipolicyviolation",
    "responsible ai policy",
)


def _is_api_key_rejected_error(error: BaseException | str) -> bool:
    """Return True when an upstream error clearly means the API key was rejected."""
    status_code = getattr(error, "status_code", None)
    text = f"{type(error).__name__}: {error}".lower()
    has_api_key_indicator = any(keyword in text for keyword in _API_KEY_REJECTED_KEYWORDS)
    if status_code == 401:
        return True
    if status_code == 403:
        return has_api_key_indicator
    # Fallback: when the error object carries no status_code attribute, fall
    # back to substring matching against the error message text.
    if "401" in text:
        return True
    if "403" in text:
        return has_api_key_indicator
    if has_api_key_indicator:
        return True
    return (
        ("authenticationerror" in text or "authentication" in text or "unauthorized" in text)
        and "api key" in text
    )


def _is_safety_violation_signal(*values: object) -> bool:
    """Return True when provider diagnostics point to safety/policy blocking."""
    text = " ".join(str(value) for value in values if value).lower()
    if not text:
        return False
    return any(keyword in text for keyword in _SAFETY_VIOLATION_KEYWORDS)


def _truncate_to_last_sentence_end(text: str) -> str:
    """Return the prefix of ``text`` up to and including the last
    sentence-terminating punctuation mark. Returns ``""`` if no sentence
    terminator is present (caller should fall through to the
    too-long-and-discarded UX in that case)."""
    last = max((text.rfind(ch) for ch in _SENTENCE_END_CHARS), default=-1)
    if last < 0:
        return ""
    return text[:last + 1]


# Punctuation/symbol density thresholds for the "model went insane" detector
# (`_is_gibberish_response` below). The point of the response-length guard is
# not really "long replies are bad" — it's a circuit breaker for runaway model
# states (BPE-loop repeating a single token, dump-everything mode emitting
# nothing but emojis / punctuation, etc.). Once we know we're in that state we
# don't want to salvage a "sentence" out of it; we want to discard.
_GIBBERISH_MIN_LEN = 30        # Below this we don't bother judging.
_GIBBERISH_PS_RATIO_FLOOR = 0.015  # < 1.5% punct/symbol → BPE-loop / wall-of-chars
_GIBBERISH_PS_RATIO_CEIL = 0.25    # > 25% punct/symbol → emoji/mark spam

# Slack between the conversational length budget and the LLM API's hard
# `max_completion_tokens`. The API cap is the *first* line of defense (let
# the model stop naturally before generating tokens we'd just discard); the
# Python-side guard kicks in only on overshoot, where it can decide
# truncate vs. gibberish-filter. We need *some* overshoot for the fence
# to actually fire — if API caps exactly at the budget the model stops
# right at the edge with a half-sentence and we can't tell apart "ran
# long" from "naturally finished at the cap". 20 tokens is enough to
# matter without bloating cost.
_MAX_TOKENS_SLACK = 20
_UNLIMITED_BUDGET = 999999  # sentinel set when user picks the slider's "无限制"

# 主动搭话遗留截图的存活上限：暂存后超过这个时长就视为过期、不再注入用户回复
# （屏幕早变了，旧图反而误导模型）。在注入点惰性判定，无需后台定时器。
_PROACTIVE_SCREENSHOT_TTL_SECONDS = 60.0


def _budget_to_max_tokens(budget: int, summary_mode: bool = False) -> int | None:
    """Convert ``max_response_length`` budget into the LLM API's
    ``max_completion_tokens``. ``None`` for the unlimited sentinel so the
    request omits the field entirely (large fixed values get rejected as
    out-of-range by some providers).

    When ``summary_mode`` is True the API-side ceiling is lifted to at
    least ``_SUMMARY_API_BUDGET_FLOOR`` (or the caller's budget+slack if
    that is larger — never CAPS to the floor, just raises the small
    defaults). The Python-side guard then decides per-response whether
    to abandon, summarize, or pass through the overshoot.
    """
    if budget >= _UNLIMITED_BUDGET:
        return None
    if summary_mode:
        return max(budget + _MAX_TOKENS_SLACK, _SUMMARY_API_BUDGET_FLOOR)
    return budget + _MAX_TOKENS_SLACK


def _find_summary_terminator(text: str) -> int:
    """Return the offset of the FIRST pause-causing punctuation char in
    ``text`` (one of ``_SUMMARY_TERMINATOR_CHARS``), or ``-1`` if none.

    Used in summary-mode cutover: once the response crosses the budget
    we want TTS to stop at the next natural breath rather than mid-word.
    Caller treats ``offset + 1`` as the inclusive boundary.
    """
    best = -1
    for ch in _SUMMARY_TERMINATOR_CHARS:
        pos = text.find(ch)
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
    return best


def _is_gibberish_response(text: str) -> bool:
    """Heuristic: is ``text`` a runaway / gibberish model output?

    Based on the density of Unicode punctuation (Pc/Pd/Pe/Pf/Pi/Po/Ps) plus
    symbols (Sc/Sk/Sm/So — i.e. emoji, math marks, kaomoji components):

    - density < 1.5% → almost certainly a tight repetition loop (a single
      character or short n-gram repeated past the token cap), no real
      sentences to recover.
    - density > 25% → almost certainly an emoji / kaomoji / mark spam mode.

    Either way the right thing to do is filter the response entirely (let
    `handle_response_discarded` show the locale "fault" placeholder and write
    that placeholder — not the gibberish — into history) rather than try to
    cut a sentence out of garbage. Short responses (< 30 chars) skip the
    judgement; the guard only fires after we've blown past the token cap, so
    in practice ``text`` is always long here.
    """
    import unicodedata
    n = len(text)
    if n < _GIBBERISH_MIN_LEN:
        return False
    n_marks = sum(
        1 for c in text
        if unicodedata.category(c)[0] in ("P", "S")
    )
    ratio = n_marks / n
    return ratio < _GIBBERISH_PS_RATIO_FLOOR or ratio > _GIBBERISH_PS_RATIO_CEIL
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

_NONVERBAL_DIRECTIVE_PATTERN = re.compile(r"\[play_music:[^\]]*(?:\]|$)", re.IGNORECASE)


def _strip_nonverbal_directives(text: str) -> str:
    if not text:
        return ""
    return _NONVERBAL_DIRECTIVE_PATTERN.sub("", text)


class _GenaiToolsUnsupported(Exception):
    """Raised by the genai SDK path when tool support is unavailable
    (SDK missing, model rejected, etc.) so the caller can fall back to
    the OpenAI-compat path with tools silently disabled."""


def _genai_messages_to_contents(
    messages: list,
) -> tuple[Optional[str], list]:
    """Translate this client's ``_conversation_history`` into the
    ``(system_instruction, contents)`` tuple expected by google-genai
    ``generate_content_stream``.

    - SystemMessage → goes to ``system_instruction`` (genai keeps it
      out of ``contents``; first-system-message wins).
    - HumanMessage / AIMessage / dicts (assistant w/ tool_calls, tool
      role) → ``Content`` entries.

    Plain dicts with role=assistant + tool_calls are translated to
    ``Content(role="model", parts=[Part(function_call=...)])``; role=tool
    becomes ``Content(role="user", parts=[Part(function_response=...)])``.
    """
    if not _ensure_genai():
        raise _GenaiToolsUnsupported("google-genai SDK not importable")
    types = _genai_types
    system_instruction: Optional[str] = None
    contents: list = []

    for msg in messages:
        # ---- BaseMessage objects (existing path) --------------------
        if isinstance(msg, SystemMessage):
            if system_instruction is None:
                system_instruction = msg.content if isinstance(msg.content, str) else str(msg.content)
            else:
                system_instruction += "\n" + (msg.content if isinstance(msg.content, str) else str(msg.content))
            continue
        if isinstance(msg, HumanMessage):
            parts = _genai_parts_from_content(msg.content)
            contents.append(types.Content(role="user", parts=parts))
            continue
        if isinstance(msg, AIMessage):
            parts = _genai_parts_from_content(msg.content)
            contents.append(types.Content(role="model", parts=parts))
            continue
        # ---- Plain dict path (tool-calling history) -----------------
        if isinstance(msg, dict):
            role = msg.get("role")
            if role == "system":
                txt = msg.get("content", "")
                if isinstance(txt, str):
                    system_instruction = (system_instruction + "\n" + txt) if system_instruction else txt
                continue
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    # 同 turn text + function_call 并存的场景：``content``
                    # 是 _astream_genai_with_tools 写进来的 streamed_text_buffer，
                    # 表示模型在调工具前已经先吐给用户的话。这条 text 必须
                    # 跟 function_call parts 一起回放给 Gemini，否则下一轮
                    # generate_content_stream 看到的历史依然缺前半句，模型
                    # 还是会重复 / 改口（这正是上一条修复的对偶点）。
                    parts = []
                    text_content = msg.get("content")
                    if isinstance(text_content, str) and text_content.strip():
                        parts.append(types.Part(text=text_content))
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        try:
                            args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
                        except json.JSONDecodeError:
                            args = {"_raw": fn.get("arguments") or ""}
                        parts.append(types.Part(function_call=types.FunctionCall(
                            id=tc.get("id") or "",
                            name=fn.get("name") or "",
                            args=args,
                        )))
                    contents.append(types.Content(role="model", parts=parts))
                else:
                    parts = _genai_parts_from_content(msg.get("content", ""))
                    contents.append(types.Content(role="model", parts=parts))
                continue
            if role == "tool":
                # Best-effort: parse the JSON content back into a dict for
                # ``response`` since genai expects a structured response.
                raw_out = msg.get("content", "")
                try:
                    parsed = json.loads(raw_out) if isinstance(raw_out, str) else raw_out
                except json.JSONDecodeError:
                    parsed = {"result": raw_out}
                if not isinstance(parsed, dict):
                    parsed = {"result": parsed}
                # Gemini FunctionResponse.name 必须与原 function_call.name 完全
                # 一致，否则 server 把这条 tool 结果当成无主消息丢弃。
                # 现在 ``_execute_and_append_openai_tool_calls`` 已写入 ``name``，
                # 但历史里若有旧条目（或外部传入的 messages）没带 name，仍要
                # 反查前面 assistant 的 tool_calls 找匹配 tool_call_id。绝不能
                # fallback 到 tool_call_id 自身——那是 "call_xxx" 格式不是函数名。
                fn_name = msg.get("name") or ""
                if not fn_name:
                    tcid = msg.get("tool_call_id") or ""
                    if tcid:
                        # 反向扫前面的 assistant tool_calls
                        for prev in reversed(messages[: messages.index(msg)] if msg in messages else []):
                            prev_calls = (prev.get("tool_calls") or []) if isinstance(prev, dict) else []
                            for tc in prev_calls:
                                if tc.get("id") == tcid:
                                    fn_name = (tc.get("function") or {}).get("name") or ""
                                    break
                            if fn_name:
                                break
                if not fn_name:
                    # 实在找不到 —— 一个不匹配原 function_call.name 的占位
                    # （比如 "unknown_tool"）只会让 Gemini 拿到一个永远找不到
                    # 对应 function_call 的孤儿 tool result，效果跟不发一样
                    # 还要白费一轮 token。直接跳过这条 malformed message。
                    logger.warning(
                        "genai message conversion: dropping tool message with no "
                        "resolvable function name, tool_call_id=%s",
                        msg.get("tool_call_id"),
                    )
                    continue
                contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=fn_name, response=parsed)
                ]))
                continue
            if role == "user":
                parts = _genai_parts_from_content(msg.get("content", ""))
                contents.append(types.Content(role="user", parts=parts))
                continue
        # Fallback: stringify
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=str(getattr(msg, "content", msg)))],
        ))
    return system_instruction, contents


def _genai_parts_from_content(content: Any) -> list:
    """Render a ``BaseMessage.content`` value as a list of
    ``types.Part``. Strings become ``Part(text=...)``; multimodal lists
    (the ``[{type:image_url, image_url:{url:"data:image/jpeg;base64,..."}}, {type:text, text:...}]``
    shape that ``stream_text`` builds for vision) become a mix of
    ``inline_data`` parts and ``text`` parts."""
    types = _genai_types
    if isinstance(content, str):
        return [types.Part(text=content)]
    if isinstance(content, list):
        parts: list = []
        for entry in content:
            if not isinstance(entry, dict):
                parts.append(types.Part(text=str(entry)))
                continue
            etype = entry.get("type")
            if etype == "text":
                parts.append(types.Part(text=entry.get("text") or ""))
            elif etype == "image_url":
                url = (entry.get("image_url") or {}).get("url") or ""
                if url.startswith("data:image/"):
                    try:
                        header, b64 = url.split(",", 1)
                        mime = header.split(";")[0].split(":", 1)[-1] or "image/jpeg"
                        import base64 as _b64
                        parts.append(types.Part.from_bytes(
                            data=_b64.b64decode(b64), mime_type=mime,
                        ))
                    except Exception:
                        parts.append(types.Part(text=f"[image dropped: {entry.get('image_url')}]"))
                else:
                    parts.append(types.Part(text=f"[image url unsupported: {url}]"))
            else:
                parts.append(types.Part(text=json.dumps(entry, ensure_ascii=False)))
        return parts or [types.Part(text="")]
    return [types.Part(text=str(content))]


def _should_use_genai_sdk(model: str, base_url: str | None) -> bool:
    """Decide whether to route this Gemini-flavoured offline call through
    the native google-genai SDK (which supports tool calling) instead of
    the OpenAI-compat endpoint (which silently drops ``tools``).

    Returns True only when:
      1. ``google-genai`` is importable in the running env, AND
      2. base_url points at Google's native Gemini endpoint OR
         the model name contains "gemini" AND base_url is empty/None
         (i.e. caller wants direct genai with no proxy).

    Explicitly excluded: lanlan.app's international free proxy uses
    Gemini under the hood but exposes only the OpenAI-compat surface, so
    its base_url ('lanlan.app') stays on the OpenAI path. Tools won't
    work there until the proxy is upgraded — see TODO in core.py.
    """
    # 先做便宜的字符串判断：只有路由确实指向 native Gemini 时才去 import SDK。
    # 这样常规 OpenAI-compat 端点（含 greeting）构造 client 时不会触发 genai
    # 这条重 import；而真要走 genai 的用户，本来下一步就得用到它。
    bl = (base_url or "").lower()
    ml = (model or "").lower()
    native = (
        any(h in bl for h in _GENAI_NATIVE_BASE_URL_HINTS)
        or (not bl and any(h in ml for h in _GENAI_NATIVE_MODEL_HINTS))
    )
    if not native:
        return False
    # 路由判断只需"是否可用"这个布尔；尊重已知/被强制的标志（测试会 force
    # _GENAI_AVAILABLE 而不装 google-genai），只有真未知时才去付 lazy import。
    if _GENAI_AVAILABLE is None:
        return _ensure_genai()
    return bool(_GENAI_AVAILABLE)

class OmniOfflineClient:
    """
    A client for text-based chat that mimics the interface of OmniRealtimeClient.
    
    This class provides a compatible interface with OmniRealtimeClient but uses
    ChatOpenAI with OpenAI-compatible API instead of realtime WebSocket,
    suitable for text-only conversations.
    
    Attributes:
        base_url (str):
            The base URL for the OpenAI-compatible API (e.g., OPENROUTER_URL).
        api_key (str):
            The API key for authentication.
        model (str):
            Model to use for chat.
        vision_model (str):
            Model to use for vision tasks.
        vision_base_url (str):
            Optional separate base URL for vision model API.
        vision_api_key (str):
            Optional separate API key for vision model.
        llm (ChatOpenAI):
            ChatOpenAI client for streaming text generation.
        on_text_delta (Callable[[str, bool], Awaitable[None]]):
            Callback for text delta events.
        on_input_transcript (Callable[[str], Awaitable[None]]):
            Callback for input transcript events (user messages).
        on_output_transcript (Callable[[str, bool], Awaitable[None]]):
            Callback for output transcript events (assistant messages).
        on_connection_error (Callable[[str], Awaitable[None]]):
            Callback for connection errors.
        on_response_done (Callable[[], Awaitable[None]]):
            Callback when a response is complete.
    """
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        vision_model: str = "",
        vision_base_url: str = "",  # 独立的视觉模型 API URL
        vision_api_key: str = "",   # 独立的视觉模型 API Key
        provider_type: str | None = None,
        vision_provider_type: str | None = None,
        voice: str = "",  # Unused for text mode but kept for compatibility
        turn_detection_mode = None,  # Unused for text mode
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_thinking_active: Optional[Callable[[bool], Awaitable[None]]] = None,
        on_audio_delta: Optional[Callable[[bytes], Awaitable[None]]] = None,  # Unused
        on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,  # Unused
        on_input_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_output_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_connection_error: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_repetition_detected: Optional[Callable[[], Awaitable[None]]] = None,
        on_response_discarded: Optional[Callable[[str, int, int, bool, Optional[str]], Awaitable[None]]] = None,
        on_status_message: Optional[Callable[[str], Awaitable[None]]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        max_response_length: Optional[int] = None,
        lanlan_name: str = "",
        master_name: str = "",
        on_tool_call: Optional[OnToolCallCallback] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        max_tool_iterations: int = 3,
        enable_long_response_summary: bool = False,
    ):
        # Use base_url directly without conversion
        self.base_url = base_url
        self.api_key = api_key if api_key and api_key != '' else None
        self.model = model
        self.vision_model = vision_model  # Store vision model for temporary switching
        # 视觉模型独立配置（如果未指定则回退到主配置）
        self.vision_base_url = vision_base_url if vision_base_url else base_url
        self.vision_api_key = vision_api_key if vision_api_key else api_key
        self.provider_type = provider_type
        self.vision_provider_type = vision_provider_type or provider_type
        self._model_switch_lock = asyncio.Lock()
        self.on_text_delta = on_text_delta
        # Called with True the first time a stream emits a reasoning / thinking
        # chunk (the text itself is filtered out before it reaches text/TTS —
        # only the "is thinking" boolean is surfaced), and with False to clear
        # when that stream ends without an external unconditional clear (see
        # _notify_reasoning_done). Drives the chat thinking-dots bubble for ANY
        # turn that actually reasons, decoupled from Focus mode.
        self.on_thinking_active = on_thinking_active
        # Reasoning-pulse ownership. A proactive prompt_ephemeral turn can
        # interleave with a user stream_text on this SAME client (core drops stale
        # proactive chunks via the expected-sid guard rather than awaiting the
        # prompt). Ownership is tracked by a single source of truth — NOT a shared
        # boolean, which _begin_reasoning_stream would reset out from under an
        # older still-running stream (Codex P2):
        #   _reasoning_stream_seq      — monotonic counter, bumped per stream entry
        #                                (stream_text / prompt_ephemeral) so each
        #                                stream has a distinct token.
        #   _reasoning_active_pulse_seq — the seq that currently owns an un-cleared
        #                                True pulse (None = bubble not lit by us).
        #                                A pulse stamps it; a clear only fires for
        #                                the owning seq. Because it is NOT reset on
        #                                stream entry, a preempted proactive turn
        #                                still clears its OWN pulse, yet cannot
        #                                clear a newer stream that already re-pulsed.
        self._reasoning_stream_seq = 0
        self._reasoning_active_pulse_seq: Optional[int] = None
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.handle_connection_error = on_connection_error
        self.on_status_message = on_status_message
        self.on_response_done = on_response_done
        self.on_proactive_done: Optional[Callable[[bool], Awaitable[None]]] = None
        self.on_repetition_detected = on_repetition_detected
        self.on_response_discarded = on_response_discarded
        
        # 普通对话守卫配置（先决定 max_response_length，create_chat_llm
        # 用得到 _budget_to_max_tokens(self.max_response_length)）。
        # 0 / 负数 在 update_max_response_length 路径里被解释成"无限制"
        # （= _UNLIMITED_BUDGET）；__init__ 必须用同样的语义，否则首轮
        # 持久化配置直接读到 0 时会先按 300+20 cap 创建 LLM，直到用户再
        # 改一次滑块才恢复 unlimited。
        self.enable_response_guard = True
        if not isinstance(max_response_length, int):
            self.max_response_length = 300
        elif max_response_length > 0:
            self.max_response_length = max_response_length
        else:
            self.max_response_length = _UNLIMITED_BUDGET
        # 最多允许的自动重 roll 次数：1 次 reroll → 总共 2 次尝试。
        # 第 2 次仍超长时不再丢弃整段，而是回退到最后一个句末标点截断。
        self.max_response_rerolls = 1

        # 长回复 summary 路径开关：开 → 长但可读的回复不再 inline abort+truncate，
        # 而是让模型继续写到 budget+slack（最少 _SUMMARY_API_BUDGET_FLOOR），TTS
        # 在 budget 后的下个 terminator 停掉，emotion-tier 小模型用人设口吻把
        # 尾巴压成一两句话续到 TTS。前端 live 看完整原文、history 存 prefix+summary，
        # 刻意的语义分叉。默认 False（保留 game 这种 max=100 短台词的现行 abort
        # 行为），core 在创建 chat client 时显式打开。
        self.enable_long_response_summary = bool(enable_long_response_summary)

        # Initialize ChatOpenAI client. max_completion_tokens 设为
        # max_response_length + 20 让 LLM API 自然在 budget+20 token 处停下来，
        # 既省掉无效生成成本，又给 fence 留 20 token slack 看到 overshoot
        # 能区分 truncate / gibberish-filter 路径。
        # ⚠️ 这里**永远**用普通 budget，不烤进 summary 的 3000 floor —— 因为
        # 同一个 self.llm 既给 stream_text 又给 prompt_ephemeral（proactive）用，
        # 把 3000 烤进 client 会让没有长度 guard 的 proactive 轮次也能吐到 3000
        # token。summary 的 budget 抬升改成 stream_text 内临时 bump + finally
        # 还原（见 stream_text 顶部），把 3000 严格限定在长回复流式路径里。
        self.llm = create_chat_llm(
            self.model, self.base_url, self.api_key,
            streaming=True, max_retries=0,
            max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
            timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard; generous so normal/long replies aren't truncated
            provider_type=self.provider_type,
        )

        # ── Tool calling state ────────────────────────────────────────
        # ``tool_definitions`` is the canonical list (ToolDefinition objects);
        # the wire-format snapshots are rebuilt from it on each request so
        # callers can mutate the list (register/unregister) between turns.
        self.on_tool_call: Optional[OnToolCallCallback] = on_tool_call
        self._tool_definitions: List[ToolDefinition] = list(tool_definitions or [])
        self.max_tool_iterations = max(1, int(max_tool_iterations))
        self._use_genai_sdk = _should_use_genai_sdk(self.model, self.base_url)
        self._genai_client = None  # initialized lazily inside _stream_text_genai
        self._genai_tools_unsupported = False  # set True if genai path falls back at runtime
        
        # State management
        self._is_responding = False
        self._conversation_history = []
        self._instructions = ""
        self._stream_task = None
        self._pending_images = []  # Store pending images to send with next text
        # 主动搭话以「屏幕」为素材投递后遗留的那张截图，待下一条用户 text 回复
        # 时作为前导视觉背景注入（让对话模型「看到」刚才搭话评论的屏幕）。刻意
        # 与 _pending_images（用户自己的下一帧）隔离：共用会偷走用户的待发帧，
        # 见 core.py proactive media 注释（Codex P2）。单张、一次性消费、带 TTL
        # （_proactive_image_staged_at = 暂存时刻的 monotonic 秒，0.0 = 无暂存）。
        # _proactive_image_history_len = 暂存那一刻的历史长度：截图只对「紧接它的
        # 下一条用户回复」有效，若中途又来了别的 AI 轮（greeting / agent 回调走
        # prompt_ephemeral，不过 finish_proactive_delivery）使历史变长，这张就过时
        # 了、注入时丢弃——把截图钉死在「最后一条 AI 轮」上（Codex P2）。
        self._proactive_image_to_inject = None
        self._proactive_image_staged_at = 0.0
        self._proactive_image_history_len = 0

        # ── Empty-completion 诊断（finish_reason / prompt_tokens / block_reason）──
        # Gemini 在 SAFETY / RECITATION / MAX_TOKENS 等场景会返回 finish_reason
        # 非 stop 但 content 为空；走 OpenAI-compat 代理时 HTTP 仍是 200，没异常，
        # 上层只能看到"流跑完了，0 个文本 token"。这里记最后一次 attempt 在 LLM
        # 这一层看到的 finish_reason / block_reason / prompt_tokens，让 stream_text
        # 的 "所有重试均未产生文本回复" / prompt_ephemeral 的 delivered=False 兜底
        # 警告能把"为什么 empty"原样吐出来。
        self._last_finish_reason: Optional[str] = None
        self._last_block_reason: Optional[str] = None
        self._last_prompt_tokens: Optional[int] = None
        
        # 重复度检测
        self._recent_responses = []  # 存储最近3轮助手回复
        self._repetition_threshold = 0.8  # 相似度阈值
        self._max_recent_responses = OMNI_RECENT_RESPONSES_MAX  # 最多存储的回复数
        
        # ========== 输出前缀检测 ==========
        self.lanlan_name = lanlan_name
        self.master_name = master_name
        self._prefix_buffer_size = max(len(lanlan_name), len(master_name)) + 3 if (lanlan_name or master_name) else 0

        # 质量守卫回调：由 core.py 设置，用于通知前端清理气泡
        # （max_response_length / max_response_rerolls / enable_response_guard
        # 已经在创建 self.llm 之前初始化，因为 _budget_to_max_tokens 用得到。）

    # ------------------------------------------------------------------
    # Tool calling configuration
    # ------------------------------------------------------------------

    def set_tools(self, tool_definitions: Optional[List[ToolDefinition]]) -> None:
        """Replace the active tool list. Takes effect on the next
        ``stream_text`` / ``prompt_ephemeral`` call. Pass ``None`` or
        ``[]`` to disable tools entirely.

        ⚠️ Also clears ``_genai_tools_unsupported``: once that flag is
        flipped to ``True`` because the old tool set triggered a
        ``GenerateContentConfig rejected`` / similar unsupported exception,
        the rest of the session would never try the native genai path
        again. Since the caller has swapped the tool list (typical case:
        hot-unloading a tool with a broken schema), the genai path deserves
        a fresh chance — otherwise it could only recover at the next
        ``connect()`` / ``switch_model()`` reset.
        """
        self._tool_definitions = list(tool_definitions or [])
        self._genai_tools_unsupported = False

    def set_tool_call_handler(self, handler: Optional[OnToolCallCallback]) -> None:
        """Plug in (or replace) the callback that executes tool calls."""
        self.on_tool_call = handler

    def has_tools(self) -> bool:
        return bool(self._tool_definitions) and self.on_tool_call is not None

    def _openai_tools_payload(self) -> Optional[List[dict]]:
        """OpenAI Chat Completions ``tools`` param — nested under
        ``function``. Returns ``None`` when the caller hasn't enabled
        tools, so ``_params`` skips both ``tools`` and ``tool_choice``."""
        if not self.has_tools():
            return None
        return [t.to_openai_chat() for t in self._tool_definitions]

    async def _execute_and_append_openai_tool_calls(
        self,
        messages,
        calls,
        assistant_text: str = "",
        assistant_reasoning: str = "",
    ) -> None:
        """Run each tool call through ``on_tool_call`` and mutate
        ``messages`` in place: append one assistant turn announcing all
        tool calls, then one tool-role message per call carrying the
        result JSON. Both shapes follow the OpenAI Chat Completions spec
        so the next astream invocation sees a valid history.

        ``assistant_text`` is written into the assistant turn's ``content``.
        The OpenAI Chat Completions protocol allows a turn to carry both
        ``content`` and ``tool_calls``, and some OpenAI-compat providers
        "emit text first, then enter tool_calls". Like the Gemini path's
        streamed_text_buffer, this text must be written into the history
        too, otherwise the next turn's context loses the prefix and the
        model repeats itself / backtracks.

        ``assistant_reasoning`` is the thinking model's reasoning chain for
        this turn (``reasoning_content``). Endpoints like DeepSeek-R /
        Qwen / GLM thinking require the ``reasoning_content`` of the
        assistant message that initiated the tool_calls to be passed back
        verbatim in multi-turn tool calling, otherwise the next turn fails
        with 400 "The `reasoning_content` in the thinking mode must be
        passed back to the API.". Non-thinking endpoints always leave it
        empty, in which case the field is omitted to avoid polluting
        normal conversations.
        """
        # 防御性过滤：``ChatOpenAI.collect_tool_calls`` 已会丢弃空 name 槽位，
        # 但万一调用方直接构造（或上游聚合实现替换），这里再兜一层 ——
        # tool_calls 历史中混入空 name 会被下一轮 server schema reject，
        # 整条会话连带挂掉。
        calls = [c for c in calls if (getattr(c, "name", "") or "").strip()]
        if not calls:
            return
        assistant_turn = {
            "role": "assistant",
            "content": assistant_text or "",
            "tool_calls": [
                {
                    "id": c.id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": c.arguments or "{}",
                    },
                }
                for i, c in enumerate(calls)
            ],
        }
        if assistant_reasoning:
            assistant_turn["reasoning_content"] = assistant_reasoning
        messages.append(assistant_turn)
        for i, c in enumerate(calls):
            tool_call = ToolCall(
                name=c.name,
                arguments=parse_arguments_json(c.arguments),
                call_id=c.id or f"call_{i}",
                raw_arguments=c.arguments or "",
            )
            handler = self.on_tool_call
            if handler is None:
                # No handler — surface a structured error back so the
                # model can apologize / abort gracefully.
                result = ToolResult(
                    call_id=tool_call.call_id, name=tool_call.name,
                    output={"error": "no on_tool_call handler bound"},
                    is_error=True, error_message="no on_tool_call handler bound",
                )
            else:
                try:
                    result = await handler(tool_call)
                except Exception as e:
                    logger.exception("OmniOfflineClient: on_tool_call '%s' raised", c.name)
                    result = ToolResult(
                        call_id=tool_call.call_id, name=tool_call.name,
                        output={"error": f"{type(e).__name__}: {e}"},
                        is_error=True, error_message=str(e),
                    )
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.call_id,
                # 写入 ``name`` 让 Gemini 路径能直接用（FunctionResponse.name
                # 必须与原 function_call name 完全一致）。OpenAI-compat 不需要
                # 这个字段也不会因此报错——它只用 tool_call_id 关联。
                "name": tool_call.name,
                "content": result.output_as_json_string(),
            })

    async def _notify_reasoning_active(self) -> None:
        """Tell the host that the model is emitting reasoning / thinking chunks, so
        the chat can show a thinking-dots bubble even on a non-Focus turn whose
        provider reasons internally. The reasoning TEXT is still filtered out at the
        call site — only this boolean pulse escapes. Pulses once per stream: it
        records the current stream's seq as the pulse owner and no-ops while that
        same seq still owns the pulse, so the three filter points can call it
        blindly. Best-effort: a callback failure must never disturb the stream.

        getattr defaults guard ``__new__`` test stubs that bypass ``__init__``."""
        cur = getattr(self, "_reasoning_stream_seq", 0)
        if getattr(self, "_reasoning_active_pulse_seq", None) == cur:
            return  # already pulsed for THIS stream
        self._reasoning_active_pulse_seq = cur
        cb = getattr(self, "on_thinking_active", None)
        if cb is None:
            return
        try:
            await cb(True)
        except Exception as e:
            logger.debug("on_thinking_active(True) callback failed (ignored): %s", e)

    def _begin_reasoning_stream(self) -> int:
        """Open a new reasoning-pulse scope for one stream and return its ownership
        token. Bumps the seq so this stream's first reasoning chunk re-pulses and
        so an older interleaving stream's clear can't fire for this scope. Crucially
        does NOT touch ``_reasoning_active_pulse_seq`` — that single source of truth
        stays owned by whoever last lit the bubble, so a preempted older stream can
        still clear its own pulse (Codex P2). Called at the top of both stream entry
        points (stream_text and prompt_ephemeral)."""
        self._reasoning_stream_seq = getattr(self, "_reasoning_stream_seq", 0) + 1
        return self._reasoning_stream_seq

    async def _notify_reasoning_done(self, owner_seq: Optional[int] = None) -> None:
        """Symmetric clear for ``_notify_reasoning_active``: push the bubble back to
        False when THIS stream still owns the active pulse. Required for callers
        without an external unconditional clear — ``prompt_ephemeral``'s proactive /
        greeting / avatar turns clear the bubble only when a visible token reaches
        ``send_lanlan_response``; a turn that reasons but commits no text (safety /
        empty / tool-only) would otherwise leave the bubble stuck on (Codex P2).
        ``stream_text``'s Focus path is cleared by core's own unconditional finally
        instead (it must also clear the Focus pre-pulse, which fires with no
        reasoning chunk), so this is wired into ``prompt_ephemeral``'s finally only.

        ``owner_seq`` is the token from this stream's ``_begin_reasoning_stream``.
        The clear fires only when ``_reasoning_active_pulse_seq`` still equals it:
          - a NEWER stream that already re-pulsed took ownership (seq differs) → we
            must NOT clear the bubble it is reasoning under;
          - but if the newer stream merely STARTED (bumped seq) without pulsing yet,
            ownership is still ours, so we correctly clear our own pulse rather than
            leaking it (the bug a shared per-stream boolean would have caused).
        Idempotent; getattr defaults guard ``__new__`` test stubs."""
        active = getattr(self, "_reasoning_active_pulse_seq", None)
        if active is None:
            return
        if owner_seq is not None and active != owner_seq:
            return
        self._reasoning_active_pulse_seq = None
        cb = getattr(self, "on_thinking_active", None)
        if cb is None:
            return
        try:
            await cb(False)
        except Exception as e:
            logger.debug("on_thinking_active(False) clear failed (ignored): %s", e)

    async def _astream_with_tools(self, messages, **overrides):
        """Polymorphic streaming entry point. Yields ``LLMStreamChunk``
        objects (text + finish_reason); tool calls are intercepted and
        executed transparently — caller never sees ``tool_call_deltas``.

        Routing:
        - Native Gemini (``_use_genai_sdk``): dispatches to
          ``_astream_genai_with_tools`` and on tools-related failures sets
          ``_genai_tools_unsupported`` so subsequent calls degrade to the
          OpenAI-compat path (where tools won't work — that's the
          documented lanlan.app/free trade-off).
        - Otherwise: ``_astream_openai_with_tools``.
        """
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        if self._use_genai_sdk and not self._genai_tools_unsupported:
            # 跟踪本轮 Gemini 路径是否已经把 text chunk yield 给上游。如果
            # 已经吐过文本，再 fallback 到 OpenAI-compat 会让用户在同一轮
            # 看到"半截 Gemini 文本 + 一份 OpenAI 重新生成的文本"拼接，
            # 必须把异常向上 raise，让 stream_text 的 retry/discard 流程
            # 触发"清空气泡 + 通知 response_discarded"的标准处理。
            genai_emitted_text = False
            try:
                async for chunk in self._astream_genai_with_tools(
                    messages,
                    _tool_leak_filter=tool_leak_filter,
                    _tool_leak_provider=tool_leak_provider,
                    **overrides,
                ):
                    if getattr(chunk, "content", None):
                        genai_emitted_text = True
                    yield chunk
                return
            except _GenaiToolsUnsupported as e:
                logger.warning(
                    "genai SDK declined tools (%s) — falling back to OpenAI-compat (tools disabled)",
                    e,
                )
                self._genai_tools_unsupported = True
                if genai_emitted_text:
                    # 已吐文本：保留永久禁用旗标，但本轮不静默拼接，
                    # 让上游 retry 路径基于 attempt+1 重新走（下次会直接
                    # 进 OpenAI-compat，因为 _genai_tools_unsupported=True）。
                    raise
                if tool_leak_filter is not None:
                    tool_leak_filter.reset()
            except Exception as e:
                # Don't break user requests on transient genai SDK errors —
                # log loudly and fall through. ``_genai_tools_unsupported``
                # stays False so the next turn retries genai (transient
                # 5xx / 429 shouldn't permanently downgrade).
                logger.error("genai SDK path errored, falling back this turn: %s", e)
                if genai_emitted_text:
                    # 同上：已吐过文本不能再静默 fallback，向上 raise 让 retry
                    # 流程清空气泡后基于 attempt+1 重试（下一次仍会先尝试
                    # genai，因为 transient 不翻 _genai_tools_unsupported）。
                    raise
                if tool_leak_filter is not None:
                    tool_leak_filter.reset()
        async for chunk in self._astream_openai_with_tools(
            messages,
            _tool_leak_filter=tool_leak_filter,
            _tool_leak_provider=tool_leak_provider,
            **overrides,
        ):
            yield chunk

    async def _astream_visible_with_tools(self, messages, **overrides):
        tool_names = {
            tool.name for tool in getattr(self, "_tool_definitions", [])
            if getattr(tool, "name", None)
        }
        leak_filter = ToolLeakFilter(tool_names=tool_names)
        provider = getattr(self, "base_url", None) or getattr(self, "model", None)

        def _finalize_filter_chunk():
            visible, event = leak_filter.finalize()
            if event:
                log_tool_leak_filtered(event, provider=provider)
            if not visible:
                return None
            chunk = LLMStreamChunk(content=visible)
            setattr(chunk, "_tool_leak_filtered", True)
            return chunk

        try:
            async for chunk in self._astream_with_tools(
                messages, _tool_leak_filter=leak_filter, _tool_leak_provider=provider, **overrides
            ):
                if getattr(chunk, "_tool_leak_filtered", False):
                    yield chunk
                    continue
                content = getattr(chunk, "content", None)
                if content:
                    chunk.content = self._filter_tool_leak_content(content, leak_filter, provider=provider)
                    setattr(chunk, "_tool_leak_filtered", True)
                yield chunk
        except Exception:
            chunk = _finalize_filter_chunk()
            if chunk is not None:
                yield chunk
            raise

        chunk = _finalize_filter_chunk()
        if chunk is not None:
            yield chunk

    def _filter_tool_leak_content(
        self,
        content: str,
        leak_filter: ToolLeakFilter,
        *,
        provider: str | None = None,
    ) -> str:
        visible, event = leak_filter.feed(content)
        if event:
            log_tool_leak_filtered(event, provider=provider)
        return visible

    async def _astream_openai_with_tools(self, messages, **overrides):
        """OpenAI Chat Completions tool loop. Streams text chunks; on
        ``finish_reason == "tool_calls"`` runs the tools, appends the
        results to ``messages``, and re-invokes — up to
        ``self.max_tool_iterations`` total LLM calls."""
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        tools_payload = self._openai_tools_payload()
        if tools_payload:
            overrides.setdefault("tools", tools_payload)
        else:
            # Belt-and-suspenders: never leak tool_choice without tools.
            overrides.pop("tool_choice", None)
            overrides.pop("tools", None)

        for tool_iter in range(self.max_tool_iterations):
            deltas_per_chunk: list = []
            finish_reason: Optional[str] = None
            # 累积本轮已 yield 给上游的 text，下面 finish_reason=tool_calls
            # 时一起写进 assistant 历史。OpenAI Chat Completions 协议允许同
            # 一 turn 既有 content 又有 tool_calls；某些兼容 provider 真会
            # 先吐文字再进 tool_calls。和 Gemini 路径完全对偶。
            streamed_text_buffer = ""
            # Thinking 模型本轮的推理链：finish_reason=tool_calls 时必须随
            # assistant tool_calls turn 一起回填，否则部分 provider 下一轮报
            # 400（reasoning_content must be passed back）。普通端点恒为空。
            streamed_reasoning_buffer = ""
            async for chunk in self.llm.astream(messages, **overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
                if getattr(chunk, "content", None):
                    if tool_leak_filter is not None:
                        chunk.content = self._filter_tool_leak_content(
                            chunk.content, tool_leak_filter, provider=tool_leak_provider
                        )
                        setattr(chunk, "_tool_leak_filtered", True)
                    streamed_text_buffer += chunk.content
                if getattr(chunk, "reasoning_content", None):
                    streamed_reasoning_buffer += chunk.reasoning_content
                    # Pulse the thinking bubble on ANY chunk carrying reasoning,
                    # BEFORE the pure-reasoning skip below — a thinking provider
                    # can pack reasoning_content onto the SAME delta as a
                    # tool_call_delta / finish_reason (the OpenAI adapter keeps
                    # them in one LLMStreamChunk), and a reasoning tool-call turn
                    # has no visible token to show feedback otherwise (Codex P2).
                    await self._notify_reasoning_active()
                if chunk.tool_call_deltas:
                    deltas_per_chunk.append(chunk.tool_call_deltas)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                # Empty-completion 诊断：记最新的 finish_reason 和 prompt_tokens，
                # 给上层 stream_text / prompt_ephemeral 的兜底 warning 用。
                # usage chunk（terminal）才带 prompt_tokens；前面 text chunk 不带。
                if chunk.usage_metadata:
                    pt = chunk.usage_metadata.get("prompt_tokens")
                    if pt:
                        self._last_prompt_tokens = pt
                # 纯 reasoning chunk（thinking 模型先吐推理链，content 为空、无
                # tool delta / finish / usage）只在上面累积进 buffer，不向下游
                # 转发：``stream_text`` 在首个 yield 的 chunk 上记 TTFT，放行
                # reasoning-only 会把"首推理 token"误当首 token，拉低延迟埋点。
                if (
                    getattr(chunk, "reasoning_content", None)
                    and not getattr(chunk, "content", None)
                    and not chunk.tool_call_deltas
                    and not chunk.finish_reason
                    and not chunk.usage_metadata
                ):
                    # Pure reasoning-only chunk: already pulsed above; drop it so
                    # the "first token" TTFT埋点 isn't fooled by a reasoning token.
                    continue
                # 永远 yield 文本 chunk —— 即便是 tool-only turn 也可能在
                # finish_reason=tool_calls 之前 emit usage chunk 和空 content。
                yield chunk
            # 记录本次 attempt 的最终 finish_reason，供上层 empty-completion
            # 兜底警告引用（"safety" / "length" / "content_filter" / "stop" 都
            # 可能在 content 为空时出现，是诊断 Gemini-via-OpenAI-compat 静默
            # empty 的关键线索）。
            self._last_finish_reason = finish_reason
            if (
                not streamed_text_buffer
                and not deltas_per_chunk
                and finish_reason != "tool_calls"
            ):
                # 单独一行 INFO：empty completion 落地证据。tool_iter / model 一起
                # 打出来，配合上层 warning 可以拼出"哪一轮哪个 attempt 被 safety
                # 拦了 / 被 length 截了"。getattr 防御：测试桩可能 __new__ 绕过
                # __init__，所以 model / _last_prompt_tokens 字段都用 getattr 兜底。
                logger.info(
                    "OmniOfflineClient(openai): empty completion finish_reason=%s "
                    "tool_iter=%d model=%s prompt_tokens=%s",
                    finish_reason, tool_iter,
                    getattr(self, "model", None),
                    getattr(self, "_last_prompt_tokens", None),
                )
            if (
                finish_reason == "tool_calls"
                and deltas_per_chunk
                and tools_payload
                and self.on_tool_call is not None
            ):
                if tool_leak_filter is not None:
                    tail, event = tool_leak_filter.finalize()
                    if event:
                        log_tool_leak_filtered(event, provider=tool_leak_provider)
                    if tail:
                        streamed_text_buffer += tail
                        tail_chunk = LLMStreamChunk(content=tail)
                        setattr(tail_chunk, "_tool_leak_filtered", True)
                        yield tail_chunk
                    tool_leak_filter.reset()
                # ChatOpenAI is the right import even though we're outside
                # ChatOpenAI — `collect_tool_calls` is a staticmethod.
                from utils.llm_client import ChatOpenAI as _ChatOpenAI
                from utils.llm_client import LLMStreamChunk as _LLMStreamChunk
                calls = _ChatOpenAI.collect_tool_calls(deltas_per_chunk)
                await self._execute_and_append_openai_tool_calls(
                    messages, calls,
                    # Strip any leaked <think> CoT before it lands in history:
                    # the streaming guard (ThinkingStreamStripper) only protects
                    # TTS/UI; this assembled pre-tool text is persisted raw to the
                    # assistant tool-call turn, so a leak-prone Focus turn would
                    # otherwise carry CoT into the next turn's context. No-op on
                    # clean replies (no think tag present).
                    assistant_text=strip_thinking_segments(streamed_text_buffer),
                    assistant_reasoning=streamed_reasoning_buffer,
                )
                # 通知上游 ``stream_text``：本轮的 pre-tool text + tool_calls
                # 已经写进 history（assistant turn）。stream_text 据此清空
                # final-segment buffer，避免之后 append 的 final AIMessage
                # 把同一段 pre-tool 文本第二次写进 history。
                yield _LLMStreamChunk(content="", tool_round_persisted=True)
                continue
            return
        logger.warning(
            "OmniOfflineClient: tool iteration cap %d reached; forcing final answer without tools",
            self.max_tool_iterations,
        )
        # Forced-finalize：工具轮次封顶后，去掉 tools 再调一次，逼模型基于已
        # 积累的 tool 结果给出最终文本。否则弱模型在 finish_reason=tool_calls
        # 上死循环到封顶后整轮静默，上游只能报"未产生文本回复"，用户那边就
        # 表现为不回话。去掉 tools 后模型无法再发起调用，必须输出文本。
        final_overrides = {
            k: v for k, v in overrides.items() if k not in ("tools", "tool_choice")
        }
        final_finish_reason: Optional[str] = None
        final_prompt_tokens: Optional[int] = None
        async for chunk in self.llm.astream(messages, **final_overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
            if chunk.finish_reason:
                final_finish_reason = chunk.finish_reason
            if chunk.usage_metadata:
                pt = chunk.usage_metadata.get("prompt_tokens")
                if pt:
                    final_prompt_tokens = pt
            # Pulse on ANY reasoning chunk (incl. reasoning bundled with a tool
            # delta / finish_reason on one delta), before the pure-reasoning skip
            # below — same fix as the main loop (Codex P2).
            if getattr(chunk, "reasoning_content", None):
                await self._notify_reasoning_active()
            # 与常规 tool-loop 路径一致：不向下游转发 thinking 模型的纯
            # reasoning chunk（有 reasoning_content、无 content / tool delta /
            # finish / usage）。stream_text 在首个 yield 的 chunk 上记 TTFT，
            # 放行 reasoning-only 会把"首推理 token"误当首 token，污染封顶轮延迟埋点。
            if (
                getattr(chunk, "reasoning_content", None)
                and not getattr(chunk, "content", None)
                and not chunk.tool_call_deltas
                and not chunk.finish_reason
                and not chunk.usage_metadata
            ):
                continue
            if getattr(chunk, "content", None) and tool_leak_filter is not None:
                chunk.content = self._filter_tool_leak_content(
                    chunk.content, tool_leak_filter, provider=tool_leak_provider
                )
                setattr(chunk, "_tool_leak_filtered", True)
            yield chunk
        # prompt_tokens 走局部变量、流结束后无条件回填（与 genai 路径同口径）：这次
        # forced-finalize 没给 usage 时写回 None，而非沿用上一轮 tool-iteration 的旧
        # 值，避免上层 empty-completion 诊断串台。
        self._last_finish_reason = final_finish_reason
        self._last_prompt_tokens = final_prompt_tokens

    async def _astream_genai_with_tools(self, messages, **overrides):
        """google-genai streaming with tool support. Yields
        ``LLMStreamChunk``-shaped objects so the caller can be agnostic
        to which path delivered the stream.

        Tool calls (``part.function_call``) are aggregated within the
        current generation, then executed via ``on_tool_call``; the
        result is appended to ``messages`` (as a plain dict in the
        OpenAI-style "assistant w/ tool_calls" + "tool role" shape so
        the SAME history works for both genai and OpenAI-compat paths
        on subsequent turns) and ``generate_content_stream`` is
        re-invoked.

        Raises ``_GenaiToolsUnsupported`` if the SDK or this model
        cannot handle tools — caller falls back to OpenAI-compat."""
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        if not _ensure_genai():
            raise _GenaiToolsUnsupported("google-genai SDK not importable")
        types = _genai_types

        # Lazy client init — re-use across turns.
        if self._genai_client is None:
            try:
                self._genai_client = _genai.Client(api_key=self.api_key or None)
            except Exception as e:
                raise _GenaiToolsUnsupported(f"genai.Client init failed: {e}") from e

        # Build tools once per session (registry is identity-stable
        # across iterations within one stream_text call).
        tools_payload: list = []
        if self.has_tools():
            decls = [t.to_gemini_function_declaration() for t in self._tool_definitions]
            tools_payload = [types.Tool(function_declarations=decls)]

        # max_completion_tokens semantics: same intent as OpenAI path.
        gen_config_kw: dict = {}
        if self.llm is not None and self.llm.max_completion_tokens:
            gen_config_kw["max_output_tokens"] = int(self.llm.max_completion_tokens)
        if tools_payload:
            gen_config_kw["tools"] = tools_payload

        for tool_iter in range(self.max_tool_iterations):
            system_instruction, contents = _genai_messages_to_contents(messages)
            cfg_kw = dict(gen_config_kw)
            if system_instruction:
                cfg_kw["system_instruction"] = system_instruction
            try:
                config = types.GenerateContentConfig(**cfg_kw)
            except Exception as e:
                raise _GenaiToolsUnsupported(f"GenerateContentConfig rejected: {e}") from e

            try:
                stream = await self._genai_client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                # ⚠️ 不要把所有异常都包成 _GenaiToolsUnsupported！
                # ``_astream_with_tools`` 的 except 分支会在捕到 _GenaiToolsUnsupported
                # 时永久翻 ``_genai_tools_unsupported=True``，导致 transient
                # 错误（429/5xx/网络抖动/auth 临时失败）让整个 session 后续都
                # 退化到 OpenAI-compat（且 OpenAI-compat 不支持 Gemini 工具）。
                # 只在错误消息里明确出现 tools 相关关键字时才认定是 SDK/模型
                # 不支持工具，其余异常直接 raise 给上层 ``except Exception``
                # —— 那条分支只本轮 fallback，下一轮还会重试 genai 路径。
                err_msg = str(e).lower()
                if (
                    ("tool" in err_msg or "function" in err_msg)
                    and ("not support" in err_msg or "not_support" in err_msg or "unsupported" in err_msg or "invalid" in err_msg)
                ):
                    raise _GenaiToolsUnsupported(
                        f"generate_content_stream rejected tools: {e}"
                    ) from e
                raise

            # Per-iteration accumulators.
            collected_tool_calls: list = []  # list of (id, name, args_dict, raw_args_str)
            had_text = False
            # 累积本轮已经 yield 给用户的 text，下面写 assistant 历史时
            # 用作 ``content`` —— 否则下一轮 LLM 看到 ``content=""`` 会
            # 不知道自己已经说过这部分话，可能重复或改口。
            streamed_text_buffer = ""
            usage_emitted = False
            # Empty-completion 诊断：最后一次见到的 finish_reason 和 prompt_feedback.
            # block_reason。Gemini 的 SAFETY / RECITATION / MAX_TOKENS 都在这两个
            # 字段里露出来；OpenAI-compat 那条路径丢这些信息，所以这里直接从 SDK
            # 原 chunk 读。
            iter_finish_reason: Optional[str] = None
            iter_block_reason: Optional[str] = None

            try:
                async for chunk in stream:
                    # prompt_feedback.block_reason：Gemini 整段 input 被 safety
                    # 拦掉时填这个，candidate 可能根本没出现。
                    pf = getattr(chunk, "prompt_feedback", None)
                    if pf is not None:
                        br = getattr(pf, "block_reason", None)
                        if br:
                            iter_block_reason = str(br)
                    candidates = getattr(chunk, "candidates", None) or []
                    if not candidates:
                        continue
                    cand = candidates[0]
                    fr = getattr(cand, "finish_reason", None)
                    if fr:
                        iter_finish_reason = str(fr)
                    cand_content = getattr(cand, "content", None)
                    parts = getattr(cand_content, "parts", None) or []
                    for part in parts:
                        # Skip thinking parts (Gemini 2.5+ thinking models) — but
                        # surface the boolean "is thinking" pulse first so the
                        # bubble shows on genai reasoning turns too (dual with the
                        # OpenAI-compat reasoning_content path). The thought TEXT
                        # itself is still dropped.
                        if getattr(part, "thought", False):
                            await self._notify_reasoning_active()
                            continue
                        text = getattr(part, "text", None) or ""
                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None:
                            tc_name = (getattr(fn_call, "name", "") or "").strip()
                            if not tc_name:
                                # 与 OpenAI 路径对偶：空 name 的 function_call 是
                                # SDK glitch / 流提前中断的产物，写进 messages 会
                                # 让下一轮 generate_content_stream 收到无名
                                # function_call 直接 schema reject。drop + warning。
                                logger.warning(
                                    "OmniOfflineClient(genai): dropping function_call "
                                    "with empty name (id=%r)",
                                    getattr(fn_call, "id", ""),
                                )
                                continue
                            args = dict(getattr(fn_call, "args", None) or {})
                            try:
                                raw_args = json.dumps(args, ensure_ascii=False)
                            except (TypeError, ValueError):
                                raw_args = "{}"
                            collected_tool_calls.append((
                                getattr(fn_call, "id", "") or "",
                                tc_name,
                                args,
                                raw_args,
                            ))
                        elif text:
                            if tool_leak_filter is not None:
                                text = self._filter_tool_leak_content(
                                    text, tool_leak_filter, provider=tool_leak_provider,
                                )
                            had_text = True
                            streamed_text_buffer += text
                            chunk_out = LLMStreamChunk(content=text)
                            if tool_leak_filter is not None:
                                setattr(chunk_out, "_tool_leak_filtered", True)
                            yield chunk_out
                    # Usage metadata may arrive on the chunk.
                    usage_meta = getattr(chunk, "usage_metadata", None)
                    if usage_meta is not None and not usage_emitted:
                        try:
                            usage_dict = {
                                "prompt_tokens": getattr(usage_meta, "prompt_token_count", 0) or 0,
                                "completion_tokens": getattr(usage_meta, "candidates_token_count", 0) or 0,
                                "total_tokens": getattr(usage_meta, "total_token_count", 0) or 0,
                            }
                            # Empty-completion 诊断：把 prompt_tokens 落进 self，
                            # 给上层 stream_text 兜底警告引用，跟 OpenAI 路径对偶。
                            if usage_dict["prompt_tokens"]:
                                self._last_prompt_tokens = usage_dict["prompt_tokens"]
                            yield LLMStreamChunk(
                                content="",
                                usage_metadata=usage_dict,
                                response_metadata={"token_usage": usage_dict},
                            )
                            usage_emitted = True
                        except Exception as usage_err:
                            # usage 是可选 telemetry —— SDK 版本差异 / 字段缺失 /
                            # 字段类型不符都不该打断主文本流。只 debug-log 一下，
                            # 让用户回复继续。
                            logger.debug(
                                "genai usage_metadata emit skipped: %s",
                                usage_err,
                            )
            except Exception as e:
                err_msg = str(e).lower()
                # 与 generate_content_stream 调用本身的异常处理保持一致：
                # 只有错误消息明确含 "tool/function" + "not_support/unsupported/
                # invalid" 关键字组合时才认定 tools 不被 SDK / 模型支持，永久
                # 翻盘退到 OpenAI-compat。其他流中异常（含 "function call timeout"
                # 之类的 transient）原样 raise，让上层临时 fallback，下一轮再试。
                if (
                    ("tool" in err_msg or "function" in err_msg)
                    and ("not support" in err_msg or "not_support" in err_msg or "unsupported" in err_msg or "invalid" in err_msg)
                ):
                    raise _GenaiToolsUnsupported(f"genai stream rejected tools: {e}") from e
                raise

            # Empty-completion 诊断：落 self 字段 + 单独 INFO log。和 OpenAI 路径
            # 对偶；finish_reason / block_reason 两边都有可能填，谁先填就以谁为
            # 准（stream_text/prompt_ephemeral 的兜底 warning 会读这两个字段）。
            self._last_finish_reason = iter_finish_reason
            self._last_block_reason = iter_block_reason
            if not had_text and not collected_tool_calls:
                # getattr 防御同 OpenAI 路径；__new__ 绕过 __init__ 的测试桩不会崩。
                logger.info(
                    "OmniOfflineClient(genai): empty completion finish_reason=%s "
                    "block_reason=%s tool_iter=%d model=%s prompt_tokens=%s",
                    iter_finish_reason, iter_block_reason, tool_iter,
                    getattr(self, "model", None),
                    getattr(self, "_last_prompt_tokens", None),
                )

            if collected_tool_calls and self.on_tool_call is not None:
                # Execute tools, append a unified assistant + tool history (dict shape
                # accepted by both paths), then continue tool-iteration loop.
                tool_calls_dict = [
                    {
                        "id": tc_id or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc_name, "arguments": tc_raw},
                    }
                    for i, (tc_id, tc_name, _args, tc_raw) in enumerate(collected_tool_calls)
                ]
                if tool_leak_filter is not None:
                    tail, event = tool_leak_filter.finalize()
                    if event:
                        log_tool_leak_filtered(event, provider=tool_leak_provider)
                    if tail:
                        streamed_text_buffer += tail
                        tail_chunk = LLMStreamChunk(content=tail)
                        setattr(tail_chunk, "_tool_leak_filtered", True)
                        yield tail_chunk
                    tool_leak_filter.reset()
                # 把本轮已经流给用户的 text 一起写进历史。Gemini 在同一 turn
                # 里允许 text part 与 function_call part 并存；如果这里仍写
                # ``content=""``，下一轮 LLM 看到的上下文会缺掉前半句，模型
                # 会重复前缀或改口，最终持久化历史的顺序也跟真实生成顺序对不上。
                messages.append({
                    "role": "assistant",
                    # Symmetric with the OpenAI path: strip leaked <think> CoT
                    # before persisting the pre-tool text to history (no-op on
                    # clean replies / the genai path, which routes thought out).
                    "content": strip_thinking_segments(streamed_text_buffer),
                    "tool_calls": tool_calls_dict,
                })
                for i, (tc_id, tc_name, tc_args, tc_raw) in enumerate(collected_tool_calls):
                    tool_call = ToolCall(
                        name=tc_name,
                        arguments=tc_args,
                        call_id=tc_id or f"call_{i}",
                        raw_arguments=tc_raw,
                    )
                    try:
                        result = await self.on_tool_call(tool_call)
                    except Exception as e:
                        logger.exception("OmniOfflineClient(genai): on_tool_call '%s' raised", tc_name)
                        result = ToolResult(
                            call_id=tool_call.call_id, name=tc_name,
                            output={"error": f"{type(e).__name__}: {e}"},
                            is_error=True, error_message=str(e),
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "name": tc_name,
                        "content": result.output_as_json_string(),
                    })
                # Sentinel：与 OpenAI 路径对偶，告诉上游 stream_text 把
                # final-segment buffer 清掉（pre-tool 文本已被持久化进
                # assistant turn 的 content 字段）。
                yield LLMStreamChunk(content="", tool_round_persisted=True)
                # Loop again to let the model produce a final answer.
                if not had_text:
                    continue
                # Edge case: model emitted text AND tool calls — text already
                # streamed to the user. Continue to next iter to give the
                # model a chance to follow up after seeing tool results.
                continue
            return
        logger.warning(
            "OmniOfflineClient(genai): tool iteration cap %d reached; forcing final answer without tools",
            self.max_tool_iterations,
        )
        # Forced-finalize：与 OpenAI 路径对偶。去掉 tools 再生成一次，逼模型
        # 基于已积累的 tool 结果输出最终文本，避免封顶后整轮静默。
        # 不吞异常：与 OpenAI 路径一致，让 SDK 调用失败原样向上抛，由 stream_text /
        # prompt_ephemeral 现成的 retry / 状态上报 / response_discarded 清泡泡逻辑
        # 接管。若在这里 try/except 成 warning，就把真实失败伪装成"空回复"，弱模型
        # 超限后反而可能重回静音态，与本兜底目标冲突。
        final_cfg_kw = {k: v for k, v in gen_config_kw.items() if k != "tools"}
        final_system_instruction, final_contents = _genai_messages_to_contents(messages)
        if final_system_instruction:
            final_cfg_kw["system_instruction"] = final_system_instruction
        final_config = types.GenerateContentConfig(**final_cfg_kw)
        final_stream = await self._genai_client.aio.models.generate_content_stream(
            model=self.model,
            contents=final_contents,
            config=final_config,
        )
        final_finish_reason: Optional[str] = None
        final_block_reason: Optional[str] = None
        final_prompt_tokens: Optional[int] = None
        final_had_text = False
        async for chunk in final_stream:
            # 与常规 genai 分支对偶地采集空回复诊断：block_reason / finish_reason /
            # prompt_tokens。否则若 forced-finalize 也被 safety / recitation /
            # max-tokens 挡住而无文本，上层只能引用上一轮 tool-iteration 的过期
            # finish_reason，诊断失真。
            pf = getattr(chunk, "prompt_feedback", None)
            if pf is not None:
                br = getattr(pf, "block_reason", None)
                if br:
                    final_block_reason = str(br)
            usage_meta = getattr(chunk, "usage_metadata", None)
            if usage_meta is not None:
                pt = getattr(usage_meta, "prompt_token_count", 0) or 0
                if pt:
                    final_prompt_tokens = pt
            candidates = getattr(chunk, "candidates", None) or []
            if not candidates:
                continue
            cand = candidates[0]
            fr = getattr(cand, "finish_reason", None)
            if fr:
                final_finish_reason = str(fr)
            cand_content = getattr(cand, "content", None)
            for part in (getattr(cand_content, "parts", None) or []):
                if getattr(part, "thought", False):
                    continue
                text = getattr(part, "text", None) or ""
                if text:
                    if tool_leak_filter is not None:
                        text = self._filter_tool_leak_content(
                            text, tool_leak_filter, provider=tool_leak_provider,
                        )
                    final_had_text = True
                    chunk_out = LLMStreamChunk(content=text)
                    if tool_leak_filter is not None:
                        setattr(chunk_out, "_tool_leak_filtered", True)
                    yield chunk_out
        # 统一回填本次 forced-finalize 自己的诊断值（含 prompt_tokens）。prompt_tokens
        # 走局部变量、流结束后无条件回填：若这次被挡住/没给 usage，写回 None 而非沿用
        # 上一轮 tool-iteration 的旧值，避免 INFO log / 上层 LLM_NO_RESPONSE 诊断串台。
        self._last_finish_reason = final_finish_reason
        self._last_block_reason = final_block_reason
        self._last_prompt_tokens = final_prompt_tokens
        if not final_had_text:
            logger.info(
                "OmniOfflineClient(genai): forced-finalize empty completion "
                "finish_reason=%s block_reason=%s model=%s prompt_tokens=%s",
                final_finish_reason, final_block_reason,
                getattr(self, "model", None),
                final_prompt_tokens,
            )

    def update_max_response_length(self, max_length: int) -> None:
        """Update the response token cap (the user may change settings mid-conversation).
        Same unit as ``self.max_response_length``: tiktoken token count.
        Also refreshes ``self.llm.max_completion_tokens`` so the next astream
        request stops naturally at the new budget+20.

        ``0`` / negative values are both interpreted as "unlimited", matching the
        ``__init__`` semantics; an upper layer passing -1 as a cancel-the-cap signal
        also passes through correctly."""
        if isinstance(max_length, int):
            self.max_response_length = max_length if max_length > 0 else _UNLIMITED_BUDGET
            if self.llm is not None:
                # 普通 budget；summary 的 3000 抬升只在 stream_text 内临时生效。
                self.llm.max_completion_tokens = _budget_to_max_tokens(self.max_response_length)
            logger.debug(f"OmniOfflineClient: token 上限已更新为 {max_length}")

    def _match_name_prefix(self, text: str, name: str) -> int:
        """Check if text starts with a name prefix like 'Name | ' or 'Name |'.
        Returns the length of the matched prefix, or 0 if no match.
        Handles variants with/without spaces around the pipe character.
        """
        if not name:
            return 0
        for variant in (f"{name} | ", f"{name} |", f"{name}| ", f"{name}|"):
            if text.startswith(variant):
                return len(variant)
        return 0

    async def connect(self, instructions: str, native_audio=False) -> None:
        """Initialize the client with system instructions."""
        self._instructions = instructions
        # Add system message to conversation history using langchain format
        self._conversation_history = [
            SystemMessage(content=instructions)
        ]
        logger.info("OmniOfflineClient initialized with instructions")
    
    async def send_event(self, event) -> None:
        """Compatibility method - not used in text mode"""
        pass
    
    async def update_session(self, config: Dict[str, Any]) -> None:
        """Compatibility method - update instructions if provided"""
        if "instructions" in config:
            self._instructions = config["instructions"]
            # Update system message using langchain format
            if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
                self._conversation_history[0] = SystemMessage(content=self._instructions)
    
    async def switch_model(self, new_model: str, use_vision_config: bool = False) -> None:
        """
        Temporarily switch to a different model (e.g., vision model).
        This allows dynamic model switching for vision tasks.

        Args:
            new_model: The model to switch to
            use_vision_config: If True, use vision_base_url and vision_api_key
        """
        lock = getattr(self, "_model_switch_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._model_switch_lock = lock

        async with lock:
            if not new_model or new_model == self.model:
                return

            logger.info(f"Switching model from {self.model} to {new_model}")

            # 选择使用的 API 配置
            if use_vision_config:
                base_url = self.vision_base_url
                api_key = self.vision_api_key if self.vision_api_key and self.vision_api_key != '' else None
                provider_type = self.vision_provider_type
            else:
                base_url = self.base_url
                api_key = self.api_key
                provider_type = self.provider_type

            # 先创建新 client，成功后再原子替换，避免半切换状态。
            # max_completion_tokens 跟随当前 max_response_length 同步设置
            # （和 __init__ 一致）。
            new_llm = await create_chat_llm_async(
                new_model, base_url, api_key,
                streaming=True, max_retries=0,
                # 普通 budget；summary 的 3000 抬升只在 stream_text 内临时生效。
                max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
                timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard; generous so normal/long replies aren't truncated
                provider_type=provider_type,
            )
            old_llm = self.llm
            self.llm = new_llm
            self.model = new_model
            # ⚠️ 同步 self.base_url / self.api_key —— 否则后续 _astream_with_tools
            # 重新计算 _use_genai_sdk 时拿到的还是旧 conversation 配置，会
            # 把 vision 走的 Gemini endpoint 错误路由到 OpenAI-compat（反之亦然）。
            self.base_url = base_url
            self.api_key = api_key
            # 路由旗标随之刷新；旧 _genai_client 抛弃（若 api_key 变了它已失效）。
            # genai.Client 内部持有 httpx 连接池——直接 = None 靠 GC 回收虽不
            # 是 leak，但提早 close() 能马上释放底层连接（SDK 没暴露 aclose，
            # close 是同步方法，放进 to_thread 不阻事件循环）。
            old_genai = self._genai_client
            self._use_genai_sdk = _should_use_genai_sdk(self.model, self.base_url)
            self._genai_client = None
            self._genai_tools_unsupported = False
            if old_genai is not None and hasattr(old_genai, "close"):
                try:
                    await asyncio.to_thread(old_genai.close)
                except Exception as _close_err:
                    logger.warning(
                        "switch_model: old genai client close failed: %s",
                        _close_err,
                    )
            try:
                await old_llm.aclose()
            except Exception as e:
                logger.warning(f"switch_model: old client aclose failed: {e}")
    
    async def _check_repetition(self, response: str) -> bool:
        """
        Check whether the reply is highly repetitive of recent replies.
        Returns True and triggers the callback if 3 consecutive turns are highly repetitive.
        """
        
        # 与最近的回复比较相似度
        high_similarity_count = 0
        for recent in self._recent_responses:
            similarity = calculate_text_similarity(response, recent)
            if similarity >= self._repetition_threshold:
                high_similarity_count += 1
        
        # 添加到最近回复列表
        self._recent_responses.append(response)
        if len(self._recent_responses) > self._max_recent_responses:
            self._recent_responses.pop(0)
        
        # 如果与最近2轮都高度重复（即第3轮重复），触发检测
        if high_similarity_count >= 2:
            logger.warning(f"OmniOfflineClient: 检测到连续{high_similarity_count + 1}轮高重复度对话")
            
            # 清空对话历史（保留系统指令）
            if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
                self._conversation_history = [self._conversation_history[0]]
            else:
                self._conversation_history = []

            # 清空重复检测缓存
            self._recent_responses.clear()
            
            # 触发回调
            if self.on_repetition_detected:
                await self.on_repetition_detected()
            
            return True
        
        return False

    async def _notify_response_discarded(self, reason: str, attempt: int, max_attempts: int, will_retry: bool,
                                         message: Optional[str] = None) -> None:
        """
        Notify the upper layer that the current reply was discarded, so the frontend bubble can be cleared / the user informed
        """
        if self.on_response_discarded:
            try:
                await self.on_response_discarded(reason, attempt, max_attempts, will_retry, message)
            except Exception as e:
                logger.warning(f"通知 response_discarded 失败: {e}")

    async def _summarize_tail_for_tts(self, prefix: str, tail: str) -> Optional[str]:
        """Small-model call for the long-reply summary path.

        ``prefix`` is the part TTS has already played to the user (used as a context
        anchor so the summary flows naturally); ``tail`` is the part after the cutover
        that was never read out and needs compressing. Returns the 1-2 closing
        sentences written by the emotion-tier LLM, or ``None`` when config is missing /
        the call fails — the caller then falls back to "read the full original text".

        The prompt does not include the persona — prefix/tail were already written by
        the main model in the persona's voice, so the small model only needs to keep
        the existing tone and shorten the tail, not re-enact the persona. Language is
        detected from ``tail`` (the prefix may be very short; the tail is more
        informative).
        """
        if not (tail and tail.strip()):
            return None

        # emotion 配置在 config/api_providers.json 每个 provider 下都有
        # `emotion_model` 字段；config_manager 拿到的就是当前 provider 的
        # emotion 子配置（model/base_url/api_key）。
        try:
            from utils.config_manager import get_config_manager  # 延迟 import 防循环
            cfg_mgr = get_config_manager()
            emotion_config = cfg_mgr.get_model_api_config('emotion') if cfg_mgr else None
        except Exception as e:
            logger.warning("summary: 取 emotion 配置失败: %s", e)
            return None
        if not emotion_config:
            return None
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        emotion_provider_type = emotion_config.get('provider_type')
        if not (emotion_api_key and emotion_model):
            logger.info("summary: emotion 模型/Key 未配置，跳过长回复摘要")
            return None

        try:
            from utils.language_utils import detect_language, normalize_language_code
            detected = detect_language(tail) or 'zh'
            lang = normalize_language_code(detected, format='short') or 'zh'
        except Exception:
            lang = 'zh'

        try:
            from config.prompts.prompts_response import get_long_response_tail_summary_prompts
            templates = get_long_response_tail_summary_prompts(lang)
        except Exception as e:
            logger.warning("summary: prompt 模板取失败: %s", e)
            return None

        # system 模板 persona-agnostic，无占位符直接用；user 模板只填 prefix/tail。
        system_text = templates['system']
        try:
            user_text = templates['user_template'].format(
                prefix=prefix or '',
                tail=tail,
            )
        except KeyError as e:
            logger.warning("summary: prompt 占位符缺失: %s", e)
            return None

        # 调用 token 用量打到 "long_response_summary" 类别下，与 emotion 区分。
        set_call_type("long_response_summary")
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=user_text),
        ]
        # 不传 temperature：project policy 让 provider 默认值决定
        # （scripts/check_no_temperature.py 会守门）。emotion-tier 模型自带
        # 一个合适的 temperature，不需要 caller 干预。
        try:
            llm = await create_chat_llm_async(
                emotion_model, emotion_base_url, emotion_api_key,
                max_completion_tokens=120,
                timeout=30,
                provider_type=emotion_provider_type,
            )
        except Exception as e:
            logger.warning("summary: 构造 emotion LLM 失败: %s", e)
            return None

        try:
            async with llm:
                result = await llm.ainvoke(messages)
        except Exception as e:
            logger.warning("summary: emotion 模型调用失败: %s", e)
            return None

        summary = ""
        try:
            summary = (result.content or "").strip()
        except Exception:
            summary = ""
        # 兜底：去掉模型可能加的引号 / 元前缀。emotion-tier 模型偶尔仍会写
        # "总之，xxx" / "总结：xxx" 之类，prompt 已禁但模型不一定听话；这里
        # 只剥首尾的引号和最常见的元前缀，剩下的就当 character 自然口语。
        if summary and summary[0] in '“”"\'「『' and summary[-1] in '“”"\'」』':
            summary = summary[1:-1].strip()
        if not summary:
            return None
        # 硬性收口：emotion-tier 模型即使被 prompt 约束也可能输出 3-4 句话，
        # 直接灌进 TTS 会把"短促收尾"这个核心目标打废。最多保留 2 个 sentence-end
        # 段，再硬截到 ``_SUMMARY_HARD_TOKEN_CAP`` 个 token。两个限制叠加：先按
        # 句末切，再按 token 兜底——任意一条触发都收口。token 口径与 budget
        # 一致，跨语种行为统一（字符口径会过度惩罚 CJK）。
        sentence_segments: list[str] = []
        cursor = 0
        for idx, ch in enumerate(summary):
            if ch in _SENTENCE_END_CHARS:
                sentence_segments.append(summary[cursor:idx + 1])
                cursor = idx + 1
                if len(sentence_segments) >= 2:
                    break
        if sentence_segments:
            trimmed = "".join(sentence_segments)
            # 若模型在 2 句之外还塞了尾巴，丢弃
            summary = trimmed.strip()
        if count_tokens(summary) > _SUMMARY_HARD_TOKEN_CAP:
            summary = truncate_to_tokens(summary, _SUMMARY_HARD_TOKEN_CAP).rstrip()
        if not summary:
            return None
        return summary

    @staticmethod
    def _focus_stream_overrides(
        thinking_on: bool, model: str, base_max_tokens: int | None = None,
    ) -> dict:
        """Per-call streaming overrides for a Focus turn.

        When thinking-on, override extra_body with ``focus_extra_body(model)`` —
        the provider's thinking knob flipped to its ENABLED form (per provider
        dialect) while PRESERVING non-thinking provider extras (e.g. step-2-mini's
        built-in web_search), which a blunt ``extra_body=None`` would drop.
        Returns ``{}`` (instance default, thinking off) otherwise.

        Also bumps ``max_completion_tokens`` by ``FOCUS_THINKING_EXTRA_TOKENS``
        — but ONLY when this turn actually flips thinking ON for the provider:
        thinking models (Qwen / GLM / Kimi / Doubao / OpenRouter) bill reasoning
        tokens against the SAME budget as the visible reply, so without headroom
        the chain-of-thought squeezes the answer short. "Actually on" is detected
        by ``focus_extra_body(model) != get_extra_body(model)`` — when the focus
        form equals the regular (thinking-off) extra_body (Claude kept disabled,
        unknown models → None, non-thinking providers only preserving their
        non-thinking extras) there is no reasoning to reserve for, and a needless
        +800 could push a request past a model's output ceiling near the cap /
        summary floor. The bump is layered on ``base_max_tokens`` (the live
        instance ceiling, already reflecting summary-mode lift / vision-model
        switch), so it composes with both. ``base_max_tokens=None`` (unlimited
        budget) omits the field — the request stays uncapped. The Python-side
        length guard still caps the visible reply at ``max_response_length``;
        this only gives reasoning its own slack on the API side.

        Vision-model turns are included: Focus runs thinking-on regardless of
        whether the turn carries images. The inline streaming timeout
        (``DIALOG_LLM_STREAM_TIMEOUT_SECONDS``, 180s) is generous enough for a
        vision reasoning turn — unlike the short-windowed proactive Phase-2 path,
        which still keeps thinking off (its 16-25s window would time out).
        """
        if not thinking_on:
            return {}
        from config.providers import focus_extra_body, get_extra_body
        fb = focus_extra_body(model)
        overrides: dict = {"extra_body": fb}
        # Headroom only when Focus actually enables thinking for this provider:
        # ``fb is None`` ⇒ no thinking-enable override at all (unknown model);
        # ``fb == get_extra_body(model)`` ⇒ focus form equals the regular
        # (thinking-off) form (Claude kept disabled, non-thinking providers only
        # preserving their own extras). Either way there's no reasoning to
        # reserve for, and a needless +800 could push a request past a model's
        # output ceiling.
        if (
            base_max_tokens is not None
            and fb is not None
            and fb != get_extra_body(model)
        ):
            overrides["max_completion_tokens"] = base_max_tokens + FOCUS_THINKING_EXTRA_TOKENS
        return overrides

    async def stream_text(
        self,
        text: str,
        *,
        system_prefix: str | None = None,
        thinking_on: bool = False,
        input_transcript_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        history_replacement_text: str | None = None,
    ) -> None:
        """
        Send a text message to the API and stream the response.
        If there are pending images, temporarily switch to vision model for this turn.
        Uses langchain ChatOpenAI for streaming.

        ``thinking_on`` (Focus mode 凝神, docs/design/focus-truename-mode.md):
        when True, this single turn drops the auto-resolved thinking-off
        ``extra_body`` so the provider runs its default reasoning ("放飞自我").
        It is a per-call override (``extra_body=None`` threaded into
        ``astream``) — the session LLM is NOT rebuilt and the next regular
        turn falls straight back to thinking-off. Applies to the
        OpenAI-compat path (where the thinking-off knob lives); the native
        google-genai path is already thinking-capable by default, so the
        override is a no-op there.

        Purpose of ``system_prefix``: the caller (typically SessionManager rendering a
        passive agent callback into watermarked ``======[系统通知] xxx======`` text)
        splices this neutral system-notice text **in place, as a prefix to this turn's
        user message content** — the LLM treats it as "extra context attached at the
        moment the user spoke" and mentions it naturally within the same turn, without
        starting a separate turn or a separate SystemMessage.

        Symmetry with voice mode: ``OmniRealtimeClient.prime_context(skipped=False)``
        on GPT/GLM/Step likewise goes through ``create_response`` to inject the
        callback as a user-role message and trigger a response. Inlining into user
        content means accepting that the callback text is persisted into
        ``_conversation_history`` along with the user message (consistent with the
        voice side's user-role injection semantics).

        ``input_transcript_callback`` lets a caller bind the transcript recording
        callback to this request. This is used when the frontend sends a long prompt
        but wants memory/history to record a concise user-facing summary.

        ``history_replacement_text`` keeps the full prompt available for the current
        LLM turn, then replaces the just-appended user history entry before the next
        turn reuses ``_conversation_history``.
        """  # noqa: DOCSTRING_CJK
        if not text or not text.strip():
            # If only images without text, use a default prompt
            if self._pending_images:
                text = "请分析这些图片。"
            else:
                return

        # Fresh stream: open a new reasoning-pulse scope (bump the ownership seq)
        # so this turn's first reasoning chunk re-pulses the bubble. stream_text
        # does not clear via _notify_reasoning_done — core's inline finally clears
        # unconditionally — so it only needs the bump, not an owner token.
        self._begin_reasoning_stream()

        # Check if we need to switch to vision model. A staged proactive-vision
        # screenshot (the screen she just commented on) counts as an image too,
        # so a text-only user reply still goes multi-modal and the model sees it.
        # The staged screenshot is dropped (not injected) when either:
        #  - TTL: older than _PROACTIVE_SCREENSHOT_TTL_SECONDS (the screen has moved
        #    on — a stale frame would mislead more than help); or
        #  - superseded: a later AI turn was appended after staging (e.g. a
        #    greeting / agent callback via prompt_ephemeral), so this reply isn't
        #    answering the screen-based talk anymore. History only grows by
        #    appends between staging and this read (the user hasn't been appended
        #    yet), so a length change means an intervening AI turn (Codex P2).
        proactive_image = self._proactive_image_to_inject
        if proactive_image:
            _expired = (
                time.monotonic() - self._proactive_image_staged_at
                > _PROACTIVE_SCREENSHOT_TTL_SECONDS
            )
            _superseded = len(self._conversation_history) != self._proactive_image_history_len
            if _expired or _superseded:
                logger.info(
                    "Proactive screenshot dropped (expired=%s superseded=%s)",
                    _expired, _superseded,
                )
                self._proactive_image_to_inject = None
                self._proactive_image_staged_at = 0.0
                self._proactive_image_history_len = 0
                proactive_image = None
        has_images = bool(proactive_image) or len(self._pending_images) > 0
        # 就地植入 system_prefix：拼到 user content 的 text 段前缀（watermark
        # 自带，不补 separator 也能区分）。callback 文本随 HumanMessage 一起
        # 落 history，跟 voice mode user-role 注入对偶。
        _user_text = text.strip()
        _prefix_clean = (system_prefix or "").strip()
        _user_text_with_prefix = (
            f"{_prefix_clean}\n\n{_user_text}" if _prefix_clean else _user_text
        )

        # Prepare user message content
        if has_images:
            # Switch to vision model permanently for this session
            # (cannot switch back because image data remains in conversation history)
            if self.vision_model and self.vision_model != self.model:
                logger.info(f"🖼️ Temporarily switching to vision model: {self.vision_model} (from {self.model})")
                await self.switch_model(self.vision_model, use_vision_config=True)

            # Multi-modal message: images + text
            content = []

            # Add images first. Temporal order: the proactive screenshot (the
            # screen she commented on, BEFORE the user spoke) leads, then the
            # user's own pending frame(s) — so the model doesn't mistake the
            # earlier screen for what the user just captured.
            if proactive_image:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{proactive_image}"
                    }
                })
            for img_b64 in self._pending_images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })

            # Add text（已含 system_prefix watermark 前缀，若有）
            content.append({
                "type": "text",
                "text": _user_text_with_prefix,
            })

            user_message = HumanMessage(content=content)
            _img_count = len(self._pending_images) + (1 if proactive_image else 0)
            logger.info(
                f"Sending multi-modal message with {_img_count} image(s)"
                f"{' (incl. proactive screen)' if proactive_image else ''}"
            )

            # Clear pending images after using them (content already holds the
            # data urls). The proactive screenshot is one-shot: consumed by this
            # reply, then cleared so it never re-injects into later turns.
            self._pending_images.clear()
            self._proactive_image_to_inject = None
            self._proactive_image_staged_at = 0.0
            self._proactive_image_history_len = 0
        else:
            # Text-only message（已含 system_prefix watermark 前缀，若有）
            user_message = HumanMessage(content=_user_text_with_prefix)

        self._conversation_history.append(user_message)
        history_replacement_index = len(self._conversation_history) - 1
        history_replacement_text = (
            str(history_replacement_text).strip()
            if history_replacement_text is not None
            else ""
        )
        if history_replacement_text and _prefix_clean:
            history_replacement_text = f"{_prefix_clean}\n\n{history_replacement_text}"
        if has_images:
            self._evict_old_images()

        # Callback for user input
        transcript_callback = input_transcript_callback or self.on_input_transcript
        if transcript_callback:
            await transcript_callback(text.strip())

        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""        # 仅最后一段未持久化的 text（final-segment）
        assistant_message_total = ""  # 整轮累计（含 pre-tool），整轮级判定看它
        status_reported = False
        guard_exhausted = False
        # Empty-completion 诊断字段重置：每轮 turn 独立，否则会读到上一轮的旧值。
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None

        # 单测会用 __new__ 绕过 __init__ 直接 mock OmniOfflineClient，此时
        # ``enable_long_response_summary`` 属性根本不存在。这里取一次本地
        # snapshot 避免后面每一处都 getattr，也防止运行中外部改属性导致
        # state machine 半生效。
        summary_mode_enabled = bool(getattr(self, "enable_long_response_summary", False))

        # summary 模式临时把 API budget 抬到 _SUMMARY_API_BUDGET_FLOOR：让模型
        # 有空间把话写完，cutover 之后的尾巴才有东西可摘要（普通 budget 下模型
        # 在 ~budget 处就停了，没有尾巴）。**只在 stream_text 内 bump**，finally
        # 还原，避免泄漏到共用同一 self.llm 的 prompt_ephemeral（proactive 没有
        # 长度 guard，被抬到 3000 会吐超长回复）。snapshot 原值精确还原，兼容
        # 运行中 update_max_response_length 改过 budget 的场景。
        _summary_prev_max_tokens = None
        if summary_mode_enabled and getattr(self, "llm", None) is not None:
            _summary_prev_max_tokens = self.llm.max_completion_tokens
            self.llm.max_completion_tokens = _budget_to_max_tokens(
                self.max_response_length, summary_mode=True,
            )

        try:
            self._is_responding = True
            reroll_count = 0
            set_call_type("conversation")

            # 防御性检查：确保对话历史中至少有用户消息
            has_user_message = any(isinstance(msg, HumanMessage) for msg in self._conversation_history)
            if not has_user_message:
                error_msg = "对话历史中没有用户消息，无法生成回复"
                logger.error(f"OmniOfflineClient: {error_msg}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "NO_USER_MESSAGE"}))
                    status_reported = True
                return
            for attempt in range(max_retries):
                # close() 是唯一会把 self.llm 设为 None 的路径。它若在前一次
                # APIConnectionError 后的 retry sleep 期间触发（用户切模式 /
                # 断连 / session 熔断），就不再重试 —— 否则下面的 reroll while
                # 会先把 _is_responding 重置回 True 然后对 None 调 .astream，
                # 触发 NoneType.astream AttributeError。
                # 同样若 cancel_response() / handle_interruption() 在 retry sleep
                # 期间把 _is_responding 翻成 False（用户主动打断），也不该再
                # 启动新一轮 attempt —— reroll while 会无条件 reset 回 True，
                # 默默吞掉用户的取消意图。和 prompt_ephemeral 的守卫保持一致。
                # 用 hasattr 守卫：单元测试用 __new__ 绕过 __init__ 不会设这个
                # 属性，但真实代码 __init__ 必设；区分"未初始化（测试桩）"和
                # "已关闭（生产）"两种情况。
                if (hasattr(self, "llm") and self.llm is None) or not self._is_responding:
                    logger.info("OmniOfflineClient.stream_text: client 已 close 或响应已被取消，终止 retry")
                    # 标记 status_reported 抑制 finally 的 LLM_NO_RESPONSE 兜底：
                    # 这是用户主动 cancel / close，不是 LLM 故障，前端不该看到
                    # 红条错误。这里没有 status 要发，仅占位让 finally 跳过。
                    status_reported = True
                    break
                try:
                    assistant_message = ""
                    assistant_message_total = ""
                    guard_attempt = 0
                    # Telemetry：TTFT（首 token 延迟）。从这里到第一个 chunk 到达
                    # 的时长，D1 流失里"响应太慢"的关键信号。只记一次（首 attempt
                    # 首 chunk）；reroll 不重置，反映用户真实等待体感。
                    _ttft_start = time.time()
                    _ttft_recorded = False
                    while guard_attempt <= self.max_response_rerolls:
                        self._is_responding = True
                        assistant_message = ""           # 仅最后一段未持久化的 text，用于 final AIMessage append
                        assistant_message_total = ""     # 全轮累积，用于 _check_repetition / 长度 guard
                        is_first_chunk = True
                        pipe_count = 0  # 围栏：追踪 | 字符的出现次数
                        fence_triggered = False  # 围栏是否已触发
                        guard_triggered = False
                        discard_reason = None
                        length_guard_recovery_text = ""
                        length_guard_persisted_prefix = ""
                        length_guard_original_tokens = 0
                        chunk_usage = None
                        prefix_buffer = ""
                        prefix_checked = not bool(self._prefix_buffer_size)

                        # ── Summary-mode 状态机（仅 summary_mode_enabled 时生效）──
                        # 完整流转图，便于把散在 4 处（这里初始化 / 长度 trigger /
                        # emit fork / 流末 epilogue）的逻辑拼成一张图：
                        #
                        #   idle ──(_total 越过 budget)──▶ pending_cutover
                        #     │                                 │
                        #     │                  (找到 budget 之后的 terminator)
                        #     │                                 ▼
                        #     │                           cutover_done
                        #     │                          /     │      \
                        #     │           (tail 攒到乱码)/      │       \(stream 结束)
                        #     │                       /        │        \
                        #     │           gibberish_fallback   │   epilogue 决策：
                        #     │           (静默截断，          │   · final<budget+slack → abandon，tail 续 TTS
                        #     │            history 只留 prefix) │   · 否则 → 调小模型摘要续 TTS（失败则 abandon）
                        #     │                                 │
                        #     └─(stream 结束仍 idle / pending)──┴─▶ 常规：完整原文进 history
                        #
                        # 各状态的 emit 去向：
                        #   idle / pending_cutover  → UI + TTS（both）
                        #   cutover_done            → 仅 UI（tail 攒进 summary_tail_buffer）
                        # tool_round_persisted 触发时：先把 cutover 后的 tail abandon 给
                        # TTS（否则永远听不到），再把整套状态重置回 idle。
                        summary_state = 'idle'
                        summary_prefix_for_history = ""  # cutover 触发那一刻 assistant_message 的快照
                        summary_tail_buffer = ""        # post-cutover UI-only 文本
                        summary_next_gibberish_check = _SUMMARY_GIBBERISH_RECHECK_TOKENS
                        summary_trigger_tokens = 0     # 触发 summary 时的 _total（仅日志）
                        # 越界点 char offset：模型一口气 yield 一个超大 chunk 时
                        # （多个 sentence/clause），budget 之前的 terminator 不算数 ——
                        # 应该从这个点开始找 terminator。trigger chunk 之后立即
                        # 消费回 0，下一 chunk 从头扫（因为它整段已经在 budget 之后）。
                        summary_overflow_offset = 0

                        def _has_unpersisted_recovery_suffix(recovery_text: str) -> bool:
                            if not recovery_text:
                                return False
                            if not length_guard_persisted_prefix:
                                return True
                            if not recovery_text.startswith(length_guard_persisted_prefix):
                                return False
                            return bool(recovery_text[len(length_guard_persisted_prefix):].strip())

                        # Tool-aware streaming: ``_astream_with_tools`` runs
                        # the multi-turn tool loop inside (executing tools and
                        # appending results to ``_conversation_history`` IN
                        # PLACE). The yielded chunks are exactly the same
                        # shape as raw ``self.llm.astream``, so the existing
                        # prefix/fence/length-guard logic below is untouched.
                        # Focus 凝神: thinking_on threads ``extra_body=None``
                        # down to ``astream`` (per-call override) so this turn
                        # reasons freely; regular turns pass nothing → the
                        # instance's thinking-off extra_body applies. Routed
                        # through the visible (tool-leak-filtered) variant,
                        # which forwards **overrides to ``_astream_with_tools``.
                        # Also threads a ``max_completion_tokens`` bump
                        # (+FOCUS_THINKING_EXTRA_TOKENS) so reasoning gets its
                        # own headroom instead of eating the reply's budget;
                        # base is the live instance ceiling (already reflects
                        # summary-mode lift / vision switch), ``None`` stays
                        # uncapped.
                        _focus_overrides = self._focus_stream_overrides(
                            thinking_on, self.model,
                            base_max_tokens=self.llm.max_completion_tokens if self.llm else None,
                        )
                        # Focus 凝神: leak-prone models (qwen3.5/3.6/3.7 hybrids)
                        # stream their chain-of-thought into ``content`` ending in
                        # a lone ``</think>``; hold + drop it before TTS/UI ever
                        # see it. Clean providers (reasoning_content path) get no
                        # stripper, so their streaming stays byte-for-byte untouched.
                        # Gate on the SAME condition as _focus_stream_overrides
                        # (just ``thinking_on`` — vision turns now reason too).
                        from config.providers import leaks_thinking_in_content
                        think_stripper = (
                            ThinkingStreamStripper()
                            if thinking_on and leaks_thinking_in_content(self.model)
                            else None
                        )
                        async for chunk in self._astream_visible_with_tools(
                            self._conversation_history, **_focus_overrides,
                        ):
                            if not _ttft_recorded:
                                _ttft_recorded = True
                                try:
                                    from utils.instrument import histogram as _instr_h
                                    _instr_h("llm_ttft_ms", max(0.0, (time.time() - _ttft_start) * 1000.0))
                                except Exception:
                                    # 埋点 best-effort，绝不打断流式响应主路径。
                                    pass
                            if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                                chunk_usage = chunk.usage_metadata
                                logger.debug(f"🔍 [Usage] {chunk_usage}")
                            if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                                if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                                    logger.debug(f"🔍 [Meta] {chunk.response_metadata}")
                            # tool 轮 sentinel：``_astream_*_with_tools`` 已把
                            # pre-tool 文本 + tool_calls + tool result inline
                            # 写进 history。重置 final-segment buffer 防止
                            # 之后 append 的 AIMessage 把同一段 pre-tool 文本
                            # 第二次写进 history。``_total`` 不重置——重复检测
                            # / token 长度 guard 仍要看完整一轮的实际文本量。
                            if getattr(chunk, "tool_round_persisted", False):
                                length_guard_persisted_prefix = assistant_message_total
                                assistant_message = ""
                                # 重置围栏 / prefix buffer：下一段是新的语义
                                # 单元（模型基于 tool 结果重新出文本），不应
                                # 复用之前的 fence / prefix 状态。
                                pipe_count = 0
                                prefix_buffer = ""
                                prefix_checked = not bool(self._prefix_buffer_size)
                                if think_stripper is not None:
                                    # Flush before reset: if this pre-tool segment
                                    # never emitted </think>, the stripper is still
                                    # holding real answer text it withheld from
                                    # TTS/UI. The inner generator already persisted
                                    # that text to history (stripped), so emit it to
                                    # TTS/UI only here — dropping it (a bare reset)
                                    # would lose the pre-tool sentence. Then re-arm
                                    # for the post-tool segment (new semantic unit).
                                    _pretool_residual = think_stripper.flush()
                                    if _pretool_residual and _pretool_residual.strip() and self.on_text_delta:
                                        await self.on_text_delta(_pretool_residual, is_first_chunk)
                                        is_first_chunk = False
                                    think_stripper.reset()
                                # Summary 状态收尾：cutover 之后的 tail 已经 UI-only
                                # 发出去了，但 TTS 还没听到。tool 边界处不知道
                                # post-tool 段会有多长，没法走"final < max+slack"
                                # 那套判断，所以一律 abandon —— 把 tail 续给 TTS
                                # 当原文读完。`_astream_*_with_tools` 已经把含
                                # tail 的完整 pre-tool 文本写进 assistant.tool_calls.content
                                # 持久化到 _conversation_history，UI/TTS/history 这下
                                # 三家口径一致。然后再重置 state 让 post-tool 重新
                                # 走 idle 起点。
                                if (
                                    summary_mode_enabled
                                    and summary_state == 'cutover_done'
                                    and summary_tail_buffer
                                ):
                                    logger.info(
                                        "OmniOfflineClient summary: tool 边界 abandon "
                                        "(pre-tool tail %d chars 续给 TTS)",
                                        len(summary_tail_buffer),
                                    )
                                    if self.on_text_delta:
                                        await self.on_text_delta(
                                            summary_tail_buffer, False,
                                            ui_enabled=False, tts_enabled=True,
                                        )
                                summary_state = 'idle'
                                summary_prefix_for_history = ""
                                summary_tail_buffer = ""
                                summary_next_gibberish_check = _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                summary_trigger_tokens = 0
                                summary_overflow_offset = 0
                                continue
                            if not self._is_responding:
                                break

                            if fence_triggered:
                                break

                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                            if think_stripper is not None and content:
                                # Holds CoT until the first </think>; returns "" while
                                # buffering so the empty-content guard below skips it.
                                content = think_stripper.feed(content)

                            if content and content.strip():
                                truncated_content = content

                                # ── 前缀检测阶段：缓冲初始输出，判断是否有角色名前缀 ──
                                if not prefix_checked:
                                    prefix_buffer += truncated_content
                                    if len(prefix_buffer) >= self._prefix_buffer_size:
                                        prefix_checked = True
                                        master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                                        lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                                        if master_match:
                                            guard_triggered = True
                                            discard_reason = "role_hallucination"
                                            logger.info(f"OmniOfflineClient: 检测到主人名前缀 '{prefix_buffer[:master_match]}'，触发重试")
                                            self._is_responding = False
                                            break
                                        elif lanlan_match:
                                            logger.info(f"OmniOfflineClient: 剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                            truncated_content = prefix_buffer[lanlan_match:]
                                        else:
                                            truncated_content = prefix_buffer
                                        # 前缀解析完毕，将结果送入下方的通用 emit/guard 路径
                                        if not (truncated_content and truncated_content.strip()):
                                            continue
                                    else:
                                        continue  # 缓冲区未满，等更多 chunk

                                for idx, char in enumerate(truncated_content):
                                    if char == '|':
                                        pipe_count += 1
                                        if pipe_count >= 2:
                                            truncated_content = truncated_content[:idx]
                                            fence_triggered = True
                                            logger.info("OmniOfflineClient: 围栏触发 - 检测到第二个 | 字符，截断输出")
                                            break

                                if truncated_content and truncated_content.strip():
                                    emit_content = truncated_content
                                    if self.enable_response_guard:
                                        # 长度 guard 看完整一轮（含 pre-tool）的 token 量。
                                        # 必须在 on_text_delta 前裁剪本 chunk，否则 UI/TTS
                                        # 会先收到超限尾巴，而 history 只保存截断文本。
                                        candidate_total = assistant_message_total + truncated_content
                                        current_length = count_tokens(candidate_total)
                                        if current_length > self.max_response_length:
                                            if summary_mode_enabled:
                                                # Summary 路径：长但可读 → 不 abort、不 inline truncate。
                                                # 第一次过线把 state 切到 pending_cutover；从这 chunk 起
                                                # 在每个 chunk 里找 terminator（含逗号），找到就走 cutover。
                                                # 不设 guard_triggered，让 stream 继续到自然终止/3000 cap，
                                                # 由 stream 结束后的 epilogue 决策 abandon / summarize。
                                                if summary_state == 'idle':
                                                    summary_state = 'pending_cutover'
                                                    summary_trigger_tokens = current_length
                                                    # 算 chunk 里"刚好越过 budget"的 char offset：
                                                    # truncate_to_tokens 把 candidate_total 砍到 budget，
                                                    # 差出来的长度就是这一 chunk 里的越线位置。供下面
                                                    # emit fork 从该 offset 起找 terminator，避免误把
                                                    # budget 之前的早期逗号当 cutover。
                                                    _capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    summary_overflow_offset = max(
                                                        0, len(_capped) - len(assistant_message_total),
                                                    )
                                                    logger.info(
                                                        "OmniOfflineClient summary: 长回复触发 "
                                                        "(%d tokens > %d，chunk 内越界 offset=%d)，"
                                                        "等待下一个 terminator",
                                                        current_length, self.max_response_length,
                                                        summary_overflow_offset,
                                                    )
                                                # emit_content 保持原 chunk，下面 emit-split 块继续走
                                            else:
                                                guard_triggered = True
                                                discard_reason = f"length>{self.max_response_length}"
                                                length_guard_original_tokens = current_length
                                                logger.info(f"OmniOfflineClient: 检测到长回复 ({current_length} tokens)，准备停止生成")
                                                self._is_responding = False
                                                emit_content = ""
                                                if not _is_gibberish_response(candidate_total):
                                                    capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                                    if candidate_recovery:
                                                        if candidate_recovery.startswith(assistant_message_total):
                                                            recovery_suffix = candidate_recovery[len(assistant_message_total):]
                                                            if recovery_suffix.strip():
                                                                emit_content = recovery_suffix
                                                                length_guard_recovery_text = candidate_recovery
                                                        elif (
                                                            assistant_message_total
                                                            and _has_unpersisted_recovery_suffix(assistant_message_total)
                                                        ):
                                                            # 已流式发出的前缀无法撤回；保持 history 与
                                                            # UI/TTS 一致，避免可见文本和上下文分叉。
                                                            length_guard_recovery_text = assistant_message_total

                                    if emit_content and emit_content.strip():
                                        # Emit fork：summary 模式下要按 cutover 边界拆分
                                        # UI / TTS 路径。其余场景（含 summary_state == 'idle'
                                        # 与 'gibberish_fallback'）都走 both 默认路径。
                                        if (
                                            summary_mode_enabled
                                            and summary_state in ('pending_cutover', 'cutover_done')
                                        ):
                                            if summary_state == 'pending_cutover':
                                                # Trigger chunk 上 offset > 0 表示越界点在 chunk 中段，
                                                # terminator 搜索从越界点开始；后续 chunk 整段都在
                                                # budget 之后，offset 复位到 0 从头扫。一次性消费。
                                                search_from = summary_overflow_offset
                                                summary_overflow_offset = 0
                                                term_pos_in_slice = _find_summary_terminator(
                                                    emit_content[search_from:]
                                                )
                                                if term_pos_in_slice >= 0:
                                                    term_pos = search_from + term_pos_in_slice
                                                    pre = emit_content[:term_pos + 1]
                                                    post = emit_content[term_pos + 1:]
                                                    if pre:
                                                        assistant_message += pre
                                                        assistant_message_total += pre
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(pre, is_first_chunk)
                                                        is_first_chunk = False
                                                    # 锁定 cutover：当前 assistant_message 即 prefix
                                                    summary_prefix_for_history = assistant_message
                                                    summary_state = 'cutover_done'
                                                    logger.info(
                                                        "OmniOfflineClient summary: cutover 完成 "
                                                        "(prefix_chars=%d, trigger=%d tokens)",
                                                        len(assistant_message), summary_trigger_tokens,
                                                    )
                                                    if post:
                                                        assistant_message += post
                                                        assistant_message_total += post
                                                        summary_tail_buffer += post
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(
                                                                post, is_first_chunk,
                                                                ui_enabled=True, tts_enabled=False,
                                                            )
                                                        is_first_chunk = False
                                                else:
                                                    # 没找到 terminator → 整段走 both，state 不变
                                                    assistant_message += emit_content
                                                    assistant_message_total += emit_content
                                                    if self.on_text_delta:
                                                        await self.on_text_delta(emit_content, is_first_chunk)
                                                    is_first_chunk = False
                                            else:
                                                # cutover_done：UI only，并攒进 tail buffer
                                                assistant_message += emit_content
                                                assistant_message_total += emit_content
                                                summary_tail_buffer += emit_content
                                                if self.on_text_delta:
                                                    await self.on_text_delta(
                                                        emit_content, is_first_chunk,
                                                        ui_enabled=True, tts_enabled=False,
                                                    )
                                                is_first_chunk = False

                                            # cutover_done 后做一次 gibberish 重检（pending→done
                                            # 同 chunk 转换也算）：每 _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                            # tail token 重检一次，命中就跳到 fallback 让 epilogue 走
                                            # RESPONSE_INVALID。
                                            if summary_state == 'cutover_done' and summary_tail_buffer:
                                                tail_tokens = count_tokens(summary_tail_buffer)
                                                if tail_tokens >= summary_next_gibberish_check:
                                                    if _is_gibberish_response(summary_tail_buffer):
                                                        summary_state = 'gibberish_fallback'
                                                        logger.warning(
                                                            "OmniOfflineClient summary: tail gibberish "
                                                            "命中 (%d tokens)，中止本轮生成",
                                                            tail_tokens,
                                                        )
                                                        self._is_responding = False
                                                    else:
                                                        summary_next_gibberish_check = (
                                                            tail_tokens + _SUMMARY_GIBBERISH_RECHECK_TOKENS
                                                        )
                                        else:
                                            assistant_message += emit_content
                                            assistant_message_total += emit_content
                                            if self.on_text_delta:
                                                await self.on_text_delta(emit_content, is_first_chunk)
                                            is_first_chunk = False

                                    if guard_triggered:
                                        break
                                    if summary_state == 'gibberish_fallback':
                                        # 不设 guard_triggered，让 epilogue 走 summary-fallback 路径
                                        break
                            elif content and not content.strip():
                                logger.debug(f"OmniOfflineClient: 过滤空白内容 - content_repr: {repr(content)[:100]}")

                        # 流结束后：先 flush thinking stripper 的残留。仅漏型
                        # provider 的 thinking_on 轮挂了它；若整轮没出现 </think>
                        # （模型本轮没思考），它一直 hold，这里把攒住的正文还回
                        # prefix_buffer，走下面的通用 emit/guard 路径，避免丢答案。
                        if think_stripper is not None:
                            _think_residual = think_stripper.flush()
                            if _think_residual:
                                prefix_buffer += _think_residual
                                # Force the unified flush below to run on this
                                # residual. When prefix checking is disabled
                                # (_prefix_buffer_size == 0) prefix_checked starts
                                # True, so `and not prefix_checked` would otherwise
                                # drop the held answer silently. Safe to clear: a
                                # non-empty residual means no </think> ever arrived,
                                # which only happens when the stripper held the whole
                                # stream → prefix_buffer was never filled by the live
                                # path, so prefix_checked carried no completed state.
                                prefix_checked = False
                        # 流结束后：flush 未处理的前缀缓冲区（走通用 emit/guard 路径）
                        if prefix_buffer and not prefix_checked:
                            prefix_checked = True
                            master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                            lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                            if master_match:
                                guard_triggered = True
                                discard_reason = "role_hallucination"
                                logger.info(f"OmniOfflineClient: 流结束时检测到主人名前缀 '{prefix_buffer[:master_match]}'，触发重试")
                            else:
                                flush_text = prefix_buffer
                                if lanlan_match:
                                    logger.info(f"OmniOfflineClient: 流结束时剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                    flush_text = prefix_buffer[lanlan_match:]
                                # fence + length guard
                                for idx, char in enumerate(flush_text):
                                    if char == '|':
                                        pipe_count += 1
                                        if pipe_count >= 2:
                                            flush_text = flush_text[:idx]
                                            fence_triggered = True
                                            break
                                if flush_text and flush_text.strip():
                                    emit_flush_text = flush_text
                                    if self.enable_response_guard:
                                        # 长度 guard 看整轮（含 pre-tool），与上方主累加块对偶。
                                        candidate_total = assistant_message_total + flush_text
                                        current_length = count_tokens(candidate_total)
                                        if current_length > self.max_response_length:
                                            if summary_mode_enabled:
                                                # 与主累加块对偶：summary 模式下不 abort，
                                                # 切到 pending_cutover，下面 emit-split 块处理。
                                                if summary_state == 'idle':
                                                    summary_state = 'pending_cutover'
                                                    summary_trigger_tokens = current_length
                                                    # 算 flush_text 内的越界 char offset（与主累加块对偶）
                                                    _capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    summary_overflow_offset = max(
                                                        0, len(_capped) - len(assistant_message_total),
                                                    )
                                                    logger.info(
                                                        "OmniOfflineClient summary: 长回复触发于 flush "
                                                        "(%d tokens > %d，flush 内越界 offset=%d)",
                                                        current_length, self.max_response_length,
                                                        summary_overflow_offset,
                                                    )
                                            else:
                                                guard_triggered = True
                                                discard_reason = f"length>{self.max_response_length}"
                                                length_guard_original_tokens = current_length
                                                emit_flush_text = ""
                                                if not _is_gibberish_response(candidate_total):
                                                    capped = truncate_to_tokens(
                                                        candidate_total, self.max_response_length,
                                                    )
                                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                                    if candidate_recovery:
                                                        if candidate_recovery.startswith(assistant_message_total):
                                                            recovery_suffix = candidate_recovery[len(assistant_message_total):]
                                                            if recovery_suffix.strip():
                                                                emit_flush_text = recovery_suffix
                                                                length_guard_recovery_text = candidate_recovery
                                                        elif (
                                                            assistant_message_total
                                                            and _has_unpersisted_recovery_suffix(assistant_message_total)
                                                        ):
                                                            length_guard_recovery_text = assistant_message_total
                                    if emit_flush_text and emit_flush_text.strip():
                                        # Emit fork（与主累加块对偶）：summary 模式下按 cutover 拆 UI/TTS
                                        if (
                                            summary_mode_enabled
                                            and summary_state in ('pending_cutover', 'cutover_done')
                                        ):
                                            if summary_state == 'pending_cutover':
                                                # 与主累加块对偶：消费 trigger flush 的越界 offset
                                                search_from = summary_overflow_offset
                                                summary_overflow_offset = 0
                                                term_pos_in_slice = _find_summary_terminator(
                                                    emit_flush_text[search_from:]
                                                )
                                                if term_pos_in_slice >= 0:
                                                    term_pos = search_from + term_pos_in_slice
                                                    pre = emit_flush_text[:term_pos + 1]
                                                    post = emit_flush_text[term_pos + 1:]
                                                    if pre:
                                                        assistant_message += pre
                                                        assistant_message_total += pre
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(pre, is_first_chunk)
                                                        is_first_chunk = False
                                                    summary_prefix_for_history = assistant_message
                                                    summary_state = 'cutover_done'
                                                    # 对偶 chunk-loop emit fork 的 cutover 日志：
                                                    # 用 summary_trigger_tokens（flush 入口写入的）
                                                    # 把"什么时候触发的"信息留在日志里。
                                                    logger.info(
                                                        "OmniOfflineClient summary: cutover 完成于 flush "
                                                        "(prefix_chars=%d, trigger=%d tokens)",
                                                        len(assistant_message), summary_trigger_tokens,
                                                    )
                                                    if post:
                                                        assistant_message += post
                                                        assistant_message_total += post
                                                        summary_tail_buffer += post
                                                        if self.on_text_delta:
                                                            await self.on_text_delta(
                                                                post, is_first_chunk,
                                                                ui_enabled=True, tts_enabled=False,
                                                            )
                                                        is_first_chunk = False
                                                else:
                                                    assistant_message += emit_flush_text
                                                    assistant_message_total += emit_flush_text
                                                    if self.on_text_delta:
                                                        await self.on_text_delta(emit_flush_text, is_first_chunk)
                                                    is_first_chunk = False
                                            else:
                                                assistant_message += emit_flush_text
                                                assistant_message_total += emit_flush_text
                                                summary_tail_buffer += emit_flush_text
                                                if self.on_text_delta:
                                                    await self.on_text_delta(
                                                        emit_flush_text, is_first_chunk,
                                                        ui_enabled=True, tts_enabled=False,
                                                    )
                                                is_first_chunk = False
                                        else:
                                            assistant_message += emit_flush_text
                                            assistant_message_total += emit_flush_text
                                            if self.on_text_delta:
                                                await self.on_text_delta(emit_flush_text, is_first_chunk)
                                            is_first_chunk = False

                        if guard_triggered:
                            guard_attempt += 1
                            reroll_count += 1
                            will_retry = guard_attempt <= self.max_response_rerolls

                            # max_attempts 报给前端的是**总尝试次数**而非
                            # rerolls 次数（rerolls 不含首次尝试）。前端 attempt
                            # / max_attempts 进度条要 1/2 → 2/2 才合理。
                            total_attempts = self.max_response_rerolls + 1

                            recovery_text = length_guard_recovery_text
                            if discard_reason and "length>" in discard_reason:
                                # 长回复若是正常可读文本，直接按已发出的截断文本
                                # 收尾，不 reroll，避免 UI/TTS 和 history 分叉。
                                if not recovery_text and not _is_gibberish_response(assistant_message_total):
                                    capped = truncate_to_tokens(
                                        assistant_message_total, self.max_response_length,
                                    )
                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                    if _has_unpersisted_recovery_suffix(candidate_recovery):
                                        recovery_text = candidate_recovery

                            if recovery_text and _has_unpersisted_recovery_suffix(recovery_text):
                                history_recovery_text = assistant_message
                                original_tokens = length_guard_original_tokens or count_tokens(assistant_message_total)
                                logger.info(
                                    "OmniOfflineClient: 长回复已流式输出，停止生成并按最后句末入历史 "
                                    "(原 %d tokens → 截断后 %d tokens)",
                                    original_tokens, count_tokens(recovery_text),
                                )
                                if history_recovery_text:
                                    self._conversation_history.append(AIMessage(content=history_recovery_text))
                                await self._check_repetition(recovery_text)
                                assistant_message = history_recovery_text
                                guard_exhausted = True
                                break
                            recovery_text = ""

                            if will_retry:
                                # 还能 retry：发 will_retry 通知，循环继续。前端
                                # 收到 response_discarded(will_retry=True, message=None)
                                # 走 retry toast 路径。
                                await self._notify_response_discarded(
                                    discard_reason or "guard",
                                    guard_attempt,
                                    total_attempts,
                                    True,
                                    None,
                                )
                                logger.info(
                                    "OmniOfflineClient: 响应被丢弃（%s），第 %d/%d 次重试",
                                    discard_reason, guard_attempt, total_attempts,
                                )
                                continue

                            # Reroll 耗尽。length 超长有两类：
                            #   (a) 模型真的写得多但还在正常说话 → 截到最后一个
                            #       句末标点，作为 RESPONSE_LENGTH_TRUNCATED 回复
                            #       发给前端，placeholder 不进 history（截取版进）。
                            #   (b) 模型疯了（BPE 重复 / emoji 刷屏 / 没标点的
                            #       连续乱码）→ 不要试图截"句子"出来，直接 filter
                            #       走 RESPONSE_TOO_LONG（语义=故障），core 那边
                            #       会让前端显示故障 placeholder + 把 placeholder
                            #       写进 history（让下一轮 LLM 知道这一轮失败）。
                            #
                            # 触发 (b) 的条件：_is_gibberish_response（标点/符号
                            # 密度 < 2% 或 > 60%）或截不出句末（整段无 . ! ? 。 ！ ？ …）。
                            #
                            # 关键：(a) 路径要先把 assistant_message 硬截到
                            # max_response_length 再找句末，否则截出来的句末仍
                            # 可能在 token 上限之外（比如最后一个句号在 950 token
                            # 处但 cap 是 300）。
                            if discard_reason and "length>" in discard_reason:
                                # 整轮判定：gibberish / 截断必须看 _total，否则
                                # tool 轮 sentinel 把 final-segment 清空之后整段
                                # pre-tool 被忽略，明明很长却走 RESPONSE_TOO_LONG。
                                if not _is_gibberish_response(assistant_message_total):
                                    capped = truncate_to_tokens(
                                        assistant_message_total, self.max_response_length,
                                    )
                                    candidate_recovery = _truncate_to_last_sentence_end(capped)
                                    if _has_unpersisted_recovery_suffix(candidate_recovery):
                                        recovery_text = candidate_recovery

                            if recovery_text:
                                original_tokens = length_guard_original_tokens or count_tokens(assistant_message_total)
                                logger.info(
                                    "OmniOfflineClient: guard 重试耗尽，截断至最后句末 "
                                    "(原 %d tokens → 截断后 %d tokens)",
                                    original_tokens, count_tokens(recovery_text),
                                )
                                truncate_msg = json.dumps({
                                    "code": "RESPONSE_LENGTH_TRUNCATED",
                                    "text": recovery_text,
                                })
                                # 走 _notify_response_discarded（不能用
                                # on_status_message）：前端在 response_discarded
                                # 分支识别 RESPONSE_LENGTH_TRUNCATED 才能触发
                                # truncate UX（不回滚输入 + 把 truncate text
                                # 当 placeholder body）。
                                await self._notify_response_discarded(
                                    discard_reason or "guard",
                                    guard_attempt,
                                    total_attempts,
                                    False,
                                    truncate_msg,
                                )
                                status_reported = True
                                # _conversation_history 由 core.handle_response_discarded
                                # 在 RESPONSE_LENGTH_TRUNCATED 分支 append
                                # （self.session 即本 OmniOfflineClient，二者共享同一
                                # 个 _conversation_history 列表）。这里只维护内部
                                # 重复检测列表。
                                await self._check_repetition(recovery_text)
                                assistant_message = recovery_text
                                guard_exhausted = True
                                break

                            final_message = json.dumps(
                                {"code": "RESPONSE_TOO_LONG"}
                                if discard_reason and "length>" in discard_reason
                                else {"code": "RESPONSE_INVALID"}
                            )
                            await self._notify_response_discarded(
                                discard_reason or "guard",
                                guard_attempt,
                                total_attempts,
                                False,
                                final_message,
                            )
                            status_reported = True
                            # gibberish 或截不出句末 / 非 length 类 guard 失败 —
                            # 走故障 placeholder 路径，core 会用 locale "fault"
                            # 文案占住 history，避免下一轮 LLM 看到空助手轮次。
                            logger.warning(
                                "OmniOfflineClient: guard 重试耗尽 (reason=%s)，"
                                "filter 输出走故障 placeholder",
                                discard_reason,
                            )
                            assistant_message = ""
                            guard_exhausted = True
                            break

                        # ── Summary 模式 epilogue ──
                        # 走到这里 guard_triggered 一定是 False（summary 路径不设
                        # length 类 guard）。根据 summary_state 决定：
                        #   - gibberish_fallback：tail 被判定胡言乱语，静默截断
                        #     —— 不发 RESPONSE_INVALID，因为那会触发 core 端的
                        #     _clear_tts_pipeline 把还在队列里没读完的 prefix
                        #     音频也清掉，反而让"已经听到的话"被截断。这里只
                        #     log + commit prefix 到 history，TTS 自然把队列
                        #     里残余的 prefix 播完。UI 显示的 gibberish 尾巴
                        #     与 live ≠ reload 的设计分岔本来就允许。
                        #   - cutover_done + 最终长度 ≤ max+slack：太短没必要摘要，把
                        #     tail 直接续给 TTS 读完，history 留完整原文。
                        #   - cutover_done + 最终长度更长：调小模型摘要，TTS 续上摘要，
                        #     history 写 prefix+summary。摘要失败 fallback 到 tail 续读。
                        #   - pending_cutover：触发了但 stream 结束前没找到 terminator
                        #     (整段无标点)。tail 全在主路径里发出去了，相当于没摘要，
                        #     history 写完整原文。
                        #   - idle：从未触发，常规流程，啥也不用做。
                        if summary_mode_enabled and summary_state == 'gibberish_fallback':
                            logger.warning(
                                "OmniOfflineClient summary: gibberish fallback, "
                                "静默 commit prefix (%d chars) 到 history，TTS 残队列保留",
                                len(summary_prefix_for_history),
                            )
                            if summary_prefix_for_history:
                                self._conversation_history.append(
                                    AIMessage(content=summary_prefix_for_history)
                                )
                            # 重复检测只看 prefix（= 真正进 history / 被 TTS 读的部分）。
                            # 用 assistant_message_total 会把判定为乱码、已丢弃的 tail
                            # 也塞进 _recent_responses，污染后续重复判定。
                            if summary_prefix_for_history:
                                await self._check_repetition(summary_prefix_for_history)
                            assistant_message = ""
                            guard_exhausted = True
                            break

                        if summary_mode_enabled and summary_state == 'cutover_done':
                            final_tokens = count_tokens(assistant_message_total)
                            slack_threshold = self.max_response_length + _SUMMARY_LATE_FINISH_SLACK
                            if final_tokens < slack_threshold:
                                # 尾巴太短：放弃摘要，tail 直接续给 TTS。
                                logger.info(
                                    "OmniOfflineClient summary: 最终 %d tokens < %d，放弃摘要，"
                                    "tail (%d chars) 续给 TTS",
                                    final_tokens, slack_threshold, len(summary_tail_buffer),
                                )
                                if summary_tail_buffer and self.on_text_delta:
                                    await self.on_text_delta(
                                        summary_tail_buffer, False,
                                        ui_enabled=False, tts_enabled=True,
                                    )
                            else:
                                summary_text = await self._summarize_tail_for_tts(
                                    prefix=summary_prefix_for_history,
                                    tail=summary_tail_buffer,
                                )
                                if summary_text:
                                    logger.info(
                                        "OmniOfflineClient summary: 摘要成功 "
                                        "(tail=%d chars → summary=%d chars)",
                                        len(summary_tail_buffer), len(summary_text),
                                    )
                                    if self.on_text_delta:
                                        await self.on_text_delta(
                                            summary_text, False,
                                            ui_enabled=False, tts_enabled=True,
                                        )
                                    # history = prefix + summary，与 TTS 听到的对齐
                                    assistant_message = summary_prefix_for_history + summary_text
                                else:
                                    logger.info(
                                        "OmniOfflineClient summary: 摘要失败/为空，"
                                        "tail 续给 TTS 读完"
                                    )
                                    if summary_tail_buffer and self.on_text_delta:
                                        await self.on_text_delta(
                                            summary_tail_buffer, False,
                                            ui_enabled=False, tts_enabled=True,
                                        )
                                    # assistant_message 不动 → history 写完整原文

                        # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
                        # 此处不再手动调用 TokenTracker.record() 避免双重计数。

                        if assistant_message:
                            # final AIMessage 只写未被 inline 持久化的最后一段
                            # （pre-tool 文本已经在前面 ``assistant.tool_calls.content``
                            # 里了，再 append 一次会双写历史）。
                            self._conversation_history.append(AIMessage(content=assistant_message))
                        # 重复检测看完整一轮文本（含 pre-tool），与人类用户感知
                        # 的"这一轮 AI 说了什么"一致。
                        if assistant_message_total:
                            await self._check_repetition(assistant_message_total)
                        break
                    
                    if guard_exhausted:
                        break

                    # 整轮判定：本轮只要产生过任何文本（含 pre-tool）就算成功完成
                    # retry 循环；用 final-segment 会让"max_tool_iterations 用尽
                    # 时只剩 pre-tool 被持久化、没出 final 回复"的轮次被错误重试。
                    if assistant_message_total:
                        break

                except _llm_retry_error_types() as e:
                    from openai import InternalServerError

                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    is_internal_error = isinstance(e, InternalServerError)
                    logger.info(f"ℹ️ 捕获到 {error_type} 错误")

                    def _count_llm_error(api_key_rejected: bool = False):
                        # D1 失败诊断：typed API 错误（连接/认证/限流/欠费/配额/
                        # key 拒绝）的**终态**也要计入 llm_error。只在给上的 break
                        # 路径调，不在 retry-continue 调（重试中不算失败）。generic
                        # except 与本块互斥，不会双计（Codex）。
                        try:
                            from utils.instrument import counter as _ic
                            # before_first_loop：错误发生在用户体验到核心 loop 之前 =
                            # 首次体验障碍型流失（开了口但没收到回复）。true/false/unknown
                            # 低基数；区分"卡在首次体验"vs"用过之后才报错"两类 D1 流失。
                            try:
                                from utils.token_tracker import TokenTracker as _TT
                                _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                            except Exception:
                                _bfl = "unknown"
                            _ic("llm_error", error_class=error_type[:48], before_first_loop=_bfl)
                            if api_key_rejected:
                                _ic("api_key_invalid", before_first_loop=_bfl)
                        except Exception:
                            # 埋点 best-effort，绝不影响错误上报 / 重试主流程。
                            pass

                    # 欠费/API Key 错误立即上报并终止；配额错误上报但继续重试
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"OmniOfflineClient: 检测到欠费错误，直接上报: {e}")
                        _count_llm_error()
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                            status_reported = True
                        break
                    elif _is_api_key_rejected_error(e):
                        logger.error(f"OmniOfflineClient: 检测到 API Key 错误，直接上报: {e}")
                        _count_llm_error(api_key_rejected=True)
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                            status_reported = True
                        break
                    elif 'quota' in error_str_lower or 'time limit' in error_str_lower:
                        logger.warning(f"OmniOfflineClient: 检测到配额错误，上报前端: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_QUOTA_TIME"}))

                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(f"OmniOfflineClient: LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                        # 整轮判定：本轮是否吐过任何文本到前端 —— 用 _total 才能
                        # 覆盖 tool_round_persisted 已重置 final-segment 的场景。
                        # 否则 pre-tool 文本残留在前端但 notify_discarded 漏触发。
                        if assistant_message_total and self.on_response_discarded:
                            await self._notify_response_discarded(
                                f"api_error:{error_type}",
                                attempt + 1,
                                max_retries,
                                will_retry=True,
                                message=None,
                            )
                        assistant_message = ""
                        assistant_message_total = ""
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_msg = f"💥 LLM连接失败（{error_type}），已重试{max_retries}次: {e}"
                        logger.error(error_msg)
                        _count_llm_error()  # 重试耗尽 = 终态失败，计入 llm_error
                        if self.on_status_message:
                            if is_internal_error:
                                await self.on_status_message(json.dumps({"code": "LLM_UPSTREAM_ERROR"}))
                            else:
                                await self.on_status_message(json.dumps({"code": "LLM_CONNECTION_EXHAUSTED", "details": {"error_type": error_type, "max_retries": max_retries, "error": str(e)}}))
                            status_reported = True
                        break
                except Exception as e:
                    is_api_key_rejected = _is_api_key_rejected_error(e)
                    # Telemetry：D1 流失里 LLM 调用失败是大头。error_class 低基数
                    # （exception 类名）；api_key_invalid 单独计——首日配错 key
                    # 是源码版用户的常见流失坑。
                    try:
                        from utils.instrument import counter as _instr_counter
                        # before_first_loop 与 typed 错误路径（_count_llm_error）保持
                        # 同维度，避免 llm_error/api_key_invalid 混合标签拆裂 D1 分桶。
                        try:
                            from utils.token_tracker import TokenTracker as _TT
                            _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                        except Exception:
                            _bfl = "unknown"
                        _instr_counter("llm_error", error_class=type(e).__name__[:48], before_first_loop=_bfl)
                        if is_api_key_rejected:
                            _instr_counter("api_key_invalid", before_first_loop=_bfl)
                    except Exception:
                        # 埋点 best-effort，绝不掩盖/打断原始 LLM 错误的处理路径。
                        pass
                    if is_api_key_rejected:
                        status_error_payload = {"code": "API_KEY_REJECTED"}
                        discard_error_payload = status_error_payload
                        error_msg = f"💥 文本生成异常: 检测到 API Key 被拒绝: {type(e).__name__}: {e}"
                    else:
                        status_error_payload = {
                            "code": "TEXT_GEN_ERROR",
                            "details": {
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                        }
                        discard_error_payload = {
                            "code": "TEXT_GEN_ERROR_AFTER_PARTIAL",
                            "details": {
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                        }
                        error_msg = f"💥 文本生成异常: {type(e).__name__}: {e}"
                    logger.error(error_msg)
                    # 如果本轮已经向前端吐过文本（典型场景：genai 路径在
                    # _astream_with_tools 已吐文本后再抛 transient/tools-
                    # unsupported，被显式 raise 上来），必须通知前端清空
                    # 那截半截气泡，否则用户会看到一段被中断的文本永远停
                    # 在那。和 (APIConnectionError 等) 分支语义对偶，但
                    # 这条路径已经决定不再重试（break 在下面），所以
                    # ``will_retry=False``，并附带可读的错误码到前端。
                    # 整轮判定：用 _total，覆盖 tool_round_persisted 已重置
                    # final-segment 但 pre-tool 文本仍在前端的场景。
                    if assistant_message_total and self.on_response_discarded:
                        try:
                            await self._notify_response_discarded(
                                f"text_gen_error:{type(e).__name__}",
                                attempt + 1,
                                max_retries,
                                will_retry=False,
                                message=json.dumps(discard_error_payload),
                            )
                            status_reported = True
                        except Exception as _notify_err:
                            logger.warning(
                                "通知 response_discarded(after partial) 失败: %s",
                                _notify_err,
                            )
                    if not status_reported and self.on_status_message:
                        await self.on_status_message(json.dumps(status_error_payload))
                        status_reported = True
                    break
        finally:
            self._is_responding = False

            if (
                history_replacement_text
                and 0 <= history_replacement_index < len(self._conversation_history)
                and self._conversation_history[history_replacement_index] is user_message
            ):
                self._conversation_history[history_replacement_index] = HumanMessage(
                    content=history_replacement_text
                )

            # 还原 summary 模式临时抬高的 API budget，别泄漏给 prompt_ephemeral。
            if _summary_prev_max_tokens is not None and getattr(self, "llm", None) is not None:
                self.llm.max_completion_tokens = _summary_prev_max_tokens

            # 整轮判定：所有重试都没产生过任何文本（包括 pre-tool）才算 LLM_NO_RESPONSE。
            # 用 final-segment 会让"tool 轮跑完了但模型没出 final 文本"的场景被错报。
            if not assistant_message_total and not guard_exhausted and not status_reported:
                # 把最后一次 attempt 的 finish_reason / block_reason / prompt_tokens
                # 拼进 warning。Gemini-via-OpenAI-compat 静默 empty 时（safety /
                # recitation / max_tokens / 上下文超限），这条 log 是日志里能拿到
                # 的唯一"为什么 empty"线索。
                logger.warning(
                    "OmniOfflineClient: 所有重试均未产生文本回复 "
                    "(finish_reason=%s block_reason=%s prompt_tokens=%s model=%s)",
                    getattr(self, "_last_finish_reason", None),
                    getattr(self, "_last_block_reason", None),
                    getattr(self, "_last_prompt_tokens", None),
                    getattr(self, "model", None),
                )
                if self.on_status_message:
                    finish_reason = getattr(self, "_last_finish_reason", None)
                    block_reason = getattr(self, "_last_block_reason", None)
                    prompt_tokens = getattr(self, "_last_prompt_tokens", None)
                    model = getattr(self, "model", None)
                    if _is_safety_violation_signal(finish_reason, block_reason):
                        await self.on_status_message(json.dumps({
                            "code": "API_POLICY_VIOLATION",
                            "details": {
                                "msg": "LLM completion was blocked by upstream safety policy.",
                                "finish_reason": finish_reason,
                                "block_reason": block_reason,
                                "prompt_tokens": prompt_tokens,
                                "model": model,
                            },
                        }))
                    else:
                        await self.on_status_message(json.dumps({"code": "LLM_NO_RESPONSE"}))
            
            # Call response done callback
            if self.on_response_done:
                await self.on_response_done()
    
    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Compatibility method - not used in text mode"""
        pass
    
    async def stream_image(self, image_b64: str, *, bypass_rate_limit: bool = False) -> None:
        """
        Add an image to pending images queue.
        Images will be sent together with the next text message.

        ``bypass_rate_limit`` is accepted for signature parity with the
        realtime client (text mode has no frame-rate throttle — it's an
        in-memory append) and is ignored here.
        """
        if not image_b64:
            return
        
        # Store base64 image
        self._pending_images.append(image_b64)
        logger.info(f"Added image to pending queue (total: {len(self._pending_images)})")
    
    def has_pending_images(self) -> bool:
        """Check if there are pending images waiting to be sent."""
        return len(self._pending_images) > 0

    def set_proactive_screenshot(self, image_b64: str | None) -> None:
        """Stage (or clear) the proactive-vision screenshot for the user's next reply.

        When proactive chat used the screen as its material, the committed
        AIMessage carries only text, so the conversation model can't see what
        was on screen when the user replies. This stashes that screenshot so the
        NEXT ``stream_text`` folds it in as leading visual context — symmetric
        with how ``_pending_images`` carries the user's own frame, but kept in a
        SEPARATE single-slot field: sharing ``_pending_images`` would steal the
        user's next frame (see core.py proactive media note / Codex P2).

        Pass ``None`` (e.g. a proactive round that obtained no screenshot) to
        clear, so the slot always reflects the most recent proactive round and a
        stale screenshot never trails a later talk. The stage timestamp arms the
        TTL (``_PROACTIVE_SCREENSHOT_TTL_SECONDS``) checked lazily at injection;
        the history-length marker pins the screenshot to the AI turn it was staged
        on, so a later proactive talk delivered through another path (greeting /
        agent callback via ``prompt_ephemeral``) supersedes it.
        """
        if image_b64:
            self._proactive_image_to_inject = image_b64
            self._proactive_image_staged_at = time.monotonic()
            self._proactive_image_history_len = len(self._conversation_history)
        else:
            self._proactive_image_to_inject = None
            self._proactive_image_staged_at = 0.0
            self._proactive_image_history_len = 0

    def _evict_old_images(self, keep_turns: int = 2) -> None:
        # 只保留最近 keep_turns 个含图 HumanMessage 的图片，更早的剥掉 image_url
        # 仅留文本。base64 图片在 vision tokenizer 下约 1.5k~3k tokens/张，
        # 多轮累积会把 input 推到 128k+。
        image_turn_indices = [
            idx for idx, msg in enumerate(self._conversation_history)
            if isinstance(msg, HumanMessage) and isinstance(msg.content, list)
            and any(isinstance(item, dict) and item.get("type") == "image_url" for item in msg.content)
        ]
        if len(image_turn_indices) <= keep_turns:
            return

        evicted_imgs = 0
        for idx in image_turn_indices[:-keep_turns]:
            old = self._conversation_history[idx]
            kept_parts = []
            for item in old.content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    evicted_imgs += 1
                else:
                    kept_parts.append(item)
            if len(kept_parts) == 1 and isinstance(kept_parts[0], dict) and kept_parts[0].get("type") == "text":
                self._conversation_history[idx] = HumanMessage(content=kept_parts[0].get("text", ""))
            else:
                self._conversation_history[idx] = HumanMessage(content=kept_parts)

        logger.info(
            f"🖼️ Evicted {evicted_imgs} image(s) from {len(image_turn_indices) - keep_turns} older turn(s); "
            f"kept images in last {keep_turns} turn(s)"
        )
    
    # ------------------------------------------------------------------
    # LLM message injection channels
    #
    # There are three distinct channels for injecting content into the
    # LLM context.  Each has different persistence and triggering
    # semantics.  Callers should pick the right one:
    #
    #   prime_context(text, skipped)
    #       Session-start context priming.  Appends *text* to the system
    #       prompt (position 0 in _conversation_history).  Used during
    #       hot-swap to inject incremental conversation cache and task
    #       summaries into a freshly created session.  The text becomes
    #       part of the permanent system prompt.
    #       Typical caller: core._perform_final_swap_sequence()
    #
    #   create_response(text, skipped)
    #       Mid-conversation persistent message.  Appends a HumanMessage
    #       to _conversation_history so the instruction and its reply
    #       both persist across turns.  Mirrors the OpenAI Realtime API's
    #       conversation.item.create + response.create pattern.
    #       No active callers at present; kept as a stable interface.
    #
    #   prompt_ephemeral(instruction)
    #       Fire-and-forget instruction.  The instruction is sent to the
    #       LLM together with the conversation history but is NOT saved;
    #       only the AI's response (AIMessage) is persisted.  Used for
    #       agent task notifications, greetings, and other proactive
    #       messages where the instruction is a stage direction that
    #       should not pollute long-term context.
    #       Typical callers: core.trigger_agent_callbacks(),
    #                        core.trigger_greeting()
    # ------------------------------------------------------------------

    async def prime_context(self, text: str, skipped: bool = False) -> None:
        """Append context to the system prompt at session start.

        Called during hot-swap to inject incremental conversation cache
        and/or task summaries into a freshly created session.  The *text*
        is concatenated to the existing SystemMessage at position 0 —
        format naturally continues the ``role | text`` lines already
        present in the initial prompt, followed by ``======`` delimiters.

        This method MUST only be called before any user interaction on the
        session (i.e. the conversation history contains only the initial
        SystemMessage from ``connect()``).

        Args:
            text: Context to append (incremental cache + summary/ready).
            skipped: Accepted for interface compatibility with
                     OmniRealtimeClient but not implemented in the
                     offline (text-mode) path.
        """
        if not text or not text.strip():
            return

        if self._conversation_history and isinstance(self._conversation_history[0], SystemMessage):
            self._conversation_history[0] = SystemMessage(
                content=self._conversation_history[0].content + text
            )
        else:
            # Defensive: should never happen — connect() always sets [0].
            self._conversation_history.insert(0, SystemMessage(content=text))

    async def create_response(self, instructions: str, skipped: bool = False) -> None:
        """Inject a persistent message and trigger an LLM response.

        Appends *instructions* as a HumanMessage to the conversation
        history.  Both the instruction and the LLM's reply persist across
        turns.  This mirrors the OpenAI Realtime API's
        ``conversation.item.create`` (role=user) + ``response.create``
        pattern.

        Unlike ``prime_context`` (system-prompt level, session start only)
        and ``prompt_ephemeral`` (instruction discarded after response),
        messages injected here become permanent conversation history.

        No active callers at present; kept as a stable interface for
        future mid-conversation injection needs.

        Args:
            instructions: Text to inject as a HumanMessage.
            skipped: Accepted for interface compatibility with
                     OmniRealtimeClient but not implemented in the
                     offline (text-mode) path.
        """
        if instructions and instructions.strip():
            self._conversation_history.append(HumanMessage(content=instructions))
    
    async def prompt_ephemeral(
        self,
        instruction: str,
        *,
        images: Optional[list] = None,
        completion_mode: str = "proactive",
        persist_response: bool = True,
        on_committed: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Send a fire-and-forget instruction to the LLM and stream the response.

        The *instruction* (typically wrapped in ``======...======`` delimiters)
        is appended as a temporary HumanMessage for this single LLM call
        but is **not** persisted to ``_conversation_history``.  The
        AI's natural-language response (AIMessage) is kept in history only
        when ``persist_response`` is True.

        This is the correct channel for agent task notifications, greeting
        nudges, and any scenario where the AI should respond to a stage
        direction that must not pollute long-term context.

        Unlike ``prime_context`` (appends to system prompt, session start)
        and ``create_response`` (persistent HumanMessage), the instruction
        here is truly ephemeral — it exists only for the duration of this
        single LLM inference call.

        Completion behaviour is caller-selectable:

        - ``completion_mode="proactive"``:
          Uses ``on_proactive_done(content_committed)`` when available.
          This keeps the existing lightweight proactive / agent-callback
          completion path while exposing whether any content was actually
          emitted.
        - ``completion_mode="response"``:
          Uses ``on_response_done()`` so the reply goes through the
          regular user-visible completion path while still keeping the
          injected instruction itself ephemeral.
        - ``on_committed``:
          Called after visible text is confirmed but before completion
          callbacks flush proactive state.

        Returns True if any user-visible text was generated, False if aborted
        or only nonverbal directives were emitted.
        """
        if not instruction or not instruction.strip():
            return False

        # 临时注入：instruction 已由调用方用 ======== 格式封装，作为 HumanMessage 发送，
        # 不持久化到 _conversation_history，避免污染长期上下文。
        # Proactive media is passed EXPLICITLY via ``images`` (per-callback,
        # carried on cb.media_images by the caller) — it is NOT pulled from
        # self._pending_images. _pending_images is the USER's screen/camera
        # staging queue for the next stream_text; consuming it here would steal
        # the user's pending frame into this proactive/greeting turn and rob the
        # user's next message of its visual context (Codex P2). When proactive
        # images are present we switch to the vision model exactly like
        # stream_text does (一旦带图就永久切 vision — 既定设计；vision model 也能跑
        # 后续纯文本轮). The instruction itself stays ephemeral (not persisted).
        if images:
            # 一旦带图就永久切到 vision model（既定设计，见上）。vision model 也能
            # 跑后续纯文本轮，且凝神不再因 vision 而关闭思考。
            if self.vision_model and self.vision_model != self.model:
                logger.info(
                    f"🖼️ prompt_ephemeral: switching to vision model {self.vision_model} (from {self.model}) for proactive media"
                )
                await self.switch_model(self.vision_model, use_vision_config=True)
            _ephemeral_content: list = []
            for img_b64 in images:
                _ephemeral_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                })
            _ephemeral_content.append({"type": "text", "text": instruction})
            logger.info(f"prompt_ephemeral: attaching {len(images)} proactive image(s)")
            _ephemeral_msg = HumanMessage(content=_ephemeral_content)
        else:
            _ephemeral_msg = HumanMessage(content=instruction)
        messages_to_send = self._conversation_history + [_ephemeral_msg]

        # Retry 策略与 stream_text 对偶（max_retries=3, [1, 2]s 间隔）。
        # 但主动搭话语义不同：用户没在等回复，retry 用尽时**静默吞掉**，
        # 不发任何 status_message 给前端 —— 失败 = 这一轮 AI 根本没想说话。
        # 唯一例外：欠费 / API Key / 配额这类账户级错误必须上报，否则用户
        # 永远不知道为什么主动搭话不工作。
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""
        # Empty-completion 诊断重置：与 stream_text 对偶。
        self._last_finish_reason = None
        self._last_block_reason = None
        self._last_prompt_tokens = None
        # Open a new reasoning-pulse scope like stream_text does and capture the
        # ownership token: the finally clear below must fire ONLY for this turn's
        # own pulse, never for a newer user stream_text that interleaved and
        # re-pulsed under a fresher seq (Codex P2).
        _reasoning_owner_seq = self._begin_reasoning_stream()

        try:
            self._is_responding = True
            set_call_type("proactive")
            for attempt in range(max_retries):
                # 每次 attempt 重置流式状态（assistant_message / prefix /
                # is_first_chunk 全部归零）。
                assistant_message = ""
                is_first_chunk = True
                prefix_buffer = ""
                prefix_checked = not bool(self._prefix_buffer_size)
                emitted_any = False  # 本 attempt 是否已经向前端 emit 过文本

                # close() 是唯一会把 self.llm 设为 None 的路径。它若在前一次
                # APIConnectionError 后的 retry sleep 期间触发（用户切模式 /
                # 断连 / session 熔断），不再做这次 attempt —— 否则会对 None
                # 调 .astream 触发 AttributeError，且就算重试 client 也已不在。
                # 用 hasattr 守卫：单元测试用 __new__ 绕过 __init__ 不会设这个
                # 属性，但真实代码 __init__ 必设。
                if (hasattr(self, "llm") and self.llm is None) or not self._is_responding:
                    break

                try:
                    # 主动搭话同样走 tool-aware streaming —— agent 注入的 stage
                    # direction 也可能让模型决定调用工具（比如 "讲一下今天天气"）。
                    async for chunk in self._astream_visible_with_tools(messages_to_send):
                        if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                            logger.debug(f"🔍 [Usage-Proactive] {chunk.usage_metadata}")
                        if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                            if 'token_usage' in chunk.response_metadata or 'usage' in chunk.response_metadata:
                                logger.debug(f"🔍 [Meta-Proactive] {chunk.response_metadata}")

                        if not self._is_responding:
                            break
                        content = chunk.content if hasattr(chunk, "content") else str(chunk)
                        if content and content.strip():
                            emit_content = content

                            # ── 前缀检测阶段：缓冲初始输出，剥离角色名前缀 ──
                            if not prefix_checked:
                                prefix_buffer += emit_content
                                if len(prefix_buffer) >= self._prefix_buffer_size:
                                    prefix_checked = True
                                    master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                                    lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                                    if master_match:
                                        logger.info(f"OmniOfflineClient.prompt_ephemeral: 剥离主人名前缀 '{prefix_buffer[:master_match]}'")
                                        emit_content = prefix_buffer[master_match:]
                                    elif lanlan_match:
                                        logger.info(f"OmniOfflineClient.prompt_ephemeral: 剥离角色名前缀 '{prefix_buffer[:lanlan_match]}'")
                                        emit_content = prefix_buffer[lanlan_match:]
                                    else:
                                        emit_content = prefix_buffer
                                    if not (emit_content and emit_content.strip()):
                                        continue
                                else:
                                    continue  # 缓冲区未满，等更多 chunk

                            assistant_message += emit_content
                            if self.on_text_delta:
                                await self.on_text_delta(emit_content, is_first_chunk)
                            is_first_chunk = False
                            emitted_any = True

                    # ── flush 前缀缓冲区（流提前结束时） ──
                    if prefix_buffer and not prefix_checked:
                        prefix_checked = True
                        master_match = self._match_name_prefix(prefix_buffer, self.master_name)
                        lanlan_match = self._match_name_prefix(prefix_buffer, self.lanlan_name)
                        if master_match:
                            logger.info("OmniOfflineClient.prompt_ephemeral: 流结束时剥离主人名前缀")
                            flush_text = prefix_buffer[master_match:]
                        elif lanlan_match:
                            logger.info("OmniOfflineClient.prompt_ephemeral: 流结束时剥离角色名前缀")
                            flush_text = prefix_buffer[lanlan_match:]
                        else:
                            flush_text = prefix_buffer
                        if flush_text and flush_text.strip():
                            assistant_message += flush_text
                            if self.on_text_delta:
                                await self.on_text_delta(flush_text, is_first_chunk)
                            is_first_chunk = False
                            emitted_any = True

                    break  # 流正常结束，跳出 retry 循环

                except _llm_retry_error_types() as e:
                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    logger.info(f"ℹ️ prompt_ephemeral 捕获到 {error_type} 错误")

                    # 账户级错误必须上报：欠费 / API Key 直接放弃 retry，
                    # 配额错误上报后继续 retry（与 stream_text 对偶）。
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"prompt_ephemeral: 检测到欠费错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                        assistant_message = ""
                        return False
                    elif _is_api_key_rejected_error(e):
                        logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                        assistant_message = ""
                        return False
                    elif 'quota' in error_str_lower or 'time limit' in error_str_lower:
                        logger.warning(f"prompt_ephemeral: 检测到配额错误，上报前端: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_QUOTA_TIME"}))

                    # 已经吐过文本就不能再 retry —— 否则前端会拼出"半截 + 重新生成"
                    # 的怪异回复。直接 break 让半截文本走 finally 的 persist 路径。
                    if emitted_any:
                        logger.info(
                            "prompt_ephemeral: %s 发生时已 emit 文本，放弃 retry",
                            error_type,
                        )
                        break

                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(
                            "prompt_ephemeral: LLM 调用失败 (尝试 %d/%d)，%d 秒后重试: %s",
                            attempt + 1, max_retries, wait_time, error_type,
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    # Retry 用尽：B 部分语义 —— 静默放弃。主动搭话失败用户
                    # 不需要知道，只 log 一条 warning（截断 str(e) 防 HTML
                    # 错误页淹没日志）。
                    logger.warning(
                        "prompt_ephemeral: %s 重试 %d 次后仍失败，静默放弃: %s",
                        error_type, max_retries, str(e)[:200],
                    )
                    assistant_message = ""
                    return False
        except Exception as e:
            if _is_api_key_rejected_error(e):
                logger.error(f"prompt_ephemeral: 检测到 API Key 错误，直接上报: {e}")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "API_KEY_REJECTED"}))
                assistant_message = ""
                return False
            # 兜底：非 API 错误（编程错误 / 数据异常）静默吞掉，截断错误文本
            # 防 HTML 错误页之类淹没日志。和上方 (APIConnectionError 等) 分支
            # 语义对偶 —— 都不向前端发 status_message。
            logger.error(
                "OmniOfflineClient.prompt_ephemeral 未分类异常 %s: %s",
                type(e).__name__, str(e)[:200],
                exc_info=True,
            )
            assistant_message = ""
            return False
        finally:
            self._is_responding = False
            # Clear the thinking bubble if this proactive/greeting/avatar turn
            # pulsed it but committed no visible text — unlike stream_text, there
            # is no external unconditional clear bracketing this call (Codex P2).
            # Passing the owner seq suppresses the clear when a newer user turn
            # interleaved and re-pulsed. No-op when nothing pulsed or it was
            # already cleared on the first visible token (idempotent).
            await self._notify_reasoning_done(_reasoning_owner_seq)
            # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
            # 此处不再手动调用 TokenTracker.record() 避免双重计数。
            committed_text = _strip_nonverbal_directives(assistant_message).strip()
            content_committed = bool(committed_text)
            # 一条可见的 ephemeral 回复（greeting / agent 回调 / 戳头像的 quip）是
            # 用户接下来要回应的「新一条 AI 轮」，它让之前为「下一条用户回复」暂存的
            # 屏幕截图过时——清掉它。persist_response=False 的回复（如头像 quip）不进
            # 历史、历史长度不变，stream_text 的 history-len marker 看不到，必须在这条
            # ephemeral 回复的 choke point 清（Codex P2）。只在真吐了可见文本时清，
            # 半途 abort / 无文本的尝试不丢一张仍有效的暂存屏。
            if content_committed:
                self._proactive_image_to_inject = None
                self._proactive_image_staged_at = 0.0
                self._proactive_image_history_len = 0
            # Empty-completion 诊断：和 stream_text 的兜底 warning 对偶。
            # 主动搭话语义上是"静默放弃"，所以不发 status_message，但 INFO
            # 一行 finish_reason 让日志能复盘——上次出问题就是因为没法区分
            # "trigger_greeting 静默失败 = LLM 被 safety 拦" vs "LLM 真的觉得
            # 这一轮不该说话"。
            if not content_committed:
                logger.info(
                    "OmniOfflineClient.prompt_ephemeral: 无可提交文本 "
                    "(finish_reason=%s block_reason=%s prompt_tokens=%s model=%s "
                    "completion_mode=%s)",
                    getattr(self, "_last_finish_reason", None),
                    getattr(self, "_last_block_reason", None),
                    getattr(self, "_last_prompt_tokens", None),
                    getattr(self, "model", None),
                    completion_mode,
                )
            elif on_committed:
                try:
                    on_committed()
                except Exception:
                    logger.exception("prompt_ephemeral on_committed callback failed")
            if content_committed and persist_response:
                self._conversation_history.append(AIMessage(content=assistant_message))
                # 防复读 corpus：只录常规 reply（completion_mode == "response"）。
                # proactive 路径已经在 ``core.finish_proactive_delivery`` 上录，
                # 这里再录会双写——这两条路径都接得到同一段 assistant 文本。
                if completion_mode == "response":
                    try:
                        from memory.anti_repeat import get_anti_repeat_corpus
                        get_anti_repeat_corpus().record_output(
                            self.lanlan_name, committed_text, is_proactive=False,
                        )
                    except Exception as _exc:  # pragma: no cover
                        logger.debug(
                            "[AntiRepeat] record reply skipped: %s", _exc,
                        )
            if completion_mode == "response":
                if self.on_response_done:
                    await self.on_response_done()
            else:
                proactive_done_cb = getattr(self, "on_proactive_done", None)
                if proactive_done_cb:
                    await proactive_done_cb(content_committed)
                elif self.on_response_done:
                    await self.on_response_done()

        return content_committed

    async def cancel_response(self) -> None:
        """Cancel the current response if possible"""
        self._is_responding = False
        # Stop processing new chunks by setting flag
    
    async def handle_interruption(self):
        """Handle user interruption - cancel current response"""
        if not self._is_responding:
            return
        
        logger.info("Handling text mode interruption")
        await self.cancel_response()
    
    async def handle_messages(self) -> None:
        """
        Compatibility method for OmniRealtimeClient interface.
        In text mode, this is a no-op as we don't have a persistent connection.
        """
        # Keep this task alive to match the interface
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Text mode message handler cancelled")
    
    async def close(self) -> None:
        """Close the client and cleanup resources."""
        self._is_responding = False
        self._conversation_history = []
        self._pending_images.clear()
        self._proactive_image_to_inject = None
        self._proactive_image_staged_at = 0.0
        self._proactive_image_history_len = 0
        if self.llm:
            try:
                await self.llm.aclose()
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: aclose failed: {e}")
            self.llm = None
        # 同 switch_model：genai.Client 持有 httpx 连接池，关掉它的
        # 同步 close()（SDK 没暴露 aclose，放 to_thread 不阻事件循环）。
        if self._genai_client is not None and hasattr(self._genai_client, "close"):
            try:
                await asyncio.to_thread(self._genai_client.close)
            except Exception as e:
                logger.warning(f"OmniOfflineClient.close: genai client close failed: {e}")
            self._genai_client = None
        self._genai_tools_unsupported = False
        logger.info("OmniOfflineClient closed")
