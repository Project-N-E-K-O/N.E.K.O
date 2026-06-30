"""Pure text filtering rules for active engagement topics."""

from __future__ import annotations


_ACTIVE_TOPIC_LOW_CONFIDENCE_TERMS = (
    "\u6838\u7535",
    "\u6838\u7535\u7ad9",
    "\u6838\u8f90\u5c04",
    "\u8f90\u5c04",
    "\u7206\u7834",
    "\u7206\u70b8",
    "\u52b3\u6539",
    "\u516c\u5f00\u793a\u4f17",
    "\u793a\u4f17",
    "\u5904\u5211",
    "\u60e9\u7f5a",
    "\u5ba1\u5224",
    "\u653b\u7565",
    "\u6559\u7a0b",
    "\u4e13\u5bb6",
    "\u61c2\u5f88\u591a",
    "\u8dd1\u4ee3\u7801",
    "\u903b\u8f91\u7535\u8def",
    "\u6f0f\u52fa",
    "nuclear",
    "radiation",
    "punish",
    "trial",
    "expert",
)


def _is_meaningful_active_topic_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return False
    if _is_low_confidence_active_topic_text(compact):
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
    if _is_low_confidence_active_topic_text(compact):
        return "low_confidence_topic"
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

def _is_low_confidence_active_topic_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return True
    lowered = compact.casefold()
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if any(term.casefold() in lowered or term.casefold() in dense for term in _ACTIVE_TOPIC_LOW_CONFIDENCE_TERMS):
        return True
    # Active engagement should not spin highly specific game/wiki/news titles
    # unless they expose a small, safe reply handle. Let danmaku_response handle
    # those directly instead of forcing a weird A/B host question.
    game_or_technical_markers = (
        "\u6cf0\u62c9\u745e\u4e9a",
        "\u6211\u7684\u4e16\u754c",
        "\u661f\u9732\u8c37",
        "\u660e\u65e5\u65b9\u821f",
        "\u539f\u795e",
        "\u5d29\u574f",
        "\u7edd\u533a\u96f6",
        "\u4ee3\u7801",
        "\u7f16\u7a0b",
        "\u7535\u8def",
        "\u673a\u5236",
        "\u914d\u88c5",
        "\u914d\u65b9",
        "terraria",
        "minecraft",
        "code",
        "circuit",
    )
    if any(marker in lowered or marker in dense for marker in game_or_technical_markers):
        return True
    return False

def _is_clean_live_material_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return False
    lowered = compact.casefold()
    # Common mojibake markers from UTF-8 text decoded as a legacy codepage.
    if any(marker in compact for marker in ("鐚", "灞", "绁", "姝", "鍍", "锛", "鈥", "�")):
        return False
    if compact.count('"') % 2:
        return False
    if _is_low_confidence_active_topic_text(compact):
        return False
    return not any(term.casefold() in lowered for term in ("public shaming", "labor camp", "punishment"))

def _is_clean_live_material(material: dict | None) -> bool:
    if not isinstance(material, dict):
        return False
    fields = ("title", "hint", "reply_affordance", "live_column")
    values = [str(material.get(field) or "").strip() for field in fields]
    return any(values) and all(_is_clean_live_material_text(value) for value in values if value)

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

