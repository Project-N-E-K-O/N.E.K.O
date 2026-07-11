"""Small text classifiers owned by the danmaku response path."""

from __future__ import annotations

from . import active_topic_mentions


def is_reaction_only(dense_lowered: str) -> bool:
    reaction_markers = (
        "\u54c8\u54c8",
        "\u7b11\u6b7b",
        "\u7ef7\u4e0d\u4f4f",
        "\u8349\u8349",
        "\u725b\u554a",
        "\u725b\u903c",
        "\u597d\u8036",
        "666",
        "lol",
        "lmao",
    )
    return len(dense_lowered) <= 8 and any(
        marker in dense_lowered for marker in reaction_markers
    )


def is_viewer_to_viewer_mention_text(text: str) -> bool:
    return active_topic_mentions.is_viewer_to_viewer_mention_text(text)
