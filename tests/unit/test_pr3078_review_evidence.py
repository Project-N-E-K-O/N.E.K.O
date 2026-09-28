"""Differential audit against PR #3078's actual base, without provider network I/O."""
import ast
import asyncio
import json
import subprocess
from types import ModuleType
from unittest.mock import AsyncMock

import pytest
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

import main_logic.core.asr_runtime as asr_module
import main_logic.core.lifecycle as lifecycle_module
import main_logic.omni_realtime_client._gemini_support as gemini_module
import main_logic.voice_turn.audio_input as pipeline_module
import main_logic.voice_identity_service.service as service_module
from app.main_server.voice_identity_runtime import OwnerVoiceRuntimeRegistry
from tests.support.asr_fakes import _Runtime
from tests.support.voice_identity_fakes import _service, _pcm, _embedding
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from tests.unit.voice_identity_service.test_profile_store import _TestKeyProtector
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity.profile import SpeakerProfile
from tests.unit.test_hot_swap_cancellation import _FakeSession, _make_swap_manager, _drain_task

BASE = "16a48f711f6842e3fbd181c41675459bb3c025bc"
# Keep the audit differential pinned to the PR's actual head.  The previous
# merge commit predates the final Gemini delivery fix and therefore exercised
# an intermediate, intentionally superseded implementation.
HEAD = "52556bde9a824ad25354ad15ab3ffbff4b1ed9c4"


def revision_method(revision, path, class_name, method_name, namespace):
    try:
        source = subprocess.check_output(
            ["git", "show", f"{revision}:{path}"], encoding="utf-8"
        )
    except subprocess.CalledProcessError:
        pytest.skip(f"historical revision {revision} is unavailable in this checkout")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method_name)
    scope = dict(namespace)
    exec(compile(ast.Module(body=[method], type_ignores=[]), f"{revision}:{path}", "exec"), scope)
    return scope[method_name]


def swap_method(revision):
    return revision_method(revision, "main_logic/core/lifecycle.py", "LifecycleMixin", "_perform_final_swap_sequence", vars(lifecycle_module))


def manager():
    mgr = _make_swap_manager()
    mgr._init_asr_runtime_state()
    mgr._voice_lease_synchronized = True
    mgr._voice_lease_owner = "core"
    mgr._voice_input_suppressed = False
    mgr._set_microphone_route("native")
    mgr.core_api_type = "gemini"
    mgr._independent_asr_route_key = "gemini"
    mgr.send_status = AsyncMock()
    mgr.is_hot_swap_imminent = True
    mgr.is_active = True
    mgr.session_closed_by_server = False
    return mgr


class GeminiSession(_FakeSession):
    def __init__(self, name, revision, *, closed=False):
        super().__init__(name)
        self._connection_generation = 1
        self._fatal_error_occurred = False
        self._gemini_session = self
        self.sent = []
        self.wire_closed = closed
        self._send = revision_method(revision, "main_logic/omni_realtime_client/_gemini_support.py", "_GeminiMixin", "_stream_audio_gemini", vars(gemini_module))

    async def send_realtime_input(self, *, audio):
        if self.wire_closed:
            raise ConnectionClosedOK(Close(1000, "normal"), Close(1000, "normal"), True)
        self.sent.append(audio["data"])

    async def stream_audio(self, pcm):
        await self._send(self, pcm)


@pytest.mark.asyncio
async def test_c1_closed_gemini_then_real_swap_differential():
    outcomes = {}
    for label, revision in (("BASE", BASE), ("HEAD", HEAD)):
        mgr = manager()
        old = GeminiSession("old", revision, closed=True)
        new = GeminiSession("new", revision)
        mgr.session, mgr.pending_session = old, new
        route = revision_method(revision, "main_logic/core/asr_runtime.py", "AsrRuntimeMixin", "_route_microphone_audio", vars(asr_module))
        try:
            await route(mgr, b"\x10\x00" * 160, sample_rate_hz=16000)
            latched = mgr.session_closed_by_server
            await swap_method(revision)(mgr)
            assert mgr.session is new
            await route(mgr, b"\x20\x00" * 160, sample_rate_hz=16000)
            outcomes[label] = {"latched_before_swap": latched, "latched_after_swap": mgr.session_closed_by_server, "successor_bytes": sum(map(len, new.sent))}
        finally:
            await _drain_task(mgr.message_handler_task)
    print("C1", json.dumps(outcomes))
    # Ordinary clients must retain the BASE successor-session delivery
    # contract even when the retired Gemini connection closes normally.
    assert outcomes["BASE"]["successor_bytes"] == 320
    assert outcomes["HEAD"]["successor_bytes"] == 320
    # The current head records the retired connection close so activation
    # recovery can distinguish it from an ordinary transport write.  The
    # completed swap clears that latch before successor delivery above.
    assert outcomes["HEAD"]["latched_before_swap"] is True


