from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from typing import Any, Awaitable, Callable

from .prompt_templates import (
    STUDY_EMPTY_INPUT_DEFAULT,
    STUDY_FALLBACK_EXPLANATION_DEFAULT,
    STUDY_FALLBACK_FEEDBACK,
    STUDY_FALLBACK_NEXT_ACTION,
    STUDY_FALLBACK_QUESTION_EMPTY,
    STUDY_FALLBACK_QUESTION_TEMPLATE,
    STUDY_FALLBACK_SUMMARY_DEFAULT,
    STUDY_FALLBACK_SUMMARY_EMPTY,
    STUDY_FALLBACK_SUMMARY_NEXT_ACTIONS,
    STUDY_FALLBACK_TRACK_NEXT_STEPS_DEFAULT,
    STUDY_FALLBACK_TRACK_NEXT_STEPS_WITH_WEAK_POINTS,
    STUDY_JSON_CORRECTION_USER_TEMPLATE,
    STUDY_MARKDOWN_SECTION_EMPTY_ITEM,
)
from plugin.sdk.plugin import SdkError

from .constants import (
    LLM_OPERATION_ANSWER_EVALUATE,
    LLM_OPERATION_CONCEPT_EXPLAIN,
    LLM_OPERATION_KNOWLEDGE_TRACK,
    LLM_OPERATION_QUESTION_GENERATE,
    LLM_OPERATION_SUMMARIZE_SESSION,
    MODE_COMPANION,
    MODE_TEACHING,
)
from .llm_prompts import build_concept_explain_messages, build_operation_messages
from .mode_manager import build_transition_phrase, normalize_mode, study_i18n_t
from .models import MODE_CONCEPT_EXPLAIN, StudyConfig, TutorReply, utc_now_iso

try:
    from utils.file_utils import robust_json_loads
except Exception:  # pragma: no cover - utility is present in the host app.
    robust_json_loads = None  # type: ignore[assignment]

try:
    import utils.config_manager as _config_manager_module
except Exception as exc:  # pragma: no cover - guarded runtime dependency.
    _config_manager_module = None  # type: ignore[assignment]
    _CONFIG_MANAGER_IMPORT_ERROR = exc
else:
    _CONFIG_MANAGER_IMPORT_ERROR = None

try:
    import utils.llm_client as _llm_client_module
except Exception as exc:  # pragma: no cover - guarded runtime dependency.
    _llm_client_module = None  # type: ignore[assignment]
    _LLM_CLIENT_IMPORT_ERROR = exc
else:
    _LLM_CLIENT_IMPORT_ERROR = None

try:
    import utils.token_tracker as _token_tracker_module
except Exception as exc:  # pragma: no cover - guarded runtime dependency.
    _token_tracker_module = None  # type: ignore[assignment]
    _TOKEN_TRACKER_IMPORT_ERROR = exc
