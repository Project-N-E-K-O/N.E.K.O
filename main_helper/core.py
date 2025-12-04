"""
本文件是主逻辑文件，负责管理整个对话流程。当选择不使用TTS时，将会通过OpenAI兼容接口使用Omni模型的原生语音输出。
当选择使用TTS时，将会通过额外的TTS API去合成语音。注意，TTS API的输出是流式输出、且需要与用户输入进行交互，实现打断逻辑。
TTS部分使用了两个队列，原本只需要一个，但是阿里的TTS API回调函数只支持同步函数，所以增加了一个response queue来异步向前端发送音频数据。
"""
import asyncio
import json
import struct  # For packing audio data
import threading
import re
import logging
import time
from datetime import datetime
from websockets import exceptions as web_exceptions
from fastapi import WebSocket, WebSocketDisconnect
from utils.frontend_utils import contains_chinese, replace_blank, replace_corner_mark, remove_bracket, \
    is_only_punctuation, split_paragraph
from utils.audio import make_wav_header
from main_helper.omni_realtime_client import OmniRealtimeClient
from main_helper.omni_offline_client import OmniOfflineClient
from main_helper.tts_helper import get_tts_worker
import base64
from io import BytesIO
from PIL import Image
from config import MEMORY_SERVER_PORT
from utils.config_manager import get_config_manager
from multiprocessing import Process, Queue as MPQueue
from uuid import uuid4
import numpy as np
import soxr
import httpx 

# Setup logger for this module
logger = logging.getLogger(__name__)



