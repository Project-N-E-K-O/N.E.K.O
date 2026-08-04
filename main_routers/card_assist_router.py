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
    get_card_assist_chat_system_prompt,
    get_card_assist_clarify_prompt,
    get_card_assist_generate_prompt,
    get_card_assist_refine_field_prompt,
)
from utils.language_utils import get_global_language
from utils.logger_config import get_module_logger

from .shared_state import get_config_manager
# 统一本地请求守卫（issue #1479）。system_router 不反向依赖 card_assist，无循环导入风险。
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


# `_resolve_locale_code` 的输出（角色卡模板文件名）→ (英文名, 本地名)。prompt 目前只写了
# zh / en 两版（见 _resolve_language），ja/ko/ru/pt/es 会落到 en、zh-TW 会落到简中。这些
# locale 如果不显式要求输出语言，助手就会用英文 / 简中提问、并把字段值也填成英文 / 简中
# （Codex #3331696257）。所以对这些 locale 追加一条输出语言指示。en / zh-CN 与基础 prompt
# 语言一致，不在表里（返回空指示）。
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
    # 注意：ainvoke / aclose 两个错误必须分开处理，否则 aclose 抛错时会把
    # 已经拿到的 resp 当成 llm_call_failed 丢掉。
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
        # aclose 失败不要影响这一次的结果，下次请求会拿新 client。
        logger.warning("card-assist: LLM aclose failed (ignored): %s", close_exc)
    content = (getattr(resp, "content", None) or "").strip()
    if not content:
        return None, {"success": False, "error": "llm_empty_response"}
    return content, None


async def _invoke_assist(prompt: Any) -> tuple[str | None, dict | None]:
    content, err = await _invoke_assist_detailed(prompt)
    return content, err


# 系统保留字段，对 LLM 来说都是噪声 / 不属于「角色设定」的部分。
# ⚠ 必须复用共享的 CHARACTER_RESERVED_FIELDS（角色编辑器、后端保存过滤
# `_filter_mutable_catgirl_fields` 都用它），不能再维护一份会漂移的部分拷贝——否则像
# `lighting` / `live3d_sub_type` / `vrm_animation` / `live2d_idle_animation` 这些 key 在
# chat/add_field 里被当普通字段渲染、autosave 报成功，但保存时又被过滤掉，刷新后行消失、
# 用户的改动静默丢失（Codex #3331668038）。在共享列表之外再补两个 card-assist 特有项：
#   - "档案名"：表单元数据 input 的固定 name（写死的中文 literal，非按 locale 翻译），
#     不在角色保留字段配置里，但同样不该让 AI 当普通设定去写。
#   - "live3d"：旧本地列表保留过的裸 key（共享配置只有 "live3d_sub_type"），保守起见留着。
# `_*` 前缀（如 `_reserved`）也一并跳过。
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
        body: Any = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)
    # request.json() 接受**任意合法 JSON**（list / str / int / null 都过），
    # 但下面所有 body.get(...) 都假设是 dict。非 object 直接打 400 不要让
    # AttributeError 飙到 500。
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
    # ja/ko/ru/pt/es/zh-TW 落到 en/简中 prompt 时，显式要求用目标语言提问（Codex #3331696257）
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
    # request.json() 接受**任意合法 JSON**（list / str / int / null 都过），
    # 但下面所有 body.get(...) 都假设是 dict。非 object 直接打 400 不要让
    # AttributeError 飙到 500。
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
    # 字段 key 已按 locale 模板给定，这里再要求字段 value 也用目标语言（Codex #3331696257）
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
    # 同时挡掉模型可能误吐回来的保留字段（"档案名"/"voice_id"/...），否则前端
    # 按 textarea[name=] 回写时会污染元数据/运行配置而不是普通角色设定。
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
    # request.json() 接受**任意合法 JSON**（list / str / int / null 都过），
    # 但下面所有 body.get(...) 都假设是 dict。非 object 直接打 400 不要让
    # AttributeError 飙到 500。
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
    # field_key 直接来自请求体，要和 _format_card_for_prompt / generate 的
    # 清洗保持一致 —— 别让客户端绕过来 refine "档案名"/"voice_id"/"system_prompt"。
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
    # 重生成的字段值也用目标语言（Codex #3331696257）
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
# /chat —— 持久陪伴聊天端点。
#
# 与 clarify/generate/refine 的「向导式」一锤子流不同，/chat 维护一段对话：
# 前端把 messages 历史 + 当前卡片状态 + 可用字段 key 一并发过来，LLM 扮演
# 「设定助手猫娘」(默认 YUI，后续会换开发猫) 回复用户，并在必要时输出
# 结构化 actions 让前端应用到表单。
# ============================================================================

# 客户端历史里允许的 role；其他 role（system / tool / function）都不放进去，
# system prompt 永远由后端按当前卡片状态重新构造。
_CHAT_HISTORY_ROLES = frozenset({"user", "assistant"})

# 历史轮数上限。聊得太多时只取尾部，避免上下文炸预算。
_CHAT_MAX_HISTORY_MESSAGES = 20

# 单条消息字符数上限。装设定的卡片字段会跟着 prompt 一起塞，所以这里把每条
# 单独的对话消息也限一下，给 system + card 的预算让位。
_CHAT_MAX_MESSAGE_CHARS = 2000

# 一次最多接受多少个 action。这是防 LLM「爽到一次性产出几十个 action 把用户设定冲掉」
# 的兜底，但不能低于一次合理的「全量重写」所需的动作数：默认模板就有 9 个可见字段
#（昵称/性别/年龄/种族/自称/核心特点/行为特点/厌恶/一句话台词），加上用户自建的自定义
# 字段，「重写全部」这类 quick action 会一字段一个 refine_field 地返回。原来卡在 8 会把第
# 9 个及之后**静默丢掉**、autosave 只落库半张卡（Codex #3328971304）。抬到 32：足够覆盖
# 默认 9 字段 + 充裕的自定义字段，又仍能拦住真正失控的超长 action 列表。
_CHAT_MAX_ACTIONS = 32

# 字段长度上限（refine_field / add_field 的 value）。和模板里手写的设定字
# 段长度大致对齐。
_CHAT_MAX_FIELD_VALUE_CHARS = 800

_VALID_ACTION_TYPES = frozenset({"refine_field", "add_field", "remove_field"})

# 「开发猫」的默认占位名，前端可在 payload.dev_cat_name 里覆盖。等真正的
# 开发猫角色 ready 后，前端会传那个名字过来。
_DEFAULT_DEV_CAT_NAME = "YUI"

