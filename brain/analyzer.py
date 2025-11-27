from typing import Dict, Any, List
import logging
from openai import AsyncOpenAI
from config import MODELS_WITH_EXTRA_BODY
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class ConversationAnalyzer:
    """
    Analyzer module: analyze ongoing voice conversation turns to infer potential task intents.
    Input is textual transcript snippets from cross-server; output is zero or more normalized task queries.
    """
    def __init__(self):
        self._config_manager = get_config_manager()

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
        import json
        
        core_config = self._config_manager.get_core_config()
        model = core_config['SUMMARY_MODEL']
        api_key = core_config['OPENROUTER_API_KEY']
        base_url = core_config['OPENROUTER_URL']
        
        prompt = self._build_prompt(messages)
        
        try:
            # 使用与 emotion_analysis 相同的调用方式
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            
            request_params = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a precise task intent extractor."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 500
            }
            
            # 只有在需要时才添加 extra_body
            if model in MODELS_WITH_EXTRA_BODY:
                request_params["extra_body"] = {"enable_thinking": False}
            
            response = await client.chat.completions.create(**request_params)
            text = response.choices[0].message.content.strip()
            
            logger.debug(f"[Analyzer] Raw response: {text[:200]}...")
            
        except Exception as e:
            logger.error(f"[Analyzer] LLM调用失败: {e}")
            return {"tasks": [], "reason": f"LLM error: {e}"}
        
        try:
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            logger.info(f"[Analyzer] 分析结果: {len(data.get('tasks', []))} 个任务")
        except Exception as e:
            logger.warning(f"[Analyzer] JSON解析失败: {e}, raw: {text[:100]}")
            data = {"tasks": [], "reason": "parse error", "raw": text}
        
        return data



