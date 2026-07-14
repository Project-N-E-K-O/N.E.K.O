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
    asyncio,
    logger,
    np,
    time,
)



class _AudioMixin:
    def _clear_uplink_resampler(self) -> None:
        """Drop the uplink resampler's pending FIR tail (soxr holds ~21ms of
        algorithmic delay at 16k→24k HQ).

        Called at every server-buffer clear/commit boundary so a finished
        turn's residual samples are not carried into — and prepended to —
        the next turn. Discard (not flush-and-send) is the right semantics:
        on ``input_audio_buffer.clear`` the server is throwing that audio
        away anyway, and on a MANUAL commit the trailing ~21ms is end-of-turn
        tail. Mirrors AudioProcessor's downsample-resampler ``.clear()`` on
        reset. No-op for every 16kHz-native provider (resampler is None).
        """
        if self._uplink_resampler is not None:
            self._uplink_resampler.clear()

    def _resample_uplink(self, pcm16_bytes: bytes) -> bytes:
        """Upsample 16kHz PCM16 mic/cache audio to the provider's uplink rate.

        No-op for every provider that accepts 16kHz (``_uplink_resampler``
        is None) — returns the bytes unchanged. Only OpenAI Realtime needs
        24kHz, in which case the persistent stream resampler converts each
        chunk while carrying FIR state across calls (no boundary clicks).

        Returns ``b''`` if the resampler is still buffering and produced no
        output for this chunk; callers should skip sending empty frames.
        """
        if self._uplink_resampler is None or not pcm16_bytes:
            return pcm16_bytes
        samples = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        out = self._uplink_resampler.resample_chunk(samples)
        if len(out) == 0:
            return b''
        return (out * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()

    async def process_audio_chunk_async(self, audio_chunk: bytes) -> bytes:
        """
        Asynchronously process audio chunk using RNNoise in a separate thread.
        This prevents blocking the main event loop during heavy calculation.
        """
        async with self._audio_processing_lock:
            processor = self._audio_processor
            if processor is None:
                return audio_chunk
            # Use run_in_executor to offload heavy processing
            # None = use default ThreadPoolExecutor
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                processor.process_chunk,
                audio_chunk,
            )

    async def set_audio_noise_reduction_enabled(self, enabled: bool) -> None:
        """Apply a live denoiser toggle after active processing quiesces."""
        async with self._audio_processing_lock:
            self._noise_reduction_enabled = enabled
            processor = self._audio_processor
            if processor is not None:
                processor.set_enabled(enabled)

    async def _close_audio_processor(self) -> None:
        """Quiesce executor processing before releasing RNNoise/soxr state."""
        async with self._audio_processing_lock:
            processor = self._audio_processor
            if processor is None:
                return
            try:
                processor.save_debug_audio()
            except Exception as exc:
                logger.error(f"Error saving debug audio: {exc}")
            try:
                processor.close()
            except Exception as exc:
                logger.error(f"Error closing audio processor: {exc}")
            finally:
                self._audio_processor = None

    async def _check_silence_timeout(self):
        """Periodically check whether the silence timeout has been exceeded; if so, trigger the timeout callback"""
        # 如果未启用静默超时（Qwen 或 Step），直接返回
        if not self._enable_silence_timeout:
            logger.debug(f"静默超时检测已禁用（API类型: {self._api_type}）")
            return

        try:
            while self.ws:
                # 检查websocket是否还有效（直接访问并捕获异常）
                try:
                    if not self.ws:
                        break
                except Exception:
                    break

                await asyncio.sleep(10)  # 每10秒检查一次

                if self._silence_timeout_triggered:
                    continue

                # 选择语音活动时间源：有 server VAD 用 _last_speech_time，否则用客户端 VAD
                if self._has_server_vad:
                    speech_time = self._last_speech_time
                else:
                    # 无 server VAD 时（free/gemini），用客户端能量/RNNoise 检测的时间戳
                    speech_time = self._client_vad_last_speech_time if self._client_vad_last_speech_time > 0 else None

                if speech_time is None:
                    # 还没有检测到任何语音，从现在开始计时
                    self._last_speech_time = time.time()
                    self._client_vad_last_speech_time = self._last_speech_time
                    continue

                elapsed = time.time() - speech_time
                if elapsed >= self._silence_timeout_seconds:
                    logger.warning(f"⏰ 检测到{self._silence_timeout_seconds}秒无语音输入，触发自动关闭")
                    self._silence_timeout_triggered = True
                    if self.on_silence_timeout:
                        await self.on_silence_timeout()
                    break
        except asyncio.CancelledError:
            logger.info("静默检测任务被取消")
        except Exception as e:
            logger.error(f"静默检测任务出错: {e}")

    def _on_silence_reset(self):
        """Called when the audio processor detects 4 seconds of silence and resets its cache. Marks a pending clear event."""
        self._silence_reset_pending = True

    def _should_clear_audio_buffer_on_silence(
        self, current_time: float, use_rnnoise_path: bool
    ) -> bool:
        """Whether the input_audio_buffer should be cleared on silence.

        With RNNoise and currently on the RNNoise path: RNNoise is authoritative (its internal 4s-silence callback sets _silence_reset_pending).
        Without RNNoise (or not on the RNNoise path): VAD + sustained local silence is authoritative.

        Criteria for sustained silence:
        - duration: no "loud" frame within the last _local_quiet_seconds seconds (default 2);
        - loud: raw PCM RMS > _client_vad_threshold (default 500, int16 range).
        I.e.: compute RMS on the raw input each frame and update _last_local_loud_time when
        above threshold; sustained silence only holds when
        (current_time - _last_local_loud_time) >= _local_quiet_seconds.

        When this returns True, the caller always sets _silence_reset_pending=False.
        """
        if use_rnnoise_path:
            return self._silence_reset_pending
        # core.py 预处理路径：RNNoise 在 process_audio_chunk_async 中运行，
        # 16kHz 结果送入 stream_audio → use_rnnoise_path=False，
        # 但 _silence_reset_pending 仍可能已被 AudioProcessor 回调置位。
        if self._silence_reset_pending:
            return True
        # 纯非 RNNoise 路径：VAD 静音 ≥ _silence_buffer_clear_seconds 且 连续本地静音 ≥ _local_quiet_seconds
        if self._has_server_vad:
            last_speech = self._last_speech_time
        else:
            last_speech = self._client_vad_last_speech_time if self._client_vad_last_speech_time > 0 else None
        if last_speech is None:
            return False
        local_quiet_elapsed = current_time - self._last_local_loud_time
        if local_quiet_elapsed < self._local_quiet_seconds:
            return False
        silence_elapsed = current_time - last_speech
        if silence_elapsed < self._silence_buffer_clear_seconds:
            return False
        if last_speech <= self._last_silence_clear_speech_time:
            return False
        self._last_silence_clear_speech_time = last_speech
        return True

    async def clear_audio_buffer(self):
        """Send an input_audio_buffer.clear event to clear the server-side buffer."""
        if self._is_gemini:
            logger.debug("Gemini mode: no WebSocket input_audio_buffer.clear event")
            return
        await self.send_event({"type": "input_audio_buffer.clear"})
        # The server is discarding this buffer; drop the uplink resampler's
        # held tail too so it isn't prepended to the next utterance.
        self._clear_uplink_resampler()
        logger.debug("📤 已发送 input_audio_buffer.clear 事件")
