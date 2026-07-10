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
GalGame Router

POST /api/galgame/options — generate three reply candidates (A serious,
B affectionate, C imaginative) for the player given recent dialogue. The
React chat window calls this after each completed catgirl turn when the
GalGame mode toggle is on.

URL convention: routes declared WITHOUT trailing slash. See the project
``check_api_trailing_slash`` script for enforcement.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.prompts.prompts_galgame import (
    GALGAME_DEFAULT_LANLAN_PLACEHOLDER,
    GALGAME_DEFAULT_MASTER_PLACEHOLDER,
    get_galgame_fallback_options,
    get_galgame_dialogue_footer,
    get_galgame_dialogue_header,
    get_galgame_option_generation_prompt,
)
from config.prompts.prompts_sys import _loc
from utils.file_utils import robust_json_loads
from utils.language_utils import detect_language, normalize_language_code
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

from .shared_state import get_config_manager, get_session_manager

router = APIRouter(prefix="/api", tags=["galgame"])

logger = get_module_logger(__name__, "GalGame")

GALGAME_MAX_HISTORY = 8
GALGAME_MAX_TEXT_PER_TURN = 240
GALGAME_OPTION_MAX_TOKENS = 600
GALGAME_OPTION_TIMEOUT_SECONDS = 10.0
GALGAME_OPTION_LABELS = ("A", "B", "C")


def _resolve_language(text_sample: str, request_lang: str | None) -> str:
    """Pick the best 'short' language code for the prompt."""
    if request_lang:
        try:
            return normalize_language_code(request_lang, format='short') or 'en'
        except Exception:
            # Bad language tag from the client — fall through to text-based detection.
            pass
    try:
        if text_sample.strip():
            return normalize_language_code(detect_language(text_sample), format='short') or 'en'
    except Exception:
        # detect_language can choke on emoji-only / very short strings — default to en.
        pass
    return 'en'


def _coerce_messages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    # Walk the list back-to-front and stop as soon as we have GALGAME_MAX_HISTORY
    # accepted turns. Forward + slice would force O(n) work on adversarial /
    # buggy clients posting megabyte payloads at this boundary endpoint.
    collected: list[dict[str, str]] = []
    for item in reversed(raw):
        if len(collected) >= GALGAME_MAX_HISTORY:
            break
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        text = item.get('text') if isinstance(item.get('text'), str) else item.get('content')
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text:
            continue
        if role not in ('assistant', 'user'):
            role = 'assistant' if item.get('isAssistant') else 'user'
        if len(text) > GALGAME_MAX_TEXT_PER_TURN:
            text = text[:GALGAME_MAX_TEXT_PER_TURN].rstrip() + '…'
        collected.append({'role': role, 'text': text})
    collected.reverse()
    return collected


def _format_dialogue(
    messages: list[dict[str, str]],
    lanlan_name: str,
    master_name: str,
) -> str:
    name_for = {'assistant': lanlan_name, 'user': master_name}
    return "\n".join(f"{name_for[msg['role']]}: {msg['text']}" for msg in messages)


def _strip_code_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, flags=re.S)
    if match:
        return match.group(1).strip()
    return text.strip()


def _take_label_map(obj: Any) -> dict[str, str]:
    """Pull canonical A/B/C label→text entries out of a dict, if any."""
    if not isinstance(obj, dict):
        return {}
    collected: dict[str, str] = {}
    for label in GALGAME_OPTION_LABELS:
        # Accept both upper and lower-case keys ("a"/"A").
        value = obj.get(label)
        if not isinstance(value, str):
            value = obj.get(label.lower())
        if isinstance(value, str) and value.strip():
            collected[label] = value.strip()
    return collected


