import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from main_logic.game_mode_resource_protection import (
    GameModeResourceProtector,
    GameModeSettingsStore,
)


def high_cpu_sample(percent=91.0):
    return {
        "ts": 1000.0,
        "cpu_percent": percent,
        "memory_percent": 40.0,
        "gpu_percent": None,
        "gpu_vram_percent": None,
        "neko_cpu_percent": 2.0,
        "neko_memory_mb": 256.0,
        "errors": {},
    }


def normal_sample():
    sample = high_cpu_sample(percent=30.0)
    sample["memory_percent"] = 40.0
    sample["gpu_percent"] = None
    return sample


def test_game_mode_runtime_starts_disabled():
    protector = GameModeResourceProtector(sampler=high_cpu_sample)
    state = protector.snapshot()

    assert state["enabled"] is False
    assert state["pressure_state"] == "normal"
    assert state["last_samples"] == []
    assert state["trigger_reason"] is None
    assert state["suppressed_until"] is None


@pytest.mark.asyncio
async def test_game_mode_disabled_sampling_does_not_trigger_or_record_samples():
    events = []
    sampler_calls = 0

    async def broadcaster(payload):
        events.append(payload)
        return 1

    def sampler():
        nonlocal sampler_calls
        sampler_calls += 1
        return high_cpu_sample()

    protector = GameModeResourceProtector(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    state = await protector.tick_once()

    assert events == []
    assert sampler_calls == 0
    assert state["enabled"] is False
    assert state["last_samples"] == []
    assert state["trigger_reason"] is None
    assert state["auto_switch_active"] is False


@pytest.mark.asyncio
async def test_game_mode_short_pressure_does_not_record_trigger_reason():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=high_cpu_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    try:
        state = await protector.tick_once()

        assert events == []
        assert state["pressure_state"] == "high"
        assert state["high_sample_count"] == 1
        assert state["trigger_reason"] is None
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_triggers_after_sustained_pressure():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=high_cpu_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    try:
        for _ in range(5):
            await protector.tick_once()
            assert events == []

        state = await protector.tick_once()

        assert len(events) == 1
        assert events[0]["type"] == "game_mode_auto_switch"
        assert events[0]["source"] == "game_mode_auto"
        assert events[0]["reason"] == "cpu"
        assert events[0]["duration_seconds"] == 30.0
        assert state["auto_switch_active"] is True
        assert state["pressure_state"] == "protected"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_long_high_pressure_keeps_protected_state_without_retriggering(caplog):
    events = []
    now = {"value": 1000.0}
    sample_index = {"value": 0}

    async def broadcaster(payload):
        events.append(payload)
        return 1

    def sampler():
        sample_index["value"] += 1
        sample = high_cpu_sample()
        sample["ts"] = now["value"] + sample_index["value"]
        return sample

    protector = GameModeResourceProtector(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )
    caplog.set_level(logging.INFO, logger="main_logic.game_mode_resource_protection")

    await protector.set_enabled(True)
    try:
        for _ in range(18):
            state = await protector.tick_once()

        assert len(events) == 1
        assert state["pressure_state"] == "protected"
        assert state["auto_switch_active"] is True
        assert state["high_sample_count"] == 18
        assert len(state["last_samples"]) == 6
        assert [sample["ts"] for sample in state["last_samples"]] == [1013.0, 1014.0, 1015.0, 1016.0, 1017.0, 1018.0]
    finally:
        await protector.set_enabled(False)

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("[GameModeBeta] auto switch requested: reason=cpu percent=91.0 duration=30.0s delivered=1") == 1
    assert not any("sample" in message.lower() and "unavailable" not in message.lower() for message in messages)


@pytest.mark.asyncio
async def test_game_mode_vram_pressure_is_diagnostic_only():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    def sampler():
        sample = normal_sample()
        sample["gpu_vram_percent"] = 99.0
        return sample

    protector = GameModeResourceProtector(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    try:
        for _ in range(6):
            state = await protector.tick_once()

        assert events == []
        assert state["pressure_state"] == "normal"
        assert state["trigger_reason"] is None
        assert state["last_samples"][-1]["gpu_vram_percent"] == 99.0
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_cpu_memory_still_trigger_when_gpu_metric_is_unavailable():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    def sampler():
        sample = normal_sample()
        sample["memory_percent"] = 89.0
        sample["gpu_percent"] = None
        sample["gpu_vram_percent"] = None
        sample["errors"] = {"gpu": "nvidia-smi failed"}
        return sample

    protector = GameModeResourceProtector(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    try:
        for _ in range(6):
            state = await protector.tick_once()

        assert len(events) == 1
        assert events[0]["reason"] == "memory"
        assert events[0]["percent"] == 89.0
        assert events[0]["sample"]["errors"] == {"gpu": "nvidia-smi failed"}
        assert state["trigger_reason"] == {
            "metric": "memory",
            "percent": 89.0,
            "duration_seconds": 30.0,
        }
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_debug_trigger_broadcasts_required_event_fields():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    try:
        state = await protector.debug_trigger(reason="debug", percent=97.0)

        assert len(events) == 1
        assert events[0]["type"] == "game_mode_auto_switch"
        assert events[0]["source"] == "game_mode_auto"
        assert events[0]["reason"] == "debug"
        assert events[0]["percent"] == 97.0
        assert events[0]["duration_seconds"] == 30.0
        assert events[0]["sample"]["cpu_percent"] == 97.0
        assert state["trigger_reason"] == {
            "metric": "debug",
            "percent": 97.0,
            "duration_seconds": 30.0,
        }
    finally:
        await protector.set_enabled(False)


def test_game_mode_ocr_heavy_dependencies_stay_lazy():
    startup_roots = [
        "app",
        "main_logic",
        "main_routers",
        "utils",
    ]
    for root in startup_roots:
        for path in Path(root).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "import cv2" not in text
            assert "from cv2" not in text

    rapidocr_runtime = Path("plugin/plugins/_shared/rapidocr/_runtime.py").read_text(encoding="utf-8")
    assert "importlib.import_module(RAPIDOCR_PACKAGE_NAME)" in rapidocr_runtime
    shared_init = Path("plugin/plugins/_shared/rapidocr/__init__.py").read_text(encoding="utf-8")
    assert "rapidocr_onnxruntime" not in shared_init

    game_mode_source = Path("main_logic/game_mode_resource_protection.py").read_text(encoding="utf-8")
    assert "load_rapidocr_runtime(" not in game_mode_source
    assert "warmup_async(" not in game_mode_source


@pytest.mark.asyncio
async def test_game_mode_pressure_clear_does_not_restore_shape_state():
    samples = [high_cpu_sample()] * 6 + [normal_sample()]
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    def sampler():
        return samples.pop(0)

    protector = GameModeResourceProtector(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    try:
        for _ in range(6):
            state = await protector.tick_once()

        assert len(events) == 1
        assert state["auto_switch_active"] is True
        assert state["pressure_state"] == "protected"

        state = await protector.tick_once()

        assert state["pressure_state"] == "normal"
        assert state["auto_switch_active"] is True
        assert state["trigger_reason"] == {
            "metric": "cpu",
            "percent": 91.0,
            "duration_seconds": 30.0,
        }
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_manual_restore_starts_cooldown():
    now = {"value": 1000.0}
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=high_cpu_sample,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )

    await protector.set_enabled(True)
    try:
        for _ in range(6):
            await protector.tick_once()
        assert len(events) == 1

        await protector.mark_manual_restore()
        for _ in range(6):
            await protector.tick_once()

        assert len(events) == 1
        assert protector.snapshot()["suppressed_until"] == 1600.0

        now["value"] = 1601.0
        for _ in range(6):
            await protector.tick_once()

        assert len(events) == 2
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_manual_restore_without_auto_switch_does_not_start_cooldown():
    protector = GameModeResourceProtector(
        sampler=normal_sample,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    try:
        state = await protector.mark_manual_restore()

        assert state["enabled"] is True
        assert state["auto_switch_active"] is False
        assert state["manual_override"] is False
        assert state["suppressed_until"] is None
        assert state["last_event"]["type"] == "enabled"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_game_mode_disable_clears_runtime_state():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=high_cpu_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )

    await protector.set_enabled(True)
    for _ in range(6):
        await protector.tick_once()

    assert events
    assert protector.snapshot()["auto_switch_active"] is True

    state = await protector.set_enabled(False)

    assert state["enabled"] is False
    assert state["pressure_state"] == "normal"
    assert state["last_samples"] == []
    assert state["trigger_reason"] is None
    assert state["suppressed_until"] is None
    assert state["auto_switch_active"] is False
    assert state["manual_override"] is False


@pytest.mark.asyncio
async def test_game_mode_key_state_transitions_are_logged(caplog):
    now = {"value": 1000.0}
    samples = [high_cpu_sample()] * 6 + [normal_sample(), normal_sample()]

    async def broadcaster(_payload):
        return 1

    def sampler():
        return samples.pop(0)

    protector = GameModeResourceProtector(
        sampler=sampler,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )

    caplog.set_level(logging.INFO, logger="main_logic.game_mode_resource_protection")

    await protector.set_enabled(True)
    try:
        for _ in range(6):
            await protector.tick_once()
        await protector.tick_once()
        await protector.mark_manual_restore()
        now["value"] = 1601.0
        await protector.tick_once()
    finally:
        await protector.set_enabled(False)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[GameModeBeta] enabled" in messages
    assert "[GameModeBeta] auto switch requested: reason=cpu percent=91.0 duration=30.0s delivered=1" in messages
    assert "[GameModeBeta] pressure cleared" in messages
    assert "[GameModeBeta] manual restore cooldown started" in messages
    assert "[GameModeBeta] manual restore cooldown ended" in messages
    assert "[GameModeBeta] disabled and runtime state cleared" in messages


def test_game_mode_metric_error_logging_is_deduplicated(caplog):
    from main_logic import game_mode_resource_protection as module

    module._METRIC_ERROR_LOGGED.clear()
    caplog.set_level(logging.WARNING, logger="main_logic.game_mode_resource_protection")

    module._remember_metric_error("gpu", RuntimeError("nvidia-smi failed"))
    module._remember_metric_error("gpu", RuntimeError("nvidia-smi failed"))
    module._remember_metric_error("gpu", RuntimeError("nvidia-smi timed out"))

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("[GameModeBeta] gpu sample unavailable: nvidia-smi failed") == 1
    assert messages.count("[GameModeBeta] gpu sample unavailable: nvidia-smi timed out") == 1


@pytest.mark.asyncio
async def test_game_mode_debug_trigger_is_env_gated(monkeypatch):
    from main_logic.game_mode_resource_protection import protector
    from main_routers.game_mode_router import debug_trigger_game_mode_beta
    from main_routers.system_router import _shared as system_router_shared
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/game-mode-beta/debug/trigger",
            "headers": [
                (b"origin", b"http://testserver"),
                (b"x-csrf-token", system_router_shared.AUTOSTART_CSRF_TOKEN.encode()),
            ],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "query_string": b"",
        }
    )
    payload = {"reason": "debug", "percent": 97}

    monkeypatch.delenv("NEKO_GAME_MODE_DEBUG", raising=False)
    monkeypatch.delenv("NEKO_DEBUG", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        await debug_trigger_game_mode_beta(request, payload)
    assert exc_info.value.status_code == 404

    monkeypatch.setenv("NEKO_GAME_MODE_DEBUG", "1")
    try:
        result = await debug_trigger_game_mode_beta(request, payload)

        assert result["success"] is True
        assert result["state"]["enabled"] is True
        assert result["state"]["trigger_reason"] == {
            "metric": "debug",
            "percent": 97.0,
            "duration_seconds": 30.0,
        }
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_debug_health_exposes_game_mode_runtime_fields():
    from main_logic.game_mode_resource_protection import protector
    from main_routers.debug_router import _collect_snapshot

    await protector.set_enabled(False)

    snapshot = _collect_snapshot(include_deep=False, channel="endpoint")
    game_mode = snapshot["game_mode_beta"]

    for key in (
        "enabled",
        "pressure_state",
        "last_samples",
        "trigger_reason",
        "suppressed_until",
    ):
        assert key in game_mode
    assert game_mode["enabled"] is False
    assert game_mode["pressure_state"] == "normal"


@pytest.mark.asyncio
async def test_debug_health_log_includes_game_mode_runtime_fields(tmp_path, monkeypatch):
    from main_logic.game_mode_resource_protection import protector
    from main_routers import debug_router

    await protector.set_enabled(False)

    log_path = tmp_path / "debug_health.jsonl"
    monkeypatch.setattr(debug_router, "_resolve_log_path", lambda: log_path)

    snapshot = debug_router._collect_snapshot(include_deep=False, channel="test")
    debug_router._append_to_log(snapshot)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    game_mode = payload["game_mode_beta"]

    assert game_mode["enabled"] is False
    assert game_mode["pressure_state"] == "normal"
    assert "last_samples" in game_mode
    assert "trigger_reason" in game_mode


def test_debug_health_log_rotates_without_losing_jsonl_suffix(tmp_path, monkeypatch):
    from main_routers import debug_router

    log_path = tmp_path / "debug_health.jsonl"
    rotated_path = tmp_path / "debug_health.jsonl.1"
    log_path.write_text("x" * 64, encoding="utf-8")
    rotated_path.write_text("old rotated data", encoding="utf-8")

    monkeypatch.setattr(debug_router, "_resolve_log_path", lambda: log_path)
    monkeypatch.setattr(debug_router, "_LOG_ROTATE_BYTES", 8)

    debug_router._append_to_log({
        "ts": 1000.0,
        "game_mode_beta": {
            "enabled": False,
            "pressure_state": "normal",
            "last_samples": [],
            "trigger_reason": None,
        },
    })

    assert rotated_path.exists()
    assert rotated_path.read_text(encoding="utf-8") == "x" * 64

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["game_mode_beta"]["enabled"] is False
    assert not (tmp_path / "debug_health.1").exists()


@pytest.mark.asyncio
async def test_exact_game_requires_three_snapshots_and_ten_seconds_before_instant_trigger():
    now = {"value": 1000.0}
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )
    await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
    await protector.set_enabled(True)
    try:
        for timestamp in (1000.0, 1005.0):
            now["value"] = timestamp
            state = await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)
            assert state["cycle_phase"] == "idle"

        now["value"] = 1010.0
        state = await protector.ingest_game_snapshot(exact_game=True, observed_at=1010.0)

        assert state["cycle_phase"] == "protected"
        assert state["cycle_trigger"] == "game_semantic"
        assert len(events) == 1
        assert events[0]["reason"] == "exact_game"
        assert events[0]["duration_seconds"] == 10.0
        assert "window_title" not in json.dumps(events[0])
        assert "process_name" not in json.dumps(events[0])
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_valid_non_game_snapshot_resets_exact_game_confirmation():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(broadcaster=broadcaster, time_fn=lambda: 1015.0)
    await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
    await protector.set_enabled(True)
    try:
        await protector.ingest_game_snapshot(exact_game=True, observed_at=1000.0)
        await protector.ingest_game_snapshot(exact_game=True, observed_at=1005.0)
        state = await protector.ingest_game_snapshot(exact_game=False, valid=True, observed_at=1007.0)
        assert state["game_snapshot_count"] == 0

        await protector.ingest_game_snapshot(exact_game=True, observed_at=1010.0)
        state = await protector.ingest_game_snapshot(exact_game=True, observed_at=1015.0)
        assert state["cycle_phase"] == "idle"
        assert events == []
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_smart_game_mode_uses_70_80_90_thresholds_for_two_consecutive_samples():
    now = {"value": 1010.0}
    events = []

    def smart_sample():
        sample = normal_sample()
        sample["cpu_percent"] = 71.0
        sample["memory_percent"] = 79.0
        sample["gpu_percent"] = 89.0
        return sample

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=smart_sample,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )
    await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="smart")
    await protector.set_enabled(True)
    try:
        for timestamp in (1000.0, 1005.0, 1010.0):
            now["value"] = timestamp
            await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)

        await protector.tick_once()
        assert events == []
        state = await protector.tick_once()

        assert len(events) == 1
        assert events[0]["trigger_source"] == "game_semantic_smart"
        assert events[0]["reason"] == "cpu"
        assert state["cycle_phase"] == "protected"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_registered_pet_requires_ack_then_enters_deep_sleep_after_ninety_seconds():
    now = {"value": 1000.0}
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(
            pet_instance_id="pet-a",
            window_type="pet",
            signal_capabilities={"exact_game": True},
        )
        state = await protector.debug_trigger()
        assert state["cycle_phase"] == "switching"
        cycle_id = state["cycle_id"]

        state = await protector.acknowledge_switch(
            cycle_id=cycle_id,
            pet_instance_id="pet-a",
            status="protected",
        )
        assert state["cycle_phase"] == "protected"
        assert state["owned_window_count"] == 1
        assert state["deep_sleep_due_at"] == 1090.0

        now["value"] = 1090.0
        state = await protector.tick_once()
        assert state["cycle_phase"] == "deep_sleep"
        deep_sleep_events = [item for item in events if item["type"] == "game_mode_deep_sleep"]
        assert deep_sleep_events[-1]["pet_instance_ids"] == ["pet-a"]
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_ack_timeout_aborts_cycle_and_requires_full_reconfirmation_after_backoff():
    now = {"value": 1000.0}
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        state = await protector.debug_trigger()
        assert state["cycle_phase"] == "switching"

        now["value"] = 1005.0
        state = await protector.tick_once()
        assert state["cycle_phase"] == "idle"
        assert state["retry_not_before"] == 1035.0
        assert state["trigger_reason"] is None
        assert any(item["type"] == "game_mode_switch_failed" for item in events)
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_settings_and_manual_restore_cooldown_persist_but_runtime_cycle_does_not(tmp_path):
    path = tmp_path / "game-mode.json"
    now = {"value": 1000.0}

    async def broadcaster(_payload):
        return 1

    first = GameModeResourceProtector(
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
        store=GameModeSettingsStore(path),
    )
    await first.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
    await first.set_enabled(True)
    await first.debug_trigger()
    await first.mark_manual_restore()
    await first.set_enabled(False)

    second = GameModeResourceProtector(
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
        store=GameModeSettingsStore(path),
    )
    assert second.settings_snapshot() == {
        "auto_cat_on_game": True,
        "game_trigger_mode": "instant",
    }
    assert second.snapshot()["cycle_phase"] == "idle"
    assert second.snapshot()["auto_switch_active"] is False

    state = await second.set_enabled(True)
    try:
        assert state["suppressed_until"] == 1600.0
    finally:
        await second.set_enabled(False)


