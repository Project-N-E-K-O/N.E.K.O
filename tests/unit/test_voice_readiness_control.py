"""Exercise readiness through actual Core routes and the WebSocket entry point."""

from __future__ import annotations

import asyncio
import gc
import json
from pathlib import Path
import shutil
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock
import weakref

from fastapi import FastAPI
import httpx
import pytest

import main_logic.core.asr_runtime as asr_module
import main_logic.core.voice_readiness as readiness_module
import main_logic.voice_input.preview as preview_module
import main_routers.websocket_router as router
import main_routers.voice_identity_router as identity_router
from main_routers.system_router import _shared as system_shared
from main_logic.core.streaming import StreamingMixin
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from tests.support.asr_fakes import _CoreActivationFactory, _Runtime
from tests.unit.test_websocket_binary_audio import (
    _EventWebSocket, _ProtocolManager, _install_protocol_endpoint,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.fixture
def registry(monkeypatch):
    value = preview_module.VoicePreviewIsolationRegistry()
    for module in (asr_module, readiness_module, preview_module):
        monkeypatch.setattr(module, "preview_isolation_registry", value)
    return value


def manager(route="native"):
    value = _Runtime()
    value._voice_lease_connection_id = "producer-a"
    value._voice_lease_generation = 1
    value._set_microphone_route(route)
    value.session.stream_audio = AsyncMock()
    value._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    return value


async def cleanup(value):
    # Retire input before joining cleanup; an _Runtime has no full manager cleanup.
    value._voice_input_suppressed = True
    value._voice_lease_synchronized = False
    value._voice_lease_owner = None
    value._set_microphone_route("blocked")
    await value.set_voice_session_activation_factory(None, activation_generation="test-cleanup")
    worker = value._audio_stream_worker_task
    if worker is not None:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    await value._asr_runtime.close()
    await asyncio.gather(*tuple(value._core_asr_cleanup_tasks), return_exceptions=True)


def retry_message(value, request_id="retry-a"):
    generation = value._capture_voice_session_activation_generation()
    return {
        "event": "activation_retry", "request_id": request_id,
        "session_id": generation.session_id,
        "microphone_generation": generation.microphone,
        "route_generation": generation.route,
        "profile_revision": generation.profile,
        "permission_revision": generation.permission,
    }


@pytest.fixture
def trial_api(monkeypatch):
    # DSP is controlled; mutation validation, bounds, claim, validation and the
    # API's finally release all run through the actual ASGI endpoint.
    service = SimpleNamespace(check_trial_audio=AsyncMock(return_value={
        "accepted": True, "audio_contract": "owner-campplus-desktop-v1",
    }))
    monkeypatch.setattr(identity_router, "get_voice_identity_service_for_router", lambda: service)
    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", "coupled-trial-test")
    app = FastAPI()
    app.include_router(identity_router.router)
    return app, service


def trial_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
        base_url="http://testserver", headers={
            "Origin": "http://testserver", "X-CSRF-Token": "coupled-trial-test",
        })


