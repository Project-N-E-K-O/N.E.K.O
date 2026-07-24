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

"""TTS provider architectural metadata registry (documentation/lookup)."""

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
    """Architectural metadata of a TTS provider, for documentation and unified lookups."""
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
    "doubao_tts": TTSProviderMeta(
        name="doubao_tts",
        category="http_sentence",
        protocol="HTTP POST /api/v3/tts/unidirectional (NDJSON base64 audio chunks)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="WAV/PCM 24kHz → resample 48kHz",
        notes="Doubao OpenSpeech seed-icl-2.0；风格控制走 additions.context_texts",
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
