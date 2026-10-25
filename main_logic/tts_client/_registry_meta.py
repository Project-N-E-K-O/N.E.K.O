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

# ─── TTS Provider Metadata Registry ─────────────────────────────────────────
#
# TTS providers fall into three architectural categories:
#
# ┌─────────────┬──────────────┬──────────────┬──────────────────────────────┐
# │ Category      │ Input method      │ Output method      │ Members                       │
# ├─────────────┼──────────────┼──────────────┼──────────────────────────────┤
# │ ws_bistream   │ WS streaming push │ WS streaming reply │ step, qwen, cosyvoice         │
# │ http_sentence │ HTTP per sentence │ SSE/JSON streaming │ cogtts, gemini, openai,       │
# │               │                   │ or one-shot        │ custom, minimax               │
# │ local         │ Provider-specific │ Provider-specific  │ gptsovits, local_cosyvoice    │
# └─────────────┴──────────────┴──────────────┴──────────────────────────────┘
#
# ws_bistream: Send text fragments as they arrive; the server handles concatenation and synthesis scheduling.
#               The client does not split sentences. This gives the lowest first-audio latency.
#               WS protocols differ substantially by provider (event names, handshake flow,
#               completion signals), so each has an independent implementation and main loop.
#
# http_sentence: The client uses SentenceBuffer to split text at punctuation and sends a request once a sentence is ready.
#                It shares the _non_bistream_tts_main_loop main loop and
#                _run_sentence_tts_worker skeleton; each provider only needs to supply
#               async setup() -> (synthesize_fn, cleanup_fn)。
#
# local:         Connects to a local service (GPT-SoVITS / local CosyVoice); protocols and
#                deployment are specialized, so each has an independent implementation.

from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True, slots=True)
class TTSProviderMeta:
    """Architectural metadata of a TTS provider, for documentation and unified lookups."""
    name: str
    category: Literal["ws_bistream", "http_sentence", "local"]
    protocol: str                   # For example: "WebSocket", "HTTP POST + SSE", "HTTP POST + JSON"
    input_streaming: bool           # Whether input is streamed (text fragments sent individually)
    output_streaming: bool          # Whether output is streamed (audio returned in chunks)
    client_sentence_split: bool     # Whether the client splits sentences
    audio_format: str               # Raw audio format, e.g. "PCM 24kHz", "OGG OPUS 48kHz"
    notes: str = ""                 # Special notes
    # Workers recognize TTS_SOFT_FLUSH_SENTINEL: when text is idle, synthesize the buffered trailing sentence first,
    # then continue speaking under the same speech_id. Core sends this sentinel only to providers marked True.
    soft_flush: bool = False

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
              "首包聚合 1KB + 后续聚合 4KB；空闲 15s 主动 complete；"
              "服务端把尾句扣到 FINISH 才合成，故认 core 的软 flush 哨兵",
        soft_flush=True,
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
    "glm_tts": TTSProviderMeta(
        name="glm_tts",
        category="http_sentence",
        protocol="HTTP POST + SSE (base64 音频块)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="GLM 克隆音色（voice_meta.provider 选中）；复用 cogtts worker，"
              "voice=复刻音色 ID；最大 1024 字符/句；首包水印检测与裁剪",
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
    "custom": TTSProviderMeta(
        name="custom",
        category="http_sentence",
        protocol="HTTP(S) POST /v1/audio/speech (streaming PCM response)",
        input_streaming=False,
        output_streaming=True,
        client_sentence_split=True,
        audio_format="PCM 24kHz → resample 48kHz",
        notes="用户配置的 OpenAI-compatible TTS endpoint",
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
        protocol="WebSocket (wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input)",
        input_streaming=True,
        output_streaming=True,
        client_sentence_split=False,
        audio_format="PCM 24kHz -> resample 48kHz",
        notes="ElevenLabs Text-to-Dialogue stream with eleven_v4_turbo",
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
