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

from config import CHARACTER_RESERVED_FIELDS
from config.prompts.prompts_card_assist import (
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
    """本地 Origin/CSRF 守卫：card-assist 这四个 POST 都会真去打用户配置的对话/辅助
    LLM、消耗其 API / 免费额度，属于「有副作用的浏览器侧请求」，必须和仓库里其它此类
    端点一样先过统一守卫，挡掉恶意网页用 ``no-cors`` + ``text/plain`` body 伪造合法 JSON
    偷跑配额——攻击者读不到响应，但不拦就能白嫖配额（Codex #3328998416）。

    复用 ``_validate_local_mutation_request``：返回 ``None`` 放行；返回 403
    JSONResponse(``error_code=csrf_validation_failed``) 表示拒绝，调用方原样 return 即可。
    payload 仅用于 body 内 ``_csrf_token`` 兜底，非 dict 传 None 避免 ``.get`` 抛错。"""
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

# Free Lanlan text API expects the generic section watermark used by other
# prompt paths. Keep this fixed Chinese marker; it is metadata, not UI text.
_FREE_API_WATERMARK = "======以下为安全水印======\n这是最通用的水印\n======以上为安全水印======"


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


def _is_free_assist_config(api_cfg: dict | None) -> bool:
    """Return True for the bundled Lanlan free text API profile."""
    if not isinstance(api_cfg, dict):
        return False
    model = str(api_cfg.get("model") or "").strip().lower()
    base_url = str(api_cfg.get("base_url") or "").strip().lower()
    api_key = str(api_cfg.get("api_key") or "").strip().lower()
    return (
        model == "free-model"
        or api_key == "free-access"
        or "lanlan.tech" in base_url
    )


def _with_free_api_watermark(prompt: Any) -> Any:
    """Attach the generic free-API watermark without changing prompt shape."""
    if isinstance(prompt, str):
        if _FREE_API_WATERMARK in prompt:
            return prompt
        return prompt.rstrip() + "\n\n" + _FREE_API_WATERMARK
    if not isinstance(prompt, list):
        return prompt

    messages: list[Any] = []
    inserted = False
    for msg in prompt:
        if not isinstance(msg, dict):
            messages.append(msg)
            continue
        copied = dict(msg)
        content = copied.get("content")
        if not inserted and copied.get("role") == "system" and isinstance(content, str):
            if _FREE_API_WATERMARK not in content:
                copied["content"] = content.rstrip() + "\n\n" + _FREE_API_WATERMARK
            inserted = True
        messages.append(copied)
    if not inserted:
        messages.append({"role": "system", "content": _FREE_API_WATERMARK})
    return messages


def _build_assist_llm():
    """Construct an LLM client backed by the assist API config. Returns
    ``(llm, error_dict_or_None, is_free_api)``. Caller must
    ``await llm.aclose()`` if llm is not None.
    """
    from utils.llm_client import create_chat_llm
    try:
        cm = get_config_manager()
        api_cfg = cm.get_model_api_config("conversation")
    except Exception as exc:
        logger.warning("card-assist: failed to read assist API config: %s", exc)
        return None, {"success": False, "error": "assist_api_not_configured",
                      "message": str(exc)}, False
    api_key = (api_cfg or {}).get("api_key")
    model = (api_cfg or {}).get("model")
    base_url = (api_cfg or {}).get("base_url")
    is_free_api = _is_free_assist_config(api_cfg)
    if not model:
        return None, {"success": False, "error": "assist_api_not_configured",
                      "message": "assist model not set"}, is_free_api
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
                      "message": str(exc)}, is_free_api
    return llm, None, is_free_api


async def _invoke_assist(prompt: Any) -> tuple[str | None, dict | None]:
    """Run a single-shot call against the assist LLM. ``prompt`` may be either
    a plain string (treated as one user message) or a list of OpenAI-style
    role/content dicts. Returns ``(content_or_None, error_dict_or_None)``.
    """
    llm, err, is_free_api = _build_assist_llm()
    if err is not None:
        return None, err
    if is_free_api:
        prompt = _with_free_api_watermark(prompt)
    # 注意：ainvoke / aclose 两个错误必须分开处理，否则 aclose 抛错时会把
    # 已经拿到的 resp 当成 llm_call_failed 丢掉。
    try:
        resp = await llm.ainvoke(prompt)
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

    dev_cat_name = str(body.get("dev_cat_name") or _DEFAULT_DEV_CAT_NAME).strip()
    if not dev_cat_name or len(dev_cat_name) > 40:
        dev_cat_name = _DEFAULT_DEV_CAT_NAME

    system_template = get_card_assist_chat_system_prompt(lang)
    system_content = system_template % (
        dev_cat_name, current_card_text, target_keys_text
    )

    messages = [{"role": "system", "content": system_content}] + history

    content, err = await _invoke_assist(messages)
    if err is not None:
        return JSONResponse(
            err,
            status_code=502 if err.get("error") == "llm_call_failed" else 400,
        )

    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as exc:
        # LLM 偶尔会忘记是 JSON 模式，吐回来一段裸的纯文本。这种情况下也别
        # 整个请求挂掉 —— 把它原样当 reply 返回，actions 留空，用户至少能
        # 看到一句回复。
        logger.warning("card-assist/chat: bad JSON from LLM: %s; raw[:200]=%s",
                       exc, content[:200])
        return JSONResponse({
            "success": True,
            "reply": content[:_CHAT_MAX_MESSAGE_CHARS],
            "actions": [],
            "warning": "llm_bad_json",
        })

    if not isinstance(parsed, dict):
        return JSONResponse({"success": False, "error": "llm_bad_shape",
                             "raw": content[:500]}, status_code=502)

    reply = parsed.get("reply")
    if not isinstance(reply, str):
        reply = ""
    reply = reply.strip()
    if len(reply) > _CHAT_MAX_MESSAGE_CHARS:
        reply = reply[:_CHAT_MAX_MESSAGE_CHARS] + "…"

    actions = _sanitize_actions(parsed.get("actions"))

    if not reply and not actions:
        # LLM 既没回话也没动作 —— 给前端一个兜底文案，不然聊天框就僵住了。
        reply = ("（嗯…我没想好怎么回，能再说一遍喵？）" if lang == "zh"
                 else "(Hmm... I'm not sure how to reply — could you say that again?)")

    return JSONResponse({
        "success": True,
        "reply": reply,
        "actions": actions,
    })
