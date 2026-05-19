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

router = APIRouter(prefix="/api/card-assist", tags=["card-assist"])


# Per-request timeout. Card assist is interactive — bail out fast so the
# user isn't staring at a spinner.
_LLM_TIMEOUT_SECONDS = 60.0


def _resolve_language(payload_locale: str | None) -> str:
    """Map a frontend locale (e.g. 'zh-CN', 'en-US') to the short prompt
    language code ('zh' / 'en'). Falls back to the global language setting."""
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


def _format_card_for_prompt(card: Any, max_chars: int = 1200) -> str:
    """Render the existing card dict as compact JSON for prompt injection.
    Truncates very long cards so we don't blow the token budget."""
    if not isinstance(card, dict):
        return "{}"
    # Strip system-reserved keys; they're noise for the LLM.
    filtered = {k: v for k, v in card.items()
                if not str(k).startswith("_") and k not in ("live2d", "live3d", "vrm",
                                                              "mmd", "voice_id",
                                                              "system_prompt",
                                                              "model_type")}
    try:
        text = json.dumps(filtered, ensure_ascii=False, indent=2)
    except Exception:
        text = str(filtered)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


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
    current_card_text = _format_card_for_prompt(body.get("current_card"))
    try:
        answers_text = json.dumps(answers, ensure_ascii=False, indent=2)
    except Exception:
        answers_text = str(answers)

    template = get_card_assist_generate_prompt(lang)
    prompt = template % (description, answers_text, current_card_text)

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
    # quotes if the LLM wrapped it anyway.
    text = _strip_json_fence(content).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'", "“", "”"):
        text = text[1:-1].strip()
    if not text:
        return JSONResponse({"success": False, "error": "llm_empty_response"},
                            status_code=502)

    return JSONResponse({"success": True, "field_key": field_key, "value": text})
