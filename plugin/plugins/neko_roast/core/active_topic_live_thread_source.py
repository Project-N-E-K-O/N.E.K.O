"""Recent live-thread source for active engagement topics."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .live_text_guards import looks_like_support_claim_text


_MAX_RECENT_ITEMS = 10
_MIN_SHARED_MENTIONS = 2
_MIN_SIGNAL_CHARS = 2
_STOP_UNITS = {
    "neko",
    "live",
    "chat",
    "room",
    "stream",
    "today",
    "tonight",
    "viewer",
    "观众",
    "直播",
    "直播间",
    "弹幕",
    "猫猫",
    "今天",
    "今晚",
    "这个",
    "那个",
    "什么",
    "一下",
    "真的",
    "感觉",
}


def live_thread_topic_candidates(selector: Any) -> list[dict[str, Any]]:
    items = _recent_thread_items(selector)
    if len(items) < _MIN_SHARED_MENTIONS:
        return []
    thread = _best_thread(items)
    if thread is None:
        return []
    term, examples, speaker_count = thread
    representative = examples[0]
    title = _thread_title(term, representative, len(examples), speaker_count)
    return [
        {
            "source": "live_thread",
            "privacy_classification": "viewer_derived",
            "key": f"thread:{term}:{_compact_key(representative)}",
            "title": title,
            "preferred_shape": "light_stance",
            "fun_axis": "viewer_callback",
            "live_column": "NEKO thread pickup",
            "reply_affordance": "viewer can add one small stance or example",
            "interest": title,
            "keywords": [term],
            "relevance": min(100, 65 + len(examples) * 8 + speaker_count * 4),
            "risk": 20,
            "evidence": examples[:3],
            "hint": (
                "Carry forward this recent live-room thread. Start from the shared "
                "viewer focus, add one small NEKO stance, and leave one easy reply handle."
            ),
        }
    ]


def _recent_thread_items(selector: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for result in reversed(selector.recent_results):
        if not isinstance(result, dict):
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        if str(event.get("source") or "") != "live_danmaku":
            continue
        if str(result.get("status") or "") not in {"pushed", "dry_run"}:
            continue
        if selector._runtime._route_from_result(result) == "avatar_roast":
            continue
        age = selector._runtime._iso_age_sec(result.get("created_at"))
        if (
            age is not None
            and age > selector._ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS
        ):
            continue
        text = str(event.get("danmaku_text") or "").strip()
        if not text or selector.is_viewer_to_viewer_mention_text(text):
            continue
        if looks_like_support_claim_text(text):
            continue
        if not selector.is_meaningful_topic_text(text):
            continue
        compact = selector._runtime._compact_context_text(text, limit=50)
        units = _topic_units(compact)
        if not units:
            continue
        items.append(
            {
                "uid": str(event.get("uid") or "").strip(),
                "text": compact,
                "units": "\n".join(sorted(units)),
            }
        )
        if len(items) >= _MAX_RECENT_ITEMS:
            break
    return items


def _best_thread(items: list[dict[str, str]]) -> tuple[str, list[str], int] | None:
    counts: Counter[str] = Counter()
    speakers: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[str]] = defaultdict(list)
    for item in items:
        uid = item.get("uid") or "<anonymous>"
        text = item.get("text") or ""
        for unit in (item.get("units") or "").splitlines():
            counts[unit] += 1
            speakers[unit].add(uid)
            if text and text not in examples[unit]:
                examples[unit].append(text)
    if not counts:
        return None
    ranked = sorted(
        counts,
        key=lambda unit: (
            counts[unit],
            len(speakers[unit]),
            len(unit),
        ),
        reverse=True,
    )
    for unit in ranked:
        if counts[unit] < _MIN_SHARED_MENTIONS:
            continue
        if speakers[unit] == {"<anonymous>"}:
            continue
        return unit, examples[unit], len(speakers[unit])
    return None


def _topic_units(text: str) -> set[str]:
    compact = " ".join(str(text or "").strip().split())
    dense = "".join(
        ch.casefold() for ch in compact if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )
    units: set[str] = set()
    latin = []
    for ch in dense:
        if ch.isascii() and ch.isalnum():
            latin.append(ch)
            continue
        if latin:
            _add_latin_unit(units, "".join(latin))
            latin = []
        if "\u4e00" <= ch <= "\u9fff":
            units.add(ch)
    if latin:
        _add_latin_unit(units, "".join(latin))

    cjk_chars = [ch for ch in dense if "\u4e00" <= ch <= "\u9fff"]
    for i in range(len(cjk_chars) - 1):
        units.add("".join(cjk_chars[i : i + 2]))
    for i in range(len(cjk_chars) - 2):
        units.add("".join(cjk_chars[i : i + 3]))
    return {
        unit
        for unit in units
        if len(unit) >= _MIN_SIGNAL_CHARS and unit not in _STOP_UNITS
    }


def _add_latin_unit(units: set[str], value: str) -> None:
    if len(value) >= 3:
        units.add(value)


def _thread_title(
    term: str, representative: str, mention_count: int, speaker_count: int
) -> str:
    if speaker_count >= 2:
        return f"多人在聊「{term}」：{representative}"
    return f"弹幕反复提到「{term}」：{representative}"


def _compact_key(value: str) -> str:
    return "".join(
        ch
        for ch in str(value or "").casefold()
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )[:24]


__all__ = ["live_thread_topic_candidates"]
