"""Local lightweight TTS server compatible with NEKO local_cosyvoice_worker.

The first integration phase intentionally keeps the wire protocol identical to
``local_cosyvoice_worker``:

1. Client sends config JSON: {"voice": "...", "speed": 1.0}
2. Client streams text JSON chunks: {"text": "..."}
3. Client sends end JSON: {"event": "end"}
4. Server replies with binary PCM s16le chunks at 22050 Hz.

Model support is implemented as optional adapters so this server can start even
when a specific local TTS runtime has not been installed yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


TARGET_SAMPLE_RATE = 22050
CHUNK_BYTES = 4096
SYNTHESIS_MODE = os.getenv("LOCAL_TTS_SYNTHESIS_MODE", "merged").strip().lower() or "merged"
DEVICE_REQUEST = os.getenv("LOCAL_TTS_KOKORO_DEVICE", "").strip().lower() or "auto"
WARMUP_ON_CONNECT = os.getenv("LOCAL_TTS_WARMUP_ON_CONNECT", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STARTUP_WARMUP_ENABLED = os.getenv("LOCAL_TTS_STARTUP_WARMUP", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOG_SYNTHESIS_TEXT = os.getenv("LOCAL_TTS_LOG_TEXT", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("local_tts_server")

app = FastAPI(title="NEKO Local Lightweight TTS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TTSEngine(Protocol):
    """Small interface implemented by every local model adapter."""

    name: str

    def synthesize(self, text: str, *, voice: str, speed: float) -> "SynthesisResult":
        """Return synthesized audio plus runtime metadata."""


@dataclass(frozen=True)
class SynthesisResult:
    pcm: bytes
    sample_rate: int
    device: str = "unknown"


@dataclass(frozen=True)
class VoiceSpec:
    """Parsed voice selector.

    Accepted examples:
    - "kokoro:zf_xiaobei"
    - "melotts:zh"
    - "chattts:default"
    - "涓枃濂? -> falls back to LOCAL_TTS_DEFAULT_MODEL
    """

    model: str
    voice: str


def list_kokoro_voices() -> list[str]:
    model_dir = os.getenv("LOCAL_TTS_KOKORO_MODEL_DIR", "").strip()
    if model_dir:
        voices_dir = Path(model_dir) / "voices"
    else:
        voices_dir = Path(__file__).resolve().parent / "kokoro_models" / "Kokoro-82M-v1.1-zh" / "voices"
    if not voices_dir.is_dir():
        return []
    return sorted(path.stem for path in voices_dir.glob("*.pt") if path.is_file())


def parse_voice(raw_voice: str) -> VoiceSpec:
    default_model = os.getenv("LOCAL_TTS_DEFAULT_MODEL", "kokoro").strip().lower() or "kokoro"
    value = (raw_voice or "").strip()
    if ":" not in value:
        return VoiceSpec(default_model, value or "default")
    model, voice = value.split(":", 1)
    model = model.strip().lower() or default_model
    return VoiceSpec(model, voice.strip() or "default")


def resample_pcm_s16le(pcm: bytes, src_rate: int, dst_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Resample mono s16le PCM with numpy interpolation.

    This keeps the local server dependency-light. NEKO still performs its own
    final 22050 -> 48000 conversion in ``local_cosyvoice_worker``.
    """

    if not pcm or src_rate == dst_rate:
        return pcm

    audio = np.frombuffer(pcm, dtype=np.int16)
    if audio.size == 0:
        return b""

    duration = audio.size / float(src_rate)
    out_size = max(1, int(round(duration * dst_rate)))
    src_x = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    dst_x = np.linspace(0.0, duration, num=out_size, endpoint=False)
    resampled = np.interp(dst_x, src_x, audio.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def read_wav_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise RuntimeError(f"Only 16-bit WAV is supported, got sample_width={sample_width}")

    if channels == 1:
        return frames, sample_rate

    audio = np.frombuffer(frames, dtype=np.int16).reshape(-1, channels)
    mono = audio.mean(axis=1).clip(-32768, 32767).astype(np.int16)
    return mono.tobytes(), sample_rate


class CommandWavEngine:
    """Generic command adapter for Kokoro/MeloTTS/ChatTTS wrappers.

    The command must accept:
      {text_file} {out_file} {voice} {speed}

    Configure per model with:
    - LOCAL_TTS_KOKORO_CMD
    - LOCAL_TTS_MELOTTS_CMD
    - LOCAL_TTS_CHATTTS_CMD
    """

    def __init__(self, name: str, env_var: str):
        self.name = name
        self._env_var = env_var

    def is_configured(self) -> bool:
        return bool(os.getenv(self._env_var, "").strip())

    def synthesize(self, text: str, *, voice: str, speed: float) -> SynthesisResult:
        template = os.getenv(self._env_var, "").strip()
        if not template:
            raise RuntimeError(f"{self._env_var} is required for {self.name}")

        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as text_file:
            text_path = Path(text_file.name)
            text_file.write(text)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
            out_path = Path(wav_file.name)

        try:
            cmd = build_command_args(
                template,
                python=sys.executable,
                text_file=str(text_path),
                out_file=str(out_path),
                voice=voice,
                speed=speed,
            )
            proc = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=float(os.getenv("LOCAL_TTS_ENGINE_TIMEOUT", "120")),
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()
                stdout = (proc.stdout or "").strip()
                details = "\n".join(part for part in (stderr, stdout) if part)
                raise RuntimeError(f"{self.name} command failed with exit code {proc.returncode}: {details[-4000:]}")
            pcm, sample_rate = read_wav_pcm(out_path)
            device_match = re.search(r"\bdevice=([A-Za-z0-9_.:-]+)", proc.stdout or "")
            device = device_match.group(1) if device_match else DEVICE_REQUEST
            return SynthesisResult(pcm=pcm, sample_rate=sample_rate, device=device)
        finally:
            for path in (text_path, out_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass


def build_command_args(template: str, **values) -> list[str]:
    """Format a wrapper command template into argv tokens safely."""

    try:
        parts = shlex.split(template, posix=os.name != "nt")
    except ValueError as exc:
        raise RuntimeError(f"Invalid command template for {values.get('python', 'command')}: {exc}") from exc

    if os.name == "nt":
        normalized: list[str] = []
        for part in parts:
            if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}:
                normalized.append(part[1:-1])
            else:
                normalized.append(part)
        parts = normalized

    return [part.format(**values) for part in parts]


class KokoroEngine:
    """In-process Kokoro adapter.

    The command adapter is useful for bringing up a model quickly, but it pays
    process startup and model loading costs for every sentence. Kokoro is small
    enough to keep resident in the local server, so this adapter loads the model
    lazily once and reuses it across websocket requests.
    """

    name = "kokoro"

    def __init__(self):
        self._lock = threading.RLock()
        self._loaded = False
        self._torch = None
        self._np = np
        self._repo_id = os.getenv("LOCAL_TTS_KOKORO_REPO_ID", "hexgrad/Kokoro-82M-v1.1-zh").strip()
        if not self._repo_id:
            self._repo_id = "hexgrad/Kokoro-82M-v1.1-zh"
        self._model_dir = self._resolve_local_model_dir()
        self._disable_hf_download = os.getenv("LOCAL_TTS_KOKORO_DISABLE_HF_DOWNLOAD", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._model = None
        self._device = "unknown"
        self._pipelines: dict[str, object] = {}
        self._en_pipeline = None

    def _resolve_local_model_dir(self) -> Path | None:
        raw = os.getenv("LOCAL_TTS_KOKORO_MODEL_DIR", "").strip()
        if raw:
            path = Path(raw)
            return path if path.is_dir() else None
        default_dir = Path(__file__).resolve().parent / "kokoro_models" / "Kokoro-82M-v1.1-zh"
        return default_dir if default_dir.is_dir() else None

    def _find_model_file(self) -> Path | None:
        if not self._model_dir:
            return None
        preferred = self._model_dir / "kokoro-v1_1-zh.pth"
        if preferred.is_file():
            return preferred
        candidates = sorted(self._model_dir.glob("*.pth"))
        return candidates[0] if candidates else None

    def _available_local_voices(self) -> set[str]:
        if not self._model_dir:
            return set()
        voices_dir = self._model_dir / "voices"
        if not voices_dir.is_dir():
            return set()
        return {path.stem for path in voices_dir.glob("*.pt") if path.is_file()}

    def _resolve_voice(self, voice: str) -> str:
        if not self._model_dir or voice.endswith(".pt"):
            return voice
        local_voice = self._model_dir / "voices" / f"{voice}.pt"
        return str(local_voice) if local_voice.is_file() else voice

    @staticmethod
    def _infer_lang_code(voice: str) -> str:
        if voice.startswith(("a", "af", "am")):
            return "a"
        if voice.startswith(("b", "bf", "bm")):
            return "b"
        return "z"

    @staticmethod
    def _audio_from_result(result):
        if hasattr(result, "audio"):
            return result.audio
        if isinstance(result, tuple) and result:
            return result[-1]
        return None

    def _audio_to_numpy(self, audio) -> np.ndarray:
        if self._torch is not None and isinstance(audio, self._torch.Tensor):
            audio = audio.detach().cpu().numpy()
        return np.asarray(audio, dtype=np.float32)

    @staticmethod
    def _speed_callable(base_speed: float):
        base = base_speed if base_speed > 0 else 1.0

        def speed_by_len(len_ps: int) -> float:
            speed = 1.0
            if len_ps > 83 and len_ps < 183:
                speed = 1.0 - (len_ps - 83) / 500.0
            elif len_ps >= 183:
                speed = 0.8
            return max(0.5, speed * base)

        return speed_by_len

    def _load(self) -> None:
        if self._loaded:
            return

        try:
            import torch
            from kokoro import KModel
        except ImportError as exc:
            raise RuntimeError(
                'kokoro v1.1-zh deps missing. Start with the Kokoro uv launcher.'
            ) from exc

        requested_device = os.getenv("LOCAL_TTS_KOKORO_DEVICE", "").strip().lower()
        if requested_device == "cuda" and not torch.cuda.is_available():
            logger.warning("Kokoro requested CUDA, but torch.cuda.is_available() is false; using CPU.")
            requested_device = "cpu"
        self._device = requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._torch = torch

        if self._model_dir:
            config_path = self._model_dir / "config.json"
            model_path = self._find_model_file()
            if not config_path.is_file() or model_path is None:
                raise RuntimeError(
                    f"Invalid LOCAL_TTS_KOKORO_MODEL_DIR: {self._model_dir} "
                    "(expected config.json and a .pth model file)"
                )
            self._model = KModel(
                repo_id=self._repo_id,
                config=str(config_path),
                model=str(model_path),
            ).to(self._device).eval()
        else:
            if self._disable_hf_download:
                raise RuntimeError(
                    "Local Kokoro model directory is missing and Hugging Face downloads are disabled. "
                    "This launcher intentionally expects users to place Kokoro model files in "
                    "local_server/local_tts_server/kokoro_models and choose the profile there. "
                    "Set LOCAL_TTS_KOKORO_MODEL_DIR to a local model directory before starting the server."
                )
            self._model = KModel(repo_id=self._repo_id).to(self._device).eval()

        self._loaded = True
        logger.info(
            "Kokoro engine loaded: adapter=in_process repo=%s model_dir=%s device=%s",
            self._repo_id,
            self._model_dir or "<hf-cache>",
            self._device,
        )

    def _get_en_callable(self):
        from kokoro import KPipeline

        if self._en_pipeline is None:
            self._en_pipeline = KPipeline(lang_code="a", repo_id=self._repo_id, model=False)

        def en_callable(text_part: str):
            if text_part == "Kokoro":
                return "kˈOkəɹO"
            if text_part == "Sol":
                return "sˈOl"
            return next(self._en_pipeline(text_part)).phonemes

        return en_callable

    def _get_pipeline(self, lang: str):
        from kokoro import KPipeline

        pipeline = self._pipelines.get(lang)
        if pipeline is not None:
            return pipeline

        en_callable = self._get_en_callable() if lang == "z" else None
        pipeline = KPipeline(
            lang_code=lang,
            repo_id=self._repo_id,
            model=self._model,
            en_callable=en_callable,
        )
        self._pipelines[lang] = pipeline
        return pipeline

    def _normalize_voice(self, voice: str) -> str:
        voice = (voice or "").strip() or os.getenv("LOCAL_TTS_KOKORO_DEFAULT_VOICE", "zf_001")
        available_voices = self._available_local_voices()
        if available_voices and voice not in available_voices:
            fallback_voice = os.getenv("LOCAL_TTS_KOKORO_DEFAULT_VOICE", "zf_001").strip() or "zf_001"
            if fallback_voice not in available_voices:
                fallback_voice = sorted(available_voices)[0]
            logger.warning("Kokoro voice '%s' not found; falling back to '%s'.", voice, fallback_voice)
            voice = fallback_voice
        return voice

    def _synthesize_loaded(self, text: str, *, voice: str, speed: float) -> SynthesisResult:
        voice = self._normalize_voice(voice)
        lang = self._infer_lang_code(voice)
        pipeline_voice = self._resolve_voice(voice)
        effective_speed = self._speed_callable(speed) if lang == "z" else speed
        pipeline = self._get_pipeline(lang)
        inference_mode = self._torch.inference_mode if self._torch is not None else None

        chunks: list[np.ndarray] = []
        if inference_mode is None:
            generator = pipeline(text, voice=pipeline_voice, speed=effective_speed)
            for result in generator:
                audio = self._audio_from_result(result)
                if audio is not None:
                    chunks.append(self._audio_to_numpy(audio))
        else:
            with inference_mode():
                generator = pipeline(text, voice=pipeline_voice, speed=effective_speed)
                for result in generator:
                    audio = self._audio_from_result(result)
                    if audio is not None:
                        chunks.append(self._audio_to_numpy(audio))

        if not chunks:
            raise RuntimeError("No audio generated")

        pcm = np.concatenate(chunks)
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm_int16 = (pcm * 32767.0).astype(np.int16)
        return SynthesisResult(pcm=pcm_int16.tobytes(), sample_rate=24000, device=self._device)

    def preload(self, *, voice: str) -> tuple[str, str]:
        with self._lock:
            self._load()
            normalized_voice = self._normalize_voice(voice)
            self._get_pipeline(self._infer_lang_code(normalized_voice))
            # Resolve the local voice path once so missing voice ids are caught
            # and logged before the first real utterance arrives.
            self._resolve_voice(normalized_voice)
            return self._device, normalized_voice

    def synthesize(self, text: str, *, voice: str, speed: float) -> SynthesisResult:
        with self._lock:
            self._load()
            return self._synthesize_loaded(text, voice=voice, speed=speed)


class FallbackTTSEngine:
    """Try the resident engine first, then use a configured command wrapper."""

    def __init__(self, name: str, primary: TTSEngine, fallback: CommandWavEngine):
        self.name = name
        self._primary = primary
        self._fallback = fallback

    def preload(self, *, voice: str) -> tuple[str, str]:
        preload = getattr(self._primary, "preload", None)
        if not callable(preload):
            return "command", voice
        try:
            return preload(voice=voice)
        except Exception as exc:
            if not self._fallback.is_configured():
                raise
            logger.warning(
                "%s in-process preload failed; command fallback will be used if synthesis is requested: %s",
                self.name,
                exc,
            )
            return "command", voice

    def synthesize(self, text: str, *, voice: str, speed: float) -> SynthesisResult:
        try:
            return self._primary.synthesize(text, voice=voice, speed=speed)
        except Exception as exc:
            if not self._fallback.is_configured():
                raise
            logger.warning(
                "%s in-process synthesis failed; retrying with command fallback: %s",
                self.name,
                exc,
            )
            return self._fallback.synthesize(text, voice=voice, speed=speed)


class ToneEngine:
    """Smoke-test engine used only when LOCAL_TTS_ENABLE_TONE=1."""

    name = "tone"

    def synthesize(self, text: str, *, voice: str, speed: float) -> SynthesisResult:
        duration = min(2.0, max(0.25, len(text) * 0.04))
        samples = int(TARGET_SAMPLE_RATE * duration)
        freq = 440.0 if "high" not in voice else 660.0
        t = np.arange(samples, dtype=np.float32) / TARGET_SAMPLE_RATE
        audio = 0.2 * np.sin(2.0 * math.pi * freq * t)
        return SynthesisResult(
            pcm=(audio * 32767.0).astype(np.int16).tobytes(),
            sample_rate=TARGET_SAMPLE_RATE,
            device="cpu",
        )


def build_engines() -> dict[str, TTSEngine]:
    engines: dict[str, TTSEngine] = {
        "kokoro": FallbackTTSEngine(
            "kokoro",
            KokoroEngine(),
            CommandWavEngine("kokoro", "LOCAL_TTS_KOKORO_CMD"),
        ),
        "melotts": CommandWavEngine("melotts", "LOCAL_TTS_MELOTTS_CMD"),
        "melo": CommandWavEngine("melotts", "LOCAL_TTS_MELOTTS_CMD"),
        "chattts": CommandWavEngine("chattts", "LOCAL_TTS_CHATTTS_CMD"),
    }
    if os.getenv("LOCAL_TTS_ENABLE_TONE", "").strip().lower() in {"1", "true", "yes", "on"}:
        engines["tone"] = ToneEngine()
    return engines


ENGINES = build_engines()
WARMUP_STATUS = {
    "on_connect": WARMUP_ON_CONNECT,
    "startup_enabled": STARTUP_WARMUP_ENABLED,
    "done": False,
    "source": None,
    "engine": None,
    "voice": None,
    "elapsed": None,
    "device": None,
    "error": None,
}


def normalize_preload_result(result, requested_voice: str) -> tuple[str, str]:
    if isinstance(result, tuple) and len(result) >= 2:
        return str(result[0]), str(result[1])
    return str(result), requested_voice


@app.on_event("startup")
async def warmup_default_kokoro_if_explicitly_requested() -> None:
    if not STARTUP_WARMUP_ENABLED:
        logger.info("Startup TTS warmup disabled; local voice warmup will happen on first WS config.")
        return

    engine = ENGINES.get("kokoro")
    preload = getattr(engine, "preload", None)
    if not callable(preload):
        logger.info("Kokoro warmup skipped: kokoro engine has no preload hook.")
        return

    voice = os.getenv("LOCAL_TTS_DEFAULT_VOICE", "kokoro:zf_001").strip() or "kokoro:zf_001"
    spec = parse_voice(voice)
    if spec.model != "kokoro":
        logger.info("Startup Kokoro warmup skipped: configured voice belongs to %s.", spec.model)
        return

    started = time.perf_counter()
    logger.info("Startup Kokoro preload starting: voice=%s", spec.voice)
    try:
        loop = asyncio.get_running_loop()
        preload_result = await loop.run_in_executor(None, lambda: preload(voice=spec.voice))
        device, resolved_voice = normalize_preload_result(preload_result, spec.voice)
        elapsed = time.perf_counter() - started
        WARMUP_STATUS.update(
            {
                "done": True,
                "source": "startup",
                "engine": "kokoro",
                "voice": resolved_voice,
                "elapsed": elapsed,
                "device": device,
                "error": None,
            }
        )
        logger.info(
            "Startup Kokoro preload done: voice=%s device=%s elapsed=%.3fs",
            resolved_voice,
            WARMUP_STATUS["device"],
            elapsed,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        WARMUP_STATUS.update(
            {
                "done": False,
                "source": "startup",
                "engine": "kokoro",
                "voice": spec.voice,
                "elapsed": elapsed,
                "device": getattr(engine, "_device", None),
                "error": str(exc),
            }
        )
        logger.error("Kokoro warmup failed after %.3fs: %s", elapsed, exc, exc_info=True)


async def warmup_engine_for_voice(spec: VoiceSpec) -> None:
    if not WARMUP_ON_CONNECT:
        return

    engine = ENGINES.get(spec.model)
    preload = getattr(engine, "preload", None)
    if engine is None or not callable(preload):
        logger.info("WS warmup skipped: engine=%s has no preload hook.", spec.model)
        return

    started = time.perf_counter()
    logger.info("WS warmup starting: engine=%s voice=%s", spec.model, spec.voice)
    try:
        loop = asyncio.get_running_loop()
        preload_result = await loop.run_in_executor(None, lambda: preload(voice=spec.voice))
        device, resolved_voice = normalize_preload_result(preload_result, spec.voice)
        elapsed = time.perf_counter() - started
        WARMUP_STATUS.update(
            {
                "done": True,
                "source": "ws_config",
                "engine": spec.model,
                "voice": resolved_voice,
                "elapsed": elapsed,
                "device": device,
                "error": None,
            }
        )
        logger.info(
            "WS warmup done: engine=%s requested_voice=%s resolved_voice=%s device=%s elapsed=%.3fs",
            spec.model,
            spec.voice,
            resolved_voice,
            device,
            elapsed,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        WARMUP_STATUS.update(
            {
                "done": False,
                "source": "ws_config",
                "engine": spec.model,
                "voice": spec.voice,
                "elapsed": elapsed,
                "device": None,
                "error": str(exc),
            }
        )
        logger.warning(
            "WS warmup failed: engine=%s voice=%s elapsed=%.3fs error=%s",
            spec.model,
            spec.voice,
            elapsed,
            exc,
        )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "target_sample_rate": TARGET_SAMPLE_RATE,
        "synthesis_mode": SYNTHESIS_MODE,
        "streaming_output": SYNTHESIS_MODE == "streaming",
        "device_request": DEVICE_REQUEST,
        "warmup": WARMUP_STATUS,
        "engines": sorted(ENGINES.keys()),
        "voices": {
            "kokoro": list_kokoro_voices(),
        },
    }


@app.get("/v1/models")
async def list_models():
    return JSONResponse(
        content={
            "object": "list",
            "data": [
                {"id": name, "object": "model", "owned_by": "local_tts"}
                for name in sorted(ENGINES.keys())
            ],
        }
    )


@app.get("/v1/voices")
async def list_voices():
    return JSONResponse(
        content={
            "object": "list",
            "data": {
                "kokoro": [
                    {"id": voice, "voice_id": f"kokoro:{voice}", "name": voice}
                    for voice in list_kokoro_voices()
                ],
            },
        }
    )


async def send_pcm_chunks(websocket: WebSocket, pcm: bytes) -> None:
    for offset in range(0, len(pcm), CHUNK_BYTES):
        await websocket.send_bytes(pcm[offset : offset + CHUNK_BYTES])
        await asyncio.sleep(0)


@app.websocket("/v1/audio/speech/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    text_parts: list[str] = []
    voice = ""
    speed = 1.0

    try:
        config_msg = await websocket.receive_text()
        config = json.loads(config_msg)
        voice = str(config.get("voice") or "").strip()
        try:
            speed = float(config.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0

        logger.info("WS connected: voice=%s speed=%s", voice or "<default>", speed)
        spec = parse_voice(voice)
        await warmup_engine_for_voice(spec)

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            text_chunk = msg.get("text")
            if isinstance(text_chunk, str) and text_chunk:
                text_parts.append(text_chunk)

            if msg.get("event") == "end":
                break

        text = "".join(text_parts).strip()
        if not text:
            await websocket.close()
            return

        engine = ENGINES.get(spec.model)
        if engine is None:
            raise RuntimeError(f"Unsupported local TTS model: {spec.model}")

        if LOG_SYNTHESIS_TEXT:
            logger.info("Synthesis text: %s", text)
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        result = await loop.run_in_executor(
            None,
            lambda: engine.synthesize(text, voice=spec.voice, speed=speed),
        )
        elapsed = time.perf_counter() - started
        pcm = resample_pcm_s16le(result.pcm, result.sample_rate, TARGET_SAMPLE_RATE)
        audio_duration = len(pcm) / (2 * TARGET_SAMPLE_RATE) if pcm else 0.0
        rtf = elapsed / audio_duration if audio_duration > 0 else 0.0
        if LOG_SYNTHESIS_TEXT:
            logger.info(
                "Synthesis done: engine=%s mode=%s output=%s device=%s voice=%s chars=%d "
                "elapsed=%.3fs audio=%.3fs rtf=%.3f text=%s",
                engine.name,
                SYNTHESIS_MODE,
                "streaming" if SYNTHESIS_MODE == "streaming" else "merged",
                result.device,
                spec.voice,
                len(text),
                elapsed,
                audio_duration,
                rtf,
                text,
            )
        else:
            logger.info(
                "Synthesis done: engine=%s mode=%s output=%s device=%s voice=%s chars=%d "
                "elapsed=%.3fs audio=%.3fs rtf=%.3f",
                engine.name,
                SYNTHESIS_MODE,
                "streaming" if SYNTHESIS_MODE == "streaming" else "merged",
                result.device,
                spec.voice,
                len(text),
                elapsed,
                audio_duration,
                rtf,
            )
        await send_pcm_chunks(websocket, pcm)
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("WS disconnected")
    except Exception as exc:
        logger.error("WS TTS error: %s", exc, exc_info=True)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="NEKO local lightweight TTS server")
    parser.add_argument("--host", default=os.getenv("LOCAL_TTS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LOCAL_TTS_PORT", "50000")))
    parser.add_argument("--log-level", default=os.getenv("LOCAL_TTS_LOG_LEVEL", "info"))
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
