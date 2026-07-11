# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for ``ActivityGuessGate`` — the activity_guess adaptive backoff.

Small base/cap/cache values keep the arithmetic obvious:
``base=10, cap=80`` → re-narrate intervals for a stable signature climb
10 → 20 → 40 → 80 (capped). ``cache=3`` makes LRU eviction easy to drive.
"""

import pytest

from main_logic.activity.activity_guess_gate import ActivityGuessGate


def _gate(base=10.0, cap=80.0, cache=3):
    return ActivityGuessGate(base_seconds=base, cap_seconds=cap, cache_size=cache)


def _fire_times(gate, sig, *, conv_seq, start, end, step):
    """Drive should_fire/record_fired across a clock and return the fire times."""
    fired = []
    now = start
    while now <= end:
        if gate.should_fire(sig, conv_seq, now):
            gate.record_fired(sig, conv_seq, now)
            fired.append(now)
        now += step
    return fired


def test_first_call_always_fires():
    gate = _gate()
    assert gate.should_fire('A', conv_seq=0, now=0.0) is True


def test_hard_floor_blocks_within_base_even_for_novel_sig():
    gate = _gate()
    assert gate.should_fire('A', 0, 0.0) is True
    gate.record_fired('A', 0, 0.0)
    # A brand-new signature 'B' would normally fire (novel), but the hard floor
    # forbids two calls closer than BASE.
    assert gate.should_fire('B', 0, 5.0) is False
    # Once BASE has elapsed, the novel signature fires.
    assert gate.should_fire('B', 0, 10.0) is True


def test_novel_signature_bypasses_grown_backoff():
    gate = _gate()
    # Let 'A' grow its backoff over several re-narrations.
    _fire_times(gate, 'A', conv_seq=0, start=0.0, end=200.0, step=5.0)
    # A genuinely different activity 'Z' must fire on the next floor-clear tick,
    # not wait out A's (now large) interval.
    now = 205.0
    assert gate.should_fire('Z', 0, now) is True


def test_same_signature_backoff_is_exponential_then_capped():
    gate = _gate(base=10.0, cap=80.0)
    fired = _fire_times(gate, 'A', conv_seq=0, start=0.0, end=400.0, step=5.0)
    # Intervals between consecutive fires: 10, 20, 40, 80, 80, ... (cap=80).
    intervals = [b - a for a, b in zip(fired, fired[1:])]
    assert intervals[:4] == [10.0, 20.0, 40.0, 80.0]
    assert all(iv == 80.0 for iv in intervals[4:])  # never exceeds the cap
    # Fire schedule: 0, 10, 30, 70, 150, 230, 310, 390.
    assert fired[:5] == [0.0, 10.0, 30.0, 70.0, 150.0]


def test_oscillation_between_two_sigs_decays():
    """The user's bug: flicking A<->B used to fire ~every BASE forever."""
    gate = _gate(base=10.0, cap=80.0)
    fired = []
    now = 0.0
    i = 0
    while now <= 600.0:
        sig = 'A' if i % 2 == 0 else 'B'
        if gate.should_fire(sig, 0, now):
            gate.record_fired(sig, 0, now)
            fired.append(now)
        now += 5.0
        i += 1
    # Old behaviour: a fire roughly every other 5s tick over 600s ≈ 60. The
    # backoff must cut that to a small bounded count and stretch the cadence.
    assert len(fired) < 30
    # Cadence clearly decays: fewer fires in a late window than in an early one.
    early = sum(1 for t in fired if t < 150.0)
    late = sum(1 for t in fired if t >= 450.0)
    assert late < early


def test_new_conversation_turn_resets_backoff():
    gate = _gate(base=10.0, cap=80.0)
    # Grow A's streak so its interval is large.
    _fire_times(gate, 'A', conv_seq=0, start=0.0, end=200.0, step=5.0)
    # A new conversation turn (conv_seq advances) is fresh context: it fires on
    # the next floor-clear tick and resets the backoff to BASE.
    assert gate.should_fire('A', conv_seq=1, now=205.0) is True
    gate.record_fired('A', conv_seq=1, now=205.0)
    # Streak reset → next re-narration of the same (still conv_seq=1) activity is
    # due again after just BASE, not the grown interval.
    assert gate.should_fire('A', conv_seq=1, now=215.0) is True


def test_lru_eviction_makes_old_signature_novel_again():
    gate = _gate(base=10.0, cap=80.0, cache=3)
    # Fire four distinct signatures, each novel, spaced past the floor.
    for k, sig in enumerate(['A', 'B', 'C', 'D']):
        t = k * 10.0
        assert gate.should_fire(sig, 0, t) is True
        gate.record_fired(sig, 0, t)
    # cache=3 → 'A' (oldest) was evicted when 'D' landed. It is therefore novel
    # again and fires immediately on the next floor-clear tick.
    assert gate.should_fire('A', 0, 40.0) is True


def test_cap_below_base_is_clamped():
    # A misconfigured cap < base must not make the interval shorter than base.
    gate = ActivityGuessGate(base_seconds=30.0, cap_seconds=5.0, cache_size=4)
    fired = _fire_times(gate, 'A', conv_seq=0, start=0.0, end=200.0, step=5.0)
    intervals = [b - a for a, b in zip(fired, fired[1:])]
    assert all(iv >= 30.0 for iv in intervals)


def test_rejects_nonpositive_base():
    with pytest.raises(ValueError):
        ActivityGuessGate(base_seconds=0.0, cap_seconds=600.0, cache_size=8)


