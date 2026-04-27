# -*- coding: utf-8 -*-
"""
DirectTaskExecutor: 合并 Analyzer + Planner 的功能
并行评估 ComputerUse / BrowserUse / UserPlugin 可行性
"""
import json
import os
import re
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
from openai import APIConnectionError, InternalServerError, RateLimitError
import httpx
from config import (
    USER_PLUGIN_SERVER_PORT,
    AGENT_HISTORY_TURNS,
    AGENT_RECENT_CTX_PER_ITEM_TOKENS,
    AGENT_RECENT_CTX_TOTAL_TOKENS,
    AGENT_PLUGIN_DESC_BM25_THRESHOLD,
    AGENT_PLUGIN_SHORTDESC_MAX_TOKENS,
    AGENT_PLUGIN_COARSE_MAX_TOKENS,
    AGENT_UNIFIED_ASSESS_MAX_TOKENS,
    AGENT_PLUGIN_FULL_MAX_TOKENS,
    TASK_DETAIL_MAX_TOKENS,
)
from utils.llm_client import create_chat_llm, ChatOpenAI
from config.prompts_agent import (
    UNIFIED_CHANNEL_SYSTEM_PROMPT,
    CHANNEL_DESC_QWENPAW,
    CHANNEL_DESC_OPENFANG,
    CHANNEL_DESC_BROWSER_USE,
    CHANNEL_DESC_COMPUTER_USE,
    USER_PLUGIN_SYSTEM_PROMPT,
    USER_PLUGIN_COARSE_SCREEN_PROMPT,
)
from config.prompts_sys import _loc
from utils.file_utils import robust_json_loads
from plugin.settings import PLUGIN_EXECUTION_TIMEOUT
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from .computer_use import ComputerUseAdapter
from .browser_use_adapter import BrowserUseAdapter
from .openclaw_adapter import OpenClawAdapter
from .openfang_adapter import OpenFangAdapter
from .plugin_filter import (
    stage1_filter,
    annotate_keyword_hits,
    _match_keywords,
)

logger = get_module_logger(__name__, "Agent")
_TIMEOUT_UNSET = object()


def _normalize_timeout_value(value: Any) -> float | None | object:
    """Normalize timeout values.

    Returns:
        `_TIMEOUT_UNSET` when the value is missing/invalid,
        `None` for explicit no-timeout (`None` or `<= 0`),
        or a positive float timeout.
    """
    if value is _TIMEOUT_UNSET:
        return _TIMEOUT_UNSET
    if value is None:
        return None
    try:
        timeout_value = float(value)
    except (TypeError, ValueError):
        return _TIMEOUT_UNSET
    return timeout_value if timeout_value > 0 else None


def _resolve_plugin_entry_timeout(meta: Optional[Dict[str, Any]], entry: Optional[str]) -> float | None:
    default_timeout = PLUGIN_EXECUTION_TIMEOUT
    if not isinstance(meta, dict):
        return default_timeout
    entries = meta.get("entries")
    if not isinstance(entries, list):
        return default_timeout
    target_entry = entry or "run"
    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("id") != target_entry:
            continue
        resolved = _normalize_timeout_value(item.get("timeout", _TIMEOUT_UNSET))
        if resolved is not _TIMEOUT_UNSET:
            return resolved
        break
    return default_timeout


def _resolve_ctx_entry_timeout(ctx_obj: Any, fallback_timeout: float | None) -> float | None:
    if isinstance(ctx_obj, dict):
        resolved = _normalize_timeout_value(ctx_obj.get("entry_timeout", _TIMEOUT_UNSET))
        if resolved is not _TIMEOUT_UNSET:
            return resolved
    return fallback_timeout


def _compute_run_wait_timeout(entry_timeout: float | None) -> float | None:
    if entry_timeout is None:
        return None
    return max(entry_timeout + 15.0, 315.0)


@dataclass
class TaskResult:
    """任务执行结果"""
    task_id: str
    has_task: bool = False
    task_description: str = ""
    execution_method: str = "none"  # "computer_use" | "browser_use" | "user_plugin" | "openclaw" | "openfang" | "none"
    success: bool = False
    result: Any = None
    error: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict] = None
    entry_id: Optional[str] = None
    reason: str = ""
    latest_user_request: str = ""
    normalized_intent: str = ""
    recent_context: Optional[List[Dict[str, str]]] = None


@dataclass
class ComputerUseDecision:
    """ComputerUse 可行性评估结果"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    reason: str = ""


@dataclass
class BrowserUseDecision:
    """BrowserUse 可行性评估结果"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    reason: str = ""

@dataclass
class UserPluginDecision:
    """UserPlugin 可行性评估结果"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    plugin_id: Optional[str] = None
    entry_id: Optional[str] = None
    plugin_args: Optional[Dict] = None
    reason: str = ""


@dataclass
class OpenFangDecision:
    """OpenFang 多 Agent 执行决策"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    suggested_tools: Optional[List[str]] = None
    reason: str = ""


