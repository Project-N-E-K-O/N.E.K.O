"""Validation for locally learned phrases that may be searched on the web."""

from dataclasses import dataclass

from .preference_model import may_search_phrase as may_search_phrase

LOCAL_ONLY_SCOPES = frozenset({"personal", "vision", "window", "chat", "mini_game"})

# Common public compounds that do not consistently appear as whole dictionary entries.
PUBLIC_TOPIC_PHRASES = frozenset(
    {
        "游泳",
        "蛙泳换气",
        "自由泳换气",
        "独立解谜",
        "独立解谜游戏",
        "回合制策略游戏",
        "桌游设计",
        "机械键盘",
        "夜景摄影",
        "天文摄影",
        "自由即兴",
        "breaststroke breathing",
        "indie puzzle games",
        "board game design",
        "mechanical keyboard",
        "night photography",
        "turn-based strategy",
    }
)


def is_public_topic(phrase: str, public_words: frozenset[str] = frozenset()) -> bool:
    # Match the complete phrase. Splitting it into familiar words cannot establish
    # that the combination is a public topic.
    return may_search_phrase(phrase) and (
        phrase in PUBLIC_TOPIC_PHRASES or phrase in public_words
    )


@dataclass(frozen=True)
class SearchPermission:
    public: bool
    game: bool = False
