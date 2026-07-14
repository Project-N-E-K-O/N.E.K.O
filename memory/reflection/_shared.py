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
"""Shared reflection constants and compatibility aliases."""

from memory._reflection.ontology import (
    ENTITY_KINDS as _ONTOLOGY_ENTITY_KINDS,
    KIND_RELATION_MAP as _ONTOLOGY_KIND_RELATION_MAP,
    MAX_REFLECTION_TEXT_TOKENS as _ONTOLOGY_MAX_REFLECTION_TEXT_TOKENS,
    RELATION_TYPES as _ONTOLOGY_RELATION_TYPES,
    TEMPORAL_SCOPES as _ONTOLOGY_TEMPORAL_SCOPES,
    allowed_relation_types as _ontology_allowed_relation_types,
    entity_kind as _ontology_entity_kind,
)
from memory._reflection.schema import REFLECTION_ARCHIVE_DAYS
from utils.logger_config import get_module_logger


RELATION_TYPES = _ONTOLOGY_RELATION_TYPES
ENTITY_KINDS = _ONTOLOGY_ENTITY_KINDS
KIND_RELATION_MAP = _ONTOLOGY_KIND_RELATION_MAP
TEMPORAL_SCOPES = _ONTOLOGY_TEMPORAL_SCOPES
MAX_REFLECTION_TEXT_TOKENS = _ONTOLOGY_MAX_REFLECTION_TEXT_TOKENS
_entity_kind = _ontology_entity_kind
_allowed_relation_types = _ontology_allowed_relation_types
_REFLECTION_ARCHIVE_DAYS = REFLECTION_ARCHIVE_DAYS

logger = get_module_logger("memory.reflection", "Memory")

MIN_FACTS_FOR_REFLECTION = 5
REFLECTION_TERMINAL_STATUSES = frozenset({
    'promoted', 'denied', 'archived', 'merged', 'promote_blocked',
})
REFLECTION_COOLDOWN_MINUTES = 30
