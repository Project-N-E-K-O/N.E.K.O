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
from utils.llm_client import create_chat_llm, openai_retry_error_types
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
        # Publish (route, client) atomically as **one** tuple, not two attributes. Separate
        # assignments can interleave during concurrent refreshes, pairing an old client with a new route fingerprint; later
        # calls then mistake that old client for the latest and stay pinned to the wrong endpoint until routing changes again.
        self._llm_cache = None
        # Construct once initially to preserve startup readiness; _get_llm makes the authoritative decision.
        self._get_llm()

    def _get_llm(self):
        """Return the summary LLM, rebuilding it if its route changed.

        ``summary`` is a region-dependent route, and the region verdict is not
        final at construction time: a Steam answer is deliberately treated as a
        usable-but-not-latched vote, so the authoritative IP probe can still
        overturn it seconds later. Freezing the client in ``__init__`` would pin
        every later dedup call to whichever endpoint happened to be selected in
        that instant, for the lifetime of the process. Comparing the resolved
        route on each use costs a string compare and also makes an ordinary
        config change take effect without a restart.

        The (route, client) pair is published as ONE tuple assignment on
        purpose: two judges racing a route change with two separate attribute
        writes can interleave into "old client tagged with the new route",
        which every later call would trust as current — pinned to the wrong
        regional endpoint until the route changes again. With the tuple swap a
        concurrent rebuild merely wastes one client object (last writer wins);
        the pair can never disagree.
        """
        api_config = get_config_manager().get_model_api_config('summary')
        route = (api_config.get('base_url'), api_config.get('model'),
                 api_config.get('api_key'), api_config.get('provider_type'))
        cached = self._llm_cache
        if cached is not None and cached[0] == route:
            return cached[1]
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'], temperature=0, max_retries=0,
            timeout=30,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; tiny JSON normally, but a thinking model's reasoning is covered too
            provider_type=api_config.get('provider_type'),
        )
        self._llm_cache = (route, llm)
        return llm

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

        # Offload route rechecks from the event loop: _get_llm calls get_model_api_config →
        # get_core_config, performing synchronous open()+json.load() disk reads. agent_server's
        # event loop is shared by three subsystems, so slow disks/antivirus would block all concurrent requests
        # (the same reason aget_core_config must offload). One recheck per judge is enough:
        # retries within a single judge need not reread routing independently.
        llm = await asyncio.to_thread(self._get_llm)

        # Retry policy: two retries, after 1 second and 2 seconds
        max_retries = 3
        retry_delays = [1, 2]

        for attempt in range(max_retries):
            try:
                set_call_type("dedup")
                resp = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # each prompt component truncated to TASK_SUMMARY/DETAIL_MAX_TOKENS in _build_prompt (truncation lives in the builder, not here).
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
            except openai_retry_error_types() as e:
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

