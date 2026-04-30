# -- coding: utf-8 --

import asyncio
import json
import re
import time
from typing import Optional, Callable, Dict, Any, Awaitable, List
from utils.llm_client import SystemMessage, HumanMessage, AIMessage, create_chat_llm
from openai import APIConnectionError, InternalServerError, RateLimitError
from utils.frontend_utils import calculate_text_similarity
from utils.tokenize import count_tokens, truncate_to_tokens
from config import OMNI_RECENT_RESPONSES_MAX
from main_logic.tool_calling import (
    OnToolCallCallback,
    ToolCall,
    ToolDefinition,
    ToolResult,
    parse_arguments_json,
)

# Lazy-import flag for google-genai (offline Gemini path). The SDK is already
# imported by omni_realtime_client at module load; we duplicate the guard
# here so the offline client can degrade gracefully if it isn't available.
try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _GENAI_AVAILABLE = True
except Exception:  # pragma: no cover — environment-specific
    _genai = None
    _genai_types = None
    _GENAI_AVAILABLE = False


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


def _budget_to_max_tokens(budget: int) -> int | None:
    """Convert ``max_response_length`` budget into the LLM API's
    ``max_completion_tokens``. ``None`` for the unlimited sentinel so the
    request omits the field entirely (large fixed values get rejected as
    out-of-range by some providers)."""
    if budget >= _UNLIMITED_BUDGET:
        return None
    return budget + _MAX_TOKENS_SLACK


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
    if not _GENAI_AVAILABLE:
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
    if not _GENAI_AVAILABLE:
        return False
    bl = (base_url or "").lower()
    ml = (model or "").lower()
    if any(h in bl for h in _GENAI_NATIVE_BASE_URL_HINTS):
        return True
    if not bl and any(h in ml for h in _GENAI_NATIVE_MODEL_HINTS):
        return True
    return False

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
        voice: str = "",  # Unused for text mode but kept for compatibility
        turn_detection_mode = None,  # Unused for text mode
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
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
        max_tool_iterations: int = 6,
    ):
        # Use base_url directly without conversion
        self.base_url = base_url
        self.api_key = api_key if api_key and api_key != '' else None
        self.model = model
        self.vision_model = vision_model  # Store vision model for temporary switching
        # 视觉模型独立配置（如果未指定则回退到主配置）
        self.vision_base_url = vision_base_url if vision_base_url else base_url
        self.vision_api_key = vision_api_key if vision_api_key else api_key
        self.on_text_delta = on_text_delta
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

        # Initialize ChatOpenAI client. max_completion_tokens 设为
        # max_response_length + 20 让 LLM API 自然在 budget+20 token 处停下来，
        # 既省掉无效生成成本，又给 fence 留 20 token slack 看到 overshoot
        # 能区分 truncate / gibberish-filter 路径。
        self.llm = create_chat_llm(
            self.model, self.base_url, self.api_key,
            temperature=1.0, streaming=True, max_retries=0,
            max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
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

        ⚠️ 顺手清掉 ``_genai_tools_unsupported``：这个旗标一旦因为旧工具集
        触发 ``GenerateContentConfig rejected`` / 类似 unsupported 异常被
        flip 成 ``True``，整条 session 后续永远不再尝试 native genai 路径。
        既然 caller 把工具列表换了（典型场景：热卸载坏 schema 工具），就该
        给 genai 路径一次重新尝试的机会，否则只能等到下次 ``connect()``
        / ``switch_model()`` 重置才能恢复。
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
    ) -> None:
        """Run each tool call through ``on_tool_call`` and mutate
        ``messages`` in place: append one assistant turn announcing all
        tool calls, then one tool-role message per call carrying the
        result JSON. Both shapes follow the OpenAI Chat Completions spec
        so the next astream invocation sees a valid history.

        ``assistant_text`` 写进 assistant turn 的 ``content``。OpenAI Chat
        Completions 协议允许同一 turn 既有 ``content`` 又有 ``tool_calls``，
        某些 OpenAI-compat provider 会"先吐文字再进 tool_calls"。和 Gemini
        路径的 streamed_text_buffer 一样，这条 text 必须一起写进历史，否则
        下一轮上下文丢前缀，模型重复 / 改口。
        """
        # 防御性过滤：``ChatOpenAI.collect_tool_calls`` 已会丢弃空 name 槽位，
        # 但万一调用方直接构造（或上游聚合实现替换），这里再兜一层 ——
        # tool_calls 历史中混入空 name 会被下一轮 server schema reject，
        # 整条会话连带挂掉。
        calls = [c for c in calls if (getattr(c, "name", "") or "").strip()]
        if not calls:
            return
        messages.append({
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
        })
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
        if self._use_genai_sdk and not self._genai_tools_unsupported:
            # 跟踪本轮 Gemini 路径是否已经把 text chunk yield 给上游。如果
            # 已经吐过文本，再 fallback 到 OpenAI-compat 会让用户在同一轮
            # 看到"半截 Gemini 文本 + 一份 OpenAI 重新生成的文本"拼接，
            # 必须把异常向上 raise，让 stream_text 的 retry/discard 流程
            # 触发"清空气泡 + 通知 response_discarded"的标准处理。
            genai_emitted_text = False
            try:
                async for chunk in self._astream_genai_with_tools(messages, **overrides):
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
        async for chunk in self._astream_openai_with_tools(messages, **overrides):
            yield chunk

    async def _astream_openai_with_tools(self, messages, **overrides):
        """OpenAI Chat Completions tool loop. Streams text chunks; on
        ``finish_reason == "tool_calls"`` runs the tools, appends the
        results to ``messages``, and re-invokes — up to
        ``self.max_tool_iterations`` total LLM calls."""
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
            async for chunk in self.llm.astream(messages, **overrides):
                if getattr(chunk, "content", None):
                    streamed_text_buffer += chunk.content
                if chunk.tool_call_deltas:
                    deltas_per_chunk.append(chunk.tool_call_deltas)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                # 永远 yield 文本 chunk —— 即便是 tool-only turn 也可能在
                # finish_reason=tool_calls 之前 emit usage chunk 和空 content。
                yield chunk
            if (
                finish_reason == "tool_calls"
                and deltas_per_chunk
                and tools_payload
                and self.on_tool_call is not None
            ):
                # ChatOpenAI is the right import even though we're outside
                # ChatOpenAI — `collect_tool_calls` is a staticmethod.
                from utils.llm_client import ChatOpenAI as _ChatOpenAI
                from utils.llm_client import LLMStreamChunk as _LLMStreamChunk
                calls = _ChatOpenAI.collect_tool_calls(deltas_per_chunk)
                await self._execute_and_append_openai_tool_calls(
                    messages, calls, assistant_text=streamed_text_buffer,
                )
                # 通知上游 ``stream_text``：本轮的 pre-tool text + tool_calls
                # 已经写进 history（assistant turn）。stream_text 据此清空
                # final-segment buffer，避免之后 append 的 final AIMessage
                # 把同一段 pre-tool 文本第二次写进 history。
                yield _LLMStreamChunk(content="", tool_round_persisted=True)
                continue
            return
        logger.warning(
            "OmniOfflineClient: tool iteration cap %d reached, stopping",
            self.max_tool_iterations,
        )

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
        from utils.llm_client import LLMStreamChunk
        if not _GENAI_AVAILABLE:
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

            try:
                async for chunk in stream:
                    candidates = getattr(chunk, "candidates", None) or []
                    if not candidates:
                        continue
                    cand = candidates[0]
                    cand_content = getattr(cand, "content", None)
                    parts = getattr(cand_content, "parts", None) or []
                    for part in parts:
                        # Skip thinking parts (Gemini 2.5+ thinking models).
                        if getattr(part, "thought", False):
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
                            had_text = True
                            streamed_text_buffer += text
                            yield LLMStreamChunk(content=text)
                    # Usage metadata may arrive on the chunk.
                    usage_meta = getattr(chunk, "usage_metadata", None)
                    if usage_meta is not None and not usage_emitted:
                        try:
                            usage_dict = {
                                "prompt_tokens": getattr(usage_meta, "prompt_token_count", 0) or 0,
                                "completion_tokens": getattr(usage_meta, "candidates_token_count", 0) or 0,
                                "total_tokens": getattr(usage_meta, "total_token_count", 0) or 0,
                            }
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
                # 把本轮已经流给用户的 text 一起写进历史。Gemini 在同一 turn
                # 里允许 text part 与 function_call part 并存；如果这里仍写
                # ``content=""``，下一轮 LLM 看到的上下文会缺掉前半句，模型
                # 会重复前缀或改口，最终持久化历史的顺序也跟真实生成顺序对不上。
                messages.append({
                    "role": "assistant",
                    "content": streamed_text_buffer,
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
            "OmniOfflineClient(genai): tool iteration cap %d reached",
            self.max_tool_iterations,
        )

    def update_max_response_length(self, max_length: int) -> None:
        """更新回复 token 上限（用户可能在对话期间修改设置）。
        单位与 ``self.max_response_length`` 一致：tiktoken token 数。
        同步刷新 ``self.llm.max_completion_tokens`` 让下一次 astream 请求
        在新的 budget+20 自然停止。

        ``0`` / 负数都解释成"无限制"，与 ``__init__`` 同款语义；上层把
        -1 当取消上限信号也能透下来。"""
        if isinstance(max_length, int):
            self.max_response_length = max_length if max_length > 0 else _UNLIMITED_BUDGET
            if self.llm is not None:
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
        if new_model and new_model != self.model:
            logger.info(f"Switching model from {self.model} to {new_model}")

            # 选择使用的 API 配置
            if use_vision_config:
                base_url = self.vision_base_url
                api_key = self.vision_api_key if self.vision_api_key and self.vision_api_key != '' else None
            else:
                base_url = self.base_url
                api_key = self.api_key

            # 先创建新 client，成功后再原子替换，避免半切换状态。
            # max_completion_tokens 跟随当前 max_response_length 同步设置
            # （和 __init__ 一致）。
            new_llm = create_chat_llm(
                new_model, base_url, api_key,
                temperature=1.0, streaming=True, max_retries=0,
                max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
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
        检查回复是否与近期回复高度重复。
        如果连续3轮都高度重复，返回 True 并触发回调。
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
        通知上层当前回复被丢弃，用于清空前端气泡/提示用户
        """
        if self.on_response_discarded:
            try:
                await self.on_response_discarded(reason, attempt, max_attempts, will_retry, message)
            except Exception as e:
                logger.warning(f"通知 response_discarded 失败: {e}")

    async def stream_text(self, text: str) -> None:
        """
        Send a text message to the API and stream the response.
        If there are pending images, temporarily switch to vision model for this turn.
        Uses langchain ChatOpenAI for streaming.
        """
        if not text or not text.strip():
            # If only images without text, use a default prompt
            if self._pending_images:
                text = "请分析这些图片。"
            else:
                return
        
        # Check if we need to switch to vision model
        has_images = len(self._pending_images) > 0
        
        # Prepare user message content
        if has_images:
            # Switch to vision model permanently for this session
            # (cannot switch back because image data remains in conversation history)
            if self.vision_model and self.vision_model != self.model:
                logger.info(f"🖼️ Temporarily switching to vision model: {self.vision_model} (from {self.model})")
                await self.switch_model(self.vision_model, use_vision_config=True)
            
            # Multi-modal message: images + text
            content = []
            
            # Add images first
            for img_b64 in self._pending_images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                })
            
            # Add text
            content.append({
                "type": "text",
                "text": text.strip()
            })
            
            user_message = HumanMessage(content=content)
            logger.info(f"Sending multi-modal message with {len(self._pending_images)} images")
            
            # Clear pending images after using them
            self._pending_images.clear()
        else:
            # Text-only message
            user_message = HumanMessage(content=text.strip())
        
        self._conversation_history.append(user_message)
        
        # Callback for user input
        if self.on_input_transcript:
            await self.on_input_transcript(text.strip())
        
        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        assistant_message = ""        # 仅最后一段未持久化的 text（final-segment）
        assistant_message_total = ""  # 整轮累计（含 pre-tool），整轮级判定看它
        status_reported = False
        guard_exhausted = False
        
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
                try:
                    assistant_message = ""
                    assistant_message_total = ""
                    guard_attempt = 0
                    while guard_attempt <= self.max_response_rerolls:
                        self._is_responding = True
                        assistant_message = ""           # 仅最后一段未持久化的 text，用于 final AIMessage append
                        assistant_message_total = ""     # 全轮累积，用于 _check_repetition / 长度 guard
                        is_first_chunk = True
                        pipe_count = 0  # 围栏：追踪 | 字符的出现次数
                        fence_triggered = False  # 围栏是否已触发
                        guard_triggered = False
                        discard_reason = None
                        chunk_usage = None
                        prefix_buffer = ""
                        prefix_checked = not bool(self._prefix_buffer_size)

                        # Tool-aware streaming: ``_astream_with_tools`` runs
                        # the multi-turn tool loop inside (executing tools and
                        # appending results to ``_conversation_history`` IN
                        # PLACE). The yielded chunks are exactly the same
                        # shape as raw ``self.llm.astream``, so the existing
                        # prefix/fence/length-guard logic below is untouched.
                        async for chunk in self._astream_with_tools(self._conversation_history):
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
                                assistant_message = ""
                                # 重置围栏 / prefix buffer：下一段是新的语义
                                # 单元（模型基于 tool 结果重新出文本），不应
                                # 复用之前的 fence / prefix 状态。
                                pipe_count = 0
                                prefix_buffer = ""
                                prefix_checked = not bool(self._prefix_buffer_size)
                                continue
                            if not self._is_responding:
                                break

                            if fence_triggered:
                                break

                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)

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
                                    assistant_message += truncated_content
                                    assistant_message_total += truncated_content
                                    if self.on_text_delta:
                                        await self.on_text_delta(truncated_content, is_first_chunk)
                                    is_first_chunk = False

                                    if self.enable_response_guard:
                                        # 长度 guard 看完整一轮（含 pre-tool）的 token 量，
                                        # 不能只看 final-segment，否则 tool 轮前长篇大论 +
                                        # tool 轮后短短一句也能逃过 guard。
                                        current_length = count_tokens(assistant_message_total)
                                        if current_length > self.max_response_length:
                                            guard_triggered = True
                                            discard_reason = f"length>{self.max_response_length}"
                                            logger.info(f"OmniOfflineClient: 检测到长回复 ({current_length} tokens)，准备重试")
                                            self._is_responding = False
                                            break
                            elif content and not content.strip():
                                logger.debug(f"OmniOfflineClient: 过滤空白内容 - content_repr: {repr(content)[:100]}")

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
                                    assistant_message += flush_text
                                    assistant_message_total += flush_text
                                    if self.on_text_delta:
                                        await self.on_text_delta(flush_text, is_first_chunk)
                                    is_first_chunk = False
                                    if self.enable_response_guard:
                                        # 长度 guard 看整轮（含 pre-tool），与上方主累加块对偶
                                        current_length = count_tokens(assistant_message_total)
                                        if current_length > self.max_response_length:
                                            guard_triggered = True
                                            discard_reason = f"length>{self.max_response_length}"

                        if guard_triggered:
                            guard_attempt += 1
                            reroll_count += 1
                            will_retry = guard_attempt <= self.max_response_rerolls

                            # max_attempts 报给前端的是**总尝试次数**而非
                            # rerolls 次数（rerolls 不含首次尝试）。前端 attempt
                            # / max_attempts 进度条要 1/2 → 2/2 才合理。
                            total_attempts = self.max_response_rerolls + 1

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
                            recovery_text = ""
                            if discard_reason and "length>" in discard_reason:
                                # 整轮判定：gibberish / 截断必须看 _total，否则
                                # tool 轮 sentinel 把 final-segment 清空之后整段
                                # pre-tool 被忽略，明明很长却走 RESPONSE_TOO_LONG。
                                if not _is_gibberish_response(assistant_message_total):
                                    capped = truncate_to_tokens(
                                        assistant_message_total, self.max_response_length,
                                    )
                                    recovery_text = _truncate_to_last_sentence_end(capped)

                            if recovery_text:
                                logger.info(
                                    "OmniOfflineClient: guard 重试耗尽，截断至最后句末 "
                                    "(原 %d tokens → 截断后 %d tokens)",
                                    count_tokens(assistant_message_total), count_tokens(recovery_text),
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

                except (APIConnectionError, InternalServerError, RateLimitError) as e:
                    error_type = type(e).__name__
                    error_str_lower = str(e).lower()
                    is_internal_error = isinstance(e, InternalServerError)
                    logger.info(f"ℹ️ 捕获到 {error_type} 错误")

                    # 欠费/API Key 错误立即上报并终止；配额错误上报但继续重试
                    if '欠费' in error_str_lower or 'standing' in error_str_lower:
                        logger.error(f"OmniOfflineClient: 检测到欠费错误，直接上报: {e}")
                        if self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "API_ARREARS"}))
                            status_reported = True
                        break
                    elif ('401' in error_str_lower or 'unauthorized' in error_str_lower
                            or 'authentication' in error_str_lower
                            or ('invalid' in error_str_lower and 'key' in error_str_lower)):
                        logger.error(f"OmniOfflineClient: 检测到 API Key 错误，直接上报: {e}")
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
                        if self.on_status_message:
                            if is_internal_error:
                                await self.on_status_message(json.dumps({"code": "LLM_UPSTREAM_ERROR"}))
                            else:
                                await self.on_status_message(json.dumps({"code": "LLM_CONNECTION_EXHAUSTED", "details": {"error_type": error_type, "max_retries": max_retries, "error": str(e)}}))
                            status_reported = True
                        break
                except Exception as e:
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
                                message=json.dumps({
                                    "code": "TEXT_GEN_ERROR_AFTER_PARTIAL",
                                    "details": {
                                        "error_type": type(e).__name__,
                                        "error": str(e),
                                    },
                                }),
                            )
                            status_reported = True
                        except Exception as _notify_err:
                            logger.warning(
                                "通知 response_discarded(after partial) 失败: %s",
                                _notify_err,
                            )
                    if not status_reported and self.on_status_message:
                        await self.on_status_message(json.dumps({"code": "TEXT_GEN_ERROR", "details": {"error_type": type(e).__name__, "error": str(e)}}))
                        status_reported = True
                    break
        finally:
            self._is_responding = False
            
            # 整轮判定：所有重试都没产生过任何文本（包括 pre-tool）才算 LLM_NO_RESPONSE。
            # 用 final-segment 会让"tool 轮跑完了但模型没出 final 文本"的场景被错报。
            if not assistant_message_total and not guard_exhausted and not status_reported:
                logger.warning("OmniOfflineClient: 所有重试均未产生文本回复")
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "LLM_NO_RESPONSE"}))
            
            # Call response done callback
            if self.on_response_done:
                await self.on_response_done()
    
    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Compatibility method - not used in text mode"""
        pass
    
    async def stream_image(self, image_b64: str) -> None:
        """
        Add an image to pending images queue.
        Images will be sent together with the next text message.
        """
        if not image_b64:
            return
        
        # Store base64 image
        self._pending_images.append(image_b64)
        logger.info(f"Added image to pending queue (total: {len(self._pending_images)})")
    
    def has_pending_images(self) -> bool:
        """Check if there are pending images waiting to be sent."""
        return len(self._pending_images) > 0
    
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
        completion_mode: str = "proactive",
        persist_response: bool = True,
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

        Returns True if any user-visible text was generated, False if aborted
        or only nonverbal directives were emitted.
        """
        if not instruction or not instruction.strip():
            return False

        # 临时注入：instruction 已由调用方用 ======== 格式封装，作为 HumanMessage 发送，
        # 不持久化到 _conversation_history，避免污染长期上下文。
        messages_to_send = (
            self._conversation_history
            + [HumanMessage(content=instruction)]
        )

        assistant_message = ""
        is_first_chunk = True
        chunk_usage = None
        prefix_buffer = ""
        prefix_checked = not bool(self._prefix_buffer_size)

        try:
            self._is_responding = True
            set_call_type("proactive")
            # 主动搭话同样走 tool-aware streaming —— agent 注入的 stage
            # direction 也可能让模型决定调用工具（比如 "讲一下今天天气"）。
            async for chunk in self._astream_with_tools(messages_to_send):
                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    chunk_usage = chunk.usage_metadata
                    logger.debug(f"🔍 [Usage-Proactive] {chunk_usage}")
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
        except Exception as e:
            logger.error("OmniOfflineClient.prompt_ephemeral error: %s", e, exc_info=True)
            if self.on_status_message:
                await self.on_status_message(json.dumps({"code": "PROACTIVE_GEN_FAILED", "details": {"error_type": type(e).__name__, "error": str(e)}}))
            assistant_message = ""
            return False
        finally:
            self._is_responding = False
            # Token usage 由 _AsyncStreamWrapper hook 在流结束时自动记录，
            # 此处不再手动调用 TokenTracker.record() 避免双重计数。
            committed_text = _strip_nonverbal_directives(assistant_message).strip()
            content_committed = bool(committed_text)
            if content_committed and persist_response:
                self._conversation_history.append(AIMessage(content=assistant_message))
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
