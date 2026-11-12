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
    TASK_TOOL_UNCERTAIN,
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


def test_that_switch_holds_when_a_switch_update_registers_them_directly(monkeypatch):
    manager = _manager(agent_enabled=False)
    manager._register_builtin_tools()
    monkeypatch.setenv("NEKO_DISABLE_BUILTIN_TOOLS", "1")

    manager.update_agent_flags({"agent_enabled": True})

    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is None
    assert manager.tool_registry.get(QUERY_TOOL_NAME) is None
    assert manager.fired == []


def test_the_switch_set_later_takes_the_two_tools_out_again(monkeypatch):
    manager = _manager()
    manager._register_builtin_tools()
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is not None
    assert manager.tool_registry.get(QUERY_TOOL_NAME) is not None
    monkeypatch.setenv("NEKO_DISABLE_BUILTIN_TOOLS", "1")

    assert manager._register_task_tools() is True
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is None
    assert manager.tool_registry.get(QUERY_TOOL_NAME) is None


def test_a_status_push_that_changes_nothing_registers_nothing_again():
    manager = _manager()
    manager._register_builtin_tools()
    before = (manager.tool_registry.get(DISPATCH_TOOL_NAME), manager.tool_registry.get(QUERY_TOOL_NAME))

    for _ in range(3):
        manager.update_agent_flags({"agent_enabled": True, "user_plugin_enabled": True})

    # The very same objects: nothing was registered (and logged) a second time.
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is before[0]
    assert manager.tool_registry.get(QUERY_TOOL_NAME) is before[1]
    assert manager.fired == []


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


def _pool_exhausted(request: httpx.Request) -> httpx.Response:
    raise httpx.PoolTimeout("no connection free", request=request)


def _no_answer_in_time(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer", request=request)


def _connection_dropped(request: httpx.Request) -> httpx.Response:
    raise httpx.RemoteProtocolError("server disconnected", request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        _raising,
        _pool_exhausted,
        lambda request: httpx.Response(400, json={"detail": "text required"}),
        lambda request: httpx.Response(422, json={"detail": "not an object"}),
    ],
    ids=["refused", "no-connection-free", "turned-down", "unreadable-request"],
)
async def test_a_dispatch_that_never_arrived_is_told_as_not_handed_over(agent_process, handler):
    process = agent_process(handler)
    manager = _manager(lang="ja")

    assert await manager._handle_dispatch_task_call({"task": "メモを取って"}) == TASK_TOOL_UNREACHABLE["ja"]
    # Not tried again: a second request could hand the same task over twice.
    assert len(process.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        _no_answer_in_time,
        _connection_dropped,
        # An error of the agent process's own can come after the task was
        # taken on (while its receipt was being written): not a refusal.
        lambda request: httpx.Response(500, json={"detail": "Internal Server Error"}),
        lambda request: httpx.Response(503, json={"text": "busy"}),
        lambda request: httpx.Response(200, json=["not", "an", "object"]),
        lambda request: httpx.Response(200, content=b"not json"),
        lambda request: httpx.Response(200, json={"kind": "accepted", "text": "  "}),
    ],
    ids=["timed-out", "dropped", "failed-inside", "unavailable", "not-an-object", "not-json", "empty-text"],
)
async def test_a_dispatch_that_went_out_unanswered_is_told_as_not_known(agent_process, handler, logged):
    # The agent process may be carrying it out all the same: saying "not
    # handed over" would have her tell the user so and dispatch it again.
    process = agent_process(handler)
    manager = _manager(lang="zh")

    answer = await manager._handle_dispatch_task_call({"task": "订一个明早七点的闹钟"})

    assert answer == TASK_TOOL_UNCERTAIN["zh"] != TASK_TOOL_UNREACHABLE["zh"]
    assert "不确定" in answer and "不要重新派发" in answer.replace("也不要", "不要") and "查询" in answer
    assert len(process.requests) == 1
    assert "闹钟" not in logged[-1]


@pytest.mark.asyncio
async def test_the_time_limit_is_on_the_whole_request_not_on_each_of_its_phases(monkeypatch):
    # The HTTP client times connecting, sending and reading each on their
    # own: a request slow in every phase would take several times the limit.
    monkeypatch.setattr("config.nekoclaw_settings.TASK_TOOL_TIMEOUT_SECONDS", 0.1)
    given: list[float] = []

    class _SlowInEveryPhase:
        async def post(self, url, **kwargs):
            given.append(kwargs["timeout"])
            for _phase in ("connect", "write", "read"):
                await asyncio.sleep(kwargs["timeout"] * 0.8)
            return httpx.Response(200, json={"kind": "accepted", "text": "已受理。"})

    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: _SlowInEveryPhase())
    manager = _manager(lang="zh")
    loop = asyncio.get_running_loop()
    began = loop.time()

    answer = await manager._handle_dispatch_task_call({"task": "订一个明早七点的闹钟"})

    assert given == [0.1]
    assert loop.time() - began < 0.19
    # Which phase it was in is not known, so neither is whether it arrived.
    assert answer == TASK_TOOL_UNCERTAIN["zh"]


