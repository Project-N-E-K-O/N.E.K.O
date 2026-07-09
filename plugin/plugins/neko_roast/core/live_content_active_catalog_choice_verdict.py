"""Active-engagement fallback choice topics for tiny verdict beats."""

from __future__ import annotations

from typing import Any


VERDICT_CHOICE_FALLBACK_TOPIC_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "source": "fallback",
        "key": "fallback:can-before-after",
        "live_column": "NEKO tiny verdict",
        "title": "\u732b\u732b\u5224\u65ad\uff1a\u73b0\u5728\u66f4\u50cf\u5f00\u7f50\u5934\u524d\u8fd8\u662f\u5f00\u7f50\u5934\u540e",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick before or after the can opens",
        "hint": "Make this a playful cat-life A/B choice.",
    },
    {
        "source": "fallback",
        "key": "fallback:desk-guardian",
        "live_column": "NEKO desk poll",
        "title": "\u684c\u9762\u5b88\u62a4\u795e\u4e8c\u9009\u4e00\uff1a\u6c34\u676f\u8fd8\u662f\u9f20\u6807",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick cup or mouse",
        "hint": "Offer one ordinary-object A/B choice with NEKO flavor.",
    },
    {
        "source": "fallback",
        "key": "fallback:doorplate",
        "live_column": "NEKO room sign",
        "title": "\u5982\u679c\u76f4\u64ad\u95f4\u6709\u95e8\u724c\uff1a\u732b\u7a9d\u8fd8\u662f\u5c0f\u5267\u573a",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick cat nest or tiny theater",
        "hint": "Make one doorplate A/B choice; keep both options concrete.",
    },
    {
        "source": "fallback",
        "key": "fallback:tsundere-choice",
        "live_column": "NEKO tiny verdict",
        "title": "\u732b\u732b\u5224\u65ad\uff1a\u4eca\u665a\u9002\u5408\u6492\u5a07\u8fd8\u662f\u5634\u786c",
        "fun_axis": "choice",
        "preferred_shape": "either_or",
        "reply_affordance": "viewer can pick cute or stubborn",
        "hint": "Turn this into one personality-flavored A/B choice.",
    },
)
