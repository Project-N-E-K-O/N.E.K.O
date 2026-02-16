"""
本模块用于将lanlan的消息转发至所有相关服务器，包括：
1. Bullet Server。对实时内容进行监听并与直播间弹幕进行交互。
2. Monitor Server。将实时内容转发至所有副终端。副终端会同步播放与主终端完全相同的内容，但不具备交互性。同一时间只有一个主终端可以交互。
3. Memory Server。对对话历史进行总结、分析，并转为持久化记忆。
注意，cross server是一个单向的转发器，不会将任何内容回传给主进程。如需回传，目前仍需要建立专门的双向连接。
"""

import ssl

import asyncio
import time
import pickle
import aiohttp
import logging
from config import MONITOR_SERVER_PORT, MEMORY_SERVER_PORT, COMMENTER_SERVER_PORT
from datetime import datetime
import json
import re
from utils.frontend_utils import replace_blank, is_only_punctuation
from main_logic.agent_event_bus import publish_analyze_request_reliably

# Setup logger for this module
logger = logging.getLogger(__name__)
emoji_pattern = re.compile(r'[^\w\u4e00-\u9fff\s>][^\w\u4e00-\u9fff\s]{2,}[^\w\u4e00-\u9fff\s<]', flags=re.UNICODE)
emoji_pattern2 = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
emotion_pattern = re.compile('<(.*?)>')


