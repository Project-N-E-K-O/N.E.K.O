import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from main_logic import game_mode_resource_protection as game_mode_resource_protection_module
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


def test_resource_sample_reuses_process_for_cpu_delta_sampling(monkeypatch):
    process_calls = 0

    class Process:
        def __init__(self):
            self.cpu_calls = 0

        def cpu_percent(self, interval=None):
            assert interval is None
            self.cpu_calls += 1
            return float(self.cpu_calls * 4)

        def memory_info(self):
            return type("MemoryInfo", (), {"rss": 256 * 1024 * 1024})()

    process = Process()

    class Psutil:
        @staticmethod
        def cpu_percent(interval=None):
            assert interval is None
            return 25.0

        @staticmethod
        def virtual_memory():
            return type("VirtualMemory", (), {"percent": 40.0})()

        @staticmethod
        def cpu_count():
            return 4

        @staticmethod
        def Process():
            nonlocal process_calls
            process_calls += 1
            return process

    monkeypatch.setattr(game_mode_resource_protection_module, "_NEKO_PROCESS", None, raising=False)
    monkeypatch.setattr(game_mode_resource_protection_module, "_load_psutil", lambda: Psutil)
    monkeypatch.setattr(
        game_mode_resource_protection_module,
        "_read_nvidia_gpu_sample",
        lambda _now: {"gpu_percent": None, "gpu_vram_percent": None, "gpu_error": None},
    )

    first = game_mode_resource_protection_module.collect_resource_sample()
    second = game_mode_resource_protection_module.collect_resource_sample()

    assert process_calls == 1
    assert first["neko_cpu_percent"] == 1.0
    assert second["neko_cpu_percent"] == 2.0
    assert second["neko_memory_mb"] == 256.0


def test_game_mode_runtime_starts_disabled():
    protector = GameModeResourceProtector(sampler=high_cpu_sample)
    state = protector.snapshot()

    assert state["enabled"] is False
    assert state["pressure_state"] == "normal"
    assert state["last_samples"] == []
    assert state["resource_session_phase"] == "idle"

@pytest.mark.asyncio
async def test_game_mode_short_pressure_does_not_emit_resource_event():
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
    finally:
        await protector.set_enabled(False)


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
        assert state["last_samples"][-1]["gpu_vram_percent"] == 99.0
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


async def test_debug_health_exposes_game_mode_resource_runtime_fields():
    from main_logic.game_mode_resource_protection import protector
    from main_routers.debug_router import _collect_snapshot

    await protector.set_enabled(False)
    game_mode = _collect_snapshot(include_deep=False, channel="endpoint")["game_mode_beta"]

    for key in (
        "enabled",
        "pressure_state",
        "last_samples",
        "resource_session_phase",
        "resource_windows",
        "settings",
    ):
        assert key in game_mode
    assert game_mode["enabled"] is False
    assert game_mode["resource_session_phase"] == "idle"

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
    assert game_mode["resource_session_phase"] == "idle"


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
            "resource_session_phase": "idle",
        },
    })

    assert rotated_path.exists()
    assert rotated_path.read_text(encoding="utf-8") == "x" * 64

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["game_mode_beta"]["enabled"] is False
    assert not (tmp_path / "debug_health.1").exists()


def test_settings_store_path_resolution_failures_are_nonfatal(monkeypatch, caplog, tmp_path):
    store = GameModeSettingsStore(tmp_path / "game-mode.json")

    def fail_path_resolution():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(store, "_resolve_path", fail_path_resolution)
    with caplog.at_level(logging.WARNING):
        assert store.load_settings() == {}
        store.save({"resource_protection_on_game": False})

    assert "failed to load settings" in caplog.text
    assert "failed to persist settings" in caplog.text


def test_settings_store_atomic_write_failures_are_nonfatal(monkeypatch, caplog, tmp_path):
    store = GameModeSettingsStore(tmp_path / "game-mode.json")

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("utils.file_utils.atomic_write_json", fail_write)
    with caplog.at_level(logging.WARNING):
        store.save({"resource_protection_on_game": False})

    assert "failed to persist settings" in caplog.text


def test_resource_protection_settings_exclude_model_switching_contract():
    protector = GameModeResourceProtector(sampler=normal_sample)

    assert protector.settings_snapshot() == {
        "resource_protection_on_game": True,
        "compact_pet_window_enabled": True,
    }


async def test_exact_game_starts_independent_resource_session():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 2

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1010.0,
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        await protector.register_window(pet_instance_id="pet-b")
        await protector.ingest_game_snapshot(exact_game=True, observed_at=1000.0)
        await protector.ingest_game_snapshot(exact_game=True, observed_at=1005.0)
        state = await protector.ingest_game_snapshot(exact_game=True, observed_at=1010.0)

        assert [event["type"] for event in events] == ["game_mode_resource_protection_enter"]
        assert events[0]["pet_instance_ids"] == ["pet-a", "pet-b"]
        assert events[0]["target_fps"] == 15
        assert events[0]["deep_sleep_after_seconds"] == 90.0
        assert state["resource_session_phase"] == "soft_protected"
        assert state["resource_session_id"] == events[0]["resource_session_id"]
    finally:
        await protector.set_enabled(False)

