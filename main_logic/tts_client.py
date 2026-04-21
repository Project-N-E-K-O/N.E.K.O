"""
TTS Helper模块
负责处理TTS语音合成，支持自定义音色（阿里云CosyVoice）和默认音色（各core_api的原生TTS）
"""
import numpy as np
import soxr
import time
import json
import re
import base64
import websockets
import io
import wave
import aiohttp
import asyncio
from functools import partial
from urllib.parse import urlparse, urlunparse
from config import GSV_VOICE_PREFIX
from utils.aiohttp_proxy_utils import aiohttp_session_kwargs_for_url
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# 关闭哨兵：core.py 通过 request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
# 通知 worker 退出主循环。不能复用 (None, None)，因为它已被用作"本轮 utterance
# 结束、flush/commit 缓冲区"的信号（见 _non_bistream_tts_main_loop、step/qwen
# worker 的 sid is None 分支）。两种语义必须分开。
TTS_SHUTDOWN_SENTINEL = "__shutdown__"


def _record_tts_telemetry(model_name: str, text: str):
    """Record TTS usage telemetry via TokenTracker."""
    if not text or not text.strip():
        return
    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().record(
            model=f"tts:{model_name}",
            prompt_tokens=len(text),
            completion_tokens=0,
            total_tokens=len(text),
            call_type='tts'
        )
    except Exception:
        pass


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


def _resample_audio(audio_int16: np.ndarray, src_rate: int, dst_rate: int, 
                    resampler: 'soxr.ResampleStream | None' = None) -> bytes:
    """使用 soxr 进行高质量音频重采样
    
    Args:
        audio_int16: int16 格式的音频 numpy 数组
        src_rate: 源采样率
        dst_rate: 目标采样率
        resampler: 可选的流式重采样器，用于维护 chunk 间状态
        
    Returns:
        重采样后的 bytes
    """
    if src_rate == dst_rate:
        return audio_int16.tobytes()
    
    # 转换为 float32 进行高质量重采样
    audio_float = audio_int16.astype(np.float32) / 32768.0
    
    if resampler is not None:
        # 使用流式重采样器（维护 chunk 边界状态）
        resampled_float = resampler.resample_chunk(audio_float)
    else:
        # 无状态重采样（不推荐用于流式音频）
        resampled_float = soxr.resample(audio_float, src_rate, dst_rate, quality='HQ')
    
    # 转回 int16
    resampled_int16 = (resampled_float * 32768.0).clip(-32768, 32767).astype(np.int16)
    return resampled_int16.tobytes()


def _enqueue_error(response_queue, error_value):
    """统一错误日志与错误消息入队。"""
    if isinstance(error_value, str):
        formatted_msg = error_value
    else:
        try:
            formatted_msg = json.dumps(error_value, ensure_ascii=False, default=str)
        except Exception:
            formatted_msg = str(error_value)
    logger.error(f"TTS错误: {formatted_msg}")
    response_queue.put(("__error__", formatted_msg))


def _adjust_free_tts_url(url: str) -> str:
    """Free TTS URL 的地区替换：委托给 ConfigManager._adjust_free_api_url。"""
    try:
        return get_config_manager()._adjust_free_api_url(url, True)
    except Exception:
        return url


try:
    from websockets.connection import State as _WsState
except (ImportError, AttributeError):
    _WsState = None


def _ws_is_open(ws_conn) -> bool:
    """兼容不同 websockets 版本的连接状态检查。"""
    if ws_conn is None:
        return False
    if _WsState is not None:
        return getattr(ws_conn, "state", None) is _WsState.OPEN
    return not getattr(ws_conn, "closed", True)


_TTS_LANGUAGE_CODE_MAP = {
    'zh':    'cmn-CN',
    'zh-CN': 'cmn-CN',
    'zh-TW': 'cmn-tw',
    'en':    'en-US',
    'ja':    'ja-JP',
    'ko':    'ko-KR',
    'es':    'es-ES',
    'fr':    'fr-FR',
    'de':    'de-DE',
    'it':    'it-IT',
    'ru':    'ru-RU',
    'tr':    'tr-TR'
}


def _get_tts_language_code() -> str:
    """获取 lanlan.app TTS 服务器所需的 language_code。"""
    try:
        from utils.language_utils import get_global_language
        lang = get_global_language()
    except Exception:
        lang = 'zh'
    return _TTS_LANGUAGE_CODE_MAP.get(lang, 'cmn-CN')


# ─── TTS Provider 元数据注册表 ─────────────────────────────────────────────
#
# 所有 TTS provider 按架构分为三类，差异如下：
#
# ┌─────────────┬──────────────┬──────────────┬──────────────────────────────┐
# │ 类别         │ 输入方式      │ 输出方式      │ 成员                          │
# ├─────────────┼──────────────┼──────────────┼──────────────────────────────┤
# │ ws_bistream │ WS 流式推送   │ WS 流式回传   │ step, qwen, cosyvoice       │
# │ http_sentence│ HTTP 按句请求 │ SSE/JSON 流式 │ cogtts, gemini, openai,     │
# │             │              │ 或一次性返回   │ minimax                      │
# │ local       │ 各自实现      │ 各自实现      │ gptsovits, local_cosyvoice  │
# └─────────────┴──────────────┴──────────────┴──────────────────────────────┘
#
# ws_bistream:  文本碎片到达即发给服务端，服务端负责拼接和合成调度。
#               客户端不做句子分割。首音频延迟最低。
#               每个 provider 的 WS 协议差异较大（事件名、握手流程、
#               完成信号），因此各自独立实现，不共享主循环。
#
# http_sentence: 客户端用 SentenceBuffer 按标点切句，凑够一句后发一次
#               HTTP 请求。共享 _non_bistream_tts_main_loop 主循环和
#               _run_sentence_tts_worker 骨架，各 provider 只需提供
#               async setup() -> (synthesize_fn, cleanup_fn)。
#
# local:        连接本地服务（GPT-SoVITS / 本地 CosyVoice），协议和
#               部署方式特殊，独立实现。

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class TTSProviderMeta:
    """TTS provider 的架构元数据，用于文档化和统一查询。"""
    name: str
    category: Literal["ws_bistream", "http_sentence", "local"]
    protocol: str                   # 如 "WebSocket", "HTTP POST + SSE", "HTTP POST + JSON"
    input_streaming: bool           # 输入是否流式（文本碎片逐个发送）
    output_streaming: bool          # 输出是否流式（音频分块返回）
    client_sentence_split: bool     # 客户端是否做句子分割
    audio_format: str               # 原始音频格式，如 "PCM 24kHz", "OGG OPUS 48kHz"
    notes: str = ""                 # 特殊说明


TTS_PROVIDER_REGISTRY: dict[str, TTSProviderMeta] = {
    "step": TTSProviderMeta(
        name="step",
        category="ws_bistream",
        protocol="WebSocket (wss://api.stepfun.com)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="WAV 24kHz → resample 48kHz",
        notes="tts.text.delta 逐片发送；每个 speech_id 重建连接",
    ),
    "qwen": TTSProviderMeta(
        name="qwen",
        category="ws_bistream",
        protocol="WebSocket (wss://dashscope.aliyuncs.com)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="input_text_buffer.append 追加文本，commit 触发合成；server_commit 模式",
    ),
    "cosyvoice": TTSProviderMeta(
        name="cosyvoice",
        category="ws_bistream",
        protocol="dashscope SDK (底层 WebSocket)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="OGG OPUS 48kHz (直接透传)",
        notes="streaming_call() 逐片发送；最小 6 字符缓冲 + 日文检测；"
              "首包聚合 1KB + 后续聚合 4KB；空闲 15s 主动 complete",
    ),
    "cogtts": TTSProviderMeta(
        name="cogtts",
        category="http_sentence",
        protocol="HTTP POST + SSE (base64 音频块)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="最大 1024 字符/句；首包水印检测与裁剪",
    ),
    "gemini": TTSProviderMeta(
        name="gemini",
        category="http_sentence",
        protocol="HTTP POST + JSON (一次性返回)",
        input_streaming=False,
        output_streaming=False,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="唯一非流式输出的 provider；带 prompt 包装；最多重试 3 次",
    ),
    "openai": TTSProviderMeta(
        name="openai",
        category="http_sentence",
        protocol="HTTP POST + streaming response (PCM 流)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="gpt-4o-mini-tts；按句切分后流式接收音频",
    ),
    "minimax": TTSProviderMeta(
        name="minimax",
        category="http_sentence",
        protocol="HTTP POST + SSE (hex 编码音频块)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="speech-2.8-turbo；hex 编码音频；聚合缓冲 4KB",
    ),
    "gptsovits": TTSProviderMeta(
        name="gptsovits",
        category="local",
        protocol="WebSocket (本地 GPT-SoVITS v3 stream-input)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM (采样率由服务端决定) → resample 48kHz",
        notes="连接本地 GPT-SoVITS 服务；支持 voice_id|JSON 高级参数",
    ),
    "local_cosyvoice": TTSProviderMeta(
        name="local_cosyvoice",
        category="local",
        protocol="HTTP POST (本地 CosyVoice 服务)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM → resample 48kHz",
        notes="连接本地 CosyVoice 服务",
    ),
}


# ─── 非流式输入 TTS 公共基础设施 ───────────────────────────────────────────


class SentenceBuffer:
    """文本句子缓冲区 — 模拟 GPT-SoVITS v3 TextBuffer 的按标点切句逻辑。

    累积文本碎片，遇到句末标点时自动切分出完整句子，使 TTS 可以
    "边收文本边合成"，而不必等待 LLM 全部回复完毕。
    """

    _SENTENCE_END_RE = re.compile(r'[。！？；…\.\!\?\;]+')
    _MIN_CHARS = 2  # 避免过短片段（如孤立标点）单独合成

    def __init__(self):
        self._buf = ""

    def append(self, text: str) -> list[str]:
        """追加文本，返回已完成的句子列表（可能为空）。"""
        self._buf += text
        sentences: list[str] = []
        last = 0
        for m in self._SENTENCE_END_RE.finditer(self._buf):
            seg = self._buf[last:m.end()]
            if len(seg.strip()) >= self._MIN_CHARS:
                sentences.append(seg)
                last = m.end()
        if last:
            self._buf = self._buf[last:]
        return sentences

    def flush(self) -> str | None:
        """返回剩余文本并清空缓冲区。无有效文本时返回 None。"""
        text = self._buf
        self._buf = ""
        return text if text.strip() else None

    def clear(self):
        """丢弃所有缓冲文本。"""
        self._buf = ""