def test_the_answer_for_an_unknown_outcome_exists_in_every_language():
    languages = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}

    assert set(TASK_TOOL_UNCERTAIN) == set(TASK_TOOL_UNREACHABLE) == languages
    assert len(set(TASK_TOOL_UNCERTAIN.values())) == len(languages)
    assert all(TASK_TOOL_UNCERTAIN[lang] != TASK_TOOL_UNREACHABLE[lang] for lang in languages)


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
    manager._latest_user_utterance = "帮我放点音乐"

    assert manager._close_turn_tool_state() is True
    assert manager._turn_has_spoken is False and manager._tool_wait_bubble is False
    # The user's words belong to the turn that just ended.
    assert manager._latest_user_utterance == ""
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


@pytest.mark.asyncio
async def test_a_task_dispatched_in_a_turn_she_started_herself_carries_no_user_words(agent_process):
    process = agent_process(lambda request: httpx.Response(200, json={"kind": "accepted", "text": "已受理。"}))
    manager = _manager()
    manager._latest_user_utterance = "帮我放点音乐"
    # The user's turn ends; later she reports a result and dispatches a follow-up on her own.
    manager._queue_turn_end("req-1")

    await manager._handle_dispatch_task_call({"task": "把下载好的文件整理进文件夹"})

    assert process.requests[0]["body"]["user_text"] == ""


def test_a_mirrored_reply_does_not_count_as_having_spoken_in_the_next_turn():
    manager = _manager()
    manager.websocket = None
    manager._turn_has_spoken = True
    manager._turn_acted_through_tools = True

    asyncio.run(manager.emit_mirror_turn_end(metadata={"mirror": True}))

    assert manager._turn_has_spoken is False and manager._turn_acted_through_tools is False
    assert "acted_through_tools" not in manager.sync_message_queue.get_nowait()


@pytest.mark.parametrize("kind", ["response", "agent_callback"])
def test_a_reply_cut_off_before_it_said_anything_leaves_nothing_for_the_next_turn(kind):
    manager = _manager()
    manager._close_reply_observation = lambda *args, **kwargs: None
    manager._close_external_reply_observation = lambda *args, **kwargs: None
    manager._current_ai_turn_observation = None
    manager._current_ai_turn_text = ""
    manager._discarded_turn_open = False
    manager._active_text_request_id = None
    manager._pending_turn_meta = None
    # She had called a tool that acts and was interrupted before saying a word.
    manager._turn_acted_through_tools = True
    manager._tool_wait_bubble = True

    assert manager._close_interrupted_offline_turn(kind) is None

    assert manager._turn_acted_through_tools is False and manager._tool_wait_bubble is False
    assert manager.sync_message_queue.empty()


def test_every_turn_end_put_on_the_queue_closes_what_the_turn_noted():
    """No turn end may leave the tool state of its turn for the next one.

    Found by reading the code: every function of the session manager that
    builds a ``turn end`` message also closes the turn's tool state (or is
    the one builder the others call).
    """
    import ast
    from pathlib import Path

    # From this file, not from wherever the tests were started.
    sources = sorted((Path(__file__).resolve().parents[2] / "main_logic" / "core").glob("*.py"))
    assert {"turn.py", "proactive.py", "streaming.py"} <= {path.name for path in sources}
    offenders: list[str] = []
    found: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            builds_turn_end = any(
                isinstance(item, ast.Dict)
                and any(
                    isinstance(key, ast.Constant) and key.value == "data"
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and value.value.startswith("turn end")
                    for key, value in zip(item.keys, item.values)
                )
                for item in ast.walk(node)
            )
            puts_on_the_queue = any(
                isinstance(item, ast.Attribute) and item.attr == "sync_message_queue" for item in ast.walk(node)
            )
            if not builds_turn_end or not puts_on_the_queue:
                # A copy for the websocket alone ends no turn for the connector.
                continue
            found.append(f"{path.name}:{node.name}")
            closes = any(
                isinstance(item, ast.Attribute) and item.attr == "_close_turn_tool_state" for item in ast.walk(node)
            )
            if not closes:
                offenders.append(f"{path.name}:{node.name}")

    # The search found what it is about; an empty one would pass for nothing.
    assert "turn.py:_queue_turn_end" in found and "proactive.py:finish_proactive_delivery" in found
    assert offenders == []


