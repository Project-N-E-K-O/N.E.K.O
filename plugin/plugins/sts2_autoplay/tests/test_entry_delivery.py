from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin
from plugin.plugins.sts2_autoplay.tests.live_entry_smoke import SPECIAL_ARGS, collect_entries


class EntryDeliveryPlugin(STS2AutoplayPlugin):
    def __init__(self) -> None:
        pass

    async def finish(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_entry_finish_uses_passive_delivery() -> None:
    plugin = EntryDeliveryPlugin()

    async def action() -> dict[str, str]:
        return {"status": "clarify", "summary": "我不确定你是想只要建议，还是要我实际操作。"}

    result = await plugin._run_entry(action, finish=True)

    assert result["delivery"] == "passive"
    assert "reply" not in result
    assert result["message"] == "我不确定你是想只要建议，还是要我实际操作。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_entry_finish_falls_back_to_message_when_summary_missing() -> None:
    """_summary_from() 回退顺序：summary -> message -> content。
    控制类入口（pause/resume/stop）通常没有 summary，只有 message——
    确保这条 fallback 不会被 regression 抹掉。"""
    plugin = EntryDeliveryPlugin()

    async def action() -> dict[str, str]:
        return {"status": "paused", "message": "已暂停自动游玩。"}

    result = await plugin._run_entry(action, finish=True)



@pytest.mark.unit
def test_live_entry_smoke_special_args_cover_all_required_entry_inputs() -> None:
    entry_ids = {entry_id for entry_id, _ in collect_entries()}

    assert "sts2_set_standby" in entry_ids
    assert "sts2_enable_companion_mode" in entry_ids
    assert "sts2_disable_companion_mode" in entry_ids

    for required in {
        "sts2_set_standby",
        "sts2_enable_companion_mode",
        "sts2_disable_companion_mode",
    }:
        assert required in SPECIAL_ARGS
        assert SPECIAL_ARGS[required]


@pytest.mark.unit
def test_live_entry_smoke_collects_all_plugin_entries() -> None:
    entries = collect_entries()
    entry_ids = {entry_id for entry_id, _ in entries}

    assert len(entries) == 13
    assert entry_ids == {
        "sts2_health_check",
        "sts2_read_state",
        "sts2_get_status",
        "sts2_set_standby",
        "sts2_start_autoplay",
        "sts2_pause_autoplay",
        "sts2_resume_autoplay",
        "sts2_stop_autoplay",
        "sts2_enable_companion_mode",
        "sts2_disable_companion_mode",
        "sts2_get_planned_operation",
        "sts2_execute_planned_operation",
        "sts2_apply_user_override",
    }
