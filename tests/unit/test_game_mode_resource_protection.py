import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from main_logic.game_mode_resource_protection import GameModeResourceProtector


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

    monkeypatch.delenv("NEKO_GAME_MODE_DEBUG", raising=False)
    monkeypatch.delenv("NEKO_DEBUG", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        await debug_trigger_game_mode_beta({"reason": "debug", "percent": 97})
    assert exc_info.value.status_code == 404

    monkeypatch.setenv("NEKO_GAME_MODE_DEBUG", "1")
    try:
        result = await debug_trigger_game_mode_beta({"reason": "debug", "percent": 97})

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
