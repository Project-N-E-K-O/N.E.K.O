"""Idle-hosting beat catalog entries for viewer callback."""

from __future__ import annotations

from typing import Any

VIEWER_CALLBACK_IDLE_HOSTING_BEAT_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "key": "idle:one-word-call",
        "live_column": "NEKO one-word callback",
        "shape": "one_word_call",
        "fun_axis": "viewer_callback",
        "title": "\u7528\u4e00\u4e2a\u5b57\u7ed9\u73b0\u5728\u7684\u732b\u732b\u6253\u4e2a\u6807\u7b7e",
        "hint": "Ask for exactly one character or one word; make it playful, not needy.",
        "reply_affordance": "viewer can answer with one character or one word",
    },
    {
        "key": "idle:three-word-password",
        "live_column": "NEKO room password",
        "shape": "one_word_call",
        "fun_axis": "viewer_callback",
        "title": "\u7ed9\u4eca\u665a\u732b\u732b\u5c0f\u76f4\u64ad\u5b9a\u4e00\u4e2a\u4e09\u5b57\u6697\u53f7",
        "hint": "Ask for one three-character password; make it playful and easy.",
        "reply_affordance": "viewer can answer with three characters",
    },
    {
        "key": "idle:temperature-word",
        "live_column": "NEKO room thermometer",
        "shape": "one_word_call",
        "fun_axis": "viewer_callback",
        "title": "\u7528\u4e00\u4e2a\u8bcd\u7ed9\u73b0\u5728\u76f4\u64ad\u95f4\u6d4b\u4e2a\u6e29\u5ea6",
        "hint": "Ask for one word only; keep it like a tiny room-temperature check.",
        "reply_affordance": "viewer can answer with one temperature or mood word",
    },
    {
        "key": "idle:one-char-command",
        "live_column": "NEKO one-char command",
        "shape": "one_word_call",
        "fun_axis": "viewer_callback",
        "title": "\u7ed9\u732b\u732b\u4e00\u4e2a\u4e00\u5b57\u6307\u4ee4\uff1a\u4e56\u3001\u51f6\u3001\u56f0",
        "hint": "Ask for exactly one character from the offered mood choices.",
        "reply_affordance": "viewer can answer with one character",
    },
    {
        "key": "idle:light-filter-word",
        "live_column": "NEKO room filter",
        "shape": "one_word_call",
        "fun_axis": "viewer_callback",
        "title": "\u8ba9\u732b\u732b\u7528\u4e00\u4e2a\u8bcd\u5f62\u5bb9\u73b0\u5728\u7684\u706f\u5149",
        "hint": "Ask for one filter word only; make the reply path tiny.",
        "reply_affordance": "viewer can answer with one light or color word",
    },
)
