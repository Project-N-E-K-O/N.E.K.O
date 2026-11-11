"""Memory-owner capture and replay of completed learned-memory snapshots."""

import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from memory.long_term_sync import DesktopLongTermAdapter, JOURNAL, LongTermMemoryRecoveryRequired, advance_view_generation, managed_path, view_files, export_document, import_document, read_views
from memory.long_term_sync import LongTermArchiveSubjectUnavailable, LongTermMemoryCapacityExceeded, RetirementSubjectConflict, retirement_evidence, validate_retirement_subjects
from memory.long_term_sync import LongTermEvidenceCapacityExceeded
from memory.long_term_merge import EVIDENCE_MAX, merge_documents, prune_evidence, same_content, upgraded
from utils.long_term_memory_protocol import MAX_ENTRIES, MAX_INTEGER
from memory.recent import _await_recent_mutation_to_completion as _complete_file_commit
from services.chat_sync.long_term_protocol import head
from services.chat_sync.long_term_events import changed
from services.chat_sync.writer import ChatSyncWriter, registered_writer, store_connection
from utils.config_manager.reserved_schema import get_character_uid
from utils.file_utils import atomic_write_json, atomic_write_text
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable

from . import runtime
from ._shared import logger, validate_lanlan_name
from .post_turn import _track_character_post_turn_task


_EVENT = 'memory.long_term_replaced'
_capture_tasks = {}
_capture_requests = {}
_capture_intents = {}
_extraction_captures = {}
# Several extraction windows in a row publish one snapshot, not one each.
EXTRACTION_CAPTURE_DELAY_SECONDS = 60
_unpublished_after_apply = set()


@contextmanager
def _session_guard(fingerprint):
    # The legacy memory process also needs the credential owner's capabilities.
    from main_routers import card_drop_router  # noqa: F401
    from services.chat_sync.runtime import _saved_session_commit_guard

    with _saved_session_commit_guard(fingerprint):
        yield


def _authority():
    config = runtime._config_manager
    if not getattr(config, 'app_docs_dir', None) or not getattr(config, 'local_state_dir', None):
        return None
    directory = Path(config.app_docs_dir) / 'chat_sync'
    witness = Path(config.local_state_dir) / 'chat_sync_writer.json'
    if not (directory / 'state.sqlite3').is_file() or not witness.is_file():
        return None
    return registered_writer(directory) or ChatSyncWriter(directory, witness)


def _uid(name):
    profile = runtime._config_manager.load_characters().get('猫娘', {}).get(name)
    return get_character_uid(profile) if isinstance(profile, dict) else None


def _binding(uid):
    from services.chat_sync.runtime import _BUILTIN_SERVICE_URL

    writer = _authority()
    if writer is None:
        return None
    with closing(store_connection(writer.directory / 'state.sqlite3')) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM sync_bindings WHERE character_uid=?', (uid,)).fetchone()
        service = conn.execute('SELECT service_url FROM sync_settings WHERE singleton=1').fetchone()
    service_url = (service[0] or _BUILTIN_SERVICE_URL) if service else None
    return {**dict(row), 'service_url': service_url} if row and row['enabled'] and row['binding_id'] and service_url else None


def _publishable(uid):
    return _authority() is not None and _binding(uid) is not None


@asynccontextmanager
async def _views_locked(name):
    admission = runtime._begin_character_request(name)
    if admission is None:
        raise ValueError('character_release_in_progress')
    try:
        async with runtime._get_settle_lock(name), runtime._reload_lock:
            async with runtime.fact_store._get_persist_alock(name), runtime.reflection_engine._get_alock(name), runtime.persona_manager._get_alock(name):
                yield
    finally:
        runtime._end_character_request(name, admission)


def _recover_snapshot(name):
    """Restore local before-images even after login, grant, or writer loss."""
    directory = Path(runtime._config_manager.memory_dir) / name
    path = directory / JOURNAL
    if not path.exists():
        return
    try:
        journal = json.loads(path.read_text(encoding='utf-8'))
        if journal.get('version') != 1 or journal.get('phase') not in ('pending', 'closed'):
            raise ValueError('invalid_long_term_journal')
        if journal['phase'] == 'closed':
            if 'archiveDiagnosticCleanup' in journal:
                _retry_archive_diagnostic_cleanup(name, path, journal['archiveDiagnosticCleanup'])
            else:
                path.unlink(missing_ok=True)
            return
        before = journal['before']
        if not isinstance(before, dict) or not before or not all(managed_path(key) and (value is None or isinstance(value, str)) for key, value in before.items()):
            raise ValueError('invalid_long_term_journal')
        receipt = journal['receipt']
        if not re.fullmatch(r'[0-9a-f]{32}-[0-9a-f]{16}\.json', receipt['filename']) or not (receipt['before'] is None or isinstance(receipt['before'], str)):
            raise ValueError('invalid_long_term_journal')
        restore = {directory / key: value for key, value in before.items()}
        restore[DesktopLongTermAdapter(runtime._config_manager).directory / receipt['filename']] = receipt['before']
        advance_view_generation(directory)
        errors = []
        for target, original in restore.items():
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write_text(target, original)
            except OSError as error:
                errors.append(error)
        if errors:
            raise errors[0]
        _replace_fact_index(name, read_views(directory, recovering=True))
        runtime.fact_store._facts.pop(name, None)
        runtime.persona_manager._personas.pop(name, None)
        atomic_write_json(path, {'version': 1, 'phase': 'closed'}, ensure_ascii=False)
        path.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LongTermMemoryRecoveryRequired() from error
    finally:
        runtime.fact_store._facts.pop(name, None)
        runtime.persona_manager._personas.pop(name, None)


