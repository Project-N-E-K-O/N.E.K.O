"""Room-topic prompt context owned by the live_events module."""

from __future__ import annotations

import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ...core.viewer_preferences import infer_viewer_preferences
from .provider_event import (
    event_nickname,
    event_prompt_text,
    event_type,
    event_uid,
    public_text,
)
from .room_pulse import (
    ROOM_PULSE_ACTIVITY_WINDOW_SECONDS,
    build_room_pulse,
    public_room_theme_key,
)
from .room_pulse_prompt import RoomPulsePrompt, render_room_pulse_prompt


_LOW_QUALITY_DANMAKU = {
    "1",
    "11",
    "111",
    "6",
    "66",
    "666",
    "?",
    "??",
    "hhh",
    "hhhh",
    "233",
    "2333",
    "www",
    "哈哈",
    "草",
}

_SCENE_RULES: tuple[tuple[str, tuple[str, ...], str, str, str], ...] = (
    (
        "question_help",
        ("?", "？", "怎么", "为什么", "如何", "请问", "求教", "有没有"),
        "questions / help",
        "Answer the shared question first, then pick one representative message.",
        "Give a short answer, then offer one light follow-up angle.",
    ),
    (
        "meme_play",
        ("梗", "笑死", "草", "哈哈", "hhh", "233", "节目效果", "名场面"),
        "meme / joke",
        "Catch the joke briefly, then pull it back to the live topic.",
        "Do not explain the meme like a lecture.",
    ),
    (
        "praise_support",
        ("可爱", "好看", "好听", "喜欢", "支持", "加油", "贴贴", "awsl"),
        "praise / support",
        "Thank the room playfully, then turn praise into one small interaction hook.",
        "Name at most one viewer; do not thank everyone one by one.",
    ),
    (
        "negative_comfort",
        ("无聊", "不好", "不行", "难看", "难听", "卡", "延迟", "垃圾", "退了"),
        "negative / comfort",
        "Acknowledge the shared feeling, then adjust or explain without attacking viewers.",
        "Reply to the representative concern; avoid amplifying negativity.",
    ),
    (
        "recommend_tutorial",
        ("推荐", "安利", "教程", "想学", "攻略", "怎么做", "怎么弄"),
        "recommendation / tutorial",
        "Group scattered requests into a tutorial or recommendation theme.",
        "Offer one or two directions, then ask beginner or advanced if needed.",
    ),
    (
        "greeting",
        ("你好", "嗨", "hi", "hello", "晚上好", "下午好", "早上好"),
        "greetings",
        "Welcome viewers as a batch, not one by one.",
        "Fold the greeting into the current room topic.",
    ),
)

@dataclass(frozen=True, slots=True)
class DanmakuCandidate:
    uid: str
    nickname: str
    text: str
    score: float
    ts: float


@dataclass
class _ViewerMemory:
    uid: str
    nickname: str = ""
    message_count: int = 0
    summaries: Counter[str] = field(default_factory=Counter)
    style: str = ""
    response_preference: str = ""
    last_seen_at: float = 0.0


@dataclass
class _Theme:
    key: str
    title: str
    count: int = 0
    score: float = 0.0
    examples: list[dict[str, str]] = field(default_factory=list)
    viewer_uids: set[str] = field(default_factory=set)
    reply_tip: str = ""
    technique: str = ""


