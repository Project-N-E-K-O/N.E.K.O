from __future__ import annotations

import gc
import importlib.util
import json
import sys
import threading
import tracemalloc
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text


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


def _create_manager(
    timeindex_module,
    tmp_path,
    rows,
    *,
    indexed=True,
    include_timestamp=True,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'time-index.db'}")
    with engine.begin() as conn:
        timestamp_column = ", timestamp DATETIME" if include_timestamp else ""
        conn.execute(
            text(
                f"CREATE TABLE {_TABLE} ("
                f"session_id TEXT, message TEXT{timestamp_column})"
            )
        )
        if indexed and include_timestamp:
            conn.execute(
                text(f"CREATE INDEX idx_{_TABLE}_timestamp ON {_TABLE}(timestamp)")
            )
        if rows:
            columns = "session_id, message, timestamp" if include_timestamp else "session_id, message"
            values = ":session_id, :message, :timestamp" if include_timestamp else ":session_id, :message"
            conn.execute(text(f"INSERT INTO {_TABLE}({columns}) VALUES ({values})"), rows)

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


def _stored_message(role, content):
    return json.dumps(
        {"type": role, "data": {"content": content}},
        ensure_ascii=False,
    )


def test_latest_assistant_texts_are_bounded_filtered_and_chronological(
    timeindex_module,
    tmp_path,
):
    timestamp = "2026-01-01 00:00:00.000000"
    rows = [
        {
            "session_id": "1",
            "message": _stored_message("human", "user secret"),
            "timestamp": timestamp,
        },
        {
            "session_id": "2",
            "message": _stored_message("ai", "oldest answer"),
            "timestamp": timestamp,
        },
        {"session_id": "3", "message": "not-json", "timestamp": timestamp},
        {
            "session_id": "4",
            "message": _stored_message(
                "ai",
                [
                    {"type": "image", "url": "private"},
                    {"type": "text", "text": "middle answer"},
                ],
            ),
            "timestamp": timestamp,
        },
        {
            "session_id": "5",
            "message": _stored_message("system", "system secret"),
            "timestamp": timestamp,
        },
        {
            "session_id": "6",
            "message": _stored_message("ai", "latest answer"),
            "timestamp": timestamp,
        },
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 2, batch_size=2)
    finally:
        engine.dispose()

    assert result.source_available is True
    assert result.messages == ["middle answer", "latest answer"]
    assert result.skipped_row_count == 1


def test_latest_assistant_texts_include_null_timestamps_across_pages(
    timeindex_module,
    tmp_path,
):
    rows = [
        {
            "session_id": "new",
            "message": _stored_message("ai", "newest answer"),
            "timestamp": "2026-01-02 00:00:00.000000",
        },
        {
            "session_id": "old",
            "message": _stored_message("ai", "older answer"),
            "timestamp": "2026-01-01 00:00:00.000000",
        },
        {
            "session_id": "legacy-a",
            "message": _stored_message("ai", "legacy answer a"),
            "timestamp": None,
        },
        {
            "session_id": "legacy-b",
            "message": _stored_message("ai", "legacy answer b"),
            "timestamp": None,
        },
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 4, batch_size=1)
    finally:
        engine.dispose()

    assert result.source_available is True
    assert result.messages == [
        "legacy answer a",
        "legacy answer b",
        "older answer",
        "newest answer",
    ]


def test_latest_assistant_texts_support_legacy_schema_without_timestamp(
    timeindex_module,
    tmp_path,
):
    rows = [
        {"session_id": "1", "message": _stored_message("ai", "old")},
        {"session_id": "2", "message": _stored_message("human", "skip")},
        {"session_id": "3", "message": _stored_message("ai", "new")},
    ]
    manager, engine = _create_manager(
        timeindex_module,
        tmp_path,
        rows,
        indexed=False,
        include_timestamp=False,
    )
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 2, batch_size=1)
    finally:
        engine.dispose()

    assert result.messages == ["old", "new"]
    assert result.source_available is True


def test_latest_assistant_texts_exclude_history_only_action_note(
    timeindex_module,
    tmp_path,
):
    visible = "给你放首歌～"
    stored = json.dumps(
        {
            "type": "ai",
            "data": {
                "content": f"{visible}\n[给小明放了《稻香》— 周杰伦]",
                "additional_kwargs": {
                    "anti_repeat_visible_text_length": str(len(visible))
                },
            },
        },
        ensure_ascii=False,
    )
    rows = [{"session_id": "1", "message": stored, "timestamp": None}]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 1)
    finally:
        engine.dispose()

    assert result.messages == [visible]


