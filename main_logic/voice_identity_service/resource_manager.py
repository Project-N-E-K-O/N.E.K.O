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
from config.resource_file_lock import ResourceFileLockBusy

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
from .runtime_installation import (
    RuntimeBundleError, load_runtime, runtime_present, runtime_repair_supported,
    stage_runtime, publish_runtime, runtime_version_published, runtime_cache_root,
)

from .audio_contract import OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID
from .enrollment import (
    EnrollmentAudioError, SileroEnrollmentSpeechValidator, enrollment_audio_diagnostics,
)
from .enrollment_audio import EnrollmentAudioNormalizationError, EnrollmentAudioNormalizer
from .state import VoiceIdentityEffectiveReason
from .preference_worker import save_preference_worker
from .publication_worker import publish_resource_worker
from .source_model_repair import SourceModelRepairUnavailable, source_model_repair_available

MAX_TRIAL_PCM_BYTES = 48_000 * 3 * 2
# A microphone check only needs a short human utterance (10 × 32 ms).
# Formal enrollment keeps the validator's 47-window default.
TRIAL_MINIMUM_SPEECH_WINDOWS = 10
RESOURCE_STATES = frozenset({"unchecked", "ready", "missing", "unavailable"})
RESOURCE_PROGRESS_PHASES = frozenset({"checking", "downloading", "verifying", "loading", "refreshing", "complete"})
RESOURCE_PROGRESS_NAMES = frozenset({"campp", "silero", "noise_reduction", "wake_model", "wake_runtime"})


def _validated_progress(value: dict) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("phase"), str) or value["phase"] not in RESOURCE_PROGRESS_PHASES:
        raise VoiceResourceError("resource_worker_failed")
    resource = value.get("resource")
    if resource is not None and (not isinstance(resource, str) or resource not in RESOURCE_PROGRESS_NAMES):
        raise VoiceResourceError("resource_worker_failed")
    received, total, attempt = value.get("downloaded_bytes"), value.get("total_bytes"), value.get("attempt")
    if (received is not None and (type(received) is not int or not 0 <= received <= 2 ** 40)
            or total is not None and (type(total) is not int or not 0 < total <= 2 ** 40)
            or attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 100)
            or value["phase"] == "downloading" and received is None
            or received is not None and total is not None and received > total):
        raise VoiceResourceError("resource_worker_failed")
    return {"phase": value["phase"], "resource": resource, "downloaded_bytes": received, "total_bytes": total, "attempt": attempt}


class VoiceResourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _resource(state: str, reason: str | None, required: bool) -> dict:
    return {"state": state, "reason": reason, "required": required}


async def _check_audio(pcm16: bytes, nr_enabled: bool) -> dict:
    validator = SileroEnrollmentSpeechValidator(minimum_speech_windows=TRIAL_MINIMUM_SPEECH_WINDOWS)
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
    validate_wake_word_resources(SherpaWakeWordConfig(model_dir=str(path), keywords=DEFAULT_WAKE_WORD_KEYWORDS,
                                                   runtime_cache_dir=str(runtime_cache_root())))


