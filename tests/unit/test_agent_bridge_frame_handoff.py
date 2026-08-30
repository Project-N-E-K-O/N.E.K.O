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

"""The cross-thread handoff into the agent loop, and its bound.

``AgentServerEventBridge`` drains its sockets on a background thread and hands
each event to the agent's event loop with ``run_coroutine_threadsafe``. Every
such call queues a callback that retains the whole event -- for a provider
frame that is a multi-megabyte base64 string. Neither neighbouring limit covers
this: the PUB high-water mark bounds the sending side, and the plane bridge's
backlog check runs inside the coroutine, i.e. after the handoff. So while the
loop is delayed and frames keep arriving, this hop is where they pile up.

These tests pin the shape of the bound rather than a number: frames are shed
under pressure (they are lossy by contract), everything else on the same
sockets is not (dropping an ack or a lifecycle signal strands the sender), the
drop is counted and logged, and the hop recovers once the loop drains.
"""
from __future__ import annotations

import asyncio
import gc
import importlib.util
import threading
import time
import weakref

import orjson
import pytest

from main_logic import agent_event_bus as bus

pytestmark = pytest.mark.unit


class _Blob:
    """Stand-in for a frame's base64 payload, weak-referenceable.

    A plain ``str`` cannot be weak-referenced, and weak references are how the
    flood test asks the real question -- how many payloads is the loop still
    holding -- instead of the proxy question of how many futures we tracked.
    """

    __slots__ = ("data", "__weakref__")

    def __init__(self, size: int) -> None:
        self.data = "x" * size


def _frame(index: int, payload: object) -> dict:
    return {
        "event_type": bus.PROVIDER_FRAME_OBSERVED_EVENT,
        "event_id": f"f{index}",
        "lanlan_name": "Yui",
        "source": "test",
        "image_base64": payload,
    }


def _make_bridge(delivered: list, loop: asyncio.AbstractEventLoop):
    async def on_session_event(event: dict) -> None:
        delivered.append(event.get("event_id"))

    bridge = bus.AgentServerEventBridge(on_session_event=on_session_event)
    bridge._owner_loop = loop
    return bridge


