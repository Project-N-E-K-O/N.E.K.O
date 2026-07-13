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
        await self.send_event({"type": "input_audio_buffer.commit"})
        # The committed buffer excludes the ~21ms tail soxr still holds in the
        # uplink resampler; drop it so it isn't prepended to the next turn.
        self._clear_uplink_resampler()
        await self.send_event({"type": "response.create"})

    async def _gemini_send_user_turn(self, text: str) -> None:
        """Inject ``text`` as a Gemini user turn and trigger a response via
        ``send_client_content(turn_complete=True)``.

        This is Gemini Live's idiomatic equivalent of OpenAI-Realtime's
        ``conversation.item.create(role=user) + response.create``. Shared by
        ``_create_response_gemini`` (callers choose error policy) and
        ``inject_text_and_request_response`` (proactive — must propagate
        errors so the caller can re-queue). Errors propagate here; callers
        that need to swallow wrap it.
        """
        from google.genai import types as genai_types

        content = genai_types.Content(
            parts=[genai_types.Part(text=text)],
            role="user",
        )
        await self._gemini_session.send_client_content(
            turns=[content],
            turn_complete=True,
        )

    async def _create_response_gemini(self, instructions: str, *, raise_on_error: bool = False) -> None:
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
            await self._gemini_send_user_turn(instructions)
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
    ) -> None:
        """Set Gemini skip state only for a successfully-started skipped turn."""
        if not skipped:
            await self._create_response_gemini(instructions, raise_on_error=raise_on_error)
            return

        previous_skip = self._skip_until_next_response
        self._skip_until_next_response = True
        try:
            await self._create_response_gemini(instructions, raise_on_error=raise_on_error)
        except Exception:
            self._skip_until_next_response = previous_skip
            raise

    async def _send_tool_result_gemini(self, results: List[ToolResult]) -> None:
        """Gemini Live SDK — batch all tool results into one
        ``send_tool_response`` call (matches the SDK's expectation when
        the model issues multiple parallel function calls)."""
        if not self._gemini_session or not results:
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
            await self._gemini_session.send_tool_response(function_responses=function_responses)
        except Exception as e:
            logger.error("Gemini send_tool_response failed: %s", e)

    async def _close_gemini(self) -> None:
        """Close Gemini Live API session."""
        if self._gemini_context_manager:
            try:
                await self._gemini_context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error closing Gemini session: {e}")
            finally:
                self._gemini_session = None
                self._gemini_context_manager = None
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
        if not self._gemini_session:
            logger.error("Gemini session not established")
            return

        try:
            while not self._fatal_error_occurred:
                try:
                    # 接收响应流
                    turn = self._gemini_session.receive()
                    async for response in turn:
                        await self._process_gemini_response(response)
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
                        if self.on_connection_error:
                            await self.on_connection_error(error_msg)
                        break
        except Exception as e:
            logger.error(f"Gemini message handler error: {e}")

    async def _process_gemini_response(self, response) -> None:
        """Process a single Gemini response event."""
        try:
            # 处理工具调用 —— 将 function_calls 中每一个调用都派给
            # ``on_tool_call``，结果通过 ``send_tool_response`` 一次性回写
            # （Gemini Live 期望批量回应，而不是逐个）。
            if hasattr(response, 'tool_call') and response.tool_call:
                fcs = list(getattr(response.tool_call, 'function_calls', []) or [])
                if fcs:
                    if self.on_tool_call is None:
                        logger.warning(
                            "Gemini tool_call received but no on_tool_call handler — replying with error"
                        )
                        results = [
                            ToolResult(
                                call_id=getattr(fc, 'id', '') or '',
                                name=getattr(fc, 'name', '') or '',
                                output={"error": "no on_tool_call handler"},
                                is_error=True, error_message="no on_tool_call handler",
                            )
                            for fc in fcs
                        ]
                    else:
                        results = []
                        for fc in fcs:
                            args = dict(getattr(fc, 'args', None) or {})
                            call = ToolCall(
                                name=getattr(fc, 'name', '') or '',
                                arguments=args,
                                call_id=getattr(fc, 'id', '') or '',
                                raw_arguments=json.dumps(args, ensure_ascii=False),
                            )
                            results.append(await self._execute_tool_call(call))
                    # Fire-and-forget — let the message loop continue. The
                    # SDK's ``send_tool_response`` is the only way to feed
                    # results back to a Live session.
                    self._fire_task(self._send_tool_result_gemini(results))
                # Tool call cancellation (if present in this SDK build) is
                # surfaced as ``response.tool_call_cancellation`` — currently
                # not actioned because we run tools fire-and-forget; if a
                # cancellation arrives mid-flight the result we eventually
                # send back will be ignored by the model. Acceptable for
                # now; revisit if cancel-rate becomes a problem.

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
                    self._is_responding = True
                    if _is_new_turn and _can_clear_interrupted:
                        # Gemini has no response.created event; clear stale interrupt state only
                        # after SDK transcription or a quiet gap proves this is not a canceled tail.
                        self._interrupted = False
                        # 在AI开始响应前，发送累积的用户输入
                        if self._gemini_user_transcript and self.on_input_transcript:
                            await self.on_input_transcript(self._gemini_user_transcript)
                            self._gemini_user_transcript = ""  # 清空累积
                        self._gemini_user_transcript_after_interrupt = False
                        self._is_first_text_chunk = True  # 重置第一个 chunk 标记
                        self._gemini_current_transcript = ""  # 清空累积
                        if not self._skip_until_next_response and not self._interrupted and self.on_new_message:
                            await self.on_new_message()
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

                # 检查是否 turn 完成（用 getattr 防止 SDK 无该字段时抛错）
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
                    if self._skip_until_next_response:
                        self._skip_until_next_response = False
                        logger.info("Gemini: skipped response (prime_context priming)")
                    elif self.on_response_done:
                        await self.on_response_done()

                # 检查是否被中断
                if hasattr(server_content, 'interrupted') and server_content.interrupted:
                    if self._skip_until_next_response:
                        self._skip_until_next_response = False
                        logger.info("Gemini: skipped response interrupted, reset skip flag")
                    self._interrupted = True
                    self._is_responding = False
                    # 被中断时也发送已累积的用户输入
                    if self._gemini_user_transcript:
                        self._gemini_user_transcript_after_interrupt = True
                        if self.on_input_transcript:
                            await self.on_input_transcript(self._gemini_user_transcript)
                        self._gemini_user_transcript = ""
                    logger.info("Gemini response was interrupted by user")

        except Exception as e:
            logger.error(f"Error processing Gemini response: {e}")
