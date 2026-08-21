"""Application wiring for the single Owner profile across character runtimes."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import uuid
import weakref

from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.asr_client.speaker_shadow.campplus import CampPlusEmbeddingModel
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity_service.asr_composition import (
    OwnerVoiceAsrCompositionFactory,
)
from main_logic.voice_identity_service.preference_store import (
    VoiceIdentityPreferenceStore,
)
from main_logic.voice_identity_service.profile_store import (
    SecureStorageUnavailableError,
    VoiceIdentityProfileStore,
)
from main_logic.voice_identity_service.registry import (
    install_voice_identity_service_for_app,
)
from main_logic.voice_identity_service.service import VoiceIdentityService
from main_logic.voice_input.suppression import VoiceInputSuppressionController


logger = logging.getLogger(__name__)

_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS = 1.0


@dataclass(slots=True)
class _OwnerActivation:
    profile: SpeakerProfile
    generation: str
    enforce: bool

    @classmethod
    def from_borrowed(
        cls,
        profile: SpeakerProfile,
        generation: str,
        *,
        enforce: bool,
    ) -> "_OwnerActivation":
        return cls(copy.copy(profile), generation, enforce)

    def factory_for(self, manager) -> OwnerVoiceAsrCompositionFactory:
        return OwnerVoiceAsrCompositionFactory(
            manager._asr_runtime,
            self.profile,
            activation_generation=self.generation,
            enforce=self.enforce,
        )

    def close(self) -> None:
        self.profile.close()


class OwnerVoiceRuntimeRegistry:
    """Serialize activation, manager replacement, and enrollment suppression."""

    def __init__(
        self,
        *,
        enforce: bool,
        restore_retry_interval_seconds: float = 0.1,
        restore_retry_timeout_seconds: float = 10.0,
    ) -> None:
        if type(enforce) is not bool:
            raise TypeError("enforce must be bool")
        if (
            not math.isfinite(restore_retry_interval_seconds)
            or not math.isfinite(restore_retry_timeout_seconds)
            or restore_retry_interval_seconds <= 0
            or restore_retry_timeout_seconds < restore_retry_interval_seconds
        ):
            raise ValueError("restore retry bounds are invalid")
        self._enforce = enforce
        self._restore_retry_interval_seconds = float(restore_retry_interval_seconds)
        self._restore_retry_timeout_seconds = float(restore_retry_timeout_seconds)
        self._lock = asyncio.Lock()
        self._managers: weakref.WeakSet = weakref.WeakSet()
        self._restore_pending: weakref.WeakSet = weakref.WeakSet()
        self._restore_retry_task: asyncio.Task[None] | None = None
        self._attach_pending: weakref.WeakSet = weakref.WeakSet()
        self._attach_retry_task: asyncio.Task[None] | None = None
        self._detach_pending: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._detach_retry_task: asyncio.Task[None] | None = None
        self._activation: _OwnerActivation | None = None
        self._suppressed = False
        self._closed = False

    async def register_manager(
        self,
        manager,
    ) -> VoiceIdentityActivationResult:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Owner voice runtime registry is closed")
            if manager in self._managers:
                activation = self._activation
                needs_attach = manager in self._attach_pending or (
                    activation is not None and manager in self._detach_pending
                )
                if not needs_attach:
                    return (
                        VoiceIdentityActivationResult.READY
                        if activation is None
                        else self._manager_activation_result(manager)
                    )
                if activation is None:
                    self._attach_pending.discard(manager)
                    return VoiceIdentityActivationResult.READY
                self._detach_pending.pop(manager, None)
                result = await self._attach_manager_bounded(manager, activation)
                if result:
                    self._attach_pending.discard(manager)
                    return result
                self._attach_pending.add(manager)
                self._ensure_attach_watchdog()
                return VoiceIdentityActivationResult.RUNTIME_DEGRADED
            self._managers.add(manager)
            try:
                if self._suppressed:
                    try:
                        await asyncio.wait_for(
                            manager.set_voice_input_suppressed(
                                "voice_identity_enrollment",
                                suppressed=True,
                            ),
                            timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                        )
                    except TimeoutError:
                        self._restore_pending.add(manager)
                        if self._activation is not None:
                            self._attach_pending.add(manager)
                            self._ensure_attach_watchdog()
                        return VoiceIdentityActivationResult.RUNTIME_DEGRADED
                    self._restore_pending.discard(manager)
                elif manager in self._restore_pending:
                    if await self._restore_manager_bounded(
                        manager,
                        "voice_identity_enrollment",
                    ):
                        self._restore_pending.discard(manager)
                    else:
                        self._ensure_restore_watchdog(
                            "voice_identity_enrollment"
                        )
                        return VoiceIdentityActivationResult.RUNTIME_DEGRADED
                activation = self._activation
                if activation is not None:
                    self._detach_pending.pop(manager, None)
                    result = await self._attach_manager_bounded(manager, activation)
                    if not result:
                        self._attach_pending.add(manager)
                        self._ensure_attach_watchdog()
                        return VoiceIdentityActivationResult.RUNTIME_DEGRADED
                    self._attach_pending.discard(manager)
                    self._detach_pending.pop(manager, None)
                    return result
                return VoiceIdentityActivationResult.READY
            except asyncio.CancelledError:
                activation = self._activation
                if self._suppressed:
                    # The manager belongs to the active enrollment gate. Keep it
                    # gated until restore() ends the lease; opening it here would
                    # admit normal PCM while every existing manager is suppressed.
                    self._restore_pending.add(manager)
                    if activation is not None:
                        self._attach_pending.add(manager)
                        self._ensure_attach_watchdog()
                elif manager in self._restore_pending:
                    self._ensure_restore_watchdog("voice_identity_enrollment")
                    if activation is not None:
                        self._attach_pending.add(manager)
                        self._ensure_attach_watchdog()
                elif activation is not None and manager in self._managers:
                    self._attach_pending.add(manager)
                    self._ensure_attach_watchdog()
                raise
            except BaseException:
                if self._suppressed:
                    self._restore_pending.add(manager)
                    if self._activation is not None:
                        self._attach_pending.add(manager)
                        self._ensure_attach_watchdog()
                elif manager in self._restore_pending:
                    self._ensure_restore_watchdog("voice_identity_enrollment")
                raise

    async def unregister_manager(self, manager) -> None:
        async with self._lock:
            self._managers.discard(manager)
            self._attach_pending.discard(manager)
            detach_generation = str(uuid.uuid4())
            cancellation: asyncio.CancelledError | None = None
            try:
                detached = await asyncio.wait_for(
                    manager.set_speaker_verifier_factory(
                        None,
                        activation_generation=detach_generation,
                    ),
                    timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                detached = False
            except asyncio.CancelledError as exc:
                cancellation = exc
                detached = False
            except Exception:
                detached = False
            if detached:
                self._detach_pending.pop(manager, None)
            else:
                self._detach_pending[manager] = detach_generation
                self._ensure_detach_watchdog()
            if self._suppressed or manager in self._restore_pending:
                try:
                    restored = await self._restore_manager_bounded(
                        manager,
                        "voice_identity_enrollment",
                    )
                except asyncio.CancelledError as exc:
                    self._restore_pending.add(manager)
                    if not self._suppressed:
                        self._ensure_restore_watchdog("voice_identity_enrollment")
                    if cancellation is None:
                        cancellation = exc
                else:
                    if restored:
                        self._restore_pending.discard(manager)
                    else:
                        self._restore_pending.add(manager)
                        if not self._suppressed:
                            self._ensure_restore_watchdog(
                                "voice_identity_enrollment"
                            )
            if cancellation is not None:
                raise cancellation

    async def activate(
        self,
        profile: SpeakerProfile | None,
        generation: str,
    ) -> VoiceIdentityActivationResult:
        if type(generation) is not str or not generation.strip():
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        try:
            next_activation = (
                None
                if profile is None
                else _OwnerActivation.from_borrowed(
                    profile,
                    generation,
                    enforce=self._enforce,
                )
            )
        except Exception:
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED

        async with self._lock:
            if self._closed:
                if next_activation is not None:
                    next_activation.close()
                return VoiceIdentityActivationResult.RUNTIME_DEGRADED
            old_activation = self._activation
            if next_activation is None:
                self._activation = None
                self._attach_pending.clear()
                if old_activation is not None:
                    # Installed factories own profile clones; this releases only
                    # the registry's retired activation material.
                    old_activation.close()
                all_detached = True
                managers = tuple(self._managers)
                for index, manager in enumerate(managers):
                    try:
                        detached = await asyncio.wait_for(
                            manager.set_speaker_verifier_factory(
                                None,
                                activation_generation=generation,
                            ),
                            timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        detached = False
                    except asyncio.CancelledError:
                        all_detached = False
                        for pending_manager in managers[index:]:
                            self._detach_pending[pending_manager] = generation
                        self._ensure_detach_watchdog()
                        if self._current_task_is_cancelling():
                            raise
                        return VoiceIdentityActivationResult.RUNTIME_DEGRADED
                    except Exception:
                        detached = False
                    if detached:
                        self._detach_pending.pop(manager, None)
                    else:
                        all_detached = False
                        self._detach_pending[manager] = generation
                if self._detach_pending:
                    self._ensure_detach_watchdog()
                return (
                    VoiceIdentityActivationResult.READY
                    if all_detached
                    else VoiceIdentityActivationResult.RUNTIME_DEGRADED
                )
            changed: list[object] = []
            activation_result = VoiceIdentityActivationResult.READY
            try:
                for manager in tuple(self._managers):
                    factory = next_activation.factory_for(manager)
                    changed.append(manager)
                    try:
                        updated = await asyncio.wait_for(
                            manager.set_speaker_verifier_factory(
                                factory,
                                activation_generation=generation,
                            ),
                            timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                        )
                    except asyncio.CancelledError:
                        factory.close()
                        if self._current_task_is_cancelling():
                            raise
                        raise RuntimeError("speaker verifier activation cancelled")
                    except BaseException:
                        factory.close()
                        raise
                    if not updated:
                        factory.close()
                        raise RuntimeError("speaker verifier activation failed")
                    if (
                        updated
                        is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
                        and not self._manager_is_inactive_blocked(manager)
                    ):
                        activation_result = (
                            VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
                        )
                    self._attach_pending.discard(manager)
                    self._detach_pending.pop(manager, None)
            except asyncio.CancelledError:
                self._rollback_activation(changed, old_activation)
                if next_activation is not None:
                    # Managers that accepted the new factory hold a cloned
                    # profile through that factory, not this activation copy.
                    next_activation.close()
                if self._current_task_is_cancelling():
                    raise
                return VoiceIdentityActivationResult.RUNTIME_DEGRADED
            except BaseException:
                self._rollback_activation(changed, old_activation)
                if next_activation is not None:
                    # Managers that accepted the new factory hold a cloned
                    # profile through that factory, not this activation copy.
                    next_activation.close()
                return VoiceIdentityActivationResult.RUNTIME_DEGRADED

            self._activation = next_activation
            self._attach_pending.clear()
            if old_activation is not None:
                # Installed factories own profile clones; this releases only
                # the registry's retired activation material.
                old_activation.close()
            return activation_result

    def activation_status(self) -> VoiceIdentityActivationResult:
        if self._closed or self._activation is None:
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        managers = tuple(self._managers)
        if self._attach_pending or any(
            manager in self._detach_pending or manager in self._restore_pending
            for manager in managers
        ):
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        result = VoiceIdentityActivationResult.READY
        for manager in managers:
            manager_result = self._manager_activation_result(manager)
            if manager_result is VoiceIdentityActivationResult.RUNTIME_DEGRADED:
                return manager_result
            if manager_result is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE:
                result = manager_result
        return result

    @staticmethod
    def _manager_activation_result(manager) -> VoiceIdentityActivationResult:
        if OwnerVoiceRuntimeRegistry._manager_is_inactive_blocked(manager):
            return VoiceIdentityActivationResult.READY
        runtime = getattr(manager, "_asr_runtime", None)
        if bool(getattr(runtime, "_speaker_verifier_degraded", False)):
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        return OwnerVoiceRuntimeRegistry._manager_route_result(manager)

    @staticmethod
    def _manager_route_result(manager) -> VoiceIdentityActivationResult:
        route_mode = getattr(manager, "_asr_route_mode", None)
        if OwnerVoiceRuntimeRegistry._manager_is_inactive_blocked(manager):
            return VoiceIdentityActivationResult.READY
        if route_mode is not None and route_mode != "independent":
            return VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
        return VoiceIdentityActivationResult.READY

    @staticmethod
    def _manager_is_inactive_blocked(manager) -> bool:
        return (
            getattr(manager, "_asr_route_mode", None) == "blocked"
            and getattr(manager, "is_active", None) is False
        )

    @staticmethod
    def _current_task_is_cancelling() -> bool:
        current = asyncio.current_task()
        return current is not None and current.cancelling() > 0

    @staticmethod
    async def _attach_manager(
        manager,
        activation: _OwnerActivation,
    ) -> VoiceIdentityActivationResult:
        factory: OwnerVoiceAsrCompositionFactory | None = None
        try:
            factory = activation.factory_for(manager)
            updated = await asyncio.wait_for(
                manager.set_speaker_verifier_factory(
                    factory,
                    activation_generation=activation.generation,
                ),
                timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            if factory is not None:
                factory.close()
            raise
        except BaseException:
            if factory is not None:
                factory.close()
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        if not updated:
            factory.close()
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        if isinstance(updated, VoiceIdentityActivationResult):
            if (
                updated is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
                and OwnerVoiceRuntimeRegistry._manager_is_inactive_blocked(manager)
            ):
                return VoiceIdentityActivationResult.READY
            return updated
        return VoiceIdentityActivationResult.READY

    @staticmethod
    async def _attach_manager_bounded(
        manager,
        activation: _OwnerActivation,
    ) -> VoiceIdentityActivationResult:
        try:
            return await asyncio.wait_for(
                OwnerVoiceRuntimeRegistry._attach_manager(manager, activation),
                timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED
        except asyncio.CancelledError:
            if OwnerVoiceRuntimeRegistry._current_task_is_cancelling():
                raise
            return VoiceIdentityActivationResult.RUNTIME_DEGRADED

    def _ensure_attach_watchdog(self) -> None:
        task = self._attach_retry_task
        if task is not None and not task.done():
            return
        self._attach_retry_task = asyncio.create_task(
            self._run_attach_watchdog(),
            name="voice-identity-attach-watchdog",
        )

    async def _run_attach_watchdog(self) -> None:
        current = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._restore_retry_timeout_seconds
        try:
            while loop.time() < deadline:
                await asyncio.sleep(self._restore_retry_interval_seconds)
                async with self._lock:
                    if self._closed:
                        return
                    activation = self._activation
                    if activation is None:
                        self._attach_pending.clear()
                        return
                    targets = tuple(self._attach_pending)
                    if not targets:
                        return
                    for manager in targets:
                        if manager not in self._managers:
                            self._attach_pending.discard(manager)
                            continue
                        call_timeout = min(
                            _WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                            deadline - loop.time(),
                        )
                        if call_timeout <= 0:
                            break
                        try:
                            attached = await asyncio.wait_for(
                                self._attach_manager(manager, activation),
                                timeout=call_timeout,
                            )
                        except asyncio.TimeoutError:
                            continue
                        except asyncio.CancelledError:
                            if current is not None and current.cancelling():
                                raise
                            continue
                        if attached:
                            self._attach_pending.discard(manager)
                            self._detach_pending.pop(manager, None)
            async with self._lock:
                pending_count = 0 if self._closed else len(self._attach_pending)
            if pending_count:
                logger.warning(
                    "Owner voice verifier attach watchdog exhausted with %d "
                    "manager(s) still pending",
                    pending_count,
                )
        except asyncio.CancelledError:
            raise
        finally:
            if self._attach_retry_task is current:
                self._attach_retry_task = None

    def _rollback_activation(
        self,
        managers: list[object],
        activation: _OwnerActivation | None,
    ) -> None:
        for manager in managers:
            if activation is None:
                self._attach_pending.discard(manager)
                self._detach_pending[manager] = str(uuid.uuid4())
            else:
                self._attach_pending.add(manager)
                self._detach_pending.pop(manager, None)
        if self._attach_pending:
            self._ensure_attach_watchdog()
        if self._detach_pending:
            self._ensure_detach_watchdog()

    async def suppress(self, reason: str) -> None:
        await self._set_suppressed(reason, True)

    async def restore(self, reason: str) -> None:
        await self._set_suppressed(reason, False)

    async def _set_suppressed(self, reason: str, suppressed: bool) -> None:
        if reason != "voice_identity_enrollment":
            raise ValueError("unsupported voice input suppression reason")
        async with self._lock:
            if self._closed:
                if suppressed:
                    raise RuntimeError("Owner voice runtime registry is closed")
                return
            if self._suppressed is suppressed and (
                suppressed or not self._restore_pending
            ):
                return
            if not suppressed:
                targets = tuple(set(self._managers).union(self._restore_pending))
                for manager in targets:
                    self._restore_pending.add(manager)
                try:
                    for manager in targets:
                        try:
                            await self._restore_manager(manager, reason)
                        except Exception:
                            self._restore_pending.add(manager)
                        else:
                            self._restore_pending.discard(manager)
                finally:
                    # Even cancellation or an unexpected BaseException cannot
                    # make future/replacement managers inherit a stale gate.
                    # Per-manager transient failures are retried immediately
                    # and then by the bounded watchdog below.
                    self._suppressed = False
                    if self._restore_pending:
                        self._ensure_restore_watchdog(reason)
                return
            changed: list[object] = []
            try:
                for manager in tuple(self._managers):
                    # Include the in-flight manager before awaiting: its Core
                    # gate is published synchronously before ASR abort/cleanup,
                    # so cancellation may leave work incomplete but must still
                    # trigger a restore attempt.
                    changed.append(manager)
                    await asyncio.wait_for(
                        manager.set_voice_input_suppressed(
                            reason,
                            suppressed=suppressed,
                        ),
                        timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                    )
            except BaseException:
                for manager in changed:
                    self._restore_pending.add(manager)
                try:
                    for manager in reversed(changed):
                        try:
                            await self._restore_manager(manager, reason)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            continue
                        else:
                            self._restore_pending.discard(manager)
                finally:
                    if self._restore_pending:
                        self._ensure_restore_watchdog(reason)
                raise
            for manager in changed:
                self._restore_pending.discard(manager)
            self._suppressed = suppressed

    def _ensure_restore_watchdog(self, reason: str) -> None:
        task = self._restore_retry_task
        if task is not None and not task.done():
            return
        self._restore_retry_task = asyncio.create_task(
            self._run_restore_watchdog(reason),
            name="voice-identity-restore-watchdog",
        )

    async def _run_restore_watchdog(self, reason: str) -> None:
        current = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._restore_retry_timeout_seconds
        try:
            while loop.time() < deadline:
                await asyncio.sleep(self._restore_retry_interval_seconds)
                async with self._lock:
                    if self._closed or self._suppressed:
                        return
                    targets = tuple(self._restore_pending)
                    if not targets:
                        return
                    for manager in targets:
                        call_timeout = min(
                            _WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                            deadline - loop.time(),
                        )
                        if call_timeout <= 0:
                            break
                        try:
                            await asyncio.wait_for(
                                self._restore_manager(manager, reason),
                                timeout=call_timeout,
                            )
                        except asyncio.CancelledError:
                            if current is not None and current.cancelling():
                                raise
                            continue
                        except Exception:
                            continue
                        self._restore_pending.discard(manager)
            async with self._lock:
                pending_count = (
                    0
                    if self._closed or self._suppressed
                    else len(self._restore_pending)
                )
            if pending_count:
                logger.warning(
                    "Owner voice input restore watchdog exhausted with %d "
                    "manager(s) still pending",
                    pending_count,
                )
        except asyncio.CancelledError:
            raise
        finally:
            if self._restore_retry_task is current:
                self._restore_retry_task = None

    def _ensure_detach_watchdog(self) -> None:
        task = self._detach_retry_task
        if task is not None and not task.done():
            return
        self._detach_retry_task = asyncio.create_task(
            self._run_detach_watchdog(),
            name="voice-identity-detach-watchdog",
        )

    async def _run_detach_watchdog(self) -> None:
        current = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._restore_retry_timeout_seconds
        try:
            while loop.time() < deadline:
                await asyncio.sleep(self._restore_retry_interval_seconds)
                async with self._lock:
                    if self._closed:
                        return
                    targets = tuple(self._detach_pending.items())
                    if not targets:
                        return
                    for manager, generation in targets:
                        call_timeout = min(
                            _WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                            deadline - loop.time(),
                        )
                        if call_timeout <= 0:
                            break
                        try:
                            detached = await asyncio.wait_for(
                                manager.set_speaker_verifier_factory(
                                    None,
                                    activation_generation=generation,
                                ),
                                timeout=call_timeout,
                            )
                        except asyncio.CancelledError:
                            if current is not None and current.cancelling():
                                raise
                            continue
                        except Exception:
                            continue
                        if detached:
                            self._detach_pending.pop(manager, None)
            async with self._lock:
                pending_count = 0 if self._closed else len(self._detach_pending)
            if pending_count:
                logger.warning(
                    "Owner voice verifier detach watchdog exhausted with %d "
                    "manager(s) still pending",
                    pending_count,
                )
        except asyncio.CancelledError:
            raise
        finally:
            if self._detach_retry_task is current:
                self._detach_retry_task = None

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            retry_task = self._restore_retry_task
            attach_task = self._attach_retry_task
            detach_task = self._detach_retry_task
        tasks = tuple(
            task
            for task in (retry_task, attach_task, detach_task)
            if task is not None
        )
        for task in tasks:
            task.cancel()
        cleanup_cancellations: list[asyncio.CancelledError] = []
        cleanup_task = asyncio.create_task(
            self._finish_close_cleanup(retry_task, attach_task, detach_task),
            name="voice-identity-registry-close-cleanup",
        )
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as exc:
                if not cleanup_cancellations:
                    cleanup_cancellations.append(exc)
        await cleanup_task
        if cleanup_cancellations:
            raise cleanup_cancellations[0]

    async def _finish_close_cleanup(
        self,
        retry_task: asyncio.Task[None] | None,
        attach_task: asyncio.Task[None] | None,
        detach_task: asyncio.Task[None] | None,
    ) -> None:
        tasks = tuple(
            task
            for task in (retry_task, attach_task, detach_task)
            if task is not None
        )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._restore_retry_task is retry_task:
            self._restore_retry_task = None
        if self._attach_retry_task is attach_task:
            self._attach_retry_task = None
        if self._detach_retry_task is detach_task:
            self._detach_retry_task = None
        async with self._lock:
            managers = tuple(
                set(self._managers)
                .union(self._restore_pending)
                .union(self._detach_pending)
            )
            self._suppressed = False
            self._restore_pending.clear()
            self._attach_pending.clear()
            self._detach_pending.clear()
            try:
                for manager in managers:
                    try:
                        await asyncio.wait_for(
                            self._restore_manager(
                                manager,
                                "voice_identity_enrollment",
                            ),
                            timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                        )
                    except asyncio.CancelledError:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(
                            manager.set_speaker_verifier_factory(
                                None,
                                activation_generation=str(uuid.uuid4()),
                            ),
                            timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
                        )
                    except asyncio.CancelledError:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                    except Exception:
                        pass
            finally:
                self._managers.clear()
                activation = self._activation
                self._activation = None
                if activation is not None:
                    activation.close()

    @staticmethod
    async def _restore_manager(manager, reason: str) -> None:
        last_error: BaseException | None = None
        attempts = 2
        attempt_timeout = _WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS / attempts
        for _attempt in range(attempts):
            try:
                await asyncio.wait_for(
                    manager.set_voice_input_suppressed(
                        reason,
                        suppressed=False,
                    ),
                    timeout=attempt_timeout,
                )
                return
            except asyncio.CancelledError as exc:
                if OwnerVoiceRuntimeRegistry._current_task_is_cancelling():
                    raise
                last_error = exc
                await asyncio.sleep(0)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0)
        assert last_error is not None
        raise last_error

    @staticmethod
    async def _restore_manager_bounded(manager, reason: str) -> bool:
        try:
            await asyncio.wait_for(
                OwnerVoiceRuntimeRegistry._restore_manager(manager, reason),
                timeout=_WATCHDOG_MANAGER_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            if OwnerVoiceRuntimeRegistry._current_task_is_cancelling():
                raise
            return False
        except Exception:
            return False
        return True


_runtime_registry: OwnerVoiceRuntimeRegistry | None = None
_service: VoiceIdentityService | None = None


class _UnavailableProfileStore(VoiceIdentityProfileStore):
    """Concrete fail-closed store used when DPAPI cannot be constructed."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> SpeakerProfile | None:
        raise SecureStorageUnavailableError("secure_storage_unavailable")

    def stage(self, profile: SpeakerProfile):
        del profile
        raise SecureStorageUnavailableError("secure_storage_unavailable")

    def delete(self) -> bool:
        raise SecureStorageUnavailableError("secure_storage_unavailable")