# --- 一个带有定期上下文压缩+在线热切换的语音会话管理器 ---
class LLMSessionManager:
    def __init__(self, sync_message_queue, lanlan_name, lanlan_prompt):
        self.websocket = None
        self.sync_message_queue = sync_message_queue
        self.session = None
        self.last_time = None
        self.is_active = False
        self.active_session_is_idle = False
        self.current_expression = None
        self.tts_request_queue = MPQueue() # TTS request (多进程队列)
        self.tts_response_queue = MPQueue() # TTS response (多进程队列)
        self.tts_process = None  # TTS子进程
        self.lock = asyncio.Lock()  # 使用异步锁替代同步锁
        self.websocket_lock = None  # websocket操作的共享锁，由main_server设置
        self.current_speech_id = None
        self.emoji_pattern = re.compile(r'[^\w\u4e00-\u9fff\s>][^\w\u4e00-\u9fff\s]{2,}[^\w\u4e00-\u9fff\s<]', flags=re.UNICODE)
        self.emoji_pattern2 = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
        self.emotion_pattern = re.compile('<(.*?)>')

        self.lanlan_prompt = lanlan_prompt
        self.lanlan_name = lanlan_name
        # 获取角色相关配置
        self._config_manager = get_config_manager()

        (
            self.master_name,
            self.her_name,
            self.master_basic_config,
            self.lanlan_basic_config,
            self.name_mapping,
            self.lanlan_prompt_map,
            self.semantic_store,
            self.time_store,
            self.setting_store,
            self.recent_log
        ) = self._config_manager.get_character_data()
        # 获取API相关配置（动态读取以支持热重载）
        core_config = self._config_manager.get_core_config()
        self.model = core_config['CORE_MODEL']  # For realtime voice
        self.text_model = core_config['CORRECTION_MODEL']  # For text-only mode
        self.vision_model = core_config['VISION_MODEL']  # For vision tasks
        self.core_url = core_config['CORE_URL']
        self.core_api_key = core_config['CORE_API_KEY']
        self.core_api_type = core_config['CORE_API_TYPE']
        self.openrouter_url = core_config['OPENROUTER_URL']
        self.openrouter_api_key = core_config['OPENROUTER_API_KEY']
        self.memory_server_port = MEMORY_SERVER_PORT
        self.audio_api_key = core_config['AUDIO_API_KEY']
        self.voice_id = self.lanlan_basic_config[self.lanlan_name].get('voice_id', '')
        # 注意：use_tts 会在 start_session 中根据 input_mode 重新设置
        self.use_tts = False
        self.generation_config = {}  # Qwen暂时不用
        self.message_cache_for_new_session = []
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.session_start_time = None
        self.pending_connector = None
        self.pending_session = None
        self.is_hot_swap_imminent = False
        self.tts_handler_task = None
        # 热切换相关变量
        self.background_preparation_task = None
        self.final_swap_task = None
        self.receive_task = None
        self.message_handler_task = None
        # 任务完成后的额外回复队列（将在下一次切换时统一汇报）
        self.pending_extra_replies = []
        # 由前端控制的Agent相关开关
        self.agent_flags = {
            'agent_enabled': False,
            'computer_use_enabled': False,
            'mcp_enabled': False,
        }
        
        # 模式标志: 'audio' 或 'text'
        self.input_mode = 'audio'
        
        # 初始化时创建audio模式的session（默认）
        self.session = None
        
        # 防止无限重试的保护机制
        self.session_start_failure_count = 0
        self.session_start_last_failure_time = None
        self.session_start_cooldown_seconds = 3.0  # 冷却时间：3秒
        self.session_start_max_failures = 3  # 最大连续失败次数
        
        # 防止并发启动的标志
        self.is_starting_session = False
        
        # TTS缓存机制：确保不丢包
        self.tts_ready = False  # TTS是否完全就绪
        self.tts_pending_chunks = []  # 待处理的TTS文本chunk: [(speech_id, text), ...]
        self.tts_cache_lock = asyncio.Lock()  # 保护缓存的锁
        
        # 输入数据缓存机制：确保session初始化期间的输入不丢失
        self.session_ready = False  # Session是否完全就绪
        self.pending_input_data = []  # 待处理的输入数据: [message_dict, ...]
        self.input_cache_lock = asyncio.Lock()  # 保护输入缓存的锁

    async def handle_new_message(self):
        """处理新模型输出：清空TTS队列并通知前端"""
        if self.use_tts and self.tts_process and self.tts_process.is_alive():
            # 清空响应队列中待发送的音频数据
            while not self.tts_response_queue.empty():
                try:
                    self.tts_response_queue.get_nowait()
                except:
                    break
            # 发送终止信号以清空TTS请求队列并停止当前合成
            try:
                self.tts_request_queue.put((None, None))
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS中断信号失败: {e}")
        
        # 清空待处理的TTS缓存
        async with self.tts_cache_lock:
            self.tts_pending_chunks.clear()
        
        await self.send_user_activity()

    async def handle_text_data(self, text: str, is_first_chunk: bool = False):
        """文本回调：处理文本显示和TTS（用于文本模式）"""
        # 如果是新消息的第一个chunk，清空TTS队列和缓存以打断之前的语音
        if is_first_chunk and self.use_tts:
            async with self.tts_cache_lock:
                self.tts_pending_chunks.clear()
            
            if self.tts_process and self.tts_process.is_alive():
                # 清空响应队列中待发送的音频数据
                while not self.tts_response_queue.empty():
                    try:
                        self.tts_response_queue.get_nowait()
                    except:
                        break
        
        # 文本模式下，无论是否使用TTS，都要发送文本到前端显示
        await self.send_lanlan_response(text, is_first_chunk)
        
        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts:
            async with self.tts_cache_lock:
                # 检查TTS是否就绪
                if self.tts_ready and self.tts_process and self.tts_process.is_alive():
                    # TTS已就绪，直接发送
                    try:
                        self.tts_request_queue.put((self.current_speech_id, text))
                    except Exception as e:
                        logger.warning(f"⚠️ 发送TTS请求失败: {e}")
                else:
                    # TTS未就绪，先缓存
                    self.tts_pending_chunks.append((self.current_speech_id, text))
                    if len(self.tts_pending_chunks) == 1:
                        logger.info(f"TTS未就绪，开始缓存文本chunk...")

    async def handle_response_complete(self):
        """Qwen完成回调：用于处理Core API的响应完成事件，包含TTS和热切换逻辑"""
        if self.use_tts and self.tts_process and self.tts_process.is_alive():
            print("Response complete")
            try:
                self.tts_request_queue.put((None, None))
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败: {e}")
        self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
        
        # 直接向前端发送turn end消息
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({'type': 'system', 'data': 'turn end'})
        except Exception as e:
            logger.error(f"💥 WS Send Turn End Error: {e}")

        # 如果有挂起的额外提示：触发热切换准备并安排renew，会在最终swap时统一植入提示
        try:
            if getattr(self, 'pending_extra_replies', None) and len(self.pending_extra_replies) > 0 \
               and not self.is_preparing_new_session and not self.is_hot_swap_imminent:
                await self._trigger_immediate_preparation_for_extra()
        except Exception as e:
            logger.error(f"💥 Extra reply preparation error: {e}")
        
        # 如果正在热切换过程中，跳过所有热切换逻辑
        if self.is_hot_swap_imminent:
            return
            
        if hasattr(self, 'is_preparing_new_session') and not self.is_preparing_new_session:
            if self.session_start_time and \
                        (datetime.now() - self.session_start_time).total_seconds() >= 40:
                logger.info(f"[{self.lanlan_name}] Main Listener: Uptime threshold met. Marking for new session preparation.")
                self.is_preparing_new_session = True  # Mark that we are in prep mode
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []  # Reset cache for this new cycle
                self.initial_cache_snapshot_len = 0  # Reset snapshot marker
                self.sync_message_queue.put({'type': 'system', 'data': 'renew session'}) 

        # If prep mode is active, summary time has passed, and a turn just completed in OLD session:
        # AND background task for initial warmup isn't already running
        if self.is_preparing_new_session and \
                self.summary_triggered_time and \
                (datetime.now() - self.summary_triggered_time).total_seconds() >= 10 and \
                (not self.background_preparation_task or self.background_preparation_task.done()) and \
                not (
                        self.pending_session_warmed_up_event and self.pending_session_warmed_up_event.is_set()):  # Don't restart if already warmed up
            logger.info(f"[{self.lanlan_name}] Main Listener: Conditions met to start BACKGROUND PREPARATION of pending session.")
            self.pending_session_warmed_up_event = asyncio.Event()  # Create event for this prep cycle
            self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())

        # Stage 2: Trigger FINAL SWAP if pending session is warmed up AND this old session just completed a turn
        elif self.pending_session_warmed_up_event and \
                self.pending_session_warmed_up_event.is_set() and \
                not self.is_hot_swap_imminent and \
                (not self.final_swap_task or self.final_swap_task.done()):
            logger.info(
                "Main Listener: OLD session completed a turn & PENDING session is warmed up. Triggering FINAL SWAP sequence.")
            self.is_hot_swap_imminent = True  # Prevent re-triggering

            # The main cache self.message_cache_for_new_session is now "spent" for transfer purposes
            # It will be fully cleared after a successful swap by _reset_preparation_state.
            self.pending_session_final_prime_complete_event = asyncio.Event()
            self.final_swap_task = asyncio.create_task(
                self._perform_final_swap_sequence()
            )
            # The old session listener's current turn is done.
            # The final_swap_task will now manage the actual switch.
            # This listener will be cancelled by the final_swap_task.


    async def handle_audio_data(self, audio_data: bytes):
        """Qwen音频回调：推送音频到WebSocket前端"""
        if not self.use_tts:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                # 这里假设audio_data为PCM16字节流，直接推送
                audio = np.frombuffer(audio_data, dtype=np.int16)
                audio = (soxr.resample(audio.astype(np.float32) / 32768.0, 24000, 48000, quality='HQ')*32767.).clip(-32768, 32767).astype(np.int16)

                await self.send_speech(audio.tobytes())
                # 你可以根据需要加上格式、isNewMessage等标记
                # await self.websocket.send_json({"type": "cozy_audio", "format": "blob", "isNewMessage": True})
            else:
                pass  # websocket未连接时忽略

    async def handle_input_transcript(self, transcript: str):
        """输入转录回调：同步转录文本到消息队列和缓存，并发送到前端显示"""
        # 推送到同步消息队列
        self.sync_message_queue.put({"type": "user", "data": {"input_type": "transcript", "data": transcript.strip()}})
        
        # 只在语音模式（OmniRealtimeClient）下发送到前端显示用户转录
        # 文本模式下前端会自己显示，无需后端发送，避免重复
        if isinstance(self.session, OmniRealtimeClient):
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    message = {
                        "type": "user_transcript",
                        "text": transcript.strip()
                    }
                    await self.websocket.send_json(message)
                except Exception as e:
                    logger.error(f"⚠️ 发送用户转录到前端失败: {e}")
        
        # 缓存到session cache
        if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                self.message_cache_for_new_session.append({"role": self.master_name, "text": transcript.strip()})
            elif self.message_cache_for_new_session[-1]['role'] == self.master_name:
                self.message_cache_for_new_session[-1]['text'] += transcript.strip()
        # 可选：推送用户活动
        async with self.lock:
            self.current_speech_id = str(uuid4())

    async def handle_output_transcript(self, text: str, is_first_chunk: bool = False):
        """输出转录回调：处理文本显示和TTS（用于语音模式）"""        
        # 无论是否使用TTS，都要发送文本到前端显示
        await self.send_lanlan_response(text, is_first_chunk)
        
        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts:
            async with self.tts_cache_lock:
                # 检查TTS是否就绪
                if self.tts_ready and self.tts_process and self.tts_process.is_alive():
                    # TTS已就绪，直接发送
                    try:
                        self.tts_request_queue.put((self.current_speech_id, text))
                    except Exception as e:
                        logger.warning(f"⚠️ 发送TTS请求失败: {e}")
                else:
                    # TTS未就绪，先缓存
                    self.tts_pending_chunks.append((self.current_speech_id, text))
                    if len(self.tts_pending_chunks) == 1:
                        logger.info(f"TTS未就绪，开始缓存文本chunk...")

    async def send_lanlan_response(self, text: str, is_first_chunk: bool = False):
        """Qwen输出转录回调：可用于前端显示/缓存/同步。"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                text = self.emotion_pattern.sub('', text)
                message = {
                    "type": "gemini_response",
                    "text": text,
                    "isNewMessage": is_first_chunk  # 标记是否是新消息的第一个chunk
                }
                await self.websocket.send_json(message)
                self.sync_message_queue.put({"type": "json", "data": message})
                if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
                    if not hasattr(self, 'message_cache_for_new_session'):
                        self.message_cache_for_new_session = []
                    if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role']==self.master_name:
                        self.message_cache_for_new_session.append(
                            {"role": self.lanlan_name, "text": text})
                    elif self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                        self.message_cache_for_new_session[-1]['text'] += text

        except WebSocketDisconnect:
            logger.info("Frontend disconnected.")
        except Exception as e:
            logger.error(f"💥 WS Send Lanlan Response Error: {e}")
        
    async def handle_silence_timeout(self):
        """处理语音输入静默超时：自动关闭session但保持live2d显示"""
        try:
            logger.warning(f"[{self.lanlan_name}] 检测到长时间无语音输入，自动关闭session")
            
            # 向前端发送特殊消息，告知自动闭麦但不关闭live2d
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({
                    "type": "auto_close_mic",
                    "message": f"{self.lanlan_name}检测到长时间无语音输入，已自动关闭麦克风"
                })
            
            # 关闭当前session
            await self.end_session(by_server=True)
            
        except Exception as e:
            logger.error(f"处理静默超时时出错: {e}")
    
    async def handle_connection_error(self, message=None):
        if message:
            if '欠费' in message:
                await self.send_status("💥 智谱API触发欠费bug。请考虑充值1元。")
            elif 'standing' in message:
                await self.send_status("💥 阿里API已欠费。")
            else:
                await self.send_status(message)
        logger.info("💥 Session closed by API Server.")
        await self.disconnected_by_server()

    def _reset_preparation_state(self, clear_main_cache=False, from_final_swap=False):
        """[热切换相关] Helper to reset flags and pending components related to new session prep."""
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        if self.background_preparation_task and not self.background_preparation_task.done():  # If bg prep was running
            self.background_preparation_task.cancel()
        if self.final_swap_task and not self.final_swap_task.done() and not from_final_swap:  # If final swap was running
            self.final_swap_task.cancel()
        self.background_preparation_task = None
        self.final_swap_task = None
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None

        if clear_main_cache:
            self.message_cache_for_new_session = []

    async def _cleanup_pending_session_resources(self):
        """[热切换相关] Safely cleans up ONLY PENDING connector and session if they exist AND are not the current main session."""
        # Stop any listener specifically for the pending session (if different from main listener structure)
        # The _listen_for_pending_session_response tasks are short-lived and managed by their callers.
        if self.pending_session:
            await self.pending_session.close()
        self.pending_session = None  # Managed by connector's __aexit__

    def _init_renew_status(self):
        self._reset_preparation_state(True)
        self.session_start_time = None  # 记录当前 session 开始时间
        self.pending_session = None  # Managed by connector's __aexit__
        self.is_hot_swap_imminent = False

    async def _flush_tts_pending_chunks(self):
        """将缓存的TTS文本chunk发送到TTS队列"""
        async with self.tts_cache_lock:
            if not self.tts_pending_chunks:
                return
            
            chunk_count = len(self.tts_pending_chunks)
            logger.info(f"TTS就绪，开始处理缓存的 {chunk_count} 个文本chunk...")
            
            if self.tts_process and self.tts_process.is_alive():
                for speech_id, text in self.tts_pending_chunks:
                    try:
                        self.tts_request_queue.put((speech_id, text))
                    except Exception as e:
                        logger.error(f"💥 发送缓存的TTS请求失败: {e}")
                        break
            
            # 清空缓存
            self.tts_pending_chunks.clear()
    
    async def _flush_pending_input_data(self):
        """将缓存的输入数据发送到session"""
        async with self.input_cache_lock:
            if not self.pending_input_data:
                return
            
            if self.session and self.is_active:
                for message in self.pending_input_data:
                    try:
                        # 重新调用stream_data处理缓存的数据
                        # 注意：这里直接处理，不再缓存（因为session_ready已设为True）
                        await self._process_stream_data_internal(message)
                    except Exception as e:
                        logger.error(f"💥 发送缓存的输入数据失败: {e}")
                        break
            
            # 清空缓存
            self.pending_input_data.clear()
    
    def normalize_text(self, text): # 对文本进行基本预处理
        text = text.strip()
        text = text.replace("\n", "")
        if contains_chinese(text):
            text = replace_blank(text)
            text = replace_corner_mark(text)
            text = text.replace(".", "。")
            text = text.replace(" - ", "，")
            text = remove_bracket(text)
            text = re.sub(r'[，、]+$', '。', text)
        else:
            text = remove_bracket(text)
        text = self.emoji_pattern2.sub('', text)
        text = self.emoji_pattern.sub('', text)
        if is_only_punctuation(text) and text not in ['<', '>']:
            return ""
        return text

    async def start_session(self, websocket: WebSocket, new=False, input_mode='audio'):
        # 检查是否正在启动中
        if self.is_starting_session:
            logger.warning(f"⚠️ Session正在启动中，忽略重复请求")
            return
        
        # 标记正在启动
        self.is_starting_session = True
        
        logger.info(f"启动新session: input_mode={input_mode}, new={new}")
        self.websocket = websocket
        self.input_mode = input_mode
        
        # 立即通知前端系统正在准备（静默期开始）
        await self.send_session_preparing(input_mode)
        
        # 重新读取核心配置以支持热重载
        core_config = self._config_manager.get_core_config()
        self.model = core_config['CORE_MODEL']
        self.text_model = core_config['CORRECTION_MODEL']
        self.vision_model = core_config['VISION_MODEL']
        self.core_url = core_config['CORE_URL']
        self.core_api_key = core_config['CORE_API_KEY']
        self.core_api_type = core_config['CORE_API_TYPE']
        self.openrouter_url = core_config['OPENROUTER_URL']
        self.openrouter_api_key = core_config['OPENROUTER_API_KEY']
        self.audio_api_key = core_config['AUDIO_API_KEY']
        
        # 重新读取角色配置以获取最新的voice_id（支持角色切换后的音色热更新）
        _,_,_,lanlan_basic_config_updated,_,_,_,_,_,_ = self._config_manager.get_character_data()
        old_voice_id = self.voice_id
        self.voice_id = lanlan_basic_config_updated.get(self.lanlan_name, {}).get('voice_id', '')
        if old_voice_id != self.voice_id:
            logger.info(f"🔄 voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")
        
        logger.info(f"📌 已重新加载配置: core_api={self.core_api_type}, model={self.model}, text_model={self.text_model}, vision_model={self.vision_model}, voice_id={self.voice_id}")
        
        # 重置TTS缓存状态
        async with self.tts_cache_lock:
            self.tts_ready = False
            self.tts_pending_chunks.clear()
        
        # 重置输入缓存状态
        async with self.input_cache_lock:
            self.session_ready = False
            # 注意：不清空 pending_input_data，因为可能已有数据在缓存中
        
        # 根据 input_mode 设置 use_tts
        if input_mode == 'text':
            # 文本模式总是需要 TTS（使用默认或自定义音色）
            self.use_tts = True
        elif self.voice_id:
            # 语音模式下有自定义音色时使用 TTS
            self.use_tts = True
        else:
            # 语音模式下无自定义音色，使用 realtime API 原生语音
            self.use_tts = False
        
        async with self.lock:
            if self.is_active:
                logger.warning(f"检测到活跃的旧session，正在清理...")
                # 释放锁后清理，避免死锁
        
        # 如果检测到旧 session，先清理
        if self.is_active:
            await self.end_session(by_server=True)
            # 等待一小段时间确保资源完全释放
            await asyncio.sleep(0.5)
            logger.info("旧session清理完成")
        
        # 如果当前不需要TTS但TTS进程仍在运行，关闭它
        if not self.use_tts and self.tts_process and self.tts_process.is_alive():
            logger.info("当前模式不需要TTS，关闭TTS进程")
            try:
                self.tts_request_queue.put((None, None))
                self.tts_process.terminate()
                self.tts_process.join(timeout=2.0)
                if self.tts_process.is_alive():
                    self.tts_process.kill()
            except Exception as e:
                logger.error(f"关闭TTS进程时出错: {e}")
            finally:
                self.tts_process = None

        # 定义 TTS 启动协程（如果需要）
        async def start_tts_if_needed():
            """异步启动 TTS 进程并等待就绪"""
            if not self.use_tts:
                return True
            
            # 启动TTS子进程
            if self.tts_process is None or not self.tts_process.is_alive():
                # 使用工厂函数获取合适的 TTS worker
                has_custom_voice = bool(self.voice_id)
                tts_worker = get_tts_worker(
                    core_api_type=self.core_api_type,
                    has_custom_voice=has_custom_voice
                )
                
                self.tts_request_queue = MPQueue() # TTS request (多进程队列)
                self.tts_response_queue = MPQueue() # TTS response (多进程队列)
                self.tts_process = Process(
                    target=tts_worker,
                    args=(self.tts_request_queue, self.tts_response_queue, self.audio_api_key if has_custom_voice else self.core_api_key, self.voice_id)
                )
                self.tts_process.daemon = True
                self.tts_process.start()
                
                # 等待TTS进程发送就绪信号（最多等待8秒）
                tts_type = "自定义音色(CosyVoice)" if has_custom_voice else f"{self.core_api_type}默认TTS"
                logger.info(f"🎤 TTS进程已启动，等待就绪... (使用: {tts_type})")
                
                tts_ready = False
                start_time = time.time()
                timeout = 8.0  # 最多等待8秒
                
                while time.time() - start_time < timeout:
                    try:
                        # 非阻塞检查队列
                        if not self.tts_response_queue.empty():
                            msg = self.tts_response_queue.get_nowait()
                            # 检查是否是就绪信号
                            if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "__ready__":
                                tts_ready = msg[1]
                                if tts_ready:
                                    logger.info(f"✅ TTS进程已就绪 (用时: {time.time() - start_time:.2f}秒)")
                                else:
                                    logger.error("❌ TTS进程初始化失败")
                                break
                            else:
                                # 不是就绪信号，放回队列
                                self.tts_response_queue.put(msg)
                                break
                    except:
                        pass
                    
                    # 小睡眠避免忙等
                    await asyncio.sleep(0.05)
                
                if not tts_ready:
                    if time.time() - start_time >= timeout:
                        logger.warning(f"⚠️ TTS进程就绪信号超时 ({timeout}秒)，继续执行...")
                    else:
                        logger.error("❌ TTS进程初始化失败，但继续执行...")
            
            # 确保旧的 TTS handler task 已经停止
            if self.tts_handler_task and not self.tts_handler_task.done():
                self.tts_handler_task.cancel()
                try:
                    await asyncio.wait_for(self.tts_handler_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            
            # 启动新的 TTS handler task
            self.tts_handler_task = asyncio.create_task(self.tts_response_handler())
            
            # 标记TTS为就绪状态并处理可能已缓存的chunk
            async with self.tts_cache_lock:
                self.tts_ready = True
            
            # 处理在TTS启动期间可能已经缓存的文本chunk
            await self._flush_tts_pending_chunks()
            return True

        # 定义 LLM Session 启动协程
        async def start_llm_session():
            """异步创建并连接 LLM Session"""
            # 获取初始 prompt
            initial_prompt = (f"你是一个角色扮演大师，并且精通电脑操作。请按要求扮演以下角色（{self.lanlan_name}），并在对方请求时、回答'我试试'并尝试操纵电脑。" if self._is_agent_enabled() else f"你是一个角色扮演大师。请按要求扮演以下角色（{self.lanlan_name}）。") + self.lanlan_prompt
            
            # 连接 Memory Server 获取记忆上下文
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"http://localhost:{self.memory_server_port}/new_dialog/{self.lanlan_name}")
                    initial_prompt += resp.text
            except httpx.ConnectError:
                raise ConnectionError(f"❌ 记忆服务未启动！请先启动记忆服务 (端口 {self.memory_server_port})")
            except httpx.TimeoutException:
                raise ConnectionError(f"❌ 记忆服务响应超时！请检查记忆服务是否正常运行 (端口 {self.memory_server_port})")
            except Exception as e:
                raise ConnectionError(f"❌ 记忆服务连接失败: {e} (端口 {self.memory_server_port})")
            
            logger.info(f"🤖 开始创建 LLM Session (input_mode={input_mode})")
            
            # 根据input_mode创建不同的session
            if input_mode == 'text':
                # 文本模式：使用 OmniOfflineClient with OpenAI-compatible API
                self.session = OmniOfflineClient(
                    base_url=self.openrouter_url,
                    api_key=self.openrouter_api_key,
                    model=self.text_model,
                    vision_model=self.vision_model,
                    on_text_delta=self.handle_text_data,
                    on_input_transcript=self.handle_input_transcript,
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete
                )
            else:
                # 语音模式：使用 OmniRealtimeClient
                self.session = OmniRealtimeClient(
                    base_url=self.core_url,
                    api_key=self.core_api_key,
                    model=self.model,
                    on_text_delta=self.handle_text_data,
                    on_audio_delta=self.handle_audio_data,
                    on_new_message=self.handle_new_message,
                    on_input_transcript=self.handle_input_transcript,
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete,
                    on_silence_timeout=self.handle_silence_timeout,
                    on_status_message=self.send_status,
                    api_type=self.core_api_type  # 传入API类型，用于判断是否启用静默超时
                )

            # 连接 session
            if self.session:
                await self.session.connect(initial_prompt, native_audio = not self.use_tts)
                logger.info(f"✅ LLM Session 已连接")
                print(initial_prompt)
                return True
            else:
                raise Exception("Session not initialized")
        
        # 重置状态
        if new:
            self.message_cache_for_new_session = []
            self.last_time = None
            self.is_preparing_new_session = False
            self.summary_triggered_time = None
            self.initial_cache_snapshot_len = 0
            # 清空输入缓存（新对话时不需要保留旧的输入）
            async with self.input_cache_lock:
                self.pending_input_data.clear()

        try:
            # 并行启动 TTS 和 LLM Session
            logger.info(f"🚀 并行启动 TTS 和 LLM Session...")
            start_parallel_time = time.time()
            
            tts_result, llm_result = await asyncio.gather(
                start_tts_if_needed(),
                start_llm_session(),
                return_exceptions=True
            )
            
            logger.info(f"⚡ 并行启动完成 (总用时: {time.time() - start_parallel_time:.2f}秒)")
            
            # 检查是否有错误
            if isinstance(tts_result, Exception):
                logger.error(f"TTS 启动失败: {tts_result}")
            if isinstance(llm_result, Exception):
                raise llm_result  # LLM Session 失败是致命的
            
            # 标记 session 激活
            if self.session:
                async with self.lock:
                    self.is_active = True
                    
                self.session_start_time = datetime.now()
                
                # 启动消息处理任务
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
                
                # 🔥 预热逻辑：对于语音模式，立即触发一次 skipped response 来 prefill instructions
                # 这样可以大幅减少首轮对话的延迟（让 API 提前处理并缓存 instructions 的 KV cache）
                if isinstance(self.session, OmniRealtimeClient):
                    try:
                        logger.info(f"🔥 开始预热 Session，prefill instructions...")
                        warmup_start = time.time()
                        
                        # 创建一个事件来等待预热完成
                        warmup_done_event = asyncio.Event()
                        original_callback = self.session.on_response_done
                        
                        # 临时替换回调，只用于等待预热完成
                        async def warmup_callback():
                            warmup_done_event.set()
                        
                        self.session.on_response_done = warmup_callback
                        
                        await self.session.create_response("", skipped=True)
                        
                        # 等待预热完成（最多5秒）
                        try:
                            await asyncio.wait_for(warmup_done_event.wait(), timeout=5.0)
                            warmup_time = time.time() - warmup_start
                            logger.info(f"✅ Session预热完成 (耗时: {warmup_time:.2f}秒)，首轮对话延迟已优化")
                        except asyncio.TimeoutError:
                            logger.warning(f"⚠️ Session预热超时（5秒），继续执行...")
                        
                        # 恢复原始回调
                        self.session.on_response_done = original_callback
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Session预热失败（不影响正常使用）: {e}")
                
                # 启动成功，重置失败计数器
                self.session_start_failure_count = 0
                self.session_start_last_failure_time = None
                
                # 通知前端 session 已成功启动
                await self.send_session_started(input_mode)
                
                # 标记session为就绪状态并处理可能已缓存的输入数据
                async with self.input_cache_lock:
                    self.session_ready = True
                
                # 处理在session启动期间可能已经缓存的输入数据
                await self._flush_pending_input_data()
            else:
                raise Exception("Session not initialized")
        
        except Exception as e:
            # 记录失败
            self.session_start_failure_count += 1
            self.session_start_last_failure_time = datetime.now()
            
            error_str = str(e)
            
            # 🔴 优先检查 Memory Server 错误（最常见的启动问题）
            is_memory_server_error = isinstance(e, ConnectionError) and "Memory Server" in error_str
            
            if is_memory_server_error:
                # Memory Server 错误使用专门的日志格式
                logger.error(f"🧠 {error_str}")
                await self.send_status(f"🧠 记忆服务器未启动！请先运行 memory_server.py")
                # Memory Server 错误不计入失败次数（因为这是配置问题而非网络问题）
                self.session_start_failure_count -= 1
            else:
                error_message = f"Error starting session: {e}"
                logger.exception(f"💥 {error_message} (失败次数: {self.session_start_failure_count})")
                
                # 如果达到最大失败次数，发送严重警告并通知前端
                if self.session_start_failure_count >= self.session_start_max_failures:
                    critical_message = f"⛔ Session启动连续失败{self.session_start_failure_count}次，已停止自动重试。请检查网络连接和API配置，然后刷新页面重试。"
                    logger.critical(critical_message)
                    await self.send_status(critical_message)
                else:
                    await self.send_status(f"{error_message} (失败{self.session_start_failure_count}次)")
                
                # 检查其他类型的连接错误
                if 'WinError 10061' in error_str or 'WinError 10054' in error_str:
                    # 检查端口号是否为memory_server端口
                    if str(self.memory_server_port) in error_str or '48912' in error_str:
                        await self.send_status(f"🧠 记忆服务器(端口{self.memory_server_port})已崩溃。请重启 memory_server.py")
                    else:
                        await self.send_status("💥 服务器连接被拒绝。请检查API Key和网络连接。")
                elif '401' in error_str:
                    await self.send_status("💥 API Key被服务器拒绝。请检查API Key是否与所选模型匹配。")
                elif '429' in error_str:
                    await self.send_status("💥 API请求频率过高，请稍后再试。")
                elif 'All connection attempts failed' in error_str:
                    await self.send_status("💥 LLM API 连接失败。请检查网络连接和API配置。")
                else:
                    await self.send_status(f"💥 连接异常关闭: {error_str}")
            
            await self.cleanup()
        
        finally:
            # 无论成功还是失败，都重置启动标志
            self.is_starting_session = False

    async def send_user_activity(self):
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                message = {
                    "type": "user_activity"
                }
                await self.websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send User Activity Error: {e}")

    def _convert_cache_to_str(self, cache):
        """[热切换相关] 将cache转换为字符串"""
        res = ""
        for i in cache:
            res += f"{i['role']} | {i['text']}\n"
        return res

    def _is_agent_enabled(self):
        return self.agent_flags['agent_enabled'] and (self.agent_flags['computer_use_enabled'] or self.agent_flags['mcp_enabled'])

    async def _background_prepare_pending_session(self):
        """[热切换相关] 后台预热pending session"""

        # 2. Create PENDING session components (as before, store in self.pending_connector, self.pending_session)
        try:
            # 重新读取核心配置以支持热重载
            core_config = self._config_manager.get_core_config()
            self.model = core_config['CORE_MODEL']
            self.text_model = core_config['CORRECTION_MODEL']
            self.vision_model = core_config['VISION_MODEL']
            self.core_url = core_config['CORE_URL']
            self.core_api_key = core_config['CORE_API_KEY']
            self.core_api_type = core_config['CORE_API_TYPE']
            self.openrouter_url = core_config['OPENROUTER_URL']
            self.openrouter_api_key = core_config['OPENROUTER_API_KEY']
            self.audio_api_key = core_config['AUDIO_API_KEY']
            
            # 重新读取角色配置以获取最新的voice_id（支持角色切换后的音色热更新）
            _,_,_,lanlan_basic_config_updated,_,_,_,_,_,_ = self._config_manager.get_character_data()
            old_voice_id = self.voice_id
            self.voice_id = lanlan_basic_config_updated.get(self.lanlan_name, {}).get('voice_id', '')
            if old_voice_id != self.voice_id:
                logger.info(f"🔄 热切换准备: voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")
            
            logger.info(f"🔄 热切换准备: 已重新加载配置, voice_id={self.voice_id}")
            
            # 创建新的pending session
            self.pending_session = OmniRealtimeClient(
                base_url=self.core_url,
                api_key=self.core_api_key,
                model=self.model,
                on_text_delta=self.handle_text_data,
                on_audio_delta=self.handle_audio_data,
                on_new_message=self.handle_new_message,
                on_input_transcript=self.handle_input_transcript,
                on_output_transcript=self.handle_output_transcript,
                on_connection_error=self.handle_connection_error,
                on_response_done=self.handle_response_complete,
                on_status_message=self.send_status,
                api_type=self.core_api_type  # 传入API类型，用于判断是否启用静默超时
            )
            
            initial_prompt = (f"你是一个角色扮演大师，并且精通电脑操作。请按要求扮演以下角色（{self.lanlan_name}），在对方请求时、回答“我试试”并尝试操纵电脑。" if self._is_agent_enabled() else f"你是一个角色扮演大师。请按要求扮演以下角色（{self.lanlan_name}）。") + self.lanlan_prompt
            self.initial_cache_snapshot_len = len(self.message_cache_for_new_session)
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{self.memory_server_port}/new_dialog/{self.lanlan_name}")
                initial_prompt += resp.text + self._convert_cache_to_str(self.message_cache_for_new_session)
            # print(initial_prompt)
            await self.pending_session.connect(initial_prompt, native_audio = not self.use_tts)

            # 4. Start temporary listener for PENDING session's *first* ignored response
            #    and wait for it to complete.
            if self.pending_session_warmed_up_event:
                self.pending_session_warmed_up_event.set() 

        except asyncio.CancelledError:
            logger.error("💥 BG Prep Stage 1: Task cancelled.")
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event here if cancelled.
        except Exception as e:
            logger.error(f"💥 BG Prep Stage 1: Error: {e}")
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event on error.
        finally:
            # Ensure this task variable is cleared so it's known to be done
            if self.background_preparation_task and self.background_preparation_task.done():
                self.background_preparation_task = None

    async def _trigger_immediate_preparation_for_extra(self):
        """当需要注入额外提示时，如果当前未进入准备流程，立即开始准备并安排renew逻辑。"""
        try:
            if not self.is_preparing_new_session:
                logger.info("Extra Reply: Triggering preparation due to pending extra reply.")
                self.is_preparing_new_session = True
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []
                self.initial_cache_snapshot_len = 0
                # 立即启动后台预热，不等待10秒
                self.pending_session_warmed_up_event = asyncio.Event()
                if not self.background_preparation_task or self.background_preparation_task.done():
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())
        except Exception as e:
            logger.error(f"💥 Extra Reply: preparation trigger error: {e}")

    # 供主服务调用，更新Agent模式相关开关
    def update_agent_flags(self, flags: dict):
        try:
            for k in ['agent_enabled', 'computer_use_enabled', 'mcp_enabled']:
                if k in flags and isinstance(flags[k], bool):
                    self.agent_flags[k] = flags[k]
        except Exception:
            pass

    async def _perform_final_swap_sequence(self):
        """[热切换相关] 执行最终的swap序列"""
        logger.info("Final Swap Sequence: Starting...")
        if not self.pending_session:
            logger.error("💥 Final Swap Sequence: Pending session not found. Aborting swap.")
            self._reset_preparation_state(clear_main_cache=False)  # Reset flags, keep cache for next attempt
            self.is_hot_swap_imminent = False
            return

        try:
            incremental_cache = self.message_cache_for_new_session[self.initial_cache_snapshot_len:]
            # 1. Send incremental cache (or a heartbeat) to PENDING session for its *second* ignored response
            if incremental_cache:
                final_prime_text = f"SYSTEM_MESSAGE | " + self._convert_cache_to_str(incremental_cache)
            else:  # Ensure session cycles a turn even if no incremental cache
                logger.error(f"💥 Unexpected: No incremental cache found. {len(self.message_cache_for_new_session)}, {self.initial_cache_snapshot_len}")
                final_prime_text = f"SYSTEM_MESSAGE | 系统自动报时，当前时间： " + str(datetime.now().strftime("%Y-%m-%d %H:%M"))

            # 若存在需要植入的额外提示，则指示模型忽略上一条消息，并在下一次响应中统一向用户补充这些提示
            if self.pending_extra_replies and len(self.pending_extra_replies) > 0:
                try:
                    items = "\n".join([f"- {txt}" for txt in self.pending_extra_replies if isinstance(txt, str) and txt.strip()])
                except Exception:
                    items = ""
                final_prime_text += (
                    "\n[注入指令] 请忽略上一次用户的最后一条输入，不要继续该轮对话。"
                    " 在你的下一次响应中，用简洁自然的一段话汇报和解释你先前执行的任务的结果，简要说明你做了什么：\n"
                    + items +
                    "\n完成上述汇报后，恢复正常的对话节奏。"
                )
                # 清空队列，避免重复注入
                self.pending_extra_replies.clear()
                try:
                    await self.pending_session.create_response(final_prime_text, skipped=False)
                except web_exceptions.ConnectionClosed as e:
                    logger.warning(f"⚠️ Final Swap Sequence: pending_session连接已关闭，跳过create_response: {e}")
            else:
                final_prime_text += f"=======以上为前情概要。现在请{self.lanlan_name}准备，即将开始用语音与{self.master_name}继续对话。\n"
                try:
                    await self.pending_session.create_response(final_prime_text, skipped=True)
                except web_exceptions.ConnectionClosed as e:
                    logger.warning(f"⚠️ Final Swap Sequence: pending_session连接已关闭，跳过create_response: {e}")

            # 2. Start temporary listener for PENDING session's *second* ignored response
            if self.pending_session_final_prime_complete_event:
                self.pending_session_final_prime_complete_event.is_set()

            # --- PERFORM ACTUAL HOT SWAP ---
            logger.info("Final Swap Sequence: Starting actual session swap...")
            old_main_session = self.session
            old_main_message_handler_task = self.message_handler_task
            
            # 先停止旧session的消息处理任务
            if old_main_message_handler_task and not old_main_message_handler_task.done():
                logger.info("Final Swap Sequence: Cancelling old message handler task...")
                old_main_message_handler_task.cancel()
                try:
                    await asyncio.wait_for(old_main_message_handler_task, timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("Final Swap Sequence: Warning: Old message handler task cancellation timeout.")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error cancelling old message handler: {e}")
            
            # 执行session切换
            logger.info("Final Swap Sequence: Swapping sessions...")
            self.session = self.pending_session
            self.session_start_time = datetime.now()

            # Start the main listener for the NEWLY PROMOTED self.session
            if self.session and hasattr(self.session, 'handle_messages'):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

            # 关闭旧session
            if old_main_session:
                logger.info("Final Swap Sequence: Closing old session...")
                try:
                    await old_main_session.close()
                    logger.info("Final Swap Sequence: Old session closed successfully.")
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error closing old session: {e}")

        
            # Reset all preparation states and clear the *main* cache now that it's fully transferred
            self.pending_session = None
            self._reset_preparation_state(
                clear_main_cache=True, from_final_swap=True)  # This will clear pending_*, is_preparing_new_session, etc. and self.message_cache_for_new_session
            logger.info("Final Swap Sequence: Hot swap completed successfully.")

        except asyncio.CancelledError:
            logger.info("Final Swap Sequence: Task cancelled.")
            # If cancelled mid-swap, state could be inconsistent. Prioritize cleaning pending.
            await self._cleanup_pending_session_resources()
            self._reset_preparation_state(clear_main_cache=False)  # Don't clear cache if swap didn't complete
            # The old main session listener might have been cancelled, needs robust restart if still active
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                logger.info(
                    "Final Swap Sequence: Task cancelled, ensuring main listener is running for potentially old session.")
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

        except Exception as e:
            logger.error(f"💥 Final Swap Sequence: Error: {e}")
            await self.send_status(f"内部更新切换失败: {e}.")
            await self._cleanup_pending_session_resources()
            self._reset_preparation_state(clear_main_cache=False)
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
        finally:
            self.is_hot_swap_imminent = False  # Always reset this flag
            if self.final_swap_task and self.final_swap_task.done():
                self.final_swap_task = None
            logger.info("Final Swap Sequence: Routine finished.")

    async def disconnected_by_server(self):
        await self.send_status(f"{self.lanlan_name}失联了，即将重启！")
        self.sync_message_queue.put({'type': 'system', 'data': 'API server disconnected'})
        await self.cleanup()

    async def stream_data(self, message: dict):  # 向Core API发送Media数据
        data = message.get("data")
        input_type = message.get("input_type")
        
        # 检查session是否就绪
        async with self.input_cache_lock:
            if not self.session_ready:
                # Session未就绪，缓存输入数据
                self.pending_input_data.append(message)
                if len(self.pending_input_data) == 1:
                    logger.info(f"Session未就绪，开始缓存输入数据...")
                else:
                    logger.debug(f"继续缓存输入数据 (总计: {len(self.pending_input_data)} 条)...")
                return
        
        # Session已就绪，直接处理
        await self._process_stream_data_internal(message)
    
    async def _process_stream_data_internal(self, message: dict):
        """内部方法：实际处理stream_data的逻辑"""
        data = message.get("data")
        input_type = message.get("input_type")
        
        # 如果正在启动session，这不应该发生（因为stream_data已经检查过了）
        if self.is_starting_session:
            logger.debug(f"Session正在启动中，跳过...")
            return
        
        # 如果 session 不存在或不活跃，检查是否可以自动重建
        if not self.session or not self.is_active:
            # 检查失败计数器和冷却时间
            if self.session_start_failure_count >= self.session_start_max_failures:
                # 达到最大失败次数，检查是否已过冷却期
                if self.session_start_last_failure_time:
                    time_since_last_failure = (datetime.now() - self.session_start_last_failure_time).total_seconds()
                    if time_since_last_failure < self.session_start_cooldown_seconds:
                        # 仍在冷却期内，不重试
                        logger.warning(f"Session启动失败过多，冷却中... (剩余 {self.session_start_cooldown_seconds - time_since_last_failure:.1f}秒)")
                        return
                    else:
                        self.session_start_failure_count = 0
                        self.session_start_last_failure_time = None
            
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
                    logger.warning(f"  └─ WebSocket状态: exists=True, 但没有client_state属性!")
            else:
                logger.error(f"  └─ WebSocket状态: exists=False! 连接可能已断开，请刷新页面")
                # 通过sync_message_queue发送错误提示
                self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                return
            
            # 根据输入类型确定模式
            mode = 'text' if input_type == 'text' else 'audio'
            await self.start_session(self.websocket, new=False, input_mode=mode)
            
            # 检查启动是否成功
            if not self.session or not self.is_active:
                logger.warning(f"⚠️ Session启动失败，放弃本次数据流")
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
                    # 先关闭旧 session
                    if self.session:
                        await self.end_session()
                    # 再创建新的文本模式 session
                    await self.start_session(self.websocket, new=False, input_mode='text')
                    
                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniOfflineClient):
                        logger.error("💥 文本模式Session重建失败，放弃本次数据流")
                        return
                
                # 文本模式：直接发送文本
                if isinstance(data, str):
                    # 为每次文本输入生成新的speech_id（用于TTS和lipsync）
                    async with self.lock:
                        self.current_speech_id = str(uuid4())

                    await self.send_user_activity()
                    await self.session.stream_text(data)
                else:
                    logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            
            # Audio输入：只有OmniRealtimeClient能处理
            if input_type == 'audio':
                # 检查 session 类型
                if not isinstance(self.session, OmniRealtimeClient):
                    # 检查是否允许重建session
                    if self.session_start_failure_count >= self.session_start_max_failures:
                        logger.error("💥 Session类型不匹配，但失败次数过多，已停止自动重建")
                        return
                    
                    logger.info(f"语音模式需要 OmniRealtimeClient，但当前是 {type(self.session).__name__}. 自动重建 session。")
                    # 先关闭旧 session
                    if self.session:
                        await self.end_session()
                    # 再创建新的语音模式 session
                    await self.start_session(self.websocket, new=False, input_mode='audio')
                    
                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniRealtimeClient):
                        logger.error("💥 语音模式Session重建失败，放弃本次数据流")
                        return
                
                # 检查WebSocket连接
                if not hasattr(self.session, 'ws') or not self.session.ws:
                    logger.error("💥 Stream: Session websocket not available")
                    return
                try:
                    if isinstance(data, list):
                        audio_bytes = struct.pack(f'<{len(data)}h', *data)
                        await self.session.stream_audio(audio_bytes)
                    else:
                        logger.error(f"💥 Stream: Invalid audio data type: {type(data)}")
                        return

                except struct.error as se:
                    logger.error(f"💥 Stream: Struct packing error (audio): {se}")
                    return
                except web_exceptions.ConnectionClosedOK:
                    return
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing audio data: {e}")
                    return

            elif input_type in ['screen', 'camera']:
                try:
                    if isinstance(data, str) and data.startswith('data:image/jpeg;base64,'):
                        img_data = data.split(',')[1]
                        img_bytes = base64.b64decode(img_data)
                        # Resize to 480p (height=480, keep aspect ratio)
                        image = Image.open(BytesIO(img_bytes))
                        w, h = image.size
                        new_h = 480
                        new_w = int(w * (new_h / h))
                        image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        buffer = BytesIO()
                        image.save(buffer, format='JPEG')
                        buffer.seek(0)
                        resized_bytes = buffer.read()
                        resized_b64 = base64.b64encode(resized_bytes).decode('utf-8')
                        
                        # 如果是文本模式（OmniOfflineClient），只存储图片，不立即发送
                        if isinstance(self.session, OmniOfflineClient):
                            # 只添加到待发送队列，等待与文本一起发送
                            await self.session.stream_image(resized_b64)
                        
                        # 如果是语音模式（OmniRealtimeClient），检查是否支持视觉并直接发送
                        elif isinstance(self.session, OmniRealtimeClient):
                            # 检查WebSocket连接
                            if not hasattr(self.session, 'ws') or not self.session.ws:
                                logger.error("💥 Stream: Session websocket not available")
                                return
                            
                            # 语音模式直接发送图片
                            await self.session.stream_image(resized_b64)
                    else:
                        logger.error(f"💥 Stream: Invalid screen data format.")
                        return
                except ValueError as ve:
                    logger.error(f"💥 Stream: Base64 decoding error (screen): {ve}")
                    return
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing screen data: {e}")
                    return

        except web_exceptions.ConnectionClosedError as e:
            logger.error(f"💥 Stream: Error sending data to session: {e}")
            if '1011' in str(e):
                print(f"💥 备注：检测到1011错误。该错误表示API服务器异常。请首先检查自己的麦克风是否有声音。")
            if '1007' in str(e):
                print(f"💥 备注：检测到1007错误。该错误大概率是欠费导致。")
            await self.disconnected_by_server()
            return
        except Exception as e:
            error_message = f"Stream: Error sending data to session: {e}"
            logger.error(f"💥 {error_message}")
            await self.send_status(error_message)

    async def end_session(self, by_server=False):  # 与Core API断开连接
        self._init_renew_status()

        async with self.lock:
            if not self.is_active:
                return

        logger.info("End Session: Starting cleanup...")
        self.sync_message_queue.put({'type': 'system', 'data': 'session end'})
        async with self.lock:
            self.is_active = False

        if self.message_handler_task:
            self.message_handler_task.cancel()
            try:
                await asyncio.wait_for(self.message_handler_task, timeout=3.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning("End Session: Warning: Listener task cancellation timeout.")
            except Exception as e:
                logger.error(f"💥 End Session: Error during listener task cancellation: {e}")
            self.message_handler_task = None

        if self.session:
            try:
                logger.info("End Session: Closing connection...")
                await self.session.close()
                logger.info("End Session: Qwen connection closed.")
            except Exception as e:
                logger.error(f"💥 End Session: Error during cleanup: {e}")
            finally:
                # 清空 session 引用，防止后续使用错误的 session 类型
                self.session = None
        # 关闭TTS子进程和相关任务
        if self.tts_handler_task and not self.tts_handler_task.done():
            self.tts_handler_task.cancel()
            try:
                await asyncio.wait_for(self.tts_handler_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self.tts_handler_task = None
            
        if self.tts_process and self.tts_process.is_alive():
            try:
                self.tts_request_queue.put((None, None))  # 通知子进程退出
                self.tts_process.terminate()
                self.tts_process.join(timeout=2.0)
                if self.tts_process.is_alive():
                    self.tts_process.kill()  # 强制杀死进程
            except Exception as e:
                logger.error(f"💥 关闭TTS进程时出错: {e}")
            finally:
                self.tts_process = None
                
        # 清理TTS队列和缓存状态
        try:
            while not self.tts_request_queue.empty():
                self.tts_request_queue.get_nowait()
        except:
            pass
        try:
            while not self.tts_response_queue.empty():
                self.tts_response_queue.get_nowait()
        except:
            pass
        
        # 重置TTS缓存状态
        async with self.tts_cache_lock:
            self.tts_ready = False
            self.tts_pending_chunks.clear()
        
        # 重置输入缓存状态
        async with self.input_cache_lock:
            self.session_ready = False
            self.pending_input_data.clear()

        self.last_time = None
        await self.send_expressions()
        if not by_server:
            await self.send_status(f"{self.lanlan_name}已离开。")
            logger.info("End Session: Resources cleaned up.")

    async def cleanup(self):
        await self.end_session(by_server=True)
        # 清理websocket引用，防止保留失效的连接
        # 使用共享锁保护websocket操作，防止与initialize_character_data()中的restore竞争
        if self.websocket_lock:
            async with self.websocket_lock:
                self.websocket = None
        else:
            # 如果没有设置websocket_lock（旧代码路径），直接清理
            self.websocket = None

    async def send_status(self, message: str): # 向前端发送status message
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "status", "message": message})
                await self.websocket.send_text(data)

                # 同步到同步服务器
                self.sync_message_queue.put({'type': 'json', 'data': {"type": "status", "message": message}})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Status Error: {e}")
    
    async def send_session_preparing(self, input_mode: str): # 通知前端session正在准备（静默期）
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_preparing", "input_mode": input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Preparing Error: {e}")
    
    async def send_session_started(self, input_mode: str): # 通知前端session已启动
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_started", "input_mode": input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Started Error: {e}")

    async def send_expressions(self, prompt=""):
        '''这个函数在直播版本中有用，用于控制Live2D模型的表情动作。但是在开源版本目前没有实际用途。'''
        try:
            expression_map = {}
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                if prompt in expression_map:
                    if self.current_expression:
                        await self.websocket.send_json({
                            "type": "expression",
                            "message": '-',
                        })
                    await self.websocket.send_json({
                        "type": "expression",
                        "message": expression_map[prompt] + '+',
                    })
                    self.current_expression = expression_map[prompt]
                else:
                    if self.current_expression:
                        await self.websocket.send_json({
                            "type": "expression",
                            "message": '-',
                        })

                if prompt in expression_map:
                    self.sync_message_queue.put({"type": "json",
                                                 "data": {
                        "type": "expression",
                        "message": expression_map[prompt] + '+',
                    }})
                else:
                    if self.current_expression:
                        self.sync_message_queue.put({"type": "json",
                         "data": {
                             "type": "expression",
                             "message": '-',
                         }})
                        self.current_expression = None

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")


    async def send_speech(self, tts_audio):
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_bytes(tts_audio)

                # 同步到同步服务器
                self.sync_message_queue.put({"type": "binary", "data": tts_audio})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")

    async def tts_response_handler(self):
        while True:
            while not self.tts_response_queue.empty():
                data = self.tts_response_queue.get_nowait()
                # 过滤掉就绪信号（格式为 ("__ready__", True/False)）
                if isinstance(data, tuple) and len(data) == 2 and data[0] == "__ready__":
                    # 这是就绪信号，不是音频数据，跳过
                    continue
                await self.send_speech(data)
            await asyncio.sleep(0.01)

