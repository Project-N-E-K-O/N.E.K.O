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
)
from main_logic.omni_offline_client import _is_safety_violation_signal
from main_logic.tts_client import (
    dummy_tts_worker,
    TTS_SHUTDOWN_SENTINEL,
    TTS_AUDIO_DONE_SENTINEL,
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
from ._shared import logger, NO_RETRY_TTS_CODES, IMMEDIATE_REPORT_TTS_CODES
from .notices import enqueue_voice_migration_notice
from .game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE, GameSpeechCaptureOwner

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
            # 优先从对话设置中读取，如果不存在则从核心配置读取
            conversation_settings = _core_facade.load_global_conversation_settings()
            if 'textGuardMaxLength' in conversation_settings:
                value = int(conversation_settings['textGuardMaxLength'])
            else:
                value = int(self._config_manager.get_core_config().get('TEXT_GUARD_MAX_LENGTH', 300))
            # 0 / 负数都表示"无限制"，与 OmniOfflineClient.__init__ /
            # update_max_response_length 的语义统一。原本 < 0 会 raise 然后
            # fallback 到 300，存量配置带 -1 的会被静默降级。
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
            # 边界不一致时宁可保留文本，也不能误删尚未播出的内容。
            logger.warning("TTS 回放句界不匹配，保留现有未确认文本")
            return
        remaining = remaining[len(sentence):]
        self._tts_replay_sent_chunks = (
            [(speech_id, remaining)] if remaining else []
        )

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
        # 保留规范化前的原始文本；替代 worker 可能属于另一协议类别，需要从原文重放。
        self._remember_tts_replay_chunk(speech_id, text)

        # speech_id 切换时重置所有 stripper 状态（pending 内容属于上一轮，丢弃）
        if speech_id != self._tts_norm_speech_id:
            self._tts_stream_normalizer.reset()
            self._tts_markdown_stripper.reset()
            self._tts_bracket_stripper.reset()
            self._tts_norm_speech_id = speech_id

        if self._tts_normalize_enabled:
            text = self._tts_stream_normalizer.feed(text)
            if not text:
                return
        # markdown → bracket 顺序固定：链接先剥成文本再交给 bracket
        text = self._tts_markdown_stripper.feed(text)
        if not text:
            return
        text = self._tts_bracket_stripper.feed(text)
        if not text:
            return
        self.tts_request_queue.put((speech_id, text))
        self._remember_tts_sent_chunk(speech_id, text)
        self._remember_pending_ai_voice_echo(speech_id, text)

    def _reset_tts_stream_normalizer(self) -> None:
        """Clear all TTS text stripper state. Called on interrupt / turn end / session rebuild."""
        self._tts_stream_normalizer.reset()
        self._tts_markdown_stripper.reset()
        self._tts_bracket_stripper.reset()
        self._tts_norm_speech_id = None

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

        if not self.tts_ready or self.tts_pending_chunks:
            self._tts_replay_done = True
            self._tts_done_pending_until_ready = True
            return "deferred"

        # 把 markdown/bracket stripper 的 pending 兜底 emit：链 markdown.flush()
        # → bracket.feed(...) → bracket.flush() 顺序，与 _enqueue_tts_text_chunk
        # 的串接顺序一致。markdown.flush 把残留的孤立 marker 字符删掉再 emit；
        # bracket.feed 处理任何残留括号字符；bracket.flush 直接 reset 不读
        # 未闭合的括号内容。normalizer.flush 永远返回 ""，省略调用。
        flushed = self._tts_markdown_stripper.flush()
        if flushed:
            flushed = self._tts_bracket_stripper.feed(flushed)
        self._tts_bracket_stripper.flush()
        if flushed and self._tts_norm_speech_id is not None:
            self.tts_request_queue.put((self._tts_norm_speech_id, flushed))
            self._remember_tts_sent_chunk(self._tts_norm_speech_id, flushed)
            self._remember_pending_ai_voice_echo(self._tts_norm_speech_id, flushed)

        self.tts_request_queue.put((None, None))
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

        async with self.tts_cache_lock:
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
        markdown_output = markdown.feed(text) + markdown.flush()
        spoken = bracket.feed(markdown_output)
        bracket.flush()
        return str(spoken or "").strip()

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
                                        GAME_SPEECH_AUDIO_CACHE.append_capture(
                                            capture_owner, speech_id, audio
                                        )
                                    continue
                                if isinstance(message, (bytes, bytearray, memoryview)):
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
                                    failure_reason = (
                                        "tts_incomplete" if not loaded else ""
                                    )
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
        """
        # ``_tts_done_queued_for_turn`` 的对偶是下面的 ``__interrupt__``：一入队
        # 就把上一轮那个 done sentinel 作废，而这个 flag 是"本轮 sentinel 已排队"
        # 的唯一记账。所以清零属于 interrupt 的同一步，必须落在函数的第一个
        # await（下面的 sleep）之前：从函数入口到 put("__interrupt__") 全是同步
        # 语句，取消无从投递，两者因此原子。放在 await 之后（历史写法：由各调用方
        # 在 await 返回后各自清）取消落在 sleep 上就会留下"worker 已被中断、记账
        # 还说已排队"的残留态，下一轮 ``_request_tts_done_locked`` 因此
        # early-return "already"，flush sentinel 永远进不了队 —— 正是
        # handle_new_message 那两行清零本来要防的后果。
        #
        # ``_tts_done_pending_until_ready`` 不能跟着挪上来：它的对偶是
        # ``tts_pending_chunks``（"这批文本还没刷出去，done 要等它们之后再补发"），
        # 两者一起在函数末尾的 tts_cache_lock 段里清。单把 flag 提前、chunks 留在
        # 原地，取消落在 sleep 上就撕成另一个方向的半套态：worker 就绪后
        # ``_flush_tts_pending_chunks`` 会把陈旧 chunks 重新入队，却因为 flag 已是
        # False 而跳过它们的 done 补发，合成器一直不 flush。所以它留在下面不动。
        # 调用方在本函数返回后的重复清零保留不动：那是给 sleep 窗口内被并发
        # 置回 True 的情况兜底，与这里要修的取消残留是两件事。
        self._tts_done_queued_for_turn = False
        self._cancel_game_speech_completion_wait()
        self._clear_game_speech_correlation()
        GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
        if self.tts_thread and self.tts_thread.is_alive():
            while not self.tts_response_queue.empty():
                try:
                    self.tts_response_queue.get_nowait()
                except Exception:
                    break
            try:
                self.tts_request_queue.put(("__interrupt__", None))
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS中断信号失败: {e}")
            self._reset_tts_stream_normalizer()
            # 等待 TTS worker 处理 __interrupt__ 并 mute 回调（worker 轮询间隔 ~10ms）
            # 然后再次清空响应队列，确保旧 synthesizer 泄漏的音频全部丢弃
            await asyncio.sleep(0.02)
            while not self.tts_response_queue.empty():
                try:
                    self.tts_response_queue.get_nowait()
                except Exception:
                    break
        async with self.tts_cache_lock:
            self.tts_pending_chunks.clear()
            # 再清一次 queued：上面那 20ms 里并发路径（finish_proactive_delivery
            # 等）可能看到 False、排了自己的 sentinel 并把它置回 True。那个
            # sentinel 排在 __interrupt__ 之后、本轮后续文本之前，本来就已作废；
            # 而记账留 True 会让后续文本的 done 请求 early-return "already"，
            # 重试那一轮因此发不出句尾 sentinel。handle_new_message 一族靠调用方
            # 返回后的重复清零兜住了这一点，丢弃/takeover 两条路径没有，所以兜底
            # 收进这里，让五个调用方拿到同一个保证。
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

    async def _stop_tts_response_handler(self) -> None:
        """Stop the handler bound to the current response queue.

        ``tts_response_handler`` captures ``tts_response_queue`` when its task
        starts. Replacing a worker also replaces that queue, so the old handler
        must be cancelled before a replacement worker/handler pair is created.
        """
        handler_task = self.tts_handler_task
        handler_queue = getattr(self, "_tts_handler_response_queue", None)
        if handler_task is not None and not handler_task.done():
            if handler_queue is self.tts_response_queue:
                GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
            handler_task.cancel()
            try:
                await asyncio.wait_for(handler_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if self.tts_handler_task is handler_task:
            self.tts_handler_task = None
            if getattr(self, "_tts_handler_response_queue", None) is handler_queue:
                self._tts_handler_response_queue = None

    def _start_tts_response_handler(self):
        """Start one handler and bind its ownership to the captured response queue."""
        task = asyncio.create_task(self.tts_response_handler())
        self.tts_handler_task = task
        self._tts_handler_response_queue = self.tts_response_queue
        return task

    async def ensure_tts_pipeline_alive(self) -> None:
        """Light TTS startup helper: spawn worker + handler task if not alive.

        Does NOT wait for ``__ready__`` — callers that need confirmed-ready
        must poll :attr:`is_tts_pipeline_ready` themselves.  Callers that
        only need ``tts_pending_chunks`` to eventually drain do not need
        to wait at all (the handler picks up pending chunks once
        ``tts_ready`` flips).
        """
        if not (self.tts_thread and self.tts_thread.is_alive()):
            # A live handler can still be blocked on the response queue owned
            # by a worker that was shut down for native Realtime voice. It must
            # not survive across the fresh queues created by _start_tts_thread.
            await self._stop_tts_response_handler()
            self._start_tts_thread(
                preserve_provider_exclusions=bool(
                    getattr(self, "_tts_excluded_provider_keys", frozenset())
                )
            )
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
        # 克隆音色始终走 custom 路径。
        if bool(self.voice_id) and not self._is_free_preset_voice:
            return True
        return False

    def _effective_tts_route(self) -> tuple[str, bool]:
        """Return the voice identity and custom flag used for worker dispatch."""
        # Recovery keeps character storage unchanged but strips the failed preset
        # from the replacement worker's routing and credential context.
        # 回退只清理替代 worker 的路由上下文，不修改角色卡中保存的音色。
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
        # 重置就绪状态，新 worker 需重新握手
        self.tts_ready = False
        self._tts_runtime_key = None
        if not preserve_provider_exclusions:
            self._tts_excluded_provider_keys = frozenset()
            self._tts_fallback_uses_default_voice = False

        # 检查是否禁用了 TTS
        tts_worker, api_key, route_voice_id, provider_key, disabled, tts_config = (
            self._resolve_tts_worker_spec()
        )
        if disabled:
            logger.info("TTS 已被用户禁用, 使用 dummy worker")
        self._tts_completion_supported = self._tts_worker_supports_completion(
            tts_worker,
            provider_key,
            tts_config,
        )
        self._tts_audio_output_supported = self._tts_completion_supported

        # 根据实际选中的 TTS provider 类别决定是否启用流式文本规范化。
        # ws_bistream 类（qwen / step / cosyvoice）直接把文本碎片发给服务端处理，
        # normalizer 的 pending_spaces 延迟投递和 CJK 边界空格删除会干扰送达节奏。
        # http_sentence 类（cogtts / gemini / openai / minimax）做客户端句子分割，
        # 需要干净的文本，normalizer 在此有意义。
        # 注意：'free' 不在 registry 中 → meta 为 None → 走 fallthrough 启用 normalizer，
        # 因为 free 国外模式走 Gemini 后端，需要 CJK 空格清理。
        meta = TTS_PROVIDER_REGISTRY.get(provider_key) if provider_key else None
        self._tts_normalize_enabled = not meta or meta.category != "ws_bistream"
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
        self.tts_thread.start()

    def _activate_configured_tts_fallback(self, failure_stage: str) -> bool:
        """Exclude a failed configured provider and restart with existing order."""
        failed_provider = getattr(self, "_tts_active_provider_key", None)
        if not _core_facade.tts_provider_falls_back_on_failure(failed_provider):
            return False

        excluded = frozenset(getattr(self, "_tts_excluded_provider_keys", frozenset()))
        if failed_provider in excluded:
            return False

        # The ledger is authoritative after response.done: some realtime paths
        # rotate ``current_speech_id`` before trailing TTS audio has finished.
        # 回复流结束时 current_speech_id 可能已提前轮换；旧 worker 的尾音仍在处理，
        # 因此优先使用账本中的 speech_id，避免恰在收尾失败时漏掉整轮重放。
        active_speech_id = getattr(self, "_tts_replay_speech_id", None)
        if active_speech_id is None:
            active_speech_id = getattr(self, "current_speech_id", None)

        # Only replay the active utterance. A new turn can be cached while a late
        # error from the superseded worker is still arriving on the old queue.
        # 只重放当前轮次，避免旧 worker 的迟到错误把上一轮文本带进新回复。
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
                # HTTP 分句 worker 会逐句确认进度：已播句从该账本删除，失败句若
                # 已输出部分音频也只跳过本句，后续完整句仍交给保底 provider。
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
                # WS 双流协议没有文本到音频位置确认，不能安全推导精确后缀。
                replay_chunks = []
                replay_done = bool(pending_chunks and replay_done)
                logger.warning(
                    "自定义 TTS API 已输出部分音频，跳过当前句重放以避免重复播报"
                )

        # Excluding one failed provider lets get_tts_worker reuse every existing
        # fallback rule below it; no fallback provider or priority is hardcoded.
        # 这里只排除失败项，再调用原 dispatcher；不新增、不重排任何保底方案。
        self._tts_excluded_provider_keys = excluded | {failed_provider}
        if _core_facade.tts_provider_uses_configured_preset_voice(failed_provider):
            self._tts_fallback_uses_default_voice = True
        # 替代 worker 不继承故障 provider 的错误码和提示次数。
        self._last_tts_error_code = ''
        self._tts_retry_notify_count = 0
        old_request_queue = self.tts_request_queue
        try:
            old_request_queue.put(("__shutdown__", None))
        except Exception:
            logger.debug("关闭故障 TTS worker 失败，继续启动保底 worker", exc_info=True)

        logger.warning(
            "自定义 TTS API provider=%s 在%s阶段失败；开始按既有顺序选择保底 provider",
            failed_provider,
            failure_stage,
        )
        # 新 worker 就绪后按原始文本重建本轮请求。不能沿用失败 preset 的
        # Voice ID/凭证；角色配置本身不变，下个会话仍可重新尝试该 provider。
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
        notified_error_keys = getattr(self, "_tts_notified_error_keys", None)
        if notified_error_keys is not None:
            notified_error_keys.clear()
        self._tts_done_queued_for_turn = False
        self._tts_fallback_uses_default_voice = False
        self._reset_tts_replay_state()
        self._tts_done_pending_until_ready = False
        self._tts_active_provider_key = None
        self._tts_excluded_provider_keys = frozenset()

    async def _teardown_tts_runtime(self, handler_task_ref, thread_ref,
                                     req_queue_ref, resp_queue_ref):
        """Tear down TTS handler task, worker thread, and drain queues.

        Operates only on the snapshot references passed in to prevent
        accidentally killing resources that have been recreated by a
        concurrent start_session.
        """
        if handler_task_ref and not handler_task_ref.done():
            handler_task_ref.cancel()
            try:
                await asyncio.wait_for(handler_task_ref, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                # Cancel echo or slow exit of the superseded handler — proceed either way.
                pass
            if self.tts_handler_task is handler_task_ref:
                self.tts_handler_task = None
                if getattr(self, "_tts_handler_response_queue", None) is resp_queue_ref:
                    self._tts_handler_response_queue = None

        if thread_ref and thread_ref.is_alive():
            try:
                # 使用独立的 shutdown sentinel；(None, None) 在 worker 里是
                # "本轮 utterance 结束、flush 缓冲区"，并不会让 worker 退出。
                req_queue_ref.put(("__shutdown__", None))
                await asyncio.to_thread(thread_ref.join, 2.0)
            except Exception as e:
                logger.error(f"💥 关闭TTS线程时出错: {e}")

            if thread_ref.is_alive():
                logger.warning("⚠️ TTS worker 未在超时内退出，清除引用以允许重建")
                if self.tts_thread is thread_ref:
                    self.tts_thread = None
            else:
                if self.tts_thread is thread_ref:
                    self.tts_thread = None
                # 仅在线程确实已停止后才安全地清空队列
                try:
                    while not req_queue_ref.empty():
                        req_queue_ref.get_nowait()
                except Exception:
                    # Queue drained concurrently — nothing left to clear.
                    pass
                try:
                    while not resp_queue_ref.empty():
                        resp_queue_ref.get_nowait()
                except Exception:
                    # Queue drained concurrently — nothing left to clear.
                    pass

        # 只在被拆除的 runtime 仍是当前 runtime 时才清全局 TTS 状态，
        # 避免新 session 已创建新队列/worker 后被旧 teardown 误重置
        if resp_queue_ref is self.tts_response_queue:
            self._cancel_game_speech_completion_wait()
            self._clear_game_speech_correlation()
            self.cancel_game_speech_preloads()
            GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
            async with self.tts_cache_lock:
                self.tts_ready = False
                self.tts_pending_chunks.clear()

    def _respawn_tts_worker(self):
        """Respawn the TTS worker when its thread is detected dead, without blocking for readiness.

        Once the new worker is ready it sends the __ready__ signal through
        response_queue; tts_response_handler receives it and calls
        _flush_tts_pending_chunks to flush the cache.

        Rate limit: at most one respawn per 12 seconds, avoiding a reconnect
        storm when the service is completely down.
        """
        if self.tts_thread and self.tts_thread.is_alive():
            return

        # 如果上次错误属于不应自动重试的类型，直接跳过 respawn
        if self._last_tts_error_code in NO_RETRY_TTS_CODES:
            logger.warning(f"⚠️ _respawn_tts_worker: 上次错误为 {self._last_tts_error_code}，跳过自动重试")
            return

        import time
        now = time.monotonic()
        if now - self._last_tts_respawn_time < 12.0:
            return  # 冷却中，保留待执行的延迟任务和错误码状态

        # 通过冷却检查后，取消可能仍在等待的延迟重试任务，既然已经在直接 respawn 了
        if self._tts_respawn_task and not self._tts_respawn_task.done():
            self._tts_respawn_task.cancel()
            self._tts_respawn_task = None
        self._last_tts_respawn_time = now

        logger.info("🔄 TTS Worker 已死亡，尝试重新拉起...")
        self._start_tts_thread(
            preserve_provider_exclusions=bool(
                getattr(self, "_tts_excluded_provider_keys", frozenset())
            )
        )

        # 重新启动 tts_response_handler 以监听新队列
        if self.tts_handler_task and not self.tts_handler_task.done():
            self.tts_handler_task.cancel()
        self._start_tts_response_handler()

        logger.info("🔄 TTS Worker 已重新拉起，等待运行时就绪信号...")

    async def _flush_tts_pending_chunks(self):
        """Send the cached TTS text chunks to the TTS queue"""
        async with self.tts_cache_lock:
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

                # 清空缓存
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
        # Livestream 上游是 free 路 Gemini 系，服务端始终承担原生 TTS。客户端
        # 角色卡的 voice_id 不论是不是 free preset，都不应该再开外部 TTS——
        # 否则文本会被客户端按整句喂给 tts_proxy，丢掉服务端 Gemini → core_proxy
        # → CV3 那条真 bistream 路径的首音频延迟优势。
        #
        # PR #1369 在原 free-preset gate 第三个条件里 OR 了 livestream-active，
        # 但前两个 AND（_is_free_preset_voice / core_api_type='free'）没拆，
        # 导致 livestream + 非 free preset 音色（克隆 / 空 voice_id / 主播
        # 自定义未识别为 preset）仍会 fallback 到外部 TTS。这里独立早退兜底。
        # _is_livestream_active 内部已经 gate 了 core_api_type='free'。
        if self._is_livestream_active():
            logger.info(f"{log_prefix}🎙️ livestream 模式：使用服务端原生语音，跳过外部 TTS")
            return False
        # Configured preset ownership must be resolved before core-native voice
        # routing; identical IDs still belong to the explicitly selected TTS API.
        # 配置型音色与核心原生音色同名时，自定义 TTS 的显式归属优先，不能被原生路由抢走。
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
        # 声音来源统一架构惰性迁移：characters.json 里 voice 可能是旧扁平串，也可能是
        # 用户设音色后迁成的结构对象 {source,provider,ref}。read_legacy_voice_id 把两形态
        # 统一读成 dispatch/route gating 一直消费的 legacy 前缀串（顺带 strip 收口空白），
        # 下游 literal 比较 / is_free_preset_voice_id 等无需感知存储形态。
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
        # free preset 选了但当前非 free 模式 → 不下发，避免把 preset id 透给别的 provider。
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
        # 免费目录归属分两半：大陆预设由 _is_free_preset_voice 标记；海外
        # free_intl（yui + 完整 Gemini 原生目录）用 registry 按**旧路由**判——
        # 翻转前音色就是在旧路由目录下解析出来的，字面量列举会漏掉非 yui 的
        # Gemini 音色。自配/克隆音色两边都不命中，保持不动。
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

    def normalize_text(self, text): # 对文本进行基本预处理
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
        await websocket.send_json(header)
        await websocket.send_bytes(tts_audio)
        # Under the same lock: the monitor mirror consumes this queue in order,
        # so a frame that is atomic on the wire must not be split here either.
        self.sync_message_queue.put({"type": "binary", "data": tts_audio})

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
            websocket = self.websocket
            if websocket and hasattr(websocket, 'client_state') and websocket.client_state == websocket.client_state.CONNECTED:
                effective_speech_id = speech_id if speech_id is not None else self.current_speech_id
                header = self._audio_chunk_header(effective_speech_id)
                async with self._ensure_audio_frame_send_lock():
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
        if not speech_id:
            # 无主信号会让前端给错误的一轮收尾，宁可不发。
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
            self.release_speech_playback_gain(speech_id)
            self._clear_game_speech_correlation(speech_id)

    async def tts_response_handler(self):
        q = self.tts_response_queue
        pending_failed_speech_id = ""
        notified_error_keys = getattr(self, "_tts_notified_error_keys", None)
        if notified_error_keys is None:
            # Some tests and compatibility callers construct the manager with
            # ``__new__``. Lazily initialize while keeping normal runtimes on
            # the manager-owned set created in ``LLMSessionManager.__init__``.
            notified_error_keys = set()
            self._tts_notified_error_keys = notified_error_keys
        logger.info(f"🎧 tts_response_handler started (queue id={id(q):#x})")
        while True:
            try:
                # 阻塞 get 挂在线程池里，无消息时主 event loop 完全沉默；
                # 取消时 except CancelledError 分支会 push 哨兵唤醒线程池里那个
                # 仍在 q.get() 上的线程，避免线程泄漏。
                data = await asyncio.to_thread(q.get)

                # 处理 cancel 时为唤醒泄漏线程而 push 的哨兵。同一个 handler 实例
                # 不会在 cancel 之后继续运行（CancelledError 已 raise），所以这里
                # 只是为了在 handler 被替换后，新 handler（绑同一 queue）若意外
                # 读到旧 handler 留下的哨兵也能正确忽略。
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
                        # marker 排在该句所有音频之后；只有音频实际送达前端才推进边界。
                        # 失败句若已播放过前缀也整体跳过，避免 fallback 从句首重念；
                        # 其后的未确认句仍保留在账本中。
                        if getattr(self, "_tts_replay_sentence_audio_emitted", False):
                            self._consume_tts_replay_sentence(speech_id, str(sentence))
                        self._tts_replay_sentence_audio_emitted = False
                    continue

                if isinstance(data, tuple) and len(data) == 2:
                    if data[0] == "__audio_done__":
                        # 必须 await，不能 _fire_task：handler 顺序消费队列，
                        # await 才能保证这条收尾信号排在该 sid 的所有
                        # __audio__/裸 bytes 之后。fire-and-forget 会插到尾音
                        # 前面，前端提前收尾——正是本信号要解决的问题。
                        speech_id = data[1]
                        done_sent = await self.send_audio_done(speech_id)
                        if not done_sent:
                            self._mark_game_speech_delivery_failed(speech_id)
                        delivered = self._game_speech_delivery_succeeded(speech_id)
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
                            self.tts_ready = ready_flag
                        if ready_flag:
                            self._last_tts_error_code = ''
                            self._tts_retry_notify_count = 0
                            logger.info("✅ 收到TTS运行时就绪信号，开始刷新缓存文本")
                            await self._flush_tts_pending_chunks()
                        else:
                            # Configured endpoints fall back once; the handler
                            # then follows the replacement worker's new queue.
                            # 自定义端点未就绪时立即切到保底 worker，并改听新队列。
                            if self._activate_configured_tts_fallback("初始化"):
                                q = self.tts_response_queue
                                continue
                            # 复用 __error__ 分支记录的 code 判断是否重试
                            _last_code = self._last_tts_error_code
                            if _last_code in NO_RETRY_TTS_CODES:
                                logger.warning(f"⚠️ TTS 未就绪且上次错误为 {_last_code}，跳过自动重试")
                                # 取消可能仍在等待的延迟重试任务，避免绕过 no-retry 策略
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                # TTS 不会恢复，清空无用的缓存文本，避免白白占用内存
                                async with self.tts_cache_lock:
                                    self.tts_pending_chunks.clear()
                            else:
                                logger.warning("⚠️ 收到TTS未就绪信号，13秒后尝试重新拉起Worker")
                                # 取消之前的延迟重试任务（如有）
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                # 捕获当前会话身份与 TTS 标志，防止跨会话的错误 respawn
                                _expected_session = self.session
                                _expected_use_tts = self.use_tts
                                async def _delayed_respawn(_expected_session=_expected_session,
                                                           _expected_use_tts=_expected_use_tts):
                                    await asyncio.sleep(13)
                                    if not self.is_active or self.tts_ready:
                                        return
                                    if self.session is not _expected_session or self.use_tts != _expected_use_tts:
                                        logger.info("🔄 TTS 延迟重试：会话已变更，跳过 respawn")
                                        return
                                    logger.info("🔄 TTS 延迟重试：尝试重新拉起 Worker...")
                                    self._respawn_tts_worker()
                                self._tts_respawn_task = asyncio.ensure_future(_delayed_respawn())
                        continue
                    elif data[0] == "__warning__":
                        # TTS worker 发来的提示性消息（如水印检测），直接转发前端
                        self._fire_task(self.send_status(data[1]))
                        continue
                    elif data[0] == "__reconnecting__":
                        self._tts_retry_notify_count += 1
                        logger.info(f"🌊 TTS 正在自动重连 (retry {self._tts_retry_notify_count})")
                        if self._tts_retry_notify_count >= 3:
                            user_msg = json.dumps({"code": "TTS_RECONNECTING", "level": "info"})
                            self._fire_task(self.send_status(user_msg))
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

                        # A configured endpoint failure is observable in the
                        # backend log above, then redispatched through the exact
                        # pre-existing fallback chain for subsequent audio.
                        # 自定义 API 运行时出错后，仅切换 provider；现有保底顺序不变。
                        if self._activate_configured_tts_fallback("运行时"):
                            q = self.tts_response_queue
                            continue

                        # 优先尝试从结构化 JSON 中提取明确的 code 字段
                        _known_codes = {
                            'API_ARREARS', 'API_QUOTA_TIME', 'API_KEY_REJECTED',
                            'API_RATE_LIMIT', 'API_POLICY_VIOLATION',
                            'API_1008_FALLBACK', 'TTS_CONNECTION_FAILED',
                            'UPSTREAM_SERVER_BUSY', 'TTS_CONFIG_INVALID',
                        }
                        _parsed_code = None
                        _keyword_target = error_msg_text  # 非 JSON 错误时回退使用
                        try:
                            _parsed = json.loads(error_msg_text)
                            if isinstance(_parsed, dict):
                                # 结构化错误：关键词匹配只看 data.message，避免元数据误判
                                _keyword_target = ""
                                # 先检查顶层 code
                                _candidate = _parsed.get('code', '')
                                if isinstance(_candidate, str) and _candidate in _known_codes:
                                    _parsed_code = _candidate
                                # 再检查 data.code（TTS 事件结构）
                                if not _parsed_code:
                                    _data = _parsed.get('data', {})
                                    if isinstance(_data, dict):
                                        _candidate = _data.get('code', '')
                                        if isinstance(_candidate, str) and _candidate in _known_codes:
                                            _parsed_code = _candidate
                                        # 关键词匹配仅针对 message 字段
                                        _keyword_target = str(_data.get('message', '') or "")
                        except (json.JSONDecodeError, TypeError):
                            # JSON parsing may fail for free-form error strings from
                            # tts.response.error events; this is expected and harmless —
                            # the keyword-based fallback below will handle classification.
                            pass

                        if _parsed_code:
                            user_msg = json.dumps({"code": _parsed_code, "details": {"msg": error_msg_text}})
                            self._last_tts_error_code = _parsed_code
                        else:
                            # 回退到关键词匹配（仅匹配 message 字段，不匹配 UUID/时间戳等元数据）
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
                                user_msg = json.dumps({"code": "API_POLICY_VIOLATION", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_POLICY_VIOLATION'
                            elif '1008' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_1008_FALLBACK", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_1008_FALLBACK'
                            elif ('401' in error_msg_lower or 'unauthorized' in error_msg_lower
                                    or 'authentication' in error_msg_lower
                                    or 'incorrect api key' in error_msg_lower
                                    or 'invalid_api_key' in error_msg_lower
                                    or ('invalid' in error_msg_lower and 'key' in error_msg_lower)):
                                user_msg = json.dumps({"code": "API_KEY_REJECTED", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_KEY_REJECTED'
                            else:
                                user_msg = json.dumps({"code": "TTS_CONNECTION_FAILED", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'TTS_CONNECTION_FAILED'
                        # Telemetry：TTS 失败。code 是已归一化的低基数枚举
                        # （API_ARREARS / API_KEY_REJECTED / TTS_CONNECTION_FAILED ...）。
                        # 首日听不到语音是核心体验断裂，D1 流失重要信号。
                        try:
                            from utils.instrument import counter as _instr_counter
                            # before_first_loop：TTS 在用户体验到核心 loop 前就坏 =
                            # 首次体验障碍（开了口但没听到回复）。低基数 true/false/unknown。
                            try:
                                from utils.token_tracker import TokenTracker as _TT
                                _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                            except Exception:
                                _bfl = "unknown"
                            _instr_counter("tts_error", code=str(self._last_tts_error_code or "unknown")[:32], before_first_loop=_bfl)
                        except Exception:
                            # 埋点 best-effort，绝不影响 TTS 错误的重试/上报主流程。
                            pass
                        # 可重试的错误：前2次静默重试，第3次失败时上报前端
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
                        self._fire_task(self.send_status(user_msg))
                        continue
                elif isinstance(data, tuple) and len(data) == 3 and data[0] == "__audio__":
                    _, speech_id, audio_payload = data
                    if await self.send_speech(audio_payload, speech_id=speech_id):
                        GAME_SPEECH_AUDIO_CACHE.append_capture(self, speech_id, audio_payload)
                        self._tts_replay_audio_emitted = True
                        self._tts_replay_sentence_audio_emitted = True
                        self._confirm_pending_ai_voice_echo(speech_id)
                        # Telemetry：音频成功投递 = 用户听到了角色的声音。配合
                        # note_core_loop_completed 的"用户已开口"前置，构成 D1
                        # 核心 loop 完成信号（每进程一次，内部幂等）。
                        try:
                            from utils.token_tracker import TokenTracker as _TT
                            _TT.get_instance().note_core_loop_completed()
                        except Exception:
                            # 埋点 best-effort，绝不影响音频投递主流程；
                            # note_core_loop_completed 自身幂等。
                            pass
                    else:
                        GAME_SPEECH_AUDIO_CACHE.fail_capture(self, speech_id)
                        self._mark_game_speech_delivery_failed(speech_id)
                        self._discard_pending_ai_voice_echo()
                    continue

                size = len(data) if isinstance(data, (bytes, bytearray)) else f"type={type(data).__name__}"
                logger.debug(f"🎧 handler dequeued audio: {size}, qsize≈{q.qsize()}")
                implicit_speech_id = str(getattr(self, "current_speech_id", "") or "")
                if await self.send_speech(data):
                    GAME_SPEECH_AUDIO_CACHE.append_unscoped_capture(
                        self, implicit_speech_id, data
                    )
                    self._tts_replay_audio_emitted = True
                    self._tts_replay_sentence_audio_emitted = True
                else:
                    GAME_SPEECH_AUDIO_CACHE.fail_capture(self, implicit_speech_id)
                    self._mark_game_speech_delivery_failed(implicit_speech_id)
                self._discard_pending_ai_voice_echo()
            except asyncio.CancelledError:
                logger.info("🎧 tts_response_handler cancelled")
                if q is self.tts_response_queue:
                    GAME_SPEECH_AUDIO_CACHE.discard_owner(self)
                # asyncio.to_thread 取消后，线程池里那个 thread 仍阻塞在 q.get()。
                # push 哨兵唤醒它返回，避免线程泄漏（线程持有 queue ref，整个 queue
                # 也会被一起留住）。put_nowait 失败不影响主流程。
                try:
                    q.put_nowait(("__handler_exit__", None))
                except Exception:
                    # See note above — the sentinel push is best-effort.
                    pass
                raise
            except Exception as e:
                logger.error(f"💥 tts_response_handler error (will retry): {e}")
                await asyncio.sleep(0.01)
