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

"""Memory Router package.

Formerly the monolithic ``main_routers/memory_router.py``; now split by route
domain. All submodules register endpoints on the single shared ``APIRouter``
defined in ``_shared`` (imported below in the original route-declaration
order); every top-level name of the old module is re-exported here so existing
imports and monkeypatches targeting ``main_routers.memory_router`` keep working.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or ``.agent/rules/neko-guide.md``
(API URLs omit trailing slashes) for the rationale; enforced by
``scripts/check_api_trailing_slash.py``.
"""

# ruff: noqa: F401
import asyncio
import base64
import binascii
import hashlib
import os
import re
import json
from contextlib import suppress
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote
import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.character_memory import (
    is_legacy_vector_store_dir,
    character_memory_exists,
    iter_character_memory_roots,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.logger_config import get_module_logger
# In merged single-process mode (release default), this module and memory_server writers share a process
# and utils.recent_file's per-path lock; bare atomic_write_json_async bypasses it.
from utils.recent_file import (
    RecentFileDeletedError,
    capture_recent_generation,
    get_recent_pending_unlocked,
    read_recent_text_unlocked,
    recent_file_access,
    set_recent_pending_unlocked,
    write_recent_payload_unlocked,
)
from fastapi.responses import JSONResponse, Response
from memory.external_markdown_import import (
    ExternalMemoryImportError,
    MAX_TOTAL_BYTES,
    batch_daily_fragments,
    build_import_candidates,
    collect_markdown_files,
)
from memory.external_molili_import import (
    MAX_ZIP_BYTES,
    build_molili_candidates,
    detect_molili_archive,
)

from ._shared import (  # noqa: F401
    router,
    _memory_toggle_write_lock,
    VALID_RECENT_FILENAME_PATTERN,
    PATH_ERROR_INVALID_REQUEST,
    PATH_ERROR_NOT_FOUND,
    REPETITION_INSIGHT_LANGUAGES,
    _RECENT_MESSAGE_TEXT_MAX_CHARS,
    _RECENT_CHAT_TOTAL_CHARS_MAX,
    _RECENT_CHAT_MAX_MESSAGES,
    logger,
    _RECENT_BROWSER_ROLE_TYPES,
)
from .paths import (  # noqa: F401
    extract_catgirl_name_from_recent_filename,
    build_recent_filename,
    iter_recent_memory_files,
    resolve_recent_file_path,
    path_error_status_code,
    validate_catgirl_name,
    validate_chat_payload,
    validate_recent_filename,
    safe_memory_path,
    _default_memory_dir_escapes_root,
    _configured_character_key,
    _insight_selectable_name,
    _resolve_memory_character,
)
from .insights import (  # noqa: F401
    RepetitionInsightsRequest,
    RepetitionEffectsResetRequest,
    _empty_repetition_effects,
    _empty_message_scoped_repetition_effects,
    _is_safe_containment_phrase,
    _is_runtime_detector_signature,
    _repetition_association_language,
    _phrases_contain_each_other,
    _aggregate_repetition_associations,
    _associate_repetition_effects,
    repetition_insights,
    reset_repetition_effects,
    HabitualPhraseOverrideRequest,
    HabitualPhraseRecomputeRequest,
    _ahabitual_phrase_language,
    _habitual_phrase_panel,
    get_habitual_phrases,
    override_habitual_phrase,
    recompute_habitual_phrases,
    get_insight_characters,
)
from .recent_files import (  # noqa: F401
    _await_browser_save_transaction,
    _recent_browser_fingerprint,
    _recent_browser_identity_token,
    _read_recent_browser_text_unlocked,
    _read_recent_browser_text,
    _read_recent_browser_snapshot,
    _RecentBrowserTheaterEditRejected,
    _recent_payload_type_and_content,
    _merge_browser_payload_with_theater,
    _write_recent_browser_payload,
    _read_recent_browser_conflict_tokens,
    _recent_browser_conflict_response,
    get_recent_files,
    get_recent_file,
    save_recent_file,
    update_catgirl_name,
)
from .settings import (  # noqa: F401
    _InvalidMemoryToggleValue,
    _load_memory_toggle_config,
    _load_memory_toggle_enabled,
    get_farewell_summary,
    get_review_config,
    update_review_config,
    get_powerful_memory_config,
    update_powerful_memory_config,
)
from .import_legacy import (  # noqa: F401
    _collect_legacy_memory_roots,
    _directory_size_safe,
    _external_import_error,
    _decode_external_archive,
    _prepare_external_import,
    preview_external_memory_import,
    commit_external_memory_import,
    get_external_import_progress,
    scan_legacy_memory,
    _is_path_within,
    purge_legacy_memory,
)
from .browser_proxy import (  # noqa: F401
    _proxy_memory_browser,
    _validate_browser_character_or_400,
    _MANUAL_LLM_COOLDOWN_SECONDS,
    _manual_llm_last_trigger,
    _check_manual_llm_cooldown,
    _parse_and_validate_browser_post,
    get_memory_overview,
    generate_memory_imprint_note,
    search_character_memory,
    _sync_local_directives_cache,
    _proxy_character_mutation,
    edit_persona_memory_entry,
    set_persona_memory_flags,
    archive_persona_memory_entry,
    restore_persona_memory_version,
    resolve_memory_correction,
    review_memory_reflection,
    edit_memory_fact,
    archive_memory_fact,
    merge_duplicate_memory,
    add_memory_directive,
    remove_memory_directive,
    get_memory_star_map,
    label_memory_star_map_clusters,
    get_memory_lineage_story,
    get_related_memories,
    list_memory_snapshots,
    create_memory_snapshot,
    restore_memory_snapshot,
    export_memory_archive,
    get_memory_lineage_graph,
    run_memory_deep_conflict_check,
)
