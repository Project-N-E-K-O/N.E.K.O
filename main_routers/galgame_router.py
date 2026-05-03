# -*- coding: utf-8 -*-
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

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.prompts_galgame import (
    GALGAME_DEFAULT_LANLAN_PLACEHOLDER,
    GALGAME_DEFAULT_MASTER_PLACEHOLDER,
    get_galgame_dialogue_footer,
    get_galgame_dialogue_header,
    get_galgame_option_generation_prompt,
)
from config.prompts_sys import _loc
from utils.file_utils import robust_json_loads
from utils.language_utils import detect_language, normalize_language_code
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

from .shared_state import get_config_manager

router = APIRouter(prefix="/api", tags=["galgame"])

logger = get_module_logger(__name__, "GalGame")

GALGAME_MAX_HISTORY = 8
GALGAME_MAX_TEXT_PER_TURN = 240
GALGAME_OPTION_MAX_TOKENS = 360
GALGAME_OPTION_LABELS = ("A", "B", "C")
GALGAME_FALLBACK_BY_LANG = {
    "zh": (
        "我有点没听清，可以再说一次吗？",
        "嗯嗯，我都在听，慢慢说就好。",
        "如果我们现在掉进童话书里会怎样？",
    ),
    "en": (
        "Could you walk me through that again?",
        "I'm right here. Take your time, I'm listening.",
        "What if we slipped into a storybook right now?",
    ),
    "ja": (
        "もう一度ゆっくり説明してくれる？",
        "ここにいるよ。ゆっくりで大丈夫。",
        "今の話、もし絵本の中に紛れ込んだらどうする？",
    ),
    "ko": (
        "한 번만 더 천천히 말해줄래?",
        "여기 있어, 천천히 말해도 괜찮아.",
        "우리가 지금 동화책 속으로 들어갔다면 어떨까?",
    ),
    "ru": (
        "Можешь повторить ещё раз помедленнее?",
        "Я рядом, не торопись, я слушаю.",
        "А если бы мы сейчас провалились в сказку?",
    ),
}


def _resolve_language(text_sample: str, request_lang: str | None) -> str:
    """Pick the best 'short' language code for the prompt."""
    if request_lang:
        try:
            return normalize_language_code(request_lang, format='short') or 'zh'
        except Exception:
            # Bad language tag from the client — fall through to text-based detection.
            pass
    try:
        if text_sample.strip():
            return normalize_language_code(detect_language(text_sample), format='short') or 'zh'
    except Exception:
        # detect_language can choke on emoji-only / very short strings — default to zh.
        pass
    return 'zh'


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


def _normalize_options(parsed: Any) -> list[dict[str, str]]:
    if isinstance(parsed, dict):
        candidates = parsed.get('options') or parsed.get('candidates') or parsed.get('replies')
    else:
        candidates = parsed
    if not isinstance(candidates, list):
        return []

    by_label: dict[str, str] = {}
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
        if normalized_label in GALGAME_OPTION_LABELS and normalized_label not in by_label:
            by_label[normalized_label] = text
        else:
            leftover.append(text)

    options: list[dict[str, str]] = []
    for label in GALGAME_OPTION_LABELS:
        text = by_label.get(label)
        if text is None and leftover:
            text = leftover.pop(0)
        if text is None:
            return []
        options.append({'label': label, 'text': text})
    return options


def _fallback_options(lang: str) -> list[dict[str, str]]:
    texts = GALGAME_FALLBACK_BY_LANG.get(lang) or GALGAME_FALLBACK_BY_LANG['en']
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

    summary_config = config_manager.get_model_api_config('summary')
    api_key = summary_config.get('api_key')
    model = summary_config.get('model')
    base_url = summary_config.get('base_url')
    if not api_key or not model:
        logger.warning("Summary model not configured; returning fallback options")
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
    llm = create_chat_llm(
        model,
        base_url,
        api_key,
        temperature=0.85,
        max_completion_tokens=GALGAME_OPTION_MAX_TOKENS,
    )
    try:
        async with llm:
            result = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=dialogue_block),
            ])
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
    options: list[dict[str, str]] = []
    if cleaned:
        try:
            parsed = robust_json_loads(cleaned)
            options = _normalize_options(parsed)
        except Exception:
            options = []
    if not options:
        logger.info("GalGame model output unparseable, using fallback. raw=%r", raw_text[:200])
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
        })

    return JSONResponse({"success": True, "options": options})
