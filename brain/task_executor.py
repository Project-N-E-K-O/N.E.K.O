# -*- coding: utf-8 -*-
"""
DirectTaskExecutor: 合并 Analyzer + Planner 的功能
并行评估 ComputerUse / BrowserUse / UserPlugin 可行性
"""
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass
from openai import AsyncOpenAI, APIConnectionError, InternalServerError, RateLimitError
import httpx
from config import get_extra_body, USER_PLUGIN_SERVER_PORT
from utils.config_manager import get_config_manager
from .computer_use import ComputerUseAdapter
from .browser_use_adapter import BrowserUseAdapter

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """任务执行结果"""
    task_id: str
    has_task: bool = False
    task_description: str = ""
    execution_method: str = "none"  # "computer_use" | "browser_use" | "user_plugin" | "none"
    success: bool = False
    result: Any = None
    error: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[Dict] = None
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
class DirectTaskExecutor:
    """
    直接任务执行器：并行评估 BrowserUse / ComputerUse / UserPlugin 可行性并执行
    """
    
    def __init__(self, computer_use: Optional[ComputerUseAdapter] = None, browser_use: Optional[BrowserUseAdapter] = None):
        self.computer_use = computer_use or ComputerUseAdapter()
        self.browser_use = browser_use or BrowserUseAdapter()
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
                async with httpx.AsyncClient(timeout=timeout) as _client:
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
        return (
            f"免费 Agent 模型今日试用次数已达上限 "
            f"({info.get('used', 0)}/{info.get('limit', 300)})，请明日再试。"
        )
    
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
                    "max_tokens": 500
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
                "max_tokens": 500,
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
                schema = p.get("input_schema", {}) if isinstance(p, dict) else getattr(p, "input_schema", {})
                entries = p.get("entries", []) if isinstance(p, dict) else getattr(p, "entries", []) or []
                # Only include well-formed plugin entries
                if not pid:
                    continue
                try:
                    schema_str = json.dumps(schema)
                except Exception:
                    schema_str = "{}"
                # Build entries description: show entry ids and short description to aid LLM in selecting entry_id
                entry_lines = []
                try:
                    for e in entries:
                        try:
                            eid = e.get("id") if isinstance(e, dict) else getattr(e, "id", None)
                            ename = e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")
                            edesc = e.get("description", "") if isinstance(e, dict) else getattr(e, "description", "")
                            if eid:
                                entry_lines.append(f"{eid} ({ename}): {edesc}")
                        except Exception:
                            continue
                except Exception:
                    entry_lines = []
                entry_desc = "; ".join(entry_lines) if entry_lines else "no entries"
                lines.append(f"- {pid}: {desc} | schema: {schema_str} | entries: {entry_desc}")
        except Exception:
            pass
        
        plugins_desc = "\n".join(lines) if lines else "No plugins available."
        # truncate to avoid overly large prompts
        if len(plugins_desc) > 2000:
            plugins_desc = plugins_desc[:2000] + "\n... (truncated)"
        logger.debug(f"[UserPlugin] passing plugin descriptions (truncated): {plugins_desc[:1000]}")
        
        # Strongly enforce JSON-only output to reduce parsing errors
        # NOTE: Require the model to return entry_id when has_task and can_execute are true.
        system_prompt = f"""You are a User Plugin selection agent. AVAILABLE PLUGINS:
{plugins_desc}

INSTRUCTIONS:
1. Analyze the conversation and determine if any available plugin can handle the user's request.
2. If yes, you MUST return the plugin id, the entry_id (the specific entry inside that plugin to invoke), and plugin_args matching the entry's schema.
3. If you cannot determine a specific plugin entry, return has_task=false or can_execute=false and explain why in the 'reason' field.
4. OUTPUT MUST BE ONLY a single JSON object and NOTHING ELSE. Do NOT include any explanatory text, markdown, or code fences.

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

VERY IMPORTANT: If has_task and can_execute are true, entry_id is REQUIRED. If entry_id is missing or null when has_task/can_execute are true, the response will be treated as non-executable.
Return only the JSON object, nothing else.
"""
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

        user_prompt = f"Conversation:\n{conversation}\n\nUser intent (one-line): {user_intent}"

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
                    "max_tokens": 500
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
                
                try:
                    decision = json.loads(text)
                except Exception as e:
                    logger.exception(f"[UserPlugin Assessment] JSON parse error: {e}; raw_text (truncated): {repr(raw_text)[:2000]}")
                    return UserPluginDecision(has_task=False, can_execute=False, task_description="", plugin_id=None, plugin_args=None, reason=f"JSON parse error: {e}")
                
                # return a simple object-like struct, include entry_id if provided by the LLM
                return UserPluginDecision(
                    has_task=decision.get("has_task", False),
                    can_execute=decision.get("can_execute", False),
                    task_description=decision.get("task_description", ""),
                    plugin_id=decision.get("plugin_id"),
                    entry_id=decision.get("entry_id") or decision.get("plugin_entry_id") or decision.get("event_id"),
                    plugin_args=decision.get("plugin_args"),
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
        agent_flags: Optional[Dict[str, bool]] = None
    ) -> Optional[TaskResult]:
        """
        并行评估 ComputerUse / BrowserUse / UserPlugin，然后执行任务
        """
        import uuid
        task_id = str(uuid.uuid4())
        
        if agent_flags is None:
            agent_flags = {"computer_use_enabled": False, "browser_use_enabled": False}
        
        computer_use_enabled = agent_flags.get("computer_use_enabled", False)
        browser_use_enabled = agent_flags.get("browser_use_enabled", False)
        user_plugin_enabled = agent_flags.get("user_plugin_enabled", False)
        
        # testUserPlugin: log entry with flags and short message summary for debugging
        try:
            msgs_summary = self._format_messages(messages)[:400].replace("\n", " ")
            print(f"testUserPlugin: analyze_and_execute called task_id={task_id}, lanlan={lanlan_name}, agent_flags={agent_flags}, messages_summary='{msgs_summary}'")
        except Exception:
            logger.info(f"testUserPlugin: analyze_and_execute called task_id={task_id}, lanlan={lanlan_name}, agent_flags={agent_flags}")
        
        if not computer_use_enabled and not browser_use_enabled and not user_plugin_enabled:
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
        
        # 并行执行评估（包含 user_plugin 分支）
        cu_decision = None
        bu_decision = None
        up_decision = None

        # user plugin 支路（由外部 provider 提供插件列表）
        plugins = []
        if user_plugin_enabled:
            await self.plugin_list_provider()
            plugins = self.plugin_list

        if user_plugin_enabled and plugins:
            assessment_tasks.append(('up', self._assess_user_plugin(conversation, plugins)))
        
        if browser_use_enabled and browser_available:
            assessment_tasks.append(('bu', self._assess_browser_use(conversation, browser_available)))
        
        if computer_use_enabled and cu_available:
            assessment_tasks.append(('cu', self._assess_computer_use(conversation, cu_available)))
        
        if not assessment_tasks:
            logger.debug("[TaskExecutor] No assessment tasks to run")
            return None
        
        # 并行执行所有评估
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
            elif task_type == 'cu':
                cu_decision = result
                logger.info(f"[ComputerUse] has_task={getattr(cu_decision,'has_task',None)}, can_execute={getattr(cu_decision,'can_execute',None)}, reason={getattr(cu_decision,'reason',None)}")
            elif task_type == 'bu':
                bu_decision = result
                logger.info(f"[BrowserUse] has_task={getattr(bu_decision,'has_task',None)}, can_execute={getattr(bu_decision,'can_execute',None)}, reason={getattr(bu_decision,'reason',None)}")
        
        # 决策逻辑
        # 1. BrowserUse
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

        # 2. ComputerUse
        if cu_decision and cu_decision.has_task and cu_decision.can_execute:
            logger.info(f"[TaskExecutor] ✅ Using ComputerUse: {cu_decision.task_description}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=cu_decision.task_description,
                execution_method='computer_use',
                success=False,  # 标记为待执行
                reason=cu_decision.reason
            )
        
        # 3. UserPlugin
        if up_decision and getattr(up_decision, "has_task", False) and getattr(up_decision, "can_execute", False):
            logger.info(f"[TaskExecutor] ✅ Using UserPlugin: {up_decision.task_description}, plugin_id={getattr(up_decision, 'plugin_id', None)}")
            try:
                return await self._execute_user_plugin(task_id=task_id, up_decision=up_decision)
            except Exception as e:
                logger.exception(f"[TaskExecutor] UserPlugin execution failed: {e}")
                return TaskResult(
                    task_id=task_id,
                    has_task=True,
                    task_description=getattr(up_decision, "task_description", ""),
                    execution_method='user_plugin',
                    success=False,
                    error=str(e),
                    reason=getattr(up_decision, "reason", "") or "UserPlugin execution error"
                )
                
        # 4. 没有可执行的分支，汇总原因
        reason_parts = []
        if cu_decision:
            reason_parts.append(f"ComputerUse: {cu_decision.reason}")
        if bu_decision:
            reason_parts.append(f"BrowserUse: {bu_decision.reason}")
        if up_decision:
            reason_parts.append(f"UserPlugin: {getattr(up_decision, 'reason', '')}")
        
        has_any_task = (
            (bu_decision and bu_decision.has_task)
            or (cu_decision and cu_decision.has_task)
            or (up_decision and getattr(up_decision, "has_task", False))
        )
        if has_any_task:
            if cu_decision and cu_decision.has_task:
                task_desc = cu_decision.task_description
            elif bu_decision and bu_decision.has_task:
                task_desc = bu_decision.task_description
            elif up_decision and getattr(up_decision, "has_task", False):
                task_desc = getattr(up_decision, "task_description", "")
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
    
    async def _execute_user_plugin(self, task_id: str, up_decision: Any) -> TaskResult:
        """
        Execute a user plugin via HTTP endpoint or specific plugin_entry.
        up_decision is expected to have attributes: plugin_id, plugin_args, task_description
        """
        plugin_id = getattr(up_decision, "plugin_id", None)
        plugin_args = getattr(up_decision, "plugin_args", {}) or {}
        task_description = getattr(up_decision, "task_description", "")
        # Optional: allow up_decision to specify a specific entry id
        # Prefer explicit 'entry_id' returned by the LLM (up_decision.entry_id), then fallback to older names
        plugin_entry_id = (
            getattr(up_decision, "entry_id", None)
            or getattr(up_decision, "plugin_entry_id", None)
            or (plugin_args.pop("_entry", None) if isinstance(plugin_args, dict) else None))
        
        if not plugin_id:
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method='user_plugin',
                success=False,
                error="No plugin_id provided",
                reason=getattr(up_decision, "reason", "")
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
                reason=getattr(up_decision, "reason", "") or "Plugin not found"
            )
        # Route via /plugin/trigger; use separate top-level entry_id when provided
        trigger_endpoint = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugin/trigger"
        trigger_body = {"task_id": task_id, "plugin_id": plugin_id, "args": plugin_args or {}}
        if plugin_entry_id:
            trigger_body["entry_id"] = plugin_entry_id
            logger.info("[TaskExecutor] Using explicit plugin_entry_id for trigger: %s", plugin_entry_id)
        # send trigger (avoid dumping full args at INFO)
        logger.info(
            "[TaskExecutor] POST to plugin trigger %s (plugin_id=%s, entry_id=%s, arg_keys=%s)",
            trigger_endpoint,
            plugin_id,
            plugin_entry_id,
            list(plugin_args.keys()) if isinstance(plugin_args, dict) else str(type(plugin_args)),
        )
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(trigger_endpoint, json=trigger_body)
                # Treat 2xx as accepted. plugin_server may synchronously execute and return executed_entry
                if 200 <= r.status_code < 300:
                    try:
                        data = r.json()
                    except Exception:
                        logger.debug("[TaskExecutor] Failed to parse trigger response as JSON, using text fallback", exc_info=True)
                        data = {"raw_text": r.text}
                    logger.info(
                        "[TaskExecutor] ✅ Trigger accepted for plugin %s (entry_id=%s)",
                        plugin_id,
                        plugin_entry_id or trigger_body.get("entry_id"),
                    )
                    logger.debug(
                        "[TaskExecutor] Trigger payload=%r, response=%r",
                        trigger_body,
                        data,
                    )
                    plugin_name = data.get("plugin_id") or plugin_id
                    # Determine executed entry id: prefer explicit returned executed_entry/entry_id, then trigger_body.entry_id
                    entry_id = None
                    if isinstance(data, dict):
                        entry_id = data.get("executed_entry") or data.get("entry_id") or trigger_body.get("entry_id")
                    # Log decision about entry_id for traceability
                    logger.debug(f"[TaskExecutor] Resolved entry_id for plugin {plugin_id}: {entry_id} (from response or trigger_body)")
                    # Return TaskResult with independent entry_id field in result
                    result_obj = {"accepted": True, "trigger_response": data, "entry_id": entry_id}
                    # success=True 表示“触发已被接受”，实际执行进度由 plugin_server 跟踪
                    return TaskResult(
                        task_id=task_id,
                        has_task=True,
                        task_description=task_description,
                        execution_method='user_plugin',
                        success=True,
                        result=result_obj,
                        tool_name=plugin_name,
                        tool_args=plugin_args,
                        reason=getattr(up_decision, "reason", "") or "trigger_accepted"
                    )
                else:
                    text = r.text
                    logger.error(f"[TaskExecutor] ❌ Trigger endpoint returned status {r.status_code}: {text}")
                    return TaskResult(
                        task_id=task_id,
                        has_task=True,
                        task_description=task_description,
                        execution_method='user_plugin',
                        success=False,
                        error=f"Trigger endpoint returned status {r.status_code}",
                        result={"status_code": r.status_code, "text": text},
                        tool_name=plugin_id,
                        tool_args=plugin_args,
                        reason=getattr(up_decision, "reason", "") or "trigger_failed"
                    )
        except Exception as e:
            logger.exception(f"[TaskExecutor] Trigger call error: {e}")
            return TaskResult(
                task_id=task_id,
                has_task=True,
                task_description=task_description,
                execution_method='user_plugin',
                success=False,
                error=str(e),
                tool_name=plugin_id,
                tool_args=plugin_args,
                reason=getattr(up_decision, "reason", "")
            )

    async def execute_user_plugin_direct(self, task_id: str, plugin_id: str, plugin_args: Dict[str, Any], entry_id: Optional[str] = None) -> TaskResult:
        """
        Directly execute a plugin entry by calling /plugin/trigger with explicit plugin_id and optional entry_id.
        This is intended for agent_server to call when it wants to trigger a plugin_entry immediately.
        """
        up_decision_stub = UserPluginDecision(
            has_task=True,
            can_execute=True,
            task_description=f"Direct plugin call {plugin_id}",
            plugin_id=plugin_id,
            entry_id=entry_id,
            plugin_args=plugin_args,
            reason="direct_call",
        )
        return await self._execute_user_plugin(task_id=task_id, up_decision=up_decision_stub)
    
    async def refresh_capabilities(self) -> Dict[str, Dict[str, Any]]:
        """保留接口兼容性，MCP 已移除，始终返回空。"""
        return {}
