import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

from plugin.plugins.qq_auto_reply import reply_generation_service as reply_module
from plugin.plugins.qq_auto_reply.reply_generation_service import (
    QQReplyGenerationService,
)
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