def _normalize_options(parsed: Any) -> dict[str, str]:
    """Best-effort parse: return whatever label→text mappings the model produced.

    Returns an empty dict if nothing salvageable, otherwise a 1-3 entry dict
    keyed by canonical labels (A/B/C). Callers fill missing labels from
    fallback rather than throwing the whole batch away — preserves any
    on-style replies the model did manage to generate.

    All recognised shapes are *merged*, not selected, so mixed payloads
    like ``{"A": "topA", "options": [{"label":"B","text":"..."}, ...]}``
    keep both the top-level label and the nested list candidates instead
    of one source silently shadowing the other. First write wins on
    same-label conflicts (top-level > nested-map > list).

    Accepted shapes:
      * ``{"A": "...", "B": "...", "C": "..."}`` (top-level label map)
      * ``{"options": {"A": "...", ...}}`` (nested label map)
      * ``{"options": [{"label": "A", "text": "..."}, ...]}`` (canonical list)
      * ``{"options": ["serious", "warm", "wild"]}`` (positional list)
      * Top-level list of either dict or string entries
      * Any mix of the above
    """
    by_label: dict[str, str] = {}

    # Shape 1: top-level dict carries A/B/C directly.
    by_label.update(_take_label_map(parsed))

    if isinstance(parsed, dict):
        candidates = parsed.get('options') or parsed.get('candidates') or parsed.get('replies')
    else:
        candidates = parsed

    # Shape 2: the inner container is itself a label map. Don't clobber any
    # top-level entry — first write wins.
    for label, text in _take_label_map(candidates).items():
        by_label.setdefault(label, text)

    # Shape 3/4/5: list of dict (with `label`) or string entries.
    if isinstance(candidates, list):
        leftover: list[str] = []
        for entry in candidates:
            if isinstance(entry, dict):
                text = entry.get('text') or entry.get('content') or entry.get('reply')
                label = entry.get('label')
            elif isinstance(entry, str):
                text = entry
                label = None
            else:
                continue
            if not isinstance(text, str):
                continue
            text = text.strip()
            if not text:
                continue
            normalized_label = str(label).strip().upper() if label else ''
            if normalized_label in GALGAME_OPTION_LABELS:
                # Recognised label. Take it only if no stronger source
                # (top-level / nested map / earlier list entry) already
                # provided this slot. Never push a labeled-but-duplicate
                # entry into leftover — the model intended this text for
                # one specific style, and reusing it as a positional fill
                # for a different label mis-attributes that style.
                if normalized_label not in by_label:
                    by_label[normalized_label] = text
                continue
            leftover.append(text)

        for label in GALGAME_OPTION_LABELS:
            if label in by_label or not leftover:
                continue
            by_label[label] = leftover.pop(0)

    return by_label


def _fallback_options(lang: str) -> list[dict[str, str]]:
    texts = get_galgame_fallback_options(lang)
    return [
        {'label': label, 'text': text}
        for label, text in zip(GALGAME_OPTION_LABELS, texts)
    ]


