# -*- coding: utf-8 -*-
"""
Agent 结果解析器 — 将 ComputerUse / BrowserUse / Plugin 的返回 dict
转换为人类可读的自然语言摘要，避免原始 JSON 污染 LLM 上下文。

所有函数均为纯函数，不依赖 LLM、不抛异常。
所有面向模型的字符串均通过 prompts_sys i18n 字典输出。
"""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from config.prompts_sys import (
    _loc,
    RESULT_PARSER_ERROR_CODES,
    RESULT_PARSER_ERROR_SUBSTRINGS,
    RESULT_PARSER_PHRASES,
)


# ── 语言工具 ──────────────────────────────────────────────────────────

def _get_lang(lang: str | None) -> str:
    """获取当前语言代码。优先使用显式传入值，其次全局设置，兜底 'zh'。"""
    if lang:
        return lang
    try:
        from utils.language_utils import get_global_language
        return get_global_language()
    except Exception:
        return 'zh'


def _phrase(key: str, lang: str, **kwargs: Any) -> str:
    """从 RESULT_PARSER_PHRASES 取出 i18n 字符串并格式化。"""
    template = _loc(RESULT_PARSER_PHRASES.get(key, {}), lang)
    if not template:
        return key
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return template


# ── 辅助 ────────────────────────────────────────────────────────────────

def _try_extract_error_message(error: str, lang: str) -> str:
    """如果 error 是 JSON 字符串，提取人类可读部分；否则原样返回。"""
    s = error.strip()
    if not (s.startswith("{") or s.startswith("[")):
        return error
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            code = str(obj.get("code") or "")
            if code in RESULT_PARSER_ERROR_CODES:
                return _loc(RESULT_PARSER_ERROR_CODES[code], lang)
            msg = str(obj.get("message") or "").strip()
            return msg or code or error
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return error


def _format_error(error: Any, lang: str) -> str:
    """统一处理各种形态的 error 值（str / dict / None）→ 人类可读字符串。"""
    if error is None:
        return ""
    if isinstance(error, dict):
        msg = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
        if msg:
            if code in RESULT_PARSER_ERROR_CODES:
                return _loc(RESULT_PARSER_ERROR_CODES[code], lang)
            return msg
        if code:
            if code in RESULT_PARSER_ERROR_CODES:
                return _loc(RESULT_PARSER_ERROR_CODES[code], lang)
            return code
        return ""
    s = str(error).strip()
    if not s:
        return ""
    # 已知错误码精确匹配
    if s in RESULT_PARSER_ERROR_CODES:
        return _loc(RESULT_PARSER_ERROR_CODES[s], lang)
    # 已知子串匹配
    for substr, i18n_dict in RESULT_PARSER_ERROR_SUBSTRINGS.items():
        if substr in s:
            return _loc(i18n_dict, lang)
    # 可能是 JSON 字符串
    return _try_extract_error_message(s, lang)


def _truncate(s: str, limit: int | None = None) -> str:
    """Cut tool-result summaries fed back into the LLM context. ``limit`` is
    in tiktoken tokens (o200k_base) — 200 ≈ 270 CJK chars / ~800 English
    chars under the current encoding. Sync helper; truncate_to_tokens
    falls back to a heuristic when tiktoken is unavailable.

    Reserves token room for the trailing ellipsis so the returned string is
    guaranteed to fit within ``limit``.
    """
    from utils.tokenize import count_tokens, truncate_to_tokens
    if limit is None:
        from config import TASK_DETAIL_MAX_TOKENS
        limit = TASK_DETAIL_MAX_TOKENS
    if count_tokens(s) <= limit:
        return s
    suffix = "…"
    suffix_tokens = count_tokens(suffix)
    if limit <= suffix_tokens:
        return truncate_to_tokens(s, limit)
    return truncate_to_tokens(s, limit - suffix_tokens) + suffix


# ── ComputerUse / BrowserUse 共用 ───────────────────────────────────────

