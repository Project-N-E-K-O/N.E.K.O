from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any, Mapping

from .models import json_copy


_EXPLAIN_EVIDENCE_TYPES = frozenset({"current_line", "history_line", "choice"})
_KEY_POINT_TYPES = frozenset({"plot", "emotion", "decision", "reveal", "objective"})
_LLM_RESPONSE_CACHE_MAX_ITEMS = 50
_LLM_NEAR_MATCH_CACHE_MAX_ITEMS = 50
_LLM_PROVIDER_BACKOFF_SECONDS = 2.0
_LLM_PROVIDER_BACKOFF_CATEGORIES = frozenset(
    {"busy", "gateway_unavailable", "provider_unavailable", "timeout"}
)
_LOCAL_QUEUE_TIMEOUT_DIAGNOSTIC = "timeout: llm semaphore acquire timed out"
_REPEAT_GUARD_MAX_ITEMS = 8
_NEAR_MATCH_SUPPORTED_OPERATIONS = frozenset(
    {"explain_line", "summarize_scene", "scene_summary"}
)
_NEAR_MATCH_OBSERVED_SIGNATURE_MAX_CHARS = 4000
_NEAR_MATCH_EXCLUDED_KEYS = frozenset(
    {
        "current_snapshot",
        "degraded_reasons",
        "diagnostic",
        "input_degraded",
        "observed_lines",
        "recent_lines",
        "screen_context",
    }
)
_OBSERVED_SIMILARITY_THRESHOLD = 0.85


def _json_payload_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return json_copy(value)


def _stable_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _stable_json_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = [_stable_json_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    return {"__non_json_type__": f"{type(value).__module__}.{type(value).__qualname__}"}


def _stable_json_fingerprint(value: Any) -> str:
    return json.dumps(
        _stable_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_observed_text(value: object) -> str:
    raw = "" if value is None else str(value)
    return " ".join(raw.strip().lower().split())


def _string_or_empty(value: object) -> str:
    return "" if value is None else str(value)


def _line_similarity_signature(line: Any) -> str:
    if isinstance(line, Mapping):
        speaker = _normalize_observed_text(line.get("speaker"))
        text = _normalize_observed_text(line.get("text"))
        line_id = _normalize_observed_text(line.get("line_id"))
        return f"{line_id}|{speaker}|{text}"
    return _normalize_observed_text(line)


def _ngrams(value: str, *, n: int = 3) -> set[str]:
    if not value:
        return set()
    if len(value) < n:
        return {value}
    return {value[index:index + n] for index in range(len(value) - n + 1)}


def _jaccard_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    left_set = _ngrams(left)
    right_set = _ngrams(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _observed_similarity(left: list[str], right: list[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    left_text = "\n".join(left)[:_NEAR_MATCH_OBSERVED_SIGNATURE_MAX_CHARS]
    right_text = "\n".join(right)[:_NEAR_MATCH_OBSERVED_SIGNATURE_MAX_CHARS]
    return _jaccard_similarity(left_text, right_text)


def _hash_line(line: Any) -> str:
    if not isinstance(line, Mapping):
        return ""
    return _stable_json_fingerprint(
        {
            "line_id": _string_or_empty(line.get("line_id")),
            "speaker": _string_or_empty(line.get("speaker")),
            "text": _string_or_empty(line.get("text")),
            "scene_id": _string_or_empty(line.get("scene_id")),
            "route_id": _string_or_empty(line.get("route_id")),
        }
    )


def _hash_stable_lines(lines: Any) -> str:
    if not isinstance(lines, list):
        return _stable_json_fingerprint([])
    return _stable_json_fingerprint([_hash_line(item) for item in lines if isinstance(item, Mapping)])


def _context_lines(context: dict[str, Any], key: str) -> list[Any]:
    value = context.get(key)
    if isinstance(value, list):
        return value
    public_context = context.get("public_context")
    if isinstance(public_context, Mapping):
        value = public_context.get(key)
        if isinstance(value, list):
            return value
    return []


def _current_line_for_near_match(context: dict[str, Any]) -> dict[str, Any]:
    if _string_or_empty(context.get("line_id")) or _string_or_empty(context.get("text")):
        return {
            "line_id": _string_or_empty(context.get("line_id")),
            "speaker": _string_or_empty(context.get("speaker")),
            "text": _string_or_empty(context.get("text")),
            "scene_id": _string_or_empty(context.get("scene_id")),
            "route_id": _string_or_empty(context.get("route_id")),
        }
    for key in ("current_line", "current_snapshot"):
        value = context.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    public_context = context.get("public_context")
    if isinstance(public_context, Mapping):
        value = public_context.get("current_line")
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _near_match_context_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _near_match_context_value(item)
            for key, item in value.items()
            if str(key) not in _NEAR_MATCH_EXCLUDED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_near_match_context_value(item) for item in value]
    return value


def _response_similarity(left: Any, right: Any) -> float:
    left_fingerprint = _stable_json_fingerprint(left)
    right_fingerprint = _stable_json_fingerprint(right)
    if left_fingerprint == right_fingerprint:
        return 1.0

    def _response_text(value: Any, fingerprint: str) -> str:
        if isinstance(value, Mapping):
            text = str(value.get("reply") or value.get("result") or "").strip()
            if text:
                return " ".join(text.lower().split())
        return " ".join(fingerprint.lower().split())

    left_text = _response_text(left, left_fingerprint)
    right_text = _response_text(right, right_fingerprint)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()
