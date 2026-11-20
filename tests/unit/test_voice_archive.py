"""Durability, exact identity association and real playback path regression tests."""
import asyncio
import hashlib
import json
from queue import Queue
import sqlite3
from contextlib import closing
import threading
from types import SimpleNamespace

import pytest

from main_logic.voice_archive import (
    VoiceArchive,
    archive_event,
    drain_voice_archive,
    rename_character_voice_archive,
    retire_voice_archive,
    delete_character_voice_archive,
    has_pending_character_voice_archive_purge,
    retry_pending_voice_archive_purges,
    write_character_voice_archive_purge_intent,
)


def event(kind, sid='speech-a', **kwargs):
    return dict(kind=kind, character='Alice', speech_id=sid, **kwargs)


def read(path, query):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(query).fetchall()


def manager(tmp_path, enabled=True, character='Alice'):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'enabled': enabled, 'unused': 'not-a-filter'}))
    cm = SimpleNamespace(app_docs_dir=tmp_path, load_root_state=lambda: {}, get_runtime_config_path=lambda _: config,
                         get_core_config=lambda: {'TTS_MODEL': 'qwen-audio-3.1-tts-flash', 'api_key': 'DO-NOT-SAVE'})
    return SimpleNamespace(_config_manager=cm, lanlan_name=character, _chat_original_turns={})


def test_recording_is_opt_in(tmp_path):
    mgr = manager(tmp_path, enabled=False)
    async def run():
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        await drain_voice_archive(mgr)
    asyncio.run(run())
    assert not (tmp_path/'voice_archive').exists()


def test_queued_pre_opt_in_events_are_not_saved_after_enabling(tmp_path):
    from utils.voice_archive_settings import save_settings
    async def run():
        mgr = manager(tmp_path, enabled=False)
        # The writer has not had a chance to read the disabled setting yet.
        await archive_event(mgr, 'audio', 'before', data=b'\0\0')
        save_settings(mgr._config_manager, True)
        await drain_voice_archive(mgr)
        assert not (tmp_path/'voice_archive').exists()

        await archive_event(mgr, 'audio', 'after', data=b'\1\1')
        await drain_voice_archive(mgr)
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT speech_id FROM clips') == [('after',)]
    asyncio.run(run())


def test_queued_audio_is_discarded_after_disabling_and_reenabling(tmp_path):
    from utils.voice_archive_settings import save_settings
    async def run():
        mgr = manager(tmp_path, enabled=True)
        await archive_event(mgr, 'audio', 'before', data=b'\0\0')
        save_settings(mgr._config_manager, False)
        save_settings(mgr._config_manager, True)
        await drain_voice_archive(mgr)
        assert not (tmp_path/'voice_archive').exists()
    asyncio.run(run())


def test_repeated_enabled_save_preserves_authorized_queue(tmp_path):
    from utils.voice_archive_settings import save_settings
    async def run():
        mgr = manager(tmp_path, enabled=True)
        await archive_event(mgr, 'audio', 'record', data=b'\x01\x02')
        # A second history window can save True again while the first one is
        # already recording; this must not revoke earlier consent.
        save_settings(mgr._config_manager, True)
        await drain_voice_archive(mgr)
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT data FROM chunks') == [(b'\x01\x02',)]
    asyncio.run(run())


def test_settings_cannot_flip_between_final_consent_check_and_sqlite_commit(tmp_path):
    from utils.voice_archive_settings import save_settings
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'\x01\x02')
        started, release = threading.Event(), threading.Event()
        # The permission lock is held around this write, off the event loop.
        while not hasattr(mgr, '_owner_voice_archive'):
            await asyncio.sleep(0)
        original = mgr._owner_voice_archive.write
        def paused(event):
            started.set()
            release.wait(3)
            original(event)
        mgr._owner_voice_archive.write = paused
        await asyncio.to_thread(started.wait, 3)
        assert started.is_set()
        turn_off = asyncio.create_task(asyncio.to_thread(save_settings, mgr._config_manager, False))
        await asyncio.sleep(.02)
        assert not turn_off.done()
        release.set()
        await drain_voice_archive(mgr)
        await turn_off
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT speech_id FROM clips') == [('s',)]
    asyncio.run(run())


