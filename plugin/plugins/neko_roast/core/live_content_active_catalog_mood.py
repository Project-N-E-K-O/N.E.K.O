"""Active-engagement fallback topic catalog entries for mood."""

from __future__ import annotations

from typing import Any

MOOD_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:tiny-confession",
        "live_column": "NEKO mood card",
        "title": "\u732b\u732b\u5076\u5c14\u4e5f\u4f1a\u88ab\u81ea\u5df1\u7684\u8ba4\u771f\u5413\u5230",
        "fun_axis": "mood",
        "preferred_shape": "light_stance",
        "reply_affordance": "viewer can agree or lightly push back",
        "hint": "Make one tiny confession, then stop before it becomes a monologue.",
    },
    {
        "source": "fallback",
        "key": "fallback:blanket-temperature",
        "live_column": "NEKO room thermometer",
        "title": "\u6b64\u523b\u6c14\u6c1b\u50cf\u6bdb\u6bef\u521a\u70ed\u8d77\u6765",
        "fun_axis": "mood",
        "preferred_shape": "light_stance",
        "reply_affordance": "viewer can answer with one mood word",
        "hint": "Describe this concrete mood image and invite one short reaction.",
    },
    {
        "source": "fallback",
        "key": "fallback:tiny-brave-stance",
        "live_column": "NEKO tiny verdict",
        "title": "\u732b\u732b\u89c9\u5f97\u53d1\u5446\u4e5f\u7b97\u4e00\u79cd\u4e3b\u64ad\u529b",
        "fun_axis": "mood",
        "preferred_shape": "light_stance",
        "reply_affordance": "viewer can agree or push back",
        "hint": "Give one tiny NEKO stance that viewers can immediately agree or object to.",
    },
    {
        "source": "fallback",
        "key": "fallback:soft-business-day",
        "live_column": "NEKO mood card",
        "title": "\u732b\u732b\u51b3\u5b9a\u628a\u4eca\u5929\u53eb\u505a\u8f7b\u8f7b\u8425\u4e1a\u65e5",
        "fun_axis": "mood",
        "preferred_shape": "light_stance",
        "reply_affordance": "viewer can agree or rename the day",
        "hint": "Give one small NEKO stance; leave one low-pressure renaming path.",
    },
    {
        "source": "fallback",
        "key": "fallback:lightstick-reflection",
        "live_column": "NEKO tiny observation",
        "title": "\u732b\u732b\u628a\u5c4f\u5e55\u53cd\u5149\u5f53\u6210\u89c2\u4f17\u5e2d\u706f\u724c",
        "fun_axis": "mood",
        "preferred_shape": "light_stance",
        "reply_affordance": "viewer can answer with one tiny scene",
        "hint": "Use one concrete visual image; no audience-bait opening.",
    },
)
