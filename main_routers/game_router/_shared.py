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

"""Shared APIRouter instance, logger and debug-material logging for the
game_router package.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

import json
import math
import re
from typing import Any, Dict
from urllib.parse import urlparse
from fastapi import APIRouter
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Game")


router = APIRouter(tags=["game"], prefix="/api/game")


# 游戏期间外部主入口路由状态。这里记录的是“主语音入口/主聊天窗是否被游戏接管”，
# 不是游戏页面内部另起一套聊天入口。
# 实际容器在 utils/game_route_state.py，这里只 re-import 以保持现有调用点 / 测试
# 使用 game_router._game_route_states / _route_state_key 的写法不变。
_DEFAULT_LAST_FULL_DIALOGUE_COUNT = 8


_ACCIDENTAL_GAME_ENTRY_GRACE_MS = 10_000


_GAME_DEBUG_MATERIAL_LOG_LIMIT = 24000


def _log_game_debug_material(
    label: str,
    material: Any,
    *,
    game_type: str = "",
    session_id: str = "",
    lanlan_name: str = "",
    source: str = "",
) -> None:
    """Log test-visible game context/memory material with a bounded body."""
    if isinstance(material, str):
        text = material
    else:
        try:
            text = json.dumps(material, ensure_ascii=False, indent=2)
        except Exception:
            text = str(material)
    if not text.strip():
        return
    truncated = ""
    body = text
    if len(body) > _GAME_DEBUG_MATERIAL_LOG_LIMIT:
        omitted = len(body) - _GAME_DEBUG_MATERIAL_LOG_LIMIT
        body = body[:_GAME_DEBUG_MATERIAL_LOG_LIMIT]
        truncated = f" truncated=+{omitted}"
    logger.info(
        "🎮 调试材料[%s]: game=%s session=%s lanlan=%s source=%s chars=%d%s\n%s",
        label,
        game_type or "-",
        session_id or "-",
        lanlan_name or "-",
        source or "-",
        len(text),
        truncated,
        body,
    )


def _infer_service_source(base_url: str, model: str = "", api_type: str = "") -> Dict[str, str]:
    """Infer a compact provider label for logs/debug responses."""
    raw_url = str(base_url or "").strip()
    raw_model = str(model or "").strip()
    raw_api_type = str(api_type or "").strip()
    model_lower = raw_model.lower()
    api_lower = raw_api_type.lower()

    host = ""
    try:
        host = (urlparse(raw_url).hostname or "").lower()
    except Exception:
        host = ""

    provider = "unknown"
    if api_lower == "local" or host in {"localhost", "127.0.0.1"}:
        provider = "local"
    elif api_lower == "gemini" or "gemini" in model_lower or "googleapis.com" in host or "generativelanguage" in host:
        provider = "gemini"
    elif "qwen" in model_lower or "dashscope" in host or "aliyuncs.com" in host:
        provider = "qwen"
    elif "glm" in model_lower or "bigmodel.cn" in host:
        provider = "glm"
    elif "gpt" in model_lower or "openai" in host:
        provider = "openai"
    elif "openrouter" in host:
        provider = "openrouter"
    elif "lanlan.app" in host and "free" in model_lower:
        provider = "lanlan-free"
    elif api_lower:
        provider = api_lower
    elif host:
        provider = host

    label_parts = [provider]
    if raw_model:
        label_parts.append(raw_model)

    return {
        "provider": provider,
        "model": raw_model,
        "api_type": raw_api_type,
        "base_url": raw_url,
        "host": host,
        "label": " / ".join(label_parts),
    }


def _strip_json_fence(text: str) -> str:
    """Extract the JSON body from an LLM reply, tolerating ```json code fences."""
    raw = text.strip()
    code_block = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, flags=re.S)
    if code_block:
        return code_block.group(1).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if 0 <= json_start < json_end:
        return raw[json_start:json_end + 1].strip()
    return raw


def _normalize_short_text(value: Any, *, max_chars: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def _is_repeated_neko_invite(opening_line: str, neko_invite_text: str) -> bool:
    line = re.sub(r"[\s，。！？、,.!?~～\"'“”‘’]+", "", str(opening_line or ""))
    invite = re.sub(r"[\s，。！？、,.!?~～\"'“”‘’]+", "", str(neko_invite_text or ""))
    if not line or not invite:
        return False
    return line == invite or line in invite or invite in line


def _coerce_payload_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _coerce_payload_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
