# -*- coding: utf-8 -*-
"""
DirectTaskExecutor: 合并 Analyzer + Planner 的功能
并行评估 ComputerUse / BrowserUse / UserPlugin 可行性
"""
import json
import re
import asyncio
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass
from openai import AsyncOpenAI, APIConnectionError, InternalServerError, RateLimitError
import httpx
from config import get_extra_body, USER_PLUGIN_SERVER_PORT
from plugin.settings import PLUGIN_EXECUTION_TIMEOUT
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from .computer_use import ComputerUseAdapter
from .browser_use_adapter import BrowserUseAdapter
from .openclaw_adapter import OpenClawAdapter
from .openfang_adapter import OpenFangAdapter

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
    
    
    def set_plugin_list_provider(self, provider: Callable[[bool], Awaitable[List[Dict[str, Any]]]]):
        """Allow agent_server to inject a custom async provider for plugin discovery."""
        self._external_plugin_provider = provider

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
            except Exception as e:
                logger.warning(f"[Agent] plugin_list_provider http fetch failed: {e}")
        logger.info(f"[Agent] Loaded {len(self.plugin_list)} plugins: {[p.get('id', 'unknown') for p in self.plugin_list if isinstance(p, dict)]}")
        return self.plugin_list


    def _get_client(self):
        """动态获取 OpenAI 客户端"""
        set_call_type("agent")
        api_config = self._config_manager.get_model_api_config('summary')
        return AsyncOpenAI(
            api_key=api_config['api_key'],
            base_url=api_config['base_url'],
            max_retries=0
        )
    
    def _get_model(self):
        """获取模型名称"""
        api_config = self._config_manager.get_model_api_config('summary')
        return api_config['model']

    def _check_agent_quota(self, source: str) -> Optional[str]:
        """免费版 Agent 模型每日 300 次本地限流。"""
        ok, info = self._config_manager.consume_agent_daily_quota(source=source, units=1)
        if ok:
            return None
        return json.dumps({"code": "AGENT_QUOTA_EXCEEDED", "details": {"used": info.get('used', 0), "limit": info.get('limit', 300)}})
    
    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        """格式化对话消息"""
        def _extract_text(m: dict) -> str:
            return str(m.get('text') or m.get('content') or '').strip()

        latest_user_text = ""
        for m in reversed(messages[-10:]):
            if m.get('role') == 'user':
                latest_user_text = _extract_text(m)
                if latest_user_text:
                    break
        lines = []
        if latest_user_text:
            lines.append(f"LATEST_USER_REQUEST: {latest_user_text}")
        for m in messages[-10:]:
            role = m.get('role', 'user')
            text = _extract_text(m)
            if text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines)
    
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

    def _build_openclaw_instruction(self, user_intent: str) -> str:
        system_hint = (
            "如果任务涉及保存文件，除非用户明确指定路径，否则默认保存到桌面；"
            "如果需要打开浏览器处理任务，使用有头模式。"
        )
        return f"{system_hint}\n\n用户任务：{user_intent}"

    def _rule_assess_openclaw(self, conversation: str) -> Optional[OpenClawDecision]:
        """Hard-match obvious execution requests to OpenClaw before other assessments."""
        user_intent = self._extract_latest_user_intent(conversation)
        if not user_intent:
            return None

        text = user_intent.lower()
        web_action_markers = (
            "打开", "进入", "访问", "search", "搜索", "百度", "谷歌", "google",
            "浏览器", "网页", "网站", "screenshot", "截图", "截屏", "保存", "本地",
        )
        explicit_action_verbs = (
            "帮我", "请", "去", "执行", "操作", "打开", "搜索", "截图", "保存",
            "open ", "search ", "save ", "take a screenshot", "capture",
        )

        has_web_marker = any(marker in user_intent or marker in text for marker in web_action_markers)
        has_action_verb = any(marker in user_intent or marker in text for marker in explicit_action_verbs)
        if not (has_web_marker and has_action_verb):
            return None

        task_description = f"Use openclaw to execute the user's browser/screenshot task: {user_intent[:120]}"
        logger.info("[OpenClaw Rule] Matched hard-route for intent: %s", user_intent[:200])

        return OpenClawDecision(
            has_task=True,
            can_execute=True,
            task_description=task_description,
            instruction=self._build_openclaw_instruction(user_intent),
            reason="rule_matched_openclaw_web_action",
        )
    
    async def _assess_computer_use(
        self, 
        conversation: str,
        cu_available: bool
    ) -> ComputerUseDecision:
        """
        独立评估 ComputerUse 可行性（专注于 GUI 操作）
        """
        if not cu_available:
            return ComputerUseDecision(
                has_task=False, 
                can_execute=False, 
                reason="ComputerUse not available"
            )
        
        system_prompt = """You are a GUI automation assessment agent, your ONLY job is to determine if the user's request requires GUI/desktop automation.

GUI AUTOMATION CAPABILITIES:
- Control mouse (click, move, drag)
- Control keyboard (type, hotkeys)
- Open/close applications
- Browse the web
- Interact with Windows UI elements

INSTRUCTIONS:
1. Analyze if the conversation contains an actionable task request
2. Determine if the task REQUIRES GUI interaction (e.g., opening apps, clicking buttons, web browsing)
3. Tasks like "open Chrome", "click on X", "type something" require GUI
4. Tasks that can be done via API/tools (file operations, data queries) do NOT need GUI
5. If `LATEST_USER_REQUEST` exists, prioritize it over assistant claims like "already done".

OUTPUT FORMAT (strict JSON):
{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "brief description of the task",
    "reason": "why this decision"
}"""

        user_prompt = f"Conversation:\n{conversation}"
        
        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        
        for attempt in range(max_retries):
            try:
                client = self._get_client()
                model = self._get_model()
                
                request_params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_completion_tokens": 500
                }
                
                extra_body = get_extra_body(model)
                if extra_body:
                    request_params["extra_body"] = extra_body

                quota_error = self._check_agent_quota("task_executor.assess_computer_use")
                if quota_error:
                    return ComputerUseDecision(
                        has_task=False, can_execute=False, reason=quota_error
                    )
                response = await client.chat.completions.create(**request_params)
                text = response.choices[0].message.content.strip()
                
                logger.debug(f"[ComputerUse Assessment] Raw response: {text[:200]}...")
                
                # 解析 JSON
                if text.startswith("```"):
                    text = text.replace("```json", "").replace("```", "").strip()
                try:
                    decision = json.loads(text)
                except json.JSONDecodeError as e:
                    logger.error(
                        "[ComputerUse Assessment] Invalid JSON response: error=%s, raw=%r",
                        e,
                        text,
                    )
                    return ComputerUseDecision(
                        has_task=False,
                        can_execute=False,
                        reason=f"Assessment parse error: {e}",
                    )
                
                return ComputerUseDecision(
                    has_task=decision.get('has_task', False),
                    can_execute=decision.get('can_execute', False),
                    task_description=decision.get('task_description', ''), 
                    reason=decision.get('reason', '')
                )
                
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                if attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"[ComputerUse Assessment] 调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"[ComputerUse Assessment] Failed after {max_retries} attempts: {e}")
                    return ComputerUseDecision(has_task=False, can_execute=False, reason=f"Assessment error after {max_retries} attempts: {e}")
            except Exception as e:
                logger.error(f"[ComputerUse Assessment] Failed: {e}")
                return ComputerUseDecision(has_task=False, can_execute=False, reason=f"Assessment error: {e}")

    async def _assess_browser_use(self, conversation: str, browser_available: bool) -> BrowserUseDecision:
        if not browser_available:
            return BrowserUseDecision(has_task=False, can_execute=False, reason="BrowserUse not available")
        system_prompt = """You are a browser automation assessment agent, assess if the task should be handled by browser automation.
Return strict JSON:
{
  "has_task": boolean,
  "can_execute": boolean,
  "task_description": "brief description",
  "reason": "why"
}
Rules:
- ONLY choose browser automation for tasks that require interacting with websites, web pages, web forms, web search engines, or downloading from the internet.
- REJECT (has_task=false or can_execute=false) tasks that are purely local OS operations such as: opening local applications (calculator, file explorer, notepad, settings), managing files/folders, controlling system settings, or any task that does not need a web browser.
- If unsure whether a task needs a browser, default to REJECT."""
        user_prompt = f"Conversation:\n{conversation}"
        try:
            client = self._get_client()
            model = self._get_model()
            req = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "max_completion_tokens": 500,
            }
            extra_body = get_extra_body(model)
            if extra_body:
                req["extra_body"] = extra_body
            quota_error = self._check_agent_quota("task_executor.assess_browser_use")
            if quota_error:
                return BrowserUseDecision(has_task=False, can_execute=False, reason=quota_error)
            response = await client.chat.completions.create(**req)
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            try:
                decision = json.loads(text)
            except json.JSONDecodeError as e:
                raw_prefix = (text or "")[:10]
                logger.warning(
                    "[BrowserUse Assessment] Invalid JSON, prefix=%r, len=%d, error=%s",
                    raw_prefix,
                    len(text or ""),
                    e,
                )
                return BrowserUseDecision(
                    has_task=False,
                    can_execute=False,
                    reason=f"Assessment parse error: {e}; prefix={raw_prefix!r}",
                )
            return BrowserUseDecision(
                has_task=decision.get("has_task", False),
                can_execute=decision.get("can_execute", False),
                task_description=decision.get("task_description", ""),
                reason=decision.get("reason", ""),
            )
        except Exception as e:
            return BrowserUseDecision(has_task=False, can_execute=False, reason=f"Assessment error: {e}")
    
    async def _assess_openfang(self, conversation: str, available_tools: List[str]) -> OpenFangDecision:
        """
        评估是否应路由到 OpenFang 执行。

        OpenFang 擅长: 多步推理+工具调用的复合任务、数据处理、Web 搜索、代码执行、消息发送。
        不适合: 需要 GUI 交互 (截屏/点击)、纯对话/闲聊、需要可视化浏览器的任务。
        """
        if not self.openfang or not self.openfang.init_ok:
            return OpenFangDecision(has_task=False, can_execute=False, reason="OpenFang not available")

        tools_str = ", ".join(available_tools[:30]) if available_tools else "web_search, code_exec, file_ops, data_processing, messaging"
        system_prompt = f"""You are a automation assessment agent, assess if the user's latest request should be handled by OpenFang multi-agent autonomous system.
Return strict JSON:
{{
  "has_task": boolean,
  "can_execute": boolean,
  "task_description": "brief description",
  "suggested_tools": ["tool1", "tool2"],
  "reason": "why"
}}
OpenFang available tools: {tools_str}

Route TO OpenFang:
- Data processing, file operations, format conversion
- Web search, information gathering, research tasks
- Code generation and execution (sandboxed)
- Sending messages/emails via API
- Multi-step compound tasks requiring tool orchestration

Do NOT route to OpenFang:
- Tasks requiring screen interaction (screenshots, mouse clicks, GUI operations) → ComputerUse
- Pure conversation, emotional exchange, role-playing → main conversation engine
- Tasks requiring a visible browser with real-time visual feedback → BrowserUse
- Simple factual questions that don't need tools"""
        user_prompt = f"Conversation:\n{conversation}"
        try:
            client = self._get_client()
            model = self._get_model()
            req = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "max_completion_tokens": 500,
            }
            extra_body = get_extra_body(model)
            if extra_body:
                req["extra_body"] = extra_body
            quota_error = self._check_agent_quota("task_executor.assess_openfang")
            if quota_error:
                return OpenFangDecision(has_task=False, can_execute=False, reason=quota_error)
            response = await client.chat.completions.create(**req)
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            try:
                decision = json.loads(text)
            except json.JSONDecodeError as e:
                raw_prefix = (text or "")[:10]
                logger.warning(
                    "[OpenFang Assessment] Invalid JSON, prefix=%r, len=%d, error=%s",
                    raw_prefix, len(text or ""), e,
                )
                return OpenFangDecision(
                    has_task=False, can_execute=False,
                    reason=f"Assessment parse error: {e}; prefix={raw_prefix!r}",
                )
            return OpenFangDecision(
                has_task=decision.get("has_task", False),
                can_execute=decision.get("can_execute", False),
                task_description=decision.get("task_description", ""),
                suggested_tools=decision.get("suggested_tools", []),
                reason=decision.get("reason", ""),
            )
        except Exception as e:
            return OpenFangDecision(has_task=False, can_execute=False, reason=f"Assessment error: {e}")

    async def _assess_user_plugin(self, conversation: str, plugins: Any) -> UserPluginDecision:
        """
        评估本地用户插件可行性（plugins 为外部传入的插件列表）
        返回结构与 MCP 决策类似，但包含 plugin_id/plugin_args
        """
        # 如果没有插件，快速返回
        try:
            if not plugins:
                return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason="No plugins")
        except Exception:
            logger.debug("[UserPlugin] Failed to check plugins validity", exc_info=True)
            return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason="Invalid plugins")

        # 构建插件描述供 LLM 参考（包含 id, description, input_schema 以及 entries 列表）
        lines = []
        try:
            # plugins can be dict or list
            iterable = plugins.items() if isinstance(plugins, dict) else enumerate(plugins)
            for _, p in iterable:
                pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
                desc = p.get("description", "") if isinstance(p, dict) else getattr(p, "description", "")
                entries = p.get("entries", []) if isinstance(p, dict) else getattr(p, "entries", []) or []
                # Only include well-formed plugin entries
                if not pid:
                    continue
                # Build entries description: entry id + description + input_schema summary
                entry_lines = []
                try:
                    for e in entries:
                        try:
                            eid = e.get("id") if isinstance(e, dict) else getattr(e, "id", None)
                            edesc = e.get("description", "") if isinstance(e, dict) else getattr(e, "description", "")
                            if not eid:
                                continue
                            # Extract input_schema field names+types for LLM context
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
        
        plugins_desc = "\n".join(lines) if lines else "No plugins available."
        # truncate to avoid overly large prompts
        if len(plugins_desc) > 4000:
            plugins_desc = plugins_desc[:4000] + "\n... (truncated)"
        logger.debug(f"[UserPlugin] passing plugin descriptions (truncated): {plugins_desc[:1000]}")
        
        # Strongly enforce JSON-only output to reduce parsing errors
        # NOTE: Require the model to return entry_id when has_task and can_execute are true.
        system_prompt = f"""You are a User Plugin automation assessment agent, AVAILABLE PLUGINS:
{plugins_desc}

INSTRUCTIONS:
1. Analyze the conversation and determine if any available plugin should be invoked for the user's request.
2. Focus on the USER's latest message/intent — NOT on whether the AI has already replied. An AI reply in the conversation does NOT mean the plugin is unnecessary; assess whether the user's request can benefit from plugin execution.
3. If yes, you MUST return the plugin id, the entry_id (the specific entry inside that plugin to invoke), and plugin_args matching the entry's schema.
4. If you cannot determine a specific plugin entry, return has_task=false or can_execute=false and explain why in the 'reason' field.
5. OUTPUT MUST BE ONLY a single JSON object and NOTHING ELSE. Do NOT include any explanatory text, markdown, or code fences.

EXAMPLE (must follow this structure exactly):
{{
    "has_task": true,
    "can_execute": true,
    "task_description": "example: call testPlugin open entry",
    "plugin_id": "testPlugin",
    "entry_id": "open",
    "plugin_args": {{"message": "hello"}},
    "reason": ""
}}

OUTPUT FORMAT (strict JSON):
{{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "brief description",
    "plugin_id": "plugin id or null",
    "entry_id": "entry id inside the plugin or null",
    "plugin_args": {{...}} or null,
    "reason": "why"
}}

VERY IMPORTANT:
- If has_task and can_execute are true, entry_id is REQUIRED.
- If entry_id is missing or null when has_task/can_execute are true, the response will be treated as non-executable.
- STRICT MATCHING: plugin_id and entry_id are code identifiers. You MUST copy them EXACTLY (case-sensitive, character-for-character) from the AVAILABLE PLUGINS list above. Do NOT invent, abbreviate, or paraphrase them. If you cannot find an exact match, set can_execute=false.
- If an entry has args(...) info, use those field names in plugin_args. Only include fields listed in the schema.
- If the user's intent does not clearly match any plugin's described functionality, set has_task=false.
Return only the JSON object, nothing else.
"""
        user_intent = self._extract_latest_user_intent(conversation)

        user_prompt = f"Conversation:\n{conversation}\n\nUser intent (one-line): {user_intent}"

        max_retries = 3
        retry_delays = [1, 2]
        up_retry_done = False
        
        for attempt in range(max_retries):
            try:
                client = self._get_client()
                model = self._get_model()
                
                request_params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_completion_tokens": 500
                }
                
                extra_body = get_extra_body(model)
                if extra_body:
                    request_params["extra_body"] = extra_body

                quota_error = self._check_agent_quota("task_executor.assess_user_plugin")
                if quota_error:
                    return UserPluginDecision(
                        has_task=False,
                        can_execute=False,
                        task_description="",
                        plugin_id=None,
                        plugin_args=None,
                        reason=quota_error,
                    )
                response = await client.chat.completions.create(**request_params)
                # Capture raw response and log prompts/response at INFO so it's visible in runtime logs
                try:
                    raw_text = response.choices[0].message.content
                except Exception:
                    raw_text = None
                # Log the prompts we sent (truncated) and the raw response (truncated) at INFO level
                try:
                    prompt_dump = (system_prompt + "\n\n" + user_prompt)[:2000]
                except Exception:
                    prompt_dump = "(failed to build prompt dump)"
                logger.debug(f"[UserPlugin Assessment] prompt (truncated): {prompt_dump}")
                logger.debug(f"[UserPlugin Assessment] raw LLM response: {repr(raw_text)[:2000]}")
                
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
                    # 只在 DEBUG 级别记录 raw_text，避免隐私泄露和日志膨胀
                    logger.debug(
                        "[UserPlugin Assessment] JSON parse error; raw_text (truncated): %s",
                        (repr(raw_text)[:2000] if raw_text is not None else None),
                    )
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

                if d_has and d_can:
                    correction_hint = None
                    if not d_pid:
                        correction_hint = f"plugin_id is required when has_task/can_execute are true. Available plugins: {list(valid_entries_map.keys())}"
                    elif d_pid not in valid_entries_map:
                        correction_hint = f"plugin_id '{d_pid}' does not exist. Available plugins: {list(valid_entries_map.keys())}"
                    elif not d_eid:
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
                        request_params["messages"].append({"role": "assistant", "content": text})
                        request_params["messages"].append({"role": "user", "content": f"CORRECTION: {correction_hint}. Please fix your response and return a valid JSON."})
                        try:
                            response2 = await client.chat.completions.create(**request_params)
                            raw2 = response2.choices[0].message.content
                            t2 = raw2.strip() if isinstance(raw2, str) else ""
                            if t2.startswith("```"):
                                t2 = t2.replace("```json", "").replace("```", "").strip()
                            t2 = re.sub(r',(\s*[}\]])', r'\1', t2)
                            decision2 = json.loads(t2)
                            logger.info("[UserPlugin Assessment] Retry response parsed: %s", {k: decision2.get(k) for k in ("has_task", "can_execute", "plugin_id", "entry_id")})
                            decision = decision2
                            d_eid = decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id")
                        except Exception as e_retry:
                            logger.warning("[UserPlugin Assessment] Retry failed: %s", e_retry)

                # Final validation: reject if plugin_id/entry_id still invalid after retry
                final_pid = decision.get("plugin_id")
                final_eid = decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id")
                final_has = decision.get("has_task", False)
                final_can = decision.get("can_execute", False)
                if final_has and final_can:
                    if not final_eid:
                        logger.warning(
                            "[UserPlugin Assessment] Final check: entry_id missing while has_task/can_execute=true (plugin_id=%s), forcing can_execute=false",
                            final_pid,
                        )
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = "entry_id missing"
                    elif valid_entries_map and final_pid not in valid_entries_map:
                        logger.warning("[UserPlugin Assessment] Final check: plugin_id '%s' still invalid after retry, forcing can_execute=false", final_pid)
                        final_can = False
                        decision["can_execute"] = False
                        decision["reason"] = f"plugin_id '{final_pid}' not found"
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
    
    async def analyze_and_execute(
        self, 
        messages: List[Dict[str, str]], 
        lanlan_name: Optional[str] = None,
        agent_flags: Optional[Dict[str, bool]] = None,
        conversation_id: Optional[str] = None
    ) -> Optional[TaskResult]:
        """
        并行评估 ComputerUse / BrowserUse / UserPlugin 可行性，返回 Decision（不执行）。
        实际执行由 agent_server 统一 dispatch。
        """
        import uuid
        task_id = str(uuid.uuid4())
        
        # conversation_id 通过显式传参传递，避免并发时共享状态串用
        
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
        
        # 准备并行评估任务
        assessment_tasks = []
        
        # ComputerUse 可用性检查
        cu_available = False
        if computer_use_enabled:
            try:
                cu_status = self.computer_use.is_available()
                cu_available = cu_status.get('ready', False)
                logger.info(f"[TaskExecutor] ComputerUse available: {cu_available}")
            except Exception as e:
                logger.warning(f"[TaskExecutor] Failed to check ComputerUse: {e}")
        browser_available = False
        if browser_use_enabled:
            try:
                browser_available = self.browser_use.is_available().get("ready", False)
                logger.info(f"[TaskExecutor] BrowserUse available: {browser_available}")
            except Exception as e:
                logger.warning(f"[TaskExecutor] Failed to check BrowserUse: {e}")
        
        # OpenFang 可用性检查
        of_available = False
        of_tools: List[str] = []
        if openfang_enabled and self.openfang:
            try:
                of_available = self.openfang.init_ok
                of_tools = self.openfang.get_tools_list()
                logger.info("[TaskExecutor] OpenFang available: %s, tools: %d", of_available, len(of_tools))
            except Exception as e:
                logger.warning("[TaskExecutor] Failed to check OpenFang: %s", e)

        # 并行执行评估（包含 user_plugin / openfang 分支）
        cu_decision = None
        bu_decision = None
        up_decision = None
        nk_decision = None
        of_decision = None

        if openclaw_enabled:
            nk_decision = self._rule_assess_openclaw(conversation)

        # user plugin 支路（由外部 provider 提供插件列表）
        plugins = []
        if user_plugin_enabled:
            await self.plugin_list_provider()
            plugins = self.plugin_list

        if user_plugin_enabled and plugins:
            assessment_tasks.append(('up', self._assess_user_plugin(conversation, plugins)))

        if openfang_enabled and of_available:
            assessment_tasks.append(('of', self._assess_openfang(conversation, of_tools)))

        if browser_use_enabled and browser_available:
            assessment_tasks.append(('bu', self._assess_browser_use(conversation, browser_available)))

        if computer_use_enabled and cu_available:
            assessment_tasks.append(('cu', self._assess_computer_use(conversation, cu_available)))
        
        if not assessment_tasks and not (isinstance(nk_decision, OpenClawDecision) and nk_decision.has_task):
            logger.debug("[TaskExecutor] No assessment tasks to run")
            return None
        
        # 并行执行所有评估
        results = []
        if assessment_tasks:
            logger.info(f"[TaskExecutor] Running {len(assessment_tasks)} assessments in parallel...")
            results = await asyncio.gather(*[task[1] for task in assessment_tasks], return_exceptions=True)
        
        # 收集结果（安全访问，先过滤异常）
        for i, (task_type, _) in enumerate(assessment_tasks):
            result = results[i]
            if isinstance(result, Exception):
                logger.error(f"[TaskExecutor] {task_type} assessment failed: {result}")
                continue
            # safe attribute access via getattr to avoid type issues
            if task_type == 'up':
                up_decision = result
                logger.info(f"[UserPlugin] has_task={getattr(up_decision,'has_task',None)}, can_execute={getattr(up_decision,'can_execute',None)}, reason={getattr(up_decision,'reason',None)}")
            elif task_type == 'of':
                of_decision = result
                logger.info(f"[OpenFang] has_task={getattr(of_decision,'has_task',None)}, can_execute={getattr(of_decision,'can_execute',None)}, reason={getattr(of_decision,'reason',None)}")
            elif task_type == 'cu':
                cu_decision = result
                logger.info(f"[ComputerUse] has_task={getattr(cu_decision,'has_task',None)}, can_execute={getattr(cu_decision,'can_execute',None)}, reason={getattr(cu_decision,'reason',None)}")
            elif task_type == 'bu':
                bu_decision = result
                logger.info(f"[BrowserUse] has_task={getattr(bu_decision,'has_task',None)}, can_execute={getattr(bu_decision,'can_execute',None)}, reason={getattr(bu_decision,'reason',None)}")
        
        # 决策逻辑
        # 1. OpenClaw
        if isinstance(nk_decision, OpenClawDecision) and nk_decision.has_task:
            if not nk_decision.can_execute:
                return TaskResult(task_id=task_id, has_task=False, reason=nk_decision.reason)
            logger.info("[TaskExecutor] ✅ Using OpenClaw: %s", nk_decision.task_description)
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=nk_decision.task_description,
                execution_method='openclaw',
                success=False,
                tool_args={"instruction": nk_decision.instruction},
                reason=nk_decision.reason,
            )

        # 2. UserPlugin — 只返回 Decision，不执行（与 CU/BU 一致，由 agent_server dispatch）
        #    can_execute is a hard requirement; if false, refuse and return has_task=False.
        if isinstance(up_decision, UserPluginDecision) and up_decision.has_task and up_decision.plugin_id and up_decision.entry_id:
            if not up_decision.can_execute:
                logger.info(
                    "[TaskExecutor] ⛔ UserPlugin refused (can_execute=False): "
                    "plugin_id=%s, entry_id=%s, reason=%s",
                    up_decision.plugin_id, up_decision.entry_id, up_decision.reason,
                )
                return TaskResult(
                    task_id=task_id,
                    has_task=False,
                    reason=up_decision.reason
                )
            logger.info(f"[TaskExecutor] ✅ Using UserPlugin: {up_decision.task_description}, plugin_id={up_decision.plugin_id}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=up_decision.task_description,
                execution_method='user_plugin',
                success=False,
                tool_name=up_decision.plugin_id,
                tool_args=up_decision.plugin_args,
                entry_id=up_decision.entry_id,
                reason=up_decision.reason
            )

        # 3. OpenFang (沙箱保护 + 丰富工具集，优先于直接操作宿主的 BrowserUse/ComputerUse)
        if isinstance(of_decision, OpenFangDecision) and of_decision.has_task and of_decision.can_execute:
            logger.info("[TaskExecutor] Using OpenFang: %s", of_decision.task_description)
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=of_decision.task_description,
                execution_method='openfang',
                success=False,
                reason=of_decision.reason,
            )

        # 4. BrowserUse
        if bu_decision and bu_decision.has_task and bu_decision.can_execute:
            logger.info(f"[TaskExecutor] ✅ Using BrowserUse: {bu_decision.task_description}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=bu_decision.task_description,
                execution_method='browser_use',
                success=False,
                reason=bu_decision.reason
            )

        # 5. ComputerUse
        if cu_decision and cu_decision.has_task and cu_decision.can_execute:
            logger.info(f"[TaskExecutor] Using ComputerUse: {cu_decision.task_description}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=cu_decision.task_description,
                execution_method='computer_use',
                success=False,  # 标记为待执行
                reason=cu_decision.reason
            )

        # 5. 没有可执行的分支，汇总原因
        reason_parts = []
        if cu_decision:
            reason_parts.append(f"ComputerUse: {cu_decision.reason}")
        if bu_decision:
            reason_parts.append(f"BrowserUse: {bu_decision.reason}")
        if isinstance(up_decision, UserPluginDecision):
            reason_parts.append(f"UserPlugin: {up_decision.reason}")
        if isinstance(nk_decision, OpenClawDecision):
            reason_parts.append(f"OpenClaw: {nk_decision.reason}")
        if isinstance(of_decision, OpenFangDecision):
            reason_parts.append(f"OpenFang: {of_decision.reason}")

        has_any_task = (
            (bu_decision and bu_decision.has_task)
            or (cu_decision and cu_decision.has_task)
            or (isinstance(up_decision, UserPluginDecision) and up_decision.has_task)
            or (isinstance(nk_decision, OpenClawDecision) and nk_decision.has_task)
            or (isinstance(of_decision, OpenFangDecision) and of_decision.has_task)
        )
        if has_any_task:
            if cu_decision and cu_decision.has_task:
                task_desc = cu_decision.task_description
            elif bu_decision and bu_decision.has_task:
                task_desc = bu_decision.task_description
            elif isinstance(up_decision, UserPluginDecision) and up_decision.has_task:
                task_desc = up_decision.task_description
            elif isinstance(nk_decision, OpenClawDecision) and nk_decision.has_task:
                task_desc = nk_decision.task_description
            elif isinstance(of_decision, OpenFangDecision) and of_decision.has_task:
                task_desc = of_decision.task_description
            else:
                task_desc = ""
            logger.info(f"[TaskExecutor] Task detected but cannot execute: {task_desc}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_desc,
                execution_method='none',
                success=False,
                reason=" | ".join(reason_parts) if reason_parts else "No suitable method"
            )
        
        # 没有检测到任务
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
