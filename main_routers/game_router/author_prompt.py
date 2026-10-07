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

"""Bounded author-managed message sequences for mini-game dialogue.

The game author owns the order and stable-prefix strategy inside ``messages``.
The host still selects credentials/provider/model and prepends one protected
N.E.K.O character boundary. Calls are one-shot and retain no host-side message
history after the request finishes.
"""

import asyncio
import time
from typing import Any, Dict

from config.prompts.prompts_minigame_common import get_author_managed_dialogue_host_prompt

from ._shared import _infer_service_source
from .char_info import _get_character_info


_AUTHOR_PROMPT_MODE = "author-managed"
_AUTHOR_PROMPT_ROLES = frozenset({"system", "user", "assistant"})
_AUTHOR_PROMPT_MAX_MESSAGES = 32
_AUTHOR_PROMPT_MAX_CONTENT_CHARS = 16_000
_AUTHOR_PROMPT_MAX_TOTAL_CHARS = 64_000
_AUTHOR_PROMPT_TIMEOUT_SECONDS = 15.0


def _normalize_author_managed_prompt(value: Any) -> Dict[str, Any] | None:
    """Validate the public author-managed Prompt envelope without reordering it."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("prompt must be an object")
    unsupported_fields = set(value) - {"mode", "messages"}
    if unsupported_fields:
        raise ValueError(f"prompt contains unsupported field: {sorted(unsupported_fields)[0]}")
    if value.get("mode") != _AUTHOR_PROMPT_MODE:
        raise ValueError("prompt.mode must be author-managed")

    raw_messages = value.get("messages")
    if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= _AUTHOR_PROMPT_MAX_MESSAGES:
        raise ValueError(f"prompt.messages must contain between 1 and {_AUTHOR_PROMPT_MAX_MESSAGES} items")

    total_chars = 0
    messages: list[Dict[str, str]] = []
    for index, raw_message in enumerate(raw_messages):
        if not isinstance(raw_message, dict):
            raise ValueError(f"prompt.messages[{index}] must be an object")
        unsupported_message_fields = set(raw_message) - {"role", "content"}
        if unsupported_message_fields:
            raise ValueError(f"prompt.messages[{index}] contains an unsupported field")
        role = raw_message.get("role")
        content = raw_message.get("content")
        if role not in _AUTHOR_PROMPT_ROLES:
            raise ValueError(f"prompt.messages[{index}].role is invalid")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"prompt.messages[{index}].content must be a non-empty string")
        if len(content) > _AUTHOR_PROMPT_MAX_CONTENT_CHARS:
            raise ValueError(f"prompt.messages[{index}].content exceeds its size limit")
        total_chars += len(content)
        if total_chars > _AUTHOR_PROMPT_MAX_TOTAL_CHARS:
            raise ValueError("prompt.messages exceed their total size limit")
        messages.append({"role": role, "content": content})

    return {"mode": _AUTHOR_PROMPT_MODE, "messages": messages}


def _build_author_managed_messages(
    prompt: Dict[str, Any],
    *,
    game_type: str,
    character_info: Dict[str, Any],
    prompt_locale: str | None,
) -> list[Any]:
    """Prepend the protected host message, then preserve author order exactly."""
    from utils.llm_client import AIMessage, HumanMessage, SystemMessage

    host_prompt = get_author_managed_dialogue_host_prompt(prompt_locale).format(
        game_type=str(game_type or "game"),
        name=str(character_info.get("lanlan_name") or "N.E.K.O"),
        master_name=str(character_info.get("master_name") or "Player"),
        personality=str(character_info.get("lanlan_prompt") or ""),
    )
    role_types = {
        "system": SystemMessage,
        "user": HumanMessage,
        "assistant": AIMessage,
    }
    messages = [SystemMessage(content=host_prompt)]
    messages.extend(
        role_types[item["role"]](content=item["content"])
        for item in prompt["messages"]
    )
    return messages


async def _run_author_managed_game_chat(
    game_type: str,
    lanlan_name: str,
    prompt: Dict[str, Any],
    *,
    prompt_locale: str | None = None,
) -> Dict[str, Any]:
    """Run one bounded request without creating or retaining a game session."""
    from utils.llm_client import create_chat_llm_async
    from utils.token_tracker import set_call_type

    character_info = _get_character_info(lanlan_name)
    messages = _build_author_managed_messages(
        prompt,
        game_type=game_type,
        character_info=character_info,
        prompt_locale=prompt_locale or character_info.get("user_language_full"),
    )
    set_call_type("game_chat")
    llm = await create_chat_llm_async(
        character_info["model"],
        character_info["base_url"],
        character_info["api_key"],
        provider_type=character_info.get("provider_type"),
        max_completion_tokens=800,
        timeout=_AUTHOR_PROMPT_TIMEOUT_SECONDS,
    )
    started_at = time.perf_counter()
    async with llm:
        result = await asyncio.wait_for(
            llm.ainvoke(messages),  # noqa: LLM_INPUT_BUDGET  # Public contract caps messages at 32 / 64k characters total.
            timeout=_AUTHOR_PROMPT_TIMEOUT_SECONDS,
        )
    return {
        "reply": str(getattr(result, "content", "") or ""),
        "llm_ms": int((time.perf_counter() - started_at) * 1000),
        "source": {
            **_infer_service_source(
                character_info.get("base_url", ""),
                character_info.get("model", ""),
                character_info.get("api_type", ""),
            ),
            "prompt_mode": _AUTHOR_PROMPT_MODE,
        },
        "message_count": len(prompt["messages"]),
        "roles": [item["role"] for item in prompt["messages"]],
    }