async def _drain(bridge, delivered: list, expected: int, timeout: float = 5.0) -> None:
    """Let the stalled loop catch up; return as soon as it has."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(delivered) >= expected and not bridge._inflight_frames:
            return
        await asyncio.sleep(0.01)


async def test_frame_flood_while_loop_is_stalled_stays_bounded() -> None:
    """A flood of frames handed to a loop that never runs retains only the cap.

    The stall is real, not simulated: this coroutine floods the bridge without
    ever awaiting, so the loop cannot execute a single queued callback until the
    flood is over -- exactly the state a busy agent loop is in.
    """
    delivered: list = []
    bridge = _make_bridge(delivered, asyncio.get_running_loop())
    cap = bus.AGENT_FRAME_HANDOFF_MAX_IN_FLIGHT
    flood = cap * 25

    refs: list[weakref.ReferenceType] = []
    accepted = 0
    for i in range(flood):
        payload = _Blob(64 * 1024)
        refs.append(weakref.ref(payload))
        if bridge._submit_to_loop(_frame(i, payload)):
            accepted += 1
        del payload

    # Still inside the stall: the loop has had no chance to run anything.
    assert delivered == []

    gc.collect()
    alive = sum(1 for ref in refs if ref() is not None)
    # The headline claim: the loop is holding at most ``cap`` payloads, not
    # ``flood`` of them. Unbounded, this is ``flood`` -- 25x the ceiling.
    assert alive <= cap, f"loop retains {alive} frame payloads, cap is {cap}"

    assert accepted == cap
    assert len(bridge._inflight_frames) == cap
    assert bridge.frame_handoff_drops == flood - cap

    await _drain(bridge, delivered, cap)
    assert len(delivered) == cap
    assert bridge._inflight_frames == set()


async def test_non_frame_events_are_not_dropped_under_the_same_pressure() -> None:
    """Acks and lifecycle signals ride through a frame flood untouched.

    Same stall, same flood, but the small events interleaved into it must all
    arrive and in order. A bound that shed them would be the wrong bound: a
    dropped ``analyze_request`` leaves main_server waiting on an ack that is
    never coming.
    """
    delivered: list = []
    bridge = _make_bridge(delivered, asyncio.get_running_loop())
    cap = bus.AGENT_FRAME_HANDOFF_MAX_IN_FLIGHT
    flood = cap * 25

    expected_small: list[str] = []
    for i in range(flood):
        bridge._submit_to_loop(_frame(i, _Blob(64 * 1024)))
        if i % 5 == 0:
            event_id = f"small{i}"
            small = {
                "event_type": "analyze_request" if i % 10 == 0 else "agent_intent_restore_signal",
                "event_id": event_id,
                "lanlan_name": "Yui",
            }
            assert bridge._submit_to_loop(small) is True, "small event refused"
            expected_small.append(event_id)

    # The pressure has to be real, or this test proves nothing.
    assert bridge.frame_handoff_drops > 0
    assert len(bridge._inflight_frames) == cap

    await _drain(bridge, delivered, cap + len(expected_small))
    assert [d for d in delivered if d.startswith("small")] == expected_small


async def test_handoff_accepts_frames_again_once_the_loop_drains() -> None:
    """The cap is on in-flight frames, not a lifetime budget."""
    delivered: list = []
    bridge = _make_bridge(delivered, asyncio.get_running_loop())
    cap = bus.AGENT_FRAME_HANDOFF_MAX_IN_FLIGHT

    for i in range(cap * 3):
        bridge._submit_to_loop(_frame(i, _Blob(1024)))
    assert bridge.frame_handoff_drops == cap * 2

    await _drain(bridge, delivered, cap)
    assert bridge._inflight_frames == set()

    assert bridge._submit_to_loop(_frame(999, _Blob(1024))) is True
    await _drain(bridge, delivered, cap + 1)
    assert delivered[-1] == "f999"
    # No new drops while there was room.
    assert bridge.frame_handoff_drops == cap * 2


async def test_frame_drops_are_counted_always_and_logged_on_a_doubling_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every drop bumps the counter; the log stays legible without hiding scale.

    Two failure modes to keep out at once. A line per drop turns the burst into
    a second flood. A single line -- what a time window gives for a stall
    shorter than the window -- reports "1" and buries the real total.
    """
    warnings: list[tuple] = []

    def _capture(msg, *args, **kwargs):
        warnings.append((msg, args))

    monkeypatch.setattr(bus.logger, "warning", _capture)

    delivered: list = []
    bridge = _make_bridge(delivered, asyncio.get_running_loop())
    cap = bus.AGENT_FRAME_HANDOFF_MAX_IN_FLIGHT
    flood = cap * 25

    for i in range(flood):
        bridge._submit_to_loop(_frame(i, _Blob(1024)))

    dropped = flood - cap
    assert dropped >= 8, "flood too small to say anything about rate limiting"
    assert bridge.frame_handoff_drops == dropped

    # 1st, 2nd, 4th, 8th ... drop.
    expected_at = [n for n in (1 << k for k in range(dropped.bit_length())) if n <= dropped]
    reported = [args[-1] for _, args in warnings]
    assert reported == expected_at
    # Far fewer lines than drops, and the last one still shows the scale.
    assert len(warnings) < dropped // 4
    assert reported[-1] > dropped // 2

    await _drain(bridge, delivered, cap)


@pytest.mark.skipif(
    importlib.util.find_spec("zmq") is None, reason="pyzmq not installed",
)
async def test_sub_receive_thread_routes_through_the_bounded_handoff() -> None:
    """The real receive loop applies the bound, not just the helper.

    A bound the receive thread does not call is not a bound. This drives
    ``_recv_sub_fn`` itself on a background thread against a fake socket while
    the loop thread is blocked -- which is what the running artifact does.
    """
    import zmq

    delivered: list = []
    bridge = _make_bridge(delivered, asyncio.get_running_loop())
    cap = bus.AGENT_FRAME_HANDOFF_MAX_IN_FLIGHT
    flood = cap * 25

    pending = [
        orjson.dumps(_frame(i, "y" * (16 * 1024))) for i in range(flood)
    ]
    handed_out = threading.Lock()

    class _FakeSub:
        def recv(self):
            with handed_out:
                if pending:
                    return pending.pop(0)
            time.sleep(0.001)
            raise zmq.Again()

    bridge.sub = _FakeSub()
    thread = threading.Thread(target=bridge._recv_sub_fn, daemon=True)
    thread.start()
    try:
        # Block the loop thread -- this IS the stall the bound exists for, so
        # the blocking sleep is the test setup, not an oversight. Awaiting here
        # would let the loop drain and there would be no pressure to measure.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if bridge.frame_handoff_drops + len(bridge._inflight_frames) >= flood:
                break
            time.sleep(0.005)  # noqa: ASYNC251 - stalling the loop is the point
        assert len(bridge._inflight_frames) <= cap
        assert bridge.frame_handoff_drops == flood - cap
    finally:
        bridge._stop.set()
        thread.join(5.0)
    assert not thread.is_alive()

    await _drain(bridge, delivered, cap)
    assert len(delivered) == cap
