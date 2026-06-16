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

"""Zhipu CogTTS worker."""

import numpy as np
import soxr
import json
import base64

from .._infra import _enqueue_error, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def cogtts_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """Zhipu AI CogTTS worker — per-sentence synthesis, SSE streaming audio output."""
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
                    """Detect the leading beep watermark; returns the number of samples to trim (0 = not detected).

                    Detection strategy: look for a short high-frequency pulse (beep) within the first 1.5s.
                    Beep signature: a short-time energy spike + a high-frequency ratio significantly above speech.
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
                    """Parse one SSE data line and enqueue the audio."""
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