@pytest.mark.asyncio
async def test_resource_session_exit_requires_three_non_game_snapshots_over_ten_seconds():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1030.0,
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        for timestamp in (1000.0, 1005.0, 1010.0):
            await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)

        await protector.ingest_game_snapshot(exact_game=False, observed_at=1015.0)
        short_alt_tab = await protector.ingest_game_snapshot(exact_game=True, observed_at=1020.0)
        assert short_alt_tab["resource_session_phase"] == "soft_protected"
        assert [event["type"] for event in events] == ["game_mode_resource_protection_enter"]

        await protector.ingest_game_snapshot(exact_game=False, observed_at=1021.0)
        await protector.ingest_game_snapshot(exact_game=False, observed_at=1026.0)
        exited = await protector.ingest_game_snapshot(exact_game=False, observed_at=1031.0)

        assert [event["type"] for event in events] == [
            "game_mode_resource_protection_enter",
            "game_mode_resource_protection_restore",
        ]
        assert events[-1]["reason"] == "game-exited"
        assert exited["resource_session_phase"] == "idle"
        assert exited["resource_session_id"] is None
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_resource_session_exits_after_thirty_seconds_without_valid_activity_signal():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1041.0,
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        for timestamp in (1000.0, 1005.0, 1010.0):
            await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)

        still_active = await protector.ingest_game_snapshot(
            exact_game=False,
            valid=False,
            observed_at=1040.0,
        )
        assert still_active["resource_session_phase"] == "soft_protected"

        exited = await protector.ingest_game_snapshot(
            exact_game=False,
            valid=False,
            observed_at=1040.1,
        )
        assert exited["resource_session_phase"] == "idle"
        assert events[-1]["type"] == "game_mode_resource_protection_restore"
        assert events[-1]["reason"] == "activity-signal-unavailable"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_resource_window_phase_ack_and_explicit_interaction_are_per_window():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 2

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1020.0,
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        await protector.register_window(pet_instance_id="pet-b")
        for timestamp in (1000.0, 1005.0, 1010.0):
            state = await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)
        session_id = state["resource_session_id"]

        await protector.acknowledge_resource_phase(
            resource_session_id=session_id,
            pet_instance_id="pet-a",
            phase="deep_sleep",
            compact_lease="acquired",
        )
        state = await protector.record_resource_interaction(
            resource_session_id=session_id,
            pet_instance_id="pet-a",
            interaction="click",
        )

        assert state["resource_windows"]["pet-a"]["phase"] == "soft_protected"
        assert state["resource_windows"]["pet-a"]["last_interaction_at"] == 1020.0
        assert state["resource_windows"]["pet-a"]["deep_sleep_due_at"] == 1110.0
        assert state["resource_windows"]["pet-b"]["phase"] == "soft_protected"
        assert state["resource_windows"]["pet-a"]["compact_lease"] == "acquired"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_explicit_resource_exit_restores_and_latches_until_confirmed_game_exit():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1020.0,
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        for timestamp in (1000.0, 1005.0, 1010.0):
            state = await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)
        session_id = state["resource_session_id"]

        exited = await protector.exit_resource_session(
            resource_session_id=session_id,
            reason="user-exit",
        )
        assert exited["resource_manual_exit_latched"] is True
        for timestamp in (1011.0, 1016.0, 1021.0):
            state = await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)
        assert state["resource_session_id"] is None

        for timestamp in (1022.0, 1027.0, 1032.0):
            state = await protector.ingest_game_snapshot(exact_game=False, observed_at=timestamp)
        assert state["resource_manual_exit_latched"] is False
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_resource_protection_uses_dynamic_diagnostic_sampling_interval_and_setting_kill_switch():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1010.0,
    )
    assert protector.sampling_interval_seconds() == 5.0
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        for timestamp in (1000.0, 1005.0, 1010.0):
            await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)
        assert protector.sampling_interval_seconds() == 30.0

        await protector.set_settings(
            resource_protection_on_game=False,
            compact_pet_window_enabled=True,
        )
        assert protector.snapshot()["resource_session_phase"] == "idle"
        assert protector.sampling_interval_seconds() == 5.0
        assert events[-1]["reason"] == "resource-protection-disabled"
    finally:
        await protector.set_enabled(False)


@pytest.mark.asyncio
async def test_disabling_compact_window_releases_active_leases_without_ending_protection():
    events = []

    async def broadcaster(payload):
        events.append(payload)
        return 1

    protector = GameModeResourceProtector(
        sampler=normal_sample,
        broadcaster=broadcaster,
        time_fn=lambda: 1010.0,
    )
    await protector.set_enabled(True)
    try:
        await protector.register_window(pet_instance_id="pet-a")
        for timestamp in (1000.0, 1005.0, 1010.0):
            await protector.ingest_game_snapshot(exact_game=True, observed_at=timestamp)
        session_id = protector.snapshot()["resource_session_id"]

        await protector.set_settings(
            resource_protection_on_game=True,
            compact_pet_window_enabled=False,
        )

        state = protector.snapshot()
        assert state["resource_session_phase"] == "soft_protected"
        assert state["resource_session_id"] == session_id
        assert state["resource_windows"]["pet-a"]["compact_lease"] == "disabled"
        assert [event["type"] for event in events] == [
            "game_mode_resource_protection_enter",
            "game_mode_resource_protection_compact_release",
        ]
        assert events[-1]["resource_session_id"] == session_id
        assert events[-1]["pet_instance_ids"] == ["pet-a"]
        assert events[-1]["reason"] == "compact-window-disabled"
    finally:
        await protector.set_enabled(False)


def test_activity_signal_has_no_removed_semantic_fuse_fallback():
    source = Path("main_routers/system_router/activity_signal.py").read_text(encoding="utf-8")

    assert "record_semantic_error" not in source
    assert "Game Mode semantic fuse update failed" not in source