def test_latest_assistant_texts_exclude_legacy_history_only_action_notes(
    timeindex_module,
    tmp_path,
):
    rows = [
        {
            "session_id": "1",
            "message": _stored_message(
                "ai", 'Visible reply\n[Played for Alice: "Song" by Artist]'
            ),
            "timestamp": "2026-01-01 00:00:00.000000",
        },
        {
            "session_id": "2",
            "message": _stored_message(
                "ai", "另一条回复\n[给小明分享了《文章》（来自 网站）]"
            ),
            "timestamp": "2026-01-02 00:00:00.000000",
        },
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 2)
    finally:
        engine.dispose()

    assert result.messages == ["Visible reply", "另一条回复"]


@pytest.mark.parametrize(
    ("content", "expected_messages"),
    [
        ('[Played for Alice: "Song" by Artist]', []),
        (
            'Visible reply\n[Played for Alice: "Song" by Artist]\n',
            ["Visible reply"],
        ),
    ],
)
def test_latest_assistant_texts_exclude_legacy_action_note_boundaries(
    timeindex_module,
    tmp_path,
    content,
    expected_messages,
):
    rows = [
        {
            "session_id": "1",
            "message": _stored_message("ai", content),
            "timestamp": None,
        }
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 1)
    finally:
        engine.dispose()

    assert result.messages == expected_messages


def test_latest_assistant_texts_preserve_non_template_bracketed_tail(
    timeindex_module,
    tmp_path,
):
    content = 'Visible reply\n[Played for effect, not metadata]'
    rows = [
        {
            "session_id": "1",
            "message": _stored_message("ai", content),
            "timestamp": None,
        }
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 1)
    finally:
        engine.dispose()

    assert result.messages == [content]


def test_latest_assistant_texts_missing_source_does_not_create_engine(
    timeindex_module,
):
    manager = timeindex_module.TimeIndexedMemory.__new__(
        timeindex_module.TimeIndexedMemory
    )
    manager.engines = {}
    manager._ensure_engine_exists = lambda _name, readonly=False: False

    result = manager.retrieve_latest_assistant_texts("missing", 100)

    assert result == timeindex_module.LatestAssistantTexts([], False, 0)


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
        assert manager._has_indexed_timeframe_order("cat", _START, _END) is True
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


def test_unindexed_readonly_database_uses_one_streaming_query_without_writes(
    timeindex_module,
    tmp_path,
):
    rows = [
        {
            "session_id": "late",
            "message": "3",
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
            "session_id": "excluded",
            "message": "4",
            "timestamp": "2026-01-03 00:00:00.000000",
        },
    ]
    manager, engine = _create_manager(
        timeindex_module,
        tmp_path,
        rows,
        indexed=False,
    )
    tracker = {"active": 0, "checkouts": 0, "checkins": 0}
    read_queries: list[tuple[str, int]] = []

    def on_checkout(*_args):
        tracker["active"] += 1
        tracker["checkouts"] += 1

    def on_checkin(*_args):
        tracker["active"] -= 1
        tracker["checkins"] += 1

    def on_execute(_conn, _cursor, statement, *_args):
        if statement.startswith("SELECT timestamp, session_id, message"):
            read_queries.append((statement, threading.get_ident()))

    event.listen(engine, "checkout", on_checkout)
    event.listen(engine, "checkin", on_checkin)
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        assert manager._has_indexed_timeframe_order("cat", _START, _END) is False
        batches = list(
            manager.iter_original_by_timeframe_batches(
                "cat",
                _START,
                _END,
                batch_size=1,
                limit_rows=3,
            )
        )
        assert [row[1] for row in _flatten(batches)] == [
            "same-a",
            "same-b",
            "late",
        ]
        assert len(read_queries) == 1
        assert "LIMIT" in read_queries[0][0]
        assert tracker["active"] == 0
        assert tracker["checkouts"] == tracker["checkins"]

        with engine.connect() as conn:
            indexes = list(conn.execute(text(f"PRAGMA index_list({_TABLE})")))
        assert indexes == []
    finally:
        engine.dispose()


