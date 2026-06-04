# -*- coding: utf-8 -*-
"""
Card-Assist Router

Four endpoints powering the in-app AI assistant that helps users author a
catgirl character card (Character Card Manager → "猫猫辅助生成" button):

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
    """本地 Origin/CSRF 守卫：card-assist 这四个 POST 都会真去打用户配置的 agent
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
    """对「没有专门 prompt 版本」的 locale 生成一条显式输出语言指示，追加到 prompt 末尾。
    字段 key 已由 _resolve_target_keys 按 locale 模板给定，这里只约束 values / 问题 / 说明
    用目标语言。en / zh-CN 与基础 prompt 一致 → 返回空串、不加任何东西。"""
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


def _build_assist_llm():
    """Construct an LLM client backed by the agent API config. Returns
    ``(llm, error_dict_or_None)``. Caller must ``await llm.aclose()`` if llm is
    not None.
    """
    from utils.llm_client import create_chat_llm
    try:
        cm = get_config_manager()
        api_cfg = cm.get_model_api_config("agent")
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
    llm, err = _build_assist_llm()
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

_CHAT_EDIT_INTENT_RE = re.compile(
    r"(修改|改写|重写|重生|重做|重新写|调整|补充|新增|添加|删除|移除|换一|换成|"
    r"优化|完善|梳理|设定|字段|rewrite|revise|regenerate|refine|update|change|"
    r"edit|add|remove|delete|replace|make\s+her|make\s+him)",
    re.IGNORECASE,
)

_CHAT_FULL_REWRITE_RE = re.compile(
    r"(所有可见字段|全部可见字段|所有字段|全部字段|每个字段|整张卡|整個卡|全卡|"
    r"整个角色卡|整個角色卡|full\s+card|whole\s+card|entire\s+card|all\s+fields|"
    r"all\s+visible\s+fields)",
    re.IGNORECASE,
)

_CHAT_REWRITE_VERB_RE = re.compile(
    r"(重写|重新写|改写|重做|重生|梳理|完善|rewrite|revise|regenerate|redo|refresh)",
    re.IGNORECASE,
)

_CHAT_ADVICE_ONLY_INTENT_RE = re.compile(
    r"(建议|意见|点评|审一下|审稿|检查一下|帮我看看|看一下|指出问题|分析|优缺点|"
    r"修改方向|修改方案|候选写法|suggest|suggestion|advice|critique|review|"
    r"pros\s+and\s+cons|candidate\s+rewrite)",
    re.IGNORECASE,
)

_CHAT_DIRECT_EDIT_REQUEST_RE = re.compile(
    r"(直接|现在|立刻|马上|帮我|替我|给我)?\s*"
    r"(改一下|改下|改一改|修改一下|调整一下|调整下|改成|修改成|换成|写成|写进|应用|采纳|"
    r"更新字段|保存到字段|直接改|帮我改|替我改|"
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


def _chat_text_requests_full_rewrite(text: str) -> bool:
    if not text:
        return False
    return bool(
        _CHAT_FULL_REWRITE_RE.search(text)
        and _CHAT_REWRITE_VERB_RE.search(text)
    )


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
