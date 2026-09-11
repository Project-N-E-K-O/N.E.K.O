"""Injecting a tool's picture into the turn, then taking it back out.

The taking-out half is the load-bearing one. ``_conversation_history`` has no
image eviction of its own, and the token counter renders an image part as a
short placeholder — so a base64 frame left behind would ride along in every
later request while looking free to the truncator.
"""

from __future__ import annotations

import json

import pytest

from main_logic.omni_offline_client import _tools as tools_module
from main_logic.omni_offline_client._tools import _ToolingMixin
from main_logic.tool_calling import ToolCall, ToolImage, ToolResult
from utils.llm_client import LLMStreamChunk


class _Client(_ToolingMixin):
    """Just enough of OmniOfflineClient to exercise the tool loop's history
    writes.

    The language provider is pinned rather than left off. Every model-facing
    string this file asserts on now comes from ``config.prompts.prompts_tool``,
    and an absent provider resolves through ``resolve_global_language()`` --
    which another test file binds to the machine's real locale for the rest of
    the pytest process (``test_user_directives`` installs the runtime
    bindings). Under ``pytest-randomly`` that would make the captions below
    depend on file order. Locale *selection* is pinned in
    test_tool_image_prompt_locale.py, which owns the provider.
    """

    def __init__(self, results: dict[str, ToolResult] | None = None):
        self._results = results or {}
        self._user_language_provider = lambda: "en"
        self.on_tool_call = self._handle

    async def _handle(self, call: ToolCall) -> ToolResult:
        return self._results.get(call.name) or ToolResult(
            call_id=call.call_id, name=call.name, output={"ok": True},
        )


class _Call:
    """Shape of an aggregated tool call as ``collect_tool_calls`` yields it."""

    def __init__(self, name="demo_tool", id="call_1", arguments="{}"):
        self.name = name
        self.id = id
        self.arguments = arguments
        self.extra_content = None


def _image_result(name="demo_tool", output=None, images=None) -> ToolResult:
    return ToolResult(
        call_id="call_1",
        name=name,
        output={"ok": True} if output is None else output,
        images=list(images or []),
    )


def _image_messages(messages):
    return [
        m for m in messages
        if isinstance(m, dict)
        and isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]


# ------------------------------------------------------------------ injection


@pytest.mark.asyncio
async def test_no_images_appends_nothing_extra():
    client = _Client()
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    assert [m["role"] for m in messages] == ["assistant", "tool"]
    assert getattr(client, "_pending_tool_image_slots", []) == []


@pytest.mark.asyncio
async def test_images_are_appended_as_a_multimodal_user_message():
    result = _image_result(images=[
        ToolImage(data_b64="IMGDATA", mime="image/jpeg", vision_prompt="watch the minimap"),
    ])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])

    assert [m["role"] for m in messages] == ["assistant", "tool", "user"]
    content = messages[-1]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,IMGDATA"
    assert content[1] == {
        "type": "text",
        "text": "Tool image from demo_tool (call_id=call_1): watch the minimap",
    }


@pytest.mark.asyncio
async def test_injected_message_is_a_plain_dict():
    """The neighbouring assistant/tool appends in this function are dicts and
    the history is a mixed list; following the local idiom keeps the tool loop
    readable."""
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    assert isinstance(messages[-1], dict)


