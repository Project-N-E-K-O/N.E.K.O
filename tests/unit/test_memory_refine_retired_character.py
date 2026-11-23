import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.memory_server import gates, refine_loops, review, runtime
from memory import FactStore, PersonaManager, ReflectionEngine


def _make_cm(tmp_path: Path, catgirls=None):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    chars = {"猫娘": dict(catgirls or {})}
    return SimpleNamespace(
        memory_dir=str(mem_dir),
        aload_characters=AsyncMock(return_value=chars),
        load_characters=lambda: chars,
    )


def _memory_stores(cm):
    pm = PersonaManager()
    fs = FactStore()
    engine = ReflectionEngine(fs, pm)
    pm._config_manager = fs._config_manager = engine._config_manager = cm
    return pm, engine, fs


@pytest.mark.asyncio
async def test_f0082_introspection_tick_skips_retired_or_deleted_character_before_peek(
    tmp_path, monkeypatch,
):
    cm = _make_cm(tmp_path, {"已删除角色": {"性格": "开朗"}})
    pm, engine, _ = _memory_stores(cm)
    monkeypatch.setattr(runtime, "_config_manager", cm, raising=False)
    monkeypatch.setattr(runtime, "persona_manager", pm, raising=False)
    monkeypatch.setattr(runtime, "reflection_engine", engine, raising=False)
    monkeypatch.setattr(gates, "_is_idle", lambda: True)
    monkeypatch.setattr(gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(refine_loops, "_introspection_due", lambda _n, _now: False)
    monkeypatch.setattr(refine_loops, "_behavior_review_due", lambda _n, _now: False)

    # Simulate character deleted between initial roster load and lock acquisition
    initial_cm_calls = 0

    async def _aload_characters():
        nonlocal initial_cm_calls
        initial_cm_calls += 1
        if initial_cm_calls == 1:
            return {"猫娘": {"已删除角色": {"性格": "开朗"}}}
        return {"猫娘": {}}

    cm.aload_characters = _aload_characters
    peek_calls = []
    orig_peek = refine_loops._next_scoped_introspection_subject

    async def _spy_next_subject(name, now):
        peek_calls.append(name)
        return await orig_peek(name, now)

    monkeypatch.setattr(refine_loops, "_next_scoped_introspection_subject", _spy_next_subject)
    monkeypatch.setattr(review, "_review_spawn_locks", {})
    monkeypatch.setattr(review, "_retired_derived_task_names", set())

    await refine_loops._run_introspection_tick()

    assert peek_calls == []
    assert not (tmp_path / "memory" / "已删除角色").exists()


@pytest.mark.asyncio
async def test_f0082_read_only_scans_never_recreate_missing_character_dir(tmp_path, monkeypatch):
    cm = _make_cm(tmp_path, {})
    pm, engine, _ = _memory_stores(cm)
    monkeypatch.setattr(runtime, "_config_manager", cm, raising=False)
    monkeypatch.setattr(runtime, "persona_manager", pm, raising=False)
    monkeypatch.setattr(runtime, "reflection_engine", engine, raising=False)

    name = "不存在角色"
    char_dir = tmp_path / "memory" / name
    assert not char_dir.exists()

    contents = await refine_loops._scoped_introspection_contents(name)
    assert contents == []
    assert not char_dir.exists()

    await refine_loops._run_persona_refine_for_character(name)
    assert not char_dir.exists()

    served = await refine_loops._run_scoped_refine_for_character(name)
    assert served is False
    assert not char_dir.exists()


@pytest.mark.asyncio
async def test_f0082_reflection_refine_never_recreates_missing_character_dir(
    tmp_path, monkeypatch,
):
    cm = _make_cm(tmp_path, {})
    pm, engine, fs = _memory_stores(cm)
    monkeypatch.setattr(runtime, "_config_manager", cm, raising=False)
    monkeypatch.setattr(runtime, "persona_manager", pm, raising=False)
    monkeypatch.setattr(runtime, "reflection_engine", engine, raising=False)
    monkeypatch.setattr(runtime, "fact_store", fs, raising=False)
    monkeypatch.setattr("memory.organizer._gate", None)

    name = "缺失反思目录角色"
    char_dir = tmp_path / "memory" / name
    assert not char_dir.exists()

    resolved = await refine_loops._run_reflection_refine_for_character(name)
    assert resolved == 0
    assert not char_dir.exists()

    await refine_loops._run_reflection_refine_with_subject_locales(name)
    assert not char_dir.exists()


@pytest.mark.asyncio
async def test_f0082_release_cancels_and_drains_in_flight_refine_task(tmp_path, monkeypatch):
    name = "正在精炼角色"
    cm = _make_cm(tmp_path, {name: {"性格": "认真"}})
    pm, engine, _ = _memory_stores(cm)
    monkeypatch.setattr(runtime, "_config_manager", cm, raising=False)
    monkeypatch.setattr(runtime, "persona_manager", pm, raising=False)
    monkeypatch.setattr(runtime, "reflection_engine", engine, raising=False)
    monkeypatch.setattr(review, "_review_spawn_locks", {})
    monkeypatch.setattr(review, "_retired_derived_task_names", set())
    monkeypatch.setattr(review, "persona_refine_tasks", {})

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _slow_refine(char_name):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _passthru_locale(_name, fn, *args):
        return await fn(*args)

    monkeypatch.setattr(refine_loops, "run_with_character_prompt_locale", _passthru_locale)

    run_task = asyncio.create_task(
        refine_loops._run_tracked_refine_character(name, _slow_refine, registry=review.persona_refine_tasks)
    )
    await started.wait()

    # A reflection pass must still run while the persona pass is waiting on the model.
    reflection_pass = AsyncMock(return_value=7)
    assert await refine_loops._run_tracked_refine_character(
        name, reflection_pass, registry=review.reflection_refine_tasks,
    ) == (True, 7)
    reflection_pass.assert_awaited_once_with(name)

    async with review._get_review_spawn_lock(name):
        drained = await review._cancel_character_derived_tasks_unlocked(name)

    assert drained == 1
    assert cancelled.is_set()
    ran, result = await run_task
    assert ran is False and result is None
    assert name not in review.persona_refine_tasks
    assert not (tmp_path / "memory" / name).exists()


@pytest.mark.asyncio
async def test_missing_reflections_respect_open_recovery_journal(tmp_path):
    from memory.long_term_sync import JOURNAL, LongTermMemoryRecoveryRequired

    cm = _make_cm(tmp_path)
    _, engine, _ = _memory_stores(cm)
    directory = Path(cm.memory_dir) / "recovering"
    directory.mkdir()
    (directory / JOURNAL).write_text('{"phase":"applying"}')
    with pytest.raises(LongTermMemoryRecoveryRequired):
        await engine.aload_reflections("recovering")
    assert not (directory / "reflections.json").exists()


@pytest.mark.asyncio
async def test_cancelled_persona_writer_retires_before_unlock(tmp_path, monkeypatch):
    import json
    import threading
    from memory.persona import persistence
    from utils import file_utils

    pm, _, _ = _memory_stores(_make_cm(tmp_path))
    name = "writing"
    started = threading.Event()
    release = threading.Event()
    real_write = file_utils.atomic_write_json

    def paused_write(path, payload, **kwargs):
        if payload["value"] == "old":
            started.set()
            assert release.wait(3)
        real_write(path, payload, **kwargs)

    monkeypatch.setattr(persistence, "assert_cloudsave_writable", lambda *a, **kw: None)
    monkeypatch.setattr(file_utils, "atomic_write_json", paused_write)

    async def save(value):
        async with pm._get_alock(name):
            await pm.asave_persona(name, {"value": value})

    old = asyncio.create_task(save("old"))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        old.cancel()
        new = asyncio.create_task(save("new"))
        await asyncio.sleep(0)
        assert pm._get_alock(name).locked() and not old.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await old
    await new
    assert json.loads((tmp_path / "memory" / name / "persona.json").read_text()) == {"value": "new"}


@pytest.mark.asyncio
async def test_cancelled_reflection_writer_retires_before_unlock(tmp_path, monkeypatch):
    import json
    import threading
    from memory.reflection import persistence
    from utils import file_utils

    _, engine, _ = _memory_stores(_make_cm(tmp_path))
    name = "writing"
    started = threading.Event()
    release = threading.Event()
    real_write = file_utils.atomic_write_json

    def paused_write(path, payload, **kwargs):
        if payload and payload[0].get("content") == "old":
            started.set()
            assert release.wait(3)
        real_write(path, payload, **kwargs)

    monkeypatch.setattr(persistence, "assert_cloudsave_writable", lambda *a, **kw: None)
    monkeypatch.setattr(file_utils, "atomic_write_json", paused_write)

    async def save(content):
        async with engine._get_alock(name):
            await engine.asave_reflections(name, [{"id": "r1", "content": content}])

    old = asyncio.create_task(save("old"))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        old.cancel()
        new = asyncio.create_task(save("new"))
        await asyncio.sleep(0)
        assert engine._get_alock(name).locked() and not old.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await old
    await new
    assert json.loads((tmp_path / "memory" / name / "reflections.json").read_text(encoding="utf-8")) == [
        {"id": "r1", "content": "new"}
    ]