@pytest.mark.asyncio
async def test_c9_replacement_close_exception_changes_cleanup():
    outcomes = {}
    for label, revision in (("BASE", BASE), ("HEAD", HEAD)):
        mgr = manager()
        class CancelAtOldClose(_FakeSession):
            async def close(self):
                raise asyncio.CancelledError()
        class BrokenReplacement(_FakeSession):
            async def close(self):
                raise RuntimeError("replacement-close-failed")
        mgr.session = CancelAtOldClose("old")
        mgr.pending_session = BrokenReplacement("new")
        mgr._cleanup_pending_session_resources = AsyncMock()
        mgr._reset_preparation_state = AsyncMock()
        error = None
        try:
            await swap_method(revision)(mgr)
        except RuntimeError as exc:
            error = str(exc)
        outcomes[label] = {"error": error, "cleanup_calls": mgr._cleanup_pending_session_resources.await_count, "reset_calls": mgr._reset_preparation_state.await_count}
    print("C9", json.dumps(outcomes))
    assert outcomes["BASE"] == {"error": None, "cleanup_calls": 1, "reset_calls": 1}
    # The current head keeps replacement-close failures best-effort, matching
    # the base cleanup contract while still completing the cancellation path.
    assert outcomes["HEAD"] == {"error": None, "cleanup_calls": 1, "reset_calls": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("sample", [0, 4000])
async def test_original_head_pipeline_with_nr_off_already_uses_energy(sample):
    pipeline = pipeline_module.VoiceInputAudioPipeline(nr_enabled=False)
    original_process = revision_method(HEAD, "main_logic/voice_turn/audio_input.py", "VoiceInputAudioPipeline", "process", vars(pipeline_module))
    try:
        frame = await original_process(pipeline, sample.to_bytes(2, "little", signed=True) * 4800, sample_rate_hz=48000)
        assert frame.pcm16
        assert frame.speech_probability is None
        activity = asr_module.AsrRuntimeMixin._voice_session_activation_has_speech(frame.pcm16, speech_probability=frame.speech_probability)
        print("NR_OFF", {"input_sample": sample, "probability": frame.speech_probability, "voice_activity": activity})
        assert activity is (sample != 0)
    finally:
        await pipeline.close()


def write_legacy_profile(path):
    try:
        source = subprocess.check_output(
            ["git", "show", f"{BASE}:main_logic/voice_identity_service/profile_store.py"],
            encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        pytest.skip(f"historical revision {BASE} is unavailable in this checkout")
    module = ModuleType("audit_base_profile_store")
    exec(compile(source, "base_profile_store.py", "exec"), vars(module))
    store = module.VoiceIdentityProfileStore(path, key_protector=_TestKeyProtector())
    reference = SpeakerReference(SpeakerModelIdentity(service_module.CAMPPLUS_MODEL_ID, service_module.CAMPPLUS_MODEL_REVISION, service_module.CAMPPLUS_EMBEDDING_DIM), _embedding())
    profile = SpeakerProfile("old-profile", reference)
    try:
        store.save(profile)
    finally:
        profile.close()
        reference.close()
    return store


@pytest.mark.asyncio
async def test_c4_c7_legacy_disabled_profile_and_reenrollment(tmp_path):
    outcomes = {}
    for label, revision in (("BASE", BASE), ("HEAD", HEAD)):
        root = tmp_path / label
        service, _, activations, _ = _service(root)
        old_store = write_legacy_profile(root / "voice_identity.profile")
        await service._preference_store.asave(False)
        if label == "BASE":
            service._profile_store = old_store
        initialize = revision_method(revision, "main_logic/voice_identity_service/service.py", "VoiceIdentityService", "initialize", vars(service_module))
        try:
            status = await initialize(service)
            outcomes[label] = {"has_profile": status.state.has_profile, "requested": status.state.requested_enabled, "reason": str(status.state.effective_reason), "startup_activation_calls": len(activations)}
            if label == "HEAD":
                # The historical HEAD initializer predates the explicit
                # rejected-profile marker used by the current runtime.
                service._rejected_profile_on_initialize = True
                enrollment = await service.start_enrollment()
                completed = await service.complete_enrollment(enrollment.enrollment_id, "new-profile", _pcm())
                outcomes[label]["requested_after_reenrollment"] = completed.state.requested_enabled
        finally:
            await service.close()
    print("C4_C7", json.dumps(outcomes))
    assert outcomes["BASE"]["has_profile"] is True
    assert outcomes["HEAD"]["has_profile"] is False
    assert outcomes["HEAD"]["startup_activation_calls"] == 0
    assert outcomes["HEAD"]["requested_after_reenrollment"] is False


@pytest.mark.asyncio
async def test_legacy_reenrollment_preserves_disabled_preference(tmp_path):
    service, _, _, _ = _service(tmp_path)
    await service._preference_store.asave(False)
    await service.initialize()
    service._rejected_profile_on_initialize = True
    enrollment = await service.start_enrollment()
    completed = await service.complete_enrollment(
        enrollment.enrollment_id, "new-profile", _pcm()
    )
    assert completed.state.requested_enabled is False
    assert str(completed.state.effective_reason) == "disabled"
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested, mode, expected_blocked", [(False, "enforce", False), (True, "enforce", True), (True, "off", False)])
async def test_legacy_upgrade_registry_gate(tmp_path, requested, mode, expected_blocked):
    service, _, _, _ = _service(tmp_path, runtime_mode=mode)
    write_legacy_profile(tmp_path / "voice_identity.profile")
    await service._preference_store.asave(requested)
    registry = OwnerVoiceRuntimeRegistry(enforce=mode == "enforce")
    service._activation_callback = registry.activate
    managers = [_Runtime(), _Runtime()]
    for mgr in managers:
        mgr._asr_route_mode = "native"
        mgr.session.stream_audio = AsyncMock()
        await registry.register_manager(mgr)
    try:
        status = await service.initialize()
        for mgr in managers:
            await mgr._route_microphone_audio(b"\x10\x00" * 160, sample_rate_hz=16000)
        sent = [mgr.session.stream_audio.await_count for mgr in managers]
        print("UPGRADE_GATE", {"requested": requested, "mode": mode, "reason": str(status.state.effective_reason), "manager_send_counts": sent})
        assert sent == ([0, 0] if expected_blocked else [1, 1])
    finally:
        await service.close()
        await registry.close()


@pytest.mark.asyncio
async def test_c9_cancel_before_replacement_close_first_step():
    outcomes = {}
    for label, revision in (("BASE", BASE), ("HEAD", HEAD)):
        mgr = manager()
        class CancelAtOldClose(_FakeSession):
            async def close(self):
                # An external cancellation is already queued when the swap
                # enters its cancellation handler and retires the replacement.
                asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
                raise asyncio.CancelledError()
        class Replacement(_FakeSession):
            async def close(self):
                self.closed = True
                await asyncio.sleep(0)
        mgr.session = CancelAtOldClose("old")
        target = Replacement("new")
        mgr.pending_session = target
        task = asyncio.create_task(swap_method(revision)(mgr))
        await asyncio.gather(task, return_exceptions=True)
        outcomes[label] = target.closed
    print("C9_CANCEL_CLOSE_STARTED", outcomes)
    assert outcomes == {"BASE": True, "HEAD": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["native", "independent"])
async def test_ordinary_ingress_payload_and_status_differential(route):
    outcomes = {}
    frames = [bytes([marker, 0]) * 160 for marker in (10, 20, 30)]
    for label, revision in (("BASE", BASE), ("HEAD", HEAD)):
        mgr = _Runtime()
        mgr._asr_route_mode = route
        mgr.session.stream_audio = AsyncMock()
        mgr._asr_runtime.submit = AsyncMock(return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED))
        submit = revision_method(revision, "main_logic/core/asr_runtime.py", "AsrRuntimeMixin", "_route_microphone_audio", vars(asr_module))
        for frame in frames:
            await submit(mgr, frame, sample_rate_hz=16000)
        if route == "native":
            delivered = [call.args[0] for call in mgr.session.stream_audio.await_args_list]
        else:
            calls = mgr._asr_runtime.submit.await_args_list
            delivered = [call.args[0].pcm16 for call in calls]
            assert all("preserve_prefix" not in call.kwargs for call in calls)
        assert delivered == frames
        assert mgr._voice_session_activation_runtime is None
        outcomes[label] = {"bytes": sum(map(len, delivered)), "status_calls": mgr.send_status.await_count, "route": mgr._asr_route_mode}
    print("BYPASS", route, outcomes)
    assert outcomes["BASE"] == outcomes["HEAD"]


@pytest.mark.asyncio
async def test_current_head_c1_successful_swap_clears_close_latch():
    mgr = manager()
    old = GeminiSession("old", HEAD, closed=True)
    new = GeminiSession("new", HEAD)
    mgr.session, mgr.pending_session = old, new
    await mgr._perform_final_swap_sequence()
    assert mgr.session is new
    assert mgr.session_closed_by_server is False
    route = revision_method(HEAD, "main_logic/core/asr_runtime.py", "AsrRuntimeMixin", "_route_microphone_audio", vars(asr_module))
    await route(mgr, b"\x20\x00" * 160, sample_rate_hz=16000)
    assert sum(map(len, new.sent)) == 320


@pytest.mark.asyncio
async def test_current_head_c9_close_failure_still_resets_swap():
    mgr = manager()
    class Old(_FakeSession):
        async def close(self):
            raise asyncio.CancelledError()
    class Broken(_FakeSession):
        async def close(self):
            raise RuntimeError("close failed")
    mgr.session, mgr.pending_session = Old("old"), Broken("new")
    mgr._cleanup_pending_session_resources = AsyncMock()
    mgr._reset_preparation_state = AsyncMock()
    await mgr._perform_final_swap_sequence()
    assert mgr._cleanup_pending_session_resources.await_count == 1
    assert mgr._reset_preparation_state.await_count == 1
    assert mgr.is_hot_swap_imminent is False

pytestmark = pytest.mark.unit_fast
