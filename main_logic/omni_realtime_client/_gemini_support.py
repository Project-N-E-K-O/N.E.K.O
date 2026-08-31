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

GEMINI_AVAILABLE: bool | None = None  # None = 尚未尝试导入

_GEMINI_IMPORT_ERROR = None

def _ensure_gemini_sdk() -> bool:
    """Import google-genai on first call and cache the result; emit an SSL diagnostic on failure.

    Returns whether the SDK is available. Under a concurrent race the worst case is one duplicate import (Python's module cache makes it idempotent).
    """
    global genai, types, GEMINI_AVAILABLE, _GEMINI_IMPORT_ERROR
    # 显式强制不可用优先级最高 → 即便对象已塞进全局也降级。
    if GEMINI_AVAILABLE is False:
        return False
    # 对象已就位（真 import 过 / 测试注入了 mock）→ 直接信任，不重导入。
    if genai is not None and types is not None:
        GEMINI_AVAILABLE = True
        return True
    try:
        from google import genai as genai_mod
        from google.genai import types as types_mod
        # 只补缺失的，保住测试可能注入的 genai mock。
        if genai is None:
            genai = genai_mod
        if types is None:
            types = types_mod
        GEMINI_AVAILABLE = True
        _GEMINI_IMPORT_ERROR = None
    except Exception as e:
        # 不覆盖外部强制设过的可用性标志；也不清空可能被测试注入的 genai/types
        # （只补缺失原则——导入失败时保留已注入的部分 mock）。
        if GEMINI_AVAILABLE is None:
            GEMINI_AVAILABLE = False
            _GEMINI_IMPORT_ERROR = e
            _emit_gemini_import_diagnostic(e)
    # 只有可用标志为真且对象确实就位才算可用——避免 forced True 但 import 失败时
    # 谎报可用、让 _connect_gemini 在 None 上解引用 genai/types。
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
            # 创建 Gemini 客户端
            self._gemini_client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})

            # 配置会话。Gemini Live 接受多个 Tool 实例同时存在，
            # 一个负责 google_search、一个负责自定义 function_declarations。
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

            # 建立 Live 连接 - connect() 返回 async context manager
            logger.info(f"Connecting to Gemini Live API with model: {self.model}")
            self._gemini_context_manager = self._gemini_client.aio.live.connect(
                model=self.model,
                config=config,
            )
            # 手动进入 async context manager
            self._gemini_session = await self._gemini_context_manager.__aenter__()

            # 设置 ws 为 session，用于兼容性检查
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

    async def _stream_audio_gemini(self, audio_chunk: bytes) -> None:
        """Send audio data to Gemini Live API."""
        if not self._gemini_session:
            return

        try:
            # 发送实时音频输入
            await self._gemini_session.send_realtime_input(
                audio={"data": audio_chunk, "mime_type": "audio/pcm"}
            )
            self._last_speech_time = time.time()
        except Exception as e:
            logger.error(f"Error sending audio to Gemini: {e}")
            if "closed" in str(e).lower():
                self._fatal_error_occurred = True

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

    async def _gemini_send_user_turn(
        self,
        text: str,
        *,
        images_bytes: tuple[bytes, ...] = (),
        image_mime_type: str = "image/jpeg",
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
        # 身份要在**发送之前**取。这个 await 期间可能有一笔新欠账被武装，而它不是
        # 这次发送送达的 —— 事后只读全局状态，会让一次更早发起、此刻才返回的发送
        # 把它错认成自己送的，从而在后继内容还没上线时就起算 TTL。
        delivering_debt_id = (
            getattr(self, "_gemini_cancelled_terminal_id", None)
            if getattr(self, "_gemini_cancelled_terminal_awaiting_delivery", False)
            else None
        )
        await self._gemini_session.send_client_content(
            turns=[content],
            turn_complete=True,
        )
        # Gemini 没有 response.cancel：被打断那一轮是**这条内容送达**才被叫停的，
        # provider 也是从这一刻起才欠它一条终结。期限若一直按 handle_interruption
        # 的时刻算，ASR 交接加这次发送（多模态还要压图）一慢就会在 provider 收到
        # 中断之前就到点，A 的终结随后被当成当前回合去结算了后继的 token —— 正是
        # 这个改动要修的那个回归。
        # 只重打一次：每次发送都续命的话，一笔始终没被终结抵掉的欠账会被无限延寿。
        # 两个字段都走 getattr：这条 send 会被没走完整构造的替身客户端直接调用
        # （tests/unit/test_proactive_sm_integration.py 就有一个），而
        # _consume_cancelled_terminal() 对同一组字段本来也是这么读的。
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

        # 跳过空内容的发送，避免预热时污染 Gemini 对话历史
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
        try:
            await session.send_tool_response(function_responses=function_responses)
        except Exception as e:
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
            and getattr(self, "_gemini_proactive_submit_task", None) is None
            and getattr(self, "_gemini_external_submit_task", None) is None
        ):
            return
        await self._own_teardown("_gemini_close_task", self._detach_for_gemini_close)

    def _detach_for_gemini_close(self):
        """Seize the context to exit, synchronously (see ``_own_teardown``)."""

        tool_tasks = self._advance_tool_scope()
        return self._close_gemini_context(
            self._gemini_context_manager,
            self._gemini_session,
            tool_tasks,
            proactive_submit_task=getattr(
                self, "_gemini_proactive_submit_task", None
            ),
            external_submit_task=getattr(
                self, "_gemini_external_submit_task", None
            ),
        )

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
        try:
            await context.__aexit__(None, None, None)
        except Exception as e:
            # A raised exit is still an exit that ran to its own conclusion —
            # the references are dropped below either way, as before.
            logger.error(f"Error closing Gemini session: {e}")

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

        # 重置静默超时相关状态（与普通close()保持一致）
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

        # 重置音频处理器状态
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
                    # 接收响应流
                    turn = provider_session.receive()
                    async for response in turn:
                        await self._process_gemini_response(
                            response,
                            provider_session=provider_session,
                            connection_generation=connection_generation,
                        )
                    # receive() 是 session 级 async generator，仅在连接断开时退出；
                    # 正常会话期间此行不会执行。缺失 turn_complete 的兜底已移至
                    # _process_gemini_response 中基于 model_turn 时间间隔的检测。
                    self._is_responding = False
                except asyncio.CancelledError:
                    logger.info("Gemini message handler cancelled")
                    break
                except Exception as e:
                    error_msg = str(e)
                    # 检测正常关闭：包含 "closed" 或者是 WebSocket 1000 正常关闭码
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
        external_outcome_token = getattr(
            self,
            "_gemini_external_outcome_token",
            None,
        )
        try:
            # 处理工具调用 —— 将 function_calls 中每一个调用都派给
            # ``on_tool_call``，结果通过 ``send_tool_response`` 一次性回写
            # （Gemini Live 期望批量回应，而不是逐个）。
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

            cancellation = getattr(response, 'tool_call_cancellation', None)
            if cancellation:
                self._cancel_tool_call_ids(list(getattr(cancellation, 'ids', None) or []))

            vad_signal = getattr(response, 'voice_activity_detection_signal', None)
            vad_signal_type = getattr(vad_signal, 'vad_signal_type', None)
            if vad_signal_type is not None and str(getattr(vad_signal_type, 'value', vad_signal_type)).endswith("_SOS"):
                self.note_user_turn_started()

            # 检查是否有服务器内容
            if response.server_content:
                server_content = response.server_content

                # 处理用户输入转录 - 只累积，不立即发送（避免碎片化显示）
                if hasattr(server_content, 'input_transcription') and server_content.input_transcription:
                    input_trans = server_content.input_transcription
                    if hasattr(input_trans, 'text') and input_trans.text:
                        self._gemini_user_transcript += input_trans.text
                        if self._interrupted:
                            self._gemini_user_transcript_after_interrupt = True

                # 检查是否有 AI 内容（model_turn 或 output_transcription）
                has_ai_content = (
                    server_content.model_turn or 
                    (hasattr(server_content, 'output_transcription') and server_content.output_transcription)
                )

                # ⚠️ 重要：检测 turn 开始 - 无论是 model_turn 还是 output_transcription 先到
                if has_ai_content and not self._is_responding:
                    # 区分"真新 turn"与"上个 turn 的迟到帧"。双判据合取：
                    #   A. 用户在 AI 最后一帧之后发过声 → 必然新 turn（back-and-forth）
                    #   B. AI 最后一帧距今超过 window → 静默够久也算新 turn
                    # 仅当两条都不满足（短静默 + 用户全程没发声）才视为
                    # late continuation —— 这正是 Gemini turn_complete 抢跑的迟到
                    # 音频、或同一长回复被拆 sub-turn 的场景。
                    # 早期版本只用时间窗，会把快速一问一答（AI→用户→AI in <3s）
                    # 误判 late continuation 导致气泡合并 / user_transcript flush 延迟
                    # （Codex P1 反馈）。加用户发声比较后合并两种场景均正确。
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
                        # 新回合开始就说明旧回合已经收场：欠账作废，免得旧回合
                        # 永不终结时把下一条**合法**终结也吃掉，让 token 永远结算
                        # 不掉、会话被钉成「忙」而主动搭话彻底哑掉。
                        #
                        # ⚠️ 判据必须与下面宣告新回合的那条**一字不差**。只看
                        # _is_new_turn 时，被取消那一轮的迟到内容自己就满足它
                        # （用户已经在 AI 最后一帧之后发过声），于是欠账在它真正
                        # 的终结到达之前就被清掉，那条终结转而去结算刚铸出的
                        # external token —— 回合还在飞就显示空闲。
                        #
                        # ⚠️ 已知残留、经产品判断后接受，别再改回松判据：
                        # 「A 的迟到内容」与「后继 B 的第一条内容」在协议层长得
                        # 一模一样（都是 model_turn / output_transcription、都不带
                        # 回合标识、都在打断之后），_turn_epoch 对两者也都自增，
                        # 区分不出来。于是只有两种取法，互斥：
                        #   看到内容就作废 → A 的终结去结算了 B 的 token，B 还在
                        #     说话会话已读作空闲（抢话时的**常见**路径）；
                        #   不作废（现在这样）→ 欠账是虚的、或 A 两种终结都没发
                        #     时，B 自己的终结被吃掉，B 的 token 没人结算。
                        # 取后者：那条残留只影响主动搭话（is_active_response 的
                        # 读取点全在 proactive 一线），而且第一道闸
                        # proactive.py 的 trigger_agent_callbacks 在**生成台词之前**
                        # 就 defer 掉，是纯跳过、不烧 token，用户自己的对话链路
                        # 一个读取点都不碰、不会卡顿。
                        #
                        # 反向风险（旧回合永不终结）有两道界，都不依赖这里：
                        # _interrupted 为真时两个 _ai_recent_activity_time 刷新点
                        # 都被跳过，3s 后 _still_within_ai_window 转假、本判据自动
                        # 放行；而「此后再无 AI 内容」那种连本分支都进不来的情形，
                        # 由 _consume_cancelled_terminal() 的期限兜底。
                        self._gemini_cancelled_terminal_pending = False
                        # 期限跟着欠账一起清：另外两条退路（消费、连接替换）都
                        # 是成对清的，留一个孤儿期限只会让状态读起来有歧义。
                        self._gemini_cancelled_terminal_deadline = None
                        self._gemini_cancelled_terminal_awaiting_delivery = False
                        self._gemini_cancelled_terminal_id = None
                    if _is_new_turn and _can_clear_interrupted:
                        # Gemini has no response.created event; clear stale interrupt state only
                        # after SDK transcription or a quiet gap proves this is not a canceled tail.
                        self._interrupted = False
                        # 在AI开始响应前，发送累积的用户输入
                        if self._gemini_user_transcript and (
                            self.on_input_transcript
                            or self.on_input_transcript_with_route
                        ):
                            await self._deliver_input_transcript(
                                self._gemini_user_transcript
                            )
                            if not event_owner_is_current():
                                return
                            self._gemini_user_transcript = ""  # 清空累积
                        self._gemini_user_transcript_after_interrupt = False
                        self._is_first_text_chunk = True  # 重置第一个 chunk 标记
                        self._gemini_current_transcript = ""  # 清空累积
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

                # 处理输出转录 - 流式发送每个 chunk 到前端
                # 不参与新 turn 检测；turn_complete 后到达的迟到转录会以 isNewMessage=false
                # 追加到当前轮次的气泡（正确行为）
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

                # 处理模型输出 (音频)
                if server_content.model_turn:
                    for part in server_content.model_turn.parts:
                        # 跳过 thinking/thought 部分
                        if hasattr(part, 'thought') and part.thought:
                            continue

                        # 处理音频
                        if hasattr(part, 'inline_data') and part.inline_data:
                            if isinstance(part.inline_data.data, bytes):
                                if not self._skip_until_next_response and not self._interrupted and self.on_audio_delta:
                                    self._ai_recent_activity_time = time.time()
                                    await self.on_audio_delta(part.inline_data.data)
                                    if not event_owner_is_current():
                                        return

                # 检查是否 turn 完成（用 getattr 防止 SDK 无该字段时抛错）
                was_interrupted = bool(
                    getattr(server_content, 'interrupted', False)
                )
                # 一个 server event 可能**同时**带 turn_complete 和 interrupted，
                # 下面两条终结分支都会跑。欠账是「每个**终结**事件一笔」：
                #   - 不能每条分支各消费一次 —— 第一条拿到 True 跳过结算，第二条
                #     拿到 False 就用旧那一轮的终结把新铸的 token 结算掉；
                #   - 也不能每个事件都消费 —— 取消之后的**非终结**迟到内容会把欠账
                #     提前清掉，随后旧回合真正的终结就把新 token 当成可结算对象。
                # 所以：先判定这是不是终结事件，是才消费，且只消费一次。
                _is_terminal_event = bool(
                    getattr(server_content, 'turn_complete', False)
                ) or was_interrupted
                _owed_to_cancelled = (
                    self._consume_cancelled_terminal()
                    if _is_terminal_event
                    else False
                )
                # ⚠️ 这里刻意【不】触发 on_audio_done（issue #1566 的音频完结信号，
                # 见 _transport.py 的 response.audio.done 分支）。Gemini（原生 +
                # lanlan.app free 代理）唯一的结束信号就是 turn_complete，而它会
                # 抢跑迟到音频 —— 本文件上下已有三处注释承认这点（"late content
                # after premature turn_complete"、"turn_complete 后到达的迟到转录"、
                # "Gemini turn_complete 抢跑的迟到音频"）。把 on_audio_done 挂在
                # turn_complete 上等于把「音频还没放完就宣告放完」重新造一遍，正是
                # 这个 issue 本身。Gemini 这条路继续靠前端的 give-up 计时器兜底：
                # 漏发是可接受的降级，早发不是。
                if getattr(server_content, 'turn_complete', False):
                    # Gemini Live API 不返回 token 数，仅记录调用次数
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
                    if self._skip_until_next_response:
                        self._skip_until_next_response = False
                        logger.info("Gemini: skipped response (prime_context priming)")
                    elif self.on_response_done:
                        await self.on_response_done()
                        if not event_owner_is_current():
                            return

                # 检查是否被中断
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
                    # 被中断时也发送已累积的用户输入
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
