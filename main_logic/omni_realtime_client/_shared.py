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

import asyncio  # noqa: F401 - compatibility export and sibling dependency

import uuid  # noqa: F401 - compatibility export and sibling dependency

import websockets  # noqa: F401 - compatibility export and sibling dependency

import json  # noqa: F401 - compatibility export and sibling dependency

import base64  # noqa: F401 - compatibility export and sibling dependency

import time  # noqa: F401 - compatibility export and sibling dependency

import wave  # noqa: F401 - compatibility export and sibling dependency

import numpy as np  # noqa: F401 - compatibility export and sibling dependency

import soxr  # noqa: F401 - compatibility export and sibling dependency

from pathlib import Path  # noqa: F401

from typing import Optional, Callable, Dict, Any, Awaitable, List  # noqa: F401

from enum import Enum

from main_logic.tool_calling import (  # noqa: F401
    OnToolCallCallback,
    ToolCall,
    ToolDefinition,
    ToolResult,
    parse_arguments_json,
)

from config import (  # noqa: F401
    NATIVE_IMAGE_MIN_INTERVAL,
    IMAGE_IDLE_RATE_MULTIPLIER,
    OMNI_RECENT_RESPONSES_MAX,
    OMNI_WS_FRAME_LIMIT_BYTES,
    VISION_ANALYSIS_MAX_TOKENS,
)

from utils.config_manager import get_config_manager  # noqa: F401

from utils.audio_processor import AudioProcessor  # noqa: F401

from utils.file_utils import atomic_write_json  # noqa: F401

from utils.frontend_utils import calculate_text_similarity  # noqa: F401

from utils.tts.providers.gemini import normalize_gemini_tts_voice  # noqa: F401

from utils.logger_config import get_module_logger  # noqa: F401

from utils.ssl_env_diagnostics import write_ssl_diagnostic  # noqa: F401

from utils.tts.providers.stepfun import get_stepfun_tts_default_voice  # noqa: F401

logger = get_module_logger(__name__, "Main")

_IMAGE_ANALYSIS_PENDING_DESCRIPTION = "[实时屏幕截图或相机画面正在分析中。先不要瞎编内容，可以稍等片刻。在此期间不要用搜索功能应付。等收到画面分析结果后再描述画面。]"

class TurnDetectionMode(Enum):
    SERVER_VAD = "server_vad"
    MANUAL = "manual"
