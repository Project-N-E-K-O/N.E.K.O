from __future__ import annotations

import gc
import importlib.util
import sys
import threading
import tracemalloc
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


_START = "0001-01-01 00:00:00.000000"
_END = "9999-12-31 23:59:59.999999"
_TABLE = "time_indexed_original"


class _NullLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


@pytest.fixture(scope="module")
def timeindex_module():
    """Load timeindex in isolation so this focused test has no app bootstrap."""
    stubs: dict[str, types.ModuleType] = {}

    utils = types.ModuleType("utils")
    utils.__path__ = []  # type: ignore[attr-defined]
    stubs["utils"] = utils

    llm_client = types.ModuleType("utils.llm_client")

    class _History:
        _engine_cache: dict = {}

    llm_client.SQLChatMessageHistory = _History
    llm_client.SystemMessage = object
    stubs["utils.llm_client"] = llm_client

    cloudsave = types.ModuleType("utils.cloudsave_runtime")

    class _MaintenanceModeError(RuntimeError):
        pass

    cloudsave.MaintenanceModeError = _MaintenanceModeError
    cloudsave.assert_cloudsave_writable = lambda *_args, **_kwargs: None
    stubs["utils.cloudsave_runtime"] = cloudsave

    config_manager = types.ModuleType("utils.config_manager")
    config_manager.get_config_manager = lambda *_args, **_kwargs: None
    stubs["utils.config_manager"] = config_manager

    logger_config = types.ModuleType("utils.logger_config")
    logger_config.get_module_logger = lambda *_args, **_kwargs: _NullLogger()
    stubs["utils.logger_config"] = logger_config

    config = types.ModuleType("config")
    config.TIME_ORIGINAL_TABLE_NAME = _TABLE
    config.TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"
    stubs["config"] = config

    memory = types.ModuleType("memory")
    memory.__path__ = []  # type: ignore[attr-defined]
    memory.ensure_character_dir = lambda *_args, **_kwargs: ""
    stubs["memory"] = memory

    stop_names = types.ModuleType("memory.stop_names")
    stop_names.collect_stop_names = lambda *_args, **_kwargs: set()
    stop_names.strip_stop_names = lambda content, _names: content
    stubs["memory.stop_names"] = stop_names

    previous = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    module_name = "_timeindex_batched_read_under_test"
    module_path = Path(__file__).parents[2] / "memory" / "timeindex.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


def _create_manager(timeindex_module, tmp_path, rows):
    engine = create_engine(f"sqlite:///{tmp_path / 'time-index.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE {_TABLE} ("
                "session_id TEXT, message TEXT, timestamp DATETIME)"
            )
        )
        conn.execute(
            text(f"CREATE INDEX idx_{_TABLE}_timestamp ON {_TABLE}(timestamp)")
        )
        if rows:
            conn.execute(
                text(
                    f"INSERT INTO {_TABLE}(session_id, message, timestamp) "
                    "VALUES (:session_id, :message, :timestamp)"
                ),
                rows,
            )

    manager = timeindex_module.TimeIndexedMemory.__new__(
        timeindex_module.TimeIndexedMemory
    )
    manager.engines = {"cat": engine}
    manager.db_paths = {}
    manager._engine_readonly_flags = {}
    manager._writable_bootstrapped = set()
    manager.recent_history_manager = None
    manager._ensure_engine_exists = lambda _name, db_path=None, readonly=False: True
    return manager, engine


def _flatten(batches):
    return [row for batch in batches for row in batch]


