import asyncio
import inspect
from collections import OrderedDict
from types import SimpleNamespace

import pytest

import main_logic.core as core_module
from utils.llm_client import AIMessage, HumanMessage


def _make_manager():
    mgr = object.__new__(core_module.LLMSessionManager)
    mgr.lanlan_name = "Lan"
    mgr.master_name = "Master"
    mgr.session = None
    mgr.session_ready = True
    mgr.is_preparing_new_session = False
    mgr._require_context_append_current_delivery = False
    mgr.message_cache_for_new_session = []
    mgr.next_session_context_messages = []
    mgr.initial_next_session_context_snapshot_len = 0
    return mgr


class _FakePrimeSession:
    def __init__(self):
        self.calls = []

    async def prime_context(self, text, *, skipped=False):
        self.calls.append((text, skipped))


class _FakeHybridTextSession(_FakePrimeSession):
    def __init__(self):
        super().__init__()
        self._conversation_history = []


@pytest.mark.asyncio
async def test_append_context_adds_active_history_message():
    mgr = _make_manager()
    history = []
    active_session = SimpleNamespace(_conversation_history=history)
    mgr.session = active_session

    result = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="  tutorial finished  ",
        audience="model",
    )

    assert result.appended is True
    assert result.deduped is False
    assert result.targets == ("active_history",)
    assert isinstance(history[0], AIMessage)
    assert history[0].content == "tutorial finished"


@pytest.mark.asyncio
async def test_append_context_does_not_prime_text_session_after_history_append():
    mgr = _make_manager()
    session = _FakeHybridTextSession()
    mgr.session = session

    result = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="tutorial finished",
        audience="model",
    )

    assert result.appended is True
    assert result.targets == ("active_history",)
    assert len(session._conversation_history) == 1
    assert isinstance(session._conversation_history[0], AIMessage)
    assert session._conversation_history[0].content == "tutorial finished"
    assert session.calls == []


@pytest.mark.asyncio
async def test_append_context_primes_realtime_for_model_only_context():
    mgr = _make_manager()
    session = _FakePrimeSession()
    mgr.session = session

    result = await mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="score snapshot",
        audience="model",
    )

    assert result.appended is True
    assert result.targets == ("realtime_prime",)
    assert session.calls == [("score snapshot", True)]


@pytest.mark.asyncio
async def test_append_context_keeps_role_prefix_for_generic_realtime_context():
    mgr = _make_manager()
    session = _FakePrimeSession()
    mgr.session = session

    result = await mgr.append_context(
        source="proactive.context",
        role="system",
        text="background note",
        audience="model",
    )

    assert result.appended is True
    assert result.targets == ("realtime_prime",)
    assert session.calls == [("system: background note", True)]


@pytest.mark.asyncio
async def test_append_context_seeds_next_session_cache_when_preparing():
    mgr = _make_manager()
    mgr.is_preparing_new_session = True

    result = await mgr.append_context(
        source="game.icebreaker",
        role="user",
        text="choice A",
        audience="model",
        lifetime="session_family",
    )

    assert result.appended is True
    assert result.targets == ("new_session_cache",)
    assert mgr.next_session_context_messages == [{"role": "Master", "text": "choice A"}]
    assert mgr.message_cache_for_new_session == []


@pytest.mark.asyncio
async def test_append_context_requires_current_delivery_after_swap_promotion():
    mgr = _make_manager()
    mgr.is_preparing_new_session = True
    mgr._require_context_append_current_delivery = True

    class _FailingPrimeSession:
        async def prime_context(self, text, *, skipped=False):
            raise RuntimeError("prime unavailable")

    mgr.session = _FailingPrimeSession()

    result = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="post promote setup",
        audience="model",
        lifetime="session_family",
    )

    assert result.appended is False
    assert result.targets == ("new_session_cache",)
    assert result.reason == "realtime_prime_failed"
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "post promote setup"},
    ]