def _archive_cleanup_journal(binding, epoch, operation):
    return {'version': 1, 'phase': 'closed',
            'archiveDiagnosticCleanup': {'binding': binding, 'epoch': list(epoch), 'operationId': operation}}


def _ensure_archive_cleanup_journal(path, closed):
    """Persist the current cleanup only when no rollback record can be lost."""
    try:
        existing = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        pass
    else:
        if existing == closed:
            return
        if not isinstance(existing, dict) or existing.get('version') != 1 or existing.get('phase') != 'closed':
            raise ValueError('invalid_long_term_cleanup_journal')
    atomic_write_json(path, closed, ensure_ascii=False)


def _retry_archive_diagnostic_cleanup(name, path, cleanup):
    """Retry only diagnostic storage after a durable, completed snapshot apply."""
    try:
        binding, epoch = cleanup['binding'], tuple(cleanup['epoch'])
        uid, operation = binding['character_uid'], cleanup['operationId']
        writer = _authority()
        if writer is None:
            return
        with writer.locked():
            try:
                adapter = DesktopLongTermAdapter(runtime._config_manager)
                if _uid(name) != uid or _binding(uid) != binding or writer.epoch() != epoch:
                    path.unlink(missing_ok=True)
                    return
                if f'download:{adapter.base_revision(binding, epoch)}' != operation:
                    path.unlink(missing_ok=True)
                    return
                if adapter.incoming_path(uid).exists():
                    return
                # Later local edits can introduce a new unresolved archive.
                with runtime.fact_store._get_lock(name):
                    read_views(path.parent)
                from services.chat_sync.ledger import ChatDomain
                domain = ChatDomain(binding['owner_id'], uid).key()
                _capture_scheduler(writer.directory).clear_archive_subject_error(domain, binding['generation'])
                path.unlink(missing_ok=True)
                return
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                # Diagnostic I/O must not quarantine an otherwise healthy writer.
                pass
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        pass
    logger.warning('[LongTermSync] archive diagnostic cleanup deferred')


def _snapshot_source_evidence(name, views, incoming=None):
    sources = {}
    for row in [*views.get('facts', []), *views.get('archive', [])]:
        if not isinstance(row, dict):
            continue
        sources.setdefault((row.get('entity'), row.get('id')), set()).update(row.get('source_message_ids') or [])
    for row in (incoming or {}).get('entries', []):
        if row['kind'] == 'fact':
            sources.setdefault((row['subject'], row['id']), set()).update(row['metadata'].get('sourceMessageIds') or [])
    ids = set().union(*(values for values in sources.values() if len(values) > 200))
    index = getattr(runtime.fact_store, '_time_indexed', None)
    return index.get_original_source_evidence(name, ids) if ids and index is not None else {}


@lru_cache(maxsize=1)
def _capture_scheduler(directory):
    from services.chat_sync.scheduler import ChatSyncScheduler
    return ChatSyncScheduler(directory / 'scheduler.sqlite3')


def _record_capture_diagnostic(directory, binding, code):
    from services.chat_sync.ledger import ChatDomain

    try:
        domain = ChatDomain(binding['owner_id'], binding['character_uid']).key()
        _capture_scheduler(directory).record_long_term_capture_error(domain, binding['generation'], code)
    except (OSError, ValueError, sqlite3.Error):
        logger.warning('[LongTermSync] capture diagnostic update deferred')


def _read_native_views(name):
    """Persist only verified legacy subjects under the owner's view locks."""
    directory = Path(runtime._config_manager.memory_dir) / name
    def repair(relative, rows):
        assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}/{relative}')
        try:
            atomic_write_text(directory / relative, json.dumps(rows, ensure_ascii=False, indent=2), create_parents=False)
        except OSError as error:
            raise ValueError('long_term_memory_save_failed') from error
    return read_views(directory, repair_archive=repair)