def test_unindexed_stream_closes_worker_connection_when_consumer_stops_early(
    timeindex_module,
    tmp_path,
):
    rows = [
        {
            "session_id": f"session-{idx}",
            "message": str(idx),
            "timestamp": f"2026-01-01 00:00:{idx:02d}.000000",
        }
        for idx in range(20)
    ]
    manager, engine = _create_manager(
        timeindex_module,
        tmp_path,
        rows,
        indexed=False,
    )
    tracker = {"active": 0, "checkouts": 0, "checkins": 0}

    def on_checkout(*_args):
        tracker["active"] += 1
        tracker["checkouts"] += 1

    def on_checkin(*_args):
        tracker["active"] -= 1
        tracker["checkins"] += 1

    event.listen(engine, "checkout", on_checkout)
    event.listen(engine, "checkin", on_checkin)
    try:
        iterator = manager.iter_original_by_timeframe_batches(
            "cat", _START, _END, batch_size=1
        )
        assert len(next(iterator)) == 1
        iterator.close()

        assert tracker["active"] == 0
        assert tracker["checkouts"] == tracker["checkins"]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_unindexed_stream_keeps_sqlite_in_worker_and_closes(
    timeindex_module,
    tmp_path,
):
    rows = [
        {
            "session_id": f"session-{idx}",
            "message": str(idx),
            "timestamp": f"2026-01-01 00:00:0{idx}.000000",
        }
        for idx in range(3)
    ]
    manager, engine = _create_manager(
        timeindex_module,
        tmp_path,
        rows,
        indexed=False,
    )
    tracker = {"active": 0, "checkouts": 0, "checkins": 0}
    query_threads: list[int] = []

    def on_checkout(*_args):
        tracker["active"] += 1
        tracker["checkouts"] += 1

    def on_checkin(*_args):
        tracker["active"] -= 1
        tracker["checkins"] += 1

    def on_execute(_conn, _cursor, statement, *_args):
        if statement.startswith("SELECT timestamp, session_id, message"):
            query_threads.append(threading.get_ident())

    event.listen(engine, "checkout", on_checkout)
    event.listen(engine, "checkin", on_checkin)
    event.listen(engine, "before_cursor_execute", on_execute)
    event_loop_thread = threading.get_ident()
    try:
        batches = [
            batch
            async for batch in manager.aiter_original_by_timeframe_batches(
                "cat", _START, _END, batch_size=1, limit_rows=2
            )
        ]

        assert [row[1] for row in _flatten(batches)] == ["session-0", "session-1"]
        assert len(query_threads) == 1
        assert query_threads[0] != event_loop_thread
        assert tracker["active"] == 0
        assert tracker["checkouts"] == tracker["checkins"]
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


def test_strict_fact_index_delete_propagates_database_failure(timeindex_module):
    class _FailingConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database locked")

    manager = timeindex_module.TimeIndexedMemory.__new__(
        timeindex_module.TimeIndexedMemory
    )
    manager.engines = {
        "cat": types.SimpleNamespace(connect=lambda: _FailingConnection())
    }
    manager._assert_timeindex_writable = lambda _name: None
    manager._ensure_engine_exists = lambda _name: True
    manager._ensure_fts_table = lambda _name: True

    # Ordinary maintenance remains best-effort.
    manager.delete_fact_from_index("cat", "fact-id")

    # Privacy erasure can now fail closed before deleting authoritative JSON.
    with pytest.raises(RuntimeError, match="Unable to delete fact fact-id"):
        manager.delete_fact_from_index("cat", "fact-id", strict=True)


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


class _FailingStreamResult:
    def __init__(self):
        self.calls = 0

    def fetchmany(self, _size):
        self.calls += 1
        if self.calls == 1:
            return [("2026-01-01 00:00:00.000000", "session", "message")]
        raise RuntimeError("stream failed")


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


def _fake_manager(timeindex_module, results, tracker, *, indexed=True):
    manager = timeindex_module.TimeIndexedMemory.__new__(
        timeindex_module.TimeIndexedMemory
    )
    manager.engines = {"cat": _FakeEngine(results, tracker)}
    manager._ensure_engine_exists = lambda _name, db_path=None, readonly=False: True
    manager._has_indexed_timeframe_order = lambda *_args, **_kwargs: indexed
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


def test_fetch_exception_propagates_and_still_closes_connection(timeindex_module):
    tracker = {"active": 0, "exits": 0, "threads": []}
    manager = _fake_manager(
        timeindex_module,
        [_FakeResult(error=RuntimeError("read failed"))],
        tracker,
    )

    with pytest.raises(RuntimeError, match="read failed"):
        list(
            manager.iter_original_by_timeframe_batches(
                "cat", _START, _END, batch_size=8
            )
        )
    assert tracker["active"] == 0
    assert tracker["exits"] == 1


def test_later_page_exception_marks_partial_iteration_failed(timeindex_module):
    tracker = {"active": 0, "exits": 0, "threads": []}
    manager = _fake_manager(
        timeindex_module,
        [
            _FakeResult([("2026-01-01 00:00:00.000000", 1, "session", "message")]),
            _FakeResult(error=RuntimeError("later page failed")),
        ],
        tracker,
    )

    iterator = manager.iter_original_by_timeframe_batches(
        "cat", _START, _END, batch_size=1
    )
    assert next(iterator) == [("2026-01-01 00:00:00.000000", "session", "message")]
    with pytest.raises(RuntimeError, match="later page failed"):
        next(iterator)
    assert tracker["active"] == 0
    assert tracker["exits"] == 2


