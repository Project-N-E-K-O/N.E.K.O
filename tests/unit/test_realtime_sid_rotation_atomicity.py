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
"""#2619: the speech-id rotation and the TTS-done ledger under cancellation.

``rotate_speech_id_for_response_done`` writes two TTS-done flags and then
rotates ``current_speech_id`` under ``self.lock``, and had no test of any kind.
The concern was that a cancellation between those writes leaves the host
saying "new turn" on the flags and "old turn" on the speech id.

What these tests pin:

* the rotation's contract, which nothing covered before;
* that the torn state is NOT reachable through the real lock, because no
  holder of ``self.lock`` suspends, so the acquire never yields and is not a
  cancellation point (the structural half of this lives in
  ``scripts/check_core_contracts.py::CORE_LOCK_NO_AWAIT``);
* what the residue WOULD be if that invariant were lost — the counterfactual
  is the reason the gate exists, so it is asserted rather than described;
* the window that was genuinely reachable: ``_clear_tts_pipeline`` sends
  ``__interrupt__`` and then sleeps 20ms, so a cancellation there used to
  leave an interrupted worker behind a ledger still claiming the done
  sentinel was queued — which makes the next turn's sentinel unsendable.
"""
from __future__ import annotations

import asyncio
from collections import deque

import pytest

import main_logic.core as core_module

pytestmark = pytest.mark.unit


class _FakeQueue:
    def __init__(self):
        self.messages = []

    def put(self, item):
        self.messages.append(item)

    def empty(self):
        return True

    def get_nowait(self):
        raise RuntimeError("empty")


class _FakeResampler:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class _FakeAliveThread:
    def is_alive(self):
        return True


class _NullAsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_manager(*, lock=None):
    """A manager carrying only what the two methods under test touch."""

    mgr = object.__new__(core_module.LLMSessionManager)
    mgr.lanlan_name = "Lan"
    mgr.lock = asyncio.Lock() if lock is None else lock
    mgr.tts_cache_lock = _NullAsyncLock()
    mgr.audio_resampler = _FakeResampler()
    mgr.current_speech_id = "sid-old"
    mgr._takeover_active = False
    mgr._tts_done_queued_for_turn = True
    mgr._tts_done_pending_until_ready = True
    mgr.tts_thread = None
    mgr.tts_ready = False
    mgr.tts_request_queue = _FakeQueue()
    mgr.tts_response_queue = _FakeQueue()
    mgr.tts_pending_chunks = []
    mgr._tts_stream_normalizer = core_module.TtsStreamNormalizer()
    mgr._tts_markdown_stripper = core_module.TtsMarkdownStripper()
    mgr._tts_bracket_stripper = core_module.TtsBracketStripper()
    mgr._tts_norm_speech_id = None
    mgr._tts_replay_done = False
    mgr._tts_replay_sent_chunks = deque()
    mgr._tts_replay_audio_emitted = False
    mgr._pending_ai_voice_echo_text = ""
    mgr._pending_ai_voice_echo_chunks = deque()
    mgr._confirmed_ai_voice_echo_audio_speech_ids = set()
    return mgr


_rotate = core_module.LLMSessionManager.rotate_speech_id_for_response_done
_clear_pipeline = core_module.LLMSessionManager._clear_tts_pipeline


def _turn_state(mgr):
    return (
        mgr.current_speech_id,
        mgr._tts_done_queued_for_turn,
        mgr._tts_done_pending_until_ready,
    )


# ── the rotation's contract (previously untested) ───────────────────────


@pytest.mark.asyncio
async def test_rotation_issues_a_new_sid_and_clears_the_done_ledger():
    mgr = _make_manager()

    await _rotate(mgr)

    assert mgr.current_speech_id != "sid-old"
    assert mgr._tts_done_queued_for_turn is False
    assert mgr._tts_done_pending_until_ready is False
    assert mgr.audio_resampler.cleared == 1


@pytest.mark.asyncio
async def test_rotation_is_suppressed_under_takeover():
    """Takeover owns the turn boundary; rotating under it would steal the sid."""

    mgr = _make_manager()
    mgr._takeover_active = True

    await _rotate(mgr)

    assert _turn_state(mgr) == ("sid-old", True, True)
    assert mgr.audio_resampler.cleared == 0


