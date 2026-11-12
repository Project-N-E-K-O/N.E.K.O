"""Main-service side of NekoClaw: the two task tools and the check of a turn.

The agent process is stood in for by an ``httpx.MockTransport`` (the task
tools) and by a recorded call (the turn check). Nothing here calls a model:
a task tool is one request to the agent process, and whether a turn is worth
a model call is decided over there.
"""

from __future__ import annotations

import asyncio
import json
import logging
from queue import Queue

import httpx
import pytest

from config import TOOL_SERVER_PORT
from config.nekoclaw_settings import TASK_TOOL_TIMEOUT_SECONDS
from config.prompts.prompts_task_tools import (
    DISPATCH_TOOL_DESCRIPTION,
    DISPATCH_TOOL_NAME,
    QUERY_TOOL_DESCRIPTION,
    QUERY_TOOL_NAME,
    TASK_TOOL_EMPTY,
    TASK_TOOL_QUERY_UNREACHABLE,
    TASK_TOOL_UNREACHABLE,
)
from main_logic import cross_server, public_knowledge
from main_logic.core._shared import logger as core_logger
from main_logic.core.notify import NotifyMixin
from main_logic.core.proactive import ProactiveMixin
from main_logic.core.tool_calling import ToolCallingMixin
from main_logic.core.turn import TurnMixin
from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

pytestmark = pytest.mark.unit


class _Manager(ToolCallingMixin, TurnMixin, NotifyMixin):
    """The mixins under test, without the session manager's ``__init__``."""

    update_agent_flags = ProactiveMixin.update_agent_flags


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    public_knowledge.reset_for_tests()
    monkeypatch.setattr(public_knowledge, "_next_check_at", float("inf"))
    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS", raising=False)
    yield
    public_knowledge.reset_for_tests()


def _manager(*, lang: str = "zh", **flags: bool) -> _Manager:
    manager = object.__new__(_Manager)
    manager.user_language = lang
    manager.lanlan_name = "Lan"
    manager.input_mode = "text"
    manager.memory_server_port = 48912
    manager.current_speech_id = "speech-1"
    manager.tool_registry = ToolRegistry()
    manager.agent_flags = {
        "agent_enabled": True,
        "computer_use_enabled": False,
        "browser_use_enabled": False,
        "user_plugin_enabled": True,
        **flags,
    }
    manager.sync_message_queue = Queue()
    manager.fired = []
    manager._fire_task = lambda coro: (manager.fired.append(coro.__qualname__), coro.close())
    manager.synced = 0

    async def _sync(**_kwargs):
        manager.synced += 1

    manager._sync_tools_to_active_session = _sync
    manager.bubble = []

    async def _bubble(active, **_kwargs):
        manager.bubble.append(active)

    manager._push_focus_thinking = _bubble
    return manager


class _AgentProcess:
    """Stands in for the agent process behind the shared internal client."""

    def __init__(self, handler):
        self.requests: list[dict] = []
        self.timeouts: list[float] = []

        def record(request: httpx.Request) -> httpx.Response:
            request.read()
            self.requests.append({"url": str(request.url), "body": json.loads(request.content)})
            return handler(request)

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(record))

    async def post(self, url, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        return await self._client.post(url, **kwargs)


@pytest.fixture
def logged():
    """Messages the session manager's own logger wrote (it does not reach caplog)."""
    messages: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Collect(level=logging.DEBUG)
    core_logger.addHandler(handler)
    try:
        yield messages
    finally:
        core_logger.removeHandler(handler)


@pytest.fixture
def agent_process(monkeypatch):
    def install(handler):
        process = _AgentProcess(handler)
        monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: process)
        return process

    return install