# ⚠️ 这四条正则拿去撞**用户实际打出来的字**，所以每个中文词都要简繁并列。
# 这个功能本身是繁中已本地化的（见 _SUPPORTED_LOCALE_FILES / _LOCALE_OUTPUT_LANGUAGE
# 都有 zh-TW 键），也就是说用户确实会用繁体打字。
#
# ⚠️⚠️ EDIT 与 ADVICE_ONLY 必须**成对**收词：_chat_text_requests_advice_only 是
# 「命中 advice 且不命中 direct-edit」的 AND-NOT，而 :1119 又是
# `edit_intent = False if advice_only else ...`。单边补齐会把反转换个方向而不是
# 修好——繁中「給我一些修改建議」此前正是 edit=True/advice=False（「修改」简繁
# 同形而「建议/建議」不同形），系统直接改写用户的卡而不是给建议。
# 台湾用词：field 叫「欄位」不叫「字段」。
_CHAT_EDIT_INTENT_RE = re.compile(
    r"(修改|改写|改寫|重写|重寫|重生|重做|重新写|重新寫|调整|調整|补充|補充|新增|添加|"
    r"删除|刪除|移除|换一|換一|换成|換成|优化|優化|完善|梳理|设定|設定|字段|欄位|"
    r"rewrite|revise|regenerate|refine|update|change|"
    r"edit|add|remove|delete|replace|make\s+her|make\s+him)",
    re.IGNORECASE,
)

# ── 「整卡目标 + 的 + …」那个逃生口的三张表 ──────────────────────────
# ⚠️ 提成常量而不是写在正则字面量里：测试要按这几张表做笛卡尔积，写在字面量
# 里测试只能去 scrape 正则源码，正则一改结构测试当场 collection error。
#
# 全称限定词。闭集（汉语的全称限定词就这些）。
# ⚠️ 要**列全**。上一版只有 每个/每個，漏掉 每一个/每一個/每項/每项/各項/各项
# ——`把整個卡的每一個欄位都重寫` 是明确的整卡请求，被挡掉后整卡补全通路不
# 触发，只落库部分字段（Codex P2）。
_WHOLE_CARD_QUANTIFIERS = (
    "全部", "所有", "每一个", "每一個", "每个", "每個",
    "每一项", "每一項", "每项", "每項", "各项", "各項", "一切",
)
# 限定词管着的**整卡级名词**。
#
# ⚠️ 光有限定词是不够的。`把整个卡的全部名字重写` / `把整个卡的所有昵称重写`
# 里限定词管的是**单个字段名**——用户只想改那一个字段，却会被判成整卡重写，
# 由 `_complete_full_rewrite_actions` 给其余所有字段合成内容并 autosave，把
# 用户从没提过的数据静默覆盖掉（Codex P1，base 是 False）。
#
# ⚠️ 字段名那一侧不能拉黑：`_is_reserved_card_field` 之外的字段名是用户自建
# 的，开集，堵不完。所以正向要求「限定词管着一个整卡级名词」。
# 整卡级名词同样是开集，但代价不对称——漏一个只是少触发一次整卡补全（用户
# 再说一句就行），放行一个字段名会覆盖用户数据。所以只往安全侧列。
_WHOLE_CARD_SCOPE_NOUNS = (
    "设定", "設定", "设置", "設置", "资料", "資料", "人设", "人設",
    "描述", "内容", "內容", "字段", "欄位", "栏位", "数据", "數據",
    "文本", "文字", "文案",
    "信息", "資訊", "资讯", "属性", "屬性", "项目", "項目",
    "条目", "條目", "细节", "細節", "部分", "东西", "東西",
)
# 整卡级名词前面可以带的限定成分。
# ⚠️ 上面那条交替里本来就把 `所有可见字段` / `所有可見欄位` 当整卡目标，却没让
# 逃生口认得 `的每个可见字段`——于是 `把整个卡的每个可见字段重写` 掉了下来
# （Codex P2，base 是 True）。写成可选前缀而不是往名词表里塞四个合成词：它对
# 表里每个名词都成立（可見設定 / 可见内容 …）。
# ⚠️ 范围名词前面的限定语不只有「可见」：`所有现有字段` / `全部已有字段`
# base 都是 True（Codex P2 第四十九轮）。这一族是「已存在/可见」类属性限定，
# 都不改变「范围是整张卡」这件事。
_WHOLE_CARD_SCOPE_MODIFIER = (
    r"(?:(?:可见|可見|现有|現有|已有|既有|当前|當前)的?)?"
)
# ⚠️⚠️ 整卡级名词是**前缀匹配**，必须要求右边界，否则 `字段名` 会被当成 `字段`：
# `把整个卡的所有字段名重写` 说的是「把所有字段**名**改掉」，却会触发
# `_complete_full_rewrite_actions` 给每个字段合成**内容**并 autosave
# （Codex P1 第十二轮；base 也是 True，属这个 PR 要修的同一族破坏）。
#
# ⚠️ 但不能只加边界：`设定项` / `字段内容` 里的续接成分**仍然是范围名词**，
# 一刀切会把这类真整卡请求也挡掉。所以允许两种续接——量词化后缀（项/項/目/值）
# 和另一个整卡级名词（设定内容 / 属性值…），它们都在闭集里。
_WHOLE_CARD_SCOPE_SUFFIX = r"(?:项|項|目|值)"
# ⚠️⚠️ 「非汉字」不能把**拉丁字母/数字/下划线**算进去：自定义字段名可以叫
# nickname / field_name / age2，于是 `把整个卡的全部 nickname重写` 又成了一条
# 绕过单字段保险的后门（Codex P1 第十三轮）。合法的非汉字收尾只有**标点**。
# ⚠️⚠️ 收尾必须**正向白名单标点**，不能写成「非汉字」的否定类。
# 否定类挡掉拉丁之后还是把**其它所有文字**当标点：`把整个卡的全部 имя重写` /
# ` 이름重写` / ` ニックネーム重写`（ja 模板的字段名本来就是片假名）照样进整卡
# 补全通路（Codex P1 第十四轮；第十三轮那版只堵了拉丁，方向还是错的——
# 「不是汉字的东西」是开集，枚举**标点**才是闭集）。
_WHOLE_CARD_TERMINATOR_PUNCT = (
    # ⚠️ 全角空格 `　` **不在**这张表里：它是空白不是标点，`\s*` 已经会跳过它。
    # 收进来的话 `把整个卡的全部　名字重写` 又成了合法收尾——第四轮那条 P1 会
    # 当场回来（我第一版真收进去了，是配对用例把它逮住的）。
    # ⚠️ 只收**句读/成对符号/引号**。不要顺手把 `_ + / @ # 等` 也收进来：
    # 字段名可以叫 `_meta`、`@type`，收了它们等于给 P1 再开一扇门（第一版把 `_`
    # 收进去了，配对用例当场变红）。
    r"[，,。．.！!？?；;：:、…·~～—–\-「」『』（）()【】《》〈〉\[\]\"'`]"
)

