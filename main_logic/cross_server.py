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
from config import MONITOR_SERVER_PORT, MEMORY_SERVER_PORT, COMMENTER_SERVER_PORT, TOOL_SERVER_PORT
from datetime import datetime
import json
import re
from utils.frontend_utils import contains_chinese, replace_blank, replace_corner_mark, remove_bracket, \
    is_only_punctuation, split_paragraph

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


def normalize_text(text):  # 对文本进行基本预处理
    text = text.strip()
    text = replace_blank(text)

    text = emoji_pattern2.sub('', text)
    text = emoji_pattern.sub('', text)
    text = emotion_pattern.sub("", text)
    if is_only_punctuation(text):
        return ""
    return text

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


def sync_connector_process(message_queue, shutdown_event, lanlan_name, sync_server_url=f"ws://localhost:{MONITOR_SERVER_PORT}", config=None):
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

                            if message["data"] == "renew session":
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
                                logger.info(f"[{lanlan_name}] 热重置：聊天历史长度 {len(chat_history)} 条消息")
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(
                                            f"http://localhost:{MEMORY_SERVER_PORT}/renew/{lanlan_name}",
                                            json={'input_history': json.dumps(chat_history, indent=2, ensure_ascii=False)},
                                            timeout=aiohttp.ClientTimeout(total=30.0)
                                        ) as response:
                                            result = await response.json()
                                            if result.get('status') == 'error':
                                                logger.error(f"[{lanlan_name}] 热重置记忆处理失败: {result.get('message')}")
                                            else:
                                                logger.info(f"[{lanlan_name}] 热重置记忆已成功上传到 memory_server")
                                except Exception as e:
                                    logger.exception(f"[{lanlan_name}] 调用 /renew API 失败: {type(e).__name__}: {e}")
                                chat_history.clear()

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
                                        async with aiohttp.ClientSession() as session:
                                            async with session.post(
                                                f"http://localhost:{TOOL_SERVER_PORT}/analyze_and_plan",
                                                json={'messages': recent, 'lanlan_name': lanlan_name},
                                                timeout=aiohttp.ClientTimeout(total=5.0)
                                            ) as resp:
                                                await resp.read()  # 确保响应被完全读取
                                        logger.debug(f"[{lanlan_name}] 已发送对话到analyzer进行分析")
                                except asyncio.TimeoutError:
                                    logger.warning(f"[{lanlan_name}] 发送到analyzer超时")
                                except Exception as e:
                                    logger.warning(f"[{lanlan_name}] 发送到analyzer失败: {e}")
                                
                                # Turn end时不保存聊天记录，只在session end或renew session时保存

                            elif message["data"] == 'session end': # 当前session结束了
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
                                        async with aiohttp.ClientSession() as session:
                                            async with session.post(
                                                f"http://localhost:{TOOL_SERVER_PORT}/analyze_and_plan",
                                                json={'messages': recent, 'lanlan_name': lanlan_name},
                                                timeout=aiohttp.ClientTimeout(total=5.0)
                                            ) as resp:
                                                await resp.read()  # 确保响应被完全读取
                                        logger.debug(f"[{lanlan_name}] 已发送对话到analyzer进行分析 (session end)")
                                except asyncio.TimeoutError:
                                    logger.warning(f"[{lanlan_name}] 发送到analyzer超时 (session end)")
                                except Exception as e:
                                    logger.warning(f"[{lanlan_name}] 发送到analyzer失败: {e} (session end)")
                                
                                # 处理聊天历史
                                logger.info(f"[{lanlan_name}] 会话结束：开始处理聊天历史，共 {len(chat_history)} 条消息")
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(
                                            f"http://localhost:{MEMORY_SERVER_PORT}/process/{lanlan_name}",
                                            json={'input_history': json.dumps(chat_history, indent=2, ensure_ascii=False)},
                                            timeout=aiohttp.ClientTimeout(total=30.0)
                                        ) as response:
                                            result = await response.json()
                                            if result.get('status') == 'error':
                                                logger.error(f"[{lanlan_name}] 会话记忆处理失败: {result.get('message')}")
                                            else:
                                                logger.info(f"[{lanlan_name}] 会话记忆已成功上传到 memory_server")
                                except Exception as e:
                                    logger.exception(f"[{lanlan_name}] 调用 /process API 失败")
                                chat_history.clear()
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
                            except Exception as e:
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
                            except Exception as e:
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
                                    f"wss://localhost:{COMMENTER_SERVER_PORT}/sync/{lanlan_name}",
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
