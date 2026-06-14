from types import SimpleNamespace
from pathlib import Path

from main_routers.system_router import _allow_open_threads_for_topic_hooks


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
    source = Path("main_routers/system_router.py").read_text(encoding="utf-8")

    assert "_rendered_followup_topics = _followup_topics[:3]" in source
    assert "followup_topics=_rendered_followup_topics" in source
    assert "for topic in _rendered_followup_topics:" in source
    assert "for topic in _followup_topics:\n                            if topic.get('id')" not in source
