from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any

from .constants import MODE_COMPANION, MODE_CONCEPT_EXPLAIN, MODE_INTERACTIVE, MODE_TEACHING, SUPPORTED_MODES

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


def _json_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item) for item in value]
    if isinstance(value, tuple):
        return [_json_copy(item) for item in value]
    return value


def _strip_noise(text: str) -> str:
    return re.sub(r"^[\s,，。.!！？?:：;；—~·-]+|[\s,，。.!！？?:：;；—~·-]+$", "", str(text or "").strip())


def _meaningful_length(text: str) -> int:
    return sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _is_english_language(language: str | None) -> bool:
    language_tag = str(language or "").strip().lower().replace("_", "-")
    primary = language_tag.split("-", 1)[0]
    return primary == "en" or primary == "eng"


def normalize_mode(mode: str | None) -> str:
    candidate = str(mode or "").strip().lower()
    if candidate == MODE_CONCEPT_EXPLAIN:
        return MODE_COMPANION
    if candidate in SUPPORTED_MODES:
        return candidate
    return MODE_COMPANION


def mode_label(mode: str, *, language: str = "zh-CN") -> str:
    normalized = normalize_mode(mode)
    language_key = "en" if _is_english_language(language) else "zh"
    return _MODE_LABELS.get(language_key, _MODE_LABELS["zh"]).get(normalized, normalized)


def build_transition_phrase(
    mode: str,
    *,
    language: str = "zh-CN",
    outcome: str = "changed",
    lock_until: float = 0.0,
) -> str:
    label = mode_label(mode, language=language)
    is_english = _is_english_language(language)
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
    folded = normalized_text.casefold()
    for mode, keywords in _MODE_INTENT_RULES:
        for keyword in keywords:
            keyword_folded = keyword.casefold()
            if keyword_folded not in folded:
                continue
            remainder = normalized_text
            remainder = re.sub(re.escape(keyword), "", remainder, count=1, flags=re.IGNORECASE)
            remainder = _strip_noise(remainder)
            if _meaningful_length(remainder) < 4:
                remainder = ""
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
