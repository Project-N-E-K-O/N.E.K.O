"""A failed proactive_chat_totals load must never overwrite disk totals."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import pytest

from main_logic.proactive_chat import state
from utils.file_utils import read_json as _real_read_json


@pytest.fixture(autouse=True)
def _isolate_proactive_totals_globals():
    saved_totals = dict(state._proactive_chat_totals)
    saved_ever = dict(state._invite_ever_delivered)
    saved_loaded = state._proactive_chat_totals_loaded
    saved_path = state._proactive_chat_totals_loaded_path
    saved_auth = state._proactive_chat_totals_authoritative
    saved_failures = state._proactive_chat_totals_read_failures
    saved_skipped = state._proactive_chat_totals_skipped_records
    saved_lock = state._proactive_chat_totals_lock

    state._proactive_chat_totals_lock = asyncio.Lock()
    state._proactive_chat_totals.clear()
    state._invite_ever_delivered.clear()
    state._proactive_chat_totals_loaded = False
    state._proactive_chat_totals_loaded_path = None
    state._proactive_chat_totals_authoritative = True
    state._proactive_chat_totals_read_failures = 0
    state._proactive_chat_totals_skipped_records = 0
    yield
    state._proactive_chat_totals.clear()
    state._proactive_chat_totals.update(saved_totals)
    state._invite_ever_delivered.clear()
    state._invite_ever_delivered.update(saved_ever)
    state._proactive_chat_totals_loaded = saved_loaded
    state._proactive_chat_totals_loaded_path = saved_path
    state._proactive_chat_totals_authoritative = saved_auth
    state._proactive_chat_totals_read_failures = saved_failures
    state._proactive_chat_totals_skipped_records = saved_skipped
    state._proactive_chat_totals_lock = saved_lock


def _totals_path(root: Path) -> Path:
    return root / state._PROACTIVE_CHAT_TOTALS_FILENAME


def _seed_totals(root: Path, totals: dict, ever: dict | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state._PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION,
        "totals": dict(totals),
        "ever_delivered": dict(ever or {}),
    }
    _totals_path(root).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


@pytest.mark.asyncio
async def test_f0173_transient_read_failure_never_truncates_disk_totals(monkeypatch, tmp_path):
    root = tmp_path / "memory"
    seeded = _seed_totals(root, {"A": 5, "B": 3}, {"A": True})

    def _fail_read(_path):
        raise PermissionError(13, "locked by another process")

    monkeypatch.setattr(state, "read_json", _fail_read)

    await state._increment_proactive_chat_total("C", memory_dir=root)
    await state._record_invite_delivery_persistent("C", memory_dir=root)
    await state._mark_invite_ever_delivered("C", memory_dir=root)

    assert json.loads(_totals_path(root).read_text(encoding="utf-8")) == seeded
    assert state._proactive_chat_totals_loaded is False
    assert state._proactive_chat_totals_loaded_path is None
    assert state._proactive_chat_totals_authoritative is False


@pytest.mark.asyncio
async def test_f0173_totals_resume_once_read_recovers(monkeypatch, tmp_path):
    root = tmp_path / "memory"
    _seed_totals(root, {"A": 5, "B": 3}, {"A": True})

    fail = True

    def _flaky_read(path):
        if fail:
            raise OSError("transient")
        return _real_read_json(path)

    monkeypatch.setattr(state, "read_json", _flaky_read)

    await state._increment_proactive_chat_total("C", memory_dir=root)
    assert json.loads(_totals_path(root).read_text(encoding="utf-8"))["totals"] == {"A": 5, "B": 3}

    fail = False
    new_val = await state._increment_proactive_chat_total("C", memory_dir=root)
    assert new_val == 1
    disk = json.loads(_totals_path(root).read_text(encoding="utf-8"))
    assert disk["totals"] == {"A": 5, "B": 3, "C": 1}
    assert disk["ever_delivered"] == {"A": True}


@pytest.mark.asyncio
async def test_f0173_corrupt_totals_json_starts_empty_and_self_heals(tmp_path):
    root = tmp_path / "memory"
    root.mkdir(parents=True, exist_ok=True)
    _totals_path(root).write_text("{corrupt json", encoding="utf-8")

    ok = await state._ensure_proactive_chat_totals_loaded(memory_dir=root)
    assert ok is True
    assert state._proactive_chat_totals == {}

    val = await state._record_invite_delivery_persistent("Mimi", memory_dir=root)
    assert val == 1
    disk = json.loads(_totals_path(root).read_text(encoding="utf-8"))
    assert disk["totals"] == {"Mimi": 1}
    assert disk["ever_delivered"] == {"Mimi": True}


@pytest.mark.asyncio
async def test_f0173_failed_reload_preserves_previous_root_and_hides_stale_totals(
    monkeypatch, tmp_path,
):
    root_a = tmp_path / "mem_a"
    root_b = tmp_path / "mem_b"
    _seed_totals(root_a, {"A": 9}, {"A": True})
    _seed_totals(root_b, {"B": 4}, {"B": True})

    assert await state._ensure_proactive_chat_totals_loaded(memory_dir=root_a) is True
    assert state._get_proactive_chat_total("A") == 9
    assert state._was_invite_ever_delivered("A") is True

    def _fail_b(path):
        if Path(path) == _totals_path(root_b):
            raise OSError("b locked")
        return _real_read_json(path)

    monkeypatch.setattr(state, "read_json", _fail_b)

    assert await state._ensure_proactive_chat_totals_loaded(memory_dir=root_b) is False
    # Reads fail open/empty rather than exposing root_a's totals under root_b
    assert state._get_proactive_chat_total("A") == 0
    assert state._was_invite_ever_delivered("A") is False
    # Switching back to root_a restores authoritative state and preserves disk
    assert await state._ensure_proactive_chat_totals_loaded(memory_dir=root_a) is True
    assert state._get_proactive_chat_total("A") == 9
    assert state._was_invite_ever_delivered("A") is True


@pytest.mark.asyncio
async def test_f0173_cancelled_load_and_root_swap_between_load_and_write(monkeypatch, tmp_path):
    root_a = tmp_path / "mem_a"
    root_b = tmp_path / "mem_b"
    _seed_totals(root_a, {"A": 2}, {})
    _seed_totals(root_b, {"B": 7}, {})

    assert await state._ensure_proactive_chat_totals_loaded(memory_dir=root_a) is True

    started = asyncio.Event()
    release = asyncio.Event()
    orig_to_thread = state.asyncio.to_thread

    async def _slow_to_thread(func, *args, **kwargs):
        if func is state.read_json and args and Path(args[0]) == _totals_path(root_b):
            started.set()
            await release.wait()
        return await orig_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(state.asyncio, "to_thread", _slow_to_thread)

    load_b = asyncio.create_task(state._ensure_proactive_chat_totals_loaded(memory_dir=root_b))
    await started.wait()
    load_b.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await load_b

    assert state._proactive_chat_totals_loaded_path == _totals_path(root_a)
    assert state._proactive_chat_totals == {"A": 2}

    # Now verify root swap between load and write aborts write instead of clobbering root_a
    monkeypatch.setattr(state.asyncio, "to_thread", orig_to_thread)
    orig_ensure = state._ensure_proactive_chat_totals_loaded

    async def _swap_after_load(*, memory_dir=None):
        ok = await orig_ensure(memory_dir=memory_dir)
        if Path(memory_dir) == root_a:
            await orig_ensure(memory_dir=root_b)
        return ok

    monkeypatch.setattr(state, "_ensure_proactive_chat_totals_loaded", _swap_after_load)
    await state._increment_proactive_chat_total("C", memory_dir=root_a)
    assert json.loads(_totals_path(root_a).read_text(encoding="utf-8"))["totals"] == {"A": 2}


@pytest.mark.asyncio
async def test_f0173_one_corrupt_total_entry_does_not_drop_other_characters(tmp_path):
    root = tmp_path / "memory"
    root.mkdir(parents=True, exist_ok=True)
    _totals_path(root).write_text(
        '{"version": 2, "totals": {"A": 5, "bad_nan": NaN, "bad_bool": true, "bad_huge": '
        + str(10**400)
        + '}, "ever_delivered": {"A": true}}',
        encoding="utf-8",
    )

    assert await state._ensure_proactive_chat_totals_loaded(memory_dir=root) is True
    assert state._get_proactive_chat_total("A") == 5
    assert math.isfinite(state._get_proactive_chat_total("bad_nan"))
    assert "bad_nan" not in state._proactive_chat_totals
    assert "bad_bool" not in state._proactive_chat_totals
    assert "bad_huge" not in state._proactive_chat_totals
