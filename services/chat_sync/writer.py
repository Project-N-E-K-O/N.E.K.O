"""External installation authority for the dedicated chat-sync SQLite writers.

The witness contains metadata only. A pending reservation with the old database
head is deliberately ambiguous: it can be a pre-commit crash or a restored file.
Only a matching new head can complete a reservation without cloud recovery.
"""

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from filelock import FileLock, Timeout


DATABASES = ('sources.sqlite3', 'state.sqlite3', 'originals.sqlite3', 'profiles.sqlite3', 'scheduler.sqlite3')
_registry = {}
_registry_lock = threading.RLock()


class WriterQuarantined(ValueError):
    """Pause optional sync without leaking paths or chat content to callers."""

    def __init__(self):
        super().__init__('chat_sync_writer_recovery_required')


def store_connection(path):
    """Dedicated stores always require explicit installation authority."""
    writer = registered_writer(Path(path).parent)
    if writer is None:
        raise WriterQuarantined()
    return writer.connect(path)


def registered_writer(directory):
    """Reuse one authority when the raw producer constructs a source per message."""
    with _registry_lock:
        return _registry.get(Path(directory).resolve())


class ChatSyncWriter:
    """Serialize local authority checks and database commits across processes."""

    def __init__(self, directory, witness_path, *, database_names=DATABASES):
        self.directory = Path(directory).resolve()
        self.database_names = tuple(database_names)
        self.path = Path(witness_path).resolve() if witness_path is not None else None
        self._local = threading.local()
        self._mutex = threading.RLock()
        self._file_lock = FileLock(str(self.path) + '.lock', timeout=10) if self.path else None
        self._initializing = False
        self._fresh = False
        self._fault = False
        self._trusted_heads = None
        with _registry_lock:
            _registry[self.directory] = self

    @contextmanager
    def locked(self):
        """Never hold this local lock during HTTP, model work or asynchronous waits."""
        if self.path is None:
            raise WriterQuarantined()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._mutex, self._file_lock:
                yield
        except (OSError, Timeout):
            self._fault = True
            raise WriterQuarantined() from None

    def _read(self):
        try:
            value = json.loads(self.path.read_bytes())
            if (not isinstance(value, dict) or set(value) != {'version', 'installationId', 'lineage', 'heads', 'pending', 'quarantined'}
                    or value['version'] != 1 or not isinstance(value['heads'], dict)
                    or any(name not in self.database_names for name in value['heads'])
                    or type(value['quarantined']) is not bool):
                raise ValueError
            for identity in (value['installationId'], value['lineage']):
                if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
                    raise ValueError
            for head in value['heads'].values():
                self._validate_head(head)
            pending = value['pending']
            if pending is not None:
                if not isinstance(pending, dict) or set(pending) != {'database', 'old', 'new'} or pending['database'] not in self.database_names:
                    raise ValueError
                self._validate_head(pending['old'])
                self._validate_head(pending['new'])
                if pending['old'] != value['heads'].get(pending['database']):
                    raise ValueError
            return value
        except (OSError, ValueError, TypeError, RecursionError):
            self._fault = True
            raise WriterQuarantined() from None

    @staticmethod
    def _validate_head(head):
        if (not isinstance(head, dict) or set(head) != {'lineage', 'revision', 'token'}
                or type(head['revision']) is not int or head['revision'] < 0):
            raise ValueError
        for value in (head['lineage'], head['token']):
            if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError

    def _write(self, value):
        """fsync both replacement contents and its directory before admitting a commit."""
        target = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(target, self.path)
            # Windows lacks directory fsync; file replacement is still atomic,
            # and an interrupted finalization retains a recoverable reservation.
            if os.name != 'nt':
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError:
            self._fault = True
            raise WriterQuarantined() from None
        except BaseException:
            self._fault = True
            raise

    @staticmethod
    def _head(path):
        if not path.exists():
            return None
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='chat_sync_writer_head'").fetchone():
                return None
            row = conn.execute('SELECT lineage,revision,token FROM chat_sync_writer_head WHERE singleton=1').fetchone()
            if row is None:
                return None
            value = dict(zip(('lineage', 'revision', 'token'), row))
            ChatSyncWriter._validate_head(value)
            return value

    def _check_locked(self):
        if self._initializing:
            return None
        if self._fault:
            raise WriterQuarantined()
        state = self._read()
        if state['quarantined']:
            raise WriterQuarantined()
        try:
            actual = {name: self._head(self.directory / name) for name in self.database_names if (self.directory / name).exists()}
        except (sqlite3.Error, ValueError, TypeError):
            self._fault = True
            raise WriterQuarantined() from None
        pending = state['pending']
        expected = dict(state['heads'])
        if pending:
            expected[pending['database']] = pending['new']
        if actual != expected:
            self._fault = True
            raise WriterQuarantined()
        if pending:
            state['heads'] = expected
            state['pending'] = None
            self._write(state)
        self._remember_heads(state['heads'])
        return state

    def _remember_heads(self, heads):
        self._trusted_heads = {name: dict(value) for name, value in heads.items()}

    def local_visibility_trusted(self):
        """Only this live process may retain local visibility after witness I/O failure."""
        if self._trusted_heads is None:
            return False
        try:
            actual = {name: self._head(self.directory / name) for name in self.database_names if (self.directory / name).exists()}
            return actual == self._trusted_heads
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return False

    def check(self):
        """Check every registered file before source recovery, permission or HTTP."""
        with self.locked():
            return self._check_locked()

    def epoch(self):
        """Pin an asynchronous operation to installation authority, not a mutable revision."""
        state = self.check()
        return state['installationId'], state['lineage']

    def require_epoch(self, expected):
        if self.epoch() != expected:
            raise WriterQuarantined()

    @contextmanager
    def initialize(self):
        """Add schemas without promoting an existing database to installation authority."""
        if self._initializing:
            yield
            return
        with self.locked():
            prior_files = {name for name in self.database_names if (self.directory / name).exists()}
            healthy = False
            existing = any((self.directory / name).exists() for name in self.database_names)
            self._fresh = not existing and not self.path.exists()
            if self.path.exists():
                try:
                    self._check_locked()
                    healthy = True
                except WriterQuarantined:
                    # Constructors may open already-existing schemas in recovery
                    # mode, but may not overwrite a corrupt witness or its heads.
                    pass
            self._initializing = not healthy or any(name not in prior_files for name in self.database_names)
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                yield
                if self._fresh:
                    state = {'version': 1, 'installationId': uuid4().hex, 'lineage': uuid4().hex,
                             'heads': {}, 'pending': None, 'quarantined': False}
                    for name in self.database_names:
                        path = self.directory / name
                        if path.exists():
                            head = {'lineage': state['lineage'], 'revision': 0, 'token': uuid4().hex}
                            self._set_head(path, head)
                            state['heads'][name] = head
                    self._write(state)
                    self._remember_heads(state['heads'])
                elif healthy:
                    state = self._read()
                    for name in self.database_names:
                        path = self.directory / name
                        if name not in prior_files and path.exists():
                            head = {'lineage': state['lineage'], 'revision': 0, 'token': uuid4().hex}
                            self._set_head(path, head)
                            state['heads'][name] = head
                    self._write(state)
                    self._remember_heads(state['heads'])
                elif not self.path.exists():
                    self._write({'version': 1, 'installationId': uuid4().hex, 'lineage': uuid4().hex,
                                 'heads': {}, 'pending': None, 'quarantined': True})
            finally:
                self._initializing = False

    def recover(self, stage):
        """Explicitly revoke copied authority before issuing any new installation head."""
        with self.locked():
            state = {'version': 1, 'installationId': uuid4().hex, 'lineage': uuid4().hex,
                     'heads': {}, 'pending': None, 'quarantined': True}
            # A crash during multi-file staging cannot expose partial revocation.
            # The durable quarantine is replaced only after every file is sealed.
            self._write(state)
            self._trusted_heads = None
            self._initializing = True
            try:
                stage()
                for name in self.database_names:
                    path = self.directory / name
                    if path.exists():
                        head = {'lineage': state['lineage'], 'revision': 0, 'token': uuid4().hex}
                        self._set_head(path, head)
                        state['heads'][name] = head
                state['quarantined'] = False
                self._write(state)
                self._fault = False
                self._remember_heads(state['heads'])
            finally:
                self._initializing = False

    @staticmethod
    def _set_head(path, head):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('CREATE TABLE IF NOT EXISTS chat_sync_writer_head '
                         '(singleton INTEGER PRIMARY KEY CHECK(singleton=1),lineage TEXT NOT NULL,revision INTEGER NOT NULL,token TEXT NOT NULL)')
            conn.execute('INSERT OR REPLACE INTO chat_sync_writer_head VALUES (1,?,?,?)', tuple(head.values()))

    def connect(self, path, **kwargs):
        """Open a checked short connection; normal reads never advance a writer head."""
        path = Path(path).resolve()
        if path.parent != self.directory or path.name not in self.database_names:
            raise WriterQuarantined()
        scope = self.locked()
        scope.__enter__()
        try:
            self._check_locked()
            conn = sqlite3.connect(path, timeout=10, factory=_WriterConnection, **kwargs)
            conn._authority, conn._scope, conn._database = self, scope, path.name
            conn._reservation = None
            conn.execute('PRAGMA synchronous=FULL')
            return conn
        except BaseException:
            scope.__exit__(None, None, None)
            raise

    def reserve(self, database):
        """Persist an unpredictable successor before any business SQL can commit."""
        state = self._check_locked()
        if state is None:
            return None
        old = state['heads'].get(database)
        if old is None:
            # A newly needed optional source file is enlisted before its first
            # raw write; an existing unheaded file always fails the earlier check.
            raise WriterQuarantined()
        new = {'lineage': state['lineage'], 'revision': old['revision'] + 1, 'token': uuid4().hex}
        state['pending'] = {'database': database, 'old': old, 'new': new}
        self._write(state)
        return state


