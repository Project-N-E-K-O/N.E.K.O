"""Prompt-copy snippets for active engagement reply shapes."""

from __future__ import annotations


def active_engagement_hook_text(shape: str, title: str) -> str:
    compact_title = title.strip() or "this live-room topic"
    return {
        "either_or": f"Make '{compact_title}' into one concrete A/B choice viewers can answer with one side.",
        "light_stance": f"Take one small, playful NEKO stance about '{compact_title}' so viewers can agree or push back.",
        "tiny_tease": f"Turn '{compact_title}' into one tiny playful tease, not a news recap or generic question.",
        "small_challenge": f"Turn '{compact_title}' into one tiny low-pressure challenge viewers can answer in a few words.",
    }.get(shape, f"Turn '{compact_title}' into one specific low-pressure hook viewers can answer quickly.")


def active_engagement_pattern_text(shape: str) -> str:
    return {
        "either_or": "two concrete sides, then let viewers pick one",
        "light_stance": "one tiny NEKO opinion, then leave room for pushback",
        "tiny_tease": "one small playful jab, then stop before it becomes a bit",
        "small_challenge": "one tiny challenge viewers can answer in a few words",
    }.get(shape, "one concrete reply point viewers can answer quickly")


def active_engagement_hint_text(shape: str) -> str:
    return {
        "either_or": "Make one tiny A/B choice; both sides must be concrete and easy to answer.",
        "light_stance": "Make one tiny NEKO stance; leave room for viewers to agree or push back.",
        "tiny_tease": "Make one tiny playful tease; stop before it becomes a bit.",
        "small_challenge": "Make one tiny low-pressure challenge viewers can answer in a few words.",
    }.get(shape, "Make one specific low-pressure hook viewers can answer quickly.")


def active_engagement_intent_text(shape: str) -> str:
    return {
        "either_or": "quick_vote",
        "light_stance": "agree_or_pushback",
        "tiny_tease": "tease_back",
        "small_challenge": "tiny_answer",
    }.get(shape, "quick_reply")


def active_engagement_fun_axis_text(shape: str) -> str:
    return {
        "either_or": "choice",
        "light_stance": "mood",
        "tiny_tease": "tease",
        "small_challenge": "micro_challenge",
    }.get(shape, "choice")


def active_engagement_reply_affordance_text(shape: str) -> str:
    return {
        "either_or": "viewer can answer with one side",
        "light_stance": "viewer can agree or push back",
        "tiny_tease": "viewer can tease NEKO back",
        "small_challenge": "viewer can answer in a few words",
    }.get(shape, "viewer can reply quickly")
