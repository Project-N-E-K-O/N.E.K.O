# -*- coding: utf-8 -*-
"""独立的"一句话 → 48kHz PCM16"合成器。

复用 ``main_logic.tts_client.get_tts_worker`` 选出的 provider worker，但不
依赖 ``core.py`` 里那套与会话状态深度耦合的 ``tts_response_handler``。供
``app/controler.py`` 把剧本台词合成成音频流推给 monitor 的 ``/sync_binary``。

worker 与 core 中一致：``Thread(target=worker, args=(req_q, resp_q, api_key, voice_id))``。
worker 输出的音频已是 48kHz PCM16（各 worker 内部 soxr 重采样），格式有两种：
``("__audio__", speech_id, bytes)`` 或裸 ``bytes``，两者都接。
"""

import asyncio
import logging
import uuid
from queue import Queue, Empty
from threading import Thread

from utils.config_manager import get_config_manager, get_reserved
from utils.native_voice_registry import (
    is_free_preset_voice_id,
    resolve_native_voice_for_routing,
)
from main_logic.tts_client import get_tts_worker

logger = logging.getLogger("Controler.TTS")

# worker 退出哨兵（与 core._teardown_tts_runtime 一致）
_TTS_SHUTDOWN = ("__shutdown__", None)
# 一段 utterance 结束、flush 缓冲（与 core 一致：(None, None) 不退出 worker）
_TTS_FLUSH = (None, None)


def _extract_audio(item):
    """从 response_queue 取出的元素里抽出音频 bytes；非音频返回 None。"""
    if isinstance(item, (bytes, bytearray)):
        return bytes(item)
    if isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__":
        payload = item[2]
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
    return None


async def _resolve_tts_runtime(lanlan_name):
    """解析 worker / api_key / voice_id。返回 (worker, api_key, voice_id) 或 None（TTS 不可用）。"""
    cm = get_config_manager()
    core_config = await cm.aget_core_config()
    if core_config.get('DISABLE_TTS', False):
        logger.info("TTS 已被禁用，台词将仅以文字呈现")
        return None

    realtime_config = cm.get_model_api_config('realtime')
    core_api_type = realtime_config.get('api_type', '') or core_config.get('CORE_API_TYPE', '')

    # 取角色卡里的 voice_id
    _, her_name, _, lanlan_basic_config, _, _, _, _, _ = await cm.aget_character_data()
    target = lanlan_name if (lanlan_name and lanlan_name in (lanlan_basic_config or {})) else her_name
    voice_id = (get_reserved(
        (lanlan_basic_config or {}).get(target, {}),
        'voice_id', default='', legacy_keys=('voice_id',),
    ) or '').strip()
    is_free_preset = is_free_preset_voice_id(voice_id)
    if is_free_preset and core_api_type != 'free':
        voice_id = ''
        is_free_preset = False

    # has_custom 判定（与 core._has_custom_tts 等价）
    _, uses_native = resolve_native_voice_for_routing(
        core_api_type, voice_id, cm.voice_id_exists_in_any_storage,
    )
    if uses_native:
        has_custom = False
    elif voice_id and not is_free_preset:
        has_custom = True
    else:
        has_custom = bool(
            core_config.get('ENABLE_CUSTOM_API')
            and core_config.get('TTS_MODEL_URL')
            and core_config.get('GPTSOVITS_ENABLED')
        )

    worker, api_key_override, _provider_key = get_tts_worker(
        core_api_type=core_api_type, has_custom_voice=has_custom, voice_id=voice_id,
    )
    tts_config = cm.get_model_api_config('tts_custom' if has_custom else 'tts_default')
    api_key = api_key_override or tts_config.get('api_key', '')
    return worker, api_key, voice_id


async def synthesize_line(text, lanlan_name=None, *, ready_timeout=12.0,
                          first_chunk_timeout=15.0, idle_timeout=2.5):
    """把一句台词合成为 48kHz PCM16 音频块，逐块异步产出。

    TTS 不可用（禁用 / 配置缺失 / worker 失败）时静默产出 0 块——调用方据此
    退化为纯文字。
    """
    if not text or not text.strip():
        return

    try:
        runtime = await _resolve_tts_runtime(lanlan_name)
    except Exception as e:
        logger.warning(f"解析 TTS runtime 失败，台词退化为纯文字: {e}")
        return
    if runtime is None:
        return
    worker, api_key, voice_id = runtime

    req_q: Queue = Queue()
    resp_q: Queue = Queue()
    thread = Thread(target=worker, args=(req_q, resp_q, api_key, voice_id), daemon=True)
    thread.start()

    try:
        # 等待 worker 就绪（吸收非就绪信号）
        ready = False
        loop_deadline = asyncio.get_event_loop().time() + ready_timeout
        while asyncio.get_event_loop().time() < loop_deadline:
            remaining = loop_deadline - asyncio.get_event_loop().time()
            try:
                msg = await asyncio.to_thread(resp_q.get, True, min(remaining, 2.0))
            except Empty:
                if not thread.is_alive():
                    break
                continue
            if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "__ready__":
                ready = bool(msg[1])
                break
            if isinstance(msg, tuple) and len(msg) >= 1 and msg[0] == "__error__":
                logger.warning(f"TTS worker 启动错误，台词退化为纯文字: {msg[1] if len(msg) > 1 else ''}")
                return
            # 其它早到消息（极少）忽略
        if not ready:
            logger.warning("TTS worker 未就绪，台词退化为纯文字")
            return

        # 投递台词 + flush
        speech_id = uuid.uuid4().hex
        req_q.put((speech_id, text))
        req_q.put(_TTS_FLUSH)

        got_audio = False
        while True:
            window = idle_timeout if got_audio else first_chunk_timeout
            try:
                item = await asyncio.to_thread(resp_q.get, True, window)
            except Empty:
                # 首块前超时 = 合成失败；有音频后超时 = 本句结束
                break
            audio = _extract_audio(item)
            if audio is not None and len(audio) > 0:
                got_audio = True
                yield audio
                continue
            # 错误信号：终止本句
            if isinstance(item, tuple) and len(item) >= 1 and item[0] == "__error__":
                logger.warning(f"TTS 合成中途出错，截断本句: {item[1] if len(item) > 1 else ''}")
                break
    finally:
        try:
            req_q.put(_TTS_SHUTDOWN)
        except Exception:
            pass