class _WriterConnection(sqlite3.Connection):
    """Cover explicit commit, sqlite context commits and executescript boundaries."""

    @staticmethod
    def _mutation(sql):
        command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ''
        return command not in {'SELECT', 'PRAGMA', 'EXPLAIN', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE', 'ATTACH', 'DETACH'}

    def _before(self, sql):
        if self._mutation(sql) and self._reservation is None:
            if not self._authority._initializing and not self.in_transaction:
                super().execute('BEGIN IMMEDIATE')
            self._reservation = self._authority.reserve(self._database)

    def execute(self, sql, parameters=(), /):
        self._before(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters, /):
        self._before(sql)
        return super().executemany(sql, parameters)

    def executescript(self, sql_script, /):
        # executescript commits before running its script. Reserve first and
        # seal afterward: any crash/partial script leaves an ambiguous old head
        # and therefore requires recovery; no SQL parser or event store is added.
        self.commit()
        self._before('CREATE')
        try:
            result = super().executescript(sql_script)
            self.commit()
            return result
        except BaseException:
            self._authority._fault = True
            self._reservation = None
            raise

    def commit(self):
        reservation = self._reservation
        if reservation is not None:
            head = reservation['pending']['new']
            super().execute('INSERT OR REPLACE INTO chat_sync_writer_head VALUES (1,?,?,?)', tuple(head.values()))
        super().commit()
        if reservation is not None:
            reservation['heads'][self._database] = reservation['pending']['new']
            reservation['pending'] = None
            # This process observed SQLite commit; preserve local visibility even
            # if the following witness I/O fails. Sync authority still pauses.
            self._authority._remember_heads(reservation['heads'])
            self._authority._write(reservation)
            self._reservation = None

    def rollback(self):
        super().rollback()
        if self._reservation is not None:
            # This live process observed SQLite rollback while retaining the lock.
            # This evidence is unavailable after a crash, which stays quarantined.
            state = self._reservation
            state['pending'] = None
            self._authority._write(state)
            self._reservation = None

    def __exit__(self, kind, value, traceback):
        if kind is None:
            try:
                self.commit()
            except BaseException:
                super().rollback()
                raise
        else:
            self.rollback()
        return False

    def close(self):
        try:
            if self.in_transaction:
                self.rollback()
            super().close()
        finally:
            self._scope.__exit__(None, None, None)
