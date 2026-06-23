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

"""xAI Grok streaming TTS worker."""

import numpy as np
import soxr
import json
import base64
import websockets
import asyncio

from utils.native_voice_registry import make_native_tts_resolver, register_tts_worker_resolver

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, make_audio_jitter_buffer, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# xAI 文档：'Individual deltas are capped at 15,000 characters'。
# pending_text 累积 + 长 utterance 合并下可能超过这个上限，需要切片发送。
_XAI_TTS_DELTA_CAP = 15000

def _grok_chunk_text_delta(text: str, cap: int = _XAI_TTS_DELTA_CAP) -> list[str]:
    """Split text that may exceed the xAI text.delta cap into multiple sequentially sent segments.
    Each returned segment has length <= cap; empty input returns an empty list."""
    if not text:
        return []
    if len(text) <= cap:
        return [text]
    return [text[i:i + cap] for i in range(0, len(text), cap)]

def grok_streaming_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    xAI Grok streaming TTS worker (wss://api.x.ai/v1/tts)

    Protocol traits (vs. step):
      - no session handshake / no tts.create; push text.delta right after connecting
      - all configuration goes through query params (voice/language/codec/sample_rate)
      - with codec=pcm the audio is raw 16-bit little-endian, no WAV header
      - language is required; use auto for server-side detection, skipping client-side language detection

    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data and the ready signal
        audio_api_key: xAI API key
        voice_id: built-in voice (eve/ara/leo/rex/sal) / alias (male, female-voice
            labels, etc.) / custom 8-char voice id / empty. The routing layer
            (native_voice_registry) recognizes aliases as native; the worker then
            normalizes to the xAI canonical id here, because the xAI endpoint's
            voice query param only accepts canonical ids or custom 8-char ids,
            not aliases.
    """
    from utils.grok_tts_voices import normalize_grok_tts_voice
    # 先 strip：whitespace-only 输入（如 '   '）等价于空，否则 'not voice_id'
    # 判定通不过，残留的空白会被透传到 xAI 的 voice query param 引发合成失败。
    voice_id = (voice_id or "").strip()
    canonical_voice, recognized = normalize_grok_tts_voice(voice_id)
    if recognized or not voice_id:
        # 识别出 native id / alias → 用归一化后的 canonical；
        # 空输入 → normalize 已经返回 default (eve)。
        voice_id = canonical_voice
    # else: 非空且不识别 → 视为用户自定义 8 位 voice_id，原样透传给 xAI

    async def async_worker():
        from urllib.parse import urlencode
        params = urlencode({
            "voice": voice_id,
            "language": "auto",
            "codec": "pcm",
            "sample_rate": 24000,
        })
        tts_url = f"wss://api.x.ai/v1/tts?{params}"
        headers = {"Authorization": f"Bearer {audio_api_key}"}

        ws = None
        current_speech_id = None
        receive_task = None
        text_done_sent = False
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # 与 step/qwen 对偶：xAI 流式音频首包后第一个 inter-chunk gap 偏大，用共享
        # jitter buffer 攒首包领先量盖过开头几个字的 jitter。
        audio_jitter = make_audio_jitter_buffer(response_queue)
        # 当 reconnect 失败时缓冲尚未发出的文本 chunks（同一 utterance）。下一次
        # 同 sid chunk 到达并 reconnect 成功后，缓冲内容会拼到第一条 text.delta
        # 前一起发送 —— 避免触发 reconnect 的那一条 chunk 在 continue 后丢失，
        # 短回复（utterance 只有 1 个 chunk）尤其需要这条保险。
        # `pending_text_sid` 把缓冲绑定到产生它的 sid：跨 utterance 时（sid 切换、
        # interrupt、当前 utterance 结束 flush 不出）必须丢弃旧 pending，否则
        # 上一轮的残文会被拼进下一轮的首条 text.delta —— 用户层会听到"上一轮内容
        # 串进新回复"的内容污染。
        pending_text: list[str] = []
        pending_text_sid: str | None = None

        async def receive_messages():
            # xAI 实际可能发 binary frame（raw PCM）或 JSON-wrapped base64 audio.delta，
            # 文档未明确给出，两路径都保留。字段名走 'delta'（OpenAI Realtime 标准）
            # 但保留 'audio' 作为兜底，未来如果 xAI 改名也不会立刻挂。
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        try:
                            audio_array = np.frombuffer(message, dtype=np.int16)
                            audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                        except Exception as e:
                            logger.error(f"xAI TTS 二进制音频解码失败: {e}")
                        continue
                    try:
                        event = json.loads(message)
                    except Exception as e:
                        preview = message if len(message) < 200 else message[:200] + "...<truncated>"
                        logger.warning(f"xAI TTS recv (non-JSON): {preview} err={e}")
                        continue
                    event_type = event.get("type")
                    if event_type == "audio.delta":
                        audio_b64 = event.get("delta") or event.get("audio") or ""
                        if not audio_b64:
                            logger.warning(f"xAI TTS audio.delta 无音频字段，event keys={list(event.keys())}")
                            continue
                        try:
                            audio_bytes = base64.b64decode(audio_b64)
                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                            audio_jitter.append(_resample_audio(audio_array, 24000, 48000, resampler))
                        except Exception as e:
                            logger.error(f"xAI TTS 音频解码失败: {e}")
                    elif event_type == "audio.done":
                        # 本轮音频结束，放掉缓冲区里不足 steady 阈值的尾音
                        audio_jitter.flush()
                    elif event_type == "error":
                        logger.error(f"xAI TTS server error: {event}")
                        _enqueue_error(response_queue, event)
                    else:
                        # 未知 event 留 INFO — 出现新事件类型时能立即看见
                        preview = message if len(message) < 200 else message[:200] + "...<truncated>"
                        logger.info(f"xAI TTS recv unknown type={event_type!r} raw={preview}")
            except websockets.exceptions.ConnectionClosed as e:
                # 仅对异常关闭出 log（1006=abnormal、4xxx=应用层）。正常 1000 静默，
                # 避免 worker 每次 sid 切换主动 close 时也刷一行。
                if e.code != 1000:
                    logger.info(f"xAI TTS WebSocket closed: code={e.code} reason={e.reason!r}")
            except Exception as e:
                logger.error(f"xAI TTS 接收出错: {type(e).__name__}: {e}")

        try:
            # close_timeout=0.5：上限 close handshake 等待，避免半开连接在 sid 切换
            # 路径 / interrupt / finally 清理时阻塞主循环数秒，伤后续 TTS 响应。
            ws = await websockets.connect(tts_url, additional_headers=headers, close_timeout=0.5)
            receive_task = asyncio.create_task(receive_messages())

            logger.info("xAI Grok TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))

            loop = asyncio.get_running_loop()
            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    if ws:
                        try:
                            await asyncio.wait_for(ws.close(), timeout=0.5)
                        except Exception:
                            pass
                        ws = None
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                        receive_task = None
                    current_speech_id = None
                    text_done_sent = False
                    pending_text.clear()
                    pending_text_sid = None
                    audio_jitter.reset()  # 打断：丢弃未放出的缓冲音频
                    continue

                if sid is None:
                    # 当前 speech 文本流结束。如果 ws 还死着（reconnect 持续失败）
                    # 但缓冲里有同 sid 的内容（典型场景：单 chunk 短消息首次 reconnect
                    # 失败，pending 缓冲了那一条，下一个进来就是 sid=None 结束信号），
                    # 做一次 last-chance reconnect，把 pending 发出去再 text.done。
                    # 失败就放弃，避免短消息被 transient 网络故障吞掉。
                    if ws is None and pending_text and pending_text_sid is not None:
                        try:
                            ws = await websockets.connect(tts_url, additional_headers=headers, close_timeout=0.5)
                            receive_task = asyncio.create_task(receive_messages())
                            current_speech_id = pending_text_sid
                            text_done_sent = False
                            resampler.clear()
                            audio_jitter.reset()
                            logger.info("xAI TTS last-chance reconnect on utterance end succeeded")
                        except Exception as e:
                            logger.warning(f"xAI TTS last-chance reconnect 失败，pending 丢弃: {e}")

                    if ws and current_speech_id is not None and not text_done_sent:
                        if pending_text and pending_text_sid == current_speech_id:
                            try:
                                for delta in _grok_chunk_text_delta("".join(pending_text)):
                                    await ws.send(json.dumps({"type": "text.delta", "delta": delta}))
                            except Exception as e:
                                # send 失败可能是 last-chance reconnect 拿到的 ws 半死状态
                                # （服务端在 utterance 间隙 close）。再做一次 fresh reconnect
                                # 重试，把 pending 救出去，避免 utterance 截尾静音。
                                logger.warning(f"flush pending_text 首次失败，尝试重连重试: {e}")
                                if ws:
                                    try:
                                        await asyncio.wait_for(ws.close(), timeout=0.5)
                                    except Exception:
                                        pass
                                if receive_task and not receive_task.done():
                                    receive_task.cancel()
                                    try:
                                        await receive_task
                                    except asyncio.CancelledError:
                                        pass
                                try:
                                    ws = await websockets.connect(tts_url, additional_headers=headers, close_timeout=0.5)
                                    receive_task = asyncio.create_task(receive_messages())
                                    # 换了新 receive_task：与 last-chance reconnect 一致，reset 掉旧连接
                                    # 里未 flush 的残留音频，避免拼到重试音频前面（ws 原本存活、上一轮
                                    # 已 append 过的路径才会非空；走过 last-chance 的路径 buffer 已空）。
                                    resampler.clear()
                                    audio_jitter.reset()
                                    for delta in _grok_chunk_text_delta("".join(pending_text)):
                                        await ws.send(json.dumps({"type": "text.delta", "delta": delta}))
                                    logger.info("flush pending_text 重连重试成功")
                                except Exception as e2:
                                    logger.warning(f"flush pending_text 重连重试仍失败，pending 丢失: {e2}")
                        try:
                            await ws.send(json.dumps({"type": "text.done"}))
                            text_done_sent = True
                        except Exception as e:
                            logger.warning(f"发送 text.done 失败: {e}")
                    pending_text.clear()
                    pending_text_sid = None
                    continue

                # 新 speech_id — 关旧开新（对偶 step worker 的重连策略）
                if current_speech_id != sid:
                    # 关旧连接 / cancel 旧 receive_task 无条件做（避免泄漏），但
                    # current_speech_id / text_done_sent / resampler 状态切换必须
                    # 推迟到 connect 成功之后再 commit。否则一次瞬态 connect 失败会
                    # 把 sid 提前推进，后续同 sid 的 chunks 走到 `if not ws: continue`
                    # 被静默丢弃，直到出现新 sid 才重试 —— 当轮 utterance 静音。
                    if ws:
                        # bound close handshake — 默认 10s 在半开连接下会阻塞主循环、
                        # 拖延下一条 chunk 响应，明显伤交互延迟。
                        try:
                            await asyncio.wait_for(ws.close(), timeout=0.5)
                        except Exception:
                            pass
                        ws = None
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                        receive_task = None
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers, close_timeout=0.5)
                        receive_task = asyncio.create_task(receive_messages())
                    except Exception as e:
                        logger.error(f"xAI TTS 重连失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        # 缓冲当前 chunk —— 否则 continue 后这条文本永远丢失，
                        # 短消息（utterance 只有 1 个 chunk）会整段静音。绑 sid，
                        # 后续如果切换到别的 sid 而旧 pending 还在，能在发送前丢掉
                        # 避免跨 utterance 内容污染。
                        if tts_text and tts_text.strip():
                            if pending_text_sid != sid:
                                # 上一个失败的 utterance 残留，丢弃后重新绑定到当前 sid
                                pending_text.clear()
                            pending_text.append(tts_text)
                            pending_text_sid = sid
                        await asyncio.sleep(1.0)
                        # 不更新 current_speech_id —— 下次同 sid 进来会重新尝试重连
                        continue
                    # connect 成功后再 commit sid 切换 + 状态 reset
                    current_speech_id = sid
                    text_done_sent = False
                    resampler.clear()
                    audio_jitter.reset()  # 新轮次重置 jitter buffer 领先量

                if not tts_text or not tts_text.strip():
                    continue
                if text_done_sent:
                    continue
                if not ws:
                    continue

                # 如果之前 reconnect 失败缓冲了同 sid 的文本，先拼上一起发出去，
                # 维持 utterance 内 chunk 的原顺序；如果 pending 属于别的 sid（跨
                # utterance 残留），直接丢掉防止内容污染。
                if pending_text and pending_text_sid == current_speech_id:
                    payload_text = "".join(pending_text) + tts_text
                    pending_text.clear()
                    pending_text_sid = None
                else:
                    if pending_text:
                        logger.debug(
                            "xAI TTS 丢弃跨 utterance 的残留 pending_text (sid=%s, current=%s, len=%d)",
                            pending_text_sid, current_speech_id, sum(len(x) for x in pending_text),
                        )
                        pending_text.clear()
                        pending_text_sid = None
                    payload_text = tts_text

                try:
                    # 字段名用 'delta'（OpenAI Realtime 标准；xAI 文档把消息体叫
                    # "deltas"——"Individual deltas are capped at 15,000 characters"）。
                    # 用 'text' 时服务端 silently 当空字符串处理，合成 0 字节后直接 audio.done。
                    # 长 buffer 合并可能超 15k 上限，按 cap 切片顺序发；xAI 流式合成
                    # 按到达顺序处理，多 delta 等价单 delta。
                    for delta in _grok_chunk_text_delta(payload_text):
                        await ws.send(json.dumps({"type": "text.delta", "delta": delta}))
                    _record_tts_telemetry("grok", len(payload_text))
                except Exception as e:
                    logger.error(f"发送 text.delta 失败: {type(e).__name__}: {e}")
                    # send 失败时把内容放回 pending（绑定当前 sid），等下次重连后重发。
                    pending_text.append(payload_text)
                    pending_text_sid = current_speech_id
                    ws = None
                    current_speech_id = None
                    # 与 step / qwen worker 对偶：send 失败时同步 cancel 旧
                    # receive_task，避免短暂窗口内僵尸 receive 协程把残音频写
                    # 进 response_queue。connection 已死，receive 会自然拿到
                    # ConnectionClosed，cancel 只是加速清理。
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                    receive_task = None

        except Exception as e:
            logger.error(f"xAI Grok TTS Worker 错误: {type(e).__name__}: {e!r}", exc_info=True)
            if 'HTTP 503' in str(e):
                _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            response_queue.put(("__ready__", False))
        finally:
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
            if ws:
                try:
                    await asyncio.wait_for(ws.close(), timeout=0.5)
                except Exception:
                    pass

    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"xAI Grok TTS Worker 启动失败: {type(e).__name__}: {e!r}", exc_info=True)
        response_queue.put(("__ready__", False))

# xAI Grok 内置音色（eve/ara/leo/rex/sal）同样走 CORE_API_KEY。
# 没有这个注册时，非空 voice_id 会让 core._has_custom_tts() 返 True，
# get_tts_worker() 在 `core_api_type == 'grok'` 默认分支前就路由到
# cosyvoice_vc_tts_worker —— 静默合成或鉴权失败。详见 PR #1306 Codex review。
register_tts_worker_resolver(
    'grok',
    make_native_tts_resolver(grok_streaming_tts_worker, 'core_api_key'),
)
