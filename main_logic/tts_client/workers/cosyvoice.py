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

import threading
import time

from utils.config_manager import get_config_manager
from utils.dashscope_region import (
    DASHSCOPE_GLOBAL_LOCK,
    configure_dashscope_sdk_urls,
    prefer_dashscope_websocket_ipv4,
)

from .._infra import (
    AudioDoneEmitter,
    TTS_SHUTDOWN_SENTINEL,
    TTS_SOFT_FLUSH_SENTINEL,
    _enqueue_error,
)
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
        def __init__(self, response_queue, audio_done):
            self.response_queue = response_queue
            self.audio_done = audio_done
            self.connection_lost = False
            self._muted = False
            # 只有主循环已经对这一轮发过 FINISH，on_complete 才代表"音频真的产完了"。
            # 重连路径也会 close 旧 synthesizer 触发 on_complete，那时本轮还没结束，
            # 此刻发 audio_done 就是早发。
            self.finish_requested_speech_id = None
            # 最新一代 synthesizer 的代号，由 _SynthCallback 构造时盖章。
            # SDK 的 on_complete 不带任何 synthesizer 标识，而这个 callback 跨轮次
            # 和重连共享：连切两轮时，旧 synthesizer 迟到的完成通知会读到新一轮的
            # _active_sid 和 FINISH 标记，把还在说话的那一轮判成放完（早发），
            # 顺带还把它的聚合缓冲清掉。迟到的旧回调据此认出自己已经过期。
            self.current_generation = None
            # 已经收到完成通知的那一代。软 flush 之后主循环靠它判断旧流是否已经
            # 放干净：干净了才敢关掉旧 synthesizer 接着说 / 直接补发 audio_done。
            self.completed_generation = None
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
            # 这个对象被 SDK 的接收线程（on_data / on_complete）和 worker 主循环
            # （退役旧流、回合边界 reset）同时改。generation 戳只能挡住「退役之后
            # 才进来」的回调；一条已经过了戳、被调度出去的回调醒来后照样会
            # 冲刷、清掉此刻已属于新流的缓冲和 FINISH 标记。所以改共享状态的路径
            # 都持这把锁：退役要等在飞的回调跑完，回调进来时再看一眼当代。
            # 可重入：on_complete 的 finally 里要调 reset_bootstrap_state。
            self._lock = threading.RLock()

        def reset_bootstrap_state(self):
            with self._lock:
                self._active_sid = None
                self._bootstrap_buffer.clear()
                self._bootstrap_sent = False
                self._agg_buffer.clear()
                self.finish_requested_speech_id = None

        def on_open(self):
            with self._lock:
                self.connection_lost = False
                self._muted = False
            elapsed = time.time() - self.construct_start_time if hasattr(self, 'construct_start_time') else -1
            logger.debug(f"TTS 连接已建立 (构造到open耗时: {elapsed:.2f}s)")
            
        def on_complete(self, generation=None):
            with self._lock:
                self._on_complete_locked(generation)

        def _on_complete_locked(self, generation):
            # 过期的 synthesizer（连切两轮 / 轮内重连时被 close 掉的那个）迟到的完成
            # 通知：它描述的是上一代的流，而这个 callback 的状态早已换成当前轮的。
            # 整个早退——不冲刷（缓冲里是别人的数据）、不发信号（会把还在说话的这轮
            # 判成放完）、也不 reset（那会把当前轮的聚合缓冲和 FINISH 标记一起抹掉，
            # 让本轮真正的收尾无从判断）。
            if generation is not None and generation != self.current_generation:
                logger.debug("CosyVoice 忽略过期 synthesizer 的 on_complete (gen=%s, 当前 gen=%s)",
                             generation, self.current_generation)
                return
            # 短句可能在首包聚合阈值前就结束，完成时强制冲刷缓冲，避免整句静音。
            # 若已静音（打断/回合切换），跳过投递，避免旧流尾包进入新回合的 response_queue。
            try:
                sid = self._active_sid
                if sid and not self._muted:
                    if self._bootstrap_buffer:
                        self.response_queue.put(("__audio__", sid, bytes(self._bootstrap_buffer)))
                    if self._agg_buffer:
                        self.response_queue.put(("__audio__", sid, bytes(self._agg_buffer)))
                    # 本轮发过 FINISH（且早退已经保证打来这通回调的就是当代
                    # synthesizer；每条重建路径都会清掉 FINISH 标记）。
                    if sid == self.finish_requested_speech_id:
                        # 尾包已经投进队列，本轮音频流到此关闭
                        self.audio_done.emit(sid)
            finally:
                self.reset_bootstrap_state()
                # 「这一代放干净」必须在共享状态归零之后才发布：主循环一读到它就会
                # 退役旧流、起续接流，若发布得早，上面这个 reset 就会落在新流身上，
                # 把它刚攒的缓冲和 round-end 的 FINISH 标记一起抹掉。尾包都已投进
                # 队列，主循环之后放的任何东西（补发的 audio_done、下一段的音频）
                # 仍然排在尾包后面。
                self.completed_generation = generation

        def on_error(self, message: str, generation=None):
            # 代际检查和它的副作用（标断开）必须在同一临界区：过了检查再被调度
            # 出去、退役后醒来写 connection_lost，写的就是新流的状态。
            with self._lock:
                self._on_error_locked(message, generation)

        def _on_error_locked(self, message: str, generation):
            # 旧 synthesizer（软 flush 后已放干净、或重建时被换掉的那个）的报错
            # 描述的是别人的连接：既不能把当代标成断开，也不该当成本轮出错上报。
            if generation is not None and generation != self.current_generation:
                logger.debug("CosyVoice 忽略过期 synthesizer 的 on_error (gen=%s, 当前 gen=%s)",
                             generation, self.current_generation)
                return
            if "request timeout after 23 seconds" in message:
                self.connection_lost = True
                logger.debug("CosyVoice SDK 内部 WebSocket 空闲超时，标记连接已断开")
            elif "request timeout" in message:
                self.connection_lost = True
                logger.warning(f"CosyVoice 请求超时，标记连接已断开: {message}")
                self.response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
            else:
                _enqueue_error(self.response_queue, message)
            
        def on_close(self, generation=None):
            # 只有当代连接的关闭才算「断开」。软 flush 之后服务端会在放完尾包后
            # 关掉旧连接，那时新 synthesizer 可能已经在说下一段——把它标成断开，
            # 下一次收尾就会跳过 FINISH 并丢掉 synthesizer，新一段的尾句直接消失。
            with self._lock:
                if generation is not None and generation != self.current_generation:
                    return
                self.connection_lost = True

        def on_event(self, message):
            pass
            
        def on_data(self, data: bytes, generation=None) -> None:
            with self._lock:
                self._on_data_locked(data, generation)

        def _on_data_locked(self, data: bytes, generation) -> None:
            # 过期 synthesizer（软 flush 排空超时后被退役、或重建时被换掉的那个）
            # 迟到的页：同一 speech_id 的新流正在用同一份聚合缓冲，混进去就是
            # 两条 OGG 流交错成坏音频。
            if generation is not None and generation != self.current_generation:
                return
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
            
    class _SynthCallback(ResultCallback):
        """Per-synthesizer view over the shared Callback.

        The DashScope SDK hands back no reference to the synthesizer that fired,
        and one Callback instance is shared across turns and reconnects. Stamping
        a generation at construction time is what lets on_complete tell "my
        synthesizer finished" apart from "a previous synthesizer's completion
        arrived late" -- the latter reads as the current turn finishing early,
        which is the defect the audio-done signal exists to remove. Everything
        else delegates untouched so the shared buffering/mute semantics stay put.
        """

        def __init__(self, inner, generation):
            self._inner = inner
            self._generation = generation
            # 建出来的这一代就是最新的一代
            inner.current_generation = generation

        def on_open(self):
            self._inner.on_open()

        def on_complete(self):
            self._inner.on_complete(generation=self._generation)

        def on_error(self, message: str):
            self._inner.on_error(message, generation=self._generation)

        def on_close(self):
            self._inner.on_close(generation=self._generation)

        def on_event(self, message):
            self._inner.on_event(message)

        def on_data(self, data: bytes) -> None:
            self._inner.on_data(data, generation=self._generation)

    audio_done = AudioDoneEmitter(response_queue)
    callback = Callback(response_queue, audio_done)
    synth_generation = 0
    synthesizer = None
    char_buffer = ""
    detected_lang = None
    last_streaming_call_time = None  # 追踪最后一次 streaming_call 的时间
    IDLE_AUTO_COMPLETE_SECONDS = 15  # 空闲超过此秒数则主动 complete（须 < 服务端 23s 超时）
    # 软 flush（core 的空闲哨兵 / 上面的空闲保活）发过 FINISH 之后，旧 synthesizer
    # 还在往回吐尾包。同一轮再来文本时不能立刻在旁边开新流：两条 OGG 流的页会在
    # 共享缓冲里交错成坏音频，所以先攒在 char_buffer 里等旧流的完成通知，最多等
    # 这么久（DashScope FINISH→complete 通常 <1s）。
    SOFT_FLUSH_DRAIN_TIMEOUT_SECONDS = 2.5
    # 当前 synthesizer 已经因软 flush 发过 FINISH（本轮没结束、还可能来文本）。
    soft_finished = False
    soft_finish_deadline = None
    # 软 flush 期间收到 (None, None)：等旧流放干净再决定是补发 audio_done 还是
    # 起新流把攒着的文本说完再收尾。
    round_end_pending = False
    # 排空期间又来了 core 的软 flush 哨兵（针对排空期间攒下的续接文本）：不能
    # 丢——续接流起来之后没人再给它 FINISH，尾句又会回到等 done。记下来，续接
    # 流一起来就补做；期间再来文本就作废（core 会为新文本重新武装定时器）。
    soft_flush_pending = False

    def _create_synthesizer(lang_hint=None):
        """Create a new SpeechSynthesizer, with an optional language hint.
        Only establishes the WebSocket connection without sending warmup text — the caller sends real text right after.
        """
        from utils.api_config_loader import (
            cosyvoice_model_supports_language_hints,
            get_cosyvoice_clone_model,
        )
        nonlocal last_streaming_call_time, synth_generation
        clone_model = _enrolled_model or get_cosyvoice_clone_model(_voice_provider)
        synth_generation += 1
        kwargs = dict(
            model=clone_model,
            voice=voice_id,
            speech_rate=1.05,
            format=AudioFormat.OGG_OPUS_48KHZ_MONO_64KBPS,
            # 钉上这一代的代号：旧 synthesizer 迟到的 on_complete 才认得出自己
            # 已经过期，不会把新一轮当成放完了。
            callback=_SynthCallback(callback, synth_generation),
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
        with prefer_dashscope_websocket_ipv4():
            synthesizer.streaming_call(char_buffer)
        _record_tts_telemetry("cosyvoice", len(char_buffer))
        last_streaming_call_time = time.time()
        char_buffer = ""

    def _do_streaming_complete(*, round_end: bool):
        """Non-blockingly notify the server that all text has been sent.
        Only sends the FINISHED signal without waiting for server confirmation. Audio keeps streaming to the frontend via the on_data callback.
        The synthesizer stays open and is closed at the next speech_id switch.

        ``round_end`` says whether this FINISH really terminates the utterance.
        The idle keep-alive caller passes False: it finishes the synthesizer only
        to beat the server's socket timeout, and more text of the same speech can
        still follow, so the resulting ``on_complete`` must not be reported as the
        end of the audio stream.
        """
        nonlocal synthesizer, last_streaming_call_time
        if synthesizer is None:
            callback.accepted_speech_id = None
            callback.reset_bootstrap_state()
            return False
        if callback.connection_lost:
            logger.info("CosyVoice WebSocket 已断开，跳过 streaming_complete")
            try:
                synthesizer.close()
            except Exception:
                pass
            synthesizer = None
            last_streaming_call_time = None
            return False

        # 标记必须在 send 之前武装：SDK 的接收线程可能在 send 返回前就把
        # on_complete 打回来（短句尤其快），标记晚一步就等于本轮白白漏发一次
        # audio_done、退化到前端 700ms give-up。
        # 本轮已经发过 FINISH：后续的 on_complete 才是真正的音频收尾。
        # 空闲保活的 FINISH 不算：本轮还可能继续来文本（届时会新建 synthesizer），
        # 那次 on_complete 发 audio_done 就是早发，前端会提前收尾。
        callback.finish_requested_speech_id = current_speech_id if round_end else None
        sent = True
        try:
            synthesizer.ws.send(synthesizer.request.getFinishRequest())
        except Exception as e:
            logger.warning(f"发送TTS完成信号失败: {e}")
            # FINISH 没发出去，服务端不会给这一轮的完成通知；撤回标记，
            # 免得后面某个别的完成通知被当成本轮收尾（早发）。
            callback.finish_requested_speech_id = None
            sent = False
        last_streaming_call_time = None
        # 这里不能立刻清 accepted_speech_id/bootstrap。
        # FINISH 发出后，服务端仍可能继续回传尾包；应由 on_complete 或后续中断/切换来收口状态。
        # 回报 FINISH 有没有真的发出去：软 flush 据此决定是等排空还是直接换流。
        return sent

    def _soft_finish():
        """Send FINISH for everything said so far without ending the round.

        The server only synthesizes the trailing sentence once it sees FINISH,
        and on some realtime routes the round's real FINISH (core's ``(None,
        None)``, tied to the provider's ``response.done``) trails the last text
        by seconds. Finishing early releases that tail; the synthesizer is then
        left to drain and more text of the same speech is handled by
        ``_service_soft_finished``.
        """
        nonlocal soft_finished, soft_finish_deadline
        if soft_finished:
            return
        try:
            _flush_buffer()  # 短尾巴可能还卡在 6 字缓冲里没建 synthesizer
        except Exception as e:
            logger.warning(f"TTS soft flush buffer 失败: {e}")
        if synthesizer is None:
            return
        sent = _do_streaming_complete(round_end=False)
        if synthesizer is None:
            # 连接已断，_do_streaming_complete 直接丢掉了 synthesizer，没什么可等的
            return
        if not sent:
            # FINISH 没发出去：服务端永远不会给完成通知，进软完成态就是白等
            # 2.5s；这条连接已经不可信，直接丢掉，后续文本走新 synthesizer。
            _retire_soft_finished_synthesizer(drained=False)
            return
        soft_finished = True
        soft_finish_deadline = time.time() + SOFT_FLUSH_DRAIN_TIMEOUT_SECONDS

    def _retire_soft_finished_synthesizer(*, drained: bool):
        """Drop a soft-finished synthesizer so the next text starts a fresh stream."""
        nonlocal synthesizer, soft_finished, soft_finish_deadline, last_streaming_call_time
        # 持锁退役：一条已经过了 generation 检查、正在冲刷/reset 的回调跑完之前
        # 不能换代，否则它醒来后清掉的是新流的缓冲和 FINISH 标记。
        with callback._lock:
            callback.finish_requested_speech_id = None
            # 先把这一代退役：close() 之后 SDK 线程仍可能迟到地打回 on_data /
            # on_close，没有当代可比对时它们一律按过期丢弃，不会混进接下来同一
            # speech_id 的新流。新 synthesizer 建出来会盖上自己的代号。
            callback.current_generation = None
            # 「断开」是这条连接的状态，随它一起退役；不然新流建起来之前它一直是
            # True，谁在这个窗口里问 connection_lost 都会跳过 FINISH。
            callback.connection_lost = False
            if not drained:
                # 没等到完成通知就放弃了：共享缓冲里可能留着旧流的半页，
                # 不能拼进新流（与重连路径同一条理由）。锁内做，和换代同一步。
                callback.reset_bootstrap_state()
        if synthesizer is not None:
            try:
                synthesizer.close()
            except Exception:
                # 这条连接反正要丢，close 失败也得继续换流；SDK 关一条多半已被
                # 服务端收掉的 ws 本来就常抛，与打断路径同款处理。
                pass
        synthesizer = None
        soft_finished = False
        soft_finish_deadline = None
        last_streaming_call_time = None

    def _service_soft_finished():
        """Advance the soft-finished state once the old stream has drained.

        Called from the idle loop and after every queue item while
        ``soft_finished`` is set. Nothing happens until the current
        generation's completion arrives (or the drain timeout passes); then
        buffered text of the same speech starts a new synthesizer, and a
        pending round end either finishes that new stream or, with nothing
        left to say, closes the audio stream directly.
        """
        nonlocal char_buffer, round_end_pending, soft_finished, soft_finish_deadline, soft_flush_pending
        if not soft_finished:
            return
        drained = callback.completed_generation == synth_generation
        if not drained and time.time() < soft_finish_deadline:
            return
        if char_buffer.strip():
            _retire_soft_finished_synthesizer(drained=drained)
            try:
                _flush_buffer()  # 同一轮接着说：新 synthesizer + 攒下的文本
            except Exception as e:
                # 与 TTS Init Error 路径同款：这段文本丢弃，别让它每 10ms 重试一次
                logger.error(f"TTS soft flush 续接失败: {e}")
                char_buffer = ""
            if round_end_pending:
                _do_streaming_complete(round_end=True)
            elif soft_flush_pending:
                # 排空期间 core 已经判定这段续接文本停了：续接流一起来就把它
                # 也软 flush 掉，否则它的尾句要等 done
                _soft_finish()
        elif round_end_pending:
            if drained:
                # 旧流的尾包早已投进队列，这里补的收尾排在它们后面
                audio_done.emit(current_speech_id)
            else:
                # 放弃等待：完成通知若迟到，还能借它把收尾补上；不来就漏发，
                # 前端 give-up 兜底（宁可漏发不可早发）。
                callback.finish_requested_speech_id = current_speech_id
            soft_finished = False
            soft_finish_deadline = None
        else:
            # 放干净了但本轮没结束、也没新文本：保持软完成态，等下一个信号
            return
        round_end_pending = False
        soft_flush_pending = False

    while True:
        # 非阻塞检查队列，优先处理打断
        if request_queue.empty():
            _service_soft_finished()
            # 主动完成：合成器空闲超过阈值，趁 WebSocket 还活着主动 complete
            # 避免等到 (None,None) 到达时 WebSocket 已被服务端回收（23s 超时）。
            # 走软 flush 同一条状态机：之后同一轮再来文本会等旧流放干净再续。
            if (not soft_finished
                    and synthesizer is not None
                    and last_streaming_call_time is not None
                    and time.time() - last_streaming_call_time > IDLE_AUTO_COMPLETE_SECONDS):
                logger.debug(f"CosyVoice 空闲 >{IDLE_AUTO_COMPLETE_SECONDS}s，主动 streaming_complete")
                _soft_finish()
            time.sleep(0.01)
            continue

        sid, tts_text = request_queue.get()

        if sid == TTS_SHUTDOWN_SENTINEL:
            break

        if sid == "__interrupt__":
            # 打断：立即静音回调 → 关闭 synthesizer → 清理状态
            # 先 mute 再 close，确保旧 SDK websocket 线程不再往 response_queue 灌数据
            callback._muted = True
            audio_done.begin_interrupt()  # 打断轮不发 audio_done（走独立 cancel 通道）
            # try/finally 与 step/qwen/grok/elevenlabs/gptsovits 对偶：拆卸段里任何
            # 意外抛出都不能把 emitter 永久停在 interrupted 上，否则本会话之后所有轮
            # 都静默漏发 audio_done。
            try:
                if synthesizer is not None:
                    try:
                        synthesizer.close()
                    except Exception:
                        # 打断拆卸：连接反正要丢弃，close 失败也得继续往下清状态，
                        # 抛上去只会把打断本身打断（回调已 _muted，不会再灌数据）。
                        pass
                synthesizer = None
                last_streaming_call_time = None
                current_speech_id = None
                char_buffer = ""
                detected_lang = None
                callback.connection_lost = False
                callback.accepted_speech_id = None
                callback.reset_bootstrap_state()
                audio_done.reset()
                soft_finished = False
                soft_finish_deadline = None
                round_end_pending = False
                soft_flush_pending = False
            finally:
                audio_done.end_interrupt()
            continue

        if sid == TTS_SOFT_FLUSH_SENTINEL:
            # core 的文本空闲哨兵：只对仍在说的这一轮有效。迟到的（sid 已经换了、
            # 或本轮已经正常收尾）直接忽略，不然会把别的轮次的流截断。
            if tts_text is not None and tts_text == current_speech_id:
                if soft_finished:
                    # 旧流还在排空：这条是给排空期间攒下的续接文本的，续接流
                    # 起来后再补做，不能就地丢掉
                    soft_flush_pending = bool(char_buffer.strip())
                else:
                    _soft_finish()
            continue

        if sid is None:
            if soft_finished:
                # 软 flush 已经把 FINISH 发出去了，同一条连接不能再发一次。
                # 等旧流放干净：有攒着的文本就起新流说完再收尾，没有就直接补收尾。
                round_end_pending = True
                _service_soft_finished()
                detected_lang = None
                continue
            # 正常结束 - 告诉TTS没有更多文本了（非阻塞）
            try:
                _flush_buffer()
            except Exception as e:
                logger.warning(f"TTS flush buffer 失败: {e}")
            _do_streaming_complete(round_end=True)
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
            audio_done.reset()  # 新轮次重置 audio_done 去重标记
            soft_finished = False
            soft_finish_deadline = None
            round_end_pending = False
            soft_flush_pending = False

        if tts_text is None or not tts_text.strip():
            time.sleep(0.01)
            continue

        if soft_finished:
            # 软 flush 之后同一轮又来文本：旧 synthesizer 已经发过 FINISH，不能再往
            # 里 streaming_call（服务端会报 task 已结束）；也不能立刻开新流（两条
            # OGG 流的页会在共享缓冲里交错）。先攒着，等旧流放干净再续。
            char_buffer += tts_text
            hint = detect_tts_language_hint(tts_text)
            if hint and detected_lang != hint:
                detected_lang = hint
            # 之前记下的软 flush 是针对更早的文本的；core 会为这一片重新武装定时器
            soft_flush_pending = False
            _service_soft_finished()
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
                with prefer_dashscope_websocket_ipv4():
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
                with prefer_dashscope_websocket_ipv4():
                    synthesizer.streaming_call(tts_text)
                last_streaming_call_time = time.time()
            except Exception:
                if synthesizer is not None:
                    # 本轮还要继续产音频，先撤掉 FINISH 标记：close() 触发的
                    # on_complete 不是本轮收尾，emit 会早发。
                    callback.finish_requested_speech_id = None
                    try:
                        synthesizer.close()
                    except Exception:
                        pass
                    synthesizer = None
                    last_streaming_call_time = None
                    # 旧流留在共享缓冲里的半包必须就地清掉。重连沿用同一个
                    # speech_id，on_data 只在 sid 变化时才重置缓冲，所以这些
                    # 半包会和新流的数据拼在一起变成坏音频。以前是靠旧
                    # synthesizer 迟到的 on_complete 顺手 reset，现在那条回调
                    # 认出自己过期就整个早退了（正是为了不动当前轮的状态），
                    # 清理只能由这里做。
                    callback.reset_bootstrap_state()

                try:
                    synthesizer = _create_synthesizer(detected_lang)
                    callback.accepted_speech_id = current_speech_id
                    with prefer_dashscope_websocket_ipv4():
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
