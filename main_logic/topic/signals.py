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

from main_logic.topic.common import ZH_TOPIC_STOP_CHARS, clean_text, topic_units


_MAX_SIGNAL_TEXT_CHARS = 500
_MAX_GLOBAL_TURNS = 80
_READY_SCORE = 80
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


def _clean_text(value: Any, *, limit: int = _MAX_SIGNAL_TEXT_CHARS) -> str:
    return clean_text(value, limit=limit)


@dataclass(frozen=True)
class TopicTurnSignal:
    actor: str
    text: str
    timestamp: float
    lang: str


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
        lang: str = "zh",
        now: float | None = None,
    ) -> None:
        cleaned = _clean_text(text)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        safe_actor = "ai" if actor == "ai" else "user"
        self._turns[name].append(
            TopicTurnSignal(
                actor=safe_actor,
                text=cleaned,
                timestamp=float(now if now is not None else time.time()),
                lang=lang or "zh",
            )
        )

    def clear(self, lanlan_name: str) -> None:
        self._turns.pop(str(lanlan_name or "default"), None)

    def readiness_percent(self, lanlan_name: str) -> int:
        user_turns = self._user_turns(lanlan_name)
        if not user_turns:
            return 0
        meaningful = [turn for turn in user_turns if _turn_information_score(turn.text) >= 20]
        if not meaningful:
            return 0

        sample_score = min(
            40,
            int(len(meaningful) * 40 / self._min_user_turns_for_topic),
        )
        density_score = int(
            sum(_turn_information_score(turn.text) for turn in meaningful)
            / len(meaningful)
            * 0.5
        )
        stability_score = _stability_score(meaningful)
        return max(0, min(100, sample_score + density_score + stability_score))

    def is_ready(self, lanlan_name: str) -> bool:
        return self.readiness_percent(lanlan_name) >= _READY_SCORE

    def format_global_signals(self, lanlan_name: str, *, max_lines: int = 40) -> str:
        name = str(lanlan_name or "default")
        turns = list(self._turns.get(name, ()))
        user_count = sum(1 for turn in turns if turn.actor == "user")
        ai_count = sum(1 for turn in turns if turn.actor == "ai")
        readiness = self.readiness_percent(name)
        meaningful_user_turns = [
            turn for turn in turns
            if turn.actor == "user" and _turn_information_score(turn.text) >= 20
        ]
        density = _average_information_density(meaningful_user_turns)
        stability = _stability_score(meaningful_user_turns)

        lines = [
            f"收集进度: {readiness}%",
            f"用户证据数: {user_count}",
            f"有效用户证据数: {len(meaningful_user_turns)}",
            f"AI回应数: {ai_count}",
            f"信息密度: {density}%",
            f"稳定度: {stability}%",
            "说明: 这是跨最近窗口的慢收集证据。收集进度只是本地信息量估算；请由模型最终判断哪些点稳定、强相关、适合低频开口；不要按关键词硬凑。",
        ]
        if not turns:
            return "\n".join(lines)

        selected = _select_turns_for_prompt(turns, max_lines=max_lines)
        base_ts = turns[-1].timestamp
        lines.append("全局证据:")
        for turn in selected:
            age_s = max(0.0, base_ts - turn.timestamp)
            if age_s < 90:
                age = f"{int(age_s)}s前"
            elif age_s < 3600:
                age = f"{int(age_s / 60)}min前"
            else:
                age = f"{int(age_s / 3600)}h前"
            label = "用户" if turn.actor == "user" else "AI"
            lines.append(f"- [{age}] {label}: {turn.text}")
        return "\n".join(lines)

    def _user_turns(self, lanlan_name: str) -> list[TopicTurnSignal]:
        name = str(lanlan_name or "default")
        return [turn for turn in self._turns.get(name, ()) if turn.actor == "user"]


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


def _turn_information_score(text: str) -> int:
    cleaned = _clean_text(text, limit=120)
    if not cleaned:
        return 0
    normalized = cleaned.lower()
    if normalized in _FILLER_TEXTS:
        return 0
    cjk_count = sum(1 for char in cleaned if "\u4e00" <= char <= "\u9fff")
    ascii_count = sum(1 for char in cleaned if char.isalnum() and not ("\u4e00" <= char <= "\u9fff"))
    signal_len = cjk_count + ascii_count
    if signal_len <= 2:
        return 0
    if signal_len <= 4:
        return 12

    score = min(60, int(signal_len * 2.5))
    unique_units = _topic_units(cleaned)
    score += min(20, int(len(unique_units) * 2.5))
    if any(mark in cleaned for mark in "，。！？,.!?"):
        score += 5
    if len(cleaned) >= 18:
        score += 8
    if len(cleaned) >= 30:
        score += 7
    return max(0, min(100, score))


def _average_information_density(turns: Iterable[TopicTurnSignal]) -> int:
    scores = [_turn_information_score(turn.text) for turn in turns]
    if not scores:
        return 0
    return int(sum(scores) / len(scores))


def _topic_units(text: str) -> set[str]:
    return topic_units(text, limit=120, stop_chars=ZH_TOPIC_STOP_CHARS)


def _stability_score(turns: Iterable[TopicTurnSignal]) -> int:
    unit_counts: dict[str, int] = defaultdict(int)
    valid_turns = 0
    for turn in turns:
        units = _topic_units(turn.text)
        if not units:
            continue
        valid_turns += 1
        for unit in units:
            unit_counts[unit] += 1
    if valid_turns < 2:
        return 0
    repeated_units = [
        unit for unit, count in unit_counts.items()
        if count >= 2 and (len(unit) >= 2 or "\u4e00" <= unit <= "\u9fff")
    ]
    return min(30, len(repeated_units) * 5)
