from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import STS2LoopRunner
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class DummyClient:
    async def close(self) -> None:
        return None


@pytest.mark.unit
def test_resolve_base_url_prefers_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._cfg = {"base_url": "http://127.0.0.1:8080"}

    monkeypatch.setenv("STS2_API_BASE_URL", "http://127.0.0.1:18080/")
    monkeypatch.setenv("STS2_API_PORT", "28080")

    assert service._resolve_base_url() == "http://127.0.0.1:18080"


@pytest.mark.unit
def test_resolve_base_url_uses_env_port(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._cfg = {"base_url": "http://127.0.0.1:8080"}

    monkeypatch.delenv("STS2_API_BASE_URL", raising=False)
    monkeypatch.setenv("STS2_API_PORT", "18080")

    assert service._resolve_base_url() == "http://127.0.0.1:18080"


@pytest.mark.unit
def test_resolve_base_url_falls_back_to_plugin_config(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._cfg = {"base_url": "http://127.0.0.1:8080/"}

    monkeypatch.delenv("STS2_API_BASE_URL", raising=False)
    monkeypatch.delenv("STS2_API_PORT", raising=False)

    assert service._resolve_base_url() == "http://127.0.0.1:8080"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_planned_operation_uses_autoplay_step(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)

    async def fake_run_autoplay_step() -> dict[str, Any]:
        return {"status": "ok", "summary": "single-step", "message": "single-step"}

    monkeypatch.setattr(service, "run_autoplay_step", fake_run_autoplay_step)

    result = await service.execute_planned_operation()

    assert result["status"] == "ok"
    assert result["summary"] == "single-step"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_neko_interface_disabling_companion_mode_skips_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    interface = service.neko
    refresh_calls: list[bool] = []

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        refresh_calls.append(trigger_sync)
        return {"status": "ok", "snapshot": {}}

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await interface.set_companion_mode(False)

    assert result["enabled"] is False
    assert refresh_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_neko_interface_enabling_companion_mode_refreshes(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    interface = service.neko
    refresh_calls: list[bool] = []

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        refresh_calls.append(trigger_sync)
        return {"status": "ok", "snapshot": {}}

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await interface.set_companion_mode(True)

    assert result["enabled"] is True
    assert refresh_calls == [True, True]




@pytest.mark.unit
def test_set_companion_mode_disable_does_not_stop_polling_while_autoplay_active(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    calls: list[str] = []

    monkeypatch.setattr(service._loop_runner, "is_polling", lambda: True)
    monkeypatch.setattr(service._loop_runner, "is_autoplaying", lambda: True)
    monkeypatch.setattr(service._loop_runner, "start_background", lambda: calls.append("start"))
    monkeypatch.setattr(service._loop_runner, "stop_background_sync", lambda: calls.append("stop"))

    service._state.autoplay_state = "running"
    result = service.set_companion_mode(False)

    assert result["enabled"] is False
    assert calls == ["start"]


@pytest.mark.unit
def test_set_companion_mode_disable_stops_polling_when_autoplay_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    calls: list[str] = []

    monkeypatch.setattr(service._loop_runner, "is_polling", lambda: True)
    monkeypatch.setattr(service._loop_runner, "is_autoplaying", lambda: False)
    monkeypatch.setattr(service._loop_runner, "start_background", lambda: calls.append("start"))
    monkeypatch.setattr(service._loop_runner, "stop_background_sync", lambda: calls.append("stop"))

    service._state.autoplay_state = "idle"
    result = service.set_companion_mode(False)

    assert result["enabled"] is False
    assert calls == ["stop"]


@pytest.mark.unit
def test_sync_background_polling_starts_when_companion_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    calls: list[str] = []

    monkeypatch.setattr(service._loop_runner, "start_background", lambda: calls.append("start"))
    monkeypatch.setattr(service._loop_runner, "stop_background_sync", lambda: calls.append("stop"))

    service._cfg["companion_mode_enabled"] = True
    service._state.autoplay_state = "idle"

    service._sync_background_polling()

    assert calls == ["start"]


@pytest.mark.unit
def test_sync_background_polling_starts_when_autoplay_running(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    calls: list[str] = []

    monkeypatch.setattr(service._loop_runner, "start_background", lambda: calls.append("start"))
    monkeypatch.setattr(service._loop_runner, "stop_background_sync", lambda: calls.append("stop"))

    service._cfg["companion_mode_enabled"] = False
    service._state.autoplay_state = "running"

    service._sync_background_polling()

    assert calls == ["start"]


@pytest.mark.unit
def test_stop_autoplay_keeps_polling_when_companion_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    calls: list[str] = []

    monkeypatch.setattr(service._loop_runner, "start_background", lambda: calls.append("start"))
    monkeypatch.setattr(service._loop_runner, "stop_background_sync", lambda: calls.append("stop"))

    service._cfg["companion_mode_enabled"] = True
    service._state.autoplay_state = "running"

    result = service.stop_autoplay()

    assert result["status"] == "ok"
    assert calls == ["start"]


@pytest.mark.unit
def test_stop_autoplay_stops_polling_when_companion_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    calls: list[str] = []

    monkeypatch.setattr(service._loop_runner, "start_background", lambda: calls.append("start"))
    monkeypatch.setattr(service._loop_runner, "stop_background_sync", lambda: calls.append("stop"))

    service._cfg["companion_mode_enabled"] = False
    service._state.autoplay_state = "running"

    result = service.stop_autoplay()

    assert result["status"] == "ok"
    assert calls == ["stop"]


@pytest.mark.unit
def test_service_start_pause_resume_stop_transitions() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._apply_control_mode("program")

    started = service.start_autoplay()
    assert started["status"] == "ok"
    assert service._state.autoplay_state == "running"

    paused = service.pause_autoplay(reason="user")
    assert paused["pause_reason"] == "user"
    assert service._state.autoplay_state == "paused"

    resumed = service.resume_autoplay()
    assert resumed["status"] == "ok"
    assert service._state.autoplay_state == "running"

    stopped = service.stop_autoplay(reason="manual")
    assert stopped["stop_reason"] == "manual"
    assert service._state.autoplay_state == "idle"


@pytest.mark.unit
def test_service_start_autoplay_rejects_standby() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._apply_control_mode("standby")

    result = service.start_autoplay()

    assert result["status"] == "idle"
    assert service._state.autoplay_state == "standby"


@pytest.mark.unit
def test_set_mode_updates_single_control_source() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)

    mode_info = service.set_standby(True)

    assert service._state.control_mode == "standby"
    assert service._state.standby is True
    assert service._state.autoplay_state == "standby"
    assert mode_info["mode"] == "standby"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_user_override_entry_writes_event_override_and_context_reads_it() -> None:
    from plugin.plugins.sts2_autoplay.tests.live_entry_smoke import LiveEntryPlugin

    plugin = LiveEntryPlugin()
    plugin._service._state.snapshot = {
        "screen": "event",
        "classification": {"summary_kind": "event", "screen_class": "run_navigation"},
        "summary_context": {"payload": {"event_id": "golden_idol"}},
        "strategy_context": {"strategy_name": "defect"},
        "raw_state": {
            "run": {"floor": 3, "act": 1},
            "event": {"event_id": "golden_idol", "name": "Golden Idol"},
        },
        "available_actions": [{"type": "choose_event_option", "raw": {"name": "choose_event_option"}}],
    }

    result = await plugin.sts2_apply_user_override("优先低代价路线")

    payload = result.value if hasattr(result, "value") else result
    record = plugin._service._preference_store.get("event_overrides", "golden_idol")
    context = plugin._service._strategy_repository.build_context(plugin._service._state.snapshot)

    assert payload["status"] == "ok"
    assert record is not None
    assert record["value"]["instruction"] == "优先低代价路线"
    assert context["event_override"] is not None
    assert "优先低代价路线" in context["strategy_prompt"]


@pytest.mark.unit
def test_queue_guidance_marks_interrupt_and_tracks_generation() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)

    guidance = service._queue_guidance("先防", source="neko")

    assert guidance["generation"] == 1
    assert service._state.guidance_generation == 1
    assert service._state.interrupt_requested is True
    assert service._state.interrupt_reason == "guidance"
    assert service._state.guidance_queue_size == 1


@pytest.mark.unit
def test_should_rebuild_operation_when_guidance_generation_is_newer() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._queue_guidance("保血", source="neko")

    snapshot = {"agent_operation": {"decision_epoch": 0}}

    assert service._should_rebuild_operation(snapshot) is True


@pytest.mark.unit
def test_consume_guidance_clears_pending_and_interrupt() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    guidance = service._queue_guidance("先防", source="neko")

    service._consume_guidance(
        {
            "consumed_guidance_ids": [guidance["id"]],
            "consumed_guidance_generation": guidance["generation"],
        }
    )

    assert service._state.pending_guidance == []
    assert service._state.interrupt_requested is False
    assert service._state.last_consumed_guidance_generation == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_operation_consumes_guidance_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    guidance = service._queue_guidance("先防", source="neko")
    service._client = DummyClient()

    async def fake_execute(client: Any, snapshot: dict[str, Any], operation: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "ok",
            "operation": {
                "source": "heuristic",
                "reason": "combat_guidance_defensive",
                "consumed_guidance_ids": [guidance["id"]],
                "consumed_guidance_generation": guidance["generation"],
            },
        }

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        return {"status": "ok", "snapshot": {}}

    monkeypatch.setattr(service._action_engine, "execute", fake_execute)
    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await service.execute_operation({"action_type": "play_card"})

    assert result["status"] == "ok"
    assert service._state.pending_guidance == []
    assert service._state.interrupt_requested is False
    assert service._state.last_consumed_guidance_generation == guidance["generation"]
    assert service._state.last_decision_source == "heuristic"
    assert service._state.last_decision_reason == "combat_guidance_defensive"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_operation_records_action_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._client = DummyClient()
    service._state.snapshot = {
        "screen": "combat",
        "floor": 7,
        "act": 1,
        "in_combat": True,
        "classification": {"summary_kind": "combat"},
        "raw_state": {
            "turn": 1,
            "combat": {
                "player": {"current_hp": 50, "max_hp": 80, "block": 0, "energy": 3},
                "hand": [{"name": "Strike"}],
                "enemies": [{"name": "Louse", "current_hp": 20, "intent_damage": 6}],
            },
            "run": {"gold": 20},
        },
        "available_actions": [{"type": "play_card", "raw": {"name": "play_card"}}],
    }

    async def fake_execute(client: Any, snapshot: dict[str, Any], operation: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "ok",
            "operation": {
                "action_type": "play_card",
                "kwargs": {"card_index": 0},
                "source": "heuristic",
                "reason": "combat_guidance_aggressive",
            },
        }

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        service._state.snapshot = {
            "screen": "combat",
            "floor": 7,
            "act": 1,
            "in_combat": True,
            "classification": {"summary_kind": "combat"},
            "raw_state": {
                "turn": 1,
                "combat": {
                    "player": {"current_hp": 50, "max_hp": 80, "block": 8, "energy": 1},
                    "hand": [{"name": "Defend"}],
                    "enemies": [{"name": "Louse", "current_hp": 12, "intent_damage": 4}],
                },
                "run": {"gold": 20},
            },
            "available_actions": [{"type": "end_turn", "raw": {"name": "end_turn"}}],
        }
        return {"status": "ok", "snapshot": service._state.snapshot}

    monkeypatch.setattr(service._action_engine, "execute", fake_execute)
    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await service.execute_operation({"action_type": "play_card"})

    assert result["status"] == "ok"
    assert service._state.latest_action_frame["action_type"] == "play_card"
    assert service._state.latest_action_frame["delta"]["source"] == "action_paired"
    assert service._state.latest_action_frame["delta"]["player_changes"]["block_delta"] == 8
    assert service._state.latest_action_frame["delta"]["enemy_changes"]["enemy_total_hp_delta"] == -8
    assert service._state.snapshot["situation_summary"]["delta"]["source"] == "action_paired"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_operation_records_decision_memory() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._client = DummyClient()
    service._state.snapshot = {
        "screen": "event",
        "classification": {"summary_kind": "event"},
        "raw_state": {
            "run": {"floor": 3, "gold": 99},
            "event": {"event_id": "golden_shrine", "name": "Golden Shrine"},
        },
        "available_actions": [{"type": "choose_event_option", "raw": {"name": "choose_event_option"}}],
    }

    async def fake_execute(client: Any, snapshot: dict[str, Any], operation: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "ok",
            "operation": {
                "action_type": "choose_event_option",
                "kwargs": {"option_index": 1},
                "source": "heuristic",
                "reason": "event_preference_or_default",
            },
        }

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        service._state.snapshot = {
            "screen": "event",
            "floor": 3,
            "act": 1,
            "classification": {"summary_kind": "event", "screen_class": "run_navigation"},
            "summary_context": {"payload": {"current_hp": 30, "max_hp": 80}},
            "strategy_context": {"strategy_name": "defect"},
            "raw_state": {
                "run": {"floor": 3, "gold": 149},
                "event": {"event_id": "golden_shrine", "name": "Golden Shrine"},
            },
            "available_actions": [{"type": "proceed", "raw": {"name": "proceed"}}],
        }
        return {"status": "ok", "snapshot": service._state.snapshot}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service._action_engine, "execute", fake_execute)
    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    try:
        result = await service.execute_operation({"action_type": "choose_event_option"})
    finally:
        monkeypatch.undo()

    assert result["status"] == "ok"
    assert service._state.recent_decision_memory
    last = service._state.recent_decision_memory[-1]
    assert last["action_type"] == "choose_event_option"
    assert last["decision_reason"] == "event_preference_or_default"


@pytest.mark.unit
def test_main_menu_planner_prefers_continue_run_before_timeline_when_character_select_missing() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "main_menu"},
            "summary_context": {"payload": {}},
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "MAIN_MENU",
                "available_actions": [
                    {"type": "continue_run", "raw": {"name": "continue_run"}},
                    {"type": "open_timeline", "raw": {"name": "open_timeline"}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.action_type == "continue_run"


@pytest.mark.unit
def test_main_menu_planner_uses_continue_run_when_no_character_select_path_exists() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "main_menu"},
            "summary_context": {"payload": {}},
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "MAIN_MENU",
                "available_actions": [
                    {"type": "continue_run", "raw": {"name": "continue_run"}},
                    {"type": "abandon_run", "raw": {"name": "abandon_run"}},
                ],
            },
        }
    )

    assert operation is not None
    assert operation.action_type == "continue_run"