async def check_actual_trial(client, token):
    return await client.post("/api/voice-identity/audio/check", content=b"\0" * 288000,
        headers={"Content-Type": "audio/pcm;format=pcm_s16le;rate=48000;channels=1",
            "X-Voice-Audio-Contract": "owner-campplus-desktop-v1", "X-Voice-Input-Check": token})


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("cleanup_token_valid", [True, False])
async def test_actual_core_api_and_frontend_keep_successful_trial_proof(registry, trial_api, route, cleanup_token_valid):
    node = shutil.which("node")
    assert node is not None, "Coupled frontend protocol validation requires Node.js"
    value = manager(route)
    app, service = trial_api
    websocket = _EventWebSocket([])
    operation_before = value._asr_route_operation_generation
    process = await asyncio.create_subprocess_exec(node,
        str(Path(__file__).parents[1] / "frontend" / "voice_preview_protocol.cjs"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    channels = []
    try:
        async with trial_client(app) as client:
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), 10)
                assert line, (await process.stderr.read()).decode()
                request = json.loads(line)
                channels.append(request["channel"])
                if request["channel"] == "result":
                    frontend = request["payload"]
                    break
                if request["channel"] == "control":
                    message = request["payload"]
                    if message["event"] == "preview_end" and not cleanup_token_valid:
                        message = {**message, "token": "invalid-cleanup-token"}
                    await router._dispatch_voice_identity_control(value, websocket, message,
                        connection_id="producer-a", owns_voice=lambda: True)
                    status = json.loads(json.loads(websocket.sent_text[-1])["message"])
                    assert status["code"] == "VOICE_IDENTITY_CONTROL_RESULT"
                    result = status["details"]
                    if result["event"] == "preview_begin":
                        assert result["ok"] is True, result
                        # The lambda reads the newly assigned closure cell;
                        # incrementing the operation is not an owner change.
                        assert value._asr_route_operation_generation > operation_before
                else:
                    assert request["channel"] == "audio-check"
                    response = await client.post("/api/voice-identity/audio/check",
                        content=b"\0" * request["payload"]["bytes"], headers=request["payload"]["headers"])
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert registry._ticket is None  # The actual API has already released it.
                process.stdin.write((json.dumps({"id": request["id"], "payload": result}) + "\n").encode())
                await process.stdin.drain()
        assert channels == ["control", "audio-check", "control", "result"]
        assert frontend["canStart"] is cleanup_token_valid
        assert frontend["audioContract"] == ("owner-campplus-desktop-v1" if cleanup_token_valid else None)
        assert frontend["ownerBlocked"] is not cleanup_token_valid
        assert frontend["recording"] is False
        assert frontend["trackEnded"] and frontend["microphoneStops"] == 1
        service.check_trial_audio.assert_awaited_once_with(b"\0" * 288000,
            noise_reduction_enabled=value._voice_input_noise_reduction_enabled)
        assert not value._voice_input_accepts_pcm()
        await value._route_microphone_audio(b"\x01\x00" * 160, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
        assert await asyncio.wait_for(process.wait(), 5) == 0
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        await cleanup(value)


@pytest.mark.parametrize("failure,diagnostic", [
    ("rpc_rejected", "controlled_transport_failure"),
    ("callback_throws", "controlled_control_result_failure"),
    ("missing_confirmation", "protocol_rpc_timeout: control preview_begin id=1"),
    ("invalid_json", "SyntaxError"),
    ("unknown_reply", "protocol_unknown_reply: 999"),
    ("pipe_closed", "protocol_pipe_closed"),
])
async def test_protocol_subprocess_routes_transport_failures_through_run(tmp_path, failure, diagnostic):
    node = shutil.which("node")
    assert node is not None, "Protocol subprocess validation requires Node.js"
    args = [node, "--unhandled-rejections=warn"]
    if failure == "callback_throws":
        # Inject a callback fault after loading the actual owner controller;
        # this checks that the success branch's exception also reaches run.
        preload = tmp_path / "throw_control_result.cjs"
        preload.write_text("""
const vm = require('node:vm');
const execute = vm.runInContext;
vm.runInContext = function (source, context, options) {
    const result = execute(source, context, options);
    if (options.filename === 'static/app/app-voice-readiness.js') {
        const create = context.createVoiceCaptureReadiness;
        context.createVoiceCaptureReadiness = function (...args) {
            const owner = create(...args);
            owner.controlResult = () => { throw new Error('controlled_control_result_failure'); };
            return owner;
        };
    }
    return result;
};
""", encoding="utf-8")
        args.extend(["--require", str(preload)])
    args.append(str(Path(__file__).parents[1] / "frontend" / "voice_preview_protocol.cjs"))
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        request = json.loads(await asyncio.wait_for(process.stdout.readline(), 20))
        assert request["channel"] == "control" and request["payload"]["event"] == "preview_begin"
        if failure == "pipe_closed":
            process.stdin.close()
        elif failure != "missing_confirmation":
            if failure == "invalid_json":
                reply = "{not-valid-json}\n"
            elif failure == "unknown_reply":
                reply = json.dumps({"id": 999, "payload": {}}) + "\n"
            elif failure == "callback_throws":
                reply = json.dumps({"id": request["id"], "payload": {
                    "event": "preview_begin", "request_id": request["payload"]["request_id"],
                    "ok": True, "token": "controlled-token", "ttl_seconds": 30,
                    "noise_reduction_enabled": True,
                }}) + "\n"
            else:
                reply = json.dumps({"id": request["id"], "error": diagnostic}) + "\n"
            process.stdin.write(reply.encode())
            await process.stdin.drain()
        # Keep stdin open for the lost-confirmation case. The harness's six-
        # second deadline must terminate itself before this external watchdog.
        assert await asyncio.wait_for(process.wait(), 8 if failure == "missing_confirmation" else 20) == 1
        output, errors = await process.stdout.read(), await process.stderr.read()
        text = errors.decode()
        assert "voice_preview_protocol_failed:" in text and diagnostic in text
        assert "UnhandledPromiseRejection" not in text
        assert '"channel":"result"' not in output.decode()
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


async def test_api_consumed_receipt_cannot_release_new_ticket_or_accept_wrong_owner(registry, trial_api):
    value, other = manager(), manager()
    other._set_microphone_route("blocked")
    other._voice_lease_owner = None
    other._voice_lease_synchronized = False
    app, _ = trial_api
    try:
        first = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "first"}, connection_id="producer-a")
        assert first["ok"]
        async with trial_client(app) as client:
            assert (await check_actual_trial(client, first["token"])).status_code == 200
            assert (await check_actual_trial(client, first["token"])).status_code == 409
        second = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "second"}, connection_id="producer-a")
        assert second["ok"]
        successor = registry._ticket
        for token, owner, expected in [(first["token"], value, True),
                (first["token"], other, False), ("wrong-token", value, False),
                (second["token"], other, False)]:
            result = await owner._handle_voice_identity_control(
                {"event": "preview_end", "request_id": "end", "token": token}, connection_id="producer-a")
            assert result["ok"] is expected
            assert registry._ticket is successor and registry.is_manager_isolated(value)
        assert registry.claim(second["token"]) is successor
        assert not value._voice_input_accepts_pcm()
    finally:
        registry.release(registry._ticket)
        await cleanup(value)
        await cleanup(other)