@pytest.mark.asyncio
async def test_append_context_next_session_context_survives_preparation_cache_reset():
    mgr = _make_manager()

    result = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="carry this tutorial note",
        audience="model",
        lifetime="next_session",
    )

    mgr.message_cache_for_new_session = []

    assert result.appended is True
    assert result.targets == ("new_session_cache",)
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "carry this tutorial note"},
    ]


@pytest.mark.asyncio
async def test_next_session_context_snapshot_consumes_only_rendered_prefix():
    mgr = _make_manager()

    await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="rendered in this start",
        lifetime="next_session",
    )
    snapshot = mgr._snapshot_next_session_context_messages()
    await mgr.append_context(
        source="game.icebreaker",
        role="user",
        text="queued during connect",
        lifetime="next_session",
    )

    mgr._consume_next_session_context_messages(len(snapshot))

    assert snapshot == [{"role": "Lan", "text": "rendered in this start"}]
    assert mgr.next_session_context_messages == [
        {"role": "Master", "text": "queued during connect"},
    ]


@pytest.mark.asyncio
async def test_final_swap_reset_preserves_unconsumed_next_session_context():
    mgr = _make_manager()
    mgr.background_preparation_task = None
    mgr.final_swap_task = None
    mgr.pending_session_warmed_up_event = object()
    mgr.pending_session_final_prime_complete_event = object()
    mgr.pending_use_tts = True
    mgr.next_session_context_messages = [{"role": "Lan", "text": "late context"}]
    mgr.message_cache_for_new_session = [{"role": "Master", "text": "main cache"}]
    mgr.initial_next_session_context_snapshot_len = 1

    await mgr._reset_preparation_state(clear_main_cache=True, from_final_swap=True)

    assert mgr.message_cache_for_new_session == []
    assert mgr.next_session_context_messages == [{"role": "Lan", "text": "late context"}]
    assert mgr.initial_next_session_context_snapshot_len == 0
    assert mgr.pending_session_warmed_up_event is None
    assert mgr.pending_use_tts is None


@pytest.mark.asyncio
async def test_append_context_preserves_system_role_in_next_session_cache():
    mgr = _make_manager()

    result = await mgr.append_context(
        source="topic.hook",
        role="system",
        text="keep this as system context",
        lifetime="next_session",
    )

    assert result.appended is True
    assert result.targets == ("new_session_cache",)
    assert mgr.next_session_context_messages == [
        {"role": "system", "text": "keep this as system context"},
    ]


@pytest.mark.asyncio
async def test_final_swap_primes_late_next_session_context_before_consuming():
    mgr = _make_manager()
    mgr.next_session_context_messages = [
        {"role": "Lan", "text": "already transferred"},
        {"role": "system", "text": "late before promote"},
    ]

    class _AppendingPrimeSession:
        def __init__(self):
            self.calls = []

        async def prime_context(self, text, *, skipped=False):
            self.calls.append((text, skipped))
            if len(self.calls) == 1:
                mgr.next_session_context_messages.append(
                    {"role": "Master", "text": "late during prime"}
                )

    session = _AppendingPrimeSession()
    mgr.session = session

    consumed = await mgr._prime_late_next_session_context_after_swap(1, 2)
    mgr._consume_next_session_context_messages(consumed)

    assert consumed == 2
    assert session.calls == [
        ("system | late before promote\n", True),
    ]
    assert mgr.next_session_context_messages == [
        {"role": "Master", "text": "late during prime"},
    ]


def test_final_swap_primes_late_context_before_flushing_cached_audio():
    source = inspect.getsource(core_module.LLMSessionManager._perform_final_swap_sequence)

    assert source.index("_prime_late_next_session_context_after_swap") < source.index(
        "_flush_hot_swap_audio_cache"
    )


def test_final_swap_consumes_next_session_context_after_cached_audio_flush():
    source = inspect.getsource(core_module.LLMSessionManager._perform_final_swap_sequence)

    assert source.index("_flush_hot_swap_audio_cache") < source.index(
        "_consume_next_session_context_messages(consumed_next_context_count)"
    )


