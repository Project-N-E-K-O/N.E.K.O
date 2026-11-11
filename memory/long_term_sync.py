"""Translate private learned memory, without opening original chat databases."""

from contextlib import contextmanager
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
import sys
from uuid import uuid4

from memory.evidence import count_fact_sources
from memory.introspection import PERMANENT_SUPPRESSION_KEY, is_permanently_suppressed
from memory.long_term_merge import empty_evidence, merge_retirement
from utils.long_term_memory_protocol import MAX_ENTRIES, SCHEMA_VERSION, document, head
from utils.recent_memory_protocol import canonical


SUBJECTS = ('master', 'neko', 'relationship')
FILES = {'persona': 'persona.json', 'facts': 'facts.json', 'archive': 'facts_archive.json', 'reflections': 'reflections.json', 'sync': 'long_term_portable.json'}
JOURNAL = '.long_term_apply.json'
ARCHIVES = {'personaArchives': 'persona_archive', 'reflectionArchives': 'reflection_archive'}


class LongTermMemoryRecoveryRequired(RuntimeError):
    def __init__(self):
        super().__init__('long_term_memory_recovery_required')


class LongTermMemoryCapacityExceeded(ValueError):
    """The full snapshot cannot fit without discarding learned-memory records."""

    def __init__(self):
        super().__init__('long_term_memory_capacity_exceeded')


class LongTermArchiveSubjectUnavailable(ValueError):
    """A legacy archive has no unambiguous subject evidence left locally."""

    def __init__(self):
        super().__init__('long_term_archive_subject_unavailable')


class RetirementSubjectConflict(ValueError):
    """A snapshot assigns an existing retirement identifier to another subject."""

    def __init__(self):
        super().__init__('invalid_long_term_memory')


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
    before = _view_generation(directory)
    require_consistent_views(directory)
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


