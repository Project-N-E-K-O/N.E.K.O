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
"""Frontend/status notifications and prompt assembly for
``LLMSessionManager``: ``send_*`` status pushes, initial prompt build,
topic hints, and user-language switching.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).

Delivery contract -- there are TWO planes and they are not interchangeable
=========================================================================

``self.websocket`` is the DISPLAY plane. ``websocket_router`` reassigns it
to EVERY newly accepted socket, so it means "the newest window for this
character", NOT "the user" and NOT "the window that is recording". A window
holding the microphone is superseded the moment a chat window opens.

``_send_to_voice_owner`` is the MICROPHONE CONTROL plane. It targets the
socket holding the voice lease -- the window with the live hardware.

**Any notification whose effect is "stop or change the microphone" must
follow the LEASE, not the newest socket.** Send it to the display plane
alone and the recording window never learns its route died: the hardware
mic stays open and keeps uploading into a dead route, with no state and no
recovery path short of the user toggling the mic by hand. Today that class
is ``session_ended_by_server``, ``auto_close_mic``, and the text-mode
``session_started`` ack; ``tests/unit/test_voice_control_plane_contract.py``
fails if a new one is added without routing to the voice owner.

Two corollaries, each of which was a separate bug before it was written
down here:

* There is NO broadcast to fall back on. ``sync_message_queue`` is not a
  per-window fan-out: it runs ``character_runtime`` -> ``cross_server``
  -> ``app/monitor.py`` ``/sync/{name}`` and feeds MONITOR VIEWER clients on
  ``MONITOR_SERVER_PORT`` (desktop pet, subtitle windows). No app window
  ever connects there -- the app always builds a same-origin ``/ws/<name>``.
* The two planes are INDEPENDENT best-effort sends. Never let one failure
  short-circuit the other: give each its own ``try``, and never leave the
  lease-holder send inside a guard on the display socket's liveness.

Ordering matters too, on the way out: a revoke clears both
``_voice_input_websocket`` and the lease id, and ``_voice_owner_socket()``
returns None on either, so the notice must be delivered BEFORE the lease is
released. ``AsrRuntimeMixin._fail_closed_voice_route`` owns that order for
the fail-closed route exits.
"""

import json
from typing import Optional
from fastapi import WebSocketDisconnect
from config import TOOL_SERVER_PORT
from config.prompts.prompts_sys import (
    _loc,
    SESSION_INIT_PROMPT,
    SESSION_INIT_PROMPT_AGENT,
    AGENT_TASK_STATUS_RUNNING,
    AGENT_TASK_STATUS_QUEUED,
    AGENT_TASKS_HEADER,
    AGENT_TASKS_NOTICE,
)
from utils.language_utils import normalize_language_code, is_supported_language_code
from ._shared import logger