# ---------------------------------------------------------------------------
# Whether NekoClaw is on for a session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"agent_enabled": True, "user_plugin_enabled": True}, True),
        ({"agent_enabled": True, "user_plugin_enabled": False, "computer_use_enabled": True}, True),
        ({"agent_enabled": True, "user_plugin_enabled": False, "browser_use_enabled": True}, True),
        # The master switch alone offers nothing to do anything with.
        ({"agent_enabled": True, "user_plugin_enabled": False}, False),
        # A capability switch left on under a master switch that is off.
        ({"agent_enabled": False, "user_plugin_enabled": True, "computer_use_enabled": True}, False),
    ],
)
def test_nekoclaw_is_on_with_the_master_switch_and_one_capability(flags, expected):
    assert _manager(**flags)._is_agent_enabled() is expected


# ---------------------------------------------------------------------------
# Registering the two tools
# ---------------------------------------------------------------------------


def test_both_tools_are_registered_in_the_session_language_while_nekoclaw_is_on():
    manager = _manager(lang="zh-TW")

    manager._register_builtin_tools()

    dispatch = manager.tool_registry.get(DISPATCH_TOOL_NAME)
    query = manager.tool_registry.get(QUERY_TOOL_NAME)
    assert dispatch.description == DISPATCH_TOOL_DESCRIPTION["zh-TW"] != DISPATCH_TOOL_DESCRIPTION["zh"]
    assert query.description == QUERY_TOOL_DESCRIPTION["zh-TW"]
    assert dispatch.metadata == query.metadata == {"source": "builtin"}
    assert manager.tool_registry.get("recall_memory") is not None


def test_a_session_without_nekoclaw_does_not_carry_the_tools():
    manager = _manager(agent_enabled=False)

    manager._register_builtin_tools()

    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is None
    assert manager.tool_registry.get(QUERY_TOOL_NAME) is None
    # The other built-in tool is not affected by the switch.
    assert manager.tool_registry.get("recall_memory") is not None


def test_registering_reports_a_change_only_when_there_is_one():
    manager = _manager()

    assert manager._register_task_tools() is True
    assert manager._register_task_tools() is False
    manager.user_language = "en"
    assert manager._register_task_tools() is True
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME).description == DISPATCH_TOOL_DESCRIPTION["en"]
    manager.agent_flags["agent_enabled"] = False
    assert manager._register_task_tools() is True
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is None
    assert manager._register_task_tools() is False


def test_a_tool_of_another_source_holding_the_name_is_left_alone():
    manager = _manager()
    theirs = ToolDefinition(name=DISPATCH_TOOL_NAME, description="a plugin's own", parameters={}, metadata={"source": "plugin"})
    manager.tool_registry.register(theirs, replace=True)

    manager._register_task_tools()
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is theirs
    assert manager.tool_registry.get(QUERY_TOOL_NAME).metadata == {"source": "builtin"}

    manager.agent_flags["agent_enabled"] = False
    manager._register_task_tools()
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is theirs
    assert manager.tool_registry.get(QUERY_TOOL_NAME) is None


def test_a_switch_change_reaches_the_live_session_once():
    manager = _manager(agent_enabled=False)
    manager._register_builtin_tools()

    manager.update_agent_flags({"agent_enabled": True})
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is not None
    assert manager.fired == ["_manager.<locals>._sync"]

    # The same switches again: nothing to send to the session.
    manager.update_agent_flags({"agent_enabled": True})
    assert len(manager.fired) == 1

    manager.update_agent_flags({"user_plugin_enabled": False})
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is None
    assert len(manager.fired) == 2


def test_the_switch_that_disables_every_built_in_tool_covers_these_two(monkeypatch):
    monkeypatch.setenv("NEKO_DISABLE_BUILTIN_TOOLS", "1")
    manager = _manager()

    manager._register_builtin_tools()

    assert manager.tool_registry.names() == []