@dataclass
class OpenClawDecision:
    """OpenClaw 独立 Agent 执行决策"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    instruction: str = ""
    reason: str = ""


@dataclass
class UnifiedChannelDecision:
    """统一渠道评估结果 — 每个渠道为 dict 或 None"""
    qwenpaw: Optional[Dict[str, Any]] = None     # {"can_execute": bool, "task_description": str, "reason": str}
    openfang: Optional[Dict[str, Any]] = None
    browser_use: Optional[Dict[str, Any]] = None
    computer_use: Optional[Dict[str, Any]] = None


# 优先级：qwenpaw > openfang > browser_use > computer_use
_CHANNEL_PRIORITY = ["qwenpaw", "openfang", "browser_use", "computer_use"]
_CHANNEL_TO_METHOD = {
    "qwenpaw": "openclaw",
    "openfang": "openfang",
    "browser_use": "browser_use",
    "computer_use": "computer_use",
}


class DirectTaskExecutor:
    """
    直接任务执行器：并行评估 BrowserUse / ComputerUse / UserPlugin 可行性并执行
    """
    
    def __init__(self, computer_use: Optional[ComputerUseAdapter] = None, browser_use: Optional[BrowserUseAdapter] = None,
                 openclaw: Optional[OpenClawAdapter] = None,
                 openfang: Optional[OpenFangAdapter] = None):
        self.computer_use = computer_use or ComputerUseAdapter()
        self.browser_use = browser_use
        self.openclaw = openclaw
        self.openfang: Optional[OpenFangAdapter] = openfang
        self._config_manager = get_config_manager()
        self.plugin_list = []
        self.user_plugin_enabled_default = False
        self._external_plugin_provider: Optional[Callable[[bool], Awaitable[List[Dict[str, Any]]]]] = None
        # ChatOpenAI instance cache: keyed by (api_key, base_url, model, temperature, max_completion_tokens)
        self._cached_llms: dict[tuple, ChatOpenAI] = {}
        self._cached_llm_config_key: tuple = ()  # tracks (api_key, base_url, model) to detect config changes
        self._cleanup_tasks: set = set()  # 持有关闭任务的强引用，防止 GC 回收
        # plugin_id -> (full_description, generated_short_description)
        self._short_desc_cache: dict[str, tuple[str, str]] = {}
        self._correction_memory_filename = "correction_memory.json"
        self._search_term_allowlist = {"id", "os", "db", "ui", "ux", "qa"}
        # 白名单 + alias 归一化，防止任意字符串被写进 correction_memory.json
        # 并跨会话注入到路由 system prompt 里。未命中一律归一为空串，由调用方丢弃。
        self._correction_tool_canonical = {
            "computer_use": "computer_use",
            "browser_use": "browser_use",
            "openclaw": "openclaw",
            "qwenpaw": "openclaw",
            "openfang": "openfang",
            "user_plugin": "user_plugin",
        }

    def _normalize_correction_tool_name(self, value: Any) -> str:
        tool = str(value or "").strip().lower()
        return self._correction_tool_canonical.get(tool, "")
    
    
    def set_plugin_list_provider(self, provider: Callable[[bool], Awaitable[List[Dict[str, Any]]]]):
        """Allow agent_server to inject a custom async provider for plugin discovery."""
        self._external_plugin_provider = provider

    async def _ensure_short_descriptions(self, plugins: List[Dict[str, Any]]) -> None:
        """For plugins missing short_description, generate one via LLM (best-effort, cached)."""
        to_generate: list[dict] = []
        for p in plugins:
            if not isinstance(p, dict):
                continue
            pid = p.get("id", "")
            short = str(p.get("short_description", "") or "").strip()
            desc = str(p.get("description", "") or "").strip()
            # Apply cached value if available and description hasn't changed
            cached = self._short_desc_cache.get(pid)
            if cached and cached[0] == desc and not short:
                p["short_description"] = cached[1]
                continue
            if not short and desc:
                to_generate.append(p)
            elif pid and short:
                self._short_desc_cache[pid] = (desc, short)

        if not to_generate:
            return

        logger.info("[Agent] Generating short_description for %d plugin(s)", len(to_generate))
        try:
            llm = self._get_llm(temperature=0, max_completion_tokens=AGENT_PLUGIN_SHORTDESC_MAX_TOKENS)
            for p in to_generate:
                quota_error = await self._check_agent_quota("task_executor.ensure_short_desc")
                if quota_error:
                    logger.debug("[Agent] Stopping short_description generation: quota exceeded")
                    break
                pid = p.get("id", "unknown")
                try:
                    from config import PLUGIN_INPUT_DESC_MAX_TOKENS
                    from utils.tokenize import truncate_to_tokens
                    raw_desc = str(p.get("description", "") or "").strip()
                    # Plugin manifest 的 description 字段无 cap，恶意/超大
                    # plugin 可能塞 1MB 文本。先截到 PLUGIN_INPUT_DESC_MAX_TOKENS
                    # 再送入 short_description 生成 prompt。
                    desc = truncate_to_tokens(raw_desc, PLUGIN_INPUT_DESC_MAX_TOKENS)
                    messages = [
                        {"role": "system", "content": "You are an agentic automation assessment agent, generate a concise plugin summary under 200 tokens in English."},
                        {"role": "user", "content": f"Plugin: {pid}\nDescription: {desc}\n\nReturn ONLY the summary."},
                    ]
                    resp = await llm.ainvoke(messages)
                    text = (resp.content or "").strip()
                    from utils.tokenize import count_tokens
                    if text and count_tokens(text) <= AGENT_PLUGIN_SHORTDESC_MAX_TOKENS:
                        p["short_description"] = text
                        self._short_desc_cache[pid] = (desc, text)
                        # LLM 生成原文不写 logger
                        logger.debug("[Agent] Generated short_description for %s (len=%d chars)", pid, len(text))
                        print(f"[Agent] short_description {pid}: {text[:80]}")
                except Exception as e:
                    # Don't cache failures — allow retry on next refresh
                    logger.debug("[Agent] Failed to generate short_description for %s: %s", pid, e)
        except Exception as e:
            logger.warning("[Agent] short_description generation batch failed: %s", e)

    async def plugin_list_provider(self, force_refresh: bool = True) -> List[Dict[str, Any]]:
        # return cached list when allowed
        if self.plugin_list and not force_refresh:
            return self.plugin_list

        # try external provider first (e.g., injected by agent_server)
        if self._external_plugin_provider is not None:
            try:
                plugins = await self._external_plugin_provider(force_refresh)
                if isinstance(plugins, list):
                    self.plugin_list = plugins
                    await self._ensure_short_descriptions(self.plugin_list)
                    logger.info(f"[Agent] Loaded {len(self.plugin_list)} plugins via external provider")
                    return self.plugin_list
            except Exception as e:
                logger.warning(f"[Agent] external plugin_list_provider failed: {e}")

        # fallback to built-in HTTP fetcher
        if (self.plugin_list == []) or force_refresh:
            try:
                url = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins"
                # increase timeout and avoid awaiting a non-awaitable .json()
                timeout = httpx.Timeout(5.0, connect=2.0)
                async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as _client:
                    resp = await _client.get(url)
                    try:
                        data = resp.json()
                    except Exception:
                        logger.warning("[Agent] Failed to parse plugins response as JSON")
                        data = {}
                    plugin_list = data.get("plugins", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    # only update cache when we obtained a non-empty list
                    if plugin_list:
                        self.plugin_list = plugin_list  # 更新实例变量
                        await self._ensure_short_descriptions(self.plugin_list)
            except Exception as e:
                logger.warning(f"[Agent] plugin_list_provider http fetch failed: {e}")
        logger.info(f"[Agent] Loaded {len(self.plugin_list)} plugins: {[p.get('id', 'unknown') for p in self.plugin_list if isinstance(p, dict)]}")
        return self.plugin_list


    def _get_llm(
        self,
        *,
        temperature: float = 0,
        max_completion_tokens: int | None = None,
        tier: str = "summary",
    ) -> ChatOpenAI:
        """Return a cached ChatOpenAI instance via create_chat_llm.

        ``tier`` selects the model tier (``summary`` / ``correction`` /
        ``emotion`` / ``vision`` …) — see ``ConfigManager.get_model_api_config``.
        Instances are cached by (tier, api_key, base_url, model, temperature,
        max_completion_tokens). When the provider config for the **summary**
        tier changes (the de-facto default), all cached instances across all
        tiers are closed and recreated, so callers don't need to flush per-tier.
        """
        set_call_type("agent")
        api_config = self._config_manager.get_model_api_config(tier)
        # The cross-tier flush key tracks the summary tier's provider config
        # (current behavior). Switching providers via the UI typically happens
        # for the summary tier and the others share the same upstream; keying
        # off summary keeps the original semantics.
        watch_config = self._config_manager.get_model_api_config("summary")
        watch_key = (watch_config['api_key'], watch_config['base_url'], watch_config['model'])
        if self._cached_llm_config_key != watch_key:
            self._close_all_llms()
            self._cached_llm_config_key = watch_key

        instance_key = (
            tier, api_config['api_key'], api_config['base_url'], api_config['model'],
            temperature, max_completion_tokens,
        )
        if instance_key not in self._cached_llms:
            llm = create_chat_llm(
                model=api_config['model'],
                base_url=api_config['base_url'],
                api_key=api_config['api_key'],
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                max_retries=0,
            )
            self._cached_llms[instance_key] = llm
            logger.debug(
                "[Agent] Created new ChatOpenAI (tier=%s, model=%s, base_url=%s, temp=%s, max_tokens=%s)",
                tier, api_config['model'], api_config['base_url'], temperature, max_completion_tokens,
            )

        return self._cached_llms[instance_key]

    def _close_all_llms(self) -> None:
        """Close all cached ChatOpenAI instances asynchronously."""
        for llm in self._cached_llms.values():
            self._close_llm_async(llm)
        self._cached_llms.clear()

    def _close_llm_async(self, llm: ChatOpenAI) -> None:
        """Asynchronously close a ChatOpenAI instance, preventing GC from dropping the task."""
        async def _do_close():
            try:
                await llm.aclose()
            except Exception as e:
                logger.warning("[Agent] Failed to close old ChatOpenAI instance: %s", e)
            finally:
                self._cleanup_tasks.discard(task)

        try:
            task = asyncio.ensure_future(_do_close())
            self._cleanup_tasks.add(task)
        except RuntimeError:
            logger.debug("[Agent] No running event loop, skipping async LLM close")

    async def _check_agent_quota(self, source: str) -> Optional[str]:
        """免费版 Agent 模型每日 300 次本地限流（async，避免事件循环阻塞）。"""
        ok, info = await self._config_manager.aconsume_agent_daily_quota(source=source, units=1)
        if ok:
            return None
        return json.dumps({"code": "AGENT_QUOTA_EXCEEDED", "details": {"used": info.get('used', 0), "limit": info.get('limit', 300)}})
    
    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        """格式化对话消息"""
        def _extract_text(m: dict) -> str:
            return str(m.get('text') or m.get('content') or '').strip()

        def _extract_attachments(m: dict) -> list[dict]:
            raw = m.get("attachments") or []
            if not isinstance(raw, list):
                return []
            normalized = []
            for item in raw:
                if isinstance(item, str):
                    url = item.strip()
                elif isinstance(item, dict):
                    url = str(item.get("url") or item.get("image_url") or "").strip()
                else:
                    url = ""
                if url:
                    normalized.append({"type": "image_url", "url": url})
            return normalized

        def _describe_user_message(text: str, attachments: list[dict]) -> str:
            if text:
                if attachments:
                    return f"{text} [Attached images: {len(attachments)}]"
                return text
            if attachments:
                return f"[User attached {len(attachments)} image(s) without text]"
            return ""

        latest_user_text = ""
        for m in reversed(messages[-AGENT_HISTORY_TURNS:]):
            if m.get('role') == 'user':
                latest_user_text = _describe_user_message(_extract_text(m), _extract_attachments(m))
                if latest_user_text:
                    break
        lines = []
        if latest_user_text:
            lines.append(f"LATEST_USER_REQUEST: {latest_user_text}")
        for m in messages[-AGENT_HISTORY_TURNS:]:
            role = m.get('role', 'user')
            text = _describe_user_message(_extract_text(m), _extract_attachments(m))
            if text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines)

    def _extract_latest_user_payload(self, messages: List[Dict[str, Any]]) -> tuple[str, list[dict]]:
        latest_text = ""
        latest_attachments: list[dict] = []
        for m in reversed(messages[-AGENT_HISTORY_TURNS:]):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            latest_text = str(m.get("text") or m.get("content") or "").strip()
            raw_attachments = m.get("attachments") or []
            if isinstance(raw_attachments, list):
                for item in raw_attachments:
                    if isinstance(item, str):
                        url = item.strip()
                    elif isinstance(item, dict):
                        url = str(item.get("url") or item.get("image_url") or "").strip()
                    else:
                        url = ""
                    if url:
                        latest_attachments.append({
                            "type": "image_url",
                            "url": url,
                        })
            if latest_text or latest_attachments:
                break
        if not latest_text and latest_attachments:
            latest_text = "请分析用户提供的图片内容，并根据图片完成任务。"
        return latest_text, latest_attachments
    
    def _format_tools(self, capabilities: Dict[str, Dict[str, Any]]) -> str:
        """格式化工具列表供 LLM 参考"""
        if not capabilities:
            return "No MCP tools available."
        
        lines = []
        for tool_name, info in capabilities.items():
            desc = info.get('description', 'No description')
            schema = info.get('input_schema', {})
            params = schema.get('properties', {})
            required = schema.get('required', [])
            param_desc = []
            for p_name, p_info in params.items():
                p_type = p_info.get('type', 'any')
                is_required = '(required)' if p_name in required else '(optional)'
                param_desc.append(f"    - {p_name}: {p_type} {is_required}")
            
            lines.append(f"- {tool_name}: {desc}")
            if param_desc:
                lines.extend(param_desc)
        
        return "\n".join(lines)

    def _extract_latest_user_intent(self, conversation: str) -> str:
        """Extract the latest user request from formatted conversation text."""
        user_intent = ""
        conv_lines = conversation.splitlines()
        for line in conv_lines:
            if line.startswith("LATEST_USER_REQUEST:"):
                user_intent = line[len("LATEST_USER_REQUEST:"):].strip()
                break

        if not user_intent:
            for line in reversed(conv_lines):
                if line.startswith("user:") or line.startswith("User:"):
                    user_intent = line[5:].strip()
                    break
        return user_intent

    @staticmethod
    def _message_text(message: Dict[str, Any]) -> str:
        return str(message.get("text") or message.get("content") or "").strip()

    def _extract_recent_context(
        self,
        messages: List[Dict[str, Any]],
        *,
        limit: int = 4,
    ) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = self._message_text(message)
            if not text:
                continue
            items.append({"role": role, "content": text})
        return items[-limit:]

    def _normalize_user_intent(
        self,
        latest_user_request: str,
        recent_context: List[Dict[str, str]],
    ) -> str:
        latest = re.sub(r"\s+", " ", (latest_user_request or "").strip())
        if not latest:
            return ""

        normalized_latest = re.sub(r"[^\w\u4e00-\u9fff]+", " ", latest.lower()).strip()
        vague_markers = (
            "这个", "那个", "一下", "继续", "继续弄", "处理一下", "帮我弄一下",
            "就这个", "刚才那个", "上一条", "发给他", "发给她", "发给它",
            "打开它", "打开这个", "继续这个", "继续那个",
            "this", "that", "this one", "that one", "it", "do it", "continue",
            "go on", "keep going", "same one", "the same", "open it", "send it",
            "上一個", "這個", "那個", "继续做", "接着做",
            "これ", "それ", "これを", "それを", "続けて", "続ける", "やって", "やってね",
            "이거", "저거", "이것", "그것", "계속", "계속해", "해줘", "그거 해줘",
        )
        user_turns = [
            item.get("content", "").strip()
            for item in recent_context
            if item.get("role") == "user" and item.get("content", "").strip()
        ]
        def _matches_vague_marker(marker: str) -> bool:
            marker_norm = re.sub(r"[^\w\u4e00-\u9fff]+", " ", marker.lower()).strip()
            if not marker_norm:
                return False
            if re.search(r"[a-z0-9]", marker_norm):
                return re.search(rf"\b{re.escape(marker_norm)}\b", normalized_latest) is not None
            return marker_norm in latest or marker_norm in normalized_latest

        length_source = normalized_latest or latest
        cjk_like_count = sum(
            1
            for ch in length_source
            if (
                "\u3040" <= ch <= "\u30ff"  # Hiragana + Katakana
                or "\u4e00" <= ch <= "\u9fff"  # CJK Unified Ideographs
                or "\uac00" <= ch <= "\ud7af"  # Hangul Syllables
            )
        )
        length_threshold = 3 if cjk_like_count * 2 >= len(length_source) else 6
        latest_is_vague = len(length_source) <= length_threshold or any(
            _matches_vague_marker(marker) for marker in vague_markers
        )
        if not latest_is_vague:
            return latest

        from utils.tokenize import truncate_to_tokens
        context_candidates: List[str] = []
        for text in user_turns[-3:]:
            if text and text != latest:
                context_candidates.append(text)
        if context_candidates:
            return truncate_to_tokens(" / ".join([*context_candidates[-2:], latest]), TASK_DETAIL_MAX_TOKENS)
        return truncate_to_tokens(latest, TASK_DETAIL_MAX_TOKENS)

    @staticmethod
    def _sanitize_correction_text(text: str) -> str:
        cleaned = str(text or "")
        cleaned = cleaned.replace("\r", " ").replace("\n", " ")
        patterns = [
            (r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+", r"\1=[REDACTED_PASSWORD]"),
            (r"(?i)(password|passwd|pwd|密码|口令)\s*(?:is|为|是|=|:|：)\s*\S+", r"\1=[REDACTED_PASSWORD]"),
            (r"(?i)authorization\s*:\s*bearer\s+\S+", "Authorization: Bearer [REDACTED_TOKEN]"),
            (r"(?i)(token|api[_-]?key|access[_-]?token|refresh[_-]?token)\s*[:=]\s*\S+", r"\1=[REDACTED_TOKEN]"),
            (
                r"(?i)(token|api(?:[\s_-]?key)|access(?:[\s_-]?token)|refresh(?:[\s_-]?token)|令牌|密钥|秘钥)\s*(?:is|为|是|=|:|：)\s*\S+",
                r"\1=[REDACTED_TOKEN]",
            ),
            (r"(?i)\bsk-[a-z0-9_-]{10,}\b", "[REDACTED_TOKEN]"),
            (r"(?i)(cookie)\s*[:=：]\s*\S+", r"\1=[REDACTED_COOKIE]"),
            (r"(?i)(cookie)\s*(?:[:=：]|is|为|是)\s*\S+", r"\1=[REDACTED_COOKIE]"),
            (r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]"),
            (
                r"(?i)(\b(?:otp|pin|verification(?:\s+code)?|sms\s*code|one[-\s]?time(?:\s+password|\s+code)?|验证码|校验码|短信码|动态码)\b(?:\s*(?:is|为|是))?[\s:：=#-]{0,6})\d{4,8}\b",
                r"\1[REDACTED_OTP]",
            ),
            (r"\b(?:\d{15}|\d{17}[0-9Xx])\b", "[REDACTED_ID]"),
            (r"\b\d{15,19}\b", "[REDACTED_NUMBER]"),
            (r"\b1[3-9]\d{9}\b", "[REDACTED_PHONE]"),
        ]
        for pattern, replacement in patterns:
            cleaned = re.sub(pattern, replacement, cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        from utils.tokenize import truncate_to_tokens
        # Per-item cap on the redacted correction text (one role-message in the
        # recent-context window). Group with `agent_server.py` callback summary —
        # both are "longer reflective blurbs" the LLM will see standalone.
        return truncate_to_tokens(cleaned, AGENT_RECENT_CTX_PER_ITEM_TOKENS)

    def _sanitize_recent_context(self, recent_context: List[Dict[str, str]]) -> List[Dict[str, str]]:
        from utils.tokenize import count_tokens
        sanitized: List[Dict[str, str]] = []
        total_tokens = 0
        # Total budget across the assembled recent-context window — fits ~2-3
        # per-item (400-token) entries plus headroom. Caller stops accumulating
        # once we cross this; partial last item is dropped.
        for item in reversed(recent_context[-4:]):
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = self._sanitize_correction_text(item.get("content", ""))
            if not content:
                continue
            total_tokens += count_tokens(content)
            if total_tokens > AGENT_RECENT_CTX_TOTAL_TOKENS:
                break
            sanitized.append({"role": role, "content": content})
        sanitized.reverse()
        return sanitized

    def _get_correction_memory_path(self) -> Path:
        self._config_manager.ensure_config_directory()
        return Path(self._config_manager.config_dir) / self._correction_memory_filename

    def _load_correction_memory(self) -> Dict[str, Any]:
        path = self._get_correction_memory_path()
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {"version": 1, "correction_events": []}
        except Exception as exc:
            logger.warning("[CorrectionMemory] Failed to load %s: %s", path, exc)
            return {"version": 1, "correction_events": []}
        if not isinstance(data, dict):
            return {"version": 1, "correction_events": []}
        events = data.get("correction_events")
        if not isinstance(events, list):
            data["correction_events"] = []
        data.setdefault("version", 1)
        return data

    def _save_correction_memory(self, data: Dict[str, Any]) -> None:
        path = self._get_correction_memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        tmp_path.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _is_allowed_search_term(self, term: str) -> bool:
        if not term or term.isdigit():
            return False
        if len(term) == 2 and term.isascii() and term.isalpha() and term not in self._search_term_allowlist:
            return False
        return True

    def _extract_search_terms(self, text: str) -> List[str]:
        lowered = str(text or "").lower()
        terms = re.findall(r"\w{2,}", lowered, flags=re.UNICODE)
        seen: set[str] = set()
        result: List[str] = []
        for term in terms:
            if not self._is_allowed_search_term(term):
                continue
            if term in seen:
                continue
            seen.add(term)
            result.append(term)
        return result[:24]

    def _retrieve_relevant_corrections(
        self,
        latest_user_request: str,
        *,
        normalized_intent: str = "",
        recent_context: Optional[List[Dict[str, str]]] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        memory = self._load_correction_memory()
        events = memory.get("correction_events", [])
        if not isinstance(events, list) or not events:
            return []

        query_blob_parts = [normalized_intent, latest_user_request]
        for item in recent_context or []:
            query_blob_parts.append(item.get("content", ""))
        query_terms = self._extract_search_terms(" ".join(part for part in query_blob_parts if part))
        if not query_terms:
            return []

        scored: List[tuple[int, datetime, Dict[str, Any]]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_context = " ".join(
                [
                    str(event.get("normalized_intent", "")),
                    str(event.get("user_query", "")),
                    self._normalize_correction_tool_name(event.get("chosen_tool", "")),
                    self._normalize_correction_tool_name(event.get("correct_tool", "")),
                    " ".join(
                        str(item.get("content", ""))
                        for item in event.get("recent_context", [])
                        if isinstance(item, dict)
                    ),
                ]
            ).lower()
            score = 0
            for term in query_terms:
                if not self._is_allowed_search_term(term):
                    continue
                if term and term in event_context:
                    score += max(1, min(len(term), 8))
            if score <= 0:
                continue
            timestamp_raw = str(event.get("timestamp", "")).strip()
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp_raw)
                if parsed_timestamp.tzinfo is None:
                    parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                parsed_timestamp = datetime.min.replace(tzinfo=timezone.utc)
            scored.append((score, parsed_timestamp, event))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    def _build_correction_lessons_block(self, events: List[Dict[str, Any]]) -> str:
        if not events:
            return ""
        lines = ["[Historical correction lessons]"]
        for event in events[:3]:
            normalized_intent = self._sanitize_correction_text(event.get("normalized_intent", ""))
            chosen_tool = self._normalize_correction_tool_name(event.get("chosen_tool", ""))
            correct_tool = self._normalize_correction_tool_name(event.get("correct_tool", ""))
            if not any((normalized_intent, chosen_tool, correct_tool)):
                continue
            lines.append("- Routing lesson:")
            lines.append(f"  Intent: {normalized_intent or '[unspecified]'}")
            if chosen_tool:
                lines.append(f"  Wrong choice: {chosen_tool}")
            if correct_tool:
                lines.append(f"  Correct tool: {correct_tool}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def _append_correction_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        memory = self._load_correction_memory()
        events = memory.setdefault("correction_events", [])
        task_id = str(event.get("task_id") or "").strip()
        if task_id:
            for idx, existing in enumerate(events):
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("task_id") or "").strip() != task_id:
                    continue
                merged_event = dict(existing)
                merged_event.update(event)
                merged_event["task_id"] = task_id
                merged_event["event_id"] = str(existing.get("event_id") or event.get("event_id") or f"corr_{uuid.uuid4().hex[:12]}")
                events[idx] = merged_event
                self._save_correction_memory(memory)
                return merged_event

        event = dict(event)
        event["event_id"] = str(event.get("event_id") or f"corr_{uuid.uuid4().hex[:12]}")
        events.append(event)
        if len(events) > 300:
            del events[:-300]
        self._save_correction_memory(memory)
        return event

    def record_tool_correction(
        self,
        task_info: Dict[str, Any],
        *,
        correct_tool: str,
        correct_instruction: str,
        user_note: str = "",
    ) -> Dict[str, Any]:
        chosen_tool = self._normalize_correction_tool_name(
            task_info.get("type")
            or task_info.get("execution_method")
            or task_info.get("method")
        )
        normalized_correct_tool = self._normalize_correction_tool_name(correct_tool)
        task_id = str(task_info.get("task_id") or task_info.get("id") or "").strip()
        task_type = chosen_tool  # Backward-compatible alias for existing readers of correction events.
        event = {
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "user_query": self._sanitize_correction_text(task_info.get("latest_user_request", "")),
            "normalized_intent": self._sanitize_correction_text(task_info.get("normalized_intent", "")),
            "recent_context": self._sanitize_recent_context(task_info.get("recent_context") or []),
            "chosen_tool": chosen_tool,
            "chosen_reason": self._sanitize_correction_text(
                task_info.get("decision_reason") or task_info.get("reason", "")
            ),
            "task_type": task_type,
            "task_description": self._sanitize_correction_text(task_info.get("task_description", "")),
            "correct_tool": normalized_correct_tool,
            "correct_instruction": self._sanitize_correction_text(correct_instruction),
            "user_note": self._sanitize_correction_text(user_note),
            "resolved": True,
        }
        return self._append_correction_event(event)

    async def _assess_unified_channels(
        self,
        conversation: str,
        *,
        qwenpaw_available: bool = False,
        openfang_available: bool = False,
        browser_available: bool = False,
        cu_available: bool = False,
        latest_user_request: str = "",
        normalized_intent: str = "",
        recent_context: Optional[List[Dict[str, str]]] = None,
        lang: str = "en",
    ) -> UnifiedChannelDecision:
        """一次 LLM 调用评估所有非 plugin 渠道（qwenpaw / openfang / browser / computer）。

        根据 available 标志动态组装 prompt，要求 LLM 选出最合适的渠道。
        如果 LLM 输出多个 can_execute=true，由调用方按优先级选取。
        """
        # 动态组装渠道描述 ──────────────────────────────────
        channel_descs: List[str] = []
        available_keys: List[str] = []

        if qwenpaw_available:
            available_keys.append("qwenpaw")
            channel_descs.append(_loc(CHANNEL_DESC_QWENPAW, lang))

        if openfang_available:
            available_keys.append("openfang")
            channel_descs.append(_loc(CHANNEL_DESC_OPENFANG, lang))

        if browser_available:
            available_keys.append("browser_use")
            channel_descs.append(_loc(CHANNEL_DESC_BROWSER_USE, lang))

        if cu_available:
            available_keys.append("computer_use")
            channel_descs.append(_loc(CHANNEL_DESC_COMPUTER_USE, lang))

        if not available_keys:
            return UnifiedChannelDecision()

        channels_block = "\n".join(channel_descs)
        keys_json = json.dumps(available_keys)
        json_fields = "\n".join(
            f'  "{k}": {{"can_execute": boolean, "task_description": "brief description", "reason": "why"}},'
            for k in available_keys
        )

        system_prompt = _loc(UNIFIED_CHANNEL_SYSTEM_PROMPT, lang).format(
            channels_block=channels_block,
            keys_json=keys_json,
            json_fields=json_fields,
        )
        lessons = self._build_correction_lessons_block(
            self._retrieve_relevant_corrections(
                latest_user_request,
                normalized_intent=normalized_intent,
                recent_context=recent_context,
            )
        )
        if lessons:
            system_prompt = f"{system_prompt}\n\n{lessons}"

        user_prompt = f"Conversation:\n{conversation}"

        max_retries = 3
        retry_delays = [1, 2]

        for attempt in range(max_retries):
            try:
                llm = self._get_llm(temperature=0, max_completion_tokens=AGENT_UNIFIED_ASSESS_MAX_TOKENS)

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]

                quota_error = await self._check_agent_quota("task_executor.assess_unified")
                if quota_error:
                    return UnifiedChannelDecision()

                response = await llm.ainvoke(messages)
                text = (response.content or "").strip()

                # LLM raw response 不写 logger
                logger.debug("[UnifiedAssessment] Raw response (len=%d chars)", len(text))
                print(f"[UnifiedAssessment] Raw response: {text[:500]}")

                if text.startswith("```"):
                    text = text.replace("```json", "").replace("```", "").strip()
                text = re.sub(r',(\s*[}\]])', r'\1', text)

                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    json_match = re.search(r'\{[\s\S]*\}', text)
                    if json_match:
                        try:
                            parsed = json.loads(re.sub(r',(\s*[}\]])', r'\1', json_match.group(0)))
                        except json.JSONDecodeError as e2:
                            logger.warning("[UnifiedAssessment] JSON parse failed after extraction: %s", e2)
                            return UnifiedChannelDecision()
                    else:
                        logger.warning("[UnifiedAssessment] No JSON found in response")
                        return UnifiedChannelDecision()

                result = UnifiedChannelDecision()
                for key in available_keys:
                    ch_data = parsed.get(key)
                    if isinstance(ch_data, dict):
                        setattr(result, key, ch_data)

                logger.info(
                    "[UnifiedAssessment] result: %s",
                    {k: (getattr(result, k) or {}).get("can_execute") for k in available_keys},
                )
                return result

            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                if attempt < max_retries - 1:
                    logger.warning("[UnifiedAssessment] Attempt %d failed: %s, retrying...", attempt + 1, e)
                    await asyncio.sleep(retry_delays[attempt])
                else:
                    logger.error("[UnifiedAssessment] Failed after %d attempts: %s", max_retries, e)
                    return UnifiedChannelDecision()
            except Exception as e:
                logger.error("[UnifiedAssessment] Unexpected error: %s", e)
                return UnifiedChannelDecision()

        return UnifiedChannelDecision()

    def _find_plugin_entry(self, plugins: Any, plugin_id: str, preferred_entry: str) -> tuple[Optional[dict], Optional[dict]]:
        """Find a plugin and a usable entry, falling back to the first declared entry."""
        iterable = plugins.items() if isinstance(plugins, dict) else enumerate(plugins)
        for _, plugin in iterable:
            if not isinstance(plugin, dict) or plugin.get("id") != plugin_id:
                continue
            entries = plugin.get("entries") or []
            if not isinstance(entries, list):
                return plugin, None
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id") == preferred_entry:
                    return plugin, entry
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id"):
                    return plugin, entry
            return plugin, None
        return None, None

    # NOTE: _rule_assess_openclaw / _assess_computer_use / _assess_browser_use / _assess_openfang
    # have been replaced by the unified _assess_unified_channels() method above.

    def _build_plugin_desc_lines(self, plugins: Any) -> list:
        """Build per-plugin description lines for LLM prompt."""
        lines = []
        try:
            iterable = plugins.items() if isinstance(plugins, dict) else enumerate(plugins)
            for _, p in iterable:
                pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
                desc = p.get("description", "") if isinstance(p, dict) else getattr(p, "description", "")
                entries = p.get("entries", []) if isinstance(p, dict) else getattr(p, "entries", []) or []
                if not pid:
                    continue
                entry_lines = []
                try:
                    for e in entries:
                        try:
                            eid = e.get("id") if isinstance(e, dict) else getattr(e, "id", None)
                            edesc = e.get("description", "") if isinstance(e, dict) else getattr(e, "description", "")
                            if not eid:
                                continue
                            schema_hint = ""
                            try:
                                schema = e.get("input_schema") if isinstance(e, dict) else getattr(e, "input_schema", None)
                                if isinstance(schema, dict):
                                    props = schema.get("properties", {})
                                    if isinstance(props, dict) and props:
                                        fields = []
                                        for fname, fdef in list(props.items())[:8]:
                                            ftype = fdef.get("type", "any") if isinstance(fdef, dict) else "any"
                                            fields.append(f"{fname}:{ftype}")
                                        required = schema.get("required", [])
                                        req_str = f" required={required}" if required else ""
                                        schema_hint = f" args({', '.join(fields)}{req_str})"
                            except Exception:
                                pass
                            part = f"{eid}: {edesc}" if edesc else eid
                            if schema_hint:
                                part += schema_hint
                            entry_lines.append(part)
                        except Exception:
                            continue
                except Exception:
                    entry_lines = []
                entry_desc = "; ".join(entry_lines) if entry_lines else "(default 'run' entry)"
                lines.append(f"- {pid}: {desc} | entries: [{entry_desc}]")
        except Exception:
            pass
        return lines

    # Stage-1 触发阈值。Stage 1 削减 stage 2 LLM prompt 长度的代价是 BM25
    # (~1 ms) 和 LLM coarse-screen（emotion tier，几百 ms）。两级分流：
    #   plugins_desc ≤ _STAGE1_TRIGGER_TOKENS → 完全跳过 stage 1
    #   plugins_desc >  _STAGE1_TRIGGER_TOKENS → BM25 与 LLM coarse-screen 用
    #     asyncio.gather 并行；关键路径 ≈ max(BM25, LLM) ≈ LLM 时长。
    _STAGE1_TRIGGER_TOKENS = AGENT_PLUGIN_DESC_BM25_THRESHOLD

    async def _stage1_llm_coarse_screen(
        self, user_text: str, plugins: list, lang: str = "en",
    ) -> list[str]:
        """Stage 1 LLM coarse screening: return list of plugin IDs deemed relevant."""
        from utils.tokenize import count_tokens, truncate_to_tokens
        summaries = []
        for p in plugins:
            pid = p.get("id", "unknown") if isinstance(p, dict) else "unknown"
            short = (p.get("short_description") or p.get("description", "")) if isinstance(p, dict) else ""
            if count_tokens(short) > AGENT_PLUGIN_SHORTDESC_MAX_TOKENS:
                # 给 "..." 预留 token 空间，保证最终长度 ≤ 200
                short = truncate_to_tokens(short, AGENT_PLUGIN_SHORTDESC_MAX_TOKENS - count_tokens("...")) + "..."
            summaries.append(f"- {pid}: {short}")
        plugin_summaries = "\n".join(summaries)

        system_prompt = _loc(USER_PLUGIN_COARSE_SCREEN_PROMPT, lang).format(
            plugin_summaries=plugin_summaries,
            user_text=user_text,
        )

        try:
            # 走 emotion tier（qwen-flash 等 latency-sensitive 档），粗筛只要
            # 快、JSON list 输出准确即可；BM25 + keyword hits 兜底，coarse-
            # screen 漏一两个候选不会让最终决策崩。
            llm = self._get_llm(temperature=0, max_completion_tokens=AGENT_PLUGIN_COARSE_MAX_TOKENS, tier="emotion")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ]

            quota_error = await self._check_agent_quota("task_executor.coarse_screen")
            if quota_error:
                return []

            response = await llm.ainvoke(messages)
            text = (response.content or "").strip()
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            ids = robust_json_loads(text)
            if isinstance(ids, list):
                return [str(i) for i in ids if isinstance(i, (str, int))]
        except Exception as e:
            logger.warning("[PluginFilter] Stage1 LLM coarse screen failed: %s", e)
        return []

    async def _assess_user_plugin(self, conversation: str, plugins: Any, lang: str = "en") -> UserPluginDecision:
        """
        Two-stage plugin assessment:
        - Stage 2 only (< 4000 chars): full LLM assessment with all plugins
        - Stage 1 + 2 (>= 4000 chars): BM25 + LLM coarse screen + keyword → filtered → Stage 2
        """
        # 如果没有插件，快速返回
        try:
            if not plugins:
                return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason="No plugins")
        except Exception:
            logger.debug("[UserPlugin] Failed to check plugins validity", exc_info=True)
            return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason="Invalid plugins")

        # Normalize plugins to list of dicts, skip passive plugins (they don't participate in analysis)
        plugin_list: list[dict] = []
        skipped_passive = 0
        try:
            iterable = plugins.items() if isinstance(plugins, dict) else enumerate(plugins)
            for _, p in iterable:
                if isinstance(p, dict):
                    if p.get("passive"):
                        skipped_passive += 1
                        continue
                    plugin_list.append(p)
        except Exception:
            logger.debug("[UserPlugin] Failed to normalize plugins to list, continuing with empty list", exc_info=True)
        if skipped_passive:
            logger.debug("[UserPlugin] Skipped %d passive plugin(s)", skipped_passive)
        if not plugin_list:
            return UserPluginDecision(
                has_task=False, can_execute=False, task_description="",
                plugin_id=None, plugin_args=None, reason="No active plugins",
            )

        # Extract user intent for keyword / BM25 matching
        user_intent = self._extract_latest_user_intent(conversation)

        # Build full description
        lines = self._build_plugin_desc_lines(plugin_list)
        plugins_desc = "\n".join(lines) if lines else "No plugins available."

        # Check keyword hits across ALL plugins (needed for annotation in both paths)
        keyword_hit_ids: list[str] = []
        for p in plugin_list:
            kws = p.get("keywords", [])
            pid = p.get("id", "")
            if isinstance(kws, list) and kws and pid and _match_keywords(
                user_intent or conversation, kws
            ):
                keyword_hit_ids.append(pid)

        # ── Two-stage decision ──────────────────────────────────
        # plugins_desc ≤ trigger 完全跳过；超阈值则 BM25 与 LLM coarse-screen
        # （emotion tier，latency-sensitive）asyncio.gather 并行执行。
        from utils.tokenize import count_tokens
        plugins_desc_tokens = count_tokens(plugins_desc)
        plugin_count = len(plugin_list)

        if plugins_desc_tokens <= self._STAGE1_TRIGGER_TOKENS:
            logger.debug(
                "[UserPlugin] Skipping stage 1: %d plugins, %d tokens",
                plugin_count, plugins_desc_tokens,
            )
            plugins = plugin_list
        else:
            try:
                logger.info(
                    "[UserPlugin] Stage 1: BM25 || LLM coarse-screen (gather), "
                    "plugins_desc=%d tokens, %d plugins",
                    plugins_desc_tokens, plugin_count,
                )
                (bm25_filtered, _), llm_ids = await asyncio.gather(
                    asyncio.to_thread(
                        stage1_filter,
                        user_intent or conversation,
                        plugin_list,
                        bm25_top_k=10,
                    ),
                    self._stage1_llm_coarse_screen(
                        user_intent or conversation, plugin_list, lang=lang,
                    ),
                )
                bm25_ids = {p.get("id") for p in bm25_filtered if isinstance(p, dict)}
                llm_id_set = set(llm_ids)

                # Union: BM25 + LLM coarse-screen + keyword hits
                selected_ids = bm25_ids | llm_id_set | set(keyword_hit_ids)

                if not selected_ids:
                    logger.info("[UserPlugin] Stage 1: no plugins selected, falling back to full list for stage 2")
                    stage2_plugins = plugin_list
                else:
                    stage2_plugins = [p for p in plugin_list if p.get("id") in selected_ids]
                    lines = self._build_plugin_desc_lines(stage2_plugins)
                    plugins_desc = "\n".join(lines) if lines else "No plugins available."

                logger.info(
                    "[UserPlugin] Stage 1 result: %d/%d plugins -> stage 2 (bm25=%d, llm=%d, kw=%d)",
                    len(stage2_plugins), plugin_count,
                    len(bm25_ids), len(llm_id_set), len(keyword_hit_ids),
                )
                plugins = stage2_plugins
            except Exception as stage1_err:
                logger.warning("[UserPlugin] Stage 1 failed, falling back to full list: %s", stage1_err)
                plugins = plugin_list

        # Annotate keyword-hit plugins
        plugins_desc = annotate_keyword_hits(plugins_desc, keyword_hit_ids)

        # plugin descriptions 可能含用户安装的插件配置文本（含 prompt 模板），不写 logger
        logger.debug(f"[UserPlugin] passing plugin descriptions (len={len(plugins_desc)})")
        print(f"[UserPlugin] plugin descriptions: {plugins_desc[:1000]}")

        # Stage 2: full LLM assessment
        system_prompt = _loc(USER_PLUGIN_SYSTEM_PROMPT, lang).format(plugins_desc=plugins_desc)

        user_prompt = f"Conversation:\n{conversation}\n\nUser intent (one-line): {user_intent}"

        max_retries = 3
        retry_delays = [1, 2]
        up_retry_done = False
        
        for attempt in range(max_retries):
            try:
                llm = self._get_llm(temperature=0, max_completion_tokens=AGENT_PLUGIN_FULL_MAX_TOKENS)

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]

                quota_error = await self._check_agent_quota("task_executor.assess_user_plugin")
                if quota_error:
                    return UserPluginDecision(
                        has_task=False,
                        can_execute=False,
                        task_description="",
                        plugin_id=None,
                        plugin_args=None,
                        reason=quota_error,
                    )
                response = await llm.ainvoke(messages)
                raw_text = response.content
                # Log the prompts we sent (truncated) and the raw response (truncated) at INFO level
                try:
                    prompt_dump = (system_prompt + "\n\n" + user_prompt)[:2000]
                except Exception:
                    prompt_dump = "(failed to build prompt dump)"
                # prompt 含用户输入 + LLM 响应原文，不写 logger
                logger.debug(f"[UserPlugin Assessment] prompt (truncated, len={len(prompt_dump)})")
                print(f"[UserPlugin Assessment] prompt (truncated): {prompt_dump}")
                _raw_repr = repr(raw_text) if raw_text is not None else "None"
                logger.debug(f"[UserPlugin Assessment] raw LLM response (len={len(_raw_repr)})")
                print(f"[UserPlugin Assessment] raw LLM response: {_raw_repr[:2000]}")
                
                text = raw_text.strip() if isinstance(raw_text, str) else ""
                
                if text.startswith("```"):
                    text = text.replace("```json", "").replace("```", "").strip()
                
                # If the response is empty or not valid JSON, log and return a safe decision
                if not text:
                    logger.warning("[UserPlugin Assessment] Empty LLM response; cannot parse JSON")
                    return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason="Empty LLM response")
                
                # Try to fix common JSON issues before parsing
                # Remove trailing commas before closing braces/brackets
                # Fix trailing commas in objects and arrays
                text = re.sub(r',(\s*[}\]])', r'\1', text)
                # NOTE: 避免"去注释"误伤字符串内容；只做最小化 JSON 修复
                # 不删除注释，因为正则表达式会误伤 JSON 字符串中的内容（如 http://、/*...*/）
                
                try:
                    decision = json.loads(text)
                except Exception as e:
                    # raw_text 含 LLM 原文，不写 logger
                    _raw_dump = repr(raw_text) if raw_text is not None else "None"
                    logger.debug(
                        "[UserPlugin Assessment] JSON parse error; raw_text len=%d",
                        len(_raw_dump),
                    )
                    print(f"[UserPlugin Assessment] JSON parse error; raw_text (truncated): {_raw_dump[:2000]}")
                    # ERROR 级别只记录错误信息，不包含敏感内容
                    logger.exception("[UserPlugin Assessment] JSON parse error")
                    # Try to extract JSON from the text if it's embedded in other text
                    try:
                        # Try to find JSON object in the text (improved regex to handle nested objects)
                        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*\}', text)
                        if json_match:
                            cleaned_text = json_match.group(0)
                            # Fix trailing commas again
                            cleaned_text = re.sub(r',(\s*[}\]])', r'\1', cleaned_text)
                            decision = json.loads(cleaned_text)
                            logger.info("[UserPlugin Assessment] Successfully extracted JSON from text")
                        else:
                            # JSON extraction failed - return safe default instead of trying to reconstruct
                            logger.warning("[UserPlugin Assessment] Failed to extract valid JSON from response")
                            return UserPluginDecision(
                                has_task=False, 
                                can_execute=False, 
                                task_description="", 
                                plugin_id=None, 
                                plugin_args=None, 
                                reason=f"JSON parse error: {e}"
                            )
                    except Exception as e2:
                        logger.warning(f"[UserPlugin Assessment] Failed to extract JSON: {e2}")
                        return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason=f"JSON parse error: {e}")
                
                # Validate plugin_id and entry_id against known plugins before returning.
                # If invalid, retry once with a corrective hint.
                d_has = decision.get("has_task", False)
                d_can = decision.get("can_execute", False)
                d_pid = decision.get("plugin_id")
                d_eid = decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id")

                # Build lookup from plugins param (always, so final validation can use it)
                valid_entries_map: Dict[str, List[str]] = {}
                try:
                    p_iter = plugins.items() if isinstance(plugins, dict) else enumerate(plugins)
                    for _, p in p_iter:
                        pid = p.get("id") if isinstance(p, dict) else None
                        if not pid:
                            continue
                        eids = []
                        for e in (p.get("entries") or []) if isinstance(p, dict) else []:
                            eid = e.get("id") if isinstance(e, dict) else None
                            if eid:
                                eids.append(eid)
                        valid_entries_map[pid] = eids
                except Exception:
                    valid_entries_map = {}

                # Normalize numeric plugin_id (LLM may return int instead of str)
                if isinstance(d_pid, int):
                    d_pid = str(d_pid)
                    decision["plugin_id"] = d_pid

                if d_has and d_can:
                    correction_hint = None
                    if not d_pid:
                        correction_hint = f"plugin_id is required when has_task/can_execute are true. Available plugins: {list(valid_entries_map.keys())}"
                    elif d_pid not in valid_entries_map:
                        correction_hint = f"plugin_id '{d_pid}' does not exist. Available plugins: {list(valid_entries_map.keys())}"
                    elif not d_eid and valid_entries_map.get(d_pid):
                        correction_hint = (
                            f"entry_id is required for plugin '{d_pid}' when has_task/can_execute are true. "
                            f"Available entries: {valid_entries_map.get(d_pid, [])}"
                        )
                    elif valid_entries_map[d_pid] and d_eid not in valid_entries_map[d_pid]:
                        correction_hint = f"entry_id '{d_eid}' does not exist in plugin '{d_pid}'. Available entries: {valid_entries_map[d_pid]}"

                    if correction_hint and not up_retry_done:
                        logger.info("[UserPlugin Assessment] Invalid decision, retrying with hint: %s", correction_hint)
                        up_retry_done = True
                        # Append correction as assistant+user follow-up to guide the LLM
                        messages.append({"role": "assistant", "content": text})
                        messages.append({"role": "user", "content": f"CORRECTION: {correction_hint}. Please fix your response and return a valid JSON."})
                        try:
                            response2 = await llm.ainvoke(messages)
                            raw2 = response2.content
                            t2 = raw2.strip() if isinstance(raw2, str) else ""
                            if t2.startswith("```"):
                                t2 = t2.replace("```json", "").replace("```", "").strip()
                            t2 = re.sub(r',(\s*[}\]])', r'\1', t2)
                            decision2 = json.loads(t2)
                            logger.info("[UserPlugin Assessment] Retry response parsed: %s", {k: decision2.get(k) for k in ("has_task", "can_execute", "plugin_id", "entry_id")})
                            decision = decision2
                            d_pid = decision.get("plugin_id")
                            if isinstance(d_pid, int):
                                d_pid = str(d_pid)
                                decision["plugin_id"] = d_pid
                            d_eid = decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id")
                        except Exception as e_retry:
                            logger.warning("[UserPlugin Assessment] Retry failed: %s", e_retry)

                # Final validation: reject if plugin_id/entry_id still invalid after retry
                final_pid = decision.get("plugin_id")
                if isinstance(final_pid, int):
                    final_pid = str(final_pid)
                    decision["plugin_id"] = final_pid
                final_eid = decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id")
                final_has = decision.get("has_task", False)
                final_can = decision.get("can_execute", False)
                if final_has and final_can:
                    if valid_entries_map and final_pid not in valid_entries_map:
                        logger.warning("[UserPlugin Assessment] Final check: plugin_id '%s' still invalid after retry, forcing can_execute=false", final_pid)
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = f"plugin_id '{final_pid}' not found"
                    elif not final_eid and valid_entries_map.get(final_pid):
                        logger.warning(
                            "[UserPlugin Assessment] Final check: entry_id missing while has_task/can_execute=true (plugin_id=%s), forcing can_execute=false",
                            final_pid,
                        )
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = "entry_id missing"
                    elif not final_eid:
                        # Plugin has no declared entries — fall back to default 'run'
                        final_eid = "run"
                        decision["entry_id"] = "run"
                    elif valid_entries_map and valid_entries_map.get(final_pid) and final_eid not in valid_entries_map[final_pid]:
                        logger.warning("[UserPlugin Assessment] Final check: entry_id '%s' still invalid for plugin '%s', forcing can_execute=false", final_eid, final_pid)
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = f"entry_id '{final_eid}' not found in plugin '{final_pid}'"

                plugin_args = decision.get("plugin_args")

                return UserPluginDecision(
                    has_task=decision.get("has_task", False),
                    can_execute=decision.get("can_execute", False),
                    task_description=decision.get("task_description", ""),
                    plugin_id=decision.get("plugin_id"),
                    entry_id=final_eid,
                    plugin_args=plugin_args,
                    reason=decision.get("reason", "")
                )
                
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])
                else:
                    return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason=f"Assessment error: {e}")
            except Exception as e:
                return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason=f"Assessment error: {e}")

        return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason="No suitable plugin")

    async def analyze_and_execute(
        self,
        messages: List[Dict[str, str]],
        lanlan_name: Optional[str] = None,
        agent_flags: Optional[Dict[str, bool]] = None,
        conversation_id: Optional[str] = None,
        lang: str = "en",
    ) -> Optional[TaskResult]:
        """
        评估各渠道可行性，返回 Decision（不执行）。
        Plugin 单独判定；qwenpaw/openfang/browser/computer 合并为一次 LLM 调用。
        实际执行由 agent_server 统一 dispatch。
        """
        task_id = str(uuid.uuid4())

        if agent_flags is None:
            agent_flags = {"computer_use_enabled": False, "browser_use_enabled": False}

        computer_use_enabled = agent_flags.get("computer_use_enabled", False)
        browser_use_enabled = agent_flags.get("browser_use_enabled", False)
        user_plugin_enabled = agent_flags.get("user_plugin_enabled", False)
        openfang_enabled = agent_flags.get("openfang_enabled", False)
        openclaw_enabled = agent_flags.get("openclaw_enabled", False)

        logger.debug(
            "[TaskExecutor] analyze_and_execute: task_id=%s lanlan=%s flags={cu=%s, bu=%s, up=%s, nk=%s, of=%s}",
            task_id, lanlan_name, computer_use_enabled, browser_use_enabled, user_plugin_enabled, openclaw_enabled, openfang_enabled,
        )

        if not computer_use_enabled and not browser_use_enabled and not user_plugin_enabled and not openclaw_enabled and not openfang_enabled:
            logger.debug("[TaskExecutor] All execution channels disabled, skipping")
            return None

        # 格式化对话
        conversation = self._format_messages(messages)
        if not conversation.strip():
            return None
        latest_user_request = self._extract_latest_user_intent(conversation)
        recent_context = self._extract_recent_context(messages)
        normalized_intent = self._normalize_user_intent(latest_user_request, recent_context)

        # ── 可用性检查 ──────────────────────────────────────
        cu_available = False
        if computer_use_enabled:
            try:
                cu_status = await asyncio.to_thread(self.computer_use.is_available)
                cu_available = cu_status.get('ready', False) if isinstance(cu_status, dict) else False
                logger.info("[TaskExecutor] ComputerUse available: %s", cu_available)
            except Exception as e:
                logger.warning("[TaskExecutor] Failed to check ComputerUse: %s", e)

        browser_available = False
        if browser_use_enabled:
            try:
                bu_status = await asyncio.to_thread(self.browser_use.is_available)
                browser_available = bu_status.get("ready", False) if isinstance(bu_status, dict) else False
                logger.info("[TaskExecutor] BrowserUse available: %s", browser_available)
            except Exception as e:
                logger.warning("[TaskExecutor] Failed to check BrowserUse: %s", e)

        of_available = False
        if openfang_enabled and self.openfang:
            try:
                of_available = self.openfang.init_ok
                logger.info("[TaskExecutor] OpenFang available: %s", of_available)
            except Exception as e:
                logger.warning("[TaskExecutor] Failed to check OpenFang: %s", e)

        qwenpaw_available = False
        if openclaw_enabled and self.openclaw:
            try:
                # openclaw.is_available 内部走 sync httpx，必须 offload
                oc_status = await asyncio.to_thread(self.openclaw.is_available)
                qwenpaw_available = oc_status.get("ready", False) if isinstance(oc_status, dict) else False
                logger.info("[TaskExecutor] QwenPaw available: %s", qwenpaw_available)
            except Exception as e:
                logger.warning("[TaskExecutor] Failed to check QwenPaw: %s", e)

        # ── 魔法命令前置拦截（仅对 openclaw/qwenpaw）──────────────────────
        user_intent, user_attachments = self._extract_latest_user_payload(messages)
        if not user_intent:
            user_intent = self._extract_latest_user_intent(conversation)
        if qwenpaw_available and self.openclaw and user_intent and not user_attachments:
            try:
                magic_intent = await self.openclaw.classify_magic_intent(user_intent)
            except Exception as e:
                logger.warning("[TaskExecutor] Failed to classify magic intent: %s", e)
                magic_intent = {"is_magic_intent": False, "command": None}
            if magic_intent.get("is_magic_intent") and magic_intent.get("command"):
                magic_command = str(magic_intent["command"])
                logger.info(
                    "[TaskExecutor] Magic intent intercepted: command=%s source=%s",
                    magic_command,
                    magic_intent.get("source", "unknown"),
                )
                return TaskResult(
                    task_id=task_id,
                    has_task=True,
                    task_description=self.openclaw.get_magic_command_task_description(magic_command),
                    execution_method="openclaw",
                    success=False,
                    tool_args={
                        "instruction": magic_command,
                        "attachments": [],
                        "magic_command": magic_command,
                        "original_user_text": user_intent,
                        "direct_reply": True,
                    },
                    reason=f"magic_intent:{magic_intent.get('source', 'unknown')}",
                )

        # ── 并行执行：plugin 单独 + 统一渠道评估 ──────────────
        parallel_tasks: List[tuple] = []   # [(key, coro), ...]

        # Plugin 支路
        plugins = []
        if user_plugin_enabled:
            await self.plugin_list_provider()
            plugins = self.plugin_list
        if user_plugin_enabled and plugins:
            parallel_tasks.append(('up', self._assess_user_plugin(conversation, plugins, lang=lang)))

        # 统一渠道评估（qwenpaw / openfang / browser / computer）
        has_any_unified = qwenpaw_available or of_available or browser_available or cu_available
        if has_any_unified:
            parallel_tasks.append(('unified', self._assess_unified_channels(
                conversation,
                qwenpaw_available=qwenpaw_available,
                openfang_available=of_available,
                browser_available=browser_available,
                cu_available=cu_available,
                latest_user_request=latest_user_request,
                normalized_intent=normalized_intent,
                recent_context=recent_context,
                lang=lang,
            )))

        if not parallel_tasks:
            logger.debug("[TaskExecutor] No assessment tasks to run")
            return None

        logger.info("[TaskExecutor] Running %d assessment(s) in parallel...", len(parallel_tasks))
        results = await asyncio.gather(*[t[1] for t in parallel_tasks], return_exceptions=True)

        up_decision: Optional[UserPluginDecision] = None
        unified: Optional[UnifiedChannelDecision] = None

        for i, (key, _) in enumerate(parallel_tasks):
            r = results[i]
            if isinstance(r, Exception):
                logger.error("[TaskExecutor] %s assessment failed: %s", key, r)
                continue
            if key == 'up':
                up_decision = r
                logger.info(
                    "[UserPlugin] has_task=%s, can_execute=%s, reason=%s",
                    getattr(up_decision, 'has_task', None),
                    getattr(up_decision, 'can_execute', None),
                    getattr(up_decision, 'reason', None),
                )
            elif key == 'unified':
                unified = r

        # ── 决策逻辑 ──────────────────────────────────────
        # 1. UserPlugin（plugin 单独判定，优先级最高）
        if isinstance(up_decision, UserPluginDecision) and up_decision.has_task and up_decision.plugin_id and up_decision.entry_id:
            if not up_decision.can_execute:
                logger.info(
                    "[TaskExecutor] UserPlugin refused (can_execute=False): plugin_id=%s, entry_id=%s, reason=%s",
                    up_decision.plugin_id, up_decision.entry_id, up_decision.reason,
                )
                return TaskResult(task_id=task_id, has_task=False, reason=up_decision.reason)
            # task_description 是 LLM 决策出的任务描述，不写 logger
            logger.info("[TaskExecutor] Using UserPlugin (desc_len=%d), plugin_id=%s", len(up_decision.task_description or ""), up_decision.plugin_id)
            print(f"[TaskExecutor] Using UserPlugin: {up_decision.task_description}, plugin_id={up_decision.plugin_id}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=up_decision.task_description,
                execution_method='user_plugin',
                success=False,
                tool_name=up_decision.plugin_id,
                tool_args=up_decision.plugin_args,
                entry_id=up_decision.entry_id,
                reason=up_decision.reason,
            )

        # 2. 统一渠道 — 按优先级 qwenpaw > openfang > browser_use > computer_use
        if isinstance(unified, UnifiedChannelDecision):
            for ch_key in _CHANNEL_PRIORITY:
                ch_info = getattr(unified, ch_key, None)
                if not isinstance(ch_info, dict) or not ch_info.get("can_execute"):
                    continue

                method = _CHANNEL_TO_METHOD[ch_key]
                task_desc = ch_info.get("task_description", "")
                reason = ch_info.get("reason", "")
                logger.info("[TaskExecutor] Using %s: %s", method, task_desc)

                tool_args = None
                if method == "openclaw":
                    tool_args = {"instruction": user_intent, "attachments": user_attachments}
                result_context_kwargs = {}
                if method in {"browser_use", "computer_use"}:
                    result_context_kwargs = {
                        "latest_user_request": latest_user_request,
                        "normalized_intent": normalized_intent,
                        "recent_context": recent_context,
                    }

                return TaskResult(
                    task_id=task_id,
                    has_task=True,
                    task_description=task_desc,
                    execution_method=method,
                    success=False,
                    tool_args=tool_args,
                    reason=reason,
                    **result_context_kwargs,
                )

        # 3. 没有可执行的分支，汇总原因
        reason_parts = []
        if isinstance(up_decision, UserPluginDecision):
            reason_parts.append(f"UserPlugin: {up_decision.reason}")
        if isinstance(unified, UnifiedChannelDecision):
            for ch_key in _CHANNEL_PRIORITY:
                ch_info = getattr(unified, ch_key, None)
                if isinstance(ch_info, dict):
                    reason_parts.append(f"{ch_key}: {ch_info.get('reason', 'N/A')}")

        has_any_task = False
        task_desc = ""
        if isinstance(unified, UnifiedChannelDecision):
            for ch_key in _CHANNEL_PRIORITY:
                ch_info = getattr(unified, ch_key, None)
                if isinstance(ch_info, dict) and ch_info.get("task_description"):
                    has_any_task = True
                    task_desc = ch_info["task_description"]
                    break
        if not has_any_task and isinstance(up_decision, UserPluginDecision) and up_decision.has_task:
            has_any_task = True
            task_desc = up_decision.task_description

        if has_any_task:
            logger.info("[TaskExecutor] Task detected but cannot execute: %s", task_desc)
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_desc,
                execution_method='none',
                success=False,
                reason=" | ".join(reason_parts) if reason_parts else "No suitable method",
            )

        logger.debug("[TaskExecutor] No task detected")
        return None

    async def _execute_user_plugin(
        self,
        task_id: str,
        *,
        plugin_id: Optional[str],
        plugin_args: Optional[Dict] = None,
        entry_id: Optional[str] = None,
        task_description: str = "",
        reason: str = "",
        lanlan_name: Optional[str] = None,
        conversation_id: Optional[str] = None,
        on_progress: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> TaskResult:
        """
        Execute a user plugin via HTTP /runs endpoint.
        This is the single implementation for all plugin execution paths.
        """
        plugin_args = dict(plugin_args) if isinstance(plugin_args, dict) else {}
        plugin_entry_id = (
            entry_id
            or (plugin_args.pop("_entry", None) if isinstance(plugin_args, dict) else None))
        
        if not plugin_id:
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method='user_plugin',
                success=False,
                error="No plugin_id provided",
                reason=reason
            )
        
        # Ensure we have a plugins list to search (use cached self.plugin_list as fallback)
        try:
            plugins_list = self.plugin_list or []
        except Exception:
            plugins_list = []
        # If cache is empty, attempt to refresh once
        if not plugins_list:
            try:
                await self.plugin_list_provider(force_refresh=True)
                plugins_list = self.plugin_list or []
            except Exception:
                plugins_list = []
        
        # Find plugin metadata in the resolved plugins list
        plugin_meta = None
        for p in plugins_list:
            try:
                if isinstance(p, dict) and p.get("id") == plugin_id:
                    plugin_meta = p
                    break
            except Exception:
                logger.debug(f"[UserPlugin] Skipped malformed plugin entry during lookup: {p}", exc_info=True)
                continue
        
        if plugin_meta is None:
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method='user_plugin',
                success=False,
                error=f"Plugin {plugin_id} not found",
                tool_name=plugin_id,
                tool_args=plugin_args,
                reason=reason or "Plugin not found"
            )

        # Strict entry_id validation: only allow case-insensitive exact match as minor tolerance.
        if plugin_entry_id and plugin_meta:
            known_entries = []
            for e in (plugin_meta.get("entries") or []):
                eid = e.get("id") if isinstance(e, dict) else None
                if eid:
                    known_entries.append(eid)
            if known_entries and plugin_entry_id not in known_entries:
                # Only tolerate case-insensitive exact match (e.g. "Run" vs "run")
                ci_matches = [e for e in known_entries if e.lower() == plugin_entry_id.lower()]
                if len(ci_matches) == 1:
                    resolved = ci_matches[0]
                    logger.info("[UserPlugin] Case-insensitive entry_id match: '%s' → '%s' (plugin=%s)", plugin_entry_id, resolved, plugin_id)
                    plugin_entry_id = resolved
                elif len(ci_matches) > 1:
                    logger.warning(
                        "[UserPlugin] Ambiguous case-insensitive entry_id '%s' in plugin '%s': multiple matches %s — not resolving",
                        plugin_entry_id, plugin_id, ci_matches,
                    )
                else:
                    logger.warning("[UserPlugin] entry_id '%s' not found in plugin '%s' entries: %s — rejecting", plugin_entry_id, plugin_id, known_entries)
                    return TaskResult(
                        task_id=task_id,
                        has_task=True,
                        task_description=task_description,
                        execution_method='user_plugin',
                        success=False,
                        error=f"entry_id '{plugin_entry_id}' not found in plugin '{plugin_id}'. Available: {known_entries}",
                        tool_name=plugin_id,
                        tool_args=plugin_args,
                        entry_id=plugin_entry_id,
                        reason=reason or "invalid_entry_id",
                    )
        # New run protocol: default path (POST /runs, return accepted immediately)
        try:
            runs_endpoint = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs"

            safe_args: Dict[str, Any]
            if isinstance(plugin_args, dict):
                safe_args = dict(plugin_args)
            else:
                safe_args = {}
            try:
                # 构建 _ctx 对象，包含 lanlan_name 和 conversation_id
                ctx_obj = safe_args.get("_ctx")
                if not isinstance(ctx_obj, dict):
                    ctx_obj = {}
                if lanlan_name and "lanlan_name" not in ctx_obj:
                    ctx_obj["lanlan_name"] = lanlan_name
                # 添加 conversation_id，用于关联触发事件和对话上下文
                if conversation_id:
                    ctx_obj["conversation_id"] = conversation_id
                entry_timeout = _resolve_plugin_entry_timeout(plugin_meta, plugin_entry_id)
                effective_entry_timeout = _resolve_ctx_entry_timeout(ctx_obj, entry_timeout)
                ctx_obj["entry_timeout"] = effective_entry_timeout
                if ctx_obj:
                    safe_args["_ctx"] = ctx_obj
            except Exception as e:
                logger.warning(
                    "[TaskExecutor] Failed to build _ctx: lanlan=%s conversation_id=%s error=%s",
                    lanlan_name, conversation_id, e
                )
                effective_entry_timeout = _resolve_plugin_entry_timeout(plugin_meta, plugin_entry_id)

            run_wait_timeout = _compute_run_wait_timeout(effective_entry_timeout)

            run_body: Dict[str, Any] = {
                "task_id": task_id,
                "plugin_id": plugin_id,
                "entry_id": plugin_entry_id or "run",
                "args": safe_args,
            }

            timeout = httpx.Timeout(10.0, connect=2.0)
            async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
                r = await client.post(runs_endpoint, json=run_body)
                if not (200 <= r.status_code < 300):
                    logger.warning(
                        "[TaskExecutor] /runs returned non-2xx; status=%s body=%s",
                        r.status_code,
                        (r.text or "")[:1000],
                    )
                    raise RuntimeError(f"/runs returned {r.status_code}")
                try:
                    data = r.json()
                except Exception:
                    logger.error(
                        "[TaskExecutor] /runs returned non-JSON response; skip fallback to avoid duplicate execution. status=%s body=%s",
                        r.status_code,
                        (r.text or "")[:1000],
                    )
                    return TaskResult(
                        task_id=task_id,
                        has_task=True,
                        task_description=task_description,
                        execution_method="user_plugin",
                        success=False,
                        error="Invalid /runs response (non-JSON)",
                        tool_name=plugin_id,
                        tool_args=plugin_args,
                        entry_id=plugin_entry_id,
                        reason=reason or "run_invalid_response",
                    )

            run_id = data.get("run_id") if isinstance(data, dict) else None
            run_token = data.get("run_token") if isinstance(data, dict) else None
            expires_at = data.get("expires_at") if isinstance(data, dict) else None
            if not isinstance(run_id, str) or not run_id or not isinstance(run_token, str) or not run_token:
                logger.error(
                    "[TaskExecutor] /runs response missing run_id/run_token; skip fallback to avoid duplicate execution. data=%r",
                    data,
                )
                return TaskResult(
                    task_id=task_id,
                    has_task=True,
                    task_description=task_description,
                    execution_method="user_plugin",
                    success=False,
                    error="Invalid /runs response (missing run_id/run_token)",
                    tool_name=plugin_id,
                    tool_args=plugin_args,
                    entry_id=plugin_entry_id,
                    reason=reason or "run_invalid_response",
                )

            # Phase 2: await run completion and fetch actual result
            try:
                completion = await self._await_run_completion(
                    run_id, timeout=run_wait_timeout, on_progress=on_progress,
                )
            except Exception as e:
                logger.warning("[TaskExecutor] _await_run_completion error: %r", e)
                completion = {"status": "unknown", "success": False, "data": None,
                              "error": str(e)}

            run_success = bool(completion.get("success"))
            result_obj: Dict[str, Any] = {
                "accepted": True,
                "run_id": run_id,
                "run_token": run_token,
                "expires_at": expires_at,
                "entry_id": plugin_entry_id or "run",
                "run_status": completion.get("status"),
                "run_success": run_success,
                "run_data": completion.get("data"),
                "run_error": completion.get("run_error", completion.get("error")),
                "meta": completion.get("meta"),
                "message": completion.get("message"),
                "progress": completion.get("progress"),
                "stage": completion.get("stage"),
            }
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method="user_plugin",
                success=run_success,
                result=result_obj,
                error=completion.get("error") if not run_success else None,
                tool_name=plugin_id,
                tool_args=plugin_args,
                entry_id=plugin_entry_id,
                reason=reason or ("run_succeeded" if run_success else "run_failed"),
            )
        except Exception as e:
            logger.warning(
                "[TaskExecutor] /runs execution failed; no legacy fallback. error=%r",
                e,
            )
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method="user_plugin",
                success=False,
                error=str(e),
                tool_name=plugin_id,
                tool_args=plugin_args,
                entry_id=plugin_entry_id,
                reason=reason or "run_failed",
            )

    async def _await_run_completion(
        self,
        run_id: str,
        *,
        timeout: float | None = 300.0,
        poll_interval: float = 0.5,
        on_progress: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Poll /runs/{run_id} until it reaches a terminal state, then fetch the export result.

        Args:
            on_progress: Optional async callback ``(progress, stage, message, step, step_total) -> None``
                called whenever the run's progress/stage/message changes between polls.

        Returns a dict:
          {"status": str, "success": bool, "data": Any, "error": str|None,
           "progress": float|None, "stage": str|None, "message": str|None}
        """
        base = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}"
        terminal = frozenset(("succeeded", "failed", "canceled", "timeout"))
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        last_status: Optional[str] = None
        # Track last-seen progress fingerprint to avoid redundant callbacks
        _last_progress_key: Optional[tuple] = None
        _consecutive_errors = 0
        _MAX_CONSECUTIVE_ERRORS = 3

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=2.0), proxy=None, trust_env=False) as client:
            # ── Phase 1: poll until terminal ──
            while True:
                remaining = None if deadline is None else deadline - asyncio.get_event_loop().time()
                if remaining is not None and remaining <= 0:
                    return {"status": "timeout", "success": False, "data": None,
                            "error": f"Timed out waiting for run {run_id} ({timeout}s)"}
                try:
                    r = await client.get(f"{base}/runs/{run_id}")
                    if r.status_code in (404, 410):
                        return {"status": "failed", "success": False, "data": None,
                                "error": f"Run {run_id} not found (HTTP {r.status_code})"}
                    if r.status_code != 200:
                        _consecutive_errors += 1
                        logger.warning(
                            "[_await_run_completion] unexpected HTTP %s for run %s (%d/%d): %s",
                            r.status_code, run_id, _consecutive_errors, _MAX_CONSECUTIVE_ERRORS, r.text[:200],
                        )
                        if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                            return {"status": "failed", "success": False, "data": None,
                                    "error": f"Run {run_id} polling failed ({_consecutive_errors} consecutive HTTP {r.status_code})"}
                    if r.status_code == 200:
                        _consecutive_errors = 0
                        run_data = r.json()
                        last_status = run_data.get("status")
                        # Fire on_progress callback when progress/stage/message changes
                        if on_progress and last_status not in terminal:
                            cur_key = (
                                run_data.get("progress"),
                                run_data.get("stage"),
                                run_data.get("message"),
                                run_data.get("step"),
                            )
                            if cur_key != _last_progress_key:
                                _last_progress_key = cur_key
                                try:
                                    await on_progress(
                                        progress=run_data.get("progress"),
                                        stage=run_data.get("stage"),
                                        message=run_data.get("message"),
                                        step=run_data.get("step"),
                                        step_total=run_data.get("step_total"),
                                    )
                                except Exception:
                                    pass
                        if last_status in terminal:
                            break
                except Exception as e:
                    _consecutive_errors += 1
                    logger.warning(
                        "[_await_run_completion] poll error for run %s (%d/%d): %s",
                        run_id, _consecutive_errors, _MAX_CONSECUTIVE_ERRORS, e,
                    )
                    if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        return {"status": "failed", "success": False, "data": None,
                                "error": f"Run {run_id} polling failed ({_consecutive_errors} consecutive transport errors)"}
                sleep_for = poll_interval if remaining is None else min(poll_interval, remaining)
                await asyncio.sleep(sleep_for)

            # ── Phase 2: fetch export to get plugin_response ──
            plugin_result: Dict[str, Any] = {
                "status": last_status,
                "success": last_status == "succeeded",
                "data": None,
                "error": None,
                "progress": run_data.get("progress"),
                "stage": run_data.get("stage"),
                "message": run_data.get("message"),
            }

            if last_status in ("failed", "canceled", "timeout"):
                err = run_data.get("error")
                if isinstance(err, dict):
                    plugin_result["error"] = err.get("message") or str(err.get("code") or "unknown")
                elif isinstance(err, str):
                    plugin_result["error"] = err
                else:
                    plugin_result["error"] = f"Run {last_status}"

            try:
                r = await client.get(f"{base}/runs/{run_id}/export", params={"limit": 50})
                if r.status_code == 200:
                    export_data = r.json()
                    items = export_data.get("items") or []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        # Look for the system trigger_response export
                        if item.get("type") == "json" and (item.get("json") is not None or item.get("json_data") is not None):
                            raw = item.get("json") or item.get("json_data")
                            if isinstance(raw, dict):
                                plugin_result["data"] = raw.get("data")
                                plugin_result["meta"] = raw.get("meta")
                                if raw.get("error"):
                                    err = raw["error"]
                                    if isinstance(err, dict):
                                        plugin_result["error"] = err.get("message") or str(err)
                                    elif isinstance(err, str):
                                        plugin_result["error"] = err
                            break
            except Exception as e:
                logger.debug("[_await_run_completion] export fetch error: %s", e)

            return plugin_result

    async def execute_user_plugin_direct(
        self,
        task_id: str,
        plugin_id: str,
        plugin_args: Dict[str, Any],
        entry_id: Optional[str] = None,
        lanlan_name: Optional[str] = None,
        conversation_id: Optional[str] = None,
        on_progress: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> TaskResult:
        """
        Directly execute a plugin entry by calling /runs with explicit plugin_id and optional entry_id.
        This is intended for agent_server to call when it wants to trigger a plugin_entry immediately.
        """
        return await self._execute_user_plugin(
            task_id=task_id,
            plugin_id=plugin_id,
            plugin_args=plugin_args,
            entry_id=entry_id,
            task_description=f"Direct plugin call {plugin_id}",
            reason="direct_call",
            lanlan_name=lanlan_name,
            conversation_id=conversation_id,
            on_progress=on_progress,
        )
    
    async def refresh_capabilities(self) -> Dict[str, Dict[str, Any]]:
        """保留接口兼容性，MCP 已移除，始终返回空。"""
        return {}
