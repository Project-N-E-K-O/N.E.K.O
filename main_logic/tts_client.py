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
from urllib.parse import quote, urlparse, urlunparse
from config import GSV_VOICE_PREFIX
from utils.aiohttp_proxy_utils import aiohttp_session_kwargs_for_url
from utils.config_manager import _as_bool, get_config_manager
from utils.gptsovits_config import (
    gsv_ws_url_from_http_base,
    is_local_http_url,
    normalize_gsv_api_url,
    redact_url_for_log,
)
from utils.dashscope_region import (
    DASHSCOPE_GLOBAL_LOCK,
    configure_dashscope_sdk_urls,
    dashscope_ws_url_from_base,
)
from utils.elevenlabs_tts_voices import (
    ELEVENLABS_TTS_DEFAULT_MODEL,
    ELEVENLABS_TTS_DEFAULT_OUTPUT_FORMAT,
    normalize_elevenlabs_voice_id,
)
from utils.gemini_tts_voices import (
    GEMINI_TTS_MODEL,
    normalize_gemini_tts_voice,
)
from utils.logger_config import get_module_logger
from utils.mimo_tts_voices import (
    MIMO_TTS_BASE_URL,
    MIMO_TTS_MODEL,
    normalize_mimo_tts_voice,
)
from utils.native_voice_registry import (
    get_native_tts_worker,
    make_native_tts_resolver,
    register_tts_worker_resolver,
)
from utils.stepfun_tts_voices import (
    STEPFUN_TTS_DEFAULT_VOICE,
    get_stepfun_tts_default_voice,
    normalize_stepfun_tts_voice,
)

logger = get_module_logger(__name__, "Main")

# 关闭哨兵：core.py 通过 request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
# 通知 worker 退出主循环。不能复用 (None, None)，因为它已被用作"本轮 utterance
# 结束、flush/commit 缓冲区"的信号（见 _non_bistream_tts_main_loop、step/qwen
# worker 的 sid is None 分支）。两种语义必须分开。
TTS_SHUTDOWN_SENTINEL = "__shutdown__"

_QWEN_REALTIME_TTS_MODEL = "qwen3-tts-flash-realtime-2025-11-27"
_DASHSCOPE_DEFAULT_REALTIME_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
VLLM_OMNI_DEFAULT_BASE_URL = "ws://localhost:8091/v1"
VLLM_OMNI_DEFAULT_MODEL = "Qwen3-TTS"


def _resolve_qwen_realtime_tts_url() -> str:
    """根据当前 Qwen/Qwen Intl 核心配置选择实时 TTS WebSocket 地址。"""
    try:
        core_config = get_config_manager().get_core_config() or {}
    except Exception:
        core_config = {}
    base_ws_url = dashscope_ws_url_from_base(
        core_config.get("CORE_URL", ""),
        "realtime",
        _DASHSCOPE_DEFAULT_REALTIME_WS_URL,
    )
    configured_model = str(core_config.get("TTS_MODEL") or "").strip()
    model = configured_model if configured_model.startswith("qwen3-tts") else _QWEN_REALTIME_TTS_MODEL
    return f"{base_ws_url}?model={quote(model, safe='')}"