@pytest.mark.asyncio
async def test_image_without_a_prompt_still_gets_a_text_part():
    """A bare image part with no text alongside it is rejected by some
    providers, so there is always a caption."""
    result = _image_result(images=[ToolImage(data_b64="IMGDATA", vision_prompt="")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    content = messages[-1]["content"]
    assert content[1]["type"] == "text"
    assert content[1]["text"].strip()


@pytest.mark.asyncio
async def test_two_images_keep_their_own_prompts_in_one_message():
    result = _image_result(images=[
        ToolImage(data_b64="A", vision_prompt="first"),
        ToolImage(data_b64="B", vision_prompt="second"),
    ])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    assert len(_image_messages(messages)) == 1
    content = messages[-1]["content"]
    assert [part["type"] for part in content] == [
        "image_url", "text", "image_url", "text",
    ]
    assert content[0]["image_url"]["url"].endswith(",A")
    assert content[1]["type"] == "text"
    assert content[1]["text"].endswith("first")
    assert content[2]["image_url"]["url"].endswith(",B")
    assert content[3]["type"] == "text"
    assert content[3]["text"].endswith("second")


@pytest.mark.asyncio
async def test_parallel_tool_images_name_their_originating_call():
    client = _Client({
        "tool_a": ToolResult(
            call_id="call_a",
            name="tool_a",
            output={"shot_id": "shot_a"},
            images=[ToolImage(data_b64="AAA")],
        ),
        "tool_b": ToolResult(
            call_id="call_b",
            name="tool_b",
            output={"shot_id": "shot_b"},
            images=[ToolImage(data_b64="BBB")],
        ),
    })
    messages: list = []

    await client._execute_and_append_openai_tool_calls(
        messages,
        [_Call(name="tool_a", id="call_a"), _Call(name="tool_b", id="call_b")],
    )

    image_messages = _image_messages(messages)
    assert len(image_messages) == 2
    first_caption = image_messages[0]["content"][1]["text"]
    second_caption = image_messages[1]["content"][1]["text"]
    assert "tool_a" in first_caption
    assert "call_a" in first_caption
    assert "tool_b" not in first_caption
    assert "call_b" not in first_caption
    assert "tool_b" in second_caption
    assert "call_b" in second_caption
    assert "tool_a" not in second_caption
    assert "call_a" not in second_caption


@pytest.mark.asyncio
async def test_image_turns_follow_every_tool_result():
    """OpenAI-compat wants all ``tool`` replies before any other role."""
    first = ToolResult(
        call_id="call_a",
        name="with_image",
        output={"ok": True},
        images=[ToolImage(data_b64="IMGDATA", mime="image/jpeg")],
    )
    second = ToolResult(
        call_id="call_b",
        name="plain",
        output={"ok": True},
    )
    client = _Client({"with_image": first, "plain": second})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(
        messages,
        [
            _Call(name="with_image", id="call_a"),
            _Call(name="plain", id="call_b"),
        ],
    )
    assert [m["role"] for m in messages] == [
        "assistant", "tool", "tool", "user",
    ]


@pytest.mark.asyncio
async def test_parallel_tool_results_share_an_aggregate_image_count_budget():
    client = _Client({
        name: _image_result(name=name, images=[ToolImage(data_b64=name.upper())])
        for name in ("first", "second", "third")
    })
    messages: list = []

    await client._execute_and_append_openai_tool_calls(
        messages,
        [_Call(name=name, id=name) for name in ("first", "second", "third")],
    )

    assert len(_image_messages(messages)) == 2
    assert "FIRST" in str(messages)
    assert "SECOND" in str(messages)
    assert "THIRD" not in str(messages)


@pytest.mark.asyncio
async def test_aggregate_image_budget_omission_is_visible_to_the_model():
    client = _Client({
        name: _image_result(name=name, images=[ToolImage(data_b64=name.upper())])
        for name in ("first", "second", "third")
    })
    messages: list = []

    await client._execute_and_append_openai_tool_calls(
        messages,
        [_Call(name=name, id=name) for name in ("first", "second", "third")],
    )

    third_result = next(
        message
        for message in messages
        if message.get("role") == "tool" and message.get("name") == "third"
    )
    payload = json.loads(third_result["content"])
    assert any(
        "omitted" in warning and "turn image budget" in warning
        for warning in payload["_image_warnings"]
    )


@pytest.mark.asyncio
async def test_tool_iterations_share_an_aggregate_image_byte_budget(monkeypatch):
    monkeypatch.setattr(
        tools_module,
        "_TOOL_IMAGE_TURN_MAX_B64_BYTES",
        5,
        raising=False,
    )
    client = _Client({
        "first": _image_result(
            name="first",
            images=[ToolImage(data_b64="AAAA")],
        ),
        "second": _image_result(
            name="second",
            images=[ToolImage(data_b64="BBBB")],
        ),
    })
    messages: list = []
    slots: list = []

    await client._execute_and_append_openai_tool_calls(
        messages,
        [_Call(name="first", id="first")],
        tool_image_slots=slots,
    )
    await client._execute_and_append_openai_tool_calls(
        messages,
        [_Call(name="second", id="second")],
        tool_image_slots=slots,
    )

    assert len(_image_messages(messages)) == 1
    assert "AAAA" in str(messages)
    assert "BBBB" not in str(messages)


# -------------------------------------------------------------------- release


@pytest.mark.asyncio
async def test_release_replaces_the_image_with_text():
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    assert _image_messages(messages)

    client._release_tool_image_slots()

    assert _image_messages(messages) == []
    assert messages[-1]["role"] == "user"
    assert isinstance(messages[-1]["content"], str)
    assert "IMGDATA" not in str(messages)
    assert client._pending_tool_image_slots == []


@pytest.mark.asyncio
async def test_release_is_idempotent():
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    client._release_tool_image_slots()
    snapshot = list(messages)
    client._release_tool_image_slots()
    assert messages == snapshot


@pytest.mark.asyncio
async def test_release_leaves_a_rewritten_slot_alone():
    """Another path may have rebuilt history under us. Replacing by index
    without checking identity would corrupt an unrelated message."""
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])

    messages[-1] = {"role": "user", "content": "something else entirely"}
    client._release_tool_image_slots()

    assert messages[-1] == {"role": "user", "content": "something else entirely"}


