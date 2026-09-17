import asyncio

import pytest

from main_logic.omni_realtime_client._protocol_capabilities import LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter


@pytest.mark.asyncio
@pytest.mark.parametrize('source,item_type', [('proactive', 'message'), ('tool_result', 'function_call_output')])
async def test_gemini_item_reply_before_send_returns_releases_lane(source, item_type):
    sent = []
    async def send(event):
        sent.append(event['type'])
        assert arbiter.notify_response_content({'type': 'response.audio.delta', 'response_id': 'reply'})
        arbiter.notify_response_terminal({'type': 'response.done', 'response': {'id': 'reply', 'status': 'completed'}})
    arbiter = RealtimeResponseArbiter(send, protocol_capabilities=LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES)
    try:
        ticket = await arbiter.enqueue(source=source, events_before_response=({'type': 'conversation.item.create', 'item': {'type': item_type, 'role': 'user'}},), ack_expected=True)
        await asyncio.wait_for(ticket.done, .3)
        assert sent == ['conversation.item.create']
        await arbiter.wait_until_idle(timeout=.2)
    finally:
        await arbiter.shutdown()


@pytest.mark.asyncio
async def test_item_write_failure_releases_owner_for_retry():
    fail = True
    async def send(event):
        if fail:
            raise OSError('write failed')
        arbiter.notify_response_terminal({'type': 'response.done', 'response': {'id': 'retry', 'status': 'completed'}})
    arbiter = RealtimeResponseArbiter(send, protocol_capabilities=LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES)
    item = {'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user'}}
    try:
        ticket = await arbiter.enqueue(source='proactive', events_before_response=(item,))
        with pytest.raises(OSError, match='write failed'):
            await asyncio.wait_for(ticket.done, .2)
        fail = False
        retry = await arbiter.enqueue(source='proactive', events_before_response=(item,))
        await asyncio.wait_for(retry.done, .2)
    finally:
        await arbiter.shutdown()


@pytest.mark.asyncio
async def test_strict_route_still_sends_explicit_create():
    sent = []
    async def send(event):
        sent.append(event['type'])
        if event['type'] == 'response.create':
            arbiter.notify_response_created({'type': 'response.created', 'response': {'id': 'strict'}})
            arbiter.notify_response_terminal({'type': 'response.done', 'response': {'id': 'strict', 'status': 'completed'}})
    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(source='proactive', events_before_response=({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user'}},))
        await asyncio.wait_for(ticket.done, .2)
        assert sent == ['conversation.item.create', 'response.create']
    finally:
        await arbiter.shutdown()


@pytest.mark.asyncio
async def test_gemini_tool_call_with_different_terminal_id_does_not_stall_successor():
    sent = []
    async def send(event):
        sent.append(event['type'])
    arbiter = RealtimeResponseArbiter(send, protocol_capabilities=LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES)
    item = {'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user'}}
    try:
        ticket = await arbiter.enqueue(source='proactive', events_before_response=(item,), ack_expected=True)
        await asyncio.wait_for(ticket.sent, .2)
        assert not arbiter.notify_response_content({'type': 'response.function_call_arguments.done', 'response_id': 'function-id', 'call_id': 'call'})
        assert arbiter.notify_response_terminal({'type': 'response.done', 'response': {'id': 'terminal-id', 'status': 'completed'}})
        await asyncio.wait_for(ticket.done, .2)
        successor = await arbiter.enqueue(source='tool_result', events_before_response=(item,))
        await asyncio.wait_for(successor.sent, .2)
        arbiter.notify_response_terminal({'type': 'response.done', 'response': {'id': 'next-id', 'status': 'completed'}})
        await asyncio.wait_for(successor.done, .2)
        assert sent == ['conversation.item.create', 'conversation.item.create']
    finally:
        await arbiter.shutdown()