def read_views(directory, *, recovering=False, repair_archive=None):
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
                  for row in rows if private(row) and not row.get('entity_key')}
    subjects = {}
    if unresolved:
        copies = Counter((relative.rsplit('/', 1)[-1], row.get('id')) for relative, rows in result['personaArchives'].items()
                         for row in rows if private(row))
        try:
            with (Path(directory) / 'events.ndjson').open(encoding='utf-8') as events:
                for line in events:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict) or not isinstance(event.get('payload'), dict):
                        continue
                    payload = event.get('payload') or {}
                    if not isinstance(payload.get('archive_shard_path'), str) or not isinstance(payload.get('entry_id'), str):
                        continue
                    identity = (payload.get('archive_shard_path'), payload.get('entry_id'))
                    if identity in unresolved and event.get('type') == 'persona.fact_added' and payload.get('entity_key') in SUBJECTS:
                        snapshot = payload.get('entry_snapshot')
                        stamp = payload.get('archived_at') or (snapshot.get('archived_at') if isinstance(snapshot, dict) else None)
                        if stamp is not None and not isinstance(stamp, str):
                            continue
                        subjects.setdefault(identity, set()).add((stamp, payload['entity_key']))
        except FileNotFoundError:
            pass
        unavailable = False
        for relative, rows in result['personaArchives'].items():
            repaired = False
            for row in rows:
                if private(row) and not row.get('entity_key'):
                    identity = (relative.rsplit('/', 1)[-1], row.get('id'))
                    candidates = subjects.get(identity, ())
                    matched = {subject for stamp, subject in candidates if stamp and stamp == row.get('archived_at')}
                    if not matched and copies[identity] == 1 and all(not stamp for stamp, _ in candidates):
                        matched = {subject for _, subject in candidates}
                    if len(matched) != 1:
                        unavailable = True
                    else:
                        row['entity_key'] = matched.pop()
                        repaired = True
            if repaired and repair_archive is not None:
                repair_archive(relative, rows)
        if unavailable:
            raise LongTermArchiveSubjectUnavailable()
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
            # The native row owns its proof: a copy kept from an earlier import
            # must not republish citations this device has since dropped.
            metadata.pop('sourceCreatedAt', None)
            metadata.pop('sourceMessageIds', None)
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
        for original, portable in [('event_when_raw', 'eventWhen'), ('status', 'status'), ('relation_type', 'relationType'),
                                  ('temporal_scope', 'temporalScope')]:
            if native.get(original) is not None: metadata[portable] = deepcopy(native[original])
        if kind != 'persona' and _schema_current(native):
            # An explicit null tells the receiver this row was already examined for
            # event time, so it does not ask a model to examine it again.
            metadata.setdefault('temporalScope' if kind == 'reflection' else 'eventWhen', None)
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
            sources = list(dict.fromkeys(native.get('source_message_ids') or []))
            if sources:
                metadata['sourceMessageIds'] = sources
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
            # Rows written before the marker existed were never queued for judging.
            metadata.update(importance=native.get('importance', 5), absorbed=bool(native.get('absorbed')), archived=archived,
                            signalJudged=bool(native.get('signal_processed', True)))
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
            if native.get('status') not in ('pending', 'confirmed', 'promoted', 'promote_blocked', 'merged', 'denied', 'archived', 'superseded', 'retracted'):
                return
            metadata['sourceFactIds'] = list(native.get('source_fact_ids') or [])
            metadata['evidenceReview'] = native.get('_sync_metadata', {}).get('evidenceReview', 'supported')
            if native.get('confirmed_at'):
                metadata['evidenceReviewedAt'] = timestamp(native['confirmed_at'])
            if native.get('promoted_at'):
                metadata['promotedAt'] = timestamp(native['promoted_at'])
        if kind in ('persona', 'reflection'):
            # What a receiver needs to keep the "backed by the user" gate: explicit
            # confirmations, which facts were counted, and the counts themselves for
            # when those facts are not in the snapshot.
            confirms = native.get('user_confirm_count')
            if type(confirms) is int and confirms >= 0:
                metadata['userConfirmCount'] = confirms
            counted = native.get('counted_fact_ids')
            if isinstance(counted, list):
                metadata['countedFactIds'] = [fid for fid in dict.fromkeys(counted) if isinstance(fid, str) and fid.strip()][:200]
            counts = native.get('fact_source_counts')
            if isinstance(counts, dict) and all(type(counts.get(key)) is int and counts[key] >= 0 for key in ('user', 'ai')):
                metadata['factSourceCounts'] = {'user': counts['user'], 'ai': counts['ai']}
        if kind in ('persona', 'reflection') and is_permanently_suppressed(native):
            # Retired by introspection on this device: peers drop it instead
            # of injecting it, and this device keeps its own row (see import).
            metadata['status'] = 'superseded'
            marker = native[PERMANENT_SUPPRESSION_KEY]
            if isinstance(marker, dict) and marker.get('merged_into'):
                metadata['supersededById'] = str(marker['merged_into'])[:200]
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
    if len(rows) > MAX_ENTRIES:
        raise LongTermMemoryCapacityExceeded()
    progress = state.get('progress') or {}
    exported = {'schemaVersion': SCHEMA_VERSION, 'completedAt': completed_at, 'entries': [rows[key] for key in sorted(rows)],
                'evidence': deepcopy(state.get('evidence') or empty_evidence()),
                'progress': {name: list(progress.get(name) or []) for name in ('a', 'b')}}
    for field in ('organizer', 'maintenance', 'lineage'):
        if state.get(field):
            exported[field] = deepcopy(state[field])
    return document(exported)


def _schema_current(native):
    from config import MEMORY_SCHEMA_VERSION_CURRENT
    return (native.get('schema_version') or 1) >= MEMORY_SCHEMA_VERSION_CURRENT


def validate_retirement_subjects(previous, incoming):
    """Reject incompatible owners of a globally unique retirement identifier."""
    subjects = {}
    for row in [*previous, *incoming]:
        if row['id'] in subjects and subjects[row['id']] != row['subject']:
            raise RetirementSubjectConflict()
        subjects[row['id']] = row['subject']