async def _prepare_resources(nr_enabled: bool, wake_path: str | None, *, on_progress=None) -> dict:
    from main_logic.asr_client.speaker_shadow.campplus import CampPlusEmbeddingModel
    from utils.audio_processor import AudioProcessor
    result = {}
    def report(resource):
        if on_progress is not None:
            on_progress({"phase": "loading", "resource": resource})
    report("campp")
    model = CampPlusEmbeddingModel()
    try:
        result["campp"] = _resource("ready" if await asyncio.to_thread(model.load) else "unavailable", None, True)
        if result["campp"]["state"] != "ready":
            result["campp"]["reason"] = "model_unavailable"
    finally:
        await asyncio.to_thread(model.close)
    report("silero")
    validator = SileroEnrollmentSpeechValidator()
    try:
        ready = await validator.load()
        result["silero"] = _resource("ready" if ready else "unavailable", None if ready else "model_unavailable", True)
    finally:
        await validator.close()
    report("noise_reduction")
    processor = AudioProcessor(noise_reduce_enabled=nr_enabled)
    try:
        processor.process_chunk(b"\0" * 960)
        ready = not nr_enabled or processor.rnnoise_available
        result["noise_reduction"] = _resource("ready" if ready else "unavailable", None if ready else "audio_processing_unavailable", nr_enabled)
    finally:
        processor.close()
    report("wake_runtime")
    try:
        sherpa_onnx = load_runtime()
        ready = (sherpa_onnx.__version__ == SUPPORTED_RUNTIME_VERSION
                 and sherpa_onnx.version == SUPPORTED_RUNTIME_VERSION)
        result["wake_runtime"] = _resource("ready" if ready else "unavailable",
                                             None if ready else WakeWordFailureReason.RUNTIME_FIX_REQUIRED.value, False)
    except Exception as exc:
        result["wake_runtime"] = _resource("unavailable", safe_wake_word_reason(exc), False)
    if wake_path:
        report("wake_model")
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
    def report(value):
        connection.send({"progress": value})
    try:
        if kind in {"audio", "prepare", "repair", "repair-prepare"}:
            from utils import audio_processor
            audio_processor.DEBUG_SAVE_AUDIO = False
        if kind == "runtime-check":
            try:
                load_runtime()
            except Exception:
                result = {"ready": False}
            else:
                result = {"ready": True}
        elif kind == "runtime-stage":
            result = stage_runtime(root=Path(wake_path), on_progress=report)
        elif kind == "runtime-publish":
            result = publish_runtime(Path(wake_path), pcm16.decode("ascii"))
        elif kind == "download":
            sherpa_onnx = load_runtime()
            if (sherpa_onnx.__version__ != SUPPORTED_RUNTIME_VERSION
                    or sherpa_onnx.version != SUPPORTED_RUNTIME_VERSION):
                raise VoiceResourceError(WakeWordFailureReason.RUNTIME_FIX_REQUIRED.value)
            def validate(path):
                asyncio.run(_prepare_wake(path))
            directory = install_bundle(Path(wake_path), validate=validate, publish=False, on_progress=report)
            result = {"version": directory.name}
        else:
            if kind == "repair":
                from .source_model_repair import repair_source_models
                repair_source_models(on_progress=report)
            result = asyncio.run(_check_audio(pcm16, nr_enabled) if kind == "audio" else _prepare_resources(nr_enabled, wake_path,
                **({"on_progress": report} if kind in {"repair", "repair-prepare"} else {})))
        connection.send({"ok": True, "result": result})
    except Exception as exc:
        reason = (exc.code if isinstance(exc, (WakeWordBundleError, RuntimeBundleError, VoiceResourceError, SourceModelRepairUnavailable)) else
                  "resource_operation_busy" if isinstance(exc, ResourceFileLockBusy) else
                  "resource_model_repair_failed" if kind == "repair" and isinstance(exc, (CampPlusAssetError, AssetManifestError)) else
                  "resource_storage_unavailable" if isinstance(exc, OSError) else
                  "resource_worker_failed" if kind in {"audio", "prepare", "repair", "repair-prepare"} else safe_wake_word_reason(exc))
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