class _AudioQueueProxy:
    """response_queue 的代理，将 synthesize_fn 的 put 调用路由到正确的 slot buffer。

    synthesize_fn 的闭包在 setup() 时捕获了 response_queue 引用。
    通过让 setup() 捕获的是这个 proxy 而非真实队列，我们可以在不修改
    synthesize_fn 签名的前提下，根据当前 asyncio Task 将音频 chunk
    路由到对应句子的 buffer。

    当没有活跃的 task 映射时（如 setup 阶段发送 __ready__ 信号），
    put 调用直接转发到真实队列。
    """

    __slots__ = ('_real_queue', '_task_map')

    def __init__(self, real_queue):
        self._real_queue = real_queue
        # task → (seq, gen_id, slot_put_fn)
        self._task_map: dict = {}

    def put(self, item):
        task = None
        try:
            task = asyncio.current_task()
        except RuntimeError:
            pass
        if task is not None and task in self._task_map:
            seq, gen_id, slot_put_fn = self._task_map[task]
            slot_put_fn(seq, gen_id, item)
        else:
            # 非 synth 上下文（setup / 错误处理），直接转发
            self._real_queue.put(item)

    def _register(self, task, seq: int, gen_id: int, slot_put_fn) -> None:
        self._task_map[task] = (seq, gen_id, slot_put_fn)

    def _unregister(self, task) -> None:
        self._task_map.pop(task, None)

    def _clear(self) -> None:
        self._task_map.clear()


