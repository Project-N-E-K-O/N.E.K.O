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

"""Structured event emission toward main_server (task_result / generic events).

Split out of the former monolithic ``app/agent_server.py``. Everything here
funnels through ``Modules.agent_bridge`` (ZeroMQ) — there is deliberately no
HTTP fallback.
"""

from typing import Dict, Optional

from utils.tokenize import truncate_to_tokens as _tt

from . import _shared
from ._shared import logger
from .registry import _now_iso

async def _emit_task_result(
    lanlan_name: Optional[str],
    *,
    channel: str,
    task_id: str,
    success: bool,
    summary: str,
    detail: str = "",
    error_message: str = "",
    direct_reply: bool = False,
    status: Optional[str] = None,
    source_kind: Optional[str] = None,
    source_name: Optional[str] = None,
    delivery_mode: str = "proactive",
) -> None:
    """Emit a structured task_result event to main_server.

    Status, source_kind, source_name and delivery_mode propagate to the
    callback queue and drive the i18n outer-template rendering in
    main_logic. ``status`` defaults to ``completed`` / ``partial`` / ``failed``
    based on (success, detail) when not explicitly passed; pass ``"cancelled"``
    for user/system cancellation.
    """
    if status is None:
        if success:
            status = "completed"
        elif detail:
            status = "partial"
        else:
            status = "failed"
    # tiktoken token-based limits（同 main_logic 的语义分组）：
    # summary 是 LLM-facing 摘要（group B "longer reflective blurb"）
    # detail 是前端 HUD 展示用的较长版本（group G "large tool result"）
    # error_message 独立一档。
    from config import (
        TASK_SUMMARY_MAX_TOKENS as _SUMMARY_LIMIT,
        TASK_LARGE_DETAIL_MAX_TOKENS as _DETAIL_LIMIT,
        TASK_ERROR_MAX_TOKENS as _ERROR_LIMIT,
    )
    # 一次性 truncate 后复用——避免同 summary 在 text/summary 字段被
    # encode 两次，也让"最终 budget 由谁负责"的语义聚拢到这一处。
    _summary_t = _tt(summary, _SUMMARY_LIMIT)
    _detail_t = _tt(detail, _DETAIL_LIMIT) if detail else ""
    _error_t = _tt(error_message, _ERROR_LIMIT) if error_message else ""
    await _emit_main_event(
        "task_result",
        lanlan_name,
        text=_summary_t,
        task_id=task_id,
        channel=channel,
        status=status,
        success=success,
        summary=_summary_t,
        detail=_detail_t,
        error_message=_error_t,
        direct_reply=direct_reply,
        source_kind=source_kind or "",
        source_name=source_name or "",
        delivery_mode=delivery_mode,
        timestamp=_now_iso(),
    )


async def _emit_main_event(event_type: str, lanlan_name: Optional[str], **payload) -> None:
    event = {"event_type": event_type, "lanlan_name": lanlan_name, **payload}
    if _shared.Modules.agent_bridge:
        try:
            sent = await _shared.Modules.agent_bridge.emit_to_main(event)
            if sent:
                return
            logger.debug("[Agent] _emit_main_event not sent: type=%s lanlan=%s (bridge returned False)", event_type, lanlan_name)
        except Exception as e:
            logger.warning("[Agent] _emit_main_event failed: type=%s lanlan=%s error=%s", event_type, lanlan_name, e)
    else:
        logger.debug("[Agent] _emit_main_event skipped: no agent_bridge, type=%s", event_type)
