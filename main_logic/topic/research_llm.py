"""Summary-tier LLM helpers for delivery-time topic research.

The topic pipeline owns candidate extraction separately. This module only
supports the delivery prepare kernel: plan, reflect, source summary, and final
synthesis. It intentionally stays in ``main_logic.topic`` so topic-specific LLM
behavior does not keep growing inside the activity tracker helpers.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

from config.prompts.prompts_activity import (
    DEEP_RESEARCH_PLAN_PROMPTS,
    DEEP_RESEARCH_REFLECT_PROMPTS,
    DEEP_RESEARCH_SOURCE_SUMMARY_PROMPTS,
    DEEP_RESEARCH_SYNTHESIS_PROMPTS,
)
from utils.file_utils import robust_json_loads
from utils.tokenize import truncate_to_tokens

logger = logging.getLogger("N.E.K.O.Main.topic.research_llm")

_MAX_INTEREST_TOKENS = 80
_MAX_KEYWORDS_TOKENS = 80
_MAX_FLOOR_TOKENS = 120
_MAX_PLAN_TOKENS = 300
_MAX_EVIDENCE_TOKENS = 900
_MAX_SOURCE_CONTENT_TOKENS = 1200


def _normalize_lang(lang: str) -> str:
    low = str(lang or "").strip().lower().replace("_", "-")
    if low.startswith(("zh-tw", "zh-hant", "zh-hk")):
        return "zh-TW"
    if low.startswith("zh"):
        return "zh"
    if low.startswith("ja"):
        return "ja"
    if low.startswith("ko"):
        return "ko"
    if low.startswith("es"):
        return "es"
    if low.startswith("pt"):
        return "pt"
    if low.startswith("ru"):
        return "ru"
    return "en"


def _select_lang_template(prompts: Mapping[str, str], lang_key: str) -> str:
    if lang_key in prompts:
        return prompts[lang_key]
    if lang_key.startswith("zh") and "zh" in prompts:
        return prompts["zh"]
    return prompts["en"]


def _strip_json_fences(text: str) -> str:
    s = str(text or "").strip()
    if s.startswith("```"):
        import re

        match = re.match(r"^```[a-zA-Z]*\s*(.+?)\s*```\s*$", s, flags=re.S)
        if match:
            return match.group(1).strip()
    return s


def _safe_parse_json(raw: str) -> Any:
    if not raw:
        return None
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        try:
            return robust_json_loads(cleaned)
        except Exception:
            return None


def _json_for_prompt(value: Any, *, token_limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return truncate_to_tokens(text, token_limit)


def _keywords_for_prompt(keywords: list[str]) -> str:
    return truncate_to_tokens(", ".join(str(k or "") for k in keywords if k), _MAX_KEYWORDS_TOKENS) or "(none)"


async def _invoke_topic_research_tier(
    prompt: str,
    *,
    timeout: float,
    label: str,
    max_completion_tokens: int,
) -> str | None:
    from utils.config_manager import get_config_manager
    from utils.llm_client import HumanMessage, create_chat_llm
    from utils.token_tracker import set_call_type

    try:
        cfg_mgr = get_config_manager()
        cfg = cfg_mgr.get_model_api_config("summary")
    except Exception as exc:
        logger.debug("summary config fetch failed for %s: %s", label, exc)
        return None
    model = cfg.get("model")
    api_key = cfg.get("api_key")
    base_url = cfg.get("base_url")
    if not model or not api_key:
        logger.debug("summary tier model/api_key missing for %s", label)
        return None

    set_call_type("topic_deep_research")
    try:
        llm = create_chat_llm(
            model,
            base_url,
            api_key,
            max_completion_tokens=max_completion_tokens,
            timeout=timeout,
        )
    except Exception as exc:
        logger.debug("summary-tier %s init failed: %s", label, exc)
        return None

    try:
        async with llm:
            # LLM_INPUT_BUDGET: prompt inputs are capped before rendering in this module.
            resp = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content=prompt)]),  # noqa
                timeout=timeout,
            )
        return getattr(resp, "content", "") or ""
    except asyncio.TimeoutError:
        logger.debug("summary-tier %s call timed out (%ss)", label, timeout)
        return None
    except Exception as exc:
        logger.debug("summary-tier %s call failed: %s", label, exc)
        return None


async def derive_deep_research_plan(
    *,
    interest: str,
    keywords: list[str],
    floor_angle: str = "",
    lang: str,
    timeout: float = 8.0,
) -> Mapping[str, Any] | None:
    interest_text = truncate_to_tokens(str(interest or "").strip(), _MAX_INTEREST_TOKENS)
    if not interest_text:
        return None
    lang_key = _normalize_lang(lang)
    template = _select_lang_template(DEEP_RESEARCH_PLAN_PROMPTS, lang_key)
    prompt = template.format(
        interest=interest_text,
        keywords=_keywords_for_prompt(keywords),
        floor_angle=truncate_to_tokens(str(floor_angle or "").strip(), _MAX_FLOOR_TOKENS) or "(none)",
    )
    raw = await _invoke_topic_research_tier(
        prompt,
        timeout=timeout,
        label="topic_deep_plan",
        max_completion_tokens=512,
    )
    parsed = _safe_parse_json(raw or "")
    if isinstance(parsed, Mapping) and parsed.get("initial_queries"):
        return parsed

    try:
        from main_logic.activity.llm_enrichment import derive_deep_search_query

        query = await derive_deep_search_query(
            interest=interest_text,
            keywords=keywords,
            floor_angle=floor_angle,
            lang=lang,
            timeout=timeout,
        )
    except Exception:
        query = None
    if not query:
        return None
    return {
        "initial_queries": [query],
        "media_intent": ["news"],
        "success_criteria": [],
        "_legacy_single_query": True,
    }


async def derive_deep_research_reflect(
    *,
    interest: str,
    keywords: list[str],
    plan: Mapping[str, Any],
    evidence: list[Mapping[str, Any]],
    lang: str,
    timeout: float = 8.0,
) -> Mapping[str, Any] | None:
    lang_key = _normalize_lang(lang)
    template = _select_lang_template(DEEP_RESEARCH_REFLECT_PROMPTS, lang_key)
    prompt = template.format(
        interest=truncate_to_tokens(str(interest or ""), _MAX_INTEREST_TOKENS),
        keywords=_keywords_for_prompt(keywords),
        plan=_json_for_prompt(plan, token_limit=_MAX_PLAN_TOKENS),
        evidence=_json_for_prompt(evidence[:6], token_limit=_MAX_EVIDENCE_TOKENS),
    )
    raw = await _invoke_topic_research_tier(
        prompt,
        timeout=timeout,
        label="topic_deep_reflect",
        max_completion_tokens=512,
    )
    parsed = _safe_parse_json(raw or "")
    return parsed if isinstance(parsed, Mapping) else None


async def summarize_research_source(
    *,
    interest: str,
    query: str,
    title: str,
    content: str,
    lang: str,
    timeout: float = 8.0,
) -> Mapping[str, Any] | None:
    content_text = truncate_to_tokens(str(content or "").strip(), _MAX_SOURCE_CONTENT_TOKENS)
    if not content_text:
        return None
    lang_key = _normalize_lang(lang)
    template = _select_lang_template(DEEP_RESEARCH_SOURCE_SUMMARY_PROMPTS, lang_key)
    prompt = template.format(
        interest=truncate_to_tokens(str(interest or ""), _MAX_INTEREST_TOKENS),
        query=truncate_to_tokens(str(query or ""), _MAX_FLOOR_TOKENS),
        title=truncate_to_tokens(str(title or ""), _MAX_FLOOR_TOKENS),
        content=content_text,
    )
    raw = await _invoke_topic_research_tier(
        prompt,
        timeout=timeout,
        label="topic_deep_source_summary",
        max_completion_tokens=512,
    )
    parsed = _safe_parse_json(raw or "")
    return parsed if isinstance(parsed, Mapping) else None


async def derive_deep_research_synthesis(
    *,
    interest: str,
    keywords: list[str],
    plan: Mapping[str, Any],
    evidence: list[Mapping[str, Any]],
    lang: str,
    timeout: float = 8.0,
) -> Mapping[str, Any] | None:
    if not evidence:
        return None
    lang_key = _normalize_lang(lang)
    template = _select_lang_template(DEEP_RESEARCH_SYNTHESIS_PROMPTS, lang_key)
    prompt = template.format(
        interest=truncate_to_tokens(str(interest or ""), _MAX_INTEREST_TOKENS),
        keywords=_keywords_for_prompt(keywords),
        plan=_json_for_prompt(plan, token_limit=_MAX_PLAN_TOKENS),
        evidence=_json_for_prompt(evidence[:6], token_limit=_MAX_EVIDENCE_TOKENS),
    )
    raw = await _invoke_topic_research_tier(
        prompt,
        timeout=timeout,
        label="topic_deep_synthesis",
        max_completion_tokens=768,
    )
    parsed = _safe_parse_json(raw or "")
    return parsed if isinstance(parsed, Mapping) else None
