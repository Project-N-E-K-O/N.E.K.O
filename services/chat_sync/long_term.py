"""Bound delivery of completed learned-memory snapshots, independent of raw archives."""

from contextlib import closing

from services.chat_sync.long_term_protocol import head, mutation
from services.chat_sync.recent_protocol import canonical
from services.chat_sync.writer import store_connection


class LongTermMemorySync:
    def __init__(self, path, adapter):
        self.path, self.adapter = path, adapter
        with closing(store_connection(path)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS long_term_memory_state '
                         '(domain TEXT PRIMARY KEY, acknowledged TEXT, revision INTEGER NOT NULL DEFAULT 0)')

    def state(self, domain):
        with closing(store_connection(self.path)) as conn:
            row = conn.execute('SELECT acknowledged,revision FROM long_term_memory_state WHERE domain=?', (domain,)).fetchone()
        return {'acknowledged': row[0], 'revision': row[1]} if row else {'acknowledged': None, 'revision': 0}

    def acknowledge(self, domain, operation, revision=None):
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute('INSERT INTO long_term_memory_state VALUES(?,?,0) ON CONFLICT(domain) '
                         'DO UPDATE SET acknowledged=EXCLUDED.acknowledged', (domain, operation))
            if revision is not None:
                conn.execute('UPDATE long_term_memory_state SET revision=? WHERE domain=?', (revision, domain))

    def applied(self, domain, revision):
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute('INSERT INTO long_term_memory_state VALUES(?,NULL,?) ON CONFLICT(domain) '
                         'DO UPDATE SET revision=EXCLUDED.revision', (domain, revision))

    async def sync(self, domain, binding, epoch, request, commit, *, can_apply, session_fingerprint):
        key = domain.key()
        state = await commit(self.state, key)
        local = await commit(self.adapter.read_local, binding, epoch)
        path = f'/v1/chat-sync/characters/{domain.character_uid}/long-term-memory'
        if local and local['operationId'] != state['acknowledged'] and not local['operationId'].startswith('download:'):
            body = canonical(mutation({'operationId': local['operationId'], 'document': local['document']}))
            receipt = await request('PUT', path, body)
            if (not isinstance(receipt, dict) or set(receipt) != {'ownerId', 'characterSyncId', 'operationId', 'revision', 'accepted'}
                    or receipt['ownerId'] != domain.owner_id or receipt['characterSyncId'] != domain.character_uid
                    or receipt['operationId'] != local['operationId'] or type(receipt['accepted']) is not bool
                    or type(receipt['revision']) is not int or not 1 <= receipt['revision'] <= 9_007_199_254_740_991):
                raise ValueError('invalid_long_term_memory')
            revision = receipt['revision'] if receipt['accepted'] else None
            await commit(self.acknowledge, key, local['operationId'], revision)
            if revision is not None:
                state['revision'] = revision
        metadata = head(await request('GET', path + '?metadataOnly=true', None), domain.owner_id, domain.character_uid)
        pending_auth = await commit(self.adapter.needs_reauthentication, binding, epoch, session_fingerprint)
        if not metadata['revision'] or (metadata['revision'] == state['revision'] and not pending_auth):
            return {'changed': False, 'hasMore': False}
        if not can_apply():
            return {'changed': False, 'hasMore': True}
        remote = head(await request('GET', path, None), domain.owner_id, domain.character_uid)
        if remote.get('document') is None or remote['revision'] < metadata['revision']:
            raise ValueError('invalid_long_term_memory')
        expected = local['operationId'] if local else None
        if not can_apply():
            return {'changed': False, 'hasMore': True}
        staged = await commit(self.adapter.stage_remote, binding, remote, epoch, expected, session_fingerprint)
        if staged:
            await commit(self.applied, key, remote['revision'])
        return {'changed': staged, 'hasMore': not staged}