async def test_consumed_cleanup_receipts_are_bounded_expiring_and_do_not_retain_owner(registry):
    clock = [0.0]
    registry.now = lambda: clock[0]
    value = manager()
    value._set_microphone_route("blocked")
    value._voice_lease_owner = None
    value._voice_lease_synchronized = False
    tokens = []
    try:
        for index in range(33):
            ticket = registry.begin(value, f"trial-{index}", noise_reduction_enabled=True, current=lambda: True)
            registry.mark_ready(ticket)
            registry.claim(ticket.token)
            assert registry.release(ticket)
            tokens.append(ticket.token)
        assert len(registry._consumed_releases) == 32
        with pytest.raises(preview_module.VoicePreviewIsolationError, match="preview_invalid"):
            registry.release_owned(tokens[0], value)
        assert registry.release_owned(tokens[-1], value)
        clock[0] = 29
        successor = registry.begin(value, "successor", noise_reduction_enabled=True, current=lambda: True)
        registry.mark_ready(successor)
        clock[0] = 30
        with pytest.raises(preview_module.VoicePreviewIsolationError, match="preview_invalid"):
            registry.release_owned(tokens[-1], value)
        assert registry._ticket is successor
        registry.claim(successor.token)
        registry.release(successor)
        assert len(registry._consumed_releases) == 1
        # The callback is dropped with the consumed ticket; the receipt has
        # just a weak owner and deadline, not a hidden runtime reference.
        owner_ref = weakref.ref(value)
        await cleanup(value)
        del value
        gc.collect()
        assert owner_ref() is None
        registry._prune_consumed_releases()
        assert not registry._consumed_releases
    finally:
        if "value" in locals():
            await cleanup(value)


async def test_unclaimed_or_http_released_ticket_keeps_owner_cleanup_receipt(registry):
    value = manager()
    try:
        for claim, by_token in [(False, False), (True, True)]:
            result = await value._handle_voice_identity_control(
                {"event": "preview_begin", "request_id": "cancelled"}, connection_id="producer-a")
            assert result["ok"]
            ticket = registry._ticket
            if claim:
                registry.claim(ticket.token)
            assert registry.release(ticket.token if by_token else ticket)
            assert registry._consumed_releases
            retry = await value._handle_voice_identity_control(
                {"event": "preview_end", "request_id": "repeat", "token": ticket.token}, connection_id="producer-a")
            assert retry["ok"] is True
    finally:
        await cleanup(value)


async def test_disconnect_release_is_bound_to_original_connection_and_cannot_release_successor(registry):
    value = manager()
    try:
        first = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "first"}, connection_id="producer-a")
        assert first["ok"]
        assert not registry.release_connection(value, "other-connection")
        assert registry.release_connection(value, "producer-a")
        value._voice_lease_connection_id = "producer-b"
        second = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "second"}, connection_id="producer-b")
        assert second["ok"]
        assert not registry.release_connection(value, "producer-a")
        assert registry._ticket.token == second["token"]
    finally:
        registry.release(registry._ticket)
        await cleanup(value)


@pytest.mark.parametrize("input_mode", ["audio", "text"])
async def test_route_start_during_preview_uses_explicit_failure_and_text_revocation(registry, input_mode):
    owner, value = manager("blocked"), manager("blocked")
    for inactive in (owner, value):
        inactive._voice_lease_synchronized = False
        inactive._voice_lease_owner = None
    ticket = registry.begin(owner, "preview", noise_reduction_enabled=True, current=lambda: True)
    registry.mark_ready(ticket)
    failed = AsyncMock(return_value=True)
    value._fail_closed_voice_route = failed
    value._send_to_voice_owner = AsyncMock()
    generation = value._asr_route_operation_generation
    try:
        await value._start_independent_asr_if_enabled(input_mode)
        assert value._asr_route_mode == "blocked"
        if input_mode == "audio":
            failed.assert_not_awaited()
            assert value._asr_route_operation_generation == generation
            assert "VOICE_INPUT_PREVIEW_BUSY" in value._send_to_voice_owner.await_args.args[0]["message"]
        else:
            assert failed.await_args.args[0] == "text_session_active"
    finally:
        registry.release(ticket)
        await cleanup(owner)
        await cleanup(value)


