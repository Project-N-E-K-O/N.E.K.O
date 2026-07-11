# -*- coding: utf-8 -*-
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

"""MCP channel: announce MCP task results already executed inside DirectTaskExecutor."""

from utils.tokenize import truncate_to_tokens as _tt

from .._shared import logger
from ..results import _emit_task_result


async def dispatch(
    result,
    *,
    messages,
    lanlan_name,
    conversation_id,
    trigger_user_msg_sig,
) -> None:
    """Handle an analyzer decision routed to the MCP channel."""
    if result.success:
        # MCP 任务已成功执行，通知 main_server
        summary = f'你的任务"{result.task_description}"已完成'
        mcp_detail = ""
        if result.result:
            try:
                if isinstance(result.result, dict):
                    detail = result.result.get('content', [])
                    if detail and isinstance(detail, list):
                        text_parts = [item.get('text', '') for item in detail if isinstance(item, dict)]
                        mcp_detail = ' '.join(text_parts)
                        if mcp_detail:
                            summary = f'你的任务"{result.task_description}"已完成：{mcp_detail}'
                elif isinstance(result.result, str):
                    mcp_detail = result.result
                    summary = f'你的任务"{result.task_description}"已完成：{mcp_detail}'
            except Exception:
                pass

        try:
            await _emit_task_result(
                lanlan_name,
                channel="mcp",
                task_id=str(getattr(result, "task_id", "") or ""),
                success=True,
                summary=summary,
                detail=mcp_detail,
            )
            # task_description 是 LLM 生成的任务描述，不写 logger；
            # print 也只截到预览长度（与同文件其他调试 print 一致），
            # 避免长 description 把 stdout 刷爆。
            logger.info(f"[TaskExecutor] ✅ MCP task completed and notified (desc_len={len(result.task_description or '')})")
            print(f"[TaskExecutor] MCP task description (preview): {_tt(result.task_description or '', 120)}")
        except Exception as e:
            logger.warning(f"[TaskExecutor] Failed to notify main_server: {e}")
    else:
        logger.error(f"[TaskExecutor] ❌ MCP task failed: {result.error}")
