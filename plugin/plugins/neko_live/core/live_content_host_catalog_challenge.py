"""Idle-hosting beat catalog entries for micro challenge."""

from __future__ import annotations

from typing import Any

MICRO_CHALLENGE_IDLE_HOSTING_BEAT_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "idle:micro-challenge",
        "live_column": "NEKO three-second challenge",
        "shape": "micro_challenge",
        "fun_axis": "micro_challenge",
        "title": "\u732b\u732b\u5047\u88c5\u4e3b\u64ad\u529b\u6ee1\u683c\u4e09\u79d2",
        "hint": "Offer one tiny challenge viewers can answer or tease back in a few words.",
        "reply_affordance": "viewer can judge or tease the tiny challenge",
    },
    {
        "key": "idle:serious-three-seconds",
        "live_column": "NEKO three-second challenge",
        "shape": "micro_challenge",
        "fun_axis": "micro_challenge",
        "title": "\u732b\u732b\u6311\u6218\u6b63\u7ecf\u4e3b\u6301\u4e09\u79d2",
        "hint": "Offer one tiny self-challenge; stop before it becomes a segment.",
        "reply_affordance": "viewer can judge whether NEKO stayed serious",
    },
    {
        "key": "idle:steady-three-sec",
        "live_column": "NEKO three-second challenge",
        "shape": "micro_challenge",
        "fun_axis": "micro_challenge",
        "title": "\u732b\u732b\u4e09\u79d2\u5047\u88c5\u5f88\u7a33",
        "hint": "Make one tiny self-challenge about staying composed; do not explain anything.",
        "reply_affordance": "viewer can judge whether NEKO stayed steady",
    },
)