def import_document(incoming, existing, *, merged=False, source_evidence=None, keep_unpublished_retirements=True):
    """Write a portable document into native views.

    merged says the document already contains what this device holds (the
    result of a three-way merge): rows it leaves exactly as this device exports
    them are kept untouched, and its evidence replaces the local evidence
    instead of being joined with it.
    """
    incoming = document(incoming)
    previous_state = existing.get('sync') or {}
    validate_retirement_subjects(previous_state.get('evidence', {}).get('retirements', []),
                                 incoming.get('evidence', {}).get('retirements', []))
    result = deepcopy(existing)
    result['sync'] = {'evidence': deepcopy(incoming.get('evidence') or previous_state.get('evidence') or empty_evidence()), 'dormantEntries': []}
    current_schema = incoming['schemaVersion'] == SCHEMA_VERSION
    for field in ('progress', 'organizer', 'maintenance', 'lineage'):
        # Older documents know nothing of these, so they cannot clear them.
        value = incoming.get(field) if current_schema else previous_state.get(field)
        if value:
            result['sync'][field] = deepcopy(value)
    if not merged:
        for field, key in [('retirements', 'id'), ('confirmations', 'fingerprint')]:
            retained = {row[key]: deepcopy(row) for row in previous_state.get('evidence', {}).get(field, [])}
            for incoming_row in result['sync']['evidence'][field]:
                row = deepcopy(incoming_row)
                previous = retained.get(row[key])
                if field == 'retirements' and previous:
                    row = merge_retirement(previous, row)
                retained[row[key]] = row
            result['sync']['evidence'][field] = list(retained.values())
    local_persona = {(row.get('entity_key'), _entry_id(row.get('entity_key'), row)): row
                     for rows in existing.get('personaArchives', {}).values() for row in rows
                     if isinstance(row, dict) and private(row) and row.get('source') != 'character_card'}
    local_persona.update({(subject, _entry_id(subject, row)): row for subject in SUBJECTS
                     for row in existing['persona'].get(subject, {}).get('facts', [])
                     if isinstance(row, dict) and private(row) and row.get('source') != 'character_card'})
    local_facts = {(row.get('entity'), _entry_id(row.get('entity'), row)): row
                   for row in existing['archive'] + existing['facts'] if isinstance(row, dict) and private(row)}
    local_reflections = {(row.get('entity'), _entry_id(row.get('entity'), row)): row
                         for rows in existing.get('reflectionArchives', {}).values() for row in rows
                         if isinstance(row, dict) and private(row)}
    local_reflections.update({(row.get('entity'), _entry_id(row.get('entity'), row)): row
                             for row in existing['reflections'] if isinstance(row, dict) and private(row)})
    pending = set()
    if merged:
        # A row the document leaves as this device already exports it stays as it is:
        # rebuilding it would drop what the protocol does not carry.
        current = {(row['kind'], row['subject'], row['id']): row
                   for row in export_document(existing, incoming['completedAt'], source_evidence=source_evidence)['entries']}
        pending = {(row['kind'], row['subject'], row['id']) for row in incoming['entries']
                   if current.get((row['kind'], row['subject'], row['id'])) == row}
    result['sync']['dormantEntries'] = [deepcopy(row) for row in previous_state.get('dormantEntries', [])
                                      if (row['kind'], row['subject'], row['id']) in pending]

    def retained(kind, subject, value):
        native = value if isinstance(value, dict) else {'text': str(value)}
        return (kind, subject, _entry_id(subject, native)) in pending

    # Rows introspection retired here stay as they are: a snapshot carries
    # them as superseded (or, from a peer that has not seen the retirement,
    # live), and neither may replace, duplicate or delete this device's row.
    locally_retired = (
        {('persona', subject, key) for (subject, key), row in local_persona.items()
         if is_permanently_suppressed(row)}
        | {('reflection', subject, key) for (subject, key), row in local_reflections.items()
           if is_permanently_suppressed(row)}
    )
    if not keep_unpublished_retirements:
        # A receipt baseline holds only what the remote already carries: a
        # retirement it does not carry as superseded is unpublished and has
        # to stay a difference, or the next capture never publishes it.
        locally_retired &= {(row['kind'], row['subject'], row['id']) for row in incoming['entries']
                            if row['metadata'].get('status') == 'superseded'}

    def retired(kind, subject, value):
        return isinstance(value, dict) and (kind, subject, _entry_id(subject, value)) in locally_retired

    # Keep static character-card entries and scoped/group partitions on this device.
    for subject in SUBJECTS:
        section = result['persona'].setdefault(subject, {})
        section['facts'] = [row for row in section.get('facts', []) if retained('persona', subject, row)
                            or retired('persona', subject, row)
                            or (isinstance(row, dict) and (not private(row) or row.get('source') == 'character_card'))]
    for field in ('facts', 'archive'):
        result[field] = [row for row in result[field] if isinstance(row, dict)
                         and (not private(row) or row.get('entity') not in SUBJECTS
                              or retained('fact', row.get('entity'), row))]
    for key, kind, subject_key in [('personaArchives', 'persona', 'entity_key'), ('reflectionArchives', 'reflection', 'entity')]:
        result[key] = {relative: [row for row in rows if not private(row) or row.get(subject_key) not in SUBJECTS
                      or row.get('source') == 'character_card' or retained(kind, row.get(subject_key), row)
                      or retired(kind, row.get(subject_key), row)]
                      for relative, rows in result.get(key, {}).items()}
    incoming_reflections = {(row['subject'], row['id']) for row in incoming['entries'] if row['kind'] == 'reflection'}
    result['reflections'] = [row for row in result['reflections'] if isinstance(row, dict)
        and (not private(row) or row.get('entity') not in SUBJECTS or retained('reflection', row.get('entity'), row)
             or retired('reflection', row.get('entity'), row)
             or (incoming['schemaVersion'] == 1 and row.get('status') not in ('confirmed', 'promoted')
                 and (not isinstance(row.get('id'), str)
                      or (row.get('entity'), row.get('id')) not in incoming_reflections)))]
    incoming_fact_sources = {row['id']: _incoming_fact_source(row) for row in incoming['entries'] if row['kind'] == 'fact'}
    withdrawn_facts = {row['id'] for row in incoming['entries']
                       if row['kind'] == 'fact' and row['metadata'].get('status') == 'retracted'}
    # Rows kept untouched stay after this import, so their source is the one that holds.
    incoming_fact_sources.update({row['id']: row.get('source') for row in result['archive'] + result['facts']
                                  if isinstance(row, dict) and row.get('id') and private(row)})
    for row in incoming['entries']:
        kind, subject, meta = row['kind'], row['subject'], row['metadata']
        if (kind, subject, row['id']) in pending or (kind, subject, row['id']) in locally_retired:
            continue
        formally_archived = kind != 'fact' and (meta.get('archivedAt') is not None or meta.get('archived'))
        arbitrated = kind == 'fact' and meta.get('archived') and meta.get('archivedAt') is not None and meta.get('status') in ('retracted', 'superseded')
        if not formally_archived and not arbitrated and (meta.get('voidedAt') is not None or meta.get('status') in ('denied', 'retracted', 'superseded', 'merged')):
            result['sync']['dormantEntries'].append(deepcopy(row))
            continue
        native = {'_sync_metadata': deepcopy(meta), 'id': row['id'], 'text': row['text'], 'source_sync_id': meta.get('sourceSyncId') or f'snapshot:{row["id"]}'[:200]}
        if ('temporalScope' if kind == 'reflection' else 'eventWhen') in meta and kind != 'persona':
            # The sender already examined this row for event time.
            from config import MEMORY_SCHEMA_VERSION_CURRENT
            native['schema_version'] = MEMORY_SCHEMA_VERSION_CURRENT
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
        for original, portable in [('event_when_raw', 'eventWhen'), ('relation_type', 'relationType'), ('temporal_scope', 'temporalScope')]:
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
            if previous.get('text') == native['text']:
                for field in ('covered_fact_ids', 'fact_source_counts'):
                    if field in previous:
                        native[field] = deepcopy(previous[field])
            _carried_fact_accounting(native, meta, incoming_fact_sources, withdrawn_facts, previous)
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
            native.update(entity=subject, source=_incoming_fact_source(row),
                          importance=meta.get('importance', 5), absorbed=bool(meta.get('absorbed')),
                          # Only a schema 3 writer can say a fact still waits for judging.
                          signal_processed=bool(meta.get('signalJudged', True)),
                          hash=hashlib.sha256(row['text'].encode()).hexdigest()[:16])
            previous = local_facts.get((subject, row['id']), {})
            if previous.get('text') == native['text']:
                # Derived from the wording alone, so still valid for the same wording.
                for field in ('embedding', 'embedding_text_sha256', 'embedding_model_id', 'tags'):
                    if field in previous:
                        native[field] = deepcopy(previous[field])
                if 'schema_version' in previous:
                    native.setdefault('schema_version', previous['schema_version'])
            if previous.get('text') == native['text'] and previous.get('extraction_path'):
                native['extraction_path'] = previous['extraction_path']
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
            previous = local_reflections.get((subject, row['id']), {})
            if previous.get('text') == native['text'] and 'covered_fact_ids' in previous:
                native['covered_fact_ids'] = deepcopy(previous['covered_fact_ids'])
            if previous.get('text') == native['text'] and 'schema_version' in previous:
                native.setdefault('schema_version', previous['schema_version'])
            native.update(entity=subject, source_fact_ids=list(meta.get('sourceFactIds') or []),
                          status=meta.get('status', 'promoted' if meta.get('promotedAt') else 'confirmed'))
            if native['status'] in ('confirmed', 'promoted') and not native.get('confirmed_at'):
                native['confirmed_at'] = iso(meta.get('evidenceReviewedAt', incoming['completedAt']))
            if meta.get('promotedAt'):
                native['promoted_at'] = iso(meta['promotedAt'])
            # The synthesis-time evidence age and fact accounting are this device's
            # record and not part of the protocol; a snapshot of the same row must
            # not drop them (without the age a line retired as stale stays hidden,
            # without the counts an AI-only line counts as backed). They only hold
            # for the same text on the same facts: a peer's rewrite may rest on
            # older ones.
            if (
                previous.get('text') == native['text']
                and sorted(previous.get('source_fact_ids') or []) == sorted(native['source_fact_ids'])
            ):
                # A line the sender confirmed passed this gate there, and that
                # confirmation is not in the protocol: stale counts would hold it here.
                confirmed_remotely = previous.get('status') == 'pending' and native['status'] != 'pending'
                for field in ('source_facts_oldest_at',) if confirmed_remotely else ('source_facts_oldest_at', 'fact_source_counts', 'counted_fact_ids'):
                    if field in previous:
                        native[field] = deepcopy(previous[field])
            elif (native['status'] == 'pending' and native['source_fact_ids']
                  and all(fid in incoming_fact_sources for fid in native['source_fact_ids'])):
                # New to this device and still pending: count the facts the snapshot
                # carries, or an AI-only line would count as backed and promote without
                # the user. A confirmed line already passed this gate on its sender,
                # and the confirmation that let it through is not in the protocol.
                native['fact_source_counts'] = count_fact_sources(
                    [{'source': incoming_fact_sources[fid]} for fid in native['source_fact_ids']])
                native['counted_fact_ids'] = sorted(native['source_fact_ids'])
            _carried_fact_accounting(native, meta, incoming_fact_sources, withdrawn_facts, previous)
            if formally_archived:
                _append_archive(result, 'reflectionArchives', native, incoming['completedAt'])
            else:
                result['reflections'].append(native)
    # A row left untouched may still rest on facts this document changed: one upgraded
    # to the user's own words, or withdrawn. Its counts follow the facts as they now stand.
    kept = [*result['reflections'], *(row for subject in SUBJECTS for row in result['persona'].get(subject, {}).get('facts', []))]
    for row in kept:
        if not isinstance(row, dict) or not private(row) or not isinstance(row.get('fact_source_counts'), dict):
            continue
        counted = row.get('counted_fact_ids')
        if isinstance(counted, list) and counted and all(fid in incoming_fact_sources for fid in counted):
            row['fact_source_counts'] = count_fact_sources(
                [{'source': incoming_fact_sources[fid]} for fid in counted if fid not in withdrawn_facts])
    return result