async def test_own_route_start_cannot_invalidate_real_preview_ticket(registry):
    value = manager()
    value._send_to_voice_owner = AsyncMock()
    try:
        result = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "real"}, connection_id="producer-a")
        ticket = registry._ticket
        assert result["ok"] and value._voice_lease_owner == "none"
        generation = value._asr_route_operation_generation
        await value._start_independent_asr_if_enabled("audio")
        assert value._asr_route_operation_generation == generation
        ticket.validate_current()
        value._voice_input_noise_reduction_enabled = not value._voice_input_noise_reduction_enabled
        assert registry.release_owned(ticket.token, value)
        assert not registry.is_manager_isolated(value)
    finally:
        registry.release(registry._ticket)
        await cleanup(value)


async def test_readiness_controls_reject_concurrent_retry_instead_of_queueing(registry, monkeypatch):
    value = manager()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def waiting(manager, message, result, connection_id):
        entered.set()
        await finish.wait()
        return {**result, "ok": True}
    monkeypatch.setattr(readiness_module.VoiceReadinessControl, "_retry", waiting)
    first = asyncio.create_task(value._handle_voice_identity_control(
        {"event": "activation_retry", "request_id": "first"}, connection_id="producer-a"))
    try:
        await entered.wait()
        result = await value._handle_voice_identity_control(
            {"event": "activation_retry", "request_id": "second"}, connection_id="producer-a")
        assert result["ok"] is False and result["reason"] == "preview_busy"
        finish.set()
        assert (await first)["ok"]
    finally:
        finish.set()
        await asyncio.gather(first, return_exceptions=True)
        await cleanup(value)


@pytest.mark.parametrize("token", [None, 123, "声纹", "x" * 129])
async def test_malformed_cleanup_capability_keeps_actual_core_ticket_isolated(registry, token):
    value = manager()
    try:
        begin = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "trial"}, connection_id="producer-a")
        assert begin["ok"]
        ticket = registry._ticket
        end = await value._handle_voice_identity_control(
            {"event": "preview_end", "request_id": "end", "token": token}, connection_id="producer-a")
        assert not end["ok"] and end["reason"] == "preview_invalid"
        assert registry._ticket is ticket and registry.is_manager_isolated(value)
        assert not value._voice_input_accepts_pcm()
    finally:
        registry.release(registry._ticket)
        await cleanup(value)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_preview_retires_actual_producer_and_never_reopens_on_release(registry, route):
    value = manager(route)
    try:
        result = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "trial-a"}, connection_id="producer-a"
        )
        assert result["ok"] is True
        assert result["token"]
        assert 0 < result["ttl_seconds"] <= registry.TTL_SECONDS
        assert value._asr_route_mode == "blocked"
        assert not value._voice_input_accepts_pcm()
        pcm = b"\x01\x00" * 160
        await value._route_microphone_audio(pcm, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
        release = await value._handle_voice_identity_control(
            {"event": "preview_end", "request_id": "end-a", "token": result["token"]},
            connection_id="producer-a",
        )
        assert release["ok"] is True
        assert not registry.is_manager_isolated(value)
        assert not value._voice_input_accepts_pcm()
        await value._route_microphone_audio(pcm, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        await cleanup(value)


async def test_another_live_producer_cannot_be_silenced_by_trial_request(registry):
    owner, other = manager(), manager()
    try:
        result = await owner._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "trial"}, connection_id="producer-a"
        )
        assert result == {"event": "preview_begin", "request_id": "trial", "ok": False, "reason": "preview_owner_active"}
        assert owner._asr_route_mode == other._asr_route_mode == "native"
        assert owner._voice_input_accepts_pcm() and other._voice_input_accepts_pcm()
    finally:
        await cleanup(owner)
        await cleanup(other)


@pytest.mark.parametrize("replacement", ["session", "lease", "noise", "operation"])
async def test_late_close_cannot_revoke_successor_or_publish_old_ticket(registry, replacement):
    value = manager()
    entered, release = asyncio.Event(), asyncio.Event()

    class ClosingRuntime:
        async def close(self):
            entered.set()
            await release.wait()

    value._voice_session_activation_runtime = ClosingRuntime()
    task = asyncio.create_task(value._handle_voice_identity_control(
        {"event": "preview_begin", "request_id": "trial"}, connection_id="producer-a"
    ))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if replacement == "session":
            value.session = SimpleNamespace(stream_audio=AsyncMock())
        elif replacement == "lease":
            value._voice_lease_generation += 1
        elif replacement == "noise":
            value._voice_input_noise_reduction_enabled = not value._voice_input_noise_reduction_enabled
        else:
            value._begin_asr_route_operation()
        value._voice_lease_owner = "core"
        value._voice_lease_synchronized = True
        value._voice_input_suppressed = False
        release.set()
        result = await asyncio.wait_for(task, 2)
        assert result["ok"] is False
        assert result["reason"] == "preview_owner_changed"
        assert value._voice_lease_owner == "core"
        assert value._voice_lease_synchronized is True
        assert not registry.is_manager_isolated(value)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await cleanup(value)


