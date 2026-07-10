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
    mgr.session_ready = True
    mgr.is_preparing_new_session = False
    mgr.message_cache_for_new_session = []
    mgr.next_session_context_messages = []
    mgr.lanlan_name = "Lan"
    mgr.master_name = "Master"
    return mgr


async def _append_icebreaker(mgr, role, text):
    return await LLMSessionManager.append_context(
        mgr,
        source="icebreaker",
        role=role,
        text=text,
        audience="model",
        timing="now",
        lifetime="session_family",
    )


@pytest.mark.asyncio
async def test_icebreaker_context_appends_to_active_conversation_history():
    mgr = _make_mgr(_FakeSession())

    assert (await _append_icebreaker(mgr, "assistant", "你好呀")).appended is True
    assert (await _append_icebreaker(mgr, "user", "继续打字")).appended is True

    history = mgr.session._conversation_history
    assert isinstance(history[0], AIMessage)
    assert history[0].content == "你好呀"
    assert isinstance(history[1], HumanMessage)
    assert history[1].content == "继续打字"
    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "你好呀"},
        {"role": "Master", "text": "继续打字"},
    ]
    assert mgr.message_cache_for_new_session == []


@pytest.mark.asyncio
async def test_icebreaker_context_primes_active_realtime_session():
    session = _FakeRealtimeSession()
    mgr = _make_mgr(session)

    assert (await _append_icebreaker(mgr, "assistant", "先认识一下")).appended is True
    assert (await _append_icebreaker(mgr, "user", "我选第一个")).appended is True

    assert session.prime_context_calls == [
        ("assistant: 先认识一下", True),
        ("user: 我选第一个", True),
    ]


@pytest.mark.asyncio
async def test_icebreaker_context_records_next_session_messages_when_preparing():
    mgr = _make_mgr(None)
    mgr.is_preparing_new_session = True

    assert (await _append_icebreaker(mgr, "assistant", "先认识一下")).appended is True
    assert (await _append_icebreaker(mgr, "user", "看得差不多了")).appended is True

    assert mgr.next_session_context_messages == [
        {"role": "Lan", "text": "先认识一下"},
        {"role": "Master", "text": "看得差不多了"},
    ]
    assert mgr.message_cache_for_new_session == []


@pytest.mark.asyncio
async def test_icebreaker_realtime_context_also_records_next_session_messages_when_preparing():
    session = _FakeRealtimeSession()
    mgr = _make_mgr(session)
    mgr.is_preparing_new_session = True

    assert (await _append_icebreaker(mgr, "assistant", "先认识一下")).appended is True

    assert mgr.next_session_context_messages == [{"role": "Lan", "text": "先认识一下"}]
    assert mgr.message_cache_for_new_session == []
    assert session.prime_context_calls == [("assistant: 先认识一下", True)]


@pytest.mark.asyncio
async def test_icebreaker_context_rejects_empty_or_unknown_role():
    mgr = _make_mgr(_FakeSession())

    assert (await _append_icebreaker(mgr, "assistant", "   ")).appended is False
    assert (await _append_icebreaker(mgr, "observer", "不要写入")).appended is False
    assert mgr.session._conversation_history == []
