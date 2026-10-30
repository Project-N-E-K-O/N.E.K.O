"""One repair owns native runtime, model publication and final readiness."""

import asyncio

import pytest

from config.voice_wake_word import save_wake_word_preference
from main_logic.voice_identity_service import resource_manager as rm


@pytest.mark.asyncio
@pytest.mark.parametrize(("failed_kind", "reason"), [
    ("runtime-stage", "WAKE_WORD_PLATFORM_UNSUPPORTED"),
    ("runtime-stage", "resource_model_repair_failed"),
    ("runtime-stage", "resource_cache_full"),
    ("download", "resource_cache_unsafe"),
    ("download", "resource_prepare_timeout"),
])
async def test_optional_wake_failure_preserves_primary_repair_and_exact_reason(tmp_path, monkeypatch, failed_kind, reason):
    monkeypatch.delenv("NEKO_WAKE_WORD_MODEL_DIR", raising=False)
    monkeypatch.delenv("NEKO_WAKE_WORD_ENABLED", raising=False)
    save_wake_word_preference(True, tmp_path)
    before = (tmp_path / "preference.json").read_bytes()
    calls = []
    primary = {name: rm._resource("ready", None, True) for name in ("campp", "silero", "noise_reduction")}

    async def worker(kind, *args, **kwargs):
        calls.append(kind)
        if kind == failed_kind:
            raise rm.VoiceResourceError(reason)
        if kind == "runtime-check":
            return {"ready": failed_kind == "download"}
        assert kind == "repair"
        return {**primary, "wake_runtime": rm._resource("ready", None, False)}

    async def refresh(operation_id):
        calls.append("refresh")

    monkeypatch.setattr(rm, "_run_worker", worker)
    monkeypatch.setattr(rm, "source_model_repair_available", lambda **kwargs: True)
    monkeypatch.setattr(rm, "resolve_verified_campplus_asset", lambda: None)
    monkeypatch.setattr(rm, "resolve_verified_assets", lambda names: {})
    manager = rm.VoiceResourceManager(lambda: False, cache_root=tmp_path, on_ready=refresh)
    try:
        operation = manager.start("repair")
        await manager._current.task
        result = manager.operation(operation["operation_id"])
        assert result["state"] == "succeeded"
        assert calls == ["runtime-check", failed_kind, "repair", "refresh"]
        assert {name: result["result"][name] for name in primary} == primary
        snapshot = await manager.resources()
        assert snapshot["can_enroll"] is True
        assert {key: snapshot["resources"]["wake_model"][key] for key in ("state", "reason", "required")} == rm._resource("unavailable", reason, True)
        if failed_kind == "runtime-stage":
            assert {key: snapshot["resources"]["wake_runtime"][key] for key in ("state", "reason", "required")} == rm._resource("unavailable", reason, True)
        assert (tmp_path / "preference.json").read_bytes() == before
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_kind", ["runtime-stage", "download", "runtime-publish", "publish"])
@pytest.mark.parametrize("reason", ["operation_cancelled", "resource_operation_busy", "resource_worker_shutdown_failed"])
async def test_wake_control_or_retirement_failure_never_starts_primary(tmp_path, monkeypatch, failed_kind, reason):
    monkeypatch.delenv("NEKO_WAKE_WORD_MODEL_DIR", raising=False)
    monkeypatch.delenv("NEKO_WAKE_WORD_ENABLED", raising=False)
    save_wake_word_preference(True, tmp_path)
    calls = []

    async def worker(kind, *args, **kwargs):
        calls.append(kind)
        if kind == failed_kind:
            raise rm.VoiceResourceError(reason)
        return {"runtime-check": {"ready": failed_kind in {"download", "publish"}},
                "runtime-stage": {"installed": False, "ticket": "a" * 32},
                "download": {"version": "model"}}[kind]

    async def refresh(operation_id):
        pytest.fail("Control failure cannot refresh readiness")

    monkeypatch.setattr(rm, "_run_worker", worker)
    manager = rm.VoiceResourceManager(lambda: False, cache_root=tmp_path, on_ready=refresh)
    try:
        operation = manager.start("repair")
        await manager._current.task
        result = manager.operation(operation["operation_id"])
        assert result["reason"] == reason
        assert result["state"] in {"cancelled", "failed"}
        assert calls[-1] == failed_kind and "repair" not in calls
        assert manager._prepared == {}
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prepare", "repair"])
@pytest.mark.parametrize("broken", [b"truncated", b"x" * 1025, b'{"schema":2,"enabled":true}', None],
                         ids=["truncated", "oversize", "schema", "managed-invalid"])
async def test_invalid_wake_preference_does_not_block_primary_resources(tmp_path, monkeypatch, kind, broken):
    monkeypatch.delenv("NEKO_WAKE_WORD_MODEL_DIR", raising=False)
    monkeypatch.delenv("NEKO_WAKE_WORD_ENABLED", raising=False)
    if broken is None:
        monkeypatch.setenv("NEKO_WAKE_WORD_ENABLED", "invalid")
    else:
        (tmp_path / "preference.json").write_bytes(broken)
    calls = []
    primary = {name: rm._resource("ready", None, True) for name in ("campp", "silero", "noise_reduction")}

    async def worker(worker_kind, *args, **kwargs):
        calls.append(worker_kind)
        assert worker_kind == kind, "Unknown wake preference must not start optional downloads"
        return {**primary, "wake_model": rm._resource("ready", None, False)}

    async def refresh(operation_id):
        calls.append("refresh")

    monkeypatch.setattr(rm, "_run_worker", worker)
    monkeypatch.setattr(rm, "source_model_repair_available", lambda **kwargs: True)
    # A broken cache must not mask the more fundamental unknown preference.
    def broken_cache(root):
        raise rm.WakeWordBundleError("resource_cache_unsafe")
    monkeypatch.setattr(rm, "resolve_cached_model_dir", broken_cache)
    manager = rm.VoiceResourceManager(lambda: False, cache_root=tmp_path, on_ready=refresh)
    try:
        operation = manager.start(kind)
        await manager._current.task
        result = manager.operation(operation["operation_id"])
        assert result["state"] == "succeeded"
        assert calls == [kind, "refresh"]
        assert {name: result["result"][name] for name in primary} == primary
        assert result["result"]["wake_model"] == rm._resource("unavailable", "WAKE_WORD_PREFERENCE_UNAVAILABLE", True)
        assert manager._prepared == result["result"]
        if broken is not None:
            assert (tmp_path / "preference.json").read_bytes() == broken
        else:
            assert not (tmp_path / "preference.json").exists()
    finally:
        await manager.close()


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