def test_unindexed_stream_exception_propagates_and_closes_connection(timeindex_module):
    tracker = {"active": 0, "exits": 0, "threads": []}
    manager = _fake_manager(
        timeindex_module,
        [_FailingStreamResult()],
        tracker,
        indexed=False,
    )

    iterator = manager.iter_original_by_timeframe_batches(
        "cat", _START, _END, batch_size=1
    )
    assert next(iterator) == [
        ("2026-01-01 00:00:00.000000", "session", "message")
    ]
    with pytest.raises(RuntimeError, match="stream failed"):
        next(iterator)
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


@pytest.mark.asyncio
async def test_async_later_page_exception_propagates_after_closing_connection(
    timeindex_module,
):
    tracker = {"active": 0, "exits": 0, "threads": []}
    manager = _fake_manager(
        timeindex_module,
        [
            _FakeResult([("2026-01-01 00:00:00.000000", 1, "session", "message")]),
            _FakeResult(error=RuntimeError("async later page failed")),
        ],
        tracker,
    )

    iterator = manager.aiter_original_by_timeframe_batches(
        "cat", _START, _END, batch_size=1
    )
    assert await anext(iterator) == [
        ("2026-01-01 00:00:00.000000", "session", "message")
    ]
    with pytest.raises(RuntimeError, match="async later page failed"):
        await anext(iterator)
    assert tracker["active"] == 0
    assert tracker["exits"] == 2


def test_wide_corpus_reader_consumes_batches_and_still_cleans_up(
    timeindex_module,
    tmp_path,
    monkeypatch,
):
    logger_module = types.ModuleType("tests.testbench.logger")
    logger_module.python_logger = _NullLogger
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
            if calls.get("fail_after_first"):
                raise RuntimeError("later page failed")
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

    calls["fail_after_first"] = True
    turns, warnings, present = module.load_time_indexed_turns("cat", limit_rows=3)
    assert present is True
    assert turns == []
    assert len(warnings) == 1
    assert "later page failed" in warnings[0]
    assert calls["cleanup"] == 2


def test_schema_migration_adds_timestamp_indexes(timeindex_module, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    tables = [_TABLE, "time_indexed_compressed"]
    try:
        with engine.begin() as conn:
            for table in tables:
                conn.execute(
                    text(f"CREATE TABLE {table} (session_id TEXT, message TEXT)")
                )

        manager = timeindex_module.TimeIndexedMemory.__new__(
            timeindex_module.TimeIndexedMemory
        )
        manager._check_and_migrate_schema(engine, "cat")

        with engine.connect() as conn:
            for table in tables:
                columns = {
                    row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
                }
                indexes = {
                    row[1] for row in conn.execute(text(f"PRAGMA index_list({table})"))
                }
                assert "timestamp" in columns
                assert f"idx_{table}_timestamp" in indexes
    finally:
        engine.dispose()


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "readonly-legacy"])
def test_batched_read_reduces_python_peak_memory(
    timeindex_module,
    tmp_path,
    indexed,
):
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
    manager, engine = _create_manager(
        timeindex_module,
        tmp_path,
        rows,
        indexed=indexed,
    )
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


def test_block_list_assistant_rows_honor_the_visible_text_boundary():
    """Block-list content is the shape cross_server actually persists.

    ``main_logic/cross_server.py`` rebuilds every assistant turn as
    ``[{"type": "text", ...}]``, so a guard that only ran on the string branch
    was effectively never enforced on real rows.
    """
    from memory.timeindex import _assistant_record_from_stored_message

    note = '[给博士放了《晴天》— 周杰伦]'
    stored = json.dumps(
        {
            "type": "ai",
            "data": {
                "content": [
                    {"type": "text", "text": "今天也要好好休息哦\n" + note},
                ]
            },
        },
        ensure_ascii=False,
    )

    record = _assistant_record_from_stored_message(stored)

    assert record is not None
    assert record[0] == "今天也要好好休息哦"
    assert note not in record[0]


def test_block_list_assistant_rows_truncate_to_the_recorded_visible_length():
    from memory.timeindex import _assistant_record_from_stored_message

    visible = "今天也要好好休息哦"
    stored = json.dumps(
        {
            "type": "ai",
            "data": {
                "content": [{"type": "text", "text": visible + "\n[hidden note]"}],
                "additional_kwargs": {
                    "anti_repeat_response_id": "turn-1",
                    "anti_repeat_visible_text_length": str(len(visible)),
                },
            },
        },
        ensure_ascii=False,
    )

    record = _assistant_record_from_stored_message(stored)

    assert record == (visible, "turn-1")


