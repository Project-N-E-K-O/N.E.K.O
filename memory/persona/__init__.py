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
PersonaManager — Tier 3 of the three-tier memory hierarchy.

Manages long-term persona data with dynamic entity support.
Core entities: master (human), neko (AI character), relationship (dynamics).
Storage is entity-agnostic: any entity key can be added at runtime
(e.g. QQ group IDs, other users, other nekos).

Key features:
- Dynamic entity sections: each entity stores a list of facts
- Pending reflections injected with a "(还不太确定)" ("not too sure yet") annotation
- Suppress mechanism: 5h window, >2 mentions → suppress (completely hidden from
  all rendering sections; suppress has highest priority)
- Contradiction detection → queued for batch correction via LLM
- Auto-migration from legacy settings files and v1 entity names
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from config import (
    PERSONA_RENDER_MAX_TOKENS,
    REFLECTION_RENDER_MAX_TOKENS,
)
from memory.evidence import evidence_score
from memory.facts import safe_int_field
from memory.stop_names import (
    acollect_stop_names,
    collect_stop_names,
    strip_stop_names,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import get_module_logger
from utils.tokenize import acount_tokens, count_tokens, tokenizer_identity

from ._shared import (  # noqa: F401
    logger,
    SUPPRESS_MENTION_LIMIT,
    SUPPRESS_WINDOW_HOURS,
    SUPPRESS_COOLDOWN_HOURS,
    SIMILARITY_THRESHOLD,
    AUTO_CONFIRM_DAYS,
    _SPLIT_RE,
    _extract_keywords,
    _is_mentioned,
)

# The manager remains import-compatible while domain methods live in semantic mixins: persistence, facts, corrections, refinement, mentions, rendering.
from .manager import PersonaManager  # noqa: F401