async def _run_worker(kind: str, nr_enabled: bool, wake_path: str | None = None, pcm16: bytes = b"", timeout: float = 30.0,
                      *, on_progress: Callable[[dict], None] | None = None) -> dict:
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
            if asyncio.get_running_loop().time() >= deadline:
                raise VoiceResourceError("resource_prepare_timeout")
            try:
                ready = parent.poll()
            except (OSError, EOFError) as exc:
                raise VoiceResourceError("resource_worker_failed") from exc
            if ready:
                try:
                    message = parent.recv()
                    if not isinstance(message, dict):
                        raise VoiceResourceError("resource_worker_failed")
                    if set(message) == {"progress"}:
                        progress = _validated_progress(message["progress"])
                        if on_progress is not None:
                            on_progress(progress)
                        # Progress neither extends the deadline nor starves cancellation.
                        await asyncio.sleep(0)
                        continue
                    if not message["ok"]:
                        raise VoiceResourceError(message["reason"])
                    result = message["result"]
                    break
                except (EOFError, OSError, ValueError, KeyError, TypeError) as exc:
                    raise VoiceResourceError("resource_worker_failed") from exc
            if not process.is_alive():
                raise VoiceResourceError("resource_worker_failed")
            if asyncio.get_running_loop().time() >= deadline:
                raise VoiceResourceError("resource_prepare_timeout")
            await asyncio.sleep(0.025)
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
    # Return after finally: frozen builds must not drop the worker result.
    return result


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
    progress_detail: dict | None = None
    source_repair: bool = True

    def snapshot(self) -> dict:
        detail = self.progress_detail
        progress = (detail["downloaded_bytes"] / detail["total_bytes"]
                    if self.state == "running" and detail and detail["phase"] == "downloading" and detail["total_bytes"] else None)
        return {"operation_id": self.operation_id, "kind": self.kind, "state": self.state, "reason": self.reason,
                "progress": progress, "progress_detail": deepcopy(detail), "result": deepcopy(self.result), "committed": self.committed}


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
                wake_failure = (name in {"wake_runtime", "wake_model"} and value["state"] == "unavailable"
                                and snapshot["resources"][name]["reason"] != WakeWordFailureReason.PREFERENCE_UNAVAILABLE.value)
                if snapshot["resources"][name]["state"] == "unchecked" or name == "noise_reduction" or wake_failure:
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
        present_runtime = runtime_present() or importlib.util.find_spec("sherpa_onnx") is not None
        resources["wake_runtime"] = _resource("unchecked" if present_runtime else "missing",
                                                 None if present_runtime else WakeWordFailureReason.RUNTIME_MISSING.value, enabled)
        packaged = getattr(sys, "frozen", False) or "__compiled__" in globals()
        repair_action = "app_repair" if packaged else (
            "source_models" if any(resources[name]["state"] != "ready" for name in ("campp", "silero")) else "source_runtime"
        )
        for name, value in resources.items():
            value["repair_action"] = "app_repair" if packaged else ("source_runtime" if name == "wake_runtime" else "source_models")
        if runtime_repair_supported():
            resources["wake_runtime"]["repair_action"] = "prebuilt_runtime"
        return {"resources": resources, "wake_enabled": enabled, "wake_configured": bool(explicit),
                "can_repair_models": not packaged and source_model_repair_available()
                    and any(resources[name]["state"] == "missing" for name in ("campp", "silero")),
                "can_repair_wake_runtime": runtime_repair_supported(),
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
        if kind not in {"prepare", "download", "repair"}:
            raise VoiceResourceError("invalid_resource_operation")
        # Snapshot discovery and the disposable worker validate manifests off
        # the event loop; reservation keeps its existing installation-only gate.
        if (kind == "repair" and not source_model_repair_available(validate_manifests=False)
                and not runtime_repair_supported()):
            raise VoiceResourceError("resource_repair_unavailable")
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
        if kind == "repair":
            operation.source_repair = source_model_repair_available(validate_manifests=False)
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
        def update_progress(value):
            if self.owns(operation.operation_id) and operation.state == "running":
                operation.progress_detail = _validated_progress(value)
        try:
            if operation.kind == "download":
                if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
                    raise VoiceResourceError(WakeWordFailureReason.PLATFORM_UNSUPPORTED.value)
                if self._cache_root is None:
                    raise VoiceResourceError("resource_storage_unavailable")
                result = await _run_worker("download", False, str(self._cache_root), timeout=180, on_progress=update_progress)
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
                wake_failures = {}
                if operation.kind == "repair" and preference["reason"] is None and preference["enabled"]:
                    wake_failures = await self._repair_wake_resources(operation, update_progress)
                    if not self.owns(operation.operation_id):
                        raise VoiceResourceError("operation_cancelled")
                path = wake_word_model_dir()
                cache_reason = None
                if path is None:
                    try:
                        cached = await asyncio.to_thread(resolve_cached_model_dir, self._cache_root)
                    except WakeWordBundleError:
                        cached = None
                        cache_reason = WakeWordFailureReason.MODEL_INVALID.value
                    path = str(cached) if cached is not None else None
                if operation.kind == "repair":
                    result = await _run_worker("repair" if operation.source_repair else "repair-prepare", nr, path,
                                               timeout=420, on_progress=update_progress)
                else:
                    result = await _run_worker("prepare", nr, path)
                result.update(wake_failures)
                if preference["reason"] is not None:
                    # Unknown wake settings fail closed without preventing
                    # independent voiceprint resources from being prepared.
                    result["wake_model"] = _resource("unavailable", WakeWordFailureReason.PREFERENCE_UNAVAILABLE.value, True)
                elif cache_reason is not None and "wake_model" not in wake_failures:
                    result["wake_model"] = _resource("unavailable", cache_reason, preference["enabled"])
                if nr != self._noise_reduction_snapshot():
                    raise VoiceResourceError("audio_contract_changed")
            if operation is not self._current or operation.cancel.is_set() or self._closed:
                raise VoiceResourceError("runtime_degraded" if operation.committed else "operation_cancelled")
            if operation.kind in {"prepare", "repair"} and self._on_ready is not None:
                update_progress({"phase": "refreshing"})
                await self._on_ready(operation.operation_id)
            if not self.owns(operation.operation_id):
                raise VoiceResourceError("operation_cancelled")
            if operation.kind in {"prepare", "repair"}:
                if nr != self._noise_reduction_snapshot():
                    raise VoiceResourceError("audio_contract_changed")
                self._prepared = deepcopy(result)
                self._prepared_noise_reduction = nr
                self._prepared_revision += 1
            operation.result = deepcopy(result)
            operation.progress_detail = _validated_progress({"phase": "complete"})
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

    async def _repair_wake_resources(self, operation: _Operation, update_progress) -> dict:
        """Prepare -> retire worker -> owner-fenced publication -> next step."""
        failures = {}
        def record_failure(exc, *names):
            # These failures indicate ownership, physical retirement or commit
            # state is unresolved. Never continue into another resource worker.
            if exc.code in {"operation_cancelled", "resource_operation_busy", "resource_worker_shutdown_failed",
                            "runtime_degraded", "audio_contract_changed"}:
                raise exc
            failures.update({name: _resource("unavailable", exc.code, True) for name in names})
        if self._cache_root is None:
            record_failure(VoiceResourceError("resource_storage_unavailable"), "wake_runtime", "wake_model")
            return failures
        runtime_root = str(self._cache_root.parent / "wake-runtime")
        update_progress({"phase": "checking", "resource": "wake_runtime"})
        staged = None
        try:
            checked = await _run_worker("runtime-check", False, timeout=30)
            if not self.owns(operation.operation_id):
                raise VoiceResourceError("operation_cancelled")
            if not checked["ready"]:
                staged = await _run_worker("runtime-stage", False, runtime_root, timeout=240, on_progress=update_progress)
        except VoiceResourceError as exc:
            record_failure(exc, "wake_runtime", "wake_model")
            return failures
        if not self.owns(operation.operation_id):
            raise VoiceResourceError("operation_cancelled")
        # Publication errors remain fatal: the commit may already have happened.
        if staged is not None and not staged["installed"]:
            await self._publish_repair_step(operation, "runtime-publish", runtime_root, staged["ticket"].encode("ascii"))
        if not self.owns(operation.operation_id):
            raise VoiceResourceError("operation_cancelled")
        # Managed deployments supply their own model directory. Downloading an
        # unrelated cache cannot repair that path, so validate it normally.
        if wake_word_model_dir() is not None:
            return failures
        try:
            cached = await asyncio.to_thread(resolve_cached_model_dir, self._cache_root)
        except WakeWordBundleError:
            cached = None
        if not self.owns(operation.operation_id):
            raise VoiceResourceError("operation_cancelled")
        if cached is None:
            try:
                staged = await _run_worker("download", False, str(self._cache_root), timeout=180, on_progress=update_progress)
            except VoiceResourceError as exc:
                record_failure(exc, "wake_model")
                return failures
            if not self.owns(operation.operation_id):
                raise VoiceResourceError("operation_cancelled")
            await self._publish_repair_step(operation, "publish", str(self._cache_root), staged["version"].encode("ascii"))
        return failures

    async def _publish_repair_step(self, operation: _Operation, kind: str, path: str, payload: bytes = b"") -> None:
        if not self.owns(operation.operation_id):
            raise VoiceResourceError("operation_cancelled")
        operation.committing = True
        async def publish():
            try:
                await _run_worker(kind, False, path, pcm16=payload, timeout=15 if kind == "runtime-publish" else 5)
            except VoiceResourceError:
                # The receipt may be lost after rename/atomic replace. Confirm
                # disk publication before resolving cancellation or failure.
                confirmed = await asyncio.to_thread(
                    runtime_version_published if kind == "runtime-publish" else is_bundle_version_published,
                    Path(path), payload.decode("ascii"))
                if not confirmed:
                    raise
            operation.committed = True
        commit = asyncio.create_task(publish())
        try:
            while not commit.done():
                try:
                    await asyncio.shield(commit)
                except asyncio.CancelledError:
                    operation.cancel.set()
            commit.result()
        finally:
            operation.committing = False
            self._prepared = {name: value for name, value in self._prepared.items() if name not in {"wake_runtime", "wake_model"}}
            self._prepared_revision += 1

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
            if operation.committing and operation.kind == "repair":
                # Let the bounded atomic step finish, then stop before starting
                # another download or publishing readiness.
                operation.cancel.set()
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
