"""Mixed downlink load through real temporary ZMQ sockets; no services started."""
from __future__ import annotations

import asyncio
import socket
import time

import pytest

from plugin.core.zmq_transport import ChildTransport, DownlinkCapacityError, HostTransport


@pytest.mark.asyncio
async def test_slow_receiver_bulk_rpc_and_stop_have_bounded_backlog():
    host = HostTransport()
    endpoints = [host.downlink_endpoint, host.uplink_endpoint, host.message_uplink_endpoint, host.image_uplink_endpoint]
    ports = [int(endpoint.rsplit(':', 1)[1]) for endpoint in endpoints]
    tasks = []
    child = None
    try:
        # No peer is connected yet, making the actual PUSH socket back up.
        for i in range(48):
            tasks.append(asyncio.create_task(host.send_command({
                'type': 'BUS_CHANGE', 'sub_id': str(i), 'bus': 'conversations',
                'op': 'record', 'delta': {'text': 'x' * 120_000},
            })))
        for i in range(4):
            tasks.append(asyncio.create_task(host.send_response({
                'type': 'PLUGIN_TO_PLUGIN_RESPONSE', 'request_id': f'r{i}',
                'result': {'text': 'm' * 900_000}, 'error': None,
            }, bounded=True)))
        await asyncio.sleep(.1)
        pending_bytes, pending_items = host._downlink_bytes, host._downlink_items
        assert 1 <= pending_items <= 128
        assert 1_000_000 <= pending_bytes <= 8 * 1024 * 1024
        assert sum(task.done() for task in tasks) >= 1  # overload must be explicit
        started = time.monotonic()
        stop = asyncio.create_task(host.send_command({'type': 'STOP'}))
        await asyncio.sleep(.02)
        child = ChildTransport(host.downlink_endpoint, host.uplink_endpoint, host.uplink_token,
                               message_uplink_endpoint=host.message_uplink_endpoint,
                               image_uplink_endpoint=host.image_uplink_endpoint)
        first = await asyncio.wait_for(child.recv_downlink(timeout_ms=2000), timeout=3)
        await asyncio.wait_for(stop, timeout=3)
        latency = time.monotonic() - started
        assert first[1]['type'] == 'STOP'
        assert latency < 3
        # Teardown with bulk still queued must terminate waiters and return credits.
        close_started = time.monotonic()
        host.close()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2)
        close_elapsed = time.monotonic() - close_started
        assert any(isinstance(item, DownlinkCapacityError) for item in results)
        assert host._downlink_items == host._downlink_bytes == 0
        assert close_elapsed < 2
        print(f'pending_items={pending_items} pending_bytes={pending_bytes} stop_seconds={latency:.3f} close_seconds={close_elapsed:.3f}')
    finally:
        host.close()
        if child is not None:
            child.close()
        await asyncio.gather(*tasks, return_exceptions=True)
    # Check the temporary listening endpoints have all been released.
    for port in ports:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))

@pytest.mark.asyncio
async def test_connected_slow_receiver_and_independent_host():
    import pickle
    import zmq
    import zmq.asyncio

    slow, fast = HostTransport(), HostTransport()
    addresses = [host.downlink_endpoint for host in (slow, fast)]
    all_endpoints = [getattr(host, attr) for host in (slow, fast) for attr in (
        'downlink_endpoint', 'uplink_endpoint', 'message_uplink_endpoint', 'image_uplink_endpoint')]
    ports = [int(endpoint.rsplit(':', 1)[1]) for endpoint in all_endpoints]
    context = zmq.asyncio.Context()
    sockets = [context.socket(zmq.PULL) for _ in addresses]
    tasks = []
    try:
        for sock, address in zip(sockets, addresses):
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVHWM, 1)
            sock.connect(address)
        await asyncio.wait_for(asyncio.gather(
            slow.send_command({'type': 'READY'}), fast.send_command({'type': 'READY'})), 3)
        await asyncio.wait_for(asyncio.gather(*(sock.recv() for sock in sockets)), 3)
        admission_started = time.monotonic()
        for i in range(70):
            tasks.append(asyncio.create_task(slow.send_command({
                'type': 'BUS_CHANGE', 'sub_id': str(i), 'bus': 'conversations',
                'op': 'record', 'delta': {'text': 'z' * 120_000},
            })))
        for i in range(4):
            tasks.append(asyncio.create_task(slow.send_response({
                'type': 'PLUGIN_TO_PLUGIN_RESPONSE', 'request_id': f'r{i}',
                'result': {'text': 'm' * 900_000}, 'error': None,
            }, bounded=True)))
        await asyncio.sleep(.15)
        admission_elapsed = time.monotonic() - admission_started
        bytes_held, items_held = slow._downlink_bytes, slow._downlink_items
        assert admission_elapsed < 2
        assert 0 < items_held <= 128 and 0 < bytes_held <= 8 * 1024 * 1024
        started = time.monotonic()
        stop = asyncio.create_task(slow.send_command({'type': 'STOP'}))
        fast_started = time.monotonic()
        await asyncio.wait_for(fast.send_command({'type': 'STOP'}), 2)
        fast_frame = pickle.loads(await asyncio.wait_for(sockets[1].recv(), 2))
        fast_latency = time.monotonic() - fast_started
        assert fast_frame[1]['type'] == 'STOP'
        ahead = 0
        while True:
            frame = pickle.loads(await asyncio.wait_for(sockets[0].recv(), 3))
            if frame[1].get('type') == 'STOP':
                break
            ahead += 1
            assert ahead < 30
        await asyncio.wait_for(stop, 2)
        latency = time.monotonic() - started
        assert latency < 3 and fast_latency < 2
        print(f'admission_seconds={admission_elapsed:.3f} connected_pending_items={items_held} '
              f'connected_pending_bytes={bytes_held} frames_before_stop={ahead} '
              f'slow_stop_seconds={latency:.3f} fast_stop_seconds={fast_latency:.3f}')
    finally:
        slow.close()
        fast.close()
        await asyncio.gather(*tasks, return_exceptions=True)
        for sock in sockets:
            sock.close(linger=0)
        context.term()
    for port in ports:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))


def test_bounded_validation_cpu_on_long_text():
    from plugin.core.zmq_transport import validate_bounded_downlink
    start = time.perf_counter()
    for _ in range(70):
        validate_bounded_downlink({'text': 'x' * 120_000})
    for _ in range(4):
        validate_bounded_downlink({'text': 'm' * 900_000})
    elapsed = time.perf_counter() - start
    print(f'long_text_validation_seconds={elapsed:.6f}')
    assert elapsed < 2
