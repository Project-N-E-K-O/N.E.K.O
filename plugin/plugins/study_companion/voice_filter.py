from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
import re
import time
from typing import Any


CATGIRL_NAMES = ("Yui", "yui", "结衣")
NAME_WINDOW_SECONDS = 3.0
SHORT_TRANSCRIPT_CHARS = 5
OCR_OVERLAP_THRESHOLD = {
    "default": 0.6,
    "math": 0.4,
    "physics": 0.4,
    "chemistry": 0.4,
}
OCR_TRUNCATION = {
    "question": 800,
    "answering": 600,
    "reading": 500,
    "review": 500,
    "notes": 500,
    "summary": 500,
    "idle": 200,
}

_SCIENCE_SUBJECTS = {"math", "physics", "chemistry"}
_QUESTION_INTENT_RE = re.compile(
    r"\b("
    r"can you|could you|does this|explain|give me|help me|"
    r"how should i|is this|make a|should i|turn this|"
    r"what should i|where did my|why does this|why is it|why is this"
    r")\b",
    re.IGNORECASE,
)
_QUESTION_INTENT_MARKERS = (
    "为什么",
    "怎么",
    "如何",
    "什么",
    "哪",
    "帮我",
    "讲一下",
    "解释",
    "提示",
    "看看",
)
_CHINESE_DIGITS = {
    "零": "0",
    "〇": "0",
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


class VoiceFilter:
    """Rule-based V1 voice filter for study self-talk.

    The class is intentionally independent from the plugin runtime. Phase 9's
    SDK voice bridge can call this class once transcript/session hooks exist.
    Explicit ``names`` passed to the constructor override configured names so
    tests and future runtime adapters can pin the activation vocabulary.
    """

    def __init__(
        self,
        names: Iterable[str] | None = None,
        *,
        config_manager: Any | None = None,
        plugin_config: Mapping[str, Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._last_name_call_times: dict[str, float] = {}
        self._names = self._load_names(
            names,
            config_manager=config_manager,
            plugin_config=plugin_config,
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._names)

    def filter(
        self,
        transcript: str,
        screen_text: str = "",
        screen_type: str = "",
        subject: str = "",
        session_key: str = "",
        extra_names: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        text = str(transcript or "").strip()
        if not text:
            return None

        now = self._clock()
        window_key = _name_window_key(session_key)
        names = list(self._names)
        _extend_names(names, extra_names)
        name, pos = _find_earliest_name(text, names)
        if name:
            self._last_name_call_times[window_key] = now
            pre_context = text[:pos].strip()
            question = text[pos + len(name) :].strip()
            return {
                "should_relay": True,
                "method": "name_call",
                "name": name,
                "pre_context": pre_context,
                "question": question or text,
                "screen_type": screen_type,
                "subject": _normalize_subject(subject) or _derive_subject(screen_text),
            }

        last_name_call_time = self._last_name_call_times.get(window_key, 0.0)
        in_name_window = (
            last_name_call_time > 0
            and now - last_name_call_time < NAME_WINDOW_SECONDS
        )
        if in_name_window:
            return {
                "should_relay": True,
                "method": "name_window",
                "question": text,
                "screen_type": screen_type,
                "subject": _normalize_subject(subject) or _derive_subject(screen_text),
            }

        if len(text) < SHORT_TRANSCRIPT_CHARS:
            return {"should_relay": False, "method": "too_short"}

        normalized_subject = _normalize_subject(subject) or _derive_subject(screen_text)
        if _has_question_intent(text):
            return {
                "should_relay": True,
                "method": "question_intent",
                "question": text,
                "screen_type": screen_type,
                "subject": normalized_subject,
            }

        threshold = OCR_OVERLAP_THRESHOLD.get(
            normalized_subject,
            OCR_OVERLAP_THRESHOLD["default"],
        )
        overlap = _text_overlap_ratio(text, screen_text)
        number_match = _number_sequence_match(text, screen_text)
        if screen_text and (
            overlap > threshold
            or (normalized_subject in _SCIENCE_SUBJECTS and number_match > 0.6)
        ):
            return {
                "should_relay": False,
                "method": "ocr_overlap",
                "overlap": overlap,
                "number_match": number_match,
                "subject": normalized_subject,
                "threshold": threshold,
            }

        return None

    @staticmethod
    def _load_names(
        names: Iterable[str] | None,
        *,
        config_manager: Any | None,
        plugin_config: Mapping[str, Any] | None,
    ) -> list[str]:
        candidates: list[str] = []
        _extend_names(candidates, names)
        if not candidates:
            _extend_names(candidates, _names_from_config_manager(config_manager))
        if not candidates:
            voice_filter = (
                plugin_config.get("voice_filter")
                if isinstance(plugin_config, Mapping)
                else None
            )
            configured_names = (
                voice_filter.get("names")
                if isinstance(voice_filter, Mapping)
                else None
            )
            _extend_names(candidates, configured_names)
        if not candidates:
            candidates = list(CATGIRL_NAMES)
        return candidates


def _extend_names(target: list[str], values: Any) -> None:
    if values is None:
        return
    if isinstance(values, str):
        values = re.split(r"[,，、/\s]+", values)
    if not isinstance(values, Iterable):
        return
    seen = {item.lower() for item in target}
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            target.append(name)
            seen.add(key)


def _names_from_config_manager(config_manager: Any | None) -> list[str]:
    if config_manager is None or not hasattr(config_manager, "get_character_data"):
        return []
    try:
        data = config_manager.get_character_data()
    except Exception:
        return []
    if not isinstance(data, tuple) or len(data) < 4:
        return []
    current_name = str(data[1] or "").strip()
    character_map = data[3] if isinstance(data[3], Mapping) else {}
    current_payload = (
        character_map.get(current_name)
        if current_name and isinstance(character_map, Mapping)
        else None
    )
    candidates = [current_name] if current_name else []
    if isinstance(current_payload, Mapping):
        for key in ("昵称", "nickname", "nicknames", "names"):
            _extend_names(candidates, current_payload.get(key))
    return candidates


def _find_earliest_name(text: str, names: Iterable[str]) -> tuple[str | None, int]:
    lower_text = text.lower()
    best_name: str | None = None
    best_pos = len(text)
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        needle = name.lower()
        if name.isascii() and name.isalnum():
            match = re.search(r"\b" + re.escape(needle) + r"\b", lower_text, re.ASCII)
            pos = match.start() if match else -1
        else:
            pos = lower_text.find(needle)
        if pos != -1 and pos < best_pos:
            best_name = name
            best_pos = pos
    return best_name, best_pos


def _name_window_key(value: str) -> str:
    key = str(value or "").strip()
    return key or "__default__"


def _text_overlap_ratio(a: str, b: str) -> float:
    a_chars = {char.lower() for char in str(a or "") if not char.isspace()}
    b_chars = {char.lower() for char in str(b or "") if not char.isspace()}
    if not a_chars or not b_chars:
        return 0.0
    return len(a_chars & b_chars) / len(a_chars)


def _number_sequence_match(transcript: str, ocr_text: str) -> float:
    transcript_numbers = _number_tokens(transcript)
    if not transcript_numbers:
        return 0.0
    ocr_numbers = set(_number_tokens(ocr_text))
    if not ocr_numbers:
        return 0.0
    matches = sum(1 for number in transcript_numbers if number in ocr_numbers)
    return matches / len(transcript_numbers)


def _number_tokens(value: str) -> list[str]:
    normalized = str(value or "").translate(_SUPERSCRIPT_DIGITS)
    tokens = re.findall(r"\d+(?:\.\d+)?", normalized)
    chinese_tokens = [_CHINESE_DIGITS[char] for char in normalized if char in _CHINESE_DIGITS]
    if "十" in normalized:
        chinese_tokens.append("10")
    return tokens + chinese_tokens


def _has_question_intent(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return (
        "?" in value
        or "？" in value
        or bool(_QUESTION_INTENT_RE.search(value))
        or any(marker in value for marker in _QUESTION_INTENT_MARKERS)
    )


def _derive_subject(ocr_text: str) -> str:
    text = str(ocr_text or "").lower()
    if not text:
        return "default"
    chemistry_hits = (
        "化学",
        "反应",
        "离子",
        "摩尔",
    )
    chemistry_formula_re = re.compile(r"(?<![a-z0-9])(?:mol|h2o|nacl|co2)(?![a-z0-9])")
    physics_hits = (
        "速度",
        "加速度",
        "重力",
        "牛顿",
        "电流",
        "电压",
        "动能",
        "势能",
        "gravity",
        "velocity",
    )
    math_hits = (
        "函数",
        "方程",
        "导数",
        "积分",
        "几何",
        "概率",
        "矩阵",
        "向量",
        "求导",
        "\\frac",
        "\\sqrt",
        "x²",
        "x³",
        "y=",
    )
    math_expression_re = re.compile(
        r"(?<![a-z])(?:"
        r"[a-z]\s*(?:=|[+*/^²³])"
        r"|[a-z]-\d"
        r"|\d-\s*[a-z]"
        r"|[a-z]\s+-\s+(?:[a-z]|\d)\s*(?=[=+*/^²³])"
        r")"
    )
    if any(token in text for token in chemistry_hits) or chemistry_formula_re.search(text):
        return "chemistry"
    if any(token in text for token in physics_hits):
        return "physics"
    if any(token in text for token in math_hits) or math_expression_re.search(text):
        return "math"
    return "default"


def build_context_for_catgirl(
    transcript: str,
    state: Any,
    screen_context: Mapping[str, Any] | None,
    filter_result: Mapping[str, Any] | None,
) -> str:
    context = screen_context or {}
    result = filter_result or {}
    parts: list[str] = []
    last_ocr_text = str(getattr(state, "last_ocr_text", "") or "").strip()
    screen_classification = getattr(state, "last_screen_classification", None)
    if not isinstance(screen_classification, Mapping):
        screen_classification = {}
    subject = _normalize_subject(str(context.get("subject") or result.get("subject") or ""))
    if not subject:
        subject = _derive_subject(last_ocr_text)

    if last_ocr_text:
        if subject in _SCIENCE_SUBJECTS:
            parts.append("[屏幕OCR-数学符号可能有识别误差，请以常识判断]")
        screen_type = str(
            screen_classification.get("screen_type")
            or result.get("screen_type")
            or ""
        )
        max_len = OCR_TRUNCATION.get(screen_type, 500)
        parts.append(f"[屏幕] {last_ocr_text[:max_len]}")
        if _ocr_is_stale(getattr(state, "last_ocr_at", "")):
            parts.append("[注意] 屏幕内容可能已切换")

    topic = str(context.get("topic") or "").strip()
    mode = str(getattr(state, "active_mode", "") or "").strip()
    if topic or mode:
        state_parts = [item for item in (topic, mode) if item]
        parts.append(f"[状态] {' | '.join(state_parts)}")

    pre_context = str(result.get("pre_context") or "").strip()
    if pre_context:
        parts.append(f"[铺垫] {pre_context[:300]}")

    question = str(result.get("question") or transcript or "").strip()
    if question:
        parts.append(f"[问题] {question}")
    return "\n".join(parts)


async def safe_cancel_response(voice_session: Any) -> bool:
    if voice_session is None or not hasattr(voice_session, "cancel_response"):
        return False
    try:
        await voice_session.cancel_response()
    except Exception as exc:
        if _is_safe_cancel_exception(exc):
            return False
        raise
    return True


def _normalize_subject(subject: str) -> str:
    normalized = str(subject or "").strip().lower()
    return normalized if normalized in OCR_OVERLAP_THRESHOLD else ""


def _ocr_is_stale(value: Any, *, max_age_seconds: float = 5.0) -> bool:
    if not value:
        return False
    try:
        if isinstance(value, (int, float)):
            return time.time() - float(value) > max_age_seconds
        text = str(value).replace("Z", "+00:00")
        captured = datetime.fromisoformat(text)
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        return (
            datetime.now(timezone.utc) - captured.astimezone(timezone.utc)
        ).total_seconds() > max_age_seconds
    except Exception:
        return False


def _is_safe_cancel_exception(exc: Exception) -> bool:
    safe_names = {"ResourceNotFound", "InvalidState", "InvalidStateError"}
    if type(exc).__name__ in safe_names:
        return True
    text = str(exc).lower()
    return (
        ("not found" in text or "already" in text)
        and ("response" in text or "cancel" in text)
    ) or "invalid state" in text


__all__ = [
    "CATGIRL_NAMES",
    "NAME_WINDOW_SECONDS",
    "OCR_OVERLAP_THRESHOLD",
    "OCR_TRUNCATION",
    "SHORT_TRANSCRIPT_CHARS",
    "VoiceFilter",
    "_derive_subject",
    "_find_earliest_name",
    "_has_question_intent",
    "_number_sequence_match",
    "_text_overlap_ratio",
    "build_context_for_catgirl",
    "safe_cancel_response",
]
