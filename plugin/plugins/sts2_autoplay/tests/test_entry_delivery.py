from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin


class EntryDeliveryPlugin(STS2AutoplayPlugin):
    def __init__(self) -> None:
        pass

    async def finish(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_entry_finish_uses_proactive_delivery() -> None:
    plugin = EntryDeliveryPlugin()

    async def action() -> dict[str, str]:
        return {"status": "clarify", "summary": "我不确定你是想只要建议，还是要我实际操作。"}

    result = await plugin._run_entry(action, finish=True)

    assert result["delivery"] == "proactive"
    assert "reply" not in result
    assert result["message"] == "我不确定你是想只要建议，还是要我实际操作。"
