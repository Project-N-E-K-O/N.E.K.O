# -- coding: utf-8 --
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

from ._shared import (  # noqa: F401 - preserve the legacy module namespace
    AIMessage,
    Any,
    Awaitable,
    Callable,
    DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
    Dict,
    FOCUS_THINKING_EXTRA_TOKENS,
    HumanMessage,
    LLMStreamChunk,
    List,
    OMNI_RECENT_RESPONSES_MAX,
    OnToolCallCallback,
    Optional,
    SystemMessage,
    ThinkingStreamStripper,
    ToolCall,
    ToolDefinition,
    ToolLeakFilter,
    ToolResult,
    _API_KEY_REJECTED_KEYWORDS,
    _GENAI_NATIVE_BASE_URL_HINTS,
    _GENAI_NATIVE_MODEL_HINTS,
    _GIBBERISH_MIN_LEN,
    _GIBBERISH_PS_RATIO_CEIL,
    _GIBBERISH_PS_RATIO_FLOOR,
    _LLM_RETRY_ERROR_TYPES,
    _MAX_TOKENS_SLACK,
    _NONVERBAL_DIRECTIVE_PATTERN,
    _PROACTIVE_SCREENSHOT_TTL_SECONDS,
    _SAFETY_VIOLATION_KEYWORDS,
    _SENTENCE_END_CHARS,
    _SUMMARY_API_BUDGET_FLOOR,
    _SUMMARY_GIBBERISH_RECHECK_TOKENS,
    _SUMMARY_HARD_TOKEN_CAP,
    _SUMMARY_LATE_FINISH_SLACK,
    _SUMMARY_TERMINATOR_CHARS,
    _UNLIMITED_BUDGET,
    _budget_to_max_tokens,
    _find_summary_terminator,
    _is_api_key_rejected_error,
    _is_gibberish_response,
    _is_safety_violation_signal,
    _llm_retry_error_types,
    _strip_nonverbal_directives,
    _truncate_to_last_sentence_end,
    asyncio,
    calculate_text_similarity,
    chat_retry_error_types,
    count_tokens,
    create_chat_llm,
    create_chat_llm_async,
    get_module_logger,
    json,
    log_tool_leak_filtered,
    logger,
    parse_arguments_json,
    re,
    set_call_type,
    strip_thinking_segments,
    time,
    truncate_to_tokens,
)

from ._genai_support import (  # noqa: F401 - preserve the legacy module namespace
    _GENAI_AVAILABLE,
    _GenaiToolsUnsupported,
    _ensure_genai,
    _genai,
    _genai_messages_to_contents,
    _genai_parts_from_content,
    _genai_types,
    _should_use_genai_sdk,
)

from ._client import OmniOfflineClient  # noqa: F401

__import__(
    "main_logic._module_state_proxy",
    fromlist=["install_state_proxy"],
).install_state_proxy(
    __name__,
    {
        "_LLM_RETRY_ERROR_TYPES": "_shared",
        "_GENAI_AVAILABLE": "_genai_support",
        "_genai": "_genai_support",
        "_genai_types": "_genai_support",
    },
)
