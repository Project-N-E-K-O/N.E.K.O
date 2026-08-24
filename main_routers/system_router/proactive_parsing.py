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

"""Compatibility facade for proactive contracts and generation parsers."""

from main_logic.proactive_chat.contracts import (  # noqa: F401
    PROACTIVE_REASON_CHAT_DELIVERED,
    PROACTIVE_REASON_PASS_BUSY,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY,
    PROACTIVE_REASON_PASS_DISABLED,
    PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
    PROACTIVE_REASON_PASS_PRIVACY,
    PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY,
    PROACTIVE_REASON_PASS_THROTTLED,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY,
    PROACTIVE_REASON_PASS_MODEL_PASS,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY,
    PROACTIVE_REASON_PASS_DUPLICATE,
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_DELIVERY_FAILED,
    PROACTIVE_REASON_ERROR_TIMEOUT,
    PROACTIVE_REASON_ERROR_INTERNAL,
    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
    PROACTIVE_REASON_PASS_UNSPECIFIED,
    PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_STAGE_MODEL_DECISION,
    PROACTIVE_STAGE_GENERATION,
    PROACTIVE_STAGE_DEDUP,
    PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_STAGE_UNKNOWN,
    _PROACTIVE_REASON_STAGE,
    _proactive_stage_for_reason,
    _proactive_response_body,
    _proactive_pass_body,
    _proactive_chat_body,
    _proactive_error_body,
    _ensure_proactive_reason_code,
)
from main_logic.proactive_chat.generation import (  # noqa: F401
    _INTENT_LABEL_DECOR,
    _PROACTIVE_BRACKET_TAG_RE,
    _PROACTIVE_LEGAL_SOURCE_TAGS,
    _PROACTIVE_LEGAL_TAG_RE,
    _PROACTIVE_SCREEN_TAG_LEAKS,
    _lookup_link_by_title,
    _parse_unified_phase1_result,
    _parse_web_screening_result,
    _strip_proactive_intent_label_leak,
    _strip_proactive_screen_tag_leak,
    _text_is_pass_sentinel,
)
from main_logic.proactive_chat.sources import _extract_links_from_raw  # noqa: F401
