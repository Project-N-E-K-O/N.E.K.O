"""Contract tests for session-scoped RitualMemory.

The design constraints come from the callback literature summarized in
`docs/live-effect-literature-research-report.md`:

- a one-off collective moment is a laugh, a signal the room *returns to* is an
  asset — so confirmation needs two separate windows, not one long burst;
- a callback needs a forgetting gap, otherwise it reads as repetition;
- the strongest callbacks recontextualize, so the same context blocks a repeat;
- running gags wear out, so each ritual retires after a fixed number of uses.

Everything here is session-scoped and bounded: no persistence, no timer, no
model call.
"""
from __future__ import annotations

import pytest

from plugin.plugins.neko_live.modules.live_events.ritual_memory import (
    RITUAL_CALLBACK_MIN_GAP_SECONDS,
    RITUAL_CONFIRM_MIN_GAP_SECONDS,
    RITUAL_MAX_TRACKED,
    RITUAL_MAX_USES,
    RITUAL_PROMPT_MAX_CHARS,
    RITUAL_STALE_SECONDS,
    RitualMemory,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture()
def clock() -> _Clock:
    return _Clock()


@pytest.fixture()
def memory(clock: _Clock) -> RitualMemory:
    return RitualMemory(now=clock)


def _confirm(memory: RitualMemory, clock: _Clock, phrase: str = "猫猫加油", support: int = 3) -> None:
    """Drive a phrase through the two-window confirmation path."""
    memory.observe_repeated_signal(kind="content", support=support, phrase=phrase)
    clock.advance(RITUAL_CONFIRM_MIN_GAP_SECONDS + 1)
    memory.observe_repeated_signal(kind="content", support=support, phrase=phrase)


# ── promotion ────────────────────────────────────────────────────────────

def test_single_collective_moment_is_not_a_ritual(memory, clock):
    memory.observe_repeated_signal(kind="content", support=5, phrase="猫猫加油")

    offer = memory.callback_for_context("small_talk")
    assert offer.text == ""
    assert offer.reason == "unconfirmed"


def test_one_long_burst_cannot_self_confirm(memory, clock):
    # Repeats inside the same window are one collective moment, not the room
    # coming back to the bit later.
    memory.observe_repeated_signal(kind="content", support=4, phrase="猫猫加油")
    clock.advance(RITUAL_CONFIRM_MIN_GAP_SECONDS - 5)
    memory.observe_repeated_signal(kind="content", support=6, phrase="猫猫加油")

    assert memory.callback_for_context("small_talk").text == ""


def test_second_window_confirms_a_ritual(memory, clock):
    _confirm(memory, clock)

    offer = memory.callback_for_context("small_talk")
    assert offer.reason == "offered"
    assert "猫猫加油" in offer.text
    assert len(offer.text) <= RITUAL_PROMPT_MAX_CHARS


def test_single_viewer_spam_never_promotes(memory, clock):
    # support < 2 means one person repeating themselves.
    memory.observe_repeated_signal(kind="content", support=1, phrase="刷屏")
    clock.advance(RITUAL_CONFIRM_MIN_GAP_SECONDS + 1)
    memory.observe_repeated_signal(kind="content", support=1, phrase="刷屏")

    assert memory.callback_for_context("small_talk").text == ""


def test_unknown_kind_is_ignored(memory, clock):
    assert memory.observe_repeated_signal(kind="", support=9, phrase="x") is False
    assert memory.observe_repeated_signal(kind="bogus", support=9, phrase="x") is False


def test_blank_phrase_is_ignored(memory):
    assert memory.observe_repeated_signal(kind="content", support=9, phrase="   ") is False


# ── callback gating ──────────────────────────────────────────────────────

def test_callback_needs_a_forgetting_gap(memory, clock):
    _confirm(memory, clock)
    first = memory.callback_for_context("small_talk")
    memory.mark_used(first.key, "small_talk")

    clock.advance(RITUAL_CALLBACK_MIN_GAP_SECONDS - 10)
    second = memory.callback_for_context("game_talk")

    assert second.text == ""
    assert second.reason == "too_soon"


def test_callback_must_recontextualize(memory, clock):
    _confirm(memory, clock)
    first = memory.callback_for_context("small_talk")
    memory.mark_used(first.key, "small_talk")
    clock.advance(RITUAL_CALLBACK_MIN_GAP_SECONDS + 10)

    same_context = memory.callback_for_context("small_talk")
    assert same_context.text == ""
    assert same_context.reason == "same_context"

    new_context = memory.callback_for_context("game_talk")
    assert new_context.reason == "offered"


def test_ritual_retires_after_max_uses(memory, clock):
    _confirm(memory, clock)
    used = 0
    for index in range(RITUAL_MAX_USES):
        if index:
            clock.advance(RITUAL_CALLBACK_MIN_GAP_SECONDS + 10)
        offer = memory.callback_for_context(f"ctx{index}")
        assert offer.reason == "offered"
        memory.mark_used(offer.key, f"ctx{index}")
        used += 1

    assert used == RITUAL_MAX_USES
    clock.advance(RITUAL_CALLBACK_MIN_GAP_SECONDS + 10)
    assert memory.callback_for_context("fresh_context").reason == "retired"


def test_ritual_expires_when_the_room_stops_echoing_it(memory, clock):
    # Expiry keys off the ROOM's last echo, not NEKO's last payoff: a bit the
    # room has abandoned should die rather than be flogged by the cat alone.
    _confirm(memory, clock)
    offer = memory.callback_for_context("small_talk")
    memory.mark_used(offer.key, "small_talk")

    clock.advance(RITUAL_STALE_SECONDS + 1)

    assert memory.callback_for_context("game_talk").reason == "no_ritual"


def test_stale_ritual_is_forgotten(memory, clock):
    _confirm(memory, clock)
    clock.advance(RITUAL_STALE_SECONDS + 1)

    assert memory.callback_for_context("small_talk").reason == "no_ritual"
    assert memory.status()["ritual_tracked_count"] == 0


# ── bounds, reset, privacy ───────────────────────────────────────────────

def test_tracking_is_bounded(memory, clock):
    for index in range(RITUAL_MAX_TRACKED * 3):
        memory.observe_repeated_signal(
            kind="content", support=2, phrase=f"phrase{index}"
        )

    assert memory.status()["ritual_tracked_count"] <= RITUAL_MAX_TRACKED


def test_eviction_keeps_confirmed_over_fresh_candidates(memory, clock):
    _confirm(memory, clock, phrase="老梗")
    for index in range(RITUAL_MAX_TRACKED * 2):
        memory.observe_repeated_signal(
            kind="content", support=2, phrase=f"new{index}"
        )

    assert memory.is_confirmed_ritual("老梗") is True


def test_reset_clears_everything(memory, clock):
    _confirm(memory, clock)
    memory.reset()

    assert memory.status()["ritual_tracked_count"] == 0
    assert memory.callback_for_context("small_talk").reason == "no_ritual"
    assert memory.is_confirmed_ritual("猫猫加油") is False


def test_status_never_exposes_the_phrase(memory, clock):
    _confirm(memory, clock, phrase="私密弹幕内容")
    memory.callback_for_context("small_talk")

    rendered = str(memory.status())
    assert "私密弹幕内容" not in rendered
    assert memory.status()["ritual_confirmed_count"] == 1


def test_status_counts_offers_and_uses(memory, clock):
    _confirm(memory, clock)
    offer = memory.callback_for_context("small_talk")
    memory.mark_used(offer.key, "small_talk")

    status = memory.status()
    assert status["ritual_callback_offers"] == 1
    assert status["ritual_callback_uses"] == 1
    assert status["ritual_confirmed_count"] == 1


def test_mark_used_on_unknown_key_is_a_noop(memory):
    assert memory.mark_used("never-seen") is False


def test_is_confirmed_ritual_ignores_retired(memory, clock):
    _confirm(memory, clock)
    assert memory.is_confirmed_ritual("猫猫加油") is True
    for index in range(RITUAL_MAX_USES):
        offer = memory.callback_for_context(f"ctx{index}")
        memory.mark_used(offer.key, f"ctx{index}")
        clock.advance(RITUAL_CALLBACK_MIN_GAP_SECONDS + 10)

    assert memory.is_confirmed_ritual("猫猫加油") is False


def test_normalization_matches_spacing_and_case(memory, clock):
    memory.observe_repeated_signal(kind="content", support=3, phrase="Nya Nya")
    clock.advance(RITUAL_CONFIRM_MIN_GAP_SECONDS + 1)
    memory.observe_repeated_signal(kind="content", support=3, phrase="nyanya")

    assert memory.is_confirmed_ritual("NYANYA") is True


# ── end-to-end through RoomTopicContext ──────────────────────────────────

def _room_topic_with_clock(clock: _Clock):
    from plugin.plugins.neko_live.modules.live_events.room_topic import RoomTopicContext

    return RoomTopicContext(now=clock)


def test_repeated_signal_key_is_exposed_for_both_kinds(clock):
    from types import SimpleNamespace

    topic = _room_topic_with_clock(clock)
    for uid in ("1", "2", "3"):
        topic.remember_danmaku(
            uid=uid, nickname=f"v{uid}", text="草", score=1.0, ts=clock(),
        )
    selected = SimpleNamespace(uid="1", nickname="v1", danmaku_text="草")

    topic.prompt_projection_for_event(selected)
    kind, support, key = topic.last_repeated_signal()

    # A reaction-kind repeat carries no representative example, but must still
    # yield a normalized key so RitualMemory can track it.
    assert kind in {"reaction", "content"}
    assert support >= 2
    assert key


def test_last_repeated_signal_clears_on_reset(clock):
    from types import SimpleNamespace

    topic = _room_topic_with_clock(clock)
    for uid in ("1", "2"):
        topic.remember_danmaku(
            uid=uid, nickname=f"v{uid}", text="草", score=1.0, ts=clock(),
        )
    topic.prompt_projection_for_event(
        SimpleNamespace(uid="1", nickname="v1", danmaku_text="草")
    )
    topic.reset()

    assert topic.last_repeated_signal() == ("", 0, "")
    assert topic.dominant_theme_key() == ""
