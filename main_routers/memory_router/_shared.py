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
"""Shared router instance, logger, locks, and constants for memory_router."""

import asyncio
import re
from fastapi import APIRouter
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/memory", tags=["memory"])


# Request-level serialization for memory switches differs from core_config.json's file-write lock:
# - config_manager.aupdate_json_config guarantees file consistency, preventing stale-snapshot overwrite
#   across read-modify-write. /core_api saves and startup migration use the same lock.
# - This additional lock covers the up-to-10-second memory_server migration when disabling strong memory, making
#   switch requests and frontend-recovery GET reads wait for the in-flight operation's final result.
# Slow migration runs outside the file lock, leaving /core_api and other writers unblocked during those ten seconds.
_memory_toggle_write_lock = asyncio.Lock()


# Pattern for valid recent file names: must start with "recent_", have content, and end with .json
# Uses blacklist approach instead of whitelist to support CJK characters
VALID_RECENT_FILENAME_PATTERN = re.compile(r'^recent_.+\.json$')


PATH_ERROR_INVALID_REQUEST = "INVALID_REQUEST"


PATH_ERROR_NOT_FOUND = "NOT_FOUND"


REPETITION_INSIGHT_LANGUAGES = frozenset(
    {"en", "es", "pt", "ru", "ja", "ko", "zh-CN", "zh-TW"}
)


# Per-message text limit in characters. Incident: a user pasted a large external text
# into recent, persisted as one message. The memory pipeline later fed it
# to the embedder as a stale entry; pad-to-longest across the batch pushed
# activation memory into multiple gigabytes. Even with embedder token-budget
# safeguards, block single huge blobs at this boundary so oversized objects cannot
# spread through NDJSON / database / recall. 32K characters is about 32K Chinese tokens,
# generous for normal chats (5K Chinese characters is already a long article) while bounding
# worst-case inbound size.
_RECENT_MESSAGE_TEXT_MAX_CHARS = 32 * 1024


# Cumulative chat-payload text limit prevents accidents/attacks such as pasting 1000 messages of 30K characters.
# 2MB still accommodates real long chats while rejecting abnormal volume.
_RECENT_CHAT_TOTAL_CHARS_MAX = 2 * 1024 * 1024


# Message-count limit provides redundant protection: hundreds of thousands of short messages
# can still overwhelm downstream scan/embed/render work.
_RECENT_CHAT_MAX_MESSAGES = 10000


logger = get_module_logger("main_routers.memory_router", "Main")


_RECENT_BROWSER_ROLE_TYPES = {"user": "human", "assistant": "ai", "system": "system"}