async def _publish_analyze_request_with_fallback(lanlan_name: str, trigger: str, messages: list[dict]) -> bool:
    """Publish analyze request via EventBus with ack/retry."""
    try:
        sent = await publish_analyze_request_reliably(
            lanlan_name=lanlan_name,
            trigger=trigger,
            messages=messages,
            ack_timeout_s=0.5,
            retries=1,
        )
        if sent:
            logger.info(
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


def cleanup_consecutive_assistant_messages(chat_history):
    """
    清理聊天历史中末尾连续的assistant消息。
    这是为了处理主动搭话未被响应的情况，避免纠错LLM混淆角色。
    
    逻辑：
    1. 保留最后一轮正常对话 [user, assistant]
    2. 如果之后有连续的assistant消息（主动搭话），合并为一条
    3. 在合并的消息前插入一个 user 消息说明用户沉默了
    
    例如：[user, assistant, assistant, assistant] 
       -> [user, assistant, user("(用户沉默了一轮)"), assistant(合并后的内容)]
    """
    if len(chat_history) < 3:
        return chat_history
    
    # 找到末尾连续的assistant消息数量
    consecutive_assistant_count = 0
    for i in range(len(chat_history) - 1, -1, -1):
        if chat_history[i].get('role') == 'assistant':
            consecutive_assistant_count += 1
        else:
            break
    
    # 如果末尾只有0或1条assistant消息，无需处理
    if consecutive_assistant_count <= 1:
        return chat_history
    
    # 检查在这些连续assistant之前是否有正常的对话轮（至少 user + assistant）
    # 我们需要保留第一条assistant（正常对话的回复），合并后续的（主动搭话）
    non_assistant_index = len(chat_history) - consecutive_assistant_count - 1
    
    if non_assistant_index < 0:
        # 全是assistant消息，保留最后一条并加上沉默提示
        last_assistant = chat_history[-1]
        silent_user = {'role': 'user', 'content': [{"type": "text", "text": "(沉默了一轮)"}]}
        logger.info("[cleanup] 全是AI消息，保留最后1条并添加沉默提示")
        return [silent_user, last_assistant]
    
    # 正常情况：保留 non_assistant 及之前的内容 + 第一条 assistant，然后合并剩余的
    # 结构：[...正常对话..., assistant(正常回复), assistant(主动1), assistant(主动2), ...]
    # 保留第一条assistant作为正常回复，合并后续的主动搭话
    
    if consecutive_assistant_count >= 2:
        # 找到第一条连续assistant的索引（这是正常对话的回复）
        first_assistant_index = len(chat_history) - consecutive_assistant_count
        
        # 保留到第一条assistant（包括它）
        preserved = chat_history[:first_assistant_index + 1]
        
        # 合并后续的assistant消息内容
        proactive_messages = chat_history[first_assistant_index + 1:]
        merged_content_parts = []
        for msg in proactive_messages:
            try:
                content = msg.get('content', [])
                if isinstance(content, list) and len(content) > 0:
                    text = content[0].get('text', '')
                    if text:
                        merged_content_parts.append(text)
            except Exception:
                pass
        
        if merged_content_parts:
            # 插入沉默提示和合并后的主动搭话
            silent_user = {'role': 'user', 'content': [{"type": "text", "text": "(沉默了一轮)"}]}
            merged_assistant = {'role': 'assistant', 'content': [{"type": "text", "text": "(尝试主动搭话)"+"\n".join(merged_content_parts)}]}
            preserved.append(silent_user)
            preserved.append(merged_assistant)
            logger.info(f"[cleanup] 检测到{len(proactive_messages)}条连续主动搭话，已合并并添加沉默提示")
        
        return preserved
    
    return chat_history

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


def sync_connector_process(message_queue, shutdown_event, lanlan_name, sync_server_url=f"ws://127.0.0.1:{MONITOR_SERVER_PORT}", config=None):
    """独立进程运行的同步连接器"""

    # 创建一个新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    chat_history = []
    default_config = {'bullet': True, 'monitor': True}
    if config is None:
        config = {}
    config = default_config | config

    async def maintain_connection(chat_history, lanlan_name):
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
        text_output_cache = '' # lanlan的当前消息
        current_turn = 'user'
        last_screen = None
        last_synced_index = 0  # 用于 turn end 时仅同步新增消息到 memory，避免 memory_browser 不更新

        while not shutdown_event.is_set():
            try:
                # 检查消息队列
                while not message_queue.empty():
                    message = message_queue.get()

                    if message["type"] == "json":
                        # Forward to monitor if enabled
                        if config['monitor'] and sync_ws:
                            await sync_ws.send_json(message["data"])

                        # Only treat assistant turn when it's a gemini_response
                        if message["data"].get("type") == "gemini_response":
                            if current_turn == 'user':  # assistant new message starts
                                if user_input_cache:
                                    chat_history.append({'role': 'user', 'content': [{"type": "text", "text": user_input_cache}]})
                                    user_input_cache = ''
                                current_turn = 'assistant'
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
                                
                                # 清理连续的assistant消息（主动搭话未被响应时只保留最后一条）
                                chat_history = cleanup_consecutive_assistant_messages(chat_history)
                                
                                # 再次检查关闭状态
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过memory_server请求")
                                    chat_history.clear()
                                    break
                                
                                logger.info(f"[{lanlan_name}] 热重置：聊天历史长度 {len(chat_history)} 条消息")
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(
                                            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/renew/{lanlan_name}",
                                            json={'input_history': json.dumps(chat_history, indent=2, ensure_ascii=False)},
                                            timeout=aiohttp.ClientTimeout(total=30.0)
                                        ) as response:
                                            result = await response.json()
                                            if result.get('status') == 'error':
                                                logger.error(f"[{lanlan_name}] 热重置记忆处理失败: {result.get('message')}")
                                            else:
                                                logger.info(f"[{lanlan_name}] 热重置记忆已成功上传到 memory_server")
                                except RuntimeError as e:
                                    if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                                        logger.info(f"[{lanlan_name}] 进程正在关闭，renew请求已取消")
                                    else:
                                        logger.exception(f"[{lanlan_name}] 调用 /renew API 失败: {type(e).__name__}: {e}")
                                except Exception as e:
                                    logger.exception(f"[{lanlan_name}] 调用 /renew API 失败: {type(e).__name__}: {e}")
                                chat_history.clear()
                                last_synced_index = 0

                            if message["data"] == 'turn end': # lanlan的消息结束了
                                current_turn = 'user'
                                text_output_cache = normalize_text(text_output_cache)
                                if len(text_output_cache) > 0:
                                    chat_history.append(
                                        {'role': 'assistant', 'content': [{'type': 'text', 'text': text_output_cache}]})
                                text_output_cache = ''
                                if config['monitor'] and sync_ws:
                                    await sync_ws.send_json({'type': 'turn end'})
                                # 非阻塞地向tool_server发送最近对话，供分析器识别潜在任务
                                # 检查是否正在关闭
                                if not shutdown_event.is_set():
                                    try:
                                        # 构造最近的消息摘要
                                        recent = []
                                        for item in chat_history[-6:]:
                                            if item.get('role') in ['user', 'assistant']:
                                                try:
                                                    txt = item['content'][0]['text'] if item.get('content') else ''
                                                except Exception:
                                                    txt = ''
                                                if txt == '':
                                                    continue
                                                recent.append({'role': item.get('role'), 'text': txt})
                                        if recent:
                                            sent = await _publish_analyze_request_with_fallback(
                                                lanlan_name=lanlan_name,
                                                trigger="turn_end",
                                                messages=recent,
                                            )
                                            if sent:
                                                logger.info(f"[{lanlan_name}] analyze_request dispatch success (turn_end), messages={len(recent)}")
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
                                
                                # Turn end 时同步新增消息到 Memory Server，使 memory_browser 及时更新（T1/T2 记忆验收）
                                if not shutdown_event.is_set() and last_synced_index < len(chat_history):
                                    new_messages = chat_history[last_synced_index:]
                                    try:
                                        async with aiohttp.ClientSession() as session:
                                            async with session.post(
                                                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/process/{lanlan_name}",
                                                json={'input_history': json.dumps(new_messages, indent=2, ensure_ascii=False)},
                                                timeout=aiohttp.ClientTimeout(total=30.0)
                                            ) as response:
                                                result = await response.json()
                                                if result.get('status') == 'error':
                                                    logger.debug(f"[{lanlan_name}] turn end 记忆同步失败: {result.get('message')}")
                                                else:
                                                    last_synced_index = len(chat_history)
                                                    logger.debug(f"[{lanlan_name}] turn end 已同步 {len(new_messages)} 条消息到 memory_server")
                                    except Exception as e:
                                        logger.debug(f"[{lanlan_name}] turn end 同步 memory_server 失败: {e}")

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
                                
                                # 向tool_server发送最近对话，供分析器识别潜在任务（与turn end逻辑相同）
                                # 再次检查关闭状态
                                if not shutdown_event.is_set():
                                    try:
                                        # 构造最近的消息摘要
                                        recent = []
                                        for item in chat_history[-6:]:
                                            if item.get('role') in ['user', 'assistant']:
                                                try:
                                                    txt = item['content'][0]['text'] if item.get('content') else ''
                                                except Exception:
                                                    txt = ''
                                                if txt == '':
                                                    continue
                                                recent.append({'role': item.get('role'), 'text': txt})
                                        if recent:
                                            sent = await _publish_analyze_request_with_fallback(
                                                lanlan_name=lanlan_name,
                                                trigger="session_end",
                                                messages=recent,
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
                                
                                # 清理连续的assistant消息（主动搭话未被响应时只保留最后一条）
                                chat_history = cleanup_consecutive_assistant_messages(chat_history)
                                
                                # 再次检查关闭状态
                                if shutdown_event.is_set():
                                    logger.info(f"[{lanlan_name}] 进程正在关闭，跳过 session end 收尾")
                                    chat_history.clear()
                                    last_synced_index = 0
                                    break
                                
                                # Session end 前兜底同步：若仍有未同步消息（例如 turn end 前崩溃），再发送一次增量
                                if not shutdown_event.is_set() and last_synced_index < len(chat_history):
                                    new_messages = chat_history[last_synced_index:]
                                    try:
                                        async with aiohttp.ClientSession() as session:
                                            async with session.post(
                                                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/process/{lanlan_name}",
                                                json={'input_history': json.dumps(new_messages, indent=2, ensure_ascii=False)},
                                                timeout=aiohttp.ClientTimeout(total=30.0)
                                            ) as response:
                                                result = await response.json()
                                                if result.get('status') == 'error':
                                                    logger.debug(f"[{lanlan_name}] session end 记忆同步失败: {result.get('message')}")
                                                else:
                                                    last_synced_index = len(chat_history)
                                                    logger.debug(f"[{lanlan_name}] session end 兜底同步 {len(new_messages)} 条消息到 memory_server")
                                    except Exception as e:
                                        logger.debug(f"[{lanlan_name}] session end 同步 memory_server 失败: {e}")

                                # Session end 不再向 memory_server 发送完整 chat_history，避免重复写入：
                                # 每轮已在 turn end 时把新增消息同步到 /process，memory 中已有完整对话；
                                # 若 session end 再发一次完整历史，recent_history 会 extend() 导致整段对话重复。
                                logger.info(f"[{lanlan_name}] 会话结束：聊天历史共 {len(chat_history)} 条（已按轮次在 turn end 同步，不再重复提交）")
                                chat_history.clear()
                                last_synced_index = 0
                        except Exception as e:
                            logger.error(f"[{lanlan_name}] System message error: {e}", exc_info=True)
                    await asyncio.sleep(0.02)
            except Exception as e:
                logger.error(f"[{lanlan_name}] Message processing error: {e}", exc_info=True)
                await asyncio.sleep(0.02)
            
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

                        # 发送心跳（捕获异常以检测连接断开）
                        if config['monitor'] and sync_ws:
                            try:
                                await sync_ws.send_json({"type": "heartbeat", "timestamp": time.time()})
                            except Exception:
                                sync_ws = None
                                
                        if config['monitor'] and binary_ws:
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
                
                # 短暂休眠避免CPU占用过高
                await asyncio.sleep(0.02)

            except asyncio.CancelledError:
                break
            except Exception as e:
                # WebSocket 连接异常，标记连接为失败状态
                logger.error(f"[{lanlan_name}] WebSocket连接异常: {e}")
                sync_ws = None
                binary_ws = None
                bullet_ws = None
                await asyncio.sleep(0.03)  # 重连前等待

        # 关闭资源
        for ws in [sync_ws, binary_ws, bullet_ws]:
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        for sess in [sync_session, binary_session, bullet_session]:
            if sess:
                try:
                    await sess.close()
                except Exception:
                    pass
        for rdr in [sync_reader, binary_reader, bullet_reader]:
            if rdr:
                try:
                    rdr.cancel()
                except Exception:
                    pass

    try:
        loop.run_until_complete(maintain_connection(chat_history, lanlan_name))
    except Exception as e:
        logger.error(f"[{lanlan_name}] Sync进程错误: {e}", exc_info=True)
    finally:
        loop.close()
        logger.info(f"[{lanlan_name}] Sync进程已终止")