async def test_cancel_during_actual_close_keeps_producer_retired_after_reservation_release(registry, monkeypatch):
    value = manager()
    entered, release = asyncio.Event(), asyncio.Event()
    original = value._voice_input_registry.wait_idle

    async def blocked_idle():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(value._voice_input_registry, "wait_idle", blocked_idle)
    task = asyncio.create_task(value._handle_voice_identity_control(
        {"event": "preview_begin", "request_id": "trial"}, connection_id="producer-a"
    ))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not registry.is_manager_isolated(value)
        assert value._asr_route_mode == "blocked"
        assert not value._voice_input_accepts_pcm()
        await value._route_microphone_audio(b"\x01\x00" * 160, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
    finally:
        release.set()
        await cleanup(value)


async def test_expired_ticket_never_reopens_retired_microphone(registry):
    value = manager()
    clock = [0.0]
    registry.now = lambda: clock[0]
    try:
        result = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "trial"}, connection_id="producer-a"
        )
        assert result["ok"]
        clock[0] = registry.TTL_SECONDS
        assert not registry.is_manager_isolated(value)
        assert not value._voice_input_accepts_pcm()
        end = await value._handle_voice_identity_control(
            {"event": "preview_end", "request_id": "end", "token": result["token"]}, connection_id="producer-a"
        )
        assert end["ok"] is False
        assert end["reason"] == "preview_invalid"
    finally:
        await cleanup(value)


async def test_activation_retry_prepares_new_runtime_but_requires_fresh_owner_evidence(registry):
    value = manager()
    factory = _CoreActivationFactory()
    factory.enforce = True
    try:
        await value.set_voice_session_activation_factory(factory, activation_generation="profile", activation_required=True)
        result = await value._handle_voice_identity_control(retry_message(value), connection_id="producer-a")
        assert result["ok"] is True
        assert value._voice_session_activation_runtime.state is ActivationState.WAITING
        assert not value._voice_session_activation_degraded
        assert len(factory.runtimes) == 1
        await value._route_microphone_audio(b"\xd0\x07" * 160, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        await cleanup(value)


@pytest.mark.parametrize("key", ["session_id", "microphone_generation", "route_generation", "profile_revision", "permission_revision"])
async def test_retry_rejects_stale_identity_before_retiring_live_authority(registry, key):
    value = manager()
    factory = _CoreActivationFactory()
    factory.enforce = True
    try:
        await value.set_voice_session_activation_factory(factory, activation_generation="profile", activation_required=True)
        request = retry_message(value)
        request[key] = "stale" if isinstance(request[key], str) else request[key] + 1
        before = value._capture_voice_session_activation_generation()
        result = await value._handle_voice_identity_control(request, connection_id="producer-a")
        assert result["reason"] == "activation_session_changed"
        assert value._capture_voice_session_activation_generation() == before
        assert factory.runtimes == []
    finally:
        await cleanup(value)


async def test_handler_cancellation_is_request_error_and_completed_begin_for_replaced_socket_is_released(registry):
    value = manager()
    socket = _EventWebSocket([])
    owns = [True]
    original = value._handle_voice_identity_control

    async def replaced(message, **kwargs):
        result = await original(message, **kwargs)
        owns[0] = False
        return result

    value._handle_voice_identity_control = replaced
    try:
        await router._dispatch_voice_identity_control(value, socket,
            {"event": "preview_begin", "request_id": "trial"},
            connection_id="producer-a", owns_voice=lambda: owns[0])
        details = json.loads(json.loads(socket.sent_text[-1])["message"])["details"]
        assert details["reason"] == "preview_owner_changed"
        assert "token" not in details
        assert not registry.is_manager_isolated(value)
        assert not value._voice_input_accepts_pcm()
        owns[0] = True

        async def cancelled(*_args, **_kwargs):
            raise asyncio.CancelledError()

        value._handle_voice_identity_control = cancelled
        await router._dispatch_voice_identity_control(value, socket,
            {"event": "preview_begin", "request_id": "cancelled"},
            connection_id="producer-a", owns_voice=lambda: owns[0])
        details = json.loads(json.loads(socket.sent_text[-1])["message"])["details"]
        assert details["reason"] == "voice_control_cancelled"
    finally:
        await cleanup(value)


class EndpointRuntime(_ProtocolManager, _Runtime):
    stream_data = StreamingMixin.stream_data

    def __init__(self, route):
        _Runtime.__init__(self)
        _ProtocolManager.__init__(self)
        self._voice_lease_synchronized = True
        self._voice_lease_owner = "core"
        self._voice_input_suppressed = False
        self._voice_lease_generation = 1
        self._set_microphone_route(route)
        self.session = SimpleNamespace(stream_audio=AsyncMock())
        self._asr_runtime.submit = AsyncMock(return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED))
        self.is_active = True
        self.is_hot_swap_imminent = False
        self.is_flushing_hot_swap_cache = False

    def _fire_task(self, coroutine):
        return asyncio.create_task(coroutine)

    def _should_drop_live_vision_stream(self, _input_type):
        return False


