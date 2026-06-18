import pytest

from main_logic.core import LLMSessionManager
from utils.llm_client import AIMessage, HumanMessage


class _FakeSession:
    def __init__(self):
        self._conversation_history = []


class _FakeRealtimeSession:
    def __init__(self):
        self.prime_context_calls = []

    async def prime_context(self, text, skipped=False):
        self.prime_context_calls.append((text, skipped))


def _make_mgr(session=None):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.session = session
    mgr.is_preparing_new_session = False
    mgr.message_cache_for_new_session = []
    mgr.lanlan_name = "Lan"
    mgr.master_name = "Master"
    return mgr


@pytest.mark.asyncio
async def test_icebreaker_context_appends_to_active_conversation_history():
    mgr = _make_mgr(_FakeSession())

    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "assistant", "你好呀") is True
    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "user", "继续打字") is True

    history = mgr.session._conversation_history
    assert isinstance(history[0], AIMessage)
    assert history[0].content == "你好呀"
    assert isinstance(history[1], HumanMessage)
    assert history[1].content == "继续打字"
    assert mgr.message_cache_for_new_session == []


@pytest.mark.asyncio
async def test_icebreaker_context_primes_active_realtime_session():
    session = _FakeRealtimeSession()
    mgr = _make_mgr(session)

    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "assistant", "先认识一下") is True
    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "user", "我选第一个") is True

    assert session.prime_context_calls == [
        ("assistant: 先认识一下", True),
        ("user: 我选第一个", True),
    ]


@pytest.mark.asyncio
async def test_icebreaker_context_reuses_hot_swap_message_cache_when_preparing():
    mgr = _make_mgr(None)
    mgr.is_preparing_new_session = True

    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "assistant", "先认识一下") is True
    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "user", "看得差不多了") is True

    assert mgr.message_cache_for_new_session == [
        {"role": "Lan", "text": "先认识一下"},
        {"role": "Master", "text": "看得差不多了"},
    ]


@pytest.mark.asyncio
async def test_icebreaker_realtime_context_also_reuses_hot_swap_cache_when_preparing():
    session = _FakeRealtimeSession()
    mgr = _make_mgr(session)
    mgr.is_preparing_new_session = True

    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "assistant", "先认识一下") is True

    assert mgr.message_cache_for_new_session == [{"role": "Lan", "text": "先认识一下"}]
    assert session.prime_context_calls == [("assistant: 先认识一下", True)]


@pytest.mark.asyncio
async def test_icebreaker_context_rejects_empty_or_unknown_role():
    mgr = _make_mgr(_FakeSession())

    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "assistant", "   ") is False
    assert await LLMSessionManager.append_icebreaker_context_async(mgr, "system", "不要写入") is False
    assert mgr.session._conversation_history == []
