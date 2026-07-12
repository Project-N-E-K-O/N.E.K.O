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

"""
DirectTaskExecutor: merges the Analyzer + Planner roles
Evaluates ComputerUse / BrowserUse / UserPlugin feasibility in parallel
"""
import json
import os
import re
import hashlib
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
from utils.llm_client import openai_retry_error_types
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
    AGENT_EXTERNAL_GATE_ENABLED,
    AGENT_EXTERNAL_GATE_THRESHOLD,
    TASK_DETAIL_MAX_TOKENS,
)
from utils.llm_client import (
    create_chat_llm,
    ChatOpenAI,
    set_active_character,
    reset_active_character,
)
from config.prompts.prompts_agent import (
    UNIFIED_CHANNEL_SYSTEM_PROMPT,
    CHANNEL_DESC_QWENPAW,
    CHANNEL_DESC_OPENFANG,
    CHANNEL_DESC_BROWSER_USE,
    CHANNEL_DESC_COMPUTER_USE,
    USER_PLUGIN_SYSTEM_PROMPT,
    USER_PLUGIN_COARSE_SCREEN_PROMPT,
)
from config.prompts.prompts_sys import _loc
from utils.file_utils import atomic_write_json, robust_json_loads
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
    """Task execution result"""
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
    """ComputerUse feasibility assessment result"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    reason: str = ""


@dataclass
class BrowserUseDecision:
    """BrowserUse feasibility assessment result"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    reason: str = ""

@dataclass
class UserPluginDecision:
    """UserPlugin feasibility assessment result"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    plugin_id: Optional[str] = None
    entry_id: Optional[str] = None
    plugin_args: Optional[Dict] = None
    reason: str = ""


@dataclass
class OpenFangDecision:
    """OpenFang multi-agent execution decision"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    suggested_tools: Optional[List[str]] = None
    reason: str = ""