async def test_endpoint_cancellation_during_control_retirement_still_cleans_connection(registry, monkeypatch):
    from utils.asyncio_retirement import await_retirement
    value = EndpointRuntime("native")
    closing, finish = asyncio.Event(), asyncio.Event()
    async def waiting_control(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            closing.set()
            await await_retirement(finish.wait())
    value._handle_voice_identity_control = waiting_control
    socket = _EventWebSocket([
        {"action": "voice_input_control", "event": "sync", "generation": 1, "owner": "core"},
        {"action": "voice_identity_control", "event": "activation_retry", "request_id": "retry"},
    ])
    _install_protocol_endpoint(monkeypatch, manager=value, websocket=socket)
    count = router._ws_active_count.get("Lan", 0)
    task = asyncio.create_task(router.websocket_endpoint(socket, "Lan"))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert router._ws_active_count["Lan"] == count
        assert value.cleanup_calls == 1
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await cleanup(value)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_websocket_receives_pcm_and_stop_while_activation_retry_waits(registry, monkeypatch, route):
    value = EndpointRuntime(route)
    entered, released = asyncio.Event(), asyncio.Event()
    async def waiting_control(message, *, connection_id):
        value._voice_session_activation_degraded = True
        entered.set()
        await released.wait()
        return {"event": message["event"], "request_id": message["request_id"], "ok": True}
    original_control = value._handle_voice_input_control
    async def stop_control(event, generation, **kwargs):
        if event == "release":
            assert entered.is_set()
            released.set()
        return await original_control(event, generation, **kwargs)
    value._handle_voice_identity_control = waiting_control
    value._handle_voice_input_control = stop_control
    socket = _EventWebSocket([
        {"action": "voice_input_control", "event": "sync", "generation": 1,
         "owner": "core", "hard_muted": False, "focus_suppressed": False},
        {"action": "voice_identity_control", "event": "activation_retry", "request_id": "retry"},
        {"action": "stream_data", "input_type": "audio", "sample_rate_hz": 16000, "data": [2000] * 160},
        {"action": "voice_input_control", "event": "release", "generation": 1},
    ])
    _install_protocol_endpoint(monkeypatch, manager=value, websocket=socket)
    try:
        await asyncio.wait_for(router.websocket_endpoint(socket, "Lan"), 2)
        assert released.is_set()
        assert value._audio_stream_queue.empty()
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        await cleanup(value)


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("binary", [False, True])
async def test_actual_endpoint_delivers_pcm_before_preview_and_blocks_after_it(registry, monkeypatch, route, binary):
    value = EndpointRuntime(route)
    # Replace only local acoustic processing and downstream transport. Real
    # endpoint, queue, ingress identity, worker and activation gates remain.
    value._voice_input_audio_pipeline.process = AsyncMock(
        return_value=ProcessedVoiceFrame(pcm16=b"\xd0\x07" * 160,
                                         sample_rate_hz=16000,
                                         speech_probability=1.0,
                                         rnnoise_available=False))
    target = value.session.stream_audio if route == "native" else value._asr_runtime.submit
    delivered = asyncio.Event()
    async def received(*_args, **_kwargs):
        delivered.set()
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED) if route == "independent" else None
    target.side_effect = received
    socket = _EventWebSocket([
        {"action": "voice_input_control", "event": "sync", "generation": 1,
         "owner": "core", "hard_muted": False, "focus_suppressed": False},
        {"action": "voice_identity_control", "event": "preview_begin", "request_id": "trial"},
    ])
    frame = ({"type": "websocket.receive", "bytes": struct.pack("<4sI", b"NEKO", 16000) + b"\xd0\x07" * 160}
             if binary else {"type": "websocket.receive", "text": json.dumps({
                 "action": "stream_data", "input_type": "audio", "sample_rate_hz": 16000, "data": [2000] * 160})})
    socket.events.insert(1, frame)
    socket.events.insert(-1, frame)
    original_receive = socket.receive
    async def receive_after_delivery():
        if socket.events[0].get("text", "").find('"preview_begin"') >= 0:
            await asyncio.wait_for(delivered.wait(), 2)
        return await original_receive()
    socket.receive = receive_after_delivery
    _install_protocol_endpoint(monkeypatch, manager=value, websocket=socket)
    try:
        await router.websocket_endpoint(socket, "Lan")
        target.assert_awaited_once()
        if route == "native":
            assert target.await_args.args == (b"\xd0\x07" * 160,)
            value._asr_runtime.submit.assert_not_awaited()
        else:
            assert target.await_args.args[0].pcm16 == b"\xd0\x07" * 160
            value.session.stream_audio.assert_not_awaited()
        assert value._audio_stream_queue.empty()
        assert not value._voice_input_accepts_pcm()
    finally:
        await cleanup(value)


