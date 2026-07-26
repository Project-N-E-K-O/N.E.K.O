"""Persistence-root compatibility tests for proactive-chat state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.proactive_chat import state
from main_routers import system_router
from main_routers.system_router import proactive_history, proactive_sources


@pytest.fixture(autouse=True)
def _restore_state_globals():
    source_history = dict(state._source_history)
    source_loaded = state._source_history_loaded
    source_loaded_path = state._source_history_loaded_path
    totals = dict(state._proactive_chat_totals)
    ever_delivered = dict(state._invite_ever_delivered)
    totals_loaded = state._proactive_chat_totals_loaded
    totals_loaded_path = state._proactive_chat_totals_loaded_path
    yield
    state._source_history.clear()
    state._source_history.update(source_history)
    state._source_history_loaded = source_loaded
    state._source_history_loaded_path = source_loaded_path
    state._proactive_chat_totals.clear()
    state._proactive_chat_totals.update(totals)
    state._invite_ever_delivered.clear()
    state._invite_ever_delivered.update(ever_delivered)
    state._proactive_chat_totals_loaded = totals_loaded
    state._proactive_chat_totals_loaded_path = totals_loaded_path


def test_explicit_memory_dir_overrides_singleton_fallback(monkeypatch, tmp_path) -> None:
    singleton_root = tmp_path / "singleton"
    injected_root = tmp_path / "injected"
    monkeypatch.setattr(
        state,
        "get_config_manager",
        lambda: SimpleNamespace(memory_dir=singleton_root),
    )

    assert state._source_history_path() == (
        singleton_root / state._SOURCE_HISTORY_FILENAME
    )
    assert state._proactive_chat_totals_path() == (
        singleton_root / state._PROACTIVE_CHAT_TOTALS_FILENAME
    )
    assert state._source_history_path(memory_dir=injected_root) == (
        injected_root / state._SOURCE_HISTORY_FILENAME
    )
    assert state._proactive_chat_totals_path(memory_dir=injected_root) == (
        injected_root / state._PROACTIVE_CHAT_TOTALS_FILENAME
    )


@pytest.mark.asyncio
async def test_source_history_reads_and_writes_only_in_explicit_root(
    monkeypatch,
    tmp_path,
) -> None:
    injected_root = tmp_path / "injected"
    expected_path = injected_root / state._SOURCE_HISTORY_FILENAME
    reads = []

    def read_json(path):
        reads.append(path)
        return {"v": state._SOURCE_HISTORY_SCHEMA_VERSION, "entries": {}}

    writer = AsyncMock()
    monkeypatch.setattr(state, "read_json", read_json)
    monkeypatch.setattr(state, "atomic_write_json_async", writer)
    monkeypatch.setattr(
        state,
        "get_config_manager",
        lambda: pytest.fail("singleton manager must not be read"),
    )
    state._source_history.clear()
    state._source_history_loaded = False

    await state._ensure_source_history_loaded(memory_dir=injected_root)
    await state._record_source_used(
        url="https://example.test/topic",
        kind="web",
        title="topic",
        memory_dir=injected_root,
    )

    assert reads == [expected_path]
    assert writer.await_count == 1
    assert writer.await_args.args[0] == expected_path
    assert writer.await_args.args[1]["v"] == state._SOURCE_HISTORY_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_totals_schema_and_atomic_invite_update_use_explicit_root(
    monkeypatch,
    tmp_path,
) -> None:
    injected_root = tmp_path / "injected"
    expected_path = injected_root / state._PROACTIVE_CHAT_TOTALS_FILENAME
    writer = AsyncMock()
    monkeypatch.setattr(state, "read_json", lambda path: {})
    monkeypatch.setattr(state, "atomic_write_json_async", writer)
    monkeypatch.setattr(
        state,
        "get_config_manager",
        lambda: pytest.fail("singleton manager must not be read"),
    )
    state._proactive_chat_totals.clear()
    state._invite_ever_delivered.clear()
    state._proactive_chat_totals_loaded = False

    assert await state._increment_proactive_chat_total(
        "Yui", memory_dir=injected_root
    ) == 1
    assert await state._record_invite_delivery_persistent(
        "Yui", memory_dir=injected_root
    ) == 2

    assert writer.await_count == 2
    for write in writer.await_args_list:
        assert write.args[0] == expected_path
        assert write.args[1]["version"] == state._PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION
    final_snapshot = writer.await_args_list[-1].args[1]
    assert final_snapshot["totals"] == {"Yui": 2}
    assert final_snapshot["ever_delivered"] == {"Yui": True}


@pytest.mark.asyncio
async def test_legacy_facades_resolve_router_shared_state_manager(
    monkeypatch,
    tmp_path,
) -> None:
    legacy_root = tmp_path / "legacy-injected"
    manager = SimpleNamespace(memory_dir=legacy_root)
    monkeypatch.setattr(
        proactive_sources,
        "_get_legacy_config_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        proactive_history,
        "_get_legacy_config_manager",
        lambda: manager,
    )
    ensure_sources = AsyncMock()
    increment_total = AsyncMock(return_value=1)
    monkeypatch.setattr(state, "_ensure_source_history_loaded", ensure_sources)
    monkeypatch.setattr(state, "_increment_proactive_chat_total", increment_total)

    assert proactive_sources._source_history_path() == (
        legacy_root / state._SOURCE_HISTORY_FILENAME
    )
    assert proactive_history._proactive_chat_totals_path() == (
        legacy_root / state._PROACTIVE_CHAT_TOTALS_FILENAME
    )
    await proactive_sources._ensure_source_history_loaded()
    assert await proactive_history._increment_proactive_chat_total("Yui") == 1

    ensure_sources.assert_awaited_once_with(memory_dir=legacy_root)
    increment_total.assert_awaited_once_with("Yui", memory_dir=legacy_root)


def test_legacy_facades_share_canonical_mutable_state() -> None:
    assert proactive_sources._source_history is state._source_history
    assert proactive_sources._source_history_lock is state._source_history_lock
    assert proactive_history._proactive_chat_totals is state._proactive_chat_totals
    assert proactive_history._invite_ever_delivered is state._invite_ever_delivered
    assert (
        proactive_history._proactive_chat_totals_lock
        is state._proactive_chat_totals_lock
    )


def test_legacy_facades_read_live_loaded_flags() -> None:
    state._source_history_loaded = False
    state._proactive_chat_totals_loaded = False
    assert proactive_sources._source_history_loaded is False
    assert proactive_history._proactive_chat_totals_loaded is False
    assert system_router._source_history_loaded is False
    assert system_router._proactive_chat_totals_loaded is False

    state._source_history_loaded = True
    state._proactive_chat_totals_loaded = True
    assert proactive_sources._source_history_loaded is True
    assert proactive_history._proactive_chat_totals_loaded is True
    assert system_router._source_history_loaded is True
    assert system_router._proactive_chat_totals_loaded is True


@pytest.mark.asyncio
async def test_source_history_reloads_when_memory_root_changes(
    monkeypatch,
    tmp_path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    def read_json(path):
        return {
            "entries": {
                path.parent.name: {
                    "ts": state.time.time(),
                    "kind": "web",
                }
            }
        }

    monkeypatch.setattr(state, "read_json", read_json)
    state._source_history_loaded = False
    state._source_history_loaded_path = None

    await state._ensure_source_history_loaded(memory_dir=root_a)
    assert set(state._source_history) == {"a"}
    await state._ensure_source_history_loaded(memory_dir=root_b)
    assert set(state._source_history) == {"b"}


@pytest.mark.asyncio
async def test_totals_reload_when_memory_root_changes(
    monkeypatch,
    tmp_path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    def read_json(path):
        if path.parent == root_a:
            total = 1
        elif path.parent == root_b:
            total = 2
        else:
            raise AssertionError(f"unexpected totals path: {path}")
        return {
            "totals": {"Yui": total},
            "ever_delivered": {path.parent.name: True},
        }

    monkeypatch.setattr(state, "read_json", read_json)
    state._proactive_chat_totals_loaded = False
    state._proactive_chat_totals_loaded_path = None

    await state._ensure_proactive_chat_totals_loaded(memory_dir=root_a)
    assert state._proactive_chat_totals == {"Yui": 1}
    assert state._invite_ever_delivered == {"a": True}
    await state._ensure_proactive_chat_totals_loaded(memory_dir=root_b)
    assert state._proactive_chat_totals == {"Yui": 2}
    assert state._invite_ever_delivered == {"b": True}