def _record_tts_telemetry(model_name: str, char_count: int):
    """Record TTS usage telemetry via TokenTracker.

    TTS providers (CosyVoice, CogTTS, GPT-SoVITS, etc.) bill per character,
    not per token, so we report the input length on the dedicated
    `prompt_chars` field instead of squatting in `prompt_tokens`. Token
    aggregates stay clean for actual LLM usage tracking.

    Telemetry hard rule: this helper takes a count only. Never pass synthesized
    text or any substring of it — only ``len(text)``. Sending raw content into
    the tracker risks leaking user utterances through the remote uploader.
    """
    if char_count <= 0:
        return
    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().record(
            model=f"tts:{model_name}",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            call_type='tts',
            prompt_chars=int(char_count),
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


def _resolve_elevenlabs_api_key(cm) -> str:
    return (cm.get_tts_api_key('elevenlabs') or '').strip()


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


def _get_tts_language_code() -> str:
    """获取 lanlan.app TTS 服务器所需的 language_code。

    实现收敛到 utils.language_utils.get_tts_language_code —— core/realtime 与
    TTS server 两条路共用同一张 BCP-47 映射表，避免漂移。
    """
    from utils.language_utils import get_tts_language_code
    return get_tts_language_code()


def _build_step_tts_create_data(sid_: str, voice_id: str, lang_hint, is_lanlan_app: bool) -> dict:
    """根据 URL 和语言提示组装 Step/free TTS 的 tts.create data 字段。"""
    data = {
        "session_id": sid_,
        "voice_id": voice_id,
        "response_format": "wav",
        "sample_rate": 24000,
    }
    if is_lanlan_app:
        # 发真实 voice_id（data 里已带传入值），由 www.lanlan.app 服务端透传给
        # Gemini 并做映射；不再客户端硬覆盖成 Leda。
        data["language_code"] = "ja-JP" if lang_hint == "ja" else _get_tts_language_code()
    else:
        # lanlan.tech (free) 和自建 StepFun 协议对称，都用 voice_label。
        if lang_hint == "ja":
            data["voice_label"] = {"language": "日语"}
    return data


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
        protocol="WebSocket (wss://dashscope*.aliyuncs.com)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="input_text_buffer.append 追加文本，commit 触发合成；server_commit 模式",
    ),
    "grok": TTSProviderMeta(
        name="grok",
        category="ws_bistream",
        protocol="WebSocket (wss://api.x.ai/v1/tts)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="text.delta 逐片发送；无 session 握手；language=auto；每个 speech_id 重连",
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
    "mimo": TTSProviderMeta(
        name="mimo",
        category="http_sentence",
        protocol="HTTP POST /v1/chat/completions (SSE audio delta)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="mimo-v2.5-tts；辅助 API 选择 MiMo 时使用",
    ),
    "elevenlabs": TTSProviderMeta(
        name="elevenlabs",
        category="ws_bistream",
        protocol="WebSocket (wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM 24kHz -> resample 48kHz",
        notes="ElevenLabs text-to-speech stream with Flash v2.5 by default",
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
    "vllm_omni": TTSProviderMeta(
        name="vllm_omni",
        category="ws_bistream",
        protocol="WebSocket (ws://host:8091/v1/audio/speech/stream)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="连接vLLM-Omni部署的TTS服务",
    ),
}


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
    """True = chunk 是 kaomoji / 怪符号堆，丢；False = 放行。

    扫完所有字符再判：任何 alnum 都让 chunk 立刻放行（含 letter 的 kaomoji
    如 `T_T` / `\\(^o^)/` 走这条路过——server clean 完还有字母，不会触发
    empty error）；无 alnum 时只看有没有非白名单符号。
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
    sentence_trace_fn=None,
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
    _sentence_enqueued_at: dict[int, float] = {}        # seq_id → enqueue monotonic time
    _sem = asyncio.Semaphore(max_concurrent)
    _drain_seq: int = 0                                 # drain 当前正在投递的序号
    _drain_task: asyncio.Task | None = None
    _generation_id: int = 0                             # 每次 cancel 递增

    def _trace_sentence(event: str, seq: int, sid: str, text: str, **extra) -> None:
        if sentence_trace_fn is None:
            return
        try:
            sentence_trace_fn(event, seq, sid, text, **extra)
        except Exception:
            pass

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
        _sentence_enqueued_at.pop(seq, None)

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
            started_at = time.perf_counter()
            enqueued_at = _sentence_enqueued_at.get(seq, started_at)
            queue_wait_ms = int((started_at - enqueued_at) * 1000)
            _trace_sentence("start", seq, sid, text, queue_wait_ms=queue_wait_ms)
            if proxy is not None:
                proxy._register(task, seq, gen_id, _slot_put)
            try:
                await synthesize_fn(text, sid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if gen_id == _generation_id:
                    _trace_sentence("error", seq, sid, text, error=str(exc))
                    _slot_put(seq, gen_id,
                              ("__synth_error__", f"{label} 合成失败: {exc}"))
            finally:
                total_ms = int((time.perf_counter() - started_at) * 1000)
                _trace_sentence("done", seq, sid, text, total_ms=total_ms)
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
        _sentence_enqueued_at[seq] = time.perf_counter()
        _trace_sentence("enqueue", seq, sid, text)
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
        _sentence_enqueued_at.clear()
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
    sentence_trace_fn=None,
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
                sentence_trace_fn=sentence_trace_fn,
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
        voice_id: 音色ID，默认读取 api_providers.json 的 StepFun 配置
    """
    # free + livestream 子模式：voice_id 优先取 api_providers.json 的
    # livestream_config.voice_id（绕过 caller 的 free_voices preset 路径）。
    # 多进程 worker 这里独立 import，与主进程对偶。
    native_provider_key = 'free' if free_mode else 'step'
    default_voice_id = get_stepfun_tts_default_voice(native_provider_key)

    if free_mode:
        try:
            from utils.api_config_loader import is_livestream_active, get_livestream_config
            if is_livestream_active():
                ls_voice = get_livestream_config().get('voice_id', '')
                if ls_voice:
                    voice_id = ls_voice
                else:
                    # 半配置状态（启用了但没填 voice_id）：明确告警，避免误以为
                    # 直播音色已生效却实际还在用 caller 传入或默认 preset
                    logger.warning(
                        "livestream_config.enabled=true 但 voice_id 为空，"
                        f"继续使用 caller 传入或默认音色: {voice_id or default_voice_id}"
                    )
        except Exception as e:
            logger.warning(f"读取 livestream voice_id 失败，回退到 caller 传入值: {e}")

    voice_id = (voice_id or '').strip()

    # 使用配置中的默认 StepFun 音色
    if not voice_id:
        voice_id = default_voice_id or STEPFUN_TTS_DEFAULT_VOICE
    else:
        normalized_voice_id, voice_recognized = normalize_stepfun_tts_voice(
            voice_id,
            native_provider_key,
        )
        if voice_recognized:
            voice_id = normalized_voice_id
    
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
            return _build_step_tts_create_data(sid_, voice_id, lang_hint, is_lanlan_app)

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
                    _record_tts_telemetry("stepfun", len(pending_text_buffer))
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
                    _record_tts_telemetry("stepfun", len(tts_text))
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
            logger.error(f"StepFun实时TTS Worker错误: {type(e).__name__}: {e!r}", exc_info=True)
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
        logger.error(f"StepFun实时TTS Worker启动失败: {type(e).__name__}: {e!r}", exc_info=True)
        response_queue.put(("__ready__", False))


register_tts_worker_resolver(
    'step',
    make_native_tts_resolver(step_realtime_tts_worker, 'tts_default_api_key'),
)
register_tts_worker_resolver(
    'free',
    make_native_tts_resolver(
        step_realtime_tts_worker,
        'tts_default_api_key',
        worker_kwargs={'free_mode': True},
    ),
)
# free_intl（海外免费 *.lanlan.app）：上游 Gemini 代理走 www.lanlan.app/tts，
# 协议同 free（StepFun-shape streaming，proxy 把 voice_id 透传给 Gemini），
# 因此复用 free 的 worker。与 free 对偶，仅 provider key 不同（registry 按
# host 把 free→free_intl 重映射，让 yui/Gemini 音色短路到这里而非外部 TTS）。
register_tts_worker_resolver(
    'free_intl',
    make_native_tts_resolver(
        step_realtime_tts_worker,
        'tts_default_api_key',
        worker_kwargs={'free_mode': True},
    ),
)


# xAI 文档：'Individual deltas are capped at 15,000 characters'。
# pending_text 累积 + 长 utterance 合并下可能超过这个上限，需要切片发送。
_XAI_TTS_DELTA_CAP = 15000


def _grok_chunk_text_delta(text: str, cap: int = _XAI_TTS_DELTA_CAP) -> list[str]:
    """把可能超过 xAI text.delta 上限的文本切成顺序发送的多段。
    返回的每段长度 ≤ cap；空输入返回空列表。"""
    if not text:
        return []
    if len(text) <= cap:
        return [text]
    return [text[i:i + cap] for i in range(0, len(text), cap)]


def grok_streaming_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    xAI Grok 流式 TTS worker（wss://api.x.ai/v1/tts）

    协议特点（对比 step）:
      - 无 session 握手 / 无 tts.create，连上即可推 text.delta
      - 配置全部走 query params（voice/language/codec/sample_rate）
      - codec=pcm 时音频是 raw 16-bit little-endian，无 WAV header
      - language 必传；用 auto 让服务端自动检测，省掉客户端语言检测

    Args:
        request_queue: 多进程请求队列，接收 (speech_id, text) 元组
        response_queue: 多进程响应队列，发送音频数据和就绪信号
        audio_api_key: xAI API key
        voice_id: 内置音色（eve/ara/leo/rex/sal）/ alias（male / 女声 等）/ 自定义
            8 位音色 id / 空。routing 层（native_voice_registry）会把 alias
            识别为 native；worker 这里再归一化成 xAI canonical id，
            因为 xAI 端点的 voice query param 只接受 canonical id 或
            自定义 8 位 id，不认 alias。
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
                            response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
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
                            response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))
                        except Exception as e:
                            logger.error(f"xAI TTS 音频解码失败: {e}")
                    elif event_type == "audio.done":
                        pass
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
        tts_url = _resolve_qwen_realtime_tts_url()
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
                    _record_tts_telemetry("qwen", len(pending_text_buffer))
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
                    _record_tts_telemetry("qwen", len(tts_text))
                except Exception as e:
                    logger.error(f"发送TTS文本失败: {e}")
                    # 连接已关闭，标记为无效以便下次重连
                    ws = None
                    current_speech_id = None  # 清空ID以强制下次重连
                    session_ready.clear()
                    if receive_task and not receive_task.done():
                        receive_task.cancel()
        
        except Exception as e:
            logger.error(f"Qwen实时TTS Worker错误: {type(e).__name__}: {e!r}", exc_info=True)
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
        logger.error(f"Qwen实时TTS Worker启动失败: {type(e).__name__}: {e!r}", exc_info=True)
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

    # 从 voice 元数据中读取注册时使用的模型和地域 URL，缺失时回退到全局配置
    _voice_meta = _get_voice_meta(voice_id)
    _enrolled_model = _voice_meta.get('clone_model') if _voice_meta else None
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
        """每次重建 SpeechSynthesizer 前调用（必须在 DASHSCOPE_GLOBAL_LOCK 内），
        保证 module-global 是 worker 自己的地域/key。
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
        """创建新的 SpeechSynthesizer，可选语言提示。
        仅建立 WebSocket 连接，不发送预热文本——调用方会紧接着发送真实文本。
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
        _record_tts_telemetry("cosyvoice", len(char_buffer))
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

                # CogTTS payload 实际只发了 text[:1024]（行 2407 的硬截断，上游
                # API 限制 1024 字符）。telemetry 记 min 而不是 len(text)，否则超
                # 长输入会高估实际计费/上行的字符数。
                _record_tts_telemetry("cogtts", min(len(text), 1024))
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

    requested_voice_id = (voice_id or "").strip()
    voice_id, voice_recognized = normalize_gemini_tts_voice(voice_id)
    if requested_voice_id and not voice_recognized:
        logger.warning(
            "Gemini TTS voice '%s' is not in the supported catalog; falling back to '%s'",
            requested_voice_id,
            voice_id,
        )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_TTS_MODEL}:generateContent?key={audio_api_key}"
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
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}",
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
                _record_tts_telemetry("gemini", len(text))
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                resampled_bytes = _resample_audio(audio_array, 24000, 48000)
                response_queue.put(resampled_bytes)

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="Gemini TTS")


