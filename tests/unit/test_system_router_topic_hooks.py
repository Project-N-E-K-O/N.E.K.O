from types import SimpleNamespace

from main_routers.system_router import (
    _allow_open_threads_for_topic_hooks,
    _render_followup_topic_hooks,
)


def test_topic_hooks_open_threads_respect_restricted_screen_only():
    restricted = SimpleNamespace(propensity="restricted_screen_only", unfinished_thread=None)
    restricted_with_thread = SimpleNamespace(
        propensity="restricted_screen_only",
        unfinished_thread={"text": "刚才没聊完的问题"},
    )
    normal = SimpleNamespace(propensity="open", unfinished_thread=None)

    assert _allow_open_threads_for_topic_hooks(None) is True
    assert _allow_open_threads_for_topic_hooks(normal) is True
    assert _allow_open_threads_for_topic_hooks(restricted) is False
    assert _allow_open_threads_for_topic_hooks(restricted_with_thread) is True


def test_followup_surfaced_ids_are_limited_to_rendered_topics():
    topics = [
        {
            "id": f"reflection-{idx}",
            "text": f"follow-up memory {idx}",
        }
        for idx in range(4)
    ]

    prompt, surfaced_ids = _render_followup_topic_hooks("en", topics)

    assert "follow-up memory 0" in prompt
    assert "follow-up memory 1" in prompt
    assert "follow-up memory 2" in prompt
    assert "follow-up memory 3" not in prompt
    assert surfaced_ids == [
        "reflection-0",
        "reflection-1",
        "reflection-2",
    ]
