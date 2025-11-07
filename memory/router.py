

"""
请注意，这是一个未完成的、未经测试的记忆模组的路由器。本文件的所有代码暂时都没有任何实际用途。为了减少项目依赖，暂时将langgraph组件移除。未来可能会也可能不会重新引入。
from langgraph.graph import StateGraph, END
"""

from typing import TypedDict, List, Dict, Any
from langchain_core.messages import BaseMessage
import json
from langchain_openai import ChatOpenAI
from config import ROUTER_MODEL
from utils.config_manager import get_config_manager

class RouterState(TypedDict):
    messages: List[BaseMessage]
    query_type: str
    results: Dict[str, Any]

class MemoryQueryRouter:
    def __init__(self, time_memory, semantic_memory, recent_history, settings_manager):
        self.time_memory = time_memory
        self.semantic_memory = semantic_memory
        self.recent_history = recent_history
        self.settings_manager = settings_manager
        self._config_manager = get_config_manager()
        self.graph = self._build_graph()
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        core_config = self._config_manager.get_core_config()
        return ChatOpenAI(model=ROUTER_MODEL, base_url=core_config['OPENROUTER_URL'], api_key=core_config['OPENROUTER_API_KEY'])

    def _build_graph(self):
        # 构建LangGraph流程图
        workflow = StateGraph(RouterState)

        # 添加节点
        workflow.add_node("route_query", self._route_query)
        workflow.add_node("time_query_agent", self._time_query_agent)
        workflow.add_node("semantic_query_agent", self._semantic_query_agent)
        workflow.add_node("semantic_query_with_time_agent", self._semantic_query_with_time_agent)

        # 定义边
        workflow.add_edge("route_query", "time_query_agent")
        workflow.add_edge("route_query", "semantic_query_agent")
        workflow.add_edge("route_query", "semantic_query_with_time_agent")

        workflow.add_edge("time_query_agent", END)
        workflow.add_edge("semantic_query_agent", END)
        workflow.add_edge("semantic_query_with_time_agent", END)

        # 设置入口点
        workflow.set_entry_point("route_query")

        return workflow.compile()

    def _route_query(self, state):
        # 分析请求类型并路由到相应的智能体
        query = state["messages"][-1].content

        # 使用LLM确定查询类型
        prompt = f"""
请分析以下查询，并确定它属于哪种类型:
1. time_query - 基于时间的查询（例如"上周我做了什么？"）
2. semantic_query - 基于语义的查询（例如"关于Python的讨论"）
3. semantic_query_with_time_constraint - 基于语义的查询（例如"昨天我们讨论玩什么"）

查询: {query}

只返回类型名称，不要有其他文本。"""

        llm = self._get_llm()
        response = llm.invoke(prompt)
        query_type = response.content.strip().lower()

        return {"query_type": query_type}

    def _time_query_agent(self, state):
        if state["query_type"] != "time_query":
            return state

        query = state["messages"][-1].content

        # 提取时间范围
        prompt = f"""
        从以下查询中提取时间范围:
        {query}

        以JSON格式返回，格式为:
        {{
            "start_time": "YYYY-MM-DD HH:MM:SS",
            "end_time": "YYYY-MM-DD HH:MM:SS"
        }}
        """

        llm = self._get_llm()
        response = llm.invoke(prompt)
        try:
            time_range = json.loads(response.content)
            results = self.time_memory.retrieve_by_timeframe(
                time_range["start_time"],
                time_range["end_time"]
            )
            return {"results": {"time_query_results": results}}
        except:
            return {"results": {"error": "无法解析时间范围"}}

    def _semantic_query_agent(self, state):
        if state["query_type"] != "semantic_query":
            return state

        query = state["messages"][-1].content
        results = self.semantic_memory.retrieve_by_query(query)

        return {"results": {"semantic_query_results": results}}

    def _semantic_query_with_time_agent(self, state):
        pass

    def process_request(self, messages, request_type=None):
        # 处理来自聊天机器人的请求
        initial_state = {
            "messages": messages,
            "query_type": request_type,
            "results": {}
        }

        result = self.graph.invoke(initial_state)
        return result["results"]
