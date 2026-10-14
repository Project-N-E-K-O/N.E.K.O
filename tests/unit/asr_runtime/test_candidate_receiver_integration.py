"""Actual Core/ASR/session/TCP-receiver PCM linkage, using research algorithms.

No real provider recognition, hardware latency, or speaker accuracy claim.
The only external endpoint is an ephemeral local TCP server owned by the test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import time
from types import SimpleNamespace

import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _AsrWorkerEvent, _RealtimeAsrSessionImpl
from main_logic.asr_client.delivery import begin_transport_write, complete_transport_write
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.asr_client.endpointing.throttle_policy import ThrottleAction
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.core.asr_runtime import VoiceSessionActivationRouteContext
from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_input.activation import ActivationGeneration, AudioFrame, OutputCommit
from main_logic.voice_input.interception_events import InterceptionOutputKind
from tests.unit.asr_runtime.test_active_session_interception import _Runtime as CoreHarness
from tests.unit.asr_client.test_candidate_rejection_runtime import _callbacks, _install_active_candidate
from tests.unit.voice_identity_service.test_candidate_runtime import CandidateScorer, RawScorerMustNotRun, WindowSource, make_selector
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Classifier, _Tse

pytestmark = pytest.mark.runtime


class Detector:
    """Controlled local detector; initial turn is explicitly already ACTIVE."""
    admission_enabled = True

    def endpointing_ready(self, _token):
        return True

    async def feed(self, pcm16, **_kwargs):
        return SimpleNamespace(endpointing_available=True, throttle_available=True, throttle_action=ThrottleAction.PROCESS_PCM, admission_records=())

    async def reset(self):
        pass

    async def release_deferred_turn(self, *_args, **_kwargs):
        pass

    async def close(self):
        pass

    async def replace_speaker_verifier(self, *_args, **_kwargs):
        pass

    def observe_provider_audio(self, *_args, **_kwargs):
        pass


class ObservedCore(CoreHarness):
    def __init__(self):
        super().__init__()
        self.observed_events = []

    async def _route_interception_events(self, result, *args, **kwargs):
        # Passive observation delegates the actual output writer unchanged.
        self.observed_events.extend(result.events)
        return await super()._route_interception_events(result, *args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_prefix", [True, False])
async def test_candidate_pcm_reaches_real_tcp_receiver_without_guest_or_raw_fallback(tmp_path, owner_prefix):
    core = ObservedCore()
    received = []
    server_closed = asyncio.Event()
    commit_read = asyncio.Event()
    final_delivered = asyncio.Event()
    segment = 0

    async def receive(reader, writer):
        nonlocal segment
        try:
            while True:
                header_size = struct.unpack("!I", await reader.readexactly(4))[0]
                metadata = json.loads(await reader.readexactly(header_size))
                pcm = await reader.readexactly(metadata["audio_bytes"])
                if metadata["kind"] == "audio":
                    # This record is formed only after the receiver read bytes.
                    received.append((metadata, pcm))
                elif metadata["kind"] == "commit":
                    commit_read.set()
                    segment += 1
                writer.write(b"R")
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            server_closed.set()

    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    wire_count = 0

    async def worker(requests, responses, _api_key, _config):
        nonlocal wire_count
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await responses.put(_AsrWorkerEvent(kind="ready", generation=0))
        try:
            while True:
                request = await requests.get()
                try:
                    if request.kind == "shutdown":
                        await responses.put(_AsrWorkerEvent(kind="closed", generation=request.generation))
                        return
                    if request.kind not in {"audio", "commit"}:
                        continue
                    metadata = {"kind": request.kind, "audio_bytes": len(request.audio), "segment_id": segment}
                    evidence = None
                    if request.kind == "audio":
                        events = [item for item in core.observed_events if item.kind is InterceptionOutputKind.AUDIO]
                        event = events[wire_count]
                        metadata.update(delivery_id=f"audio-{wire_count}", capture_id="capture-1", start=event.start_sample, end=event.end_sample)
                        wire_count += 1
                        evidence = begin_transport_write(requests, delivery_spans=request.delivery_spans)
                    body = json.dumps(metadata).encode()
                    writer.write(struct.pack("!I", len(body)) + body + request.audio)
                    await writer.drain()
                    # This is loopback receiver_read, not provider confirmation.
                    assert await reader.readexactly(1) == b"R"
                    if evidence is not None:
                        complete_transport_write(evidence, len(request.audio), generation=request.generation, buffer_epoch=request.buffer_epoch, provider="local-fixture", delivery_spans=request.delivery_spans, takes_ownership=True)
                    else:
                        await responses.put(_AsrWorkerEvent(kind="final", generation=request.generation, buffer_epoch=request.buffer_epoch, utterance_id=request.utterance_id, text="controlled receiver prefix"))
                finally:
                    requests.task_done()
        finally:
            writer.close()
            await writer.wait_closed()

    callbacks = _callbacks()
    independent = IndependentAsrRuntime(callbacks)

    async def on_final(text):
        await independent._handle_independent_asr_final(text, independent._asr_session_epoch, "glm")
        await independent.wait_transcript_idle()
        final_delivered.set()

    async def on_error(message):
        raise AssertionError(message)

    session = _RealtimeAsrSessionImpl(worker_fn=worker, api_key="", config=AsrSessionConfig(), on_input_transcript=on_final, on_connection_error=on_error, provider_policy=resolve_provider_policy("glm", "manual"))
    await session.connect()
    detector = Detector()
    _, _, token = _install_active_candidate(independent, detector)
    independent._asr_audio_dispatcher.abort()
    independent._asr_session = session
    assert independent._asr_audio_dispatcher.activate(token, session, b"")
    core._asr_runtime = independent
    core._asr_route_mode = "independent"
    core._independent_asr_provider = "glm"
    core._voice_lease_connection_id = token.ingress.connection_id
    core._voice_lease_generation = token.ingress.lease_generation
    core._microphone_route_generation = token.ingress.route_generation
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    core._capture_voice_session_activation_generation = lambda: generation
    core._voice_input_accepts_pcm = lambda: True
    backend = CandidateScorer()
    candidate_selector = make_selector(backend)
    source = WindowSource(lambda start: ((100, -100) if start < 800 else (-100,)) if owner_prefix else (-100,))
    factory = PrewireInterceptionFactory(_config(), score_backend=RawScorerMustNotRun(), classifier=_Classifier(), tse_factory=lambda _: _Tse(), candidate_factory=lambda _: candidate_selector, candidate_source_factory=lambda _: source)
    assert await core.set_active_session_interception_factory(factory)
    context = VoiceSessionActivationRouteContext(speech_probability=1, rnnoise_available=True, rnnoise_evidence=None, ingress_token=token.ingress, captured_at=time.time())
    try:
        for sequence in range(7):
            frame = AudioFrame(sequence=sequence, sample_start=sequence * 400, sample_end=(sequence + 1) * 400, captured_at=time.time(), sample_rate=16000, pcm=b"\x9c\xff" * 400, generation=generation, context=context)
            assert await core._route_voice_session_activation_output(frame, generation) is OutputCommit.LOCAL_ACCEPTED
        assert await core.finish_active_session_interception(generation=generation, context=context) is OutputCommit.LOCAL_ACCEPTED
        await independent._asr_audio_dispatcher.wait_idle()
        if owner_prefix:
            await asyncio.wait_for(commit_read.wait(), 1)
            await asyncio.wait_for(final_delivered.wait(), 1)
        assert core.observed_events[-1].kind is InterceptionOutputKind.END
        audio_events = [event for event in core.observed_events if event.kind is InterceptionOutputKind.AUDIO]
        assert len(received) == len(audio_events) == (2 if owner_prefix else 0)
        assert b"".join(pcm for _, pcm in received) == (b"\x64\x00" * 800 if owner_prefix else b"")
        assert all(pcm != b"\x9c\xff" * ((metadata["end"] - metadata["start"])) for metadata, pcm in received)
        assert session.transport_written_audio_bytes == (1600 if owner_prefix else 0)
        assert any(event.kind is InterceptionOutputKind.GAP for event in core.observed_events)
        manifest = {"schema_version": 1, "sample_rate": 16000, "receiver_scope": "actual loopback PCM receiver; controlled fixture; no acoustic/provider acceptance", "expected": [], "received": []}
        audio_index = 0
        for event in core.observed_events:
            if event.kind is InterceptionOutputKind.END:
                continue
            entry = {"delivery_id": f"audio-{audio_index}" if event.kind is InterceptionOutputKind.AUDIO else f"gap-{event.sequence}", "capture_id": "capture-1", "start": event.start_sample, "end": event.end_sample, "kind": event.kind.value}
            if event.kind is InterceptionOutputKind.AUDIO:
                path = tmp_path / f"expected-{audio_index}.pcm"
                path.write_bytes(event.pcm16)
                entry.update(identity="owner_confirmed", candidate_id="0", segment_id=0, path=str(path), sha256=hashlib.sha256(event.pcm16).hexdigest())
                audio_index += 1
            manifest["expected"].append(entry)
        for index, (metadata, pcm) in enumerate(received):
            path = tmp_path / f"received-{index}.pcm"
            path.write_bytes(pcm)
            manifest["received"].append({key: metadata[key] for key in ("delivery_id", "capture_id", "start", "end", "segment_id")} | {"path": str(path), "sha256": hashlib.sha256(pcm).hexdigest(), "receipt": "receiver_read"})
        (tmp_path / "receiver-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        from scripts.voice_interception_evaluation import audit_receiver
        # Validator independently checks actual read PCM and continuous gaps.
        audit_receiver(manifest, tmp_path)
    finally:
        await core.set_active_session_interception_factory(None, interception_required=False)
        await independent.close()
        await session.close()
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(server_closed.wait(), 1)
