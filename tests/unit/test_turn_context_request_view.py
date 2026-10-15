"""Passive turn context in whole offline turns: prompt-cache friendly, never saved.

Only the provider transport is faked (``tests.unit.test_offline_turn_
cancellation_e2e._client``); ``stream_text``, the request view, the tool loop
and the executor are production code. Pinned here:

* a turn carrying context sends the same request prefix as a turn without
  it, byte for byte, with the blocks only after the user's message;
* the ephemeral block (corpus, memories) is gone from the next turn's
  request; the resident block (knowledge) stays at the same place, so the
  next request extends the previous one;
* neither block ever enters the saved history;
* a resident block is kept only once the provider answered its turn, follows
  its user message when that message is replaced, goes when the message
  leaves the history, and is trimmed oldest-first over its budget.
"""
import json

import pytest

import main_logic.omni_offline_client._genai_support as _ofc_genai
import main_logic.omni_offline_client._turn_context as _turn_context_module
from main_logic.omni_offline_client import TurnContext
from main_logic.tool_calling import ToolResult
from tests.unit.test_offline_turn_cancellation_e2e import (
    _client, _gemini_calls, _text, _tool_calls,
)
from tests.unit.test_tool_calling import _GenaiChunk, _GenaiPart
from utils.llm_client import AIMessage, HumanMessage

pytestmark = pytest.mark.unit

_KNOWLEDGE = "======以下为参考知识======\n- [芙宁娜] 水神\n======以上为参考知识======"
_TURN = "======以下为本轮参考======\n往事\n======以上为本轮参考======"


@pytest.fixture(autouse=True)
def _genai_available(monkeypatch):
    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)


def _context(*, resident=_KNOWLEDGE, ephemeral=_TURN, ids=("pack/芙宁娜",), tokens=50):
    return TurnContext(
        resident=HumanMessage(content=resident) if resident else None,
        resident_ids=tuple(ids) if resident else (),
        resident_tokens=tokens if resident else 0,
        ephemeral=HumanMessage(content=ephemeral) if ephemeral else None,
    )


def _text_step(provider, text):
    if provider == "gemini":
        return [_GenaiChunk([_GenaiPart(text=text)])]
    return [_text(text), _text("", "stop")]


def _round_step(provider, *ids):
    if provider == "gemini":
        return [_gemini_calls(*ids)]
    return [_tool_calls(*ids)]


def _view(request, provider):
    """(role, text) per request message, comparable across providers."""
    out = []
    for item in request:
        if provider == "gemini":
            text = "".join(getattr(part, "text", None) or "" for part in (item.parts or []))
            calls = [part.function_call.name for part in (item.parts or []) if getattr(part, "function_call", None)]
            out.append((item.role, text, tuple(calls)))
        elif isinstance(item, dict):
            calls = tuple(call["id"] for call in item.get("tool_calls") or ())
            out.append((item.get("role"), str(item.get("content") or ""), calls))
        else:
            out.append((item.type, item.content if isinstance(item.content, str) else json.dumps(item.content), ()))
    return out


def _saved(client):
    return json.dumps(
        [m if isinstance(m, dict) else m.content for m in client._conversation_history],
        ensure_ascii=False,
    )


