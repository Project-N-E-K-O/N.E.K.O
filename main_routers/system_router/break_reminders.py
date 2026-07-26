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

"""Compatibility exports for the proactive-chat break-reminder domain."""

from main_logic.proactive_chat.break_reminders import (
    _deliver_break_reminder_via_llm as _deliver_break_reminder_via_llm_domain,
)
from main_logic.proactive_chat.break_reminders import (
    _render_anti_slack_prompt,
    _render_work_break_game_invite_prompt,
    _render_work_break_prompt,
    _resolve_break_reminder_label,
)

from ..shared_state import get_config_manager


async def _deliver_break_reminder_via_llm(
    *,
    lanlan_name: str,
    mgr,
    system_prompt: str,
    channel: str,
    lang: str,
    timeout_seconds: float = 25.0,
) -> tuple[str | None, str | None]:
    """Preserve the former Router helper signature for external callers."""
    return await _deliver_break_reminder_via_llm_domain(
        lanlan_name=lanlan_name,
        mgr=mgr,
        config_manager=get_config_manager(),
        system_prompt=system_prompt,
        channel=channel,
        lang=lang,
        timeout_seconds=timeout_seconds,
    )


__all__ = (
    "_deliver_break_reminder_via_llm",
    "_render_anti_slack_prompt",
    "_render_work_break_game_invite_prompt",
    "_render_work_break_prompt",
    "_resolve_break_reminder_label",
)
