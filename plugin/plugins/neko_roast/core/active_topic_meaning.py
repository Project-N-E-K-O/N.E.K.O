"""Meaningfulness rules for active-engagement topic candidates."""

from __future__ import annotations

from . import active_topic_filters, active_topic_mentions, active_topic_safety


GENERIC_HOST_PHRASES = (
    "what should we talk about",
    "what are we doing",
    "what should we do",
    "everyone interact",
    "send danmaku",
    "come chat",
    "tell me what you want",
    "get the chat moving",
    "keep the chat moving",
    "keep the chat alive",
    "keep the chat going",
    "any recommendations",
    "what do you recommend",
    "recommend me",
    "give me recommendations",
    "sponsored",
    "giveaway",
    "subscribe and win",
    "limited offer",
    "promo code",
    "death toll",
    "casualties",
    "accident",
    "disaster",
    "suicide",
    "murder",
    "scandal",
    "controversy",
    "harassment",
    "doxx",
    "why so quiet",
    "so quiet here",
    "suddenly quiet",
    "room is silent",
    "stream is quiet",
    "chat is quiet",
    "nobody is talking",
    "no one is talking",
    "dead chat",
    "\u5927\u5bb6\u4e92\u52a8",
    "\u53d1\u5f39\u5e55",
    "\u6765\u804a\u5929",
    "\u804a\u4ec0\u4e48",
    "\u505a\u4ec0\u4e48",
    "\u4eca\u665a\u505a\u4ec0\u4e48",
    "\u60f3\u542c\u4ec0\u4e48",
    "\u6765\u70b9\u5f39\u5e55",
    "\u8fd8\u5728\u5417",
    "\u6709\u4eba\u5417",
    "\u5728\u4e0d\u5728",
    "\u5192\u4e2a\u6ce1",
    "\u542d\u4e00\u58f0",
    "\u7ed9\u70b9\u53cd\u5e94",
    "\u63a5\u4e00\u53e5",
    "\u53d1\u4e2a\u8a00",
    "\u62631",
    "\u6263\u4e2a",
    "\u5f39\u5e55\u5237\u8d77\u6765",
    "\u60f3\u770b\u4ec0\u4e48",
    "\u60f3\u804a\u4ec0\u4e48",
    "\u5927\u5bb6\u60f3\u770b",
    "\u6709\u4ec0\u4e48\u63a8\u8350",
    "\u6c42\u63a8\u8350",
    "\u63a8\u8350\u4e00\u4e0b",
    "\u62bd\u5956",
    "\u8f6c\u53d1\u62bd\u5956",
    "\u5173\u6ce8\u8f6c\u53d1",
    "\u9650\u65f6\u798f\u5229",
    "\u9650\u65f6\u4f18\u60e0",
    "\u5e7f\u544a",
    "\u7a81\u53d1\u4e8b\u6545",
    "\u4e8b\u6545",
    "\u4f24\u4ea1",
    "\u6b7b\u4ea1",
    "\u53bb\u4e16",
    "\u707e\u5bb3",
    "\u5730\u9707",
    "\u5760\u673a",
    "\u706b\u707e",
    "\u584c\u623f",
    "\u4e89\u8bae",
    "\u7f51\u66b4",
    "\u5f00\u76d2",
    "\u81ea\u6740",
    "\u51f6\u6740",
    "\u6709\u4ec0\u4e48\u597d\u804a\u7684",
    "\u76f4\u64ad\u95f4\u600e\u4e48\u8fd9\u4e48\u5b89\u9759",
    "\u600e\u4e48\u8fd9\u4e48\u5b89\u9759",
    "\u4e3a\u4ec0\u4e48\u7a81\u7136\u5b89\u9759",
    "\u7a81\u7136\u5b89\u9759",
    "\u6ca1\u4eba\u8bf4\u8bdd",
    "\u6ca1\u5f39\u5e55",
    "\u5f39\u5e55\u5c11",
    "\u51b7\u573a",
)


def is_meaningful_active_topic_text(text: str) -> bool:
    compact = compact_topic_text(text)
    if not compact:
        return False
    if active_topic_safety.is_low_confidence_active_topic_text(compact):
        return False
    lowered = compact.lower()
    if lowered in {"hi", "hello", "ok", "1", "?", "\uff1f", "\u597d", "\u55ef", "\u554a", "\u8349", "6"}:
        return False
    dense_lowered = dense_topic_text(lowered)
    if is_direct_question_to_neko(compact, lowered, dense_lowered):
        return False
    if "\u4f60\u89c9\u5f97" in dense_lowered or "\u732b\u732b\u89c9\u5f97" in dense_lowered or "doyouthink" in dense_lowered:
        return False
    if active_topic_filters.is_direct_neko_request_or_ack(dense_lowered):
        return False
    if active_topic_filters.is_untargeted_request_or_reaction(dense_lowered):
        return False
    if active_topic_filters.is_live_test_or_runtime_feedback(dense_lowered):
        return False
    if matches_generic_host_phrase(lowered, dense_lowered):
        return False
    signal_chars = [ch for ch in compact if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
    return len(signal_chars) >= 4


def active_topic_filter_reason(text: str) -> str:
    compact = compact_topic_text(text)
    if not compact:
        return "filtered_recent_danmaku"
    if active_topic_safety.is_low_confidence_active_topic_text(compact):
        return "low_confidence_topic"
    lowered = compact.lower()
    dense_lowered = dense_topic_text(lowered)
    if active_topic_mentions.is_viewer_to_viewer_mention_text(compact):
        return "viewer_to_viewer_mention"
    if active_topic_filters.is_direct_neko_request_or_ack(dense_lowered) or active_topic_filters.is_untargeted_request(
        dense_lowered
    ):
        return "filtered_direct_request"
    if active_topic_filters.is_live_test_or_runtime_feedback(dense_lowered):
        return "filtered_runtime_feedback"
    if active_topic_filters.is_reaction_only(dense_lowered):
        return "filtered_reaction"
    return "filtered_recent_danmaku"


def compact_topic_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def dense_topic_text(text: str) -> str:
    return "".join(ch for ch in str(text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def is_direct_question_to_neko(compact: str, lowered: str, dense_lowered: str) -> bool:
    if "?" not in compact and "\uff1f" not in compact and not dense_lowered.endswith("\u5417"):
        return False
    if any(target in dense_lowered for target in ("\u4f60", "\u732b\u732b", "neko")):
        return True
    english_markers = ("do you", "are you", "what do you", "can you", "could you", "will you", "would you")
    return any(marker in lowered for marker in english_markers)


def matches_generic_host_phrase(lowered: str, dense_lowered: str) -> bool:
    for phrase in GENERIC_HOST_PHRASES:
        dense_phrase = dense_topic_text(phrase.lower())
        if phrase in lowered or (dense_phrase and dense_phrase in dense_lowered):
            return True
    return False
