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

"""GPT-SoVITS (local) TTS worker + custom voice fetch."""

import numpy as np
import soxr
import json
import websockets
import aiohttp
import asyncio

from config import GSV_VOICE_PREFIX
from utils.config_manager import _as_bool, get_config_manager
from utils.gptsovits_config import gsv_ws_url_from_http_base, is_valid_http_url, normalize_gsv_api_url, redact_url_for_log

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

class CustomTTSVoiceFetchError(Exception):
    """Raised when custom TTS voice list cannot be fetched from provider."""

async def get_custom_tts_voices(base_url: str, provider: str = 'gptsovits'):
    """Fetch available custom TTS voices via provider adapter.

    Args:
        base_url: provider API base URL
        provider: provider key (currently supports 'gptsovits')

    Returns:
        list[dict]: normalized voices with fields: voice_id/raw_id/name/description/version
    """
    if provider != 'gptsovits':
        raise CustomTTSVoiceFetchError(f"Unsupported custom TTS provider: {provider}")

    base_url = (base_url or "").strip().rstrip("/")
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_url}/api/v3/voices") as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise CustomTTSVoiceFetchError(f"HTTP {resp.status}: {text[:200]}")
                voices_data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise CustomTTSVoiceFetchError(str(e)) from e

    voices = []
    if not isinstance(voices_data, list):
        logger.warning(f"GPT-SoVITS /api/v3/voices 返回了非列表格式: {type(voices_data).__name__}")
        return voices

    for idx, v in enumerate(voices_data):
        if not isinstance(v, dict):
            logger.warning(
                "GPT-SoVITS /api/v3/voices 第 %d 项不是对象，已跳过: %s",
                idx,
                type(v).__name__,
            )
            continue
        raw_id = v.get('id', '')
        if not raw_id:
            continue
        voices.append({
            'voice_id': f"{GSV_VOICE_PREFIX}{raw_id}",
            'raw_id': raw_id,
            'name': v.get('name', raw_id),
            'description': v.get('description', ''),
            'version': v.get('version', ''),
        })

    return voices

# GSV worker 的"句标点"白名单。判据：
# - 含字母 / 数字（Unicode 字母含 CJK / 假名 / 韩文 / 希腊 / 西里尔 /
#   阿拉伯 letter）→ 真内容，放行
# - 无字母数字但全是白名单标点（不限个数）→ LLM 真实标点 chunk（`，` `。`
#   `？` 等，可能也含罕见的 `。。。` `？！`），放行让 server TextBuffer 切句
# - 无字母数字且含任何非白名单符号 → kaomoji（`=。=` `(╯°□°）╯` `^_^`），丢
#
# 标点集刻意覆盖多语言（CJK / ASCII / Arabic / Spanish），但**不放** `(` `)`
# `[` `]` `{` `}` `「」` `《》` `"` `'` `~` `^` `*` `_` `-` 等 kaomoji 高发字符。
_GSV_ALLOWED_PUNCT = frozenset('。！？；：，、…—．.!?;:,¿¡،؟؛')

def _gsv_should_drop_chunk(text: str) -> bool:
    """True = the chunk is a pile of kaomoji / odd symbols, drop it; False = let it through.

    Judge only after scanning all characters: any alnum lets the chunk through
    immediately (kaomoji containing letters such as `T_T` / `\\(^o^)/` pass via this
    route — after the server cleans them letters remain, so no empty error is
    triggered); with no alnum, only check for non-whitelisted symbols.
    """
    has_unsanctioned = False
    for c in text:
        if c.isspace():
            continue
        if c.isalnum():
            return False
        if c not in _GSV_ALLOWED_PUNCT:
            has_unsanctioned = True
    return has_unsanctioned

