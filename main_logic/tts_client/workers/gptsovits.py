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
from utils.gptsovits_config import gsv_ws_url_from_http_base, is_valid_http_url, redact_url_for_log, resolve_worker_gsv_api_url

from .._infra import AudioDoneEmitter, TTS_SHUTDOWN_SENTINEL, _resample_audio, _enqueue_error
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

# GSV worker sentence-punctuation allowlist. Criteria:
# - Contains letters/digits (Unicode letters include CJK / kana / Hangul / Greek / Cyrillic /
#   Arabic): real content, allow.
# - No letters/digits but entirely allowlisted punctuation, regardless of count: genuine LLM punctuation chunks (`，` `。`
#   `？`, including rare `。。。` `？！`), allowed so server TextBuffer can split sentences.
# - No letters/digits and any nonallowlisted symbol: kaomoji (`=。=` `(╯°□°）╯` `^_^`), discard.
#
# Include multilingual punctuation (CJK / ASCII / Arabic / Spanish) but exclude kaomoji-prone `(` `)`
# `[` `]` `{` `}` `「」` `《》` `"` `'` `~` `^` `*` `_` `-`, etc.
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
    _ = audio_api_key  # Unused, retained for interface consistency.

    # Get configuration.
    base_url = resolve_worker_gsv_api_url(get_config_manager())

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

    # Strip gsv:, the character system's GPT-SoVITS voice_id routing prefix.
    # Parse voice_id in either "voice_id" or "voice_id|{advanced JSON parameters}" format.
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

    # Preload websockets State, accommodating different versions.
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
        # Fallback for older websockets versions.
        return not getattr(ws_conn, 'closed', True)

    def _extract_pcm_from_wav(wav_bytes: bytes) -> tuple:
        """Extract PCM data and the sample rate from a WAV chunk"""
        if len(wav_bytes) < 44:
            return None, 0
        src_rate = int.from_bytes(wav_bytes[24:28], 'little')
        pcm_data = wav_bytes[44:]
        if len(pcm_data) < 2:
            return None, 0
        # Ensure an even length.
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]
        return pcm_data, src_rate

    async def async_worker():
        """Async TTS worker main loop - WebSocket duplex mode"""
        ws = None
        receive_task = None
        current_speech_id = None
        resampler = None
        # When close_session(send_end=True) returns, reception has ended normally after done or through
        # cancellation. No more audio can enter this turn's queue; that is the actual audio-stream closure point.
        audio_done = AudioDoneEmitter(response_queue)

        async def receive_loop(ws_conn):
            """Independent receive coroutine: handles audio chunks and JSON messages returned by the WS"""
            nonlocal resampler
            try:
                async for message in ws_conn:
                    if isinstance(message, bytes):
                        # Each binary frame is a complete WAV chunk with header.
                        pcm_data, src_rate = _extract_pcm_from_wav(message)
                        if pcm_data is not None and len(pcm_data) > 0:
                            audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                            if src_rate != 48000:
                                if resampler is None:
                                    resampler = soxr.ResampleStream(src_rate, 48000, 1, dtype='float32')
                                resampled_bytes = _resample_audio(audio_array, src_rate, 48000, resampler)
                            else:
                                resampled_bytes = audio_array.tobytes()
                            response_queue.put(resampled_bytes)
                    else:
                        # JSON message for logging.
                        try:
                            msg = json.loads(message)
                            msg_type = msg.get('type', '')
                            if msg_type == 'sentence':
                                # Never write raw TTS text through logger.
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
                    # Await done for at most 30 seconds, allowing inference to finish.
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

        async def create_connection():
            """Create a new WS connection and send init"""
            nonlocal ws, receive_task, resampler
            resampler = None

            logger.debug(f"[GPT-SoVITS v3] 连接: {WS_URL}")
            ws = await websockets.connect(WS_URL, ping_interval=None, max_size=10 * 1024 * 1024)

            # Send init, merging advanced parameters while filtering reserved fields against overwrite.
            safe_params = {k: v for k, v in extra_params.items() if k not in ("cmd", "voice_id")}
            init_msg = {"cmd": "init", "voice_id": v3_voice_id, **safe_params}
            await ws.send(json.dumps(init_msg))

            # Await ready.
            ready_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            ready_data = json.loads(ready_msg)
            if ready_data.get('type') != 'ready':
                raise RuntimeError(f"init 失败: {ready_data}")

            logger.debug(f"[GPT-SoVITS v3] 会话就绪 (voice={v3_voice_id})")

            # Start the receiver coroutine.
            receive_task = asyncio.create_task(receive_loop(ws))
            return ws

        # -- Initial connection validation --
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

        # -- Main loop --
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
                    # Interrupt: close immediately without sending end or waiting for inference.
                    audio_done.begin_interrupt()  # Interrupted turns omit audio_done, using the separate cancellation channel.
                    try:
                        if _ws_is_open(ws):
                            await close_session(ws, receive_task, send_end=False)
                            ws = None
                            receive_task = None
                        current_speech_id = None
                        audio_done.reset()
                    finally:
                        audio_done.end_interrupt()
                    continue

                # speech_id changes -> interrupt the old session and create a new connection.
                # On interruption, close directly without end to avoid waiting for inference.
                if sid != current_speech_id and sid is not None:
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=False)
                        ws = None
                        receive_task = None
                    current_speech_id = sid
                    audio_done.reset()  # Reset audio_done deduplication for the new turn.
                    for _retry in range(3):
                        try:
                            await create_connection()
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
                    # Normal completion: send end to close the session; v3 end automatically flushes remaining text.
                    # Capture this turn's sid first: close_session yields, then current_speech_id must be cleared.
                    finished_speech_id = current_speech_id
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=True)
                        ws = None
                        receive_task = None
                        # Session closed and receiver stopped; this turn has no more audio.
                        audio_done.emit(finished_speech_id)
                    current_speech_id = None
                    continue

                if not tts_text or not tts_text.strip():
                    continue

                # Kaomoji fallback: see _GSV_ALLOWED_PUNCT.
                # Discard `=。=` `(╯°□°）╯` `^_^`; allow standalone `，` `。` `？` so
                # server TextBuffer can split sentences.
                if _gsv_should_drop_chunk(tts_text):
                    continue

                # Accumulate text fragments with append; v3 TextBuffer automatically splits on punctuation for inference.
                if _ws_is_open(ws):
                    try:
                        await ws.send(json.dumps({"cmd": "append", "data": tts_text}))
                        _record_tts_telemetry("gptsovits", len(tts_text))
                        # Never write raw TTS text through logger.
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
            # Cleanup.
            if _ws_is_open(ws):
                await close_session(ws, receive_task, send_end=False)

    # Run the async worker.
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"[GPT-SoVITS v3] Worker 启动失败: {e}")
        response_queue.put(("__ready__", False))

def _gptsovits_is_selected(ctx) -> bool:
    core_config, cm = ctx.core_config, ctx.cm
    # Consolidate selection into GPTSOVITS_ENABLED: config_manager snapshots already derive
    # ttsModelProvider=='gptsovits', plus pre-#1830 legacy switches, into
    # GPTSOVITS_ENABLED. Do not raw-load core_config.json here to read ttsModelProvider again.
    # Snapshot creation already normalizes GPTSOVITS_ENABLED with _as_bool; defensively repeat it to match
    # ENABLE_CUSTOM_API / core.py and prevent raw dict strings "false"/"0"
    # from wrongly selecting GPT-SoVITS through truthiness.
    if not _as_bool(core_config.get('GPTSOVITS_ENABLED'), False):
        return False
    try:
        tts_config = cm.get_model_api_config('tts_custom')
    except Exception:
        return False
    return bool(tts_config.get('is_custom'))

def _gptsovits_resolve(ctx):
    return gptsovits_tts_worker, None, 'gptsovits'
