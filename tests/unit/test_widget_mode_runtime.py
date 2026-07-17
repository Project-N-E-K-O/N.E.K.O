from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import main_logic.widget_mode_runtime as runtime
from main_logic.widget_mode_runtime import (
    COMPACTION_ACK_TIMEOUT_SECONDS,
    DEFAULT_WIDGET_MODE_SETTINGS,
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


def normal_sample() -> dict:
    return {
        "ts": 1000.0,
        "cpu_percent": 10.0,
        "memory_percent": 20.0,
        "gpu_percent": 5.0,
        "errors": {},
    }


def high_sample() -> dict:
    return {
        "ts": 1000.0,
        "cpu_percent": 95.0,
        "memory_percent": 90.0,
        "gpu_percent": 99.0,
        "errors": {},
    }


def build_coordinator(*, sampler=normal_sample, delivered: int = 1):
    clock = Clock()
    events: list[dict] = []

    async def broadcaster(payload: dict) -> int:
        events.append(payload)
        return delivered

    coordinator = WidgetModeCoordinator(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=clock,
    )
    return coordinator, clock, events


async def enable_activity_compaction(coordinator: WidgetModeCoordinator) -> None:
    await coordinator.set_enabled(True)
    await coordinator.update_settings(activity_response="compact_on_confirm")


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


async def confirm_activity(coordinator: WidgetModeCoordinator, clock: Clock) -> str | None:
    await coordinator.ingest_activity_signal(active=True, available=True, observed_at=clock())
    clock.advance(5)
    await coordinator.ingest_activity_signal(active=True, available=True, observed_at=clock())
    clock.advance(5)
    state = await coordinator.ingest_activity_signal(active=True, available=True, observed_at=clock())
    return state["compaction_cycle_id"]


@pytest.mark.asyncio
async def test_defaults_are_disabled_and_legacy_file_is_not_read(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy_settings.json"
    legacy_path.write_text('{"activity_response":"compact_on_confirm"}', encoding="utf-8")
    new_path = tmp_path / "widget_mode_settings.json"
    coordinator = WidgetModeCoordinator(store=WidgetModeSettingsStore(new_path))

    assert coordinator.settings_snapshot() == DEFAULT_WIDGET_MODE_SETTINGS
    assert coordinator.snapshot()["enabled"] is False


@pytest.mark.asyncio
async def test_settings_persist_only_new_policy(tmp_path: Path) -> None:
    path = tmp_path / "widget_mode_settings.json"
    first = WidgetModeCoordinator(store=WidgetModeSettingsStore(path))
    await first.update_settings(activity_response="observe_only")
    second = WidgetModeCoordinator(store=WidgetModeSettingsStore(path))

    assert second.settings_snapshot() == {"activity_response": "observe_only"}


@pytest.mark.asyncio
async def test_update_settings_rolls_back_when_persistence_fails(tmp_path: Path) -> None:
    class FailingStore(WidgetModeSettingsStore):
        async def save_async(self, payload: dict) -> None:
            raise OSError("disk full")

    coordinator = WidgetModeCoordinator(
        store=FailingStore(tmp_path / "widget_mode_settings.json"),
    )

    with pytest.raises(OSError, match="disk full"):
        await coordinator.update_settings(activity_response="observe_only")

    assert coordinator.settings_snapshot() == {"activity_response": "disabled"}


@pytest.mark.asyncio
async def test_resource_pressure_is_diagnostic_only() -> None:
    coordinator, _clock, events = build_coordinator(sampler=high_sample)
    await coordinator.set_enabled(True)
    await coordinator.tick_once()
    state = coordinator.snapshot()

    assert state["resource_pressure_state"] == "high"
    assert state["high_resource_sample_count"] == 1
    assert state["last_resource_reason"]["metric"] == "gpu"
    assert state["compaction_phase"] == "idle"
    assert not any(event["type"] == "widget_mode_compaction_requested" for event in events)
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_observe_only_confirms_without_compacting() -> None:
    coordinator, clock, events = build_coordinator()
    await coordinator.set_enabled(True)
    await coordinator.update_settings(activity_response="observe_only")
    await register_capable_pet(coordinator)
    await confirm_activity(coordinator, clock)

    assert coordinator.snapshot()["activity_confirmed"] is True
    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert events == []
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_unavailable_signal_does_not_clear_candidate() -> None:
    coordinator, clock, events = build_coordinator()
    await coordinator.set_enabled(True)
    await coordinator.update_settings(activity_response="observe_only")
    await coordinator.ingest_activity_signal(active=True, available=True, observed_at=clock())
    before = coordinator.snapshot()
    clock.advance(5)
    await coordinator.ingest_activity_signal(active=False, available=False, observed_at=clock())
    after = coordinator.snapshot()

    assert before["activity_signal_count"] == after["activity_signal_count"] == 1
    assert after["activity_signal_available"] is False
    assert events[-1]["type"] == "widget_mode_activity_signal_unavailable"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_explicit_inactive_signal_clears_candidate() -> None:
    coordinator, clock, _events = build_coordinator()
    await coordinator.set_enabled(True)
    await coordinator.update_settings(activity_response="observe_only")
    await coordinator.ingest_activity_signal(active=True, available=True, observed_at=clock())
    await coordinator.ingest_activity_signal(active=False, available=True, observed_at=clock())

    assert coordinator.snapshot()["activity_signal_count"] == 0
    assert coordinator.snapshot()["activity_last_seen_at"] == clock()
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_compaction_ack_creates_owner_then_suspends_and_restores() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    await confirm_activity(coordinator, clock)
    clock.advance(COMPACTION_ACK_TIMEOUT_SECONDS)
    await coordinator.tick_once()

    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    assert events[-1]["compaction_cycle_id"]
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_pending_window_disconnect_recalculates_expected_set() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    await register_capable_pet(coordinator, "pet-2")
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    cycle_id = await confirm_activity(coordinator, clock)
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
async def test_protocol_mismatch_fails_closed() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_activity_compaction(coordinator)
    registration = await coordinator.register_window(
        pet_instance_id="pet-old",
        window_type="pet",
        widget_mode_protocol_version=WIDGET_MODE_PROTOCOL_VERSION + 1,
        widget_mode_compaction_lease_v1=True,
    )
    await confirm_activity(coordinator, clock)

    assert registration["protocol_compatible"] is False
    assert registration["widget_mode_capable"] is False
    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert not any(event["type"] == "widget_mode_compaction_requested" for event in events)
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_chat_window_is_never_counted_as_capable_pet() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_activity_compaction(coordinator)
    registration = await coordinator.register_window(
        pet_instance_id="chat-1",
        window_type="chat",
        widget_mode_protocol_version=WIDGET_MODE_PROTOCOL_VERSION,
        widget_mode_compaction_lease_v1=True,
    )
    await confirm_activity(coordinator, clock)

    assert registration["widget_mode_capable"] is False
    assert coordinator.snapshot()["registered_window_count"] == 0
    assert coordinator.snapshot()["compaction_phase"] == "idle"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_old_cycle_messages_are_ignored() -> None:
    coordinator, clock, _events = build_coordinator()
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    await register_capable_pet(coordinator, "pet-2")
    cycle_id = await confirm_activity(coordinator, clock)
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
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    cycle_id = await confirm_activity(coordinator, clock)
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


@pytest.mark.asyncio
async def test_activity_error_marks_unavailable_without_clearing_candidate() -> None:
    coordinator, clock, events = build_coordinator()
    await coordinator.set_enabled(True)
    await coordinator.update_settings(activity_response="observe_only")
    await coordinator.ingest_activity_signal(active=True, available=True, observed_at=clock())
    state = await coordinator.record_activity_signal_error()

    assert state["activity_signal_available"] is False
    assert state["activity_signal_error_count"] == 1
    assert state["activity_signal_count"] == 1
    assert events[-1]["type"] == "widget_mode_activity_signal_unavailable"
    await coordinator.set_enabled(False)


def test_collect_resource_sample_with_and_without_process_metrics(monkeypatch) -> None:
    class FakeProcess:
        def cpu_percent(self, interval=None):
            return 80.0

        def memory_info(self):
            return SimpleNamespace(rss=64 * 1024 * 1024)

    fake_psutil = SimpleNamespace(
        cpu_percent=lambda interval=None: 33.0,
        virtual_memory=lambda: SimpleNamespace(percent=44.0),
        Process=FakeProcess,
        cpu_count=lambda: 4,
    )
    monkeypatch.setattr(runtime, "_load_psutil", lambda: fake_psutil)
    monkeypatch.setattr(
        runtime,
        "_read_nvidia_gpu_sample",
        lambda _now: {"gpu_percent": 55.0, "gpu_vram_percent": 25.0, "gpu_error": None},
    )
    sample = runtime.collect_resource_sample()

    assert sample["cpu_percent"] == 33.0
    assert sample["memory_percent"] == 44.0
    assert sample["neko_cpu_percent"] == 20.0
    assert sample["neko_memory_mb"] == 64.0
    assert sample["gpu_percent"] == 55.0

    monkeypatch.setattr(runtime, "_load_psutil", lambda: None)
    unavailable = runtime.collect_resource_sample()
    assert unavailable["errors"]["psutil"] == "unavailable"


def test_nvidia_sample_success_failure_and_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_GPU_DISABLED_UNTIL", 0.0)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="71, 512, 1024\ninvalid,row\n",
            stderr="",
        ),
    )
    sample = runtime._read_nvidia_gpu_sample(1000.0)
    assert sample["gpu_percent"] == 71.0
    assert sample["gpu_vram_percent"] == 50.0

    monkeypatch.setattr(runtime, "_GPU_DISABLED_UNTIL", 0.0)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="offline"),
    )
    failed = runtime._read_nvidia_gpu_sample(2000.0)
    assert failed["gpu_error"] == "offline"
    assert runtime._read_nvidia_gpu_sample(2001.0)["gpu_error"] == "cooldown"