@pytest.mark.asyncio
async def test_append_context_when_ready_flushes_before_user_input():
    mgr = _make_manager()
    mgr.session_ready = False

    queued = await mgr.append_context(
        source="proactive.context",
        role="system",
        text="queued context",
        audience="model",
        timing="when_ready",
        ordering_key="ctx-1",
    )

    assert queued.appended is True
    assert queued.targets == ("pending_ready",)

    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    mgr.session_ready = True
    await mgr._flush_pending_context_appends()

    assert len(history) == 1
    assert isinstance(history[0], HumanMessage)
    assert history[0].content == "system: queued context"


@pytest.mark.asyncio
async def test_append_context_when_ready_uses_ordering_key_for_flush_order():
    mgr = _make_manager()
    mgr.session_ready = False

    await mgr.append_context(
        source="topic.hook",
        role="system",
        text="second context",
        timing="when_ready",
        ordering_key="002",
    )
    await mgr.append_context(
        source="topic.hook",
        role="system",
        text="first context",
        timing="when_ready",
        ordering_key="001",
    )

    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    mgr.session_ready = True
    await mgr._flush_pending_context_appends()

    assert [message.content for message in history] == [
        "system: first context",
        "system: second context",
    ]


@pytest.mark.asyncio
async def test_pending_context_can_flush_before_session_ready_opens():
    mgr = _make_manager()
    mgr.session_ready = False

    await mgr.append_context(
        source="topic.hook",
        role="system",
        text="queued context",
        timing="when_ready",
        ordering_key="ctx",
    )

    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    await mgr._flush_pending_context_appends()

    assert mgr.session_ready is False
    assert [message.content for message in history] == ["system: queued context"]


@pytest.mark.asyncio
async def test_pending_context_drain_includes_items_queued_during_flush():
    mgr = _make_manager()
    mgr.session_ready = False
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)

    await mgr.append_context(
        source="topic.hook",
        role="system",
        text="first context",
        timing="when_ready",
        ordering_key="001",
    )

    original_append = mgr._append_context_to_targets
    queued_late = False

    async def append_and_queue(payload):
        nonlocal queued_late
        result = await original_append(payload)
        if not queued_late:
            queued_late = True
            await mgr.append_context(
                source="topic.hook",
                role="system",
                text="second context",
                timing="when_ready",
                ordering_key="002",
            )
        return result

    mgr._append_context_to_targets = append_and_queue
    await mgr._drain_pending_context_appends_before_ready()

    assert mgr.session_ready is False
    assert mgr.pending_context_appends == []
    assert [message.content for message in history] == [
        "system: first context",
        "system: second context",
    ]


@pytest.mark.asyncio
async def test_pending_context_drain_keeps_going_when_failed_pass_queues_new_item():
    mgr = _make_manager()
    mgr.session_ready = False
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)

    await mgr.append_context(
        source="topic.hook",
        role="system",
        text="stuck context",
        timing="when_ready",
        ordering_key="001",
    )

    original_append = mgr._append_context_to_targets
    queued_late = False

    async def fail_first_and_queue(payload):
        nonlocal queued_late
        if payload["text"] == "stuck context":
            if not queued_late:
                queued_late = True
                await mgr.append_context(
                    source="topic.hook",
                    role="system",
                    text="fresh context",
                    timing="when_ready",
                    ordering_key="002",
                )
            return core_module.ContextAppendResult(appended=False, reason="no_context_target")
        return await original_append(payload)

    mgr._append_context_to_targets = fail_first_and_queue
    await mgr._drain_pending_context_appends_before_ready()

    assert len(mgr.pending_context_appends) == 1
    assert mgr.pending_context_appends[0]["text"] == "stuck context"
    assert [message.content for message in history] == ["system: fresh context"]