def install_voice_identity_runtime(config_manager) -> VoiceIdentityService:
    """Construct and install the application singleton once."""

    global _runtime_registry, _service
    if _service is not None:
        return _service
    configured_mode = (
        os.environ.get(
            "NEKO_VOICE_IDENTITY_MODE",
            "enforce",
        )
        .strip()
        .lower()
    )
    runtime_mode = (
        configured_mode if configured_mode in {"off", "shadow", "enforce"} else "off"
    )
    if configured_mode not in {"off", "shadow", "enforce"}:
        logger.warning(
            "Unsupported NEKO_VOICE_IDENTITY_MODE value %r; Owner voice "
            "filtering is disabled",
            configured_mode,
        )
    registry = OwnerVoiceRuntimeRegistry(enforce=runtime_mode == "enforce")
    local_state_dir = Path(config_manager.local_state_dir)
    try:
        profile_store = VoiceIdentityProfileStore(
            local_state_dir / "voice_identity.profile"
        )
    except SecureStorageUnavailableError:
        profile_store = _UnavailableProfileStore(
            local_state_dir / "voice_identity.profile"
        )
    suppression = VoiceInputSuppressionController(
        registry.suppress,
        registry.restore,
        default_ttl_seconds=30.0,
        hard_ttl_seconds=60.0,
    )
    service = VoiceIdentityService(
        profile_store,
        VoiceIdentityPreferenceStore(local_state_dir / "voice_identity.settings.json"),
        suppression,
        CampPlusEmbeddingModel,
        registry.activate,
        runtime_mode=runtime_mode,
        runtime_status_callback=registry.activation_status,
    )
    install_voice_identity_service_for_app(service)
    _runtime_registry = registry
    _service = service
    return service


