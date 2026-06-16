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

"""vLLM-Omni TTS worker."""

import numpy as np
import soxr
import json
import websockets
import asyncio

from functools import partial
from urllib.parse import urlparse, urlunparse
from utils.config_manager import _as_bool
from utils.gptsovits_config import redact_url_for_log

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, _enqueue_error
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

VLLM_OMNI_DEFAULT_BASE_URL = "ws://localhost:8091/v1"
VLLM_OMNI_DEFAULT_MODEL = "Qwen3-TTS"

def vllm_omni_tts_worker(request_queue, response_queue, audio_api_key, voice_id,
                          base_url='', model='', voice=''):
    """vLLM-Omni TTS worker — full-duplex WebSocket streaming synthesis.

    Protocol: ``ws://{base_url}/v1/audio/speech/stream``

    Client → Server:
      1. ``{"type": "session.config", "model": "...", "voice": "...", ...}``
      2. ``{"type": "input.text", "text": "..."}``  (may be sent multiple times)
      3. ``{"type": "input.done"}``

    Server → Client:
      1. ``{"type": "audio.start", "sentence_index": N, ...}``
      2. <binary frame: PCM 24kHz/16bit/mono>
      3. ``{"type": "audio.done", "sentence_index": N}``
      4. ``{"type": "session.done", "total_sentences": N}``

    Args:
        base_url:  vLLM-Omni service root URL (e.g. ``http://localhost:8091``);
                   automatically rewritten to ws:// scheme.
        model:     Model name (defaults to ``Qwen3-TTS``).
        voice:     Voice id exposed by vllm-omni.
    """
    raw_base_url = (base_url or '').strip().rstrip('/')
    if not raw_base_url:
        logger.error("[vLLM-Omni TTS] 未配置 base_url（TTS_MODEL_URL 为空）")
        _enqueue_error(response_queue, {
            "code": "TTS_CONFIG_INVALID",
            "provider": "vllm_omni",
            "message": "vLLM-Omni TTS 未配置 URL",
        })
        response_queue.put(("__ready__", False))
        return

    # 修复 PR #1764 review #1（CodeRabbit）：URL 规整 + 补 /v1 + 协议转换
    # 原实现：base_url + '/audio/speech/stream'，未做 http→ws 协议转换，未补 /v1，
    # 用户传 http://host:8091 直接交给 websockets.connect 必失败
    if raw_base_url.startswith("https://"):
        ws_url = "wss://" + raw_base_url[len("https://"):]
    elif raw_base_url.startswith("http://"):
        ws_url = "ws://" + raw_base_url[len("http://"):]
    elif raw_base_url.startswith(("ws://", "wss://")):
        ws_url = raw_base_url
    else:
        # 裸 host:port 形式，默认 ws
        ws_url = "ws://" + raw_base_url

    parsed = urlparse(ws_url)
    base_path = (parsed.path or "").rstrip("/")
    if base_path in ("", "/"):
        base_path = "/v1"
    # 修复 PR #1764 review 第二轮 #2：URL 规整幂等——若 path 已是完整 endpoint 则不重复拼接
    if base_path.endswith("/audio/speech/stream"):
        ws_endpoint = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                base_path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
    else:
        ws_endpoint = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                f"{base_path}/audio/speech/stream",
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    effective_model = (model or '').strip() or 'Qwen3-TTS'
    effective_voice = (voice_id or '').strip() or (voice or '').strip() or 'default'

    logger.info(
        "[vLLM-Omni TTS] ws=%s model=%s voice=%s",
        redact_url_for_log(ws_endpoint), effective_model, effective_voice,
    )

    async def async_worker():
        ws = None
        receive_task = None
        # 流式重采样器（24kHz→48kHz）
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        # 修复 PR #1764 review #3（CodeRabbit）：会话生命周期状态
        # session.done 后置为 False，下次 input 前重建连接 + 重发 session.config
        session_state = {
            "active": False,
            "awaiting_done": False,
            "speech_id": None,
        }
        pending_text: list[str] = []
        pending_text_sid: str | None = None

        async def _connect_and_config() -> bool:
            """Open the WS connection and send session.config; return success.

            PR #1764 review #2 (CodeRabbit) fix: forward audio_api_key both via
            the WS handshake Authorization header and the session.config.api_key
            field so deployments behind reverse proxies / auth layers are covered.
            """
            nonlocal ws
            ws_kwargs = {"max_size": None}
            key_for_auth = (audio_api_key or "").strip() if audio_api_key else ""
            if key_for_auth:
                # websockets >= 12: additional_headers；< 12: extra_headers
                ws_kwargs["additional_headers"] = [
                    ("Authorization", f"Bearer {key_for_auth}"),
                ]
            try:
                ws = await websockets.connect(ws_endpoint, **ws_kwargs)
            except TypeError:
                # 兼容旧版本 websockets：参数名退化为 extra_headers
                if "additional_headers" in ws_kwargs:
                    ws_kwargs["extra_headers"] = ws_kwargs.pop("additional_headers")
                try:
                    ws = await websockets.connect(ws_endpoint, **ws_kwargs)
                except Exception as e:
                    logger.error(f"[vLLM-Omni TTS] WS 连接失败(兼容旧版): {e}")
                    return False
            except Exception as e:
                logger.error(f"[vLLM-Omni TTS] WS 连接失败: {e}")
                return False

            try:
                config = {
                    "type": "session.config",
                    "model": effective_model,
                    "voice": effective_voice,
                    "response_format": "pcm",
                    "speed": 1.0,
                    "stream_audio": True,
                    "split_granularity": "sentence",
                }
                # session 层鉴权（部分自建服务端从 config 读 api_key）
                if key_for_auth:
                    config["api_key"] = key_for_auth
                await ws.send(json.dumps(config))
                return True
            except Exception as e:
                logger.error(f"[vLLM-Omni TTS] 发送 session.config 失败: {e}")
                try:
                    await ws.close()
                except Exception:
                    pass
                ws = None
                return False

        async def _receive_loop():
            """Receive WS messages: JSON events plus binary PCM frames."""
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        # 二进制 PCM 帧：24kHz/16bit/mono → 重采样 48kHz
                        if len(message) < 2:
                            continue
                        audio_array = np.frombuffer(message, dtype=np.int16)
                        response_queue.put(
                            _resample_audio(audio_array, 24000, 48000, resampler)
                        )
                    else:
                        try:
                            event = json.loads(message)
                        except json.JSONDecodeError:
                            continue
                        event_type = event.get("type", "")
                        if event_type == "session.done":
                            logger.debug(
                                "[vLLM-Omni TTS] session.done: total_sentences=%s",
                                event.get("total_sentences", "?"),
                            )
                            # 修复 PR #1764 review #3：标记会话结束 + 清重采样器
                            # 主循环在下次 input.text 前会重建连接并重发 session.config
                            session_state["active"] = False
                            session_state["awaiting_done"] = False
                            session_state["speech_id"] = None
                            try:
                                resampler.clear()
                            except Exception:
                                pass
                        elif event_type == "audio.start":
                            logger.debug(
                                "[vLLM-Omni TTS] audio.start: idx=%s text=%s",
                                event.get("sentence_index"),
                                event.get("sentence_text", "")[:40],
                            )
                        elif event_type == "audio.done":
                            pass  # 静默
                        elif event_type == "error":
                            _enqueue_error(response_queue, event)
                            # 修复 PR #1764 review 第六轮：服务端 error 事件后会话已不可用，
                            # 标记 session 失效，主循环下次 input 前会主动重建（与 session.done 处理对齐）
                            session_state["active"] = False
                            session_state["awaiting_done"] = False
                            session_state["speech_id"] = None
                            response_queue.put(("__ready__", False))
            except websockets.exceptions.ConnectionClosed:
                was_awaiting_done = bool(session_state.get("awaiting_done"))
                # 修复 PR #1764 review 第六轮：WS 关闭后必须同步本地状态，
                # 否则主循环会试图往已死连接发送，依赖 send 异常才触发重建（噪声+延迟）
                session_state["active"] = False
                session_state["awaiting_done"] = False
                session_state["speech_id"] = None
                if was_awaiting_done:
                    _enqueue_error(response_queue, {
                        "code": "TTS_CONNECTION_FAILED",
                        "provider": "vllm_omni",
                        "message": "vLLM-Omni TTS 连接在 session.done 前关闭",
                    })
                    response_queue.put(("__ready__", False))
            except Exception as e:
                was_awaiting_done = bool(session_state.get("awaiting_done"))
                logger.error(f"[vLLM-Omni TTS] 接收异常: {e}")
                session_state["active"] = False
                session_state["awaiting_done"] = False
                session_state["speech_id"] = None
                if was_awaiting_done:
                    _enqueue_error(response_queue, {
                        "code": "TTS_CONNECTION_FAILED",
                        "provider": "vllm_omni",
                        "message": "vLLM-Omni TTS 接收异常，session.done 未完成",
                    })
                    response_queue.put(("__ready__", False))

        # 首次连接 + 就绪信号
        if not await _connect_and_config():
            _enqueue_error(response_queue, {
                "code": "TTS_CONNECTION_FAILED",
                "provider": "vllm_omni",
                "message": "vLLM-Omni TTS 初始连接失败",
            })
            response_queue.put(("__ready__", False))
            return

        session_state["active"] = True  # 修复 PR #1764 review #3
        receive_task = asyncio.create_task(_receive_loop())
        response_queue.put(("__ready__", True))
        logger.info("[vLLM-Omni TTS] 已就绪")

        async def _rebuild_session() -> bool:
            """PR #1764 review #3 helper: tear down the old session and rebuild a new one.

            Called after session.done / on ws.send failure / on __interrupt__.
            Returns True on success, False on failure (outer loop should stop).
            """
            nonlocal ws, receive_task
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            receive_task = None
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
                ws = None
            try:
                resampler.clear()
            except Exception:
                pass
            if not await _connect_and_config():
                session_state["active"] = False
                session_state["awaiting_done"] = False
                session_state["speech_id"] = None
                return False
            session_state["active"] = True
            session_state["awaiting_done"] = False
            session_state["speech_id"] = None
            receive_task = asyncio.create_task(_receive_loop())
            return True

        async def _replay_pending_text() -> bool:
            if not pending_text:
                return True
            try:
                replay_text = "".join(pending_text)
                await ws.send(json.dumps({
                    "type": "input.text",
                    "text": replay_text,
                }))
                session_state["speech_id"] = pending_text_sid
                session_state["awaiting_done"] = False
                return True
            except Exception as e:
                logger.error(f"[vLLM-Omni TTS] 重放 pending_text 失败: {e}")
                session_state["active"] = False
                return False

        def _fail_pending_flush(message: str):
            nonlocal pending_text_sid
            pending_text.clear()
            pending_text_sid = None
            session_state["active"] = False
            session_state["awaiting_done"] = False
            session_state["speech_id"] = None
            _enqueue_error(response_queue, {
                "code": "TTS_CONNECTION_FAILED",
                "provider": "vllm_omni",
                "message": message,
            })
            response_queue.put(("__ready__", False))

        loop = asyncio.get_running_loop()

        while True:
            try:
                sid, tts_text = await loop.run_in_executor(None, request_queue.get)
            except Exception:
                break

            if sid == TTS_SHUTDOWN_SENTINEL:
                break

            if sid == "__interrupt__":
                # 修复 PR #1764 review 第二轮 #3：打断时只销毁当前连接、把 session 标记失效，
                # 不立刻重连——避免上游短暂不可用时一次失败就把整个 worker 退出。
                # 实际重连延迟到下一条输入到来时由活跃性检查（while 循环下方）处理。
                if receive_task is not None and not receive_task.done():
                    receive_task.cancel()
                    try:
                        await receive_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                receive_task = None
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = None
                try:
                    resampler.clear()
                except Exception:
                    pass
                session_state["active"] = False
                session_state["awaiting_done"] = False
                pending_text.clear()
                pending_text_sid = None
                continue

            if sid is None:
                if not pending_text:
                    continue
                if not session_state["active"] or ws is None:
                    logger.info("[vLLM-Omni TTS] 会话已结束/失效，重建连接以发送 flush")
                    if not await _rebuild_session():
                        logger.error("[vLLM-Omni TTS] 重建会话失败，标记 worker 未就绪")
                        _fail_pending_flush("vLLM-Omni TTS flush 重连失败")
                        break
                    if not await _replay_pending_text():
                        _fail_pending_flush("vLLM-Omni TTS flush 重放失败")
                        break
                if ws is not None:
                    try:
                        await ws.send(json.dumps({"type": "input.done"}))
                        pending_text.clear()
                        pending_text_sid = None
                        session_state["awaiting_done"] = True
                    except Exception as e:
                        logger.warning(f"[vLLM-Omni TTS] 发送 input.done 失败: {e}")
                        session_state["active"] = False
                        session_state["awaiting_done"] = False
                        if not await _rebuild_session():
                            _fail_pending_flush("vLLM-Omni TTS flush 重连失败")
                            break
                        try:
                            if not await _replay_pending_text():
                                _fail_pending_flush("vLLM-Omni TTS flush 重放失败")
                                break
                            await ws.send(json.dumps({"type": "input.done"}))
                            pending_text.clear()
                            pending_text_sid = None
                            session_state["awaiting_done"] = True
                            logger.info("[vLLM-Omni TTS] 重放 pending_text 并重发 input.done 成功")
                        except Exception as e2:
                            logger.warning(f"[vLLM-Omni TTS] 重发 input.done 仍失败: {e2}")
                            _fail_pending_flush("vLLM-Omni TTS flush 重发失败")
                            break
                else:
                    _fail_pending_flush("vLLM-Omni TTS flush 连接不可用")
                    break
                continue

            # 修复 PR #1764 review #3：发送前检查会话是否仍然可复用
            # active session 只能承载同一个 utterance；sid 切换时先重建，避免串音
            if (
                session_state["active"]
                and session_state.get("speech_id") not in (None, sid)
            ):
                logger.info(
                    "[vLLM-Omni TTS] 收到新 sid=%s，重建会话避免跨 utterance 复用",
                    sid,
                )
                pending_text.clear()
                pending_text_sid = None
                session_state["active"] = False
            if not session_state["active"] or ws is None:
                logger.info("[vLLM-Omni TTS] 会话已结束/失效，重建连接以发送新输入")
                if not await _rebuild_session():
                    logger.error("[vLLM-Omni TTS] 重建会话失败，标记 worker 未就绪")
                    _enqueue_error(response_queue, {
                        "code": "TTS_CONNECTION_FAILED",
                        "provider": "vllm_omni",
                        "message": "vLLM-Omni TTS 重连失败",
                    })
                    response_queue.put(("__ready__", False))
                    break
                if pending_text and pending_text_sid == sid:
                    if not await _replay_pending_text():
                        _fail_pending_flush("vLLM-Omni TTS input.text 重连后重放失败")
                        break

            if tts_text and tts_text.strip() and ws is not None:
                if pending_text and pending_text_sid not in (None, sid):
                    logger.debug(
                        "[vLLM-Omni TTS] 丢弃跨 utterance 的 pending_text (sid=%s, current=%s, len=%d)",
                        pending_text_sid,
                        sid,
                        sum(len(part) for part in pending_text),
                    )
                    pending_text.clear()
                    pending_text_sid = None
                payload = json.dumps({
                    "type": "input.text",
                    "text": tts_text,
                })
                try:
                    await ws.send(payload)
                    _record_tts_telemetry(effective_model, len(tts_text))
                    pending_text.append(tts_text)
                    pending_text_sid = sid
                    session_state["speech_id"] = sid
                    session_state["awaiting_done"] = False
                except Exception as e:
                    logger.error(f"[vLLM-Omni TTS] 发送 input.text 失败: {e}，尝试重建并重发")
                    session_state["active"] = False
                    session_state["awaiting_done"] = False
                    if await _rebuild_session():
                        try:
                            if not await _replay_pending_text():
                                _fail_pending_flush("vLLM-Omni TTS input.text 重放失败")
                                break
                            await ws.send(payload)
                            _record_tts_telemetry(effective_model, len(tts_text))
                            pending_text.append(tts_text)
                            pending_text_sid = sid
                            session_state["speech_id"] = sid
                            session_state["awaiting_done"] = False
                            logger.info("[vLLM-Omni TTS] 重发 input.text 成功")
                        except Exception as e2:
                            logger.error(f"[vLLM-Omni TTS] 重发 input.text 仍失败: {e2}，标记 worker 未就绪")
                            session_state["active"] = False
                            _enqueue_error(response_queue, {
                                "code": "TTS_CONNECTION_FAILED",
                                "provider": "vllm_omni",
                                "message": "vLLM-Omni TTS 发送失败",
                            })
                            response_queue.put(("__ready__", False))
                            break
                    else:
                        _enqueue_error(response_queue, {
                            "code": "TTS_CONNECTION_FAILED",
                            "provider": "vllm_omni",
                            "message": "vLLM-Omni TTS 重连失败",
                        })
                        response_queue.put(("__ready__", False))
                        break

        # 清理
        if receive_task and not receive_task.done():
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"[vLLM-Omni TTS] Worker 启动失败: {e}")
        response_queue.put(("__ready__", False))