@pytest.mark.parametrize("failure", ["create", "prepare"])
async def test_retry_failure_preserves_unavailable_reason_and_never_reopens_input(registry, failure):
    value = manager()
    factory = _CoreActivationFactory()
    factory.enforce = True
    original_create = factory.create
    def create(*args, **kwargs):
        if failure == "create":
            raise RuntimeError("controlled creation failure")
        runtime = original_create(*args, **kwargs)
        runtime.prepare = AsyncMock(side_effect=RuntimeError("controlled prepare failure"))
        return runtime
    factory.create = create
    try:
        await value.set_voice_session_activation_factory(factory, activation_generation="profile", activation_required=True)
        result = await value._handle_voice_identity_control(retry_message(value), connection_id="producer-a")
        reason = "runtime_creation_failed" if failure == "create" else "prepare_failed"
        assert result["ok"] is False
        assert result["reason"] == reason
        assert value._voice_session_activation_status[1:] == (ActivationState.UNAVAILABLE, reason)
        assert value._voice_session_activation_runtime is None
        assert value._voice_session_activation_degraded is True
        await value._route_microphone_audio(b"\xd0\x07" * 160, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
        if factory.runtimes:
            assert factory.runtimes[0].state is ActivationState.CLOSED
    finally:
        await cleanup(value)


@pytest.mark.parametrize("fault", ["blocked", "native_closed", "prefix_cleanup"])
async def test_retry_cannot_reuse_uncertain_or_retired_downstream(registry, fault):
    value = manager()
    factory = _CoreActivationFactory()
    factory.enforce = True
    try:
        await value.set_voice_session_activation_factory(factory, activation_generation="profile", activation_required=True)
        if fault == "blocked":
            value._set_microphone_route("blocked")
        elif fault == "native_closed":
            value.session_closed_by_server = True
        else:
            value._voice_activation_prefix_cleanup = object()
        before = value._capture_voice_session_activation_generation()
        result = await value._handle_voice_identity_control(retry_message(value), connection_id="producer-a")
        assert result["reason"] == "voice_session_restart_required"
        assert result["ok"] is False
        assert factory.runtimes == []
        assert value._capture_voice_session_activation_generation() == before
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        value._voice_activation_prefix_cleanup = None
        await cleanup(value)


@pytest.mark.parametrize("cancel", [False, True])
async def test_retry_cancel_or_successor_during_prepare_retires_candidate_without_adoption(registry, cancel):
    value = manager()
    factory = _CoreActivationFactory()
    factory.enforce = True
    entered, release = asyncio.Event(), asyncio.Event()
    original_create = factory.create
    def create(*args, **kwargs):
        runtime = original_create(*args, **kwargs)
        original_prepare = runtime.prepare
        async def prepare():
            entered.set()
            await release.wait()
            await original_prepare()
        runtime.prepare = prepare
        return runtime
    factory.create = create
    await value.set_voice_session_activation_factory(factory, activation_generation="profile", activation_required=True)
    task = asyncio.create_task(value._handle_voice_identity_control(retry_message(value), connection_id="producer-a"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert value._voice_session_activation_runtime is None
            assert value._voice_session_activation_status[1:] == (ActivationState.UNAVAILABLE, "prepare_failed")
            assert value._voice_session_activation_degraded is True
        else:
            successor = object()
            value.session = SimpleNamespace(stream_audio=AsyncMock())
            value._voice_session_activation_runtime = successor
            release.set()
            result = await asyncio.wait_for(task, 2)
            assert result["reason"] == "activation_session_changed"
            assert value._voice_session_activation_runtime is successor
            value._voice_session_activation_runtime = None
        await asyncio.gather(*tuple(value._core_asr_cleanup_tasks), return_exceptions=True)
        assert factory.runtimes[0].state is ActivationState.CLOSED
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await cleanup(value)


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("binary", [False, True])
async def test_actual_websocket_json_and_binary_pcm_are_blocked_after_owner_preview_begin(registry, monkeypatch, route, binary):
    value = EndpointRuntime(route)
    completed = asyncio.Event()
    class PreviewSocket(_EventWebSocket):
        async def receive(self):
            if self.events[0]["type"] == "websocket.disconnect":
                await asyncio.wait_for(completed.wait(), 2)
            return await super().receive()
        async def send_text(self, payload):
            await super().send_text(payload)
            if "VOICE_IDENTITY_CONTROL_RESULT" in payload:
                completed.set()
    socket = PreviewSocket([
        {"action": "voice_input_control", "event": "sync", "generation": 1,
         "owner": "core", "hard_muted": False, "focus_suppressed": False},
        {"action": "voice_identity_control", "event": "preview_begin", "request_id": "trial"},
    ])
    pcm = b"\xd0\x07" * 160
    frame = ({"type": "websocket.receive", "bytes": struct.pack("<4sI", b"NEKO", 16000) + pcm}
             if binary else {"type": "websocket.receive", "text": json.dumps({
                 "action": "stream_data", "input_type": "audio", "sample_rate_hz": 16000, "data": [2000] * 160})})
    socket.events.insert(-1, frame)
    _install_protocol_endpoint(monkeypatch, manager=value, websocket=socket)
    try:
        await router.websocket_endpoint(socket, "Lan")
        statuses = [json.loads(json.loads(payload)["message"]) for payload in socket.sent_text
                    if json.loads(payload).get("type") == "status"]
        result = next(status["details"] for status in statuses if status.get("code") == "VOICE_IDENTITY_CONTROL_RESULT")
        assert result["ok"] is True
        assert result["token"]
        assert value._audio_stream_queue.empty()
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        await cleanup(value)


async def test_unclaimed_display_websocket_cannot_request_preview(registry, monkeypatch):
    value = EndpointRuntime("native")
    socket = _EventWebSocket([{ "action": "voice_identity_control", "event": "preview_begin", "request_id": "viewer" }])
    _install_protocol_endpoint(monkeypatch, manager=value, websocket=socket)
    try:
        await router.websocket_endpoint(socket, "Lan")
        status = json.loads(json.loads(socket.sent_text[-1])["message"])
        assert status["details"]["reason"] == "preview_owner_changed"
        assert not registry.is_manager_isolated(value)
        assert value._asr_route_mode == "native"
    finally:
        await cleanup(value)


async def test_actual_preview_timeout_releases_ticket_without_restoring_producer(registry, monkeypatch):
    value = manager()
    entered, release = asyncio.Event(), asyncio.Event()
    original_timeout = asyncio.timeout
    monkeypatch.setattr(readiness_module.asyncio, "timeout", lambda seconds:
                        original_timeout(0.03 if seconds == 5.0 else seconds))

    class ClosingRuntime:
        async def close(self):
            entered.set()
            await release.wait()

    value._voice_session_activation_runtime = ClosingRuntime()
    try:
        result = await value._handle_voice_identity_control(
            {"event": "preview_begin", "request_id": "timeout-trial"}, connection_id="producer-a")
        assert entered.is_set()
        assert result["reason"] == "voice_cleanup_timeout"
        assert "token" not in result
        assert not registry.is_manager_isolated(value)
        assert not value._voice_input_accepts_pcm()
        assert value._asr_route_mode == "blocked"
        await value._route_microphone_audio(b"\xd0\x07" * 160, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        release.set()
        await cleanup(value)


async def test_actual_retry_prepare_timeout_publishes_unavailable_and_retires_candidate(registry, monkeypatch):
    value = manager()
    factory = _CoreActivationFactory()
    factory.enforce = True
    release = asyncio.Event()
    original_create, original_timeout = factory.create, asyncio.timeout
    monkeypatch.setattr(readiness_module.asyncio, "timeout", lambda seconds:
                        original_timeout(0.03 if seconds == 35.0 else seconds))

    def create(*args, **kwargs):
        runtime = original_create(*args, **kwargs)
        runtime.prepare = AsyncMock(side_effect=release.wait)
        return runtime

    factory.create = create
    try:
        await value.set_voice_session_activation_factory(factory, activation_generation="profile", activation_required=True)
        result = await value._handle_voice_identity_control(retry_message(value), connection_id="producer-a")
        assert result["reason"] == "voice_cleanup_timeout"
        assert value._voice_session_activation_status[1:] == (ActivationState.UNAVAILABLE, "voice_cleanup_timeout")
        assert value._voice_session_activation_degraded is True
        assert value._voice_session_activation_runtime is None
        await asyncio.gather(*tuple(value._core_asr_cleanup_tasks), return_exceptions=True)
        assert factory.runtimes[0].state is ActivationState.CLOSED
        await value._route_microphone_audio(b"\xd0\x07" * 160, sample_rate_hz=16000)
        value.session.stream_audio.assert_not_awaited()
        value._asr_runtime.submit.assert_not_awaited()
    finally:
        release.set()
        await cleanup(value)