@dataclass
class OpenClawDecision:
    """OpenClaw standalone-agent execution decision"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    instruction: str = ""
    reason: str = ""


@dataclass
class UnifiedChannelDecision:
    """Unified channel assessment result — each channel is a dict or None"""
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
    Direct task executor: evaluates BrowserUse / ComputerUse / UserPlugin feasibility in parallel and executes
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
        # plugin_id -> (description_key, generated_short_description)
        # description_key = full description 的 hash（见 _desc_key）：既精确反映完整
        # description 的变化（截断只用于喂 LLM，不能当失效 key），又有界，避免超大
        # description 撑爆内存/缓存文件。只有 LLM 生成的条目会落盘（见
        # _persist_generated_short_descriptions）；manifest 自带 short_description
        # 的插件每次加载都能免费重新 prime，无需持久化。
        self._short_desc_cache_filename = "plugin_short_desc_cache.json"
        self._short_desc_cache: dict[str, tuple[str, str]] = self._load_short_desc_cache()
        # plugin ids currently being generated in a background prewarm task —
        # dedupes the per-analyze force_refresh so we don't pile up duplicate
        # generation tasks. The tasks set holds strong refs to prevent GC.
        self._short_desc_prewarm_inflight: set[str] = set()
        self._short_desc_prewarm_tasks: set = set()
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

    async def _set_character_context_token(self, lanlan_name: Optional[str]):
        """Fetch master_name from the config manager and bind the active
        character ``(master_name, lanlan_name)`` to the current async
        context. The wrapped LLM clients (``utils.llm_client.ChatOpenAI``)
        substitute ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders that
        come in via plugin-supplied prompt fragments before the wire send.

        Returns a token; pass to ``reset_active_character`` in a ``finally``
        block. Best-effort on failure — if config_manager can't yield a
        master_name, the token still binds with an empty master so partial
        substitution (lanlan only) still works and the leak check WARNING
        is at most a no-op.
        """
        master_name = ""
        try:
            cd = await self._config_manager.aget_character_data()
            # aget_character_data returns a tuple; element 0 is master_name
            if cd and len(cd) > 0 and isinstance(cd[0], str):
                master_name = cd[0]
        except Exception as exc:
            logger.debug(
                "[Agent] character-context fetch failed; placeholder substitution will be partial: %s: %s",
                type(exc).__name__, exc,
            )
        return set_active_character(master_name, lanlan_name or "")

    def set_plugin_list_provider(self, provider: Callable[[bool], Awaitable[List[Dict[str, Any]]]]):
        """Allow agent_server to inject a custom async provider for plugin discovery."""
        self._external_plugin_provider = provider

    @staticmethod
    def _desc_key(desc: str) -> str:
        """Stable, bounded validity key for a plugin description. The cache hits
        only while the *full* description is unchanged; hashing keeps the key
        small (a plugin's raw description is uncapped)."""
        return hashlib.sha256((desc or "").encode("utf-8")).hexdigest()

    def _apply_cached_short_descriptions(self, plugins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply manifest-provided or previously-generated short_description
        onto each plugin dict. Pure manifest/cache read — NEVER calls the LLM,
        so it is safe on the analyze hot path.

        Returns the plugins that are still missing a short_description (and have
        a description to summarize) — i.e. the candidates for background prewarm.
        """
        missing: list[dict] = []
        for p in plugins:
            if not isinstance(p, dict):
                continue
            pid = p.get("id", "")
            short = str(p.get("short_description", "") or "").strip()
            desc = str(p.get("description", "") or "").strip()
            if not short:
                # Apply cached value if available and the (full) description
                # hasn't changed. Key off the full description, not the truncated
                # one used for the LLM prompt, so long-description plugins still hit.
                cached = self._short_desc_cache.get(pid)
                if cached and cached[0] == self._desc_key(desc):
                    p["short_description"] = cached[1]
                    continue
                if desc:
                    missing.append(p)
            elif pid:
                # (a) manifest already carries short_description — use it as-is,
                # zero LLM. Prime the cache so it survives a desc-unchanged refresh.
                self._short_desc_cache[pid] = (self._desc_key(desc), short)
        return missing

    def _schedule_short_desc_prewarm(self, plugins: List[Dict[str, Any]]) -> None:
        """Apply cached/manifest short_descriptions onto ``plugins`` (hot-path
        safe, zero LLM) and, for any plugin still missing one, schedule a
        fire-and-forget background task to generate it at plugin-load time.

        NEVER awaited on the analyze path: the current analyze safely falls back
        to the full description for plugins whose short_description hasn't been
        generated yet (see ``_stage1_llm_coarse_screen``). The generated value
        lands in ``_short_desc_cache`` for subsequent analyze runs.

        Deduped by plugin id via ``_short_desc_prewarm_inflight`` so the
        per-analyze ``force_refresh`` doesn't pile up duplicate generation tasks.
        """
        missing = self._apply_cached_short_descriptions(plugins)
        if not missing:
            return
        # Lazy-init for instances built via object.__new__ (test fixtures bypass __init__).
        inflight = getattr(self, "_short_desc_prewarm_inflight", None)
        if inflight is None:
            inflight = set()
            self._short_desc_prewarm_inflight = inflight
        if getattr(self, "_short_desc_prewarm_tasks", None) is None:
            self._short_desc_prewarm_tasks = set()
        pending = [
            p for p in missing
            if str(p.get("id", "")).strip() and str(p.get("id", "")) not in inflight
        ]
        if not pending:
            return
        pids = {str(p.get("id", "")) for p in pending}
        # Resolve the loop BEFORE constructing the coroutine: if there's no
        # running event loop (sync context), bail out without leaving an
        # un-awaited coroutine behind. analyze still falls back to full desc.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        inflight |= pids
        task = loop.create_task(self._prewarm_short_descriptions(pending, pids))
        self._short_desc_prewarm_tasks.add(task)
        task.add_done_callback(self._short_desc_prewarm_tasks.discard)

    async def _prewarm_short_descriptions(
        self, to_generate: List[Dict[str, Any]], pids: set[str],
    ) -> None:
        """Background LLM generation of short_description for plugins missing one
        (best-effort, cached). Runs OFF the analyze hot path — scheduled by
        ``_schedule_short_desc_prewarm`` at plugin-load time. Newly generated
        entries are persisted to disk so subsequent app restarts reuse them
        (keyed by description, so a manifest change still invalidates)."""
        generated: dict[str, tuple[str, str]] = {}
        try:
            logger.info("[Agent] Generating short_description for %d plugin(s)", len(to_generate))
            llm = self._get_llm(temperature=0, max_completion_tokens=AGENT_PLUGIN_SHORTDESC_MAX_TOKENS)
            for p in to_generate:
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
                        # Key off the FULL description (truncation is prompt-only),
                        # so apply-time lookup hits even for long-description plugins.
                        desc_key = self._desc_key(raw_desc)
                        self._short_desc_cache[pid] = (desc_key, text)
                        if isinstance(pid, str) and pid:
                            generated[pid] = (desc_key, text)
                        # LLM 生成原文不写 logger
                        logger.debug("[Agent] Generated short_description for %s (len=%d chars)", pid, len(text))
                        print(f"[Agent] short_description {pid}: {text[:80]}")
                except Exception as e:
                    # Don't cache failures — allow retry on next refresh
                    logger.debug("[Agent] Failed to generate short_description for %s: %s", pid, e)
        except Exception as e:
            logger.warning("[Agent] short_description generation batch failed: %s", e)
        finally:
            self._short_desc_prewarm_inflight -= pids
            # 把本批生成的（贵的）条目落盘，下次启动直接复用、不再现生成。
            self._persist_generated_short_descriptions(generated)

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
                    # Apply cached/manifest short_descriptions synchronously
                    # (zero LLM) and prewarm any missing ones in the background —
                    # never generate on the analyze hot path.
                    self._schedule_short_desc_prewarm(self.plugin_list)
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
                        # 同步应用缓存/manifest 的 short_description（零 LLM），
                        # 缺失的放后台预热，绝不在 analyze 热路径上现生成。
                        self._schedule_short_desc_prewarm(self.plugin_list)
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
        watch_key = (
            watch_config['api_key'],
            watch_config['base_url'],
            watch_config['model'],
            watch_config.get('provider_type'),
        )
        if self._cached_llm_config_key != watch_key:
            self._close_all_llms()
            self._cached_llm_config_key = watch_key

        instance_key = (
            tier, api_config['api_key'], api_config['base_url'], api_config['model'],
            api_config.get('provider_type'), temperature, max_completion_tokens,
        )
        if instance_key not in self._cached_llms:
            llm = create_chat_llm(
                model=api_config['model'],
                base_url=api_config['base_url'],
                api_key=api_config['api_key'],
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                max_retries=0,
                timeout=120.0,  # hang-guard for agent task LLM calls (large context + tool loops)
                provider_type=api_config.get('provider_type'),
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

    def _format_messages(self, messages: List[Dict[str, str]], *, proactive: bool = False) -> str:
        """Format conversation messages.

        ``proactive`` marks a self-initiated turn with no new user request: the
        ``LATEST_USER_REQUEST`` marker (which both the unified and plugin
        assessors key on) is taken from lanlan's own latest utterance instead of
        the stale prior user line, so the assessment is driven by the proactive
        intent rather than the old request.
        """
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
        if proactive:
            # Self-initiated turn → the actionable "request" is lanlan's own
            # latest utterance, not the (stale) latest user line.
            for m in reversed(messages[-AGENT_HISTORY_TURNS:]):
                if str(m.get('role') or '').lower() == 'assistant':
                    latest_user_text = _extract_text(m)
                    if latest_user_text:
                        break
        else:
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
        """Format the tool list for LLM reference"""
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
        # 走统一原子写（tmp + fsync + os.replace）：补齐此前缺的 fsync，断电不留 0
        # 字节文件；mkstemp 的 tmp 天生 0o600，不经过 umask 决定的可读窗口。
        atomic_write_json(path, data, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _get_short_desc_cache_path(self) -> Path:
        self._config_manager.ensure_config_directory()
        return Path(self._config_manager.config_dir) / self._short_desc_cache_filename

    def _load_short_desc_cache(self) -> dict[str, tuple[str, str]]:
        """Load the on-disk short_description cache (LLM-generated entries only).

        Returns ``{plugin_id: (description_key, short_description)}``.
        ``description_key`` is a hash of the full description (see ``_desc_key``):
        at apply time we re-generate when the plugin's current description no
        longer hashes to the stored key. Best-effort — a missing or corrupt file
        just yields an empty cache.
        """
        try:
            path = self._get_short_desc_cache_path()
        except Exception as exc:
            logger.debug("[Agent] short_desc cache path unavailable: %s", exc)
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("[Agent] Failed to load short_desc cache %s: %s", path, exc)
            return {}
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return {}
        cache: dict[str, tuple[str, str]] = {}
        for pid, item in entries.items():
            if not isinstance(pid, str) or not isinstance(item, dict):
                continue
            key = item.get("key")
            short = item.get("short")
            if isinstance(key, str) and isinstance(short, str) and short:
                cache[pid] = (key, short)
        return cache

    def _persist_generated_short_descriptions(self, generated: dict[str, tuple[str, str]]) -> None:
        """Merge newly LLM-generated entries into the on-disk cache (atomic write).

        Only generated entries are persisted; manifest-provided short_descriptions
        are re-derived for free on every load and would only bloat the file (a
        plugin's raw ``description`` is uncapped). Best-effort — a write failure
        just means the next session regenerates."""
        if not generated:
            return
        try:
            path = self._get_short_desc_cache_path()
        except Exception as exc:
            logger.debug("[Agent] short_desc cache path unavailable, skip persist: %s", exc)
            return
        # Re-read so concurrent prewarm batches don't clobber each other's entries.
        on_disk = self._load_short_desc_cache()
        on_disk.update(generated)
        payload = {
            "version": 1,
            "entries": {pid: {"key": k, "short": s} for pid, (k, s) in on_disk.items()},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
            # 统一原子写：tmp + fsync + os.replace，崩溃只丢 .tmp 不破坏原文件。
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            logger.warning("[Agent] Failed to persist short_desc cache %s: %s", path, exc)

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
        """Assess all non-plugin channels (qwenpaw / openfang / browser / computer) in a single LLM call.

        Assembles the prompt dynamically from the available flags and asks the LLM to pick
        the most suitable channel. If the LLM outputs multiple can_execute=true, the caller
        picks by priority.
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

            except openai_retry_error_types() as e:
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

    @staticmethod
    def _is_plugin_entry_agent_hidden(entry: Any) -> bool:
        """Return True when an entry should be hidden from automatic Agent routing."""
        if not isinstance(entry, dict):
            return False
        meta = entry.get("metadata")
        if not isinstance(meta, dict):
            return False
        for key in ("agent_auto", "agent_exposed", "llm_exposed"):
            if key in meta and meta.get(key) is False:
                return True
        if meta.get("agent_hidden") is True:
            return True
        return False

    def _agent_visible_plugin_entries(self, plugin: Any) -> list[dict]:
        """Return entries that are available to automatic Agent routing."""
        entries = plugin.get("entries") if isinstance(plugin, dict) else getattr(plugin, "entries", None)
        if entries is None:
            return [{"id": "run", "description": "Default plugin entry"}]
        if not isinstance(entries, list):
            return []
        return [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("id")
            and not self._is_plugin_entry_agent_hidden(entry)
        ]

    def _find_plugin_entry(self, plugins: Any, plugin_id: str, preferred_entry: str) -> tuple[Optional[dict], Optional[dict]]:
        """Find a plugin and a matching entry. Returns (plugin, None) if preferred_entry is not found among visible entries."""
        iterable = plugins.items() if isinstance(plugins, dict) else enumerate(plugins)
        for _, plugin in iterable:
            if not isinstance(plugin, dict) or plugin.get("id") != plugin_id:
                continue
            visible_entries = self._agent_visible_plugin_entries(plugin)
            for entry in visible_entries:
                if entry.get("id") == preferred_entry:
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
                if not pid:
                    continue
                visible_entries = self._agent_visible_plugin_entries(p)
                if not visible_entries:
                    continue
                entry_lines = []
                try:
                    for e in visible_entries:
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
                                            enum_hint = ""
                                            if isinstance(fdef, dict):
                                                enum_values = fdef.get("enum")
                                                if isinstance(enum_values, list) and enum_values:
                                                    shown = [str(v) for v in enum_values[:12]]
                                                    inner = "|".join(shown)
                                                    # 截断时把 "..." 放进 [] 内，并附上剩余数量。
                                                    # 让 LLM 明确知道 "还有 N 个未列出的合法值"，
                                                    # 而不是把可见 12 个误当成完整白名单。
                                                    if len(enum_values) > 12:
                                                        enum_hint = (
                                                            f" enum=[{inner}|... +{len(enum_values) - 12} more]"
                                                        )
                                                    else:
                                                        enum_hint = f" enum=[{inner}]"
                                            fields.append(f"{fname}:{ftype}{enum_hint}")
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
                if not entry_lines:
                    continue
                entry_desc = "; ".join(entry_lines)
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
        - Stage 2 only (plugins_desc <= AGENT_PLUGIN_DESC_BM25_THRESHOLD tokens):
          full LLM assessment with all active plugins
        - Stage 1 + 2 (plugins_desc > AGENT_PLUGIN_DESC_BM25_THRESHOLD tokens):
          BM25 + LLM coarse screen + keyword -> filtered candidates -> Stage 2
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

        # Pre-filter: only keep plugins that have at least one Agent-visible entry.
        # This prevents hidden-entry-only plugins from leaking into Stage 1 (BM25 / keyword / coarse-screen).
        # Keep the full list for valid_entries_map so error reasons can distinguish "no visible entries" from "not found".
        all_plugin_list = list(plugin_list)
        pre_filter_count = len(plugin_list)
        plugin_list = [p for p in plugin_list if self._agent_visible_plugin_entries(p)]
        if pre_filter_count != len(plugin_list):
            logger.debug("[UserPlugin] Pre-filtered %d plugin(s) with no visible entries", pre_filter_count - len(plugin_list))

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
                    logger.info("[UserPlugin] Stage 1: no plugins selected; stage 2 will receive no plugin candidates")
                    stage2_plugins = []
                    plugins_desc = "No plugins available."
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

                response = await llm.ainvoke(messages)
                raw_text = response.content
                # Telemetry: every LLM response that actually came back is one
                # Stage-2 assessment — including the empty / unparseable ones that
                # return early below. This is the *total* denominator, so the
                # documented retry / extra-round-trip rates aren't overstated by
                # silently dropping non-retry failures (a parse error never fires
                # a correction retry but still cost a Stage-2 round-trip).
                try:
                    from utils.instrument import counter as _instr_counter
                    _instr_counter("plugin_assess_stage2")
                except Exception:
                    # 埋点失败静默，绝不能影响插件评估主路径。
                    pass
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

                # Build the executable lookup from the plugins actually shown in this
                # Stage 2 prompt. In large plugin lists, Stage 1 may filter every
                # plugin out; a hallucinated id from the full registry must not pass
                # validation just because the plugin exists globally.
                valid_entries_map: Dict[str, List[str]] = {}
                all_entries_map: Dict[str, List[str]] = {}
                try:
                    for p in plugins:
                        pid = p.get("id") if isinstance(p, dict) else None
                        if not pid:
                            continue
                        eids = [str(e.get("id")) for e in self._agent_visible_plugin_entries(p) if e.get("id")]
                        valid_entries_map[pid] = eids
                except Exception:
                    valid_entries_map = {}
                try:
                    for p in all_plugin_list:
                        pid = p.get("id") if isinstance(p, dict) else None
                        if not pid:
                            continue
                        eids = [str(e.get("id")) for e in self._agent_visible_plugin_entries(p) if e.get("id")]
                        all_entries_map[pid] = eids
                except Exception:
                    all_entries_map = {}

                # Normalize numeric plugin_id (LLM may return int instead of str)
                if isinstance(d_pid, int):
                    d_pid = str(d_pid)
                    decision["plugin_id"] = d_pid

                if d_has and d_can:
                    # Telemetry: the subset of Stage-2 assessments where the LLM
                    # claimed an executable task — the only state a correction
                    # retry can fire from. retries / this counter = conditional
                    # correction rate; retries / plugin_assess_stage2 = the broader
                    # extra-round-trip cost rate across all Stage-2 calls.
                    try:
                        from utils.instrument import counter as _instr_counter
                        _instr_counter("plugin_assess_stage2_actionable")
                    except Exception:
                        # 埋点失败静默，绝不能影响插件评估主路径。
                        pass
                    correction_hint = None
                    visible_entries = valid_entries_map.get(d_pid)
                    if not d_pid:
                        correction_hint = f"plugin_id is required when has_task/can_execute are true. Available plugins: {list(valid_entries_map.keys())}"
                    elif d_pid not in valid_entries_map:
                        if all_entries_map.get(d_pid) == []:
                            correction_hint = f"plugin '{d_pid}' has no Agent-visible entries."
                        else:
                            correction_hint = f"plugin_id '{d_pid}' is not available in the current candidate set. Available plugins: {list(valid_entries_map.keys())}"
                    elif visible_entries == []:
                        correction_hint = f"plugin '{d_pid}' has no Agent-visible entries."
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
                    final_visible = valid_entries_map.get(final_pid)
                    if final_pid not in valid_entries_map:
                        logger.warning("[UserPlugin Assessment] Final check: plugin_id '%s' is not in current candidate set, forcing can_execute=false", final_pid)
                        final_can = False
                        decision["can_execute"] = False
                        if all_entries_map.get(final_pid) == []:
                            decision["reason"] = "no_agent_visible_entries"
                        else:
                            decision["reason"] = f"plugin_id '{final_pid}' not available in current candidates"
                    elif final_visible == []:
                        logger.warning(
                            "[UserPlugin Assessment] Final check: plugin_id '%s' has no Agent-visible entries, forcing can_execute=false",
                            final_pid,
                        )
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = "no_agent_visible_entries"
                    elif not final_eid and valid_entries_map.get(final_pid):
                        logger.warning(
                            "[UserPlugin Assessment] Final check: entry_id missing while has_task/can_execute=true (plugin_id=%s), forcing can_execute=false",
                            final_pid,
                        )
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = "entry_id missing"
                    elif not final_eid:
                        logger.warning(
                            "[UserPlugin Assessment] Final check: no Agent-visible entry for plugin_id=%s, forcing can_execute=false",
                            final_pid,
                        )
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = "no_agent_visible_entries"
                    elif valid_entries_map.get(final_pid) and final_eid not in valid_entries_map[final_pid]:
                        logger.warning("[UserPlugin Assessment] Final check: entry_id '%s' still invalid for plugin '%s', forcing can_execute=false", final_eid, final_pid)
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = f"entry_id '{final_eid}' not found in plugin '{final_pid}'"

                # Telemetry: if a correction retry fired, record whether the
                # re-asked decision ended up actionable. At this point
                # decision["can_execute"] reflects final validation (forced False
                # when plugin_id/entry_id is still invalid), so has_task &
                # can_execute both true means a valid plugin_id/entry_id survived.
                # Combined with the plugin_assess_stage2 denominator this gives the
                # retry trigger rate (sum of this counter / stage2) and the
                # post-retry success rate, without logging any LLM text.
                if up_retry_done:
                    try:
                        from utils.instrument import counter as _instr_counter
                        _retry_ok = bool(decision.get("has_task") and decision.get("can_execute"))
                        _instr_counter(
                            "plugin_assess_correction_retry",
                            result="success" if _retry_ok else "fail",
                        )
                    except Exception:
                        # 埋点失败静默，绝不能影响插件评估主路径。
                        pass

                return UserPluginDecision(
                    has_task=decision.get("has_task", False),
                    can_execute=decision.get("can_execute", False),
                    task_description=decision.get("task_description", ""),
                    plugin_id=decision.get("plugin_id"),
                    entry_id=final_eid,
                    plugin_args=decision.get("plugin_args"),
                    reason=decision.get("reason", "")
                )
                
            except openai_retry_error_types() as e:
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
        external_intent: Optional[float] = None,
        proactive: bool = False,
    ) -> Optional[TaskResult]:
        """
        Assess each channel's feasibility and return a Decision (no execution).
        Plugin is judged separately; qwenpaw/openfang/browser/computer are merged into one LLM call.
        Actual execution is dispatched uniformly by agent_server.

        ``proactive`` marks a self-initiated turn (lanlan spoke with no fresh user
        input). There is no new user request, so the actionable intent is taken
        from lanlan's own latest utterance instead of the (stale) latest user
        line.
        """
        # Bind active character for {MASTER_NAME}/{LANLAN_NAME} substitution
        # in any LLM call made under this analyze_and_execute (assess_user_plugin
        # / assess_unified_channels / classify_magic_intent / shortdesc gen,
        # all on the inherited async context). Without this the brain LLM
        # gets literal placeholders from plugin prompt fragments and the
        # leak check fires a WARNING.
        char_token = await self._set_character_context_token(lanlan_name)
        try:
            return await self._analyze_and_execute_inner(
                messages=messages,
                lanlan_name=lanlan_name,
                agent_flags=agent_flags,
                conversation_id=conversation_id,
                lang=lang,
                external_intent=external_intent,
                proactive=proactive,
            )
        finally:
            reset_active_character(char_token)

    def _deterministic_action_signal(
        self, user_text: str, *, openclaw_enabled: bool, user_plugin_enabled: bool,
    ) -> bool:
        """Zero-LLM action shortcuts the cheap pre-gate must NEVER skip.

        Returns True if either fires — meaning the gate must not brake even when
        the small model read the turn as chat:
        * OpenClaw magic word (pure rule match, no LLM).
        * Plugin keyword match over the ALREADY-CACHED plugin list only (no fetch,
          so the brake path never triggers short_description generation).

        Fails open (returns True) when user_plugin is on but the plugin list is
        not yet loaded: we cannot run the keyword shortcut, so we must not let the
        gate skip it. A brand-new plugin's keyword may be missed by the gate until
        the next non-braked turn refreshes ``self.plugin_list`` — acceptable since
        a session's plugin set rarely changes mid-conversation.
        """
        text = (user_text or "").strip()
        if not text:
            return False
        if openclaw_enabled:
            try:
                from brain.openclaw_adapter import OpenClawAdapter
                # Full ZERO-LLM rule classifier, not just exact magic words: it
                # also catches natural-language commands ("取消这个任务" → /stop,
                # "换个话题" → /new, …). These are no-LLM shortcuts the gate must
                # keep — only the LLM magic-intent path is fair to skip on chat.
                if OpenClawAdapter.rule_magic_command(text):
                    return True
            except Exception:
                return True  # can't run the shortcut → fail open
        if user_plugin_enabled:
            plugins = self.plugin_list
            if not plugins:
                return True  # not loaded → can't run keyword shortcut → fail open
            try:
                from brain.plugin_filter import _match_keywords
                for p in plugins:
                    # Skip passive plugins: they never participate in dispatch
                    # (mirrors _assess_user_plugin), so a passive plugin must not
                    # influence the gate at all.
                    if not isinstance(p, dict) or p.get("passive"):
                        continue
                    kws = p.get("keywords", [])
                    if isinstance(kws, list) and kws:
                        if _match_keywords(text, kws):
                            return True
                    elif self._agent_visible_plugin_entries(p):
                        # A dispatchable plugin with no usable keyword shortcut can
                        # only be selected by the LLM _assess_user_plugin (e.g.
                        # first-party plugins exposing agent entries but no
                        # top-level keywords). The keyword shortcut can't represent
                        # it, so the gate must fail open when one exists — never
                        # brake a turn such a plugin could have handled.
                        return True
            except Exception:
                return True  # on any error, fail open
        return False

    async def _analyze_and_execute_inner(
        self,
        messages: List[Dict[str, str]],
        lanlan_name: Optional[str] = None,
        agent_flags: Optional[Dict[str, bool]] = None,
        conversation_id: Optional[str] = None,
        lang: str = "en",
        external_intent: Optional[float] = None,
        proactive: bool = False,
    ) -> Optional[TaskResult]:
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

        # 格式化对话。主动搭话轮把 LATEST_USER_REQUEST 设为猫娘自己最近这句主动
        # 台词（而非上一轮旧用户请求），让 unified / plugin 两路评估都对着主动意图
        # 跑——两者都从 conversation 的 LATEST_USER_REQUEST 取意图。
        conversation = self._format_messages(messages, proactive=proactive)
        if not conversation.strip():
            return None
        latest_user_request = self._extract_latest_user_intent(conversation)
        recent_context = self._extract_recent_context(messages)
        normalized_intent = self._normalize_user_intent(latest_user_request, recent_context)

        # ── 廉价前置闸 ───────────────────────────────────────
        # external_intent 是 master-emotion 在 input-time 那次小模型调用产出的
        # 「外部能力相关度」信号（显式对外操作 + 需要外部/实时信息两类合一），已在
        # main 侧做过两件事：(1) 按本轮 user 文本做 freshness 匹配（陈旧/异轮读数 →
        # None，绝不用上一轮信号刹本轮）；(2) 折进 complexity 取 max，所以高
        # complexity 的硬推理轮（如 openfang 多步推理）即便 external 低也不会被刹。
        # 这里只要：自信地低 + 零 LLM 确定性 shortcut（magic word 规则 + 插件关键词）
        # 也全静默，就跳过下面 1~2 次大模型评估。
        # 闸非对称：None（无可用信号/陈旧）或任一确定性命中都不刹车 —— 最坏多花一次
        # 评估，绝不漏真任务。
        if external_intent is not None and AGENT_EXTERNAL_GATE_ENABLED:
            if external_intent < AGENT_EXTERNAL_GATE_THRESHOLD and not self._deterministic_action_signal(
                latest_user_request,
                openclaw_enabled=openclaw_enabled,
                user_plugin_enabled=user_plugin_enabled,
            ):
                logger.info(
                    "[AgentGate] skip assessment: external_intent=%.2f < %.2f, no deterministic signal",
                    external_intent, AGENT_EXTERNAL_GATE_THRESHOLD,
                )
                return None

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
        if proactive:
            # 主动搭话轮：意图是猫娘自己最近这句主动台词（已写进 latest_user_request /
            # conversation 的 LATEST_USER_REQUEST），无 user 附件。绝不拿窗口里那条陈旧
            # user 消息去 classify_magic_intent——否则旧 user 的魔法命令会被重放，或
            # OpenClaw 派单成旧 user 文本而非主动意图。
            user_intent, user_attachments = latest_user_request, []
        else:
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
                latest_user_request=latest_user_request,
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
        latest_user_request: str = "",
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

        raw_entries = plugin_meta.get("entries") if isinstance(plugin_meta, dict) else None
        entries_field_exists = isinstance(raw_entries, list)
        known_entries = [str(e.get("id")) for e in self._agent_visible_plugin_entries(plugin_meta) if e.get("id")]
        if entries_field_exists and not known_entries:
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method='user_plugin',
                success=False,
                error=f"Plugin {plugin_id} has no Agent-visible entries",
                tool_name=plugin_id,
                tool_args=plugin_args,
                entry_id=plugin_entry_id,
                reason=reason or "no_agent_visible_entries",
            )
        if entries_field_exists and not plugin_entry_id:
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method='user_plugin',
                success=False,
                error=f"entry_id is required for plugin '{plugin_id}'. Available: {known_entries}",
                tool_name=plugin_id,
                tool_args=plugin_args,
                entry_id=plugin_entry_id,
                reason=reason or "entry_id_missing",
            )

        # Normalize entry_id to string (LLM may return int)
        if isinstance(plugin_entry_id, int):
            plugin_entry_id = str(plugin_entry_id)

        # Strict entry_id validation: only allow case-insensitive exact match as minor tolerance.
        if plugin_entry_id and plugin_meta:
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
                # 用户最新原话：framework 在 dispatch 时已经提取过，通过 _ctx 暴露给
                # plugin。plugin 在内部 NL 决策时，可以拿原文兜底，避免 LLM 改写过的
                # plugin_args 里 string 字段丢失语气/连词等关键信号。是否使用由 plugin
                # 自己决定，setdefault 让 plugin 提前塞的同名值优先。
                if latest_user_request:
                    ctx_obj.setdefault("latest_user_request", latest_user_request)
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

            resolved_entry_id = plugin_entry_id or "run"
            run_body: Dict[str, Any] = {
                "task_id": task_id,
                "plugin_id": plugin_id,
                "entry_id": resolved_entry_id,
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
                        entry_id=resolved_entry_id,
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
                    entry_id=resolved_entry_id,
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
                "entry_id": resolved_entry_id,
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
                entry_id=resolved_entry_id,
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
                entry_id=plugin_entry_id or "run",
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
        latest_user_request: str = "",
        on_progress: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> TaskResult:
        """
        Directly execute a plugin entry by calling /runs with explicit plugin_id and optional entry_id.
        This is intended for agent_server to call when it wants to trigger a plugin_entry immediately.

        Same character-context binding as analyze_and_execute, since
        ``_execute_user_plugin`` may dispatch to a plugin entry whose
        callback chain ends with brain LLM calls (e.g. result digestion);
        without this, those calls would leak {MASTER_NAME} placeholders.
        """
        char_token = await self._set_character_context_token(lanlan_name)
        try:
            return await self._execute_user_plugin(
                task_id=task_id,
                plugin_id=plugin_id,
                plugin_args=plugin_args,
                entry_id=entry_id,
                task_description=f"Direct plugin call {plugin_id}",
                reason="direct_call",
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                latest_user_request=latest_user_request,
                on_progress=on_progress,
            )
        finally:
            reset_active_character(char_token)
    
    async def refresh_capabilities(self) -> Dict[str, Dict[str, Any]]:
        """Kept for interface compatibility; MCP has been removed, always returns empty."""
        return {}
