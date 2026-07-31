import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugin.plugins.qq_auto_reply import reply_generation_service as reply_module
from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_generation_service import (
    QQReplyGenerationService,
)
from plugin.plugins.qq_auto_reply.reply_pipeline import QQReplyPipelineRunner
from plugin.plugins.qq_auto_reply.reply_postprocess_node import (
    QQReplyPostprocessNode,
)


def test_qq_recall_tool_does_not_install_a_pre_tool_discard_hook(monkeypatch):
    """QQ must not receive ownership of the model's outbound text buffer."""

    class _ToolService:
        @staticmethod
        def build_recall_tool_definition():
            return SimpleNamespace(name="recall_memory")

    class _Client:
        model = "tool-capable-model"
        base_url = "https://provider.example/v1"

        def __init__(self):
            self.handler = None
            self.round_start_callbacks = []

        def set_tools(self, _tools):
            pass

        def set_tool_call_handler(self, handler):
            self.handler = handler

        def set_tool_round_start_callback(self, callback):
            self.round_start_callbacks.append(callback)

    monkeypatch.setattr(reply_module, "route_supports_tool_calls", lambda *_: True)
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = SimpleNamespace(
        memory_tool_service=_ToolService(),
        logger=MagicMock(),
    )
    client = _Client()

    armed = service._arm_recall_tool(
        context=SimpleNamespace(recall_via_tool=True, use_memory_context=True),
        user_session=client,
        consent_before={},
    )

    assert armed is True
    assert client.handler is not None
    assert client.round_start_callbacks == []
    assert "reply_chunks" not in inspect.signature(
        service._build_recall_tool_handler
    ).parameters


def test_dynamic_xml_keeps_visible_text_before_the_first_message():
    """Pre-tool text must survive the default dynamic XML parser."""
    blocks = QQReplyPostprocessNode._parse_blocks(
        "<wait>2</wait>我查一下<msg><text>查到了</text></msg>"
    )

    assert [block.text for block in blocks] == ["我查一下", "查到了"]


def test_dynamic_xml_parses_unescaped_characters_in_pre_tool_text():
    """The plain prefix is not part of the XML document."""
    blocks = QQReplyPostprocessNode._parse_blocks(
        "我看 1 < 2 & 等一下<msg><text>答案</text><emoji>277</emoji></msg>"
    )

    assert [block.text for block in blocks] == ["我看 1 < 2 & 等一下", "答案"]
    assert blocks[1].emoji == "277"


def test_dynamic_xml_fence_is_not_delivered_as_pre_tool_text():
    """A recognized XML code fence is formatting, not assistant content."""
    blocks = QQReplyPostprocessNode._parse_blocks(
        "```xml\n<msg><text>查到了</text></msg>\n```"
    )

    assert [block.text for block in blocks] == ["查到了"]


def test_dynamic_xml_wait_inside_fence_is_not_delivered_as_pre_tool_text():
    """Wait removal must expose and then remove the opening XML fence."""
    blocks = QQReplyPostprocessNode._parse_blocks(
        "```xml\n<wait>2</wait><msg><text>查到了</text></msg>\n```"
    )

    assert [block.text for block in blocks] == ["查到了"]


@pytest.mark.asyncio
async def test_buffer_summary_receives_pre_tool_and_final_text():
    """The buffered summary input must contain every visible text block."""
    buffer_service = SimpleNamespace(schedule_reply=AsyncMock())
    plugin = SimpleNamespace(
        reply_buffer_service=buffer_service,
        _build_session_key=lambda **_kwargs: "group:7788",
        _emit_log=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    blocks = [QQMessageBlock(text="我查一下"), QQMessageBlock(text="查到了")]
    plan = SimpleNamespace(blocks=blocks, target_type="group", target_id="7788")
    request = SimpleNamespace(
        source_kind="incoming",
        sender_id="2046",
        is_group=True,
        group_id="7788",
        forward_sub_count=0,
        persist_memory=True,
    )
    outcome = SimpleNamespace(
        raw_reply_text="我查一下<msg><text>查到了</text></msg>",
        used_fallback=False,
        used_default_message=False,
    )

    await runner._run_delivery(plan, request, outcome)

    assert buffer_service.schedule_reply.await_args.kwargs["reply_text"] == (
        "我查一下\n查到了"
    )


@pytest.mark.asyncio
async def test_prefixed_malformed_xml_still_uses_repair():
    """A literal prefix must not hide malformed XML from the repair path."""
    plugin = SimpleNamespace(
        _strategy_mode="neko_dynamic",
        _sanitize_generated_reply=lambda text: text,
        _emit_log=MagicMock(),
    )
    node = QQReplyPostprocessNode(plugin)
    node._repair_xml = AsyncMock(
        return_value="<msg><sticker>5</sticker></msg>"
    )

    outcome = await node.finalize(
        SimpleNamespace(ephemeral_session=False),
        SimpleNamespace(
            reply_text="我查一下<msg><sticker>5</msg>",
            used_fallback=False,
        ),
    )

    node._repair_xml.assert_awaited_once_with("<msg><sticker>5</msg>")
    assert [block.text for block in outcome.blocks] == ["我查一下", ""]
    assert outcome.blocks[1].sticker == "5"