def _capture(name, force, expected_uid=None, require_consumed=False):
    _recover_snapshot(name)
    writer = _authority()
    uid = _uid(name)
    if expected_uid is not None and uid != expected_uid:
        return None
    if writer is None or uid is None:
        return False
    with writer.locked():
        binding = _binding(uid)
        if binding is None:
            return False
        epoch = writer.epoch()
        adapter = DesktopLongTermAdapter(runtime._config_manager)
        if require_consumed and adapter.incoming_path(uid).is_file():
            raise ValueError('long_term_memory_incoming_pending')
        local = adapter.read_local(binding, epoch) or {'base': None, 'pending': None, 'unseenRevision': False, 'sent': [], 'republish': False}
        base, pending = local['base'], local['pending']
        newest = max([part['document']['completedAt'] for part in (base, pending) if part and part['document']] or [0])
        completed = max(time.time_ns() // 1_000_000, newest + 1)
        try:
            with runtime.fact_store._get_lock(name):
                if local['unseenRevision']:
                    _raise_lineage(name)
                if _prune_evidence(name, base):
                    try:
                        # Shown in the sync panel for a while: old records are gone for good.
                        adapter.mark_evidence_trimmed(uid, _now_ms())
                    except OSError:
                        logger.debug(f'[LongTermSync] {name}: evidence trim notice not saved')
                views = _read_native_views(name)
            if _evidence_over_limit(_saved_evidence(views)):
                # A device that does not organize never trims. Its lists shrink when the
                # organizer's next snapshot is merged in; until then the reason is shown.
                # Checked before the export, which refuses such lists as an invalid document.
                raise LongTermEvidenceCapacityExceeded()
            document = export_document(views, completed, source_evidence=_snapshot_source_evidence(name, views))
        except (LongTermMemoryCapacityExceeded, LongTermArchiveSubjectUnavailable) as error:
            if _uid(name) != uid or _binding(uid) != binding:
                return None if expected_uid is not None else False
            _record_capture_diagnostic(writer.directory, binding, str(error))
            raise
        _record_capture_diagnostic(writer.directory, binding, None)
        revision = base['revision'] if base else 0
        # The name-to-UID mapping and current grant are still protected at publication.
        if _uid(name) != uid or _binding(uid) != binding:
            return None if expected_uid is not None else False
        try:
            if not force and not _unpublished(document, base):
                if pending is not None or local['republish']:
                    # The device returned to what the server holds; nothing waits for upload.
                    # The dropped snapshot may still be in flight, so it stays recognizable.
                    adapter.write_local(binding, epoch, base=base, pending=None,
                                        sent=[pending, *local['sent']] if pending else local['sent'])
                adapter.owed_path(uid).unlink(missing_ok=True)
                return False
            if (not force and pending is not None and pending['baseRevision'] == revision
                    and same_content(pending['document'], document)):
                # The frozen operation keeps retrying with identical bytes.
                adapter.owed_path(uid).unlink(missing_ok=True)
                return False
            document = _renewed_with(name, document, base, _device_id(writer), completed)
            # The replaced snapshot may be on its way to the server right now; it is kept
            # so that its acceptance, or its return as the next revision, is recognized.
            adapter.write_local(binding, epoch, base=base, sent=[pending, *local['sent']] if pending else local['sent'],
                                pending={'operationId': uuid4().hex, 'baseRevision': revision, 'document': document})
            # Everything saved so far is in a snapshot (or needs none), so nothing is owed.
            adapter.owed_path(uid).unlink(missing_ok=True)
        except OSError as error:
            raise ValueError('long_term_memory_save_failed') from error
    changed()
    return True


def _renewed_with(name, document, base, device, now):
    """Let a snapshot the holder publishes anyway renew its claim.

    Other devices then see the holder alive without a publish made only for
    that, and the hourly renewal is left for a holder with nothing else to say.
    """
    held = base['document'].get('organizer') if base and base['document'] else None
    if not held or held['deviceId'] != device or document.get('organizer') != held:
        # Not the confirmed holder, or a claim, rank change or release is already staged.
        return document
    path = Path(runtime._config_manager.memory_dir) / name / 'long_term_portable.json'
    with runtime.fact_store._get_lock(name):
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(state, dict) or state.get('organizer') != held:
                return document
            renewed = {**held, 'renewedAt': max(now, held['renewedAt'] + 1)}
            if renewed['renewedAt'] > MAX_INTEGER:
                # The published claim already sits at the largest time a snapshot may
                # carry; a later one would make every snapshot from here invalid.
                return document
            assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}/long_term_portable.json')
            atomic_write_json(path, {**state, 'organizer': renewed}, ensure_ascii=False, indent=2)
        except (OSError, ValueError, MaintenanceModeError):
            # Renewing here is a courtesy; the snapshot goes out as it is and the
            # hourly renewal still runs.
            return document
    return {**document, 'organizer': renewed}


def _unpublished(document, base):
    """Whether this device holds anything the confirmed base does not."""
    published = upgraded(base['document']) if base and base['document'] is not None else None
    if published is None:
        return bool(document['entries'] or document['evidence']['retirements'] or document['evidence']['confirmations']
                    or document['progress']['a'] or document['progress']['b']
                    or any(field in document for field in ('organizer', 'maintenance', 'lineage')))
    if document['entries'] != base['nativeEntries']:
        return True
    return any(document.get(field) != published.get(field)
               for field in ('evidence', 'progress', 'organizer', 'maintenance', 'lineage'))


def _evidence_over_limit(evidence):
    """Whether a snapshot carrying these evidence lists would be refused."""
    return any(len(evidence.get(field) or ()) > EVIDENCE_MAX for field in ('retirements', 'confirmations'))


def _saved_evidence(views):
    state = views.get('sync')
    evidence = state.get('evidence') if isinstance(state, dict) else None
    return evidence if isinstance(evidence, dict) else {}


