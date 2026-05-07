"""Minimal Kokoro v1.1-zh CLI wrapper for local_tts_server.

Usage:
    python kokoro_cli.py <text_file> <out_file> <voice> <speed>

Reads text from <text_file>, synthesizes with kokoro, writes WAV to <out_file>.
"""

from __future__ import annotations

import argparse
import os
import sys
import wave
from pathlib import Path

import numpy as np


DEFAULT_REPO_ID = "hexgrad/Kokoro-82M-v1.1-zh"
DEFAULT_VOICE = "zf_001"
SAMPLE_RATE = 24000
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LOCAL_REPO = SCRIPT_DIR / "kokoro_models" / "Kokoro-82M-v1.1-zh"


def _audio_from_result(result):
    if hasattr(result, "audio"):
        return result.audio
    if isinstance(result, tuple) and result:
        return result[-1]
    return None


def _infer_lang_code(voice: str) -> str:
    # Kokoro uses single-letter lang codes: z=zh, a=en-us, b=en-gb.
    if voice.startswith(("a", "af", "am")):
        return "a"
    if voice.startswith(("b", "bf", "bm")):
        return "b"
    return "z"


def _speed_callable(base_speed: float):
    """Mitigate rushed long Chinese phoneme sequences in v1.1-zh."""

    base = base_speed if base_speed > 0 else 1.0

    def speed_by_len(len_ps: int) -> float:
        speed = 1.0
        if len_ps > 83 and len_ps < 183:
            speed = 1.0 - (len_ps - 83) / 500.0
        elif len_ps >= 183:
            speed = 0.8
        return max(0.5, speed * base)

    return speed_by_len


def _resolve_local_model_dir() -> Path | None:
    raw = os.getenv("LOCAL_TTS_KOKORO_MODEL_DIR", "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_dir() else None
    return DEFAULT_LOCAL_REPO if DEFAULT_LOCAL_REPO.is_dir() else None


def _find_model_file(model_dir: Path) -> Path | None:
    preferred = model_dir / "kokoro-v1_1-zh.pth"
    if preferred.is_file():
        return preferred
    candidates = sorted(model_dir.glob("*.pth"))
    return candidates[0] if candidates else None


def _resolve_voice(voice: str, model_dir: Path | None) -> str:
    if not model_dir:
        return voice
    if voice.endswith(".pt"):
        return voice
    local_voice = model_dir / "voices" / f"{voice}.pt"
    return str(local_voice) if local_voice.is_file() else voice


def _available_local_voices(model_dir: Path | None) -> set[str]:
    if not model_dir:
        return set()
    voices_dir = model_dir / "voices"
    if not voices_dir.is_dir():
        return set()
    return {path.stem for path in voices_dir.glob("*.pt") if path.is_file()}


def synthesize(text_path: str, out_path: str, voice: str, speed: float) -> int:
    try:
        import torch
        from kokoro import KModel, KPipeline
    except ImportError:
        print(
            'kokoro v1.1-zh deps missing. Run: uv pip install "kokoro>=0.8.2" "misaki[zh]>=0.8.2"',
            file=sys.stderr,
        )
        return 1

    text = Path(text_path).read_text(encoding="utf-8").strip()
    if not text:
        print("Empty text file", file=sys.stderr)
        return 1

    model_dir = _resolve_local_model_dir()
    repo_id = os.getenv("LOCAL_TTS_KOKORO_REPO_ID", DEFAULT_REPO_ID).strip() or DEFAULT_REPO_ID
    voice = (voice or "").strip() or os.getenv("LOCAL_TTS_KOKORO_DEFAULT_VOICE", DEFAULT_VOICE)
    available_voices = _available_local_voices(model_dir)
    if available_voices and voice not in available_voices:
        fallback_voice = os.getenv("LOCAL_TTS_KOKORO_DEFAULT_VOICE", DEFAULT_VOICE).strip() or DEFAULT_VOICE
        if fallback_voice not in available_voices:
            fallback_voice = sorted(available_voices)[0]
        print(
            f"Kokoro voice '{voice}' not found in local model dir; falling back to '{fallback_voice}'.",
            file=sys.stderr,
        )
        voice = fallback_voice
    pipeline_voice = _resolve_voice(voice, model_dir)
    lang = _infer_lang_code(voice)
    device = os.getenv("LOCAL_TTS_KOKORO_DEVICE", "").strip()
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if model_dir:
        config_path = model_dir / "config.json"
        model_path = _find_model_file(model_dir)
        if not config_path.is_file() or model_path is None:
            print(
                f"Invalid LOCAL_TTS_KOKORO_MODEL_DIR: {model_dir} "
                "(expected config.json and a .pth model file)",
                file=sys.stderr,
            )
            return 1
        model = KModel(repo_id=repo_id, config=str(config_path), model=str(model_path)).to(device).eval()
    else:
        model = KModel(repo_id=repo_id).to(device).eval()

    en_pipeline = None
    en_callable = None
    if lang == "z":
        en_pipeline = KPipeline(lang_code="a", repo_id=repo_id, model=False)

        def en_callable(text_part: str):
            if text_part == "Kokoro":
                return "kˈOkəɹO"
            if text_part == "Sol":
                return "sˈOl"
            return next(en_pipeline(text_part)).phonemes

    pipeline = KPipeline(
        lang_code=lang,
        repo_id=repo_id,
        model=model,
        en_callable=en_callable,
    )
    effective_speed = _speed_callable(speed) if lang == "z" else speed
    generator = pipeline(text, voice=pipeline_voice, speed=effective_speed)

    chunks: list[np.ndarray] = []
    for result in generator:
        audio = _audio_from_result(result)
        if audio is not None:
            chunks.append(np.asarray(audio, dtype=np.float32))

    if not chunks:
        print("No audio generated", file=sys.stderr)
        return 1

    pcm = np.concatenate(chunks)
    pcm = np.clip(pcm, -1.0, 1.0)
    pcm_int16 = (pcm * 32767.0).astype(np.int16)

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_int16.tobytes())

    print(
        f"Wrote {out_path}: {len(pcm_int16)} samples @ {SAMPLE_RATE} Hz "
        f"repo={repo_id} model_dir={model_dir or '<hf-cache>'} voice={voice} device={device}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kokoro CLI wrapper for local_tts")
    parser.add_argument("text_file")
    parser.add_argument("out_file")
    parser.add_argument("voice")
    parser.add_argument("speed", type=float)
    args = parser.parse_args()
    return synthesize(args.text_file, args.out_file, args.voice, args.speed)


if __name__ == "__main__":
    raise SystemExit(main())
