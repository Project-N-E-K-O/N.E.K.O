import asyncio
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
    assert mgr.message_cache_for_new_session == [{"role": "Master", "text": "choice A"}]


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
    assert session.calls == [("user: same context", True)]


@pytest.mark.asyncio
async def test_append_context_applies_token_budget(monkeypatch):
    mgr = _make_manager()
    history = []
    mgr.session = SimpleNamespace(_conversation_history=history)

    def fake_truncate(text, max_tokens, *args, **kwargs):
        assert max_tokens == 3
        return "one two three"

    monkeypatch.setattr(core_module, "_CONTEXT_APPEND_DEFAULT_MAX_TOKENS", 3)
    monkeypatch.setattr("utils.tokenize.truncate_to_tokens", fake_truncate)

    result = await mgr.append_context(
        source="test.context",
        role="user",
        text="one two three four five",
    )

    assert result.appended is True
    assert history[0].content == "one two three"
