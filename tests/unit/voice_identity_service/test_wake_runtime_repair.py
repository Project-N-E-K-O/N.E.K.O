"""One repair owns native runtime, model publication and final readiness."""

import asyncio

import pytest

from config.voice_wake_word import save_wake_word_preference
from main_logic.voice_identity_service import resource_manager as rm


@pytest.mark.asyncio
async def test_one_repair_installs_runtime_then_model_then_prepares(tmp_path, monkeypatch):
    cache = tmp_path / "wake-word"
    save_wake_word_preference(True, cache)
    calls = []
    prepared = {"campp": rm._resource("ready", None, True)}
    async def worker(kind, *args, **kwargs):
        calls.append(kind)
        return {"runtime-check": {"ready": False}, "runtime-stage": {"installed": False, "ticket": "a" * 32},
                "runtime-publish": {"installed": True}, "download": {"version": "model"},
                "publish": {"installed": True}, "repair": prepared}[kind]
    async def refresh(operation_id):
        calls.append("refresh")
    monkeypatch.setattr(rm, "_run_worker", worker)
    manager = rm.VoiceResourceManager(lambda: False, cache_root=cache, on_ready=refresh)
    operation = manager.start("repair")
    await manager._current.task
    assert calls == ["runtime-check", "runtime-stage", "runtime-publish", "download", "publish", "repair", "refresh"]
    assert manager.operation(operation["operation_id"])["state"] == "succeeded"
    assert manager._prepared == prepared
    await manager.close()


@pytest.mark.asyncio
async def test_disabled_wake_never_installs_optional_runtime(tmp_path, monkeypatch):
    save_wake_word_preference(False, tmp_path)
    calls = []
    async def worker(kind, *args, **kwargs):
        calls.append(kind)
        assert kind == "repair"
        return {}
    monkeypatch.setattr(rm, "_run_worker", worker)
    manager = rm.VoiceResourceManager(lambda: False, cache_root=tmp_path)
    manager.start("repair")
    await manager._current.task
    assert calls == ["repair"]
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["runtime-check", "runtime-stage", "runtime-publish", "download", "publish"])
async def test_cancellation_stops_next_stage_and_readiness(tmp_path, monkeypatch, cancel_at):
    save_wake_word_preference(True, tmp_path)
    entered, release, retired = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    async def worker(kind, *args, **kwargs):
        calls.append(kind)
        if kind == cancel_at:
            entered.set()
            try:
                await release.wait()
            finally:
                retired.set()
        return {"runtime-check": {"ready": False}, "runtime-stage": {"installed": False, "ticket": "a" * 32},
                "runtime-publish": {"installed": True}, "download": {"version": "model"},
                "publish": {"installed": True}}[kind]
    async def refresh(operation_id):
        pytest.fail("Cancelled repair cannot publish readiness")
    monkeypatch.setattr(rm, "_run_worker", worker)
    manager = rm.VoiceResourceManager(lambda: False, cache_root=tmp_path, on_ready=refresh)
    operation = manager.start("repair")
    try:
        await asyncio.wait_for(entered.wait(), 3)
        cancellation = asyncio.create_task(manager.cancel(operation["operation_id"]))
        if cancel_at in {"runtime-publish", "publish"}:
            # Commit is bounded and allowed to finish, cancellation applies
            # before another worker starts, not halfway through atomic rename.
            await asyncio.sleep(0)
            assert not cancellation.done()
            release.set()
        result = await asyncio.wait_for(cancellation, 3)
        assert result["state"] in {"failed", "cancelled"}
        assert calls[-1] == cancel_at and retired.is_set()
        assert manager._prepared == {}
    finally:
        release.set()
        await manager.close()
