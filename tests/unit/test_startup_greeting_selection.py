from __future__ import annotations

from main_logic.startup_greeting_policy import (
    _STARTUP_GREETING_RECALL_SECONDS,
    _STARTUP_GREETING_STRICT_SECONDS,
    _select_startup_followup,
    _select_startup_greeting_variant,
    _startup_greeting_burst_age,
    split_startup_history_windows,
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


def test_recall_window_is_three_days_and_strict_window_is_one_day():
    """The two windows are the whole point of the feature; pin their sizes."""

    assert _STARTUP_GREETING_RECALL_SECONDS == 3 * 24 * 60 * 60
    assert _STARTUP_GREETING_STRICT_SECONDS == 24 * 60 * 60
    assert _STARTUP_GREETING_RECALL_SECONDS > _STARTUP_GREETING_STRICT_SECONDS


def test_recall_records_split_into_strict_and_earlier_layers():
    now = 10 * 24 * 60 * 60.0
    hour = 3600.0
    recall = [
        _record("simple_presence", ts=now - hour),
        _record("light_question", ts=now - _STARTUP_GREETING_STRICT_SECONDS + 1),
        # Exactly one day old is already outside the strict layer.
        _record("personal_share", ts=now - _STARTUP_GREETING_STRICT_SECONDS),
        _record("recent_continuity", ts=now - 2 * 24 * hour),
    ]

    strict, earlier = split_startup_history_windows(recall, observed_at=now)

    assert [item.variant_key for item in strict] == [
        "simple_presence",
        "light_question",
    ]
    assert [item.variant_key for item in earlier] == [
        "personal_share",
        "recent_continuity",
    ]


def test_future_timestamp_counts_as_strict_so_avoidance_only_tightens():
    """Splitting a future-dated record must tighten avoidance, not loosen it.

    In the production path ``StartupGreetingHistory.recent`` already drops
    records newer than ``now``, so this pins the splitter's own contract for
    any other caller rather than a reachable wall-clock-rollback scenario.
    """

    now = 1000.0
    strict, earlier = split_startup_history_windows(
        [_record("simple_presence", ts=now + 5000.0)],
        observed_at=now,
    )

    assert [item.variant_key for item in strict] == ["simple_presence"]
    assert earlier == []


def test_variant_rotation_reads_the_strict_layer_not_the_whole_recall():
    """Rotating against three days would exhaust every angle after day one."""

    now = 10 * 24 * 60 * 60.0
    recall = [
        _record("light_question", ts=now - 3600.0),
        # Older than a day: these must not count as "already used" for rotation.
        _record("recent_continuity", ts=now - 2 * 24 * 3600.0),
        _record("personal_share", ts=now - 2 * 24 * 3600.0 - 1),
        _record("simple_presence", ts=now - 2 * 24 * 3600.0 - 2),
    ]
    strict, _earlier = split_startup_history_windows(recall, observed_at=now)

    # Only light_question is inside the strict layer, so the first unused angle
    # in rotation order is still available.
    assert _select_startup_greeting_variant(strict, has_followup=False) == (
        "recent_continuity"
    )
    # Feeding the full recall set marks every angle used and collapses this into
    # the round-robin fallback, which picks the successor of the newest angle.
    assert _select_startup_greeting_variant(recall, has_followup=False) == (
        "simple_presence"
    )


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