def test_slow_archive_does_not_hold_up_live_event_producers(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'text', 's', text='hello')
        await drain_voice_archive(mgr)
        started = threading.Event()
        release = threading.Event()
        original = mgr._owner_voice_archive.write
        def slow(e):
            started.set()
            release.wait(3)
            original(e)
        mgr._owner_voice_archive.write = slow
        await asyncio.wait_for(archive_event(mgr, 'audio', 's', data=b'\0\0'), .1)
        await asyncio.to_thread(started.wait, 3)
        assert started.is_set()
        # Even while SQLite is stuck in another thread, a live audio frame
        # and its terminal notification only enqueue their archive events.
        await asyncio.wait_for(archive_event(mgr, 'done', 's'), .1)
        release.set()
        await drain_voice_archive(mgr)
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT count(*) FROM chunks') == [(1,)]
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT stream_complete FROM clips') == [(1,)]
    asyncio.run(run())


def test_archive_failure_does_not_break_caller(tmp_path, caplog):
    mgr = manager(tmp_path)
    (tmp_path/'voice_archive').write_text('not a directory')
    async def run():
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        await drain_voice_archive(mgr)
    asyncio.run(run())
    assert 'Voice archive write failed' in caplog.text


def test_real_audio_frame_and_done_integration(tmp_path):
    from main_logic.core import LLMSessionManager
    class Socket:
        client_state = SimpleNamespace()
        client_state.CONNECTED = client_state
        def __init__(self):
            self.calls = []
        async def send_json(self, value):
            self.calls.append(value['type'])
        async def send_bytes(self, value):
            self.calls.append(bytes(value))
    async def run():
        mgr = LLMSessionManager.__new__(LLMSessionManager)
        mgr.__dict__.update(manager(tmp_path).__dict__)
        mgr.websocket = Socket()
        mgr.sync_message_queue = Queue()
        mgr._tts_output_is_current = lambda: True
        mgr._game_speech_correlation_for = lambda _: None
        mgr.release_speech_playback_gain = lambda _: None
        mgr._clear_game_speech_correlation = lambda _: None
        await mgr._write_audio_frame(mgr.websocket, {'type': 'audio_chunk', 'speech_id': 's'}, b'\0\0')
        assert await mgr.send_audio_done('s')
        await drain_voice_archive(mgr)
        assert mgr.websocket.calls == ['audio_chunk', b'\0\0', 'audio_done']
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT stream_complete FROM clips') == [(1,)]
        # Neither audio nor model credentials enter a memory/sync text message.
        assert mgr.sync_message_queue.get_nowait()['type'] == 'binary'
    asyncio.run(run())


