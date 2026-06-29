"""Pure active-engagement topic rules.

These helpers are intentionally free of runtime state so filtering and
reply-shape policy can be reviewed without opening the scheduler.
"""

from __future__ import annotations

from collections import deque
from difflib import SequenceMatcher
import re


_ACTIVE_TOPIC_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
_ACTIVE_TOPIC_SIMILARITY_THRESHOLD = 0.78
_ACTIVE_TOPIC_MIN_NORMALIZED_CHARS = 6


def _is_meaningful_active_topic_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return False
    lowered = compact.lower()
    if lowered in {"hi", "hello", "ok", "1", "?", "？", "好", "嗯", "啊", "草", "6"}:
        return False
    generic_host_phrases = (
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
        "\u62631",
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
    dense_lowered = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if (
        ("?" in compact or "\uff1f" in compact or dense_lowered.endswith("\u5417"))
        and (
            any(target in dense_lowered for target in ("\u4f60", "\u732b\u732b", "neko"))
            or any(
                marker in lowered
                for marker in ("do you", "are you", "what do you", "can you", "could you", "will you", "would you")
            )
        )
    ):
        return False
    if (
        "\u4f60\u89c9\u5f97" in dense_lowered
        or "\u732b\u732b\u89c9\u5f97" in dense_lowered
        or "doyouthink" in dense_lowered
    ):
        return False
    if _is_direct_neko_request_or_ack(dense_lowered):
        return False
    if _is_untargeted_request_or_reaction(dense_lowered):
        return False
    if _is_live_test_or_runtime_feedback(dense_lowered):
        return False
    if any(
        phrase in lowered
        or "".join(ch for ch in phrase if ch.isalnum() or "\u4e00" <= ch <= "\u9fff") in dense_lowered
        for phrase in generic_host_phrases
    ):
        return False
    signal_chars = [ch for ch in compact if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
    return len(signal_chars) >= 4

def _active_topic_filter_reason(text: str) -> str:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return "filtered_recent_danmaku"
    lowered = compact.lower()
    dense_lowered = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if _is_viewer_to_viewer_mention_text(compact):
        return "viewer_to_viewer_mention"
    if _is_direct_neko_request_or_ack(dense_lowered) or _is_untargeted_request(
        dense_lowered
    ):
        return "filtered_direct_request"
    if _is_live_test_or_runtime_feedback(dense_lowered):
        return "filtered_runtime_feedback"
    if _is_reaction_only(dense_lowered):
        return "filtered_reaction"
    return "filtered_recent_danmaku"

def _is_direct_neko_request_or_ack(dense_lowered: str) -> bool:
    if not any(target in dense_lowered for target in ("\u732b\u732b", "neko")):
        return False
    chinese_markers = (
        "\u8bb2\u8bb2",
        "\u8bf4\u8bf4",
        "\u804a\u804a",
        "\u8bc4\u4ef7\u4e00\u4e0b",
        "\u9510\u8bc4\u4e00\u4e0b",
        "\u70b9\u8bc4\u4e00\u4e0b",
        "\u5e2e\u6211",
        "\u7ed9\u6211",
        "\u80fd\u4e0d\u80fd",
        "\u53ef\u4e0d\u53ef\u4ee5",
        "\u53ef\u4ee5\u4e0d\u53ef\u4ee5",
        "\u8981\u4e0d\u8981",
        "\u8c22\u8c22",
        "\u611f\u8c22",
        "\u8f9b\u82e6\u4e86",
    )
    english_markers = (
        "tellus",
        "helpme",
        "giveme",
        "ratemy",
        "tellme",
        "canyou",
        "couldyou",
        "willyou",
        "wouldyou",
        "please",
        "pls",
        "thankyou",
        "thanks",
        "thx",
    )
    return any(marker in dense_lowered for marker in chinese_markers + english_markers)

def _is_untargeted_request_or_reaction(dense_lowered: str) -> bool:
    return _is_untargeted_request(dense_lowered) or _is_reaction_only(dense_lowered)

def _is_untargeted_request(dense_lowered: str) -> bool:
    request_markers = (
        "\u8bb2\u8bb2",
        "\u8bf4\u8bf4",
        "\u804a\u804a",
        "\u8bc4\u4ef7\u4e00\u4e0b",
        "\u9510\u8bc4\u4e00\u4e0b",
        "\u70b9\u8bc4\u4e00\u4e0b",
        "\u63a8\u8350\u4e00\u4e0b",
        "\u9009\u4e00\u4e0b",
        "\u8d77\u4e2a\u5916\u53f7",
        "\u5e2e\u6211",
        "\u7ed9\u6211",
        "tellme",
        "recommendme",
        "giveme",
        "ratemy",
        "helpme",
        "canyou",
        "couldyou",
        "please",
        "pls",
    )
    return any(marker in dense_lowered for marker in request_markers)

def _is_reaction_only(dense_lowered: str) -> bool:
    reaction_markers = (
        "\u54c8\u54c8",
        "\u7b11\u6b7b",
        "\u7ef7\u4e0d\u4f4f",
        "\u8349\u8349",
        "\u725b\u554a",
        "\u725b\u903c",
        "\u597d\u8036",
        "666",
        "lol",
        "lmao",
    )
    return len(dense_lowered) <= 8 and any(marker in dense_lowered for marker in reaction_markers)

def _is_live_test_or_runtime_feedback(dense_lowered: str) -> bool:
    control_markers = (
        "\u4e0b\u4e00\u6b65",
        "\u770b\u72b6\u6001",
        "\u68c0\u6d4b\u72b6\u6001",
        "\u91cd\u542f",
        "\u5173\u95ed",
        "\u5f00\u542f",
        "\u63d0\u4ea4",
        "\u63a8\u9001",
        "\u6d4b\u8bd5\u7ed3\u675f",
        "nextstep",
        "checkstatus",
        "restart",
        "reload",
        "shutdown",
    )
    if any(marker in dense_lowered for marker in control_markers):
        return True
    feedback_markers = (
        "\u5ef6\u8fdf",
        "\u5361\u4e86",
        "\u5361\u4f4f",
        "\u6709\u70b9\u957f",
        "\u592a\u957f",
        "\u56de\u590d\u957f",
        "\u8f93\u51fa\u957f",
        "\u6ca1\u8f93\u51fa",
        "\u6ca1\u6709\u8f93\u51fa",
        "\u6ca1\u89e6\u53d1",
        "\u89e6\u53d1\u4e86",
        "latency",
        "toolong",
        "nooutput",
        "notriggered",
    )
    return any(marker in dense_lowered for marker in feedback_markers)

def _has_active_engagement_streak(values: deque[str], value: str, count: int) -> bool:
    if count <= 0 or len(values) < count:
        return False
    tail = list(values)[-count:]
    return all(str(item or "") == value for item in tail)

def _normalize_active_topic_title(text: str) -> str:
    return _ACTIVE_TOPIC_NORMALIZE_RE.sub("", str(text or "").casefold())

def _is_similar_active_topic_title(title: str, recent_titles: deque[str] | list[str] | tuple[str, ...]) -> bool:
    normalized = _normalize_active_topic_title(title)
    if len(normalized) < _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS:
        return False
    for previous in recent_titles:
        previous_normalized = _normalize_active_topic_title(previous)
        if len(previous_normalized) < _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS:
            continue
        if normalized == previous_normalized:
            return True
        shorter, longer = (
            (normalized, previous_normalized)
            if len(normalized) <= len(previous_normalized)
            else (previous_normalized, normalized)
        )
        if len(shorter) >= _ACTIVE_TOPIC_MIN_NORMALIZED_CHARS and shorter in longer:
            return True
        if SequenceMatcher(None, normalized, previous_normalized).ratio() >= _ACTIVE_TOPIC_SIMILARITY_THRESHOLD:
            return True
    return False

def _host_material_family(material: dict | None) -> str:
    if not isinstance(material, dict):
        return ""
    combined = " ".join(
        str(material.get(field) or "")
        for field in ("key", "title", "fun_axis", "shape", "preferred_shape")
    )
    lowered = combined.lower()
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if not dense:
        return ""
    if any(marker in dense for marker in ("oneword", "onechar", "password", "\u4e00\u4e2a\u5b57", "\u4e00\u4e2a\u8bcd", "\u4e09\u5b57", "\u6697\u53f7")):
        return "short_callback"
    if any(marker in dense for marker in ("choice", "eitheror", "ab", "\u4e8c\u9009\u4e00", "\u8fd8\u662f", "\u9009\u4e00")):
        return "choice_vote"
    if any(marker in dense for marker in ("serious", "hosting", "hostscore", "\u4e3b\u64ad\u529b", "\u6b63\u7ecf\u4e3b\u64ad", "\u50cf\u4e0d\u50cf\u4e3b\u64ad")):
        return "host_self_test"
    if any(marker in dense for marker in ("keyboard", "screen", "desk", "\u952e\u76d8", "\u5c4f\u5e55", "\u684c\u9762", "\u6c34\u676f", "\u96f6\u98df")):
        return "object_scene"
    if any(
        marker in dense
        for marker in (
            "radio",
            "blanket",
            "weather",
            "temperature",
            "mood",
            "stamp",
            "\u7535\u53f0",
            "\u6bdb\u6bef",
            "\u6e29\u5ea6",
            "\u6674\u5929",
            "\u5c0f\u96e8",
            "\u6c14\u6c1b",
            "\u72b6\u6001",
            "\u5fc3\u60c5",
            "\u7ae0",
        )
    ):
        return "room_mood"
    if any(marker in dense for marker in ("tease", "\u5410\u69fd", "\u88ab\u81ea\u5df1", "\u5148\u522b\u7b11")):
        return "tease"
    if any(marker in dense for marker in ("challenge", "mission", "\u6311\u6218", "\u4efb\u52a1", "\u59ff\u52bf")):
        return "micro_challenge"
    axis = str(material.get("fun_axis") or "").strip()
    shape = str(material.get("shape") or material.get("preferred_shape") or "").strip()
    return axis or shape

def _active_topic_material_profile(title: str) -> dict[str, str]:
    compact = " ".join(str(title or "").strip().split())
    lowered = compact.lower()
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if not dense:
        return {}
    if any(
        marker in dense
        for marker in (
            "choice",
            "pick",
            "vs",
            "\u4e8c\u9009\u4e00",
            "\u4e8c\u62e9\u4e00",
            "\u9009\u4e00",
            "\u54ea\u8fb9",
            "\u8fd8\u662f",
            "\u6295\u7968",
        )
    ):
        return {
            "preferred_shape": "either_or",
            "fun_axis": "choice",
            "live_column": "NEKO micro poll",
            "reply_affordance": "viewer can pick one concrete side",
            "hint": "Turn this material into one concrete A/B choice; do not ask a broad topic question.",
        }
    if any(
        marker in dense
        for marker in (
            "challenge",
            "mission",
            "score",
            "rate",
            "\u6311\u6218",
            "\u4efb\u52a1",
            "\u6253\u5206",
            "\u6d4b\u8bd5",
            "\u8bd5\u8bd5",
            "\u6b63\u7ecf",
            "\u5047\u88c5",
        )
    ):
        return {
            "preferred_shape": "small_challenge",
            "fun_axis": "micro_challenge",
            "live_column": "NEKO three-second challenge",
            "reply_affordance": "viewer can answer the tiny challenge in a few words",
            "hint": "Turn this material into one tiny low-pressure challenge; stop before it becomes a segment.",
        }
    if any(
        marker in dense
        for marker in (
            "tease",
            "funny",
            "weird",
            "sleepy",
            "suspicious",
            "\u5410\u69fd",
            "\u79bb\u8c31",
            "\u5077\u5077",
            "\u6253\u76f9",
            "\u7b11",
            "\u5947\u602a",
            "\u600e\u4e48\u8fd9\u4e48",
            "\u786c\u6491",
        )
    ):
        return {
            "preferred_shape": "tiny_tease",
            "fun_axis": "tease",
            "live_column": "NEKO tiny verdict",
            "reply_affordance": "viewer can tease NEKO or the topic back",
            "hint": "Turn this material into one tiny playful tease; do not make it a news recap.",
        }
    if any(
        marker in dense
        for marker in (
            "mood",
            "room",
            "radio",
            "weather",
            "temperature",
            "\u6c14\u6c1b",
            "\u5c0f\u7535\u53f0",
            "\u7535\u53f0",
            "\u6e29\u5ea6",
            "\u6674\u5929",
            "\u5c0f\u96e8",
            "\u72b6\u6001",
        )
    ):
        return {
            "preferred_shape": "light_stance",
            "fun_axis": "mood",
            "live_column": "NEKO mood card",
            "reply_affordance": "viewer can agree or answer with one small mood word",
            "hint": "Turn this material into one tiny NEKO stance or mood image; keep it easy to answer.",
        }
    return {}

def _is_viewer_to_viewer_mention_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().replace("＠", "@").split())
    if "@" not in compact:
        return False
    aliases = {"neko", "\u732b\u732b", "\u5c0f\u5929", "\u732b\u5a18"}
    lowered_aliases = {alias.lower() for alias in aliases}
    for part in compact.split("@")[1:]:
        target = []
        for ch in part.strip():
            if ch.isspace() or ch in ":：,，.。!！?？/\\|[]()（）<>《》":
                break
            target.append(ch)
        name = "".join(target).strip()
        if not name:
            continue
        if _is_neko_mention_target(name, lowered_aliases):
            return False
        return True
    return False

def _is_neko_mention_target(name: str, lowered_aliases: set[str]) -> bool:
    lowered_name = str(name or "").strip().lower()
    if not lowered_name:
        return False
    if lowered_name in lowered_aliases:
        return True
    live_address_prefixes = (
        "\u4eca",
        "\u4f60",
        "\u5728",
        "\u80fd",
        "\u53ef",
        "\u5e2e",
        "\u6765",
        "\u8bb2",
        "\u8bf4",
        "\u600e",
        "\u4e3a",
        "\u8981",
        "\u6709",
        "\u662f",
        "\u4f1a",
        "\u60f3",
        "\u64ad",
        "\u8bc4",
        "\u9510",
        "\u65e9",
        "\u665a",
        "\u5462",
        "\u5440",
        "\u554a",
        "\u5417",
        "\u561b",
        "\u5427",
        "\u54c8",
        "what",
        "why",
        "how",
        "can",
        "could",
        "please",
        "pls",
        "pick",
        "rate",
        "tell",
        "help",
        "say",
    )
    for alias in lowered_aliases:
        if not lowered_name.startswith(alias):
            continue
        rest = lowered_name[len(alias) :].lstrip("_-")
        if not rest:
            return True
        if rest.startswith(live_address_prefixes):
            return True
    return False

def _active_engagement_hook_text(shape: str, title: str) -> str:
    compact_title = title.strip() or "this live-room topic"
    return {
        "either_or": f"Make '{compact_title}' into one concrete A/B choice viewers can answer with one side.",
        "light_stance": f"Take one small, playful NEKO stance about '{compact_title}' so viewers can agree or push back.",
        "tiny_tease": f"Turn '{compact_title}' into one tiny playful tease, not a news recap or generic question.",
        "small_challenge": f"Turn '{compact_title}' into one tiny low-pressure challenge viewers can answer in a few words.",
    }.get(shape, f"Turn '{compact_title}' into one specific low-pressure hook viewers can answer quickly.")

def _active_engagement_pattern_text(shape: str) -> str:
    return {
        "either_or": "two concrete sides, then let viewers pick one",
        "light_stance": "one tiny NEKO opinion, then leave room for pushback",
        "tiny_tease": "one small playful jab, then stop before it becomes a bit",
        "small_challenge": "one tiny challenge viewers can answer in a few words",
    }.get(shape, "one concrete reply point viewers can answer quickly")

def _active_engagement_hint_text(shape: str) -> str:
    return {
        "either_or": "Make one tiny A/B choice; both sides must be concrete and easy to answer.",
        "light_stance": "Make one tiny NEKO stance; leave room for viewers to agree or push back.",
        "tiny_tease": "Make one tiny playful tease; stop before it becomes a bit.",
        "small_challenge": "Make one tiny low-pressure challenge viewers can answer in a few words.",
    }.get(shape, "Make one specific low-pressure hook viewers can answer quickly.")

def _active_engagement_intent_text(shape: str) -> str:
    return {
        "either_or": "quick_vote",
        "light_stance": "agree_or_pushback",
        "tiny_tease": "tease_back",
        "small_challenge": "tiny_answer",
    }.get(shape, "quick_reply")

def _active_engagement_fun_axis_text(shape: str) -> str:
    return {
        "either_or": "choice",
        "light_stance": "mood",
        "tiny_tease": "tease",
        "small_challenge": "micro_challenge",
    }.get(shape, "choice")

def _active_engagement_reply_affordance_text(shape: str) -> str:
    return {
        "either_or": "viewer can answer with one side",
        "light_stance": "viewer can agree or push back",
        "tiny_tease": "viewer can tease NEKO back",
        "small_challenge": "viewer can answer in a few words",
    }.get(shape, "viewer can reply quickly")
