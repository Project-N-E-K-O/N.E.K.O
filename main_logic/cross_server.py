"""
本模块用于将lanlan的消息转发至所有相关服务器，包括：
1. Bullet Server。对实时内容进行监听并与直播间弹幕进行交互。
2. Monitor Server。将实时内容转发至所有副终端。副终端会同步播放与主终端完全相同的内容，但不具备交互性。同一时间只有一个主终端可以交互。
3. Memory Server。对对话历史进行总结、分析，并转为持久化记忆。
注意，cross server是一个单向的转发器，不会将任何内容回传给主进程。如需回传，目前仍需要建立专门的双向连接。
"""

import ssl
import uuid
from urllib.parse import quote

import asyncio
import time
import pickle
import aiohttp
from config import (
    MONITOR_SERVER_PORT,
    MEMORY_SERVER_PORT,
    COMMENTER_SERVER_PORT,
    AVATAR_INTERACTION_DEDUPE_WINDOW_MS,
    PENDING_USER_IMAGES_MAX,
)
from datetime import datetime
import json
import re
import httpx
from utils.frontend_utils import replace_blank, is_only_punctuation
from utils.internal_http_client import get_internal_http_client
from utils.logger_config import get_module_logger
from main_logic.agent_event_bus import publish_analyze_request_reliably

# Setup logger for this module
logger = get_module_logger(__name__, "Main")
AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = AVATAR_INTERACTION_DEDUPE_WINDOW_MS
MEMORY_CACHE_SCOPE_AVATAR = "avatar interaction cache"
MEMORY_CACHE_SCOPE_TURN_END = "turn end cache"
emoji_pattern = re.compile(r'[^\w\u4e00-\u9fff\s>][^\w\u4e00-\u9fff\s]{2,}[^\w\u4e00-\u9fff\s<]', flags=re.UNICODE)
emoji_pattern2 = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
emotion_pattern = re.compile('<(.*?)>')


async def _publish_analyze_request_with_fallback(lanlan_name: str, trigger: str, messages: list[dict], *, conversation_id: str | None = None) -> bool:
    """Publish analyze request via EventBus with ack/retry."""
    try:
        sent = await publish_analyze_request_reliably(
            lanlan_name=lanlan_name,
            trigger=trigger,
            messages=messages,
            ack_timeout_s=0.8,
            retries=1,
            conversation_id=conversation_id,
        )
        if sent:
            logger.debug(
                "[%s] analyze_request forwarded with ack: trigger=%s messages=%d",
                lanlan_name,
                trigger,
                len(messages) if isinstance(messages, list) else 0,
            )
            return True
    except Exception as e:
        logger.info(
            "[%s] analyze_request forwarding exception: trigger=%s error=%s",
            lanlan_name,
            trigger,
            e,
        )
        return False
    return False


def normalize_text(text):  # 对文本进行基本预处理
    text = text.strip()
    text = replace_blank(text)

    text = emoji_pattern2.sub('', text)
    text = emoji_pattern.sub('', text)
    text = emotion_pattern.sub("", text)
    if is_only_punctuation(text):
        return ""
    return text


def merge_unsynced_tail_assistants(chat_history, last_synced_index):
    """合并 last_synced_index 之后末尾连续的 assistant 消息为一条。

    只触碰未同步到 memory 的主动搭话消息，不影响已同步的正常回复。
    返回被消除的消息数（0 表示无需合并）。
    """
    tail = chat_history[last_synced_index:]
    if len(tail) < 2:
        return 0

    consecutive = 0
    for msg in reversed(tail):
        if msg.get('role') == 'assistant':
            consecutive += 1
        else:
            break

    if consecutive < 2:
        return 0

    first_idx = len(chat_history) - consecutive
    parts = []
    for msg in chat_history[first_idx:]:
        try:
            text = msg['content'][0]['text']
            if text:
                parts.append(text)
        except (KeyError, IndexError, TypeError):
            pass

    if not parts:
        return 0

    # 只保留最后一条主动搭话，丢弃之前的冗余内容，避免持久记忆膨胀
    merged = {'role': 'assistant', 'content': [{'type': 'text', 'text': parts[-1]}]}
    removed = consecutive - 1
    chat_history[first_idx:] = [merged]
    logger.info(f"[cleanup] 精简了 {consecutive} 条未同步的连续主动搭话消息，仅保留最后一条")
    return removed


def _extract_chat_item_text(item: dict) -> str:
    try:
        content = item.get('content') or []
        if not content:
            return ''
        first = content[0]
        if isinstance(first, dict):
            return str(first.get('text', '') or '')
        return str(first or '')
    except Exception:
        return ''


