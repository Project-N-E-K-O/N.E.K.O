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

"""Compatibility facade for proactive-chat state.

The canonical implementation lives in :mod:`main_logic.proactive_chat.state`.
New code must import that module directly.
"""

from main_logic.proactive_chat import state as _state
from main_logic.proactive_chat.state import (  # noqa: F401
    _proactive_chat_history,
    _proactive_material_history,
    _PROACTIVE_MATERIAL_HISTORY_MAX,
    _PROACTIVE_CHAT_TOTALS_FILENAME,
    _PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION,
    _proactive_chat_totals,
    _invite_ever_delivered,
    _proactive_chat_totals_lock,
    _proactive_chat_totals_loaded,
    _RECENT_CHAT_MAX_AGE_SECONDS,
    _PROACTIVE_SIMILARITY_THRESHOLD,
    _format_recent_proactive_chats,
    _REMINISCENCE_USAGE_MAX,
    _reminiscence_usage_history,
    _record_reminiscence_usage,
    _record_proactive_chat,
    _normalize_material_key,
    _proactive_material_key,
    _is_recent_proactive_material,
    _record_proactive_material,
    _get_proactive_chat_total,
    _was_invite_ever_delivered,
    _clear_channel_from_proactive_history,
    _normalize_text_for_similarity,
    _is_similar_to_recent_proactive_chat,
)
from ..shared_state import get_config_manager as _get_legacy_config_manager


def _legacy_memory_dir():
    return _get_legacy_config_manager().memory_dir


def _proactive_chat_totals_path():
    return _state._proactive_chat_totals_path(memory_dir=_legacy_memory_dir())


async def _ensure_proactive_chat_totals_loaded() -> None:
    await _state._ensure_proactive_chat_totals_loaded(memory_dir=_legacy_memory_dir())


async def _persist_totals_unlocked() -> None:
    await _state._persist_totals_unlocked(memory_dir=_legacy_memory_dir())


async def _increment_proactive_chat_total(lanlan_name: str) -> int:
    return await _state._increment_proactive_chat_total(
        lanlan_name,
        memory_dir=_legacy_memory_dir(),
    )


async def _mark_invite_ever_delivered(lanlan_name: str) -> None:
    await _state._mark_invite_ever_delivered(
        lanlan_name,
        memory_dir=_legacy_memory_dir(),
    )


async def _record_invite_delivery_persistent(lanlan_name: str) -> int:
    return await _state._record_invite_delivery_persistent(
        lanlan_name,
        memory_dir=_legacy_memory_dir(),
    )