# ── atomicity through the real lock ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rotation_never_yields_so_no_task_can_observe_it_midway():
    """The acquire takes the uncontended fast path, so nothing interleaves.

    This is the load-bearing measurement: an observer task that runs on every
    event-loop pass must not get a single turn between the first write and the
    last, which is exactly why a cancellation cannot land between them.
    """

    mgr = _make_manager()
    observations = []

    async def observer():
        while True:
            observations.append(_turn_state(mgr))
            await asyncio.sleep(0)

    watcher = asyncio.create_task(observer())
    await asyncio.sleep(0)
    observations.clear()

    await _rotate(mgr)

    watcher.cancel()
    # Every sample is either the fully-old state or the fully-new one; a
    # sample with cleared flags and the old sid would be the #2619 tear.
    assert all(
        state == ("sid-old", True, True) or state == _turn_state(mgr)
        for state in observations
    ), observations


@pytest.mark.asyncio
async def test_cancelling_the_rotation_leaves_it_all_or_nothing():
    mgr = _make_manager()

    task = asyncio.create_task(_rotate(mgr))
    task.cancel()
    assert isinstance(
        (await asyncio.gather(task, return_exceptions=True))[0],
        asyncio.CancelledError,
    )

    # Cancelled before entry: nothing applied, and nothing half-applied.
    assert _turn_state(mgr) == ("sid-old", True, True)


@pytest.mark.asyncio
async def test_a_suspending_lock_holder_is_what_would_tear_the_rotation():
    """The counterfactual the CORE_LOCK_NO_AWAIT gate exists to prevent.

    Held by a suspending holder, the lock becomes contendable, the acquire
    becomes a real suspension point, and a cancellation there splits the
    write into exactly the state #2619 described: the flags say a fresh turn,
    the speech id still says the old one. Nothing in the package holds the
    lock this way today — the gate is what keeps that true.
    """

    mgr = _make_manager()

    await mgr.lock.acquire()  # stand in for a holder that awaits mid-section
    task = asyncio.create_task(_rotate(mgr))
    await asyncio.sleep(0)  # let it reach the contended acquire
    task.cancel()
    mgr.lock.release()
    assert isinstance(
        (await asyncio.gather(task, return_exceptions=True))[0],
        asyncio.CancelledError,
    )

    assert _turn_state(mgr) == ("sid-old", False, False)


# ── the window that was reachable: interrupt vs. the done ledger ────────


@pytest.mark.asyncio
async def test_pipeline_clear_resets_the_done_ledger_with_the_interrupt():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()

    await _clear_pipeline(mgr)

    assert mgr.tts_request_queue.messages == [("__interrupt__", None)]
    assert mgr._tts_done_queued_for_turn is False
    assert mgr._tts_done_pending_until_ready is False


async def _cancel_inside_the_interrupt_sleep(mgr):
    """Start the clear, cancel it during the 20ms wait, assert it got that far."""

    task = asyncio.create_task(_clear_pipeline(mgr))
    # Long enough to get past the interrupt and into the sleep, short enough
    # to stay inside its 20ms.
    await asyncio.sleep(0.005)
    task.cancel()
    assert isinstance(
        (await asyncio.gather(task, return_exceptions=True))[0],
        asyncio.CancelledError,
    )
    assert mgr.tts_request_queue.messages == [("__interrupt__", None)], (
        "test must cancel after the interrupt was queued, or it proves nothing"
    )


@pytest.mark.asyncio
async def test_cancelling_the_pipeline_clear_cannot_strand_the_done_ledger():
    """Regression: the interrupt and the queued-flag reset must land together.

    ``_clear_tts_pipeline`` sleeps 20ms waiting for the worker to act on
    ``__interrupt__``. A cancellation there used to leave the sentinel voided
    on the worker side but still recorded as queued on the manager side, so
    the next turn's ``_request_tts_done_locked`` short-circuits on "already"
    and its flush sentinel never reaches the worker at all.
    """

    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()

    await _cancel_inside_the_interrupt_sleep(mgr)

    assert mgr._tts_done_queued_for_turn is False


@pytest.mark.asyncio
async def test_cancelling_the_pipeline_clear_keeps_deferred_done_with_its_chunks():
    """The other flag must NOT be hoisted alongside — it is paired elsewhere.

    ``_tts_done_pending_until_ready`` means "these pending chunks still owe a
    done sentinel", so it belongs with ``tts_pending_chunks``, and the two are
    cleared together at the end of the clear. Resetting it early would tear
    the pair in the opposite direction: cancellation during the sleep would
    drop the flag while leaving the chunks, and once the worker reports ready
    ``_flush_tts_pending_chunks`` re-enqueues that text with no sentinel
    behind it, leaving the synthesizer unflushed.
    """

    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_pending_chunks = [("sid-old", "还没刷出去的文本")]
    mgr._tts_done_pending_until_ready = True

    await _cancel_inside_the_interrupt_sleep(mgr)

    assert mgr._tts_done_pending_until_ready is True
    assert mgr.tts_pending_chunks == [("sid-old", "还没刷出去的文本")]