class NotifyMixin:
    """Notification and prompt-assembly methods (see module docstring)."""

    def _has_connected_websocket(self) -> bool:
        websocket = self.websocket
        if not websocket or not hasattr(websocket, 'client_state'):
            return False
        try:
            return websocket.client_state == websocket.client_state.CONNECTED
        except Exception:
            return False

    def _should_suppress_activity_narration(self) -> bool:
        """Whether the activity_guess emotion-tier narration has no live consumer.

        Injected into the tracker as the narration suppressed-check (see where
        ``set_narration_suppressed_check`` is wired). The narration only feeds
        proactive Phase 2's state_section, and Phase 2 is a no-op in two cases —
        paying for the LLM call then is pure idle burn:

          * ``is_goodbye_silent()`` — cat-mode silence; Phase 2 bails at its
            goodbye guard.
          * no connected WebSocket — after a plain disconnect / End Session the
            tracker heartbeat keeps ticking (it outlives the session so the
            rule-based break-reminder / context-prompt logic still runs), but a
            proactive turn has no client to reach. Without this, closing the page
            leaves the loop re-narrating at the backoff cap (~900s) all night.

        Both conditions recover on their own: the per-signature narration cache
        stays warm across the suppressed window, so reconnecting (or leaving
        goodbye-silence) resumes narration once that signature's backoff interval
        elapses — on the next tick if a new turn advanced conv_seq or the interval
        already passed during the gap, otherwise after the remaining interval.
        """
        return self.is_goodbye_silent() or not self._has_connected_websocket()

    async def send_user_activity(self, interrupted_speech_id: Optional[str] = None):
        """Send the user-activity signal, attaching the interrupted speech_id for precise interruption control"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                if interrupted_speech_id is None:
                    interrupted_speech_id = self.current_speech_id
                message = {
                    "type": "user_activity",
                    "interrupted_speech_id": interrupted_speech_id  # 告诉前端应丢弃哪个 speech_id
                }
                await self.websocket.send_json(message)
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send User Activity Error: {e}")

    def _convert_cache_to_str(self, cache):
        """[Hot-swap related] Convert the cache to a string"""
        res = ""
        for i in cache:
            res += f"{i['role']} | {i['text']}\n"
        return res

    async def _build_initial_prompt(self) -> str:
        """Build the system prompt and inject active task summary when agent is enabled."""
        _lang = normalize_language_code(self.user_language, format='short')
        if self._is_agent_enabled():
            # Keep the current wrapper structure but revert prompt semantics:
            # do not distinguish browser/computer/plugin in the initial capability text.
            # Historical dynamic capability block kept for rollback:
            # capability_parts = []
            # if self.agent_flags.get('computer_use_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_COMPUTER_USE, _lang))
            # if self.agent_flags.get('browser_use_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_BROWSER_USE, _lang))
            # if self.agent_flags.get('user_plugin_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_USER_PLUGIN_USE, _lang))
            # caps_text = (
            #     _loc(AGENT_CAPABILITY_SEPARATOR, _lang).join(capability_parts)
            #     if capability_parts else _loc(AGENT_CAPABILITY_GENERIC, _lang)
            # )
            # prompt = _loc(SESSION_INIT_PROMPT_AGENT_DYNAMIC, _lang).format(
            #     name=self.lanlan_name,
            #     capabilities=caps_text,
            # ) + self.lanlan_prompt
            prompt = _loc(SESSION_INIT_PROMPT_AGENT, _lang).format(name=self.lanlan_name) + self.lanlan_prompt
        else:
            prompt = _loc(SESSION_INIT_PROMPT, _lang).format(name=self.lanlan_name) + self.lanlan_prompt
        if self._is_agent_enabled():
            # Plugin summary (with plugin ids) is intentionally disabled to avoid
            # exposing implementation identifiers in the general agent prompt.
            # Keep method call removed here for deterministic prompt content.
            # Historical prompt merge kept for rollback:
            # plugin_prompt, active_tasks_prompt = await asyncio.gather(
            #     self._fetch_plugin_summary_prompt(),
            #     self._fetch_active_agent_tasks_prompt(),
            # )
            # prompt += plugin_prompt
            active_tasks_prompt = await self._fetch_active_agent_tasks_prompt()
            prompt += active_tasks_prompt

        # 记录 / 查询 key：lanlan_name 为空时落到 "default" 与 sink 端对齐
        # （sink 在 lanlan 字段空 / "default" 时把 directive 写到 "default"
        # bucket；这里读取也得用同一 key，否则用户的 ban-topic 永远进不来
        # system prompt，codex P2）。
        _directives_key = self.lanlan_name or "default"

        # ── 用户显式 ban-topic 注入 ─────────────────────────────────
        # 用户在过去 3 天里说过的 "别再提 X / stop saying X" 类指令，本轮 LLM
        # 在 context 里已经看过；下一次 session 重启时原话已被 compress_history
        # 抹掉，需要把活跃 term 拼成 system prompt 一段重新提醒模型避开。
        # 抽取与落盘走 ``memory.user_directives`` 的 user_utterance sink；
        # 这里只读。空时 render_prompt_block 返回 ""，对 prompt 长度无影响。
        try:
            from memory.user_directives import get_user_directives_manager
            prompt += get_user_directives_manager().render_prompt_block(
                _directives_key, _lang,
            )
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug(
                "[UserDirectives] prompt injection skipped: %s", _exc,
            )

        # ── 防复读 soft hint 注入 ──────────────────────────────────
        # 把最近高 BM25 rank 的 topic 词列出来，提示模型"已经聊过这些"。这是
        # 对**所有路径**生效的软约束（与 user ban list 不同：那个是用户明确
        # 说过别提，必须强约束）。proactive 还会在 system_router Phase 2 出口
        # 被 BM25 总分阈值二次拦截（regen / drop），常规 reply 只靠这段 prompt
        # 软约束。空 corpus / 新角色第一轮 → render 返回 ""，无副作用。
        try:
            from memory.anti_repeat import get_anti_repeat_corpus
            from config.prompts.prompts_directives import render_recent_topics_block
            topics = get_anti_repeat_corpus().top_recent_topics(_directives_key)
            prompt += render_recent_topics_block(topics, _lang)
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug(
                "[AntiRepeat] soft hint injection skipped: %s", _exc,
            )

        return prompt

    def _is_agent_enabled(self):
        try:
            gate_ok, _ = self._config_manager.is_agent_api_ready()
        except Exception:
            gate_ok = False
        return gate_ok and self.agent_flags['agent_enabled'] and (
            self.agent_flags['computer_use_enabled']
            or self.agent_flags.get('browser_use_enabled', False)
            or self.agent_flags.get('user_plugin_enabled', False)
            or self.agent_flags.get('openclaw_enabled', False)
            or self.agent_flags.get('openfang_enabled', False)
        )

    async def _fetch_plugin_summary_prompt(self) -> str:
        """Plugin prompt segment is intentionally disabled for chat prompt minimalism."""
        # This hook is kept for compatibility with older call sites.
        # Disabled by product decision: do not include plugin IDs in agent prompt.
        # Historical implementation kept for rollback:
        # if not (self._is_agent_enabled() and self.agent_flags.get('user_plugin_enabled')):
        #     return ""
        # _lang = normalize_language_code(self.user_language, format='short')
        # header = _loc(AGENT_PLUGINS_HEADER, _lang)
        # count_tmpl = _loc(AGENT_PLUGINS_COUNT, _lang)
        # try:
        #     async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0), proxy=None, trust_env=False) as client:
        #         r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
        #         if r.status_code != 200:
        #             return ""
        #         data = r.json()
        #         plugins = data.get("plugins", []) if isinstance(data, dict) else []
        #         if not plugins:
        #             return ""
        #         if len(plugins) <= 5:
        #             lines = []
        #             for p in plugins:
        #                 if not isinstance(p, dict):
        #                     continue
        #                 pid = p.get("id", "")
        #                 if pid:
        #                     lines.append(f"  - {pid}")
        #             if lines:
        #                 return header + "\n".join(lines) + "\n"
        #         else:
        #             return count_tmpl.format(count=len(plugins))
        # except Exception as e:
        #     logger.debug(f"获取插件摘要失败，已忽略: {e}")
        return ""

    async def _fetch_active_agent_tasks_prompt(self) -> str:
        """Query agent server for active tasks and return a prompt snippet."""
        if not self._is_agent_enabled():
            return ""
        # 复用 internal_http_client 单例：agent mode session init 走此路径，
        # TOOL_SERVER_PORT 也是 127.0.0.1 内部服务
        try:
            from utils.internal_http_client import get_internal_http_client
            client = get_internal_http_client()
            resp = await client.get(
                f"http://127.0.0.1:{TOOL_SERVER_PORT}/tasks", timeout=1.5,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            tasks = data.get("tasks", [])
            active = [t for t in tasks if t.get("status") in ("running", "queued")]
            if not active:
                return ""
            _lang = normalize_language_code(self.user_language, format='short')
            lines = []
            for t in active:
                params = t.get("params") or {}
                desc = params.get("query") or params.get("instruction") or t.get("original_query") or t.get("id", "")[:8]
                status = _loc(AGENT_TASK_STATUS_RUNNING, _lang) if t.get("status") == "running" else _loc(AGENT_TASK_STATUS_QUEUED, _lang)
                lines.append(f"  - [{status}] {desc}")
            if len(lines) > 0:
                return (
                    _loc(AGENT_TASKS_HEADER, _lang)
                    + "\n".join(lines)
                    + _loc(AGENT_TASKS_NOTICE, _lang)
                )
            else:
                return ""
        except Exception:
            return ""

    def _get_translation_service(self):
        """Get the translation service instance (lazily initialized)"""
        if self._translation_service is None:
            from utils.language_utils import get_translation_service
            self._translation_service = get_translation_service(self._config_manager)
        return self._translation_service
    
    def set_user_language(self, language: str):
        """
        Set the user language (reuses normalize_language_code for normalization)
        
        Supported normalization rules:
        - 'zh', 'zh-CN', 'zh-TW' and anything starting with 'zh' → 'zh-CN'
        - 'en', 'en-US', 'en-GB' and anything starting with 'en' → 'en'
        - 'ja', 'ja-JP' and anything starting with 'ja' → 'ja'
        - other languages unsupported for now, stays at the default 'zh-CN'
        """
        if not language:
            logger.warning(f"语言参数为空，保持当前语言: {self.user_language}")
            return

        # 校验原始输入：``normalize_language_code`` 对未识别值会默认回退 ``'en'``，
        # 外部来源（ws ``message['language']`` 携带的 corrupted ``localStorage``、
        # 第三方客户端发的 ``'undefined'`` / ``'null'`` / ``'estonian'`` 等 garbage）
        # 会被静默归一成 ``'en'``，覆盖正确的 session locale。先用公共白名单挡掉。
        if not is_supported_language_code(language):
            logger.warning(
                f"语言参数不支持: {language!r}，保持当前语言: {self.user_language}"
            )
            return

        # 使用公共函数进行语言代码归一化
        normalized_lang = normalize_language_code(language, format='full')

        self.user_language = normalized_lang
        self._conversation_turn_language = normalized_lang
        self._set_conversation_turn_language(normalized_lang)
        if normalized_lang != language:
            logger.info(f"用户语言已归一化: {language} → {normalized_lang}")
        else:
            logger.info(f"用户语言已设置为: {normalized_lang}")

        # 文本模式下无需额外同步改写提示语言（已移除 rewrite 逻辑）

        # 内置工具的 description / 参数说明是按 user_language 渲染的，
        # 这里换语言后重新注册一份覆盖 registry 旧描述，并 fire-and-forget
        # 推到当前 active / pending session 的 wire 上（OmniRealtimeClient
        # 支持 session.update 携带新 tools；OmniOfflineClient 下次 stream_text
        # 自动用最新 _tool_definitions）。
        self._register_builtin_tools()
        self._fire_task(self._sync_tools_to_active_session())
    
    def _voice_owner_socket(self):
        """Return the socket holding the voice lease, when it is not the current one.

        ``self.websocket`` is reassigned to every newly accepted socket, so the
        window that is actually recording can be superseded by a newer chat
        window while keeping the microphone. Mic control-plane messages have to
        reach that window or its teardown never runs. Returns None whenever the
        lease holder IS the current socket, so single-window behaviour is
        bit-identical.

        Note ``sync_message_queue`` is NOT an alternative: it feeds the monitor
        process (desktop pet / subtitle viewers) over a separate port, and no
        app window ever connects there.
        """

        socket = getattr(self, "_voice_input_websocket", None)
        if socket is None or socket is self.websocket:
            return None
        if not getattr(self, "_voice_lease_connection_id", ""):
            return None
        state = getattr(socket, "client_state", None)
        if state is None or state != socket.client_state.CONNECTED:
            return None
        return socket

    async def _send_to_voice_owner(self, payload: dict) -> None:
        """Best-effort push to the voice-lease holder; never raises."""

        socket = self._voice_owner_socket()
        if socket is None:
            return
        try:
            await socket.send_text(json.dumps(payload))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send To Voice Owner Error: {e}")

    async def send_status(self, message: str):
        """Send a status message to the frontend. message should be a JSON string {"code": "XXX", "details": {...}}, translated by the frontend via i18next."""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "status", "message": message})
                await self.websocket.send_text(data)

                # 同步到同步服务器
                self.sync_message_queue.put({'type': 'json', 'data': {"type": "status", "message": message}})
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Status Error: {e}")
    
    async def send_topic_hint(self, *, turn_id: Optional[str] = None) -> bool:
        """Show a frontend-only teaser bubble right before she opens a deep-topic hook.

        Deliberately does NOT touch ``sync_message_queue`` / chat memory — the
        teaser is pure frontend display (rendered by react-neko-chat's dedicated
        topic-hint component) and must never enter the chat-LLM context, the
        same isolation as :meth:`passthrough_to_chat_bubble`. The frontend
        renders the localized copy itself; we only hand it the character name.
        """
        if not (
            self.websocket
            and hasattr(self.websocket, 'client_state')
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json({
                "type": "topic_hint",
                "author": self.lanlan_name,
                "turn_id": str(turn_id or ''),
            })
            return True
        except WebSocketDisconnect:
            return False
        except Exception as e:
            logger.warning("[%s] send_topic_hint failed: %s", self.lanlan_name, e)
            return False

    async def send_cancel_topic_hint(self, *, turn_id: Optional[str] = None) -> bool:
        """Retract a previously sent topic-hint teaser (matched by ``turn_id``).

        Used when the opener fails before any committed output, so the frontend
        removes the dangling teaser instead of leaving an orphan bubble. Like
        :meth:`send_topic_hint`, this stays off ``sync_message_queue`` entirely.
        """
        if not (
            self.websocket
            and hasattr(self.websocket, 'client_state')
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json({
                "type": "cancel_topic_hint",
                "turn_id": str(turn_id or ''),
            })
            return True
        except WebSocketDisconnect:
            return False
        except Exception as e:
            logger.warning("[%s] send_cancel_topic_hint failed: %s", self.lanlan_name, e)
            return False

    async def send_session_preparing(self, input_mode: str): # 通知前端session正在准备（静默期）
        payload = {"type": "session_preparing", "input_mode": input_mode}
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    await self.websocket.send_text(json.dumps(payload))
                except WebSocketDisconnect:
                    # Isolated like the sibling senders: a display socket dying
                    # between the CONNECTED check and the send must not skip the
                    # lease-holder copy below.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Preparing Error: {e}")
            if getattr(self, "_voice_lease_owner", "none") != "game":
                # Completes the set with session_started / session_failed: the
                # window that asked for an audio session is the LEASE holder,
                # while self.websocket is whichever socket connected most
                # recently. This one only drives the "preparing" banner, so
                # losing it is cosmetic rather than a stuck microphone -- but a
                # requester that never sees "preparing" and then never sees the
                # ack has no feedback at all for the whole start.
                #
                # No-op for a single window: _voice_owner_socket returns None
                # when the lease holder IS the current socket.
                await self._send_to_voice_owner(dict(payload))
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Preparing Error: {e}")
    
    async def send_session_started(self, input_mode: str): # 通知前端session已启动
        # Carry the SETTLED microphone route on the ack itself (Codex P2).
        #
        # The route verdict otherwise travels only as an ASR_INDEPENDENT_*
        # status on the mic control plane, which reaches the lease holder and
        # the current display socket -- and there are paths where it reaches
        # neither. The load-bearing one: a second window claiming the voice
        # lease while _asr_runtime.start() is still running bumps the ASR start
        # generation, so the failing start's own terminal status is fenced off
        # and NEVER EMITTED, leaving the route pinned "blocked" with no verdict
        # delivered anywhere. Both windows' fail-closed latches stay false, the
        # ack below says "started", and the microphone opens onto a route that
        # discards every frame -- no status, no recovery, the user just talks
        # into nothing.
        #
        # Qualifying the ack fixes every emitter at once: the ordinary one in
        # _start_session_activate, the in-flight dedupe re-ack in
        # _start_session_handle_inflight (which re-acks WITHOUT re-running the
        # route decision), and the stale-start case above that no status-based
        # fix can reach. Suppressing the ack instead would be worse -- the
        # dedupe re-ack exists precisely so the requester is not stranded on its
        # 15s timeout, whose end_session tears down the session that did start.
        payload = {"type": "session_started", "input_mode": input_mode}
        route_mode = str(getattr(self, "_asr_route_mode", "") or "")
        if route_mode:
            # Omitted when unknown rather than defaulted: a manager without the
            # ASR mixin should keep today's behaviour, not have every audio
            # start refuse the microphone.
            payload["microphone_route"] = route_mode
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps(payload)
                try:
                    await self.websocket.send_text(data)
                except WebSocketDisconnect:
                    # Isolated for the same reason as
                    # send_session_ended_by_server: the CONNECTED check and the
                    # send are separated by an await, so the display socket can
                    # die in between. Letting that reach the outer handler would
                    # skip the text fan-out below -- the notice that tells a
                    # recorder superseded by THIS chat window that its route has
                    # gone blocked, without which it keeps a live hardware
                    # microphone feeding a route that discards every frame.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Started Error: {e}")
            if getattr(self, "_voice_lease_owner", "none") != "game":
                # A text session pins the microphone route to "blocked", so the
                # window still holding the mic has to hear about it or it keeps
                # uploading into a route that discards everything.
                #
                # Audio is here too, and the "would flip voiceChatActive in an
                # unrelated window" reasoning this used to carry does not hold
                # for it (Codex P2). The lease holder is not an unrelated
                # window: for a user-initiated audio start the router claims the
                # lease for the REQUESTING socket synchronously
                # (_claim_voice_input_connection) BEFORE firing start_session,
                # so the lease holder IS the window that asked. Meanwhile
                # ``self.websocket`` is reassigned to every newly accepted
                # socket, and a whole session start (TTS + LLM + independent
                # ASR) sits between that reassignment and this ack -- seconds,
                # against a 15s frontend deadline. Any second window opening in
                # that interval used to take the ack, and the window that
                # actually asked sat on ``sessionStartPromise`` until it timed
                # out and never called startMicCapture: the user clicks the mic
                # and simply never gets a microphone, while the backend audio
                # session stays up with no recording client.
                #
                # This is a no-op for the single-window case:
                # ``_voice_owner_socket`` returns None whenever the lease holder
                # IS the current socket, so the fan-out fires only in exactly
                # that race.
                #
                # Game owner exempt, matching send_session_ended_by_server and
                # _fail_closed_voice_route. The galgame gate owns the mic
                # through the built-in game consumer route and tears down via
                # GAME_ROUTE_ENDED, and websocket_router acknowledges a text
                # entry during an active game route with a bare
                # send_session_started("text") -- no ordinary text session, no
                # blocked route. Fanning that ack out anyway reaches the game
                # window, whose session_started(text) handler calls
                # stopRecording({notifyServer:false}) on any window with
                # isRecording true (which a game STT gate requires), releasing
                # the game lease and closing hardware the text entry never
                # meant to touch.
                await self._send_to_voice_owner(dict(payload))
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Started Error: {e}")
    
    async def send_session_failed(self, input_mode: str): # 通知前端session启动失败
        """Notify the frontend that session start failed, so it hides the preparing banner and resets state"""
        payload = {"type": "session_failed", "input_mode": input_mode}
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    await self.websocket.send_text(json.dumps(payload))
                except WebSocketDisconnect:
                    # Isolated like the sibling senders: a display socket dying
                    # between the CONNECTED check and the send must not skip the
                    # lease-holder copy below.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Failed Error: {e}")
            if getattr(self, "_voice_lease_owner", "none") != "game":
                # Same reasoning as send_session_started (Codex P2). The window
                # that asked for an audio session is the LEASE holder -- the
                # router claims the lease for the requesting socket before
                # firing start_session -- while self.websocket is reassigned to
                # every newly accepted socket. A second window opening during a
                # start that then FAILS took this notice, and the window that
                # asked sat on sessionStartPromise until its 15s deadline
                # instead of failing fast; the timeout path then sends
                # end_session, tearing down whatever did get built.
                #
                # No-op for a single window: _voice_owner_socket returns None
                # when the lease holder IS the current socket. Game owner
                # exempt, matching the other senders.
                await self._send_to_voice_owner(dict(payload))
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Failed Error: {e}")

    async def send_avatar_interaction_ack(self, interaction_id: str, accepted: bool, reason: str = '', turn_id: str = ''):
        """Acknowledge to the frontend the delivery result of an avatar-tap interaction, enabling retry and state wrap-up on the frontend."""
        if not interaction_id:
            return
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({
                    "type": "avatar_interaction_ack",
                    "interaction_id": interaction_id,
                    "accepted": bool(accepted),
                    "reason": str(reason or ''),
                    "turn_id": str(turn_id or ''),
                })
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Avatar Interaction Ack Error: {e}")

    async def send_session_ended_by_server(self): # 通知前端session已被服务器终止
        """Notify the frontend that the session was terminated server-side (e.g. API disconnect), so it resets the session state"""
        payload = {"type": "session_ended_by_server", "input_mode": self.input_mode}
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    await self.websocket.send_text(json.dumps(payload))
                except WebSocketDisconnect:
                    # Isolated on purpose: the CONNECTED check above and the
                    # send are separated by an await, so the display socket
                    # can die in between. Letting that reach the outer handler
                    # would skip the lease holder's copy below -- the one send
                    # that stops a live hardware microphone.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Ended By Server Error: {e}")
            # The terminal event is also the recorder's microphone teardown, and
            # the window holding the hardware is not necessarily the current
            # socket. Outside the guard on purpose: a dead current socket must
            # not swallow the lease holder's copy. Game owner exempt -- the
            # galgame gate owns the mic through the built-in game consumer and
            # tears down via GAME_ROUTE_ENDED.
            if getattr(self, "_voice_lease_owner", "none") != "game":
                await self._send_to_voice_owner(payload)
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Ended By Server Error: {e}")
