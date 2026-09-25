"""A cancelled startup flush must not replay an input already accepted by LLM."""

import asyncio

import pytest

from main_logic import core as core_module
from tests.unit.test_core_game_route_memory_contract import (
    _make_callback_media_manager,
    _make_offline_session_for_callback_media,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_flush_restores_only_inputs_not_submitted(monkeypatch):
    session = _make_offline_session_for_callback_media()
    manager = _make_callback_media_manager(session)
    accepted = asyncio.Event()
    submissions = []

    async def stream_text(text, **kwargs):
        submissions.append(text)
        if callable(kwargs.get("on_turn_committed")):
            kwargs["on_turn_committed"]()
        accepted.set()
        await asyncio.Event().wait()

    session.stream_text = stream_text
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *args: None)
    first = {"input_type": "text", "data": "already submitted", "request_id": "first"}
    untouched = {"input_type": "text", "data": "not submitted", "request_id": "second"}
    manager.pending_input_data = [first, untouched]
    flushing = asyncio.create_task(manager._flush_pending_input_data())
    await asyncio.wait_for(accepted.wait(), 2)
    flushing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flushing
    assert submissions == ["already submitted"]
    assert manager.pending_input_data == [untouched]