def gptsovits_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """GPT-SoVITS TTS worker - uses the v3 WebSocket stream-input duplex mode
    
    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key (unused, kept for interface consistency)
        voice_id: v3 voice config ID, formatted as "voice_id" or "voice_id|advanced-params JSON"
                  e.g.: "my_voice" or "my_voice|{\"speed\":1.2,\"text_lang\":\"all_zh\"}"
    
    Config (set via TTS_MODEL_URL):
        base_url: GPT-SoVITS API address, e.g. "http://127.0.0.1:9881"
                  automatically converted to the ws:// protocol for the WebSocket connection
    """
    _ = audio_api_key  # 未使用，但保持接口一致

    # 获取配置
    cm = get_config_manager()
    tts_config = cm.get_model_api_config('tts_custom')
    base_url = normalize_gsv_api_url(tts_config.get('base_url'))

    if not is_valid_http_url(base_url):
        message = "GPT-SoVITS URL 配置无效：需要 http(s):// 的有效地址（本地或远程均可）"
        logger.error("[GPT-SoVITS v3] %s，当前: %s", message, redact_url_for_log(base_url))
        _enqueue_error(response_queue, {
            "code": "TTS_CONFIG_INVALID",
            "provider": "gptsovits",
            "message": message,
        })
        response_queue.put(("__ready__", False))
        return

    WS_URL = gsv_ws_url_from_http_base(base_url)
    logger.info(
        "[GPT-SoVITS v3] 使用服务: base=%s ws=%s",
        redact_url_for_log(base_url),
        redact_url_for_log(WS_URL),
    )

    # 剥离 gsv: 前缀（角色系统用于标识 GPT-SoVITS voice_id 的路由前缀）
    # 解析 voice_id：支持 "voice_id" 或 "voice_id|{JSON高级参数}" 格式
    extra_params = {}
    raw_voice = voice_id.strip() if voice_id else ""
    if raw_voice.startswith(GSV_VOICE_PREFIX):
        raw_voice = raw_voice[len(GSV_VOICE_PREFIX):].strip()
    if '|' in raw_voice:
        parts = raw_voice.split('|', 1)
        v3_voice_id = parts[0].strip() or "_default"
        try:
            extra_params = json.loads(parts[1])
            if not isinstance(extra_params, dict):
                logger.warning(f"[GPT-SoVITS v3] 高级参数不是对象，已忽略: {type(extra_params).__name__}")
                extra_params = {}
        except (json.JSONDecodeError, IndexError, TypeError, ValueError) as e:
            logger.warning(f"[GPT-SoVITS v3] voice_id 高级参数解析失败，忽略: {e}")
            extra_params = {}
    else:
        v3_voice_id = raw_voice or "_default"

    # 预加载 websockets State（兼容不同版本）
    try:
        from websockets.connection import State as _WsState
    except (ImportError, AttributeError):
        _WsState = None

    def _ws_is_open(ws_conn):
        """Check whether the WS connection is still open (compatible with websockets v14+/v16)"""
        if ws_conn is None:
            return False
        if _WsState is not None:
            return getattr(ws_conn, 'state', None) is _WsState.OPEN
        # fallback: 旧版 websockets
        return not getattr(ws_conn, 'closed', True)

    def _extract_pcm_from_wav(wav_bytes: bytes) -> tuple:
        """Extract PCM data and the sample rate from a WAV chunk"""
        if len(wav_bytes) < 44:
            return None, 0
        src_rate = int.from_bytes(wav_bytes[24:28], 'little')
        pcm_data = wav_bytes[44:]
        if len(pcm_data) < 2:
            return None, 0
        # 确保偶数长度
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]
        return pcm_data, src_rate

    async def async_worker():
        """Async TTS worker main loop - WebSocket duplex mode"""
        ws = None
        receive_task = None
        current_speech_id = None
        resampler = None

        async def receive_loop(ws_conn, source_speech_id):
            """Independent receive coroutine: handles audio chunks and JSON messages returned by the WS"""
            nonlocal resampler
            try:
                async for message in ws_conn:
                    if isinstance(message, bytes):
                        # 每个 binary frame 是完整 WAV chunk（含 header）
                        pcm_data, src_rate = _extract_pcm_from_wav(message)
                        if pcm_data is not None and len(pcm_data) > 0:
                            audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                            if src_rate != 48000:
                                if resampler is None:
                                    resampler = soxr.ResampleStream(src_rate, 48000, 1, dtype='float32')
                                resampled_bytes = _resample_audio(audio_array, src_rate, 48000, resampler)
                            else:
                                resampled_bytes = audio_array.tobytes()
                            if source_speech_id is not None:
                                response_queue.put(("__audio__", source_speech_id, resampled_bytes))
                    else:
                        # JSON 消息（日志用）
                        try:
                            msg = json.loads(message)
                            msg_type = msg.get('type', '')
                            if msg_type == 'sentence':
                                # TTS 文本原文不写 logger
                                _gsv_text = msg.get('text', '')
                                logger.debug(f"[GPT-SoVITS v3] 合成 (len={len(_gsv_text)} chars)")
                                print(f"[GPT-SoVITS v3] 合成: {_gsv_text[:30]}...")
                            elif msg_type == 'sentence_done':
                                logger.debug(f"[GPT-SoVITS v3] 句完成 (task={msg.get('task_id')}, chunks={msg.get('chunks_sent', '?')})")
                            elif msg_type == 'done':
                                logger.debug("[GPT-SoVITS v3] 会话完成")
                            elif msg_type == 'error':
                                error_msg = str(msg.get('message', ''))
                                _enqueue_error(response_queue, f"[GPT-SoVITS v3] 服务端错误: {error_msg}")
                            elif msg_type == 'flushed':
                                logger.debug("[GPT-SoVITS v3] flush 完成")
                        except json.JSONDecodeError:
                            pass
            except websockets.exceptions.ConnectionClosed:
                logger.debug("[GPT-SoVITS v3] WS 连接已关闭")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                _enqueue_error(response_queue, f"[GPT-SoVITS v3] 接收循环异常: {e}")

        async def close_session(ws_conn, recv_task, send_end=True):
            """Close the current WS session"""
            nonlocal resampler
            if send_end and _ws_is_open(ws_conn):
                try:
                    await ws_conn.send(json.dumps({"cmd": "end"}))
                    # 等待 done 消息（最多 30 秒，让推理完成）
                    await asyncio.wait_for(recv_task, timeout=30.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            if recv_task and not recv_task.done():
                recv_task.cancel()
                try:
                    await recv_task
                except (asyncio.CancelledError, Exception):
                    pass
            if _ws_is_open(ws_conn):
                try:
                    await ws_conn.close()
                except Exception:
                    pass
            resampler = None

        async def create_connection(source_speech_id=None):
            """Create a new WS connection and send init"""
            nonlocal ws, receive_task, resampler
            resampler = None

            logger.debug(f"[GPT-SoVITS v3] 连接: {WS_URL}")
            ws = await websockets.connect(WS_URL, ping_interval=None, max_size=10 * 1024 * 1024)

            # 发送 init 指令（合并高级参数，过滤保留字段防止覆盖）
            safe_params = {k: v for k, v in extra_params.items() if k not in ("cmd", "voice_id")}
            init_msg = {"cmd": "init", "voice_id": v3_voice_id, **safe_params}
            await ws.send(json.dumps(init_msg))

            # 等待 ready 响应
            ready_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            ready_data = json.loads(ready_msg)
            if ready_data.get('type') != 'ready':
                raise RuntimeError(f"init 失败: {ready_data}")

            logger.debug(f"[GPT-SoVITS v3] 会话就绪 (voice={v3_voice_id})")

            # 启动接收协程
            receive_task = asyncio.create_task(receive_loop(ws, source_speech_id))
            return ws

        # ─── 初始连接验证 ───
        try:
            await create_connection()
            logger.info(f"[GPT-SoVITS v3] TTS 已就绪 (WS 双工模式): {WS_URL}")
            logger.info(f"  voice_id: {v3_voice_id}")
            response_queue.put(("__ready__", True))
        except Exception as e:
            logger.error(f"[GPT-SoVITS v3] 初始连接失败: {e}")
            logger.error("请确保 GPT-SoVITS 服务已运行且端口正确")
            response_queue.put(("__ready__", False))
            return

        # ─── 主循环 ───
        try:
            loop = asyncio.get_running_loop()

            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    # 打断：立即关闭连接，不发 end、不等推理完成
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=False)
                        ws = None
                        receive_task = None
                    current_speech_id = None
                    continue

                # speech_id 变化 → 打断旧会话，创建新连接
                # 打断时不发 end（避免等待推理完成），直接关闭连接
                if sid != current_speech_id and sid is not None:
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=False)
                        ws = None
                        receive_task = None
                    current_speech_id = sid
                    for _retry in range(3):
                        try:
                            await create_connection(sid)
                            break
                        except Exception as e:
                            logger.warning(f"[GPT-SoVITS v3] 连接失败 (retry {_retry+1}/3): {e}")
                            ws = None
                            if _retry < 2:
                                await asyncio.sleep(0.5 * (2 ** _retry))
                    else:
                        logger.error("[GPT-SoVITS v3] 连接重试耗尽，跳过当前文本")
                        continue

                if sid is None:
                    # 正常结束：发送 end 关闭会话（v3 end 会自动 flush 剩余文本）
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=True)
                        ws = None
                        receive_task = None
                    current_speech_id = None
                    continue

                if not tts_text or not tts_text.strip():
                    continue

                # kaomoji / 颜文字兜底：见 _GSV_ALLOWED_PUNCT 注释。
                # `=。=` `(╯°□°）╯` `^_^` 这类丢；单标点 `，` `。` `？` 放过让
                # server TextBuffer 触发切句。
                if _gsv_should_drop_chunk(tts_text):
                    continue

                # 用 append 累积碎片文本，v3 TextBuffer 自动按标点切句推理
                if _ws_is_open(ws):
                    try:
                        await ws.send(json.dumps({"cmd": "append", "data": tts_text}))
                        _record_tts_telemetry("gptsovits", len(tts_text))
                        # TTS 文本原文不写 logger
                        logger.debug(f"[GPT-SoVITS v3] append (len={len(tts_text)} chars)")
                        print(f"[GPT-SoVITS v3] append: {tts_text[:30]}...")
                    except Exception as e:
                        logger.error(f"[GPT-SoVITS v3] 发送失败: {e}")
                        ws = None
                        receive_task = None
                        current_speech_id = None

        except Exception as e:
            _enqueue_error(response_queue, f"[GPT-SoVITS v3] Worker 错误: {e}")
            response_queue.put(("__ready__", False))
        finally:
            # 清理
            if _ws_is_open(ws):
                await close_session(ws, receive_task, send_end=False)

    # 运行异步 worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"[GPT-SoVITS v3] Worker 启动失败: {e}")
        response_queue.put(("__ready__", False))

def _gptsovits_is_selected(ctx) -> bool:
    core_config, cm = ctx.core_config, ctx.cm
    # 选中信号收口到 GPTSOVITS_ENABLED 单一真相：config_manager 的 snapshot 已把
    # ttsModelProvider=='gptsovits' 下拉（与 pre-#1830 存量旧开关）派生进
    # GPTSOVITS_ENABLED，这里不再自己 raw load core_config.json 读 ttsModelProvider。
    # snapshot 写 GPTSOVITS_ENABLED 时已 _as_bool 规整，这里再包一层防御性对齐隔壁
    # ENABLE_CUSTOM_API / core.py，避免直接传入未规整 dict 时字符串 "false"/"0"
    # 被当真值误抢 GPT-SoVITS。
    if not _as_bool(core_config.get('GPTSOVITS_ENABLED'), False):
        return False
    try:
        tts_config = cm.get_model_api_config('tts_custom')
    except Exception:
        return False
    return bool(tts_config.get('is_custom'))

def _gptsovits_resolve(ctx):
    return gptsovits_tts_worker, None, 'gptsovits'
