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
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.prompts_galgame import (
    GALGAME_DEFAULT_LANLAN_PLACEHOLDER,
    GALGAME_DEFAULT_MASTER_PLACEHOLDER,
    get_galgame_fallback_options,
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
_LANLAN_FREE_API_KEY = "free-access"
_LANLAN_FREE_TEXT_HOSTS = {
    "www.lanlan.tech",
    "lanlan.tech",
    "www.lanlan.app",
    "lanlan.app",
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
    texts = get_galgame_fallback_options(lang)
    return [
        {'label': label, 'text': text}
        for label, text in zip(GALGAME_OPTION_LABELS, texts)
    ]


def _clean_llm_config(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    return {
        "api_key": (config.get("api_key") or "").strip(),
        "model": (config.get("model") or "").strip(),
        "base_url": (config.get("base_url") or "").strip(),
        "is_custom": bool(config.get("is_custom")),
    }


def _base_url_host(base_url: str) -> str:
    try:
        return (urlsplit(base_url).hostname or "").lower()
    except Exception:
        return ""


def _is_lanlan_free_text_config(config: dict[str, Any]) -> bool:
    return (
        config.get("api_key") == _LANLAN_FREE_API_KEY
        and not config.get("is_custom")
        and _base_url_host(config.get("base_url", "")) in _LANLAN_FREE_TEXT_HOSTS
    )


def _resolve_galgame_llm_config(config_manager: Any) -> tuple[dict[str, Any], str, bool]:
    summary_config = _clean_llm_config(config_manager.get_model_api_config('summary'))
    if _is_lanlan_free_text_config(summary_config):
        try:
            agent_config = _clean_llm_config(config_manager.get_model_api_config('agent'))
        except Exception as exc:
            logger.warning("GalGame free API agent config unavailable: %s", exc)
        else:
            if agent_config.get("model") and agent_config.get("base_url"):
                return agent_config, "agent", True
            logger.warning("GalGame free API agent model/base_url not configured")

    return summary_config, "summary", False


async def _consume_free_agent_quota(config_manager: Any) -> tuple[bool, dict[str, Any]]:
    consume_quota = getattr(config_manager, "aconsume_agent_daily_quota", None)
    if not callable(consume_quota):
        return True, {}
    try:
        return await consume_quota(source="galgame.options", units=1)
    except Exception as exc:
        logger.warning("GalGame free API quota check failed: %s", exc)
        return False, {"code": "AGENT_QUOTA_CHECK_FAILED"}


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

    llm_config, llm_config_source, uses_free_agent_quota = _resolve_galgame_llm_config(config_manager)
    api_key = llm_config["api_key"]
    model = llm_config["model"]
    base_url = llm_config["base_url"]
    if not model or not base_url:
        logger.warning("%s model/base_url not configured; returning fallback options", llm_config_source)
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
        })
    if uses_free_agent_quota:
        quota_ok, quota_info = await _consume_free_agent_quota(config_manager)
        if not quota_ok:
            return JSONResponse({
                "success": True,
                "options": _fallback_options(lang),
                "fallback": True,
                "error": "AGENT_QUOTA_EXCEEDED",
                "quota": quota_info,
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
        logger.info("GalGame model output unparseable, using fallback")
        return JSONResponse({
            "success": True,
            "options": _fallback_options(lang),
            "fallback": True,
        })

    return JSONResponse({"success": True, "options": options})
