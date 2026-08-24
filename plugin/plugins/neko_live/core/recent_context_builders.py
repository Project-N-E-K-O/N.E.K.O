"""Builders for recent live-context prompt memory."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .contracts_public import public_text
from .recent_context_lines import (
    active_engagement_context_line,
    idle_hosting_context_line,
    viewer_event_context_line,
)
from .recent_context_routes import route_from_result
from .recent_context_text import compact_context_text
from .recent_output_families import spent_output_families, spent_output_text
from .live_text_guards import looks_like_support_claim_text


_LOW_VALUE_DANMAKU = {
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
    "哈哈哈",
}


def build_recent_interaction_context(
    recent_results: Any, *, limit: int = 3
) -> list[str]:
    lines: list[str] = []
    for result in reversed(list(recent_results or [])):
        if not _is_context_result(result):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        source = str(event.get("source") or "unknown")
        route = route_from_result(result)
        if source == "idle_hosting":
            line = idle_hosting_context_line(route, event)
        elif source == "warmup_hosting":
            line = f"{route} / warmup_hosting: solo opening host beat"
        elif source == "active_engagement":
            line = active_engagement_context_line(route, event)
        else:
            line = viewer_event_context_line(route, source, event, result)
        line = _append_spent_output_context(line, result, output_limit=60)
        lines.append(line)
        if len(lines) >= max(1, int(limit)):
            break
    return lines


def build_recent_room_danmaku_context(
    recent_results: Any,
    current_event: Any | None = None,
    *,
    limit: int = 6,
) -> list[str]:
    """Build a tiny room-topic summary from recent live danmaku results.

    This is prompt-only guidance. It never changes routing, queueing, or
    persistence; its job is to help reply modules avoid one-by-one tunnel
    vision when the room has an obvious shared topic.
    """

    candidates: list[dict[str, str]] = []
    low_value_count = 0
    max_items = max(1, int(limit))
    for result in reversed(list(recent_results or [])):
        if not _is_context_result(result):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        text = public_text(event.get("danmaku_text"), max_len=80)
        if not text:
            continue
        if looks_like_support_claim_text(text):
            continue
        if _is_low_value_danmaku(text):
            low_value_count += 1
            continue
        candidates.append(
            {
                "uid": public_text(event.get("uid"), max_len=48),
                "nickname": public_text(event.get("nickname"), max_len=32),
                "text": compact_context_text(text, limit=42),
                "theme": _room_theme_key(text),
            }
        )
        if len(candidates) >= max_items:
            break

    current_text = _event_text(current_event)
    current_theme = (
        _room_theme_key(current_text)
        if current_text and not looks_like_support_claim_text(current_text)
        else ""
    )
    if len(candidates) < 2 and not low_value_count:
        return []

    theme_counts = Counter(item["theme"] for item in candidates if item.get("theme"))
    if current_theme:
        theme_counts[current_theme] += 1
    if not theme_counts:
        return []
    theme_key, theme_count = theme_counts.most_common(1)[0]
    examples = [item for item in candidates if item.get("theme") == theme_key][:3]
    if not examples:
        examples = candidates[:3]

    lines = [
        f"room_theme={_room_theme_title(theme_key)} ({theme_count} signals)",
        "room_rule=answer the current viewer first; if it matches the room theme, bridge the theme instead of replying one-by-one",
        "room_rule=filter low-value repeats and do not re-ask the same choice or topic prompt",
    ]
    if low_value_count:
        lines.append(f"filtered_low_value_danmaku={low_value_count}")
    rendered_examples = _render_room_examples(examples)
    if rendered_examples:
        lines.append(f"examples={rendered_examples}")
    current_title = _room_theme_title(current_theme) if current_theme else ""
    if current_title:
        lines.append(f"current_danmaku_theme={current_title}")
    return lines


def build_viewer_session_context(
    recent_results: Any, uid: str, *, limit: int = 2
) -> list[str]:
    target_uid = str(uid or "").strip()
    if not target_uid:
        return []
    lines: list[str] = []
    for result in reversed(list(recent_results or [])):
        if not _is_context_result(result):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("uid") or "").strip() != target_uid:
            continue
        text = str(event.get("danmaku_text") or "").strip()
        route = route_from_result(result)
        output = spent_output_text(result)
        if not text and not output:
            continue
        line = f"{route}: {compact_context_text(text, limit=60)}" if text else route
        line = _append_spent_output_context(line, result, output_limit=50)
        lines.append(line)
        if len(lines) >= max(1, int(limit)):
            break
    return lines


def _event_text(event: Any | None) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return public_text(event.get("danmaku_text") or event.get("text"), max_len=80)
    return public_text(getattr(event, "danmaku_text", ""), max_len=80)


def _is_low_value_danmaku(text: str) -> bool:
    dense = _dense_text(text)
    if not dense:
        return True
    if dense in _LOW_VALUE_DANMAKU:
        return True
    if len(dense) <= 1:
        return True
    if len(set(dense)) == 1 and len(dense) <= 6:
        return True
    return False


def _room_theme_key(text: str) -> str:
    lowered = str(text or "").casefold()
    dense = _dense_text(lowered)
    if any(marker in lowered or marker in dense for marker in ("晚好", "晚上好", "你好", "hi", "hello")):
        return "greeting"
    if any(marker in lowered or marker in dense for marker in ("笑话", "讲讲", "说说", "解释", "展开", "来一个", "joke", "explain")):
        return "content_request"
    if any(marker in lowered or marker in dense for marker in ("甜食", "热饮", "夜里", "选", "二选一", "告示", "告诉")):
        return "choice_prompt"
    if any(marker in lowered or marker in dense for marker in ("怎么", "为什么", "有没有", "配置", "教程", "推荐", "?", "？")):
        return "question_help"
    if any(marker in lowered or marker in dense for marker in ("贴贴", "抱抱", "可爱", "喵", "猫猫")):
        return "affection_play"
    if any(marker in lowered or marker in dense for marker in ("战雷", "游戏", "直播", "挑战")):
        return "game_stream"
    keywords = _keywords(text)
    return "topic:" + "|".join(keywords[:2]) if keywords else "small_talk"


def _room_theme_title(key: str) -> str:
    if key.startswith("topic:"):
        title = key.removeprefix("topic:").replace("|", " / ").strip()
        return title or "shared topic"
    return {
        "greeting": "greetings",
        "content_request": "content requests",
        "choice_prompt": "choice / preference prompt",
        "question_help": "questions / help",
        "affection_play": "affection / playful chat",
        "game_stream": "game / stream moment",
        "small_talk": "small talk",
    }.get(key, key or "room topic")


def _render_room_examples(examples: list[dict[str, str]]) -> str:
    rendered: list[str] = []
    for item in examples:
        who = item.get("nickname") or item.get("uid") or "viewer"
        text = item.get("text") or ""
        if text:
            rendered.append(f"{who}: {text}")
    return " | ".join(rendered)


def _keywords(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in str(text or ""):
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current += char
            continue
        if len(current) >= 2:
            chunks.append(current)
        current = ""
    if len(current) >= 2:
        chunks.append(current)
    stop = {"这个", "那个", "就是", "感觉", "真的", "可以", "不是", "什么", "今天", "大家", "快说"}
    counts = Counter(chunk for chunk in chunks if chunk not in stop)
    return [word for word, _count in counts.most_common(3)]


def _dense_text(text: str) -> str:
    return "".join(ch for ch in str(text or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _is_context_result(result: Any) -> bool:
    return isinstance(result, dict) and str(result.get("status") or "") in {
        "pushed",
        "dry_run",
    }


def _append_spent_output_context(
    line: str, result: dict[str, Any], *, output_limit: int
) -> str:
    output = spent_output_text(result)
    if not output:
        return line
    families = spent_output_families(output)
    if families:
        line += f" / spent_output_family={','.join(families)}"
    return f"{line} / NEKO already said: {compact_context_text(output, limit=output_limit)}"