def test_block_list_assistant_rows_reject_an_impossible_visible_length():
    from memory.timeindex import _assistant_record_from_stored_message

    stored = json.dumps(
        {
            "type": "ai",
            "data": {
                "content": [{"type": "text", "text": "short"}],
                "additional_kwargs": {"anti_repeat_visible_text_length": "9999"},
            },
        },
        ensure_ascii=False,
    )

    assert _assistant_record_from_stored_message(stored) is None


def test_latest_assistant_texts_keep_response_ids_positionally_aligned(
    timeindex_module,
    tmp_path,
):
    """`response_ids` must be one entry per message, `None` where absent.

    A compacted list loses the alignment the caller needs to tell WHICH analyzed
    replies are linkable. With a mix of legacy rows and newer ones, a compacted
    list looks like full coverage of a shorter window, and the panel would label
    a partial aggregate as handling for the whole requested range.
    """

    def _row(session_id, text, response_id, stamp):
        data = {"content": text}
        if response_id is not None:
            data["additional_kwargs"] = {"anti_repeat_response_id": response_id}
        return {
            "session_id": session_id,
            "message": json.dumps({"type": "ai", "data": data}, ensure_ascii=False),
            "timestamp": stamp,
        }

    rows = [
        _row("1", "oldest reply", None, "2026-01-01 00:00:00.000000"),
        _row("2", "middle reply", "turn-b", "2026-01-02 00:00:00.000000"),
        _row("3", "newest reply", None, "2026-01-03 00:00:00.000000"),
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 10)
    finally:
        engine.dispose()

    assert result.messages == ["oldest reply", "middle reply", "newest reply"]
    assert len(result.response_ids) == len(result.messages)
    assert result.response_ids == [None, "turn-b", None]


def test_damaged_visible_length_drops_only_its_own_row():
    """One damaged metadata field must not fail the whole insights request.

    A digit string longer than CPython's int-conversion limit passes isdigit()
    and then raises ValueError, which escaped the per-row parser. So does a
    superscript: "²".isdigit() is True while int("²") raises. A
    present-but-unusable value drops just that row -- falling back to the legacy
    stripper could read past the visible text and expose the hidden tail.

    This pins the CONTRACT (damaged field drops its own row, never escapes), not
    each individual guard. The `isdecimal()` predicate, the length bound and the
    `try/except` are deliberately redundant, so removing any ONE of them leaves
    this test green; removing the predicate and the catch together reddens it.
    """
    from memory.timeindex import _assistant_record_from_stored_message

    def _row(value):
        return json.dumps(
            {
                "type": "ai",
                "data": {
                    "content": "hello there",
                    "additional_kwargs": {"anti_repeat_visible_text_length": value},
                },
            }
        )

    assert _assistant_record_from_stored_message(_row("9" * 5000)) is None
    assert _assistant_record_from_stored_message(_row("abc")) is None
    assert _assistant_record_from_stored_message(_row(12)) is None
    # isdigit() is not an int() predicate: superscripts satisfy it and raise.
    assert _assistant_record_from_stored_message(_row("²")) is None
    assert _assistant_record_from_stored_message(_row("³²")) is None
    assert _assistant_record_from_stored_message(_row("5")) == ("hello", None)
    # An EXPLICIT null is unusable metadata, not a missing key: `.get()` cannot
    # tell them apart, and falling back to the legacy stripper on a corrupt
    # field risks reading past the visible text.
    assert _assistant_record_from_stored_message(_row(None)) is None
    # Non-ASCII DECIMAL digits are accepted by int() and stay supported.
    assert _assistant_record_from_stored_message(
        _row("٥")
    ) == ("hello", None)


def test_engine_initialization_is_serialized_per_character(timeindex_module):
    """Re-landed from c64d7ab31, which was reverted without a recorded reason.

    `_ensure_engine_exists_unlocked` is "read the cached engine -> dispose it ->
    rebuild", and this PR added a concurrent reader
    (`retrieve_latest_assistant_texts`, readonly, under `asyncio.to_thread`)
    racing the writer `/cache` drives. Without the per-character lock the
    writable branch can dispose the very engine the reader is querying.
    """
    manager = timeindex_module.TimeIndexedMemory(recent_history_manager=None)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()
    second_entered = threading.Event()
    call_lock = threading.Lock()
    call_count = 0
    errors: list[BaseException] = []

    def fake_ensure(_name, db_path=None, readonly=False):
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_entered.set()
            if not release_first.wait(2):
                raise TimeoutError("test did not release first initialization")
        else:
            second_entered.set()
        return True

    manager._ensure_engine_exists_unlocked = fake_ensure

    def run_first():
        try:
            manager._ensure_engine_exists("cat", readonly=True)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    def run_second():
        second_attempted.set()
        try:
            manager._ensure_engine_exists("cat", readonly=False)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    first = threading.Thread(target=run_first)
    second = threading.Thread(target=run_second)
    first.start()
    assert first_entered.wait(1)
    second.start()
    assert second_attempted.wait(1)
    # The second caller must still be blocked on the lock, not inside the body.
    assert not second_entered.wait(0.1)
    release_first.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert call_count == 2


