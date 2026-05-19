# -*- coding: utf-8 -*-
"""
Card-Assist Router

Three endpoints powering the in-app AI assistant that helps users author a
catgirl character card (Character Card Manager → "AI 辅助生成" button):

  POST /api/card-assist/clarify   — return 2-4 chip-style clarifying questions
  POST /api/card-assist/generate  — return a full field dict (Chinese keys)
  POST /api/card-assist/refine    — regenerate a single field value

All three reuse the existing "assist API" provider (the user's configured
auxiliary LLM, falling back to the bundled free tier). Modeled on
``NEKO/memory/refine.py``'s ``create_chat_llm`` usage.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.prompts.prompts_card_assist import (
    get_card_assist_clarify_prompt,
    get_card_assist_generate_prompt,
    get_card_assist_refine_field_prompt,
)
from utils.language_utils import get_global_language
from utils.logger_config import get_module_logger

from .shared_state import get_config_manager

logger = get_module_logger(__name__, "CardAssist")

# Repo root for resolving `config/characters/<locale>.json` template paths.
REPO_ROOT = Path(__file__).resolve().parent.parent

router = APIRouter(prefix="/api/card-assist", tags=["card-assist"])


# Per-request timeout. Card assist is interactive — bail out fast so the
# user isn't staring at a spinner.
_LLM_TIMEOUT_SECONDS = 60.0


def _resolve_language(payload_locale: str | None) -> str:
    """Map a frontend locale (e.g. 'zh-CN', 'en-US') to the short prompt
    language code ('zh' / 'en'). Falls back to the global language setting.

    Prompt is currently only authored in zh & en; ja/ko/ru/pt/es get the en
    prompt (target field keys still pull the locale's own template — see
    `_resolve_locale_code` + `_load_template_keys_for_locale`)."""
    if payload_locale:
        code = payload_locale.strip().lower()
        if code.startswith("zh"):
            return "zh"
        if code.startswith("en"):
            return "en"
    try:
        glob = (get_global_language() or "").strip().lower()
        if glob.startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


# Locale tag → `config/characters/<file>.json` filename. Keep in sync with
# the files actually present in `config/characters/`.
_SUPPORTED_LOCALE_FILES = {
    "en": "en", "en-us": "en", "en-gb": "en",
    "zh-cn": "zh-CN", "zh-hans": "zh-CN", "zh": "zh-CN",
    "zh-tw": "zh-TW", "zh-hant": "zh-TW", "zh-hk": "zh-TW",
    "ja": "ja", "ja-jp": "ja",
    "ko": "ko", "ko-kr": "ko",
    "pt": "pt", "pt-br": "pt", "pt-pt": "pt",
    "ru": "ru", "ru-ru": "ru",
    "es": "es", "es-es": "es", "es-mx": "es",
}


def _resolve_locale_code(payload_locale: str | None) -> str:
    """Pick the closest matching `config/characters/<x>.json` filename for
    the payload locale. Falls back to the global language setting, then `en`.
    """
    if payload_locale:
        code = payload_locale.strip().lower()
        if code in _SUPPORTED_LOCALE_FILES:
            return _SUPPORTED_LOCALE_FILES[code]
        # primary subtag (e.g. "ja-JP" → "ja", "pt-BR" → "pt")
        primary = code.split("-", 1)[0]
        if primary in _SUPPORTED_LOCALE_FILES:
            return _SUPPORTED_LOCALE_FILES[primary]
    try:
        glob = (get_global_language() or "").strip().lower()
        if glob in _SUPPORTED_LOCALE_FILES:
            return _SUPPORTED_LOCALE_FILES[glob]
        primary = glob.split("-", 1)[0]
        if primary in _SUPPORTED_LOCALE_FILES:
            return _SUPPORTED_LOCALE_FILES[primary]
    except Exception:
        pass
    return "en"


def _strip_json_fence(raw: str) -> str:
    """LLMs love to wrap JSON in ```json ... ``` fences even when told not to.
    Strip them defensively before json.loads. Same approach as memory/refine.py.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    return text


