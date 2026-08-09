"""Injecting a tool's picture into the turn, then taking it back out.

The taking-out half is the load-bearing one. ``_conversation_history`` has no
image eviction of its own, and the token counter renders an image part as a
short placeholder — so a base64 frame left behind would ride along in every
later request while looking free to the truncator.
"""

from __future__ import annotations

import pytest

from main_logic.omni_offline_client._tools import _ToolingMixin
from main_logic.tool_calling import ToolCall, ToolImage, ToolResult


class _Client(_ToolingMixin):
    """Just enough of OmniOfflineClient to exercise the tool loop's history
    writes."""

    def __init__(self, results: dict[str, ToolResult] | None = None):
        self._results = results or {}
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
    assert content[1] == {"type": "text", "text": "watch the minimap"}


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
async def test_two_images_ride_one_message():
    result = _image_result(images=[
        ToolImage(data_b64="A", vision_prompt="first"),
        ToolImage(data_b64="B", vision_prompt="second"),
    ])
    client = _Client({"demo_tool": result})
    messages: list = []
    await client._execute_and_append_openai_tool_calls(messages, [_Call()])
    assert len(_image_messages(messages)) == 1
    types = [p["type"] for p in messages[-1]["content"]]
    assert types == ["image_url", "image_url", "text"]


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
    history: list = [{"role": "user", "content": "untouched"}]
    await client._execute_and_append_openai_tool_calls(scratch, [_Call()])

    client._release_tool_image_slots()

    assert history == [{"role": "user", "content": "untouched"}]
    assert _image_messages(scratch) == []
