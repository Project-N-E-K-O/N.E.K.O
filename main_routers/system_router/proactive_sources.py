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

"""Compatibility facade for proactive source state and decisions."""

from main_logic.proactive_chat import state as _state
from main_logic.proactive_chat.decisions import (  # noqa: F401
    _SOURCE_WEIGHT_DECAY_LAMBDA,
    _SOURCE_WEIGHT_FLOOR,
    _SOURCE_WEIGHT_K,
    _SOURCE_WEIGHT_WINDOW,
    _compute_source_weights,
    _filter_sources_by_weight,
    _should_skip_source,
)
from main_logic.proactive_chat.state import (  # noqa: F401
    _SOURCE_HISTORY_FILENAME,
    _SOURCE_HISTORY_SCHEMA_VERSION,
    _half_life_for,
    _source_hash,
    _source_history,
    _source_history_loaded,
    _source_history_lock,
    _source_skip_probability,
)
from ..shared_state import get_config_manager as _get_legacy_config_manager


def _legacy_memory_dir():
    return _get_legacy_config_manager().memory_dir


def _source_history_path():
    return _state._source_history_path(memory_dir=_legacy_memory_dir())


async def _ensure_source_history_loaded() -> None:
    await _state._ensure_source_history_loaded(memory_dir=_legacy_memory_dir())


async def _record_source_used(
    *,
    url: str,
    kind: str,
    title: str = '',
) -> None:
    await _state._record_source_used(
        url=url,
        kind=kind,
        title=title,
        memory_dir=_legacy_memory_dir(),
    )
