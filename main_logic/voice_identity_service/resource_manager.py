"""Resource operations and privacy-safe trial audio, outside enrollment leases."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from copy import deepcopy
from dataclasses import dataclass, field
import importlib.util
import multiprocessing
from multiprocessing.connection import Connection
from pathlib import Path
import platform
import sys
import threading
import time
from typing import Callable
import uuid
from utils.asyncio_retirement import await_retirement

from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS, wake_word_model_dir, wake_word_preference
from main_logic.asr_client.endpointing.asset_manifest import AssetManifestError, resolve_verified_assets
from main_logic.asr_client.speaker_shadow.asset_manifest import CampPlusAssetError, resolve_verified_campplus_asset
from .wake_word_bundle import (
    ASSETS, WakeWordBundleError, default_cache_root, install_bundle, resolve_cached_model_dir,
    is_bundle_version_published,
)
from main_logic.voice_input.wake_word.sherpa_backend import (
    SUPPORTED_RUNTIME_VERSION, SherpaWakeWordConfig, validate_wake_word_resources,
)
from main_logic.voice_input.wake_word.errors import WakeWordFailureReason, safe_wake_word_reason

from .audio_contract import OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID
from .enrollment import (
    EnrollmentAudioError, SileroEnrollmentSpeechValidator, enrollment_audio_diagnostics,
)
from .enrollment_audio import EnrollmentAudioNormalizationError, EnrollmentAudioNormalizer
from .state import VoiceIdentityEffectiveReason
from .preference_worker import save_preference_worker
from .publication_worker import publish_resource_worker

MAX_TRIAL_PCM_BYTES = 48_000 * 3 * 2
RESOURCE_STATES = frozenset({"unchecked", "ready", "missing", "unavailable"})


class VoiceResourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _resource(state: str, reason: str | None, required: bool) -> dict:
    return {"state": state, "reason": reason, "required": required}


async def _check_audio(pcm16: bytes, nr_enabled: bool) -> dict:
    validator = SileroEnrollmentSpeechValidator()
    normalized = b""
    try:
        normalizer = EnrollmentAudioNormalizer(nr_enabled=nr_enabled)
        normalized = await normalizer.normalize(pcm16, sample_rate_hz=48_000, target_samples=len(pcm16) // 6)
        diagnostics = enrollment_audio_diagnostics(normalized)
        if not await validator.load():
            raise VoiceResourceError("model_unavailable")
        try:
            await validator.validate_pcm16(normalized)
        except EnrollmentAudioError as exc:
            return {"accepted": False, "reason": exc.code, "diagnostics": diagnostics}
        return {"accepted": True, "reason": None, "diagnostics": diagnostics}
    except EnrollmentAudioNormalizationError as exc:
        return {"accepted": False, "reason": exc.code, "diagnostics": None}
    finally:
        await validator.close()
        normalized = b""


async def _prepare_wake(path: Path) -> None:
    # This function runs only in a disposable resource process. Do not spawn a
    # nested detector child which could survive cancellation of that process.
    validate_wake_word_resources(SherpaWakeWordConfig(model_dir=str(path), keywords=DEFAULT_WAKE_WORD_KEYWORDS))


async def _prepare_resources(nr_enabled: bool, wake_path: str | None) -> dict:
    from main_logic.asr_client.speaker_shadow.campplus import CampPlusEmbeddingModel
    from utils.audio_processor import AudioProcessor
    result = {}
    model = CampPlusEmbeddingModel()
    try:
        result["campp"] = _resource("ready" if await asyncio.to_thread(model.load) else "unavailable", None, True)
        if result["campp"]["state"] != "ready":
            result["campp"]["reason"] = "model_unavailable"
    finally:
        await asyncio.to_thread(model.close)
    validator = SileroEnrollmentSpeechValidator()
    try:
        ready = await validator.load()
        result["silero"] = _resource("ready" if ready else "unavailable", None if ready else "model_unavailable", True)
    finally:
        await validator.close()
    processor = AudioProcessor(noise_reduce_enabled=nr_enabled)
    try:
        processor.process_chunk(b"\0" * 960)
        ready = not nr_enabled or processor.rnnoise_available
        result["noise_reduction"] = _resource("ready" if ready else "unavailable", None if ready else "audio_processing_unavailable", nr_enabled)
    finally:
        processor.close()
    try:
        import sherpa_onnx
        ready = (sherpa_onnx.__version__ == SUPPORTED_RUNTIME_VERSION
                 and sherpa_onnx.version == SUPPORTED_RUNTIME_VERSION)
        result["wake_runtime"] = _resource("ready" if ready else "unavailable",
                                             None if ready else WakeWordFailureReason.RUNTIME_FIX_REQUIRED.value, False)
    except Exception as exc:
        result["wake_runtime"] = _resource("unavailable", safe_wake_word_reason(exc), False)
    if wake_path:
        try:
            await _prepare_wake(Path(wake_path))
        except Exception as exc:
            # Wake backend only exports stable typed error codes.
            reason = safe_wake_word_reason(exc)
            result["wake_model"] = _resource("unavailable", reason, False)
            result["wake_runtime"] = _resource("unavailable", reason, False)
        else:
            result["wake_model"] = _resource("ready", None, False)
            result["wake_runtime"] = _resource("ready", None, False)
    return result


def _resource_worker(connection: Connection, kind: str, nr_enabled: bool, wake_path: str | None, pcm16: bytes):
    """Disposable worker makes native hangs, cancellation and shutdown bounded."""
    try:
        if kind in {"audio", "prepare"}:
            from utils import audio_processor
            audio_processor.DEBUG_SAVE_AUDIO = False
        if kind == "download":
            import sherpa_onnx
            if (sherpa_onnx.__version__ != SUPPORTED_RUNTIME_VERSION
                    or sherpa_onnx.version != SUPPORTED_RUNTIME_VERSION):
                raise VoiceResourceError(WakeWordFailureReason.RUNTIME_FIX_REQUIRED.value)
            def validate(path):
                asyncio.run(_prepare_wake(path))
            directory = install_bundle(Path(wake_path), validate=validate, publish=False)
            result = {"version": directory.name}
        else:
            result = asyncio.run(_check_audio(pcm16, nr_enabled) if kind == "audio" else _prepare_resources(nr_enabled, wake_path))
        connection.send({"ok": True, "result": result})
    except Exception as exc:
        reason = (exc.code if isinstance(exc, (WakeWordBundleError, VoiceResourceError)) else
                  "resource_storage_unavailable" if isinstance(exc, OSError) else
                  "resource_worker_failed" if kind in {"audio", "prepare"} else safe_wake_word_reason(exc))
        connection.send({"ok": False, "reason": reason})
    finally:
        pcm16 = b""
        connection.close()


async def _stop_process(process) -> None:
    if process.is_alive():
        process.terminate()
    await asyncio.to_thread(process.join, 1.0)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, 1.0)
    if process.is_alive():
        raise VoiceResourceError("resource_worker_shutdown_failed")
    process.close()


async def _run_worker(kind: str, nr_enabled: bool, wake_path: str | None = None, pcm16: bytes = b"", timeout: float = 30.0) -> dict:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    target = {"preference": save_preference_worker, "publish": publish_resource_worker}.get(kind, _resource_worker)
    process = context.Process(target=target, args=(child, kind, nr_enabled, wake_path, pcm16), daemon=False)
    started = False
    try:
        try:
            try:
                await await_retirement(asyncio.to_thread(process.start))
            except asyncio.CancelledError:
                started = process.pid is not None
                raise
            started = True
        except Exception as exc:
            raise VoiceResourceError("resource_worker_start_failed") from exc
        child.close()
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                ready = parent.poll()
            except (OSError, EOFError) as exc:
                raise VoiceResourceError("resource_worker_failed") from exc
            if ready:
                break
            if not process.is_alive():
                raise VoiceResourceError("resource_worker_failed")
            if asyncio.get_running_loop().time() >= deadline:
                raise VoiceResourceError("resource_prepare_timeout")
            await asyncio.sleep(0.025)
        try:
            message = parent.recv()
            if not message["ok"]:
                raise VoiceResourceError(message["reason"])
            return message["result"]
        except (EOFError, OSError, ValueError, KeyError, TypeError) as exc:
            raise VoiceResourceError("resource_worker_failed") from exc
    finally:
        parent.close()
        child.close()
        if started:
            cleanup = asyncio.create_task(_stop_process(process))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()


@dataclass
class _Operation:
    operation_id: str
    kind: str
    state: str = "pending"
    reason: str | None = None
    result: dict | None = None
    task: asyncio.Task | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    commit_lock: threading.Lock = field(default_factory=threading.Lock)
    committing: bool = False
    committed: bool = False
    refresh_task: asyncio.Task | None = None
    reservation_deadline: float | None = None

    def snapshot(self) -> dict:
        return {"operation_id": self.operation_id, "kind": self.kind, "state": self.state, "reason": self.reason,
                "progress": None, "result": deepcopy(self.result), "committed": self.committed}


class VoiceResourceManager:
    """One service owner, one operation, bounded recent results and trial worker."""

    def __init__(self, noise_reduction_snapshot: Callable[[], bool], *, cache_root: Path | None = None,
                 on_ready: Callable[[str], Awaitable[None]] | None = None):
        self._noise_reduction_snapshot = noise_reduction_snapshot
        try:
            self._cache_root = cache_root or default_cache_root()
        except WakeWordBundleError:
            self._cache_root = None
        self._operations: dict[str, _Operation] = {}
        self._current: _Operation | None = None
        self._prepared: dict = {}
        self._closed = False
        self._trial_lock = asyncio.Lock()
        self._trial_task: asyncio.Task | None = None
        self._on_ready = on_ready
        self._prepared_noise_reduction: bool | None = None
        self._prepared_revision = 0
        self._now = time.monotonic

    async def save_preference(self, enabled: bool) -> dict:
        if self._closed:
            raise VoiceResourceError("runtime_degraded")
        if type(enabled) is not bool:
            raise VoiceResourceError("invalid_enabled")
        if self._cache_root is None:
            raise VoiceResourceError("resource_storage_unavailable")
        return await _run_worker("preference", False, str(self._cache_root), b"\1" if enabled else b"\0", timeout=5)

    def owns(self, operation_id: str) -> bool:
        return (not self._closed and self._current is not None and self._current.operation_id == operation_id
                and not self._current.cancel.is_set())

    async def resources(self) -> dict:
        nr = self._noise_reduction_snapshot()
        revision = self._prepared_revision
        snapshot = await asyncio.to_thread(self._snapshot_sync, nr)
        if self._closed:
            raise VoiceResourceError("runtime_degraded")
        if nr is not self._noise_reduction_snapshot():
            raise VoiceResourceError("audio_contract_changed")
        # All mutable runtime state belongs to the event loop. Discovery may
        # overlap publication, but must never certify a stale prepared version.
        if revision == self._prepared_revision and self._prepared_noise_reduction is nr:
            for name, value in self._prepared.items():
                if snapshot["resources"][name]["state"] == "unchecked" or name == "noise_reduction":
                    snapshot["resources"][name].update(deepcopy(value), required=snapshot["resources"][name]["required"])
        snapshot["can_enroll"] = all(snapshot["resources"][name]["state"] == "ready" for name in ("campp", "silero", "noise_reduction"))
        snapshot["operation"] = self._current.snapshot() if self._current is not None else None
        if snapshot["repair_action"] != "app_repair":
            snapshot["repair_action"] = ("source_models" if any(snapshot["resources"][name]["state"] != "ready"
                                         for name in ("campp", "silero")) else "source_runtime")
        return snapshot

    def _snapshot_sync(self, nr: bool) -> dict:
        resources = {}
        try:
            resolve_verified_campplus_asset()
            resources["campp"] = _resource("unchecked", None, True)
        except CampPlusAssetError:
            resources["campp"] = _resource("missing", "model_unavailable", True)
        try:
            resolve_verified_assets(("silero_vad.onnx",))
            resources["silero"] = _resource("unchecked", None, True)
        except AssetManifestError:
            resources["silero"] = _resource("missing", "model_unavailable", True)
        resources["noise_reduction"] = _resource("unchecked" if nr else "ready", None, nr)
        explicit = wake_word_model_dir()
        preference = wake_word_preference(self._cache_root)
        enabled = preference["enabled"] if preference["reason"] is None else True
        cache_reason = None
        try:
            installed = resolve_cached_model_dir(self._cache_root)
        except WakeWordBundleError:
            installed = None
            cache_reason = WakeWordFailureReason.MODEL_INVALID.value
        path = Path(explicit) if explicit else installed
        present = path is not None and all((path / name).is_file() for name in ASSETS)
        resources["wake_model"] = _resource("unchecked" if present else "missing", None if present else "WAKE_WORD_MODEL_MISSING", enabled)
        if not explicit and cache_reason is not None:
            resources["wake_model"] = _resource("unavailable", cache_reason, enabled)
        if preference["reason"] is not None:
            resources["wake_model"] = _resource("unavailable", WakeWordFailureReason.PREFERENCE_UNAVAILABLE.value, True)
        # Discovery never imports native code, and works in frozen builds which
        # can omit wheel metadata. prepare checks Python AND native versions.
        present_runtime = importlib.util.find_spec("sherpa_onnx") is not None
        resources["wake_runtime"] = _resource("unchecked" if present_runtime else "missing",
                                                 None if present_runtime else WakeWordFailureReason.RUNTIME_MISSING.value, enabled)
        packaged = getattr(sys, "frozen", False) or "__compiled__" in globals()
        repair_action = "app_repair" if packaged else (
            "source_models" if any(resources[name]["state"] != "ready" for name in ("campp", "silero")) else "source_runtime"
        )
        for name, value in resources.items():
            value["repair_action"] = "app_repair" if packaged else ("source_runtime" if name == "wake_runtime" else "source_models")
        return {"resources": resources, "wake_enabled": enabled, "wake_configured": bool(explicit),
                "repair_action": repair_action,
                "wake_managed": preference["managed"], "wake_preference_reason": (
                    None if preference["reason"] is None else WakeWordFailureReason.PREFERENCE_UNAVAILABLE.value),
                "can_enroll": all(resources[name]["state"] == "ready" for name in ("campp", "silero", "noise_reduction")),
                "audio_contract": {"contract_id": OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID, "revision": 1, "noise_reduction_enabled": nr},
                "operation": None}

    def start(self, kind: str) -> dict:
        # Preserve the original empty-body endpoint for older clients.
        reservation = self.reserve(kind)
        return self.start_reserved(reservation["operation_id"])

    def _expire_reservations(self) -> None:
        for operation in self._operations.values():
            if operation.state == "reserved" and self._now() >= operation.reservation_deadline:
                operation.state = "cancelled"
                operation.reason = "operation_cancelled"
                operation.cancel.set()

    def reserve(self, kind: str) -> dict:
        if self._closed:
            raise VoiceResourceError("runtime_degraded")
        if kind not in {"prepare", "download"}:
            raise VoiceResourceError("invalid_resource_operation")
        if self._current is not None and self._current.task is not None and not self._current.task.done():
            raise VoiceResourceError("resource_operation_busy")
        self._expire_reservations()
        while len(self._operations) >= 16:
            removable = next((key for key, value in self._operations.items()
                              if value is not self._current and value.state in {"succeeded", "failed", "cancelled"}
                              and (value.task is None or value.task.done())), None)
            if removable is None:
                raise VoiceResourceError("resource_operation_busy")
            del self._operations[removable]
        operation = _Operation(str(uuid.uuid4()), kind, state="reserved", reservation_deadline=self._now() + 30)
        self._operations[operation.operation_id] = operation
        return operation.snapshot()

    def _get_operation(self, operation_id: str) -> _Operation:
        if type(operation_id) is not str or len(operation_id) > 128:
            raise VoiceResourceError("invalid_resource_operation")
        self._expire_reservations()
        operation = self._operations.get(operation_id)
        if operation is None:
            raise VoiceResourceError("invalid_resource_operation")
        return operation

    def start_reserved(self, operation_id: str) -> dict:
        if self._closed:
            raise VoiceResourceError("runtime_degraded")
        operation = self._get_operation(operation_id)
        # An expired, cancelled, completed or already-started ID is never a new
        # operation. Evicted IDs are rejected rather than recreated.
        if operation.state != "reserved":
            return operation.snapshot()
        if self._current is not None and self._current.task is not None and not self._current.task.done():
            raise VoiceResourceError("resource_operation_busy")
        self._current = operation
        operation.state = "pending"
        operation.task = asyncio.create_task(self._execute(operation), name=f"voice-resource-{operation.kind}")
        return operation.snapshot()

    def operation(self, operation_id: str) -> dict:
        operation = self._get_operation(operation_id)
        return operation.snapshot()

    async def _execute(self, operation: _Operation):
        operation.state = "running"
        try:
            if operation.kind == "download":
                if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
                    raise VoiceResourceError(WakeWordFailureReason.PLATFORM_UNSUPPORTED.value)
                if self._cache_root is None:
                    raise VoiceResourceError("resource_storage_unavailable")
                result = await _run_worker("download", False, str(self._cache_root), timeout=180)
                if not self.owns(operation.operation_id):
                    raise VoiceResourceError("operation_cancelled")
                # No await separates the ownership fence from this boundary.
                # The child only staged an immutable version; this owner now
                # completes publication/refresh and reports their actual result.
                operation.committing = True
                commit = asyncio.create_task(self._commit_download(operation, result["version"]))
                while not commit.done():
                    try:
                        await asyncio.shield(commit)
                    except asyncio.CancelledError:
                        continue
                commit.result()
                result = operation.result
            else:
                nr = self._noise_reduction_snapshot()
                preference = await asyncio.to_thread(wake_word_preference, self._cache_root)
                path = wake_word_model_dir()
                cache_reason = None
                if path is None:
                    try:
                        cached = await asyncio.to_thread(resolve_cached_model_dir, self._cache_root)
                    except WakeWordBundleError:
                        cached = None
                        cache_reason = WakeWordFailureReason.MODEL_INVALID.value
                    path = str(cached) if cached is not None else None
                result = await _run_worker("prepare", nr, path)
                if cache_reason is not None:
                    result["wake_model"] = _resource("unavailable", cache_reason, preference["enabled"])
                if nr != self._noise_reduction_snapshot():
                    raise VoiceResourceError("audio_contract_changed")
            if operation is not self._current or operation.cancel.is_set() or self._closed:
                raise VoiceResourceError("runtime_degraded" if operation.committed else "operation_cancelled")
            if operation.kind == "prepare" and self._on_ready is not None:
                await self._on_ready(operation.operation_id)
            if not self.owns(operation.operation_id):
                raise VoiceResourceError("operation_cancelled")
            if operation.kind == "prepare":
                if nr != self._noise_reduction_snapshot():
                    raise VoiceResourceError("audio_contract_changed")
                self._prepared = deepcopy(result)
                self._prepared_noise_reduction = nr
                self._prepared_revision += 1
            operation.result = deepcopy(result)
            operation.state = "succeeded"
        except asyncio.CancelledError:
            operation.state = "failed" if operation.committed else "cancelled"
            operation.reason = "runtime_degraded" if operation.committed else "operation_cancelled"
        except TimeoutError:
            operation.state = "failed"
            operation.reason = "resource_prepare_timeout"
        except Exception as exc:
            operation.reason = getattr(exc, "code", None) or "resource_prepare_failed"
            if operation.committed and operation.reason == "operation_cancelled":
                operation.reason = "runtime_degraded"
            operation.state = "cancelled" if operation.reason == "operation_cancelled" else "failed"

    async def _commit_download(self, operation: _Operation, version: str) -> None:
        """Retire a bounded publisher before resolving any cancellation request."""
        self._prepared = {name: value for name, value in self._prepared.items() if name != "wake_model"}
        self._prepared_revision += 1
        try:
            await _run_worker("publish", False, str(self._cache_root), pcm16=version.encode("ascii"), timeout=5)
        except VoiceResourceError:
            # A publisher can exit after atomic replace but before IPC receipt.
            # Confirm the actual bounded pointer instead of claiming rollback.
            if not await asyncio.to_thread(is_bundle_version_published, self._cache_root, version):
                raise
        operation.committed = True
        operation.result = {"installed": True}
        if self._closed:
            raise VoiceResourceError("runtime_degraded")
        if self._on_ready is not None:
            refresh = asyncio.create_task(self._on_ready(operation.operation_id))
            operation.refresh_task = refresh
            try:
                await asyncio.wait_for(refresh, timeout=5)
            except TimeoutError as exc:
                # The resource pointer is already committed. A runtime refresh
                # deadline must not masquerade as preparation/download failure.
                raise VoiceResourceError(VoiceIdentityEffectiveReason.RUNTIME_DEGRADED.value) from exc
            finally:
                if operation.refresh_task is refresh:
                    operation.refresh_task = None

    async def cancel(self, operation_id: str) -> dict:
        operation = self._get_operation(operation_id)
        return await self._cancel_operation(operation)

    async def _cancel_operation(self, operation: _Operation) -> dict:
        if operation.state == "reserved":
            operation.cancel.set()
            operation.state = "cancelled"
            operation.reason = "operation_cancelled"
        if operation.task is not None and not operation.task.done():
            if not operation.committing:
                with operation.commit_lock:
                    operation.cancel.set()
                operation.task.cancel()
            cancellation = None
            while not operation.task.done():
                try:
                    await asyncio.shield(operation.task)
                except asyncio.CancelledError as exc:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        cancellation = exc
            if operation.task.cancelled():
                # A task cancelled before its first instruction cannot execute
                # the coroutine's own cancellation handler.
                operation.state = "cancelled"
                operation.reason = "operation_cancelled"
            else:
                operation.task.result()
            if cancellation is not None:
                raise cancellation
        return operation.snapshot()

    async def check_audio(self, pcm16: bytes, *, noise_reduction_enabled: bool) -> dict:
        if self._closed:
            raise VoiceResourceError("runtime_degraded")
        if type(pcm16) is not bytes or len(pcm16) % 960 or not 144_000 <= len(pcm16) <= MAX_TRIAL_PCM_BYTES:
            raise VoiceResourceError("invalid_pcm")
        if self._trial_lock.locked():
            raise VoiceResourceError("resource_operation_busy")
        async with self._trial_lock:
            nr = noise_reduction_enabled
            if type(nr) is not bool or nr != self._noise_reduction_snapshot():
                raise VoiceResourceError("audio_contract_changed")
            task = asyncio.create_task(_run_worker("audio", nr, pcm16=pcm16))
            self._trial_task = task
            try:
                result = await task
            finally:
                if self._trial_task is task:
                    self._trial_task = None
            if self._closed or nr != self._noise_reduction_snapshot():
                raise VoiceResourceError("audio_contract_changed")
            result["audio_contract"] = {"contract_id": OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID, "revision": 1, "noise_reduction_enabled": nr}
            return result

    async def close(self):
        self._closed = True
        try:
            if self._current is not None:
                refresh = self._current.refresh_task
                if refresh is not None and not refresh.done():
                    # Retire the service-lock waiter before committed delivery.
                    refresh.cancel()
                await self._cancel_operation(self._current)
        finally:
            trial = self._trial_task
            if trial is not None and not trial.done():
                trial.cancel()
                try:
                    await asyncio.shield(trial)
                except asyncio.CancelledError:
                    if not trial.cancelled():
                        raise
