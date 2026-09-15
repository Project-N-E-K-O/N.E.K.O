"""ASR-independent Owner voice scoring for session activation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
import logging
import math
from pathlib import Path
from typing import Protocol

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CAMPPLUS_SAMPLE_RATE_HZ,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CampPlusBackendFactory,
)
from main_logic.asr_client.speaker_shadow.contracts import (
    MAX_SPEAKER_BACKEND_PCM_BYTES,
)
from main_logic.asr_client.speaker_shadow.runtime import (
    _BackendHostError,
    _BackendHostTimeout,
    _BackendProcessHost,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile


logger = logging.getLogger(__name__)


class ActivationScoreStatus(StrEnum):
    READY = "ready"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_AUDIO = "invalid_audio"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ActivationScoreIdentity:
    profile_generation: str
    scorer_generation: int
    candidate_id: int

    def __post_init__(self) -> None:
        if not self.profile_generation:
            raise ValueError("profile_generation must not be empty")
        if self.scorer_generation <= 0:
            raise ValueError("scorer_generation must be positive")
        if self.candidate_id <= 0:
            raise ValueError("candidate_id must be positive")


@dataclass(frozen=True, slots=True)
class ActivationScoreResult:
    identity: ActivationScoreIdentity
    status: ActivationScoreStatus
    similarity: float | None = None

    def __post_init__(self) -> None:
        if self.status is ActivationScoreStatus.READY:
            if (
                type(self.similarity) not in {int, float}
                or not math.isfinite(float(self.similarity))
                or not -1.0 <= float(self.similarity) <= 1.0
            ):
                raise ValueError("ready result requires a finite similarity")
        elif self.similarity is not None:
            raise ValueError("failed result must not expose a similarity")


class _ActivationBackend(Protocol):
    def load(self) -> bool: ...

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float: ...

    def close(self) -> None: ...


ActivationBackendFactory = Callable[[], _ActivationBackend]


class CampPlusActivationScorer:
    """Own one serialized CAM++ scorer without ASR or Provider identities.

    Production CAM++ runs in the killable spawn-process host already used by
    speaker shadow. ``backend_factory`` remains an in-process unit-test seam.
    ``timeout_seconds`` bounds hot scoring; process start and model loading
    each use ``load_timeout_seconds``. A timeout permanently retires the scorer.
    """

    def __init__(
        self,
        profile: SpeakerProfile,
        *,
        scorer_generation: int,
        timeout_seconds: float = 2.0,
        load_timeout_seconds: float = 15.0,
        shutdown_timeout_seconds: float = 0.25,
        asset_dir: Path | None = None,
        backend_factory: ActivationBackendFactory | None = None,
    ) -> None:
        if type(profile) is not SpeakerProfile:
            raise TypeError("profile must be SpeakerProfile")
        if type(scorer_generation) is not int or scorer_generation <= 0:
            raise ValueError("scorer_generation must be positive")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not math.isfinite(load_timeout_seconds) or load_timeout_seconds <= 0:
            raise ValueError("load_timeout_seconds must be positive")
        if not math.isfinite(shutdown_timeout_seconds) or shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        expected = SpeakerModelIdentity(
            CAMPPLUS_MODEL_ID,
            CAMPPLUS_MODEL_REVISION,
            CAMPPLUS_EMBEDDING_DIM,
        )
        if profile.model_identity != expected:
            raise ValueError("speaker profile model identity does not match CAM++")

        self._profile_generation = profile.generation
        self._scorer_generation = scorer_generation
        self._timeout_seconds = float(timeout_seconds)
        self._load_timeout_seconds = float(load_timeout_seconds)
        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._lock = asyncio.Lock()
        self._closed = False
        self._loaded = False
        self._cleanup_started = False
        self._close_task: asyncio.Task[None] | None = None
        self._active_task: asyncio.Task[object] | None = None
        self._local_close_task: asyncio.Task[object] | None = None
        self._host_start_task: asyncio.Task[_BackendProcessHost] | None = None
        self._host_retirement_task: asyncio.Task[object] | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._host: _BackendProcessHost | None = None
        self._backend: _ActivationBackend | None = None
        self._process_factory: CampPlusBackendFactory | None = None

        if backend_factory is not None:
            self._backend = backend_factory()
            return

        reference = profile.clone_reference()
        embedding = None
        try:
            embedding = reference.copy_embedding()
            self._process_factory = CampPlusBackendFactory(
                embedding,
                asset_dir=asset_dir,
            )
        finally:
            if embedding is not None:
                embedding.fill(0.0)
            reference.close()

    @property
    def profile_generation(self) -> str:
        return self._profile_generation

    @property
    def scorer_generation(self) -> int:
        return self._scorer_generation

    @property
    def closed(self) -> bool:
        return self._closed

    async def prepare(self) -> ActivationScoreStatus:
        async with self._lock:
            return await self._prepare_locked()

    async def score(
        self,
        identity: ActivationScoreIdentity,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> ActivationScoreResult:
        if identity.profile_generation != self._profile_generation:
            return ActivationScoreResult(identity, ActivationScoreStatus.CLOSED)
        if identity.scorer_generation != self._scorer_generation:
            return ActivationScoreResult(identity, ActivationScoreStatus.CLOSED)
        if (
            not isinstance(pcm16, bytes)
            or not pcm16
            or len(pcm16) % 2
            or len(pcm16) > MAX_SPEAKER_BACKEND_PCM_BYTES
            or sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ
        ):
            return ActivationScoreResult(identity, ActivationScoreStatus.INVALID_AUDIO)

        async with self._lock:
            if self._closed:
                return ActivationScoreResult(identity, ActivationScoreStatus.CLOSED)
            if not self._loaded:
                prepared = await self._prepare_locked()
                if prepared is not ActivationScoreStatus.READY:
                    return ActivationScoreResult(identity, prepared)
            status, value = await self._run_backend("score", pcm16, sample_rate_hz)
            if status is not ActivationScoreStatus.READY:
                return ActivationScoreResult(identity, status)
            try:
                similarity = float(value)
            except (TypeError, ValueError):
                return ActivationScoreResult(identity, ActivationScoreStatus.FAILED)
            if not math.isfinite(similarity) or not -1.0 <= similarity <= 1.0:
                return ActivationScoreResult(identity, ActivationScoreStatus.FAILED)
            return ActivationScoreResult(identity, status, similarity)

    async def close(self) -> None:
        self._closed = True
        self._loaded = False
        close_task = self._close_task
        if close_task is None:
            self._cleanup_started = True
            close_task = asyncio.create_task(
                self._finish_close_cleanup(),
                name="voice-activation-scorer-close",
            )
            self._close_task = close_task
        cancellation: asyncio.CancelledError | None = None
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError as error:
                if close_task.cancelled():
                    raise
                if cancellation is None:
                    cancellation = error
        await close_task
        if cancellation is not None:
            raise cancellation

    async def _finish_close_cleanup(self) -> None:
        start_task = self._host_start_task
        if start_task is None:
            self._close_process_factory()
        process_operation_pending = False
        active_task = self._active_task
        if self._backend is None and active_task is not None and not active_task.done():
            active_task.cancel()
            done = await self._wait_tasks_bounded(
                (active_task,),
                timeout_seconds=self._shutdown_timeout_seconds * 3 + 0.1,
            )
            process_operation_pending = active_task not in done
            if process_operation_pending:
                self._track_background(active_task)
        host = self._host
        self._host = None
        if host is not None and not process_operation_pending:
            await self._close_host_bounded(host)
        if start_task is not None:
            self._retire_late_host(start_task)

        if self._backend is not None:
            close_task = self._ensure_local_close_task()
            await self._wait_tasks_bounded(
                task for task in (self._active_task, close_task) if task is not None
            )
        await self._wait_for_background_cleanup()

    async def _prepare_locked(self) -> ActivationScoreStatus:
        if self._closed:
            return ActivationScoreStatus.CLOSED
        if self._loaded:
            return ActivationScoreStatus.READY
        status, value = await self._run_backend("load")
        if status is not ActivationScoreStatus.READY or value is not True:
            return (
                ActivationScoreStatus.MODEL_UNAVAILABLE
                if status is ActivationScoreStatus.READY
                else status
            )
        if self._closed:
            return ActivationScoreStatus.CLOSED
        self._loaded = True
        return ActivationScoreStatus.READY

    async def _run_backend(
        self,
        operation: str,
        *args: object,
    ) -> tuple[ActivationScoreStatus, object | None]:
        if self._closed:
            return ActivationScoreStatus.CLOSED, None
        if self._backend is not None:
            method = getattr(self._backend, operation)
            return await self._run_local_backend(
                method, *args,
                timeout_seconds=(
                    self._load_timeout_seconds if operation == "load"
                    else self._timeout_seconds
                ),
            )
        return await self._run_process_backend(operation, *args)

    async def _run_process_backend(
        self,
        operation: str,
        *args: object,
    ) -> tuple[ActivationScoreStatus, object | None]:
        status, host = await self._ensure_process_host()
        if status is not ActivationScoreStatus.READY or host is None:
            return status, None
        try:
            if operation == "load":
                operation_task = asyncio.create_task(
                    host.load(timeout_seconds=self._load_timeout_seconds)
                )
            else:
                pcm16, sample_rate_hz = args
                if sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ:
                    return ActivationScoreStatus.INVALID_AUDIO, None
                if not isinstance(pcm16, bytes):
                    return ActivationScoreStatus.INVALID_AUDIO, None
                operation_task = asyncio.create_task(
                    host.score(
                        pcm16,
                        timeout_seconds=self._timeout_seconds,
                    )
                )
            self._active_task = operation_task
            operation_task.add_done_callback(self._retire_active_task)
            value = await operation_task
            if self._closed:
                return ActivationScoreStatus.CLOSED, None
            return ActivationScoreStatus.READY, value
        except _BackendHostTimeout:
            self._mark_terminal()
            return ActivationScoreStatus.TIMED_OUT, None
        except asyncio.CancelledError:
            self._mark_terminal()
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            return ActivationScoreStatus.CLOSED, None
        except (ValueError, TypeError):
            return ActivationScoreStatus.INVALID_AUDIO, None
        except _BackendHostError as error:
            # Only fixed host identities cross into logs. Backend exception
            # messages, PCM, embeddings and similarity scores must stay private.
            reason = {
                "candidate PCM exceeds host buffer": "candidate_pcm_exceeds_host_buffer",
                "backend host PCM buffer is closed": "pcm_buffer_closed",
                "backend host is not alive": "host_not_alive",
                "backend host command failed": "command_failed",
                "backend host exited without a response": "host_exited",
                "backend host response failed": "response_failed",
            }.get(str(error), "backend_operation_failed")
            pcm_bytes = (
                len(args[0])
                if operation == "score" and args and isinstance(args[0], bytes)
                else 0
            )
            logger.warning(
                "Voice activation backend failed: operation=%s reason=%s "
                "pcm_bytes=%s limit_bytes=%s host_alive=%s",
                operation,
                reason,
                pcm_bytes,
                MAX_SPEAKER_BACKEND_PCM_BYTES,
                host.alive,
            )
            return ActivationScoreStatus.FAILED, None

    async def _ensure_process_host(
        self,
    ) -> tuple[ActivationScoreStatus, _BackendProcessHost | None]:
        if self._closed:
            return ActivationScoreStatus.CLOSED, None
        if self._host is not None:
            return ActivationScoreStatus.READY, self._host
        factory = self._process_factory
        if factory is None:
            return ActivationScoreStatus.FAILED, None
        start_task = asyncio.create_task(
            asyncio.to_thread(
                _BackendProcessHost.create_started,
                factory=factory,
                terminate_timeout_seconds=self._shutdown_timeout_seconds,
                max_pcm_bytes=MAX_SPEAKER_BACKEND_PCM_BYTES,
            )
        )
        self._host_start_task = start_task
        try:
            done, _ = await asyncio.wait({start_task}, timeout=self._load_timeout_seconds)
        except asyncio.CancelledError:
            self._mark_terminal()
            self._retire_late_host(start_task)
            raise
        if not done:
            self._mark_terminal()
            self._retire_late_host(start_task)
            return ActivationScoreStatus.TIMED_OUT, None
        self._host_start_task = None
        try:
            host = start_task.result()
        except Exception:
            return ActivationScoreStatus.FAILED, None
        if self._closed:
            await self._terminate_host_bounded(host)
            return ActivationScoreStatus.CLOSED, None
        self._host = host
        return ActivationScoreStatus.READY, host

    async def _run_local_backend(
        self,
        operation: Callable[..., object],
        *args: object,
        timeout_seconds: float,
    ) -> tuple[ActivationScoreStatus, object | None]:
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._active_task = task
        task.add_done_callback(self._retire_active_task)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
            if not done:
                self._mark_terminal()
                close_task = self._ensure_local_close_task()
                await self._wait_tasks_bounded((task, close_task))
                return ActivationScoreStatus.TIMED_OUT, None
            try:
                value = task.result()
            except (ValueError, TypeError):
                return ActivationScoreStatus.INVALID_AUDIO, None
            except Exception:
                return ActivationScoreStatus.FAILED, None
            if self._closed:
                return ActivationScoreStatus.CLOSED, None
            return ActivationScoreStatus.READY, value
        except asyncio.CancelledError:
            self._mark_terminal()
            close_task = self._ensure_local_close_task()
            try:
                await self._wait_tasks_bounded((task, close_task))
            except asyncio.CancelledError:
                pass
            raise

    def _mark_terminal(self) -> None:
        self._closed = True
        self._loaded = False
        # Process.start() pickles the factory on a worker thread. Its owned
        # reference must stay intact until that thread finishes; the late-host
        # retirement coroutine closes it after the hand-off is complete.
        if self._host_start_task is None:
            self._close_process_factory()

    def _close_process_factory(self) -> None:
        factory, self._process_factory = self._process_factory, None
        if factory is not None:
            factory.close()

    def _ensure_local_close_task(self) -> asyncio.Task[object]:
        task = self._local_close_task
        if task is None:
            backend = self._backend
            assert backend is not None
            task = asyncio.create_task(asyncio.to_thread(backend.close))
            self._local_close_task = task
            self._track_background(task)
        return task

    def _retire_active_task(self, task: asyncio.Task[object]) -> None:
        if self._active_task is task:
            self._active_task = None
        self._consume_task(task)

    def _retire_late_host(
        self,
        start_task: asyncio.Task[_BackendProcessHost],
    ) -> None:
        if self._host_retirement_task is not None:
            return

        async def retire() -> None:
            try:
                try:
                    host = await asyncio.shield(start_task)
                except BaseException:
                    return
                await self._terminate_host_bounded(host)
            finally:
                if self._host_start_task is start_task:
                    self._host_start_task = None
                self._close_process_factory()

        retirement_task = asyncio.create_task(retire())
        self._host_retirement_task = retirement_task

        def clear_retirement_task(completed: asyncio.Task[object]) -> None:
            if self._host_retirement_task is completed:
                self._host_retirement_task = None

        retirement_task.add_done_callback(clear_retirement_task)
        self._track_background(retirement_task)

    async def _close_host_bounded(self, host: _BackendProcessHost) -> None:
        task = asyncio.create_task(
            host.close(timeout_seconds=self._shutdown_timeout_seconds)
        )
        try:
            done = await self._wait_tasks_bounded(
                (task,),
                timeout_seconds=self._shutdown_timeout_seconds * 5 + 0.1,
            )
        except asyncio.CancelledError:
            self._track_background(task)
            raise
        if task not in done:
            self._track_background(task)
            return
        self._consume_task(task)

    async def _terminate_host_bounded(self, host: _BackendProcessHost) -> None:
        task = asyncio.create_task(host.terminate())
        try:
            done = await self._wait_tasks_bounded(
                (task,),
                timeout_seconds=self._shutdown_timeout_seconds * 2 + 0.1,
            )
        except asyncio.CancelledError:
            self._track_background(task)
            raise
        if task not in done:
            self._track_background(task)
            return
        self._consume_task(task)

    async def _wait_tasks_bounded(
        self,
        tasks: Iterable[asyncio.Task[object]],
        *,
        timeout_seconds: float | None = None,
    ) -> set[asyncio.Task[object]]:
        pending = {task for task in tasks if not task.done()}
        if not pending:
            return set()
        done, _ = await asyncio.wait(
            pending,
            timeout=(
                self._shutdown_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
        )
        return done

    async def _wait_for_background_cleanup(self) -> None:
        await self._wait_tasks_bounded(tuple(self._background_tasks))

    def _track_background(self, task: asyncio.Task[object]) -> None:
        self._background_tasks.add(task)

        def retire(completed: asyncio.Task[object]) -> None:
            self._background_tasks.discard(completed)
            self._consume_task(completed)

        task.add_done_callback(retire)

    @staticmethod
    def _consume_task(task: asyncio.Task[object]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass


__all__ = [
    "ActivationBackendFactory",
    "ActivationScoreIdentity",
    "ActivationScoreResult",
    "ActivationScoreStatus",
    "CampPlusActivationScorer",
]