def _carried_fact_accounting(native, meta, fact_sources, withdrawn, previous):
    """Apply the sender's record of confirmations and counted facts to a rebuilt row.

    With every counted fact in the snapshot the counts are worked out here, so
    a fact withdrawn or upgraded to the user's own words since then counts as
    it stands now. Otherwise the sender's counts are used: a row without
    counts would pass the gate ungated.
    """
    if 'userConfirmCount' in meta:
        native['user_confirm_count'] = meta['userConfirmCount']
    elif 'user_confirm_count' in previous:
        # An older writer does not carry it; the count belongs to the row whatever its wording.
        native['user_confirm_count'] = previous['user_confirm_count']
    counted, carried = meta.get('countedFactIds'), meta.get('factSourceCounts')
    if counted is None and carried is None:
        return
    ids = list(counted) if counted is not None else list(native.get('source_fact_ids') or [])
    if counted is not None:
        native['counted_fact_ids'] = ids
    if ids and all(fid in fact_sources for fid in ids):
        native['fact_source_counts'] = count_fact_sources(
            [{'source': fact_sources[fid]} for fid in ids if fid not in withdrawn])
    elif carried is not None:
        native['fact_source_counts'] = dict(carried)


def _incoming_fact_source(row):
    return row['metadata'].get('source', 'ai_disclosure' if row['subject'] == 'neko' else 'user_observation')


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
    """Trusted originals are incomplete; callers retain evidence and bound retries."""


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
        raw_id = (original.get('recent_memory_id') or source_proof.get('recordId') or source_proof.get('messageId')
                  or original.get('memory_message_id'))
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
            # The user may correct what the character said about itself; only the
            # user can correct a fact about the user or the relationship.
            roles = ('ai', 'human') if row['subject'] == 'neko' else ('human',)
            times = [at for identity in ids for role, at in known[identity] if role in roles]
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
    cites_correction = set()

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
                # A fact from the correction that replaces the old one is the new
                # value; only one that means the same restates what was retired.
                if decision['action'] == 'replace' and (id(fact), row['id']) in cites_correction:
                    continue
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
            if ids & set(row.get('retiredBySourceMessageIds') or []):
                cites_correction.add((id(fact), row['id']))
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
    """Return trusted provenance, {} when sources cannot be proven, None to reject, or fail for unresolved evidence.

    Only a user observation cited to a non-user message is rejected outright; a missing or
    unusable citation keeps the fact without provenance, as before source evidence existed.
    """
    from memory.facts import FactExtractionFailed
    matching = [row for row in retirements if row.get('subject') == entity and isinstance(row.get('text'), str)
                and (row['id'] in matched_ids or _assertion_text(row['text']) == _assertion_text(fact.get('text', '')))]
    indices = fact.get('source_indices')
    in_range = [index for index in indices if type(index) is int and 1 <= index <= len(source_messages)] if (
        isinstance(indices, list) and source_messages) else []
    cited = [source_messages[index - 1] for index in dict.fromkeys(in_range)]
    selected = cited if in_range and len(in_range) == len(indices) else []
    proofs = []
    # A misattributed user observation is rejected whatever it matches; retrying the
    # whole batch cannot make a character line prove a user fact.
    if source == 'user_observation' and any(getattr(message, 'type', '') not in ('human', 'user') for message in cited):
        return None
    for message in selected:
        identity, at = trusted_message_source(message)
        if not identity:
            proofs = []
            break
        proofs.append((identity, at))
    if matching and not proofs:
        if any(trusted_message_source(message)[0] for message in source_messages or []):
            raise FactExtractionFailed('retired_assertion_source_unavailable')
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