def test_engine_disposal_shares_the_initialization_lock(timeindex_module):
    """Disposal is the other half of the race, and the lock is re-entrant."""
    manager = timeindex_module.TimeIndexedMemory(recent_history_manager=None)

    lock = manager._get_engine_lock("cat")
    assert manager._get_engine_lock("cat") is lock
    assert manager._get_engine_lock("other") is not lock

    # Re-entrant: the in-place repair branches dispose while already holding it.
    with lock:
        manager.dispose_engine("cat")

    entered = threading.Event()
    release = threading.Event()
    disposed = threading.Event()

    def hold_initialization(_name, db_path=None, readonly=False):
        entered.set()
        release.wait(2)
        return True

    manager._ensure_engine_exists_unlocked = hold_initialization

    initializer = threading.Thread(
        target=lambda: manager._ensure_engine_exists("cat", readonly=True)
    )
    disposer = threading.Thread(
        target=lambda: (manager.dispose_engine("cat"), disposed.set())
    )
    initializer.start()
    assert entered.wait(1)
    disposer.start()
    assert not disposed.wait(0.1), "disposal ran while initialization held the lock"
    release.set()
    initializer.join(2)
    disposer.join(2)

    assert disposed.is_set()
    assert not initializer.is_alive()
    assert not disposer.is_alive()


def test_disposal_waits_for_an_in_flight_latest_assistant_read(
    timeindex_module,
    tmp_path,
):
    """A read in flight must keep its engine until it finishes.

    The lock used to be released as soon as the engine was acquired, so
    `dispose_engine` could take it and tear down the very engine the schema
    probe and paging queries were still using — surfacing to the caller as
    RuntimeError("latest assistant history read failed").
    """
    rows = [
        {
            "session_id": str(index),
            "message": _stored_message("ai", f"reply {index}"),
            "timestamp": f"2026-01-0{index} 00:00:00.000000",
        }
        for index in range(1, 4)
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    # The shared fixture builds the manager through __new__ and assigns only
    # what the read path needs; disposal also consults this ledger.
    manager._undisposed_pools = {}

    read_started = threading.Event()
    allow_read_to_finish = threading.Event()
    disposal_returned = threading.Event()
    results: list[object] = []
    failures: list[BaseException] = []

    original_validate = manager._validate_table_name

    def blocking_validate(table_name):
        # Called after the engine is acquired and before the schema probe, i.e.
        # exactly the window the lock has to keep covered.
        read_started.set()
        allow_read_to_finish.wait(2)
        return original_validate(table_name)

    manager._validate_table_name = blocking_validate

    def run_read():
        try:
            results.append(manager.retrieve_latest_assistant_texts("cat", 10))
        except BaseException as exc:  # noqa: BLE001 - surfaced via `failures`
            failures.append(exc)

    def run_dispose():
        try:
            manager.dispose_engine("cat")
        except BaseException as exc:  # noqa: BLE001 - surfaced via `failures`
            failures.append(exc)
        disposal_returned.set()

    reader = threading.Thread(target=run_read)
    disposer = threading.Thread(target=run_dispose)
    try:
        reader.start()
        assert read_started.wait(2)
        disposer.start()
        assert not disposal_returned.wait(0.2), "disposal ran during an in-flight read"
        allow_read_to_finish.set()
        reader.join(5)
        disposer.join(5)
    finally:
        allow_read_to_finish.set()
        engine.dispose()

    assert not reader.is_alive()
    assert not disposer.is_alive()
    assert failures == []
    assert disposal_returned.is_set()
    assert results and results[0].messages == ["reply 1", "reply 2", "reply 3"]


def test_latest_assistant_texts_stop_at_the_scan_budget(timeindex_module, tmp_path):
    """Rows EXAMINED bound the read, not only assistant messages found.

    A history whose tail is all human rows used to page through the entire
    table looking for a full window, holding the per-character engine lock the
    whole time. The router's HTTP timeout does not stop the worker thread, so
    that scan kept blocking the character's memory reads and writes long after
    the request had given up.
    """
    timestamp = "2026-01-01 00:00:00.000000"
    budget = timeindex_module._LATEST_ASSISTANT_MIN_SCAN_BUDGET
    rows = [
        {
            "session_id": "0",
            "message": _stored_message("ai", "buried answer"),
            "timestamp": timestamp,
        }
    ]
    rows += [
        {
            "session_id": str(index + 1),
            "message": _stored_message("human", "user turn"),
            "timestamp": timestamp,
        }
        for index in range(budget + 500)
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 1, batch_size=256)
    finally:
        engine.dispose()

    assert result.source_available is True
    assert result.messages == [], (
        "the scan ran past its budget to reach the buried assistant row"
    )
    assert result.skipped_row_count <= budget + 256


def _user_bodies_seen(timeindex_module, monkeypatch):
    """Record every stored message the Python-side parser is handed."""
    seen: list[object] = []
    original = timeindex_module._assistant_record_from_stored_message

    def _spy(message_raw):
        seen.append(message_raw)
        return original(message_raw)

    monkeypatch.setattr(
        timeindex_module, "_assistant_record_from_stored_message", _spy
    )
    return seen


def test_user_turn_bodies_never_reach_this_process(
    timeindex_module, tmp_path, monkeypatch
):
    """The role filter runs in SQL, so a user turn's text is never transferred.

    Without it every row in the scanned window was SELECTed and materialized to
    produce a handful of assistant replies -- reported as 4.3 MB of user prose
    read to return one 17-character answer.
    """
    monkeypatch.setattr(timeindex_module, "_json1_supported", None)
    seen = _user_bodies_seen(timeindex_module, monkeypatch)
    timestamp = "2026-01-01 00:00:00.000000"
    rows = [
        {
            "session_id": str(index),
            "message": _stored_message("human", "PRIVATE-USER-TEXT " * 4096),
            "timestamp": timestamp,
        }
        for index in range(8)
    ]
    rows.append(
        {
            "session_id": "9",
            "message": _stored_message("ai", "the visible reply"),
            "timestamp": timestamp,
        }
    )
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 100, batch_size=256)
    finally:
        engine.dispose()

    # The reply still comes back, and the user rows are still counted as
    # skipped -- otherwise "no user text was read" would also hold for a query
    # that returned nothing at all.
    assert result.messages == ["the visible reply"]
    assert result.skipped_row_count == 8
    assert not any("PRIVATE-USER-TEXT" in str(body) for body in seen)


