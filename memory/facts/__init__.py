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

"""FactStore — Tier 1 of the three-tier memory hierarchy.

Extracts atomic facts from conversations using LLM, deduplicates via
SHA-256 hash + FTS5 semantic search, and persists to JSON files.
Facts are indexed in TimeIndexedMemory's FTS5 table for later retrieval.

Split into semantic domain mixins matching ``memory/persona/`` and
``memory/reflection/``: persistence, forget_archive, speaker_trust,
formatting, extraction, write_pipeline, signals, external_import,
recheck_manual. All original top-level names are re-exported here and
test-patched facade attributes are read late-bound by every submodule.
"""
# ruff: noqa: F401
from __future__ import annotations
import hashlib
import itertools
import json
import os
import re
import asyncio
import secrets
import threading
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from config import (
    EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS,
    EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS,
    EVIDENCE_DETECT_SIGNALS_MODEL_TIER,
    EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
    EXTERNAL_IMPORT_DAILY_CALL_INTERVAL,
    EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS,
    EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY,
    EXTERNAL_IMPORT_DAILY_MAX_FILES,
    MEMORY_SCHEMA_VERSION_CURRENT,
    SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS,
    SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS,
    SCOPED_BATCH_SEGMENT_NONCE_BYTES,
)
from memory.temporal import (
    compute_event_timestamps,
    normalize_event_when,
    to_naive_local,
)
from config.prompts.prompts_memory import (
    get_fact_extraction_batch_prompt,
    get_fact_extraction_prompt,
    get_scoped_batch_middle_omission_marker,
    get_signal_detection_prompt,
)
from memory import cold_store
from memory.evidence import evidence_score
from memory.timeindex import (
    FACT_NEAR_DUP_ARBITRATE_OVERLAP,
    FACT_NEAR_DUP_ENQUEUE_TIMEOUT_SECONDS,
)
from memory.scopes import (
    SUBJECT_GROUP_CHAT,
    MemorySubject,
    coerce_subject,
    entry_matches_subject,
    subject_from_entry,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import (
    detect_prompt_language_with_ascii_fallback,
    get_global_language_full,
)
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
if TYPE_CHECKING:
    from memory.timeindex import TimeIndexedMemory

from ._shared import (
    logger,
    SHARED_SOURCE_OVERLAP,
    EXTRACTION_PATH_B,
    _detect_fact_extraction_prompt_language,
    _ARCHIVE_AGE_DAYS,
    _ARCHIVE_COOLDOWN_HOURS,
    safe_importance,
    safe_int_field,
    FactExtractionFailed,
    _readable_fact_id,
    _TYPED_TRUST_FACT_ID_PREFIX,
    _speaker_trust_fact_id,
    _speaker_trust_fact_identity,
    _fact_scoped_identity,
    _COLD_MARKERS,
    _is_cold_row,
    _merge_archive_entries,
    _RECHECK_MEM_BOOTSTRAP,
)

from .manager import FactStore
