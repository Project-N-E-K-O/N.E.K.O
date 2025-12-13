from typing import Dict, Any, Optional
import asyncio
import logging
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, InternalServerError, RateLimitError
from config import get_extra_body
from utils.config_manager import get_config_manager
from .mcp_client import McpRouterClient, McpToolCatalog

# Configure logging
logger = logging.getLogger(__name__)


class Processor:
    """
    Processor module: accepts a natural language query and routes to appropriate MCP tools via LLM reasoning.
    Minimal implementation uses LLM to choose server capability and return a structured action plan.
    """
    def __init__(self):
        self.router = McpRouterClient()
        self.catalog = McpToolCatalog(self.router)
        self._config_manager = get_config_manager()
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('summary')
        return ChatOpenAI(model=api_config['model'], base_url=api_config['base_url'], api_key=api_config['api_key'], temperature=0, extra_body=get_extra_body(api_config['model']) or None)

    async def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        capabilities = await self.catalog.get_capabilities()
        
        # Log MCP capabilities
        logger.info(f"[MCP] Processing query: {query[:100]}...")
        logger.info(f"[MCP] Available capabilities: {len(capabilities)}")
        for cap_id, cap_info in capabilities.items():
            logger.info(f"[MCP]   - {cap_id}: {cap_info.get('title', 'No title')} (status: {cap_info.get('status', 'unknown')})")
        
        tools_brief = "\n".join([f"- {k}: {v['description']} (status={v['status']})" for k, v in capabilities.items()])
        system = (
            "You are a tool routing agent. Given a user task, select one MCP server capability by id and"
            " produce a concise JSON with fields: can_execute (boolean), reason, server_id, tool_calls (list of specific tool names that would be used)."
            " If a server can handle the task, set can_execute=true, provide server_id, and list the specific tools that would be called."
            " If no server fits or status is not online, set can_execute=false with reason."
            " For tool_calls, be specific about which tools from the server would be used (e.g., ['save_memory', 'retrieve_memory'])."
        )
        user = f"Capabilities:\n{tools_brief}\n\nTask: {query}"
        
        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        text = ""
        
        for attempt in range(max_retries):
            try:
                llm = self._get_llm()
                resp = await llm.ainvoke([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ])
                text = resp.content.strip()
                break  # 成功则退出重试循环
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                if attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"[MCP] LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"[MCP] LLM调用失败，已达到最大重试次数: {e}")
                    return {"can_execute": False, "reason": f"LLM error after {max_retries} attempts: {e}"}
            except Exception as e:
                logger.error(f"[MCP] LLM调用失败: {e}")
                return {"can_execute": False, "reason": f"LLM error: {e}"}
        
        # Log raw LLM response for debugging
        logger.info(f"[MCP] Raw LLM response: {text}")
        
        import json
        try:
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(text)
            
            # Ensure can_execute field exists and is boolean
            if 'can_execute' not in parsed:
                # If server_id is provided, assume it can execute
                parsed['can_execute'] = bool(parsed.get('server_id'))
                logger.info(f"[MCP] Missing can_execute field, inferred from server_id: {parsed['can_execute']}")
        except Exception as e:
            logger.error(f"[MCP] JSON parse error: {e}, raw text: {text}")
            parsed = {"can_execute": False, "reason": "LLM parse error", "raw": text}
        
        # Log MCP processing result and execute tools
        if parsed.get('can_execute'):
            server_id = parsed.get('server_id', 'unknown')
            reason = parsed.get('reason', 'no reason provided')
            tool_calls = parsed.get('tool_calls', [])
            
            if tool_calls:
                tools_info = ", ".join([f"'{tool}'" for tool in tool_calls])
                logger.info(f"[MCP] ✅ Query processed successfully using MCP server '{server_id}' with tools: {tools_info}")
                
                # Execute the tools and log results
                tool_results = []
                for tool_name in tool_calls:
                    logger.info(f"[MCP] 🔧 Executing tool: {server_id}.{tool_name}")
                    
                    # Prepare tool arguments based on the query
                    arguments = self._prepare_tool_arguments(tool_name, query)
                    
                    # Call the tool
                    result = await self.router.call_tool(server_id, tool_name, arguments)
                    
                    if result.get('success'):
                        logger.info(f"[MCP] ✅ Tool {tool_name} executed successfully: {result.get('result', 'No result')}")
                        tool_results.append({
                            'tool': tool_name,
                            'success': True,
                            'result': result.get('result')
                        })
                    else:
                        logger.error(f"[MCP] ❌ Tool {tool_name} failed: {result.get('error', 'Unknown error')}")
                        tool_results.append({
                            'tool': tool_name,
                            'success': False,
                            'error': result.get('error')
                        })
                
                # Add tool results to the response
                parsed['tool_results'] = tool_results
            else:
                logger.info(f"[MCP] ✅ Query processed successfully using MCP server '{server_id}' (no specific tools called)")
            
            logger.info(f"[MCP]   Reason: {reason}")
        else:
            reason = parsed.get('reason', 'no reason provided')
            logger.info(f"[MCP] ❌ Query cannot be processed by MCP: {reason}")
        
        return parsed

    def _prepare_tool_arguments(self, tool_name: str, query: str) -> Dict[str, Any]:
        """Prepare arguments for tool calls based on the tool name and query"""
        if tool_name == "save_memory":
            return {
                "content": query,
                "timestamp": "2025-09-24T07:30:00Z",
                "tags": ["user_query", "memory"]
            }
        elif tool_name == "retrieve_memory":
            return {
                "query": query,
                "limit": 10,
                "include_metadata": True
            }
        else:
            return {
                "input": query,
                "parameters": {}
            }