@pytest.mark.asyncio
async def test_clear_pending_context_appends_drops_stale_ready_queue():
    mgr = _make_manager()
    mgr.session_ready = False

    await mgr.append_context(
        source="topic.hook",
        role="system",
        text="old session only",
        timing="when_ready",
        lifetime="current_session",
    )

    assert len(mgr.pending_context_appends) == 1

    mgr._clear_pending_context_appends()
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    await mgr._drain_pending_context_appends_before_ready()

    assert mgr.pending_context_appends == []
    assert history == []


@pytest.mark.asyncio
async def test_when_ready_durable_context_is_cached_before_readiness_flush():
    mgr = _make_manager()
    mgr.session_ready = False

    queued = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable setup",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-request",
    )

    assert queued.appended is True
    assert queued.targets == ("pending_ready",)
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "durable setup"},
    ]

    mgr._clear_pending_context_appends()
    duplicate = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable setup replay",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-request",
    )

    assert duplicate.appended is False
    assert duplicate.deduped is True
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "durable setup"},
    ]


@pytest.mark.asyncio
async def test_full_new_session_reset_releases_abandoned_durable_pending_dedup():
    mgr = _make_manager()
    mgr.session_ready = False

    queued = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="abandoned durable setup",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-request",
    )

    assert queued.appended is True
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "abandoned durable setup"},
    ]

    mgr.next_session_context_messages = []
    mgr._clear_pending_context_appends(release_durable_cached=True)
    retry = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="requeued durable setup",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-request",
    )

    assert retry.appended is True
    assert retry.deduped is False
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "requeued durable setup"},
    ]


@pytest.mark.asyncio
async def test_ready_durable_context_retry_recreates_cache_after_reset():
    mgr = _make_manager()

    class _FailingPrimeSession:
        async def prime_context(self, text, *, skipped=False):
            raise RuntimeError("prime unavailable")

    mgr.session = _FailingPrimeSession()
    failed = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable realtime setup",
        lifetime="session_family",
        request_id="durable-realtime",
    )

    assert failed.appended is False
    assert failed.reason == "realtime_prime_failed"
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "durable realtime setup"},
    ]

    mgr.next_session_context_messages = []
    mgr._clear_pending_context_appends(release_durable_cached=True)
    recovered_session = _FakePrimeSession()
    mgr.session = recovered_session

    retry = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable realtime setup",
        lifetime="session_family",
        request_id="durable-realtime",
    )

    assert retry.appended is True
    assert retry.deduped is False
    assert retry.targets == ("new_session_cache", "realtime_prime")
    assert recovered_session.calls == [("assistant: durable realtime setup", True)]
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "durable realtime setup"},
    ]


@pytest.mark.asyncio
async def test_when_ready_durable_context_retries_if_realtime_prime_fails():
    mgr = _make_manager()
    mgr.session_ready = False

    queued = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable realtime setup",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-realtime",
    )

    class _FailingPrimeSession:
        async def prime_context(self, text, *, skipped=False):
            raise RuntimeError("prime unavailable")

    mgr.session = _FailingPrimeSession()
    flushed = await mgr._flush_pending_context_appends()

    assert queued.appended is True
    assert flushed == 0
    assert len(mgr.pending_context_appends) == 1
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "durable realtime setup"},
    ]

    duplicate = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable realtime setup replay",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-realtime",
    )
    assert duplicate.appended is False
    assert duplicate.deduped is True

    recovered_session = _FakePrimeSession()
    mgr.session = recovered_session
    flushed = await mgr._flush_pending_context_appends()

    assert flushed == 1
    assert mgr.pending_context_appends == []
    assert recovered_session.calls == [("assistant: durable realtime setup", True)]
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "durable realtime setup"},
    ]


@pytest.mark.asyncio
async def test_when_ready_durable_context_delivered_in_start_prompt_is_not_replayed():
    mgr = _make_manager()
    mgr.session_ready = False

    queued = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="already in start prompt",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-start",
    )
    snapshot = mgr._snapshot_next_session_context_messages()
    mgr._mark_pending_context_appends_delivered_in_start_prompt(snapshot)

    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    await mgr._flush_pending_context_appends()

    assert queued.appended is True
    assert mgr.pending_context_appends == []
    assert history == []
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "already in start prompt"},
    ]

    mgr._consume_next_session_context_messages(len(snapshot))
    assert mgr.next_session_context_messages == []