def _build_assist_llm():
    """Construct an LLM client backed by the assist API config. Returns
    ``(llm, error_dict_or_None)``. Caller must ``await llm.aclose()`` if llm
    is not None.
    """
    from utils.llm_client import create_chat_llm
    try:
        cm = get_config_manager()
        api_cfg = cm.get_model_api_config("conversation")
    except Exception as exc:
        logger.warning("card-assist: failed to read assist API config: %s", exc)
        return None, {"success": False, "error": "assist_api_not_configured",
                      "message": str(exc)}
    api_key = (api_cfg or {}).get("api_key")
    model = (api_cfg or {}).get("model")
    base_url = (api_cfg or {}).get("base_url")
    if not model:
        return None, {"success": False, "error": "assist_api_not_configured",
                      "message": "assist model not set"}
    try:
        llm = create_chat_llm(
            model,
            base_url,
            api_key,
            timeout=_LLM_TIMEOUT_SECONDS,
            max_retries=1,
        )
    except Exception as exc:
        logger.warning("card-assist: create_chat_llm failed: %s", exc)
        return None, {"success": False, "error": "assist_api_init_failed",
                      "message": str(exc)}
    return llm, None


async def _invoke_assist(prompt: str) -> tuple[str | None, dict | None]:
    """Run a single-shot prompt against the assist LLM. Returns
    ``(content_or_None, error_dict_or_None)``.
    """
    llm, err = _build_assist_llm()
    if err is not None:
        return None, err
    try:
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
    except Exception as exc:
        logger.warning("card-assist: LLM ainvoke failed: %s", exc)
        return None, {"success": False, "error": "llm_call_failed",
                      "message": str(exc)}
    content = (getattr(resp, "content", None) or "").strip()
    if not content:
        return None, {"success": False, "error": "llm_empty_response"}
    return content, None


# 系统保留字段，对 LLM 来说都是噪声 / 不属于「角色设定」的部分：
#   - "档案名"：表单上的元数据 input 的固定 name（见 character_card_manager.js
#     里 `form.querySelector('input[name="档案名"]')`），是写死的中文 literal 而
#     非按 locale 翻译的字段，所以这里也用中文 literal。
#   - live2d / live3d / vrm / mmd：模型文件配置
#   - voice_id / model_type / system_prompt：运行时配置 / 系统提示词
# `_*` 前缀（如 `_reserved`）也一并跳过。
_RESERVED_CARD_FIELDS: frozenset[str] = frozenset({
    "档案名",
    "live2d", "live3d", "vrm", "mmd",
    "voice_id", "model_type", "system_prompt",
})


def _is_reserved_card_field(key: Any) -> bool:
    s = str(key)
    return s.startswith("_") or s in _RESERVED_CARD_FIELDS


def _format_card_for_prompt(card: Any, max_chars: int = 1200) -> str:
    """Render the existing card dict as compact JSON for prompt injection.
    Truncates very long cards so we don't blow the token budget."""
    if not isinstance(card, dict):
        return "{}"
    filtered = {k: v for k, v in card.items() if not _is_reserved_card_field(k)}
    try:
        text = json.dumps(filtered, ensure_ascii=False, indent=2)
    except Exception:
        text = str(filtered)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


# 不同 locale 的角色卡模板字段名不同（en 用 "Gender"/"Age"，zh-CN 用 "性别"/"年龄"，
# ja 用 "ニックネーム"/"性別" 等等）。前端走 textarea[name=...] 精确匹配应用生成
# 结果，prompt 必须告诉 LLM 使用这些真实 key，否则会以"新增字段"形式平行插入。
# 前端会把表单上看到的字段名一并发过来；空白新建卡的兜底从模板文件读取，硬
# 编码每个 locale 的字段表迟早会和 `config/characters/<x>.json` 漂移。

_HARDCODED_EN_FALLBACK = [
    "Nickname", "Gender", "Age", "Race", "Self-Reference",
    "Core Traits", "Behavioral Traits", "Dislikes", "Signature Line",
]


def _characters_template_path(locale_code: str) -> Path:
    return REPO_ROOT / "config" / "characters" / f"{locale_code}.json"


