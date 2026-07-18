from __future__ import annotations

from pathlib import Path

import pytest

from main_logic.widget_mode_runtime import (
    COMPACTION_ACK_TIMEOUT_SECONDS,
    RENDERER_SUSPENSION_DELAY_SECONDS,
    WINDOW_REGISTRATION_TTL_SECONDS,
    WIDGET_MODE_PROTOCOL_VERSION,
    WidgetModeCoordinator,
    WidgetModeSettingsStore,
)


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def build_coordinator(*, delivered: int = 1):
    clock = Clock()
    events: list[dict] = []

    async def broadcaster(payload: dict) -> int:
        events.append(payload)
        return delivered

    coordinator = WidgetModeCoordinator(
        broadcaster=broadcaster,
        time_fn=clock,
    )
    return coordinator, clock, events


async def enable_widget_mode(coordinator: WidgetModeCoordinator) -> None:
    await coordinator.set_enabled(True)


async def register_capable_pet(
    coordinator: WidgetModeCoordinator,
    pet_instance_id: str = "pet-1",
) -> dict:
    return await coordinator.register_window(
        pet_instance_id=pet_instance_id,
        window_type="pet",
        widget_mode_protocol_version=WIDGET_MODE_PROTOCOL_VERSION,
        widget_mode_compaction_lease_v1=True,
    )


async def trigger_compaction(coordinator: WidgetModeCoordinator) -> str | None:
    state = await coordinator.trigger_debug_compaction(reason="test")
    return state["compaction_cycle_id"]


@pytest.mark.asyncio
async def test_defaults_are_disabled_and_legacy_file_is_not_read(tmp_path: Path) -> None:
    path = tmp_path / "widget_mode_settings.json"
    path.write_text('{"legacy_setting":"ignored"}', encoding="utf-8")
    coordinator = WidgetModeCoordinator(store=WidgetModeSettingsStore(path))

    state = coordinator.snapshot()
    assert state["enabled"] is False
    assert "settings" not in state
    assert "legacy_setting" not in state


