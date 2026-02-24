from typing import List, Dict, Any, Optional, Tuple
import asyncio
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, InternalServerError, RateLimitError
from config import get_extra_body
from utils.config_manager import get_config_manager
import logging
import json

logger = logging.getLogger(__name__)


class TaskDeduper:
    """
    LLM-based deduplication for task scheduling. Given a new task description and
    a list of existing task descriptions, decide if the new task is semantically
    duplicate (equivalent or strict subset) of an existing one.
    """

    def __init__(self):
        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config('summary')
        self.llm = ChatOpenAI(
            model=api_config['model'],
            base_url=api_config['base_url'],
            api_key=api_config['api_key'],
            temperature=0,
            max_retries=0,
            extra_body=get_extra_body(api_config['model']) or None
        )

    def _build_prompt(self, new_task: str, candidates: List[Tuple[str, str]]) -> str:
        lines = ["New task:", new_task.strip(), "\nExisting tasks:"]
        for tid, desc in candidates:
            lines.append(f"- id={tid}: {desc}")
        lines.append(
            "\nTask: Decide whether the NEW task duplicates ANY existing task (same goal or a strict subset). "
            "Ignore superficial wording differences. Scan the existing tasks; "
            "if you find a duplicate, immediately return that task's id. If none are duplicate, use null. "
            "Output this strict JSON array (no prose): [matched_id_or_null, duplicate_boolean]."
        )
        return "\n".join(lines)

    async def judge(self, new_task: str, candidates: List[Tuple[str, str]]) -> Dict[str, Any]:
        if not new_task or not candidates:
            return {"duplicate": False, "matched_id": None}

        prompt = self._build_prompt(new_task, candidates)
        
        # Retry策略：重试2次，间隔1秒、2秒
        max_retries = 3
        retry_delays = [1, 2]
        
        for attempt in range(max_retries):
            try:
                ok, info = get_config_manager().consume_agent_daily_quota(
                    source="deduper.judge",
                    units=1,
                )
                if not ok:
                    logger.warning(
                        "[Deduper] Agent quota exceeded: used=%s, limit=%s",
                        info.get("used"),
                        info.get("limit"),
                    )
                    return {"duplicate": False, "matched_id": None}
                resp = await self.llm.ainvoke([
                    {"role": "system", "content": "You are a careful deduplication judge."},
                    {"role": "user", "content": prompt},
                ])
                text = (resp.content or "").strip()
                try:
                    if text.startswith("```"):
                        text = text.replace("```json", "").replace("```", "").strip()
                    data = json.loads(text)
                    # Preferred contract: JSON array [matched_id_or_null, duplicate_boolean]
                    if isinstance(data, list) and len(data) >= 2:
                        matched_id = data[0]
                        duplicate = bool(data[1])
                        return {"duplicate": duplicate, "matched_id": matched_id}
                    # Fallback: accept dict shape if model returns it
                    if isinstance(data, dict):
                        return {
                            "duplicate": bool(data.get("duplicate", False)),
                            "matched_id": data.get("matched_id")
                        }
                    # Unknown shape
                    return {"duplicate": False, "matched_id": None}
                except Exception:
                    return {"duplicate": False, "matched_id": None}
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                if attempt < max_retries - 1:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"[Deduper] LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"[Deduper] LLM调用失败，已达到最大重试次数: {e}")
                    return {"duplicate": False, "matched_id": None}
            except Exception as e:
                logger.error(f"[Deduper] LLM调用失败: {e}")
                return {"duplicate": False, "matched_id": None}