def _mergeable(views):
    """The views with evidence lists short enough to be exported for a merge.

    Lists too long to publish cannot be exported either, so without this a
    published snapshot could never be merged in and the device would stay
    stuck. The newest records are kept, which is what the merge keeps of an
    over-long result anyway.
    """
    evidence = _saved_evidence(views)
    if not _evidence_over_limit(evidence):
        return views
    fitting = {field: rows[-EVIDENCE_MAX:] if isinstance(rows, list) else rows for field, rows in evidence.items()}
    return {**views, 'sync': {**views['sync'], 'evidence': fitting}}


def _prune_evidence(name, base):
    """Keep the evidence lists far from the size at which a snapshot is refused. Returns whether it trimmed.

    Only the organizer trims, so two devices never trim differently, and it
    trims what the server already holds, so nothing unpublished is dropped.
    The one exception is lists already too long to publish at all, for
    example after a long time offline: left alone they could never be
    published again, so the oldest records go then too, unless another
    device holds the role and will trim.
    """
    path = Path(runtime._config_manager.memory_dir) / name / 'long_term_portable.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return False
    evidence = state.get('evidence') if isinstance(state, dict) else None
    if not isinstance(evidence, dict):
        return False
    published = (base['document'].get('evidence') or None) if base and base['document'] else None
    if evidence == published:
        allowed = organizes(name)
    else:
        allowed = _evidence_over_limit(evidence) and not _left_to_the_organizer(name, state)
    if not allowed:
        return False
    pruned = prune_evidence(evidence)
    if pruned is evidence:
        return False
    logger.warning(f'[LongTermSync] {name}: evidence records trimmed to stay below the snapshot limit')
    assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}/long_term_portable.json')
    atomic_write_json(path, {**state, 'evidence': pruned}, ensure_ascii=False, indent=2)
    return True


def _raise_lineage(name):
    """Record that the next upload replaces a revision this device could not read."""
    path = Path(runtime._config_manager.memory_dir) / name / 'long_term_portable.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        state = {}
    assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}/long_term_portable.json')
    state['lineage'] = int(state.get('lineage') or 0) + 1
    atomic_write_json(path, state, ensure_ascii=False, indent=2)


def queue_capture(name, *, force=False):
    """Queue after owner locks are released; never await network from organization."""
    uid = _uid(name)
    if uid is None:
        return
    _capture_intents[uid] = force or _capture_intents.get(uid, False)
    _capture_requests[name] = _capture_intents[uid] or _capture_requests.get(name, False)
    if name in _capture_tasks:
        return

    async def run():
        delay = 0.2
        while name in _capture_requests:
            await asyncio.sleep(delay)
            requested = _capture_requests.pop(name)
            try:
                if not await asyncio.to_thread(_publishable, uid):
                    # Unsynced characters must not queue behind the owner locks;
                    # _capture would reach the same verdict after taking them.
                    if name not in _capture_requests:
                        _capture_intents.pop(uid, None)
                    delay = 0.2
                    continue
                # Incorporate downloaded facts before timestamping a new local view.
                await consume(uid)
                async with _views_locked(name):
                    captured = await _complete_file_commit(_capture, name, requested, uid, True)
                if captured is not None and name not in _capture_requests:
                    _capture_intents.pop(uid, None)
                delay = 0.2
            except LongTermMemoryCapacityExceeded:
                # The same unchanged views cannot succeed on a timer. A later
                # memory update queues a fresh capture without trimming history.
                if name not in _capture_requests:
                    _capture_intents.pop(uid, None)
                delay = 0.2
            except Exception:
                _capture_requests[name] = requested or _capture_requests.get(name, False)
                delay = min(delay * 2, 30)
                logger.warning('[LongTermSync] local snapshot staging deferred')

    def finished(task):
        if _capture_tasks.get(name) is task:
            _capture_tasks.pop(name, None)
            if task.cancelled():
                _capture_requests.pop(name, None)
            elif name in _capture_requests:
                queue_capture(name)
    task = _track_character_post_turn_task(name, runtime._spawn_background_task(run()))
    _capture_tasks[name] = task
    task.add_done_callback(finished)


def queue_capture_after_extraction(name):
    """Publish what extraction just saved, once the burst of windows settles."""
    uid = _uid(name)
    if uid is None:
        return
    try:
        # The facts and progress are on disk but their snapshot is a minute away. The
        # mark outlives a shutdown in that minute, so the capture is made after restart
        # instead of leaving other devices to extract the same messages again.
        owed = DesktopLongTermAdapter(runtime._config_manager).owed_path(uid)
        owed.parent.mkdir(parents=True, exist_ok=True)
        owed.touch()
    except OSError:
        logger.debug(f'[LongTermSync] {name}: capture mark not saved')
    if name in _extraction_captures:
        return

    async def run():
        try:
            await asyncio.sleep(EXTRACTION_CAPTURE_DELAY_SECONDS)
        finally:
            _extraction_captures.pop(name, None)
        queue_capture(name)

    _extraction_captures[name] = _track_character_post_turn_task(name, runtime._spawn_background_task(run()))


def _now_ms():
    return time.time_ns() // 1_000_000


