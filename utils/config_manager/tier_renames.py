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

"""Renamed model tiers: ``emotion`` -> ``recognition``, ``summary`` -> ``memory``.

The tiers kept their old code names long after their jobs changed: the
"emotion" tier is the small recognition model most light jobs use, and the
"summary" tier is the memory-upkeep model. Saved ``core_config.json`` files
still carry the old slot fields and follow values, so every reader of the raw
file runs :func:`migrate_renamed_tier_fields` first, and startup persists the
result once.

The old ``model_type`` strings stay accepted by ``get_model_api_config`` via
:data:`RENAMED_MODEL_TYPES`.
"""

from __future__ import annotations

# old model type -> new model type (also the slot field prefix)
RENAMED_MODEL_TYPES = {
    'emotion': 'recognition',
    'summary': 'memory',
}

# old follow value -> new follow value, in any ``*ModelProvider`` field
RENAMED_FOLLOW_VALUES = {
    'follow_emotion': 'follow_recognition',
    'follow_summary': 'follow_memory',
}

_SLOT_FIELD_SUFFIXES = ('ModelProvider', 'ModelUrl', 'ModelId', 'ModelApiKey')


def canonical_model_type(model_type: str) -> str:
    """Map an old tier name to its current one; anything else is returned as is."""
    return RENAMED_MODEL_TYPES.get(model_type, model_type)


def migrate_renamed_tier_fields(core_cfg: dict) -> bool:
    """Move old slot fields and follow values to the new names, in place.

    A field already saved under the new name wins; the old one is dropped
    either way, so the file never holds both. Returns True when anything
    changed.
    """
    if not isinstance(core_cfg, dict):
        return False
    changed = False
    for old, new in RENAMED_MODEL_TYPES.items():
        for suffix in _SLOT_FIELD_SUFFIXES:
            old_key, new_key = f'{old}{suffix}', f'{new}{suffix}'
            if old_key not in core_cfg:
                continue
            value = core_cfg.pop(old_key)
            if new_key not in core_cfg:
                core_cfg[new_key] = value
            changed = True
    for key, value in list(core_cfg.items()):
        if key.endswith('ModelProvider') and isinstance(value, str):
            renamed = RENAMED_FOLLOW_VALUES.get(value.strip())
            if renamed:
                core_cfg[key] = renamed
                changed = True
    return changed
