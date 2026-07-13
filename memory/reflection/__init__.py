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

"""
ReflectionEngine — Tier 2 of the three-tier memory hierarchy.

Synthesizes multiple Tier-1 facts into higher-level reflections (insights).
Reflections start as "pending" and require feedback confirmation before
being promoted to persona (Tier 3).

Cognitive flow:
  Facts(passive) → Reflection(active thinking) → Persona(confirmed & solidified)

Trigger: called during proactive chat, NOT during every conversation.
This allows reflection to double as a "callback" mechanism where the AI naturally
mentions its observations and gauges the user's response.

Auto-promotion: pending reflections that remain 3 days without denial are
automatically promoted to persona.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from config import (
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_PROMOTE_MAX_RETRIES,
    EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES,
    EVIDENCE_PROMOTED_THRESHOLD,
    EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
)
from memory.evidence import evidence_score, initial_reinforcement_from_importance
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from memory.persona import (
    PersonaManager,
    SUPPRESS_COOLDOWN_HOURS,
    SUPPRESS_MENTION_LIMIT,
    SUPPRESS_WINDOW_HOURS,
    _is_mentioned,
)
from memory.stop_names import acollect_stop_names
from memory._reflection.ontology import (
    ENTITY_KINDS as _ONTOLOGY_ENTITY_KINDS,
    KIND_RELATION_MAP as _ONTOLOGY_KIND_RELATION_MAP,
    MAX_REFLECTION_TEXT_TOKENS as _ONTOLOGY_MAX_REFLECTION_TEXT_TOKENS,
    RELATION_TYPES as _ONTOLOGY_RELATION_TYPES,
    TEMPORAL_SCOPES as _ONTOLOGY_TEMPORAL_SCOPES,
    allowed_relation_types as _ontology_allowed_relation_types,
    entity_kind as _ontology_entity_kind,
    validate_reflection_ontology as _validate_reflection_ontology,
)
from memory._reflection.schema import (
    REFLECTION_ARCHIVE_DAYS,
    make_archive_stamper,
    normalize_reflection,
    prepare_save_reflections,
    refine_reflection_id,
    reflection_id_from_facts as _reflection_id_from_facts,
)
from memory._reflection.refine import (
    build_merge_reflection,
    build_split_reflection,
)
from memory._reflection.selection import (
    filter_active_confirmed,
    filter_followup_candidates,
    followup_render_key,
    in_window,
    record_mentions,
    update_suppressions,
)
from memory._reflection.transitions import (
    apply_batch_mark,
    apply_mark_surfaced_handled,
    apply_promotion_status,
    compute_merged_evidence,
    find_reflection,
)

# Compatibility re-exports keep every old top-level name and object identity.
from ._shared import (  # noqa: F401
    RELATION_TYPES,
    ENTITY_KINDS,
    KIND_RELATION_MAP,
    TEMPORAL_SCOPES,
    MAX_REFLECTION_TEXT_TOKENS,
    _entity_kind,
    _allowed_relation_types,
    _REFLECTION_ARCHIVE_DAYS,
    logger,
    MIN_FACTS_FOR_REFLECTION,
    REFLECTION_TERMINAL_STATUSES,
    REFLECTION_COOLDOWN_MINUTES,
)

if TYPE_CHECKING:
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager

# The manager remains import-compatible while domain methods live in semantic mixins: persistence, synthesis, refinement, evidence_flow, surfacing, promotion, promotion_merge.
from .manager import ReflectionEngine  # noqa: F401
