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
    Dict,
    IMAGE_IDLE_RATE_MULTIPLIER,
    List,
    NATIVE_IMAGE_MIN_INTERVAL,
    OMNI_WS_FRAME_LIMIT_BYTES,
    Optional,
    ToolCall,
    ToolResult,
    TurnDetectionMode,
    VISION_ANALYSIS_MAX_TOKENS,
    _IMAGE_ANALYSIS_PENDING_DESCRIPTION,
    asyncio,
    base64,
    calculate_text_similarity,
    get_stepfun_tts_default_voice,
    json,
    logger,
    np,
    parse_arguments_json,
    time,
    websockets,
)



class _TransportMixin:
    _WS_FRAME_LIMIT = OMNI_WS_FRAME_LIMIT_BYTES  # safe threshold below 256KB server cap

    async def connect(self, instructions: str, native_audio=True) -> None:
        """Establish WebSocket connection with the Realtime API."""
        # Validate turn_detection_mode BEFORE any side effect (websockets.connect,
        # silence-check task, or Gemini SDK init). Applies uniformly to all providers.
        if self.turn_detection_mode not in (TurnDetectionMode.MANUAL, TurnDetectionMode.SERVER_VAD):
            raise ValueError(f"Invalid turn detection mode: {self.turn_detection_mode}")
        self._response_arbiter.reset_connection_state()

        # [ISSUE4c] Reset the tool-call flood window on every (re)connect. The
        # same OmniRealtimeClient instance is reused across sessions, so stale
        # timestamps from a previous connection must not carry over and make the
        # new session's first tool calls look like a burst. Cleared before the
        # provider branch so it covers both Gemini and the WS providers.
        self._recent_tool_call_times = []

        # ``close()`` releases RNNoise/soxr state. The client object is reused
        # across sessions, so recreate that session-owned processor on demand.
        if self._audio_processor is None:
            self._audio_processor = self._create_audio_processor()

        # Gemini uses google-genai SDK, not raw WebSocket
        if self._is_gemini:
            await self._connect_gemini(instructions, native_audio)
            return

        # 确保开始新连接时状态完全重置
        self._silence_reset_pending = False
        self._last_silence_clear_speech_time = 0.0
        self._last_local_loud_time = 0.0
        self._client_vad_active = False
        self._client_vad_last_speech_time = 0.0
        self._speech_detect_start = 0.0
        self._rnnoise_vad_active = False
        self._user_recent_activity_time = 0.0
        self._ai_recent_activity_time = 0.0
        if self._audio_processor is not None:
            self._audio_processor.reset()
        # Flush uplink resampler FIR history so a previous session's tail
        # samples don't bleed into the new connection's first frames.
        self._clear_uplink_resampler()

        # WebSocket-based APIs (GLM, Qwen, GPT, Step, Free)
        url = f"{self.base_url}?model={self.model}" if self._model_lower != "free-model" else self.base_url
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        # close_timeout=0.5 缩短 close handshake 的等待上限：默认 10s 会把
        # end_session 协程挂住数百毫秒~数秒（Qwen 回 CLOSE 帧偶尔很慢），
        # 超时后 websockets 内部会 transport.abort() 强制关闭。
        self.ws = await websockets.connect(url, additional_headers=headers, close_timeout=0.5)
        # Clear fatal flag so send_event/update_session work on this new
        # connection (flag may be leftover from a previous failed session
        # when the same OmniRealtimeClient instance is reused).
        self._fatal_error_occurred = False

        # 启动静默检测任务（只在启用时）
        self._last_speech_time = time.time()
        self._silence_timeout_triggered = False
        if self._silence_check_task:
            self._silence_check_task.cancel()
        # 只在启用静默超时时启动检测任务
        if self._enable_silence_timeout:
            self._silence_check_task = asyncio.create_task(self._check_silence_timeout())
        else:
            reason = "livestream模式" if self._livestream_mode else f"API类型: {self._api_type}"
            logger.info(f"静默超时检测已禁用（{reason}），不会自动关闭会话")

        # Set up default session configuration
        is_manual = self.turn_detection_mode == TurnDetectionMode.MANUAL
        # MANUAL mode: every per-provider session.update below sends
        # ``turn_detection: null``, so the provider will NOT emit
        # speech_started / speech_stopped events. _has_server_vad was
        # initialised in __init__ from provider/model heuristics
        # (defaults to True for Qwen/GLM/GPT/Step/lanlan.tech-free), but
        # those events won't arrive in MANUAL — so downstream branches in
        # stream_audio() and _check_silence_timeout() must take the
        # client-VAD path, same as Gemini / lanlan.app-free. Override the
        # flag here uniformly across all providers; the Gemini connect
        # path is unaffected because __init__ already set this to False
        # for ``_is_gemini`` clients.
        if is_manual:
            self._has_server_vad = False
        self._modalities = ["text", "audio"] if native_audio else ["text"]

        if 'glm' in self._model_lower:
            # GLM: server_vad payload in SERVER_VAD; turn_detection=null in MANUAL.
            # Best-effort — provider may reject; if so we degrade to local-suppression-only.
            glm_session = {
                "instructions": instructions,
                "modalities": self._modalities ,
                "voice": self.voice if self.voice else "tongtong",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm",
                "turn_detection": None if is_manual else {
                    "type": "server_vad",
                },
                "input_audio_noise_reduction": {
                    "type": "far_field",
                },
                "beta_fields":{
                    "chat_mode": "video_passive",
                    "auto_search": True,
                },
                "temperature": 1.0
            }
            # GLM Realtime: tools only honoured in audio mode per docs.
            # Use the flat (OpenAI-Realtime-style) schema GLM expects.
            if self.has_tools() and 'audio' in self._modalities:
                glm_session["tools"] = self._tools_for_openai_realtime()
            await self.update_session(glm_session)
        elif "qwen" in self._model_lower:
            qwen_session: Dict[str, Any] = {
                "instructions": instructions,
                "modalities": self._modalities ,
                "voice": self.voice if self.voice else "Momo",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gummy-realtime-v1"
                },
                "turn_detection": None if is_manual else {
                    # TODO: 未来需要cover更多型号
                    "type": "semantic_vad" if "3.5" in self._model_lower else "server_vad",
                    "threshold": 0.55,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 650
                },
                "repetition_penalty": 1.2,
                "temperature": 0.7,
                # "enable_search": True,
                # "search_options": {'enable_source': True}
            }
            # Qwen-Omni-Realtime 自 2026 起支持 tools（嵌套 function 形，
            # 同 StepFun）。重要约束：tools 与 enable_search 互斥——
            # 我们注册了自定义工具时强制 enable_search=False，避免
            # session.update 被服务端拒绝。文档参见 Aliyun client-events
            # 章节 "工具调用（tools）和联网搜索（enable_search）不兼容"。
            if self.has_tools():
                qwen_session["tools"] = self._tools_for_qwen()
                qwen_session["enable_search"] = False
            await self.update_session(qwen_session)
        elif "gpt" in self._model_lower:
            gpt_session = {
                "type": "realtime",
                "model": self.model,
                "instructions": instructions,
                "output_modalities": ['audio'] if 'audio' in self._modalities else ['text'],
                "audio": {
                    "input": {
                        # OpenAI Realtime PCM 输入只支持 24kHz；显式声明以匹配
                        # 我们 _resample_uplink 上采后的实际采样率。复用
                        # _uplink_sample_rate（此分支恒为 24000）作单一数据源，
                        # 避免声明与实际两处来源漂移。
                        "format": {"type": "audio/pcm", "rate": self._uplink_sample_rate},
                        "transcription": {"model": "gpt-4o-mini-transcribe"},
                        "turn_detection": None if is_manual else {
                            "type": "semantic_vad",
                            "eagerness": "auto",
                            "create_response": True,
                            "interrupt_response": True
                        },
                    },
                    "output": {
                        "voice": self.voice if self.voice else "marin",
                        "speed": 1.0
                    }
                }
            }
            if self.has_tools():
                gpt_session["tools"] = self._tools_for_openai_realtime()
                gpt_session["tool_choice"] = "auto"
            await self.update_session(gpt_session)
        elif "step" in self._model_lower:
            default_voice = get_stepfun_tts_default_voice('step')
            step_session = {
                "instructions": instructions,
                "modalities": ['text', 'audio'], # Step API只支持这一个模式
                "voice": self.voice if self.voice else default_voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": None if is_manual else {
                    "type": "server_vad"
                },
            }
            step_tools: List[Dict[str, Any]] = []
            if self.has_tools():
                step_tools.extend(self._tools_for_step())
            step_session["tools"] = step_tools
            await self.update_session(step_session)
        elif "free" in self._model_lower:
            # NOTE: lanlan.tech (China free) backs onto StepFun and
            # supports the StepFun custom-function protocol — the
            # server-side tool stripping the user mentioned will be
            # lifted, after which our tools propagate naturally.
            # lanlan.app (international free) backs onto Vertex AI
            # Live; that path is currently TODO (no client→server
            # tools propagation confirmed). Tools below match the
            # StepFun shape and become a no-op on lanlan.app until
            # the proxy supports them.
            #
            # MANUAL mode: both proxies receive ``turn_detection: null``
            # via the StepFun-shape websocket session config. lanlan.tech
            # (StepFun proxy) honours it natively; lanlan.app (Vertex
            # Gemini proxy) translates the disabled-VAD intent on the
            # server side, since the proxy already maps StepFun-shape
            # client events to Vertex Live (see _has_server_vad gate
            # at __init__ — lanlan.app+free is already treated as
            # client-side VAD only).
            default_voice = get_stepfun_tts_default_voice('free')
            free_session = {
                "instructions": instructions,
                "modalities": ['text', 'audio'],
                "voice": self.voice if self.voice else default_voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": None if is_manual else {
                    "type": "server_vad"
                },
            }
            # 海外免费（lanlan.app，Gemini 代理）建 session 时一次性指定
            # language_code，与 TTS server 路对偶；lanlan.tech（StepFun）不发，
            # 沿用其自动识别 / voice_label 语义。
            if 'lanlan.app' in (self.base_url or ''):
                from utils.language_utils import get_tts_language_code
                free_session["language_code"] = get_tts_language_code()
            free_tools: List[Dict[str, Any]] = []
            if self.has_tools():
                free_tools.extend(self._tools_for_step())
            free_session["tools"] = free_tools
            await self.update_session(free_session)
        elif "grok" in self._model_lower:
            # xAI Grok Voice：OpenAI Realtime 1.0 风格的扁平 schema。
            # 内置 voice 见 GET /v1/tts/voices（eve/ara/leo/rex/sal），默认 eve。
            # tools 走 OpenAI 兼容的 function 协议（response.function_call_arguments.done）。
            grok_session = {
                "instructions": instructions,
                "modalities": self._modalities,
                "voice": self.voice if self.voice else "eve",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": None if is_manual else {
                    "type": "server_vad"
                },
            }
            if self.has_tools():
                grok_session["tools"] = self._tools_for_openai_realtime()
                grok_session["tool_choice"] = "auto"
            await self.update_session(grok_session)
        else:
            raise ValueError(f"Invalid model: {self.model}")
        self.instructions = instructions

    @staticmethod
    def _try_shrink_image_payload(event: dict, payload: str) -> Optional[str]:
        """Re-compress an oversized image payload at lower JPEG quality.

        Looks for a base64 image blob in the event (``image``,
        ``video_frame``, or ``image_url`` fields), decodes it, re-encodes
        at progressively lower quality, and returns a new JSON payload that
        fits under ``_WS_FRAME_LIMIT``.  Returns *None* if the frame
        cannot be shrunk (non-image event, or still too big at minimum
        quality).
        """
        from io import BytesIO
        from PIL import Image as PILImage

        limit = _TransportMixin._WS_FRAME_LIMIT

        # Locate the base64 blob and a setter to write it back
        b64_data: Optional[str] = None
        prefix = ""

        etype = event.get("type", "")
        if "image" in etype and "image" in event:
            # input_image_buffer.append  →  event["image"]
            b64_data = event.get("image")
        elif "video_frame" in etype and "video_frame" in event:
            # input_audio_buffer.append_video_frame  →  event["video_frame"]
            b64_data = event.get("video_frame")
        elif etype == "conversation.item.create":
            # GPT path: content[0].image_url = "data:image/jpeg;base64,<b64>"
            try:
                url = event["item"]["content"][0]["image_url"]
                if isinstance(url, str) and url.startswith("data:image/"):
                    prefix, b64_data = url.split(",", 1)
                    prefix += ","
            except (KeyError, IndexError, TypeError, ValueError):
                pass

        if not b64_data:
            logger.warning(
                "⚠️ 丢弃超大帧 type=%s size=%d bytes (非图片，无法压缩)",
                etype, len(payload),
            )
            return None

        try:
            raw = base64.b64decode(b64_data)
            img = PILImage.open(BytesIO(raw))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")

            for quality in (50, 35, 20):
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                new_b64 = base64.b64encode(buf.getvalue()).decode()

                # Write back into the event dict (mutates in place)
                if "image" in etype and "image" in event:
                    event["image"] = new_b64
                elif "video_frame" in etype and "video_frame" in event:
                    event["video_frame"] = new_b64
                elif prefix:
                    event["item"]["content"][0]["image_url"] = prefix + new_b64

                new_payload = json.dumps(event)
                if len(new_payload) <= limit:
                    logger.info(
                        "🗜️ 图片帧重压缩成功 q=%d: %d → %d bytes",
                        quality, len(payload), len(new_payload),
                    )
                    return new_payload

            logger.warning(
                "⚠️ 丢弃超大图片帧 type=%s (q=20 仍 %d bytes > %d 上限)",
                etype, len(new_payload), limit,
            )
            return None
        except Exception as e:
            logger.warning("⚠️ 图片重压缩失败 type=%s: %s — 丢弃帧", etype, e)
            return None

    async def send_event(self, event) -> None:
        # 检查是否已发生致命错误，直接跳过发送
        if self._fatal_error_occurred:
            return

        # Gemini 不使用 WebSocket 风格的事件发送
        # 而是使用 session.send_client_content() 或 session.send_realtime_input()
        if self._is_gemini:
            # Gemini 的事件通过专用方法处理，这里直接返回
            # 对于 session.update / conversation.item.create 等事件，Gemini 不支持
            logger.debug(f"Gemini mode: skipping WebSocket event {event.get('type', 'unknown')}")
            return

        # Backpressure: 检查是否处于节流状态
        if self._is_throttled:
            if time.time() < self._throttle_until:
                # 仍在节流期，丢弃音频帧以减轻服务器压力
                if event.get("type") == "input_audio_buffer.append":
                    return  # 丢弃音频帧
            else:
                # 节流期结束，恢复正常发送
                self._is_throttled = False
                logger.info("🔄 Backpressure throttle ended, resuming sends")

        # 检查websocket是否有效
        if not self.ws:
            return

        # Use setdefault so callers that explicitly stamp an event_id
        # (e.g. proactive inject paths matching server-side
        # ``error.event_id`` echoes for rejection callbacks) keep theirs.
        # Otherwise fall back to the legacy timestamp-based id.
        event.setdefault('event_id', "event_" + str(int(time.time() * 1000)))
        async with self._send_semaphore:  # 限制并发发送数量
            try:
                if not self.ws:
                    return
                payload = json.dumps(event)
                # Guard: Qwen/GLM/Step servers enforce 256KB max frame; for
                # oversized image payloads, try to re-compress the JPEG at
                # lower quality before dropping. PIL decode + JPEG re-encode
                # is CPU-heavy (50-150ms on a 4K screenshot), so off-load to
                # a thread to keep the event loop responsive.
                if len(payload) > OMNI_WS_FRAME_LIMIT_BYTES:
                    payload = await asyncio.to_thread(
                        self._try_shrink_image_payload, event, payload
                    )
                    if payload is None:
                        return
                await self.ws.send(payload)
            except Exception as e:
                error_msg = str(e)
                # ── Fatal WebSocket errors ────────────────────────────
                # 1009 (message too big) / 1006 (abnormal close) /
                # 1011 (internal error) / Response timeout
                # → mark fatal, fire error callback, schedule close,
                #   and *re-raise* so callers (connect, update_session)
                #   see the failure instead of assuming success.
                is_frame_error = '1009' in error_msg or '1006' in error_msg
                is_server_error = 'Response timeout' in error_msg or '1011' in error_msg
                if is_frame_error or is_server_error:
                    if not self._fatal_error_occurred:
                        self._fatal_error_occurred = True
                        self.ws = None
                        code = "WS_FRAME_ERROR" if is_frame_error else "RESPONSE_TIMEOUT"
                        logger.error("💥 WebSocket 致命错误 (%s)，停止发送: %s", code, error_msg)
                        if self.on_connection_error:
                            self._fire_task(self.on_connection_error(json.dumps({"code": code})))
                        self._fire_task(self.close())
                    raise
                if '1000' not in error_msg:
                    logger.warning(f"⚠️ 发送 {event.get('type', '未知')} 事件失败: {error_msg}")

                raise

    async def update_session(self, config: Dict[str, Any]) -> None:
        """Update session configuration."""
        # Mirror the chat-completion chokepoint: catch any unrendered
        # {placeholder} before the system instruction (nested at provider-
        # specific paths inside `config`) is shipped over the wire. See
        # utils/llm_prompt_leak_check.py for rationale.
        try:
            from utils import llm_prompt_leak_check
            llm_prompt_leak_check.check_dict_strings_for_leaks(
                config, context="OmniRealtimeClient.update_session"
            )
        except AssertionError:
            raise
        except Exception:
            pass
        event = {
            "type": "session.update",
            "session": config
        }
        await self.send_event(event)

    async def stream_audio(self, audio_chunk: bytes) -> None:
        """Stream raw audio data to the API.

        Supports two input modes:
        - 48kHz from PC: Apply RNNoise then downsample to 16kHz
        - 16kHz from mobile: Pass through directly (no RNNoise)
        """
        # 检查是否已发生致命错误，如果是则直接返回
        if self._fatal_error_occurred:
            return

        current_time = time.time()
        # 本地音量判定：用原始输入做 RMS，避免 VAD 延迟时误清 buffer
        raw_samples = np.frombuffer(audio_chunk, dtype=np.int16)
        if len(raw_samples) > 0:
            local_rms = np.sqrt(np.mean(raw_samples.astype(np.float32) ** 2))
            if local_rms > self._client_vad_threshold:
                self._last_local_loud_time = current_time

        # Detect input sample rate based on chunk size
        # 48kHz: 480 samples (10ms) = 960 bytes
        # 16kHz: 512 samples (~32ms) = 1024 bytes
        num_samples = len(audio_chunk) // 2  # 16-bit = 2 bytes per sample
        is_48khz = (num_samples == 480)  # RNNoise frame size


        use_rnnoise_path = is_48khz and self._audio_processor is not None
        # Apply RNNoise noise reduction only for 48kHz input (PC)
        if use_rnnoise_path:
            # Use async wrapper to avoid blocking main loop
            audio_chunk = await self.process_audio_chunk_async(audio_chunk)

            # Skip if RNNoise is buffering (returns empty)
            if len(audio_chunk) == 0:
                return

        # Unified VAD update (priority: server VAD > RNNoise > RMS)
        # Grace period check: always runs regardless of VAD source
        if self._client_vad_active and current_time - self._client_vad_last_speech_time > self._client_vad_grace_period:
            self._client_vad_active = False

        # Client-side speech detection (only when no server VAD — server events handle it in handle_messages)
        # use_rnnoise_path is true only for 48kHz input when AudioProcessor exists;
        # for 16kHz/mobile input RNNoise doesn't run, so fall back to RMS.
        audio_processor = self._audio_processor
        use_rnnoise_path = use_rnnoise_path and audio_processor is not None
        _rnnoise_vad_live = (
            use_rnnoise_path
            and audio_processor.noise_reduce_enabled
            and audio_processor._denoiser is not None
        )
        self._rnnoise_vad_active = _rnnoise_vad_live
        if not self._has_server_vad:
            if _rnnoise_vad_live:
                # Priority 2: RNNoise speech probability with sustained threshold
                if audio_processor.speech_probability > 0.4:
                    # B: 单帧 RNNoise 判定为语音就立即打点，独立于 sustain。
                    # _client_vad_active 仍需 500ms sustain，_user_recent_activity
                    # 只看"最近是否发声"，fudge guard 用它兜住首 500ms 和停顿缝隙。
                    self._user_recent_activity_time = current_time
                    if self._speech_detect_start == 0.0:
                        self._speech_detect_start = current_time
                    elif current_time - self._speech_detect_start >= self._speech_sustain_threshold:
                        self._client_vad_last_speech_time = current_time
                        self._client_vad_active = True
                else:
                    self._speech_detect_start = 0.0
            else:
                # Priority 3: RMS energy fallback
                samples = np.frombuffer(audio_chunk, dtype=np.int16)
                if len(samples) > 0:
                    rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
                    if rms > self._client_vad_threshold:
                        self._client_vad_last_speech_time = current_time
                        self._client_vad_active = True
                        # RMS 噪音率高，但若 RNNoise 不可用（16kHz/移动端），
                        # RMS 是唯一信号，也喂给 B 兜底。阈值已经是 500（较高），
                        # 一般环境噪音达不到。
                        self._user_recent_activity_time = current_time

        # Suppress mic → server during proactive nudge injection (VAD above still updates)
        if self._proactive_injecting:
            return

        # 静音清 buffer：有 RNNoise 以 RNNoise 为准，否则 VAD + 连续本地静音（见 _should_clear_audio_buffer_on_silence）
        if self._should_clear_audio_buffer_on_silence(current_time, use_rnnoise_path):
            self._silence_reset_pending = False
            await self.clear_audio_buffer()

        # Gemini uses different API (16kHz, no uplink resample needed)
        if self._is_gemini:
            await self._stream_audio_gemini(audio_chunk)
            return

        # By this point audio_chunk is always 16kHz (RNNoise-downsampled,
        # mobile-native, or hot-swap-cache replay). Upsample to the provider
        # uplink rate as the very last step (24kHz for OpenAI; no-op others).
        audio_chunk = self._resample_uplink(audio_chunk)
        if not audio_chunk:
            return  # resampler still buffering — nothing to send this frame

        audio_b64 = base64.b64encode(audio_chunk).decode()

        append_event = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        await self.send_event(append_event)

    async def _analyze_image_with_vision_model(self, image_b64: str) -> str:
        """Use VISION_MODEL to analyze image and return description."""
        try:
            # 使用统一的视觉分析函数
            from utils.screenshot_utils import analyze_image_with_vision_model

            description = await analyze_image_with_vision_model(
                image_b64=image_b64,
                max_completion_tokens=VISION_ANALYSIS_MAX_TOKENS
            )

            if description:
                self._image_description = f"[实时屏幕截图或相机画面]: {description}"
                logger.info("✅ Image analysis complete.")
                self._image_recognized_this_turn = True
                return description
            else:
                logger.warning("VISION_MODEL not configured or analysis failed")
                self._image_description = _IMAGE_ANALYSIS_PENDING_DESCRIPTION
                self._image_recognized_this_turn = False
                self._latest_image_b64 = None
                self._proactive_image_consumed = True
                return ""

        except Exception as e:
            logger.error(f"Error analyzing image with vision model: {e}")
            self._image_recognized_this_turn = False
            self._image_description = _IMAGE_ANALYSIS_PENDING_DESCRIPTION
            self._latest_image_b64 = None
            self._proactive_image_consumed = True
            # 检测内容审查错误并发送中文提示到前端（不关闭session）
            error_str = str(e)
            if 'censorship' in error_str:
                if self.on_status_message:
                    await self.on_status_message(json.dumps({"code": "IMAGE_BLOCKED"}))
            return ""
        finally:
            self._image_being_analyzed = False

    async def stream_image(self, image_b64: str, *, bypass_rate_limit: bool = False) -> None:
        """Stream raw image data to the API.

        ``bypass_rate_limit=True`` skips the native-vision frame-rate throttle
        for a deliberate single cue image (e.g. a proactive callback's
        screenshot) so it isn't silently dropped just because a high-frequency
        screen/camera frame was streamed within NATIVE_IMAGE_MIN_INTERVAL
        (Codex P2). It's one intentional image, not a stream, so it won't flood.
        """
        # Cache latest frame for proactive injection
        self._latest_image_b64 = image_b64
        self._proactive_image_consumed = False

        try:
            # Models without native vision (step, free on lanlan.tech) — first frame triggers VISION_MODEL analysis
            if '实时屏幕截图或相机画面正在分析中' in self._image_description and not self._supports_native_image:
                # 非原生视觉后端只需要本轮第一帧做分析；后续高频帧直接丢弃，避免并发刷爆 VISION_MODEL。
                async with self._image_lock:
                    if self._image_recognized_this_turn or self._image_being_analyzed:
                        return
                    self._image_being_analyzed = True
                await self._analyze_image_with_vision_model(image_b64)
                return

            # Rate limiting for native image input (with VAD-based throttling).
            # A deliberate cue image (bypass_rate_limit) skips the interval check
            # so it's never silently dropped, but still stamps the timestamp.
            if self._supports_native_image:
                current_time = time.time()
                if not bypass_rate_limit:
                    elapsed = current_time - self._last_native_image_time
                    min_interval = NATIVE_IMAGE_MIN_INTERVAL
                    if not self._client_vad_active:
                        min_interval *= IMAGE_IDLE_RATE_MULTIPLIER
                    if elapsed < min_interval:
                        # Skip this image frame due to rate limiting
                        return
                # Stamp even on the bypass path: a frame WAS sent to the server,
                # so it must count toward the throttle window — this keeps
                # back-to-back bypassed cue images from flooding native vision.
                self._last_native_image_time = current_time

            # Gemini uses SDK, not WebSocket events (_audio_in_buffer is not set for Gemini)
            if self._is_gemini:
                if self._gemini_session:
                    try:
                        image_bytes = base64.b64decode(image_b64)
                        await self._gemini_session.send_realtime_input(
                            media={"data": image_bytes, "mime_type": "image/jpeg"}
                        )
                    except Exception as e:
                        logger.error(f"Error sending image to Gemini: {e}")
                        if "closed" in str(e).lower():
                            self._fatal_error_occurred = True
                return

            if self._is_free_proxy:
                append_event = {
                    "type": "input_image_buffer.append" ,
                    "image": image_b64
                }
                await self.send_event(append_event)
                return

            if self._audio_in_buffer:
                if "qwen" in self._model_lower:
                    append_event = {
                        "type": "input_image_buffer.append" ,
                        "image": image_b64
                    }
                elif "glm" in self._model_lower:
                    append_event = {
                        "type": "input_audio_buffer.append_video_frame",
                        "video_frame": image_b64
                    }
                elif "gpt" in self._model_lower:
                    append_event = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/jpeg;base64," + image_b64
                                }
                            ]
                        }
                    }
                else:
                    # Model does not support video streaming, use VISION_MODEL to analyze
                    # Only recognize one image per conversation turn
                    async with self._image_lock:
                        if not self._image_recognized_this_turn:
                            if not self._image_being_analyzed:
                                self._image_being_analyzed = True
                                text_event = {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "input_text",
                                                "text": self._image_description
                                            }
                                        ]
                                    }
                                }
                                logger.info("Sending image description before recognition.")
                                await self.send_event(text_event)
                                await self._analyze_image_with_vision_model(image_b64)
                        elif not self._image_sent_this_turn:
                            self._image_sent_this_turn = True
                            text_event = {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "input_text",
                                                "text": self._image_description
                                            }
                                        ]
                                    }
                                }
                            logger.info("Sending image description after recognition.")
                            await self.send_event(text_event)
                    return

                await self.send_event(append_event)
        except Exception as e:
            logger.error(f"Error streaming image: {e}")
            raise e

    async def _check_repetition(self, response: str) -> bool:
        """
        Check whether the reply is highly repetitive of recent replies.
        Returns True and triggers the callback if 3 consecutive turns are highly repetitive.
        """

        # 与最近的回复比较相似度
        high_similarity_count = 0
        for recent in self._recent_responses:
            similarity = calculate_text_similarity(response, recent)
            if similarity >= self._repetition_threshold:
                high_similarity_count += 1

        # 添加到最近回复列表
        self._recent_responses.append(response)
        if len(self._recent_responses) > self._max_recent_responses:
            self._recent_responses.pop(0)

        # 如果与最近2轮都高度重复（即第3轮重复），触发检测
        if high_similarity_count >= 2:
            logger.warning(f"OmniRealtimeClient: 检测到连续{high_similarity_count + 1}轮高重复度对话")

            # 清空重复检测缓存
            self._recent_responses.clear()

            # 触发回调
            if self.on_repetition_detected:
                await self.on_repetition_detected()

            return True

        return False

    async def handle_interruption(self):
        """Handle user interruption of the current response."""
        if not self._is_responding:
            return

        logger.info("Handling interruption")

        # Mark as interrupted to suppress any remaining output until next response
        self._interrupted = True

        # 1. Cancel the current response
        if self._current_response_id:
            await self.cancel_response()

        self._is_responding = False
        # Keep the cancelled response identity until its terminal event arrives.
        # Clearing it here makes the stale-event filter drop that response.done,
        # leaving the arbiter busy until a later response happens to complete.
        self._current_item_id = None
        # 清空转录buffer和重置标志，防止打断后的错位
        self._output_transcript_buffer = ""
        self._is_first_transcript_chunk = True

    async def handle_messages(self) -> None:
        # Gemini uses different message handling
        if self._is_gemini:
            await self._handle_messages_gemini()
            return

        try:
            if not self.ws:
                logger.error("WebSocket connection is not established")
                return

            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")

                # if event_type not in ["response.audio.delta", "response.audio_transcript.delta",  "response.output_audio.delta", "response.output_audio_transcript.delta"]:
                #     # print(f"Received event: {event}")
                #     print(f"Received event: {event_type}")
                # else:
                #     print(f"Event type: {event_type}")
                if event_type == "error":
                    error_msg = str(event.get('error', ''))
                    logger.error(f"API Error: {error_msg}")

                    # Route server rejections of a proactive inject's
                    # ``response.create`` / ``conversation.item.create`` back to
                    # the caller so it can re-enqueue the optimistically-pruned
                    # cb (see _route_inject_rejection). ``error`` events
                    # normally echo the offending client event_id at
                    # ``error.event_id``; some providers put it top-level or
                    # omit it entirely — the helper handles all three.
                    err_obj = event.get('error') if isinstance(event.get('error'), dict) else {}
                    err_event_id = err_obj.get('event_id') or event.get('event_id')
                    self._route_inject_rejection(err_event_id, error_msg)
                    self._response_arbiter.notify_error(err_event_id, error_msg)

                    # 检测503过载错误，触发backpressure节流
                    if '503' in error_msg or 'overloaded' in error_msg.lower():
                        self._is_throttled = True
                        self._throttle_until = time.time() + self._throttle_duration
                        self._server_busy_count += 1
                        logger.warning(f"⚡ 503 detected (count={self._server_busy_count}), throttling for {self._throttle_duration}s")
                        # 前2次静默节流，第3次起通知前端
                        if self._server_busy_count >= 3 and self.on_status_message:
                            await self.on_status_message(json.dumps({"code": "SERVER_BUSY_THROTTLE"}))
                        continue

                    error_msg_lower = error_msg.lower()

                    # Idle timeout — Qwen 约 25s 无操作断连
                    if 'too long without operation' in error_msg_lower or 'idle' in error_msg_lower:
                        logger.warning("⏰ Idle timeout from API: %s", error_msg)
                        if self.on_connection_error:
                            await self.on_connection_error(json.dumps({"code": "API_IDLE_TIMEOUT", "details": {"msg": error_msg}}))
                        await self.close()
                        continue

                    if ('欠费' in error_msg or 'standing' in error_msg_lower or 'time limit' in error_msg_lower or
                        'policy violation' in error_msg_lower or '1008' in error_msg_lower or
                        '429' in error_msg_lower or 'quota' in error_msg_lower or 'too many' in error_msg_lower):
                        if self.on_connection_error:
                            await self.on_connection_error(error_msg)
                        await self.close()
                    continue

                # A cancelled response can still emit buffered events after a
                # replacement response has become current.  Providers that
                # include response identity let us reject those late events
                # without changing the legacy behaviour of id-less proxies.
                if event_type != "response.created":
                    event_response_id = event.get("response_id")
                    if event_type == "response.done" and not event_response_id:
                        response = event.get("response")
                        if isinstance(response, dict):
                            event_response_id = response.get("id")
                    if (
                        event_response_id
                        and event_response_id != self._current_response_id
                    ):
                        logger.info(
                            "Dropping stale response event type=%s response_id=%s current_response_id=%s",
                            event_type,
                            event_response_id,
                            self._current_response_id,
                        )
                        continue
                # ── Tool calling events ────────────────────────────
                # Three providers, three flavours of the same idea:
                #   - OpenAI Realtime (gpt): the canonical event is the
                #     output_item.done with item.type=="function_call";
                #     response.done also carries it inside output[].
                #     Arguments are streamed as
                #     response.function_call_arguments.delta and finalized
                #     in response.function_call_arguments.done.
                #   - StepFun (step / lanlan.tech free): same pattern,
                #     function_call_arguments.delta + .done with call_id.
                #   - GLM (glm): only function_call_arguments.done is
                #     emitted (no delta), and there is no call_id field —
                #     we synthesize one from response_id+output_index.
                # All three return results via conversation.item.create
                # of type function_call_output + response.create, handled
                # by ``_send_tool_result_openai_realtime``.
                if event_type == "response.function_call_arguments.delta":
                    call_id = event.get("call_id") or ""
                    if call_id:
                        slot = self._inflight_tool_args.setdefault(call_id, {
                            "name": event.get("name") or "",
                            "arguments": "",
                        })
                        if event.get("name"):
                            slot["name"] = event["name"]
                        delta = event.get("delta") or ""
                        if delta:
                            slot["arguments"] += delta
                elif event_type == "response.function_call_arguments.done":
                    name = event.get("name") or ""
                    raw_args = event.get("arguments") or ""
                    call_id = event.get("call_id") or ""
                    if not call_id:
                        # GLM path: synthesize a stable call_id so we have
                        # something to thread through the registry.
                        rid = event.get("response_id") or ""
                        idx = event.get("output_index", 0)
                        call_id = f"glm_{rid}_{idx}" if rid else f"glm_call_{int(time.time()*1000)}"
                    # Prefer accumulated delta args if delta path was used.
                    accumulated = self._inflight_tool_args.pop(call_id, None)
                    if accumulated and accumulated.get("arguments"):
                        raw_args = accumulated["arguments"]
                        if not name:
                            name = accumulated.get("name") or name
                    if not name:
                        logger.warning(
                            "function_call_arguments.done with no name (call_id=%s) — skipping",
                            call_id,
                        )
                    elif self.on_tool_call is None:
                        logger.warning(
                            "function_call '%s' but no on_tool_call handler bound — replying with error",
                            name,
                        )
                        result = ToolResult(
                            call_id=call_id, name=name,
                            output={"error": "no on_tool_call handler"},
                            is_error=True, error_message="no on_tool_call handler",
                        )
                        self._fire_task(self._send_tool_result_openai_realtime(result))
                    else:
                        # Execute and reply asynchronously — don't block the
                        # message loop. handle_messages stays responsive to
                        # other events while the tool runs.
                        async def _run_tool(_name=name, _args=raw_args, _cid=call_id):
                            call = ToolCall(
                                name=_name,
                                arguments=parse_arguments_json(_args),
                                call_id=_cid,
                                raw_arguments=_args,
                            )
                            result = await self._execute_tool_call(call)
                            await self._send_tool_result_openai_realtime(result)
                        self._fire_task(_run_tool())
                elif event_type == "conversation.item.created":
                    self._response_arbiter.notify_item_created(event)
                elif event_type == "response.done":
                    self._response_arbiter.notify_response_terminal(event)
                    self._response_done_total += 1
                    self._last_response_done_time = time.time()
                    # Lifecycle cleanup of proactive inject rejection handlers
                    # (see _sweep_inject_rejection_handlers): any pending
                    # rejection has already fired by now, so the remaining
                    # entries belong to injects that succeeded — reap them.
                    self._sweep_inject_rejection_handlers()
                    # 解析实时 API 返回的 token 用量
                    try:
                        resp_data = event.get("response", {})
                        _rt_usage = resp_data.get("usage")
                        if _rt_usage:
                            from utils.token_tracker import TokenTracker
                            TokenTracker.get_instance().record(
                                model=resp_data.get("model", self.model or "realtime"),
                                prompt_tokens=_rt_usage.get("input_tokens", 0),
                                completion_tokens=_rt_usage.get("output_tokens", 0),
                                total_tokens=_rt_usage.get("total_tokens", 0),
                                call_type="conversation_realtime",
                                source="main_logic/omni_realtime_client",
                            )
                    except Exception:
                        pass
                    self._is_responding = False
                    self._current_response_id = None
                    self._current_item_id = None
                    self._skip_until_next_response = False
                    self._interrupted = False  # 确保中断标志在响应结束时清除，防止阻塞下一轮 text.delta
                    # 响应完成，检测重复度
                    if self._current_response_transcript:
                        self._last_response_transcript = self._current_response_transcript
                        print(f"OmniRealtimeClient: response.done - 当前转录: '{self._current_response_transcript[:50]}...' | audio_deltas={self._audio_delta_count}")
                        await self._check_repetition(self._current_response_transcript)
                        self._current_response_transcript = ""
                    else:
                        self._last_response_transcript = ""
                        print(f"OmniRealtimeClient: response.done - 没有转录文本 | audio_deltas={self._audio_delta_count}")
                    # [有声无字兜底] 部分 provider（如 lanlan.app Gemini 语音代理）只发
                    # response.audio_transcript.delta、从不发 response.audio_transcript.done，
                    # 输出转录全靠下面 streaming 分支（_print_input_transcript=True）实时送出。
                    # 但带工具调用的一轮里，工具调用那一轮的 response.done 会把
                    # _print_input_transcript 置 False（见下方），紧随其后的真回复转录便走
                    # buffer 分支累积进 _output_transcript_buffer，没有 transcript.done 来 flush，
                    # 就在这里被直接清空 → 前端有声无字。这里在清空前补一次 flush：只要本轮真
                    # 出过声（audio_delta_count>0）且 buffer 仍有残留就补发。streaming 分支每次都
                    # 会清空 buffer，故正常轮此处为 no-op，不会重复发送。
                    if (
                        self._output_transcript_buffer
                        and self.on_output_transcript
                        and self._audio_delta_count > 0
                    ):
                        # 「有声无字」是反复出现的问题（见上方 ISSUE4b），留一条 debug
                        # 日志方便下次诊断时确认是这条兜底生效、还是 streaming/transcript.done
                        # 路径生效。audio_delta_count 此处尚未清零，记录的是本轮真实值。
                        logger.debug(
                            "response.done 兜底 flush 输出转录: buffer_len=%d audio_deltas=%d is_first=%s",
                            len(self._output_transcript_buffer),
                            self._audio_delta_count,
                            self._is_first_transcript_chunk,
                        )
                        await self.on_output_transcript(
                            self._output_transcript_buffer, self._is_first_transcript_chunk
                        )
                        self._is_first_transcript_chunk = False
                    self._audio_delta_count = 0
                    # 确保 buffer 被清空
                    self._output_transcript_buffer = ""
                    self._print_input_transcript = False
                    self._image_recognized_this_turn = False
                    self._image_sent_this_turn = False
                    if self.on_response_done:
                        await self.on_response_done()
                    # No-server-VAD providers (Gemini-proxy: lanlan.app+free /
                    # livestream) never emit input_audio_buffer.speech_stopped,
                    # so handle_messages' on_new_message path on speech_stopped
                    # never fires and current_speech_id never rotates between
                    # turns. Without rotation, TTS upstream silently drops text
                    # after the first tts.response.done closes the initial sid.
                    # Hook here at response.done (Gemini's turn_complete, the
                    # only reliable end-of-AI-turn signal in those proxies) and
                    # call the lightweight rotate-only path — full
                    # handle_new_message would clip trailing TTS audio and
                    # mis-fire USER_INPUT (no user input actually happened).
                    if not self._has_server_vad and self.on_sid_rotate:
                        await self.on_sid_rotate()
                elif event_type == "response.created":
                    self._response_arbiter.notify_response_created(event)
                    self._response_created_total += 1
                    self._last_response_created_time = time.time()
                    # A response started — our proactive inject's response.create
                    # was either accepted (this IS its response) or a different
                    # response is now active; either way close the no-id
                    # content-fallback window so a later unrelated no-id
                    # conflict can't fire a lingering (accepted) inject handler.
                    self._proactive_inject_awaiting_outcome = False
                    self._current_response_id = event.get("response", {}).get("id")
                    self._is_responding = True
                    self._interrupted = False  # Clear interruption flag on new response
                    self._is_first_text_chunk = self._is_first_transcript_chunk = True
                    # 清空转录 buffer，防止累积旧内容
                    self._output_transcript_buffer = ""
                    self._current_response_transcript = ""  # 重置当前回复转录
                elif event_type == "response.output_item.added":
                    self._current_item_id = event.get("item", {}).get("id")
                elif event_type == "input_audio_buffer.committed":
                    self._input_audio_committed_total += 1
                    self._last_input_audio_committed_time = time.time()
                    logger.info("input_audio_buffer.committed observed (total=%d)", self._input_audio_committed_total)
                # Handle interruptions
                elif event_type == "input_audio_buffer.speech_started":
                    self._speech_started_total += 1
                    logger.info("Speech detected")
                    self._audio_in_buffer = True
                    # 重置静默计时器
                    self._last_speech_time = time.time()
                    # Priority 1: server VAD → sync to unified _client_vad_active
                    self._client_vad_active = True
                    self._client_vad_last_speech_time = self._last_speech_time
                    # B: server-VAD 也喂给 _user_recent_activity，保持各 VAD 源对称。
                    # 但 fudge 注入期间 server 会对我们自己 append 的 fudge 音频
                    # 回 speech_started —— 这不是真用户活动，若打点 prompt_ephemeral
                    # 循环会检测到 _user_recent_activity_time > _inject_start 而自 abort，
                    # 并在之后 8s 内阻塞下一次 fudge（入口 guard 一起被污染）。
                    if not self._proactive_injecting:
                        self._user_recent_activity_time = self._last_speech_time
                    if self._is_responding:
                        logger.info("Handling interruption")
                        await self.handle_interruption()
                elif event_type == "input_audio_buffer.speech_stopped":
                    self._speech_stopped_total += 1
                    logger.info("Speech ended")
                    if self.on_new_message:
                        await self.on_new_message()
                    self._audio_in_buffer = False
                    # Update timestamp so grace period starts from speech end
                    _now = time.time()
                    self._client_vad_last_speech_time = _now
                    # 同 speech_started：fudge 自己的音频结束时 server 也会 emit
                    # speech_stopped，不能当成真用户活动打点。
                    if not self._proactive_injecting:
                        self._user_recent_activity_time = _now
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    self._print_input_transcript = True
                    transcript = event.get("transcript", "")
                    if self.on_input_transcript:
                        await self.on_input_transcript(transcript)
                elif event_type in ["response.audio_transcript.done", "response.output_audio_transcript.done"]:
                    self._print_input_transcript = False
                    # [ISSUE4b] Voice-without-text fix. Audio deltas and transcript
                    # deltas are gated by _skip_until_next_response/_interrupted at
                    # delta time. But this transcript.done re-checks those flags at
                    # *done* time — if a flag flipped True between audio playing and
                    # done (session-transition / proactive-inject race), the audio
                    # was already spoken yet the transcript got dropped → 前端有声无字.
                    # If audio already went out this response (_audio_delta_count>0),
                    # always forward the matching transcript regardless of a late
                    # flag flip; only suppress when nothing was spoken (interrupted
                    # before any audio).
                    _audio_already_spoken = self._audio_delta_count > 0
                    if (
                        self._output_transcript_buffer and self.on_output_transcript
                        and (
                            (not self._skip_until_next_response and not self._interrupted)
                            or _audio_already_spoken
                        )
                    ):
                        await self.on_output_transcript(self._output_transcript_buffer, self._is_first_transcript_chunk)
                        self._is_first_transcript_chunk = False
                    self._output_transcript_buffer = ""

                if not self._skip_until_next_response and not self._interrupted:
                    if event_type in ["response.text.delta", "response.output_text.delta"]:
                        if self.on_text_delta:
                            if "glm" not in self._model_lower:
                                self._ai_recent_activity_time = time.time()
                                await self.on_text_delta(event["delta"], self._is_first_text_chunk)
                                self._is_first_text_chunk = False
                    elif event_type in ["response.audio.delta", "response.output_audio.delta"]:
                        self._audio_delta_count += 1
                        self._audio_delta_total += 1
                        self._last_audio_delta_time = time.time()
                        if self._audio_delta_count == 1:
                            logger.info(f"🔊 首个 audio.delta 已收到 (type={event_type}, bytes={len(event.get('delta',''))})")
                        if self.on_audio_delta:
                            audio_bytes = base64.b64decode(event["delta"])
                            self._ai_recent_activity_time = time.time()
                            await self.on_audio_delta(audio_bytes)
                    elif event_type in ["response.audio_transcript.done", "response.output_audio_transcript.done"]:
                        if self.on_output_transcript and self._is_first_transcript_chunk:
                            transcript = event.get("transcript", "")
                            if transcript:
                                await self.on_output_transcript(transcript, True)
                                self._is_first_transcript_chunk = False
                    elif event_type in ["response.audio_transcript.delta", "response.output_audio_transcript.delta"]:
                        if self.on_output_transcript:
                            delta = event.get("delta", "")
                            # 累积当前回复的转录文本用于重复度检测
                            self._current_response_transcript += delta
                            if not self._print_input_transcript:
                                self._output_transcript_buffer += delta
                            else:
                                if self._output_transcript_buffer:
                                    # logger.info(f"{self._output_transcript_buffer} is_first_chunk: True")
                                    await self.on_output_transcript(self._output_transcript_buffer, self._is_first_transcript_chunk)
                                    self._is_first_transcript_chunk = False
                                    self._output_transcript_buffer = ""
                                await self.on_output_transcript(delta, self._is_first_transcript_chunk)
                                self._is_first_transcript_chunk = False

                    elif event_type in self.extra_event_handlers:
                        await self.extra_event_handlers[event_type](event)
                else:
                    # 调试日志：text.delta 被 _interrupted/_skip 标志拦截（每个 response 仅记录一次）
                    if event_type in ["response.text.delta", "response.output_text.delta"]:
                        if self._suppressed_delta_logged_resp_id != self._current_response_id:
                            self._suppressed_delta_logged_resp_id = self._current_response_id
                            logger.warning(
                                "⚠️ text.delta suppressed: _skip=%s, _interrupted=%s, resp_id=%s",
                                self._skip_until_next_response, self._interrupted, self._current_response_id
                            )

            await self._close_failed_transport("realtime message stream ended")
        except websockets.exceptions.ConnectionClosedOK:
            await self._close_failed_transport("realtime connection closed")
            logger.info("Connection closed as expected")
        except websockets.exceptions.ConnectionClosedError as e:
            error_msg = str(e)
            await self._close_failed_transport(error_msg)
            logger.error(f"Connection closed with error: {error_msg}")
            if self.on_connection_error:
                await self.on_connection_error(error_msg)
        except asyncio.TimeoutError:
            await self._close_failed_transport("realtime connection timeout")
            if self.on_connection_error:
                await self.on_connection_error(json.dumps({"code": "CONNECTION_TIMEOUT"}))
        except Exception as e:
            await self._close_failed_transport(
                f"realtime message handling failed: {type(e).__name__}"
            )
            logger.error(f"Error in message handling: {str(e)}")
            raise

    async def _close_failed_transport(self, reason: str) -> None:
        """Fail response tickets and atomically detach the failed socket."""

        response_arbiter = getattr(self, "_response_arbiter", None)
        if response_arbiter is not None:
            response_arbiter.notify_connection_lost(reason)
        await self._abort_failed_transport(reason)

    async def _abort_failed_transport(self, reason: str) -> None:
        """Atomically detach and physically close a failed raw WebSocket."""

        self._fatal_error_occurred = True
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception as exc:
                logger.debug(
                    "failed transport close also failed (%s): %s",
                    reason,
                    type(exc).__name__,
                )

    async def close(self) -> None:
        """Close the WebSocket connection."""
        response_arbiter = getattr(self, "_response_arbiter", None)
        if response_arbiter is not None:
            response_arbiter.notify_connection_lost("realtime client closed")

        # 取消静默检测任务
        if self._silence_check_task:
            self._silence_check_task.cancel()
            try:
                await self._silence_check_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error cancelling silence check task: {e}")
            finally:
                self._silence_check_task = None

        # 重置静默超时相关状态
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

        # Wait for any executor-owned chunk to finish before releasing the
        # session's RNNoise native state and soxr streaming buffers.
        await self._close_audio_processor()

        # Gemini uses different cleanup
        if self._is_gemini:
            await self._close_gemini()
            return

        if self.ws:
            try:
                # 连接时已设 close_timeout=0.5s：远端超时未回 CLOSE 帧时，
                # websockets 内部会自行 abort transport 强制关闭，
                # 保证 end_session 快速返回、主事件循环心跳不受影响。
                await self.ws.close()
            except Exception as e:
                logger.error(f"Error closing websocket: {e}")
            finally:
                self.ws = None  # 清空引用，防止后续误用
                logger.info("WebSocket connection closed")
        else:
            logger.warning("WebSocket connection is already closed or None")