# 限定词**自己当中心语**（`把整個卡的全部重寫一遍`，base 是 True）时的合法收尾：
# 重写动词首字 / 副词「都」/ 语气词 / 句末 / 标点等非汉字。闭集——字段名不长这样。
#
# ⚠️ 空白**不能**算合法收尾。上一版直接写 `[^一-鿿]`，空格也在里面，于是
# `把整个卡的全部 名字重写` 被判成整卡重写——同一句话不带空格时是正确的单字段
# 编辑，加个空格就走进 `_complete_full_rewrite_actions` 把其余字段全覆盖并
# autosave（Codex P1）。空白只能**跳过**，跳过之后仍然要落到真正的收尾上。
# ⚠️ 动词前面还可以夹**并列/强调副词**：`把整个卡的全部一并重写` /
# `把整個卡的全部一併重寫` / `把整个卡的全部彻底重写` base 都是 True，只认
# 「紧跟动词首字」会把它们挡掉（Codex P2 第十二轮）。副词是封闭词类，列干净；
# 单字段那条路不受影响——副词后面仍然要求重写动词首字，字段名进不来。
# ⚠️ 英文重写动词也是合法收尾：`把所有字段 rewrite` base 是 True，而第十四轮
# 把收尾收成「只认标点」时把它一起挡掉了（Codex P2 第二十二轮）。
# 这一侧安全——它是 _CHAT_REWRITE_VERB_RE 里的**闭集**，不像 `nickname` 那样是
# 任意字段名；`\b` 保证不会命中 `rewriteX` 这种更长的标识符。
# ⚠️ 右边界用 `(?![A-Za-z0-9_])` 而不是 `\b`：汉字也是 Unicode 词字符，`\b` 在
# `rewrite一下` 的 e/一 之间**不成立**，于是这类中英混写被挡掉了
# （Codex P2 第三十轮，base 是 True）。这里要拒的只是拉丁标识符的续接。
_WHOLE_CARD_EN_REWRITE_VERB = (
    r"(?:rewrite|revise|regenerate|redo|refresh)(?![A-Za-z0-9_])"
)
# ⚠️ 写成元组而不是手写正则：测试要按这张表自动派生用例、并断言它是**前缀码**
# （见下面 _WHOLE_CARD_ADVERB_RUN）。手写正则时这张表混进过一个重复的「统统」。
_WHOLE_CARD_BARE_ADVERBS = (
    "一并", "一併", "一起", "统统", "統統", "通通", "全都", "彻底", "徹底",
    "好好", "认真", "認真", "重新", "全面", "一律", "统一", "統一",
    "逐一", "逐个", "逐個", "挨个", "挨個", "再",
    # 全称限定词也能当副词用：`把所有字段全部重写`（base 是 True，Codex P2 第二十八轮）。
    "全部", "所有", "逐项", "逐項", "逐条", "逐條",
    # 批量类副词（Codex P2 第三十四轮，base 全是 True）。
    "批量", "依次", "各自", "挨着", "挨著", "一次性", "集中",
    # 总括/顺序类副词（Codex P2 第三十七轮，base 全是 True）。
    # ⚠️ **不收「分别/分別」**：它 base 就是 False，而且原因是 _CHAT_NEGATED_REWRITE_RE
    # 里的 `别|別`——`分别` 撞上了否定守卫。那条守卫的取舍写得很清楚：漏触发 =
    # 用户说「别改」却把整张卡改了并 autosave。为了一个 base 从来没成立过的说法
    # 去松动否定守卫，方向反了。
    "均", "依序", "一概", "悉数", "悉數", "分开", "分開",
)
# ⚠️ 轻动词「进行」占的是**跟副词同一个槽**（目标 + X + 重写动词）：
# `请对所有字段进行重写` / `请将所有字段全部进行重写` base 都是 True，上一版的收尾
# 白名单在真正的重写动词之前就把目标判掉了（Codex P2 第四十二轮）。
# ⚠️ 它跟副词可以**互相穿插**（全部进行重写 / 进行统一重写），所以直接并进那个
# `+` 循环，而不是在动词前面单加一节。词类不同，所以表分开列、正则合起来用。
_WHOLE_CARD_LIGHT_VERBS = ("进行", "進行")
_WHOLE_CARD_PREVERB_WORDS = _WHOLE_CARD_BARE_ADVERBS + _WHOLE_CARD_LIGHT_VERBS
_WHOLE_CARD_BARE_ADVERB = r"(?:" + "|".join(_WHOLE_CARD_PREVERB_WORDS) + r")"
# ⚠️ 副词可以**叠着用**：`把所有字段再统一重写` / `把整个卡的所有内容批量统一重写`
# base 都是 True，上一版只吃一个副词就要求重写动词，这些请求全掉了
# （Codex P2 第三十五轮）。
# ⚠️ 三张收尾表统一走这一条，别再各写一份——第二十九轮改两张漏一张，静默失效过。
# ⚠️ 这个 `+` 不会指数回溯：上面那张表是**前缀码**（没有哪个词是另一个词的前缀），
# 所以任何输入至多只有一种切分，失败时线性退出。测试直接断言这条性质，往表里
# 加会破坏前缀码的词（比如单独加个「一」）会当场见红。
# ⚠️ 也**不能**写成原子组：`重新` 的首字就是重写动词的首字，`统一重新` 只有回退
# 成「副词 统一 + 动词首字 重」才匹配得上，原子化会让这个 run 比原来的单副词
# 写法更窄——它必须是原来那一版的严格超集。
_WHOLE_CARD_ADVERB_RUN = r"(?:" + _WHOLE_CARD_BARE_ADVERB + r"地?\s*)+"

# 动量补语：`重写所有字段一遍` / `请重写所有字段两次` 里动词在**目标前面**，
# 目标后面跟的是 一遍/一次/一下（base 是 True，Codex P2 第三十一轮）。
# ⚠️ 它是**能产**的：一遍/两遍/三次/2遍/几遍… 数词是闭集、量词也是闭集，
# 所以写成「数词 + 量词」而不是逐个列成品（Codex P2 第三十三轮）。
# ⚠️ 两张表都提成常量给测试派生用——上一版量词是手写的 `遍|次|下|轮|輪|遭|回`，
# 测试那边人眼抄成六个、漏了「遭」，那一支被误删也不会见红（CodeRabbit）。
# ⚠️⚠️ 第三十三轮加这一支时只加进了 _WHOLE_CARD_SCOPE_NOUN_TAIL，另外两张收尾表
# 没有，于是 `重写所有字段的所有内容两遍` / `重写整个卡的全部两遍` 从 base 的 True
# 掉成 False（CodeRabbit Major）。**这已经是三张收尾表第三次漏改**（第二十九轮
# 英文动词漏一张、第三十五轮副词叠加漏三张），所以跟副词一样收成共用常量：
# 三张表现在只在「的」递归那一支上不同，其余判据全部共用同一个定义点。
# ⚠️ 数量成分不止确数：`重写所有字段多次` / `好几遍` / `若干次` / `数次` base 都是
# True（Codex P2 第三十九轮）。不定量词是闭集，跟确数并列成一支，别拿「多」去
# 扩数词字符类——那会让 `多` 混进 `十多` 这类组合里说不清。
_WHOLE_CARD_MEASURE_WORDS = ("遍", "次", "下", "轮", "輪", "遭", "回")
# ⚠️ 数词字符类要带**位值**：`一百遍` / `几百遍` / `千次` / `一万次` base 都是 True
# （Codex P2 第四十三轮）。位值字是闭集，一次列全，别一个一个补。
_WHOLE_CARD_NUMERAL_CHARS = "一二两兩三四五六七八九十百千万萬亿億零几幾半"
_WHOLE_CARD_INDEFINITE_QUANTITIES = (
    "好几", "好幾", "若干", "若幹", "许多", "許多", "数", "數", "多",
)
_WHOLE_CARD_MEASURE_COMPLEMENT = (
    r"(?:[" + _WHOLE_CARD_NUMERAL_CHARS + r"]+|\d+|"
    + "|".join(_WHOLE_CARD_INDEFINITE_QUANTITIES) + r")\s*(?:"
    + "|".join(_WHOLE_CARD_MEASURE_WORDS) + r")"
)

