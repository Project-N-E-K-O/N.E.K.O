"""Cancellation at output-buffer and asynchronous completion boundaries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_proactive_vision_screenshot_staging import (
    _make_offline_for_ephemeral,
    _make_offline_for_stream,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mode", ["response", "proactive", "proactive_fallback"])
@pytest.mark.parametrize("interrupt", ["interrupt", "replace", "close", "none"])
async def test_ephemeral_completion_rechecks_generation_after_cleanup(mode, interrupt):
    """During the cleanup await an interruption claims the completion (the
    interrupter closes the turn) and a newer generation supersedes it; a
    session close takes nothing over, so the completion still runs, as on
    main."""
    client, set_chunks = _make_offline_for_ephemeral()
    set_chunks([SimpleNamespace(content="Hello")])
    client.on_proactive_done = AsyncMock() if mode != "proactive_fallback" else None

    async def cleanup(_owner):
        if interrupt == "interrupt":
            await client.handle_interruption()
        elif interrupt == "replace":
            client._begin_response_generation()
        elif interrupt == "close":
            client._cancel_response_generation()

    client._notify_reasoning_done = cleanup
    delivered = await client.prompt_ephemeral(
        "hello", completion_mode="response" if mode == "response" else "proactive",
        persist_response=False,
    )
    # The reply was fully delivered before the cleanup await, so it reports
    # True in every mode even when its completion is skipped; callers that
    # hand state to the completion check whether it was consumed.
    assert delivered is True
    expected = int(interrupt in ("none", "close"))
    assert client.on_response_done.await_count == (expected if mode != "proactive" else 0)
    if client.on_proactive_done is not None:
        assert client.on_proactive_done.await_count == (expected if mode == "proactive" else 0)


@pytest.mark.parametrize("ephemeral", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_cancel_drops_unemitted_prefix_but_normal_end_flushes(ephemeral, cancel):
    client, _ = (_make_offline_for_ephemeral() if ephemeral else _make_offline_for_stream())
    client._prefix_buffer_size = 100
    client.enable_response_guard = False
    client.master_name = "Master"
    client.lanlan_name = "Character"
    client.on_response_done = AsyncMock()
    client._check_repetition = AsyncMock()

    async def stream(_messages, **_overrides):
        yield SimpleNamespace(content="short")
        if cancel:
            await client.cancel_response()

    client._astream_visible_with_tools = stream
    if ephemeral:
        await client.prompt_ephemeral("hello", completion_mode="response")
    else:
        await client.stream_text("hello")
    texts = [call.args[0] for call in client.on_text_delta.await_args_list]
    assert texts == ([] if cancel else ["short"])
    replies = [m.content for m in client._conversation_history if getattr(m, "type", "") == "ai"]
    assert replies == ([] if cancel else ["short"])
    assert client.on_response_done.await_count == int(not cancel)


@pytest.mark.parametrize("interrupt", ["interrupt", "replace", "close", "none"])
async def test_normal_completion_rechecks_generation_after_status_await(interrupt):
    client, _ = _make_offline_for_stream()
    client.on_response_done = AsyncMock()

    async def status(_message):
        if interrupt == "interrupt":
            await client.handle_interruption()
        elif interrupt == "replace":
            client._begin_response_generation()
        elif interrupt == "close":
            client._cancel_response_generation()

    client.on_status_message = AsyncMock(side_effect=status)

    async def empty_stream(_messages, **_overrides):
        if False:
            yield

    client._astream_visible_with_tools = empty_stream
    await client.stream_text("hello")
    client.on_status_message.assert_awaited_once()
    assert client.on_response_done.await_count == int(interrupt in ("none", "close"))


@pytest.mark.parametrize("cancel", [False, True])
async def test_guard_pause_does_not_hide_cancellation_during_discard(cancel):
    client, _ = _make_offline_for_stream()
    client.master_name = "Master"
    client.lanlan_name = "Character"
    client._prefix_buffer_size = 3
    client.enable_response_guard = True
    client._recent_responses = []
    client.on_response_done = AsyncMock()

    async def discarded(*_args, **_kwargs):
        if cancel:
            await client.cancel_response()

    client._notify_response_discarded = AsyncMock(side_effect=discarded)

    async def stream(_messages, **_overrides):
        yield SimpleNamespace(content="Master | wrong speaker")

    client._astream_visible_with_tools = stream
    await client.stream_text("hello")
    client._notify_response_discarded.assert_awaited_once()
    assert client.on_response_done.await_count == int(not cancel)