@pytest.mark.asyncio
async def test_release_handles_a_truncated_history():
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])

    del messages[1:]
    client._release_tool_image_slots()

    assert messages == [messages[0]]


@pytest.mark.asyncio
async def test_release_covers_every_tool_call_in_the_round():
    """One model turn can call the screenshot tool more than once; each
    injected frame needs its own replacement."""
    client = _Client({
        "tool_a": ToolResult(call_id="1", name="tool_a", output={},
                             images=[ToolImage(data_b64="AAA")]),
        "tool_b": ToolResult(call_id="2", name="tool_b", output={},
                             images=[ToolImage(data_b64="BBB")]),
    })
    messages: list = []
    await client._execute_and_append_openai_tool_calls(
        messages, [_Call(name="tool_a", id="1"), _Call(name="tool_b", id="2")],
    )
    assert len(_image_messages(messages)) == 2

    client._release_tool_image_slots()

    assert _image_messages(messages) == []
    assert "AAA" not in str(messages)
    assert "BBB" not in str(messages)


@pytest.mark.asyncio
async def test_placeholder_names_the_tool_so_the_model_can_follow_up():
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    client._release_tool_image_slots()
    assert "demo_tool" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_placeholder_preserves_the_screenshot_recall_path():
    recall_hint = "稍后请调用截图召回工具。"
    result = _image_result(
        output={"shot_id": "shot_7", "recall_hint": recall_hint},
        images=[ToolImage(data_b64="IMGDATA")],
    )
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])

    client._release_tool_image_slots()

    placeholder = messages[-1]["content"]
    assert "shot_7" in placeholder
    assert recall_hint in placeholder


@pytest.mark.asyncio
async def test_release_targets_the_list_it_injected_into():
    """``prompt_ephemeral`` runs the tool loop over a scratch list, not
    ``_conversation_history``; the slot must remember which list it wrote to."""
    result = _image_result(images=[ToolImage(data_b64="IMGDATA")])
    client = _Client({"demo_tool": result})
    scratch: list = []
    history: list = [
        {"role": "user", "content": f"untouched-{index}"}
        for index in range(3)
    ]
    baseline = list(history)
    await client._execute_and_append_openai_tool_calls(scratch, [_Call()])
    assert len(scratch) == len(history)

    client._release_tool_image_slots()

    assert history == baseline
    assert _image_messages(scratch) == []


@pytest.mark.asyncio
async def test_each_visible_tool_stream_gets_its_own_image_slot_scope():
    class _ScopeProbeClient(_Client):
        def __init__(self):
            super().__init__()
            self.scopes = []

        async def _astream_with_tools(self, _messages, **overrides):
            self.scopes.append(overrides.get("_tool_image_slots"))
            if False:
                yield None

    client = _ScopeProbeClient()
    async for _chunk in client._astream_visible_with_tools([]):
        pass
    async for _chunk in client._astream_visible_with_tools([]):
        pass

    assert isinstance(client.scopes[0], list)
    assert isinstance(client.scopes[1], list)
    assert client.scopes[0] is not client.scopes[1]


def test_releasing_one_stream_scope_leaves_a_sibling_image_available():
    client = _Client()
    first_messages: list = []
    second_messages: list = []
    first_slots: list = []
    second_slots: list = []
    client._append_tool_result_images(
        first_messages,
        _image_result(name="first", images=[ToolImage(data_b64="FIRST")]),
        slots=first_slots,
    )
    client._append_tool_result_images(
        second_messages,
        _image_result(name="second", images=[ToolImage(data_b64="SECOND")]),
        slots=second_slots,
    )

    client._release_tool_image_slots(first_slots)

    assert _image_messages(first_messages) == []
    assert len(_image_messages(second_messages)) == 1
    assert "SECOND" in str(second_messages)


# ---------------------------------------------------------------------------
# 图像轮必须活过外层重试阶梯
# ---------------------------------------------------------------------------
#
# 释放原本在 ``_astream_visible_with_tools`` 自己的 finally 里。一次可重试的
# 失败之后，历史里 assistant 的 tool_calls 和 tool 结果都还在、唯独像素被换成
# 了文字占位符——而外层重试用的正是同一份历史。重试成功的那一轮，模型会当作
# 自己已经看过那张图并据此回答。这是一类静默的错误回答，不是性能问题。


