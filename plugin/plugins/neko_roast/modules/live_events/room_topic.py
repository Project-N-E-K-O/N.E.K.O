"""Room-topic prompt context owned by the live_events module."""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ...core.live_reply_tactics import tactic_for_theme
from ...core.viewer_preferences import infer_viewer_preferences
from .provider_event import event_nickname, event_prompt_text, event_uid, public_text


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
        return {
            "recent_danmaku_candidates": len(self._recent),
            "viewer_memory_count": len(self._viewer_memory),
            "last_theme_keys": list(self._last_theme_keys),
        }

    def remember_live_event(self, event: Any, *, score: float) -> None:
        candidate = self._candidate_from_live_event(event, score=score)
        if candidate is None:
            return
        self._recent.append(candidate)
        if not self._is_low_quality(candidate.text):
            self._remember_viewer(candidate)
        self._prune()

    def prompt_block_for_event(self, event: Any) -> str:
        self._prune()
        selected = self._candidate_from_viewer_event(event)
        candidates = list(self._recent)
        if selected is not None and not self._contains(candidates, selected):
            candidates.append(selected)
        return self._render_prompt_block(self._build_context(candidates, selected=selected))

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
        low_quality = 0
        low_quality_signals: Counter[str] = Counter()
        themes: dict[str, _Theme] = {}
        viewer_hints: list[str] = []

        for candidate in candidates:
            preference = self._infer_viewer_preferences(candidate.text)
            if self._is_low_quality(candidate.text):
                low_quality += 1
                low_quality_signals[self._dense_text(candidate.text)] += 1
                continue
            key, title, reply_tip, technique = self._classify(candidate.text)
            theme = themes.get(key)
            if theme is None:
                theme = _Theme(key=key, title=title, reply_tip=reply_tip, technique=technique)
                themes[key] = theme
            theme.count += 1
            theme.score += self._candidate_score(candidate, key)
            if len(theme.examples) < 3:
                theme.examples.append(
                    {
                        "uid": candidate.uid,
                        "nickname": candidate.nickname,
                        "text": self._compact_text(candidate.text, 36),
                    }
                )
            hint = self._viewer_hint(candidate.uid, candidate.nickname, preference)
            if hint and hint not in viewer_hints and len(viewer_hints) < 3:
                viewer_hints.append(hint)

        ordered = sorted(themes.values(), key=lambda item: (-item.score, -item.count, item.title))[:3]
        self._last_theme_keys = [theme.key for theme in ordered]
        selected_theme = ""
        if selected_candidate is not None:
            selected_key, _title, _reply_tip, _technique = self._classify(selected_candidate.text)
            if any(theme.key == selected_key for theme in ordered):
                selected_theme = selected_key

        return {
            "version": 1,
            "total_candidates": len(candidates),
            "low_quality_count": low_quality,
            "low_quality_signals": low_quality_signals.most_common(3),
            "selected_uid": selected_candidate.uid if selected_candidate is not None else "",
            "selected_theme": selected_theme,
            "themes": [
                {
                    "key": theme.key,
                    "title": theme.title,
                    "count": theme.count,
                    "reply_tip": theme.reply_tip,
                    "technique": theme.technique,
                    "examples": theme.examples,
                }
                for theme in ordered
            ],
            "viewer_hints": viewer_hints,
        }

    def _render_prompt_block(self, context: dict[str, Any] | None) -> str:
        if not isinstance(context, dict):
            return ""
        total = int(context.get("total_candidates") or 0)
        themes = context.get("themes")
        low = int(context.get("low_quality_count") or 0)
        if total <= 1 and not themes and not low:
            return ""

        lines = ["Live event room-topic context:"]
        lines.append("- guidance: filter low-value messages, merge related danmaku, and reply to the room theme instead of one-by-one.")
        lines.append("- output_policy: the selected danmaku is the trigger; when a theme is present, answer the room theme in one line.")
        lines.append(f"- observed_candidates: {total}")
        if low:
            lines.append(f"- filtered_low_quality: {low}")
        if low >= 10:
            lines.append("- burst_mode: true")
            lines.append("- burst_reply_rule: answer the selected representative message, not the low-value numeric burst.")
        signals = context.get("low_quality_signals")
        if isinstance(signals, list):
            for value, count in signals[:3]:
                if value and count:
                    lines.append(f"- dominant_low_value_signal: {value} ({int(count)} messages)")
        if isinstance(themes, list):
            for theme in themes[:3]:
                if not isinstance(theme, dict):
                    continue
                title = str(theme.get("title") or "").strip()
                count = int(theme.get("count") or 0)
                if title:
                    lines.append(f"- theme: {title} ({count} messages)")
                rendered = self._render_examples(theme.get("examples"))
                if rendered:
                    lines.append(f"  examples: {rendered}")
                reply_tip = str(theme.get("reply_tip") or "").strip()
                if reply_tip:
                    lines.append(f"  reply_tip: {reply_tip}")
                technique = str(theme.get("technique") or "").strip()
                if technique:
                    lines.append(f"  technique: {technique}")
                tactic_key, tactic = tactic_for_theme(str(theme.get("key") or ""))
                lines.append(f"  reply_tactic: {tactic_key} - {tactic}")
        hints = [str(item).strip() for item in context.get("viewer_hints", []) if str(item).strip()] if isinstance(context.get("viewer_hints"), list) else []
        memory = self._viewer_memory_lines(str(context.get("selected_uid") or ""))
        combined_hints = (hints + memory)[:4]
        if combined_hints:
            lines.append("- viewer_hints: " + " ; ".join(combined_hints))
            lines.append("- personalization_rule: use viewer hints as private guidance; do not announce stored data.")
        return "\n".join(lines) + "\n\n"

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

    def _viewer_memory_lines(self, uid: str) -> list[str]:
        memory = self._viewer_memory.get(str(uid or ""))
        if memory is None:
            return []
        label = memory.nickname or memory.uid
        parts = [f"@{label}: seen_messages={memory.message_count}"]
        top_summaries = [summary for summary, _count in memory.summaries.most_common(2)]
        if top_summaries:
            parts.append("likes=" + ", ".join(top_summaries))
        if memory.style:
            parts.append("style=" + memory.style)
        if memory.response_preference:
            parts.append("reply_preference=" + memory.response_preference)
        return ["; ".join(parts)]

    def _prune(self) -> None:
        cutoff = self._now() - self._window_seconds
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
            ts=float(getattr(event, "ts", 0.0) or self._now()),
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
            ts=self._now(),
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
                ts=float(event.get("ts") or self._now()),
            )
        return self._candidate_from_live_event(event, score=1.0)

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
    def _render_examples(examples: Any) -> str:
        rendered: list[str] = []
        if not isinstance(examples, list):
            return ""
        for item in examples[:3]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("nickname") or item.get("uid") or "viewer").strip()
            text = str(item.get("text") or "").strip()
            if text:
                rendered.append(f"{name}: {text}")
        return " | ".join(rendered)

    @staticmethod
    def _viewer_hint(uid: str, nickname: str, preference: dict[str, Any]) -> str:
        summary = str(preference.get("summary") or "").strip()
        response = str(preference.get("response_preference") or "").strip()
        if not summary and not response:
            return ""
        label = nickname or uid or "viewer"
        parts = [part for part in (summary, response) if part]
        return f"@{label}: " + "; ".join(parts)

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
