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
from dataclasses import dataclass, field
from queue import Empty

from config import GSV_VOICE_PREFIX
from utils.config_manager import _as_bool, get_config_manager
from utils.gptsovits_config import gsv_ws_url_from_http_base, is_valid_http_url, redact_url_for_log, resolve_worker_gsv_api_url

from .._infra import AudioDoneEmitter, TTS_SHUTDOWN_SENTINEL, _resample_audio, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")
GPTSOVITS_END_TIMEOUT_SECONDS = 30.0

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


@dataclass(eq=False)
class _GsvSpeech:
    """One utterance owns its network work; the control queue never waits for it."""

    speech_id: str
    commands: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    cancelled: bool = False
    ending: bool = False
    closed: bool = False

def gptsovits_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """GPT-SoVITS TTS worker - uses the v3 WebSocket stream-input duplex mode
    
    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key (unused, kept for interface consistency)
        voice_id: v3 voice config ID, formatted as "voice_id" or "voice_id|advanced-params JSON"
                  e.g.: "my_voice" or "my_voice|{\"speed_factor\":1.2,\"text_lang\":\"all_zh\"}"
    
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
        current = None
        live_speeches = set()
        stopping = False

        async def cleanup_socket(ws_conn, receiver=None, command=None):
            for task in (command, receiver):
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if ws_conn is not None:
                try:
                    await ws_conn.close()
                except Exception as exc:
                    logger.debug("[GPT-SoVITS v3] WS 关闭失败: %s", exc)

        async def finish_cleanup(ws_conn, receiver=None, command=None):
            # A later interrupt may arrive during normal close. It must not
            # cancel cleanup itself or strand an old socket/receiver.
            cleanup = asyncio.create_task(cleanup_socket(ws_conn, receiver, command))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

        async def open_socket():
            ws_conn = await websockets.connect(WS_URL, ping_interval=None, max_size=10 * 1024 * 1024)
            try:
                safe_params = {k: v for k, v in extra_params.items() if k not in ("cmd", "voice_id")}
                await ws_conn.send(json.dumps({"cmd": "init", "voice_id": v3_voice_id, **safe_params}))
                ready = json.loads(await asyncio.wait_for(ws_conn.recv(), timeout=10.0))
                if ready.get("type") != "ready":
                    raise RuntimeError(f"init 失败: {ready}")
                return ws_conn
            except (asyncio.CancelledError, Exception):
                await finish_cleanup(ws_conn)
                raise

        async def warmup():
            ws_conn = None
            try:
                ws_conn = await open_socket()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[GPT-SoVITS v3] 初始连接失败: %s", exc)
                response_queue.put(("__ready__", False))
                return False
            finally:
                await finish_cleanup(ws_conn)
            if not stopping:
                response_queue.put(("__ready__", True))
            return True

        def report_error(speech, error):
            if not speech.cancelled:
                # The manager already understands this marker. A retired turn's
                # error must not be charged to a replacement speech_id either.
                response_queue.put(("__tts_sentence_failed__", speech.speech_id, ""))
                _enqueue_error(response_queue, f"[GPT-SoVITS v3] Worker 错误: {error}")

        def put_pcm(speech, payload):
            if payload and not speech.cancelled:
                response_queue.put(("__audio__", speech.speech_id, payload))

        async def receive_audio(speech, ws_conn):
            resampler = None
            source_rate = None
            async for message in ws_conn:
                if speech.cancelled:
                    return False
                if isinstance(message, bytes):
                    pcm, source_rate = _extract_pcm_from_wav(message)
                    if pcm:
                        audio = np.frombuffer(pcm, dtype=np.int16)
                        if source_rate != 48000:
                            if resampler is None:
                                resampler = soxr.ResampleStream(source_rate, 48000, 1, dtype='float32')
                            pcm = _resample_audio(audio, source_rate, 48000, resampler)
                        put_pcm(speech, pcm)
                    continue
                try:
                    message = json.loads(message)
                except json.JSONDecodeError:
                    continue
                kind = message.get("type")
                logger.debug("[GPT-SoVITS v3] event=%s speech_id=%s", kind, speech.speech_id)
                if kind == "error":
                    raise RuntimeError(f"服务端错误: {message.get('message', '')}")
                if kind == "done":
                    if resampler is not None and not speech.cancelled:
                        tail = _resample_audio(np.empty(0, dtype=np.int16), source_rate, 48000,
                                               resampler, last=True)
                        put_pcm(speech, tail)
                    return True
            return False

        async def run_speech(speech):
            ws_conn = receiver = command = None
            complete = False
            audio_done = AudioDoneEmitter(response_queue)
            try:
                # Cancelling one utterance must not cancel shared initialization.
                if not await asyncio.shield(startup):
                    return
                for attempt in range(3):
                    try:
                        ws_conn = await open_socket()
                        break
                    except Exception as exc:
                        if attempt == 2:
                            raise
                        logger.warning("[GPT-SoVITS v3] 连接失败 (retry %s/3): %s", attempt + 1, exc)
                        await asyncio.sleep(0.5 * (2 ** attempt))
                receiver = asyncio.create_task(receive_audio(speech, ws_conn))
                while not speech.cancelled:
                    command = asyncio.create_task(speech.commands.get())
                    done, _ = await asyncio.wait((command, receiver), return_when=asyncio.FIRST_COMPLETED)
                    if receiver in done:
                        await receiver  # Retrieve upstream errors before reporting an early close.
                        raise RuntimeError("会话在文本结束前关闭")
                    text = await command
                    command = None
                    if text is None:
                        await ws_conn.send(json.dumps({"cmd": "end"}))
                        try:
                            complete = await asyncio.wait_for(receiver, timeout=GPTSOVITS_END_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError as exc:
                            raise RuntimeError(f"等待尾部音频超时（{GPTSOVITS_END_TIMEOUT_SECONDS:g}秒）") from exc
                        if not complete:
                            raise RuntimeError("会话关闭但未收到 done")
                        break
                    await ws_conn.send(json.dumps({"cmd": "append", "data": text}))
                    _record_tts_telemetry("gptsovits", len(text))
                    logger.debug("[GPT-SoVITS v3] append (len=%s chars)", len(text))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                report_error(speech, exc)
                # Mute before cancelling a failed receiver: a callback may already
                # have taken a frame off the wire when cancellation arrives.
                speech.cancelled = True
            finally:
                try:
                    await finish_cleanup(ws_conn, receiver, command)
                finally:
                    speech.closed = True
            if complete and not speech.cancelled:
                audio_done.emit(speech.speech_id)

        def retire(speech):
            if speech is not None and not speech.cancelled and not speech.closed:
                speech.cancelled = True
                logger.debug("[GPT-SoVITS v3] interrupt speech_id=%s", speech.speech_id)
                speech.task.cancel()

        def speech_finished(speech, task):
            speech.closed = True
            live_speeches.discard(speech)
            if not task.cancelled() and task.exception() is not None:
                report_error(speech, task.exception())

        startup = asyncio.create_task(warmup())
        try:
            while True:
                if startup.done() and not startup.result():
                    break
                try:
                    # Queue.get itself cannot be cancelled by asyncio. A bounded
                    # idle wait lets startup failure retire the worker/executor.
                    sid, text = await asyncio.to_thread(request_queue.get, timeout=0.1)
                except Empty:
                    continue
                if sid == TTS_SHUTDOWN_SENTINEL:
                    break
                if sid == "__interrupt__":
                    retire(current)
                    current = None
                    continue
                if sid is None:
                    if current is not None and not current.cancelled and not current.closed and not current.ending:
                        current.ending = True
                        current.commands.put_nowait(None)
                    continue
                if not text or not text.strip() or _gsv_should_drop_chunk(text):
                    continue
                if current is None or current.speech_id != sid or current.cancelled or current.closed:
                    retire(current)
                    current = _GsvSpeech(sid)
                    live_speeches.add(current)
                    current.task = asyncio.create_task(run_speech(current))
                    current.task.add_done_callback(lambda task, speech=current: speech_finished(speech, task))
                if not current.ending:
                    current.commands.put_nowait(text)
        finally:
            stopping = True
            remaining = list(live_speeches)
            for speech in remaining:
                retire(speech)
            if not startup.done():
                startup.cancel()
            await asyncio.gather(startup, *(speech.task for speech in remaining), return_exceptions=True)

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