def test_novel_sig_does_not_reset_other_sigs_backoff():
    """Per-signature streak: a one-off new activity must not re-open the backoff
    that a separate, still-oscillating signature has accumulated."""
    gate = _gate(base=10.0, cap=80.0, cache=8)
    # Grow 'A' to its CAP interval (80) via repeated re-narration.
    _fire_times(gate, 'A', conv_seq=0, start=0.0, end=400.0, step=5.0)
    # 'A' last fired at 390. A one-off NEW activity 'C' fires (past the floor).
    assert gate.should_fire('C', 0, 405.0) is True
    gate.record_fired('C', 0, 405.0)
    # 'A' must still be on its grown (CAP) interval — NOT reset to BASE. With a
    # single global streak, C's fire would have reset it and A would fire here.
    assert gate.should_fire('A', 0, 415.0) is False   # 415-390=25 < CAP(80)
    assert gate.should_fire('A', 0, 475.0) is True     # 475-390=85 >= 80


def test_cached_serves_per_signature_narration():
    """The narration is stored per signature, so a suppressed re-narration never
    leaves the consumer reading another activity's guess (Codex P2)."""
    gate = _gate()
    gate.record_fired('work', 0, 0.0, scores={'focused_work': 0.9}, guess='deep in code')
    gate.record_fired('chat', 0, 30.0, scores={'chatting': 0.8}, guess='chatting on IM')
    # 'chat' fired last, but asking for 'work' returns work's own narration.
    assert gate.cached('work') == ({'focused_work': 0.9}, 'deep in code')
    assert gate.cached('chat') == ({'chatting': 0.8}, 'chatting on IM')


def test_cached_unknown_signature_is_empty():
    gate = _gate()
    assert gate.cached('never-seen') == ({}, '')
    gate.record_fired('A', 0, 0.0, scores={'x': 1.0}, guess='g')
    # A different, not-yet-narrated signature stays empty (honest, not stale).
    assert gate.cached('B') == ({}, '')


def test_default_multiplier_is_two():
    """Omitting ``backoff_multiplier`` preserves the historical 2x schedule, so
    callers that predate the knob (and these tests' ``_gate`` helper) are
    unaffected."""
    gate = ActivityGuessGate(base_seconds=10.0, cap_seconds=80.0, cache_size=3)
    fired = _fire_times(gate, 'A', conv_seq=0, start=0.0, end=400.0, step=5.0)
    intervals = [b - a for a, b in zip(fired, fired[1:])]
    assert intervals[:4] == [10.0, 20.0, 40.0, 80.0]


def test_custom_multiplier_grows_faster_then_caps():
    """A larger multiplier reaches the cap in fewer re-narrations. base=10,
    mult=4, cap=160 → a stable signature's intervals climb 10 → 40 → 160 (capped),
    i.e. two re-narrations instead of four to settle at the floor."""
    gate = ActivityGuessGate(
        base_seconds=10.0, cap_seconds=160.0, cache_size=3, backoff_multiplier=4.0,
    )
    fired = _fire_times(gate, 'A', conv_seq=0, start=0.0, end=1000.0, step=1.0)
    intervals = [b - a for a, b in zip(fired, fired[1:])]
    assert intervals[:2] == [10.0, 40.0]
    assert all(iv == 160.0 for iv in intervals[2:])  # never exceeds the cap


def test_production_4x_900_schedule():
    """The user-tuned production schedule: base=30, mult=4, cap=900 → a stable
    activity decays 30 → 120 → 480 → 900 (capped) — to the floor in 3 steps."""
    gate = ActivityGuessGate(
        base_seconds=30.0, cap_seconds=900.0, cache_size=8, backoff_multiplier=4.0,
    )
    fired = _fire_times(gate, 'A', conv_seq=0, start=0.0, end=4000.0, step=1.0)
    intervals = [b - a for a, b in zip(fired, fired[1:])]
    assert intervals[:3] == [30.0, 120.0, 480.0]
    assert all(iv == 900.0 for iv in intervals[3:])


def test_multiplier_must_exceed_one():
    """A multiplier <= 1 would degrade the backoff to 'every BASE' (the original
    idle burn), so it is rejected at construction like a non-positive base;
    anything strictly above 1 is accepted. Asserting both sides locks the ``> 1``
    boundary so a mutation that wrongly tightens the check (e.g. ``< 2.0``,
    rejecting a legitimate 1.5) can't slip through."""
    for bad in (1.0, 0.5, 0.0, -2.0):
        with pytest.raises(ValueError):
            ActivityGuessGate(
                base_seconds=10.0, cap_seconds=80.0, cache_size=3,
                backoff_multiplier=bad,
            )
    # Just-above-boundary value must construct without raising.
    ActivityGuessGate(
        base_seconds=10.0, cap_seconds=80.0, cache_size=3,
        backoff_multiplier=1.0001,
    )


def test_new_conversation_freshness_is_per_signature():
    """A new turn must refresh EVERY cached activity when next visited, not just
    the first one re-narrated. With a single global last-fired conv seq, the
    first re-narration would consume the turn and leave the rest looking fresh.
    """
    gate = _gate(base=10.0, cap=80.0)
    # Grow 'B' to its CAP interval under conv_seq=0; last fire at 390.
    _fire_times(gate, 'B', conv_seq=0, start=0.0, end=400.0, step=5.0)
    # New conversation turn (0 -> 1); a DIFFERENT activity 'A' is narrated first.
    assert gate.should_fire('A', 1, 405.0) is True
    gate.record_fired('A', 1, 405.0)
    # 'B' was last narrated under conv_seq=0, so its guess predates the turn. It
    # must refresh on revisit even though 'A' already consumed the new turn and
    # B's own grown interval (CAP=80) has NOT elapsed (415 - 390 = 25 < 80).
    assert gate.should_fire('B', 1, 415.0) is True
