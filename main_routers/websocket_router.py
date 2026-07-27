# -*- coding: utf-8 -*-
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

"""
WebSocket Router

Handles WebSocket endpoints including:
- Main WebSocket connection for chat
- Proactive chat
- Task notifications

URL convention: WebSocket routes (``@router.websocket('/ws/...')``) follow the
same no-trailing-slash rule as HTTP routes. See
``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import array
import json
import math
import struct
import sys
import uuid
import asyncio
import time

from utils.logger_config import get_module_logger
from utils.new_character_greeting_state import has_pending as has_new_character_greeting_pending
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .shared_state import (
    get_session_manager, 
    get_config_manager,
    get_session_id,
)
from .game_router import is_game_route_active, route_external_stream_message
from utils.icebreaker_route_state import (
    finalize_icebreaker_route,
    get_active_icebreaker_route_session_id,
)


_VOICE_BINARY_MAGIC = b"NEKO"
_VOICE_BINARY_HEADER_BYTES = 8
_VOICE_BINARY_MAX_DURATION_MS = 1_000


def _decode_binary_audio_frame(payload: bytes) -> dict[str, object]:
    """Decode and validate a frontend binary PCM frame."""

    if len(payload) <= _VOICE_BINARY_HEADER_BYTES:
        raise ValueError("VOICE_BINARY_FRAME_INVALID: frame is too short")
    magic, sample_rate_hz = struct.unpack_from("<4sI", payload)
    pcm = payload[_VOICE_BINARY_HEADER_BYTES:]
    if (
        magic != _VOICE_BINARY_MAGIC
        or sample_rate_hz not in {16_000, 48_000}
        or len(pcm) % 2
    ):
        raise ValueError("VOICE_BINARY_FRAME_INVALID: invalid header or PCM")
    max_pcm_bytes = sample_rate_hz * 2 * _VOICE_BINARY_MAX_DURATION_MS // 1_000
    if len(pcm) > max_pcm_bytes:
        raise ValueError("VOICE_BINARY_FRAME_INVALID: frame is too large")
    # The int-list materialization cannot be deferred past this point: the
    # Core ASR bridge's lease check runs downstream of stream_data and its
    # consumers require ``data`` to be a real ``list`` of PCM16 ints (see
    # main_logic/core/asr_runtime.py _QueuedMicFrame.from_message and the
    # hot-swap repack). array('h').tolist() builds the identical list ~20%
    # cheaper than list(struct.unpack(...)) for the worst-case 48k-sample
    # frame; the wire format is little-endian, so byteswap on big-endian.
    samples_array = array.array("h", pcm)
    if sys.byteorder == "big":
        samples_array.byteswap()
    samples = samples_array.tolist()
    return {
        "action": "stream_data",
        "input_type": "audio",
        "sample_rate_hz": sample_rate_hz,
        "data": samples,
    }

router = APIRouter(tags=["websocket"])
logger = get_module_logger(__name__, "Main")

# Lock for session management
_lock = asyncio.Lock()

# 防止 fire-and-forget 任务被 Python 3.11+ GC 回收
_ws_bg_tasks: set = set()
# A character can have more than one WebSocket at a time (for example the
# main page and /chat_full).  Each one sends its own greeting_check, so the
# router must coalesce the *scheduled* greeting before the core state machine
# is reached.  The state machine only protects an in-progress delivery; by
# then two tasks may already have independently completed their gap checks.
_greeting_tasks: dict[str, asyncio.Task] = {}
_SESSION_INPUT_TYPES = frozenset({"audio", "screen", "camera", "text", "avatar_drop_image", "user_image"})
_TEXT_SESSION_INPUT_TYPES = frozenset({"text", "avatar_drop_image", "user_image"})
_ORDERED_STREAM_INPUT_TYPES = frozenset({"audio", "avatar_drop_image", "user_image"})
_CAT_GREETING_MAX_DURATION_SECONDS = 7 * 24 * 3600
_CAT_GREETING_TIERS = frozenset({"cat1", "cat2", "cat3"})
_CAT_GREETING_EPISODE_KINDS = frozenset({"rest_after_activity", "rested", "activity"})
_CAT_GREETING_EPISODE_HIGHLIGHTS = frozenset({"played_yarn", "ate_snack", "small_move", "social_ping"})


def _fire_task(coro):
    """Create a background task with GC protection."""
    task = asyncio.create_task(coro)
    _ws_bg_tasks.add(task)
    task.add_done_callback(_ws_bg_tasks.discard)
    return task


def _is_voice_path_message(message: dict) -> bool:
    """True for messages gated by the voice connection identity.

    Exactly the message classes MicLease owns: lease control events and PCM
    (JSON stream_data with audio input_type, or a decoded binary frame).
    Everything else — including an audio-mode start_session — stays on the
    newest-socket-wins global session identity.
    """
    action = message.get("action")
    if action == "voice_input_control":
        return True
    return action == "stream_data" and message.get("input_type") == "audio"


def _stamp_user_input_ingress(message: dict) -> dict:
    """Stamp genuine user input before fire-and-forget task dispatch."""
    if (
        message.get("input_type") not in _TEXT_SESSION_INPUT_TYPES
        and message.get("action") != "avatar_interaction"
    ):
        return message
    # This is a client trust boundary: never preserve a JSON-supplied private
    # timestamp. A future-dated value would suppress idle/proactive behavior.
    # Downstream internal dispatch preserves this server-owned stamp.
    return {
        **message,
        "_user_input_ingress_time": time.time(),
    }


def _reserve_avatar_interaction_ingress(
    manager,
    message: dict,
    *,
    lanlan_name: str,
) -> bool:
    """Keep defensive ingress failures inside the current WS message."""
    try:
        return bool(manager.note_avatar_interaction_ingress(message))
    except Exception as exc:
        logger.warning(
            "[%s] note_avatar_interaction_ingress failed: %s",
            lanlan_name,
            exc,
        )
        return False


def _record_stream_engagement_ingress(
    manager,
    message: dict,
    *,
    lanlan_name: str,
) -> bool:
    """Expose genuine one-shot text/image engagement before stream routing."""
    if message.get("input_type") not in _TEXT_SESSION_INPUT_TYPES:
        return False
    try:
        return bool(manager.note_stream_input_ingress(message))
    except Exception as exc:
        logger.warning(
            "[%s] text/image ingress engagement failed: %s",
            lanlan_name,
            exc,
        )
        return False


def _schedule_greeting_task(lanlan_name: str, kind: str, coro_factory) -> bool:
    """Start at most one greeting-like task per character at a time.

    All greeting sources share this gate: ordinary reconnect/switch greetings,
    first-appearance greetings, and cat-return greetings.  Passing a factory
    rather than a ready coroutine is important: a coalesced request must not
    construct an unawaited coroutine merely to discard it.
    """
    existing = _greeting_tasks.get(lanlan_name)
    if existing is not None and not existing.done():
        logger.info(
            "[%s] %s greeting request coalesced: another greeting task is in flight",
            lanlan_name,
            kind,
        )
        return False

    task = _fire_task(coro_factory())
    # Unit-test task shims can intentionally return None after closing the
    # coroutine.  Production _fire_task always returns asyncio.Task.
    if task is None:
        return True

    _greeting_tasks[lanlan_name] = task

    def _clear_if_current(completed_task):
        if _greeting_tasks.get(lanlan_name) is completed_task:
            _greeting_tasks.pop(lanlan_name, None)

    task.add_done_callback(_clear_if_current)
    return True


def _normalize_cat_greeting_check(message: dict) -> tuple[float, str, bool, dict | None]:
    """Reduce one untrusted cat-greeting check to canonical inputs.

    The reported duration is retained for diagnostics only; the greeting gate
    uses the server-observed goodbye cycle. Only the summary's already-bounded
    ``episode`` enum may cross this router; all other summary fields are
    intentionally ignored.
    """
    payload = message if isinstance(message, dict) else {}

    raw_duration = payload.get("cat_duration_seconds")
    duration = 0.0
    if not isinstance(raw_duration, bool) and isinstance(raw_duration, (int, float)):
        try:
            candidate = float(raw_duration)
        except (OverflowError, TypeError, ValueError):
            candidate = 0.0
        if math.isfinite(candidate):
            duration = max(0.0, min(candidate, _CAT_GREETING_MAX_DURATION_SECONDS))

    raw_tier = payload.get("tier")
    tier = raw_tier.strip().lower() if isinstance(raw_tier, str) else ""
    if tier not in _CAT_GREETING_TIERS:
        tier = ""

    was_auto = payload.get("was_auto") is True
    episode = None
    summary = payload.get("cat_memory_summary")
    if isinstance(summary, dict):
        raw_episode = summary.get("episode")
        if isinstance(raw_episode, dict):
            kind = raw_episode.get("kind")
            if isinstance(kind, str) and kind in _CAT_GREETING_EPISODE_KINDS:
                has_highlight = "highlight" in raw_episode
                highlight = raw_episode.get("highlight")
                if kind == "rested":
                    if not has_highlight:
                        episode = {"kind": kind}
                elif not has_highlight:
                    episode = {"kind": kind}
                elif isinstance(highlight, str) and highlight in _CAT_GREETING_EPISODE_HIGHLIGHTS:
                    episode = {"kind": kind, "highlight": highlight}

    return duration, tier, was_auto, episode


async def _publish_agent_intent_restore_signal(lanlan_name: str, *, new_session: bool = False) -> None:
    """Tell agent_server (via ZMQ) that a real client session is alive,
    so it can restore persisted agent runtime intent (analyzer_enabled +
    5 sub flags). Agent-side once-flag means duplicate signals are cheap.
    Failures (e.g. agent_server not up yet) are swallowed silently —
    the next greeting_check will retry, and the user-facing UI doesn't
    depend on this restore succeeding.

    ``new_session`` is True only for a genuine new greeting (character switch or
    a real gap, NOT a refresh/reconnect within the 15s window). agent_server uses
    it to reset the per-session proactive-analyze budget, so a refresh can't farm
    a fresh budget mid-conversation."""
    try:
        from main_logic.agent_event_bus import publish_session_event
        await publish_session_event({
            "event_type": "agent_intent_restore_signal",
            "lanlan_name": lanlan_name,
            "new_session": bool(new_session),
        })
    except Exception as exc:
        logger.debug("[Greeting] agent intent restore signal publish failed: %s", exc)


# 每个角色的 WS 断开时间戳（epoch），用于区分"首次连接"与"刷新/重连"
_ws_disconnect_time: dict[str, float] = {}
# 每个角色当前活跃的 WS 连接数（pet + /chat_full 等可并存）。用于判定
# greeting_check 是不是"真·新会话"：并发开第二个窗口时不能算新会话（否则会重置
# 主动搭话预算被刷新/多窗口 farm）。单事件循环内 inc/dec 无 await 间隙，天然原子。
_ws_active_count: dict[str, int] = {}

# ---- Telemetry helpers ----

# Dim 字段安全限制 —— 前端是 untrusted 输入，必须挡掉：
# - 高基数维度（如把消息内容塞进 dim）会污染 instrument counter map
# - 超长 key / value 浪费上报带宽
# 32B key / 64B value 对所有合理的 enum 标签都够用；超的截断而不是丢，
# 保留 prefix 至少能切片诊断（如果某个错误 dim 反复触发，前缀也能看出来源）。
_TELEM_MAX_DIMS = 8
_TELEM_KEY_MAX = 32
_TELEM_VAL_MAX = 64
_TELEM_NAME_MAX = 64
# event fields 的 value 比 counter dims 宽松（128B vs 64B），允许 hash / 短
# stack signature 之类略长的标识进 event 但不进 counter map。fields **数量**
# 仍受 _TELEM_MAX_DIMS=8 限制 —— event 也不该塞高基数 payload。
_TELEM_EVENT_VAL_MAX = 128


def _sanitize_dims(d, value_max: int) -> dict:
    """Filter the dims dict from the frontend into a form safe for instrument.

    Drops: non-dict input / non-string keys / values not (str/int/float/bool) / excess keys.
    Truncates: over-long string values.
    """
    out: dict = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        if len(out) >= _TELEM_MAX_DIMS:
            break
        if not isinstance(k, str) or len(k) == 0 or len(k) > _TELEM_KEY_MAX:
            continue
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:value_max]
        # 其它类型（list / dict / None）丢弃
    return out


def _handle_ws_telemetry(message: dict, *, lanlan_name: str) -> None:
    """Forward frontend WS telemetry messages to utils.instrument.

    The ``lanlan_name`` parameter is kept only for logging / context and is **not**
    written as a telemetry dim — it is a user-defined character name; putting it
    in a dim would leak raw user strings into the telemetry DB and explode
    metric_key cardinality. If a character dimension is needed, the business side
    should define a bounded enum (e.g. is_default / character_class) and pass it
    explicitly as a dim.
    """
    try:
        kind = message.get("kind")
        name = message.get("name")
        if not isinstance(name, str) or not name:
            return
        name = name[:_TELEM_NAME_MAX]

        from utils.instrument import counter as _c, histogram as _h, event as _e

        # 前端是 untrusted 输入：Python JSON 解析接受 NaN/Infinity token，
        # 必须在这里挡掉非有限值。否则 NaN 会毒化 client 端 in-memory counter
        # （nan + n = nan），上传时被 storage 的 isfinite 守卫整条丢弃 → 静默
        # 丢掉该 counter 的整个窗口（Codex）。与 storage 端守卫对称。
        if kind == "counter":
            dims = _sanitize_dims(message.get("dims"), _TELEM_VAL_MAX)
            val = message.get("value", 1)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                val = 1  # 缺失 / 非数字 → 默认 +1（事件发生了）
            elif not math.isfinite(val):
                return  # NaN / Inf：reject 整条，不污染 counter
            elif isinstance(val, float):
                # counter 是整数计数：storage 只收整数（4.0 可、1.5 不可），
                # 非整数 float 这里不挡的话会先聚合进内存、上传时被静默丢
                # 整窗（CodeRabbit）。整数值 float 归一化成 int。
                if not val.is_integer():
                    return
                val = int(val)
            _c(name, val, **dims)
        elif kind == "histogram":
            val = message.get("value")
            if (not isinstance(val, (int, float)) or isinstance(val, bool)
                    or not math.isfinite(val)):
                return
            dims = _sanitize_dims(message.get("dims"), _TELEM_VAL_MAX)
            _h(name, val, **dims)
        elif kind == "event":
            fields = _sanitize_dims(message.get("fields"), _TELEM_EVENT_VAL_MAX)
            _e(name, **fields)
        # 其它 kind 静默丢弃
    except Exception as e:
        logger.debug(f"WS telemetry handler error (non-critical): {e}")


@router.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name: str):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    await websocket.accept()
    # Telemetry：WS 连接计数。**不带** lanlan_name dim —— 那是用户自定义的
    # character 名（characters_router 接受 user-controlled new_name），直接进
    # dim 会把 raw 用户字符串泄到远程 telemetry DB，同时让 metric_key 基数
    # 按 (用户数 × 角色数) 爆炸。诊断"哪个角色被打开"对 D2-D7 流失意义有限，
    # 不值得这两个风险。需要时由业务侧显式埋一个 bounded enum 维度。
    try:
        from utils.instrument import counter as _instr_counter
        _instr_counter("ws_connect")
    except Exception:
        # 埋点失败绝不阻塞 WS 业务路径 —— 计数丢一条比让用户连不上服务严重程度
        # 差几个数量级。imports 失败的可能性主要在打包环境下 utils 不齐时。
        pass
    _ws_connect_ts = time.time()

    # 检查角色是否存在，如果不存在则通知前端并关闭连接
    if lanlan_name not in session_manager:
        logger.warning(f"❌ 角色 {lanlan_name} 不存在，当前可用角色: {list(session_manager.keys())}")
        # 获取当前正确的角色名
        current_catgirl = None
        if session_manager:
            current_catgirl = next(iter(session_manager))
        # 通知前端切换到正确的角色
        if current_catgirl:
            try:
                # 注意：此时还没有session_manager，无法获取用户语言，使用默认语言
                message = {
                    "type": "catgirl_switched",
                    "new_catgirl": current_catgirl,
                    "old_catgirl": lanlan_name
                }
                await websocket.send_text(json.dumps(message))
                logger.info(f"已通知前端切换到正确的角色: {current_catgirl}")
                # 等待一下让客户端有时间处理消息，避免 onclose 在 onmessage 之前触发
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"通知前端失败: {e}")
        await websocket.close()
        return
    
    this_session_id = uuid.uuid4()
    # [DIAG] stream_data 计数器：按连接独立，重连后 `#1` 首包可见
    # sd_log_counter = 0
    async with _lock:
        session_id = get_session_id()
        session_id[lanlan_name] = this_session_id
    logger.info(f"⭐ WebSocket accepted: {websocket.client}, new session id: {session_id[lanlan_name]}, lanlan_name: {lanlan_name}")
    
    # 立即设置websocket到session manager，以支持主动搭话
    # 注意：这里设置后，即使cleanup()被调用，websocket也会在start_session时重新设置
    mgr = session_manager[lanlan_name]
    mgr.websocket = websocket
    logger.info(f"✅ 已设置 {lanlan_name} 的WebSocket连接")

    # Engagement-deferred voice-input claim. Claiming the manager-wide voice
    # connection identity at accept time would let a second socket for the
    # same character (the separate chat window, or a reconnect overlap) kill
    # an ongoing recording merely by opening: _begin_voice_input_connection
    # resets the lease owner to "none", drops queued PCM and suppresses
    # ingress. Instead the identity is claimed only when THIS socket first
    # engages voice input (voice_input_control incl. the lease_sync the
    # frontend force-sends on open, an audio-mode start_session, or audio
    # stream_data / a binary PCM frame). Until then the previous voice
    # socket's session continues undisturbed. Once a newer socket engages,
    # the takeover semantics are unchanged: newest engaging connection wins
    # and the superseded socket is closed on its next message by the
    # session-id check below. The two identities are deliberately separate:
    # the global session_id is the NON-VOICE identity (text sessions, UI
    # actions — newest socket always wins those), while voice-path messages
    # are gated against the voice connection identity, so a socket that lost
    # session_id but still holds the voice claim keeps its recording alive.
    voice_input_claimed = False

    def _claim_voice_input_connection() -> None:
        nonlocal voice_input_claimed
        if voice_input_claimed:
            return
        voice_input_claimed = True
        begin_voice_input = getattr(
            session_manager[lanlan_name],
            "_begin_voice_input_connection",
            None,
        )
        if callable(begin_voice_input):
            begin_voice_input(str(this_session_id))

    def _owns_voice_connection() -> bool:
        """True while this socket still holds the manager voice identity.

        Requires both that THIS socket engaged voice input and that no newer
        socket has re-claimed the identity since (a takeover moves
        _voice_lease_connection_id, immediately failing this check).
        Managers without the MicLease mixin never grant ownership.
        """
        if not voice_input_claimed:
            return False
        lease_connection_id = getattr(
            session_manager[lanlan_name],
            "_voice_lease_connection_id",
            None,
        )
        return lease_connection_id == str(this_session_id)

    async def _dispatch_voice_message_while_superseded(message: dict) -> None:
        """Dispatch one voice-path message for the superseded voice socket.

        Deliberately narrower than the main dispatch loop: no engagement
        claim, no ingress stamping, no avatar-position writes and no manager
        state that belongs to the newer session_id owner — only MicLease
        control and PCM keep flowing.
        """
        voice_mgr = session_manager[lanlan_name]
        if message.get("action") == "voice_input_control":
            handle_voice_input_control = getattr(
                voice_mgr,
                "_handle_voice_input_control",
                None,
            )
            if not callable(handle_voice_input_control):
                return
            control_applied = await handle_voice_input_control(
                message.get("event", ""),
                message.get("lease_generation", -1),
                owner=message.get("owner"),
                hard_muted=message.get("hard_muted"),
                focus_suppressed=message.get("focus_suppressed"),
            )
            if not control_applied:
                # manager.send_status targets manager.websocket, which the
                # newer socket now owns; the rejection belongs to THIS
                # socket, so send the same status envelope directly.
                # Best-effort like send_status.
                try:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "status",
                                "message": json.dumps(
                                    {
                                        "code": "VOICE_INPUT_CONTROL_REJECTED",
                                        "details": {
                                            "reason": "invalid_or_stale_control"
                                        },
                                    }
                                ),
                            }
                        )
                    )
                except Exception:
                    pass
            return
        if is_game_route_active(lanlan_name):
            await route_external_stream_message(
                lanlan_name,
                {"input_type": "audio", "stt_provider": "realtime"},
            )
        await voice_mgr.stream_data(message)

    if mgr.pending_agent_callbacks:
        logger.info(f"[{lanlan_name}] websocket reconnect: {len(mgr.pending_agent_callbacks)} pending callbacks, scheduling delivery")
        _fire_task(mgr.trigger_agent_callbacks())

    # finally 块要在所有路径上能读到这个变量，包括 BaseException 抢断
    # try-else 链的情形（SystemExit / KeyboardInterrupt 都不走 else）。
    _ws_disconnect_reason = "unknown"
    try:
        # 计入活跃连接（finally 必减）。greeting_check 判定真·新会话时据此排除
        # 「并发开第二个窗口」的情形。
        _ws_active_count[lanlan_name] = _ws_active_count.get(lanlan_name, 0) + 1
        while True:
            receive = getattr(websocket, "receive", None)
            if callable(receive):
                ws_event = await receive()
                if ws_event.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(ws_event.get("code", 1000))
                binary_payload = ws_event.get("bytes")
                if binary_payload is not None:
                    try:
                        message = _decode_binary_audio_frame(binary_payload)
                    except ValueError as exc:
                        logger.warning(
                            "[%s] dropping malformed binary audio frame: %s",
                            lanlan_name,
                            exc,
                        )
                        continue
                else:
                    data = ws_event.get("text")
                    if not isinstance(data, str):
                        raise ValueError("WEBSOCKET_MESSAGE_INVALID")
                    message = json.loads(data)
            else:
                # 兼容只实现 receive_text 的测试 double。
                data = await websocket.receive_text()
                message = json.loads(data)
            # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
            if lanlan_name not in session_manager:
                logger.info(f"角色 {lanlan_name} 已被重命名或删除，关闭旧连接")
                await websocket.close()
                break
            if session_id.get(lanlan_name) != this_session_id:
                # Separate connection identities: losing the global session_id
                # (a newer window opened, or the newer window since closed and
                # popped it) must not terminate an ongoing recording. While
                # this socket still owns the voice connection, its voice-path
                # messages keep dispatching through the narrow helper above;
                # any non-voice message from it, or any message once a newer
                # socket re-claims voice, closes it exactly as before.
                if _is_voice_path_message(message) and _owns_voice_connection():
                    await _dispatch_voice_message_while_superseded(message)
                    continue
                if lanlan_name not in session_id:
                    logger.info(f"角色 {lanlan_name} 已被重命名或删除，关闭旧连接")
                    await websocket.close()
                    break
                await session_manager[lanlan_name].send_status(json.dumps({"code": "CHARACTER_SWITCHING_TERMINAL", "details": {"name": lanlan_name}}))
                await websocket.close()
                break
            action = message.get("action")

            # 处理语言设置（可以在任何消息中携带）
            if "language" in message:
                user_language = message.get("language")
                session_manager[lanlan_name].set_user_language(user_language)
                logger.info(f"收到用户语言设置: {user_language}")

            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            # ── Telemetry dispatch（前端 counter / histogram / event 通道）──
            # 前端 static/app/app-telemetry.js 通过 action="telemetry" 投递数据；
            # 这里转交 utils.instrument，跟 Python 端发出去的走同一上报通道。
            # 早返回避免污染下面的业务 dispatch；不需要 session_manager 状态。
            if action == "telemetry":
                _handle_ws_telemetry(message, lanlan_name=lanlan_name)
                continue

            if action == "goodbye_state":
                active = bool(message.get("active"))
                reason = str(message.get("reason") or ("goodbye" if active else "return")).strip().lower()[:64]
                goodbye_mgr = session_manager[lanlan_name]
                goodbye_mgr.set_goodbye_silent(active, reason)
                if not active and goodbye_mgr.pending_agent_callbacks:
                    logger.info(
                        "[%s] goodbye_state cleared: retrying %d pending callback(s)",
                        lanlan_name, len(goodbye_mgr.pending_agent_callbacks),
                    )
                    _fire_task(goodbye_mgr.trigger_agent_callbacks())
                continue

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                session_manager[lanlan_name].set_goodbye_silent(False, "start_session")
                # Handshake: the frontend rides its authoritative independent-ASR
                # toggle along on every start_session so the route decision cannot
                # use a stale persisted value (settings POST failed or still in
                # flight). Forward the raw field on every start_session — the
                # setter strictly type-checks (bool only) and an absent or
                # malformed field clears the override, keeping older frontends on
                # the persisted-setting behavior.
                handshake_setter = getattr(
                    session_manager[lanlan_name],
                    "set_independent_asr_handshake",
                    None,
                )
                if callable(handshake_setter):
                    handshake_setter(message.get("independent_asr_enabled"))
                input_type = message.get("input_type", "audio")
                if input_type in _SESSION_INPUT_TYPES:
                    if is_game_route_active(lanlan_name):
                        if input_type in _TEXT_SESSION_INPUT_TYPES:
                            logger.info("[%s] game route active: acknowledging text entry without starting ordinary text session", lanlan_name)
                            _fire_task(session_manager[lanlan_name].send_session_started("text"))
                            continue
                        if input_type == "audio":
                            logger.info("[%s] game route active: starting ordinary realtime as STT provider for game voice", lanlan_name)
                            _claim_voice_input_connection()
                            if session_manager[lanlan_name]._starting_session_count == 0:
                                session_manager[lanlan_name].reset_session_start_circuit()
                            _fire_task(route_external_stream_message(lanlan_name, {"input_type": "audio", "stt_provider": "realtime"}))
                            _fire_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), "audio", user_initiated=True))
                            continue
                    # 传递input_mode参数，告知session manager使用何种模式
                    # 注意：音频模块由 main_server 后台预加载，Python import lock 会自动等待首次导入完成
                    mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
                    if mode == "audio":
                        _claim_voice_input_connection()
                        ensure_voice_input_authorized = getattr(
                            session_manager[lanlan_name],
                            "_ensure_voice_input_session_authorized",
                            None,
                        )
                        if callable(ensure_voice_input_authorized):
                            authorized = await ensure_voice_input_authorized(
                                str(this_session_id)
                            )
                            if not authorized:
                                await session_manager[lanlan_name].send_status(
                                    json.dumps(
                                        {
                                            "code": "VOICE_INPUT_LEASE_REQUIRED",
                                            "details": {
                                                "reason": (
                                                    "voice_input_control_required"
                                                )
                                            },
                                        }
                                    )
                                )
                                continue
                    # 用户显式 start_session（刷新页面 / 点重试）= 清熔断。
                    # 内部 recovery 路径不会走到这里，熔断只能从这条路被清。
                    # 但要避开"上一轮 start_session 还在跑"的 race：那时清零会让
                    # 正在跑的失败重新算第 1 次，熔断永远开不起来。这种情况下
                    # 让正在跑的那次自己处理；新的 start_session 进入后会被
                    # _starting_session_count > 0 的早退拦掉。
                    if session_manager[lanlan_name]._starting_session_count == 0:
                        session_manager[lanlan_name].reset_session_start_circuit()
                    _fire_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), mode, user_initiated=True))
                else:
                    await session_manager[lanlan_name].send_status(json.dumps({"code": "INVALID_INPUT_TYPE", "details": {"input_type": input_type}}))

            elif action == "stream_data":
                input_type = message.get("input_type")
                if input_type == "audio":
                    # PCM (JSON or decoded binary frame) is a voice engagement:
                    # first audio frame on this socket claims the voice input
                    # connection identity.
                    _claim_voice_input_connection()
                # Plain text is dispatched with create_task below. Stamp the
                # server-arrival time before yielding so an earlier user input
                # can never look newer than a proactive commit merely because
                # its task started later.
                message = _stamp_user_input_ingress(message)
                stream_mgr = session_manager[lanlan_name]
                _record_stream_engagement_ingress(
                    stream_mgr,
                    message,
                    lanlan_name=lanlan_name,
                )
                if is_game_route_active(lanlan_name):
                    if input_type == "audio":
                        await route_external_stream_message(lanlan_name, {"input_type": "audio", "stt_provider": "realtime"})
                    else:
                        handled_by_game = await route_external_stream_message(lanlan_name, message)
                        if handled_by_game:
                            continue
                # [DIAG] 切换猫娘后语音 STT 不触发的排查：确认前端是否送达音频
                # _input_type_dbg = message.get("input_type")
                # _data = message.get("data")
                # _data_len = len(_data) if isinstance(_data, (str, bytes, bytearray)) else -1
                # # 按连接计数，重连后 #1 首包仍可见；每 50 次打一条够判断通路是否活
                # sd_log_counter += 1
                # if sd_log_counter == 1 or sd_log_counter % 50 == 0:
                #     logger.info(
                #         f"[{lanlan_name}] stream_data #{sd_log_counter} input_type={_input_type_dbg} data_len={_data_len}"
                #     )
                # Extract and store avatar position metadata (paired with screenshot)
                # 显式清空：前端不发 avatar_position = 不应叠加，防止旧坐标残留
                av_pos = message.get("avatar_position")
                if av_pos and isinstance(av_pos, dict):
                    session_manager[lanlan_name]._avatar_position = av_pos
                else:
                    session_manager[lanlan_name]._avatar_position = None
                if input_type in _ORDERED_STREAM_INPUT_TYPES:
                    await stream_mgr.stream_data(message)
                else:
                    _fire_task(stream_mgr.stream_data(message))

            elif action == "avatar_interaction":
                message = _stamp_user_input_ingress(message)
                avatar_mgr = session_manager[lanlan_name]
                # Validate and expose genuine engagement synchronously, before
                # the background handler can lose a scheduling race to a ready
                # proactive commit. Reserve the interaction ID in that same
                # synchronous step so rapid retransmits cannot reset silence.
                reserved = _reserve_avatar_interaction_ingress(
                    avatar_mgr,
                    message,
                    lanlan_name=lanlan_name,
                )
                message = {
                    **message,
                    "_avatar_interaction_ingress_reserved": reserved,
                }
                _fire_task(avatar_mgr.handle_avatar_interaction(message))

            elif action == "end_session":
                session_manager[lanlan_name].active_session_is_idle = False
                end_reason = str(message.get("reason") or "").strip().lower()[:64]
                if bool(message.get("goodbye_active")) or end_reason == "goodbye":
                    session_manager[lanlan_name].set_goodbye_silent(True, end_reason or "goodbye")
                _fire_task(session_manager[lanlan_name].end_session())

            elif action == "pause_session":
                session_manager[lanlan_name].active_session_is_idle = True
                _fire_task(session_manager[lanlan_name].end_session())

            elif action == "voice_input_control":
                # Any MicLease control message engages voice input for this
                # socket; the frontend force-sends lease_sync on socket open,
                # so a reconnect claims the identity here immediately.
                _claim_voice_input_connection()
                # MicLease 是音频路由的后端权威控制面；按 websocket 消息顺序
                # 同步处理，避免控制事件之后的 PCM 抢先进入旧 turn。
                # getattr 守卫与 _begin_voice_input_connection /
                # _ensure_voice_input_session_authorized 对齐：没有 mixin 的
                # manager double 应 no-op 而不是抛出 SERVER_ERROR。
                handle_voice_input_control = getattr(
                    session_manager[lanlan_name],
                    "_handle_voice_input_control",
                    None,
                )
                if not callable(handle_voice_input_control):
                    continue
                control_applied = await handle_voice_input_control(
                    message.get("event", ""),
                    message.get("lease_generation", -1),
                    owner=message.get("owner"),
                    hard_muted=message.get("hard_muted"),
                    focus_suppressed=message.get("focus_suppressed"),
                )
                if not control_applied:
                    await session_manager[lanlan_name].send_status(
                        json.dumps(
                            {
                                "code": "VOICE_INPUT_CONTROL_REJECTED",
                                "details": {
                                    "reason": "invalid_or_stale_control"
                                },
                            }
                        )
                    )

            elif action == "capture_bridge_status":
                from utils.capture_bridge import mark_capture_client
                mark_capture_client(lanlan_name, websocket, message)

            elif action == "capture_bridge_response":
                from utils.capture_bridge import resolve_capture_response
                resolve_capture_response(lanlan_name, message)

            elif action == "screenshot_response":
                raw = message.get("data", "")
                b64 = raw.split(",", 1)[1] if "," in raw else raw
                # Extract and store avatar position metadata (paired with fresh screenshot)
                av_pos = message.get("avatar_position")
                if av_pos and isinstance(av_pos, dict):
                    session_manager[lanlan_name]._avatar_position = av_pos
                else:
                    session_manager[lanlan_name]._avatar_position = None
                session_manager[lanlan_name].resolve_screenshot_request(b64)

            elif action == "greeting_check":
                # 首次连接或切换角色时，前端请求检查是否需要主动搭话
                # is_switch=true 时始终触发；否则检查上次断开距今是否 >15s（排除刷新/重连）
                is_switch = message.get("is_switch", False)
                greeting_reason = str(message.get("reason") or "").strip().lower()[:64]
                last_disconnect = _ws_disconnect_time.get(lanlan_name, 0)
                since_disconnect = time.time() - last_disconnect if last_disconnect else float('inf')
                # 触发问候的判定（保持原行为）：切角色 或 距上次断开 >15s。
                new_session = bool(is_switch or since_disconnect > 15)
                # 重置主动搭话预算用的更严判定：在上面基础上还要求本连接是该角色唯一
                # 活跃连接，排除「并发开第二个窗口」（无断开时间戳 → since_disconnect=inf
                # 假成新会话 → 重置预算被多窗口 farm）。本连接已在 try 起始处计数，唯一
                # 时为 1。问候判定不受此约束，避免改动既有问候行为。
                budget_new_session = new_session and _ws_active_count.get(lanlan_name, 1) <= 1
                #
                # 顺便：这也是 agent_server 启动后第一个"用户实际进入会话"的信号 ——
                # 我们用它来触发 agent runtime intent restore (analyzer_enabled +
                # 5 个 sub flag 上次会话的开关状态)。restore 是 fire-and-forget 的
                # ZMQ event，agent_server 端有 once-flag 保证只跑一次。
                _fire_task(_publish_agent_intent_restore_signal(lanlan_name, new_session=budget_new_session))
                # A freshly-connected window (notably the separate /chat_full
                # window, which has its own ws and misses any earlier Focus
                # enter) must land on the current edge-glow brightness — push the
                # live charge now. Best-effort; harmless when charge is 0.
                try:
                    _fire_task(session_manager[lanlan_name].resync_focus_for_new_window())
                except Exception:
                    # Best-effort cosmetic re-sync (missing manager / not-yet-ready
                    # session): the focus glow/indicator is non-essential and must
                    # never block or break greeting_check, so swallow and move on.
                    pass
                if new_session:
                    if await has_new_character_greeting_pending(_config_manager, lanlan_name):
                        logger.info(f"[{lanlan_name}] greeting_check: is_switch={is_switch} since_disconnect={since_disconnect:.1f}s reason={greeting_reason or '-'} → new character greeting")
                        _schedule_greeting_task(
                            lanlan_name,
                            "new-character",
                            session_manager[lanlan_name].trigger_new_character_greeting,
                        )
                    else:
                        logger.info(f"[{lanlan_name}] greeting_check: is_switch={is_switch} since_disconnect={since_disconnect:.1f}s reason={greeting_reason or '-'} → triggering")
                        _schedule_greeting_task(
                            lanlan_name,
                            "ordinary",
                            session_manager[lanlan_name].trigger_greeting,
                        )
                else:
                    logger.info(f"[{lanlan_name}] greeting_check: since_disconnect={since_disconnect:.1f}s ≤15s reason={greeting_reason or '-'} → skip (refresh/reconnect)")

            elif action == "cat_greeting_check":
                # 从猫咪形态变回猫娘（请她回来）时，前端按猫咪停留时长请求一次专属问候。
                # 与 greeting_check 对偶，但独立计时：门槛采用服务端观测到的 goodbye 周期，
                # 前端时长仅用于诊断且不能抬高门槛；不查对话 gap；
                # 不发 agent intent restore（那是"首次进入会话"信号，变回不是）。
                reported_duration, cat_tier, cat_was_auto, episode = _normalize_cat_greeting_check(message)
                cat_duration = session_manager[lanlan_name].consume_goodbye_cycle_duration()
                if cat_duration is None:
                    logger.info(
                        "[%s] cat_greeting_check: no completed server goodbye cycle, skipping",
                        lanlan_name,
                    )
                    continue
                raw_summary = message.get("cat_memory_summary") if isinstance(message, dict) else None
                raw_episode = raw_summary.get("episode") if isinstance(raw_summary, dict) else None
                logger.info(
                    "[%s] cat_greeting_check: server_duration=%.0fs reported_duration=%.0fs "
                    "tier=%s was_auto=%s "
                    "summary_object=%s episode_object=%s episode=%s",
                    lanlan_name,
                    cat_duration,
                    reported_duration,
                    cat_tier or "-",
                    cat_was_auto,
                    isinstance(raw_summary, dict),
                    isinstance(raw_episode, dict),
                    episode or "-",
                )
                _schedule_greeting_task(
                    lanlan_name,
                    "cat-return",
                    lambda: session_manager[lanlan_name].trigger_cat_greeting(
                        cat_duration,
                        cat_tier,
                        cat_was_auto,
                        episode=episode,
                    ),
                )

            elif action == "ping":
                # 心跳保活消息，回复pong
                await websocket.send_text(json.dumps({"type": "pong"}))
                # logger.debug(f"收到心跳ping，已回复pong")

            elif action == "language_update":
                # 前端 i18next 'languageChanged' fire 时发的纯语言同步消息：``language``
                # 字段已被 line 136-139 通用 handler 处理（``set_user_language``），
                # 这里 no-op 以避免落到 default 分支推 UNKNOWN_ACTION 状态给前端。
                pass

            elif action in ("voice_play_start", "voice_play_end"):
                # FRONTEND-reported real audio playback boundaries. start =
                # buffered audio actually began playing; end = the audio queue
                # fully drained (she truly stopped talking). This is strictly
                # later than the realtime API's response.done (generation),
                # so the proactive inject gate keys off THIS rather than
                # response.done to avoid self-interruption. Rides the same ws
                # path as every other frontend→backend action (incl. the
                # Electron chat.html WSProxy/IPC bridge → Pet real ws), so no
                # special proxy handling is needed.
                session_manager[lanlan_name].on_voice_playback_signal(
                    playing=(action == "voice_play_start"),
                    turn_id=message.get("turnId") or message.get("turn_id") or "",
                    source=message.get("source") or "audio_playback",
                )

            else:
                logger.warning(f"Unknown action received: {action}")
                await session_manager[lanlan_name].send_status(json.dumps({"code": "UNKNOWN_ACTION", "details": {"action": action}}))

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
        _ws_disconnect_reason = "client_disconnect"
    except Exception as e:
        error_message = f"WebSocket handler error: {e}"
        logger.error(f"💥 {error_message}")
        _ws_disconnect_reason = "handler_error"
        try:
            if lanlan_name in session_manager:
                await session_manager[lanlan_name].send_status(json.dumps({"code": "SERVER_ERROR"}))
        except: # noqa
            pass
    else:
        # 进 finally 时既不是 disconnect 也不是异常 —— 实际上 while True 循环
        # 内只有 break 才到这；break 路径上面都设过 reason；这里兜底防 NameError。
        _ws_disconnect_reason = "normal_break"
    finally:
        # Telemetry：连接生命周期。reason 是低基数 enum，duration 进 histogram
        # 看用户实际停留时长（D2-D7 流失诊断的关键指标之一）。
        # lanlan_name 不进 dim —— 见 accept 处 ws_connect 同样原因（PII + 高基数）。
        try:
            from utils.instrument import counter as _instr_counter, histogram as _instr_histogram
            _ws_dur = time.time() - _ws_connect_ts
            _instr_counter("ws_disconnect", reason=_ws_disconnect_reason)
            if _ws_dur > 0:
                _instr_histogram("ws_session_sec", _ws_dur)
        except Exception:
            # finally 阶段 telemetry 失败不能再 raise —— 已经在 cleanup 路径上，
            # 抛异常会污染调用栈让真正的 WS error 看不到。
            pass
        logger.info(f"Cleaning up WebSocket resources: {websocket.client}")
        # 记录 WS 断开时间，供下次连接时判断是否为"刷新/重连"
        _ws_disconnect_time[lanlan_name] = time.time()
        # 释放活跃连接计数（与 try 起始处的 +1 对偶）
        _ws_active_count[lanlan_name] = max(0, _ws_active_count.get(lanlan_name, 1) - 1)
        # 释放 capture_bridge 注册并 resolve 其所有 pending futures 为错误，
        # 让 /api/capture/health 立即返回 503。
        try:
            from utils.capture_bridge import unmark_capture_client
            unmark_capture_client(lanlan_name, expected_websocket=websocket)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[capture_bridge] unmark on disconnect failed: %s", exc)
        # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
        async with _lock:
            session_id = get_session_id()
            is_current = session_id.get(lanlan_name) == this_session_id
            icebreaker_session_id = ""
            if is_current:
                icebreaker_session_id = get_active_icebreaker_route_session_id(lanlan_name)
            if is_current:
                session_id.pop(lanlan_name, None)

        if is_current and icebreaker_session_id:
            try:
                finalize_icebreaker_route(
                    lanlan_name,
                    session_id=icebreaker_session_id,
                    reason="websocket_disconnect",
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[icebreaker] finalize on ws disconnect failed: %s", exc)

        if is_current and lanlan_name in session_manager:
            await session_manager[lanlan_name].cleanup(expected_websocket=websocket)
