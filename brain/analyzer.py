from typing import Dict, Any, List
from langchain_openai import ChatOpenAI
from config import MODELS_WITH_EXTRA_BODY
from utils.config_manager import get_config_manager


class ConversationAnalyzer:
    """
    Analyzer module: analyze ongoing voice conversation turns to infer potential task intents.
    Input is textual transcript snippets from cross-server; output is zero or more normalized task queries.
    """
    def __init__(self):
        self._config_manager = get_config_manager()
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        core_config = self._config_manager.get_core_config()
        return ChatOpenAI(model=core_config['SUMMARY_MODEL'], base_url=core_config['OPENROUTER_URL'], api_key=core_config['OPENROUTER_API_KEY'], temperature=0, extra_body={"enable_thinking": False} if core_config['SUMMARY_MODEL'] in MODELS_WITH_EXTRA_BODY else None)

    def _build_prompt(self, messages: List[Dict[str, str]]) -> str:
        lines = []
        for m in messages[-20:]:
            role = m.get('role', 'user')
            text = m.get('text', '')
            lines.append(f"{role}: {text}")
        conversation = "\n".join(lines)
        return (
            "You analyze conversation snippets and extract potential actionable task queries from the user."
            " Return JSON: {reason: string, tasks: string[]}."
            " Only include tasks that can be delegated to tools; avoid chit-chat."
            f"\nConversation:\n{conversation}"
        )

    async def analyze(self, messages: List[Dict[str, str]]):
        prompt = self._build_prompt(messages)
        llm = self._get_llm()
        resp = await llm.ainvoke([
            {"role": "system", "content": "You are a precise task intent extractor."},
            {"role": "user", "content": prompt},
        ])
        text = resp.content.strip()
        import json
        try:
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
        except Exception as e:
            print(f"Analyzer parse error: {e}")
            data = {"tasks": [], "reason": "parse error", "raw": text}
        return data



