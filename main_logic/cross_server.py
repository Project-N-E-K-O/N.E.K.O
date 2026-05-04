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


# Mirror schema + detection now lives in main_logic.mirror_meta;
# cross_server only consumes those helpers — does not own the schema.
from main_logic.mirror_meta import (
    MIRROR_USER_INPUT_TYPES,
    is_mirror_assistant_message,
    is_mirror_turn_end_meta,
)


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


async def _safe_close(target) -> None:
    """关 ws / session 的统一兜底。已进入清理阶段，close 失败不影响后续流程。"""
    if target is None:
        return
    try:
        await target.close()
    except Exception as e:
        logger.debug(f"_safe_close: ignored exception during close: {e}")


class _WSSlot:
    """单 ws 端点的共享状态。主 loop / reader / maintainer 三方读写。

    主 loop 只对 ``ws`` / ``dead_event`` 做读 + 标死，从不调 ws_connect / close；
    全部生命周期由 :func:`_slot_maintainer` 在独立 task 里管理。
    """
    __slots__ = ("name", "url", "lanlan_name", "ws_kwargs",
                 "ws", "session", "reader", "maintainer", "dead_event")

    def __init__(self, name: str, url: str, lanlan_name: str, *, ws_kwargs=None):
        self.name = name
        self.url = url
        self.lanlan_name = lanlan_name
        self.ws_kwargs = ws_kwargs or {}
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.session: aiohttp.ClientSession | None = None
        self.reader: asyncio.Task | None = None
        self.maintainer: asyncio.Task | None = None
        self.dead_event = asyncio.Event()
        self.dead_event.set()  # 初始即"死" → maintainer 第一次 wait 立即返回，触发首连


def _mark_dead(slot: _WSSlot) -> None:
    """标记 slot 为断开。幂等。主 loop send 失败 / reader 检测 close 都调这个。"""
    slot.ws = None
    slot.dead_event.set()


async def _slot_reader(slot: _WSSlot, ws: aiohttp.ClientWebSocketResponse) -> None:
    """绑定到具体 ws 实例的读循环。检测 close → mark_dead 唤醒 maintainer。

    ws 通过参数传入而不是从 slot 读，避免 maintainer 已经替换 slot.ws 后老 reader
    误标新 ws 为死。被 maintainer 主动 cancel 时不调 mark_dead。
    """
    try:
        while True:
            try:
                msg = await ws.receive(timeout=30)
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                return
    except Exception:
        pass
    # 仅当当前 slot.ws 仍是这条 ws 时才标死，避免覆盖 maintainer 刚装上的新连接
    if slot.ws is ws:
        _mark_dead(slot)