@lru_cache(maxsize=16)
def _load_template_keys_for_locale(locale_code: str) -> tuple[str, ...]:
    """Pull the field-name list out of `config/characters/<locale>.json` —
    structure is `{'猫娘': {<char_name>: {<field>: <value>, ...}}}`, take the
    first character's non-reserved keys in order. Returns empty tuple on any
    failure (missing file / corrupted JSON / unexpected shape); caller falls
    back to the hardcoded en list.
    """
    p = _characters_template_path(locale_code)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("card-assist: failed to load template %s: %s", p, exc)
        return ()
    girls = data.get("猫娘") if isinstance(data, dict) else None
    if not isinstance(girls, dict) or not girls:
        return ()
    first = next(iter(girls.values()), None)
    if not isinstance(first, dict):
        return ()
    keys = [
        str(k) for k in first.keys()
        if str(k).strip() and not _is_reserved_card_field(k)
    ]
    return tuple(keys)


def _resolve_target_keys(payload: Dict[str, Any], locale_code: str,
                         current_card: Any) -> list[str]:
    """Return the field-key list the LLM must use, in priority order:
    1) explicit payload["target_field_keys"] from the frontend (truthy strings only)
    2) keys present in the existing card (less reliable for empty new-card forms)
    3) locale template's field names (read from config/characters/<locale>.json)
    4) hardcoded en fallback (last resort if the template file is missing/broken).
    """
    raw = payload.get("target_field_keys")
    if isinstance(raw, list) and raw:
        keys = [str(x).strip() for x in raw
                if str(x).strip() and not _is_reserved_card_field(x)]
        if keys:
            return keys
    if isinstance(current_card, dict) and current_card:
        keys = [
            str(k).strip()
            for k in current_card.keys()
            if str(k).strip() and not _is_reserved_card_field(k)
        ]
        if keys:
            return keys
    tmpl_keys = _load_template_keys_for_locale(locale_code)
    if tmpl_keys:
        return list(tmpl_keys)
    return list(_HARDCODED_EN_FALLBACK)


