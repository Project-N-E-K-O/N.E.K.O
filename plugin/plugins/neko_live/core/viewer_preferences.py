"""Safe viewer preference inference for NEKO Live.

These helpers intentionally return labels, counts, and short rule-like
summaries instead of raw danmaku text. The output is safe to persist in viewer
profiles and safe to reuse as private prompt guidance.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any

from .contracts_public import public_text

_PREFERENCE_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("games", ("游戏", "boss", "副本", "抽卡", "角色", "装备", "攻略"), "likes games/strategy"),
    ("music", ("唱歌", "歌", "bgm", "音乐", "点歌", "好听"), "likes music/singing"),
    ("tech_ai", ("ai", "模型", "代码", "插件", "电脑", "配置", "bug"), "likes tech/AI"),
    ("anime_role", ("动画", "番", "二次元", "声优", "cos", "live2d", "猫娘"), "likes anime/character topics"),
    ("meme", ("梗", "笑死", "草", "哈哈", "hhh", "233", "节目效果"), "likes memes"),
    ("comfort", ("累", "难过", "睡不着", "压力", "抱抱", "安慰"), "may want comfort"),
    ("support", ("可爱", "喜欢", "支持", "加油", "贴贴", "awsl"), "often supportive"),
    ("questions", ("怎么", "为什么", "请问", "求教", "如何", "?"), "often asks questions"),
)

_TAG_LABELS: dict[str, str] = {
    key: label
    for key, _keywords, label in _PREFERENCE_RULES
}
_TAG_LABELS.update(
    {
        "question": "often asks questions",
        "chat": "likes light chat",
    }
)

_FAVORITE_TOPIC_TAGS = {"games", "music", "tech_ai", "anime_role"}
_STABLE_MEMORY_MIN_COUNT = 2

_RUNNING_JOKE_LABELS: dict[str, str] = {
    "meme_callback": "likes meme callbacks",
    "game_strategy_bit": "likes game/strategy bits",
    "music_callback": "likes music callbacks",
    "character_roleplay_bit": "likes character/live2D bits",
    "soft_thanks_callback": "responds well to warm acknowledgement",
    "short_helper_mode": "prefers concise helper replies",
}


def safe_text(value: Any, *, max_len: int = 180) -> str:
    if not isinstance(value, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        else:
            return ""
    text = public_text(value, max_len=max_len)
    return "" if "[redacted]" in text else text


def safe_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except Exception:
        return default
    return max(number, 0)


def infer_viewer_preferences(text: str) -> dict[str, Any]:
    raw = str(text or "")
    lowered = raw.casefold()
    tags: list[str] = []
    labels: list[str] = []
    for key, keywords, label in _PREFERENCE_RULES:
        if any(_contains_keyword(raw, lowered, keyword) for keyword in keywords):
            tags.append(key)
            labels.append(label)

    style = ""
    response_preference = ""
    if looks_like_question(raw):
        style = "question"
        response_preference = "answer first, then add one light follow-up"
    elif "meme" in tags:
        style = "meme"
        response_preference = "catch the joke briefly without overexplaining"
    elif "support" in tags:
        style = "support"
        response_preference = "a named thanks or playful acknowledgement works well"
    elif len(raw.strip()) >= 12:
        style = "chat"
        response_preference = "extend the current topic lightly"

    if style and style not in tags:
        tags.append(style)
    summary = ", ".join(labels[:3])
    if not summary and style:
        summary = {
            "question": "often asks questions",
            "meme": "likes memes",
            "support": "often supportive",
            "chat": "likes light chat",
        }.get(style, "")

    favorite_topics = [tag for tag in tags if tag in _FAVORITE_TOPIC_TAGS]
    running_jokes = _running_jokes_for(tags, style)
    avoid_guidance = _avoid_guidance_for(tags, style)
    impression_summary = _impression_summary(summary, style, response_preference, avoid_guidance)

    return {
        "tags": [safe_text(tag, max_len=48) for tag in tags if safe_text(tag, max_len=48)][:6],
        "favorite_topics": [safe_text(tag, max_len=48) for tag in favorite_topics if safe_text(tag, max_len=48)][:6],
        "running_jokes": [safe_text(tag, max_len=48) for tag in running_jokes if safe_text(tag, max_len=48)][:6],
        "summary": safe_text(summary, max_len=160),
        "impression_summary": safe_text(impression_summary, max_len=180),
        "interaction_style": safe_text(style, max_len=48),
        "response_preference": safe_text(response_preference, max_len=180),
        "avoid_guidance": safe_text(avoid_guidance, max_len=180),
    }


def _contains_keyword(raw: str, lowered: str, keyword: str) -> bool:
    if keyword.isascii() and keyword.isalnum():
        return re.search(
            rf"(?<![a-z0-9]){re.escape(keyword.casefold())}(?![a-z0-9])",
            lowered,
        ) is not None
    return keyword in lowered or keyword in raw


def looks_like_question(text: str) -> bool:
    raw = str(text or "")
    dense = "".join(ch for ch in raw.casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if any(marker in raw for marker in ("?", "？")):
        return True
    question_markers = ("怎么", "为什么", "有没有", "是不是", "能不能", "可以吗", "好不好", "请问", "求教")
    return any(marker in dense for marker in question_markers) or dense.endswith(("吗", "呢", "么", "嘛"))


def safe_preference_counts(value: Any, *, max_items: int = 12) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, int] = {}
    for key, count in value.items():
        safe_key = safe_text(key, max_len=48)
        safe_count = safe_int(count)
        if safe_key and safe_count > 0:
            cleaned[safe_key] = safe_count
        if len(cleaned) >= max_items:
            break
    return cleaned


def merge_preference_counts(existing: Any, tags: list[str]) -> dict[str, int]:
    counts = safe_preference_counts(existing)
    for tag in tags:
        safe_tag = safe_text(tag, max_len=48)
        if safe_tag:
            counts[safe_tag] = safe_int(counts.get(safe_tag)) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12])


def viewer_profile_projection(profile: Any) -> dict[str, Any]:
    counts = safe_preference_counts(_get(profile, "preference_tags"))
    favorite_counts = safe_preference_counts(_get(profile, "favorite_topics"))
    joke_counts = safe_preference_counts(_get(profile, "running_jokes"))
    danmaku_count = safe_int(_get(profile, "danmaku_count"))
    roast_count = safe_int(_get(profile, "roast_count"))
    stored_response = safe_text(_get(profile, "response_preference"), max_len=180)
    latest_summary = safe_text(_get(profile, "last_interaction_summary"), max_len=160)
    stored_impression = safe_text(_get(profile, "impression_summary"), max_len=180)
    avoid_guidance = safe_text(_get(profile, "avoid_guidance"), max_len=180)
    last_interaction_at = safe_text(_get(profile, "last_interaction_at"), max_len=80)
    last_seen_at = safe_text(_get(profile, "last_seen_at"), max_len=80)
    freshness = _profile_freshness(last_interaction_at or last_seen_at)
    top_tags = [
        {"tag": key, "count": count, "label": _TAG_LABELS.get(key, key)}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]
    ]
    stable_favorite_counts = _stable_counts(favorite_counts)
    stable_joke_counts = _stable_counts(joke_counts)
    top_favorite_topics = [
        {"tag": key, "count": count, "label": _TAG_LABELS.get(key, key)}
        for key, count in sorted(stable_favorite_counts.items(), key=lambda item: (-item[1], item[0]))[:4]
    ]
    top_running_jokes = [
        {"tag": key, "count": count, "label": _RUNNING_JOKE_LABELS.get(key, key)}
        for key, count in sorted(stable_joke_counts.items(), key=lambda item: (-item[1], item[0]))[:4]
    ]
    stage = _viewer_stage(danmaku_count=danmaku_count, roast_count=roast_count)
    confidence = _profile_confidence(danmaku_count=danmaku_count, top_tags=top_tags, freshness=freshness)
    reply_guidance = stored_response if confidence in {"medium", "high"} else _reply_guidance_for_stage(stage)
    summary = stored_impression if confidence in {"medium", "high"} else latest_summary or _summary_from_top_tags(top_tags)
    return {
        "viewer_stage": stage,
        "profile_confidence": confidence,
        "profile_freshness": freshness,
        "top_preference_tags": top_tags,
        "top_favorite_topics": top_favorite_topics,
        "top_running_jokes": top_running_jokes,
        "reply_guidance": reply_guidance,
        "profile_summary": summary,
        "impression_summary": summary,
        "avoid_guidance": avoid_guidance,
        "memory_use_rule": _memory_use_rule(confidence=confidence, freshness=freshness),
    }


def viewer_preference_prompt_block(profile: Any) -> str:
    counts = safe_preference_counts(getattr(profile, "preference_tags", None))
    style = safe_text(getattr(profile, "interaction_style", ""), max_len=48)
    response = safe_text(getattr(profile, "response_preference", ""), max_len=180)
    summary = safe_text(getattr(profile, "last_interaction_summary", ""), max_len=160)
    impression = safe_text(getattr(profile, "impression_summary", ""), max_len=180)
    avoid_guidance = safe_text(getattr(profile, "avoid_guidance", ""), max_len=180)
    danmaku_count = safe_int(getattr(profile, "danmaku_count", 0))
    projection = viewer_profile_projection(profile)
    top_tags = [key for key, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]]
    top_topics = [str(item["tag"]) for item in projection["top_favorite_topics"]]
    top_jokes = [str(item["tag"]) for item in projection["top_running_jokes"]]
    if not any((top_tags, top_topics, top_jokes, style, response, summary, impression, avoid_guidance, danmaku_count)):
        return ""
    lines = ["Viewer impression memory (private guidance):"]
    if danmaku_count:
        lines.append(f"- observed_live_danmaku_count: {danmaku_count}")
    lines.append(f"- viewer_stage: {projection['viewer_stage']}")
    lines.append(f"- profile_confidence: {projection['profile_confidence']}")
    lines.append(f"- profile_freshness: {projection['profile_freshness']}")
    lines.append(f"- memory_use_rule: {projection['memory_use_rule']}")
    if top_tags:
        lines.append("- preference_tags: " + ", ".join(top_tags))
        rendered = [
            f"{item['tag']}({item['count']})"
            for item in projection["top_preference_tags"]
        ]
        lines.append("- top_preferences: " + ", ".join(rendered))
    if top_topics:
        lines.append("- favorite_topics: " + ", ".join(top_topics))
    if top_jokes:
        rendered_jokes = [
            f"{item['tag']}({item['count']})"
            for item in projection["top_running_jokes"]
        ]
        lines.append("- running_jokes_or_reply_cues: " + ", ".join(rendered_jokes))
    if style:
        lines.append(f"- interaction_style: {style}")
    if projection["reply_guidance"]:
        lines.append(f"- response_preference: {projection['reply_guidance']}")
    if projection["impression_summary"]:
        lines.append(f"- viewer_impression: {projection['impression_summary']}")
    elif summary:
        lines.append(f"- latest_safe_summary: {summary}")
    if avoid_guidance:
        lines.append(f"- avoid_guidance: {avoid_guidance}")
    lines.append("- priority_rule: the current danmaku text is mandatory; never let viewer impression, avatar, nickname, or old memory become the main reply topic.")
    lines.append("- avatar_memory_rule: for normal danmaku response, do not mention avatar or visual impressions unless the current danmaku explicitly asks about them.")
    lines.append("- evidence_rule: treat one-off topics or jokes as weak evidence; prefer current danmaku over old impressions.")
    lines.append("- privacy_rule: use these hints silently; do not announce stored viewer data or say you remember the profile.")
    return "\n".join(lines) + "\n\n"


def _running_jokes_for(tags: list[str], style: str) -> list[str]:
    cues: list[str] = []
    if "meme" in tags:
        cues.append("meme_callback")
    if "games" in tags:
        cues.append("game_strategy_bit")
    if "music" in tags:
        cues.append("music_callback")
    if "anime_role" in tags:
        cues.append("character_roleplay_bit")
    if "support" in tags:
        cues.append("soft_thanks_callback")
    if style == "question" or "tech_ai" in tags:
        cues.append("short_helper_mode")
    return cues


def _avoid_guidance_for(tags: list[str], style: str) -> str:
    if "comfort" in tags:
        return "keep it warm; avoid sharp teasing"
    if style == "question":
        return "answer before teasing; do not dodge the question"
    if "meme" in tags:
        return "do not overexplain the joke"
    if "support" in tags:
        return "do not turn support into a harsh roast"
    return ""


def _impression_summary(summary: str, style: str, response_preference: str, avoid_guidance: str) -> str:
    parts = [safe_text(summary, max_len=120)]
    if style and not summary:
        parts.append(
            {
                "question": "often asks questions",
                "meme": "likes memes",
                "support": "often supportive",
                "chat": "likes light chat",
            }.get(style, "")
        )
    if response_preference:
        parts.append(response_preference)
    if avoid_guidance:
        parts.append("avoid: " + avoid_guidance)
    return "; ".join(part for part in parts if part)[:180]


def _get(profile: Any, key: str) -> Any:
    if isinstance(profile, Mapping):
        return profile.get(key)
    return getattr(profile, key, None)


def _stable_counts(counts: dict[str, int], *, min_count: int = _STABLE_MEMORY_MIN_COUNT) -> dict[str, int]:
    return {key: count for key, count in counts.items() if count >= min_count}


def _viewer_stage(*, danmaku_count: int, roast_count: int) -> str:
    total = danmaku_count + roast_count
    if total >= 12:
        return "familiar_viewer"
    if total >= 5:
        return "regular_viewer"
    if total >= 2:
        return "returning_viewer"
    return "new_viewer"


def _profile_confidence(*, danmaku_count: int, top_tags: list[dict[str, Any]], freshness: str = "none") -> str:
    if not top_tags:
        return "none"
    if freshness in {"old", "none"}:
        return "low"
    if freshness == "stale":
        return "medium" if danmaku_count >= 10 and top_tags[0]["count"] >= 3 else "low"
    if danmaku_count >= 10 and top_tags[0]["count"] >= 3:
        return "high"
    if danmaku_count >= 4 or top_tags[0]["count"] >= 2:
        return "medium"
    return "low"


def _profile_freshness(timestamp: str) -> str:
    if not timestamp:
        return "none"
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return "none"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
    if age_days <= 7:
        return "fresh"
    if age_days <= 30:
        return "warm"
    if age_days <= 90:
        return "stale"
    return "old"


def _memory_use_rule(*, confidence: str, freshness: str) -> str:
    if confidence == "high" and freshness in {"fresh", "warm"}:
        return "stable: one subtle callback is allowed if it fits the current danmaku"
    if confidence == "medium" and freshness in {"fresh", "warm", "stale"}:
        return "cautious: use as background context, not as a script"
    if freshness == "old":
        return "old: only use if the current danmaku clearly invites it"
    return "weak: do not assume familiarity; answer the current danmaku first"


def _reply_guidance_for_stage(stage: str) -> str:
    if stage == "familiar_viewer":
        return "you may use one familiar callback, but keep it natural"
    if stage == "regular_viewer":
        return "acknowledge lightly, then answer the current danmaku"
    if stage == "returning_viewer":
        return "use a small continuity hint only if it fits the current danmaku"
    return "treat as a new viewer; do not pretend familiarity"


def _summary_from_top_tags(top_tags: list[dict[str, Any]]) -> str:
    labels = [
        safe_text(item.get("label"), max_len=80)
        for item in top_tags[:3]
        if safe_text(item.get("label"), max_len=80)
    ]
    return ", ".join(labels)
