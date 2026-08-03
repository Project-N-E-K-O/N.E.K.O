from __future__ import annotations

from main_logic.startup_greeting_policy import (
    _select_startup_followup,
    _select_startup_greeting_variant,
    _startup_greeting_burst_age,
)
from memory.startup_greeting_history import StartupGreetingRecord


def _record(variant: str, *, topic_key: str | None = None, ts: float = 1.0):
    return StartupGreetingRecord(
        ts=ts,
        text=f"opening for {variant}",
        variant_key=variant,
        topic_key=topic_key,
    )


def test_memory_followup_is_preferred_but_not_used_twice_in_a_row():
    assert _select_startup_greeting_variant([], has_followup=True) == "memory_followup"

    variant = _select_startup_greeting_variant(
        [_record("memory_followup", topic_key="ref_1")],
        has_followup=True,
    )
    assert variant == "recent_continuity"

    # The memory source itself stays on cooldown for the whole 24h record set,
    # even when a generic greeting happened after it.
    variant = _select_startup_greeting_variant(
        [
            _record("personal_share", ts=2.0),
            _record("memory_followup", topic_key="ref_1", ts=1.0),
        ],
        has_followup=True,
    )
    assert variant == "recent_continuity"


def test_generic_opening_angles_rotate_before_reuse():
    recent = [
        _record("personal_share", ts=2.0),
        _record("recent_continuity", ts=1.0),
    ]
    assert (
        _select_startup_greeting_variant(recent, has_followup=False) == "light_question"
    )

    exhausted = [
        _record("simple_presence", ts=4.0),
        _record("light_question", ts=3.0),
        _record("personal_share", ts=2.0),
        _record("recent_continuity", ts=1.0),
    ]
    assert (
        _select_startup_greeting_variant(exhausted, has_followup=False)
        == "recent_continuity"
    )


def test_followup_selection_skips_recent_sensitive_blank_and_malformed_topics():
    selected = _select_startup_followup(
        [
            None,
            {"id": "ref_used", "text": "继续上次的话题"},
            {"id": "ref_sensitive", "text": "敏感内容", "sensitive": True},
            {"id": "ref_private", "text": "隐私内容", "private": True},
            {"id": "ref_rejected", "text": "已拒绝话题", "rejected": True},
            {"id": "ref_blank", "text": "   "},
            {"id": "ref_ok", "text": "  下次继续聊那本书的结尾  "},
        ],
        recently_used_topic_keys={"ref_used"},
    )

    assert selected == ("ref_ok", "下次继续聊那本书的结尾")


def test_followup_topic_key_has_a_24h_history_identity():
    selected = _select_startup_followup(
        [{"id": "ref_same", "text": "仍然可用的话题"}],
        recently_used_topic_keys={"ref_same"},
    )
    assert selected is None


def test_real_user_engagement_ends_the_startup_burst():
    recent = [_record("simple_presence", ts=900.0)]

    assert (
        _startup_greeting_burst_age(
            recent, observed_at=1000.0, last_user_engagement_at=None
        )
        == 100.0
    )
    assert (
        _startup_greeting_burst_age(
            recent, observed_at=1000.0, last_user_engagement_at=950.0
        )
        is None
    )
    assert (
        _startup_greeting_burst_age(
            recent, observed_at=2701.0, last_user_engagement_at=None
        )
        is None
    )