def test_batches_preserve_order_limit_and_legacy_list_api(timeindex_module, tmp_path):
    rows = [
        {
            "session_id": "late",
            "message": "4",
            "timestamp": "2026-01-02 00:00:00.000000",
        },
        {
            "session_id": "same-a",
            "message": "1",
            "timestamp": "2026-01-01 00:00:00.000000",
        },
        {
            "session_id": "same-b",
            "message": "2",
            "timestamp": "2026-01-01 00:00:00.000000",
        },
        {
            "session_id": "latest",
            "message": "5",
            "timestamp": "2026-01-03 00:00:00.000000",
        },
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        legacy_rows = manager.retrieve_original_by_timeframe(
            "cat", _START, _END, limit_rows=3
        )
        assert isinstance(legacy_rows, list)
        assert [row[1] for row in legacy_rows] == ["same-a", "same-b", "late"]

        batches = list(
            manager.iter_original_by_timeframe_batches(
                "cat", _START, _END, batch_size=1, limit_rows=3
            )
        )
        assert [len(batch) for batch in batches] == [1, 1, 1]
        assert [row[1] for row in _flatten(batches)] == [
            "same-a",
            "same-b",
            "late",
        ]
    finally:
        engine.dispose()


def test_batch_size_must_be_positive(timeindex_module):
    manager = timeindex_module.TimeIndexedMemory.__new__(
        timeindex_module.TimeIndexedMemory
    )
    with pytest.raises(ValueError, match="batch_size"):
        list(
            manager.iter_original_by_timeframe_batches(
                "cat", _START, _END, batch_size=0
            )
        )


class _FakeResult:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.fetch_sizes: list[int] = []

    def fetchmany(self, size):
        self.fetch_sizes.append(size)
        if self.error is not None:
            raise self.error
        return self.rows


class _FakeConnection:
    def __init__(self, result, tracker):
        self.result = result
        self.tracker = tracker

    def __enter__(self):
        self.tracker["active"] += 1
        self.tracker["threads"].append(threading.get_ident())
        return self

    def __exit__(self, *_args):
        self.tracker["active"] -= 1
        self.tracker["exits"] += 1

    def execute(self, *_args, **_kwargs):
        return self.result


class _FakeEngine:
    def __init__(self, results, tracker):
        self.results = iter(results)
        self.tracker = tracker

    def connect(self):
        return _FakeConnection(next(self.results), self.tracker)


def _fake_manager(timeindex_module, results, tracker):
    manager = timeindex_module.TimeIndexedMemory.__new__(
        timeindex_module.TimeIndexedMemory
    )
    manager.engines = {"cat": _FakeEngine(results, tracker)}
    manager._ensure_engine_exists = lambda _name, db_path=None, readonly=False: True
    return manager


def test_connection_is_closed_before_yield_and_fetchmany_is_bounded(timeindex_module):
    tracker = {"active": 0, "exits": 0, "threads": []}
    first_result = _FakeResult(
        [
            ("2026-01-01 00:00:00.000000", 1, "session", "message"),
        ]
    )
    manager = _fake_manager(timeindex_module, [first_result], tracker)

    iterator = manager.iter_original_by_timeframe_batches(
        "cat", _START, _END, batch_size=7, limit_rows=1
    )
    assert next(iterator) == [("2026-01-01 00:00:00.000000", "session", "message")]
    assert first_result.fetch_sizes == [1]
    assert tracker["active"] == 0
    assert tracker["exits"] == 1


def test_fetch_exception_is_soft_and_still_closes_connection(timeindex_module):
    tracker = {"active": 0, "exits": 0, "threads": []}
    manager = _fake_manager(
        timeindex_module,
        [_FakeResult(error=RuntimeError("read failed"))],
        tracker,
    )

    assert (
        list(
            manager.iter_original_by_timeframe_batches(
                "cat", _START, _END, batch_size=8
            )
        )
        == []
    )
    assert tracker["active"] == 0
    assert tracker["exits"] == 1


@pytest.mark.asyncio
async def test_async_batches_finish_worker_connection_before_crossing_thread(
    timeindex_module,
):
    tracker = {"active": 0, "exits": 0, "threads": []}
    manager = _fake_manager(
        timeindex_module,
        [
            _FakeResult(
                [
                    ("2026-01-01 00:00:00.000000", 1, "session", "message"),
                ]
            )
        ],
        tracker,
    )
    event_loop_thread = threading.get_ident()

    iterator = manager.aiter_original_by_timeframe_batches(
        "cat", _START, _END, batch_size=1, limit_rows=1
    )
    batch = await anext(iterator)
    assert batch == [("2026-01-01 00:00:00.000000", "session", "message")]
    assert tracker["threads"] and tracker["threads"][0] != event_loop_thread
    assert tracker["active"] == 0
    assert tracker["exits"] == 1
    await iterator.aclose()


def test_wide_corpus_reader_consumes_batches_and_still_cleans_up(
    timeindex_module,
    tmp_path,
    monkeypatch,
):
    logger_module = types.ModuleType("tests.testbench.logger")
    logger_module.python_logger = lambda: _NullLogger()
    monkeypatch.setitem(sys.modules, "tests.testbench.logger", logger_module)

    calls: dict = {"cleanup": 0}

    class _FakeTimeIndexedMemory:
        def __init__(self, _recent_history_manager):
            pass

        def iter_original_by_timeframe_batches(
            self,
            character,
            start_time,
            end_time,
            *,
            batch_size,
            limit_rows,
        ):
            calls["args"] = (
                character,
                start_time,
                end_time,
                batch_size,
                limit_rows,
            )
            yield [
                ("2026-01-01", "s0", '{"type":"human","data":{"content":"hello"}}'),
                ("2026-01-02", "s1", '{"type":"ai","data":{"content":""}}'),
            ]
            yield [
                ("2026-01-03", "s2", '{"type":"ai","data":{"content":"world"}}'),
            ]

        def cleanup(self):
            calls["cleanup"] += 1

    fake_timeindex = types.ModuleType("memory.timeindex")
    fake_timeindex.TimeIndexedMemory = _FakeTimeIndexedMemory
    monkeypatch.setitem(sys.modules, "memory.timeindex", fake_timeindex)

    module_name = "_conversation_corpus_batched_read_under_test"
    module_path = (
        Path(__file__).parents[1] / "testbench" / "pipeline" / "conversation_corpus.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    character_dir = tmp_path / "cat"
    character_dir.mkdir()
    (character_dir / "time_indexed.db").touch()
    monkeypatch.setattr(module, "_character_memory_dir", lambda _name: character_dir)

    turns, warnings, present = module.load_time_indexed_turns("cat", limit_rows=3)

    assert present is True
    assert warnings == []
    assert [turn["content"] for turn in turns] == ["hello", "world"]
    assert turns[1]["id"] == module._db_turn_id("s2", 2)
    assert calls["args"][0] == "cat"
    assert calls["args"][3:] == (module._TIME_INDEX_BATCH_SIZE, 3)
    assert calls["cleanup"] == 1


def test_batched_read_reduces_python_peak_memory(timeindex_module, tmp_path):
    payload = "x" * 4096
    row_count = 2500
    rows = [
        {
            "session_id": f"session-{idx}",
            "message": payload,
            "timestamp": f"2026-01-01 00:{idx // 60:02d}:{idx % 60:02d}.{idx:06d}",
        }
        for idx in range(row_count)
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        gc.collect()
        tracemalloc.start()
        legacy_rows = manager.retrieve_original_by_timeframe("cat", _START, _END)
        _, legacy_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert len(legacy_rows) == row_count
        del legacy_rows

        gc.collect()
        tracemalloc.start()
        streamed_count = 0
        for batch in manager.iter_original_by_timeframe_batches(
            "cat", _START, _END, batch_size=64
        ):
            streamed_count += len(batch)
        _, batched_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert streamed_count == row_count
        assert batched_peak < legacy_peak * 0.5, (
            f"expected batched peak < 50% of list peak, got "
            f"{batched_peak=} {legacy_peak=}"
        )
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        engine.dispose()