@pytest.mark.asyncio
async def test_concurrent_loser_does_not_clear_winner_start_prompt_marks():
    mgr = _make_manager()
    mgr.session_ready = False

    queued = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="winner prompt context",
        timing="when_ready",
        lifetime="session_family",
        request_id="durable-winner",
    )
    snapshot = mgr._snapshot_next_session_context_messages()
    winner_owner = object()
    loser_owner = object()
    mgr._mark_pending_context_appends_delivered_in_start_prompt(
        snapshot,
        owner=winner_owner,
    )
    mgr._clear_pending_context_start_prompt_marks(owner=loser_owner)

    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    flushed = await mgr._flush_pending_context_appends()

    assert queued.appended is True
    assert flushed == 1
    assert mgr.pending_context_appends == []
    assert history == []
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "winner prompt context"},
    ]


@pytest.mark.asyncio
async def test_late_prime_failure_still_allows_transferred_prefix_consumption():
    mgr = _make_manager()
    mgr.next_session_context_messages = [
        {"role": "Lan", "text": "already transferred"},
        {"role": "system", "text": "late context"},
    ]

    class _FailingPrimeSession:
        async def prime_context(self, text, *, skipped=False):
            raise RuntimeError("provider rejected late prime")

    mgr.session = _FailingPrimeSession()

    consumed = await mgr._prime_late_next_session_context_after_swap(1, 2)
    mgr._consume_next_session_context_messages(consumed)

    assert consumed == 1
    assert mgr.next_session_context_messages == [
        {"role": "system", "text": "late context"},
    ]


@pytest.mark.asyncio
async def test_clear_pending_context_appends_releases_only_stale_request_ids():
    mgr = _make_manager()
    history = []
    active_session = SimpleNamespace(_conversation_history=history)
    mgr.session = active_session

    active = await mgr.append_context(
        source="topic.hook",
        role="system",
        text="already written",
        request_id="active-request",
    )
    mgr.session = None
    mgr.session_ready = False
    queued = await mgr.append_context(
        source="topic.hook",
        role="system",
        text="queued for old session",
        timing="when_ready",
        request_id="queued-request",
    )

    assert active.appended is True
    assert queued.appended is True

    mgr._clear_pending_context_appends()
    mgr.session = active_session
    mgr.session_ready = True
    retry_stale = await mgr.append_context(
        source="topic.hook",
        role="system",
        text="queued retry in new session",
        request_id="queued-request",
    )
    retry_active = await mgr.append_context(
        source="topic.hook",
        role="system",
        text="already written duplicate",
        request_id="active-request",
    )

    assert retry_stale.appended is True
    assert retry_active.appended is False
    assert retry_active.deduped is True
    assert [message.content for message in history] == [
        "system: already written",
        "system: queued retry in new session",
    ]


@pytest.mark.asyncio
async def test_current_session_request_id_can_replay_after_session_replaced():
    mgr = _make_manager()
    first_session = _FakePrimeSession()
    mgr.session = first_session

    first = await mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same request in first session",
        request_id="ctx-current",
    )
    duplicate = await mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same request duplicate",
        request_id="ctx-current",
    )

    second_session = _FakePrimeSession()
    mgr.session = second_session
    replay = await mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same request in replacement session",
        request_id="ctx-current",
    )

    assert first.appended is True
    assert duplicate.appended is False
    assert duplicate.deduped is True
    assert replay.appended is True
    assert first_session.calls == [("same request in first session", True)]
    assert second_session.calls == [("same request in replacement session", True)]


