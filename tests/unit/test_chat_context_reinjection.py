"""Reproduction contracts for duplicate context on the chat-API path.

No live provider is called. Real preparation, final swap, callback bookkeeping,
and offline system-message assembly run with configuration/HTTP stubs. These
tests assert the repaired once-only behavior.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.omni_offline_client import OmniOfflineClient
from tests.unit.test_hot_swap_cancellation import (
    _FakeSession,
    _drain_task,
    _make_swap_manager,
)


class _ChatSession(OmniOfflineClient):
    """Keep real connect/prime_context; never construct an API client."""

    def __init__(self):
        self.model = "qwen3.7-plus"
        self.closed = False
        self.llm = object()
        self._conversation_history = []

    async def close(self):
        self.closed = True
        self.llm = None

    async def handle_messages(self):
        await asyncio.Event().wait()


def _manager(monkeypatch):
    mgr = _make_swap_manager()
    mgr.input_mode = "text"
    mgr.session = _FakeSession("old")
    mgr.pending_agent_callbacks = []
    mgr.voice_id = "test-voice"
    mgr.memory_server_port = 1
    mgr.is_preparing_new_session = True
    mgr.pending_session_warmed_up_event = asyncio.Event()
    mgr._config_manager = SimpleNamespace(
        aensure_region_resolved=AsyncMock(),
        aget_core_config=AsyncMock(return_value={"AUDIO_API_KEY": "unused"}),
        aget_model_api_config=AsyncMock(return_value={"model": "qwen3.7-plus"}),
        aget_character_data=AsyncMock(return_value=(None,) * 9),
        cleanup_invalid_voice_ids=MagicMock(return_value=(0, [])),
    )
    mgr._enqueue_voice_migration_notice = MagicMock()
    mgr._apply_voice_id_for_route = MagicMock()
    mgr._resolve_session_use_tts = MagicMock(return_value=False)
    mgr._register_builtin_tools = MagicMock()
    mgr.tool_registry = SimpleNamespace(all=lambda: [])
    mgr._get_text_guard_max_length = MagicMock(return_value=1024)
    mgr._build_initial_prompt = AsyncMock(return_value="SYSTEM\n")
    mgr._make_tool_call_handler = MagicMock(return_value=None)
    mgr._bind_session_lifecycle_callbacks = MagicMock()
    mgr._new_dialog_request_kwargs = lambda: {}
    pending = _ChatSession()
    mgr._create_offline_vlm_client = MagicMock(return_value=pending)
    monkeypatch.setattr(
        "main_logic.core.lifecycle.ensure_default_yui_voice_for_free_api",
        AsyncMock(),
    )
    return mgr, pending


async def _swap(mgr, pending):
    mgr.is_hot_swap_imminent = True
    await mgr._perform_final_swap_sequence()
    assert mgr.session is pending, "fixture must reach successful promotion"
    assert not pending.closed
    return pending._conversation_history[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival", ["before", "during", "after"])
async def test_chat_swap_injects_each_cache_entry_once(monkeypatch, arrival):
    """Only arrivals inside the memory HTTP await should expose the race."""
    mgr, pending = _manager(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    marker = "SCREEN_CACHE_UNIQUE_001"
    entry = {"role": mgr.lanlan_name, "text": marker}

    async def get(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(is_success=True, text="MEMORY\n")

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: SimpleNamespace(get=get),
    )
    if arrival == "before":
        mgr.message_cache_for_new_session.append(entry)
    prep = asyncio.create_task(mgr._background_prepare_pending_session())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if arrival == "during":
            mgr.message_cache_for_new_session.append(entry)
        release.set()
        await asyncio.wait_for(prep, 3)
        assert mgr.pending_session_warmed_up_event.is_set()
        assert mgr.pending_session is pending
        if arrival == "after":
            mgr.message_cache_for_new_session.append(entry)
        content = await _swap(mgr, pending)
        assert content.count(marker) == 1, content
    finally:
        release.set()
        await _drain_task(prep)
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_mode", ["passive", "proactive"])
async def test_consumed_chat_callback_is_not_reinjected_by_swap(monkeypatch, delivery_mode):
    """Ordinary chat consumes a callback; its fallback must not replay later."""
    mgr, pending = _manager(monkeypatch)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    marker = "SCREEN_CALLBACK_UNIQUE_002"
    mgr.enqueue_agent_callback({
        "origin": "event",
        "source_kind": "agent",
        "summary": marker,
        "delivery_mode": delivery_mode,
    })
    assert len(mgr.pending_agent_callbacks) == 1
    # Exercise selection, render, ack, and queue removal, not a hand-built mirror.
    rendered = mgr.drain_agent_callbacks_for_llm()
    assert marker in rendered
    assert not mgr.pending_agent_callbacks
    assert mgr.drain_agent_callbacks_for_llm() == ""
    await pending.connect("SYSTEM\n")
    mgr.pending_session = pending
    try:
        content = await _swap(mgr, pending)
        assert marker not in content, content
    finally:
        await _drain_task(mgr.message_handler_task)


def test_text_drain_preserves_legacy_extras(monkeypatch):
    mgr, _ = _manager(monkeypatch)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": "unique mixed queue notice", "delivery_mode": "proactive",
    })
    unrelated = {"_callback_delivery_id": "unrelated", "text": "keep"}
    mgr.pending_extra_replies.extend(["legacy text", unrelated])
    assert "unique mixed queue notice" in mgr.drain_agent_callbacks_for_llm()
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == ["legacy text", unrelated]


def test_text_drain_render_failure_keeps_mirror(monkeypatch):
    """A drain whose render raises hands nothing back to requeue; the mirror
    must survive so the hot swap can still deliver the notice."""
    mgr, _ = _manager(monkeypatch)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": "render failure notice", "delivery_mode": "proactive",
    })
    original_extras = list(mgr.pending_extra_replies)
    assert original_extras

    def fail_render(*_args, **_kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr(
        "main_logic.core.proactive._build_callback_instruction", fail_render,
    )
    with pytest.raises(RuntimeError):
        mgr.drain_agent_callbacks_for_llm()
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == original_extras


@pytest.mark.asyncio
async def test_callback_dequeued_during_media_await_is_not_requeued(monkeypatch):
    """Only callbacks this drain consumed are restored on a precommit failure;
    one another path delivered during the media await must stay gone."""
    from main_logic import core as core_module
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager,
        _make_offline_session_for_callback_media,
    )

    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    for summary in ("KEPT_BY_DRAIN", "TAKEN_ELSEWHERE"):
        mgr.enqueue_agent_callback({
            "origin": "event", "source_kind": "agent",
            "summary": summary, "delivery_mode": "proactive",
        })
    kept, taken = mgr.pending_agent_callbacks
    taken_id = taken["_callback_delivery_id"]
    kept_extras = [
        e for e in mgr.pending_extra_replies
        if e.get("_callback_delivery_id") != taken_id
    ]

    async def stage_and_deliver_elsewhere(_callbacks, _session):
        # Another path delivers TAKEN (both halves) inside the media await.
        mgr.pending_agent_callbacks = [
            cb for cb in mgr.pending_agent_callbacks if cb is not taken
        ]
        mgr.pending_extra_replies = [
            e for e in mgr.pending_extra_replies
            if e.get("_callback_delivery_id") != taken_id
        ]
        return {}

    mgr._stage_passive_callback_media = stage_and_deliver_elsewhere
    session.stream_text = AsyncMock(side_effect=RuntimeError("precommit"))
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *_: None)
    await mgr._process_stream_data_internal({"input_type": "text", "data": "hello"})
    assert mgr.pending_agent_callbacks == [kept]
    assert mgr.pending_extra_replies == kept_extras


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError, None])
async def test_plain_callback_survives_only_precommit_failure(
    monkeypatch, committed, failure,
):
    from main_logic import core as core_module
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager,
        _make_offline_session_for_callback_media,
    )

    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": "plain notice must survive", "delivery_mode": "proactive",
    })
    callback = mgr.pending_agent_callbacks[0]
    assert mgr.pending_extra_replies
    original_extras = list(mgr.pending_extra_replies)
    reached_stream = []

    async def fail_stream(_text, **kwargs):
        reached_stream.append(kwargs["system_prefix"])
        if committed:
            kwargs["on_turn_committed"]()
        if failure is not None:
            raise failure("interrupted")

    session.stream_text = AsyncMock(side_effect=fail_stream)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *_: None)
    try:
        await mgr._process_stream_data_internal({"input_type": "text", "data": "hello"})
    except asyncio.CancelledError:
        assert failure is asyncio.CancelledError
    assert len(reached_stream) == 1
    assert "plain notice must survive" in reached_stream[0]
    assert mgr.pending_extra_replies == ([] if committed else original_extras)
    assert mgr.pending_agent_callbacks == ([] if committed else [callback])
    if not committed:
        assert "plain notice must survive" in mgr.drain_agent_callbacks_for_llm()
        assert mgr.drain_agent_callbacks_for_llm() == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_mode", ["passive", "proactive"])
async def test_real_empty_turn_restores_callback_for_hot_swap(monkeypatch, delivery_mode):
    from main_logic import core as core_module
    from tests.unit.test_hot_swap_cancellation import _make_fake_realtime_session
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager,
        _make_offline_session_for_callback_media,
    )

    session = _make_offline_session_for_callback_media()
    session.stream_text = OmniOfflineClient.stream_text.__get__(session)
    mgr = _make_callback_media_manager(session)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    marker = "RESTORED_CALLBACK_SWAP_003"
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": marker, "delivery_mode": delivery_mode,
    })
    callback = mgr.pending_agent_callbacks[0]
    original_extras = list(mgr.pending_extra_replies)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *_: None)
    await mgr._process_stream_data_internal({"input_type": "text", "data": "   "})
    assert mgr.pending_agent_callbacks == [callback]
    assert mgr.pending_extra_replies == original_extras

    swap_mgr = _make_swap_manager()
    pending = _make_fake_realtime_session("restored-callback")
    swap_mgr.pending_agent_callbacks = mgr.pending_agent_callbacks
    swap_mgr.pending_extra_replies = mgr.pending_extra_replies
    swap_mgr.pending_session = pending
    swap_mgr.is_hot_swap_imminent = True
    try:
        await swap_mgr._perform_final_swap_sequence()
        assert swap_mgr.session is pending
        content = "\n".join(text for text, _ in pending.prime_calls)
        assert content.count(marker) == 1
        assert swap_mgr.pending_agent_callbacks == []
        assert swap_mgr.pending_extra_replies == []
        assert swap_mgr.drain_agent_callbacks_for_llm() == ""
        next_pending = _make_fake_realtime_session("second-swap")
        swap_mgr.pending_session = next_pending
        swap_mgr.is_hot_swap_imminent = True
        await swap_mgr._perform_final_swap_sequence()
        assert swap_mgr.session is next_pending
        assert all(marker not in text for text, _ in next_pending.prime_calls)
    finally:
        await _drain_task(swap_mgr.message_handler_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("prime_fails", [False, True])
async def test_swap_consumes_only_original_successful_callback(monkeypatch, prime_fails):
    mgr, pending = _manager(monkeypatch)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    marker = "ORIGINAL_SWAP_NOTICE"
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": marker, "delivery_mode": "proactive",
    })
    original = mgr.pending_agent_callbacks[0]
    ack = asyncio.get_running_loop().create_future()
    from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY
    original[DELIVERY_ACK_FUTURE_KEY] = ack
    original_extras = list(mgr.pending_extra_replies)
    replacement = dict(original, summary="NEW_SAME_ID_NOTICE")
    replacement.pop(DELIVERY_ACK_FUTURE_KEY)
    unrelated = dict(replacement, summary="UNRELATED_NOTICE", _callback_delivery_id="other")
    await pending.connect("SYSTEM\n")
    real_prime = pending.prime_context

    async def prime(text, *, skipped=False):
        # New objects arrive while the old snapshot is in flight.
        mgr.pending_agent_callbacks.extend([replacement, unrelated])
        if prime_fails:
            raise RuntimeError("prime unavailable")
        await real_prime(text, skipped=skipped)

    pending.prime_context = prime
    mgr.pending_session = pending
    mgr.is_hot_swap_imminent = True
    try:
        await mgr._perform_final_swap_sequence()
        assert replacement in mgr.pending_agent_callbacks
        assert unrelated in mgr.pending_agent_callbacks
        if prime_fails:
            assert original in mgr.pending_agent_callbacks
            assert mgr.pending_extra_replies == original_extras
            assert not ack.done()
            assert marker in mgr.drain_agent_callbacks_for_llm()
        else:
            assert all(cb is not original for cb in mgr.pending_agent_callbacks)
            assert ack.result() is True
            rendered = mgr.drain_agent_callbacks_for_llm()
            assert marker not in rendered
            assert "NEW_SAME_ID_NOTICE" in rendered
            assert "UNRELATED_NOTICE" in rendered
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("requeue_at", ["during_prime", "after_swap"])
async def test_swap_consumes_callback_claimed_by_failing_text_turn(
    monkeypatch, requeue_at,
):
    """A text turn holds the callback while the swap primes its mirror, then
    fails before commit. Whenever its restore lands, the swap-delivered notice
    must not come back for the next text turn or hot swap."""
    from main_logic.proactive_delivery import SWAP_PRIME_DELIVERY_CLAIM_KEY

    mgr, pending = _manager(monkeypatch)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    marker = "CLAIMED_BY_TEXT_NOTICE"
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": marker, "delivery_mode": "proactive",
    })
    callback = mgr.pending_agent_callbacks[0]
    # The text turn has claimed it and is awaiting media staging.
    callback[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    await pending.connect("SYSTEM\n")
    real_prime = pending.prime_context
    text_turn = {}

    async def prime(text, *, skipped=False):
        # The text turn drains inside the prime await, then fails precommit.
        text_turn["extras"] = list(mgr.pending_extra_replies)
        assert marker in mgr.drain_agent_callbacks_for_llm([callback])
        if requeue_at == "during_prime":
            mgr._requeue_undelivered_callbacks([callback], text_turn["extras"])
        await real_prime(text, skipped=skipped)

    pending.prime_context = prime
    mgr.pending_session = pending
    mgr.is_hot_swap_imminent = True
    try:
        await mgr._perform_final_swap_sequence()
        assert mgr.session is pending
        assert marker in "\n".join(m.content for m in pending._conversation_history)
        if requeue_at == "after_swap":
            mgr._requeue_undelivered_callbacks([callback], text_turn["extras"])
        assert mgr.pending_agent_callbacks == []
        assert mgr.pending_extra_replies == []
        assert mgr.drain_agent_callbacks_for_llm() == ""
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
async def test_post_promote_cancel_leaves_text_turn_restore_intact(monkeypatch):
    """A swap cancelled after promote never delivers its prime, so a text turn
    that drained the callback inside the window must still restore it."""
    from main_logic.proactive_delivery import SWAP_PRIME_DELIVERY_CLAIM_KEY
    from tests.unit.test_hot_swap_cancellation import _run_swap_as_final_swap_task

    mgr, pending = _manager(monkeypatch)
    mgr._normalize_context_text_for_source = lambda _source, text: text
    marker = "CANCELLED_SWAP_NOTICE"
    mgr.enqueue_agent_callback({
        "origin": "event", "source_kind": "agent",
        "summary": marker, "delivery_mode": "proactive",
    })
    callback = mgr.pending_agent_callbacks[0]
    callback[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
    original_extras = list(mgr.pending_extra_replies)
    await pending.connect("SYSTEM\n")
    real_prime = pending.prime_context

    async def prime(text, *, skipped=False):
        assert marker in mgr.drain_agent_callbacks_for_llm([callback])
        await real_prime(text, skipped=skipped)

    async def cancelled_mid_post_promote(*_args, **_kwargs):
        asyncio.current_task().cancel()
        await asyncio.sleep(0)
        return 0

    pending.prime_context = prime
    mgr._prime_late_next_session_context_after_swap = cancelled_mid_post_promote
    mgr.pending_session = pending
    mgr.is_hot_swap_imminent = True
    try:
        await _run_swap_as_final_swap_task(mgr)
        assert mgr.session is pending, "fixture must cancel after promote"
        mgr._requeue_undelivered_callbacks([callback], original_extras)
        assert mgr.pending_agent_callbacks == [callback]
        assert mgr.pending_extra_replies == original_extras
    finally:
        await _drain_task(mgr.message_handler_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("split", [False, True])
async def test_final_swap_judges_the_increment_with_what_was_primed(monkeypatch, split):
    """The final swap primes only the increment, judged together with the
    snapshot primed at preparation (next-session context, then cache): a
    chain whose preceding user turn sits in that snapshot is still caught.
    ``split`` starts the chain in the primed cache snapshot, so it joins the
    increment only if the snapshot is replayed in prime order."""
    mgr, pending = _manager(monkeypatch)
    mgr.master_name = "Alice"
    comment_a = "屏幕搭话 蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。"
    comment_b = "屏幕搭话 远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。"

    async def get(*_args, **_kwargs):
        return SimpleNamespace(is_success=True, text="MEMORY\n")

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: SimpleNamespace(get=get),
    )
    mgr.next_session_context_messages = [{"role": "Alice", "text": "陪我聊聊"}]
    if split:
        mgr.message_cache_for_new_session = [{"role": mgr.lanlan_name, "text": comment_a}]
    prep = asyncio.create_task(mgr._background_prepare_pending_session())
    try:
        await asyncio.wait_for(prep, 3)
        assert mgr.pending_session_warmed_up_event.is_set()
        mgr.message_cache_for_new_session += [
            {"role": mgr.lanlan_name, "text": comment_b},
        ] if split else [
            {"role": mgr.lanlan_name, "text": comment_a},
            {"role": mgr.lanlan_name, "text": comment_b},
        ]
        content = await _swap(mgr, pending)
        # The second comment is left out either way.
        assert "红色小车" not in content, content
        if split:
            # comment_a went out at preparation, alone and unjudgeable.
            assert comment_a in content, content
        else:
            # Kept as the first comment, without its label.
            assert comment_a not in content, content
            assert comment_a[len("屏幕搭话 "):] in content, content
    finally:
        await _drain_task(prep)
        await _drain_task(mgr.message_handler_task)
