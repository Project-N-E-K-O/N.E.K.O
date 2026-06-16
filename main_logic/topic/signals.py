"""Slow global signal collection for topic hooks.

The signal layer deliberately does not decide what the user cares about.
It only keeps compact evidence across a longer window so the LLM can judge
stable, high-readiness topic opportunities instead of overfitting the last
few chat turns.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from main_logic.topic.common import clean_text
from utils.tokenize import truncate_to_tokens


_MAX_SIGNAL_TEXT_CHARS = 500
# Per-turn evidence cap in tokens. The topic candidate prompt feeds this slow
# evidence as its only conversation input, so the per-turn budget lives here.
_MAX_SIGNAL_TOKENS_PER_TURN = 300
_MAX_GLOBAL_TURNS = 80
_FILLER_TEXTS = {
    "你好",
    "啊",
    "嗯",
    "哦",
    "好",
    "可以",
    "对",
    "對",
    "行",
    "行吧",
    "哈哈",
    "没事",
    "沒事",
    "不知道",
}


_GLOBAL_SIGNAL_LABELS = {
    "zh": {
        "user": "用户",
        "ai": "AI",
        "seconds_ago": "{value}s前",
        "minutes_ago": "{value}min前",
        "hours_ago": "{value}h前",
    },
    "zh-TW": {
        "user": "使用者",
        "ai": "AI",
        "seconds_ago": "{value}s前",
        "minutes_ago": "{value}min前",
        "hours_ago": "{value}h前",
    },
    "en": {
        "user": "User",
        "ai": "AI",
        "seconds_ago": "{value}s ago",
        "minutes_ago": "{value}min ago",
        "hours_ago": "{value}h ago",
    },
    "ja": {
        "user": "ユーザー",
        "ai": "AI",
        "seconds_ago": "{value}秒前",
        "minutes_ago": "{value}分前",
        "hours_ago": "{value}時間前",
    },
    "ko": {
        "user": "사용자",
        "ai": "AI",
        "seconds_ago": "{value}초 전",
        "minutes_ago": "{value}분 전",
        "hours_ago": "{value}시간 전",
    },
    "es": {
        "user": "Usuario",
        "ai": "IA",
        "seconds_ago": "hace {value}s",
        "minutes_ago": "hace {value}min",
        "hours_ago": "hace {value}h",
    },
    "pt": {
        "user": "Usuário",
        "ai": "IA",
        "seconds_ago": "há {value}s",
        "minutes_ago": "há {value}min",
        "hours_ago": "há {value}h",
    },
    "ru": {
        "user": "Пользователь",
        "ai": "AI",
        "seconds_ago": "{value}с назад",
        "minutes_ago": "{value}мин назад",
        "hours_ago": "{value}ч назад",
    },
}


def _clean_text(value: Any, *, limit: int = _MAX_SIGNAL_TEXT_CHARS) -> str:
    return clean_text(value, limit=limit)


def _label_key_for_lang(lang: str | None) -> str:
    raw = str(lang or "").strip().replace("_", "-")
    if not raw:
        return "zh"
    if raw in _GLOBAL_SIGNAL_LABELS:
        return raw
    lower = raw.lower()
    if lower.startswith(("zh-tw", "zh-hant", "zh-hk")):
        return "zh-TW"
    if lower.startswith("zh"):
        return "zh"
    short = lower.split("-", 1)[0]
    return short if short in _GLOBAL_SIGNAL_LABELS else "en"


def _format_age(age_s: float, labels: Mapping[str, str]) -> str:
    if age_s < 90:
        return labels["seconds_ago"].format(value=int(age_s))
    if age_s < 3600:
        return labels["minutes_ago"].format(value=int(age_s / 60))
    return labels["hours_ago"].format(value=int(age_s / 3600))


@dataclass(frozen=True)
class TopicTurnSignal:
    actor: str
    text: str
    timestamp: float


class TopicSignalStore:
    """In-memory slow evidence store, scoped per character."""

    def __init__(
        self,
        *,
        min_user_turns_for_topic: int = 4,
        max_turns: int = _MAX_GLOBAL_TURNS,
    ) -> None:
        self._min_user_turns_for_topic = max(1, int(min_user_turns_for_topic))
        self._turns: dict[str, deque[TopicTurnSignal]] = defaultdict(
            lambda: deque(maxlen=max(1, int(max_turns)))
        )

    def note_turn(
        self,
        lanlan_name: str,
        *,
        actor: str,
        text: Any,
        now: float | None = None,
    ) -> None:
        cleaned = truncate_to_tokens(_clean_text(text), _MAX_SIGNAL_TOKENS_PER_TURN)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        safe_actor = "ai" if actor == "ai" else "user"
        self._turns[name].append(
            TopicTurnSignal(
                actor=safe_actor,
                text=cleaned,
                timestamp=float(now if now is not None else time.time()),
            )
        )

    def clear(self, lanlan_name: str) -> None:
        self._turns.pop(str(lanlan_name or "default"), None)

    def readiness_percent(self, lanlan_name: str) -> int:
        # Coarse "have we heard enough to bother analysing" estimate, for logs.
        count = len(self._meaningful_user_turns(lanlan_name))
        return min(100, int(count * 100 / self._min_user_turns_for_topic))

    def is_ready(self, lanlan_name: str) -> bool:
        return (
            len(self._meaningful_user_turns(lanlan_name))
            >= self._min_user_turns_for_topic
        )

    def format_global_signals(self, lanlan_name: str, *, max_lines: int = 40, lang: str | None = None) -> str:
        """Render the slow-evidence turns as the topic prompt's only context.

        Just the turn list — the readiness count gate (see ``is_ready`` /
        ``readiness_percent``) stays backend-only and never reaches the prompt.
        The caller fences this block with the conversation-history watermark.
        """
        name = str(lanlan_name or "default")
        labels = _GLOBAL_SIGNAL_LABELS[_label_key_for_lang(lang)]
        turns = list(self._turns.get(name, ()))
        if not turns:
            return ""

        selected = _select_turns_for_prompt(turns, max_lines=max_lines)
        base_ts = turns[-1].timestamp
        lines: list[str] = []
        for turn in selected:
            age_s = max(0.0, base_ts - turn.timestamp)
            age = _format_age(age_s, labels)
            label = labels["user"] if turn.actor == "user" else labels["ai"]
            lines.append(f"- [{age}] {label}: {turn.text}")
        return "\n".join(lines)

    def _user_turns(self, lanlan_name: str) -> list[TopicTurnSignal]:
        name = str(lanlan_name or "default")
        return [turn for turn in self._turns.get(name, ()) if turn.actor == "user"]

    def _meaningful_user_turns(self, lanlan_name: str) -> list[TopicTurnSignal]:
        return [
            turn for turn in self._user_turns(lanlan_name)
            if _is_meaningful_turn(turn.text)
        ]


def _select_turns_for_prompt(
    turns: Iterable[TopicTurnSignal],
    *,
    max_lines: int,
) -> list[TopicTurnSignal]:
    try:
        max_lines = int(max_lines)
    except (TypeError, ValueError):
        max_lines = 0
    if max_lines <= 0:
        return []
    all_turns = list(turns)
    if len(all_turns) <= max_lines:
        return all_turns
    head_count = min(12, max_lines // 2)
    tail_count = max_lines - head_count
    return all_turns[:head_count] + all_turns[-tail_count:]


def _is_meaningful_turn(text: str) -> bool:
    """Whether a user turn carries enough signal to count toward readiness.

    Filler words and near-empty turns don't count; anything with a few real
    information characters does. Coarse "have we heard enough to bother
    analysing" gate, not a quality score.
    """
    cleaned = _clean_text(text, limit=120)
    if not cleaned or cleaned.lower() in _FILLER_TEXTS:
        return False
    signal_len = sum(
        1 for char in cleaned
        if ("一" <= char <= "鿿") or char.isalnum()
    )
    return signal_len >= 3