@pytest.mark.asyncio
async def test_append_context_dedups_request_id_inside_manager():
    mgr = _make_manager()
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)

    first = await mgr.append_context(
        source="topic.hook",
        role="user",
        text="same hook",
        request_id="hook-1",
    )
    duplicate = await mgr.append_context(
        source="topic.hook",
        role="user",
        text="same hook replay",
        request_id="hook-1",
    )
    other_source = await mgr.append_context(
        source="proactive.context",
        role="user",
        text="same id, different source",
        request_id="hook-1",
    )

    assert first.appended is True
    assert duplicate.appended is False
    assert duplicate.deduped is True
    assert other_source.appended is True
    assert [message.content for message in history] == [
        "same hook",
        "same id, different source",
    ]


def test_promoted_pending_request_id_keeps_ttl_eviction_order(monkeypatch):
    mgr = _make_manager()
    mgr.session = object()
    mgr._context_append_request_ids = OrderedDict()
    pending_payload = {
        "source": "topic.hook",
        "request_id": "old-hook",
        "lifetime": "current_session",
        "_dedup_pending_ready": True,
    }
    current_payload = {
        "source": "topic.hook",
        "request_id": "new-hook",
        "lifetime": "current_session",
        "_dedup_session_id": id(mgr.session),
    }

    monkeypatch.setattr(core_module.time, "time", lambda: 1000.0)
    mgr._remember_context_append_request_id(pending_payload)
    monkeypatch.setattr(core_module.time, "time", lambda: 1110.0)
    mgr._remember_context_append_request_id(current_payload)

    mgr._promote_context_append_request_id_to_current_session(pending_payload)

    monkeypatch.setattr(core_module.time, "time", lambda: 1121.0)
    assert mgr._context_append_request_seen(pending_payload) is False
    assert mgr._context_append_request_seen(current_payload) is True


@pytest.mark.asyncio
async def test_append_context_reserves_request_id_before_awaiting_prime():
    mgr = _make_manager()
    entered = asyncio.Event()
    release = asyncio.Event()

    class _BlockingPrimeSession:
        def __init__(self):
            self.calls = []

        async def prime_context(self, text, *, skipped=False):
            self.calls.append((text, skipped))
            entered.set()
            await release.wait()

    session = _BlockingPrimeSession()
    mgr.session = session

    first = asyncio.create_task(mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context",
        request_id="ctx-1",
    ))
    await asyncio.wait_for(entered.wait(), timeout=1)

    duplicate_task = asyncio.create_task(mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context replay",
        request_id="ctx-1",
    ))
    await asyncio.sleep(0)
    assert duplicate_task.done() is False

    release.set()
    first_result = await first
    duplicate = await duplicate_task

    assert first_result.appended is True
    assert duplicate.appended is False
    assert duplicate.deduped is True
    assert duplicate.reason == "duplicate_request_id"
    assert session.calls == [("same context", True)]


@pytest.mark.asyncio
async def test_append_context_duplicate_waits_for_failed_original_before_reporting():
    mgr = _make_manager()
    entered = asyncio.Event()
    release = asyncio.Event()

    class _FailingBlockingPrimeSession:
        def __init__(self):
            self.calls = []

        async def prime_context(self, text, *, skipped=False):
            self.calls.append((text, skipped))
            entered.set()
            await release.wait()
            raise RuntimeError("prime unavailable")

    session = _FailingBlockingPrimeSession()
    mgr.session = session

    first = asyncio.create_task(mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context",
        request_id="ctx-1",
    ))
    await asyncio.wait_for(entered.wait(), timeout=1)

    duplicate_task = asyncio.create_task(mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context replay",
        request_id="ctx-1",
    ))
    await asyncio.sleep(0)
    assert duplicate_task.done() is False

    release.set()
    first_result = await first
    duplicate = await duplicate_task

    assert first_result.appended is False
    assert first_result.deduped is False
    assert first_result.reason == "realtime_prime_failed"
    assert duplicate.appended is False
    assert duplicate.deduped is False
    assert duplicate.reason == "realtime_prime_failed"
    assert session.calls == [("same context", True)]


