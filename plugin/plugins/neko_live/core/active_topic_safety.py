"""Safety filters for active engagement topics and live materials."""

from __future__ import annotations


ACTIVE_TOPIC_LOW_CONFIDENCE_TERMS = (
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


def is_low_confidence_active_topic_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return True
    lowered = compact.casefold()
    dense = "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if any(term.casefold() in lowered or term.casefold() in dense for term in ACTIVE_TOPIC_LOW_CONFIDENCE_TERMS):
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


def is_clean_live_material_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return False
    lowered = compact.casefold()
    # Common mojibake markers from UTF-8 text decoded as a legacy codepage.
    mojibake_markers = ("\ufffd", "锟", "閻", "鐏", "鐚", "缁", "濮", "閸", "閿", "閳")
    if any(marker in compact for marker in mojibake_markers):
        return False
    if compact.count('"') % 2:
        return False
    if is_low_confidence_active_topic_text(compact):
        return False
    return not any(term.casefold() in lowered for term in ("public shaming", "labor camp", "punishment"))


def is_clean_live_material(material: dict | None) -> bool:
    if not isinstance(material, dict):
        return False
    fields = ("title", "hint", "reply_affordance", "live_column")
    values = [str(material.get(field) or "").strip() for field in fields]
    return any(values) and all(is_clean_live_material_text(value) for value in values if value)
