# -*- coding: utf-8 -*-
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

import json
import math
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

router = APIRouter(tags=["websocket"])
logger = get_module_logger(__name__, "Main")

# Lock for session management
_lock = asyncio.Lock()

# 防止 fire-and-forget 任务被 Python 3.11+ GC 回收
_ws_bg_tasks: set = set()


def _fire_task(coro):
    """Create a background task with GC protection."""
    task = asyncio.create_task(coro)
    _ws_bg_tasks.add(task)
    task.add_done_callback(_ws_bg_tasks.discard)
    return task


async def _publish_agent_intent_restore_signal(lanlan_name: str) -> None:
    """Tell agent_server (via ZMQ) that a real client session is alive,
    so it can restore persisted agent runtime intent (analyzer_enabled +
    5 sub flags). Agent-side once-flag means duplicate signals are cheap.
    Failures (e.g. agent_server not up yet) are swallowed silently —
    the next greeting_check will retry, and the user-facing UI doesn't
    depend on this restore succeeding."""
    try:
        from main_logic.agent_event_bus import publish_session_event
        await publish_session_event({
            "event_type": "agent_intent_restore_signal",
            "lanlan_name": lanlan_name,
        })
    except Exception as exc:
        logger.debug("[Greeting] agent intent restore signal publish failed: %s", exc)


# 每个角色的 WS 断开时间戳（epoch），用于区分"首次连接"与"刷新/重连"
_ws_disconnect_time: dict[str, float] = {}

# 前端首页新手引导的轻量兜底状态。只用于阻止已经穿过前端的 greeting_check，
# 带 TTL，避免断线或前端异常导致后端长期误判仍在教程中。
_HOME_TUTORIAL_STATE_TTL_SECONDS = 60.0
_home_tutorial_blocking_greeting: dict[str, tuple[bool, float]] = {}