def _vllm_omni_is_selected(ctx) -> bool:
    core_config, cm = ctx.core_config, ctx.cm
    if not _as_bool(core_config.get('ENABLE_CUSTOM_API'), False):
        return False
    try:
        raw = cm.load_json_config('core_config.json', {})
    except Exception:
        return False
    return (raw.get('ttsModelProvider') or '').strip() == 'vllm_omni'

def _vllm_omni_resolve(ctx):
    cm = ctx.cm
    try:
        raw = cm.load_json_config('core_config.json', {})
    except Exception:
        raw = {}
    vllm_url = (raw.get('ttsModelUrl') or '').strip() or VLLM_OMNI_DEFAULT_BASE_URL
    vllm_model = (raw.get('ttsModelId') or '').strip() or VLLM_OMNI_DEFAULT_MODEL
    vllm_voice = (raw.get('ttsVoiceId') or '').strip() or 'default'
    # 凭证防泄漏：无 key 时返回空字符串而非 None，配合 core.resolve_tts_api_key
    # 的 provider_key=='vllm_omni' 特判，禁止 fallback 到别家 provider 的 key
    # （见 get_tts_worker 原注释 / PR #1764 review 第三轮 #3）。
    vllm_key = (raw.get('ttsModelApiKey') or '')
    worker = partial(
        vllm_omni_tts_worker,
        base_url=vllm_url, model=vllm_model, voice=vllm_voice,
    )
    return worker, vllm_key, 'vllm_omni'
