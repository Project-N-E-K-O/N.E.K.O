import asyncio
from unittest.mock import AsyncMock
import pytest
from tests.unit.test_session_handoff_lifecycle import make_manager
from main_logic.core import LLMSessionManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

async def test_unsafe_retirement_reports_failure_to_all_waiters(monkeypatch):
    manager = make_manager()
    manager._init_session_lifecycle_state()
    client = manager.session
    client.allow_close.set()
    async def fail(**kwargs):
        raise RuntimeError('controlled isolation failure')
    monkeypatch.setattr(manager, '_close_independent_asr', fail)
    ending = manager.request_end_session(by_server=True)
    with pytest.raises(RuntimeError, match='handoff failed'):
        await asyncio.wait_for(asyncio.shield(ending), 2)
    record = manager._session_retirements[-1]
    assert record.handoff_finished.is_set()
    assert not record.handoff_safe.is_set()
    assert isinstance(record.handoff_error, RuntimeError)
    with pytest.raises(RuntimeError, match='handoff failed'):
        await manager._wait_session_end(ending)
    manager.send_session_failed = AsyncMock()
    await LLMSessionManager.start_session(manager, manager.websocket, request_id='failed-start')
    manager.send_session_failed.assert_awaited_once_with(
        'audio', request_id='failed-start', also_notify=manager.websocket,
    )
    async def settle():
        pass
    successor = manager.request_end_session(by_server=True, after_memory_settlement=settle)
    assert successor is not ending
    with pytest.raises(RuntimeError, match='Previous session handoff failed'):
        await asyncio.wait_for(asyncio.shield(successor), 2)
    assert record in manager._session_retirements
    assert manager._session_retirements[-1].handoff_finished.is_set()
    assert not manager._session_retirements[-1].handoff_safe.is_set()