async def initialize_voice_identity_runtime(config_manager) -> None:
    service = install_voice_identity_runtime(config_manager)
    await service.initialize()


async def close_voice_identity_runtime() -> None:
    service = _service
    registry = _runtime_registry
    try:
        if service is not None:
            await service.close()
    except BaseException:
        try:
            if registry is not None:
                await registry.close()
        except BaseException:
            logger.warning(
                "Owner voice runtime registry cleanup failed after service "
                "cleanup failure",
                exc_info=True,
            )
        raise
    if registry is not None:
        await registry.close()


async def register_voice_identity_manager(
    manager,
) -> VoiceIdentityActivationResult:
    registry = _runtime_registry
    if registry is None:
        return VoiceIdentityActivationResult.READY
    try:
        return await registry.register_manager(manager)
    except Exception:
        return VoiceIdentityActivationResult.RUNTIME_DEGRADED


async def unregister_voice_identity_manager(manager) -> None:
    registry = _runtime_registry
    if registry is not None:
        await registry.unregister_manager(manager)


__all__ = [
    "OwnerVoiceRuntimeRegistry",
    "close_voice_identity_runtime",
    "initialize_voice_identity_runtime",
    "install_voice_identity_runtime",
    "register_voice_identity_manager",
    "unregister_voice_identity_manager",
]
