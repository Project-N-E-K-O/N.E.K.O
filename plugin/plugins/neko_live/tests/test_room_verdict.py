"""Contract tests for the collective-answer ballot.

The plugin already knew how to ask ("A or B?") and how to recognise one short
reply as an answer; nothing counted what the room chose. RoomVerdict owns that
third step of the loop described in
`docs/live-effect-literature-research-report.md`: NEKO makes an event, a group
of viewers form a similar reaction, NEKO recognises the group reaction.

The rules under test keep a tally from lying: one viewer one vote, a verdict
needs a room rather than a person, a ballot expires, and a result is announced
at most once.
"""
from __future__ import annotations

import pytest

from plugin.plugins.neko_live.modules.live_events.room_verdict import (
    ROOM_VERDICT_MAX_OPTIONS,
    ROOM_VERDICT_MAX_VOTERS,
    ROOM_VERDICT_PROMPT_MAX_CHARS,
    ROOM_VERDICT_WINDOW_SECONDS,
    RoomVerdict,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 500.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture()
def clock() -> _Clock:
    return _Clock()


@pytest.fixture()
def verdict(clock: _Clock) -> RoomVerdict:
    return RoomVerdict(now=clock)


def _hook_result(shape: str = "either_or", source: str = "active_engagement", status: str = "pushed") -> dict:
    return {
        "status": status,
        "event": {
            "source": source,
            "live_mode": "solo_stream",
            "topic_shape": shape,
        },
    }


def _vote(verdict: RoomVerdict, uid: str, text: str) -> bool:
    return verdict.observe_answer(uid=uid, text=text)


def _open_confirmed(verdict: RoomVerdict, shape: str = "either_or") -> bool:
    """Exercise the tally without pretending plugin handoff was playback."""
    return verdict._open_confirmed_ballot(shape)


# ── ballot opening ───────────────────────────────────────────────────────

def test_choice_shaped_handoff_stays_closed_without_playback_confirmation(verdict):
    assert verdict.observe_result(_hook_result()) is False
    status = verdict.status()
    assert status["room_verdict_ballot_open"] is False
    assert status["room_verdict_ballots_opened"] == 0
    assert status["room_verdict_delivery_unconfirmed_count"] == 1
    assert status["room_verdict_last_reason"] == "delivery_unconfirmed"


def test_non_choice_beat_opens_nothing(verdict):
    # A beat that never asked must not turn later short danmaku into votes.
    assert verdict.observe_result(_hook_result(shape="soft_observation")) is False
    assert verdict.status()["room_verdict_ballot_open"] is False


def test_unpushed_result_opens_nothing(verdict):
    assert verdict.observe_result(_hook_result(status="skipped")) is False
    assert verdict.observe_result(_hook_result(status="dry_run")) is False


def test_viewer_result_opens_nothing(verdict):
    assert verdict.observe_result(_hook_result(source="live_danmaku")) is False


def test_unconfirmed_new_hook_closes_the_previous_ballot(verdict):
    _open_confirmed(verdict)
    _vote(verdict, "1", "a")
    _vote(verdict, "2", "a")

    verdict.observe_result(_hook_result(shape="tiny_choice"))

    status = verdict.status()
    assert status["room_verdict_ballot_open"] is False
    assert verdict.status()["room_verdict_current_voters"] == 0
    assert verdict.verdict_prompt().reason == "no_ballot"
    assert verdict.status()["room_verdict_last_reason"] == "delivery_unconfirmed"


# ── tallying ─────────────────────────────────────────────────────────────

def test_one_viewer_one_vote(verdict):
    _open_confirmed(verdict)

    assert _vote(verdict, "1", "a") is True
    # The same person shouting again must not manufacture a landslide.
    assert _vote(verdict, "1", "a") is False
    assert _vote(verdict, "1", "b") is False
    assert verdict.status()["room_verdict_current_voters"] == 1


def test_votes_ignore_spacing_and_case(verdict):
    _open_confirmed(verdict)
    _vote(verdict, "1", " A ")
    _vote(verdict, "2", "a")

    prompt = verdict.verdict_prompt()
    assert prompt.winner == "a"
    assert prompt.voters == 2


def test_long_replies_are_conversation_not_votes(verdict):
    _open_confirmed(verdict)
    assert _vote(verdict, "1", "我觉得这个问题要看情况的啦") is False
    assert verdict.status()["room_verdict_current_voters"] == 0


def test_blank_and_uidless_votes_are_ignored(verdict):
    _open_confirmed(verdict)
    assert _vote(verdict, "1", "   ") is False
    assert _vote(verdict, "", "a") is False


def test_votes_outside_an_open_ballot_are_ignored(verdict):
    assert _vote(verdict, "1", "a") is False


def test_option_count_is_bounded(verdict):
    _open_confirmed(verdict)
    for index in range(ROOM_VERDICT_MAX_OPTIONS + 4):
        _vote(verdict, f"uid{index}", f"o{index}")

    assert verdict.status()["room_verdict_current_options"] <= ROOM_VERDICT_MAX_OPTIONS


def test_voter_count_is_bounded(verdict):
    _open_confirmed(verdict)
    for index in range(ROOM_VERDICT_MAX_VOTERS + 10):
        _vote(verdict, f"uid{index}", "a")

    assert verdict.status()["room_verdict_current_voters"] <= ROOM_VERDICT_MAX_VOTERS


# ── verdict ──────────────────────────────────────────────────────────────

def test_one_answer_is_not_a_room_verdict(verdict):
    _open_confirmed(verdict)
    _vote(verdict, "1", "a")

    assert verdict.verdict_prompt().reason == "too_few_voters"


def test_decisive_verdict_is_announced(verdict):
    _open_confirmed(verdict)
    for uid in ("1", "2", "3"):
        _vote(verdict, uid, "b")

    prompt = verdict.verdict_prompt()
    assert prompt.reason == "announced"
    assert prompt.winner == "b"
    assert "b" in prompt.text
    assert len(prompt.text) <= ROOM_VERDICT_PROMPT_MAX_CHARS


def test_split_verdict_is_reported_as_split(verdict):
    _open_confirmed(verdict)
    _vote(verdict, "1", "a")
    _vote(verdict, "2", "b")

    prompt = verdict.verdict_prompt()
    assert prompt.reason == "announced"
    assert "分开" in prompt.text
    assert "打平" in prompt.text
    assert "暂时领先" not in prompt.text


def test_verdict_is_announced_at_most_once(verdict):
    _open_confirmed(verdict)
    for uid in ("1", "2", "3"):
        _vote(verdict, uid, "a")

    assert verdict.verdict_prompt().reason == "announced"
    # Reading the same result twice is the repetition anti-repeat exists to stop.
    assert verdict.verdict_prompt().reason == "already_announced"
    assert verdict.status()["room_verdict_announced_count"] == 1


def test_ballot_expires(verdict, clock):
    _open_confirmed(verdict)
    _vote(verdict, "1", "a")
    _vote(verdict, "2", "a")
    clock.advance(ROOM_VERDICT_WINDOW_SECONDS + 1)

    assert verdict.verdict_prompt().reason == "expired"
    assert verdict.status()["room_verdict_ballot_open"] is False


def test_votes_after_expiry_are_ignored(verdict, clock):
    _open_confirmed(verdict)
    clock.advance(ROOM_VERDICT_WINDOW_SECONDS + 1)

    assert _vote(verdict, "1", "a") is False


# ── ritual handoff ───────────────────────────────────────────────────────

def test_decisive_winner_is_offered_as_a_ritual_candidate(verdict):
    _open_confirmed(verdict)
    for uid in ("1", "2", "3"):
        _vote(verdict, uid, "小鱼干")

    assert verdict.winning_answer() == "小鱼干"


def test_split_room_produces_no_ritual_candidate(verdict):
    # A room that did not converge has not made a shared bit.
    _open_confirmed(verdict)
    _vote(verdict, "1", "a")
    _vote(verdict, "2", "b")

    assert verdict.winning_answer() == ""


def test_single_voter_produces_no_ritual_candidate(verdict):
    _open_confirmed(verdict)
    _vote(verdict, "1", "a")

    assert verdict.winning_answer() == ""


# ── projection ───────────────────────────────────────────────────────────

def test_status_never_exposes_answer_tokens(verdict):
    _open_confirmed(verdict)
    for uid in ("1", "2"):
        _vote(verdict, uid, "私密答案")
    verdict.verdict_prompt()

    assert "私密答案" not in str(verdict.status())


def test_reset_clears_the_ballot(verdict):
    _open_confirmed(verdict)
    verdict.observe_result(_hook_result())
    _vote(verdict, "1", "a")
    verdict.reset()

    status = verdict.status()
    assert status["room_verdict_ballot_open"] is False
    assert status["room_verdict_ballots_opened"] == 0
    assert status["room_verdict_delivery_unconfirmed_count"] == 0
    assert status["room_verdict_current_voters"] == 0


# ── combined prompt budget ───────────────────────────────────────────────

def test_combined_live_context_block_is_bounded():
    """All four collaborators can fire on the same turn; the total must stay
    capped, and a block that does not fit is dropped whole rather than cut."""
    from plugin.plugins.neko_live.modules.live_events.module import (
        LIVE_CONTEXT_PROMPT_MAX_CHARS,
        LiveEventsModule,
    )

    fit = LiveEventsModule._fit_context_blocks
    assert fit(("", "", "", "")) == ""

    big = "x" * 300
    combined = fit((big, big, big, big))
    assert len(combined) <= LIVE_CONTEXT_PROMPT_MAX_CHARS
    # Whole blocks only: the result is a concatenation of complete inputs.
    assert len(combined) % 300 == 0

    # The most time-sensitive block (room verdict) is added first, so it always
    # survives even when everything else is dropped.
    oversized = "y" * (LIVE_CONTEXT_PROMPT_MAX_CHARS - 1)
    assert fit((oversized, big, big, big)) == oversized
