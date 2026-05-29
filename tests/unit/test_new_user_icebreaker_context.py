from main_logic.core import LLMSessionManager
from utils.llm_client import AIMessage, HumanMessage


class _FakeSession:
    def __init__(self):
        self._conversation_history = []


def _make_mgr(session=None):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.session = session
    mgr.pending_icebreaker_context = []
    return mgr


def test_icebreaker_context_appends_to_active_conversation_history():
    mgr = _make_mgr(_FakeSession())

    assert LLMSessionManager.append_icebreaker_context(mgr, "assistant", "你好呀") is True
    assert LLMSessionManager.append_icebreaker_context(mgr, "user", "继续打字") is True

    history = mgr.session._conversation_history
    assert isinstance(history[0], AIMessage)
    assert history[0].content == "你好呀"
    assert isinstance(history[1], HumanMessage)
    assert history[1].content == "继续打字"
    assert mgr.pending_icebreaker_context == []


def test_icebreaker_context_waits_for_next_session_when_none_active():
    mgr = _make_mgr(None)

    assert LLMSessionManager.append_icebreaker_context(mgr, "assistant", "先认识一下") is True
    assert LLMSessionManager.append_icebreaker_context(mgr, "user", "看得差不多了") is True

    assert len(mgr.pending_icebreaker_context) == 2

    mgr.session = _FakeSession()
    LLMSessionManager._flush_pending_icebreaker_context(mgr)

    history = mgr.session._conversation_history
    assert [type(message) for message in history] == [AIMessage, HumanMessage]
    assert [message.content for message in history] == ["先认识一下", "看得差不多了"]
    assert mgr.pending_icebreaker_context == []


def test_icebreaker_context_rejects_empty_or_unknown_role():
    mgr = _make_mgr(_FakeSession())

    assert LLMSessionManager.append_icebreaker_context(mgr, "assistant", "   ") is False
    assert LLMSessionManager.append_icebreaker_context(mgr, "system", "不要写入") is False
    assert mgr.session._conversation_history == []