# ---------------------------------------------------------------------------
# dispatch_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatching_is_one_request_and_returns_the_receipt(agent_process, logged):
    process = agent_process(lambda request: httpx.Response(200, json={"kind": "accepted", "text": "已受理：订闹钟。"}))
    manager = _manager(lang="zh-TW")
    manager._latest_user_utterance = "明早七点叫我"

    receipt = await manager._handle_dispatch_task_call({"task": "  订一个明早七点的闹钟  "})

    assert receipt == "已受理：订闹钟。"
    assert process.requests == [
        {
            "url": f"http://127.0.0.1:{TOOL_SERVER_PORT}/nekoclaw/dispatch",
            "body": {
                "text": "订一个明早七点的闹钟",
                "lanlan_name": "Lan",
                "user_text": "明早七点叫我",
                "conversation_id": "speech-1",
                "language": "zh-TW",
            },
        }
    ]
    # It waits for the first round only, never for the work.
    assert process.timeouts == [TASK_TOOL_TIMEOUT_SECONDS] and TASK_TOOL_TIMEOUT_SECONDS <= 3
    # The log carries names and the outcome, never what was said.
    assert len(logged) == 1 and "kind=accepted" in logged[0] and "name=Lan" in logged[0]
    assert "闹钟" not in logged[0] and "七点" not in logged[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"task": "   "}, {"task": 7}, None, "订闹钟"])
async def test_dispatching_nothing_asks_nobody(agent_process, arguments):
    process = agent_process(lambda request: httpx.Response(200, json={"text": "x"}))
    manager = _manager(lang="en")

    assert await manager._handle_dispatch_task_call(arguments) == TASK_TOOL_EMPTY["en"]
    assert process.requests == []


def _raising(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        _raising,
        lambda request: httpx.Response(503, json={"text": "starting"}),
        lambda request: httpx.Response(200, json=["not", "an", "object"]),
        lambda request: httpx.Response(200, content=b"not json"),
        lambda request: httpx.Response(200, json={"kind": "accepted", "text": "  "}),
    ],
    ids=["refused", "status", "not-an-object", "not-json", "empty-text"],
)
async def test_when_the_task_system_cannot_be_reached_she_is_told_nothing_was_taken_up(agent_process, handler):
    process = agent_process(handler)
    manager = _manager(lang="ja")

    assert await manager._handle_dispatch_task_call({"task": "メモを取って"}) == TASK_TOOL_UNREACHABLE["ja"]
    # Not tried again: a second request could hand the same task over twice.
    assert len(process.requests) == 1


# ---------------------------------------------------------------------------
# query_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_querying_is_one_request_and_returns_the_answer(agent_process, logged):
    process = agent_process(lambda request: httpx.Response(200, json={"text": "在办：订闹钟。"}))
    manager = _manager()

    answer = await manager._handle_query_tasks_call({"question": "闹钟订好了吗"})

    assert answer == "在办：订闹钟。"
    assert process.requests == [
        {
            "url": f"http://127.0.0.1:{TOOL_SERVER_PORT}/nekoclaw/query",
            "body": {"text": "闹钟订好了吗", "lanlan_name": "Lan", "language": "zh"},
        }
    ]
    assert process.timeouts == [TASK_TOOL_TIMEOUT_SECONDS]
    assert len(logged) == 1 and "answered" in logged[0] and "闹钟" not in logged[0]


@pytest.mark.asyncio
async def test_a_query_without_a_question_asks_what_is_under_way(agent_process):
    process = agent_process(lambda request: httpx.Response(200, json={"text": "Nothing is under way."}))
    manager = _manager(lang="en")

    assert await manager._handle_query_tasks_call({}) == "Nothing is under way."
    assert process.requests[0]["body"]["text"] == ""


@pytest.mark.asyncio
async def test_a_query_that_cannot_be_answered_says_so_and_claims_nothing(agent_process):
    agent_process(_raising)
    manager = _manager(lang="ko")

    assert await manager._handle_query_tasks_call({"question": "뭐 하고 있어?"}) == TASK_TOOL_QUERY_UNREACHABLE["ko"]


# ---------------------------------------------------------------------------
# What a tool call leaves on the turn
# ---------------------------------------------------------------------------