def test_the_role_filter_falls_back_when_the_sqlite_build_lacks_json1(
    timeindex_module, tmp_path, monkeypatch
):
    """An older build without JSON1 reads more, but must not lose the feature."""
    monkeypatch.setattr(timeindex_module, "_json1_supported", False)
    seen = _user_bodies_seen(timeindex_module, monkeypatch)
    timestamp = "2026-01-01 00:00:00.000000"
    rows = [
        {
            "session_id": "1",
            "message": _stored_message("human", "user secret"),
            "timestamp": timestamp,
        },
        {
            "session_id": "2",
            "message": _stored_message("ai", "the visible reply"),
            "timestamp": timestamp,
        },
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 100, batch_size=256)
    finally:
        engine.dispose()

    assert result.messages == ["the visible reply"]
    assert result.skipped_row_count == 1
    assert any("user secret" in str(body) for body in seen)


def test_paging_advances_through_a_window_with_no_assistant_rows(
    timeindex_module, tmp_path, monkeypatch
):
    """The cursor comes from the KEY window, not from the surviving rows.

    Pushing the filter into the paged query would have made LIMIT count
    matching rows, so one statement could walk an unbounded stretch of history;
    keeping the two queries separate is what preserves the scan budget, and it
    means a page containing no assistant row must still advance the cursor.
    """
    monkeypatch.setattr(timeindex_module, "_json1_supported", None)
    timestamp = "2026-01-01 00:00:00.000000"
    rows = [
        {
            "session_id": "0",
            "message": _stored_message("ai", "the oldest answer"),
            "timestamp": timestamp,
        }
    ]
    rows += [
        {
            "session_id": str(index + 1),
            "message": _stored_message("human", f"user turn {index}"),
            "timestamp": timestamp,
        }
        for index in range(300)
    ]
    manager, engine = _create_manager(timeindex_module, tmp_path, rows)
    try:
        result = manager.retrieve_latest_assistant_texts("cat", 5, batch_size=2)
    finally:
        engine.dispose()

    assert result.messages == ["the oldest answer"]
    assert result.skipped_row_count == 300


def test_the_json1_probe_reports_a_modern_build(
    timeindex_module, tmp_path, monkeypatch
):
    monkeypatch.setattr(timeindex_module, "_json1_supported", None)
    manager, engine = _create_manager(timeindex_module, tmp_path, [])
    try:
        assert timeindex_module._supports_json1(engine) is True
    finally:
        engine.dispose()


