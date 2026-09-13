"""Control-plane diagnostics must preserve messages and exclude private data."""

import asyncio
import json
from pathlib import Path
from queue import Queue
from threading import Thread
from unittest.mock import AsyncMock, Mock

import pytest

from tests.unit.test_app_websocket_static import _run_settings_node_harness


def test_lifecycle_sender_records_only_code_sites_and_preserves_handshake():
    source = Path("static/app/app-websocket.js").read_text(encoding="utf-8")
    start = source.index("function attachStartSessionHandshake(ws)")
    end = source.index("function connectWebSocket()", start)
    script = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const frames = [];
const ws = { send(data) { frames.push(data); return 42; } };
const context = vm.createContext({ ws, S: {
    settingsHydrated: true, independentAsrAuthoritative: true,
    independentAsrEnabled: true
} });
vm.runInContext(SOURCE, context, {filename: 'http://localhost/static/app/app-websocket.js'});
vm.runInContext('attachStartSessionHandshake(ws)', context);
for (const action of ['start_session', 'pause_session', 'end_session']) {
    context.payload = JSON.stringify({action, input_type: 'audio'});
    assert.equal(vm.runInContext('ws.send(payload)', context,
        {filename: 'http://localhost/static/app/app-buttons.js'}), 42);
    const sent = JSON.parse(frames.at(-1));
    assert.equal(sent.action, action);
    assert.equal(sent.input_type, 'audio');
    assert.match(sent.lifecycle_trace, /app-buttons\.js:\d+:\d+/);
    assert.match(sent.lifecycle_trace, /^app-[a-z-]+\.js:\d+:\d+(;app-[a-z-]+\.js:\d+:\d+){0,3}$/);
    assert.equal(sent.lifecycle_trace.includes('localhost'), false);
    if (action === 'start_session') assert.equal(sent.independent_asr_enabled, true);
}
const text = JSON.stringify({action: 'stream_data', data: 'private end_session text'});
ws.send(text);
assert.equal(frames.at(-1), text);
const binary = new Uint8Array([1, 2, 3]);
ws.send(binary);
assert.equal(frames.at(-1), binary);
ws.send('invalid start_session json');
assert.equal(frames.at(-1), 'invalid start_session json');
// An unavailable stack must not block the control message or its handshake.
context.Error = function () { throw new TypeError('stack unavailable'); };
ws.send(JSON.stringify({action: 'start_session'}));
assert.equal(JSON.parse(frames.at(-1)).independent_asr_enabled, true);
""".replace("SOURCE", json.dumps(source[start:end]))
    result = _run_settings_node_harness(script)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("trace", [None, "https://private/secret", "app-buttons.js:1:2\nsecret", "x" * 300])
def test_server_rejects_untrusted_trace_text(monkeypatch, trace):
    import main_routers.websocket_router as router

    info = Mock()
    monkeypatch.setattr(router.logger, "info", info)
    router._log_voice_lifecycle_request(
        {"action": "pause_session", "lifecycle_trace": trace, "data": "private"},
        connection_id="test", is_current=True,
    )
    assert info.call_args.args[-1] == "unavailable"
    assert "private" not in str(info.call_args)


def test_server_records_control_sites_but_never_audio_messages(monkeypatch):
    import main_routers.websocket_router as router

    info = Mock()
    monkeypatch.setattr(router.logger, "info", info)
    trace = "app-websocket.js:234:5;app-audio-capture.js:123:4"
    router._log_voice_lifecycle_request(
        {"action": "end_session", "lifecycle_trace": trace},
        connection_id="test", is_current=False,
    )
    assert info.call_args.args[-1] == trace
    info.reset_mock()
    router._log_voice_lifecycle_request(
        {"action": "stream_data", "data": "private"},
        connection_id="test", is_current=True,
    )
    info.assert_not_called()


@pytest.mark.asyncio
async def test_disabling_during_activation_replay_does_not_end_session_or_tts(tmp_path):
    from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
    from tests.unit.test_core_independent_asr import _CoreActivationFactory, _Runtime
    from tests.unit.voice_identity_service.test_service import _service

    service, _, _, _ = _service(tmp_path)
    registry = OwnerVoiceRuntimeRegistry(enforce=True)
    service._activation_callback = registry.activate
    await service.initialize()
    manager = _Runtime()
    manager.end_session = AsyncMock()
    manager.start_session = AsyncMock()
    await registry.register_manager(manager)
    factory = _CoreActivationFactory()
    manager._asr_route_mode = "native"
    await manager.set_voice_session_activation_factory(
        factory, activation_generation="profile", activation_required=True,
    )
    service._requested_enabled = True
    session = manager.session
    delivering, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked_delivery(_pcm):
        delivering.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    session.stream_audio = blocked_delivery
    requests = Queue()
    received = []

    def worker():
        received.append(requests.get(timeout=10))

    thread = Thread(target=worker, daemon=True)
    thread.start()
    manager.tts_thread = thread
    manager.tts_request_queue = requests
    manager.tts_ready = True
    try:
        for _ in range(15):
            await manager._route_microphone_audio(b"\xd0\x07" * 1600, sample_rate_hz=16_000)
            await asyncio.sleep(0)
        await asyncio.wait_for(delivering.wait(), timeout=2)
        status = await service.set_filter(False)
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        assert status.as_dict()["requested_enabled"] is False
        assert factory.closed and factory.scorers[0].closed
        assert manager._voice_session_activation_runtime is None
        manager.end_session.assert_not_called()
        manager.start_session.assert_not_called()
        assert manager.session is session
        assert manager.tts_ready and thread.is_alive()
        assert received == []
    finally:
        requests.put("test_cleanup")
        await asyncio.to_thread(thread.join, 2)
        await service.close()
        await registry.close()