def _parse_tool_result(res: Any, lang: str) -> tuple[bool, str]:
    """解析 ComputerUse / BrowserUse 返回值 → (succeeded, 自然语言摘要)。

    返回二元组方便调用方区分成功/失败，将 detail 和 error_message 放入正确字段。
    """
    if not isinstance(res, dict):
        return False, _phrase('no_result', lang)

    if res.get("success"):
        result = _truncate(str(res.get("result") or "").strip())
        steps = res.get("steps")
        if steps and result:
            return True, _phrase('steps_done_with', lang, n=steps, detail=result)
        if steps:
            return True, _phrase('steps_done', lang, n=steps)
        if result:
            return True, _phrase('completed_with', lang, detail=result)
        return True, _phrase('completed', lang)

    raw_err = res.get("error")
    err = _format_error(raw_err, lang)
    if err:
        return False, _phrase('failed', lang, detail=_truncate(err))
    return False, _phrase('exec_failed', lang)


def parse_computer_use_result(res: Any, *, lang: str | None = None) -> tuple[bool, str]:
    """解析 ComputerUse run_instruction 返回值 → (succeeded, 自然语言摘要)。"""
    return _parse_tool_result(res, _get_lang(lang))


def parse_browser_use_result(res: Any, *, lang: str | None = None) -> tuple[bool, str]:
    """解析 BrowserUse run_instruction 返回值 → (succeeded, 自然语言摘要)。"""
    return _parse_tool_result(res, _get_lang(lang))


# ── Plugin ──────────────────────────────────────────────────────────────

def _format_field_value(val: Any, lang: str) -> Optional[str]:
    """将单个字段值格式化为人类可读字符串。"""
    if val is None:
        return None
    if isinstance(val, dict):
        return None
    if isinstance(val, list):
        return _phrase('list_count', lang, n=len(val))
    s = str(val).strip()
    return s if s else None


def parse_plugin_result(
    run_data: Any,
    *,
    llm_result_fields: Optional[Sequence[str]] = None,
    plugin_message: str = "",
    error: Any = None,
    lang: str | None = None,
) -> str:
    """解析 Plugin 执行结果 → 自然语言摘要。"""
    lang = _get_lang(lang)

    # 失败路径
    if error:
        err = _format_error(error, lang)
        return _phrase('failed', lang, detail=_truncate(err)) if err else _phrase('exec_error', lang)

    fallback = plugin_message.strip() if plugin_message else _phrase('exec_done', lang)

    if not isinstance(run_data, dict):
        return fallback

    if not llm_result_fields:
        # Fallback for reply-style tool results: when caller did not specify
        # llm_result_fields, surface the reply field if present.
        reply_val = run_data.get("reply")
        if isinstance(reply_val, str):
            reply_text = reply_val.strip()
            if reply_text:
                return _truncate(reply_text)
        return fallback

    parts: list[tuple[str, str]] = []
    for field_name in llm_result_fields:
        val = run_data.get(field_name)
        formatted = _format_field_value(val, lang)
        if formatted is not None:
            parts.append((field_name, formatted))

    if not parts:
        return fallback

    # 单字段：直接输出值（不带字段名）
    if len(parts) == 1:
        return _truncate(parts[0][1])

    return _truncate(", ".join(f"{k}: {v}" for k, v in parts))


# ── Push Message ───────────────────────────────────────────────────────

def parse_push_message_content(content: Any, *, lang: str | None = None) -> str:
    """解析插件 push_message 的 content → 干净的自然语言字符串。"""
    lang = _get_lang(lang)

    if content is None:
        return ""
    if isinstance(content, dict):
        msg = str(content.get("message") or content.get("content") or "").strip()
        if msg:
            return _truncate(msg)
        parts = []
        for k, v in content.items():
            fv = _format_field_value(v, lang)
            if fv:
                parts.append(f"{k}: {fv}")
        return _truncate(", ".join(parts)) if parts else _phrase('plugin_notification', lang)
    s = str(content).strip()
    if not s:
        return ""
    if s.startswith("{") or s.startswith("["):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                msg = str(obj.get("message") or obj.get("content") or "").strip()
                if msg:
                    return _truncate(msg)
            return _truncate(_phrase('notification_received', lang))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return _truncate(s)