def _should_persist_avatar_interaction_memory(
    cache: dict[str, dict[str, int | str]],
    memory_note: str,
    dedupe_key: str = '',
    dedupe_rank: int = 1,
) -> bool:
    note = str(memory_note or '').strip()
    if not note:
        return False

    key = str(dedupe_key or note).strip() or note
    try:
        rank = max(1, int(dedupe_rank))
    except (TypeError, ValueError):
        rank = 1

    now_ms = int(time.time() * 1000)
    expired_keys = [
        cache_key
        for cache_key, entry in cache.items()
        if now_ms - int((entry or {}).get('ts', 0) or 0) >= AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS
    ]
    for cache_key in expired_keys:
        cache.pop(cache_key, None)

    previous = cache.get(key)
    if previous:
        previous_ts = int(previous.get('ts', 0) or 0)
        previous_rank = int(previous.get('rank', 1) or 1)
        if now_ms - previous_ts < AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS and rank <= previous_rank:
            return False

    cache[key] = {
        'ts': now_ms,
        'rank': rank,
        'note': note,
    }
    return True


def _normalize_pending_user_attachments(pending_user_images: list) -> list[dict]:
    attachments = []
    for raw in pending_user_images or []:
        url = str(raw or "").strip()
        if not url:
            continue
        attachments.append({
            "type": "image_url",
            "url": url,
        })
    return attachments


def _build_recent_analyze_messages(
    chat_history: list,
    pending_user_images: list,
    limit: int = 6,
    *,
    allow_attach_to_last_user: bool = False,
) -> list[dict]:
    recent: list[dict] = []
    last_user_idx: int | None = None
    last_user_source_idx: int | None = None
    slice_start = max(0, len(chat_history) - limit)

    for source_idx, item in enumerate(chat_history[-limit:], start=slice_start):
        if item.get('role') not in ['user', 'assistant']:
            continue
        try:
            txt = item['content'][0]['text'] if item.get('content') else ''
        except Exception:
            txt = ''
        txt = str(txt or '')
        if txt == '':
            continue
        recent.append({'role': item.get('role'), 'content': txt})
        if item.get('role') == 'user':
            last_user_idx = len(recent) - 1
            last_user_source_idx = source_idx

    attachments = _normalize_pending_user_attachments(pending_user_images)
    if attachments:
        if (
            not allow_attach_to_last_user
            or last_user_idx is None
            or last_user_source_idx is None
            or last_user_source_idx < slice_start
        ):
            recent.append({
                'role': 'user',
                'content': '',
                'attachments': attachments,
            })
        else:
            recent[last_user_idx]['attachments'] = attachments

    return [msg for msg in recent if msg.get('content') or msg.get('attachments')]


async def keep_reader(ws: aiohttp.ClientWebSocketResponse):
    """保持 WebSocket 连接活跃的读取循环"""
    try:
        while True:
            try:
                msg = await ws.receive(timeout=30)
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
    except Exception:
        pass


async def _post_memory_server(
    endpoint: str,
    lanlan_name: str,
    payload: list[dict],
    *,
    timeout_s: float,
) -> tuple[bool, str, dict]:
    """Post history payload to memory_server and treat only 2xx+valid JSON as success."""
    encoded_name = quote(lanlan_name, safe="")
    url = f"http://127.0.0.1:{MEMORY_SERVER_PORT}/{endpoint}/{encoded_name}"

    client = get_internal_http_client()
    response = await client.post(
        url,
        json={"input_history": json.dumps(payload, indent=2, ensure_ascii=False)},
        timeout=timeout_s,
    )
    raw_body = response.text
    status_code = response.status_code

    if status_code < 200 or status_code >= 300:
        return False, f"HTTP {status_code} (body_len={len(raw_body)})", {}

    try:
        result = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return False, f"non-JSON response (body_len={len(raw_body)})", {}

    if not isinstance(result, dict):
        return False, f"unexpected response type: {type(result).__name__}", {}

    if result.get("status") == "error":
        return False, str(result.get("message", "unknown_error")), result

    return True, "", result


def _is_expected_memory_write_exception(exc: Exception) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError, httpx.HTTPError, ConnectionError, OSError))


def _mark_memory_cache_success(lanlan_name: str, scope: str, health_state: dict[str, bool]) -> None:
    if health_state.get(scope, False):
        logger.info(f"[{lanlan_name}] {scope} 已恢复")
    health_state[scope] = False


def _mark_memory_cache_business_failure(
    lanlan_name: str,
    scope: str,
    detail: str,
    health_state: dict[str, bool],
) -> None:
    was_unhealthy = health_state.get(scope, False)
    health_state[scope] = True
    if was_unhealthy:
        logger.debug(f"[{lanlan_name}] {scope} 失败（持续）: {detail}")
    else:
        logger.debug(f"[{lanlan_name}] {scope} 失败（进入失败状态）: {detail}")