def _with_tools(manager: _Manager) -> _Manager:
    manager._register_builtin_tools()
    for name in ("recall_memory", DISPATCH_TOOL_NAME, QUERY_TOOL_NAME):
        current = manager.tool_registry.get(name)
        manager.tool_registry.register(
            ToolDefinition(
                name=name,
                description=current.description,
                parameters=current.parameters,
                handler=lambda arguments: "ok",
                metadata=current.metadata,
            ),
            replace=True,
        )
    manager.tool_registry.register(
        ToolDefinition(name="plugin_tool", description="d", parameters={}, handler=lambda arguments: "ok", metadata={"source": "plugin"}),
        replace=True,
    )
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "acted"),
    [
        (DISPATCH_TOOL_NAME, True),
        ("plugin_tool", True),
        # Looking something up is not acting: her words are still checked afterwards.
        (QUERY_TOOL_NAME, False),
        ("recall_memory", False),
        (public_knowledge.TOOL_NAME, False),
    ],
)
async def test_only_a_tool_that_acts_marks_the_turn_as_acted(name, acted):
    manager = _with_tools(_manager())
    manager._turn_acted_through_tools = False

    await manager._on_tool_call(ToolCall(name=name, arguments={}, call_id="c1"))

    assert manager._turn_acted_through_tools is acted


@pytest.mark.asyncio
async def test_a_tool_call_before_her_first_words_lights_the_waiting_bubble():
    manager = _with_tools(_manager())
    manager._turn_has_spoken = False

    result = await manager._on_tool_call(ToolCall(name=DISPATCH_TOOL_NAME, arguments={"task": "t"}, call_id="c1"))

    assert result.output == "ok"
    assert manager.bubble == [True]
    assert manager._tool_wait_bubble is True


@pytest.mark.asyncio
async def test_a_tool_call_after_she_has_spoken_lights_nothing():
    manager = _with_tools(_manager())
    manager._turn_has_spoken = True

    await manager._on_tool_call(ToolCall(name=DISPATCH_TOOL_NAME, arguments={"task": "t"}, call_id="c1"))

    assert manager.bubble == []
    assert getattr(manager, "_tool_wait_bubble", False) is False


@pytest.mark.asyncio
async def test_a_bubble_that_cannot_be_lit_does_not_stop_the_call():
    manager = _with_tools(_manager())

    async def _broken(active, **_kwargs):
        raise RuntimeError("socket closed")

    manager._push_focus_thinking = _broken

    result = await manager._on_tool_call(ToolCall(name=DISPATCH_TOOL_NAME, arguments={"task": "t"}, call_id="c1"))

    assert result.output == "ok" and result.is_error is False


def test_closing_a_turn_reports_once_whether_it_acted_and_drops_the_bubble():
    manager = _manager()
    manager._turn_acted_through_tools = True
    manager._turn_has_spoken = True
    manager._tool_wait_bubble = True

    assert manager._close_turn_tool_state() is True
    assert manager._turn_has_spoken is False and manager._tool_wait_bubble is False
    assert manager.fired == ["_manager.<locals>._bubble"]
    # The next turn starts clean.
    assert manager._close_turn_tool_state() is False
    assert len(manager.fired) == 1


def test_the_turn_end_says_whether_she_acted_through_a_tool():
    manager = _manager()
    manager._turn_acted_through_tools = True

    acted = manager._queue_turn_end("req-1")
    quiet = manager._queue_turn_end("req-2")

    assert acted == {"type": "system", "data": "turn end", "request_id": "req-1", "acted_through_tools": True}
    assert quiet == {"type": "system", "data": "turn end", "request_id": "req-2"}
    assert manager.sync_message_queue.get_nowait() is acted


def test_a_callback_turn_end_clears_the_mark_without_carrying_it():
    manager = _manager()
    manager._turn_acted_through_tools = True

    ended = manager._queue_agent_callback_turn_end("req-1")

    assert "acted_through_tools" not in ended
    assert manager._turn_acted_through_tools is False


# ---------------------------------------------------------------------------
# Which exchange of a turn is checked
# ---------------------------------------------------------------------------


