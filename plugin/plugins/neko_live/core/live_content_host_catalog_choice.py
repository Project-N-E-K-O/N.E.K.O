"""Idle-hosting beat catalog entries for choice."""

from __future__ import annotations

from typing import Any

CHOICE_IDLE_HOSTING_BEAT_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "idle:tiny-choice",
        "live_column": "NEKO micro poll",
        "shape": "tiny_choice",
        "fun_axis": "choice",
        "title": "\u732b\u732b\u7ed9\u89c2\u4f17\u7684\u5c0f\u9009\u62e9",
        "hint": "Offer one tiny A/B choice viewers can answer in a few words.",
        "reply_affordance": "viewer can answer in danmaku with one concrete side",
    },
    {
        "key": "idle:prop-choice",
        "live_column": "NEKO desk poll",
        "shape": "tiny_choice",
        "fun_axis": "choice",
        "title": "\u684c\u9762\u4e8c\u9009\u4e00\uff1a\u6c34\u676f\u8fd8\u662f\u96f6\u98df",
        "hint": "Offer one concrete A/B choice from ordinary stream-room objects.",
        "reply_affordance": "viewer can pick one ordinary object",
    },
    {
        "key": "idle:cat-paw-button",
        "live_column": "NEKO cat-paw button",
        "shape": "tiny_choice",
        "fun_axis": "choice",
        "title": "\u732b\u722a\u6309\u94ae\u4e8c\u9009\u4e00\uff1a\u5356\u840c\u8fd8\u662f\u5410\u69fd",
        "hint": "Offer one playful cat-paw A/B choice; both sides must be concrete.",
        "reply_affordance": "viewer can pick cute mode or roast mode",
    },
    {
        "key": "idle:tail-state-choice",
        "live_column": "NEKO tail poll",
        "shape": "tiny_choice",
        "fun_axis": "choice",
        "title": "\u7ed9\u6b64\u523b\u7684\u732b\u732b\u9009\u4e00\u4e2a\u5c3e\u5df4\u72b6\u6001\uff1a\u7ad6\u8d77\u8fd8\u662f\u5377\u4f4f",
        "hint": "Offer one tail-state A/B choice; both sides must be short.",
        "reply_affordance": "viewer can pick raised tail or curled tail",
    },
)
