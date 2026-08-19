"""Multi-message buffer summaries must be built from the real inbound messages,
not the bot's own draft replies.

``schedule_reply`` overwrites ``buffered_texts[0]`` with the bot's draft
(reply_buffer_service.schedule_reply), while the real inbound text only lives
in ``buffered_user_texts``. When a multi-message buffer runs
``_deliver_after_wait`` to build the summary, feeding ``buffered_texts`` into
the prompt would pass the cat girl's own draft off as a message from the other
side.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugin.plugins.qq_auto_reply.reply_buffer_service import (
    PendingReply,
    QQReplyBufferService,
)


def _make_plugin():
    plugin = SimpleNamespace(
        _emit_log=lambda _level, _msg: None,
        _maybe_push_status_event=lambda: None,
        reply_pipeline=SimpleNamespace(run=AsyncMock()),
        session_memory_service=SimpleNamespace(
            record_synthetic_prompt_rows=lambda *a, **k: 0,
            session_history_len=lambda _key: 0,
        ),
        _user_sessions={},
    )

    async def _run_with_session_lock(_key, func):
        return await func()

    plugin._run_with_session_lock = _run_with_session_lock
    return plugin


@pytest.mark.asyncio
async def test_buffer_summary_uses_user_texts_not_bot_draft():
    """A multi-message summary prompt must be built from buffered_user_texts,
    with no bot draft."""
    plugin = _make_plugin()
    service = QQReplyBufferService(plugin)
    session_key = "g|123|user_1"

    # Production shape: schedule_reply overwrote buffered_texts[0] with the bot
    # draft while real inbound text stays in buffered_user_texts; pre_buffer
    # appends later messages to both lists.
    pending = PendingReply(
        first_text="(overwritten placeholder)",
        wait_seconds=0.0,
        sender_id="user_1",
        is_group=True,
        group_id="123",
    )
    pending.wait_until = time.time() - 1  # wait already elapsed, skip sleep
    pending.buffered_texts = ["(cat girl's draft reply)", "second original message"]
    pending.buffered_user_texts = ["first original message", "second original message"]
    pending.message_count = 2
    service._pending[session_key] = pending

    await service._deliver_after_wait(session_key, pending, 0)

    request = plugin.reply_pipeline.run.await_args.args[0]
    text = request.message_text
    assert "first original message" in text
    assert "second original message" in text
    assert "cat girl's draft reply" not in text


@pytest.mark.asyncio
async def test_buffer_summary_single_message_delivers_bot_reply():
    """A single-message buffer still delivers the bot draft (not echoing the
    original message back)."""
    plugin = _make_plugin()
    service = QQReplyBufferService(plugin)
    session_key = "g|123|user_1"

    pending = PendingReply(
        first_text="(overwritten placeholder)",
        wait_seconds=0.0,
        sender_id="user_1",
        is_group=True,
        group_id="123",
    )
    pending.wait_until = time.time() - 1
    pending.buffered_texts = ["(cat girl's draft reply)"]
    pending.buffered_user_texts = ["first original message"]
    pending.message_count = 1
    # The single-message path goes through reply_delivery_node.deliver, not
    # reply_pipeline.run.
    delivered = {"ok": False}

    async def _deliver(plan, **kwargs):
        delivered["ok"] = True
        delivered["text"] = plan.blocks[0].text
        return SimpleNamespace(delivered=True)

    plugin.reply_delivery_node = SimpleNamespace(deliver=_deliver)
    plugin._spawn_memory_sync_task = lambda coro, session_key=None: coro
    service._pending[session_key] = pending

    await service._deliver_after_wait(session_key, pending, 0)

    assert delivered["ok"] is True
    assert delivered["text"] == "(cat girl's draft reply)"