# Gemini 内置音色和 realtime/LLM endpoint 共用 CORE_API_KEY，不走自定义 TTS slot。
register_tts_worker_resolver(
    'gemini',
    make_native_tts_resolver(gemini_tts_worker, 'core_api_key'),
)


# xAI Grok 内置音色（eve/ara/leo/rex/sal）同样走 CORE_API_KEY。
# 没有这个注册时，非空 voice_id 会让 core._has_custom_tts() 返 True，
# get_tts_worker() 在 `core_api_type == 'grok'` 默认分支前就路由到
# cosyvoice_vc_tts_worker —— 静默合成或鉴权失败。详见 PR #1306 Codex review。
register_tts_worker_resolver(
    'grok',
    make_native_tts_resolver(grok_streaming_tts_worker, 'core_api_key'),
)


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
                _record_tts_telemetry("gpt-4o-mini-tts", len(text))
                async for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        audio_array = np.frombuffer(chunk, dtype=np.int16)
                        response_queue.put(_resample_audio(audio_array, 24000, 48000))

        return synthesize, None

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="OpenAI TTS")


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


def _get_mimo_chat_completions_url(base_url: str | None = None) -> str:
    """Normalize a MiMo API base URL to the chat-completions endpoint."""
    raw_url = (base_url or MIMO_TTS_BASE_URL).strip().rstrip("/")
    if raw_url.startswith("ws://"):
        raw_url = "http://" + raw_url[5:]
    elif raw_url.startswith("wss://"):
        raw_url = "https://" + raw_url[6:]
    elif not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise ValueError(f"无效的 MiMo base_url: {base_url!r}")

    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    else:
        if not path or path == "/":
            endpoint_path = "/v1/chat/completions"
        elif path.endswith("/v1"):
            endpoint_path = f"{path}/chat/completions"
        else:
            endpoint_path = f"{path}/v1/chat/completions"
    return urlunparse((parsed.scheme, parsed.netloc, endpoint_path, "", "", ""))


def _extract_mimo_tts_audio_bytes(payload: dict) -> bytes | None:
    """Extract base64 PCM16 audio from MiMo's chat-completions response."""
    candidates: list[object] = [payload.get("audio")]
    for choice in payload.get("choices") or []:
        if isinstance(choice, dict):
            candidates.extend([
                choice.get("audio"),
                (choice.get("message") or {}).get("audio"),
                (choice.get("delta") or {}).get("audio"),
            ])
            content = (choice.get("message") or {}).get("content")
            if isinstance(content, list):
                candidates.extend(content)

    for candidate in candidates:
        audio_b64 = ""
        if isinstance(candidate, str):
            audio_b64 = candidate
        elif isinstance(candidate, dict):
            audio_b64 = (
                candidate.get("data")
                or candidate.get("audio")
                or candidate.get("content")
                or ""
            )
        if not audio_b64:
            continue
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            continue
        usable_len = len(audio_bytes) - (len(audio_bytes) % 2)
        if usable_len > 0:
            return audio_bytes[:usable_len]
    return None


