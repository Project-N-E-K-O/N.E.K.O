from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any


MODE_COMPANION = "companion"
MODE_INTERACTIVE = "interactive"
MODE_TEACHING = "teaching"
MODE_CONCEPT_EXPLAIN = "concept_explain"

SUPPORTED_MODES = frozenset({MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING})

MODE_MIN_DWELL_SECONDS = 180.0
MODE_SWITCH_WINDOW_SECONDS = 180.0
MODE_LOCK_SECONDS = 180.0

_MODE_LABELS = {
    "zh": {
        MODE_COMPANION: "伴学模式",
        MODE_INTERACTIVE: "互动模式",
        MODE_TEACHING: "教学模式",
    },
    "en": {
        MODE_COMPANION: "companion mode",
        MODE_INTERACTIVE: "interactive mode",
        MODE_TEACHING: "teaching mode",
    },
}

_MODE_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        MODE_TEACHING,
        (
            "教学模式",
            "教学",
            "教我",
            "讲解模式",
            "讲解",
            "老师模式",
            "teach me",
            "teach",
            "teacher mode",
            "teaching mode",
        ),
    ),
    (
        MODE_INTERACTIVE,
        (
            "互动模式",
            "互动",
            "讨论模式",
            "讨论",
            "问答",
            "一起想",
            "一起思考",
            "interactive mode",
            "discussion mode",
            "interactive",
            "discussion",
            "discuss",
        ),
    ),
    (
        MODE_COMPANION,
        (
            "伴学模式",
            "伴学",
            "陪我学",
            "陪我",
            "陪学",
            "陪读",
            "companion mode",
            "companion",
            "study with me",
            "study mode",
        ),
    ),
)

_EXPLAIN_INTENT_RULES: tuple[str, ...] = (
    "解释一下",
    "解释下",
    "解释",
    "说明",
    "explain this",
    "please explain",
    "explain",
)

_MODE_SWITCH_PREFIXES: tuple[str, ...] = (
    "set mode to",
    "switch into",
    "switch to",
    "change into",
    "change to",
    "turn on",
    "set to",
    "go to",
    "enter",
    "enable",
    "use",
    "please",
    "teach me",
    "study with me",
    "请教我",
    "切换到",
    "切到",
    "切换",
    "设置成",
    "改成",
    "设为",
    "进入",
    "开启",
    "打开",
    "启用",
    "使用",
    "教我",
    "用",
    "请",
)


def _json_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item) for item in value]
    if isinstance(value, tuple):
        return [_json_copy(item) for item in value]
    return value


def _strip_noise(text: str) -> str:
    return re.sub(r"^[\s,，。.!！？?:：;；—~·\-\\]+|[\s,，。.!！？?:：;；—~·\-\\]+$", "", str(text or "").strip())


def _is_ascii_keyword(keyword: str) -> bool:
    return all(ord(char) < 128 for char in keyword)


def _previous_char_is_word(text: str, start: int) -> bool:
    if start <= 0:
        return False
    previous = text[start - 1]
    return bool(re.fullmatch(r"[0-9A-Za-z_]", previous) or re.fullmatch(r"[\u3400-\u9fff\uf900-\ufaff]", previous))


def _ends_with_mode_switch_prefix(prefix: str) -> bool:
    folded = _strip_noise(prefix).casefold()
    return any(folded.endswith(item) for item in _MODE_SWITCH_PREFIXES)


def _mode_command_span_start(text: str, match_start: int) -> int:
    prefix = text[:match_start]
    for item in sorted(_MODE_SWITCH_PREFIXES, key=len, reverse=True):
        pattern = rf"{re.escape(item)}[\s,，。.!！？?:：;；—~·\-\\]*$"
        match = re.search(pattern, prefix, flags=re.IGNORECASE)
        if match:
            return match.start()
    return match_start


def _keyword_pattern(keyword: str) -> str:
    parts = [re.escape(part) for part in str(keyword or "").strip().split()]
    body = r"\s+".join(parts) if parts else re.escape(str(keyword or ""))
    if _is_ascii_keyword(keyword):
        return rf"(?<![0-9A-Za-z_]){body}(?![0-9A-Za-z_])"
    return body


def _find_mode_keyword_match(text: str, keyword: str) -> re.Match[str] | None:
    keyword_folded = keyword.casefold()
    command_required = keyword_folded in {
        "teach",
        "interactive",
        "discussion",
        "discuss",
        "companion",
        "教学",
        "互动",
        "讨论",
        "问答",
        "陪我",
        "陪学",
        "陪读",
        "讲解",
        "讲讲",
        "讲一下",
    }
    direct_command = keyword_folded in {
        "教我",
        "teach me",
        "study with me",
        "陪我学",
        "一起想",
        "一起思考",
    }
    is_mode_label = "mode" in keyword_folded or "模式" in keyword

    for match in re.finditer(_keyword_pattern(keyword), text, flags=re.IGNORECASE):
        if _is_ascii_keyword(keyword) and _previous_char_is_word(text, match.start()):
            continue
        prefix_allows_command = _ends_with_mode_switch_prefix(text[: match.start()])
        starts_cleanly = match.start() == 0 or not _previous_char_is_word(text, match.start())
        if command_required:
            if prefix_allows_command:
                return match
            continue
        if direct_command or is_mode_label or prefix_allows_command or starts_cleanly:
            return match
    return None


