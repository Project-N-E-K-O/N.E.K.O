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
    assert len(fired) < 25
    intervals = [b - a for a, b in zip(fired, fired[1:])]
    # Late cadence has clearly backed off well above the BASE (10s) floor
    # (converges to ~25s combined for two capped signatures).
    assert intervals[-1] >= 20.0


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
    import pytest
    with pytest.raises(ValueError):
        ActivityGuessGate(base_seconds=0.0, cap_seconds=600.0, cache_size=8)