def _mark_memory_cache_exception(
    lanlan_name: str,
    scope: str,
    exc: Exception,
    health_state: dict[str, bool],
) -> None:
    was_unhealthy = health_state.get(scope, False)
    health_state[scope] = True
    reason = "网络层" if _is_expected_memory_write_exception(exc) else "未知类型"
    msg = f"[{lanlan_name}] {scope} 异常（{reason}{'，持续' if was_unhealthy else '，进入异常状态'}）: {type(exc).__name__}: {exc}"
    if was_unhealthy:
        logger.debug(msg)
    elif reason == "未知类型":
        logger.warning(msg, exc_info=True)
    else:
        logger.warning(msg)


async def run_sync_connector(
    message_queue: asyncio.Queue,
    lanlan_name,
    sync_server_url=f"ws://127.0.0.1:{MONITOR_SERVER_PORT}",
    config=None,
    status_callback=None,
):
    """Async-native 同步连接器，跑在调用方主 event loop 上。

    历史：最早是 multiprocessing 子进程（``*_process`` 命名遗留），
    后来变成 daemon Thread + 内部 ``asyncio.new_event_loop()``。现在合并到
    主 loop：
    - 取消通过 ``asyncio.CancelledError`` 触发；cleanup 在 finally 完成
    - ``message_queue`` 改为 ``asyncio.Queue``，用 ``await get()`` 替代 20ms
      轮询。生产端 ``put_nowait()`` 与旧版 ``queue.Queue`` API 兼容
    - 应用层 heartbeat 节流到 ``HEARTBEAT_INTERVAL`` 一次（当前 1s）；底层 ws
      ping/pong 由 ``aiohttp.ws_connect(heartbeat=10)`` 自动维持

    Args:
        status_callback: 可选 ``Callable[[str], None]``。运行在主 loop 上，
            可直接 ``asyncio.create_task(...)``，无需 ``run_coroutine_threadsafe``。
    """
    chat_history: list = []
    default_config = {'bullet': True, 'monitor': True}
    if config is None:
        config = {}
    config = default_config | config

    # 历史保留：旧 thread 版本里多处 ``if shutdown_event.is_set(): break`` 用于
    # 子进程时代跳过对正在关闭的 memory_server 的 HTTP 调用。改 async 后取消
    # 由 await 点自然 raise CancelledError 顶替，guard 不再有意义。统一替换成
    # 永远 False 的 stub，避免大面积重排缩进；这些 guard 现在是死代码，但语义
    # 仍然正确（不阻挡正常路径），后续清理 PR 可一并删。
    class _NeverShutdown:
        @staticmethod
        def is_set() -> bool:
            return False
    shutdown_event = _NeverShutdown()

    sync_session = None
    sync_ws = None
    sync_reader = None
    binary_session = None
    binary_ws = None
    binary_reader = None
    bullet_session = None
    bullet_ws = None
    bullet_reader = None

    user_input_cache = ''
    text_output_cache = ''  # lanlan的当前消息
    current_turn = 'user'
    had_user_input_this_turn = False  # 当前 turn 是否有用户输入（False = 主动搭话）
    current_turn_start_index = 0
    last_screen = None
    pending_user_images: list = []
    last_synced_index = 0  # 用于 turn end 时仅同步新增消息到 memory，避免 memory_browser 不更新
    avatar_interaction_memory_cache: dict[str, dict[str, int | str]] = {}
    memory_cache_health_state = {
        MEMORY_CACHE_SCOPE_AVATAR: False,
        MEMORY_CACHE_SCOPE_TURN_END: False,
    }

    last_heartbeat_at = 0.0
    # 节流后的应用层 heartbeat 间隔。原因不是省 CPU（aiohttp ws_connect(heartbeat=10)
    # 已经在底层做了 ping/pong），而是控制 send-fail → ws=None → 下一轮 reconnect
    # 这条链路的检测延迟。1s 在 idle 期足够安静（远低于改造前 50Hz），同时把
    # 断线检测窗口锁在 ~1s。设太大（比如 10s）会让 idle→burst 切换时第一波消息
    # 撞到旧 ws 上更长一段时间。
    HEARTBEAT_INTERVAL = 1.0
    IDLE_TIMEOUT = 10.0  # 没消息时仍每 10s 唤醒一次 ws 检查/重连
    # 外层 loop 唤醒粒度必须 <= heartbeat 间隔，否则 idle 期 heartbeat / reconnect
    # 检查仍要等 IDLE_TIMEOUT 才轮上一次——HEARTBEAT_INTERVAL 调到 1s 但 wait_for
    # 还卡 10s 的话，注释里写的"~1s 探测窗口"就是空头支票。
    LOOP_TICK = min(IDLE_TIMEOUT, HEARTBEAT_INTERVAL)

    try:
        while True:
            message = None
            try:
                # check_async_blocking.py 用纯名字启发式（``*_queue.get()`` 一律拍），
                # 类型标注不影响判断；这里 message_queue 实际是 ``asyncio.Queue``，
                # ``.get()`` 返回 coroutine 由 wait_for 调度，不会阻塞 event loop。
                message = await asyncio.wait_for(
                    message_queue.get(),  # noqa: ASYNC_BLOCK — asyncio.Queue, not queue.Queue
                    timeout=LOOP_TICK,
                )
            except asyncio.TimeoutError:
                # 超时 = 没消息：保持 message=None 走到下面 ws 维持段做周期性
                # reconnect/heartbeat 检查。idle 期每 LOOP_TICK 触发一次（当前
                # 1s，由 min(IDLE_TIMEOUT, HEARTBEAT_INTERVAL) 决定）；不打日志
                # 否则会变成稳定噪音。
                pass

            if message is not None:
                try:
                    if message["type"] == "json":
                        # Forward to monitor if enabled
                        if config['monitor'] and sync_ws:
                            await sync_ws.send_json(message["data"])

                        # Only treat assistant turn when it's a gemini_response
                        if message["data"].get("type") == "gemini_response":
                            if current_turn == 'user':  # assistant new message starts
                                had_user_input_this_turn = bool(user_input_cache)
                                if user_input_cache:
                                    chat_history.append({'role': 'user', 'content': [{"type": "text", "text": user_input_cache}]})
                                    user_input_cache = ''
                                current_turn = 'assistant'
                                current_turn_start_index = len(chat_history)
                                text_output_cache = datetime.now().strftime('[%Y%m%d %a %H:%M] ')

                                if config['bullet'] and bullet_ws:
                                    try:
                                        last_user = last_ai = None
                                        for i in chat_history[::-1]:
                                            if i["role"] == "user":
                                                last_user = i['content'][0]['text']
                                                break
                                        for i in chat_history[::-1]:
                                            if i["role"] == "assistant":
                                                last_ai = i['content'][0]['text']
                                                break

                                        message_data = {
                                            "user": last_user,
                                            "ai": last_ai,
                                            "screen": last_screen
                                        }
                                        binary_message = pickle.dumps(message_data)
                                        await bullet_ws.send_bytes(binary_message)
                                    except Exception as e:
                                        logger.error(f"[{lanlan_name}] Error when sending to commenter: {e}")

                            # Append assistant streaming text
                            try:
                                text_output_cache += message["data"].get("text", "")
                            except Exception:
                                pass

                    elif message["type"] == "binary":
                        if config['monitor'] and binary_ws:
                            await binary_ws.send_bytes(message["data"])

                    elif message["type"] == "user":  # 准备转录
                        data = message["data"].get("data")
                        input_type = message["data"].get("input_type")
                        if input_type == "transcript": # 暂时只处理语音，后续还需要记录图片
                            if user_input_cache == '' and config['monitor'] and sync_ws:
                                await sync_ws.send_json({'type': 'user_activity'}) #用于打断前端声音播放
                            user_input_cache += data
                            # 发送用户转录到 monitor 供副终端显示
                            if config['monitor'] and sync_ws and data:
                                await sync_ws.send_json({'type': 'user_transcript', 'text': data})
                        elif input_type == "screen":
                            last_screen = data
                            if data:
                                pending_user_images.append(data)
                                if len(pending_user_images) > PENDING_USER_IMAGES_MAX:
                                    del pending_user_images[:-PENDING_USER_IMAGES_MAX]
                        elif input_type == "camera":
                            last_screen = data
                            if data:
                                pending_user_images.append(data)
                                if len(pending_user_images) > PENDING_USER_IMAGES_MAX:
                                    del pending_user_images[:-PENDING_USER_IMAGES_MAX]

                    elif message["type"] == "system":
                        try:
                            if message["data"] == "google disconnected":
                                if len(text_output_cache) > 0:
                                    chat_history.append({'role': 'system', 'content': [
                                        {'type': 'text', 'text': "网络错误，您已断开连接！"}]})
                                text_output_cache = ''
                            
                            elif message["data"] == "response_discarded_clear":
                                logger.debug(f"[{lanlan_name}] 收到 response_discarded_clear，清空当前输出缓存")
                                text_output_cache = ''
                            
                            if message["data"] == "renew session":
                                # 检查是否正在关闭
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过renew session处理")
                                    break
                                
                                # 先处理未完成的用户输入缓存（如果有）
                                if user_input_cache:
                                    chat_history.append({'role': 'user', 'content': [{"type": "text", "text": user_input_cache}]})
                                    user_input_cache = ''
                                
                                # 再处理未完成的输出缓存（如果有）
                                current_turn = 'user'
                                text_output_cache = normalize_text(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                            {'role': 'assistant', 'content': [{'type': 'text', 'text': text_output_cache}]})
                                text_output_cache = ''
                                current_turn_start_index = len(chat_history)
                                # 合并未同步的连续主动搭话消息
                                merge_unsynced_tail_assistants(chat_history, last_synced_index)

                                # 再次检查关闭状态
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过memory_server请求")
                                    chat_history.clear()
                                    break
                                
                                # 增量发送：只发 /cache 未覆盖的剩余消息，触发 LLM 结算
                                remaining = chat_history[last_synced_index:]
                                logger.info(f"[{lanlan_name}] 热重置：聊天历史 {len(chat_history)} 条，增量 {len(remaining)} 条")
                                # 确定调用端点：有增量走 /renew，无增量走 /settle（补全摘要+时间戳）
                                _renew_endpoint = "renew" if remaining else "settle"
                                _renew_payload = remaining if remaining else []
                                try:
                                    ok, err_detail, _ = await _post_memory_server(
                                        _renew_endpoint,
                                        lanlan_name,
                                        _renew_payload,
                                        timeout_s=30.0,
                                    )
                                    if not ok:
                                        logger.error(f"[{lanlan_name}] 热重置记忆处理失败 ({_renew_endpoint}): {err_detail}")
                                        if status_callback:
                                            try:
                                                status_callback(f"⚠️ 热重置记忆失败: {err_detail}")
                                            except Exception:
                                                pass
                                    else:
                                        logger.info(f"[{lanlan_name}] 热重置记忆已成功上传到 memory_server ({_renew_endpoint})")
                                except RuntimeError as e:
                                    if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                        logger.info(f"[{lanlan_name}] 进程正在关闭，{_renew_endpoint}请求已取消")
                                    else:
                                        logger.exception(f"[{lanlan_name}] 调用 /{_renew_endpoint} API 失败: {type(e).__name__}: {e}")
                                except Exception as e:
                                    logger.exception(f"[{lanlan_name}] 调用 /{_renew_endpoint} API 失败: {type(e).__name__}: {e}")
                                chat_history.clear()
                                last_synced_index = 0

                            if message["data"] in ('turn end', 'turn end agent_callback'): # lanlan的消息结束了
                                is_agent_callback_turn_end = (message["data"] == 'turn end agent_callback')
                                current_turn = 'user'
                                text_output_cache = normalize_text(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                        {'role': 'assistant', 'content': [{'type': 'text', 'text': text_output_cache}]})
                                text_output_cache = ''
                                # 后端打标：meta 与 turn end 事件原子绑定，不再依赖独立通道
                                # 的 pending_* 状态。kind == 'avatar_interaction' 才进入隔离
                                # 路径，其它情况按 proactive / normal 处理。
                                turn_end_meta = message.get("meta") if isinstance(message, dict) else None
                                if not isinstance(turn_end_meta, dict):
                                    turn_end_meta = None
                                # meta 由 core 端 turn end 原子打标；avatar 互动若被用户
                                # 接管则 core 会清空 meta，所以这里不再需要 had_user_input
                                # 二次兜底，避免"用户语音缓存刚好先入队"误落回普通路径。
                                is_avatar_interaction_turn = (
                                    turn_end_meta is not None
                                    and turn_end_meta.get("kind") == "avatar_interaction"
                                )
                                avatar_turn_start_index = min(current_turn_start_index, len(chat_history))
                                avatar_turn_slice = chat_history[avatar_turn_start_index:] if is_avatar_interaction_turn else []
                                avatar_turn_assistant_text = ''
                                if is_avatar_interaction_turn and avatar_turn_slice:
                                    avatar_turn_assistant_text = _extract_chat_item_text(avatar_turn_slice[-1]).strip()
                                # 主动搭话（无用户输入）时：合并未同步的连续 assistant 消息，不写入 /cache
                                if not had_user_input_this_turn and not is_avatar_interaction_turn:
                                    merge_unsynced_tail_assistants(chat_history, last_synced_index)
                                if config['monitor'] and sync_ws:
                                    await sync_ws.send_json({'type': 'turn end'})
                                if is_avatar_interaction_turn:
                                    # Avatar tool turns are handled in an isolated
                                    # memory path so they never leak into analyzer
                                    # requests or later session-end bulk syncs.
                                    memory_note = str(turn_end_meta.get('memory_note') or '').strip()
                                    memory_dedupe_key = str(turn_end_meta.get('memory_dedupe_key') or '').strip()
                                    memory_dedupe_rank = turn_end_meta.get('memory_dedupe_rank', 1)
                                    # 先快照 dedupe 槽位，/cache 失败时回滚，避免 8s 窗口内后续
                                    # 真实互动被误判为"已记录"丢失。
                                    dedupe_rollback_key = (memory_dedupe_key or memory_note).strip()
                                    dedupe_prior_entry = avatar_interaction_memory_cache.get(dedupe_rollback_key)
                                    should_persist_avatar_turn = (
                                        bool(avatar_turn_assistant_text)
                                        and _should_persist_avatar_interaction_memory(
                                            avatar_interaction_memory_cache,
                                            memory_note,
                                            memory_dedupe_key,
                                            memory_dedupe_rank,
                                        )
                                    )
                                    if should_persist_avatar_turn:
                                        avatar_memory_messages = [
                                            {'role': 'user', 'content': [{'type': 'text', 'text': memory_note}]},
                                            {'role': 'assistant', 'content': [{'type': 'text', 'text': avatar_turn_assistant_text}]},
                                        ]
                                        cache_persist_failed = False
                                        try:
                                            ok, err_detail, _ = await _post_memory_server(
                                                "cache",
                                                lanlan_name,
                                                avatar_memory_messages,
                                                timeout_s=10.0,
                                            )
                                            if ok:
                                                _mark_memory_cache_success(
                                                    lanlan_name,
                                                    MEMORY_CACHE_SCOPE_AVATAR,
                                                    memory_cache_health_state,
                                                )
                                            else:
                                                cache_persist_failed = True
                                                _mark_memory_cache_business_failure(
                                                    lanlan_name,
                                                    MEMORY_CACHE_SCOPE_AVATAR,
                                                    err_detail,
                                                    memory_cache_health_state,
                                                )
                                        except Exception as e:
                                            cache_persist_failed = True
                                            _mark_memory_cache_exception(
                                                lanlan_name,
                                                MEMORY_CACHE_SCOPE_AVATAR,
                                                e,
                                                memory_cache_health_state,
                                            )
                                        if cache_persist_failed and dedupe_rollback_key:
                                            if dedupe_prior_entry is not None:
                                                avatar_interaction_memory_cache[dedupe_rollback_key] = dedupe_prior_entry
                                            else:
                                                avatar_interaction_memory_cache.pop(dedupe_rollback_key, None)

                                    if avatar_turn_slice:
                                        del chat_history[avatar_turn_start_index:]
                                        if last_synced_index > len(chat_history):
                                            last_synced_index = len(chat_history)

                                    current_turn_start_index = len(chat_history)
                                    # avatar 分支 continue 会跳过正常 finally 的 pending_user_images
                                    # 清理，陈旧截图/摄像帧若留到下一轮 analyzer 会变成"跨轮污染"。
                                    pending_user_images = []
                                    continue
                                # 非阻塞地向tool_server发送最近对话，供分析器识别潜在任务。
                                # 仅 agent-callback 专用通道会显式跳过，避免任务结果回调引发二次分析。
                                if not shutdown_event.is_set():
                                    try:
                                        # 构造最近的消息摘要，并保留本轮最近的图片附件
                                        recent = _build_recent_analyze_messages(
                                            chat_history,
                                            pending_user_images,
                                            allow_attach_to_last_user=had_user_input_this_turn,
                                        )
                                        has_user = any(m.get('role') == 'user' for m in recent)
                                        logger.info(
                                            f"[{lanlan_name}] turn_end analyze check: "
                                            f"history={len(chat_history)} recent={len(recent)} "
                                            f"has_user={has_user} had_input={had_user_input_this_turn} "
                                            f"agent_callback_turn={is_agent_callback_turn_end}"
                                        )
                                        if recent and not is_agent_callback_turn_end:
                                            sent = await _publish_analyze_request_with_fallback(
                                                lanlan_name=lanlan_name,
                                                trigger="turn_end",
                                                messages=recent,
                                                conversation_id=uuid.uuid4().hex,
                                            )
                                            if sent:
                                                logger.debug(f"[{lanlan_name}] analyze_request dispatch success (turn_end), messages={len(recent)}")
                                            else:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch failed (turn_end), messages={len(recent)}")
                                    except asyncio.TimeoutError:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer超时")
                                    except RuntimeError as e:
                                        if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                            logger.info(f"[{lanlan_name}] 进程正在关闭，跳过analyzer请求")
                                        else:
                                            logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e}")
                                    except Exception as e:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e}")
                                    finally:
                                        pending_user_images = []

                                # Turn end 轻量缓存：仅写入 recent history，不触发 LLM 摘要/整理
                                # 主动搭话不写缓存——等用户回应后随下一轮正常 turn 一起入库
                                if had_user_input_this_turn and not shutdown_event.is_set() and last_synced_index < len(chat_history):
                                    new_messages = chat_history[last_synced_index:]
                                    try:
                                        ok, err_detail, _ = await _post_memory_server(
                                            "cache",
                                            lanlan_name,
                                            new_messages,
                                            timeout_s=10.0,
                                        )
                                        if ok:
                                            _mark_memory_cache_success(
                                                lanlan_name,
                                                MEMORY_CACHE_SCOPE_TURN_END,
                                                memory_cache_health_state,
                                            )
                                            last_synced_index = len(chat_history)
                                        else:
                                            _mark_memory_cache_business_failure(
                                                lanlan_name,
                                                MEMORY_CACHE_SCOPE_TURN_END,
                                                err_detail,
                                                memory_cache_health_state,
                                            )
                                    except Exception as e:
                                        _mark_memory_cache_exception(
                                            lanlan_name,
                                            MEMORY_CACHE_SCOPE_TURN_END,
                                            e,
                                            memory_cache_health_state,
                                        )

                            elif message["data"] == 'session end': # 当前session结束了
                                # 检查是否正在关闭，如果是则跳过网络操作
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过session end处理")
                                    break
                                
                                # 先处理未完成的用户输入缓存（如果有）
                                if user_input_cache:
                                    chat_history.append({'role': 'user', 'content': [{"type": "text", "text": user_input_cache}]})
                                    user_input_cache = ''
                                
                                # 再处理未完成的输出缓存（如果有）
                                current_turn = 'user'
                                text_output_cache = normalize_text(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                        {'role': 'assistant', 'content': [{'type': 'text', 'text': text_output_cache}]})
                                text_output_cache = ''
                                current_turn_start_index = len(chat_history)
                                # 合并未同步的连续主动搭话消息
                                merge_unsynced_tail_assistants(chat_history, last_synced_index)

                                # 向tool_server发送最近对话，供分析器识别潜在任务（与turn end逻辑相同）
                                # 再次检查关闭状态
                                if not shutdown_event.is_set():
                                    try:
                                        # 构造最近的消息摘要，并保留本轮最近的图片附件
                                        recent = _build_recent_analyze_messages(
                                            chat_history,
                                            pending_user_images,
                                            allow_attach_to_last_user=had_user_input_this_turn,
                                        )
                                        has_user = any(m.get('role') == 'user' for m in recent)
                                        if recent and has_user:
                                            sent = await _publish_analyze_request_with_fallback(
                                                lanlan_name=lanlan_name,
                                                trigger="session_end",
                                                messages=recent,
                                                conversation_id=uuid.uuid4().hex,
                                            )
                                            if sent:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch success (session_end), messages={len(recent)}")
                                            else:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch failed (session_end), messages={len(recent)}")
                                    except asyncio.TimeoutError:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer超时 (session end)")
                                    except RuntimeError as e:
                                        if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                            logger.info(f"[{lanlan_name}] 进程正在关闭，跳过analyzer请求")
                                        else:
                                            logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e} (session end)")
                                    except Exception as e:
                                        logger.debug(f"[{lanlan_name}] 发送到analyzer失败: {e} (session end)")
                                    finally:
                                        pending_user_images = []
                                
                                # 再次检查关闭状态
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过 session end 收尾")
                                    chat_history.clear()
                                    last_synced_index = 0
                                    break
                                
                                # 会话结算：
                                # - 有增量（未被 /cache 覆盖）→ /process
                                # - 无增量但有历史（已全部 /cache）→ /settle，补全摘要/时间索引/事实提取
                                remaining = chat_history[last_synced_index:]
                                logger.info(f"[{lanlan_name}] 会话结束：聊天历史 {len(chat_history)} 条，增量 {len(remaining)} 条")
                                _settle_endpoint = "process" if remaining else "settle"
                                _settle_payload = remaining if remaining else []
                                if not shutdown_event.is_set():
                                    try:
                                        ok, err_detail, _ = await _post_memory_server(
                                            _settle_endpoint,
                                            lanlan_name,
                                            _settle_payload,
                                            timeout_s=30.0,
                                        )
                                        if not ok:
                                            logger.warning(f"[{lanlan_name}] session end 记忆结算失败 ({_settle_endpoint}): {err_detail}")
                                            if status_callback:
                                                try:
                                                    status_callback(f"⚠️ 记忆摘要失败: {err_detail}")
                                                except Exception:
                                                    pass
                                        else:
                                            logger.info(f"[{lanlan_name}] session end 记忆结算完成（{_settle_endpoint}），{len(_settle_payload)} 条消息")
                                    except Exception as e:
                                        logger.warning(f"[{lanlan_name}] session end 记忆结算失败 ({_settle_endpoint}): {e}")
                                        if status_callback:
                                            try:
                                                status_callback(f"⚠️ 记忆结算异常: {type(e).__name__}")
                                            except Exception:
                                                pass
                                chat_history.clear()
                                last_synced_index = 0
                        except Exception as e:
                            logger.error(f"[{lanlan_name}] System message error: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"[{lanlan_name}] Message processing error: {e}", exc_info=True)

            # WebSocket 连接管理（独立于消息处理）
            try:
                # 如果连接不存在，尝试建立连接
                try:
                    if config['monitor']:
                        if sync_ws is None:
                            if sync_session:
                                await sync_session.close()
                            sync_session = aiohttp.ClientSession()
                            try:
                                sync_ws = await sync_session.ws_connect(
                                    f"{sync_server_url}/sync/{lanlan_name}",
                                    heartbeat=10,
                                )
                                # print(f"[Sync Process] [{lanlan_name}] 文本连接已建立")
                                sync_reader = asyncio.create_task(keep_reader(sync_ws))
                            except Exception:
                                # logger.warning(f"[{lanlan_name}] Monitor文本连接失败: {e}")
                                sync_ws = None

                        if binary_ws is None:
                            if binary_session:
                                await binary_session.close()
                            binary_session = aiohttp.ClientSession()
                            try:
                                binary_ws = await binary_session.ws_connect(
                                    f"{sync_server_url}/sync_binary/{lanlan_name}",
                                    heartbeat=10,
                                )
                                # print(f"[Sync Process] [{lanlan_name}] 二进制连接已建立")
                                binary_reader = asyncio.create_task(keep_reader(binary_ws))
                            except Exception:
                                # logger.warning(f"[{lanlan_name}] Monitor二进制连接失败: {e}")
                                binary_ws = None

                        # 发送应用层心跳（节流到每 HEARTBEAT_INTERVAL 一次）。
                        # 底层 ws ping/pong 已由 aiohttp.ws_connect(heartbeat=10)
                        # 维持，应用层 heartbeat 主要作为 send 失败 = 连接断的探针。
                        _now_ts = time.time()
                        _should_heartbeat = (_now_ts - last_heartbeat_at >= HEARTBEAT_INTERVAL)
                        if _should_heartbeat:
                            last_heartbeat_at = _now_ts

                        if _should_heartbeat and config['monitor'] and sync_ws:
                            try:
                                await sync_ws.send_json({"type": "heartbeat", "timestamp": _now_ts})
                            except Exception:
                                sync_ws = None

                        if _should_heartbeat and config['monitor'] and binary_ws:
                            try:
                                await binary_ws.send_bytes(b'\x00\x01\x02\x03')
                            except Exception:
                                binary_ws = None

                except Exception as e:
                    logger.error(f"[{lanlan_name}] Monitor连接异常: {e}", exc_info=True)
                    sync_ws = None
                    binary_ws = None

                try:
                    if config['bullet']:
                        if bullet_ws is None:
                            if bullet_session:
                                await bullet_session.close()
                            bullet_session = aiohttp.ClientSession()
                            try:
                                bullet_ws = await bullet_session.ws_connect(
                                    f"wss://127.0.0.1:{COMMENTER_SERVER_PORT}/sync/{lanlan_name}",
                                    ssl=ssl._create_unverified_context()
                                )
                                # print(f"[Sync Process] [{lanlan_name}] Bullet连接已建立")
                                bullet_reader = asyncio.create_task(keep_reader(bullet_ws))
                            except Exception:
                                # Bullet 连接失败是正常的（该服务可能未启动）
                                bullet_ws = None
                except Exception as e:
                    logger.error(f"[{lanlan_name}] Bullet连接异常: {e}", exc_info=True)
                    bullet_ws = None

                # 不再需要 0.02s 的 spin sleep —— 上层 await message_queue.get()
                # 已经是阻塞的，无消息时整个 loop 自然挂起，不会忙等。

            except asyncio.CancelledError:
                # 让外层 try 的 finally 接管 cleanup
                raise
            except Exception as e:
                logger.error(f"[{lanlan_name}] WebSocket连接异常: {e}")
                sync_ws = None
                binary_ws = None
                bullet_ws = None
                await asyncio.sleep(0.5)  # 出错时退避 0.5s 再重连，避免快速失败 spam

    except asyncio.CancelledError:
        raise
    finally:
        # 关闭资源（并行：3 个 ws + 3 个 session 互相独立）。无论是正常完成、
        # 取消、还是异常退出，都走这里清理。
        async def _safe_close(target):
            if target is None:
                return
            try:
                await target.close()
            except Exception as e:
                # 已进入重连/退出阶段，close 失败不影响后续流程；记 debug 方便排障
                logger.debug(f"_safe_close: ignored exception during close: {e}")

        await asyncio.gather(
            _safe_close(sync_ws), _safe_close(binary_ws), _safe_close(bullet_ws),
            _safe_close(sync_session), _safe_close(binary_session), _safe_close(bullet_session),
            return_exceptions=True,
        )
        # cancel reader task 后必须 await，否则 task 在 loop 关闭时还 pending
        # 会触发 "Task was destroyed but it is pending!" 告警。keep_reader 内部
        # 已经处理 CancelledError → break，所以 gather(return_exceptions=True)
        # 会安静地收掉 cancel 副作用。
        readers = [r for r in (sync_reader, binary_reader, bullet_reader) if r is not None]
        # asyncio.Task.cancel() 不抛异常（返回 bool），所以这里不需要 try/except
        # 包裹——旧线程版本的过度防御代码已删除。
        for rdr in readers:
            rdr.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        # 注意：不在这里调用 aclose_internal_http_client_current_loop()。
        # 旧版子进程/独立线程拥有自己的 event loop，其 http client 也是 per-loop
        # 缓存的，退出时需要 close。现在合并到主 loop，client 由主代码共享，
        # 我们没有所有权，不应当 close。