@router.post('/galgame/options')
async def generate_galgame_options(request: Request):
    """Generate three reply candidates for the player.

    Request body: {
        "messages": [{"role": "assistant"|"user", "text": "..."}],
        "language": "zh"|"en"|...,
        "lanlan_name": "...",
        "master_name": "..."
    }
    Returns: {"success": true, "options": [{"label":"A","text":"..."}, ...]}
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)

    if not isinstance(data, dict):
        return JSONResponse({"success": False, "error": "invalid_payload"}, status_code=400)

    # Telemetry：galgame 是 feature 之一，counter 用于"哪些功能被实际触发 +
    # 多频繁"。**不**带 lanlan_name（用户自定义角色名，PII + 高基数）。
    try:
        from utils.instrument import counter as _instr_counter
        _instr_counter("feature_invoked", feature="galgame_options")
    except Exception:
        # 埋点失败不能挡 galgame endpoint —— 静默继续，不打日志防刷屏。
        pass

    messages = _coerce_messages(data.get('messages'))
    if not messages or messages[-1]['role'] != 'assistant':
        return JSONResponse(
            {"success": False, "error": "no_assistant_turn"},
            status_code=400,
        )

    last_text = messages[-1]['text']
    lang = _resolve_language(last_text, data.get('language'))

    config_manager = get_config_manager()
    try:
        master_name_current, her_name_current, *_ = await config_manager.aget_character_data()
    except Exception:
        master_name_current, her_name_current = '', ''
    lanlan_name = (data.get('lanlan_name') or her_name_current or '').strip() \
        or _loc(GALGAME_DEFAULT_LANLAN_PLACEHOLDER, lang)
    master_name = (data.get('master_name') or master_name_current or '').strip() \
        or _loc(GALGAME_DEFAULT_MASTER_PLACEHOLDER, lang)

    # 纵深防御：game route 接管会话期间（语音输入被改路由进游戏逻辑），
    # React composer 的 galgame 面板并非当前活动界面，此时生成选项只会白白
    # 烧掉 summary 档 token。前端已在聊天窗口隐藏（voice-only / proactive
    # 路径）时跳过该请求；这里与 core.py 里 voice-proactive 的 _takeover_active
    # 守卫对称，确保异常 / 老版本客户端也无法绕过空耗 token。
    try:
        mgr = get_session_manager().get(lanlan_name)
    except Exception:
        mgr = None
    if mgr is not None and getattr(mgr, "_takeover_active", False):
        logger.info("GalGame options skipped: session takeover active")
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
            "reason": "session_takeover",
        })

    summary_config = config_manager.get_model_api_config('summary') or {}
    api_key = (summary_config.get('api_key') or '').strip()
    model = (summary_config.get('model') or '').strip()
    base_url = (summary_config.get('base_url') or '').strip()
    if not model or not base_url:
        logger.warning("Summary model/base_url not configured; returning fallback options")
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
        })

    system_prompt = get_galgame_option_generation_prompt(
        lang,
        lanlan_name=lanlan_name,
        master_name=master_name,
    )
    dialogue_block = "\n".join((
        get_galgame_dialogue_header(lang),
        _format_dialogue(messages, lanlan_name, master_name),
        get_galgame_dialogue_footer(lang),
    ))

    set_call_type("galgame_options")
    try:
        llm = await create_chat_llm_async(
            model,
            base_url,
            api_key,
            max_completion_tokens=GALGAME_OPTION_MAX_TOKENS,
            timeout=GALGAME_OPTION_TIMEOUT_SECONDS,
            provider_type=summary_config.get('provider_type'),
        )
        async with llm:
            result = await asyncio.wait_for(
                llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=dialogue_block),
                ]),
                timeout=GALGAME_OPTION_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        logger.warning("GalGame option generation timed out")
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
            "error": "timeout",
        })
    except Exception as exc:
        logger.warning("GalGame option generation failed: %s", exc)
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
            "error": str(exc),
        })

    raw_text = (getattr(result, 'content', '') or '').strip()
    cleaned = _strip_code_fence(raw_text)
    parsed_map: dict[str, str] = {}
    parse_error: str | None = None
    if cleaned:
        try:
            parsed = robust_json_loads(cleaned)
            parsed_map = _normalize_options(parsed)
        except Exception as exc:
            parse_error = type(exc).__name__

    if not parsed_map:
        # The raw output is generated from recent chat context and can carry
        # PII (names, personal disclosures). Keep INFO logs content-free —
        # only the parse-error class and total length — and stash the
        # truncated snippet under DEBUG so deeper diagnosis still works when
        # an operator deliberately opts into NEKO_LOG_LEVEL=DEBUG.
        logger.info(
            "GalGame model output unparseable, using fallback "
            "(parse_error=%s raw_len=%d)",
            parse_error, len(raw_text),
        )
        if logger.isEnabledFor(logging.DEBUG):
            snippet = re.sub(r'\s+', ' ', raw_text)[:200]
            logger.debug("GalGame unparseable raw_head: %r", snippet)
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
        })

    fallback_texts = get_galgame_fallback_options(lang)
    options: list[dict[str, str]] = []
    missing_labels: list[str] = []
    for label, fb_text in zip(GALGAME_OPTION_LABELS, fallback_texts):
        text = parsed_map.get(label)
        if text:
            options.append({'label': label, 'text': text})
        else:
            options.append({'label': label, 'text': fb_text})
            missing_labels.append(label)

    if missing_labels:
        logger.info(
            "GalGame partial parse: model returned %d/3 options; filled %s from fallback",
            3 - len(missing_labels), missing_labels,
        )
        return JSONResponse({
            "success": True,
            "options": options,
            "partial": True,
            "missing_labels": missing_labels,
        })

    return JSONResponse({"success": True, "options": options})
