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
"""Greeting flows for ``LLMSessionManager``: session/cat/new-character
greetings and avatar interaction handling.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import time
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionEvent
from config.prompts.avatar_interaction_contract import (
    normalize_avatar_interaction_payload,
)
from config.prompts.prompts_avatar_interaction import (
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
    _sanitize_avatar_interaction_text_context,
)
from utils.config_manager import get_config_manager
from utils.language_utils import normalize_language_code, get_global_language
from uuid import uuid4
from ._shared import logger, _proactive_expected_sid


class GreetingMixin:
    """Greeting and avatar-interaction methods (see module docstring)."""

    def _remember_avatar_interaction_id(self, interaction_id: str) -> None:
        if interaction_id in self._recent_avatar_interaction_id_set:
            return
        if self._recent_avatar_interaction_ids.maxlen and len(self._recent_avatar_interaction_ids) >= self._recent_avatar_interaction_ids.maxlen:
            oldest_id = self._recent_avatar_interaction_ids[0]
            self._recent_avatar_interaction_id_set.discard(oldest_id)
        self._recent_avatar_interaction_ids.append(interaction_id)
        self._recent_avatar_interaction_id_set.add(interaction_id)

    async def handle_avatar_interaction(self, payload: dict) -> dict:
        raw_interaction_id = str(payload.get("interaction_id") or payload.get("interactionId") or "").strip() if isinstance(payload, dict) else ""
        raw = normalize_avatar_interaction_payload(
            payload,
            sanitize_text_context=_sanitize_avatar_interaction_text_context,
        )
        if not raw:
            logger.debug("[%s] handle_avatar_interaction: ignored invalid payload", self.lanlan_name)
            await self.send_avatar_interaction_ack(raw_interaction_id, False, "invalid_payload")
            return {"accepted": False, "reason": "invalid_payload"}

        interaction_id = raw["interaction_id"]
        now_ms = int(time.time() * 1000)

        if interaction_id in self._recent_avatar_interaction_id_set:
            logger.debug("[%s] handle_avatar_interaction: duplicate interaction_id=%s", self.lanlan_name, interaction_id)
            await self.send_avatar_interaction_ack(interaction_id, False, "duplicate")
            return {"accepted": False, "reason": "duplicate", "interaction_id": interaction_id}

        if now_ms - self._last_avatar_interaction_at < self.avatar_interaction_cooldown_ms:
            logger.debug("[%s] handle_avatar_interaction: cooldown skip interaction_id=%s", self.lanlan_name, interaction_id)
            self._remember_avatar_interaction_id(interaction_id)
            await self.send_avatar_interaction_ack(interaction_id, False, "cooldown")
            return {"accepted": False, "reason": "cooldown", "interaction_id": interaction_id}

        self._remember_avatar_interaction_id(interaction_id)
        self._last_avatar_interaction_at = now_ms

        if self.is_active and isinstance(self.session, OmniRealtimeClient):
            logger.debug("[%s] handle_avatar_interaction: voice session active, skipping", self.lanlan_name)
            await self.send_avatar_interaction_ack(interaction_id, False, "voice_session_active")
            return {"accepted": False, "reason": "voice_session_active", "interaction_id": interaction_id}

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            if not self._has_connected_websocket():
                logger.warning("[%s] handle_avatar_interaction: no connected websocket, skipping", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "no_websocket")
                return {"accepted": False, "reason": "no_websocket", "interaction_id": interaction_id}
            try:
                logger.info("[%s] handle_avatar_interaction: auto-starting text session", self.lanlan_name)
                await self.start_session(self.websocket, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] handle_avatar_interaction: auto start_session failed: %s", self.lanlan_name, e)
                await self.send_avatar_interaction_ack(interaction_id, False, "session_start_failed")
                return {"accepted": False, "reason": "session_start_failed", "interaction_id": interaction_id}

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            logger.warning("[%s] handle_avatar_interaction: session is not text mode after start, skipping", self.lanlan_name)
            await self.send_avatar_interaction_ack(interaction_id, False, "not_text_session")
            return {"accepted": False, "reason": "not_text_session", "interaction_id": interaction_id}

        instruction = _build_avatar_interaction_instruction(
            getattr(self, "user_language", None),
            self.lanlan_name,
            self.master_name,
            raw,
        )
        memory_meta = _build_avatar_interaction_memory_meta(
            getattr(self, "user_language", None),
            raw,
            self.master_name,
        )
        memory_note = memory_meta["memory_note"]
        delivered = False

        async with self._proactive_write_lock:
            if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
                await self.send_avatar_interaction_ack(interaction_id, False, "session_changed")
                return {"accepted": False, "reason": "session_changed", "interaction_id": interaction_id}
            if getattr(self.session, "_is_responding", False):
                logger.debug("[%s] handle_avatar_interaction: text session busy, skipping", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "busy")
                return {"accepted": False, "reason": "busy", "interaction_id": interaction_id}
            speak_now_ms = int(time.time() * 1000)
            if speak_now_ms - self._last_avatar_interaction_speak_at < self.avatar_interaction_speak_cooldown_ms:
                logger.debug("[%s] handle_avatar_interaction: speak cooldown skip interaction_id=%s", self.lanlan_name, interaction_id)
                await self.send_avatar_interaction_ack(interaction_id, False, "speak_cooldown")
                return {"accepted": False, "reason": "speak_cooldown", "interaction_id": interaction_id}

            async with self.lock:
                self.current_speech_id = str(uuid4())
                self._tts_done_queued_for_turn = False

            if hasattr(self.session, 'update_max_response_length'):
                self.session.update_max_response_length(self._get_text_guard_max_length())

            # 后端打标：把 avatar interaction 元数据挂在 session manager 上，
            # 等 prompt_ephemeral 触发 handle_response_complete 时随 turn end
            # 原子地下发。不再走独立的 sync_message_queue 控制消息，避免
            # meta 与 turn end 两条消息时序错乱导致本轮被误判成 proactive。
            self._pending_turn_meta = {
                "kind": "avatar_interaction",
                "interaction_id": interaction_id,
                "memory_note": memory_note,
                "memory_dedupe_key": memory_meta["memory_dedupe_key"],
                "memory_dedupe_rank": memory_meta["memory_dedupe_rank"],
            }

            current_turn_id = self.current_speech_id
            # 主动搭话 race guard：prompt_ephemeral 运行期间若用户发起新输入
            # 会换 current_speech_id + 清 TTS queue，本路径产生的 text delta
            # 必须靠 _proactive_expected_sid 在 handle_text_data/handle_output_transcript
            # 里判同，不一致就丢。和 trigger_agent_callbacks 走同一套保护。
            _sid_token = _proactive_expected_sid.set(current_turn_id)
            try:
                try:
                    delivered = await self.session.prompt_ephemeral(
                        instruction,
                        completion_mode="response",
                        persist_response=False,
                    )
                except Exception as e:
                    logger.exception(
                        "[%s] handle_avatar_interaction: prompt_ephemeral failed interaction_id=%s: %s",
                        self.lanlan_name,
                        interaction_id,
                        e,
                    )
                    # prompt_ephemeral 抛错时 handle_response_complete 不会被触发，
                    # 必须主动清掉 meta，避免泄漏到下一轮。
                    self._pending_turn_meta = None
                    await self.send_avatar_interaction_ack(interaction_id, False, "error")
                    return {"accepted": False, "reason": "error", "interaction_id": interaction_id}
            finally:
                _proactive_expected_sid.reset(_sid_token)

            # Prompt 跑完后若 current_speech_id 已换（用户中途接管），
            # 本轮 avatar 响应算未送达：meta 不该挂到用户的新 turn end 上，
            # ack 也要汇报 interrupted 而非 delivered。
            interrupted = self.current_speech_id != current_turn_id
            accepted = bool(delivered) and not interrupted
            if interrupted:
                self._pending_turn_meta = None
            if accepted:
                self._last_avatar_interaction_speak_at = int(time.time() * 1000)
            ack_reason = "delivered" if accepted else ("interrupted" if interrupted else "empty_response")
            await self.send_avatar_interaction_ack(
                interaction_id,
                accepted,
                ack_reason,
                turn_id=current_turn_id if accepted else "",
            )

        # 未 accepted 时 handle_response_complete 不一定被触发（或者触发在用户
        # 的新 turn 上已被 interrupted 分支清空），留下的 meta 可能被下一轮
        # turn end 误消费；在这里兜底清掉。accepted=True 时 meta 已被
        # handle_response_complete 消费，这里是幂等 no-op。
        if not accepted:
            self._pending_turn_meta = None

        if accepted:
            logger.info(
                "[%s] handle_avatar_interaction: delivered interaction_id=%s tool=%s action=%s",
                self.lanlan_name,
                interaction_id,
                raw["tool_id"],
                raw["action_id"],
            )
            return {"accepted": True, "interaction_id": interaction_id}

        logger.debug(
            "[%s] handle_avatar_interaction: not accepted interaction_id=%s reason=%s",
            self.lanlan_name, interaction_id, ack_reason,
        )
        return {"accepted": False, "reason": ack_reason, "interaction_id": interaction_id}

    async def trigger_greeting(self) -> None:
        """On first connect or character switch, trigger a proactive greeting based on the gap since the last conversation.

        Flow: query memory_server for the gap → build the guiding prompt → proactively start a text session → deliver.
        """
        if self.is_goodbye_silent():
            logger.info("[%s] trigger_greeting: goodbye silent, skipping", self.lanlan_name)
            return
        # ── 守卫：语音 session 正在启动 / 已活跃时，跳过 greeting ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session active/starting, skipping", self.lanlan_name)
            return
        # ── 守卫：takeover 期间跳过 greeting ──
        # 与 trigger_voice_proactive_nudge / trigger_agent_callbacks 对偶。
        # takeover 时 ordinary chat 输出在 handler 层会被静音，跑 greeting
        # 只会白消耗节日 budget + 写一份永远到不了用户的 LLM 回复。
        if self._takeover_active:
            logger.info("[%s] trigger_greeting: session takeover active, skipping", self.lanlan_name)
            return

        # 复用 internal_http_client 单例：session 启动路径，避开 AsyncClient 构造开销
        # （Windows idle 157ms，事件循环压力下可达 1.1s，详见 utils/internal_http_client.py）
        try:
            from utils.internal_http_client import get_internal_http_client
            _mem_client = get_internal_http_client()
            resp = await _mem_client.get(
                f"http://127.0.0.1:{self.memory_server_port}/last_conversation_gap/{self.lanlan_name}",
                timeout=2.0,
            )
            if not resp.is_success:
                logger.warning("[%s] trigger_greeting: memory server returned %s", self.lanlan_name, resp.status_code)
                return
            gap_seconds = resp.json().get("gap_seconds", -1)
        except Exception as e:
            logger.warning("[%s] trigger_greeting: failed to query gap: %s", self.lanlan_name, e)
            return

        if gap_seconds < 900:  # < 15分钟，不触发
            logger.debug("[%s] trigger_greeting: gap %.0fs < 15min, skipping", self.lanlan_name, gap_seconds)
            return

        # ── await 归来后再检查一次：memory 查询期间用户可能已点了麦克风 ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session appeared during gap query, skipping", self.lanlan_name)
            return

        _lang = normalize_language_code(self.user_language, format='short')
        from config.prompts.prompts_proactive import get_greeting_prompt, get_time_of_day_hint
        from utils.time_format import format_elapsed as _format_elapsed
        from utils.holiday_cache import preview_holiday_or_weekend_hint, commit_holiday_or_weekend_hint
        template = get_greeting_prompt(gap_seconds, _lang)
        if not template:
            return

        # 先确认投递通道可用，再消费节日预算（避免 session 拉起失败白扣次数）
        # 如果已有 text session 且空闲，直接走投递逻辑
        if isinstance(self.session, OmniOfflineClient) and not getattr(self.session, "_is_responding", False):
            pass
        else:
            # 没有 session 或不是 text session → 主动拉起
            # ── 拉起前再次检查：避免与即将到来的语音 session 竞争 ──
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            ws = self.websocket
            if not ws or not hasattr(ws, 'client_state') or ws.client_state != ws.client_state.CONNECTED:
                logger.warning("[%s] trigger_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(ws, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] trigger_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        # 投递通道已就绪，构建 instruction（节日预算仅 preview，不消费）
        elapsed = _format_elapsed(_lang, gap_seconds)
        time_hint = get_time_of_day_hint(_lang).format(master=self.master_name)

        _holiday_token = None
        try:
            holiday_hint_text, _holiday_token = await preview_holiday_or_weekend_hint(_lang, self.lanlan_name)
        except Exception as e:
            logger.debug("[%s] trigger_greeting: holiday hint failed: %s", self.lanlan_name, e)
            holiday_hint_text = None
        holiday_hint = (holiday_hint_text + '\n') if holiday_hint_text else ''

        instruction = template.format(
            elapsed=elapsed, name=self.lanlan_name, master=self.master_name,
            time_hint=time_hint, holiday_hint=holiday_hint,
        )
        print(f"[trigger_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_greeting: gap=%.0fs elapsed=%s, delivering", self.lanlan_name, gap_seconds, elapsed)

        # ── 投递前最终检查：构建 instruction 期间（holiday hint 等 await）语音可能已接管 ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        # 原子 SM claim：与 trigger_agent_callbacks / /api/proactive_chat 互斥
        # 并拦截"AI 正在为用户回复"（session._is_responding）的场景
        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        try:
            async with self._proactive_write_lock:
                # 持锁后仍需检查：_proactive_write_lock 等待期间语音可能已启动
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    # sticky preempt 复查：USER_INPUT 路径在本锁段内翻 flag 和写
                    # user sid 是原子的；若 preempt==True 说明用户已抢到本轮 turn，
                    # 不能再覆盖 current_speech_id 成 proactive sid。
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    # 防御 stale session: 4429 start_session 之后到这里又过了
                    # 多次 await（holiday hint / try_start_proactive /
                    # _proactive_write_lock / self.lock / state.fire ×2），
                    # 期间 cleanup / disconnected_by_server / 切音色重建路径
                    # 都可能把 self.session 置 None 或换为 OmniRealtimeClient。
                    # 直接 self.session.prompt_ephemeral 会触发 AttributeError
                    # 把 trigger_greeting task 整个挂掉（参考切音色后并发
                    # session 重建期间 trigger_greeting 撞 self.session=None
                    # 的崩溃 trace）。先快照本地引用 + 类型校验，stale 时
                    # 静默 skip，外层 finally 会 fire PROACTIVE_DONE 让 SM
                    # 不卡在 PHASE2 / CLAIM。
                    session_ref = self.session
                    if not isinstance(session_ref, OmniOfflineClient):
                        logger.info(
                            "[%s] trigger_greeting: session swapped/nullified "
                            "before prompt_ephemeral (now=%s), skipping",
                            self.lanlan_name, type(session_ref).__name__,
                        )
                        return
                    delivered = await session_ref.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_greeting: delivered=%s", self.lanlan_name, delivered)
                # 投递成功后才真正消费节日/周末预算
                # commit 内部会 atomic_write_json 消费预算文件，offload 以免阻塞事件循环
                if delivered and _holiday_token is not None:
                    await asyncio.to_thread(commit_holiday_or_weekend_hint, self.lanlan_name, _holiday_token)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def trigger_cat_greeting(self, duration_seconds: float, tier: str, was_auto: bool) -> None:
        """When transforming back from cat form to catgirl (asking her back), trigger one dedicated greeting based on "behavior (tier) × time spent as a cat".

        Dual of trigger_greeting, but with independent timing: it doesn't query
        last_conversation_gap, instead using the cat-dwell duration measured and
        passed in by the frontend (the datetime gap is "since the last
        conversation", this is "how long she stayed a cat" — two clocks that don't
        interfere). Flow: pick the behavior/duration tier → build the guiding
        prompt → proactively start a text session → deliver.
        """
        if self.is_goodbye_silent():
            logger.info("[%s] trigger_cat_greeting: goodbye silent, skipping", self.lanlan_name)
            return
        # ── 守卫：语音 session 正在启动 / 已活跃时，跳过（与 trigger_greeting 对偶）──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_cat_greeting: voice session active/starting, skipping", self.lanlan_name)
            return
        if self._takeover_active:
            logger.info("[%s] trigger_cat_greeting: session takeover active, skipping", self.lanlan_name)
            return

        # tier → 行为：cat1=清醒 / cat2=打盹 / cat3=熟睡。
        behavior = {"cat1": "awake", "cat2": "nap", "cat3": "sleep"}.get(str(tier or "").strip().lower(), "awake")

        _lang = normalize_language_code(self.user_language, format='short')
        from config.prompts.prompts_proactive import (
            get_cat_greeting_prompt, get_cat_greeting_reason_hint, get_time_of_day_hint,
        )
        from utils.time_format import format_elapsed as _format_elapsed
        # < 3min 静默由 get_cat_greeting_prompt 内部判定，返回 None 时不触发。
        template = get_cat_greeting_prompt(behavior, duration_seconds, _lang)
        if not template:
            logger.debug("[%s] trigger_cat_greeting: duration %.0fs below threshold, skipping", self.lanlan_name, duration_seconds)
            return

        # 投递通道：已有空闲 text session 则直接用，否则主动拉起（与 trigger_greeting 对偶）
        if isinstance(self.session, OmniOfflineClient) and not getattr(self.session, "_is_responding", False):
            pass
        else:
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_cat_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            ws = self.websocket
            if not ws or not hasattr(ws, 'client_state') or ws.client_state != ws.client_state.CONNECTED:
                logger.warning("[%s] trigger_cat_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_cat_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(ws, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] trigger_cat_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_cat_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        # 与 time_hint 一样，reason_hint 先 format 好 {master} 再注入主模板。
        reason_hint = get_cat_greeting_reason_hint(was_auto, _lang).format(master=self.master_name)
        elapsed = _format_elapsed(_lang, duration_seconds)
        time_hint = get_time_of_day_hint(_lang).format(master=self.master_name)

        instruction = template.format(
            reason_hint=reason_hint, elapsed=elapsed, name=self.lanlan_name,
            master=self.master_name, time_hint=time_hint,
        )
        print(f"[trigger_cat_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_cat_greeting: behavior=%s duration=%.0fs was_auto=%s elapsed=%s, delivering",
                    self.lanlan_name, behavior, duration_seconds, was_auto, elapsed)

        # ── 投递前最终检查：构建 instruction 期间语音可能已接管 ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_cat_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        # 原子 SM claim：与 trigger_greeting / trigger_agent_callbacks / proactive_chat 互斥
        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_cat_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        try:
            async with self._proactive_write_lock:
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_cat_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_cat_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    # stale session 防御：与 trigger_greeting 同款快照 + 类型校验。
                    session_ref = self.session
                    if not isinstance(session_ref, OmniOfflineClient):
                        logger.info(
                            "[%s] trigger_cat_greeting: session swapped/nullified "
                            "before prompt_ephemeral (now=%s), skipping",
                            self.lanlan_name, type(session_ref).__name__,
                        )
                        return
                    delivered = await session_ref.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_cat_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def trigger_new_character_greeting(self) -> None:
        from config.prompts.prompts_proactive import get_new_character_greeting_prompt
        from utils.new_character_greeting_state import has_pending, remove_pending

        config_manager = get_config_manager()
        if not await has_pending(config_manager, self.lanlan_name):
            logger.debug("[%s] trigger_new_character_greeting: no pending intent", self.lanlan_name)
            return

        if self.is_goodbye_silent():
            logger.info("[%s] trigger_new_character_greeting: goodbye silent, skipping", self.lanlan_name)
            return

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session active/starting, skipping", self.lanlan_name)
            return

        _lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
        template = get_new_character_greeting_prompt(_lang)

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session appeared before text session check, skipping", self.lanlan_name)
            return

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_new_character_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            if not self._has_connected_websocket():
                logger.warning("[%s] trigger_new_character_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_new_character_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(self.websocket, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] trigger_new_character_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_new_character_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        if not await has_pending(config_manager, self.lanlan_name):
            logger.debug("[%s] trigger_new_character_greeting: pending intent already consumed", self.lanlan_name)
            return

        instruction = template.format(name=self.lanlan_name, master=self.master_name)
        print(f"[trigger_new_character_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_new_character_greeting: delivering", self.lanlan_name)

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_new_character_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        delivered = False
        proactive_sid = None
        history_len = None
        appended_snapshot = None
        try:
            async with self._proactive_write_lock:
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_new_character_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_new_character_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                history = getattr(self.session, "_conversation_history", None)
                if isinstance(history, list):
                    history_len = len(history)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    delivered = await self.session.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                if history_len is not None and isinstance(history, list) and len(history) > history_len:
                    appended_snapshot = list(history[history_len:])
                logger.info("[%s] trigger_new_character_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            try:
                interrupted = bool(proactive_sid) and self.current_speech_id != proactive_sid
                if (not delivered or interrupted) and history_len is not None:
                    history = getattr(self.session, "_conversation_history", None)
                    if isinstance(history, list) and appended_snapshot:
                        suffix_len = len(appended_snapshot)
                        if suffix_len <= len(history) and history[-suffix_len:] == appended_snapshot:
                            del history[-suffix_len:]
                if delivered and not interrupted:
                    try:
                        await remove_pending(config_manager, self.lanlan_name)
                    except Exception as exc:
                        logger.warning("[%s] trigger_new_character_greeting: remove pending failed: %s", self.lanlan_name, exc)
            finally:
                await self.state.fire(SessionEvent.PROACTIVE_DONE)
