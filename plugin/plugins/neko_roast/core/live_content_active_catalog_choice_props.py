"""Active-engagement fallback choice topics for props and titles."""

from __future__ import annotations

from typing import Any


PROP_CHOICE_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:desk-item-choice",
        "live_column": "NEKO desk poll",
        "title": "\u5982\u679c\u684c\u9762\u4e0a\u53ea\u80fd\u7559\u4e00\u6837\uff1a\u6c34\u676f\u8fd8\u662f\u96f6\u98df",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can choose one desk item",
        "hint": "Make one concrete desk-life A/B choice, not a broad chat topic.",
    },
    {
        "source": "fallback",
        "key": "fallback:cat-paw-button",
        "live_column": "NEKO cat-paw button",
        "title": "\u5982\u679c\u732b\u722a\u6709\u4e00\u4e2a\u6309\u94ae\uff1a\u5356\u840c\u8fd8\u662f\u5410\u69fd",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can choose the button mode",
        "hint": "Make one playful cat-paw A/B choice; stop after the hook.",
    },
    {
        "source": "fallback",
        "key": "fallback:cat-weather-forecast",
        "live_column": "NEKO weather vote",
        "title": "\u5982\u679c\u732b\u732b\u6709\u76f4\u64ad\u95f4\u5929\u6c14\u9884\u62a5\uff1a\u5fae\u98ce\u8fd8\u662f\u5c0f\u96ea",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick breeze or light snow",
        "hint": "Turn this into one cozy A/B forecast; no broad topic question.",
    },
    {
        "source": "fallback",
        "key": "fallback:night-title",
        "live_column": "NEKO tiny title",
        "title": "\u7ed9\u4eca\u665a\u7684\u732b\u732b\u9009\u4e00\u4e2a\u79f0\u53f7\uff1a\u5b88\u591c\u732b\u8fd8\u662f\u6478\u9c7c\u732b",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick one title",
        "hint": "Offer exactly two short titles; invite one quick pick.",
    },
)