def _handler():
    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})
    return handler


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_context_turn_extends_the_normal_prefix_and_the_next_turn_extends_it(provider):
    client = _client(provider, handler=_handler())
    client.script = [
        _text_step(provider, "回答一"),
        _text_step(provider, "回答二"),
        _text_step(provider, "回答三"),
    ]
    await client.stream_text("第一句")
    await client.stream_text("第二句", turn_context=_context())
    await client.stream_text("第三句")

    normal, with_context, after = (_view(r, provider) for r in client.requests)
    # The context turn's request starts with everything the normal turn sent.
    assert with_context[:len(normal)] == normal
    tail = [text for _role, text, _calls in with_context[len(normal):]]
    assert tail == ["回答一", "第二句", _KNOWLEDGE, _TURN], "blocks only after the user's message"
    # The next turn extends the context turn's request up to the knowledge
    # block: the resident block stays in place, the ephemeral one is gone.
    kept = with_context.index(next(item for item in with_context if item[1] == _TURN))
    assert after[:kept] == with_context[:kept]
    assert [text for _role, text, _calls in after[kept:]] == ["回答二", "第三句"]
    assert all(text != _TURN for _role, text, _calls in after)
    # Nothing of either block was saved.
    assert _KNOWLEDGE not in _saved(client) and _TURN not in _saved(client)
    assert client.resident_turn_context_ids() == ["pack/芙宁娜"]


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_every_request_of_a_tool_turn_carries_both_blocks_before_its_round(provider):
    client = _client(provider, handler=_handler())
    client.script = [
        _round_step(provider, "c1"),
        _text_step(provider, "查好了"),
        _text_step(provider, "下一轮"),
    ]
    await client.stream_text("帮我查一下", turn_context=_context())
    await client.stream_text("谢谢")

    first, follow_up, next_turn = (_view(r, provider) for r in client.requests)
    assert follow_up[:len(first)] == first, "the tool loop's follow-up extends the first request"
    texts = [text for _role, text, _calls in follow_up]
    user_at = texts.index("帮我查一下")
    assert texts[user_at + 1:user_at + 3] == [_KNOWLEDGE, _TURN]
    assert any(calls for _role, _text_, calls in follow_up[user_at + 3:]), "the round follows the blocks"
    next_texts = [text for _role, text, _calls in next_turn]
    assert _KNOWLEDGE in next_texts and _TURN not in next_texts
    assert next_texts.index(_KNOWLEDGE) == next_texts.index("帮我查一下") + 1
    assert _KNOWLEDGE not in _saved(client) and _TURN not in _saved(client)


async def test_a_turn_the_provider_never_answered_keeps_no_resident_block():
    client = _client("openai", handler=_handler())
    client.script = [ValueError("boom"), _text_step("openai", "好的")]
    await client.stream_text("第一句", turn_context=_context())
    assert client.resident_turn_context_ids() == []
    await client.stream_text("第二句")
    assert all(text != _KNOWLEDGE for _r, text, _c in _view(client.requests[-1], "openai"))


async def test_the_resident_block_follows_its_replaced_user_message():
    client = _client("openai", handler=_handler())
    client.script = [_text_step("openai", "回答"), _text_step("openai", "再答")]
    await client.stream_text(
        "隐藏的脚手架提示", history_replacement_text="用户看得见的原话", turn_context=_context(),
    )
    await client.stream_text("下一句")
    texts = [text for _r, text, _c in _view(client.requests[-1], "openai")]
    assert "隐藏的脚手架提示" not in texts
    assert texts[texts.index("用户看得见的原话") + 1] == _KNOWLEDGE


async def test_a_wiped_history_drops_the_resident_block():
    client = _client("openai", handler=_handler())
    client.script = [_text_step("openai", "回答"), _text_step("openai", "再答")]
    await client.stream_text("第一句", turn_context=_context())
    client._conversation_history = [client._conversation_history[0]]
    assert client.resident_turn_context_ids() == []
    await client.stream_text("重新开始")
    assert all(text != _KNOWLEDGE for _r, text, _c in _view(client.requests[-1], "openai"))


def test_resident_blocks_over_budget_drop_the_oldest_first(monkeypatch):
    monkeypatch.setattr(_turn_context_module, "TURN_CONTEXT_RESIDENT_KNOWLEDGE_MAX_TOKENS", 100)
    client = _client("openai")
    anchors = [HumanMessage(content=f"问题{i}") for i in range(3)]
    client._conversation_history += [item for anchor in anchors for item in (anchor, AIMessage(content="答"))]
    entries = [
        client._register_resident_context(anchor, _context(ids=(f"card{i}",), tokens=40))
        for i, anchor in enumerate(anchors)
    ]
    for entry in entries:
        client._settle_resident_context(entry, delivered=True)
    assert client.resident_turn_context_ids() == ["card1", "card2"]


def test_a_block_alone_over_budget_is_still_kept(monkeypatch):
    monkeypatch.setattr(_turn_context_module, "TURN_CONTEXT_RESIDENT_KNOWLEDGE_MAX_TOKENS", 10)
    client = _client("openai")
    anchor = HumanMessage(content="问题")
    client._conversation_history.append(anchor)
    entry = client._register_resident_context(anchor, _context(tokens=40))
    client._settle_resident_context(entry, delivered=True)
    assert client.resident_turn_context_ids() == ["pack/芙宁娜"]


