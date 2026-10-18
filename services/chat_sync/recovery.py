"""Quarantine copied authority and reuse the existing cloud recovery contract."""

from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from services.chat_sync.ledger import ChatDomain, ChatOriginal
from services.chat_sync.protocol import decode_chat_original, encode_chat_original
from services.chat_sync.writer import store_connection


SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_recovery_candidates (
    record_id TEXT NOT NULL, owner TEXT, uid TEXT NOT NULL,
    original TEXT, original_hash TEXT, queued_at_ms INTEGER,
    origin_stream_id TEXT, origin_seq INTEGER, payload BLOB,
    kind TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unchecked');
CREATE UNIQUE INDEX IF NOT EXISTS source_recovery_candidate_domain
    ON source_recovery_candidates(uid,record_id,COALESCE(owner,''));
CREATE TABLE IF NOT EXISTS source_recovery_roles (
    uid TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS source_recovery_delete_ids (
    owner TEXT, uid TEXT NOT NULL, message_id TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS source_recovery_delete_domain
    ON source_recovery_delete_ids(uid,message_id,COALESCE(owner,''));
CREATE TABLE IF NOT EXISTS source_recovery_delete_tasks (
    task_id TEXT PRIMARY KEY, uid TEXT NOT NULL, owner TEXT NOT NULL,
    binding TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'preview');
CREATE TABLE IF NOT EXISTS source_recovery_delete_members (
    task_id TEXT NOT NULL, message_id TEXT NOT NULL, registered INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(task_id,message_id));
'''


def save_outage(directory, uid, record_id, original):
    """Retain producer-proven bytes without borrowing an untrusted old stream.

    Each actual new arrival owns a random stream with sequence one. Retrying the
    same stable message ID retains that first identity; equal text is unrelated.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with closing(sqlite3.connect(directory / 'outage.sqlite3', timeout=10)) as conn, conn:
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('CREATE TABLE IF NOT EXISTS outage_originals '
                     '(record_id TEXT PRIMARY KEY, uid TEXT NOT NULL, original TEXT, original_hash TEXT NOT NULL, '
                     'queued_at_ms INTEGER NOT NULL, origin_stream_id TEXT NOT NULL, origin_seq INTEGER NOT NULL, '
                     'deleted INTEGER NOT NULL DEFAULT 0)')
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT uid,original_hash FROM outage_originals WHERE record_id=?', (record_id,)).fetchone()
        if old:
            if old != (uid, digest):
                raise ValueError('chat_source_identity_conflict')
            return
        conn.execute('INSERT INTO outage_originals VALUES (?,?,?,?,?,?,1,0)',
                     (record_id, uid, payload, digest, time.time_ns() // 1_000_000, uuid4().hex))



def save_outage_deletions(directory, uid, identities):
    """Keep new local hiding separate from every copied deletion request."""
    from services.chat_sync.ledger import _message_ids
    ids = _message_ids(identities)
    with closing(sqlite3.connect(Path(directory) / 'outage.sqlite3', timeout=10)) as conn, conn:
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('CREATE TABLE IF NOT EXISTS outage_deletions '
                     '(uid TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(uid,message_id))')
        conn.executemany('INSERT OR IGNORE INTO outage_deletions VALUES (?,?)', [(uid, identity) for identity in ids])
        _erase_outage_originals(conn, uid, ids)


def _erase_outage_originals(conn, uid, ids):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='outage_originals'").fetchone():
        conn.executemany('UPDATE outage_originals SET original=NULL,deleted=1 WHERE uid=? AND record_id=?', [(uid, identity) for identity in ids])


def _outage_has_originals(conn, uid, ids):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='outage_originals'").fetchone():
        return False
    return any(conn.execute(
        'SELECT 1 FROM outage_originals WHERE uid=? AND original IS NOT NULL AND record_id IN (' + ','.join('?' for _ in page) + ') LIMIT 1',
        (uid, *page)).fetchone() for page in (ids[offset:offset + 200] for offset in range(0, len(ids), 200)))


def purge_outage_originals(directory, uid, ids):
    """Erase readable outage copies of exact deleted IDs while keeping their identities."""
    path = Path(directory) / 'outage.sqlite3'
    if not path.exists():
        return
    with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
        # Most deletes have no outage copy; only an actual body needs a durable write.
        if not _outage_has_originals(conn, uid, ids):
            return
        conn.execute('PRAGMA synchronous=FULL')
        _erase_outage_originals(conn, uid, ids)


def stage_authority(runtime):
    """Preserve immutable evidence while revoking every copied business permission."""
    directory = runtime.state.path.parent
    with closing(store_connection(directory / 'sources.sqlite3')) as source, source:
        source.executescript(SCHEMA)
        with closing(store_connection(directory / 'originals.sqlite3')) as ledger:
            for domain, identity, payload, kind in ledger.execute(
                    "SELECT domain,message_id,payload,'local' FROM chat_originals UNION ALL "
                    "SELECT domain,message_id,payload,'download' FROM chat_downloaded"):
                try:
                    owner, uid, channel, scope = json.loads(domain)
                    ChatDomain(owner, uid, channel, scope)
                except (ValueError, TypeError, RecursionError):
                    continue
                # Malformed or unknown evidence stays hidden. It must not abort
                # revocation and accidentally leave some old tasks executable.
                try:
                    record = decode_chat_original(payload)
                    if record['ownerId'] != owner or record['characterSyncId'] != uid or record['messageId'] != identity:
                        raise ValueError
                except (ValueError, TypeError, RecursionError):
                    # Retain unknown bytes without manufacturing raw provenance
                    # or making this candidate eligible for cloud publication.
                    source.execute('INSERT OR IGNORE INTO source_recovery_candidates '
                                   '(record_id,owner,uid,payload,kind) VALUES (?,?,?,?,?)',
                                   (identity, owner, uid, payload, kind))
                    continue
                original = ChatOriginal(record['role'], record['text'], record['originalTime'], record['timezone'],
                                        record['conversationId'], record['replyToMessageId'])
                raw = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                source.execute('INSERT OR IGNORE INTO source_recovery_candidates '
                               '(record_id,owner,uid,original,original_hash,origin_stream_id,origin_seq,payload,kind) VALUES (?,?,?,?,?,?,?,?,?)',
                               (identity, owner, uid, raw, hashlib.sha256(raw.encode()).hexdigest(),
                                record['originStreamId'], record['originSeq'], payload, kind))
            for domain, payload in ledger.execute('SELECT domain,payload FROM chat_deletions'):
                try:
                    from services.chat_sync.ledger import _message_ids
                    owner, uid, channel, scope = json.loads(domain)
                    ChatDomain(owner, uid, channel, scope)
                    identities = _message_ids(json.loads(payload)['messageIds'])
                except (KeyError, ValueError, TypeError, RecursionError):
                    continue
                for identity in identities:
                    source.execute('INSERT OR IGNORE INTO source_recovery_delete_ids VALUES (?,?,?)', (owner, uid, identity))
        source.execute("INSERT OR IGNORE INTO source_recovery_candidates "
                       "(record_id,owner,uid,original,original_hash,queued_at_ms,origin_stream_id,origin_seq,kind,deleted) "
                       "SELECT record_id,owner,uid,original,original_hash,queued_at_ms,origin_stream_id,origin_seq,'local',deleted FROM source_originals")
        source.execute("INSERT OR IGNORE INTO source_recovery_candidates "
                       "(record_id,uid,original,original_hash,queued_at_ms,origin_stream_id,origin_seq,kind,deleted) "
                       "SELECT record_id,uid,original,original_hash,queued_at_ms,origin_stream_id,origin_seq,'local',deleted FROM source_unbound")
        source.execute('INSERT OR IGNORE INTO source_recovery_delete_ids SELECT NULL,uid,record_id FROM source_unbound WHERE deleted=1 AND deletion_pending=1')
        source.execute("UPDATE source_recovery_candidates SET status='unchecked'")
        source.execute('UPDATE source_recovery_candidates AS candidate SET deleted=1,original=NULL,payload=NULL '
                       'WHERE EXISTS (SELECT 1 FROM source_recovery_delete_ids AS deletion '
                       'WHERE deletion.uid=candidate.uid AND deletion.message_id=candidate.record_id '
                       'AND (deletion.owner IS NULL OR deletion.owner=candidate.owner))')
        source.execute("INSERT INTO source_recovery_roles SELECT DISTINCT uid,'pending' FROM source_recovery_candidates WHERE 1 "
                       "ON CONFLICT(uid) DO UPDATE SET status='pending'")
        source.execute("INSERT INTO source_recovery_roles SELECT DISTINCT uid,'pending' FROM source_recovery_delete_ids WHERE 1 "
                       "ON CONFLICT(uid) DO UPDATE SET status='pending'")
        source.execute("UPDATE source_history_tasks SET status='quarantined'")
        source.execute("UPDATE source_recovery_delete_tasks SET status='quarantined'")
        source.execute('DELETE FROM source_origin_streams')
        source.execute('UPDATE source_originals SET pending=0')
        source.execute('UPDATE source_unbound SET deletion_pending=0')
        # Candidate bodies can enter this table only after current cloud fences.
        # Their stable IDs/origins remain in the quarantine table meanwhile.
        source.execute('DELETE FROM source_unbound WHERE record_id IN (SELECT record_id FROM source_recovery_candidates)')
    with closing(store_connection(directory / 'originals.sqlite3')) as ledger, ledger:
        for table in ('chat_pending', 'chat_batches', 'chat_deletions', 'chat_originals', 'chat_downloaded',
                      'chat_sources', 'chat_lane_rotation', 'chat_cursors', 'chat_recovery_deletion_pages'):
            ledger.execute(f'DELETE FROM {table}')
        if ledger.execute("SELECT 1 FROM sqlite_master WHERE name='chat_oversized'").fetchone():
            ledger.execute('DELETE FROM chat_oversized')
    with closing(store_connection(directory / 'state.sqlite3')) as state, state:
        state.execute('UPDATE sync_settings SET device_id=?', (uuid4().hex,))
        for row in state.execute('SELECT character_uid FROM sync_bindings').fetchall():
            state.execute('UPDATE sync_bindings SET enabled=0,generation=generation+1,request_id=?,binding_id=NULL WHERE character_uid=?', (uuid4().hex, row[0]))
    with closing(store_connection(directory / 'profiles.sqlite3')) as profiles, profiles:
        profiles.execute("UPDATE profile_outbox SET status='quarantined' WHERE status IN ('pending','conflict')")
        profiles.execute('UPDATE profile_state SET apply_pending=0,dirty=1')
        profiles.execute('UPDATE profile_imports SET completed=1')
    with closing(store_connection(directory / 'scheduler.sqlite3')) as scheduler, scheduler:
        scheduler.execute('DELETE FROM scheduler_tasks')
    import_outage(runtime)


def import_outage(runtime):
    """Idempotently stage outage arrivals; cloud verification still precedes consent."""
    directory = runtime.state.path.parent
    path = directory / 'outage.sqlite3'
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as outage, closing(store_connection(directory / 'sources.sqlite3')) as source, source:
        tables = {row[0] for row in outage.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = outage.execute('SELECT * FROM outage_originals') if 'outage_originals' in tables else []
        for identity, uid, original, digest, queued, stream, seq, deleted in rows:
            source.execute('INSERT OR IGNORE INTO source_recovery_candidates '
                           '(record_id,uid,original,original_hash,queued_at_ms,origin_stream_id,origin_seq,kind,deleted) '
                           "VALUES (?,?,?,?,?,?,?,'local',?)", (identity, uid, original, digest, queued, stream, seq, deleted))
            source.execute("INSERT OR IGNORE INTO source_recovery_roles VALUES (?,'pending')", (uid,))
            if deleted:
                source.execute('INSERT OR IGNORE INTO source_recovery_delete_ids VALUES (NULL,?,?)', (uid, identity))
        if 'outage_deletions' in tables:
            for uid, identity in outage.execute('SELECT uid,message_id FROM outage_deletions'):
                source.execute('INSERT OR IGNORE INTO source_recovery_delete_ids VALUES (NULL,?,?)', (uid, identity))
                source.execute("INSERT OR IGNORE INTO source_recovery_roles VALUES (?,'pending')", (uid,))
                source.execute('UPDATE source_recovery_candidates SET original=NULL,payload=NULL,deleted=1 WHERE uid=? AND record_id=?', (uid, identity))
                source.execute('UPDATE source_originals SET original=NULL,pending=0,deleted=1 WHERE uid=? AND record_id=?', (uid, identity))
                source.execute('UPDATE source_unbound SET original=NULL,deleted=1,deletion_pending=0 WHERE uid=? AND record_id=?', (uid, identity))


def role_pending(runtime, uid):
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as conn:
        row = conn.execute('SELECT status FROM source_recovery_roles WHERE uid=?', (uid,)).fetchone()
        return bool(row and row[0] != 'verified')


def pending_candidates(runtime, domain):
    """Build canonical bytes from proven IDs/origins, never from copied ACK flags."""
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as conn:
        rows = conn.execute('SELECT * FROM source_recovery_candidates WHERE uid=? AND (owner IS NULL OR owner=?) '
                            "AND deleted=0 AND status='unchecked' ORDER BY rowid", (domain.character_uid, domain.owner_id)).fetchall()
        names = [item[0] for item in conn.execute('SELECT * FROM source_recovery_candidates LIMIT 0').description]
    grouped = {}
    for values in rows:
        row = dict(zip(names, values))
        try:
            raw = row['original']
            if raw is None or hashlib.sha256(raw.encode()).hexdigest() != row['original_hash']:
                raise ValueError
            original = ChatOriginal(**json.loads(raw))
            canonical = encode_chat_original({
                'schemaVersion': 1, 'ownerId': domain.owner_id, 'characterSyncId': domain.character_uid,
                'channel': 'chat', 'scope': 'private-chat', 'messageId': row['record_id'],
                'originStreamId': row['origin_stream_id'], 'originSeq': row['origin_seq'], **original.fields()})
            if row['payload'] is not None and row['payload'] != canonical:
                raise ValueError
            row['payload'] = canonical
            identity = row['record_id']
            if identity in grouped:
                previous = grouped[identity]
                # Unbound producer proof and its later bound copy represent one
                # current-domain message only when all immutable bytes agree.
                if previous is None or previous['payload'] != canonical:
                    grouped[identity] = None
                elif row['kind'] == 'local':
                    previous['kind'] = 'local'
            else:
                grouped[identity] = row
        except (ValueError, TypeError, AttributeError, RecursionError):
            # Unverifiable legacy rows retain local quarantine and no upload
            # permission. A competing proof cannot choose a body for that ID.
            grouped[row['record_id']] = None
    return [row for row in grouped.values() if row is not None]


def apply_cloud_deletions(runtime, domain, identities):
    """Current remote fences are local tombstones, without echoing a deletion outbox."""
    with closing(store_connection(runtime.ledger.path)) as ledger, ledger:
        ledger.executemany('INSERT OR IGNORE INTO chat_tombstones VALUES (?,?)', [(domain.key(), value) for value in identities])
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as source, source:
        for identity in identities:
            source.execute("UPDATE source_recovery_candidates SET deleted=1,status='deleted',original=NULL,payload=NULL "
                           'WHERE uid=? AND record_id=? AND (owner IS NULL OR owner=?)',
                           (domain.character_uid, identity, domain.owner_id))
            source.execute('DELETE FROM source_recovery_delete_ids WHERE uid=? AND message_id=? AND (owner IS NULL OR owner=?)',
                           (domain.character_uid, identity, domain.owner_id))
            source.execute('UPDATE source_originals SET original=NULL,pending=0,deleted=1 WHERE uid=? AND record_id=? AND owner=?',
                           (domain.character_uid, identity, domain.owner_id))
            source.execute('UPDATE source_unbound SET original=NULL,deleted=1,deletion_pending=0 WHERE uid=? AND record_id=?', (domain.character_uid, identity))
    runtime.ledger.purge_deleted_originals(domain)


def apply_checked_candidates(runtime, domain, candidates, results):
    """Only cloud-proven delivery becomes visible; missing local proof awaits consent."""
    from services.chat_sync.ledger import _remember_origin_identity
    for row, result in zip(candidates, results):
        identity, state = row['record_id'], result['state']
        if state == 'deleted':
            apply_cloud_deletions(runtime, domain, [identity])
            continue
        with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as source:
            hidden = source.execute('SELECT deleted FROM source_recovery_candidates WHERE uid=? AND record_id=? AND owner IS ?',
                                    (domain.character_uid, identity, row['owner'])).fetchone()[0]
        if hidden:
            continue
        if state == 'received':
            with closing(store_connection(runtime.ledger.path)) as ledger, ledger:
                _remember_origin_identity(ledger, domain, decode_chat_original(row['payload']))
                if not ledger.execute('SELECT 1 FROM chat_tombstones WHERE domain=? AND message_id=?', (domain.key(), identity)).fetchone():
                    ledger.execute('INSERT OR IGNORE INTO chat_downloaded VALUES (?,?,?)', (domain.key(), identity, row['payload']))
        with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as source, source:
            source.execute('UPDATE source_recovery_candidates SET status=?,payload=? WHERE uid=? AND record_id=? '
                           'AND (owner IS NULL OR owner=?) AND deleted=0 AND original_hash=? '
                           'AND origin_stream_id=? AND origin_seq=? AND (payload IS NULL OR payload=?)',
                           (state, row['payload'], domain.character_uid, identity, domain.owner_id,
                            row['original_hash'], row['origin_stream_id'], row['origin_seq'], row['payload']))
            if state in ('missing', 'staged') and row['kind'] == 'local':
                # Each file finishes its head before opening the next writer.
                # The role gate stays pending across this safe cross-file gap.
                source.execute('DELETE FROM source_originals WHERE record_id=? AND uid=? AND owner=?',
                               (identity, domain.character_uid, domain.owner_id))
                source.execute('INSERT OR IGNORE INTO source_unbound '
                               '(record_id,uid,scope,original,original_hash,queued_at_ms,deleted,origin_stream_id,origin_seq,verified_owner) '
                               "VALUES (?,?,'private-chat-original-v1',?,?,?,0,?,?,?)",
                               (identity, domain.character_uid, row['original'], row['original_hash'],
                                row['queued_at_ms'] or 0, row['origin_stream_id'], row['origin_seq'], domain.owner_id))


def finish_role(runtime, domain, binding, fingerprint):
    """Freeze a separately reviewed deletion set and release the normal role gate."""
    task_id = uuid4().hex
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as conn, conn:
        conn.execute("UPDATE source_recovery_delete_tasks SET status='cancelled' WHERE uid=? AND status='preview'", (domain.character_uid,))
        conn.execute('INSERT INTO source_recovery_delete_tasks(task_id,uid,owner,binding,fingerprint) VALUES (?,?,?,?,?)',
                     (task_id, domain.character_uid, domain.owner_id, json.dumps(binding, sort_keys=True), fingerprint))
        conn.execute('INSERT INTO source_recovery_delete_members(task_id,message_id) '
                     'SELECT DISTINCT ?,message_id FROM source_recovery_delete_ids WHERE uid=? AND (owner IS NULL OR owner=?)',
                     (task_id, domain.character_uid, domain.owner_id))
        count = conn.execute('SELECT count(*) FROM source_recovery_delete_members WHERE task_id=?', (task_id,)).fetchone()[0]
        conn.execute("UPDATE source_recovery_roles SET status='verified' WHERE uid=?", (domain.character_uid,))
    return {'taskId': task_id, 'count': count}


def approve_deletions(runtime, uid, task_id, binding, fingerprint):
    """Reuse the ordinary pure-marker outbox after exact renewed deletion consent."""
    path = runtime.state.path.parent / 'sources.sqlite3'
    with closing(store_connection(path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        task = conn.execute('SELECT * FROM source_recovery_delete_tasks WHERE task_id=? AND uid=?', (task_id, uid)).fetchone()
        if task is None or task['status'] not in ('preview', 'approved'):
            raise ValueError('chat_sync_history_changed')
        pinned = json.loads(task['binding'])
        if (any(pinned[key] != binding[key] for key in ('owner_id', 'character_uid', 'binding_id', 'request_id'))
                or (task['status'] == 'preview' and (task['fingerprint'] != fingerprint or pinned != binding))):
            raise ValueError('chat_sync_history_changed')
        conn.execute("UPDATE source_recovery_delete_tasks SET status='approved' WHERE task_id=?", (task_id,))
        ids = [row[0] for row in conn.execute('SELECT message_id FROM source_recovery_delete_members WHERE task_id=? AND registered=0 ORDER BY rowid LIMIT 200', (task_id,))]
    if ids:
        runtime.ledger.queue_recovery_deletions(ChatDomain(binding['owner_id'], uid), task_id, ids)
    with closing(store_connection(path)) as conn, conn:
        conn.executemany('UPDATE source_recovery_delete_members SET registered=1 WHERE task_id=? AND message_id=?', [(task_id, identity) for identity in ids])
        remaining = conn.execute('SELECT count(*) FROM source_recovery_delete_members WHERE task_id=? AND registered=0', (task_id,)).fetchone()[0]
        if not remaining:
            conn.execute("UPDATE source_recovery_delete_tasks SET status='registered' WHERE task_id=?", (task_id,))
    return {'taskId': task_id, 'registeredCount': len(ids), 'remaining': remaining}


def approved_deletion_tasks(runtime, uid):
    """Continue only renewed approved fixed tasks through the existing worker."""
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as conn:
        return [row[0] for row in conn.execute("SELECT task_id FROM source_recovery_delete_tasks WHERE uid=? AND status='approved' ORDER BY rowid", (uid,))]


def quarantined_local_ids(runtime, uid):
    """Read an exact local clear boundary without accepting any copied permission."""
    directory, identities = runtime.state.path.parent, []
    for name, queries in (
        ('sources.sqlite3', [('source_originals', 'record_id'), ('source_unbound', 'record_id'), ('source_recovery_candidates', 'record_id')]),
        ('outage.sqlite3', [('outage_originals', 'record_id')]),
    ):
        path = directory / name
        if not path.exists():
            continue
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, field in queries:
                if table in tables:
                    identities.extend(row[0] for row in conn.execute(f'SELECT {field} FROM {table} WHERE uid=? AND deleted=0', (uid,)))
    with closing(sqlite3.connect(runtime.ledger.path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        for domain, identity in conn.execute('SELECT domain,message_id FROM chat_originals UNION SELECT domain,message_id FROM chat_downloaded'):
            if json.loads(domain)[1] == uid:
                identities.append(identity)
    return list(dict.fromkeys(identities))