class _RetryClient(_Client):
    """Drives the REAL tool loop: the model asks for a tool, the tool returns a
    picture, and the follow-up request answers. Injection and release therefore
    share whichever slot list the loop was given, which is the thing under test.
    """

    _use_genai_sdk = False
    _genai_tools_unsupported = False
    base_url = "https://test.invalid/v1"

    def __init__(self, results=None):
        super().__init__(results)
        self._tool_definitions = []
        self.max_tool_iterations = 4
        self.model = "test-model"
        self._last_prompt_tokens = None
        self.on_tool_round_start = None

        outer = self

        class _ScriptedLLM:
            def __init__(self) -> None:
                self.rounds = 0

            async def astream(self, _messages, **_overrides):
                self.rounds += 1
                if self.rounds == 1:
                    yield LLMStreamChunk(
                        content="",
                        tool_call_deltas=[{
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "demo_tool", "arguments": "{}"},
                        }],
                        finish_reason="tool_calls",
                    )
                    return
                # 第二轮：带着图的那次请求。把当时历史里的图像部分记下来，
                # 断言用得上——「循环跑到过带图的请求」是前提，不是结论。
                outer.saw_images_in_request = bool(_image_parts(_messages))
                yield LLMStreamChunk(content="看到了")

        self.llm = _ScriptedLLM()
        self.saw_images_in_request = False
        self.drained: list = []
        self.drained_lists: list = []

    def _openai_tools_payload(self):
        return [{"type": "function", "function": {"name": "demo_tool"}}]

    async def _notify_tool_round_start(self):
        return None

    async def _notify_reasoning_active(self):
        return None

    def _publish_pending_tool_frames(self, pending, *, turn_id=None):
        # 记下排空时看到的那份列表对象本身，用来分辨「用了调用方的」还是
        # 「自己又建了一份」；然后按真实实现的语义排空。
        self.drained_lists.append(pending)
        if pending:
            self.drained.extend(pending)
            pending.clear()
        return None


def _image_parts(messages):
    return [
        part
        for m in messages
        if isinstance(m, dict) and isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


def _tool_client():
    return _RetryClient(
        {"demo_tool": _image_result(images=[ToolImage(data_b64="IMGDATA")])}
    )


@pytest.mark.asyncio
async def test_a_caller_owned_slot_list_survives_the_inner_finally():
    """The whole fix in one property.

    The outer retry ladder re-runs against this same history. If the inner
    finally releases, the retry sees the tool_calls and the tool result but a
    text placeholder where the pixels were, and answers as though it had looked.

    Mutation: drop the ``owned_tool_image_slots is None`` guard so the inner
    finally releases regardless.
    """
    client = _tool_client()
    messages: list = []
    owned: list = []

    async for _ in client._astream_visible_with_tools(
        messages, _tool_image_slots=owned,
    ):
        pass

    assert client.saw_images_in_request, "前提没成立：带图的那次请求没发生"
    assert _image_parts(messages), (
        "调用方拥有的槽位被内层 finally 释放了——重试将看不到像素"
    )
    assert owned, "槽位列表被清空了"


@pytest.mark.asyncio
async def test_an_unowned_slot_list_is_still_released_by_the_inner_finally():
    """The dual: a caller that does not take ownership keeps the old behaviour.

    Base64 left in history rides every later request while the token counter
    renders it as a short placeholder, so the release must not simply move.

    Mutation: skip the release unconditionally.
    """
    client = _tool_client()
    messages: list = []

    async for _ in client._astream_visible_with_tools(messages):
        pass

    assert client.saw_images_in_request, "前提没成立：带图的那次请求没发生"
    assert not _image_parts(messages), (
        "没有调用方接管时，图像轮必须仍由内层 finally 清掉"
    )


@pytest.mark.asyncio
async def test_a_caller_owned_bus_frame_list_is_the_one_the_loop_uses():
    """The staged bus frames need the same ownership as the slots.

    Keeping the pixels in history across a retry while letting the staged copy
    die with the failed attempt produces a new asymmetry: the retry delivers to
    the provider and nothing is ever copied to the bus, because attempt 2's tool
    loop does not run again.

    Mutation: ignore ``_tool_bus_frames`` and always build a fresh list.
    """
    client = _tool_client()
    messages: list = []
    owned_frames: list = []

    async for _ in client._astream_visible_with_tools(
        messages,
        _tool_image_slots=[],
        _tool_bus_frames=owned_frames,
    ):
        pass

    assert client.saw_images_in_request, "前提没成立：带图的那次请求没发生"
    assert client.drained_lists, "前提没成立：排空根本没被调用"
    assert all(d is owned_frames for d in client.drained_lists), (
        "循环用的是自己新建的暂存列表，不是调用方传进来的那份"
    )
    assert client.drained, "调用方的列表里什么都没暂存过"
