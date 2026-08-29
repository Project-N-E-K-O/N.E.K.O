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
"""Live input streaming for ``LLMSessionManager``: screen/audio stream
intake, hot-swap cache flushes, and stream-time turn-end
bookkeeping.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import json
import time
from websockets import exceptions as web_exceptions
from utils.screenshot_utils import overlay_avatar_annotation
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionEvent
from utils.language_utils import get_global_language_full
from uuid import uuid4
from ._shared import (
    _TEXT_SESSION_INPUT_TYPES,
    _IMAGE_INPUT_TYPES,
    _LIVE_VISION_STREAM_INPUT_TYPES,
    FRONTEND_START_SESSION_TIMEOUT_SECONDS,
    logger,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class StreamingMixin:
    """Live input streaming methods (see module docstring)."""

    @staticmethod
    def _user_input_ingress_time(message: dict) -> float:
        """Return the server-captured ingress time, or sample a safe fallback."""
        captured_at = message.get("_user_input_ingress_time")
        if isinstance(captured_at, (int, float)):
            return float(captured_at)
        return time.time()

    def note_stream_input_ingress(self, message: dict) -> bool:
        """Record nonblank text/image input before fallible staging."""
        input_type = message.get("input_type")
        if input_type == "text":
            memory_text = self._clean_frontend_memory_text(
                message.get("memory_text")
            )
            content = memory_text or message.get("data")
        elif input_type in {"avatar_drop_image", "user_image"}:
            content = message.get("data")
        else:
            return False

        if isinstance(content, str):
            has_content = bool(content.strip())
        elif isinstance(content, (bytes, bytearray)):
            has_content = bool(content)
        else:
            has_content = False
        if not has_content:
            return False

        self.note_user_engagement(
            at=self._user_input_ingress_time(message)
        )
        return True

    def _emit_cooldown_turn_end_if_needed(self):
        """Deduplicated turn_end emission during cooldown, at most once per second. Returns True when currently cooling down."""
        if not self._memory_error_retry_after or time.time() >= self._memory_error_retry_after:
            return False
        now = time.time()
        if now - self._last_cooldown_turn_end_time >= 1.0:
            self._last_cooldown_turn_end_time = now
            time_left = int(self._memory_error_retry_after - now)
            self._fire_task(self.send_status(json.dumps({
                "code": "MEMORY_SERVER_COOLDOWN",
                "details": {"wait_time": time_left}
            })))
            self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                self._fire_task(self.websocket.send_json({'type': 'system', 'data': 'turn end'}))
        return True
    
    async def _flush_pending_input_data(self):
        """Send the cached input data to the session"""
        # A realtime -> offline attachment handoff must stage the attachment
        # before inputs that arrived while the replacement session was
        # starting. ``start_session`` normally flushes as soon as the session
        # becomes ready; defer that nested flush until the owning attachment
        # finishes its one-shot ``stream_image`` call.
        if getattr(self, "_deferred_pending_input_flush_count", 0) > 0:
            return
        async with self.input_cache_lock:
            if getattr(self, "_pending_input_flush_active", False):
                return
            if not self.pending_input_data:
                return
            self._pending_input_flush_active = True

        try:
            while True:
                async with self.input_cache_lock:
                    if not self.pending_input_data:
                        self._pending_input_flush_active = False
                        return
                    # Drain atomically, then process outside this lock. One-shot
                    # image attachments may need _ensure_offline_session_for_text_input(),
                    # whose handoff reacquires input_cache_lock; awaiting that
                    # path while still holding the lock would deadlock.
                    pending_messages = list(self.pending_input_data)
                    self.pending_input_data.clear()

                # Once detached from ``pending_input_data``, this local batch
                # owns every message until each item reaches a terminal handling
                # point. Cancellation/session teardown must put the untouched
                # suffix back ahead of live inputs queued while the flush was
                # active; otherwise startup cancellation silently loses input.
                next_unprocessed = 0
                try:
                    if not self.session or not self.is_active:
                        return

                    # 缓存阶段（_stream_data_now）不知道 session 最终是 voice 还是
                    # text。如果最终启好的是 voice session，缓存里的纯 text 输入若
                    # 直接 flush 进 _process_stream_data_internal，会把刚 ready 的 voice
                    # session 撕成 text；继续丢弃纯文本。但 avatar_drop_image/user_image
                    # 是明确的一次性附件，必须保留其既有 offline vision 合同。screen /
                    # camera 也继续走 realtime 合法路径。audio 在缓存阶段不会出现。
                    dropped_text_for_voice = 0
                    for index, message in enumerate(pending_messages):
                        msg_input_type = message.get("input_type")
                        try:
                            if msg_input_type == "audio":
                                await self._enqueue_audio_stream_data(message)
                            else:
                                if (
                                    isinstance(self.session, OmniRealtimeClient)
                                    and msg_input_type == "text"
                                ):
                                    self.note_stream_input_ingress(message)
                                    dropped_text_for_voice += 1
                                    next_unprocessed = index + 1
                                    continue
                                await self._process_stream_data_internal(message)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            # The message was attempted and is now terminally
                            # failed. Retrying it at the queue head would block
                            # every later cached/live input indefinitely; drop
                            # only this item and preserve batch ordering.
                            next_unprocessed = index + 1
                            logger.error(
                                "💥 发送缓存的输入数据失败，丢弃当前消息: %s",
                                e,
                            )
                            continue
                        next_unprocessed = index + 1
                    if dropped_text_for_voice:
                        logger.info(
                            "[%s] _flush_pending_input_data: dropped %d cached text "
                            "message(s) because final session is voice mode",
                            self.lanlan_name,
                            dropped_text_for_voice,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 发送缓存的输入数据失败: {e}")
                    return
                finally:
                    unprocessed = pending_messages[next_unprocessed:]
                    if unprocessed:
                        async with self.input_cache_lock:
                            self.pending_input_data[0:0] = unprocessed
        finally:
            async with self.input_cache_lock:
                if getattr(self, "_pending_input_flush_active", False):
                    self._pending_input_flush_active = False
    
    def _should_drop_live_vision_stream(self, input_type: str | None) -> bool:
        """Deliberately checked at each stream boundary; callers may enter below stream_data."""
        return input_type in _LIVE_VISION_STREAM_INPUT_TYPES and self.is_goodbye_silent()

    async def stream_data(self, message: dict):  # 向Core API发送Media数据
        input_type = message.get("input_type")
        if self._should_drop_live_vision_stream(input_type):
            return
        if input_type == "audio":
            await self._enqueue_audio_stream_data(message)
            return
        await self._stream_data_now(message)

    async def _stream_data_now(self, message: dict):
        input_type = message.get("input_type")
        if self._should_drop_live_vision_stream(input_type):
            return
        if input_type in _TEXT_SESSION_INPUT_TYPES:
            # Preserve when the user action reached the server. Session startup,
            # router task scheduling, mode rebuilds, and pending-input flushes
            # may delay actual handling. Preserve a router-provided timestamp;
            # direct/internal callers get a safe fallback sampled here.
            # Copy so callers cannot observe this internal transport metadata.
            message = {
                **message,
                "_user_input_ingress_time": self._user_input_ingress_time(message),
            }
            # Genuine one-shot input must reset unanswered evidence even if a
            # circuit breaker, failed startup, or final voice-mode flush drops
            # it before the normal text/image processing branches are reached.
            self.note_stream_input_ingress(message)
        elif input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
            # Preserve router ingress ordering across the independent screen /
            # camera tasks and their threaded validation. Direct/internal
            # callers receive a monotonic fallback sampled before any await.
            captured_at = message.get("_visual_input_ingress_time")
            message = {
                **message,
                "_visual_input_ingress_time": (
                    float(captured_at)
                    if isinstance(captured_at, (int, float))
                    else time.monotonic()
                ),
            }
        # 检查session是否就绪
        async with self.input_cache_lock:
            if getattr(self, "_pending_input_flush_active", False):
                # Replay owns ordering until its current batch finishes. Queue
                # live input behind it instead of racing the same offline
                # session's stream_text/stream_image call.
                self.pending_input_data.append(message)
                return
            if not self.session_ready:
                # 检查是否正在启动session - 只有在启动过程中才缓存
                if self._starting_session_count > 0:
                    if input_type == "audio":
                        return
                    # Session正在启动中，缓存输入数据
                    self.pending_input_data.append(message)
                    if len(self.pending_input_data) == 1:
                        logger.info("Session正在启动中，开始缓存输入数据...")
                    else:
                        logger.debug(f"继续缓存输入数据 (总计: {len(self.pending_input_data)} 条)...")
                    return

        # 在锁外检查是否需要创建新session（不要在锁内创建session，避免死锁）
        if not self.session_ready and self._starting_session_count == 0:
            if not self.session or not self.is_active:
                if input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
                    return
                # Memory Server 专属冷却检查
                if self._emit_cooldown_turn_end_if_needed():
                    return
                # 熔断早退：start_session 内部也会拦，但这里再加一层省掉
                # 每个音频包的"自动创建 session" info 日志，避免日志洪水。
                if self._session_start_circuit_open:
                    return
                logger.info(f"Session未就绪且不存在，根据输入类型 {input_type} 自动创建 session")
                # 根据输入类型确定模式
                mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
                await self.start_session(self.websocket, new=False, input_mode=mode)

                # 检查启动是否成功
                if not self.session or not self.is_active:
                    logger.warning("⚠️ Session启动失败，放弃本次数据流")
                    return
        
        # Session已就绪，直接处理
        await self._process_stream_data_internal(message)

    async def _ensure_offline_session_for_text_input(
        self,
        input_type: str,
    ) -> bool:
        """Move text/attachment input onto its offline-session contract."""
        if isinstance(self.session, OmniOfflineClient):
            return True
        # 与 _handoff_to_offline_vlm_and_submit 共用同一把闸。两边做的是同一件事
        # ——把当前会话就地换成 offline 客户端——各自却都有一段 end_session +
        # start_session 的 await 窗口。不共闸的话，两条并发 handoff 会互相拆掉对方
        # 刚建好的 offline 会话：后进的那条 teardown 掉先进的成果，先进的那条随后
        # 往已经退役的客户端提交。加锁顺序与 lifecycle 那条一致（handoff 闸在外、
        # swap 闸在里），不会反向。
        lock = getattr(self, '_multimodal_handoff_lock', None)
        if lock is None:
            lock = asyncio.Lock()
            self._multimodal_handoff_lock = lock
        try:
            await asyncio.wait_for(
                lock.acquire(),
                # 闸的持有者正在做一次会话重建，等它的预算就用会话启动的预算。
                timeout=FRONTEND_START_SESSION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # 超时不能"绕过闸自己重建" —— 那正好是这把闸要防的并发。只在对方已经
            # 把会话换成 offline 时算成功（它替我们做完了这件事）。
            if isinstance(self.session, OmniOfflineClient):
                return True
            logger.error(
                "💥 %s 等待 offline handoff 闸超时，放弃本次数据流",
                input_type,
            )
            return False
        try:
            return await self._rebuild_offline_session_for_text_input(input_type)
        finally:
            lock.release()

    async def _rebuild_offline_session_for_text_input(
        self,
        input_type: str,
    ) -> bool:
        """The handoff body itself; runs holding ``_multimodal_handoff_lock``."""
        # 拿到闸之后重读：排在前面的那条 handoff 可能已经把会话换成 offline 了，
        # 这时候再拆一次就是白白打断一个刚建好的会话。
        if isinstance(self.session, OmniOfflineClient):
            return True
        if self.session_start_failure_count >= self.session_start_max_failures:
            logger.error(
                "💥 %s 需要文本模式，但失败次数过多，已停止自动重建",
                input_type,
            )
            return False

        logger.info(
            "%s 需要 OmniOfflineClient，但当前是 %s. 自动重建 session。",
            input_type,
            type(self.session).__name__,
        )
        # Hold the startup guard across end_session's await window. Without
        # this, concurrent microphone work can recreate an audio session before
        # the attachment/text handoff starts its offline replacement.
        async with self.input_cache_lock:
            self.session_ready = False
        self._starting_session_count += 1
        self._starting_input_mode = "text"
        try:
            if self.session:
                # preserve_pending_input：这次 teardown 是「就地换成 offline 会话」
                # 的一步，不是会话结束。session_ready 在上面已经置 False，所以拆
                # session 的整个 await 窗口里，并发的 text / 附件任务都会把消息缓存
                # 进 pending_input_data；end_session 默认会把这个队列清空，那些输入
                # 就在用户毫无察觉的情况下没了。重建完成后由
                # _flush_pending_input_data 正常放出去。
                await self.end_session(
                    # by_server=True：这是内部的 Realtime → Offline 就地替换，不是
                    # 用户会话结束。默认值会在收尾时向前端推 CHARACTER_LEFT，用户
                    # 只是发了条文本/拖了张图，却先看到「角色离开」再看到新会话启动
                    # ——前端可能据此清理对话界面。既有的 idle_session_reset 内部路径
                    # 就是用 by_server=True 抑制这条推送的，判据一致。
                    by_server=True,
                    reset_starting_count=False,
                    preserve_pending_input=True,
                )
        finally:
            self._starting_session_count = max(0, self._starting_session_count - 1)
            if self._starting_session_count == 0:
                self._starting_input_mode = None
        # Do not await between releasing the guard and entering start_session;
        # its synchronous prologue reacquires the startup ownership.
        await self.start_session(
            self.websocket,
            new=False,
            input_mode="text",
        )
        if (
            not self.session
            or not self.is_active
            or not isinstance(self.session, OmniOfflineClient)
        ):
            logger.error("💥 文本模式Session重建失败，放弃本次数据流")
            return False
        return True

    async def _process_stream_data_internal(self, message: dict):
        """Internal method: the actual stream_data processing logic"""
        data = message.get("data")
        input_type = message.get("input_type")
        if self._should_drop_live_vision_stream(input_type):
            return
        # 检查session是否发生致命错误（如1011错误、Response timeout）
        if (
            input_type != "audio"
            and self.session
            and isinstance(self.session, OmniRealtimeClient)
        ):
            if hasattr(self.session, '_fatal_error_occurred') and self.session._fatal_error_occurred:
                logger.warning("⚠️ Session已发生致命错误，忽略新的输入数据")
                return
        
        # 如果正在启动session，这不应该发生（因为stream_data已经检查过了）
        if self._starting_session_count > 0:
            logger.debug("Session正在启动中，跳过...")
            return

        # 如果 session 不存在或不活跃，检查是否可以自动重建
        if not self.session or not self.is_active:
            if input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
                return
            # Memory Server 专属冷却检查
            if self._emit_cooldown_turn_end_if_needed():
                return
            # 失败上限保护：start_session 内部熔断会早退，这里再加一层是为了
            # 不让 stream 路径每个包都打"Session 不存在"info 日志，省日志开销。
            if self._session_start_circuit_open:
                return

            logger.info(f"Session 不存在或未激活，根据输入类型 {input_type} 自动创建 session")
            # 检查WebSocket状态
            ws_exists = self.websocket is not None
            if ws_exists:
                has_state = hasattr(self.websocket, 'client_state')
                if has_state:
                    logger.info(f"  └─ WebSocket状态: exists=True, state={self.websocket.client_state}")
                    # 进一步检查连接状态
                    if self.websocket.client_state != self.websocket.client_state.CONNECTED:
                        logger.error(f"  └─ WebSocket未连接，状态: {self.websocket.client_state}")
                        self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                        return
                else:
                    logger.warning("  └─ WebSocket状态: exists=True, 但没有client_state属性!")
            else:
                logger.error("  └─ WebSocket状态: exists=False! 连接可能已断开，请刷新页面")
                # 通过sync_message_queue发送错误提示
                self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                return
            
            # 根据输入类型确定模式
            mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
            await self.start_session(self.websocket, new=False, input_mode=mode)
            
            # 检查启动是否成功
            if not self.session or not self.is_active:
                logger.warning("⚠️ Session启动失败，放弃本次数据流")
                return
        
        defer_pending_flush = False
        try:
            validated_one_shot_image_b64 = None
            if input_type == "text" and not isinstance(data, str):
                logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            if input_type in {"avatar_drop_image", "user_image"}:
                if self._should_drop_magic_command_image(message.get("request_id")):
                    return
                # Validate one-shot attachments before the destructive realtime
                # -> offline handoff. A malformed/oversized attachment must not
                # tear down an otherwise healthy voice session only to be
                # rejected a few lines later.
                try:
                    validated_one_shot_image_b64 = (
                        await _core_facade.process_screen_data(data)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing image data: {e}")
                    return
                if not validated_one_shot_image_b64:
                    logger.error("💥 Stream: 图像数据验证失败")
                    return

                if not isinstance(self.session, OmniOfflineClient):
                    self._deferred_pending_input_flush_count = (
                        getattr(self, "_deferred_pending_input_flush_count", 0)
                        + 1
                    )
                    defer_pending_flush = True

            if input_type == "text" and not isinstance(self.session, OmniOfflineClient):
                # 纯文本同样是这次 handoff 的发起者，要拿同一把 owner-before-flush
                # 的锁。end_session 现在会保留拆 session 期间缓存进来的输入
                # （preserve_pending_input），而 start_session 结束前就会 flush 它们
                # —— 这条**发起**本次 handoff 的消息却要等本函数往下走才提交。不挡
                # 一下的话，后到的那条先进历史、先生成，随后这条更早的消息再把它打
                # 断，用户的两句话顺序就反了。
                self._deferred_pending_input_flush_count = (
                    getattr(self, "_deferred_pending_input_flush_count", 0) + 1
                )
                defer_pending_flush = True

            if input_type in _TEXT_SESSION_INPUT_TYPES:
                if not await self._ensure_offline_session_for_text_input(input_type):
                    return

            if input_type == 'text':
                # 文本模式：直接发送文本
                if isinstance(data, str):
                    memory_text = self._clean_frontend_memory_text(message.get("memory_text"))
                    message_source = str(message.get("source") or "").strip()
                    record_data = memory_text or data
                    # 更新用户活动时间戳（与 handle_input_transcript / _record_external_user_input
                    # 对偶）。idle reset loop 依赖该字段判断静默时长，文本路径不补的话
                    # 纯文本会话永远满足"静默 ≥ 30 min"被误重置。
                    _user_input_time = self._user_input_ingress_time(message)
                    _last_activity_time = getattr(
                        self,
                        "last_user_activity_time",
                        None,
                    )
                    self.last_user_activity_time = (
                        max(float(_last_activity_time), _user_input_time)
                        if isinstance(_last_activity_time, (int, float))
                        else _user_input_time
                    )
                    # 「真消息」时间戳：strip 后非空才刷，与语音路径
                    # `if transcript_text:` 对偶——空白输入不算真实回应，否则会误
                    # 推进 mini-game 邀请隐式 dismiss 判定（CodeRabbit）。注意
                    # last_user_activity_time 仍无条件刷（服务 idle reset，语义是
                    # 「有没有发请求」，与「是不是真消息」不同）。
                    if record_data.strip():
                        _last_message_time = getattr(
                            self,
                            "last_user_message_time",
                            None,
                        )
                        self.last_user_message_time = (
                            max(float(_last_message_time), _user_input_time)
                            if isinstance(_last_message_time, (int, float))
                            else _user_input_time
                        )
                        self.note_user_engagement(at=_user_input_time)

                    # 更新字数限制（可能用户在对话期间修改了设置）
                    if hasattr(self.session, 'update_max_response_length'):
                        self.session.update_max_response_length(self._get_text_guard_max_length())

                    # 先打断当前正在播放的语音（旧speech_id），避免误打断新回复
                    async with self.lock:
                        interrupted_speech_id = self.current_speech_id

                    # 再停掉**产出方**：offline 会话可能正跑着一轮独立 ASR 的
                    # external turn（_external_voice_submit_task），或一轮还没收完
                    # 的普通文本响应。下面就要轮换 speech_id，不先取消的话那条流
                    # 会继续吐 delta，全部挂到这条新消息的 sid 上；两条流还共用
                    # _is_responding，先收尾的那条把它翻 False，另一条被截断。
                    # 与独立 ASR 准备回合前那次 handle_interruption() 同一判据。
                    _interrupt = getattr(self.session, "handle_interruption", None)
                    if callable(_interrupt):
                        try:
                            await _interrupt()
                        except asyncio.CancelledError:
                            raise
                        except Exception as _interrupt_error:
                            # 打断是尽力而为：一个坏掉的会话不该把用户刚打的这句话
                            # 一起吞掉。失败时旧流可能继续吐 delta（就是这段要修的
                            # 问题），但比丢消息轻。
                            logger.warning(
                                "[%s] text input could not interrupt the session: %s",
                                self.lanlan_name,
                                _interrupt_error,
                            )

                    self.audio_resampler.clear()
                    await self._clear_tts_pipeline()
                    await self.send_user_activity(interrupted_speech_id)

                    # 再为本次新回复生成新的speech_id（用于TTS和lipsync）
                    async with self.lock:
                        self.current_speech_id = str(uuid4())
                        self._tts_done_queued_for_turn = False
                        self._tts_done_pending_until_ready = False
                        new_user_sid = self.current_speech_id
                        # 与 handle_new_message 同理：sid 写入的同一锁段内同步翻
                        # _preempted，避免 prepare_proactive_delivery 插到 lock
                        # 释放 ~ fire() 之间再覆盖新 user sid。
                        self.state.mark_user_input_preempt()
                    # 状态机：文本模式 stream_text 入口同样需要发射 USER_INPUT。
                    # handle_new_message 只在语音模式走到，这里是文本模式的对偶。
                    await self.state.fire(SessionEvent.USER_INPUT, sid=new_user_sid)
                    # Activity tracker：文本模式真实用户输入。故意不在 handle_new_message
                    # 里挂——后者也被 proactive abort 流程调用做清理（见
                    # main_routers/system_router.py），那不算用户活动。
                    # text 进 buffer 给 emotion-tier 用。
                    self._note_user_turn(text=record_data)
                    # Telemetry：D1 漏斗——本进程首条用户消息（lazy import 防循环）。
                    try:
                        from utils.token_tracker import TokenTracker as _TT
                        _tt = _TT.get_instance()
                        _tt.note_first_user_message("text")
                        # 每条用户消息：user_message_sent counter + 累加 per-session 轮数。
                        # 此处是文本侧 on_user_message 唯一入口，每条真实消息恰好一次。
                        _tt.note_user_message("text")
                    except Exception:
                        # 埋点 best-effort，绝不阻塞用户消息处理；note_first_user_message
                        # 自身幂等，丢一次也不影响 D1 漏斗统计。
                        pass
                    # 与 on_user_message 对偶：把"用户原话"推到插件总线 user-context
                    # bucket。语音路径在 handle_input_transcript 里发布，这里只覆盖
                    # 文本路径，避免与语音入口重复发布。
                    self._publish_user_utterance_to_plugin_bus(
                        record_data,
                        is_voice_source=False,
                    )

                    # Mini-game 邀请的关键词文本兜底（PR #1141 follow-up E2）。
                    # 用户在 pending 邀请期间自己打字（没点 ChoicePrompt 三按钮）
                    # → 扫关键词命中就触发对应 state 转换。与语音转写路径
                    # （handle_input_transcript）共用同一方法，逻辑见
                    # _dispatch_mini_game_invite_keyword。
                    await self._dispatch_mini_game_invite_keyword(
                        record_data,
                    )

                    openclaw_magic_command = self._normalize_explicit_openclaw_magic_command(data)
                    if (
                        openclaw_magic_command
                        and self._is_agent_enabled()
                        and self.agent_flags.get("openclaw_enabled", False)
                        and self.agent_flags.get("openclaw_ready", False)
                    ):
                        self._session_turn_count += 1
                        self._clear_text_pending_images()
                        self._mark_magic_command_image_drop_request(message.get("request_id"))
                        await self.mirror_user_input(
                            data,
                            metadata={
                                "source": "openclaw",
                                "kind": "magic_command",
                                "command": openclaw_magic_command,
                            },
                            request_id=message.get("request_id"),
                        )
                        await self._emit_agent_callback_turn_end(message.get("request_id"))
                        self._fire_task(self._publish_openclaw_magic_command(openclaw_magic_command))
                        logger.info("[%s] text input sent explicit openclaw magic command", self.lanlan_name)
                        return

                    # 文本模式：把挂起的 agent 任务回调**就地拼到本轮 user
                    # message 的 content 前缀**——LLM 把它当作"用户当前发声那
                    # 一刻附带的额外上下文"，在同一轮回答里自然提及，不再起
                    # 独立 turn（issue #1033）。drain 出来的字符串已含
                    # ``======[系统通知] 来自xxx的xxx======`` watermark，LLM
                    # 看得出来是 system notice 而不是用户原话。
                    #
                    # 与 voice mode 的对偶：``prime_context(skipped=False)`` 在
                    # GPT/GLM/Step 上同样走 ``create_response`` 把 callback
                    # 注入成 user role 消息，offline 这边 inline 进 user
                    # content 跟那条路径语义一致——callback 文本随 user message
                    # 进 transcript 持久化（issue 旧注释里担忧的"持久化污染"作
                    # 废，passive callback 跟用户输入一起留在 history 让 AI
                    # 后续仍能 reference）。
                    #
                    # best-effort 注入：drain 的 ``finally clear`` 是 PR #1032
                    # 的设计决定（passive=单次软通知），即便 drain 或 stream_text
                    # 失败也不回填——延续到这条路径仍是这样，不在 caller 加
                    # snapshot 回滚。
                    _agent_cb_ctx = ""
                    _agent_cb_images = []
                    _agent_cb_media_drained = []
                    # ⚠️ 必须在外层 try **之前**绑定：回滚发生在 drain 之后的任意
                    # 一个 await 上，包括早于下面赋值点的那些。定义在 try 内部的话，
                    # 早期取消会让 except 里读到未绑定的名字，回滚静默失效。
                    _cb_turn_committed = False
                    if self.pending_agent_callbacks:
                        callbacks_snapshot = self._claim_agent_callbacks_for_llm()
                        try:
                            _passive_media_outcome = await self._stage_passive_callback_media(
                                callbacks_snapshot,
                                self.session,
                            )
                            _agent_cb_ctx = (
                                self.drain_agent_callbacks_for_llm(
                                    callbacks_snapshot
                                )
                                or ""
                            )
                            if _agent_cb_ctx:
                                _agent_cb_images = list(
                                    _passive_media_outcome.get(
                                        "system_prefix_images",
                                        [],
                                    )
                                )
                                # 记下**真正被 drain 掉的、带图的**那些 callback。
                                # 下面这轮如果在 user message 落 history 之前就抛
                                # 了（offline 切 vision model 会新建 LLM 客户端，
                                # 网络抖 / key 失效都会抛），文本和图一起消失且已
                                # 报告投递成功，再也不会重试。
                                _still_queued = {
                                    id(cb) for cb in self.pending_agent_callbacks
                                }
                                _agent_cb_media_drained = [
                                    cb
                                    for cb in callbacks_snapshot
                                    if isinstance(cb, dict)
                                    and cb.get("media_images")
                                    and id(cb) not in _still_queued
                                ]
                        except Exception as _cb_err:
                            logger.warning(f"⚠️ Agent callback drain failed: {_cb_err}")
                            _agent_cb_ctx = ""
                        finally:
                            self._release_agent_callback_prompt_claims(
                                callbacks_snapshot
                            )

                    try:
                        text_request_id = message.get("request_id")
                        self._active_text_request_id = text_request_id
                        # Path A (inline) Focus 凝神：score this user message and, if
                        # over the bar, run THIS reply thinking-on. Scored on
                        # ``record_data`` (= memory_text or data) — the user-VISIBLE
                        # text that also feeds the activity tracker / cadence baseline
                        # and history replacement. Scoring raw ``data`` instead would
                        # read a hidden scaffold prompt (e.g. avatar-drop file
                        # contents) the user never typed, mismatching the cadence
                        # signal and entering Focus on evidence the user didn't author.
                        _focus_thinking = await self._focus_inline_decision(record_data)

                        async def response_discarded_callback(
                            reason: str,
                            attempt: int,
                            max_attempts: int,
                            will_retry: bool,
                            discard_message: str | None = None,
                            *,
                            _request_id=text_request_id,
                        ) -> None:
                            await self.handle_response_discarded(
                                reason,
                                attempt,
                                max_attempts,
                                will_retry,
                                discard_message,
                                request_id=_request_id,
                            )

                        input_transcript_callback = None
                        if memory_text:
                            transcript_metadata = {"source": message_source} if message_source else None

                            async def input_transcript_callback(
                                _transcript: str,
                                *,
                                _memory_text: str = memory_text,
                                _message_source: str = message_source,
                                _transcript_metadata: dict | None = transcript_metadata,
                            ) -> None:
                                await self.handle_input_transcript(
                                    _memory_text,
                                    is_voice_source=False,
                                    source=_message_source,
                                    metadata=_transcript_metadata,
                                )

                        stream_text_kwargs = {
                            "system_prefix": _agent_cb_ctx or None,
                            "thinking_on": _focus_thinking,
                            "response_discarded_callback": response_discarded_callback,
                        }
                        def _mark_cb_turn_committed() -> None:
                            nonlocal _cb_turn_committed
                            _cb_turn_committed = True

                        if _agent_cb_images:
                            stream_text_kwargs["system_prefix_images"] = _agent_cb_images
                        if _agent_cb_media_drained:
                            # 装载判据必须跟下面回滚的判据**是同一个**。回滚看的是
                            # _agent_cb_media_drained（带图且已出队的 callback），
                            # 按 _agent_cb_images 装的话，两者一旦分叉，那一轮就没
                            # 人置 _cb_turn_committed：stream_text 把文字写进
                            # history 之后再抛，外层回滚会认定"没提交过"而把
                            # callback 放回队列，下一轮重复投递同一条通知。
                            #
                            # 今天这两个集合在 Offline 上是同进同出的——staging 的
                            # _renderable 截断、预算延后标志、drain 的 STOP 判据三
                            # 者对齐，凡是被 drain 摘走的带图 callback 都拿得到图，
                            # 所以现在**构造不出**上面那个分叉（Codex P2 提的场景
                            # 我没能复现）。改成按回滚判据装，是不让这个"同进同出"
                            # 变成隐式前提：它由三处独立代码共同维持，任一处以后
                            # 松动，分叉就会以"重复投递"的形式出现在用户面前，而
                            # 那时没有任何断言会先红。
                            #
                            # 本次调用自己的「已进 history」标记。不能拿全局 history
                            # 长度判断：并发的另一条文本请求同样会追加。
                            stream_text_kwargs["on_turn_committed"] = (
                                _mark_cb_turn_committed
                            )
                        if input_transcript_callback:
                            stream_text_kwargs["input_transcript_callback"] = input_transcript_callback
                        if memory_text:
                            stream_text_kwargs["history_replacement_text"] = memory_text
                        try:
                            if _focus_thinking:
                                # 凝神 turn runs thinking-on: pre-pulse the frontend so
                                # the bubble shows up the instant the turn starts
                                # (immediate feedback), before any reasoning chunk
                                # arrives. Idempotent and harmless — a non-Focus turn
                                # instead pulses lazily from
                                # OmniOfflineClient.on_thinking_active on its first
                                # reasoning chunk (handle_thinking_active). Either way
                                # the bubble clears on the first visible token
                                # (send_lanlan_response) or in the finally below.
                                #
                                # ⚠️ 这个 True 必须在 try **之内**：它自己就会等
                                # websocket 锁 / send_json，拆除时正好卡在这里被取消
                                # 的话，_focus_thinking_active 已经置上、通知已经入队，
                                # 而清理永远不会执行，气泡就一直亮到下一轮偶然把它关掉。
                                await self._push_focus_thinking(True)
                            await self.session.stream_text(data, **stream_text_kwargs)
                        finally:
                            # Clear unconditionally: a non-Focus turn may have pulsed the
                            # bubble True via the reasoning callback, so gating the clear
                            # on _focus_thinking would leave it stuck on tool-only / empty
                            # / error turns. _push_focus_thinking is idempotent, so a no-op
                            # clear when nothing pulsed costs nothing.
                            await self._push_focus_thinking(False)
                    except BaseException:
                        # drain 之后到本轮进 history 之间的**每一个** await 都要
                        # 覆盖，不只是 stream_text：会话拆除时这条输入任务可能在
                        # _focus_inline_decision / _push_focus_thinking(True) 里就
                        # 被取消，那时 callback 已经摘队并报告投递成功，文本和图会
                        # 一起永久消失。
                        #
                        # 仍然只在**这一轮没进 history** 时回滚：已提交之后的失败
                        # 属于既有的 best-effort 契约（内容已经在模型眼前了），
                        # 回滚反而会重复投递。
                        if _agent_cb_media_drained and not _cb_turn_committed:
                            self._requeue_undelivered_callback_media(
                                _agent_cb_media_drained
                            )
                        raise
                else:
                    logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            
            if input_type in _IMAGE_INPUT_TYPES:
                try:
                    if self._should_drop_magic_command_image(message.get("request_id")):
                        return
                    image_arrival_time = (
                        self._user_input_ingress_time(message)
                        if input_type in {"avatar_drop_image", "user_image"}
                        else None
                    )
                    # 使用统一的图像工具处理数据（只验证，不缩放）
                    image_b64 = (
                        validated_one_shot_image_b64
                        if input_type in {"avatar_drop_image", "user_image"}
                        else await _core_facade.process_screen_data(data)
                    )

                    if image_b64:
                        image_accepted = False
                        # 叠加 Avatar 文字注解（仅当本条消息携带了位置元数据时）
                        # 不回退到 self._avatar_position：前端未附带位置说明该截图不应叠加
                        # （如窗口截图、手机相机等场景）
                        av_pos = message.get('avatar_position') if input_type in {"screen", "camera"} else None
                        if av_pos and isinstance(av_pos, dict):
                            try:
                                image_b64 = await asyncio.to_thread(
                                    overlay_avatar_annotation,
                                    image_b64, av_pos, self.lanlan_name,
                                    get_global_language_full(),
                                )
                            except Exception as ann_err:
                                logger.warning("[%s] avatar annotation failed, sending original: %s",
                                               self.lanlan_name, ann_err)

                        independent_live_frame = (
                            input_type in _LIVE_VISION_STREAM_INPUT_TYPES
                            and getattr(self, "_asr_route_mode", "blocked")
                            == "independent"
                        )
                        if independent_live_frame:
                            captured_at = message.get("_visual_input_ingress_time")
                            image_accepted = self._stage_independent_visual_frame(
                                image_b64,
                                source=input_type,
                                request_id=message.get("request_id"),
                                captured_at=captured_at,
                            )
                            # Keep the active Realtime adapter's provider-neutral
                            # latest-frame cache warm for proactive observation,
                            # without sending the image to the provider. Once a
                            # handoff promotes Offline, Core remains the sole owner
                            # of live frames and they never leak into its attachment
                            # queue.
                            stage_session_frame = getattr(
                                self.session,
                                "stage_multimodal_frame",
                                None,
                            )
                            if image_accepted and callable(stage_session_frame):
                                try:
                                    stage_session_frame(
                                        image_b64,
                                        source=input_type,
                                        request_id=message.get("request_id"),
                                        captured_at=captured_at,
                                    )
                                except asyncio.CancelledError:
                                    raise
                                except Exception as stage_error:
                                    logger.warning(
                                        "[%s] Realtime multimodal frame cache update failed: %s",
                                        self.lanlan_name,
                                        stage_error,
                                    )

                        # 如果是文本模式（OmniOfflineClient），只存储图片，不立即发送
                        elif isinstance(self.session, OmniOfflineClient):
                            # 只添加到待发送队列，等待与文本一起发送
                            await self.session.stream_image(image_b64)
                            image_accepted = True
                            image_data = (
                                ""
                                if input_type in {"avatar_drop_image", "user_image"}
                                else f"data:image/jpeg;base64,{image_b64}"
                            )
                            image_message = {
                                "input_type": input_type,
                                "data": image_data,
                                "has_image": True,
                                "mime_type": "image/jpeg",
                            }
                            message_source = str(message.get("source") or "").strip()
                            if message_source:
                                image_message["source"] = message_source
                            if message.get("request_id"):
                                image_message["request_id"] = message.get("request_id")
                            self.sync_message_queue.put({
                                "type": "user",
                                "data": image_message,
                            })

                        # 如果是语音模式（OmniRealtimeClient），检查是否支持视觉并直接发送
                        elif isinstance(self.session, OmniRealtimeClient):
                            # 检查WebSocket连接
                            if not hasattr(self.session, 'ws') or not self.session.ws:
                                logger.error("💥 Stream: Session websocket not available")
                                return

                            # screen/camera are live environmental frames. The
                            # Realtime session owns whether they are delivered
                            # natively or staged for external vision, and needs
                            # their source/request identity to bind a fixed
                            # generation to the matching independent-ASR turn.
                            # One-shot avatar/chat attachments retain the
                            # pre-existing text/offline contract above.
                            if input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
                                stage_result = await self.session.stream_image(
                                    image_b64,
                                    source=input_type,
                                    request_id=message.get("request_id"),
                                    captured_at=message.get(
                                        "_visual_input_ingress_time"
                                    ),
                                )
                                image_accepted = bool(
                                    getattr(stage_result, "accepted", False)
                                )
                            else:
                                logger.info(
                                    "[%s] one-shot image kept out of realtime "
                                    "visual staging: input_type=%s",
                                    self.lanlan_name,
                                    input_type,
                                )
                        if (
                            image_accepted
                            and image_arrival_time is not None
                        ):
                            self.note_user_engagement(at=image_arrival_time)
                    else:
                        logger.error("💥 Stream: 图像数据验证失败")
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing image data: {e}")
                    return

        except web_exceptions.ConnectionClosedError as e:
            logger.error(f"💥 Stream: Error sending data to session: {e}")
            if '1011' in str(e):
                await self.send_status(json.dumps({"code": "ERROR_1011_MIC_CHECK"}))
            if '1007' in str(e):
                await self.send_status(json.dumps({"code": "ERROR_1007_ARREARS"}))
            await self.disconnected_by_server()
            return
        except Exception as e:
            error_message = f"Stream: Error sending data to session: {e}"
            logger.error(f"💥 {error_message}")
            await self.send_status(json.dumps({"code": "API_UNKNOWN_ERROR", "details": {"msg": error_message}}))
        finally:
            if defer_pending_flush:
                self._deferred_pending_input_flush_count = max(
                    0,
                    getattr(self, "_deferred_pending_input_flush_count", 1) - 1,
                )
                if (
                    self._deferred_pending_input_flush_count == 0
                    and self.is_active
                    and isinstance(self.session, OmniOfflineClient)
                ):
                    try:
                        await self._flush_pending_input_data()
                    except asyncio.CancelledError:
                        raise
                    except Exception as flush_error:
                        logger.error(
                            "💥 deferred attachment input flush failed: %s",
                            flush_error,
                        )
