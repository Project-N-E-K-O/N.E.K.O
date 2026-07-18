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

"""Aliyun CosyVoice (hosted) TTS worker."""

import time

from utils.config_manager import get_config_manager
from utils.dashscope_region import DASHSCOPE_GLOBAL_LOCK, configure_dashscope_sdk_urls

from .._infra import TTS_SHUTDOWN_SENTINEL, _enqueue_error
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


def _get_enrolled_model(voice_meta):
    if not voice_meta:
        return None
    return voice_meta.get('design_model') or voice_meta.get('clone_model')


def cosyvoice_vc_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    TTS multiprocess worker function for Aliyun CosyVoice TTS
    
    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue sending audio data (also used for the ready signal)
        audio_api_key: API key
        voice_id: voice ID
    """
    import dashscope
    from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer, AudioFormat
    from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS
    # _get_voice_meta 住在包 __init__（与 get_tts_worker 共享、可被
    # monkeypatch tts_client._get_voice_meta 命中）；这里惰性导入避免 worker 模块
    # 在 __init__ 导入它时形成循环导入。
    from main_logic.tts_client import _get_voice_meta

    # 从 voice 元数据中读取注册时使用的模型和地域 URL，缺失时回退到全局配置
    _voice_meta = _get_voice_meta(voice_id)
    _enrolled_model = _get_enrolled_model(_voice_meta)
    _voice_provider = _voice_meta.get('provider') if _voice_meta else None

    # dashscope.api_key 和 dashscope.base_*_api_url 是模块级全局状态，同一进程内
    # /voice_preview 端点 (characters_router.py) 和声音克隆 (utils/voice_clone.py)
    # 也会改写它们。worker 只在启动时设一次，下次 _create_synthesizer 重连时会
    # 继承到别人最后一次设置的地域/key，混用国内+国际场景下会出现"voice 没换
    # 但请求打到错地域"的 401。地域 URL 先在启动时算好捕获到闭包里，每次
    # _create_synthesizer 重新写一遍 module-global。
    try:
        _tts_api_config = get_config_manager().get_model_api_config('tts_custom')
        _dashscope_base_url = (_voice_meta or {}).get('dashscope_base_url') or _tts_api_config.get('base_url', '')
    except Exception as e:
        logger.warning("DashScope TTS 地域 URL 读取失败，回退到默认地域: %s", e, exc_info=True)
        _dashscope_base_url = ""

    def _apply_dashscope_region():
        """Called before every SpeechSynthesizer rebuild (must be inside DASHSCOPE_GLOBAL_LOCK),
        ensuring the module-global is the worker's own region/key.
        """
        dashscope.api_key = audio_api_key
        try:
            configure_dashscope_sdk_urls(dashscope, _dashscope_base_url, websocket_path="inference")
        except Exception as e:
            logger.warning("DashScope TTS 地域 URL 配置失败，已重置为默认地域: %s", e, exc_info=True)
            try:
                configure_dashscope_sdk_urls(dashscope, "", websocket_path="inference")
            except Exception as reset_error:
                logger.error("DashScope TTS 默认地域重置失败: %s", reset_error, exc_info=True)
                raise

    # 不在这里 eagerly 写 module-global：startup 到首次 _create_synthesizer 之间
    # 没有任何 dashscope SDK 调用读 global；_create_synthesizer 重连时会在
    # DASHSCOPE_GLOBAL_LOCK 内 _apply_dashscope_region。这里多一次 unlocked
    # 写只会和并发的 /voice_preview / clone_voice 抢同一份 global → 重新
    # 引入 Codex P1 #3258691457 已经修过的 cross-credential 错路由 race
    # (Codex P1 #3258856950)。

    # CosyVoice 不需要预连接，直接发送就绪信号
    logger.info("CosyVoice TTS 已就绪，发送就绪信号")
    response_queue.put(("__ready__", True))
    
    current_speech_id = None

    class Callback(ResultCallback):
        def __init__(self, response_queue):
            self.response_queue = response_queue
            self.connection_lost = False
            self._muted = False
            # 当前允许投递的 speech_id（由 worker 在回合边界显式设置）
            # 不能在 on_data 时动态读取 current_speech_id，否则旧流尾包可能被错标到新流。
            self.accepted_speech_id = None
            # CosyVoice 常先回很小的 OGG 头页（~200B），前端会因“数据不足”暂不解码，
            # 造成首词听感被吞。这里为每个 speech_id 做一次首包聚合后再下发。
            self._active_sid = None
            self._bootstrap_buffer = bytearray()
            self._bootstrap_sent = False
            self._bootstrap_min_bytes = 1024
            # 后续小包聚合：OGG OPUS 页常只有几百字节，高频小包
            # 会给前端主线程带来大量 WASM 解码调用，Live2D 渲染繁忙时
            # 容易导致 audio buffer underrun。聚合到 ≥4KB 再下发，
            # 减少前端处理次数、增大每段解码出的音频长度。
            self._agg_buffer = bytearray()
            self._agg_min_bytes = 4096

        def reset_bootstrap_state(self):
            self._active_sid = None
            self._bootstrap_buffer.clear()
            self._bootstrap_sent = False
            self._agg_buffer.clear()
            
        def on_open(self): 
            self.connection_lost = False
            self._muted = False
            elapsed = time.time() - self.construct_start_time if hasattr(self, 'construct_start_time') else -1
            logger.debug(f"TTS 连接已建立 (构造到open耗时: {elapsed:.2f}s)")
            
        def on_complete(self): 
            # 短句可能在首包聚合阈值前就结束，完成时强制冲刷缓冲，避免整句静音。
            # 若已静音（打断/回合切换），跳过投递，避免旧流尾包进入新回合的 response_queue。
            try:
                sid = self._active_sid
                if sid and not self._muted:
                    if self._bootstrap_buffer:
                        self.response_queue.put(("__audio__", sid, bytes(self._bootstrap_buffer)))
                    if self._agg_buffer:
                        self.response_queue.put(("__audio__", sid, bytes(self._agg_buffer)))
            finally:
                self.reset_bootstrap_state()
                
        def on_error(self, message: str):
            if "request timeout after 23 seconds" in message:
                self.connection_lost = True
                logger.debug("CosyVoice SDK 内部 WebSocket 空闲超时，标记连接已断开")
            elif "request timeout" in message:
                self.connection_lost = True
                logger.warning(f"CosyVoice 请求超时，标记连接已断开: {message}")
                self.response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
            else:
                _enqueue_error(self.response_queue, message)
            
        def on_close(self): 
            self.connection_lost = True
            
        def on_event(self, message): 
            pass
            
        def on_data(self, data: bytes) -> None:
            sid = self.accepted_speech_id
            if not sid or self._muted:
                # 回合切换窗口或未就绪时直接丢弃，避免错序串包
                return

            # speech_id 切换时重置首包聚合状态（含后续聚合缓冲，避免旧数据串入新回合）
            if sid != self._active_sid:
                self._active_sid = sid
                self._bootstrap_buffer.clear()
                self._bootstrap_sent = False
                self._agg_buffer.clear()

            if not self._bootstrap_sent:
                self._bootstrap_buffer.extend(data)
                if len(self._bootstrap_buffer) < self._bootstrap_min_bytes:
                    return
                self.response_queue.put(("__audio__", sid, bytes(self._bootstrap_buffer)))
                self._bootstrap_buffer.clear()
                self._bootstrap_sent = True
                return

            self._agg_buffer.extend(data)
            if len(self._agg_buffer) >= self._agg_min_bytes:
                self.response_queue.put(("__audio__", sid, bytes(self._agg_buffer)))
                self._agg_buffer.clear()
            
    callback = Callback(response_queue)
    synthesizer = None
    char_buffer = ""
    detected_lang = None
    last_streaming_call_time = None  # 追踪最后一次 streaming_call 的时间
    IDLE_AUTO_COMPLETE_SECONDS = 15  # 空闲超过此秒数则主动 complete（须 < 服务端 23s 超时）

    def _create_synthesizer(lang_hint=None):
        """Create a new SpeechSynthesizer, with an optional language hint.
        Only establishes the WebSocket connection without sending warmup text — the caller sends real text right after.
        """
        from utils.api_config_loader import (
            cosyvoice_model_supports_language_hints,
            get_cosyvoice_clone_model,
        )
        nonlocal last_streaming_call_time
        clone_model = _enrolled_model or get_cosyvoice_clone_model(_voice_provider)
        kwargs = dict(
            model=clone_model,
            voice=voice_id,
            speech_rate=1.05,
            format=AudioFormat.OGG_OPUS_48KHZ_MONO_64KBPS,
            callback=callback,
        )
        if lang_hint and cosyvoice_model_supports_language_hints(clone_model):
            kwargs["language_hints"] = [lang_hint]
        callback.construct_start_time = time.time()
        # 写 module-global + 构造 SpeechSynthesizer 必须握 DASHSCOPE_GLOBAL_LOCK，
        # 否则 /voice_preview / clone_voice 等同进程其它流程并发跑时会在
        # "set global → __init__" 之间互相覆盖 → 拿别人的 key/地域建连。
        # SpeechSynthesizer 一旦建好就由实例内部状态承载请求，解锁后继续跑安全。
        with DASHSCOPE_GLOBAL_LOCK:
            _apply_dashscope_region()
            syn = SpeechSynthesizer(**kwargs)
        last_streaming_call_time = time.time()
        return syn

    def _flush_buffer():
        """Detect the language, create the synthesizer (if needed) and flush the buffer"""
        nonlocal synthesizer, char_buffer, detected_lang, last_streaming_call_time
        if not char_buffer.strip():
            char_buffer = ""
            return
        hint = detect_tts_language_hint(char_buffer)
        if hint and detected_lang != hint:
            detected_lang = hint
            logger.info(f"CosyVoice 检测到 {hint} 语言提示")
        if synthesizer is None:
            synthesizer = _create_synthesizer(detected_lang)
            callback.accepted_speech_id = current_speech_id
        synthesizer.streaming_call(char_buffer)
        _record_tts_telemetry("cosyvoice", len(char_buffer))
        last_streaming_call_time = time.time()
        char_buffer = ""

    def _do_streaming_complete():
        """Non-blockingly notify the server that all text has been sent.
        Only sends the FINISHED signal without waiting for server confirmation. Audio keeps streaming to the frontend via the on_data callback.
        The synthesizer stays open and is closed at the next speech_id switch.
        """
        nonlocal synthesizer, last_streaming_call_time
        if synthesizer is None:
            callback.accepted_speech_id = None
            callback.reset_bootstrap_state()
            return
        if callback.connection_lost:
            logger.info("CosyVoice WebSocket 已断开，跳过 streaming_complete")
            try:
                synthesizer.close()
            except Exception:
                pass
            synthesizer = None
            last_streaming_call_time = None
            return

        try:
            synthesizer.ws.send(synthesizer.request.getFinishRequest())
        except Exception as e:
            logger.warning(f"发送TTS完成信号失败: {e}")
        last_streaming_call_time = None
        # 这里不能立刻清 accepted_speech_id/bootstrap。
        # FINISH 发出后，服务端仍可能继续回传尾包；应由 on_complete 或后续中断/切换来收口状态。

    while True:
        # 非阻塞检查队列，优先处理打断
        if request_queue.empty():
            # 主动完成：合成器空闲超过阈值，趁 WebSocket 还活着主动 complete
            # 避免等到 (None,None) 到达时 WebSocket 已被服务端回收（23s 超时）
            if (synthesizer is not None
                    and last_streaming_call_time is not None
                    and time.time() - last_streaming_call_time > IDLE_AUTO_COMPLETE_SECONDS):
                logger.debug(f"CosyVoice 空闲 >{IDLE_AUTO_COMPLETE_SECONDS}s，主动 streaming_complete")
                _do_streaming_complete()
            time.sleep(0.01)
            continue

        sid, tts_text = request_queue.get()

        if sid == TTS_SHUTDOWN_SENTINEL:
            break

        if sid == "__interrupt__":
            # 打断：立即静音回调 → 关闭 synthesizer → 清理状态
            # 先 mute 再 close，确保旧 SDK websocket 线程不再往 response_queue 灌数据
            callback._muted = True
            if synthesizer is not None:
                try:
                    synthesizer.close()
                except Exception:
                    pass
            synthesizer = None
            last_streaming_call_time = None
            current_speech_id = None
            char_buffer = ""
            detected_lang = None
            callback.connection_lost = False
            callback.accepted_speech_id = None
            callback.reset_bootstrap_state()
            continue

        if sid is None:
            # 正常结束 - 告诉TTS没有更多文本了（非阻塞）
            try:
                _flush_buffer()
            except Exception as e:
                logger.warning(f"TTS flush buffer 失败: {e}")
            _do_streaming_complete()
            # 不清 current_speech_id / synthesizer：
            # 音频继续流到前端，由下次 speech_id 切换时打断
            char_buffer = ""
            detected_lang = None
            continue

        if current_speech_id is None:
            current_speech_id = sid
            callback.accepted_speech_id = sid
        elif current_speech_id != sid:
            # 先屏蔽回调，避免旧流尾包误标到新回合
            callback.accepted_speech_id = None
            callback._muted = True
            if synthesizer is not None:
                try:
                    synthesizer.close()
                except Exception:
                    pass
            synthesizer = None
            last_streaming_call_time = None
            current_speech_id = sid
            char_buffer = ""
            detected_lang = None
            # 显式清理聚合缓冲：close() 会触发 on_complete→reset_bootstrap_state，
            # 但若 SDK 线程延迟触发 on_complete，新 synthesizer 的 on_open 可能先执行
            # 导致 _agg_buffer 带着旧数据进入新回合。此处提前清理消除该竞态。
            callback.reset_bootstrap_state()
            callback.accepted_speech_id = sid
            
        if tts_text is None or not tts_text.strip():
            time.sleep(0.01)
            continue

        # 尚未创建 synthesizer 时先缓冲，等够 TTS_LANG_DETECT_MIN_CHARS 个字符再一起发送
        if synthesizer is None:
            char_buffer += tts_text
            hint = detect_tts_language_hint(tts_text)
            if hint and detected_lang != hint:
                detected_lang = hint
            if len(char_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                continue
            try:
                if detected_lang:
                    logger.info(f"CosyVoice 语言提示: {detected_lang}")
                synthesizer = _create_synthesizer(detected_lang)
                callback.accepted_speech_id = current_speech_id
                synthesizer.streaming_call(char_buffer)
                _record_tts_telemetry("cosyvoice", len(char_buffer))
                last_streaming_call_time = time.time()
                char_buffer = ""
            except Exception as e:
                logger.error(f"TTS Init Error: {e}")
                synthesizer = None
                current_speech_id = None
                char_buffer = ""
                detected_lang = None
                last_streaming_call_time = None
                callback.accepted_speech_id = None
                callback.reset_bootstrap_state()
                time.sleep(0.1)
                continue
        else:
            try:
                synthesizer.streaming_call(tts_text)
                last_streaming_call_time = time.time()
            except Exception:
                if synthesizer is not None:
                    try:
                        synthesizer.close()
                    except Exception:
                        pass
                    synthesizer = None
                    last_streaming_call_time = None

                try:
                    synthesizer = _create_synthesizer(detected_lang)
                    callback.accepted_speech_id = current_speech_id
                    synthesizer.streaming_call(tts_text)
                    last_streaming_call_time = time.time()
                except Exception as reconnect_error:
                    logger.error(f"TTS Reconnect Error: {reconnect_error}")
                    response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                    time.sleep(1.0)
                    synthesizer = None
                    current_speech_id = None
                    last_streaming_call_time = None
                    callback.accepted_speech_id = None
                    callback.reset_bootstrap_state()

    # 收到 TTS_SHUTDOWN_SENTINEL 退出循环后：静音回调并关闭 synthesizer，
    # 避免 SDK 内部 WebSocket 线程继续往 response_queue 写数据。
    callback._muted = True
    if synthesizer is not None:
        try:
            synthesizer.close()
        except Exception:
            # best-effort：关闭路径不 raise，与文件内其他 synthesizer.close()
            # 块保持一致（L1644 / 1683 / 1718 / 1770）。SDK WS 在关闭时通常
            # 已被服务端回收，异常既常见又不可恢复，log 只会增噪。
            pass
        synthesizer = None

def _cosyvoice_clone_is_selected(ctx) -> bool:
    vm = ctx.voice_meta
    return bool(vm and vm.get('provider') in ('cosyvoice', 'cosyvoice_intl'))

def _cosyvoice_clone_resolve(ctx):
    vm = ctx.voice_meta or {}
    provider = vm.get('provider') or 'cosyvoice'
    runtime = ctx.cm.get_cosyvoice_clone_runtime(provider)
    runtime_key = (runtime.get('api_key') or '').strip()
    # provider=='cosyvoice_intl' 必须用 intl key 调 intl 端点。runtime_key 缺失时若只返回
    # None，core.py 会用 `api_key_override or tts_config['api_key']` 兜底到 tts_custom 槽位的
    # 国内 key，结果拿国内 key 打 intl 端点，每次 utterance 吃一次 401 — 比 dummy 静音更难查。
    if provider == 'cosyvoice_intl' and not runtime_key:
        logger.warning(
            "阿里国际版 CosyVoice 克隆音色 %s 选中，但 intl key 缺失，"
            "改用 dummy TTS worker 避免用错凭证打 intl 端点", ctx.voice_id)
        return dummy_tts_worker, None, None
    logger.info("检测到阿里 CosyVoice 克隆音色: %s (provider=%s)，使用 CosyVoice TTS Worker",
                ctx.voice_id, provider)
    return cosyvoice_vc_tts_worker, (runtime_key or None), 'cosyvoice'
