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

"""Manual on-demand warm relationship note generator for the Imprint view.

LLM discipline:
- Uses ``summary`` tier from ``config_manager.aget_model_api_config('summary')``.
- Never passes ``temperature``.
- Enforces both input token budget (``IMPRINT_NOTE_INPUT_MAX_TOKENS``) and
  output budget + timeout (``IMPRINT_NOTE_MAX_TOKENS``, ``IMPRINT_NOTE_TIMEOUT_SECONDS``).
- Persists result to ``memory/<character>/imprint_notes_cache.json`` so opening
  the Memory Browser never triggers automatic LLM calls.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    IMPRINT_NOTE_INPUT_MAX_TOKENS,
    IMPRINT_NOTE_MAX_TOKENS,
    IMPRINT_NOTE_TIMEOUT_SECONDS,
)
from config.prompts.prompts_memory import get_imprint_note_prompt
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json_async
from utils.language_utils import get_global_language_full, normalize_language_code
from utils.llm_client import create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.token_budget import truncate_to_tokens
from utils.token_tracker import set_call_type

from .imprint import _IMPRINT_CACHE_FILENAME, abuild_memory_overview

logger = get_module_logger(__name__, "Memory")


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _build_memory_digest(overview: dict[str, Any]) -> tuple[str, str]:
    stats = overview.get("imprint", {}).get("stats", {})
    stats_summary = (
        f"companion_days={stats.get('companion_days', 0)}, "
        f"total_messages={stats.get('total_messages', 0)}, "
        f"facts_total={stats.get('facts_total', 0)}, "
        f"imprint_persona_count={stats.get('imprint_persona_count', 0)}"
    )
    groups = overview.get("memories", {}).get("groups", {})
    milestones = overview.get("imprint", {}).get("milestones", [])
    evolution = overview.get("imprint", {}).get("evolution", [])

    lines: list[str] = []
    for item in groups.get("about_you", [])[:12]:
        lines.append(f"- [about_user] {item.get('text', '')}")
    for item in groups.get("relationship", [])[:8]:
        lines.append(f"- [relationship] {item.get('text', '')}")
    for item in groups.get("growth", [])[:6]:
        lines.append(f"- [companion_growth] {item.get('text', '')}")
    for item in milestones[:6]:
        lines.append(f"- [milestone] {item.get('text', '')}")
    for item in evolution[:4]:
        lines.append(
            f"- [evolution] {item.get('previous_text', '')} -> {item.get('current_text', '')}"
        )

    digest = "\n".join(lines) if lines else "(No long-term memories yet)"
    capped_digest = truncate_to_tokens(digest, IMPRINT_NOTE_INPUT_MAX_TOKENS)
    return stats_summary, capped_digest


async def agenerate_imprint_note(
    character_name: str,
    *,
    language: str | None = None,
    config_manager=None,
) -> dict[str, Any]:
    """Generate and cache a warm relationship summary/commentary/blessing via summary tier."""
    cm = config_manager or get_config_manager()
    memory_dir = Path(cm.memory_dir)
    assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{character_name}/{_IMPRINT_CACHE_FILENAME}",
    )

    overview = await abuild_memory_overview(character_name, memory_dir=memory_dir)
    stats_summary, memory_digest = _build_memory_digest(overview)

    raw_lang = language or get_global_language_full()
    prompt_lang = normalize_language_code(raw_lang, format="short")
    if str(raw_lang or "").lower().replace("_", "-") in ("zh-tw", "zh-hk", "zh-hant"):
        prompt_lang = "zh-TW"

    template = get_imprint_note_prompt(prompt_lang)
    prompt = (
        template.replace("{CHARACTER_NAME}", str(character_name))
        .replace("{STATS_SUMMARY}", stats_summary)
        .replace("{MEMORY_DIGEST}", memory_digest)
    )
    prompt = truncate_to_tokens(prompt, IMPRINT_NOTE_INPUT_MAX_TOKENS + 600)

    api_config = await cm.aget_model_api_config("summary")
    set_call_type("imprint_warm_note")
    llm = await create_chat_llm_async(
        api_config["model"],
        api_config["base_url"],
        api_config["api_key"],
        timeout=IMPRINT_NOTE_TIMEOUT_SECONDS,
        max_retries=0,
        max_completion_tokens=IMPRINT_NOTE_MAX_TOKENS,
        provider_type=api_config.get("provider_type"),
    )
    try:
        resp = await llm.ainvoke(prompt)
    finally:
        await llm.aclose()

    parsed = _extract_json_object(str(getattr(resp, "content", "") or ""))
    if not parsed:
        raise ValueError("Imprint note model returned invalid JSON")

    summary = str(parsed.get("summary") or "").strip()
    commentary = str(parsed.get("commentary") or "").strip()
    blessing = str(parsed.get("blessing") or "").strip()
    if not (summary or commentary or blessing):
        raise ValueError("Imprint note response was empty")

    note = {
        "summary": summary,
        "commentary": commentary,
        "blessing": blessing,
        "generated_at": datetime.now().isoformat(),
        "language": prompt_lang,
        "source": "llm_cached",
    }

    char_dir = memory_dir / character_name
    await asyncio.to_thread(char_dir.mkdir, parents=True, exist_ok=True)
    await atomic_write_json_async(
        str(char_dir / _IMPRINT_CACHE_FILENAME),
        note,
        indent=2,
        ensure_ascii=False,
    )
    return note