def test_settings_store_handles_invalid_json_and_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "widget_mode_settings.json"
    path.write_text("not-json", encoding="utf-8")
    store = WidgetModeSettingsStore(path)
    assert store.load_settings() == {}

    store.save({"activity_response": "observe_only"})
    assert store.load_settings() == {"activity_response": "observe_only"}


def test_settings_store_save_propagates_write_failure(monkeypatch, tmp_path: Path) -> None:
    import utils.file_utils as file_utils

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(file_utils, "atomic_write_json", fail_write)
    store = WidgetModeSettingsStore(tmp_path / "widget_mode_settings.json")

    with pytest.raises(OSError, match="disk full"):
        store.save({"activity_response": "observe_only"})


@pytest.mark.asyncio
async def test_invalid_policy_and_empty_window_id_are_rejected() -> None:
    coordinator, _clock, _events = build_coordinator()
    with pytest.raises(ValueError):
        await coordinator.update_settings(activity_response="unknown")
    with pytest.raises(ValueError):
        await coordinator.register_window(pet_instance_id="")


@pytest.mark.asyncio
async def test_delivery_failure_closes_cycle() -> None:
    coordinator, clock, events = build_coordinator(delivered=0)
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    await confirm_activity(coordinator, clock)

    assert coordinator.snapshot()["compaction_phase"] == "idle"
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_disconnect_of_only_pending_window_fails_cycle() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator)
    await confirm_activity(coordinator, clock)
    state = await coordinator.unregister_window("pet-1")

    assert state["compaction_phase"] == "idle"
    assert events[-1]["type"] == "widget_mode_compaction_failed"
    await coordinator.set_enabled(False)


@pytest.mark.asyncio
async def test_late_join_after_suspension_receives_targeted_request() -> None:
    coordinator, clock, events = build_coordinator()
    await enable_activity_compaction(coordinator)
    await register_capable_pet(coordinator, "pet-1")
    cycle_id = await confirm_activity(coordinator, clock)
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