# 范围成分续接：`字段值` / `设定内容` / `属性值项`。
# ⚠️ 「可见/可見」修饰要能挂在**每一节**上：`把所有字段可见内容重写` base 是 True，
# 上一版只有「的」那一支带修饰、直接续接那三处都没有（Codex P2 第三十九轮）。
# ⚠️ 四处续接统一走这两条常量。这是三张收尾表之后的**第二个**「同一件事写四份」
# 的位置，别再各写一份——第二十九/三十五/三十六轮都在这种漂移上栽过。
# ⚠️ 原子化不能去掉：重叠解析会指数回溯（`项目` × 40 那条最坏用例）。
# ⚠️ 目标和范围名词之间可以隔一个**方位短语**：`重写所有字段里的内容` /
# `把全部欄位中的內容重寫`（base 都是 True，Codex P2 第四十五轮）。
# ⚠️ 方位词后面**仍然要求是范围名词**，所以单字段保险不受影响：
# `重写所有字段里的名字` 仍然是 False（base 是 True，本 PR 故意改掉）。
_WHOLE_CARD_SCOPE_LOCATIVE = (
    # ⚠️ 复合方位词（之中/之内/内部/里头）同族，Codex P2 第四十八轮。
    r"(?:(?:之中|之内|之內|内部|內部|里头|裡頭"
    r"|里面|裡面|里边|裡邊|当中|當中|里|裡|中|内|內)的?\s*)?"
)
# ⚠️ 方位短语和范围名词之间还能夹一个全称限定词：`重写所有字段里的全部内容` /
# `把全部欄位裡的每個內容重寫`（base 都是 True，Codex P2 第四十六轮）。
# 「的」那一支早就允许了，方位这一支上一轮漏了——限定词表直接复用，别另抄。
# ⚠️ 限定词后面**仍然要求是范围名词**，`重写所有字段里的全部名字` 照旧是 False。
_WHOLE_CARD_SCOPE_QUANTIFIER = (
    r"(?:(?:" + "|".join(_WHOLE_CARD_QUANTIFIERS) + r")\s*的?\s*)?"
)
_WHOLE_CARD_SCOPE_RUN_BODY = (
    r"(?:\s*" + _WHOLE_CARD_SCOPE_LOCATIVE + _WHOLE_CARD_SCOPE_QUANTIFIER
    + _WHOLE_CARD_SCOPE_MODIFIER
    + r"(?:" + _WHOLE_CARD_SCOPE_SUFFIX + "|"
    + "|".join(_WHOLE_CARD_SCOPE_NOUNS) + r"))"
)
# 子句续接：目标说完之后接一个并列/承接连词再讲下一件事。
# `请重写所有字段并保存` / `重写所有字段然后告诉我` base 都是 True，上一版的收尾
# 白名单把它们连同目标一起判掉了（Codex P2 第四十三轮）。
# ⚠️⚠️ **不收裸的「后/後」**，虽然 reviewer 举的例子里有 `重写所有字段后发给我`：
# 收了它 `重写所有字段后缀` 会一起放行，而那正是这个 PR 要修的单字段破坏本体
# （base 是 True，本 PR 故意改成 False）。`后` 后面是动词还是名词是开集，分不干净，
# 所以取安全那一侧——用 之后/然后 的说法仍然走得通，少认一种说法只是少补几个字段。
# ⚠️ 写成元组：这张表已经被扩了两轮，测试要按它自动派生用例。
_WHOLE_CARD_CLAUSE_CONTINUATIONS = (
    "并且", "並且", "然后", "然後", "之后", "之後", "接着", "接著", "以及",
    # 省略连词的承接词（Codex P2 第四十七轮，base 全是 True）。
    # ⚠️ 它们都是**两字词**，跟裸的「后」不同：`后缀` 那个坏例子撞不上。
    "随后", "隨後", "最后", "最後", "接下来", "接下來",
    "同时", "同時", "而且", "并", "並", "且",
)
# 结果短语收尾：`重写所有字段即可` / `重写全部字段就行`
# （base 全是 True，Codex P2 第五十轮）。动词在目标前面时，目标后面跟的就是这一类
# 「就行了」式的收尾。这一族是闭集。
_WHOLE_CARD_RESULT_PHRASES = (
    "即可", "就可以", "就行了", "就行", "就好了", "就好",
    "就成了", "就成", "便可", "可以了", "行了", "好了",
)
_WHOLE_CARD_RESULT_PHRASE = (
    r"(?:" + "|".join(_WHOLE_CARD_RESULT_PHRASES) + r")"
)
_WHOLE_CARD_CLAUSE_CONTINUATION = (
    r"(?:" + "|".join(_WHOLE_CARD_CLAUSE_CONTINUATIONS) + r")"
)
_WHOLE_CARD_SCOPE_RUN_OPT = r"(?>" + _WHOLE_CARD_SCOPE_RUN_BODY + r"*)"
_WHOLE_CARD_SCOPE_RUN_ONE = r"(?>" + _WHOLE_CARD_SCOPE_RUN_BODY + r"+)"

