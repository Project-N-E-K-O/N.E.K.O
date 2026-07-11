"""Active-engagement fallback topic catalog entries for tease."""

from __future__ import annotations

from typing import Any

TEASE_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:keyboard-busy",
        "live_column": "NEKO tiny verdict",
        "title": "\u952e\u76d8\u4eca\u5929\u50cf\u5728\u5077\u5077\u6253\u76f9",
        "fun_axis": "tease",
        "preferred_shape": "tiny_tease",
        "reply_affordance": "viewer can tease the keyboard or NEKO back",
        "hint": "Make one playful observation; leave a tiny opening without talking about room silence.",
    },
    {
        "source": "fallback",
        "key": "fallback:screen-staring-back",
        "live_column": "NEKO tiny verdict",
        "title": "\u76ef\u5c4f\u5e55\u4e45\u4e86\uff0c\u732b\u732b\u6000\u7591\u5c4f\u5e55\u4e5f\u5728\u76ef\u56de\u6765",
        "fun_axis": "tease",
        "preferred_shape": "tiny_tease",
        "reply_affordance": "viewer can tease back or say caught",
        "hint": "Make one tiny playful observation, not a generic question.",
    },
    {
        "source": "fallback",
        "key": "fallback:serious-hosting",
        "live_column": "NEKO self-roast",
        "title": "\u732b\u732b\u6b63\u5728\u52aa\u529b\u50cf\u4e2a\u6b63\u7ecf\u4e3b\u64ad\uff0c\u5148\u522b\u7b11",
        "fun_axis": "tease",
        "preferred_shape": "tiny_tease",
        "reply_affordance": "viewer can tease NEKO's serious host act",
        "hint": "Take one tiny NEKO stance and leave room for viewers to tease back.",
    },
    {
        "source": "fallback",
        "key": "fallback:weird-score",
        "live_column": "NEKO tiny score",
        "title": "\u732b\u732b\u7ed9\u8fd9\u5206\u949f\u6253\u4e00\u4e2a\u5947\u602a\u5206\u6570",
        "fun_axis": "tease",
        "preferred_shape": "tiny_tease",
        "reply_affordance": "viewer can answer with a weird score",
        "hint": "Make one odd score joke; do not ask for a long explanation.",
    },
    {
        "source": "fallback",
        "key": "fallback:tiny-court",
        "live_column": "NEKO tiny court",
        "title": "\u732b\u732b\u5c0f\u6cd5\u5ead\uff1a\u53d1\u5446\u7b97\u4e0d\u7b97\u8ba4\u771f\u8425\u4e1a",
        "fun_axis": "tease",
        "preferred_shape": "tiny_tease",
        "reply_affordance": "viewer can rule yes or no",
        "hint": "Make one playful tiny-court line; no real debate.",
    },
    {
        "source": "fallback",
        "key": "fallback:sleep-thief",
        "live_column": "NEKO tiny detective",
        "title": "\u732b\u732b\u5c0f\u4fa6\u63a2\uff1a\u8c01\u5728\u5077\u8d70\u56f0\u610f",
        "fun_axis": "tease",
        "preferred_shape": "tiny_tease",
        "reply_affordance": "viewer can name a tiny suspect",
        "hint": "Make one playful detective hook; do not ask a broad question.",
    },
)
