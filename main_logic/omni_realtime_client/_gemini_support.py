# -- coding: utf-8 --
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ._shared import (
    GEMINI_CANCELLED_TERMINAL_TTL_SECONDS,
    GEMINI_TURN_IMAGE_MIME,
    Any,
    List,
    Path,
    ToolCall,
    ToolResult,
    TurnDetectionMode,
    asyncio,
    atomic_write_json,
    get_config_manager,
    json,
    logger,
    normalize_gemini_tts_voice,
    time,
    write_ssl_diagnostic,
)


genai = None

types = None

GEMINI_AVAILABLE: bool | None = None  # None = import not yet attempted

_GEMINI_IMPORT_ERROR = None

def _ensure_gemini_sdk() -> bool:
    """Import google-genai on first call and cache the result; emit an SSL diagnostic on failure.

    Returns whether the SDK is available. Under a concurrent race the worst case is one duplicate import (Python's module cache makes it idempotent).
    """
    global genai, types, GEMINI_AVAILABLE, _GEMINI_IMPORT_ERROR
    # Explicitly forced unavailability takes highest priority: degrade even if objects are already in globals.
    if GEMINI_AVAILABLE is False:
        return False
    # An existing object (real import or test-injected mock) is trusted directly without reimporting.
    if genai is not None and types is not None:
        GEMINI_AVAILABLE = True
        return True
    try:
        from google import genai as genai_mod
        from google.genai import types as types_mod
        # Fill only missing objects, preserving any genai mock injected by tests.
        if genai is None:
            genai = genai_mod
        if types is None:
            types = types_mod
        GEMINI_AVAILABLE = True
        _GEMINI_IMPORT_ERROR = None
    except Exception as e:
        # Do not overwrite externally forced availability flags or clear genai/types potentially injected by tests.
        # (Fill-missing-only rule: preserve partially injected mocks on import failure.)
        if GEMINI_AVAILABLE is None:
            GEMINI_AVAILABLE = False
            _GEMINI_IMPORT_ERROR = e
            _emit_gemini_import_diagnostic(e)
    # Availability requires both a true flag and actual objects, avoiding false availability when forced True but import failed,
    # which would make _connect_gemini dereference genai/types on None.
    return bool(GEMINI_AVAILABLE) and genai is not None and types is not None

_config_manager = get_config_manager()

