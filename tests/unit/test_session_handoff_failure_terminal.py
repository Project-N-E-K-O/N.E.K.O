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
    attempts = 0
    async def fail_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('controlled isolation failure')
    monkeypatch.setattr(manager, '_close_independent_asr', fail_once)
    ending = manager.request_end_session(by_server=True)
    record = manager._session_retirements[-1]
    with pytest.raises(RuntimeError, match='handoff failed'):
        await asyncio.wait_for(asyncio.shield(ending), 2)
    assert record.handoff_finished.is_set()
    assert record.handoff_safe.is_set()
    assert isinstance(record.handoff_error, RuntimeError)
    with pytest.raises(RuntimeError, match='handoff failed'):
        await manager._wait_session_end(ending)
    manager.send_session_failed = AsyncMock()
    manager._session_start_circuit_open = True
    await LLMSessionManager.start_session(manager, manager.websocket, request_id='failed-start')
    manager.send_session_failed.assert_not_awaited()
    successor = manager.request_end_session(by_server=True)
    assert successor is not ending
    await asyncio.wait_for(asyncio.shield(successor), 2)
    assert record not in manager._session_retirements
