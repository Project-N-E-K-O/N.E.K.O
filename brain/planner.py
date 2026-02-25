import asyncio
import json
import uuid
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, InternalServerError, RateLimitError
from config import get_extra_body
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from .computer_use import ComputerUseAdapter

# Configure logging
logger = get_module_logger(__name__, "Agent")


@dataclass
class Task:
    id: str
    title: str
    original_query: str
    server_id: Optional[str] = None
    steps: List[str] = field(default_factory=list)
    status: str = "queued"  # queued | running | done | failed
    meta: Dict[str, Any] = field(default_factory=dict)


class TaskPlanner:
    """
    Planner module: preloads server capabilities, judges executability, decomposes task into executable queries.
    """
    def __init__(self, computer_use: Optional[ComputerUseAdapter] = None):
        self.task_pool: Dict[str, Task] = {}
        self.computer_use = computer_use or ComputerUseAdapter()
        self._config_manager = get_config_manager()
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('summary')
        return ChatOpenAI(model=api_config['model'], base_url=api_config['base_url'], api_key=api_config['api_key'], temperature=0, max_retries=0, extra_body=get_extra_body(api_config['model']) or None)

    async def refresh_capabilities(self, force_refresh: bool = True) -> Dict[str, Dict[str, Any]]:
        """MCP 已移除，始终返回空。"""
        return {}

    async def assess_and_plan(self, task_id: str, query: str, register: bool = True) -> Task:
        # Phase 1: MCP-only decision
        capabilities = await self.refresh_capabilities()
        tools_brief = "\n".join([f"- {k}: {v['description']}" for k, v in capabilities.items()])
        
        # Log MCP capabilities discovery
        logger.info(f"[MCP] Planning task {task_id} - Discovered {len(capabilities)} MCP capabilities")
        for cap_id, cap_info in capabilities.items():
            logger.info(f"[MCP]   - {cap_id}: {cap_info.get('title', 'No title')} (status: {cap_info.get('status', 'unknown')})")
        mcp_system = (
            "You are a planning agent. Decide ONLY based on MCP server capabilities whether the task is executable."
            " Do NOT consider GUI or computer-use in this step."
            " Output strict JSON: {can_execute: bool, reason: string, server_id: string|null, steps: string[]}"
            " steps should be granular tool queries for the MCP processor."
        )
        mcp_user = f"Capabilities:\n{tools_brief}\n\nTask: {query}"
        
        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        mcp = {"can_execute": False, "reason": "LLM call failed", "server_id": None, "steps": []}
        
        for attempt in range(max_retries):
            try:
                llm = self._get_llm()
                resp1 = await llm.ainvoke([
                    {"role": "system", "content": mcp_system},
                    {"role": "user", "content": mcp_user},
                ])
                text1 = resp1.content.strip()
                try:
                    if text1.startswith("```"):
                        text1 = text1.replace("```json", "").replace("```", "").strip()
                    mcp = json.loads(text1)
                except Exception:
                    mcp = {"can_execute": False, "reason": "LLM parse error", "server_id": None, "steps": []}
                break  # 成功则退出重试循环
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                if attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"[Planner MCP] LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"[Planner MCP] LLM调用失败，已达到最大重试次数: {e}")
                    mcp = {"can_execute": False, "reason": f"LLM error after {max_retries} attempts: {e}", "server_id": None, "steps": []}
            except Exception as e:
                logger.error(f"[Planner MCP] LLM调用失败: {e}")
                mcp = {"can_execute": False, "reason": f"LLM error: {e}", "server_id": None, "steps": []}
                break
        
        # Log MCP decision
        if mcp.get('can_execute'):
            server_id = mcp.get('server_id', 'unknown')
            steps_count = len(mcp.get('steps', []))
            logger.info(f"[MCP] ✅ Task {task_id} can be executed by MCP server '{server_id}' with {steps_count} steps")
            for i, step in enumerate(mcp.get('steps', []), 1):
                logger.info(f"[MCP]   Step {i}: {step}")
        else:
            reason = mcp.get('reason', 'no reason provided')
            logger.info(f"[MCP] ❌ Task {task_id} cannot be executed by MCP: {reason}")

        cu_decision = None
        cu = self.computer_use.is_available()
        logger.info(f"[ComputerUse] Availability check: {cu}")

        # Phase 2: Only if MCP cannot execute, evaluate ComputerUse
        if not mcp.get('can_execute'):
            if cu.get('ready'):
                cu_system = (
                    "You are deciding whether a GUI computer-use agent that can control mouse/keyboard, open/close"
                    " apps, browse the web, and interact with typical Windows UI can accomplish the task."
                    " Ignore any MCP tools; ONLY decide feasibility of GUI agent. Output strict JSON:"
                    " {use_computer: bool, reason: string}"
                )
                cu_user = f"Task: {query}"
                
                # Retry策略：重试2次，间隔1秒、2秒
                cu_decision = {"use_computer": False, "reason": "LLM call failed"}
                for attempt in range(max_retries):
                    try:
                        llm = self._get_llm()
                        resp2 = await llm.ainvoke([
                            {"role": "system", "content": cu_system},
                            {"role": "user", "content": cu_user},
                        ])
                        text2 = resp2.content.strip()
                        try:
                            if text2.startswith("```"):
                                text2 = text2.replace("```json", "").replace("```", "").strip()
                            cu_decision = json.loads(text2)
                        except Exception:
                            cu_decision = {"use_computer": False, "reason": "LLM parse error"}
                        break  # 成功则退出重试循环
                    except (APIConnectionError, InternalServerError, RateLimitError) as e:
                        logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                        if attempt < max_retries - 1:
                            wait_time = retry_delays[attempt]
                            logger.warning(f"[Planner ComputerUse] LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"[Planner ComputerUse] LLM调用失败，已达到最大重试次数: {e}")
                            cu_decision = {"use_computer": False, "reason": f"LLM error after {max_retries} attempts: {e}"}
                    except Exception as e:
                        logger.error(f"[Planner ComputerUse] LLM调用失败: {e}")
                        cu_decision = {"use_computer": False, "reason": f"LLM error: {e}"}
                        break
                
                # Log Computer Use decision
                if cu_decision.get('use_computer'):
                    logger.info(f"[ComputerUse] ✅ Task {task_id} can be executed by ComputerUse: {cu_decision.get('reason', '')}")
                else:
                    logger.info(f"[ComputerUse] ❌ Task {task_id} rejected by ComputerUse: {cu_decision.get('reason', '')}")
            else:
                cu_decision = {"use_computer": False, "reason": f"ComputerUse not ready: {cu.get('reasons', [])}"}
                logger.warning(f"[ComputerUse] ⚠️ ComputerUse not available: {cu.get('reasons', [])}")

        # Determine status without executing blocking GUI operations here
        status = "queued"
        if mcp.get('can_execute'):
            status = "queued"
        else:
            if cu_decision and cu_decision.get('use_computer'):
                status = "queued"
            else:
                status = "failed"

        t = Task(
            id=task_id or str(uuid.uuid4()),
            title=query[:50],
            original_query=query,
            server_id=mcp.get('server_id'),
            steps=mcp.get('steps', []),
            status=status,
            meta={
                "mcp": mcp,
                "computer_use_decision": cu_decision
            },
        )
        if register:
            self.task_pool[t.id] = t
        return t