def test_a_session_end_says_whether_she_acted_in_the_turn_it_cut_off():
    manager = _manager()
    manager._turn_acted_through_tools = True

    cut_off = manager._session_end_message()
    between_turns = manager._session_end_message()

    assert cut_off == {"type": "system", "data": "session end", "acted_through_tools": True}
    assert between_turns == {"type": "system", "data": "session end"}
    assert manager._turn_acted_through_tools is False


@pytest.mark.asyncio
async def test_the_barrier_kind_of_session_end_carries_it_too():
    from main_logic.core.lifecycle import LifecycleMixin

    manager = _manager()
    manager._turn_acted_through_tools = True

    def callback() -> None:
        return None

    completion = LifecycleMixin._queue_session_end_memory_barrier(manager, callback)

    queued = manager.sync_message_queue.get_nowait()
    assert queued == {
        "type": "system",
        "data": "session end",
        "acted_through_tools": True,
        "_after_memory_settlement": callback,
        "_memory_settlement_done": completion,
    }


# ---------------------------------------------------------------------------
# When a turn is sent to be checked
# ---------------------------------------------------------------------------


def _said(text: str) -> dict:
    return {"type": "user", "data": {"data": text, "input_type": "transcript"}}


def _replied(text: str) -> dict:
    return {"type": "json", "data": {"type": "gemini_response", "text": text, "isNewMessage": True}}


@pytest.fixture
def connector(monkeypatch):
    """The sync connector loop on a queue, with the memory server and the turn check stood in for."""
    started = []

    async def memory(endpoint, name, payload, *, timeout_s, language=None, render_language=None):
        return True, "", {}

    def start():
        triggers: list[str] = []

        async def turn_check(lanlan_name, trigger, messages, *, conversation_id=None, language=None):
            triggers.append(trigger)
            return True

        monkeypatch.setattr(cross_server, "_post_memory_server", memory)
        monkeypatch.setattr(cross_server, "_publish_turn_check_with_fallback", turn_check)
        queue: asyncio.Queue = asyncio.Queue()
        started.append(
            asyncio.create_task(
                cross_server.run_sync_connector(queue, "Lan", config={"monitor": False, "bullet": False})
            )
        )
        return queue, triggers

    yield start
    for task in started:
        task.cancel()


async def _drained(queue: asyncio.Queue) -> None:
    async def poll() -> None:
        while not queue.empty():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), 2.0)
    # The item taken last is still being handled.
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ending", "expected"),
    [
        # A turn that ran to its end: checked once, unless she acted through a tool in it.
        ([{"type": "system", "data": "turn end"}], ["turn_end"]),
        ([{"type": "system", "data": "turn end", "acted_through_tools": True}], []),
        # A session that ended in the middle of the turn: the same rule.
        ([{"type": "system", "data": "session end"}], ["session_end"]),
        ([{"type": "system", "data": "session end", "acted_through_tools": True}], []),
        # A session that ended after the turn did: the turn is not checked twice.
        ([{"type": "system", "data": "turn end"}, {"type": "system", "data": "session end"}], ["turn_end"]),
        (
            [{"type": "system", "data": "turn end", "acted_through_tools": True}, {"type": "system", "data": "session end"}],
            [],
        ),
    ],
    ids=["turn-end", "turn-end-acted", "cut-off", "cut-off-acted", "ended-after-the-turn", "ended-after-an-acted-turn"],
)
async def test_a_turn_in_which_she_acted_through_a_tool_is_never_checked(connector, ending, expected):
    queue, triggers = connector()

    for item in (_said("帮我订个明早七点的闹钟"), _replied("好，我去订"), *ending):
        queue.put_nowait(item)
    await _drained(queue)

    assert triggers == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [{"type": "system", "data": "session end"}, {"type": "system", "data": "renew session"}],
    ids=["session-end", "hot-reset"],
)
async def test_the_first_turn_after_the_history_was_emptied_is_checked_when_it_is_cut_off(connector, boundary):
    # The earlier session leaves a longer history behind than the next one
    # has when it is cut off; the position the check starts from must not
    # be left pointing past the end of the new history.
    queue, triggers = connector()
    earlier = [
        _said("帮我查一下上海的天气"), _replied("好，我去查"), {"type": "system", "data": "turn end"},
        _said("再查一下北京的"), _replied("好的"), {"type": "system", "data": "turn end"},
    ]
    for item in (*earlier, boundary):
        queue.put_nowait(item)
    await _drained(queue)
    assert triggers == ["turn_end", "turn_end"]

    # The next session: one exchange, ended before its turn end.
    for item in (_said("帮我订个明早七点的闹钟"), _replied("好，我去订"), {"type": "system", "data": "session end"}):
        queue.put_nowait(item)
    await _drained(queue)

    assert triggers == ["turn_end", "turn_end", "session_end"]


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
