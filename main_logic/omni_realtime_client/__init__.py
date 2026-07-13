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
    Any,
    AudioProcessor,
    Awaitable,
    Callable,
    Dict,
    Enum,
    IMAGE_IDLE_RATE_MULTIPLIER,
    List,
    NATIVE_IMAGE_MIN_INTERVAL,
    OMNI_RECENT_RESPONSES_MAX,
    OMNI_WS_FRAME_LIMIT_BYTES,
    OnToolCallCallback,
    Optional,
    Path,
    ToolCall,
    ToolDefinition,
    ToolResult,
    TurnDetectionMode,
    VISION_ANALYSIS_MAX_TOKENS,
    _IMAGE_ANALYSIS_PENDING_DESCRIPTION,
    asyncio,
    atomic_write_json,
    base64,
    calculate_text_similarity,
    get_config_manager,
    get_module_logger,
    get_stepfun_tts_default_voice,
    json,
    logger,
    normalize_gemini_tts_voice,
    np,
    parse_arguments_json,
    soxr,
    time,
    uuid,
    wave,
    websockets,
    write_ssl_diagnostic,
)

from ._gemini_support import (  # noqa: F401 - preserve the legacy module namespace
    GEMINI_AVAILABLE,
    _GEMINI_IMPORT_ERROR,
    _config_manager,
    _emit_gemini_import_diagnostic,
    _ensure_gemini_sdk,
    genai,
    types,
)

from ._proactive_audio import (  # noqa: F401 - preserve the legacy module namespace
    _PROACTIVE_AUDIO_CACHE,
    _PROACTIVE_AUDIO_DIR,
    _load_proactive_audio,
)

from ._client import OmniRealtimeClient  # noqa: F401

__import__(
    "main_logic._module_state_proxy",
    fromlist=["install_state_proxy"],
).install_state_proxy(
    __name__,
    {
        "GEMINI_AVAILABLE": "_gemini_support",
        "_GEMINI_IMPORT_ERROR": "_gemini_support",
        "_PROACTIVE_AUDIO_CACHE": "_proactive_audio",
        "_config_manager": "_gemini_support",
        "genai": "_gemini_support",
        "types": "_gemini_support",
    },
)