@pytest.mark.asyncio
async def test_append_context_cancellation_releases_duplicate_waiter_and_request_id():
    mgr = _make_manager()
    entered = asyncio.Event()
    release = asyncio.Event()

    class _CancelledPrimeSession:
        def __init__(self):
            self.calls = []

        async def prime_context(self, text, *, skipped=False):
            self.calls.append((text, skipped))
            entered.set()
            await release.wait()

    session = _CancelledPrimeSession()
    mgr.session = session

    first = asyncio.create_task(mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context",
        request_id="ctx-1",
    ))
    await asyncio.wait_for(entered.wait(), timeout=1)

    duplicate_task = asyncio.create_task(mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context replay",
        request_id="ctx-1",
    ))
    await asyncio.sleep(0)
    assert duplicate_task.done() is False

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    duplicate = await asyncio.wait_for(duplicate_task, timeout=1)

    retry_session = _FakePrimeSession()
    mgr.session = retry_session
    retry = await mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context retry after cancellation",
        request_id="ctx-1",
    )

    assert duplicate.appended is False
    assert duplicate.deduped is False
    assert duplicate.reason == "context_inject_cancelled"
    assert retry.appended is True
    assert session.calls == [("same context", True)]
    assert retry_session.calls == [("same context retry after cancellation", True)]


@pytest.mark.asyncio
async def test_append_context_requires_current_delivery_when_ready_session_family_prime_fails():
    mgr = _make_manager()

    class _FailingPrimeSession:
        async def prime_context(self, text, *, skipped=False):
            raise RuntimeError("prime unavailable")

    mgr.session = _FailingPrimeSession()

    result = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="cached for next session",
        lifetime="session_family",
        request_id="ctx-partial",
    )
    duplicate = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="cached for next session again",
        lifetime="session_family",
        request_id="ctx-partial",
    )

    assert result.appended is False
    assert result.targets == ("new_session_cache",)
    assert result.reason == "realtime_prime_failed"
    assert duplicate.deduped is False
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "cached for next session"},
    ]
    assert mgr.message_cache_for_new_session == []


@pytest.mark.asyncio
async def test_pending_context_retries_when_all_targets_fail():
    mgr = _make_manager()
    mgr.session_ready = False
    await mgr.append_context(
        source="game.realtime_context",
        role="system",
        text="retry later",
        timing="when_ready",
        request_id="ctx-retry",
    )

    class _FailingPrimeSession:
        async def prime_context(self, text, *, skipped=False):
            raise RuntimeError("prime unavailable")

    mgr.session = _FailingPrimeSession()
    await mgr._flush_pending_context_appends()

    assert len(mgr.pending_context_appends) == 1
    duplicate = await mgr.append_context(
        source="game.realtime_context",
        role="system",
        text="retry duplicate",
        timing="when_ready",
        request_id="ctx-retry",
    )
    assert duplicate.appended is False
    assert duplicate.deduped is True


@pytest.mark.asyncio
async def test_pending_context_flush_cancellation_restores_queue_and_request_ids():
    mgr = _make_manager()
    mgr.session_ready = False
    await mgr.append_context(
        source="game.realtime_context",
        role="system",
        text="first pending",
        timing="when_ready",
        request_id="ctx-cancel-1",
        ordering_key="001",
    )
    await mgr.append_context(
        source="game.realtime_context",
        role="system",
        text="second pending",
        timing="when_ready",
        request_id="ctx-cancel-2",
        ordering_key="002",
    )

    original_append = mgr._append_context_to_targets

    async def cancelled_append(_payload):
        raise asyncio.CancelledError

    mgr._append_context_to_targets = cancelled_append

    with pytest.raises(asyncio.CancelledError):
        await mgr._flush_pending_context_appends()

    assert [payload["text"] for payload in mgr.pending_context_appends] == [
        "first pending",
        "second pending",
    ]

    mgr._clear_pending_context_appends()
    mgr._append_context_to_targets = original_append
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    mgr.session_ready = True
    retry = await mgr.append_context(
        source="game.realtime_context",
        role="system",
        text="first pending retry",
        timing="when_ready",
        request_id="ctx-cancel-1",
    )

    assert retry.appended is True
    assert retry.deduped is False
    assert [message.content for message in history] == ["system: first pending retry"]