def _find_explain_keyword_match(text: str, keyword: str) -> re.Match[str] | None:
    for match in re.finditer(_keyword_pattern(keyword), text, flags=re.IGNORECASE):
        if match.start() == 0 or not _previous_char_is_word(text, match.start()):
            return match
    return None


def _meaningful_length(text: str) -> int:
    return sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def normalize_mode(mode: str | None) -> str:
    candidate = str(mode or "").strip().lower()
    if candidate == MODE_CONCEPT_EXPLAIN:
        return MODE_COMPANION
    if candidate in SUPPORTED_MODES:
        return candidate
    return MODE_COMPANION


def mode_label(mode: str, *, language: str = "zh-CN") -> str:
    normalized = normalize_mode(mode)
    language_key = "en" if str(language or "").lower().startswith("en") else "zh"
    return _MODE_LABELS.get(language_key, _MODE_LABELS["zh"]).get(normalized, normalized)


def build_transition_phrase(
    mode: str,
    *,
    language: str = "zh-CN",
    outcome: str = "changed",
    lock_until: float = 0.0,
) -> str:
    label = mode_label(mode, language=language)
    is_english = str(language or "").lower().startswith("en")
    if outcome == "same":
        return f"You are already in {label}." if is_english else f"当前已经是{label}。"
    if outcome == "locked":
        if lock_until:
            remaining = max(1, int(round(lock_until - time.time())))
            if is_english:
                return f"Mode switching is temporarily locked for {remaining} second(s)."
            return f"模式切换已进入温和锁定，还要再等约 {remaining} 秒。"
        return "Mode switching is temporarily locked." if is_english else "模式切换已进入温和锁定。"
    if outcome == "dwell":
        return (
            f"Please keep the current mode for 3 minutes before switching again."
            if is_english
            else "当前模式刚切换不久，请先停留 3 分钟再切换。"
        )
    if normalized := normalize_mode(mode):
        if normalized == MODE_TEACHING and not is_english:
            return "教学模式已开启。"
        if is_english:
            return f"{mode_label(normalized, language=language).capitalize()} enabled."
        return f"已切换到{label}。"
    return "Mode updated." if is_english else "模式已更新。"


def handle_user_intent(text: str, *, language: str = "zh-CN") -> dict[str, Any]:
    normalized_text = str(text or "").strip()
    best_mode_match: tuple[int, str, str, re.Match[str]] | None = None
    for mode, keywords in _MODE_INTENT_RULES:
        for keyword in keywords:
            match = _find_mode_keyword_match(normalized_text, keyword)
            if match is None:
                continue
            score = match.end() - match.start()
            if best_mode_match is None or score > best_mode_match[0]:
                best_mode_match = (score, mode, keyword, match)
    if best_mode_match is not None:
        _, mode, keyword, match = best_mode_match
        remove_start = _mode_command_span_start(normalized_text, match.start())
        remainder = f"{normalized_text[:remove_start]}{normalized_text[match.end() :]}"
        remainder = _strip_noise(remainder)
        return {
            "matched": True,
            "kind": "mode_switch",
            "mode": mode,
            "keyword": keyword,
            "pure_switch": not remainder,
            "remaining_text": remainder,
            "normalized_text": normalized_text,
            "transition_phrase": build_transition_phrase(mode, language=language, outcome="changed"),
        }
    for keyword in sorted(_EXPLAIN_INTENT_RULES, key=len, reverse=True):
        match = _find_explain_keyword_match(normalized_text, keyword)
        if match is None:
            continue
        remainder = f"{normalized_text[: match.start()]}{normalized_text[match.end() :]}"
        remainder = _strip_noise(remainder)
        return {
            "matched": True,
            "kind": "concept_explain",
            "mode": MODE_CONCEPT_EXPLAIN,
            "keyword": keyword,
            "pure_switch": False,
            "remaining_text": remainder,
            "normalized_text": normalized_text,
            "transition_phrase": "",
        }
    return {
        "matched": False,
        "kind": "",
        "mode": "",
        "keyword": "",
        "pure_switch": False,
        "remaining_text": normalized_text,
        "normalized_text": normalized_text,
        "transition_phrase": "",
    }