# 名词短语收尾：重写动词首字 / 副词「都」/ 语气词 / 句末 / 标点。与
# _WHOLE_CARD_BARE_QUANTIFIER_TAIL 同一套，空白同样只跳过不算收尾。
# ⚠️⚠️ 「的」是合法收尾，但**它后面必须再跟一个范围成分**。
# 上一版把裸「的」当收尾，于是 `把所有字段的名字重写`（要改的是字段**名**）
# 从这里绕过了单字段保险，照样触发整卡补全并 autosave（CodeRabbit Major）——
# 跟 `字段名` 是同一族，只是中间多了个「的」。
# `把所有字段的内容重写一遍` 仍然是 True：「内容」本身就是整卡级名词。
# ⚠️ 名词收尾同样要接受并列副词（`把所有字段一并重写`，base 是 True，
# Codex P2 第十四轮）——跟限定词那一支同一套副词表，两处别漂开。
# 名词短语的**闭集收尾**（不含「的」那一支，否则下面那条会无限递归）。
_WHOLE_CARD_SCOPE_NOUN_TAIL_CLOSE = (
    r"(?=\s*(?:$|"
    + _WHOLE_CARD_TERMINATOR_PUNCT
    + "|" + _WHOLE_CARD_EN_REWRITE_VERB
    + "|" + _WHOLE_CARD_ADVERB_RUN
    # ⚠️ 副词后面也可能是英文重写动词。第二十九轮改了另外**两张**收尾表，唯独
    # 漏了这一张——同一件事三处各写一份，漏一处就静默失效（CodeRabbit）。
    + r"(?:重|改|梳|完|" + _WHOLE_CARD_EN_REWRITE_VERB + r")"
    + r"|" + _WHOLE_CARD_MEASURE_COMPLEMENT
    + r"|" + _WHOLE_CARD_CLAUSE_CONTINUATION
    + r"|" + _WHOLE_CARD_RESULT_PHRASE
    + r"|重|改|梳|完|都|了|吧|啊|呀|呢|嘛|喔|哦|嗎|吗))"
)
_WHOLE_CARD_SCOPE_NOUN_TAIL = (
    r"(?=\s*(?:$|"
    + _WHOLE_CARD_TERMINATOR_PUNCT
    + "|" + _WHOLE_CARD_EN_REWRITE_VERB
    # ⚠️⚠️ 「的」这一支自己也要**递归到收尾**，不能只检查一个范围成分：
    # `把所有字段的内容概要重写` 里 `内容` 匹配上了、`概要` 没人管，于是又从这里
    # 进了整卡补全通路（CodeRabbit Major）。这是「白名单词是更长词的前缀」在本
    # PR 里的**第五个入口**——前四个是 字段名 / 字段清单 / 的名字 / 内容名。
    # 所以：吃掉一串范围成分（原子化，避免重叠解析），再要求那个闭集收尾。
    # ⚠️ 「的」和范围成分之间还能夹一个**全称限定词**：`把所有字段的所有内容重写`
    # base 是 True（Codex P2 第二十六轮）。限定词表就是上面那张闭集，直接复用。
    + r"|的\s*(?:(?:" + "|".join(_WHOLE_CARD_QUANTIFIERS) + r")\s*的?)?"
    # ⚠️ 嵌套范围也要允许「可见/可見」修饰（`把所有字段的可见内容重写`，
    # base 是 True，Codex P2 第三十一轮）——外层那一支早就允许了，这一支漏了。
    + _WHOLE_CARD_SCOPE_RUN_ONE
    + _WHOLE_CARD_SCOPE_NOUN_TAIL_CLOSE + r"|"
    + _WHOLE_CARD_ADVERB_RUN
    + r"(?:重|改|梳|完|" + _WHOLE_CARD_EN_REWRITE_VERB + r")"
    # 动量补语也是合法收尾（定义与三表共用见 _WHOLE_CARD_MEASURE_COMPLEMENT）。
    + r"|" + _WHOLE_CARD_MEASURE_COMPLEMENT
    + r"|" + _WHOLE_CARD_CLAUSE_CONTINUATION
    + r"|" + _WHOLE_CARD_RESULT_PHRASE
    + r"|重|改|梳|完|都|了|吧|啊|呀|呢|嘛|喔|哦|嗎|吗))"
)
_WHOLE_CARD_BARE_QUANTIFIER_TAIL = (
    r"\s*(?:$|" + _WHOLE_CARD_TERMINATOR_PUNCT
    + "|" + _WHOLE_CARD_EN_REWRITE_VERB + r"|"
    + _WHOLE_CARD_ADVERB_RUN
    # ⚠️ 副词后面也可能是**英文**重写动词：`把所有字段全部 rewrite`
    # （base 是 True，Codex P2 第二十九轮）。
    + r"(?:重|改|梳|完|" + _WHOLE_CARD_EN_REWRITE_VERB + r")"
    + r"|" + _WHOLE_CARD_MEASURE_COMPLEMENT
    + r"|" + _WHOLE_CARD_CLAUSE_CONTINUATION
    + r"|" + _WHOLE_CARD_RESULT_PHRASE
    + r"|重|改|梳|完|都|了|吧|啊|呀|呢|嘛|喔|哦|嗎|吗)"
)
# 紧贴「的」时**自己就代表整卡**的副词/普通名词（`重寫整個卡片的內容`）。
# ⚠️ 只在紧贴「的」时算数：`把整个卡的名字整体重写` 里「整体」修饰的是单字段。
_WHOLE_CARD_HEAD_NOUNS = ("整体", "整體", "内容", "內容")

