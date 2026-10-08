"""Tool calls a reply wrote as text never travel through memory again.

When native tool calls stopped, the model wrote them into its replies
(``declaration:default_api:pvz_instruction{...}``, ``asynccall:...{...}``).
Those replies were stored like any other line, and the next session started
with them in its prompt: a ready-made example of calling a tool by writing it.
The markup is cut where memory renders recent history and where core primes
its own cache, so lines already stored need no migration, and before a reply
is handed to memory, so new ones are stored clean.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_LEAKED = "好的！declaration:default_api:pvz_instruction{instruction:立刻在第四行使用樱桃炸弹，…}"
_ASYNC_ONLY = "asynccall:pvz_instruction{instruction:种豌豆}asynccall:pvz_instruction{instruction:补坚果}"
_BARE = "我来pvz_instruction(instruction='第三行种坚果')。"


def _render_recent(history):
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master", None, None, None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None, None, None, None,
        )),
    )
    fake_recent = SimpleNamespace(aget_recent_history=AsyncMock(return_value=history))
    return memory_server, fake_config, fake_recent


async def test_recent_history_renders_character_lines_without_call_markup():
    history = [
        SimpleNamespace(type="human", content="帮我打僵尸 asynccall:x{y:1}"),
        SimpleNamespace(type="ai", content=_LEAKED),
        SimpleNamespace(type="ai", content=_ASYNC_ONLY),
        SimpleNamespace(type="ai", content=[{"type": "text", "text": _LEAKED}]),
        SimpleNamespace(type="ai", content=_BARE),
    ]
    memory_server, fake_config, fake_recent = _render_recent(history)
    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    lines = [line for line in result.splitlines() if " | " in line]
    assert lines == [
        "Master | 帮我打僵尸 asynccall:x{y:1}",
        "test_char | 好的！",
        "test_char | 好的！",
        # Memory has no tool registry: a bare name(...) is not judged there.
        f"test_char | {_BARE}",
    ], "the user's own words stay; a line that was only markup is dropped"
    assert history[1].content == _LEAKED, "the stored message is not rewritten"
    assert history[3].content == [{"type": "text", "text": _LEAKED}]


def test_the_shared_render_helper_cuts_markup_before_the_screen_projection():
    from app.memory_server.routes import _screen_guarded_recent_history
    from utils.llm_client import AIMessage, HumanMessage

    stored = [HumanMessage(content="hi"), AIMessage(content=_LEAKED), AIMessage(content=_ASYNC_ONLY)]
    rendered = _screen_guarded_recent_history(stored)
    assert [m.content for m in rendered] == ["hi", "好的！"]
    assert stored[1].content == _LEAKED


def _cache(owner_tools=None):
    from main_logic.core.notify import NotifyMixin

    owner = SimpleNamespace(lanlan_name="YUI", master_name="Alice", user_language="zh")
    if owner_tools is not None:
        owner.list_tools = lambda: list(owner_tools)
    cache = [
        {"role": "Alice", "text": "你在干嘛 default_api:x{y:1}"},
        {"role": "YUI", "text": _LEAKED},
        {"role": "YUI", "text": _ASYNC_ONLY},
        {"role": "YUI", "text": _BARE},
    ]
    return NotifyMixin._convert_cache_to_str(owner, cache).splitlines()


def test_the_primed_cache_uses_the_registered_tool_names():
    assert _cache(owner_tools=["pvz_instruction"]) == [
        "Alice | 你在干嘛 default_api:x{y:1}",
        "YUI | 好的！",
        "YUI | 我来。",
    ]


def test_the_primed_cache_without_a_registry_cuts_prefixed_markup_only():
    assert _cache() == [
        "Alice | 你在干嘛 default_api:x{y:1}",
        "YUI | 好的！",
        f"YUI | {_BARE}",
    ]


# ── Write side: a reply is cleaned before it is handed to memory ────────────

def _gemini(text):
    return {"type": "json", "data": {"type": "gemini_response", "text": text, "isNewMessage": False}}


async def _stored_replies(monkeypatch, chunks, tool_names_provider=None):
    import asyncio
    import json

    from main_logic import cross_server

    posted = []

    async def post_memory(endpoint, name, payload, *, timeout_s, language=None, render_language=None):
        posted.append(payload)
        return True, "", {}

    async def publish_analyze(*_args, **_kwargs):
        return True

    monkeypatch.setattr(cross_server, "_post_memory_server", post_memory)
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", publish_analyze)
    queue = asyncio.Queue()
    done = asyncio.get_running_loop().create_future()
    connector = asyncio.create_task(cross_server.run_sync_connector(
        queue, "YUI", config={"monitor": False, "bullet": False},
        tool_names_provider=tool_names_provider,
    ))
    queue.put_nowait({"type": "user", "data": {"data": "打僵尸", "input_type": "transcript"}})
    for chunk in chunks:
        queue.put_nowait(_gemini(chunk))
    queue.put_nowait({"type": "system", "data": "session end", "_memory_settlement_done": done})
    try:
        await asyncio.wait_for(done, timeout=1.0)
    finally:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
    sent = [m for payload in posted for m in payload]
    return [m["content"][0]["text"] for m in sent if m["role"] == "assistant"], json.dumps(
        sent, ensure_ascii=False,
    )


async def test_a_reply_with_a_spoken_call_reaches_memory_without_it(monkeypatch):
    """Realtime transcripts and offline replies alike: the call may span the
    stream chunks, so it is cut from the whole reply at the flush."""
    replies, sent = await _stored_replies(
        monkeypatch,
        ["好的！declaration:default_", "api:pvz_instruction{instruction:丢樱", "桃炸弹} 我来pvz_instruction(",
         "instruction='种坚果')。"],
        tool_names_provider=lambda: ["pvz_instruction"],
    )
    assert len(replies) == 1
    assert replies[0].endswith("]好的！我来。")
    assert "pvz_instruction" not in sent


async def test_without_a_tool_registry_only_prefixed_calls_are_cut(monkeypatch):
    def broken():
        raise RuntimeError("no session")

    replies, _sent = await _stored_replies(
        monkeypatch,
        ["冲asynccall:pvz_instruction{instruction:a}", "，我来pvz_instruction(instruction='b')"],
        tool_names_provider=broken,
    )
    assert replies[0].endswith("]冲，我来pvz_instruction(instruction='b')")


async def test_a_reply_that_was_only_a_spoken_call_is_not_stored(monkeypatch):
    replies, _sent = await _stored_replies(
        monkeypatch, ["asynccall:pvz_instruction{instruction:a}"],
    )
    assert replies == [], "not even as a bare time stamp"


def test_a_call_split_across_text_parts_is_still_cut():
    from app.memory_server.routes import _screen_guarded_recent_history
    from utils.llm_client import AIMessage, HumanMessage

    stored = [HumanMessage(content="hi"), AIMessage(content=[
        {"type": "text", "text": "好的 async"},
        {"type": "text", "text": "call:pvz_instruction{instruction:丢"},
        {"type": "text", "text": "樱桃} 完毕"},
    ])]
    rendered = _screen_guarded_recent_history(stored)
    assert [part["text"] for part in rendered[1].content] == ["好的 ", "", " 完毕"]
