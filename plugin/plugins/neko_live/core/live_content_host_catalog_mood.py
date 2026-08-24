"""Idle-hosting beat catalog entries for mood."""

from __future__ import annotations

from typing import Any

MOOD_IDLE_HOSTING_BEAT_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "idle:soft-observation",
        "live_column": "NEKO tiny radio",
        "shape": "soft_observation",
        "fun_axis": "mood",
        "title": "\u5b89\u9759\u7684\u76f4\u64ad\u95f4\u6c14\u6c1b",
        "hint": "Say one soft concrete observation, not a direct question.",
        "reply_affordance": "viewer can agree or answer with one small mood word",
    },
    {
        "key": "idle:small-mood",
        "live_column": "NEKO mood card",
        "shape": "small_mood",
        "fun_axis": "mood",
        "title": "\u732b\u732b\u73b0\u5728\u7684\u5c0f\u72b6\u6001",
        "hint": "Share one tiny NEKO mood line with a small opening for viewers.",
        "reply_affordance": "viewer can answer with one mood word",
    },
    {
        "key": "idle:cat-radio",
        "live_column": "NEKO tiny radio",
        "shape": "soft_observation",
        "fun_axis": "mood",
        "title": "\u8fd9\u4e00\u5206\u949f\u50cf\u732b\u732b\u5c0f\u7535\u53f0\u7684\u7a7a\u62cd",
        "hint": "Make one cozy radio-like observation; keep the hook low-pressure and indirect.",
        "reply_affordance": "viewer can answer with one cozy mood or tiny scene",
    },
    {
        "key": "idle:screen-blink",
        "live_column": "NEKO tiny observation",
        "shape": "soft_observation",
        "fun_axis": "mood",
        "title": "\u5c4f\u5e55\u95ea\u4e00\u4e0b\u4e5f\u50cf\u5728\u548c\u732b\u732b\u70b9\u5934",
        "hint": "Make one tiny visual observation; do not turn it into a question.",
        "reply_affordance": "viewer can answer with one tiny scene or mood",
    },
    {
        "key": "idle:quiet-stamp",
        "live_column": "NEKO tiny stamp",
        "shape": "small_mood",
        "fun_axis": "mood",
        "title": "\u732b\u732b\u7ed9\u7a7a\u6c14\u76d6\u4e00\u679a\u5b89\u9759\u7ae0",
        "hint": "Make one tiny mood-stamp line; do not ask a broad question.",
        "reply_affordance": "viewer can answer with one stamp word",
    },
    {
        "key": "idle:corner-snack",
        "live_column": "NEKO tiny observation",
        "shape": "soft_observation",
        "fun_axis": "mood",
        "title": "\u5c4f\u5e55\u89d2\u843d\u50cf\u85cf\u4e86\u4e00\u9897\u5c0f\u9c7c\u5e72",
        "hint": "Make one small visual joke; keep it concrete and short.",
        "reply_affordance": "viewer can answer with one tiny scene",
    },
    {
        "key": "idle:air-purr",
        "live_column": "NEKO tiny radio",
        "shape": "soft_observation",
        "fun_axis": "mood",
        "title": "\u732b\u732b\u628a\u7a7a\u62cd\u542c\u6210\u5c0f\u547c\u565c",
        "hint": "Make one cozy radio-like line; keep it as a mood, not a question.",
        "reply_affordance": "viewer can answer with one cozy sound or mood",
    },
    {
        "key": "idle:half-open-drawer",
        "live_column": "NEKO room image",
        "shape": "small_mood",
        "fun_axis": "mood",
        "title": "\u6b64\u523b\u76f4\u64ad\u95f4\u50cf\u534a\u5f00\u7684\u62bd\u5c49",
        "hint": "Use this as one odd but concrete room image.",
        "reply_affordance": "viewer can answer with one object or mood word",
    },
)
