"""Active-engagement fallback topic catalog entries for viewer callback."""

from __future__ import annotations

from typing import Any

VIEWER_CALLBACK_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:room-temperature-word",
        "live_column": "NEKO room thermometer",
        "title": "\u7528\u4e00\u4e2a\u8bcd\u5f62\u5bb9\u73b0\u5728\u76f4\u64ad\u95f4\u7684\u6e29\u5ea6",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with one word",
        "hint": "Ask for one word only; avoid open-ended long discussion.",
    },
    {
        "source": "fallback",
        "key": "fallback:danmaku-password",
        "live_column": "NEKO room password",
        "title": "\u7ed9\u4eca\u665a\u76f4\u64ad\u95f4\u5b9a\u4e00\u4e2a\u4e09\u5b57\u6697\u53f7",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can reply with a three-character password",
        "hint": "Ask for one three-character room password; make it easy to answer in one short danmaku.",
    },
    {
        "source": "fallback",
        "key": "fallback:one-word-barrage",
        "live_column": "NEKO one-word callback",
        "title": "\u7528\u4e00\u4e2a\u5b57\u7ed9\u732b\u732b\u73b0\u5728\u7684\u4e3b\u64ad\u529b\u6253\u5206",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with one character",
        "hint": "Ask for one character only; make it sound like NEKO inviting a tiny roast back.",
    },
    {
        "source": "fallback",
        "key": "fallback:host-score-one-word",
        "live_column": "NEKO one-word callback",
        "title": "\u7528\u4e00\u4e2a\u8bcd\u5224\u5b9a\u732b\u732b\u4eca\u665a\u50cf\u4e0d\u50cf\u4e3b\u64ad",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with one word",
        "hint": "Ask for one word only; make it easy to tease back.",
    },
    {
        "source": "fallback",
        "key": "fallback:tail-one-char",
        "live_column": "NEKO one-char command",
        "title": "\u7528\u4e00\u4e2a\u5b57\u7ed9\u732b\u732b\u7684\u5c3e\u5df4\u6253\u72b6\u6001",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with one character",
        "hint": "Ask for one character only; keep the request tiny.",
    },
    {
        "source": "fallback",
        "key": "fallback:two-char-password",
        "live_column": "NEKO room password",
        "title": "\u7ed9\u732b\u732b\u4e00\u4e2a\u6697\u53f7\uff0c\u4e24\u4e2a\u5b57\u5c31\u884c",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with two characters",
        "hint": "Ask for two characters only; make the constraint explicit and tiny.",
    },
    {
        "source": "fallback",
        "key": "fallback:air-filter-word",
        "live_column": "NEKO room filter",
        "title": "\u7528\u4e00\u4e2a\u8bcd\u7ed9\u4eca\u665a\u7684\u7a7a\u6c14\u52a0\u6ee4\u955c",
        "fun_axis": "viewer_callback",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with one filter word",
        "hint": "Ask for one word only; keep the hook visual and small.",
    },
)
