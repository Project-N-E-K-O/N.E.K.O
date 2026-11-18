"""Keep SQLite handles from locking index publication and collection on Windows."""

import sqlite3

import pytest

from services.knowledge_library import indexing
from services.knowledge_library.models import LibraryLimits

pytestmark = pytest.mark.unit_fast


@pytest.fixture
def connections(monkeypatch):
    """Retain connections so garbage collection cannot hide a missing close."""
    opened = []
    real_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            """Record actual handle release, independently of transaction exit."""
            super().close()
            self.closed = True

    def connect(path):
        connection = real_connect(path, factory=TrackedConnection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(indexing.sqlite3, "connect", connect)
    try:
        yield opened
    finally:
        for connection in opened:
            connection.close()


def test_index_publication_releases_writer_before_atomic_replace(tmp_path, monkeypatch, connections):
    """Emulate Windows refusing to rename a database with an open handle."""
    replace = indexing.os.replace

    def checked_replace(source, destination):
        if any(not connection.closed for connection in connections):
            raise PermissionError("SQLite handle still locks index publication")
        replace(source, destination)

    monkeypatch.setattr(indexing.os, "replace", checked_replace)
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, [], LibraryLimits())
    assert path.is_file()


@pytest.mark.parametrize("invalid_query", [False, True])
def test_index_search_releases_reader_on_success_and_error(tmp_path, monkeypatch, connections, invalid_query):
    """A completed or rejected query must leave an old index removable."""
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, [], LibraryLimits())
    for connection in connections:
        connection.close()
    connections.clear()
    if invalid_query:
        monkeypatch.setattr(indexing, "fts_match_expression", lambda _query: '"unterminated')
        with pytest.raises(sqlite3.OperationalError):
            indexing.rank(path, [], "query", None, None, 5)
    else:
        assert indexing.rank(path, [], "query", None, None, 5) == []
    assert len(connections) == 1 and connections[0].closed
