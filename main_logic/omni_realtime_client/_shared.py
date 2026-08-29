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

import os  # noqa: F401 - compatibility export and sibling dependency

import uuid  # noqa: F401 - compatibility export and sibling dependency

import websockets  # noqa: F401 - compatibility export and sibling dependency
# Explicit, because `websockets.exceptions` is NOT reachable as a lazy
# attribute on the package (measured on 15.0.1: it raises AttributeError
# until some submodule import pulls it in). `handle_messages` names it in
# except clauses, and today those only resolve because `connect()` ran
# first and imported `websockets.asyncio.client` as a side effect. That
# holds in production and does not hold anywhere else — a test driving
# the receive loop over a fake socket hits AttributeError inside the
# except clause instead of the handler it was reaching for.
import websockets.exceptions  # noqa: F401 - named in except clauses

import json  # noqa: F401 - compatibility export and sibling dependency

import base64  # noqa: F401 - compatibility export and sibling dependency

import time  # noqa: F401 - compatibility export and sibling dependency

import wave  # noqa: F401 - compatibility export and sibling dependency

import numpy as np  # noqa: F401 - compatibility export and sibling dependency

import soxr  # noqa: F401 - compatibility export and sibling dependency

from pathlib import Path  # noqa: F401

from typing import Optional, Callable, Dict, Any, Awaitable, List  # noqa: F401

from enum import Enum

from dataclasses import dataclass

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


class VisualDeliveryMode(str, Enum):
    """How ambient images are delivered to the active realtime session."""

    NATIVE = "native"
    EXTERNAL_DESCRIPTION = "external_description"


@dataclass(frozen=True, slots=True)
class ImageStageResult:
    """Observable result of staging or delivering one image."""

    accepted: bool
    mode: str
    generation: int | None = None
    description: str | None = None
    rejection_reason: str | None = None
    # 成功送出后仍然注册着的拒绝回调的 event_id。拒绝可能晚于 send 返回才到，所以
    # stream_image 不能自己摘掉它；但一旦调用方拿到「provider 已处理」的更强证据
    # （例如 session.updated 屏障），这个 handler 就无关了，而它的闭包扣着整条
    # callback（可能有数张 ~13MB 的 base64）。把 id 交出去，让那个证据点去摘。
    rejection_event_id: str | None = None


# Opt-in escape hatch for the response arbiter's escalation policy (issue
# #2583). When a response lifecycle cannot reach a terminal state the arbiter
# tears the realtime WebSocket down by default — safe, but a provider-side
# event-timing quirk in the field would then present as repeated
# disconnect-and-rebuild, with no server-side switch to reach those users.
# Setting this makes the arbiter end only the stuck turn and keep the
# connection, whenever it can still tell whose events are whose.
#
# An environment variable on purpose, not a settings-UI toggle: the support
# path is "set this, restart, tell us if it helped". Read once per client
# construction, so a change needs a restart.
_ARBITER_FAIL_OPEN_ENV_VAR = "NEKO_REALTIME_ARBITER_FAIL_OPEN"


def response_arbiter_fail_open_enabled() -> bool:
    """Read the arbiter fail-open escape hatch. Default off."""

    raw = os.getenv(_ARBITER_FAIL_OPEN_ENV_VAR, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ``api_type`` carries the provider key that ``CORE_API_TYPE`` resolves to
# ('openai', 'qwen_intl', ...), never a model-name fragment. Several wire
# branches were written against 'gpt' — a value the config layer has never
# produced — so an OpenAI session silently missed every mid-session tool
# update while its connect-time tools still went out (that path matches on
# the model name, where 'gpt' really does appear). 'qwen_intl' missed the
# 'qwen' branch the same way. Normalising here keeps the dialect branches
# keyed on one vocabulary; 'gpt' stays accepted so existing callers and
# fixtures that pass it keep working.
_REALTIME_DIALECT_ALIASES = {
    "openai": "gpt",
    "gpt": "gpt",
    "qwen_intl": "qwen",
}


# 取消欠账的存活上限。被取消的那一轮欠一条终结事件，而 Gemini 没有
# response.cancel —— 它是被**后继内容送达**叫停的，所以计时从那一刻起算
# （_gemini_send_user_turn），不是从 handle_interruption 决定取消那一刻。
# 取值方向不对称：取小了最多多一次早结算，会话读作空闲，下一轮自愈；取大了会让
# 陈旧欠账吃掉一条**合法**终结，那一轮的 external token 没人结算，
# is_active_response() 恒真、主动搭话彻底哑。所以宁可短。
GEMINI_CANCELLED_TERMINAL_TTL_SECONDS = 3.0


def canonical_realtime_dialect(api_type: object) -> str:
    """Map a provider key to the wire dialect its session speaks."""

    raw = str(api_type or "").strip().lower()
    return _REALTIME_DIALECT_ALIASES.get(raw, raw)
