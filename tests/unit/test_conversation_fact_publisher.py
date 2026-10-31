from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock
import pytest
from main_logic import agent_event_bus as bus


@pytest.mark.asyncio
async def test_fact_wire_and_failure_isolation(monkeypatch):
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(bus, 'publish_session_event_threadsafe', publish)
    assert await bus.publish_conversation_fact_observed_best_effort(
        'Lan', fact='input.submitted', payload={'input_id': 'i', 'text': 'hello'}
    )
    event = publish.await_args.args[0]
    assert event['event_type'] == 'conversation_fact_observed'
    assert event['lanlan_name'] == 'Lan' and event['fact'] == 'input.submitted'
    assert event['payload'] == {'input_id': 'i', 'text': 'hello'}
    assert event['event_id'] and isinstance(event['ts'], float)
    publish.side_effect = RuntimeError('offline')
    assert not await bus.publish_conversation_fact_observed_best_effort('Lan', fact='input.submitted', payload={})


@pytest.mark.asyncio
async def test_fact_scheduler_is_bounded(monkeypatch):
    gate = asyncio.Event()
    async def blocked(*args, **kwargs):
        await gate.wait()
        return True
    monkeypatch.setattr(bus, 'publish_conversation_fact_observed_best_effort', blocked)
    monkeypatch.setattr(bus, '_CONVERSATION_FACT_MAX_TASKS', 1)
    assert bus.schedule_conversation_fact_observed_best_effort('Lan', fact='input.submitted', payload={'text': 'hello'})
    assert not bus.schedule_conversation_fact_observed_best_effort('Lan', fact='input.submitted', payload={'text': 'next'})
    gate.set()
    await asyncio.gather(*tuple(bus._conversation_fact_tasks))
    await asyncio.sleep(0)
    assert not bus.schedule_conversation_fact_observed_best_effort('Lan', fact='input.submitted', payload={'text': 'x' * 65537})


def test_scheduler_without_running_loop_does_not_construct_coroutine(monkeypatch):
    from unittest.mock import Mock
    publish = Mock()
    monkeypatch.setattr(bus, "publish_conversation_fact_observed_best_effort", publish)
    assert not bus.schedule_conversation_fact_observed_best_effort("Lan", fact="reply.closed", payload={})
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_closes_coroutine_when_loop_rejects_task(monkeypatch):
    import inspect
    loop = asyncio.get_running_loop()
    async def emit():
        return None
    coroutine = emit()
    monkeypatch.setattr(bus, "publish_conversation_fact_observed_best_effort", lambda *args, **kwargs: coroutine)
    with monkeypatch.context() as patch:
        def reject(_coroutine):
            raise RuntimeError("closing loop")
        patch.setattr(loop, "create_task", reject)
        assert not bus.schedule_conversation_fact_observed_best_effort("Lan", fact="reply.closed", payload={})
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
