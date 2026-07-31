from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from plugin.plugins.qq_auto_reply.reply_generation_service import (
    QQReplyGenerationService,
)


@pytest.mark.asyncio
async def test_recall_handler_does_not_own_the_outbound_buffer():
    """The shared core owns tool-round text filtering."""

    class _ToolService:
        @staticmethod
        def has_recall_arguments(_arguments):
            return False

        @staticmethod
        async def execute_recall(*, context, arguments):
            return "no result", {}

    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = SimpleNamespace(
        memory_tool_service=_ToolService(),
        logger=MagicMock(),
    )
    service._store_consent_snapshot = MagicMock()
    outbound_chunks = ["already released final text"]

    handler = service._build_recall_tool_handler(
        context=SimpleNamespace(),
        consent_before={},
    )
    result = await handler(SimpleNamespace(
        name="recall_memory",
        arguments={},
        call_id="call_1",
    ))

    assert result.output == "no result"
    assert outbound_chunks == ["already released final text"]