def _coerce_timestamp(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(slots=True)
class ModeManager:
    current_mode: str = MODE_COMPANION
    mode_started_at: float = 0.0
    recent_mode_switches: list[dict[str, Any]] = field(default_factory=list)
    suggestion_cooldowns: dict[str, float] = field(default_factory=dict)
    session_suggestions: list[dict[str, Any]] = field(default_factory=list)
    mode_lock_until: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "current_mode": normalize_mode(self.current_mode),
            "mode_started_at": float(self.mode_started_at or 0.0),
            "recent_mode_switches": _json_copy(self.recent_mode_switches),
            "suggestion_cooldowns": {str(key): float(value) for key, value in self.suggestion_cooldowns.items()},
            "session_suggestions": _json_copy(self.session_suggestions),
            "mode_lock_until": float(self.mode_lock_until or 0.0),
        }

    def restore(self, payload: dict[str, Any] | None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self.current_mode = normalize_mode(payload.get("current_mode") or payload.get("active_mode") or self.current_mode)
        self.mode_started_at = _coerce_timestamp(payload.get("mode_started_at"), self.mode_started_at)
        self.mode_lock_until = _coerce_timestamp(payload.get("mode_lock_until"), self.mode_lock_until)
        self.recent_mode_switches = [
            item
            for item in (_json_copy(payload.get("recent_mode_switches")) if isinstance(payload.get("recent_mode_switches"), list) else [])
            if isinstance(item, dict)
        ]
        self.suggestion_cooldowns = {}
        raw_cooldowns = payload.get("suggestion_cooldowns")
        if isinstance(raw_cooldowns, dict):
            for key, value in raw_cooldowns.items():
                self.suggestion_cooldowns[str(key)] = _coerce_timestamp(value, 0.0)
        raw_session = payload.get("session_suggestions")
        self.session_suggestions = [item for item in (_json_copy(raw_session) if isinstance(raw_session, list) else []) if isinstance(item, dict)]

    def switch_to(
        self,
        mode: str,
        reason: str,
        now: float | None = None,
        *,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        raw_mode = str(mode or "").strip().lower()
        if raw_mode not in SUPPORTED_MODES and raw_mode != MODE_CONCEPT_EXPLAIN:
            raise ValueError(f"unsupported mode: {mode}")
        requested_mode = normalize_mode(raw_mode)
        now_ts = float(time.time() if now is None else now)
        current_mode = normalize_mode(self.current_mode)
        checkpoint_before = self.snapshot()

        if current_mode == requested_mode:
            return {
                "changed": False,
                "old_mode": current_mode,
                "new_mode": current_mode,
                "transition_phrase": build_transition_phrase(current_mode, language=language, outcome="same"),
                "reason": reason,
                "locked": False,
                "lock_reason": "",
                "lock_until": float(self.mode_lock_until or 0.0),
                "checkpoint": checkpoint_before,
            }

        if self.mode_lock_until and now_ts < self.mode_lock_until:
            return {
                "changed": False,
                "old_mode": current_mode,
                "new_mode": current_mode,
                "transition_phrase": build_transition_phrase(
                    current_mode,
                    language=language,
                    outcome="locked",
                    lock_until=self.mode_lock_until,
                ),
                "reason": reason,
                "locked": True,
                "lock_reason": "mode_lock",
                "lock_until": float(self.mode_lock_until),
                "checkpoint": checkpoint_before,
            }

        if self.mode_started_at and now_ts - self.mode_started_at < MODE_MIN_DWELL_SECONDS:
            return {
                "changed": False,
                "old_mode": current_mode,
                "new_mode": current_mode,
                "transition_phrase": build_transition_phrase(current_mode, language=language, outcome="dwell"),
                "reason": reason,
                "locked": True,
                "lock_reason": "minimum_dwell",
                "lock_until": float(self.mode_started_at + MODE_MIN_DWELL_SECONDS),
                "checkpoint": checkpoint_before,
            }

        self.current_mode = requested_mode
        self.mode_started_at = now_ts
        self.mode_lock_until = 0.0
        self.recent_mode_switches = [
            item
            for item in self.recent_mode_switches
            if now_ts - _coerce_timestamp(item.get("at"), now_ts) <= MODE_SWITCH_WINDOW_SECONDS
        ]
        self.recent_mode_switches.append({"mode": requested_mode, "reason": reason, "at": now_ts})
        if len(self.recent_mode_switches) >= 3:
            self.mode_lock_until = now_ts + MODE_LOCK_SECONDS

        checkpoint_after = self.snapshot()
        checkpoint_after.update(
            {
                "changed": True,
                "old_mode": current_mode,
                "new_mode": requested_mode,
                "reason": reason,
                "transition_phrase": build_transition_phrase(requested_mode, language=language, outcome="changed"),
            }
        )
        return {
            "changed": True,
            "old_mode": current_mode,
            "new_mode": requested_mode,
            "transition_phrase": checkpoint_after["transition_phrase"],
            "reason": reason,
            "locked": False,
            "lock_reason": "",
            "lock_until": float(self.mode_lock_until or 0.0),
            "checkpoint": checkpoint_after,
        }


default_mode_manager = ModeManager()


def switch_to(mode: str, reason: str, now: float | None = None) -> dict[str, Any]:
    return default_mode_manager.switch_to(mode, reason, now=now)