def _device_id(writer):
    with closing(store_connection(writer.directory / 'state.sqlite3')) as conn:
        row = conn.execute('SELECT device_id FROM sync_settings WHERE singleton=1').fetchone()
    return row[0] if row else None


def _published_organizer(name):
    """(uid, device, the claim in the last confirmed snapshot), or None for a character that does not sync."""
    writer, uid = _authority(), _uid(name)
    if writer is None or uid is None:
        return None
    binding = _binding(uid)
    device = _device_id(writer) if binding is not None else None
    if binding is None or not device:
        return None
    local = DesktopLongTermAdapter(runtime._config_manager).read_local(binding, writer.epoch())
    base = local['base'] if local else None
    return uid, device, (base['document'].get('organizer') if base and base['document'] else None)


def confirmed_maintenance(name):
    """The ``maintenance`` map of the last snapshot the server confirmed, or None for a character that does not sync."""
    writer, uid = _authority(), _uid(name)
    if writer is None or uid is None:
        return None
    binding = _binding(uid)
    if binding is None:
        return None
    local = DesktopLongTermAdapter(runtime._config_manager).read_local(binding, writer.epoch())
    base = local['base'] if local else None
    document = base['document'] if base and base['document'] else {}
    maintenance = document.get('maintenance')
    return dict(maintenance) if isinstance(maintenance, dict) else {}


def organizes(name):
    """Whether this device runs organizer-only work for a character.

    A character that does not sync, or whose sync state cannot be read, is
    organized here as it always was: stalled memory is worse than a duplicate
    the next merge folds together.
    """
    try:
        published = _published_organizer(name)
    except Exception:
        return True
    if published is None:
        return True
    _, device, holder = published
    return holder is not None and holder['deviceId'] == device


def _left_to_the_organizer(name, state):
    """Whether another device holds the role and this one is not taking it over.

    A claim staged here, for a holder that fell silent or ranks lower, has to
    be published like anything else, so it counts as this device's turn.
    Sync state that cannot be read counts as left to the organizer: the
    trimming this decides drops unpublished records, so not knowing who
    organizes must not allow it. The capture then reports the lists as too
    long. The error is not passed on, because an OSError leaving the writer
    lock would mark the whole sync store as needing recovery.
    """
    try:
        published = _published_organizer(name)
    except Exception:
        return True
    if published is None:
        return False
    _, device, holder = published
    staged = state.get('organizer')
    taking_over = isinstance(staged, dict) and staged.get('deviceId') == device
    return holder is not None and holder['deviceId'] != device and not taking_over


async def _organizer_rank():
    from memory import organizer
    from . import gates

    try:
        model = await runtime._config_manager.aget_model_api_config('summary')
    except Exception:
        return None
    if not isinstance(model, dict) or not model.get('model') or not model.get('base_url'):
        return organizer.RANK_UNABLE
    return organizer.RANK_DESKTOP_EVIDENCE if await gates._ais_powerful_memory_enabled() else organizer.RANK_DESKTOP


def _tend_organizer_state(name, rank):
    """Decide what to publish about the role and stage it in the sync state. Returns whether a capture is due."""
    from memory import organizer

    published = _published_organizer(name)
    if published is None:
        return False
    uid, device, holder = published
    now = _now_ms()
    seen_path = DesktopLongTermAdapter(runtime._config_manager).directory / f'{uid}.organizer.json'
    try:
        seen = json.loads(seen_path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        seen = None
    observed = organizer.observe(holder, seen if isinstance(seen, dict) else None, now)
    if observed != seen:
        if observed is None:
            seen_path.unlink(missing_ok=True)
        else:
            atomic_write_json(seen_path, observed, ensure_ascii=False)
    action = organizer.decide(holder, device, rank, observed, now)
    if action == organizer.NONE:
        return False
    path = Path(runtime._config_manager.memory_dir) / name / 'long_term_portable.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {}
    if state == []:
        state = {}
    if not isinstance(state, dict):
        raise ValueError('invalid_long_term_memory')
    staged = state.get('organizer')
    if action == organizer.RELEASE:
        if staged is None:
            return True
        state.pop('organizer')
    else:
        mine = isinstance(staged, dict) and staged.get('deviceId') == device and staged.get('rank') == rank
        if mine and (action == organizer.CLAIM or staged != holder):
            # Already staged and waiting for the server; a new value each tick
            # would only make every snapshot differ.
            return True
        state['organizer'] = organizer.claim(device, rank, now)
    assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}/long_term_portable.json')
    atomic_write_json(path, state, ensure_ascii=False, indent=2)
    return True


async def tend_organizer(name):
    """Claim, renew or release the organizer role for one character when its rules say so."""
    rank = await _organizer_rank()
    if rank is None or not await asyncio.to_thread(_publishable, _uid(name)):
        return False
    async with _views_locked(name):
        def step():
            with runtime.fact_store._get_lock(name):
                return _tend_organizer_state(name, rank)
        due = await _complete_file_commit(step)
    if due:
        queue_capture(name)
    return due