def mimo_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None):
    """Xiaomi MiMo-V2.5-TTS worker — chat-completions JSON returns PCM16."""
    import httpx

    requested_voice_id = (voice_id or "").strip()
    voice_id, voice_recognized = normalize_mimo_tts_voice(voice_id)
    if requested_voice_id and not voice_recognized:
        logger.warning(
            "MiMo TTS voice '%s' is not in the supported catalog; falling back to '%s'",
            requested_voice_id,
            voice_id,
        )

    async def setup(response_queue):
        if not audio_api_key:
            _enqueue_error(response_queue, {
                "code": "API_KEY_MISSING",
                "provider": "mimo",
                "message": "MiMo API key is not configured",
            })
            raise RuntimeError("MiMo API key is not configured")

        api_url = _get_mimo_chat_completions_url(base_url)
        headers = {
            "Content-Type": "application/json",
            "api-key": audio_api_key,
        }
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            payload = {
                "model": MIMO_TTS_MODEL,
                "messages": [
                    {"role": "assistant", "content": text},
                ],
                "audio": {
                    "format": "pcm16",
                    "voice": voice_id,
                },
                "stream": True,
            }
            resampler = soxr.ResampleStream(24000, 48000, 1, dtype="float32")

            def handle_event(event: dict) -> None:
                audio_bytes = _extract_mimo_tts_audio_bytes(event)
                if not audio_bytes:
                    return
                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))

            try:
                async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        error_text = ""
                        async for chunk in resp.aiter_text():
                            error_text += chunk
                        _enqueue_error(
                            response_queue,
                            f"MiMo TTS API错误 ({resp.status_code}): {error_text[:300]}",
                        )
                        return

                    _record_tts_telemetry(MIMO_TTS_MODEL, len(text))
                    content_type = resp.headers.get("content-type", "").lower()
                    if "text/event-stream" not in content_type:
                        try:
                            body = await resp.aread()
                            handle_event(json.loads(body.decode("utf-8")))
                        except Exception as exc:
                            _enqueue_error(response_queue, f"MiMo TTS 响应 JSON 解析失败: {exc}")
                        return

                    buffer = ""
                    async for raw_chunk in resp.aiter_text():
                        buffer += raw_chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if not line or line == "[DONE]":
                                continue
                            try:
                                handle_event(json.loads(line))
                            except json.JSONDecodeError:
                                logger.warning("MiMo TTS SSE JSON 解析失败 (len=%d)", len(line))
                                continue

                    residual = buffer.strip()
                    if residual:
                        if residual.startswith("data:"):
                            residual = residual[5:].strip()
                        if residual and residual != "[DONE]":
                            try:
                                handle_event(json.loads(residual))
                            except json.JSONDecodeError:
                                logger.warning("MiMo TTS SSE JSON 解析失败 (残留, len=%d)", len(residual))
            except Exception as exc:
                _enqueue_error(response_queue, f"MiMo TTS 请求失败: {exc}")
                return

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="MiMo TTS")


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
    base_url = normalize_gsv_api_url(tts_config.get('base_url'))

    if not is_local_http_url(base_url):
        message = (
            "GPT-SoVITS URL 配置无效：需要 http(s)://localhost 或 "
            "http(s)://127.0.0.1 这类本地服务地址"
        )
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
        "[GPT-SoVITS v3] 使用本地服务: base=%s ws=%s",
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

            _record_tts_telemetry("minimax", len(text))

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
                                # 上游响应可能含 TTS 原文，不写 logger
                                logger.warning("MiniMax TTS SSE JSON 解析失败 (len=%d)", len(json_str))
                                print(f"[MiniMax TTS] SSE JSON 解析失败 raw: {json_str[:200]}")
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
                                logger.warning("MiniMax TTS SSE JSON 解析失败 (残留, len=%d)", len(json_str))
                                print(f"[MiniMax TTS] SSE JSON 解析失败 (残留) raw: {json_str[:200]}")

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
                            # 不完整的 JSON 或格式错误，记录警告后跳过；不写原文到 logger
                            logger.warning("MiniMax TTS JSON 解析失败 (len=%d)", len(line))
                            print(f"[MiniMax TTS] JSON 解析失败 raw: {line[:200]}")
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
                            logger.warning("MiniMax TTS JSON 解析失败 (残留, len=%d)", len(residual))
                            print(f"[MiniMax TTS] JSON 解析失败 (残留) raw: {residual[:200]}")

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


def _normalize_elevenlabs_voice_id(voice_id: str | None) -> str:
    return normalize_elevenlabs_voice_id(voice_id)


def _parse_elevenlabs_pcm_sample_rate(output_format: str | None) -> int:
    match = re.match(r"^pcm_(\d+)$", (output_format or "").strip())
    if not match:
        return 24000
    try:
        return int(match.group(1))
    except ValueError:
        return 24000


def _is_elevenlabs_pcm_output_format(output_format: str | None) -> bool:
    return bool(re.match(r"^pcm_(\d+)$", (output_format or "").strip()))


def _get_elevenlabs_options(base_url=None):
    raw_base_url = (
        base_url
        or "https://api.elevenlabs.io"
    )
    base_url = (raw_base_url or "https://api.elevenlabs.io").strip().rstrip('/')

    return {
        'base_url': base_url,
        'model': ELEVENLABS_TTS_DEFAULT_MODEL,
        'output_format': ELEVENLABS_TTS_DEFAULT_OUTPUT_FORMAT,
        'stability': 0.5,
        'similarity_boost': 0.75,
        'style': 0.0,
        'use_speaker_boost': True,
    }


_ELEVENLABS_WS_CHUNK_SCHEDULE = [120, 160, 250, 290]


def _elevenlabs_ws_base_url(base_url: str | None) -> str:
    raw = (base_url or "https://api.elevenlabs.io").strip().rstrip("/")
    if raw.startswith("https://"):
        return "wss://" + raw[len("https://"):]
    if raw.startswith("http://"):
        return "ws://" + raw[len("http://"):]
    if raw.startswith("wss://") or raw.startswith("ws://"):
        return raw
    return "wss://" + raw