async def _slot_maintainer(
    slot: _WSSlot,
    *,
    backoff_min: float = 0.25,
    backoff_max: float = 1.5,
) -> None:
    """单 slot 的 reconnect 循环，事件驱动 + 指数退避。

    monitor 是外挂；不在的时候这条 task 会以 backoff_max 节奏静默重试，主 sync loop
    完全不感知。

    周期保证：每次尝试 = ``wait_for(ws_connect, timeout=backoff)``，所以 cycle 时长
    被 backoff 双向夹住——
    - 上限：connect 挂到 timeout 才失败，cycle ≈ backoff
    - 下限：connect 极速 fail（refused），sleep 补足到 backoff，cycle = backoff

    backoff_max=1.5s 即"最差 1.5s 轮询"。Windows 上裸 ws_connect 失败有时要 ~4s
    （TCP SYN timeout），不加 wait_for 这条保证就破了。
    """
    backoff = backoff_min
    while True:
        await slot.dead_event.wait()

        # 旧 reader / session 异步清理（不阻塞重连本身）
        old_reader = slot.reader
        old_session = slot.session
        slot.reader = None
        slot.session = None
        if old_reader is not None:
            old_reader.cancel()
        if old_session is not None:
            asyncio.create_task(_safe_close(old_session))

        new_session = aiohttp.ClientSession()
        slot.session = new_session
        cycle_start = time.monotonic()
        try:
            new_ws = await asyncio.wait_for(
                new_session.ws_connect(slot.url, **slot.ws_kwargs),
                timeout=backoff,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(
                f"[{slot.lanlan_name}] {slot.name} ws_connect 失败: "
                f"{type(e).__name__}: {e} (backoff {backoff:.2f}s)"
            )
            # 失败的 session 立即后台收，避免 lingering
            asyncio.create_task(_safe_close(new_session))
            slot.session = None
            # 拉到 backoff 周期：fast-fail (refused, ms 级) 时补 sleep；
            # timeout 到点失败时基本不 sleep 直接进下一轮
            elapsed = time.monotonic() - cycle_start
            if elapsed < backoff:
                await asyncio.sleep(backoff - elapsed)
            backoff = min(backoff * 2, backoff_max)
            continue  # dead_event 仍 set，下轮立即再试

        # 顺序很重要：先把 ws 装上 + 清 dead_event，再创建 reader。
        # 否则 reader 可能在 clear() 之前就 mark_dead，clear 会把这个信号擦掉。
        slot.ws = new_ws
        slot.dead_event.clear()
        slot.reader = asyncio.create_task(
            _slot_reader(slot, new_ws),
            name=f"WSReader-{slot.name}-{slot.lanlan_name}",
        )
        backoff = backoff_min


async def _try_send_json(slot: _WSSlot | None, payload: dict) -> bool:
    """fail-soft 发送。slot 不存在 / ws 死 / send 抛异常都返回 False，不向上传播。"""
    if slot is None:
        return False
    ws = slot.ws
    if ws is None:
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        _mark_dead(slot)
        return False


async def _try_send_bytes(slot: _WSSlot | None, payload: bytes) -> bool:
    if slot is None:
        return False
    ws = slot.ws
    if ws is None:
        return False
    try:
        await ws.send_bytes(payload)
        return True
    except Exception:
        _mark_dead(slot)
        return False


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

    架构：
    - 主 loop：``await message_queue.get()`` 阻塞等消息，处理后用 ``_try_send_*``
      把数据塞进 ws；ws 死/不存在时 fail-soft 跳过。**主 loop 永远不调
      ws_connect / session.close / ws.close**，所以 monitor 没启动或断线
      不会阻塞 memory 等其它 await 路径。
    - 每个 ws 一个 :func:`_slot_maintainer` 后台 task：``dead_event`` 唤醒 +
      指数退避重连（0.25s → 2s 封顶）。失败时静默重试，不打扰主 loop。
    - 每条活 ws 一个 :func:`_slot_reader` 后台 task：检测服务端 close →
      ``_mark_dead`` → 唤醒 maintainer。
    - 应用层 heartbeat 已删除：aiohttp 底层 ``heartbeat=10`` 的 ping/pong
      会在 ~10s 内把死连接变成 reader 看得见的 ``CLOSED``。

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

    # ws slots：config 里关掉的端点压根不创建 maintainer，零成本。
    sync_slot: _WSSlot | None = None
    binary_slot: _WSSlot | None = None
    bullet_slot: _WSSlot | None = None
    if config['monitor']:
        sync_slot = _WSSlot(
            'sync',
            f"{sync_server_url}/sync/{lanlan_name}",
            lanlan_name,
            ws_kwargs={'heartbeat': 10},
        )
        binary_slot = _WSSlot(
            'binary',
            f"{sync_server_url}/sync_binary/{lanlan_name}",
            lanlan_name,
            ws_kwargs={'heartbeat': 10},
        )
    if config['bullet']:
        bullet_slot = _WSSlot(
            'bullet',
            f"wss://127.0.0.1:{COMMENTER_SERVER_PORT}/sync/{lanlan_name}",
            lanlan_name,
            ws_kwargs={'ssl': ssl._create_unverified_context()},
        )
    slots: list[_WSSlot] = [s for s in (sync_slot, binary_slot, bullet_slot) if s is not None]
    for s in slots:
        s.maintainer = asyncio.create_task(
            _slot_maintainer(s),
            name=f"WSMaint-{s.name}-{lanlan_name}",
        )

    user_input_cache = ''
    text_output_cache = ''  # lanlan的当前消息
    text_output_request_id = None
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

    try:
        while True:
            # 阻塞等消息。无消息时主 loop 完全沉默——ws 维护已下沉到 _slot_maintainer，
            # 不再需要周期唤醒做 reconnect/heartbeat 巡检。
            message = await message_queue.get()  # noqa: ASYNC_BLOCK — asyncio.Queue, not queue.Queue

            if message is not None:
                try:
                    if message["type"] == "json":
                        # Forward to monitor if enabled (fail-soft: monitor 不在直接跳过)
                        await _try_send_json(sync_slot, message["data"])

                        # Only treat assistant turn when it's a gemini_response
                        if message["data"].get("type") == "gemini_response":
                            if is_mirror_assistant_message(message["data"]):
                                logger.debug(
                                    "[%s] mirror assistant line skipped for ordinary memory: source=%s",
                                    lanlan_name,
                                    (message["data"].get("metadata") or {}).get("source"),
                                )
                                continue
                            if current_turn == 'user':  # assistant new message starts
                                had_user_input_this_turn = bool(user_input_cache)
                                if user_input_cache:
                                    chat_history.append({'role': 'user', 'content': [{"type": "text", "text": user_input_cache}]})
                                    user_input_cache = ''
                                current_turn = 'assistant'
                                text_output_request_id = message["data"].get("request_id")
                                current_turn_start_index = len(chat_history)
                                text_output_cache = datetime.now().strftime('[%Y%m%d %a %H:%M] ')

                                if bullet_slot is not None and bullet_slot.ws is not None:
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
                                        await _try_send_bytes(bullet_slot, binary_message)
                                    except Exception as e:
                                        logger.error(f"[{lanlan_name}] Error when sending to commenter: {e}")

                            # Append assistant streaming text
                            try:
                                text_output_cache += message["data"].get("text", "")
                            except Exception:
                                pass

                    elif message["type"] == "binary":
                        await _try_send_bytes(binary_slot, message["data"])

                    elif message["type"] == "user":  # 准备转录
                        data = message["data"].get("data")
                        input_type = message["data"].get("input_type")
                        if input_type == "transcript": # 暂时只处理语音，后续还需要记录图片
                            if user_input_cache == '':
                                await _try_send_json(sync_slot, {'type': 'user_activity'})  # 用于打断前端声音播放
                            user_input_cache += data
                            # 发送用户转录到 monitor 供副终端显示
                            if data:
                                await _try_send_json(sync_slot, {'type': 'user_transcript', 'text': data})
                        elif input_type in MIRROR_USER_INPUT_TYPES:
                            # Mirror channel user inputs (e.g. text/voice that
                            # was hijacked into an external controller) are
                            # logged for monitor display but **never** flushed
                            # into chat_history as a UserMessage — the chat
                            # LLM did not "hear" them; they belong to the
                            # external controller's transcript log.
                            if data:
                                await _try_send_json(sync_slot, {'type': 'user_transcript', 'text': data})
                            await _try_send_json(sync_slot, {'type': 'user_activity'})
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
                                text_output_request_id = None
                            
                            elif message["data"] == "response_discarded_clear":
                                logger.debug(f"[{lanlan_name}] 收到 response_discarded_clear，清空当前输出缓存")
                                text_output_cache = ''
                                text_output_request_id = None
                            
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
                                text_output_request_id = None
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
                                # 后端打标：meta 与 turn end 事件原子绑定，不再依赖独立通道
                                # 的 pending_* 状态。game-only 足球台词在这里先截断，避免
                                # 未打标的兼容镜像文本先 append 到 ordinary chat_history。
                                turn_end_meta = message.get("meta") if isinstance(message, dict) else None
                                if not isinstance(turn_end_meta, dict):
                                    turn_end_meta = None
                                was_assistant_turn = current_turn == 'assistant'
                                if is_mirror_turn_end_meta(turn_end_meta):
                                    turn_end_request_id = message.get("request_id") if isinstance(message, dict) else None
                                    if was_assistant_turn and (
                                        not turn_end_request_id or turn_end_request_id == text_output_request_id
                                    ):
                                        text_output_cache = ''
                                        text_output_request_id = None
                                    current_turn = 'user'
                                    current_turn_start_index = len(chat_history)
                                    had_user_input_this_turn = False
                                    user_input_cache = ''
                                    pending_user_images = []
                                    await _try_send_json(sync_slot, {'type': 'turn end'})
                                    logger.debug("[%s] mirror turn end skipped for ordinary memory/analyzer", lanlan_name)
                                    continue
                                current_turn = 'user'
                                text_output_cache = normalize_text(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                        {'role': 'assistant', 'content': [{'type': 'text', 'text': text_output_cache}]})
                                text_output_cache = ''
                                text_output_request_id = None
                                # kind == 'avatar_interaction' 才进入隔离路径，其它情况按
                                # proactive / normal 处理。
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
                                await _try_send_json(sync_slot, {'type': 'turn end'})
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
                                text_output_request_id = None
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

            # ws 维护已下沉到独立 _slot_maintainer task，主 loop 不再做巡检。

    except asyncio.CancelledError:
        raise
    finally:
        # cleanup 顺序：先 cancel maintainer + reader（停止新建连接 / 读循环），
        # 再 close 现存的 ws + session。maintainer 自己持有 session 引用，cancel
        # 后我们从 slot 里读出来一并关。
        for s in slots:
            if s.maintainer is not None:
                s.maintainer.cancel()
            if s.reader is not None:
                s.reader.cancel()

        bg_tasks = [t for s in slots for t in (s.maintainer, s.reader) if t is not None]
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)

        await asyncio.gather(
            *(_safe_close(s.ws) for s in slots),
            *(_safe_close(s.session) for s in slots),
            return_exceptions=True,
        )
        # 注意：不在这里调用 aclose_internal_http_client_current_loop()。
        # 旧版子进程/独立线程拥有自己的 event loop，其 http client 也是 per-loop
        # 缓存的，退出时需要 close。现在合并到主 loop，client 由主代码共享，
        # 我们没有所有权，不应当 close。
