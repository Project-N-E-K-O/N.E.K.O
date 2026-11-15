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

def cogtts_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None, model="cogtts"):
    """Zhipu AI CogTTS worker — per-sentence synthesis, SSE streaming audio output.

    base_url is optional (defaults to the official open.bigmodel.cn endpoint); the
    GLM clone resolver passes the glm_base_url persisted at registration time so
    historical clone entries keep synthesizing against their registration endpoint.
    model defaults to the native "cogtts"; the GLM clone resolver passes "glm-tts",
    the only model the official /audio/speech docs list for cloned voices."""
    import httpx

    if not voice_id:
        voice_id = "tongtong"

    from utils.glm_tts import GLM_TTS_SPEECH_MODEL, glm_speech_url

    tts_url = glm_speech_url(base_url)
    # Classify telemetry by provider: GLM cloned voices use glm_tts, native CogTTS uses cogtts.
    telemetry_provider = "glm_tts" if model == GLM_TTS_SPEECH_MODEL else "cogtts"

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
                "model": model,
                "input": text[:1024],  # CogTTS supports at most 1024 characters.
                "voice": voice_id,
                "response_format": "pcm",
                "encode_format": "base64",
                "speed": 1.0,
                "volume": 1.0,
                "stream": True,
                # Official watermark_enabled=false works only for accounts completing watermark-removal management
                # in the Zhipu console; otherwise the server ignores it and adds watermarks. Local first-chunk
                # beep detection/cropping remains the fallback; both layers can coexist without affecting ordinary users.
                "watermark_enabled": False,
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

                # The CogTTS payload actually sends only text[:1024] (hard truncation at line 2407; upstream
                # API limit 1024 characters). Record min rather than len(text) in telemetry, or excessively
                # long inputs overestimate actual billable/uploaded character counts.
                _record_tts_telemetry(telemetry_provider, min(len(text), 1024))
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

                    frame_size = int(sr * 0.01)   # 10 ms frames.
                    hop = frame_size
                    hf_threshold = 0.55            # High-frequency energy ratio threshold.
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

                    # Crop through the last beep frame plus a 5 ms safety margin.
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

                        # First audio chunk: detect and crop watermark beeps.
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
                                # Notify the frontend that a watermark was detected.
                                response_queue.put((
                                    "__warning__",
                                    json.dumps({
                                        "code": "TTS_WATERMARK_DETECTED",
                                        "level": "info",
                                    }),
                                ))
                                # Fade in for 10 ms after cropping to avoid clicks.
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

                # Process trailing residue; the final server message may lack a newline.
                if buffer.strip():
                    _handle_sse_line(buffer)

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="CogTTS")


def _glm_voice_meta_is_clone(vm) -> bool:
    return bool(vm and vm.get("provider") == "glm_tts")


def _glm_clone_is_selected(ctx) -> bool:
    """GLM cloned voices are selected via voice_meta.provider (dual to doubao).

    Deliberately does NOT consider config selection (ttsModelProvider=='glm_tts'):
    the native core_api_type=='glm' CogTTS path is still handled by
    get_tts_worker's core branch (its key comes from the tts_custom slot). If
    this entry also claimed config selection, the registry priority would
    intercept un-cloned native users too and re-authenticate them with
    assistApiKeyGlm, breaking the existing GLM TTS behavior."""
    return _glm_voice_meta_is_clone(ctx.voice_meta)


def _glm_clone_resolve(ctx):
    from functools import partial

    from utils.glm_tts import GLM_TTS_DEFAULT_BASE_URL, GLM_TTS_SPEECH_MODEL

    from .dummy import dummy_tts_worker

    vm = ctx.voice_meta or {}
    api_key = (ctx.cm.get_tts_api_key("glm_tts") or "").strip()
    if "***" in api_key:
        api_key = ""
    if not api_key:
        logger.warning("GLM 克隆音色已选中但 API Key 缺失，改用 dummy TTS worker")
        return dummy_tts_worker, None, None
    # The cogtts worker's voice parameter officially supports cloned voices; synthesize directly with the cloned ID,
    # using the same SSE streaming and watermark-detection path. voice_meta retains the registration glm_base_url;
    # pass it to the worker so historical entries keep their registration endpoint, falling back to the official default if absent.
    base_url = str(vm.get("glm_base_url") or "").strip() or GLM_TTS_DEFAULT_BASE_URL
    worker = partial(cogtts_tts_worker, base_url=base_url, model=GLM_TTS_SPEECH_MODEL)
    return worker, api_key, "glm_tts"