@pytest.mark.asyncio
async def test_append_context_applies_token_budget(monkeypatch):
    mgr = _make_manager()
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)
    truncate_calls = 0

    def fake_truncate(text, max_tokens, *args, **kwargs):
        nonlocal truncate_calls
        truncate_calls += 1
        assert max_tokens == 3
        return "__sentinel_truncated_context__"

    monkeypatch.setattr(core_module, "_CONTEXT_APPEND_DEFAULT_MAX_TOKENS", 3)
    monkeypatch.setattr("utils.tokenize.truncate_to_tokens", fake_truncate)

    result = await mgr.append_context(
        source="test.context",
        role="user",
        text="one two three four five",
    )

    assert result.appended is True
    assert truncate_calls == 1
    assert history[0].content == "__sentinel_truncated_context__"


@pytest.mark.asyncio
async def test_durable_cache_desync_warns_and_reappends_when_swap_consumed_cache(monkeypatch):
    # 失步窗口：when_ready 的 durable 上下文先入 next-session 缓存并记账，session swap
    # 期间 _consume_next_session_context_messages 把内容消费走，但去重记账本（TTL 内）未清。
    # readiness flush 重走 _append_context_to_targets 时应：(1) 自检告警两套结构失步，
    # (2) 靠 present 二次核对兜底重写、不丢上下文。
    mgr = _make_manager()
    mgr.session_ready = False

    warnings_seen = []
    monkeypatch.setattr(
        core_module.logger,
        "warning",
        lambda msg, *args, **kwargs: warnings_seen.append(msg % args if args else msg),
    )

    queued = await mgr.append_context(
        source="game.icebreaker",
        role="assistant",
        text="durable setup",
        timing="when_ready",
        lifetime="next_session",
        request_id="durable-request",
    )
    assert queued.targets == ("pending_ready",)
    assert mgr.next_session_context_messages == [{"role": "Lan", "text": "durable setup"}]
    assert len(mgr.pending_context_appends) == 1

    # 模拟 session swap 消费缓存（记账本不动）。
    mgr._consume_next_session_context_messages(len(mgr.next_session_context_messages))
    assert mgr.next_session_context_messages == []

    mgr.session_ready = True
    flushed = await mgr._flush_pending_context_appends()

    assert flushed == 1
    assert any("durable context cache desync" in str(msg) for msg in warnings_seen)
    assert "content missing from next-session cache" in " ".join(str(m) for m in warnings_seen)
    # 兜底重写，上下文没丢。
    assert mgr.next_session_context_messages == [{"role": "Lan", "text": "durable setup"}]


@pytest.mark.asyncio
async def test_durable_cache_desync_warns_when_content_present_but_record_missing(monkeypatch):
    # 反向失步：内容已在 next-session 缓存，但去重记账本没有对应记录（例如记账被清/未写成功）。
    # 这条会被当作首次写而重复入库，应在重写前自检告警。
    mgr = _make_manager()
    mgr.next_session_context_messages = [{"role": "Lan", "text": "dup line"}]

    warnings_seen = []
    monkeypatch.setattr(
        core_module.logger,
        "warning",
        lambda msg, *args, **kwargs: warnings_seen.append(msg % args if args else msg),
    )

    payload = {
        "source": "game.icebreaker",
        "role": "assistant",
        "text": "dup line",
        "audience": "model",
        "timing": "now",
        "lifetime": "next_session",
        "request_id": "dup-request",
        "ordering_key": "",
        "metadata": {},
    }
    result = await mgr._append_context_to_targets(payload)

    assert result.appended is True
    assert "new_session_cache" in result.targets
    assert any(
        "content present but dedup record" in str(msg) for msg in warnings_seen
    )
    # 记账缺失导致重复入库。
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "dup line"},
        {"role": "Lan", "text": "dup line"},
    ]
