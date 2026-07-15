"""Active-engagement fallback choice topics for room and mood beats."""

from __future__ import annotations

from typing import Any


ROOM_CHOICE_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:snack-choice",
        "live_column": "NEKO micro poll",
        "title": "\u591c\u91cc\u53ea\u80fd\u9009\u4e00\u6837\uff1a\u5c0f\u751c\u98df\u8fd8\u662f\u70ed\u996e",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can answer in danmaku with one concrete side",
        "hint": "Turn this into one tiny A/B choice; both sides must be concrete and easy to answer.",
    },
    {
        "source": "fallback",
        "key": "fallback:today-mood-vote",
        "live_column": "NEKO micro poll",
        "title": "\u4eca\u5929\u7684\u72b6\u6001\u66f4\u50cf\u5145\u7535\u4e2d\u8fd8\u662f\u5df2\u6b7b\u673a",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick one side",
        "hint": "Ask one tiny vote that can be answered with one side, not a long explanation.",
    },
    {
        "source": "fallback",
        "key": "fallback:cat-radio-room",
        "live_column": "NEKO tiny radio",
        "title": "\u4eca\u665a\u76f4\u64ad\u95f4\u66f4\u50cf\u732b\u7a9d\u8fd8\u662f\u5c0f\u7535\u53f0",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick cat nest or tiny radio",
        "hint": "Turn this into one cozy A/B choice; keep it concrete and easy to answer.",
    },
    {
        "source": "fallback",
        "key": "fallback:night-owl-energy",
        "live_column": "NEKO late-night check",
        "title": "\u591c\u732b\u5b50\u73b0\u5728\u662f\u771f\u6e05\u9192\u8fd8\u662f\u5728\u786c\u6491",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick one late-night state",
        "hint": "Make one small stance or A/B choice about late-night energy.",
    },
    {
        "source": "fallback",
        "key": "fallback:cat-weather",
        "live_column": "NEKO weather vote",
        "title": "\u4eca\u665a\u732b\u732b\u72b6\u6001\u662f\u6674\u5929\u8fd8\u662f\u5c0f\u96e8",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick one weather mood",
        "hint": "Turn this into one playful A/B mood vote; viewers can answer with one side.",
    },
)
