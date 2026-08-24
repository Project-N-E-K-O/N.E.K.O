"""Active-engagement fallback topic catalog entries for micro challenge."""

from __future__ import annotations

from typing import Any

MICRO_CHALLENGE_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:serious-cat",
        "live_column": "NEKO three-second challenge",
        "title": "\u732b\u732b\u8981\u5047\u88c5\u6b63\u7ecf\u4e09\u79d2",
        "fun_axis": "micro_challenge",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can judge whether NEKO passed the tiny challenge",
        "hint": "Make one small challenge viewers can react to in a few words.",
    },
    {
        "source": "fallback",
        "key": "fallback:three-word-task",
        "live_column": "NEKO three-word mission",
        "title": "\u7ed9\u732b\u732b\u4e00\u4e2a\u4e09\u5b57\u5c0f\u4efb\u52a1\uff1a\u5356\u840c\u3001\u5410\u69fd\u3001\u88c5\u4e56",
        "fun_axis": "micro_challenge",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can answer with one of three short choices",
        "hint": "Make one tiny challenge that viewers can answer with a short choice.",
    },
    {
        "source": "fallback",
        "key": "fallback:micro-mission-pose",
        "live_column": "NEKO three-second challenge",
        "title": "\u7ed9\u732b\u732b\u6307\u5b9a\u4e00\u4e2a\u4e09\u79d2\u5c0f\u59ff\u52bf",
        "fun_axis": "micro_challenge",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can reply with one tiny pose idea",
        "hint": "Ask for one tiny pose idea; avoid turning it into a segment.",
    },
    {
        "source": "fallback",
        "key": "fallback:reliable-three-sec",
        "live_column": "NEKO three-second challenge",
        "title": "\u4e09\u79d2\u5c0f\u4efb\u52a1\uff1a\u732b\u732b\u88c5\u4f5c\u5f88\u53ef\u9760",
        "fun_axis": "micro_challenge",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can judge reliable or not",
        "hint": "Make one tiny challenge; stop after the short hook.",
    },
    {
        "source": "fallback",
        "key": "fallback:self-compliment",
        "live_column": "NEKO three-second challenge",
        "title": "\u8ba9\u732b\u732b\u4e09\u79d2\u5185\u5938\u81ea\u5df1\u4e00\u6b21",
        "fun_axis": "micro_challenge",
        "preferred_shape": "small_challenge",
        "reply_affordance": "viewer can rate the self-compliment",
        "hint": "Make one short self-compliment challenge with room for teasing back.",
    },
)
