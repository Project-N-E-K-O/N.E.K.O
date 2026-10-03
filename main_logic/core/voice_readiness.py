"""Core-owned, producer-targeted microphone isolation and activation recovery."""

from __future__ import annotations

import asyncio
from utils.asyncio_retirement import await_retirement

from main_logic.voice_input.activation import ActivationDecision, ActivationState
from main_logic.voice_input.preview import (
    VoicePreviewIsolationError, preview_isolation_registry,
)


async def _bounded_close(awaitable):
    # Preserve the original cleanup budget, while caller cancellation must not
    # interrupt physical retirement before that budget has run.
    async with asyncio.timeout(5.0):
        await awaitable


class VoiceReadinessControl:
    @staticmethod
    async def handle(manager, message: dict, *, connection_id: str) -> dict:
        event, request_id = message.get("event"), message.get("request_id")
        result = {"event": event, "request_id": request_id, "ok": False}
        if (event not in {"preview_begin", "preview_end", "activation_retry"}
                or type(request_id) is not str or not request_id.strip()
                or len(request_id) > 128):
            return {**result, "reason": "preview_request_invalid"}
        if manager._voice_lease_connection_id != connection_id:
            return {**result, "reason": "preview_owner_changed"}
        try:
            if event == "preview_end":
                token = message.get("token")
                preview_isolation_registry.release_owned(token, manager)
                return {**result, "ok": True}
            if event == "preview_begin":
                control = VoiceReadinessControl._begin
                arguments = request_id
            else:
                control = VoiceReadinessControl._retry
                arguments = message
            if getattr(manager, "_voice_readiness_control_active", False):
                return {**result, "reason": "preview_busy"}
            manager._voice_readiness_control_active = True
            try:
                return await control(manager, arguments, result, connection_id)
            finally:
                manager._voice_readiness_control_active = False
        except VoicePreviewIsolationError as exc:
            return {**result, "reason": exc.code}
        except TimeoutError:
            return {**result, "reason": "voice_cleanup_timeout"}

    @staticmethod
    async def _begin(manager, request_id: str, result: dict, connection_id: str) -> dict:
        nr = manager._voice_input_noise_reduction_enabled
        session = manager.session
        lease_generation = manager._voice_lease_generation
        operation = manager._asr_route_operation_generation
        ticket = preview_isolation_registry.begin(
            manager, request_id, noise_reduction_enabled=nr,
            connection_id=connection_id,
            current=lambda: (manager._voice_lease_connection_id == connection_id
                             and manager.session is session
                             and manager._voice_lease_generation == lease_generation
                             and manager._asr_route_operation_generation == operation
                             and manager._voice_input_noise_reduction_enabled is nr),
        )
        # Retire the producer synchronously. Removing the temporary reservation
        # on timeout/cancel must never restore its previous input authority.
        manager._voice_lease_owner = "none"
        manager._voice_lease_synchronized = False
        operation = manager._begin_asr_route_operation()
        try:
            # Reservation blocks both PCM ingress and already queued activation
            # output before any await. The existing close retires pipeline,
            # utterances and runtime without reopening or replaying old input.
            async with asyncio.timeout(5.0):
                old_runtime = manager._voice_session_activation_runtime
                await await_retirement(_bounded_close(manager._close_independent_asr(next_route_mode="blocked",
                                                     operation_generation=operation)))
                preview_isolation_registry.validate(ticket, require_ready=False)
                generation = manager._capture_voice_session_activation_generation()
                prior_current = ticket.current
                ticket.current = lambda: (prior_current()
                    and manager._capture_voice_session_activation_generation() == generation)
                if old_runtime is not None:
                    await await_retirement(_bounded_close(old_runtime.close()))
                # This is an actual producer stop, without ending a display
                # window's unrelated text session or revoking its connection.
                preview_isolation_registry.validate(ticket, require_ready=False)
                await manager._voice_input_registry.wait_idle()
                preview_isolation_registry.mark_ready(ticket)
            return {**result, "ok": True, **ticket.as_dict()}
        except BaseException:
            preview_isolation_registry.release(ticket)
            raise

    @staticmethod
    async def _retry(manager, message: dict, result: dict, connection_id: str) -> dict:
        if preview_isolation_registry.is_manager_isolated(manager):
            raise VoicePreviewIsolationError("preview_busy")
        generation = manager._capture_voice_session_activation_generation()
        expected = {
            "session_id": generation.session_id,
            "microphone_generation": generation.microphone,
            "route_generation": generation.route,
            "profile_revision": generation.profile,
            "permission_revision": generation.permission,
        }
        if any(type(message.get(key)) is not type(value) or message.get(key) != value
               for key, value in expected.items()):
            raise VoicePreviewIsolationError("activation_session_changed")
        factory = manager._voice_session_activation_factory
        if factory is None or not factory.enforce:
            raise VoicePreviewIsolationError("activation_unavailable")
        if (manager._asr_route_mode == "blocked"
                or getattr(manager, "session_closed_by_server", False)
                or getattr(manager, "_voice_activation_prefix_cleanup", None) is not None):
            # An uncertain downstream write was retired by the existing
            # transport owner. Only an explicit microphone/session restart can
            # create a safe route; verifier retry cannot reuse or replay it.
            return {**result, "reason": "voice_session_restart_required"}
        session = manager.session
        old = manager._voice_session_activation_runtime
        manager._voice_session_activation_degraded = True
        manager._invalidate_voice_pcm_sync("activation_retry")
        generation = manager._capture_voice_session_activation_generation()

        def current() -> bool:
            return (manager._voice_lease_connection_id == connection_id
                    and manager.session is session
                    and manager._voice_session_activation_factory is factory
                    and manager._capture_voice_session_activation_generation() == generation)

        runtime = None
        try:
            async with asyncio.timeout(5.0):
                if old is not None:
                    await await_retirement(_bounded_close(old.close()))
                if not current():
                    raise VoicePreviewIsolationError("activation_session_changed")
                async with manager._voice_session_activation_lock:
                    if not current():
                        raise VoicePreviewIsolationError("activation_session_changed")

                    async def output(frame):
                        return await manager._route_voice_session_activation_output(frame, generation)

                    runtime = factory.create(generation, output,
                        status_callback=lambda decision: manager._on_voice_session_activation_status(generation, decision))
                    manager._voice_session_activation_runtime = runtime
                    runtime.set_capture_progress_provider(manager._voice_activation_capture_watermark)
                    manager._on_voice_session_activation_status(generation,
                        ActivationDecision(ActivationState.PREPARING, "preparing"))
            async with asyncio.timeout(35.0):
                await manager._prepare_voice_session_activation_runtime(runtime, generation)
        except BaseException as exc:
            if current():
                reason = ("voice_cleanup_timeout" if isinstance(exc, TimeoutError) else
                          "runtime_creation_failed" if runtime is None else "prepare_failed")
                manager._voice_session_activation_runtime = None
                manager._voice_session_activation_degraded = True
                decision = ActivationDecision(ActivationState.UNAVAILABLE, reason)
                manager._voice_session_activation_status = (generation, decision.state, decision.reason)
                manager._voice_session_activation_status_revision += 1
                manager._schedule_core_asr_cleanup(
                    manager._send_voice_session_activation_status(generation, decision,
                        manager._voice_session_activation_status_revision),
                    name="voice-activation-retry-unavailable")
            if runtime is not None:
                manager._schedule_core_asr_cleanup(runtime.close(), name="voice-activation-retry-retire")
            if isinstance(exc, Exception) and not isinstance(exc, (VoicePreviewIsolationError, TimeoutError)):
                return {**result, "reason": "runtime_creation_failed" if runtime is None else "prepare_failed"}
            raise
        if not current():
            raise VoicePreviewIsolationError("activation_session_changed")
        state = manager._voice_session_activation_status
        if state is not None and state[0] == generation and state[1] is ActivationState.UNAVAILABLE:
            return {**result, "reason": state[2]}
        if manager._voice_session_activation_runtime is not runtime:
            raise VoicePreviewIsolationError("activation_session_changed")
        if state is None or state[1] is not ActivationState.WAITING:
            return {**result, "reason": state[2] if state else "prepare_failed"}
        manager._voice_session_activation_degraded = False
        return {**result, "ok": True}