def test_metadata_whitelist_and_late_proof(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        mgr._chat_original_turns['s'] = {'recordId': 'a'*32}
        await archive_event(mgr, 'text', 's', text='hello', is_first=True)
        await drain_voice_archive(mgr)
        row = read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT record_id,metadata FROM clips')[0]
        assert row[0] == 'a'*32
        assert 'DO-NOT-SAVE' not in row[1]
        assert 'qwen-audio' in row[1]
    asyncio.run(run())


def test_actual_text_publish_binds_the_same_chat_identity(tmp_path):
    from main_logic.core.turn import TurnMixin
    from tests.unit.test_core_game_route_memory_contract import _make_manager
    async def run():
        mgr = _make_manager()
        mgr.__dict__.update(manager(tmp_path).__dict__)
        await TurnMixin.send_lanlan_response(mgr, '第一句', True)
        await TurnMixin.send_lanlan_response(mgr, '第二句', False)
        await drain_voice_archive(mgr)
        responses = [x['data'] for x in mgr.sync_message_queue.messages
                     if x['type'] == 'json' and x['data']['type'] == 'gemini_response']
        row = read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT record_id,text FROM clips')[0]
        assert row == (responses[0]['chat_original_source']['recordId'], '第一句第二句')
        assert responses[0]['chat_original_source']['recordId'] == responses[1]['chat_original_source']['recordId']
        assert 'voice_archive' not in json.dumps(responses)
    asyncio.run(run())


def test_any_character_can_record_and_disabling_takes_effect(tmp_path):
    async def run():
        mgr = manager(tmp_path, character='Another character')
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        mgr._chat_original_turns['s'] = {'recordId': 'durable-id'}
        await archive_event(mgr, 'text', 's', text='visible reply', is_first=True)
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        path = tmp_path/'voice_archive/archive.sqlite3'
        assert read(path, 'SELECT character,record_id,stream_complete FROM clips') == [('Another character', 'durable-id', 1)]
        (tmp_path/'config.json').write_text('{"enabled": false}')
        await archive_event(mgr, 'audio', 'next', data=b'\1\0')
        await drain_voice_archive(mgr)
        assert read(path, 'SELECT count(*) FROM clips') == [(1,)]
        # Closing each write/read makes this immediate on Windows as well.
        path.rename(tmp_path/'retained.sqlite3')
    asyncio.run(run())


def test_recovered_write_failure_keeps_recording_partial(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        await drain_voice_archive(mgr)
        original = mgr._owner_voice_archive.write
        def fail(_):
            raise OSError('disk full')
        mgr._owner_voice_archive.write = fail
        await archive_event(mgr, 'audio', 's', data=b'\1\0')
        await drain_voice_archive(mgr)
        mgr._owner_voice_archive.write = original
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT archive_error FROM clips') == [(1,)]
    asyncio.run(run())


def test_enabling_mid_speech_never_claims_a_complete_recording(tmp_path):
    async def run():
        mgr = manager(tmp_path, enabled=False)
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        await drain_voice_archive(mgr)
        (tmp_path/'config.json').write_text('{"enabled": true}')
        await archive_event(mgr, 'audio', 's', data=b'\1\0')
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        # The first frame was never authorized, so even the later frames are
        # suppressed; partial Ogg streams without bootstrap headers cannot play.
        assert not (tmp_path/'voice_archive/archive.sqlite3').exists()
    asyncio.run(run())


def test_mid_speech_ogg_opt_in_does_not_expose_headerless_tail(tmp_path):
    from utils.voice_archive_settings import save_settings
    async def run():
        mgr = manager(tmp_path, enabled=False)
        await archive_event(mgr, 'audio', 's', data=b'OggS-bootstrap', format='ogg')
        save_settings(mgr._config_manager, True)
        await archive_event(mgr, 'audio', 's', data=b'page-without-headers', format='ogg')
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        assert not (tmp_path/'voice_archive/archive.sqlite3').exists()
    asyncio.run(run())


def test_maintenance_fence_blocks_recording_without_breaking_live_speech(tmp_path):
    mgr = manager(tmp_path)
    mgr._config_manager.load_root_state = lambda: {'mode': 'maintenance_readonly'}
    async def run():
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        await drain_voice_archive(mgr)
    asyncio.run(run())
    assert not (tmp_path/'voice_archive').exists()


def test_text_config_metadata_read_runs_off_event_loop(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        loop_thread = threading.get_ident()
        seen_threads = []

        def read_config():
            seen_threads.append(threading.get_ident())
            return {'TTS_MODEL': 'test-tts'}

        mgr._config_manager.get_core_config = read_config
        await archive_event(mgr, 'text', 'speech-a', text='hello', is_first=True)
        await drain_voice_archive(mgr)
        assert seen_threads and seen_threads[0] != loop_thread
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT text FROM clips') == [('hello',)]
    asyncio.run(run())


def test_provider_switch_uses_explicit_format_for_each_chunk(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'OggS-first', format='ogg')
        await archive_event(mgr, 'audio', 's', data=b'ogg-page-tail', format='ogg')
        await archive_event(mgr, 'audio', 's', data=b'\0\0', format='pcm_s16le_48000')
        await drain_voice_archive(mgr)
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT format FROM chunks ORDER BY seq') == [
            ('ogg',), ('ogg',), ('pcm_s16le_48000',),
        ]
    asyncio.run(run())


def test_retry_discards_previous_attempt_audio_but_retains_chat_identity(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        mgr._chat_original_turns['s'] = {'recordId': 'durable-id'}
        await archive_event(mgr, 'audio', 's', data=b'OggS-old', format='ogg')
        await archive_event(mgr, 'text', 's', text='discarded', is_first=True)
        await archive_event(mgr, 'retry', 's')
        await archive_event(mgr, 'audio', 's', data=b'\0\1', format='pcm_s16le_48000')
        await archive_event(mgr, 'text', 's', text='accepted', is_first=True)
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        path = tmp_path/'voice_archive/archive.sqlite3'
        assert read(path, 'SELECT data FROM chunks') == [(b'\0\1',)]
        assert read(path, 'SELECT record_id,text,stream_complete FROM clips') == [('durable-id', 'accepted', 1)]
    asyncio.run(run())


def test_character_rename_keeps_recorded_voice_attached_to_history(tmp_path):
    from services.chat_history.voice import attach_voice

    async def run():
        mgr = manager(tmp_path)
        mgr._chat_original_turns['s'] = {'recordId': 'durable-id'}
        await archive_event(mgr, 'audio', 's', data=b'\0\1')
        await archive_event(mgr, 'text', 's', text='hello', is_first=True)
        await drain_voice_archive(mgr)
    asyncio.run(run())

    assert rename_character_voice_archive(tmp_path, 'Alice', 'Bob')
    page = {'messages': [{'recordId': 'durable-id', 'text': 'hello'}]}
    assert attach_voice(page, tmp_path, 'Bob')['messages'][0]['voice'] == {'recordId': 'durable-id'}
    assert not attach_voice({'messages': [{'recordId': 'durable-id'}]}, tmp_path, 'Alice')['messages'][0].get('voice')
    assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT character,record_id FROM clips') == [
        ('Bob', 'durable-id'),
    ]


def test_retired_character_rejects_late_archive_events(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'\x01\x02')
        await retire_voice_archive(mgr)
        await archive_event(mgr, 'audio', 's', data=b'\x03\x04')
        await archive_event(mgr, 'done', 's')
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT data FROM chunks') == [(b'\x01\x02',)]
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT stream_complete FROM clips') == [(0,)]
    asyncio.run(run())


def test_deleting_character_purges_only_her_saved_voice_and_text(tmp_path):
    archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
    for character in ('Alice', 'Bob'):
        for kind, fields in (('audio', {'data': b'\x01\x02', 'format': 'pcm_s16le_48000'}),
                             ('text', {'text': 'private copy', 'is_first': True}),
                             ('done', {})):
            archive.write(dict(character=character, speech_id='s', kind=kind, **fields))
    assert delete_character_voice_archive(tmp_path, 'Alice') == 1
    path = archive.path
    assert read(path, 'SELECT character FROM clips') == [('Bob',)]
    assert read(path, 'SELECT count(*) FROM chunks') == [(1,)]
    assert read(path, 'SELECT count(*) FROM text_events') == [(1,)]
    assert delete_character_voice_archive(tmp_path, 'Alice') == 0


def test_rename_cannot_move_recordings_into_or_out_of_pending_purge(tmp_path):
    archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
    archive.write(event('audio', 'alice-speech', data=b'alice', format='pcm_s16le_48000'))
    archive.write({
        'kind': 'audio', 'character': 'Bob', 'speech_id': 'bob-speech',
        'data': b'bob', 'format': 'pcm_s16le_48000',
    })

    write_character_voice_archive_purge_intent(
        tmp_path, 'Bob', character_uid='deleted-bob', character_id='deleted-bob-id',
    )
    with pytest.raises(RuntimeError, match='pending voice archive purge'):
        rename_character_voice_archive(tmp_path, 'Alice', 'Bob')
    assert read(archive.path, 'SELECT character,speech_id FROM clips ORDER BY character') == [
        ('Alice', 'alice-speech'), ('Bob', 'bob-speech'),
    ]

    # The same fence applies when the old identity itself is awaiting purge.
    write_character_voice_archive_purge_intent(
        tmp_path, 'Alice', character_uid='deleted-alice', character_id='deleted-alice-id',
    )
    with pytest.raises(RuntimeError, match='pending voice archive purge'):
        rename_character_voice_archive(tmp_path, 'Alice', 'Carol')
    assert read(archive.path, 'SELECT character,speech_id FROM clips ORDER BY character') == [
        ('Alice', 'alice-speech'), ('Bob', 'bob-speech'),
    ]


def test_pending_voice_purge_fences_same_name_replacement_until_retry(tmp_path):
    archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
    archive.write(event('audio', data=b'old-audio', format='pcm_s16le_48000'))
    archive.write(event('text', text='old private reply', is_first=True))
    write_character_voice_archive_purge_intent(
        tmp_path, 'Alice', character_uid='old-uid', character_id='old-id',
    )
    assert has_pending_character_voice_archive_purge(tmp_path, 'Alice')
    with pytest.raises(RuntimeError, match='purge is pending'):
        archive.write(event('audio', 'new-speech', data=b'new-audio', format='pcm_s16le_48000'))

    # A same-name character with a different immutable identity is not allowed
    # to inherit the old clip; startup treats the prepared marker as committed.
    config = SimpleNamespace(
        app_docs_dir=tmp_path,
        load_characters=lambda **kwargs: {
            '猫娘': {'Alice': {'_reserved': {
                'character_uid': 'new-uid', 'character_id': 'new-id',
            }}},
        },
    )
    summary = retry_pending_voice_archive_purges(config)
    assert summary['purged'] == [{'character': 'Alice', 'clips': 1}]
    assert not has_pending_character_voice_archive_purge(tmp_path, 'Alice')
    assert read(archive.path, 'SELECT count(*) FROM clips') == [(0,)]

    archive.write(event('audio', 'new-speech', data=b'new-audio', format='pcm_s16le_48000'))
    assert read(archive.path, 'SELECT character,speech_id FROM clips') == [('Alice', 'new-speech')]


def test_prepared_voice_purge_for_same_live_identity_is_discarded(tmp_path):
    archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
    archive.write(event('audio', data=b'keep-audio', format='pcm_s16le_48000'))
    write_character_voice_archive_purge_intent(
        tmp_path, 'Alice', character_uid='same-uid', character_id='same-id',
    )
    config = SimpleNamespace(
        app_docs_dir=tmp_path,
        load_characters=lambda: {
            '猫娘': {'Alice': {'_reserved': {
                'character_uid': 'same-uid', 'character_id': 'same-id',
            }}},
        },
    )
    summary = retry_pending_voice_archive_purges(config)
    assert summary['discarded'] == ['Alice']
    assert not has_pending_character_voice_archive_purge(tmp_path, 'Alice')
    assert read(archive.path, 'SELECT count(*) FROM chunks') == [(1,)]


def test_purge_recovery_keeps_fence_when_character_config_is_not_authoritative(tmp_path):
    archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
    archive.write(event('audio', data=b'protected-audio', format='pcm_s16le_48000'))
    write_character_voice_archive_purge_intent(
        tmp_path, 'Alice', character_uid='old-uid', character_id='old-id',
    )

    def unreadable_config(**kwargs):
        raise FileNotFoundError('characters.json unavailable')

    config = SimpleNamespace(app_docs_dir=tmp_path, load_characters=unreadable_config)
    summary = retry_pending_voice_archive_purges(config)
    assert summary['errors']
    assert has_pending_character_voice_archive_purge(tmp_path, 'Alice')
    assert read(archive.path, 'SELECT count(*) FROM chunks') == [(1,)]


def test_failed_voice_purge_keeps_intent_for_startup_retry(tmp_path, monkeypatch):
    import main_logic.voice_archive as module

    archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
    archive.write(event('audio', data=b'old-audio', format='pcm_s16le_48000'))
    original_connect = module.sqlite3.connect

    def fail_connect(*args, **kwargs):
        raise sqlite3.OperationalError('simulated busy database')

    monkeypatch.setattr(module.sqlite3, 'connect', fail_connect)
    with pytest.raises(sqlite3.OperationalError):
        delete_character_voice_archive(tmp_path, 'Alice')
    assert has_pending_character_voice_archive_purge(tmp_path, 'Alice')

    monkeypatch.setattr(module.sqlite3, 'connect', original_connect)
    config = SimpleNamespace(app_docs_dir=tmp_path, load_characters=lambda **kwargs: {'猫娘': {}})
    summary = retry_pending_voice_archive_purges(config)
    assert summary['purged'] == [{'character': 'Alice', 'clips': 1}]
    assert not has_pending_character_voice_archive_purge(tmp_path, 'Alice')
    assert read(archive.path, 'SELECT count(*) FROM chunks') == [(0,)]


def test_retry_reset_is_preserved_when_archive_queue_is_full(tmp_path, monkeypatch):
    import main_logic.voice_archive as module
    monkeypatch.setattr(module, 'MAX_PENDING_EVENTS', 2)

    async def run():
        mgr = manager(tmp_path)
        mgr._owner_voice_archive = VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')
        started, release = threading.Event(), threading.Event()
        original = mgr._owner_voice_archive.write

        def slow_write(item):
            if item['kind'] == 'audio' and item.get('data') == b'old-inflight':
                started.set()
                release.wait(3)
            original(item)

        mgr._owner_voice_archive.write = slow_write
        await archive_event(mgr, 'audio', 's', data=b'old-inflight')
        assert await asyncio.to_thread(started.wait, 3)

        # Fill the bounded queue with the discarded attempt while the first
        # chunk is still being committed in the writer thread.
        await archive_event(mgr, 'text', 's', text='discarded attempt', is_first=True)
        await archive_event(mgr, 'audio', 's', data=b'old-queued')
        assert mgr._owner_voice_archive_queue.full()

        # The retry reset must not be dropped just because media events filled
        # the queue. It is retained as a priority control event instead.
        await archive_event(mgr, 'retry', 's')
        assert 's' in mgr._owner_voice_archive_retry_resets
        # A new-attempt chunk can also overflow before the reset reaches disk.
        # Its partial marker must not be cleared as if it belonged to the old attempt.
        await archive_event(mgr, 'audio', 's', data=b'dropped-new-attempt')
        release.set()
        await drain_voice_archive(mgr)
        assert 's' not in mgr._owner_voice_archive_retry_resets

        await archive_event(mgr, 'audio', 's', data=b'accepted-attempt')
        await archive_event(mgr, 'text', 's', text='accepted attempt', is_first=True)
        await drain_voice_archive(mgr)
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)

        path = tmp_path / 'voice_archive/archive.sqlite3'
        assert read(path, 'SELECT data FROM chunks') == [(b'accepted-attempt',)]
        assert read(path, 'SELECT text FROM text_events') == [('accepted attempt',)]
        assert read(path, 'SELECT text,stream_complete,archive_error FROM clips') == [('accepted attempt', 1, 1)]

        # A second retry after done must advance the same speech's generation
        # and clear only the partial marker from the discarded prior attempt.
        await archive_event(mgr, 'retry', 's')
        await archive_event(mgr, 'audio', 's', data=b'clean-retry')
        await archive_event(mgr, 'text', 's', text='clean retry', is_first=True)
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        assert read(path, 'SELECT data FROM chunks') == [(b'clean-retry',)]
        assert read(path, 'SELECT text FROM text_events') == [('clean retry',)]
        assert read(path, 'SELECT text,stream_complete,archive_error FROM clips') == [('clean retry', 1, 0)]

    asyncio.run(run())


def test_queue_overflow_marks_next_accepted_event_partial(tmp_path, monkeypatch):
    import main_logic.voice_archive as module
    monkeypatch.setattr(module, 'MAX_PENDING_EVENTS', 1)
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        await archive_event(mgr, 'audio', 's', data=b'\1\1')
        await drain_voice_archive(mgr)
        await archive_event(mgr, 'done', 's')
        await drain_voice_archive(mgr)
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT archive_error FROM clips') == [(1,)]
    asyncio.run(run())

