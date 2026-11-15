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

"""Aliyun CosyVoice (hosted) TTS worker."""

import threading
import time

from utils.config_manager import get_config_manager
from utils.dashscope_region import (
    DASHSCOPE_GLOBAL_LOCK,
    configure_dashscope_sdk_urls,
    prefer_dashscope_websocket_ipv4,
)

from .._infra import (
    AudioDoneEmitter,
    TTS_SHUTDOWN_SENTINEL,
    TTS_SOFT_FLUSH_SENTINEL,
    _enqueue_error,
)
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


def _get_enrolled_model(voice_meta):
    if not voice_meta:
        return None
    return voice_meta.get('design_model') or voice_meta.get('clone_model')


def cosyvoice_vc_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    TTS multiprocess worker function for Aliyun CosyVoice TTS
    
    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key
        voice_id: voice ID
    """
    import dashscope
    from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer, AudioFormat
    from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS
    # _get_voice_meta lives in package __init__, shared with get_tts_worker and patchable through
    # tts_client._get_voice_meta. Import lazily to avoid circular imports when __init__
    # imports worker modules.
    from main_logic.tts_client import _get_voice_meta

    # Read registration model/region URL from voice metadata, falling back to global config if missing.
    _voice_meta = _get_voice_meta(voice_id)
    _enrolled_model = _get_enrolled_model(_voice_meta)
    _voice_provider = _voice_meta.get('provider') if _voice_meta else None

    # dashscope.api_key and dashscope.base_*_api_url are module globals; /voice_preview
    # (characters_router.py) and cloning (utils/voice_clone.py) also mutate them in-process.
    # Setting once at worker startup makes later _create_synthesizer reconnects inherit someone else's
    # latest region/key, producing wrong-region 401s without changed voices when domestic/international
    # usage mixes. Resolve region URLs at startup and capture in closures; each
    # _create_synthesizer restores module globals.
    try:
        _tts_api_config = get_config_manager().get_model_api_config('tts_custom')
        _dashscope_base_url = (_voice_meta or {}).get('dashscope_base_url') or _tts_api_config.get('base_url', '')
    except Exception as e:
        logger.warning("DashScope TTS 地域 URL 读取失败，回退到默认地域: %s", e, exc_info=True)
        _dashscope_base_url = ""

    def _apply_dashscope_region():
        """Called before every SpeechSynthesizer rebuild (must be inside DASHSCOPE_GLOBAL_LOCK),
        ensuring the module-global is the worker's own region/key.
        """
        dashscope.api_key = audio_api_key
        try:
            configure_dashscope_sdk_urls(dashscope, _dashscope_base_url, websocket_path="inference")
        except Exception as e:
            logger.warning("DashScope TTS 地域 URL 配置失败，已重置为默认地域: %s", e, exc_info=True)
            try:
                configure_dashscope_sdk_urls(dashscope, "", websocket_path="inference")
            except Exception as reset_error:
                logger.error("DashScope TTS 默认地域重置失败: %s", reset_error, exc_info=True)
                raise

    # Do not eagerly write globals here: no SDK call reads them between startup and first
    # _create_synthesizer. Reconnects apply _apply_dashscope_region under
    # DASHSCOPE_GLOBAL_LOCK. Another unlocked write only races concurrent
    # /voice_preview /clone_voice over the same globals, reintroducing
    # the cross-credential routing race fixed by Codex P1 #3258691457.
    # (Codex P1 #3258856950)。

    # CosyVoice needs no preconnection; send ready immediately.
    logger.info("CosyVoice TTS 已就绪，发送就绪信号")
    response_queue.put(("__ready__", True))
    
    current_speech_id = None

    class Callback(ResultCallback):
        def __init__(self, response_queue, audio_done):
            self.response_queue = response_queue
            self.audio_done = audio_done
            self.connection_lost = False
            self._muted = False
            # on_complete means audio really finished only after the main loop sent FINISH for this round.
            # Reconnects also close old synthesizers and trigger on_complete before the round ends;
            # sending audio_done then would be premature.
            self.finish_requested_speech_id = None
            # Latest synthesizer generation, stamped during _SynthCallback construction.
            # SDK on_complete carries no synthesizer identity, and this callback shares state across rounds
            # and reconnects. After two quick switches, old completion sees the new round's
            # _active_sid/FINISH, falsely marks ongoing speech complete and clears its aggregation buffer.
            # Generation identifies stale callbacks.
            self.current_generation = None
            # Generation already completed. After soft flush, the main loop uses it to determine
            # old-stream drainage before closing it to continue / directly sending audio_done.
            self.completed_generation = None
            # Current deliverable speech_id, explicitly set at worker round boundaries.
            # Never read current_speech_id dynamically in on_data; old tail packets could get new-stream labels.
            self.accepted_speech_id = None
            # CosyVoice often starts with tiny OGG header pages (~200B), temporarily undecodable by frontends,
            # swallowing the perceived first word. Aggregate the first packet once per speech_id before delivery.
            self._active_sid = None
            self._bootstrap_buffer = bytearray()
            self._bootstrap_sent = False
            self._bootstrap_min_bytes = 1024
            # Subsequent aggregation: OGG OPUS pages are often hundreds of bytes; frequent small packets
            # trigger many frontend WASM decode calls, risking audio-buffer underruns
            # during busy Live2D rendering. Aggregate >=4KB before delivery,
            # reducing frontend calls and increasing decoded segment duration.
            self._agg_buffer = bytearray()
            self._agg_min_bytes = 4096
            # SDK receiving threads (on_data/on_complete) and worker loops (old-stream retirement,
            # boundary resets) both mutate this object. Generation stamps block only callbacks entering
            # **after retirement**; callbacks already past the stamp check may resume and
            # flush/clear buffers and FINISH now belonging to new streams. Lock every shared-state
            # mutation: retirement awaits in-flight callbacks, which recheck generation on entry.
            # Reentrant: on_complete finally calls reset_bootstrap_state.
            self._lock = threading.RLock()

        def reset_bootstrap_state(self):
            with self._lock:
                self._active_sid = None
                self._bootstrap_buffer.clear()
                self._bootstrap_sent = False
                self._agg_buffer.clear()
                self.finish_requested_speech_id = None

        def on_open(self):
            with self._lock:
                self.connection_lost = False
                self._muted = False
            elapsed = time.time() - self.construct_start_time if hasattr(self, 'construct_start_time') else -1
            logger.debug(f"TTS 连接已建立 (构造到open耗时: {elapsed:.2f}s)")
            
        def on_complete(self, generation=None):
            with self._lock:
                self._on_complete_locked(generation)

        def _on_complete_locked(self, generation):
            # Late completion from retired synthesizers (closed by rapid round switches or in-round reconnects)
            # describes old streams, while callback state already belongs to the current round.
            # Return completely: no flush (others' data), no signal (falsely ending current speech),
            # no reset (clearing current aggregation and FINISH,
            # preventing real round-ending detection).
            if generation is not None and generation != self.current_generation:
                logger.debug("CosyVoice 忽略过期 synthesizer 的 on_complete (gen=%s, 当前 gen=%s)",
                             generation, self.current_generation)
                return
            # Short sentences may finish before initial aggregation thresholds; force-flush on completion to avoid entirely silent sentences.
            # If muted by interruption/round switch, skip delivery so old tails cannot enter the new response_queue.
            try:
                sid = self._active_sid
                if sid and not self._muted:
                    if self._bootstrap_buffer:
                        self.response_queue.put(("__audio__", sid, bytes(self._bootstrap_buffer)))
                    if self._agg_buffer:
                        self.response_queue.put(("__audio__", sid, bytes(self._agg_buffer)))
                    # FINISH sent this round; early returns guarantee this callback belongs to the current
                    # synthesizer, and all rebuild paths clear FINISH.
                    if sid == self.finish_requested_speech_id:
                        # Tail packets queued; this round's audio stream closes here.
                        self.audio_done.emit(sid)
            finally:
                self.reset_bootstrap_state()
                # Publish generation-drained only after resetting shared state: the main loop immediately
                # retires old streams and starts continuations; earlier publication lets this reset hit the new stream,
                # clearing its newly buffered audio and round-end FINISH. Tails already entered the queue,
                # so subsequent main-loop audio_done or next-segment audio
                # still follows them.
                self.completed_generation = generation

        def on_error(self, message: str, generation=None):
            # Generation checks and marking disconnected share one critical section: yielding after checking
            # and resuming after retirement writes connection_lost into the new stream.
            with self._lock:
                self._on_error_locked(message, generation)

        def _on_error_locked(self, message: str, generation):
            # Errors from old synthesizers (drained after soft flush / replaced on rebuild)
            # describe others' connections: neither disconnect current generations nor report current-round failures.
            if generation is not None and generation != self.current_generation:
                logger.debug("CosyVoice 忽略过期 synthesizer 的 on_error (gen=%s, 当前 gen=%s)",
                             generation, self.current_generation)
                return
            if "request timeout after 23 seconds" in message:
                self.connection_lost = True
                logger.debug("CosyVoice SDK 内部 WebSocket 空闲超时，标记连接已断开")
            elif "request timeout" in message:
                self.connection_lost = True
                logger.warning(f"CosyVoice 请求超时，标记连接已断开: {message}")
                self.response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
            else:
                _enqueue_error(self.response_queue, message)
            
        def on_close(self, generation=None):
            # Only current-generation closure means disconnected. After soft flush, servers close old
            # connections after draining tails while new synthesizers may already speak;
            # disconnecting them skips FINISH and discards synthesizers on next cleanup, losing final sentences.
            with self._lock:
                if generation is not None and generation != self.current_generation:
                    return
                self.connection_lost = True

        def on_event(self, message):
            pass
            
        def on_data(self, data: bytes, generation=None) -> None:
            with self._lock:
                self._on_data_locked(data, generation)

        def _on_data_locked(self, data: bytes, generation) -> None:
            # Late pages from retired synthesizers (drain timeout after soft flush / replacement on rebuild)
            # mix into the same speech_id's new-stream aggregation buffer,
            # interleaving two OGG streams into corrupt audio.
            if generation is not None and generation != self.current_generation:
                return
            sid = self.accepted_speech_id
            if not sid or self._muted:
                # Discard while switching rounds or unready, avoiding reordered/mixed packets.
                return

            # Reset initial and subsequent aggregation on speech_id changes, preventing old data leaking into new rounds.
            if sid != self._active_sid:
                self._active_sid = sid
                self._bootstrap_buffer.clear()
                self._bootstrap_sent = False
                self._agg_buffer.clear()

            if not self._bootstrap_sent:
                self._bootstrap_buffer.extend(data)
                if len(self._bootstrap_buffer) < self._bootstrap_min_bytes:
                    return
                self.response_queue.put(("__audio__", sid, bytes(self._bootstrap_buffer)))
                self._bootstrap_buffer.clear()
                self._bootstrap_sent = True
                return

            self._agg_buffer.extend(data)
            if len(self._agg_buffer) >= self._agg_min_bytes:
                self.response_queue.put(("__audio__", sid, bytes(self._agg_buffer)))
                self._agg_buffer.clear()
            
    class _SynthCallback(ResultCallback):
        """Per-synthesizer view over the shared Callback.

        The DashScope SDK hands back no reference to the synthesizer that fired,
        and one Callback instance is shared across turns and reconnects. Stamping
        a generation at construction time is what lets on_complete tell "my
        synthesizer finished" apart from "a previous synthesizer's completion
        arrived late" -- the latter reads as the current turn finishing early,
        which is the defect the audio-done signal exists to remove. Everything
        else delegates untouched so the shared buffering/mute semantics stay put.
        """

        def __init__(self, inner, generation):
            self._inner = inner
            self._generation = generation
            # Newly created generation is the latest.
            inner.current_generation = generation

        def on_open(self):
            self._inner.on_open()

        def on_complete(self):
            self._inner.on_complete(generation=self._generation)

        def on_error(self, message: str):
            self._inner.on_error(message, generation=self._generation)

        def on_close(self):
            self._inner.on_close(generation=self._generation)

        def on_event(self, message):
            self._inner.on_event(message)

        def on_data(self, data: bytes) -> None:
            self._inner.on_data(data, generation=self._generation)

    audio_done = AudioDoneEmitter(response_queue)
    callback = Callback(response_queue, audio_done)
    synth_generation = 0
    synthesizer = None
    char_buffer = ""
    detected_lang = None
    last_streaming_call_time = None  # Track the latest streaming_call time.
    IDLE_AUTO_COMPLETE_SECONDS = 15  # Complete proactively after this idle duration, below the server's 23s timeout.
    # After soft flush (core idle sentinel / idle keepalive above) sends FINISH, the old synthesizer
    # still emits tails. More same-round text cannot immediately start another stream: two OGG streams
    # would interleave pages in shared buffers and corrupt audio. Buffer in char_buffer until old completion,
    # waiting at most this duration (DashScope FINISH->complete is usually <1s).
    SOFT_FLUSH_DRAIN_TIMEOUT_SECONDS = 2.5
    # Current synthesizer already sent FINISH for soft flush; the round remains open for more text.
    soft_finished = False
    soft_finish_deadline = None
    # (None, None) during soft flush: await old drainage before deciding to send audio_done or
    # start a new stream for buffered continuation before ending.
    round_end_pending = False
    # Another core soft-flush sentinel during drainage targets buffered continuation. Retain it,
    # or nobody sends FINISH after continuation starts and its tail waits for done. Apply it
    # on continuation startup; invalidate on further text, whose core timer rearms.
    soft_flush_pending = False

    def _create_synthesizer(lang_hint=None):
        """Create a new SpeechSynthesizer, with an optional language hint.
        Only establishes the WebSocket connection without sending warmup text — the caller sends real text right after.
        """
        from utils.api_config_loader import (
            cosyvoice_model_supports_language_hints,
            get_cosyvoice_clone_model,
        )
        nonlocal last_streaming_call_time, synth_generation
        clone_model = _enrolled_model or get_cosyvoice_clone_model(_voice_provider)
        synth_generation += 1
        kwargs = dict(
            model=clone_model,
            voice=voice_id,
            speech_rate=1.05,
            format=AudioFormat.OGG_OPUS_48KHZ_MONO_64KBPS,
            # Stamp this generation so late old-synthesizer on_complete callbacks recognize
            # expiry instead of falsely completing the new round.
            callback=_SynthCallback(callback, synth_generation),
        )
        if lang_hint and cosyvoice_model_supports_language_hints(clone_model):
            kwargs["language_hints"] = [lang_hint]
        callback.construct_start_time = time.time()
        # Hold DASHSCOPE_GLOBAL_LOCK while writing module globals and constructing SpeechSynthesizer;
        # concurrent /voice_preview /clone_voice flows otherwise overwrite between global assignment
        # and __init__, connecting with someone else's credentials/region.
        # Constructed synthesizers retain request state internally; running after unlock is safe.
        with DASHSCOPE_GLOBAL_LOCK:
            _apply_dashscope_region()
            syn = SpeechSynthesizer(**kwargs)
        last_streaming_call_time = time.time()
        return syn

    def _flush_buffer():
        """Detect the language, create the synthesizer (if needed) and flush the buffer"""
        nonlocal synthesizer, char_buffer, detected_lang, last_streaming_call_time
        if not char_buffer.strip():
            char_buffer = ""
            return
        hint = detect_tts_language_hint(char_buffer)
        if hint and detected_lang != hint:
            detected_lang = hint
            logger.info(f"CosyVoice 检测到 {hint} 语言提示")
        if synthesizer is None:
            synthesizer = _create_synthesizer(detected_lang)
            callback.accepted_speech_id = current_speech_id
        with prefer_dashscope_websocket_ipv4():
            synthesizer.streaming_call(char_buffer)
        _record_tts_telemetry("cosyvoice", len(char_buffer))
        last_streaming_call_time = time.time()
        char_buffer = ""

    def _do_streaming_complete(*, round_end: bool):
        """Non-blockingly notify the server that all text has been sent.
        Only sends the FINISHED signal without waiting for server confirmation. Audio keeps streaming to the frontend via the on_data callback.
        The synthesizer stays open and is closed at the next speech_id switch.

        ``round_end`` says whether this FINISH really terminates the utterance.
        The idle keep-alive caller passes False: it finishes the synthesizer only
        to beat the server's socket timeout, and more text of the same speech can
        still follow, so the resulting ``on_complete`` must not be reported as the
        end of the audio stream.
        """
        nonlocal synthesizer, last_streaming_call_time
        if synthesizer is None:
            callback.accepted_speech_id = None
            callback.reset_bootstrap_state()
            return False
        if callback.connection_lost:
            logger.info("CosyVoice WebSocket 已断开，跳过 streaming_complete")
            try:
                synthesizer.close()
            except Exception:
                pass
            synthesizer = None
            last_streaming_call_time = None
            return False

        # Arm markers before sending: SDK receiving threads may invoke on_complete before send
        # returns, especially for short sentences. Late arming misses audio_done
        # and falls back to frontend 700ms give-up.
        # FINISH sent this round; subsequent on_complete is actual audio finalization.
        # Idle keepalive FINISH does not count: more text may arrive and create another synthesizer;
        # audio_done from that completion would end frontend playback prematurely.
        callback.finish_requested_speech_id = current_speech_id if round_end else None
        sent = True
        try:
            synthesizer.ws.send(synthesizer.request.get_finish_request())
        except Exception as e:
            logger.warning(f"发送TTS完成信号失败: {e}")
            # FINISH failed to send, so the server sends no completion for this round. Retract the marker,
            # preventing another completion from being mistaken for this round's ending.
            callback.finish_requested_speech_id = None
            sent = False
        last_streaming_call_time = None
        # Do not clear accepted_speech_id/bootstrap immediately here.
        # Tails can still arrive after FINISH; finalize state through on_complete or later interruption/switching.
        # Report actual FINISH delivery so soft flush chooses drainage waiting or direct replacement.
        return sent

    def _soft_finish():
        """Send FINISH for everything said so far without ending the round.

        The server only synthesizes the trailing sentence once it sees FINISH,
        and on some realtime routes the round's real FINISH (core's ``(None,
        None)``, tied to the provider's ``response.done``) trails the last text
        by seconds. Finishing early releases that tail; the synthesizer is then
        left to drain and more text of the same speech is handled by
        ``_service_soft_finished``.
        """
        nonlocal soft_finished, soft_finish_deadline
        if soft_finished:
            return
        try:
            _flush_buffer()  # Short tails may remain in the six-character buffer without a synthesizer.
        except Exception as e:
            logger.warning(f"TTS soft flush buffer 失败: {e}")
        if synthesizer is None:
            return
        sent = _do_streaming_complete(round_end=False)
        if synthesizer is None:
            # Disconnected: _do_streaming_complete already discarded the synthesizer; nothing to await.
            return
        if not sent:
            # FINISH unsent means completion never arrives; entering soft-complete only wastes
            # 2.5s. Discard this untrustworthy connection; later text uses a fresh synthesizer.
            _retire_soft_finished_synthesizer(drained=False)
            return
        soft_finished = True
        soft_finish_deadline = time.time() + SOFT_FLUSH_DRAIN_TIMEOUT_SECONDS

    def _retire_soft_finished_synthesizer(*, drained: bool):
        """Drop a soft-finished synthesizer so the next text starts a fresh stream."""
        nonlocal synthesizer, soft_finished, soft_finish_deadline, last_streaming_call_time
        # Retire under lock: callbacks past generation checks must finish flushing/resetting
        # before replacement, or resume clearing new-stream buffers and FINISH.
        with callback._lock:
            callback.finish_requested_speech_id = None
            # Retire first: SDK threads can call on_data/on_close late after close(); without a current
            # generation they are stale and discarded, never entering the next same-speech_id
            # stream. New synthesizers stamp their own generation.
            callback.current_generation = None
            # Disconnected belongs to this connection and retires with it; leaving it True until
            # replacement makes connection_lost callers skip FINISH during the window.
            callback.connection_lost = False
            if not drained:
                # Abandoning before completion may leave old partial pages in shared buffers;
                # never join them to new streams, as with reconnects. Clear under lock with retirement.
                callback.reset_bootstrap_state()
        if synthesizer is not None:
            try:
                synthesizer.close()
            except Exception:
                # Discard this connection anyway; close failure must not prevent replacement. SDK closure
                # often fails on server-closed WebSockets, handled like interruption.
                pass
        synthesizer = None
        soft_finished = False
        soft_finish_deadline = None
        last_streaming_call_time = None

    def _service_soft_finished():
        """Advance the soft-finished state once the old stream has drained.

        Called from the idle loop and after every queue item while
        ``soft_finished`` is set. Nothing happens until the current
        generation's completion arrives (or the drain timeout passes); then
        buffered text of the same speech starts a new synthesizer, and a
        pending round end either finishes that new stream or, with nothing
        left to say, closes the audio stream directly.
        """
        nonlocal char_buffer, round_end_pending, soft_finished, soft_finish_deadline, soft_flush_pending
        if not soft_finished:
            return
        drained = callback.completed_generation == synth_generation
        if not drained and time.time() < soft_finish_deadline:
            return
        if char_buffer.strip():
            _retire_soft_finished_synthesizer(drained=drained)
            try:
                _flush_buffer()  # Continue the same round with a fresh synthesizer and buffered text.
            except Exception as e:
                # Like TTS Init Error: discard this text rather than retrying every 10ms.
                logger.error(f"TTS soft flush 续接失败: {e}")
                char_buffer = ""
            if round_end_pending:
                _do_streaming_complete(round_end=True)
            elif soft_flush_pending:
                # Core already detected continuation text becoming idle during drainage; soft-flush
                # the new stream immediately on creation, or its final sentence waits for done.
                _soft_finish()
        elif round_end_pending:
            if drained:
                # Old tails are queued; this supplementary ending follows them.
                audio_done.emit(current_speech_id)
            else:
                # Stop waiting: late completion can still send the missing ending. Otherwise frontend
                # give-up handles it; prefer missing endings to premature ones.
                callback.finish_requested_speech_id = current_speech_id
            soft_finished = False
            soft_finish_deadline = None
        else:
            # Drained but round open without new text: remain soft-complete awaiting the next signal.
            return
        round_end_pending = False
        soft_flush_pending = False

    while True:
        # Poll queue nonblockingly, prioritizing interruptions.
        if request_queue.empty():
            _service_soft_finished()
            # Proactive completion: exceed idle threshold while WebSocket still lives, before
            # (None,None) arrives after server's 23s connection timeout.
            # Use soft flush's state machine; later same-round text awaits drainage before continuation.
            if (not soft_finished
                    and synthesizer is not None
                    and last_streaming_call_time is not None
                    and time.time() - last_streaming_call_time > IDLE_AUTO_COMPLETE_SECONDS):
                logger.debug(f"CosyVoice 空闲 >{IDLE_AUTO_COMPLETE_SECONDS}s，主动 streaming_complete")
                _soft_finish()
            time.sleep(0.01)
            continue

        sid, tts_text = request_queue.get()

        if sid == TTS_SHUTDOWN_SENTINEL:
            break

        if sid == "__interrupt__":
            # Interrupt: mute callbacks immediately -> close synthesizer -> clear state.
            # Mute before close so old SDK WebSocket threads stop feeding response_queue.
            callback._muted = True
            audio_done.begin_interrupt()  # Interrupted rounds send no audio_done; they use the independent cancellation channel.
            # try/finally mirrors step/qwen/grok/elevenlabs/gptsovits: unexpected teardown errors
            # must not leave emitters permanently interrupted, silently missing audio_done
            # for every later session round.
            try:
                if synthesizer is not None:
                    try:
                        synthesizer.close()
                    except Exception:
                        # Interruption teardown: discard connection anyway, so close failures must not stop state cleanup;
                        # propagation only interrupts interruption itself (already-muted callbacks cannot feed data).
                        pass
                synthesizer = None
                last_streaming_call_time = None
                current_speech_id = None
                char_buffer = ""
                detected_lang = None
                callback.connection_lost = False
                callback.accepted_speech_id = None
                callback.reset_bootstrap_state()
                audio_done.reset()
                soft_finished = False
                soft_finish_deadline = None
                round_end_pending = False
                soft_flush_pending = False
            finally:
                audio_done.end_interrupt()
            continue

        if sid == TTS_SOFT_FLUSH_SENTINEL:
            # Core text-idle sentinel applies only to the currently speaking round. Ignore late signals with changed sid
            # or normally finalized rounds, avoiding truncation of others' streams.
            if tts_text is not None and tts_text == current_speech_id:
                if soft_finished:
                    # Old stream draining: retain this for buffered continuation and apply after
                    # continuation starts, never discard locally.
                    soft_flush_pending = bool(char_buffer.strip())
                else:
                    _soft_finish()
            continue

        if sid is None:
            if soft_finished:
                # Soft flush already sent FINISH; never send twice on one connection.
                # Await old drainage: speak buffered continuation in a fresh stream before ending, or send ending directly without text.
                round_end_pending = True
                _service_soft_finished()
                detected_lang = None
                continue
            # Normal end: notify TTS no more text, nonblockingly.
            try:
                _flush_buffer()
            except Exception as e:
                logger.warning(f"TTS flush buffer 失败: {e}")
            _do_streaming_complete(round_end=True)
            # Retain current_speech_id/synthesizer:
            # audio keeps streaming to frontend until the next speech_id switch interrupts.
            char_buffer = ""
            detected_lang = None
            continue

        if current_speech_id is None:
            current_speech_id = sid
            callback.accepted_speech_id = sid
        elif current_speech_id != sid:
            # Disable callbacks first so old tails cannot get new-round labels.
            callback.accepted_speech_id = None
            callback._muted = True
            if synthesizer is not None:
                try:
                    synthesizer.close()
                except Exception:
                    pass
            synthesizer = None
            last_streaming_call_time = None
            current_speech_id = sid
            char_buffer = ""
            detected_lang = None
            # Explicitly clear aggregation: close() triggers on_complete->reset_bootstrap_state,
            # but delayed SDK on_complete may follow new on_open, carrying old _agg_buffer data
            # into new rounds. Clearing now removes the race.
            callback.reset_bootstrap_state()
            callback.accepted_speech_id = sid
            audio_done.reset()  # Reset audio_done deduplication for the new round.
            soft_finished = False
            soft_finish_deadline = None
            round_end_pending = False
            soft_flush_pending = False

        if tts_text is None or not tts_text.strip():
            time.sleep(0.01)
            continue

        if soft_finished:
            # More same-round text after soft flush: old synthesizers sent FINISH and reject further
            # streaming_call as task-ended; new streams cannot start immediately because two OGG
            # streams interleave shared pages. Buffer until old drainage, then continue.
            char_buffer += tts_text
            hint = detect_tts_language_hint(tts_text)
            if hint and detected_lang != hint:
                detected_lang = hint
            # Previous soft flush targeted earlier text; core rearms timers for this fragment.
            soft_flush_pending = False
            _service_soft_finished()
            continue

        # Before synthesizer creation, buffer until TTS_LANG_DETECT_MIN_CHARS and send together.
        if synthesizer is None:
            char_buffer += tts_text
            hint = detect_tts_language_hint(tts_text)
            if hint and detected_lang != hint:
                detected_lang = hint
            if len(char_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                continue
            try:
                if detected_lang:
                    logger.info(f"CosyVoice 语言提示: {detected_lang}")
                synthesizer = _create_synthesizer(detected_lang)
                callback.accepted_speech_id = current_speech_id
                with prefer_dashscope_websocket_ipv4():
                    synthesizer.streaming_call(char_buffer)
                _record_tts_telemetry("cosyvoice", len(char_buffer))
                last_streaming_call_time = time.time()
                char_buffer = ""
            except Exception as e:
                logger.error(f"TTS Init Error: {e}")
                synthesizer = None
                current_speech_id = None
                char_buffer = ""
                detected_lang = None
                last_streaming_call_time = None
                callback.accepted_speech_id = None
                callback.reset_bootstrap_state()
                time.sleep(0.1)
                continue
        else:
            try:
                with prefer_dashscope_websocket_ipv4():
                    synthesizer.streaming_call(tts_text)
                last_streaming_call_time = time.time()
            except Exception:
                if synthesizer is not None:
                    # More audio remains this round; retract FINISH first, because close-triggered
                    # on_complete is not round completion and would emit early.
                    callback.finish_requested_speech_id = None
                    try:
                        synthesizer.close()
                    except Exception:
                        pass
                    synthesizer = None
                    last_streaming_call_time = None
                    # Clear old partial packets from shared buffers locally. Reconnects reuse
                    # speech_id, while on_data resets only on sid changes, so these partial
                    # packets would join new-stream data into corrupt audio. Previously old
                    # synthesizer late on_complete incidentally reset them, but stale callbacks
                    # now return completely to protect current-round state;
                    # cleanup must happen here.
                    callback.reset_bootstrap_state()

                try:
                    synthesizer = _create_synthesizer(detected_lang)
                    callback.accepted_speech_id = current_speech_id
                    with prefer_dashscope_websocket_ipv4():
                        synthesizer.streaming_call(tts_text)
                    last_streaming_call_time = time.time()
                except Exception as reconnect_error:
                    logger.error(f"TTS Reconnect Error: {reconnect_error}")
                    response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                    time.sleep(1.0)
                    synthesizer = None
                    current_speech_id = None
                    last_streaming_call_time = None
                    callback.accepted_speech_id = None
                    callback.reset_bootstrap_state()

    # After TTS_SHUTDOWN_SENTINEL exits the loop, mute callbacks and close synthesizers,
    # preventing internal SDK WebSocket threads writing response_queue.
    callback._muted = True
    if synthesizer is not None:
        try:
            synthesizer.close()
        except Exception:
            # Best-effort closure never raises, matching other synthesizer.close()
            # blocks (L1644 /1683 /1718 /1770). SDK WebSockets are often already
            # reclaimed by servers; common unrecoverable errors only add log noise.
            pass
        synthesizer = None

def _cosyvoice_clone_is_selected(ctx) -> bool:
    vm = ctx.voice_meta
    return bool(vm and vm.get('provider') in ('cosyvoice', 'cosyvoice_intl'))

def _cosyvoice_clone_resolve(ctx):
    vm = ctx.voice_meta or {}
    provider = vm.get('provider') or 'cosyvoice'
    runtime = ctx.cm.get_cosyvoice_clone_runtime(provider)
    runtime_key = (runtime.get('api_key') or '').strip()
    # provider=='cosyvoice_intl' requires international keys/endpoints. Missing runtime_key returning only
    # None lets core.py's `api_key_override or tts_config['api_key']` fall back to tts_custom's
    # domestic key, causing 401 on every international utterance, harder to diagnose than dummy silence.
    if provider == 'cosyvoice_intl' and not runtime_key:
        logger.warning(
            "阿里国际版 CosyVoice 克隆音色 %s 选中，但 intl key 缺失，"
            "改用 dummy TTS worker 避免用错凭证打 intl 端点", ctx.voice_id)
        return dummy_tts_worker, None, None
    logger.info("检测到阿里 CosyVoice 克隆音色: %s (provider=%s)，使用 CosyVoice TTS Worker",
                ctx.voice_id, provider)
    return cosyvoice_vc_tts_worker, (runtime_key or None), 'cosyvoice'