def test_settings_store_path_resolution_failures_are_nonfatal(monkeypatch, caplog, tmp_path):
    store = GameModeSettingsStore(tmp_path / "game-mode.json")

    def fail_path_resolution():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(store, "_resolve_path", fail_path_resolution)
    with caplog.at_level(logging.WARNING):
        assert store.load_settings() == {}
        store.save({"auto_cat_on_game": True})

    assert "failed to load settings" in caplog.text
    assert "failed to persist settings" in caplog.text


def test_settings_store_atomic_write_failures_are_nonfatal(monkeypatch, caplog, tmp_path):
    store = GameModeSettingsStore(tmp_path / "game-mode.json")

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("utils.file_utils.atomic_write_json", fail_write)
    with caplog.at_level(logging.WARNING):
        store.save({"auto_cat_on_game": True})

    assert "failed to persist settings" in caplog.text


@pytest.mark.asyncio
async def test_new_pet_joins_active_device_cycle_without_reloading_first():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(broadcaster=broadcaster, time_fn=lambda: 1000.0)
    await protector.set_enabled(True)
    try:
        await protector.debug_trigger()
        registration = await protector.register_window(
            pet_instance_id="late-pet",
            window_type="pet",
            signal_capabilities={"exact_game": True},
        )
        assert registration["join_as_cat"] is True
        assert registration["cycle_phase"] == "protected"

        state = await protector.acknowledge_switch(
            cycle_id=registration["cycle_id"],
            pet_instance_id="late-pet",
            status="protected",
        )
        assert state["owned_window_count"] == 1
        assert state["auto_switch_active"] is True
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_already_protected_windows_do_not_leave_an_unowned_cycle_active():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(broadcaster=broadcaster, time_fn=lambda: 1000.0)
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-already")
        state = await protector.debug_trigger()
        state = await protector.acknowledge_switch(
            cycle_id=state["cycle_id"],
            pet_instance_id="pet-already",
            status="already_protected",
        )

        assert state["cycle_phase"] == "idle"
        assert state["auto_switch_active"] is False
        assert state["owned_window_count"] == 0
        assert state["last_event"]["type"] == "already_protected"
        assert not any(event["type"] == "game_mode_switch_confirmed" for event in events)
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_mixed_window_cycle_restores_only_game_mode_owned_pet():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(broadcaster=broadcaster, time_fn=lambda: 1000.0)
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-owned")
        await protector.register_window(pet_instance_id="pet-already")
        state = await protector.debug_trigger()
        cycle_id = state["cycle_id"]
        await protector.acknowledge_switch(
            cycle_id=cycle_id,
            pet_instance_id="pet-owned",
            status="protected",
        )
        state = await protector.acknowledge_switch(
            cycle_id=cycle_id,
            pet_instance_id="pet-already",
            status="already_protected",
        )
        assert state["owned_window_count"] == 1

        await protector.set_enabled(False)
        restore = [event for event in events if event["type"] == "game_mode_restore"][-1]
        assert restore["pet_instance_ids"] == ["pet-owned"]
    finally:
        if protector.snapshot()["enabled"]:
            await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_stale_game_signal_notifies_once_per_outage_and_clears_candidate():
    now = {"value": 1000.0}
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: now["value"],
    )
    await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
    await protector.set_enabled(True)
    try:
        await protector.ingest_game_snapshot(exact_game=True, observed_at=1000.0)
        now["value"] = 1016.0
        state = await protector.ingest_game_snapshot(exact_game=False, valid=False, observed_at=1016.0)
        assert state["game_snapshot_count"] == 0
        assert len([event for event in events if event["type"] == "game_mode_semantic_signal_unavailable"]) == 1

        now["value"] = 1032.0
        await protector.ingest_game_snapshot(exact_game=False, valid=False, observed_at=1032.0)
        assert len([event for event in events if event["type"] == "game_mode_semantic_signal_unavailable"]) == 1

        await protector.ingest_game_snapshot(exact_game=True, observed_at=1040.0)
        await protector.ingest_game_snapshot(exact_game=False, valid=False, observed_at=1056.0)
        assert len([event for event in events if event["type"] == "game_mode_semantic_signal_unavailable"]) == 2
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_semantic_fuse_does_not_disable_legacy_pressure_protection():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=high_cpu_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1000.0,
    )
    await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
    await protector.set_enabled(True)
    try:
        for _ in range(3):
            await protector.record_semantic_error()
        assert protector.snapshot()["semantic_fuse_enabled"] is False

        for _ in range(6):
            state = await protector.tick_once()
        assert state["cycle_phase"] == "protected"
        auto_switches = [event for event in events if event["type"] == "game_mode_auto_switch"]
        assert auto_switches[-1]["trigger_source"] == "resource_pressure"
        assert auto_switches[-1]["reason"] == "cpu"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_semantic_errors_are_ignored_until_game_semantic_mode_is_enabled():
    protector = GameModeResourceProtector(sampler=normal_sample)

    for _ in range(3):
        await protector.record_semantic_error()
    assert protector.snapshot()["semantic_error_count"] == 0
    assert protector.snapshot()["semantic_fuse_enabled"] is True

    await protector.set_enabled(True)
    try:
        for _ in range(3):
            await protector.record_semantic_error()
        assert protector.snapshot()["semantic_error_count"] == 0
        assert protector.snapshot()["semantic_fuse_enabled"] is True

        await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
        for _ in range(3):
            await protector.record_semantic_error()
        assert protector.snapshot()["semantic_fuse_enabled"] is False
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_disabling_auto_cat_subfeature_does_not_restore_active_protection_cycle():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(broadcaster=broadcaster, time_fn=lambda: 1000.0)
    await protector.set_settings(auto_cat_on_game=True, game_trigger_mode="instant")
    await protector.set_enabled(True)
    try:
        state = await protector.debug_trigger()
        assert state["auto_switch_active"] is True

        await protector.set_settings(auto_cat_on_game=False, game_trigger_mode="instant")
        state = protector.snapshot()
        assert state["auto_switch_active"] is True
        assert state["cycle_phase"] == "protected"
        assert not any(event["type"] == "game_mode_restore" for event in events)
    finally:
        await protector.set_enabled(False)