_CHAT_FULL_REWRITE_RE = re.compile(
    # 「整个卡」是本来就缺的简体配对（表里原有「整個卡」但没有它），不是繁体
    # 补齐引入的——它既不是「整张卡」的子串，也不是「整个角色卡」的子串，所以
    # 简体用户用「个」当量词时这条一直匹配不到（CodeRabbit）。
    #
    # ⚠️ CJK 那组统一带 `(?!的)`：这些词后面跟「的」时是**定语**而不是重写目标。
    # 「把整個卡的名字重寫一下」只想改 name，却会被判成全量重写，进而由
    # `_complete_full_rewrite_actions` 给所有缺失字段合成内容、覆盖掉整张卡
    # （Codex P1）。繁体侧此前就有这个 bug，补简体「整个卡」时一并收口。
    # ⚠️ 「…卡片」是**完整的重写目标**，不是要排除的东西——`重写整个角色卡片`
    # 就是一次真正的整卡重写。所以把 卡片 各形式显式收进来，并**排在对应的
    # 「…卡」之前**（正则交替取最先匹配成功的分支）。上一版只加了 `(?![的片])`
    # 而没收 卡片，把这类请求全挡掉了（Codex P2 第三轮）。
    # 顺序：整个角色卡片 → 整个角色卡 → 整张卡片 → 整张卡 → 整个卡片 → 整个卡。
    # ⚠️ 「字段 / 欄位」那一组**不带** lookahead：`把所有字段的内容重写一遍` 是
    # 真正的整卡重写请求，「的」后面跟的是内容而不是某个单一字段（Codex P2）。
    # 只有「卡」类目标才需要挡定语——那里的「的」意味着只改卡的某一个属性。
    # ⚠️ 这一组同样是**前缀匹配**：`所有字段` 是 `所有字段名` 的前缀，
    # `把整个卡的所有字段名重写` 会从这里进整卡补全通路（Codex P1 第十二轮）。
    # 用与整卡级名词同一套的右边界收口。
    rf"((?:所有可见字段|所有可見欄位|全部可见字段|全部可見欄位|所有字段|所有欄位|"
    rf"全部字段|全部欄位|每个字段|每個欄位)"
    # ⚠️ 跟整卡级名词那一支同一套：先吃掉合法的范围续接（值/项/内容…）再判边界，
    # 否则 `把所有字段值重写` / `重写全部字段内容` 被自己的边界挡掉
    # （Codex P2 第十三轮，base 是 True）。
    # ⚠️ 续接前面也可能有空白（`把所有字段 内容重写`，base 是 True）。
    rf"{_WHOLE_CARD_SCOPE_RUN_OPT}"
    rf"{_WHOLE_CARD_SCOPE_NOUN_TAIL}"
    r"|(?:整个角色卡片|整個角色卡片|整个角色卡|整個角色卡|"
    r"整张卡片|整張卡片|整张卡|整張卡|"
    # lookahead 仍是 `(?![的片])` 而不是 `(?!的)`：正则交替会回溯，
    # `重写整个卡片的名字` 在 整个卡片 因「的」失败后会退到 整个卡 分支，那时后面
    # 跟的是「片」——只挡「的」的话它照样匹配上。两个都留才对：
    #   整个卡片        → 整个卡片 命中，后面是句尾        → True
    #   整个卡片的名字   → 整个卡片 被「的」挡，退到 整个卡 被「片」挡 → False
    #   整个卡          → 命中                          → True
    #   整个卡的名字     → 被「的」挡                     → False
    # ⚠️ `(?![的片])` 之外再开一个口子：「整卡目标 + 的 + 全量限定词 + 整卡级
    # 名词」仍然是整卡重写，只有「…的名字」这类单字段定语才该被挡。
    #
    # ⚠️⚠️ 口子的判据是**限定词 + 它管着的中心语**，两半都要。
    # 只看「的」后面那个名词叫什么不行：上一版只白名单了「内容」，于是
    # `把整個角色卡的全部設定重寫一遍` 被挡掉，整卡补全通路不触发、只落库半
    # 张卡。只看限定词也不行：`把整个卡的全部名字重写` 里限定词管的是单字段
    # 「名字」，却被判成整卡重写、把其余字段全覆盖（Codex P1，base 是 False）。
    # 两张表分别见 _WHOLE_CARD_QUANTIFIERS / _WHOLE_CARD_SCOPE_NOUNS。
    #
    # ⚠️⚠️ 限定词必须**紧贴「的」**，不能给它一个浮动窗口。
    #
    # 窗口版本连着被判了三次 P1，每次都是同一个破坏面：`把整個卡的名字整體重寫`
    # / `把整个卡的名字全部重写` 里，限定词修饰的是单字段「名字」，窗口却跨过它
    # 匹配上了，于是整句被判成整卡重写，_complete_full_rewrite_actions 给**其余
    # 所有字段**合成重写动作，把用户从没提过的内容静默覆盖掉。
    #
    # 试过靠「限定词按语法分布二分」（全称限定词允许后置浮动、副词/普通名词不
    # 允许）——挡住了 整体/内容 那一半，`的名字全部重写` 照样漏。也试过用卡片
    # 字段名当闭集去区分「名字」和「設定」，但 _is_reserved 表明卡片允许任意
    # 自定义字段名，字段名是开集。
    #
    # ⚠️ 所以这里选的是**取舍**而不是又一个更精巧的正则：
    #   · 过度触发 = 静默覆盖用户没要求改的字段（破坏性，还会 autosave 落库）
    #   · 触发不足 = 少几个字段没被自动补全，用户再说一句就行
    #   两者代价不对称，宁可触发不足。
    #
    # 代价是「…的設定全部重寫」这种**语序倒置**的说法不再触发整卡补全（模型
    # 仍会照用户原话改设定，只是不跑补全那一趟）。而 Codex 当初要求修的
    # `把整個角色卡的全部設定重寫一遍` 里「的全部」本来就紧贴「的」，不靠窗口
    # 也命中——窗口从头到尾只多救了语序倒置这一种较少见的说法。
    r"整个卡片|整個卡片|整个卡|整個卡|全卡)"
    # ⚠️⚠️ 目标必须是**完整的词**。`整个卡` 同时是 整个卡通 / 整个卡组 /
    # 整个卡牌 的前缀，于是 `把整个卡通角色的名字重写` 触发整卡补全、把其余
    # 字段全覆盖掉（Codex P1；简体 base 是 False，我引入的——本 PR 才加的
    # 简体目标 整个卡 / 整个卡片）。
    #
    # ⚠️ 不能靠拉黑续接字（通/组/牌/座/车…是开集）。正向要求：目标后面必须是
    # 句末、非汉字、结构助词「的」，或者一个**重写动词的首字**——重写动词表
    # 就在下面 _CHAT_REWRITE_VERB_RE 里，是闭集。
    # ⚠️ 语气词也是完整目标的合法收尾。上一版只放行「的 + 重写动词首字」，
    # 于是 `重寫整個卡吧` / `重寫整個卡啊` 被判成不是整卡请求（base 是 True）。
    # 语气词是封闭词类，跟重写动词表一样可以列干净。
    r"(?=$|[^一-鿿]|的|重|改|梳|完|全|都|了|吧|啊|呀|呢|嘛|喔|哦|嗎|吗)"
    # 口子一：的 + 全称限定词 + （整卡级名词 | 限定词自己当中心语的合法收尾）。
    # 口子二：的 + 紧贴着当中心语就代表整卡的那几个词。
    # ⚠️ 限定词和中心语之间可以有结构助词「的」：`把整个卡的所有的字段重写`
    # 是最自然的说法之一，漏了它整卡补全不触发（Codex P2，base 是 True）。
    # 单字段那道保险不受影响——`把整个卡的所有的名字重写` 仍然是 False，因为
    # 「名字」照样不在整卡级名词表里。
    # ⚠️ 目标和「的」之间也可能有空白：`把整个卡 的名字重写` 里 `(?![的片])` 只看
    # 一个字符、看到的是空格，于是整句被判成整卡重写、覆盖用户没要求改的字段
    # （CodeRabbit）。三处都要跳过空白，否则「加个空格」就是一条绕过保险的后门。
    rf"(?:(?=\s*的(?:{'|'.join(_WHOLE_CARD_QUANTIFIERS)})"
    # ⚠️ 空白只在**中心语确实是整卡级名词**时才跳过（`把整个卡的所有 字段重写`
    # base 是 True）。单字段那道保险不受影响：`把整个卡的全部 名字重写` 里跳过
    # 空白之后「名字」照样不在整卡级名词表里，而限定词自己当中心语那一支有它
    # 自己的收尾要求（见 _WHOLE_CARD_BARE_QUANTIFIER_TAIL）。
    rf"(?:\s*的?\s*{_WHOLE_CARD_SCOPE_MODIFIER}"
    # ⚠️ 范围后缀（值/项/目）也能**自己当中心语**：`把整个卡的全部值都重写`
    # base 是 True，只让它当续接会把这类挡掉（Codex P2 第二十轮）。
    # 单字段保险不受影响——`名字` 既不是整卡级名词也不是范围后缀。
    rf"(?:{'|'.join(_WHOLE_CARD_SCOPE_NOUNS)}|{_WHOLE_CARD_SCOPE_SUFFIX})"
    rf"{_WHOLE_CARD_SCOPE_RUN_OPT}"
    rf"{_WHOLE_CARD_SCOPE_NOUN_TAIL}"
    rf"|{_WHOLE_CARD_BARE_QUANTIFIER_TAIL}))"
    # ⚠️ 头部名词那一支同样是前缀匹配，也要右边界：`把整个卡的内容名重写` /
    # `的内容概要重写` 会从这里进整卡补全通路（CodeRabbit Major）。
    # ⚠️ 头部名词后面同样允许先吃掉范围成分再判边界：`把整个卡的内容设定重写`
    # base 是 True（Codex P2 第二十轮）。`的内容名` 仍然被挡——「名」既不是范围
    # 后缀也不是整卡级名词。
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


# ⚠️ 否定的整卡重写请求**不能**走整卡补全通路——那是本 PR 里破坏性最强的一条
# 路径（_complete_full_rewrite_actions 会给每个缺失字段合成内容并 autosave）。
# `不要重写整个卡` 同时满足整卡目标和重写动词两条谓词。否定词是**封闭类虚词**，
# 可以列干净；窗口 0-4 个非标点字符，保证「不要」管不到逗号后面。
# ⚠️ 撇号有三种写法：ASCII `'`、iOS/macOS/Word 会自动替换成的 U+2019 `’`、
# 以及 U+02BC `ʼ`。只认 ASCII 那个时 `don’t rewrite the whole card` 从否定守卫
# 底下漏过去，直接触发整卡补全并 autosave——覆盖用户**明确说了不要动**的字段
# （CodeRabbit Major）。
# ⚠️ base 也是 True，**不是**本 PR 的回归；但方向是危险的那一侧、改动只有一个
# 字符类，就一起修了。music_requests.py 的英文否定同一处理，两边别漂开。
# ⚠️ 写成**字符集合**而不是现成的 `[...]`：下面 `n[o…]t` 那一支要把 o 和三个撇号
# 放进同一个类里。第一版另开了个 `don[…]t` 分支，结果 `n[o…]t` 早就覆盖了它，
# 变异「把这个常量收回 ASCII」照样全绿——两条写法重叠时常量根本不受力。
_EN_APOSTROPHE_CHARS = "'’ʼ"
# 否定/禁止词本身（不带后面那个重写动词）。
# ⚠️ 提成单独常量是为了让 _chat_clause_without_quotes 判断「这段引号里到底有没有
# 禁止」，两处必须同源——另抄一张表就是下一个漂移点。
_CHAT_NEGATION_LEXEME = (
    # ⚠️ 否定/禁止是**封闭词类**，一次列全，不要被 reviewer 一个一个揪。
    # greptile 只报了「不准」，实测同时漏的还有 不許/不许/禁止/嚴禁/严禁/
    # 休要/不得/莫——逐个补是打地鼠，这一维本来就可以枚举干净。
    r"(?:不要|不用|不需要|不必|不想|不准|不準|不許|不许|不得|不可|不能"
    r"|别|別|甭|莫|休要|先不|暫不|暂不|暫時不|暂时不"
    r"|無需|无需|勿|切勿|請勿|请勿|禁止|嚴禁|严禁"
    # ⚠️ 英文否定同样要收——整卡目标和重写动词那两张表本来就含英文分支
    # （rewrite/regenerate/all fields），只有否定守卫是纯中文，于是
    # `don't rewrite the whole card` 直接绕过去了（CodeRabbit）。
    # `do\s*n[o…]t` 一支就盖住 do not / don't / don’t / donʼt / do n’t；
    # `dont`（整个撇号都不打）中间没有字符，只能单列。
    rf"|(?:do\s*n[o{_EN_APOSTROPHE_CHARS}]t|dont|never|no\s+need\s+to"
    rf"|please\s+do\s*n[o{_EN_APOSTROPHE_CHARS}]t)\s*)"
)
_CHAT_NEGATED_REWRITE_LEXEME_RE = re.compile(_CHAT_NEGATION_LEXEME, re.IGNORECASE)
_CHAT_NEGATED_REWRITE_RE = re.compile(
    _CHAT_NEGATION_LEXEME +
    # ⚠️ 窗口要盖住整个宾语短语。`請勿把整個角色卡全部重寫` 里 請勿 和 重寫
    # 之间隔了八个字，{0,4} 够不着。
    # 这里放宽是**安全方向**：否定守卫误触发 = 整卡补全不跑（少补几个字段），
    # 漏触发 = 用户说「别改」却把整张卡改了并 autosave。两者代价不对称。
    # ⚠️⚠️ 这里**不能**是固定长度窗口。{0,4} 盖不住宾语短语，放宽到 {0,12}
    # 又被更长的句子绕过，再放宽到 {0,24} 只是把门槛往后推一格——宾语短语
    # 可以任意长，这一路没有终点（reviewer 连着报了三次）。
    #
    # 真正的上界是**子句**：否定词管到句读为止。所以窗口改成「不跨句读的
    # 任意长度」，也就是下面 _CHAT_CLAUSE_SPLIT_RE 那张标点表的补集。
    # ⚠️ 两处必须同源，否则「否定只在自己子句内生效」在两个地方含义不一样。
    r"[^。，、！？,.!?;；]*?"
    # ⚠️ 动词侧也要收英文。整卡目标和重写动词那两张表本来就有英文分支，
    # 只补否定词而不补动词，`don't rewrite the whole card` 照样绕过去。
    r"(?:重写|重寫|重新写|重新寫|改写|改寫|重做|重生|梳理|完善"
    r"|rewrite|revise|regenerate|redo|refresh|change|update)",
    # ⚠️ 整卡目标和重写动词那两条正则都带 re.IGNORECASE，否定守卫漏了就是
    # 单边不对称：`Don't rewrite the whole card` 满足两条正向谓词却躲过守卫，
    # 直接走进整卡补全通路（Codex P1）。三条谓词的大小写口径必须一致。
    re.IGNORECASE,
)


# 子句边界。和 _CHAT_NEGATED_REWRITE_RE 里那个「不许跨过」的字符类是同一张表。
_CHAT_CLAUSE_SPLIT_RE = re.compile(r"[。，、！？,.!?;；]+")


def _chat_clauses(text: str) -> list[str]:
    """按句读把整段文本切成子句。"""  # noqa: DOCSTRING_CJK
    return [c for c in _CHAT_CLAUSE_SPLIT_RE.split(text or "") if c.strip()]


# ⚠️ 引号里的内容是**被引用的素材**，不是对我们下的指令。
# `Use “Don’t Panic” as the theme and rewrite all fields` 里的 `Don’t` 是歌名的
# 一部分，否定守卫却把它当成「别重写」，整卡补全被跳过（Codex P2 第四十二轮）。
# ⚠️ 这不只是撇号那一版引进的：ASCII 的 `"Don't Panic"` 在 base 上就已经是 False，
# 只是没人报过。所以修的是**判据形状**——查否定之前先把引用跨度抹掉。
# ⚠️ 只给否定守卫抹，整卡目标和重写动词仍然看原文：目标写在引号里
# （`把《整个卡》重写`）时抹掉会把它一起弄丢。
# ⚠️ **不收 ASCII 单引号和左单引号**：`Don't Stop Believin'` 里它们是撇号不是引号，
# 收了会把 `'t Stop Believin'` 当成一段引用、把真正的否定词抹掉——那是危险方向。
# music_requests 的 _ZH_AMBIGUOUS_QUOTE_OPENERS 是同一条取舍。
_CHAT_QUOTED_SPAN_RE = re.compile(
    r"“[^”]*”|「[^」]*」|『[^』]*』|《[^》]*》|【[^】]*】|\"[^\"]*\""
)


def _chat_span_carries_a_prohibition(span: str) -> bool:
    """这一段引号里带着禁止吗？——只用于**正向信号**，否定守卫永远看原文。

    ⚠️⚠️ 这里的判据经历了三轮反复，最后收在**安全那一侧**，别再往回改：

    * 第四十二轮：为了让 `Use “Don’t Panic” as the theme and rewrite all fields`
      不被歌名里的 `Don’t` 挡住，把引用跨度从否定守卫里抹掉；
    * 第四十三轮 P1：`请“不要重写”所有字段` 的禁止被抹没了 → 改成「引号里含重写
      动词就是指令」；
    * 第四十七轮 P1：`Please “do not” rewrite all fields` 又漏 → 改成「去掉否定词
      还剩别的字才算素材」；
    * 第四十八轮 P1：`请“千万不要”重写所有字段` / `Please “do not ever” …` 里
      「千万」「ever」就是那个「剩下的字」，再次漏掉。

    **三轮三个新形状，说明这条线划不出来**：引号里是歌名还是被强调的禁止，
    句法上完全同形，只有语义能分。而两个方向的代价差着量级——放过一个被引用的
    歌名只是少补几个字段，放过一个真禁止是把用户明说不要动的数据覆盖掉并 autosave。

    所以停在这里：**否定守卫读原文，引号里的禁止一律算数**。引号只对正向信号
    生效——被引用的禁止不该顺便提供整卡目标（第四十四轮 P1 的
    `Use “do not touch all fields” as an example and rewrite the title`）。
    代价是 `“Don’t Panic”` 那条仍然少触发一次整卡补全，那是轻的一侧，接受。
    """  # noqa: DOCSTRING_CJK
    return bool(_CHAT_NEGATED_REWRITE_LEXEME_RE.search(span))


def _chat_clause_without_quoted_prohibitions(clause: str) -> str:
    """把**带着禁止的引用跨度**换成空格，只用于正向信号。

    被引用的禁止不该顺便提供整卡目标：
    `Use “do not touch all fields” as an example and rewrite the title`
    里的 `all fields` 在引号里，配上引号外的单字段 `rewrite` 不能算整卡请求
    （Codex P1 第四十四轮）。

    ⚠️ 没有禁止的引用（`把《整个卡》重写` / `把「所有字段」重写`）不抹：
    用户就是用引号强调目标，base 上那些说法都是 True。
    ⚠️ **否定守卫不用这份文本**，它永远读原句——理由见
    _chat_span_carries_a_prohibition 的注释。
    """  # noqa: DOCSTRING_CJK
    return _CHAT_QUOTED_SPAN_RE.sub(
        lambda m: " " if _chat_span_carries_a_prohibition(m.group(0)) else m.group(0),
        clause,
    )


def _chat_text_requests_full_rewrite(text: str) -> bool:
    """整卡重写判据——三条谓词必须落在**同一个子句**里。

    ⚠️ 上一版对整段文本分别 search 整卡目标 / 重写动词 / 否定守卫再组合。
    那个形状同时产出三条互相矛盾的缺陷，根因是同一个——判据作用在整段文本上：

    * 否定守卫靠固定长度窗口连接否定词和重写动词，够不着长宾语。窗口从
      {0,4} 放宽到 {0,12} 再到 {0,24}，每次都被更长的句子绕过；
    * 否定守卫是**全局早退**，一个子句里的「不用」把另一个子句里明确的整卡
      请求也一起否掉（`名字不用重寫，但請重寫整個卡`）；
    * 整卡目标和重写动词可以分属**不同子句**却被组合起来
      （`先展示整个卡，然后重写名字` 里「整个卡」是「展示」的宾语）。

    按子句求值一次解掉三条：切分让跨子句的信号不再相遇，否定只在自己所在的
    子句内生效，而「同子句」这个天然上界取代了那个永远不够长的固定窗口。

    ⚠️ 代价方向仍然是**宁可触发不足**：过度触发会让
    `_complete_full_rewrite_actions` 给每个缺失字段合成内容并 autosave，覆盖
    用户没要求改的数据；触发不足只是少补几个字段。所以判据是「**任一**子句
    同时满足三条」，而不是把散落各处的信号在全段上拼起来。
    """  # noqa: DOCSTRING_CJK
    if not text:
        return False
    for clause in _chat_clauses(text):
        # ⚠️ 三条谓词读的必须是**同一份文本**：抹掉的那段引用对否定和正向信号
        # 一视同仁。上一版只从否定守卫里抹，正向信号照读原文，于是引号里的
        # `all fields` 配上引号外的单字段 `rewrite` 进了整卡补全通路
        # （Codex P1 第四十四轮）。
        # ⚠️ 否定守卫读**原句**：引号里的禁止一律算数。
        if _CHAT_NEGATED_REWRITE_RE.search(clause):
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
    """
    if lang == "zh":
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
    # 只保留最近的 N 条，但确保以 user 收尾 —— 否则后面一条 LLM 看到的最后一
    # 句话是 assistant，会迷茫不知道要回什么。
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
        # refine / add 都需要 value
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
    # 同 clarify/generate/refine：拒绝非 object payload（list/str/null 等），
    # 否则下面 body.get(...) 会 AttributeError 飙到 500。
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
    # 聊天回复 + actions 里的字段值也用目标语言（Codex #3331696257）
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
        # LLM 偶尔会忘记是 JSON 模式，吐回来一段裸的纯文本。这种情况下也别
        # 整个请求挂掉 —— 把它原样当 reply 返回；如果用户确实要求改字段，
        # 后面的 provider-agnostic action recovery 会尝试补 actions。
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
    # 前端「重写整张卡」quick action 透传的 locale 无关 flag 优先——本地化文案（es/ja/ko/pt/
    # ru/zh-TW 的「重写」措辞）正则匹配不到，只靠 _chat_text_requests_full_rewrite 会漏判，
    # _complete_full_rewrite_actions 补全通路不触发、部分 action 被当部分重写存下（Codex
    # #3333137718）。同时保留文本启发式，兼容用户手敲的全量重写措辞。
    full_rewrite_intent = (not advice_only) and (
        body.get("full_rewrite") is True
        or _chat_text_requests_full_rewrite(latest_user)
    )

    # recovery gate 也要带上 full_rewrite_intent：本地化「重写整张卡」quick chip（es/ja/ko/
    # pt/ru/zh-TW）的文本 _CHAT_EDIT_INTENT_RE 匹配不到、edit_intent 为 False，若首轮 LLM 又
    # 没吐出可用 actions（纯文本 / actions:[]），不走 _recover_actions_from_reply 就只回一句
    # 话、卡一点没改，辜负了那个显式 flag；而 _complete_full_rewrite_actions 只补全已有 actions、
    # actions 为空时也救不回来。所以 flag 在场时一并触发恢复（Codex #3333394174）。
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
        # LLM 既没回话也没动作 —— 给前端一个兜底文案，不然聊天框就僵住了。
        reply = ("（嗯…我没想好怎么回，能再说一遍喵？）" if lang == "zh"
                 else "(Hmm... I'm not sure how to reply — could you say that again?)")

    response_payload = {
        "success": True,
        "reply": reply,
        "actions": actions,
    }
    if warning:
        response_payload["warning"] = warning
    return JSONResponse(response_payload)
