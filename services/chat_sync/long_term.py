"""Bound delivery of completed learned-memory snapshots, independent of raw archives."""

import time

from services.chat_sync.long_term_protocol import head, mutation, receipt
from services.chat_sync.recent_protocol import canonical
from services.chat_sync.writer import registered_writer

# Each refusal is followed by a merge with the newer revision; this many in a
# row means another device keeps publishing first, so the lane backs off.
MAX_CONSECUTIVE_CONFLICTS = 5


class LongTermMemorySync:
    def __init__(self, path, adapter):
        self.path, self.adapter = path, adapter
        self._conflicts = {}

    async def sync(self, domain, binding, epoch, request, commit, *, can_apply, session_fingerprint):
        key = domain.key()
        local = await commit(self.adapter.read_local, binding, epoch)
        pending = local['pending'] if local else None
        path = f'/v1/chat-sync/characters/{domain.character_uid}/long-term-memory'
        if pending:
            # The server writes only onto the revision this snapshot was merged with.
            body = canonical(mutation({'operationId': pending['operationId'], 'baseRevision': pending['baseRevision'],
                                       'document': pending['document']}))
            answer = receipt(await request('PUT', path, body), domain.owner_id, domain.character_uid, pending['operationId'])
            if answer['accepted']:
                self._conflicts.pop(key, None)
                await commit(self.adapter.acknowledge, binding, epoch, pending['operationId'], answer['revision'])
            elif answer.get('conflict'):
                # Another device published first. Nothing is recorded as uploaded;
                # the newer revision is fetched below and merged by the memory owner.
                self._conflicts[key] = self._conflicts.get(key, 0) + 1
                if self._conflicts[key] > MAX_CONSECUTIVE_CONFLICTS:
                    self._conflicts.pop(key, None)
                    raise ValueError('long_term_memory_conflict_unresolved')
            else:
                await commit(self.adapter.refresh_pending, binding, epoch, pending['operationId'],
                             time.time_ns() // 1_000_000)
                raise ValueError('long_term_memory_upload_refused')
        revision = await commit(self.adapter.base_revision, binding, epoch)
        metadata = head(await request('GET', path + '?metadataOnly=true', None), domain.owner_id, domain.character_uid)
        if metadata.get('expired') or metadata['revision'] < revision:
            return await self._follow(binding, epoch, metadata, commit)
        pending_auth = await commit(self.adapter.needs_reauthentication, binding, epoch, session_fingerprint)
        if not metadata['revision'] or (metadata['revision'] <= revision and not pending_auth):
            return {'changed': False, 'hasMore': False}
        if not can_apply():
            return {'changed': False, 'hasMore': True}
        if not pending_auth and await commit(self.adapter.staged_revision, binding, epoch) == metadata['revision']:
            # Already downloaded and waiting for the memory owner.
            return {'changed': True, 'hasMore': False}
        remote = head(await request('GET', path, None), domain.owner_id, domain.character_uid)
        if remote.get('expired'):
            return await self._follow(binding, epoch, remote, commit)
        if remote.get('document') is None or remote['revision'] < metadata['revision']:
            raise ValueError('invalid_long_term_memory')
        if not can_apply():
            return {'changed': False, 'hasMore': True}
        staged = await commit(self.adapter.stage_remote, binding, remote, epoch, revision, session_fingerprint)
        return {'changed': staged, 'hasMore': not staged}

    async def _follow(self, binding, epoch, metadata, commit):
        """Adopt a revision number whose body cannot be merged: expired, or a server that restarted."""
        if metadata.get('expired'):
            await commit(self._expire_incoming, binding, epoch, metadata)
        await commit(self.adapter.adopt_revision, binding, epoch, metadata['revision'])
        return {'changed': False, 'hasMore': False}

    def _expire_incoming(self, binding, epoch, expired):
        writer = registered_writer(self.path.parent)
        if writer is None:
            raise ValueError('chat_sync_store_unregistered')
        self.adapter.expire_incoming(binding, epoch, expired, require_epoch=writer.require_epoch)