def _capture_owed(name):
    """Whether an applied merge or an adopted revision left content here that no snapshot carries yet."""
    writer, uid = _authority(), _uid(name)
    binding = _binding(uid) if writer is not None and uid is not None else None
    if binding is None:
        return False
    adapter = DesktopLongTermAdapter(runtime._config_manager)
    if adapter.owed_path(uid).exists():
        return True
    local = adapter.read_local(binding, writer.epoch())
    return bool(local and local['republish'])


async def resume_owed_capture(name):
    """Queue the capture a merge still owes, for example after the process exited before queueing it."""
    owed = await asyncio.to_thread(_capture_owed, name)
    if owed:
        queue_capture(name)
    return owed


ORGANIZER_CHECK_INTERVAL_SECONDS = 5 * 60
_ORGANIZER_INITIAL_DELAY_SECONDS = 120


async def _periodic_organizer_loop():
    await asyncio.sleep(_ORGANIZER_INITIAL_DELAY_SECONDS)
    while True:
        try:
            if getattr(runtime._config_manager, 'app_docs_dir', None):
                characters = (await runtime._config_manager.aload_characters()).get('猫娘', {})
                for name in list(characters):
                    try:
                        await resume_owed_capture(validate_lanlan_name(name))
                        await tend_organizer(validate_lanlan_name(name))
                    except Exception as error:
                        logger.debug(f'[LongTermSync] {name}: organizer check skipped: {error}')
        except Exception as error:
            logger.debug(f'[LongTermSync] organizer check skipped: {error}')
        await asyncio.sleep(ORGANIZER_CHECK_INTERVAL_SECONDS)


def resume_captures(characters):
    """Requeue canceled publication by UID after rename or rollback reload."""
    names = {uid: name for name, profile in (characters.get('猫娘') or {}).items()
             if isinstance(profile, dict) and (uid := get_character_uid(profile))}
    for uid, force in list(_capture_intents.items()):
        if uid not in names:
            _capture_intents.pop(uid, None)
            continue
        name = validate_lanlan_name(names[uid])
        if name not in _capture_tasks and runtime._is_character_publication_admitted(name):
            queue_capture(name, force=force)


def _write_snapshot(name, payload, *, check_only=False):
    _recover_snapshot(name)
    fingerprint = payload.get('sessionFingerprint')
    if not isinstance(fingerprint, str) or not fingerprint:
        return False
    try:
        head(payload['head'], payload['binding']['owner_id'], payload['binding']['character_uid'])
    except ValueError:
        # Invalid historical sync events must not block subsequent memory events.
        return False
    try:
        with _session_guard(fingerprint):
            return _write_snapshot_locked(name, payload, check_only=check_only)
    except LongTermArchiveSubjectUnavailable as error:
        binding = payload['binding']
        try:
            if _uid(name) == binding['character_uid'] and _binding(binding['character_uid']) == binding:
                writer = _authority()
                if writer is not None:
                    _record_capture_diagnostic(writer.directory, binding, str(error))
        except (OSError, ValueError, sqlite3.Error):
            logger.warning('[LongTermSync] archive diagnostic recording deferred')
        if not check_only:
            # Retained inboxes can retry without blocking later local events.
            return False
        raise
    except ValueError as error:
        if isinstance(error, RetirementSubjectConflict) or str(error) in {'chat_sync_login_required', 'chat_sync_session_changed', 'chat_sync_session_unavailable'}:
            return False
        raise


