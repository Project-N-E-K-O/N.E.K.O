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
Card-Assist Router

Four endpoints powering the in-app AI assistant that helps users author a
catgirl character card (Character Card Manager -> "AI-assisted generation"
button):

  POST /api/card-assist/clarify   — return 2-4 chip-style clarifying questions
  POST /api/card-assist/generate  — return a full field dict (Chinese keys)
  POST /api/card-assist/refine    — regenerate a single field value
  POST /api/card-assist/chat      — persistent companion chat + edit actions

All four reuse the existing "agent API" provider so the bundled free path uses
``free-agent-model`` and the agent URL normalization in ``ConfigManager``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import CHARACTER_RESERVED_FIELDS
from config.prompts.prompts_card_assist import (
    get_card_assist_chat_advice_only_directive,
    get_card_assist_chat_empty_reply_fallback,
    get_card_assist_chat_system_prompt,
    get_card_assist_clarify_prompt,
    get_card_assist_generate_prompt,
    get_card_assist_refine_field_prompt,
    normalize_card_assist_locale,
)
from utils.language_utils import get_global_language_full
from utils.logger_config import get_module_logger

from .shared_state import get_config_manager
# Shared local request guard (issue #1479). system_router does not depend back on card_assist, avoiding circular imports.
from .system_router import _validate_local_mutation_request

logger = get_module_logger(__name__, "CardAssist")


def _reject_untrusted_card_assist(request: Request, payload: Any) -> JSONResponse | None:
    """Local Origin/CSRF guard: all four card-assist POSTs actually call the user's
    configured agent LLM and consume its API / free quota, making them
    "browser-side requests with side effects". Like every other such endpoint in
    the repo, they must pass the unified guard first to block malicious pages
    from forging legitimate-looking JSON via ``no-cors`` + ``text/plain`` bodies
    to burn quota — the attacker cannot read the response, but without the guard
    they could still freeload the quota (Codex #3328998416).

    Reuses ``_validate_local_mutation_request``: ``None`` means allow; a 403
    JSONResponse(``error_code=csrf_validation_failed``) means reject, and the
    caller should return it as-is. ``payload`` is only used for the in-body
    ``_csrf_token`` fallback; pass None for non-dict payloads to avoid ``.get`` errors."""
    return _validate_local_mutation_request(
        request,
        payload=payload if isinstance(payload, dict) else None,
    )

# Repo root for resolving `config/characters/<locale>.json` template paths.
REPO_ROOT = Path(__file__).resolve().parent.parent

router = APIRouter(prefix="/api/card-assist", tags=["card-assist"])


# Per-request timeout. Card assist is interactive — bail out fast so the
# user isn't staring at a spinner.
_LLM_TIMEOUT_SECONDS = 60.0
_ACTION_RECOVERY_SPLIT_MAX_FIELDS = 32

