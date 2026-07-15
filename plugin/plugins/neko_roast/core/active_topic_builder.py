"""Topic assembly and rotation bookkeeping for active engagement."""

from __future__ import annotations

from typing import Any


def _privacy_classification(chosen: dict[str, Any]) -> str:
    source = str(chosen.get("source") or "").strip()
    if source in {"recent_danmaku", "live_thread"}:
        return "viewer_derived"
    if source not in {"fallback", "bili_trending"}:
        return "private"
    explicit = chosen.get("privacy_classification")
    if explicit in {"public", "viewer_derived", "private"}:
        return str(explicit)
    return "public"


def build_topic(
    selector: Any,
    chosen: dict[str, Any],
    fallback: dict[str, Any],
    shape: str,
) -> dict[str, Any]:
    rotation_shape = selector.guarded_shape(shape)
    rotation_guard_reason = str(selector._active_engagement_shape_guard_reason or "").strip()
    preferred_shape = str(chosen.get("preferred_shape") or rotation_shape).strip() or rotation_shape
    shape = selector.guarded_shape(preferred_shape)
    if rotation_guard_reason and not selector._active_engagement_shape_guard_reason:
        selector._active_engagement_shape_guard_reason = rotation_guard_reason
    key = str(chosen.get("key") or chosen.get("title") or fallback["key"]).strip()
    title = str(chosen.get("title") or fallback["title"]).strip()
    intent = selector.intent_text(shape)
    hint = str(chosen.get("hint") or fallback["hint"]).strip()
    if shape != preferred_shape:
        hint = selector.hint_text(shape)
    topic = {
        "source": str(chosen.get("source") or "fallback"),
        "privacy_classification": _privacy_classification(chosen),
        "shape": shape,
        "key": key,
        "title": title,
        "family": selector.host_material_family(chosen),
        "fun_axis": str(chosen.get("fun_axis") or "").strip()
        or selector.fun_axis_text(shape),
        "hook": selector.hook_text(shape, title),
        "pattern": selector.pattern_text(shape),
        "intent": intent,
        "live_column": str(chosen.get("live_column") or "").strip(),
        "topic_pack": selector.topic_pack(chosen),
        "reply_affordance": str(chosen.get("reply_affordance") or "").strip()
        or selector.reply_affordance_text(shape),
        "hint": hint,
    }
    remember_topic(selector, topic, key, title, intent, shape)
    return topic


def remember_topic(
    selector: Any,
    topic: dict[str, Any],
    key: str,
    title: str,
    intent: str,
    shape: str,
) -> None:
    selector._active_engagement_recent_topic_keys.append(key)
    if title:
        selector._active_engagement_recent_topic_titles.append(title)
    selector._active_engagement_recent_topic_sources.append(str(topic["source"]))
    if topic["fun_axis"]:
        selector._active_engagement_recent_fun_axes.append(str(topic["fun_axis"]))
    if topic["family"]:
        selector._recent_host_material_families.append(str(topic["family"]))
    selector._active_engagement_recent_shapes.append(shape)
    selector._active_engagement_recent_intents.append(intent)
    if topic["reply_affordance"]:
        selector._active_engagement_recent_reply_affordances.append(
            str(topic["reply_affordance"])
        )
    skip_reason = str(
        selector._active_engagement_recent_topic_skip_reason or ""
    ).strip()
    if skip_reason:
        topic["recent_topic_skip_reason"] = skip_reason
    shape_guard_reason = str(
        selector._active_engagement_shape_guard_reason or ""
    ).strip()
    if shape_guard_reason:
        topic["shape_guard_reason"] = shape_guard_reason