def _write_snapshot_locked(name, payload, *, check_only=False):
    writer = _authority()
    if writer is None:
        return False
    binding, epoch = payload['binding'], tuple(payload['epoch'])
    uid = binding['character_uid']
    remote = head(payload['head'], binding['owner_id'], uid)
    if remote.get('document') is None:
        raise ValueError('invalid_long_term_memory')
    with writer.locked():
        try:
            writer.require_epoch(epoch)
        except ValueError:
            return False
        if _uid(name) != uid or _binding(uid) != binding:
            return False
        adapter = DesktopLongTermAdapter(runtime._config_manager)
        local = adapter.read_local(binding, epoch) or {'base': None, 'pending': None, 'unseenRevision': False, 'sent': [], 'republish': False}
        base = local['base']
        applied = bool(base and base['revision'] == remote['revision'] and base['document'] == remote['document'])
        if base and base['revision'] >= remote['revision'] and not applied:
            return False
        if check_only:
            validate_retirement_subjects(retirement_evidence(Path(runtime._config_manager.memory_dir) / name),
                                         remote['document'].get('evidence', {}).get('retirements', []))
            with runtime.fact_store._get_lock(name):
                _read_native_views(name)
            return True
        operation = f'download:{remote["revision"]}'
        if applied:
            # A crash can occur after the file receipt and before the derived
            # index commits. Repair from current views without replacing them.
            with runtime.fact_store._get_lock(name):
                _replace_fact_index(name, read_views(Path(runtime._config_manager.memory_dir) / name))
            return True
        own = next((item for item in [*([local['pending']] if local['pending'] else []), *local['sent']]
                    if same_content(item['document'], remote['document'])), None)
        if own is not None:
            # The published revision is a snapshot this device uploaded and never saw
            # accepted (a lost reply, or a capture that replaced it mid-upload). It is
            # already in the native views, so it becomes the base as it stands: merging
            # it in again would add its increments a second time.
            adapter.write_local(binding, epoch, pending=None, republish=True, base={
                'revision': remote['revision'], 'document': remote['document'], 'nativeEntries': own['document']['entries']})
            _unpublished_after_apply.add(name)
            return True
        directory = Path(runtime._config_manager.memory_dir) / name
        assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}')
        with runtime.fact_store._get_lock(name):
            held = _read_native_views(name)
            existing = _mergeable(held)
            source_evidence = _snapshot_source_evidence(name, existing, remote['document'])
            # What this device holds, the base both sides started from and the
            # published document become one document; an upload the server never
            # accepted is part of what this device holds, never of the base.
            current = export_document(existing, remote['completedAt'], source_evidence=source_evidence)
            report = {}
            merged = merge_documents(base if base and base['document'] is not None else None,
                                     current, remote['document'], remote['completedAt'], report=report)
            if len(merged['entries']) > MAX_ENTRIES:
                raise LongTermMemoryCapacityExceeded()
            unpublished = not same_content(merged, remote['document'])
            if unpublished:
                _unpublished_after_apply.add(name)
            views = import_document(merged, existing, merged=True, source_evidence=source_evidence)
            # The base keeps the published entries as this device would export them,
            # so whatever the merge kept from this device still differs from it and
            # the next capture uploads it; so do retirements the remote does not
            # carry as superseded yet.
            native_entries = export_document(import_document(
                remote['document'], existing, merged=True, source_evidence=source_evidence,
                keep_unpublished_retirements=False,
            ), remote['completedAt'], source_evidence=source_evidence)['entries']
            files = view_files(views)
            before = {relative: (directory / relative).read_text(encoding='utf-8')
                      if (directory / relative).is_file() else None for relative in files}
            receipt_path = adapter.local_path(binding)
            journal = {'version': 1, 'phase': 'pending', 'before': before,
                       'receipt': {'filename': receipt_path.name,
                                   'before': receipt_path.read_text(encoding='utf-8') if receipt_path.is_file() else None}}
            atomic_write_json(directory / JOURNAL, journal, ensure_ascii=False)
            try:
                advance_view_generation(directory)
                for relative, value in files.items():
                    atomic_write_json(directory / relative, value, ensure_ascii=False, indent=2)
                # The mark is saved with the new base: if the process exits before the
                # capture below is queued, the periodic check still finds it.
                adapter.write_local(binding, epoch, pending=None, republish=unpublished, base={
                    'revision': remote['revision'], 'document': remote['document'], 'nativeEntries': native_entries})
                _replace_fact_index(name, views)
                runtime.fact_store._facts.pop(name, None)
                runtime.persona_manager._personas.pop(name, None)
                atomic_write_json(directory / JOURNAL, _archive_cleanup_journal(binding, epoch, operation), ensure_ascii=False)
            except BaseException as error:
                # A failed rollback retains a durable journal and blocks all derived reads.
                try:
                    _recover_snapshot(name)
                except LongTermMemoryRecoveryRequired:
                    logger.warning('[LongTermSync] rollback incomplete; original save error retained')
                if isinstance(error, OSError):
                    raise ValueError('long_term_memory_save_failed') from error
                raise
            finally:
                runtime.fact_store._facts.pop(name, None)
                runtime.persona_manager._personas.pop(name, None)
        if existing is not held or report['localEvidenceDropped']:
            try:
                # The oldest records this device held are gone, some perhaps never
                # published: cut before the merge because they could not be exported,
                # or by the merge because the joined lists were too long. The sync
                # panel says so, as it does after the organizer trims.
                adapter.mark_evidence_trimmed(uid, _now_ms())
            except OSError:
                logger.debug(f'[LongTermSync] {name}: evidence trim notice not saved')
        return True


def _replace_fact_index(name, views):
    index = getattr(runtime.fact_store, '_time_indexed', None)
    if index is not None:
        from memory.facts import _readable_fact_id
        rows = [(fid, str(row.get('text') or '')) for row in views['facts'] + views['archive']
                if isinstance(row, dict) and (fid := _readable_fact_id(row)) is not None]
        index.replace_fact_index(name, rows)


def _staged_payload(event):
    """Resolve a journaled inbox reference; None once the inbox was replaced or consumed."""
    if 'head' in event:
        # Events journaled before inbox references carried the whole snapshot.
        return event
    uid, digest = event.get('characterSyncId'), event.get('inboxSha256')
    if not isinstance(uid, str) or not re.fullmatch(r'[0-9a-f]{32}', uid) or not isinstance(digest, str):
        return None
    try:
        text = DesktopLongTermAdapter(runtime._config_manager).incoming_path(uid).read_text(encoding='utf-8')
    except (OSError, ValueError):
        # A missing or momentarily unreadable inbox must not stall this
        # character's later events; the inbox itself stays, and consume
        # applies it once it can be read.
        return None
    # The digest was taken from parsed text, so a matching inbox always parses.
    return json.loads(text) if _inbox_digest(text) == digest else None