def _resolve_language(payload_locale: str | None) -> str:
    """Map a frontend locale (e.g. 'zh-CN', 'en-US') to a card-assist prompt
    language key ('zh' / 'zh-TW' / 'en'). Falls back to the global language setting.

    Prompt is currently authored in zh, zh-TW & en; ja/ko/ru/pt/es get the en
    prompt (target field keys still pull the locale's own template — see
    `_resolve_locale_code` + `_load_template_keys_for_locale`).

    The global fallback reads `get_global_language_full()`, not the short getter:
    the short one collapses Traditional to 'zh', which would leave every 'zh-TW'
    template in `prompts_card_assist` unreachable for a Traditional user who did
    not send a locale in the payload (issue #2500 step 2)."""
    if payload_locale:
        code = payload_locale.strip().lower()
        if code.startswith("zh"):
            return normalize_card_assist_locale(code)
        if code.startswith("en"):
            return "en"
    try:
        glob = (get_global_language_full() or "").strip().lower()
        if glob.startswith("zh"):
            return normalize_card_assist_locale(glob)
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

    Like `_resolve_language`, the global fallback has to be the full code: the
    short getter answers 'zh' for a Traditional user, which maps to the
    Simplified `zh-CN.json` template and skips the `zh-TW` output-language
    directive below (issue #2500 step 2).
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
        glob = (get_global_language_full() or "").strip().lower()
        if glob in _SUPPORTED_LOCALE_FILES:
            return _SUPPORTED_LOCALE_FILES[glob]
        primary = glob.split("-", 1)[0]
        if primary in _SUPPORTED_LOCALE_FILES:
            return _SUPPORTED_LOCALE_FILES[primary]
    except Exception:
        pass
    return "en"


# `_resolve_locale_code` output (character-card template filename) -> (English name, native name). Prompts currently have
# zh / zh-TW / en versions (see _resolve_language), with ja/ko/ru/pt/es falling back to en. Without an explicit output-language
# instruction, the assistant asks questions and fills field values in English for those locales (Codex #3331696257).
# Add an output-language instruction for them. en / zh-CN match the base prompt and are absent from this table
# (returning an empty instruction). zh-TW remains here despite having its own prompt: the base prompt controls
# how the assistant thinks, while this instruction controls the script used for field values; neither replaces the other.
_LOCALE_OUTPUT_LANGUAGE: dict[str, tuple[str, str]] = {
    "zh-TW": ("Traditional Chinese", "繁體中文"),
    "ja": ("Japanese", "日本語"),
    "ko": ("Korean", "한국어"),
    "pt": ("Portuguese", "Português"),
    "ru": ("Russian", "Русский"),
    "es": ("Spanish", "Español"),
}


def _output_language_directive(locale_code: str) -> str:
    """For locales without a dedicated prompt version, generate an explicit output-language
    directive appended to the end of the prompt. Field keys are already fixed by
    _resolve_target_keys per the locale template; this only constrains values / questions /
    descriptions to the target language. en / zh-CN match the base prompt -> return an empty
    string and add nothing."""
    pair = _LOCALE_OUTPUT_LANGUAGE.get(locale_code)
    if not pair:
        return ""
    name, native = pair
    return (
        f"\n\n[OUTPUT LANGUAGE] Respond entirely in {name}（{native}）. Every question, "
        f"field value, and explanation you produce MUST be written in {name}; do NOT use "
        f"English or Simplified Chinese for any user-facing text. Keep the JSON structure "
        f"and the field keys exactly as specified."
    )


def _strip_json_fence(raw: str) -> str:
    """LLMs love to wrap JSON in ```json ... ``` fences even when told not to.
    Strip them defensively before json.loads. Same approach as memory/refine.py.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    return text


def _extract_first_json_object(raw: str) -> str | None:
    """Return the first decodable JSON object embedded in raw LLM text.

    Weak/free models often wrap the required object in chatty prose. Use
    JSONDecoder rather than brace counting so strings/escapes are handled by
    the standard parser.
    """
    text = _strip_json_fence(raw)
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return text[idx:idx + end].strip()
    return None


def _loads_json_lenient(raw: str) -> Any:
    """Parse strict JSON first; if that fails, parse an embedded object."""
    text = _strip_json_fence(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_first_json_object(text)
        if not extracted:
            raise
        return json.loads(extracted)


_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
}


def _clean_plain_field_value(raw: str) -> str:
    """Normalize a single-field plain-string LLM response."""
    text = _strip_json_fence(raw).strip()
    if len(text) >= 2 and _QUOTE_PAIRS.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return text


async def _build_assist_llm():
    """Construct an LLM client backed by the agent API config. Returns
    ``(llm, error_dict_or_None)``. Caller must ``await llm.aclose()`` if llm is
    not None.
    """
    from utils.llm_client import create_chat_llm_async
    try:
        cm = get_config_manager()
        api_cfg = await cm.aget_model_api_config("agent")
    except Exception as exc:
        logger.warning("card-assist: failed to read agent API config: %s", exc)
        return None, {"success": False, "error": "assist_api_not_configured",
                      "message": str(exc)}
    api_key = (api_cfg or {}).get("api_key")
    model = (api_cfg or {}).get("model")
    base_url = (api_cfg or {}).get("base_url")
    if not model:
        return None, {"success": False, "error": "assist_api_not_configured",
                      "message": "agent model not set"}
    try:
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            model,
            base_url,
            api_key,
            provider_type=(api_cfg or {}).get("provider_type"),
            timeout=_LLM_TIMEOUT_SECONDS,
            max_retries=1,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length structured suggestions aren't truncated
        )
    except Exception as exc:
        logger.warning("card-assist: create_chat_llm failed: %s", exc)
        return None, {"success": False, "error": "assist_api_init_failed",
                      "message": str(exc)}
    return llm, None


async def _reserve_agent_quota(source: str) -> dict | None:
    """Reserve one local free-agent quota unit before an actual LLM call."""
    try:
        ok, info = await get_config_manager().aconsume_agent_daily_quota(
            source=source,
            units=1,
        )
    except Exception as exc:
        logger.warning("card-assist: agent quota check failed: %s", exc)
        return {"success": False, "error": "agent_quota_check_failed",
                "message": str(exc)}
    if ok:
        return None
    used = info.get("used", 0)
    limit = info.get("limit", 500)
    return {
        "success": False,
        "error": "AGENT_QUOTA_EXCEEDED",
        "code": "AGENT_QUOTA_EXCEEDED",
        "message": "agent quota exceeded",
        "details": {"used": used, "limit": limit},
    }


async def _invoke_assist_detailed(prompt: Any) -> tuple[str | None, dict | None]:
    """Run a single-shot call against the card-assist LLM. ``prompt`` may be either
    a plain string (treated as one user message) or a list of OpenAI-style
    role/content dicts. Returns ``(content_or_None, error_dict_or_None)``.
    """
    llm, err = await _build_assist_llm()
    if err is not None:
        return None, err
    quota_err = await _reserve_agent_quota("card_assist.invoke")
    if quota_err is not None:
        try:
            await llm.aclose()
        except Exception as close_exc:
            logger.warning("card-assist: LLM aclose after quota failure: %s",
                           close_exc)
        return None, quota_err
    # Handle ainvoke / aclose errors separately, or an aclose error
    # discards an already obtained resp as llm_call_failed.
    try:
        resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt is the user's own card draft (user-provided config — uncapped by design, cf. llm-prompt-budget.md §6).
    except Exception as exc:
        logger.warning("card-assist: LLM ainvoke failed: %s", exc)
        try:
            await llm.aclose()
        except Exception as close_exc:
            logger.warning("card-assist: LLM aclose after ainvoke failure: %s",
                           close_exc)
        return None, {"success": False, "error": "llm_call_failed",
                      "message": str(exc)}
    try:
        await llm.aclose()
    except Exception as close_exc:
        # An aclose failure must not affect this result; the next request gets a new client.
        logger.warning("card-assist: LLM aclose failed (ignored): %s", close_exc)
    content = (getattr(resp, "content", None) or "").strip()
    if not content:
        return None, {"success": False, "error": "llm_empty_response"}
    return content, None


async def _invoke_assist(prompt: Any) -> tuple[str | None, dict | None]:
    content, err = await _invoke_assist_detailed(prompt)
    return content, err


# System-reserved fields are noise for the LLM / outside the character definition.
# ⚠ Reuse the shared CHARACTER_RESERVED_FIELDS (also used by the character editor and backend save filter
# `_filter_mutable_catgirl_fields`); do not maintain a drifting partial copy, or keys such as
# `lighting` / `live3d_sub_type` / `vrm_animation` / `live2d_idle_animation`
# render as ordinary fields in chat/add_field and autosave reports success, yet saving filters them out; rows vanish on refresh,
# silently losing user edits (Codex #3331668038). Add two card-assist-specific entries beyond the shared list:
#   - "档案名": the metadata input's fixed name (a hardcoded Chinese literal, not localized),
#     absent from character reserved-field settings, but still unsuitable for AI edits as an ordinary definition.
#   - "live3d": a bare key retained by the old local list (the shared config only has "live3d_sub_type"); keep it conservatively.
# Also skip the `_*` prefix (such as `_reserved`).
_RESERVED_CARD_FIELDS: frozenset[str] = frozenset(CHARACTER_RESERVED_FIELDS) | {
    "档案名", "live3d",
}


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


# Character-card template field names vary by locale (en: "Gender"/"Age"; zh-CN: "性别"/"年龄";
# ja: "ニックネーム"/"性別", etc.). The frontend applies generated results via exact textarea[name=...] matches,
# so the prompt must give the LLM the real keys, or it inserts parallel new fields instead.
# The frontend sends the field names visible in the form; blank-card fallback reads the template file, since
# hardcoded per-locale field tables would eventually drift from `config/characters/<x>.json`.

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
    """  # noqa: DOCSTRING_CJK
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
        body: Any = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)
    # request.json() accepts **any valid JSON** (list / str / int / null),
    # but every body.get(...) below assumes a dict. Reject non-objects with 400 instead of
    # letting AttributeError become 500.
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "invalid_json",
                             "message": "JSON body must be an object"}, status_code=400)

    rejected = _reject_untrusted_card_assist(request, body)
    if rejected is not None:
        return rejected

    description = str(body.get("description") or "").strip()
    if not description:
        return JSONResponse({"success": False, "error": "description_required"},
                            status_code=400)

    lang = _resolve_language(body.get("locale"))
    current_card_text = _format_card_for_prompt(body.get("current_card"))

    template = get_card_assist_clarify_prompt(lang)
    prompt = template % (description, current_card_text)
    # When ja/ko/ru/pt/es/zh-TW fall back to English / Simplified Chinese prompts, explicitly request questions in the target language (Codex #3331696257).
    prompt += _output_language_directive(_resolve_locale_code(body.get("locale")))

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
        body: Any = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)
    # request.json() accepts **any valid JSON** (list / str / int / null),
    # but every body.get(...) below assumes a dict. Reject non-objects with 400 instead of
    # letting AttributeError become 500.
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "invalid_json",
                             "message": "JSON body must be an object"}, status_code=400)

    rejected = _reject_untrusted_card_assist(request, body)
    if rejected is not None:
        return rejected

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
    # Field keys already follow locale templates; require field values in the target language too (Codex #3331696257).
    prompt += _output_language_directive(locale_code)

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
    # Also block reserved fields the model may accidentally return ("档案名"/"voice_id"/...), or frontend
    # textarea[name=] writeback contaminates metadata / runtime settings rather than ordinary character definitions.
    cleaned: Dict[str, str] = {}
    for k, v in fields.items():
        key = str(k).strip()
        if not key or _is_reserved_card_field(key):
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
        body: Any = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)
    # request.json() accepts **any valid JSON** (list / str / int / null),
    # but every body.get(...) below assumes a dict. Reject non-objects with 400 instead of
    # letting AttributeError become 500.
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "invalid_json",
                             "message": "JSON body must be an object"}, status_code=400)

    rejected = _reject_untrusted_card_assist(request, body)
    if rejected is not None:
        return rejected

    field_key = str(body.get("field_key") or "").strip()
    if not field_key:
        return JSONResponse({"success": False, "error": "field_key_required"},
                            status_code=400)
    # field_key comes directly from the request body; keep sanitization consistent with _format_card_for_prompt / generate
    # so clients cannot bypass it to refine "档案名"/"voice_id"/"system_prompt".
    if _is_reserved_card_field(field_key):
        return JSONResponse({"success": False, "error": "field_key_reserved",
                             "message": f"field_key '{field_key}' is reserved"},
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
    # Regenerated field values also use the target language (Codex #3331696257).
    prompt += _output_language_directive(_resolve_locale_code(body.get("locale")))

    content, err = await _invoke_assist(prompt)
    if err is not None:
        return JSONResponse(err, status_code=502 if err.get("error") == "llm_call_failed" else 400)

    # The refine prompt asks for a plain string. Strip code fences and surrounding
    # quotes if the LLM wrapped it anyway.
    text = _clean_plain_field_value(content)
    if not text:
        return JSONResponse({"success": False, "error": "llm_empty_response"},
                            status_code=502)

    return JSONResponse({"success": True, "field_key": field_key, "value": text})


# ============================================================================
# /chat -- persistent companion-chat endpoint.
#
# Unlike the one-shot wizard flow in clarify/generate/refine, /chat maintains a conversation:
# the frontend sends messages history + current card state + available field keys; the LLM acts as
# the character-definition assistant catgirl (YUI by default, later the developer cat), replying to the user and
# emitting structured actions for the frontend form when needed.
# ============================================================================

# Roles allowed in client history; exclude all others (system / tool / function),
# always reconstructing the system prompt on the backend from current card state.
_CHAT_HISTORY_ROLES = frozenset({"user", "assistant"})

# History-turn cap. Keep only the tail of long chats to avoid exhausting context budgets.
_CHAT_MAX_HISTORY_MESSAGES = 20

# Per-message character cap. Character-card fields accompany the prompt, so cap individual
# conversation messages too, reserving budget for system + card.
_CHAT_MAX_MESSAGE_CHARS = 2000

# Maximum actions accepted per response. This guards against dozens of LLM actions overwriting user definitions,
# but must accommodate a reasonable full rewrite: the default template already has nine visible fields
# (nickname/gender/age/species/self-reference/core traits/behavior/dislikes/one-line quote), plus user-created custom
# fields. A rewrite-all quick action returns one refine_field per field. The old cap of eight silently dropped
# the ninth and later actions, autosaving only half a card (Codex #3328971304). Raise to 32: enough for
# the default nine fields plus ample custom fields, while still blocking genuinely runaway action lists.
_CHAT_MAX_ACTIONS = 32

# Field value length cap (refine_field / add_field), roughly matching manually authored
# definition lengths in the template.
_CHAT_MAX_FIELD_VALUE_CHARS = 800

_VALID_ACTION_TYPES = frozenset({"refine_field", "add_field", "remove_field"})

# Default placeholder name for the developer cat, overridable via payload.dev_cat_name. Once the real
# developer-cat character is ready, the frontend will send its name.
_DEFAULT_DEV_CAT_NAME = "YUI"

# ⚠ These four regexes match **actual user input**, so include both Simplified and Traditional forms of every Chinese word.
# The feature itself is localized for Traditional Chinese (zh-TW exists in both _SUPPORTED_LOCALE_FILES /
# _LOCALE_OUTPUT_LANGUAGE), so users really will type Traditional Chinese.
#
# ⚠⚠ EDIT and ADVICE_ONLY vocabulary must be added **in pairs**: _chat_text_requests_advice_only is
# advice AND NOT direct-edit, while :1119 uses
# `edit_intent = False if advice_only else ...`. Updating just one reverses the inversion rather than
# fixing it: Traditional Chinese "給我一些修改建議" previously gave edit=True/advice=False ("修改" is shared,
# while "建议/建議" differ), directly rewriting the user's card instead of offering advice.
# Taiwan terminology: use "欄位" rather than "字段" for field.
_CHAT_EDIT_INTENT_RE = re.compile(
    r"(修改|改写|改寫|重写|重寫|重生|重做|重新写|重新寫|调整|調整|补充|補充|新增|添加|"
    r"删除|刪除|移除|换一|換一|换成|換成|优化|優化|完善|梳理|设定|設定|字段|欄位|"
    r"rewrite|revise|regenerate|refine|update|change|"
    r"edit|add|remove|delete|replace|make\s+her|make\s+him)",
    re.IGNORECASE,
)

# ── Three tables for the whole-card target + 的 + ... escape hatch ──────────────────────────
# ⚠ Extract constants instead of embedding regex literals: tests take their Cartesian product; with literals,
# tests must scrape regex source and any structural regex change causes collection errors.
#
# Universal quantifiers. A closed set (these are all Chinese universal quantifiers).
# ⚠ Include **all** of them. The previous version had only 每个/每個, omitting 每一个/每一個/每項/每项/各項/各项;
# `把整個卡的每一個欄位都重寫` is an explicit whole-card request. Blocking it prevented whole-card completion
# and persisted only some fields (Codex P2).
# ⚠⚠ **Use two quantifier tables**. Round 48 added `每一项` thinking only of
# `把所有字段里的每一项内容重写` (quantifier + scope noun, base=True), but this also made
# `整个卡的每一项` itself a whole-card target, a form where base=False.
# This produced three P1s in rounds 57-58: `最后第2段` bypassed the attributive guard;
# `根据整个卡的每一项重写名字` edited only the name yet completed the whole card; `我不认为需要重写整个卡的
# 每一项` explicitly negated rewriting but still edited. All three overwrite data.
#
# Fix the source rather than adding three downstream guards: `每一项` counts only **when followed by a scope noun**,
# never as a head by itself. The table below is the full set for quantifier + scope noun;
# `_WHOLE_CARD_BARE_QUANTIFIERS` is the narrower set for quantifiers acting as heads themselves.
_WHOLE_CARD_QUANTIFIERS = (
    "全部", "所有", "每一个", "每一個", "每个", "每個",
    "每一项", "每一項", "每项", "每項", "各项", "各項", "一切",
)
# **Whole-card nouns** governed by the quantifier.
#
# ⚠ A quantifier alone is insufficient. In `把整个卡的全部名字重写` / `把整个卡的所有昵称重写`,
# the quantifier governs a **single field name**. The user wants only that field edited, but whole-card classification
# causes `_complete_full_rewrite_actions` to synthesize and autosave contents for every other field,
# silently overwriting data the user never mentioned (Codex P1, base=False).
#
# ⚠ Cannot denylist field names: beyond `_is_reserved_card_field`, user-created names form
# an open set impossible to exhaust. Positively require the quantifier to govern a whole-card noun instead.
# Whole-card nouns are also an open set, but costs differ: missing one only skips completion once (the user
# can ask again), while admitting a field name overwrites user data. Enumerate conservatively.
_WHOLE_CARD_SCOPE_NOUNS = (
    "设定", "設定", "设置", "設置", "资料", "資料", "人设", "人設",
    "描述", "内容", "內容", "字段", "欄位", "栏位", "数据", "數據",
    "文本", "文字", "文案",
    "信息", "資訊", "资讯", "属性", "屬性", "项目", "項目",
    "条目", "條目", "细节", "細節", "部分", "东西", "東西",
)
# Qualifiers allowed before whole-card nouns.
# ⚠ The alternative above already treated `所有可见字段` / `所有可見欄位` as whole-card targets, but the
# escape hatch missed `的每个可见字段`, rejecting `把整个卡的每个可见字段重写`
# (Codex P2, base=True). Use an optional prefix rather than adding four composite nouns:
# it applies to every noun in the table (可見設定 / 可见内容 ...).
# ⚠ Scope-noun qualifiers include more than "可见": `所有现有字段` / `全部已有字段`
# both have base=True (Codex P2 round 49). This family describes existing/visible attributes,
# without changing the fact that the scope covers the whole card.
# ⚠ Quantifiers that **can act as heads themselves**: `把整个卡的全部重写` has base=True,
# but `把整个卡的每一项重写` has base=False: `每一项` denotes each individual item,
# naming items rather than the whole card. Exclude all itemwise forms (每个/每项/每一个/每一项/各项/逐一...)
# from this narrow table.
# ⚠⚠ **`每一项` is a whole-card target introduced by this PR; base never recognized it.**
# Base has no such table (this PR extracted it from an inline regex). Differential checks show:
# all universal forms (全部/所有/一切) have base=True; itemwise 每个/每一个/每项/各项
# also have base=True; **only `每一项`/`每一項` have base=False**.
#
# Round 58 removed it from the bare-head branch, but left it in quantifier + scope
# noun, making `整个卡的每一项内容` a whole-card target absent from base.
# All **seven P1s** reported in rounds 68/69/70 use this target: wh-heads,
# trailing negation, prepositional references, modal A-not-A, deferred confirmation, and completed-action reports
# all use it as their example. Removing the source eliminates them together.
#
# ⚠ Remove only these two terms, not the whole itemwise family: 264 tested itemwise combinations have base=True;
# removing them wholesale would reject all of those.
_WHOLE_CARD_PR_ONLY_QUANTIFIERS = ("每一项", "每一項")
_WHOLE_CARD_SCOPED_QUANTIFIERS = tuple(
    word for word in _WHOLE_CARD_QUANTIFIERS
    if word not in _WHOLE_CARD_PR_ONLY_QUANTIFIERS
)
_WHOLE_CARD_BARE_QUANTIFIERS = tuple(
    word for word in _WHOLE_CARD_QUANTIFIERS
    if not word.startswith(("每", "各", "逐"))
)
_WHOLE_CARD_SCOPE_MODIFIER = (
    r"(?:(?:可见|可見|现有|現有|已有|既有|当前|當前)的?)?"
)
# ⚠⚠ Whole-card nouns use **prefix matching** and need a right boundary, or `字段名` matches `字段`:
# `把整个卡的所有字段名重写` asks to change every field **name**, yet triggers
# `_complete_full_rewrite_actions` to synthesize and autosave every field's **content**
# (Codex P1 round 12; base=True too, the same destructive family this PR fixes).
#
# ⚠ A boundary alone is insufficient: continuations in `设定项` / `字段内容` **remain scope nouns**,
# and blanket rejection blocks genuine whole-card requests. Allow two continuations: classifier-like suffixes (项/項/目/值)
# and another whole-card noun (设定内容 / 属性值...), both closed sets.
_WHOLE_CARD_SCOPE_SUFFIX = r"(?:项|項|目|值)"
# ⚠⚠ "Non-Han" must not include **Latin letters/digits/underscores**: custom field names can be
# nickname / field_name / age2, making `把整个卡的全部 nickname重写` another
# single-field guard bypass (Codex P1 round 13). Only **punctuation** is a valid non-Han ending.
# ⚠⚠ Endings need a **positive punctuation allowlist**, not a negated non-Han character class.
# Even after excluding Latin, a negative class treats **all other scripts** as punctuation: `把整个卡的全部 имя重写` /
# ` 이름重写` / ` ニックネーム重写` (the ja template really uses katakana field names) still enter whole-card
# completion (Codex P1 round 14; round 13 blocked only Latin and still had the wrong approach:
# non-Han is an open set, while enumerated **punctuation** is closed).
_WHOLE_CARD_TERMINATOR_PUNCT = (
    # ⚠ The full-width space `　` is **not** in this table: it is whitespace, not punctuation, and `\s*` already skips it.
    # Including it makes `把整个卡的全部　名字重写` a valid ending again, immediately reviving
    # the round 4 P1 (my first version did include it; paired tests caught it).
    # ⚠ Include only **sentence punctuation/paired symbols/quotes**. Do not casually add `_ + / @ #`, etc.:
    # fields can be named `_meta` or `@type`, so admitting them opens another P1 bypass (the first version included `_`,
    # immediately turning paired tests red).
    # ⚠⚠ **Opening quotes/brackets are not endings**. Counting them stops target matching at `《`, so
    # the quote narrowing scope to a subfield in `把整个卡的每一项《正文》重写` is ignored,
    # and whole-card completion still runs and autosaves (Codex P1 round 55, base=False: data overwrite).
    # ⚠ Keep only the **closing** half: a closing quote necessarily follows an opening quote, so the target really ended.
    # ⚠⚠ **Remove all paired delimiters**: the previous round removed only opening halves, but closing halves can still
    # precede a scope-narrowing qualifier: `）` ends the target in `把（整个卡的每一项）的名字重写`,
    # ignoring the following `的名字` and running whole-card completion + autosave (Codex P1 round 57).
    # Determining whether a closing delimiter pairs with a target-level opener requires positions, beyond a pure regex;
    # the costs differ by orders of magnitude, so take the safe side: endings recognize only **sentence punctuation**, not delimiters.
    # Cost: forms ending in a closing delimiter, such as `把整个卡的全部设定重写一遍）`, miss completion once: the lighter cost.
    r"[，,。．.！!？?；;：:、…·~～—–\-]"
)

# Valid endings when the quantifier **acts as head itself** (`把整個卡的全部重寫一遍`, base=True):
# first rewrite-verb character / adverb "都" / particles / sentence end / non-Han punctuation. A closed set unlike field names.
#
# ⚠ Whitespace **cannot** be a valid ending. The previous `[^一-鿿]` included spaces, so
# `把整个卡的全部 名字重写` became a whole-card rewrite: without the space the same sentence correctly edits one field,
# but adding a space enters `_complete_full_rewrite_actions`, overwriting and autosaving
# every other field (Codex P1). Only **skip** whitespace; afterward an actual ending is still required.
# ⚠ **Coordinating/emphatic adverbs** may precede the verb: `把整个卡的全部一并重写` /
# `把整個卡的全部一併重寫` / `把整个卡的全部彻底重写` all have base=True; requiring the next
# character to start the verb blocks them (Codex P2 round 12). Adverbs form a closed class; enumerate them fully.
# Single-field handling remains unaffected: after adverbs a rewrite-verb initial is still required, excluding field names.
# ⚠ English rewrite verbs are valid endings too: `把所有字段 rewrite` has base=True, but round 14's
# punctuation-only ending accidentally blocked it (Codex P2 round 22).
# This is safe: _CHAT_REWRITE_VERB_RE is a **closed set**, unlike arbitrary field names such as
# `nickname`; `\b` prevents matching longer identifiers such as `rewriteX`.
# ⚠ Use `(?![A-Za-z0-9_])` instead of `\b` for the right boundary: Han characters are Unicode word characters too, so `\b`
# **does not exist** between e/一 in `rewrite一下`, blocking mixed Chinese/English requests
# (Codex P2 round 30, base=True). Reject only continuations of Latin identifiers here.
_WHOLE_CARD_EN_REWRITE_VERB = (
    r"(?:rewrite|revise|regenerate|redo|refresh)(?![A-Za-z0-9_])"
)
# ⚠ Use a tuple rather than a handwritten regex: tests derive cases from this table and assert it is a **prefix code**
# (see _WHOLE_CARD_ADVERB_RUN below). A handwritten regex once duplicated "统统" in this table.
_WHOLE_CARD_BARE_ADVERBS = (
    "一并", "一併", "一起", "统统", "統統", "通通", "全都", "彻底", "徹底",
    "好好", "认真", "認真", "重新", "全面", "一律", "统一", "統一",
    "逐一", "逐个", "逐個", "挨个", "挨個", "再",
    # Universal quantifiers can act as adverbs too: `把所有字段全部重写` (base=True, Codex P2 round 28).
    "全部", "所有", "逐项", "逐項", "逐条", "逐條",
    # Batch adverbs (Codex P2 round 34, all base=True).
    "批量", "依次", "各自", "挨着", "挨著", "一次性", "集中",
    # Comprehensive/sequential adverbs (Codex P2 round 37, all base=True).
    # ⚠ **Exclude "分别/分別"**: base=False because `别|別` in _CHAT_NEGATED_REWRITE_RE
    # makes `分别` hit the negation guard. That guard's tradeoff is explicit: a missed trigger means
    # rewriting and autosaving the entire card despite the user saying not to edit. Relaxing it for a form
    # that never matched base takes the wrong direction.
    "均", "依序", "一概", "悉数", "悉數", "分开", "分開",
)
# ⚠ The light verb "进行" occupies **the same slot as adverbs** (target + X + rewrite verb):
# `请对所有字段进行重写` / `请将所有字段全部进行重写` both have base=True; the former ending
# allowlist rejected the target before reaching the actual rewrite verb (Codex P2 round 42).
# ⚠ It can **interleave** with adverbs (全部进行重写 / 进行统一重写), so include it directly in that
# `+` loop rather than as a separate pre-verb section. Keep tables separate by word class and combine them in the regex.
# ⚠ Benefactive/polite phrases (给我/帮我/替我/为我) occupy the same target + X + rewrite-verb slot:
# `把所有字段给我重写` has base=True (Codex P2 round 57). Like light verbs,
# include them in the `+` loop; keep separate word-class tables combined in the regex.
_WHOLE_CARD_LIGHT_VERBS = (
    "进行", "進行",
    "给我", "給我", "帮我", "幫我", "替我", "为我", "為我",
)
# ⚠ Necessity/obligation modal verbs occupy **that same slot**: `所有字段必须重写` has base=True,
# but this ending grammar rejected the target at the modal (Codex P2 round 61).
# ⚠ Codex reported only 必须/務必, but testing found the same family also lost
# 需要/应该/得/一定要/最好. Modals are a **closed class**: enumerate them all rather than patching individually.
# ⚠ Include only **necessity** forms, excluding possibility (能/会/可以/想/愿意):
# `所有字段能重写` asks about ability rather than issuing a command; admitting it risks data overwrite.
# ⚠ List `需` and `一定`, not `需要`/`一定要`: the run is `(?:词地?\s*)+`,
# composing the compounds as 需+要 / 一定+要 while retaining the table's **prefix-code** property
# (listing both `需要` and `需` breaks it and immediately fails the test).
# ⚠ Negative forms need no exclusion here: `所有字段不需要重写` uses _CHAT_NEGATED_REWRITE_RE,
# while `所有字段该不该重写` uses _CHAT_QUESTION_CLAUSE_RE; both guards precede this.
_WHOLE_CARD_MODAL_VERBS = (
    "必须", "必須", "必需", "务必", "務必", "需", "要", "得",
    # ⚠ Monosyllabic 须/應/當 are the roots of this family: `所有字段须重写` /
    # `所有欄位應重寫` both have base=True (round 63).
    # ⚠⚠ Adding single characters `应/應/当/當` **requires** removing compounds `应该/應該/应当/應當` too:
    # they break the table's **prefix-code** property (`应` prefixes `应该`), which ensures
    # `_WHOLE_CARD_ADVERB_RUN`'s `+` cannot backtrack exponentially. The run builds compounds from
    # 应+该 / 应+当 without reducing coverage. The prefix-code assertion catches this immediately.
    "须", "須", "应", "應", "当", "當", "该", "該", "一定", "最好",
)
_WHOLE_CARD_PREVERB_WORDS = (
    _WHOLE_CARD_BARE_ADVERBS + _WHOLE_CARD_LIGHT_VERBS + _WHOLE_CARD_MODAL_VERBS
)
_WHOLE_CARD_BARE_ADVERB = r"(?:" + "|".join(_WHOLE_CARD_PREVERB_WORDS) + r")"
# ⚠ Adverbs can **stack**: `把所有字段再统一重写` / `把整个卡的所有内容批量统一重写`
# both have base=True; the former version consumed only one adverb before requiring a rewrite verb,
# rejecting all such requests (Codex P2 round 35).
# ⚠ Use this shared run for all three ending tables; separate copies caused a silent failure when round 29 updated two but missed one.
# ⚠ This `+` cannot backtrack exponentially: the table above is a **prefix code** (no term prefixes another),
# so any input has at most one segmentation and failure exits linearly. Tests assert this property directly;
# adding a term that breaks it (such as a standalone "一") immediately turns them red.
# ⚠ Nor can this use an atomic group: `重新` starts with a rewrite-verb initial, and `统一重新` matches only by backtracking
# to adverb 统一 + verb initial 重. Atomicity would make this run narrower than the former single-adverb
# form, whereas it must strictly contain that version's accepted set.
_WHOLE_CARD_ADVERB_RUN = r"(?:" + _WHOLE_CARD_BARE_ADVERB + r"地?\s*)+"

# Action-count complements: in `重写所有字段一遍` / `请重写所有字段两次`, the verb **precedes the target**,
# followed by 一遍/一次/一下 (base=True, Codex P2 round 31).
# ⚠ This is **productive**: 一遍/两遍/三次/2遍/几遍... Numerals and classifiers are both closed sets,
# so express numeral + classifier rather than listing finished combinations (Codex P2 round 33).
# ⚠ Extract both tables as constants for generated tests. Previously classifiers were handwritten as `遍|次|下|轮|輪|遭|回`,
# and the test manually copied only six, omitting "遭"; deleting that branch would not fail tests (CodeRabbit).
# ⚠⚠ Round 33 added this branch only to _WHOLE_CARD_SCOPE_NOUN_TAIL, omitting the other two ending tables,
# so `重写所有字段的所有内容两遍` / `重写整个卡的全部两遍` changed from base=True
# to False (CodeRabbit Major). **This was the third missed update across three ending tables** (round 29
# missed one English-verb branch; round 35 missed all three stacked-adverb branches). Share a constant as with adverbs:
# the three tables now differ only in their recursive 的 branch; all other criteria share one definition.
# ⚠ Quantities include indefinite forms: `重写所有字段多次` / `好几遍` / `若干次` / `数次` all have base=True
# (Codex P2 round 39). Indefinite quantifiers are closed; add a branch alongside exact counts rather than adding 多
# to the numeral character class, which makes combinations such as 十多 ambiguous.
_WHOLE_CARD_MEASURE_WORDS = ("遍", "次", "下", "轮", "輪", "遭", "回")
# ⚠ The numeral class needs **place values**: `一百遍` / `几百遍` / `千次` / `一万次` all have base=True
# (Codex P2 round 43). Place-value characters are closed; include all at once instead of patching individually.
_WHOLE_CARD_NUMERAL_CHARS = "一二两兩三四五六七八九十百千万萬亿億零几幾半"
_WHOLE_CARD_INDEFINITE_QUANTITIES = (
    "好几", "好幾", "若干", "若幹", "许多", "許多", "数", "數", "多",
)
_WHOLE_CARD_MEASURE_COMPLEMENT = (
    r"(?:[" + _WHOLE_CARD_NUMERAL_CHARS + r"]+|\d+|"
    + "|".join(_WHOLE_CARD_INDEFINITE_QUANTITIES) + r")\s*(?:"
    + "|".join(_WHOLE_CARD_MEASURE_WORDS) + r")"
)

# Scope continuations: `字段值` / `设定内容` / `属性值项`.
# ⚠ The 可见/可見 modifier must apply to **every segment**: `把所有字段可见内容重写` has base=True;
# the former version allowed modifiers only after 的, omitting all three direct continuations (Codex P2 round 39).
# ⚠ All four continuations share these two constants. After the three ending tables, this is the **second** place
# where one rule was copied four times; avoid separate definitions, which already drifted in rounds 29/35/36.
# ⚠ Keep atomicity: overlapping parses cause exponential backtracking (the worst-case `项目` repeated 40 times).
# ⚠ A **locative phrase** may separate target and scope noun: `重写所有字段里的内容` /
# `把全部欄位中的內容重寫` (both base=True, Codex P2 round 45).
# ⚠ A scope noun is **still required** after the locative, preserving the single-field guard:
# `重写所有字段里的名字` remains False (base=True, intentionally changed by this PR).
_WHOLE_CARD_SCOPE_LOCATIVE = (
    # ⚠ Compound locatives (之中/之内/内部/里头) belong to the same family, Codex P2 round 48.
    r"(?:(?:之中|之内|之內|内部|內部|里头|裡頭"
    r"|里面|裡面|里边|裡邊|当中|當中|里|裡|中|内|內)的?\s*)?"
)
# ⚠ A universal quantifier may also intervene between locative and scope noun: `重写所有字段里的全部内容` /
# `把全部欄位裡的每個內容重寫` (both base=True, Codex P2 round 46).
# The 的 branch already allowed this; the locative branch missed it last round. Reuse the quantifier table directly.
# ⚠ A scope noun is **still required** after the quantifier; `重写所有字段里的全部名字` stays False.
_WHOLE_CARD_SCOPE_QUANTIFIER = (
    r"(?:(?:" + "|".join(_WHOLE_CARD_QUANTIFIERS) + r")\s*的?\s*)?"
)
_WHOLE_CARD_SCOPE_RUN_BODY = (
    r"(?:\s*" + _WHOLE_CARD_SCOPE_LOCATIVE + _WHOLE_CARD_SCOPE_QUANTIFIER
    + _WHOLE_CARD_SCOPE_MODIFIER
    + r"(?:" + _WHOLE_CARD_SCOPE_SUFFIX + "|"
    + "|".join(_WHOLE_CARD_SCOPE_NOUNS) + r"))"
)
# Clause continuation: after the target, a coordinating/sequential conjunction introduces the next action.
# `请重写所有字段并保存` / `重写所有字段然后告诉我` both have base=True; the former ending
# allowlist rejected them together with the target (Codex P2 round 43).
# ⚠⚠ **Exclude bare 后/後**, despite the reviewer's `重写所有字段后发给我` example:
# admitting it also admits `重写所有字段后缀`, precisely the destructive single-field case this PR fixes
# (base=True, intentionally changed to False). Distinguishing every verb/noun after 后 is an open-ended problem,
# so choose safety: 之后/然后 still work, and missing one phrasing only skips completion of a few fields.
# ⚠ Use a tuple: this table has expanded twice, and tests need to derive cases from it.
_WHOLE_CARD_CLAUSE_CONTINUATIONS = (
    "并且", "並且", "然后", "然後", "之后", "之後", "接着", "接著", "以及",
    # Sequential terms with omitted conjunctions (Codex P2 round 47, all base=True).
    # ⚠ These are all **two-character words**, unlike bare 后: the bad 后缀 example cannot match.
    "随后", "隨後", "最后", "最後", "接下来", "接下來",
    "同时", "同時", "而且", "并", "並", "且",
)
# Result-phrase endings: `重写所有字段即可` / `重写全部字段就行`
# (all base=True, Codex P2 round 50). When the verb precedes the target, the target can end with
# a phrase like 就行了. This family is a closed set.
_WHOLE_CARD_RESULT_PHRASES = (
    "即可", "就可以", "就行了", "就行", "就好了", "就好",
    "就成了", "就成", "便可", "可以了", "行了", "好了",
)
_WHOLE_CARD_RESULT_PHRASE = (
    r"(?:" + "|".join(_WHOLE_CARD_RESULT_PHRASES) + r")"
)
# ⚠⚠ Sequential terms must not precede **attributive constructions**: `重写所有字段最后一项` /
# `最后两个` / `最后的名字` denote **one item**, not the whole card (CodeRabbit Major).
# This is dangerous: whole-card misclassification synthesizes and autosaves missing fields.
# Criterion: a true sequential term precedes a **predicate**, neither 的 nor numeral + classifier.
_WHOLE_CARD_CONTINUATION_NOT_ATTRIBUTIVE = (
    r"(?!\s*的)"
    # ⚠⚠ Classifiers cannot be exhausted by a short list: 段 / 章 / 节 / 页 were absent, so
    # `把整个卡的每一项最后两段重写` bypassed it; the user wanted only each item's last two paragraphs,
    # yet whole-card completion and autosave ran (Codex P1 round 54, base=False).
    # Use a structural rule: **numeral + any single Han character** counts as an attribute (a classifier is one character).
    # ⚠ Allow an exception: numeral + character forms that are themselves adverbs, such as 一起 / 一并;
    # `重写所有字段最后一起保存` has base=True and must not be blocked with attributes.
    # ⚠ Quantities may have **ordinal/range modifiers**: `最后第2段` / `最后前两段`
    # (Codex P1 round 58). Requiring a numeral first let them bypass the guard.
    r"(?!\s*(?!一起|一并|一併|一同|一块|一塊|一律|一概|一直|一次性)"
    r"(?:第|前|后|後|头|頭|末)?\s*"
    # ⚠ Include `\d+` on the numeral side: Han-only numerals let `最后2段` bypass the attributive guard,
    # returning to whole-card completion + autosave (Codex P1 round 55, base=False).
    # The adjacent action-count constant already used both `[Han numerals]+|\d+`; only half was copied here.
    # ⚠⚠ **Reuse** _WHOLE_CARD_NUMERAL_CHARS rather than handwriting another numeral class.
    # The preceding P1 (`最后2段` bypass) came from a manual half-copy here;
    # defining the same dimension separately in the action complement, this guard, and test parameters repeats
    # the multiple-table drift already seen four times in this PR (CodeRabbit Major). One source ends this family.
    # ⚠ Both sides need **the same union**: action-count complements use
    # numeral characters | \d+ | indefinite quantifiers. Taking one branch alone lets `最后数行` / `最后好几段`
    # bypass the guard again, a typical manual-copy failure. Sharing the union truly shares the source.
    # ⚠ Quantities can also be **ranges**: `最后2-3段` / `2～3段` (Codex P1 round 57).
    # Requiring a Han character immediately after the digits let the intervening range separator bypass the guard.
    r"(?:[" + _WHOLE_CARD_NUMERAL_CHARS + r"]+|\d+|"
    + "|".join(_WHOLE_CARD_INDEFINITE_QUANTITIES) + r")"
    r"(?:\s*[-–—~～至到]\s*(?:[" + _WHOLE_CARD_NUMERAL_CHARS + r"]+|\d+))?"
    r"\s*[一-鿿])"
)
_WHOLE_CARD_CLAUSE_CONTINUATION = (
    r"(?:" + "|".join(_WHOLE_CARD_CLAUSE_CONTINUATIONS) + r")"
    + _WHOLE_CARD_CONTINUATION_NOT_ATTRIBUTIVE
)
_WHOLE_CARD_SCOPE_RUN_OPT = r"(?>" + _WHOLE_CARD_SCOPE_RUN_BODY + r"*)"
_WHOLE_CARD_SCOPE_RUN_ONE = r"(?>" + _WHOLE_CARD_SCOPE_RUN_BODY + r"+)"

# Noun-phrase endings: rewrite-verb initial / adverb 都 / particles / sentence end / punctuation. Same as
# _WHOLE_CARD_BARE_QUANTIFIER_TAIL; whitespace is skipped, never counted as an ending.
# ⚠⚠ 的 is a valid ending only **when followed by another scope component**.
# Previously bare 的 counted, letting `把所有字段的名字重写` (editing field **names**)
# bypass the single-field guard and trigger whole-card completion + autosave (CodeRabbit Major),
# the same family as 字段名 with an intervening 的.
# `把所有字段的内容重写一遍` remains True: 内容 is itself a whole-card noun.
# ⚠ Noun endings must also accept coordinating adverbs (`把所有字段一并重写`, base=True,
# Codex P2 round 14), using the same table as the quantifier branch to prevent drift.
# Noun-phrase **closed-set endings** (excluding the 的 branch to prevent infinite recursion below).
# ⚠ Targets may be followed by **reflexive emphasis**: `重写所有字段本身` /
# `把所有字段自身重新写` (both base=True, Codex P2 round 52).
# This reinforces the already explicit whole-card scope rather than naming one field, so it is **transparent**:
# the same following endings still apply (sentence end / adverb + verb / punctuation ...).
# ⚠⚠ Closing delimiters are **transparent**, not endings. Treating them as endings makes
# `）` end the target in `把（整个卡的每一项）的名字重写`, ignoring the following 的名字
# and still running whole-card completion + autosave (Codex P1 round 57, base=False).
# Yet simply deleting them blocks `把「所有字段」重写`, where quotes emphasize the target
# and base=True. Like reflexive emphasis, use a **transparent prefix**: skip it, then evaluate the real ending.
_WHOLE_CARD_CLOSER_PREFIX = r"(?:[」』）)】》〉\]”’'`\"]\s*)*"
_WHOLE_CARD_REFLEXIVE_PREFIX = (
    _WHOLE_CARD_CLOSER_PREFIX + r"(?:(?:本身|自身|本体|本體)\s*)?"
)
_WHOLE_CARD_SCOPE_NOUN_TAIL_CLOSE = (
    r"(?=\s*" + _WHOLE_CARD_REFLEXIVE_PREFIX + r"(?:$|"
    + _WHOLE_CARD_TERMINATOR_PUNCT
    + "|" + _WHOLE_CARD_EN_REWRITE_VERB
    + "|" + _WHOLE_CARD_ADVERB_RUN
    # ⚠ Adverbs can precede English rewrite verbs too. Round 29 updated the other **two** ending tables,
    # missing only this one; three copies of one rule silently fail when one update is missed (CodeRabbit).
    + r"(?:重|改|梳|完|" + _WHOLE_CARD_EN_REWRITE_VERB + r")"
    + r"|" + _WHOLE_CARD_MEASURE_COMPLEMENT
    + r"|" + _WHOLE_CARD_CLAUSE_CONTINUATION
    + r"|" + _WHOLE_CARD_RESULT_PHRASE
    + r"|重|改|梳|完|都|了|吧|啊|呀|嘛|喔|哦|(?:[啦喽嘍咯嘞咧]\s*[。！!？?]?\s*$)))"
)
_WHOLE_CARD_SCOPE_NOUN_TAIL = (
    r"(?=\s*" + _WHOLE_CARD_REFLEXIVE_PREFIX + r"(?:$|"
    + _WHOLE_CARD_TERMINATOR_PUNCT
    + "|" + _WHOLE_CARD_EN_REWRITE_VERB
    # ⚠⚠ The 的 branch must itself **recurse through the ending**, not merely check one scope component:
    # in `把所有字段的内容概要重写`, 内容 matches and 概要 is unchecked, reentering
    # whole-card completion (CodeRabbit Major). This is the **fifth entry point** in this PR where an allowlisted
    # word prefixes a longer word; the first four were 字段名 / 字段清单 / 的名字 / 内容名.
    # Consume a run of scope components (atomically to avoid overlapping parses), then require the closed-set ending.
    # ⚠ A **universal quantifier** may intervene between 的 and scope component: `把所有字段的所有内容重写`
    # has base=True (Codex P2 round 26). Reuse the closed quantifier table above directly.
    + r"|的\s*(?:(?:" + "|".join(_WHOLE_CARD_QUANTIFIERS) + r")\s*的?)?"
    # ⚠ Nested scopes also allow 可见/可見 modifiers (`把所有字段的可见内容重写`,
    # base=True, Codex P2 round 31); the outer branch already allowed them, but this branch missed them.
    + _WHOLE_CARD_SCOPE_RUN_ONE
    + _WHOLE_CARD_SCOPE_NOUN_TAIL_CLOSE + r"|"
    + _WHOLE_CARD_ADVERB_RUN
    + r"(?:重|改|梳|完|" + _WHOLE_CARD_EN_REWRITE_VERB + r")"
    # Action-count complements are also valid endings (shared definition for all three tables: _WHOLE_CARD_MEASURE_COMPLEMENT).
    + r"|" + _WHOLE_CARD_MEASURE_COMPLEMENT
    + r"|" + _WHOLE_CARD_CLAUSE_CONTINUATION
    + r"|" + _WHOLE_CARD_RESULT_PHRASE
    + r"|重|改|梳|完|都|了|吧|啊|呀|嘛|喔|哦|(?:[啦喽嘍咯嘞咧]\s*[。！!？?]?\s*$)))"
)
_WHOLE_CARD_BARE_QUANTIFIER_TAIL = (
    r"\s*" + _WHOLE_CARD_REFLEXIVE_PREFIX + r"(?:$|" + _WHOLE_CARD_TERMINATOR_PUNCT
    + "|" + _WHOLE_CARD_EN_REWRITE_VERB + r"|"
    + _WHOLE_CARD_ADVERB_RUN
    # ⚠ Adverbs can also precede **English** rewrite verbs: `把所有字段全部 rewrite`
    # (base=True, Codex P2 round 29).
    + r"(?:重|改|梳|完|" + _WHOLE_CARD_EN_REWRITE_VERB + r")"
    + r"|" + _WHOLE_CARD_MEASURE_COMPLEMENT
    + r"|" + _WHOLE_CARD_CLAUSE_CONTINUATION
    + r"|" + _WHOLE_CARD_RESULT_PHRASE
    + r"|重|改|梳|完|都|了|吧|啊|呀|嘛|喔|哦|(?:[啦喽嘍咯嘞咧]\s*[。！!？?]?\s*$))"
)
# Adverbs/common nouns denoting the whole card **by themselves when immediately after 的** (`重寫整個卡片的內容`).
# ⚠ Only count them immediately after 的: in `把整个卡的名字整体重写`, 整体 modifies a single field.
_WHOLE_CARD_HEAD_NOUNS = ("整体", "整體", "内容", "內容")

# ⚠ Prepositions introduce **reference material**, not the rewrite verb's object: in `根据整个卡的每一项内容重写
# 名字`, the user wants only 名字 changed; the whole-card phrase is 根据's object (base=False: data
# overwrite direction, round 68 P1).
# ⚠ Prepositions form a **closed class** and can be fully enumerated. This differs from the general question
# of whether the rewrite verb governs a whole-card target, which requires dependency relations (a new mechanism under issue #2693);
# this merely adds a left boundary to the existing target regex.
_CHAT_REFERENCE_PREPOSITIONS = (
    "根据", "根據", "按照", "依据", "依據", "参考", "參考", "照着", "照著",
    "对照", "對照", "比照", "仿照", "按", "依", "凭", "憑", "就着", "就著",
)
# ⚠ An **opening quote** may separate preposition and target: in `参考“整个卡的每一项内容”重写标题`,
# the quotation is reference material (base=False: data overwrite direction, round 69 P1).
# Fixed-length lookbehind cannot express one optional character, so expand preposition × (no quote + each opener);
# both tables are closed sets, making this mechanical expansion rather than phrase-by-phrase patching.
_CHAT_QUOTE_OPENERS_FOR_LOOKBEHIND = ("", "\u201c", "\u300c", "\u300e", "\u300a", "\u3010", '"')
_CHAT_REFERENCE_PREPOSITION_LEFT = "".join(
    rf"(?<!{preposition}{opener})"
    for preposition in _CHAT_REFERENCE_PREPOSITIONS
    for opener in _CHAT_QUOTE_OPENERS_FOR_LOOKBEHIND
)
_CHAT_FULL_REWRITE_RE = re.compile(
    _CHAT_REFERENCE_PREPOSITION_LEFT +
    # 整个卡 is a preexisting missing Simplified counterpart (the table had 整個卡), rather than an issue introduced
    # by Traditional coverage. It is a substring of neither 整张卡 nor 整个角色卡, so Simplified users choosing
    # 个 as classifier have always missed this match (CodeRabbit).
    #
    # ⚠ All CJK forms use `(?!的)`: followed by 的, they are **attributes**, not rewrite targets.
    # `把整個卡的名字重寫一下` intends only name changes, yet was classified as a full rewrite,
    # making `_complete_full_rewrite_actions` synthesize every missing field and overwrite the entire card
    # (Codex P1). Traditional Chinese already had this bug; fix it while adding Simplified 整个卡.
    # ⚠ Forms ending in 卡片 are **complete rewrite targets**, not exclusions: `重写整个角色卡片`
    # really rewrites the whole card. Include each 卡片 form explicitly and **place it before its
    # corresponding 卡 form** (regex alternation takes the first successful branch). The previous `(?![的片])`
    # addition omitted 卡片 and blocked all these requests (Codex P2 round 3).
    # Order: 整个角色卡片 -> 整个角色卡 -> 整张卡片 -> 整张卡 -> 整个卡片 -> 整个卡.
    # ⚠ The 字段 / 欄位 group has **no** lookahead: `把所有字段的内容重写一遍` is
    # a genuine whole-card rewrite; 的 precedes content rather than one field (Codex P2).
    # Only card targets need the attributive guard, where 的 indicates editing one card property.
    # ⚠ This group also uses **prefix matching**: 所有字段 prefixes 所有字段名,
    # letting `把整个卡的所有字段名重写` enter whole-card completion (Codex P1 round 12).
    # Close it with the same right boundary as whole-card nouns.
    rf"((?:所有可见字段|所有可見欄位|全部可见字段|全部可見欄位|所有字段|所有欄位|"
    rf"全部字段|全部欄位|每个字段|每個欄位)"
    # ⚠ As for whole-card nouns, consume valid scope continuations (值/项/内容...) before checking boundaries,
    # or `把所有字段值重写` / `重写全部字段内容` are blocked by their own boundary
    # (Codex P2 round 13, base=True).
    # ⚠ Whitespace may also precede continuations (`把所有字段 内容重写`, base=True).
    rf"{_WHOLE_CARD_SCOPE_RUN_OPT}"
    rf"{_WHOLE_CARD_SCOPE_NOUN_TAIL}"
    r"|(?:整个角色卡片|整個角色卡片|整个角色卡|整個角色卡|"
    r"整张卡片|整張卡片|整张卡|整張卡|"
    # Keep lookahead `(?![的片])`, not `(?!的)`: regex alternatives backtrack;
    # after 整个卡片 fails on 的 in `重写整个卡片的名字`, it falls back to 整个卡, followed by
    # 片. Blocking only 的 would still match. Both exclusions are required:
    #   整个卡片        -> 整个卡片 matches, followed by sentence end -> True
    #   整个卡片的名字   -> 整个卡片 blocked by 的, fallback 整个卡 blocked by 片 -> False
    #   整个卡          -> matches -> True
    #   整个卡的名字     -> blocked by 的 -> False
    # ⚠ Add an escape hatch beyond `(?![的片])`: whole-card target + 的 + universal quantifier + whole-card
    # noun remains a whole-card rewrite; only single-field attributes such as ...的名字 should be blocked.
    #
    # ⚠⚠ The escape hatch requires **both quantifier and its governed head**.
    # Checking only the noun after 的 fails: the former version allowlisted only 内容, blocking
    # `把整個角色卡的全部設定重寫一遍`, skipping whole-card completion and persisting only half
    # the card. Checking only quantifiers fails too: in `把整个卡的全部名字重写`, the quantifier governs the single field
    # 名字, yet whole-card classification overwrites all other fields (Codex P1, base=False).
    # See the two tables: _WHOLE_CARD_QUANTIFIERS / _WHOLE_CARD_SCOPE_NOUNS.
    #
    # ⚠⚠ Quantifiers must **immediately follow 的**, without a floating window.
    #
    # The window version received three consecutive P1 findings with the same destructive effect: in `把整個卡的名字整體重寫`
    # / `把整个卡的名字全部重写`, the quantifier modifies the single field 名字, but the window jumps across it
    # and matches, classifying the whole sentence as a whole-card rewrite; _complete_full_rewrite_actions synthesizes
    # rewrite actions for **every other field**, silently overwriting content the user never mentioned.
    #
    # Splitting quantifiers by syntactic distribution was tried (allow floating universal quantifiers, forbid adverbs/common
    # nouns): it blocked 整体/内容 but still leaked 的名字全部重写. Treating card field names as
    # a closed set to distinguish 名字 from 設定 was also tried, but _is_reserved shows cards allow arbitrary
    # custom field names: an open set.
    #
    # ⚠ This is therefore a **tradeoff**, not another more intricate regex:
    #   · Over-triggering silently overwrites unrequested fields (destructive, and autosaved).
    #   · Under-triggering leaves a few fields uncompleted; the user can ask again.
    #   The costs are asymmetric, so prefer under-triggering.
    #
    # Cost: inverted word order such as ...的設定全部重寫 no longer triggers whole-card completion (the model
    # still edits definitions as requested, only skipping completion). Codex's original requested fix,
    # `把整個角色卡的全部設定重寫一遍`, already has 的全部 adjacent to 的 and matches without a
    # window. The window only ever rescued the less common inverted word order.
    r"整个卡片|整個卡片|整个卡|整個卡|全卡)"
    # ⚠⚠ Targets must be **whole words**. 整个卡 also prefixes 整个卡通 / 整个卡组 /
    # 整个卡牌, so `把整个卡通角色的名字重写` triggered whole-card completion and overwrote every other
    # field (Codex P1; Simplified base=False, introduced here with this PR's Simplified
    # targets 整个卡 / 整个卡片).
    #
    # ⚠ Cannot denylist following characters (通/组/牌/座/车... are open-ended). Positively require the target to precede
    # sentence end, non-Han text, structural particle 的, or a **rewrite-verb initial**; the rewrite verbs
    # in _CHAT_REWRITE_VERB_RE below are a closed set.
    # ⚠⚠ Particles must **actually end the sentence** (only final punctuation/whitespace may follow).
    # Merely allowing a particle to complete the target lets 啦 make 整个卡啦OK a valid whole-card
    # target; in `把整个卡啦OK的名字重写`, the single-field qualifier 的名字 is ignored and the whole card
    # is synthesized and autosaved (base=False: data overwrite direction, round 75).
    # All six new particles create this regex opening; only 啦 occurs in a real word (卡啦OK).
    # ⚠ The existing particle test is **ineffective**: it asserts `重写所有字段{particle}`,
    # which uses another alternative with unconditional base=True, never reaching card targets.
    # ⚠ Particles are also valid endings for complete targets. Previously only 的 + rewrite-verb initial were allowed,
    # rejecting `重寫整個卡吧` / `重寫整個卡啊` as whole-card requests (base=True).
    # Particles form a closed class and can be fully enumerated like rewrite verbs.
    r"(?=$|[^一-鿿]|的|重|改|梳|完|全|都|了|吧|啊|呀|嘛|喔|哦|(?:[啦喽嘍咯嘞咧]\s*[。！!？?]?\s*$))"
    # Escape hatch 1: 的 + universal quantifier + (whole-card noun | valid ending for a quantifier acting as its own head).
    # Escape hatch 2: 的 + a directly adjacent term denoting the whole card as head itself.
    # ⚠ Structural particle 的 may separate quantifier and head: `把整个卡的所有的字段重写`
    # is among the most natural forms; missing it skips whole-card completion (Codex P2, base=True).
    # The single-field guard is unaffected: `把整个卡的所有的名字重写` stays False because
    # 名字 remains absent from the whole-card noun table.
    # ⚠ Whitespace may separate target and 的: in `把整个卡 的名字重写`, `(?![的片])` sees only
    # one character, a space, classifying the sentence as a whole-card rewrite and overwriting unrequested fields
    # (CodeRabbit). Skip whitespace in all three places, or adding a space bypasses the guard.
    rf"(?:(?=\s*的(?:{'|'.join(_WHOLE_CARD_SCOPED_QUANTIFIERS)})"
    # ⚠ Skip whitespace only **when the head really is a whole-card noun** (`把整个卡的所有 字段重写`,
    # base=True). The single-field guard is unaffected: after skipping whitespace in `把整个卡的全部 名字重写`,
    # 名字 remains absent from whole-card nouns; the bare-quantifier branch has its own
    # ending requirements (see _WHOLE_CARD_BARE_QUANTIFIER_TAIL).
    rf"(?:\s*的?\s*{_WHOLE_CARD_SCOPE_MODIFIER}"
    # ⚠ Scope suffixes (值/项/目) can also **act as heads themselves**: `把整个卡的全部值都重写`
    # has base=True; allowing only continuations blocks such forms (Codex P2 round 20).
    # The single-field guard is unaffected: 名字 is neither a whole-card noun nor a scope suffix.
    rf"(?:{'|'.join(_WHOLE_CARD_SCOPE_NOUNS)}|{_WHOLE_CARD_SCOPE_SUFFIX})"
    rf"{_WHOLE_CARD_SCOPE_RUN_OPT}"
    rf"{_WHOLE_CARD_SCOPE_NOUN_TAIL}"
    rf"))"
    # ⚠⚠ Use the narrow table for **quantifiers acting as heads themselves**: `把整个卡的全部重写` has base=True,
    # but `把整个卡的每一项重写` has base=False: itemwise quantifiers **name items** rather than
    # denote the whole card. Round 48 added 每一项 to the full table thinking only of quantifier + scope noun,
    # but also made it a standalone whole-card target, producing three P1s in rounds 57/58.
    rf"|(?=\s*的(?:{'|'.join(_WHOLE_CARD_BARE_QUANTIFIERS)})"
    rf"{_WHOLE_CARD_BARE_QUANTIFIER_TAIL})"
    # ⚠ Head nouns use prefix matching too and require a right boundary: `把整个卡的内容名重写` /
    # `的内容概要重写` enter whole-card completion here (CodeRabbit Major).
    # ⚠ Head nouns likewise allow scope components before boundary checks: `把整个卡的内容设定重写`
    # has base=True (Codex P2 round 20). 的内容名 remains blocked: 名 is neither a scope
    # suffix nor a whole-card noun.
    rf"|(?=\s*的(?:{'|'.join(_WHOLE_CARD_HEAD_NOUNS)})"
    rf"{_WHOLE_CARD_SCOPE_RUN_OPT}"
    rf"{_WHOLE_CARD_SCOPE_NOUN_TAIL})"
    r"|(?!\s*[的片]))"
    r"|full\s+card|whole\s+card|entire\s+card|all\s+fields|"
    r"all\s+visible\s+fields)",
    re.IGNORECASE,
)

_CHAT_REWRITE_VERB_RE = re.compile(
    r"(重写|重寫|重新写|重新寫|改写|改寫|重做|重生|梳理|完善|"
    r"rewrite|revise|regenerate|redo|refresh)",
    re.IGNORECASE,
)

_CHAT_ADVICE_ONLY_INTENT_RE = re.compile(
    r"(建议|建議|意见|意見|点评|點評|审一下|審一下|审稿|審稿|检查一下|檢查一下|"
    r"帮我看看|幫我看看|看一下|指出问题|指出問題|分析|优缺点|優缺點|"
    r"修改方向|修改方案|候选写法|候選寫法|suggest|suggestion|advice|critique|review|"
    r"pros\s+and\s+cons|candidate\s+rewrite)",
    re.IGNORECASE,
)

_CHAT_DIRECT_EDIT_REQUEST_RE = re.compile(
    r"(直接|现在|現在|立刻|马上|馬上|帮我|幫我|替我|给我|給我)?\s*"
    r"(改一下|改下|改一改|修改一下|调整一下|調整一下|调整下|調整下|改成|修改成|"
    r"换成|換成|写成|寫成|写进|寫進|应用|應用|采纳|採納|"
    r"更新字段|更新欄位|保存到字段|儲存到欄位|直接改|帮我改|幫我改|替我改|"
    r"apply|make\s+the\s+changes|edit\s+the\s+field|update\s+the\s+field|change\s+it\s+to)",
    re.IGNORECASE,
)


def _latest_user_text(history: list[dict]) -> str:
    for msg in reversed(history):
        if msg.get("role") == "user":
            return str(msg.get("content") or "").strip()
    return ""


def _chat_text_requests_edits(text: str) -> bool:
    text = text or ""
    return bool(
        _CHAT_EDIT_INTENT_RE.search(text)
        or _CHAT_DIRECT_EDIT_REQUEST_RE.search(text)
    )


# ⚠ Negated whole-card rewrite requests **must not** enter whole-card completion, the most destructive path
# in this PR (_complete_full_rewrite_actions synthesizes and autosaves every missing field).
# `不要重写整个卡` satisfies both whole-card target and rewrite-verb predicates. Negators are a **closed function-word class**,
# fully enumerable; a 0-4 nonpunctuation character window prevents 不要 from governing beyond a comma.
# ⚠ Apostrophes have three forms: ASCII `'`, U+2019 `’` automatically substituted by iOS/macOS/Word,
# and U+02BC `ʼ`. Recognizing only ASCII lets `don’t rewrite the whole card` bypass the negation
# guard, triggering whole-card completion + autosave and overwriting fields the user **explicitly said not to change**.
# （CodeRabbit Major）。
# ⚠ Base=True too, **not** a regression from this PR; nevertheless the direction is dangerous and the fix is just
# one character class, so fix it together. Handle English negation in music_requests.py identically to prevent drift.
# ⚠ Define a **set of characters**, not a ready-made `[...]`: the `n[o…]t` branch below needs o and the three apostrophes
# in one class. The first version added a separate `don[…]t` branch already covered by `n[o…]t`,
# so mutating this constant back to ASCII kept tests green: overlapping forms left the constant untested.
_EN_APOSTROPHE_CHARS = "'’ʼ"
# Negation/prohibition terms themselves (without the following rewrite verb).
# ⚠ A separate constant lets _chat_clause_without_quotes determine whether a quotation contains a prohibition;
# both uses must share one source, rather than another copied table waiting to drift.
# ⚠ Use a tuple for Chinese negators so tests derive Cartesian products. The former test split the regex
# at its first `)` then `|`; adding lookbehind to English moved the split early and silently truncated the table
# (seen when CodeRabbit suggested Latin boundaries). Constants eliminate source scraping.
_CHAT_NEGATION_WORDS = (
    # ⚠ Negation/prohibition is a **closed class**; enumerate all at once instead of waiting for reviewers to find one at a time.
    # Greptile reported only 不准; testing also found missing 不許/不许/禁止/嚴禁/严禁/
    # 休要/不得/莫. Individual patches are whack-a-mole for an exhaustively enumerable dimension.
    "不要", "不用", "不需要", "不必", "不想", "不准", "不準", "不許", "不许",
    "不得", "不可", "不能",
    "别", "別", "甭", "莫", "休要", "先不", "暫不", "暂不", "暫時不", "暂时不",
    "無需", "无需", "勿", "切勿", "請勿", "请勿", "禁止", "嚴禁", "严禁",
    # ⚠ The 没有必要 family: `没有必要重写整个卡的每一项内容` has base=False
    # (round 68 P1). This is a **negative assertion**, not an imperative prohibition, but equivalent for our purpose.
    "没有必要", "沒有必要", "没必要", "沒必要", "不必要", "無必要", "无必要",
)
_CHAT_NEGATION_LEXEME = (
    r"(?:" + "|".join(_CHAT_NEGATION_WORDS)
    # ⚠ Include English negation too: whole-card targets and rewrite verbs already have English branches
    # (rewrite/regenerate/all fields), but a Chinese-only negation guard lets
    # `don't rewrite the whole card` bypass it directly (CodeRabbit).
    # The `do\s*n[o…]t` branch covers do not / don't / don’t / donʼt / do n’t;
    # `dont` (omitting the apostrophe entirely) has no intervening character and needs a separate entry.
    # ⚠ English branches need **Latin word boundaries**: `never` is a substring of `whenever`, so
    # `whenever you rewrite all fields` was treated as negation and silently skipped whole-card completion (CodeRabbit).
    # ⚠ Do not use `\b`: it always matches between Latin and Han here; only the Latin-side boundary is wanted.
    + r"|(?<![A-Za-z])"
    + rf"(?:do\s*n[o{_EN_APOSTROPHE_CHARS}]t|dont|never|no\s+need\s+to"
    + rf"|please\s+do\s*n[o{_EN_APOSTROPHE_CHARS}]t)(?![A-Za-z])"
    + r"\s*)"
)
_CHAT_NEGATED_REWRITE_LEXEME_RE = re.compile(_CHAT_NEGATION_LEXEME, re.IGNORECASE)
_CHAT_NEGATED_REWRITE_RE = re.compile(
    _CHAT_NEGATION_LEXEME +
    # ⚠ The window must cover the whole object phrase. In `請勿把整個角色卡全部重寫`, eight characters
    # separate 請勿 and 重寫, beyond {0,4}.
    # Widening this takes the **safe direction**: false-positive negation skips completion (a few fields remain missing),
    # while false negatives rewrite and autosave the whole card despite a don't-edit request. Costs differ.
    # ⚠⚠ A fixed-length window **cannot** work here. {0,4} misses object phrases; {0,12}
    # is bypassed by longer sentences; {0,24} merely moves the threshold. Object phrases
    # can be arbitrarily long, so this path has no end (three consecutive reviewer reports).
    #
    # The true limit is the **clause**: negation governs until sentence punctuation. Use arbitrary length without
    # crossing clause punctuation: the complement of _CHAT_CLAUSE_SPLIT_RE's punctuation table below.
    # ⚠ Both uses must share one source, or negation acting only within its own clause means different things in each.
    r"[^。，、！？,.!?;；]*?"
    # ⚠ Include English on the verb side too. Whole-card targets and rewrite verbs already include English;
    # adding English negators without verbs still lets `don't rewrite the whole card` bypass the guard.
    r"(?:重写|重寫|重新写|重新寫|改写|改寫|重做|重生|梳理|完善"
    r"|rewrite|revise|regenerate|redo|refresh|change|update)",
    # ⚠ Whole-card target and rewrite-verb regexes both use re.IGNORECASE; omitting it from the negation guard
    # creates asymmetry: `Don't rewrite the whole card` meets both positive predicates but bypasses the guard,
    # entering whole-card completion (Codex P1). All three predicates must share case handling.
    re.IGNORECASE,
)


# Clause boundaries: the same table as the do-not-cross character class in _CHAT_NEGATED_REWRITE_RE.
_CHAT_CLAUSE_SPLIT_RE = re.compile(r"[。，、！？,.!?;；]+")


def _chat_clauses(text: str) -> list[str]:
    """Split the entire text into clauses at sentence punctuation.

    ⚠⚠ **This once skipped punctuation inside quotations, then was reverted. Do not change it a third time.**

    The change addressed `重写所有字段并把口头禅设为“好不好，随便”`: the quoted comma
    split the span, exposing 好不好 to the question guard and discarding the whole command.
    That was real, but merely **left a few fields uncompleted**; the user could ask again.

    The cost was **two data-overwrite bugs**, both on the irreversible side:
      · Without splitting quoted commas, whole-card targets and rewrite verbs formerly in separate clauses merged,
        letting `先展示“整个卡，姓名”然后重写名字` enter whole-card completion + autosave;
      · Worse, the window in `_CHAT_NEGATED_REWRITE_RE` is **the complement of this punctuation table**,
        but was not updated, separating the two definitions of clause length:
        in `不要把“整个卡，包括头像”重写`, 不要 could not reach 重写, overwriting the entire card
        despite explicit prohibition. The comment on `_CHAT_CLAUSE_SPLIT_RE` saying both uses must share one source
        means exactly this; only one use was changed then.

    Trading one omitted action for two extra irreversible actions takes the wrong direction.
    Reverting removed both automatically, along with the paired guards added in round 63 to compensate.
    The accepted cost is recorded as a by-design test; see
    `test_a_separator_inside_a_quoted_value_still_splits_the_clause`。
    """  # noqa: DOCSTRING_CJK
    return [c for c in _CHAT_CLAUSE_SPLIT_RE.split(text or "") if c.strip()]


# ⚠ Quoted content is **referenced material**, not an instruction to us.
# In `Use “Don’t Panic” as the theme and rewrite all fields`, Don’t belongs to a song title,
# but the guard treated it as don't-rewrite and skipped whole-card completion (Codex P2 round 42).
# ⚠ This was not introduced solely by apostrophe support: ASCII `"Don't Panic"` already had base=False,
# just unreported. Fix the **criterion's structure**: erase quotation spans before checking negation.
# ⚠ Erase only for the negation guard; targets and rewrite verbs still inspect original text, or a target
# quoted as `把《整个卡》重写` would disappear too.
# ⚠ **Exclude ASCII single quotes and left single quotes**: in `Don't Stop Believin'` they are apostrophes,
# not quotations. Including them treats `'t Stop Believin'` as quoted text and erases actual negation, the dangerous direction.
# music_requests._ZH_AMBIGUOUS_QUOTE_OPENERS makes the same tradeoff.
# Trailing negative assertions: verb first, negation afterward. A closed set.
_CHAT_POSTPOSED_NEGATION_RE = re.compile(
    r"(?:并不是|並不是|不是|并非|並非|算不上|谈不上|談不上|没有一个是|沒有一個是)"
    r"\s*(?:很|太|特别|特別)?\s*"
    r"(?:必要|必须|必須|必需|需要|应该|應該)"
)
_CHAT_QUOTED_SPAN_RE = re.compile(
    r"“[^”]*”|「[^」]*」|『[^』]*』|《[^》]*》|【[^】]*】|\"[^\"]*\""
)


def _chat_span_carries_a_question(span: str) -> bool:
    """Does this quotation contain a question/conditional head? Same criterion shape as the prohibition check above.

    ⚠⚠ The root cause is **two text versions treating the same quotation asymmetrically**: the question guard reads
    text with all spans erased, while positive signals read text with only prohibition spans erased. A complete
    conditional clause inside quotes is invisible to the question guard but visible to positive signals:
    `卡里这句“Whenever you rewrite all fields keep the tone”有点奇怪` therefore
    enters whole-card completion + autosave (base=False: data overwrite direction, round 68).
    The user is merely commenting on a card line or repeating someone else's rule.

    ⚠ Fix **positive signals**, rather than making the question guard inspect quotations: quoted interrogative
    forms may be field values (`把口头禅设为“好不好”`, round 59), and exposing them
    would wrongly block that command. Erasing the whole span handles both correctly: target and verb there are outside quotes.
    """  # noqa: DOCSTRING_CJK
    return bool(_CHAT_QUESTION_CLAUSE_RE.search(span))


def _chat_span_carries_a_prohibition(span: str) -> bool:
    """Does this quotation contain a prohibition? Used only for **positive signals**; the negation guard always reads original text.

    ⚠⚠ This criterion went through three revisions and settled on **the safe side**; do not revert it:

    * Round 42: to avoid a song title's Don’t blocking `Use “Don’t Panic” as the theme and rewrite all fields`,
      quotation spans were erased for the negation guard;
    * Round 43 P1: this erased the prohibition in `请“不要重写”所有字段`, so the rule became quoted rewrite
      verbs indicate instructions;
    * Round 47 P1: `Please “do not” rewrite all fields` still slipped through, so material required additional
      text after removing negators;
    * Round 48 P1: 千万 / ever were that additional text in `请“千万不要”重写所有字段` / `Please “do not ever” …`,
      letting prohibition slip through again.

    **Three rounds with three new shapes show that this boundary cannot be drawn**: song titles and emphasized prohibitions
    have identical syntax and differ only semantically. The costs differ by orders of magnitude: admitting a quoted
    song title only skips a few completed fields; admitting a real prohibition overwrites and autosaves explicitly protected user data.

    Stop here: **the negation guard reads original text; quoted prohibitions always count**. Quotes affect only positive signals:
    a quoted prohibition must not also provide a whole-card target (round 44 P1's
    `Use “do not touch all fields” as an example and rewrite the title`）。
    The cost is still missing whole-card completion once for “Don’t Panic”; accept the lighter cost.
    """  # noqa: DOCSTRING_CJK
    return bool(_CHAT_NEGATED_REWRITE_LEXEME_RE.search(span))


_CHAT_ANY_QUOTED_SPAN_SUB = _CHAT_QUOTED_SPAN_RE.sub


# ⚠⚠ Free-choice frames **must require their correlatives too**, not merely 都/就/也.
# Music's `_ZH_CORRELATIVE_RIGHT` only looks ahead for correlatives; its cost is missing one music stop.
# Here the cost is **overwriting every unrequested field**, far heavier: correlative-only matching would treat
# genuine questions such as `所有字段是不是都要重写` as commands and enter whole-card completion.
# Require the complete frame term + window + correlative; both conditions are indispensable.
_CHAT_FREE_CHOICE_FRAMES = (
    # Free choice.
    "无论", "無論", "不论", "不論", "不管", "任凭", "任憑", "随便", "隨便",
    # ⚠ Conditional/concessive/cognitive families were initially missed when only free-choice forms were copied: `即使是否满意都把所有字段重写
    # 一遍` / `不知道为什么就是要重写整个卡的全部设定` still looked like questions and were discarded
    # (both base=True, round 63). Music has 45 terms in the same family, versus only nine here;
    # different tables for the same linguistic phenomenon are another instance of round 62's asymmetric
    # guards across modules.
    # ⚠ Adding terms here **widens** acceptance and is normally dangerous, but round 62's criterion requires
    # frame term + window + correlative together; additions cannot let true questions such as `是不是都要重写所有字段`
    # bypass the guard, as the safety-boundary assertion ensures.
    # Conditional.
    "如果", "假如", "若是", "要是", "倘若", "万一", "萬一", "假若",
    # Concessive.
    "即使", "即便", "就算", "哪怕", "纵使", "縱使",
    # Cognitive.
    "不知道", "不記得", "不记得", "不清楚", "不确定", "不確定",
)
_CHAT_FREE_CHOICE_SPAN_RE = re.compile(
    r"(?:" + "|".join(_CHAT_FREE_CHOICE_FRAMES) + r")"
    r"[^。，、！？,.!?;；]{0,12}?[都就也]"
)


def _chat_clause_without_free_choice(clause: str) -> str:
    """Replace the entire free-choice frame ... correlative span with spaces, only for the question guard."""  # noqa: DOCSTRING_CJK
    return _CHAT_FREE_CHOICE_SPAN_RE.sub(" ", clause)


def _chat_clause_without_quotes(clause: str) -> str:
    """Replace **all** quotation spans with spaces, only for the question guard."""  # noqa: DOCSTRING_CJK
    return _CHAT_ANY_QUOTED_SPAN_SUB(" ", clause)


def _chat_clause_without_quoted_prohibitions(clause: str) -> str:
    """Replace **quotation spans containing prohibitions** with spaces, only for positive signals.

    A quoted prohibition must not also supply a whole-card target:
    `Use “do not touch all fields” as an example and rewrite the title`
    quoted `all fields` combined with an unquoted single-field `rewrite` is not a whole-card request
    (Codex P1 round 44).

    ⚠ Keep quotations without prohibitions (`把《整个卡》重写` / `把「所有字段」重写`):
    the user is emphasizing a target with quotes, and these forms all have base=True.
    ⚠ **The negation guard does not use this text**: it always reads the original sentence, as explained in
    _chat_span_carries_a_prohibition's comments.

    ⚠⚠ Erase spans containing **question/conditional heads** too (round 68): in
    `卡里这句“Whenever you rewrite all fields keep the tone”有点奇怪`,
    the user comments/reports rather than commands. The question guard erases all spans and
    cannot see quoted `Whenever`, while positive signals read this text and see quoted
    `all fields` and `rewrite`: **two views of the same quotation** create the gap.
    Fixing this makes both uses agree on quoted interrogative forms.
    """  # noqa: DOCSTRING_CJK
    return _CHAT_QUOTED_SPAN_RE.sub(
        lambda m: " " if (
            _chat_span_carries_a_prohibition(m.group(0))
            or _chat_span_carries_a_question(m.group(0))
        ) else m.group(0),
        clause,
    )


# ⚠⚠ Questions **are not edit commands**. Cards previously had no question guard (music had a full set),
# so `把整个卡的每一项都需要重写吗` / `是否要把整个卡的每一项重写` reached
# `_complete_full_rewrite_actions`, synthesizing and autosaving every missing field even though the user merely
# asked whether to edit (Codex P1 round 57, base=False).
# ⚠ Erring broadly is safe: false positives leave a few fields uncompleted (the user can rephrase),
# while false negatives actually edit and save something merely asked about. Prefer a broad guard.
# ⚠ Recognize only **closed-set** question markers: final particles, polarity terms, and modal A-not-A reduplication.
# Do not recognize bare question marks: `重写整个卡?` is a baseline command; blanket rejection changes existing behavior.
# ⚠ Share **one table** across modules rather than copying it; see the A-not-A comment below.
from main_logic.text_patterns import zh_a_not_a_forms  # noqa: E402

_CHAT_QUESTION_CLAUSE_RE = re.compile(
    r"(?:[吗嗎呢]\s*[？?]?\s*$"
    # ⚠ 有没有 needs a left boundary: in `把整个卡的所有没有填的内容重写一遍`, it is
    # 所有 + 没有, not a polarity marker (base=True, round 63).
    # This is the PR's ninth entry point where an allowlisted term is a substring of a longer word.
    r"|是否|能否|可否|(?<!所)有没有|(?<!所)有沒有"
    # ⚠ The following branch is **computed**, so concatenate with `+`, not adjacent literals.
    # ⚠⚠ Generate modal A-not-A forms **directly from music's table generator**, without copying it here.
    # The manual copy had only nine, missing 愿不愿意 / 值不值得 / 允不允许 / 舍不舍得...;
    # questions became whole-card commands and autosaved (all base=False, round 69 P1).
    # This PR has already failed **three times** from duplicating one concept (clause splitting vs negation
    # windows, title masking vs question markers, reason scope vs frame scope),
    # so share the source directly. main_routers -> main_logic is an existing dependency direction.
    # ⚠ Generated branches must also exclude preceding 所: 有没有 in 所有没有填的内容 is merely a substring;
    # carry the handwritten branch's `(?<!所)` into generated forms to avoid reopening the same hole elsewhere.
    + r"|(?<!所)(?:" + "|".join(zh_a_not_a_forms()) + r")"
    + r"|是不是|好不好|行不行|对不对|對不對"
    # ⚠ Wh-question heads. Round 57's guard included only polarity/modal forms,
    # so `为什么要重写整个卡的所有内容` still entered whole-card completion + autosave.
    # ⚠ This is **not** a PR regression (base=True too); the preceding guard was only half built.
    # Completing it narrows baseline behavior in the safe direction (a few fields remain uncompleted).
    # ⚠ Exclude preceding 因: 为什么 is just a substring in `因为什么都没写所以重写…`
    # (base=True), the PR's seventh allowlisted-substring entry point.
    r"|(?<!因)为什么|(?<!因)為什麼|(?<!因)为何|(?<!因)為何|(?<!因)为啥|(?<!因)為啥"
    r"|干嘛|幹嘛|凭什么|憑什麼"
    # ⚠ Wh-heads initially covered only 为 forms (为什么/为何/为啥), leaving **how-to questions** such as `怎么把整个卡的每一项
    # 内容重写` / `如何…` / `谁…` entering whole-card completion + autosave
    # (all base=False: data overwrite direction, round 68 P1).
    # ⚠ Like 谁 below, recognize them **only at clause start**, where they are how-to question heads.
    # The first version denylisted left contexts (不管/无论/该), but mutation testing showed that list was **redundant**:
    # free-choice masking already removed 不管怎么…都…, so deleting the list failed no tests.
    # It also introduced a type-3 regression: in `把所有字段该怎么重写就怎么重写`, the final
    # 怎么 follows 就 rather than 该 and still fired. Clause-start matching fixes both problems.
    r"|^(?:怎么|怎麼|怎样|怎樣|如何)"
    # ⚠ Recognize 谁 **only at clause start**, where it is an interrogative subject. Initially a left denylist plus
    # a rewrite verb within 20 following characters wrongly blocked `把所有字段里谁的名字都重写` and
    # `告诉我谁写的然后重写所有字段` (both base=True). Medial 谁 is common, and left neighbors are open-ended
    # (里谁/我谁/问谁/看谁...), impossible to denylist exhaustively. Clause start is a closed criterion.
    r"|^(?:谁|誰)"
    # ⚠⚠ English had no guard at all: whole-card targets and rewrite verbs already included English,
    # while question/conditional guards were Chinese-only, making **conditional clauses** such as `Whenever you rewrite all fields, keep the
    # tone consistent` into whole-card rewrite commands with autosave
    # (base=False: data overwrite direction, round 63).
    # This is the same asymmetry as round 30's missing English negation guard.
    # ⚠ Use inline `(?i:…)` rather than IGNORECASE for the whole regex, leaving fixed-length
    # lookbehinds in Chinese branches unaffected and minimizing scope.
    # ⚠ Require Latin word boundaries on both sides, or substrings in `iffy` / `whenever` falsely match.
    r"|(?i:(?<![A-Za-z])(?:whenever|wherever|whichever|if|when|whether"
    r"|unless|should|could|would|can|do|does|did|why|how|what|which"
    r"|are|is)(?![A-Za-z]))"
    r")"
)


def _chat_text_requests_full_rewrite(text: str) -> bool:
    """Whole-card rewrite criterion: all three predicates must hold **within the same clause**.

    Previously the whole text was searched separately for targets / rewrite verbs / negation, then combined.
    That produced three contradictory bugs with one root cause: criteria applied across the entire text.

    * The negation guard connected negators and rewrite verbs through a fixed window that missed long objects.
      Widening {0,4} to {0,12} then {0,24} was bypassed by longer sentences each time;
    * Negation caused a **global early return**, so 不用 in one clause also rejected another clause's explicit
      whole-card request (`名字不用重寫，但請重寫整個卡`);
    * Whole-card targets and rewrite verbs could belong to **different clauses** yet combine
      (整个卡 is 展示's object in `先展示整个卡，然后重写名字`).

    Clause evaluation solves all three: signals in separate clauses no longer meet, negation acts only within
    its own clause, and same-clause scope naturally replaces an eternally insufficient fixed window.

    ⚠ Still prefer **under-triggering**: over-triggering makes
    `_complete_full_rewrite_actions` synthesize and autosave every missing field, overwriting
    unrequested user data; under-triggering only leaves a few fields uncompleted. Require **any one** clause
    to meet all three criteria together, rather than combining signals scattered throughout the text.
    """  # noqa: DOCSTRING_CJK
    if not text:
        return False
    for clause in _chat_clauses(text):
        # ⚠ All three predicates must read **the same text**, treating erased quotations identically for negation
        # and positive signals. Previously only the negation guard erased them while positive signals read original text,
        # combining quoted `all fields` with an unquoted single-field `rewrite` to enter whole-card completion
        # (Codex P1 round 44).
        # ⚠ The negation guard reads the **original sentence**: quoted prohibitions always count.
        # ⚠ Trailing negative assertions: `把整个卡的每一项内容都重写并不是必要的` places negation
        # **after** the verb, beyond the preceding window (base=False, round 68 P1).
        # This is a closed family (并不是/不是/算不上 + 必要/必须/必需 + 的); give it a separate branch.
        if _CHAT_POSTPOSED_NEGATION_RE.search(clause):
            continue
        if _CHAT_NEGATED_REWRITE_RE.search(clause):
            continue
        # ⚠ Skip interrogative clauses too: the user is asking whether to edit, not issuing a command.
        # ⚠ Inspect text **after erasing quotation spans**: 好不好 in `重写所有字段并把口头禅设为“好不好”`
        # is a field value, not a question, and must not discard the whole command
        # (base=True, Codex P2 round 59). Same rationale as the negation guard, with opposite handling:
        # quoted prohibitions always count there, while quoted questions do not count here.
        # Both consistently choose **fewer edits to user data**.
        # ⚠ Within free-choice frames, polarity markers mean regardless of whether, rather than questions:
        # `无论是否缺失都重写所有字段` has base=True, yet the question guard discarded the whole command
        # (Codex P2 round 62; 60 related tested cases).
        readable_question = _chat_clause_without_free_choice(
            _chat_clause_without_quotes(clause)
        )
        if _CHAT_QUESTION_CLAUSE_RE.search(readable_question):
            continue
        readable = _chat_clause_without_quoted_prohibitions(clause)
        if (
            _CHAT_FULL_REWRITE_RE.search(readable)
            and _CHAT_REWRITE_VERB_RE.search(readable)
        ):
            return True
    return False

def _chat_text_requests_advice_only(text: str) -> bool:
    if not text:
        return False
    return bool(
        _CHAT_ADVICE_ONLY_INTENT_RE.search(text)
        and not _CHAT_DIRECT_EDIT_REQUEST_RE.search(text)
    )


def _build_action_recovery_prompt(
    *,
    lang: str,
    locale_code: str,
    user_instruction: str,
    current_card_text: str,
    target_keys_text: str,
    assistant_reply: str,
) -> str:
    """Build a provider-agnostic protocol recovery prompt.

    This is intentionally not a replacement for the companion persona prompt:
    the original reply stays visible to the user. This pass only recovers the
    structured actions the UI protocol needs.

    Traditional Chinese shares the Simplified branch: this prompt is machinery
    the user never sees, and the reply's own language is pinned separately by
    ``_output_language_directive`` off ``locale_code``, which does keep zh-TW.
    """
    if lang.startswith("zh"):
        prompt = f"""你是角色卡动作恢复器，不要扮演角色，不要回复用户。

用户原话：
{user_instruction}

当前角色卡：
{current_card_text}

可用字段 key（field_key 必须原样复制；除 add_field 外不要创造新 key）：
{target_keys_text}

上一轮助手回复（仅供判断意图，不要改写这段话）：
{assistant_reply[:2000]}

只返回 JSON，禁止 markdown 和 JSON 外文字：
{{"actions":[{{"type":"refine_field","field_key":"字段名","value":"新值","reason":"原因"}}]}}

规则：
- 如果用户原话明确要求修改、重写、补充、删除角色卡字段，actions 必须包含具体操作。
- 如果用户要求“所有/全部/整张卡/所有可见字段”重写，尽量覆盖所有可用字段 key。
- 改已有字段用 refine_field；新增字段用 add_field；删除字段用 remove_field 且不要 value。
- 如果用户没有修改字段意图，返回 {{"actions":[]}}。
- 不要触及保留字段：档案名 / voice_id / system_prompt / live2d / live3d / vrm / mmd / model_type。"""
    else:
        prompt = f"""You are a character-card action recovery tool. Do not roleplay and do not reply to the user.

User message:
{user_instruction}

Current character card:
{current_card_text}

Available field keys (copy field_key exactly; do not invent keys except for add_field):
{target_keys_text}

Previous assistant reply (intent context only; do not rewrite it):
{assistant_reply[:2000]}

Return JSON only. No markdown or text outside JSON:
{{"actions":[{{"type":"refine_field","field_key":"Field Name","value":"new value","reason":"why"}}]}}

Rules:
- If the user clearly asked to edit, rewrite, add, or remove card fields, actions must contain concrete operations.
- If the user asked to rewrite all fields / the whole card / all visible fields, try to cover every available field key.
- Use refine_field for existing fields; add_field for new fields; remove_field without value for removals.
- If there is no field-edit intent, return {{"actions":[]}}.
- Never touch reserved fields: 档案名 / voice_id / system_prompt / live2d / live3d / vrm / mmd / model_type."""
    return prompt + _output_language_directive(locale_code)


async def _recover_actions_from_reply(
    *,
    lang: str,
    locale_code: str,
    user_instruction: str,
    current_card_text: str,
    target_keys_text: str,
    assistant_reply: str,
) -> list[dict]:
    prompt = _build_action_recovery_prompt(
        lang=lang,
        locale_code=locale_code,
        user_instruction=user_instruction,
        current_card_text=current_card_text,
        target_keys_text=target_keys_text,
        assistant_reply=assistant_reply,
    )
    content, err = await _invoke_assist(prompt)
    if err is not None or not content:
        return []
    try:
        parsed = _loads_json_lenient(content)
    except json.JSONDecodeError:
        return []
    return _sanitize_actions(parsed.get("actions") if isinstance(parsed, dict) else None)


async def _complete_missing_fields_by_refine(
    *,
    lang: str,
    locale_code: str,
    card_text: str,
    current_card: Any,
    missing_keys: list[str],
    instruction: str,
) -> Dict[str, str]:
    completed: Dict[str, str] = {}
    template = get_card_assist_refine_field_prompt(lang)
    for field_key in missing_keys[:_ACTION_RECOVERY_SPLIT_MAX_FIELDS]:
        current_value = ""
        if isinstance(current_card, dict):
            current_value = str(current_card.get(field_key) or "")
        prompt = template % (card_text, field_key, current_value, instruction)
        prompt += _output_language_directive(locale_code)
        content, err = await _invoke_assist(prompt)
        if err is not None or not content:
            continue
        value = _clean_plain_field_value(content)
        if value:
            completed[field_key] = value
    return completed


async def _complete_full_rewrite_actions(
    *,
    lang: str,
    locale_code: str,
    actions: list[dict],
    user_instruction: str,
    current_card: Any,
    current_card_text: str,
    target_keys: list[str],
) -> list[dict]:
    present = {
        str(a.get("field_key") or "").strip()
        for a in actions
        if str(a.get("type") or "").strip() in {"refine_field", "add_field"}
    }
    missing = [
        k for k in target_keys
        if k not in present and not _is_reserved_card_field(k)
    ]
    if missing:
        fields = await _complete_missing_fields_by_refine(
            lang=lang,
            locale_code=locale_code,
            card_text=current_card_text,
            current_card=current_card,
            missing_keys=missing,
            instruction=user_instruction,
        )
        for key in target_keys:
            value = fields.get(key)
            if not value:
                continue
            actions.append({
                "type": "refine_field",
                "field_key": key,
                "value": value,
                "reason": "full_field_rewrite",
            })
            if len(actions) >= _CHAT_MAX_ACTIONS:
                break
    return actions[:_CHAT_MAX_ACTIONS]


def _normalize_chat_history(raw: Any) -> list[dict]:
    """Filter+truncate the client's message history. Returns OpenAI-style
    role/content dicts only, never raises."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        if role not in _CHAT_HISTORY_ROLES:
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if len(content) > _CHAT_MAX_MESSAGE_CHARS:
            content = content[:_CHAT_MAX_MESSAGE_CHARS] + "…"
        out.append({"role": role, "content": content})
    # Keep only the last N entries, but ensure the final role is user; otherwise the next LLM sees an assistant
    # utterance last and cannot tell what to answer.
    if len(out) > _CHAT_MAX_HISTORY_MESSAGES:
        out = out[-_CHAT_MAX_HISTORY_MESSAGES:]
    while out and out[-1]["role"] != "user":
        out.pop()
    return out


def _sanitize_actions(raw: Any) -> list[dict]:
    """Validate the LLM-proposed action list. Drops anything that touches
    reserved fields, has unknown types, or carries non-string keys/values."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for a in raw:
        if len(cleaned) >= _CHAT_MAX_ACTIONS:
            break
        if not isinstance(a, dict):
            continue
        atype = str(a.get("type") or "").strip()
        if atype not in _VALID_ACTION_TYPES:
            continue
        field_key = str(a.get("field_key") or "").strip()
        if not field_key or _is_reserved_card_field(field_key):
            continue
        reason = a.get("reason")
        reason_str = str(reason).strip() if isinstance(reason, str) else ""
        entry: dict[str, Any] = {"type": atype, "field_key": field_key}
        if reason_str:
            entry["reason"] = reason_str[:300]
        if atype == "remove_field":
            cleaned.append(entry)
            continue
        # Both refine and add require value.
        v = a.get("value")
        if isinstance(v, (list, tuple)):
            value = ", ".join(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, dict):
            try:
                value = json.dumps(v, ensure_ascii=False)
            except Exception:
                value = str(v)
        elif v is None:
            value = ""
        else:
            value = str(v).strip()
        if not value:
            continue
        if len(value) > _CHAT_MAX_FIELD_VALUE_CHARS:
            value = value[:_CHAT_MAX_FIELD_VALUE_CHARS] + "…"
        entry["value"] = value
        cleaned.append(entry)
    return cleaned


@router.post("/chat")
async def chat(request: Request):
    """Persistent companion-style chat. The assistant (default persona: YUI,
    swappable via ``dev_cat_name``) sees the current card + conversation
    history and replies with text + optional structured actions to apply."""
    try:
        body: Any = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"},
                            status_code=400)
    # As with clarify/generate/refine, reject non-object payloads (list/str/null, etc.),
    # or body.get(...) below raises AttributeError and returns 500.
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "invalid_json",
                             "message": "JSON body must be an object"},
                            status_code=400)

    rejected = _reject_untrusted_card_assist(request, body)
    if rejected is not None:
        return rejected

    history = _normalize_chat_history(body.get("messages"))
    if not history:
        return JSONResponse({"success": False, "error": "messages_required"},
                            status_code=400)

    lang = _resolve_language(body.get("locale"))
    locale_code = _resolve_locale_code(body.get("locale"))
    current_card = body.get("current_card")
    current_card_text = _format_card_for_prompt(current_card)
    target_keys = _resolve_target_keys(body, locale_code, current_card)
    target_keys_text = " / ".join(target_keys)
    latest_user = _latest_user_text(history)
    advice_only = (
        body.get("advice_only") is True
        or _chat_text_requests_advice_only(latest_user)
    )

    dev_cat_name = str(body.get("dev_cat_name") or _DEFAULT_DEV_CAT_NAME).strip()
    if not dev_cat_name or len(dev_cat_name) > 40:
        dev_cat_name = _DEFAULT_DEV_CAT_NAME

    system_template = get_card_assist_chat_system_prompt(lang)
    system_content = system_template % (
        dev_cat_name, current_card_text, target_keys_text
    )
    if advice_only:
        system_content += get_card_assist_chat_advice_only_directive(lang)
    # Use the target language for chat replies and action field values too (Codex #3331696257).
    system_content += _output_language_directive(locale_code)

    messages = [{"role": "system", "content": system_content}] + history

    content, err = await _invoke_assist_detailed(messages)
    if err is not None:
        return JSONResponse(
            err,
            status_code=502 if err.get("error") == "llm_call_failed" else 400,
        )

    warning: str | None = None
    try:
        parsed = _loads_json_lenient(content)
    except json.JSONDecodeError as exc:
        # The LLM sometimes forgets JSON mode and returns bare text. Do not fail the entire
        # request: return it unchanged as reply; if the user requested field edits,
        # provider-agnostic action recovery below attempts to supply actions.
        logger.warning("card-assist/chat: bad JSON from LLM: %s; raw[:200]=%s",
                       exc, (content or "")[:200])
        parsed = None
        warning = "llm_bad_json"

    reply = ""
    actions: list[dict] = []
    if isinstance(parsed, dict):
        raw_reply = parsed.get("reply")
        if isinstance(raw_reply, str):
            reply = raw_reply.strip()
        if len(reply) > _CHAT_MAX_MESSAGE_CHARS:
            reply = reply[:_CHAT_MAX_MESSAGE_CHARS] + "…"
        actions = _sanitize_actions(parsed.get("actions"))
        if advice_only:
            actions = []
    elif parsed is not None:
        warning = "llm_bad_shape"

    if not reply and content and not isinstance(parsed, dict):
        reply = (content or "")[:_CHAT_MAX_MESSAGE_CHARS]

    edit_intent = False if advice_only else _chat_text_requests_edits(latest_user)
    # Prefer the locale-independent flag passed through by the frontend's rewrite-whole-card quick action: regexes miss
    # localized rewrite wording (es/ja/ko/pt/ru/zh-TW), so _chat_text_requests_full_rewrite alone misclassifies it,
    # skipping _complete_full_rewrite_actions and saving partial actions as partial rewrites (Codex
    # #3333137718). Keep text heuristics too for manually typed full-rewrite requests.
    full_rewrite_intent = (not advice_only) and (
        body.get("full_rewrite") is True
        or _chat_text_requests_full_rewrite(latest_user)
    )

    # Include full_rewrite_intent in the recovery gate too: localized rewrite-whole-card quick-chip text (es/ja/ko/
    # pt/ru/zh-TW) does not match _CHAT_EDIT_INTENT_RE, leaving edit_intent=False. If the first LLM response also
    # has no usable actions (bare text / actions:[]), skipping _recover_actions_from_reply returns words
    # without editing the card, violating the explicit flag. _complete_full_rewrite_actions only completes existing actions
    # and cannot rescue an empty list. Trigger recovery whenever the flag is present too (Codex #3333394174).
    if (edit_intent or full_rewrite_intent) and not actions:
        actions = await _recover_actions_from_reply(
            lang=lang,
            locale_code=locale_code,
            user_instruction=latest_user,
            current_card_text=current_card_text,
            target_keys_text=target_keys_text,
            assistant_reply=reply or (content or ""),
        )

    if full_rewrite_intent and actions:
        actions = await _complete_full_rewrite_actions(
            lang=lang,
            locale_code=locale_code,
            actions=actions,
            user_instruction=latest_user,
            current_card=current_card,
            current_card_text=current_card_text,
            target_keys=target_keys,
        )

    if not reply and not actions:
        # The LLM returned neither reply nor actions: send frontend fallback text to avoid freezing the chat box.
        reply = get_card_assist_chat_empty_reply_fallback(lang)

    response_payload = {
        "success": True,
        "reply": reply,
        "actions": actions,
    }
    if warning:
        response_payload["warning"] = warning
    return JSONResponse(response_payload)
