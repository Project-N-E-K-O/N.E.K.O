"""Material profile hints for active engagement topics."""

from __future__ import annotations


def active_topic_material_profile(title: str) -> dict[str, str]:
    compact = " ".join(str(title or "").strip().split())
    dense = _dense_text(compact)
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
            "reply_affordance": "viewer can answer in danmaku with one concrete side",
            "hint": "Use A/B word choice and turn this material into one concrete A/B choice; do not ask a broad topic question.",
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
            "keyboard",
            "screen",
            "desk",
            "snack",
            "drink",
            "\u952e\u76d8",
            "\u5c4f\u5e55",
            "\u684c\u9762",
            "\u6c34\u676f",
            "\u996e\u6599",
            "\u96f6\u98df",
        )
    ):
        return {
            "preferred_shape": "tiny_tease",
            "fun_axis": "object_scene",
            "live_column": "NEKO room observation",
            "reply_affordance": "viewer can answer with one small object or room detail",
            "hint": "Turn this material into one tiny room observation; do not pretend to know details beyond the title.",
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


def _dense_text(value: str) -> str:
    lowered = value.lower()
    return "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
