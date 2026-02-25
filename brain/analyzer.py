from typing import Dict, List
import asyncio
from openai import AsyncOpenAI, APIConnectionError, InternalServerError, RateLimitError
from config import get_extra_body
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")


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
        
        api_config = self._config_manager.get_model_api_config('summary')
        model = api_config['model']
        api_key = api_config['api_key']
        base_url = api_config['base_url']
        
        prompt = self._build_prompt(messages)
        
        # Retry策略：重试2次，每次间隔1秒
        max_retries = 3
        retry_delays = [1, 1]  # 第1次重试等1秒，第2次重试等1秒
        
        for attempt in range(max_retries):
            try:
                # 使用与 emotion_analysis 相同的调用方式
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
                
                request_params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a precise task intent extractor."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 500
                }
                
                extra_body = get_extra_body(model)
                if extra_body:
                    request_params["extra_body"] = extra_body
                
                response = await client.chat.completions.create(**request_params)
                text = response.choices[0].message.content.strip()
                
                logger.debug(f"[Analyzer] Raw response: {text[:200]}...")
                break  # 成功则退出重试循环
                
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                if attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"[Analyzer] LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"[Analyzer] LLM调用失败，已达到最大重试次数: {e}")
                    return {"tasks": [], "reason": f"LLM error after {max_retries} attempts: {e}"}
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



