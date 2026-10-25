"""Translate private learned memory, without opening original chat databases."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from utils.long_term_memory_protocol import document
from utils.recent_memory_protocol import canonical


SUBJECTS = ('master', 'neko', 'relationship')
FILES = {'persona': 'persona.json', 'facts': 'facts.json', 'archive': 'facts_archive.json', 'reflections': 'reflections.json'}
EVIDENCE_FIELDS = {'reinforcement': 'reinforcement', 'disputation': 'disputation', 'importance': 'importance',
    'user_fact_reinforce_count': 'userFactReinforceCount', 'protected': 'protected',
    'rein_last_signal_at': 'reinLastSignalAt', 'disp_last_signal_at': 'dispLastSignalAt'}


def timestamp(value):
    if type(value) in (int, float):
        return int(value)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        try:
            seconds = parsed.timestamp()
        except OSError:
            if sys.platform != 'win32' or parsed.tzinfo is not None:
                raise
            from win32timezone import TimeZoneInfo

            # Use the OS local timezone and datetime arithmetic, bypassing
            # Windows CRT limits without interpreting local dates as UTC.
            seconds = parsed.replace(tzinfo=TimeZoneInfo.local()).timestamp()
        return int(seconds * 1000)
    return 0


def iso(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def private(entry):
    return not isinstance(entry, dict) or not (entry.get('scope') or entry.get('subject_kind'))


def _entry_id(subject, native):
    return native.get('id') or 'legacy_' + hashlib.sha256(f'{subject}:{native.get("text")}'.encode()).hexdigest()[:24]


def read_views(directory):
    result = {}
    for key, filename in FILES.items():
        path = Path(directory) / filename
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            value = {} if key == 'persona' else []
        if not isinstance(value, dict if key == 'persona' else list):
            raise ValueError('invalid_long_term_memory')
        result[key] = value
    return result


def export_document(views, completed_at):
    rows = {}

    def add(kind, subject, value, *, archived=False):
        if not private(value) or subject not in SUBJECTS:
            return
        native = value if isinstance(value, dict) else {'text': str(value)}
        text = native.get('text')
        if not isinstance(text, str) or not text.strip():
            return
        key = _entry_id(subject, native)
        metadata = {'sourceSyncId': native.get('source_sync_id') or f'desktop:{key}'[:200],
                    'createdAt': timestamp(native.get('created_at')), 'updatedAt': timestamp(native.get('updated_at') or native.get('created_at'))}
        for original, portable in EVIDENCE_FIELDS.items():
            if native.get(original) is not None:
                metadata[portable] = timestamp(native[original]) if portable.endswith('At') else native[original]
        for original, portable in [('source_message_ids', 'sourceMessageIds'), ('source_fact_ids', 'sourceFactIds')]:
            if native.get(original):
                metadata[portable] = list(native[original])
        if kind == 'persona':
            if native.get('source') == 'character_card':
                return
            sources = list(native.get('merged_from_ids') or [])
            if native.get('source') == 'reflection' and native.get('source_id'):
                sources.append(native['source_id'])
            if sources:
                metadata['sourceReflectionIds'] = list(dict.fromkeys(sources))
        if kind == 'fact':
            metadata.update(importance=native.get('importance', 5), absorbed=bool(native.get('absorbed')), archived=archived)
            if native.get('event_start_at'):
                metadata['eventAt'] = timestamp(native['event_start_at'])
        if kind == 'reflection':
            if native.get('status') not in ('confirmed', 'promoted'):
                return
            metadata['sourceFactIds'] = list(native.get('source_fact_ids') or [])
            metadata['evidenceReview'] = 'supported'
            if native.get('confirmed_at'):
                metadata['evidenceReviewedAt'] = timestamp(native['confirmed_at'])
            if native.get('promoted_at'):
                metadata['promotedAt'] = timestamp(native['promoted_at'])
        identity = (kind, subject, key)
        if identity not in rows or not archived:
            rows[identity] = {'kind': kind, 'subject': subject, 'id': key, 'text': text, 'metadata': metadata}

    for subject in SUBJECTS:
        section = views['persona'].get(subject) or {}
        for value in section.get('facts', []):
            add('persona', subject, value)
    for field, archived in [('archive', True), ('facts', False)]:
        for value in views[field]:
            if isinstance(value, dict):
                add('fact', value.get('entity'), value, archived=archived)
    for value in views['reflections']:
        if isinstance(value, dict):
            add('reflection', value.get('entity'), value)
    return document({'schemaVersion': 1, 'completedAt': completed_at, 'entries': [rows[key] for key in sorted(rows)]})


def import_document(incoming, existing, *, baseline=None):
    incoming = document(incoming)
    result = deepcopy(existing)
    local_persona = {(subject, _entry_id(subject, row)): row for subject in SUBJECTS
                     for row in existing['persona'].get(subject, {}).get('facts', [])
                     if isinstance(row, dict) and private(row) and row.get('source') != 'character_card'}
    pending = set()
    if baseline is not None:
        previous = {(row['kind'], row['subject'], row['id']): row for row in baseline}
        current = {(row['kind'], row['subject'], row['id']): row
                   for row in export_document(existing, incoming['completedAt'])['entries']}
        pending = {key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)}

    def retained(kind, subject, value):
        native = value if isinstance(value, dict) else {'text': str(value)}
        return (kind, subject, _entry_id(subject, native)) in pending

    # Keep static character-card entries and scoped/group partitions on this device.
    for subject in SUBJECTS:
        section = result['persona'].setdefault(subject, {})
        section['facts'] = [row for row in section.get('facts', []) if retained('persona', subject, row)
                            or (isinstance(row, dict) and (not private(row) or row.get('source') == 'character_card'))]
    for field in ('facts', 'archive'):
        result[field] = [row for row in result[field] if isinstance(row, dict)
                         and (not private(row) or row.get('entity') not in SUBJECTS
                              or retained('fact', row.get('entity'), row))]
    incoming_reflections = {(row['subject'], row['id']) for row in incoming['entries'] if row['kind'] == 'reflection'}
    result['reflections'] = [row for row in result['reflections'] if isinstance(row, dict)
        and (not private(row) or row.get('entity') not in SUBJECTS or retained('reflection', row.get('entity'), row)
             or (row.get('status') not in ('confirmed', 'promoted')
                 and (not isinstance(row.get('id'), str)
                      or (row.get('entity'), row.get('id')) not in incoming_reflections)))]
    for row in incoming['entries']:
        kind, subject, meta = row['kind'], row['subject'], row['metadata']
        if (kind, subject, row['id']) in pending:
            continue
        if meta.get('voidedAt') or meta.get('status') in ('denied', 'retracted', 'superseded'):
            continue
        native = {'id': row['id'], 'text': row['text'], 'source_sync_id': meta.get('sourceSyncId') or f'snapshot:{row["id"]}'[:200]}
        for original, portable in [('created_at', 'createdAt'), ('updated_at', 'updatedAt'),
                                   ('rein_last_signal_at', 'reinLastSignalAt'), ('disp_last_signal_at', 'dispLastSignalAt')]:
            if meta.get(portable):
                native[original] = iso(meta[portable])
        for original, portable in EVIDENCE_FIELDS.items():
            if portable in meta and not portable.endswith('At'):
                native[original] = meta[portable]
        for original, portable in [('source_message_ids', 'sourceMessageIds'), ('source_fact_ids', 'sourceFactIds')]:
            if meta.get(portable):
                native[original] = list(meta[portable])
        if kind == 'persona':
            sources = meta.get('sourceReflectionIds') or []
            native.update(source='reflection' if sources else 'manual', source_id=sources[0] if sources else native['source_sync_id'],
                          merged_from_ids=list(sources[1:]), protected=False)
            previous = local_persona.get((subject, row['id']), {})
            # The protocol omits receiver-only state and external-import ledgers.
            # Keep these locally; reflection links still come from the snapshot.
            for field in ('suppress', 'suppressed_at', 'recent_mentions', 'sub_zero_days', 'sub_zero_last_increment_date'):
                if field in previous:
                    native[field] = deepcopy(previous[field])
            if previous.get('source') and previous['source'] != 'reflection':
                native['merged_from_ids'] = list(sources)
                for field in ('source', 'source_id', 'external_import'):
                    if field in previous:
                        native[field] = deepcopy(previous[field])
            result['persona'][subject]['facts'].append(native)
        elif kind == 'fact':
            native.update(entity=subject, source='ai_disclosure' if subject == 'neko' else 'user_observation',
                          importance=meta.get('importance', 5), absorbed=bool(meta.get('absorbed')), signal_processed=True,
                          hash=hashlib.sha256(row['text'].encode()).hexdigest()[:16])
            if meta.get('eventAt'):
                native['event_start_at'] = iso(meta['eventAt'])
            result['archive' if meta.get('archived') else 'facts'].append(native)
        else:
            native.update(entity=subject, source_fact_ids=list(meta.get('sourceFactIds') or []),
                          status='promoted' if meta.get('promotedAt') else 'confirmed',
                          confirmed_at=iso(meta.get('evidenceReviewedAt') or incoming['completedAt']))
            if meta.get('promotedAt'):
                native['promoted_at'] = iso(meta['promotedAt'])
            result['reflections'].append(native)
    return result


def domain_filename(owner, uid, service_url):
    return f'{uid}-{hashlib.sha256(canonical([owner, service_url])).hexdigest()[:16]}'


class DesktopLongTermAdapter:
    """Main-process staging; only memory_server applies native learned views."""

    def __init__(self, config_manager):
        self.config = config_manager
        self.directory = Path(config_manager.app_docs_dir) / 'chat_sync' / 'long_term'
        self._cache = {}

    def local_path(self, binding):
        return self.directory / (domain_filename(binding['owner_id'], binding['character_uid'], binding['service_url']) + '.json')

    def incoming_path(self, uid):
        return self.directory / f'{uid}.incoming.json'

    def read_local(self, binding, epoch):
        path = self.local_path(binding)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = self._cache.get(path)
        if cached is None or cached[0] != stamp:
            value = json.loads(path.read_text(encoding='utf-8'))
            document(value['document'])
            self._cache[path] = (stamp, value)
        value = self._cache[path][1]
        if (value.get('epoch') != list(epoch) or value.get('ownerId') != binding['owner_id']
                or value.get('characterSyncId') != binding['character_uid'] or value.get('serviceUrl') != binding['service_url']):
            return None
        return {'operationId': value['operationId'], 'document': value['document'],
                'nativeEntries': value.get('nativeEntries', value['document']['entries'])}

    def stage_remote(self, binding, remote, epoch, expected_operation, session_fingerprint):
        from utils.file_utils import atomic_write_json
        current = self.read_local(binding, epoch)
        if ((current['operationId'] if current else None) != expected_operation
                or (current and current['document']['completedAt'] > remote['document']['completedAt'])):
            return False
        atomic_write_json(self.incoming_path(binding['character_uid']), {
            'binding': binding, 'epoch': list(epoch), 'head': remote, 'sessionFingerprint': session_fingerprint,
        }, ensure_ascii=False)
        return True

    def needs_reauthentication(self, binding, epoch, session_fingerprint):
        try:
            pending = json.loads(self.incoming_path(binding['character_uid']).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        return (pending.get('sessionFingerprint') != session_fingerprint or pending.get('binding') != binding
                or pending.get('epoch') != list(epoch))

    async def flush(self, uid):
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client
        import httpx
        if not self.incoming_path(uid).is_file():
            return False
        try:
            response = await get_internal_http_client().post(
                f'http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/chat-sync/long-term-memory/{uid}', timeout=3)
            return response.status_code == 200 and response.json().get('applied') is True
        except (httpx.HTTPError, ValueError):
            # The committed inbox is retried by a later wake or a context read.
            return False
