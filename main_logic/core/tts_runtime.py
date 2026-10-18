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
"""TTS runtime for ``LLMSessionManager``: worker thread lifecycle, the
audio stream worker, text chunk feeding and normalization, voice
routing/resolution, and the TTS response handler.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import hashlib
import json
import math
import re
import time
from typing import Optional
from fastapi import WebSocketDisconnect
from utils.frontend_utils import (
    contains_chinese,
    replace_blank,
    replace_corner_mark,
    remove_bracket,
    is_only_punctuation,
    TtsMarkdownStripper,
    TtsBracketStripper,
    is_tts_operand_char,
    is_tts_word_char,
    strip_leading_emoji_joiners,
    strip_tts_muted_symbols,
    tts_chunk_ends_in_emoji,
    tts_chunk_trailing_name_hash,
    tts_first_unmuted_char,
    tts_chunk_trailing_minus,
    tts_compact_symbol_run,
)
from main_logic.omni_offline_client import _is_safety_violation_signal
from main_logic.tts_client import (
    dummy_tts_worker,
    TTS_SHUTDOWN_SENTINEL,
    TTS_AUDIO_DONE_SENTINEL,
    TTS_SOFT_FLUSH_SENTINEL,
    TTS_PROVIDER_REGISTRY,
    VLLM_OMNI_DEFAULT_BASE_URL,
    VLLM_OMNI_DEFAULT_MODEL,
)
from utils.gptsovits_config import is_gsv_disabled_voice_id
from utils.config_manager import _as_bool, get_reserved
from utils.tts.native_voice_registry import is_free_preset_voice_id, resolve_native_voice_for_routing
from utils.api_config_loader import get_livestream_config
from threading import Thread
from queue import Queue
from ._shared import (
    logger,
    NO_RETRY_TTS_CODES,
    IMMEDIATE_REPORT_TTS_CODES,
    TTS_RESPAWN_DELAY_SECONDS,
    TTS_RATE_LIMIT_MAX_RESPAWN_DELAY_SECONDS,
    TTS_RATE_LIMIT_DEADLINE_SLACK_SECONDS,
)
from .notices import enqueue_voice_migration_notice
from .game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE, GameSpeechCaptureOwner
from .tts_records import (
    MAX_LIVE_TTS_RUNTIMES, TTS_FRAME_WRITE_TIMEOUT_SECONDS, TTS_HANDLER_CANCEL_GRACE_SECONDS,
    TTS_SOCKET_CLOSE_TIMEOUT_SECONDS, TtsCapacityError, TtsRuntimeRecord, tts_output_runtime,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class _GameSpeechPreloadCancelled(Exception):
    """Internal signal that a preload batch was superseded or torn down.

    Deliberately not ``asyncio.CancelledError``: absorbing that one to return
    a normal result swallows a genuine cancellation of the request task, so
    the coroutine reports a value to a caller that asked it to stop.
    """


class TtsRuntimeMixin:
    """TTS runtime methods (see module docstring)."""

    @staticmethod
    def _normalize_speech_playback_gain(value) -> float:
        if isinstance(value, bool):
            return 1.0
        try:
            gain = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(gain):
            return 1.0
        return max(0.0, min(2.0, gain))

    def remember_speech_playback_gain(self, speech_id, gain) -> float:
        """Remember a bounded per-speech mix override until its stream closes."""
        max_items = 32
        normalized = self._normalize_speech_playback_gain(gain)
        key = str(speech_id or "")
        if not key:
            return normalized
        gains = getattr(self, "_speech_playback_gains", None)
        if gains is None:
            # Compatibility for tests and integrations constructing via __new__.
            from collections import OrderedDict
            gains = self._speech_playback_gains = OrderedDict()
        gains.pop(key, None)
        # Gain 1.0 is the implicit default and needs no resident entry.
        if normalized != 1.0:
            gains[key] = normalized
            while len(gains) > max_items:
                gains.popitem(last=False)
        return normalized

    def speech_playback_gain(self, speech_id) -> float:
        key = str(speech_id or "")
        gains = getattr(self, "_speech_playback_gains", None)
        if not key or gains is None:
            return 1.0
        gain = gains.get(key, 1.0)
        if key in gains:
            gains.move_to_end(key)
        return self._normalize_speech_playback_gain(gain)

    def release_speech_playback_gain(self, speech_id) -> None:
        gains = getattr(self, "_speech_playback_gains", None)
        if gains is not None:
            gains.pop(str(speech_id or ""), None)

    def clear_speech_playback_gains(self) -> None:
        gains = getattr(self, "_speech_playback_gains", None)
        if gains is not None:
            gains.clear()

    def _begin_game_speech_completion_wait(self, speech_id: object) -> asyncio.Future:
        """Create the single bounded completion slot used by game speech."""
        self._cancel_game_speech_completion_wait()
        future = asyncio.get_running_loop().create_future()
        speech_key = str(speech_id or "")
        self._game_speech_completion_waiter = (speech_key, future)
        self._game_speech_delivery_state = (speech_key, True)
        return future

    def _mark_game_speech_delivery_failed(self, speech_id: object) -> None:
        """Latch a failed chunk/done delivery for the current bounded slot."""
        speech_key = str(speech_id or "")
        slot = getattr(self, "_game_speech_delivery_state", None)
        if slot and slot[0] == speech_key:
            self._game_speech_delivery_state = (speech_key, False)
        elif slot is None and speech_key:
            # Cache-capture tests and legacy callers may stream without the
            # HTTP completion waiter. Keep the same single bounded failure
            # latch until their matching audio_done arrives.
            self._game_speech_delivery_state = (speech_key, False)

    def _game_speech_delivery_succeeded(self, speech_id: object) -> bool:
        slot = getattr(self, "_game_speech_delivery_state", None)
        if not slot or slot[0] != str(speech_id or ""):
            return True
        return bool(slot[1])

    def _clear_game_speech_delivery_state(self, speech_id: object | None = None) -> None:
        slot = getattr(self, "_game_speech_delivery_state", None)
        if speech_id is not None and slot and slot[0] != str(speech_id or ""):
            return
        self._game_speech_delivery_state = None

    def _fail_dropped_tts_speech(self, speech_ids) -> None:
        """Settle rounds whose cached TTS text is discarded without synthesis.

        A completion waiter for such a round would otherwise sit out its
        timeout, because no audio_done will ever arrive for it.
        """
        for speech_id in speech_ids:
            GAME_SPEECH_AUDIO_CACHE.fail_capture(self, speech_id)
            self._resolve_game_speech_completion_wait(speech_id, False)

    def _resolve_game_speech_completion_wait(self, speech_id: object, completed: bool) -> None:
        slot = getattr(self, "_game_speech_completion_waiter", None)
        if not slot or slot[0] != str(speech_id or ""):
            return
        self._game_speech_completion_waiter = None
        self._clear_game_speech_delivery_state(speech_id)
        future = slot[1]
        if not future.done():
            future.set_result(bool(completed))

    def _cancel_game_speech_completion_wait(self) -> None:
        slot = getattr(self, "_game_speech_completion_waiter", None)
        self._game_speech_completion_waiter = None
        self._clear_game_speech_delivery_state(slot[0] if slot else None)
        if slot and not slot[1].done():
            slot[1].set_result(False)

    def _remember_game_speech_correlation(self, speech_id: object, correlation_id: object) -> None:
        speech_key = str(speech_id or "")
        correlation_key = str(correlation_id or "")[:128]
        self._game_speech_correlation = (
            (speech_key, correlation_key)
            if speech_key and correlation_key
            else None
        )

    def _game_speech_correlation_for(self, speech_id: object) -> str:
        slot = getattr(self, "_game_speech_correlation", None)
        if not slot or slot[0] != str(speech_id or ""):
            return ""
        return slot[1]

    def _clear_game_speech_correlation(self, speech_id: object | None = None) -> None:
        slot = getattr(self, "_game_speech_correlation", None)
        if speech_id is not None and slot and slot[0] != str(speech_id or ""):
            return
        self._game_speech_correlation = None

    async def _wait_for_game_speech_completion(
        self,
        speech_id: object,
        future: asyncio.Future,
        timeout: float,
    ) -> bool:
        """Wait with a hard lifetime and release the slot on every exit path."""
        try:
            return bool(await asyncio.wait_for(
                asyncio.shield(future),
                timeout=max(0.1, min(float(timeout), 55.0)),
            ))
        except asyncio.TimeoutError:
            return False
        except asyncio.CancelledError:
            raise
        finally:
            slot = getattr(self, "_game_speech_completion_waiter", None)
            if slot and slot[0] == str(speech_id or "") and slot[1] is future:
                self._game_speech_completion_waiter = None
                self._clear_game_speech_delivery_state(speech_id)
                if not future.done():
                    future.set_result(False)

    def _get_text_guard_max_length(self) -> int:
        """Read the user-configured reply token cap.
        Unit: tiktoken (o200k_base) tokens. 0 = unlimited (returns 999999).
        Default 300 tokens ≈ 400 CJK characters / ~1200 English characters.
        """
        try:
            # Prefer conversation settings; fall back to core configuration if absent.
            conversation_settings = _core_facade.load_global_conversation_settings()
            if 'textGuardMaxLength' in conversation_settings:
                value = int(conversation_settings['textGuardMaxLength'])
            else:
                value = int(self._config_manager.get_core_config().get('TEXT_GUARD_MAX_LENGTH', 300))
            # Zero / negative means unlimited, matching OmniOfflineClient.__init__ /
            # update_max_response_length. Previously negative values raised and
            # fell back to 300, silently degrading existing -1 configurations.
            if value <= 0:
                return 999999
            return value
        except Exception:
            return 300

    def _reset_tts_replay_state(self) -> None:
        """Forget text retained for configured-provider runtime recovery."""
        self._tts_replay_speech_id = None
        self._tts_replay_chunks = []
        self._tts_replay_sent_chunks = []
        self._tts_replay_done = False
        self._tts_replay_audio_emitted = False
        self._tts_replay_sentence_audio_emitted = False

    def _remember_tts_replay_chunk(self, speech_id, text: str) -> None:
        """Retain one raw text chunk until the active utterance is replaced."""
        if speech_id != getattr(self, "_tts_replay_speech_id", None):
            self._reset_tts_replay_state()
            self._tts_replay_speech_id = speech_id
        if text:
            self._tts_replay_chunks.append((speech_id, text))

    def _remember_tts_sent_chunk(self, speech_id, text: str) -> None:
        """Retain text actually submitted to a sentence-aware worker."""
        if speech_id == getattr(self, "_tts_replay_speech_id", None) and text:
            self._tts_replay_sent_chunks.append((speech_id, text))

    def _consume_tts_replay_sentence(self, speech_id, sentence: str) -> None:
        """Remove one confirmed audible sentence from the fallback suffix."""
        if speech_id != getattr(self, "_tts_replay_speech_id", None) or not sentence:
            return
        remaining = "".join(
            text
            for sid, text in getattr(self, "_tts_replay_sent_chunks", ())
            if sid == speech_id
        )
        if not remaining.startswith(sentence):
            # On boundary mismatch, retain text rather than wrongly removing unplayed content.
            logger.warning("TTS 回放句界不匹配，保留现有未确认文本")
            return
        remaining = remaining[len(sentence):]
        self._tts_replay_sent_chunks = (
            [(speech_id, remaining)] if remaining else []
        )

    def _put_tts_text(self, speech_id, text: str) -> None:
        """Put one cleaned text chunk on the TTS request queue (the only place text is queued).

        Also records it for replay / echo suppression, and reports its length
        to the ``on_enqueued`` callback of a streaming mirror speech with
        this ``speech_id`` (``open_mirror_speech_stream``); ordinary chat
        registers none. The caller holds ``tts_cache_lock``.
        """
        self.tts_request_queue.put((speech_id, text))
        self._remember_tts_sent_chunk(speech_id, text)
        self._remember_pending_ai_voice_echo(speech_id, text)
        callbacks = getattr(self, "_mirror_stream_callbacks", None)
        callback = callbacks.get(speech_id) if callbacks and speech_id is not None else None
        if callback is not None:
            try:
                callback(len(text))
            except Exception as exc:
                logger.warning("mirror speech enqueue callback failed: %s", exc)

    def _enqueue_tts_text_chunk(self, speech_id, text: str) -> None:
        """Enqueue a text chunk into the TTS queue; http_sentence-class providers go through the normalizer.

        The caller must already hold ``self.tts_cache_lock`` (consistent with the
        existing put call sites). For ws_bistream-class providers (qwen / step /
        cosyvoice), text fragments are sent straight to the server, skipping the
        normalizer to avoid pending_spaces latency and CJK-boundary space removal
        disturbing the server's synthesis cadence. Control signals
        (``__interrupt__`` interrupt / ``(None, None)`` end-of-utterance flush /
        ``("__shutdown__", None)`` worker exit) should still be sent directly via
        ``tts_request_queue.put``, calling ``_reset_tts_stream_normalizer`` at the
        appropriate moment.
        """
        # Retain pre-normalized text because the replacement may use another protocol class.
        # Preserve raw pre-normalization text; fallback worker may use another protocol class and need original-text replay.
        self._remember_tts_replay_chunk(speech_id, text)

        # On speech_id change, reset all stripper state (discard pending content from previous turn).
        if speech_id != self._tts_norm_speech_id:
            self._tts_stream_normalizer.reset()
            self._tts_markdown_stripper.reset()
            self._tts_bracket_stripper.reset()
            self._reset_tts_symbol_gap()
            self._tts_norm_speech_id = speech_id

        if self._tts_normalize_enabled:
            text = self._tts_stream_normalizer.feed(text)
            if not text:
                return
        # Fixed markdown → bracket order: strip links to text before bracket processing.
        text = self._tts_markdown_stripper.feed(text)
        if not text:
            return
        text = self._tts_bracket_stripper.feed(text)
        if not text:
            return
        # Final pass: remove pronounceable decorative / technical symbols (@, |, etc.); preserve operators, currency,
        # minus signs, C#'s #, and ordinary punctuation.
        text = self._strip_tts_symbols_across_chunks(text)
        if getattr(self, "_tts_pending_name_hash", ""):
            # With chunks 'C', '#', the # is undecided: idle soft flush would synthesize C first, losing C# pronunciation.
            # Wait for the next chunk or finalization to resolve #.
            self._cancel_tts_soft_flush()
            if text:
                self._put_tts_text(speech_id, text)
            return
        if not text:
            return
        self._put_tts_text(speech_id, text)
        logger.debug(
            "[voice-chain] stage=tts_enqueue speech_id=%s text_len=%d",
            speech_id,
            len(text),
        )
        # Every enqueued chunk resets idle timer to idle seconds later; if text stops and done
        # is delayed, let the worker synthesize its buffered final phrase first.
        self._arm_tts_soft_flush(speech_id)

    @staticmethod
    def _tts_soft_flush_idle_seconds() -> float:
        """How long the turn's text may go quiet before the worker is told to flush.

        Measured on lanlan.app: gaps between transcript deltas inside one reply
        stay under ~0.6s, while ``response.done`` trails the last delta by 1.2s
        in the good case and 9.5s in the bad one. 1s sits between the two.
        Overridable via ``NEKO_TTS_SOFT_FLUSH_IDLE_SECONDS`` (floored at 0.3s so
        a typo cannot turn every inter-word pause into a synthesizer rebuild).
        """
        import os
        raw = os.environ.get("NEKO_TTS_SOFT_FLUSH_IDLE_SECONDS", "").strip()
        if not raw:
            return 1.0
        try:
            return max(0.3, float(raw))
        except ValueError:
            return 1.0

    def _arm_tts_soft_flush(self, speech_id) -> None:
        """(Re)start the idle timer that asks the worker to flush the held tail.

        Only for realtime voice sessions on a provider whose worker understands
        ``TTS_SOFT_FLUSH_SENTINEL``. In text mode the completion callback lands
        right behind the last chunk, so there is nothing to bridge; a worker
        that does not know the sentinel would read it as a new speech id.
        """
        if not getattr(self, "_tts_soft_flush_supported", False):
            return
        if getattr(self, "input_mode", None) != "audio":
            return
        self._cancel_tts_soft_flush()
        try:
            self._tts_soft_flush_task = self._fire_task(
                self._tts_soft_flush_after_idle(speech_id)
            )
        except RuntimeError:
            # No running loop (synchronous test fixture enqueues directly): no fallback, simply do not send.
            self._tts_soft_flush_task = None

    def _cancel_tts_soft_flush(self) -> None:
        task = getattr(self, "_tts_soft_flush_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._tts_soft_flush_task = None

    async def _tts_soft_flush_after_idle(self, speech_id) -> None:
        """Fire the soft flush if the turn is still open and still this speech.

        Everything that would make the flush wrong is re-checked under the
        cache lock after the sleep: the round already has its done sentinel
        queued (or deferred until the worker is ready — the pending chunks will
        re-arm this timer when they flush), the speech id moved on (barge-in /
        rotation), the worker went away, or chunks are still waiting to be
        replayed to it.
        """
        runtime = self._snapshot_tts_runtime()
        await asyncio.sleep(self._tts_soft_flush_idle_seconds())
        async with self.tts_cache_lock:
            if not self._tts_runtime_is_current(runtime):
                return
            if self._tts_done_queued_for_turn or self._tts_done_pending_until_ready:
                return
            if speech_id is None or speech_id != self.current_speech_id:
                return
            if not (self.tts_ready and self.tts_thread and self.tts_thread.is_alive()):
                return
            if self.tts_pending_chunks:
                return
            self.tts_request_queue.put((TTS_SOFT_FLUSH_SENTINEL, speech_id))
        logger.debug("TTS 文本空闲 %.1fs 且 done 未到，发软 flush speech_id=%s",
                     self._tts_soft_flush_idle_seconds(), speech_id)

    def _reset_tts_stream_normalizer(self) -> None:
        """Clear all TTS text stripper state. Called on interrupt / turn end / session rebuild."""
        self._tts_stream_normalizer.reset()
        self._tts_markdown_stripper.reset()
        self._tts_bracket_stripper.reset()
        self._reset_tts_symbol_gap()
        self._tts_norm_speech_id = None

    def _reset_tts_symbol_gap(self) -> None:
        self._tts_last_spoken_char = ""
        # Last two pronounced characters: deciding whether leading # belongs to names like C# needs two-character context.
        self._tts_last_spoken_tail = ""
        self._tts_symbol_gap_pending = False
        self._tts_prev_chunk_ended_emoji = False
        self._tts_pending_minus = ""
        self._tts_pending_name_hash = ""
        self._tts_deferred_symbols = ""

    def _strip_tts_symbols_across_chunks(self, text: str) -> str:
        """Apply ``strip_tts_muted_symbols`` to one streamed chunk, keeping the
        separator a symbol-only chunk stood for.

        Streaming often splits ``3~5`` into ``"3"``, ``"~"``, ``"5"``. The middle
        chunk filters to nothing, and dropping it would glue the neighbours into
        ``35``. When that happens between an ASCII letter/digit and the next
        chunk's ASCII letter/digit, the next chunk gets a leading space instead.
        A minus sign cut off at the end of a chunk ("x = -" + "5") is likewise
        re-attached when the next chunk starts with a digit, and the "#" of a
        name split off its letter ("C" + "#" + " dev") is kept.
        Returns ``""`` when nothing is left to speak.
        """
        if text and not text.strip():
            # Pass whitespace-only chunks unchanged (streaming providers without normalizer use them to separate
            # '9', ' ', '28'); record as previous character so next chunk needs no extra space.
            pending_name_hash = getattr(self, "_tts_pending_name_hash", "")
            self._tts_symbol_gap_pending = False
            self._tts_prev_chunk_ended_emoji = False
            self._tts_pending_minus = ""
            self._tts_pending_name_hash = ""
            self._tts_deferred_symbols = ""
            self._tts_last_spoken_char = text[-1]
            self._tts_last_spoken_tail = (
                getattr(self, "_tts_last_spoken_tail", "") + pending_name_hash + text
            )[-2:]
            return pending_name_hash + text
        if getattr(self, "_tts_prev_chunk_ended_emoji", False):
            # Prior chunk ended in emoji: leading zero-width joiners / variation selectors here are residue of a split
            # composite emoji; remove them too.
            text = strip_leading_emoji_joiners(text)
            if not text:
                return ""
        self._tts_prev_chunk_ended_emoji = tts_chunk_ends_in_emoji(text)
        last = getattr(self, "_tts_last_spoken_char", "")
        last_tail = getattr(self, "_tts_last_spoken_tail", "") or last
        # Trailing minus cannot see next chunk's number and would be stripped as a symbol: remember it,
        # restoring when next chunk starts with a digit.
        pending_minus = getattr(self, "_tts_pending_minus", "")
        # Include earlier unspoken symbol-only chunks when deciding, so chunk boundaries do not change results:
        # 'C', '#', '-', '5' equals 'C#-5'; '3', '@', '-', '5' equals '3@-5'.
        deferred_symbols = getattr(self, "_tts_deferred_symbols", "")
        self._tts_pending_minus = tts_chunk_trailing_minus(
            deferred_symbols + text, last_tail
        )
        # C# may split ('C', '#', ' dev'; 'a#', '@', 'b'): trailing # cannot see
        # following text, so buffer until next nonempty chunk or finalization; intervening symbol-only chunks do not count.
        pending_name_hash = getattr(self, "_tts_pending_name_hash", "")
        held_name_hash = tts_chunk_trailing_name_hash(text, last_tail)
        cleaned = strip_tts_muted_symbols(text, last_tail)
        if held_name_hash and cleaned.endswith(held_name_hash):
            cleaned = cleaned[: -len(held_name_hash)]
        if not cleaned or not cleaned.strip():
            # Entire chunk is symbols: remember a separator existed and let next chunk decide whether to insert a space.
            if held_name_hash:
                self._tts_pending_name_hash = held_name_hash
            if text and text.strip() and is_tts_operand_char(last):
                self._tts_symbol_gap_pending = True
            self._tts_deferred_symbols = tts_compact_symbol_run(
                deferred_symbols + text
            )
            return ""
        # This chunk's buffered trailing # also counts as unspoken symbol: 'C#', '-', '5' equals 'C#-5'.
        self._tts_deferred_symbols = held_name_hash
        self._tts_pending_name_hash = held_name_hash
        next_char = tts_first_unmuted_char(text)
        minus_follows = bool(pending_minus) and cleaned[0].isdigit()
        prefix = ""
        if pending_name_hash and (
            minus_follows or not (next_char.isascii() and next_char.isalnum())
        ):
            prefix += pending_name_hash
        if minus_follows:
            prefix += pending_minus
        if prefix:
            cleaned = prefix + cleaned
        elif (
            getattr(self, "_tts_symbol_gap_pending", False)
            and is_tts_operand_char(last)
            and is_tts_word_char(cleaned[0])
        ):
            cleaned = " " + cleaned
        # Buffered trailing #: there was a separator; insert space when next chunk starts alphanumeric.
        self._tts_symbol_gap_pending = bool(held_name_hash) and is_tts_word_char(
            cleaned[-1]
        )
        self._tts_last_spoken_char = cleaned[-1]
        self._tts_last_spoken_tail = (last_tail + cleaned)[-2:]
        return cleaned

    def _request_tts_done_locked(self) -> str:
        """Request that a TTS end signal be enqueued for the current turn.

        The caller must already hold ``self.tts_cache_lock``. If text is still
        pending or the worker isn't ready yet, only the deferred state is recorded,
        and `_flush_tts_pending_chunks()` re-sends it after ready, so that
        `(None, None)` never enters the queue before the text chunks.
        """
        if self._tts_done_queued_for_turn:
            return "already"

        worker_alive = bool(self.tts_thread and self.tts_thread.is_alive())
        if not worker_alive:
            return "no_worker"

        # Turn is finalizing (queued immediately or replayed after readiness): idle soft flush no longer matters.
        self._cancel_tts_soft_flush()

        if not self.tts_ready or self.tts_pending_chunks:
            self._tts_replay_done = True
            self._tts_done_pending_until_ready = True
            return "deferred"

        # Emit pending markdown/bracket stripper content via fallback: chain markdown.flush()
        # → bracket.feed(...) → bracket.flush(), matching _enqueue_tts_text_chunk's
        # composition order. markdown.flush strips residual lone markers before emitting;
        # bracket.feed handles remaining bracket characters, while bracket.flush resets without reading
        # unclosed bracket content. normalizer.flush always returns "", so omit it.
        # Leave dangling * _ ~ ` to symbol filtering below, which remembers the prior chunk ending and can insert separating spaces.
        flushed = self._tts_markdown_stripper.flush(keep_symbol_markers=True)
        if flushed:
            flushed = self._tts_bracket_stripper.feed(flushed)
        self._tts_bracket_stripper.flush()
        if flushed:
            flushed = self._strip_tts_symbols_across_chunks(flushed)
        # Turn ends with 'C', '#' or finalization buffer itself ends in C#: buffered # has no
        # next chunk to supply context, so release it on finalization too.
        pending_name_hash = getattr(self, "_tts_pending_name_hash", "")
        self._tts_pending_name_hash = ""
        if pending_name_hash:
            flushed = (flushed or "") + pending_name_hash
        if flushed and self._tts_norm_speech_id is not None:
            self._put_tts_text(self._tts_norm_speech_id, flushed)

        self.tts_request_queue.put((None, None))
        # Keep streaming mirror's enqueue callback registered until its end marker actually queues (immediate or readiness replay both pass here),
        # handing the TTS turn to the next stream at the same moment.
        callbacks = getattr(self, "_mirror_stream_callbacks", None)
        if callbacks:
            callbacks.pop(self.current_speech_id, None)
        ends = getattr(self, "_mirror_stream_ends", None)
        end_queued = ends.pop(self.current_speech_id, None) if ends else None
        if end_queued is not None:
            end_queued()
        self._tts_replay_done = True
        self._tts_done_queued_for_turn = True
        self._tts_done_pending_until_ready = False
        return "queued"

    async def _request_tts_done_for_turn(
        self,
        source: str,
        expected_speech_id: str | None = None,
    ) -> str:
        """Thread-safely request the TTS end signal for the current turn.

        ``expected_speech_id`` is an optional sid check: callers holding a snapshot
        of this turn's sid pass it in, and the function only sends done after
        confirming inside the lock that ``self.current_speech_id`` still equals the
        snapshot. In recovery / proactive scenarios where the user starts a new
        turn between awaits, the old turn's done signal would otherwise terminate
        the new turn's TTS outright (first sentence clipped / whole turn silent).
        Omitting it keeps the original behavior: always send done."""
        if not self.use_tts:
            return "disabled"

        runtime = self._snapshot_tts_runtime()
        async with self.tts_cache_lock:
            if not self._tts_runtime_is_current(runtime) or not self._tts_output_is_current():
                return "stale"
            if expected_speech_id is not None and self.current_speech_id != expected_speech_id:
                logger.debug(
                    "%s: stale TTS done skipped (expected=%s current=%s)",
                    source, expected_speech_id, self.current_speech_id,
                )
                return "stale"
            status = self._request_tts_done_locked()

        if status == "already":
            logger.debug("%s: TTS done 已排入队列，跳过重复信号", source)
        elif status == "deferred":
            logger.debug("%s: TTS 未就绪或仍有 pending chunk，延迟排入 done 信号", source)

        return status

    def _can_preserve_tts_ready_for_session_start(self) -> bool:
        """A live, previously-ready TTS worker will not emit __ready__ again."""
        current_key = self._build_tts_runtime_key()
        worker_key = getattr(self, "_tts_runtime_key", None)
        return bool(
            self.tts_ready
            and self._tts_runtime_is_current(self._snapshot_tts_runtime())
            and self.tts_thread is not None
            and self.tts_thread.is_alive()
            and current_key == worker_key
        )

    @staticmethod
    def resolve_tts_api_key(provider_key: str | None, api_key_override: str | None, tts_config: dict) -> str:
        if provider_key == 'vllm_omni':
            return api_key_override or ''
        if provider_key == 'qwen' and api_key_override is not None:
            # Qwen fallback owns its credential explicitly. An empty override
            # must stay empty instead of leaking the failed custom provider key.
            return api_key_override
        return api_key_override or tts_config.get('api_key', '')

    @staticmethod
    def _is_vllm_omni_tts_enabled(core_config: dict) -> bool:
        return _as_bool(core_config.get('ENABLE_CUSTOM_API'), False) and (
            str(core_config.get('ttsModelProvider') or '').strip() == 'vllm_omni'
        )

    @classmethod
    def _resolve_vllm_omni_runtime_config(cls, core_config: dict) -> tuple[str, str, str]:
        if not cls._is_vllm_omni_tts_enabled(core_config):
            return ('', '', '')
        return (
            str(core_config.get('ttsModelUrl') or '').strip()
            or VLLM_OMNI_DEFAULT_BASE_URL,
            str(core_config.get('ttsModelId') or '').strip()
            or VLLM_OMNI_DEFAULT_MODEL,
            str(core_config.get('ttsVoiceId') or '').strip()
            or 'default',
        )

    def _build_tts_runtime_key(self) -> tuple:
        """Return the effective TTS worker identity for ready-state reuse."""
        try:
            core_config = self._config_manager.get_core_config()
            if core_config.get('DISABLE_TTS', False):
                return ("disabled",)
            route_voice_id, has_custom = self._effective_tts_route()
            _, api_key_override, provider_key = _core_facade.get_tts_worker(
                core_api_type=self.core_api_type,
                has_custom_voice=has_custom,
                voice_id=route_voice_id,
                excluded_provider_keys=getattr(
                    self, "_tts_excluded_provider_keys", frozenset()
                ),
            )
            tts_config = self._config_manager.get_model_api_config(
                'tts_custom' if has_custom else 'tts_default'
            )
            api_key = self.resolve_tts_api_key(provider_key, api_key_override, tts_config)
            return (
                provider_key,
                self.core_api_type,
                route_voice_id,
                bool(getattr(self, "_is_free_preset_voice", False)),
                bool(has_custom),
                tts_config.get('base_url', ''),
                tts_config.get('model', ''),
                self._resolve_vllm_omni_runtime_config(core_config),
                api_key,
                tuple(sorted(getattr(self, "_tts_excluded_provider_keys", frozenset()))),
            )
        except Exception:
            return (
                "fallback",
                getattr(self, "core_api_type", ""),
                getattr(self, "voice_id", ""),
                bool(getattr(self, "_is_free_preset_voice", False)),
            )

    def game_speech_audio_cache_identity(
        self,
        clean_text: str,
        *,
        render_language: str = "",
    ) -> tuple[str, str]:
        """Return opaque runtime and utterance hashes for mini-game audio reuse.

        ``render_language`` lets a caller that must not mutate the shared
        session locale (silent preload) key its audio under the locale it was
        asked for. Empty means "use whatever the session currently renders in",
        which is what the interactive speak path wants.
        """
        language = str(
            render_language
            or getattr(self, "_conversation_render_language", "")
            or getattr(self, "_conversation_turn_language", "")
            or getattr(self, "user_language", "")
            or ""
        ).strip()
        runtime_material = (
            "neko-game-speech-audio-v1",
            self._build_tts_runtime_key(),
            language,
        )
        runtime_signature = hashlib.sha256(
            repr(runtime_material).encode("utf-8", errors="strict")
        ).hexdigest()
        cache_key = hashlib.sha256(
            repr((runtime_signature, str(clean_text))).encode("utf-8", errors="strict")
        ).hexdigest()
        return cache_key, runtime_signature

    def current_game_speech_audio_runtime_signature(self) -> str:
        return self.game_speech_audio_cache_identity("")[1]

    def _resolve_tts_worker_spec(self):
        """Resolve the current project TTS worker without mutating runtime state."""
        core_config = self._config_manager.get_core_config()
        route_voice_id = self.voice_id or ""
        if core_config.get("DISABLE_TTS", False):
            return dummy_tts_worker, "", route_voice_id, None, True, {}
        route_voice_id, has_custom = self._effective_tts_route()
        worker, api_key_override, provider_key = _core_facade.get_tts_worker(
            core_api_type=self.core_api_type,
            has_custom_voice=has_custom,
            voice_id=route_voice_id,
            excluded_provider_keys=getattr(
                self, "_tts_excluded_provider_keys", frozenset()
            ),
        )
        tts_config = self._config_manager.get_model_api_config(
            "tts_custom" if has_custom else "tts_default"
        )
        api_key = self.resolve_tts_api_key(
            provider_key, api_key_override, tts_config
        )
        return worker, api_key, route_voice_id, provider_key, False, dict(tts_config or {})

    @staticmethod
    def _normalize_game_speech_preload_text(clean_text: str, *, normalize_spaces: bool) -> str:
        # Gated on contains_chinese exactly like normalize_text() below, which
        # is what the real speak path runs. replace_blank drops every ASCII
        # space whose neighbour is non-ASCII, so applying it unconditionally
        # glued Korean/Cyrillic/Thai words together -- and produced cached audio
        # that differs from what the same line would sound like when spoken.
        text = (
            replace_blank(clean_text)
            if normalize_spaces and contains_chinese(clean_text)
            else clean_text
        )
        markdown = TtsMarkdownStripper()
        bracket = TtsBracketStripper()
        markdown_output = markdown.feed(text) + markdown.flush(keep_symbol_markers=True)
        spoken = bracket.feed(markdown_output)
        bracket.flush()
        # Use the same symbol filter as _enqueue_tts_text_chunk so cached audio matches
        # actual pronunciation of the same sentence.
        return strip_tts_muted_symbols(str(spoken or "")).strip()

    def cancel_game_speech_preloads(self) -> None:
        """Cancel active/queued preload batches and wake their isolated workers."""
        self._game_speech_preload_cancel_epoch = (
            int(getattr(self, "_game_speech_preload_cancel_epoch", 0)) + 1
        )
        workers = getattr(self, "_game_speech_preload_active_workers", {})
        for request_queue in list(workers.values()):
            try:
                request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
            except Exception:
                pass

    async def preflight_game_speech_audio(self, text: str, *, render_language: str = "") -> dict:
        """Validate the current provider even when the probe audio is cached."""
        worker, _key, _voice, provider, disabled, config = self._resolve_tts_worker_spec()
        if disabled:
            return {"ok": False, "reason": "tts_disabled"}
        if not self._tts_worker_supports_completion(worker, provider, config):
            return {"ok": False, "reason": "completion_unavailable"}
        return await self.preload_game_speech_audio([text], render_language=render_language)

    async def preload_game_speech_audio(
        self,
        lines: list[str],
        *,
        render_language: str = "",
    ) -> dict:
        """Silently synthesize bounded mini-game text into the reusable cache.

        The caller passes its request locale instead of writing it onto the
        shared session: this batch runs for tens of seconds behind its own lock,
        and every concurrent speak/preload/UI language change would otherwise
        move the field the cache identity is derived from.
        """
        max_lines = 32
        max_pending_batches = 4
        ready_timeout_seconds = 30.0
        item_timeout_seconds = max(
            0.05,
            min(
                float(
                    getattr(
                        self,
                        "_game_speech_preload_item_timeout_seconds",
                        90.0,
                    )
                ),
                90.0,
            ),
        )
        poll_seconds = 0.01
        unique_lines: list[str] = []
        seen: set[str] = set()
        for value in list(lines or []):
            clean = str(value or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            unique_lines.append(clean)
        if not unique_lines:
            return {"ok": False, "reason": "missing_lines", "results": []}
        if len(unique_lines) > max_lines:
            return {
                "ok": False,
                "reason": "too_many_lines",
                "limit": max_lines,
                "results": [],
            }

        if not hasattr(self, "_game_speech_preload_lock"):
            self._game_speech_preload_lock = asyncio.Lock()
            self._game_speech_preload_pending_batches = 0
            self._game_speech_preload_cancel_epoch = 0
            self._game_speech_preload_active_workers = {}
        pending = int(getattr(self, "_game_speech_preload_pending_batches", 0))
        if pending >= max_pending_batches:
            return {"ok": False, "reason": "busy", "results": []}
        self._game_speech_preload_pending_batches = pending + 1
        epoch = int(self._game_speech_preload_cancel_epoch)

        def summarize(result_map: dict[int, dict], reason: str = "") -> dict:
            ordered = [result_map[index] for index in sorted(result_map)]
            loaded_count = sum(item["status"] == "loaded" for item in ordered)
            hit_count = sum(item["status"] == "hit" for item in ordered)
            response = {
                "ok": all(
                    item["status"] in {"loaded", "hit"} for item in ordered
                ),
                "results": ordered,
                "loaded": loaded_count,
                "hits": hit_count,
                "failed": len(ordered) - loaded_count - hit_count,
            }
            if reason:
                response["reason"] = reason
            return response

        try:
            async with self._game_speech_preload_lock:
                if epoch != self._game_speech_preload_cancel_epoch:
                    raise _GameSpeechPreloadCancelled
                active_workers = self._game_speech_preload_active_workers
                for active_thread in list(active_workers):
                    if not active_thread.is_alive():
                        active_workers.pop(active_thread, None)
                if active_workers:
                    return {
                        "ok": False,
                        "reason": "worker_cleanup_pending",
                        "results": [],
                    }
                results_by_index: dict[int, dict] = {}
                pending_lines: list[tuple[int, str, str, str]] = []
                for index, clean in enumerate(unique_lines):
                    cache_key, runtime_signature = (
                        self.game_speech_audio_cache_identity(
                            clean, render_language=render_language,
                        )
                    )
                    if GAME_SPEECH_AUDIO_CACHE.get(cache_key) is not None:
                        results_by_index[index] = {"index": index, "status": "hit"}
                    else:
                        pending_lines.append(
                            (index, clean, cache_key, runtime_signature)
                        )
                if not pending_lines:
                    return summarize(results_by_index)
                worker, api_key, route_voice_id, provider_key, disabled, tts_config = (
                    self._resolve_tts_worker_spec()
                )
                if disabled:
                    for index, _clean, _key, _signature in pending_lines:
                        results_by_index[index] = {
                            "index": index,
                            "status": "failed",
                            "reason": "tts_disabled",
                        }
                    return summarize(results_by_index, "tts_disabled")
                if not self._tts_worker_supports_completion(
                    worker,
                    provider_key,
                    tts_config,
                ):
                    for index, _clean, _key, _signature in pending_lines:
                        results_by_index[index] = {
                            "index": index,
                            "status": "failed",
                            "reason": "tts_unavailable",
                        }
                    return summarize(results_by_index, "tts_unavailable")

                meta = TTS_PROVIDER_REGISTRY.get(provider_key) if provider_key else None
                normalize_spaces = not meta or meta.category != "ws_bistream"
                request_queue = Queue()
                response_queue = Queue()
                capture_owner = GameSpeechCaptureOwner()
                thread = Thread(
                    target=worker,
                    args=(request_queue, response_queue, api_key, route_voice_id),
                    daemon=True,
                )
                self._game_speech_preload_active_workers[thread] = request_queue
                thread.start()

                async def next_response(timeout_seconds: float):
                    deadline = time.monotonic() + timeout_seconds
                    while True:
                        if epoch != self._game_speech_preload_cancel_epoch:
                            raise _GameSpeechPreloadCancelled
                        try:
                            return response_queue.get_nowait()
                        except Exception:
                            if not thread.is_alive() and response_queue.empty():
                                raise RuntimeError("tts_worker_stopped")
                            if time.monotonic() >= deadline:
                                raise asyncio.TimeoutError
                            await asyncio.sleep(poll_seconds)

                try:
                    ready = False
                    ready_deadline = (
                        time.monotonic()
                        + ready_timeout_seconds
                    )
                    try:
                        while not ready:
                            message = await next_response(
                                max(0.001, ready_deadline - time.monotonic())
                            )
                            if (
                                isinstance(message, tuple)
                                and len(message) == 2
                                and message[0] == "__ready__"
                            ):
                                if message[1] is not True:
                                    for index, _clean, _key, _signature in pending_lines:
                                        results_by_index[index] = {
                                            "index": index,
                                            "status": "failed",
                                            "reason": "tts_unavailable",
                                        }
                                    return summarize(results_by_index, "tts_unavailable")
                                ready = True
                    except asyncio.TimeoutError:
                        for index, _clean, _key, _signature in pending_lines:
                            results_by_index[index] = {
                                "index": index,
                                "status": "failed",
                                "reason": "tts_ready_timeout",
                            }
                        return summarize(results_by_index, "tts_ready_timeout")
                    except RuntimeError:
                        for index, _clean, _key, _signature in pending_lines:
                            results_by_index[index] = {
                                "index": index,
                                "status": "failed",
                                "reason": "tts_unavailable",
                            }
                        return summarize(results_by_index, "tts_unavailable")

                    for position, (
                        index,
                        clean,
                        cache_key,
                        runtime_signature,
                    ) in enumerate(pending_lines):
                        if GAME_SPEECH_AUDIO_CACHE.get(cache_key) is not None:
                            results_by_index[index] = {"index": index, "status": "hit"}
                            continue
                        spoken = self._normalize_game_speech_preload_text(
                            clean, normalize_spaces=normalize_spaces
                        )
                        if not spoken:
                            results_by_index[index] = {
                                "index": index,
                                "status": "failed",
                                "reason": "empty_after_normalization",
                            }
                            continue
                        speech_id = f"game-preload-{hashlib.sha256(cache_key.encode()).hexdigest()[:16]}-{index}"
                        if not GAME_SPEECH_AUDIO_CACHE.begin_capture(
                            capture_owner, speech_id, cache_key, runtime_signature
                        ):
                            results_by_index[index] = {
                                "index": index,
                                "status": "bypass_capacity",
                            }
                            continue
                        request_queue.put((speech_id, spoken))
                        request_queue.put((None, None))
                        loaded = False
                        failure_reason = "tts_incomplete"
                        received_bytes = 0
                        item_deadline = (
                            time.monotonic()
                            + item_timeout_seconds
                        )
                        try:
                            while True:
                                message = await next_response(
                                    max(0.001, item_deadline - time.monotonic())
                                )
                                if (
                                    isinstance(message, tuple)
                                    and len(message) == 3
                                    and message[0] == "__audio__"
                                ):
                                    _, response_speech_id, audio = message
                                    if str(response_speech_id or "") == speech_id:
                                        if isinstance(audio, (bytes, bytearray, memoryview)):
                                            received_bytes += audio.nbytes if isinstance(audio, memoryview) else len(audio)
                                        GAME_SPEECH_AUDIO_CACHE.append_capture(
                                            capture_owner, speech_id, audio
                                        )
                                    continue
                                if isinstance(message, (bytes, bytearray, memoryview)):
                                    received_bytes += message.nbytes if isinstance(message, memoryview) else len(message)
                                    GAME_SPEECH_AUDIO_CACHE.append_capture(
                                        capture_owner, speech_id, message
                                    )
                                    continue
                                if (
                                    isinstance(message, tuple)
                                    and len(message) == 2
                                    and message[0] == TTS_AUDIO_DONE_SENTINEL
                                    and str(message[1] or "") == speech_id
                                ):
                                    loaded = GAME_SPEECH_AUDIO_CACHE.complete_capture(
                                        capture_owner,
                                        speech_id,
                                        # The signature this batch precomputed,
                                        # not a fresh read: one worker is
                                        # resolved for the whole batch with no
                                        # await between the precompute and the
                                        # resolve, so this audio provably came
                                        # from that runtime. Re-reading compares
                                        # against whatever the session renders
                                        # in *now* and throws away audio that
                                        # was already synthesized. (The
                                        # streaming speak path below is
                                        # different: its worker really can be
                                        # restarted mid-utterance, so it must
                                        # re-read.)
                                        runtime_signature,
                                    )
                                    failure_reason = "" if loaded else "tts_incomplete"
                                    if not loaded and received_bytes > GAME_SPEECH_AUDIO_CACHE.max_entry_bytes:
                                        failure_reason = "audio_too_large"
                                    break
                                if (
                                    isinstance(message, tuple)
                                    and len(message) == 2
                                    and message[0] in {"__error__", "__ready__"}
                                ):
                                    failure_reason = "tts_unavailable"
                                    break
                        except asyncio.TimeoutError:
                            failure_reason = "timeout"
                        except RuntimeError:
                            failure_reason = "tts_unavailable"
                        finally:
                            if not loaded:
                                GAME_SPEECH_AUDIO_CACHE.fail_capture(capture_owner, speech_id)
                        results_by_index[index] = (
                            {"index": index, "status": "loaded"}
                            if loaded
                            else {
                                "index": index,
                                "status": "failed",
                                "reason": failure_reason,
                            }
                        )
                        if not loaded and failure_reason in {
                            "timeout",
                            "tts_unavailable",
                        }:
                            # Legacy workers may emit raw, untagged bytes. Once an
                            # item ends without its matching completion, late raw
                            # bytes cannot be distinguished from the next item.
                            # Retire this isolated worker instead of reusing it.
                            for (
                                remaining_index,
                                _remaining_clean,
                                _remaining_key,
                                _remaining_signature,
                            ) in pending_lines[position + 1:]:
                                results_by_index[remaining_index] = {
                                    "index": remaining_index,
                                    "status": "failed",
                                    "reason": "tts_worker_reset_required",
                                }
                            break
                finally:
                    try:
                        request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
                    except Exception:
                        pass
                    try:
                        await asyncio.to_thread(thread.join, 2.0)
                    finally:
                        if not thread.is_alive():
                            self._game_speech_preload_active_workers.pop(thread, None)
                        GAME_SPEECH_AUDIO_CACHE.discard_owner(capture_owner)

                return summarize(results_by_index)
        except _GameSpeechPreloadCancelled:
            # Only our own supersede/teardown signal is absorbed here; a real
            # CancelledError propagates so the request task actually stops.
            return {"ok": False, "reason": "cancelled", "results": []}
        finally:
            self._game_speech_preload_pending_batches = max(
                0,
                int(getattr(self, "_game_speech_preload_pending_batches", 1)) - 1,
            )

    async def _clear_tts_pipeline(self):
        """Clear the TTS request/response queues and pending caches, stopping the current synthesis.

        Gate is on worker liveness, not ``self.use_tts``: mirror channel
        (e.g. ``mirror_assistant_speech``) feeds the project TTS pipeline
        regardless of ``use_tts``, so a Realtime native voice session
        (``use_tts=False``) can still have a live worker that needs
        interrupting on ``interrupt_audio``.

        The two TTS-done flags are NOT reset together, because they are not
        paired with the same thing — see the comments at each reset.

        Three steps, for a caller that must tell the frontend in between (a
        final discard: no audio of the discarded reply may arrive after its
        notice): ``_interrupt_tts_now`` (synchronous),
        ``_let_tts_interrupt_land`` (audio a handler already held goes out,
        what leaked meanwhile is dropped) and ``_finish_tts_clear``.
        """
        interrupt = self._interrupt_tts_now()
        await self._let_tts_interrupt_land(interrupt)
        await self._finish_tts_clear(interrupt)

    @staticmethod
    def _drain_tts_responses(response_queue) -> None:
        # A cancelled handler still owns its executor's blocking get().
        # Discard audio, but return its wakeups after draining the queue.
        wakeups = []
        while not response_queue.empty():
            try:
                item = response_queue.get_nowait()
            except Exception:
                break
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "__handler_exit__":
                wakeups.append(item)
        for item in wakeups:
            response_queue.put_nowait(item)

    def _interrupt_tts_now(self):
        """The synchronous first half of ``_clear_tts_pipeline``: interrupt
        the worker and drop the audio already queued for the frontend.

        Returns what ``_finish_tts_clear`` needs, or None when this output is
        not current (nothing to clear).
        """
        # ``_tts_done_queued_for_turn`` pairs with ``__interrupt__`` below: enqueueing invalidates
        # previous turn's done sentinel, and this flag alone records that this turn's sentinel is queued.
        # Therefore reset as part of interrupt, before the first await
        # (sleep in _finish_tts_clear). This function is synchronous, so cancellation cannot
        # intervene, making both atomic. After-await clearing (historically each caller cleared after
        # await returned) leaves worker-interrupted / bookkeeping-still-queued residue if cancellation arrives at sleep,
        # causing next turn's ``_request_tts_done_locked`` to
        # return 'already' early, preventing flush sentinel enqueue forever; exactly
        # what handle_new_message's two reset lines intended to prevent.
        #
        # ``_tts_done_pending_until_ready`` must not move earlier with it: it pairs with
        # ``tts_pending_chunks`` ('text not flushed yet; replay done afterward'),
        # both cleared in tts_cache_lock at _finish_tts_clear's end. Moving only flag
        # while retaining chunks allows sleep cancellation to leave the opposite partial state:
        # when ready, ``_flush_tts_pending_chunks`` requeues stale chunks,
        # but skips done replay because flag is False, leaving synthesis unflushed.
        # Therefore keep it in the latter section. Preserve caller resets after _clear_tts_pipeline returns:
        # they guard concurrent True assignment during sleep, separate from this
        # cancellation residue.
        runtime = self._snapshot_tts_runtime()
        request_queue = self.tts_request_queue
        response_queue = self.tts_response_queue
        pending_chunks = self.tts_pending_chunks
        session = getattr(self, "session", None)
        speech_id = getattr(self, "current_speech_id", None)
        # A retired installed runtime can still own fallback replay. Clear
        # that conversation output, but never accept a stale worker callback.
        if not self._tts_output_is_current():
            return None

        self._tts_done_queued_for_turn = False
        # Interruption invalidates everything this turn, including not-yet-due idle soft flush; cancel synchronously before
        # first await too, atomically with __interrupt__ enqueue.
        self._cancel_tts_soft_flush()
        self._cancel_game_speech_completion_wait()
        self._clear_game_speech_correlation()
        GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
        interrupted = False
        if self._tts_runtime_is_current(runtime) and self.tts_thread and self.tts_thread.is_alive():
            self._drain_tts_responses(response_queue)
            try:
                request_queue.put(("__interrupt__", None))
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS中断信号失败: {e}")
            self._reset_tts_stream_normalizer()
            interrupted = True
        return runtime, response_queue, pending_chunks, session, speech_id, interrupted

    async def _let_tts_interrupt_land(self, interrupt, still_owned=None) -> None:
        """Wait for an interrupt from ``_interrupt_tts_now`` to land, then drop
        the audio the old synthesizer leaked meanwhile.

        A response handler that had already taken an audio item off the queue
        sends it in this wait, so it reaches the frontend ahead of anything
        sent after this returns. ``still_owned``, when given, is rechecked
        before the drop: a caller taken over in the wait leaves the queue to
        its taker.
        """
        if interrupt is None or not interrupt[-1]:
            return
        # Wait for TTS worker to process __interrupt__ and mute callbacks (worker polls ~10ms).
        # Then clear response queue again to discard all audio leaked by old synthesizer.
        await asyncio.sleep(0.02)
        if still_owned is None or still_owned():
            self._drain_tts_responses(interrupt[1])

    async def _finish_tts_clear(self, interrupt) -> None:
        """The last step of ``_clear_tts_pipeline``, given what
        ``_interrupt_tts_now`` returned: the pending caches."""
        if interrupt is None:
            return
        runtime, response_queue, pending_chunks, session, speech_id, _interrupted = interrupt
        async with self.tts_cache_lock:
            owns_queues = (runtime is getattr(self, "_tts_runtime", None)
                           and response_queue is self.tts_response_queue)
            owns_transferred_replay = (
                runtime is not None and runtime.retired
                and self.tts_pending_chunks is pending_chunks
                and getattr(self, "session", None) is session
                and getattr(self, "current_speech_id", None) == speech_id
            )
            # Fallback can install its successor while this interrupt waits
            # for the cache lock. Follow only the unchanged turn's transferred
            # replay list; a replacement cache still belongs to its new owner.
            if (not (owns_queues or owns_transferred_replay)
                    or not self._tts_output_is_current()):
                return
            self.tts_pending_chunks.clear()
            # Clear queued again: during the 20ms above, concurrent paths (finish_proactive_delivery,
            # etc.) may see False, enqueue their own sentinel, and set it True. That
            # sentinel is after __interrupt__ but before this turn's subsequent text and is already invalid;
            # leaving bookkeeping True makes later text's done request return 'already' early,
            # preventing this retry turn's final sentinel. handle_new_message and related paths use callers
            # post-return resets to guard this, unlike discard/takeover paths; centralize the fallback
            # here so all five callers get the same guarantee.
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            self._reset_tts_replay_state()
            # Drop only queued-but-unconfirmed TTS text. Already-confirmed
            # audio may still be echoed by STT shortly after an interrupt.
            self._discard_pending_ai_voice_echo()

    @property
    def is_tts_pipeline_ready(self) -> bool:
        """Light health check: TTS worker thread alive and ready, no orchestration."""
        return bool(
            self.tts_thread
            and self.tts_thread.is_alive()
            and self.tts_ready
        )

    async def _stop_tts_response_handler(self, *, deadline=None) -> None:
        """Stop the handler bound to the current response queue.

        ``tts_response_handler`` captures ``tts_response_queue`` when its task
        starts. Replacing a worker also replaces that queue, so the old handler
        must be cancelled before a replacement worker/handler pair is created.
        """
        handler_task = self.tts_handler_task
        handler_queue = getattr(self, "_tts_handler_response_queue", None)
        runtime = self._snapshot_tts_runtime()
        if handler_task is not None and not handler_task.done():
            if handler_queue is self.tts_response_queue:
                GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
            if not handler_task.cancelling():
                handler_task.cancel()
            timeout = (
                TTS_FRAME_WRITE_TIMEOUT_SECONDS
                + TTS_SOCKET_CLOSE_TIMEOUT_SECONDS
                + TTS_HANDLER_CANCEL_GRACE_SECONDS
            )
            if deadline is not None:
                timeout = min(timeout, max(0, deadline - asyncio.get_running_loop().time()))
            try:
                await asyncio.wait_for(
                    asyncio.shield(handler_task),
                    timeout=timeout,
                )
            except asyncio.CancelledError:
                if not handler_task.done() or asyncio.current_task().cancelling():
                    raise
            except asyncio.TimeoutError:
                if runtime is not None and runtime.handler is handler_task:
                    # Fence output and retain physical cleanup after the
                    # optional startup wait has exhausted its own budget.
                    self._retire_tts_runtime(runtime)
                    if self.tts_handler_task is handler_task:
                        self.tts_handler_task = None
                        if getattr(self, "_tts_handler_response_queue", None) is handler_queue:
                            self._tts_handler_response_queue = None
                raise TimeoutError("TTS response handler did not stop before handoff")
        if self.tts_handler_task is handler_task:
            self.tts_handler_task = None
            if getattr(self, "_tts_handler_response_queue", None) is handler_queue:
                self._tts_handler_response_queue = None

    def _start_tts_response_handler(self):
        """Start one handler and bind its ownership to the captured response queue."""
        runtime = self._snapshot_tts_runtime()
        if not self._tts_runtime_is_current(runtime):
            raise RuntimeError("Cannot start a handler for retired TTS runtime")
        token = tts_output_runtime.set(runtime)
        try:
            task = asyncio.create_task(self.tts_response_handler())
        finally:
            tts_output_runtime.reset(token)
        self.tts_handler_task = task
        self._tts_handler_response_queue = self.tts_response_queue
        if runtime is not None:
            runtime.handler = task
        return task

    async def ensure_tts_pipeline_alive(self, *, deadline=None) -> None:
        """Light TTS startup helper: spawn worker + handler task if not alive.

        Does NOT wait for ``__ready__`` — callers that need confirmed-ready
        must poll :attr:`is_tts_pipeline_ready` themselves.  Callers that
        only need ``tts_pending_chunks`` to eventually drain do not need
        to wait at all (the handler picks up pending chunks once
        ``tts_ready`` flips).
        """
        runtime = self._snapshot_tts_runtime()
        if not (self.tts_thread and self.tts_thread.is_alive()
                and self._tts_runtime_is_current(runtime)):
            # A live handler can still be blocked on the response queue owned
            # by a worker that was shut down for native Realtime voice. It must
            # not survive across the fresh queues created by _start_tts_thread.
            current_request = getattr(self, "_current_start_request", None)
            operation = current_request() if current_request else None
            recovering = deadline is None and operation is None
            if recovering and runtime is not None:
                # A turn must not wait on retired workers. Retirement retains
                # the old handler; its fenced output cannot reach fresh queues.
                self._retire_tts_runtime(runtime)
                if self.tts_handler_task is runtime.handler:
                    self.tts_handler_task = None
                    self._tts_handler_response_queue = None
            else:
                await self._stop_tts_response_handler(deadline=deadline)
            check = getattr(self, "_check_start_operation", None)
            if recovering:
                check = None
            if check:
                check()
            worker = self._resolve_tts_worker_spec()[0] if hasattr(self, "_config_manager") else None
            try:
                await self._wait_tts_capacity(
                    asyncio.get_running_loop().time() if recovering else deadline,
                    worker=worker,
                )
            except TtsCapacityError:
                if not recovering:
                    raise
                self.tts_ready = False
                self._schedule_tts_capacity_recovery()
                return
            if check:
                check()
            # Another lazy startup may have installed a healthy worker while
            # this caller was waiting for the handler or a capacity slot.
            runtime = self._snapshot_tts_runtime()
            if self.tts_thread and self.tts_thread.is_alive() and self._tts_runtime_is_current(runtime):
                if self.tts_handler_task is None or self.tts_handler_task.done():
                    self._start_tts_response_handler()
                return
            try:
                self._start_tts_thread(
                    preserve_provider_exclusions=bool(
                        getattr(self, "_tts_excluded_provider_keys", frozenset())
                    )
                )
            except TtsCapacityError:
                if not recovering:
                    raise
                self.tts_ready = False
                self._schedule_tts_capacity_recovery()
                return
        if self.tts_handler_task is None or self.tts_handler_task.done():
            self._start_tts_response_handler()

    async def _apply_pending_tts_route_after_swap(self) -> None:
        """Apply pending TTS route and reconcile worker state after hot-swap."""
        if self.pending_use_tts is None:
            return
        self.use_tts = self.pending_use_tts
        if self.use_tts:
            await self.ensure_tts_pipeline_alive()

    def _has_custom_tts(self) -> bool:
        """Decide whether the current session uses custom TTS (a cloned voice or a custom TTS URL)."""
        core_config = self._config_manager.get_core_config()
        _, uses_provider_native_voice = resolve_native_voice_for_routing(
            self.core_api_type,
            self.voice_id,
            self._config_manager.voice_id_exists_in_any_storage,
            realtime_base_url=self._realtime_base_url(),
        )
        if uses_provider_native_voice:
            return False
        gsv_voice_id = str(core_config.get('TTS_VOICE_ID') or '')
        gsv_enabled = (
            _as_bool(core_config.get('GPTSOVITS_ENABLED'), False)
            and not is_gsv_disabled_voice_id(gsv_voice_id)
        )
        if gsv_enabled:
            return True
        # Cloned voices always use the custom path.
        if bool(self.voice_id) and not self._is_free_preset_voice:
            return True
        return False

    def _effective_tts_route(self) -> tuple[str, bool]:
        """Return the voice identity and custom flag used for worker dispatch."""
        # Recovery keeps character storage unchanged but strips the failed preset
        # from the replacement worker's routing and credential context.
        # Fallback clears only replacement-worker routing context, without changing the voice saved in character card.
        if getattr(self, "_tts_fallback_uses_default_voice", False):
            return "", False
        return self.voice_id or "", self._has_custom_tts()

    def _tts_worker_supports_completion(
        self,
        tts_worker,
        provider_key: str | None,
        tts_config: dict | None,
    ) -> bool:
        if tts_worker is dummy_tts_worker:
            return False
        if provider_key != "local_cosyvoice":
            return True
        local_base_url = str((tts_config or {}).get("base_url") or "").strip().lower()
        return local_base_url.startswith(("ws://", "wss://"))

    def _start_tts_thread(self, *, preserve_provider_exclusions: bool = False):
        """Create and start the TTS worker thread.

        Selects the worker by voice_id / core_api_type, resolves the api_key,
        creates fresh request/response Queues and starts the daemon thread.
        tts_ready is reset to False around the call; the new worker must send
        __ready__ again.
        """
        check = getattr(self, "_check_start_operation", None)
        if check:
            check()
        # Admission before retirement only enforces the total resource cap.
        # Exclusive workers must first receive shutdown, then pass the
        # worker-specific check below after their physical exit.
        if self._live_tts_runtime_count() >= MAX_LIVE_TTS_RUNTIMES:
            self._tts_capacity_exhausted = True
            raise TtsCapacityError("TTS worker capacity is still occupied")
        old_runtime = self._snapshot_tts_runtime()
        if old_runtime is not None and not old_runtime.retired:
            self._retire_tts_runtime(old_runtime)
        self._tts_capacity_exhausted = False
        # Reset readiness; new worker requires a fresh handshake.
        self.tts_ready = False
        self._tts_runtime_key = None
        if not preserve_provider_exclusions:
            self._tts_excluded_provider_keys = frozenset()
            self._tts_fallback_uses_default_voice = False

        # Check whether TTS is disabled.
        tts_worker, api_key, route_voice_id, provider_key, disabled, tts_config = (
            self._resolve_tts_worker_spec()
        )
        if self._live_tts_runtime_count() >= self._tts_capacity_limit(tts_worker):
            self._tts_capacity_exhausted = True
            raise TtsCapacityError("TTS worker requires prior runtime to exit")
        if disabled:
            logger.info("TTS 已被用户禁用, 使用 dummy worker")
        self._tts_completion_supported = self._tts_worker_supports_completion(
            tts_worker,
            provider_key,
            tts_config,
        )
        self._tts_audio_output_supported = self._tts_completion_supported

        # Enable streaming text normalization according to actual selected TTS provider class.
        # ws_bistream (qwen / step / cosyvoice) sends fragments directly for server processing;
        # normalizer pending_spaces delays and CJK-boundary space removal would disrupt delivery cadence.
        # http_sentence (cogtts / gemini / openai / minimax) splits sentences client-side
        # and needs clean text, making normalization useful.
        # Note: 'free' is absent from registry → meta None → fallthrough enables normalizer,
        # because overseas free uses Gemini and needs CJK space cleanup.
        meta = TTS_PROVIDER_REGISTRY.get(provider_key) if provider_key else None
        self._tts_normalize_enabled = not meta or meta.category != "ws_bistream"
        # Send text-idle soft flush only to workers recognizing this sentinel (capability in registry).
        self._tts_soft_flush_supported = bool(meta and meta.soft_flush)
        self._cancel_tts_soft_flush()
        self._tts_replay_progress_supported = bool(
            meta and meta.category == "http_sentence"
        )

        self.tts_request_queue = Queue()
        self.tts_response_queue = Queue()

        self.tts_thread = Thread(
            target=tts_worker,
            args=(self.tts_request_queue, self.tts_response_queue, api_key, route_voice_id),
            daemon=True,
        )
        self._tts_active_provider_key = provider_key
        self._tts_runtime_key = self._build_tts_runtime_key()
        runtime = TtsRuntimeRecord(
            self.tts_thread, self.tts_request_queue, self.tts_response_queue,
            supports_runtime_overlap=bool(getattr(tts_worker, "supports_runtime_overlap", True)),
        )
        self._tts_runtimes.append(runtime)
        self._tts_runtime = runtime
        try:
            self.tts_thread.start()
        except BaseException:
            self._retire_tts_runtime(runtime)
            raise

    def _activate_configured_tts_fallback(self, failure_stage: str) -> bool:
        """Exclude a failed configured provider and restart with existing order."""
        if not self._tts_output_is_current():
            return False
        failed_provider = getattr(self, "_tts_active_provider_key", None)
        if not _core_facade.tts_provider_falls_back_on_failure(failed_provider):
            return False

        excluded = frozenset(getattr(self, "_tts_excluded_provider_keys", frozenset()))
        if failed_provider in excluded:
            return False

        if self._live_tts_runtime_count() >= MAX_LIVE_TTS_RUNTIMES:
            self._tts_capacity_exhausted = True
            raise TtsCapacityError("TTS fallback cannot exceed live worker capacity")

        # The ledger is authoritative after response.done: some realtime paths
        # rotate ``current_speech_id`` before trailing TTS audio has finished.
        # current_speech_id may rotate before response stream ends, while old worker's final audio is still processing;
        # prefer ledger speech_id to avoid missing entire-turn replay on finalization failure.
        active_speech_id = getattr(self, "_tts_replay_speech_id", None)
        if active_speech_id is None:
            active_speech_id = getattr(self, "current_speech_id", None)

        # Only replay the active utterance. A new turn can be cached while a late
        # error from the superseded worker is still arriving on the old queue.
        # Replay current turn only; late old-worker errors must not inject previous-turn text into new replies.
        replay_chunks = [
            item
            for item in getattr(self, "_tts_replay_chunks", ())
            if active_speech_id is None or item[0] == active_speech_id
        ]
        pending_chunks = [
            item
            for item in getattr(self, "tts_pending_chunks", ())
            if active_speech_id is None or item[0] == active_speech_id
        ]
        replay_done = bool(
            getattr(self, "_tts_replay_done", False)
            or getattr(self, "_tts_done_queued_for_turn", False)
            or getattr(self, "_tts_done_pending_until_ready", False)
        )
        audio_emitted = bool(getattr(self, "_tts_replay_audio_emitted", False))
        if audio_emitted:
            if getattr(self, "_tts_replay_progress_supported", False):
                # HTTP sentence workers acknowledge per-sentence progress: remove played sentences from ledger; if a failed sentence
                # emitted partial audio, skip only that sentence and send remaining complete ones to fallback provider.
                replay_chunks = [
                    item
                    for item in getattr(self, "_tts_replay_sent_chunks", ())
                    if active_speech_id is None or item[0] == active_speech_id
                ]
                replay_done = bool((replay_chunks or pending_chunks) and replay_done)
                logger.warning(
                    "自定义 TTS API 已输出部分音频，仅重放未确认的后续文本"
                )
            else:
                # WS bidirectional protocol has no text-to-audio position acknowledgements, so exact suffix cannot be safely inferred.
                replay_chunks = []
                replay_done = bool(pending_chunks and replay_done)
                logger.warning(
                    "自定义 TTS API 已输出部分音频，跳过当前句重放以避免重复播报"
                )

        # Excluding one failed provider lets get_tts_worker reuse every existing
        # fallback rule below it; no fallback provider or priority is hardcoded.
        # Exclude only failures, then call existing dispatcher; add/reorder no fallback options.
        self._tts_excluded_provider_keys = excluded | {failed_provider}
        if _core_facade.tts_provider_uses_configured_preset_voice(failed_provider):
            self._tts_fallback_uses_default_voice = True
        # Replacement worker does not inherit failed provider's error codes or notification counts.
        self._last_tts_error_code = ''
        self._tts_retry_notify_count = 0
        old_runtime = self._snapshot_tts_runtime()
        if old_runtime is not None:
            self._retire_tts_runtime(old_runtime, stop_handler=False)
        else:
            self.tts_request_queue.put(("__shutdown__", None))

        logger.warning(
            "自定义 TTS API provider=%s 在%s阶段失败；开始按既有顺序选择保底 provider",
            failed_provider,
            failure_stage,
        )
        # Once ready, rebuild this turn from original text; never reuse failed preset's
        # voice ID/credentials. Character config stays unchanged; next session may retry the provider.
        self.tts_pending_chunks = replay_chunks + pending_chunks
        self._reset_tts_replay_state()
        self._tts_done_queued_for_turn = False
        self._tts_done_pending_until_ready = replay_done
        self._reset_tts_stream_normalizer()
        self._start_tts_thread(preserve_provider_exclusions=True)
        logger.warning(
            "自定义 TTS API 已回退: failed_provider=%s fallback_provider=%s",
            failed_provider,
            self._tts_active_provider_key or "default",
        )
        return True

    async def _activate_configured_tts_fallback_after_capacity(
        self, failure_stage: str, runtime: TtsRuntimeRecord | None,
        *, fallback_prepared: bool = False,
    ) -> bool:
        """Wait for real worker exits before retrying the existing fallback."""
        expected_session = getattr(self, "session", None)
        expected_use_tts = getattr(self, "use_tts", None)
        deadline_getter = getattr(self, "_current_start_deadline", None)
        deadline = deadline_getter() if callable(deadline_getter) else None
        loop = asyncio.get_running_loop()
        deadline = min(deadline or float("inf"), loop.time() + 15.0)
        for _ in range(3):
            try:
                if fallback_prepared:
                    self._start_tts_thread(preserve_provider_exclusions=True)
                    return True
                return self._activate_configured_tts_fallback(failure_stage)
            except TtsCapacityError:
                if (
                    getattr(self, "session", None) is not expected_session
                    or getattr(self, "use_tts", None) != expected_use_tts
                ):
                    return False
                if self._tts_runtime_is_current(runtime):
                    # The first capacity check has not touched the provider or
                    # replay ledger; retry the complete fallback after cleanup.
                    waiting_for_retirement = False
                elif (
                    runtime is not None and runtime.retired
                    and getattr(self, "_tts_runtime", None) is runtime
                    and getattr(self, "tts_thread", None) is runtime.thread
                    and tts_output_runtime.get() is runtime
                ):
                    # A non-overlapping replacement may reject the old worker
                    # after fallback has already retired it and saved replay.
                    waiting_for_retirement = True
                else:
                    return False
                blocked = tuple(
                    record for record in self._tts_runtimes
                    if record.retired and record.thread is not None
                    and record.thread.is_alive()
                )
                cleanup_tasks = tuple(
                    task for record in blocked
                    if (task := self._schedule_tts_cleanup(record)) is not None
                )
                if not cleanup_tasks:
                    raise
                self._tts_capacity_exhausted = False
                # Retirement can precede replacement admission. Keep the
                # waiting handler's ownership visible to startup polling.
                fallback_task = asyncio.current_task()
                if runtime is not None:
                    runtime.fallback_task = fallback_task
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            *(asyncio.shield(task) for task in cleanup_tasks),
                            return_exceptions=True,
                        ),
                        timeout=max(0, deadline - loop.time()),
                    )
                except asyncio.TimeoutError as error:
                    if (
                        getattr(self, "session", None) is not expected_session
                        or getattr(self, "use_tts", None) != expected_use_tts
                        or getattr(self, "_tts_runtime", None) is not runtime
                        or (runtime is not None and
                            getattr(self, "tts_thread", None) is not runtime.thread)
                    ):
                        return False
                    self.tts_ready = False
                    # Bound this handler's wait, while retaining fallback/replay
                    # ownership for capacity released after its deadline.
                    self._schedule_tts_capacity_recovery(
                        fallback_stage=failure_stage,
                        fallback_prepared=waiting_for_retirement,
                    )
                    raise TtsCapacityError("TTS fallback capacity did not clear") from error
                finally:
                    if runtime is not None and runtime.fallback_task is fallback_task:
                        runtime.fallback_task = None
                if (
                    getattr(self, "session", None) is not expected_session
                    or getattr(self, "use_tts", None) != expected_use_tts
                    or getattr(self, "_tts_runtime", None) is not runtime
                    or (waiting_for_retirement and
                        getattr(self, "tts_thread", None) is not runtime.thread)
                    or (not waiting_for_retirement and
                        not self._tts_runtime_is_current(runtime))
                ):
                    return False
                fallback_prepared = waiting_for_retirement
        raise TtsCapacityError("TTS fallback capacity remained occupied")

    def _reset_tts_retry_state(self):
        """Cancel pending TTS respawn task and clear error/cooldown state.

        Safe to call whether or not a session is active.  When called from
        within an ``async with self.lock`` block the cancellation of
        ``_tts_respawn_task`` is race-free; outside the lock the worst case
        is a harmless double-cancel.
        """
        if self._tts_respawn_task and not self._tts_respawn_task.done():
            self._tts_respawn_task.cancel()
            self._tts_respawn_task = None
        self._last_tts_error_code = ''
        self._last_tts_respawn_time = 0.0
        self._tts_retry_notify_count = 0
        self._tts_rate_limit_backoff_level = 0
        self._tts_rate_limit_retry_at = 0.0
        self._tts_quota_blocked = False
        self._tts_quota_stale_speech_ids = None
        self._tts_quota_from_server_close = False
        notified_error_keys = getattr(self, "_tts_notified_error_keys", None)
        if notified_error_keys is not None:
            notified_error_keys.clear()
        self._tts_done_queued_for_turn = False
        self._cancel_tts_soft_flush()
        self._tts_fallback_uses_default_voice = False
        self._reset_tts_replay_state()
        self._tts_done_pending_until_ready = False
        self._tts_active_provider_key = None
        self._tts_excluded_provider_keys = frozenset()

    async def _teardown_tts_runtime(self, handler_task_ref, thread_ref,
                                     req_queue_ref, resp_queue_ref):
        """Retire only the captured resources; caller cancellation leaves cleanup owned."""
        self._init_tts_lifecycle_state()
        runtime = next((record for record in self._tts_runtimes
                        if record.thread is thread_ref
                        and record.request_queue is req_queue_ref
                        and record.response_queue is resp_queue_ref), None)
        if runtime is None:
            runtime = TtsRuntimeRecord(thread_ref, req_queue_ref, resp_queue_ref)
            runtime.handler = handler_task_ref
            self._tts_runtimes.append(runtime)
        elif runtime.handler is None:
            runtime.handler = handler_task_ref
        self._retire_tts_runtime(runtime)
        if (req_queue_ref is getattr(self, "tts_request_queue", None)
                and resp_queue_ref is getattr(self, "tts_response_queue", None)):
            # Direct teardown still owns this runtime's game completion slot.
            # Do this before suspending; a retired runtime's later thread exit
            # must never clear a replacement's completion or correlation.
            self._cancel_game_speech_completion_wait()
            self._clear_game_speech_correlation()
        # The timeout bounds this caller, not the owned worker lifetime. A live
        # timed-out thread stays registered and continues consuming its slot.
        try:
            await asyncio.wait_for(asyncio.shield(runtime.cleanup_task), 2.0)
        except asyncio.TimeoutError:
            logger.warning("TTS worker cleanup remains registered after timeout")

    def _respawn_tts_worker(self, *, timed: bool = False):
        """Respawn the TTS worker when its thread is detected dead, without blocking for readiness.

        Once the new worker is ready it sends the __ready__ signal through
        response_queue; tts_response_handler receives it and calls
        _flush_tts_pending_chunks to flush the cache.

        Rate limit: at most one respawn per 12 seconds, avoiding a reconnect
        storm when the service is completely down. After a server rate-limit
        rejection no respawn starts before the backoff deadline recorded at
        the rejection, so per-reply respawns cannot outpace the timed backoff.
        ``timed`` marks the scheduled backoff respawn itself, which alone gets
        a small tolerance for asyncio timers waking a clock tick early.
        """
        if not self._tts_output_is_current() or getattr(self, "_tts_capacity_exhausted", False):
            return
        runtime = self._snapshot_tts_runtime()
        # A retired worker still owns capacity, but no longer serves this
        # session. The capacity gate below decides whether a replacement fits.
        if self.tts_thread and self.tts_thread.is_alive() and not (runtime and runtime.retired):
            return

        # Skip respawn directly if last error must not retry automatically.
        if self._last_tts_error_code in NO_RETRY_TTS_CODES:
            logger.warning(f"⚠️ _respawn_tts_worker: 上次错误为 {self._last_tts_error_code}，跳过自动重试")
            return

        import time
        now = time.monotonic()
        if now - self._last_tts_respawn_time < 12.0:
            return  # Cooling down: preserve pending delayed task and error code state.
        deadline_slack = TTS_RATE_LIMIT_DEADLINE_SLACK_SECONDS if timed else 0.0
        if (
            self._last_tts_error_code == 'API_RATE_LIMIT'
            and now + deadline_slack < getattr(self, '_tts_rate_limit_retry_at', 0.0)
        ):
            return  # Rate-limit backoff: deadline begins at rejection; scheduled respawn starts at expiry.

        if (
            getattr(self, '_tts_quota_blocked', False)
            and getattr(self, '_tts_quota_stale_speech_ids', None) is None
            and self.tts_pending_chunks
        ):
            # Callers buffer before spawning, so latest entry is the reply triggering this retry;
            # other cached speech_ids are residual tails from quota-rejected turns. Classify old turns once
            # per quota interception, at first implicit retry. If that retry's worker fails for another reason,
            # later scheduled/implicit retries must not reclassify using newer queued speech as trigger,
            # or the original recovery reply would be deleted as an old turn.
            trigger_speech_id = self.tts_pending_chunks[-1][0]
            self._tts_quota_stale_speech_ids = frozenset(
                speech_id for speech_id, _text in self.tts_pending_chunks
                if speech_id != trigger_speech_id
            )

        # After cooldown passes, cancel any delayed retry still waiting since direct respawn is underway.
        if self._tts_respawn_task and not self._tts_respawn_task.done():
            # A delayed retry may be the caller. Never cancel that task from
            # inside its own respawn attempt.
            if self._tts_respawn_task is not asyncio.current_task():
                self._tts_respawn_task.cancel()
            self._tts_respawn_task = None
        self._last_tts_respawn_time = now
        # Error codes describe only the previous worker. If new worker reports not-ready without __error__
        # (e.g. handshake timeout), stale quota/rate-limit codes would wrongly stop scheduled retry or extend backoff.
        self._last_tts_error_code = ''
        self._tts_quota_from_server_close = False

        logger.info("🔄 TTS Worker 已死亡，尝试重新拉起...")
        try:
            self._start_tts_thread(
                preserve_provider_exclusions=bool(
                    getattr(self, "_tts_excluded_provider_keys", frozenset())
                )
            )
        except TtsCapacityError as error:
            # Capacity is a normal transient state while a retired worker is
            # still draining.  Respawn is called from provider callbacks, so
            # letting this escape would abort the current text delivery.  The
            # startup path owns the terminal capacity flag; this lazy retry
            # path must clear it or the delayed retry would return immediately.
            self._tts_capacity_exhausted = False
            logger.warning("TTS respawn deferred: %s", error)
            self._schedule_tts_capacity_recovery()
            return

        # Restart tts_response_handler to listen on new queue.
        if self.tts_handler_task and not self.tts_handler_task.done():
            self.tts_handler_task.cancel()
        self._start_tts_response_handler()

        logger.info("🔄 TTS Worker 已重新拉起，等待运行时就绪信号...")

    def _schedule_tts_capacity_recovery(self, *, fallback_stage=None, fallback_prepared=False):
        """Retry the captured session once its retired workers physically exit."""
        import time
        pending = getattr(self, "_tts_respawn_task", None)
        if (pending is not None and not pending.done()
                and pending is not asyncio.current_task()):
            if fallback_stage is None:
                return
            # The fallback owns provider/replay state that a plain retry cannot
            # resume. Replace only the reservation, never its cleanup tasks.
            pending.cancel()
        blocked = tuple(
            record for record in self._tts_runtimes
            if record.retired and record.thread is not None
            and record.thread.is_alive()
        )
        cleanup_tasks = tuple(
            task for record in blocked
            if (task := self._schedule_tts_cleanup(record)) is not None
        )
        # Capacity may have cleared between the handler timeout and this snapshot.
        # Fallback still needs a consumer even when there is nothing left to await.
        if not cleanup_tasks and fallback_stage is None:
            return
        expected_session = getattr(self, "session", None)
        expected_use_tts = getattr(self, "use_tts", None)
        expected_runtime = getattr(self, "_tts_runtime", None)
        expected_thread = getattr(self, "tts_thread", None)

        async def retry_after_retirement():
            try:
                # Cancellation of this retry must not cancel manager-owned
                # cleanup or release capacity before the worker exits.
                pending_cleanup = set(cleanup_tasks)
                while pending_cleanup:
                    worker = self._resolve_tts_worker_spec()[0] if hasattr(self, "_config_manager") else None
                    if self._live_tts_runtime_count() < self._tts_capacity_limit(worker):
                        break
                    _, pending_cleanup = await asyncio.wait(
                        pending_cleanup, return_when=asyncio.FIRST_COMPLETED,
                    )
                remaining = 12.0 - (time.monotonic() - getattr(self, "_last_tts_respawn_time", 0.0))
                if remaining > 0:
                    await asyncio.sleep(remaining)
                if (
                    asyncio.current_task() is not self._tts_respawn_task
                    or getattr(self, "session", None) is not expected_session
                    or getattr(self, "use_tts", None) != expected_use_tts
                    or getattr(self, "_tts_runtime", None) is not expected_runtime
                    or getattr(self, "tts_thread", None) is not expected_thread
                    or not getattr(self, "is_active", True)
                    or self.tts_ready
                ):
                    return
                # The provider callback had the retired runtime in its
                # context; this new attempt belongs to the current owner.
                token = tts_output_runtime.set(expected_runtime if fallback_stage is not None else None)
                try:
                    if fallback_stage is None:
                        self._respawn_tts_worker()
                    elif await self._activate_configured_tts_fallback_after_capacity(
                        fallback_stage, expected_runtime,
                        fallback_prepared=fallback_prepared,
                    ):
                        self._start_tts_response_handler()
                except TtsCapacityError as error:
                    # A second admission deadline may transfer the reservation
                    # to another owned retry while physical cleanup continues.
                    logger.warning("TTS fallback recovery deferred: %s", error)
                finally:
                    tts_output_runtime.reset(token)
            finally:
                if asyncio.current_task() is self._tts_respawn_task:
                    self._tts_respawn_task = None

        self._tts_respawn_task = asyncio.create_task(retry_after_retirement())
        return

    async def _flush_tts_pending_chunks(self):
        """Send the cached TTS text chunks to the TTS queue"""
        runtime = self._snapshot_tts_runtime()
        response_queue = getattr(self, "tts_response_queue", None)
        async with self.tts_cache_lock:
            if (not self._tts_runtime_is_current(runtime)
                    or not self._tts_output_is_current()
                    or response_queue is not getattr(self, "tts_response_queue", None)):
                return
            if self.tts_pending_chunks:
                chunk_count = len(self.tts_pending_chunks)
                logger.info(f"TTS就绪，开始处理缓存的 {chunk_count} 个文本chunk...")

                if self.tts_thread and self.tts_thread.is_alive():
                    for speech_id, text in self.tts_pending_chunks:
                        try:
                            self._enqueue_tts_text_chunk(speech_id, text)
                        except Exception as e:
                            logger.error(f"💥 发送缓存的TTS请求失败: {e}")
                            break

                # Clear cache.
                self.tts_pending_chunks.clear()

            if self._tts_done_pending_until_ready:
                status = self._request_tts_done_locked()
                if status == "queued":
                    logger.debug("_flush_tts_pending_chunks: pending 文本已刷出，补发 TTS done 信号")

    
    def _resolve_session_use_tts(
        self,
        input_mode: str,
        realtime_config: dict,
        core_config_snapshot: dict,
        *,
        log_prefix: str = "",
    ) -> bool:
        """Resolve whether this session should use the external TTS pipeline."""
        has_custom_tts_config = (
            bool(core_config_snapshot.get('GPTSOVITS_ENABLED'))
            and not is_gsv_disabled_voice_id(core_config_snapshot.get('TTS_VOICE_ID', ''))
        )

        if input_mode == 'text':
            return True
        # Livestream upstream is the free Gemini route; server always handles native TTS. Client
        # character-card voice_id must never activate external TTS, whether free preset or otherwise,
        # or client sends complete sentences to tts_proxy, losing the first-audio latency advantage of server Gemini → core_proxy
        # → CV3's true bidirectional streaming path.
        #
        # PR #1369 ORed livestream-active into the original free-preset gate's third condition,
        # but retained its first two ANDs (_is_free_preset_voice / core_api_type='free'),
        # so livestream + non-free-preset voices (clones / empty voice_id / streamer-custom
        # voices unrecognized as presets) still fell back to external TTS. Add independent early return here.
        # _is_livestream_active already gates core_api_type='free' internally.
        if self._is_livestream_active():
            logger.info(f"{log_prefix}🎙️ livestream 模式：使用服务端原生语音，跳过外部 TTS")
            return False
        # Configured preset ownership must be resolved before core-native voice
        # routing; identical IDs still belong to the explicitly selected TTS API.
        # When configured and core-native voices share a name, explicit custom-TTS ownership takes priority over native routing.
        configured_provider = _core_facade.selected_configured_tts_preset_provider_key(
            core_config_snapshot,
            self._config_manager,
            self.voice_id,
        )
        if configured_provider:
            logger.info(
                "%s🔊 语音模式：音色归属已配置 TTS provider=%s，将使用外部 TTS",
                log_prefix,
                configured_provider,
            )
            return True
        if self._is_vllm_omni_tts_enabled(core_config_snapshot):
            logger.info(f"{log_prefix}🔊 语音模式：检测到 vLLM-Omni TTS provider，将使用外部 TTS")
            return True
        base_url = realtime_config.get('base_url', '')
        _, uses_provider_native_voice = resolve_native_voice_for_routing(
            self.core_api_type,
            self.voice_id,
            self._config_manager.voice_id_exists_in_any_storage,
            realtime_base_url=base_url,
        )
        if uses_provider_native_voice:
            logger.info(f"{log_prefix}🔊 {self.core_api_type} 原生音色 '{self.voice_id}' 将直接传入 RealtimeClient")
            return False
        if (
            self._is_free_preset_voice
            and self.core_api_type == 'free'
            and 'lanlan.tech' in realtime_config.get('base_url', '')
        ):
            logger.info(f"{log_prefix}🆓 免费预设音色 '{self.voice_id}' 将直接传入 session config，不启动外部 TTS")
            return False
        if self.voice_id or has_custom_tts_config:
            if has_custom_tts_config and not self.voice_id:
                logger.info(f"{log_prefix}🔊 语音模式：检测到自定义TTS配置，将使用自定义TTS覆盖原生语音")
            return True
        return False

    def _get_voice_id(self) -> str:
        raw = get_reserved(
            self.lanlan_basic_config[self.lanlan_name],
            'voice_id',
            default='',
            legacy_keys=('voice_id',),
        )
        # Lazy migration to unified voice sources: characters.json voice may be an old flat string or
        # structured {source,provider,ref} after user voice selection. read_legacy_voice_id reads both
        # as the legacy prefixed string consumed by dispatch/routing gates, stripping surrounding whitespace,
        # keeping downstream literal comparisons / is_free_preset_voice_id independent of storage shape.
        from utils.voice_config import read_legacy_voice_id
        return read_legacy_voice_id(raw)

    def _apply_voice_id_for_route(self) -> None:
        """Resolve the character card's voice_id into self.voice_id /
        self._is_free_preset_voice according to the current route.

        Shared by __init__ / start_session / _background_prepare_pending_session:
        reads _get_voice_id() → corrects the pairing between free presets and
        core_api_type. Centralized here to prevent rule drift.

        Historically this also suppressed voice delivery via "overseas lanlan.app
        hard-overrides to Leda"; now overseas free uniformly goes through
        www.lanlan.app with voice pass-through (full Gemini set + yui, claimed by
        the free_intl provider), no more suppression — stale StepFun/free preset
        voices won't hit the free_intl catalog under the overseas route and
        naturally fall through, no pre-clearing needed.

        An empty voice_id stays empty: under overseas free, the "empty → default
        voice" mapping is left to the server (www.lanlan.app); the client no
        longer injects a fallback voice.
        """
        raw_voice_id = self._get_voice_id()
        self.voice_id = raw_voice_id
        self._is_free_preset_voice = is_free_preset_voice_id(raw_voice_id)
        # Free preset selected outside free mode → do not send it, avoiding leakage of preset ids to other providers.
        if self._is_free_preset_voice and self.core_api_type != 'free':
            self.voice_id = ''
            self._is_free_preset_voice = False

    def _is_livestream_active(self) -> bool:
        """Livestream is a sub-mode on top of core_api_type='free'; both must hold simultaneously."""
        return self.core_api_type == 'free' and _core_facade.is_livestream_active()

    def _resolve_realtime_voice(self, realtime_config: dict):
        """Decide the voice that OmniRealtimeClient passes to the server/provider.

        Priority:
        1. core_api_type has a registered native voice provider and voice_id hits
           its catalog (Gemini Puck / Chinese male, etc.) → normalized and consumed
           directly by the provider client.
        2. livestream sub-mode enabled with a configured voice_id → use the
           livestream voice_id (bypassing the free_voices preset gate; the derived
           base_url no longer contains lanlan.tech)
        3. otherwise keep the original logic: deliver only when the character's
           voice is a free preset, core_api_type='free' and base_url still points
           at the lanlan.tech domain, to avoid leaking preset ids to non-lanlan
           services. Overseas free (free + *.lanlan.app) yui / Gemini voices are
           remapped via free_intl by resolve_native_voice_for_routing and hit
           step 1 directly.
        """
        base_url = realtime_config.get('base_url', '')
        voice_name, uses_provider_native_voice = resolve_native_voice_for_routing(
            self.core_api_type,
            self.voice_id,
            self._config_manager.voice_id_exists_in_any_storage,
            realtime_base_url=base_url,
        )
        if uses_provider_native_voice:
            return voice_name
        if self._is_livestream_active():
            ls_voice = get_livestream_config().get('voice_id', '')
            if ls_voice:
                return ls_voice
        base_url = realtime_config.get('base_url', '') or ''
        if (self._is_free_preset_voice
                and self.core_api_type == 'free'
                and 'lanlan.tech' in base_url):
            return self.voice_id
        return None

    def _resolve_realtime_free_voice(self, realtime_config: dict):
        """Backward-compatible wrapper for older callers/tests."""
        return self._resolve_realtime_voice(realtime_config)

    def _drop_free_voice_on_route_flip(self, old_core_url: str, new_core_url: str) -> bool:
        """Clear a free-catalog voice when the regional route flipped between snapshots.

        The domestic ``free`` and overseas ``free_intl`` catalogs are disjoint,
        so a voice resolved against the pre-flip region is guaranteed invalid on
        the post-flip route. Realtime already fails safe at delivery time
        (``_resolve_realtime_voice`` keys on the snapshot's base_url), but the
        text-mode TTS path synthesizes with ``self.voice_id`` directly — this
        applies the same fail-safe there: deliver nothing and let the server
        pick its default for the session. Custom voices are region-independent
        and left untouched.
        """
        if str(old_core_url or '') == str(new_core_url or ''):
            return False
        # Free catalog ownership has two parts: mainland presets use _is_free_preset_voice; overseas
        # free_intl (yui + full native Gemini catalog) uses registry against the **old route**,
        # because voice resolved under that catalog before switching. Literal enumeration misses non-yui
        # Gemini voices. Custom/cloned voices match neither side and remain unchanged.
        overseas_native = False
        if not self._is_free_preset_voice and self.voice_id:
            try:
                _, overseas_native = resolve_native_voice_for_routing(
                    self.core_api_type,
                    self.voice_id,
                    self._config_manager.voice_id_exists_in_any_storage,
                    realtime_base_url=str(old_core_url or ''),
                )
            except Exception:
                overseas_native = False
        if not (self._is_free_preset_voice or overseas_native):
            return False
        logger.info(
            "[GeoIP] 区域翻转后清空旧区域免费音色 '%s'，本场使用服务端默认", self.voice_id,
        )
        self.voice_id = ''
        self._is_free_preset_voice = False
        return True

    def _enqueue_voice_migration_notice(self, legacy_names: list) -> None:
        """Push the voice migration notice into the buffer pool, delegating to the module-level function for unified dedup."""
        enqueue_voice_migration_notice(legacy_names)

    def normalize_text(self, text): # Perform basic text preprocessing.
        text = text.strip()
        text = text.replace("\n", "")
        if contains_chinese(text):
            text = replace_blank(text)
            text = replace_corner_mark(text)
            text = text.replace(".", "。")
            text = text.replace(" - ", "，")
            text = remove_bracket(text)
            text = re.sub(r'[，、]+$', '。', text)
        else:
            text = remove_bracket(text)
        text = self.emoji_pattern2.sub('', text)
        text = self.emoji_pattern.sub('', text)
        if is_only_punctuation(text) and text not in ['<', '>']:
            return ""
        return text

    def _ensure_audio_frame_send_lock(self) -> asyncio.Lock:
        """Serialize the header/payload pairs that make up one audio frame.

        ``send_speech`` writes a JSON header and then its binary payload in two
        separate awaits, and the frontend pairs them FIFO. A mini-game line
        replayed from the audio cache is sent from the HTTP task while ordinary
        TTS is still streaming from the response handler (``interruptExisting``
        defaults to false), so without this the two can interleave as header A,
        header B, bytes A -- and the browser then plays A's bytes under B's
        speech id, gain and correlation. ``send_audio_done`` takes the same lock
        so a terminal signal cannot land between a header and its payload.

        Created lazily rather than in ``__init__``: this mixin is also exercised
        against partially-built manager doubles, and an attribute that only
        exists on the real constructor would make them diverge here.
        """
        lock = getattr(self, "_audio_frame_send_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._audio_frame_send_lock = lock
        return lock

    async def _write_audio_frame(self, websocket, header: dict, tts_audio) -> None:
        """Write one header/payload pair. The caller holds the frame lock."""
        async def write_frame():
            try:
                async with asyncio.timeout(TTS_FRAME_WRITE_TIMEOUT_SECONDS):
                    await websocket.send_json(header)
                    await websocket.send_bytes(tts_audio)
            except TimeoutError:
                # A partial frame must never be followed by another frame on
                # this socket. Close the captured transport, not a successor.
                try:
                    async with asyncio.timeout(TTS_SOCKET_CLOSE_TIMEOUT_SECONDS):
                        await websocket.close(code=1011)
                except Exception:
                    logger.warning("Failed to close stalled TTS audio transport", exc_info=True)
                raise WebSocketDisconnect(code=1011) from None
            self.sync_message_queue.put({"type": "binary", "data": tts_audio})

        writing = asyncio.create_task(write_frame())
        cancelled = False
        while not writing.done():
            try:
                await asyncio.shield(writing)
            except asyncio.CancelledError:
                cancelled = True
        # Retirement cannot release the frame lock with a header already sent
        # and the corresponding payload still pending. The handler stays part
        # of the handoff barrier until this pair finishes, even under repeated
        # cancellation. The writer itself times out and closes a partial stream.
        if cancelled:
            if not writing.cancelled():
                writing.exception()
            raise asyncio.CancelledError
        writing.result()

    def _audio_chunk_header(self, effective_speech_id: str) -> dict:
        header = {
            "type": "audio_chunk",
            "speech_id": effective_speech_id
        }
        correlation_id = self._game_speech_correlation_for(effective_speech_id)
        if correlation_id:
            header["sdk_speech_correlation_id"] = correlation_id
        playback_gain = self.speech_playback_gain(effective_speech_id)
        if playback_gain != 1.0:
            header["playback_gain"] = playback_gain
        return header

    async def send_cached_speech_batch(self, audio_chunks, speech_id: str):
        """Replay a cached utterance as ONE unit, terminal signal included.

        A per-frame lock keeps each header with its own payload but releases
        between frames, so a cached replay overlapping ordinary/project TTS
        (``interruptExisting`` defaults to false) still arrives as A1, B1, A2,
        B2. The frontend schedules decoded chunks in arrival order, so the two
        utterances are heard interwoven and the streaming decoder keeps
        switching speech ids. Cached replay is the one path that has every
        chunk in hand up front, so it can hold the lock across the whole batch
        -- streaming TTS cannot, and is unchanged.

        Returns ``(audio_sent, done_sent)``.
        """
        chunks = list(audio_chunks or ())
        try:
            websocket = self.websocket
            if not (
                websocket
                and hasattr(websocket, 'client_state')
                and websocket.client_state == websocket.client_state.CONNECTED
            ):
                ws_state = getattr(websocket, 'client_state', None) if websocket else None
                logger.warning(
                    f"⚠️ send_cached_speech_batch skipped: ws={websocket is not None}, state={ws_state}"
                )
                return False, False
            audio_sent = bool(chunks)
            async with self._ensure_audio_frame_send_lock():
                for tts_audio in chunks:
                    if websocket is not self.websocket:
                        logger.warning(
                            f"⚠️ send_cached_speech_batch aborted: websocket replaced mid-batch, speech_id={speech_id}"
                        )
                        return False, False
                    header = self._audio_chunk_header(speech_id)
                    await self._write_audio_frame(websocket, header, tts_audio)
                    self._speech_output_total += 1
                    self._last_speech_output_time = time.time()
                    self._last_speech_output_bytes = len(tts_audio)
                done_message = {
                    "type": "audio_done",
                    "speech_id": speech_id
                }
                correlation_id = self._game_speech_correlation_for(speech_id)
                if correlation_id:
                    done_message["sdk_speech_correlation_id"] = correlation_id
                if websocket is not self.websocket:
                    logger.warning(
                        f"⚠️ send_cached_speech_batch terminal signal skipped: websocket replaced, speech_id={speech_id}"
                    )
                    return audio_sent, False
                await websocket.send_json(done_message)
            return audio_sent, True
        except WebSocketDisconnect:
            logger.warning("⚠️ send_cached_speech_batch: WebSocket disconnected")
            return False, False
        except Exception as exc:
            logger.warning(f"⚠️ send_cached_speech_batch 发送失败: speech_id={speech_id}, err={exc}")
            return False, False
        finally:
            # Same bookkeeping ``send_audio_done`` performs: the stream
            # lifecycle is over even when the client disconnected mid-batch.
            self.release_speech_playback_gain(speech_id)
            self._clear_game_speech_correlation(speech_id)

    async def send_speech(self, tts_audio, speech_id: Optional[str] = None):
        """Send speech data to the frontend, sending the speech_id header first for precise interruption control"""
        try:
            # Pinned once. The lock keeps other SENDERS out, but ``self.websocket``
            # is reassigned by reconnect/teardown, which are not senders and do not
            # take it -- so re-reading the attribute per await could put the header
            # on the retired socket and its payload on the replacement.
            if not self._tts_output_is_current():
                return False
            websocket = self.websocket
            if websocket and hasattr(websocket, 'client_state') and websocket.client_state == websocket.client_state.CONNECTED:
                effective_speech_id = speech_id if speech_id is not None else self.current_speech_id
                header = self._audio_chunk_header(effective_speech_id)
                async with self._ensure_audio_frame_send_lock():
                    if not self._tts_output_is_current():
                        return False
                    if websocket is not self.websocket:
                        # Reconnect/teardown landed while this call was queued for
                        # the frame lock. The pin above keeps the frame whole; it
                        # cannot make a retired socket the right destination.
                        # Writing it there delivers to nobody and hides the loss
                        # from the caller, so report it instead.
                        logger.warning(
                            f"⚠️ send_speech skipped: websocket replaced while waiting for the frame lock, speech_id={effective_speech_id}"
                        )
                        return False
                    await self._write_audio_frame(websocket, header, tts_audio)
                if not self._tts_output_is_current():
                    return False
                logger.debug(f"🔊 send_speech OK: {len(tts_audio)} bytes, speech_id={effective_speech_id}")
                self._speech_output_total += 1
                self._last_speech_output_time = time.time()
                self._last_speech_output_bytes = len(tts_audio)
                return True
            else:
                ws_state = getattr(websocket, 'client_state', None) if websocket else None
                logger.warning(f"⚠️ send_speech skipped: ws={websocket is not None}, state={ws_state}")
                return False
        except WebSocketDisconnect:
            logger.warning("⚠️ send_speech: WebSocket disconnected")
            return False
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")
            return False

    async def send_audio_done(self, speech_id: Optional[str]):
        """Tell the frontend that this speech_id's audio stream is closed.

        Callers MUST await this only after the last ``audio_chunk`` of the same
        speech_id has already been awaited out. The frontend finalizes the turn
        on this signal, so an emission that overtakes trailing audio truncates
        the utterance -- which is the very defect this signal exists to fix.
        Never scheduled fire-and-forget for that reason.

        Best-effort by design: a dropped signal only costs the frontend its
        fast path (it still has its own give-up timer plus a watchdog), so any
        failure is swallowed instead of propagating to the caller. Unlike
        ``send_speech`` this is not mirrored to ``sync_message_queue``: no
        monitor/viewer surface consumes audio.
        """
        if not speech_id or not self._tts_output_is_current():
            # Ownerless signals would finalize the wrong frontend turn; prefer not sending.
            return False
        try:
            # Pinned for the same reason as send_speech: the terminal signal has
            # to land on the socket its audio went to, not on a replacement that
            # attached while this call was waiting for the frame lock.
            websocket = self.websocket
            if websocket and hasattr(websocket, 'client_state') and websocket.client_state == websocket.client_state.CONNECTED:
                message = {
                    "type": "audio_done",
                    "speech_id": speech_id
                }
                correlation_id = self._game_speech_correlation_for(speech_id)
                if correlation_id:
                    message["sdk_speech_correlation_id"] = correlation_id
                async with self._ensure_audio_frame_send_lock():
                    if not self._tts_output_is_current():
                        return False
                    if websocket is not self.websocket:
                        logger.warning(
                            f"⚠️ send_audio_done skipped: websocket replaced while waiting for the frame lock, speech_id={speech_id}"
                        )
                        return False
                    await websocket.send_json(message)
                logger.debug(f"🔚 send_audio_done OK: speech_id={speech_id}")
                return True
            else:
                ws_state = getattr(websocket, 'client_state', None) if websocket else None
                logger.warning(f"⚠️ send_audio_done skipped: ws={websocket is not None}, state={ws_state}")
                return False
        except WebSocketDisconnect:
            logger.warning("⚠️ send_audio_done: WebSocket disconnected")
            return False
        except Exception as e:
            logger.warning(f"⚠️ send_audio_done 发送失败: speech_id={speech_id}, err={e}")
            return False
        finally:
            # The stream lifecycle is over even when the client disconnected.
            if self._tts_output_is_current():
                self.release_speech_playback_gain(speech_id)
                self._clear_game_speech_correlation(speech_id)

    async def tts_response_handler(self):
        runtime = tts_output_runtime.get() or self._snapshot_tts_runtime()
        q = runtime.response_queue if runtime is not None else self.tts_response_queue
        if not self._tts_runtime_is_current(runtime):
            return
        tts_output_runtime.set(runtime)
        pending_failed_speech_id = ""
        notified_error_keys = getattr(self, "_tts_notified_error_keys", None)
        if notified_error_keys is None:
            # Some tests and compatibility callers construct the manager with
            # ``__new__``. Lazily initialize while keeping normal runtimes on
            # the manager-owned set created in ``LLMSessionManager.__init__``.
            notified_error_keys = set()
            self._tts_notified_error_keys = notified_error_keys
        logger.info(f"🎧 tts_response_handler started (queue id={id(q):#x})")
        pending_get = None
        while True:
            if not self._tts_runtime_is_current(runtime) or q is not self.tts_response_queue:
                return
            try:
                # Blocking get runs in thread pool, leaving main event loop idle when no messages;
                # on cancellation, except CancelledError pushes a sentinel to wake the thread
                # still blocked in q.get(), avoiding thread leaks.
                pending_get = asyncio.create_task(asyncio.to_thread(q.get))
                data = await asyncio.shield(pending_get)
                pending_get = None
                if (not self._tts_runtime_is_current(runtime)
                        or q is not self.tts_response_queue):
                    return

                # Handle the sentinel pushed on cancellation to wake leaked threads. The same handler
                # never continues after cancel (CancelledError re-raised); this only lets
                # a replacement handler on the same queue correctly ignore a leftover sentinel
                # accidentally read from its predecessor.
                if isinstance(data, tuple) and len(data) == 2 and data[0] == "__handler_exit__":
                    continue

                if (
                    isinstance(data, tuple)
                    and len(data) == 3
                    and data[0] in {"__tts_sentence_done__", "__tts_sentence_failed__"}
                ):
                    _, speech_id, sentence = data
                    if data[0] == "__tts_sentence_failed__":
                        # Sentence workers put this marker immediately before the
                        # matching ``__error__`` item.  Keep the speech identity so
                        # parallel failures from one reply can share one user notice.
                        pending_failed_speech_id = str(speech_id or "")
                        GAME_SPEECH_AUDIO_CACHE.fail_capture(self, speech_id)
                        self._mark_game_speech_delivery_failed(speech_id)
                    if speech_id == getattr(self, "_tts_replay_speech_id", None):
                        # Marker follows all sentence audio; advance boundaries only when audio actually reached frontend.
                        # Skip the whole failed sentence if any prefix played, preventing fallback from repeating its start;
                        # retain subsequent unacknowledged sentences in ledger.
                        if getattr(self, "_tts_replay_sentence_audio_emitted", False):
                            self._consume_tts_replay_sentence(speech_id, str(sentence))
                        self._tts_replay_sentence_audio_emitted = False
                    continue

                if isinstance(data, tuple) and len(data) == 2:
                    if data[0] == "__audio_done__":
                        # Must await, not _fire_task: handler consumes queue sequentially,
                        # and await ensures this finalization signal follows all
                        # __audio__/raw bytes for this sid. Fire-and-forget can overtake final audio,
                        # finalizing frontend early, exactly the issue this signal addresses.
                        speech_id = data[1]
                        done_sent = await self.send_audio_done(speech_id)
                        if not self._tts_runtime_is_current(runtime):
                            return
                        if not done_sent:
                            self._mark_game_speech_delivery_failed(speech_id)
                        delivered = self._game_speech_delivery_succeeded(speech_id)
                        logger.info(
                            "[voice-chain] stage=tts_done speech_id=%s sent=%s delivered=%s",
                            speech_id,
                            done_sent,
                            delivered,
                        )
                        if delivered:
                            GAME_SPEECH_AUDIO_CACHE.complete_capture(
                                self,
                                speech_id,
                                self.current_game_speech_audio_runtime_signature(),
                            )
                        else:
                            GAME_SPEECH_AUDIO_CACHE.fail_capture(self, speech_id)
                        self._resolve_game_speech_completion_wait(speech_id, delivered)
                        self._clear_game_speech_delivery_state(speech_id)
                        completed_speech_id = str(speech_id or "")
                        if completed_speech_id:
                            completed_keys = {
                                key for key in notified_error_keys
                                if key[0] == completed_speech_id
                            }
                            notified_error_keys.difference_update(completed_keys)
                        continue
                    if data[0] == "__ready__":
                        ready_flag = bool(data[1])
                        async with self.tts_cache_lock:
                            if not self._tts_runtime_is_current(runtime) or q is not self.tts_response_queue:
                                return
                            self.tts_ready = ready_flag
                        if ready_flag:
                            logger.info(
                                "[voice-chain] stage=tts_ready ready=true speech_id=%s",
                                getattr(self, "current_speech_id", ""),
                            )
                            self._last_tts_error_code = ''
                            self._tts_retry_notify_count = 0
                            self._tts_rate_limit_backoff_level = 0
                            self._tts_rate_limit_retry_at = 0.0
                            self._tts_quota_from_server_close = False
                            if getattr(self, '_tts_quota_blocked', False):
                                # During quota rejection, cache contains tails of rejected replies; discard only speech_ids classified
                                # as old at this retry's initial trigger. Later legitimately queued
                                # speech (retry-triggering turn, mirrored speech, etc.) plays normally.
                                stale_speech_ids = getattr(
                                    self, '_tts_quota_stale_speech_ids', None
                                ) or frozenset()
                                self._tts_quota_blocked = False
                                self._tts_quota_stale_speech_ids = None
                                if stale_speech_ids:
                                    async with self.tts_cache_lock:
                                        dropped = {
                                            chunk[0] for chunk in self.tts_pending_chunks
                                            if chunk[0] in stale_speech_ids
                                        }
                                        self.tts_pending_chunks[:] = [
                                            chunk for chunk in self.tts_pending_chunks
                                            if chunk[0] not in stale_speech_ids
                                        ]
                                    self._fail_dropped_tts_speech(dropped)
                            logger.info("✅ 收到TTS运行时就绪信号，开始刷新缓存文本")
                            await self._flush_tts_pending_chunks()
                        else:
                            logger.warning(
                                "[voice-chain] stage=tts_ready ready=false speech_id=%s",
                                getattr(self, "current_speech_id", ""),
                            )
                            # Configured endpoints fall back once; the handler
                            # then follows the replacement worker's new queue.
                            # If custom endpoint is not ready, immediately switch to fallback worker and listen on new queue.
                            if await self._activate_configured_tts_fallback_after_capacity("初始化", runtime):
                                q = self.tts_response_queue
                                runtime = self._snapshot_tts_runtime()
                                tts_output_runtime.set(runtime)
                                if runtime is not None:
                                    runtime.handler = asyncio.current_task()
                                self._tts_handler_response_queue = q
                                continue
                            if not self._tts_runtime_is_current(runtime):
                                return
                            # Use the code recorded by __error__ to decide retries.
                            _last_code = self._last_tts_error_code
                            if _last_code in NO_RETRY_TTS_CODES:
                                logger.warning(f"⚠️ TTS 未就绪且上次错误为 {_last_code}，跳过自动重试")
                                # Cancel pending delayed retry to avoid bypassing no-retry policy.
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                # TTS cannot recover; clear unused buffered text to free memory.
                                async with self.tts_cache_lock:
                                    if not self._tts_runtime_is_current(runtime):
                                        return
                                    dropped = {chunk[0] for chunk in self.tts_pending_chunks}
                                    self.tts_pending_chunks.clear()
                                self._fail_dropped_tts_speech(dropped)
                            elif _last_code == 'API_QUOTA_TIME' and getattr(
                                self, '_tts_quota_from_server_close', False
                            ):
                                # Server provides no recovery time (IP-based rolling 24h window); scheduled
                                # respawns would repeatedly reject. Disable scheduled retries but continue implicit respawn
                                # on each reply's first chunk; the next utterance recovers once window expires.
                                logger.warning("⚠️ TTS 配额已用完，停止定时重试；下一次回复时再尝试")
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                self._tts_quota_blocked = True
                                self._tts_quota_stale_speech_ids = None
                                async with self.tts_cache_lock:
                                    dropped = {chunk[0] for chunk in self.tts_pending_chunks}
                                    self.tts_pending_chunks.clear()
                                self._fail_dropped_tts_speech(dropped)
                            else:
                                # 1013 rate/concurrency limit: increase delay progressively; other errors keep 13 seconds.
                                if _last_code == 'API_RATE_LIMIT':
                                    backoff_level = getattr(self, '_tts_rate_limit_backoff_level', 0)
                                    respawn_delay = min(
                                        TTS_RESPAWN_DELAY_SECONDS * (2 ** backoff_level),
                                        TTS_RATE_LIMIT_MAX_RESPAWN_DELAY_SECONDS,
                                    )
                                    self._tts_rate_limit_backoff_level = backoff_level + 1
                                    self._tts_rate_limit_retry_at = time.monotonic() + respawn_delay
                                else:
                                    respawn_delay = TTS_RESPAWN_DELAY_SECONDS
                                logger.warning(f"⚠️ 收到TTS未就绪信号，{respawn_delay}秒后尝试重新拉起Worker")
                                # Cancel previous delayed retry task, if any.
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                # Capture current session identity and TTS flag to prevent cross-session respawn.
                                _expected_session = self.session
                                _expected_use_tts = self.use_tts
                                async def _delayed_respawn(_expected_session=_expected_session,
                                                           _expected_use_tts=_expected_use_tts,
                                                           _expected_runtime=runtime,
                                                           _delay=respawn_delay):
                                    await asyncio.sleep(_delay)
                                    if not self._tts_runtime_is_current(_expected_runtime):
                                        return
                                    if not self.is_active or self.tts_ready:
                                        return
                                    if self.session is not _expected_session or self.use_tts != _expected_use_tts:
                                        logger.info("🔄 TTS 延迟重试：会话已变更，跳过 respawn")
                                        return
                                    logger.info("🔄 TTS 延迟重试：尝试重新拉起 Worker...")
                                    self._respawn_tts_worker(timed=True)
                                self._tts_respawn_task = asyncio.ensure_future(_delayed_respawn())
                        continue
                    elif data[0] == "__warning__":
                        # Forward informational TTS-worker messages (e.g. watermark detection) directly to frontend.
                        # Keep the transport write inside the handler lifetime:
                        # retirement drains this task before allowing handoff.
                        await self.send_status(data[1])
                        continue
                    elif data[0] == "__reconnecting__":
                        self._tts_retry_notify_count += 1
                        logger.info(f"🌊 TTS 正在自动重连 (retry {self._tts_retry_notify_count})")
                        if self._tts_retry_notify_count >= 3:
                            user_msg = json.dumps({"code": "TTS_RECONNECTING", "level": "info"})
                            await self.send_status(user_msg)
                        continue
                    elif data[0] == "__error__":
                        error_msg = data[1]
                        error_msg_text = str(error_msg)
                        error_speech_id = pending_failed_speech_id or str(
                            getattr(self, "current_speech_id", "") or ""
                        )
                        pending_failed_speech_id = ""
                        GAME_SPEECH_AUDIO_CACHE.fail_capture(self, error_speech_id)
                        self._mark_game_speech_delivery_failed(error_speech_id)
                        logger.error(f"TTS Worker Error: {error_msg}")
                        logger.info(
                            "[voice-chain] stage=tts_error speech_id=%s error_len=%d",
                            error_speech_id,
                            len(error_msg_text),
                        )

                        # A configured endpoint failure is observable in the
                        # backend log above, then redispatched through the exact
                        # pre-existing fallback chain for subsequent audio.
                        # On custom-API runtime failure, change provider only, preserving existing fallback order.
                        if await self._activate_configured_tts_fallback_after_capacity("运行时", runtime):
                            q = self.tts_response_queue
                            runtime = self._snapshot_tts_runtime()
                            tts_output_runtime.set(runtime)
                            if runtime is not None:
                                runtime.handler = asyncio.current_task()
                            self._tts_handler_response_queue = q
                            continue
                        if not self._tts_runtime_is_current(runtime):
                            return

                        # Prefer explicit code field from structured JSON.
                        _known_codes = {
                            'API_ARREARS', 'API_QUOTA_TIME', 'API_KEY_REJECTED',
                            'API_RATE_LIMIT', 'API_POLICY_VIOLATION',
                            'API_1008_FALLBACK', 'TTS_CONNECTION_FAILED',
                            'UPSTREAM_SERVER_BUSY', 'TTS_CONFIG_INVALID',
                            'API_ACCESS_DENIED',
                        }
                        _parsed_code = None
                        _keyword_target = error_msg_text  # Fallback for non-JSON errors.
                        # User-facing text defaults to original, except close-frame errors report only
                        # close code: provider-controlled reason text must not enter UI (contract in
                        # tests/unit/runtime/test_realtime_connection_recovery.py）。
                        # Keyword classification still reads data.message, unaffected.
                        _display_msg = error_msg_text
                        # Free-service rejections classified by close frame include data.close_code.
                        _from_server_close = False
                        try:
                            _parsed = json.loads(error_msg_text)
                            if isinstance(_parsed, dict):
                                _close_data = _parsed.get('data')
                                # Require nonempty close_code, used to build display string;
                                # missing values would render 'WebSocket close code None'.
                                _from_server_close = (
                                    isinstance(_close_data, dict)
                                    and _close_data.get('close_code') is not None
                                )
                                if _from_server_close:
                                    _display_msg = (
                                        f"WebSocket close code {_close_data.get('close_code')}"
                                    )
                                # Structured errors: keyword matching reads only data.message, avoiding metadata false positives.
                                _keyword_target = ""
                                # Check top-level code first.
                                _candidate = _parsed.get('code', '')
                                if isinstance(_candidate, str) and _candidate in _known_codes:
                                    _parsed_code = _candidate
                                # Then check data.code (TTS event structure).
                                if not _parsed_code:
                                    _data = _parsed.get('data', {})
                                    if isinstance(_data, dict):
                                        _candidate = _data.get('code', '')
                                        if isinstance(_candidate, str) and _candidate in _known_codes:
                                            _parsed_code = _candidate
                                        # Match keywords only in message field.
                                        _keyword_target = str(_data.get('message', '') or "")
                        except (json.JSONDecodeError, TypeError):
                            # JSON parsing may fail for free-form error strings from
                            # tts.response.error events; this is expected and harmless —
                            # the keyword-based fallback below will handle classification.
                            pass

                        if _parsed_code:
                            user_msg = json.dumps({"code": _parsed_code, "details": {"msg": _display_msg}})
                            self._last_tts_error_code = _parsed_code
                        else:
                            # Fall back to keyword matching only on message, excluding UUID/timestamp metadata.
                            error_msg_lower = _keyword_target.lower()
                            if '欠费' in error_msg_lower or 'standing' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_ARREARS"})
                                self._last_tts_error_code = 'API_ARREARS'
                            elif 'quota' in error_msg_lower or 'time limit' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_QUOTA_TIME"})
                                self._last_tts_error_code = 'API_QUOTA_TIME'
                            elif '429' in error_msg_lower or 'too many' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_RATE_LIMIT"})
                                self._last_tts_error_code = 'API_RATE_LIMIT'
                            elif _is_safety_violation_signal(error_msg_lower):
                                user_msg = json.dumps({"code": "API_POLICY_VIOLATION", "details": {"msg": _display_msg}})
                                self._last_tts_error_code = 'API_POLICY_VIOLATION'
                            elif '1008' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_1008_FALLBACK", "details": {"msg": _display_msg}})
                                self._last_tts_error_code = 'API_1008_FALLBACK'
                            elif ('401' in error_msg_lower or 'unauthorized' in error_msg_lower
                                    or 'authentication' in error_msg_lower
                                    or 'incorrect api key' in error_msg_lower
                                    or 'invalid_api_key' in error_msg_lower
                                    or ('invalid' in error_msg_lower and 'key' in error_msg_lower)):
                                user_msg = json.dumps({"code": "API_KEY_REJECTED", "details": {"msg": _display_msg}})
                                self._last_tts_error_code = 'API_KEY_REJECTED'
                            else:
                                user_msg = json.dumps({"code": "TTS_CONNECTION_FAILED", "details": {"msg": _display_msg}})
                                self._last_tts_error_code = 'TTS_CONNECTION_FAILED'
                        # Disable scheduled retries only for free-service daily quotas identified by close frame; paid/custom
                        # providers' '429 quota exceeded' also classifies as quota but often means recoverable
                        # per-minute throttling, retaining existing scheduled retries.
                        self._tts_quota_from_server_close = (
                            self._last_tts_error_code == 'API_QUOTA_TIME'
                            and _from_server_close
                        )
                        # Telemetry: TTS failure; code is a normalized low-cardinality enum.
                        # （API_ARREARS / API_KEY_REJECTED / TTS_CONNECTION_FAILED ...）。
                        # No voice on day one breaks the core experience, an important D1 churn signal.
                        try:
                            from utils.instrument import counter as _instr_counter
                            # before_first_loop: TTS breaks before user experiences core loop =
                            # first-experience barrier (spoke but heard no reply). Low-cardinality true/false/unknown.
                            try:
                                from utils.token_tracker import TokenTracker as _TT
                                _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                            except Exception:
                                _bfl = "unknown"
                            _instr_counter("tts_error", code=str(self._last_tts_error_code or "unknown")[:32], before_first_loop=_bfl)
                        except Exception:
                            # Telemetry is best effort and must never affect TTS retry/reporting flow.
                            pass
                        # Retryable errors: first two retries silent; report to frontend on the third failure.
                        if self._last_tts_error_code not in IMMEDIATE_REPORT_TTS_CODES:
                            self._tts_retry_notify_count += 1
                            if self._tts_retry_notify_count < 3:
                                logger.info(f"TTS 错误重试 {self._tts_retry_notify_count}/3，暂不通知前端")
                                continue
                        error_code = str(self._last_tts_error_code or "")
                        notification_key = (error_speech_id, error_code)
                        if (
                            error_speech_id
                            and error_code
                            and notification_key in notified_error_keys
                        ):
                            logger.info(
                                "TTS user notice deduplicated: code=%s speech_id=%s",
                                error_code,
                                error_speech_id,
                            )
                            continue
                        if error_speech_id and error_code:
                            notified_error_keys.add(notification_key)
                        await self.send_status(user_msg)
                        continue
                elif isinstance(data, tuple) and len(data) == 3 and data[0] == "__audio__":
                    _, speech_id, audio_payload = data
                    sent = await self.send_speech(audio_payload, speech_id=speech_id)
                    logger.debug(
                        "[voice-chain] stage=tts_audio_delivery speech_id=%s bytes=%d sent=%s",
                        speech_id,
                        len(audio_payload) if isinstance(audio_payload, (bytes, bytearray)) else 0,
                        sent,
                    )
                    if not self._tts_runtime_is_current(runtime):
                        return
                    if sent:
                        GAME_SPEECH_AUDIO_CACHE.append_capture(self, speech_id, audio_payload)
                        self._tts_replay_audio_emitted = True
                        self._tts_replay_sentence_audio_emitted = True
                        self._confirm_pending_ai_voice_echo(speech_id)
                        # Telemetry: successful audio delivery means user heard the character. Together with
                        # note_core_loop_completed's prerequisite that user spoke, this signals D1
                        # core-loop completion (once per process, internally idempotent).
                        try:
                            from utils.token_tracker import TokenTracker as _TT
                            _TT.get_instance().note_core_loop_completed()
                        except Exception:
                            # Telemetry is best effort and never affects primary audio delivery;
                            # note_core_loop_completed is itself idempotent.
                            pass
                    else:
                        GAME_SPEECH_AUDIO_CACHE.fail_capture(self, speech_id)
                        self._mark_game_speech_delivery_failed(speech_id)
                        self._discard_pending_ai_voice_echo()
                    continue

                size = len(data) if isinstance(data, (bytes, bytearray)) else f"type={type(data).__name__}"
                logger.debug(f"🎧 handler dequeued audio: {size}, qsize≈{q.qsize()}")
                implicit_speech_id = str(getattr(self, "current_speech_id", "") or "")
                sent = await self.send_speech(data)
                if not self._tts_runtime_is_current(runtime):
                    return
                if sent:
                    GAME_SPEECH_AUDIO_CACHE.append_unscoped_capture(
                        self, implicit_speech_id, data
                    )
                    self._tts_replay_audio_emitted = True
                    self._tts_replay_sentence_audio_emitted = True
                else:
                    GAME_SPEECH_AUDIO_CACHE.fail_capture(self, implicit_speech_id)
                    self._mark_game_speech_delivery_failed(implicit_speech_id)
                self._discard_pending_ai_voice_echo()
            except TtsCapacityError:
                # Capacity is transient while the retired worker drains. Do
                # not latch the session into a permanent no-audio state; the
                # next response or scheduled respawn must be allowed to retry.
                self._tts_capacity_exhausted = False
                if self._tts_runtime_is_current(runtime):
                    self.tts_ready = False
                    await self.send_status(json.dumps({"code": "TTS_CONNECTION_FAILED"}))
                return
            except asyncio.CancelledError:
                logger.info("🎧 tts_response_handler cancelled")
                if q is self.tts_response_queue and self._tts_runtime_is_current(runtime):
                    GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
                if pending_get is not None and not pending_get.done():
                    q.put_nowait(("__handler_exit__", None))
                    # Do not let runtime cleanup drain this wake-up sentinel
                    # before the real executor consumer has returned. Shield
                    # keeps cancellation from hiding a still-blocked q.get().
                    while not pending_get.done():
                        try:
                            await asyncio.shield(pending_get)
                        except asyncio.CancelledError:
                            pass
                    pending_get.result()
                raise
            except Exception as e:
                logger.error(f"💥 tts_response_handler error (will retry): {e}")
                await asyncio.sleep(0.01)
