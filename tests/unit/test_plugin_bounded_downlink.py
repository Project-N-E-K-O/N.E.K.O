from __future__ import annotations

import asyncio
import pickle
from collections import deque

import pytest
import zmq

from plugin.core import zmq_transport as zt
from plugin.server.messaging import request_router as rr


class Socket:
    def __init__(self, blocked=False):
        self.blocked = blocked
        self.sent = []
    async def send(self, data, flags):
        assert flags == zmq.DONTWAIT
        if self.blocked:
            raise zmq.Again()
        self.sent.append(pickle.loads(data))
    def close(self, **kwargs):
        pass


class Context:
    def term(self):
        pass


def transport(blocked=False):
    host = zt.HostTransport.__new__(zt.HostTransport)
    host._closed = False
    host._dl_sock = Socket(blocked)
    host._ul_sock = host._msg_sock = host._img_sock = Socket()
    host._ctx = Context()
    host._downlink_priority = deque()
    host._downlink_bulk = deque()
    host._downlink_bytes = host._downlink_items = 0
    host._downlink_drain_task = None
    host._downlink_owner_loop = None
    return host


async def spin():
    await asyncio.sleep(.02)


@pytest.mark.asyncio
async def test_new_read_limits_do_not_restrict_legacy_bus_payload_types():
    host = transport()
    try:
        legacy = {'type': 'BUS_CHANGE', 'bus': 'events', 'delta': {'raw': b'legacy'}}
        await host.send_command(legacy)
        assert host._dl_sock.sent[-1] == (zt.CH_CMD, legacy)
        with pytest.raises(zt.DownlinkCapacityError):
            await host.send_command({**legacy, 'bus': 'conversations'})
        assert host._downlink_items == host._downlink_bytes == 0
    finally:
        host.close()


@pytest.mark.asyncio
async def test_blocked_bulk_stop_priority_other_host_and_close_release():
    first, second = transport(True), transport()
    bulk = asyncio.create_task(first.send_response({'result': 'x' * 100_000}, bounded=True))
    await spin()
    assert first._downlink_items == 1 and first._downlink_bytes > 100_000
    stop = asyncio.create_task(first.send_command({'type': 'STOP'}))
    await asyncio.wait_for(second.send_command({'type': 'STOP'}), .2)
    assert second._dl_sock.sent[0][1]['type'] == 'STOP'
    first._dl_sock.blocked = False
    await asyncio.wait_for(asyncio.gather(stop, bulk), .2)
    assert [frame[0] for frame in first._dl_sock.sent] == [zt.CH_CMD, zt.CH_RESP]
    assert first._downlink_items == 0 and first._downlink_bytes == 0
    first._dl_sock.blocked = True
    pending = asyncio.create_task(first.send_response({'result': 'z' * 1000}, bounded=True))
    await spin()
    first.close()
    with pytest.raises(RuntimeError, match='closed'):
        await pending
    await asyncio.sleep(0)
    assert first._downlink_items == first._downlink_bytes == 0
    assert zt._downlink_global_bytes == zt._downlink_global_items == 0
    second.close()


@pytest.mark.asyncio
async def test_observation_eviction_and_bounded_response_busy(monkeypatch):
    monkeypatch.setattr(zt, '_DOWNLINK_HOST_ITEMS', 1)
    host = transport(True)
    one = asyncio.create_task(host.send_command({'type': 'BUS_CHANGE', 'bus': 'conversations', 'delta': {'n': 1}}))
    await spin()
    two = asyncio.create_task(host.send_command({'type': 'BUS_CHANGE', 'bus': 'conversations', 'delta': {'n': 2}}))
    await spin()
    with pytest.raises(zt.DownlinkCapacityError, match='evicted'):
        await one
    with pytest.raises(zt.DownlinkCapacityError, match='capacity'):
        await host.send_response({'result': 'large'}, bounded=True)
    assert host._downlink_items == 1
    two.cancel()
    with pytest.raises(asyncio.CancelledError):
        await two
    assert host._downlink_items == 0
    host.close()
    await asyncio.sleep(0)
    assert zt._downlink_global_bytes == zt._downlink_global_items == 0


@pytest.mark.asyncio
async def test_router_bounds_before_encoding_and_busy_is_explicit(monkeypatch):
    sent = []
    async def sender(msg):
        sent.append(msg.copy())
        if msg.get('_bounded_downlink'):
            raise zt.DownlinkCapacityError('full')
    monkeypatch.setattr(rr.state, 'get_downlink_sender', lambda _: sender)
    real_pack = rr.pack_rpc_payload
    calls = []
    def pack(value):
        calls.append(value)
        return real_pack(value)
    monkeypatch.setattr(rr, 'pack_rpc_payload', pack)
    router = rr.PluginRouter()
    reply = {'type': 'PLUGIN_TO_PLUGIN_RESPONSE', 'to_plugin': 'p', 'request_id': 'r', 'result': 'x' * (zt._DOWNLINK_FRAME_MAX + 1), 'error': None}
    await router._send_response(reply, bounded=True)
    assert calls[0]['result'] is None
    assert sent[-1]['error']['code'] == 'RESPONSE_TOO_LARGE'
    sent.clear()
    await router._send_response(dict(reply, result={'sections': []}), bounded=True)
    assert sent[0]['_bounded_downlink'] is True
    assert '_bounded_downlink' not in sent[1]
    assert sent[1]['error']['code'] == 'BUSY'
    await router.stop()


def test_bounded_tree_rejects_deep_and_non_json():
    value = None
    for _ in range(40):
        value = [value]
    with pytest.raises(zt.DownlinkCapacityError):
        zt.validate_bounded_downlink(value)
    with pytest.raises(zt.DownlinkCapacityError):
        zt.validate_bounded_downlink({'x': object()})

@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'error', 'again'])
async def test_inflight_cancel_and_eviction_never_remove_or_uncharge_neighbor(monkeypatch, outcome):
    monkeypatch.setattr(zt, '_DOWNLINK_HOST_ITEMS', 2)
    host = transport()
    entered, resume = asyncio.Event(), asyncio.Event()

    class YieldingSocket(Socket):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def send(self, data, flags):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                await resume.wait()
                if outcome == 'error':
                    raise OSError('injected send failure')
                if outcome == 'again':
                    raise zmq.Again()
            self.sent.append(pickle.loads(data))

    host._dl_sock = YieldingSocket()
    def event(n):
        return {'type': 'BUS_CHANGE', 'bus': 'conversations', 'sub_id': str(n), 'delta': {'text': 'payload'}}
    first = asyncio.create_task(host.send_command(event(1)))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(host.send_command(event(2)))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    # In-flight bytes remain charged despite the sender being cancelled.
    assert host._downlink_items == 2
    third = asyncio.create_task(host.send_command(event(3)))
    await asyncio.sleep(0)
    with pytest.raises(zt.DownlinkCapacityError, match='evicted'):
        await second
    assert host._downlink_items == 2
    resume.set()
    await asyncio.wait_for(third, 1)
    sent = [frame[1]['sub_id'] for frame in host._dl_sock.sent]
    assert sent == (['1', '3'] if outcome == 'success' else ['3'])
    assert host._downlink_items == host._downlink_bytes == 0
    host.close()
    await asyncio.sleep(0)
    assert zt._downlink_global_items == zt._downlink_global_bytes == 0