async def _non_bistream_tts_main_loop(
    request_queue,
    response_queue,
    synthesize_fn,
    *,
    label: str = "TTS",
    max_concurrent: int = 3,
):
    """非流式输入 TTS 的通用主循环（按句切分 + 并行合成 + 顺序投递）。

    文本到达后立即按句切分，多个句子的 TTS 请求并行发起（受
    ``max_concurrent`` 限制），但音频严格按句子顺序投递到
    ``response_queue``，保证前端播放时序正确。

    设计要点
    --------
    - **并行请求**：句子 N 的合成不必等句子 N-1 完成即可开始。
    - **顺序投递**：drain 协程按 seq_id 递增顺序转发音频 chunk。
    - **打断安全**：``__interrupt__`` / speech_id 切换时立即递增
      ``_generation_id``，所有 in-flight task 检测到 generation 过期
      后自动丢弃数据并退出，不会有残留音频泄漏到 response_queue。
    - **无 GIL 阻塞**：request_queue.get 通过 ``run_in_executor``
      执行；内部同步全部使用 asyncio 原语（Event / Semaphore），
      不使用 threading.Lock 或 time.sleep。

    response_queue 代理机制
    -----------------------
    ``synthesize_fn`` 的闭包已经捕获了 ``response_queue`` 引用。
    为了在不修改 synthesize_fn 签名的前提下将音频重定向到 per-sentence
    buffer，调用方（``_run_sentence_tts_worker``）应传入一个
    ``_AudioQueueProxy`` 实例作为 ``response_queue``。该代理的 ``put``
    方法根据当前 asyncio Task 查找对应的 slot buffer 并写入。
    若调用方传入的是真实队列（向后兼容），则退化为串行模式（max_concurrent=1）。

    Args:
        request_queue: 多进程请求队列，接收 (speech_id, text) 元组
        response_queue: 响应队列或 ``_AudioQueueProxy`` 实例
        synthesize_fn: async def(text: str, speech_id: str) -> None
        label: 日志前缀
        max_concurrent: 最大并行合成数
    """
    sentence_buf = SentenceBuffer()
    current_speech_id = None

    # ── 代理检测 ──
    is_proxy = isinstance(response_queue, _AudioQueueProxy)
    real_queue = response_queue._real_queue if is_proxy else response_queue
    proxy: _AudioQueueProxy | None = response_queue if is_proxy else None

    # 非代理模式退化为串行（向后兼容）
    if not is_proxy:
        max_concurrent = 1

    # ── 并行合成 + 顺序投递基础设施 ──

    _next_seq: int = 0                                  # 下一个分配的序号
    _slot_buffers: dict[int, list] = {}                 # seq_id → [chunk, ...]
    _slot_done: dict[int, asyncio.Event] = {}           # seq_id → 合成完成事件
    _slot_new_data: dict[int, asyncio.Event] = {}       # seq_id → 有新数据通知
    _tasks: dict[int, asyncio.Task] = {}                # seq_id → synth task
    _sem = asyncio.Semaphore(max_concurrent)
    _drain_seq: int = 0                                 # drain 当前正在投递的序号
    _drain_task: asyncio.Task | None = None
    _generation_id: int = 0                             # 每次 cancel 递增

    def _alloc_slot() -> int:
        nonlocal _next_seq
        seq = _next_seq
        _next_seq += 1
        _slot_buffers[seq] = []
        _slot_done[seq] = asyncio.Event()
        _slot_new_data[seq] = asyncio.Event()
        return seq

    def _free_slot(seq: int) -> None:
        _slot_buffers.pop(seq, None)
        _slot_done.pop(seq, None)
        _slot_new_data.pop(seq, None)
        _tasks.pop(seq, None)

    def _slot_put(seq: int, gen_id: int, item) -> None:
        """将一个 chunk 写入指定 slot 的 buffer（供 proxy 回调）。"""
        if gen_id != _generation_id:
            return
        buf = _slot_buffers.get(seq)
        evt = _slot_new_data.get(seq)
        if buf is None or evt is None:
            return
        buf.append(item)
        evt.set()

    async def _synth_one(seq: int, text: str, sid: str, gen_id: int) -> None:
        """在信号量保护下运行 synthesize_fn。"""
        done_evt = _slot_done.get(seq)
        if done_evt is None:
            return

        async with _sem:
            if gen_id != _generation_id:
                return
            task = asyncio.current_task()
            if proxy is not None:
                proxy._register(task, seq, gen_id, _slot_put)
            try:
                await synthesize_fn(text, sid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if gen_id == _generation_id:
                    _slot_put(seq, gen_id,
                              ("__synth_error__", f"{label} 合成失败: {exc}"))
            finally:
                if proxy is not None:
                    proxy._unregister(task)
                if done_evt is _slot_done.get(seq):
                    done_evt.set()
                    nd = _slot_new_data.get(seq)
                    if nd:
                        nd.set()

    async def _drain_loop(gen_id: int) -> None:
        """按 seq_id 顺序将 slot buffer 中的音频转发到真实 response_queue。"""
        nonlocal _drain_seq
        while gen_id == _generation_id:
            seq = _drain_seq
            buf = _slot_buffers.get(seq)
            done_evt = _slot_done.get(seq)
            new_data_evt = _slot_new_data.get(seq)

            if buf is None or done_evt is None or new_data_evt is None:
                # 当前序号的 slot 还没分配，让出控制权
                await asyncio.sleep(0.01)
                continue

            cursor = 0
            while gen_id == _generation_id:
                # 转发已有的 chunk
                while cursor < len(buf):
                    item = buf[cursor]
                    cursor += 1
                    if (isinstance(item, tuple) and len(item) >= 2
                            and item[0] == "__synth_error__"):
                        _enqueue_error(real_queue, item[1])
                    else:
                        real_queue.put(item)

                if done_evt.is_set():
                    # 该句子合成完毕，转发剩余 chunk 后推进到下一句
                    while cursor < len(buf):
                        item = buf[cursor]
                        cursor += 1
                        if (isinstance(item, tuple) and len(item) >= 2
                                and item[0] == "__synth_error__"):
                            _enqueue_error(real_queue, item[1])
                        else:
                            real_queue.put(item)
                    _free_slot(seq)
                    _drain_seq = seq + 1
                    break

                # 等待新数据或完成信号
                new_data_evt.clear()
                if cursor < len(buf) or done_evt.is_set():
                    continue
                try:
                    await asyncio.wait_for(new_data_evt.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

    def _ensure_drain() -> None:
        nonlocal _drain_task
        if _drain_task is None or _drain_task.done():
            _drain_task = asyncio.create_task(_drain_loop(_generation_id))

    def _enqueue_sentence(text: str, sid: str) -> None:
        seq = _alloc_slot()
        task = asyncio.create_task(_synth_one(seq, text, sid, _generation_id))
        _tasks[seq] = task
        _ensure_drain()

    async def _drain_remaining() -> None:
        """等待所有已提交的句子合成并投递完毕。"""
        tasks = list(_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(200):  # 最多等 2 秒
            if not _slot_buffers:
                break
            await asyncio.sleep(0.01)

    async def _cancel_all() -> None:
        nonlocal _drain_task, _next_seq, _drain_seq, _generation_id
        _generation_id += 1  # 使所有 in-flight 的 synth 和 drain 立即失效

        for task in list(_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(_tasks.values()):
            if not task.done():
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if _drain_task and not _drain_task.done():
            _drain_task.cancel()
            try:
                await _drain_task
            except (asyncio.CancelledError, Exception):
                pass
        _drain_task = None

        _slot_buffers.clear()
        _slot_done.clear()
        _slot_new_data.clear()
        _tasks.clear()
        _next_seq = 0
        _drain_seq = 0
        if proxy is not None:
            proxy._clear()

    # ── 主循环 ──
    loop = asyncio.get_running_loop()

    while True:
        try:
            sid, tts_text = await loop.run_in_executor(None, request_queue.get)
        except Exception:
            break

        if sid == TTS_SHUTDOWN_SENTINEL:
            break

        if sid == "__interrupt__":
            await _cancel_all()
            sentence_buf.clear()
            current_speech_id = None
            continue

        if current_speech_id != sid and sid is not None:
            await _cancel_all()
            current_speech_id = sid
            sentence_buf.clear()

        if sid is None:
            remaining = sentence_buf.flush()
            if remaining and current_speech_id is not None:
                _enqueue_sentence(remaining, current_speech_id)
            await _drain_remaining()
            current_speech_id = None
            continue

        if tts_text and tts_text.strip():
            for sent in sentence_buf.append(tts_text):
                _enqueue_sentence(sent, current_speech_id)

    await _cancel_all()

def _run_sentence_tts_worker(
    request_queue,
    response_queue,
    async_setup_fn,
    *,
    label: str,
):
    """HTTP 按句合成类 TTS worker 的通用骨架。

    封装了所有 ``_non_bistream_tts_main_loop`` 系 worker 共有的样板代码：
    asyncio 事件循环启动、就绪信号发送、主循环异常处理、资源清理。

    内部会创建 ``_AudioQueueProxy`` 代理并传给 ``async_setup_fn``，
    使 ``synthesize_fn`` 闭包捕获的是代理而非真实队列，从而支持
    并行合成时按 task 路由音频到正确的 slot buffer。

    Args:
        request_queue / response_queue: 多进程队列。
        async_setup_fn: 一个 **async** 工厂函数，签名为::

            async def setup(queue_proxy) -> tuple[synthesize_fn, cleanup_fn | None]

            - queue_proxy: ``_AudioQueueProxy`` 实例，synthesize_fn 应通过
              它（而非直接引用 response_queue）来 put 音频数据。
            - synthesize_fn: ``async def(text: str, speech_id: str) -> None``
            - cleanup_fn: 可选的 ``async def() -> None``

            如果 setup 过程中发现不可恢复的错误，应自行
            ``queue_proxy.put(("__ready__", False))`` 并 raise。
        label: 日志 / 错误消息前缀。
    """
    proxy = _AudioQueueProxy(response_queue)

    async def _worker():
        cleanup_fn = None
        try:
            synthesize_fn, cleanup_fn = await async_setup_fn(proxy)
        except Exception as exc:
            logger.error(f"{label} 初始化失败: {exc}")
            try:
                response_queue.put(("__ready__", False))
            except Exception:
                pass
            return

        logger.info(f"{label} 已就绪，发送就绪信号")
        response_queue.put(("__ready__", True))

        try:
            await _non_bistream_tts_main_loop(
                request_queue, proxy, synthesize_fn,
                label=label,
            )
        except Exception as exc:
            _enqueue_error(response_queue, f"{label} Worker 错误: {exc}")
            response_queue.put(("__ready__", False))
        finally:
            if cleanup_fn:
                try:
                    await cleanup_fn()
                except Exception:
                    pass

    try:
        asyncio.run(_worker())
    except Exception as e:
        logger.error(f"{label} Worker 启动失败: {e}")
        response_queue.put(("__ready__", False))


# ─── TTS Workers ──────────────────────────────────────────────────────────


def step_realtime_tts_worker(request_queue, response_queue, audio_api_key, voice_id, free_mode=False):
    """
    StepFun实时TTS worker（用于默认音色）
    使用阶跃星辰的实时TTS API（step-tts-mini）
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID，默认使用"qingchunshaonv"
    """
    # 使用默认音色 "qingchunshaonv"
    if not voice_id:
        voice_id = "qingchunshaonv"
    
    async def async_worker():
        """异步TTS worker主循环"""
        from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS

        if free_mode:
            tts_url = _adjust_free_tts_url("wss://www.lanlan.tech/tts")
        else:
            tts_url = "wss://api.stepfun.com/v1/realtime/audio?model=step-tts-2"
        is_lanlan_app = 'lanlan.app' in tts_url
        ws = None
        current_speech_id = None
        receive_task = None
        session_id = None
        session_ready = asyncio.Event()
        response_done = asyncio.Event()  # 用于标记当前响应是否完成
        text_done_sent = False  # 防止同一轮次重复发送 tts.text.done
        # 延迟 tts.create：等收到 TTS_LANG_DETECT_MIN_CHARS 个字符、检测完
        # 语言后再发送 tts.create（lanlan.tech 的 voice_label.language /
        # lanlan.app 的 language_code 都只能在建 session 时指定一次，
        # 所以必须在首批文本到达后才能发），和 CosyVoice worker 对偶。
        session_created = False
        pending_text_buffer = ""
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')

        def _build_tts_create_data(sid_: str, lang_hint):
            """根据 URL 和语言提示组装 tts.create 的 data 字段。
            - lanlan.app: language_code（Gemini streaming-TTS 风格；命中 ja 时覆盖全局语言）
            - lanlan.tech / 自建 StepFun: 协议对称，voice_label.language="日语"（命中 ja 时）
            """
            data = {
                "session_id": sid_,
                "voice_id": voice_id,
                "response_format": "wav",
                "sample_rate": 24000,
            }
            if is_lanlan_app:
                data["voice_id"] = "Leda"
                data["language_code"] = "ja-JP" if lang_hint == "ja" else _get_tts_language_code()
            else:
                # lanlan.tech (free) 和自建 StepFun (wss://api.stepfun.com/...) 共用同一协议
                if lang_hint == "ja":
                    data["voice_label"] = {"language": "日语"}
            return data

        async def _flush_deferred_create(force: bool = False) -> bool:
            """尚未发 tts.create 时，检测语言并发送，然后把 pending 文本刷出去。

            force=True 用于 sid=None 提前收尾的场景：不够 MIN_CHARS 也强制发。
            返回 True 表示 session 已就绪（本次新建或此前已建）。
            """
            nonlocal session_created, pending_text_buffer
            if session_created:
                return True
            if not ws or not session_id:
                return False
            if not force and len(pending_text_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                return False
            lang_hint = detect_tts_language_hint(pending_text_buffer)
            if lang_hint:
                logger.info(f"StepFun TTS 语言提示: {lang_hint}")
            create_data = _build_tts_create_data(session_id, lang_hint)
            try:
                await ws.send(json.dumps({"type": "tts.create", "data": create_data}))
            except Exception as e:
                logger.error(f"发送 tts.create 失败: {e}")
                return False
            session_created = True
            if pending_text_buffer.strip():
                try:
                    await ws.send(json.dumps({
                        "type": "tts.text.delta",
                        "data": {"session_id": session_id, "text": pending_text_buffer},
                    }))
                    _record_tts_telemetry("stepfun", pending_text_buffer)
                except Exception as e:
                    # delta 发失败时连接多半已断，调用方不能继续发 tts.text.done；
                    # 返回 False 让 sid=None/文本发送路径都走 continue 触发重连。
                    logger.error(f"刷出缓冲文本失败: {e}")
                    return False
            pending_text_buffer = ""
            return True
        
        try:
            # 连接WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}
            
            ws = await websockets.connect(tts_url, additional_headers=headers)
            
            # 等待连接成功事件
            async def wait_for_connection():
                """等待连接成功"""
                nonlocal session_id
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")
                        
                        if event_type == "tts.connection.done":
                            session_id = event.get("data", {}).get("session_id")
                            session_ready.set()
                            break
                        elif event_type == "tts.response.error":
                            _enqueue_error(response_queue, event)
                            break
                except Exception as e:
                    _enqueue_error(response_queue, e)
            
            # 等待连接成功
            try:
                await asyncio.wait_for(wait_for_connection(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error("等待连接超时")
                # 发送失败信号
                response_queue.put(("__ready__", False))
                return
            
            if not session_ready.is_set() or not session_id:
                logger.error("连接未能正确建立")
                # 发送失败信号
                response_queue.put(("__ready__", False))
                return
            
            # 启动预热 session：这段只作为 WS 连通性验证，首个真实 speech_id
            # 到达时会关闭重连。仍走一次 tts.create 保证旧逻辑的 ready 信号
            # 时序不变（服务端确认 tts.response.created 后再 __ready__）。
            create_data = _build_tts_create_data(session_id, None)
            create_event = {"type": "tts.create", "data": create_data}
            await ws.send(json.dumps(create_event))
            session_created = True

            # 等待会话创建成功
            async def wait_for_session_ready():
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "tts.response.created":
                            break
                        elif event_type == "tts.response.error":
                            logger.error(f"创建会话错误: {event}")
                            break
                except Exception as e:
                    logger.error(f"等待会话创建时出错: {e}")

            try:
                await asyncio.wait_for(wait_for_session_ready(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("会话创建超时")

            # 发送就绪信号，通知主进程 TTS 已经可以使用
            logger.info("StepFun TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))
            
            # 初始接收任务
            _text_done_error_suppressed = False  # 抑制 "tts.text.done already sent" 错误洪泛

            async def receive_messages_initial():
                """初始接收任务"""
                nonlocal _text_done_error_suppressed
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "tts.response.error":
                            # 抑制 "tts.text.done already sent" 错误级联
                            err_msg = event.get("data", {}).get("message", "")
                            if "tts.text.done" in err_msg and "already" in err_msg:
                                if not _text_done_error_suppressed:
                                    _text_done_error_suppressed = True
                                    logger.warning("TTS: 服务端报告 tts.text.done 重复，后续同类错误将被静默")
                                continue
                            _enqueue_error(response_queue, event)
                        elif event_type == "tts.response.audio.delta":
                            try:
                                # StepFun 返回 BASE64 编码的完整音频（包含 wav header）
                                audio_b64 = event.get("data", {}).get("audio", "")
                                if audio_b64:
                                    audio_bytes = base64.b64decode(audio_b64)
                                    # 使用 wave 模块读取 WAV 数据
                                    with io.BytesIO(audio_bytes) as wav_io:
                                        with wave.open(wav_io, 'rb') as wav_file:
                                            # 读取音频数据
                                            pcm_data = wav_file.readframes(wav_file.getnframes())
                                    
                                    # 转换为 numpy 数组
                                    audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                                    # 使用流式重采样器 24000Hz -> 48000Hz
                                    response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                            except Exception as e:
                                logger.error(f"处理音频数据时出错: {e}")
                        elif event_type in ["tts.response.done", "tts.response.audio.done"]:
                            # 服务器明确表示音频生成完成，设置完成标志
                            logger.debug(f"收到响应完成事件: {event_type}")
                            response_done.set()
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.error(f"消息接收出错: {e}")
            
            receive_task = asyncio.create_task(receive_messages_initial())
            
            # 主循环：处理请求队列
            loop = asyncio.get_running_loop()
            while True:
                try:
                    sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                except Exception:
                    break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    # 打断：立即关闭连接，不发 tts.text.done、不等服务器确认
                    if ws:
                        try:
                            await ws.close()
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
                    session_id = None
                    session_ready.clear()
                    current_speech_id = None
                    text_done_sent = False
                    session_created = False
                    pending_text_buffer = ""
                    continue

                if sid is None:
                    # 正常结束（非阻塞）：发送完成信号，但不等待服务器确认、不关闭连接
                    # 音频继续通过 receive_task 流入 response_queue，
                    # 连接由下次 speech_id 切换 / __interrupt__ 关闭
                    if ws and session_id and current_speech_id is not None and not text_done_sent:
                        # 若缓冲中还有不足 MIN_CHARS 的文本，强制刷出以保证短句也能合成
                        if not session_created:
                            if not await _flush_deferred_create(force=True):
                                # flush 失败（tts.create 或 delta 发失败），连接已死，
                                # 跳过 tts.text.done，等待下一个 speech_id 触发重连
                                continue
                        try:
                            done_event = {
                                "type": "tts.text.done",
                                "data": {"session_id": session_id}
                            }
                            await ws.send(json.dumps(done_event))
                            text_done_sent = True
                        except Exception as e:
                            logger.warning(f"发送TTS完成信号失败: {e}")
                    continue

                # 新的语音ID，重新建立连接
                if current_speech_id != sid:
                    current_speech_id = sid
                    text_done_sent = False
                    session_created = False
                    pending_text_buffer = ""
                    response_done.clear()
                    resampler.clear()  # 重置重采样器状态（新轮次音频不应与上轮次连续）
                    if ws:
                        try:
                            await ws.close()
                        except:  # noqa: E722
                            pass
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass
                    
                    # 建立新连接
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers)
                        
                        # 等待连接成功
                        session_id = None
                        session_ready.clear()
                        
                        async def wait_conn():
                            nonlocal session_id
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    if event.get("type") == "tts.connection.done":
                                        session_id = event.get("data", {}).get("session_id")
                                        session_ready.set()
                                        break
                            except Exception:
                                pass
                        
                        try:
                            await asyncio.wait_for(wait_conn(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning("新连接超时")
                            continue
                        
                        if not session_id:
                            continue

                        # 延迟 tts.create 到首批文本到达后，由 _flush_deferred_create
                        # 发送（带语言提示）。此处仅启动接收任务消费服务端事件。
                        _text_done_error_suppressed = False  # 重连后重置错误抑制标记

                        async def receive_messages():
                            nonlocal _text_done_error_suppressed
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")

                                    if event_type == "tts.response.error":
                                        err_msg = event.get("data", {}).get("message", "")
                                        if "tts.text.done" in err_msg and "already" in err_msg:
                                            if not _text_done_error_suppressed:
                                                _text_done_error_suppressed = True
                                                logger.warning("TTS: 服务端报告 tts.text.done 重复，后续同类错误将被静默")
                                            continue
                                        _enqueue_error(response_queue, event)
                                    elif event_type == "tts.response.audio.delta":
                                        try:
                                            audio_b64 = event.get("data", {}).get("audio", "")
                                            if audio_b64:
                                                audio_bytes = base64.b64decode(audio_b64)
                                                # 使用 wave 模块读取 WAV 数据
                                                with io.BytesIO(audio_bytes) as wav_io:
                                                    with wave.open(wav_io, 'rb') as wav_file:
                                                        # 读取音频数据
                                                        pcm_data = wav_file.readframes(wav_file.getnframes())
                                                
                                                # 转换为 numpy 数组
                                                audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                                                # 使用流式重采样器 24000Hz -> 48000Hz
                                                response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                                        except Exception as e:
                                            logger.error(f"处理音频数据时出错: {e}")
                                    elif event_type in ["tts.response.done", "tts.response.audio.done"]:
                                        # 服务器明确表示音频生成完成，设置完成标志
                                        logger.debug(f"收到响应完成事件: {event_type}")
                                        response_done.set()
                            except websockets.exceptions.ConnectionClosed:
                                pass
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                        
                        receive_task = asyncio.create_task(receive_messages())
                        
                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        await asyncio.sleep(1.0)
                        continue

                # 检查文本有效性
                if not tts_text or not tts_text.strip():
                    continue

                # 已发送 tts.text.done 后，丢弃同一轮次的残余文本（防止服务端报错）
                if text_done_sent:
                    logger.debug("TTS: 丢弃 text_done 之后的残余文本 chunk")
                    continue

                if not ws or not session_id:
                    continue

                # 尚未发送 tts.create 时，先缓冲 MIN_CHARS 个字符用于语言检测
                if not session_created:
                    pending_text_buffer += tts_text
                    ready = await _flush_deferred_create(force=False)
                    if not ready:
                        continue
                    # 已在 _flush_deferred_create 内把 pending_text_buffer 随 tts.create
                    # 一起发出，无需再次发送当前 tts_text
                    continue

                # 发送文本
                try:
                    text_event = {
                        "type": "tts.text.delta",
                        "data": {
                            "session_id": session_id,
                            "text": tts_text
                        }
                    }
                    await ws.send(json.dumps(text_event))
                    _record_tts_telemetry("stepfun", tts_text)
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    session_id = None
                    current_speech_id = None  # 清空ID以强制下次重连
                    session_created = False
                    pending_text_buffer = ""
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
        
        except Exception as e:
            logger.error(f"StepFun实时TTS Worker错误: {e}")
            if 'HTTP 503' in str(e):
                _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            response_queue.put(("__ready__", False))
        finally:
            # 清理资源
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

    # 运行异步worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"StepFun实时TTS Worker启动失败: {e}")
        response_queue.put(("__ready__", False))


def qwen_realtime_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    Qwen实时TTS worker（用于默认音色）
    使用阿里云的实时TTS API（qwen3-tts-flash-2025-09-18）
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID, 默认使用"Momo"
    """
    if not voice_id:
        voice_id = "Momo"

    from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS

    async def async_worker():
        """异步TTS worker主循环"""
        tts_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime-2025-11-27"
        ws = None
        current_speech_id = None
        receive_task = None
        session_ready = asyncio.Event()
        response_done = asyncio.Event()  # 用于标记当前响应是否完成
        buffer_committed = False  # 防止同一轮次重复提交缓冲区
        session_configured = False  # 当前连接是否已发出 session.update（延迟到首批文本到达）
        pending_text_buffer = ""  # 延迟发送的文本缓冲，用于首 N 字语言检测
        # 流式重采样器（24kHz→48kHz）- 维护 chunk 边界状态
        resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')

        def build_config_message(lang_hint=None):
            """构造 session.update 消息；lang_hint='ja' 时指定 Japanese，其他走服务端 Auto。"""
            session = {
                "mode": "server_commit",
                "voice": voice_id,
                "response_format": "pcm",
                "sample_rate": 24000,
                "channels": 1,
                "bit_depth": 16,
            }
            if lang_hint == "ja":
                session["language_type"] = "Japanese"
            return {
                "type": "session.update",
                "event_id": f"event_{int(time.time() * 1000)}",
                "session": session,
            }

        async def _flush_deferred_config(force: bool = False) -> bool:
            """按需发送延迟的 session.update，并把缓冲文本 append 出去。

            - 未达到阈值且非 force：返回 False。
            - 已发送或执行后：返回 True。
            """
            nonlocal session_configured, pending_text_buffer
            if session_configured:
                return True
            if not ws:
                return False
            if not force and len(pending_text_buffer) < TTS_LANG_DETECT_MIN_CHARS:
                return False
            lang_hint = detect_tts_language_hint(pending_text_buffer)
            try:
                await ws.send(json.dumps(build_config_message(lang_hint)))
            except Exception as e:
                logger.error(f"发送延迟 session.update 失败: {e}")
                return False
            session_configured = True
            try:
                await asyncio.wait_for(session_ready.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Qwen TTS: 延迟 session.update 等待超时")
            if pending_text_buffer.strip():
                try:
                    await ws.send(json.dumps({
                        "type": "input_text_buffer.append",
                        "event_id": f"event_{int(time.time() * 1000)}",
                        "text": pending_text_buffer,
                    }))
                    _record_tts_telemetry("qwen", pending_text_buffer)
                except Exception as e:
                    # append 发失败时连接多半已断，调用方不能继续发 commit；
                    # 返回 False 让 sid=None/文本路径走 continue 触发重连。
                    logger.error(f"发送缓冲文本失败: {e}")
                    return False
            pending_text_buffer = ""
            return True

        try:
            # 连接WebSocket
            headers = {"Authorization": f"Bearer {audio_api_key}"}

            ws = await websockets.connect(tts_url, additional_headers=headers)
            
            # 等待并处理初始消息
            async def wait_for_session_ready():
                """等待会话创建确认"""
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")
                        
                        # Qwen TTS API 返回 session.updated 而不是 session.created
                        if event_type in ["session.created", "session.updated"]:
                            session_ready.set()
                            break
                        elif event_type == "error":
                            _enqueue_error(response_queue, event)
                            break
                except Exception as e:
                    _enqueue_error(response_queue, e)
            
            # 发送预热配置（pre-warm），真正的 session.update 会在首批文本到达后
            # 通过 _flush_deferred_config 重新发送（携带语言提示）。
            await ws.send(json.dumps(build_config_message(None)))
            session_configured = True

            # 等待会话就绪（超时5秒）
            try:
                await asyncio.wait_for(wait_for_session_ready(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error("❌ 等待会话就绪超时")
                response_queue.put(("__ready__", False))
                return

            if not session_ready.is_set():
                logger.error("❌ 会话未能正确初始化")
                response_queue.put(("__ready__", False))
                return

            # 发送就绪信号
            logger.info("Qwen TTS 已就绪，发送就绪信号")
            response_queue.put(("__ready__", True))

            # 初始接收任务（会在每次新 speech_id 时重新创建）
            async def receive_messages_initial():
                """初始接收任务"""
                nonlocal ws
                try:
                    async for message in ws:
                        event = json.loads(message)
                        event_type = event.get("type")

                        if event_type == "error":
                            # 空闲超时 / 会话过期：不报 error，标记连接丢失，按需重连
                            err_msg = event.get("error", {}).get("message", "")
                            if "request timeout" in err_msg or "session_expired" in err_msg:
                                logger.debug(f"Qwen TTS 空闲超时，标记连接已断开: {err_msg}")
                                break
                            _enqueue_error(response_queue, event)
                        elif event_type == "response.audio.delta":
                            try:
                                audio_bytes = base64.b64decode(event.get("delta", ""))
                                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                                # 使用流式重采样器 24000Hz -> 48000Hz
                                response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                            except Exception as e:
                                logger.error(f"处理音频数据时出错: {e}")
                        elif event_type in ["response.done", "response.audio.done", "output.done"]:
                            # 服务器明确表示音频生成完成，设置完成标志
                            logger.debug(f"收到响应完成事件: {event_type}")
                            response_done.set()
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.error(f"消息接收出错: {e}")
                finally:
                    # 接收循环退出（超时/断开），清理连接状态以便主循环按需重连
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                        ws = None
                    session_ready.clear()

            receive_task = asyncio.create_task(receive_messages_initial())
            
            # 主循环：处理请求队列
            loop = asyncio.get_running_loop()
            pending = None  # 断线重试时暂存当前片段，保证顺序（不回共享队列）
            while True:
                # 优先处理断线暂存的片段，再从队列取新请求
                if pending:
                    sid, tts_text = pending
                    pending = None
                else:
                    try:
                        sid, tts_text = await loop.run_in_executor(None, request_queue.get)
                    except Exception:
                        break

                if sid == TTS_SHUTDOWN_SENTINEL:
                    break

                if sid == "__interrupt__":
                    # 打断：立即关闭连接，不发 commit、不等服务器确认
                    if ws:
                        try:
                            await ws.close()
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
                    session_ready.clear()
                    current_speech_id = None
                    buffer_committed = False
                    session_configured = False
                    pending_text_buffer = ""
                    continue

                if sid is None:
                    # 正常结束（非阻塞）：提交缓冲区，但不等待服务器确认、不关闭连接
                    # 音频继续通过 receive_task 流入 response_queue，
                    # 连接由下次 speech_id 切换 / __interrupt__ 关闭
                    if ws and current_speech_id is not None:
                        # 若此轮文本不足 MIN_CHARS 还没发出 session.update，force 一次
                        if not session_configured:
                            if not await _flush_deferred_config(force=True):
                                # flush 失败（session.update 或 append 发失败），连接已死，
                                # 跳过 commit，等待下一个 speech_id 触发重连
                                continue
                        # 短句场景下 session.updated 可能比 _flush 内的 2s 等待更晚到达；
                        # 不再依赖 session_ready，直接发 commit（服务端会在 session.updated
                        # 就绪后按顺序处理 append + commit）。漏 commit 会导致短句静默丢失。
                        if not buffer_committed:
                            try:
                                await ws.send(json.dumps({
                                    "type": "input_text_buffer.commit",
                                    "event_id": f"event_{int(time.time() * 1000)}_commit"
                                }))
                                buffer_committed = True
                            except Exception as e:
                                logger.warning(f"提交缓冲区失败: {e}")
                    continue
                
                # 新的语音ID，重新建立连接（类似 speech_synthesis_worker 的逻辑）
                # 直接关闭旧连接，打断旧语音
                if current_speech_id != sid:
                    current_speech_id = sid
                    buffer_committed = False
                    session_configured = False
                    pending_text_buffer = ""
                    response_done.clear()
                    resampler.clear()  # 重置重采样器状态（新轮次音频不应与上轮次连续）
                    if ws:
                        try:
                            await ws.close()
                        except:  # noqa: E722
                            pass
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass

                    # 建立新连接（延迟 session.update 至首批文本到达后发送，携带语言提示）
                    try:
                        ws = await websockets.connect(tts_url, additional_headers=headers)
                        session_ready.clear()

                        # 启动新的接收任务（合并 session.updated 监听）
                        async def receive_messages():
                            nonlocal ws
                            try:
                                async for message in ws:
                                    event = json.loads(message)
                                    event_type = event.get("type")

                                    if event_type in ["session.created", "session.updated"]:
                                        session_ready.set()
                                    elif event_type == "error":
                                        # 空闲超时 / 会话过期：不报 error，标记连接丢失，按需重连
                                        err_msg = event.get("error", {}).get("message", "")
                                        if "request timeout" in err_msg or "session_expired" in err_msg:
                                            logger.debug(f"Qwen TTS 空闲超时，标记连接已断开: {err_msg}")
                                            break
                                        _enqueue_error(response_queue, event)
                                    elif event_type == "response.audio.delta":
                                        try:
                                            audio_bytes = base64.b64decode(event.get("delta", ""))
                                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                                            # 使用流式重采样器 24000Hz -> 48000Hz
                                            response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                                        except Exception as e:
                                            logger.error(f"处理音频数据时出错: {e}")
                                    elif event_type in ["response.done", "response.audio.done", "output.done"]:
                                        # 服务器明确表示音频生成完成，设置完成标志
                                        logger.debug(f"收到响应完成事件: {event_type}")
                                        response_done.set()
                            except websockets.exceptions.ConnectionClosed:
                                pass
                            except Exception as e:
                                logger.error(f"消息接收出错: {e}")
                            finally:
                                # 接收循环退出（超时/断开），清理连接状态以便主循环按需重连
                                if ws:
                                    try:
                                        await ws.close()
                                    except Exception:
                                        pass
                                    ws = None
                                session_ready.clear()
                        
                        receive_task = asyncio.create_task(receive_messages())
                        
                    except Exception as e:
                        logger.error(f"重新建立连接失败: {e}")
                        if 'HTTP 503' in str(e):
                            _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                        response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                        await asyncio.sleep(1.0)
                        continue

                # 检查文本有效性
                if not tts_text or not tts_text.strip():
                    continue

                if not ws:
                    # 连接已因空闲超时断开，暂存当前片段并重置 speech_id 以触发重连
                    current_speech_id = None
                    pending = (sid, tts_text)
                    continue

                # 尚未发送 session.update 时，先缓冲 MIN_CHARS 个字符用于语言检测
                if not session_configured:
                    pending_text_buffer += tts_text
                    ready = await _flush_deferred_config(force=False)
                    if not ready:
                        continue
                    # 已在 _flush_deferred_config 内把 pending_text_buffer 随 append 一起发出
                    continue

                if not session_ready.is_set():
                    # session.update 已发但会话还未就绪（超时/断开），触发重连
                    current_speech_id = None
                    pending = (sid, tts_text)
                    continue

                # 追加文本到缓冲区（不立即提交，等待响应完成时的终止信号再 commit）
                try:
                    await ws.send(json.dumps({
                        "type": "input_text_buffer.append",
                        "event_id": f"event_{int(time.time() * 1000)}",
                        "text": tts_text
                    }))
                    _record_tts_telemetry("qwen", tts_text)
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    current_speech_id = None  # 清空ID以强制下次重连
                    session_ready.clear()
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
        
        except Exception as e:
            logger.error(f"Qwen实时TTS Worker错误: {e}")
            if 'HTTP 503' in str(e):
                _enqueue_error(response_queue, json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            response_queue.put(("__ready__", False))
        finally:
            # 清理资源
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
    
    # 运行异步worker
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"Qwen实时TTS Worker启动失败: {e}")
        response_queue.put(("__ready__", False))


def cosyvoice_vc_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    TTS多进程worker函数，用于阿里云CosyVoice TTS
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥
        voice_id: 音色ID
    """
    import dashscope
    from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer, AudioFormat
    from utils.language_utils import detect_tts_language_hint, TTS_LANG_DETECT_MIN_CHARS

    dashscope.api_key = audio_api_key

    # 从 voice 元数据中读取注册时使用的模型，fallback 到全局配置
    _voice_meta = _get_voice_meta(voice_id)
    _enrolled_model = (_voice_meta or {}).get('clone_model') if _voice_meta else None
    
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
        """创建新的 SpeechSynthesizer，可选语言提示。
        仅建立 WebSocket 连接，不发送预热文本——调用方会紧接着发送真实文本。
        """
        from utils.api_config_loader import (
            cosyvoice_model_supports_language_hints,
            get_cosyvoice_clone_model,
        )
        nonlocal last_streaming_call_time
        clone_model = _enrolled_model or get_cosyvoice_clone_model()
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
        syn = SpeechSynthesizer(**kwargs)
        last_streaming_call_time = time.time()
        return syn

    def _flush_buffer():
        """检测语言、创建 synthesizer（如果需要）并刷出缓冲区"""
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
        _record_tts_telemetry("cosyvoice", char_buffer)
        last_streaming_call_time = time.time()
        char_buffer = ""

    def _do_streaming_complete():
        """非阻塞地通知服务器文本已全部发送。
        只发 FINISHED 信号，不等服务器确认。音频继续通过 on_data 回调流向前端。
        synthesizer 保持开放，由下一次 speech_id 切换时关闭。
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
                _record_tts_telemetry("cosyvoice", char_buffer)
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


def cogtts_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """智谱AI CogTTS worker — 按句切分合成，SSE 流式输出音频。"""
    import httpx

    if not voice_id:
        voice_id = "tongtong"

    tts_url = "https://open.bigmodel.cn/api/paas/v4/audio/speech"

    async def setup(response_queue):
        headers = {
            "Authorization": f"Bearer {audio_api_key}",
            "Content-Type": "application/json",
        }

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            payload = {
                "model": "cogtts",
                "input": text[:1024],  # CogTTS最大支持1024字符
                "voice": voice_id,
                "response_format": "pcm",
                "encode_format": "base64",
                "speed": 1.0,
                "volume": 1.0,
                "stream": True,
            }
            async with client.stream(
                "POST", tts_url, headers=headers, json=payload,
                timeout=httpx.Timeout(15, connect=10),
            ) as resp:
                if resp.status_code != 200:
                    error_text = ""
                    async for chunk in resp.aiter_text():
                        error_text += chunk
                    _enqueue_error(
                        response_queue,
                        f"CogTTS API错误 ({resp.status_code}): {error_text[:300]}",
                    )
                    return

                _record_tts_telemetry("cogtts", text[:1024])
                buffer = ""
                first_audio_received = False

                def _detect_beep_watermark(audio: np.ndarray, sr: int) -> int:
                    """检测开头的滴滴声水印，返回应裁剪的采样数（0 = 未检测到）。

                    检测策略：在前 1.5s 内寻找短促高频脉冲（beep）。
                    beep 特征：短时能量突增 + 高频占比显著高于语音。
                    """
                    scan_len = min(int(sr * 1.5), len(audio))
                    if scan_len < int(sr * 0.05):
                        return 0

                    frame_size = int(sr * 0.01)   # 10ms 帧
                    hop = frame_size
                    hf_threshold = 0.55            # 高频能量占比阈值
                    energy_floor = 1e-6
                    beep_frames: list[int] = []

                    for start in range(0, scan_len - frame_size, hop):
                        frame = audio[start:start + frame_size]
                        spectrum = np.abs(np.fft.rfft(frame))
                        freqs = np.fft.rfftfreq(frame_size, 1.0 / sr)

                        total_energy = np.sum(spectrum ** 2)
                        if total_energy < energy_floor:
                            continue

                        hf_energy = np.sum(spectrum[freqs >= 2000] ** 2)
                        hf_ratio = hf_energy / total_energy

                        if hf_ratio >= hf_threshold:
                            beep_frames.append(start + frame_size)

                    if len(beep_frames) < 2:
                        return 0

                    # 裁剪到最后一个 beep 帧之后 + 5ms 安全余量
                    trim_end = beep_frames[-1] + int(sr * 0.005)
                    return min(trim_end, scan_len)

                def _handle_sse_line(line: str) -> None:
                    """解析单条 SSE data 行并将音频入队。"""
                    nonlocal first_audio_received
                    line = line.strip()
                    if not line or not line.startswith('data: '):
                        return
                    json_str = line[6:]
                    try:
                        event_data = json.loads(json_str)
                        choices = event_data.get('choices', [])
                        if not choices or 'delta' not in choices[0]:
                            return
                        delta = choices[0]['delta']
                        audio_b64 = delta.get('content', '')
                        if not audio_b64:
                            return

                        audio_bytes = base64.b64decode(audio_b64)
                        if len(audio_bytes) < 200:
                            return

                        sample_rate = delta.get('return_sample_rate', 24000)
                        audio_array = np.frombuffer(
                            audio_bytes, dtype=np.int16,
                        ).astype(np.float32) / 32768.0

                        # 首个音频块：检测并裁剪水印滴滴声
                        if not first_audio_received:
                            first_audio_received = True
                            trim_samples = _detect_beep_watermark(
                                audio_array, sample_rate,
                            )
                            if trim_samples > 0:
                                logger.info(
                                    "CogTTS: 检测到水印滴滴声，裁剪 %.0fms",
                                    trim_samples / sample_rate * 1000,
                                )
                                audio_array = audio_array[trim_samples:]
                                # 通知前端检测到水印
                                response_queue.put((
                                    "__warning__",
                                    json.dumps({
                                        "code": "TTS_WATERMARK_DETECTED",
                                        "level": "info",
                                    }),
                                ))
                                # 裁剪后淡入 10ms 避免爆音
                                fade_samples = min(
                                    int(sample_rate * 0.01),
                                    len(audio_array),
                                )
                                if fade_samples > 0:
                                    audio_array[:fade_samples] *= np.linspace(
                                        0.0, 1.0, fade_samples,
                                    )

                        if len(audio_array) == 0:
                            return

                        resampled = soxr.resample(
                            audio_array, sample_rate, 48000, quality='HQ',
                        )
                        resampled_int16 = (
                            (resampled * 32768.0)
                            .clip(-32768, 32767)
                            .astype(np.int16)
                        )
                        response_queue.put(resampled_int16.tobytes())
                    except json.JSONDecodeError as e:
                        logger.warning(f"CogTTS SSE JSON 解析失败: {e}")
                    except Exception as e:
                        logger.error(f"CogTTS 音频处理出错: {e}")

                async for raw_chunk in resp.aiter_text():
                    buffer += raw_chunk
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        _handle_sse_line(line)

                # 处理尾部残留（服务端最后一条消息可能不带换行）
                if buffer.strip():
                    _handle_sse_line(buffer)

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="CogTTS")


def gemini_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """Gemini TTS worker — 按句切分合成，httpx 异步直连。"""
    import httpx

    if not voice_id:
        voice_id = "Leda"

    MODEL = "gemini-2.5-flash-preview-tts"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{MODEL}:generateContent?key={audio_api_key}"
    )
    TTS_TIMEOUT = 12
    MAX_RETRIES = 3

    async def setup(response_queue):
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(TTS_TIMEOUT + 2, connect=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        # TLS 连接预热
        try:
            logger.info("Gemini TTS TLS 预热中...")
            await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}",
                params={"key": audio_api_key},
                timeout=10,
            )
            logger.info("Gemini TTS TLS 预热完成")
        except Exception as e:
            logger.warning(f"Gemini TTS TLS 预热失败（不影响后续使用）: {e}")

        async def synthesize(text: str, speech_id: str) -> None:
            wrapped = (
                "Say the text with a proper tone, "
                f"don't omit or add any words:\n\"{text}\""
            )
            payload = {
                "contents": [{"parts": [{"text": wrapped}]}],
                "generationConfig": {
                    "response_modalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": voice_id}
                        }
                    },
                },
            }
            audio_data = None
            for attempt in range(1, MAX_RETRIES + 1):
                t0 = time.time()
                try:
                    r = await client.post(url, json=payload, timeout=TTS_TIMEOUT)
                    r.raise_for_status()
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            inline = parts[0].get("inlineData", {})
                            audio_b64 = inline.get("data")
                            if audio_b64:
                                audio_data = base64.b64decode(audio_b64)
                    dt = time.time() - t0
                    if audio_data:
                        logger.info(
                            f"Gemini TTS API 返回: {len(audio_data)}B, "
                            f"{dt:.1f}s (attempt {attempt})"
                        )
                    break
                except Exception as e:
                    dt = time.time() - t0
                    logger.warning(
                        f"Gemini TTS attempt {attempt}/{MAX_RETRIES} "
                        f"失败 ({dt:.1f}s): {e}"
                    )
                    if attempt == MAX_RETRIES:
                        raise

            if audio_data:
                _record_tts_telemetry("gemini", text)
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                resampled_bytes = _resample_audio(audio_array, 24000, 48000)
                response_queue.put(resampled_bytes)

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="Gemini TTS")


def openai_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """OpenAI TTS worker — 按句切分合成，流式接收音频。"""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("❌ 无法导入 openai 库，OpenAI TTS 不可用")
        response_queue.put(("__ready__", False))
        while True:
            try:
                sid, _ = request_queue.get()
                if sid == TTS_SHUTDOWN_SENTINEL:
                    break
            except Exception:
                break
        return

    if not voice_id:
        voice_id = "marin"

    async def setup(response_queue):
        client = AsyncOpenAI(api_key=audio_api_key)

        async def synthesize(text: str, speech_id: str) -> None:
            async with client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts",
                voice=voice_id,
                input=text,
                response_format="pcm",
            ) as response:
                _record_tts_telemetry("gpt-4o-mini-tts", text)
                async for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        audio_array = np.frombuffer(chunk, dtype=np.int16)
                        response_queue.put(_resample_audio(audio_array, 24000, 48000))

        return synthesize, None

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="OpenAI TTS")


def gptsovits_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """GPT-SoVITS TTS Worker - 使用 v3 WebSocket stream-input 双工模式
    
    Args:
        request_queue: 多进程请求队列，接收 (speech_id, text) 元组
        response_queue: 多进程响应队列，发送音频数据（也用于发送就绪信号）
        audio_api_key: API密钥（未使用，保持接口一致）
        voice_id: v3 声音配置ID，格式为 "voice_id" 或 "voice_id|高级参数JSON"
                  例如: "my_voice" 或 "my_voice|{\"speed\":1.2,\"text_lang\":\"all_zh\"}"
    
    配置项（通过 TTS_MODEL_URL 设置）:
        base_url: GPT-SoVITS API 地址，如 "http://127.0.0.1:9881"
                  会自动转换为 ws:// 协议用于 WebSocket 连接
    """
    _ = audio_api_key  # 未使用，但保持接口一致

    # 获取配置
    cm = get_config_manager()
    tts_config = cm.get_model_api_config('tts_custom')
    base_url = (tts_config.get('base_url') or 'http://127.0.0.1:9881').rstrip('/')

    # 转换为 WS URL
    if base_url.startswith('http://'):
        ws_base = 'ws://' + base_url[7:]
    elif base_url.startswith('https://'):
        ws_base = 'wss://' + base_url[8:]
    elif base_url.startswith('ws://') or base_url.startswith('wss://'):
        ws_base = base_url
    else:
        ws_base = 'ws://' + base_url

    WS_URL = f'{ws_base}/api/v3/tts/stream-input'

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
        """检查 WS 连接是否仍然打开（兼容 websockets v14+/v16）"""
        if ws_conn is None:
            return False
        if _WsState is not None:
            return getattr(ws_conn, 'state', None) is _WsState.OPEN
        # fallback: 旧版 websockets
        return not getattr(ws_conn, 'closed', True)

    def _extract_pcm_from_wav(wav_bytes: bytes) -> tuple:
        """从 WAV chunk 中提取 PCM 数据和采样率"""
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
        """异步 TTS worker 主循环 - WebSocket 双工模式"""
        ws = None
        receive_task = None
        current_speech_id = None
        resampler = None

        async def receive_loop(ws_conn):
            """独立接收协程：处理 WS 返回的音频 chunk 和 JSON 消息"""
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
                            response_queue.put(resampled_bytes)
                    else:
                        # JSON 消息（日志用）
                        try:
                            msg = json.loads(message)
                            msg_type = msg.get('type', '')
                            if msg_type == 'sentence':
                                logger.debug(f"[GPT-SoVITS v3] 合成: {msg.get('text', '')[:30]}...")
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
            """关闭当前 WS 会话"""
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

        async def create_connection():
            """创建新的 WS 连接并发送 init"""
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
            receive_task = asyncio.create_task(receive_loop(ws))
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
                    # 正常结束：发送 end 关闭会话（v3 end 会自动 flush 剩余文本）
                    if _ws_is_open(ws):
                        await close_session(ws, receive_task, send_end=True)
                        ws = None
                        receive_task = None
                    current_speech_id = None
                    continue

                if not tts_text or not tts_text.strip():
                    continue

                if not re.sub(r'\W+', '', tts_text.strip()):
                    continue

                # 用 append 累积碎片文本，v3 TextBuffer 自动按标点切句推理
                if _ws_is_open(ws):
                    try:
                        await ws.send(json.dumps({"cmd": "append", "data": tts_text}))
                        _record_tts_telemetry("gptsovits", tts_text)
                        logger.debug(f"[GPT-SoVITS v3] append: {tts_text[:30]}...")
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


def _get_minimax_tts_http_url(base_url: str | None = None) -> str:
    """将 MiniMax API base URL 规范化为 TTS HTTP SSE 地址。"""
    raw_url = (base_url or "https://api.minimaxi.com").strip().rstrip("/")
    # 将 ws/wss 协议转为 http/https
    if raw_url.startswith("ws://"):
        raw_url = "http://" + raw_url[5:]
    elif raw_url.startswith("wss://"):
        raw_url = "https://" + raw_url[6:]
    elif not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise ValueError(f"无效的 MiniMax base_url: {base_url!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "/v1/t2a_v2", "", "", ""))


async def _minimax_sse_synthesize(
    client, api_url: str, headers: dict, model: str,
    text: str, voice_id: str, speech_id: str,
    response_queue, agg_flush_bytes: int,
):
    """对 MiniMax T2A v2 HTTP SSE 接口发起一次合成请求并流式接收音频。"""
    import binascii

    payload = {
        "model": model,
        "text": text,
        "stream": True,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 24000,
            "bitrate": 128000,
            "format": "pcm",
            "channel": 1,
        },
        "output_format": "hex",
        "stream_options": {
            "exclude_aggregated_audio": True,
        },
    }

    resampler = None
    audio_chunk_buffer = bytearray()

    def flush_audio(force: bool = False) -> None:
        nonlocal audio_chunk_buffer
        while len(audio_chunk_buffer) >= agg_flush_bytes:
            chunk = bytes(audio_chunk_buffer[:agg_flush_bytes])
            del audio_chunk_buffer[:agg_flush_bytes]
            response_queue.put(("__audio__", speech_id, chunk))
        if force and audio_chunk_buffer:
            response_queue.put(("__audio__", speech_id, bytes(audio_chunk_buffer)))
            audio_chunk_buffer.clear()

    def process_audio_chunk(audio_hex: str) -> None:
        """处理单个音频块（hex 编码）"""
        nonlocal resampler
        if not audio_hex:
            return
        try:
            pcm_bytes = binascii.unhexlify(audio_hex)
        except (binascii.Error, ValueError) as exc:
            _enqueue_error(response_queue, f"MiniMax TTS 音频解码失败: {exc}")
            return
        if pcm_bytes:
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
            if resampler is None:
                resampler = soxr.ResampleStream(24000, 48000, 1, dtype="float32")
            audio_chunk_buffer.extend(
                _resample_audio(audio_array, 24000, 48000, resampler)
            )
            flush_audio(force=False)

    def process_event(event: dict) -> bool:
        """处理单个事件，返回 False 表示遇到错误需要停止"""
        base_resp = event.get("base_resp") or {}
        if base_resp.get("status_code", 0) != 0:
            _enqueue_error(
                response_queue,
                f"MiniMax TTS 服务端错误: {base_resp.get('status_msg', '')} (code={base_resp.get('status_code')})",
            )
            return False
        
        data = event.get("data") or {}
        audio_hex = data.get("audio", "")
        process_audio_chunk(audio_hex)
        return True

    try:
        async with client.stream("POST", api_url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                error_text = ""
                async for chunk in resp.aiter_text():
                    error_text += chunk
                _enqueue_error(response_queue, f"MiniMax TTS API错误 ({resp.status_code}): {error_text[:300]}")
                return

            _record_tts_telemetry("minimax", text)

            content_type = resp.headers.get("content-type", "").lower()

            # SSE 格式: text/event-stream
            if "text/event-stream" in content_type:
                buffer = ""
                async for raw_chunk in resp.aiter_text():
                    buffer += raw_chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        # SSE 格式: "data: {json}"
                        if line.startswith("data:"):
                            json_str = line[5:].strip()
                            if not json_str or json_str == "[DONE]":
                                continue
                            try:
                                event = json.loads(json_str)
                                if not process_event(event):
                                    flush_audio(force=True)
                                    return
                            except json.JSONDecodeError:
                                logger.warning("MiniMax TTS SSE JSON 解析失败: %s", json_str[:200])
                                continue

                # 处理流结束后 buffer 中可能残留的最后一行（服务端未发尾部换行）
                residual = buffer.strip()
                if residual:
                    if residual.startswith("data:"):
                        json_str = residual[5:].strip()
                        if json_str and json_str != "[DONE]":
                            try:
                                event = json.loads(json_str)
                                process_event(event)
                            except json.JSONDecodeError:
                                logger.warning("MiniMax TTS SSE JSON 解析失败 (残留): %s", json_str[:200])

            # JSON 流格式: application/json (逐行 JSON 对象)
            else:
                buffer = ""
                async for raw_chunk in resp.aiter_text():
                    buffer += raw_chunk
                    # 尝试按行分割 JSON 对象
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue

                        # 移除可能的逗号分隔符
                        if line.startswith(","):
                            line = line[1:].strip()
                        if line.endswith(","):
                            line = line[:-1].strip()

                        # 跳过数组开始/结束标记
                        if line in ("[", "]"):
                            continue

                        try:
                            event = json.loads(line)
                            if not process_event(event):
                                flush_audio(force=True)
                                return
                        except json.JSONDecodeError:
                            # 不完整的 JSON 或格式错误，记录警告后跳过
                            logger.warning("MiniMax TTS JSON 解析失败: %s", line[:200])
                            continue

                # 处理流结束后 buffer 中可能残留的最后一行
                residual = buffer.strip()
                if residual:
                    if residual.startswith(","):
                        residual = residual[1:].strip()
                    if residual.endswith(","):
                        residual = residual[:-1].strip()
                    if residual and residual not in ("[", "]"):
                        try:
                            event = json.loads(residual)
                            process_event(event)
                        except json.JSONDecodeError:
                            logger.warning("MiniMax TTS JSON 解析失败 (残留): %s", residual[:200])

            flush_audio(force=True)

    except Exception as exc:
        _enqueue_error(response_queue, f"MiniMax TTS 合成失败: {exc}")
        flush_audio(force=True)


def minimax_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None):
    """MiniMax TTS worker — 按句切分合成，HTTP SSE 流式输出音频。"""
    import httpx

    async def setup(response_queue):
        api_url = _get_minimax_tts_http_url(base_url)
        headers = {
            "Authorization": f"Bearer {audio_api_key}",
            "Content-Type": "application/json",
        }
        model_name = "speech-2.8-turbo"
        agg_flush_bytes = 4096

        # 连通性探测
        # per-call AsyncClient: 一次性 probe，紧接着下面会构造 per-worker 持久 client
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=10)) as probe:
            probe_resp = await probe.post(
                api_url, headers=headers,
                json={"model": model_name, "text": "", "stream": False,
                      "voice_setting": {"voice_id": voice_id}},
                timeout=10,
            )
            if probe_resp.status_code not in (200, 400):
                error_text = probe_resp.text[:200]
                _enqueue_error(
                    response_queue,
                    f"MiniMax TTS 探测失败 ({probe_resp.status_code}): {error_text}",
                )
                raise RuntimeError("MiniMax TTS 探测失败")

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            await _minimax_sse_synthesize(
                client, api_url, headers, model_name,
                text, voice_id, speech_id,
                response_queue, agg_flush_bytes,
            )

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="MiniMax TTS")


def dummy_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    空的TTS worker（用于不支持TTS的core_api）
    持续清空请求队列但不生成任何音频，使程序正常运行但无语音输出
    
    Args:
        request_queue: 多进程请求队列，接收(speech_id, text)元组
        response_queue: 多进程响应队列（也用于发送就绪信号）
        audio_api_key: API密钥（不使用）
        voice_id: 音色ID（不使用）
    """
    logger.warning("TTS Worker 未启用，不会生成语音")
    
    # 立即发送就绪信号
    response_queue.put(("__ready__", True))
    
    while True:
        try:
            # 持续清空队列以避免阻塞，但不做任何处理
            sid, tts_text = request_queue.get()
            if sid == TTS_SHUTDOWN_SENTINEL:
                break
            # sid is None 是 end-of-utterance 信号，dummy 不做任何处理
            if sid == "__interrupt__" or sid is None:
                continue
        except Exception as e:
            logger.error(f"Dummy TTS Worker 错误: {e}")
            break


def _get_voice_meta(voice_id: str) -> dict | None:
    """获取 voice_id 对应的 voice_data 元信息（含 provider 字段）。

    返回 voice_data dict（至少含 ``provider``），找不到时返回 None。
    """
    if not voice_id:
        return None
    try:
        cm = get_config_manager()
        voices = cm.get_voices_for_current_api()
        vdata = voices.get(voice_id)
        if isinstance(vdata, dict):
            return vdata
    except Exception:
        pass
    return None


def get_tts_worker(core_api_type='qwen', has_custom_voice=False, voice_id=''):
    """
    根据 core_api 类型和是否有自定义音色，返回一个 callable。

    该 callable 的签名为 (request_queue, response_queue, api_key, voice_id)，
    所有 provider 特有的参数（如 base_url）已通过 partial 绑定。
    若某个 provider 需要替换 api_key，返回的第二个值非 None。

    Returns:
        (worker_fn, api_key_override, provider_key)
        - worker_fn: 签名统一的 TTS worker callable
        - api_key_override: 若非 None，替换 tts_config['api_key']
        - provider_key: 实际选中的 provider 名称（对应 TTS_PROVIDER_REGISTRY 的 key），
          用于调用方查询 provider 元数据（如 category）。
          特殊值：'free' 故意不在 registry 中（国外走 Gemini 后端需要 normalizer，
          meta=None → 调用方 fallthrough 启用 normalizer）；
          不支持原生 TTS 时为 None
    """
    cm = get_config_manager()

    # 优先检查克隆音色 provider（MiniMax / 阿里 CosyVoice）
    if has_custom_voice and voice_id:
        voice_meta = _get_voice_meta(voice_id)
        if voice_meta is None:
            # 本地元数据缺失 — 可能是本地 TTS 音色（GPT-SoVITS / CosyVoice local），
            # 也可能是远端 clone 成功但本地保存失败。fallthrough 让后续分支处理。
            logger.debug("克隆音色 %s 无本地元数据，跳过 MiniMax 检测", voice_id)
        elif voice_meta.get('provider', '').startswith('minimax'):
            provider = voice_meta['provider']
            logger.info("检测到 MiniMax 克隆音色: %s (provider=%s)，使用 MiniMax TTS Worker",
                        voice_id, provider)
            api_key = cm.get_tts_api_key(provider)
            from utils.voice_clone import MINIMAX_DOMESTIC_BASE_URL, MINIMAX_INTL_BASE_URL
            base_url = voice_meta.get('minimax_base_url') or (
                MINIMAX_INTL_BASE_URL if provider == 'minimax_intl' else MINIMAX_DOMESTIC_BASE_URL
            )
            worker = partial(minimax_tts_worker, base_url=base_url)
            return worker, api_key, 'minimax'

    try:
        tts_config = cm.get_model_api_config('tts_custom')
        if tts_config.get('is_custom'):
            base_url = tts_config.get('base_url') or ''
            # GPT-SoVITS / local CosyVoice 需要用户显式启用 gptsovitsEnabled 开关，
            # 仅 enableCustomApi + http URL 不应自动路由到 GPT-SoVITS。
            core_cfg = cm.get_core_config()
            gsv_enabled = core_cfg.get('GPTSOVITS_ENABLED', False)
            if gsv_enabled and (base_url.startswith('http://') or base_url.startswith('https://')):
                return gptsovits_tts_worker, None, 'gptsovits'
            if gsv_enabled and (base_url.startswith('ws://') or base_url.startswith('wss://')):
                return local_cosyvoice_worker, None, 'local_cosyvoice'
    except Exception as e:
        logger.warning(f'TTS调度器检查报告:{e}')

    # 如果有自定义克隆音色，使用 CosyVoice（阿里云）
    # 必须同时有有效的 voice_id 且不是免费预设音色，否则 fallthrough 到默认 TTS
    if has_custom_voice and voice_id:
        from utils.api_config_loader import get_free_voices
        if voice_id not in set(get_free_voices().values()):
            return cosyvoice_vc_tts_worker, None, 'cosyvoice'
        logger.info("voice_id '%s' 是免费预设音色，跳过 CosyVoice，使用默认 TTS", voice_id)

    # 没有自定义音色时，使用与 core_api 匹配的默认 TTS
    if core_api_type == 'qwen':
        return qwen_realtime_tts_worker, None, 'qwen'
    if core_api_type == 'free':
        # provider_key 故意用 'free' 而非 'step'：'free' 不在 TTS_PROVIDER_REGISTRY 中，
        # 使调用方 meta=None → normalizer 启用，因为 free 国外模式走 Gemini 后端需要
        # CJK 空格清理。若改为 'step'（ws_bistream）则国外 free 用户的 normalizer 会被错误禁用。
        return partial(step_realtime_tts_worker, free_mode=True), None, 'free'
    elif core_api_type == 'step':
        return step_realtime_tts_worker, None, 'step'
    elif core_api_type == 'glm':
        return cogtts_tts_worker, None, 'cogtts'
    elif core_api_type == 'gemini':
        return gemini_tts_worker, None, 'gemini'
    elif core_api_type == 'openai':
        return openai_tts_worker, None, 'openai'
    else:
        logger.error(f"{core_api_type}不支持原生TTS，请使用自定义语音")
        return dummy_tts_worker, None, None


def local_cosyvoice_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    本地 CosyVoice WebSocket Worker（OpenAI 兼容 bistream 版本）
    适配 openai_server.py 定义的 /v1/audio/speech/stream 接口
    
    协议流程：
    1. 连接后发送 config: {"voice": ..., "speed": ...}
    2. 发送文本: {"text": ...}
    3. 发送结束信号: {"event": "end"}
    4. 接收 bytes 音频数据（16-bit PCM, 22050Hz）
    
    特性：
    - 双工流：发送和接收独立运行，互不阻塞
    - 打断支持：speech_id 变化时关闭旧连接，打断旧语音
    - 非阻塞：异步架构，不会卡住主循环
    
    注意：audio_api_key 参数未使用（本地模式不需要 API Key），保留是为了与其他 worker 保持统一签名
    """
    _ = audio_api_key  # 本地模式不需要 API Key

    cm = get_config_manager()
    tts_config = cm.get_model_api_config('tts_custom')

    ws_base = tts_config.get('base_url', '')
    if (ws_base and not ws_base.startswith('ws://') and not ws_base.startswith('wss://')) or not ws_base:
        if ws_base:
            logger.error(f'本地cosyvoice URL协议无效: {ws_base}，需要 ws/wss 协议')
        else:
            logger.error('本地cosyvoice未配置url, 请在设置中填写正确的端口')
        response_queue.put(("__ready__", True))
        # 模仿 dummy_tts：持续清空队列但不生成音频
        while True:
            try:
                sid, _ = request_queue.get()
                if sid == TTS_SHUTDOWN_SENTINEL:
                    break
            except Exception:
                break
        return
    
    # OpenAI 兼容端点
    WS_URL = f'{ws_base}/v1/audio/speech/stream'
    
    # 从 voice_id 解析 voice 和 speed（格式：voice 或 voice:speed）
    voice_name = voice_id or "中文女"
    speech_speed = 1.0
    if voice_id and ':' in voice_id:
        parts = voice_id.split(':', 1)
        voice_name = parts[0]
        try:
            speech_speed = float(parts[1])
        except ValueError:
            pass
    
    # 服务器返回的采样率（22050Hz）
    SRC_RATE = 22050

    async def async_worker():
        ws = None
        receive_task = None
        current_speech_id = None
        
        resampler = soxr.ResampleStream(SRC_RATE, 48000, 1, dtype='float32')

        async def receive_loop(ws_conn):
            """独立接收任务，处理音频流"""
            try:
                async for message in ws_conn:
                    if isinstance(message, bytes):
                        # 服务器返回 16-bit PCM @ 22050Hz
                        audio_array = np.frombuffer(message, dtype=np.int16)
                        resampled_bytes = _resample_audio(audio_array, SRC_RATE, 48000, resampler)
                        response_queue.put(resampled_bytes)
            except websockets.exceptions.ConnectionClosed:
                logger.debug("本地 WebSocket 连接已关闭")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                _enqueue_error(response_queue, f"接收循环异常: {e}")

        async def send_end_signal(ws_conn):
            """发送结束信号（文本已在主循环中实时发送，此处只需发送 end）"""
            try:
                await ws_conn.send(json.dumps({"event": "end"}))
                logger.debug("发送结束信号")
            except Exception as e:
                _enqueue_error(response_queue, f"发送结束信号失败: {e}")

        async def create_connection():
            """创建新连接并发送配置"""
            nonlocal ws, receive_task, resampler
            
            # 清理旧连接
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
            
            # 重置 resampler
            resampler = soxr.ResampleStream(SRC_RATE, 48000, 1, dtype='float32')
            
            logger.info(f"🔄 [LocalTTS] 正在连接: {WS_URL}")
            ws = await websockets.connect(WS_URL, ping_interval=None)
            logger.info("✅ [LocalTTS] 连接成功")
            
            # 发送配置
            config = {
                "voice": voice_name,
                "speed": speech_speed,
            }
            await ws.send(json.dumps(config))
            logger.debug(f"发送配置: {config}")
            
            # 启动接收任务
            receive_task = asyncio.create_task(receive_loop(ws))
            return ws

        # 初始连接
        try:
            await create_connection()
            response_queue.put(("__ready__", True))
        except Exception as e:
            logger.error(f"❌ [LocalTTS] 初始连接失败: {e}")
            logger.error("请确保服务器已运行且端口正确")
            response_queue.put(("__ready__", False))
            return

        # 主循环
        loop = asyncio.get_running_loop()
        while True:
            try:
                sid, tts_text = await loop.run_in_executor(None, request_queue.get)
            except Exception as e:
                logger.error(f'队列获取异常: {e}')
                break

            if sid == TTS_SHUTDOWN_SENTINEL:
                break

            if sid == "__interrupt__":
                # 打断：立即关闭连接，不发 end 信号
                if receive_task and not receive_task.done():
                    receive_task.cancel()
                    try:
                        await receive_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    receive_task = None
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = None
                current_speech_id = None
                continue

            # speech_id 变化 -> 打断旧语音，建立新连接
            if sid != current_speech_id and sid is not None:
                if ws:
                    await send_end_signal(ws)
                
                current_speech_id = sid
                try:
                    await create_connection()
                except Exception as e:
                    logger.error(f"重连失败: {e}")
                    ws = None
                    continue

            if sid is None:
                # 正常结束：发送结束信号
                if ws:
                    await send_end_signal(ws)
                current_speech_id = None
                continue

            if not tts_text or not tts_text.strip():
                continue
            
            # 同时发送（bistream 模式允许边发边收）
            if ws:
                try:
                    await ws.send(json.dumps({"text": tts_text}))
                    _record_tts_telemetry("local_cosyvoice", tts_text)
                    logger.debug(f"发送合成片段: {tts_text}")
                except Exception as e:
                    _enqueue_error(response_queue, f"发送失败: {e}")
                    ws = None

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

    # 运行 Asyncio 循环
    try:
        asyncio.run(async_worker())
    except Exception as e:
        logger.error(f"Local CosyVoice Worker 崩溃: {e}")
        response_queue.put(("__ready__", False))
