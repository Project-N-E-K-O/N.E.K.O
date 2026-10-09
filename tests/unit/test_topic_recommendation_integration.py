"""Exercise shared chat/output boundaries, with no real model or user storage."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.conversation_turns import ConversationTurnDispatcher
from main_logic.core import LLMSessionManager
from main_logic.proactive_chat import generation
from main_logic.topic.recommendation.contracts import RecommendationSnapshot
from tests.unit.test_proactive_phase2_generation import (
    _FakeManager, _generate, _make_llm_factory, _patch_runtime_guards,
)
from tests.unit.test_proactive_sid_guard import _make_mgr


@pytest.fixture(scope='session', autouse=True)
def mock_memory_server():
    yield


def _snapshot():
    return RecommendationSnapshot(
        'character_' + 'a' * 32, 'session', 'binding', 'root', 'a' * 32,
        1, 1, 1,
        ({'subject_id': 'drawing', 'summary': '画作进度', 'angle': '先问画作进展',
          'basis': 'explicit', 'evidence_refs': ['turn:session:user:input']},), (),
    )


def test_dispatcher_preserves_real_identity_without_changing_legacy_sinks():
    events = []
    dispatcher = ConversationTurnDispatcher('Yui', privacy_check=lambda: True)
    dispatcher.add_sink(SimpleNamespace(note_turn=events.append))
    dispatcher.note_user_message(text='明天继续画', now=100, input_mode='text',
                                 turn_id='input', session_id='session')
    dispatcher.note_ai_message(text='好的')
    assert events[0].raw_text == '明天继续画' and events[0].text is None
    assert (events[0].turn_id, events[0].session_id, events[0].input_mode) == ('input', 'session', 'text')
    assert events[1].input_mode is None and events[1].turn_id is None


@pytest.mark.asyncio
async def test_tts_guard_is_rechecked_after_cache_lock():
    mgr = _make_mgr()
    mgr.current_speech_id = 'proactive'
    current = True
    await mgr.tts_cache_lock.acquire()
    task = asyncio.create_task(mgr.feed_tts_chunk('画怎么样了', expected_speech_id='proactive',
                                                 publish_if=lambda: current))
    await asyncio.sleep(0)
    current = False
    mgr.tts_cache_lock.release()
    assert await task is False
    mgr._enqueue_tts_text_chunk.assert_not_called()


def _publication_mgr():
    mgr = _make_mgr()
    mgr._active_text_request_id = None
    mgr.websocket_lock = None
    mgr._push_focus_thinking = AsyncMock()
    # Use the actual synchronous publication boundary rather than mocking it.
    mgr.send_lanlan_response = LLMSessionManager.send_lanlan_response.__get__(mgr)
    return mgr


@pytest.mark.asyncio
async def test_focus_await_optout_has_no_publication_capture_or_ai_buffer():
    mgr = _publication_mgr()
    mgr.current_speech_id = 'proactive'
    captures = []
    entered, resume = asyncio.Event(), asyncio.Event()
    current = True
    async def focus(_active):
        entered.set()
        await resume.wait()
    mgr._push_focus_thinking = focus
    task = asyncio.create_task(mgr.send_lanlan_response('画怎么样了', True,
        publish_if=lambda: current, on_published=captures.append))
    await entered.wait()
    current = False
    resume.set()
    assert await task is None
    assert not captures and mgr.sync_message_queue.empty()
    assert mgr._current_ai_turn_text == ''


@pytest.mark.asyncio
async def test_sync_publication_captured_once_even_when_websocket_missing():
    mgr = _publication_mgr()
    mgr.current_speech_id = 'proactive'
    captures = []
    def captured(at):
        assert not mgr.sync_message_queue.empty()
        captures.append(at)
    assert await mgr.send_lanlan_response('画怎么样了', True, publish_if=lambda: True,
                                         on_published=captured) is False
    assert len(captures) == 1
    assert mgr.sync_message_queue.get_nowait()['data']['text'] == '画怎么样了'


@pytest.mark.asyncio
async def test_publication_captured_before_cancelled_websocket_wait():
    mgr = _publication_mgr()
    mgr.current_speech_id = 'proactive'
    websocket = SimpleNamespace(client_state=SimpleNamespace(CONNECTED=None))
    websocket.client_state.CONNECTED = websocket.client_state
    entered = asyncio.Event()
    async def send(_message):
        entered.set()
        await asyncio.Event().wait()
    websocket.send_json = send
    mgr.websocket = websocket
    captures = []
    task = asyncio.create_task(mgr.send_lanlan_response('画怎么样了', True,
        publish_if=lambda: True, on_published=captures.append))
    await entered.wait()
    assert len(captures) == 1 and not mgr.sync_message_queue.empty()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(captures) == 1


@pytest.mark.asyncio
async def test_streamed_choice_survives_source_parsing_but_is_removed_before_delivery(monkeypatch):
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()
    generated = await _generate(mgr, ['[CHA', 'T][RE', 'C:R1]画到哪里了？'])
    assert generated.result is None
    assert generated.source_tag == 'CHAT'
    assert generated.response_text == '[REC:R1]画到哪里了？'
    monkeypatch.setattr(generation, '_proactive_directive_hits', lambda *_: ['blocked'])
    guarded = await _guard(mgr, generated.response_text, source='CHAT')
    # The existing directive gate rejects after reserved metadata has been stripped.
    assert guarded.result is not None
    assert guarded.response_text == '画到哪里了？'
    assert guarded.recommendation_subject_id == 'drawing'


async def _guard(mgr, text, *, source='CHAT'):
    return await generation._guard_phase2_output(
        mgr=mgr, proactive_sid='proactive-sid', lanlan_name='Yui',
        response_text=text, full_text=text, source_tag=source, active_channels=['chat'],
        selected_music_link=None, selected_meme_link=None, music_content=None, meme_content=None,
        is_playing_music=False, music_cooldown=False, expects_source_tag=True,
        make_llm=_make_llm_factory([]), messages=[], human_text='', screenshot_b64=None,
        phase2_use_vision=False, phase2_disable_thinking=True, proactive_lang='zh-CN',
        master_name='主人', recommendation_snapshot=_snapshot(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('text,source', [
    ('画怎么样了', 'CHAT'), ('[REC:R3]画怎么样了', 'CHAT'),
    ('[REC:R1][REC:R1]画怎么样了', 'CHAT'), ('画怎么样了[REC:R1]', 'CHAT'),
    ('[REC:R1]听这个歌', 'MUSIC'),
])
async def test_unconfirmed_or_wrong_source_choice_cannot_get_delivery_receipt(text, source):
    result = await _guard(_FakeManager(), text, source=source)
    assert result.result is not None and result.recommendation_subject_id is None


def test_valid_candidate_can_pass_all_sources_rejected_gate():
    decision = generation._decide_phase1_channels([], None, has_unfinished_thread=False,
                                                 has_recommendation=True)
    assert decision.result is None and decision.active_channels == ['chat']
    assert generation._decide_phase1_channels([], None, has_unfinished_thread=False).result is not None
