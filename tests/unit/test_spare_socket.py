"""SpareSocket never leaves a socket nobody owns.

Worker-level tests cover the rules end to end; these pin two cancellation
races inside the component, where a spare that already holds a connected
socket loses its last reference.
"""

import asyncio

import pytest
from websockets.protocol import State

from main_logic.tts_client._preconnect import SpareSocket

pytestmark = pytest.mark.unit


class _Ws:
    def __init__(self):
        self.closed = False

    @property
    def state(self):
        return State.CLOSED if self.closed else State.OPEN

    async def close(self):
        self.closed = True


def test_a_retired_spare_that_still_returns_its_socket_has_it_closed():
    # wait_for can hand back a result that raced the cancellation; the
    # retired task then finishes with a socket nobody adopts.
    async def scenario():
        spare = SpareSocket(connect=None, defer_control=lambda: False)
        ws = _Ws()

        async def finished_anyway():
            return ws, "session"

        spare._keep_until_done(asyncio.ensure_future(finished_anyway()))
        assert spare.closing
        await spare.drain(1.0)
        return ws, spare.closing

    ws, still_closing = asyncio.run(scenario())
    assert ws.closed
    assert not still_closing


@pytest.mark.parametrize("yields_before_cancel", [0, 1, 2, 3, 4])
def test_a_take_cancelled_around_the_spares_connect_leaves_no_socket_open(yields_before_cancel):
    async def scenario():
        gate = asyncio.Event()
        sockets = []

        async def connect():
            await gate.wait()
            ws = _Ws()
            sockets.append(ws)
            return ws

        spare = SpareSocket(connect=connect, defer_control=lambda: False)
        spare.start()
        taker = asyncio.ensure_future(spare.take())
        for _ in range(3):
            await asyncio.sleep(0)  # the take is now waiting on the connect
        gate.set()
        for _ in range(yields_before_cancel):
            await asyncio.sleep(0)
        taker.cancel()
        try:
            adopted = await taker
        except asyncio.CancelledError:
            adopted = None
        await spare.drain(1.0)
        spare.discard()
        await spare.drain(1.0)
        return adopted, sockets

    adopted, sockets = asyncio.run(scenario())
    for ws in sockets:
        if adopted is not None and ws is adopted[0]:
            continue  # the take won the race and handed it over
        assert ws.closed, "a connected spare was left open after its take was cancelled"


def test_audio_done_counts_as_emitted_only_once_it_is_on_the_queue():
    from main_logic.tts_client._infra import AudioDoneEmitter

    class _BrokenQueue:
        def __init__(self):
            self.puts = 0

        def put(self, _item):
            self.puts += 1
            raise RuntimeError("queue closed")

    broken = _BrokenQueue()
    emitter = AudioDoneEmitter(broken)
    emitter.emit("speech-1")
    emitter.emit("speech-1")
    assert not emitter.emitted("speech-1"), "a round whose end never reached core counts as finished"
    assert broken.puts == 1, "a failed delivery is not retried"

    delivered = []

    class _Queue:
        def put(self, item):
            delivered.append(item)

    ok = AudioDoneEmitter(_Queue())
    ok.emit("speech-2")
    assert ok.emitted("speech-2")
    ok.reset()
    assert not ok.emitted("speech-2")