@pytest.mark.asyncio
async def test_compaction_ack_creates_owner_then_suspends_and_restores() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    assert cycle_id
    assert events[-1]["type"] == "widget_mode_compaction_requested"

    state = await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="compacted",
    )
    assert state["compaction_phase"] == "compacted"
    assert state["owned_window_count"] == 1
    assert events[-1]["type"] == "widget_mode_compaction_confirmed"

    clock.advance(RENDERER_SUSPENSION_DELAY_SECONDS)
    await register_capable_pet(coordinator)
    await coordinator.tick_once()
    assert coordinator.snapshot()["compaction_phase"] == "renderer_suspended"
    assert events[-1]["type"] == "widget_mode_renderer_suspension_requested"

    await coordinator.acknowledge_renderer_suspension(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        success=True,
    )
    assert coordinator.snapshot()["renderer_suspension_success_count"] == 1

    state = await coordinator.mark_user_restore("pet-1")
    assert state["compaction_phase"] == "idle"
    assert state["user_restore_active"] is True
    assert state["suppressed_until"] > clock()
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_expired_compaction_owner_is_removed_and_cycle_converges() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="compacted",
    )

    clock.advance(WINDOW_REGISTRATION_TTL_SECONDS + 1)
    await coordinator.tick_once()

    state = coordinator.snapshot()
    assert state["registered_window_count"] == 0
    assert state["owned_window_count"] == 0
    assert state["compaction_phase"] == "idle"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_all_already_compacted_acknowledgements_end_without_suspension() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    state = await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="already_compacted",
    )

    assert state["compaction_phase"] == "idle"
    assert state["owned_window_count"] == 0
    assert not any(event["type"] == "widget_mode_compaction_confirmed" for event in events)
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_failed_ack_ends_cycle_and_sets_retry() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    state = await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="failed",
    )

    assert state["compaction_phase"] == "idle"
    assert state["retry_not_before"] > clock()
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_ack_timeout_ends_cycle() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    await trigger_compaction(coordinator)
    clock.advance(COMPACTION_ACK_TIMEOUT_SECONDS)
    await coordinator.tick_once()

    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    assert events[-1]["compaction_cycle_id"]
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_pending_window_disconnect_recalculates_expected_set() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    await register_capable_pet(coordinator, "pet-2")
    cycle_id = await trigger_compaction(coordinator)
    await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="compacted",
    )
    state = await coordinator.unregister_window("pet-2")

    assert state["expected_window_count"] == 1
    assert state["compaction_phase"] == "compacted"
    assert state["owned_window_count"] == 1
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_late_join_during_compacting_becomes_expected() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    cycle_id = await trigger_compaction(coordinator)
    registration = await register_capable_pet(coordinator, "pet-2")
    assert registration["join_as_compacted"] is True
    assert coordinator.snapshot()["expected_window_count"] == 2

    for pet_id in ("pet-1", "pet-2"):
        await coordinator.acknowledge_compaction(
            compaction_cycle_id=cycle_id,
            pet_instance_id=pet_id,
            status="compacted",
        )
    assert coordinator.snapshot()["owned_window_count"] == 2
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_new_late_join_extends_ack_deadline_once() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    await trigger_compaction(coordinator)
    original_deadline = coordinator.snapshot()["compaction_ack_deadline"]

    clock.advance(COMPACTION_ACK_TIMEOUT_SECONDS - 0.5)
    await register_capable_pet(coordinator, "pet-2")
    extended_deadline = coordinator.snapshot()["compaction_ack_deadline"]

    assert extended_deadline == pytest.approx(clock() + COMPACTION_ACK_TIMEOUT_SECONDS)
    assert extended_deadline > original_deadline

    clock.advance(1.0)
    await register_capable_pet(coordinator, "pet-2")
    assert coordinator.snapshot()["compaction_ack_deadline"] == extended_deadline

    await coordinator.tick_once()
    assert coordinator.snapshot()["compaction_phase"] == "compacting"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_protocol_mismatch_fails_closed() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    registration = await coordinator.register_window(
        pet_instance_id="pet-old",
        window_type="pet",
        widget_mode_protocol_version=WIDGET_MODE_PROTOCOL_VERSION + 1,
        widget_mode_compaction_lease_v1=True,
    )
    await trigger_compaction(coordinator)

    assert registration["protocol_compatible"] is False
    assert registration["widget_mode_capable"] is False
    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert not any(event["type"] == "widget_mode_compaction_requested" for event in events)
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_chat_window_is_never_counted_as_capable_pet() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    registration = await coordinator.register_window(
        pet_instance_id="chat-1",
        window_type="chat",
        widget_mode_protocol_version=WIDGET_MODE_PROTOCOL_VERSION,
        widget_mode_compaction_lease_v1=True,
    )
    await trigger_compaction(coordinator)

    assert registration["widget_mode_capable"] is False
    assert coordinator.snapshot()["registered_window_count"] == 0
    assert coordinator.snapshot()["compaction_phase"] == "idle"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_old_cycle_messages_are_ignored() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    before = coordinator.snapshot()
    await coordinator.acknowledge_compaction(
        compaction_cycle_id="old-cycle",
        pet_instance_id="pet-1",
        status="restored",
    )
    after = coordinator.snapshot()

    assert after["compaction_cycle_id"] == cycle_id == before["compaction_cycle_id"]
    assert after["compaction_phase"] == "compacting"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_renderer_suspension_partial_success_is_recorded() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    await register_capable_pet(coordinator, "pet-2")
    cycle_id = await trigger_compaction(coordinator)
    for pet_id in ("pet-1", "pet-2"):
        await coordinator.acknowledge_compaction(
            compaction_cycle_id=cycle_id,
            pet_instance_id=pet_id,
            status="compacted",
        )
    clock.advance(RENDERER_SUSPENSION_DELAY_SECONDS)
    await register_capable_pet(coordinator, "pet-1")
    await register_capable_pet(coordinator, "pet-2")
    await coordinator.tick_once()
    await coordinator.acknowledge_renderer_suspension(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        success=True,
    )
    await coordinator.acknowledge_renderer_suspension(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-2",
        success=False,
    )

    assert coordinator.snapshot()["renderer_suspension_success_count"] == 1
    assert coordinator.snapshot()["compaction_phase"] == "renderer_suspended"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_disable_broadcasts_restore_and_ends_backend_cycle() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="compacted",
    )
    state = await coordinator.set_enabled(False)

    restore_event = next(
        event for event in events if event["type"] == "widget_mode_compaction_restore_requested"
    )
    assert restore_event["compaction_cycle_id"] == cycle_id
    assert state["enabled"] is False
    assert state["compaction_phase"] == "idle"


