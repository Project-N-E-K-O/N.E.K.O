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

import contextlib
import base64
import soxr
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from main_logic.audio_message_payload import AudioMessageManualModeUnconfirmed
from utils.asyncio_retirement import await_retirement

from ._shared import TurnDetectionMode

from ._shared import (
    asyncio,
    logger,
    np,
    time,
)


_AUDIO_TRANSCRIPT_GRACE_SECONDS = 0.75
_AUDIO_TRANSCRIPT_RETRY_DELAYS = (0.1, 0.2)
_AUDIO_MANUAL_ACK_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True, kw_only=True)
class _AudioMessageTranscript:
    on_transcript: Callable[..., Awaitable[bool | None]] | None
    transcript_ready: asyncio.Event = field(default_factory=asyncio.Event)
    transcript_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    transcript_publications: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    transcript_retry: asyncio.Task[bool] | None = None
    transcript: str = ""
    published: str = ""
    published_final: bool = False
    finalized: bool = False

    async def publish(self) -> None:
        # Keep the first write and retry registration together if the
        # receiver is cancelled while handling an already captured transcript.
        publication = asyncio.create_task(self._publish_and_schedule_retry())
        self.transcript_publications.add(publication)
        try:
            await await_retirement(publication)
        finally:
            self.transcript_publications.discard(publication)

    async def wait_for_started_publications(self) -> None:
        # An in-flight first write may register a retry after the grace expires.
        # Snapshot the work already started without waiting for new transcripts.
        for publication in tuple(self.transcript_publications):
            await asyncio.shield(publication)
        if self.transcript_retry is not None and not await asyncio.shield(self.transcript_retry):
            raise ConnectionError("audio transcript could not be stored")

    async def _publish_and_schedule_retry(self) -> None:
        if await self._write_transcript() is False and (
            self.transcript_retry is None or self.transcript_retry.done()
        ):
            self.transcript_retry = asyncio.create_task(self._retry_transcript())

    async def _write_transcript(self) -> bool | None:
        async with self.transcript_lock:
            # This callback owns the receipt captured at submission. A session
            # handoff fences live delivery, while that receipt may still need
            # the transcript that was already received.
            if self.on_transcript is None or not self.transcript.strip():
                return None
            transcript, final = self.transcript, self.finalized
            if transcript == self.published and final == self.published_final:
                return True
            written = await self.on_transcript(transcript, final=final)
            if written is True:
                self.published = transcript
                self.published_final = final
            return written

    async def _retry_transcript(self) -> bool:
        try:
            for delay in _AUDIO_TRANSCRIPT_RETRY_DELAYS:
                await asyncio.sleep(delay)
                if await self._write_transcript() is not False:
                    return True
            logger.warning("Audio message transcript could not be stored after retry")
        except Exception:
            logger.exception("Audio message transcript retry failed")
        return False


@dataclass(slots=True)
class _GeminiAudioMessage(_AudioMessageTranscript):
    outcome: asyncio.Future[bool]
    publish_partial: bool = False
    pending_tool_ids: set[str] = field(default_factory=set)
    used_tools: bool = False
    reply_after_tools: bool = False

    async def append(self, transcription) -> None:
        if self.finalized:
            return
        self.transcript += transcription.text or ""
        finished = getattr(transcription, "finished", None)
        if finished is True:
            self.finalized = True
        if self.finalized or self.publish_partial:
            await self.publish()
        if self.finalized:
            self.transcript_ready.set()


