"""Static reply tactics for live-room prompt guidance."""

from __future__ import annotations

_TACTICS_BY_THEME: dict[str, tuple[str, str]] = {
    "question_help": (
        "answer_then_hook",
        "Answer the shared question first, then add one tiny follow-up angle.",
    ),
    "meme_play": (
        "catch_meme",
        "Catch the joke in one beat; do not explain it or stretch it into a lecture.",
    ),
    "praise_support": (
        "playful_thanks",
        "Acknowledge the praise playfully; do not thank every viewer one by one.",
    ),
    "negative_comfort": (
        "soft_defuse",
        "Acknowledge the concern briefly, then defuse without attacking viewers.",
    ),
    "recommend_tutorial": (
        "one_direction",
        "Group scattered requests and offer one concrete direction, not a full tutorial.",
    ),
    "greeting": (
        "batch_welcome",
        "Batch greetings into the current room mood; do not welcome everyone separately.",
    ),
    "small_chat": (
        "tiny_mood",
        "Mirror the shared mood in one short line without opening a new segment.",
    ),
}


def tactic_for_theme(theme_key: str) -> tuple[str, str]:
    key = str(theme_key or "").strip()
    if key.startswith("topic:"):
        return (
            "theme_bridge",
            "Reply to the shared point, then add one small expansion tied to the theme.",
        )
    return _TACTICS_BY_THEME.get(key, _TACTICS_BY_THEME["small_chat"])
