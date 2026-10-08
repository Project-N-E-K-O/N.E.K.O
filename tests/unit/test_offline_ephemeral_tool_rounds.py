"""``prompt_ephemeral`` saves its tool rounds the way ``stream_text`` does.

A proactive / callback turn used to run its tool loop on a copy of the history
(``history + [instruction]``) and save only its reply text. Its tool rounds,
the only record of which calls really ran, were thrown away with the copy, and
the pre-tool text was written into the reply. After a few proactive turns the
history held nothing but "I'll drop the cherry bomb" with no call behind it,
and the model went on to write its calls into the reply as text.

Only the provider transport is faked (``tests.unit.test_offline_turn_
cancellation_e2e._client``): the visible filter, the tool loop, the executor
and the request view are production code.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIConnectionError

import main_logic.omni_offline_client._genai_support as _ofc_genai
from config.prompts.prompts_tool import TOOL_ROUND_PROMPT_PLACEHOLDER
from main_logic.tool_calling import ToolResult
from tests.unit.test_offline_turn_cancellation_e2e import (
    _client, _emitted, _gemini_calls, _history_shape, _text, _tool_calls,
)
from tests.unit.test_tool_calling import _GenaiChunk, _GenaiPart
from utils.llm_client import AIMessage, HumanMessage, SystemMessage

pytestmark = pytest.mark.unit

_INSTRUCTION = "======[系统通知] 第四行来了一排僵尸======"
_SLEEP = "main_logic.omni_offline_client._lifecycle.asyncio.sleep"


@pytest.fixture(autouse=True)
def _genai_available(monkeypatch):
    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)


async def _no_backoff(_delay):
    """The retry ladder without its wall clock."""


def _recording_handler(executed):
    async def handler(call):
        executed.append(call.call_id)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})
    return handler


def _seeded(client):
    client._conversation_history += [HumanMessage(content="开局"), AIMessage(content="好呀")]
    return client


def _round_step(provider, *ids, text=""):
    if provider == "gemini":
        return [_gemini_calls(*ids, text=text or None)]
    return ([_text(text)] if text else []) + [_tool_calls(*ids)]


def _text_step(provider, text):
    if provider == "gemini":
        return [_GenaiChunk([_GenaiPart(text=text)])]
    return [_text(text), _text("", "stop")]


def _saved_text(client):
    return json.dumps(
        [m if isinstance(m, dict) else m.content for m in client._conversation_history],
        ensure_ascii=False,
    )


def _content_text(content):
    return "".join(getattr(part, "text", None) or "" for part in (content.parts or []))


def _has_call(content):
    return any(getattr(part, "function_call", None) for part in (content.parts or []))


def _assert_calls_follow_a_user_turn(request, provider):
    """Gemini rejects a function call turn that does not come right after a
    user turn or a function response; OpenAI-compatible Gemini gateways
    forward the same history, so the OpenAI view is held to it too."""
    if provider == "gemini":
        for index, content in enumerate(request):
            if _has_call(content):
                assert index > 0 and request[index - 1].role == "user", (
                    f"function call turn at {index} follows {request[index - 1].role}"
                )
        return
    for index, message in enumerate(request):
        if isinstance(message, dict) and message.get("tool_calls"):
            before = request[index - 1]
            assert isinstance(before, HumanMessage) or (
                isinstance(before, dict) and before.get("role") in ("user", "tool")
            ), f"tool call turn at {index} follows {before!r}"


# ── The round is saved, the instruction is not ──────────────────────────────

@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_a_callback_saves_its_tool_round_but_never_its_instruction(provider):
    executed = []
    client = _seeded(_client(provider, handler=_recording_handler(executed)))
    client.script = [
        _round_step(provider, "c1", text="我来丢樱桃炸弹"),
        _text_step(provider, "丢好了。"),
    ]
    assert await client.prompt_ephemeral(_INSTRUCTION) is True

    assert executed == ["c1"]
    assert _history_shape(client) == [
        ("human", "开局", None),
        ("ai", "好呀", None),
        ("assistant", "我来丢樱桃炸弹", ["c1"]),
        ("tool", json.dumps({"ok": True}), None),
        ("ai", "丢好了。", None),
    ], "the pre-tool text is saved once, inside its round"
    assert _INSTRUCTION not in _saved_text(client)
    assert _emitted(client) == ["我来丢樱桃炸弹", "丢好了。"]
    assert client._conversation_history[-1].additional_kwargs == {"dialog_source": "proactive"}


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_the_instruction_sits_right_before_the_round_it_prompted(provider):
    """The follow-up request inside the turn still carries the instruction,
    between the earlier history and the round that answers it."""
    client = _seeded(_client(provider, handler=_recording_handler([])))
    client.script = [_round_step(provider, "c1"), _text_step(provider, "丢好了。")]
    assert await client.prompt_ephemeral(_INSTRUCTION) is True

    assert len(client.requests) == 2
    follow_up = client.requests[1]
    if provider == "gemini":
        texts = [_content_text(c) for c in follow_up]
        at = texts.index(_INSTRUCTION)
        assert follow_up[at].role == "user" and _has_call(follow_up[at + 1])
        assert texts.count(_INSTRUCTION) == 1
    else:
        at = next(i for i, m in enumerate(follow_up)
                  if isinstance(m, HumanMessage) and m.content == _INSTRUCTION)
        assert follow_up[at + 1]["tool_calls"][0]["id"] == "c1"
        assert follow_up[at + 2]["role"] == "tool"
    _assert_calls_follow_a_user_turn(follow_up, provider)


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_the_next_request_seats_the_saved_round_after_a_user_turn(provider):
    """Once the turn is over the saved round follows an assistant message;
    the next request (any turn) puts a stand-in user turn before it. The
    stand-in never reaches history."""
    client = _seeded(_client(provider, handler=_recording_handler([])))
    client.script = [
        _round_step(provider, "c1", text="我来丢"),
        _text_step(provider, "丢好了。"),
        _text_step(provider, "嗯嗯"),
    ]
    assert await client.prompt_ephemeral(_INSTRUCTION) is True
    await client.stream_text("下一句")

    assert len(client.requests) == 3
    request = client.requests[2]
    _assert_calls_follow_a_user_turn(request, provider)
    stand_in = TOOL_ROUND_PROMPT_PLACEHOLDER["zh"]
    if provider == "gemini":
        assert [_content_text(c) for c in request].count(stand_in) == 1
    else:
        assert request.count({"role": "user", "content": stand_in}) == 1
    assert stand_in not in _saved_text(client)


# ── Retries, cancellation, unsaved replies ──────────────────────────────────

async def test_a_retried_attempt_never_writes_the_round_twice():
    """Attempt 1 runs the call and then fails before any text; attempt 2
    sees the saved round (after the instruction) and answers from it."""
    executed = []
    client = _seeded(_client(handler=_recording_handler(executed)))
    client.script = [
        [_tool_calls("c1")],
        APIConnectionError(request=httpx.Request("POST", "http://provider.invalid")),
        [_text("丢好了。"), _text("", "stop")],
    ]
    with patch(_SLEEP, _no_backoff):
        assert await client.prompt_ephemeral(_INSTRUCTION) is True

    assert executed == ["c1"]
    assert len(client.requests) == 3
    assert _history_shape(client)[2:] == [
        ("assistant", "", ["c1"]),
        ("tool", json.dumps({"ok": True}), None),
        ("ai", "丢好了。", None),
    ]
    retry = client.requests[2]
    rounds = [m for m in retry if isinstance(m, dict) and m.get("tool_calls")]
    assert len(rounds) == 1
    at = retry.index(rounds[0])
    assert isinstance(retry[at - 1], HumanMessage) and retry[at - 1].content == _INSTRUCTION


async def test_a_reply_cut_after_its_round_lands_before_the_interrupter():
    """The round stays where it ran; the post-tool text that was shown goes
    after it and before the user turn that cut the reply."""
    client = _seeded(_client(handler=_recording_handler([])))

    async def interrupt():
        await client.handle_interruption()
        client._conversation_history.append(HumanMessage(content="Q-new"))

    client.script = [
        [_text("我来丢"), _tool_calls("c1")],
        [_text("丢好"), interrupt, _text("了。"), _text("", "stop")],
    ]
    assert await client.prompt_ephemeral(_INSTRUCTION) is True
    assert _history_shape(client)[2:] == [
        ("assistant", "我来丢", ["c1"]),
        ("tool", json.dumps({"ok": True}), None),
        ("ai", "丢好", None),
        ("human", "Q-new", None),
    ]


async def test_a_task_cancelled_inside_a_round_keeps_the_pretool_text_once():
    """Cancelled during the second call of a batch, the round keeps the call
    that ran and its sentinel is lost: the shown pre-tool text is already in
    that round, so it is not written again as a reply."""
    async def handler(call):
        if call.call_id == "c2":
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _seeded(_client(handler=handler))
    client.script = [[_text("我来丢"), _tool_calls("c1", "c2")]]
    turn = asyncio.create_task(client.prompt_ephemeral(_INSTRUCTION))
    await asyncio.gather(turn, return_exceptions=True)
    assert turn.cancelled()
    assert _history_shape(client)[2:] == [
        ("assistant", "我来丢", ["c1"]),
        ("tool", "{}", None),
    ]


async def test_a_reply_that_is_not_kept_leaves_no_round_behind():
    client = _seeded(_client(handler=_recording_handler([])))
    before = list(client._conversation_history)
    client.script = [[_text("我来丢"), _tool_calls("c1")], [_text("好了。"), _text("", "stop")]]
    assert await client.prompt_ephemeral(
        "avatar", completion_mode="response", persist_response=False,
    ) is True
    assert client._conversation_history == before


# ── The request view repair ─────────────────────────────────────────────────

def _round(call_id):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": "lookup", "arguments": "{}"},
    }]}


def _reply(call_id):
    return {"role": "tool", "tool_call_id": call_id, "name": "lookup", "content": "{}"}


@pytest.mark.parametrize("language,expected", [
    ("zh", TOOL_ROUND_PROMPT_PLACEHOLDER["zh"]),
    ("en", TOOL_ROUND_PROMPT_PLACEHOLDER["en"]),
])
def test_a_round_after_an_assistant_message_gets_a_stand_in(language, expected):
    client = _client()
    client._user_language_provider = lambda: language
    saved = [SystemMessage(content="sys"), HumanMessage(content="A"),
             AIMessage(content="好呀"), _round("c1"), _reply("c1"),
             AIMessage(content="丢好了"), HumanMessage(content="B")]
    snapshot = list(saved)
    view = client._dialog_messages_for_provider(saved)
    assert view[3] == {"role": "user", "content": expected}
    assert view[:3] == saved[:3] and all(a is b for a, b in zip(view[4:], saved[3:]))
    assert saved == snapshot, "history itself is never rewritten"


def test_a_round_after_a_user_turn_or_a_tool_reply_is_left_alone():
    client = _client()
    saved = [SystemMessage(content="sys"), HumanMessage(content="A"),
             _round("c1"), _reply("c1"), _round("c2"), _reply("c2"),
             AIMessage(content="好了"), HumanMessage(content="B")]
    assert client._dialog_messages_for_provider(saved) is saved


def test_a_round_right_after_the_system_prompt_gets_a_stand_in():
    """The system prompt leaves ``contents`` on the native path, so the call
    would be the first turn."""
    client = _client()
    saved = [SystemMessage(content="sys"), _round("c1"), _reply("c1")]
    view = client._dialog_messages_for_provider(saved)
    assert [m.get("role") if isinstance(m, dict) else m.type for m in view] == [
        "system", "user", "assistant", "tool",
    ]


# ── Second review round ─────────────────────────────────────────────────────

@pytest.mark.parametrize("post_text", [True, False])
async def test_a_reply_cut_earlier_lands_before_a_later_callbacks_saved_round(post_text):
    """A typed reply is cut while its text send is still awaiting; a callback
    begins meanwhile (nothing is in progress any more) and saves its round.
    The cut reply then commits what it showed: before the callback's round,
    not between the round and its reply, nor after a round with no reply."""
    gate = asyncio.Event()
    cut = asyncio.Event()

    async def on_text_delta(text, is_first, **_kw):
        if text == "先说一句" and not cut.is_set():
            await client.handle_interruption()
            cut.set()
            await gate.wait()

    client = _seeded(_client(handler=_recording_handler([])))
    client.on_text_delta = AsyncMock(side_effect=on_text_delta)
    client.script = [
        [_text("先说一句"), _text("还没说完"), _text("", "stop")],
        [_tool_calls("p1")],
        [_text("丢好了。"), _text("", "stop")] if post_text else [_text("", "stop")],
    ]
    typed = asyncio.create_task(client.stream_text("Q"))
    await cut.wait()
    await client.prompt_ephemeral(_INSTRUCTION)
    gate.set()
    await typed

    expected = [
        ("human", "Q", None),
        ("ai", "先说一句", None),
        ("assistant", "", ["p1"]),
        ("tool", json.dumps({"ok": True}), None),
    ]
    if post_text:
        expected.append(("ai", "丢好了。", None))
    assert _history_shape(client)[2:] == expected


async def test_round_boundaries_outlive_many_later_callbacks():
    """A later callback's saved round stays known as a boundary however many
    callbacks without rounds run while the cut reply is still suspended."""
    gate = asyncio.Event()
    cut = asyncio.Event()

    async def on_text_delta(text, is_first, **_kw):
        if text == "先说一句" and not cut.is_set():
            await client.handle_interruption()
            cut.set()
            await gate.wait()

    client = _seeded(_client(handler=_recording_handler([])))
    client.on_text_delta = AsyncMock(side_effect=on_text_delta)
    later = 25
    client.script = [
        [_text("先说一句"), _text("还没说完"), _text("", "stop")],
        [_tool_calls("p1")],
        [_text("", "stop")],
    ]
    for index in range(later):
        client.script += [[_tool_calls(f"q{index}")], [_text("", "stop")]]
    client.script += [[_text("", "stop")]] * 5
    typed = asyncio.create_task(client.stream_text("Q"))
    await cut.wait()
    await client.prompt_ephemeral(_INSTRUCTION)
    for _ in range(later):
        await client.prompt_ephemeral(_INSTRUCTION)
    for _ in range(5):
        await client.prompt_ephemeral(_INSTRUCTION)
    gate.set()
    await typed

    assert _history_shape(client)[2:6] == [
        ("human", "Q", None),
        ("ai", "先说一句", None),
        ("assistant", "", ["p1"]),
        ("tool", json.dumps({"ok": True}), None),
    ]
    assert len(client._proactive_turn_rounds) == 1 + later, (
        "every turn that saved a round keeps its entry; turns without one keep none"
    )


async def test_a_callback_with_images_skips_tools_once_images_refused_them():
    """The images ride the instruction, which only the request view holds:
    the "refuses tools with images" memory must judge that view."""
    from tests.unit.test_offline_provider_frame_publish import _png_b64

    client = _seeded(_client(handler=_recording_handler([])))
    client._openai_tools_unsupported_with_images = True
    sent_tools = []
    astream = client.llm.astream

    def recording_astream(messages, **kwargs):
        sent_tools.append("tools" in kwargs)
        return astream(messages, **kwargs)

    client.llm.astream = recording_astream
    client.script = [[_text("看到了。"), _text("", "stop")]]
    assert await client.prompt_ephemeral(_INSTRUCTION, images=[_png_b64(4, 4, (1, 2, 3))]) is True
    assert sent_tools == [False]