def test_the_resident_block_follows_its_message_when_old_images_are_evicted():
    client = _client("openai")

    def image_turn(text):
        return HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "text", "text": text},
        ])

    oldest = image_turn("第一张图")
    client._conversation_history += [oldest, AIMessage(content="看到了"), image_turn("第二张图")]
    entry = client._register_resident_context(oldest, _context())
    client._settle_resident_context(entry, delivered=True)

    client._evict_old_images(keep_turns=1)

    replaced = client._conversation_history[1]
    assert replaced is not oldest and replaced.content == "第一张图"
    assert client.resident_turn_context_ids() == ["pack/芙宁娜"]
    view = client._dialog_messages_for_provider(client._conversation_history)
    assert view[view.index(replaced) + 1].content == _KNOWLEDGE



def test_a_block_still_waiting_on_its_provider_does_not_exclude_its_cards():
    client = _client("openai")
    anchor = HumanMessage(content="问题")
    client._conversation_history.append(anchor)
    entry = client._register_resident_context(anchor, _context())
    assert client.resident_turn_context_ids() == [], "a superseding turn may still bring the card in"
    client._settle_resident_context(entry, delivered=True)
    assert client.resident_turn_context_ids() == ["pack/芙宁娜"]



def test_a_block_whose_cards_another_turn_delivered_meanwhile_is_dropped():
    client = _client("openai")
    first, second = HumanMessage(content="问题一"), HumanMessage(content="问题二")
    client._conversation_history += [first, AIMessage(content="答"), second]
    earlier = client._register_resident_context(first, _context())
    later = client._register_resident_context(second, _context())
    client._settle_resident_context(earlier, delivered=True)
    client._settle_resident_context(later, delivered=True)
    assert client.resident_turn_context_ids() == ["pack/芙宁娜"]
    view = client._dialog_messages_for_provider(client._conversation_history)
    assert sum(1 for item in view if getattr(item, "content", None) == _KNOWLEDGE) == 1



@pytest.mark.parametrize("with_resident", [False, True])
@pytest.mark.parametrize("with_ephemeral", [False, True])
def test_context_blocks_never_move_the_screen_projection_boundary(with_resident, with_ephemeral):
    from tests.unit.test_screen_history_stopgap import _COMMENT_A, _COMMENT_B

    # A chain spread over the assistant run right before the user's message:
    # only judged when that message is the turn's boundary.
    client = _client("openai")
    user = HumanMessage(content="那你继续说说。")
    client._conversation_history += [
        HumanMessage(content="陪我聊聊。"), AIMessage(content=_COMMENT_A), AIMessage(content=_COMMENT_B), user,
    ]
    plain = client._dialog_messages_for_provider(client._conversation_history)
    plain_ai = [item.content for item in plain if getattr(item, "type", None) == "ai"]
    assert len(plain_ai) == 1, "the run is cut to one comment on a plain turn"

    context = _context(
        resident=_KNOWLEDGE if with_resident else None,
        ephemeral=_TURN if with_ephemeral else None,
    )
    if with_resident:
        entry = client._register_resident_context(user, context)
        client._settle_resident_context(entry, delivered=True)
    instruction = context.ephemeral
    if instruction is not None:
        instruction.metadata["turn_context"] = "ephemeral"
    view = client._dialog_messages_for_provider(
        client._conversation_history, instruction=instruction, own_rounds=[],
    )
    view_ai = [item.content for item in view if getattr(item, "type", None) == "ai"]
    assert view_ai == plain_ai, "the context blocks are not the turn boundary"


def test_an_earlier_block_the_new_one_covers_is_dropped():
    client = _client("openai")
    first, second = HumanMessage(content="问题一"), HumanMessage(content="问题二")
    client._conversation_history += [first, AIMessage(content="答"), second]
    earlier = client._register_resident_context(first, _context(resident="块一", ids=("A",)))
    client._settle_resident_context(earlier, delivered=True)
    later = client._register_resident_context(second, _context(resident="块二", ids=("A", "B")))
    client._settle_resident_context(later, delivered=True)
    assert sorted(client.resident_turn_context_ids()) == ["A", "B"]
    contents = [getattr(item, "content", None) for item in client._dialog_messages_for_provider(client._conversation_history)]
    assert "块一" not in contents and "块二" in contents
