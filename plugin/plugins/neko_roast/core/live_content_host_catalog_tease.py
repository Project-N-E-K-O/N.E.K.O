"""Idle-hosting beat catalog entries for tease."""

from __future__ import annotations

from typing import Any

TEASE_IDLE_HOSTING_BEAT_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "idle:light-tease",
        "live_column": "NEKO tiny verdict",
        "shape": "light_tease",
        "fun_axis": "tease",
        "title": "\u732b\u732b\u5f0f\u8f7b\u5410\u69fd",
        "hint": "Make one harmless NEKO-flavored tease about the current quiet mood.",
        "reply_affordance": "viewer can tease NEKO back in one short line",
    },
    {
        "key": "idle:reverse-tease",
        "live_column": "NEKO self-roast",
        "shape": "light_tease",
        "fun_axis": "tease",
        "title": "\u732b\u732b\u5148\u88ab\u81ea\u5df1\u7684\u4e3b\u64ad\u529b\u5410\u69fd\u4e00\u4e0b",
        "hint": "Make one self-directed tease, not a tease at viewers.",
        "reply_affordance": "viewer can tease NEKO back gently",
    },
    {
        "key": "idle:keyboard-patrol",
        "live_column": "NEKO tiny patrol",
        "shape": "soft_observation",
        "fun_axis": "tease",
        "title": "\u4eca\u665a\u7684\u952e\u76d8\u50cf\u5728\u5077\u5077\u5de1\u903b",
        "hint": "Make one playful object observation; do not turn it into a topic survey.",
        "reply_affordance": "viewer can tease the keyboard or NEKO",
    },
    {
        "key": "idle:unreliable-award",
        "live_column": "NEKO tiny award",
        "shape": "light_tease",
        "fun_axis": "tease",
        "title": "\u732b\u732b\u7ed9\u81ea\u5df1\u9881\u4e00\u4e2a\u4e0d\u592a\u9760\u8c31\u5956",
        "hint": "Make one self-roast award line; do not announce a full ceremony.",
        "reply_affordance": "viewer can name or tease the award",
    },
)
