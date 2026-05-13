from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import re
import time
from typing import Any

from plugin.sdk.shared.i18n import load_plugin_i18n_from_dir

from .constants import MODE_COMPANION, MODE_CONCEPT_EXPLAIN, MODE_INTERACTIVE, MODE_TEACHING, SUPPORTED_MODES
from .json_utils import json_copy

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
    "解释",
    "解释下",
    "解释一下",
    "说明",
    "explain",
    "explain this",
    "please explain",
)

_MODE_SWITCH_PREFIXES: tuple[str, ...] = (
    "switch to",
    "switch into",
    "change to",
    "change into",
    "set mode to",
    "set to",
    "turn on",
    "go to",
    "enter",
    "enable",
    "use",
    "please",
    "teach me",
    "study with me",
    "切换到",
    "切到",
    "切换",
    "改成",
    "设为",
    "设置成",
    "进入",
    "开启",
    "打开",
    "启用",
    "使用",
    "请",
    "教我",
)

_MODE_PREFIX_REQUIRED_KEYWORDS = frozenset(
    {
        "teach",
        "interactive",
        "discussion",
        "discuss",
        "companion",
        "教学",
        "互动",
        "讨论",
        "伴学",
        "讲解",
    }
)


def _strip_noise(text: str) -> str:
    return re.sub(r"^[\s,，。.!！？?:：;；—~·\-\\]+|[\s,，。.!！？?:：;；—~·\-\\]+$", "", str(text or "").strip())


def _keyword_pattern(keyword: str) -> str:
    candidate = str(keyword or "").strip()
    if not candidate:
        return ""
    if all(ord(char) < 128 for char in candidate):
        escaped = r"\s+".join(re.escape(part) for part in candidate.split())
        return rf"(?<![0-9A-Za-z_]){escaped}(?![0-9A-Za-z_])"
    return re.escape(candidate)


def _find_keyword_match(text: str, keyword: str) -> re.Match[str] | None:
    pattern = _keyword_pattern(keyword)
    if not pattern:
        return None
    return re.search(pattern, text, flags=re.IGNORECASE)


def _command_prefix_start(text: str, match_start: int) -> int:
    prefix = text[:match_start]
    chain_start = match_start
    search_end = len(prefix)
    while True:
        best_match = None
        for candidate in sorted(_MODE_SWITCH_PREFIXES, key=len, reverse=True):
            pattern = rf"{re.escape(candidate)}[\s,，。.!！？?:：;；—~·\-\\]*$"
            match = re.search(pattern, prefix[:search_end], flags=re.IGNORECASE)
            if match is not None:
                best_match = match
                break
        if best_match is None:
            return chain_start
        chain_start = best_match.start()
        if chain_start == 0:
            return 0
        search_end = chain_start


def _is_english_language(language: str | None) -> bool:
    language_tag = str(language or "").strip().lower().replace("_", "-")
    primary = re.split(r"[-]", language_tag, maxsplit=1)[0]
    return primary == "en" or primary == "eng"


def _is_chinese_language(language: str | None) -> bool:
    language_tag = str(language or "").strip().lower().replace("_", "-")
    primary = re.split(r"[-]", language_tag, maxsplit=1)[0]
    return primary in {"zh", "zho", "chi"}


@lru_cache(maxsize=1)
def _study_i18n():
    return load_plugin_i18n_from_dir(Path(__file__).resolve().parent / "i18n", default_locale="en")


def study_i18n_t(language: str | None, key: str, *, default: str = "", **params: object) -> str:
    return _study_i18n().t(key, locale=language, default=default, **params)


def _transition_i18n_or_fallback(language: str | None, key: str, fallback: str, **params: object) -> str:
    localized = study_i18n_t(language, key, default="", **params)
    if localized and localized != key:
        return localized
    return fallback.format(**params)


def normalize_mode(mode: str | None) -> str:
    candidate = str(mode or "").strip().lower()
    if candidate == MODE_CONCEPT_EXPLAIN:
        return MODE_COMPANION
    if candidate in SUPPORTED_MODES:
        return candidate
    return MODE_COMPANION


def mode_label(mode: str, *, language: str = "zh-CN") -> str:
    normalized = normalize_mode(mode)
    fallback = _MODE_LABELS["en"].get(normalized, normalized)
    return study_i18n_t(language, f"status.mode.{normalized}", default=fallback)


def build_transition_phrase(
    mode: str,
    *,
    language: str = "zh-CN",
    outcome: str = "changed",
    lock_until: float = 0.0,
) -> str:
    label = mode_label(mode, language=language)
    is_english = _is_english_language(language)
    is_chinese = _is_chinese_language(language)
    normalized = normalize_mode(mode)
    if outcome == "same":
        fallback = "当前已经是{label}。" if is_chinese else "You are already in {label}."
        return _transition_i18n_or_fallback(language, "status.transition.same", fallback, label=label)
    if outcome == "locked":
        if lock_until:
            remaining = max(1, int(round(lock_until - time.time())))
            fallback = (
                "模式切换已进入温和锁定，还要再等约 {remaining_seconds} 秒。"
                if is_chinese
                else "Mode switching is temporarily locked for {remaining_seconds} second(s)."
            )
            return _transition_i18n_or_fallback(
                language,
                "status.transition.locked",
                fallback,
                label=label,
                remaining_seconds=remaining,
            )
        return "模式切换已进入温和锁定。" if is_chinese else "Mode switching is temporarily locked."
    if outcome == "dwell":
        fallback = (
            "当前模式刚切换不久，请先停留 3 分钟再切换。"
            if is_chinese
            else "Please keep the current mode for 3 minutes before switching again."
        )
        return _transition_i18n_or_fallback(language, "status.transition.dwell", fallback, label=label)
    if normalized == MODE_TEACHING and is_chinese:
        return "教学模式已开启。"
    fallback = "已切换到{label}。" if is_chinese else "{label} enabled."
    return _transition_i18n_or_fallback(language, "status.transition.changed", fallback, label=label)