def _inbox_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def register_replay(reconciler, persona_manager=None, fact_store=None):
    def replay(name, payload):
        staged = _staged_payload(payload)
        result = _write_snapshot(name, staged) if staged is not None else False
        if persona_manager is not None:
            persona_manager._personas.pop(name, None)
        if fact_store is not None:
            fact_store._facts.pop(name, None)
        return result
    reconciler.register(_EVENT, replay)


async def consume(uid):
    adapter = DesktopLongTermAdapter(runtime._config_manager)
    path = adapter.incoming_path(uid)
    if not path.is_file():
        return False
    if _authority() is None:
        return False
    data = await runtime._config_manager.aload_characters()
    name = next((name for name, profile in data.get('猫娘', {}).items()
                 if isinstance(profile, dict) and get_character_uid(profile) == uid), None)
    if name is None:
        return False
    name = validate_lanlan_name(name)
    if ((name in _capture_tasks and _capture_tasks[name] is not asyncio.current_task())
            or name in _capture_requests):
        return False
    from . import review
    if any(name in tasks and not tasks[name].done() for tasks in (review.correction_tasks, review.compress_backup_tasks)):
        return False
    async with _views_locked(name):
        await _complete_file_commit(_recover_snapshot, name)
        await runtime.reconciler.areconcile(name)
        def commit():
            text = path.read_text(encoding='utf-8')
            payload = json.loads(text)
            try:
                head(payload['head'], payload['binding']['owner_id'], uid)
            except ValueError:
                return False
            writer = _authority()
            if writer is not None:
                with writer.locked():
                    if adapter.base_revision(payload['binding'], tuple(payload['epoch'])) > payload['head']['revision']:
                        if path.is_file() and json.loads(path.read_text(encoding='utf-8')) == payload:
                            path.unlink()
                        return False
            # An inbox waiting for login, a binding or an epoch stays staged without
            # a journal entry; only an inbox that can apply now is journaled.
            if not _write_snapshot(name, payload, check_only=True):
                return False
            applied = [False]
            def save(_name, frozen):
                applied[0] = _write_snapshot(_name, frozen)
            # Journal a reference: the inbox outlives the event until it applies.
            runtime.event_log.record_and_save(name, _EVENT, {'characterSyncId': uid, 'inboxSha256': _inbox_digest(text)},
                sync_load_view=lambda _: deepcopy(payload), sync_mutate_view=lambda _: None, sync_save_view=save)
            # Never delete a replacement inbox installed while this event was committing.
            writer = _authority()
            if writer is not None:
                with writer.locked():
                    if applied[0] and path.is_file() and json.loads(path.read_text(encoding='utf-8')) == payload:
                        journal = Path(runtime._config_manager.memory_dir) / name / JOURNAL
                        closed = _archive_cleanup_journal(payload['binding'], payload['epoch'],
                                                          f'download:{payload["head"]["revision"]}')
                        try:
                            _ensure_archive_cleanup_journal(journal, closed)
                        except (OSError, ValueError):
                            logger.warning('[LongTermSync] archive cleanup journal save deferred')
                            return False
                        path.unlink()
                        _retry_archive_diagnostic_cleanup(name, journal, closed['archiveDiagnosticCleanup'])
            return applied[0]
        applied = await _complete_file_commit(commit)
    if applied and name in _unpublished_after_apply:
        _unpublished_after_apply.discard(name)
        _publish_merge(name)
    return applied


def _publish_merge(name):
    """Whatever a merge kept from this device is not on the server yet."""
    queue_capture(name)


async def recover_pending_snapshot(name):
    """Finish an interrupted snapshot; raises LongTermMemoryRecoveryRequired while it cannot."""
    if not getattr(runtime._config_manager, 'app_docs_dir', None):
        return
    if await asyncio.to_thread((Path(runtime._config_manager.memory_dir) / name / JOURNAL).is_file):
        async with _views_locked(name):
            await _complete_file_commit(_recover_snapshot, name)


async def consume_for_name(name):
    try:
        if not getattr(runtime._config_manager, 'app_docs_dir', None):
            return False
        def pending():
            journal = (Path(runtime._config_manager.memory_dir) / name / JOURNAL).is_file()
            adapter = DesktopLongTermAdapter(runtime._config_manager)
            # No character config read is needed for the ordinary, sync-free path.
            if not journal and not any(adapter.directory.glob('*.incoming.json')):
                return False, None
            uid = _uid(name)
            return journal, uid if uid and adapter.incoming_path(uid).is_file() else None
        journal, uid = await asyncio.to_thread(pending)
        if not journal and uid is None:
            return False
        if journal:
            await recover_pending_snapshot(name)
        if uid:
            return await consume(uid)
    except LongTermMemoryRecoveryRequired:
        raise
    except Exception:
        from memory.long_term_sync import require_consistent_views
        await asyncio.to_thread(require_consistent_views, Path(runtime._config_manager.memory_dir) / name)
        logger.warning('[LongTermSync] incoming snapshot deferred')
    return False


async def close():
    tasks = [*_capture_tasks.values(), *_extraction_captures.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _capture_requests.clear()
    _capture_intents.clear()
    _extraction_captures.clear()
