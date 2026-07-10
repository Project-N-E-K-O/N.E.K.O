# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List, Dict, Any, Tuple
import asyncio
from utils.llm_client import create_chat_llm
from openai import APIConnectionError, InternalServerError, RateLimitError
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from utils.file_utils import robust_json_loads
import json

logger = get_module_logger(__name__, "Agent")


class TaskDeduper:
    """
    LLM-based deduplication for task scheduling. Given a new task description and
    a list of existing task descriptions, decide if the new task is semantically
    duplicate (equivalent or strict subset) of an existing one.
    """

    def __init__(self):
        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config('summary')
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        self.llm = create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'], temperature=0, max_retries=0,
            timeout=30,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; tiny JSON normally, but a thinking model's reasoning is covered too
            provider_type=api_config.get('provider_type'),
        )

    def _build_prompt(self, new_task: str, candidates: List[Tuple[str, str]]) -> str:
        # Input budget: cap each component so the dedup prompt can't blow up on a
        # pathologically long task description. Use HEAD+TAIL truncation — users
        # often put context first and the concrete ask last, so a head-only cut
        # could drop the actual task and make a later identical request look
        # non-duplicate. Total stays within the same TASK_* token budget.
        from utils.tokenize import truncate_head_tail_tokens
        from config import (
            TASK_SUMMARY_MAX_TOKENS,
            TASK_DETAIL_MAX_TOKENS,
            AGENT_DEDUP_CANDIDATES_MAX,
        )
        _h_sum = TASK_SUMMARY_MAX_TOKENS // 2
        _h_det = TASK_DETAIL_MAX_TOKENS // 2
        lines = [
            "New task:",
            truncate_head_tail_tokens(new_task.strip(), _h_sum, _h_sum),
            "\nExisting tasks:",
        ]
        # Cap candidate count so a backlog/flood can't grow the prompt without
        # bound; with per-item head/tail truncation this gives a real total cap.
        # Keep the NEWEST candidates (task_registry appends new tasks at the end,
        # _collect_existing_task_descriptions preserves that order): a user
        # repeating a recently-queued task must have it included, or the judge
        # could return non-duplicate and schedule it twice.
        for tid, desc in candidates[-AGENT_DEDUP_CANDIDATES_MAX:]:
            lines.append(f"- id={tid}: {truncate_head_tail_tokens(desc, _h_det, _h_det)}")
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
                set_call_type("dedup")
                resp = await self.llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # each prompt component truncated to TASK_SUMMARY/DETAIL_MAX_TOKENS in _build_prompt (truncation lives in the builder, not here).
                    {"role": "system", "content": "You are a careful deduplication judge."},
                    {"role": "user", "content": prompt},
                ])
                text = (resp.content or "").strip()
                try:
                    if text.startswith("```"):
                        text = text.replace("```json", "").replace("```", "").strip()
                    data = robust_json_loads(text)
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