@router.post("/clarify")
async def clarify(request: Request):
    """Step 1: given a one-line description, return 2-4 chip-style questions."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)

    description = str(body.get("description") or "").strip()
    if not description:
        return JSONResponse({"success": False, "error": "description_required"},
                            status_code=400)

    lang = _resolve_language(body.get("locale"))
    current_card_text = _format_card_for_prompt(body.get("current_card"))

    template = get_card_assist_clarify_prompt(lang)
    prompt = template % (description, current_card_text)

    content, err = await _invoke_assist(prompt)
    if err is not None:
        return JSONResponse(err, status_code=502 if err.get("error") == "llm_call_failed" else 400)

    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as exc:
        logger.warning("card-assist/clarify: bad JSON from LLM: %s; raw[:200]=%s",
                       exc, content[:200])
        return JSONResponse({"success": False, "error": "llm_bad_json",
                             "raw": content[:500]}, status_code=502)

    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list) or not questions:
        return JSONResponse({"success": False, "error": "llm_bad_shape",
                             "raw": content[:500]}, status_code=502)

    # Normalize: clamp options, fill missing flags.
    # NOTE: do not name the loop var `q` — the async-blocking linter heuristically
    # flags `q.get(...)` as a queue.Queue.get() call and fails CI.
    normalized = []
    for idx, qd in enumerate(questions[:4]):
        if not isinstance(qd, dict):
            continue
        qid = str(qd.get("id") or f"q{idx+1}").strip() or f"q{idx+1}"
        label = str(qd.get("label") or "").strip()
        if not label:
            continue
        header = str(qd.get("header") or label[:8]).strip()
        opts = qd.get("options") or []
        if not isinstance(opts, list):
            opts = []
        clean_opts = [str(o).strip() for o in opts if str(o).strip()][:4]
        allow_custom = bool(qd.get("allowCustom", True))
        normalized.append({
            "id": qid,
            "header": header,
            "label": label,
            "options": clean_opts,
            "allowCustom": allow_custom,
        })

    if not normalized:
        return JSONResponse({"success": False, "error": "llm_no_usable_questions",
                             "raw": content[:500]}, status_code=502)

    return JSONResponse({"success": True, "questions": normalized})


@router.post("/generate")
async def generate(request: Request):
    """Step 2: given description + answers, return the full field set."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)

    description = str(body.get("description") or "").strip()
    if not description:
        return JSONResponse({"success": False, "error": "description_required"},
                            status_code=400)

    answers = body.get("answers") or {}
    if not isinstance(answers, dict):
        answers = {}

    lang = _resolve_language(body.get("locale"))
    locale_code = _resolve_locale_code(body.get("locale"))
    current_card = body.get("current_card")
    current_card_text = _format_card_for_prompt(current_card)
    try:
        answers_text = json.dumps(answers, ensure_ascii=False, indent=2)
    except Exception:
        answers_text = str(answers)
    target_keys = _resolve_target_keys(body, locale_code, current_card)
    target_keys_text = " / ".join(target_keys)

    template = get_card_assist_generate_prompt(lang)
    prompt = template % (description, answers_text, current_card_text,
                         target_keys_text)

    content, err = await _invoke_assist(prompt)
    if err is not None:
        return JSONResponse(err, status_code=502 if err.get("error") == "llm_call_failed" else 400)

    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as exc:
        logger.warning("card-assist/generate: bad JSON from LLM: %s; raw[:200]=%s",
                       exc, content[:200])
        return JSONResponse({"success": False, "error": "llm_bad_json",
                             "raw": content[:500]}, status_code=502)

    fields = parsed.get("fields") if isinstance(parsed, dict) else None
    if not isinstance(fields, dict) or not fields:
        return JSONResponse({"success": False, "error": "llm_bad_shape",
                             "raw": content[:500]}, status_code=502)

    # Coerce every value to a non-empty string; drop empties.
    cleaned: Dict[str, str] = {}
    for k, v in fields.items():
        key = str(k).strip()
        if not key or key.startswith("_"):
            continue
        if isinstance(v, (list, tuple)):
            val = ", ".join(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, dict):
            try:
                val = json.dumps(v, ensure_ascii=False)
            except Exception:
                val = str(v)
        elif v is None:
            val = ""
        else:
            val = str(v).strip()
        if val:
            cleaned[key] = val

    if not cleaned:
        return JSONResponse({"success": False, "error": "llm_no_usable_fields",
                             "raw": content[:500]}, status_code=502)

    return JSONResponse({"success": True, "fields": cleaned})


@router.post("/refine")
async def refine(request: Request):
    """Step 3: regenerate a single field's value given an adjustment instruction."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)

    field_key = str(body.get("field_key") or "").strip()
    if not field_key:
        return JSONResponse({"success": False, "error": "field_key_required"},
                            status_code=400)
    instruction = str(body.get("instruction") or "").strip()
    if not instruction:
        return JSONResponse({"success": False, "error": "instruction_required"},
                            status_code=400)

    lang = _resolve_language(body.get("locale"))
    current_card = body.get("current_card") or {}
    current_value = ""
    if isinstance(current_card, dict):
        current_value = str(current_card.get(field_key) or "")
    card_text = _format_card_for_prompt(current_card)

    template = get_card_assist_refine_field_prompt(lang)
    prompt = template % (card_text, field_key, current_value, instruction)

    content, err = await _invoke_assist(prompt)
    if err is not None:
        return JSONResponse(err, status_code=502 if err.get("error") == "llm_call_failed" else 400)

    # The refine prompt asks for a plain string. Strip code fences and surrounding
    # quotes if the LLM wrapped it anyway. Unicode left/right quotes are *different*
    # codepoints, so equality won't catch the common `“…”` / `‘…’` pairings — use
    # an explicit open→close map.
    text = _strip_json_fence(content).strip()
    _QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’",
                    "「": "」", "『": "』"}
    if len(text) >= 2 and _QUOTE_PAIRS.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    if not text:
        return JSONResponse({"success": False, "error": "llm_empty_response"},
                            status_code=502)

    return JSONResponse({"success": True, "field_key": field_key, "value": text})