def _emit_gemini_import_diagnostic(import_error) -> None:
    """Emit an SSL diagnostic when the first genai SDK import fails (deduplicated with a 24h throttle)."""
    diagnostics_dir = Path(_config_manager.app_docs_dir) / "logs" / "diagnostics"
    sentinel_path = diagnostics_dir / "gemini_sdk_import_failed.last.json"
    throttle_window_seconds = 24 * 60 * 60
    now_ts = time.time()

    recent_diag_path = None
    try:
        if sentinel_path.exists():
            with open(sentinel_path, "r", encoding="utf-8") as f:
                sentinel_data = json.load(f)
            sentinel_diag_path = sentinel_data.get("path")
            sentinel_ts = float(sentinel_data.get("timestamp", 0))
            if sentinel_diag_path and (now_ts - sentinel_ts) < throttle_window_seconds:
                if Path(sentinel_diag_path).exists():
                    recent_diag_path = sentinel_diag_path
    except Exception as sentinel_err:
        logger.error(f"Gemini diagnostic sentinel read failed: {sentinel_err}")

    if recent_diag_path is None:
        try:
            if diagnostics_dir.exists():
                for diag_file in diagnostics_dir.glob("ssl_diagnostic_*.json"):
                    try:
                        with open(diag_file, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                        if payload.get("event") != "gemini_sdk_import_failed":
                            continue
                        file_mtime = diag_file.stat().st_mtime
                        if (now_ts - file_mtime) < throttle_window_seconds:
                            if (
                                recent_diag_path is None
                                or file_mtime > Path(recent_diag_path).stat().st_mtime
                            ):
                                recent_diag_path = str(diag_file)
                    except Exception as diag_file_err:
                        logger.debug(
                            "Skipping diagnostic file scan due to parse/read error: %s (%s)",
                            diag_file,
                            diag_file_err,
                        )
                        continue
        except Exception as scan_err:
            logger.error(f"Gemini diagnostic scan failed: {scan_err}")

    if recent_diag_path:
        logger.warning(f"Gemini SDK import failed, recent diagnostic exists: {recent_diag_path}")
    else:
        try:
            diag_path = write_ssl_diagnostic(
                event="gemini_sdk_import_failed",
                output_dir=str(diagnostics_dir),
                error=import_error,
                extra={"stage": "first_use_import"},
            )
            if diag_path:
                logger.warning(f"Gemini SDK import failed, diagnostic saved: {diag_path}")
                try:
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(
                        sentinel_path,
                        {
                            "path": diag_path,
                            "timestamp": now_ts,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                except Exception as sentinel_write_err:
                    logger.error(f"Gemini diagnostic sentinel write failed: {sentinel_write_err}")
        except Exception as diag_err:
            logger.error(f"Gemini SDK diagnostic write failed: {diag_err}")


class _GeminiMixin:
    def _tools_for_gemini_live(self) -> List[Any]:
        """Gemini Live SDK ``tools`` config — list of ``types.Tool``.
        Returns ``[]`` if no tools so caller can decide to keep the
        existing google_search Tool intact."""
        if not self.has_tools() or types is None:
            return []
        decls = [t.to_gemini_function_declaration() for t in self._tool_definitions]
        return [types.Tool(function_declarations=decls)]

    async def _connect_gemini(self, instructions: str, native_audio: bool = True) -> None:
        """Establish connection with Gemini Live API using google-genai SDK."""
        if not _ensure_gemini_sdk() or genai is None or types is None:
            detail = f": {_GEMINI_IMPORT_ERROR}" if _GEMINI_IMPORT_ERROR else ""
            raise RuntimeError(
                "google-genai SDK unavailable. "
                "If this is an SSL/证书问题, repair your system certificate chain or switch to non-Gemini API"
                f"{detail}"
            )

        try:
            # Create the Gemini client.
            self._gemini_client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})

            # Configure the session. Gemini Live accepts multiple Tool instances simultaneously,
            # one for google_search and another for custom function_declarations.
            gemini_tools: List[Any] = [types.Tool(google_search=types.GoogleSearch())]
            if self.has_tools():
                gemini_tools.extend(self._tools_for_gemini_live())

            gemini_voice, voice_recognized = normalize_gemini_tts_voice(self.voice)
            if self.voice and not voice_recognized:
                logger.warning(
                    "Gemini Live voice '%s' is not in the supported catalog; falling back to '%s'",
                    self.voice,
                    gemini_voice,
                )

            config = {
                "response_modalities": ["AUDIO"],
                "system_instruction": instructions,
                "media_resolution": types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "tools": gemini_tools,
                "generation_config": {"temperature": 1.1},
                "input_audio_transcription": {},
                "output_audio_transcription": {},
                "speech_config": types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=gemini_voice)
                    )
                ),
            }

            # MANUAL turn detection: disable Gemini's automatic activity
            # detection so end-of-turn is signalled explicitly by the
            # client (audio_stream_end / activity_end). SERVER_VAD path
            # leaves automatic_activity_detection at SDK default (enabled).
            if self.turn_detection_mode == TurnDetectionMode.MANUAL:
                config["realtime_input_config"] = types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=True
                    )
                )

            # Establish Live connection: connect() returns an async context manager.
            logger.info(f"Connecting to Gemini Live API with model: {self.model}")
            self._gemini_context_manager = self._gemini_client.aio.live.connect(
                model=self.model,
                config=config,
            )
            # Enter the async context manager manually.
            self._gemini_session = await self._gemini_context_manager.__aenter__()

            # Set ws to session for compatibility checks.
            self.ws = self._gemini_session
            self._on_connection_attached()
            self._fatal_error_occurred = False
            self._gemini_user_transcript = ""
            self._gemini_user_transcript_after_interrupt = False

            self._last_speech_time = time.time()
            self.instructions = instructions
            logger.info("✅ Gemini Live API connected successfully")

        except Exception as e:
            error_msg = f"Failed to connect to Gemini Live API: {e}"
            logger.error(error_msg)
            self._fatal_error_occurred = True
            if self.on_connection_error:
                await self.on_connection_error(error_msg)
            raise

    async def _stream_audio_gemini(
        self,
        audio_chunk: bytes,
        *,
        raise_on_error: bool = True,
    ) -> None:
        """Send audio data to Gemini Live API."""
        session = self._gemini_session
        if session is None:
            if raise_on_error:
                raise ConnectionError("Gemini audio session is not connected")
            return
        connection_generation = self._connection_generation

        def send_is_current() -> bool:
            return (
                self._gemini_session is session
                and self._connection_generation == connection_generation
            )

        try:
            # Send real-time audio input.
            await session.send_realtime_input(
                audio={"data": audio_chunk, "mime_type": "audio/pcm"}
            )
            if send_is_current():
                self._last_speech_time = time.time()
        except Exception as e:
            logger.error(f"Error sending audio to Gemini: {e}")
            if send_is_current() and "closed" in str(e).lower():
                self._fatal_error_occurred = True
            if raise_on_error:
                raise

    async def signal_user_activity_end(self) -> None:
        """Explicitly signal end-of-turn in MANUAL VAD mode.

        With ``TurnDetectionMode.MANUAL`` the server-side VAD is
        disabled, so the client owns turn boundaries and must emit a
        provider-specific signal when the user stops speaking. Without
        this, the model will never see a turn boundary and never
        respond.

        Per provider (MANUAL only — no-op in SERVER_VAD):
        - Gemini Live: ``send_realtime_input(activity_end=ActivityEnd())``
          (Google genai SDK ``LiveClientRealtimeInput`` docs:
          "If automatic voice detection is disabled, the client must
          send activity signals." ``audio_stream_end`` is NOT applicable
          here — it's documented as "only when automatic activity
          detection is enabled".)
        - OpenAI / Qwen / GLM / Step / Free: ``input_audio_buffer.commit``
          followed by ``response.create``.
        """
        if self.turn_detection_mode != TurnDetectionMode.MANUAL:
            return
        if self._fatal_error_occurred:
            return
        self.note_user_turn_started()
        voice_handoff_input_sequence = getattr(
            self,
            "_voice_handoff_input_sequence",
            0,
        )
        # This commit is the turn boundary for both providers below. Read the
        # owner NOW so frames streamed while the commit is in flight cannot move
        # it, but only pin it once the boundary actually reached the provider:
        # a commit that never went out produces no transcript, so a freeze left
        # behind would answer for the next utterance instead.
        pending_route_identity = self._pending_input_route_identity_commit()
        if self._is_gemini:
            if not self._gemini_session:
                return
            if types is None:
                logger.error("signal_user_activity_end: genai.types unavailable")
                return
            try:
                await self._gemini_session.send_realtime_input(
                    activity_end=types.ActivityEnd()
                )
            except Exception as e:
                logger.error(f"Error sending activity_end to Gemini: {e}")
                if "closed" in str(e).lower():
                    self._fatal_error_occurred = True
                return
            self._apply_input_route_identity_commit(pending_route_identity)
            self._note_voice_handoff_input_boundary(
                expected_sequence=voice_handoff_input_sequence
            )
            return
        # The committed buffer excludes the ~21ms tail soxr still holds in the
        # uplink resampler; drop it so it isn't prepended to the next turn.
        self._clear_uplink_resampler()
        suffix = str(time.time_ns())
        ticket = await self._response_arbiter.enqueue(
            source="manual_audio_commit",
            events_before_response=(
                {
                    "type": "input_audio_buffer.commit",
                    "event_id": f"event_audio_commit_{suffix}",
                },
            ),
            response_event={
                "type": "response.create",
                "event_id": f"event_audio_response_{suffix}",
            },
            priority=0,
        )
        await ticket.sent
        self._apply_input_route_identity_commit(pending_route_identity)
        self._note_voice_handoff_input_boundary(
            expected_sequence=voice_handoff_input_sequence
        )

    async def _gemini_send_user_turn(
        self,
        text: str,
        *,
        images_bytes: tuple[bytes, ...] = (),
        image_mime_type: str = GEMINI_TURN_IMAGE_MIME,
        starts_user_turn: bool = True,
    ) -> None:
        """Inject one Gemini user turn and trigger a response via
        ``send_client_content(turn_complete=True)``.

        This is Gemini Live's idiomatic equivalent of OpenAI-Realtime's
        ``conversation.item.create(role=user) + response.create``. Shared by
        ``_create_response_gemini`` (callers choose error policy) and
        ``inject_text_and_request_response`` (proactive — must propagate
        errors so the caller can re-queue). Errors propagate here; callers
        that need to swallow wrap it.
        """
        # Proactive plugin notifications use a provider ``role=user`` message
        # as a transport detail, but they do not replace the real user's turn.
        # Keep that distinction explicit so a notification cannot cancel a
        # still-running tool call owned by the current user turn.
        if starts_user_turn:
            self.note_user_turn_started()
        from google.genai import types as genai_types

        parts = []
        for image_bytes in images_bytes:
            parts.append(
                genai_types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=image_mime_type,
                )
            )
        parts.append(genai_types.Part(text=text))
        content = genai_types.Content(
            parts=parts,
            role="user",
        )
        # Capture identity **before sending**. A new pending debt may be armed during this await, but it is not
        # delivered by this send. Reading only global state afterward would make an earlier-started send returning now
        # incorrectly claim that debt, starting TTL before the subsequent content is available.
        delivering_debt_id = (
            getattr(self, "_gemini_cancelled_terminal_id", None)
            if getattr(self, "_gemini_cancelled_terminal_awaiting_delivery", False)
            else None
        )
        await self._gemini_session.send_client_content(
            turns=[content],
            turn_complete=True,
        )
        # Gemini has no response.cancel: the interrupted turn is stopped only when **this content is delivered**,
        # The provider owes a terminal event only from this point. If the deadline still starts at handle_interruption,
        # slow ASR handoff and this send (plus image compression for multimodal input) can expire it before the provider receives
        # the interruption, causing A's terminal event to settle the successor token as the current turn; precisely
        # the regression this change fixes.
        # Refresh only once: renewing on every send would indefinitely extend a debt never cleared by a terminal event.
        # Use getattr for both fields: substitute clients without full construction call this send directly
        # (tests/unit/test_proactive_sm_integration.py contains one), and
        # _consume_cancelled_terminal() already reads the same fields this way.
        if (
            delivering_debt_id is not None
            and getattr(self, "_gemini_cancelled_terminal_id", None)
            is delivering_debt_id
            and getattr(self, "_gemini_cancelled_terminal_pending", False)
            and getattr(
                self, "_gemini_cancelled_terminal_awaiting_delivery", False
            )
        ):
            self._gemini_cancelled_terminal_awaiting_delivery = False
            self._gemini_cancelled_terminal_deadline = (
                time.monotonic() + GEMINI_CANCELLED_TERMINAL_TTL_SECONDS
            )

    async def _create_response_gemini(
        self,
        instructions: str,
        *,
        raise_on_error: bool = False,
        starts_user_turn: bool = True,
    ) -> None:
        """Send text content to Gemini and trigger response."""
        if not self._gemini_session:
            logger.warning("Gemini session not available for create_response")
            if raise_on_error:
                raise RuntimeError("Gemini session not available for create_response")
            return

        # Skip empty sends to avoid contaminating Gemini conversation history during warmup.
        if not instructions or not instructions.strip():
            logger.info("Gemini: skipping empty content (warmup or empty message)")
            return

        try:
            await self._gemini_send_user_turn(
                instructions,
                starts_user_turn=starts_user_turn,
            )
            logger.info("Gemini: sent client content, waiting for response")
        except Exception as e:
            logger.error(f"Error sending client content to Gemini: {e}")
            if raise_on_error:
                raise

    async def _create_response_gemini_with_skip_guard(
        self,
        instructions: str,
        *,
        skipped: bool = False,
        raise_on_error: bool = False,
        starts_user_turn: bool = True,
    ) -> None:
        """Set Gemini skip state only for a successfully-started skipped turn."""
        if not skipped:
            await self._create_response_gemini(
                instructions,
                raise_on_error=raise_on_error,
                starts_user_turn=starts_user_turn,
            )
            return

        previous_skip = self._skip_until_next_response
        self._skip_until_next_response = True
        try:
            await self._create_response_gemini(
                instructions,
                raise_on_error=raise_on_error,
                starts_user_turn=starts_user_turn,
            )
        except Exception:
            self._skip_until_next_response = previous_skip
            raise

    async def _send_tool_result_gemini(
        self,
        results: List[ToolResult],
        *,
        provider_session=None,
        owner=None,
    ) -> None:
        """Gemini Live SDK — batch all tool results into one
        ``send_tool_response`` call (matches the SDK's expectation when
        the model issues multiple parallel function calls)."""
        session = provider_session if provider_session is not None else self._gemini_session
        if not session or not results:
            return
        if owner is not None and not self._tool_task_owner_is_current(owner):
            return
        if types is None:  # SDK unavailable — should never hit here
            return
        function_responses = []
        for r in results:
            payload = r.output if isinstance(r.output, dict) else {"result": r.output}
            kw = {"name": r.name, "response": payload}
            if r.call_id:
                kw["id"] = r.call_id
            function_responses.append(types.FunctionResponse(**kw))
        source = (owner, object()) if owner is not None else None
        if source is not None:
            # Install before the SDK await: model output can arrive before the
            # send returns. A new user/connection still invalidates this owner.
            self._gemini_tool_response_source = source
        try:
            await session.send_tool_response(function_responses=function_responses)
        except Exception as e:
            # A failed SDK await does not prove that no bytes reached Gemini.
            # Any resulting reply remains local until its provider terminal.
            logger.error("Gemini send_tool_response failed: %s", e)

    async def _close_gemini(self) -> None:
        """Close Gemini Live API session.

        An async context manager is one-shot: a ``__aexit__()`` interrupted by
        a cancel cannot be resumed by calling it again — the cleanup generator
        has already been unwound, and the second call returns or raises without
        redoing what was interrupted. So the exit must not be interrupted in
        the first place. It runs as a task this client owns and every caller
        awaits it through ``shield``, which matters because a caller here can
        genuinely be cancelled: besides ``close()``, the Gemini proactive
        quarantine in ``_responses.py`` calls this from a fired task.
        """
        if (
            not self._gemini_context_manager
            and not self._gemini_close_retry_contexts
            and getattr(self, "_gemini_proactive_submit_task", None) is None
            and getattr(self, "_gemini_external_submit_task", None) is None
        ):
            return
        # A completed close task may have failed while the SDK context was
        # still retained for a physical-close retry.  Do not keep awaiting the
        # same failed task forever; the next caller must get a fresh attempt.
        close_task = getattr(self, "_gemini_close_task", None)
        if close_task is not None and close_task.done():
            try:
                close_error = close_task.exception()
            except asyncio.CancelledError:
                close_error = asyncio.CancelledError()
            if (close_error is not None or self._gemini_context_manager is not None
                    or self._gemini_close_retry_contexts):
                self._gemini_close_task = None
        await self._own_teardown("_gemini_close_task", self._detach_for_gemini_close)

    async def _retry_gemini_contexts(self, contexts) -> None:
        """Retry captured retired SDK owners, never reread a replacement."""
        for context, session in contexts:
            retained = self._gemini_close_retry_contexts.get(id(context))
            if retained is not None and retained[0] is context:
                await self._close_gemini_context(context, session)

    def _detach_for_gemini_close(self):
        """Seize the context to exit, synchronously (see ``_own_teardown``)."""

        tool_tasks = self._advance_tool_scope()
        return self._close_gemini_with_retries(
            self._gemini_context_manager,
            self._gemini_session,
            tool_tasks,
            retired_contexts=tuple(
                pair for pair in self._gemini_close_retry_contexts.values()
                if pair[0] is not self._gemini_context_manager
            ),
            proactive_submit_task=getattr(
                self, "_gemini_proactive_submit_task", None
            ),
            external_submit_task=getattr(
                self, "_gemini_external_submit_task", None
            ),
        )

    async def _close_gemini_with_retries(
        self, context, session, tool_tasks, *, retired_contexts, **submit_tasks,
    ) -> None:
        # All owners were captured before spawning this shielded teardown.
        # A connection attaching during either await is never ours to close.
        await self._close_gemini_context(context, session, tool_tasks, **submit_tasks)
        await self._retry_gemini_contexts(retired_contexts)

    async def _cancel_gemini_submit_tasks(
        self,
        proactive_submit_task=None,
        external_submit_task=None,
    ) -> None:
        """Cancel and join the SDK sends a closing Gemini session still owns."""

        await self._cancel_gemini_proactive_submit(
            session_closing=True,
            submit_task=proactive_submit_task,
        )
        if (
            external_submit_task is not None
            and external_submit_task is not asyncio.current_task()
            and not external_submit_task.done()
        ):
            external_submit_task.cancel()
            await asyncio.gather(external_submit_task, return_exceptions=True)

    async def _close_gemini_context(
        self,
        context,
        session,
        tool_tasks=(),
        *,
        proactive_submit_task=None,
        external_submit_task=None,
    ) -> None:
        """Exit one Gemini context exactly once, even across replacements."""

        if context is None:
            await self._cancel_gemini_submit_tasks(
                proactive_submit_task, external_submit_task
            )
            await self._await_retired_tool_tasks(tool_tasks)
            return
        registry = self._gemini_context_close_tasks
        key = id(context)
        existing = registry.get(key)
        if existing is not None and existing[0] is context:
            close_task = existing[1]
            # The context is already being exited by an earlier caller, but the
            # submit tasks WE seized are ours to cancel either way -- cancelling
            # an already-finished one is a no-op.
            await self._cancel_gemini_submit_tasks(
                proactive_submit_task, external_submit_task
            )
            await self._await_retired_tool_tasks(tool_tasks)
        else:
            close_task = asyncio.create_task(
                self._close_gemini_impl(
                    context,
                    session,
                    tool_tasks,
                    proactive_submit_task=proactive_submit_task,
                    external_submit_task=external_submit_task,
                )
            )
            registry[key] = (context, close_task)

            def _forget_finished_context(done_task) -> None:
                current = registry.get(key)
                if (
                    current is not None
                    and current[0] is context
                    and current[1] is done_task
                ):
                    registry.pop(key, None)

            close_task.add_done_callback(_forget_finished_context)
        try:
            await asyncio.shield(close_task)
        finally:
            current = registry.get(key)
            if (
                close_task.done()
                and current is not None
                and current[0] is context
                and current[1] is close_task
            ):
                registry.pop(key, None)

    async def _close_gemini_impl(
        self,
        context,
        session,
        tool_tasks=(),
        *,
        proactive_submit_task=None,
        external_submit_task=None,
    ) -> None:
        await self._cancel_gemini_submit_tasks(
            proactive_submit_task, external_submit_task
        )
        await self._await_retired_tool_tasks(tool_tasks)
        if context is None:
            return
        context_key = id(context)
        retry_transport = context_key in self._gemini_close_retry_contexts
        close_error = None
        try:
            await context.__aexit__(None, None, None)
        except Exception as e:
            # A raised exit does not prove that the SDK transport physically
            # exited. Retain that uncertainty for the session registry instead
            # of acknowledging a safe handoff.
            close_error = e
            logger.error(f"Error closing Gemini session: {e}")

        if close_error is not None or retry_transport:
            # google-genai's asynccontextmanager can be exhausted after a
            # failed __aexit__; a second exit may return without touching its
            # WebSocket. The retained AsyncSession owns the actual socket, so
            # use its close() as the physical-release confirmation.
            session_close = getattr(session, "close", None)
            if not callable(session_close):
                if close_error is None:
                    close_error = RuntimeError(
                        "Gemini session has no retryable close operation"
                    )
            else:
                try:
                    await session_close()
                except Exception as e:
                    logger.error(f"Error closing Gemini transport: {e}")
                    if close_error is None:
                        close_error = e
                else:
                    self._gemini_close_retry_contexts.pop(context_key, None)
                    # The SDK context reported an error, but the retained
                    # session has now confirmed the underlying transport is
                    # closed, which is the ownership condition we need.
                    close_error = None

        if close_error is not None:
            # Keep strong references even if a replacement overwrote the
            # current fields while this exit awaited. A later retirement can
            # retry the authoritative SDK transport close. The
            # connection registry will keep its capacity slot occupied until
            # one such retry completes successfully.
            if self._gemini_context_manager is context:
                logger.warning(
                    "Gemini close failed; retaining the context for a later retry"
                )
            self._gemini_close_retry_contexts[context_key] = (context, session)
            raise close_error

        if self._gemini_context_manager is not context:
            # A replacement session attached while the SDK exit ran. Its
            # references — and the client-wide state below — are not ours to
            # clear; ours was the context we just exited.
            logger.info(
                "Gemini close: a replacement session attached; leaving its state alone"
            )
            return

        self._gemini_context_manager = None
        if self._gemini_session is session:
            self._gemini_session = None
        if self.ws is session:
            self.ws = None

        # Reset silence-timeout state (consistent with normal close()).
        self._silence_timeout_triggered = False
        self._last_speech_time = None
        self._silence_reset_pending = False
        self._last_silence_clear_speech_time = 0.0
        self._last_local_loud_time = 0.0
        self._client_vad_active = False
        self._client_vad_last_speech_time = 0.0
        self._speech_detect_start = 0.0
        self._rnnoise_vad_active = False
        self._user_recent_activity_time = 0.0
        self._ai_recent_activity_time = 0.0

        # Reset audio processor state.
        if self._audio_processor is not None:
            self._audio_processor.reset()

        logger.info("Gemini Live API session closed")

    async def _handle_messages_gemini(self) -> None:
        """Handle messages from Gemini Live API."""
        provider_session = self._gemini_session
        if not provider_session:
            logger.error("Gemini session not established")
            return
        connection_generation = self._connection_generation
        try:
            while not self._fatal_error_occurred:
                if (
                    connection_generation != self._connection_generation
                    or provider_session is not self._gemini_session
                ):
                    logger.info(
                        "Gemini receive loop retired after a replacement connection attached"
                    )
                    return
                try:
                    # Receive the response stream.
                    turn = provider_session.receive()
                    async for response in turn:
                        await self._process_gemini_response(
                            response,
                            provider_session=provider_session,
                            connection_generation=connection_generation,
                        )
                    # receive() is a session-level async generator that exits only on disconnection;
                    # this line does not run during a normal session. The missing-turn_complete fallback now uses
                    # model_turn interval detection inside _process_gemini_response.
                    self._is_responding = False
                except asyncio.CancelledError:
                    logger.info("Gemini message handler cancelled")
                    break
                except Exception as e:
                    error_msg = str(e)
                    # Detect normal closure: contains 'closed' or WebSocket normal close code 1000.
                    if "closed" in error_msg.lower() or "1000" in error_msg:
                        logger.info("Gemini session closed")
                        break
                    else:
                        logger.error(f"Error receiving Gemini response: {e}")
                        if (
                            connection_generation == self._connection_generation
                            and provider_session is self._gemini_session
                            and self.on_connection_error
                        ):
                            await self.on_connection_error(error_msg)
                        break
        except Exception as e:
            logger.error(f"Gemini message handler error: {e}")
        finally:
            outcome_owner = getattr(self, "_gemini_proactive_outcome_owner", None)
            if (
                outcome_owner is not None
                and outcome_owner[0] == connection_generation
                and outcome_owner[1] is provider_session
            ):
                self._settle_gemini_proactive_inject(
                    error_msg="Gemini realtime message loop ended",
                    expected_connection_generation=connection_generation,
                    expected_provider_session=provider_session,
                    expected_outcome_token=outcome_owner[2],
                )
            if self._still_owns_connection(connection_generation):
                outcome_token = getattr(
                    self,
                    "_gemini_external_outcome_token",
                    None,
                )
                if outcome_token is not None:
                    self._settle_gemini_external_turn(outcome_token)

    async def _process_gemini_response(
        self,
        response,
        *,
        provider_session=None,
        connection_generation: int | None = None,
    ) -> None:
        """Process a single Gemini response event."""
        if connection_generation is None:
            connection_generation = self._connection_generation
        if not self._still_owns_connection(connection_generation):
            return
        handoff_turn_epoch = self._current_turn_epoch
        handoff_input_sequence = getattr(
            self, "_voice_handoff_response_input_sequence", 0,
        )
        external_outcome_token = getattr(
            self,
            "_gemini_external_outcome_token",
            None,
        )
        tool_response_source = getattr(self, "_gemini_tool_response_source", None)
        server_content = getattr(response, "server_content", None)
        source_terminal = bool(server_content and (
            getattr(server_content, "turn_complete", False)
            or getattr(server_content, "interrupted", False)
        ))
        try:
            # Handle tool calls: dispatch every function_calls entry to
            # ``on_tool_call`` and return all results together via ``send_tool_response``
            # (Gemini Live expects a batch response, rather than individual responses).
            session = provider_session if provider_session is not None else self._gemini_session
            def event_owner_is_current() -> bool:
                return bool(
                    provider_session is None
                    or (
                        session is self._gemini_session
                        and connection_generation == self._connection_generation
                    )
                )

            def settle_event_outcome(error_msg=None) -> None:
                if provider_session is None:
                    self._settle_gemini_proactive_inject(error_msg=error_msg)
                    return
                owner = getattr(self, "_gemini_proactive_outcome_owner", None)
                if (
                    owner is not None
                    and owner[0] == connection_generation
                    and owner[1] is session
                ):
                    owner_scope = owner[4] if len(owner) > 4 else None
                    if (
                        owner_scope is not None
                        and owner_scope
                        != getattr(self, "_tool_scope_generation", 0)
                    ):
                        # This terminal belongs to the USER's turn, not to the
                        # proactive inject: the connection and the session both
                        # survive a new user turn, so only the scope tells them
                        # apart. Settling it as a COMPLETION would report the
                        # notification delivered on the strength of a response
                        # that was abandoned, and the caller drops the callback
                        # instead of re-queueing it. Force a rejection so it
                        # goes back in the queue for the live turn.
                        error_msg = error_msg or (
                            "Gemini proactive response was abandoned by a new "
                            "user turn"
                        )
                    self._settle_gemini_proactive_inject(
                        error_msg=error_msg,
                        expected_connection_generation=connection_generation,
                        expected_provider_session=session,
                        expected_outcome_token=owner[2],
                    )

            if not event_owner_is_current():
                return
            if hasattr(response, 'tool_call') and response.tool_call:
                fcs = list(getattr(response.tool_call, 'function_calls', []) or [])
                if fcs:
                    calls = []
                    for fc in fcs:
                        args = dict(getattr(fc, 'args', None) or {})
                        calls.append(ToolCall(
                            name=getattr(fc, 'name', '') or '',
                            arguments=args,
                            call_id=getattr(fc, 'id', '') or '',
                            raw_arguments=json.dumps(args, ensure_ascii=False),
                        ))
                    if self.on_tool_call is None:
                        logger.warning(
                            "Gemini tool_call received but no on_tool_call handler — replying with error"
                        )
                    owner = self._capture_tool_task_owner(
                        session,
                        connection_generation=connection_generation,
                    )
                    self._start_gemini_tool_batch(calls, owner)
                    # Also own this frame's dispatch token if a compatible
                    # transport combines the call and terminal in one frame.
                    tool_response_source = self._gemini_tool_response_source

            cancellation = getattr(response, 'tool_call_cancellation', None)
            if cancellation:
                self._cancel_tool_call_ids(list(getattr(cancellation, 'ids', None) or []))

            vad_signal = getattr(response, 'voice_activity_detection_signal', None)
            vad_signal_type = getattr(vad_signal, 'vad_signal_type', None)
            if vad_signal_type is not None and str(getattr(vad_signal_type, 'value', vad_signal_type)).endswith("_SOS"):
                self.note_user_turn_started()

            # Check for server content.
            if response.server_content:
                server_content = response.server_content

                # Handle user-input transcription: accumulate without immediate sending to avoid fragmented display.
                if hasattr(server_content, 'input_transcription') and server_content.input_transcription:
                    input_trans = server_content.input_transcription
                    if hasattr(input_trans, 'text') and input_trans.text:
                        self._gemini_user_transcript += input_trans.text
                        if self._interrupted:
                            self._gemini_user_transcript_after_interrupt = True

                # Check for AI content (model_turn or output_transcription).
                has_ai_content = (
                    server_content.model_turn or 
                    (hasattr(server_content, 'output_transcription') and server_content.output_transcription)
                )

                # Warning: detect turn start whichever arrives first, model_turn or output_transcription.
                if has_ai_content and not self._is_responding:
                    # Distinguish a genuinely new turn from late frames of the previous turn using two criteria:
                    #   A. User spoke after the last AI frame → necessarily a new turn (back-and-forth).
                    #   B. Last AI frame is older than window → sufficiently long silence also means a new turn.
                    # Treat as late continuation only when neither holds (short silence + no user speech),
                    # covering late audio after premature Gemini turn_complete
                    # or a long response split into sub-turns.
                    # Earlier versions used only the time window, misclassifying fast exchanges (AI→user→AI in <3s)
                    # as late continuation, merging bubbles / delaying user_transcript flush
                    # (Codex P1 feedback). Adding user-speech comparison handles both cases correctly.
                    _user_spoke_after_ai = (
                        self._user_recent_activity_time > self._ai_recent_activity_time
                    )
                    _still_within_ai_window = (
                        self._ai_recent_activity_time > 0
                        and time.time() - self._ai_recent_activity_time
                        <= self._ai_recent_activity_window
                    )
                    _is_new_turn = _user_spoke_after_ai or not _still_within_ai_window
                    _can_clear_interrupted = (
                        not self._interrupted
                        or self._gemini_user_transcript_after_interrupt
                        or not _still_within_ai_window
                    )
                    # The epoch bump below has no reader on this path today: a
                    # Gemini client never enqueues through the arbiter (every
                    # entry point takes an ``_is_gemini`` branch first), so
                    # ``_on_arbiter_stuck_release`` — the epoch's only consumer
                    # — cannot fire here. Maintained anyway because the
                    # invariant is "every turn start advances the epoch", not
                    # "every turn start something currently reads": once a turn
                    # start stops advancing it, a release that DOES run cannot
                    # tell that turn from its successor. The host-turn sample
                    # (#2612) is maintained here for the same reason and is
                    # equally unread today — this path ends its turn by calling
                    # ``on_response_done`` directly rather than through
                    # ``_notify_turn_finished``, which is where the comparison
                    # lives. Tracked with the rest of that divergence.
                    #
                    # Kept above the assignment, not between it and the bump:
                    # ``test_every_turn_start_advances_the_epoch`` discovers
                    # turn starts by proximity, so a comment wedged in there
                    # reads as a missing bump.
                    self._is_responding = True
                    self._turn_epoch += 1
                    self._current_turn_epoch = self._turn_epoch
                    self._current_turn_host_id = self._read_host_turn_id()
                    if _is_new_turn and _can_clear_interrupted:
                        # Only a recognized new response may claim this input.
                        # Canceled/late content still advances the legacy epoch,
                        # but must not borrow a successor's handoff marker.
                        handoff_turn_epoch = self._current_turn_epoch
                        handoff_input_sequence = getattr(
                            self, "_voice_handoff_input_sequence", 0,
                        )
                        self._voice_handoff_response_input_sequence = handoff_input_sequence
                        # A new turn means the old turn ended: invalidate its debt so a never-terminating old turn
                        # cannot consume the next **valid** terminal event, leave its token unsettled forever,
                        # pin the session as busy, and completely silence proactive speech.
                        #
                        # Warning: the criterion must **exactly match** the new-turn declaration below. Checking only
                        # _is_new_turn also accepts late content from the cancelled turn itself
                        # (the user already spoke after the last AI frame), clearing the debt before its actual
                        # terminal event arrives; that terminal then settles the newly minted
                        # external token, marking the session idle while the turn is still in flight.
                        #
                        # Warning: known residual accepted after product assessment; do not restore the looser criterion:
                        # 'late content from A' and 'first content from successor B' look identical
                        # at protocol level (both model_turn / output_transcription, both without
                        # turn identifiers, both after interruption). _turn_epoch increments for both,
                        # so they cannot be distinguished. Two mutually exclusive choices remain:
                        #   Invalidate upon content → A's terminal settles B's token while B is still
                        #     speaking, marking the session idle (the **common** interruption path);
                        #   Keep the debt (current behavior) → if the debt is spurious or A sends neither terminal type,
                        #     B's own terminal is consumed, leaving B's token unsettled.
                        # Choose the latter: this residual affects only proactive speech (all is_active_response
                        # reads are in the proactive path), and its first gate,
                        # proactive.py's trigger_agent_callbacks, defers **before generating dialogue**,
                        # simply skipping without spending tokens. The user's own conversation path
                        # never reads this state and cannot stall because of it.
                        #
                        # The opposite risk (old turn never terminates) has two independent bounds:
                        # when _interrupted is true, both _ai_recent_activity_time refresh points
                        # are skipped; after 3s _still_within_ai_window becomes false and this criterion automatically
                        # passes. If no AI content ever arrives afterward, this branch is never reached,
                        # and _consume_cancelled_terminal() provides a deadline fallback.
                        self._gemini_cancelled_terminal_pending = False
                        # Clear the deadline together with the debt: the other exits (consumption, connection replacement)
                        # already clear them together; an orphan deadline only makes state interpretation ambiguous.
                        self._gemini_cancelled_terminal_deadline = None
                        self._gemini_cancelled_terminal_awaiting_delivery = False
                        self._gemini_cancelled_terminal_id = None
                    if _is_new_turn and _can_clear_interrupted:
                        # Gemini has no response.created event; clear stale interrupt state only
                        # after SDK transcription or a quiet gap proves this is not a canceled tail.
                        self._interrupted = False
                        # Send accumulated user input before the AI starts responding.
                        if self._gemini_user_transcript and (
                            self.on_input_transcript
                            or self.on_input_transcript_with_route
                        ):
                            await self._deliver_input_transcript(
                                self._gemini_user_transcript
                            )
                            if not event_owner_is_current():
                                return
                            self._gemini_user_transcript = ""  # Clear accumulation.
                        self._gemini_user_transcript_after_interrupt = False
                        self._is_first_text_chunk = True  # Reset the first-chunk flag.
                        self._gemini_current_transcript = ""  # Clear accumulation.
                        if not self._skip_until_next_response and not self._interrupted and self.on_new_message:
                            await self.on_new_message()
                            if not event_owner_is_current():
                                return
                            # Core rotates the host speech id while opening the
                            # new message. Gemini tool calls can arrive in a
                            # later standalone event for this same provider
                            # turn, so keep their ownership snapshot aligned.
                            self._current_turn_host_id = self._read_host_turn_id()
                    else:
                        logger.debug(
                            "Gemini: late content after premature turn_complete/interruption (%.2fs ago), treating as continuation",
                            time.time() - self._ai_recent_activity_time,
                        )

                # Handle output transcription: stream each chunk to the frontend.
                # Does not participate in new-turn detection; late transcription after turn_complete appends
                # to the current turn's bubble with isNewMessage=false (correct behavior).
                if hasattr(server_content, 'output_transcription') and server_content.output_transcription:
                    output_trans = server_content.output_transcription
                    if hasattr(output_trans, 'text') and output_trans.text:
                        text = output_trans.text
                        self._gemini_current_transcript += text
                        if not self._skip_until_next_response and not self._interrupted and self.on_text_delta:
                            self._ai_recent_activity_time = time.time()
                            await self.on_text_delta(text, self._is_first_text_chunk)
                            if not event_owner_is_current():
                                return
                            self._is_first_text_chunk = False

                # Handle model output (audio).
                if server_content.model_turn:
                    for part in server_content.model_turn.parts:
                        # Skip thinking/thought parts.
                        if hasattr(part, 'thought') and part.thought:
                            continue

                        # Handle audio.
                        if hasattr(part, 'inline_data') and part.inline_data:
                            if isinstance(part.inline_data.data, bytes):
                                if not self._skip_until_next_response and not self._interrupted and self.on_audio_delta:
                                    self._ai_recent_activity_time = time.time()
                                    await self.on_audio_delta(part.inline_data.data)
                                    if not event_owner_is_current():
                                        return

                # Check whether the turn is complete (getattr avoids errors if the SDK lacks this field).
                was_interrupted = bool(
                    getattr(server_content, 'interrupted', False)
                )
                # A server event may contain **both** turn_complete and interrupted,
                # executing both terminal branches below. Debt is counted once per **terminal** event:
                #   - Do not consume in each branch: the first sees True and skips settlement, while the second
                #     sees False and uses the old turn's terminal to settle the newly minted token;
                #   - Do not consume on every event either: **nonterminal** late content after cancellation would
                #     clear debt early, letting the old turn's actual terminal settle the new token afterward.
                # Therefore identify terminal events first, then consume only those, exactly once.
                _is_terminal_event = bool(
                    getattr(server_content, 'turn_complete', False)
                ) or was_interrupted
                _owed_to_cancelled = (
                    self._consume_cancelled_terminal()
                    if _is_terminal_event
                    else False
                )
                # Warning: deliberately do **not** trigger on_audio_done here (issue #1566's audio-completion signal;
                # see _transport.py's response.audio.done branch). Gemini (native +
                # lanlan.app free proxy) has only turn_complete as its end signal, and it can
                # precede late audio; three comments in this file already acknowledge this ('late content
                # after premature turn_complete', 'late transcription arriving after turn_complete',
                # 'late audio after premature Gemini turn_complete'). Attaching on_audio_done to
                # turn_complete would recreate 'declaring playback finished before audio finishes', precisely
                # this issue. Gemini continues relying on the frontend give-up timer:
                # missing the signal is an acceptable degradation; sending it early is not.
                if getattr(server_content, 'turn_complete', False):
                    # Gemini Live API does not return token counts; record call counts only.
                    try:
                        from utils.token_tracker import TokenTracker
                        TokenTracker.get_instance().record(
                            model=self.model or "gemini-live",
                            prompt_tokens=0, completion_tokens=0, total_tokens=0,
                            call_type="conversation_realtime_gemini",
                            source="main_logic/omni_realtime_client",
                        )
                    except Exception:
                        pass
                    self._is_responding = False
                    if (
                        not _owed_to_cancelled
                        and external_outcome_token is not None
                        and self._still_owns_connection(connection_generation)
                    ):
                        self._settle_gemini_external_turn(
                            external_outcome_token
                        )
                    if not was_interrupted:
                        settle_event_outcome()
                    if (
                        not was_interrupted
                        and not _owed_to_cancelled
                        and not self._interrupted
                        and handoff_turn_epoch == self._turn_epoch == self._current_turn_epoch
                        and event_owner_is_current()
                    ):
                        # Gemini has no per-event turn ID. Use the existing
                        # turn/cancellation classification and this event's
                        # snapshot, never a marker replaced across callbacks.
                        self._note_voice_handoff_input_boundary(
                            expected_sequence=handoff_input_sequence,
                        )
                    if self._skip_until_next_response:
                        self._skip_until_next_response = False
                        logger.info("Gemini: skipped response (prime_context priming)")
                    elif self.on_response_done:
                        await self.on_response_done()
                        if not event_owner_is_current():
                            return

                # Check for interruption.
                if was_interrupted:
                    if (
                        not _owed_to_cancelled
                        and external_outcome_token is not None
                        and self._still_owns_connection(connection_generation)
                    ):
                        self._settle_gemini_external_turn(
                            external_outcome_token
                        )
                    settle_event_outcome(
                        error_msg="Gemini proactive response interrupted"
                    )
                    if self._skip_until_next_response:
                        self._skip_until_next_response = False
                        logger.info("Gemini: skipped response interrupted, reset skip flag")
                    self._interrupted = True
                    self._is_responding = False
                    # Send accumulated user input on interruption too.
                    if self._gemini_user_transcript:
                        self._gemini_user_transcript_after_interrupt = True
                        if self.on_input_transcript or self.on_input_transcript_with_route:
                            await self._deliver_input_transcript(
                                self._gemini_user_transcript
                            )
                            if not event_owner_is_current():
                                return
                        self._gemini_user_transcript = ""
                    logger.info("Gemini response was interrupted by user")

        except Exception as e:
            logger.error(f"Error processing Gemini response: {e}")
        finally:
            # A terminal only closes the tool response it observed. A result
            # sent during an awaited callback may already own a new token.
            # Do not use the tool scope here: SERVER_VAD can keep that scope
            # unchanged across many independent ordinary voice responses.
            if source_terminal and getattr(self, "_gemini_tool_response_source", None) is tool_response_source:
                self._gemini_tool_response_source = None