else:
    _TOKEN_TRACKER_IMPORT_ERROR = None


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.DOTALL)
_JSON_CORRECTION_MAX_ATTEMPTS = 1
_JSON_CORRECTION_BAD_OUTPUT_MAX_TOKENS = 6000
_JSON_CORRECTION_ERROR_MAX_TOKENS = 300
_LLM_CALL_TIMEOUT_GRACE_SECONDS = 0.5
_ANSWER_VERDICTS = frozenset({"correct", "partial", "wrong", "dont_know"})


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: object, *, limit: int = 6) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        text = _as_str(item, str(item)).strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _clamp_float(
    value: object, minimum: float, maximum: float, default: float
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _clamp_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        return _CODE_FENCE_RE.sub("", text).strip()
    return text


def _bounded_prompt_text(value: object, *, max_tokens: int) -> str:
    from utils.tokenize import count_tokens, truncate_to_tokens

    text = _as_str(value, str(value))
    if count_tokens(text) <= max_tokens:
        return text
    clipped = truncate_to_tokens(text, max_tokens)
    omitted = len(text) - len(clipped)
    result = f"{clipped}\n...[truncated {omitted} chars]"
    while clipped and count_tokens(result) > max_tokens:
        clipped = clipped[:-1]
        omitted = len(text) - len(clipped)
        result = f"{clipped}\n...[truncated {omitted} chars]"
    return result


def _bounded_prompt_text_chars(value: object, *, max_chars: int) -> str:
    """Char-based bound for notebook sources.

    Unlike :func:`_bounded_prompt_text` (token budget, shared with the JSON
    corrector), this keeps an exact CHARACTER prefix so the reported
    ``...[truncated N chars]`` count is precise — notebook expand/summarize
    sources are user-pasted prose where a predictable char cap is the contract.
    """
    text = _as_str(value, str(value))
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    omitted = len(text) - len(clipped)
    return f"{clipped}\n...[truncated {omitted} chars]"


def diagnostic_code_for_exception(exc: BaseException) -> str:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if (
        isinstance(exc, asyncio.TimeoutError)
        or "timeout" in name
        or "timeout" in message
    ):
        return "timeout"
    if isinstance(exc, SdkError) and (
        "missing configured" in message
        or "failed to initialize" in message
        or "missing runtime dependency" in message
    ):
        return "model_unavailable"
    if "auth" in name or "connection" in name or "unavailable" in name:
        return "model_unavailable"
    return "llm_call_failed"


__all__ = [
    "Any",
    "Awaitable",
    "Callable",
    "asyncio",
    "hashlib",
    "inspect",
    "re",
    "STUDY_EMPTY_INPUT_DEFAULT",
    "STUDY_FALLBACK_EXPLANATION_DEFAULT",
    "STUDY_FALLBACK_FEEDBACK",
    "STUDY_FALLBACK_NEXT_ACTION",
    "STUDY_FALLBACK_QUESTION_EMPTY",
    "STUDY_FALLBACK_QUESTION_TEMPLATE",
    "STUDY_FALLBACK_SUMMARY_DEFAULT",
    "STUDY_FALLBACK_SUMMARY_EMPTY",
    "STUDY_FALLBACK_SUMMARY_NEXT_ACTIONS",
    "STUDY_FALLBACK_TRACK_NEXT_STEPS_DEFAULT",
    "STUDY_FALLBACK_TRACK_NEXT_STEPS_WITH_WEAK_POINTS",
    "STUDY_JSON_CORRECTION_USER_TEMPLATE",
    "STUDY_MARKDOWN_SECTION_EMPTY_ITEM",
    "SdkError",
    "LLM_OPERATION_ANSWER_EVALUATE",
    "LLM_OPERATION_CONCEPT_EXPLAIN",
    "LLM_OPERATION_KNOWLEDGE_TRACK",
    "LLM_OPERATION_QUESTION_GENERATE",
    "LLM_OPERATION_SUMMARIZE_SESSION",
    "MODE_COMPANION",
    "MODE_TEACHING",
    "build_concept_explain_messages",
    "build_operation_messages",
    "build_transition_phrase",
    "normalize_mode",
    "study_i18n_t",
    "MODE_CONCEPT_EXPLAIN",
    "StudyConfig",
    "TutorReply",
    "utc_now_iso",
    "robust_json_loads",
    "_config_manager_module",
    "_CONFIG_MANAGER_IMPORT_ERROR",
    "_llm_client_module",
    "_LLM_CLIENT_IMPORT_ERROR",
    "_token_tracker_module",
    "_TOKEN_TRACKER_IMPORT_ERROR",
    "_CODE_FENCE_RE",
    "_JSON_CORRECTION_MAX_ATTEMPTS",
    "_JSON_CORRECTION_BAD_OUTPUT_MAX_TOKENS",
    "_JSON_CORRECTION_ERROR_MAX_TOKENS",
    "_LLM_CALL_TIMEOUT_GRACE_SECONDS",
    "_ANSWER_VERDICTS",
    "_as_str",
    "_as_dict",
    "_as_list",
    "_string_list",
    "_clamp_float",
    "_clamp_int",
    "_strip_code_fences",
    "_bounded_prompt_text",
    "_bounded_prompt_text_chars",
    "diagnostic_code_for_exception",
]
