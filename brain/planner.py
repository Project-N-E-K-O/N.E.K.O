import asyncio
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from langchain_openai import ChatOpenAI
from config import MODELS_WITH_EXTRA_BODY
from utils.config_manager import get_config_manager
from .mcp_client import McpRouterClient, McpToolCatalog
from .computer_use import ComputerUseAdapter

# Configure logging
logger = logging.getLogger(__name__)


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
        self.router = McpRouterClient()
        self.catalog = McpToolCatalog(self.router)
        self.task_pool: Dict[str, Task] = {}
        self.computer_use = computer_use or ComputerUseAdapter()
        self._config_manager = get_config_manager()
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        core_config = self._config_manager.get_core_config()
        return ChatOpenAI(model=core_config['SUMMARY_MODEL'], base_url=core_config['OPENROUTER_URL'], api_key=core_config['OPENROUTER_API_KEY'], temperature=0, extra_body={"enable_thinking": False} if core_config['SUMMARY_MODEL'] in MODELS_WITH_EXTRA_BODY else None)

    async def refresh_capabilities(self, force_refresh: bool = True) -> Dict[str, Dict[str, Any]]:
        """
        刷新MCP能力列表
        
        Args:
            force_refresh: 默认为True，强制刷新以获取最新的工具列表
        """
        try:
            return await self.catalog.get_capabilities(force_refresh=force_refresh)
        except Exception:
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
        llm = self._get_llm()
        resp1 = await llm.ainvoke([
            {"role": "system", "content": mcp_system},
            {"role": "user", "content": mcp_user},
        ])
        text1 = resp1.content.strip()
        import json, uuid
        try:
            if text1.startswith("```"):
                text1 = text1.replace("```json", "").replace("```", "").strip()
            mcp = json.loads(text1)
        except Exception:
            mcp = {"can_execute": False, "reason": "LLM parse error", "server_id": None, "steps": []}
        
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

                # Do not execute here to avoid blocking; scheduling is handled by server
                # if cu_decision.get('use_computer'): execution will be scheduled by the caller
            else:
                cu_decision = {"use_computer": False, "reason": "ComputerUse not ready"}

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