class RoomTopicContext:
    """Short-lived danmaku memory used only as prompt guidance."""

    def __init__(
        self,
        *,
        now: Callable[[], float],
        window_seconds: float = 45.0,
        max_candidates: int = 80,
    ) -> None:
        self._now = now
        self._window_seconds = float(window_seconds)
        self._recent: deque[DanmakuCandidate] = deque(maxlen=max_candidates)
        self._viewer_memory: dict[str, _ViewerMemory] = {}
        self._last_theme_keys: list[str] = []

    def status(self) -> dict[str, Any]:
        self._prune()
        context = self._build_context(list(self._recent))
        status = {
            "recent_danmaku_candidates": len(self._recent),
            "viewer_memory_count": len(self._viewer_memory),
            "last_theme_keys": [
                key
                for key in (public_room_theme_key(item) for item in self._last_theme_keys)
                if key
            ],
        }
        status.update(build_room_pulse(context).to_status())
        return status

    def reset(self) -> None:
        """Discard short-lived room context at a live-session boundary."""

        self._recent.clear()
        self._viewer_memory.clear()
        self._last_theme_keys = []

    def remember_live_event(self, event: Any, *, score: float) -> None:
        candidate = self._candidate_from_live_event(event, score=score)
        if candidate is None:
            return
        self._remember_candidate(candidate)

    def remember_danmaku(
        self,
        *,
        uid: str,
        nickname: str,
        text: str,
        score: float,
        ts: float,
    ) -> None:
        """Remember fields already sanitized by ``live_events.submit``."""

        if not text:
            return
        observed_at = self._safe_timestamp(ts)
        self._remember_candidate(
            DanmakuCandidate(
                uid=uid,
                nickname=nickname,
                text=text[:120],
                score=score,
                ts=observed_at,
            )
        )

    def _remember_candidate(self, candidate: DanmakuCandidate) -> None:
        if candidate.ts < self._safe_now() - self._window_seconds:
            self._prune()
            return
        self._recent.append(candidate)
        if not self._is_low_quality(candidate.text):
            self._remember_viewer(candidate)
        self._prune()

    def prompt_block_for_event(self, event: Any) -> str:
        return self.prompt_projection_for_event(event).text

    def prompt_projection_for_event(self, event: Any) -> RoomPulsePrompt:
        self._prune()
        selected = (
            None
            if event_type(event) in {"gift", "super_chat", "guard"}
            else self._candidate_from_viewer_event(event)
        )
        candidates = list(self._recent)
        if selected is not None and not self._contains(candidates, selected):
            candidates.append(selected)
        context = self._build_context(candidates, selected=selected)
        return render_room_pulse_prompt(context)

    def is_low_reply_value(self, text: str) -> bool:
        return self._is_low_quality(text)

    def _build_context(
        self,
        events: list[Any],
        *,
        selected: Any | None = None,
    ) -> dict[str, Any]:
        candidates = [item for item in (self._coerce_candidate(event) for event in events) if item is not None]
        selected_candidate = self._coerce_candidate(selected) if selected is not None else None
        now = self._safe_now()
        low_quality = 0
        low_quality_signals: Counter[str] = Counter()
        low_quality_viewers: dict[str, set[str]] = {}
        short_content_signals: Counter[str] = Counter()
        short_content_viewers: dict[str, set[str]] = {}
        short_content_examples: dict[str, str] = {}
        themes: dict[str, _Theme] = {}
        unique_viewers: set[str] = set()
        recent_activity_count = 0

        for candidate in candidates:
            support_uid = self._support_uid(candidate.uid)
            if support_uid:
                unique_viewers.add(support_uid)
            age = now - candidate.ts
            if math.isfinite(age) and 0.0 <= age <= ROOM_PULSE_ACTIVITY_WINDOW_SECONDS:
                recent_activity_count += 1
            if self._is_low_quality(candidate.text):
                low_quality += 1
                signal = self._dense_text(candidate.text)
                low_quality_signals[signal] += 1
                if support_uid:
                    low_quality_viewers.setdefault(signal, set()).add(support_uid)
                continue
            key, title, reply_tip, technique = self._classify(candidate.text)
            theme = themes.get(key)
            if theme is None:
                theme = _Theme(key=key, title=title, reply_tip=reply_tip, technique=technique)
                themes[key] = theme
            theme.count += 1
            theme.score += self._candidate_score(candidate, key)
            if support_uid:
                theme.viewer_uids.add(support_uid)
            short_signal = self._short_content_signal(candidate.text)
            if short_signal:
                short_content_signals[short_signal] += 1
                short_content_examples.setdefault(
                    short_signal, self._compact_text(candidate.text, 36)
                )
                if support_uid:
                    short_content_viewers.setdefault(short_signal, set()).add(support_uid)
            if len(theme.examples) < 3:
                theme.examples.append(
                    {
                        "uid": candidate.uid,
                        "nickname": candidate.nickname,
                        "text": self._compact_text(candidate.text, 36),
                    }
                )

        ordered = sorted(themes.values(), key=lambda item: (-item.score, -item.count, item.title))[:3]
        self._last_theme_keys = [theme.key for theme in ordered]
        question_theme = themes.get("question_help")
        question_support_count = len(question_theme.viewer_uids) if question_theme else 0
        reaction_support_count = len(
            set().union(*low_quality_viewers.values()) if low_quality_viewers else set()
        )
        (
            repeated_signal_kind,
            repeated_signal_support,
            repeated_signal_text,
            repeated_signal_theme_key,
        ) = self._best_repeated_signal(
            low_quality_signals=low_quality_signals,
            low_quality_viewers=low_quality_viewers,
            short_content_signals=short_content_signals,
            short_content_viewers=short_content_viewers,
            short_content_examples=short_content_examples,
        )
        dominant_theme = ordered[0] if ordered else None
        selected_theme = ""
        if selected_candidate is not None:
            selected_key, _title, _reply_tip, _technique = self._classify(selected_candidate.text)
            if any(theme.key == selected_key for theme in ordered):
                selected_theme = selected_key

        return {
            "version": 1,
            "total_candidates": len(candidates),
            "unique_viewer_count": len(unique_viewers),
            "recent_activity_count": recent_activity_count,
            "low_quality_count": low_quality,
            "low_quality_signals": low_quality_signals.most_common(3),
            "question_support_count": question_support_count,
            "reaction_support_count": reaction_support_count,
            "dominant_theme_key": dominant_theme.key if dominant_theme else "",
            "dominant_theme_support": (
                len(dominant_theme.viewer_uids) if dominant_theme else 0
            ),
            "repeated_signal_kind": repeated_signal_kind,
            "repeated_signal_support": repeated_signal_support,
            "repeated_signal_text": repeated_signal_text,
            "repeated_signal_theme_key": repeated_signal_theme_key,
            "selected_uid": selected_candidate.uid if selected_candidate is not None else "",
            "selected_text": selected_candidate.text if selected_candidate is not None else "",
            "selected_theme": selected_theme,
            "themes": [
                {
                    "key": theme.key,
                    "title": theme.title,
                    "count": theme.count,
                    "support_count": len(theme.viewer_uids),
                    "reply_tip": theme.reply_tip,
                    "technique": theme.technique,
                    "examples": theme.examples,
                }
                for theme in ordered
            ],
        }

    def _remember_viewer(self, candidate: DanmakuCandidate) -> None:
        memory = self._viewer_memory.get(candidate.uid)
        if memory is None:
            memory = _ViewerMemory(uid=candidate.uid)
            self._viewer_memory[candidate.uid] = memory
        memory.nickname = candidate.nickname or memory.nickname
        memory.message_count += 1
        memory.last_seen_at = candidate.ts
        preference = self._infer_viewer_preferences(candidate.text)
        summary = str(preference.get("summary") or "").strip()
        if summary:
            memory.summaries[summary] += 1
        style = str(preference.get("interaction_style") or "").strip()
        if style:
            memory.style = style
        response = str(preference.get("response_preference") or "").strip()
        if response:
            memory.response_preference = response

    def _prune(self) -> None:
        cutoff = self._safe_now() - self._window_seconds
        while self._recent and self._recent[0].ts < cutoff:
            self._recent.popleft()

    @staticmethod
    def _contains(candidates: list[DanmakuCandidate], selected: DanmakuCandidate) -> bool:
        return any(item.uid == selected.uid and item.text == selected.text for item in candidates)

    def _candidate_from_live_event(self, event: Any, *, score: float) -> DanmakuCandidate | None:
        text = event_prompt_text(event)
        if not text:
            return None
        return DanmakuCandidate(
            uid=event_uid(event),
            nickname=event_nickname(event),
            text=text,
            score=score,
            ts=self._safe_timestamp(getattr(event, "ts", 0.0)),
        )

    def _candidate_from_viewer_event(self, event: Any) -> DanmakuCandidate | None:
        text = public_text(getattr(event, "danmaku_text", ""))
        if not text:
            raw = getattr(event, "raw", None)
            if isinstance(raw, dict):
                text = public_text(raw.get("danmaku_text") or raw.get("text") or "")
        if not text:
            return None
        return DanmakuCandidate(
            uid=event_uid(event),
            nickname=event_nickname(event),
            text=text,
            score=1.0,
            ts=self._safe_now(),
        )

    def _coerce_candidate(self, event: Any) -> DanmakuCandidate | None:
        if isinstance(event, DanmakuCandidate):
            return event
        if event is None:
            return None
        if isinstance(event, dict):
            text = public_text(event.get("danmaku_text") or event.get("text") or "")
            if not text:
                return None
            return DanmakuCandidate(
                uid=event_uid(event),
                nickname=public_text(event.get("nickname") or "", max_length=64),
                text=text,
                score=float(event.get("score") or 1.0),
                ts=self._safe_timestamp(event.get("ts")),
            )
        return self._candidate_from_live_event(event, score=1.0)

    def _safe_now(self) -> float:
        try:
            value = float(self._now())
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    def _safe_timestamp(self, value: Any) -> float:
        now = self._safe_now()
        try:
            timestamp = float(value)
        except (TypeError, ValueError, OverflowError):
            return now
        if not math.isfinite(timestamp) or timestamp <= 0.0:
            return now
        if timestamp > now:
            return now
        return timestamp

    @staticmethod
    def _candidate_score(candidate: DanmakuCandidate, key: str) -> float:
        score = 1.0 + min(float(candidate.score) / 1000.0, 4.0)
        if key in {"question_help", "negative_comfort", "recommend_tutorial"}:
            score += 3.0
        if len(candidate.text) >= 8:
            score += 1.0
        if len(candidate.text) >= 18:
            score += 1.0
        return score

    @staticmethod
    def _support_uid(uid: str) -> str:
        value = str(uid or "").strip()
        return "" if value in {"", "0"} else value

    @staticmethod
    def _short_content_signal(text: str) -> str:
        dense = RoomTopicContext._dense_text(text)
        if 2 <= len(dense) <= 24:
            return dense
        return ""

    @staticmethod
    def _best_repeated_signal(
        *,
        low_quality_signals: Counter[str],
        low_quality_viewers: dict[str, set[str]],
        short_content_signals: Counter[str],
        short_content_viewers: dict[str, set[str]],
        short_content_examples: dict[str, str],
    ) -> tuple[str, int, str, str]:
        options: list[tuple[int, int, int, str, str, str]] = []
        for kind, counts, viewers, content_priority in (
            ("reaction", low_quality_signals, low_quality_viewers, 0),
            ("content", short_content_signals, short_content_viewers, 1),
        ):
            for signal, message_count in counts.items():
                support = len(viewers.get(signal, set()))
                if signal and support >= 2:
                    example = short_content_examples.get(signal, "") if kind == "content" else ""
                    theme_key = (
                        RoomTopicContext._classify(example)[0] if example else ""
                    )
                    options.append(
                        (
                            support,
                            int(message_count),
                            content_priority,
                            kind,
                            example,
                            theme_key,
                        )
                    )
        if not options:
            return "", 0, "", ""
        (
            support,
            _message_count,
            _content_priority,
            kind,
            example,
            theme_key,
        ) = max(options)
        return kind, support, example, theme_key

    @staticmethod
    def _is_low_quality(text: str) -> bool:
        dense = RoomTopicContext._dense_text(text)
        if not dense:
            return True
        if dense in _LOW_QUALITY_DANMAKU:
            return True
        if len(dense) <= 1:
            return True
        if len(set(dense)) == 1 and len(dense) <= 6:
            return True
        return False

    @staticmethod
    def _dense_text(text: str) -> str:
        return "".join(ch for ch in str(text or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    @staticmethod
    def _classify(text: str) -> tuple[str, str, str, str]:
        lowered = str(text or "").casefold()
        for key, keywords, title, reply_tip, technique in _SCENE_RULES:
            if any(keyword in lowered or keyword in text for keyword in keywords):
                return key, title, reply_tip, technique
        keywords = RoomTopicContext._keywords(text)
        if keywords:
            title = " / ".join(keywords[:2])
            return "topic:" + "|".join(keywords[:2]), title, "Reply to the shared point, then add one topic expansion.", "Pick one representative danmaku; do not read the whole batch."
        return "small_chat", "small talk", "Reply to the shared mood in one sentence.", "Keep it short and do not open a new segment."

    @staticmethod
    def _keywords(text: str) -> list[str]:
        chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", str(text or ""))
        stop = {"这个", "那个", "就是", "感觉", "真的", "可以", "不是", "什么", "怎么", "今天"}
        counts = Counter(chunk for chunk in chunks if chunk not in stop)
        return [word for word, _count in counts.most_common(3)]

    @staticmethod
    def _compact_text(text: str, limit: int) -> str:
        cleaned = " ".join(str(text or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)] + "..."

    @staticmethod
    def _infer_viewer_preferences(text: str) -> dict[str, Any]:
        return infer_viewer_preferences(text)

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        raw = str(text or "")
        dense = "".join(ch for ch in raw.casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if any(marker in raw for marker in ("?", "？")):
            return True
        question_markers = ("怎么", "为什么", "有没有", "是不是", "能不能", "可以吗")
        return any(marker in dense for marker in question_markers) or dense.endswith(("吗", "呢", "么"))
