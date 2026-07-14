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
"""Compatibility facade for the lightweight LLM client package.

Provider clients, message models, lifecycle state, and factories are split by
domain while every historical top-level import remains available here.
"""
# ruff: noqa: F401

from __future__ import annotations
import asyncio
import base64
import contextvars
import json as _json
import os
import re
import ssl
import threading
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator
from urllib.parse import urlparse

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

from .retry import (
    _ANTHROPIC_RETRY_EXCEPTION_TYPES,
    _OPENAI_RETRY_EXCEPTION_TYPES,
    anthropic_retry_error_types,
    chat_retry_error_types,
    openai_retry_error_types,
)
from .thinking import (
    _THINK_ANY_CLOSE_RE,
    _THINK_DANGLING_CLOSE_RE,
    _THINK_PAIRED_RE,
    ThinkingStreamStripper,
    strip_thinking_segments,
)
from .lifecycle import (
    _DEFAULT_SSL_CONTEXT,
    _DEFAULT_SSL_CONTEXT_LOCK,
    _PENDING_CLIENT_CLOSE_TASKS,
    _active_character,
    _close_async_openai_client_best_effort,
    _close_async_openai_client_from_sync_best_effort,
    _close_chat_clients_best_effort,
    _close_chat_openai_clients_best_effort,
    _create_httpx_default_ssl_context,
    _get_default_ssl_context,
    _schedule_async_openai_client_close_best_effort,
    _substitute_character_placeholders,
    reset_active_character,
    set_active_character,
)
from .cache_control import (
    _CACHE_CONTROL_EPHEMERAL,
    _TEXT_PART_TYPES,
    _attach_cache_control,
    _inject_cache_control,
)
from .messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    LLMResponse,
    LLMStreamChunk,
    SystemMessage,
    ToolCallAggregate,
    _normalize_messages,
    _ROLE_CLS,
    _ROLE_TO_TYPE,
    _TYPE_CLS,
    _TYPE_TO_ROLE,
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)
from .openai_client import ChatOpenAI
from .anthropic_client import (
    Anthropic,
    AsyncAnthropic,
    ChatAnthropic,
    _ANTHROPIC_BODY_OVERRIDE_KEYS,
    _ANTHROPIC_REQUEST_OPTION_KEYS,
    _SENTINEL,
    _anthropic_stop_reason_to_finish_reason,
    _anthropic_text_from_blocks,
    _anthropic_usage_to_dict,
    _anthropic_usage_with_openai_aliases,
    _apply_anthropic_body_fields,
    _coerce_anthropic_max_tokens,
    _convert_openai_content_to_anthropic,
    _convert_openai_tool_call_to_anthropic,
    _convert_openai_tool_choice_to_anthropic,
    _convert_openai_tool_result_to_anthropic,
    _convert_openai_tool_schema_to_anthropic,
    _convert_openai_tools_to_anthropic,
    _convert_orphan_tool_result_to_user_blocks,
    _detect_image_media_type,
    _drop_unanswered_anthropic_tool_uses,
    _is_anthropic_endpoint,
    _is_kimi_code_anthropic_base_url,
    _merge_anthropic_usage,
    _normalize_anthropic_sdk_base_url,
    _normalize_messages_to_anthropic,
    _parse_base_url,
    _record_anthropic_token_usage,
    _sanitize_anthropic_metadata,
)
from .factory import create_chat_llm, create_chat_llm_async, _close_cancelled_chat_llm_result
from .embeddings import OpenAIEmbeddings
from .history import SQLChatMessageHistory