def test_the_latest_user_message_and_everything_she_said_after_it():
    messages = [
        {"role": "user", "content": "之前的话"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "帮我订个闹钟", "attachments": [{"type": "image"}]},
        {"role": "assistant", "content": "好的"},
        {"role": "system", "content": "不算"},
        {"role": "assistant", "content": "这就去"},
        {"role": "assistant", "content": ""},
    ]

    assert cross_server._turn_to_check(messages) == ("帮我订个闹钟", "好的\n这就去", True)


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "assistant", "content": "她自己开口"}],
        [{"role": "user", "content": "没人回答"}],
        [{"role": "user", "content": "只有空回答"}, {"role": "assistant", "content": "   "}],
        ["not a message", None],
    ],
)
def test_nothing_to_check_without_both_sides(messages):
    assert cross_server._turn_to_check(messages) is None


@pytest.fixture
def published(monkeypatch):
    calls: list[dict] = []
    outcome = {"sent": True}

    async def _publish(**kwargs):
        calls.append(kwargs)
        return outcome["sent"]

    monkeypatch.setattr(cross_server, "publish_turn_check_reliably", _publish)
    monkeypatch.setattr("main_logic.activity.master_emotion.gate_signal_for", lambda name, text: 0.42)
    calls_and_outcome = (calls, outcome)
    return calls_and_outcome


_EXCHANGE = [{"role": "user", "content": "帮我订个闹钟"}, {"role": "assistant", "content": "好，我去订"}]


@pytest.mark.asyncio
async def test_a_turn_is_sent_once_with_both_sides_and_the_hint(published):
    calls, _ = published

    sent = await cross_server._publish_turn_check_with_fallback(
        "Lan", "turn_end", _EXCHANGE, conversation_id="conv-1", language="zh-TW"
    )

    assert sent is True
    assert calls == [
        {
            "lanlan_name": "Lan",
            "user_text": "帮我订个闹钟",
            "reply_text": "好，我去订",
            "ack_timeout_s": 0.8,
            "retries": 1,
            "conversation_id": "conv-1",
            "external_intent": 0.42,
            "language": "zh-TW",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "assistant", "content": "她自己开口"}],
        [{"role": "user", "content": "没人回答"}],
        # A turn of pictures only: no words to hold her reply against.
        [{"role": "user", "content": "  ", "attachments": [{"type": "image"}]}, {"role": "assistant", "content": "好看"}],
    ],
)
async def test_a_turn_with_nothing_to_compare_is_not_sent(published, messages):
    calls, _ = published

    assert await cross_server._publish_turn_check_with_fallback("Lan", "turn_end", messages) is False
    assert calls == []


@pytest.mark.asyncio
async def test_the_hint_is_left_out_when_the_request_may_be_in_a_picture(published):
    calls, _ = published
    messages = [{"role": "user", "content": "照这个做", "attachments": [{"type": "image"}]}, _EXCHANGE[1]]

    assert await cross_server._publish_turn_check_with_fallback("Lan", "turn_end", messages) is True
    assert calls[0]["external_intent"] is None


@pytest.mark.asyncio
async def test_a_hint_that_cannot_be_read_does_not_hold_the_turn_back(published, monkeypatch):
    calls, _ = published

    def _broken(name, text):
        raise RuntimeError("tracker not ready")

    monkeypatch.setattr("main_logic.activity.master_emotion.gate_signal_for", _broken)

    assert await cross_server._publish_turn_check_with_fallback("Lan", "turn_end", _EXCHANGE) is True
    assert calls[0]["external_intent"] is None


@pytest.mark.asyncio
async def test_an_unacknowledged_or_failing_send_reports_false_and_raises_nothing(published, monkeypatch):
    calls, outcome = published
    outcome["sent"] = False

    assert await cross_server._publish_turn_check_with_fallback("Lan", "turn_end", _EXCHANGE) is False
    assert len(calls) == 1

    async def _raise(**kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cross_server, "publish_turn_check_reliably", _raise)
    assert await cross_server._publish_turn_check_with_fallback("Lan", "session_end", _EXCHANGE) is False