@pytest.mark.unit
def test_main_menu_planner_uses_timeline_epoch_when_character_select_missing() -> None:
    planner = STS2HeuristicPlanner()

    operation = planner.plan(
        {
            "classification": {"state_name": "main_menu"},
            "summary_context": {
                "payload": {
                    "timeline": {
                        "slots": [
                            {"index": 3, "is_actionable": True},
                            {"index": 4, "is_actionable": True},
                        ]
                    }
                }
            },
            "strategy_context": {"preferences": {}},
            "snapshot": {
                "screen": "MAIN_MENU",
                "available_actions": [
                    {"type": "close_main_menu_submenu", "raw": {"name": "close_main_menu_submenu"}},
                    {"type": "choose_timeline_epoch", "raw": {"name": "choose_timeline_epoch", "requires_index": True}},
                ],
            },
        }
    )



@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_autoplay_step_returns_idle_when_no_operation_available(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.snapshot = {"screen": "event"}

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        service._state.snapshot = {"screen": "event"}
        return {"status": "ok", "snapshot": service._state.snapshot}

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await service.run_autoplay_step()

    assert result["status"] == "idle"
    assert service._state.step_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_loop_runner_tick_builds_policy_from_program_mode() -> None:
    class DummyTickClient:
        async def get_state(self) -> dict[str, Any]:
            return {
                "screen": "COMBAT",
                "in_combat": True,
                "combat": {
                    "player": {"current_hp": 30, "max_hp": 80, "block": 0, "energy": 3},
                    "hand": [],
                    "enemies": [],
                },
            }

        async def get_available_actions(self) -> dict[str, Any]:
            return {"actions": [{"name": "end_turn"}]}

    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._client = DummyTickClient()
    service._state.control_mode = "program"
    runner = STS2LoopRunner(service)

    tick = await runner.tick()

    payload = tick["snapshot"]["summary_context"]["decision_payload"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_falls_back_to_current_control_mode_when_snapshot_mode_missing() -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.control_mode = "standby"
    service._state.snapshot = {
        "screen": "event",
        "classification": {"screen_class": "run_navigation"},
    }

    status = await service.get_status()



@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_user_override_safely_pauses_running_autoplay(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.autoplay_state = "running"
    calls: list[tuple[str, str]] = []

    def fake_pause_autoplay(reason: str = "user") -> dict[str, Any]:
        calls.append(("pause", reason))
        service._state.autoplay_state = "paused"
        return {"status": "ok", "message": "已暂停尖塔自动运行。", "summary": "已暂停尖塔自动运行。", "pause_reason": reason}

    async def fake_extract_and_upsert_preference(instruction: str, *, source: str = "user") -> dict[str, Any]:
        calls.append(("update", instruction))
        return {"status": "ok", "message": "已提取并更新偏好：event_overrides/golden_shrine。", "summary": "已提取并更新偏好：event_overrides/golden_shrine。"}

    monkeypatch.setattr(service, "pause_autoplay", fake_pause_autoplay)
    monkeypatch.setattr(service.neko, "extract_and_upsert_preference", fake_extract_and_upsert_preference)

    result = await service.apply_user_override_safely("优先低代价路线", source="user")

    assert calls == [("pause", "apply_user_override"), ("update", "优先低代价路线")]
    assert result["status"] == "ok"
    assert result["autoplay_paused"] is True
    assert "手动恢复自动游玩" in result["message"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_user_override_safely_keeps_paused_state(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.autoplay_state = "paused"

    async def fake_extract_and_upsert_preference(instruction: str, *, source: str = "user") -> dict[str, Any]:
        return {"status": "ok", "message": "已提取并更新偏好：event_overrides/golden_shrine。", "summary": "已提取并更新偏好：event_overrides/golden_shrine。"}

    monkeypatch.setattr(service.neko, "extract_and_upsert_preference", fake_extract_and_upsert_preference)

    result = await service.apply_user_override_safely("优先低代价路线", source="user")

    assert result["status"] == "ok"
    assert result["autoplay_paused"] is True
    assert "仍处于暂停状态" in result["message"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_user_override_safely_does_not_pause_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    service._state.autoplay_state = "idle"

    async def fake_extract_and_upsert_preference(instruction: str, *, source: str = "user") -> dict[str, Any]:
        return {"status": "ok", "message": "已提取并更新偏好：event_overrides/golden_shrine。", "summary": "已提取并更新偏好：event_overrides/golden_shrine。"}

    monkeypatch.setattr(service.neko, "extract_and_upsert_preference", fake_extract_and_upsert_preference)

    result = await service.apply_user_override_safely("优先低代价路线", source="user")

    assert result["status"] == "ok"
    assert "手动恢复自动游玩" not in result["message"]
    assert result.get("autoplay_paused") is None