def _is_home_tutorial_blocking_greeting(lanlan_name: str) -> bool:
    state = _home_tutorial_blocking_greeting.get(lanlan_name)
    if not state:
        return False
    blocking, updated_at = state
    if time.time() - updated_at > _HOME_TUTORIAL_STATE_TTL_SECONDS:
        _home_tutorial_blocking_greeting.pop(lanlan_name, None)
        return False
    return bool(blocking)


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
    """把前端传入的 dims dict 过滤成 instrument 能吃的安全形式。

    丢弃：非 dict / 非字符串 key / 非 (str/int/float/bool) value / 超量 key。
    截断：超长 string value。
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
    """把前端 WS telemetry message 转交 utils.instrument。

    ``lanlan_name`` 参数保留只为日志 / 上下文，**不**作为 dim 写入埋点 ——
    那是用户自定义的 character 名，进 dim 会把 raw 用户字符串泄到 telemetry
    DB 且让 metric_key 基数爆炸。需要 character 维度时业务侧应自己定义一个
    bounded enum（如 is_default / character_class）显式传 dim。
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

    if mgr.pending_agent_callbacks:
        logger.info(f"[{lanlan_name}] websocket reconnect: {len(mgr.pending_agent_callbacks)} pending callbacks, scheduling delivery")
        _fire_task(mgr.trigger_agent_callbacks())

    # finally 块要在所有路径上能读到这个变量，包括 BaseException 抢断
    # try-else 链的情形（SystemExit / KeyboardInterrupt 都不走 else）。
    _ws_disconnect_reason = "unknown"
    try:
        while True:
            data = await websocket.receive_text()
            # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
            if lanlan_name not in session_id or lanlan_name not in session_manager:
                logger.info(f"角色 {lanlan_name} 已被重命名或删除，关闭旧连接")
                await websocket.close()
                break
            if session_id[lanlan_name] != this_session_id:
                await session_manager[lanlan_name].send_status(json.dumps({"code": "CHARACTER_SWITCHING_TERMINAL", "details": {"name": lanlan_name}}))
                await websocket.close()
                break
            message = json.loads(data)
            action = message.get("action")

            # 处理语言设置（可以在任何消息中携带）
            if "language" in message:
                user_language = message.get("language")
                session_manager[lanlan_name].set_user_language(user_language)
                logger.info(f"收到用户语言设置: {user_language}")

            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            # ── Telemetry dispatch（前端 counter / histogram / event 通道）──
            # 前端 static/app-telemetry.js 通过 action="telemetry" 投递数据；
            # 这里转交 utils.instrument，跟 Python 端发出去的走同一上报通道。
            # 早返回避免污染下面的业务 dispatch；不需要 session_manager 状态。
            if action == "telemetry":
                _handle_ws_telemetry(message, lanlan_name=lanlan_name)
                continue

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type", "audio")
                if input_type in ['audio', 'screen', 'camera', 'text']:
                    if is_game_route_active(lanlan_name):
                        if input_type == "text":
                            logger.info("[%s] game route active: acknowledging text entry without starting ordinary text session", lanlan_name)
                            _fire_task(session_manager[lanlan_name].send_session_started("text"))
                            continue
                        if input_type == "audio":
                            logger.info("[%s] game route active: starting ordinary realtime as STT provider for game voice", lanlan_name)
                            if session_manager[lanlan_name]._starting_session_count == 0:
                                session_manager[lanlan_name].reset_session_start_circuit()
                            _fire_task(route_external_stream_message(lanlan_name, {"input_type": "audio", "stt_provider": "realtime"}))
                            _fire_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), "audio"))
                            continue
                    # 传递input_mode参数，告知session manager使用何种模式
                    # 注意：音频模块由 main_server 后台预加载，Python import lock 会自动等待首次导入完成
                    mode = 'text' if input_type == 'text' else 'audio'
                    # 用户显式 start_session（刷新页面 / 点重试）= 清熔断。
                    # 内部 recovery 路径不会走到这里，熔断只能从这条路被清。
                    # 但要避开"上一轮 start_session 还在跑"的 race：那时清零会让
                    # 正在跑的失败重新算第 1 次，熔断永远开不起来。这种情况下
                    # 让正在跑的那次自己处理；新的 start_session 进入后会被
                    # _starting_session_count > 0 的早退拦掉。
                    if session_manager[lanlan_name]._starting_session_count == 0:
                        session_manager[lanlan_name].reset_session_start_circuit()
                    _fire_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), mode))
                else:
                    await session_manager[lanlan_name].send_status(json.dumps({"code": "INVALID_INPUT_TYPE", "details": {"input_type": input_type}}))

            elif action == "stream_data":
                if is_game_route_active(lanlan_name):
                    input_type = message.get("input_type")
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
                if message.get("input_type") == "audio":
                    await session_manager[lanlan_name].stream_data(message)
                else:
                    _fire_task(session_manager[lanlan_name].stream_data(message))

            elif action == "avatar_interaction":
                _fire_task(session_manager[lanlan_name].handle_avatar_interaction(message))

            elif action == "end_session":
                session_manager[lanlan_name].active_session_is_idle = False
                _fire_task(session_manager[lanlan_name].end_session())

            elif action == "pause_session":
                session_manager[lanlan_name].active_session_is_idle = True
                _fire_task(session_manager[lanlan_name].end_session())

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

            elif action == "home_tutorial_state":
                blocking = bool(message.get("blocking_greeting"))
                _home_tutorial_blocking_greeting[lanlan_name] = (blocking, time.time())
                logger.debug(
                    "[%s] home_tutorial_state: blocking_greeting=%s reason=%s",
                    lanlan_name, blocking, message.get("reason") or "",
                )

            elif action == "greeting_check":
                # 首次连接或切换角色时，前端请求检查是否需要主动搭话
                # is_switch=true 时始终触发；否则检查上次断开距今是否 >15s（排除刷新/重连）
                #
                # 顺便：这也是 agent_server 启动后第一个"用户实际进入会话"的信号 ——
                # 我们用它来触发 agent runtime intent restore (analyzer_enabled +
                # 5 个 sub flag 上次会话的开关状态)。restore 是 fire-and-forget 的
                # ZMQ event，agent_server 端有 once-flag 保证只跑一次；即使本次
                # greeting_check 被 home tutorial guard 阻塞，agent intent 也应该
                # 趁机会恢复（无害：用户在新手引导期一般也没旧 intent 要恢复）。
                _fire_task(_publish_agent_intent_restore_signal(lanlan_name))
                if _is_home_tutorial_blocking_greeting(lanlan_name):
                    logger.info(f"[{lanlan_name}] greeting_check: skipped by home tutorial guard")
                    continue
                is_switch = message.get("is_switch", False)
                greeting_reason = str(message.get("reason") or "").strip().lower()[:64]
                # 教程结束释放的是延迟问好，不应被刚经历过的页面/窗口重连保护吞掉。
                bypass_reconnect_guard = greeting_reason in {"tutorial-completed", "tutorial-skipped"}
                last_disconnect = _ws_disconnect_time.get(lanlan_name, 0)
                since_disconnect = time.time() - last_disconnect if last_disconnect else float('inf')
                if is_switch or since_disconnect > 15 or bypass_reconnect_guard:
                    if await has_new_character_greeting_pending(_config_manager, lanlan_name):
                        logger.info(f"[{lanlan_name}] greeting_check: is_switch={is_switch} since_disconnect={since_disconnect:.1f}s reason={greeting_reason or '-'} → new character greeting")
                        _fire_task(session_manager[lanlan_name].trigger_new_character_greeting())
                    else:
                        logger.info(f"[{lanlan_name}] greeting_check: is_switch={is_switch} since_disconnect={since_disconnect:.1f}s reason={greeting_reason or '-'} → triggering")
                        _fire_task(session_manager[lanlan_name].trigger_greeting())
                else:
                    logger.info(f"[{lanlan_name}] greeting_check: since_disconnect={since_disconnect:.1f}s ≤15s reason={greeting_reason or '-'} → skip (refresh/reconnect)")

            elif action == "cat_greeting_check":
                # 从猫咪形态变回猫娘（请她回来）时，前端按猫咪停留时长请求一次专属问候。
                # 与 greeting_check 对偶，但独立计时：时长由前端测量传入，不查对话 gap；
                # 不发 agent intent restore（那是"首次进入会话"信号，变回不是）。
                if _is_home_tutorial_blocking_greeting(lanlan_name):
                    logger.info(f"[{lanlan_name}] cat_greeting_check: skipped by home tutorial guard")
                    continue
                try:
                    cat_duration = float(message.get("cat_duration_seconds", 0) or 0)
                except (TypeError, ValueError):
                    cat_duration = 0.0
                # sanitize：非负、封顶 7 天，防前端异常值（如丢失 goodbyeEnteredAt → now-0）
                cat_duration = max(0.0, min(cat_duration, 7 * 24 * 3600))
                cat_tier = str(message.get("tier") or "").strip().lower()[:16]
                cat_was_auto = bool(message.get("was_auto"))
                logger.info(f"[{lanlan_name}] cat_greeting_check: duration={cat_duration:.0f}s tier={cat_tier or '-'} was_auto={cat_was_auto}")
                _fire_task(session_manager[lanlan_name].trigger_cat_greeting(cat_duration, cat_tier, cat_was_auto))

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
            if is_current:
                session_id.pop(lanlan_name, None)

        if is_current and lanlan_name in session_manager:
            await session_manager[lanlan_name].cleanup(expected_websocket=websocket)
