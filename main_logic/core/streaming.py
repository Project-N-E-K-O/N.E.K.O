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
import struct
import time
from websockets import exceptions as web_exceptions
from utils.screenshot_utils import overlay_avatar_annotation
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionEvent
from main_logic.asr_client.lifecycle_contracts import VoiceIngressToken
from utils.language_utils import get_global_language_full
from uuid import uuid4
from ._shared import (
    _TEXT_SESSION_INPUT_TYPES,
    _IMAGE_INPUT_TYPES,
    _LIVE_VISION_STREAM_INPUT_TYPES,
    logger,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class StreamingMixin:
    """Live input streaming methods (see module docstring)."""

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
        async with self.input_cache_lock:
            if not self.pending_input_data:
                return

            if self.session and self.is_active:
                # 缓存阶段（_stream_data_now）不知道 session 最终是 voice 还是
                # text。如果最终启好的是 voice session，缓存里的 text 输入若
                # 直接 flush 进 _process_stream_data_internal，会触发 4977-4995
                # 的"硬撕 voice → 重建 text"自动切换路径，把刚 ready 的 voice
                # session 撕成 CHARACTER_LEFT / "角色离开"——这是用户在切音色
                # 后开语音、麦启动期打字的典型 race。这里只防御 text → voice
                # 这一条不对偶的路径；screen / camera 等 vision 输入会在
                # _process_stream_data_internal 里路由到
                # OmniRealtimeClient.stream_image（5262-5278），是 voice session
                # 的合法路径，不能误丢。audio 在 _stream_data_now 缓存阶段已经
                # 直接 return 不缓存，pending_input_data 不会出现 audio。
                is_voice_session = isinstance(self.session, OmniRealtimeClient)
                dropped_text_for_voice = 0
                for message in self.pending_input_data:
                    msg_input_type = message.get("input_type")
                    try:
                        # 重新调用stream_data处理缓存的数据
                        # 注意：这里直接处理，不再缓存（因为session_ready已设为True）
                        if msg_input_type == "audio":
                            await self._enqueue_audio_stream_data(message)
                        else:
                            if is_voice_session and msg_input_type in _TEXT_SESSION_INPUT_TYPES:
                                dropped_text_for_voice += 1
                                continue
                            await self._process_stream_data_internal(message)
                    except Exception as e:
                        logger.error(f"💥 发送缓存的输入数据失败: {e}")
                        break
                if dropped_text_for_voice:
                    logger.info(
                        "[%s] _flush_pending_input_data: dropped %d cached text "
                        "message(s) because final session is voice mode",
                        self.lanlan_name, dropped_text_for_voice,
                    )

            # 清空缓存
            self.pending_input_data.clear()
    
    async def _flush_hot_swap_audio_cache(self):
        """After hot-swap completes, push cached audio data to the new session in a loop until the cache is stably empty"""
        # 设置标志，让新的音频继续缓存而不是直接发送
        self.is_flushing_hot_swap_cache = True
        
        try:
            # 检查session是否可用
            if not self.session or not self.is_active:
                logger.warning("⚠️ 热切换音频缓存刷新时session不可用，丢弃缓存")
                async with self.hot_swap_cache_lock:
                    self.hot_swap_audio_cache.clear()
                return
            
            # 检查session类型
            if not isinstance(self.session, OmniRealtimeClient):
                logger.debug("热切换音频缓存仅适用于语音模式，当前session类型不匹配，跳过flush")
                async with self.hot_swap_cache_lock:
                    self.hot_swap_audio_cache.clear()
                return
            
            max_iterations = 20  # 最多迭代20次，防止无限循环
            iteration = 0
            total_chunks_sent = 0
            
            logger.info("🔄 开始循环推送热切换音频缓存...")
            
            while iteration < max_iterations:
                # 检查并取出当前缓存
                async with self.hot_swap_cache_lock:
                    cache_len = len(self.hot_swap_audio_cache)
                    
                    if cache_len == 0:
                        break
                    else:
                        audio_chunks = self.hot_swap_audio_cache.copy()
                        self.hot_swap_audio_cache.clear()
                
                # 如果有缓存，合并并发送
                if cache_len > 0:
                    logger.info(f"🔄 推送第{iteration+1}批音频缓存: {cache_len} 个chunk")
                    
                    # 合并小chunk成大chunk（节流）
                    combined_audio = b''.join(audio_chunks)
                    
                    # 计算每个大chunk的大小（16kHz，约10ms = 160 samples = 320 bytes）
                    original_chunk_size = 320  # 16kHz: 160 samples × 2 bytes
                    large_chunk_size = original_chunk_size * self.HOT_SWAP_FLUSH_CHUNK_MULTIPLIER
                    
                    # 分批发送
                    for i in range(0, len(combined_audio), large_chunk_size):
                        chunk = combined_audio[i:i + large_chunk_size]
                        try:
                            await self._route_microphone_audio(
                                chunk,
                                sample_rate_hz=16_000,
                            )
                            await asyncio.sleep(0.025)
                            total_chunks_sent += 1
                        except Exception as e:
                            logger.error(f"💥 推送音频缓存失败: {e}")
                            return  # 推送失败，放弃
                
                iteration += 1
                
            if iteration >= max_iterations:
                logger.warning(f"⚠️ 达到最大迭代次数({max_iterations})，停止推送")
            
            logger.info(f"✅ 热切换音频缓存推送完成，共推送约 {total_chunks_sent} 个大chunk，迭代 {iteration} 次")
            
        finally:
            # 无论如何都要清除flag，恢复正常音频输入
            self.is_flushing_hot_swap_cache = False
    
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

    async def _stream_data_now(
        self,
        message: dict,
        *,
        ingress_token: VoiceIngressToken | None = None,
    ):
        input_type = message.get("input_type")
        if self._should_drop_live_vision_stream(input_type):
            return
        if (
            input_type == "audio"
            and ingress_token is not None
            and not self._ingress_token_matches(ingress_token)
        ):
            return
        # 检查session是否就绪
        async with self.input_cache_lock:
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
        await self._process_stream_data_internal(
            message,
            ingress_token=ingress_token,
        )
    
    async def _process_stream_data_internal(
        self,
        message: dict,
        *,
        ingress_token: VoiceIngressToken | None = None,
    ):
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
        
        try:
            if input_type == 'text':
                # 文本模式：检查 session 类型是否正确
                if not isinstance(self.session, OmniOfflineClient):
                    # 检查是否允许重建session
                    if self.session_start_failure_count >= self.session_start_max_failures:
                        logger.error("💥 Session类型不匹配，但失败次数过多，已停止自动重建")
                        return
                    
                    logger.info(f"文本模式需要 OmniOfflineClient，但当前是 {type(self.session).__name__}. 自动重建 session。")
                    # 占用 _starting_session_count guard 跨过 end_session 窗口期。
                    # 默认 end_session(reset_starting_count=True) 会把 guard 清零；
                    # 它内部又有多个 await 拆 session，期间另一条 _stream_data_now
                    # （比如 audio worker 拉到下一包）看到 session=None / count=0 会
                    # 从 4941-4953 的 auto-create 分支抢跑 start_session(audio)，
                    # 等本路径走到 await self.start_session(text) 时命中 2776 的
                    # "Session正在启动中" guard 被静默忽略，重建静默失败
                    # （ERROR "💥 文本模式Session重建失败"）。
                    #
                    # 同时把 session_ready 提前置 False，与 start_session 2867-2868
                    # 的初始化对偶：rebuild 期间若 session_ready 仍是 True，并发
                    # _stream_data_now 跳过 4926-4938 的 cache 分支（条件为
                    # not session_ready），落到 _process_stream_data_internal 后
                    # 命中 4975 的 count>0 早退被 silent drop——用户在 rebuild
                    # 窗口内打的字直接丢失。提前置 False 让 cache 路径接住，
                    # rebuild 完成后 _flush_pending_input_data 会 flush 出去。
                    async with self.input_cache_lock:
                        self.session_ready = False
                    self._starting_session_count += 1
                    self._starting_input_mode = 'text'
                    try:
                        if self.session:
                            await self.end_session(reset_starting_count=False)
                    finally:
                        self._starting_session_count = max(0, self._starting_session_count - 1)
                        if self._starting_session_count == 0:
                            self._starting_input_mode = None
                    # 释放 guard 与下面的 start_session 之间禁止 await，否则窗口
                    # 重新打开。start_session 入口的 +=1 (2781) 之前都是同步代码，
                    # 函数调用本身不让出控制权，安全。
                    await self.start_session(self.websocket, new=False, input_mode='text')

                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniOfflineClient):
                        logger.error("💥 文本模式Session重建失败，放弃本次数据流")
                        return
                
                # 文本模式：直接发送文本
                if isinstance(data, str):
                    memory_text = self._clean_frontend_memory_text(message.get("memory_text"))
                    message_source = str(message.get("source") or "").strip()
                    record_data = memory_text or data
                    # 更新用户活动时间戳（与 handle_input_transcript / _record_external_user_input
                    # 对偶）。idle reset loop 依赖该字段判断静默时长，文本路径不补的话
                    # 纯文本会话永远满足"静默 ≥ 30 min"被误重置。
                    self.last_user_activity_time = time.time()
                    # 「真消息」时间戳：strip 后非空才刷，与语音路径
                    # `if transcript_text:` 对偶——空白输入不算真实回应，否则会误
                    # 推进 mini-game 邀请隐式 dismiss 判定（CodeRabbit）。注意
                    # last_user_activity_time 仍无条件刷（服务 idle reset，语义是
                    # 「有没有发请求」，与「是不是真消息」不同）。
                    if record_data.strip():
                        self.last_user_message_time = time.time()

                    # 更新字数限制（可能用户在对话期间修改了设置）
                    if hasattr(self.session, 'update_max_response_length'):
                        self.session.update_max_response_length(self._get_text_guard_max_length())

                    # 先打断当前正在播放的语音（旧speech_id），避免误打断新回复
                    async with self.lock:
                        interrupted_speech_id = self.current_speech_id

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
                    if self.pending_agent_callbacks:
                        try:
                            _agent_cb_ctx = self.drain_agent_callbacks_for_llm() or ""
                        except Exception as _cb_err:
                            logger.warning(f"⚠️ Agent callback drain failed: {_cb_err}")
                            _agent_cb_ctx = ""

                    self._active_text_request_id = message.get("request_id")
                    # Path A (inline) Focus 凝神：score this user message and, if
                    # over the bar, run THIS reply thinking-on. Scored on
                    # ``record_data`` (= memory_text or data) — the user-VISIBLE
                    # text that also feeds the activity tracker / cadence baseline
                    # and history replacement. Scoring raw ``data`` instead would
                    # read a hidden scaffold prompt (e.g. avatar-drop file
                    # contents) the user never typed, mismatching the cadence
                    # signal and entering Focus on evidence the user didn't author.
                    _focus_thinking = await self._focus_inline_decision(record_data)
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
                    }
                    if input_transcript_callback:
                        stream_text_kwargs["input_transcript_callback"] = input_transcript_callback
                    if memory_text:
                        stream_text_kwargs["history_replacement_text"] = memory_text
                    if _focus_thinking:
                        # 凝神 turn runs thinking-on: pre-pulse the frontend so the
                        # bubble shows up the instant the turn starts (immediate
                        # feedback), before any reasoning chunk arrives. Idempotent
                        # and harmless — a non-Focus turn instead pulses lazily from
                        # OmniOfflineClient.on_thinking_active on its first reasoning
                        # chunk (handle_thinking_active). Either way the bubble clears
                        # on the first visible token (send_lanlan_response) or in the
                        # unconditional finally below.
                        await self._push_focus_thinking(True)
                    try:
                        await self.session.stream_text(data, **stream_text_kwargs)
                    finally:
                        # Clear unconditionally: a non-Focus turn may have pulsed the
                        # bubble True via the reasoning callback, so gating the clear
                        # on _focus_thinking would leave it stuck on tool-only / empty
                        # / error turns. _push_focus_thinking is idempotent, so a no-op
                        # clear when nothing pulsed costs nothing.
                        await self._push_focus_thinking(False)
                else:
                    logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            
            # 麦克风 PCM 只进入独立 ASR，Core 会话类型不参与音频路由。
            if input_type == 'audio':
                if (
                    ingress_token is not None
                    and not self._ingress_token_matches(ingress_token)
                ):
                    return
                if getattr(self, "_asr_route_mode", "independent") not in {
                    "independent",
                    "blocked",
                }:
                    raise RuntimeError("VOICE_ROUTE_MODE_INVALID")

                session_ref = self.session
                audio_epoch = self._audio_stream_epoch
                # Microphone preprocessing and routing are independent from the
                # Core/Omni transport. A missing Omni WSS must not intercept PCM.
                try:
                    if not isinstance(data, list):
                        logger.error(
                            "Microphone input rejected: expected a PCM sample list"
                        )
                        return
                    audio_bytes = struct.pack(f'<{len(data)}h', *data)
                    declared_rate_hz = message.get("sample_rate_hz")
                    if declared_rate_hz is None:
                        # 兼容旧版 JSON PCM 帧；新版二进制协议必须显式声明。
                        source_rate_hz = 48_000 if len(data) == 480 else 16_000
                    elif declared_rate_hz in {16_000, 48_000}:
                        source_rate_hz = int(declared_rate_hz)
                    else:
                        logger.error(
                            "Microphone input rejected: unsupported sample rate %r",
                            declared_rate_hz,
                        )
                        return
                    processed_frame = await self._process_microphone_audio(
                        audio_bytes,
                        sample_rate_hz=source_rate_hz,
                    )
                    if not processed_frame.pcm16:
                        return
                    if (
                        (
                            ingress_token is not None
                            and not self._ingress_token_matches(ingress_token)
                        )
                        or
                        self.session is not session_ref
                        or not self.is_active
                        or self._audio_stream_epoch != audio_epoch
                    ):
                        return
                    if self.is_hot_swap_imminent or self.is_flushing_hot_swap_cache:
                        async with self.hot_swap_cache_lock:
                            self.hot_swap_audio_cache.append(processed_frame.pcm16)
                        return
                    if (
                        ingress_token is not None
                        and not self._ingress_token_matches(ingress_token)
                    ):
                        return
                    await self._route_microphone_audio(
                        processed_frame.pcm16,
                        sample_rate_hz=processed_frame.sample_rate_hz,
                        speech_probability=processed_frame.speech_probability,
                    )
                    return
                except struct.error:
                    logger.error("Microphone input rejected: invalid PCM samples")
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error(
                        "Microphone preprocessing or independent ASR routing failed"
                    )
                    return

            elif input_type in _IMAGE_INPUT_TYPES:
                try:
                    if self._should_drop_magic_command_image(message.get("request_id")):
                        return
                    # 使用统一的图像工具处理数据（只验证，不缩放）
                    image_b64 = await _core_facade.process_screen_data(data)

                    if image_b64:
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

                        # 如果是文本模式（OmniOfflineClient），只存储图片，不立即发送
                        if isinstance(self.session, OmniOfflineClient):
                            # 只添加到待发送队列，等待与文本一起发送
                            await self.session.stream_image(image_b64)
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

                            # 语音模式直接发送图片
                            await self.session.stream_image(image_b64)
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