def handle_user_intent(text: str, *, language: str = "zh-CN") -> dict[str, Any]:
    normalized_text = str(text or "").strip()
    best_mode_match: tuple[int, str, str, re.Match[str], int] | None = None
    for mode, keywords in _MODE_INTENT_RULES:
        for keyword in keywords:
            match = _find_keyword_match(normalized_text, keyword)
            if match is None:
                continue
            removal_start = _command_prefix_start(normalized_text, match.start())
            keyword_folded = keyword.casefold()
            if keyword_folded in _MODE_PREFIX_REQUIRED_KEYWORDS and removal_start == match.start():
                continue
            score = match.end() - removal_start
            if best_mode_match is None or score > best_mode_match[0]:
                best_mode_match = (score, mode, keyword, match, removal_start)
    if best_mode_match is not None:
        _, mode, keyword, match, removal_start = best_mode_match
        remainder = f"{normalized_text[:removal_start]}{normalized_text[match.end():]}"
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
        match = _find_keyword_match(normalized_text, keyword)
        if match is None:
            continue
        removal_start = _command_prefix_start(normalized_text, match.start())
        remainder = f"{normalized_text[:removal_start]}{normalized_text[match.end():]}"
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
            "recent_mode_switches": json_copy(self.recent_mode_switches),
            "suggestion_cooldowns": {str(key): float(value) for key, value in self.suggestion_cooldowns.items()},
            "session_suggestions": json_copy(self.session_suggestions),
            "mode_lock_until": float(self.mode_lock_until or 0.0),
        }

    def restore(self, payload: dict[str, Any] | None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self.current_mode = normalize_mode(payload.get("current_mode") or payload.get("active_mode") or self.current_mode)
        self.mode_started_at = _coerce_timestamp(payload.get("mode_started_at"), self.mode_started_at)
        self.mode_lock_until = _coerce_timestamp(payload.get("mode_lock_until"), self.mode_lock_until)
        self.recent_mode_switches = [
            item
            for item in (json_copy(payload.get("recent_mode_switches")) if isinstance(payload.get("recent_mode_switches"), list) else [])
            if isinstance(item, dict)
        ]
        self.suggestion_cooldowns = {}
        raw_cooldowns = payload.get("suggestion_cooldowns")
        if isinstance(raw_cooldowns, dict):
            for key, value in raw_cooldowns.items():
                self.suggestion_cooldowns[str(key)] = _coerce_timestamp(value, 0.0)
        raw_session = payload.get("session_suggestions")
        self.session_suggestions = [item for item in (json_copy(raw_session) if isinstance(raw_session, list) else []) if isinstance(item, dict)]

    def _prune_recent_mode_switches(self, now_ts: float) -> None:
        self.recent_mode_switches = [
            item
            for item in self.recent_mode_switches
            if now_ts - _coerce_timestamp(item.get("at"), now_ts) <= MODE_SWITCH_WINDOW_SECONDS
        ]

    def _record_mode_switch_attempt(self, *, mode: str, reason: str, at: float) -> None:
        self._prune_recent_mode_switches(at)
        self.recent_mode_switches.append({"mode": mode, "reason": reason, "at": at})

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

        self._record_mode_switch_attempt(mode=requested_mode, reason=reason, at=now_ts)
        checkpoint_after_attempt = self.snapshot()
        if self.mode_started_at and now_ts - self.mode_started_at < MODE_MIN_DWELL_SECONDS:
            if len(self.recent_mode_switches) >= 3:
                self.mode_lock_until = now_ts + MODE_LOCK_SECONDS
                checkpoint_after_lock = self.snapshot()
                checkpoint_after_lock.update(
                    {
                        "changed": False,
                        "old_mode": current_mode,
                        "new_mode": current_mode,
                        "reason": reason,
                        "transition_phrase": build_transition_phrase(
                            current_mode,
                            language=language,
                            outcome="locked",
                            lock_until=self.mode_lock_until,
                        ),
                    }
                )
                return {
                    "changed": False,
                    "old_mode": current_mode,
                    "new_mode": current_mode,
                    "transition_phrase": checkpoint_after_lock["transition_phrase"],
                    "reason": reason,
                    "locked": True,
                    "lock_reason": "mode_lock",
                    "lock_until": float(self.mode_lock_until or 0.0),
                    "checkpoint": checkpoint_after_lock,
                }
            return {
                "changed": False,
                "old_mode": current_mode,
                "new_mode": current_mode,
                "transition_phrase": build_transition_phrase(current_mode, language=language, outcome="dwell"),
                "reason": reason,
                "locked": True,
                "lock_reason": "minimum_dwell",
                "lock_until": float(self.mode_started_at + MODE_MIN_DWELL_SECONDS),
                "checkpoint": checkpoint_after_attempt,
            }

        self.current_mode = requested_mode
        self.mode_started_at = now_ts
        self.mode_lock_until = 0.0
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
