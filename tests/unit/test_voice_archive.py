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

from main_logic.voice_archive import VoiceArchive, archive_event


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
    asyncio.run(archive_event(mgr, 'audio', 's', data=b'\0\0'))
    assert not (tmp_path/'voice_archive').exists()


def test_cancelled_write_finishes_before_lock_release(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'text', 's', text='hello')
        started = threading.Event()
        release = threading.Event()
        original = mgr._owner_voice_archive.write
        def slow(e):
            started.set()
            release.wait(3)
            original(e)
        mgr._owner_voice_archive.write = slow
        task = asyncio.create_task(archive_event(mgr, 'audio', 's', data=b'\0\0'))
        await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        assert mgr._owner_voice_archive_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT count(*) FROM chunks') == [(1,)]
        assert not mgr._owner_voice_archive_lock.locked()
    asyncio.run(run())


def test_archive_failure_does_not_break_caller(tmp_path, caplog):
    mgr = manager(tmp_path)
    (tmp_path/'voice_archive').write_text('not a directory')
    asyncio.run(archive_event(mgr, 'audio', 's', data=b'\0\0'))
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
        path = tmp_path/'voice_archive/archive.sqlite3'
        assert read(path, 'SELECT character,record_id,stream_complete FROM clips') == [('Another character', 'durable-id', 1)]
        (tmp_path/'config.json').write_text('{"enabled": false}')
        await archive_event(mgr, 'audio', 'next', data=b'\1\0')
        assert read(path, 'SELECT count(*) FROM clips') == [(1,)]
        # Closing each write/read makes this immediate on Windows as well.
        path.rename(tmp_path/'retained.sqlite3')
    asyncio.run(run())


def test_recovered_write_failure_keeps_recording_partial(tmp_path):
    async def run():
        mgr = manager(tmp_path)
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        original = mgr._owner_voice_archive.write
        def fail(_):
            raise OSError('disk full')
        mgr._owner_voice_archive.write = fail
        await archive_event(mgr, 'audio', 's', data=b'\1\0')
        mgr._owner_voice_archive.write = original
        await archive_event(mgr, 'done', 's')
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT archive_error FROM clips') == [(1,)]
    asyncio.run(run())


def test_enabling_mid_speech_never_claims_a_complete_recording(tmp_path):
    async def run():
        mgr = manager(tmp_path, enabled=False)
        await archive_event(mgr, 'audio', 's', data=b'\0\0')
        (tmp_path/'config.json').write_text('{"enabled": true}')
        await archive_event(mgr, 'audio', 's', data=b'\1\0')
        await archive_event(mgr, 'done', 's')
        assert read(tmp_path/'voice_archive/archive.sqlite3', 'SELECT archive_error FROM clips') == [(1,)]
    asyncio.run(run())


def test_maintenance_fence_blocks_recording_without_breaking_live_speech(tmp_path):
    mgr = manager(tmp_path)
    mgr._config_manager.load_root_state = lambda: {'mode': 'maintenance_readonly'}
    asyncio.run(archive_event(mgr, 'audio', 's', data=b'\0\0'))
    assert not (tmp_path/'voice_archive').exists()
