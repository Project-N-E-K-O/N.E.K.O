"""Memory-owner capture and replay of completed learned-memory snapshots."""

import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
from copy import deepcopy
import json
import re
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from memory.long_term_sync import DesktopLongTermAdapter, JOURNAL, LongTermMemoryRecoveryRequired, advance_view_generation, managed_path, view_files, export_document, import_document, read_views
from memory.recent import _await_recent_mutation_to_completion as _complete_file_commit
from services.chat_sync.long_term_protocol import head
from services.chat_sync.long_term_events import changed
from services.chat_sync.writer import ChatSyncWriter, registered_writer, store_connection
from utils.config_manager.reserved_schema import get_character_uid
from utils.file_utils import atomic_write_json, atomic_write_text
from utils.cloudsave_runtime import assert_cloudsave_writable

from . import runtime
from ._shared import logger, validate_lanlan_name
from .post_turn import _track_character_post_turn_task


_EVENT = 'memory.long_term_replaced'
_capture_tasks = {}
_capture_requests = {}
_capture_intents = {}


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
        previous = adapter.read_local(binding, epoch)
        with runtime.fact_store._get_lock(name):
            views = read_views(Path(runtime._config_manager.memory_dir) / name)
        completed = max(time.time_ns() // 1_000_000, (previous['document']['completedAt'] if previous else 0) + 1)
        document = export_document(views, completed, source_evidence=_snapshot_source_evidence(name, views))
        if not force and ((not previous and not document['entries'] and not document['evidence']['retirements'] and not document['evidence']['confirmations'])
                          or (previous and previous['nativeEntries'] == document['entries'] and (previous['document'].get('evidence') or {'retirements': [], 'confirmations': []}) == document['evidence'])):
            return False
        # The name-to-UID mapping and current grant are still protected at publication.
        if _uid(name) != uid or _binding(uid) != binding:
            return None if expected_uid is not None else False
        try:
            atomic_write_json(adapter.local_path(binding), {
                'ownerId': binding['owner_id'], 'characterSyncId': uid, 'serviceUrl': binding['service_url'],
                'epoch': list(epoch), 'operationId': uuid4().hex, 'document': document, 'nativeEntries': document['entries'],
            }, ensure_ascii=False)
        except OSError as error:
            raise ValueError('long_term_memory_save_failed') from error
    changed()
    return True


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
                # Incorporate downloaded facts before timestamping a new local view.
                await consume(uid)
                async with _views_locked(name):
                    captured = await _complete_file_commit(_capture, name, requested, uid, True)
                if captured is not None and name not in _capture_requests:
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


def _write_snapshot(name, payload):
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
            return _write_snapshot_locked(name, payload)
    except ValueError as error:
        if str(error) in {'chat_sync_login_required', 'chat_sync_session_changed', 'chat_sync_session_unavailable'}:
            return False
        raise


def _write_snapshot_locked(name, payload):
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
        local = adapter.read_local(binding, epoch)
        if local and local['document']['completedAt'] > remote['completedAt']:
            return False
        operation = f'download:{remote["revision"]}'
        if local and local['operationId'] == operation and local['document'] == remote['document']:
            # A crash can occur after the file receipt and before the derived
            # index commits. Repair from current views without replacing them.
            with runtime.fact_store._get_lock(name):
                _replace_fact_index(name, read_views(Path(runtime._config_manager.memory_dir) / name))
            return True
        directory = Path(runtime._config_manager.memory_dir) / name
        assert_cloudsave_writable(runtime._config_manager, operation='sync', target=f'memory/{name}')
        with runtime.fact_store._get_lock(name):
            existing = read_views(directory)
            source_evidence = _snapshot_source_evidence(name, existing, remote['document'])
            views = import_document(remote['document'], existing, baseline=local['nativeEntries'] if local else [], source_evidence=source_evidence)
            # Retained local edits have not been published. Keep them outside the
            # receipt baseline so a later completed organization still captures them.
            native_entries = export_document(import_document(remote['document'], existing), remote['completedAt'], source_evidence=source_evidence)['entries']
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
                atomic_write_json(adapter.local_path(binding), {
                    'ownerId': binding['owner_id'], 'characterSyncId': uid, 'serviceUrl': binding['service_url'],
                    'epoch': list(epoch), 'operationId': operation, 'document': remote['document'],
                    'nativeEntries': native_entries,
                }, ensure_ascii=False)
                _replace_fact_index(name, views)
                runtime.fact_store._facts.pop(name, None)
                runtime.persona_manager._personas.pop(name, None)
                atomic_write_json(directory / JOURNAL, {'version': 1, 'phase': 'closed'}, ensure_ascii=False)
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
            (directory / JOURNAL).unlink(missing_ok=True)
        return True


def _replace_fact_index(name, views):
    index = getattr(runtime.fact_store, '_time_indexed', None)
    if index is not None:
        from memory.facts import _readable_fact_id
        rows = [(fid, str(row.get('text') or '')) for row in views['facts'] + views['archive']
                if isinstance(row, dict) and (fid := _readable_fact_id(row)) is not None]
        index.replace_fact_index(name, rows)


def register_replay(reconciler, persona_manager=None, fact_store=None):
    def replay(name, payload):
        result = _write_snapshot(name, payload)
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
            payload = json.loads(path.read_text(encoding='utf-8'))
            try:
                head(payload['head'], payload['binding']['owner_id'], uid)
            except ValueError:
                return False
            writer = _authority()
            if writer is not None:
                with writer.locked():
                    local = adapter.read_local(payload['binding'], tuple(payload['epoch']))
                    if local and local['document']['completedAt'] > payload['head']['completedAt']:
                        if path.is_file() and json.loads(path.read_text(encoding='utf-8')) == payload:
                            path.unlink()
                        return False
            applied = [False]
            def save(_name, frozen):
                applied[0] = _write_snapshot(_name, frozen)
            runtime.event_log.record_and_save(name, _EVENT, payload,
                sync_load_view=lambda _: deepcopy(payload), sync_mutate_view=lambda _: None, sync_save_view=save)
            # Never delete a replacement inbox installed while this event was committing.
            writer = _authority()
            if writer is not None:
                with writer.locked():
                    if applied[0] and path.is_file() and json.loads(path.read_text(encoding='utf-8')) == payload:
                        path.unlink()
            return applied[0]
        return await _complete_file_commit(commit)


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
            async with _views_locked(name):
                await _complete_file_commit(_recover_snapshot, name)
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
    tasks = list(_capture_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _capture_requests.clear()
    _capture_intents.clear()