def elevenlabs_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None):
    """ElevenLabs TTS worker - WebSocket stream-input PCM output."""
    from urllib.parse import urlencode

    normalized_voice_id = _normalize_elevenlabs_voice_id(voice_id)
    options = _get_elevenlabs_options(base_url)
    output_format = options['output_format']
    if not _is_elevenlabs_pcm_output_format(output_format):
        _enqueue_error(response_queue, {
            "code": "ELEVENLABS_OUTPUT_FORMAT_UNSUPPORTED",
            "provider": "elevenlabs",
            "message": f"ElevenLabs TTS worker requires PCM output, got {output_format!r}",
        })
        response_queue.put(("__ready__", False))
        return

    ws_base_url = _elevenlabs_ws_base_url(options['base_url'])
    ws_url = f"{ws_base_url}/v1/text-to-speech/{normalized_voice_id}/stream-input"
    ws_params = urlencode({
        "model_id": options['model'],
        "output_format": output_format,
    })
    ws_url = f"{ws_url}?{ws_params}"
    chunk_schedule = list(_ELEVENLABS_WS_CHUNK_SCHEDULE)
    pcm_sample_rate = _parse_elevenlabs_pcm_sample_rate(output_format)

    def _build_voice_settings() -> dict:
        return {
            "stability": options['stability'],
            "similarity_boost": options['similarity_boost'],
            "style": options['style'],
            "use_speaker_boost": options['use_speaker_boost'],
            "speed": 1.0,
        }

    async def async_worker():
        ws = None
        receive_task = None
        current_speech_id = None
        response_finished = asyncio.Event()
        text_done_sent = False
        resampler = None
        pending_text: list[str] = []
        pending_text_sid: str | None = None

        def _reset_session_metrics() -> None:
            nonlocal response_finished, text_done_sent, resampler
            response_finished = asyncio.Event()
            text_done_sent = False
            resampler = (
                soxr.ResampleStream(pcm_sample_rate, 48000, 1, dtype='float32')
                if pcm_sample_rate != 48000
                else None
            )

        async def _close_ws(send_final_empty: bool = False, wait_for_final: bool = False) -> None:
            nonlocal ws, receive_task, text_done_sent
            if ws is not None:
                if send_final_empty and not text_done_sent:
                    try:
                        await ws.send(json.dumps({"text": ""}))
                        text_done_sent = True
                    except Exception as exc:
                        logger.debug("ElevenLabs WS final empty send failed: %s", exc)
                if wait_for_final:
                    try:
                        await asyncio.wait_for(response_finished.wait(), timeout=30.0)
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(ws.close(), timeout=0.5)
                except Exception:
                    pass
            ws = None
            if receive_task and not receive_task.done():
                receive_task.cancel()
                try:
                    await receive_task
                except (asyncio.CancelledError, Exception):
                    pass
            receive_task = None

        async def _open_ws(speech_id: str) -> None:
            nonlocal ws, receive_task, current_speech_id
            if not normalized_voice_id:
                raise RuntimeError("ElevenLabs voice_id is not configured")
            if not audio_api_key:
                raise RuntimeError("ElevenLabs API key is not configured")
            ws = await websockets.connect(
                ws_url,
                additional_headers={"xi-api-key": audio_api_key},
                ping_interval=None,
                close_timeout=0.5,
                max_size=10 * 1024 * 1024,
            )
            _reset_session_metrics()
            current_speech_id = speech_id
            receive_task = asyncio.create_task(_receive_ws_messages(speech_id))
            init_payload = {
                "text": " ",
                "voice_settings": _build_voice_settings(),
                "generation_config": {
                    "chunk_length_schedule": chunk_schedule,
                },
                "xi_api_key": audio_api_key,
            }
            await ws.send(json.dumps(init_payload))

        async def _receive_ws_messages(speech_id: str) -> None:
            try:
                async for message in ws:
                    audio_bytes = None
                    is_final = False
                    payload = None

                    if isinstance(message, bytes):
                        if message[:1] == b"{":
                            try:
                                payload = json.loads(message.decode("utf-8", errors="replace"))
                            except Exception:
                                payload = None
                        if payload is None:
                            audio_bytes = message
                    else:
                        try:
                            payload = json.loads(message)
                        except Exception:
                            preview = message if len(message) < 200 else message[:200] + "...<truncated>"
                            logger.warning("ElevenLabs WS recv non-JSON: %s", preview)
                            continue

                    if payload is not None:
                        event_type = payload.get("type")
                        audio_b64 = payload.get("audio") or payload.get("data") or payload.get("delta") or ""
                        if audio_b64:
                            try:
                                audio_bytes = base64.b64decode(audio_b64)
                            except Exception as exc:
                                logger.warning("ElevenLabs WS audio decode failed: %s", exc)
                                audio_bytes = None
                        is_final = bool(
                            payload.get("isFinal")
                            or payload.get("is_final")
                            or payload.get("final")
                            or event_type in {"final", "audio.done"}
                        )
                        if event_type == "error":
                            _enqueue_error(response_queue, {
                                "code": "API_REQUEST_FAILED",
                                "provider": "elevenlabs",
                                "message": f"ElevenLabs TTS API error: {payload}",
                            })
                            continue
                        if not audio_bytes and not is_final:
                            preview = message if isinstance(message, str) else repr(message[:200])
                            logger.debug(
                                "ElevenLabs WS recv unknown event type=%r raw=%s",
                                event_type,
                                preview,
                            )
                            continue

                    if audio_bytes:
                        usable_len = len(audio_bytes) - (len(audio_bytes) % 2)
                        if usable_len <= 0:
                            continue
                        if usable_len < len(audio_bytes):
                            audio_bytes = audio_bytes[:usable_len]
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                        response_queue.put(_resample_audio(audio_array, pcm_sample_rate, 48000, resampler))
                    if is_final:
                        response_finished.set()
                        break
            except websockets.exceptions.ConnectionClosed as exc:
                if exc.code != 1000:
                    logger.info(
                        "ElevenLabs WS closed: speech_id=%s code=%s reason=%r",
                        speech_id,
                        exc.code,
                        exc.reason,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("ElevenLabs WS receive failed: %s", exc)
            finally:
                response_finished.set()

        async def _send_text(text: str, speech_id: str, *, final: bool = False) -> None:
            if ws is None:
                raise RuntimeError("ElevenLabs WS is not connected")
            payload = {"text": text}
            if text and text.strip():
                payload["try_trigger_generation"] = True
            if final and text:
                payload["flush"] = True
            await ws.send(json.dumps(payload))

        async def _ensure_session(speech_id: str) -> None:
            nonlocal current_speech_id, pending_text_sid
            if current_speech_id != speech_id or ws is None:
                wait_for_previous_final = (
                    ws is not None
                    and text_done_sent
                    and not response_finished.is_set()
                )
                await _close_ws(send_final_empty=False, wait_for_final=wait_for_previous_final)
                if pending_text and pending_text_sid not in (None, speech_id):
                    logger.debug(
                        "ElevenLabs WS dropping stale pending text: pending_sid=%s current_sid=%s len=%d",
                        pending_text_sid,
                        speech_id,
                        sum(len(part) for part in pending_text),
                    )
                    pending_text.clear()
                    pending_text_sid = None
                await _open_ws(speech_id)

        try:
            if not normalized_voice_id:
                _enqueue_error(response_queue, {
                    "code": "TTS_VOICE_ID_MISSING",
                    "provider": "elevenlabs",
                    "message": "ElevenLabs voice_id is not configured",
                })
                response_queue.put(("__ready__", False))
                return
            if not audio_api_key:
                _enqueue_error(response_queue, {
                    "code": "API_KEY_MISSING",
                    "provider": "elevenlabs",
                    "message": "ElevenLabs API key is not configured",
                })
                response_queue.put(("__ready__", False))
                return
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
                    await _close_ws(send_final_empty=False, wait_for_final=False)
                    current_speech_id = None
                    pending_text.clear()
                    pending_text_sid = None
                    continue

                if sid is None:
                    if pending_text and pending_text_sid is not None:
                        target_sid = pending_text_sid
                        try:
                            if ws is None or current_speech_id != target_sid:
                                await _ensure_session(target_sid)
                            if ws is not None and current_speech_id == target_sid:
                                sent_text = "".join(pending_text)
                                await _send_text(sent_text, current_speech_id)
                                _record_tts_telemetry(options['model'], len(sent_text))
                        except Exception as exc:
                            logger.warning("ElevenLabs WS flush pending text failed: %s", exc)
                            _enqueue_error(response_queue, {
                                "code": "API_REQUEST_FAILED",
                                "provider": "elevenlabs",
                                "message": f"ElevenLabs pending text flush failed: {exc}",
                            })
                            response_queue.put(("__reconnecting__", "TTS_RECONNECTING"))
                            await _close_ws(send_final_empty=False, wait_for_final=False)
                            current_speech_id = None
                            continue
                        pending_text.clear()
                        pending_text_sid = None
                    # 只发 final empty，让 receive_task 在后台继续把剩余音频抽完；
                    # 真正的 close 由下一个 sid 切换 / __interrupt__ / shutdown 触发，
                    # 避免主循环在这里阻塞最长 30s 拖慢下一句 utterance 首音延迟喵。
                    if ws is not None and not text_done_sent:
                        try:
                            await ws.send(json.dumps({"text": ""}))
                            text_done_sent = True
                        except Exception as exc:
                            logger.debug("ElevenLabs WS final empty send failed: %s", exc)
                    current_speech_id = None
                    pending_text.clear()
                    pending_text_sid = None
                    continue

                if tts_text and tts_text.strip():
                    payload_text = tts_text
                    if pending_text and pending_text_sid == sid:
                        payload_text = "".join(pending_text) + tts_text
                        pending_text.clear()
                        pending_text_sid = None
                    elif pending_text and pending_text_sid not in (None, sid):
                        logger.debug(
                            "ElevenLabs WS dropping cross-utterance pending text: pending_sid=%s current_sid=%s len=%d",
                            pending_text_sid,
                            sid,
                            sum(len(part) for part in pending_text),
                        )
                        pending_text.clear()
                        pending_text_sid = None
                    try:
                        await _ensure_session(sid)
                    except Exception as exc:
                        logger.warning("ElevenLabs WS ensure session failed: %s", exc)
                        pending_text.append(payload_text)
                        pending_text_sid = sid
                        await _close_ws(send_final_empty=False, wait_for_final=False)
                        current_speech_id = None
                        continue
                    try:
                        await _send_text(payload_text, current_speech_id)
                        _record_tts_telemetry(options['model'], len(payload_text))
                    except Exception as exc:
                        logger.warning("ElevenLabs WS send text failed: %s", exc)
                        pending_text.append(payload_text)
                        pending_text_sid = sid
                        await _close_ws(send_final_empty=False, wait_for_final=False)
                        current_speech_id = None

        except Exception as exc:
            logger.error("ElevenLabs WS Worker error: %s", exc, exc_info=True)
            response_queue.put(("__ready__", False))
        finally:
            try:
                await _close_ws(send_final_empty=False, wait_for_final=False)
            except Exception:
                pass

    try:
        asyncio.run(async_worker())
    except Exception as exc:
        logger.error("ElevenLabs WS Worker startup failed: %s", exc, exc_info=True)
        response_queue.put(("__ready__", False))


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
            # 即便不合成音频也上报字符数 + 调用次数，方便分析"配置成无 TTS"
            # 的用户产生了多少假装合成的请求；只传 len()，不传原文。
            if tts_text:
                _record_tts_telemetry("dummy", len(tts_text))
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


_XAI_CUSTOM_VOICE_PATTERN = re.compile(r'^[a-z0-9]{8}$')


def _grok_voice_id_is_xai_custom(voice_id: str) -> bool:
    """判断 voice_id 是否真的是 xAI 自定义 voice 而非 alias-clone collision /
    残留的非-xAI id。

    要 short-circuit 到 grok worker，voice_id 必须满足下列其一：
      (a) 是 grok 词表（canonical id 或 alias）且 canonical 没被任何 voice
          storage 槽克隆 —— 走 grok 内置音色路径；
      (b) 不是词表内容，但形如 xAI 自定义 voice 的 8-char lowercase
          alphanumeric id（POST /v1/custom-voices 返回的格式）。

    场景 (a) 防 alias collision：用户把克隆音色命名为 grok canonical id（例如
    'leo'）后在 UI 上选 alias（例如 'male'，会 normalize 到 'leo'）。
    ``core._has_custom_tts()`` 走 ``resolve_native_voice_for_routing`` 用
    canonical name 当 voice_id_exists 探针，命中 collision 后会让
    has_custom_voice=True 进入这里，但 ``_get_voice_meta(raw_voice_id)`` 是
    None（用户存的是 canonical 'leo'，不是 alias 'male'）。直接路由到 grok
    worker 的话 worker 会 normalize 回 'leo' 内置音色，悄悄绕过用户克隆。
    跨槽检查与 ``core._has_custom_tts()`` 对齐：那边用
    ``voice_id_exists_in_any_storage`` 跨所有 API-key 槽位查找（用户可能在
    qwen 槽克隆了 'leo' 而当前 session 是 grok）。

    场景 (b) 防"无 meta 但不是 xAI"误路由：``voice_meta is None`` 也可能是
    远端 cosyvoice clone 成功但本地 meta 丢失，或历史遗留的非-xAI voice id。
    旧设计无脑短路到 grok worker，xAI 会拒绝这些非 8-char id。改为只匹配
    xAI custom 实际格式，让其他 unknown id 回 cosyvoice fallback。
    """
    if not voice_id:
        return False
    try:
        from utils.grok_tts_voices import normalize_grok_tts_voice
    except Exception:
        # 没装 grok adapter — 保守要求 xAI custom 格式才路由
        return bool(_XAI_CUSTOM_VOICE_PATTERN.match(voice_id))
    canonical, recognized = normalize_grok_tts_voice(voice_id)
    if recognized:
        # alias / canonical → collision check (current + 跨槽)
        if _get_voice_meta(canonical) is not None:
            return False
        try:
            if get_config_manager().voice_id_exists_in_any_storage(canonical):
                return False
        except Exception:
            pass
        return True
    # 不识别 → 必须形如 xAI custom voice id 才路由到 grok
    return bool(_XAI_CUSTOM_VOICE_PATTERN.match(voice_id))


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
    try:
        core_cfg = cm.get_core_config() or {}
    except Exception:
        core_cfg = {}

    if core_cfg.get('DISABLE_TTS', False):
        logger.info("TTS disabled; using dummy TTS worker")
        return dummy_tts_worker, None, None

    tts_provider = str(core_cfg.get('TTS_PROVIDER') or core_cfg.get('ttsProvider') or '').strip().lower()
    assist_api_type = str(core_cfg.get('assistApi') or '').strip().lower()
    try:
        _raw_cfg_for_route = cm.load_json_config('core_config.json', {})
        _tts_provider_sel = (_raw_cfg_for_route.get('ttsModelProvider') or '').strip()
    except Exception as _e:
        _raw_cfg_for_route = {}
        _tts_provider_sel = ''
        logger.warning(f"读取 ttsModelProvider 失败，跳过 vllm_omni 优先检查: {_e}")

    _tts_config_for_route = None
    try:
        _tts_config_for_route = cm.get_model_api_config('tts_custom')
        if _tts_config_for_route.get('is_custom') and core_cfg.get('GPTSOVITS_ENABLED', False):
            return gptsovits_tts_worker, None, 'gptsovits'
    except Exception as e:
        logger.warning(f'TTS调度器检查报告:{e}')

    # 用户在 TTS 下拉里显式选 vllm_omni 时，应优先于克隆音色 / assistApi
    # fallback；但 GPT-SoVITS enabled 是显式本地 TTS 开关，仍在上方优先。
    if _as_bool(core_cfg.get('ENABLE_CUSTOM_API'), False) and _tts_provider_sel == 'vllm_omni':
        vllm_url = (_raw_cfg_for_route.get('ttsModelUrl') or '').strip() or VLLM_OMNI_DEFAULT_BASE_URL
        vllm_model = (_raw_cfg_for_route.get('ttsModelId') or '').strip() \
            or VLLM_OMNI_DEFAULT_MODEL
        vllm_voice = (_raw_cfg_for_route.get('ttsVoiceId') or '').strip() \
            or 'default'
        vllm_key = (_raw_cfg_for_route.get('ttsModelApiKey') or '')
        # 修复 PR #1764 review 第三轮 #3（CodeRabbit Major）：跨 provider 凭证泄漏防护
        # 若用户没为 vllm_omni 单独配置 key，必须返回空字符串而非 None，
        # 否则 core.py 中 `api_key = api_key_override or tts_config['api_key']`
        # 会 fallback 到默认 TTS provider（Qwen/Gemini/Step/OpenAI/Grok）的 key，
        # 进而把别家 provider 的凭证 Bearer 发送到用户配置的 vLLM-Omni endpoint。
        # 用空字符串 + core.py 中 provider_key == 'vllm_omni' 的特判共同保证：
        # vllm_omni 显式无 key 时不允许通用 fallback，本地 vLLM 服务通常无需 Auth。
        logger.info(
            "[get_tts_worker] 用户选择 vllm_omni provider，绕过克隆/原生 TTS 路由 "
            "(core_api_type=%s, has_custom_voice=%s, key_present=%s)",
            core_api_type, has_custom_voice, bool(vllm_key),
        )
        worker = partial(
            vllm_omni_tts_worker,
            base_url=vllm_url, model=vllm_model, voice=vllm_voice,
        )
        return worker, vllm_key, 'vllm_omni'

    # voice_meta 提到 outer scope：cosyvoice 分支也需要它来跟"已存 clone"区分
    # "xAI 自定义 voice / 未知 voice"。MiniMax / ElevenLabs 分支保持嵌套以保留现有日志。
    voice_meta = None

    # 优先检查克隆音色 provider（MiniMax / ElevenLabs / 阿里 CosyVoice）
    if has_custom_voice and voice_id:
        voice_meta = _get_voice_meta(voice_id)
        if voice_meta is None:
            # 本地元数据缺失 — 可能是本地 TTS 音色（GPT-SoVITS / CosyVoice local），
            # 远端 clone 成功但本地保存失败，或 xAI 自定义 voice。
            # 不要在这里 short-circuit，让下面的 tts_custom（GPT-SoVITS / local
            # CosyVoice）分支先有机会匹配 — grok 短路放到 cosyvoice 块里。
            logger.debug("克隆音色 %s 无本地元数据，跳过 MiniMax/ElevenLabs 检测", voice_id)
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
        elif voice_meta.get('provider') == 'elevenlabs':
            logger.info("检测到 ElevenLabs 克隆音色: %s，使用 ElevenLabs TTS Worker", voice_id)
            elevenlabs_options = _get_elevenlabs_options()
            base_url = voice_meta.get('elevenlabs_base_url') or elevenlabs_options['base_url']
            worker = partial(elevenlabs_tts_worker, base_url=base_url)
            return worker, _resolve_elevenlabs_api_key(cm), 'elevenlabs'
        elif voice_meta.get('provider') in ('cosyvoice', 'cosyvoice_intl'):
            provider = voice_meta.get('provider') or 'cosyvoice'
            runtime = cm.get_cosyvoice_clone_runtime(provider)
            runtime_key = (runtime.get('api_key') or '').strip()
            # provider=='cosyvoice_intl' 必须用 intl 的 key 调 intl 端点。runtime_key
            # 缺失时如果只返回 None，core.py 会用 `api_key_override or tts_config['api_key']`
            # 兜底到 tts_custom 槽位的国内 key，结果拿国内 key 打 intl 端点，每次
            # utterance 都吃一次上游 401 — 比直接 dummy 静音更难排查。
            if provider == 'cosyvoice_intl' and not runtime_key:
                logger.warning(
                    "阿里国际版 CosyVoice 克隆音色 %s 选中，但 intl key 缺失，"
                    "改用 dummy TTS worker 避免用错凭证打 intl 端点", voice_id)
                return dummy_tts_worker, None, None
            logger.info("检测到阿里 CosyVoice 克隆音色: %s (provider=%s)，使用 CosyVoice TTS Worker",
                        voice_id, provider)
            return cosyvoice_vc_tts_worker, (runtime_key or None), 'cosyvoice'

    if tts_provider == 'mimo' or assist_api_type == 'mimo':
        mimo_base_url = core_cfg.get('OPENROUTER_URL') if assist_api_type == 'mimo' else None
        mimo_api_key = (cm.get_tts_api_key('mimo') or '').strip()
        if not mimo_api_key:
            logger.warning(
                "MiMo TTS 已选中但 MiMo API Key 缺失，改用 dummy TTS worker 避免复用主 TTS Key")
            return dummy_tts_worker, None, None
        return (
            partial(mimo_tts_worker, base_url=mimo_base_url),
            mimo_api_key,
            'mimo',
        )

    # core_api_type 命中 native voice provider + 用户选了该 provider 的原生声线
    # (e.g. Gemini Puck/Leda/中文男) 时优先走原生 worker，不能被 has_custom_voice=False
    # 的 GPT-SoVITS / local CosyVoice fallthrough 拦截 —— _has_custom_tts 已经判断
    # voice_id 不是用户克隆音色，这里 has_custom_voice 必为 False，是用户显式选择的
    # 原生路径，应当尊重该选择喵。api_key 由 provider 注册的 resolver 提供
    # (Gemini 用 CORE_API_KEY；若 fallback 到 get_model_api_config('tts_default')
    # 会拿到自定义 TTS 的 key，鉴权必失败)。
    # 显式选择 MiMo 时已在上方短路，避免 Gemini/Grok 等 core-native voice
    # 覆盖 MiMo 的辅助 API TTS 路由。
    if not has_custom_voice:
        native = get_native_tts_worker(core_api_type, cm, voice_id)
        if native is not None:
            return native

    try:
        tts_config = cm.get_model_api_config('tts_custom')
        base_url = tts_config.get('base_url') or ''

        if tts_config.get('is_custom'):
            gsv_enabled = core_cfg.get('GPTSOVITS_ENABLED', False)
            if gsv_enabled:
                return gptsovits_tts_worker, None, 'gptsovits'
    except Exception as e:
        logger.warning(f'TTS调度器检查报告:{e}')

    # 如果有自定义克隆音色，使用 CosyVoice（阿里云）
    # 必须同时有有效的 voice_id 且不是免费预设音色，否则 fallthrough 到默认 TTS
    # 注：core.py 的 _has_custom_tts 对 core_api_type=='gemini' + Gemini voice 短路返回 False，
    # 仅当 voice_id 不在用户已克隆音色列表里时才生效；同名克隆 voice (例如自己上传的 Puck)
    # 仍会保留 has_custom_voice=True 进入此分支。
    if has_custom_voice and voice_id:
        from utils.api_config_loader import get_free_voices
        if voice_id in set(get_free_voices().values()):
            logger.info("voice_id '%s' 是免费预设音色，跳过 CosyVoice，使用默认 TTS", voice_id)
        elif core_api_type == 'grok' and voice_meta is None and _grok_voice_id_is_xai_custom(voice_id):
            # grok session + voice 不是已存 clone（voice_meta=None）+ 不是 free preset
            # + 不是 alias 撞用户克隆 → 必然是 xAI 自定义 voice（8-char lowercase
            # alphanumeric，POST /v1/custom-voices 返回的 id）。走 grok worker
            # 用 xAI 端点合成，api_key 显式给 CORE_API_KEY（has_custom=True 默认
            # 从 tts_custom 槽取凭证，对 xAI 是错凭证）。voice_meta 非 None 的
            # cosyvoice clone 不进这分支，即使 core_api='grok' 也保留 cosyvoice 路径。
            # tts_custom (GPT-SoVITS / local CosyVoice) 已经在前面的 try 块里短路
            # 返回，到不了这里。`_grok_voice_id_is_xai_custom` 还会拦下 alias
            # collision：用户克隆了 canonical voice 'leo' 但输入 alias 'male' 时，
            # core._has_custom_tts 会因 collision 把 has_custom_voice 置 True，
            # 这里要识别出来转走 cosyvoice，否则 grok worker 会把 alias normalize
            # 回内置 voice，悄悄绕过用户的克隆。
            grok_api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
            return grok_streaming_tts_worker, grok_api_key, 'grok'
        else:
            return cosyvoice_vc_tts_worker, None, 'cosyvoice'

    # 没有自定义音色时，使用与 core_api 匹配的默认 TTS
    if core_api_type in ('qwen', 'qwen_intl'):
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
    elif core_api_type == 'grok':
        # default 段 fallthrough（has_custom=True + free preset voice 时也会到这里）
        # 也必须显式给 CORE_API_KEY override —— has_custom=True 时 _start_tts_thread
        # 默认从 tts_custom 槽取凭证，对 xAI 是错的鉴权 key（往往是 cosyvoice 或
        # qwen 的 ASSIST key）。与 _resolve_grok_native_tts_worker / cosyvoice 上面
        # 的 grok short-circuit 同源。
        grok_api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
        return grok_streaming_tts_worker, grok_api_key, 'grok'
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
                    _record_tts_telemetry("local_cosyvoice", len(tts_text))
                    # TTS 文本原文不写 logger
                    logger.debug(f"发送合成片段 (len={len(tts_text)} chars)")
                    print(f"发送合成片段: {tts_text}")
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