class _AudioMixin:
    def _observe_audio_message_event(self, event: dict) -> None:
        if event.get("type") == "session.updated":
            session = event.get("session")
            session = session if isinstance(session, dict) else {}
            audio = session.get("audio")
            audio = audio if isinstance(audio, dict) else {}
            audio_input = audio.get("input")
            audio_input = audio_input if isinstance(audio_input, dict) else {}
            confirmed = (
                "turn_detection" in session and session["turn_detection"] is None
            ) or ("turn_detection" in audio_input and audio_input["turn_detection"] is None)
            # DashScope's full session snapshot omits the null turn_detection
            # field after accepting manual mode. Do not accept an empty ACK
            # or one that explicitly reports an active detector.
            if (
                self.turn_detection_mode == TurnDetectionMode.MANUAL
                and self._api_type == "qwen"
                and "qwen" in self._model_lower
                and session.get("id")
                and session.get("input_audio_format") == "pcm16"
                and "turn_detection" not in session
                and "turn_detection" not in audio_input
            ):
                confirmed = True
            self._audio_message_manual_confirmed = confirmed
            waiter = getattr(self, "_audio_message_manual_ack", None)
            if waiter is not None and not waiter.done():
                waiter.set_result(confirmed)
        pending = getattr(self, "_audio_message_commit", None)
        if pending is None or pending.done():
            return
        if event.get("type") == "input_audio_buffer.committed":
            self._audio_message_input_item = event.get("item_id")
            self._audio_message_input_committed = True
            pending.set_result(None)
        elif event.get("type") == "error":
            error = event.get("error")
            commit_id = getattr(self, "_audio_message_commit_event_id", None)
            if commit_id and isinstance(error, dict) and error.get("event_id") == commit_id:
                pending.set_exception(ConnectionError("audio message rejected by provider"))

    async def submit_audio_message(
        self, pcm: bytes, request_id: str, *, is_current: Callable[[], bool],
        on_accepted=None, on_transcript=None,
    ) -> None:
        """Submit a complete 16kHz recording with one explicit response request.

        The caller owns a fresh MANUAL session and retires it on any uncertain
        failure. Full recordings bypass microphone VAD, silence clearing and
        the two-second live-input queue.
        """
        if self.turn_detection_mode != TurnDetectionMode.MANUAL or not is_current():
            raise RuntimeError("audio messages require an owned manual session")
        transcript = _AudioMessageTranscript(on_transcript=on_transcript)
        self._audio_message_transcript = transcript
        self._audio_message_input_item = None
        self._audio_message_input_committed = False
        self._audio_message_early_transcripts = {}
        self.note_user_turn_started()
        if self._is_gemini:
            from ._gemini_support import types

            if types is None:
                raise RuntimeError("Gemini Live SDK is unavailable")
            session = self._gemini_session
            audio_message = _GeminiAudioMessage(
                asyncio.get_running_loop().create_future(), on_transcript=on_transcript,
            )
            self._gemini_audio_message = audio_message
            try:
                if not is_current() or session is None:
                    raise ConnectionError("audio session superseded")
                await session.send_realtime_input(activity_start=types.ActivityStart())
                if not is_current():
                    raise ConnectionError("audio session superseded")
                await session.send_realtime_input(
                    audio={"data": pcm, "mime_type": "audio/pcm;rate=16000"}
                )
                if not is_current():
                    raise ConnectionError("audio session superseded")
                await session.send_realtime_input(activity_end=types.ActivityEnd())
                if not await asyncio.wait_for(audio_message.outcome, timeout=60):
                    raise ConnectionError("Gemini audio message response interrupted")
                if on_accepted is not None:
                    await on_accepted()
                if on_transcript is not None and not audio_message.transcript_ready.is_set():
                    try:
                        await asyncio.wait_for(
                            audio_message.transcript_ready.wait(), timeout=_AUDIO_TRANSCRIPT_GRACE_SECONDS,
                        )
                    except TimeoutError:
                        if is_current() and not audio_message.finalized:
                            # Keep captured text even when the final marker is late.
                            audio_message.publish_partial = True
                            await audio_message.publish()
                await audio_message.wait_for_started_publications()
            except BaseException:
                if getattr(self, "_gemini_audio_message", None) is audio_message:
                    self._gemini_audio_message = None
                if not audio_message.outcome.done():
                    audio_message.outcome.cancel()
                raise
            return

        if not getattr(self, "_audio_message_manual_confirmed", False):
            waiter = asyncio.get_running_loop().create_future()
            self._audio_message_manual_ack = waiter
            try:
                if not await asyncio.wait_for(waiter, timeout=_AUDIO_MANUAL_ACK_TIMEOUT_SECONDS):
                    raise AudioMessageManualModeUnconfirmed("provider did not confirm manual audio turns")
            except TimeoutError as exc:
                raise AudioMessageManualModeUnconfirmed("provider did not confirm manual audio turns") from exc
            finally:
                self._audio_message_manual_ack = None

        def encode_recording() -> tuple[dict, ...]:
            audio = pcm
            if self._uplink_sample_rate != 16_000:
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
                samples = soxr.resample(samples, 16_000, self._uplink_sample_rate)
                audio = samples.clip(-32768, 32767).astype("<i2").tobytes()
            # One second per append stays below the WebSocket frame cap even
            # at 24 kHz. All chunks share one commit and one response request.
            chunk_size = self._uplink_sample_rate * 2
            return tuple(
                {"type": "input_audio_buffer.append",
                 "audio": base64.b64encode(audio[offset:offset + chunk_size]).decode("ascii")}
                for offset in range(0, len(audio), chunk_size)
            )

        append_events = await asyncio.to_thread(encode_recording)
        if not is_current():
            raise ConnectionError("audio session superseded")
        committed = asyncio.get_running_loop().create_future()
        self._audio_message_commit = committed
        commit_id = "event_audio_commit_" + request_id[-36:]
        self._audio_message_commit_event_id = commit_id
        tool_batches: list[asyncio.Task] = []
        self._audio_message_tool_batches = tool_batches
        self._audio_message_tool_responses = set()
        arbiter = self._ensure_response_arbiter()

        async def send(event):
            if not is_current():
                raise ConnectionError("audio session superseded")
            if not await self.send_event(event):
                raise ConnectionError("audio message transport unavailable")
            if event["type"] == "input_audio_buffer.commit":
                await asyncio.wait_for(asyncio.shield(committed), timeout=5)
                if on_accepted is not None:
                    await on_accepted()
                item_id = self._audio_message_input_item
                early = self._audio_message_early_transcripts.pop(item_id, None)
                self._audio_message_early_transcripts.clear()
                if early is not None:
                    await self._deliver_input_transcript(early, item_id=item_id)

        ticket = None
        response_event = {"type": "response.create", "event_id": "event_audio_" + request_id[-36:]}
        # Qwen applies the output limit in session.update. Its response.create
        # has no per-response token override.
        if "qwen" not in self._model_lower:
            response_event["response"] = {"max_output_tokens": 4096}
        try:
            ticket = await arbiter.enqueue(
                source="audio_message",
                events_before_response=(
                    *append_events,
                    {"type": "input_audio_buffer.commit", "event_id": commit_id},
                ),
                response_event=response_event,
                event_sender=send,
                admission_check=is_current,
                response_done_timeout=60,
            )
            async with asyncio.timeout(70):
                await asyncio.shield(ticket.done)
                # Each audio-owned batch waits for its continuation terminal.
                # A continuation can itself enqueue another batch.
                for batch in tool_batches:
                    await asyncio.shield(batch)
            if on_transcript is not None and is_current() and not transcript.transcript_ready.is_set():
                # Input transcription can arrive after the final response or
                # tool continuation. Keep its receiver alive briefly before
                # a completed receipt lets the UI start another turn.
                try:
                    await asyncio.wait_for(transcript.transcript_ready.wait(), timeout=_AUDIO_TRANSCRIPT_GRACE_SECONDS)
                except TimeoutError:
                    pass
            await transcript.wait_for_started_publications()
        except BaseException:
            if ticket is not None:
                await arbiter.cancel_ticket(ticket)
            raise
        finally:
            if self._audio_message_commit is committed:
                self._audio_message_commit = None
                self._audio_message_commit_event_id = None
                self._audio_message_tool_batches = None
                self._audio_message_tool_responses = None
            if not committed.done():
                committed.cancel()
            elif not committed.cancelled():
                committed.exception()

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
                # The caller selected the 48 kHz RNNoise path before awaiting
                # this lock. Returning that original frame would make it look
                # like processed 16 kHz PCM after a concurrent close.
                return b""
            worker = asyncio.create_task(
                asyncio.to_thread(processor.process_chunk, audio_chunk)
            )
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A native RNNoise call keeps running after cancellation. Keep
                # the lifecycle lock until it exits so close/toggle cannot
                # destroy or mutate the processor underneath that call.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if worker.done() and not worker.cancelled():
                    with contextlib.suppress(Exception):
                        worker.result()
                raise

    async def set_audio_noise_reduction_enabled(self, enabled: bool) -> None:
        """Apply a live denoiser toggle after active processing quiesces."""
        async with self._audio_processing_lock:
            self._noise_reduction_enabled = enabled
            processor = self._audio_processor
            if processor is not None:
                try:
                    processor.set_enabled(enabled)
                except Exception as exc:
                    logger.error(f"Error toggling audio noise reduction: {exc}")

    async def _close_audio_processor(self, generation=None) -> None:
        """Quiesce executor processing before releasing RNNoise/soxr state.

        ``generation`` names the connection whose processor this is. Waiting
        for the lock is an await like any other, so a replacement can attach
        during it — and because connect() only builds a processor when the
        field is empty, the replacement adopts this very one. Releasing it
        afterwards would leave the live connection resampling through nothing.
        Callers outside a connection teardown pass nothing and are unaffected.
        """
        async with self._audio_processing_lock:
            if generation is not None and not self._still_owns_connection(generation):
                logger.info(
                    "Audio processor close: a replacement connection adopted it; leaving it alone"
                )
                return
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
        # Return directly if silent timeout is disabled (Qwen or Step).
        if not self._enable_silence_timeout:
            logger.debug(f"静默超时检测已禁用（API类型: {self._api_type}）")
            return

        try:
            while self.ws:
                # Check WebSocket validity by direct access and exception handling.
                try:
                    if not self.ws:
                        break
                except Exception:
                    break

                await asyncio.sleep(10)  # Check every ten seconds.

                if self._silence_timeout_triggered:
                    continue

                # Select voice-activity clock: _last_speech_time with server VAD, otherwise client VAD.
                if self._has_server_vad:
                    speech_time = self._last_speech_time
                else:
                    # Without server VAD (free/gemini), use client energy/RNNoise detection timestamps.
                    speech_time = self._client_vad_last_speech_time if self._client_vad_last_speech_time > 0 else None

                if speech_time is None:
                    # No speech detected yet: start timing now.
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
        # core.py preprocessing: RNNoise runs in process_audio_chunk_async,
        # sending 16kHz output to stream_audio → use_rnnoise_path=False,
        # but AudioProcessor callbacks may still have set _silence_reset_pending.
        if self._silence_reset_pending:
            return True
        # Pure non-RNNoise path: VAD silence ≥ _silence_buffer_clear_seconds and continuous local silence ≥ _local_quiet_seconds.
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
        self._input_route_identity = None
        self._input_route_identity_captured = False
        self._reset_input_route_identity_stream()
        if self._is_gemini:
            logger.debug("Gemini mode: no WebSocket input_audio_buffer.clear event")
            return
        await self.send_event({"type": "input_audio_buffer.clear"})
        # The server is discarding this buffer; drop the uplink resampler's
        # held tail too so it isn't prepended to the next utterance.
        self._clear_uplink_resampler()
        logger.debug("📤 已发送 input_audio_buffer.clear 事件")