# How many replaced snapshots are remembered: enough for one upload in flight plus
# a capture or two landing during it.
SENT_SNAPSHOTS_KEPT = 3


def _current_state(value):
    """Read a state file written before uploads carried a base revision.

    A downloaded snapshot was the document the server held, so it is a sound
    base. Anything else was an upload from this device, recorded as acknowledged
    whether or not the server took it: it proves nothing, so the device starts
    without a base and merges as a union with whatever the server holds.
    """
    if 'operationId' not in value:
        return value
    operation = value['operationId']
    base = None
    if isinstance(operation, str) and re.fullmatch(r'download:[0-9]{1,16}', operation):
        base = {'revision': int(operation[9:]), 'document': value['document'],
                'nativeEntries': value.get('nativeEntries', value['document']['entries'])}
    return {**{key: value.get(key) for key in ('ownerId', 'characterSyncId', 'serviceUrl', 'epoch')},
            'base': base, 'pending': None, 'unseenRevision': False}


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

    def _checked_path(self, uid):
        return self.directory / f'{uid}.checked.json'

    def mark_checked(self, uid, at):
        """Record that this device held the newest published snapshot at this moment of its own clock."""
        from utils.file_utils import atomic_write_json
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._checked_path(uid), {'at': int(at)})

    def checked_at(self, uid):
        try:
            at = json.loads(self._checked_path(uid).read_text(encoding='utf-8')).get('at')
        except (OSError, ValueError, AttributeError):
            return None
        return at if type(at) is int else None

    def received_directory(self, uid):
        """Where lines another device said wait for this device to extract facts from them."""
        return self.directory.parent / 'received' / uid

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
        """Return the confirmed base and the snapshot waiting for upload, or None without local state.

        base is what the server confirmed: an accepted upload from this device
        or a remote revision it applied. pending is never a base, because an
        upload the server has not accepted proves nothing about other devices.
        """
        path = self.local_path(binding)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = self._cache.get(path)
        if cached is None or cached[0] != stamp:
            value = _current_state(json.loads(path.read_text(encoding='utf-8')))
            for part in (value['base'], value['pending']):
                if part is not None and part['document'] is not None:
                    document(part['document'])
            self._cache[path] = (stamp, value)
        value = self._cache[path][1]
        if (value.get('epoch') != list(epoch) or value.get('ownerId') != binding['owner_id']
                or value.get('characterSyncId') != binding['character_uid'] or value.get('serviceUrl') != binding['service_url']):
            return None
        return {'base': value['base'], 'pending': value['pending'], 'unseenRevision': bool(value.get('unseenRevision')),
                'sent': list(value.get('sent') or []), 'republish': bool(value.get('republish'))}

    def write_local(self, binding, epoch, *, base, pending, unseen_revision=False, sent=(), republish=False):
        """sent keeps snapshots a newer capture replaced while their upload may still be in flight.

        republish says this device holds content the base lacks and no snapshot
        of it waits yet. It is stored with the base, so a process that exits
        before capturing still captures after it starts again.
        """
        from utils.file_utils import atomic_write_json
        atomic_write_json(self.local_path(binding), {
            'ownerId': binding['owner_id'], 'characterSyncId': binding['character_uid'], 'serviceUrl': binding['service_url'],
            'epoch': list(epoch), 'base': base, 'pending': pending, 'unseenRevision': bool(unseen_revision),
            'sent': list(sent)[:SENT_SNAPSHOTS_KEPT], 'republish': bool(republish),
        }, ensure_ascii=False)

    def base_revision(self, binding, epoch):
        local = self.read_local(binding, epoch)
        return local['base']['revision'] if local and local['base'] else 0

    def acknowledge(self, binding, epoch, operation, revision):
        """The accepted upload becomes the base every later merge starts from."""
        local = self.read_local(binding, epoch)
        if not local:
            return False
        if local['pending'] and local['pending']['operationId'] == operation:
            uploaded, waiting = local['pending']['document'], None
        else:
            # A capture replaced the snapshot while this upload was in flight. What the
            # server took is still the base; the newer snapshot now rests on it, so only
            # what it adds counts as unpublished and nothing is counted twice.
            uploaded = next((item['document'] for item in local['sent'] if item['operationId'] == operation), None)
            if uploaded is None:
                return False
            waiting = {**local['pending'], 'baseRevision': revision} if local['pending'] else None
        self.write_local(binding, epoch, pending=waiting,
                         base={'revision': revision, 'document': uploaded, 'nativeEntries': uploaded['entries']})
        return True

    def refresh_pending(self, binding, epoch, operation, now):
        """Give a refused upload a new completion time; one refused as expired would otherwise stay expired."""
        local = self.read_local(binding, epoch)
        if not local or not local['pending'] or local['pending']['operationId'] != operation:
            return False
        renewed = {**local['pending']['document'], 'completedAt': max(now, local['pending']['document']['completedAt'] + 1)}
        self.write_local(binding, epoch, base=local['base'], unseen_revision=local['unseenRevision'], sent=local['sent'],
                         republish=local['republish'],
                         pending={**local['pending'], 'operationId': uuid4().hex, 'document': renewed})
        return True

    def adopt_revision(self, binding, epoch, revision):
        """Follow a revision whose body is gone, or a server that restarted its numbering.

        An expired body cannot be read, so the base keeps its content under the
        new number and the next upload declares that it never saw that revision.
        A number below the base means the server lost its history: every local
        entry then counts as new.
        """
        local = self.read_local(binding, epoch) or {'base': None, 'pending': None, 'unseenRevision': False, 'sent': [], 'republish': False}
        current = local['base']['revision'] if local['base'] else 0
        if revision == current:
            return False
        if revision < current:
            base, unseen = None, False
        else:
            base = {**(local['base'] or {'document': None, 'nativeEntries': []}), 'revision': revision}
            unseen = True
        # A snapshot captured against the old number would only be refused again;
        # what it held has to be captured anew against the adopted one.
        self.write_local(binding, epoch, base=base, pending=None, unseen_revision=unseen, republish=True)
        return True

    def stage_remote(self, binding, remote, epoch, expected_revision, session_fingerprint):
        """Stage a newer revision for the memory owner; the base it merges onto must not have moved."""
        from utils.file_utils import atomic_write_json
        if self.base_revision(binding, epoch) != expected_revision or remote['revision'] <= expected_revision:
            return False
        atomic_write_json(self.incoming_path(binding['character_uid']), {
            'binding': binding, 'epoch': list(epoch), 'head': remote, 'sessionFingerprint': session_fingerprint,
        }, ensure_ascii=False)
        return True

    def staged_revision(self, binding, epoch):
        """The revision already waiting in the inbox for this grant, if any."""
        try:
            pending = json.loads(self.incoming_path(binding['character_uid']).read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError):
            return None
        if pending.get('binding') != binding or pending.get('epoch') != list(epoch):
            return None
        revision = (pending.get('head') or {}).get('revision')
        return revision if type(revision) is int else None

    def forget(self, uid):
        """Drop the confirmed base and inbox of a role whose cloud copy was retired."""
        for path in [self.incoming_path(uid), self.directory / f'{uid}.organizer.json', self._checked_path(uid),
                     *self.directory.glob(f'{uid}-*.json'), *self.directory.glob(f'{uid}.expired-*.json')]:
            path.unlink(missing_ok=True)
            self._cache.pop(path, None)
        received = self.received_directory(uid)
        if received.is_dir():
            for path in received.iterdir():
                path.unlink(missing_ok=True)
            received.rmdir()

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