def test_settings_store_handles_invalid_json_and_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "widget_mode_settings.json"
    path.write_text("not-json", encoding="utf-8")
    store = WidgetModeSettingsStore(path)
    assert store.load_settings() == {}

    store.save({"suppressed_until": 1234.5})
    assert store.load_settings() == {"suppressed_until": 1234.5}


def test_settings_store_save_propagates_write_failure(monkeypatch, tmp_path: Path) -> None:
    import utils.file_utils as file_utils

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(file_utils, "atomic_write_json", fail_write)
    store = WidgetModeSettingsStore(tmp_path / "widget_mode_settings.json")

    with pytest.raises(OSError, match="disk full"):
        store.save({"suppressed_until": 1234.5})


@pytest.mark.asyncio
async def test_user_restore_persistence_failure_preserves_cycle_ownership(tmp_path: Path) -> None:
    class FailingOnDemandStore(WidgetModeSettingsStore):
        fail = False

        async def save_async(self, payload: dict) -> None:
            if self.fail:
                raise OSError("disk full")
            await super().save_async(payload)

    clock = Clock()
    events: list[dict] = []

    async def broadcaster(payload: dict) -> int:
        events.append(payload)
        return 1

    store = FailingOnDemandStore(tmp_path / "widget_mode_settings.json")
    coordinator = WidgetModeCoordinator(
        store=store,
        broadcaster=broadcaster,
        time_fn=clock,
    )
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await trigger_compaction(coordinator)
    await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="compacted",
    )
    clock.advance(RENDERER_SUSPENSION_DELAY_SECONDS)
    await register_capable_pet(coordinator)
    await coordinator.tick_once()
    assert coordinator.snapshot()["compaction_phase"] == "renderer_suspended"

    store.fail = True
    with pytest.raises(OSError, match="disk full"):
        await coordinator.mark_user_restore("pet-1")

    state = coordinator.snapshot()
    assert state["compaction_phase"] == "renderer_suspended"
    assert state["owned_window_count"] == 1
    assert state["user_restore_active"] is False
    assert state["suppressed_until"] is None


@pytest.mark.asyncio
async def test_empty_window_id_is_rejected() -> None:
    coordinator, _clock, _events = build_coordinator()
    with pytest.raises(ValueError):
        await coordinator.register_window(pet_instance_id="")


@pytest.mark.asyncio
async def test_delivery_failure_closes_cycle() -> None:
    coordinator, clock, events = build_coordinator(delivered=0)
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    await trigger_compaction(coordinator)

    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_disconnect_of_only_pending_window_fails_cycle() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator)
    await trigger_compaction(coordinator)
    state = await coordinator.unregister_window("pet-1")

    assert state["compaction_phase"] == "idle"
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_late_join_after_suspension_receives_targeted_request() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_widget_mode(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    cycle_id = await trigger_compaction(coordinator)
    await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-1",
        status="compacted",
    )
    clock.advance(RENDERER_SUSPENSION_DELAY_SECONDS)
    await register_capable_pet(coordinator, "pet-1")
    await coordinator.tick_once()
    await register_capable_pet(coordinator, "pet-2")
    await coordinator.acknowledge_compaction(
        compaction_cycle_id=cycle_id,
        pet_instance_id="pet-2",
        status="compacted",
    )

    assert events[-1]["type"] == "widget_mode_renderer_suspension_requested"
    assert events[-1]["pet_instance_ids"] == ["pet-2"]
    await coordinator.set_enabled(False)