def test_the_json1_probe_failing_costs_the_filter_not_the_feature(
    timeindex_module, monkeypatch
):
    """JSON1 ships by default from SQLite 3.38, but an older build must not 500.

    Falling back to an unfiltered body read costs memory on such a build;
    letting the probe's error escape would cost the whole insights feature.
    """
    monkeypatch.setattr(timeindex_module, "_json1_supported", None)

    class _BuildWithoutJson1:
        def connect(self):
            raise RuntimeError("no such function: json_valid")

    assert timeindex_module._supports_json1(_BuildWithoutJson1()) is False
    # Cached, so the probe costs one query per process rather than one per page.
    assert timeindex_module._json1_supported is False


def test_a_database_without_the_history_table_reports_no_source(
    timeindex_module, tmp_path
):
    """PRAGMA table_info does not raise for a missing table -- it returns [].

    So an empty or partially restored database read as "a schema with no
    timestamp column", and the SELECT that followed failed against a table
    that is not there. That surfaced as a 503, which the panel renders as a
    retryable error, and no amount of retrying can create the table.

    No table is exactly what source_available=False already means, and it is
    the answer the engine-unavailable branches above give too.
    """
    from sqlalchemy import create_engine

    manager = timeindex_module.TimeIndexedMemory(recent_history_manager=None)

    # A real, perfectly readable database that simply holds something else.
    db_path = tmp_path / "time_indexed.db"
    engine = create_engine("sqlite:///" + str(db_path))
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE something_else (id INTEGER)"))
        conn.commit()

    manager.engines["Carol"] = engine
    manager._ensure_engine_exists = lambda *_a, **_k: True

    result = manager._retrieve_latest_assistant_texts_locked(
        "Carol", 5, batch_size=200,
    )

    assert result.messages == []
    assert result.source_available is False, (
        "a missing table was reported as a failed read, which the panel shows "
        "as retryable even though retrying cannot create it"
    )

    # The dual: a database that DOES have the table still reads from it, so
    # the check cannot pass by reporting no source for everything.
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE " + timeindex_module.TIME_ORIGINAL_TABLE_NAME
                + " (rowid INTEGER PRIMARY KEY, message TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO " + timeindex_module.TIME_ORIGINAL_TABLE_NAME
                + " (message) VALUES (:m)"
            ),
            {"m": json.dumps({"type": "ai", "data": {"content": "hello there"}})},
        )
        conn.commit()

    populated = manager._retrieve_latest_assistant_texts_locked(
        "Carol", 5, batch_size=200,
    )
    assert populated.source_available is True
    assert populated.messages == ["hello there"]


def test_the_visible_boundary_counts_raw_characters_in_a_block_list(
    timeindex_module,
):
    """The recorded length counts the text as it was WRITTEN.

    ``main_logic/core/proactive.py`` stores ``len(full_text)`` and then appends
    the history-only note to that same string, so the boundary indexes the raw
    concatenation. Stripping each block first shortened the body, which slid the
    boundary along by however much came off the front -- and what it slid into
    is the note the rule exists to keep out.

    The string branch is the oracle here: both shapes carry the same text and
    the same recorded length, so they have to produce the same answer.
    """
    import json

    def record(content, visible=None):
        kwargs = {}
        if visible is not None:
            kwargs["anti_repeat_visible_text_length"] = str(visible)
        return json.dumps(
            {"type": "ai", "data": {"content": content, "additional_kwargs": kwargs}}
        )

    def read(raw):
        got = timeindex_module._assistant_record_from_stored_message(raw)
        return got[0] if got else None

    visible = chr(10) + chr(10) + "好呀好呀"
    hidden = chr(10) + "[hidden note]"
    body = visible + hidden

    from_blocks = read(record([{"type": "text", "text": body}], len(visible)))
    from_string = read(record(body, len(visible)))

    assert from_blocks == "好呀好呀", from_blocks
    assert from_blocks == from_string, (
        "the two content shapes disagree, which is the drift this boundary was "
        "shared to prevent"
    )

    # The duals, so this cannot pass by never slicing. Without leading
    # whitespace nothing moved before or after; with no recorded length the
    # body is still trimmed; and a whitespace-only block is still dropped.
    plain = "好呀好呀"
    assert read(record([{"type": "text", "text": plain + hidden}], len(plain))) == plain
    assert read(record([{"type": "text", "text": "  好呀好呀  "}])) == "好呀好呀"
    assert (
        read(record([{"type": "text", "text": "   "}, {"type": "text", "text": "好呀好呀"}]))
        == "好呀好呀"
    )
