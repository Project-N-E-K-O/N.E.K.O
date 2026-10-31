"""Translate private learned memory, without opening original chat databases."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
import sys
from uuid import uuid4

from utils.long_term_memory_protocol import document, head
from utils.recent_memory_protocol import canonical


SUBJECTS = ('master', 'neko', 'relationship')
FILES = {'persona': 'persona.json', 'facts': 'facts.json', 'archive': 'facts_archive.json', 'reflections': 'reflections.json', 'sync': 'long_term_portable.json'}
JOURNAL = '.long_term_apply.json'
ARCHIVES = {'personaArchives': 'persona_archive', 'reflectionArchives': 'reflection_archive'}


class LongTermMemoryRecoveryRequired(RuntimeError):
    def __init__(self):
        super().__init__('long_term_memory_recovery_required')


def require_consistent_views(directory):
    path = Path(directory) / JOURNAL
    if path.exists():
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('phase') == 'closed':
                return
        except (ValueError, OSError, AttributeError):
            pass
        raise LongTermMemoryRecoveryRequired()


def _view_generation(directory):
    try:
        return (Path(directory) / '.long_term_generation').read_text(encoding='utf-8')
    except FileNotFoundError:
        return None


def advance_view_generation(directory):
    """Only snapshot commit and recovery advance this durable read fence."""
    from utils.file_utils import atomic_write_text
    atomic_write_text(Path(directory) / '.long_term_generation', uuid4().hex)


@contextmanager
def consistent_view_read(path):
    """Reject reads spanning a snapshot commit or rollback, even after journal removal."""
    directory = Path(path).parent
    require_consistent_views(directory)
    before = _view_generation(directory)
    def validate():
        require_consistent_views(directory)
        if before != _view_generation(directory):
            raise LongTermMemoryRecoveryRequired()
    try:
        yield
    except Exception:
        validate()
        raise
    else:
        validate()


def managed_path(relative):
    return relative in FILES.values() or relative == 'reflections_archive.json' or bool(
        re.fullmatch(r'(?:persona_archive|reflection_archive)/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9a-f]{8}\.json', relative))


def view_files(views):
    return {**{filename: views[key] for key, filename in FILES.items()},
            **{relative: rows for key in ARCHIVES for relative, rows in views.get(key, {}).items()}}


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
    if value is None:
        return None
    try:
        instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
        try:
            local = instant.astimezone()
        except OSError:
            if sys.platform != 'win32':
                raise
            from win32timezone import TimeZoneInfo
            local = instant.astimezone(TimeZoneInfo.local())
        return local.replace(tzinfo=None).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def merge_fact_source_evidence(previous, incoming):
    """Keep complete local proof; a bounded snapshot is not an evidence deletion."""
    old_ids = previous.get('source_message_ids', [])
    new_ids = incoming.get('source_message_ids', [])
    times = []
    for value in (previous.get('source_created_at') if old_ids else None, incoming.get('source_created_at')):
        try:
            times.append(timestamp(value) if type(value) in (int, float)
                         or isinstance(value, str) and value.strip() else None)
        except (ValueError, OverflowError, OSError):
            times.append(None)
    old_time, new_time = times
    if set(new_ids).issubset(old_ids) and old_time is not None:
        latest = old_time
    elif set(old_ids).issubset(new_ids):
        latest = new_time
    else:
        latest = max(old_time, new_time) if old_time is not None and new_time is not None else None
    return {'source_message_ids': list(dict.fromkeys([*old_ids, *new_ids])),
            **({'source_created_at': iso(latest)} if latest is not None else {})}


def private(entry):
    return not isinstance(entry, dict) or not (entry.get('scope') or entry.get('subject_kind'))


def _entry_id(subject, native):
    return native.get('id') or 'legacy_' + hashlib.sha256(f'{subject}:{native.get("text")}'.encode()).hexdigest()[:24]


def read_views(directory, *, recovering=False):
    if not recovering:
        require_consistent_views(directory)
    result = {}
    for key, filename in FILES.items():
        path = Path(directory) / filename
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            value = {} if key in ('persona', 'sync') else []
        if key == 'sync' and value == []: value = {}
        if not isinstance(value, dict if key in ('persona', 'sync') else list):
            raise ValueError('invalid_long_term_memory')
        result[key] = value
    from memory.archive_shards import _list_shard_files
    for key, folder in ARCHIVES.items():
        result[key] = {}
        paths = [f'{folder}/{filename}' for filename, _, _ in _list_shard_files(str(Path(directory) / folder))]
        if key == 'reflectionArchives' and (Path(directory) / 'reflections_archive.json').exists():
            paths.append('reflections_archive.json')
        for relative in paths:
            rows = json.loads((Path(directory) / relative).read_text(encoding='utf-8'))
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('invalid_long_term_archive')
            result[key][relative] = rows
    # Older persona shards omit their subject; the durable archive event owns it.
    unresolved = {(relative.rsplit('/', 1)[-1], row.get('id')) for relative, rows in result['personaArchives'].items()
                  for row in rows if not row.get('entity_key')}
    subjects = {}
    if unresolved:
        try:
            with (Path(directory) / 'events.ndjson').open(encoding='utf-8') as events:
                for line in events:
                    event = json.loads(line)
                    payload = event.get('payload') or {}
                    identity = (payload.get('archive_shard_path'), payload.get('entry_id'))
                    if identity in unresolved and event.get('type') == 'persona.fact_added' and payload.get('entity_key'):
                        subjects[identity] = payload['entity_key']
        except FileNotFoundError:
            pass
        for relative, rows in result['personaArchives'].items():
            for row in rows:
                if not row.get('entity_key'):
                    subject = subjects.get((relative.rsplit('/', 1)[-1], row.get('id')))
                    if subject is None and private(row):
                        raise ValueError('long_term_archive_subject_unavailable')
                    if subject is not None:
                        row['entity_key'] = subject
    return result


def export_document(views, completed_at, *, source_evidence=None):
    if any(isinstance(row, dict) and private(row) and row.get('entity') in SUBJECTS
           and row.get('_near_dup_pending') for row in views.get('facts', [])):
        raise ValueError('long_term_memory_arbitration_pending')
    rows = {}
    arbitrated_facts = {}

    def add(kind, subject, value, *, archived=False):
        if not private(value) or subject not in SUBJECTS:
            return
        native = value if isinstance(value, dict) else {'text': str(value)}
        text = native.get('text')
        if not isinstance(text, str) or not text.strip():
            return
        key = _entry_id(subject, native)
        metadata = {**deepcopy(native.get('_sync_metadata') or {}), 'sourceSyncId': native.get('source_sync_id') or f'desktop:{key}'[:200],
                    'createdAt': timestamp(native.get('created_at')), 'updatedAt': timestamp(native.get('updated_at') or native.get('created_at'))}
        if kind == 'fact':
            metadata.pop('sourceCreatedAt', None)
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
        for original, portable in [('event_start_at', 'eventStartAt'), ('event_end_at', 'eventEndAt'),
                                  ('source_created_at', 'sourceCreatedAt'), ('archived_at', 'archivedAt'),
                                  ('voided_at', 'voidedAt'), ('confirmed_at', 'confirmedAt')]:
            if native.get(original) is not None: metadata[portable] = timestamp(native[original])
        for original, portable in [('event_when', 'eventWhen'), ('status', 'status'), ('relation_type', 'relationType'),
                                  ('temporal_scope', 'temporalScope')]:
            if native.get(original) is not None: metadata[portable] = deepcopy(native[original])
        if native.get('version_history') is not None:
            history = []
            for item in native['version_history'][-5:]:
                reason = item.get('reason', 'refine_modify')
                known = reason in ('correction', 'refine_merge', 'refine_split', 'refine_modify', 'refine_discard')
                history.append({'text': item['text'], 'replacedAt': timestamp(item.get('replaced_at', item.get('replacedAt'))),
                                'reason': reason if known else 'refine_modify',
                                **({'detail': item['detail']} if 'detail' in item else
                                   {'detail': reason[:300]} if not known else {})})
            metadata['versionHistory'] = history
        if archived:
            metadata['archived'] = True
        if kind == 'fact':
            sources = native.get('source_message_ids') or []
            if len(sources) > 200:
                role = 'ai' if native.get('source') == 'ai_disclosure' else 'human'
                def source_time(key):
                    original = (source_evidence or {}).get(key)
                    return original[1] if original and original[0] == role and type(original[1]) in (int, float) else None
                # Unknown chronology fills remaining slots; it never displaces newer verified evidence.
                selected = sorted(sources, key=lambda key: source_time(key) if source_time(key) is not None else float('-inf'))[-200:]
                metadata['sourceMessageIds'] = selected
                times = [source_time(key) for key in selected]
                if all(value is not None for value in times): metadata['sourceCreatedAt'] = max(times)
                else: metadata.pop('sourceCreatedAt', None)
            if native.get('source') in ('user_observation', 'ai_disclosure', 'chat'): metadata['source'] = native['source']
            metadata.update(importance=native.get('importance', 5), absorbed=bool(native.get('absorbed')), archived=archived)
            if native.get('arbitration_archived_at'):
                metadata.update(status='superseded' if native.get('superseded_by') else 'retracted',
                                archivedAt=timestamp(native['arbitration_archived_at']))
                if native.get('superseded_by'):
                    metadata['supersededById'] = str(native['superseded_by'])
            elif native.get('arbitration_restored_at'):
                for field in ('status', 'archivedAt', 'supersededById', 'arbitrationAction'):
                    metadata.pop(field, None)
            if native.get('event_start_at') and ('eventAt' in metadata or not native.get('_sync_metadata')):
                metadata['eventAt'] = timestamp(native['event_start_at'])
        if kind == 'reflection':
            if native.get('status') not in ('pending', 'confirmed', 'promoted', 'promote_blocked', 'merged', 'denied', 'archived'):
                return
            metadata['sourceFactIds'] = list(native.get('source_fact_ids') or [])
            metadata['evidenceReview'] = native.get('_sync_metadata', {}).get('evidenceReview', 'supported')
            if native.get('confirmed_at'):
                metadata['evidenceReviewedAt'] = timestamp(native['confirmed_at'])
            if native.get('promoted_at'):
                metadata['promotedAt'] = timestamp(native['promoted_at'])
        identity = (kind, subject, key)
        if kind == 'fact':
            if archived and native.get('arbitration_archived_at'):
                arbitrated_facts[identity] = native.get('arbitration_restored_at')
            elif not archived and identity in arbitrated_facts:
                # Two-file commits can leave both copies. An explicit restore changes
                # the native restore marker even when the wall clock moves backwards.
                restored = native.get('arbitration_restored_at')
                if not restored or restored == arbitrated_facts[identity]:
                    return
        if identity not in rows or not archived or (kind == 'fact' and native.get('arbitration_archived_at')):
            rows[identity] = {'kind': kind, 'subject': subject, 'id': key, 'text': text, 'metadata': metadata}

    for key, kind, subject_key in [('personaArchives', 'persona', 'entity_key'), ('reflectionArchives', 'reflection', 'entity')]:
        for values in views.get(key, {}).values():
            for value in values:
                add(kind, value.get(subject_key), value, archived=True)
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
    state = views.get('sync') or {}
    for entry in state.get('dormantEntries', []):
        rows.setdefault((entry['kind'], entry['subject'], entry['id']), deepcopy(entry))
    return document({'schemaVersion': 2, 'completedAt': completed_at, 'entries': [rows[key] for key in sorted(rows)],
                     'evidence': deepcopy(state.get('evidence') or {'retirements': [], 'confirmations': []})})


def import_document(incoming, existing, *, baseline=None, source_evidence=None):
    incoming = document(incoming)
    result = deepcopy(existing)
    result['sync'] = {'evidence': deepcopy(incoming.get('evidence') or existing.get('sync', {}).get('evidence') or {'retirements': [], 'confirmations': []}), 'dormantEntries': []}
    for field, key in [('retirements', 'id'), ('confirmations', 'fingerprint')]:
        retained = {row[key]: deepcopy(row) for row in existing.get('sync', {}).get('evidence', {}).get(field, [])}
        for incoming_row in result['sync']['evidence'][field]:
            row = deepcopy(incoming_row)
            previous = retained.get(row[key])
            if field == 'retirements' and previous:
                old_proof = bool(previous.get('retiredBySourceMessageIds') or previous.get('retiredThroughAtFromCorrection'))
                new_proof = bool(row.get('retiredBySourceMessageIds') or row.get('retiredThroughAtFromCorrection'))
                times = [item['retiredThroughAt'] for item, proof in [(previous, old_proof), (row, new_proof)]
                         if 'retiredThroughAt' in item and (proof or not (old_proof or new_proof))]
                row = {**previous, **row, 'sourceMessageIds': list(dict.fromkeys(previous['sourceMessageIds'] + row['sourceMessageIds']))}
                corrections = list(dict.fromkeys(previous.get('retiredBySourceMessageIds', []) + row.get('retiredBySourceMessageIds', [])))
                if corrections: row['retiredBySourceMessageIds'] = corrections
                if times:
                    row['retiredThroughAt'] = max(times)
                    if old_proof or new_proof: row['retiredThroughAtFromCorrection'] = True
                else:
                    row.pop('retiredThroughAt', None)
                    row.pop('retiredThroughAtFromCorrection', None)
            retained[row[key]] = row
        result['sync']['evidence'][field] = list(retained.values())
    local_persona = {(subject, _entry_id(subject, row)): row for subject in SUBJECTS
                     for row in existing['persona'].get(subject, {}).get('facts', [])
                     if isinstance(row, dict) and private(row) and row.get('source') != 'character_card'}
    local_facts = {(row.get('entity'), _entry_id(row.get('entity'), row)): row
                   for row in existing['archive'] + existing['facts'] if isinstance(row, dict) and private(row)}
    pending = set()
    if baseline is not None:
        previous = {(row['kind'], row['subject'], row['id']): row for row in baseline}
        current = {(row['kind'], row['subject'], row['id']): row
                   for row in export_document(existing, incoming['completedAt'], source_evidence=source_evidence)['entries']}
        pending = {key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)}
    result['sync']['dormantEntries'] = [deepcopy(row) for row in existing.get('sync', {}).get('dormantEntries', [])
                                      if (row['kind'], row['subject'], row['id']) in pending]

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
    for key, kind, subject_key in [('personaArchives', 'persona', 'entity_key'), ('reflectionArchives', 'reflection', 'entity')]:
        result[key] = {relative: [row for row in rows if not private(row) or row.get(subject_key) not in SUBJECTS
                      or row.get('source') == 'character_card' or retained(kind, row.get(subject_key), row)]
                      for relative, rows in result.get(key, {}).items()}
    incoming_reflections = {(row['subject'], row['id']) for row in incoming['entries'] if row['kind'] == 'reflection'}
    result['reflections'] = [row for row in result['reflections'] if isinstance(row, dict)
        and (not private(row) or row.get('entity') not in SUBJECTS or retained('reflection', row.get('entity'), row)
             or (incoming['schemaVersion'] == 1 and row.get('status') not in ('confirmed', 'promoted')
                 and (not isinstance(row.get('id'), str)
                      or (row.get('entity'), row.get('id')) not in incoming_reflections)))]
    for row in incoming['entries']:
        kind, subject, meta = row['kind'], row['subject'], row['metadata']
        if (kind, subject, row['id']) in pending:
            continue
        formally_archived = kind != 'fact' and (meta.get('archivedAt') is not None or meta.get('archived'))
        arbitrated = kind == 'fact' and meta.get('archived') and meta.get('archivedAt') is not None and meta.get('status') in ('retracted', 'superseded')
        if not formally_archived and not arbitrated and (meta.get('voidedAt') is not None or meta.get('status') in ('denied', 'retracted', 'superseded', 'merged')):
            result['sync']['dormantEntries'].append(deepcopy(row))
            continue
        native = {'_sync_metadata': deepcopy(meta), 'id': row['id'], 'text': row['text'], 'source_sync_id': meta.get('sourceSyncId') or f'snapshot:{row["id"]}'[:200]}
        for original, portable in [('created_at', 'createdAt'), ('updated_at', 'updatedAt'),
                                   ('rein_last_signal_at', 'reinLastSignalAt'), ('disp_last_signal_at', 'dispLastSignalAt')]:
            if meta.get(portable) is not None:
                native[original] = iso(meta[portable])
        for original, portable in EVIDENCE_FIELDS.items():
            if portable in meta and not portable.endswith('At'):
                native[original] = meta[portable]
        for original, portable in [('source_message_ids', 'sourceMessageIds'), ('source_fact_ids', 'sourceFactIds')]:
            if meta.get(portable):
                native[original] = list(meta[portable])
        for original, portable in [('event_start_at', 'eventStartAt'), ('event_end_at', 'eventEndAt'), ('source_created_at', 'sourceCreatedAt'),
                                  ('confirmed_at', 'confirmedAt'), ('archived_at', 'archivedAt'), ('voided_at', 'voidedAt')]:
            if portable in meta: native[original] = iso(meta[portable])
        for original, portable in [('event_when', 'eventWhen'), ('relation_type', 'relationType'), ('temporal_scope', 'temporalScope')]:
            if portable in meta: native[original] = deepcopy(meta[portable])
        if kind == 'persona':
            if 'versionHistory' in meta:
                native['version_history'] = [
                    {'text': item['text'], 'replaced_at': iso(item['replacedAt']), 'reason': item['reason'],
                     **({'detail': item['detail']} if 'detail' in item else {})}
                    for item in meta['versionHistory']]
            sources = meta.get('sourceReflectionIds') or []
            native.update(source='reflection' if sources else 'manual', source_id=sources[0] if sources else native['source_sync_id'],
                          merged_from_ids=list(sources[1:]))
            previous = local_persona.get((subject, row['id']), {})
            if 'protected' in previous:
                native.setdefault('protected', previous['protected'])
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
            if formally_archived:
                native['entity_key'] = subject
                _append_archive(result, 'personaArchives', native, incoming['completedAt'])
            else:
                result['persona'][subject]['facts'].append(native)
        elif kind == 'fact':
            native.update(entity=subject, source=meta.get('source', 'ai_disclosure' if subject == 'neko' else 'user_observation'),
                          importance=meta.get('importance', 5), absorbed=bool(meta.get('absorbed')), signal_processed=True,
                          hash=hashlib.sha256(row['text'].encode()).hexdigest()[:16])
            previous = local_facts.get((subject, row['id']), {})
            if previous.get('text') == native['text'] and previous.get('external_import'):
                native['external_import'] = deepcopy(previous['external_import'])
                if previous.get('hash'):
                    native['hash'] = previous['hash']
            if (previous.get('text') == native['text'] and previous.get('source_message_ids')
                    and (previous.get('source', 'user_observation') == 'ai_disclosure') == (native['source'] == 'ai_disclosure')):
                proof = merge_fact_source_evidence(previous, native)
                native.pop('source_created_at', None)
                native.update(proof)
            if arbitrated:
                native.update(arbitration_archived_at=iso(meta['archivedAt']), arbitration_reason='dedup_arbitration')
                if meta.get('supersededById'):
                    native['superseded_by'] = meta['supersededById']
            if meta.get('eventAt') is not None:
                native['event_start_at'] = iso(meta['eventAt'])
            result['archive' if meta.get('archived') else 'facts'].append(native)
        else:
            native.update(entity=subject, source_fact_ids=list(meta.get('sourceFactIds') or []),
                          status=meta.get('status', 'promoted' if meta.get('promotedAt') else 'confirmed'))
            if native['status'] in ('confirmed', 'promoted') and not native.get('confirmed_at'):
                native['confirmed_at'] = iso(meta.get('evidenceReviewedAt', incoming['completedAt']))
            if meta.get('promotedAt'):
                native['promoted_at'] = iso(meta['promotedAt'])
            if formally_archived:
                _append_archive(result, 'reflectionArchives', native, incoming['completedAt'])
            else:
                result['reflections'].append(native)
    return result



def _append_archive(views, key, entry, completed):
    from config import ARCHIVE_FILE_MAX_ENTRIES
    from memory.archive_shards import shard_filename_for
    archived_at = entry.get('archived_at') or iso(completed)
    entry['archived_at'] = archived_at
    date = datetime.fromisoformat(archived_at)
    folder = ARCHIVES[key]
    shards = views.setdefault(key, {})
    relative = next((path for path, rows in shards.items()
                     if path.startswith(f'{folder}/{date:%Y-%m-%d}_') and len(rows) < ARCHIVE_FILE_MAX_ENTRIES), None)
    if relative is None:
        relative = f'{folder}/{shard_filename_for(date)}'
        while relative in shards:
            relative = f'{folder}/{shard_filename_for(date)}'
    entry['archive_shard_path'] = relative.rsplit('/', 1)[-1]
    shards.setdefault(relative, []).append(entry)


def retirement_evidence(directory):
    require_consistent_views(directory)
    try:
        state = json.loads((Path(directory) / FILES['sync']).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    if state == []:
        return []
    if not isinstance(state, dict):
        raise ValueError('invalid_long_term_memory')
    dormant = {(row['subject'], row['id']): row['text'] for row in state.get('dormantEntries', []) if row['kind'] == 'fact'}
    return [{**row, **({'text': dormant[(row['subject'], row['id'])]}
                      if not row.get('text') and (row['subject'], row['id']) in dormant else {})}
            for row in state.get('evidence', {}).get('retirements', [])]


def retirement_source_prompt(directory, messages, formatted, names):
    if not messages:
        return formatted, ''
    from memory.facts import FactStore
    # Exactly one source position per stored message, including multiline bodies.
    numbered = '\n'.join(f'[SOURCE {index}] {message.type} ({names.get(message.type, message.type)}) | {FactStore._flatten_message_content(message.content)}'
                         for index, message in enumerate(messages, 1))
    return numbered, ('\nFor each extracted fact include source_indices: a nonempty array of 1-based SOURCE message indices '
                      'that directly support this assertion. Return [] when there are no new facts. '
                      'Resolve first-person statements using the SOURCE speaker, not names mentioned in the text. '
                      'A character acknowledging or repeating a user fact does not make it a fact about the character. '
                      'Do not infer sources from prior memory or invent source identities or timestamps.')


class FactEvidenceUnavailable(RuntimeError):
    """Trusted originals are incomplete; retry without spending model failure budget."""


def original_source_time(value):
    """Parse supported original times; unknown or malformed values stay unknown."""
    if not isinstance(value, str):
        return None
    if value.isascii() and value.isdecimal() and len(value) <= 16:
        at = int(value)
        if at > 9_007_199_254_740_991:
            return None
    elif re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,3})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', value):
        try:
            at = timestamp(value)
        except (ValueError, OverflowError, OSError):
            return None
    else:
        return None
    return at if iso(at) is not None else None


def trusted_message_source(message, fallback_timestamp=None):
    from utils.llm_client import message_metadata
    metadata = getattr(message, 'additional_kwargs', {})
    identity, at = metadata.get('memory_source_id'), metadata.get('memory_source_created_at')
    original = message_metadata(message)
    source_proof = original.get('chat_original_source')
    source_proof = source_proof if isinstance(source_proof, dict) else {}
    if not identity:
        raw_id = original.get('recent_memory_id') or source_proof.get('recordId') or source_proof.get('messageId')
        if isinstance(raw_id, str) and re.fullmatch(r'[0-9a-f]{32}', raw_id):
            identity = f'sync:{raw_id}'
        raw_time = original.get('recent_memory_time', source_proof.get('originalTime'))
        at = original_source_time(raw_time)
        if ('recent_memory_time' not in original and 'originalTime' not in source_proof
                and (type(fallback_timestamp) in (int, float) or isinstance(fallback_timestamp, datetime)
                     or isinstance(fallback_timestamp, str) and bool(fallback_timestamp))):
            try:
                at = timestamp(fallback_timestamp.isoformat() if isinstance(fallback_timestamp, datetime) else fallback_timestamp)
            except (ValueError, OverflowError, OSError):
                at = None
    return (identity if isinstance(identity, str) and identity else None,
            at if type(at) in (int, float) and iso(at) is not None else None)


def original_message_source(stored, session_id, stored_at):
    """Use persisted metadata only; legacy identities match the extraction projection."""
    identity, at = trusted_message_source(stored, stored_at)
    if identity is None:
        identity = 'desktop:' + hashlib.sha256(json.dumps(
            [str(session_id), str(stored_at), stored], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return identity, at


async def resolve_retirement_cutoffs(retirements, messages, lookup=None):
    """Resolve correction clocks without writing; the caller rechecks under its persist lock."""
    unresolved = [row for row in retirements if row.get('retiredThroughAt') is None and row.get('retiredBySourceMessageIds')]
    if not unresolved:
        return retirements
    from memory.message_sources import is_theater_memory_message
    known = {}
    for message in messages or []:
        if getattr(message, 'type', '') not in ('human', 'ai') or is_theater_memory_message(message):
            continue
        identity, at = trusted_message_source(message)
        if identity and at is not None:
            known.setdefault(identity, set()).add((message.type, at))
    wanted = {identity for row in unresolved for identity in row['retiredBySourceMessageIds']}
    missing = wanted - known.keys()
    if missing and lookup is not None:
        for identity, (role, at) in (await lookup(sorted(missing))).items():
            if identity in wanted and role in ('human', 'ai') and type(at) in (int, float) and iso(at) is not None:
                known.setdefault(identity, set()).add((role, at))
    updates = {}
    for row in unresolved:
        ids = row['retiredBySourceMessageIds']
        if all(len(known.get(identity, ())) == 1 for identity in ids):
            expected_role = 'ai' if row['subject'] == 'neko' else 'human'
            times = [at for identity in ids for role, at in known[identity] if role == expected_role]
            if times:
                updates[row['id']] = max(times)
    if not updates:
        return retirements

    return [{**row, **({'retiredThroughAt': updates[row['id']], 'retiredThroughAtFromCorrection': True}
                     if row['id'] in updates else {})} for row in retirements]


def _assertion_text(text):
    # Meaningful punctuation and case differences require semantic arbitration.
    return text.strip()


async def verify_retired_assertions(retirements, extracted, messages, request, *, default_source):
    """Use existing arbitration semantics before writes; never discard a candidate tail."""
    from config import EVIDENCE_EXTRACT_FACTS_MODEL_TIER
    from config.prompts.prompts_memory import get_fact_dedup_prompt
    from memory.fact_dedup import FACT_DEDUP_BATCH_LIMIT, _detect_fact_dedup_prompt_language
    from memory.facts import FactExtractionFailed
    from utils.language_utils import get_global_language_full
    from utils.tokenize import acount_tokens
    matched, batch = {}, []

    async def review(pairs):
        lines = '\n'.join(f'[{index}] candidate: {json.dumps(fact["text"], ensure_ascii=False)} | existing: {json.dumps(row["text"], ensure_ascii=False)}'
                          for index, (fact, row) in enumerate(pairs))
        prompt = get_fact_dedup_prompt(_detect_fact_dedup_prompt_language(lines, ui_language=get_global_language_full()))
        prompt = prompt.replace('{COUNT}', str(len(pairs))).replace('{PAIRS}', lines)
        if await acount_tokens(prompt) > 8000:
            if len(pairs) == 1:
                raise FactExtractionFailed('retired_assertion_context_exceeds_budget')
            middle = len(pairs) // 2
            await review(pairs[:middle])
            await review(pairs[middle:])
            return
        decisions = await request(prompt, tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER, call_type='memory_fact_dedup', parse_max_retries=1)
        if not isinstance(decisions, list) or len(decisions) != len(pairs):
            raise FactExtractionFailed('retired_assertion_review_failed')
        seen = set()
        for decision in decisions:
            if (not isinstance(decision, dict) or type(decision.get('index')) is not int
                    or not 0 <= decision['index'] < len(pairs) or decision['index'] in seen
                    or decision.get('action') not in ('merge', 'replace', 'keep_both')):
                raise FactExtractionFailed('retired_assertion_review_failed')
            seen.add(decision['index'])
            if decision['action'] != 'keep_both':
                fact, row = pairs[decision['index']]
                matched.setdefault(id(fact), set()).add(row['id'])

    now = timestamp(datetime.now().isoformat())
    for fact in extracted:
        if not isinstance(fact, dict) or not isinstance(fact.get('text'), str):
            continue
        source = fact.get('source', default_source)
        proof = fact_source_evidence([], fact, fact.get('entity', 'master'), messages, source=source)
        if proof is None:
            continue
        ids = set(proof.get('source_message_ids', []))
        latest = timestamp(proof['source_created_at']) if proof.get('source_created_at') is not None else None
        if not ids:
            continue
        for row in retirements:
            if (row.get('subject') != fact.get('entity', 'master') or not isinstance(row.get('text'), str)
                    or _assertion_text(row['text']) == _assertion_text(fact['text'])):
                continue
            cutoff = row.get('retiredThroughAt')
            if (latest is not None and not ids.issubset(row.get('sourceMessageIds', []))
                    and not (cutoff is not None and cutoff <= now and latest <= cutoff)
                    and not (cutoff is None and row.get('retiredBySourceMessageIds'))):
                continue
            batch.append((fact, row))
            if len(batch) == FACT_DEDUP_BATCH_LIMIT:
                await review(batch)
                batch = []
    if batch:
        await review(batch)
    return matched


def fact_source_evidence(retirements, fact, entity, source_messages, *, source='user_observation', matched_ids=()):
    """Return trusted provenance, None for retired replay, or fail for unresolved evidence."""
    from memory.facts import FactExtractionFailed
    matching = [row for row in retirements if row.get('subject') == entity and isinstance(row.get('text'), str)
                and (row['id'] in matched_ids or _assertion_text(row['text']) == _assertion_text(fact.get('text', '')))]
    indices = fact.get('source_indices')
    valid = isinstance(indices, list) and bool(indices) and source_messages and all(
        type(index) is int and 1 <= index <= len(source_messages) for index in indices)
    selected = [source_messages[index - 1] for index in dict.fromkeys(indices)] if valid else []
    proofs = []
    for message in selected:
        identity, at = trusted_message_source(message)
        if not identity:
            proofs = []
            break
        if source == 'user_observation' and getattr(message, 'type', '') not in ('human', 'user'):
            proofs = []
            break
        proofs.append((identity, at))
    if matching and not proofs:
        if any(trusted_message_source(message)[0] for message in source_messages or []):
            raise FactExtractionFailed('retired_assertion_source_unavailable')
        return None
    if not proofs and any(trusted_message_source(message)[0] for message in source_messages or []):
        return None
    if not proofs:
        return {}
    ids = [identity for identity, _ in proofs]
    latest = max(at for _, at in proofs) if all(at is not None for _, at in proofs) else None
    for row in matching:
        # Explicit disjoint event ranges describe independent occurrences.
        from memory.temporal import compute_event_timestamps, normalize_event_when
        start, end = compute_event_timestamps(normalize_event_when(fact.get('event_when')), iso(latest), fallback_start=False, fallback_end=False) if latest is not None else (None, None)
        if start is not None and row.get('eventEndAt') is not None and timestamp(start) > row['eventEndAt']:
            continue
        if end is not None and row.get('eventStartAt') is not None and timestamp(end) < row['eventStartAt']:
            continue
        if set(ids).issubset(row.get('sourceMessageIds', [])):
            return None
        cutoff = row.get('retiredThroughAt')
        if cutoff is None and row.get('retiredBySourceMessageIds'):
            # A missing correction timestamp is a retryable failure, never processing time.
            raise FactEvidenceUnavailable('retired_correction_time_unavailable')
        if cutoff is not None and latest is None:
            raise FactEvidenceUnavailable('retired_assertion_source_time_unavailable')
        if cutoff is not None and cutoff <= timestamp(datetime.now().isoformat()) and latest <= cutoff:
            return None
    return {'source_message_ids': ids, **({'source_created_at': iso(latest)} if latest is not None else {})}

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

    def expire_incoming(self, binding, epoch, expired, *, require_epoch):
        """Retain obsolete inbox bytes outside the live slot after an authorized expiry."""
        from uuid import uuid4
        require_epoch(epoch)
        path = self.incoming_path(binding['character_uid'])
        try:
            pending = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        saved = pending['binding']
        incoming = head(pending['head'], saved['owner_id'], binding['character_uid'])
        if (saved['owner_id'] == binding['owner_id'] and saved['service_url'] == binding['service_url']
                and incoming['revision'] > expired['revision']):
            return
        path.rename(path.with_name(f'{binding["character_uid"]}.expired-{uuid4().hex}.json'))

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
