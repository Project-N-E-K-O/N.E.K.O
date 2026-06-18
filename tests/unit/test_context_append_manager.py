import asyncio
import inspect
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
    mgr.session = SimpleNamespace(_conversation_history=history)

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
    assert session.calls == [("user: score snapshot", True)]


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

    consumed = await mgr._prime_late_next_session_context_after_swap(1)
    mgr._consume_next_session_context_messages(consumed)

    assert consumed == 3
    assert session.calls == [
        ("system | late before promote\n", True),
        ("Master | late during prime\n", True),
    ]
    assert mgr.next_session_context_messages == []


def test_final_swap_primes_late_context_before_flushing_cached_audio():
    source = inspect.getsource(core_module.LLMSessionManager._perform_final_swap_sequence)

    assert source.index("_prime_late_next_session_context_after_swap") < source.index(
        "_flush_hot_swap_audio_cache"
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
async def test_clear_pending_context_appends_releases_only_stale_request_ids():
    mgr = _make_manager()
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)

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
    mgr.session = SimpleNamespace(_conversation_history=history)
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

    duplicate = await mgr.append_context(
        source="game.realtime_context",
        role="user",
        text="same context replay",
        request_id="ctx-1",
    )
    release.set()
    first_result = await first

    assert first_result.appended is True
    assert duplicate.appended is False
    assert duplicate.deduped is True
    assert duplicate.reason == "duplicate_request_id"
    assert session.calls == [("user: same context", True)]


@pytest.mark.asyncio
async def test_append_context_keeps_partial_targets_when_realtime_prime_fails():
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

    assert result.appended is True
    assert result.targets == ("new_session_cache",)
    assert duplicate.appended is False
    assert duplicate.deduped is True
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
